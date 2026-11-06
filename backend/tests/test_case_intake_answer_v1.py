"""Case Intake answer closure (U00033) — deterministic tests.

Pins the answer-loop contract for "查询 <case> 用户情况"-shaped first
submissions:

- the AMBIGUOUS / proposal-less understanding fallback forms an explicit,
  verifiable case-overview contract (standard GoalContract machinery);
- the composed conversational answer carries the SUBSTANTIVE case overview
  (finding count, overall risk level/score, finding id+title list) built
  from the risk_case_fetch observation summary — never just an artifact
  id, tool log, or the "可能未覆盖全部内容" warning alone;
- Option A (user ruling 2026-10-31): the intake overview is delivered
  WITHOUT a coexisting artifact bundle — the U00004 delivery-intent
  gate strips the planned generate_artifact step, and the strip is
  auditable;
- the fetched evidence is REUSED: an identical re-plan collapses to the
  bounded completion instead of re-running the same fetch;
- no findings → no fabricated overview (honest empty/thin answer);
- goal understanding failure still runs the loop (never a turn
  fallback) and answers from whatever the evidence supports.
"""

import json
import sqlite3
import time

from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner, DecisionProposal
from app.api.investigations import InvestigationAPI
from app.executor_v2 import ExecutorV2, ToolProvider
from app.investigation_service import InvestigationService
from app.models import ToolError, ToolResult, ToolResultOutcome
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from eval.fixtures import ScriptedLLM, default_findings, plan_json

CASE = "U00033"


class ScriptedProvider(ToolProvider):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.last = None
        self.calls = []

    def has(self, name):
        return True

    def get(self, name):
        def invoke(args):
            self.calls.append(name)
            if self.results:
                self.last = self.results.pop(0)
            return self.last
        return invoke


class ScriptedProposer:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        d = self.decisions.pop(0) if self.decisions else "CONTINUE"
        return DecisionProposal(decision=d, reason="test", source="llm")


def fetch_with_findings():
    findings = [f.model_dump() for f in default_findings()]
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"case_id": CASE, "findings": findings,
              "evidence_refs": [{"kind": "risk_event", "id": CASE}],
              "signal_refs": [], "policy_refs": [],
              "ext": {"risk_level": "CRITICAL", "risk_score": 86.1}},
        evidence_refs=[{"kind": "risk_event", "id": CASE}],
    )


def artifact_bundle_success():
    """A generate_artifact-step success whose payload carries the artifact
    dict the executor harvests (same contract as the domain tool)."""
    artifact = {
        "artifact_id": "ART-testbundle01",
        "source_tool_calls": ["TC-S2-test"],
        "investigation_id": "CASE:" + CASE,
        "task_id": "TASK-X",
        "scope": "case:" + CASE,
        "artifact_type": "findings_summary",
        "format": "md",
        "title": "Investigation Evidence Bundle — " + CASE,
        "content": "# bundle",
    }
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"artifact": artifact,
              "artifact_id": artifact["artifact_id"],
              "title": artifact["title"],
              "scope": artifact["scope"],
              "source_tool_call_count": 1},
        evidence_refs=[],
    )


class Holder:
    """Shares the counting provider and the last loop result with tests."""
    provider = None
    result = None


def build_env(tmp_path, provider_results, decisions, resolver=None,
              plan_types=("fetch_case",)):
    import app.api.investigations as inv_module

    store = TaskStoreV2(str(tmp_path / "intake_answer.db"))
    holder = Holder()

    class CountingProvider(ToolProvider):
        def __init__(self, results):
            super().__init__()
            self.results = list(results)
            self.calls = []

        def has(self, name):
            return True

        def get(self, name):
            def invoke(args):
                holder.provider.calls.append(name)
                if self.results:
                    self.last = self.results.pop(0)
                return self.last
            return invoke

    provider = CountingProvider(provider_results)
    holder.provider = provider

    def factory(store):  # per-investigation runner (fresh per submit)
        return AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM([
                plan_json("case_intake", list(plan_types)),
            ] * 4)),
            executor=ExecutorV2(provider),
            store=store,
            proposer=ScriptedProposer(list(decisions)),
            findings_provider=lambda: default_findings(),
        )

    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(ScriptedLLM([
                plan_json("case_intake", list(plan_types)),
            ] * 4)),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda case_id: [],
        loop_runner_factory=factory,
    )
    if resolver is not None:
        api.goal_resolver = resolver
    # capture the loop result for idempotency probes
    original = InvestigationAPI._record_loop_result

    def recording(self, investigation_id, result):
        holder.result = result
        return original(self, investigation_id, result)

    api._record_loop_result = recording.__get__(api)
    inv_module.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/v2/investigations", json={"case_reference": CASE})
    inv = r.json()["investigation"]["investigation_id"]
    return client, api, store, inv, holder


def submit_and_wait(client, inv, message, timeout_s=30):
    r = client.post(f"/api/v2/investigations/{inv}/submit",
                    json={"message": message})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "loop"
    task_id = r.json()["task_id"]
    deadline = time.time() + timeout_s
    d = {}
    while time.time() < deadline:
        resp = client.get(f"/api/v2/tasks/{task_id}")
        if resp.status_code == 200:
            d = resp.json()
            if d["task"]["status"] in ("completed", "failed"):
                break
        time.sleep(0.2)
    return task_id, d


def ambiguous_resolver(*a, **k):
    from app.goal_resolution import GoalResolutionStatus, GoalRepresentation
    return GoalRepresentation(
        case_id=CASE, goal_resolution=GoalResolutionStatus.AMBIGUOUS.value,
        subject_type="case", subject_ref=CASE, source="llm",
        request_text="查询00033用户情况",
        ambiguous_goal_types=["overview", "findings"])


def broken_resolver(*a, **k):
    raise RuntimeError("LLM unavailable")


class TestCaseIntakeAnswerClosure:
    def test_ambiguous_fallback_forms_verifiable_contract(
            self, tmp_path, monkeypatch):
        """The AMBIGUOUS fallback forms an explicit case-overview contract:
        the run ends goal_satisfied with the case summary composed."""
        from app.config import settings
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        client, api, store, inv, _ = build_env(
            tmp_path, [fetch_with_findings()],
            ["CONTINUE", "CONTINUE", "STOP"],
            resolver=ambiguous_resolver,
            plan_types=("fetch_case", "generate_artifact"))
        task_id, _ = submit_and_wait(client, inv, "Investigate U00033")
        conn = sqlite3.connect(str(tmp_path / "intake_answer.db"))
        st = json.loads(conn.execute(
            "SELECT document FROM agent_states WHERE task_id=?",
            (task_id,)).fetchone()[0])
        # the contract was ESTABLISHED from the deterministic intake rep
        assert st["goal_contract"] is not None
        assert st["goal_contract_decision"]["outcome"] == "contract_accepted"
        assert st["goal_contract"]["outputs"][0][
            "evidence_requirements"] == ["case_summary"]
        # the run satisfied the contract and stopped goal_satisfied
        assert st["agent_status"] == "completed"
        assert st["stop_reason"] == "goal_satisfied"

    def test_answer_carries_substantive_case_overview(self, tmp_path):
        n = len(default_findings())
        client, api, store, inv, holder = build_env(
            tmp_path, [fetch_with_findings()],
            ["CONTINUE", "CONTINUE", "STOP"],
            resolver=ambiguous_resolver,
            plan_types=("fetch_case", "generate_artifact"))
        task_id, detail = submit_and_wait(
            client, inv, "Investigate U00033")
        text = detail.get("task_response_text") or ""
        # substantive case overview from the observation whitelist:
        # count, overall risk level/score, finding id+title list —
        # in the request's language (EN intake → EN overview)
        assert f"retrieved {n} identified risk findings" in text
        assert "CRITICAL" in text
        assert "86.1" in text
        assert "ML Pattern Detection" in text

    def test_option_a_intake_overview_without_artifact_bundle(
            self, tmp_path):
        """Case Intake product contract — user ruling 2026-10-31,
        Option A (KEEP EXISTING STRIP BEHAVIOR): the first submission's
        accepted intake contract carries detail_list, so the U00004
        delivery-intent gate strips the planned generate_artifact step
        BEFORE execution. The受限 case-level overview is still delivered
        conversationally; NO artifact bundle coexists with it; the
        stripped step's tool payload is never consumed; the strip is
        auditable via planning_diagnostics.delivery_intent_gate."""
        client, api, store, inv, holder = build_env(
            tmp_path,
            [fetch_with_findings(), artifact_bundle_success()],
            ["CONTINUE", "CONTINUE", "STOP"],
            resolver=ambiguous_resolver,
            plan_types=("fetch_case", "generate_artifact"))
        task_id, detail = submit_and_wait(
            client, inv, "Investigate U00033")
        task_id_artifacts = set(
            client.get(f"/api/v2/tasks/{task_id}").json()
            .get("task", {}).get("artifact_ids") or [])
        # Option A: NO artifact bundle coexists with the intake answer
        # (task container AND loop state both bundle-free)
        assert detail["artifacts"] == []
        assert task_id_artifacts == set()
        state = holder.result.state
        assert state.artifact_ids == []
        # the stripped step never reached the tool boundary
        assert holder.provider.calls.count("artifact_bundle") == 0
        # the strip is auditable (U00004 delivery-intent gate record)
        stripped = (state.planning_diagnostics or {}).get(
            "delivery_intent_gate") or []
        assert any(s.get("step_id") == "S2"
                   and s.get("type") == "generate_artifact"
                   for s in stripped)
        # the受限 conversational case overview IS delivered (substantive:
        # the identified findings are enumerated with their titles)
        text = detail.get("task_response_text") or ""
        assert "identified risk findings" in text
        assert "ML Pattern Detection" in text

    def test_identical_replan_reuses_evidence_no_duplicate_fetch(
            self, tmp_path):
        """The runtime judged question_answered unmet and re-planned —
        but an identical plan after a fully-successful execution collapses
        to the bounded completion: the fetch runs exactly ONCE and the
        composed answer renders from the acquired evidence."""
        n = len(default_findings())
        client, api, store, inv, holder = build_env(
            tmp_path, [fetch_with_findings()],
            ["CONTINUE", "CONTINUE", "RE_PLAN", "STOP"],
            resolver=ambiguous_resolver,
            plan_types=("fetch_case", "generate_artifact"))
        task_id, detail = submit_and_wait(
            client, inv, "Investigate U00033")
        fetches = holder.provider.calls.count("risk_case_fetch")
        assert fetches == 1, f"expected exactly one fetch, got {fetches}"
        assert detail["task"]["status"] in ("completed", "failed")
        assert "identified risk findings" in (
            detail.get("task_response_text") or "")

    def test_no_findings_no_fabricated_overview(self, tmp_path):
        """An empty fetch is answered honestly (zero findings) — no
        invented case content."""
        empty = ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"case_id": CASE, "findings": [], "evidence_refs": [],
                  "signal_refs": [], "policy_refs": [],
                  "ext": {"risk_level": "LOW", "risk_score": 12.5}},
            evidence_refs=[])
        client, api, store, inv, holder = build_env(
            tmp_path, [empty],
            ["CONTINUE", "CONTINUE", "STOP"],
            resolver=ambiguous_resolver,
            plan_types=("fetch_case", "generate_artifact"))
        task_id, detail = submit_and_wait(
            client, inv, "Investigate U00033")
        text = detail.get("task_response_text") or ""
        assert "retrieved 0 identified risk findings" in text
        assert "ML Pattern Detection" not in text

    def test_understanding_failure_still_answers_from_evidence(
            self, tmp_path):
        """Understanding failure: the loop classifies the request itself
        (heuristic single goal) and the composer answers from the acquired
        evidence — never a turn fallback, never a fabricated success."""
        n = len(default_findings())
        client, api, store, inv, holder = build_env(
            tmp_path, [fetch_with_findings()],
            ["CONTINUE", "CONTINUE", "STOP"],
            resolver=broken_resolver,
            plan_types=("fetch_case", "generate_artifact"))
        task_id, detail = submit_and_wait(
            client, inv, "Investigate U00033")
        assert detail["task"]["type"] == "agent_loop"
        text = detail.get("task_response_text") or ""
        # the honest no-contract closure + the acquired-evidence summary —
        # both in the REQUEST's language (per-turn response language: an
        # English intake gets the English closure and English overview)
        assert "could not establish a complete goal decomposition" in text
        assert "identified risk findings" in text
        from app.response_language import detect_response_language
        assert detect_response_language(text) == "en"


class TestNoContractComposerCaseSummary:
    def test_no_contract_composer_renders_case_fetch_summary(self):
        """Direct composer pin: the no-contract closure renders the case
        fetch summary (count, risk level/score, finding titles)."""
        from app.agent_loop import AgentLoopState, compose_loop_response
        state = AgentLoopState(
            task_id="TASK-L", investigation_id="INV-L", case_id=CASE,
            goal="Investigate U00033", criteria_kind="question_answered",
            agent_status="completed", stop_reason="goal_satisfied",
            observations=[{
                "tool_name": "risk_case_fetch",
                "step_type": "fetch_case",
                "outcome": "success",
                "semantic_status": None,
                "summary": {"finding_count": 8, "risk_level": "CRITICAL",
                            "risk_score": 86.1,
                            "findings": [
                                {"id": "F1",
                                 "title": "ML Pattern Detection"}]},
                "evidence_refs": [],
            }],
        )
        resp = compose_loop_response(state)
        assert "案例 U00033 概况" in resp
        assert "8 条已识别的风险发现" in resp
        assert "CRITICAL" in resp
        assert "F1 ML Pattern Detection" in resp

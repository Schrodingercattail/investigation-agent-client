"""Defect-fix regression tests (QA-01..QA-09) — activity/loop boundaries.

QA-01: goal endpoint honors the pending UI selection (context_action);
       focus reaches AgentLoopState; latest selection wins; case-level
       still works; no stale focus leaks.
QA-02: terminal plan snapshot is re-synced (executed step is success,
       pending stays pending) across all terminal kinds.
QA-04: findings-provider failure → distinct BLOCKED/dependency state at
       run start (NOT planning failure); fault-injection provider makes
       tool-level retry→success and retry→blocked reachable E2E through
       the real API.
QA-05: observation records carry analyst-facing display phrases.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner
from app.executor_v2 import ExecutorV2, ToolProvider
from app.models import (
    Finding,
    FindingCapability,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.agent_loop import LoopDecision
from app.planner_v2 import PlannerV2, _is_cross_account_trade_request
from app.task_store_v2 import TaskStoreV2
from eval.fixtures import ScriptedLLM, plan_json

CASE = "U00299"


# ---------------------------------------------------------------------------
# Deterministic loop parts
# ---------------------------------------------------------------------------

class ScriptedProvider(ToolProvider):
    """Scripted results; `faults` = fail-first-N integration errors."""

    def __init__(self, results, faults=0,
                 error=None):
        super().__init__()
        self.results = list(results)
        self.last = self.results[-1] if self.results else None
        self.faults = faults
        self.error = error or ToolError(
            code="RISK_PLATFORM_UNAVAILABLE", message="RP timed out")
        self.calls = 0

    def has(self, name):
        return True

    def get(self, name):
        def invoke(args):
            self.calls += 1
            if self.faults > 0:
                self.faults -= 1
                return ToolResult(outcome=ToolResultOutcome.INTEGRATION_ERROR,
                                  error=self.error)
            if self.results:
                self.last = self.results.pop(0)
            return self.last
        return invoke


class ScriptedProposer:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        from app.agent_loop import DecisionProposal
        d = self.decisions.pop(0) if self.decisions else "CONTINUE"
        return DecisionProposal(decision=d, reason="deterministic scenario",
                                source="heuristic")


def timeline_success():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": "F2", "view": "timeline",
              "events": [{"event_id": "F2-E001",
                          "timestamp": "2026-08-19T10:00:00Z",
                          "event_type": "transaction",
                          "summary": "BUY trade"}],
              "total_events": 1, "truncated": False},
        evidence_refs=[])


def focused_findings():
    f = Finding(finding_id="F2", case_id=CASE, type="rule_signal",
                title="Coordinated Trading Pattern", summary="s",
                capabilities=FindingCapability.model_validate(
                    ["timeline", "signal_explain", "policy_lookup",
                     "self_opposite_trades"]))
    return [f]


def focused_factory(decisions, provider=None, findings=focused_findings):
    """Runner whose findings_provider resolves the F2 finding (RP up)."""

    def factory(store: TaskStoreV2) -> AgentLoopRunner:
        return AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("trade_investigation",
                           ["analyze_cross_account_trades"])])),
            executor=ExecutorV2(provider or ScriptedProvider(
                [timeline_success()])),
            store=store,
            proposer=ScriptedProposer(list(decisions)),
            findings_provider=lambda: findings())
    return factory


@pytest.fixture()
def client(tmp_path):
    from app.api.investigations import InvestigationAPI
    import app.api.investigations as inv_module
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "fixes.db"))

    def make_api(loop_factory=None):
        api = InvestigationAPI(
            service=InvestigationService(
                planner=PlannerV2(ScriptedLLM(
                    [plan_json("case_intake",
                               ["fetch_case", "generate_artifact"])])),
                executor=None, task_store=store),
            store=store,
            case_findings_provider=lambda case_id: focused_findings(),
            loop_runner_factory=loop_factory or focused_factory(["STOP"]),
        )
        inv_module.api = api
        return api

    # wire the DEFAULT api immediately — tests without an explicit
    # loop_factory must hit the deterministic loop, never the module
    # default (real planner/LLM)
    make_api()

    from app.main import app
    yield TestClient(app, raise_server_exceptions=False), store, make_api
    import app.api.investigations as inv_module2
    # leave the module-level api detached from test stores
    inv_module2.api = InvestigationAPI()


def create_and_start(client, goal, context_action=None,
                     loop_factory=None):
    c, store, make_api = client
    if loop_factory is not None:
        make_api(loop_factory)
    r = c.post("/api/v2/investigations",
               json={"case_reference": CASE})
    assert r.status_code == 200, r.text
    inv = r.json()["investigation"]["investigation_id"]
    body = {"goal": goal}
    if context_action is not None:
        body["context_action"] = context_action
    g = c.post(f"/api/v2/investigations/{inv}/goal", json=body)
    assert g.status_code == 200, g.text
    return inv, g.json()["task_id"]


def await_terminal(client, task_id, timeout_s=10.0):
    c, store, _ = client
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        a = c.get(f"/api/v2/tasks/{task_id}/activity")
        if a.status_code == 200 and a.json()["is_terminal"]:
            return a.json()
        time.sleep(0.05)
    # diagnostics: dump state + thread stacks at timeout
    st = store.get_agent_state(task_id)
    import faulthandler
    print(f"\nDEBUG task={task_id} state={st and (st.agent_status, st.stop_reason, st.focused_finding_id, [x['event_type'] for x in st.activity])}")
    print(f"DEBUG activity api: {a.status_code} {a.text[:300]}")
    faulthandler.dump_traceback()
    raise AssertionError("loop did not terminate")


# ---------------------------------------------------------------------------
# QA-01: focus preservation through the goal endpoint
# ---------------------------------------------------------------------------

class TestQA01FocusPreservation:
    def test_english_focused_goal_preserves_selection(self, client):
        c, store, _ = client
        inv, task_id = create_and_start(
            client, "find cross-account opposite trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        activity = await_terminal(client, task_id)
        # the loop actually investigated the FOCUSED finding
        assert activity["focused_finding_id"] == "F2"
        assert activity["agent_status"] == "completed"
        # focus is persisted authoritative investigation state
        ctx = c.get(f"/api/v2/investigations/{inv}").json()["context"]
        assert ctx["focused_finding_id"] == "F2"
        assert ctx["focus_source"] == "user_selected"

    def test_chinese_focused_goal_preserves_selection(self, client):
        c, store, _ = client
        inv, task_id = create_and_start(
            client, "檢查這個發現的跨賬戶對向交易",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        activity = await_terminal(client, task_id)
        assert activity["focused_finding_id"] == "F2"
        assert activity["agent_status"] == "completed"

    def test_selection_reaches_agent_state_and_planner(self, client):
        c, store, _ = client
        inv, task_id = create_and_start(
            client, "find cross-account opposite trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        await_terminal(client, task_id)
        state = store.get_agent_state(task_id)
        assert state.focused_finding_id == "F2"
        # the plan was the FOCUSED trade skill's step (planner received focus)
        assert state.plan_steps[0]["type"] == "analyze_cross_account_trades"

    def test_case_level_goal_without_selection_still_works(self, client):
        c, store, _ = client
        inv, task_id = create_and_start(
            client, "investigate the case", context_action=None,
            loop_factory=lambda store: AgentLoopRunner(
                planner=PlannerV2(ScriptedLLM(
                    [plan_json("case_intake", ["fetch_case"])])),
                executor=ExecutorV2(ScriptedProvider([
                    ToolResult(outcome=ToolResultOutcome.SUCCESS,
                               data={"case_id": CASE, "findings": [],
                                     "evidence_refs": [], "signal_refs": [],
                                     "policy_refs": []})])),
                store=store,
                proposer=ScriptedProposer(["STOP"]),
                findings_provider=lambda: focused_findings()))
        activity = await_terminal(client, task_id)
        assert activity["agent_status"] == "completed"
        assert activity["focused_finding_id"] is None

    def test_latest_selection_wins(self, client):
        c, store, _ = client
        inv, task_id = create_and_start(
            client, "find cross-account opposite trades",
            context_action={"type": "focus_finding", "finding_id": "F9"})
        # F9 does not exist in findings — the loop starts but the planner
        # fails (bounded); what matters here is NO stale F2 leak
        activity = await_terminal(client, task_id)
        state = store.get_agent_state(task_id)
        assert state.focused_finding_id == "F9"
        assert state.focused_finding_id != "F2"

    def test_no_stale_focus_leaks_into_next_run(self, client):
        c, store, _ = client
        # run 1: focused
        inv1, t1 = create_and_start(
            client, "find cross-account opposite trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        await_terminal(client, t1)
        # run 2: NEW investigation, NO selection
        r = c.post("/api/v2/investigations", json={"case_reference": CASE})
        inv2 = r.json()["investigation"]["investigation_id"]
        g = c.post(f"/api/v2/investigations/{inv2}/goal",
                   json={"goal": "case-level investigation"})
        task2 = g.json()["task_id"]
        activity = await_terminal(client, task2)
        assert activity["focused_finding_id"] is None


# ---------------------------------------------------------------------------
# QA-02: terminal plan snapshot consistency
# ---------------------------------------------------------------------------

class TestQA02TerminalPlanState:
    def test_one_step_completion_marks_step_success(self, client):
        c, store, _ = client
        inv, task_id = create_and_start(
            client, "find cross-account opposite trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        activity = await_terminal(client, task_id)
        assert activity["agent_status"] == "completed"
        executed = [s for s in activity["plan_steps"]
                    if s["type"] == "analyze_cross_account_trades"]
        assert executed and executed[0]["status"] == "success"

    def test_pending_step_stays_pending_at_terminal(self, client):
        c, store, _ = client
        # plan: cross analysis then retrieve_policy (STOP after first step)
        def factory(store):
            return AgentLoopRunner(
                planner=PlannerV2(ScriptedLLM(
                    [plan_json("trade_investigation",
                               ["analyze_cross_account_trades",
                                "retrieve_policy"])])),
                executor=ExecutorV2(ScriptedProvider([timeline_success()])),
                store=store,
                proposer=ScriptedProposer(["STOP"]),
                findings_provider=lambda: focused_findings())
        inv, task_id = create_and_start(
            client, "find cross-account trades",
            context_action={"type": "focus_finding", "finding_id": "F2"},
            loop_factory=factory)
        activity = await_terminal(client, task_id)
        by_type = {s["type"]: s["status"] for s in activity["plan_steps"]}
        assert by_type["analyze_cross_account_trades"] == "success"
        # AL-2.6: the deterministic plan's second step is the bounded
        # bundle; the QA-02 property is unchanged — a pending step at a
        # STOP terminal stays pending (never marked done).
        assert by_type["generate_artifact"] == "pending"

    def test_stopped_by_limit_keeps_state_consistent(self, client):
        from types import SimpleNamespace

        class AlwaysContinue:
            def propose(self, state):
                from app.agent_loop import DecisionProposal
                return DecisionProposal(decision="CONTINUE", reason="go")

        def factory(store):
            return AgentLoopRunner(
                planner=PlannerV2(ScriptedLLM(
                    [plan_json("case_intake", ["fetch_case"])])),
                executor=ExecutorV2(ScriptedProvider([
                    ToolResult(outcome=ToolResultOutcome.SUCCESS,
                               data={"case_id": CASE, "findings": [],
                                     "evidence_refs": [], "signal_refs": [],
                                     "policy_refs": []})])),
                store=store,
                proposer=AlwaysContinue(),
                findings_provider=lambda: focused_findings())
        c, store, make_api = client
        inv, task_id = create_and_start(
            client, "endless investigation", loop_factory=factory)
        activity = await_terminal(client, task_id, timeout_s=30)
        # AL-2.13: an always-CONTINUE proposer over an identical-success
        # query now converges via the no-progress guard BEFORE the
        # iteration bound; the bound remains the backstop for genuinely
        # varying results.
        assert activity["agent_status"] in (
            "stopped_by_limit", "completed")
        # every executed fetch_case step is success in the snapshot
        for s in activity["plan_steps"]:
            if s["type"] == "fetch_case":
                assert s["status"] == "success"

    def test_reload_shows_same_plan(self, client):
        c, store, _ = client
        inv, task_id = create_and_start(
            client, "find cross-account opposite trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        first = await_terminal(client, task_id)
        second = await_terminal(client, task_id)
        assert first["plan_steps"] == second["plan_steps"]


# ---------------------------------------------------------------------------
# QA-04: dependency-unavailable vs retryable runtime failure
# ---------------------------------------------------------------------------

class TestQA04DependencyBoundaries:
    def test_rp_unavailable_at_start_is_blocked_not_planning_failure(
            self, client):
        c, store, make_api = client

        def failing_provider():
            raise RuntimeError("Risk Platform unreachable")

        make_api(lambda store: AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("trade_investigation",
                           ["analyze_cross_account_trades"])])),
            executor=ExecutorV2(ScriptedProvider([timeline_success()])),
            store=store,
            proposer=ScriptedProposer(["STOP"]),
            findings_provider=failing_provider))
        inv, task_id = create_and_start(
            client, "find cross-account trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        activity = await_terminal(client, task_id)
        # DISTINCT dependency state — not a planning failure, not completion
        assert activity["agent_status"] == "blocked"
        assert activity["stop_reason"] == "dependency_unavailable"
        assert "unavailable" in activity["activity"][-1]["summary"].lower()

    def test_transient_rp_failure_retries_then_continues(self, client):
        c, store, make_api = client
        # fault once, then success
        make_api(lambda store: AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("trade_investigation",
                           ["analyze_cross_account_trades"])])),
            executor=ExecutorV2(ScriptedProvider(
                [timeline_success()], faults=1)),
            store=store,
            proposer=ScriptedProposer(["STOP"]),
            findings_provider=lambda: focused_findings()))
        inv, task_id = create_and_start(
            client, "find cross-account trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        activity = await_terminal(client, task_id)
        assert activity["agent_status"] == "completed"
        retries = [r for r in activity["activity"]
                   if r["event_type"] == "step.retried"]
        assert len(retries) == 1
        # the retry row documents the FAILED attempt; the retry pass itself
        # is the next iteration (state.iteration == 2, see Layer A test 7)
        assert retries[0]["iteration"] == 1
        # exactly one observation: the retry's success, no duplicate step
        assert len([r for r in activity["activity"]
                    if r["event_type"] == "observation.recorded"]) == 1

    def test_persistent_rp_failure_is_bounded_blocked(self, client):
        c, store, make_api = client
        make_api(lambda store: AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("trade_investigation",
                           ["analyze_cross_account_trades"])])),
            executor=ExecutorV2(ScriptedProvider(
                [timeline_success()], faults=99)),
            store=store,
            proposer=ScriptedProposer(["BLOCK"]),
            findings_provider=lambda: focused_findings()))
        inv, task_id = create_and_start(
            client, "find cross-account trades",
            context_action={"type": "focus_finding", "finding_id": "F2"})
        activity = await_terminal(client, task_id)
        assert activity["agent_status"] == "blocked"
        assert activity["stop_reason"] == "persistent_error"
        retries = [r for r in activity["activity"]
                   if r["event_type"] == "step.retried"]
        assert len(retries) == 1                 # capped, not infinite
        assert activity["iteration"] == 2        # initial + retry


# ---------------------------------------------------------------------------
# QA-05/QA-06: display phrases + traditional CJK routing
# ---------------------------------------------------------------------------

class TestSmallFixes:
    def test_qa05_display_phrases_are_user_language(self):
        from app.agent_loop import project_observation
        rec = project_observation(
            "cross_account_trade_analysis", "analyze_cross_account_trades",
            ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
                "analysis": "cross_account_trade_analysis",
                "analysis_status": "MATCH_FOUND",
                "candidates": [], "candidates_found": 3,
                "candidates_emitted": 3, "totals": {}, "retrieval": {},
                "data_quality": {}}))
        assert rec["display"] == (
            "Found 3 candidate cross-account opposite-trade relationships "
            "(candidates only, not conclusions).")
        # machine digest still exists for the planner, separately
        assert rec["summary"]["candidates_found"] == 3

    def test_qa06_traditional_cjk_routes_to_cross(self):
        assert _is_cross_account_trade_request("檢查跨賬戶對向交易")
        assert _is_cross_account_trade_request("跨賬戶對倒分析")
        # same-account traditional wording must not trip the cross hook
        assert not _is_cross_account_trade_request("這個賬戶的對向交易")

    def test_qa07_conclusive_empty_sets_precise_reason(self):
        from app.agent_loop import LoopPolicy, DecisionProposal, \
            AgentLoopState
        state = AgentLoopState(
            task_id="T", investigation_id="CASE:U00299", case_id=CASE,
            goal="g", criteria_kind="question_answered",
            observations=[{"outcome": "success",
                           "semantic_status": "NO_MATCH",
                           "summary": {}, "step_id": "S1",
                           "step_type": "analyze"}])
        enforced = LoopPolicy().enforce(
            DecisionProposal(decision="STOP", reason="zero matches"), state)
        assert enforced.decision == LoopDecision.STOP
        # the runner maps conclusive_empty to the precise stop reason
        assert state.conclusive_empty() is True

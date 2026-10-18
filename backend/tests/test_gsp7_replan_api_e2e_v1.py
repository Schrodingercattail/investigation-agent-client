"""GSP WP-GSP-7 — the actual RE_PLAN flow over the REAL HTTP API surface.

L2 API E2E (in-process ASGI — the established deterministic tier of
test_api_investigations_v1 / test_agent_activity_api_v1): the real
FastAPI app serves POST /api/v2/investigations/{id}/goal; the loop runs
in the API's background thread through the PRODUCTION runner factory
(``_default_loop_runner``), with the GSP-7 API-E2E seam
(``AGENT_LOOP_SCRIPTED_SESSION_FILE``) scripting ONLY the model
boundary: planner LLM texts, decision proposals, and the
goal-understanding proposal. Everything else is the real production
path: HTTP API → route → loop factory → AgentLoopRunner → deterministic
context resolution → PlannerV2 validation → coverage admission →
ExecutorV2 → domain tools (RP faked at the adapter boundary) →
postconditions → composer → TaskStoreV2 persistence → activity
projection served back over HTTP.

Verified here (the loop internals are pinned by
test_gsp7_replan_preservation_v1; this module pins the API-SERVED
RE_PLAN flow):

- R1 a RE_PLAN over the API: scripted RE_PLAN decision → re-plan
      adoption → coverage re-admission → NEW legal path executed →
      bounded completion; the accepted goal contract is served intact
      after the re-plan (never rewritten or narrowed).
- R2 a no-progress re-plan (repeats this run's confirmed-unsupported
      path) is deterministically stripped → bounded REPLAN_FAILURE;
      the unsupported path is never executed twice, and the failed
      re-plan is never adopted (plan_revisions unchanged).
- R3 flag OFF identity: no GSP-7 filter (the repeat IS adopted and
      re-executed — legacy semantics), no contract, no coverage.

Real deterministic unsupported path: inspect_self_opposite_trades on
finding F1 (ML-only finding — no ``self_opposite_trades`` capability →
the drilldown view is bounded UNSUPPORTED by the domain tool). The goal
text deliberately avoids cross-account/opposite-trade/withdrawal
phrasings so the planner's semantic step specialization never rewrites
the scripted steps.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.goal_resolution import build_goal_contract, GoalRepresentation
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from eval.fixtures import ScriptedLLM, plan_json
import tests.test_risk_platform_adapter_v2 as fx
from tests.test_api_investigations_v1 import rp_patches

CASE = "U00299"
GOAL = "这个账号是否有洗钱情况？"
FOCUS_FINDING = "F1"          # ML-only → self-opposite view UNSUPPORTED
POLL_TIMEOUT_S = 30.0

# Initial plan: a legal signal explanation, then the path that is bounded
# UNSUPPORTED for F1 (the laundering lineage).
PLAN1 = plan_json("timeline_investigation",
                  ["explain_signal", "inspect_self_opposite_trades"])
# A NEW legal path (different step type; same tool, different view).
PLAN2_NEW = plan_json("timeline_investigation", ["inspect_evidence"])
# The repeat the GSP-7 filter must strip (no-progress re-plan).
PLAN2_REPEAT = plan_json("timeline_investigation",
                         ["inspect_self_opposite_trades"])

# Scripted goal understanding: the laundering contract proposal (GSP-2/
# GSP-7 lineage fixture) so the accepted contract is deterministic
# without an LLM. Subject = the case's account id; claim_kind assessment
# (the runtime gathers evidence; the judgment itself stays
# unadjudicable).
GOAL_SECTION = {
    "case_id": CASE,
    "goal_type": "policy_check",
    "goal_resolution": "INFERRED",
    "subject_type": "case",
    "subject_ref": CASE,
    "ambiguous_goal_types": [],
    "reason": "scripted session",
    "source": "llm",
    "request_text": GOAL,
    "contract_proposal": {
        "required_outputs": [
            {"claim": "money_laundering_assessment",
             "claim_kind": "assessment",
             "subject_ref": CASE,
             "scope": "account_level",
             "evidence_requirements": [
                 "transaction_records", "withdrawal_records",
                 "network_relations", "policy_references",
                 "ml_signal_explanation"],
             },
        ],
    },
}


# ---------------------------------------------------------------------------
# Harness: real API app, production loop factory, scripted session seam
# ---------------------------------------------------------------------------

def _fixture_findings():
    """The SAME findings the production loop path resolves via
    ``_findings_strict`` (raw adapter derivation — deliberately no
    capability overrides)."""
    p1, p2 = rp_patches()
    with p1, p2:
        from app.domain_tools import risk_case_fetch
        result = risk_case_fetch(CASE)
    return list(result.data["findings"])


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """Real FastAPI app + real InvestigationAPI/store; the loop keeps the
    PRODUCTION ``_default_loop_runner`` factory — the scripted session
    seam (env) is what makes it deterministic."""
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "gsp7_api_e2e.db"))
    findings = _fixture_findings()
    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(_TurnScript()),
            executor=None,                    # default provider (real tools)
            task_store=store),
        store=store,
        case_findings_provider=lambda case_id: findings)
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), store, monkeypatch


class _TurnScript:
    """The turn path is never invoked by the goal endpoint; a bounded
    placeholder keeps the service constructible without an LLM."""

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        return ""


def _write_session(tmp_path, plans, decisions, with_goal=True):
    session = {"plans": list(plans), "decisions": list(decisions)}
    if with_goal:
        session["goal"] = GOAL_SECTION
    path = tmp_path / "replan_session.json"
    path.write_text(json.dumps(session, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def _await_terminal(client, task_id, timeout_s=POLL_TIMEOUT_S):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/v2/tasks/{task_id}/activity")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["is_terminal"]:
            return last
        time.sleep(0.02)
    raise AssertionError(f"loop did not terminate; last={last}")


def _run_goal(client, monkeypatch, tmp_path, *, plans, decisions,
              gsp_on, with_goal=True):
    """One full API journey: create investigation → focus F1 → goal run
    (scripted session) → poll to terminal. The RP adapter patches stay
    held for the WHOLE run: the loop executes in a background thread and
    resolves findings/evidence through the real tool boundary. Returns
    (activity, task_id)."""
    c = client
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", gsp_on)
    monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
    monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE",
                       _write_session(tmp_path, plans, decisions,
                                      with_goal=with_goal))
    p1, p2 = rp_patches()
    with p1, p2:
        inv = c.post("/api/v2/investigations", json={"case_id": CASE})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = c.post(f"/api/v2/investigations/{inv_id}/goal",
                     json={"goal": GOAL,
                           "context_action": {"type": "focus_finding",
                                              "finding_id": FOCUS_FINDING}})
        assert run.status_code == 200, run.text
        assert run.json()["agent_status"] == "running"
        task_id = run.json()["task_id"]
        return _await_terminal(c, task_id), task_id


def _decision_sequence(activity):
    return [r["decision"] for r in activity["activity"]
            if r["event_type"] == "decision.made"]


def _replan_count(activity):
    return sum(1 for r in activity["activity"]
               if r["event_type"] == "plan.created"
               and (r.get("summary") or "").startswith("re-plan "))


def _associations(activity, step_type):
    return [a for a in activity["observation_associations"]
            if a["step_type"] == step_type]


def _step_starts(activity, step_type):
    """Step executions as served by the activity log (works under both
    flag states — the association list exists only under GSP)."""
    return [r for r in activity["activity"]
            if r["event_type"] == "step.started"
            and r.get("step_type") == step_type]


def _expected_contract():
    """The deterministic contract the scripted goal section must produce
    (the acceptance boundary runs for real inside the loop; this is the
    independent expectation)."""
    contract, reason = build_goal_contract(
        GoalRepresentation.model_validate(GOAL_SECTION), CASE)
    assert reason is None and contract is not None
    return contract


# ---------------------------------------------------------------------------
# R1 — RE_PLAN over the API: new legal path, contract served intact
# ---------------------------------------------------------------------------

class TestReplanHappyPath:
    def test_replan_adopts_new_path_and_completes(self, api_client, tmp_path):
        client, store, monkeypatch = api_client
        activity, _ = _run_goal(
            client, monkeypatch, tmp_path,
            plans=[PLAN1, PLAN2_NEW],
            decisions=["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"],
            gsp_on=True)
        c = client

        # bounded completion through the real composition path
        assert activity["agent_status"] == "completed"
        assert activity["stop_reason"] == "goal_satisfied"
        assert activity["focused_finding_id"] == FOCUS_FINDING

        # the RE_PLAN actually happened and was adopted exactly once
        # (plan_revisions counts adoptions: initial plan + the re-plan)
        assert activity["plan_revisions"] == 2
        assert _replan_count(activity) == 1
        assert _decision_sequence(activity) == [
            "CONTINUE", "RE_PLAN", "CONTINUE", "STOP"]

        # the adopted (re-planned) plan is the NEW legal path, executed
        assert [(s["type"], s["status"]) for s in activity["plan_steps"]] \
            == [("inspect_evidence", "success")]

        # the unsupported path ran exactly ONCE (before the re-plan)
        assert len(_associations(activity, "inspect_self_opposite_trades")) == 1
        unsupported = _associations(
            activity, "inspect_self_opposite_trades")[0]
        assert unsupported["outcome"] == "unsupported"
        assert unsupported["for_outputs"] == ["O1"]

        # every observation is associated with the contract output
        assert all(a["for_outputs"] == ["O1"]
                   for a in activity["observation_associations"])

        # the accepted contract is SERVED, intact, after the re-plan —
        # content equals the deterministic acceptance of the SAME
        # proposal (no rewrite, no narrowing by the re-plan)
        served = activity["goal_contract"]
        expected = _expected_contract()
        assert served["subject_ref"] == expected.subject_ref
        assert [o["output_id"] for o in served["outputs"]] == ["O1"]
        assert served["outputs"][0]["claim"] == \
            expected.outputs[0].claim
        assert served["outputs"][0]["claim_kind"] == \
            expected.outputs[0].claim_kind.value
        assert served["outputs"][0]["scope"] == \
            expected.outputs[0].scope.value
        assert sorted(served["outputs"][0]["evidence_requirements"]) == \
            sorted(e.value for e in
                   expected.outputs[0].evidence_requirements)

        # the composed response is served through the conversation read
        # model (loop products mirrored into the investigation session)
        detail = c.get(f"/api/v2/tasks/{activity['task_id']}").json()
        assert detail.get("task_response_text")

    def test_replan_coverage_readmission_served_state(
            self, api_client, tmp_path):
        """The re-planned plan passed coverage re-admission against the
        SAME contract (state-level record; the coverage list itself is
        not an activity-projection field)."""
        client, store, monkeypatch = api_client
        activity, task_id = _run_goal(
            client, monkeypatch, tmp_path,
            plans=[PLAN1, PLAN2_NEW],
            decisions=["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"],
            gsp_on=True)
        state = store.get_agent_state(task_id)
        assert state.goal_contract is not None
        records = {r["output_id"]: r for r in state.planning_coverage}
        assert records["O1"]["coverage_status"] == "covered"
        assert "inspect_evidence" in records["O1"]["eligible_step_types"]


# ---------------------------------------------------------------------------
# Seam degradation: an unreadable session file must end bounded
# ---------------------------------------------------------------------------

class TestSeamBoundedDegradation:
    def test_missing_session_file_fails_bounded(self, api_client, tmp_path,
                                                monkeypatch):
        """A typo'd AGENT_LOOP_SCRIPTED_SESSION_FILE never hangs the loop
        or fabricates a plan: with no LLM available (the documented no-key
        environment), no scripts → the planner's own bounded
        LLM_UNAVAILABLE failure → terminal planning_failure; goal
        understanding degrades to the heuristic (no contract)."""
        c, _, _ = api_client
        from app.llm_provider import ClaudeProvider

        def _no_llm(self, messages=None, max_tokens=0, temperature=0.1):
            raise RuntimeError("no LLM configured in this test")

        monkeypatch.setattr(ClaudeProvider, "generate", _no_llm)
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE",
                           str(tmp_path / "does_not_exist.json"))
        p1, p2 = rp_patches()
        with p1, p2:
            inv = c.post("/api/v2/investigations",
                         json={"case_id": CASE})
            assert inv.status_code == 200, inv.text
            run = c.post(
                f"/api/v2/investigations/{inv.json()['investigation']['investigation_id']}/goal",
                json={"goal": GOAL,
                      "context_action": {"type": "focus_finding",
                                         "finding_id": FOCUS_FINDING}})
            assert run.status_code == 200, run.text
            activity = _await_terminal(c, run.json()["task_id"])
        assert activity["agent_status"] == "failed"
        assert activity["stop_reason"] == "planning_failure"
        assert activity["plan_steps"] == []
        assert activity["goal_contract"] is None


# ---------------------------------------------------------------------------
# R2 — no-progress re-plan: repeat stripped → bounded REPLAN_FAILURE
# ---------------------------------------------------------------------------

class TestReplanNoProgressTermination:
    def test_repeat_unsupported_replan_terminates_bounded(
            self, api_client, tmp_path):
        client, store, monkeypatch = api_client
        activity, task_id = _run_goal(
            client, monkeypatch, tmp_path,
            plans=[PLAN1, PLAN2_REPEAT],
            decisions=["CONTINUE", "RE_PLAN"],
            gsp_on=True)

        # bounded, distinct terminal — never a misleading completion
        assert activity["agent_status"] == "failed"
        assert activity["stop_reason"] == "replan_failure"
        assert activity["is_terminal"] is True

        # the failed re-plan was NEVER adopted (revisions = initial plan
        # adoption only)
        assert activity["plan_revisions"] == 1
        assert _replan_count(activity) == 0
        assert [s["type"] for s in activity["plan_steps"]] == [
            "explain_signal", "inspect_self_opposite_trades"]

        # the known-bad path was not re-executed
        assert len(_associations(activity, "inspect_self_opposite_trades")) == 1

        # the contract is served intact even on the bounded failure
        served = activity["goal_contract"]
        expected = _expected_contract()
        assert served["outputs"][0]["claim"] == expected.outputs[0].claim
        assert _decision_sequence(activity) == ["CONTINUE", "RE_PLAN"]


# ---------------------------------------------------------------------------
# R3 — flag OFF identity: no filter, no contract, legacy repeat behavior
# ---------------------------------------------------------------------------

class TestFlagOffIdentity:
    def test_flag_off_replan_repeats_legacy_path(self, api_client, tmp_path):
        """Flag OFF: no GSP-7 filter and no contract — the repeat IS
        adopted and re-executed (the known-bad path runs twice), and the
        run ends in the legacy bounded way (the completion criteria are
        never met on this lineage — the only success observation carries
        an evidence gap — so the exhausted proposer's STOP is coerced and
        the next re-plan fails bounded)."""
        client, store, monkeypatch = api_client
        activity, task_id = _run_goal(
            client, monkeypatch, tmp_path,
            plans=[PLAN1, PLAN2_REPEAT],
            decisions=["CONTINUE", "RE_PLAN"],
            gsp_on=False, with_goal=True)

        # legacy semantics preserved: the repeat IS adopted and executed
        assert activity["plan_revisions"] == 2
        assert [s["type"] for s in activity["plan_steps"]] == [
            "inspect_self_opposite_trades"]
        assert len(_step_starts(activity, "inspect_self_opposite_trades")) == 2

        # legacy bounded end (no GSP-7 fast-fail; the loop's own caps end it)
        assert activity["agent_status"] == "failed"
        assert activity["stop_reason"] == "replan_failure"

        # no GSP artifacts when the flag is OFF
        assert activity["goal_contract"] is None
        assert activity["goal_outcome"] is None
        assert activity["observation_associations"] == []
        state = store.get_agent_state(task_id)
        assert state.goal_contract is None
        assert state.planning_coverage == []

"""Agent Activity API — contract + deterministic E2E tests.

Covers the three additive backend pieces from the Agent Activity baseline
(docs/baselines/2026-09-19-agent-activity-v1.md §15):

- POST /api/v2/investigations/{id}/goal  (loop execution path: starts a
  bounded loop; deterministic runner injected via loop_runner_factory)
- GET  /api/v2/tasks/{task_id}/activity  (bounded, UI-safe projection;
  404 NO_AGENT_ACTIVITY for pre-loop turn-local tasks)
- state-borne activity records (sequence-ordered, bounded, user-safe —
  no raw payloads, no chain-of-thought)

The E2E case runs an actual deterministic loop through the API and
validates the full payload shape the frontend consumes.
"""

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner
from app.executor_v2 import ExecutorV2, ToolProvider
from app.models import (
    InvestigationContext,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from eval.fixtures import ScriptedLLM, plan_json

CASE = "U00299"
INV = "INV-ACTIVITY-1"


# ---------------------------------------------------------------------------
# Deterministic loop parts
# ---------------------------------------------------------------------------

class ScriptedProvider(ToolProvider):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.last = self.results[-1] if results else None

    def has(self, name):
        return True

    def get(self, name):
        def invoke(args):
            if self.results:
                self.last = self.results.pop(0)
            return self.last
        return invoke


class ScriptedProposer:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        d = self.decisions.pop(0) if self.decisions else "CONTINUE"
        return __import__("app.agent_loop", fromlist=["DecisionProposal"]) \
            .DecisionProposal(decision=d, reason="deterministic scenario",
                              source="heuristic")


def case_fetch_success():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"case_id": CASE, "findings": [], "evidence_refs": [],
              "signal_refs": [], "policy_refs": []},
        evidence_refs=[])


def deterministic_factory(decisions):
    """Fresh runner per goal call (scripts do not bleed across runs).
    Case-level plan (no finding focus in this scenario). Findings resolve
    from the fixture (never live RP) so the scenario stays deterministic
    regardless of Risk Platform availability."""
    def factory(store: TaskStoreV2) -> AgentLoopRunner:
        return AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("case_intake", ["fetch_case"])])),
            executor=ExecutorV2(ScriptedProvider([case_fetch_success()])),
            store=store,
            proposer=ScriptedProposer(list(decisions)),
            findings_provider=lambda: focused_findings())
    return factory


def focused_findings():
    from app.models import Finding, FindingCapability
    return [Finding(finding_id="F2", case_id=CASE, type="rule_signal",
                    title="Coordinated Trading Pattern", summary="s",
                    capabilities=FindingCapability.model_validate(
                        ["timeline", "signal_explain", "policy_lookup",
                         "self_opposite_trades"]))]


@pytest.fixture()
def client(tmp_path):
    import tempfile
    from app.api.investigations import InvestigationAPI
    import app.api.investigations as inv_module
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "activity_api.db"))
    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("case_intake",
                           ["fetch_case", "generate_artifact"])])),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda case_id: [],
        loop_runner_factory=deterministic_factory(["STOP"]),
    )
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), store, api


def _create_investigation(client):
    r = client.post("/api/v2/investigations", json={
        "case_reference": CASE})
    assert r.status_code == 200, r.text
    return r.json()["investigation"]["investigation_id"]


def _await_terminal(client, task_id, timeout_s=10.0):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/v2/tasks/{task_id}/activity")
        if r.status_code == 200:
            last = r.json()
            if last["is_terminal"]:
                return last
        time.sleep(0.05)
    raise AssertionError(f"loop did not terminate; last={last}")


# ---------------------------------------------------------------------------
# Goal execution path + activity contract
# ---------------------------------------------------------------------------

class TestGoalExecutionPath:
    def test_goal_starts_bounded_loop_and_activity_reaches_terminal(
            self, client):
        c, store, _ = client
        inv = _create_investigation(c)
        r = c.post(f"/api/v2/investigations/{inv}/goal",
                   json={"goal": "investigate the case timeline"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["investigation_id"] == inv
        assert body["agent_status"] == "running"

        activity = _await_terminal(c, body["task_id"])
        assert activity["agent_status"] == "completed"
        assert activity["stop_reason"] == "goal_satisfied"
        assert activity["goal"] == "investigate the case timeline"
        assert activity["is_terminal"] is True

    def test_goal_unknown_investigation_404(self, client):
        c, _, _ = client
        r = c.post(f"/api/v2/investigations/{uuid.uuid4().hex}/goal",
                   json={"goal": "x"})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "INVESTIGATION_NOT_FOUND"

    def test_goal_blank_rejected(self, client):
        c, _, _ = client
        inv = _create_investigation(c)
        r = c.post(f"/api/v2/investigations/{inv}/goal", json={"goal": "  "})
        assert r.status_code == 422


class TestActivityContract:
    def test_projection_is_whitelisted_and_ordered(self, client):
        c, _, _ = client
        inv = _create_investigation(c)
        goal = c.post(f"/api/v2/investigations/{inv}/goal",
                      json={"goal": "show the timeline"}).json()
        activity = _await_terminal(c, goal["task_id"])

        # whitelisted top-level fields only
        assert set(activity.keys()) == {
            "task_id", "investigation_id", "case_id", "goal",
            "criteria_kind", "focused_finding_id", "agent_status",
            "stop_reason", "iteration", "max_iterations", "plan_revisions",
            "plan_steps", "artifact_ids", "activity",
            "goal_representation", "is_terminal"}
        # NO internal state structures leak
        for forbidden in ("observations", "decisions", "retries",
                          "invalid_proposals", "response"):
            assert forbidden not in activity

        records = activity["activity"]
        assert records, "activity records must exist"
        seqs = [r["seq"] for r in records]
        assert seqs == sorted(seqs)                       # stable ordering
        assert len(set(seqs)) == len(seqs)                # no duplicates
        for rec in records:
            assert set(rec.keys()) == {
                "seq", "event_type", "timestamp", "iteration", "step_id",
                "step_type", "tool_name", "outcome", "decision",
                "agent_status", "stop_reason", "summary", "artifact_ids"}
            assert rec["summary"] is None or len(rec["summary"]) <= 300

        # the loop lifecycle is reconstructable from records alone
        types = [r["event_type"] for r in records]
        assert types[0] == "agent.started"
        assert types[-1] == "agent.completed"
        for required in ("context.resolved", "plan.created", "step.started",
                         "tool.called", "tool.completed",
                         "observation.recorded", "decision.made",
                         "step.completed"):
            assert required in types, required
        # decision record carries the decision + user-safe reason
        decision_rows = [r for r in records if r["event_type"] == "decision.made"]
        assert decision_rows[-1]["decision"] == "STOP"
        assert decision_rows[-1]["summary"]

    def test_activity_records_carry_no_raw_payloads_or_cot(self, client):
        c, _, _ = client
        inv = _create_investigation(c)
        goal = c.post(f"/api/v2/investigations/{inv}/goal",
                      json={"goal": "show the timeline"}).json()
        activity = _await_terminal(c, goal["task_id"])
        blob = json.dumps(activity)
        # raw RP payload markers and CoT markers never appear
        for forbidden in ("risk_reason", "price", "quantity",
                          "transaction_evidence", "chain_of_thought",
                          "reasoning"):
            assert forbidden not in blob, forbidden

    def test_bounded_activity_window(self, client):
        # a bounded run still produces a bounded record list
        c, _, _ = client
        inv = _create_investigation(c)
        goal = c.post(f"/api/v2/investigations/{inv}/goal",
                      json={"goal": "show the timeline"}).json()
        activity = _await_terminal(c, goal["task_id"])
        assert len(activity["activity"]) <= 200


class TestPreLoopFallback:
    def test_turn_local_task_has_no_agent_activity(self, client):
        c, store, api = client
        inv = _create_investigation(c)
        # a turn-local run through the EXISTING pipeline (real tools, stubs)
        from app.models import InvestigationContext
        result = api.service.run_turn(
            user_request="Investigate U00299",
            context=InvestigationContext(case_id=CASE),
            case_findings=[], investigation_id=inv, task_store=store)
        r = c.get(f"/api/v2/tasks/{result.task.task_id}/activity")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "NO_AGENT_ACTIVITY"

    def test_unknown_task_404(self, client):
        c, _, _ = client
        r = c.get(f"/api/v2/tasks/{uuid.uuid4().hex}/activity")
        assert r.status_code == 404

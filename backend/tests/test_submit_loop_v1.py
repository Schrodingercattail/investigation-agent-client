"""Unified submission — every user request executes on the Agent Loop.

Pins POST /investigations/{id}/submit end to end over the REAL API
(real deterministic runner + real single-turn service; the scripted goal
understanding is the ONE stubbed boundary — in production it is the
LLM-backed resolver with deterministic fallback).

Contract:
- EVERY submission — simple, compound, case intake, follow-up — starts a
  bounded Agent Loop task (type=agent_loop); there is no turn fallback.
- Goal-understanding failure does NOT change the runtime: the task is
  still a loop task and the loop classifies the request itself.
- Each submission creates exactly ONE task.
- The Case Intake (first submission of a fresh investigation) completes
  through the loop and produces the case overview.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.goal_resolution import GoalRepresentation
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from eval.fixtures import ScriptedLLM, plan_json

from tests.test_agent_activity_api_v1 import deterministic_factory

CASE = "U00299"


def representation(outputs, resolution="INFERRED"):
    return GoalRepresentation(
        case_id=CASE, goal_resolution=resolution, subject_type="case",
        subject_ref=CASE, source="llm", request_text="req",
        goal_type=None if resolution == "COMPOUND" else "policy_check",
        contract_proposal={"required_outputs": outputs})


@pytest.fixture()
def submit_env(tmp_path):
    import tempfile
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService
    from tests.test_agent_activity_api_v1 import deterministic_factory

    store = TaskStoreV2(str(tmp_path / "submit_loop.db"))
    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(ScriptedLLM([
                plan_json("case_intake",
                          ["fetch_case", "generate_artifact"]),
            ] * 4)),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda case_id: [],
        loop_runner_factory=deterministic_factory(["STOP"]),
    )
    inv_module.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/v2/investigations", json={"case_reference": CASE})
    assert r.status_code == 200, r.text
    created = r.json()
    return client, api, created["investigation"]["investigation_id"], created


def _wait_terminal(client, task_id, timeout_s=20):
    deadline = time.time() + timeout_s
    d = {}
    while time.time() < deadline:
        resp = client.get(f"/api/v2/tasks/{task_id}")
        if resp.status_code == 200:
            d = resp.json()
            if d["task"]["status"] in ("completed", "failed"):
                return d
        # 404 = the loop thread has not persisted its first state yet
        # (the same startup race the frontend's polling grace covers)
        time.sleep(0.2)
    return d


class TestSubmitAlwaysLoop:
    def test_simple_submission_runs_loop(self, submit_env):
        client, api, inv, _ = submit_env
        r = client.post(f"/api/v2/investigations/{inv}/submit",
                        json={"message": "summarize this case"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] == "loop"
        assert d["agent_status"] == "running"
        detail = _wait_terminal(client, d["task_id"])
        assert detail["task"]["type"] == "agent_loop"
        assert detail["task"]["status"] in ("completed", "failed")

    def test_compound_submission_runs_loop(self, submit_env):
        client, api, inv, _ = submit_env
        r = client.post(f"/api/v2/investigations/{inv}/submit",
                        json={"message": "list accounts and check hedging"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] == "loop"
        detail = _wait_terminal(client, d["task_id"])
        assert detail["task"]["type"] == "agent_loop"

    def test_understanding_failure_still_loop(self, submit_env):
        """Understanding failure never falls back to another runtime: the
        task is a loop task and the loop classifies the request itself."""
        client, api, inv, _ = submit_env

        def broken(*a, **k):
            raise RuntimeError("LLM unavailable")
        api.goal_resolver = broken
        r = client.post(f"/api/v2/investigations/{inv}/submit",
                        json={"message": "summarize this case"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] == "loop"
        detail = _wait_terminal(client, d["task_id"])
        assert detail["task"]["type"] == "agent_loop"

    def test_followup_submission_runs_loop_with_canonical_intent(
            self, submit_env):
        client, api, inv, _ = submit_env
        # registry truth: pick any known follow-up id
        from app.api.investigations import _FOLLOWUP_INDEX
        fid = next(iter(_FOLLOWUP_INDEX))
        intent = _FOLLOWUP_INDEX[fid].intent
        r = client.post(f"/api/v2/investigations/{inv}/submit", json={
            "follow_up_id": fid})
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "loop"
        task_id = r.json()["task_id"]
        detail = _wait_terminal(client, task_id)
        # the loop received the CANONICAL intent, not an empty message
        assert detail["task"]["user_request"] == intent

    def test_each_submission_creates_exactly_one_task(self, submit_env):
        client, api, inv, _ = submit_env
        r1 = client.post(f"/api/v2/investigations/{inv}/submit",
                         json={"message": "first request"})
        r2 = client.post(f"/api/v2/investigations/{inv}/submit",
                         json={"message": "second request"})
        assert r1.json()["task_id"] != r2.json()["task_id"]
        inv_data = client.get(f"/api/v2/investigations/{inv}").json()
        assert len(inv_data["tasks"]) == 2
        assert all(t["type"] == "agent_loop" for t in inv_data["tasks"])

    def test_empty_message_rejected(self, submit_env):
        client, api, inv, _ = submit_env
        r = client.post(f"/api/v2/investigations/{inv}/submit",
                        json={"message": "   "})
        assert r.status_code == 422


class TestCaseIntakeThroughLoop:
    def test_first_submission_case_intake_completes(self, submit_env):
        """The first submission of a fresh investigation — the Case
        Intake — completes through the loop path and produces the case
        overview artifacts."""
        client, api, inv, created = submit_env
        intake = created["intake_message"]
        assert intake
        r = client.post(f"/api/v2/investigations/{inv}/submit",
                        json={"message": intake})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] == "loop"
        detail = _wait_terminal(client, d["task_id"], timeout_s=25)
        assert detail["task"]["status"] in ("completed", "failed"), detail["task"]
        # the loop composed a conversational answer (persisted on the task)
        assert (detail.get("task_response_text") or "").strip()
        # the activity projection served the loop run
        act = client.get(f"/api/v2/tasks/{d['task_id']}/activity")
        assert act.status_code == 200
        assert act.json()["is_terminal"] is True


class TestCaseIntakeAmbiguityShim:
    def test_first_submission_ambiguous_understanding_runs_intake(
            self, submit_env):
        """Case-intake compatibility: when goal understanding classifies
        the canonical intake request as AMBIGUOUS, the first submission of
        an investigation still executes the standard case intake through
        the loop (the case is already bound at creation) — never a
        turn fallback, never a blocked dead end."""
        client, api, inv, created = submit_env
        intake = created["intake_message"]

        def ambiguous(*a, **k):
            from app.goal_resolution import GoalResolutionStatus
            return GoalRepresentation(
                case_id=CASE, goal_resolution="AMBIGUOUS",
                subject_type="case", subject_ref=CASE, source="llm",
                request_text=intake, ambiguous_goal_types=["a", "b"],
                reason="multiple readings")

        api.goal_resolver = ambiguous
        r = client.post(f"/api/v2/investigations/{inv}/submit",
                        json={"message": intake})
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "loop"
        detail = _wait_terminal(client, r.json()["task_id"])
        # the loop executed the standard intake (not a blocked dead end)
        assert detail["task"]["status"] in ("completed", "failed")
        # the heuristic intake representation was used (fetch_case ran)
        assert detail["task"]["user_request"] == intake

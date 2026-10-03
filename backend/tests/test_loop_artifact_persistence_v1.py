"""AL-2.5 regression — loop-produced artifacts must be readable through the
investigation read model.

Regression for the defect found by the AL-2.5 real-RP browser E2E: the
goal loop persisted artifact IDS (state/task) but never the artifact
BODIES — GET /tasks/{id}/artifacts and the reopen restore (both filter
the investigation session by task.artifact_ids) served nothing for loop
runs, so the browser Artifacts panel stayed empty and the bundle was
unrecoverable.

Fix under test: the AgentLoopResult now carries the produced artifact
bodies and the API goal-run/resume path mirrors them into the
investigation session (the same read model the turn path records).

Deterministic seams only (scripted planner LLM / proposer via the
documented loop_runner_factory); executor and tools are the REAL
defaults (RP boundary controlled with the repo-standard rp_patches —
this is a component/regression test, NOT the real-RP E2E).
"""

import tempfile
import time

from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner
from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.context_resolution import ContextResolver
from app.executor_v2 import ExecutorV2, default_tool_provider
from app.goal_resolution import understand_goal
from app.investigation_service import InvestigationService
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import (
    ScriptedLLM,
    make_findings,
    rp_patches,
)

RESOLVE_CASE_LEVEL = (
    '{"resolution": "UNRESOLVED", "candidate_finding_ids": [], '
    '"reason": "case-level request"}')
PLAN_INTAKE_WITH_ARTIFACT = (
    '{"skill_id": "case_intake", "goal": "g", "steps": ['
    '{"type": "fetch_case", "reason": "r"}, '
    '{"type": "generate_artifact", "reason": "r2"}]}')


class ScriptedProposer:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        from app.agent_loop import DecisionProposal
        d = self.decisions.pop(0) if self.decisions else "STOP"
        return DecisionProposal(decision=d, reason="scripted",
                                source="heuristic", valid=True)


def _make_env():
    store = TaskStoreV2(tempfile.mkdtemp() + "/al25_regr.db")
    findings = make_findings()

    def runner_factory(s):
        return AgentLoopRunner(
            store=s,
            executor=ExecutorV2(default_tool_provider()),
            resolver=ContextResolver(
                llm_provider=ScriptedLLM([RESOLVE_CASE_LEVEL]),
                llm_assist=True),
            planner=PlannerV2(ScriptedLLM([PLAN_INTAKE_WITH_ARTIFACT])),
            goal_resolver=lambda req, fid, case: understand_goal(
                req, resolved_finding_id=fid, case_id=case, use_llm=False),
            findings_provider=lambda: [
                f.model_copy(deep=True) for f in findings],
            proposer=ScriptedProposer(["CONTINUE", "STOP"]))

    service = InvestigationService(
        planner=PlannerV2(ScriptedLLM([PLAN_INTAKE_WITH_ARTIFACT])),
        executor=None, task_store=store)
    api = InvestigationAPI(service=service, store=store,
                           loop_runner_factory=runner_factory,
                           case_findings_provider=lambda case_id: findings)
    old_api = inv_api.api
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    return client, store, old_api


def _poll(client, task_id, timeout=30):
    a = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = client.get(f"/api/v2/tasks/{task_id}/activity").json()
        if a.get("is_terminal"):
            return a
        time.sleep(0.2)
    return a


def test_loop_artifact_reachable_via_read_model(monkeypatch):
    client, store, old_api = _make_env()
    try:
        with rp_patches()[0], rp_patches()[1]:
            inv = client.post("/api/v2/investigations",
                              json={"case_id": "U00299"}).json()
            inv_id = inv["investigation"]["investigation_id"]
            run = client.post(f"/api/v2/investigations/{inv_id}/goal",
                              json={"goal": "Investigate U00299"}).json()
            activity = _poll(client, run["task_id"])
        assert activity["agent_status"] == "completed"
        assert activity["artifact_ids"], "loop produced no artifact"
        art_id = activity["artifact_ids"][0]

        # 1) task artifacts endpoint (the UI's read model) serves the BODY
        body = client.get(f"/api/v2/tasks/{run['task_id']}/artifacts").json()
        arts = body["artifacts"]
        assert len(arts) == 1 and arts[0]["artifact_id"] == art_id
        art_text = str(arts[0])
        assert "U00299" in art_text
        # real tool-produced content (from the RP-shaped fetch result)
        assert "Coordinated Trading Pattern" in art_text

        # 2) task detail (the reopen restore surface) serves it too
        detail = client.get(f"/api/v2/tasks/{run['task_id']}").json()
        assert any(a["artifact_id"] == art_id for a in detail["artifacts"])

        # 3) the session holds no duplicates across repeated mirroring
        session = store.get_session(inv_id)
        ids = [a.get("artifact_id") for a in session.get("artifacts", [])]
        assert ids == [art_id]
    finally:
        inv_api.api = old_api


def test_loop_without_artifacts_leaves_read_model_unchanged(monkeypatch):
    """A run that produces no artifact (fetch-only plan) must not write
    any session artifacts entry."""
    store = TaskStoreV2(tempfile.mkdtemp() + "/al25_regr2.db")
    findings = make_findings()
    plan_fetch_only = (
        '{"skill_id": "case_intake", "goal": "g", "steps": '
        '[{"type": "fetch_case", "reason": "r"}]}')

    def runner_factory(s):
        return AgentLoopRunner(
            store=s,
            executor=ExecutorV2(default_tool_provider()),
            resolver=ContextResolver(
                llm_provider=ScriptedLLM([RESOLVE_CASE_LEVEL]),
                llm_assist=True),
            planner=PlannerV2(ScriptedLLM([plan_fetch_only])),
            goal_resolver=lambda req, fid, case: understand_goal(
                req, resolved_finding_id=fid, case_id=case, use_llm=False),
            findings_provider=lambda: [
                f.model_copy(deep=True) for f in findings],
            proposer=ScriptedProposer(["STOP"]))

    service = InvestigationService(
        planner=PlannerV2(ScriptedLLM([plan_fetch_only])),
        executor=None, task_store=store)
    api = InvestigationAPI(service=service, store=store,
                           loop_runner_factory=runner_factory,
                           case_findings_provider=lambda case_id: findings)
    old_api = inv_api.api
    inv_api.api = api
    try:
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        with rp_patches()[0], rp_patches()[1]:
            inv = client.post("/api/v2/investigations",
                              json={"case_id": "U00299"}).json()
            inv_id = inv["investigation"]["investigation_id"]
            run = client.post(f"/api/v2/investigations/{inv_id}/goal",
                              json={"goal": "Investigate U00299"}).json()
            activity = _poll(client, run["task_id"])
        assert activity["agent_status"] == "completed"
        assert activity["artifact_ids"] == []
        session = store.get_session(inv_id) or {}
        assert not session.get("artifacts")
    finally:
        inv_api.api = old_api

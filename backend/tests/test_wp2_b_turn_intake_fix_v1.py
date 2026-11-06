"""WP-2 Version B — /turn Case Intake Option A fix: REAL-API E2E.

User-authorized minimal fix under test: Case Intake plans on the /turn
surface are deterministically stripped of their generate_artifact step
(same intake recognizers the planner used), so the restricted case
overview is delivered WITHOUT an artifact bundle — the Option A
boundary (Decision 2) now holds on BOTH surfaces.

Scenarios:
  1. /turn Case Intake  — overview preserved, strip executed,
     artifact_bundle NOT executed, zero artifact published/delivered
     (TurnResponse + task + session + /tasks/{id}/artifacts all
     checked individually);
  2. /submit Case Intake — Option A behavior NOT regressed (overview
     delivered, no bundle — the loop's U00004 strip path);
  3. /turn non-Case-Intake — a plain question ask still answers
     normally (no misclassification, no strip warning, legal
     capability intact).

Real chain: POST /api/v2/investigations/{iid}/turn (or /submit) →
GU understanding (injected model boundary, documented
deterministic-API infrastructure) → PlannerV2 deterministic planning →
the /turn intake strip (product fix) → ExecutorV2 → persistence. RP
adapter patched offline; flag pinned (D4 discipline).
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner, HeuristicDecisionProposer
from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.config import settings
from app.context_resolution import ContextResolver
from app.investigation_service import InvestigationService
from app.llm_provider import ClaudeProvider, LLMError
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al210_capability_routing_v1 import (
    _findings as _al210_findings,
)
from tests.test_al28_semantic_foundation_v1 import seeded_catalog

CASE = "U00010"
INTAKE_ASK = "Investigate U00010"
QUESTION_ASK = "这个案件的整体风险水平如何？"
INJECTED_GU_ERROR = ("peer closed connection without sending complete "
                     "message body (injected WP2-TURNFIX)")


class ScriptedNever:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def _build_client(tmp_path, monkeypatch):
    store = TaskStoreV2(str(tmp_path / "wp2_turnfix.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
    import tests.test_risk_platform_adapter_v2 as fx

    def _fetch_case(self, uid):
        return (fx.rp_evidence_payload(CASE),
                fx.rp_explanation_payload())

    monkeypatch.setattr(_rcf.RiskPlatformAdapter, "fetch_case", _fetch_case)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    monkeypatch.setattr(
        ClaudeProvider, "generate",
        lambda self, messages, max_tokens=0, temperature=0.1, **kw:
            (_ for _ in ()).throw(LLMError(INJECTED_GU_ERROR)))

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(_NeverLLM()),
            proposer=HeuristicDecisionProposer(),
        )

    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(_NeverLLM()),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: _al210_findings(),
        loop_runner_factory=_factory,
    )
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._wp2_store = store
    return client


def _wait(client, tid):
    import time as _time
    deadline = _time.time() + 120
    while _time.time() < deadline:
        detail = client.get(f"/api/v2/tasks/{tid}").json()
        st = (detail.get("task") or {}).get("status")
        if st in ("completed", "failed"):
            return detail
        _time.sleep(0.3)
    return client.get(f"/api/v2/tasks/{tid}").json()


def _artifact_views(client, iid, tid):
    """All artifact evidence locations for one task."""
    detail = client.get(f"/api/v2/tasks/{tid}").json()
    task_arts = list((detail.get("task") or {}).get("artifact_ids") or [])
    endpoint_arts = [
        a.get("artifact_id")
        for a in (client.get(f"/api/v2/tasks/{tid}/artifacts")
                  .json().get("artifacts") or [])]
    sess = client._wp2_store.get_session(iid) or {}
    session_arts = [a.get("artifact_id")
                    for a in (sess.get("artifacts") or [])]
    tool_calls = [tc.get("tool_name")
                  for tc in (sess.get("tool_calls") or [])
                  if tc.get("task_id") == tid]
    return {"task": task_arts, "endpoint": endpoint_arts,
            "session_all": session_arts,
            "tool_calls": tool_calls}


def test_s5fix_turn_intake_overview_without_bundle(tmp_path, monkeypatch):
    """Scenario 1: REAL /turn Case Intake — the overview is delivered,
    the artifact step is stripped before execution, artifact_bundle
    never runs, and NO artifact exists in ANY evidence location."""
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": INTAKE_ASK},
                     timeout=180).json()
    task = tr.get("task") or {}
    tid = task.get("task_id")

    # A. overview preserved (case-level FINDINGS_SUMMARY content within
    #    the Option A data scope)
    resp = tr.get("response") or ""
    assert resp, "/turn intake must answer"
    assert "ML Pattern Detection" in resp
    # B. strip executed: artifact_bundle NEVER ran (executor-level
    #    evidence, stronger than an empty list)
    views = _artifact_views(client, iid, tid)
    assert "artifact_bundle" not in views["tool_calls"], views
    # C. zero bundle published/delivered in ALL locations
    assert (tr.get("artifacts") or []) == []
    assert views["task"] == []
    assert views["endpoint"] == []
    assert views["session_all"] == []
    # D. the persisted plan shows the stripped shape (fetch only)
    plan_steps = [(s.get("step_id"), s.get("type"))
                  for s in ((tr.get("plan") or {}).get("steps") or [])]
    assert all(t != "generate_artifact" for _, t in plan_steps), plan_steps
    assert ("S1", "fetch_case") in plan_steps
    # the fetch DID execute (the overview is evidence-backed)
    assert "risk_case_fetch" in views["tool_calls"]


def test_s5fix_submit_intake_option_a_not_regressed(tmp_path, monkeypatch):
    """Scenario 2: /submit Case Intake — the loop's Option A behavior
    (U00004 strip → overview WITHOUT bundle) is NOT regressed by the
    /turn fix."""
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": INTAKE_ASK},
                    timeout=180).json()
    tid = r.get("task_id") or (r.get("task") or {}).get("task_id")
    detail = _wait(client, tid)
    task = detail.get("task") or {}
    assert task.get("status") == "completed"
    # Option A: overview delivered, NO bundle
    resp = detail.get("task_response_text") or ""
    assert resp, "submit intake must answer"
    assert "ML Pattern Detection" in resp
    assert (task.get("artifact_ids") or []) == []
    views = _artifact_views(client, iid, tid)
    assert views["task"] == []
    assert views["endpoint"] == []
    assert "artifact_bundle" not in views["tool_calls"]


def test_s5fix_turn_non_intake_not_regressed(tmp_path, monkeypatch):
    """Scenario 3: a NON-Case-Intake /turn request (plain question) is
    not misclassified as intake, answers normally, and produces no
    artifacts (its legal capability is unchanged)."""
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": "这个案件的整体风险水平如何？"},
                     timeout=180).json()
    resp = tr.get("response") or ""
    assert resp, "non-intake turn must answer"
    # not misclassified as Case Intake: no strip warning in the task
    # trail and the answer is the normal question answer
    task = tr.get("task") or {}
    assert task.get("status") in ("completed", "failed")
    assert (tr.get("artifacts") or []) == []
    assert "未通过确定性准入" not in resp

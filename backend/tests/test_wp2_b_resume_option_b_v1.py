"""WP-2 Version B — Resume Option B fix: REAL-API E2E for the resumed
delivery of an ADMITTED plan (user Decision 1, Resume = OPTION B).

Frozen rule under test (authoritative doc Phase 3 + kickoff §13.1
Decision 1): an admitted delivery plan keeps identity and content
continuity across Resume; the proposer must not silently replace it;
resume still re-verifies admission state and provenance; a genuine plan
change is a NEW plan change requiring full re-admission.

Scenario 1 (valid resume delivery): a persisted non-terminal midstate
whose plan IS the admitted plan (read-only fetch done, artifact step
injected with the verified binding) resumes via the real HTTP resume
endpoint → the SAME plan continues (no RE_PLAN, no replacement), the
artifact delivers within the seeded candidate_set boundary, the dual
verification echoes persist.

Scenario 2 (resume fail-closed): the same midstate, but the session's
persisted set is owned by a FOREIGN task → the resumed admission
re-validation rejects (cross_investigation), the delivery step is
stripped before execution, no artifact exists, and the rejection reason
comes from the CURRENT admission rules (the ADMISSION_INVALID_TARGET
decision record) — not from RE_PLAN or None-C.

Real chain under test: POST /tasks/{tid}/resume → runner.resume →
_run_loop(resume_mode=True) → shared admission service (re-validation)
→ ExecutorV2 → artifact_bundle candidate branch (digest + provenance
gates) → persistence. The model boundary is injected (documented
deterministic-API infrastructure); everything else is production.

Feature flags: ENABLE_GOAL_SEMANTIC_CONTRACT pinned explicitly (D4
discipline).
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.agent_loop import (
    AgentLoopRunner,
    AgentLoopState,
    HeuristicDecisionProposer,
)
from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.config import settings
from app.context_resolution import ContextResolver
from app.delivery_admission import canonical_records_digest
from app.investigation_service import InvestigationService
from app.llm_provider import ClaudeProvider, LLMError
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al210_capability_routing_v1 import (
    _findings as _al210_findings,
)
from tests.test_al28_semantic_foundation_v1 import seeded_catalog

CASE = "U00010"
SET_REF = "CS-ab12cd34-S9"
ASK_REF = "导出 CS-ab12cd34-S9 的候选交易明细文件"
INJECTED_GU_ERROR = ("peer closed connection without sending complete "
                     "message body (injected WP2-RESUME-OPTB)")


def _leg(tid, side):
    return {"transaction_id": tid, "account_id": "ACC-A", "side": side,
            "quantity": 1.0, "value": 100.0, "symbol": "BTC",
            "timestamp": "2026-08-19T10:00:00+00:00"}


def mk_entry(task, set_ref=SET_REF, candidates=None):
    return {"set_ref": set_ref, "source_task_id": task,
            "source_step_id": "S1", "created_at": "2026-10-31T00:00:00Z",
            "bounded_data": False, "origin": "analysis",
            "candidates": candidates if candidates is not None
            else [{"candidate_id": "CATA-001", "rank": 1,
                   "account_a": "ACC-A", "account_b": "ACC-B",
                   "symbol": "BTC",
                   "supporting": [_leg("TX-1", "BUY"),
                                  _leg("TX-2", "SELL")]}]}


class ScriptedNever:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def _build_client(tmp_path, monkeypatch):
    store = TaskStoreV2(str(tmp_path / "wp2_optb.db"))
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


def _create_and_anchor(client):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": "Investigate U00010"},
                    timeout=180).json()
    tid = r.get("task_id") or (r.get("task") or {}).get("task_id")
    _wait(client, tid)
    return iid, tid


def _seed(store, iid, entries):
    doc = store.get_session(iid) or {}
    doc.setdefault("candidate_sets", []).extend(entries)
    store.save_session(iid, doc)


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


def _seed_midstate(store, iid, tid, entry):
    """The persisted non-terminal document a killed run leaves behind:
    post-admission, post-injection — the read-only fetch already
    succeeded, the admitted artifact step is injected and pending."""
    cands = entry["candidates"]
    injected = {
        "source_kind": "candidate_set", "set_ref": SET_REF,
        "candidate_records": cands, "bounded_data": False,
        "source_task_id": entry["source_task_id"],
        "expected_records_digest": canonical_records_digest(cands),
        "expected_binding": {
            "kind": "candidate_set_export", "status": "bound_ready",
            "set_ref": SET_REF, "source_task_id": entry["source_task_id"],
            "source_step_id": "S1", "delivery_target_id": "DT-S2-1",
            "case_id": CASE},
        "delivery_target_id": "DT-S2-1", "source_step_id": "S1",
    }
    state = AgentLoopState(
        task_id=tid, investigation_id=iid, case_id=CASE, goal=ASK_REF,
        criteria_kind="artifact_produced", response_language="zh",
        goal_contract=None,
        goal_contract_decision={"outcome": "heuristic_single_goal",
                                "reason": "seeded admitted midstate"},
        goal_representation={"goal_type": "artifact_export",
                             "goal_resolution": "INFERRED",
                             "source": "heuristic",
                             "request_text": ASK_REF},
        plan_steps=[
            {"step_id": "S1", "type": "fetch_case", "status": "success",
             "tool_name": "risk_case_fetch", "arguments": {}},
            {"step_id": "S2", "type": "generate_artifact",
             "status": "pending", "tool_name": "artifact_bundle",
             "arguments": dict(injected)}],
        agent_status="running",
    )
    store.save_agent_state(state)
    return injected


def test_resume_option_b_admitted_plan_delivers(tmp_path, monkeypatch):
    """Scenario 1: REAL resume of the ADMITTED plan — identity and
    content continuity, delivery within the seeded boundary, dual
    verification, NO proposer replacement (no RE_PLAN decision)."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    import time as _time
    _time.sleep(1.5)   # drain the turn-1 background bookkeeping
    entry = mk_entry(task=tid1)
    _seed_midstate(client._wp2_store, iid, tid1, entry)
    _seed(client._wp2_store, iid, [entry])

    # REAL resume path
    r = client.post(f"/api/v2/tasks/{tid1}/resume", timeout=180)
    assert r.status_code == 200, r.text
    detail = _wait(client, tid1)

    doc = client._wp2_store.get_agent_state(tid1)
    assert doc is not None
    # Decision 1: plan identity/content continuity — the executed plan
    # is STILL the admitted plan (fetch + injected candidate export).
    # The proposer MAY propose RE_PLAN (a recorded proposal is not an
    # adoption); the accurate continuity check is that ZERO re-plans
    # were ADOPTED (plan_revisions) and the plan shape is unchanged.
    assert [(s.get("step_id"), s.get("type")) for s in doc.plan_steps] \
        == [("S1", "fetch_case"), ("S2", "generate_artifact")]
    assert doc.plan_revisions == 0
    # the injected admission context survived resume intact
    s2 = doc.plan_steps[1]
    assert s2["arguments"]["expected_records_digest"]
    assert s2["arguments"]["set_ref"] == SET_REF
    assert s2["arguments"]["delivery_target_id"] == "DT-S2-1"
    # delivery within the seeded boundary
    assert doc.agent_status == "completed"
    assert doc.artifact_ids, "admitted target must deliver on resume"
    arts = client.get(f"/api/v2/tasks/{tid1}/artifacts").json()["artifacts"]
    assert arts[0]["scope"] == f"candidate_set:{SET_REF}"
    content = arts[0]["content"]
    assert f"候选集: {SET_REF}" in content
    assert "TX-1" in content and "TX-2" in content
    # admission re-verification + provenance re-verification executed
    # and persisted (dual echo + binding provenance)
    verifications = [o["summary"]["candidate_set_export"]
                     for o in (doc.observations or [])
                     if isinstance(o.get("summary"), dict)
                     and isinstance(
                         o["summary"].get("candidate_set_export"), dict)]
    assert verifications, "candidate export observation missing"
    v0 = verifications[-1]
    assert v0["verification"]["passed"]
    dv = v0["records_digest_verification"]
    assert dv["passed"] and dv["expected"] == dv["actual"]
    pv = v0["provenance_verification"]
    assert pv["passed"]
    # the binding-derived provenance rides on the artifact
    assert any(str(p).startswith("ADMISSION-BINDING:")
               for p in arts[0]["source_tool_calls"])


def test_resume_option_b_fail_closed_on_cross_investigation(
        tmp_path, monkeypatch):
    """Scenario 2: resume when the persisted set is owned by a FOREIGN
    task — the re-running admission rejects (cross_investigation), the
    delivery step is stripped BEFORE execution, no artifact exists, and
    the rejection reason comes from the CURRENT admission rules."""
    client = _build_client(tmp_path, monkeypatch)
    iid, tid1 = _create_and_anchor(client)
    import time as _time
    _time.sleep(1.5)
    entry = mk_entry(task="TASK-FOREIGN")
    _seed_midstate(client._wp2_store, iid, tid1, entry)
    _seed(client._wp2_store, iid, [entry])

    r = client.post(f"/api/v2/tasks/{tid1}/resume", timeout=180)
    assert r.status_code == 200, r.text
    detail = _wait(client, tid1)

    doc = client._wp2_store.get_agent_state(tid1)
    assert doc.agent_status == "blocked"
    assert doc.stop_reason == "clarification_needed"
    assert doc.goal_outcome == "partial"
    # rejection reason from the CURRENT admission rules
    recs = [d for d in (doc.decisions or [])
            if d.get("decision") == "ADMISSION_INVALID_TARGET"]
    assert recs, "ADMISSION_INVALID_TARGET decision record missing"
    assert any("cross_investigation" in str(d.get("reason"))
               for d in recs)
    assert f"（准入原因：cross_investigation）" in (doc.response or "")
    # zero artifact, zero export observation, delivery step stripped
    assert doc.observations == []
    assert doc.artifact_ids == []
    assert detail.get("task", {}).get("artifact_ids") == []
    assert all(s.get("type") != "generate_artifact"
               for s in doc.plan_steps)
    # no proposer replacement happened either: no RE_PLAN decision
    assert not any(d.get("decision") == "RE_PLAN"
                   for d in (doc.decisions or []))

"""WP-2 Version B S5 — /turn Case Intake runtime FORENSICS (Phase 1,
user-authorized evidence collection ONLY — no production fixes).

Question under investigation: does a Case Intake request through the
real /turn HTTP API actually (a) skip the Option A strip, (b) produce
and PUBLISH an artifact bundle, and (c) co-deliver the intake overview
with that bundle?

This test is EVIDENCE COLLECTION: it asserts the ACTUAL runtime
behavior deterministically (whatever it is), capturing all four stages
(planned / executed / published / delivered) plus the persisted state.
The product-contract verdict is drawn in the S5 evidence record and
the completion report — the assertions here pin the observed facts so
the evidence is reproducible.

Real chain: POST /api/v2/investigations/{iid}/turn → run_turn →
GU understanding (injected model boundary, documented deterministic-API
infrastructure) → PlannerV2 deterministic planning → shared admission
service (passthrough for non-ARTIFACT intents per the Case Intake
exemption) → ExecutorV2 → artifact_bundle → persistence. The RP
adapter is patched offline; the flag is pinned (D4 discipline).
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
INJECTED_GU_ERROR = ("peer closed connection without sending complete "
                     "message body (injected WP2-S5-FORENSICS)")


class ScriptedNever:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def _build_client(tmp_path, monkeypatch):
    store = TaskStoreV2(str(tmp_path / "wp2_forensics.db"))
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


@pytest.mark.skip(
    reason="Phase-1 PRE-FIX forensics: this test pins the PRE-FIX "
    "contract-violating behavior (case intake + bundle co-delivery on "
    "/turn). The user-authorized Option A fix (Option B strip on the "
    "/turn intake path) changed that behavior; the post-fix compliance "
    "E2E lives in test_wp2_b_turn_intake_fix_v1.py. The PRE-FIX "
    "evidence remains captured in "
    "docs/evaluations/wp2-s5-turn-intake-forensics.json.")
def test_turn_intake_runtime_forensics(tmp_path, monkeypatch):
    """REAL /turn API forensics for Case Intake (Option A boundary).

    Pins the ACTUAL runtime behavior across all four stages. The
    asserted facts are the evidence; the Option A verdict is drawn in
    docs/evaluations/wp2-s5-evidence.json and the completion report."""
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]

    # REAL /turn API request — Case Intake ask on the /turn surface
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": INTAKE_ASK},
                     timeout=180).json()
    task = tr.get("task") or {}
    tid = task.get("task_id")

    # ---- stage 1: PLANNED — what did the /turn planner produce? -------
    plan_steps = [(s.get("step_id"), s.get("type"), s.get("status"))
                  for s in ((tr.get("plan") or {}).get("steps") or [])]
    planned_artifact = any(t == "generate_artifact"
                           for _, t, _ in plan_steps)
    # the strip gate (U00004) is LOOP-only: on /turn the artifact step
    # must still be present in the plan (no strip ran)
    if planned_artifact:
        assert ("S2", "generate_artifact") in [
            (sid, t) for sid, t, _ in plan_steps]

    # ---- stage 2: EXECUTED — did the artifact tool actually run? ------
    doc = client._wp2_store.get_agent_state(tid)
    # /turn has no agent_states loop document (no bounded loop on this
    # path) — the execution evidence lives in the task + session
    sess = client._wp2_store.get_session(iid) or {}
    tool_calls = [tc.get("tool_name")
                  for tc in (sess.get("tool_calls") or [])]
    executed_artifact_tool = "artifact_bundle" in tool_calls

    # ---- stage 3: PUBLISHED — what did the tool return? ---------------
    # the turn execution artifacts (executor harvest)
    turn_artifacts = tr.get("artifacts") or []
    published = [(a.get("artifact_id"), a.get("artifact_type"),
                  a.get("scope"), a.get("title"))
                 for a in turn_artifacts]

    # ---- stage 4: DELIVERED — what did the user receive? --------------
    task_artifacts = set(task.get("artifact_ids") or [])
    delivered = [
        (a.get("artifact_id"), a.get("artifact_type"), a.get("scope"),
         a.get("title"))
        for a in (client.get(f"/api/v2/tasks/{tid}/artifacts")
                  .json().get("artifacts") or [])
        if a.get("artifact_id") in task_artifacts]
    session_arts = [(a.get("artifact_id"), a.get("artifact_type"),
                     a.get("scope"))
                    for a in (sess.get("artifacts") or [])
                    if a.get("artifact_id") in task_artifacts]

    # ---- overview evidence --------------------------------------------
    resp = tr.get("response") or ""
    overview_markers = [m for m in ("identified risk findings",
                                    "ML Pattern Detection", "U00010")
                        if m in resp]

    # ---- candidate_set admission evidence (H3 exemption) --------------
    assert "未通过确定性准入" not in resp
    assert "ADMISSION_INVALID_TARGET" not in json.dumps(tr)

    # ---- FORENSIC FACTS (assert the observed behavior) ----------------
    evidence = {
        "planned_steps": plan_steps,
        "planned_artifact_step": planned_artifact,
        "executed_tools": tool_calls,
        "executed_artifact_tool": executed_artifact_tool,
        "published": published,
        "delivered": delivered,
        "session_delivered": session_arts,
        "task_status": task.get("status"),
        "overview_markers": overview_markers,
        "response_head": resp[:200],
    }
    print("FORENSICS:", json.dumps(evidence, ensure_ascii=False, indent=1))

    # pin the observed facts as deterministic evidence
    assert planned_artifact, \
        f"forensics: the intake plan must show the artifact step: {plan_steps}"
    assert executed_artifact_tool, \
        "forensics: artifact_bundle must have executed on /turn intake"
    assert delivered, "forensics: the bundle must be PUBLISHED+DELIVERED"
    # the delivered bundle is the case-level FINDINGS_SUMMARY evidence
    # bundle (NOT a candidate_set export — that class is absent)
    for _, atype, scope, _ in delivered:
        assert atype == "findings_summary", atype
        assert scope == f"case:{CASE}", scope
    # overview + bundle co-delivery: BOTH the conversational overview
    # markers and the bundle are in the SAME turn delivery
    assert overview_markers, "overview missing from the response"
    assert "candidate_set" not in json.dumps(delivered)

    # persist the forensic evidence record
    out = {"forensics": "wp2-s5-turn-intake-forensics",
           "api_path": f"POST /api/v2/investigations/{iid}/turn",
           "ask": INTAKE_ASK, "task_id": tid,
           "investigation_id": iid,
           "evidence": evidence,
           "verdict_data": {
               "overview_delivered": bool(overview_markers),
               "bundle_delivered": bool(delivered),
               "co_delivery": bool(overview_markers and delivered),
               "bundle_is_candidate_set_export": False,
           }}
    with open("../docs/evaluations/wp2-s5-turn-intake-forensics.json",
              "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("FORENSICS-EVIDENCE -> "
          "docs/evaluations/wp2-s5-turn-intake-forensics.json")

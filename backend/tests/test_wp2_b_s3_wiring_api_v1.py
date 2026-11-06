"""WP-2 Version B S3 — REAL-API wiring E2E for the per-target delivery
admission (loop + /turn).

Frozen behaviors under test (design Final Rulings 1-4/7, /turn
requirement):
  (i)   VALID binding, loop path: HSG decision + explicit CS-ref +
        persisted session set (source task of THIS investigation) → the
        target delivers ONLY within its own candidate_set boundary
        (artifact content = the seeded candidates), dual verification
        echoes pass, normal completion, no clarify framing.
  (ii)  INVALID target, /turn path: an artifact ask WITHOUT an explicit
        ref over /turn must NOT deliver — the rejected target never
        reaches the artifact tool and the response carries the
        clarification (anti-bypass).
  (iii) VALID binding, /turn path: the same seeded set delivered via
        /turn with the explicit ref — /turn is not a bypass for legal
        deliveries either.

The harness mirrors the accepted S1 injection pattern (TestClient real
HTTP face; class-level ClaudeProvider.generate transport-failure
injection drives the REAL GU heuristic fallback to the
heuristic_single_goal decision; RP adapter patched offline; isolated
store). All evidence is WP-2 Version B implementation evidence for S3;
the full S5 E2E matrices are NOT exercised here.

Feature flags: ENABLE_GOAL_SEMANTIC_CONTRACT pinned explicitly (D4
discipline).
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
SET_REF = "CS-ab12cd34-S9"
ASK_REF = "导出 CS-ab12cd34-S9 的候选交易明细文件"
ASK_PLAIN = "导出这个账户的全部交易记录文件"
# /turn scenarios use the EN deterministic artifact ask: the zh
# records-stream (R2a) ask fails PLAN-TIME contract validation on the
# legacy /turn planning environment (no focused-finding capabilities) —
# a PRE-EXISTING /turn limitation unrelated to admission (recorded, not
# fixed in S3).
ASK_TURN_REF = "export the candidates in CS-ab12cd34-S9 as an artifact"
ASK_TURN_PLAIN = "generate all trade records of this account as an artifact"
INJECTED_GU_ERROR = ("peer closed connection without sending complete "
                     "message body (injected WP2-S3)")


def _seed_candidates():
    def leg(tid, side):
        return {"transaction_id": tid, "account_id": "ACC-A",
                "side": side, "quantity": 1.0, "value": 100.0,
                "symbol": "BTC", "timestamp": "2026-08-19T10:00:00+00:00"}
    return [{"candidate_id": "CATA-001", "rank": 1,
             "account_a": "ACC-A", "account_b": "ACC-B", "symbol": "BTC",
             "supporting": [leg("TX-1", "BUY"), leg("TX-2", "SELL")]}]


class ScriptedNever:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def _build_client(tmp_path, monkeypatch, *, inject_gu=True):
    store = TaskStoreV2(str(tmp_path / "wp2_s3.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
    import tests.test_risk_platform_adapter_v2 as fx

    def _fetch_case(self, uid):
        return (fx.rp_evidence_payload(CASE),
                fx.rp_explanation_payload())

    monkeypatch.setattr(_rcf.RiskPlatformAdapter, "fetch_case", _fetch_case)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    if inject_gu:
        monkeypatch.setattr(
            ClaudeProvider, "generate",
            lambda self, messages, max_tokens=0, temperature=0.1, **kw:
                (_ for _ in ()).throw(LLMError(INJECTED_GU_ERROR)))
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

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


def _seed_set(store, investigation_id, source_task_id):
    doc = store.get_session(investigation_id) or {}
    doc.setdefault("candidate_sets", []).append({
        "set_ref": SET_REF,
        "source_task_id": source_task_id,
        "source_step_id": "S1",
        "created_at": "2026-10-31T00:00:00Z",
        "bounded_data": False,
        "origin": "analysis",
        "candidates": _seed_candidates(),
    })
    store.save_session(investigation_id, doc)


def _wait_terminal(client, tid):
    import time as _time
    deadline = _time.time() + 120
    while _time.time() < deadline:
        detail = client.get(f"/api/v2/tasks/{tid}").json()
        st = (detail.get("task") or {}).get("status")
        if st in ("completed", "failed"):
            return detail
        _time.sleep(0.3)
    return client.get(f"/api/v2/tasks/{tid}").json()


def test_valid_binding_delivers_within_boundary(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    # Turn 1 (intake, injected GU): creates the investigation's first
    # task — the seeded set's provenance anchor.
    r1 = client.post(f"/api/v2/investigations/{iid}/submit",
                     json={"message": "Investigate U00010"},
                     timeout=180).json()
    tid1 = r1.get("task_id") or (r1.get("task") or {}).get("task_id")
    _wait_terminal(client, tid1)
    # Seed the persisted session set owned by THIS investigation/task.
    _seed_set(client._wp2_store, iid, tid1)
    # Turn 2: explicit CS-ref ask under the injected HSG fallback —
    # admission binds the target and the delivery runs gate-free WITHIN
    # the seeded boundary.
    r2 = client.post(f"/api/v2/investigations/{iid}/submit",
                     json={"message": ASK_REF},
                     timeout=180).json()
    tid2 = r2.get("task_id") or (r2.get("task") or {}).get("task_id")
    detail = _wait_terminal(client, tid2)

    doc = client._wp2_store.get_agent_state(tid2)
    assert doc is not None
    dec = (doc.goal_contract_decision or {}).get("outcome")
    assert dec == "heuristic_single_goal"
    assert doc.agent_status == "completed"
    assert doc.artifact_ids, "valid binding must deliver"
    # boundary: the delivered artifact is the seeded set's export
    arts = client.get(f"/api/v2/tasks/{tid2}/artifacts").json()["artifacts"]
    assert arts, "artifact body must be visible for the task"
    content = arts[0]["content"]
    assert f"候选集: {SET_REF}" in content
    assert "TX-1" in content and "TX-2" in content
    assert arts[0]["scope"] == f"candidate_set:{SET_REF}"
    cs = doc and None
    # dual verification echo passed (records digest + provenance)
    obs = doc.observations or []
    verifications = [o["summary"]["candidate_set_export"]
                     for o in obs
                     if isinstance(o.get("summary"), dict)
                     and isinstance(
                         o["summary"].get("candidate_set_export"), dict)]
    assert verifications, "candidate export observation missing"
    v0 = verifications[-1]
    assert v0["verification"]["passed"]
    assert v0["records_digest_verification"]["passed"]
    assert v0["provenance_verification"]["passed"]
    # no clarify framing on a delivered run
    resp = detail.get("task_response_text") or ""
    assert "等待澄清" not in resp and "await clarification" not in resp


def test_turn_without_ref_cannot_bypass(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    # seed a set AND a prior task so ownership could pass if the rules
    # were bypassed — the missing explicit ref must still fail closed.
    r0 = client.post(f"/api/v2/investigations/{iid}/submit",
                     json={"message": "Investigate U00010"},
                     timeout=180).json()
    tid0 = r0.get("task_id") or (r0.get("task") or {}).get("task_id")
    _wait_terminal(client, tid0)
    _seed_set(client._wp2_store, iid, tid0)
    # /turn with the artifact ask but NO explicit CS-ref
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": ASK_TURN_PLAIN},
                     timeout=180).json()
    artifacts = tr.get("artifacts") or []
    assert artifacts == [], "/turn must not deliver an ungated artifact"
    resp = tr.get("response") or ""
    assert "未通过确定性准入" in resp or "failed deterministic" in resp


def test_turn_with_valid_ref_delivers_within_boundary(
        tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    r0 = client.post(f"/api/v2/investigations/{iid}/submit",
                     json={"message": "Investigate U00010"},
                     timeout=180).json()
    tid0 = r0.get("task_id") or (r0.get("task") or {}).get("task_id")
    _wait_terminal(client, tid0)
    _seed_set(client._wp2_store, iid, tid0)
    tr = client.post(f"/api/v2/investigations/{iid}/turn",
                     json={"message": ASK_TURN_REF},
                     timeout=180).json()
    artifacts = tr.get("artifacts") or []
    assert artifacts, "/turn valid binding must deliver within boundary"
    assert artifacts[0]["scope"] == f"candidate_set:{SET_REF}"
    assert "TX-1" in artifacts[0]["content"]
    resp = tr.get("response") or ""
    assert "未通过确定性准入" not in resp

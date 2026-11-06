"""GJ-OPPOSITE-TRADE-RATIO — REAL-API E2E (Golden Journeys 001/002/003).

Real HTTP API face (TestClient -> FastAPI -> InvestigationAPI ->
AgentLoopRunner -> ExecutorV2 -> persistence). The GU LLM is scripted
per scenario (what the live LLM proposes varies); the deterministic
candidate stage, canonicalization, guards, merge, coverage, planning,
execution and composition are the REAL production paths. RP is offline
(adapter patched at its boundary).

- GJ-001 (the incident ask): the metric question must produce ONLY the
  metric-semantics goal — no F5 finding-explanation output, no F5
  section in the served answer.
- GJ-002: an explicit F5 why-ask still produces the finding explanation.
- GJ-003: metric + F5 why in ONE ask keeps BOTH goals, both answered.
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
from app.llm_provider import ClaudeProvider
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

import tests.test_risk_platform_adapter_v2 as fx
from tests.test_al28_semantic_foundation_v1 import seeded_catalog
from tests.test_wp2_b_s5_e2e_v1 import ScriptedNever

CASE = "U00058"
ACCOUNTS = [f"{CASE}L{i:02d}" for i in range(1, 6)]

GU_METRIC_ONLY = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "summarize",
    "ambiguous_goal_types": [],
    "reason": "metric semantics ask",
    "required_outputs": [
        {"aspect": "reverse_trade_metric_semantics"}],
}, ensure_ascii=False)

GU_F5_WHY = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "explain_finding",
    "ambiguous_goal_types": [],
    "reason": "finding why ask",
    "required_outputs": [{
        "claim": "为什么「Linked Account Network」被标记为风险",
        "claim_kind": "explanation",
        "claim_description": (
            "解释发现 F1「Linked Account Network」被标记为风险的依据"),
        "subject_ref": "F1", "scope": "finding_level",
        "evidence_requirements": ["rule_explanation"]}],
}, ensure_ascii=False)

GU_METRIC_AND_F5 = json.dumps({
    "goal_resolution": "COMPOUND", "goal_type": None,
    "ambiguous_goal_types": [],
    "reason": "metric semantics + finding why",
    "required_outputs": [
        {"aspect": "reverse_trade_metric_semantics"},
        {"claim": "为什么「Linked Account Network」被标记为风险",
         "claim_kind": "explanation",
         "claim_description": (
             "解释发现 F1「Linked Account Network」被标记为风险的依据"),
         "subject_ref": "F1", "scope": "finding_level",
         "evidence_requirements": ["rule_explanation"]}],
}, ensure_ascii=False)


def _evidence():
    payload = fx.rp_evidence_payload(CASE)
    payload["feature_evidence"] = dict(
        payload.get("feature_evidence") or {},
        opposite_trade_ratio=0.3429)
    payload["network_evidence"] = {
        "cluster_id": "CLU-U00058", "cluster_name": "cluster_linked_u00058",
        "detection_type": "linked_accounts",
        "member_count": len(ACCOUNTS) + 1, "cluster_risk_score": 70.0,
        "role_in_cluster": "member",
        "related_accounts": [{"user_id": a} for a in ACCOUNTS],
        "related_accounts_count": len(ACCOUNTS), "shared_devices": []}
    return payload


def _network_signals():
    return {"connected_accounts": [{"user_id": a} for a in ACCOUNTS],
            "connected_account_count": len(ACCOUNTS)}


def _explanation_payload():
    return {
        "summary": "Case overview",
        "key_findings": [
            "1. Linked Account Network [1]",
            "2. High Withdrawal Frequency",
        ],
        "recommended_action": "Manual Review",
        "citations": [],
        "explanation_source": "LLM", "llm_error": None,
        "missing_info": [],
    }


def _account_transactions(self, user_id, account_ids=None, **kw):
    ids = account_ids or []
    txns = []
    for i, a in enumerate(ids):
        txns.append({"transaction_id": f"TX-{a}", "account_id": a,
                     "symbol": "BTC", "side": "BUY" if i % 2 else "SELL",
                     "price": 100.0, "quantity": 1.0, "value": 100.0,
                     "timestamp": f"2026-08-19T10:0{i}:00+00:00"})
    return {"case_user_id": user_id, "requested_accounts": ids,
            "transactions": txns, "total_matched": len(txns),
            "returned_count": len(txns), "offset": 0, "limit": 100,
            "truncated": False, "has_more": False, "next_offset": None}


def _build_client(tmp_path, monkeypatch, gu_payload):
    store = TaskStoreV2(str(tmp_path / "gj_ratio.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _adapter = importlib.import_module(
        "app.adapters.risk_platform").RiskPlatformAdapter

    def _fetch_case(self, uid):
        return (_evidence(), _explanation_payload())

    def _fetch_evidence(self, uid, **kw):
        return _evidence()

    def _fetch_network(self, uid, limit=None):
        return _network_signals()

    monkeypatch.setattr(_adapter, "fetch_case", _fetch_case)
    monkeypatch.setattr(_adapter, "fetch_case_evidence", _fetch_evidence)
    monkeypatch.setattr(_adapter, "fetch_network_signals", _fetch_network)
    monkeypatch.setattr(_adapter, "fetch_account_transactions",
                        _account_transactions)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)

    def _gu(self, messages=None, max_tokens=0, temperature=0.1, **kw):
        return gu_payload

    monkeypatch.setattr(ClaudeProvider, "generate", _gu)

    from eval.fixtures import ScriptedLLM, plan_json

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_trade_analysis",
                ["analyze_related_accounts",
                 "analyze_related_account_set_trades"])])),
            proposer=HeuristicDecisionProposer(),
        )

    from app.adapters.risk_platform import normalize_case
    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_trade_analysis",
                ["analyze_related_accounts",
                 "analyze_related_account_set_trades"])])),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: normalize_case(
            case_id=cid, evidence=_evidence(),
            explanation=_explanation_payload())["findings"],
        loop_runner_factory=_factory,
    )
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._wp2_store = store
    return client


def _run(tmp_path, ask, gu_payload, monkeypatch):
    client = _build_client(tmp_path, monkeypatch, gu_payload=gu_payload)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": ask}, timeout=180)
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]
    import time as _time
    deadline = _time.time() + 120
    doc = None
    while _time.time() < deadline:
        doc = client._wp2_store.get_agent_state(tid)
        if doc is not None and doc.agent_status in (
                "completed", "failed", "blocked"):
            break
        _time.sleep(0.2)
    assert doc is not None and doc.agent_status in (
        "completed", "failed", "blocked")
    session = client._wp2_store.get_session(iid) or {}
    answer = (session.get("turn_responses") or {}).get(tid) \
        or doc.response or ""
    return doc, answer


INCIDENT_ASK = ("please explain how to calculate the opposite-trade "
                "ratio of 34.29%. Is it the opposite-trade ratio of "
                "34.29% within the linked accounts?")
F5_ASK = ("Explain why the Linked Account Network finding was flagged "
          "as a risk.")
COMPOUND = ("Explain how opposite-trade ratio is calculated, and also "
            "explain why the Linked Account Network is risky.")


class TestGJ001MetricOnly:
    def test_no_f5_goal_no_f5_section(self, tmp_path, monkeypatch):
        doc, answer = _run(tmp_path, INCIDENT_ASK, GU_METRIC_ONLY, monkeypatch)
        # the contract: ONLY the metric goal — no F5 finding-explanation
        claims = [(o.claim, o.satisfaction.value)
                  for o in doc.goal_contract.outputs]
        assert len(claims) == 1, claims
        assert "Linked Account Network" not in claims[0][0]
        assert claims[0][1] == "satisfied"
        # the answer: metric semantics present, F5 grounds absent
        assert "34.29%" in answer or "34,29" in answer
        assert "min(" in answer or "较少一侧" in answer
        assert "不是跨账户" in answer or "NOT a cross-account" in answer
        assert "被标记为风险" not in answer
        assert "Graph detection" not in answer \
            and "图关系检测" not in answer


class TestGJ002F5Why:
    def test_f5_why_still_answered(self, tmp_path, monkeypatch):
        doc, answer = _run(tmp_path, F5_ASK, GU_F5_WHY, monkeypatch)
        f5 = [o for o in doc.goal_contract.outputs
              if "Linked Account Network" in o.claim]
        assert f5, [o.claim for o in doc.goal_contract.outputs]
        assert f5[0].satisfaction.value == "satisfied"
        assert "Linked Account Network" in answer


class TestGJ003Compound:
    def test_both_goals_answered(self, tmp_path, monkeypatch):
        doc, answer = _run(tmp_path, COMPOUND, GU_METRIC_AND_F5, monkeypatch)
        kinds = [(o.claim, o.satisfaction.value)
                 for o in doc.goal_contract.outputs]
        metric = [c for c, s in kinds
                  if "反向交易率" in c or "opposite" in c.lower()]
        f5 = [c for c, s in kinds if "Linked Account Network" in c]
        assert metric and f5, kinds
        assert "反向交易率" in answer or "opposite" in answer.lower()
        assert "Linked Account Network" in answer

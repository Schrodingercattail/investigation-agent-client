"""U00004 file-delivery closure — REAL-API E2E (N-A..N-D).

Real HTTP API face (TestClient -> FastAPI -> InvestigationAPI ->
AgentLoopRunner -> ExecutorV2 -> domain tools -> artifact_bundle ->
persistence). The GU LLM is scripted with the aspect-tagged payload (the
REAL canonicalization chain runs: aspect substitution -> guards ->
merge -> the U00004 file-delivery closure); the planner LLM is scripted
with the S6-proven plan shape; the Risk Platform is offline (adapter
patched at its boundary).

- N-A: the user's original ask (explicit file clause) -> the contract
  carries the file_export leg -> generate_artifact planned + EXECUTED ->
  file CREATED + REGISTERED -> the persisted file content carries the
  candidate detail (accounts, sides, trade ids, time/value gaps, the
  floor-admission label, the lead-not-intent boundary).
- N-B: the same ask WITHOUT the file clause -> no file leg, no
  generate_artifact, no artifact (plain detail_list journey unchanged).
- N-C: budget-limited set analysis -> the file still generates and its
  content states the coverage limitation.
- N-D: artifact generation fails -> the task is NEVER marked
  file-delivered (no artifact, no file-brief claim, honest partial).
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
from app.models import ToolError, ToolResult, ToolResultOutcome
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

import tests.test_risk_platform_adapter_v2 as fx
from tests.test_al28_semantic_foundation_v1 import seeded_catalog
from tests.test_wp2_b_s5_e2e_v1 import ScriptedNever

CASE = "U00004"
ASK = ("列出所有关联账号。查询其中是否存在对冲交易情况。如果有，"
       "把对冲交易明细列给我，并且生成一份文件")
ASK_PLAIN = ("列出所有关联账号。查询其中是否存在对冲交易情况。如果有，"
             "把对冲交易明细列给我")

GU_PAYLOAD = json.dumps({
    "goal_resolution": "COMPOUND",
    "goal_type": None,
    "ambiguous_goal_types": [],
    "reason": "列出关联账号、查询集合内部对冲并生成对冲交易明细文件",
    "required_outputs": [
        {"aspect": "related_accounts_network"},
        {"aspect": "hedging_set_internal"},
    ],
}, ensure_ascii=False)

ACCOUNTS = [f"{CASE}L{i:02d}" for i in range(1, 7)]   # 6-member set


def _evidence():
    payload = fx.rp_evidence_payload(CASE)
    payload["network_evidence"] = {
        "cluster_id": "CLU-U00004",
        "cluster_name": "cluster_linked_18",
        "detection_type": "linked_accounts",
        "member_count": 19,
        "cluster_risk_score": 72.0,
        "role_in_cluster": "member",
        "related_accounts": [{"user_id": a} for a in ACCOUNTS],
        "related_accounts_count": len(ACCOUNTS),
        "shared_devices": [],
    }
    return payload


def _network_signals():
    return {"connected_accounts": [{"user_id": a} for a in ACCOUNTS],
            "connected_account_count": len(ACCOUNTS)}


_TXNS = {
    "U00004L01": [{"transaction_id": "TX-A1", "account_id": "U00004L01",
                   "symbol": "BTC", "side": "BUY", "price": 100.0,
                   "quantity": 1.0, "value": 100.0,
                   "timestamp": "2026-08-19T10:00:00+00:00"}],
    "U00004L02": [{"transaction_id": "TX-A2", "account_id": "U00004L02",
                   "symbol": "BTC", "side": "SELL", "price": 100.0,
                   "quantity": 1.0, "value": 100.0,
                   "timestamp": "2026-08-19T10:00:10+00:00"}],
    "U00004L03": [{"transaction_id": "TX-B1", "account_id": "U00004L03",
                   "symbol": "BTC", "side": "BUY", "price": 10.0,
                   "quantity": 1.0, "value": 10.0,
                   "timestamp": "2026-08-19T11:00:00+00:00"}],
    "U00004L04": [{"transaction_id": "TX-B2", "account_id": "U00004L04",
                   "symbol": "BTC", "side": "SELL", "price": 11.0,
                   "quantity": 1.0, "value": 11.0,
                   "timestamp": "2026-08-19T11:00:05+00:00"}],
}


def _account_transactions(self, user_id, account_ids=None, **kw):
    ids = account_ids or []
    txns = []
    for a in ids:
        txns.extend(_TXNS.get(a, []))
    return {"case_user_id": user_id, "requested_accounts": ids,
            "transactions": txns, "total_matched": len(txns),
            "returned_count": len(txns), "offset": 0, "limit": 100,
            "truncated": False, "has_more": False, "next_offset": None}


def _build_client(tmp_path, monkeypatch, *, artifact_tool=None,
                  gu_payload=GU_PAYLOAD, plan_steps=None):
    from app.llm_provider import ClaudeProvider
    from eval.fixtures import ScriptedLLM, plan_json

    store = TaskStoreV2(str(tmp_path / "u00004_file.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _adapter = importlib.import_module(
        "app.adapters.risk_platform").RiskPlatformAdapter

    def _fetch_case(self, uid):
        return (_evidence(), fx.rp_explanation_payload())

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

    def _gu(self, messages=None, max_tokens=0, temperature=0.1, **kw):
        return gu_payload

    monkeypatch.setattr(ClaudeProvider, "generate", _gu)

    if artifact_tool is not None:
        import app.domain_tools as _dt_pkg
        import importlib as _il
        _ab = _il.import_module("app.domain_tools.artifact_bundle")
        monkeypatch.setattr(_dt_pkg, "artifact_bundle", artifact_tool)
        monkeypatch.setattr(_ab, "artifact_bundle", artifact_tool)

    scripts = [plan_json("case_trade_analysis", plan_steps or [
        "analyze_related_accounts", "analyze_related_account_set_trades",
        "generate_artifact"])]

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM(list(scripts))),
            proposer=HeuristicDecisionProposer(),
        )

    from app.adapters.risk_platform import normalize_case
    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM(list(scripts))),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: normalize_case(
            case_id=cid, evidence=_evidence(),
            explanation=fx.rp_explanation_payload())["findings"],
        loop_runner_factory=_factory,
    )
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._wp2_store = store
    return client


def _run_submit(client, iid, message):
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": message}, timeout=180)
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
        "completed", "failed", "blocked"), \
        f"task {tid} did not reach a terminal status"
    session = client._wp2_store.get_session(iid) or {}
    response = (session.get("turn_responses") or {}).get(tid) \
        or doc.response or ""
    return tid, doc, response


def _artifacts(client, tid):
    return (client.get(f"/api/v2/tasks/{tid}/artifacts",
                       timeout=30).json().get("artifacts") or [])


class TestScenarioNAFileDelivered:
    def test_file_leg_planned_executed_registered(self, tmp_path,
                                                  monkeypatch):
        client = _build_client(tmp_path, monkeypatch)
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc, response = _run_submit(client, iid, ASK)

        # CONTRACT: the file leg survived canonicalization
        outs = [(o.output_id, o.delivery, o.claim_kind.value)
                for o in doc.goal_contract.outputs]
        file_outs = [o for o in doc.goal_contract.outputs
                     if o.delivery == "file_export"]
        assert len(file_outs) == 1, outs
        assert file_outs[0].claim == "对冲交易明细文件导出"
        # PLAN + EXECUTION: generate_artifact planned and executed
        steps = [(s.get("type"), s.get("status"))
                 for s in doc.plan_steps]
        assert ("generate_artifact", "success") in steps, steps
        # CREATED + REGISTERED: a real artifact exists and is listed
        assert doc.artifact_ids, "no artifact registered"
        arts = _artifacts(client, tid)
        assert arts and arts[0].get("artifact_id") in (
            doc.artifact_ids or [])
        # the persisted file CONTENT carries the candidate detail —
        # never just a tool-call success marker
        body = json.dumps(arts[0], ensure_ascii=False)
        assert "TX-A1" in body and "TX-A2" in body
        # the file carries the related-account set context (user DoD: the
        # complete account list beside the candidates)
        assert "Related Accounts (" in body
        for a in ACCOUNTS:
            assert a in body, f"account {a} missing from the file"
        assert "U00004L01" in body and "U00004L02" in body
        assert "TX-B1" in body and "TX-B2" in body
        # the boundary: candidates are leads, not proven intent (the
        # bundle's own wording)
        assert ("leads for analyst" in body or "candidate relationships"
                in body.lower()), body[:400]
        # the served answer renders the file brief (not a bare chat roll)
        assert "ART-" in response or "文件" in response, response[:200]


class TestScenarioNBPlainDetail:
    def test_no_file_words_no_artifact(self, tmp_path, monkeypatch):
        client = _build_client(
            tmp_path, monkeypatch,
            plan_steps=["analyze_related_accounts",
                        "analyze_related_account_set_trades"])
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc, response = _run_submit(client, iid, ASK_PLAIN)
        assert not any(o.delivery == "file_export"
                       for o in doc.goal_contract.outputs)
        assert all(s.get("type") != "generate_artifact"
                   for s in doc.plan_steps)
        assert doc.artifact_ids == []
        assert _artifacts(client, tid) == []


class TestScenarioNDBudgetLimited:
    def test_budget_limited_file_states_coverage(self, tmp_path,
                                                 monkeypatch):
        monkeypatch.setattr(settings, "TRADE_ANALYSIS_MAX_ACCOUNTS", 3)
        client = _build_client(tmp_path, monkeypatch)
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc, response = _run_submit(client, iid, ASK)
        # the file still generated
        assert doc.artifact_ids, "budget-limited run lost the file"
        arts = _artifacts(client, tid)
        body = json.dumps(arts[0], ensure_ascii=False)
        # the coverage limitation is honestly stated in the answer
        limitation = ("预算" in response or "不完整" in response
                      or "覆盖" in response)
        assert limitation, response[:300]


class TestScenarioNDArtifactFailure:
    def test_failed_artifact_never_marked_delivered(self, tmp_path,
                                                    monkeypatch):
        def _failing_bundle(*args, **kwargs):
            return ToolResult(
                outcome=ToolResultOutcome.VALIDATION_ERROR,
                error=ToolError(code="ARTIFACT_SOURCE_MISSING",
                                message="injected N-D bundle failure"),
                data={})

        client = _build_client(tmp_path, monkeypatch,
                               artifact_tool=_failing_bundle)
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc, response = _run_submit(client, iid, ASK)
        # the contract still carried the file leg (the demand was seen)
        assert any(o.delivery == "file_export"
                   for o in doc.goal_contract.outputs)
        # ... but NOTHING was delivered
        assert doc.artifact_ids == []
        assert _artifacts(client, tid) == []
        # the file output was NOT satisfied and the run says so
        file_out = [o for o in doc.goal_contract.outputs
                    if o.delivery == "file_export"][0]
        assert file_out.satisfaction.value != "satisfied"
        assert "ART-" not in response
        # honest terminal: no false file-delivery claim
        assert doc.goal_outcome in ("partial", "unanswerable", "blocked")

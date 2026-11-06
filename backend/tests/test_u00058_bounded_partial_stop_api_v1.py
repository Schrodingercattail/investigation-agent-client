"""U00004 bounded partial STOP — REAL-API E2E (the TASK-14dcda628770
incident shape, case U00058).

Real HTTP API face; the GU LLM is scripted with the aspect-tagged
payload (the REAL canonicalization chain runs; the user's explicit file
clause adds the file_export leg through the U00004 closure); the planner
is scripted with the audited first-plan shape
[analyze_related_account_set_trades, generate_artifact]; the proposer is
scripted to propose STOP after the first round (what the live LLM did
at it=2).

- flag ON:  the STOP is ACCEPTED at the decision — the run ends
  completed / BOUNDED_DATA / goal_outcome=partial after ONE round, with
  NO repeated trade analysis and NO repeated artifact generation; O1
  stays partially_supported (budget-truncated), O2 SATISFIED.
- flag OFF: the legacy behavior — the STOP is coerced to RE_PLAN (the
  incident's coercion), proving the branch is the only difference.
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
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

import tests.test_risk_platform_adapter_v2 as fx
from tests.test_al28_semantic_foundation_v1 import seeded_catalog
from tests.test_wp2_b_s5_e2e_v1 import ScriptedNever

CASE = "U00058"
ASK = ("查询以上关联账号之间是否存在对冲交易，如果有，列出对冲交易"
       "明细，并生成一份文件")

GU_PAYLOAD = json.dumps({
    "goal_resolution": "COMPOUND",
    "goal_type": None,
    "ambiguous_goal_types": [],
    "reason": "查询关联账号集合内部对冲并生成对冲交易明细文件",
    "required_outputs": [{"aspect": "hedging_set_internal"}],
}, ensure_ascii=False)

ACCOUNTS = [f"{CASE}L{i:02d}" for i in range(1, 7)]

_TXNS = {
    "U00058L01": [{"transaction_id": f"TX-{CASE}-A1",
                   "account_id": "U00058L01", "symbol": "BTC",
                   "side": "BUY", "price": 100.0, "quantity": 1.0,
                   "value": 100.0,
                   "timestamp": "2026-08-19T10:00:00+00:00"}],
    "U00058L02": [{"transaction_id": f"TX-{CASE}-A2",
                   "account_id": "U00058L02", "symbol": "BTC",
                   "side": "SELL", "price": 100.0, "quantity": 1.0,
                   "value": 100.0,
                   "timestamp": "2026-08-19T10:00:10+00:00"}],
}


def _evidence():
    payload = fx.rp_evidence_payload(CASE)
    payload["network_evidence"] = {
        "cluster_id": "CLU-U00058",
        "cluster_name": "cluster_linked_u00058",
        "detection_type": "linked_accounts",
        "member_count": len(ACCOUNTS) + 1,
        "cluster_risk_score": 70.0,
        "role_in_cluster": "member",
        "related_accounts": [{"user_id": a} for a in ACCOUNTS],
        "related_accounts_count": len(ACCOUNTS),
        "shared_devices": [],
    }
    return payload


def _network_signals():
    return {"connected_accounts": [{"user_id": a} for a in ACCOUNTS],
            "connected_account_count": len(ACCOUNTS)}


def _account_transactions(self, user_id, account_ids=None, **kw):
    ids = account_ids or []
    txns = []
    for a in ids:
        txns.extend(_TXNS.get(a, []))
    # budget-bounded retrieval: the authoritative window declares more
    # matched transactions than were returned (the incident's O1 shape)
    return {"case_user_id": user_id, "requested_accounts": ids,
            "transactions": txns, "total_matched": 1173,
            "returned_count": len(txns), "offset": 0, "limit": 100,
            "truncated": True, "has_more": True, "next_offset": len(txns)}


def _build_client(tmp_path, monkeypatch, *, flag_on=True, scripts=None):
    from app.llm_provider import ClaudeProvider
    from eval.fixtures import ScriptedLLM, plan_json

    store = TaskStoreV2(str(tmp_path / "u00058_bp.db"))
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
    monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", flag_on)

    def _gu(self, messages=None, max_tokens=0, temperature=0.1, **kw):
        return GU_PAYLOAD

    monkeypatch.setattr(ClaudeProvider, "generate", _gu)

    class ScriptedProposer:
        def __init__(self, decisions):
            self.decisions = list(decisions)

        def propose(self, state):
            from app.agent_loop import DecisionProposal
            d = self.decisions.pop(0) if self.decisions else "CONTINUE"
            return DecisionProposal(decision=d, reason="scripted STOP")

    # mirror the incident's decision sequence: CONTINUE past the
    # analysis, then STOP right after the file generation (the live
    # it=1/it=2 decisions of TASK-14dcda628770)
    proposer = ScriptedProposer(["CONTINUE", "STOP"])

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_trade_analysis", scripts or [
                    "analyze_related_account_set_trades",
                    "generate_artifact"])])),
            proposer=proposer,
        )

    from app.adapters.risk_platform import normalize_case
    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_trade_analysis", scripts or [
                    "analyze_related_account_set_trades",
                    "generate_artifact"])])),
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
    return tid, doc


class TestBoundedPartialStopAccepted:
    def test_one_round_completed_partial_bounded(self, tmp_path,
                                                 monkeypatch):
        client = _build_client(tmp_path, monkeypatch, flag_on=True)
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc = _run_submit(client, iid, ASK)

        # the contract: O1 honestly partial, O2 (file) satisfied
        sats = {o.output_id: (o.delivery, o.satisfaction.value)
                for o in doc.goal_contract.outputs}
        file_outs = [k for k, (d, s) in sats.items()
                     if d == "file_export"]
        assert file_outs, sats
        assert sats[file_outs[0]][1] == "satisfied"
        assert sats["O1"][1] == "partially_supported", sats

        # ONE round: no repeated trade analysis, no repeated artifact
        obs_types = [o.get("step_type") for o in doc.observations]
        assert obs_types.count("analyze_related_account_set_trades") == 1, \
            obs_types
        assert obs_types.count("generate_artifact") == 1, obs_types

        # the file is real and registered
        assert doc.artifact_ids, "no artifact registered"
        arts = (client.get(f"/api/v2/tasks/{tid}/artifacts",
                           timeout=30).json().get("artifacts") or [])
        assert arts and arts[0].get("artifact_id") == doc.artifact_ids[0]
        assert "TX-" in (arts[0].get("content") or "")

        # the terminal: completed / BOUNDED_DATA / partial, and the
        # accepted STOP decision (never a forced RE_PLAN)
        assert doc.agent_status == "completed"
        assert doc.stop_reason == "bounded_data", doc.stop_reason
        assert doc.goal_outcome == "partial"
        decisions = [d.get("decision") for d in doc.decisions]
        assert "RE_PLAN" not in decisions, decisions


class TestBoundedPartialStopFlagOff:
    def test_flag_off_keeps_legacy_coercion(self, tmp_path, monkeypatch):
        client = _build_client(tmp_path, monkeypatch, flag_on=False,
                               scripts=[
                                   "analyze_related_account_set_trades",
                                   "generate_artifact"])
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc = _run_submit(client, iid, ASK)
        # the ESSENCE of flag-off compatibility: the STOP proposal over
        # the bounded-partial state hit the LEGACY coercion (the
        # incident's own wording), and the bounded-partial acceptance
        # branch never fired — no decision carries its reason
        reasons = [str(d.get("reason") or "") for d in doc.decisions]
        assert any("stop proposed before" in r for r in reasons), reasons
        assert not any("bounded partial completion" in r
                       for r in reasons), reasons
        # and the legacy run's shape differs from the flag-ON one-round
        # acceptance (the coercion forced at least one further decision)
        assert len(doc.decisions) > 1

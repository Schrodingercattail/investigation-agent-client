"""WP-2 U00033 Fix-5 — REAL-API E2E Scenarios N1/N2.

Real HTTP API face (TestClient -> FastAPI -> InvestigationAPI ->
InvestigationService / AgentLoopRunner -> ExecutorV2 -> domain tools ->
persistence). The model boundary is injected (deterministic-API
infrastructure, the established S5 harness); the Risk Platform is
OFFLINE — the adapter is patched at its boundary with the U00033
incident-shaped payloads (Linked Account Network / Shared Device
findings, the §5.1 citation, the 12-account shared-device cluster).

- N1 (the real compound ask): 「关联账号为什么是风险项？相关政策原文发我」
  → the explain step enters the Executor WITH its Finding binding
  (Fix-1 anchoring at adoption — never the incident's INVALID_ARGUMENT
  blind failure) → explain EXECUTES → the §5.1 policy citation is
  served; the explain and policy steps carry the SAME resolved Finding
  identity (DoD 2/12); terminal = goal_satisfied family with the
  heuristic-cap PARTIAL outcome (Fix-4 rows 1).
- N2 (the incident's degraded shape): the explain tool FAILS at the RP
  boundary (injected) while the policy retrieval SUCCEEDS → row 2 of
  the calibrated P1 table: the terminal stays BLOCK-family, the served
  answer keeps the delivered citation and explicitly names the
  ungenerated core (Fix-3 demotion + Fix-4 NOT-PARTIAL) — never the
  incident's false "nothing was gathered" completion of failure.

R1/R2 (resume journeys) re-run green in this round's regression set:
tests/test_wp2_b_s5_e2e_v1.py.
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
from app.llm_provider import LLMError
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from app.models import ToolError, ToolResult, ToolResultOutcome

import tests.test_risk_platform_adapter_v2 as fx
from tests.test_al28_semantic_foundation_v1 import seeded_catalog
from tests.test_wp2_b_s5_e2e_v1 import (
    INJECTED_GU_ERROR,
    ScriptedNever,
)

CASE = "U00033"
ASK = "关联账号为什么是风险项？相关政策原文发我"

GUARD_SESSION_ATTR = "_wp2_u00033_store"


def _u00033_explanation():
    return {
        "summary": "Linked account network and shared device usage detected",
        "key_findings": [
            "1. Linked Account Network [1]",
            "2. Shared Device Relationships",
        ],
        "recommended_action": "Manual Review",
        "citations": [
            {
                "id": 1,
                "doc": "AML_Suspicious_Indicators.md",
                "section": "5.1 Linked-Account Monitoring",
                "quote": "通过关联账户进行交易的用户应当作为一个风险单元"
                         "进行监测。",
                "chunk_id": "AML#5.1#001",
            },
        ],
        "explanation_source": "LLM",
        "llm_error": None,
        "missing_info": [],
    }


def _u00033_evidence():
    payload = fx.rp_evidence_payload(CASE)
    payload["network_evidence"] = {
        "cluster_id": "CLU-00033-1",
        "cluster_name": "cluster_device_sharing_4",
        "detection_type": "device_sharing",
        "member_count": 4,
        "cluster_risk_score": 80.0,
        "role_in_cluster": "spoke",
        "related_accounts": [
            {"user_id": f"U00033L{i:02d}"} for i in range(12)
        ],
        "related_accounts_count": 12,
        "shared_devices": ["DEV1L71883DLR"],
    }
    return payload


def _u00033_network_signals():
    return {
        "connected_accounts": [
            {"user_id": f"U00033L{i:02d}"} for i in range(12)
        ],
        "connected_account_count": 12,
    }


def _u00033_findings():
    from app.adapters.risk_platform import normalize_case
    result = normalize_case(
        case_id=CASE,
        evidence=_u00033_evidence(),
        explanation=_u00033_explanation(),
    )
    return result["findings"]


def _build_client(tmp_path, monkeypatch, *, explain_tool=None):
    """The S5 harness shape, bound to the U00033 case payloads. The
    planner LLM is scripted to the case_intake skill (the eligible
    case-level skill for the no-focus incident shape); the coverage
    supplement adds the explanation step for O1, and Fix-1's loop-top
    anchoring resolves every finding-level binding at adoption."""
    store = TaskStoreV2(str(tmp_path / "wp2_u00033.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _adapter = importlib.import_module(
        "app.adapters.risk_platform").RiskPlatformAdapter

    def _fetch_case(self, uid):
        return (_u00033_evidence(), _u00033_explanation())

    def _fetch_evidence(self, uid):
        return _u00033_evidence()

    def _fetch_network(self, uid, limit=None):
        return _u00033_network_signals()

    monkeypatch.setattr(_adapter, "fetch_case", _fetch_case)
    monkeypatch.setattr(_adapter, "fetch_case_evidence", _fetch_evidence)
    monkeypatch.setattr(_adapter, "fetch_network_signals", _fetch_network)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)

    from app.llm_provider import ClaudeProvider
    monkeypatch.setattr(
        ClaudeProvider, "generate",
        lambda self, messages, max_tokens=0, temperature=0.1, **kw:
            (_ for _ in ()).throw(LLMError(INJECTED_GU_ERROR)))

    if explain_tool is not None:
        # the provider's `from app.domain_tools import signal_explain`
        # reads the PACKAGE attribute (the __init__ re-export) — patch
        # both faces so the provider closure picks up the injected tool
        import app.domain_tools as _dt_pkg
        import importlib as _il
        _se_mod = _il.import_module("app.domain_tools.signal_explain")
        monkeypatch.setattr(_dt_pkg, "signal_explain", explain_tool)
        monkeypatch.setattr(_se_mod, "signal_explain", explain_tool)

    from eval.fixtures import ScriptedLLM, plan_json
    planner_scripts = [plan_json(
        "case_intake", ["fetch_case", "retrieve_policy"])]

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM(list(planner_scripts))),
            proposer=HeuristicDecisionProposer(),
        )

    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM(list(planner_scripts))),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: _u00033_findings(),
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
    detail = client.get(f"/api/v2/tasks/{tid}").json()
    # the SERVED answer is the composed per-output text persisted under
    # the session's turn_responses (what the conversation renders)
    session = client._wp2_store.get_session(iid) or {}
    response = (session.get("turn_responses") or {}).get(tid) \
        or doc.response or ""
    return tid, doc, detail, response


# --- Scenario N1 ---------------------------------------------------------------

class TestScenarioN1RealAsk:
    def test_explain_anchors_executes_and_answers(self, tmp_path,
                                                  monkeypatch):
        client = _build_client(tmp_path, monkeypatch)
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc, detail, response = _run_submit(client, iid, ASK)

        # --- persisted plan: the explain step carries its binding (DoD 1)
        steps = doc.plan_steps or []
        explain = [s for s in steps if s.get("type") == "explain_signal"]
        assert explain, f"no explain_signal step in plan: {steps}"
        assert explain[0]["arguments"].get("finding_id"), \
            f"explain_signal left unanchored: {explain[0]}"
        # --- identity consistency (DoD 2/12): the policy step resolves
        # the SAME finding through the SAME deterministic resolver
        policy = [s for s in steps if s.get("type") == "retrieve_policy"]
        assert policy, f"no retrieve_policy step in plan: {steps}"
        assert policy[0]["arguments"].get("finding_id") == \
            explain[0]["arguments"]["finding_id"], (
                f"policy {policy[0]['arguments']} and explain "
                f"{explain[0]['arguments']} resolved DIFFERENT findings")
        # --- the explain step EXECUTED (never the incident's
        # INVALID_ARGUMENT blind failure)
        observations = doc.observations or []
        explain_obs = [o for o in observations
                       if o.get("step_type") == "explain_signal"]
        assert explain_obs, "explain_signal never executed"
        assert explain_obs[0].get("outcome") == "success", \
            f"explain_signal outcome: {explain_obs[0].get('outcome')}"
        # --- the served answer carries the §5.1 policy citation (O2)
        assert "5.1" in response, f"policy citation missing: {response!r}"
        assert "关联账户" in response      # the §5.1 quote content
        # never the incident's false claim
        assert "no evidence or policy text was gathered" not in response
        # --- terminal semantics (DoD 7): the core ask was answered →
        # goal_satisfied family; heuristic-cap PARTIAL outcome; the task
        # is NOT failed-by-explanation-tool
        assert doc.agent_status == "completed", doc.agent_status
        # row 1's family: the normal goal-satisfied stop (both halves
        # delivered) — or the bounded BLOCK-branch delivery
        assert doc.stop_reason in ("goal_satisfied", "bounded_data"), \
            doc.stop_reason
        assert doc.goal_outcome in ("partial", "complete"), \
            doc.goal_outcome
        assert (detail.get("task") or {}).get("status") == "completed"
        # the contract's per-output truth: BOTH halves delivered
        gc = doc.goal_contract
        sats = {o.output_id: getattr(o.satisfaction, "value", o.satisfaction)
                for o in gc.outputs} if gc else {}
        assert sats.get("O1") == "satisfied", sats
        assert sats.get("O2") == "satisfied", sats


# --- Scenario N2 ---------------------------------------------------------------

def _failing_explain(*args, **kwargs):
    return ToolResult(
        outcome=ToolResultOutcome.INTEGRATION_ERROR,
        error=ToolError(code="RISK_PLATFORM_UNAVAILABLE",
                        message="injected U00033 N2 explain failure"),
        data={})


class TestScenarioN2IncidentDegradedShape:
    def test_core_missing_policy_delivered_stays_block_family(
            self, tmp_path, monkeypatch):
        client = _build_client(tmp_path, monkeypatch,
                               explain_tool=_failing_explain)
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        tid, doc, detail, response = _run_submit(client, iid, ASK)

        # the explain genuinely ran and failed (bounded) — the tool path
        # was reached WITH the anchor, unlike the incident's blind calls
        observations = doc.observations or []
        explain_obs = [o for o in observations
                       if o.get("step_type") == "explain_signal"]
        assert explain_obs, "explain_signal never executed"
        assert all(o.get("outcome") != "success"
                   for o in explain_obs)
        # the policy retrieval SUCCEEDED (the delivered half)
        policy_obs = [o for o in observations
                      if o.get("step_type") == "retrieve_policy"]
        assert policy_obs and policy_obs[0].get("outcome") == "success"
        # --- Fix-4 row 2: NOT PARTIAL — the terminal stays BLOCK-family
        assert doc.agent_status == "blocked", doc.agent_status
        # --- the served answer (Fix-3 demotion + Fix-4): the delivered
        # §5.1 citation stands AND the ungenerated core is named
        assert "5.1" in response, f"citation missing: {response!r}"
        assert "核心解释" in response and "未能生成" in response, \
            f"core-gap statement missing: {response!r}"
        assert "未被回答" in response
        # the incident's factual error never serves again
        assert "no evidence or policy text was gathered" not in response
        # the contract's per-output truth — judged by CLASS (never output
        # ids: the heuristic GU may order policy before explanation): the
        # core-explanation output is pending, the policy-support output
        # is satisfied
        gc = doc.goal_contract
        core_pending = policy_delivered = False
        for o in (gc.outputs if gc else []):
            sat = getattr(o.satisfaction, "value", o.satisfaction)
            ev = {getattr(t, "value", str(t))
                  for t in (o.evidence_requirements or [])}
            if "policy_references" in ev:
                policy_delivered = sat == "satisfied"
            elif str(getattr(o.claim_kind, "value", o.claim_kind)) \
                    in ("explanation", "ClaimKind.EXPLANATION"):
                core_pending = sat == "pending"
        assert core_pending, [(o.output_id, str(o.satisfaction))
                              for o in (gc.outputs if gc else [])]
        assert policy_delivered

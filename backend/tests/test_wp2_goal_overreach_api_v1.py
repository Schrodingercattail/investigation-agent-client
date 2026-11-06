"""WP-2 goal-overreach fix — REAL-API E2E: the over-claim
claim_description ("Export ... as a downloadable artifact") flows
through the REAL HTTP API (POST /submit → GU (injected model boundary,
deterministic fallback wording carries the violation) → contract
acceptance → sanitizer → persisted goal contract), and the persisted
contract + served answer must be free of the over-reach semantics.

This is REAL-API evidence for the SANITIZER path (the deterministic
fix). The live-LLM wording variance is deliberately bypassed via the
injection so the assertion is deterministic; the prompt hardening is
verified separately by the sanitizer's determinism over any LLM output.
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
ASK = "generate all trade records of this account as an artifact"
INJECTED_GU_ERROR = ("peer closed connection without sending complete "
                     "message body (injected WP2-OVERREACH)")
VIOLATING_CLAIM_DESC = ("Export all trade records of the investigated "
                        "account under case U00010 as a downloadable "
                        "artifact.")


class ScriptedNever:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def _build_client(tmp_path, monkeypatch):
    store = TaskStoreV2(str(tmp_path / "wp2_overreach.db"))
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


def test_overreach_claim_sanitized_through_real_api(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": ASK}, timeout=180).json()
    tid = r.get("task_id") or (r.get("task") or {}).get("task_id")
    import time as _time
    deadline = _time.time() + 600
    status = None
    while _time.time() < deadline:
        d = client.get(f"/api/v2/tasks/{tid}", timeout=30).json()
        status = (d.get("task") or {}).get("status")
        if status in ("completed", "failed"):
            break
        _time.sleep(0.3)
    assert status == "completed", status

    doc = client._wp2_store.get_agent_state(tid)
    assert doc is not None
    contract = doc.goal_contract
    assert contract is not None
    for out in contract.outputs:
        blob = ((out.claim or "") + " " + (out.claim_description or "")
                ).lower()
        assert "downloadable" not in blob, blob
        assert "export" not in blob, blob
    # the served answer carries the section header derived from the
    # sanitized description — no over-reach wording reaches the user
    served = doc.response or ""
    assert "downloadable" not in served.lower()
    assert "export all trade records" not in served.lower()



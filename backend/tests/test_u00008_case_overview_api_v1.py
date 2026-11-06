"""U00008 case-overview composer fix — REAL-API E2E (the incident shape).

Real HTTP API face (TestClient); the GU LLM is scripted with the
overview output (the REAL canonicalization/acceptance chain runs); the
planner is scripted with the audited single-step plan [fetch_case]; the
Risk Platform is offline (adapter patched at its boundary with the
7-finding U00008 payload).

The served answer must carry the unresolved declaration AND the fetched
case facts (finding ids/titles/summaries, overall risk), with the
contract/goal_outcome semantics untouched (O1 unresolved, outcome
unanswerable).
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

CASE = "U00008"
ASK = ("An overview of case U00008, including its findings and current "
       "investigation status.")

GU_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED",
    "goal_type": "summarize",
    "ambiguous_goal_types": [],
    "reason": "case overview ask",
    "required_outputs": [{
        "claim": "U00008 case overview",
        "claim_kind": "retrieval",
        "subject_ref": "U00008",
        "scope": "case_level",
        "evidence_requirements": ["case_summary", "timeline_events",
                                  "graph_cluster_facts"],
    }],
}, ensure_ascii=False)


def _evidence():
    payload = fx.rp_evidence_payload(CASE)
    return payload


def _explanation():
    return {
        "summary": "Case overview",
        "key_findings": [
            f"{i}. Finding {i} — RP echo {i}"
            for i in range(1, 8)
        ],
        "recommended_action": "Manual Review",
        "citations": [],
        "explanation_source": "LLM",
        "llm_error": None,
        "missing_info": [],
    }


def _build_client(tmp_path, monkeypatch):
    store = TaskStoreV2(str(tmp_path / "u00008_overview.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _adapter = importlib.import_module(
        "app.adapters.risk_platform").RiskPlatformAdapter

    def _fetch_case(self, uid):
        return (_evidence(), _explanation())

    monkeypatch.setattr(_adapter, "fetch_case", _fetch_case)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)

    def _gu(self, messages=None, max_tokens=0, temperature=0.1, **kw):
        return GU_PAYLOAD

    monkeypatch.setattr(ClaudeProvider, "generate", _gu)

    from eval.fixtures import ScriptedLLM, plan_json

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_intake", ["fetch_case"])])),
            proposer=HeuristicDecisionProposer(),
        )

    from app.adapters.risk_platform import normalize_case
    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_intake", ["fetch_case"])])),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: normalize_case(
            case_id=cid, evidence=_evidence(),
            explanation=_explanation())["findings"],
        loop_runner_factory=_factory,
    )
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._wp2_store = store
    return client


class TestU00008CaseOverviewAnswer:
    def test_unresolved_declaration_with_case_facts(self, tmp_path,
                                                    monkeypatch):
        client = _build_client(tmp_path, monkeypatch)
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE}).json()
        iid = inv["investigation"]["investigation_id"]
        r = client.post(f"/api/v2/investigations/{iid}/submit",
                        json={"message": ASK}, timeout=180)
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

        # the unresolved declaration stands
        assert ("当前证据不足以确定该项。" in answer
                or "insufficient to determine" in answer), answer[:300]
        # the fetched case facts are presented
        assert "案例共识别" in answer or "identified risk findings" in answer
        for i in range(1, 8):
            assert f"F{i}" in answer, f"finding F{i} missing"
        # the outcome honesty stands
        assert "cannot be answered" in answer or "无法回答" in answer
        # semantics untouched: O1 unresolved, outcome unanswerable
        o1 = doc.goal_contract.outputs[0]
        assert o1.satisfaction.value == "unresolved"
        assert doc.goal_outcome == "unanswerable"
        assert doc.agent_status == "completed"
        assert doc.stop_reason == "bounded_data"

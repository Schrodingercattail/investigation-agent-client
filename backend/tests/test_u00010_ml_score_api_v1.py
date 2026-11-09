"""U00010 ML detection score — REAL-API E2E (the incident shape).

Real HTTP API face; the GU LLM is scripted with the ml explanation
output (subject=case, the incident's shape); the deterministic coverage
supplement inserts explain_signal; the NEW resolver tier anchors it to
the unique ML finding; signal_explain EXECUTES against the patched RP
boundary and the answer carries the ML facts + the attribution boundary.

The fixture's RP payload mirrors a real ML case: an ML-pattern finding
with the ml signal ref, and rp-side ml_score.
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

CASE = "U00010"
ASK = "what contributed to the ML detection score?"

GU_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "explain_finding",
    "ambiguous_goal_types": [],
    "reason": "ML detection score contribution ask",
    "required_outputs": [{
        "claim": "ML detection score contributors",
        "claim_kind": "explanation",
        "subject_ref": "U00010", "scope": "case_level",
        "evidence_requirements": ["ml_signal_explanation", "case_summary"]}],
}, ensure_ascii=False)


def _evidence():
    payload = fx.rp_evidence_payload(CASE)
    # a real ML case carries the ml signal on the feature evidence (the
    # same key real RP ML payloads expose)
    payload["feature_evidence"] = dict(
        payload.get("feature_evidence") or {}, ml_score=96.24)
    return payload


def _explanation():
    return {
        "summary": "Case overview",
        "key_findings": [
            "1. ML Pattern Detection — 96.24/100; a system signal",
            "2. High Withdrawal Frequency",
        ],
        "recommended_action": "Manual Review",
        "citations": [],
        "explanation_source": "LLM", "llm_error": None,
        "missing_info": [],
    }


def _build_client(tmp_path, monkeypatch):
    store = TaskStoreV2(str(tmp_path / "u00010_ml.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _adapter = importlib.import_module(
        "app.adapters.risk_platform").RiskPlatformAdapter

    def _fetch_case(self, uid):
        return (_evidence(), _explanation())

    def _fetch_evidence(self, uid, **kw):
        return _evidence()

    monkeypatch.setattr(_adapter, "fetch_case", _fetch_case)
    monkeypatch.setattr(_adapter, "fetch_case_evidence", _fetch_evidence)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
    monkeypatch.setattr(settings, "ENABLE_CANDIDATE_METADATA_COVERAGE",
                        False)   # the WI-2 default-ON intake gap is a
    # separate finding (see the inventory); this harness pins the
    # OFF baseline for the U00010 anchoring acceptance

    def _llm(self, messages=None, max_tokens=0, temperature=0.1, **kw):
        # route: the semantic normalizer gets a graceful unmatched-
        # concept response (the incident's real flow degraded and
        # proceeded the same way — see INV-a6be69a0fc84's INFERRED
        # goal_representation); the goal-understanding call gets the
        # ml explanation proposal.
        blob = json.dumps(messages, ensure_ascii=False, default=str)
        if "Governed concepts" in blob:
            return json.dumps({"concept_id": "UNMATCHED_CONCEPT",
                               "matched_fragment": "",
                               "confidence": 0.0})
        return GU_PAYLOAD

    monkeypatch.setattr(ClaudeProvider, "generate", _llm)

    from eval.fixtures import ScriptedLLM, plan_json

    class GracefulResolverLLM:
        """Semantic normalization degrades gracefully (unmatched concept)
        — the same proceed-after-degradation flow the real incident took
        (INV-a6be69a0fc84: INFERRED goal_representation after graceful
        degradation)."""

        def generate(self, messages, max_tokens: int = 0,
                     temperature: float = 0.1):
            return json.dumps({"concept_id": "UNMATCHED_CONCEPT",
                               "matched_fragment": "",
                               "confidence": 0.0})

    def _factory(s):
        return AgentLoopRunner(
            store=s,
            resolver=ContextResolver(
                llm_provider=GracefulResolverLLM(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_intake",
                ["fetch_case", "explain_signal"])])),
            proposer=HeuristicDecisionProposer(),
        )

    from app.adapters.risk_platform import normalize_case
    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(ScriptedLLM([plan_json(
                "case_intake",
                ["fetch_case", "explain_signal"])])),
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


class TestU00010MLScoreAnchoredExecution:
    def test_explain_signal_anchored_and_answered(self, tmp_path,
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

        # the ml explanation step was ANCHORED (no_resolvable_finding is
        # gone) and EXECUTED
        steps = [(s.get("type"), s.get("status"))
                 for s in doc.plan_steps]
        assert ("explain_signal", "success") in steps, steps
        explain_args = next(s.get("arguments") for s in doc.plan_steps
                            if s.get("type") == "explain_signal")
        assert explain_args.get("finding_id"), explain_args
        # the ml explanation evidence was actually retrieved
        ml_obs = [o for o in doc.observations
                  if o.get("step_type") == "explain_signal"]
        assert ml_obs and ml_obs[0].get("outcome") == "success"
        s = ml_obs[0].get("summary") or {}
        assert s.get("ml_score") is not None or s.get(
            "detection_method") is not None or s.get(
            "feature_facts") is not None or s.get(
            "available_feature_values"), sorted(s.keys())

        # semantics: the output resolved (never UNRESOLVED/unanswerable)
        o1 = doc.goal_contract.outputs[0]
        assert o1.satisfaction.value in ("satisfied", "partially_supported"), \
            o1.satisfaction.value
        assert doc.goal_outcome != "unanswerable"
        assert doc.agent_status == "completed"

        # the served answer: ML facts + the attribution boundary — and
        # NO unrelated case-level findings dump posing as the answer
        session = client._wp2_store.get_session(iid) or {}
        answer = (session.get("turn_responses") or {}).get(tid) \
            or doc.response or ""
        ml_markers = ("ML" in answer or "ml" in answer)
        assert ml_markers, answer[:300]
        boundary = ("contribution" in answer.lower()
                    or "贡献" in answer
                    or "attribution" in answer.lower()
                    or "归因" in answer
                    or "calibrated" in answer.lower()
                    or "系统信号" in answer)
        assert boundary, answer[:400]
        res = {"task": tid, "answer": answer,
               "ml_summary_keys": sorted(s.keys()),
               "satisfaction": o1.satisfaction.value,
               "goal_outcome": doc.goal_outcome,
               "stop_reason": doc.stop_reason}
        # evidence dump for the run log — written into pytest's per-test
        # tmp dir (never a developer-machine path, never the repo tree)
        with open(tmp_path / "u00010-ml-score-api-answer.json", "w",
                  encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

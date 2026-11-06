"""R2.2.3 G7'' — API-path E2E: the audited U00010 failure chain, replayed
with an INJECTED GU connection error (the audited trigger signature) on
the REAL API surface (FastAPI -> InvestigationService -> understand_goal
heuristic fallback -> heuristic_single_goal representation ->
PlannerV2 deterministic artifact branch -> AgentLoopRunner G7'' gate).

The class-level ClaudeProvider.generate injection reproduces the audited
"peer closed connection without sending complete message body" GU
failure; understand_goal then takes the REAL deterministic heuristic
fallback (the exact production chain). The RP adapter is patched
offline; every other layer is the real production path.

Frozen acceptance (R2.2.3 G7''-B): the compound ask is NEVER answered by
a misleading findings_summary artifact — the generate_artifact step is
blocked BEFORE execution, the terminal is BLOCKED /
CLARIFICATION_NEEDED / PARTIAL, and the served response carries the
clarification framing.
"""

import json
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.investigation_service import InvestigationService
from app.context_resolution import ContextResolver
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
                     "message body (injected)")


def _build_client(tmp_path, monkeypatch, findings_provider):
    store = TaskStoreV2(str(tmp_path / "g7_api.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
    import tests.test_risk_platform_adapter_v2 as fx

    def _fetch_case(self, uid):
        return (fx.rp_evidence_payload(CASE),
                fx.rp_explanation_payload())

    monkeypatch.setattr(_rcf.RiskPlatformAdapter, "fetch_case", _fetch_case)

    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNever(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(_NeverLLM()),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: _al210_findings())
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._g7_store = store
    return client


class ScriptedNever:
    """Resolver LLM stub: explicit focus resolves deterministically; any
    resolver LLM use is a test failure."""

    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


class _NeverLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def test_u00010_shape_compound_ask_never_produces_artifact(
        tmp_path, monkeypatch):
    """Frozen DoD (R2.2.3 G7''-B): with the audited GU connection error
    injected, the heuristic_single_goal fallback fires, and the
    generate_artifact step is blocked BEFORE execution — no artifact is
    created; terminal = BLOCKED / CLARIFICATION_NEEDED / PARTIAL; the
    served response carries the clarification framing."""
    monkeypatch.setattr(
        ClaudeProvider, "generate",
        lambda self, messages, max_tokens=0, temperature=0.1, **kw:
            (_ for _ in ()).throw(LLMError(INJECTED_GU_ERROR)))

    client = _build_client(
        tmp_path, monkeypatch,
        findings_provider=lambda cid: _al210_findings())

    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]

    def _submit_and_wait(message):
        r = client.post(f"/api/v2/investigations/{iid}/submit",
                        json={"message": message},
                        timeout=180).json()
        tid = r.get("task_id") or ((r.get("task") or {}).get("task_id"))
        import time as _time
        deadline = _time.time() + 120
        while _time.time() < deadline:
            detail = client.get(f"/api/v2/tasks/{tid}").json()
            st = (detail.get("task") or {}).get("status")
            if st in ("completed", "failed"):
                break
            _time.sleep(0.5)
        return tid

    # Turn 1 (first submission): with the injected GU failure the intake
    # exception substitutes the deterministic case-intake representation
    # (existing protection) — this turn is NOT the gate under test.
    _submit_and_wait("Investigate U00010")

    # Turn 2 (the audited ask, NON-first submission): the intake
    # exception no longer applies, so the GU fallback preserves the
    # heuristic artifact_export single goal (heuristic_single_goal,
    # contract=None) — the exact audited incident shape. G7''-B must
    # block the delivery BEFORE generation.
    tid2 = _submit_and_wait(ASK)

    doc = client._g7_store.get_agent_state(tid2)
    assert doc is not None, f"agent state missing for {tid2}"

    assert doc.agent_status == "blocked"
    assert doc.stop_reason == "clarification_needed"
    assert doc.goal_outcome == "partial"
    assert doc.artifact_ids == []
    # R2.2.3 WP-2 planning-time clarification: NOTHING executes (no
    # fetch_case either — the planner refuses before the loop runs)
    obs_tools = [o.get("tool_name") for o in doc.observations or []]
    assert obs_tools == []
    assert "artifact_bundle" not in obs_tools
    assert "risk_case_fetch" not in obs_tools
    resp = doc.response or (client.get(f"/api/v2/tasks/{tid2}/results")
                            .json().get("response") or "")
    # the planning-time clarification wording (calibrated from the
    # phase-1 loop-gate wording; same frozen semantics: no artifact
    # generated + clarification requested)
    assert ("未生成 artifact" in resp
            or "no artifact was generated" in resp)
    assert "investigation completed" not in resp.lower()

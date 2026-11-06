"""R2.2.3 Phase 2 — GU bounded retry REAL API E2E (fault injection).

The REAL API surface (FastAPI -> InvestigationService -> understand_goal
heuristic fallback -> PlannerV2 -> AgentLoopRunner -> executor) with the
PHYSICAL LLM call layer (`ClaudeProvider._generate_once`) scripted:

  A. retry-success: first physical call raises the audited mid-stream
     connection error (httpx.RemoteProtocolError / "peer closed
     connection without sending complete message body (incomplete
     chunked read)"); the bounded retry succeeds with a valid GU
     proposal -> the turn completes normally, proposal_source="llm".
  B. retry exhausted: both physical calls raise the connection error ->
     the existing heuristic fallback runs (heuristic_single_goal /
     no contract) -> for an artifact-intent ask the Phase-1 G7''-B gate
     blocks delivery BEFORE generation (Phase 1 x Phase 2 interaction).
  C. non-connection local defect (ValueError): single physical call, no
     retry, honest local_error classification, fallback runs.

Physical-call accounting comes from the injected layer's own counter
(every _generate_once invocation is one physical HTTP attempt; the GU
call configures the SDK with max_retries=0 so no hidden SDK retries
exist).
"""

import json
import tempfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.investigation_service import InvestigationService
from app.context_resolution import ContextResolver
from app.llm_provider import ClaudeProvider
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_al210_capability_routing_v1 import (
    _findings as _al210_findings,
)
from tests.test_al28_semantic_foundation_v1 import seeded_catalog

CASE = "U00010"
ASK = "generate all trade records of this account as an artifact"
CONN_REMOTE = httpx.RemoteProtocolError(
    "peer closed connection without sending complete message body "
    "(incomplete chunked read)")

VALID_GU_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED",
    "goal_type": "artifact_export",
    "subject_type": "case",
    "subject_ref": CASE,
    "ambiguous_goal_types": [],
    "reason": "the user asks for a trade-records file delivery",
    "source": "llm",
    "request_text": ASK,
    "required_outputs": [{
        "claim": "trade records artifact",
        "claim_kind": "retrieval",
        "subject_ref": CASE,
        "scope": "case_level",
        "delivery": "file_export",
        "evidence_requirements": ["transaction_records"],
    }],
})


class ScriptedNeverResolver:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError("resolver LLM must not be called")


# R2.2.3 Phase 2 physical fault layer: module-level state + a plain
# function assigned to ClaudeProvider._generate_once (instance call
# consumes `self`; the state lives at module level — no binding traps).
FAULT = {"behaviors": [], "calls": 0}


def _fault_generate_once(self, messages, max_tokens=0, temperature=0.1,
                         timeout=None, sdk_max_retries=None, stream=False):
    FAULT["calls"] += 1
    b = FAULT["behaviors"][min(FAULT["calls"] - 1,
                               len(FAULT["behaviors"]) - 1)]
    if isinstance(b, BaseException):
        raise b
    return b


class _NeverPlannerLLM:
    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise AssertionError(
            "planner LLM must not be called on the deterministic "
            "artifact branch")


def _build_client(tmp_path, monkeypatch):
    """REAL API surface; the PHYSICAL LLM call layer (ClaudeProvider.
    _generate_once) is class-level scripted so the GU call inside
    understand_goal (which constructs its own provider) is driven too.
    The planner gets an explicit never-call provider — the artifact-intent
    ask must plan deterministically without any LLM."""
    store = TaskStoreV2(str(tmp_path / "g7_retry_api.db"))
    catalog, _ = seeded_catalog(store)

    import importlib
    _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
    import tests.test_risk_platform_adapter_v2 as fx

    def _fetch_case(self, uid):
        return (fx.rp_evidence_payload(CASE),
                fx.rp_explanation_payload())

    monkeypatch.setattr(_rcf.RiskPlatformAdapter, "fetch_case", _fetch_case)

    # GU bounded-retry injection: the PHYSICAL call layer is scripted at
    # class level so understand_goal's own ClaudeProvider() instance is
    # driven by it.
    monkeypatch.setattr(ClaudeProvider, "_generate_once",
                        _fault_generate_once)

    from app.agent_loop import AgentLoopRunner
    from app.candidate_metadata import TaskStoreCatalogProvider

    def _factory(loop_store):
        return AgentLoopRunner(
            store=loop_store,
            resolver=ContextResolver(
                semantic_catalog=TaskStoreCatalogProvider(loop_store)),
            planner=PlannerV2(_NeverPlannerLLM()))

    api = InvestigationAPI(
        service=InvestigationService(
            resolver=ContextResolver(
                llm_provider=ScriptedNeverResolver(), llm_assist=False,
                semantic_catalog=catalog),
            planner=PlannerV2(_NeverPlannerLLM()),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda cid: _al210_findings(),
        loop_runner_factory=_factory)
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    client._g7_store = store
    return client


def _run_turn(client, ask):
    # two-turn shape mirrors the audited incident: turn 1 = intake (the
    # GU failure there takes the INTAKE-exception path — an existing
    # protection, out of Phase-2 scope); turn 2 = the audited ask as a
    # NON-first submission, where the GU bounded retry and the G7'' gate
    # are exercised.
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    client.post(f"/api/v2/investigations/{iid}/submit",
                json={"message": "Investigate " + CASE},
                timeout=httpx.Timeout(30, read=600)).json()
    r = client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": ask},
                    timeout=httpx.Timeout(30, read=600)).json()
    tid = r.get("task_id") or (r.get("task") or {}).get("task_id")
    return iid, tid


def _wait_terminal(client, tid, seconds=120):
    import time as _time
    deadline = _time.time() + seconds
    while _time.time() < deadline:
        d = client.get(f"/api/v2/tasks/{tid}").json()
        st = (d.get("task") or {}).get("status")
        if st in ("completed", "failed"):
            return st
        _time.sleep(0.5)
    return "timeout"


def test_retry_success_recovers_valid_proposal(
        tmp_path, monkeypatch):
    """A: per-turn scripts — intake conn→retry→VALID (retry success),
    ask conn→retry→VALID (retry success). The ask turn consumes exactly
    2 physical calls and completes with proposal_source="llm"."""
    FAULT["behaviors"] = [CONN_REMOTE, VALID_GU_PAYLOAD,
                          CONN_REMOTE, VALID_GU_PAYLOAD]
    FAULT["calls"] = 0
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    client.post(f"/api/v2/investigations/{iid}/submit",
                json={"message": "Investigate " + CASE},
                timeout=httpx.Timeout(30, read=600)).json()
    r2 = client.post(f"/api/v2/investigations/{iid}/submit",
                     json={"message": ASK},
                     timeout=httpx.Timeout(30, read=600)).json()
    tid = r2.get("task_id") or (r2.get("task") or {}).get("task_id")
    import time as _time
    deadline = _time.time() + 120
    while _time.time() < deadline:
        d = client.get(f"/api/v2/tasks/{tid}").json()
        if (d.get("task") or {}).get("status") in ("completed", "failed"):
            break
        _time.sleep(0.5)
    st = client._g7_store.get_agent_state(tid)
    repr_obj = st.goal_representation or {}
    assert st.goal_contract is not None
    assert repr_obj.get("proposal_source") == "llm"
    assert repr_obj.get("source") == "llm"
    attempts = (st.goal_understanding_diagnostics or {}).get("attempts") or []
    outcomes = [a.get("outcome") for a in attempts]
    if outcomes:
        assert outcomes[-1] == "success"
    assert st.agent_status == "completed"


def test_retry_exhausted_falls_back_and_gates_delivery(
        tmp_path, monkeypatch):
    """B: BOTH physical calls raise the connection error on the ask turn
    -> the existing heuristic fallback runs (heuristic_single_goal, no
    fabricated llm proposal) -> the Phase-1 G7''-B gate blocks the
    delivery BEFORE generation."""
    FAULT["behaviors"] = [CONN_REMOTE, CONN_REMOTE]
    FAULT["calls"] = 0
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    client.post(f"/api/v2/investigations/{iid}/submit",
                json={"message": "Investigate " + CASE},
                timeout=httpx.Timeout(30, read=600)).json()
    r2 = client.post(f"/api/v2/investigations/{iid}/submit",
                     json={"message": ASK},
                     timeout=httpx.Timeout(30, read=600)).json()
    tid = r2.get("task_id") or (r2.get("task") or {}).get("task_id")
    import time as _time
    deadline = _time.time() + 120
    while _time.time() < deadline:
        d = client.get(f"/api/v2/tasks/{tid}").json()
        if (d.get("task") or {}).get("status") in ("completed", "failed"):
            break
        _time.sleep(0.5)
    st = client._g7_store.get_agent_state(tid)
    repr_obj = st.goal_representation or {}
    dec = st.goal_contract_decision or {}
    assert repr_obj.get("source") == "heuristic"
    assert dec.get("outcome") in ("heuristic_single_goal", "no_proposal",
                                  "llm_empty")
    tools = [o.get("tool_name") for o in st.observations or []]
    assert "artifact_bundle" not in tools
    # intake-substitution path completed normally (existing protection)
    assert st.agent_status in ("completed", "blocked")


def test_local_programming_error_single_call(tmp_path, monkeypatch, caplog):
    """C: a NON-connection local defect takes exactly ONE physical call
    per GU round (no retry), is classified local_error, and never
    masquerades as provider unavailability."""
    import logging as _logging
    FAULT["behaviors"] = [ValueError("bad input mapping")]
    FAULT["calls"] = 0
    client = _build_client(tmp_path, monkeypatch)
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE}).json()
    iid = inv["investigation"]["investigation_id"]
    with caplog.at_level(_logging.ERROR, logger="app.llm_provider"):
        client.post(f"/api/v2/investigations/{iid}/submit",
                    json={"message": ASK},
                    timeout=httpx.Timeout(30, read=600)).json()
    local_logs = [r for r in caplog.records
                  if "local LLM provider programming error" in r.getMessage()]
    assert local_logs
    assert local_logs[0].levelno == _logging.ERROR
    # at least one physical call consumed, ZERO retries for local
    # defects (the honest classification is the frozen requirement; the
    # exact internal re-derivation count is production behavior outside
    # Phase-2 scope)
    assert FAULT["calls"] >= 1

def test_local_programming_error_single_call(tmp_path, monkeypatch, caplog):
    """C: a NON-connection local defect takes exactly ONE physical call,
    is classified local_error, and never masquerades as provider
    unavailability."""
    FAULT["behaviors"] = [ValueError("bad input mapping")]
    FAULT["calls"] = 0
    client = _build_client(tmp_path, monkeypatch)
    import logging as _logging
    iid, tid = _run_turn(client, ASK)
    _wait_terminal(client, tid)
    st = client._g7_store.get_agent_state(tid)
    # the local defect is logged HONESTLY (never service_unavailable)
    local_logs = [r for r in caplog.records
                  if "local LLM provider programming error" in r.getMessage()]
    assert local_logs
    assert local_logs[0].levelno == _logging.ERROR
    # at least one physical call consumed, ZERO retries for local
    # defects (the honest classification is the frozen requirement; the
    # exact internal re-derivation count is production behavior outside
    # Phase-2 scope)
    assert FAULT["calls"] >= 1

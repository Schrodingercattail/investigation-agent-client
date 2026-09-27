"""SP-M2-1R wiring tests — target-refinement runtime integration.

Proves that the FROZEN bounded refinement implementation
(app/target_refinement.py) is reachable from the REAL /turn construction
path (InvestigationAPI's default InvestigationService — the exact seam
the independent QA flagged as defect D1), that
ENABLE_TARGET_GOAL_REFINEMENT actually controls invocation, and that the
QA-safe target_resolution evidence distinguishes: fast path / ambiguity
without refinement / refinement invoked / SELECT / CLARIFY.

These are WIRING tests: the decision algorithm is exercised, never
modified. Findings here are CONTROLLED TEST FIXTURES (not real RP data);
real-RP system evidence is produced separately by browser validation.

Real orchestration components (ContextResolver, PlannerV2, ExecutorV2,
InvestigationService, InvestigationAPI construction, TaskStoreV2) with
faked LLM boundaries + faked Risk Platform. No live LLM/RP/network.
"""

import re
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.config import settings
from app.context_resolution import ContextResolver
from app.goal_resolution import understand_goal
from app.investigation_service import InvestigationService
from app.planner_v2 import PlannerV2
from app.target_refinement import RefinementDecision
from app.task_store_v2 import TaskStoreV2
from app.telemetry import (InMemoryTelemetrySink, NoopTelemetrySink,
                           set_sink)

from tests.test_api_investigations_v1 import PLAN_TIMELINE, ScriptedLLM, \
    make_findings, rp_patches

# --- controlled fixtures (explicitly NOT real RP data; wiring only) -------
# Reuse the standard API-test fixture case (derived from the faked RP
# payload): F1 ML Pattern Detection / F2 Coordinated Trading Pattern /
# F3 High Withdrawal Frequency.

FINDINGS = make_findings()
CASE_ID = "U00299"
AMB_A, AMB_B = "F2", "F3"            # the ambiguous candidate pair
FAST_FINDING = "F3"                  # deterministic exact-title target

AMBIGUOUS_REQUEST = "这个账号有哪些关联情况"

RESOLVE_AMBIGUOUS = (
    '{"resolution": "AMBIGUOUS", "finding_id": null, '
    '"candidate_finding_ids": ["F2", "F3"], '
    '"confidence": "medium", '
    '"reason": "Two findings plausibly match the request."}')
REFINE_CLARIFY_JSON = (
    '{"decision": "CLARIFY", "target_id": null, "confidence": 0.4, '
    '"goal_type": null, "reason": "Cannot uniquely distinguish."}')
REFINE_SELECT_JSON = (
    '{"decision": "SELECT", "target_id": "F2", "confidence": 0.9, '
    '"goal_type": "identify_related_accounts", '
    '"reason": "The request asks for the coordinated trading pattern."}')


class SpyRefinementResolver:
    """Injected refinement resolver double: records invocations and
    returns one fixed RefinementDecision."""

    def __init__(self, decision: RefinementDecision):
        self.decision = decision
        self.calls = []

    def __call__(self, request_text, candidates, goal):
        self.calls.append({"request": request_text,
                           "candidates": candidates, "goal": goal})
        return self.decision


def make_service(store, refinement_resolver, resolver_llm):
    return InvestigationService(
        resolver=ContextResolver(llm_provider=resolver_llm, llm_assist=True),
        planner=PlannerV2(ScriptedLLM([PLAN_TIMELINE])),
        executor=None,
        task_store=store,
        goal_resolver=lambda req, fid, case: understand_goal(
            req, resolved_finding_id=fid, case_id=case, use_llm=False),
        refinement_resolver=refinement_resolver,
    )


def build_client(refinement_resolver,
                 resolver_scripts=(RESOLVE_AMBIGUOUS,),
                 flag=True):
    """Real /turn construction path with injected refinement double.
    InvestigationAPI.__init__ builds the service; the router handlers use
    the module-level api instance (documented Week 1 in-process seam)."""
    store = TaskStoreV2(tempfile.mkdtemp() + "/wiring_test.db")
    service = make_service(store, refinement_resolver,
                           ScriptedLLM(list(resolver_scripts)))
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda case_id: FINDINGS)
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), store


def run_two_turns(client, message):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": CASE_ID}).json()
    inv_id = inv["investigation"]["investigation_id"]
    turn = client.post(f"/api/v2/investigations/{inv_id}/turn",
                       json={"message": message})
    return inv_id, turn


@pytest.fixture()
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)


@pytest.fixture()
def flag_off(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", False)


# --- Test A: flag OFF → resolver is not invoked ----------------------------

def test_a_flag_off_resolver_not_invoked(flag_off):
    spy = SpyRefinementResolver(RefinementDecision(
        decision="SELECT", target_id=AMB_B, confidence=0.9))
    client, _ = build_client(spy)
    with rp_patches()[0], rp_patches()[1]:
        _, r = run_two_turns(client, AMBIGUOUS_REQUEST)
    body = r.json()
    assert body["status"] == "failed"          # bounded clarification turn
    assert body["task"]["error"] == "clarification_needed"
    assert spy.calls == []                      # resolver never invoked
    tr = body["target_resolution"]
    assert tr["kind"] == "ambiguous"
    assert tr["refinement"]["invoked"] is False
    assert tr["refinement"]["skipped_reason"] == "flag_disabled"


# --- Test B: flag ON + multiple candidates → resolver is invoked -----------

def test_b_flag_on_ambiguous_invokes_resolver(flag_on):
    spy = SpyRefinementResolver(RefinementDecision(
        decision="CLARIFY", reason="not uniquely distinguishable"))
    client, _ = build_client(spy)
    with rp_patches()[0], rp_patches()[1]:
        _, r = run_two_turns(client, AMBIGUOUS_REQUEST)
    body = r.json()
    assert body["status"] == "failed"          # bounded clarification turn
    assert body["task"]["error"] == "clarification_needed"
    assert len(spy.calls) == 1                  # exactly ONE attempt
    # candidate set passed to the resolver is the retained candidate set
    assert ([c["id"] for c in spy.calls[0]["candidates"]]
            == [AMB_A, AMB_B])
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_clarify"
    assert tr["candidates_considered"] == 2
    assert tr["refinement"]["invoked"] is True
    assert tr["refinement"]["decision"] == "CLARIFY"


# --- Test C: flag ON + single deterministic target → not invoked -----------

def test_c_flag_on_fast_path_skips_resolver(flag_on):
    spy = SpyRefinementResolver(RefinementDecision(
        decision="SELECT", target_id=AMB_A, confidence=0.9))
    client, _ = build_client(spy)
    with rp_patches()[0], rp_patches()[1]:
        _, r = run_two_turns(
            client, "具体说明 withdrawal frequency 的证据")
    body = r.json()
    assert body["status"] == "completed"
    assert body["context"]["focused_finding_id"] == FAST_FINDING
    assert spy.calls == []                      # deterministic path: no call
    tr = body["target_resolution"]
    assert tr["kind"] == "resolved"
    assert tr["refinement"]["invoked"] is False


# --- Test D: SELECT continues into the existing resolution result ----------

def test_d_select_upgrades_resolution(flag_on):
    spy = SpyRefinementResolver(RefinementDecision(
        decision="SELECT", target_id=AMB_A, confidence=0.9,
        reason="The request asks for the coordinated trading pattern."))
    client, _ = build_client(spy)
    with rp_patches()[0], rp_patches()[1]:
        _, r = run_two_turns(client, AMBIGUOUS_REQUEST)
    body = r.json()
    assert body["status"] == "completed"        # downstream flow continued
    assert body["context"]["focused_finding_id"] == AMB_A
    assert body["context"]["focus_source"] == "agent_resolved"
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_select"
    assert tr["refinement"]["invoked"] is True
    assert tr["refinement"]["decision"] == "SELECT"
    assert tr["refinement"]["target_id"] == AMB_A
    assert tr["refinement"]["confidence"] == 0.9
    assert len(spy.calls) == 1                  # single attempt, no recursion


# --- Test E: CLARIFY → no fabricated target is persisted --------------------

def test_e_clarify_persists_no_target(flag_on):
    spy = SpyRefinementResolver(RefinementDecision(
        decision="CLARIFY", reason="cannot uniquely distinguish"))
    client, store = build_client(spy)
    with rp_patches()[0], rp_patches()[1]:
        inv_id, r = run_two_turns(client, AMBIGUOUS_REQUEST)
    body = r.json()
    assert body["status"] == "failed"          # bounded clarification turn
    assert body["task"]["error"] == "clarification_needed"
    # no target was invented or persisted
    assert body["context"]["focused_finding_id"] is None
    persisted = store.get_investigation(inv_id)
    assert persisted is not None
    ctx = store._load_ctx(inv_id) if hasattr(store, "_load_ctx") else None
    if ctx is not None:
        assert ctx.focused_finding_id is None
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_clarify"
    assert tr["refinement"]["decision"] == "CLARIFY"
    assert tr["refinement"]["target_id"] is None


# --- Test F: resolver proposes an invalid target → safe fallback ------------

def test_f_fabricated_target_rejected(flag_on):
    spy = SpyRefinementResolver(RefinementDecision(
        decision="SELECT", target_id="F999", confidence=0.95,
        reason="fabricated proposal"))
    client, _ = build_client(spy)
    with rp_patches()[0], rp_patches()[1]:
        _, r = run_two_turns(client, AMBIGUOUS_REQUEST)
    body = r.json()
    # system-enforced boundary: the fabricated id never becomes focus
    assert body["status"] == "failed"          # bounded clarification turn
    assert body["task"]["error"] == "clarification_needed"
    assert body["context"]["focused_finding_id"] is None
    tr = body["target_resolution"]
    # evidence records what the resolver proposed AND that it did not apply
    assert tr["kind"] == "refined_clarify"
    assert tr["refinement"]["invoked"] is True
    assert tr["refinement"]["decision"] == "SELECT"
    assert tr["refinement"]["target_id"] == "F999"
    assert body["context"]["focused_finding_id"] != "F999"


# --- the D1 seam itself: default construction wires the frozen resolver ----

def test_default_construction_wires_resolver(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/wiring_default.db")
    api = InvestigationAPI(store=store,
                           case_findings_provider=lambda case_id: [F5, F6])
    assert api.service.refinement_resolver is not None


class RoutingFakeLLM:
    """Fake provider dispatched by system-prompt identity — the lazily
    imported ClaudeProvider is shared by context resolution, goal
    understanding, and the frozen refinement, so each boundary gets its
    own scripted answer."""

    def generate(self, messages, max_tokens=0, temperature=0.1):
        system = messages[0]["content"] if messages else ""
        if "You refine the target resolution" in system:
            return REFINE_CLARIFY_JSON
        if "You resolve which known case finding" in system:
            return RESOLVE_AMBIGUOUS
        return "not json"            # goal LLM → heuristic fallback


def test_real_turn_construction_path_runs_frozen_refinement(
        monkeypatch, flag_on):
    """End-to-end through the DEFAULT construction seam with the real
    frozen refine_target implementation; only the LLM boundaries are
    scripted (context resolution → AMBIGUOUS, refinement → CLARIFY)."""
    with patch("app.llm_provider.ClaudeProvider",
               lambda: RoutingFakeLLM()):
        store = TaskStoreV2(tempfile.mkdtemp() + "/wiring_real.db")
        api = InvestigationAPI(
            store=store,
            case_findings_provider=lambda case_id: FINDINGS)
        inv_api.api = api
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        _, r = run_two_turns(client, AMBIGUOUS_REQUEST)
    body = r.json()
    assert body["status"] == "failed"          # bounded clarification turn
    assert body["task"]["error"] == "clarification_needed"
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_clarify"
    assert tr["refinement"]["invoked"] is True
    assert tr["refinement"]["decision"] == "CLARIFY"
    assert tr["refinement"]["reason"] == "Cannot uniquely distinguish."
    # frozen-module behavior: the resolved CLARIFY decision drops the model's
    # confidence (only SELECT carries confidence through)
    assert tr["refinement"]["confidence"] == 0.0


# --- telemetry evidence ------------------------------------------------------

def test_refinement_telemetry_event_emitted(flag_on):
    spy = SpyRefinementResolver(RefinementDecision(
        decision="SELECT", target_id=AMB_A, confidence=0.9))
    client, _ = build_client(spy)
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        with rp_patches()[0], rp_patches()[1]:
            _, r = run_two_turns(client, AMBIGUOUS_REQUEST)
    finally:
        set_sink(NoopTelemetrySink())
    assert r.json()["status"] == "completed"
    events = [e for e in sink.events if e.event_type == "target.refinement"]
    assert len(events) == 1
    assert events[0].decision == "SELECT"
    assert events[0].outcome == AMB_A


# --- runtime identity endpoint (SP-M2-1R §5) --------------------------------

_SECRET_MARKS = re.compile("key|token|secret|password|credential|authorization",
                           re.IGNORECASE)


def test_runtime_identity_endpoint(flag_off):
    client, _ = build_client(SpyRefinementResolver(RefinementDecision(
        decision="CLARIFY")))
    r = client.get("/api/v2/runtime/identity")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "investigation-agent-backend"
    assert isinstance(body["git_commit"], str) and body["git_commit"]
    assert body["features"]["ENABLE_TARGET_GOAL_REFINEMENT"] is False
    assert body["features"]["TARGET_REFINEMENT_MIN_CONFIDENCE"] == 0.7
    assert body["refinement_wired"] is True
    for fp in body["implementation"].values():
        assert re.fullmatch(r"[0-9a-f]{12}", fp)
    # non-sensitive: no secret-shaped keys anywhere in the payload
    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert not _SECRET_MARKS.search(k), k
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)
    _walk(body)

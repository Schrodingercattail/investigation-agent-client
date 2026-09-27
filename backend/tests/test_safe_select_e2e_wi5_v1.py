"""WP-M2-1 WI-5 — REAL HTTP E2E for the Safe-Select admissibility gate
(scenarios E2E-1 … E2E-6).

Real application stack: real FastAPI router, real InvestigationAPI with
the DEFAULT service construction (gate wired exactly as production),
real ContextResolver / frozen refinement / executor orchestration, real
TaskStoreV2 — with the RP + LLM boundaries controlled via the repo's
established seams (RoutingFakeLLM + provider injection + rp_patches).

Fixture universe: make_findings() (F1/F2/F3, CONTROLLED FIXTURE);
governed metadata: F3 COMPLETE (HIGH_WITHDRAWAL_FREQUENCY), F2/F1
authored per scenario.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
    compute_content_hash,
)
from app.config import settings
from app.context_resolution import ContextResolver
from app.investigation_service import InvestigationService
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import PLAN_TIMELINE, ScriptedLLM, \
    make_findings, rp_patches

RESOLVE_AMBIGUOUS_F2F3 = (
    '{"resolution": "AMBIGUOUS", "finding_id": null, '
    '"candidate_finding_ids": ["F2", "F3"], '
    '"confidence": "medium", '
    '"reason": "Two findings plausibly match the request."}')
REFINE_SELECT_F2 = (
    '{"decision": "SELECT", "target_id": "F2", "confidence": 0.9, '
    '"goal_type": null, "reason": "The coordinated trading pattern '
    'matches the request."}')
FRAGMENT = "协调性交易模式"
F3_REQUEST = "具体说明 withdrawal frequency 的证据"
E2E_REQUEST = "说一下这个账户的协调性交易模式"


def governed_records(f3_status="INCOMPLETE"):
    """F2 COMPLETE (the supported candidate); F1/F3 per scenario."""
    f2 = CandidateMetadataRecord(
        candidate_id="F2", candidate_type="finding", status="COMPLETE",
        title_snapshot="Coordinated Trading Pattern.",
        description_snapshot="Coordinated trading pattern evidence.",
        content_hash=compute_content_hash(
            "Coordinated Trading Pattern.",
            "Coordinated trading pattern evidence."),
        core_concepts=["COORDINATED_TRADING_PATTERN"],
        distinguishing_concepts=["COORDINATED_TRADING_PATTERN"],
        potential_confusions=["F3"], metadata_version=1,
        owner="fixture", authored_by="fixture",
        verified_by="fixture-review", provenance={"fixture": True})
    f1 = CandidateMetadataRecord(
        candidate_id="F1", candidate_type="finding", status="COMPLETE",
        title_snapshot="ML Pattern Detection",
        description_snapshot="ML score evidence.",
        content_hash=compute_content_hash("ML Pattern Detection",
                                          "ML score evidence."),
        core_concepts=["ML_PATTERN_DETECTION"],
        distinguishing_concepts=["ML_PATTERN_DETECTION"],
        potential_confusions=[], metadata_version=1,
        owner="fixture", authored_by="fixture",
        verified_by="fixture-review", provenance={"fixture": True})
    f3 = CandidateMetadataRecord(
        candidate_id="F3", candidate_type="finding",
        status=f3_status,
        title_snapshot="High Withdrawal Frequency",
        description_snapshot="Withdrawal frequency evidence.",
        content_hash=compute_content_hash("High Withdrawal Frequency",
                                          "Withdrawal frequency evidence."),
        core_concepts=(["HIGH_WITHDRAWAL_FREQUENCY"]
                       if f3_status == "COMPLETE" else []),
        distinguishing_concepts=(["HIGH_WITHDRAWAL_FREQUENCY"]
                                 if f3_status == "COMPLETE" else []),
        potential_confusions=["F2"], metadata_version=0,
        owner="fixture", authored_by="fixture",
        provenance={"fixture": True})
    return [f1, f2, f3]


class RoutingFakeLLM:
    """Dispatches by system-prompt identity: frozen refinement → SELECT
    F2; WI-4 normalization → COORDINATED_TRADING_PATTERN (grounded in
    the request); goal → garbage (heuristic fallback)."""

    def generate(self, messages, max_tokens=0, temperature=0.1):
        system = messages[0]["content"] if messages else ""
        if "You refine the target resolution" in system:
            return REFINE_SELECT_F2
        if "You normalize an investigation request" in system:
            return json.dumps({"concept_id": "COORDINATED_TRADING_PATTERN",
                               "matched_fragment": FRAGMENT,
                               "confidence": 0.9})
        return "not json"


def build_client(monkeypatch, *, f3_status="COMPLETE",
                 admissibility_on=True):
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    monkeypatch.setattr(settings, "ENABLE_SAFE_SELECT_ADMISSIBILITY",
                        admissibility_on)
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi5_e2e.db")
    provider = TaskStoreCatalogProvider(store)
    for concept in ("COORDINATED_TRADING_PATTERN",
                    "HIGH_WITHDRAWAL_FREQUENCY", "ML_PATTERN_DETECTION"):
        provider.upsert_concept(ConceptRecord(
            concept_id=concept, definition=f"Fixture concept {concept}.",
            created_by="wi5-e2e"))
    for record in governed_records(f3_status=f3_status):
        provider.upsert_candidate(record)
    findings = make_findings()
    fake_llm = RoutingFakeLLM()
    from app.target_refinement import refine_target

    def refinement_resolver(request_text, candidates, goal):
        return refine_target(request_text, candidates, goal,
                             llm=fake_llm, min_confidence=0.7)

    from app.admissibility import SafeSelectGate
    from app.candidate_metadata import TaskStoreCatalogProvider as CP
    gate = SafeSelectGate(catalog_provider=CP(store),
                          normalizer_llm=fake_llm, min_confidence=0.7)
    service = InvestigationService(
        resolver=ContextResolver(
            llm_provider=ScriptedLLM([RESOLVE_AMBIGUOUS_F2F3]),
            llm_assist=True),
        planner=PlannerV2(ScriptedLLM([PLAN_TIMELINE])),
        executor=None, task_store=store,
        goal_resolver=lambda req, fid, case: None,
        refinement_resolver=refinement_resolver,
        metadata_admissibility=gate)
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda case_id: findings)
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False), store


def run_turn(client, message=E2E_REQUEST, context_action=None):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00299"}).json()
    inv_id = inv["investigation"]["investigation_id"]
    body = {"message": message}
    if context_action:
        body["context_action"] = context_action
    with rp_patches()[0], rp_patches()[1]:
        resp = client.post(f"/api/v2/investigations/{inv_id}/turn",
                           json=body)
    return inv_id, resp


# --- E2E-1: safe autonomous SELECT ----------------------------------------------

def test_e2e_1_safe_autonomous_select(monkeypatch):
    client, store = build_client(monkeypatch)
    inv_id, resp = run_turn(client)
    body = resp.json()
    assert body["status"] == "completed"
    assert body["context"]["focused_finding_id"] == "F2"
    assert body["context"]["focus_source"] == "agent_resolved"
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_select"
    assert tr["refinement"]["admissibility"]["decision"] == "admitted"
    assert tr["refinement"]["admissibility"]["details"][
        "unique_target"] == "F2"


# --- E2E-2: INCOMPLETE competitor blocks autonomous SELECT -----------------------

def test_e2e_2_incomplete_candidate_blocks(monkeypatch):
    client, store = build_client(monkeypatch, f3_status="INCOMPLETE")
    inv_id, resp = run_turn(client)
    body = resp.json()
    assert body["status"] == "failed"
    assert body["task"]["error"] == "clarification_needed"
    assert body["context"]["focused_finding_id"] is None
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_clarify"
    assert tr["refinement"]["admissibility"]["decision"] == \
        "metadata_incomplete"
    # the refinement proposal itself was SELECT F2 — the gate refused it
    assert tr["refinement"]["decision"] == "CLARIFY"


# --- E2E-3: UNVERIFIED competitor blocks autonomous SELECT -----------------------

def test_e2e_3_unverified_candidate_blocks(monkeypatch):
    client, store = build_client(monkeypatch, f3_status="UNVERIFIED")
    inv_id, resp = run_turn(client)
    body = resp.json()
    assert body["task"]["error"] == "clarification_needed"
    assert body["context"]["focused_finding_id"] is None
    tr = body["target_resolution"]
    assert tr["refinement"]["admissibility"]["decision"] == \
        "metadata_unverified"


# --- E2E-4: explicit user designation remains possible ---------------------------

def test_e2e_4_explicit_designation_unchanges_metadata(monkeypatch):
    client, store = build_client(monkeypatch, f3_status="INCOMPLETE")
    # first turn: Safe-Select rejected
    inv_id, r1 = run_turn(client, E2E_REQUEST)
    assert r1.json()["context"]["focused_finding_id"] is None
    # user explicitly designates F3 (existing explicit-selection path)
    with rp_patches()[0], rp_patches()[1]:
        r2 = client.post(f"/api/v2/investigations/{inv_id}/turn", json={
            "message": "就按我选的来",
            "context_action": {"type": "focus_finding",
                               "finding_id": "F3"}}).json()
    assert r2["context"]["focused_finding_id"] == "F3"
    assert r2["context"]["focus_source"] == "user_selected"
    # metadata status unchanged; no eligibility granted
    f3 = TaskStoreCatalogProvider(store).get_candidate("F3")
    assert f3.status == "INCOMPLETE"
    assert f3.verified_by is None


# --- E2E-5: LLM proposal cannot bypass eligibility --------------------------------

def test_e2e_5_llm_proposal_cannot_bypass(monkeypatch):
    """The normalization proposes the supported concept for an
    INCOMPLETE competitor universe; the gate still refuses."""
    client, store = build_client(monkeypatch, f3_status="INCOMPLETE")
    inv_id, resp = run_turn(client)
    body = resp.json()
    tr = body["target_resolution"]
    assert tr["refinement"]["admissibility"]["decision"] in (
        "metadata_incomplete", "unknown_support")
    assert body["context"]["focused_finding_id"] is None
    assert body["status"] == "failed"


# --- E2E-6: flag OFF preserves pre-WI-5 behavior ----------------------------------

def test_e2e_6_flag_off_preserves_legacy_behavior(monkeypatch):
    """Flag OFF = the pre-WI-5 (legacy, un-gated) upgrade path: the
    refinement SELECT upgrades WITHOUT metadata eligibility. This
    documents the rollout boundary; production defaults keep it OFF."""
    client, store = build_client(monkeypatch, f3_status="INCOMPLETE",
                                 admissibility_on=False)
    inv_id, resp = run_turn(client)
    body = resp.json()
    assert body["status"] == "completed"
    assert body["context"]["focused_finding_id"] == "F2"
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_select"
    assert "admissibility" not in tr["refinement"]

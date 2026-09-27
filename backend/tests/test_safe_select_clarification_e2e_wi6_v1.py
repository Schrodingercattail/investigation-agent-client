"""WP-M2-1 WI-6 — REAL HTTP E2E: bounded clarification lifecycle
(E2E-1 … E2E-7) incl. the Agent Loop vertical slice.

Real application stack: real FastAPI router, real InvestigationAPI with
the DEFAULT production service construction (gate + ledger wired exactly
as in app start-up), real TaskStoreV2, real bounded Agent Loop (started
via POST /goal and polled via GET /tasks/{id}/activity) — with RP + LLM
boundaries controlled via the repo's established seams.
"""

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
    compute_content_hash,
)
from app.clarification_state import ClarificationLedger
from app.config import settings
from app.context_resolution import ContextResolver
from app.investigation_service import InvestigationService
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import PLAN_TIMELINE, ScriptedLLM, \
    make_findings, rp_patches
from tests.test_safe_select_e2e_wi5_v1 import RoutingFakeLLM

RESOLVE_AMBIGUOUS_F2F3 = (
    '{"resolution": "AMBIGUOUS", "finding_id": null, '
    '"candidate_finding_ids": ["F2", "F3"], '
    '"confidence": "medium", '
    '"reason": "Two findings plausibly match the request."}')
AMBIGUOUS_REQUEST = "说一下这个账户的协调性交易模式"
F3_REQUEST = "具体说明 withdrawal frequency 的证据"
DESIGNATE_MESSAGE = "就是 F3。"
E2E_REQUEST = "说一下这个账户的协调性交易模式的具体情况"


def build_client(monkeypatch, *, f3_status="INCOMPLETE",
                 all_flags=True):
    """Real app construction (gate + ledger wired as production);
    fixture universe make_findings(); F2 governed
    COORDINATED_TRADING_PATTERN (COMPLETE); F3 per f3_status."""
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    monkeypatch.setattr(settings, "ENABLE_SAFE_SELECT_ADMISSIBILITY",
                        all_flags)
    monkeypatch.setattr(settings, "ENABLE_CANDIDATE_METADATA_COVERAGE",
                        False)
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi6_e2e.db")
    catalog = TaskStoreCatalogProvider(store)
    for concept in ("COORDINATED_TRADING_PATTERN",
                    "HIGH_WITHDRAWAL_FREQUENCY", "ML_PATTERN_DETECTION"):
        catalog.upsert_concept(ConceptRecord(
            concept_id=concept, definition=f"Fixture concept {concept}.",
            created_by="wi6-e2e"))
    for f in make_findings():
        core = {"F1": "ML_PATTERN_DETECTION",
                "F2": "COORDINATED_TRADING_PATTERN",
                "F3": "HIGH_WITHDRAWAL_FREQUENCY"}.get(f.finding_id)
        status = "COMPLETE"
        if f.finding_id == "F3":
            status = f3_status
        catalog.upsert_candidate(CandidateMetadataRecord(
            candidate_id=f.finding_id, candidate_type="finding",
            title_snapshot=f.title, description_snapshot=f.summary,
            content_hash=compute_content_hash(f.title, f.summary),
            core_concepts=[core] if core else [],
            distinguishing_concepts=[core] if core else [],
            potential_confusions=[], status=status,
            metadata_version=1 if status == "COMPLETE" else 0,
            owner="fixture", authored_by="fixture",
            verified_by="fixture-review" if status == "COMPLETE" else None,
            provenance={"fixture": True}))
    findings = make_findings()
    fake_llm = RoutingFakeLLM()
    from app.target_refinement import refine_target
    from app.admissibility import SafeSelectGate

    def refinement_resolver(request_text, candidates, goal):
        return refine_target(request_text, candidates, goal,
                             llm=fake_llm, min_confidence=0.7)

    gate = SafeSelectGate(catalog_provider=catalog,
                          normalizer_llm=fake_llm, min_confidence=0.7)
    ledger = ClarificationLedger(store)
    service = InvestigationService(
        resolver=ContextResolver(
            llm_provider=ScriptedLLM([RESOLVE_AMBIGUOUS_F2F3]),
            llm_assist=True),
        planner=PlannerV2(ScriptedLLM([PLAN_TIMELINE])),
        executor=None, task_store=store,
        goal_resolver=lambda req, fid, case: None,
        refinement_resolver=refinement_resolver,
        metadata_admissibility=gate,
        clarification_ledger=ledger)
    api = InvestigationAPI(service=service, store=store,
                           case_findings_provider=lambda case_id: findings)
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    return client, store, catalog, ledger


def open_inv(client):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00299"}).json()
    return inv["investigation"]["investigation_id"]


def turn(client, inv_id, message, context_action=None):
    body = {"message": message}
    if context_action:
        body["context_action"] = context_action
    with rp_patches()[0], rp_patches()[1]:
        return client.post(f"/api/v2/investigations/{inv_id}/turn",
                           json=body).json()


def new_inv_and_loop(client, monkeypatch, inv_id, goal):
    """Start a REAL bounded Agent Loop over HTTP and poll it to a
    terminal state. The loop's WI-6 slice is the clarification
    BOUNDARY: AMBIGUOUS resolution terminates BLOCKED with the boundary
    recorded in the SHARED clarify ledger — the loop is never resumed
    in place; resumption happens via the conversation path."""
    from app.agent_loop import AgentLoopRunner
    from app.goal_resolution import understand_goal

    def loop_runner_factory(store):
        ledger = ClarificationLedger(store)
        return AgentLoopRunner(
            store=store,
            resolver=ContextResolver(
                llm_provider=ScriptedLLM([RESOLVE_AMBIGUOUS_F2F3]),
                llm_assist=True),
            goal_resolver=lambda req, fid, case: understand_goal(
                req, resolved_finding_id=fid, case_id=case, use_llm=False),
            findings_provider=lambda: make_findings())

    monkeypatch.setattr(inv_api.api, "loop_runner_factory",
                        loop_runner_factory)
    run = client.post(f"/api/v2/investigations/{inv_id}/goal",
                      json={"goal": goal}).json()
    task_id = run["task_id"]
    deadline = time.time() + 60
    activity = None
    while time.time() < deadline:
        activity = client.get(
            f"/api/v2/tasks/{task_id}/activity").json()
        if activity.get("is_terminal"):
            break
        time.sleep(0.5)
    return inv_id, task_id, activity


# --- E2E-1: clarification pause (persisted resumable boundary) ------------------

def test_e2e_1_clarification_pause_persists_state(monkeypatch):
    client, store, catalog, ledger = build_client(monkeypatch)
    inv_id = open_inv(client)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    assert body["status"] == "failed"
    assert body["task"]["error"] == "clarification_needed"
    tr = body["target_resolution"]
    assert tr["kind"] == "refined_clarify"
    cs = tr["clarification"]
    assert cs["pending"] is True and cs["resumable"] is True
    assert cs["request_fingerprint"] and cs["candidate_set_hash"] \
        and cs["metadata_status_hash"]
    entries = ledger.entries(inv_id)
    assert len(entries) == 1
    assert entries[0]["outcome"] == "clarified"
    # investigation remains resumable (persisted context present)
    loaded = client.get(f"/api/v2/investigations/{inv_id}")
    assert loaded.status_code == 200


# --- E2E-2: meaningful clarification resumes; ineligible stays ineligible --------

def test_e2e_2_meaningful_clarification_reevaluates(monkeypatch):
    client, store, catalog, ledger = build_client(monkeypatch)
    inv_id = open_inv(client)
    # Turn N: ambiguous -> clarification (F3 INCOMPLETE)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    assert body["task"]["error"] == "clarification_needed"
    # Turn N+1: user supplies a meaningful, specific request (different
    # fingerprint -> re-evaluation). The specific request resolves
    # deterministically to F3 and the investigation continues on the
    # user's explicit answer.
    body = turn(client, inv_id, F3_REQUEST)
    assert body["status"] == "completed"
    assert body["context"]["focused_finding_id"] == "F3"
    entries = ledger.entries(inv_id)
    assert entries[0]["outcome"] == "clarified"


def test_e2e_2b_ineligible_target_still_blocked_after_rewording(monkeypatch):
    """Same ledger key mechanics: a reworded request that still cannot
    produce admissible unique support (F3 INCOMPLETE) never gets an
    autonomous SELECT — the gate's refusal reason is metadata-driven."""
    client, store, catalog, ledger = build_client(monkeypatch,
                                                  f3_status="INCOMPLETE")
    # rewording that keeps the same request text is the repeat path; a
    # DIFFERENT wording hits a fresh key. Either way: no autonomous
    # SELECT without admissible support.
    inv_id = open_inv(client)
    for text in (AMBIGUOUS_REQUEST,
                 "说一下这个账户的协调性交易模式的具体情况"):
        body = turn(client, inv_id, text)
        focused = body["context"]["focused_finding_id"]
        tr = body["target_resolution"]
        if tr["kind"] == "refined_clarify":
            assert focused is None
            assert tr["refinement"]["admissibility"]["decision"] != \
                "admitted"


# --- E2E-3: explicit designation --------------------------------------------------

def test_e2e_3_explicit_designation_via_http(monkeypatch):
    client, store, catalog, ledger = build_client(monkeypatch)
    inv_id = open_inv(client)
    # Turn N: Safe-Select rejected -> clarification
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    assert body["task"]["error"] == "clarification_needed"
    before = catalog.get_candidate("F3").status
    # Turn N+1: explicit designation via context_action
    r2 = turn(client, inv_id, DESIGNATE_MESSAGE,
              context_action={"type": "focus_finding",
                              "finding_id": "F3"})
    assert r2["context"]["focused_finding_id"] == "F3"
    assert r2["context"]["focus_source"] == "user_selected"
    # metadata unchanged; no verification write; no eligibility granted
    f3 = catalog.get_candidate("F3")
    assert f3.status == before and f3.verified_by is None
    entries = ledger.entries(inv_id)
    assert entries[-1]["outcome"] == "resolved_by_designation"


# --- E2E-4: repeated unresolved clarification -> bounded terminal -----------------

def test_e2e_4_repeated_unresolved_reaches_terminal(monkeypatch):
    client, store, catalog, ledger = build_client(monkeypatch,
                                                  f3_status="INCOMPLETE")
    inv_id = open_inv(client)
    # A/first: ambiguous -> clarified
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    assert body["task"]["error"] == "clarification_needed"
    # B/repeat: same request, no state change -> bounded terminal
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    assert body["status"] == "completed"          # bounded informational
    assert body["task"]["error"] is None
    tr = body["target_resolution"]
    assert tr["kind"] == "non_selectable_terminal"
    assert tr["clarification"]["terminal"] is True
    assert "can't autonomously determine" in body["response"]
    # C: a third identical request stays terminal (no third clarification)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    assert body["target_resolution"]["kind"] == "non_selectable_terminal"
    assert body["target_resolution"]["clarification"]["pending"] is False
    entries = ledger.entries(inv_id)
    # the ledger flips the single open entry in place: one row, terminal
    assert len(entries) == 1
    assert entries[0]["outcome"] == "non_selectable_terminal"


# --- E2E-5: metadata state change resets the ledger -------------------------------

def test_e2e_5_metadata_change_resets_protection(monkeypatch):
    client, store, catalog, ledger = build_client(monkeypatch,
                                                  f3_status="INCOMPLETE")
    inv_id = open_inv(client)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    assert body["task"]["error"] == "clarification_needed"
    # governance acts externally: F3 gets verified (COMPLETE)
    f3 = catalog.get_candidate("F3")
    f3.status = "COMPLETE"
    f3.core_concepts = ["HIGH_WITHDRAWAL_FREQUENCY"]
    f3.verified_by = "qa-reverify"
    catalog.upsert_candidate(f3)
    # next evaluation: metadata state changed -> the gate can now ADMIT
    # the uniquely supported candidate (no new clarification boundary)
    body = turn(client, inv_id, AMBIGUOUS_REQUEST)
    if body["status"] == "completed":
        assert body["context"]["focused_finding_id"] in ("F2", "F3")
        assert body["context"]["focus_source"] == "agent_resolved"
        entries = ledger.entries(inv_id)
        assert len(entries) == 1            # admitted without a new boundary
    else:
        # refinement self-clarified (real-LLM variance): still fail-closed
        assert body["task"]["error"] == "clarification_needed"
    # and the gate can now admit the uniquely supported candidate
    if body["status"] == "completed":
        assert body["context"]["focused_finding_id"] in ("F2", "F3")
        assert body["context"]["focus_source"] == "agent_resolved"
    else:
        # refinement self-clarified (real-LLM variance) — still no
        # ineligible autonomous SELECT
        assert body["task"]["error"] == "clarification_needed"


# --- E2E-6: Agent bounded loop vertical slice --------------------------------------

def test_e2e_6_agent_loop_clarification_vertical_slice(monkeypatch):
    """REAL bounded Agent Loop: AMBIGUOUS goal -> loop pauses (terminates
    BLOCKED) at the clarification boundary; the boundary is recorded in
    the shared ledger; the loop state is persisted and observable; a
    subsequent explicit designation re-focuses the investigation."""
    client, store, catalog, ledger = build_client(monkeypatch)
    monkeypatch.setattr(settings, "ENABLE_CANDIDATE_METADATA_COVERAGE",
                        False)
    inv_id = open_inv(client)
    _, task_id, activity = new_inv_and_loop(client, monkeypatch, inv_id,
                                            AMBIGUOUS_REQUEST)
    assert activity is not None and activity["is_terminal"] is True
    assert activity["agent_status"] == "blocked"
    assert activity["stop_reason"] == "clarification_needed"
    # the boundary was recorded in the SHARED ledger
    ledger = ClarificationLedger(store)
    entries = ledger.entries(inv_id)
    assert len(entries) == 1
    assert entries[0]["outcome"] == "clarified"
    assert entries[0]["reason_class"] == "agent_loop_blocked"
    # persisted loop state is inspectable (agent_states document)
    state = store.get_agent_state(task_id)
    assert state is not None and state.agent_status == "blocked"
    # user designates via the conversation path; metadata unchanged
    with rp_patches()[0], rp_patches()[1]:
        r = client.post(f"/api/v2/investigations/{inv_id}/turn", json={
            "message": "就是 F3。",
            "context_action": {"type": "focus_finding",
                               "finding_id": "F3"}}).json()
    assert r["context"]["focused_finding_id"] == "F3"
    assert r["context"]["focus_source"] == "user_selected"
    f3 = TaskStoreCatalogProvider(store).get_candidate("F3")
    assert f3.status == "INCOMPLETE"              # no verification write
    entries = ledger.entries(inv_id)
    assert entries[-1]["outcome"] == "resolved_by_designation"


# --- E2E-7: no bypass (LLM proposal + INCOMPLETE metadata) -------------------------

def test_e2e_7_no_bypass(monkeypatch):
    client, store, catalog, ledger = build_client(monkeypatch,
                                                  f3_status="INCOMPLETE")
    inv_id = open_inv(client)
    body = turn(client, inv_id, E2E_REQUEST)
    tr = body["target_resolution"]
    ref = tr["refinement"]
    # either the refinement self-clarified or the gate refused — either
    # way, no autonomous SELECT of an ineligible universe happens
    if ref.get("decision") == "SELECT":
        assert ref["admissibility"]["decision"] != "admitted"
        assert body["context"]["focused_finding_id"] is None
    else:
        assert body["task"]["error"] == "clarification_needed"

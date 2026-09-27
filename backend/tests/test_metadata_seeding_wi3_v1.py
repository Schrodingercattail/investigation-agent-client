"""WP-M2-1 WI-3 — seed + verification utility tests.

LEVEL 1: seed-document conformance, registry conformance, COMPLETE
prerequisites, provenance requirements, no-LLM structural guard,
no-candidate-specific-code guard.
LEVEL 2: real temp TaskStoreV2 — seed 8 candidates (3 UNVERIFIED /
5 INCOMPLETE) + 6 concepts, verify transition, reopen persistence,
concept-reference resolution, existing tables unaffected.
"""

import json
import tempfile
from pathlib import Path

import pytest

from app.metadata_seeding import (
    DEFAULT_SEED_FILE,
    SeedConformanceError,
    seed_from_document,
    validate_seed_document,
    verify_candidate,
)
from app.candidate_metadata import TaskStoreCatalogProvider
from app.task_store_v2 import TaskStoreV2

REPO_SEED = Path(__file__).resolve().parents[1] / DEFAULT_SEED_FILE


def fresh_store_with_repo_seed():
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi3_test.db")
    doc = json.loads(REPO_SEED.read_text(encoding="utf-8"))
    summary = seed_from_document(store, doc)
    return store, doc, summary


# --- LEVEL 1: seed document conformance ---------------------------------------

def test_repo_seed_document_is_valid():
    doc = json.loads(REPO_SEED.read_text(encoding="utf-8"))
    assert validate_seed_document(doc) == []


def test_seed_document_rejects_missing_fields():
    bad = {"candidates": [{"candidate_id": "FX"}]}
    errors = validate_seed_document(bad)
    assert any("concept_registry" in e for e in errors)
    assert any("target_status" in e for e in errors)


def test_seed_document_rejects_unknown_target_status():
    doc = {"concept_registry": [], "candidates": [
        {"candidate_id": "X", "candidate_type": "finding",
         "core_concepts": [], "distinguishing_concepts": [],
         "potential_confusions": [], "target_status": "PENDING"}]}
    errors = validate_seed_document(doc)
    assert any("PENDING" in e for e in errors)   # no new states


def test_incomplete_target_requires_insufficiency_reason():
    doc = {"concept_registry": [], "candidates": [
        {"candidate_id": "X", "candidate_type": "finding",
         "core_concepts": [], "distinguishing_concepts": [],
         "potential_confusions": [], "target_status": "INCOMPLETE"}]}
    errors = validate_seed_document(doc)
    assert any("insufficiency_reason" in e for e in errors)


def test_unverified_target_requires_evidence_sources():
    doc = {"concept_registry": [], "candidates": [
        {"candidate_id": "X", "candidate_type": "finding",
         "core_concepts": ["A"], "distinguishing_concepts": ["A"],
         "potential_confusions": [], "target_status": "UNVERIFIED"}]}
    errors = validate_seed_document(doc)
    assert any("evidence.sources" in e for e in errors)


def test_no_llm_in_seeding_module():
    """AC-WI3-7 structural guard: the seeding/verification module must
    not import any LLM provider — no model output can produce COMPLETE
    metadata through this path."""
    src = Path(__file__).resolve().parents[1].joinpath(
        "app/metadata_seeding.py").read_text(encoding="utf-8")
    assert "llm_provider" not in src
    assert "ClaudeProvider" not in src


def test_no_candidate_specific_code_in_seeding_module():
    """AC-WI3-5 structural guard: candidate specifics live in the seed
    document, not in code."""
    src = Path(__file__).resolve().parents[1].joinpath(
        "app/metadata_seeding.py").read_text(encoding="utf-8")
    for literal in ('"F5"', '"F6"', '"F8"', '"F1"', "'F5'"):
        assert literal not in src, literal


# --- LEVEL 2: real-store seeding + verification --------------------------------

def test_seed_provisions_full_universe():
    store, doc, summary = fresh_store_with_repo_seed()
    assert summary.inventory_count == 8          # full live RP universe
    assert summary.coverage_complete             # scope A: no omission
    assert sorted(summary.candidates_seeded) == \
        ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"]
    assert len(summary.concepts_seeded) == 6
    statuses = {c.candidate_id: c.status
                for c in TaskStoreCatalogProvider(store).list_candidates()}
    assert statuses == {"F5": "UNVERIFIED", "F6": "UNVERIFIED",
                        "F8": "UNVERIFIED",
                        "F1": "INCOMPLETE", "F2": "INCOMPLETE",
                        "F3": "INCOMPLETE", "F4": "INCOMPLETE",
                        "F7": "INCOMPLETE"}


def test_seed_is_idempotent_and_never_overwrites():
    store, doc, _ = fresh_store_with_repo_seed()
    provider = TaskStoreCatalogProvider(store)
    verify_candidate(store, "F5", verified_by="qa")   # governed transition
    summary = seed_from_document(store, doc)          # re-run seed
    assert sorted(summary.candidates_already_present) == \
        ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"]
    assert provider.get_candidate("F5").status == "COMPLETE"  # protected


def test_verify_transitions_evidence_backed_candidates_to_complete():
    store, doc, _ = fresh_store_with_repo_seed()
    for cid in ("F5", "F6", "F8"):
        rec = verify_candidate(store, cid, verified_by="qa-seed-review")
        assert rec.status == "COMPLETE"
        assert rec.verified_by == "qa-seed-review"
        assert rec.metadata_version == 1
        assert rec.last_verified_at
        assert rec.provenance["verification"]["verified_by"] == \
            "qa-seed-review"


def test_verify_refuses_insufficient_evidence_candidates():
    store, doc, _ = fresh_store_with_repo_seed()
    for cid in ("F1", "F2", "F3", "F4", "F7"):
        with pytest.raises(SeedConformanceError) as e:
            verify_candidate(store, cid, verified_by="qa")
        assert any("no core concepts" in err or "status" in err
                   for err in e.value.errors)
        assert provider_status(store, cid) == "INCOMPLETE"   # stays honest


def provider_status(store, cid):
    return TaskStoreCatalogProvider(store).get_candidate(cid).status


def test_verify_requires_concept_registry_conformance():
    store, doc, _ = fresh_store_with_repo_seed()
    provider = TaskStoreCatalogProvider(store)
    # simulate an authored record referencing a concept outside the registry
    rec = provider.get_candidate("F5")
    rec.core_concepts = ["INVENTED_CONCEPT"]
    provider.upsert_candidate(rec)
    with pytest.raises(SeedConformanceError) as e:
        verify_candidate(store, "F5", verified_by="qa")
    assert any("INVENTED_CONCEPT" in err for err in e.value.errors)
    assert provider.get_candidate("F5").status == "UNVERIFIED"  # unchanged


def test_complete_concept_references_resolve_after_reopen():
    store, path = make_fresh_verified()
    reopened = TaskStoreV2(path)
    provider = TaskStoreCatalogProvider(reopened)
    registry = {c.concept_id for c in provider.list_concepts()}
    for rec in provider.list_candidates(status="COMPLETE"):
        for concept in rec.core_concepts + rec.distinguishing_concepts:
            assert concept in registry, (rec.candidate_id, concept)
    # existing tables still work after reopen
    assert reopened.list_investigations() is not None


def make_fresh_verified():
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi3_verified.db")
    doc = json.loads(REPO_SEED.read_text(encoding="utf-8"))
    seed_from_document(store, doc)
    for cid in ("F5", "F6", "F8"):
        verify_candidate(store, cid, verified_by="qa-seed-review")
    return store, store._db_path

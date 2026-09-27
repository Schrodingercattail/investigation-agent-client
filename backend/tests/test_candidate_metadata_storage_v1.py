"""WP-M2-1 WI-1 — Candidate Semantic Metadata storage layer tests.

LEVEL 1 (unit): status validation, JSON serialization round-trips,
proposal-only discipline of suggested_core_concepts.
LEVEL 2 (integration): real temp TaskStoreV2 SQLite — migration creates
the two new tables alongside the four existing ones, CRUD through the
TaskStoreCatalogProvider, persistence survives reopen, existing tables
keep working.

NOT in scope here (later WIs): coverage reconcile, normalization,
support evaluation, admissibility — asserted ABSENT by the
runtime-bypass tests.
"""

import sqlite3
import tempfile

import pytest

from app.candidate_metadata import (
    CANDIDATE_METADATA_STATUSES,
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
    ensure_candidate_metadata_schema,
    upsert_candidate,
    validate_candidate_status,
)
from app.task_store_v2 import TaskStoreV2


def make_store():
    path = tempfile.mkdtemp() + "/wi1_test.db"
    return TaskStoreV2(path), path


def full_record(candidate_id="F5", status="INCOMPLETE", **overrides):
    fields = dict(
        candidate_id=candidate_id,
        candidate_type="finding",
        case_scope="U00010",
        title_snapshot="Shared Device Relationships.",
        description_snapshot="1 shared device was used by this account.",
        content_hash="abc123",
        core_concepts=["SHARED_DEVICE_RELATIONSHIP"],
        distinguishing_concepts=["SHARED_DEVICE"],
        potential_confusions=["F6"],
        mechanism_concepts=[],
        suggested_core_concepts=["SOME_SUGGESTION"],
        status=status,
        metadata_version=0,
        owner="domain-owner",
        authored_by="author-1",
        verified_by=None,
        last_verified_at=None,
        provenance={"seed": False, "note": "test"},
    )
    fields.update(overrides)
    return CandidateMetadataRecord(**fields)


# --- LEVEL 1: unit ----------------------------------------------------------

def test_status_enum_is_closed():
    assert CANDIDATE_METADATA_STATUSES == (
        "INCOMPLETE", "UNVERIFIED", "COMPLETE", "DEPRECATED")
    for s in CANDIDATE_METADATA_STATUSES:
        assert validate_candidate_status(s) == s
    with pytest.raises(ValueError):
        validate_candidate_status("PENDING")          # no new states
    with pytest.raises(ValueError):
        validate_candidate_status("NEEDS_VERIFICATION")


def test_json_serialization_round_trip():
    store, _ = make_store()
    provider = TaskStoreCatalogProvider(store)
    provider.upsert_candidate(full_record())
    got = provider.get_candidate("F5")
    assert got.core_concepts == ["SHARED_DEVICE_RELATIONSHIP"]
    assert got.potential_confusions == ["F6"]
    assert got.provenance == {"seed": False, "note": "test"}
    assert got.status == "INCOMPLETE"
    assert got.metadata_version == 0


def test_suggested_concepts_are_proposal_only():
    """WI-1 invariant: suggestions must NOT change status, core_concepts,
    or eligibility. No implicit path may exist."""
    store, _ = make_store()
    provider = TaskStoreCatalogProvider(store)
    provider.upsert_candidate(full_record())
    rec = provider.get_candidate("F5")
    rec.suggested_core_concepts = ["LINKED_ACCOUNT_NETWORK"]
    provider.upsert_candidate(rec)
    got = provider.get_candidate("F5")
    assert got.suggested_core_concepts == ["LINKED_ACCOUNT_NETWORK"]
    assert got.status == "INCOMPLETE"                 # unchanged
    assert got.core_concepts == ["SHARED_DEVICE_RELATIONSHIP"]  # unchanged


def test_concept_registry_round_trip():
    store, _ = make_store()
    provider = TaskStoreCatalogProvider(store)
    provider.upsert_concept(ConceptRecord(
        concept_id="SHARED_DEVICE_RELATIONSHIP",
        definition="A relationship between accounts established by a "
                   "shared device.",
        created_by="seed"))
    got = provider.get_concept("SHARED_DEVICE_RELATIONSHIP")
    assert got is not None and got.status == "ACTIVE"
    assert provider.get_concept("NOPE") is None
    with pytest.raises(ValueError):
        provider.upsert_concept(ConceptRecord(
            concept_id="X", definition="d", status="MAYBE"))


# --- LEVEL 2: integration on the real TaskStoreV2 store ----------------------

def test_migration_creates_new_tables_alongside_existing():
    store, _ = make_store()
    with store.metadata_write() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tasks_v2", "investigations", "investigation_sessions",
            "agent_states"} <= tables, "existing tables must survive"
    assert {"candidate_metadata", "semantic_concepts"} <= tables


def test_status_constraint_enforced_at_sql_level():
    store, _ = make_store()
    with pytest.raises(sqlite3.IntegrityError):
        with store.metadata_write() as conn:
            conn.execute(
                "INSERT INTO candidate_metadata (candidate_id, "
                "candidate_type, status, created_at, updated_at) "
                "VALUES ('FBAD', 'finding', 'PENDING', 't', 't')")


def test_crud_persists_across_reopen_and_existing_tables_keep_working():
    store, path = make_store()
    provider = TaskStoreCatalogProvider(store)
    # existing store behavior first (pre-existing tables/data path)
    from app.models import Investigation, TaskV2
    inv = Investigation(investigation_id="INV-wi1", case_id="U00010")
    store.save_investigation(inv)
    store.create(TaskV2(task_id="TASK-wi1", investigation_id="INV-wi1",
                        user_request="u"))
    # metadata write
    provider.upsert_candidate(full_record())
    provider.upsert_concept(ConceptRecord(
        concept_id="SHARED_DEVICE_RELATIONSHIP", definition="d",
        created_by="t"))
    # reopen the SAME database file through a fresh store instance
    reopened = TaskStoreV2(path)
    rp = TaskStoreCatalogProvider(reopened)
    got = rp.get_candidate("F5")
    assert got is not None and got.status == "INCOMPLETE"
    assert got.core_concepts == ["SHARED_DEVICE_RELATIONSHIP"]
    assert got.owner == "domain-owner"
    assert rp.get_concept("SHARED_DEVICE_RELATIONSHIP") is not None
    # existing tables/data intact after reopen
    assert reopened.get_investigation("INV-wi1") is not None
    assert reopened.get("TASK-wi1") is not None
    # metadata update primitives (status + provenance/version fields)
    rp.update_status("F5", "COMPLETE", verified_by="qa-1",
                     last_verified_at="2026-09-24T00:00:00+00:00",
                     metadata_version=1,
                     provenance_update={"verified": True})
    final = rp.get_candidate("F5")
    assert final.status == "COMPLETE"
    assert final.verified_by == "qa-1"
    assert final.metadata_version == 1
    assert final.provenance["verified"] is True
    with pytest.raises(LookupError):
        rp.update_status("F999", "COMPLETE")


def test_list_by_status():
    store, _ = make_store()
    provider = TaskStoreCatalogProvider(store)
    provider.upsert_candidate(full_record(candidate_id="F5"))
    provider.upsert_candidate(full_record(
        candidate_id="F12", status="UNVERIFIED"))
    assert {c.candidate_id for c in provider.list_candidates()} == \
        {"F5", "F12"}
    assert [c.candidate_id for c in
            provider.list_candidates(status="UNVERIFIED")] == ["F12"]

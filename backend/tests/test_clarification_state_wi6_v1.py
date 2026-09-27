"""WP-M2-1 WI-6 — bounded clarification lifecycle tests.

LEVEL 1 (unit): ledger key semantics, first/repeat/terminal,
designation closure, metadata ineligibility after rewording.
LEVEL 2 (integration): real TaskStoreV2 — persist, reopen, resume,
designation, terminal persistence, existing tables unaffected.
"""

import sqlite3
import tempfile

from app.candidate_metadata import TaskStoreCatalogProvider
from app.clarification_state import (
    OUTCOME_CLARIFIED,
    OUTCOME_NON_SELECTABLE_TERMINAL,
    OUTCOME_RESOLVED_BY_DESIGNATION,
    ClarificationLedger,
    candidate_set_hash,
    metadata_status_hash,
    request_fingerprint,
)
from app.task_store_v2 import TaskStoreV2


def make_store():
    return ClarificationLedger(
        TaskStoreV2(tempfile.mkdtemp() + "/wi6_test.db"))


def test_key_functions_deterministic_and_sensitive():
    assert request_fingerprint("abc") == request_fingerprint("abc")
    assert request_fingerprint(" abc ") == request_fingerprint("abc")
    assert request_fingerprint("abc") != request_fingerprint("abd")
    assert candidate_set_hash(["F5", "F6"]) == \
        candidate_set_hash(["F6", "F5"])          # order-insensitive
    assert candidate_set_hash(["F5"]) != candidate_set_hash(["F5", "F6"])
    assert metadata_status_hash({"F5": "COMPLETE"}) != \
        metadata_status_hash({"F5": "INCOMPLETE"})
    assert metadata_status_hash({"F5": "missing"}) != \
        metadata_status_hash({"F5": "COMPLETE"})


def test_first_clarification_allowed_then_repeat_terminal():
    ledger = make_store()
    s1 = ledger.record_clarification("INV-1", "ambiguous request",
                                     ["F5", "F6"],
                                     reason_class="test")
    assert s1.terminal is False and s1.repeated is False
    s2 = ledger.record_clarification("INV-1", "ambiguous request",
                                     ["F5", "F6"])
    assert s2.terminal is True and s2.repeated is True   # bounded terminal
    s3 = ledger.record_clarification("INV-1", "ambiguous request",
                                     ["F5", "F6"])
    assert s3.terminal is True                    # stays terminal, no 3rd


def test_changed_request_fingerprint_is_meaningful():
    ledger = make_store()
    s1 = ledger.record_clarification("INV-1", "关联情况", ["F5", "F6"])
    assert s1.terminal is False
    s2 = ledger.record_clarification("INV-1", "共享设备的关联账户",
                                     ["F5", "F6"])
    assert s2.terminal is False                   # new key: allowed
    assert s2.request_fingerprint != s1.request_fingerprint


def test_changed_candidate_set_resets_protection():
    ledger = make_store()
    ledger.record_clarification("INV-1", "same words", ["F5", "F6"])
    s = ledger.record_clarification("INV-1", "same words", ["F5"])
    assert s.terminal is False                    # candidate set changed


def test_changed_metadata_status_resets_protection():
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi6_meta.db")
    cat = TaskStoreCatalogProvider(store)
    from app.candidate_metadata import CandidateMetadataRecord
    for cid in ("F5", "F6"):
        cat.upsert_candidate(CandidateMetadataRecord(
            candidate_id=cid, candidate_type="finding",
            status="INCOMPLETE"))
    ledger = ClarificationLedger(store)
    ledger.record_clarification("INV-1", "same words", ["F5", "F6"])
    # governed metadata state changes externally (re-verification)
    f5 = cat.get_candidate("F5")
    f5.status = "COMPLETE"
    cat.upsert_candidate(f5)
    s = ledger.record_clarification("INV-1", "same words", ["F5", "F6"])
    assert s.terminal is False                    # metadata state changed


def test_designation_closes_and_does_not_change_metadata():
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi6_des.db")
    cat = TaskStoreCatalogProvider(store)
    from app.candidate_metadata import CandidateMetadataRecord
    cat.upsert_candidate(CandidateMetadataRecord(
        candidate_id="F7", candidate_type="finding",
        status="INCOMPLETE", owner="t"))
    ledger = ClarificationLedger(store)
    ledger.record_clarification("INV-1", "ambiguous", ["F7"])
    closed = ledger.close_by_designation("INV-1", "F7")
    assert closed is not None
    f7 = cat.get_candidate("F7")
    assert f7.status == "INCOMPLETE"              # metadata untouched
    assert f7.verified_by is None                 # no verification write
    entries = ledger.entries("INV-1")
    assert entries[-1]["outcome"] == "resolved_by_designation"


def test_designation_without_open_entry_is_none():
    ledger = make_store()
    assert ledger.close_by_designation("INV-X", "F7") is None


def test_persistence_across_reopen():
    store, path = None, tempfile.mkdtemp() + "/wi6_persist.db"
    store = TaskStoreV2(path)
    ledger = ClarificationLedger(store)
    ledger.record_clarification("INV-P", "request", ["F5", "F6"])
    ledger.record_clarification("INV-P", "request", ["F5", "F6"])
    reopened = ClarificationLedger(TaskStoreV2(path))
    s = reopened.record_clarification("INV-P", "request", ["F5", "F6"])
    assert s.terminal is True                     # survives reopen
    entries = reopened.entries("INV-P")
    assert entries[-1]["outcome"] == "non_selectable_terminal"


def test_schema_uses_same_store_connection():
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi6_same.db")
    with store.metadata_write() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"clarify_ledger", "candidate_metadata", "semantic_concepts",
            "tasks_v2", "investigations", "investigation_sessions",
            "agent_states"} <= tables
    with store.metadata_write() as conn:
        assert conn is store._connection()        # single shared store


def test_sql_level_ledger_persistence():
    path = tempfile.mkdtemp() + "/wi6_sql.db"
    ledger = ClarificationLedger(TaskStoreV2(path))
    ledger.record_clarification("INV-S", "request", ["F5"])
    conn = sqlite3.connect(path)                  # raw external read
    n = conn.execute("SELECT COUNT(*) FROM clarify_ledger").fetchone()[0]
    assert n == 1

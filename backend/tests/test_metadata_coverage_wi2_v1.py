"""WP-M2-1 WI-2 — Coverage Check + auto-provisioning + invalidation.

LEVEL 1: reconcile logic over an in-memory CatalogProvider double
(no-op discipline, provisioning, invalidation, gone semantics, empty
fetch guard, DEPRECATED skip, generic iteration).
LEVEL 2: real TaskStoreV2 seeded with the WI-3 universe (F1–F8;
F5/F6/F8 COMPLETE) + reconcile against real Finding objects; F12
insertion; repeated idempotency; F5 semantic change; disappearance;
reopen persistence.
"""

import copy
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.candidate_metadata import (
    CandidateMetadataRecord,
    TaskStoreCatalogProvider,
    compute_content_hash,
    reconcile_coverage,
)
from app.metadata_seeding import seed_from_document, verify_candidate
from app.models import Finding, FindingCapability
from app.task_store_v2 import TaskStoreV2

REPO_SEED = Path(__file__).resolve().parents[1] / "seed" / \
    "candidate_metadata_seed.json"


# --- helpers -------------------------------------------------------------------

@dataclass
class FakeFinding:
    finding_id: str
    title: str
    summary: str
    case_id: str = "U00010"


class InMemoryCatalog:
    """CatalogProvider double over a dict (LEVEL 1)."""

    def __init__(self, records=None):
        self.records = {r.candidate_id: r for r in (records or [])}

    def upsert_candidate(self, record):
        self.records[record.candidate_id] = record
        return record

    def get_candidate(self, cid):
        return self.records.get(cid)

    def list_candidates(self, status=None):
        return [r for r in self.records.values()
                if status is None or r.status == status]

    def update_status(self, cid, status, **kw):
        self.records[cid].status = status
        return self.records[cid]

    def upsert_concept(self, concept):
        raise NotImplementedError

    def get_concept(self, cid):
        raise NotImplementedError

    def list_concepts(self, status=None):
        raise NotImplementedError


def make_finding(fid, title, summary):
    return FakeFinding(finding_id=fid, title=title, summary=summary)


def live_universe():
    """Findings byte-identical to the live RP universe — built from the
    seed document's snapshots (which were extracted programmatically from
    live RP at seed time), so content hashes line up exactly."""
    doc = json.loads(REPO_SEED.read_text(encoding="utf-8"))
    return [FakeFinding(c["candidate_id"], c["title_snapshot"],
                        c["description_snapshot"])
            for c in doc["candidates"]]


def seed_live_universe_verified(store):
    """Seed the real store with the WI-3 universe; F5/F6/F8 verified."""
    doc = json.loads(REPO_SEED.read_text(encoding="utf-8"))
    seed_from_document(store, doc)
    for cid in ("F5", "F6", "F8"):
        verify_candidate(store, cid, verified_by="seed-review")
    return store


# --- LEVEL 1: reconcile logic ---------------------------------------------------

def test_content_hash_deterministic_and_sensitive():
    h1 = compute_content_hash("T", "D")
    assert h1 == compute_content_hash("T", "D")
    assert h1 != compute_content_hash("T", "D2")
    assert h1 != compute_content_hash("T2", "D")


def test_new_candidate_is_provisioned_incomplete():
    provider = InMemoryCatalog()
    findings = [make_finding("F12", "New Finding",
                             "A brand-new RP finding.")]
    report = reconcile_coverage(provider, findings)
    assert report.provisioned == ["F12"]
    rec = provider.records["F12"]
    assert rec.status == "INCOMPLETE"                 # never COMPLETE
    assert rec.metadata_version == 0
    assert rec.core_concepts == []                    # never auto-filled
    assert rec.provenance["auto_provisioned"] is True
    assert rec.content_hash == compute_content_hash(
        "New Finding", "A brand-new RP finding.")


def test_unchanged_candidate_is_noop():
    provider = InMemoryCatalog()
    findings = live_universe()
    reconcile_coverage(provider, findings)
    snapshots = {cid: copy.deepcopy(r)
                 for cid, r in provider.records.items()}
    report = reconcile_coverage(provider, findings)
    assert report.unchanged == [f.finding_id for f in findings]
    for f in findings:
        assert provider.records[f.finding_id] == snapshots[f.finding_id]


def test_content_change_invalidates_governed_candidate():
    provider = InMemoryCatalog()
    reconcile_coverage(provider, live_universe())
    assert provider.records["F5"].status == "INCOMPLETE"
    # govern it (as the WI-3 lifecycle would): author concepts, then
    # transition to COMPLETE with a version
    governed = provider.records["F5"]
    governed.status = "COMPLETE"
    governed.core_concepts = ["SHARED_DEVICE_RELATIONSHIP"]
    governed.distinguishing_concepts = ["SHARED_DEVICE"]
    governed.metadata_version = 2
    provider.upsert_candidate(governed)
    old_hash = provider.records["F5"].content_hash
    changed = live_universe()
    next(f for f in changed if f.finding_id == "F5").summary = \
        "2 shared devices were used by this account."
    report = reconcile_coverage(provider, changed)
    assert report.invalidated == ["F5"]
    rec = provider.records["F5"]
    assert rec.status == "UNVERIFIED"
    assert rec.metadata_version == 3                  # version increment
    assert rec.core_concepts == ["SHARED_DEVICE_RELATIONSHIP"]  # preserved
    inv = rec.provenance["invalidation"]
    assert inv["old_hash"] == old_hash and inv["new_hash"] != old_hash


def test_gone_detection_once_then_known_then_reinstated():
    provider = InMemoryCatalog()
    universe = live_universe()
    reconcile_coverage(provider, universe)
    without_f8 = [f for f in universe if f.finding_id != "F8"]
    report = reconcile_coverage(provider, without_f8)      # F8 disappears
    assert report.gone == ["F8"]
    assert provider.records["F8"].status != "DEPRECATED"   # never auto
    assert provider.records["F8"].provenance["candidate_gone"][
        "detected_at"]
    report = reconcile_coverage(provider, without_f8)      # still gone
    assert report.gone_known == ["F8"] and report.gone == []
    report = reconcile_coverage(provider, universe)        # returns
    assert report.reinstated == ["F8"]


def test_empty_fetch_is_skipped_not_read_as_gone():
    provider = InMemoryCatalog()
    reconcile_coverage(provider, live_universe())
    report = reconcile_coverage(provider, [])
    assert report.skipped_empty_fetch is True
    assert report.gone == [] and report.gone_known == []
    assert len(provider.records) == 8


def test_deprecated_candidates_are_skipped():
    provider = InMemoryCatalog()
    reconcile_coverage(provider, live_universe())
    provider.records["F3"].status = "DEPRECATED"
    report = reconcile_coverage(provider, live_universe())
    assert "F3" in report.skipped_deprecated
    before = provider.records["F3"]
    reconcile_coverage(provider, live_universe()[:7])      # F3 disappears
    assert provider.records["F3"] == before                # untouched


def test_incomplete_content_change_refreshes_without_version_bump():
    provider = InMemoryCatalog()
    reconcile_coverage(provider, live_universe())
    changed = live_universe()
    next(f for f in changed if f.finding_id == "F1").summary = \
        "96.00/100; rescored system signal."
    report = reconcile_coverage(provider, changed)
    assert report.refreshed == ["F1"]
    rec = provider.records["F1"]
    assert rec.status == "INCOMPLETE"                 # no invalidation
    assert rec.metadata_version == 0                  # no version bump
    assert rec.provenance["last_content_change"]


# --- LEVEL 2: real TaskStoreV2 integration --------------------------------------

def seeded_store():
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi2_test.db")
    doc = json.loads(REPO_SEED.read_text(encoding="utf-8"))
    seed_from_document(store, doc)
    for cid in ("F5", "F6", "F8", "F3", "F7"):  # F3/F7 authored
        verify_candidate(store, cid, verified_by="seed-review")
    return store


def test_real_store_reconcile_idempotent_and_preserves_complete():
    store = seeded_store()
    provider = TaskStoreCatalogProvider(store)
    findings = live_universe()
    r1 = reconcile_coverage(provider, findings)
    assert r1.provisioned == []                        # WI-3 seeded 8/8
    assert r1.gone == []
    statuses = {c.candidate_id: c.status
                for c in provider.list_candidates()}
    assert statuses["F5"] == "COMPLETE"
    assert statuses["F6"] == "COMPLETE"
    assert statuses["F8"] == "COMPLETE"
    assert statuses["F3"] == "COMPLETE"                # AL-2.8 authored
    assert statuses["F7"] == "COMPLETE"                # AL-2.9 authored
    assert sum(1 for s in statuses.values() if s == "INCOMPLETE") == 3
    r2 = reconcile_coverage(provider, findings)        # idempotent
    assert r2.unchanged == [f.finding_id for f in findings]
    assert provider.get_candidate("F5").status == "COMPLETE"


def test_real_store_f12_insertion_and_semantic_change():
    store = seeded_store()
    provider = TaskStoreCatalogProvider(store)
    findings = live_universe() + [
        FakeFinding("F12", "Unusual Login Pattern.",
                    "Login attempts from three countries within one "
                    "hour.")]
    report = reconcile_coverage(provider, findings)
    assert report.provisioned == ["F12"]
    assert provider.get_candidate("F12").status == "INCOMPLETE"
    assert provider.get_candidate("F12").core_concepts == []
    # semantic change on a verified candidate
    changed = live_universe()
    next(f for f in changed if f.finding_id == "F8").summary = (
        "An opposite-trade ratio of 55% was observed, above the 40% "
        "threshold.")
    report = reconcile_coverage(provider, changed)
    assert report.invalidated == ["F8"]
    rec = provider.get_candidate("F8")
    assert rec.status == "UNVERIFIED"
    assert rec.metadata_version == 2   # seed v1 + one invalidation
    assert rec.core_concepts == ["OPPOSITE_TRADE_RATIO"]   # preserved
    # reopen persistence
    reopened = TaskStoreCatalogProvider(TaskStoreV2(store._db_path))
    assert reopened.get_candidate("F12").status == "INCOMPLETE"
    assert reopened.get_candidate("F8").status == "UNVERIFIED"


def test_real_store_candidate_gone_and_reappearance():
    store = seeded_store()
    provider = TaskStoreCatalogProvider(store)
    reconcile_coverage(provider, live_universe())          # provision 8/8
    assert provider.get_candidate("F8").status == "COMPLETE"
    without_f8 = [f for f in live_universe() if f.finding_id != "F8"]
    reconcile_coverage(provider, without_f8)               # F8 disappears
    assert provider.get_candidate("F8").provenance["candidate_gone"]
    assert provider.get_candidate("F8").status == "COMPLETE"  # not deprecated
    reconcile_coverage(provider, live_universe())          # returns
    assert "candidate_gone_reinstated_at" in (
        provider.get_candidate("F8").provenance)

"""WP-M2-1 WI-7 — telemetry observability tests.

LEVEL 1: event schema, prohibited-content absence, sink failure
isolation, Noop default.
LEVEL 2: REAL runtime transitions emit the events (reconcile,
support evaluation, admissibility, clarification lifecycle,
designation) — events come from state transitions, not from direct
helper calls.
"""

import json
import tempfile

import pytest

from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
)
from app.clarification_state import ClarificationLedger
from app.task_store_v2 import TaskStoreV2
from app.telemetry import emit_event
from app.semantic_resolution import SemanticNormalizer, SupportEvaluator
from app.telemetry import (
    InMemoryTelemetrySink,
    NoopTelemetrySink,
    set_sink,
)

from app.admissibility import SafeSelectGate
from tests.test_admissibility_gate_wi5_v1 import InMemoryCatalog, \
    ScriptedNormalizerLLM, governed
from tests.test_metadata_coverage_wi2_v1 import live_universe


class RaisingSink:
    def record(self, event):
        raise RuntimeError("sink down")


def events_for(fn):
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        fn()
    finally:
        pass  # caller restores
    return sink, [e for e in sink.events]


# --- LEVEL 1: schema + isolation -------------------------------------------------

def test_telemetry_failure_isolation():
    """A raising sink can never break the caller (existing convention,
    verified for the new emit_event helper)."""
    from app.telemetry import emit_event

    class Boom:
        def record(self, event):
            raise RuntimeError("sink down")

    set_sink(Boom())
    try:
        emit_event("candidate.metadata.provisioned", candidate_id="F5")
        emit_event("support.evaluated", summary=None)
    finally:
        set_sink(NoopTelemetrySink())


def test_noop_default_swallows_and_baseline_unaffected():
    set_sink(NoopTelemetrySink())
    from app.telemetry import emit_event
    emit_event("anything.at.all", candidate_id="X")   # must not raise


def test_event_schema_required_fields_and_prohibited_content():
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        from app.telemetry import emit_event
        emit_event("candidate.metadata.provisioned",
                   candidate_id="F5", candidate_type="finding",
                   status="INCOMPLETE", metadata_version=0,
                   content_hash="abc", reason_class="auto_provisioned")
        e = sink.events[0]
        assert e.event_type == "candidate.metadata.provisioned"
        assert e.candidate_id == "F5"
        assert e.reason_class == "auto_provisioned"
        parsed = e.model_dump()
        for prohibited in ("prompt", "messages", "chain_of_thought",
                           "reasoning"):
            assert prohibited not in parsed
            assert parsed.get(prohibited) in (None, [], {})
    finally:
        set_sink(NoopTelemetrySink())


# --- LEVEL 2: real transitions emit real events ----------------------------------

def real_setup_with_sink(f3_status="INCOMPLETE"):
    """Real store seeded with the WI-3-style fixture universe + a real
    gate; returns the pieces + the attached in-memory sink."""
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi7_test.db")
    catalog = TaskStoreCatalogProvider(store)
    for concept in ("COORDINATED_TRADING_PATTERN",
                    "HIGH_WITHDRAWAL_FREQUENCY"):
        catalog.upsert_concept(__import__("app.candidate_metadata",
                                          fromlist=["ConceptRecord"])
                               .ConceptRecord(
                                   concept_id=concept,
                                   definition=f"Fixture {concept}.",
                                   created_by="t"))
    catalog.upsert_candidate(CandidateMetadataRecord(
        candidate_id="F2", candidate_type="finding", status="COMPLETE",
        core_concepts=["COORDINATED_TRADING_PATTERN"],
        distinguishing_concepts=["COORDINATED_TRADING_PATTERN"],
        potential_confusions=["F3"], owner="t", authored_by="t",
        verified_by="review", provenance={"fixture": True}))
    catalog.upsert_candidate(CandidateMetadataRecord(
        candidate_id="F3", candidate_type="finding", status=f3_status,
        core_concepts=(["HIGH_WITHDRAWAL_FREQUENCY"]
                       if f3_status == "COMPLETE" else []),
        distinguishing_concepts=(["HIGH_WITHDRAWAL_FREQUENCY"]
                                 if f3_status == "COMPLETE" else []),
        potential_confusions=["F2"], owner="t", authored_by="t",
        provenance={"fixture": True}))
    return store, catalog, sink


def test_wi7_reconcile_emits_provisioned_and_coverage():
    from app.candidate_metadata import reconcile_coverage
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        store = TaskStoreV2(tempfile.mkdtemp() + "/wi7_rec.db")
        provider = TaskStoreCatalogProvider(store)
        report = reconcile_coverage(provider, live_universe())
    finally:
        set_sink(NoopTelemetrySink())
    kinds = [e.event_type for e in sink.events]
    assert "candidate.metadata.provisioned" in kinds
    assert "candidate.metadata.coverage" in kinds
    cov = next(e for e in sink.events
               if e.event_type == "candidate.metadata.coverage")
    assert cov.coverage_counts["candidates_seen"] == 8
    assert cov.coverage_counts["provisioned"] == 8


def test_wi7_invalidation_emits_event():
    from app.candidate_metadata import reconcile_coverage
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        store = TaskStoreV2(tempfile.mkdtemp() + "/wi7_inv.db")
        provider = TaskStoreCatalogProvider(store)
        reconcile_coverage(provider, live_universe())
        f5 = provider.get_candidate("F5")
        f5.status = "COMPLETE"
        provider.upsert_candidate(f5)
        changed = live_universe()
        for f in changed:
            if f.finding_id == "F5":
                f.summary = f.summary + " (updated)"
        report = reconcile_coverage(provider, changed)
    finally:
        set_sink(NoopTelemetrySink())
    assert report.invalidated == ["F5"]
    kinds = [e.event_type for e in sink.events]
    assert "candidate.metadata.invalidated" in kinds
    evt = next(e for e in sink.events
               if e.event_type == "candidate.metadata.invalidated")
    assert evt.candidate_id == "F5"
    assert evt.reason_class == "semantic_source_changed"
    assert "status COMPLETE -> UNVERIFIED" in (evt.summary or "")


def test_wi7_support_and_admissibility_events():
    from app.admissibility import SafeSelectGate
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        store = TaskStoreV2(tempfile.mkdtemp() + "/wi7_adm.db")
        catalog = TaskStoreCatalogProvider(store)
        for r in [governed("FA"), governed("FB", core="CONCEPT_Y")]:
            catalog.upsert_candidate(r)
        for concept in ("CONCEPT_X", "CONCEPT_Y"):
            catalog.upsert_concept(__import__("app.candidate_metadata",
                                              fromlist=["ConceptRecord"])
                                   .ConceptRecord(
                                       concept_id=concept,
                                       definition="d",
                                       created_by="t"))

        class LLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return json.dumps({"concept_id": "CONCEPT_X",
                                   "matched_fragment": "X",
                                   "confidence": 0.9})

        gate = SafeSelectGate(catalog_provider=catalog,
                              normalizer_llm=LLM(), min_confidence=0.7)
        decision = gate.decide(request_text="request X",
                               candidate_ids=["FA", "FB"],
                               proposed_target="FA", confidence=0.9)
        assert decision.admitted is True
    finally:
        set_sink(NoopTelemetrySink())
    kinds = [e.event_type for e in sink.events]
    assert "support.evaluated" in kinds
    assert "admissibility.decided" in kinds
    decided = next(e for e in sink.events
                   if e.event_type == "admissibility.decided")
    assert decided.decision == "ADMIT"
    assert decided.reason_class == "admitted"


def test_wi7_support_unknown_event_when_ineligible():
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        store = TaskStoreV2(tempfile.mkdtemp() + "/wi7_unk.db")
        catalog = TaskStoreCatalogProvider(store)
        catalog.upsert_candidate(__import__("app.candidate_metadata",
                                            fromlist=[
                                                "CandidateMetadataRecord"])
                                 .CandidateMetadataRecord(
                                     candidate_id="FA",
                                     candidate_type="finding",
                                     status="COMPLETE",
                                     core_concepts=["CONCEPT_X"],
                                     distinguishing_concepts=["CONCEPT_X"]))
        catalog.upsert_candidate(__import__("app.candidate_metadata",
                                            fromlist=[
                                                "CandidateMetadataRecord"])
                                 .CandidateMetadataRecord(
                                     candidate_id="FB",
                                     candidate_type="finding",
                                     status="INCOMPLETE"))
        for concept in ("CONCEPT_X",):
            catalog.upsert_concept(__import__("app.candidate_metadata",
                                              fromlist=["ConceptRecord"])
                                   .ConceptRecord(
                                       concept_id=concept,
                                       definition="d", created_by="t"))

        class LLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return json.dumps({"concept_id": "CONCEPT_X",
                                   "matched_fragment": "X",
                                   "confidence": 0.9})

        gate = SafeSelectGate(catalog_provider=catalog,
                              normalizer_llm=LLM(), min_confidence=0.7)
        decision = gate.decide(request_text="request X",
                               candidate_ids=["FA", "FB"],
                               proposed_target="FA", confidence=0.9)
        assert decision.admitted is False
    finally:
        set_sink(NoopTelemetrySink())
    # eligibility failure short-circuits BEFORE support evaluation, so
    # there is intentionally NO support.evaluated for this early-reject
    # path; the admissibility decision event carries the metadata reason.
    evaluated = [e for e in sink.events
                 if e.event_type == "support.evaluated"]
    assert evaluated == []
    decided = [e for e in sink.events
               if e.event_type == "admissibility.decided"]
    assert decided and decided[-1].decision == "REJECT"
    assert decided[-1].reason_class == "metadata_incomplete"
    assert decided[-1].summary and '"candidate_ids"' in decided[-1].summary


def test_wi7_clarification_and_designation_events():
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi7_clar.db")
    sink = InMemoryTelemetrySink()
    set_sink(sink)
    try:
        ledger = ClarificationLedger(store)
        # J1: first clarification -> opened
        ledger.record_clarification("INV-1", "ambiguous", ["F5", "F6"],
                                    reason_class="ambiguous_resolution")
        # J2: same key repeated -> bounded terminal (no third clarify)
        ledger.record_clarification("INV-1", "ambiguous", ["F5", "F6"])
        # J3: fresh key -> open; then explicit designation resolves it
        ledger.record_clarification("INV-1", "rephrased request",
                                    ["F5", "F6"])
        ledger.close_by_designation("INV-1", "F5")
    finally:
        set_sink(NoopTelemetrySink())
    kinds = [e.event_type for e in sink.events]
    assert "clarification.opened" in kinds
    assert "clarification.terminal" in kinds
    assert "clarification.resolved" in kinds
    assert "user.designated_candidate" in kinds
    opened = next(e for e in sink.events
                  if e.event_type == "clarification.opened")
    assert opened.request_fingerprint
    designated = next(e for e in sink.events
                      if e.event_type == "user.designated_candidate")
    assert designated.candidate_id == "F5"
    assert designated.reason_class == "explicit_focus"
    # no metadata verification semantics in the designation event
    raw = designated.model_dump_json()
    assert "verified_by" not in raw and "COMPLETE" not in raw

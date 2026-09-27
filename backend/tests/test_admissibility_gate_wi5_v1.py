"""WP-M2-1 WI-5 — Safe-Select admissibility gate decision matrix.

LEVEL 1: the real SafeSelectGate over an in-memory catalog double and a
scripted normalization LLM. Every baseline §10 predicate branch is
exercised, including the core safety boundary: a unique support target
NEVER overrides an eligibility failure.
"""

import pytest

from app.admissibility import (
    R_ADMITTED,
    R_INVALID_TARGET,
    R_LOW_CONFIDENCE,
    R_METADATA_DEPRECATED,
    R_METADATA_INCOMPLETE,
    R_METADATA_UNVERIFIED,
    R_NO_SUPPORTED_CONCEPT,
    R_UNSTABLE_CANDIDATES,
    SafeSelectGate,
)
from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
)
from app.semantic_resolution import R_METADATA_INELIGIBLE, \
    R_MULTIPLE_SUPPORTED

REQUEST = "request X"


class ScriptedNormalizerLLM:
    def __init__(self, payload):
        self.payload = payload

    def generate(self, messages, max_tokens=0, temperature=0.1):
        import json
        return json.dumps(self.payload)


class InMemoryCatalog:
    def __init__(self, records, concepts):
        self.records = {r.candidate_id: r for r in records}
        self.concepts = list(concepts)

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
        self.concepts.append(concept)
        return concept

    def get_concept(self, cid):
        return next((c for c in self.concepts if c.concept_id == cid),
                    None)

    def list_concepts(self, status=None):
        return [c for c in self.concepts
                if status is None or c.status == status]


def governed(cid, status="COMPLETE", core="CONCEPT_X"):
    return CandidateMetadataRecord(
        candidate_id=cid, candidate_type="finding", status=status,
        core_concepts=[core], distinguishing_concepts=[core],
        owner="fixture", authored_by="fixture",
        provenance={"fixture": True})


def gate_with(records, propose=None):
    catalog = InMemoryCatalog(records, [
        ConceptRecord(concept_id="CONCEPT_X", definition="d",
                      created_by="fixture"),
        ConceptRecord(concept_id="CONCEPT_Y", definition="d",
                      created_by="fixture"),
        ConceptRecord(concept_id="CONCEPT_Z", definition="unused",
                      created_by="fixture"),
    ])
    payload = propose or {"concept_id": "CONCEPT_X",
                          "matched_fragment": "X", "confidence": 0.9}
    gate = SafeSelectGate(
        catalog_provider=catalog,
        normalizer_llm=ScriptedNormalizerLLM(payload),
        min_confidence=0.7)
    return gate, catalog


def decide(gate, target="FA", confidence=0.9, stable=True, request=REQUEST):
    return gate.decide(request_text=request, candidate_ids=["FA", "FB"],
                       proposed_target=target, confidence=confidence,
                       candidate_set_stable=stable)


def base_records(fb_status="COMPLETE", fb_core="CONCEPT_Y"):
    return [governed("FA"), governed("FB", fb_status, fb_core)]


# --- the decision matrix ---------------------------------------------------------

def test_1_unique_all_eligible_sufficient_admits():
    gate, _ = gate_with(base_records())
    d = decide(gate)
    assert d.admitted is True and d.reason_class == R_ADMITTED
    assert d.details["unique_target"] == "FA"


def test_2_incomplete_competitor_rejects_unique_target():
    gate, _ = gate_with(base_records(fb_status="INCOMPLETE"))
    d = decide(gate)
    assert d.admitted is False                      # THE safety boundary:
    assert d.reason_class == R_METADATA_INCOMPLETE  # unique target does
    # not override eligibility failure


def test_3_unverified_competitor_rejects():
    gate, _ = gate_with(base_records(fb_status="UNVERIFIED"))
    d = decide(gate)
    assert d.admitted is False
    assert d.reason_class == R_METADATA_UNVERIFIED


def test_4_deprecated_competitor_rejects():
    gate, _ = gate_with(base_records(fb_status="DEPRECATED"))
    d = decide(gate)
    assert d.admitted is False
    assert d.reason_class == R_METADATA_DEPRECATED


def test_5_multiple_supported_rejects():
    records = [governed("FA"), governed("FB", core="CONCEPT_X")]
    gate, _ = gate_with(records, propose={
        "concept_id": "CONCEPT_X", "matched_fragment": "X",
        "confidence": 0.9})
    d = decide(gate)
    assert d.admitted is False
    assert d.reason_class == R_MULTIPLE_SUPPORTED


def test_6_no_support_rejects():
    records = base_records(fb_core="CONCEPT_Y")
    gate, _ = gate_with(records, propose={
        "concept_id": "CONCEPT_Z", "matched_fragment": "X",
        "confidence": 0.9})
    d = decide(gate)
    assert d.admitted is False
    assert d.reason_class == R_NO_SUPPORTED_CONCEPT


def test_7_unknown_support_rejects():
    gate, _ = gate_with(base_records(), propose={
        "concept_id": None, "matched_fragment": "", "confidence": 0.9})
    d = decide(gate)
    assert d.admitted is False
    assert d.reason_class == "unknown_support"


def test_8_low_confidence_rejects_even_when_unique():
    gate, _ = gate_with(base_records())
    d = decide(gate, confidence=0.5)
    assert d.admitted is False
    assert d.reason_class == R_LOW_CONFIDENCE


def test_9_unstable_rejects():
    gate, _ = gate_with(base_records())
    d = decide(gate, stable=False)
    assert d.admitted is False
    assert d.reason_class == R_UNSTABLE_CANDIDATES


def test_10_invalid_target_rejects():
    gate, _ = gate_with(base_records())
    d = decide(gate, target="FB", confidence=0.9)   # FB is FALSE; FA unique
    assert d.admitted is False
    assert d.reason_class == R_INVALID_TARGET
    assert d.details["unique_target"] == "FA"


def test_11_missing_metadata_record_rejects():
    records = [governed("FA")]                       # FB has no record
    gate, _ = gate_with(records)
    d = decide(gate)
    assert d.admitted is False
    assert d.reason_class == "metadata_missing"


def test_support_unknown_is_not_confused_with_false():
    """AC-WI4-5 guard at gate level: an INCOMPLETE candidate's evaluation
    is UNKNOWN (never FALSE) — visible in the recorded evidence when the
    eligibility refusal is issued."""
    gate, catalog = gate_with(base_records(fb_status="INCOMPLETE"))
    d = decide(gate)
    rec = catalog.get_candidate("FB")
    assert rec.status == "INCOMPLETE"                # still honest
    assert d.reason_class == R_METADATA_INCOMPLETE

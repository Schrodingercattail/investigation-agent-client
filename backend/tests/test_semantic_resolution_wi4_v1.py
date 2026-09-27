"""WP-M2-1 WI-4 — semantic normalization + SupportEvaluator tests.

LEVEL 1 (unit, scripted/fake LLM): registry conformance, grounding,
malformed output, LLM failure, deterministic core mapping, set predicate
combinations, confidence-not-support, no-keyword-path.
LEVEL 2 (integration, REAL TaskStoreV2 + REAL WI-3 seed + REAL
registry): normalization and support evaluation over the actual
governed F1–F8 metadata.
"""

import json
import tempfile
from pathlib import Path

import pytest

from app.candidate_metadata import (
    CandidateMetadataRecord,
    ConceptRecord,
    TaskStoreCatalogProvider,
)
from app.metadata_seeding import seed_from_document, verify_candidate
from app.semantic_resolution import (
    R_LLM_UNAVAILABLE,
    R_MALFORMED_OUTPUT,
    R_METADATA_INELIGIBLE,
    R_MULTIPLE_SUPPORTED,
    R_NO_SUPPORTED_CONCEPT,
    R_NORMALIZATION_INVALID_CONCEPT,
    R_NORMALIZATION_UNGROUNDED,
    R_UNIQUE_SUPPORT,
    R_UNKNOWN_SUPPORT,
    SemanticNormalizer,
    SupportEvaluator,
)
from app.task_store_v2 import TaskStoreV2

REPO_SEED = Path(__file__).resolve().parents[1] / "seed" / \
    "candidate_metadata_seed.json"

REQUEST_F5 = "有哪些账户和这个账户共享了设备？"
FRAGMENT_F5 = "共享了设备"


class FakeLLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def generate(self, messages, max_tokens=0, temperature=0.1):
        self.calls += 1
        if self.error:
            raise self.error
        if isinstance(self.payload, str):
            return self.payload                # raw (possibly malformed)
        return json.dumps(self.payload)


def OK(concept, matched_fragment=FRAGMENT_F5, confidence=0.9):
    return {"concept_id": concept, "matched_fragment": matched_fragment,
            "confidence": confidence}


def governed_records():
    """Governed records mirroring the WI-3 real seed: F5/F6/F8 COMPLETE
    with real concepts; F1/F2/F3/F4/F7 INCOMPLETE with empty cores."""
    store = TaskStoreV2(tempfile.mkdtemp() + "/wi4_test.db")
    doc = json.loads(REPO_SEED.read_text(encoding="utf-8"))
    seed_from_document(store, doc)
    for cid in ("F5", "F6", "F8"):
        verify_candidate(store, cid, verified_by="seed-review")
    provider = TaskStoreCatalogProvider(store)
    return (provider.list_candidates(),
            provider.list_concepts(status="ACTIVE"))


def evaluator(llm_payload=None, llm_error=None):
    llm = FakeLLM(payload=llm_payload, error=llm_error)
    candidates, concepts = governed_records()
    return SupportEvaluator(SemanticNormalizer(llm)).evaluate(
        REQUEST_F5, candidates, concepts), llm


# --- LEVEL 1: normalization + deterministic mapping -----------------------------

def test_registry_concept_mapped_to_core_is_true_and_unique():
    result, _ = evaluator(OK("SHARED_DEVICE_RELATIONSHIP"))
    assert result.per_candidate["F5"]["support"] == "TRUE"
    assert result.per_candidate["F6"]["support"] == "FALSE"
    assert result.per_candidate["F8"]["support"] == "FALSE"
    assert result.unique is True
    assert result.unique_target == "F5"
    assert result.reason_class == R_UNIQUE_SUPPORT


def test_invented_concept_is_rejected_as_unknown():
    result, llm = evaluator(OK("INVENTED_CONCEPT", "共享了设备",
                               confidence=0.99))
    assert result.per_candidate["F5"]["support"] == "UNKNOWN"
    assert result.reason_class == R_UNKNOWN_SUPPORT
    assert result.normalization["reason_class"] == \
        R_NORMALIZATION_INVALID_CONCEPT
    assert result.normalization["llm_confidence"] == 0.99  # provenance only


def test_ungrounded_fragment_is_rejected_as_unknown():
    result, _ = evaluator(OK("SHARED_DEVICE_RELATIONSHIP",
                             "this sentence is not in the request"))
    assert result.reason_class == R_UNKNOWN_SUPPORT
    assert result.normalization["reason_class"] == \
        R_NORMALIZATION_UNGROUNDED


def test_llm_failure_degrades_to_unknown():
    result, llm = evaluator(llm_error=RuntimeError("no text block"))
    assert result.reason_class == R_UNKNOWN_SUPPORT
    assert result.normalization["reason_class"] == R_LLM_UNAVAILABLE
    assert llm.calls == 1                        # no retry storm


def test_malformed_llm_output_degrades_to_unknown():
    result, _ = evaluator(llm_payload="not json at all")
    assert result.reason_class == R_UNKNOWN_SUPPORT
    assert result.normalization["reason_class"] == R_MALFORMED_OUTPUT


def test_ineligible_candidate_is_unknown_even_if_concept_maps():
    result, _ = evaluator(OK("SHARED_DEVICE_RELATIONSHIP"))
    # F1/F2/F3/F4/F7 are INCOMPLETE: UNKNOWN regardless of wording
    for cid in ("F1", "F2", "F3", "F4", "F7"):
        assert result.per_candidate[cid]["support"] == "UNKNOWN"
        assert result.per_candidate[cid]["reason"] == R_METADATA_INELIGIBLE


def test_keyword_similarity_alone_cannot_produce_true():
    """Request literally contains candidate titles, but the normalized
    governed concept is not in any candidate's core -> FALSE, never
    TRUE (AC-WI4-7)."""
    result, _ = evaluator(OK("OPPOSITE_SIDE_TRADING",
                             matched_fragment="共享了设备"))
    for cid in ("F5", "F6", "F8"):
        assert result.per_candidate[cid]["support"] == "FALSE"
    assert result.reason_class == R_NO_SUPPORTED_CONCEPT


def test_multiple_true_is_not_unique():
    candidates = [
        CandidateMetadataRecord(
            candidate_id="FA", candidate_type="finding",
            status="COMPLETE", core_concepts=["CONCEPT_X"]),
        CandidateMetadataRecord(
            candidate_id="FB", candidate_type="finding",
            status="COMPLETE", core_concepts=["CONCEPT_X"]),
    ]
    concepts = [ConceptRecord(concept_id="CONCEPT_X", definition="d")]
    result = SupportEvaluator(SemanticNormalizer(FakeLLM(OK(
        "CONCEPT_X", matched_fragment="X")))) \
        .evaluate("request X", candidates, concepts)
    assert result.unique is False
    assert result.reason_class == R_MULTIPLE_SUPPORTED


def test_no_true_is_no_supported_concept():
    candidates = [
        CandidateMetadataRecord(
            candidate_id="FA", candidate_type="finding",
            status="COMPLETE", core_concepts=["CONCEPT_Y"]),
    ]
    concepts = [ConceptRecord(concept_id="CONCEPT_X", definition="d")]
    result = SupportEvaluator(SemanticNormalizer(FakeLLM(OK(
        "CONCEPT_X", matched_fragment="X")))) \
        .evaluate("request X", candidates, concepts)
    assert result.unique is False
    assert result.reason_class == R_NO_SUPPORTED_CONCEPT


def test_unknown_candidate_blocks_unique_even_with_one_true():
    candidates = [
        CandidateMetadataRecord(
            candidate_id="FA", candidate_type="finding",
            status="COMPLETE", core_concepts=["CONCEPT_X"]),
        CandidateMetadataRecord(
            candidate_id="FB", candidate_type="finding",
            status="COMPLETE", core_concepts=["CONCEPT_X"]),
        CandidateMetadataRecord(
            candidate_id="FC", candidate_type="finding",
            status="INCOMPLETE", core_concepts=["CONCEPT_X"]),
    ]
    concepts = [ConceptRecord(concept_id="CONCEPT_X", definition="d")]
    result = SupportEvaluator(SemanticNormalizer(FakeLLM(OK(
        "CONCEPT_X", matched_fragment="X")))) \
        .evaluate("request X", candidates, concepts)
    # two eligible TRUEs -> multiple supported; the ineligible UNKNOWN is
    # recorded as evidence but does not block (the eligibility gate does)
    assert result.per_candidate["FC"]["support"] == "UNKNOWN"
    assert result.unique is False
    assert result.reason_class == R_MULTIPLE_SUPPORTED


def test_single_eligible_true_without_comparable_false_is_not_unique():
    candidates = [
        CandidateMetadataRecord(
            candidate_id="FA", candidate_type="finding",
            status="COMPLETE", core_concepts=["CONCEPT_X"]),
        CandidateMetadataRecord(
            candidate_id="FB", candidate_type="finding",
            status="INCOMPLETE", core_concepts=[]),
    ]
    concepts = [ConceptRecord(concept_id="CONCEPT_X", definition="d")]
    result = SupportEvaluator(SemanticNormalizer(FakeLLM(OK(
        "CONCEPT_X", matched_fragment="X")))) \
        .evaluate("request X", candidates, concepts)
    # non-equivalence unproven: no eligible FALSE comparable against FA
    assert result.per_candidate["FA"]["support"] == "TRUE"
    assert result.per_candidate["FB"]["support"] == "UNKNOWN"
    assert result.unique is False
    assert result.reason_class == R_UNKNOWN_SUPPORT


# --- LEVEL 2: real store + real registry -----------------------------------------

def test_real_store_real_registry_unique_f5():
    candidates, concepts = governed_records()
    result = SupportEvaluator(
        SemanticNormalizer(FakeLLM(OK("SHARED_DEVICE_RELATIONSHIP")))) \
        .evaluate(REQUEST_F5, candidates, concepts)
    assert result.unique is True
    assert result.unique_target == "F5"


def test_real_store_real_registry_unique_f6_and_f8():
    candidates, concepts = governed_records()
    request = "这个账户的 Linked Account Network 具体是什么？"
    result = SupportEvaluator(SemanticNormalizer(FakeLLM(OK(
        "LINKED_ACCOUNT_NETWORK",
        matched_fragment="Linked Account Network")))) \
        .evaluate(request, candidates, concepts)
    assert result.unique is True
    assert result.unique_target == "F6"
    request = "我想查看下这个账户和其他账户之间有没有关联对冲交易"
    result = SupportEvaluator(SemanticNormalizer(FakeLLM(OK(
        "OPPOSITE_TRADE_RATIO",
        matched_fragment="关联对冲交易")))) \
        .evaluate(request, candidates, concepts)
    assert result.unique is True
    assert result.unique_target == "F8"

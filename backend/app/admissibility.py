"""Safe-Select admissibility gate (WP-M2-1 WI-5).

Deterministic system control between the frozen refinement's PROPOSAL
and any resolution upgrade:

    refinement proposal (LLM)
        → WI-4 SupportEvaluator (semantic support evidence)
        → THIS gate (eligibility + uniqueness + target match +
          confidence + stability)
        → ADMIT (upgrade resolution) | REJECT (clarify)

Implements the WP-M2-1 Contract §10 predicate without relaxation.
Policy notes encoded here (per the rev. 3 Implementation Baseline):

- eligibility = metadata_status COMPLETE only (INCOMPLETE / UNVERIFIED /
  DEPRECATED / missing record ⇒ UNKNOWN ⇒ REJECT) — a unique support
  target NEVER overrides an eligibility failure (Item 2A C13 class);
- the predicate evaluates the ELIGIBLE universe; ineligible candidates
  are recorded as UNKNOWN evidence but cannot be autonomously selected
  anyway (the eligibility refusal is what keeps them unsafe-free);
- stability: the candidate set is frozen within the turn by
  construction; the caller asserts cross-turn stability separately
  (WI-2 owns reconciliation; the loop path has no refinement at all);
- confidence uses the existing frozen threshold setting only.

No LLM: the gate consumes a WI-4 SupportResult produced by an injected
SemanticNormalizer. No MCP, no candidate-specific branches.
"""

import json

from dataclasses import dataclass, field
from typing import Any

from app.telemetry import emit_event

from app.semantic_resolution import (
    R_METADATA_INELIGIBLE,
    R_MULTIPLE_SUPPORTED,
    R_NO_SUPPORTED_CONCEPT,
    R_UNKNOWN_SUPPORT,
    SupportEvaluator,
    SemanticNormalizer,
)

# reason classes (WP-M2-1 Contract §10 / rev. 3 plan §11)
R_METADATA_INCOMPLETE = "metadata_incomplete"
R_METADATA_UNVERIFIED = "metadata_unverified"
R_METADATA_DEPRECATED = "metadata_deprecated"
R_METADATA_MISSING = "metadata_missing"
R_UNSTABLE_CANDIDATES = "unstable_candidates"
R_LOW_CONFIDENCE = "low_confidence"
R_INVALID_TARGET = "invalid_target"
R_ADMITTED = "admitted"

_REASON_BY_STATUS = {
    "INCOMPLETE": R_METADATA_INCOMPLETE,
    "UNVERIFIED": R_METADATA_UNVERIFIED,
    "DEPRECATED": R_METADATA_DEPRECATED,
}


@dataclass
class AdmissibilityDecision:
    admitted: bool
    reason_class: str
    details: dict[str, Any] = field(default_factory=dict)


class SafeSelectGate:
    """Deterministic admissibility gate over governed candidate metadata
    and WI-4 support evidence."""

    def __init__(self, catalog_provider, normalizer_llm,
                 min_confidence: float):
        self._catalog = catalog_provider   # CatalogProvider (WI-1)
        self._support = SupportEvaluator(  # WI-4 evidence layer
            SemanticNormalizer(normalizer_llm))
        self._min_confidence = min_confidence

    def __call__(self, request_text: str, candidate_ids: list[str],
                 proposed_target: str, confidence: float, *,
                 candidate_set_stable: bool = True,
                 investigation_id: str | None = None)             -> AdmissibilityDecision:
        """The gate object is the injectable callable
        (InvestigationService.metadata_admissibility)."""
        return self.decide(request_text, candidate_ids, proposed_target,
                           confidence,
                           candidate_set_stable=candidate_set_stable,
                           investigation_id=investigation_id)

    def decide(self, request_text: str, candidate_ids: list[str],
               proposed_target: str, confidence: float, *,
               candidate_set_stable: bool = True,
               investigation_id: str | None = None) -> AdmissibilityDecision:
        """Deterministic decision + observational emission (WI-7). The
        emission never alters the decision; telemetry never decides."""
        decision = self._decide_impl(
            request_text, candidate_ids, proposed_target, confidence,
            candidate_set_stable=candidate_set_stable,
            investigation_id=investigation_id)
        emit_event("admissibility.decided",
                   investigation_id=investigation_id,
                   candidate_id=proposed_target,
                   decision=("ADMIT" if decision.admitted else "REJECT"),
                   reason_class=decision.reason_class,
                   summary=json.dumps({
                       "stability": ("within_turn_frozen"
                                     if candidate_set_stable else
                                     "unstable"),
                       "confidence": confidence,
                       "threshold": self._min_confidence,
                       "details": decision.details,
                   }, default=str))
        return decision

    def _decide_impl(self, request_text: str, candidate_ids: list[str],
                     proposed_target: str, confidence: float, *,
                     candidate_set_stable: bool = True,
                     investigation_id: str | None = None)             -> AdmissibilityDecision:
        details: dict[str, Any] = {"candidate_ids": list(candidate_ids)}

        if not candidate_set_stable:
            return AdmissibilityDecision(
                False, R_UNSTABLE_CANDIDATES, details)

        # --- eligibility: governed metadata for EVERY retained candidate
        records = []
        for cid in candidate_ids:
            record = self._catalog.get_candidate(cid)
            if record is None:
                details["missing_metadata"] = cid
                return AdmissibilityDecision(
                    False, R_METADATA_MISSING, details)
            if record.status != "COMPLETE":
                details["ineligible"] = {
                    "candidate_id": cid,
                    "status": record.status,
                    "reason_class": _REASON_BY_STATUS[record.status]}
                return AdmissibilityDecision(
                    False, _REASON_BY_STATUS[record.status], details)
            records.append(record)

        # --- support evidence (WI-4 deterministic layer)
        concepts = self._catalog.list_concepts(status="ACTIVE")
        support = self._support.evaluate(request_text, records, concepts,
                                         investigation_id=investigation_id)
        details["support"] = {
            "reason_class": support.reason_class,
            "normalization": support.normalization,
            "per_candidate": support.per_candidate,
        }

        # --- uniqueness + support
        if support.reason_class == R_UNKNOWN_SUPPORT:
            details["support_unknown"] = True
            return AdmissibilityDecision(False, R_UNKNOWN_SUPPORT, details)
        if support.reason_class == R_MULTIPLE_SUPPORTED:
            return AdmissibilityDecision(
                False, R_MULTIPLE_SUPPORTED, details)
        if support.reason_class == R_NO_SUPPORTED_CONCEPT:
            return AdmissibilityDecision(
                False, R_NO_SUPPORTED_CONCEPT, details)
        if not support.unique:
            return AdmissibilityDecision(False, R_UNKNOWN_SUPPORT, details)

        # --- target identity
        if proposed_target != support.unique_target:
            details["unique_target"] = support.unique_target
            details["proposed_target"] = proposed_target
            return AdmissibilityDecision(False, R_INVALID_TARGET, details)

        # --- confidence (necessary filter; frozen threshold)
        if confidence < self._min_confidence:
            details["confidence"] = confidence
            details["threshold"] = self._min_confidence
            return AdmissibilityDecision(False, R_LOW_CONFIDENCE, details)

        details["unique_target"] = support.unique_target
        return AdmissibilityDecision(True, R_ADMITTED, details)

"""Semantic normalization + deterministic support evaluation (WP-M2-1 WI-4).

Implements the evidence layer of the Safe Target Refinement contract
(docs/baselines/2026-09-24-wp-m2-1-safe-target-refinement-contract-v1.md)
as validated by the Item 2 / Item 2A discoveries.

Layering (LLM proposes, system decides):

    user language
        ↓  SemanticNormalizer: LLM proposes a governed concept + a
           verbatim request fragment; the system enforces (1) registry
           conformance, (2) verbatim grounding. ANY failure -> UNKNOWN.
    governed canonical concept
        ↓  SupportEvaluator: deterministic CORE mapping over governed
           candidate metadata + a deterministic set predicate with a
           first-class UNKNOWN.

Safety invariants encoded here:

- LLM output can never directly produce Support TRUE/FALSE, a SELECT,
  or eligibility — only a *proposal* that must pass registry
  conformance and grounding;
- confidence is recorded as provenance and is never used by the
  predicate (AC-WI4-9);
- keyword/title/description similarity never produces support — the
  ONLY path to TRUE is the normalized governed concept appearing in the
  candidate's governed core_concepts (AC-WI4-7);
- INCOMPLETE / UNVERIFIED / DEPRECATED candidate metadata evaluates to
  UNKNOWN, never FALSE and never TRUE (AC-WI4-5);
- LLM unavailability, malformed output, invalid concepts, and ungrounded
  fragments all degrade to UNKNOWN — no nearest-guess fallback
  (AC-WI4-2/3/4).

WI-4 scope note: this module is NOT wired into any runtime path and
implements no admissibility/select logic — that is WI-5. It depends on
injected ports only (no findings-provider, no service imports), so the
normal turn path and the Agent Loop path can both adopt it later
(baseline §13 integration gap stays open for WI-5).
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.telemetry import emit_event

logger = logging.getLogger(__name__)

SUPPORT_TRUE = "TRUE"
SUPPORT_FALSE = "FALSE"
SUPPORT_UNKNOWN = "UNKNOWN"

# reason classes (contract §10 vocabulary + normalization layer)
R_UNIQUE_SUPPORT = "unique_support"
R_MULTIPLE_SUPPORTED = "multiple_supported"
R_NO_SUPPORTED_CONCEPT = "no_supported_concept"
R_UNKNOWN_SUPPORT = "unknown_support"
R_METADATA_INELIGIBLE = "metadata_ineligible"
R_NORMALIZATION_UNGROUNDED = "normalization_ungrounded"
R_NORMALIZATION_INVALID_CONCEPT = "normalization_invalid_concept"
R_LLM_UNAVAILABLE = "llm_unavailable"
R_MALFORMED_OUTPUT = "malformed_output"
# AL-2.10 taxonomy fix: the provider was REACHABLE and answered, but the
# output carried no usable text block (thinking model + small budget).
# That is a semantic-resolution failure, NOT a provider outage — it must
# never be reported as llm_unavailable.
R_OUTPUT_NO_TEXT = "provider_output_no_text"


@dataclass
class Normalization:
    """Outcome of semantic normalization. status is OK or UNKNOWN."""
    status: str                                # OK | UNKNOWN
    concept_id: str | None = None
    matched_fragment: str | None = None
    reason_class: str | None = None
    llm_confidence: float | None = None        # provenance only — never
    #                                            used by the predicate
    registry_size: int = 0


@dataclass
class SupportResult:
    per_candidate: dict[str, dict[str, Any]] = field(default_factory=dict)
    unique: bool | None = None
    unique_target: str | None = None
    reason_class: str | None = None
    normalization: dict[str, Any] = field(default_factory=dict)
    deterministic: bool = True                 # predicate is system-owned


# --- normalization (LLM proposal + deterministic validation) --------------------

NORMALIZATION_SYSTEM = (
    "You normalize an investigation request to ONE governed semantic "
    "concept. Choose concept_id ONLY from the provided governed concept "
    "list — inventing an id is forbidden. matched_fragment must be a "
    "verbatim substring of the user request that justifies the concept. "
    "When several governed concepts relate to the same subject, choose "
    "the one the finding is CENTRALLY about (its core concept) — not a "
    "distinguishing signal and not a derivation mechanism. If no governed "
    "concept matches, set concept_id to null. Respond with raw JSON "
    "only: {\"concept_id\": \"<id or null>\", "
    "\"matched_fragment\": \"<verbatim fragment>\", "
    "\"confidence\": <0.0-1.0>}"
)


class NormalizingLLM(Protocol):
    """Minimal LLM port (injected; no provider import — AC-WI4-12)."""

    def generate(self, messages, max_tokens: int = 0,
                 temperature: float = 0.1) -> str: ...


class SemanticNormalizer:
    """LLM proposes; the system validates registry conformance and
    verbatim grounding. Any failure degrades to UNKNOWN."""

    def __init__(self, llm: NormalizingLLM, max_tokens: int = 2048):
        # 2048, not smaller: thinking-style models emit reasoning blocks
        # BEFORE the JSON text, and a small budget truncates the answer
        # inside the reasoning (captured live: the planner needed 2048;
        # the former 400 budget here made every registry-grounding call
        # return a text-less response — AL-2.10 audit).
        self._llm = llm
        self._max_tokens = max_tokens

    def normalize(self, request_text: str,
                  governed_concepts: dict[str, str]) -> Normalization:
        """governed_concepts: {concept_id: definition} of ACTIVE registry
        concepts (governed input from the Metadata Catalog)."""
        if not request_text.strip():
            return Normalization(
                status="UNKNOWN",
                reason_class=R_NORMALIZATION_UNGROUNDED,
                registry_size=len(governed_concepts))
        try:
            listing = "\n".join(f"- {cid}: {definition}"
                                for cid, definition in
                                governed_concepts.items())
            text = self._llm.generate(
                messages=[
                    {"role": "system", "content": NORMALIZATION_SYSTEM},
                    {"role": "user",
                     "content": f"Governed concepts:\n{listing}\n\n"
                                f"User request: {request_text}"},
                ],
                max_tokens=self._max_tokens, temperature=0.0)
        except Exception as e:
            # Truthful taxonomy (AL-2.10): a response that CARRIED NO TEXT
            # is a provider-output failure, not an outage; only genuine
            # reachability/config failures are llm_unavailable. The
            # exception type is logged (it was previously discarded).
            from app.exceptions import LLMEmptyResponseError
            if isinstance(e, LLMEmptyResponseError):
                logger.warning("semantic normalization degraded: provider "
                               "returned no text block (%s)", e)
                return Normalization(status="UNKNOWN",
                                     reason_class=R_OUTPUT_NO_TEXT,
                                     registry_size=len(governed_concepts))
            logger.warning("semantic normalization degraded: LLM "
                           "unavailable (%s: %s)", type(e).__name__, e)
            return Normalization(status="UNKNOWN",
                                 reason_class=R_LLM_UNAVAILABLE,
                                 registry_size=len(governed_concepts))
        try:
            payload = json.loads(_extract_json_object(text))
        except Exception:
            logger.warning("semantic normalization degraded: malformed "
                           "LLM output")
            return Normalization(status="UNKNOWN",
                                 reason_class=R_MALFORMED_OUTPUT,
                                 registry_size=len(governed_concepts))
        concept_id = payload.get("concept_id")
        fragment = payload.get("matched_fragment") or ""
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        if not concept_id:
            return Normalization(status="UNKNOWN",
                                 reason_class=R_NO_SUPPORTED_CONCEPT,
                                 llm_confidence=confidence,
                                 registry_size=len(governed_concepts))
        if concept_id not in governed_concepts:
            # invented / non-governed concept — rejected, never guessed
            return Normalization(status="UNKNOWN",
                                 reason_class=R_NORMALIZATION_INVALID_CONCEPT,
                                 llm_confidence=confidence,
                                 registry_size=len(governed_concepts))
        if not fragment or fragment not in request_text:
            # grounding failure — no fuzzy rescue
            return Normalization(status="UNKNOWN",
                                 reason_class=R_NORMALIZATION_UNGROUNDED,
                                 llm_confidence=confidence,
                                 registry_size=len(governed_concepts))
        return Normalization(status="OK", concept_id=concept_id,
                             matched_fragment=fragment,
                             llm_confidence=confidence,
                             registry_size=len(governed_concepts))


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in LLM output")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("unterminated JSON object in LLM output")


# --- deterministic support evaluation -------------------------------------------

class SupportEvaluator:
    """Deterministic outer shell: eligibility -> core mapping -> set
    predicate. The normalizer's proposal is the only semantic input; the
    mapping and the predicate are pure system logic."""

    def __init__(self, normalizer: SemanticNormalizer):
        self._normalizer = normalizer

    def evaluate(self, request_text: str,
                 candidates: list[Any],
                 concepts: list[Any],
                 investigation_id: str | None = None) -> SupportResult:
        """candidates: governed CandidateMetadataRecord-like objects;
        concepts: governed ConceptRecord-like objects (both from the
        Metadata Catalog)."""
        active = {c.concept_id: c for c in concepts
                  if getattr(c, "status", "ACTIVE") == "ACTIVE"}
        result = SupportResult()
        result.normalization = {"registry_size": len(active)}
        fingerprint = None
        try:
            fingerprint = hashlib.sha256(
                request_text.encode("utf-8")).hexdigest()
        except Exception:
            fingerprint = None

        ineligible = [c.candidate_id for c in candidates
                      if c.status != "COMPLETE"]
        for cid in ineligible:
            result.per_candidate[cid] = {
                "support": SUPPORT_UNKNOWN,
                "reason": R_METADATA_INELIGIBLE}

        normalization = self._normalizer.normalize(request_text, {
            cid: c.definition for cid, c in active.items()})
        result.normalization.update({
            "status": normalization.status,
            "concept_id": normalization.concept_id,
            "matched_fragment": normalization.matched_fragment,
            "reason_class": normalization.reason_class,
            "llm_confidence": normalization.llm_confidence,
        })

        if normalization.status != "OK":
            for c in candidates:
                if c.candidate_id not in result.per_candidate:
                    result.per_candidate[c.candidate_id] = {
                        "support": SUPPORT_UNKNOWN,
                        "reason": normalization.reason_class}
            result.unique = False
            result.unique_target = None
            result.reason_class = R_UNKNOWN_SUPPORT
            return result

        concept = normalization.concept_id
        for c in candidates:
            if c.candidate_id in result.per_candidate:
                continue
            supported = concept in (c.core_concepts or [])
            result.per_candidate[c.candidate_id] = {
                "support": SUPPORT_TRUE if supported else SUPPORT_FALSE,
                "reason": ("normalized concept in governed core_concepts"
                           if supported else
                           "normalized concept not in governed "
                           "core_concepts")}

        trues = [cid for cid, v in result.per_candidate.items()
                 if v["support"] == SUPPORT_TRUE]
        falses = [cid for cid, v in result.per_candidate.items()
                  if v["support"] == SUPPORT_FALSE]
        unknowns = [cid for cid, v in result.per_candidate.items()
                    if v["support"] == SUPPORT_UNKNOWN]
        # The predicate evaluates the ELIGIBLE (COMPLETE) universe.
        # Ineligible candidates are recorded as UNKNOWN evidence but do
        # not block uniqueness: they can never be autonomously selected
        # (the WI-5 eligibility gate enforces that), so they cannot make
        # an admitted SELECT unsafe. If normalization itself is UNKNOWN
        # the whole eligible set is UNKNOWN (handled above).
        if len(trues) == 1 and falses:
            result.unique = True
            result.unique_target = trues[0]
            result.reason_class = R_UNIQUE_SUPPORT
        elif len(trues) > 1:
            result.unique = False
            result.unique_target = None
            result.reason_class = R_MULTIPLE_SUPPORTED
        elif trues:
            # one TRUE but no comparable FALSE among eligible candidates:
            # non-equivalence unproven
            result.unique = False
            result.unique_target = None
            result.reason_class = R_UNKNOWN_SUPPORT
        else:
            result.unique = False
            result.unique_target = None
            result.reason_class = R_NO_SUPPORTED_CONCEPT

        counts = {"TRUE": 0, "FALSE": 0, "UNKNOWN": 0}
        for v in result.per_candidate.values():
            counts[v["support"]] = counts.get(v["support"], 0) + 1
        emit_event("support.evaluated",
                   investigation_id=investigation_id,
                   request_fingerprint=fingerprint,
                   reason_class=result.reason_class,
                   summary=json.dumps({
                       "counts": counts,
                       "unique": result.unique,
                       "unique_target": result.unique_target,
                       "normalization_status": result.normalization.get(
                           "status"),
                       "normalization_concept": result.normalization.get(
                           "concept_id"),
                       "deterministic": True,
                   }))
        return result

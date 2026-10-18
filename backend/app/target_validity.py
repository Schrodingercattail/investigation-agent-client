"""AL-3.5.1 G1 — Target Validity Guard (Layer B).

Deterministic, system-only admission control for finding-specific
investigation targets (docs/plans/2026-10-05-al3-5-1-target-validity-
guard-design-v1.md):

    resolution → TARGET VALIDITY → planner / execution

The LLM may propose a target; only the system admits it. This module
never runs an LLM and never inspects chain-of-thought: it classifies
the SYSTEM-side evidence kind carried by the resolution outcome (or
persisted loop state, on resume) into one of four states.

Four-state model (UNKNOWN ≠ FALSE discipline):

    TARGET_VALID       execute — a system evidence kind holds
    TARGET_AMBIGUOUS   clarify — ≥2 candidates co-supported
    TARGET_UNGROUNDED  clarify — a RESOLVED binding with no system
                       evidence kind ("cannot prove the target correct"
                       is NOT "the target is proven wrong"; the response
                       must not claim the topic does not exist)
    TARGET_INVALID     log + clarify — positive contradiction only
                       (v1: the goal verbatim names a different
                       finding id than the adopted focus)

Valid evidence kinds (set by the resolver; additive metadata):

    explicit_selection         E1 user-selected focus (never guard-gated;
                               PMO decision: explicit selection is a
                               separate path from autonomous selection)
    context_continuation       E4 existing focus preserved by the resolver
    title_match                E2 verbatim authoritative-title match (P3)
    concept_grounding          E3 governed-concept grounding (P3.5)
    event_match                deterministic event resolution
                               (caller-supplied events)
    validated_assist_evidence  E5 Layer-A-validated assist proposal (P4)

Any other / missing kind on a finding-focused RESOLVED outcome is
TARGET_UNGROUNDED: no finding-specific investigation tool may run
before the clarification.

Flag: ENABLE_TARGET_VALIDITY_GUARD (default OFF = this module is never
consulted and behavior is byte-for-byte pre-guard).
"""

from dataclasses import dataclass

# --- states -----------------------------------------------------------------
TARGET_VALID = "TARGET_VALID"
TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
TARGET_UNGROUNDED = "TARGET_UNGROUNDED"
TARGET_INVALID = "TARGET_INVALID"

# --- evidence kinds (mirrors ResolutionOutcome.evidence_kind) ---------------
EVIDENCE_EXPLICIT_SELECTION = "explicit_selection"
EVIDENCE_CONTEXT_CONTINUATION = "context_continuation"
EVIDENCE_TITLE_MATCH = "title_match"
EVIDENCE_CONCEPT_GROUNDING = "concept_grounding"
EVIDENCE_EVENT_MATCH = "event_match"
EVIDENCE_VALIDATED_ASSIST = "validated_assist_evidence"
# the focus was re-anchored to the ACCEPTED GoalContract's single
# finding-level subject (the contract is built from THIS request's goal
# understanding — a governed resolution source, same precedence family as
# validated assist)
EVIDENCE_CONTRACT_SUBJECT = "contract_subject"

_VALID_EVIDENCE_KINDS = {
    EVIDENCE_EXPLICIT_SELECTION,
    EVIDENCE_CONTEXT_CONTINUATION,
    EVIDENCE_TITLE_MATCH,
    EVIDENCE_CONCEPT_GROUNDING,
    EVIDENCE_EVENT_MATCH,
    EVIDENCE_VALIDATED_ASSIST,
    EVIDENCE_CONTRACT_SUBJECT,
}

# --- reason codes (structured telemetry; never chain-of-thought) ------------
R_NO_FINDING_TARGET = "no_finding_target"       # case-level / no focus
R_EXPLICIT_SELECTION = "e1_explicit_selection"
R_CONTEXT_CONTINUATION = "e4_context_continuation"
R_TITLE_MATCH = "e2_title_match"
R_CONCEPT_GROUNDING = "e3_concept_grounding"
R_EVENT_MATCH = "e_event_match"
R_VALIDATED_ASSIST = "e5_validated_assist_evidence"
R_NO_EVIDENCE_KIND = "r_no_evidence_kind"
R_FINDING_ID_MISMATCH = "r_finding_id_mismatch"
R_AMBIGUOUS_PASSTHROUGH = "r_ambiguous_passthrough"
R_UNRESOLVED_PASSTHROUGH = "r_unresolved_passthrough"

USER_SELECTED = "user_selected"
AGENT_RESOLVED = "agent_resolved"


@dataclass
class GuardDecision:
    """Outcome of one target-validity admission evaluation."""
    state: str
    reason_code: str
    evidence_kind: str | None = None
    candidate_finding_id: str | None = None


def _mentioned_finding_ids(goal: str) -> list[str]:
    """Finding ids explicitly named in the goal text (deterministic).
    Used only for the v1 INVALID positive-contradiction check.

    Word-boundary lookarounds instead of \\b: CJK characters are \\w in
    Python re, so \\bF\\d+\\b would never match 「谈谈F7这个风险项」
    (no boundary between a CJK char and 'F')."""
    import re
    return list(dict.fromkeys(re.findall(
        r"(?<![A-Za-z0-9])F\d+(?![A-Za-z0-9])", goal or "")))


def decide(
    *,
    goal: str,
    resolution_status: str | None,
    focused_finding_id: str | None,
    focus_source: str | None,
    evidence_kind: str | None,
) -> GuardDecision:
    """Classify one adopted investigation target.

    Pure function — no LLM, no I/O, no catalog access (Layer A already
    validated metadata membership; this layer classifies the evidence
    kind and enforces the admission boundary).
    """
    # No finding target (case-level, guidance, unresolved runs) — the
    # guard never constrains case-level tools.
    if resolution_status != "resolved" or not focused_finding_id:
        return GuardDecision(TARGET_VALID, R_NO_FINDING_TARGET, evidence_kind)

    # v1 INVALID: positive contradiction only — the goal explicitly names
    # finding ids and none of them is the adopted focus. Log + clarify
    # (never a hard BLOCK terminal in v1, per the approved design).
    mentioned = _mentioned_finding_ids(goal)
    if mentioned and focused_finding_id not in mentioned:
        return GuardDecision(
            TARGET_INVALID, R_FINDING_ID_MISMATCH, evidence_kind,
            focused_finding_id)

    # E1: explicit user selection is authoritative and never guard-gated.
    if (focus_source == USER_SELECTED
            or evidence_kind == EVIDENCE_EXPLICIT_SELECTION):
        return GuardDecision(
            TARGET_VALID, R_EXPLICIT_SELECTION, evidence_kind,
            focused_finding_id)

    # E2/E3/E4/E5 (+ deterministic event resolution): system evidence kind.
    if evidence_kind in _VALID_EVIDENCE_KINDS:
        reason = {
            EVIDENCE_CONTEXT_CONTINUATION: R_CONTEXT_CONTINUATION,
            EVIDENCE_TITLE_MATCH: R_TITLE_MATCH,
            EVIDENCE_CONCEPT_GROUNDING: R_CONCEPT_GROUNDING,
            EVIDENCE_EVENT_MATCH: R_EVENT_MATCH,
            EVIDENCE_VALIDATED_ASSIST: R_VALIDATED_ASSIST,
            EVIDENCE_CONTRACT_SUBJECT: "r_contract_subject",
        }.get(evidence_kind, R_VALIDATED_ASSIST)
        return GuardDecision(
            TARGET_VALID, reason, evidence_kind, focused_finding_id)

    # A finding-focused RESOLVED outcome with no recognizable system
    # evidence kind: UNKNOWN ≠ FALSE — clarify; no finding-specific
    # tool may run.
    return GuardDecision(
        TARGET_UNGROUNDED, R_NO_EVIDENCE_KIND, evidence_kind,
        focused_finding_id)


def ambiguous_passthrough(candidate_finding_id: str | None = None) -> GuardDecision:
    """Complete the four-state surface for AMBIGUOUS outcomes (the loop
    already terminates AMBIGUOUS resolutions before this guard; provided
    so callers can represent the full state machine without inventing
    states)."""
    return GuardDecision(TARGET_AMBIGUOUS, R_AMBIGUOUS_PASSTHROUGH,
                         None, candidate_finding_id)


def ungrounded_passthrough(candidate_finding_id: str | None = None) -> GuardDecision:
    """Complete the four-state surface for UNRESOLVED outcomes."""
    return GuardDecision(TARGET_UNGROUNDED, R_UNRESOLVED_PASSTHROUGH,
                         None, candidate_finding_id)

"""Shared composition-level wording helpers.

Single owner for composition-time sentence semantics used by BOTH
composition surfaces (conversation composer and artifact composer):

- per-producer evidence-gap statements: a payload's `next_data_needed` is
  the authoritative statement of WHAT is missing, and the rendered sentence
  must never widen it into a broader investigation claim (P14);
- detection-mechanism identification for signal-explanation responses:
  derived deterministically from the authoritative structured signal data
  (`signal_type` + rule/ML/graph fields) — never from the LLM and never
  from user text.
"""

from typing import Any


def is_policy_gap_payload(payload: dict[str, Any]) -> bool:
    """True when the payload is a policy_lookup result (its gap IS the
    missing finding-level association — rendered by the policy branch)."""
    return payload.get("finding_policy_status") is not None


def gap_statement(payload: dict[str, Any]) -> str | None:
    """The bounded-honesty gap sentence for one payload, naming the ACTUAL
    missing item(s). None when the payload reports no gap. Never produces a
    claim broader than `next_data_needed` (e.g. never infers "transaction-
    level evidence unavailable" from a feature-attribution gap)."""
    if not payload.get("evidence_missing"):
        return None
    needed = [str(nd) for nd in (payload.get("next_data_needed") or [])]
    if is_policy_gap_payload(payload):
        return "No finding-level policy association is attached to this finding."
    if needed:
        if len(needed) == 1:
            return f'Missing: "{needed[0]}" (not available from the Risk Platform).'
        listing = "; ".join(f'"{nd}"' for nd in needed)
        return (f"Missing: {listing} (none of these are currently "
                "available from the Risk Platform).")
    return ("Some supporting data for this result is not currently "
            "available from the Risk Platform.")


# --- detection-mechanism identification (signal explanations) ---------------

# Grammatically-correct confirmations for each producer's gap, phrased to
# follow "The Risk Platform confirms the finding, but …". Keyed by the
# producer's own next_data_needed item (exact strings the tools emit).
_GAP_FOLLOWUPS = {
    "transaction-level feature attribution":
        "transaction-level feature attribution is not currently available,"
        " so the specific model features driving the score cannot be shown",
    "triggered-rule evidence associated with this finding":
        "the triggered-rule evidence associated with this finding is not "
        "currently available",
    "network/cluster evidence for this case":
        "network or cluster evidence is not currently available",
    "graph signal associated with this finding":
        "the graph signal associated with this finding is not currently "
        "available",
}
_GENERIC_GAP_FOLLOWUP = (
    "some supporting evidence for this result is not currently available"
)


def gap_confirmation_clause(payload: dict[str, Any]) -> str | None:
    """The '…, but <gap>' continuation for a signal explanation, with
    grammar fixed per producer. None when there is no gap. Unknown
    next_data_needed items are quoted verbatim without a verb so no
    subject-verb agreement is ever wrong."""
    if not payload.get("evidence_missing"):
        return None
    needed = [str(nd) for nd in (payload.get("next_data_needed") or [])]
    if is_policy_gap_payload(payload):
        return None          # policy payloads are rendered by their branch
    if needed:
        parts = [_GAP_FOLLOWUPS.get(nd, f'missing: "{nd}"')
                 for nd in needed]
        return ", but " + " and ".join(parts)
    return f", but {_GENERIC_GAP_FOLLOWUP}"


def detection_mechanism_sentence(payload: dict[str, Any]) -> str | None:
    """Deterministic detection-mechanism identification for one
    signal-explanation payload, from authoritative structured data:
      - a backed rule payload  → rule-based detection (names the rule only
        when the payload carries no description that will name it again)
      - signal_type ML         → the ML pattern detection model
      - signal_type Graph      → graph-based risk detection
      - signal_type Rule (no backing rule) → rule-based detection
    Returns None for non-signal payloads. No thresholds, features, rules,
    or network facts are fabricated beyond what the payload carries."""
    signal_type = payload.get("signal_type")
    if payload.get("signal_type") not in ("ML", "Rule", "Graph"):
        return None
    rule = payload.get("rule")
    if signal_type == "ML":
        return ("This finding was flagged by the ML Pattern Detection "
                "model.")
    if signal_type == "Graph":
        return "This finding was flagged by graph-based risk detection."
    # Rule
    if isinstance(rule, dict) and rule.get("name"):
        return (f"This finding was flagged by rule-based detection "
                f"(the {rule['name']} rule).")
    return "This finding was flagged by rule-based detection."

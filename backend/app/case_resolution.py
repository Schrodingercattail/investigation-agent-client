"""Case Reference Resolution — initial case identification.

Establishes the canonical ``case_id`` for an initial investigation from the
user's raw input (e.g. ``"investigate case 00299"`` → ``"U00299"``).

This is related to but DISTINCT from Context Resolution
(``app.context_resolution``):

    Case Reference Resolution → canonical case_id BEFORE an investigation exists
    Context Resolution        → finding/event references AFTER context exists

Design constraints (Week 1):
- deterministic only — no LLM, no fuzzy matching, no database search
- explicit pattern for the current Risk Platform identifier format
  (``U`` + digits, e.g. ``U00299``); numeric references normalize by
  prefixing ``U``
- bounded outcomes: resolved / missing / ambiguous / invalid — ambiguity is
  never silently resolved
- user text is data: it can only ever produce a case reference matching the
  canonical pattern; arbitrary identifiers cannot be injected
"""

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Canonical RP case identifier: U + 3-6 digits (e.g. U00299).
#
# Boundary rule (whitespace-intelligence): a CJK character and a digit have
# no \b between them (regex \b is ASCII-word-aware), so the classic
# \b[Uu]?\d{3,6}\b silently failed "调查00033" / "investigate00033".
# The boundary below instead rejects only references *embedded inside an
# alphanumeric word* (lookbehind: no [A-Za-z0-9] immediately before the
# optional U / the digits) — whitespace, punctuation, line start, and CJK
# neighbors all qualify as boundaries.
_CASE_REF_PATTERN = re.compile(r"(?<![A-Za-z0-9])[Uu]?(\d{3,6})(?![0-9A-Za-z])")
_CANONICAL_PATTERN = re.compile(r"^[Uu]\d{3,6}$")

# Case-intent words whose direct concatenation with an identifier is normal
# natural formatting ("investigate00033", "调查U00033"), not one long word.
# Boundary before the word is \b-free for the same CJK/ASCII reason: it only
# requires the preceding char (if any) to be non-alphanumeric.
# Any CJK character already acts as a boundary for the digits.
_INTENT_WORD_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(investigation|investigate|check|review|show|open|start|case|调查|检查|审查)"
    r"(?=[Uu]?\d{3,6})",
    re.IGNORECASE,
)

# Simple natural-language wrappers around a case reference (Week 1 set).
_WRAPPER_WORDS = re.compile(
    r"\b(investigate|investigation|look|into|check|case|show|open|start|review|on|for|the|a|an|please|of|me|up)\b",
    re.IGNORECASE,
)


class CaseResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    MISSING = "missing"          # no case reference supplied
    AMBIGUOUS = "ambiguous"      # multiple distinct references
    INVALID = "invalid"          # text looks like a reference but is not one


class CaseResolution(BaseModel):
    """Bounded outcome of resolving an initial case reference."""

    status: CaseResolutionStatus
    case_id: str | None = None
    candidates: list[str] = Field(default_factory=list)
    message: str | None = None    # bounded user-facing clarification


def _canonicalize(raw: str) -> str:
    """Numeric reference → U-prefixed canonical id; canonical stays as-is
    (normalized to uppercase)."""
    raw = raw.strip()
    if raw.isdigit():
        return f"U{raw}"
    return raw.upper()


def _is_wrapper_only(text: str) -> bool:
    """True when, after removing wrapper words, nothing meaningful remains
    (i.e. the text had no case reference at all)."""
    return _WRAPPER_WORDS.sub("", text).strip() == ""


def resolve_case_reference(user_input: str) -> CaseResolution:
    """Resolve a raw initial request to a canonical case_id.

    Examples:
        "00299"                      → resolved U00299
        "U00299" / "u00299"          → resolved U00299
        "investigate case 00299"     → resolved U00299
        "look into 00299"            → resolved U00299
        "investigate case 00299 and 00301" → ambiguous [U00299, U00301]
        "hello"                      → missing
        "case ABCXYZ"                → invalid (looks like a reference)
    """
    if not user_input or not user_input.strip():
        return CaseResolution(
            status=CaseResolutionStatus.MISSING,
            message=(
                "I couldn't identify a case. Please provide a case ID "
                "such as 00299."
            ),
        )

    text = user_input.strip()

    # Boundary normalization (shared parsing layer): separate case-intent
    # words from a directly concatenated identifier so formatting variants
    # ("investigate00033", "调查U00033") resolve identically to their
    # spaced forms. Whitespace is never semantically required at that
    # boundary.
    text = _INTENT_WORD_RE.sub(lambda m: m.group(1) + " ", text)

    matches = _CASE_REF_PATTERN.findall(text)
    if not matches:
        # No digit-reference found. If the user appears to be pointing at
        # an identifier (an explicit "case/reference/id <token>" pattern),
        # the reference is invalid; otherwise it is plain conversation with
        # no reference at all.
        has_identifier_intent = bool(
            re.search(
                r"\b(case|reference|ref|id)\b[:\s]+\S+",
                text,
                re.IGNORECASE,
            )
        )
        if has_identifier_intent:
            return CaseResolution(
                status=CaseResolutionStatus.INVALID,
                message=(
                    "I couldn't recognize that case reference. Please "
                    "provide a case ID such as 00299."
                ),
            )
        return CaseResolution(
            status=CaseResolutionStatus.MISSING,
            message=(
                "I couldn't identify a case. Please provide a case ID "
                "such as 00299."
            ),
        )

    candidates = [_canonicalize(digits) for digits in matches]

    # dedupe preserving order; a repeated reference is not ambiguity
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    if len(unique) > 1:
        listing = " and ".join(unique)
        return CaseResolution(
            status=CaseResolutionStatus.AMBIGUOUS,
            candidates=unique,
            message=(
                f"I found multiple case references: {listing}. Which case "
                "should I investigate?"
            ),
        )

    return CaseResolution(
        status=CaseResolutionStatus.RESOLVED,
        case_id=unique[0],
    )


def resolve_or_raise(user_input: str) -> str:
    """Convenience: return the canonical case_id or raise a ValueError with
    the bounded clarification message (callers map it to their error
    envelope)."""
    resolution = resolve_case_reference(user_input)
    if resolution.status == CaseResolutionStatus.RESOLVED:
        return resolution.case_id  # type: ignore[return-value]
    raise ValueError(resolution.message or "Could not resolve a case reference.")

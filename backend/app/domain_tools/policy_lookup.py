"""policy_lookup — Week 1 finding-aware policy context tool.

Retrieves policy context relevant to the investigation through the existing
Risk Platform citation pipeline (POST /api/risk/explain via the adapter —
the only HTTP policy surface RP exposes; no new endpoint, no local corpus,
no new RAG).

Semantics:
- topic is the investigator's retrieval focus; it RANKS the case's already-
  validated citations deterministically (keyword overlap). It is untrusted
  content: it never redefines tools/skills/capabilities and is echoed, never
  executed.
- finding_id must be a canonical Finding ID from the case context (no fuzzy
  title matching).
- The finding's own authoritative policy_refs are preserved and reported
  separately from newly-retrieved matches.
- Zero matched citations → empty (a valid query with no results — NOT an
  error, NOT evidence_missing). Evidence-missing is reserved for the case
  where the finding cites no policies at all, so policy requirements cannot
  be assessed for it.
"""

import logging
from typing import Any

from app.adapters.risk_platform import (
    RiskPlatformError,
    normalize_policy_matches,
    split_policy_refs,
)
from app.models import (
    EvidenceRef,
    PolicyRef,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)

logger = logging.getLogger(__name__)


def _validation_error(message: str) -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.VALIDATION_ERROR,
        error=ToolError(code="INVALID_ARGUMENT", message=message),
    )


def policy_lookup(
    topic: str,
    finding_id: str,
    case_id: str | None = None,
    case_context: dict[str, Any] | None = None,
) -> ToolResult:
    """Look up policy context for one finding, ranked by topic.

    Returns ToolResult with data payload:
        {finding_id, topic, matches[], associated_policy_refs,
         newly_retrieved_refs, policy_refs, required_evidence,
         evidence_missing, next_data_needed}
    """
    # --- input validation -----------------------------------------------------
    if not isinstance(topic, str) or not topic.strip():
        return _validation_error("topic is required (non-empty string).")
    topic = topic.strip()
    if not isinstance(finding_id, str) or not finding_id.strip():
        return _validation_error("finding_id is required (non-empty string).")
    finding_id = finding_id.strip()
    if case_context is None and (not isinstance(case_id, str) or not case_id.strip()):
        return _validation_error(
            "policy_lookup requires case context (case_context or case_id)."
        )

    # --- canonical finding resolution -------------------------------------------
    findings: list = []
    explanation: dict[str, Any] | None = None
    if case_context is not None:
        findings = list(case_context.get("findings") or [])
        case_id = case_context.get("case_id", case_id)
    if not findings:
        from app.domain_tools.risk_case_fetch import risk_case_fetch
        base = risk_case_fetch(case_id=str(case_id).strip())
        if base.outcome == ToolResultOutcome.EMPTY:
            return ToolResult(
                outcome=ToolResultOutcome.EMPTY,
                data={"finding_id": finding_id, "topic": topic},
                warnings=["Case has no data; finding cannot exist."],
            )
        if base.outcome != ToolResultOutcome.SUCCESS:
            return base
        data = base.data or {}
        findings = list(data.get("findings") or [])
        case_context = data

    matches_finding = [f for f in findings if f.finding_id == finding_id]
    if not matches_finding:
        return _validation_error(
            f"Finding {finding_id!r} does not exist in the current case context."
        )
    finding = matches_finding[0]

    # Capability gate: policy_lookup is a policy_lookup-capability action.
    if not finding.capabilities.supports("policy_lookup"):
        return ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(
                code="CAPABILITY_NOT_SUPPORTED",
                message=(
                    f"Finding {finding_id} does not support policy lookup."
                ),
                detail={"supported_capabilities": sorted(finding.capabilities)},
            ),
        )

    # --- retrieve the case's authoritative citations ----------------------------
    try:
        if case_context is not None and case_context.get("_explanation") is not None:
            explanation = case_context["_explanation"]
        else:
            from app.adapters.risk_platform import RiskPlatformAdapter
            _, explanation = RiskPlatformAdapter().fetch_case(str(case_id))
    except RiskPlatformError as err:
        logger.error("policy_lookup integration failure: %s", err.message)
        code = {
            "unavailable": "RISK_PLATFORM_UNAVAILABLE",
            "auth": "RISK_PLATFORM_AUTH_FAILED",
            "http": "RISK_PLATFORM_HTTP_ERROR",
            "malformed": "RISK_PLATFORM_MALFORMED_RESPONSE",
        }.get(err.kind, "RISK_PLATFORM_ERROR")
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(code=code, message=err.message,
                            detail={"status_code": err.status_code}
                            if err.status_code else None),
        )

    citations = list((explanation or {}).get("citations") or [])

    # --- normalize + partition ---------------------------------------------------
    try:
        matches = normalize_policy_matches(citations=citations, topic=topic)
        associated, newly = split_policy_refs(finding=finding, matches=matches)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        logger.error("policy_lookup normalization failure: %s", e)
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code="RISK_PLATFORM_MALFORMED_RESPONSE",
                message=f"Could not normalize policy citations: {e}",
            ),
        )

    if not matches:
        # Valid lookup, zero relevant policy matches → empty (per contract).
        return ToolResult(
            outcome=ToolResultOutcome.EMPTY,
            data={"finding_id": finding_id, "topic": topic,
                  "matches": [], "policy_refs": []},
            warnings=[
                "No policy citations were returned for this case.",
            ],
        )

    # Evidence-missing: the finding cites NO policies at all, so policy
    # requirements specific to it cannot be assessed (case-level citations
    # may still exist).
    evidence_missing = not finding.policy_refs
    next_data_needed = (
        ["finding-level policy association (citations attached to this "
         "finding by the Risk Platform)"] if evidence_missing else []
    )

    payload = {
        "finding_id": finding_id,
        "topic": topic,
        "matches": matches,
        "associated_policy_refs": [p.model_dump() for p in associated],
        "newly_retrieved_refs": [p.model_dump() for p in newly],
        "policy_refs": [p.model_dump() for p in finding.policy_refs],
        # RP exposes no required-evidence checklist; never invented.
        "required_evidence": [],
        "evidence_missing": evidence_missing,
        "next_data_needed": next_data_needed,
    }

    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data=payload,
        evidence_refs=[
            EvidenceRef(kind="policy_citation", id=str(m.get("chunk_id") or ""))
            for m in matches if m.get("chunk_id")
        ],
        citation_refs=[
            PolicyRef(
                citation_id=m.get("citation_id"),
                chunk_id=m.get("chunk_id"),
                doc=m.get("document"),
                section=m.get("section"),
            ) for m in matches
        ],
    )

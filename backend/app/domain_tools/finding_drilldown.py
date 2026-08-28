"""finding_drilldown — Week 1 Focus Mode drill-down tool.

Week 1 view: "timeline" only (opposite_trades is future work).

Pipeline:
  input validation (finding_id, view lock, top_n bounds)
  → canonical Finding resolution from case context (no title guessing)
  → capability enforcement (finding must declare "timeline")
  → RiskPlatformAdapter.fetch_case (existing endpoint, no new RP endpoints)
  → adapter normalize_timeline_events (RP shapes → TimelineEvent dicts)
  → validated TimelineEvent models, chronological, deterministic IDs
  → normalized ToolResult

The tool returns structured data only — never a final narrative — and never
fabricates timestamps, evidence, signals, or policy references.
"""

import logging
from typing import Any

from app.adapters.risk_platform import (
    RiskPlatformError,
    normalize_timeline_events,
)
from app.models import (
    EvidenceRef,
    Finding,
    FindingCapability,
    PolicyRef,
    SignalRef,
    TimelineEvent,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)

logger = logging.getLogger(__name__)

ALLOWED_VIEWS = {"timeline"}          # opposite_trades arrives in a later step
TOP_N_DEFAULT = 20
TOP_N_MAX = 100


def _validation_error(message: str) -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.VALIDATION_ERROR,
        error=ToolError(code="INVALID_ARGUMENT", message=message),
    )


def finding_drilldown(
    finding_id: str,
    view: str = "timeline",
    top_n: int = TOP_N_DEFAULT,
    case_context: dict[str, Any] | None = None,
    case_id: str | None = None,
) -> ToolResult:
    """Drill into one finding's timeline composition.

    Args:
        finding_id: canonical Finding ID from the current case context.
        view: locked to "timeline" in Week 1 (parameter-locked by the
              timeline_investigation skill).
        top_n: maximum events returned, 1..TOP_N_MAX.
        case_context: canonical case payload previously returned by
            risk_case_fetch ({case_id, findings: [Finding...]}) — the source
            for deterministic finding resolution. Providing it avoids a
            redundant fetch when the executor already holds case context.
        case_id: fallback case id when no case_context is supplied; the tool
            then fetches the case itself through the adapter.

    Returns ToolResult with data payload:
        {finding_id, view, events: [TimelineEvent...], total_events,
         truncated, top_n}
    """
    # --- input validation -----------------------------------------------------
    if not isinstance(finding_id, str) or not finding_id.strip():
        return _validation_error("finding_id is required (non-empty string).")
    finding_id = finding_id.strip()

    if view not in ALLOWED_VIEWS:
        # Locked view: other views (e.g. opposite_trades) are not executable
        # in Week 1 — validation error against the current tool contract.
        return _validation_error(
            f"view {view!r} is not supported by finding_drilldown; "
            f"supported views: {sorted(ALLOWED_VIEWS)}."
        )
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
        return _validation_error("top_n must be a positive integer.")
    top_n = min(top_n, TOP_N_MAX)

    # --- canonical finding resolution -------------------------------------------
    findings: list[Finding] = []
    if case_context is not None:
        findings = list(case_context.get("findings") or [])
        case_id = case_context.get("case_id", case_id)
    if not findings:
        if not case_id or not str(case_id).strip():
            return _validation_error(
                "finding_drilldown requires case context (case_context or "
                "case_id) to resolve the canonical finding."
            )
        # Fetch canonical case context through the adapter boundary — the
        # same endpoint risk_case_fetch uses; no new RP calls invented.
        from app.domain_tools.risk_case_fetch import risk_case_fetch
        base = risk_case_fetch(case_id=str(case_id).strip())
        if base.outcome != ToolResultOutcome.SUCCESS:
            # propagate empty/integration/validation semantics unchanged
            if base.outcome == ToolResultOutcome.EMPTY:
                return ToolResult(
                    outcome=ToolResultOutcome.EMPTY,
                    data={"finding_id": finding_id, "view": view,
                          "events": [], "total_events": 0,
                          "truncated": False, "top_n": top_n},
                    warnings=["Case has no data; finding cannot exist."],
                )
            return base
        findings = list((base.data or {}).get("findings") or [])

    matches = [f for f in findings if f.finding_id == finding_id]
    if not matches:
        return _validation_error(
            f"Finding {finding_id!r} does not exist in the current case "
            "context."
        )
    finding = matches[0]

    # --- capability enforcement (runtime re-check; never silent-empty) --------
    if not finding.capabilities.supports("timeline"):
        return ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(
                code="CAPABILITY_NOT_SUPPORTED",
                message=(
                    f"Finding {finding_id} does not support the timeline "
                    "investigation."
                ),
                detail={"supported_capabilities": sorted(finding.capabilities)},
            ),
        )

    # --- fetch raw evidence if not already supplied -----------------------------
    evidence: dict[str, Any]
    try:
        if case_context is not None and "_raw_evidence" in case_context:
            evidence = case_context["_raw_evidence"]
        else:
            from app.adapters.risk_platform import RiskPlatformAdapter
            if case_context is not None and case_context.get("case_id"):
                cid = case_context["case_id"]
            else:
                cid = case_id
            evidence = RiskPlatformAdapter().fetch_case_evidence(str(cid))
    except RiskPlatformError as err:
        logger.error("finding_drilldown integration failure: %s", err.message)
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

    # --- timeline normalization (adapter owns RP shapes) --------------------------
    try:
        event_dicts = normalize_timeline_events(
            case_id=str(case_id or evidence.get("user_id", "")),
            evidence=evidence,
            finding=finding,
        )
        events = [TimelineEvent(**e) for e in event_dicts]
    except RiskPlatformError as e:
        logger.error("finding_drilldown normalization failure: %s", e.message)
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code="RISK_PLATFORM_MALFORMED_RESPONSE",
                message=e.message,
            ),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.error("finding_drilldown normalization failure: %s", e)
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code="RISK_PLATFORM_MALFORMED_RESPONSE",
                message=f"Could not normalize evidence into timeline: {e}",
            ),
        )

    if not events:
        # Valid finding, timeline-capable, but no timestamped evidence exists.
        return ToolResult(
            outcome=ToolResultOutcome.EMPTY,
            data={"finding_id": finding_id, "view": view, "events": [],
                  "total_events": 0, "truncated": False, "top_n": top_n},
            warnings=[
                "No timestamped evidence is available for this finding in "
                "the Risk Platform."
            ],
        )

    # Provenance preservation: attach case-level citations the finding is
    # actually linked to (from the Finding's own policy_refs only).
    if finding.policy_refs:
        events = [
            e.model_copy(update={
                "policy_refs": [p.model_copy() for p in finding.policy_refs]
            })
            for e in events
        ]

    total = len(events)
    truncated = total > top_n
    events = events[:top_n]

    payload = {
        "finding_id": finding_id,
        "view": view,
        "events": events,                    # TimelineEvent models
        "total_events": total,
        "truncated": truncated,
        "top_n": top_n,
    }
    warnings = []
    if truncated:
        warnings.append(
            f"Timeline truncated to top_n={top_n} of {total} events "
            "(chronological order preserved)."
        )
    # Detail insufficiency: if the finding's evidence references point at data
    # the RP evidence response did not include, surface evidence_missing while
    # remaining a success.
    linked_kinds = {r.kind for r in finding.evidence_refs}
    if linked_kinds - {"transaction", "withdrawal", "risk_event"}:
        payload["evidence_missing"] = True
        payload["next_data_needed"] = sorted(
            f"evidence stream: {k}" for k in sorted(linked_kinds)
        )

    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data=payload,
        evidence_refs=[r for e in events for r in e.evidence_refs],
        citation_refs=list(finding.policy_refs),
    )

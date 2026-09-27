"""finding_drilldown — Week 1 Focus Mode drill-down tool.

Views:
- "timeline"  : chronological events of a finding. Built from the COMPLETE
                authoritative evidence (RP opt-in complete records); top_n
                bounds the returned list ONLY when the user explicitly
                requested a subset (labeled truncated + total, never
                presented as the complete timeline).
- "evidence"  : CONCRETE EVIDENCE view — the COMPLETE authoritative record
                set (all RP-stored withdrawals/transactions relevant to the
                finding). top_n is IGNORED for this view: completeness is
                its contract, and a subset may never be presented as the
                evidence.
- "self_opposite_trades" : SAME-ACCOUNT opposite-side trading composition
                (canonical name; legacy alias "opposite_trades"). The
                authoritative opposite_trade_ratio and its threshold rule
                are Risk Platform aggregates echoed verbatim; the account's
                own complete transaction record set rides along. Capability
                gated on "self_opposite_trades". This is NOT the
                cross-account analysis — that is the separate
                cross_account_trade_analysis tool.

Pipeline:
  input validation (finding_id, view lock, top_n bounds)
  → canonical Finding resolution from case context (no title guessing)
  → capability enforcement (finding must declare "timeline")
  → RiskPlatformAdapter.fetch_case / fetch_case_evidence (existing RP
    endpoints; expose_complete_records only for view="evidence")
  → adapter normalization (RP shapes → TimelineEvent / evidence records)
  → validated models, chronological, deterministic IDs
  → normalized ToolResult

The tool returns structured data only — never a final narrative — and never
fabricates timestamps, evidence, signals, or policy references.
"""

import logging
from typing import Any

from app.adapters.risk_platform import (
    RiskPlatformError,
    normalize_evidence_records,
    normalize_rule_signal,
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

ALLOWED_VIEWS = {"timeline", "evidence", "self_opposite_trades"}
# Legacy alias for the canonical self-opposite view (compatibility only).
SELF_VIEW_LEGACY_ALIAS = "opposite_trades"
SELF_VIEW = "self_opposite_trades"
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
    top_n: int | None = None,
    case_context: dict[str, Any] | None = None,
    case_id: str | None = None,
    stream: str | None = None,
) -> ToolResult:
    """Drill into one finding's timeline or concrete evidence.

    Args:
        finding_id: canonical Finding ID from the current case context.
        view: "timeline", "evidence", or "self_opposite_trades" — all are
            COMPLETE views ("opposite_trades" is accepted as a legacy
            alias of the self view).
        stream: EXPLICIT evidence-stream scope for view="evidence"
            ("withdrawals" | "transactions"). When supplied it governs
            which RP stream is returned and OVERRIDES the finding-title
            heuristic — a withdrawal request can never silently return
            transactions. None keeps the title-based stream selection for
            unscoped evidence requests.
        top_n: EXPLICIT user-requested subset only ("show 5 examples").
            None (default — an ordinary request) returns the complete
            available timeline; an explicit N bounds the returned events
            and marks the result truncated=True with total_events, so a
            subset can never masquerade as the complete timeline.
        case_context: canonical case payload previously returned by
            risk_case_fetch ({case_id, findings: [Finding...]}) — the source
            for deterministic finding resolution. Providing it avoids a
            redundant fetch when the executor already holds case context.
        case_id: fallback case id when no case_context is supplied; the tool
            then fetches the case itself through the adapter.

    Returns ToolResult with data payload:
        timeline view: {finding_id, view, events: [TimelineEvent...],
                        total_events, truncated, top_n}
        evidence view: {finding_id, view, records: [...], record_count,
                        risk_features, streams, complete: true}
    """
    # --- input validation -----------------------------------------------------
    if not isinstance(finding_id, str) or not finding_id.strip():
        return _validation_error("finding_id is required (non-empty string).")
    finding_id = finding_id.strip()

    # Legacy alias → canonical self-opposite view (compatibility only:
    # self_opposite_trades is the canonical name; the alias executes the
    # same bounded, capability-gated view and is recorded on the payload).
    legacy_alias_used = view == SELF_VIEW_LEGACY_ALIAS
    if legacy_alias_used:
        view = SELF_VIEW
    if view not in ALLOWED_VIEWS:
        return _validation_error(
            f"view {view!r} is not supported by finding_drilldown; "
            f"supported views: {sorted(ALLOWED_VIEWS)}."
        )
    if top_n is not None:
        if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
            return _validation_error(
                "top_n must be a positive integer (or omitted for the "
                "complete result).")
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
    # View-scoped: the self-opposite view requires the self_opposite_trades
    # capability (same-account opposite-side composition data exists);
    # every other view keeps the timeline gate. A finding lacking the
    # capability is a bounded unsupported result — never an empty success.
    required_capability = "self_opposite_trades" \
        if view == SELF_VIEW else "timeline"
    if not finding.capabilities.supports(required_capability):
        capability_noun = "self-opposite-trades" \
            if required_capability == "self_opposite_trades" else "timeline"
        return ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(
                code="CAPABILITY_NOT_SUPPORTED",
                message=(
                    f"Finding {finding_id} does not support the "
                    f"{capability_noun} investigation."
                ),
                detail={
                    "capability": required_capability,
                    "scope": "finding",
                    "finding_id": finding_id,
                    "supported_capabilities": sorted(finding.capabilities),
                },
            ),
        )

    # --- fetch raw evidence if not already supplied -----------------------------
    # RP's default response is a top-5 REPRESENTATIVE subset. Both
    # investigation views fetch the COMPLETE record set: a plain "show the
    # timeline" must yield the complete authoritative timeline, never the
    # representative 5. top_n bounds the returned events ONLY as an
    # explicit-user-subset mechanism (planner passes it solely for requests
    # like "show me 5 recent events") — and a bounded result is always
    # flagged truncated=true with the total, never presented as complete.
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
            evidence = RiskPlatformAdapter().fetch_case_evidence(
                str(cid),
                expose_complete_records=True,
            )
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

    # --- self-opposite view: SAME-ACCOUNT opposite-side composition -------------
    # The authoritative semantics are the Risk Platform's own: a count-based
    # opposite_trade_ratio over the account's OWN trade stream
    # (min(BUY, SELL) / total, computed by RP) plus the threshold rule it
    # backs, echoed VERBATIM — never recomputed client-side (no second
    # definition, no pairing, no time/value matching; that is the separate
    # cross_account_trade_analysis tool). The concrete transaction records
    # of the same account ride along as the complete supporting record set.
    if view == SELF_VIEW:
        try:
            payload = normalize_evidence_records(
                case_id=str(case_id or evidence.get("user_id", "")),
                evidence=evidence,
                finding=finding,
                stream="transactions",
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.error("finding_drilldown self-view normalization "
                         "failure: %s", e)
            return ToolResult(
                outcome=ToolResultOutcome.INTEGRATION_ERROR,
                error=ToolError(
                    code="RISK_PLATFORM_MALFORMED_RESPONSE",
                    message=f"Could not normalize transaction records: {e}",
                ),
            )
        features = evidence.get("feature_evidence") or {}
        ratio = features.get("opposite_trade_ratio")
        if ratio is None and payload["record_count"] == 0:
            # Valid finding with the capability, but RP holds neither the
            # aggregate nor any concrete trades: genuinely nothing to show.
            return ToolResult(
                outcome=ToolResultOutcome.EMPTY,
                data={
                    "finding_id": finding_id, "view": SELF_VIEW,
                    "semantics": "same_account_opposite_side",
                    "records": [], "record_count": 0,
                    "complete": True,
                },
                warnings=[
                    "The Risk Platform holds no opposite-side trading "
                    "activity data for this account.",
                ],
            )
        rule = normalize_rule_signal(finding=finding, evidence=evidence)
        data = {
            "finding_id": finding_id,
            "view": SELF_VIEW,
            "semantics": "same_account_opposite_side",
            "account_id": str(evidence.get("user_id") or case_id or ""),
            "opposite_trade_ratio": ratio,
            "ratio_source": "risk_platform_feature_evidence",
            "rule": rule["rule"] if rule else None,
            "records": payload["records"],
            "record_count": payload["record_count"],
            "complete": True,          # completeness is the record contract
        }
        if legacy_alias_used:
            data["requested_view_alias"] = SELF_VIEW_LEGACY_ALIAS
        warnings = [
            "The opposite-trade ratio is the Risk Platform's aggregate "
            "over this account's full trade stream; the records below are "
            "the concrete transaction set — the two are related but never "
            "recombined here.",
        ]
        return ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data=data,
            evidence_refs=[
                EvidenceRef(kind="transaction", id=r["record_id"])
                for r in payload["records"]
            ],
            citation_refs=list(finding.policy_refs),
            warnings=warnings,
        )

    # --- evidence view: COMPLETE concrete records (top_n never applies) ---------
    if view == "evidence":
        # Validate the explicit stream scope against what the finding can
        # legitimately return (bounded unsupported, never silent fallback).
        allowed_streams = {"withdrawals", "transactions"}
        if stream is not None and stream not in allowed_streams:
            return _validation_error(
                f"stream must be one of {sorted(allowed_streams)} or None "
                f"(derive from the finding), got {stream!r}."
            )
        if stream == "withdrawals" and "withdrawal" not in finding.title.lower():
            return ToolResult(
                outcome=ToolResultOutcome.UNSUPPORTED,
                error=ToolError(
                    code="EVIDENCE_STREAM_NOT_SUPPORTED",
                    message=(
                        f"Finding {finding_id} ({finding.title}) has no "
                        "withdrawal evidence stream in the Risk Platform."
                    ),
                    detail={
                        "capability": "evidence_withdrawals",
                        "scope": "finding",
                        "finding_id": finding_id,
                        "requested_stream": stream,
                    },
                ),
            )
        if stream == "transactions" and "transaction" not in finding.title.lower() \
                and not any(
                    kw in finding.title.lower()
                    for kw in ("trade", "trading", "pattern detection")):
            return ToolResult(
                outcome=ToolResultOutcome.UNSUPPORTED,
                error=ToolError(
                    code="EVIDENCE_STREAM_NOT_SUPPORTED",
                    message=(
                        f"Finding {finding_id} ({finding.title}) has no "
                        "transaction evidence stream in the Risk Platform."
                    ),
                    detail={
                        "capability": "evidence_transactions",
                        "scope": "finding",
                        "finding_id": finding_id,
                        "requested_stream": stream,
                    },
                ),
            )
        try:
            payload = normalize_evidence_records(
                case_id=str(case_id or evidence.get("user_id", "")),
                evidence=evidence,
                finding=finding,
                stream=stream,
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.error("finding_drilldown evidence normalization failure: %s", e)
            return ToolResult(
                outcome=ToolResultOutcome.INTEGRATION_ERROR,
                error=ToolError(
                    code="RISK_PLATFORM_MALFORMED_RESPONSE",
                    message=f"Could not normalize evidence records: {e}",
                ),
            )
        if payload["record_count"] == 0:
            return ToolResult(
                outcome=ToolResultOutcome.EMPTY,
                data={
                    "finding_id": finding_id, "view": view,
                    "records": [], "record_count": 0,
                    "risk_features": payload.get("risk_features", {}),
                    "streams": payload.get("streams", {}),
                    "complete": True,
                },
                warnings=[
                    "The Risk Platform holds no concrete withdrawal/"
                    "transaction records for this finding's evidence "
                    "streams; only aggregate feature values exist.",
                ],
            )
        payload["complete"] = True   # completeness is this view's contract
        payload["finding_id"] = finding_id
        payload["view"] = view
        # Provenance: finding-level citations ride along, as in the timeline.
        return ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data=payload,
            evidence_refs=[
                EvidenceRef(kind=r["record_kind"], id=r["record_id"])
                for r in payload["records"]
            ],
            citation_refs=list(finding.policy_refs),
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
    # P4: ordinary request (top_n=None) → COMPLETE timeline. Only an
    # explicit user-requested subset bounds the result, always labeled.
    truncated = top_n is not None and total > top_n
    if truncated:
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
            f"Timeline is a bounded subset: showing top_n={top_n} of "
            f"{total} events at the user's explicit request "
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
        warnings=warnings,
        evidence_refs=[r for e in events for r in e.evidence_refs],
        citation_refs=list(finding.policy_refs),
    )

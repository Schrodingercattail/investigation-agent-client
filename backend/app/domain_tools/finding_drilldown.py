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


def _destination_identity_block(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """WP-GS-B: deterministic destination-identity comparison facts over
    the withdrawal records of an evidence payload.

    Identity = the record's `address` string, trimmed. Comparison is
    EXACT and case-sensitive: RP supplies no chain/network context, so
    no case folding or chain-aware normalization is assumed (case
    variants count as different RECORDED identifiers; the block records
    the applied rule). Shape annotations are descriptive metadata only —
    they never alter equality, and the system claims record-identifier
    equality only, not on-chain destination identity.

    Returns None when the payload contains no withdrawal records (the
    block is a withdrawal-stream fact; transaction-only payloads carry
    no destination identity). Groups preserve first-occurrence order of
    the (timestamp, record_id)-sorted records and carry their
    authoritative withdrawal record IDs as provenance.
    """
    withdrawals = [r for r in records
                   if isinstance(r, dict)
                   and r.get("record_kind") == "withdrawal"]
    if not withdrawals:
        return None
    groups: dict[str, dict[str, Any]] = {}
    with_identity = 0
    without_identity = 0
    for r in withdrawals:
        ident = str(r.get("address") or "").strip()
        if not ident:
            without_identity += 1
            continue
        with_identity += 1
        g = groups.setdefault(ident, {
            "destination_ref": f"dest-{len(groups) + 1}",
            "address": ident,
            "shape": _address_shape(ident),
            "record_ids": [],
        })
        g["record_ids"].append(str(r.get("record_id") or ""))
    return {
        "basis": "withdrawal.address",
        "records_total": len(withdrawals),
        "records_with_identity": with_identity,
        "records_without_identity": without_identity,
        "unique_destinations": len(groups),
        "groups": [
            {"destination_ref": g["destination_ref"],
             "address": g["address"],
             "shape": g["shape"],
             "record_ids": list(g["record_ids"])}
            for g in groups.values()
        ],
        "normalization": "trim; exact case-sensitive (shape descriptive)",
    }


def _population_reconciliation_block(
        evidence: dict[str, Any],
        records: list[dict[str, Any]],
        stream: str | None,
        finding_title: str | None = None) -> dict[str, Any] | None:
    """Cross-domain summary-detail consistency (facts only): reconcile a
    governed 24h-frequency summary metric against the record population
    this evidence view actually exposes, under RP's exposed feature
    reference anchor.

    The metric and the detail table can legitimately disagree in SCOPE:
    e.g. trade_frequency_24h counts trades within 24h before RP's
    reference anchor, while the evidence view exposes the account's full
    stored history. The block makes that scope explicit and
    machine-checkable (claimed vs window-recomputed), so downstream
    presentation can reconcile instead of silently substituting.
    Returns None when the anchor or the governed metric is unavailable
    (older RP payloads) — absence is never treated as consistency.
    """
    feats = (evidence or {}).get("feature_evidence") or {}
    anchor = feats.get("feature_reference_time")
    metric_key = ("withdrawal_frequency_24h" if stream == "withdrawals"
                  else "trade_frequency_24h" if stream == "transactions"
                  else None)
    claimed = feats.get(metric_key) if metric_key else None
    if not anchor or claimed is None:
        return None
    from datetime import datetime, timedelta
    try:
        anchor_dt = datetime.fromisoformat(str(anchor))
        window_start = anchor_dt - timedelta(hours=24)
        in_window = 0
        for r in records:
            ts = r.get("timestamp")
            if not ts:
                continue
            t = datetime.fromisoformat(str(ts))
            if t.tzinfo is None and anchor_dt.tzinfo is not None:
                t = t.replace(tzinfo=anchor_dt.tzinfo)
            if window_start <= t <= anchor_dt:
                in_window += 1
    except (ValueError, TypeError):
        return None
    entries = []
    if in_window == len(records) == claimed:
        # metric, window and detail population all agree — nothing to
        # reconcile (no scope difference exists)
        pass
    else:
        entries.append({
            "type": "window_count",
            "metric": metric_key,
            "claimed": claimed,
            "observed_in_window": in_window,
            "exposed_total": len(records),
            "outside_window": len(records) - in_window,
            "verdict": ("consistent_scoped_window" if in_window == claimed
                        else "population_mismatch"),
        })
    # Non-numeric rule claims over the exposed records (frozen RP rule
    # catalog names → deterministic record predicates). A governed rule
    # claim without a registered predicate is reported as unmapped.
    title = (finding_title or "").strip().lower()
    registered_claims = set()
    if stream == "withdrawals" and records \
            and title == "first withdrawal to new address.":
        registered_claims.add("first_withdrawal_new_address")
        ordered = sorted(
            (r for r in records if r.get("timestamp")),
            key=lambda r: str(r.get("timestamp")))
        first_new = (ordered[0].get("is_new_address") is True)
        entries.append({
            "type": "rule_claim",
            "claim": "first_withdrawal_new_address",
            "observed": first_new,
            "verdict": ("claim_supported" if first_new
                        else "claim_contradicted"),
        })
    if stream == "transactions" and records:
        registered_claims.add("opposite_trade_ratio")
        sides = [str(r.get("side")).upper() for r in records
                 if r.get("side")]
        ratio_claimed = feats.get("opposite_trade_ratio")
        if len(sides) >= 2 and "BUY" in sides and "SELL" in sides \
                and ratio_claimed is not None:
            observed = min(sides.count("BUY"),
                           sides.count("SELL")) / len(sides)
            agrees = abs(float(ratio_claimed) - observed) <= 0.0005
            entries.append({
                "type": "ratio_recompute",
                "metric": "opposite_trade_ratio",
                "claimed": float(ratio_claimed),
                "observed": round(observed, 4),
                "denominator": len(sides),
                "verdict": ("claim_supported" if agrees
                            else "population_mismatch"),
            })
    unmapped = []
    # governed frequency/ratio metrics are stream-scoped: the withdrawal
    # evidence view answers withdrawal metrics; the transaction view
    # answers trade metrics + the ratio. A governed metric present in
    # feature_evidence but with no registered verification for THIS
    # view is reported unmapped (never silently "verified").
    if stream == "transactions":
        governed = ("trade_frequency_24h", "trade_frequency_7d",
                    "opposite_trade_ratio")
        registered = {metric_key, "opposite_trade_ratio"}
    elif stream == "withdrawals":
        governed = ("withdrawal_frequency_24h",
                    "withdrawal_new_address_share")
        registered = {metric_key} | registered_claims
    else:
        governed, registered = (), set()
    for k in governed:
        if feats.get(k) is not None and k not in registered:
            unmapped.append(k)
    if not entries and not unmapped:
        return None
    worst = "claim_supported"
    for e in entries:
        if e["verdict"] in ("population_mismatch", "claim_contradicted"):
            worst = e["verdict"]
            break
        if e["verdict"] == "consistent_scoped_window":
            worst = "consistent_scoped_window"
    if unmapped and worst in ("claim_supported",
                              "consistent_scoped_window"):
        worst = "unmapped_metrics_present"
    return {
        "reference_time": str(anchor),
        "window_start": window_start.isoformat() if window_start else None,
        "metrics": entries,
        "unmapped_metrics": unmapped,
        "verdict": worst,
    }


def _new_address_status_block(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Authoritative per-record is_new_address status over the
    withdrawal records (facts only — RP's is_new_address field is the
    ONLY novelty authority; destination distinctness is NEVER used to
    infer it). Missing/blank values are counted as unverified."""
    withdrawals = [r for r in records
                   if isinstance(r, dict)
                   and r.get("record_kind") == "withdrawal"]
    if not withdrawals:
        return None
    rows = []
    true_n = false_n = missing_n = 0
    for r in withdrawals:
        raw = r.get("is_new_address")
        rid = str(r.get("record_id") or "")
        if raw is True:
            status = True
            true_n += 1
        elif raw is False:
            status = False
            false_n += 1
        else:
            status = None                       # missing/unverifiable
            missing_n += 1
        rows.append({"record_id": rid, "is_new_address": status})
    return {"basis": "withdrawal.is_new_address",
            "records_total": len(withdrawals),
            "true_count": true_n, "false_count": false_n,
            "missing_count": missing_n,
            "records": rows}


def _address_shape(ident: str) -> str:
    """Descriptive shape annotation (never used for equality)."""
    low = ident.lower()
    if low.startswith("0x") and len(ident) >= 3 \
            and all(c in "0123456789abcdef" for c in low[2:]):
        return "hex_0x"
    if low.startswith(("bc1", "tb1")):
        return "bech32"
    if len(ident) >= 26 and not any(c in ident for c in "0OIl"):
        return "base58"
    return "unclassified"


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
        # WP-GS-B: deterministic destination-identity comparison facts
        # over the withdrawal records (facts only — the same/different
        # conclusion is drawn downstream by the GSP-4 evaluator).
        identity_block = _destination_identity_block(payload["records"])
        if identity_block is not None:
            payload["destination_identity"] = identity_block
        # Cross-domain consistency: authoritative per-record
        # is_new_address status for the withdrawal population (F4-class /
        # 哪些笔提现是新地址 deliverables — keyed by withdrawal record ID).
        new_addr_block = _new_address_status_block(payload["records"])
        if new_addr_block is not None:
            payload["new_address_status"] = new_addr_block
        # Cross-domain consistency: the case's actually-triggered rule
        # evidence (rule_name/severity/trigger/threshold/contribution) —
        # source-backed detection-decision provenance for rule-backed
        # explanations.
        rule_ev = evidence.get("rule_evidence") or []
        if rule_ev:
            payload["rule_evidence"] = rule_ev
        # Cross-domain summary-detail consistency: reconcile the governed
        # 24h-frequency summary metrics against the exposed record
        # population, using RP's exposed feature reference anchor.
        reconciliation = _population_reconciliation_block(
            evidence, payload["records"], stream,
            finding_title=getattr(finding, "title", None))
        if reconciliation is not None:
            payload["population_reconciliation"] = reconciliation
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

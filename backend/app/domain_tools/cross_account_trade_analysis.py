"""cross_account_trade_analysis — derived analytical domain tool.

Identifies CANDIDATE cross-account opposite-trade relationships from
authoritative Risk Platform transaction evidence, per the frozen V1
contract in docs/baselines/2026-09-18-cross-account-trade-analysis-v1.md
(§5 rules, §12b parameters, §6 observation contract).

Boundary:

- The tool is Agent-owned DERIVED ANALYSIS: it computes candidate
  relationships deterministically; every cited record remains Risk
  Platform evidence (transaction ids + account attribution preserved).
- Retrieval goes exclusively through the §12a logical retrieval layer
  (`app.trade_evidence.retrieve_trade_evidence`) — this module never
  talks to the Risk Platform adapter directly and never accepts raw
  transaction data from the planner/LLM.
- All thresholds come from system configuration (`TRADE_ANALYSIS_*`
  settings); they are never caller- or LLM-chosen, and every payload
  echoes the values actually applied.
- Results are CANDIDATE relationships under configured analytical
  tolerances — never confirmed coordinated trading, collusion, or
  intent. The ranking is an ordering device, not a risk score.

Matching (frozen V1): distinct accounts · same symbol · opposite side ·
|Δt| ≤ max_time_delta (inclusive, UTC) · value within tolerance
(|Δv| ≤ max(absolute_floor, relative × max(v_a, v_b))) · aggregation is
1_to_1 / 1_to_N / N_to_1 only (single anchor, greedy timestamp-order
accumulation, leg cap, no backtracking) · every transaction id appears
in at most one candidate.

Target-account scope (Case-4 correction): the case account is the
ANALYSIS TARGET. Its own transactions are retrieved completely (within
budget) as the anchor universe; counterparty (cluster-connected)
transactions are retrieved bounded to the time window matching can
reach; and only target-anchored candidates are computed - every emitted
candidate contains at least one target transaction, so global pairs
between unrelated accounts can never be presented as the target
account's cross-account evidence. The authoritative same-account
opposite_trade_ratio feature is echoed (best-effort) so the same-account
signal and the cross-account analysis stay explicitly distinct
(baseline S8).
"""

import logging
from datetime import datetime, timedelta as _timedelta
from typing import Any

from app.adapters.risk_platform import RiskPlatformAdapter
from app.models import (
    EvidenceRef,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.trade_evidence import (
    AccountTransaction,
    AccountScope,
    RetrievalStatus,
    TradeEvidenceQuery,
    TradeEvidenceRetrieval,
    resolve_account_scope,
    retrieve_trade_evidence,
)

logger = logging.getLogger(__name__)

# The one supported analysis (locked by the step binding; the planner can
# never emit another value).
ANALYSIS_TYPE = "cross_account_opposite_trade"


def _validation_error(message: str) -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.VALIDATION_ERROR,
        error=ToolError(code="INVALID_ARGUMENT", message=message),
    )


# ---------------------------------------------------------------------------
# Deterministic matching engine (pure functions; unit-tested directly)
# ---------------------------------------------------------------------------

def _parse_ts(raw: str | None) -> datetime | None:
    """Parse an RP ISO-8601 timestamp into a comparable instant. None for
    absent/unparseable values — such records are excluded from matching
    and surfaced in data quality (never given synthetic times)."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _side_key(side: str) -> str:
    return (side or "").strip().upper()


class _Candidate:
    """One candidate relationship under construction (internal)."""

    __slots__ = ("anchor", "legs")

    def __init__(self, anchor: AccountTransaction,
                 legs: list[AccountTransaction]):
        self.anchor = anchor
        self.legs = legs


def _candidate_view(candidate: _Candidate) -> dict[str, Any]:
    """Frozen §6 candidate record (deterministic field order).

    Side naming (§12b.5): account_a owns the earliest leg timestamp;
    ties go to the lexicographically smaller account id. match_type is
    expressed relative to side_a/side_b."""
    anchor, legs = candidate.anchor, candidate.legs
    members = [anchor, *legs]
    a_side = _side_key(anchor.side)
    b_side = _side_key(legs[0].side)

    def sort_key(t: AccountTransaction):
        return (t.timestamp or "", t.transaction_id)

    def account_key(t: AccountTransaction):
        ts = _parse_ts(t.timestamp)
        epoch = datetime.max.replace(tzinfo=ts.tzinfo) if ts else None
        return ((ts or epoch), t.account_id)

    earliest_a = min((t for t in members if _side_key(t.side) == a_side),
                     key=account_key)
    earliest_b = min((t for t in members if _side_key(t.side) == b_side),
                     key=account_key)
    # account_a = earlier earliest-leg; tie → lexicographically smaller id
    ea_ts, eb_ts = (_parse_ts(earliest_a.timestamp),
                    _parse_ts(earliest_b.timestamp))
    if (eb_ts, earliest_b.account_id) < (ea_ts, earliest_a.account_id):
        account_a, account_b = earliest_b.account_id, earliest_a.account_id
        side_a, side_b = b_side, a_side
    else:
        account_a, account_b = earliest_a.account_id, earliest_b.account_id
        side_a, side_b = a_side, b_side

    ids_a = sorted(t.transaction_id for t in members
                   if t.account_id == account_a)
    ids_b = sorted(t.transaction_id for t in members
                   if t.account_id == account_b)
    value_a = sum(t.value for t in members if t.account_id == account_a)
    value_b = sum(t.value for t in members if t.account_id == account_b)
    times = sorted(_parse_ts(t.timestamp) for t in members)
    time_gap = (times[-1] - times[0]).total_seconds()
    difference = abs(value_a - value_b)
    relative = difference / max(value_a, value_b) if max(value_a, value_b) else 0.0
    if len(ids_a) == 1 and len(ids_b) == 1:
        match_type = "1_to_1"
    elif len(ids_a) == 1:
        match_type = "1_to_N"
    else:
        match_type = "N_to_1"

    timestamps = sorted(t.timestamp for t in members if t.timestamp)
    return {
        "account_a": account_a,
        "account_b": account_b,
        "symbol": anchor.symbol,
        "side_a": side_a,
        "side_b": side_b,
        "transaction_ids_a": ids_a,
        "transaction_ids_b": ids_b,
        "timestamps": timestamps,
        "max_time_gap_seconds": time_gap,
        "value_a": value_a,
        "value_b": value_b,
        "value_difference": difference,
        "value_difference_relative": relative,
        "match_type": match_type,
        "supporting": [
            {"transaction_id": t.transaction_id, "account_id": t.account_id,
             "symbol": t.symbol, "side": t.side, "price": t.price,
             "quantity": t.quantity, "value": t.value,
             "timestamp": t.timestamp}
            for t in sorted(members, key=sort_key)
        ],
    }


def _value_matches(v_anchor: float, v_sum: float, cfg: dict[str, float]) -> bool:
    tolerance = max(cfg["value_tolerance_absolute_floor"],
                    cfg["value_tolerance_relative"] * max(v_anchor, v_sum))
    return abs(v_anchor - v_sum) <= tolerance


def find_candidates(
    records: list[AccountTransaction],
    config: dict[str, Any],
    target_account: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministic V1 matching over normalized records.

    Returns (ranked candidate views, data-quality facts). Pure function
    of (records, config): identical inputs produce identical output.

    Algorithm (frozen §12b.4): records are ordered by (timestamp, id);
    each unconsumed record is an ANCHOR — a 1:1 opposite-side match is
    attempted first, then bounded greedy accumulation of opposite-side
    legs in timestamp order (each leg within the anchor's time window
    individually, ≤ max_legs, no backtracking). Consumed transactions
    never anchor again and never join another candidate. Unparseable-
    timestamp records are excluded (surfaced in data quality).

    ``target_account``: when set, only records of THIS account may
    anchor a candidate (case-account-anchored analysis — every emitted
    candidate then contains at least one target transaction by
    construction, so unrelated global pairs can never be presented as
    the target account's cross-account evidence). Legs may still come
    from any other in-scope account; all §5 matching rules (distinct
    accounts, same symbol, opposite side, time delta, value tolerance,
    bounded aggregation, single-use consumption) are unchanged. None
    keeps the legacy every-record-may-anchor behavior.
    """
    max_delta = float(config["max_time_delta_seconds"])
    max_legs = int(config["max_legs_per_candidate"])

    excluded_untimestamped = 0
    usable: list[AccountTransaction] = []
    for r in records:
        if _parse_ts(r.timestamp) is None:
            excluded_untimestamped += 1
        else:
            usable.append(r)

    # Deterministic global order; consumed ids block reuse everywhere.
    ordered = sorted(usable, key=lambda t: (
        _parse_ts(t.timestamp), t.transaction_id))
    consumed: set[str] = set()
    raw_candidates: list[_Candidate] = []

    for anchor in ordered:
        if anchor.transaction_id in consumed:
            continue
        if target_account is not None and anchor.account_id != target_account:
            continue                     # target-anchored: only the target
        anchor_ts = _parse_ts(anchor.timestamp)
        anchor_side = _side_key(anchor.side)
        if not anchor_side:
            continue
        # Opposite-side legs in deterministic order, each individually
        # within the anchor's time window; unconsumed only.
        legs_pool = [
            t for t in ordered
            if t.transaction_id not in consumed
            and t.transaction_id != anchor.transaction_id
            and t.account_id != anchor.account_id        # distinct accounts
            and t.symbol == anchor.symbol                # same instrument
            and _side_key(t.side) not in ("", anchor_side)  # opposite side
            and abs((_parse_ts(t.timestamp) - anchor_ts).total_seconds())
                <= max_delta
        ]
        if not legs_pool:
            continue

        # A candidate record has exactly account_a/account_b (§6), so the
        # anchor aggregates legs of ONE counterparty account: the earliest
        # opposite leg's account (deterministic).
        counterparty = legs_pool[0].account_id
        counterparty_legs = [t for t in legs_pool
                             if t.account_id == counterparty]

        # 1:1 first: earliest opposite leg whose value alone matches.
        match: _Candidate | None = None
        for leg in counterparty_legs:
            if _value_matches(anchor.value, leg.value, config):
                match = _Candidate(anchor, [leg])
                break
        # Bounded 1:N / N:1: greedy timestamp-order accumulation, no
        # backtracking; overshoot beyond tolerance abandons the anchor.
        if match is None:
            total = 0.0
            acc: list[AccountTransaction] = []
            for leg in counterparty_legs:
                if len(acc) >= max_legs:
                    break
                total += leg.value
                acc.append(leg)
                if _value_matches(anchor.value, total, config):
                    match = _Candidate(anchor, list(acc))
                    break
                if total - anchor.value > max(
                        config["value_tolerance_absolute_floor"],
                        config["value_tolerance_relative"]
                        * max(anchor.value, total)):
                    break                     # overshoot — no backtracking
        if match is None:
            continue
        raw_candidates.append(match)
        consumed.add(anchor.transaction_id)
        consumed.update(leg.transaction_id for leg in match.legs)

    # Ranking (§12b.5): relative value difference → max time gap → leg
    # count, ascending; first-transaction-id as the stable final tie-break.
    # Rank and candidate id are assigned HERE (deterministic output of the
    # engine); the tool applies the top-K emission cap afterwards.
    views = [_candidate_view(c) for c in raw_candidates]
    views.sort(key=lambda v: (
        v["value_difference_relative"],
        v["max_time_gap_seconds"],
        len(v["transaction_ids_a"]) + len(v["transaction_ids_b"]),
        min(v["transaction_ids_a"] + v["transaction_ids_b"]),
    ))
    for rank, view in enumerate(views, start=1):
        view["rank"] = rank
        view["candidate_id"] = f"CATA-{rank:03d}"
    data_quality = {
        "records_input": len(records),
        "records_usable": len(usable),
        "records_excluded_untimestamped": excluded_untimestamped,
        "transactions_matched": len(consumed),
    }
    return views, data_quality


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

# Governed analysis scopes (system-selected, never LLM-chosen):
# - "target_account": Case-4 semantics — every candidate must contain the
#   case account (the analysis target).
# - "related_account_set": the user asked for hedging WITHIN a named
#   related-account population. The analysis set is
#   S = {case account} ∪ {authoritative related accounts}; candidate
#   pairs may involve the case account or only two related members.
ANALYSIS_SCOPES = ("target_account", "related_account_set")


def cross_account_trade_analysis(
    case_id: str,
    finding_id: str | None = None,
    analysis_type: str = ANALYSIS_TYPE,
    analysis_scope: str = "target_account",
    adapter: Any | None = None,
    config: Any | None = None,
) -> ToolResult:
    """Identify candidate cross-account opposite-trade relationships.

    Args:
        case_id: the investigated case (executor-injected; never
            planner/LLM-supplied in the live path).
        finding_id: optional focused finding (executor-injected) — recorded
            on the payload as the investigation anchor; V1 performs no
            finding-scoped filtering.
        analysis_type: locked by the step binding to ANALYSIS_TYPE; any
            other value is a validation error.
        adapter/config: injection seams for tests; defaults to the real
            adapter and app settings.

    Returns ToolResult with the §6 observation contract:
        MATCH_FOUND / NO_MATCH → outcome="success";
        INSUFFICIENT_DATA → outcome="success" + evidence_missing;
        ERROR → outcome="integration_error" (never absence-of-data).
    """
    if config is None:
        from app.config import settings as config
    # --- input validation ---------------------------------------------------
    if not isinstance(case_id, str) or not case_id.strip():
        return _validation_error(
            "case_id is required for cross_account_trade_analysis "
            "(non-empty string).")
    case_id = case_id.strip()
    if analysis_type != ANALYSIS_TYPE:
        return _validation_error(
            f"analysis_type {analysis_type!r} is not supported; the only "
            f"registered analysis is {ANALYSIS_TYPE!r}.")
    if analysis_scope not in ANALYSIS_SCOPES:
        return _validation_error(
            f"analysis_scope {analysis_scope!r} is not supported; the "
            f"registered scopes are {list(ANALYSIS_SCOPES)}.")
    finding_id = finding_id.strip() if isinstance(finding_id, str) \
        and finding_id.strip() else None

    matching_config = {
        "max_time_delta_seconds":
            config.TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS,
        "value_tolerance_relative":
            config.TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE,
        "value_tolerance_absolute_floor":
            config.TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR,
        "max_legs_per_candidate":
            config.TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE,
        "max_candidates": config.TRADE_ANALYSIS_MAX_CANDIDATES,
    }

    # --- authoritative account scope (case account + cluster-connected) ----
    scope: AccountScope = resolve_account_scope(
        case_id, adapter=adapter,
        max_accounts=config.TRADE_ANALYSIS_MAX_ACCOUNTS, config=config)
    if scope.error is not None:
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code=scope.error.code,
                message=(
                    "The cross-account trade analysis could not resolve the "
                    f"authoritative account scope for case {case_id}: "
                    f"{scope.error.message}"),
                detail=dict(scope.error.detail),
            ),
        )

    # --- target-anchored bounded retrieval (two §12a streams) ---------------
    # The analysis question is whether THE CASE ACCOUNT hedges with other
    # accounts, so the case account is the ANALYSIS TARGET: its own
    # transaction set is the anchor universe and must be retrieved
    # COMPLETELY (within budget) — a shared global retrieval window can
    # truncate the target's own records away and would let unrelated
    # pairs stand in for target evidence. Two bounded streams:
    #
    # 1. the target account's own transactions (complete within budget);
    # 2. the counterparty (cluster-connected) transactions, bounded to
    #    the time window matching can reach: [min_target_ts − Δ,
    #    max_target_ts + Δ] over the target's parseable timestamps. A leg
    #    outside that window can never satisfy |Δt| ≤ Δ with any target
    #    anchor, so the window loses zero reachable pairs while keeping
    #    the counterparty retrieval complete-for-purpose in normal data.
    target_account = case_id
    target_retrieval: TradeEvidenceRetrieval = retrieve_trade_evidence(
        TradeEvidenceQuery(
            case_id=case_id,
            account_ids=[target_account],
        ),
        adapter=adapter,
        allowed_accounts=scope.allowed,
        config=config,
    )

    def _stream_error(stream: str, ret: TradeEvidenceRetrieval) -> ToolResult:
        err = ret.error
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code=err.code if err else "RISK_PLATFORM_ERROR",
                message=(
                    "The cross-account trade analysis could not retrieve "
                    f"the {stream} transaction evidence for case {case_id}: "
                    f"{err.message if err else 'unknown retrieval failure'}"),
                detail=dict(err.detail) if err else {},
            ),
        )

    if analysis_scope == "related_account_set":
        # SET-INTERNAL analysis: every member of S = {case account} ∪
        # {related accounts} is an equal analysis candidate — candidate
        # pairs may involve the case account or only two related
        # members. One bounded union retrieval over the whole set
        # (truncation honestly flagged; a partial set can never support
        # a set-wide no-match), then unanchored matching restricted to
        # the retrieved set (both legs of every pair are S members by
        # construction - accounts outside S are never retrieved).
        set_retrieval: TradeEvidenceRetrieval = retrieve_trade_evidence(
            TradeEvidenceQuery(
                case_id=case_id,
                account_ids=sorted(scope.allowed),
            ),
            adapter=adapter,
            allowed_accounts=scope.allowed,
            config=config,
        )
        if set_retrieval.status == RetrievalStatus.ERROR:
            return _stream_error("account set", set_retrieval)

        records = []
        seen_ids: set[str] = set()
        duplicates_dropped = 0
        for r in set_retrieval.records:
            if r.transaction_id in seen_ids:
                duplicates_dropped += 1
                continue
            seen_ids.add(r.transaction_id)
            records.append(r)
        truncated_reasons = [
            f"account_set:{reason}"
            for reason in set_retrieval.truncated_reasons]
        merged_truncated = (
            set_retrieval.status == RetrievalStatus.TRUNCATED)
        merged_status = (
            RetrievalStatus.TRUNCATED if merged_truncated
            else (RetrievalStatus.NO_DATA
                  if not records
                  and not (set_retrieval.total_matched or 0)
                  else RetrievalStatus.COMPLETE))

        def _set_stream_echo(ret: TradeEvidenceRetrieval) -> dict[str, Any]:
            return {
                "status": ret.status.value,
                "truncated": ret.status == RetrievalStatus.TRUNCATED,
                "transactions_retrieved": len(ret.records),
                "total_matched": ret.total_matched,
            }

        def _payload(**extra: Any) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "analysis": "cross_account_trade_analysis",
                "analysis_type": ANALYSIS_TYPE,
                "analysis_scope": analysis_scope,
                "case_id": case_id,
                "finding_id": finding_id,
                "target_account": target_account,
                "accounts_analyzed": sorted(scope.allowed),
                "config": matching_config,
                "retrieval": {
                    "status": merged_status.value,
                    "truncated": merged_truncated,
                    "truncated_reasons": truncated_reasons,
                    "account_set": {
                        **_set_stream_echo(set_retrieval),
                        "accounts_queried": len(scope.allowed),
                    },
                },
                "totals": {
                    "transactions_retrieved": len(records),
                    "total_matched": (set_retrieval.total_matched or 0),
                    "pages_fetched": set_retrieval.pages_fetched,
                },
            }
            payload.update(extra)
            return payload

        warnings: list[str] = list(set_retrieval.warnings)
        if not records:
            payload = _payload(
                analysis_status="INSUFFICIENT_DATA",
                evidence_missing=True,
                data_quality={"records_input": 0},
                next_data_needed=[
                    "account-attributed transaction evidence for the "
                    f"related-account set of {target_account}",
                ],
            )
            return ToolResult(
                outcome=ToolResultOutcome.SUCCESS,
                data=payload,
                next_data_needed=list(payload["next_data_needed"]),
                warnings=warnings,
            )

        candidates, engine_dq = find_candidates(records, matching_config)
        data_quality = {**engine_dq,
                        "records_duplicate_dropped": duplicates_dropped}
        found = len(candidates)
        emitted = candidates[:int(matching_config["max_candidates"])]
        if found:
            analysis_status = "MATCH_FOUND"
        elif merged_truncated:
            analysis_status = "INSUFFICIENT_DATA"
        else:
            analysis_status = "NO_MATCH"
        payload = _payload(
            analysis_status=analysis_status,
            candidates=emitted if found else [],
            candidates_found=found,
            candidates_emitted=len(emitted),
            data_quality=data_quality,
        )
        if found > len(emitted):
            warnings.append(
                f"{found} candidate relationships were found; the top "
                f"{len(emitted)} are reported (TRADE_ANALYSIS_MAX_CANDIDATES="
                f"{matching_config['max_candidates']}).")
        if merged_truncated and found:
            warnings.append(
                "Retrieval was bounded (partial set coverage); candidates "
                "are valid but absence of further set-internal matches is "
                "not established.")
        next_needed = None
        if analysis_status == "INSUFFICIENT_DATA":
            payload["evidence_missing"] = True
            payload["next_data_needed"] = [
                "complete (non-truncated) transaction evidence for the "
                "related-account set",
            ]
            next_needed = list(payload["next_data_needed"])
        return ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data=payload,
            evidence_refs=[
                EvidenceRef(kind="transaction", id=tid)
                for c in emitted
                for tid in sorted(c["transaction_ids_a"]
                                  + c["transaction_ids_b"])
            ],
            warnings=warnings,
            next_data_needed=next_needed,
        )

    # Either stream's retrieval failure is an ERROR — never absence of data.
    if target_retrieval.status == RetrievalStatus.ERROR:
        return _stream_error("target account", target_retrieval)

    delta = float(matching_config["max_time_delta_seconds"])
    target_times = sorted(
        t for t in (_parse_ts(r.timestamp)
                    for r in target_retrieval.records)
        if t is not None)
    window_start = (target_times[0] - _timedelta(seconds=delta)).isoformat() \
        if target_times else None
    window_end = (target_times[-1] + _timedelta(seconds=delta)).isoformat() \
        if target_times else None
    counterparty_accounts = sorted(scope.allowed - {target_account})
    counterparty_retrieval: TradeEvidenceRetrieval = retrieve_trade_evidence(
        TradeEvidenceQuery(
            case_id=case_id,
            account_ids=counterparty_accounts,
            start_time=window_start,
            end_time=window_end,
        ),
        adapter=adapter,
        allowed_accounts=scope.allowed,
        config=config,
    )
    if counterparty_retrieval.status == RetrievalStatus.ERROR:
        return _stream_error("counterparty accounts", counterparty_retrieval)

    # --- merge (populations are disjoint by query; dedupe is a safety net) --
    records: list[AccountTransaction] = []
    seen_ids: set[str] = set()
    duplicates_dropped = 0
    for r in [*target_retrieval.records, *counterparty_retrieval.records]:
        if r.transaction_id in seen_ids:
            duplicates_dropped += 1
            continue
        seen_ids.add(r.transaction_id)
        records.append(r)

    truncated_reasons = [
        f"target_account:{reason}"
        for reason in target_retrieval.truncated_reasons
    ] + [
        f"counterparty_accounts:{reason}"
        for reason in counterparty_retrieval.truncated_reasons
    ]
    # The stream STATUS is the authoritative truncation flag (§12a);
    # truncated_reasons are detail and may be empty on a flagged stream.
    merged_truncated = (
        target_retrieval.status == RetrievalStatus.TRUNCATED
        or counterparty_retrieval.status == RetrievalStatus.TRUNCATED)
    if merged_truncated:
        merged_status = RetrievalStatus.TRUNCATED
    elif not records and not (target_retrieval.total_matched or 0) \
            and not (counterparty_retrieval.total_matched or 0):
        merged_status = RetrievalStatus.NO_DATA
    else:
        merged_status = RetrievalStatus.COMPLETE

    def _stream_echo(ret: TradeEvidenceRetrieval) -> dict[str, Any]:
        return {
            "status": ret.status.value,
            "truncated": ret.status == RetrievalStatus.TRUNCATED,
            "transactions_retrieved": len(ret.records),
            "total_matched": ret.total_matched,
        }

    def _payload(**extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "analysis": "cross_account_trade_analysis",
            "analysis_type": ANALYSIS_TYPE,
            "analysis_scope": analysis_scope,
            "case_id": case_id,
            "finding_id": finding_id,
            "target_account": target_account,
            "accounts_analyzed": sorted(scope.allowed),
            "config": matching_config,
            "retrieval": {
                "status": merged_status.value,
                "truncated": merged_truncated,
                "truncated_reasons": truncated_reasons,
                "target": _stream_echo(target_retrieval),
                "counterparty": {
                    **_stream_echo(counterparty_retrieval),
                    "accounts_queried": len(counterparty_accounts),
                    "time_window": (
                        {"start": window_start, "end": window_end}
                        if window_start and window_end else None),
                },
            },
            "totals": {
                "transactions_retrieved": len(records),
                "total_matched":
                    (target_retrieval.total_matched or 0)
                    + (counterparty_retrieval.total_matched or 0),
                "pages_fetched": target_retrieval.pages_fetched
                + counterparty_retrieval.pages_fetched,
            },
        }
        payload.update(extra)
        return payload

    # Best-effort authoritative account metric (the same-account
    # opposite_trade_ratio feature): context that keeps the same-account
    # signal and the cross-account analysis explicitly distinct (baseline
    # §8). Never gates the analysis; unavailability is surfaced, not fatal.
    target_metrics: dict[str, Any] | None = None
    metrics_warning: str | None = None
    try:
        evidence = (adapter or RiskPlatformAdapter()).fetch_case_evidence(
            case_id)
        ratio = (evidence.get("feature_evidence") or {}).get(
            "opposite_trade_ratio")
        if isinstance(ratio, (int, float)):
            target_metrics = {
                "opposite_trade_ratio": float(ratio),
                "source": "risk_platform.feature_evidence",
            }
    except Exception as exc:                      # noqa: BLE001 — bounded
        metrics_warning = (
            "The authoritative opposite_trade_ratio metric for account "
            f"{target_account} could not be retrieved: "
            f"{type(exc).__name__}.")

    warnings: list[str] = []
    if metrics_warning:
        warnings.append(metrics_warning)

    if not records:
        # Complete-and-empty or bounded-to-nothing: INSUFFICIENT_DATA —
        # never NO_MATCH (absence from nothing proves nothing) and never
        # an integration error (valid execution, insufficient evidence).
        payload = _payload(
            analysis_status="INSUFFICIENT_DATA",
            evidence_missing=True,
            data_quality={"records_input": 0},
            next_data_needed=[
                "account-attributed transaction evidence for the target "
                f"account {target_account} and its connected accounts",
            ],
        )
        if target_metrics:
            payload["target_account_metrics"] = target_metrics
        return ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data=payload,
            next_data_needed=list(payload["next_data_needed"]),
            warnings=warnings,
        )

    candidates, engine_dq = find_candidates(
        records, matching_config, target_account=target_account)
    data_quality = {
        **engine_dq,
        "target_transactions_retrieved": len(target_retrieval.records),
        "counterparty_transactions_retrieved":
            len(counterparty_retrieval.records),
        "records_duplicate_dropped": duplicates_dropped,
    }
    found = len(candidates)
    emitted = candidates[:int(matching_config["max_candidates"])]

    if found:
        analysis_status = "MATCH_FOUND"
    elif merged_truncated:
        # Absence within a partial dataset proves nothing (§6 mapping).
        analysis_status = "INSUFFICIENT_DATA"
    else:
        analysis_status = "NO_MATCH"

    payload = _payload(
        analysis_status=analysis_status,
        candidates=emitted if found else [],
        candidates_found=found,
        candidates_emitted=len(emitted),
        data_quality=data_quality,
    )
    if target_metrics:
        payload["target_account_metrics"] = target_metrics
    warnings = [*warnings,
                *target_retrieval.warnings, *counterparty_retrieval.warnings]
    if found > len(emitted):
        warnings.append(
            f"{found} candidate relationships were found; the top "
            f"{len(emitted)} are reported (TRADE_ANALYSIS_MAX_CANDIDATES="
            f"{matching_config['max_candidates']}).")
    if merged_truncated and found:
        warnings.append(
            "Retrieval was bounded (partial evidence); candidates are "
            "valid but absence of further matches is not established.")

    next_needed = None
    if analysis_status == "INSUFFICIENT_DATA":
        payload["evidence_missing"] = True
        payload["next_data_needed"] = [
            "complete (non-truncated) transaction evidence for the "
            "analyzed accounts",
        ]
        next_needed = list(payload["next_data_needed"])

    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data=payload,
        evidence_refs=[
            EvidenceRef(kind="transaction", id=tid)
            for c in emitted
            for tid in sorted(c["transaction_ids_a"]
                              + c["transaction_ids_b"])
        ],
        warnings=warnings,
        next_data_needed=next_needed,
    )

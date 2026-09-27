"""Bounded trade-evidence retrieval — the data-contract layer beneath
``cross_account_trade_analysis``.

Phase scope (docs/baselines/2026-09-18-cross-account-trade-analysis-v1.md,
OQ-4 resolution): this module provides the RETRIEVAL capability only —
a safe, bounded, explicit contract for authoritative transaction
evidence. It performs NO matching (no pairing, no opposite-side logic,
no tolerance comparison); that belongs to the analytical phase.

Boundary (the reason this module exists):

- The logical request identifies investigation SCOPE (case, accounts,
  time window, symbols) — never raw records, never free-form queries.
- Raw transaction rows stay inside tool execution. ``records`` feed the
  deterministic analysis layer; ``observation`` is the ONLY payload that
  may reach the planner/LLM/response composer, and it carries counts,
  ranges, and flags — never transaction rows.
- Account scope is resolved from AUTHORITATIVE case context (the case
  account plus cluster-connected accounts from RP network signals).
  Requested accounts outside that scope are a bounded ERROR (listed) —
  never silently narrowed, never an arbitrary expansion of the account
  universe. The Risk Platform enforces the same scope server-side.
- Retrieval budgets are system configuration (``TRADE_ANALYSIS_*``
  settings), never LLM-chosen parameters. A partial retrieval is always
  labeled TRUNCATED — absence from a partial dataset must never be
  composed as a completed negative result.
- The source of truth remains the Risk Platform; records preserve the
  transaction ids and account attribution needed for provenance.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.adapters.risk_platform import (
    RiskPlatformAdapter,
    RiskPlatformError,
)


# ---------------------------------------------------------------------------
# Contract models
# ---------------------------------------------------------------------------

class RetrievalStatus(str, Enum):
    """Completeness semantics of one retrieval (baseline §5).

    - COMPLETE  : the full authoritative population under the requested
                  scope was retrieved (server reported no more rows and
                  no budget engaged).
    - NO_DATA   : complete AND empty — the platform holds no matching
                  rows. Distinct from ERROR and from TRUNCATED.
    - TRUNCATED : a partial view (budget or server bounds engaged).
                  Absence of a match in a TRUNCATED retrieval proves
                  nothing; consumers must treat it as incomplete.
    - ERROR     : the retrieval did not complete (scope violation,
                  invalid query, or RP integration failure).
    """

    COMPLETE = "complete"
    NO_DATA = "no_data"
    TRUNCATED = "truncated"
    ERROR = "error"


class AccountTransaction(BaseModel):
    """Minimal analytical projection of one authoritative transaction.

    Exactly the fields the cross-account analysis needs; unrelated source
    fields (e.g. risk_reason narrative) are deliberately not projected.
    ``transaction_id`` + ``account_id`` are the provenance pair.
    """

    transaction_id: str
    account_id: str
    symbol: str
    side: str                 # BUY/SELL verbatim from the Risk Platform
    price: float
    quantity: float
    value: float
    timestamp: str | None = None


class TradeEvidenceQuery(BaseModel):
    """Logical retrieval request — investigation scope only.

    ``account_ids`` selects among the AUTHORITATIVE case scope (the case
    account and its cluster-connected accounts); an empty list means the
    case account only. There are deliberately no limit/cursor fields:
    budgets and pagination are system-controlled, not caller-chosen.
    """

    case_id: str
    account_ids: list[str] = Field(default_factory=list)
    start_time: str | None = None       # ISO-8601, passed through to RP
    end_time: str | None = None
    symbols: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_scope_shape(self) -> "TradeEvidenceQuery":
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be a non-empty string")
        for list_field in ("account_ids", "symbols"):
            values = getattr(self, list_field)
            if any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{list_field} entries must be non-empty strings")
        for t_field in ("start_time", "end_time"):
            v = getattr(self, t_field)
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ValueError(f"{t_field} must be a non-empty ISO-8601 string or None")
        return self


class RetrievalError(BaseModel):
    """Structured bounded failure. Codes are stable identifiers."""

    code: str   # INVALID_QUERY | ACCOUNT_OUT_OF_SCOPE |
                # RISK_PLATFORM_UNAVAILABLE | RISK_PLATFORM_AUTH_FAILED |
                # RISK_PLATFORM_HTTP_ERROR | RISK_PLATFORM_MALFORMED_RESPONSE
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class TradeEvidenceRetrieval(BaseModel):
    """Outcome of one bounded retrieval.

    ``records`` are tool-internal (deterministic analysis input).
    ``observation`` is the compact, row-free summary that may reach the
    planner/LLM/response composer. The two are kept separate so raw rows
    can never leak into an LLM observation merely because they were
    retrieved.
    """

    status: RetrievalStatus
    records: list[AccountTransaction] = Field(default_factory=list)
    observation: dict[str, Any]
    total_matched: int | None = None    # authoritative full count when known
    pages_fetched: int = 0
    truncated_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: RetrievalError | None = None

    @model_validator(mode="after")
    def _validate_status_invariants(self) -> "TradeEvidenceRetrieval":
        if self.status == RetrievalStatus.ERROR and self.error is None:
            raise ValueError("status=error requires an error record")
        if self.status != RetrievalStatus.ERROR and self.error is not None:
            raise ValueError("error record requires status=error")
        if self.status == RetrievalStatus.NO_DATA and self.records:
            raise ValueError("status=no_data must not carry records")
        return self


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

_ERROR_CODE_BY_KIND = {
    "unavailable": "RISK_PLATFORM_UNAVAILABLE",
    "auth": "RISK_PLATFORM_AUTH_FAILED",
    "http": "RISK_PLATFORM_HTTP_ERROR",
    "malformed": "RISK_PLATFORM_MALFORMED_RESPONSE",
}

_PROJECTION_FIELDS = (
    "transaction_id", "account_id", "symbol", "side",
    "price", "quantity", "value", "timestamp",
)


def _error(code: str, message: str, **detail: Any) -> TradeEvidenceRetrieval:
    return TradeEvidenceRetrieval(
        status=RetrievalStatus.ERROR,
        observation=_observation_base(RetrievalStatus.ERROR.value),
        error=RetrievalError(code=code, message=message, detail=detail),
    )


def _observation_base(status: str) -> dict[str, Any]:
    return {"analysis": "trade_evidence_retrieval", "status": status}


def _integration_error(err: RiskPlatformError, case_id: str) -> TradeEvidenceRetrieval:
    code = _ERROR_CODE_BY_KIND.get(err.kind, "RISK_PLATFORM_ERROR")
    result = _error(code, err.message, case_id=case_id)
    result.observation["case_id"] = case_id
    return result


class AccountScope(BaseModel):
    """The AUTHORITATIVE account scope for one case: the case account plus
    its cluster-connected accounts (per the RP network-signals surface),
    with a flag for when the connected-account listing itself was bounded
    by the account budget."""

    case_id: str
    allowed: set[str]
    bounded: bool = False
    error: RetrievalError | None = None


def resolve_account_scope(
    case_id: str,
    adapter: RiskPlatformAdapter | None = None,
    max_accounts: int | None = None,
    config: Any | None = None,
) -> AccountScope:
    """Resolve the case's authoritative account scope (sync).

    Extracted from `retrieve_trade_evidence` so callers that pre-resolve
    the scope (e.g. the analysis tool) share the exact same authoritative
    resolution instead of re-deriving it."""
    if config is None:
        from app.config import settings as config
    adapter = adapter or RiskPlatformAdapter()
    if max_accounts is None:
        max_accounts = config.TRADE_ANALYSIS_MAX_ACCOUNTS
    try:
        signals = adapter.fetch_network_signals(case_id, limit=max_accounts)
    except RiskPlatformError as err:
        code = _ERROR_CODE_BY_KIND.get(err.kind, "RISK_PLATFORM_ERROR")
        return AccountScope(
            case_id=case_id,
            allowed={case_id},
            error=RetrievalError(code=code, message=err.message,
                                 detail={"case_id": case_id}),
        )
    connected = [
        a.get("user_id")
        for a in (signals.get("connected_accounts") or [])
        if isinstance(a, dict) and isinstance(a.get("user_id"), str)
    ]
    full_count = signals.get("connected_account_count")
    bounded = isinstance(full_count, int) and full_count > len(connected)
    return AccountScope(
        case_id=case_id, allowed={case_id, *connected}, bounded=bounded,
    )


def _project_transaction(raw: Any) -> AccountTransaction:
    """Whitelist-projection of one raw RP row; missing identity fields are
    malformed upstream data, not skippable rows."""
    if not isinstance(raw, dict):
        raise ValueError("transaction row is not an object")
    for required in ("transaction_id", "account_id"):
        v = raw.get(required)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"transaction row missing {required!r}")
    return AccountTransaction(
        transaction_id=raw["transaction_id"],
        account_id=raw["account_id"],
        symbol=str(raw.get("symbol", "")),
        side=str(raw.get("side", "")),
        price=float(raw.get("price", 0.0)),
        quantity=float(raw.get("quantity", 0.0)),
        value=float(raw.get("value", 0.0)),
        timestamp=raw.get("timestamp") or None,
    )


def retrieve_trade_evidence(
    query: TradeEvidenceQuery,
    adapter: RiskPlatformAdapter | None = None,
    allowed_accounts: set[str] | None = None,
    config: Any | None = None,
) -> TradeEvidenceRetrieval:
    """Retrieve bounded account-scoped transaction evidence (sync).

    ``allowed_accounts``: pre-resolved authoritative scope; when None the
    scope is resolved from RP network signals (case account + cluster-
    connected accounts, bounded by MAX_ACCOUNTS).
    ``config``: settings override (tests); defaults to app settings.
    """
    if config is None:
        from app.config import settings as config
    adapter = adapter or RiskPlatformAdapter()
    case_id = query.case_id.strip()

    budgets = {
        "max_accounts": config.TRADE_ANALYSIS_MAX_ACCOUNTS,
        "max_total_transactions": config.TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS,
        "page_size": config.TRADE_ANALYSIS_MAX_PAGE_SIZE,
        "max_pages": config.TRADE_ANALYSIS_MAX_PAGES,
    }

    # --- authoritative account scope --------------------------------------
    scope_bounded = False
    if allowed_accounts is None:
        scope = resolve_account_scope(
            case_id, adapter=adapter,
            max_accounts=budgets["max_accounts"], config=config)
        if scope.error is not None:
            result = _error(scope.error.code, scope.error.message,
                            **scope.error.detail)
            result.observation["case_id"] = case_id
            return result
        scope_bounded = scope.bounded
        allowed_accounts = scope.allowed

    # --- scope validation: reject, never narrow ----------------------------
    requested = list(dict.fromkeys(query.account_ids))
    offending = [a for a in requested if a not in allowed_accounts]
    if offending:
        return _error(
            "ACCOUNT_OUT_OF_SCOPE",
            "Requested accounts outside the case's authoritative scope.",
            case_id=case_id,
            offending_account_ids=offending,
        )

    # --- account budget: bound + flag (never silent) -----------------------
    truncated_reasons: list[str] = []
    warnings: list[str] = []
    effective_accounts = requested or [case_id]
    if len(effective_accounts) > budgets["max_accounts"]:
        dropped = effective_accounts[budgets["max_accounts"]:]
        effective_accounts = effective_accounts[:budgets["max_accounts"]]
        truncated_reasons.append("account_budget_exhausted")
        warnings.append(
            "Account budget capped retrieval at "
            f"{budgets['max_accounts']} accounts; not retrieved: "
            + ", ".join(dropped)
        )

    # --- budgeted pagination ------------------------------------------------
    records: list[AccountTransaction] = []
    total_matched: int | None = None
    pages_fetched = 0
    offset = 0
    try:
        while True:
            remaining = budgets["max_total_transactions"] - len(records)
            if remaining <= 0:
                truncated_reasons.append(
                    f"retrieval_budget_exhausted("
                    f"TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS="
                    f"{budgets['max_total_transactions']})")
                break
            page_limit = min(budgets["page_size"], remaining)
            page = adapter.fetch_account_transactions(
                case_id,
                account_ids=effective_accounts,
                start_time=query.start_time,
                end_time=query.end_time,
                symbols=query.symbols or None,
                limit=page_limit,
                offset=offset,
            )
            pages_fetched += 1
            total_matched = int(page.get("total_matched", 0))
            for raw in page.get("transactions") or []:
                records.append(_project_transaction(raw))
            if not page.get("has_more"):
                break
            if pages_fetched >= budgets["max_pages"]:
                truncated_reasons.append(
                    f"max_pages_reached(TRADE_ANALYSIS_MAX_PAGES="
                    f"{budgets['max_pages']})")
                break
            offset = page.get("next_offset") or (offset + len(records))
    except RiskPlatformError as err:
        # A page failure discards the partial accumulation: an ERROR must
        # never be composed from a half-fetched dataset, and a partial
        # dataset must never be presented as complete.
        result = _integration_error(err, case_id)
        result.pages_fetched = pages_fetched
        if records:
            result.warnings.append(
                f"{len(records)} records from earlier pages were discarded "
                "because a later page failed; the retrieval is an error, "
                "not a partial result.")
        return result
    except (ValueError, TypeError, KeyError) as e:
        result = _error(
            "RISK_PLATFORM_MALFORMED_RESPONSE",
            f"Could not project transaction evidence: {e}",
            case_id=case_id,
        )
        result.pages_fetched = pages_fetched
        return result

    # --- status + observation ----------------------------------------------
    if scope_bounded:
        truncated_reasons.append("account_scope_listing_bounded")
    if not records and (total_matched or 0) == 0 and not truncated_reasons:
        status = RetrievalStatus.NO_DATA
    elif truncated_reasons:
        status = RetrievalStatus.TRUNCATED
    else:
        status = RetrievalStatus.COMPLETE

    timestamps = sorted(r.timestamp for r in records if r.timestamp)
    per_account: dict[str, int] = {}
    for r in records:
        per_account[r.account_id] = per_account.get(r.account_id, 0) + 1

    observation: dict[str, Any] = {
        **_observation_base(status.value),
        "case_id": case_id,
        "accounts_requested": requested,
        "accounts_analyzed": effective_accounts,
        "accounts_in_scope_count": len(allowed_accounts),
        "accounts_in_scope_bounded": scope_bounded,
        "records_per_account": per_account,
        "total_records": len(records),
        "total_matched": total_matched,
        "symbols_present": sorted({r.symbol for r in records}),
        "time_range": (
            {"earliest": timestamps[0], "latest": timestamps[-1]}
            if timestamps else None
        ),
        "truncated": status == RetrievalStatus.TRUNCATED,
        "truncated_reasons": truncated_reasons,
        "limits_applied": budgets,
    }

    if scope_bounded:
        warnings.append(
            "The authoritative connected-account listing exceeded the "
            f"account budget; scope contains at least "
            f"{len(allowed_accounts)} accounts but detail rows were bounded.")

    return TradeEvidenceRetrieval(
        status=status,
        records=records,
        observation=observation,
        total_matched=total_matched,
        pages_fetched=pages_fetched,
        truncated_reasons=truncated_reasons,
        warnings=warnings,
        error=None,
    )

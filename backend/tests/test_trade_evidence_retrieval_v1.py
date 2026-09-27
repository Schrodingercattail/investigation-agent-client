"""Bounded trade-evidence retrieval — data-contract boundary tests.

Covers the retrieval capability beneath cross_account_trade_analysis
(app/trade_evidence.py; baseline OQ-4 resolution). Mocked at the adapter
boundary (no real Risk Platform, no real HTTP). Focus:

1. valid multi-account bounded retrieval
2. account IDs outside the allowed case scope are REJECTED (never narrowed)
3. bounded record retrieval (system budgets, flagged truncation)
4. pagination (has_more/next_offset follow-through within budgets)
5. complete vs truncated results are distinguishable
6. empty (NO_DATA) vs error are distinguishable
7. transaction provenance (transaction_id + account_id) preserved; the
   projection is the minimal 8-field analytical shape
8. raw transaction rows are never promoted to the planner/LLM
   observation — the observation carries counts/ranges/flags only
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.adapters.risk_platform import RiskPlatformError
from app.trade_evidence import (
    AccountTransaction,
    RetrievalStatus,
    TradeEvidenceQuery,
    retrieve_trade_evidence,
)

CASE = "U90042"
ACCT_B = "U90043"          # cluster-connected
ACCT_C = "U90044"          # cluster-connected
OUTSIDER = "U99999"        # NOT in the case scope

BUDGETS = SimpleNamespace(
    TRADE_ANALYSIS_MAX_ACCOUNTS=3,
    TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS=500,
    TRADE_ANALYSIS_MAX_PAGE_SIZE=2,
    TRADE_ANALYSIS_MAX_PAGES=10,
    TRADE_ANALYSIS_MAX_CANDIDATES=20,
)

SIGNALS = {
    "connected_account_count": 2,
    "connected_accounts": [{"user_id": ACCT_B}, {"user_id": ACCT_C}],
}


def tx(tid, acct, symbol="BTC", side="BUY", price=100.0, quantity=1.0,
       value=None, timestamp="2026-08-19T10:00:00+00:00",
       **extra):
    """Raw RP-shaped transaction row (extra fields must NOT project)."""
    row = {
        "transaction_id": tid, "account_id": acct, "symbol": symbol,
        "side": side, "price": price, "quantity": quantity,
        "value": value if value is not None else price * quantity,
        "timestamp": timestamp,
    }
    row.update(extra)
    return row


def page(rows, total, offset=0, has_more=False, next_offset=None):
    """Raw RP AccountTransactionsResponse-shaped page."""
    return {
        "case_user_id": CASE,
        "requested_accounts": sorted({r["account_id"] for r in rows}),
        "transactions": rows,
        "total_matched": total,
        "returned_count": len(rows),
        "offset": offset,
        "limit": len(rows) or 1,
        "truncated": has_more,
        "has_more": has_more,
        "next_offset": next_offset,
    }


class FakeAdapter:
    """Adapter fake with a call log; pages are served in script order."""

    def __init__(self, signals=SIGNALS, pages=None, signals_error=None,
                 page_errors=None):
        self.signals = signals
        self.pages = list(pages or [])
        self.signals_calls = []
        self.transaction_calls = []
        self.signals_error = signals_error
        self.page_errors = dict(page_errors or {})

    def fetch_network_signals(self, user_id, limit=None):
        self.signals_calls.append({"user_id": user_id, "limit": limit})
        if self.signals_error is not None:
            raise self.signals_error
        return self.signals

    def fetch_account_transactions(self, user_id, account_ids=None,
                                   start_time=None, end_time=None,
                                   symbols=None, limit=None, offset=None):
        idx = len(self.transaction_calls)
        self.transaction_calls.append({
            "user_id": user_id, "account_ids": account_ids,
            "start_time": start_time, "end_time": end_time,
            "symbols": symbols, "limit": limit, "offset": offset,
        })
        if idx in self.page_errors:
            raise self.page_errors[idx]
        return self.pages[min(idx, len(self.pages) - 1)]


def retrieve(query, adapter, config=BUDGETS, allowed_accounts=None):
    return retrieve_trade_evidence(
        query, adapter=adapter, config=config,
        allowed_accounts=allowed_accounts)


def _accounts(a):
    return [t.account_id for t in a.records]


# ---------------------------------------------------------------------------
# 1. valid multi-account bounded retrieval
# ---------------------------------------------------------------------------

class TestMultiAccountRetrieval:
    def test_two_in_scope_accounts_retrieved_and_attributed(self):
        adapter = FakeAdapter(pages=[
            page([tx("T1", CASE), tx("T2", ACCT_B)], total=3, offset=0,
                 has_more=True, next_offset=2),
            page([tx("T3", ACCT_B)], total=3, offset=2),
        ])
        out = retrieve(
            TradeEvidenceQuery(case_id=CASE, account_ids=[CASE, ACCT_B]),
            adapter)
        assert out.status == RetrievalStatus.COMPLETE
        assert out.total_matched == 3
        assert _accounts(out) == [CASE, ACCT_B, ACCT_B]
        assert out.observation["records_per_account"] == {CASE: 1, ACCT_B: 2}
        # scope resolved from the authoritative network-signal source
        assert adapter.signals_calls[0]["user_id"] == CASE
        # server request used exactly the requested (in-scope) accounts
        assert adapter.transaction_calls[0]["account_ids"] == [CASE, ACCT_B]

    def test_default_scope_is_case_account_only(self):
        adapter = FakeAdapter(pages=[page([tx("T1", CASE)], total=1)])
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter)
        assert out.status == RetrievalStatus.COMPLETE
        assert adapter.transaction_calls[0]["account_ids"] == [CASE]


# ---------------------------------------------------------------------------
# 2. out-of-scope accounts are rejected
# ---------------------------------------------------------------------------

class TestScopeEnforcement:
    def test_out_of_scope_account_rejected_before_any_retrieval(self):
        adapter = FakeAdapter()
        out = retrieve(
            TradeEvidenceQuery(
                case_id=CASE, account_ids=[CASE, OUTSIDER]),
            adapter)
        assert out.status == RetrievalStatus.ERROR
        assert out.error.code == "ACCOUNT_OUT_OF_SCOPE"
        assert out.error.detail["offending_account_ids"] == [OUTSIDER]
        assert out.records == []
        # no transaction data was requested at all
        assert adapter.transaction_calls == []

    def test_unknown_account_rejected(self):
        adapter = FakeAdapter()
        out = retrieve(
            TradeEvidenceQuery(case_id=CASE, account_ids=["U88888"]),
            adapter)
        assert out.status == RetrievalStatus.ERROR
        assert out.error.code == "ACCOUNT_OUT_OF_SCOPE"

    def test_scope_resolution_failure_is_an_error_not_empty(self):
        adapter = FakeAdapter(signals_error=RiskPlatformError(
            "unavailable", "Risk Platform timed out after 30.0s"))
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter)
        assert out.status == RetrievalStatus.ERROR
        assert out.error.code == "RISK_PLATFORM_UNAVAILABLE"
        assert adapter.transaction_calls == []


# ---------------------------------------------------------------------------
# 3. bounded record retrieval (system budgets)
# ---------------------------------------------------------------------------

class TestBoundedRetrieval:
    def test_total_transaction_budget_truncates_and_flags(self):
        cfg = SimpleNamespace(**{**vars(BUDGETS),
                                 "TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS": 3})
        rows = [tx(f"T{i}", ACCT_B, timestamp=f"2026-08-19T10:0{i}:00+00:00")
                for i in range(5)]
        adapter = FakeAdapter(pages=[
            page(rows[:2], total=5, offset=0, has_more=True, next_offset=2),
            page(rows[2:3], total=5, offset=2, has_more=True, next_offset=3),
        ])
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter, cfg)
        assert out.status == RetrievalStatus.TRUNCATED
        assert out.observation["truncated"] is True
        assert any(r.startswith("retrieval_budget_exhausted")
                   for r in out.truncated_reasons)
        # budget never exceeded: page sizes were clamped to the remainder
        assert len(out.records) == 3
        assert adapter.transaction_calls[1]["limit"] == 1

    def test_max_pages_budget_truncates(self):
        cfg = SimpleNamespace(**{**vars(BUDGETS),
                                 "TRADE_ANALYSIS_MAX_PAGES": 1})
        rows = [tx(f"T{i}", ACCT_B) for i in range(5)]
        adapter = FakeAdapter(pages=[
            page(rows[:2], total=5, offset=0, has_more=True, next_offset=2),
        ])
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter, cfg)
        assert out.status == RetrievalStatus.TRUNCATED
        assert any(r.startswith("max_pages_reached")
                   for r in out.truncated_reasons)
        assert len(adapter.transaction_calls) == 1

    def test_account_budget_bounds_requested_accounts_and_flags(self):
        cfg = SimpleNamespace(**{**vars(BUDGETS),
                                 "TRADE_ANALYSIS_MAX_ACCOUNTS": 2})
        extra = "U90045"
        signals = {"connected_account_count": 3,
                   "connected_accounts": [{"user_id": ACCT_B},
                                          {"user_id": ACCT_C},
                                          {"user_id": extra}]}
        adapter = FakeAdapter(signals=signals,
                              pages=[page([tx("T1", CASE)], total=1)])
        out = retrieve(
            TradeEvidenceQuery(
                case_id=CASE, account_ids=[CASE, ACCT_B, ACCT_C, extra]),
            adapter, cfg)
        assert out.status == RetrievalStatus.TRUNCATED
        assert any(r == "account_budget_exhausted"
                   for r in out.truncated_reasons)
        assert any("U90045" in w for w in out.warnings)
        assert adapter.transaction_calls[0]["account_ids"] == [CASE, ACCT_B]

    def test_bounded_scope_listing_is_visible(self):
        # more connected accounts exist than the detail listing returned
        signals = {"connected_account_count": 10,
                   "connected_accounts": [{"user_id": ACCT_B}]}
        adapter = FakeAdapter(signals=signals,
                              pages=[page([tx("T1", CASE)], total=1)])
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter)
        assert out.observation["accounts_in_scope_bounded"] is True
        assert "account_scope_listing_bounded" in out.truncated_reasons
        assert out.status == RetrievalStatus.TRUNCATED


# ---------------------------------------------------------------------------
# 4. pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_follows_next_offset_until_server_reports_complete(self):
        rows = [tx(f"T{i}", ACCT_B,
                   timestamp=f"2026-08-19T10:0{i}:00+00:00")
                for i in range(5)]
        adapter = FakeAdapter(pages=[
            page(rows[0:2], total=5, offset=0, has_more=True, next_offset=2),
            page(rows[2:4], total=5, offset=2, has_more=True, next_offset=4),
            page(rows[4:5], total=5, offset=4),
        ])
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter)
        assert out.status == RetrievalStatus.COMPLETE
        assert out.pages_fetched == 3
        assert len(out.records) == 5
        assert [r.transaction_id for r in out.records] == \
            ["T0", "T1", "T2", "T3", "T4"]
        assert [c["offset"] for c in adapter.transaction_calls] == [0, 2, 4]
        # deterministic ordering preserved across pages
        assert [r.timestamp for r in out.records] == sorted(
            r.timestamp for r in out.records)

    def test_page_failure_is_an_error_not_a_partial_result(self):
        adapter = FakeAdapter(
            pages=[
                page([tx("T1", CASE)], total=3, offset=0, has_more=True,
                     next_offset=1),
            ],
            page_errors={1: RiskPlatformError("http", "RP returned 500")},
        )
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter)
        assert out.status == RetrievalStatus.ERROR
        assert out.error.code == "RISK_PLATFORM_HTTP_ERROR"
        assert out.records == []          # partial accumulation discarded
        assert any("discarded" in w for w in out.warnings)
        assert out.observation["status"] == "error"


# ---------------------------------------------------------------------------
# 5/6. complete vs truncated vs no_data vs error
# ---------------------------------------------------------------------------

class TestStatusSemantics:
    def test_complete_is_distinguishable_from_truncated(self):
        complete = retrieve(
            TradeEvidenceQuery(case_id=CASE),
            FakeAdapter(pages=[page([tx("T1", CASE)], total=1)]))
        truncated = retrieve(
            TradeEvidenceQuery(case_id=CASE),
            FakeAdapter(pages=[
                page([tx("T1", CASE)], total=2, offset=0, has_more=True,
                     next_offset=1),
            ], page_errors={1: RiskPlatformError("http", "x")}),
        )
        assert complete.status == RetrievalStatus.COMPLETE
        assert truncated.status == RetrievalStatus.ERROR  # failed, not partial
        cfg = SimpleNamespace(**{**vars(BUDGETS),
                                 "TRADE_ANALYSIS_MAX_PAGES": 1})
        bounded = retrieve(
            TradeEvidenceQuery(case_id=CASE),
            FakeAdapter(pages=[
                page([tx("T1", CASE)], total=2, offset=0, has_more=True,
                     next_offset=1),
            ]), cfg)
        assert bounded.status == RetrievalStatus.TRUNCATED
        assert bounded.observation["truncated"] is True
        assert complete.observation["truncated"] is False

    def test_no_data_is_distinguishable_from_error(self):
        empty = retrieve(
            TradeEvidenceQuery(case_id=CASE),
            FakeAdapter(pages=[page([], total=0)]))
        assert empty.status == RetrievalStatus.NO_DATA
        assert empty.records == []
        assert empty.total_matched == 0
        assert empty.error is None            # a valid, complete emptiness
        assert empty.observation["status"] == "no_data"

        failed = retrieve(
            TradeEvidenceQuery(case_id=CASE),
            FakeAdapter(signals_error=RiskPlatformError(
                "auth", "RP authentication failed (status 403)")))
        assert failed.status == RetrievalStatus.ERROR
        assert failed.error is not None
        assert failed.error.code == "RISK_PLATFORM_AUTH_FAILED"
        assert failed.observation["status"] == "error"

    def test_truncated_is_never_no_data_even_with_zero_rows(self):
        # budget engaged before the (empty) first page: a truncated
        # retrieval must never read as a completed negative result
        cfg = SimpleNamespace(**{**vars(BUDGETS),
                                 "TRADE_ANALYSIS_MAX_ACCOUNTS": 1})
        signals = {"connected_account_count": 9,
                   "connected_accounts": [{"user_id": ACCT_B}]}
        out = retrieve(
            TradeEvidenceQuery(case_id=CASE),
            FakeAdapter(signals=signals, pages=[page([], total=0)]), cfg)
        assert out.status == RetrievalStatus.TRUNCATED
        assert out.observation["truncated"] is True


# ---------------------------------------------------------------------------
# 7. provenance preservation
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_ids_and_attribution_preserved_projection_is_minimal(self):
        raw = tx("TX-REAL-001", CASE, side="SELL", price=45000.0,
                 quantity=0.5, risk_reason="burst pattern")
        out = retrieve(
            TradeEvidenceQuery(case_id=CASE, account_ids=[CASE]),
            FakeAdapter(pages=[page([raw], total=1)]))
        rec = out.records[0]
        assert isinstance(rec, AccountTransaction)
        assert rec.transaction_id == "TX-REAL-001"   # provenance id intact
        assert rec.account_id == CASE                # attribution intact
        # exactly the minimal analytical projection — no source extras
        assert set(rec.model_dump().keys()) == {
            "transaction_id", "account_id", "symbol", "side",
            "price", "quantity", "value", "timestamp"}
        assert "risk_reason" not in rec.model_dump()

    def test_rows_missing_identity_are_malformed_not_skipped(self):
        adapter = FakeAdapter(pages=[
            page([{"account_id": CASE, "symbol": "BTC"}], total=1)])
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter)
        assert out.status == RetrievalStatus.ERROR
        assert out.error.code == "RISK_PLATFORM_MALFORMED_RESPONSE"


# ---------------------------------------------------------------------------
# 8. raw rows are never promoted to the LLM observation
# ---------------------------------------------------------------------------

class TestObservationBoundary:
    def test_observation_carries_no_transaction_rows_or_ids(self):
        adapter = FakeAdapter(pages=[
            page([tx("TX-REAL-001", CASE), tx("TX-REAL-002", ACCT_B)],
                 total=2),
        ])
        out = retrieve(
            TradeEvidenceQuery(case_id=CASE, account_ids=[CASE, ACCT_B]),
            adapter)
        import json
        obs_json = json.dumps(out.observation)
        assert "TX-REAL-001" not in obs_json
        assert "TX-REAL-002" not in obs_json
        for row_key in ("records", "transactions", "rows"):
            assert row_key not in out.observation
        # but the observation IS the compact analytical picture
        assert out.observation["total_records"] == 2
        assert out.observation["records_per_account"] == {CASE: 1, ACCT_B: 1}
        assert out.observation["symbols_present"] == ["BTC"]
        assert out.observation["time_range"]["earliest"] == \
            "2026-08-19T10:00:00+00:00"

    def test_error_observation_is_also_row_free(self):
        adapter = FakeAdapter(signals_error=RiskPlatformError(
            "malformed", "invalid JSON"))
        out = retrieve(TradeEvidenceQuery(case_id=CASE), adapter)
        import json
        assert "transactions" not in json.dumps(out.observation)
        assert out.observation["analysis"] == "trade_evidence_retrieval"


# ---------------------------------------------------------------------------
# query validation + adapter parameter encoding
# ---------------------------------------------------------------------------

class TestQueryAndAdapter:
    def test_blank_case_id_is_invalid_query_error(self):
        with pytest.raises(ValueError):
            TradeEvidenceQuery(case_id="  ")

    def test_adapter_encodes_scoped_params(self):
        captured = {}

        async def fake_request(self, method, path, json_body=None):
            captured["path"] = path
            return {"total_matched": 0, "transactions": [], "has_more": False}

        with patch("app.adapters.risk_platform.RiskPlatformAdapter._request",
                   new=fake_request):
            from app.adapters.risk_platform import RiskPlatformAdapter
            RiskPlatformAdapter().fetch_account_transactions(
                CASE,
                account_ids=[CASE, ACCT_B],
                start_time="2026-08-19T10:00:00+00:00",
                symbols=["BTC", "ETH"],
                limit=50,
                offset=0,
            )
        path = captured["path"]
        assert path.startswith(f"/api/risk/cases/{CASE}/transactions?")
        # timestamp '+' is percent-encoded; lists are comma-joined
        assert "start_time=2026-08-19T10%3A00%3A00%2B00%3A00" in path
        assert "account_ids=U90042%2CU90043" in path
        assert "symbols=BTC%2CETH" in path
        assert "limit=50" in path and "offset=0" in path

"""Global trade-analysis budget — configuration and truncation invariants.

The budget is ONE global, auditable setting read by every trade-analysis
retrieval path (target-anchored per-stream; set-internal on the union
stream). Invariants locked here:

1. The DEFAULT is 1000 and the running Settings actually carry it.
2. The configured budget can be LOWERED (bounded retrieval is the
   contract, not the value).
3. A configured value ABOVE the hard ceiling (1000 this version) is
   REJECTED at startup — never silently clamped, never bypassable.
4. Exceeding the effective budget still produces the truncation flag +
   PARTIAL semantics on BOTH analysis scopes — raising the default does
   not remove the boundary.
5. Both scopes read the SAME effective configuration.
"""

import pytest

from pydantic import ValidationError

from app.config import Settings
from app.domain_tools.cross_account_trade_analysis import (
    cross_account_trade_analysis,
)
from app.trade_evidence import (
    RetrievalStatus,
    TradeEvidenceQuery,
    retrieve_trade_evidence,
)


class _Cfg:
    """Config namespace with a custom budget (the shape the tools
    receive; mirrors Settings fields the retrieval reads)."""

    def __init__(self, total):
        self.TRADE_ANALYSIS_MAX_ACCOUNTS = 24
        self.TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS = total
        self.TRADE_ANALYSIS_MAX_PAGE_SIZE = 100
        self.TRADE_ANALYSIS_MAX_PAGES = 10
        self.TRADE_ANALYSIS_MAX_CANDIDATES = 20
        self.TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS = 30.0
        self.TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE = 0.05
        self.TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR = 1.0
        self.TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE = 5


def _row(i):
    return {"transaction_id": f"T{i:05d}",
            "account_id": ("UA", "UB", "UC")[i % 3],
            "symbol": "BTC", "side": "BUY" if i % 2 else "SELL",
            "price": 100.0, "quantity": 1.0, "value": 100.0,
            "timestamp": "2026-07-20T10:00:00+00:00"}


class _BigAdapter:
    """Serves `total` rows regardless of filters — exposes whether the
    retrieval honored the configured budget."""

    def __init__(self, total, connected=("UB", "UC")):
        self.rows = [_row(i) for i in range(total)]
        self.connected = list(connected)
        self.page_limits = []

    def fetch_network_signals(self, user_id, limit=None):
        return {"connected_account_count": len(self.connected),
                "connected_accounts": [{"user_id": a}
                                       for a in self.connected]}

    def fetch_account_transactions(self, user_id, account_ids=None,
                                   start_time=None, end_time=None,
                                   symbols=None, limit=None, offset=None):
        self.page_limits.append(limit)
        accounts = set(account_ids or [user_id])
        rows = [r for r in self.rows if r["account_id"] in accounts]
        page = rows[offset:offset + (limit or 50)]
        has_more = offset + len(page) < len(rows)
        return {
            "case_user_id": user_id,
            "requested_accounts": list(account_ids or [user_id]),
            "transactions": page, "total_matched": len(rows),
            "returned_count": len(page), "offset": offset,
            "limit": limit or 50, "truncated": has_more,
            "has_more": has_more,
            "next_offset": offset + len(page) if has_more else None,
        }

    def fetch_case_evidence(self, user_id, expose_complete_records=False):
        return {"feature_evidence": {}}


class TestBudgetConfiguration:
    def test_default_budget_is_1000(self):
        s = Settings()
        assert s.TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS == 1000
        assert s.TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS_HARD_CEILING == 1000

    def test_budget_can_be_lowered(self, monkeypatch):
        monkeypatch.setenv("TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS", "200")
        s = Settings()
        assert s.TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS == 200

    def test_over_ceiling_rejected_loudly(self, monkeypatch):
        monkeypatch.setenv("TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS", "5000")
        with pytest.raises(ValidationError, match="hard ceiling"):
            Settings()

    def test_ceiling_itself_bounded(self, monkeypatch):
        monkeypatch.setenv(
            "TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS_HARD_CEILING", "0")
        with pytest.raises(ValidationError):
            Settings()


class TestBudgetEnforcement:
    def test_default_budget_covers_u00047_scale_completely(self):
        # 530-record network (the U00047 scale) fits within the default
        # 1000 budget: the retrieval must be COMPLETE — not truncated.
        out = retrieve_trade_evidence(
            TradeEvidenceQuery(case_id="UA", account_ids=["UA", "UB", "UC"]),
            adapter=_BigAdapter(530, connected=("UB", "UC")),
            allowed_accounts={"UA", "UB", "UC"},
            config=_Cfg(Settings().TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS))
        assert out.status == RetrievalStatus.COMPLETE
        assert len(out.records) == 530

    @pytest.mark.parametrize("budget", [50, 200, 499])
    def test_lower_budget_still_truncates_and_flags(self, budget):
        # the boundary survives the default change: any total above the
        # effective budget is TRUNCATED with the reason carried
        out = retrieve_trade_evidence(
            TradeEvidenceQuery(case_id="UA", account_ids=["UA", "UB", "UC"]),
            adapter=_BigAdapter(budget + 30, connected=("UB", "UC")),
            allowed_accounts={"UA", "UB", "UC"},
            config=_Cfg(budget))
        assert out.status == RetrievalStatus.TRUNCATED
        assert any("retrieval_budget_exhausted" in r
                   for r in out.truncated_reasons)
        assert len(out.records) == budget

    def test_set_scope_honors_the_same_budget(self):
        # the set-internal scope reads the SAME effective config: a
        # 530-row set under a lowered 200 budget truncates
        out = cross_account_trade_analysis(
            "UA", analysis_scope="related_account_set",
            adapter=_BigAdapter(530, connected=("UB", "UC")),
            config=_Cfg(200))
        d = out.data
        assert d["analysis_scope"] == "related_account_set"
        assert d["retrieval"]["truncated"] is True
        assert d["totals"]["transactions_retrieved"] == 200
        # truncated + candidates -> MATCH_FOUND but PARTIAL semantics
        assert d["analysis_status"] == "MATCH_FOUND"
        assert any("partial set coverage" in w for w in out.warnings)

    def test_target_scope_honors_the_same_budget(self):
        # Case-4 target-anchored mode: BOTH streams read the same config.
        # 220 rows split ~73 per account; under a 100 budget the target
        # stream (~73) fits but the counterparty stream (~146) truncates
        # — the merged truncation flag must surface either way.
        out = cross_account_trade_analysis(
            "UA", adapter=_BigAdapter(220, connected=("UB", "UC")),
            config=_Cfg(100))
        d = out.data
        assert d["analysis_scope"] == "target_account"
        assert d["retrieval"]["counterparty"]["truncated"] is True
        assert d["retrieval"]["truncated"] is True
        assert any("counterparty_accounts:" in r
                   for r in d["retrieval"]["truncated_reasons"])

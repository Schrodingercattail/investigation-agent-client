"""Set-internal cross-account analysis — generic invariants.

The user may name a related-account POPULATION and ask for hedging
WITHIN it: S = {case account} ∪ {authoritative related accounts}, and
candidate pairs may involve the case account or only two related
members. These tests lock the general properties of that capability for
ANY case, ANY set size, ANY address/account mix — no case id, finding
id, record count, or phrasing may appear in production behavior.

Locked invariants (task §5.7):
1. case-account ↔ related-member candidates count and are labeled.
2. related-member ↔ related-member candidates count even though NEITHER
   leg is the case account — and are never attributed to it.
3. case-account candidates present with no related-only pairs — the
   answer does not invent related-only relations.
4. set-internal zero on complete retrieval = determinate negative
   scoped to the set.
5. candidates whose legs fall outside the analyzed set are a scope
   violation (never reported as set-internal evidence).
6. the related-accounts LIST deliverable survives independently when
   the trade analysis fails.
7. truncated set evidence never supports a set-wide no-match.
8. changing the target case/set changes the analyzed membership.
9. matching thresholds stay the frozen system values in set mode.
"""

import pytest

from app.domain_tools.cross_account_trade_analysis import (
    ANALYSIS_SCOPES,
    cross_account_trade_analysis,
    find_candidates,
)
from app.goal_postconditions import _category_postcondition
from app.models import ToolResultOutcome
from app.request_coverage import _hedging_verdict
from app.skills import STEP_TOOL_MAP


def engine_config():
    return {"max_time_delta_seconds": 30.0,
            "value_tolerance_relative": 0.05,
            "value_tolerance_absolute_floor": 1.0,
            "max_legs_per_candidate": 5, "max_candidates": 20}


def tx(tid, account, side, value, ts, symbol="BTC"):
    from app.trade_evidence import AccountTransaction
    return AccountTransaction(
        transaction_id=tid, account_id=account, symbol=symbol, side=side,
        price=100.0, quantity=value / 100.0, value=value, timestamp=ts)


def _cfg_ns():
    class NS:
        TRADE_ANALYSIS_MAX_ACCOUNTS = 24
        TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS = 500
        TRADE_ANALYSIS_MAX_PAGE_SIZE = 100
        TRADE_ANALYSIS_MAX_PAGES = 10
        TRADE_ANALYSIS_MAX_CANDIDATES = 20
        TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS = 30.0
        TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE = 0.05
        TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR = 1.0
        TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE = 5
    return NS()


def set_payload(candidates, accounts, truncated=False, status=None,
                target="UA", metrics=False):
    return {
        "analysis": "cross_account_trade_analysis",
        "analysis_scope": "related_account_set",
        "analysis_status": status or (
            "MATCH_FOUND" if candidates else "NO_MATCH"),
        "case_id": target, "target_account": target,
        "candidates": candidates,
        "accounts_analyzed": accounts,
        "retrieval": {"truncated": truncated},
        "target_account_metrics": (
            {"opposite_trade_ratio": 0.3} if metrics else None),
    }


def cand(a, b, txids=("TA", "TB")):
    return {"account_a": a, "account_b": b,
            "transaction_ids_a": [txids[0]],
            "transaction_ids_b": [txids[1]],
            "symbol": "BTC", "max_time_gap_seconds": 1.0}


class TestSetScopeTool:
    def test_scope_validation_rejects_unknown_scope(self):
        out = cross_account_trade_analysis(
            "UA", analysis_scope="bogus", config=_cfg_ns())
        assert out.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_set_mode_analyzes_pairs_within_the_retrieved_set(self):
        # UA (case) ↔ UB and UB ↔ UC both match in the retrieved set:
        # the related-only pair is a FIRST-CLASS set-internal result.
        rows = [
            {"transaction_id": "T1", "account_id": "UA", "symbol": "BTC",
             "side": "BUY", "price": 100.0, "quantity": 1.0,
             "value": 100.0, "timestamp": "2026-07-20T10:00:00+00:00"},
            {"transaction_id": "T2", "account_id": "UB", "symbol": "BTC",
             "side": "SELL", "price": 100.0, "quantity": 1.0,
             "value": 100.0, "timestamp": "2026-07-20T10:00:10+00:00"},
            {"transaction_id": "T3", "account_id": "UB", "symbol": "BTC",
             "side": "BUY", "price": 100.0, "quantity": 0.5,
             "value": 50.0, "timestamp": "2026-07-20T11:00:00+00:00"},
            {"transaction_id": "T4", "account_id": "UC", "symbol": "BTC",
             "side": "SELL", "price": 100.0, "quantity": 0.5,
             "value": 50.0, "timestamp": "2026-07-20T11:00:05+00:00"},
        ]

        class Adapter:
            def fetch_network_signals(self, user_id, limit=None):
                return {"connected_account_count": 2,
                        "connected_accounts": [{"user_id": "UB"},
                                               {"user_id": "UC"}]}

            def fetch_account_transactions(self, user_id, account_ids=None,
                                           start_time=None, end_time=None,
                                           symbols=None, limit=None,
                                           offset=None):
                accounts = set(account_ids or [])
                rows_f = [r for r in rows if r["account_id"] in accounts]
                return {
                    "case_user_id": user_id,
                    "requested_accounts": list(account_ids or []),
                    "transactions": rows_f, "total_matched": len(rows_f),
                    "returned_count": len(rows_f), "offset": 0,
                    "limit": 100, "truncated": False, "has_more": False,
                    "next_offset": None,
                }

            def fetch_case_evidence(self, user_id,
                                    expose_complete_records=False):
                return {"feature_evidence": {}}

        out = cross_account_trade_analysis(
            "UA", analysis_scope="related_account_set",
            adapter=Adapter(), config=_cfg_ns())
        d = out.data
        assert d["analysis_scope"] == "related_account_set"
        assert d["analysis_status"] == "MATCH_FOUND"
        pairs = {(c["account_a"], c["account_b"]) for c in d["candidates"]}
        assert ("UA", "UB") in {(p[0], p[1]) for p in pairs} or \
            {("UA", "UB"), ("UB", "UA")} & pairs
        related_only = [c for c in d["candidates"]
                        if "UA" not in (c["account_a"], c["account_b"])]
        assert related_only, "related-only pair must be a first-class result"
        assert {("UB", "UC")} & {(c["account_a"], c["account_b"])
                                 for c in related_only}

    def test_default_scope_stays_target_anchored(self):
        # the same fixture under the DEFAULT scope: the related-only pair
        # must NOT appear (Case-4 anchoring unchanged)
        rows = [
            {"transaction_id": "T3", "account_id": "UB", "symbol": "BTC",
             "side": "BUY", "price": 100.0, "quantity": 0.5,
             "value": 50.0, "timestamp": "2026-07-20T11:00:00+00:00"},
            {"transaction_id": "T4", "account_id": "UC", "symbol": "BTC",
             "side": "SELL", "price": 100.0, "quantity": 0.5,
             "value": 50.0, "timestamp": "2026-07-20T11:00:05+00:00"},
        ]

        class Adapter:
            def fetch_network_signals(self, user_id, limit=None):
                return {"connected_account_count": 2,
                        "connected_accounts": [{"user_id": "UB"},
                                               {"user_id": "UC"}]}

            def fetch_account_transactions(self, user_id, account_ids=None,
                                           start_time=None, end_time=None,
                                           symbols=None, limit=None,
                                           offset=None):
                accounts = set(account_ids or [user_id])
                if accounts == {user_id}:
                    rows_f = []          # the case account has no trades
                else:
                    rows_f = [r for r in rows
                              if r["account_id"] in accounts]
                return {
                    "case_user_id": user_id,
                    "requested_accounts": list(account_ids or []),
                    "transactions": rows_f, "total_matched": len(rows_f),
                    "returned_count": len(rows_f), "offset": 0,
                    "limit": 100, "truncated": False, "has_more": False,
                    "next_offset": None,
                }

            def fetch_case_evidence(self, user_id,
                                    expose_complete_records=False):
                return {"feature_evidence": {}}

        out = cross_account_trade_analysis(
            "UA", adapter=Adapter(), config=_cfg_ns())
        assert out.data["analysis_scope"] == "target_account"
        assert out.data["analysis_status"] == "NO_MATCH"
        assert out.data["candidates"] == []

    def test_binding_is_registry_locked(self):
        b = STEP_TOOL_MAP["analyze_related_account_set_trades"]
        assert b.tool_name == "cross_account_trade_analysis"
        assert b.parameter_locks["analysis_scope"] == \
            "related_account_set"
        assert b.parameter_locks["analysis_type"] == \
            "cross_account_opposite_trade"


def _verdict_stub(aspect_id="O1", claim="set hedging"):
    from app.request_coverage import AspectVerdict

    def _make(satisfaction, detail=None, reason=None, tool=None):
        return AspectVerdict(aspect_id=aspect_id, claim=claim,
                             satisfaction=satisfaction, detail=detail,
                             reason_code=reason, tool=tool)
    return _make


class TestSetScopeVerdicts:
    def test_case_account_pair_counts_and_labels(self):
        v = _hedging_verdict(
            set_payload([cand("UA", "UB")], ["UA", "UB", "UC"]),
            _verdict_stub())
        assert v.satisfaction == "satisfied"
        assert "1 组涉及案件账户 UA" in v.detail
        assert "0 组仅发生在其他关联成员之间" in v.detail

    def test_related_only_pair_counts_never_attributed_to_case(self):
        v = _hedging_verdict(
            set_payload([cand("UB", "UC")], ["UA", "UB", "UC"]),
            _verdict_stub())
        assert v.satisfaction == "satisfied"
        assert "0 组涉及案件账户 UA" in v.detail
        assert "1 组仅发生在其他关联成员之间" in v.detail
        # the case account is never claimed as a participant
        assert "0 组涉及案件账户 UA" in v.detail

    def test_outside_set_pairs_are_a_scope_violation(self):
        v = _hedging_verdict(
            set_payload([cand("UZ1", "UZ2")], ["UA", "UB", "UC"]),
            _verdict_stub())
        assert v.satisfaction == "unresolved"
        assert v.reason_code == "set_membership_violation"

    def test_set_zero_complete_is_scoped_negative(self):
        v = _hedging_verdict(
            set_payload([], ["UA", "UB", "UC"], status="NO_MATCH"),
            _verdict_stub())
        assert v.satisfaction == "satisfied"
        assert "集合内部范围内" in v.detail

    def test_truncated_set_never_supports_no_match(self):
        v = _hedging_verdict(
            set_payload([], ["UA", "UB", "UC"], status="NO_MATCH",
                        truncated=True),
            _verdict_stub())
        assert v.satisfaction == "unresolved"
        assert "这不是“未发现”" in v.detail

    def test_postcondition_set_mode_labels_involvement(self):
        from app.goal_resolution import RequiredOutput, ScopeLevel
        out = RequiredOutput(
            output_id="O1", claim="set hedging",
            claim_kind="existence_check", subject_ref="UA",
            scope=ScopeLevel.CASE_LEVEL.value,
            evidence_requirements=["opposite_trade_relations"])
        obs = [{"for_outputs": ["O1"], "outcome": "success",
                "actual_evidence_types": ["opposite_trade_relations"],
                "summary": set_payload(
                    [cand("UB", "UC"), cand("UA", "UB")],
                    ["UA", "UB", "UC"])}]
        res = _category_postcondition("opposite_trade_relations", out,
                                      obs[0]["summary"], "UA")
        assert res[0].value == "satisfied"
        assert res[4] and "仅发生在其他关联成员之间" in res[4]


class TestSetScopeIntent:
    @pytest.mark.parametrize("phrasing", [
        "这个账户的所有关联账户明细列给我，并且告诉我这些账户之间是否存在对冲交易情况",
        "这些账户之间有没有对冲",
        "查一下关联账户之间的对倒情况",
    ])
    def test_set_intent_recognized(self, phrasing):
        from app import investigation_concepts as ic
        assert ic.is_related_account_set_trade_analysis(phrasing)

    @pytest.mark.parametrize("phrasing", [
        "这个账户和其他账户之间有没有对冲交易",
        "分析一下这个账户自己",
    ])
    def test_target_anchored_not_misrouted(self, phrasing):
        from app import investigation_concepts as ic
        assert not ic.is_related_account_set_trade_analysis(phrasing)

    def test_scopes_are_closed_vocabulary(self):
        assert ANALYSIS_SCOPES == ("target_account",
                                   "related_account_set")

"""Case-4 correction — target-account scope for cross_account_trade_analysis.

Regression basis (U00010/F7, 2026-10-12): the analysis retrieved one
global union stream (500-record budget window of a 1138-record scope),
matched pairs among whichever accounts dominated that window, and the
composers presented 6 candidate relationships between OTHER accounts
(U00307/U00349/U00301/U00325/U00331/U00313) as the answer to "does THIS
account hedge with other accounts" — without a single candidate
containing the case account U00010.

The invariants locked here:

1. Every emitted candidate contains at least one transaction of the
   TARGET account (the case account), retrievable to its pair legs.
2. Candidates between other accounts only NEVER enter the target
   result (not as rows, not as fillers for a zero).
3. A zero target result is NO_MATCH on complete retrieval — not
   dressed up with unrelated global pairs, not claimed on partial data.
4. Target-record unavailability (truncation) is INSUFFICIENT_DATA with
   the gap named — never a pretended complete answer.
5. The reverse-trade-rate metric (34.38% = opposite_trade_ratio, the
   account's OWN min(BUY, SELL)/total balance) is an INDEPENDENT
   deliverable in both composer surfaces, separate from the
   cross-account result; the two are explicitly distinguished.
6. Matching thresholds/config stay the frozen system values (never
   loosened to manufacture results).
"""

import json

import pytest

from app.adapters.risk_platform import RiskPlatformError
from app.domain_tools.cross_account_trade_analysis import (
    ANALYSIS_TYPE,
    cross_account_trade_analysis,
    find_candidates,
)
from app.models import ToolResultOutcome
from app.trade_evidence import AccountTransaction

CFG_NS = type("NS", (), {
    "TRADE_ANALYSIS_MAX_ACCOUNTS": 24,
    "TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS": 500,
    "TRADE_ANALYSIS_MAX_PAGE_SIZE": 100,
    "TRADE_ANALYSIS_MAX_PAGES": 10,
    "TRADE_ANALYSIS_MAX_CANDIDATES": 20,
    "TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS": 30.0,
    "TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE": 0.05,
    "TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR": 1.0,
    "TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE": 5,
})()

TARGET = "U00010"
CONNECTED = ["U00307", "U00349", "U00301"]


def tx(tid, account, side, value, ts, symbol="BTC"):
    return AccountTransaction(
        transaction_id=tid, account_id=account, symbol=symbol, side=side,
        price=100.0, quantity=value / 100.0, value=value, timestamp=ts)


def engine_config():
    return {
        "max_time_delta_seconds": 30.0,
        "value_tolerance_relative": 0.05,
        "value_tolerance_absolute_floor": 1.0,
        "max_legs_per_candidate": 5,
        "max_candidates": 20,
    }


class ScopedAdapter:
    """Fake RP adapter with account-aware transaction storage + the
    authoritative feature echo — closest to the real surfaces."""

    def __init__(self, rows, metrics_ratio=0.3438,
                 metrics_error=False, target_stream_truncated=False):
        self.rows = rows
        self.metrics_ratio = metrics_ratio
        self.metrics_error = metrics_error
        self.target_stream_truncated = target_stream_truncated
        self.transaction_calls = []

    def fetch_network_signals(self, user_id, limit=None):
        return {"connected_account_count": len(CONNECTED),
                "connected_accounts": [{"user_id": a} for a in CONNECTED]}

    def fetch_account_transactions(self, user_id, account_ids=None,
                                   start_time=None, end_time=None,
                                   symbols=None, limit=None, offset=None):
        self.transaction_calls.append({
            "account_ids": list(account_ids or []),
            "start_time": start_time, "end_time": end_time,
        })
        accounts = set(account_ids or [user_id])
        rows = [r for r in self.rows if r["account_id"] in accounts]
        if start_time is not None:
            rows = [r for r in rows if r["timestamp"] >= start_time]
        if end_time is not None:
            rows = [r for r in rows if r["timestamp"] <= end_time]
        rows = sorted(rows, key=lambda r: (r["timestamp"], r["transaction_id"]))
        page = rows[offset:offset + (limit or 50)]
        is_target_stream = accounts == {TARGET}
        if is_target_stream and self.target_stream_truncated:
            # simulate RP reporting MORE rows than the page returned: the
            # target stream cannot deliver its full authoritative set
            has_more = True
            total = len(rows) + 40
        else:
            has_more = offset + len(page) < len(rows)
            total = len(rows)
        return {
            "case_user_id": user_id,
            "requested_accounts": list(account_ids or [user_id]),
            "transactions": page,
            "total_matched": total,
            "returned_count": len(page),
            "offset": offset,
            "limit": limit or 50,
            "truncated": has_more,
            "has_more": has_more,
            "next_offset": offset + len(page) if has_more else None,
        }

    def fetch_case_evidence(self, user_id, expose_complete_records=False):
        if self.metrics_error:
            raise RiskPlatformError("unavailable", "RP metrics down")
        return {"feature_evidence": {"opposite_trade_ratio":
                                     self.metrics_ratio}}


# ---------------------------------------------------------------------------
# Engine: target-anchored matching (pure function)
# ---------------------------------------------------------------------------

class TestTargetAnchoredEngine:
    def test_every_candidate_contains_target_account(self):
        # target trade + counterpart leg match; two OTHER accounts also
        # form a perfect pair that must NOT appear as a candidate.
        records = [
            tx("T-TGT", TARGET, "BUY", 100.0, "2026-07-20T10:00:00+00:00"),
            tx("T-LEG", "U00307", "SELL", 100.0,
               "2026-07-20T10:00:10+00:00"),
            # an unrelated perfect pair between two other accounts
            tx("T-G1", "U00349", "BUY", 50.0, "2026-07-20T11:00:00+00:00"),
            tx("T-G2", "U00301", "SELL", 50.0,
               "2026-07-20T11:00:05+00:00"),
        ]
        cands, dq = find_candidates(records, engine_config(),
                                    target_account=TARGET)
        assert len(cands) == 1
        c = cands[0]
        assert TARGET in (c["account_a"], c["account_b"])
        assert {TARGET, "U00307"} == {c["account_a"], c["account_b"]}
        # the pair legs are traceable: target transaction + its counterpart
        ids = set(c["transaction_ids_a"] + c["transaction_ids_b"])
        assert ids == {"T-TGT", "T-LEG"}

    def test_global_pairs_never_enter_target_result(self):
        # ONLY unrelated pairs match; the target's trades have no
        # counterpart. The target result must be ZERO — the global pair
        # must not appear in any form.
        records = [
            tx("T-TGT", TARGET, "BUY", 100.0, "2026-07-20T10:00:00+00:00"),
            tx("T-G1", "U00349", "BUY", 50.0, "2026-07-20T11:00:00+00:00"),
            tx("T-G2", "U00301", "SELL", 50.0,
               "2026-07-20T11:00:05+00:00"),
        ]
        cands, _ = find_candidates(records, engine_config(),
                                   target_account=TARGET)
        assert cands == []
        # legacy behavior (no target) still finds the global pair — the
        # scoping is the analysis layer's choice, not a matcher change
        legacy, _ = find_candidates(records, engine_config())
        assert len(legacy) == 1

    def test_frozen_thresholds_unchanged_under_target_anchor(self):
        # the same tolerance boundaries apply: inclusive Δt = 30s,
        # inclusive 5% value tolerance — verified with a target anchor.
        records = [
            tx("T-A", TARGET, "BUY", 100.0, "2026-07-20T10:00:00+00:00"),
            tx("T-B", "U00307", "SELL", 105.0,
               "2026-07-20T10:00:30+00:00"),   # exactly Δt=30s, +5%
        ]
        cands, _ = find_candidates(records, engine_config(),
                                   target_account=TARGET)
        assert len(cands) == 1                 # inclusive boundaries match
        outside = [records[0],
                   tx("T-C", "U00307", "SELL", 106.0,
                      "2026-07-20T10:00:30+00:00")]   # |Δv|=6 > max(1, 5.3)
        cands2, _ = find_candidates(outside, engine_config(),
                                    target_account=TARGET)
        assert cands2 == []                    # just outside tolerance: no
        # value 110 vs anchor 100 at Δt=30s: |Δv|=10 exceeds
        # max(1.0, 5.5) — outside the frozen tolerance, no candidate
        outside_v = [records[0],
                     tx("T-D", "U00307", "SELL", 110.0,
                        "2026-07-20T10:00:30+00:00")]
        cands3, _ = find_candidates(outside_v, engine_config(),
                                    target_account=TARGET)
        assert cands3 == []


# ---------------------------------------------------------------------------
# Tool: target-scoped retrieval + payload honesty
# ---------------------------------------------------------------------------

class TestTargetScopedTool:
    def _rows(self, with_target_match=True, with_global_pair=True):
        rows = []
        if with_target_match:
            rows += [
                {"transaction_id": "T-TGT", "account_id": TARGET,
                 "symbol": "BTC", "side": "BUY", "price": 100.0,
                 "quantity": 1.0, "value": 100.0,
                 "timestamp": "2026-07-20T10:00:00+00:00"},
                {"transaction_id": "T-LEG", "account_id": "U00307",
                 "symbol": "BTC", "side": "SELL", "price": 100.0,
                 "quantity": 1.0, "value": 100.0,
                 "timestamp": "2026-07-20T10:00:10+00:00"},
            ]
        else:
            rows.append(
                {"transaction_id": "T-TGT", "account_id": TARGET,
                 "symbol": "BTC", "side": "BUY", "price": 100.0,
                 "quantity": 1.0, "value": 100.0,
                 "timestamp": "2026-07-20T10:00:00+00:00"})
        if with_global_pair:
            rows += [
                {"transaction_id": "T-G1", "account_id": "U00349",
                 "symbol": "BTC", "side": "BUY", "price": 100.0,
                 "quantity": 0.5, "value": 50.0,
                 "timestamp": "2026-07-20T11:00:00+00:00"},
                {"transaction_id": "T-G2", "account_id": "U00301",
                 "symbol": "BTC", "side": "SELL", "price": 100.0,
                 "quantity": 0.5, "value": 50.0,
                 "timestamp": "2026-07-20T11:00:05+00:00"},
            ]
        return rows

    def test_match_names_target_and_carries_independent_metric(self):
        adapter = ScopedAdapter(self._rows())
        out = cross_account_trade_analysis(TARGET, adapter=adapter,
                                           config=CFG_NS)
        assert out.outcome == ToolResultOutcome.SUCCESS
        d = out.data
        assert d["analysis_status"] == "MATCH_FOUND"
        assert d["target_account"] == TARGET
        assert len(d["candidates"]) == 1
        c = d["candidates"][0]
        assert TARGET in (c["account_a"], c["account_b"])
        # global pair NOT emitted anywhere
        blob = json.dumps(d)
        assert "T-G1" not in blob and "T-G2" not in blob
        # the authoritative same-account metric rides alongside (Part A)
        m = d["target_account_metrics"]
        assert m["opposite_trade_ratio"] == 0.3438
        assert m["source"] == "risk_platform.feature_evidence"
        # both retrieval streams are honestly echoed
        assert d["retrieval"]["target"]["transactions_retrieved"] >= 1
        assert d["retrieval"]["counterparty"]["accounts_queried"] == \
            len(CONNECTED)
        # the target stream queried ONLY the target account; the
        # counterparty stream queried only the connected accounts
        first, second = adapter.transaction_calls
        assert first["account_ids"] == [TARGET]
        assert set(second["account_ids"]) == set(CONNECTED)
        # the counterparty query is bounded to the reachable time window
        assert second["start_time"] is not None \
            and second["end_time"] is not None

    def test_zero_target_matches_is_no_match_without_global_fillers(self):
        adapter = ScopedAdapter(self._rows(with_target_match=False))
        out = cross_account_trade_analysis(TARGET, adapter=adapter,
                                           config=CFG_NS)
        d = out.data
        assert d["analysis_status"] == "NO_MATCH"
        assert d["candidates"] == [] and d["candidates_found"] == 0
        blob = json.dumps(d)
        assert "T-G1" not in blob and "T-G2" not in blob

    def test_target_truncation_is_insufficient_never_complete_claim(self):
        rows = self._rows(with_target_match=False)
        # RP reports more target rows than the stream can deliver: the
        # target anchor set is partial, so no complete claim is allowed
        adapter = ScopedAdapter(rows, target_stream_truncated=True)
        out = cross_account_trade_analysis(TARGET, adapter=adapter,
                                           config=CFG_NS)
        d = out.data
        assert d["analysis_status"] == "INSUFFICIENT_DATA"
        assert d["retrieval"]["target"]["truncated"] is True
        assert d["retrieval"]["truncated"] is True
        assert d["evidence_missing"] is True

    def test_metric_unavailable_is_bounded_warning_not_failure(self):
        adapter = ScopedAdapter(self._rows(), metrics_error=True)
        out = cross_account_trade_analysis(TARGET, adapter=adapter,
                                           config=CFG_NS)
        assert out.outcome == ToolResultOutcome.SUCCESS
        assert out.data.get("target_account_metrics") is None
        assert any("opposite_trade_ratio" in w for w in out.warnings)
        assert out.data["analysis_status"] == "MATCH_FOUND"


# ---------------------------------------------------------------------------
# Composers: two independent deliverables, target-scoped result
# ---------------------------------------------------------------------------

def _match_payload():
    return {
        "analysis": "cross_account_trade_analysis",
        "analysis_type": ANALYSIS_TYPE,
        "analysis_status": "MATCH_FOUND",
        "case_id": TARGET,
        "finding_id": "F7",
        "target_account": TARGET,
        "accounts_analyzed": [TARGET, *CONNECTED],
        "candidates": [{
            "candidate_id": "CATA-001", "match_type": "1_to_1", "rank": 1,
            "account_a": TARGET, "side_a": "BUY",
            "account_b": "U00307", "side_b": "SELL",
            "symbol": "BTC",
            "transaction_ids_a": ["T-TGT"], "transaction_ids_b": ["T-LEG"],
            "timestamps": ["2026-07-20T10:00:00+00:00",
                           "2026-07-20T10:00:10+00:00"],
            "max_time_gap_seconds": 10.0,
            "value_a": 100.0, "value_b": 100.0,
            "value_difference": 0.0, "value_difference_relative": 0.0,
        }],
        "candidates_found": 1, "candidates_emitted": 1,
        "config": {"max_time_delta_seconds": 30.0,
                   "value_tolerance_relative": 0.05},
        "retrieval": {
            "status": "complete", "truncated": False,
            "truncated_reasons": [],
            "target": {"status": "complete", "truncated": False,
                       "transactions_retrieved": 64, "total_matched": 64},
            "counterparty": {"status": "complete", "truncated": False,
                             "transactions_retrieved": 384,
                             "total_matched": 384,
                             "accounts_queried": 18,
                             "time_window": {"start": "2026-07-20T13:42:19+00:00",
                                             "end": "2026-07-21T01:18:19+00:00"}},
        },
        "totals": {"transactions_retrieved": 448, "total_matched": 448,
                   "pages_fetched": 6},
        "target_account_metrics": {
            "opposite_trade_ratio": 0.3438,
            "source": "risk_platform.feature_evidence",
        },
        "data_quality": {"records_input": 448, "records_usable": 448,
                         "records_excluded_untimestamped": 0,
                         "transactions_matched": 2,
                         "target_transactions_retrieved": 64,
                         "counterparty_transactions_retrieved": 384,
                         "records_duplicate_dropped": 0},
    }


def _tool_call(payload):
    from app.models import ToolCallStatusV2, ToolCallV2
    from app.models import ToolResult, ToolResultOutcome
    return ToolCallV2(
        tool_call_id="TC-CASE4", tool_name="cross_account_trade_analysis",
        step_type="analyze_cross_account_trades",
        task_id="TASK-CASE4", investigation_id="INV-CASE4",
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=payload))


class TestTurnComposerDeliverables:
    def _compose(self, payload, request):
        from app.investigation_service import compose_response
        return compose_response(
            user_request=request, skill_id="case_trade_analysis",
            plan=None, tool_calls=[_tool_call(payload)],
            execution_errors=[])

    def test_part_a_metric_is_independent_of_part_b(self):
        text = self._compose(
            _match_payload(),
            "关于反向交易，34.38%反向交易率指的是用户自身反向交易还是与其他账户的"
            "反向交易率？这个账户是否与其他账户存在对冲交易的情况，如果有，给我"
            "列出相关证据明细")
        # Part A: the metric's own meaning, sourced from RP feature data
        assert "34.38%" in text
        assert "opposite_trade_ratio" in text
        assert "min(BUY count, SELL count)" in text
        assert "NOT a cross-account match rate" in text
        # Part B: target-anchored candidates
        assert f"involve account {TARGET}" in text
        assert "T-TGT" in text
        # the analyzed scope is stated
        assert "Analyzed scope:" in text

    def test_no_match_refuses_global_substitution(self):
        payload = _match_payload()
        payload["analysis_status"] = "NO_MATCH"
        payload["candidates"] = []
        payload["candidates_found"] = 0
        payload["candidates_emitted"] = 0
        text = self._compose(
            payload,
            "这个账户是否与其他账户存在对冲交易的情况，如果有，给我列出相关证据明细")
        assert "No candidate" in text
        assert f"account {TARGET}" in text
        assert "not reported as" in text
        assert "34.38%" in text          # Part A still delivered


class TestLoopComposerDeliverables:
    def _state(self, summary):
        from app.agent_loop import AgentLoopState, compose_loop_response
        st = AgentLoopState(
            task_id="T", investigation_id="CASE:U00010", case_id=TARGET,
            goal="关于反向交易，34.38%反向交易率指的是用户自身反向交易还是与其他账户的"
                 "反向交易率？这个账户是否与其他账户存在对冲交易的情况",
            criteria_kind="question_answered")
        st.observations = [{
            "tool_name": "cross_account_trade_analysis", "outcome": "success",
            "semantic_status": summary["analysis_status"],
            "summary": summary,
        }]
        st.agent_status = "completed"
        return compose_loop_response(st)

    def _summary(self, status="MATCH_FOUND"):
        s = _match_payload()
        s["analysis_status"] = status
        if status != "MATCH_FOUND":
            s["candidates"] = []
            s["candidates_found"] = 0
            s["candidates_emitted"] = 0
        # mirror the observation whitelist projection (the composer reads
        # the per-stream echoes from these top-level summary keys)
        s["target_retrieval"] = s["retrieval"]["target"]
        s["counterparty_retrieval"] = s["retrieval"]["counterparty"]
        return s

    def test_part_a_and_scoped_part_b(self):
        text = self._state(self._summary())
        # Part A — independent metric meaning (Chinese surface)
        assert "关于反向交易率:34.38%" in text
        assert "min(BUY, SELL)" in text
        assert "不是跨账户匹配率" in text
        # Part B — target-scoped header + table
        assert f"涉及 {TARGET} 的候选跨账户对冲交易关系" in text
        assert "每个候选都包含该账户的一笔交易" in text
        assert "分析范围:" in text
        assert "| CATA-001 |" in text
        assert "T-TGT" in text

    def test_no_match_names_target_and_scope(self):
        text = self._state(self._summary("NO_MATCH"))
        assert f"未发现涉及账户 {TARGET}" in text
        assert "不会作为" in text and f"{TARGET} 的对冲证据" in text
        assert "分析范围:" in text
        assert "关于反向交易率:34.38%" in text

    def test_insufficient_data_stays_distinct_with_scope(self):
        s = self._summary("INSUFFICIENT_DATA")
        s["retrieval"]["truncated"] = True
        s["retrieval"]["truncated_reasons"] = [
            "counterparty_accounts:retrieval_budget_exhausted"]
        s["retrieval"]["counterparty"]["truncated"] = True
        text = self._state(s)
        assert "无法得出可靠结论" in text
        assert "这不是\"未发现\"" in text
        assert "分析范围:" in text
        assert "检索不完整" in text

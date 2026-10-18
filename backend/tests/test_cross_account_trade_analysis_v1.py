"""cross_account_trade_analysis — matching-phase contract tests.

Implements the baseline's 17-case evaluation matrix
(docs/baselines/2026-09-18-cross-account-trade-analysis-v1.md §10) plus
tool-boundary coverage: status mapping (§6/§12b.6), scope enforcement,
provenance, compact observation, error semantics, deterministic routing
(cross-account vs same-account self), composer wording, and artifact
integration. Deterministic throughout: matching-engine cases are pure
function tests; tool cases inject fakes; no real RP, no LLM.

Assertions pin EXACT account pairs, transaction ids, sides, match types,
gaps, differences, ordering, and counts — regressions must be visible.
"""

import json
from types import SimpleNamespace

import pytest

from app.adapters.risk_platform import RiskPlatformError
from app.domain_tools.cross_account_trade_analysis import (
    ANALYSIS_TYPE,
    cross_account_trade_analysis,
    find_candidates,
)
from app.models import (
    ToolCallStatusV2,
    ToolCallV2,
    ToolResultOutcome,
)
from app.trade_evidence import (
    AccountScope,
    AccountTransaction,
    RetrievalError,
    RetrievalStatus,
    TradeEvidenceRetrieval,
)

CFG = {
    "max_time_delta_seconds": 30.0,
    "value_tolerance_relative": 0.05,
    "value_tolerance_absolute_floor": 1.0,
    "max_legs_per_candidate": 5,
    "max_candidates": 20,
}
CFG_NS = SimpleNamespace(
    TRADE_ANALYSIS_MAX_ACCOUNTS=5,
    TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS=500,
    TRADE_ANALYSIS_MAX_PAGE_SIZE=100,
    TRADE_ANALYSIS_MAX_PAGES=10,
    TRADE_ANALYSIS_MAX_CANDIDATES=20,
    TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS=30.0,
    TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE=0.05,
    TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR=1.0,
    TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE=5,
)

T0 = "2026-08-19T10:00:00+00:00"


def tx(tid, account, side, value, offset_s, symbol="BTC"):
    minute, secs = divmod(offset_s, 60)
    ts = f"2026-08-19T10:{minute:02d}:{secs:02d}+00:00"
    return AccountTransaction(
        transaction_id=tid, account_id=account, symbol=symbol, side=side,
        price=100.0, quantity=value / 100.0, value=value, timestamp=ts)


def one(candidates):
    assert len(candidates) == 1, candidates
    return candidates[0]


# ---------------------------------------------------------------------------
# Cases 1-9, 14, 16, 17 — deterministic matching engine (pure functions)
# ---------------------------------------------------------------------------

class TestMatchingMatrix:
    def test_1_clear_one_to_one_match(self):
        out, dq = find_candidates([
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B", "U90002", "SELL", 100.0, 10),
        ], CFG)
        c = one(out)
        assert c["account_a"] == "U90001" and c["account_b"] == "U90002"
        assert c["side_a"] == "BUY" and c["side_b"] == "SELL"
        assert c["transaction_ids_a"] == ["T-A"]
        assert c["transaction_ids_b"] == ["T-B"]
        assert c["symbol"] == "BTC"
        assert c["match_type"] == "1_to_1"
        assert c["max_time_gap_seconds"] == 10.0
        assert c["value_a"] == 100.0 and c["value_b"] == 100.0
        assert c["value_difference"] == 0.0
        assert c["value_difference_relative"] == 0.0
        assert dq["transactions_matched"] == 2

    def test_2_clear_one_to_N_match(self):
        out, _ = find_candidates([
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B1", "U90002", "SELL", 60.0, 5),
            tx("T-B2", "U90002", "SELL", 40.0, 12),
        ], CFG)
        c = one(out)
        # 1 trade on side A vs 2 accumulated legs on side B
        assert c["match_type"] == "1_to_N"
        assert c["account_a"] == "U90001" and c["account_b"] == "U90002"
        assert c["transaction_ids_a"] == ["T-A"]
        assert c["transaction_ids_b"] == ["T-B1", "T-B2"]
        assert c["value_b"] == 100.0
        assert c["max_time_gap_seconds"] == 12.0

    def test_3_clear_N_to_one_match(self):
        # 2 legs on the EARLIEST account (side a) vs 1 trade on side b
        out, _ = find_candidates([
            tx("T-A1", "U90001", "BUY", 60.0, 0),
            tx("T-A2", "U90001", "BUY", 40.0, 5),
            tx("T-B", "U90002", "SELL", 100.0, 10),
        ], CFG)
        c = one(out)
        assert c["match_type"] == "N_to_1"
        assert c["account_a"] == "U90001" and c["account_b"] == "U90002"
        assert c["transaction_ids_a"] == ["T-A1", "T-A2"]
        assert c["transaction_ids_b"] == ["T-B"]

    def test_4_same_account_only_never_matches(self):
        out, _ = find_candidates([
            tx("T-A1", "U90001", "BUY", 100.0, 0),
            tx("T-A2", "U90001", "SELL", 100.0, 5),
        ], CFG)
        assert out == []          # self-opposite is NOT a cross-account candidate

    def test_5_same_side_never_matches(self):
        out, _ = find_candidates([
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B", "U90002", "BUY", 100.0, 5),
        ], CFG)
        assert out == []

    def test_6_different_instruments_never_match(self):
        out, _ = find_candidates([
            tx("T-A", "U90001", "BUY", 100.0, 0, symbol="BTC"),
            tx("T-B", "U90002", "SELL", 100.0, 5, symbol="ETH"),
        ], CFG)
        assert out == []

    def test_7_time_outside_tolerance_never_matches(self):
        out, _ = find_candidates([
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B", "U90002", "SELL", 100.0, 31),
        ], CFG)
        assert out == []

    def test_8_value_outside_tolerance_never_matches(self):
        out, _ = find_candidates([
            tx("T-A", "U90001", "BUY", 200.0, 0),
            tx("T-B", "U90002", "SELL", 150.0, 5),   # 25% off, tolerance 5%
        ], CFG)
        assert out == []

    def test_9_exact_boundary_matches_are_inclusive(self):
        # Δt == 30s exactly AND |Δv| == tolerance exactly → candidate
        out, _ = find_candidates([
            tx("T-A", "U90001", "BUY", 200.0, 0),
            tx("T-B", "U90002", "SELL", 190.0, 30),   # diff 10 == 5% of 200
        ], CFG)
        c = one(out)
        assert c["max_time_gap_seconds"] == 30.0
        assert c["value_difference"] == pytest.approx(10.0)
        assert c["value_difference_relative"] == pytest.approx(0.05)

    def test_14_ranking_deterministic_by_strength(self):
        # candidate Y: perfect value match but larger gap;
        # candidate X: tiny value difference and smaller gap.
        # Ranking dims: relative difference → max gap → leg count.
        out, _ = find_candidates([
            tx("T-X1", "U90001", "BUY", 100.0, 0),
            tx("T-X2", "U90002", "SELL", 101.0, 5),    # rel 1/101, gap 5
            tx("T-Y1", "U90003", "BUY", 100.0, 0),
            tx("T-Y2", "U90004", "SELL", 100.0, 25),   # rel 0, gap 25
        ], CFG)
        assert [c["match_type"] for c in out] == ["1_to_1", "1_to_1"]
        # relative difference 0 ranks FIRST despite its larger gap
        assert out[0]["transaction_ids_a"] == ["T-Y1"]
        assert out[0]["rank"] == 1 and out[1]["rank"] == 2
        assert out[0]["candidate_id"] == "CATA-001"
        assert out[1]["candidate_id"] == "CATA-002"

    def test_16_transaction_never_reused_across_candidates(self):
        # C's trade could also match T-B, but T-B is consumed by the
        # first (earliest) candidate: suppressed, never duplicated.
        out, dq = find_candidates([
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B", "U90002", "SELL", 100.0, 5),
            tx("T-C", "U90003", "BUY", 100.0, 8),
        ], CFG)
        c = one(out)
        all_ids = set(c["transaction_ids_a"] + c["transaction_ids_b"])
        assert all_ids == {"T-A", "T-B"}
        assert dq["transactions_matched"] == 2

    def test_17_deterministic_repeatability(self):
        records = [
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B", "U90002", "SELL", 100.0, 10),
            tx("T-C", "U90003", "BUY", 100.0, 0),
            tx("T-D", "U90004", "SELL", 99.0, 3),
        ]
        first = find_candidates(records, CFG)
        second = find_candidates(list(reversed(records)), CFG)
        assert json.dumps(first) == json.dumps(second)   # order-independent

    def test_unparseable_timestamps_excluded_and_reported(self):
        broken = AccountTransaction(
            transaction_id="T-X", account_id="U90002", symbol="BTC",
            side="SELL", price=1.0, quantity=100.0, value=100.0,
            timestamp="not-a-time")
        out, dq = find_candidates([
            tx("T-A", "U90001", "BUY", 100.0, 0), broken,
        ], CFG)
        assert out == []
        assert dq["records_excluded_untimestamped"] == 1

    def test_aggregation_leg_cap_bounds_candidates(self):
        # 1 anchor of 100 vs 7 legs of 10: the cap (5) bounds accumulation
        # at 50 — short of the anchor within tolerance → no candidate; the
        # 6th/7th legs can never be reached (no backtracking).
        records = [tx("T-A", "U90001", "BUY", 100.0, 0)]
        records += [tx(f"T-B{i}", "U90002", "SELL", 10.0, i + 1)
                    for i in range(7)]
        out, dq = find_candidates(records, CFG)
        assert out == []
        assert dq["transactions_matched"] == 0
        # at exactly the cap the match still forms: 5 legs of 20 == 100
        records = [tx("T-A", "U90001", "BUY", 100.0, 0)]
        records += [tx(f"T-B{i}", "U90002", "SELL", 20.0, i + 1)
                    for i in range(5)]
        out, _ = find_candidates(records, CFG)
        c = one(out)
        assert len(c["transaction_ids_b"]) == 5
        assert c["match_type"] == "1_to_N"


# ---------------------------------------------------------------------------
# Tool-level behavior — status mapping, boundaries, provenance (§6)
# ---------------------------------------------------------------------------

def retrieval_of(records, status=RetrievalStatus.COMPLETE,
                 total_matched=None, warnings=()):
    return TradeEvidenceRetrieval(
        status=status,
        records=records,
        observation={"analysis": "trade_evidence_retrieval",
                     "status": status.value},
        total_matched=total_matched if total_matched is not None
        else len(records),
        pages_fetched=1,
        warnings=list(warnings),
    )


@pytest.fixture()
def scoped(monkeypatch):
    """Pin the authoritative account scope for tool-unit tests."""
    scope = AccountScope(case_id="U90001",
                         allowed={"U90001", "U90002", "U90003"})
    monkeypatch.setattr(
        "app.domain_tools.cross_account_trade_analysis.resolve_account_scope",
        lambda *a, **kw: scope)
    return scope


class TestToolStatusSemantics:
    def test_10_no_match_with_complete_evidence(self, scoped, monkeypatch):
        records = [tx("T-A", "U90001", "BUY", 100.0, 0),
                   tx("T-B", "U90002", "BUY", 100.0, 5)]   # same side
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: retrieval_of(records))
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        assert out.outcome == ToolResultOutcome.SUCCESS
        assert out.data["analysis_status"] == "NO_MATCH"
        assert "evidence_missing" not in out.data
        assert out.error is None
        assert out.data["candidates"] == []
        assert out.data["config"]["max_time_delta_seconds"] == 30.0

    def test_11_truncated_with_zero_candidates_is_insufficient(self, scoped,
                                                               monkeypatch):
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: retrieval_of(
                [tx("T-A", "U90001", "BUY", 100.0, 0)],
                status=RetrievalStatus.TRUNCATED,
                warnings=["budget"]))
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        assert out.outcome == ToolResultOutcome.SUCCESS
        assert out.data["analysis_status"] == "INSUFFICIENT_DATA"
        assert out.data["evidence_missing"] is True
        assert out.next_data_needed
        # NEVER a no-match on partial evidence
        assert out.data["analysis_status"] != "NO_MATCH"

    def test_11_truncated_with_candidates_is_match_found_and_flagged(
            self, scoped, monkeypatch):
        records = [tx("T-A", "U90001", "BUY", 100.0, 0),
                   tx("T-B", "U90002", "SELL", 100.0, 10)]
        result = retrieval_of(records, status=RetrievalStatus.TRUNCATED)
        result.truncated_reasons[:] = ["retrieval_budget_exhausted"]
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: result)
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        assert out.data["analysis_status"] == "MATCH_FOUND"
        assert len(out.data["candidates"]) == 1
        assert out.data["retrieval"]["truncated"] is True
        assert any("partial evidence" in w for w in out.warnings)

    def test_12_no_data_is_insufficient_never_error_or_no_match(
            self, scoped, monkeypatch):
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: retrieval_of([], status=RetrievalStatus.NO_DATA,
                                          total_matched=0))
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        assert out.outcome == ToolResultOutcome.SUCCESS
        assert out.data["analysis_status"] == "INSUFFICIENT_DATA"
        assert out.error is None

    def test_13_unusable_timestamps_surface_in_data_quality(self, scoped,
                                                            monkeypatch):
        broken = AccountTransaction(
            transaction_id="T-X", account_id="U90002", symbol="BTC",
            side="SELL", price=1.0, quantity=1.0, value=100.0,
            timestamp="bad")
        records = [tx("T-A", "U90001", "BUY", 100.0, 0), broken]
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: retrieval_of(records))
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        assert out.data["data_quality"]["records_excluded_untimestamped"] == 1

    def test_retrieval_error_is_integration_error_never_empty(
            self, scoped, monkeypatch):
        failed = TradeEvidenceRetrieval(
            status=RetrievalStatus.ERROR,
            observation={"status": "error"},
            error=RetrievalError(code="RISK_PLATFORM_UNAVAILABLE",
                                 message="RP timed out"))
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: failed)
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        assert out.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert out.error.code == "RISK_PLATFORM_UNAVAILABLE"
        assert "timed out" in out.error.message

    def test_scope_resolution_failure_is_integration_error(self, monkeypatch):
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "resolve_account_scope",
            lambda *a, **kw: AccountScope(
                case_id="U90001", allowed={"U90001"},
                error=RetrievalError(code="RISK_PLATFORM_AUTH_FAILED",
                                     message="auth failed")))
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        assert out.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert out.error.code == "RISK_PLATFORM_AUTH_FAILED"

    def test_input_validation(self):
        blank = cross_account_trade_analysis("  ", config=CFG_NS)
        assert blank.outcome == ToolResultOutcome.VALIDATION_ERROR
        wrong = cross_account_trade_analysis(
            "U90001", analysis_type="something_else", config=CFG_NS)
        assert wrong.outcome == ToolResultOutcome.VALIDATION_ERROR


class TestToolCandidateEvidence:
    def test_15_candidate_limit_found_vs_emitted(self, scoped, monkeypatch):
        # three independent candidate pairs, limit 2
        records = [
            tx("T-A1", "U90001", "BUY", 100.0, 0),
            tx("T-B1", "U90002", "SELL", 100.0, 1),
            tx("T-A2", "U90001", "BUY", 100.0, 100),
            tx("T-B2", "U90002", "SELL", 100.0, 101),
            tx("T-A3", "U90001", "BUY", 100.0, 200),
            tx("T-B3", "U90002", "SELL", 100.0, 201),
        ]
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: retrieval_of(records))
        cfg = SimpleNamespace(**{**vars(CFG_NS),
                                 "TRADE_ANALYSIS_MAX_CANDIDATES": 2})
        out = cross_account_trade_analysis("U90001", config=cfg)
        assert out.data["analysis_status"] == "MATCH_FOUND"
        assert out.data["candidates_found"] == 3
        assert out.data["candidates_emitted"] == 2
        assert len(out.data["candidates"]) == 2
        assert any("top 2 are reported" in w for w in out.warnings)

    def test_provenance_evidence_refs_cover_every_leg(self, scoped,
                                                      monkeypatch):
        records = [
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B1", "U90002", "SELL", 60.0, 5),
            tx("T-B2", "U90002", "SELL", 40.0, 12),
        ]
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: retrieval_of(records))
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        ref_ids = sorted(r.id for r in out.evidence_refs)
        assert ref_ids == ["T-A", "T-B1", "T-B2"]
        assert all(r.kind == "transaction" for r in out.evidence_refs)

    def test_compact_observation_never_dumps_raw_retrieval(
            self, scoped, monkeypatch):
        # 5 retrieved records; only 2 belong to the single candidate
        records = [
            tx("T-A", "U90001", "BUY", 100.0, 0),
            tx("T-B", "U90002", "SELL", 100.0, 10),
            tx("T-X1", "U90001", "BUY", 500.0, 300),
            tx("T-X2", "U90002", "SELL", 400.0, 301),
            tx("T-X3", "U90002", "SELL", 300.0, 302),
        ]
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence",
            lambda *a, **kw: retrieval_of(records, total_matched=5))
        out = cross_account_trade_analysis("U90001", config=CFG_NS)
        payload_json = json.dumps(out.data)
        assert "T-X1" not in payload_json and "T-X2" not in payload_json
        assert "T-X3" not in payload_json           # non-candidate rows stay internal
        assert "records" not in out.data            # no raw record dump key
        assert out.data["totals"]["transactions_retrieved"] == 5
        # target-anchored analysis echoes the two retrieval streams' full
        # counts summed (disjoint populations on the real path; this fake
        # serves the same 5-row page to both streams, hence 5 + 5)
        assert out.data["totals"]["total_matched"] == 10

    def test_finding_anchor_recorded_not_filtering(self, scoped, monkeypatch):
        monkeypatch.setattr(
            "app.domain_tools.cross_account_trade_analysis."
            "retrieve_trade_evidence", lambda *a, **kw: retrieval_of([]))
        out = cross_account_trade_analysis("U90001", finding_id="F2",
                                           config=CFG_NS)
        assert out.data["finding_id"] == "F2"


class TestFullPathThroughRetrieval:
    """Tool → resolve_account_scope → retrieve_trade_evidence with a fake
    adapter (both seams), no monkeypatching of the retrieval layer."""

    def test_end_to_end_match_through_fake_adapter(self):
        class FakeAdapter:
            def fetch_network_signals(self, user_id, limit=None):
                return {"connected_account_count": 1, "connected_accounts": [
                    {"user_id": "U90002"}]}

            def fetch_account_transactions(self, user_id, account_ids=None,
                                           start_time=None, end_time=None,
                                           symbols=None, limit=None,
                                           offset=None):
                return {
                    "case_user_id": user_id,
                    "requested_accounts": account_ids,
                    "transactions": [
                        {"transaction_id": "T-A", "account_id": "U90001",
                         "symbol": "BTC", "side": "BUY", "price": 100.0,
                         "quantity": 1.0, "value": 100.0,
                         "timestamp": "2026-08-19T10:00:00+00:00"},
                        {"transaction_id": "T-B", "account_id": "U90002",
                         "symbol": "BTC", "side": "SELL", "price": 100.0,
                         "quantity": 1.0, "value": 100.0,
                         "timestamp": "2026-08-19T10:00:10+00:00"},
                    ],
                    "total_matched": 2, "returned_count": 2, "offset": 0,
                    "limit": 100, "truncated": False, "has_more": False,
                    "next_offset": None,
                }

        out = cross_account_trade_analysis(
            "U90001", adapter=FakeAdapter(), config=CFG_NS)
        assert out.outcome == ToolResultOutcome.SUCCESS
        c = one(out.data["candidates"])
        assert c["match_type"] == "1_to_1"
        assert c["account_a"] == "U90001" and c["account_b"] == "U90002"
        # applied tolerances are visible on the evidence (§9: never hidden)
        assert out.data["config"]["value_tolerance_relative"] == 0.05

    def test_scope_failure_through_fake_adapter(self):
        class DownAdapter:
            def fetch_network_signals(self, user_id, limit=None):
                raise RiskPlatformError("unavailable", "network down")

        out = cross_account_trade_analysis(
            "U90001", adapter=DownAdapter(), config=CFG_NS)
        assert out.outcome == ToolResultOutcome.INTEGRATION_ERROR


# ---------------------------------------------------------------------------
# Routing: cross-account intent must win over same-account wording
# ---------------------------------------------------------------------------

class TestRouting:
    def test_cross_account_regex_matches_all_phrasings(self):
        from app.planner_v2 import _is_cross_account_trade_request
        for text in ("find opposite trades across accounts",
                     "look for cross-account opposite-side trades",
                     "check whether related accounts traded opposite sides",
                     "trades between accounts", "跨账户对倒"):
            assert _is_cross_account_trade_request(text), text

    def test_cross_account_wins_over_self_opposite(self, monkeypatch):
        from app.planner_v2 import (_is_cross_account_trade_request,
                                    _is_opposite_trade_request)
        text = "Find opposite trades across accounts."
        # the documented ordering hazard: BOTH regexes match...
        assert _is_opposite_trade_request(text)
        # ...and the planner evaluates cross-account FIRST.
        from app.planner_v2 import PlannerV2
        from app.models import FindingCapability, FocusSource, \
            InvestigationContext

        class LLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return ('{"skill_id": "trade_investigation", "goal": "g", '
                        '"steps": [{"type": "inspect_self_opposite_trades", '
                        '"reason": "r"}]}')

        caps = FindingCapability.model_validate(["self_opposite_trades"])
        ctx = InvestigationContext(case_id="U90001",
                                   focused_finding_id="F2",
                                   focus_source=FocusSource.USER_SELECTED)
        plan = PlannerV2(LLM()).plan(
            "Find opposite trades across accounts.", ctx,
            ["trade_investigation"], caps)
        # AL-2.6: the deterministic case-level branch now plans this
        # governed step directly (the LLM script below is only a fallback
        # that must never be reached for this intent); the bundle step
        # keeps the run bounded when the analysis honestly reports
        # insufficient data.
        assert plan.steps[0].type == "analyze_cross_account_trades"
        assert plan.steps[0].tool_name == "cross_account_trade_analysis"
        assert plan.steps[0].arguments == {
            "analysis_type": "cross_account_opposite_trade"}
        assert [s.type for s in plan.steps] == [
            "analyze_cross_account_trades", "generate_artifact"]

    def test_self_wording_still_routes_to_self_step(self):
        from app.planner_v2 import (_is_cross_account_trade_request,
                                    _is_opposite_trade_request)
        text = "Show me the opposite trade."
        assert _is_opposite_trade_request(text)
        assert not _is_cross_account_trade_request(text)

    def test_executor_injects_context_arguments_for_analysis_step(self):
        from app.executor_v2 import ExecutorV2
        from app.models import InvestigationContext, PlanStep
        # the planner applies the registry lock; runtime injects context
        step = PlanStep(step_id="S1", type="analyze_cross_account_trades",
                        arguments={
                            "analysis_type": "cross_account_opposite_trade"})
        ctx = InvestigationContext(case_id="U90001",
                                   focused_finding_id="F2")
        args, _sources = ExecutorV2._runtime_arguments(step, ctx)
        assert args["case_id"] == "U90001"
        assert args["finding_id"] == "F2"
        assert args["analysis_type"] == "cross_account_opposite_trade"


# ---------------------------------------------------------------------------
# Composer + artifact integration (evidence language rules)
# ---------------------------------------------------------------------------

def _analysis_call(data, outcome=ToolResultOutcome.SUCCESS,
                   next_data_needed=None):
    from app.models import ToolResult
    return ToolCallV2(
        tool_call_id="TC-CATA-1",
        investigation_id="CASE:U90001", task_id="TASK-1",
        tool_name="cross_account_trade_analysis",
        arguments={"case_id": "U90001",
                   "analysis_type": "cross_account_opposite_trade"},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=outcome, data=data,
                          next_data_needed=next_data_needed))


MATCH_PAYLOAD = {
    "analysis": "cross_account_trade_analysis",
    "analysis_type": ANALYSIS_TYPE,
    "case_id": "U90001", "finding_id": "F2",
    "analysis_status": "MATCH_FOUND",
    "candidates": [{
        "candidate_id": "CATA-001", "rank": 1, "match_type": "1_to_1",
        "account_a": "U90001", "account_b": "U90002",
        "symbol": "BTC", "side_a": "BUY", "side_b": "SELL",
        "transaction_ids_a": ["T-A"], "transaction_ids_b": ["T-B"],
        "max_time_gap_seconds": 10.0,
        "value_a": 100.0, "value_b": 100.0,
        "value_difference": 0.0, "value_difference_relative": 0.0,
    }],
    "candidates_found": 1, "candidates_emitted": 1,
    "config": {"max_time_delta_seconds": 30.0,
               "value_tolerance_relative": 0.05,
               "value_tolerance_absolute_floor": 1.0,
               "max_legs_per_candidate": 5, "max_candidates": 20},
    "retrieval": {"status": "complete", "truncated": False,
                  "truncated_reasons": []},
    "totals": {"transactions_retrieved": 2, "total_matched": 2,
               "pages_fetched": 1},
    "data_quality": {"records_input": 2, "records_usable": 2,
                     "records_excluded_untimestamped": 0,
                     "transactions_matched": 2},
}

INSUFFICIENT_PAYLOAD = {
    "analysis": "cross_account_trade_analysis",
    "analysis_status": "INSUFFICIENT_DATA",
    "case_id": "U90001", "finding_id": None,
    "evidence_missing": True,
    "next_data_needed": ["complete transaction evidence"],
    "candidates": [], "candidates_found": 0, "candidates_emitted": 0,
    "config": MATCH_PAYLOAD["config"],
    "retrieval": {"status": "truncated", "truncated": True,
                  "truncated_reasons": ["retrieval_budget_exhausted"]},
    "totals": {"transactions_retrieved": 3, "total_matched": 500,
               "pages_fetched": 5},
    "data_quality": {},
}

NO_MATCH_PAYLOAD = {
    "analysis": "cross_account_trade_analysis",
    "analysis_status": "NO_MATCH",
    "case_id": "U90001", "finding_id": None,
    "candidates": [], "candidates_found": 0, "candidates_emitted": 0,
    "config": MATCH_PAYLOAD["config"],
    "retrieval": {"status": "complete", "truncated": False,
                  "truncated_reasons": []},
    "totals": {"transactions_retrieved": 4, "total_matched": 4,
               "pages_fetched": 2},
    "data_quality": {},
}


class TestComposerWording:
    def _compose(self, data):
        from app.investigation_service import compose_response
        return compose_response(
            user_request="Find opposite trades across accounts.",
            skill_id="trade_investigation", plan=None,
            tool_calls=[_analysis_call(data)], execution_errors=[])

    def test_match_found_uses_candidate_terminology(self):
        text = self._compose(MATCH_PAYLOAD)
        assert "1 candidate cross-account opposite-trade relationship" in text
        assert "CATA-001" in text and "1_to_1" in text
        assert "U90001 BUY vs U90002 SELL" in text
        assert "T-A, T-B" in text
        assert "not conclusions about intent" in text
        # forbidden framings
        for fragment in ("confirmed", "collusion", "proves coordination"):
            assert fragment not in text

    def test_no_match_is_scoped_not_exoneration(self):
        text = self._compose(NO_MATCH_PAYLOAD)
        assert "No candidate cross-account opposite-trade" in text
        assert "analyzed accounts and data window only" in text

    def test_insufficient_data_is_distinct_from_no_match(self):
        from app.investigation_service import compose_response
        text = compose_response(
            user_request="Find opposite trades across accounts.",
            skill_id="trade_investigation", plan=None,
            tool_calls=[_analysis_call(
                INSUFFICIENT_PAYLOAD,
                next_data_needed=list(INSUFFICIENT_PAYLOAD["next_data_needed"]))],
            execution_errors=[])
        assert "could not reach a reliable conclusion" in text
        assert "This is not a no-match result" in text
        assert "Additional data needed" in text


class TestArtifactIntegration:
    def _bundle(self, data):
        from app.domain_tools.artifact_bundle import artifact_bundle
        out = artifact_bundle(
            scope="case", case_id="U90001", task_id="TASK-1",
            source_tool_calls=[_analysis_call(data)])
        assert out.outcome == ToolResultOutcome.SUCCESS
        return out

    def test_candidate_section_with_criteria_and_limitations(self):
        payload = {**MATCH_PAYLOAD,
                   "retrieval": {"status": "truncated", "truncated": True,
                                 "truncated_reasons": ["account_budget"]}}
        out = self._bundle(payload)
        content = out.data["artifact"]["content"]
        assert "## Candidate Cross-Account Relationships" in content
        assert "Matching criteria (system configuration)" in content
        assert "CATA-001" in content and "T-A, T-B" in content
        assert "candidate relationships" in content
        assert "Retrieval was bounded" in content
        # provenance closure: the analysis call contributed the section
        assert "TC-CATA-1" in out.data["artifact"]["source_tool_calls"]

    def test_insufficient_analysis_renders_bounded_statement(self):
        out = self._bundle(INSUFFICIENT_PAYLOAD)
        content = out.data["artifact"]["content"]
        assert "insufficient for a reliable conclusion" in content
        assert "not a no-match result" in content

"""U00033 post-acceptance quality fixes — bounded regression locks.

1. ML feature list: partial display is LABELED (前 N 项/共 M 项), never
   reads as complete.
2. Candidate rendering: every candidate line carries direction, symbol
   values, transaction ids, time gap and relative difference; the
   criteria statement covers BOTH admission paths (relative tolerance OR
   near-zero-notional floor) and floor-admitted candidates are labeled.
3. Proposal order: a combined related-accounts + hedging ask renders the
   related-account list FIRST (user mention order), pure asks unchanged.
4. The matcher itself is NOT changed here: floor-admitted candidates are
   the baseline's DECIDED (Relative + absolute-floor) semantics — the
   fix is transparency, not threshold edits.
"""

from app.agent_loop import _gsp_render_output_facts, _signal_answer_lines
from app.domain_tools.cross_account_trade_analysis import (
    _candidate_view, _value_matches)
from app.goal_resolution import deterministic_contract_proposal
from app.goal_resolution import RequiredOutput
from app.skills import EvidenceType


def _ml_summary():
    return {
        "ml_score": 99.6,
        "detection_method": "LightGBM",
        "score_interpretation": "system signal",
        "attribution_available": False,
        "available_feature_values": {
            f"feature_{i}": i for i in range(12)},
    }


class TestIssue1MLFeatureListLabeling:
    def test_partial_feature_list_is_labeled(self):
        lines = "\n".join(_signal_answer_lines(_ml_summary()))
        assert "前 5 项" in lines and "共 12 项" in lines
        assert "（共 12 项）" not in lines    # never reads as complete

    def test_complete_feature_list_has_no_partial_label(self):
        s = _ml_summary()
        s["available_feature_values"] = {"a": 1, "b": 2}
        lines = "\n".join(_signal_answer_lines(s))
        assert "前 5 项" not in lines

    def test_gsp_feature_facts_branch_labels_partial(self):
        from app.goal_resolution import RequiredOutput, ScopeLevel
        out = RequiredOutput(
            output_id="O1", claim="c", claim_kind="explanation",
            subject_ref="F1", scope="finding_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION])
        summary = {"feature_facts": [
            {"name": f"f{i}", "value": i} for i in range(9)]}
        lines = "\n".join(_gsp_render_output_facts(out, summary))
        assert "前 5 项" in lines and "共 9 项" in lines


def _candidate(relative, diff, cid="CATA-001"):
    return {
        "candidate_id": cid, "match_type": "1_to_1",
        "account_a": "UA", "side_a": "BUY",
        "account_b": "UB", "side_b": "SELL",
        "symbol": "SYN", "max_time_gap_seconds": 0.5,
        "value_a": 100.0, "value_b": 100.0 - diff,
        "value_difference": diff,
        "value_difference_relative": relative,
        "transaction_ids_a": ["TA1"], "transaction_ids_b": ["TB1"],
    }


def _trade_summary(candidates, rel_tol=0.05, floor=1.0):
    return {
        "candidates": candidates,
        "candidates_found": len(candidates),
        "candidates_emitted": len(candidates),
        "analysis_scope": "related_account_set",
        "max_time_delta_seconds": 30,
        "value_tolerance_relative": rel_tol,
        "value_tolerance_absolute_floor": floor,
        "subject_ref": "UC",
    }


class TestIssue3CandidateDetailAndCriteria:
    def test_candidate_line_carries_direction_values_and_tx_ids(self):
        out = RequiredOutput = __import__(
            "app.goal_resolution", fromlist=["RequiredOutput"]
        ).RequiredOutput(
            output_id="O1", claim="hedging", claim_kind="existence_check",
            subject_ref="UC", scope="case_level",
            evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS])
        out.satisfaction = "satisfied"
        out.polarity = "positive"
        summary = _trade_summary([_candidate(0.041, 4.1)])
        lines = "\n".join(_gsp_render_output_facts(out, summary))
        assert "BUY" in lines and "SELL" in lines
        assert "TA1, TB1" in lines
        assert "4.1%" in lines
        assert "价值差 ≤ 较大一侧的 5%" in lines
        assert "绝对下限" in lines            # dual criteria stated

    def test_floor_admitted_candidate_is_labeled(self):
        from app.goal_resolution import RequiredOutput
        out = RequiredOutput(
            output_id="O1", claim="hedging", claim_kind="existence_check",
            subject_ref="UC", scope="case_level",
            evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS])
        out.satisfaction = "satisfied"
        out.polarity = "positive"
        summary = _trade_summary([_candidate(0.971, 0.2097, "CATA-005")])
        lines = "\n".join(_gsp_render_output_facts(out, summary))
        assert "97.1%" in lines                          # never hidden
        # admission basis, stated against the ACTUAL matcher rule:
        # diff <= absolute floor (quote ccy), relative tolerance N/A
        assert "按绝对下限 放行" in lines
        assert "未适用相对容差" in lines
        assert "绝对下限 1.0[计价币]" in lines
        # the WRONG wording (both sides below the floor) must not appear:
        # CATA-005 is 0.0063 vs 0.216 — one side is, the other is not
        # below the floor; and CATA-004 (1.41/0.69) never satisfies it
        assert "双侧名义价值均低于绝对下限" not in lines

    def test_relative_matched_candidate_has_no_floor_label(self):
        from app.goal_resolution import RequiredOutput
        out = RequiredOutput(
            output_id="O1", claim="hedging", claim_kind="existence_check",
            subject_ref="UC", scope="case_level",
            evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS])
        out.satisfaction = "satisfied"
        out.polarity = "positive"
        summary = _trade_summary([_candidate(0.041, 4.1)])
        lines = "\n".join(_gsp_render_output_facts(out, summary))
        assert "按绝对下限 放行" not in lines   # relative match: no basis note


class TestIssue4MatcherSemanticsUnchanged:
    def test_relative_tolerance_semantics(self):
        cfg = {"value_tolerance_relative": 0.05,
               "value_tolerance_absolute_floor": 1.0}
        assert _value_matches(100.0, 103.0, cfg)       # 2.9% ≤ 5%
        assert not _value_matches(100.0, 110.0, cfg)   # 9.5% > 5%
        # near-zero notionals: the floor exempts dust pairs
        assert _value_matches(0.0063, 0.216, cfg)

    def test_candidate_view_carries_all_pair_evidence(self):
        from app.domain_tools.cross_account_trade_analysis import (
            AccountTransaction, _Candidate)
        anchor = AccountTransaction(
            transaction_id="TA1", account_id="UA", symbol="SYN",
            side="BUY", price=1.0, quantity=100.0, value=100.0,
            timestamp="2026-01-01T00:00:01Z")
        leg = AccountTransaction(
            transaction_id="TB1", account_id="UB", symbol="SYN",
            side="SELL", price=1.0, quantity=99.0, value=99.0,
            timestamp="2026-01-01T00:00:01.5Z")
        view = _candidate_view(_Candidate(anchor, [leg]))
        assert view["side_a"] == "BUY" and view["side_b"] == "SELL"
        assert view["transaction_ids_a"] == ["TA1"]
        assert view["transaction_ids_b"] == ["TB1"]
        assert view["value_a"] == 100.0 and view["value_b"] == 99.0


class TestIssue3ProposalOrder:
    def test_combined_ask_lists_related_before_hedging(self):
        Q = ("把关联账户的明细发我，并且查询关联账户中是否有对冲交易，"
             "如有，交易明细也发我")
        p = deterministic_contract_proposal(Q, None, "U90001",
                                            case_findings=[])
        claims = [o["claim"] for o in p["required_outputs"]]
        assert len(claims) == 2
        assert "关联" in claims[0]          # network attribution FIRST
        assert "对冲" in claims[1]          # hedging SECOND

    def test_pure_asks_keep_single_deliverable(self):
        rel = deterministic_contract_proposal(
            "哪些账号是这个账号的关联账号", None, "U90001", case_findings=[])
        hedge = deterministic_contract_proposal(
            "这个账户和其他账户之间有没有对冲交易", None, "U90001",
            case_findings=[])
        assert len(rel["required_outputs"]) == 1
        assert len(hedge["required_outputs"]) == 1


class TestFullCandidateDelivery:
    """P1: a user asking for all candidate details gets ALL emitted
    candidates in the dialogue — the renderer no longer truncates below
    the system emission cap. The system emission cap (config
    max_candidates) remains the only bound; found-vs-emitted accounting
    is still disclosed when the system cap dropped candidates."""

    def _render(self, candidates, found):
        out = RequiredOutput(
            output_id="O1", claim="hedging", claim_kind="existence_check",
            subject_ref="UC", scope="case_level",
            evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS])
        out.satisfaction = "satisfied"
        out.polarity = "positive"
        summary = _trade_summary(candidates)
        summary["candidates_found"] = found
        return "\n".join(_gsp_render_output_facts(out, summary))

    def test_all_emitted_candidates_rendered(self):
        from app.agent_loop import _gsp_render_output_facts
        cands = [_candidate(0.02 * (i + 1) % 0.9 + 0.01, 0.1, f"C-{i}")
                 for i in range(11)]
        lines = self._render(cands, 11)
        shown = sum(1 for l in lines.split("\n")
                    if l.strip().startswith("- C-"))
        assert shown == 11
        # count consistency: the answer claims 11 and shows 11
        assert "11 组" in lines

    def test_cap_note_only_when_system_cap_dropped_candidates(self):
        cands = [_candidate(0.02, 0.1, f"C-{i}") for i in range(11)]
        lines = self._render(cands, 11)     # found == emitted → full delivery
        assert "受系统候选上限" not in lines
        lines2 = self._render(cands[:8], 11)  # system cap dropped 3
        assert "受系统候选上限" in lines2
        assert "已完整交付其内的 8 组" in lines2
        shown = sum(1 for l in lines2.split("\n")
                    if l.strip().startswith("- C-"))
        assert shown == 8                   # all emitted still rendered

    def test_direction_and_evidence_preserved_per_candidate(self):
        cands = [_candidate(0.03, 0.2, f"C-{i}") for i in range(11)]
        lines = self._render(cands, 11)
        assert lines.count("BUY") == 11 and lines.count("SELL") == 11
        assert all(f"交易 TA1, TB1" in lines for _ in [0])

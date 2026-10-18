"""Display-cap disclosure invariants — parameterized overflow shapes.

Real production cases currently report ≤8 candidates per analysis, so
the overflow shapes (more candidates than a display cap) are exercised
here through the renderers directly. Invariants:

1. A display cap (GSP candidates 8/6, NETWORK 25, record table 10) that
   silently drops entries violates the summary/detail consistency rule —
   every capped render must carry a shown/total disclosure line.
2. found > emitted (the system candidate limit) must be user-visible in
   the turn answer — the listed count must never read as the total.
3. Within-cap renders carry NO truncation disclosure (no false
   partial markers on complete lists).
"""

import pytest

from app.agent_loop import _gsp_render_output_facts
from app.goal_resolution import RequiredOutput, ScopeLevel


def _trade_output(subject="UA"):
    return RequiredOutput(
        output_id="O1", claim="hedging", claim_kind="existence_check",
        subject_ref=subject, scope=ScopeLevel.CASE_LEVEL.value,
        evidence_requirements=["opposite_trade_relations"])


def _cands(n, target="UA", others=("UB", "UC", "UD")):
    out = []
    for i in range(n):
        if i % 2 == 0:
            a, b = target, others[i % len(others)]   # involves the case
        else:
            a, b = others[i % len(others)], \
                others[(i + 1) % len(others)]        # related-only
        out.append({
            "candidate_id": f"CATA-{i + 1:03d}", "account_a": a,
            "account_b": b, "symbol": "BTC",
            "max_time_gap_seconds": 1.0 + i,
            "transaction_ids_a": [f"TA{i}"], "transaction_ids_b": [f"TB{i}"],
        })
    return out


def _summary(cands, scope="related_account_set", truncated=False,
             accounts=None, found=None):
    s = {
        "analysis_scope": scope,
        "candidates": cands,
        "candidates_found": found if found is not None else len(cands),
        "candidates_emitted": len(cands),
        "accounts_analyzed": accounts or ["UA", "UB", "UC", "UD"],
        "target_account": "UA",
        "retrieval": {"truncated": truncated},
    }
    return s


class TestGspCandidateCapDisclosure:
    def test_set_scope_overflow_discloses_shown_total(self):
        lines = _gsp_render_output_facts(
            _trade_output(), _summary(_cands(12)))
        blob = "\n".join(lines)
        assert "对话内展示前 8 组" in blob
        assert "共报告 12 组候选" in blob
        # the disclosure must stay truthful: unshown candidates are in
        # the structured record — no artifact/view claim beyond that
        assert "未在对话中展示" in blob
        assert "完整候选清单见证据结果" not in blob
        # the first 8 ARE rendered
        assert "CATA-008" in blob and "CATA-009" not in blob

    def test_target_scope_overflow_discloses_shown_total(self):
        # 10 candidates ALL involving the target: the target branch shows
        # 6 and must disclose shown/total
        cands = []
        for i in range(10):
            c = _cands(1, target="UA")[0]
            c["candidate_id"] = f"CATA-{i + 1:03d}"
            c["account_b"] = ("UB", "UC", "UD")[i % 3]
            cands.append(c)
        lines = _gsp_render_output_facts(
            _trade_output(), _summary(cands, scope="target_account"))
        blob = "\n".join(lines)
        assert "对话内展示前 6 组" in blob
        assert "涉及 UA 的候选共 10 组" in blob
        assert "CATA-010" not in blob            # beyond the display cap

    def test_within_cap_carries_no_false_disclosure(self):
        lines = _gsp_render_output_facts(
            _trade_output(), _summary(_cands(6)))
        blob = "\n".join(lines)
        assert "对话内展示前" not in blob
        assert "CATA-006" in blob               # all present

    def test_truncated_scope_keeps_the_budget_note(self):
        lines = _gsp_render_output_facts(
            _trade_output(), _summary(_cands(3), truncated=True))
        blob = "\n".join(lines)
        assert "预算限制" in blob

    def test_related_only_pairs_still_labeled_in_overflow(self):
        # even in the overflow window the involvement split stays honest
        lines = _gsp_render_output_facts(
            _trade_output(), _summary(_cands(12)))
        blob = "\n".join(lines)
        assert "仅涉及关联成员" in blob
        assert "涉及案件账户" in blob


class TestNetworkCapDisclosure:
    def test_network_overflow_discloses(self):
        out = RequiredOutput(
            output_id="O1", claim="related accounts",
            claim_kind="retrieval", subject_ref="UA",
            scope=ScopeLevel.CASE_LEVEL.value,
            evidence_requirements=["network_relations"])
        summary = {
            "related_accounts": [f"R{i:03d}" for i in range(40)],
            "related_accounts_count": 40,
            "shared_devices": ["DEV1"],
        }
        lines = _gsp_render_output_facts(out, summary)
        blob = "\n".join(lines)
        assert "对话内展示前 25 个" in blob
        assert "关联账号共 40 个" in blob
        assert "R039" not in blob               # beyond the cap

    def test_network_within_cap_no_disclosure(self):
        out = RequiredOutput(
            output_id="O1", claim="related accounts",
            claim_kind="retrieval", subject_ref="UA",
            scope=ScopeLevel.CASE_LEVEL.value,
            evidence_requirements=["network_relations"])
        summary = {
            "related_accounts": [f"R{i:03d}" for i in range(12)],
            "related_accounts_count": 12,
        }
        lines = _gsp_render_output_facts(out, summary)
        blob = "\n".join(lines)
        assert "对话内展示前" not in blob
        assert "关联账号共 12 个" in blob


class TestFoundEmittedPartialSemantics:
    def test_postcondition_found_gt_emitted_is_partial(self):
        from app.goal_postconditions import _category_postcondition
        from app.goal_resolution import RequiredOutput, ScopeLevel,             OutputSatisfaction
        out = RequiredOutput(
            output_id="O1", claim="set hedging",
            claim_kind="existence_check", subject_ref="UA",
            scope=ScopeLevel.CASE_LEVEL.value,
            evidence_requirements=["opposite_trade_relations"])
        summary = _summary(_cands(20), found=27)
        s, pol, reason, completeness = _category_postcondition(
            "opposite_trade_relations", out, summary, "UA")
        assert s == OutputSatisfaction.PARTIALLY_SUPPORTED
        assert reason == "candidate_cap_partial"

    def test_postcondition_found_eq_emitted_stays_satisfied(self):
        from app.goal_postconditions import _category_postcondition
        from app.goal_resolution import OutputSatisfaction
        out = RequiredOutput(
            output_id="O1", claim="set hedging",
            claim_kind="existence_check", subject_ref="UA",
            scope=ScopeLevel.CASE_LEVEL.value,
            evidence_requirements=["opposite_trade_relations"])
        summary = _summary(_cands(12), found=12)
        s, pol, reason, completeness, detail = _category_postcondition(
            "opposite_trade_relations", out, summary, "UA")
        assert s == OutputSatisfaction.SATISFIED

    def test_gsp_header_discloses_undelivered(self):
        # found 27, rendered 8 (display cap): the note must use the
        # ACTUAL displayed count — the 12 rendered-dropped plus the
        # 7 beyond the candidate cap are ALL undelivered to the user
        lines = _gsp_render_output_facts(
            _trade_output(), _summary(_cands(20), found=27))
        blob = "\n".join(lines)
        assert "共发现 27 组候选" in blob
        assert "仅展示前 8 组" in blob
        assert "其余 19 组未通过当前回复交付" in blob
        # breakdown: 12 retained in record + 7 beyond the candidate cap
        assert "12 组保留于本次调查的结构化记录" in blob
        assert "7 组受系统候选上限未列出" in blob
        assert "当前没有其他可访问的完整清单" in blob
        # the emitted count must NOT be presented as user-delivered
        assert "列出前 20 组" not in blob

    def test_coverage_found_gt_emitted_is_partial(self):
        from app.request_coverage import _hedging_verdict, AspectVerdict
        made = {}

        def _make(satisfaction, detail=None, reason=None, tool=None):
            made["v"] = AspectVerdict(
                aspect_id="O1", claim="c", satisfaction=satisfaction,
                detail=detail, reason_code=reason, tool=tool)
            return made["v"]
        payload = _summary(_cands(20), found=27)
        payload["analysis_status"] = "MATCH_FOUND"
        v = _hedging_verdict(payload, _make)
        assert v.satisfaction == "partially_supported"
        assert v.reason_code == "candidate_cap_partial"


class TestTurnFoundEmittedDisclosure:
    def _compose(self, cands, found):
        from app.models import ToolCallStatusV2, ToolCallV2, ToolResult, \
            ToolResultOutcome
        from app.investigation_service import compose_response
        payload = {
            "analysis": "cross_account_trade_analysis",
            "analysis_scope": "related_account_set",
            "analysis_status": "MATCH_FOUND",
            "case_id": "UA", "target_account": "UA",
            "candidates": cands, "candidates_found": found,
            "candidates_emitted": len(cands),
            "config": {"max_time_delta_seconds": 30.0,
                       "value_tolerance_relative": 0.05},
            "retrieval": {"truncated": False},
        }
        tc = ToolCallV2(
            tool_call_id="TC-FE", tool_name="cross_account_trade_analysis",
            step_type="analyze_related_account_set_trades",
            task_id="TASK-FE", investigation_id="INV-FE",
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS,
                              data=payload))
        return compose_response(
            user_request="这些账户之间有没有对冲",
            skill_id="case_trade_analysis", plan=None,
            tool_calls=[tc], execution_errors=[])

    def test_found_gt_emitted_is_user_visible(self):
        text = self._compose(_cands(20), found=27)
        assert "27 candidates were found in total" in text
        assert "the top 20 are listed" in text
        assert "system candidate limit" in text

    def test_found_eq_emitted_carries_no_cap_note(self):
        text = self._compose(_cands(6), found=6)
        assert "candidates were found in total" not in text


class TestRecordTableMarker:
    def test_record_table_overflow_has_marker(self):
        # the record table cap (10) carries the display marker when the
        # summary flags row truncation — locked regression
        out = RequiredOutput(
            output_id="O1", claim="records", claim_kind="retrieval",
            subject_ref="UA", scope=ScopeLevel.FINDING_LEVEL.value,
            evidence_requirements=["withdrawal_records"])
        summary = {"record_count": 9,
                   "record_rows": [{"record_id": f"W{i:03d}",
                                    "timestamp": "t",
                                    "summary": "s"} for i in range(9)],
                   "_actual_evidence_types": ["withdrawal_records"],
                   "rows_truncated": True}
        lines = _gsp_render_output_facts(out, summary)
        blob = "\n".join(lines)
        assert "前 10 条" in blob or "完整集合" in blob

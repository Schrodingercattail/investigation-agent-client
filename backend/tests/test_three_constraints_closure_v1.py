"""Three-constraint closure — request coverage, case/evidence scope,
answer completeness.

Locks the closed behavior for:

1. Request Coverage Constraint — multi-aspect requests are verified per
   aspect (deterministic identification over the bounded supported
   patterns); single-facet success never misjudges the whole request as
   answered; unmatched requests keep their coverage uncertainty.
2. Case & Evidence Scope Constraint — candidate relations must contain
   the target account to support a target conclusion (independent
   subject-membership check on the turn path too); population shortfall
   and metric-window mismatches are disclosed, never papered over.
3. Answer Completeness Constraint — per-aspect status
   (satisfied / checked-no-match / insufficient / uncovered), partial
   answers keep their answered parts, the completion outcome reuses the
   contract path's GoalOutcome semantics, and the contractless guard no
   longer fires for supported patterns (the deterministic proposal path
   gives them real per-output verification instead).
"""

import json

import pytest

from app.goal_postconditions import (
    PostconditionResult,  # noqa: F401 — import-guard for module wiring
    _destination_facts_verdict,
    _metric_facts_verdict,
    _new_address_facts_verdict,
    derive_goal_outcome,
    evaluate_output,
)
from app.goal_resolution import (
    GoalResolutionStatus,
    GoalRepresentation,
    OutputSatisfaction,
    PostconditionFacet,
    RequiredOutput,
    ScopeLevel,
    build_goal_contract,
    deterministic_contract_proposal,
    understand_goal,
)
from app.request_coverage import (
    ASPECT_DESTINATION,
    ASPECT_HEDGING,
    ASPECT_METRIC,
    ASPECT_NEW_ADDRESS,
    UNCOVERED,
    coverage_lines,
    identify_aspects,
    verify_coverage,
)

CASE4_Q = ("关于反向交易，34.38%反向交易率指的是用户自身反向交易还是与其他账户的"
           "反向交易率？这个账户是否与其他账户存在对冲交易的情况，如果有，给我列出"
           "相关证据明细")
JOURNEY_A_Q = ("7 条提现都提向同一个新地址，还是提向不同的地址？"
               "哪些笔提现属于新地址？")


def _di_block(total=7, unique=7, missing=0):
    # structurally consistent fixture: `unique` groups partition the
    # `total - missing` identified record ids
    ids = [f"W{i:03d}" for i in range(total - missing)]
    base = len(ids) // unique
    groups = []
    for i in range(unique):
        chunk = ids[i * base: (i + 1) * base] if i < unique - 1 \
            else ids[(unique - 1) * base:]
        groups.append({"destination_ref": f"dest-{i}",
                       "record_ids": chunk})
    return {
        "basis": "withdrawal.address", "records_total": total,
        "records_with_identity": total - missing,
        "records_without_identity": missing,
        "unique_destinations": unique,
        "groups": groups,
    }


def _na_block(total=7, true_n=7, false_n=0, missing_n=0):
    records = ([{"record_id": f"W{i:03d}", "is_new_address": True}
                for i in range(true_n)]
               + [{"record_id": f"W{(100 + i):03d}", "is_new_address": False}
                  for i in range(false_n)]
               + [{"record_id": f"W{(200 + i):03d}", "is_new_address": None}
                  for i in range(missing_n)])
    return {"basis": "withdrawal.is_new_address", "records_total": total,
            "true_count": true_n, "false_count": false_n,
            "missing_count": missing_n, "records": records}


def _cross_payload(status="NO_MATCH", candidates=None, truncated=False,
                   metrics=True, target="U00010"):
    return {
        "analysis": "cross_account_trade_analysis",
        "analysis_status": status,
        "case_id": target, "target_account": target,
        "candidates": candidates or [],
        "retrieval": {"truncated": truncated},
        "target_account_metrics": (
            {"opposite_trade_ratio": 0.3438,
             "source": "risk_platform.feature_evidence"} if metrics else None),
    }


# ---------------------------------------------------------------------------
# 1. Request Coverage Constraint
# ---------------------------------------------------------------------------

class TestAspectIdentification:
    def test_case4_request_yields_two_aspects(self):
        ids = [a["aspect_id"] for a in identify_aspects(CASE4_Q)]
        assert ids == [ASPECT_METRIC, ASPECT_HEDGING]

    def test_journey_a_request_yields_two_aspects(self):
        ids = [a["aspect_id"] for a in identify_aspects(JOURNEY_A_Q)]
        assert ids == [ASPECT_DESTINATION, ASPECT_NEW_ADDRESS]

    def test_unmatched_request_yields_no_aspects(self):
        # no invented coverage: an unmatched request keeps its uncertainty
        assert identify_aspects("总结一下这个案子") == []
        assert verify_coverage("总结一下这个案子", []) is None

    def test_comparison_without_withdrawal_subject_is_not_the_aspect(self):
        # a comparison over non-withdrawal objects never claims the
        # withdrawal destination facet
        assert identify_aspects("这些交易是同一类型还是不同类型？") == []


class TestCoverageVerification:
    def test_single_aspect_satisfied_completes_normally(self):
        cov = verify_coverage(
            "这个账户和其他账户之间有对冲交易吗",
            [("cross_account_trade_analysis",
              _cross_payload("NO_MATCH"))])
        assert cov["outcome"] == "complete"
        assert cov["aspects"][0]["satisfaction"] == "satisfied"
        assert coverage_lines(cov) == []      # nothing unresolved: no noise

    def test_multi_aspect_one_uncovered_never_reads_complete(self):
        # the metric question was asked, but no cross-account analysis
        # ran at all — BOTH tool-backed aspects are uncovered, and the
        # run is never misjudged as complete
        cov = verify_coverage(CASE4_Q, [("risk_case_fetch", None)])
        assert cov["outcome"] == "unanswerable"
        by_id = {a["aspect_id"]: a for a in cov["aspects"]}
        assert by_id[ASPECT_METRIC]["satisfaction"] == UNCOVERED
        assert by_id[ASPECT_HEDGING]["satisfaction"] == UNCOVERED
        lines = coverage_lines(cov)
        assert any("尚未覆盖" in ln and "反向交易率" in ln for ln in lines)

    def test_tool_ran_but_metric_absent_is_unresolved_not_satisfied(self):
        # the analysis executed (hedging aspect answered) but the
        # authoritative metric echo is missing — the metric aspect is
        # unresolved, and the hedging answer STANDS
        cov = verify_coverage(
            CASE4_Q, [("cross_account_trade_analysis",
                       _cross_payload("NO_MATCH", metrics=False))])
        by_id = {a["aspect_id"]: a for a in cov["aspects"]}
        assert by_id[ASPECT_METRIC]["satisfaction"] == "unresolved"
        assert by_id[ASPECT_HEDGING]["satisfaction"] == "satisfied"
        assert cov["outcome"] == "partial"
        lines = coverage_lines(cov)
        # only the OPEN aspect is disclosed; the answered one stands
        assert any("证据不足" in ln and "反向交易率" in ln for ln in lines)
        assert all("尚未覆盖" not in ln for ln in lines)

    def test_multi_aspect_one_insufficient_keeps_the_other_answer(self):
        cov = verify_coverage(
            JOURNEY_A_Q,
            [("finding_drilldown",
              {"view": "evidence", "record_count": 7,
               "destination_identity": _di_block(),
               # new-address marker missing for some records
               "new_address_status": _na_block(total=7, true_n=5,
                                               false_n=0, missing_n=2)})])
        by_id = {a["aspect_id"]: a for a in cov["aspects"]}
        assert by_id[ASPECT_DESTINATION]["satisfaction"] == "satisfied"
        assert by_id[ASPECT_NEW_ADDRESS]["satisfaction"] == \
            "partially_supported"
        assert cov["outcome"] == "partial"
        lines = coverage_lines(cov)
        assert any("部分覆盖" in ln or "部分" in ln for ln in lines)
        # the destination answer still stands in the coverage record
        assert by_id[ASPECT_DESTINATION]["detail"]


# ---------------------------------------------------------------------------
# 2. Case & Evidence Scope Constraint
# ---------------------------------------------------------------------------

class TestScopeOnCoveragePath:
    def test_u00010_conclusion_cannot_rest_on_u003xx_matches(self):
        # MATCH_FOUND with candidates that do NOT contain the target:
        # tool success + records + a narrative never become a target answer
        cov = verify_coverage(
            "这个账户和其他账户有对冲交易吗",
            [("cross_account_trade_analysis", _cross_payload(
                "MATCH_FOUND",
                candidates=[{"account_a": "U00307",
                             "account_b": "U00349"}]))])
        a = cov["aspects"][0]
        assert a["satisfaction"] == "unresolved"
        assert a["reason_code"] == "subject_membership_violation"
        assert "不能作为该账户的对冲证据" in a["detail"]
        assert cov["outcome"] == "unanswerable"
        assert any("请求覆盖情况" in ln for ln in coverage_lines(cov))

    def test_subject_present_candidates_satisfy_with_scope_wording(self):
        cov = verify_coverage(
            "这个账户和其他账户有对冲交易吗",
            [("cross_account_trade_analysis", _cross_payload(
                "MATCH_FOUND",
                candidates=[{"account_a": "U00010",
                             "account_b": "U00307",
                             "transaction_ids_a": ["T-TGT"],
                             "transaction_ids_b": ["T-LEG"]}]))])
        a = cov["aspects"][0]
        assert a["satisfaction"] == "satisfied"
        assert "每组均包含该账户的一笔交易" in a["detail"]

    def test_no_match_refuses_global_substitution_and_overclaim(self):
        cov = verify_coverage(
            "这个账户和其他账户有对冲交易吗",
            [("cross_account_trade_analysis", _cross_payload("NO_MATCH"))])
        a = cov["aspects"][0]
        assert a["satisfaction"] == "satisfied"
        assert a["reason_code"] == "subject_absent_complete_scope"
        # scoped absence — never "objectively no relationship exists"
        assert "完整检索" in a["detail"]
        assert "不作为该账户的证据" in a["detail"]

    def test_truncated_retrieval_never_claims_complete(self):
        cov = verify_coverage(
            "这个账户和其他账户有对冲交易吗",
            [("cross_account_trade_analysis",
              _cross_payload("INSUFFICIENT_DATA", truncated=True))])
        a = cov["aspects"][0]
        assert a["satisfaction"] == "unresolved"
        assert "这不是“未发现”" in a["detail"]

    def test_asserted_population_shortfall_blocks_full_claim(self):
        # request asserts 9 withdrawals; U00010's authoritative total is 7
        cov = verify_coverage(
            "9 笔提现都提到同一个地址吗",
            [("finding_drilldown",
              {"view": "evidence", "record_count": 7,
               "destination_identity": _di_block(total=7, unique=1)})])
        a = {x["aspect_id"]: x for x in cov["aspects"]}[ASPECT_DESTINATION]
        assert a["satisfaction"] == "unresolved"
        assert a["reason_code"] == "population_shortfall"
        assert "不构成对全部 9 笔请求的完整回答" in a["detail"]

    def test_no_aspect_borrows_data_from_another_case(self):
        # the verifier reads only THIS run's payloads; a payload naming a
        # different case account is a case-scope violation — foreign
        # evidence never becomes this investigation's answer
        cov = verify_coverage(
            "U00010 和其他账户有对冲交易吗",
            [("cross_account_trade_analysis",
              _cross_payload("MATCH_FOUND", target="U00020",
                             candidates=[{"account_a": "U00020",
                                          "account_b": "U00021"}]))],
            expected_subject="U00010")
        a = cov["aspects"][0]
        assert a["satisfaction"] == "unresolved"
        assert a["reason_code"] == "case_scope_violation"
        assert "异案证据不能作为本目标的结论依据" in a["detail"]


class TestScopeDisclosureInTurnComposer:
    def _compose(self, data, request):
        from app.models import ToolCallStatusV2, ToolCallV2, ToolResult, \
            ToolResultOutcome
        from app.investigation_service import compose_response
        tc = ToolCallV2(
            tool_call_id="TC-3C", tool_name="finding_drilldown",
            step_type="inspect_withdrawals",
            task_id="TASK-3C", investigation_id="INV-3C",
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data))
        return compose_response(
            user_request=request, skill_id="trade_investigation",
            plan=None, tool_calls=[tc], execution_errors=[])

    def test_destination_and_new_address_conclusions_rendered(self):
        data = {"view": "evidence", "record_count": 7, "complete": True,
                "streams": {"withdrawals_included": True},
                "destination_identity": _di_block(),
                "new_address_status": _na_block()}
        text = self._compose(data, JOURNEY_A_Q)
        # dimension 1: same-vs-different — answered explicitly
        assert "7 DIFFERENT destinations" in text
        # dimension 2: which records are new addresses — answered per record
        assert "ALL 7 withdrawals are marked" in text
        assert "W000" in text

    def test_metric_window_vs_record_list_disclosed(self):
        data = {"view": "evidence", "record_count": 64, "complete": True,
                "streams": {"transactions_included": True},
                "population_reconciliation": {
                    "reference_time": "2026-07-21T15:30:00+00:00",
                    "metrics": [{
                        "type": "window_count", "metric":
                            "trade_frequency_24h", "claimed": 54,
                        "observed_in_window": 54, "exposed_total": 64,
                        "outside_window": 10,
                        "verdict": "window_scoped"}]}}
        text = self._compose(data, "列出这个账号的全部交易记录")
        assert "Metric scope:" in text
        assert "54" in text and "64" in text and "10 outside" in text
        assert "neither number substitutes" in text

    def test_shortfall_line_names_the_gap(self):
        data = {"view": "evidence", "record_count": 7, "complete": True,
                "streams": {"withdrawals_included": True},
                "destination_identity": _di_block(total=7, unique=1)}
        text = self._compose(data, "9 笔提现是否都提到同一个地址")
        assert "holds only 7" in text
        assert "NOT an answer over the full requested population" in text


# ---------------------------------------------------------------------------
# 3. Answer Completeness Constraint
# ---------------------------------------------------------------------------

class TestDeterministicProposalContractPath:
    def test_case4_class_gets_contract_even_on_llm_empty(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        rep = understand_goal(CASE4_Q, resolved_finding_id="F7",
                              case_id="U00010", llm=_BoomLLM())
        # the goal-understanding LLM failed — the deterministic proposer
        # still yields the multi-output proposal for the supported pattern
        assert rep.source == "heuristic"
        assert rep.contract_proposal is not None
        contract, reason = build_goal_contract(rep, "U00010")
        assert contract is not None, reason
        claims = [o.claim for o in contract.outputs]
        assert len(claims) == 2                       # both aspects tracked
        facets = [o.postcondition_facets for o in contract.outputs]
        assert any(PostconditionFacet.REVERSE_TRADE_METRIC_SEMANTICS.value
                   in f for f in facets)

    def test_journey_a_class_gets_both_facets(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        rep = understand_goal(JOURNEY_A_Q, resolved_finding_id="F3",
                              case_id="U00010", llm=_BoomLLM())
        contract, reason = build_goal_contract(rep, "U00010")
        assert contract is not None, reason
        facets = {f for o in contract.outputs
                  for f in (o.postcondition_facets or [])}
        assert PostconditionFacet.DESTINATION_ADDRESS_IDENTITY.value \
            in facets
        assert PostconditionFacet.WITHDRAWAL_NEW_ADDRESS_STATUS.value \
            in facets
        # the withdrawal record population the request asserts is carried
        assert contract.asserted_record_population == 7

    def test_unmatched_request_keeps_uncertainty_and_guard(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        rep = understand_goal("总结一下这个案子", resolved_finding_id=None,
                              case_id="U00010", llm=_BoomLLM())
        assert rep.contract_proposal is None
        contract, reason = build_goal_contract(rep, "U00010")
        assert contract is None and reason == "no_proposal"


class _BoomLLM:
    """A goal-understanding provider that always fails (llm_empty class)."""

    def generate(self, messages, max_tokens=0, temperature=0.1):
        raise RuntimeError("LLMEmptyResponseError: no text block")


class TestMetricFacetPostcondition:
    def _output(self, **kw):
        return RequiredOutput(
            output_id="O1", claim="反向交易率口径",
            claim_kind="explanation", subject_ref="U00010",
            scope=ScopeLevel.ACCOUNT_LEVEL.value,
            evidence_requirements=["opposite_trade_relations"],
            postcondition_facets=["reverse_trade_metric_semantics"], **kw)

    def test_metric_echo_satisfies_with_semantics_detail(self):
        out = self._output()
        obs = [{"for_outputs": ["O1"], "outcome": "success",
                "actual_evidence_types": ["opposite_trade_relations"],
                "summary": {"target_account_metrics": {
                    "opposite_trade_ratio": 0.3438,
                    "source": "risk_platform.feature_evidence"}}}]
        res = evaluate_output(out, "U00010", obs)
        assert res.satisfaction == OutputSatisfaction.SATISFIED
        assert res.reason_code == "reverse_trade_metric_semantics_complete"
        assert "不是跨账户匹配率" in res.detail
        assert "34.38%" in res.detail

    def test_retrieval_success_never_satisfies_the_metric_facet(self):
        out = self._output()
        obs = [{"for_outputs": ["O1"], "outcome": "success",
                "actual_evidence_types": ["opposite_trade_relations"],
                "summary": {"candidates": [], "candidates_found": 0,
                            "accounts_analyzed": ["U00010"]}}]
        res = evaluate_output(out, "U00010", obs)
        assert res.satisfaction == OutputSatisfaction.UNRESOLVED
        assert res.reason_code == "comparison_facts_absent"

    def test_pure_fact_verdicts_reused_by_both_surfaces(self):
        # one semantics: the turn-path verifier and the contract-path
        # facet evaluator read the same fact functions
        assert _metric_facts_verdict(None) is None
        assert _metric_facts_verdict({"opposite_trade_ratio": "x"}) is None
        s, pol, reason, comp, _ = _metric_facts_verdict(
            {"opposite_trade_ratio": 0.3438})
        assert s == OutputSatisfaction.SATISFIED and pol == "positive"
        assert _new_address_facts_verdict({"records_total": 0}) is None
        s2, _, reason2, _, _ = _destination_facts_verdict(
            _di_block(total=7, unique=7), asserted_population=9)
        assert s2 == OutputSatisfaction.UNRESOLVED
        assert reason2 == "population_shortfall"


class TestCompletionSemantics:
    def test_goal_outcome_reused_across_surfaces(self):
        # the turn-path coverage outcome and the contract-path overall
        # outcome share one derivation
        assert derive_goal_outcome(["satisfied"]).value == "complete"
        assert derive_goal_outcome(
            ["satisfied", "unresolved"]).value == "partial"
        assert derive_goal_outcome(["pending", "uncovered" if False
                                    else "unresolved"]).value == \
            "unanswerable"

    def test_contract_path_renders_per_output_not_generic_guard(self):
        from app.agent_loop import AgentLoopState, compose_loop_response
        from app.config import settings
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
            proposal = deterministic_contract_proposal(
                CASE4_Q, "F7", "U00010")
            rep = GoalRepresentation(
                case_id="U00010", goal_type="cross_account_check",
                goal_resolution=GoalResolutionStatus.INFERRED.value,
                subject_type="finding", subject_ref="F7",
                source="heuristic", request_text=CASE4_Q[:400],
                contract_proposal=proposal)
            contract, reason = build_goal_contract(rep, "U00010")
            assert contract is not None, reason
            # O1 (metric) SATISFIED; O2 (hedging, subject U00010) —
            # complete NO_MATCH → SATISFIED negative
            contract.outputs[0].satisfaction = "satisfied"
            contract.outputs[0].detail = "34.38% 为账户自身口径。"
            contract.outputs[1].satisfaction = "satisfied"
            contract.outputs[1].polarity = "negative"
            contract.outputs[1].detail = "未发现涉及 U00010 的候选。"
            st = AgentLoopState(
                task_id="T", investigation_id="CASE:U00010",
                case_id="U00010", goal=CASE4_Q,
                criteria_kind="question_answered",
                goal_contract=contract,
                goal_contract_decision={"outcome": "llm_empty",
                                        "reason": "boom"})
            st.observations = []
            st.agent_status = "completed"
            text = compose_loop_response(st)
            # per-output sections render — the generic guard does NOT
            assert "关于「" in text
            assert "未能建立完整的目标分解" not in text
            assert "整体结果:" in text
            assert "完整回答" in text or "部分回答" in text
        finally:
            monkey.undo()

    def test_contractless_unmatched_request_keeps_guard(self):
        from app.agent_loop import AgentLoopState, compose_loop_response
        from app.config import settings
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
            st = AgentLoopState(
                task_id="T", investigation_id="CASE:U00010",
                case_id="U00010", goal="总结一下这个案子",
                criteria_kind="question_answered", goal_contract=None)
            st.agent_status = "completed"
            text = compose_loop_response(st)
            # genuinely-unmatched request: the conservative guard STAYS
            assert "未能建立完整的目标分解" in text
        finally:
            monkey.undo()

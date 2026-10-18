"""U00033 — multi-output answer dedup + plain-export note scoping.

Composer-level regression tests over `_compose_gsp_response` (pure
rendering: no state mutation, no retrieval, no business rules).

Grounded in the REAL persisted U00033 answers:
- TASK-37c8754ba99a: the withdrawal question answered the address
  comparison (verdict + destination grouping + the FULL 9-record facet
  table) and then repeated the SAME 9 records in the detail output's
  plain list.
- TASK-137d3ca9a8e3 ("生成此账户的提现明细和交易明细文件"): the pure
  transaction-detail export auto-attached the ratio-recompute line and
  the "trade_frequency_7d 暂无可复核的明细口径映射" verification note.
- U00047-shaped hedge answers: the existence output and the detail
  delivery output fully repeated the matching-criteria line, the budget
  boundary note and the verdict summary in adjacent sections.

Contract: OutputSatisfaction / GoalOutcome / evidence references and
Artifact content are untouched — the composed ANSWER is deduplicated.
"""

import pytest

from app.agent_loop import AgentLoopState, _compose_gsp_response
from app.goal_resolution import GoalContract
from app.goal_resolution import RequiredOutput


CASE = "U00033"


def _output(oid, claim, kind, reqs, sat="satisfied", facets=None,
            detail=None, polarity="positive"):
    return RequiredOutput(
        output_id=oid, claim=claim, claim_kind=kind, subject_ref=CASE,
        scope="case_level", evidence_requirements=reqs,
        postcondition_facets=facets or [], satisfaction=sat,
        polarity=polarity, detail=detail)


def _withdrawal_state():
    """The real TASK-37c8754ba99a shape: rule explain + destination
    comparison (facet) + withdrawal detail list sharing ONE evidence
    observation."""
    from app.goal_resolution import ClaimKind, OutputSatisfaction
    contract = GoalContract(
        contract_id="GC-U33A", subject_ref=CASE, intent_source="llm",
        outputs=[
            _output("O1", "为什么高频提现被标记为风险", "explanation",
                    ["rule_explanation"]),
            _output("O2", "提现目的地地址是否相同（逐笔记录目的地标识比较）",
                    "existence_check", ["withdrawal_records"],
                    facets=["destination_address_identity"],
                    detail="9 笔提现共指向 9 个不同目的地"
                           "（记录中的目的地标识）。"),
            _output("O3", "提现明细清单", "retrieval",
                    ["withdrawal_records"]),
        ])
    rows = [
        {"record_id": f"W0002{i}", "timestamp": f"2026-07-21T0{i}:00:00Z",
         "summary": f"Withdrawal of {i}.6{i} BTC to a newly encountered "
                    f"address {i}"}
        for i in range(1, 10)]
    groups = [
        {"destination_ref": f"dest-{i}",
         "address": f"addr{i}",
         "record_ids": [f"W0002{i}"]}
        for i in range(1, 10)]
    evidence_obs = {
        "tool_name": "finding_drilldown", "step_type": "inspect_evidence",
        "outcome": "success", "semantic_status": "records",
        "step_id": "S1", "iteration": 1,
        "for_outputs": ["O2", "O3"],
        "summary": {
            "record_count": 9, "record_rows": rows,
            "rows_truncated": False,
            "destination_identity": {
                "records_total": 9, "unique_destinations": 9,
                "groups": groups, "basis": "recorded_identity",
            },
        },
        "evidence_refs": [{"kind": "withdrawal", "id": r["record_id"]}
                          for r in rows],
    }
    rule_obs = {
        "tool_name": "signal_explain", "step_type": "explain_signal",
        "outcome": "success", "semantic_status": "Rule",
        "step_id": "S2", "iteration": 2, "for_outputs": ["O1"],
        "summary": {
            "signal_type": "Rule",
            "rule_evidence": [{
                "rule_name": "High withdrawal frequency",
                "severity": "high",
                "threshold": "withdrawal_frequency_24h > 5",
                "trigger": {"withdrawal_frequency_24h": 9},
            }],
        },
        "evidence_refs": [],
    }
    return AgentLoopState(
        task_id="TASK-U33A", investigation_id="INV-U33A", case_id=CASE,
        goal="高频提现的触发规则是什么？这些提现是提向相同地址还是不同地址？"
             "把提现明细发我",
        criteria_kind="question_answered",
        goal_contract=contract, observations=[evidence_obs, rule_obs])


def _hedge_state():
    """The U00047-shaped hedge request: existence + detail delivery share
    ONE trade analysis observation (4 candidates, truncated retrieval);
    the account list is its own output."""
    contract = GoalContract(
        contract_id="GC-U33B", subject_ref=CASE, intent_source="llm",
        outputs=[
            _output("O1", "关联账号网络归因明细", "retrieval",
                    ["network_relations"]),
            _output("O2", "关联账户集合内部是否存在对冲交易候选",
                    "existence_check", ["opposite_trade_relations"],
                    sat="partially_supported"),
            _output("O3", "生成一份包含对冲交易明细的文件", "retrieval",
                    ["transaction_records", "opposite_trade_relations"]),
        ])
    candidates = [
        {"candidate_id": f"CATA-00{i}",
         "account_a": f"A{i}", "account_b": f"B{i}", "symbol": "BTC",
         "max_time_gap_seconds": 0.001,
         "value_a": 1.0, "value_b": 0.9,
         "value_difference_relative": 0.10,
         "value_difference": 0.1,
         "transaction_ids_a": [f"T00{i}"], "transaction_ids_b": [f"T10{i}"],
         "supporting": [
             {"transaction_id": f"T00{i}"}, {"transaction_id": f"T10{i}"}]}
        for i in range(1, 5)]
    trade_obs = {
        "tool_name": "cross_account_trade_analysis",
        "step_type": "analyze_related_account_set_trades",
        "outcome": "success", "semantic_status": "MATCH_FOUND",
        "step_id": "S1", "iteration": 1, "for_outputs": ["O2", "O3"],
        "summary": {
            "analysis": "cross_account_trade_analysis",
            "analysis_status": "MATCH_FOUND",
            "analysis_scope": "related_account_set",
            "target_account": CASE,
            "accounts_analyzed": [CASE, "A1", "B1"],
            "candidates": candidates,
            "candidates_found": 4, "candidates_emitted": 4,
            "totals": {"transactions_retrieved": 500},
            "retrieval": {"status": "truncated", "truncated": True,
                          "truncated_reasons": ["account_set:retrieval_"
                                                "budget_exhausted"],
                          "account_set": {"accounts_queried": 3}},
            "max_time_delta_seconds": 30,
            "value_tolerance_relative": 0.05,
            "value_tolerance_absolute_floor": 1.0,
        },
        "evidence_refs": [],
    }
    network_obs = {
        "tool_name": "related_account_analysis",
        "step_type": "analyze_related_accounts",
        "outcome": "success", "semantic_status": "RELATED_ACCOUNTS_FOUND",
        "step_id": "S2", "iteration": 2, "for_outputs": ["O1"],
        "summary": {"related_accounts_count": 2,
                    "related_accounts": ["R1", "R2"],
                    "shared_devices": [], "shared_ip_attributions": {}},
        "evidence_refs": [],
    }
    return AgentLoopState(
        task_id="TASK-U33B", investigation_id="INV-U33B", case_id=CASE,
        goal="列出所有关联账号，查询关联账号之间是否存在对冲交易，"
             "如果有，把交易明细发我，并生成一份文件",
        criteria_kind="question_answered",
        goal_contract=contract, observations=[trade_obs, network_obs])


def _trade_export_state():
    """The real TASK-137d3ca9a8e3 shape: a PURE transaction-detail export
    whose evidence carries RP feature-verification content the user never
    asked for (ratio recompute + unmapped trade_frequency_7d)."""
    contract = GoalContract(
        contract_id="GC-U33C", subject_ref=CASE, intent_source="llm",
        outputs=[
            _output("O1", "withdrawal_details_file", "retrieval",
                    ["withdrawal_records"]),
            _output("O2", "transaction_details_file", "retrieval",
                    ["transaction_records"]),
        ])
    rows = [
        {"record_id": "T001", "timestamp": "2026-07-20T23:17:49Z",
         "summary": "BUY trade of 0.7487 BTC"},
        {"record_id": "T002", "timestamp": "2026-07-20T23:22:49Z",
         "summary": "SELL trade of 1.8198 AVAX"},
    ]
    trade_obs = {
        "tool_name": "finding_drilldown", "step_type": "inspect_transactions",
        "outcome": "success", "semantic_status": "records",
        "step_id": "S1", "iteration": 1, "for_outputs": ["O2"],
        "summary": {
            "record_count": 2, "record_rows": rows,
            "rows_truncated": False,
            "population_reconciliation": {
                "reference_time": "2026-07-21T00:00:00Z",
                "metrics": [{
                    "type": "ratio_recompute", "verdict": "claim_supported",
                    "claimed": "0.3333", "observed": "0.3333",
                    "denominator": 2}],
                "unmapped_metrics": ["trade_frequency_7d"],
            },
        },
        "evidence_refs": [],
    }
    wd_obs = {
        "tool_name": "finding_drilldown", "step_type": "inspect_withdrawals",
        "outcome": "success", "semantic_status": "records",
        "step_id": "S2", "iteration": 2, "for_outputs": ["O1"],
        "summary": {"record_count": 2,
                    "record_rows": [rows[0]],
                    "rows_truncated": False},
        "evidence_refs": [],
    }
    return AgentLoopState(
        task_id="TASK-U33C", investigation_id="INV-U33C", case_id=CASE,
        goal="生成此账户的提现明细和交易明细文件",
        criteria_kind="question_answered",
        goal_contract=contract, observations=[trade_obs, wd_obs])


class TestWithdrawalAnswerDedup:
    def test_full_withdrawal_details_render_exactly_once(self):
        resp = _compose_gsp_response(_withdrawal_state())
        # each full detail line appears exactly once (the detail owner's
        # list) — the destination-facet TABLE no longer repeats them
        assert resp.count("Withdrawal of 1.61 BTC") == 1
        assert resp.count("| 记录 ID") == 0
        # the destination grouping (comparison conclusion) is kept
        assert "9 个不同目的地" in resp
        assert "dest-1" in resp
        # the rule + threshold explanation is kept
        assert "withdrawal_frequency_24h > 5" in resp
        # each output keeps its own section header
        assert resp.count("关于「") == 3


class TestHedgeAnswerDedup:
    def test_candidates_criteria_and_boundary_appear_once(self):
        resp = _compose_gsp_response(_hedge_state())
        # full candidate list exactly once (the detail owner)
        assert resp.count("CATA-001") == 1
        # matching criteria exactly once (beside the list)
        assert resp.count("匹配判据:") == 1
        # budget boundary exactly once (the existence output's section)
        assert resp.count("受预算限制") == 1
        # verdict summary (counts + case-account relationship) once
        assert resp.count("候选对冲交易关系") == 1
        # the account list is kept as its own section
        assert "关联账号共 2 个" in resp
        # the non-owner section still references the detail section
        assert "完整候选明细见" in resp

    def test_partials_and_verdicts_unchanged(self):
        state = _hedge_state()
        resp = _compose_gsp_response(state)
        # satisfaction states are untouched by rendering
        outs = {o.output_id: o for o in state.goal_contract.outputs}
        assert outs["O2"].satisfaction.value == "partially_supported"
        assert outs["O3"].satisfaction.value == "satisfied"
        assert "已获取部分证据，但覆盖范围不完整" in resp


class TestPlainExportNoteScoping:
    def test_pure_export_has_no_feature_verification_notes(self):
        resp = _compose_gsp_response(_trade_export_state())
        assert "trade_frequency_7d" not in resp
        assert "暂无可复核" not in resp
        assert "口径核实" not in resp
        # the requested records + source note are intact
        assert "完整交易记录共 2 条:" in resp
        assert "BUY trade of 0.7487 BTC" in resp

    def test_rule_verification_path_keeps_reconciliation(self):
        """A metric/rule-verification output (rule explanation) keeps the
        population reconciliation — the verification capability is not
        removed, only the auto-attach to pure exports."""
        state = _trade_export_state()
        state.goal_contract.outputs.append(
            _output("O3", "核实提现频率窗口口径", "explanation",
                    ["rule_explanation"]))
        state.observations.append({
            "tool_name": "signal_explain", "step_type": "explain_signal",
            "outcome": "success", "semantic_status": "Rule",
            "step_id": "S3", "iteration": 3, "for_outputs": ["O3"],
            "summary": {
                "signal_type": "Rule",
                "rule_evidence": [{
                    "rule_name": "High withdrawal frequency",
                    "severity": "high",
                    "threshold": "withdrawal_frequency_24h > 5"}],
                "population_reconciliation": {
                    "reference_time": "2026-07-21T00:00:00Z",
                    "metrics": [{
                        "type": "window_count", "verdict": "consistent",
                        "metric": "withdrawal_frequency_24h",
                        "claimed": 2, "observed_in_window": 2,
                        "exposed_total": 2, "outside_window": 0}],
                    "unmapped_metrics": []},
            },
            "evidence_refs": [],
        })
        resp = _compose_gsp_response(state)
        assert "总体口径说明" in resp

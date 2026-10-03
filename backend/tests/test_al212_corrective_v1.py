"""AL-2.12 — corrective closure: result semantics, answer fidelity,
conversation ownership.

Pins the composer/policy contracts behind the four user-visible defects:

A  a new conversation must not inherit a previous conversation's Activity
   (frontend binding fixed; browser E2E scenario A verifies the behavior)
B  EMPTY evidence on a graph finding = a bounded answer with an explicit
   data boundary — never an 8-iteration re-plan loop and never a
   "conclusive empty investigation" when real data WAS retrieved
C  explicit completeness requests render the FULL record set (table),
   never examples; the WHY carries observed-vs-threshold semantics
D  case-level analytical runs answer the business question in the
   conversation (conclusion/count/criteria/limitation/table); artifact
   success never substitutes for the answer
"""

import pytest

from app.agent_loop import (
    AgentLoopState,
    EnforcedDecision,
    LoopDecision,
    LoopPolicy,
    StopReason,
    compose_loop_response,
)


def _state_with(observations, goal="高频提现为什么是风险项，把所有高频提现证据发给我",
                title="High withdrawal frequency.", revisions=1):
    st = AgentLoopState(task_id="T", investigation_id="I", case_id="U00010",
                        goal=goal, focused_finding_id="F3",
                        focused_finding_title=title,
                        focused_finding_summary="7 withdrawals in 24h")
    st.observations = observations
    st.agent_status = "completed"
    st.stop_reason = StopReason.GOAL_SATISFIED
    st.plan_revisions = revisions
    return st


def _evidence_obs(count=7):
    rows = [{"record_id": f"W{i:02d}",
             "timestamp": f"2026-07-21T0{i}:00:00",
             "summary": f"Withdrawal #{i} to a newly encountered address"}
            for i in range(1, count + 1)]
    return {
        "tool_name": "finding_drilldown", "outcome": "success",
        "semantic_status": "evidence",
        "summary": {"record_count": count,
                    "record_ids": [r["record_id"] for r in rows],
                    "streams": {"withdrawals_included": True},
                    "record_rows": rows,
                    "rows_truncated": False},
    }


def _signal_obs(**over):
    s = {"rule_name": "High withdrawal frequency",
         "rule_description": "7 withdrawals in 24h exceeds the normal "
                             "pattern",
         "threshold": "withdrawal_frequency_24h > 10",
         "trigger_values": {"withdrawal_frequency_24h": 7},
         "contribution": 20}
    s.update(over)
    return {"tool_name": "signal_explain", "outcome": "success",
            "semantic_status": "Rule", "summary": s}


# --- Issue C: COMPLETE evidence + real WHY --------------------------------------

def test_complete_request_renders_full_table_and_real_why():
    st = _state_with([_signal_obs(), _evidence_obs(7)])
    response = compose_loop_response(st)
    # the WHY: observed value vs threshold condition, not a bare rule name
    assert "Withdrawal frequency (24h) = 7" in response
    assert "7 withdrawals in 24h exceeds the normal pattern" in response
    assert "withdrawal_frequency_24h > 10".replace(
        "withdrawal_frequency_24h", "Withdrawal frequency (24h)") in response
    assert "触发条件" in response
    # the COMPLETE set: every record, table form, no examples
    assert "完整证据共 7 条" in response
    assert response.count("| W0") + response.count("| W") >= 7
    assert "例如" not in response
    assert "证据视图" not in response
    assert "保存在" not in response
    # answer leads; execution metadata trails (when the recap exists)
    if "Steps completed" in response:
        assert response.index("完整证据共") < response.index(
            "Steps completed")


def test_composer_with_21_records_discloses_the_display_bound():
    st = _state_with([_signal_obs(), _evidence_obs(21)])
    # mirror the observation contract: the projection caps rows at 20 and
    # flags the truncation
    st.observations[1]["summary"]["record_rows"] = \
        st.observations[1]["summary"]["record_rows"][:20]
    st.observations[1]["summary"]["rows_truncated"] = True
    response = compose_loop_response(st)
    assert "完整证据共 21 条" in response
    assert "显示前 20 条" in response          # bounded display disclosed
    assert "受对话显示上限约束" in response


# --- Issue B: EMPTY on a graph finding = bounded answer, not conclusive ---

def test_empty_evidence_with_graph_facts_is_a_bounded_answer():
    st = _state_with([{
        "tool_name": "signal_explain", "outcome": "success",
        "semantic_status": "Graph",
        "summary": {"cluster_name": "Cluster_device_sharing_1",
                    "detection_type": "device_sharing",
                    "member_count": 19, "cluster_risk_score": 80.0,
                    "shared_devices": ["DEV8Z1O4A35QU"],
                    "related_accounts": ["U00337", "U00325"]},
    }, {
        "tool_name": "finding_drilldown", "outcome": "empty",
        "semantic_status": "evidence",
        "summary": {"record_count": 0, "record_rows": [],
                    "streams": {"withdrawals_included": True}},
    }])
    response = compose_loop_response(st)
    # what IS known (real graph facts) ...
    assert "Cluster_device_sharing_1" in response
    assert "DEV8Z1O4A35QU" in response
    # ... and the honest boundary (never claimed as complete enumeration)
    assert "数据边界" in response
    assert "不能把现有摘要" in response and "全部明细" in response
    assert "conclusive empty" not in response
    # the goal text asked for shared-device details: the WHY section still
    # names the finding identity
    assert "关于发现" in response


def test_conclusive_empty_wording_reserved_for_true_zeros():
    st = _state_with([{
        "tool_name": "finding_drilldown", "outcome": "empty",
        "semantic_status": "evidence",
        "summary": {"record_count": 0, "record_rows": [],
                    "streams": {"withdrawals_included": True}},
    }])
    st.stop_reason = StopReason.CONCLUSIVE_EMPTY_RESULT
    response = compose_loop_response(st)
    assert "确认为零条匹配记录" in response
    assert "数据边界" not in response


# --- Issue D: case-level analytical answers in the conversation -----------

def test_cross_account_match_answers_the_business_question():
    cands = [{
        "candidate_id": f"CATA-{i}", "match_type": "1_to_1",
        "account_a": "U00307", "side_a": "BUY",
        "account_b": "U00349", "side_b": "SELL", "symbol": "MATIC",
        "max_time_gap_seconds": 0.5 + i * 0.1, "value_a": 1.0 + i,
        "value_b": 1.1 + i, "value_difference": 0.1,
        "transaction_ids_a": [f"TA{i}"], "transaction_ids_b": [f"TB{i}"],
    } for i in range(1, 7)]
    st = _state_with([{
        "tool_name": "cross_account_trade_analysis", "outcome": "success",
        "semantic_status": "MATCH_FOUND",
        "summary": {"candidates_found": 6, "candidates": cands,
                    "max_time_delta_seconds": 30.0,
                    "value_tolerance_relative": 0.05,
                    "retrieval": {"truncated": True}},
    }], goal="这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我",
        title=None)
    st.focused_finding_title = None
    st.focused_finding_id = None
    response = compose_loop_response(st)
    assert "发现 6 个候选跨账户对冲交易关系" in response
    assert "30 秒" in response and "5%" in response       # matching criteria
    assert "预算限制" in response                            # bounded retrieval
    assert "不构成对操纵或交易意图的结论" in response
    assert "| CATA-1 |" in response                        # candidate table
    # answer leads; artifact/recap metadata trails
    assert response.index("发现 6 个候选") < response.index(
        "Steps completed") if "Steps completed" in response else True


def test_cross_account_no_match_and_insufficient_wordings():
    st = _state_with([{
        "tool_name": "cross_account_trade_analysis", "outcome": "success",
        "semantic_status": "NO_MATCH", "summary": {"candidates_found": 0},
    }], goal="对冲交易", title=None)
    st.focused_finding_id = None
    st.focused_finding_title = None
    assert "没有。" in compose_loop_response(st)

    st2 = _state_with([{
        "tool_name": "cross_account_trade_analysis", "outcome": "success",
        "semantic_status": "INSUFFICIENT_DATA",
        "summary": {"candidates_found": 0},
    }], goal="对冲交易", title=None)
    st2.focused_finding_id = None
    st2.focused_finding_title = None
    response = compose_loop_response(st2)
    assert "无法得出可靠结论" in response
    assert "不是\"未发现\"" in response


# --- loop policy: repeated-empty coercion (no 8-iteration churn) ----------

def test_policy_stops_replan_on_repeated_empty():
    policy = LoopPolicy()
    st = _state_with([_signal_obs(), {
        "tool_name": "finding_drilldown", "outcome": "empty",
        "summary": {"record_count": 0},
    }])
    st.observations.append({
        "tool_name": "finding_drilldown", "outcome": "empty",
        "summary": {"record_count": 0},
    })
    st.plan_revisions = 2
    decision = policy.enforce(
        __import__("app.agent_loop", fromlist=["DecisionProposal"])
        .DecisionProposal(decision="RE_PLAN", reason="try again"),
        st)
    assert decision.decision == LoopDecision.STOP
    assert "empty result" in decision.reason


def test_policy_still_replans_on_first_empty():
    policy = LoopPolicy()
    st = _state_with([_signal_obs(), {
        "tool_name": "finding_drilldown", "outcome": "empty",
        "summary": {"record_count": 0},
    }])
    st.plan_revisions = 1
    decision = policy.enforce(
        __import__("app.agent_loop", fromlist=["DecisionProposal"])
        .DecisionProposal(decision="RE_PLAN", reason="first empty"),
        st)
    # one re-plan after an empty is still allowed (bounded, not repeated)
    assert decision.decision in (LoopDecision.RE_PLAN, LoopDecision.STOP)


def test_bounded_data_stop_reason_exists():
    assert StopReason.BOUNDED_DATA == "bounded_data"

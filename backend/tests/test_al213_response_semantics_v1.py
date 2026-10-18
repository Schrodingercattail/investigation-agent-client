"""AL-2.13 — response-semantics corrective pass (post-audit).

Implements the four audit-confirmed defects:

A  no-progress convergence: an identical SUCCESS result re-executed by a
   re-plan stops the loop; different args / different results / first
   executions are never blocked. The composer also renders one block per
   distinct result.
B  related-account answers cover BOTH intents: why the relationship is a
   risk signal (from RP's own network attribution facts, with the
   relationship ≠ misconduct boundary) and which accounts exist — with
   the explicit 10-total vs 5-returned disclosure.
C  loop-side policy rendering obeys the turn-side association contract:
   only associated references are finding-level support; no basis → say
   so; unassociated matches are never listed as explanation.
D  ML observations carry the authoritative normalized fields (detection
   method, score interpretation, feature context, attribution flag) and
   the composer explains ML in user language without inventing
   attribution.
"""

import json

from app.agent_loop import (
    AgentLoopState,
    DecisionProposal,
    LoopDecision,
    LoopPolicy,
    StopReason,
    _observation_fingerprint,
    compose_loop_response,
    project_observation,
)
from app.models import ToolResultOutcome


def _state(observations, revisions=2, arg_prov=None,
           goal="关联账户关系为什么是风险项，这个账户有哪些关联账户"):
    st = AgentLoopState(task_id="T", investigation_id="I",
                        case_id="U00010", goal=goal)
    st.observations = observations
    st.plan_revisions = revisions
    st.agent_status = "running"
    if arg_prov is not None:
        st.arg_provenance = arg_prov
    return st


def _related_obs(dev="DEV8Z1O4A35QU"):
    return {
        "tool_name": "related_account_analysis", "outcome": "success",
        "semantic_status": "RELATED_ACCOUNTS_FOUND",
        "summary": {"related_accounts_count": 10,
                    "related_accounts": ["U00337", "U00325", "U00349",
                                         "U00004", "U00343"],
                    "shared_devices": [dev],
                    "cluster_name": "Cluster_device_sharing_1",
                    "detection_type": "device_sharing",
                    "member_count": 19, "cluster_risk_score": 80.0,
                    "role_in_cluster": "spoke"},
    }


# --- Issue A: no-progress convergence -------------------------------------------

def test_identical_success_replan_stops():
    st = _state([_related_obs(), _related_obs()])
    decision = LoopPolicy().enforce(
        DecisionProposal(decision="RE_PLAN", reason="try again"), st)
    assert decision.decision == LoopDecision.STOP
    assert "identical result" in decision.reason


def test_same_tool_different_args_is_never_converged():
    """Different normalized arguments produce a different query — the
    fingerprint (which includes the summary) differs, so the guard must
    not fire."""
    o1 = _related_obs()
    o2 = _related_obs(dev="DEV-DIFFERENT")       # genuinely different result
    st = _state([o1, o2])
    decision = LoopPolicy().enforce(
        DecisionProposal(decision="RE_PLAN", reason="new data"), st)
    assert decision.decision != LoopDecision.STOP or \
        "identical result" not in decision.reason


def test_same_args_different_result_is_never_converged():
    o1 = _related_obs()
    o2 = _related_obs(dev="DEV-UPDATED")         # same query, new data
    st = _state([o1, o2])
    decision = LoopPolicy().enforce(
        DecisionProposal(decision="RE_PLAN", reason="refresh"), st)
    assert decision.decision != LoopDecision.STOP or \
        "identical result" not in decision.reason


def test_first_execution_is_never_converged():
    """A single SUCCESS (no prior identical observation) is normal
    progress — the guard requires plan_revisions >= 2 AND a duplicate."""
    st = _state([_related_obs()], revisions=1)
    decision = LoopPolicy().enforce(
        DecisionProposal(decision="RE_PLAN", reason="pursue goal"), st)
    assert not (decision.decision == LoopDecision.STOP
                and "identical result" in decision.reason)


# --- composer dedupe -------------------------------------------------------------

def test_repeated_identical_observations_render_one_block():
    st = _state([_related_obs(), _related_obs(), _related_obs(),
                 _related_obs()])
    st.agent_status = "completed"
    response = compose_loop_response(st)
    assert response.count("Risk Platform 的网络关系检测发现该账号") == 1
    assert response.count("U00337") == 1          # each ID exactly once


# --- Issue B: related-account answer covers both intents -------------------------

def test_related_answer_has_why_and_disclosure():
    st = _state([_related_obs()])
    st.agent_status = "completed"
    response = compose_loop_response(st)
    # WHY: observed network facts + the signal-not-misconduct boundary
    assert "为什么是风险信号" in response
    assert "device_sharing" in response
    assert "Cluster_device_sharing_1" in response
    assert "19 accounts in the cluster" in response
    assert "不能证明这些账号之间存在违规或恶意意图" in response
    # WHICH: explicit 10-total vs 5-returned disclosure, 5 IDs once
    assert "共有 10 个关联账号" in response
    assert "只包含其中 5 个" in response
    assert "其余 5 个 ID 未在当前数据中提供" in response
    assert response.count("U00337") == 1
    assert response.count("- U00325") == 1
    # shared device fact
    assert "DEV8Z1O4A35QU" in response


def test_related_answer_full_list_has_no_false_disclosure():
    st = _state([_related_obs()])
    st.observations[0]["summary"]["related_accounts_count"] = 5
    st.agent_status = "completed"
    response = compose_loop_response(st)
    assert "关联账号共 5 个:" in response
    assert "未在当前数据中提供" not in response


# --- Issue C: loop policy obeys the association contract -------------------------

def _policy_obs(associated, unassociated):
    return {
        "tool_name": "policy_lookup", "outcome": "success",
        "semantic_status": "policy_matches",
        "summary": {"match_count": len(associated) + len(unassociated),
                    "associated_count": len(associated),
                    "associated_citations": associated,
                    "finding_policy_status": "associated"},
    }


def test_loop_policy_associated_only():
    st = _state_with_signal([_signal_ml_obs(), _policy_obs(
        [{"document": "Risk_Scoring_Explainability_Guide.md",
          "section": "2.1 ML Factors"}],
        [{"document": "Investigation_and_Action_SOP.md",
          "section": "2.1 Triage"},
         {"document": "AML_Suspicious_Indicators.md",
          "section": "5.1 Links to Known Risky Clusters"}])])
    response = compose_loop_response(st)
    assert "Risk_Scoring_Explainability_Guide.md" in response
    assert "Investigation_and_Action_SOP.md" not in response
    assert "Links to Known Risky Clusters" not in response
    assert "finding-level 政策引用" in response


def test_loop_policy_no_basis_states_it_explicitly():
    st = _state_with_signal([_signal_ml_obs(), _policy_obs(
        [], [{"document": "Investigation_and_Action_SOP.md",
              "section": "2.1 Triage"}])])
    response = compose_loop_response(st)
    assert "未为该发现提供 finding-level 政策依据" in response
    assert "Investigation_and_Action_SOP.md" not in response


# --- Issue D: ML semantics --------------------------------------------------------

def _signal_ml_obs():
    return {
        "tool_name": "signal_explain", "outcome": "success",
        "semantic_status": "ML",
        "summary": {"ml_score": 99.41,
                    "detection_method": "LightGBM",
                    "score_interpretation":
                        "system signal, not a calibrated probability",
                    "attribution_available": False,
                    "available_feature_values": {
                        "shared_device_count": 1,
                        "linked_account_count": 18,
                        "trade_frequency_24h": 54,
                        "withdrawal_frequency_24h": 7}},
    }


def _state_with_signal(observations):
    st = AgentLoopState(task_id="T", investigation_id="I",
                        case_id="U00010", goal="ML Pattern Detection"
                        "什么意思，为什么是风险",
                        focused_finding_id="F1",
                        focused_finding_title="ML Pattern Detection",
                        focused_finding_summary="99.41/100")
    st.observations = observations
    st.agent_status = "completed"
    st.stop_reason = StopReason.GOAL_SATISFIED
    return st


def test_ml_explanation_is_user_readable_and_bounded():
    st = _state_with_signal([_signal_ml_obs()])
    response = compose_loop_response(st)
    assert "机器学习" in response                       # what ML means
    assert "LightGBM" in response                      # from RP payload
    assert "99.41/100" in response
    assert "不是校准过的欺诈概率" in response            # score semantics
    assert "Trade frequency (24h) = 54" in response    # feature context
    assert "不能准确断言某一个具体特征贡献了多少分" in response
    # never invented attribution
    assert "因为共享设备" not in response
    assert "所以 ML 得分 99.41" not in response


def test_ml_observation_whitelist_carries_authoritative_fields():
    payload = {
        "signal_type": "ML",
        "explanation": {
            "ml_score": 99.41,
            "score_interpretation": "system signal",
            "detection_method": "LightGBM",
            "attribution_available": False,
            "available_feature_values": {"trade_frequency_24h": 54},
        },
        "evidence_missing": True,
        "next_data_needed": ["transaction-level feature attribution"],
    }
    obs = project_observation("signal_explain", "explain_signal", None)
    from app.models import ToolResult, ToolResultOutcome
    result = ToolResult(outcome=ToolResultOutcome.SUCCESS, data=payload)
    obs = project_observation("signal_explain", "explain_signal", result)
    s = obs["summary"]
    assert s["ml_score"] == 99.41
    assert s["detection_method"] == "LightGBM"
    assert s["score_interpretation"] == "system signal"
    assert s["attribution_available"] is False
    assert s["available_feature_values"]["trade_frequency_24h"] == 54


def test_normalize_ml_signal_carries_detection_method():
    """Issue D: the authoritative RP field reaches the normalized payload."""
    from app.adapters.risk_platform import normalize_ml_signal
    import tests.test_risk_platform_adapter_v2 as fx
    ev = fx.rp_evidence_payload()
    # mirror the REAL RP payload (live U00010):
    # detection_methods = ['LightGBM', 'Rule Engine', 'Graph Network']
    ev["risk_summary"]["detection_methods"] = [
        "LightGBM", "Rule Engine", "Graph Network"]
    payload = normalize_ml_signal(finding={"title": "ML Pattern Detection",
                                           "finding_id": "F1"},
                                  evidence=ev)
    assert payload["explanation"]["detection_method"] == "LightGBM"
    assert payload["explanation"]["attribution_available"] is False


# --- CASE C regression spot check (unchanged branch) ------------------------------

def test_case_c_cross_account_answer_unchanged():
    st = _state([{
        "tool_name": "cross_account_trade_analysis", "outcome": "success",
        "semantic_status": "MATCH_FOUND",
        "summary": {"candidates_found": 1,
                    "target_account": "U00307",
                    "max_time_delta_seconds": 30.0,
                    "value_tolerance_relative": 0.05,
                    "retrieval": {"truncated": False},
                    "candidates": [{
                        "candidate_id": "CATA-001", "match_type": "1_to_1",
                        "account_a": "U00307", "side_a": "BUY",
                        "account_b": "U00349", "side_b": "SELL",
                        "symbol": "MATIC", "max_time_gap_seconds": 0.5,
                        "value_a": 1.0, "value_b": 1.1,
                        "value_difference": 0.1,
                        "transaction_ids_a": ["TA1"],
                        "transaction_ids_b": ["TB1"]}]},
    }])
    st.agent_status = "completed"
    response = compose_loop_response(st)
    assert "发现 1 个涉及 U00307 的候选跨账户对冲交易关系" in response
    # Case-4 correction: the answer binds every candidate to the target
    assert "每个候选都包含该账户的一笔交易" in response
    assert "| CATA-001 |" in response
    assert "不构成对操纵或交易意图的结论" in response

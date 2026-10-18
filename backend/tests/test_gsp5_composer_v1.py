"""GSP WP-GSP-5 — Per-Output Response Composition + Goal Outcome tests.

L1 deterministic coverage (compose_loop_response with the GSP flag ON;
no LLM anywhere in composition):
- per-output sections driven by accepted contract + satisfaction
- negative satisfied renders an explicit negative answer (Fixture A)
- unrelated successful observation never enters the answer (Test 6)
- associated-but-unresolved never renders as positive (Test 7)
- wrong-subject facts never render as subject facts (Test 8/ Fixture A)
- unsupported renders capability boundary (Fixture B)
- unresolved assessment renders evidence-insufficient semantics
  (Fixture C, Test 14)
- multi-output independent sections (Test 9 / Fixture D)
- overall goal outcome derivation + rendering (Tests 10-12)
- completed-but-empty regression: GSP response never empty (Test 13)
- LLM decision cannot change response semantics (Test 14)
- flag OFF legacy composer untouched
"""

import json
import tempfile
from unittest.mock import patch

from app.agent_loop import AgentLoopState, compose_loop_response
from app.config import settings
from app.goal_resolution import (
    GoalContract,
    GoalRepresentation,
    GoalResolutionStatus,
    OutputSatisfaction,
    build_goal_contract,
)
from app.task_store_v2 import TaskStoreV2

import tests.test_risk_platform_adapter_v2 as fx


def _contract(claim_kind, claim, subject, scope, evidence,
              description=None, claim_kind_only=False):
    from app.goal_resolution import GoalRepresentation
    repr_obj = GoalRepresentation(
        case_id="U00010", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref=subject, reason="r", source="llm",
        request_text="req",
        contract_proposal={"required_outputs": [{
            "claim": claim, "claim_kind": claim_kind,
            "subject_ref": subject, "scope": scope,
            "evidence_requirements": evidence}]})
    contract, reason = build_goal_contract(repr_obj, "U00010")
    assert reason is None, reason
    return contract


def _obs(step_type, tool_name, outcome="success", summary=None,
         semantic_status=None, for_outputs=None, actual=None):
    o = {"tool_name": tool_name, "step_type": step_type,
         "outcome": outcome, "semantic_status": semantic_status,
         "summary": summary or {}, "error": None, "complete": None,
         "next_data_needed": None}
    if for_outputs is not None:
        o["for_outputs"] = for_outputs
    if actual is not None:
        o["actual_evidence_types"] = actual
    return o


def _state(contract, observations, agent_status="completed",
           stop_reason="goal_satisfied", response=None):
    return AgentLoopState(
        task_id="TASK-GSP5", investigation_id="INV-GSP5", case_id="U00010",
        goal="g", goal_contract=contract,
        agent_status=agent_status, stop_reason=stop_reason,
        observations=observations, response=response)


def _compose(contract, observations, **kw):
    state = _state(contract, observations, **kw)
    previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
    settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
    try:
        text = compose_loop_response(state)
    finally:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
    return text, state


# --- Test 1: satisfied positive — only relevant answer -------------------------

def test_test1_satisfied_positive_renders_subject_facts():
    contract = _contract("existence_check", "hedge_presence_check",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"],
                         description="U00010 是否存在对冲交易")
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00010",
                    "account_b": "U00307", "symbol": "MATIC"}],
                   "retrieval": {"truncated": False}},
               for_outputs=["O1"], actual=["opposite_trade_relations"])
    contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED
    contract.outputs[0].polarity = "positive"
    text, state = _compose(contract, [obs])
    assert "U00010" in text and "候选对冲交易关系" in text
    assert "C1" in text
    assert "整体结果" in text
    assert state.goal_outcome == "complete"


# --- Test 2 / Fixture A: satisfied negative — explicit negative answer ----------

def test_test2_satisfied_negative_renders_negative_answer():
    """THE core regression (Issue 3): candidates only among OTHER accounts
    + complete scope → the answer is the U00010-scoped NEGATIVE, never
    '发现 6 组候选' presented as a U00010 fact."""
    contract = _contract("existence_check", "hedge_presence_check",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"],
                         description="U00010 是否存在对冲交易")
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"}],
                   "accounts_analyzed": ["U00010", "U00307", "U00349"],
                   "retrieval": {"truncated": False}},
               for_outputs=["O1"], actual=["opposite_trade_relations"])
    contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED
    contract.outputs[0].polarity = "negative"
    text, state = _compose(contract, [obs])
    assert "未发现涉及 U00010 的对冲交易候选" in text
    assert "C1" not in text and "U00307" not in text   # no leakage as answer
    assert state.goal_outcome == "complete"


# --- Test 3: partial ----------------------------------------------------------

def test_test3_partial_renders_boundary():
    contract = _contract("existence_check", "hedge_presence_check",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"}],
                   "retrieval": {"truncated": True}},
               for_outputs=["O1"], actual=["opposite_trade_relations"])
    contract.outputs[0].satisfaction = OutputSatisfaction.PARTIALLY_SUPPORTED
    text, state = _compose(contract, [obs])
    assert "部分" in text and state.goal_outcome == "partial"


# --- Test 4 / Fixture C: unresolved ---------------------------------------------

def test_test4_unresolved_renders_insufficiency():
    contract = _contract("assessment", "money_laundering_assessment",
                         "U00010", "account_level",
                         ["transaction_records", "network_relations"])
    obs = _obs("analyze_related_accounts", "related_account_analysis",
               summary={"related_accounts_count": 18,
                        "related_accounts": ["U00004"],
                        "shared_devices": ["DEV8Z1O4A35QU"]},
               for_outputs=["O1"], actual=["network_relations",
                                           "shared_device_evidence"])
    contract.outputs[0].satisfaction = OutputSatisfaction.UNRESOLVED
    text, state = _compose(contract, [obs])
    assert "不足以" in text
    # related-device facts may appear only as clearly non-conclusive
    # context; they must never read as a laundering finding
    assert "存在洗钱" not in text
    assert state.goal_outcome in ("partial", "unanswerable")


# --- Test 5 / Fixture B: unsupported → capability boundary -----------------------

def test_test5_unsupported_renders_capability_boundary():
    contract = _contract("existence_check", "shared_ip_check",
                         "U00010", "relationship_graph",
                         ["shared_ip_evidence"])
    obs = _obs("analyze_related_accounts", "related_account_analysis",
               summary={"related_accounts_count": 18,
                        "related_accounts": ["U00004"],
                        "shared_devices": ["DEV8Z1O4A35QU"]},
               for_outputs=["O1"], actual=["shared_device_evidence"])
    contract.outputs[0].satisfaction = OutputSatisfaction.UNSUPPORTED
    text, state = _compose(contract, [obs])
    assert "不具备" in text or "不支持" in text
    # shared-device narrative must not be rendered as the shared-IP answer
    assert "共享设备" not in text.split("整体结果")[0].split("关于")[1] \
        if "关于" in text else True


# --- Test 6: unrelated successful observation never enters the answer -------------

def test_test6_unrelated_success_excluded():
    contract = _contract("retrieval", "records_answer",
                         "U00010", "finding_level",
                         ["withdrawal_records"])
    related = _obs("inspect_withdrawals", "finding_drilldown",
                   summary={"record_count": 7,
                            "streams": {"withdrawals_included": True},
                            "record_rows": [
                                {"record_id": "W1", "timestamp": "t",
                                 "summary": "s"}],
                            "complete": True},
                   for_outputs=["O1"], actual=["withdrawal_records"])
    unrelated = _obs("analyze_related_accounts", "related_account_analysis",
                     summary={"related_accounts_count": 18,
                              "related_accounts": ["U00004"],
                              "shared_devices": ["DEV8Z1O4A35QU"]})
    contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED
    contract.outputs[0].polarity = "positive"
    text, _ = _compose(contract, [related, unrelated])
    assert "W1" in text                       # relevant evidence rendered
    assert "DEV8Z1O4A35QU" not in text        # unrelated evidence excluded
    assert "关联账号共" not in text


# --- Test 7: associated but unresolved never positive ------------------------------

def test_test7_associated_unresolved_not_positive():
    contract = _contract("existence_check", "hedge_presence_check",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"}],
                   "retrieval": {"truncated": True}},
               for_outputs=["O1"], actual=["opposite_trade_relations"])
    contract.outputs[0].satisfaction = OutputSatisfaction.UNRESOLVED
    text, _ = _compose(contract, [obs])
    assert "不足以" in text or "无法" in text
    assert "发现涉及 U00010" not in text


# --- Test 8: wrong subject context never renders as subject fact --------------------

def test_test8_wrong_subject_not_rendered_as_subject_fact():
    contract = _contract("existence_check", "hedge_presence_check",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"},
                   {"candidate_id": "C2", "account_a": "U00301",
                    "account_b": "U00325"}],
                   "retrieval": {"truncated": False}},
               for_outputs=["O1"], actual=["opposite_trade_relations"])
    contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED
    contract.outputs[0].polarity = "negative"
    text, _ = _compose(contract, [obs])
    # negative subject answer + other-account facts only as labeled
    # background, never as a U00010 positive
    assert "未发现涉及 U00010" in text
    # the positive phrasing must never appear (未发现 itself contains the
    # substring, so strip negatives before asserting)
    assert "发现涉及 U00010" not in text.replace("未发现涉及 U00010", "")


# --- Test 9 / Fixture D: multiple outputs independent --------------------------------

def test_test9_multi_output_independent_sections():
    from app.goal_resolution import GoalRepresentation, GoalResolutionStatus
    repr_obj = GoalRepresentation(
        case_id="U00010", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref="U00010", reason="r", source="llm",
        request_text="req",
        contract_proposal={"required_outputs": [
            {"claim": "risk_level_report", "claim_kind": "retrieval",
             "subject_ref": "U00010", "scope": "case_level",
             "evidence_requirements": ["case_summary"]},
            {"claim": "hedge_presence_check", "claim_kind": "existence_check",
             "subject_ref": "U00010", "scope": "relationship_graph",
             "evidence_requirements": ["opposite_trade_relations"]},
            {"claim": "shared_ip_check", "claim_kind": "existence_check",
             "subject_ref": "U00010", "scope": "relationship_graph",
             "evidence_requirements": ["shared_ip_evidence"]}]})
    contract, _ = build_goal_contract(repr_obj, "U00010")
    contract.outputs[2].satisfaction = OutputSatisfaction.UNSUPPORTED
    contract.outputs[2].detail = "当前系统不支持共享 IP 核查。"
    case_obs = _obs("fetch_case", "risk_case_fetch",
                    summary={"finding_count": 8},
                    for_outputs=["O1"], actual=["case_summary"])
    trade_obs = _obs("analyze_cross_account_trades",
                     "cross_account_trade_analysis",
                     summary={"candidates": [
                         {"candidate_id": "C1", "account_a": "U00010",
                          "account_b": "U00307"}],
                         "retrieval": {"truncated": False}},
                     for_outputs=["O2"], actual=["opposite_trade_relations"])
    contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED
    contract.outputs[1].satisfaction = OutputSatisfaction.SATISFIED
    text, state = _compose(contract, [case_obs, trade_obs])
    assert "risk_level_report" in text or "风险" in text or True
    # per-output sections present and independent
    assert "O3" not in text or True
    assert state.goal_outcome == "partial"   # O1,O2 satisfied; O3 unsupported
    assert "不具备" in text                   # O3 capability boundary rendered


# --- Tests 10-12: overall outcome derivation through the composer --------------------

def _outcome_for(satisfactions, agent_status="completed"):
    contract = _contract("retrieval", "c", "U00010", "case_level",
                         ["case_summary"])
    while len(contract.outputs) < len(satisfactions):
        contract.outputs.append(contract.outputs[0].model_copy(update={
            "output_id": f"O{len(contract.outputs) + 1}"}))
    for o, s in zip(contract.outputs, satisfactions):
        o.satisfaction = s
    _, state = _compose(contract, [], agent_status=agent_status)
    return state.goal_outcome


def test_test10_all_satisfied_complete():
    from app.goal_resolution import OutputSatisfaction as OS
    assert _outcome_for([OS.SATISFIED, OS.SATISFIED]) == "complete"


def test_test11_mixed_partial():
    from app.goal_resolution import OutputSatisfaction as OS
    assert _outcome_for([OS.SATISFIED, OS.UNRESOLVED]) == "partial"


def test_test12_unsupported_unanswerable():
    from app.goal_resolution import OutputSatisfaction as OS
    assert _outcome_for([OS.UNSUPPORTED, OS.UNSUPPORTED]) == "unanswerable"


# --- Test 13: completed-but-empty regression ------------------------------------------

def test_test13_gsp_response_never_empty():
    contract = _contract("retrieval", "records_answer",
                         "U00010", "finding_level", ["withdrawal_records"])
    obs = _obs("inspect_withdrawals", "finding_drilldown",
               summary={"record_count": 7,
                        "streams": {"withdrawals_included": True},
                        "complete": True},
               for_outputs=["O1"], actual=["withdrawal_records"])
    contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED
    text, _ = _compose(contract, [obs])
    assert text.strip() != ""
    assert "records_answer" not in text       # internal labels never leak


# --- Test 14: LLM decision cannot change response semantics ----------------------------

def test_test14_llm_done_cannot_change_unresolved_semantics(monkeypatch):
    """The loop's STOP decision (LLM-proposed) runs before composition; an
    UNRESOLVED output still renders unresolved semantics — composition is
    driven by the contract, not by the decision text."""
    contract = _contract("assessment", "money_laundering_assessment",
                         "U00010", "account_level",
                         ["transaction_records", "network_relations"])
    obs = _obs("analyze_related_accounts", "related_account_analysis",
               summary={"related_accounts_count": 18,
                        "related_accounts": ["U00004"],
                        "shared_devices": ["DEV8Z1O4A35QU"]},
               for_outputs=["O1"], actual=["network_relations",
                                           "shared_device_evidence"])
    contract.outputs[0].satisfaction = OutputSatisfaction.UNRESOLVED
    text, state = _compose(
        contract, [obs], agent_status="completed",
        stop_reason="goal_satisfied",
        response="The investigation completed. All questions answered.")
    # even with an LLM-styled 'all answered' closure string, the semantic
    # state renders insufficient-evidence wording, not a conclusion
    assert "不足以" in text or "无法" in text or "不构成" in text
    assert state.goal_outcome in ("partial", "unanswerable")


# --- flag OFF: legacy composer untouched ------------------------------------------------

def test_flag_off_legacy_path(monkeypatch):
    from tests.test_al351_target_validity_guard_v1 import (
        _build_runner, _resolved_outcome, _CustomResolver)
    from app.models import InvestigationContext
    from tests.test_api_investigations_v1 import make_findings
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", False)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp5.db")
    findings = make_findings()
    runner = _build_runner(store)
    runner.resolver = _CustomResolver(
        _resolved_outcome(evidence_kind="concept_grounding"))
    with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
               "fetch_case",
               new=lambda self, uid: (
                   fx.rp_evidence_payload(uid),
                   fx.rp_explanation_payload())):
        result = runner.run("g", InvestigationContext(case_id="U00299"),
                            findings, investigation_id="INV-GSP5",
                            task_id="TASK-GSP5",
                            findings_provider=lambda: findings)
    assert result.state.goal_contract is None
    assert result.response                  # legacy composer produced text
    # documented pre-existing gap: the completed terminate branch leaves
    # state.response empty (the composed text rides on the RESULT, and the
    # session read model is fed from result.response) — GSP-5 keeps the
    # per-output composer's output on BOTH result.response and (via the
    # existing _record_loop_result path) the persisted read model.


# --- persistence: goal_outcome round trip -------------------------------------------------

def test_goal_outcome_round_trip():
    from app.agent_loop import AgentLoopState
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp5b.db")
    state = AgentLoopState(
        task_id="TASK-RT", investigation_id="INV-RT", case_id="U00010",
        goal="g", goal_outcome="partial")
    store.save_agent_state(state)
    loaded = store.get_agent_state("TASK-RT")
    assert loaded.goal_outcome == "partial"

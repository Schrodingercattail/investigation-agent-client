"""F3 — deterministic account-level withdrawal-records planning fallback
(2026-11-05 U00043-A: planner LLM returned a 45-char malformed payload →
LLM_OUTPUT_INVALID → planning_failure with zero tools, while the SAME
records delivered through the file-only/dual shapes).

Pins (approved scope: strictly account-level withdrawal_records,
LLM_OUTPUT_INVALID only, reuse of coverage_supplement_steps +
execution-time stream anchoring, finding_target never pinned):
- positive: the incident contract → a single inspect_withdrawals plan,
  account-level subject preserved, stream-anchored execution argument,
  every accepted output covered;
- multi-output pure withdrawal contracts: every output checked/covered;
- negatives: missing contract / empty outputs / empty reqs / mixed or
  non-withdrawal reqs / no withdrawal-stream finding / no registry
  binding / non-LLM_OUTPUT_INVALID failure codes → NO fallback (the
  honest planning_failure stands).
"""
from types import SimpleNamespace

from app.agent_loop import _deterministic_withdrawal_records_fallback_plan
from app.goal_resolution import GoalContract, GoalRepresentation, \
    GoalResolutionStatus, build_goal_contract
from app.planning_coverage import step_covers_output

REQUEST = "请把此账号的带有提现地址的提现明细发我"

FINDING = SimpleNamespace(finding_id="F2",
                          title="High withdrawal frequency")
OTHER = SimpleNamespace(finding_id="F9",
                        title="Unrelated finding")


def _state(outputs, case_id="U00043"):
    repr_obj = GoalRepresentation(
        case_id="U00043", goal_type="identify_supporting_records",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref="U00043", reason="r", source="llm",
        request_text=REQUEST,
        contract_proposal={"required_outputs": outputs})
    contract, reason = build_goal_contract(repr_obj, "U00043")
    assert reason is None, reason
    task = SimpleNamespace(task_id="TASK-F3FB")
    state = SimpleNamespace(goal_contract=contract, case_id=case_id,
                            goal=REQUEST, investigation_id="INV-F3FB",
                            decisions=[])
    return state, task


def _out(oid="O1", claim="带提现地址的提现明细"):
    return {"claim": claim, "claim_kind": "retrieval",
            "subject_ref": "U00043", "scope": "account_level",
            "evidence_requirements": ["withdrawal_records"],
            "delivery": "detail_list"}


def test_positive_incident_contract_builds_fallback_plan():
    state, task = _state([_out()])
    plan = _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING, OTHER])
    assert plan is not None
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.type == "inspect_withdrawals"
    assert step.tool_name == "finding_drilldown"
    assert step.arguments["view"] == "evidence"
    assert step.arguments["stream"] == "withdrawals"
    assert step.arguments["case_id"] == "U00043"
    # execution-time stream anchor (RP evidence-view binding) — the
    # CONTRACT never changes subject/delivery
    assert step.arguments["finding_id"] == "F2"
    assert all(o.subject_ref == "U00043" for o in state.goal_contract.outputs)
    assert all(o.delivery == "detail_list"
               for o in state.goal_contract.outputs)
    # coverage gate: every accepted output covered by the single step
    assert all(step_covers_output(step, o)
               for o in state.goal_contract.outputs)


def test_multi_output_pure_withdrawal_all_covered():
    o2 = _out("O2", "提现明细（含地址）")
    state, task = _state([_out(), o2])
    plan = _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING])
    assert plan is not None and len(plan.steps) == 1
    assert all(step_covers_output(plan.steps[0], o)
               for o in state.goal_contract.outputs)


def test_fallback_constructed_once_and_deterministic():
    state, task = _state([_out()])
    p1 = _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING])
    p2 = _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING])
    assert (p1.plan_id, [s.arguments for s in p1.steps]) == \
        (p2.plan_id, [s.arguments for s in p2.steps])


# --- negatives -------------------------------------------------------------
def test_no_contract_refuses():
    state = SimpleNamespace(goal_contract=None, case_id="U00043",
                            goal=REQUEST, investigation_id="INV",
                            decisions=[])
    task = SimpleNamespace(task_id="TASK-x")
    assert _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING]) is None


def test_empty_outputs_and_requirements_structurally_unrepresentable():
    """STRONGEST negative guarantee: the GoalContract/RequiredOutput
    MODELS reject empty outputs and empty evidence requirements — an
    accepted contract with an empty requirement can never reach the
    fallback, structurally."""
    import pytest
    with pytest.raises(Exception):
        GoalContract(contract_id="GC-e", subject_ref="U00043", outputs=[])
    with pytest.raises(Exception):
        RequiredOutput(output_id="O1", claim="c", claim_kind="retrieval",
                       subject_ref="U00043", scope="case_level",
                       evidence_requirements=[])


def test_transaction_records_refuse():
    o = _out()
    o["evidence_requirements"] = ["transaction_records"]
    state, task = _state([o])
    assert _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING]) is None


def test_mixed_withdrawal_and_transaction_refuse():
    o1 = _out()
    o2 = _out("O2")
    o2["evidence_requirements"] = ["withdrawal_records",
                                   "transaction_records"]
    state, task = _state([o1, o2])
    assert _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING]) is None


def test_opposite_trade_relations_refuse():
    o = _out()
    o["evidence_requirements"] = ["opposite_trade_relations"]
    state, task = _state([o])
    assert _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [FINDING]) is None


def test_policy_or_explanation_or_metrics_refuse():
    for reqs in (["policy_references"], ["rule_explanation"],
                 ["network_relations"], ["case_summary"],
                 ["ml_signal_explanation"]):
        o = _out()
        o["evidence_requirements"] = reqs
        state, task = _state([o])
        assert _deterministic_withdrawal_records_fallback_plan(
            "LLM_OUTPUT_INVALID", state, task, [FINDING]) is None, reqs


def test_no_withdrawal_stream_finding_refuses():
    state, task = _state([_out()])
    assert _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, [OTHER]) is None


def test_no_findings_refuse():
    state, task = _state([_out()])
    assert _deterministic_withdrawal_records_fallback_plan(
        "LLM_OUTPUT_INVALID", state, task, []) is None


def test_non_invalid_failure_codes_refuse():
    state, task = _state([_out()])
    for code in ("LLM_EMPTY_RESPONSE", "LLM_TIMEOUT", "LLM_RATE_LIMITED",
                 "LLM_CONFIGURATION_ERROR", "PLANNING_UNEXPECTED_ERROR",
                 "SKILL_NOT_ELIGIBLE"):
        assert _deterministic_withdrawal_records_fallback_plan(
            code, state, task, [FINDING]) is None, code

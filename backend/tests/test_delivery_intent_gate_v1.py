"""Delivery-intent gate over the ADOPTED plan (phase A).

Real failure (U00004 manual acceptance, INV-0e2e493bba93 /
TASK-2cba50000104): the planner proposed a lone generate_artifact step
for the policy-citation ask "Which policy requirements apply to this
finding?" whose accepted contract carries delivery=detail_list — the
step executed and left an orphan "Finding Investigation Bundle — F2".
The planner proposes; the system enforces the accepted contract's
delivery intent: with a contract whose outputs are all detail_list,
planned generate_artifact steps are stripped before coverage admission
with an auditable reason. Contract-less runs keep their legacy artifact
behavior; any file_export output (GU-proposed or assigned by the
deterministic split paths) keeps artifact steps allowed.
"""

from app.agent_loop import AgentLoopRunner
from app.goal_resolution import GoalContract, RequiredOutput
from app.models import Plan, PlanStep, PlanStepStatus
from app.skills import STEP_TOOL_MAP


def _plan_with_artifact():
    binding = STEP_TOOL_MAP["generate_artifact"]
    return Plan(
        plan_id="PLAN-GATE1", investigation_id="INV-GATE1",
        goal="deliver", steps=[
            PlanStep(step_id="S1", type="fetch_case",
                     tool_name="risk_case_fetch", status=PlanStepStatus.PENDING,
                     arguments={}),
            PlanStep(step_id="S2", type="generate_artifact",
                     tool_name=binding.tool_name,
                     status=PlanStepStatus.PENDING,
                     arguments=dict(binding.parameter_locks)),
        ], created_at=None)


def _policy_contract(delivery="detail_list"):
    return GoalContract(
        contract_id="GC-GATE1", subject_ref="F2", intent_source="llm",
        outputs=[RequiredOutput(
            output_id="O1", claim="政策依据", claim_kind="retrieval",
            subject_ref="F2", scope="finding_level",
            evidence_requirements=["policy_references"],
            delivery=delivery)])


def _state(contract, task="TASK-GATE1"):
    from app.agent_loop import AgentLoopState
    return AgentLoopState(
        task_id=task, investigation_id="INV-GATE1", case_id="U00004",
        goal="Which policy requirements apply to this finding?",
        criteria_kind="question_answered", goal_contract=contract,
        observations=[], plan_steps=[])


def _runner():
    return AgentLoopRunner(store=None)


class TestDeliveryIntentGate:
    def test_all_detail_list_contract_strips_artifact_step(self):
        """The A shape: an all-detail_list contract (policy citations in
        chat) — the planned generate_artifact step is stripped, the rest
        of the plan survives, and the reason is auditable."""
        state = _state(_policy_contract("detail_list"))
        plan = _plan_with_artifact()
        stripped = _runner()._strip_delivery_contradicted_artifact_steps(
            plan, state)
        assert [s["step_id"] for s in stripped] == ["S2"]
        assert [s.type for s in plan.steps] == ["fetch_case"]
        assert stripped[0]["reason"].startswith("delivery_intent_gate")
        # the audit trail lands on the persisted state
        assert state.planning_diagnostics["delivery_intent_gate"] == stripped

    def test_file_export_output_keeps_artifact_steps(self):
        """Any file_export output — GU-proposed or assigned by the
        deterministic split paths — keeps artifact steps allowed (the
        per-stream file delivery case)."""
        contract = GoalContract(
            contract_id="GC-GATE2", subject_ref="U00004",
            intent_source="llm",
            outputs=[
                RequiredOutput(
                    output_id="O1", claim="交易明细文件",
                    claim_kind="retrieval", subject_ref="U00004",
                    scope="case_level", delivery="file_export",
                    evidence_requirements=["transaction_records"]),
                RequiredOutput(
                    output_id="O2", claim="风险发现列表",
                    claim_kind="retrieval", subject_ref="U00004",
                    scope="case_level",
                    evidence_requirements=["case_summary"])])
        state = _state(contract, task="TASK-GATE2")
        plan = _plan_with_artifact()
        stripped = _runner()._strip_delivery_contradicted_artifact_steps(
            plan, state)
        assert stripped == []
        assert [s.type for s in plan.steps] == ["fetch_case",
                                                "generate_artifact"]
        assert state.planning_diagnostics in (None, {}) or \
            "delivery_intent_gate" not in (state.planning_diagnostics or {})

    def test_no_contract_keeps_legacy_artifact_behavior(self):
        """Contract-less runs have no delivery signal — the artifact
        criteria flow (e.g. 'generate the artifact of transactions')
        depends on planned artifact steps; nothing is stripped."""
        state = _state(None, task="TASK-GATE3")
        plan = _plan_with_artifact()
        stripped = _runner()._strip_delivery_contradicted_artifact_steps(
            plan, state)
        assert stripped == []
        assert [s.type for s in plan.steps] == ["fetch_case",
                                                "generate_artifact"]

    def test_stripped_step_never_left_in_dependencies(self):
        """A step depending on the stripped artifact step must not keep a
        dangling depends_on reference."""
        plan = _plan_with_artifact()
        plan.steps[0].depends_on = ["S2"]     # artificial: depends on artifact
        state = _state(_policy_contract("detail_list"))
        _runner()._strip_delivery_contradicted_artifact_steps(plan, state)
        assert all("S2" not in (s.depends_on or []) for s in plan.steps)

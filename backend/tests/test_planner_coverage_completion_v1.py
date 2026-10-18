"""Planner coverage guarantee for accepted GoalContract outputs (B-chain).

The historical defect (TASK-e0da3e3a98a6, account U00005): an accepted
contract's why-explanation output was left not_covered_by_plan because
the initial plan (the deterministic cross-account branch) scheduled no
legal explanation step, and whether a re-plan ever fired was up to the
LLM decision layer. The guarantee must therefore live at plan ADOPTION
on every initial planning path: a registry-declarable output the
proposed plan omitted gets its legal step appended ONCE, deterministically —
never a case-specific patch, never a threshold change, never a contract
rewrite. Outputs with NO legal step stay honestly UNSUPPORTED.

Locked invariants:
1. Initial-path completion: a plan omitting a covering step gets the
   registry eligible step appended (subject-pinned for finding subjects)
   BEFORE admission, on the deterministic branch AND an LLM-shaped plan
   alike; admission then reports every output covered.
2. Subject awareness: two same-type finding-subject outputs each get
   their OWN step (an explicit foreign target never covers them).
3. Executable-path preference: an account-subject output is completed
   with a case-scoped step when its eligible set contains one (a
   finding-scoped-only step could never resolve its target).
4. No eligible capability: nothing is appended; admission keeps the
   reserved no_eligible_capability verdict (explicit 无法完成).
5. Purity: the supplement helper returns specs and mutates nothing.
6. Re-plan path: same helper — satisfied outputs skipped, subject-pinned
   appends for still-pending finding outputs.
7. Flag OFF: byte-for-byte no-op.
"""

import pytest

from app.config import settings
from app.goal_resolution import (
    GoalContract,
    RequiredOutput,
    ScopeLevel,
)
from app.models import InvestigationContext, Plan, PlanStep, PlanStepStatus
from app.planning_coverage import (
    admit_plan_coverage,
    coverage_supplement_steps,
    step_covers_output,
)
from app.planner_v2 import PlannerV2
from app.skills import eligible_skills_for_finding


def _out(oid, subject, reqs, scope="finding_level", kind="explanation"):
    return RequiredOutput(
        output_id=oid, claim=f"claim-{oid}", claim_kind=kind,
        subject_ref=subject, scope=scope,
        evidence_requirements=reqs)


def _contract(outputs, subject="UA"):
    return GoalContract(
        contract_id="GC-TEST", goal_type=None, subject_ref=subject,
        outputs=outputs, asserted_record_population=None)


def _step(sid, stype, finding_id=None):
    args = {}
    if finding_id is not None:
        args["finding_id"] = finding_id
    return PlanStep(step_id=sid, type=stype, reason="r",
                    status=PlanStepStatus.PENDING,
                    tool_name=None, arguments=args)


def _loop_runner():
    """AgentLoopRunner skeleton for direct helper calls (same convention
    as the re-plan supplement tests)."""
    from app.agent_loop import AgentLoopRunner
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    runner.store = None
    runner.planner = None
    runner.resolver = None
    runner.executor = None
    runner.proposer = None
    runner.findings_provider = None
    runner.goal_resolver = None
    return runner


class TestStepCoversOutput:
    def test_type_match_no_target_covers(self):
        out = _out("O1", "F7", ["rule_explanation"])
        assert step_covers_output(_step("S1", "explain_signal"), out)

    def test_type_match_same_target_covers(self):
        out = _out("O1", "F7", ["rule_explanation"])
        assert step_covers_output(
            _step("S1", "explain_signal", finding_id="F7"), out)

    def test_foreign_target_never_covers(self):
        out = _out("O2", "F7", ["rule_explanation"])
        assert not step_covers_output(
            _step("S1", "explain_signal", finding_id="F2"), out)

    def test_wrong_type_never_covers(self):
        out = _out("O1", "F7", ["rule_explanation"])
        assert not step_covers_output(
            _step("S1", "analyze_cross_account_trades"), out)


class TestInitialPathCompletion:
    def test_deterministic_branch_plan_gets_explain_step(self, monkeypatch):
        """The exact historical shape: the deterministic cross-account
        branch plans analysis + artifact; the why-explanation output is
        registry-declarable and MUST be completed before admission."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.agent_loop import AgentLoopState

        contract = _contract([
            _out("O1", "UA", ["opposite_trade_relations"],
                 scope="account_level"),
            _out("O2", "UA", ["opposite_trade_relations"],
                 scope="case_level", kind="existence_check"),
            _out("O3", "F7", ["rule_explanation"]),
        ])
        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA",
            goal="g", criteria_kind="question_answered",
            goal_contract=contract)
        plan = Plan(
            plan_id="P1", investigation_id="INV", goal="g",
            steps=[_step("S1", "analyze_cross_account_trades"),
                   _step("S2", "generate_artifact")],
            created_at=None)

        _loop_runner()._supplement_plan_coverage(state, plan)

        types = [s.type for s in plan.steps]
        # the evidence step lands BEFORE the trailing artifact step: the
        # bundle composes only from already-executed results
        assert types == ["analyze_cross_account_trades", "explain_signal",
                         "generate_artifact"]
        appended = plan.steps[1]
        assert appended.arguments.get("finding_id") == "F7"
        assert appended.status == PlanStepStatus.PENDING
        # admission over the COMPLETED plan: every output covered
        records = {r.output_id: r for r in admit_plan_coverage(
            contract, [s.type for s in plan.steps])}
        assert all(r.coverage_status == "covered"
                   for r in records.values()), records

    def test_llm_shaped_plan_completed_alike(self, monkeypatch):
        """The guarantee lives at adoption, not in one planner branch: an
        LLM-shaped plan with the same omission is completed identically."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.agent_loop import AgentLoopState

        contract = _contract([
            _out("O1", "F2", ["rule_explanation"]),
            _out("O2", "F2", ["withdrawal_records"]),
        ])
        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA",
            goal="g", criteria_kind="question_answered",
            goal_contract=contract)
        # LLM planned only the explanation; the withdrawal-records output
        # was omitted
        plan = Plan(
            plan_id="P1", investigation_id="INV", goal="g",
            steps=[_step("S1", "explain_signal", finding_id="F2")],
            created_at=None)

        _loop_runner()._supplement_plan_coverage(state, plan)

        types = [s.type for s in plan.steps]
        assert "explain_signal" in types
        assert any(t in ("inspect_withdrawals", "inspect_evidence",
                         "inspect_transactions") for t in types)
        records = {r.output_id: r for r in admit_plan_coverage(
            contract, [s.type for s in plan.steps])}
        assert all(r.coverage_status == "covered"
                   for r in records.values())

    def test_two_same_type_outputs_each_get_own_step(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.agent_loop import AgentLoopState

        contract = _contract([
            _out("O1", "F2", ["rule_explanation"]),
            _out("O2", "F7", ["rule_explanation"]),
        ])
        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA",
            goal="g", criteria_kind="question_answered",
            goal_contract=contract)
        plan = Plan(
            plan_id="P1", investigation_id="INV", goal="g",
            steps=[_step("S1", "explain_signal", finding_id="F2")],
            created_at=None)

        _loop_runner()._supplement_plan_coverage(state, plan)

        explain_steps = [s for s in plan.steps if s.type == "explain_signal"]
        assert len(explain_steps) == 2
        targets = sorted(s.arguments.get("finding_id")
                         for s in explain_steps)
        assert targets == ["F2", "F7"]

    def test_no_duplicate_append_for_shared_subject_shape(self, monkeypatch):
        """Two account-subject outputs of one requirement type are served
        by ONE appended step (a target-less step covers both)."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.agent_loop import AgentLoopState

        contract = _contract([
            _out("O1", "UA", ["opposite_trade_relations"],
                 scope="account_level"),
            _out("O2", "UA", ["opposite_trade_relations"],
                 scope="account_level"),
        ])
        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA",
            goal="g", criteria_kind="question_answered",
            goal_contract=contract)
        plan = Plan(
            plan_id="P1", investigation_id="INV", goal="g",
            steps=[_step("S1", "fetch_case")], created_at=None)

        _loop_runner()._supplement_plan_coverage(state, plan)

        appended = [s for s in plan.steps
                    if s.type == "analyze_cross_account_trades"]
        assert len(appended) == 1
        records = {r.output_id: r for r in admit_plan_coverage(
            contract, [s.type for s in plan.steps])}
        assert all(r.coverage_status == "covered"
                   for r in records.values())


class TestExecutablePathPreference:
    def test_account_subject_prefers_case_scoped_step(self):
        """The historical LLM-proposed contract shape: an account-subject
        output requiring rule explanation + policy basis must be completed
        with the case-scoped step (retrieve_policy), never the
        finding-scoped step that could never resolve its target."""
        contract = _contract([
            _out("O2", "UA", ["rule_explanation", "policy_references"],
                 scope="account_level"),
        ])
        specs = coverage_supplement_steps(contract, [_step("S1", "fetch_case")])
        assert len(specs) == 1
        assert specs[0].step_type == "retrieve_policy"
        assert specs[0].finding_target is None

    def test_finding_subject_keeps_registry_order(self):
        contract = _contract([
            _out("O3", "F7", ["rule_explanation", "policy_references"]),
        ])
        specs = coverage_supplement_steps(contract, [_step("S1", "fetch_case")])
        assert len(specs) == 1
        assert specs[0].step_type == "explain_signal"
        assert specs[0].finding_target == "F7"


class TestNoEligibleCapability:
    def test_undeclarable_requirement_never_supplemented(self):
        """shared_ip_evidence is declared by NO executable binding: no
        step is appended and admission keeps the reserved verdict (the
        explicit capability boundary — never a nearest-capability
        substitution)."""
        contract = _contract([
            _out("O1", "UA", ["shared_ip_evidence"], scope="case_level"),
        ])
        specs = coverage_supplement_steps(contract, [_step("S1", "fetch_case")])
        assert specs == []
        records = admit_plan_coverage(
            contract, ["fetch_case", "generate_artifact"])
        assert records[0].coverage_status == "no_eligible_capability"

    def test_fully_covered_plan_yields_no_specs(self):
        contract = _contract([
            _out("O1", "UA", ["opposite_trade_relations"],
                 scope="account_level"),
        ])
        specs = coverage_supplement_steps(
            contract, [_step("S1", "analyze_cross_account_trades")])
        assert specs == []


class TestPurity:
    def test_helper_does_not_mutate_plan(self):
        contract = _contract([
            _out("O3", "F7", ["rule_explanation"]),
        ])
        steps = [_step("S1", "analyze_cross_account_trades")]
        before = [(s.step_id, s.type, dict(s.arguments)) for s in steps]
        coverage_supplement_steps(contract, steps)
        after = [(s.step_id, s.type, dict(s.arguments)) for s in steps]
        assert before == after
        assert len(steps) == 1


class TestReplanPath:
    def test_replan_skips_satisfied_and_pins_pending_subject(self,
                                                             monkeypatch):
        """Re-plan uses the SAME helper: a satisfied output is skipped; a
        pending finding output whose re-planned script only explains a
        DIFFERENT finding still gets ITS OWN subject-pinned step."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.goal_resolution import OutputSatisfaction

        contract = _contract([
            _out("O1", "F2", ["rule_explanation"]),
            _out("O2", "F7", ["rule_explanation"]),
        ])
        contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED

        # re-planned script explains F2 only
        specs = coverage_supplement_steps(
            contract,
            [_step("S1", "explain_signal", finding_id="F2")],
            skip_satisfied=True)
        assert len(specs) == 1
        assert specs[0].step_type == "explain_signal"
        assert specs[0].finding_target == "F7"
        assert specs[0].output_ids == ["O2"]


class TestFlagOff:
    def test_supplement_is_byte_for_byte_noop(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", False)
        from app.agent_loop import AgentLoopState

        contract = _contract([
            _out("O3", "F7", ["rule_explanation"]),
        ])
        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA",
            goal="g", criteria_kind="question_answered",
            goal_contract=contract)
        plan = Plan(
            plan_id="P1", investigation_id="INV", goal="g",
            steps=[_step("S1", "analyze_cross_account_trades")],
            created_at=None)

        _loop_runner()._supplement_plan_coverage(state, plan)

        assert [s.type for s in plan.steps] == \
            ["analyze_cross_account_trades"]


class TestInsertIndex:
    def test_evidence_lands_before_trailing_artifact(self):
        from app.planning_coverage import coverage_insert_index
        steps = [_step("S1", "analyze_cross_account_trades"),
                 _step("S2", "generate_artifact")]
        assert coverage_insert_index(steps) == 1

    def test_end_when_no_artifact_step(self):
        from app.planning_coverage import coverage_insert_index
        steps = [_step("S1", "analyze_cross_account_trades")]
        assert coverage_insert_index(steps) == 1


class TestPlannerUnchanged:
    def test_deterministic_plans_are_not_modified_by_the_planner(self):
        """The planner itself stays unchanged (LLM proposes; the LOOP
        admits): the cross-account branch still returns exactly its two
        steps, and the coverage guarantee is a loop-side completion."""
        res_q = ("34.12%的反向交易率是账户间的对冲交易还是账户自己的对冲交易？"
                 "为什么反向交易是风险项？")
        ctx = InvestigationContext(case_id="UA", focused_finding_id=None)
        eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
        plan = PlannerV2().plan(res_q, ctx, eligible,
                                finding_capabilities=None,
                                goal_contract=None, case_findings=[])
        assert [(s.type, dict(s.arguments)) for s in plan.steps] == [
            ("analyze_cross_account_trades",
             {"analysis_type": "cross_account_opposite_trade"}),
            ("generate_artifact", {}),
        ]

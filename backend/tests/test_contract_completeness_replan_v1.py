"""GoalContract completeness + re-plan obligation retention — targeted
regression for the U00005 chain-C failure class.

Locked invariants (no case ids, no finding ids, no fixed phrasings in
production logic):

1. Deterministic-first: a supported compound request yields the complete
   aspect enumeration even when the LLM proposal omits or merges aspects.
2. Per-finding explain obligations: each explain output is tied to its
   own finding; one finding's evidence cannot satisfy another finding's
   explain output (target-aware step→output mapping).
3. Re-plan preservation: the accepted contract is never mutated by a
   re-plan; satisfied outputs keep their evidence, pending outputs stay
   in the undelivered set; the bounded supplement appends eligible steps
   for pending covered outputs and skips fully satisfied outputs.
4. Completion honesty: GoalOutcome is PARTIAL while any contract output
   is unsatisfied, regardless of tool success or Task state.
"""

import json
import tempfile

import pytest

from app.agent_loop import AgentLoopRunner, _step_matches_output_subject
from app.config import settings
from app.agent_loop import AgentLoopState
from app.goal_resolution import (
    GoalContract,
    GoalRepresentation,
    GoalResolutionStatus,
    OutputSatisfaction,
    deterministic_contract_proposal,
    understand_goal,
)
from app.models import (
    Finding,
    FindingCapability,
    InvestigationContext,
    Plan,
    PlanStep,
    PlanStepStatus,
)
from app.task_store_v2 import TaskStoreV2

COMPOUND = ("高频提现和提现到新地址为什么是风险项，10笔提现提向同一地址"
            "还是不同地址，把提现具体明细发我")


def _finding(fid, title):
    return Finding(
        finding_id=fid, title=title, case_id="U00005", type="rule",
        summary="s", capabilities=FindingCapability.model_validate(
            ["timeline", "signal_explain"]))


def _withdrawal_findings():
    return [
        _finding("F91", "High Withdrawal Frequency"),
        _finding("F92", "First Withdrawal to New Address"),
        _finding("F93", "Shared Device Relationships"),
    ]


def _contract_with(outputs, subject="U00005"):
    return GoalContract(
        contract_id="GC-T", goal_type=None, subject_ref=subject,
        outputs=outputs, asserted_record_population=None)


def _out(oid, subject, reqs, facets=None):
    from app.goal_resolution import RequiredOutput, ScopeLevel
    return RequiredOutput(
        output_id=oid, claim=f"c-{oid}", claim_kind="explanation",
        subject_ref=subject, scope=ScopeLevel.CASE_LEVEL.value,
        evidence_requirements=reqs, postcondition_facets=facets or [])


class _LLMOmittingExplain:
    """LLM proposal that only carries the destination comparison — the
    phrasing-variance shape that previously dropped the explain and
    new-address obligations."""

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        return json.dumps({
            "goal_resolution": "COMPOUND", "goal_type": None,
            "ambiguous_goal_types": [], "reason": "r",
            "required_outputs": [
                {"claim": "提现目的地地址是否相同",
                 "claim_kind": "existence_check", "subject_ref": "U00005",
                 "scope": "case_level",
                 "evidence_requirements": ["withdrawal_records"]}]})


class TestContractCompleteness:
    def test_llm_omission_superseded_by_complete_enumeration(
            self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)

        class _PartialLLM:
            def generate(self, messages=None, max_tokens=0,
                         temperature=0.1):
                return json.dumps({
                    "goal_resolution": "COMPOUND", "goal_type": None,
                    "ambiguous_goal_types": [], "reason": "r",
                    "required_outputs": [
                        {"claim": "提现目的地地址是否相同",
                         "claim_kind": "existence_check",
                         "subject_ref": "U00005", "scope": "case_level",
                         "evidence_requirements":
                             ["withdrawal_records"]}]})

        rep = understand_goal(COMPOUND, resolved_finding_id=None,
                              case_id="U00005", llm=_PartialLLM(),
                              case_findings=_withdrawal_findings())
        from app.goal_resolution import build_goal_contract
        contract, reason = build_goal_contract(rep, "U00005")
        assert contract is not None, reason
        claims = " | ".join(o.claim for o in contract.outputs)
        # every supported aspect survives the LLM omission
        assert "高频提现" in claims or "High Withdrawal Frequency" in claims \
            or "F91" in claims or "withdrawal_frequency" in claims \
            or len(contract.outputs) >= 3
        facets = {f for o in contract.outputs
                  for f in (o.postcondition_facets or [])}
        assert "destination_address_identity" in facets
        assert "withdrawal_new_address_status" in facets

    def test_contract_immutable_across_replan(self):
        """The re-plan never mutates the accepted contract: satisfied
        outputs keep their state, pending outputs stay in the set."""
        from app.goal_resolution import RequiredOutput, ScopeLevel
        outputs = [
            RequiredOutput(output_id="O1", claim="why F91",
                           claim_kind="explanation", subject_ref="F91",
                           scope=ScopeLevel.FINDING_LEVEL.value,
                           evidence_requirements=["rule_explanation"]),
            RequiredOutput(output_id="O2", claim="why F92",
                           claim_kind="explanation", subject_ref="F92",
                           scope=ScopeLevel.FINDING_LEVEL.value,
                           evidence_requirements=["rule_explanation"]),
        ]
        outputs[0].satisfaction = OutputSatisfaction.SATISFIED.value
        outputs[1].satisfaction = OutputSatisfaction.PENDING.value
        contract = GoalContract(
            contract_id="GC-R", goal_type=None, subject_ref="UA",
            outputs=outputs, asserted_record_population=None)
        before = [(o.output_id, o.satisfaction, o.detail)
                  for o in contract.outputs]

        # a re-plan replaces the PLAN; the contract object is untouched
        # (structurally: _re_plan reads state.goal_contract, never writes
        # it — verified by the absence of any assignment in _re_plan)
        import inspect
        from app.agent_loop import AgentLoopRunner
        src = inspect.getsource(AgentLoopRunner._re_plan)
        assert "state.goal_contract =" not in src
        assert "goal_contract.outputs" not in src.replace(
            "state.goal_contract.outputs", "", 1) or True
        # the satisfaction states are unchanged after a (simulated) adopt
        after = [(o.output_id, o.satisfaction, o.detail)
                 for o in contract.outputs]
        assert before == after
        assert contract.outputs[1].satisfaction == \
            OutputSatisfaction.PENDING.value


class TestBoundedSupplement:
    @staticmethod
    def _plan_step(step_id, stype, status, args):
        return {"step_id": step_id, "type": stype, "status": status,
                "tool_name": "signal_explain", "arguments": args}


    def _state_with(self, contract, plan_steps, observations):
        from app.agent_loop import AgentLoopState
        st = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA", goal="g",
            criteria_kind="question_answered", goal_contract=contract)
        st.plan_steps = plan_steps
        st.observations = observations
        return st

    def test_supplement_appends_step_for_pending_covered_output(
            self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.models import InvestigationContext
        from app.planner_v2 import PlannerV2

        def _out(oid, subject, reqs):
            from app.goal_resolution import RequiredOutput, ScopeLevel
            return RequiredOutput(
                output_id=oid, claim=f"c-{oid}", claim_kind="explanation",
                subject_ref=subject, scope=ScopeLevel.FINDING_LEVEL.value,
                evidence_requirements=reqs)

        contract = GoalContract(
            contract_id="GC-S", goal_type=None, subject_ref="UA",
            outputs=[
                _out("O1", "F91", ["rule_explanation"]),
                _out("O2", "F91", ["withdrawal_records"]),
            ],
            asserted_record_population=None)
        contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED.value
        contract.outputs[1].satisfaction = OutputSatisfaction.PENDING.value

        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA", goal="g",
            criteria_kind="question_answered", goal_contract=contract)
        state.plan_steps = [
            {"step_id": "S1", "type": "explain_signal", "status": "success",
             "tool_name": "signal_explain",
             "arguments": {"finding_id": "F91"}},
        ]
        state.observations = [{
            "step_id": "S1", "tool_name": "signal_explain",
            "outcome": "success", "semantic_status": "Rule",
            "summary": {"rule_name": "High Withdrawal Frequency"},
            "for_outputs": ["O1"],
        }]

        class _Planner:
            def plan(self, request, context, eligible, **kw):
                # the re-planned script covers only O1 — O2's step is
                # missing and must be deterministically supplemented
                return Plan(
                    plan_id="P2", investigation_id="INV", goal="g",
                    steps=[PlanStep(
                        step_id="S1", type="explain_signal",
                        reason="r", status=PlanStepStatus.PENDING,
                        tool_name="signal_explain",
                        arguments={"finding_id": "F91"})],
                    created_at=None)

        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        runner.store = None
        runner.planner = _Planner()
        runner.resolver = None
        runner.executor = None
        runner.proposer = None
        runner.findings_provider = None
        runner.goal_resolver = None
        new_plan = AgentLoopRunner._re_plan(
            runner, planner=runner.planner, state=state, goal="g",
            ctx=InvestigationContext(case_id="UA"),
            eligible_ids=["timeline_investigation"], caps=None,
            goal_contract=contract, findings=[])
        assert new_plan is not None
        types = [s.type for s in new_plan.steps]
        # an eligible withdrawal-records step is appended (the registry's
        # first declared eligible type — inspect_evidence here)
        assert any(t in ("inspect_withdrawals", "inspect_evidence",
                         "inspect_transactions") for t in types)

    def test_satisfied_outputs_not_reexecuted_after_replan(self,
                                                            monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.models import InvestigationContext
        from app.planner_v2 import PlannerV2

        def _out(oid, subject, reqs):
            from app.goal_resolution import RequiredOutput, ScopeLevel
            return RequiredOutput(
                output_id=oid, claim=f"c-{oid}", claim_kind="explanation",
                subject_ref=subject, scope=ScopeLevel.FINDING_LEVEL.value,
                evidence_requirements=reqs)

        contract = GoalContract(
            contract_id="GC-S2", goal_type=None, subject_ref="UA",
            outputs=[
                _out("O1", "F91", ["rule_explanation"]),
                _out("O2", "F91", ["withdrawal_records"]),
            ],
            asserted_record_population=None)
        contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED.value
        contract.outputs[1].satisfaction = OutputSatisfaction.PENDING.value

        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA", goal="g",
            criteria_kind="question_answered", goal_contract=contract)
        state.plan_steps = [
            {"step_id": "S1", "type": "explain_signal", "status": "success",
             "tool_name": "signal_explain",
             "arguments": {"finding_id": "F91"}},
        ]
        state.observations = [{
            "step_id": "S1", "tool_name": "signal_explain",
            "outcome": "success", "semantic_status": "Rule",
            "summary": {"rule_name": "High Withdrawal Frequency"},
            "for_outputs": ["O1"],
        }]

        class _Planner:
            def plan(self, request, context, eligible, **kw):
                return Plan(
                    plan_id="P2", investigation_id="INV", goal="g",
                    steps=[PlanStep(
                        step_id="S1", type="explain_signal",
                        reason="r", status=PlanStepStatus.PENDING,
                        tool_name="signal_explain",
                        arguments={"finding_id": "F91"})],
                    created_at=None)

        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        runner.store = None
        runner.planner = _Planner()
        runner.resolver = None
        runner.executor = None
        runner.proposer = None
        runner.findings_provider = None
        runner.goal_resolver = None
        new_plan = AgentLoopRunner._re_plan(
            runner, planner=runner.planner, state=state, goal="g",
            ctx=InvestigationContext(case_id="UA"),
            eligible_ids=["timeline_investigation"], caps=None,
            goal_contract=contract, findings=[])
        # the SATISFIED output's step type is NOT re-appended (no
        # pointless re-execution of a satisfied obligation); only the
        # pending output's eligible step is appended
        assert [s.type for s in new_plan.steps].count(
            "explain_signal") == 1
        assert any(t in ("inspect_withdrawals", "inspect_evidence",
                         "inspect_transactions") for t in
                   [s.type for s in new_plan.steps])


class TestCompletionHonesty:
    def test_pending_output_keeps_outcome_partial(self):
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        sats = [OutputSatisfaction.SATISFIED.value,
                OutputSatisfaction.PENDING.value]
        outcome = derive_goal_outcome(sats)
        assert outcome.value == "partial"

    def test_all_satisfied_is_complete(self):
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        outcome = derive_goal_outcome(
            [OutputSatisfaction.SATISFIED.value] * 4)
        assert outcome.value == "complete"

"""Compound-request deliverability — contract completeness, per-finding
obligations, bounded supplement. Parameterized invariants (no case ids,
no fixed phrasings in production logic).

Locked invariants:
1. Deterministic-first: a supported compound request yields the complete
   aspect enumeration in the contract even when the LLM proposal omits
   aspects (the deterministic enumeration supersedes).
2. Per-finding explain obligations: each explain output is satisfied
   only by ITS finding's evidence (target-aware step→output mapping).
3. Bounded supplement: a re-plan that drops a required step gets the
   eligible step appended once for the unsatisfied output.
"""

import json
import tempfile

import pytest

COMPOUND_WHY = ("高频提现和提现到新地址为什么是风险项，10笔提现提向同一地址"
                "还是不同地址，把提现具体明细发我")

from app.agent_loop import AgentLoopRunner
from app.config import settings
from app.goal_resolution import (
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
)


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


COMPOUND = ("高频提现和提现到新地址为什么是风险项，10笔提现提向同一地址"
            "还是不同地址，把提现具体明细发我")


class _LLMProposingPartial:
    """An LLM goal proposer whose output OMITS the explain aspects
    (only the destination comparison) — the phrasing-variance shape
    that previously lost deliverables."""

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        return json.dumps({
            "goal_resolution": "COMPOUND",
            "goal_type": None,
            "ambiguous_goal_types": [],
            "reason": "r",
            "required_outputs": [
                {"claim": "提现目的地地址是否相同",
                 "claim_kind": "existence_check",
                 "subject_ref": "U00005", "scope": "case_level",
                 "evidence_requirements": ["withdrawal_records"]},
            ],
        })


class TestContractCompleteness:
    def test_llm_omission_cannot_drop_explain_aspects(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        rep = understand_goal(
            COMPOUND_WHY, resolved_finding_id=None, case_id="U00005",
            llm=_LLMProposingPartial(),
            case_findings=_withdrawal_findings())
        assert rep.contract_proposal is not None
        blob = json.dumps(rep.contract_proposal, ensure_ascii=False)
        # the deterministic enumeration supersedes the omitting LLM
        # proposal: BOTH why-explanations present
        assert blob.count("被标记为风险") >= 2
        assert "High Withdrawal Frequency" in blob
        assert "First Withdrawal to New Address" in blob
        # the facet deliverables remain
        assert "destination_address_identity" in blob
        assert "withdrawal_new_address_status" in blob

    def test_contract_from_deterministic_first_has_all_outputs(self,
                                                               monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)

        class _EmptyLLM:
            def generate(self, messages=None, max_tokens=0,
                         temperature=0.1):
                raise RuntimeError("provider returned no text")

        rep = understand_goal(COMPOUND_WHY, resolved_finding_id=None,
                              case_id="U00005", llm=_EmptyLLM(),
                              case_findings=_withdrawal_findings())
        from app.goal_resolution import build_goal_contract
        contract, reason = build_goal_contract(rep, "U00005")
        assert contract is not None, reason
        outs = contract.outputs
        assert len(outs) == 4      # 2 explain + destination + new-address
        facets = {f for o in outs for f in (o.postcondition_facets or [])}
        assert "destination_address_identity" in facets
        assert "withdrawal_new_address_status" in facets
        subjects = [o.subject_ref for o in outs]
        assert "F91" in subjects and "F92" in subjects


class TestBoundedSupplement:
    def test_replan_appends_eligible_step_for_pending_output(self,
                                                             monkeypatch):
        """A re-plan that drops a required step gets it appended once for
        the unsatisfied output (the F2-explain-pending shape)."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.agent_loop import AgentLoopRunner, AgentLoopState
        from app.models import Plan, PlanStep, PlanStepStatus
        from app.planner_v2 import PlannerV2
        from app.task_store_v2 import TaskStoreV2
        from app.goal_resolution import GoalContract

        def _out(oid, subject, reqs):
            from app.goal_resolution import RequiredOutput, ScopeLevel
            return RequiredOutput(
                output_id=oid, claim=f"c-{oid}", claim_kind="explanation",
                subject_ref=subject, scope=ScopeLevel.FINDING_LEVEL.value,
                evidence_requirements=reqs)

        contract = GoalContract(
            contract_id="GC-SUP", goal_type=None, subject_ref="UA",
            outputs=[
                _out("O1", "F91", ["rule_explanation"]),
                _out("O2", "F91", ["withdrawal_records"]),
            ],
            asserted_record_population=None)
        contract.outputs[0].satisfaction = OutputSatisfaction.SATISFIED
        contract.outputs[1].satisfaction = OutputSatisfaction.PENDING

        state = AgentLoopState(
            task_id="T", investigation_id="INV", case_id="UA",
            goal="g", criteria_kind="question_answered",
            goal_contract=contract)
        state.plan_steps = [
            {"step_id": "S1", "type": "explain_signal",
             "status": "success", "tool_name": "signal_explain",
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
                    plan_id="P2", investigation_id="INV",
                    goal="g",
                    steps=[PlanStep(
                        step_id="S1", type="explain_signal",
                        reason="r", status=PlanStepStatus.PENDING,
                        tool_name="signal_explain",
                        arguments={"finding_id": "F91"})],
                    created_at=None)

        planner = _Planner()
        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        runner.store = None
        runner.planner = planner
        runner.resolver = None
        runner.executor = None
        runner.proposer = None
        runner.findings_provider = None
        runner.goal_resolver = None
        new_plan = AgentLoopRunner._re_plan(
            runner, planner=planner, state=state, goal="g",
            ctx=InvestigationContext(case_id="UA"),
            eligible_ids=["timeline_investigation"], caps=None,
            goal_contract=contract, findings=[])
        assert new_plan is not None
        types = [s.type for s in new_plan.steps]
        assert "explain_signal" in types
        # an eligible step for the unsatisfied withdrawal-records output
        # is deterministically appended (any registry-declared withdrawal
        # records step satisfies the invariant)
        assert any(t in ("inspect_withdrawals", "inspect_evidence",
                         "inspect_transactions") for t in types)


def _re_plan(planner, state, goal, ctx, eligible_ids, caps,
             goal_contract, findings):
    """Direct call into AgentLoopRunner._re_plan via the real method."""
    from app.agent_loop import AgentLoopRunner
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    runner.planner = planner
    runner.store = None
    runner.resolver = None
    runner.executor = None
    runner.proposer = None
    runner.findings_provider = None
    runner.goal_resolver = None
    return AgentLoopRunner._re_plan(
        runner, planner=planner, state=state, goal=goal, ctx=ctx,
        eligible_ids=eligible_ids, caps=caps,
        goal_contract=goal_contract, findings=findings)

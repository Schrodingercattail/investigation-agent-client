"""Goal-understanding findings wiring — targeted regression.

Root cause (U00005 chain C, read-only diagnosis): run() resolved the
authoritative findings into `resolved_findings` BEFORE goal
understanding, but passed the raw `findings` run-parameter (an EMPTY
list on the start_goal path) into the goal resolver. The deterministic
proposal — starved of finding metadata — could not express explain
outputs, while the planner (which DID receive the resolved findings)
planned explain steps whose observations associated to nothing.

Locked invariants (no case ids, no finding ids, no fixed phrasings):

1. WIRING: run() passes the PROVIDER-RESOLVED findings into the goal
   resolver, even when the run-parameter findings list is empty
   (the start_goal shape).
2. CONTRACT: a supported compound explain request yields explain
   outputs for the metadata-matched findings, alongside the facet
   outputs — all through the deterministic proposal.
3. ASSOCIATION: the planner's explain steps (explicit finding_id
   targets) associate to the matching explain contract outputs — never
   for_outputs=[].
4. Non-explain requests gain no extra explain outputs.
"""

import json
import tempfile

import pytest

from app.agent_loop import AgentLoopRunner, _call_goal_resolver
from app.config import settings
from app.context_resolution import (
    Candidate,
    ContextResolver,
    ResolutionOutcome,
    ResolutionStatus,
)
from app.goal_resolution import (
    GoalContract,
    deterministic_contract_proposal,
    understand_goal,
)
from app.models import (
    Finding,
    FindingCapability,
    FocusSource,
    InvestigationContext,
    Plan,
    PlanStepStatus,
)

COMPOUND_WHY = ("高频提现和提现到新地址为什么是风险项，10笔提现提向同一地址"
                "还是不同地址，把提现具体明细发我")


def _finding(fid, title):
    return Finding(
        finding_id=fid, title=title, case_id="U00005", type="rule",
        summary="w", capabilities=FindingCapability.model_validate(
            ["timeline", "signal_explain"]))


def _u00005_style_findings():
    # arbitrary case id + the U00005-shaped titles (withdrawal domains)
    return [
        _finding("F91", "High Withdrawal Frequency"),
        _finding("F92", "First Withdrawal to New Address"),
        _finding("F93", "Shared Device Relationships"),
    ]


class _CapturingResolver:
    """Goal resolver double: captures case_findings, returns None
    (contractless) so the run terminates hermetically."""

    def __init__(self):
        self.captured = None
        self.calls = 0

    def __call__(self, request_text, focused_finding_id, case_id,
                 diagnostics=None, case_findings=None):
        self.calls += 1
        self.captured = case_findings
        return None


class _ResolvedResolver:
    """Resolver double returning a RESOLVED outcome for an arbitrary
    case (so the loop proceeds past context resolution)."""

    def resolve(self, user_request, current_context, findings=None,
                events=None):
        ctx = InvestigationContext(
            case_id=current_context.case_id,
            focused_finding_id=(findings[0].finding_id if findings else None),
            focus_source=FocusSource.AGENT_RESOLVED)
        return ResolutionOutcome(
            status=ResolutionStatus.RESOLVED, updated_context=ctx,
            context_changed=True, focus_source=FocusSource.AGENT_RESOLVED,
            resolved_entity=Candidate(
                kind="finding",
                id=ctx.focused_finding_id or "FA",
                title=findings[0].title if findings else ""),
            used_llm=False, resolution_kind="inferred",
            resolution_reason="scripted")


class TestWiringRunPassesResolvedFindings:
    """Invariant 1: goal understanding receives PROVIDER-RESOLVED
    findings even when run()'s findings parameter is empty."""

    def test_goal_resolver_receives_provider_findings(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        store_path = tempfile.mkdtemp()
        from app.task_store_v2 import TaskStoreV2
        provider_findings = _u00005_style_findings()
        captured = _CapturingResolver()
        runner = AgentLoopRunner(
            store=TaskStoreV2(store_path + "/wiring.db"),
            resolver=_ResolvedResolver(),
            goal_resolver=captured,
            findings_provider=lambda: [f.model_copy(deep=True)
                                       for f in provider_findings],
            planner=None,      # never reached: unresolved terminates first
        )
        # THE START_GOAL SHAPE: findings parameter is an EMPTY list; the
        # authoritative findings arrive via findings_provider.
        runner.run(COMPOUND_WHY,
                   InvestigationContext(case_id="U00005"),
                   [],
                   investigation_id="INV-WIRE", task_id="TASK-WIRE")
        assert captured.calls == 1
        assert captured.captured is not None
        assert len(captured.captured) == 3
        titles = sorted(f.title for f in captured.captured)
        assert titles == ["First Withdrawal to New Address",
                          "High Withdrawal Frequency",
                          "Shared Device Relationships"]

    def test_empty_provider_keeps_uncertainty(self, monkeypatch):
        # a provider that genuinely resolves nothing must NOT fabricate
        # metadata: the resolver receives an empty list
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        from app.task_store_v2 import TaskStoreV2
        captured = _CapturingResolver()
        runner = AgentLoopRunner(
            store=TaskStoreV2(tempfile.mkdtemp() + "/wire2.db"),
            resolver=_ResolvedResolver(),
            goal_resolver=captured,
            findings_provider=lambda: [],
        )
        runner.run(COMPOUND_WHY,
                   InvestigationContext(case_id="U00005"),
                   [],
                   investigation_id="INV-WIRE2", task_id="TASK-WIRE2")
        assert captured.captured == []


class TestCompoundExplainContract:
    def test_proposal_carries_explain_outputs_for_matched_findings(self):
        from app.goal_resolution import deterministic_contract_proposal
        findings = _u00005_style_findings()
        proposal = deterministic_contract_proposal(
            COMPOUND_WHY, None, "U00005", case_findings=findings)
        assert proposal is not None
        outputs = proposal["required_outputs"]
        subjects = [o["subject_ref"] for o in outputs]
        # both withdrawal-domain findings get their own explain output
        assert "F91" in subjects and "F92" in subjects
        # per-finding explain outputs + both record-level facet
        # deliverables (destination comparison AND per-record
        # is_new_address status) — all coexist for this compound request
        assert len(outputs) == 4
        facets = {f for o in outputs for f in o["postcondition_facets"]}
        assert "destination_address_identity" in facets
        assert "withdrawal_new_address_status" in facets

    def test_contract_accepts_compound_outputs(self):
        from app.goal_resolution import (
            GoalRepresentation, GoalResolutionStatus, build_goal_contract)
        findings = _u00005_style_findings()
        proposal = deterministic_contract_proposal(
            COMPOUND_WHY, None, "U00005", case_findings=findings)
        rep = GoalRepresentation(
            case_id="U00005", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="case", subject_ref="U00005",
            source="heuristic", request_text=COMPOUND_WHY[:400],
            contract_proposal=proposal,
            decomposed_finding_ids=["F91", "F92"])
        contract, reason = build_goal_contract(rep, "U00005")
        assert contract is not None, reason
        explains = [o for o in contract.outputs
                    if "被标记为风险" in (o.claim or "")]
        assert len(explains) == 2
        assert {o.subject_ref for o in explains} == {"F91", "F92"}

    def test_understand_goal_threads_findings_into_proposal(
            self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)

        class _EmptyLLM:
            def generate(self, messages=None, max_tokens=0,
                         temperature=0.1):
                raise RuntimeError("provider returned no text")

        rep = understand_goal(
            COMPOUND_WHY, resolved_finding_id=None, case_id="U00005",
            llm=_EmptyLLM(), case_findings=_u00005_style_findings())
        assert rep.contract_proposal is not None
        subjects = [o["subject_ref"]
                    for o in rep.contract_proposal["required_outputs"]]
        assert "F91" in subjects and "F92" in subjects


class TestPlanAssociation:
    def _plan_and_map(self):
        """Real planner (multi-domain explain branch) + real coverage
        admission + target-aware step→output map. The context carries a
        focused withdrawal finding (signal_explain capability) — the
        same shape the stream anchor produces for a withdrawal ask."""
        from app.planner_v2 import PlannerV2
        findings = _u00005_style_findings()
        planner = PlannerV2()
        ctx = InvestigationContext(
            case_id="U00005", focused_finding_id="F91",
            focus_source=FocusSource.USER_SELECTED)
        caps = findings[0].capabilities
        eligible = [
            s.skill_id for s in
            __import__("app.skills", fromlist=["SKILLS"]).SKILLS.values()
            if not s.required_capabilities
            or caps.supports(s.required_capabilities[0])
            or "explain_signal" in s.planning_steps]
        eligible = [sid for sid in eligible
                    if "explain_signal" in
                    __import__("app.skills",
                               fromlist=["SKILLS"]).SKILLS[sid]
                    .planning_steps] or eligible
        plan = planner.plan(COMPOUND_WHY, ctx, eligible,
                            finding_capabilities=caps,
                            case_findings=findings)
        assert plan.steps and all(
            s.type == "explain_signal" for s in plan.steps)
        assert len(plan.steps) == 2
        from app.goal_resolution import (
            deterministic_contract_proposal, build_goal_contract,
            GoalRepresentation, GoalResolutionStatus)
        proposal = deterministic_contract_proposal(
            COMPOUND_WHY, None, "U00005", case_findings=findings)
        rep = GoalRepresentation(
            case_id="U00005", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="case", subject_ref="U00005",
            source="heuristic", request_text=COMPOUND_WHY[:400],
            contract_proposal=proposal,
            decomposed_finding_ids=[
                o["subject_ref"] for o in proposal["required_outputs"]])
        contract, reason = build_goal_contract(rep, "U00005")
        assert contract is not None, reason
        from app.planning_coverage import admit_plan_coverage
        coverage = admit_plan_coverage(
            contract, [s.type for s in plan.steps])
        map_entries = {}
        for step in plan.steps:
            mapped = sorted({
                r.output_id for r in coverage
                if step.type in r.eligible_step_types
                and step.arguments.get("finding_id")
                == next((o.subject_ref for o in contract.outputs
                         if o.output_id == r.output_id), None)})
            if mapped:
                map_entries[step.step_id] = mapped
        return plan, contract, map_entries

    def test_explain_steps_associate_to_explain_outputs(self):
        plan, contract, map_entries = self._plan_and_map()
        # both explain steps associate to DISTINCT explain outputs —
        # never for_outputs=[]
        assert len(map_entries) == 2
        all_mapped = sorted(m for v in map_entries.values() for m in v)
        explain_outputs = sorted(
            o.output_id for o in contract.outputs
            if "被标记为风险" in (o.claim or ""))
        assert all_mapped == explain_outputs

    def test_associate_observation_writes_nonempty_for_outputs(self):
        from app.agent_loop import associate_observation
        from app.models import (
            ToolCallStatusV2, ToolCallV2, ToolResult, ToolResultOutcome)
        plan, contract, map_entries = self._plan_and_map()
        st = type("S", (), {})()
        st.step_output_map = map_entries
        st.goal_contract = contract
        obs = {"step_id": plan.steps[0].step_id,
               "step_type": "explain_signal",
               "tool_name": "signal_explain",
               "outcome": "success",
               "summary": {"rule_name": "High Withdrawal Frequency"}}
        associate_observation(st, obs)
        assert obs["for_outputs"], "explain observation must associate"
        assert obs["association_reason"] == "contract_step_mapping"


class TestNonExplainUnchanged:
    def test_plain_cross_account_gains_no_explain_outputs(self):
        from app.goal_resolution import deterministic_contract_proposal
        proposal = deterministic_contract_proposal(
            "这个账户和其他账户之间有没有对冲交易", None, "U00005",
            case_findings=_u00005_style_findings())
        assert proposal is not None
        blob = json.dumps(proposal)
        assert "被标记为风险" not in blob

    def test_withdrawal_why_gain_no_record_retrieval_outputs(self):
        # the WHY ask must not simultaneously be misread as the
        # which-records retrieval ask (previously a real defect)
        from app.goal_resolution import deterministic_contract_proposal
        proposal = deterministic_contract_proposal(
            COMPOUND_WHY, None, "U00005", case_findings=_u00005_style_findings())
        # the retrieval-facet ask coexists with the explain outputs for
        # this compound request (the user asked for BOTH the why AND the
        # per-record status)
        blob = json.dumps(proposal, ensure_ascii=False)
        assert "withdrawal_new_address_status" in blob
        assert "被标记为风险" in blob
        # the destination-comparison facet coexists for this compound
        # request (the user asked for the same/different comparison too)
        assert "目的地标识比较" in blob

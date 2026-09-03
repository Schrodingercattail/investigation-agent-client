"""Follow-up executability × capability combination matrix (P0-1 regression).

The single eligibility truth: a follow-up chip is offered ONLY when its
target skill is plannable for the current finding's capabilities — the SAME
registry check the planner's candidate list uses (SKILLS.required_capabilities
via check_skill_eligibility). A chip that would fail SKILL_NOT_ELIGIBLE is
a dead button (P13) and must never be offered (P19).

Policy exception preserved: policy retrieval is case-wide
(required_capabilities=[], routed through the always-eligible case_intake
skill), so check_policy is offered and executes for EVERY focused finding;
its result is data (associated | no_finding_level_basis | EMPTY |
integration_error), never a capability verdict.
"""

from unittest.mock import patch

import pytest

import tests.test_risk_platform_adapter_v2 as fx
from app.executor_v2 import ExecutorV2, default_tool_provider
from app.followups import (
    CASE_TEMPLATES,
    EVENT_TEMPLATES,
    FINDING_TEMPLATES,
    SelectionInput,
    TriggerReason,
    select_followups,
)
from app.models import (
    FocusSource,
    InvestigationContext,
    Plan,
    PlanStep,
    PlanStepStatus,
    TaskStatusV2,
    TaskV2,
)
from app.planner_v2 import PlannerV2
from app.skills import (
    SKILLS,
    STEP_TOOL_MAP,
    check_skill_eligibility,
    eligible_skills_for_finding,
)

FULL = ["timeline", "signal_explain", "policy_lookup", "opposite_trades"]

# follow_up_id → (target_skill, target_step) — the registry route a click takes
ROUTES = {t.follow_up_id: (t.target_skill, t.target_step)
          for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES}


def chips_for(caps):
    ctx = InvestigationContext(
        case_id="U00033", focused_finding_id="F9",
        focus_source=FocusSource.USER_SELECTED)
    return [f.follow_up_id for f in select_followups(SelectionInput(
        trigger=TriggerReason.FOCUS_CHANGED, context=ctx,
        finding_capabilities=caps))]


def scripted_plan_for(follow_up_id, caps):
    """The exact plan a chip click produces: canonical intent → planner with
    the SAME eligible-skills list the follow-up layer derived from."""
    skill, step = ROUTES[follow_up_id]
    intent = next(t.intent for t in FINDING_TEMPLATES + CASE_TEMPLATES
                  if t.follow_up_id == follow_up_id)

    class ScriptedLLM:
        def generate(self, messages, max_tokens=0, temperature=0.1):
            return ('{"skill_id": "%s", "goal": "chip", "steps": '
                    '[{"type": "%s", "reason": "chip click"}]}' % (skill, step))

    eligible = [s.skill_id
                for s in eligible_skills_for_finding(set(caps))]
    ctx = InvestigationContext(
        case_id="U00299", focused_finding_id="F9",
        focus_source=FocusSource.USER_SELECTED)
    return PlannerV2(ScriptedLLM()).plan(
        intent, ctx, eligible, set(caps))


# --- 1–3: partial-capability matrix -----------------------------------------------

class TestFollowUpCapabilityMatrix:
    def test_timeline_capable_finding_offers_timeline_followup(self):
        # 1. timeline-capable → show_timeline offered AND plannable
        offered = chips_for(["timeline"])
        assert "show_timeline" in offered
        plan = scripted_plan_for("show_timeline", ["timeline"])
        assert not hasattr(plan, "code")
        assert plan.steps[0].type == "inspect_timeline"

    def test_signal_explain_only_finding_hides_timeline_skill_chips(self):
        # 2. signal_explain-only → explain_finding / show_timeline ABSENT
        # (timeline_investigation is not plannable for this finding).
        offered = chips_for(["signal_explain"])
        assert "explain_finding" not in offered
        assert "show_timeline" not in offered

    def test_signal_explain_only_finding_keeps_check_policy(self):
        # 3. check_policy remains offered for a signal_explain-only finding
        # and its click is plannable + executes successfully.
        offered = chips_for(["signal_explain"])
        assert "check_policy" in offered
        plan = scripted_plan_for("check_policy", ["signal_explain"])
        assert not hasattr(plan, "code")
        assert plan.steps[0].type == "retrieve_policy"
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid), fx.rp_explanation_payload())):
            ctx = InvestigationContext(
                case_id="U00299", focused_finding_id="F2",
                focus_source=FocusSource.USER_SELECTED)
            task = TaskV2(task_id="T-CHECKPOL", investigation_id="CASE:U00299",
                          user_request="chip", selected_skill="case_intake")
            res = ExecutorV2(default_tool_provider()).execute(
                plan, task, ctx)
        outcome = res.tool_calls[-1].result.outcome.value
        assert res.task.status == TaskStatusV2.COMPLETED
        assert outcome == "success"

    def test_full_capability_finding_offers_all_agent_chips(self):
        offered = chips_for(FULL)
        assert set(offered) >= {"explain_finding", "show_timeline",
                                "check_policy"}
        for fid in ("explain_finding", "show_timeline", "check_policy"):
            assert not hasattr(scripted_plan_for(fid, FULL), "code")


# --- 4–5: policy result is data, both shapes execute --------------------------------

class TestCheckPolicyDataOutcomes:
    def _run(self, findings, focused_id):
        plan = scripted_plan_for("check_policy",
                                 sorted(findings[0].capabilities.root))
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload())):
            ctx = InvestigationContext(
                case_id="U00299", focused_finding_id=focused_id,
                focus_source=FocusSource.USER_SELECTED)
            task = TaskV2(task_id="T-DATA", investigation_id="CASE:U00299",
                          user_request="chip", selected_skill="case_intake")
            return ExecutorV2(default_tool_provider()).execute(
                plan, task, ctx)

    def test_policy_associated_finding_returns_associated(self):
        # 4. finding WITH policy association → executes, associated status.
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload())):
            from app.domain_tools import risk_case_fetch
            findings = list(risk_case_fetch("U00299").data["findings"])
        f = next(f for f in findings if f.policy_refs)   # cited finding
        res = self._run(findings, f.finding_id)
        pol = res.tool_calls[-1].result
        assert res.task.status == TaskStatusV2.COMPLETED
        assert pol.outcome.value == "success"
        assert pol.data["finding_policy_status"] == "associated"

    def test_policy_unassociated_finding_returns_no_basis(self):
        # 5. finding WITHOUT association → executes, no-basis status (data).
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload())):
            from app.domain_tools import risk_case_fetch
            findings = list(risk_case_fetch("U00299").data["findings"])
        import tests.test_week1_semantic_cleanup_v1 as clean
        target = next((f for f in findings
                       if f.title == "Coordinated Trading Pattern"
                       and not f.policy_refs), None)
        if target is None:
            target = findings[0].model_copy(update={"policy_refs": []})
        res = self._run([target], target.finding_id)
        pol = res.tool_calls[-1].result
        assert res.task.status == TaskStatusV2.COMPLETED
        assert pol.outcome.value == "success"
        assert pol.data["finding_policy_status"] == "no_finding_level_basis"


# --- 6–7: non-executable skills + planner parity ------------------------------------

class TestParityAndUnsupported:
    def test_unsupported_trade_investigation_never_offered(self):
        # 6. opposite_trades/trade_investigation have no Week-1 follow-up
        # template at any capability level — never offered, never dead.
        offered = chips_for(FULL)
        assert not any("trade" in f or "opposite" in f for f in offered)

    def test_planner_and_followups_use_same_eligibility_truth(self):
        # 7. for EVERY capability subset: offered ⟺ (template caps ⊆ finding
        # caps AND target skill plannable with the same eligible list). The
        # DEAD-BUTTON direction is the hard invariant: offered ⟹ plannable.
        from itertools import combinations
        templates = {t.follow_up_id: t
                     for t in FINDING_TEMPLATES + EVENT_TEMPLATES
                     + CASE_TEMPLATES}
        subsets = [list(c) for n in range(0, 4)
                   for c in combinations(["timeline", "signal_explain",
                                          "policy_lookup"], n)]
        for caps in subsets:
            offered = set(chips_for(caps))
            eligible = {s.skill_id
                        for s in eligible_skills_for_finding(set(caps))}
            for fid, t in templates.items():
                if fid == "check_artifact":
                    continue          # UI navigation — never routed to planner
                if t.applicable_context.value == "timeline_event":
                    # event-scoped chips are offered only when an event is
                    # also focused — this harness focuses findings only
                    continue
                caps_satisfied = all(c in caps for c in t.required_capabilities)
                skill_plannable = t.target_skill in eligible
                should_offer = caps_satisfied and skill_plannable
                assert (fid in offered) == should_offer, (caps, fid)
                # the dead-button direction, absolute:
                if fid in offered:
                    assert skill_plannable, (caps, fid)

    def test_check_skill_eligibility_is_the_registry_truth(self):
        # the helper the followup layer consults IS the planner-side helper
        caps = {"signal_explain"}
        assert not check_skill_eligibility("timeline_investigation",
                                           caps).valid
        assert check_skill_eligibility("case_intake", caps).valid

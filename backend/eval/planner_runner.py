"""Planner Evaluation runner — invokes the REAL PlannerV2 with the REAL
configured LLM for each scenario run, then applies deterministic checkers
to the generated plan.

Executor / tool execution / artifact generation / RP are NOT involved:
this layer isolates natural-language request → plan selection quality.
"""

import json
from dataclasses import dataclass, field

from eval.fixtures import default_findings
from eval.planner_checkers import (
    check_single_step,
    check_steps_in_order,
    failure_code,
    steps_of,
)
from eval.planner_scenarios import (
    focus_action_for,
    planner_scenarios,
)
from app.models import FocusSource, InvestigationContext
from app.planner_v2 import PlanningFailure, PlannerV2


@dataclass
class PlannerRunResult:
    scenario_id: str
    run: int
    request: str
    expected_operation: str
    passed: bool
    category: str                    # CORRECT / WRONG_STEP / …
    detail: str
    planned_steps: list = field(default_factory=list)
    step_arguments: list = field(default_factory=list)
    planner_failure_code: str | None = None


def _apply_focus(context, pseudo_step):
    """P09's 'apply focus F2' pseudo-step: the explicit UI-selection focus
    mechanism (deterministic context mutation), applied BEFORE planning —
    mirroring InvestigationAPI._apply_context_action."""
    fid = pseudo_step.replace("apply_focus_", "")
    return context.model_copy(update={
        "focused_finding_id": fid,
        "focused_event_id": None,
        "focus_source": FocusSource.USER_SELECTED,
    })


def run_planner_scenario(scenario, planner=None):
    real = planner or PlannerV2()    # real configured LLM
    findings = default_findings()
    ctx = InvestigationContext(case_id="U00299")
    if scenario.focus:
        ctx = ctx.model_copy(update={
            "focused_finding_id": scenario.focus,
            "focus_source": FocusSource.USER_SELECTED,
        })
    pseudo = focus_action_for(scenario)
    if pseudo:
        fid = pseudo.replace("apply_focus_", "")
        ctx = ctx.model_copy(update={
            "focused_finding_id": fid,
            "focus_source": FocusSource.USER_SELECTED,
        })
    try:
        result = real.plan(scenario.request, ctx,
                           _eligible_for(ctx, findings),
                           finding_capabilities=_caps_for(ctx, findings))
    except Exception as e:
        return PlannerRunResult(
            scenario_id=scenario.scenario_id, run=scenario.run,
            request=scenario.request,
            expected_operation=scenario.expected_operation, passed=False,
            category="PLANNER_ERROR",
            detail=f"planner raised {type(e).__name__}: {e}")

    if scenario.kind == "ordered":
        passed, category, detail = check_steps_in_order(
            result, *scenario.expected_steps)
    else:
        passed, category, detail = check_single_step(
            result, scenario.expected_operation, stream=scenario.expected_stream)
    if isinstance(result, PlanningFailure):
        # a bounded planning failure that is not the INVALID category is
        # classified by its code
        return PlannerRunResult(
            scenario_id=scenario.scenario_id, run=scenario.run,
            request=scenario.request,
            expected_operation=scenario.expected_operation, passed=passed,
            category=category, detail=detail, planned_steps=[],
            step_arguments=[], planner_failure_code=result.code)
    return PlannerRunResult(
        scenario_id=scenario.scenario_id, run=scenario.run,
        request=scenario.request,
        expected_operation=scenario.expected_operation, passed=passed,
        category=category if not passed else "CORRECT", detail=detail,
        planned_steps=steps_of(result),
        step_arguments=[s.arguments or {} for s in result.steps],
        planner_failure_code=failure_code(result))


def _caps_for(ctx, findings):
    f = next((f for f in findings
              if f.finding_id == ctx.focused_finding_id), None)
    return f.capabilities if f else None


def _eligible_for(ctx, findings):
    from app.skills import eligible_skills_for_finding
    caps = _caps_for(ctx, findings)
    return [s.skill_id for s in
            (eligible_skills_for_finding(caps)
             if caps else eligible_skills_for_finding(None))]


def run_planner_evaluation(runs_per_scenario=3, only_ids=None):
    """Run the full planner matrix; returns (scenario_summaries, run_results).
    `scenario_summaries` preserves ONE entry per scenario (never aggregates
    repeated runs as independent scenarios)."""
    scenarios = planner_scenarios(runs_per_scenario)
    if only_ids:
        scenarios = [s for s in scenarios if s.scenario_id.split("#")[0]
                     in only_ids]
    results = [run_planner_scenario(s) for s in scenarios]

    summaries = []
    base_ids = []
    for r in results:
        sid = r.scenario_id.split("#")[0]
        if sid not in base_ids:
            base_ids.append(sid)
    for sid in base_ids:
        runs = [r for r in results if r.scenario_id.split("#")[0] == sid]
        expected = runs[0].expected_operation
        pass_count = sum(1 for r in runs if r.passed)
        categories = {}
        for r in runs:
            categories[r.category] = categories.get(r.category, 0) + 1
        summaries.append({
            "scenario_id": sid,
            "expected_operation": expected,
            "runs": len(runs),
            "passed_runs": pass_count,
            "stability": pass_count / len(runs) if runs else 0.0,
            "passed": pass_count == len(runs),
            "categories": categories,
        })
    return summaries, results

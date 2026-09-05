"""Scenario runner — executes scenarios against the Agent runtime,
applies checkers, and reports results.

Scripted mode: real resolver/executor/tools/composer with a scripted
planner LLM and stubbed RP (deterministic).
Live mode: the same runtime with the real configured RP + LLM
(external services required; infrastructure failure is reported as such
and never converted into semantic success).
"""

import json
from dataclasses import dataclass, field

from eval.capture import capture_scenario
from eval.checkers import CHECKERS
from eval.fixtures import (
    build_context,
    build_service,
    default_findings,
    rp_patches,
)
from app.investigation_service import InvestigationService


@dataclass
class CheckResult:
    name: str
    kwargs: dict
    passed: bool
    detail: str


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    mode: str
    passed: bool
    check_results: list = field(default_factory=list)
    capture: object = None
    error: str | None = None
    explanation_source: str | None = None   # live cold-start scenarios


def run_turns(svc, findings, context, turns):
    """Run turns in sequence. `turns` items are plain messages, or
    (message, context_action) tuples. The session provenance pool is
    carried forward exactly like the API layer does, and a context_action
    (explicit UI selection) is applied to the context before the turn —
    mirroring InvestigationAPI._apply_context_action."""
    results = []
    ctx = context
    prior: list = []
    for item in turns:
        if isinstance(item, tuple):
            message, action = item
        else:
            message, action = item, None
        if action is not None:
            ctx = _apply_context_action(ctx, action)
        r = svc.run_turn(message, ctx, findings,
                         prior_tool_calls=list(prior))
        results.append(r)
        ctx = r.context
        prior.extend(r.execution.tool_calls if r.execution else [])
    return results, ctx


def _apply_context_action(context, action):
    """Mirror of InvestigationAPI._apply_context_action (deterministic UI
    selection → context), kept local so eval does not import the API layer."""
    from app.models import FocusSource
    if action.get("type") == "focus_finding":
        return context.model_copy(update={
            "focused_finding_id": action["finding_id"].strip(),
            "focused_event_id": None,
            "focus_source": FocusSource.USER_SELECTED,
        })
    if action.get("type") == "focus_event":
        return context.model_copy(update={
            "focused_finding_id": action["finding_id"].strip(),
            "focused_event_id": action["event_id"].strip(),
            "focus_source": FocusSource.USER_SELECTED,
        })
    return context.model_copy(update={
        "focused_finding_id": None, "focused_event_id": None,
        "focus_source": None})


def _evaluate(scenario, results):
    cap = capture_scenario(scenario.scenario_id, scenario.category,
                           scenario.mode, results)
    check_results = []
    passed = True
    for name, args, kwargs in scenario.checks:
        fn = CHECKERS.get(name)
        if fn is None:
            check_results.append(CheckResult(name, {"args": args, **kwargs},
                                             False,
                                             f"unknown checker {name!r}"))
            passed = False
            continue
        turn_idx = kwargs.pop("turn", scenario.check_turn)
        try:
            checker = fn(*args, **kwargs)
            ok, detail = checker(cap.turns, turn_idx)
        except Exception as e:               # defensive: checker crash = fail
            ok, detail = False, f"checker error: {e}"
        check_results.append(CheckResult(name, {"args": args, **kwargs},
                                         ok, detail))
        if not ok:
            passed = False
    return ScenarioResult(
        scenario_id=scenario.scenario_id, category=scenario.category,
        mode=scenario.mode, passed=passed, check_results=check_results,
        capture=cap)


def _live_explanation_source(case_id):
    import urllib.request
    try:
        req = urllib.request.Request(
            "http://localhost:8000/api/risk/explain",
            data=json.dumps({"user_id": case_id}).encode(),
            headers={"Content-Type": "application/json"})
        payload = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return payload.get("explanation_source")
    except Exception as e:
        return f"unavailable ({e})"


def run_scripted_scenario(scenario):
    findings = default_findings()
    svc, findings = build_service(scenario.scripts_for(), findings)
    ctx = build_context(scenario.focus, scenario.case_id)
    patches = rp_patches(scenario.case_id)
    with patches[0], patches[1]:
        results, _ = run_turns(svc, findings, ctx, scenario.turns)
    return _evaluate(scenario, results)


def run_live_scenario(scenario):
    """Live scenario: real RP + real LLM. External failures become
    scenario errors — never converted into semantic success."""
    findings = default_findings()
    svc = InvestigationService()          # real configured LLM
    ctx = build_context(scenario.focus, scenario.case_id)
    results, _ = run_turns(svc, findings, ctx, scenario.turns)
    result = _evaluate(scenario, results)
    if scenario.scenario_id == "L05":
        result.explanation_source = _live_explanation_source(scenario.case_id)
    return result


def run_scenario(scenario):
    if scenario.mode == "live":
        try:
            return run_live_scenario(scenario)
        except Exception as e:
            return ScenarioResult(
                scenario_id=scenario.scenario_id,
                category=scenario.category, mode=scenario.mode,
                passed=False, error=(
                    f"infrastructure failure (live): {type(e).__name__}: {e}"))
    return run_scripted_scenario(scenario)


def run_all(scenarios):
    return [run_scenario(s) for s in scenarios]

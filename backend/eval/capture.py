"""Result capture — normalize an InvestigationTurnResult (or a whole
multi-turn scenario run) into a stable semantic view that checkers and
metrics operate on. One canonical shape, so scenario assertions and
aggregate metrics never poke raw model objects."""

from dataclasses import dataclass, field


@dataclass
class TurnView:
    user_request: str
    task_status: str
    task_error: str | None
    selected_skill: str | None
    planned_steps: list          # [{type, arguments}]
    tool_calls: list             # [{name, outcome, arguments, data}]
    response: str
    focused_finding_id: str | None
    focused_event_id: str | None
    case_id: str | None
    context_changed: bool
    artifact_ids: list
    artifact_scopes: list
    follow_up_ids: list
    planning_failure_code: str | None


@dataclass
class ScenarioCapture:
    scenario_id: str
    category: str
    mode: str
    turns: list = field(default_factory=list)          # [TurnView]
    # turn-level results filled by the runner after checkers:
    task_completed_turns: int = 0
    semantic_pass_turns: int = 0


def _args_of(step):
    return step.arguments if isinstance(step.arguments, dict) else {}


def capture_turn(result) -> TurnView:
    plan = result.plan
    tool_calls = []
    for tc in (result.execution.tool_calls if result.execution else []):
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        tool_calls.append({
            "name": tc.tool_name,
            "outcome": tc.result.outcome.value if tc.result else None,
            "arguments": tc.arguments,
            "data": data,
            "error_code": (tc.result.error.code if tc.result and tc.result.error
                           else None),
        })
    return TurnView(
        user_request=result.task.user_request,
        task_status=result.task.status.value if hasattr(result.task.status, "value")
                    else str(result.task.status),
        task_error=result.task.error,
        selected_skill=result.task.selected_skill,
        planned_steps=[
            {"type": s.type, "arguments": _args_of(s)}
            for s in (plan.steps if plan else [])],
        tool_calls=tool_calls,
        response=result.response,
        focused_finding_id=result.context.focused_finding_id,
        focused_event_id=result.context.focused_event_id,
        case_id=result.context.case_id,
        context_changed=result.context_changed,
        artifact_ids=[a["artifact_id"] for a in
                      (result.execution.artifacts if result.execution else [])],
        artifact_scopes=[a["scope"] for a in
                         (result.execution.artifacts if result.execution else [])],
        follow_up_ids=[f.follow_up_id for f in result.follow_ups],
        planning_failure_code=(result.planning_failure.code
                               if result.planning_failure else None),
    )


def capture_scenario(scenario_id, category, mode, turn_results) -> ScenarioCapture:
    cap = ScenarioCapture(scenario_id=scenario_id, category=category, mode=mode)
    for r in turn_results:
        view = capture_turn(r)
        cap.turns.append(view)
        if view.task_status == "completed":
            cap.task_completed_turns += 1
    return cap

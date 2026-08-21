from app.tools import execute_tool
from datetime import datetime, timezone
from time import perf_counter

from app.models import (
    StepStatus,
    TaskStatus,
    ToolCall,
    ToolCallStatus,
    Task,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_step(
    task: Task,
    step_index: int,
    context: dict,
) -> dict:
    step = task.steps[step_index]

    step.status = StepStatus.RUNNING
    step.started_at = utc_now()

    tool_call = None

    try:
        tool_call = ToolCall(
            id=f"toolcall-{step.id}",
            tool_name=step.tool_name,
            args={},
            status=ToolCallStatus.RUNNING,
            started_at=utc_now(),
        )

        step.tool_call = tool_call

        start = perf_counter()

        if step.tool_name == "policy_search":
            args = {
                "query": task.user_intent,
                "top_k": 3,
            }

        elif step.tool_name == "evidence_fetch":
            args = {
                "case_id": task.case_id,
            }

        elif step.tool_name == "compose_structured_result":
            args = {
                "evidence": context["evidence"],
                "policies": context["policies"],
                "user_intent": task.user_intent,
            }

        elif step.tool_name == "citation_validate":
            args = {
                "claims": context["structured_result"]["findings"],
                "policies": context["policies"],
            }

        else:
            raise ValueError(
                f"Unknown tool: {step.tool_name}"
            )

        tool_call.args = args

        output = execute_tool(
            step.tool_name,
            args,
        )

        elapsed_ms = int(
            (perf_counter() - start) * 1000
        )

        tool_call.status = ToolCallStatus.SUCCESS
        tool_call.completed_at = utc_now()
        tool_call.latency_ms = elapsed_ms
        tool_call.output = output
        tool_call.output_summary = (
            f"{step.tool_name} completed successfully"
        )

        step.status = StepStatus.SUCCESS
        step.completed_at = utc_now()

        return output

    except Exception as exc:
        print(
            f"[Tool Execution Error] "
            f"step={step.id} "
            f"tool={getattr(step, 'tool_name', None)} "
            f"error={exc}"
        )

        if tool_call is not None:
            tool_call.status = ToolCallStatus.FAILED
            tool_call.completed_at = utc_now()
            tool_call.error = str(exc)

        step.status = StepStatus.FAILED
        step.completed_at = utc_now()
        step.error = str(exc)

        raise

def run_task(task: Task) -> Task:
    task.status = TaskStatus.RUNNING
    task.updated_at = utc_now()

    context = {}

    try:
        for index, step in enumerate(task.steps):

            output = execute_step(
                task,
                index,
                context,
            )

            if step.tool_name == "policy_search":
                context["policies"] = output

            elif step.tool_name == "evidence_fetch":
                context["evidence"] = output

            elif step.tool_name == "compose_structured_result":
                context["structured_result"] = output

            elif step.tool_name == "citation_validate":
                context["citation_validation"] = output

        task.status = TaskStatus.COMPLETED

    except Exception as exc:
        print(
            f"[Task Execution Error] "
            f"task={task.id} "
            f"error={exc}"
        )
        task.status = TaskStatus.FAILED

    task.updated_at = utc_now()

    return task
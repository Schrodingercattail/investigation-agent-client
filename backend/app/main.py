import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.agent import InvestigationAgent
from app.models import (
    Artifact,
    ArtifactType,
    ExecutionMode,
    Step,
    StepStatus,
    Task,
    TaskStatus,
    ToolCall,
    ToolCallStatus,
)
from app.planner import create_investigation_plan
from app.runner import run_task
from app.store import task_store


CASE_ID_PATTERN = re.compile(r"\bcase\s+([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b", re.IGNORECASE)


def extract_case_id(user_intent: str) -> str:
    """
    Extract case_id from user_intent using deterministic regex.

    Supports patterns like:
    - "Investigate case U00299"
    - "case U00299"
    """
    match = CASE_ID_PATTERN.search(user_intent)

    if not match:
        raise ValueError(
            "No case_id found in user_intent. "
            "Please specify a case identifier (e.g., 'Investigate case U00299')."
        )

    return match.group(1)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


app = FastAPI(
    title="Investigation Agent Client",
    version="0.1.0",
)


class CreateTaskRequest(BaseModel):
    user_intent: str
    execution_mode: ExecutionMode = ExecutionMode.AGENT


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/tasks", response_model=Task)
def create_task(request: CreateTaskRequest):
    try:
        case_id = extract_case_id(request.user_intent)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    now = utc_now()

    task = Task(
        id=str(uuid4()),
        user_intent=request.user_intent,
        case_id=case_id,
        execution_mode=request.execution_mode,
        status=TaskStatus.READY,
        steps=create_investigation_plan() if request.execution_mode == ExecutionMode.DETERMINISTIC else [],
        created_at=now,
        updated_at=now,
    )

    return task_store.create(task)


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: str):
    task = task_store.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return task

def run_agent_task(task: Task) -> Task:
    """
    Execute a Task using the InvestigationAgent.

    Converts Agent loop execution into Step/ToolCall records and generates artifacts.
    """
    task.status = TaskStatus.RUNNING
    task.updated_at = utc_now()

    agent = InvestigationAgent()
    agent_result = agent.run(task.user_intent, max_steps=8)

    if agent_result["status"] == "completed":
        # Convert agent steps to Task Steps
        for agent_step in agent_result["steps"]:
            step_id = f"step-{agent_step['step']}"
            tool_name = agent_step["tool_name"]
            tool_args = agent_step["tool_args"]
            result = agent_step["result"]

            step = Step(
                id=step_id,
                name=f"Agent Step: {tool_name}",
                description=f"Agent-selected tool execution",
                tool_name=tool_name,
                status=StepStatus.SUCCESS,
                started_at=utc_now(),
                completed_at=utc_now(),
            )

            tool_call = ToolCall(
                id=f"toolcall-{step_id}",
                tool_name=tool_name,
                args=tool_args,
                status=ToolCallStatus.SUCCESS,
                started_at=utc_now(),
                completed_at=utc_now(),
                output=result,
                output_summary=f"{tool_name} completed successfully",
            )

            step.tool_call = tool_call
            task.steps.append(step)

        # Generate artifacts from agent results
        artifacts = []

        # Find compose_structured_result and citation_validate results
        compose_result = None
        citation_result = None

        for agent_step in agent_result["steps"]:
            if agent_step["tool_name"] == "compose_structured_result":
                compose_result = agent_step["result"]
            elif agent_step["tool_name"] == "citation_validate":
                citation_result = agent_step["result"]

        # Create findings artifact
        if compose_result and "findings" in compose_result:
            artifacts.append(Artifact(
                id=f"artifact-findings-{task.id[:8]}",
                type=ArtifactType.FINDINGS,
                title="Investigation Findings",
                data=compose_result["findings"],
                created_at=utc_now(),
            ))

        # Create actions artifact
        if compose_result and "actions" in compose_result:
            artifacts.append(Artifact(
                id=f"artifact-actions-{task.id[:8]}",
                type=ArtifactType.ACTIONS,
                title="Recommended Actions",
                data=compose_result["actions"],
                created_at=utc_now(),
            ))

        # Create citations artifact
        if citation_result:
            artifacts.append(Artifact(
                id=f"artifact-citations-{task.id[:8]}",
                type=ArtifactType.CITATIONS,
                title="Citation Validation",
                data=citation_result,
                created_at=utc_now(),
            ))

        # Create narrative artifact from final decision
        if agent_result.get("final_decision"):
            artifacts.append(Artifact(
                id=f"artifact-narrative-{task.id[:8]}",
                type=ArtifactType.NARRATIVE,
                title="Agent Narrative",
                data=agent_result["final_decision"],
                created_at=utc_now(),
            ))

        task.artifacts = artifacts
        task.status = TaskStatus.COMPLETED

    elif agent_result["status"] == "max_steps_exceeded":
        # Convert partial steps
        for agent_step in agent_result["steps"]:
            step_id = f"step-{agent_step['step']}"
            tool_name = agent_step["tool_name"]
            tool_args = agent_step["tool_args"]
            result = agent_step["result"]

            step = Step(
                id=step_id,
                name=f"Agent Step: {tool_name}",
                description=f"Agent-selected tool execution",
                tool_name=tool_name,
                status=StepStatus.SUCCESS,
                started_at=utc_now(),
                completed_at=utc_now(),
            )

            tool_call = ToolCall(
                id=f"toolcall-{step_id}",
                tool_name=tool_name,
                args=tool_args,
                status=ToolCallStatus.SUCCESS,
                started_at=utc_now(),
                completed_at=utc_now(),
                output=result,
                output_summary=f"{tool_name} completed successfully",
            )

            step.tool_call = tool_call
            task.steps.append(step)

        # Add a failed final step
        failed_step = Step(
            id=f"step-max-{len(agent_result['steps']) + 1}",
            name="Max Steps Exceeded",
            description="Agent did not complete within max_steps",
            tool_name="final",
            status=StepStatus.FAILED,
            error=agent_result.get("error", "Max steps exceeded"),
            started_at=utc_now(),
            completed_at=utc_now(),
        )
        task.steps.append(failed_step)

        task.status = TaskStatus.FAILED

    else:  # error status
        # Convert successful steps before the error
        for agent_step in agent_result["steps"]:
            step_id = f"step-{agent_step['step']}"
            tool_name = agent_step["tool_name"]
            tool_args = agent_step["tool_args"]
            result = agent_step["result"]

            step = Step(
                id=step_id,
                name=f"Agent Step: {tool_name}",
                description=f"Agent-selected tool execution",
                tool_name=tool_name,
                status=StepStatus.SUCCESS,
                started_at=utc_now(),
                completed_at=utc_now(),
            )

            tool_call = ToolCall(
                id=f"toolcall-{step_id}",
                tool_name=tool_name,
                args=tool_args,
                status=ToolCallStatus.SUCCESS,
                started_at=utc_now(),
                completed_at=utc_now(),
                output=result,
                output_summary=f"{tool_name} completed successfully",
            )

            step.tool_call = tool_call
            task.steps.append(step)

        # Add a failed final step
        error_msg = agent_result.get("error", "Unknown error")
        failed_step = Step(
            id=f"step-error-{len(agent_result['steps']) + 1}",
            name="Agent Execution Error",
            description="Agent loop encountered an error",
            tool_name="final",
            status=StepStatus.FAILED,
            error=error_msg,
            started_at=utc_now(),
            completed_at=utc_now(),
        )
        task.steps.append(failed_step)

        task.status = TaskStatus.FAILED

    task.updated_at = utc_now()
    return task


@app.post("/api/tasks/{task_id}/run", response_model=Task)
def run_task_endpoint(task_id: str):
    task = task_store.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    if task.status == TaskStatus.COMPLETED:
        return task

    if task.status == TaskStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Task is already running",
        )

    if task.status != TaskStatus.READY:
        raise HTTPException(
            status_code=409,
            detail=f"Task cannot be run from status: {task.status}",
        )

    # Route based on execution_mode
    if task.execution_mode == ExecutionMode.AGENT:
        task = run_agent_task(task)
    else:  # DETERMINISTIC
        task = run_task(task)

    return task_store.update(task)
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent import InvestigationAgent
from app.exceptions import (
    AgentExecutionError,
    InvestigationAgentError,
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    MaxStepsExceededError,
    RiskPlatformAuthenticationError,
    RiskPlatformError,
    RiskPlatformUnavailableError,
    ToolExecutionError,
)
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


logger = logging.getLogger(__name__)


def configure_logging():
    """Configure application logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )


configure_logging()


CASE_ID_PATTERN = re.compile(r"\bcase\s+([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b", re.IGNORECASE)


def extract_case_id(user_intent: str) -> str:
    """
    Extract case_id from user_intent using deterministic regex.

    Supports patterns like:
    - "Investigate case U00299"
    - "case U00299"
    - "case 00299" (normalized to U00299)

    Risk Platform uses U-prefixed user_ids as canonical identifiers.
    Normalizes input to canonical U-prefix format.
    """
    match = CASE_ID_PATTERN.search(user_intent)

    if not match:
        raise ValueError(
            "No case_id found in user_intent. "
            "Please specify a case identifier (e.g., 'Investigate case U00299')."
        )

    raw_case_id = match.group(1)

    # Normalize to canonical U-prefixed format for Risk Platform
    # Risk Platform uses U-prefix as canonical identifier (e.g., U00010)
    # User may enter "00010" -> normalize to "U00010"
    # User may enter "U00010" -> already canonical, return as-is
    if not raw_case_id.startswith('U'):
        return f"U{raw_case_id}"
    return raw_case_id


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


app = FastAPI(
    title="Investigation Agent Client",
    version="0.1.0",
)

# --- V2 Investigation API (separate boundary; legacy endpoints untouched) ---
from app.api.investigations import router as investigations_v2_router  # noqa: E402

app.include_router(investigations_v2_router)



# Global exception handlers
@app.exception_handler(LLMConfigurationError)
async def llm_configuration_error_handler(request: Request, exc: LLMConfigurationError):
    """Handle LLM configuration errors."""
    logger.error(f"LLM configuration error: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"Agent configuration error: {exc.message}"},
    )


@app.exception_handler(LLMRateLimitError)
async def llm_rate_limit_error_handler(request: Request, exc: LLMRateLimitError):
    """Handle LLM rate limit errors."""
    logger.warning(f"LLM rate limit exceeded: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": f"Agent rate limit exceeded: {exc.message}"},
    )


@app.exception_handler(LLMTimeoutError)
async def llm_timeout_error_handler(request: Request, exc: LLMTimeoutError):
    """Handle LLM timeout errors."""
    logger.warning(f"LLM timeout: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={"detail": f"Agent request timed out: {exc.message}"},
    )


@app.exception_handler(RiskPlatformAuthenticationError)
async def risk_platform_auth_error_handler(request: Request, exc: RiskPlatformAuthenticationError):
    """Handle Risk Platform authentication errors."""
    logger.error(f"Risk Platform authentication error: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": f"Risk Platform authentication failed: {exc.message}"},
    )


@app.exception_handler(RiskPlatformUnavailableError)
async def risk_platform_unavailable_error_handler(request: Request, exc: RiskPlatformUnavailableError):
    """Handle Risk Platform unavailable errors."""
    logger.warning(f"Risk Platform unavailable: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": f"Risk Platform temporarily unavailable: {exc.message}"},
    )


@app.exception_handler(RiskPlatformError)
async def risk_platform_error_handler(request: Request, exc: RiskPlatformError):
    """Handle general Risk Platform errors."""
    logger.error(f"Risk Platform error: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"Risk Platform error: {exc.message}"},
    )


@app.exception_handler(InvestigationAgentError)
async def investigation_agent_error_handler(request: Request, exc: InvestigationAgentError):
    """Handle general investigation agent errors."""
    logger.error(f"Investigation agent error: {exc.message}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": exc.message},
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

    Handles agent errors and converts them to appropriate task states.

    Args:
        task: Task to execute

    Returns:
        Updated task with execution results

    Raises:
        HTTPException: For configuration errors (4xx) or server issues (5xx)
    """
    task.status = TaskStatus.RUNNING
    task.updated_at = utc_now()

    try:
        agent = InvestigationAgent()
    except LLMConfigurationError as e:
        # Configuration errors should be 500 - server misconfiguration
        logger.error(f"LLM configuration error: {e.message}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent configuration error: {e.message}",
        ) from e

    try:
        agent_result = agent.run(task.user_intent, max_steps=8)
    except LLMError as e:
        # LLM errors during execution
        logger.error(f"LLM error during agent execution: {e.message}")
        # Create a failed step with the error
        failed_step = Step(
            id=f"step-llm-error-{str(uuid4())[:8]}",
            name="LLM Error",
            description="LLM execution failed",
            tool_name="llm",
            status=StepStatus.FAILED,
            error=e.message,
            started_at=utc_now(),
            completed_at=utc_now(),
        )
        task.steps.append(failed_step)
        task.status = TaskStatus.FAILED
        task.updated_at = utc_now()
        return task
    except Exception as e:
        # Unexpected errors
        logger.error(f"Unexpected error during agent execution: {str(e)}", exc_info=True)
        failed_step = Step(
            id=f"step-unexpected-error-{str(uuid4())[:8]}",
            name="Unexpected Error",
            description="An unexpected error occurred",
            tool_name="system",
            status=StepStatus.FAILED,
            error=f"Unexpected error: {str(e)}",
            started_at=utc_now(),
            completed_at=utc_now(),
        )
        task.steps.append(failed_step)
        task.status = TaskStatus.FAILED
        task.updated_at = utc_now()
        return task

    if agent_result["status"] == "completed":
        # Convert agent steps to Task Steps
        for agent_step in agent_result["steps"]:
            step_id = f"step-{agent_step['step']}-{str(uuid4())[:8]}"
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
                id=f"toolcall-{step_id}-{str(uuid4())[:8]}",
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

        # Create narrative artifact from risk_summary (user-facing investigation summary)
        if compose_result and "risk_summary" in compose_result:
            # Build user-facing summary from authoritative risk_summary
            risk_summary = compose_result["risk_summary"]
            narrative_data = {
                "case_id": risk_summary.get("case_id", task.case_id),
                "risk_level": risk_summary.get("risk_level", "unknown"),
                "risk_score": risk_summary.get("risk_score", 0),
                "primary_reason": risk_summary.get("primary_reason"),
                "recommended_action": risk_summary.get("recommended_action"),
            }
            artifacts.append(Artifact(
                id=f"artifact-narrative-{task.id[:8]}",
                type=ArtifactType.NARRATIVE,
                title="Investigation Summary",
                data=narrative_data,
                created_at=utc_now(),
            ))

        task.artifacts = artifacts
        task.status = TaskStatus.COMPLETED

    elif agent_result["status"] == "max_steps_exceeded":
        # Convert partial steps
        for agent_step in agent_result["steps"]:
            step_id = f"step-{agent_step['step']}-{str(uuid4())[:8]}"
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
                id=f"toolcall-{step_id}-{str(uuid4())[:8]}",
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

        # Add a failed final step with detailed error context
        error_details = agent_result.get("error_details", {})
        failed_step = Step(
            id=f"step-max-{str(uuid4())[:8]}",  # Use unique ID to avoid conflicts
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
        logger.warning(f"Agent max steps exceeded for task {task.id}")

    else:  # error status
        # Convert successful steps before the error
        for agent_step in agent_result["steps"]:
            step_id = f"step-{agent_step['step']}-{str(uuid4())[:8]}"
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
                id=f"toolcall-{step_id}-{str(uuid4())[:8]}",
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

        # Add a failed final step with detailed error context
        error_msg = agent_result.get("error", "Unknown error")
        error_details = agent_result.get("error_details", {})
        error_type = error_details.get("error_type", "UnknownError")

        # Generate more descriptive error based on type
        if error_type == "LLMTimeoutError":
            error_msg = f"LLM request timed out: {error_msg}"
        elif error_type == "LLMRateLimitError":
            error_msg = f"LLM rate limit exceeded: {error_msg}"
        elif error_type == "ToolExecutionError":
            tool_name = error_details.get("context", {}).get("tool_name", "unknown")
            error_msg = f"Tool execution failed ({tool_name}): {error_msg}"
        elif error_type == "AgentExecutionError":
            step_num = error_details.get("context", {}).get("step_number", "unknown")
            error_msg = f"Agent execution error at step {step_num}: {error_msg}"

        failed_step = Step(
            id=f"step-error-{str(uuid4())[:8]}",  # Use unique ID to avoid conflicts
            name="Agent Execution Error",
            description=f"Agent loop encountered a {error_type}",
            tool_name="final",
            status=StepStatus.FAILED,
            error=error_msg,
            started_at=utc_now(),
            completed_at=utc_now(),
        )
        task.steps.append(failed_step)

        task.status = TaskStatus.FAILED
        logger.error(f"Agent execution failed for task {task.id}: {error_msg}")

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

    # Allow running tasks from READY or FAILED status
    if task.status not in (TaskStatus.READY, TaskStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=f"Task cannot be run from status: {task.status}",
        )

    # For failed tasks, clean up previous execution results before re-running
    if task.status == TaskStatus.FAILED:
        # First, update the task to clear old data in the database
        task.steps = []
        task.artifacts = []
        task.status = TaskStatus.READY
        task.updated_at = utc_now()
        # Persist the cleared state to database
        task = task_store.update(task)

    # Route based on execution_mode
    if task.execution_mode == ExecutionMode.AGENT:
        task = run_agent_task(task)
    else:  # DETERMINISTIC
        task = run_task(task)

    # Update the final task state to database
    return task_store.update(task)


@app.post("/api/tasks/{task_id}/regenerate", response_model=Task)
def regenerate_task_endpoint(task_id: str):
    """
    Regenerate a completed task by re-running the Agent.

    Reuses the same task ID and updates Steps, ToolCalls, and Artifacts
    with the new execution result.
    """
    task = task_store.get(task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    if task.status == TaskStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Task is currently running",
        )

    # Clear previous execution results
    task.steps = []
    task.artifacts = []
    task.status = TaskStatus.READY
    task.updated_at = utc_now()

    # Update store to cleared state
    task = task_store.update(task)

    # Re-run the agent
    if task.execution_mode == ExecutionMode.AGENT:
        task = run_agent_task(task)
    else:  # DETERMINISTIC
        task = run_task(task)

    return task_store.update(task)
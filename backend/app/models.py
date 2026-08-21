from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    """Determines how a Task is executed."""
    AGENT = "agent"
    DETERMINISTIC = "deterministic"


class TaskStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolCallStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ArtifactType(str, Enum):
    FINDINGS = "findings"
    ACTIONS = "actions"
    CITATIONS = "citations"
    NARRATIVE = "narrative"


class ToolCall(BaseModel):
    id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)

    status: ToolCallStatus = ToolCallStatus.RUNNING

    started_at: str | None = None
    completed_at: str | None = None
    latency_ms: int | None = None

    output_summary: str | None = None
    output: Any | None = None
    error: str | None = None


class Step(BaseModel):
    id: str
    name: str
    description: str
    tool_name: str  # The tool to execute for this step

    status: StepStatus = StepStatus.PENDING

    tool_call: ToolCall | None = None

    started_at: str | None = None
    completed_at: str | None = None

    error: str | None = None


class Artifact(BaseModel):
    id: str
    type: ArtifactType

    title: str
    data: Any

    created_at: str | None = None


class Task(BaseModel):
    id: str

    user_intent: str
    case_id: str  # Extracted case identifier (e.g., "U00299")
    execution_mode: ExecutionMode = ExecutionMode.AGENT

    status: TaskStatus = TaskStatus.DRAFT

    steps: list[Step] = Field(default_factory=list)

    artifacts: list[Artifact] = Field(default_factory=list)

    acceptance_status: str = "pending"

    created_at: str | None = None
    updated_at: str | None = None
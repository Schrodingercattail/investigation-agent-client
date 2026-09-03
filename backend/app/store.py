"""
SQLite-based TaskStore for durable persistence.

Provides the same logical API as the previous in-memory store
but persists all Tasks, Steps, ToolCalls, and Artifacts to SQLite.
"""

import json
from typing import Optional

from app.database import get_connection
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


class TaskStore:
    """SQLite-backed storage for Investigation Tasks."""

    def create(self, task: Task) -> Task:
        """Persist a new Task and its related entities."""
        conn = get_connection()

        # Insert task
        conn.execute(
            """
            INSERT INTO tasks (id, user_intent, case_id, execution_mode, status,
                              acceptance_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.user_intent,
                task.case_id,
                task.execution_mode.value,
                task.status.value,
                task.acceptance_status,
                task.created_at,
                task.updated_at,
            ),
        )

        # Insert steps
        for order, step in enumerate(task.steps):
            self._insert_step(conn, step, task.id, order)

        # Insert artifacts
        for artifact in task.artifacts:
            self._insert_artifact(conn, artifact, task.id)

        conn.commit()
        return task

    def get(self, task_id: str) -> Optional[Task]:
        """Retrieve a Task by ID with all related entities."""
        conn = get_connection()

        # Get task
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,)
        ).fetchone()

        if not row:
            return None

        # Reconstruct Task
        task = Task(
            id=row["id"],
            user_intent=row["user_intent"],
            case_id=row["case_id"],
            execution_mode=ExecutionMode(row["execution_mode"]),
            status=TaskStatus(row["status"]),
            acceptance_status=row["acceptance_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            steps=[],
            artifacts=[],
        )

        # Load steps with tool_calls in execution order
        step_rows = conn.execute(
            """
            SELECT * FROM steps
            WHERE task_id = ?
            ORDER BY execution_order
            """,
            (task_id,)
        ).fetchall()

        for step_row in step_rows:
            step = Step(
                id=step_row["id"],
                name=step_row["name"],
                description=step_row["description"],
                tool_name=step_row["tool_name"],
                status=StepStatus(step_row["status"]),
                started_at=step_row["started_at"],
                completed_at=step_row["completed_at"],
                error=step_row["error"],
                tool_call=None,
            )

            # Load tool_call for this step
            tool_call_row = conn.execute(
                "SELECT * FROM tool_calls WHERE step_id = ?",
                (step_row["id"],)
            ).fetchone()

            if tool_call_row:
                step.tool_call = ToolCall(
                    id=tool_call_row["id"],
                    tool_name=tool_call_row["tool_name"],
                    args=json.loads(tool_call_row["args"]) if tool_call_row["args"] else {},
                    status=ToolCallStatus(tool_call_row["status"]),
                    started_at=tool_call_row["started_at"],
                    completed_at=tool_call_row["completed_at"],
                    latency_ms=tool_call_row["latency_ms"],
                    output_summary=tool_call_row["output_summary"],
                    output=json.loads(tool_call_row["output"]) if tool_call_row["output"] else None,
                    error=tool_call_row["error"],
                )

            task.steps.append(step)

        # Load artifacts
        artifact_rows = conn.execute(
            "SELECT * FROM artifacts WHERE task_id = ?",
            (task_id,)
        ).fetchall()

        for artifact_row in artifact_rows:
            task.artifacts.append(
                Artifact(
                    id=artifact_row["id"],
                    type=ArtifactType(artifact_row["type"]),
                    title=artifact_row["title"],
                    data=json.loads(artifact_row["data"]) if artifact_row["data"] else None,
                    created_at=artifact_row["created_at"],
                )
            )

        return task

    def update(self, task: Task) -> Task:
        """Update an existing Task and its related entities."""
        conn = get_connection()

        # Update task
        conn.execute(
            """
            UPDATE tasks
            SET user_intent = ?, case_id = ?, execution_mode = ?, status = ?,
                acceptance_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                task.user_intent,
                task.case_id,
                task.execution_mode.value,
                task.status.value,
                task.acceptance_status,
                task.updated_at,
                task.id,
            ),
        )

        # Delete existing steps and tool_calls (cascading delete handles this)
        conn.execute("DELETE FROM steps WHERE task_id = ?", (task.id,))

        # Re-insert steps
        for order, step in enumerate(task.steps):
            self._insert_step(conn, step, task.id, order)

        # Delete existing artifacts
        conn.execute("DELETE FROM artifacts WHERE task_id = ?", (task.id,))

        # Re-insert artifacts
        for artifact in task.artifacts:
            self._insert_artifact(conn, artifact, task.id)

        conn.commit()
        return task

    def _insert_step(self, conn, step: Step, task_id: str, order: int) -> None:
        """Insert a step and its tool_call."""
        conn.execute(
            """
            INSERT INTO steps (id, task_id, name, description, tool_name, status,
                              started_at, completed_at, error, execution_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step.id,
                task_id,
                step.name,
                step.description,
                step.tool_name,
                step.status.value,
                step.started_at,
                step.completed_at,
                step.error,
                order,
            ),
        )

        # Insert tool_call if present
        if step.tool_call:
            conn.execute(
                """
                INSERT INTO tool_calls (id, step_id, tool_name, args, status,
                                       started_at, completed_at, latency_ms,
                                       output_summary, output, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.tool_call.id,
                    step.id,
                    step.tool_call.tool_name,
                    json.dumps(step.tool_call.args),
                    step.tool_call.status.value,
                    step.tool_call.started_at,
                    step.tool_call.completed_at,
                    step.tool_call.latency_ms,
                    step.tool_call.output_summary,
                    json.dumps(step.tool_call.output) if step.tool_call.output else None,
                    step.tool_call.error,
                ),
            )

    def _insert_artifact(self, conn, artifact: Artifact, task_id: str) -> None:
        """Insert an artifact."""
        conn.execute(
            """
            INSERT INTO artifacts (id, task_id, type, title, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.id,
                task_id,
                artifact.type.value,
                artifact.title,
                json.dumps(artifact.data) if artifact.data else None,
                artifact.created_at,
            ),
        )


# Global store instance
task_store = TaskStore()

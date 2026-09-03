"""
Tests for SQLite-based TaskStore.

Tests persistence, reconstruction, and backend restart behavior.
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.database import get_database_path, initialize_schema, close_connection
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
from app.store import TaskStore


@pytest.fixture
def temp_database():
    """Create a temporary database for testing."""
    # Override the database path for testing
    temp_db = tempfile.mktemp(suffix=".db")

    try:
        # Save original path and set temp path
        import app.database as db_module
        original_get_path = db_module.get_database_path
        db_module.get_database_path = lambda: Path(temp_db)

        # Close any existing connection and reset global connection
        db_module._db_connection = None
        close_connection()

        # Initialize schema for the temp database
        import sqlite3
        conn = sqlite3.connect(temp_db)
        initialize_schema(conn)
        conn.close()

        yield temp_db

    finally:
        # Clean up
        close_connection()
        if os.path.exists(temp_db):
            os.remove(temp_db)

        # Restore original path
        db_module.get_database_path = original_get_path
        db_module._db_connection = None


@pytest.fixture
def store(temp_database):
    """Create a fresh TaskStore for each test."""
    close_connection()  # Ensure clean state
    return TaskStore()


def test_database_initializes(temp_database):
    """Test that the database is created with correct schema."""
    import sqlite3

    conn = sqlite3.connect(temp_database)
    cursor = conn.cursor()

    # Check tables exist
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]

    assert "tasks" in tables
    assert "steps" in tables
    assert "tool_calls" in tables
    assert "artifacts" in tables

    # Check indexes exist
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='index'
        ORDER BY name
    """)
    indexes = [row[0] for row in cursor.fetchall()]

    assert "idx_steps_task_id" in indexes
    assert "idx_tool_calls_step_id" in indexes
    assert "idx_artifacts_task_id" in indexes

    conn.close()


def test_create_task_persisted(store):
    """Test that creating a Task persists it to the database."""
    task = Task(
        id="test-task-1",
        user_intent="Investigate case U00299",
        case_id="U00299",
        execution_mode=ExecutionMode.AGENT,
        status=TaskStatus.READY,
        acceptance_status="pending",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        steps=[],
        artifacts=[],
    )

    created = store.create(task)

    # Should return the same task
    assert created.id == task.id
    assert created.user_intent == task.user_intent

    # Should be retrievable
    retrieved = store.get("test-task-1")
    assert retrieved is not None
    assert retrieved.id == task.id
    assert retrieved.user_intent == task.user_intent
    assert retrieved.case_id == task.case_id


def test_get_task_reconstructs_complete_object(store):
    """Test that get() reconstructs the complete Task with steps and artifacts."""
    # Create a task with steps and artifacts
    tool_call = ToolCall(
        id="toolcall-1",
        tool_name="test_tool",
        args={"param": "value"},
        status=ToolCallStatus.SUCCESS,
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:01:00Z",
        latency_ms=60000,
        output_summary="Test completed",
        output={"result": "data"},
        error=None,
    )

    step = Step(
        id="step-1",
        name="Test Step",
        description="A test step",
        tool_name="test_tool",
        status=StepStatus.SUCCESS,
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:01:00Z",
        tool_call=tool_call,
        error=None,
    )

    artifact = Artifact(
        id="artifact-1",
        type=ArtifactType.FINDINGS,
        title="Test Findings",
        data={"findings": []},
        created_at="2024-01-01T00:01:00Z",
    )

    task = Task(
        id="test-task-complete",
        user_intent="Test complete task reconstruction",
        case_id="U00123",
        execution_mode=ExecutionMode.DETERMINISTIC,
        status=TaskStatus.COMPLETED,
        acceptance_status="accepted",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:01:00Z",
        steps=[step],
        artifacts=[artifact],
    )

    store.create(task)

    # Retrieve and verify complete reconstruction
    retrieved = store.get("test-task-complete")

    assert retrieved is not None
    assert len(retrieved.steps) == 1
    assert len(retrieved.artifacts) == 1

    # Verify step details
    retrieved_step = retrieved.steps[0]
    assert retrieved_step.id == step.id
    assert retrieved_step.name == step.name
    assert retrieved_step.tool_name == step.tool_name
    assert retrieved_step.status == step.status
    assert retrieved_step.started_at == step.started_at
    assert retrieved_step.completed_at == step.completed_at

    # Verify tool_call details
    assert retrieved_step.tool_call is not None
    assert retrieved_step.tool_call.id == tool_call.id
    assert retrieved_step.tool_call.tool_name == tool_call.tool_name
    assert retrieved_step.tool_call.args == tool_call.args
    assert retrieved_step.tool_call.status == tool_call.status
    assert retrieved_step.tool_call.latency_ms == tool_call.latency_ms
    assert retrieved_step.tool_call.output_summary == tool_call.output_summary
    assert retrieved_step.tool_call.output == tool_call.output

    # Verify artifact details
    retrieved_artifact = retrieved.artifacts[0]
    assert retrieved_artifact.id == artifact.id
    assert retrieved_artifact.type == artifact.type
    assert retrieved_artifact.title == artifact.title
    assert retrieved_artifact.data == artifact.data


def test_update_task_persists_changes(store):
    """Test that updating a Task persists all changes."""
    # Create initial task
    task = Task(
        id="test-task-update",
        user_intent="Initial intent",
        case_id="U00123",
        execution_mode=ExecutionMode.AGENT,
        status=TaskStatus.READY,
        steps=[],
        artifacts=[],
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )

    store.create(task)

    # Update the task
    tool_call = ToolCall(
        id="toolcall-update",
        tool_name="updated_tool",
        args={"new": "args"},
        status=ToolCallStatus.SUCCESS,
        output_summary="Updated output",
    )

    step = Step(
        id="step-update",
        name="Updated Step",
        description="Updated description",
        tool_name="updated_tool",
        status=StepStatus.SUCCESS,
        tool_call=tool_call,
    )

    artifact = Artifact(
        id="artifact-update",
        type=ArtifactType.ACTIONS,
        title="Updated Actions",
        data=["action1", "action2"],
    )

    task.user_intent = "Updated intent"
    task.status = TaskStatus.COMPLETED
    task.acceptance_status = "accepted"
    task.updated_at = "2024-01-01T00:02:00Z"
    task.steps = [step]
    task.artifacts = [artifact]

    store.update(task)

    # Verify updates persisted
    retrieved = store.get("test-task-update")
    assert retrieved is not None
    assert retrieved.user_intent == "Updated intent"
    assert retrieved.status == TaskStatus.COMPLETED
    assert retrieved.acceptance_status == "accepted"
    assert retrieved.updated_at == "2024-01-01T00:02:00Z"
    assert len(retrieved.steps) == 1
    assert len(retrieved.artifacts) == 1

    # Verify step details
    retrieved_step = retrieved.steps[0]
    assert retrieved_step.name == "Updated Step"
    assert retrieved_step.description == "Updated description"
    assert retrieved_step.tool_call is not None
    assert retrieved_step.tool_call.tool_name == "updated_tool"

    # Verify artifact details
    retrieved_artifact = retrieved.artifacts[0]
    assert retrieved_artifact.type == ArtifactType.ACTIONS
    assert retrieved_artifact.title == "Updated Actions"
    assert retrieved_artifact.data == ["action1", "action2"]


def test_get_nonexistent_task_returns_none(store):
    """Test that getting a non-existent task returns None."""
    result = store.get("nonexistent-task-id")
    assert result is None


def test_multiple_steps_preserve_execution_order(store):
    """Test that multiple steps preserve their execution order."""
    steps = [
        Step(
            id=f"step-{i}",
            name=f"Step {i}",
            description=f"Description {i}",
            tool_name="tool",
            status=StepStatus.SUCCESS,
            started_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:01:00Z",
        )
        for i in range(5)
    ]

    task = Task(
        id="test-order",
        user_intent="Test execution order",
        case_id="U00123",
        execution_mode=ExecutionMode.DETERMINISTIC,
        status=TaskStatus.COMPLETED,
        steps=steps,
        artifacts=[],
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:01:00Z",
    )

    store.create(task)

    # Retrieve and verify order
    retrieved = store.get("test-order")
    assert retrieved is not None
    assert len(retrieved.steps) == 5

    # Verify the order matches the original
    for i, step in enumerate(retrieved.steps):
        assert step.id == f"step-{i}"
        assert step.name == f"Step {i}"


def test_backend_restart_simulation(store):
    """Test that closing and reopening the database preserves data."""
    # Create a task
    task = Task(
        id="test-restart",
        user_intent="Test backend restart",
        case_id="U00999",
        execution_mode=ExecutionMode.AGENT,
        status=TaskStatus.READY,
        steps=[],
        artifacts=[],
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )

    store.create(task)

    # Simulate backend restart by closing connection
    close_connection()

    # Create a new store instance (simulating backend restart)
    new_store = TaskStore()

    # Verify task is still retrievable
    retrieved = new_store.get("test-restart")
    assert retrieved is not None
    assert retrieved.id == task.id
    assert retrieved.user_intent == task.user_intent
    assert retrieved.case_id == task.case_id


def test_json_serialization_of_complex_data(store):
    """Test that complex JSON data is properly serialized and deserialized."""
    # Create task with complex nested data
    complex_args = {
        "query": "SELECT * FROM users WHERE active = true",
        "filters": {
            "date_range": {"start": "2024-01-01", "end": "2024-12-31"},
            "status": ["active", "pending"],
        },
        "limit": 100,
    }

    complex_output = {
        "results": [
            {"id": 1, "name": "User 1", "data": {"nested": {"value": "complex"}}},
            {"id": 2, "name": "User 2", "data": {"nested": {"value": "structure"}}},
        ],
        "metadata": {"total": 2, "page": 1},
    }

    tool_call = ToolCall(
        id="toolcall-complex",
        tool_name="complex_tool",
        args=complex_args,
        status=ToolCallStatus.SUCCESS,
        output=complex_output,
        output_summary="Complex operation completed",
    )

    step = Step(
        id="step-complex",
        name="Complex Step",
        description="Step with complex data",
        tool_name="complex_tool",
        status=StepStatus.SUCCESS,
        tool_call=tool_call,
    )

    task = Task(
        id="test-complex",
        user_intent="Test complex JSON serialization",
        case_id="U00555",
        execution_mode=ExecutionMode.AGENT,
        status=TaskStatus.COMPLETED,
        steps=[step],
        artifacts=[],
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:01:00Z",
    )

    store.create(task)

    # Retrieve and verify complex data integrity
    retrieved = store.get("test-complex")
    assert retrieved is not None
    assert len(retrieved.steps) == 1

    retrieved_step = retrieved.steps[0]
    assert retrieved_step.tool_call is not None
    assert retrieved_step.tool_call.args == complex_args
    assert retrieved_step.tool_call.output == complex_output


def test_status_changes_persist(store):
    """Test that Task status changes are properly persisted."""
    task = Task(
        id="test-status",
        user_intent="Test status persistence",
        case_id="U00333",
        execution_mode=ExecutionMode.AGENT,
        status=TaskStatus.READY,
        steps=[],
        artifacts=[],
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )

    store.create(task)

    # Update status to RUNNING
    task.status = TaskStatus.RUNNING
    task.updated_at = "2024-01-01T00:00:30Z"
    store.update(task)

    retrieved = store.get("test-status")
    assert retrieved is not None
    assert retrieved.status == TaskStatus.RUNNING

    # Update status to COMPLETED
    task.status = TaskStatus.COMPLETED
    task.updated_at = "2024-01-01T00:01:00Z"
    store.update(task)

    retrieved = store.get("test-status")
    assert retrieved.status == TaskStatus.COMPLETED


def test_all_artifact_types_persist(store):
    """Test that all artifact types are properly persisted."""
    artifacts = [
        Artifact(
            id="artifact-findings",
            type=ArtifactType.FINDINGS,
            title="Findings",
            data={"findings": []},
        ),
        Artifact(
            id="artifact-actions",
            type=ArtifactType.ACTIONS,
            title="Actions",
            data=["action1", "action2"],
        ),
        Artifact(
            id="artifact-citations",
            type=ArtifactType.CITATIONS,
            title="Citations",
            data={"supported": 10, "unsupported": 2},
        ),
        Artifact(
            id="artifact-narrative",
            type=ArtifactType.NARRATIVE,
            title="Narrative",
            data="Summary text",
        ),
    ]

    task = Task(
        id="test-artifacts",
        user_intent="Test all artifact types",
        case_id="U00777",
        execution_mode=ExecutionMode.AGENT,
        status=TaskStatus.COMPLETED,
        steps=[],
        artifacts=artifacts,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:01:00Z",
    )

    store.create(task)

    # Retrieve and verify all artifact types
    retrieved = store.get("test-artifacts")
    assert retrieved is not None
    assert len(retrieved.artifacts) == 4

    artifact_types = {a.type for a in retrieved.artifacts}
    assert ArtifactType.FINDINGS in artifact_types
    assert ArtifactType.ACTIONS in artifact_types
    assert ArtifactType.CITATIONS in artifact_types
    assert ArtifactType.NARRATIVE in artifact_types

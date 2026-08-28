"""Tests for Week 1 runtime models: Plan, PlanStep, ToolCallV2, ArtifactV2, TaskV2.

Covers the model-contract invariants from docs/architecture/DOMAIN_MODELS_V1.md
§2.6–2.11 without implementing any runtime systems (planner/executor).
"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.models import (
    ArtifactTypeV2,
    ArtifactV2,
    Plan,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    TaskStatusV2,
    TaskV2,
    ToolCallStatusV2,
    ToolCallV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)


# --- helpers -----------------------------------------------------------------

def make_step(**overrides: Any) -> PlanStep:
    base: dict[str, Any] = {
        "step_id": "S1",
        "type": "signal_explain",
        "reason": "explain why F3 was flagged",
        "tool_name": "signal_explain",
        "arguments": {"finding_id": "F3", "signal_type": "Rule"},
    }
    base.update(overrides)
    return PlanStep(**base)


def make_plan(**overrides: Any) -> Plan:
    base: dict[str, Any] = {
        "plan_id": "PLAN-1",
        "investigation_id": "INV-1",
        "goal": "Explain why F3 was flagged",
        "steps": [make_step()],
        "status": PlanStatus.DRAFT,
        "created_at": "2026-08-27T00:00:00Z",
    }
    base.update(overrides)
    return Plan(**base)


def make_tool_result() -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"timeline_events": [{"event_id": "EVT-1"}]},
    )


def make_tool_call(**overrides: Any) -> ToolCallV2:
    base: dict[str, Any] = {
        "tool_call_id": "TC-1",
        "investigation_id": "INV-1",
        "task_id": "TASK-1",
        "tool_name": "finding_drilldown",
        "arguments": {"finding_id": "F3", "view": "timeline", "top_n": 20},
        "status": ToolCallStatusV2.SUCCESS,
        "result": make_tool_result(),
        "started_at": "2026-08-27T00:00:00Z",
        "completed_at": "2026-08-27T00:00:01Z",
    }
    base.update(overrides)
    return ToolCallV2(**base)


def make_artifact(**overrides: Any) -> ArtifactV2:
    base: dict[str, Any] = {
        "artifact_id": "ART-1",
        "investigation_id": "INV-1",
        "task_id": "TASK-1",
        "scope": "case:U00299",
        "artifact_type": ArtifactTypeV2.FINDINGS_SUMMARY,
        "title": "Findings Summary — U00299",
        "content": "# Findings\n\n- F3 Coordinated Trading Pattern",
        "source_tool_calls": ["TC-1"],
        "created_at": "2026-08-27T00:01:00Z",
    }
    base.update(overrides)
    return ArtifactV2(**base)


def make_task(**overrides: Any) -> TaskV2:
    base: dict[str, Any] = {
        "task_id": "TASK-1",
        "investigation_id": "INV-1",
        "type": "investigation_turn",
        "status": TaskStatusV2.COMPLETED,
        "user_request": "Why was F3 flagged?",
        "plan_id": "PLAN-1",
        "selected_skill": "timeline_investigation",
        "tool_call_ids": ["TC-1"],
        "artifact_ids": ["ART-1"],
        "started_at": "2026-08-27T00:00:00Z",
        "completed_at": "2026-08-27T00:01:00Z",
    }
    base.update(overrides)
    return TaskV2(**base)


# --- valid objects --------------------------------------------------------------

class TestValidPlan:
    def test_valid_plan(self):
        plan = make_plan()
        assert plan.plan_id == "PLAN-1"
        assert plan.investigation_id == "INV-1"
        assert plan.goal == "Explain why F3 was flagged"
        assert len(plan.steps) == 1
        assert plan.status == PlanStatus.DRAFT
        assert plan.plan_version == 1  # default

    def test_plan_status_lifecycle_values(self):
        assert {s.value for s in PlanStatus} == {
            "draft", "validating", "rejected", "ready",
            "executing", "completed", "failed",
        }

    def test_plan_requires_non_empty_fields(self):
        for field in ("plan_id", "investigation_id", "goal"):
            data = {"plan_id": "P", "investigation_id": "I", "goal": "g"}
            data[field] = ""
            with pytest.raises(ValidationError):
                Plan(**data)

    def test_plan_step_type_is_open_string_for_registry_layer(self):
        # Model layer accepts registry-vocabulary strings; membership in the
        # vocabulary is enforced later by the validator, not here.
        step = make_step(type="fetch_case")
        assert step.type == "fetch_case"


class TestValidPlanStep:
    def test_valid_plan_step(self):
        step = make_step()
        assert step.step_id == "S1"
        assert step.status == PlanStepStatus.PENDING
        assert step.arguments == {"finding_id": "F3", "signal_type": "Rule"}
        assert step.depends_on == []
        assert step.tool_name == "signal_explain"

    def test_structured_arguments_required(self):
        # arguments must remain structured data — a free-form execution string
        # is rejected.
        with pytest.raises(ValidationError):
            make_step(arguments="run signal explain on F3")

    def test_arguments_default_to_empty_dict(self):
        step = make_step(arguments=None) if False else PlanStep(
            step_id="S2", type="artifact"
        )
        assert step.arguments == {}
        assert step.tool_name is None

    def test_per_step_execution_status(self):
        step = make_step(status=PlanStepStatus.RUNNING, started_at="2026-08-27T00:00:00Z")
        assert step.status == PlanStepStatus.RUNNING

    def test_rejected_and_skipped_are_distinct_statuses(self):
        # Both exist and are semantically different (validator-level vs
        # execution-time precondition failure).
        assert PlanStepStatus.REJECTED != PlanStepStatus.SKIPPED
        rejected = make_step(status=PlanStepStatus.REJECTED, error="not in skill vocabulary")
        skipped = make_step(status=PlanStepStatus.SKIPPED, error="dependency failed")
        assert {rejected.status, skipped.status} == {
            PlanStepStatus.REJECTED, PlanStepStatus.SKIPPED,
        }

    def test_depends_on_ordering(self):
        step_b = PlanStep(
            step_id="S2", type="artifact",
            depends_on=["S1"],
        )
        assert step_b.depends_on == ["S1"]


class TestValidToolCallAndResult:
    def test_toolcall_with_normalized_result(self):
        call = make_tool_call()
        assert call.result is not None
        assert call.result.outcome == ToolResultOutcome.SUCCESS
        assert isinstance(call.result, ToolResult)  # normalized envelope, not raw dict

    def test_toolcall_pending_state_has_no_result(self):
        call = make_tool_call(status=ToolCallStatusV2.RUNNING, result=None)
        assert call.result is None
        assert call.error is None

    def test_toolcall_failure_record(self):
        call = make_tool_call(
            status=ToolCallStatusV2.FAILED,
            result=None,
            error="Risk Platform unavailable",
        )
        assert call.status == ToolCallStatusV2.FAILED
        assert "unavailable" in call.error


class TestArtifactProvenance:
    def test_valid_artifact_with_provenance(self):
        artifact = make_artifact(source_tool_calls=["TC-1", "TC-2"])
        assert artifact.source_tool_calls == ["TC-1", "TC-2"]
        assert artifact.format == "md"

    def test_invalid_artifact_empty_provenance_list(self):
        with pytest.raises(ValidationError):
            make_artifact(source_tool_calls=[])

    def test_artifact_requires_content_or_storage_ref(self):
        with pytest.raises(ValidationError):
            make_artifact(content=None, storage_ref=None)
        # storage_ref alternative is valid
        via_ref = make_artifact(content=None, storage_ref="s3://artifacts/art-1.md")
        assert via_ref.storage_ref.startswith("s3://")

    def test_markdown_only_format_week1(self):
        assert make_artifact().format == "md"
        with pytest.raises(ValidationError):
            make_artifact(format="pdf")


class TestTaskAuditContainer:
    def test_valid_task_links_plan_tools_artifacts(self):
        task = make_task()
        assert task.plan_id == "PLAN-1"
        assert task.tool_call_ids == ["TC-1"]
        assert task.artifact_ids == ["ART-1"]
        assert task.selected_skill == "timeline_investigation"

    def test_task_status_values(self):
        assert {s.value for s in TaskStatusV2} == {
            "pending", "planning", "executing", "completed", "failed", "cancelled",
        }

    def test_task_selected_skill_optional(self):
        task = make_task(selected_skill=None, plan_id=None)
        assert task.selected_skill is None
        assert task.plan_id is None


class TestEnumValidation:
    def test_invalid_plan_status_rejected(self):
        with pytest.raises(ValidationError):
            make_plan(status="waiting_around")

    def test_invalid_task_status_rejected(self):
        with pytest.raises(ValidationError):
            make_task(status="half_done")

    def test_invalid_toolcall_status_rejected(self):
        with pytest.raises(ValidationError):
            make_tool_call(status="kinda_works")

    def test_legacy_and_v2_status_enums_do_not_collide(self):
        from app.models import StepStatus as LegacyStepStatus
        from app.models import TaskStatus as LegacyTaskStatus
        # V2 enums are distinct classes that supersede, not alias, the legacy
        # ones: PlanStepStatus adds 'rejected'; TaskStatusV2 splits 'running'
        # into 'planning'/'executing' and drops draft/ready.
        assert PlanStepStatus is not LegacyStepStatus
        assert "rejected" in {m.value for m in PlanStepStatus}
        assert "rejected" not in {m.value for m in LegacyStepStatus}
        assert "planning" in {m.value for m in TaskStatusV2}
        assert "planning" not in {m.value for m in LegacyTaskStatus}
        assert "draft" in {m.value for m in LegacyTaskStatus}
        assert "draft" not in {m.value for m in TaskStatusV2}


class TestRoundTripSerialization:
    def test_full_graph_round_trip(self):
        task = make_task()
        plan = make_plan()
        call = make_tool_call()
        artifact = make_artifact()

        restored = {
            "task": TaskV2.model_validate_json(task.model_dump_json()),
            "plan": Plan.model_validate_json(plan.model_dump_json()),
            "call": ToolCallV2.model_validate_json(call.model_dump_json()),
            "artifact": ArtifactV2.model_validate_json(artifact.model_dump_json()),
        }

        assert restored["task"] == task
        assert restored["plan"] == plan
        assert restored["call"].result.outcome == ToolResultOutcome.SUCCESS
        assert restored["artifact"].source_tool_calls == ["TC-1"]

    def test_plan_with_dependency_chain_round_trip(self):
        steps = [
            make_step(step_id="S1", type="fetch_case"),
            make_step(step_id="S2", type="timeline", depends_on=["S1"]),
            make_step(step_id="S3", type="artifact", depends_on=["S2"]),
        ]
        plan = make_plan(steps=steps)
        again = Plan.model_validate_json(plan.model_dump_json())
        assert [s.step_id for s in again.steps] == ["S1", "S2", "S3"]
        assert again.steps[2].depends_on == ["S2"]

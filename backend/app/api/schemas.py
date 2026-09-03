"""Pydantic API schemas for the V2 investigation HTTP boundary.

Thin, product-oriented request/response contracts. Internal models
(InvestigationContext, TaskV2, Plan, PlanStep, ToolCallV2, ArtifactV2,
FollowUp, Investigation) are reused directly where safe; these schemas only
add what the HTTP boundary needs (requests, envelopes, summaries) and never
expose internal class names, stack traces, or raw RP payloads.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.followups import FollowUp
from app.models import (
    ArtifactV2,
    Investigation,
    InvestigationContext,
    Plan,
    TaskV2,
    ToolCallV2,
)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class CreateInvestigationRequest(BaseModel):
    """POST /api/v2/investigations.

    Two accepted forms (never both):
    - canonical:  {"case_id": "U00299"}
    - natural:    {"message": "investigate case 00299"}  (or
                  {"case_reference": "00299"}) — resolved server-side by
                  Case Reference Resolution; the frontend never normalizes.
    """

    case_id: str | None = None
    case_reference: str | None = None
    message: str | None = None
    preferences: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_source(self) -> "CreateInvestigationRequest":
        canonical = (self.case_id or "").strip()
        reference = (self.case_reference or self.message or "").strip()
        if canonical and reference:
            raise ValueError(
                "Provide either case_id or message/case_reference, not both"
            )
        if not canonical and not reference:
            raise ValueError(
                "case_id or message is required (e.g. 'Investigate U00299')"
            )
        return self


class FocusAction(BaseModel):
    """Explicit UI selection shortcut — establishes context for the turn.

    Only these server-validated shapes are accepted; the client can never
    submit capabilities, tool names, skill ids, plans, or tool calls.
    """

    type: Literal["focus_finding", "focus_event", "clear_focus"]
    finding_id: str | None = None
    event_id: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "FocusAction":
        if self.type == "focus_finding":
            if not self.finding_id or not self.finding_id.strip():
                raise ValueError("focus_finding requires finding_id")
        elif self.type == "focus_event":
            if not self.finding_id or not self.finding_id.strip():
                raise ValueError("focus_event requires finding_id")
            if not self.event_id or not self.event_id.strip():
                raise ValueError("focus_event requires event_id")
        # clear_focus: no ids needed
        return self


class TurnRequest(BaseModel):
    """POST /api/v2/investigations/{id}/turn.

    `message` is the user intent (untrusted content). `context_action` is the
    optional explicit UI selection. `follow_up_id` must exist in the
    server-side follow-up registry — the server reconstructs the canonical
    intent; the client never supplies tool/skill/argument names. When a
    follow_up_id is supplied, `message` is optional (the canonical intent IS
    the message).
    """

    message: str = Field(default="")
    context_action: FocusAction | None = None
    follow_up_id: str | None = None

    @model_validator(mode="after")
    def _validate_message(self) -> "TurnRequest":
        # A follow-up click carries the canonical intent, so a blank message
        # is fine; otherwise the message is required and must not be blank.
        if not self.follow_up_id and not self.message.strip():
            raise ValueError(
                "message is required when follow_up_id is not supplied"
            )
        return self


# ---------------------------------------------------------------------------
# Response summaries (bounded views of internal records)
# ---------------------------------------------------------------------------

class ToolCallSummary(BaseModel):
    """Execution summary for the UI/audit — no raw RP payloads, no internal
    class names. `data_preview` is a bounded, outcome-level view."""

    tool_call_id: str
    tool_name: str
    status: str
    outcome: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    summary: str | None = None
    evidence_ref_count: int = 0
    citation_ref_count: int = 0


class PlanSummary(BaseModel):
    """Plan + step statuses (no LLM prompts or internal plumbing)."""

    plan_id: str
    goal: str
    status: str
    selected_skill: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionSummary(BaseModel):
    status: str
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class TurnResponse(BaseModel):
    """Structured result of one investigation turn — everything the future
    frontend needs to render context/task/plan/execution/response/artifacts/
    follow-ups."""

    investigation_id: str
    task: TaskV2
    context: InvestigationContext
    response: str
    plan: PlanSummary | None = None
    execution: ExecutionSummary | None = None
    artifacts: list[ArtifactV2] = Field(default_factory=list)
    follow_ups: list[FollowUp] = Field(default_factory=list)
    context_changed: bool = False
    status: str                       # completed | failed | clarification_needed


class InvestigationResponse(BaseModel):
    """POST /api/v2/investigations and the investigation section of GET.

    `intake_message` is the canonical Case Intake turn request the client
    should submit as the first conversational turn (present on creation
    only). It lets the frontend start Case Intake without knowledge of the
    canonical message format and WITHOUT re-sending the raw case-reference
    text as a conversational turn.
    """

    investigation: Investigation
    context: InvestigationContext
    tasks: list[TaskV2] = Field(default_factory=list)
    intake_message: str | None = None


class InvestigationCreatedResponse(InvestigationResponse):
    pass


class InvestigationNotFound(BaseModel):
    error: dict[str, Any] = Field(
        default_factory=lambda: {"code": "INVESTIGATION_NOT_FOUND"})

    def __init__(self, investigation_id: str = "", **kw):
        super().__init__(
            error={"code": "INVESTIGATION_NOT_FOUND",
                   "message": f"Unknown investigation: {investigation_id}"},
            **kw)


class TaskNotFound(BaseModel):
    def __init__(self, task_id: str = "", **kw):
        super().__init__(
            error={"code": "TASK_NOT_FOUND",
                   "message": f"Unknown task: {task_id}"},
            **kw)


class TaskDetailResponse(BaseModel):
    """Task Center view: task + plan + tool-call summaries + artifacts +
    bounded errors."""

    task: TaskV2
    plan: PlanSummary | None = None
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    artifacts: list[ArtifactV2] = Field(default_factory=list)
    selected_skill: str | None = None
    error: str | None = None
    """Composed human-readable turn response (conversation restore). Absent
    for investigations recorded before this field existed."""
    task_response_text: str | None = None
    """This Agent message's own Suggested Follow-ups (conversation restore —
    chips belong to their specific message and survive a refresh)."""
    task_follow_ups: list["FollowUp"] = Field(default_factory=list)


def summarize_tool_call(tc: ToolCallV2) -> ToolCallSummary:
    """Bounded ToolCall view: outcome-level summary, ref counts; never raw
    Risk Platform payloads or stack traces."""
    outcome = tc.result.outcome.value if tc.result else None
    summary: str | None = None
    ev_count = len(tc.result.evidence_refs) if tc.result else 0
    cite_count = len(tc.result.citation_refs) if tc.result else 0
    if tc.result is not None:
        if outcome == "success":
            data = tc.result.data if isinstance(tc.result.data, dict) else {}
            if data.get("events") is not None:
                summary = f"timeline with {len(data['events'])} events"
            elif data.get("findings") is not None:
                summary = f"case with {len(data['findings'])} findings"
            elif data.get("rule") is not None:
                summary = f"rule explanation: {data['rule'].get('name')}"
            elif data.get("matches") is not None:
                summary = f"{len(data['matches'])} policy matches"
            elif data.get("artifact") is not None:
                summary = f"artifact {data.get('artifact_id')}"
            if data.get("evidence_missing"):
                summary = (summary + " (evidence_missing)") if summary \
                    else "evidence_missing"
        elif outcome == "empty":
            summary = "no matching data"
        elif outcome == "unsupported":
            summary = "capability not supported for current target"
        elif outcome == "integration_error":
            summary = "Risk Platform integration failure"
        elif outcome == "validation_error":
            summary = "invalid request"
    return ToolCallSummary(
        tool_call_id=tc.tool_call_id,
        tool_name=tc.tool_name,
        status=tc.status.value,
        outcome=outcome,
        started_at=tc.started_at,
        completed_at=tc.completed_at,
        error=tc.error,
        summary=summary,
        evidence_ref_count=ev_count,
        citation_ref_count=cite_count,
    )


def result_view(tc: ToolCallV2) -> dict[str, Any] | None:
    """Read-only view of a successful tool result's normalized payload —
    the normalized Agent-domain data (never raw Risk Platform shapes)."""
    if tc.result is None or tc.result.outcome.value != "success":
        return None
    data = tc.result.data
    if not isinstance(data, dict):
        return None
    view: dict[str, Any] = {"tool_call_id": tc.tool_call_id,
                            "tool_name": tc.tool_name}
    if data.get("findings") is not None:
        view["findings"] = [
            f if isinstance(f, dict) else f.model_dump()
            for f in data["findings"]
        ]
    if data.get("events") is not None:
        view["timeline_events"] = [
            e if isinstance(e, dict) else e.model_dump()
            for e in data["events"]
        ]
    if data.get("records") is not None:
        # view="evidence" result: COMPLETE concrete record set (never a
        # bounded preview) plus the feature-level aggregate facts kept
        # separate from records.
        view["evidence_records"] = {
            "records": data["records"],
            "record_count": data.get("record_count", len(data["records"])),
            "complete": bool(data.get("complete")),
            "risk_features": data.get("risk_features") or {},
            "streams": data.get("streams") or {},
        }
    if data.get("rule") is not None:
        view["signal_explanation"] = {
            "signal_type": data.get("signal_type"),
            "rule": data["rule"],
            "evidence_missing": data.get("evidence_missing", False),
            "next_data_needed": data.get("next_data_needed") or [],
        }
    if data.get("matches") is not None:
        view["policy_context"] = {
            "topic": data.get("topic"),
            "matches": data["matches"],
            "associated_policy_refs": data.get("associated_policy_refs") or [],
            "newly_retrieved_refs": data.get("newly_retrieved_refs") or [],
            "evidence_missing": data.get("evidence_missing", False),
        }
    if data.get("artifact") is not None:
        view["artifact"] = data["artifact"]
    if data.get("evidence_missing"):
        view["evidence_missing"] = True
        view["next_data_needed"] = data.get("next_data_needed") or []
    return view


def summarize_plan(plan: Plan | None, selected_skill: str | None = None) \
        -> PlanSummary | None:
    if plan is None:
        return None
    steps = [{
        "step_id": s.step_id,
        "type": s.type,
        "reason": s.reason,
        "status": s.status.value,
        "tool_name": s.tool_name,
        "arguments": s.arguments,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
        "error": s.error,
    } for s in plan.steps]
    return PlanSummary(
        plan_id=plan.plan_id,
        goal=plan.goal,
        status=plan.status.value,
        selected_skill=selected_skill,
        steps=steps,
    )


def summarize_execution(execution) -> ExecutionSummary | None:
    if execution is None:
        return None
    return ExecutionSummary(
        status=execution.status.value,
        tool_calls=[summarize_tool_call(tc) for tc in execution.tool_calls],
        errors=[e.model_dump() for e in execution.errors],
    )

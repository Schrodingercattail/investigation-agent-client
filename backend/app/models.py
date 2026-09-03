import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, RootModel, model_serializer, model_validator


class ProvenanceType(str, Enum):
    """Type of finding provenance - indicates how the finding was generated."""
    DIRECT_EVIDENCE = "direct_evidence"
    RULE_DERIVED = "rule_derived"
    ML_DERIVED = "ml_derived"
    GRAPH_DERIVED = "graph_derived"
    PRIMARY_REASON = "primary_reason"
    UNKNOWN = "unknown"


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


# ---------------------------------------------------------------------------
# V2 domain models (docs/architecture/DOMAIN_MODELS_V1.md)
#
# Slice 1: Investigation, InvestigationContext, Finding,
#          FindingCapability, TimelineEvent, ToolResult.
# Slice 2 (this section, bottom): Plan, PlanStep, and the V2 runtime
#          records ToolCallV2 / ArtifactV2 / TaskV2.
#
# Naming note: the contract names ToolCall / Artifact / Task collide with
# superseded-era classes above that current consumers still import. Because
# `from app.models import X` binds at import time, redefining those names
# here would silently switch every legacy consumer onto the new class. The
# V2 runtime records therefore carry explicit V2-suffixed names for the
# coexistence period; they map 1:1 to the contract objects and are the only
# variants new code may use. The legacy trio (plus its enums) is removable
# once the superseded agent-loop/store paths retire.
#
# Legacy models above are otherwise left untouched; their removal is a
# separate authorized cleanup step because other modules still import them.
# ---------------------------------------------------------------------------


# --- Shared reference types -------------------------------------------------

class EvidenceRef(BaseModel):
    """Reference to an underlying evidence item owned by the Risk Platform."""
    kind: str   # open string, e.g. "transaction", "withdrawal", "risk_factor"
    id: str     # Risk Platform evidence identifier (e.g. "EV-WD-001")


class SignalRef(BaseModel):
    """Reference to a detection signal behind a finding/event."""
    signal_type: str   # open string: "ML" | "Rule" | "Graph" | ... (extensible)
    name: str          # e.g. "coordinated_trading_rule", "ml_score"


class PolicyRef(BaseModel):
    """Reference to a Risk Platform citation actually returned for the case.

    Only citations returned by the Risk Platform may be mirrored here;
    no client-side policy assignment is permitted.
    """
    citation_id: int | None = None   # e.g. [1] marker id from key_findings
    chunk_id: str | None = None      # e.g. "AML_Suspicious_Indicators#2.1#001"
    doc: str | None = None           # e.g. "AML_Suspicious_Indicators.md"
    section: str | None = None       # section path as returned by RP


def _require_non_empty(value: Any, field_name: str) -> Any:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _strip_or_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


# --- 1. Investigation --------------------------------------------------------

class InvestigationStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class Investigation(BaseModel):
    """One investigation session for one case. Intentionally minimal."""

    investigation_id: str
    case_id: str
    status: InvestigationStatus = InvestigationStatus.ACTIVE

    created_at: str | None = None   # ISO-8601 UTC
    updated_at: str | None = None

    @model_validator(mode="after")
    def _validate_required_strings(self) -> "Investigation":
        _require_non_empty(self.investigation_id, "investigation_id")
        _require_non_empty(self.case_id, "case_id")
        return self


# --- 2. InvestigationContext ---------------------------------------------------

class ContextPreferences(BaseModel):
    """Minimal preference memory (the only cross-request memory in Week 1)."""
    citation_required: bool = True
    response_length: Literal["brief", "standard", "detailed"] = "standard"
    output_format: Literal["md"] = "md"


class FocusSource(str, Enum):
    """How the current focus was established (DOMAIN_MODELS_V1 §2.2)."""
    USER_SELECTED = "user_selected"
    AGENT_RESOLVED = "agent_resolved"
    SYSTEM_DEFAULT = "system_default"


class RequestIntent(str, Enum):
    """The kind of request a user turn expresses, resolved BEFORE any
    target resolution (P12/P14: routing precedes target resolution).

    - CAPABILITY_QUESTION : "what can you do?" — answered with the
      executable capability guide, no target needed.
    - ARTIFACT_CASE       : explicit artifact request, case scope.
    - ARTIFACT_FINDING    : explicit artifact request for the focused
      finding (requires one; without it the user is guided).
    - INVESTIGATION       : a case/finding investigation request that goes
      through normal target resolution.
    """
    CAPABILITY_QUESTION = "capability_question"
    ARTIFACT_CASE = "artifact_case"
    ARTIFACT_FINDING = "artifact_finding"
    INVESTIGATION = "investigation"


class InvestigationContext(BaseModel):
    """Explicit short-term memory. Serializable and inspectable — there is no
    hidden state: anything the agent knows is here or in the task log."""

    case_id: str

    focused_finding_id: str | None = None
    focused_event_id: str | None = None
    focus_source: FocusSource | None = None     # how the current focus was
                                                # established; null when no
                                                # finding/event focus exists
    time_window: str | None = None              # free-form Week 1 (e.g. "24h")
    selected_policy_ids: list[str] = Field(default_factory=list)
    preferences: ContextPreferences = Field(default_factory=ContextPreferences)

    @model_validator(mode="before")
    @classmethod
    def _normalize_focus_chain(cls, data: Any) -> Any:
        # Normalize empty strings on focus fields / source so "" and None are
        # equivalent at the boundary (UI forms often submit blanks).
        if isinstance(data, dict):
            for key in ("focused_finding_id", "focused_event_id", "time_window"):
                v = data.get(key)
                if isinstance(v, str) and not v.strip():
                    data[key] = None
            src = data.get("focus_source")
            if isinstance(src, str):
                try:
                    data["focus_source"] = FocusSource(src.strip().lower()).value
                except ValueError:
                    data["focus_source"] = None
        return data

    @model_validator(mode="after")
    def _validate_focus_chain(self) -> "InvestigationContext":
        # Invariant: an event belongs to a finding, so event focus implies
        # finding focus.
        if self.focused_event_id is not None and not self.focused_finding_id:
            raise ValueError(
                "focused_event_id cannot be set without focused_finding_id"
            )
        # focus_source describes an existing focus; it never exists alone.
        if self.focus_source is not None and (
            self.focused_finding_id is None and self.focused_event_id is None
        ):
            raise ValueError(
                "focus_source cannot be set without a focused finding/event"
            )
        return self


# --- 3/4. Finding + FindingCapability -----------------------------------------

_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class FindingCapability(RootModel[frozenset[str]]):
    """Extensible set of capability identifiers supported by one finding.

    Deliberately NOT fixed boolean fields: new capabilities attach without
    schema changes, and absence means unsupported.

    Week 1 vocabulary (not enforced as a closed enum — extensible):
        timeline, opposite_trades, signal_explain, policy_lookup
    """

    root: frozenset[str]

    @model_serializer
    def _sorted(self) -> list[str]:  # keep serialized form a deterministic sorted list
        return sorted(self.root)

    def supports(self, capability: str) -> bool:
        return capability in self.root

    def __contains__(self, capability: object) -> bool:
        return capability in self.root

    def __iter__(self):  # type: ignore[override]
        return iter(sorted(self.root))

    def __len__(self) -> int:
        return len(self.root)

    @model_validator(mode="after")
    def _validate_members(self) -> "FindingCapability":
        for cap in self.root:
            if not _CAPABILITY_PATTERN.match(cap):
                raise ValueError(
                    f"Invalid capability identifier: {cap!r}. "
                    "Must match ^[a-z][a-z0-9_]*$"
                )
        return self


class FindingSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class Finding(BaseModel):
    """Unit of investigation focus.

    Derived deterministically from authoritative risk_case_fetch output;
    never generated/renamed/re-scored client-side. `type` is deliberately
    an open string (Risk Platform owns the taxonomy) and domain-specific
    facts live in `ext` so no per-type schemas are hard-coded.
    """

    finding_id: str
    case_id: str
    type: str                                    # open string, RP-owned taxonomy
    title: str
    severity: FindingSeverity = FindingSeverity.UNKNOWN
    summary: str

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    signal_refs: list[SignalRef] = Field(default_factory=list)
    policy_refs: list[PolicyRef] = Field(default_factory=list)

    capabilities: FindingCapability               # required, explicit per finding

    ext: dict[str, Any] = Field(default_factory=dict)   # domain-specific fields

    @model_validator(mode="before")
    @classmethod
    def _normalize_severity(cls, data: Any) -> Any:
        # Accept and normalize common raw severity spellings ("HIGH", "MEDIUM").
        if isinstance(data, dict) and isinstance(data.get("severity"), str):
            sev = data["severity"].strip().lower() or None
            if sev is None:
                data["severity"] = FindingSeverity.UNKNOWN.value
            else:
                try:
                    data["severity"] = FindingSeverity(sev).value
                except ValueError:
                    data["severity"] = FindingSeverity.UNKNOWN.value
        return data

    @model_validator(mode="after")
    def _validate_required_strings(self) -> "Finding":
        _require_non_empty(self.finding_id, "finding_id")
        _require_non_empty(self.case_id, "case_id")
        _require_non_empty(self.type, "type")
        _require_non_empty(self.title, "title")
        return self


# --- 5. TimelineEvent ----------------------------------------------------------

class EventImportance(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TimelineEvent(BaseModel):
    """One event in a finding's timeline composition.

    Raw payloads stay thin in Week 1; depth comes from references, and events
    are compositions of Risk Platform evidence — never recomputed signals.
    """

    event_id: str                                # unique within investigation
    finding_id: str                              # parent finding
    timestamp: str                               # ISO-8601 UTC, drives ordering
    event_type: str                              # open string, extensible
    summary: str

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    signal_refs: list[SignalRef] = Field(default_factory=list)
    policy_refs: list[PolicyRef] = Field(default_factory=list)

    importance: EventImportance | None = None    # None = not rated

    @model_validator(mode="after")
    def _validate_required_strings(self) -> "TimelineEvent":
        _require_non_empty(self.event_id, "event_id")
        _require_non_empty(self.finding_id, "finding_id")
        _require_non_empty(self.timestamp, "timestamp")
        _require_non_empty(self.event_type, "event_type")
        return self


# --- 6. ToolResult ----------------------------------------------------------------

class ToolResultOutcome(str, Enum):
    """Outcome categories for every tool result. Critical distinctions:

    - empty             = valid execution, genuinely no matching data
                          ("no timeline events exist")
    - unsupported       = requested investigation capability is not supported
    - integration_error = external system failed ("Risk Platform timeline
                          API failed") — must NEVER be rendered as absence of data
    - validation_error  = invalid request/arguments
    - success           = valid execution returning data; available-evidence-
                          insufficiency is represented INSIDE data, not here:

        {"outcome": "success",
         "data": {"evidence_missing": true, "next_data_needed": [...]}}
    """

    SUCCESS = "success"
    UNSUPPORTED = "unsupported"
    EMPTY = "empty"
    INTEGRATION_ERROR = "integration_error"
    VALIDATION_ERROR = "validation_error"


class ToolError(BaseModel):
    code: str                      # stable machine-readable code
    message: str                   # human-readable detail
    detail: dict[str, Any] | None = None


class ToolResult(BaseModel):
    """Normalized result envelope for tool executions.

    Raw Risk Platform response shapes must not leak into this domain
    contract; adapter mapping happens once inside the tool/adapter layer.
    """

    outcome: ToolResultOutcome
    data: Any | None = None                    # payload shaped by the tool contract
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    citation_refs: list[PolicyRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ToolError | None = None
    next_data_needed: list[str] | None = None  # accompanies data.evidence_missing

    @model_validator(mode="after")
    def _validate_outcome_invariants(self) -> "ToolResult":
        # Error outcomes require an error record describing what went wrong.
        error_outcomes = {
            ToolResultOutcome.UNSUPPORTED,
            ToolResultOutcome.INTEGRATION_ERROR,
            ToolResultOutcome.VALIDATION_ERROR,
        }
        if self.outcome in error_outcomes and self.error is None:
            raise ValueError(
                f"outcome={self.outcome.value} requires an error record"
            )
        # Successful-looking outcomes (valid executions) must not carry an
        # error record — non-fatal nuance belongs in `warnings`.
        if self.outcome in (ToolResultOutcome.SUCCESS, ToolResultOutcome.EMPTY) \
                and self.error is not None:
            raise ValueError(
                f"outcome={self.outcome.value} is a valid execution and must "
                "not carry an error record (use warnings instead)"
            )
        if self.next_data_needed is not None and self.data is None:
            raise ValueError(
                "next_data_needed requires a data payload (it accompanies "
                "data.evidence_missing on outcome=success)"
            )
        return self


# --- 7. Plan -------------------------------------------------------------------

class PlanStatus(str, Enum):
    """Lightweight plan lifecycle: draft → validating → ready → executing →
    completed; rejected/failed are terminal. No workflow engine — these states
    only need to be representable and auditable."""

    DRAFT = "draft"
    VALIDATING = "validating"
    REJECTED = "rejected"
    READY = "ready"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class Plan(BaseModel):
    """Structured plan produced by the LLM planner for ONE user request /
    investigation turn.

    Vocabulary constraint (representational only at the model layer): step
    types come from the registered planning vocabulary and tools from the
    selected Skill's allowed_tools — both enforced later by the planner /
    validator / executor, never by LLM invention.
    """

    plan_id: str
    investigation_id: str
    goal: str
    steps: list["PlanStep"] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_at: str | None = None           # ISO-8601 UTC
    plan_version: int = 1                   # supports per-turn regeneration

    @model_validator(mode="after")
    def _validate_required_strings(self) -> "Plan":
        _require_non_empty(self.plan_id, "plan_id")
        _require_non_empty(self.investigation_id, "investigation_id")
        _require_non_empty(self.goal, "goal")
        return self


# --- 8. PlanStep -----------------------------------------------------------------

class PlanStepStatus(str, Enum):
    """Per-step execution status.

    `rejected` = plan-validation-level rejection of this step;
    `skipped`  = execution-time precondition failure (reason recorded in
    `error`). These remain semantically distinct by design
    (DOMAIN_MODELS_V1 §2.7).
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class PlanStep(BaseModel):
    """Planning-level intent for one step of a plan.

    `type` is an open string at the model layer but must belong to the
    registered planning vocabulary (e.g. fetch_case, timeline,
    opposite_trades, signal_explain, policy_lookup, artifact) — enforcement
    lives in the registry-driven validator, not here. No Skill logic is
    encoded on this model; skill rules come from the Skill Registry.
    """

    step_id: str
    type: str                                    # registry-vocabulary identifier
    reason: str | None = None                    # why this step was planned
    status: PlanStepStatus = PlanStepStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)   # prerequisite step_ids

    tool_name: str | None = None                 # resolved tool binding; must be
                                                 # allowed by the selected Skill
    arguments: dict[str, Any] = Field(default_factory=dict)   # structured data,
                                                              # never a free-form
                                                              # execution string

    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None                     # failure / rejection detail

    @model_validator(mode="after")
    def _validate_required_strings(self) -> "PlanStep":
        _require_non_empty(self.step_id, "step_id")
        _require_non_empty(self.type, "type")
        return self


# --- 9. ToolCall (V2 execution audit record) ---------------------------------------

class ToolCallStatusV2(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ToolCallV2(BaseModel):
    """Actual execution-level audit record for one tool invocation.

    Contract name: ToolCall. Suffixed V2 during coexistence with the
    superseded-era ToolCall above (see section header note).

    The canonical result is the normalized ToolResult envelope — raw Risk
    Platform response shapes are never stored as the result. A ToolCall is
    NOT a PlanStep: steps carry intent; calls carry what actually ran.
    """

    tool_call_id: str
    investigation_id: str                # denormalized for audit queries
    task_id: str                         # denormalized for audit queries
    tool_name: str                       # must exist in TOOL_REGISTRY
    arguments: dict[str, Any] = Field(default_factory=dict)

    status: ToolCallStatusV2 = ToolCallStatusV2.RUNNING
    started_at: str | None = None
    completed_at: str | None = None

    result: ToolResult | None = None     # normalized envelope
    error: str | None = None             # execution-level error summary

    @model_validator(mode="after")
    def _validate_required_strings(self) -> "ToolCallV2":
        _require_non_empty(self.tool_call_id, "tool_call_id")
        _require_non_empty(self.investigation_id, "investigation_id")
        _require_non_empty(self.task_id, "task_id")
        _require_non_empty(self.tool_name, "tool_name")
        return self


# --- 10. Artifact (V2 persisted output) ----------------------------------------------

class ArtifactTypeV2(str, Enum):
    """Week 1 artifact categories (open to extension without schema change)."""
    FINDINGS_SUMMARY = "findings_summary"
    TIMELINE = "timeline"
    SIGNAL_EXPLANATION = "signal_explanation"
    POLICY_REQUIREMENTS = "policy_requirements"
    ACTION_CHECKLIST = "action_checklist"
    INVESTIGATION_NOTES = "investigation_notes"


class ArtifactV2(BaseModel):
    """Reusable investigation output with mandatory provenance.

    Contract name: Artifact. Suffixed V2 during coexistence with the
    superseded-era Artifact above (see section header note).

    Provenance invariant: source_tool_calls must reference actual ToolCallV2
    records of the same task; provenance closure is enforced where artifacts
    are composed (artifact service), while this model guarantees the fields
    exist and are non-empty when content is present.
    """

    artifact_id: str
    investigation_id: str
    task_id: str
    scope: str                                   # e.g. "case:U00299", "finding:F3"
    artifact_type: ArtifactTypeV2
    format: Literal["md"] = "md"                 # Week 1: Markdown only
    title: str

    content: str | None = None                   # rendered Markdown body
    storage_ref: str | None = None               # external pointer alternative

    source_tool_calls: list[str] = Field(min_length=1)   # tool_call_ids; provenance

    created_at: str | None = None

    @model_validator(mode="after")
    def _validate_content_or_ref(self) -> "ArtifactV2":
        _require_non_empty(self.artifact_id, "artifact_id")
        _require_non_empty(self.investigation_id, "investigation_id")
        _require_non_empty(self.task_id, "task_id")
        _require_non_empty(self.scope, "scope")
        _require_non_empty(self.title, "title")
        if not self.content and not self.storage_ref:
            raise ValueError(
                "Artifact requires either content or storage_ref"
            )
        if self.content == "" or self.storage_ref == "":
            raise ValueError("content/storage_ref must not be empty strings")
        return self


# --- 11. Task (V2 audit container) -----------------------------------------------------

class TaskStatusV2(str, Enum):
    """Task lifecycle for one user-requested investigation turn."""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskV2(BaseModel):
    """Auditable unit of work shown in Task Center — one investigation
    request/turn end to end.

    Contract name: Task. Suffixed V2 during coexistence with the superseded-era
    Task above (see section header note).

    Audit container tying the work together: selected_skill records which
    skill scoped this turn's planning vocabulary; plan/tool_calls/artifacts
    are indexed by id (records live in their own stores).
    """

    task_id: str
    investigation_id: str
    type: str = "investigation_turn"             # open string; Week 1 default

    status: TaskStatusV2 = TaskStatusV2.PENDING
    user_request: str

    plan_id: str | None = None
    selected_skill: str | None = None            # skill_id from Skill Registry

    tool_call_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)

    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _validate_required_strings(self) -> "TaskV2":
        _require_non_empty(self.task_id, "task_id")
        _require_non_empty(self.investigation_id, "investigation_id")
        _require_non_empty(self.user_request, "user_request")
        return self
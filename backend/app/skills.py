"""Week 1 Skill Registry and Contract Checker.

Implements the normative skill definitions from
docs/architecture/SKILL_MODEL_V1.md as static, data-driven records —
no runtime class hierarchy, no execution framework.

Boundary (enforced by structure):

- The future LLM Planner receives ONLY the eligible skills and their
  planning vocabulary (`eligible_skills_for_finding`, `SKILLS`,
  `PlanningVocabulary`). It is never trusted to invent skill ids, step
  types, tool names, or fixed parameters: every such identifier must come
  from this registry and survive `check_plan` validation.
- The deterministic `check_plan` is reusable both planner-side (before a
  plan is accepted) and by the future executor/runtime (defense in depth).
- Contract violations are structured `ContractViolation` results, NOT
  exceptions for ordinary violations, and NOT ToolResult outcomes:
  plan validation never pretends a Risk Platform tool ran.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.models import FindingCapability, PlanStep


def _supports(capabilities: Any, capability: str) -> bool:
    """Capability membership across FindingCapability / raw sets (None = no
    capabilities at all)."""
    if capabilities is None:
        return False
    supports = getattr(capabilities, "supports", None)
    if supports is not None:
        return supports(capability)
    return capability in capabilities


# ---------------------------------------------------------------------------
# Static step → tool mapping (single source of truth; no arbitrary mappings)
# ---------------------------------------------------------------------------

class StepToolBinding(BaseModel):
    """Maps one planning-step type to its executing tool.

    `parameter_locks` pin tool arguments the Planner may not change,
    e.g. finding_drilldown under timeline_investigation is locked to
    view="timeline".
    """

    tool_name: str
    parameter_locks: dict[str, Any] = Field(default_factory=dict)


STEP_TOOL_MAP: dict[str, StepToolBinding] = {
    "fetch_case": StepToolBinding(tool_name="risk_case_fetch"),
    "generate_artifact": StepToolBinding(tool_name="artifact_bundle"),
    "inspect_timeline": StepToolBinding(
        tool_name="finding_drilldown",
        parameter_locks={"view": "timeline"},
    ),
    "inspect_opposite_trades": StepToolBinding(
        tool_name="finding_drilldown",
        parameter_locks={"view": "opposite_trades"},
    ),
    "explain_signal": StepToolBinding(tool_name="signal_explain"),
    "retrieve_policy": StepToolBinding(tool_name="policy_lookup"),
}


# ---------------------------------------------------------------------------
# Skill definition (declarative record — deliberately not a class hierarchy)
# ---------------------------------------------------------------------------

class SkillDefinition(BaseModel):
    """One Week 1 skill. Plain validated data; registry entries are values."""

    skill_id: str
    name: str
    description: str
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str]
    planning_steps: list[str]
    constraints: list[str] = Field(default_factory=list)


SKILLS: dict[str, SkillDefinition] = {
    "case_intake": SkillDefinition(
        skill_id="case_intake",
        name="Case Intake",
        description=(
            "Fetch the authoritative picture of a case and produce an "
            "intake summary."
        ),
        required_capabilities=[],
        allowed_tools=["risk_case_fetch", "artifact_bundle"],
        planning_steps=["fetch_case", "generate_artifact"],
        constraints=[
            "Runs at case level; ignores focused_finding_id.",
            "If no cached case context exists for the session, fetch_case "
            "must precede generate_artifact.",
            "generate_artifact may compose only from steps executed in "
            "this plan.",
        ],
    ),
    "timeline_investigation": SkillDefinition(
        skill_id="timeline_investigation",
        name="Timeline Investigation",
        description=(
            "Investigate a focused finding through its chronological "
            "evidence timeline."
        ),
        required_capabilities=["timeline"],
        allowed_tools=[
            "finding_drilldown",   # locked to view="timeline"
            "signal_explain",
            "policy_lookup",
            "artifact_bundle",
        ],
        planning_steps=[
            "inspect_timeline",
            "explain_signal",
            "retrieve_policy",
            "generate_artifact",
        ],
        constraints=[
            "Requires InvestigationContext.focused_finding_id.",
            "Executable only if the focused finding declares capability "
            "'timeline'.",
            "finding_drilldown is locked to view='timeline'; any other "
            "view is invalid under this skill.",
        ],
    ),
    "trade_investigation": SkillDefinition(
        skill_id="trade_investigation",
        name="Trade Investigation",
        description=(
            "Investigate a focused finding through the trades composing "
            "its opposite-trade signal."
        ),
        required_capabilities=["opposite_trades"],
        allowed_tools=[
            "finding_drilldown",   # locked to view="opposite_trades"
            "signal_explain",
            "policy_lookup",
            "artifact_bundle",
        ],
        planning_steps=[
            "inspect_opposite_trades",
            "explain_signal",
            "retrieve_policy",
            "generate_artifact",
        ],
        constraints=[
            "Requires InvestigationContext.focused_finding_id.",
            "Executable only if the focused finding declares capability "
            "'opposite_trades'.",
            "finding_drilldown is locked to view='opposite_trades'; any "
            "other view is invalid under this skill.",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Planning vocabulary handed to the LLM Planner (it sees nothing else)
# ---------------------------------------------------------------------------

class PlanningVocabulary(BaseModel):
    """What the LLM Planner is allowed to know/produce for one selection."""

    skill_id: str
    required_capabilities: list[str]
    allowed_tools: list[str]
    planning_steps: list[str]


def planning_vocabulary(skill_id: str) -> PlanningVocabulary | None:
    """The closed vocabulary for one skill — the complete surface exposed to
    the LLM Planner for that selection."""
    skill = SKILLS.get(skill_id)
    if skill is None:
        return None
    return PlanningVocabulary(
        skill_id=skill.skill_id,
        required_capabilities=skill.required_capabilities,
        allowed_tools=skill.allowed_tools,
        planning_steps=skill.planning_steps,
    )


def eligible_skills_for_finding(
    capabilities: FindingCapability | set[str] | frozenset[str] | None,
) -> list[SkillDefinition]:
    """Skills whose capability gates are satisfied.

    case-level skills (empty required_capabilities) are always included.
    This is the eligibility list the Planner selects from.
    """
    return [
        s for s in SKILLS.values()
        if all(_supports(capabilities, c) for c in s.required_capabilities)
    ]


# ---------------------------------------------------------------------------
# Deterministic contract checker
# ---------------------------------------------------------------------------

class ContractViolation(BaseModel):
    """One structured contract violation. Codes are stable identifiers."""

    code: str          # SKILL_NOT_FOUND | CAPABILITY_NOT_SUPPORTED |
                       # STEP_NOT_ALLOWED | TOOL_NOT_ALLOWED |
                       # PARAMETER_LOCK_VIOLATION | INVALID_STEP_TOOL_MAPPING
    step_id: str | None = None
    message: str


class PlanValidationResult(BaseModel):
    """Structured result of plan-contract validation.

    Design note: ordinary contract violations land here as structured
    errors — callers decide how to react (planner-side reject, runtime
    defense). Only truly unexpected situations raise. These are NOT
    ToolResult outcomes; no tool execution is implied.
    """

    valid: bool
    errors: list[ContractViolation] = Field(default_factory=list)

    def add(self, code: str, message: str, step_id: str | None = None) -> None:
        self.errors.append(ContractViolation(code=code, step_id=step_id, message=message))
        self.valid = False


def check_skill_eligibility(
    skill_id: str,
    capabilities: FindingCapability | set[str] | frozenset[str] | None,
) -> PlanValidationResult:
    """Rules 1–2: skill exists; required capabilities supported by the
    current Finding. Independent of any concrete plan/steps."""
    result = PlanValidationResult(valid=True)
    skill = SKILLS.get(skill_id)
    if skill is None:
        result.add("SKILL_NOT_FOUND", f"Unknown skill: {skill_id!r}")
        return result

    for cap in skill.required_capabilities:
        if not _supports(capabilities, cap):
            result.add(
                "CAPABILITY_NOT_SUPPORTED",
                f"Skill {skill.skill_id!r} requires capability {cap!r} which the "
                "current finding does not support.",
            )
    return result


def check_plan(
    skill_id: str,
    steps: list[PlanStep],
    capabilities: FindingCapability | set[str] | frozenset[str] | None = None,
) -> PlanValidationResult:
    """Validate a full plan against the selected skill's contract.

    Rules enforced:
      1. The selected skill exists.                          (SKILL_NOT_FOUND)
      2. Required capabilities are provided by the current Finding.
                                                             (CAPABILITY_NOT_SUPPORTED)
      3. Every PlanStep.type ∈ skill.planning_steps.         (STEP_NOT_ALLOWED)
      4. Every PlanStep.tool_name matches the registered step→tool mapping.
                                                             (INVALID_STEP_TOOL_MAPPING)
      5. Locked arguments match parameter locks exactly on the locked keys;
             absence of a lock key or mismatch both violate. (PARAMETER_LOCK_VIOLATION)
      6. No tool outside skill.allowed_tools is invoked.     (TOOL_NOT_ALLOWED)

    Reusable planner-side and by the future executor (defense in depth).
    """
    result = PlanValidationResult(valid=True)

    # Rule 1
    skill = SKILLS.get(skill_id)
    if skill is None:
        result.add("SKILL_NOT_FOUND", f"Unknown skill: {skill_id!r}")
        return result

    # Rule 2
    for cap in skill.required_capabilities:
        if not _supports(capabilities, cap):
            result.add(
                "CAPABILITY_NOT_SUPPORTED",
                f"Skill {skill.skill_id!r} requires capability {cap!r} which "
                "the current finding does not support.",
                step_id=None,
            )

    allowed_tools = set(skill.allowed_tools)
    vocabulary = set(skill.planning_steps)

    for step in steps:
        # Rule 3 — step type belongs to this skill's planning vocabulary
        if step.type not in vocabulary:
            result.add(
                "STEP_NOT_ALLOWED",
                f"Step type {step.type!r} is not in planning_steps of skill "
                f"{skill.skill_id!r}.",
                step_id=step.step_id,
            )
            continue  # mapping/lock checks are meaningless for unknown types

        binding = STEP_TOOL_MAP.get(step.type)
        if binding is None:
            # Registered in vocabulary but unmapped — registry integrity issue.
            result.add(
                "INVALID_STEP_TOOL_MAPPING",
                f"Step type {step.type!r} has no registered tool binding.",
                step_id=step.step_id,
            )
            continue

        # Rule 4 — declared tool matches the registered mapping exactly
        if step.tool_name != binding.tool_name:
            result.add(
                "INVALID_STEP_TOOL_MAPPING",
                f"Step {step.step_id!r} of type {step.type!r} must bind to "
                f"{binding.tool_name!r}, got {step.tool_name!r}.",
                step_id=step.step_id,
            )

        # Rule 6 — no tool outside the skill's allowed set
        if binding.tool_name not in allowed_tools:
            result.add(
                "TOOL_NOT_ALLOWED",
                f"Tool {binding.tool_name!r} bound to step {step.step_id!r} is "
                f"not allowed by skill {skill.skill_id!r}.",
                step_id=step.step_id,
            )
        elif step.tool_name is not None and step.tool_name not in allowed_tools:
            result.add(
                "TOOL_NOT_ALLOWED",
                f"Tool {step.tool_name!r} is not allowed by skill "
                f"{skill.skill_id!r}.",
                step_id=step.step_id,
            )

        # Rule 5 — parameter locks: lock key must be present with exact value
        for lock_key, lock_value in binding.parameter_locks.items():
            actual = step.arguments.get(lock_key)
            if actual != lock_value:
                result.add(
                    "PARAMETER_LOCK_VIOLATION",
                    f"Step {step.step_id!r}: argument {lock_key!r} is locked "
                    f"to {lock_value!r} under skill {skill.skill_id!r}, "
                    f"got {actual!r}.",
                    step_id=step.step_id,
                )

    return result

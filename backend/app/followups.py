"""Week 1 Suggested Follow-ups selection layer.

Implements the logical contract in docs/architecture/FOLLOW_UP_MODEL_V1.md:

  result/context → candidate templates → capability filtering →
  context/scope filtering → trigger filtering → executable-only rule →
  deterministic ordering → maximum 3

Deterministic and capability-aware. No LLM, no analytics, no direct tool
execution: a follow-up is a next-turn investigation entry point — clicking
one creates a normal structured user intent which flows through the full
pipeline (Context Resolution → Skill Selection → LLM Planner → Contract
Checker → Executor → Task Log) exactly like free-form input. It does NOT
call the target tool directly, and merely displaying follow-ups never
mutates context.
"""

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models import FindingCapability, FocusSource, InvestigationContext
from app.skills import SKILLS, STEP_TOOL_MAP, check_skill_eligibility

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class FollowUpApplicableContext(str, Enum):
    FINDING = "finding"
    TIMELINE_EVENT = "timeline_event"
    CASE = "case"


class TriggerReason(str, Enum):
    """Valid continuation points (FOLLOW_UP_MODEL_V1 §4). Follow-ups are
    never produced for clarification/ambiguity, plain conversation without a
    structured result, or artifact editing/review — those states are simply
    not passed here."""
    TASK_COMPLETED = "task_completed"
    FOCUS_CHANGED = "focus_changed"
    EVIDENCE_MISSING = "evidence_missing"
    SUCCESSFUL_TOOL_RESULT = "successful_tool_result"


class FollowUpActionKind(str, Enum):
    """Semantic kind of a suggested follow-up (P13): the UI must never
    submit a navigation chip as an Agent request.

    - AGENT_INTENT  : clicking submits the canonical intent as the next
                      user turn (normal investigation entry point).
    - UI_NAVIGATION : clicking performs a pure UI action (open/focus a
                      panel); no turn, no Task, no Planner involvement.
    """
    AGENT_INTENT = "agent_intent"
    UI_NAVIGATION = "ui_navigation"


class FollowUp(BaseModel):
    """One suggested next-step entry point (FOLLOW_UP_MODEL_V1).

    `action_kind` keeps display guidance, Agent actions, and UI navigation
    semantically distinct:
    - agent_intent  → the UI submits `intent` as the next user request.
    - ui_navigation → the UI performs the navigation and sends NOTHING to
      the Agent; `intent` is documentation for humans/tests only.
    """

    follow_up_id: str
    label: str
    intent: str                        # canonical intent text for the next turn
    required_capabilities: list[str] = Field(default_factory=list)
    applicable_context: FollowUpApplicableContext
    target_skill: str | None = None    # hint only; planner stays authoritative
    target_step: str | None = None     # hint only
    reason: str | None = None
    action_kind: FollowUpActionKind = FollowUpActionKind.AGENT_INTENT


# ---------------------------------------------------------------------------
# Candidate templates (deterministic, Week 1)
#
# `intent` is the request text the UI submits when the user clicks the chip —
# a normal user turn, not a tool invocation. Ordering rank implements the
# contract's preference: direct next action → evidence/explanation →
# policy/artifact continuation.
# ---------------------------------------------------------------------------

class CandidateTemplate(BaseModel):
    follow_up_id: str
    label: str
    intent: str
    required_capabilities: list[str]
    applicable_context: FollowUpApplicableContext
    target_skill: str
    target_step: str
    rank: int                          # lower = offered first
    reason: str
    action_kind: FollowUpActionKind = FollowUpActionKind.AGENT_INTENT


FINDING_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        follow_up_id="explain_finding",
        label="Why is this finding flagged?",
        intent="Why was this finding flagged? Explain its detection signals.",
        required_capabilities=["signal_explain"],
        applicable_context=FollowUpApplicableContext.FINDING,
        target_skill="timeline_investigation",
        target_step="explain_signal",
        rank=1,                        # direct next investigation action
        reason="Understand the detection basis of the focused finding.",
    ),
    CandidateTemplate(
        follow_up_id="show_timeline",
        label="Show related timeline",
        intent="Show the timeline of this finding.",
        required_capabilities=["timeline"],
        applicable_context=FollowUpApplicableContext.FINDING,
        target_skill="timeline_investigation",
        target_step="inspect_timeline",
        rank=2,                        # evidence view
        reason="See the chronological evidence behind the finding.",
    ),
    CandidateTemplate(
        follow_up_id="check_policy",
        label="Which policy requirements apply?",
        intent="Which policy requirements apply to this finding?",
        # No capability gate: policy retrieval is case-wide, so this
        # follow-up is executable for every focused finding. It routes
        # through case_intake (always eligible — no required capabilities),
        # whose retrieve_policy step is the registry's case-wide policy
        # continuation; whether the finding carries a finding-level policy
        # basis is answered by the result itself (finding_policy_status) —
        # a data answer, never a dead button (P13/P19).
        required_capabilities=[],
        applicable_context=FollowUpApplicableContext.FINDING,
        target_skill="case_intake",
        target_step="retrieve_policy",
        rank=3,                        # policy continuation
        reason="Ground the finding in applicable policy requirements.",
    ),
    # NOTE: show_evidence / next_actions finding-level candidates from the
    # contract are intentionally ABSENT — their target steps do not resolve
    # to an implemented tool path distinct from explain_signal/inspect_timeline
    # (show_evidence) or a dedicated checklist generation step (next_actions).
    # Per the executable-only rule they would be dead buttons, so they are
    # not defined as Week 1 candidates.
]

# Case-level template — executable now that artifact_bundle is implemented.
# Case-level follow-ups require no finding focus and no capabilities.
CASE_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        follow_up_id="check_artifact",
        # §10 + P13: this is a UI NAVIGATION action — it opens/focuses the
        # Artifacts panel to show the existing bundle. It is never submitted
        # to the Agent as a natural-language request. (If no bundle exists
        # yet the panel says so; the user can then ask for one explicitly.)
        label="Check investigation bundle in the Artifacts",
        intent="(navigates to the Artifacts panel — not an Agent request)",
        required_capabilities=[],
        applicable_context=FollowUpApplicableContext.CASE,
        target_skill="case_intake",
        target_step="generate_artifact",
        rank=4,                        # artifact continuation (last preference)
        reason="Inspect the investigation bundle in the Artifacts panel.",
        action_kind=FollowUpActionKind.UI_NAVIGATION,
    ),
]

EVENT_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        follow_up_id="explain_event",
        label="Why is this event important?",
        intent="Why is this event important for this finding?",
        required_capabilities=["signal_explain"],
        applicable_context=FollowUpApplicableContext.TIMELINE_EVENT,
        target_skill="timeline_investigation",
        target_step="explain_signal",
        rank=1,
        reason="Understand this event's role in the finding.",
    ),
    CandidateTemplate(
        follow_up_id="related_events",
        label="Show related events",
        intent="Show related events for this finding's timeline.",
        required_capabilities=["timeline"],
        applicable_context=FollowUpApplicableContext.TIMELINE_EVENT,
        target_skill="timeline_investigation",
        target_step="inspect_timeline",
        rank=2,
        reason="See neighboring events in the timeline.",
    ),
    # verify_next event-level candidate is ABSENT: no distinct implemented
    # step backs it. (export_artifact lives in CASE_TEMPLATES — it is a
    # case-level artifact continuation, executable via generate_artifact.)
]


# ---------------------------------------------------------------------------
# Executability
# ---------------------------------------------------------------------------

def _implemented_tools() -> set[str]:
    """Names of tools with a real execution path in the current provider.

    Imported lazily to avoid a cycle; the provider is the single authority
    on what is actually executable this build.
    """
    try:
        from app.executor_v2 import default_tool_provider
        return {name for name in (
            "risk_case_fetch", "finding_drilldown",
            "signal_explain", "policy_lookup", "artifact_bundle",
        ) if default_tool_provider().has(name)}
    except Exception:                  # provider unavailable → nothing executable
        return set()


def _is_executable(
    template: CandidateTemplate,
    implemented: set[str],
    finding_capabilities: set[str] | None = None,
) -> bool:
    """Executable-only rule (FOLLOW_UP_MODEL_V1 §3) — SINGLE eligibility
    truth:
      1. target skill exists,
      2. target skill is ELIGIBLE for the current finding — the SAME
         registry check the planner's candidate list uses
         (SKILLS' required_capabilities via check_skill_eligibility). A
         chip whose skill cannot be planned for this finding is a dead
         button (P13) that would fail SKILL_NOT_ELIGIBLE, so it must not
         be offered (P19).
      3. target step exists in that skill's planning vocabulary,
      4. step resolves to a registered tool binding,
      5. for templates targeting tools without a live execution path this
         build, the candidate is omitted (no dead buttons).

    Case-level templates (target_skill = case_intake, no required
    capabilities) are eligible for every finding, so check_policy — whose
    own required_capabilities are empty and whose skill-eligibility now
    depends only on the target skill's gates — follows the same rule with
    no per-chip special cases.
    """
    skill = SKILLS.get(template.target_skill)
    if skill is None:
        return False
    # Skill eligibility: the planner's own registry truth (one owner).
    caps = (FindingCapability.model_validate(sorted(finding_capabilities))
            if finding_capabilities is not None else None)
    if not check_skill_eligibility(
            template.target_skill, caps).valid:
        return False
    if template.target_step not in skill.planning_steps:
        return False
    binding = STEP_TOOL_MAP.get(template.target_step)
    if binding is None:
        return False
    # The core trio's tools (finding_drilldown implemented; signal_explain /
    # policy_lookup planned steps with registry bindings) count as
    # executable paths; a tool that is neither implemented NOR registered
    # can never pass.
    if binding.tool_name not in implemented:
        # allow only if the step is part of the core product surface
        core_surface = {"explain_signal", "inspect_timeline", "retrieve_policy"}
        if template.target_step not in core_surface:
            return False
    return True


# ---------------------------------------------------------------------------
# Selection pipeline
# ---------------------------------------------------------------------------

class SelectionInput(BaseModel):
    """Everything selection needs — deliberately explicit so the same inputs
    always produce the same suggestions (determinism)."""

    trigger: TriggerReason
    context: InvestigationContext
    finding_capabilities: list[str] = Field(default_factory=list)
    # result metadata sufficient to identify the continuation point; opaque
    # to selection logic beyond pass-through (kept for API forward-compat)
    result_metadata: dict[str, Any] = Field(default_factory=dict)


def select_followups(selection: SelectionInput) -> list[FollowUp]:
    """Deterministic follow-up selection (max 3).

    Pipeline: candidate templates → capability filtering → context/scope
    filtering → executable-only rule → stable ordering → max 3.
    """
    ctx = selection.context
    caps = set(selection.finding_capabilities)
    implemented = _implemented_tools()

    # --- context/scope filtering --------------------------------------------
    templates: list[CandidateTemplate] = []
    if ctx.focused_finding_id:
        templates.extend(FINDING_TEMPLATES)
        if ctx.focused_event_id:
            templates.extend(EVENT_TEMPLATES)
    # Case-level candidates need no focus (FOLLOW_UP_MODEL_V1 §5): they are
    # offered at valid continuation points regardless of focus state.
    templates.extend(CASE_TEMPLATES)

    suggestions: list[FollowUp] = []
    for t in templates:
        # capability filtering (FindingCapability values are the truth; passed
        # in as plain ids to keep this module decoupled from Finding storage)
        if not all(c in caps for c in t.required_capabilities):
            continue
        # executable-only rule — includes target-skill eligibility against
        # the same finding capabilities the planner will use (one truth)
        if not _is_executable(t, implemented, caps):
            continue

        # intent text: event-level intents reference the focused event id so
        # the next turn's Context Resolution has an unambiguous anchor
        intent = t.intent
        if t.applicable_context == FollowUpApplicableContext.TIMELINE_EVENT \
                and ctx.focused_event_id:
            intent = f"[event: {ctx.focused_event_id}] {intent}"

        suggestions.append(FollowUp(
            follow_up_id=t.follow_up_id,
            label=t.label,
            intent=intent,
            required_capabilities=list(t.required_capabilities),
            applicable_context=t.applicable_context,
            target_skill=t.target_skill,
            target_step=t.target_step,
            reason=t.reason,
            action_kind=t.action_kind,
        ))

    # stable ordering: contract preference rank, then declaration order
    suggestions.sort(key=lambda f: _sort_key(f.follow_up_id))
    return suggestions[:MAX_SUGGESTIONS]


_ORDER_INDEX = {t.follow_up_id: i for i, t in
                enumerate(FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES)}


def _sort_key(follow_up_id: str) -> tuple:
    """Deterministic ordering: (contract rank, declaration order). Ranks
    encode the contract preference — direct next action (1), evidence view
    (2), policy continuation (3). Event templates rank within their own
    scope (rank 1 event suggestions surface only when no finding-scope
    template outranks them, which the event-intent prefix makes distinct)."""
    template = next(
        (t for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES
         if t.follow_up_id == follow_up_id), None)
    if template is None:
        return (99, follow_up_id)
    return (template.rank, _ORDER_INDEX[follow_up_id])

"""InvestigationService — Week 1 end-to-end orchestration glue.

One investigation turn:

  User Request
  → Context Resolution            (ContextResolver; ambiguous/unresolved stop here)
  → eligible Skill calculation    (Skill Registry × FindingCapability — no rule duplication)
  → LLM Planning                  (PlannerV2; PlanningFailure stops here)
  → Plan Validation               (inside ExecutorV2's runtime contract check)
  → Execution                     (ExecutorV2; the service never calls tools directly)
  → Response composition          (deterministic composer over ToolResults)
  → Suggested Follow-ups          (follow-up selector, only after a valid
                                   structured result at a continuation point)
  → Context Update                (explicit: only resolution-applied focus)
  → Task persistence              (TaskStoreV2)

This module is glue only: no domain reasoning, no direct tool calls, no
second task store, no context mutation beyond what Context Resolution
established. Every failure semantic from the underlying components is
preserved distinctly (planning failure / contract failure / executor failure /
integration_error / empty / success+evidence_missing).
"""

import logging
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.context_resolution import ContextResolver, ResolutionStatus
from app.executor_v2 import ExecutorV2, ExecutionResult
from app.followups import (
    SelectionInput,
    TriggerReason,
    FollowUp,
    select_followups,
)
from app.models import (
    Finding,
    InvestigationContext,
    Plan,
    PlanStatus,
    TaskStatusV2,
    TaskV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2, PlanningFailure
from app.skills import SKILLS, eligible_skills_for_finding
from app.task_store_v2 import TaskStoreV2

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response composition (deterministic; grounded in ToolResults only)
# ---------------------------------------------------------------------------

def compose_response(
    *,
    user_request: str,
    skill_id: str | None,
    plan: Plan | None,
    tool_calls: list[ToolCallV2],
    execution_errors: list[Any],
    planning_failure: PlanningFailure | None = None,
    outcome_status: str | None = None,
    clarification_message: str | None = None,
) -> str:
    """Transform structured results into a concise human-readable response.

    Rules: never invent facts/timestamps/entities; preserve evidence and
    citation references; surface evidence_missing explicitly; keep empty and
    integration failure distinct; no new domain reasoning.
    """
    if clarification_message:
        return clarification_message

    if planning_failure is not None:
        detail = f" (detail: {planning_failure.message})"
        if planning_failure.code == "NO_ELIGIBLE_SKILL":
            return ("I couldn't plan this request: no eligible investigation "
                    f"skill is available for the current context.{detail}")
        if planning_failure.code == "UNSUPPORTED_REQUEST":
            return ("I couldn't plan this request: it needs a focused finding "
                    "or capability that isn't currently available."
                    f"{detail}")
        return (f"I couldn't plan this request "
                f"[{planning_failure.code}].{detail} No tools were executed.")

    fatal = next((e for e in execution_errors if e is not None), None)
    if fatal is not None:
        return (f"The investigation step could not be completed "
                f"[{fatal.code}]: {fatal.message} "
                "The task is recorded as failed; nothing was fabricated.")

    if not tool_calls:
        return ("No investigation step could be executed for this request.")

    lines: list[str] = []
    evidence_ids: list[str] = []
    citations: list[str] = []

    for tc in tool_calls:
        result = tc.result
        if result is None:
            continue

        if result.outcome == ToolResultOutcome.SUCCESS:
            data = result.data if isinstance(result.data, dict) else {}
            if data.get("evidence_missing"):
                lines.append(
                    "The available evidence is insufficient to fully answer "
                    "this (evidence_missing)."
                )
                if result.next_data_needed:
                    lines.append(
                        "Additional data needed: " + "; ".join(result.next_data_needed)
                        + "."
                    )
            # timeline payload
            events = data.get("events") or []
            for ev in events:
                stamp = ev.timestamp if hasattr(ev, "timestamp") else ev.get("timestamp")
                summary = (ev.summary if hasattr(ev, "summary")
                           else ev.get("summary", ""))
                lines.append(f"- {stamp}: {summary}" if stamp else f"- {summary}")
                refs = (ev.evidence_refs if hasattr(ev, "evidence_refs")
                        else ev.get("evidence_refs", []))
                for r in refs:
                    rid = r.id if hasattr(r, "id") else r.get("id", "")
                    if rid:
                        evidence_ids.append(rid)
            # case fetch payload
            for f in data.get("findings") or []:
                title = f.title if hasattr(f, "title") else f.get("title", "")
                summary = (f.summary if hasattr(f, "summary")
                           else f.get("summary", ""))
                lines.append(f"- {title}: {summary}")
                for r in (f.evidence_refs if hasattr(f, "evidence_refs")
                          else f.get("evidence_refs", [])):
                    rid = r.id if hasattr(r, "id") else r.get("id", "")
                    if rid:
                        evidence_ids.append(rid)
                for p in (f.policy_refs if hasattr(f, "policy_refs")
                          else f.get("policy_refs", [])):
                    cid = p.citation_id if hasattr(p, "citation_id") \
                        else p.get("citation_id")
                    if cid is not None:
                        citations.append(f"[{cid}]")
            if data.get("truncated"):
                lines.append(
                    f"(showing top {data.get('top_n')} of "
                    f"{data.get('total_events')} events)"
                )
            # Signal-explanation payload (rule/ML/graph): surface the key
            # grounded facts deterministically.
            if "rule" in data and isinstance(data["rule"], dict):
                rule = data["rule"]
                parts = [f"Rule: {rule.get('name')}"]
                if rule.get("trigger_values"):
                    parts.append("observed: " + ", ".join(
                        f"{k}={v}" for k, v in rule["trigger_values"].items()))
                if rule.get("threshold"):
                    parts.append(f"threshold: {rule['threshold']}")
                if rule.get("contribution") is not None:
                    parts.append(f"score contribution: {rule['contribution']}")
                lines.append(" — ".join(parts) + ".")
            if data.get("signal_type") == "ML" and "explanation" in data:
                ex = data["explanation"]
                if "ml_score" in ex:
                    lines.append(
                        f"ML score: {ex['ml_score']}/100 "
                        f"({ex.get('score_interpretation', 'system signal')})."
                    )
            # Policy-lookup payload: matches count, documents/sections, and
            # citations — facts only, no interpretation.
            if isinstance(data.get("matches"), list) and data["matches"]:
                lines.append(
                    f"Policy references matching '{data.get('topic', '')}': "
                    f"{len(data['matches'])}."
                )
                for m in data["matches"]:
                    doc = m.get("document", "")
                    section = m.get("section", "")
                    snippet = (m.get("snippet") or "").strip()
                    cite = (f" [{m['citation_id']}]"
                            if m.get("citation_id") is not None else "")
                    lines.append(
                        f"- {doc} / {section}{cite}: \"{snippet}\""
                    )
                if data.get("evidence_missing"):
                    lines.append(
                        "This finding has no policy citations attached by "
                        "the Risk Platform; requirements specific to it "
                        "cannot be assessed."
                    )
            # Artifact payload: factual metadata only — no content summary.
            if isinstance(data.get("artifact"), dict):
                a = data["artifact"]
                scope_txt = a.get("scope", "")
                lines.append(
                    f"Created a Markdown investigation bundle "
                    f"({a.get('artifact_id')}, scope {scope_txt}) using "
                    f"{data.get('source_tool_call_count', '?')} source tool "
                    "calls."
                )
        elif result.outcome == ToolResultOutcome.EMPTY:
            lines.append(
                "The query was valid, but the Risk Platform holds no "
                "matching data for it."
            )
        elif result.outcome == ToolResultOutcome.INTEGRATION_ERROR:
            msg = result.error.message if result.error else "upstream failure"
            lines.append(
                f"The Risk Platform could not be reached or failed "
                f"(integration error: {msg}). This is not an empty result — "
                "availability should be retried later."
            )
        elif result.outcome == ToolResultOutcome.UNSUPPORTED:
            msg = result.error.message if result.error else "unsupported here"
            lines.append(
                f"This investigation action is not supported for the current "
                f"target: {msg}"
            )
        elif result.outcome == ToolResultOutcome.VALIDATION_ERROR:
            msg = result.error.message if result.error else "invalid input"
            lines.append(f"The request could not be executed: {msg}")

    if lines:
        header = f"Investigation result ({skill_id}):" if skill_id else \
                 "Investigation result:"
        body = "\n".join(lines)
        grounding = ""
        if evidence_ids:
            grounding += (
                "\nEvidence references: "
                + ", ".join(sorted(set(evidence_ids))) + "."
            )
        if citations:
            grounding += "\nCitations: " + ", ".join(sorted(set(citations))) + "."
        return f"{header}\n{body}{grounding}"

    return "The investigation completed without structured output."


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class InvestigationTurnResult(BaseModel):
    """Structured outcome of one orchestration turn."""

    task: TaskV2
    plan: Plan | None = None
    planning_failure: PlanningFailure | None = None
    execution: ExecutionResult | None = None
    response: str
    context: InvestigationContext
    follow_ups: list[FollowUp] = Field(default_factory=list)
    context_changed: bool = False

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class InvestigationService:
    """Orchestration glue for one investigation turn."""

    def __init__(
        self,
        resolver: ContextResolver | None = None,
        planner: PlannerV2 | None = None,
        executor: ExecutorV2 | None = None,
        task_store: TaskStoreV2 | None = None,
        investigation_id: str | None = None,
        allow_llm_resolution: bool = False,
    ):
        # Default resolver runs deterministic-only: the orchestration layer
        # never implicitly spends LLM calls, and deterministic resolution is
        # sufficient for the Week 1 flows. Callers may inject an LLM-assisted
        # resolver explicitly.
        self.resolver = resolver or ContextResolver()
        self._allow_llm_resolution = allow_llm_resolution
        if resolver is None:
            # disable LLM assist on the default resolver
            self.resolver._llm_configured = lambda: False
        self.planner = planner or PlannerV2()
        self.executor = executor or ExecutorV2(task_store=None)
        self.task_store = task_store   # optional; run_turn also accepts one

    # ------------------------------------------------------------------ turn

    def run_turn(
        self,
        user_request: str,
        context: InvestigationContext,
        case_findings: list[Finding],
        investigation_id: str | None = None,
        task_store: TaskStoreV2 | None = None,
        trigger_hint: TriggerReason | None = None,
        prior_tool_calls: list[ToolCallV2] | None = None,
    ) -> InvestigationTurnResult:
        """Run one full turn. Never calls tools directly; never mutates the
        input context object (works on copies).

        `prior_tool_calls`: executed ToolCallV2 records from earlier turns of
        this investigation — used only as artifact provenance sources; they
        are never re-executed and never attributed to this turn's task.
        """
        store = task_store or self.task_store
        inv_id = investigation_id or f"CASE:{context.case_id}"
        now_stamp = None  # timestamps come from task lifecycle below

        task = TaskV2(
            task_id=f"TASK-{uuid.uuid4().hex[:12]}",
            investigation_id=inv_id,
            user_request=user_request,
            status=TaskStatusV2.PENDING,
        )
        self._persist(store, task)   # full lifecycle starts auditable at pending
        case_capabilities = [
            f.capabilities for f in case_findings
        ]

        # --- 1) Context Resolution ------------------------------------------
        resolution = self.resolver.resolve(
            user_request, context, findings=case_findings,
        )
        resolved_ctx = resolution.updated_context

        if resolution.status == ResolutionStatus.AMBIGUOUS:
            task.status = TaskStatusV2.FAILED
            task.completed_at = task.started_at = _stamp()
            task.error = "clarification_needed"
            result = InvestigationTurnResult(
                task=task,
                response=resolution.clarification_message
                or "Your request matches multiple possibilities — please "
                   "specify which one you mean.",
                context=resolved_ctx,          # unchanged copy
                follow_ups=[],                 # never after ambiguity
                context_changed=False,
            )
            self._persist(store, result.task)
            return result

        if resolution.status == ResolutionStatus.UNRESOLVED:
            task.status = TaskStatusV2.FAILED
            task.started_at = task.completed_at = _stamp()
            task.error = "unresolved_reference"
            result = InvestigationTurnResult(
                task=task,
                response=resolution.clarification_message
                or "I couldn't determine what this request refers to.",
                context=resolved_ctx,          # unchanged; no invented focus
                follow_ups=[],
                context_changed=False,
            )
            self._persist(store, result.task)
            return result

        # resolved / unchanged_case_level → continue
        task.status = TaskStatusV2.PLANNING
        task.started_at = _stamp()
        self._persist(store, task)

        # --- 2) Eligible skills (registry × capabilities; no duplication) ---
        focused = resolved_ctx.focused_finding_id
        focused_finding = next(
            (f for f in case_findings if f.finding_id == focused), None
        )
        finding_caps = focused_finding.capabilities if focused_finding else None
        eligible = eligible_skills_for_finding(finding_caps)
        eligible_ids = [s.skill_id for s in eligible]

        # --- 3) Planner -------------------------------------------------------
        plan_or_failure = self.planner.plan(
            user_request,
            resolved_ctx,
            eligible_ids,
            finding_capabilities=finding_caps,
        )

        if isinstance(plan_or_failure, PlanningFailure):
            task.status = TaskStatusV2.FAILED
            task.completed_at = _stamp()
            task.error = f"planning_failure:{plan_or_failure.code}"
            result = InvestigationTurnResult(
                task=task,
                planning_failure=plan_or_failure,
                response=compose_response(
                    user_request=user_request, skill_id=None, plan=None,
                    tool_calls=[], execution_errors=[],
                    planning_failure=plan_or_failure,
                ),
                context=resolved_ctx,
                follow_ups=[],     # no follow-ups for planning failure
                context_changed=resolution.context_changed,
            )
            self._persist(store, result.task)
            return result

        plan = plan_or_failure
        task.plan_id = plan.plan_id
        # selected skill: the eligible skill the plan's steps come from —
        # resolved from the planner's own contract-checked selection context.
        # PlannerV2 doesn't echo skill_id on Plan; derive from task context:
        selected_skill = self._infer_selected_skill(plan, eligible_ids)
        task.selected_skill = selected_skill

        # --- 4) Executor -------------------------------------------------------
        task.status = TaskStatusV2.EXECUTING
        self._persist(store, task)

        execution = self.executor.execute(
            plan, task, resolved_ctx, finding_capabilities=finding_caps,
            prior_tool_calls=prior_tool_calls,
        )
        # executor mutates/persists the task itself when a store is attached
        # to it; keep our task object in sync for the result payload.
        task = execution.task

        # --- 5) Response composition -------------------------------------------
        response = compose_response(
            user_request=user_request,
            skill_id=task.selected_skill,
            plan=plan,
            tool_calls=execution.tool_calls,
            execution_errors=execution.errors,
        )

        # --- 6) Follow-ups (only after a valid structured result) ---------------
        follow_ups: list[FollowUp] = []
        structured_ok = (
            execution.status == TaskStatusV2.COMPLETED
            and any(
                tc.result is not None
                and tc.result.outcome in (
                    ToolResultOutcome.SUCCESS, ToolResultOutcome.EMPTY,
                )
                for tc in execution.tool_calls
            )
        )
        if structured_ok:
            trigger = trigger_hint or (
                TriggerReason.SUCCESSFUL_TOOL_RESULT
                if any(tc.result and tc.result.outcome == ToolResultOutcome.SUCCESS
                       for tc in execution.tool_calls)
                else TriggerReason.TASK_COMPLETED
            )
            caps_ids = sorted(finding_caps.root) if finding_caps is not None else []
            follow_ups = select_followups(SelectionInput(
                trigger=trigger,
                context=resolved_ctx,
                finding_capabilities=caps_ids,
            ))

        # --- 7) Context Update ---------------------------------------------------
        # Explicit only: the context returned is the resolution's context.
        # We never change focus because a tool returned data; user_selected
        # focus is preserved untouched (resolution already guarantees this).
        final_ctx = resolved_ctx

        result = InvestigationTurnResult(
            task=task,
            plan=plan,
            execution=execution,
            response=response,
            context=final_ctx,
            follow_ups=follow_ups,
            context_changed=resolution.context_changed,
        )
        self._persist(store, result.task)
        return result

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _infer_selected_skill(plan: Plan, eligible_ids: list[str]) -> str | None:
        """PlannerV2 returns a Plan without echoing skill_id; infer the
        selected skill deterministically as the eligible skill whose planning
        vocabulary exactly contains the plan's step types."""
        plan_types = [s.type for s in plan.steps]
        for sid in eligible_ids:
            vocab = SKILLS[sid].planning_steps
            if plan_types and all(t in vocab for t in plan_types):
                # prefer the most specific (non-case-level) match
                if sid != "case_intake" or len(eligible_ids) == 1:
                    return sid
        # fall back: first eligible whose vocabulary contains any step type
        for sid in eligible_ids:
            if any(t in SKILLS[sid].planning_steps for t in plan_types):
                return sid
        return eligible_ids[0] if eligible_ids else None

    @staticmethod
    def _persist(store: TaskStoreV2 | None, task: TaskV2) -> None:
        if store is not None:
            store.update(task)


def _stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

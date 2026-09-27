"""V2 Investigation HTTP API.

Product-oriented boundary over the Agent runtime: the frontend talks to
investigations/turns/tasks/artifacts — never to PlannerV2, ExecutorV2,
ToolProvider, RiskPlatformAdapter, or TaskStoreV2 internals.

Mounted from main.py with include_router (legacy endpoints untouched).
Imports: InvestigationService, TaskStoreV2, domain models — never legacy
agent.py/store.py.

Persistence note (documented gap): InvestigationService is turn-scoped, so
the API layer owns the investigation session (Investigation record +
InvestigationContext) persisted through TaskStoreV2's investigations table
(the same SQLite store/file — no second database). This is the smallest
compatible extension of the existing V2 persistence boundary.

Follow-up semantics: a follow_up_id in a turn request is validated against
the server-side registry and reconstructed into the canonical intent text.
The client can never submit tool names, skills, arguments, plans, or tool
calls — those are server-controlled.
"""

import logging
import pathlib
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    CreateInvestigationRequest,
    ExecutionSummary,
    GoalRunRequest,
    GoalRunResponse,
    InvestigationResponse,
    PlanSummary,
    TaskActivityResponse,
    TaskDetailResponse,
    TaskNotFound,
    TurnRequest,
    TurnResponse,
    result_view,
    summarize_execution,
    summarize_plan,
)
from app.case_resolution import CaseResolutionStatus, resolve_case_reference
from app.candidate_metadata import reconcile_coverage, TaskStoreCatalogProvider
from app.clarification_state import ClarificationLedger
from app.config import settings
from app.context_resolution import ContextResolver
from app.followups import (
    CASE_TEMPLATES,
    EVENT_TEMPLATES,
    FINDING_TEMPLATES,
    FollowUpApplicableContext,
    SelectionInput,
    TriggerReason,
    select_followups,
)
from app.investigation_service import InvestigationService
from app.models import (
    Finding,
    FocusSource,
    Investigation,
    InvestigationContext,
    InvestigationStatus,
    Plan,
    TaskStatusV2,
    ToolCallV2,
)
from app.task_store_v2 import TaskStoreV2
from app.telemetry import emit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["investigations-v2"])

# canonical follow-up registry (server-side truth for follow_up_id)
_FOLLOWUP_INDEX = {
    t.follow_up_id: t
    for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvestigationAPI:
    """Dependency container for the V2 router. Instances own their stores
    and service, so tests can inject fakes without global state."""

    def __init__(
        self,
        service: InvestigationService | None = None,
        store: TaskStoreV2 | None = None,
        case_findings_provider=None,
        loop_runner_factory=None,
    ):
        self.store = store or TaskStoreV2()
        self.service = service or InvestigationService(
            executor=_default_executor(self.store), task_store=self.store,
            goal_resolver=_default_goal_resolver(),
            # SP-M2-1R: the existing bounded refinement (app/target_refinement.py)
            # is wired into the real /turn construction path; the flag
            # ENABLE_TARGET_GOAL_REFINEMENT (default False) controls whether
            # the refinement attempt actually runs (gate lives in
            # InvestigationService.run_turn §1b).
            refinement_resolver=_default_refinement_resolver(),
            # WP-M2-1 WI-5: deterministic Safe-Select admissibility gate
            # (app/admissibility.py). Gated at runtime by
            # ENABLE_SAFE_SELECT_ADMISSIBILITY (default False = legacy
            # pre-WI-5 behavior).
            metadata_admissibility=_default_metadata_admissibility(
                self.store),
            # WP-M2-1 WI-6: bounded-clarification ledger (same runtime
            # flag; inactive when the admissibility flag is off).
            clarification_ledger=ClarificationLedger(self.store),
        )
        # case_findings_provider(case_id) -> list[Finding]: pluggable source
        # for canonical findings (default: real risk_case_fetch via adapter).
        self.case_findings_provider = case_findings_provider or _default_findings
        # loop_runner_factory(store) -> AgentLoopRunner: pluggable bounded
        # loop coordinator (default: real runner with real planner/executor
        # and the LLM/heuristic decision proposer). Tests inject a
        # deterministic factory.
        self.loop_runner_factory = loop_runner_factory or _default_loop_runner

    # --- turn ---------------------------------------------------------------

    def run_turn(self, investigation_id: str, request: TurnRequest) -> TurnResponse:
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVESTIGATION_NOT_FOUND",
                        "message": f"Unknown investigation: {investigation_id}"},
            )
        context = self._load_context(investigation_id)
        findings = self._findings(investigation.case_id)

        context = self._apply_context_action(context, request.context_action)
        # WP-M2-1 WI-6: an explicit focus designation is evidence of user
        # INTENT — it closes any open clarification boundary (loop
        # protection) but NEVER touches candidate metadata status or
        # Safe-Select eligibility (Explicit Selection ≠ Safe-Select).
        if (request.context_action is not None
                and request.context_action.type == "focus_finding"
                and request.context_action.finding_id
                and settings.ENABLE_SAFE_SELECT_ADMISSIBILITY):
            try:
                ClarificationLedger(self.store).close_by_designation(
                    investigation_id,
                    request.context_action.finding_id)
            except Exception:
                logger.warning("clarify ledger designation close failed",
                               exc_info=True)
            emit_event("user.designated_candidate",
                       investigation_id=investigation_id,
                       candidate_id=request.context_action.finding_id,
                       reason_class="explicit_focus",
                       outcome="focus_transition")
        message = self._resolve_followup(request)

        result = self.service.run_turn(
            user_request=message,
            context=context,
            case_findings=findings,
            investigation_id=investigation_id,
            task_store=self.store,
            prior_tool_calls=self._prior_calls(investigation_id),
        )

        # Record session artifacts/calls for later task/artifact retrieval
        # and future-turn provenance.
        self._record_session(investigation_id, result)

        # Persist evolved context + touched-up investigation timestamps.
        self._save_context(investigation_id, result.context)
        self._touch_investigation(investigation)

        status = _turn_status(result)
        goal = (result.goal_representation.project()
                if result.goal_representation is not None else None)
        return TurnResponse(
            investigation_id=investigation_id,
            task=result.task,
            context=result.context,
            response=result.response,
            plan=summarize_plan(result.plan, result.task.selected_skill),
            execution=summarize_execution(result.execution),
            artifacts=[
                a for a in (result.execution.artifacts if result.execution else [])
            ],
            follow_ups=result.follow_ups,
            context_changed=result.context_changed,
            status=status,
            goal=goal,
            target_resolution=result.target_resolution,
        )

    # --- investigation ---------------------------------------------------------

    def create_investigation(self, request: CreateInvestigationRequest) \
            -> InvestigationResponse:
        """Create an investigation. Canonical `case_id` is accepted as-is;
        natural-language / numeric input is resolved server-side via Case
        Reference Resolution (the frontend never normalizes)."""
        if (request.case_id or "").strip():
            case_id = request.case_id.strip()
        else:
            raw = (request.message or request.case_reference or "").strip()
            resolution = resolve_case_reference(raw)
            if resolution.status == CaseResolutionStatus.RESOLVED:
                case_id = resolution.case_id
            elif resolution.status == CaseResolutionStatus.AMBIGUOUS:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "AMBIGUOUS_CASE_REFERENCE",
                            "message": resolution.message,
                            "candidates": resolution.candidates},
                )
            elif resolution.status == CaseResolutionStatus.INVALID:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "INVALID_CASE_REFERENCE",
                            "message": resolution.message},
                )
            else:   # missing
                raise HTTPException(
                    status_code=422,
                    detail={"code": "CASE_REFERENCE_REQUIRED",
                            "message": resolution.message},
                )

        investigation = Investigation(
            investigation_id=f"INV-{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            status=InvestigationStatus.ACTIVE,
            created_at=_now(),
            updated_at=_now(),
        )
        self.store.save_investigation(investigation)
        # all downstream components see only the canonical case_id
        context = InvestigationContext(case_id=case_id)
        if request.preferences:
            context = context.model_copy(update={
                "preferences": context.preferences.model_copy(
                    update=request.preferences)
            })
        self._save_context(investigation_id=investigation.investigation_id,
                           context=context)
        # Canonical Case Intake turn request: the client submits THIS as the
        # first conversational turn — never the raw case-reference text
        # (which is an identification input, not a conversational message).
        return InvestigationResponse(
            investigation=investigation, context=context, tasks=[],
            intake_message=f"Investigate {case_id}",
        )

    def get_investigation(self, investigation_id: str) -> InvestigationResponse:
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVESTIGATION_NOT_FOUND",
                        "message": f"Unknown investigation: {investigation_id}"},
            )
        return InvestigationResponse(
            investigation=investigation,
            context=self._load_context(investigation_id),
            tasks=self.store.list_by_investigation(investigation_id),
        )

    def get_task(self, task_id: str) -> TaskDetailResponse:
        task = self.store.get(task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "TASK_NOT_FOUND",
                        "message": f"Unknown task: {task_id}"},
            )
        session = self._session(task.investigation_id)
        # task-scoped view: only this task's own calls and plan (the session
        # pool also holds earlier turns' records for cross-turn provenance)
        own_calls = self._session_tool_calls(session, task.task_id)
        plan = self._session_plan(session, task.task_id)
        detail = TaskDetailResponse(
            task=task,
            plan=summarize_plan(plan, task.selected_skill),
            tool_calls=[summarize_tool_call_api(tc) for tc in own_calls],
            artifacts=[a for a in session.get("artifacts", [])
                       if a.get("artifact_id") in set(task.artifact_ids)],
            selected_skill=task.selected_skill,
            error=task.error,
        )
        # composed response + this message's own follow-ups for conversation
        # restore (may be absent for older investigations)
        response = (session.get("turn_responses") or {}).get(task.task_id)
        if response is not None:
            detail.task_response_text = response
        follow_ups = (session.get("turn_followups") or {}).get(task.task_id)
        if follow_ups is not None:
            detail.task_follow_ups = follow_ups
        return detail

    def get_task_artifacts(self, task_id: str) -> dict:
        task = self.store.get(task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "TASK_NOT_FOUND",
                        "message": f"Unknown task: {task_id}"},
            )
        session = self._session(task.investigation_id)
        artifacts = [a for a in session.get("artifacts", [])
                     if a.get("artifact_id") in set(task.artifact_ids)]
        return {"task_id": task_id, "artifacts": artifacts}

    def get_task_results(self, task_id: str) -> dict:
        """Read-only view of this task's normalized tool results (Agent-domain
        shapes: findings, timeline_events, signal explanation, policy
        context, artifact). Never raw Risk Platform payloads."""
        task = self.store.get(task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "TASK_NOT_FOUND",
                        "message": f"Unknown task: {task_id}"},
            )
        session = self._session(task.investigation_id)
        own_calls = self._session_tool_calls(session, task.task_id)
        results = []
        for tc in own_calls:
            view = result_view(tc)
            if view is not None:
                results.append(view)
        return {"task_id": task_id, "results": results}

    def list_followups(self, investigation_id: str,
                       context_action: str | None = None) -> dict:
        """Suggested Follow-ups for the current persisted context (optionally
        previewed after a selection's context_action), computed by the same
        deterministic selection pipeline the turn flow uses. Read-only: no
        planning, no execution, no context mutation.

        `context_action`: JSON-encoded FocusAction (e.g. the focus_finding a
        selection sends with its next turn). Only the single documented
        action shape is honored; anything else is rejected like a turn's
        malformed action would be."""
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVESTIGATION_NOT_FOUND",
                        "message": f"Unknown investigation: {investigation_id}"},
            )
        context = self._load_context(investigation_id)
        if context_action:
            from app.api.schemas import FocusAction
            try:
                action = FocusAction.model_validate_json(context_action)
            except ValueError as e:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "INVALID_CONTEXT_ACTION",
                            "message": f"Malformed context_action: {e}"},
                )
            context = self._apply_context_action(context, action)
        findings = self._findings(investigation.case_id)
        focused_finding = next(
            (f for f in findings
             if f.finding_id == context.focused_finding_id), None
        )
        caps_ids = (
            sorted(focused_finding.capabilities.root)
            if focused_finding is not None else []
        )
        follow_ups = select_followups(SelectionInput(
            trigger=TriggerReason.FOCUS_CHANGED,
            context=context,
            finding_capabilities=caps_ids,
        ))
        return {
            "investigation_id": investigation_id,
            "context": context,
            "follow_ups": [f.model_dump() for f in follow_ups],
        }

    # --- Agent Loop (bounded) ---------------------------------------------------
    #
    # The goal endpoint STARTS a bounded loop run in a background thread;
    # state persists after every transition into the SAME store, so the
    # activity endpoint can serve the run while it executes and after it
    # terminates (Agent Activity baseline §12/§16: start-and-poll, no
    # streaming). Existing task/turn APIs are unchanged.

    def start_goal(self, investigation_id: str,
                   request: GoalRunRequest) -> GoalRunResponse:
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVESTIGATION_NOT_FOUND",
                        "message": f"Unknown investigation: {investigation_id}"},
            )
        goal = (request.goal or "").strip()
        if not goal:
            raise HTTPException(
                status_code=422,
                detail={"code": "GOAL_REQUIRED",
                        "message": "goal must be a non-empty string."})
        context = self._load_context(investigation_id)
        # QA-01: the client's explicit pending selection (focused finding)
        # is the ONLY client-writable context surface — the loop MUST
        # honor it exactly like a turn. Applied AND persisted so the
        # user-selected focus is authoritative investigation state.
        context = self._apply_context_action(context, request.context_action)
        if request.context_action is not None:
            self._save_context(investigation_id, context)
        runner = self.loop_runner_factory(self.store)
        # QA-04: findings resolve INSIDE the loop run — an unavailable
        # Risk Platform becomes a distinct BLOCKED/dependency state there,
        # never a synchronous endpoint failure or a planning error.
        # (Only injected when the factory did not provide its own — a
        # custom runner's provider wins.)
        if getattr(runner, "findings_provider", None) is None:
            runner.findings_provider = lambda: self._findings_strict(
                investigation.case_id)

        # Pre-create the loop task synchronously so the client gets a
        # stable id to poll; the runner reuses it (run(task_id=...)).
        task_id = f"TASK-{uuid.uuid4().hex[:12]}"

        def _run() -> None:
            try:
                runner.run(goal, context, [],
                           investigation_id=investigation_id,
                           task_id=task_id,
                           findings_provider=runner.findings_provider)
            except Exception:                     # noqa: BLE001 — the runner
                logger.exception(                 # already bounds its own
                    "Agent Loop run failed for task %s", task_id)

        thread = threading.Thread(target=_run, name=f"agent-loop-{task_id}",
                                  daemon=True)
        thread.start()
        return GoalRunResponse(task_id=task_id,
                               investigation_id=investigation_id,
                               agent_status="running")

    def get_task_activity(self, task_id: str) -> TaskActivityResponse:
        from app.agent_loop import project_task_activity
        state = self.store.get_agent_state(task_id)
        if state is None:
            # Pre-loop (turn-local) tasks have no Agent Activity: the
            # client falls back to the existing TaskDetail rendering and
            # never mislabels them as Agent Activity (baseline §12).
            raise HTTPException(
                status_code=404,
                detail={"code": "NO_AGENT_ACTIVITY",
                        "message": f"No Agent Activity for task: {task_id}"},
            )
        return TaskActivityResponse(**project_task_activity(state))

    # --- context/session store (persisted in TaskStoreV2) ----------------------

    def _session(self, investigation_id: str) -> dict:
        """Persisted session document (context, per-task plans, executed
        tool-call records, artifacts). Survives refresh AND backend restart."""
        doc = self.store.get_session(investigation_id)
        return doc if doc is not None else {}

    @staticmethod
    def _session_tool_calls(session: dict, task_id: str | None = None) -> list:
        """ToolCallV2 records from a session document (deserialized)."""
        calls = [ToolCallV2(**tc) for tc in session.get("tool_calls", [])]
        if task_id is not None:
            calls = [tc for tc in calls if tc.task_id == task_id]
        return calls

    @staticmethod
    def _session_plan(session: dict, task_id: str | None = None):
        raw = (session.get("plans") or {}).get(task_id) if task_id else None
        return Plan(**raw) if raw else None

    def _load_context(self, investigation_id: str) -> InvestigationContext:
        session = self._session(investigation_id)
        ctx = session.get("context")
        if ctx is not None:
            return InvestigationContext(**ctx)
        inv = self.store.get_investigation(investigation_id)
        return InvestigationContext(case_id=inv.case_id if inv else "")

    def _save_context(self, investigation_id: str, context) -> None:
        session = self._session(investigation_id)
        session["context"] = context.model_dump()
        self.store.save_session(investigation_id, session)

    def _record_session(self, investigation_id: str, result) -> None:
        session = self._session(investigation_id)
        session["context"] = result.context.model_dump()
        if result.plan is not None:
            session.setdefault("plans", {})[result.task.task_id] = \
                result.plan.model_dump()
        if result.execution is not None:
            calls = session.setdefault("tool_calls", [])
            calls.extend(tc.model_dump() for tc in result.execution.tool_calls)
            session.setdefault("artifacts", []).extend(
                result.execution.artifacts)
        # Conversation persistence: the composed human-readable response is
        # part of the investigation record, so a reopened investigation
        # restores what the Agent actually said (not just the request) —
        # including its own Suggested Follow-ups (they belong to that
        # specific Agent message and must survive a refresh).
        session.setdefault("turn_responses", {})[result.task.task_id] = \
            result.response
        session.setdefault("turn_followups", {})[result.task.task_id] = [
            f.model_dump() for f in result.follow_ups
        ]
        self.store.save_session(investigation_id, session)

    def _prior_calls(self, investigation_id: str):
        session = self._session(investigation_id)
        return [ToolCallV2(**tc) for tc in session.get("tool_calls", [])]

    def _touch_investigation(self, investigation: Investigation) -> None:
        investigation.updated_at = _now()
        self.store.save_investigation(investigation)

    # --- history / delete ------------------------------------------------------

    def list_investigations(self) -> list[Investigation]:
        return self.store.list_investigations()

    def delete_investigation(self, investigation_id: str) -> None:
        investigation = self.store.get_investigation(investigation_id)
        if investigation is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVESTIGATION_NOT_FOUND",
                        "message": f"Unknown investigation: {investigation_id}"},
            )
        # hard delete (Week 1): investigation + session doc + its tasks,
        # cascaded within the same store; other investigations are untouched
        self.store.delete_session(investigation_id)

    # --- focus / follow-up handling ----------------------------------------------

    @staticmethod
    def _apply_context_action(context: InvestigationContext, action) \
            -> InvestigationContext:
        """Apply the client's explicit UI selection — the ONLY client-writable
        context surface. focus_source is set to user_selected per contract."""
        if action is None:
            return context
        if action.type == "focus_finding":
            return context.model_copy(update={
                "focused_finding_id": action.finding_id.strip(),
                "focused_event_id": None,
                "focus_source": FocusSource.USER_SELECTED,
            })
        if action.type == "focus_event":
            return context.model_copy(update={
                "focused_finding_id": action.finding_id.strip(),
                "focused_event_id": action.event_id.strip(),
                "focus_source": FocusSource.USER_SELECTED,
            })
        # clear_focus
        return context.model_copy(update={
            "focused_finding_id": None,
            "focused_event_id": None,
            "focus_source": None,
        })

    @staticmethod
    def _resolve_followup(request: TurnRequest) -> str:
        """Reconstruct the canonical intent from the server-side registry.
        The client-supplied follow_up_id never carries tool/skill/arguments."""
        if request.follow_up_id:
            template = _FOLLOWUP_INDEX.get(request.follow_up_id)
            if template is None:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "INVALID_FOLLOW_UP",
                            "message": f"Unknown follow_up_id: "
                                       f"{request.follow_up_id}"},
                )
            return template.intent
        return request.message

    def _findings(self, case_id: str) -> list[Finding]:
        try:
            findings = list(self.case_findings_provider(case_id) or [])
        except Exception:
            logger.exception("findings provider failed for %s", case_id)
            return []
        self._reconcile_candidate_metadata(case_id, findings)
        return findings

    def _reconcile_candidate_metadata(self, case_id: str,
                                      findings: list[Finding]) -> None:
        """WP-M2-1 WI-2 (flag-gated governance bypass): deterministic
        reconcile of the live RP candidate universe against the metadata
        catalog. Governance side-path only — a coverage failure is
        logged and NEVER blocks or alters the investigation turn."""
        if not settings.ENABLE_CANDIDATE_METADATA_COVERAGE:
            return
        if not findings:
            # empty fetch = provider failure per _default_findings; an
            # absence caused by failure must not read as candidate-gone.
            return
        try:
            report = reconcile_coverage(
                TaskStoreCatalogProvider(self.store), findings)
            logger.info("candidate.metadata.coverage case=%s %s",
                        case_id, report.summary())
        except Exception:
            logger.warning(
                "candidate metadata coverage failed for %s; continuing "
                "with existing behavior", case_id, exc_info=True)

    def _metadata_coverage_summary(self) -> dict:
        """Read-only coverage summary for the runtime identity endpoint
        (WI-7): counts by lifecycle status + coverage ratio. Never
        raises; failures degrade to an explicit error marker."""
        try:
            provider = TaskStoreCatalogProvider(self.store)
            records = provider.list_candidates()
            counts = {"total": len(records), "complete": 0,
                      "incomplete": 0, "unverified": 0, "deprecated": 0}
            for r in records:
                key = {"COMPLETE": "complete",
                       "INCOMPLETE": "incomplete",
                       "UNVERIFIED": "unverified",
                       "DEPRECATED": "deprecated"}.get(r.status)
                if key:
                    counts[key] += 1
            eligible = counts["complete"]
            counts["coverage_ratio"] = (
                round(eligible / counts["total"], 3)
                if counts["total"] else 0.0)
            return counts
        except Exception:
            return {"error": "unavailable"}

    def _findings_strict(self, case_id: str) -> list[Finding]:
        """Strict findings resolution for the Agent Loop goal path (QA-04):
        an unavailable Risk Platform RAISES — the loop maps it to its
        distinct BLOCKED/dependency state instead of degrading to an empty
        list that would surface as a misleading planning failure."""
        from app.adapters.risk_platform import RiskPlatformError
        from app.domain_tools import risk_case_fetch
        result = risk_case_fetch(case_id=case_id)
        if result.outcome.value == "integration_error":
            raise RiskPlatformError(
                "unavailable",
                result.error.message if result.error
                else "Risk Platform unavailable")
        if result.outcome.value != "success":
            return []
        return list((result.data or {}).get("findings") or [])


def summarize_tool_call_api(tc):
    from app.api.schemas import summarize_tool_call
    return summarize_tool_call(tc)


def _default_executor(store: TaskStoreV2):
    from app.executor_v2 import ExecutorV2, default_tool_provider
    return ExecutorV2(default_tool_provider(), store)


def _default_findings(case_id: str) -> list[Finding]:
    """Canonical findings via the real Agent tool boundary (adapter mocked
    nowhere here — production path)."""
    from app.domain_tools import risk_case_fetch
    result = risk_case_fetch(case_id=case_id)
    if result.outcome.value != "success":
        return []
    return list((result.data or {}).get("findings") or [])


def _turn_status(result) -> str:
    if result.task.status == TaskStatusV2.COMPLETED:
        return "completed"
    if result.planning_failure is not None:
        if result.planning_failure.code in ("NO_ELIGIBLE_SKILL",
                                            "UNSUPPORTED_REQUEST",
                                            "SKILL_NOT_ELIGIBLE"):
            return "unsupported"
        return "failed"
    if result.execution is not None and result.execution.errors:
        return "execution_failed"
    return "failed"


def _default_loop_runner(store: TaskStoreV2):
    """Real bounded loop coordinator: ContextResolver (Stage 1 target
    resolution) + Stage 2.1 goal understanding + PlannerV2 + ExecutorV2
    (default provider) + LLM/heuristic decision proposer, all persisting
    into the shared store."""
    from app.agent_loop import AgentLoopRunner
    return AgentLoopRunner(store=store,
                           resolver=ContextResolver(),
                           goal_resolver=_default_goal_resolver())


def _default_goal_resolver():
    """Stage 2.1 goal-understanding callable (request_text,
    focused_finding_id, case_id) -> GoalRepresentation, LLM-backed with
    deterministic heuristic fallback."""
    from app.goal_resolution import understand_goal

    def resolve(request_text: str, focused_finding_id: str | None,
                case_id: str | None):
        return understand_goal(request_text,
                               resolved_finding_id=focused_finding_id,
                               case_id=case_id, use_llm=True)
    return resolve


def _default_refinement_resolver():
    """SP-M2-1 bounded goal-aware target refinement callable
    (request_text, candidates, goal) -> RefinementDecision.

    Delegates to the frozen spike implementation (app/target_refinement.py);
    this factory only supplies the LLM provider and the system-controlled
    threshold, mirroring the planner's ClaudeProvider convention. The
    decision algorithm, prompt, and threshold semantics are unchanged.
    """
    from app.llm_provider import ClaudeProvider
    from app.target_refinement import refine_target

    def refine(request_text: str, candidates, goal):
        return refine_target(request_text, candidates, goal,
                             llm=ClaudeProvider(),
                             min_confidence=settings
                             .TARGET_REFINEMENT_MIN_CONFIDENCE)
    return refine


def _default_metadata_admissibility(store):
    """WP-M2-1 WI-5: deterministic Safe-Select admissibility gate
    (app/admissibility.py) over the governed metadata catalog. The LLM
    inside the normalizer only proposes; the gate's decision is purely
    deterministic system logic. Runtime-enforced only when
    ENABLE_SAFE_SELECT_ADMISSIBILITY is on."""
    from app.admissibility import SafeSelectGate
    from app.llm_provider import ClaudeProvider

    return SafeSelectGate(
        catalog_provider=TaskStoreCatalogProvider(store),
        normalizer_llm=ClaudeProvider(),
        min_confidence=settings.TARGET_REFINEMENT_MIN_CONFIDENCE)


api = InvestigationAPI()


@router.get("/investigations")
def list_investigations():
    """Investigation history (deterministic: most recently updated first)."""
    investigations = api.list_investigations()
    return {
        "investigations": [
            {
                "investigation_id": i.investigation_id,
                "case_id": i.case_id,
                "status": i.status.value,
                "created_at": i.created_at,
                "updated_at": i.updated_at,
            }
            for i in investigations
        ]
    }


@router.post("/investigations", response_model=InvestigationResponse)
def create_investigation(request: CreateInvestigationRequest):
    return api.create_investigation(request)


@router.post("/investigations/{investigation_id}/turn",
             response_model=TurnResponse)
def run_turn(investigation_id: str, request: TurnRequest):
    return api.run_turn(investigation_id, request)


@router.get("/investigations/{investigation_id}",
            response_model=InvestigationResponse)
def get_investigation(investigation_id: str):
    return api.get_investigation(investigation_id)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task(task_id: str):
    return api.get_task(task_id)


@router.get("/tasks/{task_id}/artifacts")
def get_task_artifacts(task_id: str):
    return api.get_task_artifacts(task_id)


@router.get("/tasks/{task_id}/results")
def get_task_results(task_id: str):
    return api.get_task_results(task_id)


@router.post("/investigations/{investigation_id}/goal",
             response_model=GoalRunResponse)
def start_goal(investigation_id: str, request: GoalRunRequest):
    """Start a bounded Agent Loop run for this investigation. Returns
    immediately with the task id; poll GET /tasks/{task_id}/activity until
    a terminal agent_status (Agent Activity baseline §16)."""
    return api.start_goal(investigation_id, request)


@router.get("/tasks/{task_id}/activity", response_model=TaskActivityResponse)
def get_task_activity(task_id: str):
    """Bounded, UI-safe Agent Activity projection for one loop task
    (404 NO_AGENT_ACTIVITY for turn-local tasks — the client falls back to
    the existing TaskDetail rendering)."""
    return api.get_task_activity(task_id)


@router.get("/investigations/{investigation_id}/followups")
def list_followups(investigation_id: str, context_action: str | None = None):
    """Read-only preview of the Suggested Follow-ups the backend would offer
    for the investigation's CURRENT persisted context (optionally as it
    would stand AFTER applying the same context_action a selection would
    send on the next turn) — no turn is run, no tool executed, nothing
    mutated. Uses the same deterministic select_followups pipeline as turn
    responses (capability + scope + executable-only filtering against the
    server-side registry), so the ids are the same server-owned set a
    follow-up click submits."""
    return api.list_followups(investigation_id, context_action=context_action)


@router.delete("/investigations/{investigation_id}")
def delete_investigation(investigation_id: str):
    api.delete_investigation(investigation_id)
    return {"deleted": True, "investigation_id": investigation_id}


# --- runtime identity (SP-M2-1R §5) ------------------------------------------

_GIT_COMMIT: str | None = None
_GIT_COMMIT_LOCK = threading.Lock()


def _runtime_git_commit() -> str:
    """Short commit of the running source tree, resolved once per process
    (best-effort: 'unknown' when git is unavailable, e.g. container images).
    Non-sensitive; used by QA to tie observed behavior to the source."""
    global _GIT_COMMIT
    with _GIT_COMMIT_LOCK:
        if _GIT_COMMIT is None:
            import subprocess
            try:
                _GIT_COMMIT = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=_repo_root(), capture_output=True, text=True,
                    timeout=3,
                ).stdout.strip() or "unknown"
            except Exception:
                _GIT_COMMIT = "unknown"
    return _GIT_COMMIT


def _repo_root() -> str:
    return str(pathlib.Path(__file__).resolve().parents[2])


def _source_fingerprint(relpath: str) -> str:
    """First 12 hex of sha256 over a source file — lets QA verify the
    refinement implementation in the running process is the current one.
    Content-only; no secrets, no environment values."""
    import hashlib
    p = pathlib.Path(__file__).resolve().parents[1] / relpath
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return "unknown"


def _metadata_coverage_summary() -> dict:
    """Read-only coverage counts from the governed metadata catalog
    (WI-7 identity observability). Degrades to an explicit error marker;
    never exposes candidate contents."""
    try:
        return api._metadata_coverage_summary()
    except Exception:
        return {"error": "unavailable"}


@router.get("/runtime/identity")
def runtime_identity():
    """Minimal, non-sensitive runtime identity for QA operators
    (SP-M2-1R): proves which source tree / refinement implementation /
    experimental-flag state the browser-facing backend is running.
    Deliberately excludes secrets, credentials, and environment dumps."""
    return {
        "service": "investigation-agent-backend",
        "api": "v2",
        "git_commit": _runtime_git_commit(),
        "features": {
            "ENABLE_TARGET_GOAL_REFINEMENT":
                settings.ENABLE_TARGET_GOAL_REFINEMENT,
            "ENABLE_CANDIDATE_METADATA_COVERAGE":
                settings.ENABLE_CANDIDATE_METADATA_COVERAGE,
            "ENABLE_SAFE_SELECT_ADMISSIBILITY":
                settings.ENABLE_SAFE_SELECT_ADMISSIBILITY,
            "TARGET_REFINEMENT_MIN_CONFIDENCE":
                settings.TARGET_REFINEMENT_MIN_CONFIDENCE,
        },
        "implementation": {
            "target_refinement": _source_fingerprint("target_refinement.py"),
            "investigation_service":
                _source_fingerprint("investigation_service.py"),
        },
        "refinement_wired": api.service.refinement_resolver is not None,
        "metadata_coverage": _metadata_coverage_summary(),
    }

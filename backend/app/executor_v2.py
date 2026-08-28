"""Week 1 Executor (executor_v2) and structured execution summary.

Executes a validated Plan against a tool provider — never plans, never
retries. Pipeline per plan:

1. Runtime contract re-validation via the Skill Registry Contract Checker
   (defense in depth: planner already validated; context may have shifted).
   Validation failure ⇒ task failed, ZERO tool executions, no fake ToolCalls.
2. Task → executing; steps run in plan order.
3. Each executed step: create ToolCallV2 (actual execution audit record with
   normalized ToolResult), update PlanStep status.
4. Task finalized from step outcomes; timestamps persisted.

Failure semantics (deliberately NOT collapsed):
- Plan validation failure        → no execution at all
- Tool missing from provider     → executor failure category TOOL_UNAVAILABLE
                                  (distinct from RP integration_error;
                                   distinct from ToolResult.empty)
- RP integration failure         → ToolResult(outcome="integration_error")
- Valid query, genuinely no data → ToolResult(outcome="empty")
- Insufficient evidence          → success + data.evidence_missing
- Unsupported capability/view    → ToolResult(outcome="unsupported")

No autonomous retries in Week 1; failed steps stay failed for later
task/UI-driven retry.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.models import (
    FindingCapability,
    InvestigationContext,
    Plan,
    PlanStep,
    PlanStepStatus,
    TaskStatusV2,
    TaskV2,
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
)
from app.skills import SKILLS, check_plan

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tool provider boundary
# ---------------------------------------------------------------------------

class ToolProvider:
    """Registry of callable Agent tools the executor may invoke.

    The executor knows only names + callables — never Risk Platform endpoint
    details. Adapter logic lives behind the tool boundary.

    A provider entry is either a plain callable or (callable, skill_id)
    pairs are not needed here because the *plan* carries selected_skill;
    entries map tool_name → callable(args_dict) -> ToolResult.
    """

    def __init__(self, tools: dict[str, Callable[[dict[str, Any]], ToolResult]] | None = None):
        self._tools = dict(tools or {})

    def register(self, tool_name: str, fn: Callable[[dict[str, Any]], ToolResult]) -> None:
        self._tools[tool_name] = fn

    def get(self, tool_name: str) -> Callable[[dict[str, Any]], ToolResult] | None:
        return self._tools.get(tool_name)

    def has(self, tool_name: str) -> bool:
        return tool_name in self._tools


def default_tool_provider() -> ToolProvider:
    """Week 1 provider: the wired Agent tools. Additional tools plug in
    through this same boundary."""
    from app.domain_tools import (
        artifact_bundle,
        finding_drilldown,
        policy_lookup,
        risk_case_fetch,
        signal_explain,
    )

    provider = ToolProvider()
    provider.register("risk_case_fetch", lambda args: risk_case_fetch(
        case_id=args.get("case_id", ""),
    ))
    provider.register("finding_drilldown", lambda args: finding_drilldown(
        finding_id=args.get("finding_id", ""),
        view=args.get("view", "timeline"),
        top_n=args.get("top_n", 20),
        case_id=args.get("case_id"),
    ))
    provider.register("signal_explain", lambda args: signal_explain(
        finding_id=args.get("finding_id", ""),
        signal_type=args.get("signal_type", "Rule"),
        case_id=args.get("case_id"),
    ))
    provider.register("policy_lookup", lambda args: policy_lookup(
        topic=args.get("topic", ""),
        finding_id=args.get("finding_id", ""),
        case_id=args.get("case_id"),
    ))
    provider.register("artifact_bundle", lambda args: artifact_bundle(
        scope=args.get("scope", "case"),
        format=args.get("format", "md"),
        case_id=args.get("case_id"),
        finding_id=args.get("finding_id"),
        task_id=args.get("task_id"),
        source_tool_calls=args.get("source_tool_calls"),
    ))
    return provider


# ---------------------------------------------------------------------------
# Execution result structures
# ---------------------------------------------------------------------------

class ExecutorError(BaseModel):
    """Executor-level error record (distinct from ToolError/ToolResult).

    Categories:
    - PLAN_CONTRACT_VIOLATION : pre-execution validation failed; nothing ran
    - TOOL_NOT_IMPLEMENTED    : resolved tool not in current provider
                                (recorded on the step AND surfaced as an
                                executor-level failure; never empty-success)
    - STEP_EXECUTION_ERROR    : unexpected exception invoking a registered tool
    """

    code: str                     # PLAN_CONTRACT_VIOLATION | TOOL_NOT_IMPLEMENTED |
                                  # STEP_EXECUTION_ERROR
    message: str
    step_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """Structured execution summary returned to callers."""

    task: TaskV2
    plan: Plan
    tool_calls: list[ToolCallV2] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    status: TaskStatusV2                       # final overall status
    errors: list[ExecutorError] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == TaskStatusV2.COMPLETED


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class ExecutorV2:
    """Runs validated plans against a ToolProvider with full task logging."""

    def __init__(
        self,
        tool_provider: ToolProvider | None = None,
        task_store: Any | None = None,
    ):
        self.provider = tool_provider or default_tool_provider()
        self.task_store = task_store

    @staticmethod
    def _runtime_arguments(step: PlanStep, context: InvestigationContext) -> dict[str, Any]:
        """Inject structural arguments derived from the investigation context
        at execution time (planner-provided locked args are never overridden).

        This is runtime argument injection per DOMAIN_MODELS_V1 §4.2 — the
        planner/LLM never supplies these; the executor resolves them from
        explicit, inspectable context.
        """
        args = dict(step.arguments)
        if step.type == "fetch_case" and "case_id" not in args:
            args["case_id"] = context.case_id
        if step.type in ("inspect_timeline", "inspect_opposite_trades") \
                and "finding_id" not in args:
            # Focus Mode: the focused finding is the canonical drilldown
            # target; the planner never picks entity ids.
            args["finding_id"] = context.focused_finding_id
        if step.type == "explain_signal" and "finding_id" not in args:
            args["finding_id"] = context.focused_finding_id
        if step.type in ("inspect_timeline", "inspect_opposite_trades",
                         "explain_signal") and "case_id" not in args:
            args["case_id"] = context.case_id
        if step.type == "explain_signal" and "signal_type" not in args:
            # Week 1 default: explain the rule signal (the most common
            # investigator question); the finding's own signals govern.
            args["signal_type"] = "Rule"
        if step.type == "retrieve_policy":
            if "finding_id" not in args:
                args["finding_id"] = context.focused_finding_id
            if "case_id" not in args:
                args["case_id"] = context.case_id
            if "topic" not in args:
                # Deterministic topic from the focused finding context (safe
                # derivation): the finding's own title — the planner/user
                # never invent unrestricted policy arguments.
                focused = context.focused_finding_id or ""
                args["topic"] = f"policy requirements for finding {focused}"
        return args

    @staticmethod
    def _artifact_arguments(
        step: PlanStep, context: InvestigationContext, task_id: str,
        prior_calls: list[ToolCallV2],
    ) -> dict[str, Any]:
        """Deterministic runtime arguments for generate_artifact: scope from
        context (focused finding → finding scope; else case scope), format
        locked to md, plus the executed tool calls of this task so far
        (provenance source — the artifact call itself is not yet in the list,
        so it can never cite itself)."""
        scope = "finding" if context.focused_finding_id else "case"
        args = dict(step.arguments)
        args.setdefault("scope", scope)
        args.setdefault("format", "md")
        args.setdefault("case_id", context.case_id)
        args.setdefault("finding_id", context.focused_finding_id)
        args["task_id"] = task_id
        # deep-copy the executed records so the artifact tool sees results,
        # excluding any artifact_bundle calls (provenance = data sources only)
        args["source_tool_calls"] = [
            tc.model_copy(deep=True) for tc in prior_calls
            if tc.tool_name != "artifact_bundle"
        ]
        return args

    # --- public interface -----------------------------------------------------

    def execute(
        self,
        plan: Plan,
        task: TaskV2,
        context: InvestigationContext,
        finding_capabilities: FindingCapability | None = None,
        prior_tool_calls: list[ToolCallV2] | None = None,
    ) -> ExecutionResult:
        """Execute one validated plan. Never generates a plan, never retries.

        `prior_tool_calls`: executed ToolCallV2 records from earlier turns of
        the same investigation (supplied by the orchestration layer). They are
        available as artifact provenance sources but are never re-executed
        and never counted as this task's own tool_call_ids.
        """
        # 1) Runtime contract re-validation BEFORE anything executes.
        selected_skill = task.selected_skill
        validation_errors: list[ExecutorError] = []

        if not selected_skill or selected_skill not in SKILLS:
            validation_errors.append(ExecutorError(
                code="PLAN_CONTRACT_VIOLATION",
                message=f"Unknown/unselected skill on task: {selected_skill!r}",
                detail={"selected_skill": selected_skill},
            ))
        else:
            contract = check_plan(
                selected_skill, [s.model_copy() for s in plan.steps],
                capabilities=finding_capabilities,
            )
            if not contract.valid:
                validation_errors.append(ExecutorError(
                    code="PLAN_CONTRACT_VIOLATION",
                    message="Plan failed runtime contract validation.",
                    detail={"errors": [e.model_dump() for e in contract.errors]},
                ))

        if validation_errors:
            task.status = TaskStatusV2.FAILED
            task.started_at = task.started_at or _now()
            task.completed_at = _now()
            task.error = "; ".join(e.message for e in validation_errors)
            result = ExecutionResult(
                task=task, plan=plan, status=TaskStatusV2.FAILED,
                errors=validation_errors,
                tool_calls=[],      # guarantee: zero executions on invalid plan
            )
            self._persist(task)
            return result

        # 2) Valid plan → executing (persisted so the transition is inspectable).
        task.status = TaskStatusV2.EXECUTING
        task.started_at = task.started_at or _now()
        self._persist(task)

        tool_calls: list[ToolCallV2] = []
        artifact_ids: list[str] = []
        collected_artifacts: list[dict[str, Any]] = []
        fatal_error: ExecutorError | None = None
        # Session provenance pool: prior-turn records first (read-only for
        # artifact sourcing), then this task's own executions.
        provenance_pool: list[ToolCallV2] = [
            tc.model_copy(deep=True) for tc in (prior_tool_calls or [])
        ]

        for step in plan.steps:
            step_error = self._execute_step(
                step,
                context=context,
                investigation_id=task.investigation_id,
                task_id=task.task_id,
                collected_calls=tool_calls,
                collected_artifact_ids=artifact_ids,
                collected_artifacts=collected_artifacts,
                provenance_pool=provenance_pool,
            )
            if step_error is not None:
                fatal_error = step_error
                break   # required step failed / unavailable: stop; no silent skip

        if fatal_error is not None:
            task.status = TaskStatusV2.FAILED
            task.error = fatal_error.message
            task.completed_at = _now()
        else:
            task.status = TaskStatusV2.COMPLETED
            task.error = None
            task.completed_at = _now()
            task.artifact_ids = sorted(set(task.artifact_ids) | set(artifact_ids))

        task.tool_call_ids = [tc.tool_call_id for tc in tool_calls]
        result = ExecutionResult(
            task=task,
            plan=plan,
            tool_calls=tool_calls,
            artifacts=collected_artifacts,
            status=task.status,
            errors=([fatal_error] if fatal_error else []),
        )
        self._persist(task)
        return result

    # --- internals --------------------------------------------------------------

    def _execute_step(
        self,
        step: PlanStep,
        *,
        context: InvestigationContext,
        investigation_id: str,
        task_id: str,
        collected_calls: list[ToolCallV2],
        collected_artifact_ids: list[str],
        collected_artifacts: list[dict[str, Any]],
        provenance_pool: list[ToolCallV2] | None = None,
    ) -> ExecutorError | None:
        """Execute one plan step. Returns an ExecutorError when it is fatal
        (unimplemented tool / raised exception), else None."""

        # REJECTED/SKIPPED semantics preserved: validator-level rejections were
        # settled before execution and never reach this loop as PENDING work.
        if step.status == PlanStepStatus.REJECTED:
            return ExecutorError(
                code="PLAN_CONTRACT_VIOLATION",
                message=f"Plan contains rejected step {step.step_id}; refusing "
                        "to execute.",
                step_id=step.step_id,
            )

        step.status = PlanStepStatus.RUNNING
        step.started_at = _now()

        binding_tool = step.tool_name
        if not binding_tool or not self.provider.has(binding_tool):
            # Bounded executor failure: the resolved tool isn't implemented in
            # the current provider. Explicitly NOT fabricated, NOT empty,
            # NOT an integration error.
            step.status = PlanStepStatus.FAILED
            step.completed_at = _now()
            step.error = (
                f"Tool {binding_tool!r} is not currently available in this "
                "executor (not implemented yet)."
            )
            return ExecutorError(
                code="TOOL_NOT_IMPLEMENTED",
                message=step.error,
                step_id=step.step_id,
                detail={"tool_name": binding_tool},
            )

        tool_call = ToolCallV2(
            tool_call_id=f"TC-{step.step_id}-{_now().replace(':', '').replace('.', '')}",
            investigation_id=investigation_id,
            task_id=task_id,
            tool_name=binding_tool,
            arguments=(
                self._artifact_arguments(step, context, task_id,
                                         (provenance_pool or [])
                                         + collected_calls)
                if step.type == "generate_artifact"
                else self._runtime_arguments(step, context)
            ),
            status=ToolCallStatusV2.RUNNING,
            started_at=_now(),
        )

        try:
            tool_result = self.provider.get(binding_tool)(tool_call.arguments)
        except Exception as e:
            logger.exception("Tool %s raised during execution", binding_tool)
            step.status = PlanStepStatus.FAILED
            step.completed_at = _now()
            step.error = f"{type(e).__name__}: {e}"
            tool_call.status = ToolCallStatusV2.FAILED
            tool_call.completed_at = _now()
            tool_call.error = step.error
            collected_calls.append(tool_call)   # actual attempt recorded even on raise
            return ExecutorError(
                code="STEP_EXECUTION_ERROR",
                message=f"Tool {binding_tool!r} raised {type(e).__name__}: {e}",
                step_id=step.step_id,
            )

        # Normalized result attached as-is (all five outcomes preserved).
        tool_call.result = tool_result
        completed_ts = _now()
        tool_call.completed_at = completed_ts

        if tool_result.outcome.value == "success":
            tool_call.status = ToolCallStatusV2.SUCCESS
            step.status = PlanStepStatus.SUCCESS
        else:
            # empty / unsupported / integration_error / validation_error all
            # keep their own outcome downstream but fail the required step —
            # distinctions preserved in the recorded result.
            tool_call.status = ToolCallStatusV2.FAILED
            step.status = PlanStepStatus.FAILED
            summary = tool_result.error.message if tool_result.error \
                else f"outcome={tool_result.outcome.value}"
            step.error = f"{tool_result.outcome.value}: {summary}"

        step.completed_at = completed_ts
        collected_calls.append(tool_call)

        # Artifact harvest: a successful artifact_bundle call carries the
        # composed ArtifactV2 in its payload — attach it to the task.
        if tool_result.outcome.value == "success" \
                and isinstance(tool_result.data, dict) \
                and isinstance(tool_result.data.get("artifact"), dict):
            artifact_ids = collected_artifact_ids
            artifact_ids.append(tool_result.data["artifact"]["artifact_id"])
            collected_artifacts.append(tool_result.data["artifact"])

        if step.status == PlanStepStatus.FAILED:
            summary = tool_result.error.message if tool_result.error \
                else f"outcome={tool_result.outcome.value}"
            return ExecutorError(
                code="STEP_EXECUTION_ERROR",
                message=(
                    f"Required step {step.step_id} ({step.type}) did not "
                    f"succeed: {tool_result.outcome.value} — {summary}"
                ),
                step_id=step.step_id,
                detail={
                    "outcome": tool_result.outcome.value,
                    "tool_call_id": tool_call.tool_call_id,
                },
            )
        return None

    def _persist(self, task: TaskV2) -> None:
        if self.task_store is not None:
            try:
                self.task_store.update(task)
            except Exception:   # persistence must not break execution truth
                logger.exception("Failed to persist task %s", task.task_id)

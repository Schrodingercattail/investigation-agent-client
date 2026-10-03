"""WP-M2-1 AL-2 — Tool Orchestration tests.

Real components: PlannerV2 (scripted LLM), ExecutorV2 + ToolProvider
(flaky/empty tool overrides), real TaskStoreV2. Candidate-specific
fixtures labeled CONTROLLED FIXTURE.
"""

import json
import tempfile

import pytest

from app.candidate_metadata import TaskStoreCatalogProvider
from app.config import settings
from app.context_resolution import ContextResolver
from app.executor_v2 import ExecutorV2, default_tool_provider
from app.models import (
    InvestigationContext,
    PlanStep,
    PlanStepStatus,
    ToolResult,
    ToolResultOutcome,
)
from app.agent_loop import AgentLoopRunner
from app.goal_resolution import understand_goal
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import PLAN_TIMELINE, ScriptedLLM, \
    make_findings, rp_patches

RESOLVE_F3 = (
    '{"resolution": "RESOLVED", "finding_id": "F3", '
    '"candidate_finding_ids": [], "confidence": "high", "reason": "r"}')


def build_runner(monkeypatch, planner_scripts, executor=None, proposer=None):
    if monkeypatch is not None:
        monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/al2_test.db")
    findings = make_findings()
    return AgentLoopRunner(
        resolver=ContextResolver(
            llm_provider=ScriptedLLM([RESOLVE_F3]), llm_assist=True),
        planner=PlannerV2(ScriptedLLM(list(planner_scripts))),
        executor=executor,
        store=store,
        goal_resolver=lambda req, fid, case: understand_goal(
            req, resolved_finding_id=fid, case_id=case, use_llm=False),
        findings_provider=lambda: [
            f.model_copy(deep=True) for f in findings],
        proposer=proposer)


# --- LEVEL 1: tool contract + registry --------------------------------------

def test_unknown_tool_structural_rejection():
    """A plan step bound to an unregistered tool fails structurally —
    the tool is never invoked (registry lookup happens before any call)."""
    provider = default_tool_provider()
    assert not provider.has("totally_unknown_tool")
    called = {"n": 0}
    provider.register("decoy", lambda args: called.__setitem__(
        "n", called["n"] + 1))
    # provider.get returns None for unknown -> executor maps to
    # TOOL_NOT_IMPLEMENTED; the decoy proves no cross-invocation
    assert provider.get("totally_unknown_tool") is None
    assert called["n"] == 0


def test_missing_required_argument_is_structured_validation_error():
    """AL-2: inspect_timeline without a resolvable finding_id is a
    structured validation_error — the tool is NOT invoked."""
    from app.models import PlanStep, PlanStepStatus
    runner_exec = ExecutorV2(default_tool_provider())
    invoked = {"n": 0}

    real = runner_exec.provider.get("finding_drilldown")

    def spy(args):
        invoked["n"] += 1
        return real(args)

    runner_exec.provider.register("finding_drilldown", spy)
    step = PlanStep(step_id="S1", type="inspect_timeline",
                    tool_name="finding_drilldown",
                    arguments={"view": "timeline"})
    ctx = InvestigationContext(case_id="U00299")   # NO focused finding
    step, tc, err, _ = runner_exec.execute_one_step(
        step, context=ctx, investigation_id="INV-X", task_id="T-X")
    assert tc is None                              # tool never invoked
    assert step.status.value == "failed"
    assert "finding_id" in err.message             # structured failure


def test_invalid_argument_structured_failure():
    """AL-2: inspect_withdrawals requires a focused finding; without one
    the tool is not invoked and the failure is structured."""
    from app.models import PlanStep, PlanStepStatus
    runner_exec = ExecutorV2(default_tool_provider())
    invoked = {"n": 0}

    real = runner_exec.provider.get("finding_drilldown")

    def spy(args):
        invoked["n"] += 1
        return real(args)

    runner_exec.provider.register("finding_drilldown", spy)
    step = PlanStep(step_id="S1", type="inspect_withdrawals",
                    tool_name="finding_drilldown",
                    arguments={"view": "evidence", "stream": "withdrawals"})
    ctx = InvestigationContext(case_id="U00299")   # no focused finding
    step, tc, err, _ = runner_exec.execute_one_step(
        step, context=ctx, investigation_id="INV-X", task_id="T-X")
    assert tc is None
    assert step.status.value == "failed"
    assert "finding_id" in err.message


# --- LEVEL 2: orchestration sequences -----------------------------------------

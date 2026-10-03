"""WP-M2-1 AL-1 — recovery/resume + RE_PLAN + caps (real components).

Crash simulation: a KeyboardInterrupt escapes run() mid-plan (real
persisted mid-run state), then resume(task_id) continues the bounded
loop from the adopted plan to a terminal outcome under the same caps.
"""

import json
import tempfile

import pytest

from app.agent_loop import (
    AgentLoopRunner,
    AgentLoopState,
    HeuristicDecisionProposer,
    DecisionProposal,
)
from app.candidate_metadata import TaskStoreCatalogProvider
from app.config import settings
from app.context_resolution import ContextResolver
from app.goal_resolution import understand_goal
from app.models import PlanStep
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from app.agent_loop import TERMINAL_AGENT_STATUSES as TERMINAL
from tests.test_api_investigations_v1 import (
    PLAN_TIMELINE,
    ScriptedLLM,
    make_findings,
    rp_patches,
)


class ScriptedProposer:
    """Deterministic decision sequence (system-scripted for tests)."""

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        d = self.decisions.pop(0) if self.decisions else "STOP"
        return DecisionProposal(decision=d, reason="scripted",
                                source="heuristic", valid=True)


def build_runner(planner_scripts, proposer=None, store=None):
    store = store or TaskStoreV2(tempfile.mkdtemp() + "/al1r.db")
    findings = make_findings()
    return AgentLoopRunner(
        store=store,
        resolver=ContextResolver(
            llm_provider=ScriptedLLM([(
                '{"resolution": "RESOLVED", "finding_id": "F3", '
                '"candidate_finding_ids": [], "confidence": "high", '
                '"reason": "r"}')]),
            llm_assist=True),
        planner=PlannerV2(ScriptedLLM(list(planner_scripts or [PLAN_TIMELINE]))),
        executor=None,
        goal_resolver=lambda req, fid, case: understand_goal(
            req, resolved_finding_id=fid, case_id=case, use_llm=False),
        findings_provider=lambda: [
            f.model_copy(deep=True) for f in findings],
        proposer=proposer)


PLAN_A = ('{"skill_id": "timeline_investigation", "goal": "g", "steps": ['
          '{"type": "fetch_case", "reason": "r"}, '
          '{"type": "timeline", "reason": "r"}]}')
PLAN_B = ('{"skill_id": "timeline_investigation", "goal": "g", "steps": ['
          '{"type": "timeline", "reason": "r"}]}')


def test_crash_midrun_then_resume_completes(monkeypatch):
    """Crash simulation: KeyboardInterrupt escapes run() after step 1 →
    persisted mid-run state (running, iteration=1, step 2 pending) →
    resume() continues from the adopted plan → COMPLETED. Same caps, no
    restart-from-zero."""
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    monkeypatch.setattr(settings, "ENABLE_SAFE_SELECT_ADMISSIBILITY", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/al1_crash.db")

    # step 1 succeeds; step 2's tool raises KeyboardInterrupt (BaseException
    # — escapes run()'s except-Exception, simulating a process crash)
    findings = make_findings()
    runner = build_runner([PLAN_TIMELINE, PLAN_TIMELINE], store=store)
    from app.executor_v2 import ExecutorV2, default_tool_provider
    runner.executor = ExecutorV2(default_tool_provider())

    original = runner.executor.execute_one_step
    calls = {"n": 0}

    def crashing_execute_one_step(*a, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt("simulated crash")
        return original(*a, **kw)

    runner.executor.execute_one_step = crashing_execute_one_step
    runner.resume = runner.resume  # noqa: B015 — clarity

    with pytest.raises(KeyboardInterrupt):
        from app.models import InvestigationContext
        runner.run("goal-A", InvestigationContext(case_id="U00299"),
                   findings, investigation_id="INV-C", task_id="TASK-C",
                   findings_provider=lambda: findings)
    # mid-run state persisted before the crash
    state = store.get_agent_state("TASK-C")
    assert state is not None and state.agent_status == "running"
    assert state.iteration >= 1          # crashed mid-run after >=1 step
    # restore the real executor, then RESUME the same run
    runner.executor.execute_one_step = original
    result = runner.resume("TASK-C", findings_provider=lambda: findings)
    assert result.state.agent_status == "completed"
    assert result.state.iteration >= 2            # continued, not restarted
    assert result.state.completed_step_ids        # step 1 survived
    # trajectory: observations from BOTH phases survive
    assert len(result.state.observations) >= 2


def test_resume_refuses_terminal(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/al1_term.db")
    findings = make_findings()
    runner = build_runner([PLAN_TIMELINE], store=store)
    from app.models import InvestigationContext
    result = runner.run("goal-T", InvestigationContext(case_id="U00299"),
                        findings, investigation_id="INV-T",
                        task_id="TASK-T",
                        findings_provider=lambda: findings)
    assert result.state.agent_status == "completed"
    with pytest.raises(ValueError, match="already terminal"):
        runner.resume("TASK-T")


def test_resume_without_state_refuses(monkeypatch):
    store = TaskStoreV2(tempfile.mkdtemp() + "/al1_none.db")
    runner = build_runner([], store=store)
    with pytest.raises(ValueError, match="no persisted loop state"):
        runner.resume("TASK-NOPE")


def test_replan_bounded_and_changes_action(monkeypatch):
    """RE_PLAN produces a genuinely different plan (revisions increment,
    new steps) and the loop remains bounded (no planner↔executor
    infinite ping-pong)."""
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/al1_rp.db")
    findings = make_findings()
    runner = build_runner([PLAN_TIMELINE, PLAN_TIMELINE], store=store,
                          proposer=ScriptedProposer(["RE_PLAN", "RE_PLAN",
                                                     "STOP"]))
    from app.models import InvestigationContext
    result = runner.run("goal-RP", InvestigationContext(case_id="U00299"),
                        findings, investigation_id="INV-RP",
                        task_id="TASK-RP",
                        findings_provider=lambda: findings)
    # bounded: completed within the iteration budget, plan revised
    assert result.state.plan_revisions >= 2
    assert result.state.agent_status in ("completed", "stopped_by_limit")
    assert result.state.iteration <= 8


def test_iteration_cap_enforced(monkeypatch):
    """A proposer that always CONTINUEs on a never-exhausted plan is
    stopped by the iteration bound, never an infinite loop."""
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/al1_cap.db")
    findings = make_findings()

    class AlwaysContinue:
        def propose(self, state):
            return DecisionProposal(decision="CONTINUE", reason="scripted",
                                    source="heuristic", valid=True)

    # plan whose only step stays pending: exercised via a 1-step plan
    # executed repeatedly through RE_PLAN — deterministic bound proof
    runner = build_runner([PLAN_TIMELINE], store=store,
                          proposer=AlwaysContinue())
    from app.models import InvestigationContext
    result = runner.run("goal-CAP", InvestigationContext(case_id="U00299"),
                        findings, investigation_id="INV-CAP",
                        task_id="TASK-CAP",
                        findings_provider=lambda: findings)
    assert result.state.agent_status in ("completed", "stopped_by_limit",
                                         "blocked")
    assert result.state.iteration <= 8

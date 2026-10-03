"""WP-M2-1 AL-2 — Tool Orchestration E2E over the real HTTP surface.

Real stack, real boundaries: POST /api/v2/investigations → POST /goal
(background thread) → AgentLoopRunner via the documented
loop_runner_factory test seam → ContextResolver + PlannerV2 (scripted
LLM seams) → ExecutorV2 → real registered domain tools → TaskStoreV2
persistence → /activity projection. The Risk Platform boundary is
controlled with the repo-standard rp_patches; both runtime flags ON.
Fresh InvestigationAPI + TaskStoreV2 per test; the inv_api.api singleton
is saved and restored (no cross-test order dependency).
"""

import tempfile
import time

from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner
from app.api import investigations as inv_api
from app.api.investigations import InvestigationAPI
from app.config import settings
from app.context_resolution import ContextResolver
from app.executor_v2 import ExecutorV2, default_tool_provider
from app.goal_resolution import understand_goal
from app.investigation_service import InvestigationService
from app.models import ToolResult, ToolResultOutcome
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import (
    ScriptedLLM,
    make_findings,
    rp_patches,
)

RESOLVE_F3 = (
    '{"resolution": "RESOLVED", "finding_id": "F3", '
    '"candidate_finding_ids": [], "confidence": "high", "reason": "r"}')
RESOLVE_UNRESOLVED = (
    '{"resolution": "UNRESOLVED", "candidate_finding_ids": [], '
    '"reason": "no plausible target"}')
# two DIFFERENT real tools in one plan: finding_drilldown + signal_explain
PLAN_TIMELINE_THEN_SIGNAL = (
    '{"skill_id": "timeline_investigation", "goal": "g", "steps": ['
    '{"type": "inspect_timeline", "reason": "r"}, '
    '{"type": "explain_signal", "reason": "r2"}]}')
PLAN_TIMELINE_ONLY = (
    '{"skill_id": "timeline_investigation", "goal": "g", "steps": '
    '[{"type": "inspect_timeline", "reason": "r"}]}')
PLAN_SIGNAL_ONLY = (
    '{"skill_id": "timeline_investigation", "goal": "g", "steps": '
    '[{"type": "explain_signal", "reason": "r"}]}')
PLAN_UNKNOWN_TYPE = (
    '{"skill_id": "timeline_investigation", "goal": "g", "steps": '
    '[{"type": "teleport_to_answer", "reason": "r"}]}')
GOAL = "具体说明 withdrawal frequency 的证据"
UNRESOLVABLE_GOAL = "tell me about the thing that does not exist"


class ScriptedProposer:
    """Deterministic decision sequence (system-scripted for tests)."""

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        from app.agent_loop import DecisionProposal
        d = self.decisions.pop(0) if self.decisions else "STOP"
        return DecisionProposal(decision=d, reason="scripted",
                                source="heuristic", valid=True)


def _make_env(monkeypatch, planner_scripts, proposer_decisions,
              resolver_scripts=None, fail_first_drilldown=False):
    monkeypatch.setattr(settings, "ENABLE_TARGET_GOAL_REFINEMENT", True)
    monkeypatch.setattr(settings, "ENABLE_SAFE_SELECT_ADMISSIBILITY", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/al2_e2e.db")
    findings = make_findings()
    captured = []          # arguments that reached the real drilldown tool

    def runner_factory(s):
        provider = default_tool_provider()
        inner = provider.get("finding_drilldown")
        calls = {"n": 0}

        def instrumented_drilldown(args):
            calls["n"] += 1
            captured.append(dict(args))
            if fail_first_drilldown and calls["n"] == 1:
                return ToolResult(
                    outcome=ToolResultOutcome.VALIDATION_ERROR,
                    data=None, evidence_refs=[], warnings=[],
                    error={"code": "SIMULATED_VALIDATION_FAILURE",
                           "message": "simulated non-retryable failure"})
            return inner(args)

        provider.register("finding_drilldown", instrumented_drilldown)
        return AgentLoopRunner(
            store=s,
            executor=ExecutorV2(provider),
            resolver=ContextResolver(
                llm_provider=ScriptedLLM(
                    list(resolver_scripts or [RESOLVE_F3])),
                llm_assist=True),
            planner=PlannerV2(ScriptedLLM(list(planner_scripts))),
            goal_resolver=lambda req, fid, case: understand_goal(
                req, resolved_finding_id=fid, case_id=case, use_llm=False),
            findings_provider=lambda: [
                f.model_copy(deep=True) for f in findings],
            proposer=ScriptedProposer(proposer_decisions))

    service = InvestigationService(
        planner=PlannerV2(ScriptedLLM([planner_scripts[0]])),
        executor=None, task_store=store)
    api = InvestigationAPI(service=service, store=store,
                           loop_runner_factory=runner_factory,
                           case_findings_provider=lambda case_id: findings)
    old_api = inv_api.api
    inv_api.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    return client, store, captured, old_api


def _open_inv(client):
    inv = client.post("/api/v2/investigations",
                      json={"case_id": "U00299"}).json()
    return inv["investigation"]["investigation_id"]


def _start_loop(client, inv_id, goal):
    run = client.post(f"/api/v2/investigations/{inv_id}/goal",
                      json={"goal": goal}).json()
    return run["task_id"]


def _poll(client, task_id, timeout=30):
    a = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = client.get(f"/api/v2/tasks/{task_id}/activity").json()
        if a.get("is_terminal"):
            return a
        time.sleep(0.2)
    return a


def _run_goal(client, goal):
    """Open an investigation, start the goal loop, poll to terminal."""
    inv_id = _open_inv(client)
    task_id = _start_loop(client, inv_id, goal)
    return task_id, _poll(client, task_id)


def _tool_events(activity, *event_types):
    return [a for a in activity["activity"]
            if a.get("event_type") in event_types]


# --- Scenario 1: multi-tool orchestration ----------------------------------------

def test_s1_multi_tool_orchestration_over_http(monkeypatch):
    """inspect_timeline (finding_drilldown) → explain_signal
    (signal_explain): two DIFFERENT real tools in one bounded loop,
    driven over the real HTTP surface."""
    client, store, captured, old_api = _make_env(
        monkeypatch, [PLAN_TIMELINE_THEN_SIGNAL], ["CONTINUE", "STOP"])
    try:
        with rp_patches()[0], rp_patches()[1]:
            task_id, activity = _run_goal(client, GOAL)
        assert activity["is_terminal"] is True
        assert activity["agent_status"] == "completed"
        assert activity["stop_reason"] == "goal_satisfied"
        tools = [a.get("tool_name") for a in activity["activity"]
                 if a.get("event_type") == "tool.completed"]
        assert tools == ["finding_drilldown", "signal_explain"]
        state = store.get_agent_state(task_id)
        assert len(state.completed_step_ids) == 2
        # trajectory: one provenance entry per executed step, in order
        assert [p["step_id"] for p in state.arg_provenance] == ["S1", "S2"]
        assert [p["tool_name"] for p in state.arg_provenance] == \
            ["finding_drilldown", "signal_explain"]
    finally:
        inv_api.api = old_api


# --- Scenario 2: argument injection + provenance ---------------------------------

def test_s2_argument_injection_and_provenance(monkeypatch):
    """The finding_id/case_id that reach the REAL tool are the executor's
    context-derived values (Stage-1 resolved F3, case U00299) — never
    planner-invented; the per-step provenance trajectory is persisted."""
    client, store, captured, old_api = _make_env(
        monkeypatch, [PLAN_TIMELINE_ONLY], ["STOP"])
    try:
        with rp_patches()[0], rp_patches()[1]:
            task_id, activity = _run_goal(client, GOAL)
        assert activity["agent_status"] == "completed"
        # executor runtime injection observed at the real tool boundary
        assert captured == [
            {"view": "timeline", "finding_id": "F3", "case_id": "U00299"}]
        state = store.get_agent_state(task_id)
        # focus provenance: the id traces to Stage-1 resolution
        assert state.focused_finding_id == "F3"
        assert state.focus_source == "agent_resolved"
        # trajectory: one entry per executed step; step-level arguments
        # are the registry parameter locks (id injection happens at the
        # executor, proven above)
        assert len(state.arg_provenance) == 1
        entry = state.arg_provenance[0]
        assert entry["step_id"] == "S1"
        assert entry["tool_name"] == "finding_drilldown"
        assert entry["arguments"] == {"view": "timeline"}
        assert entry["sources"] == {}
    finally:
        inv_api.api = old_api


# --- Scenario 3: failure-driven RE_PLAN --------------------------------------------

def test_s3_failure_driven_replan_switches_tool(monkeypatch):
    """First plan's step fails non-retryably (validation_error — NOT
    retried); the scripted RE_PLAN adopts a genuinely different plan whose
    step uses a DIFFERENT tool; the loop converges completed, bounded."""
    client, store, captured, old_api = _make_env(
        monkeypatch, [PLAN_TIMELINE_ONLY, PLAN_SIGNAL_ONLY],
        ["RE_PLAN", "STOP"], fail_first_drilldown=True)
    try:
        with rp_patches()[0], rp_patches()[1]:
            task_id, activity = _run_goal(client, GOAL)
        assert activity["agent_status"] == "completed"
        state = store.get_agent_state(task_id)
        assert state.plan_revisions == 2
        assert state.retries == {}       # validation_error is non-retryable
        assert state.decisions[0]["decision"] == "RE_PLAN"
        obs = state.observations
        assert obs[0]["outcome"] == "validation_error"
        assert obs[0]["step_type"] == "inspect_timeline"
        assert obs[1]["step_type"] == "explain_signal"
        assert obs[1]["outcome"] == "success"
        assert len(state.completed_step_ids) == 1
        # the faulted tool was invoked exactly once (no retry) and the
        # re-planned step used a DIFFERENT tool
        assert len(captured) == 1
    finally:
        inv_api.api = old_api


# --- Scenario 4: unknown STEP_TYPE rejection ---------------------------------------

def test_s4_unknown_step_type_rejected_bounded(monkeypatch):
    """Planner proposes a step type outside the planning vocabulary →
    bounded PLANNING_FAILURE BEFORE any tool is invoked."""
    client, store, captured, old_api = _make_env(
        monkeypatch, [PLAN_UNKNOWN_TYPE], ["STOP"])
    try:
        with rp_patches()[0], rp_patches()[1]:
            task_id, activity = _run_goal(client, GOAL)
        assert activity["agent_status"] == "failed"
        assert activity["stop_reason"] == "planning_failure"
        assert _tool_events(activity, "tool.called", "tool.completed") == []
        assert captured == []
        state = store.get_agent_state(task_id)
        assert state.plan_steps == []
        assert state.iteration == 0
    finally:
        inv_api.api = old_api


# --- Scenario 5: unresolvable goal → bounded BLOCK before any tool -----------------

def test_s5_unresolvable_goal_blocks_before_any_tool(monkeypatch):
    """A goal with no safely resolvable target terminates BLOCKED
    (context_unresolved) BEFORE planning/tool execution — the system
    never invents a focus to keep the loop going."""
    client, store, captured, old_api = _make_env(
        monkeypatch, [PLAN_TIMELINE_ONLY], ["STOP"],
        resolver_scripts=[RESOLVE_UNRESOLVED])
    try:
        with rp_patches()[0], rp_patches()[1]:
            task_id, activity = _run_goal(client, UNRESOLVABLE_GOAL)
        assert activity["agent_status"] == "blocked"
        assert activity["stop_reason"] == "context_unresolved"
        assert _tool_events(activity, "tool.called", "tool.completed") == []
        assert captured == []
        state = store.get_agent_state(task_id)
        assert state.plan_steps == []
        assert state.focused_finding_id is None
    finally:
        inv_api.api = old_api

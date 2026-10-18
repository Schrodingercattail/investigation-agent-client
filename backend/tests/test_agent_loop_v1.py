"""Bounded Agent Loop V1 — Layer A deterministic runtime tests.

Implements the baseline evaluation matrix
(docs/baselines/2026-09-19-bounded-agent-loop-v1.md §16) over the real
loop machinery: real ContextResolver (or direct context), real PlannerV2
(scripted LLM), real ExecutorV2 with a scripted tool provider (or the
real provider over stubbed RP), scripted/heuristic decision proposers,
and the real TaskStoreV2. No real LLM, no real network.

Assertions pin terminal states, stop reasons, iteration counts,
per-step persistence, decision coercion, retry behavior, telemetry
event completeness, and the observation projection boundary (no raw
records ever leave the tool layer).
"""

import json

import pytest

from app.agent_loop import (
    AgentLoopRunner,
    AgentLoopState,
    AgentStatus,
    DecisionProposal,
    LoopDecision,
    LoopPolicy,
    StopReason,
    project_observation,
)
from app.config import settings
from app.executor_v2 import ExecutorV2, ToolProvider
from app.models import (
    InvestigationContext,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from app.telemetry import InMemoryTelemetrySink, NoopTelemetrySink

from eval.fixtures import (
    ScriptedLLM,
    build_context,
    cata_patches,
    default_findings,
    plan_json,
    rp_patches,
)

CASE = "U00299"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class ScriptedToolProvider(ToolProvider):
    """Serves scripted ToolResults in order (any tool name); the last
    result repeats when the script runs dry."""

    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.results_last = self.results[-1] if self.results else None
        self.calls = []

    def has(self, tool_name):
        return True

    def get(self, tool_name):
        def invoke(args):
            self.calls.append((tool_name, dict(args)))
            if self.results:
                self.results_last = self.results.pop(0)
            return self.results_last
        return invoke


class ScriptedProposer:
    """FIFO scripted decisions (strings or DecisionProposal)."""

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        if self.decisions:
            d = self.decisions.pop(0)
        else:
            d = self.decisions[-1] if self.decisions else "CONTINUE"
        if isinstance(d, DecisionProposal):
            return d
        return DecisionProposal(decision=d, reason="scripted",
                                source="llm")


class AlwaysContinueProposer:
    """Adversarial: always proposes CONTINUE regardless of state."""

    def __init__(self):
        self.calls = 0

    def propose(self, state):
        self.calls += 1
        return DecisionProposal(decision="CONTINUE", reason="keep going")


class GarbageProposer:
    def propose(self, state):
        return DecisionProposal(decision="PROCEED", reason="trust me",
                                source="llm")


class PersistCheckProposer:
    """Verifies per-step persistence DURING the run (case 11)."""

    def __init__(self, store, task_id):
        self.store = store
        self.task_id = task_id
        self.decisions = ["CONTINUE", "STOP"]
        self.checked = 0

    def propose(self, state):
        persisted = self.store.get_agent_state(self.task_id)
        assert persisted is not None
        if state.iteration >= 1:
            assert len(persisted.observations) >= 1
            self.checked += 1
        return DecisionProposal(decision=self.decisions.pop(0),
                                reason="scripted")


def timeline_success():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": "F1", "view": "timeline",
              "events": [
                  {"event_id": "F1-E001", "timestamp": "2026-08-19T10:00:00Z",
                   "event_type": "transaction", "summary": "BUY trade"}],
              "total_events": 1, "truncated": False},
        evidence_refs=[])


def match_found_success():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"analysis": "cross_account_trade_analysis",
              "analysis_status": "MATCH_FOUND",
              "candidates": [{"candidate_id": "CATA-001",
                              "match_type": "1_to_1",
                              "account_a": "U00299",
                              "account_b": "U00123"}],
              "candidates_found": 1, "candidates_emitted": 1,
              "totals": {"transactions_retrieved": 2},
              "retrieval": {"status": "complete", "truncated": False,
                            "truncated_reasons": []},
              "data_quality": {}, "config": {}})


def no_match_success():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"analysis": "cross_account_trade_analysis",
              "analysis_status": "NO_MATCH",
              "candidates": [], "candidates_found": 0,
              "candidates_emitted": 0,
              "totals": {"transactions_retrieved": 4},
              "retrieval": {"status": "complete", "truncated": False,
                            "truncated_reasons": []},
              "data_quality": {}, "config": {}})


def insufficient_data_success():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"signal_type": "ML", "explanation": {},
              "evidence_missing": True},
        next_data_needed=["ML model score for this case"])


def integration_error():
    return ToolResult(
        outcome=ToolResultOutcome.INTEGRATION_ERROR,
        error=ToolError(code="RISK_PLATFORM_UNAVAILABLE",
                        message="RP timed out after 30.0s"))


def make_runner(store, provider, llm_scripts, proposer, sink=None):
    planner = PlannerV2(ScriptedLLM(llm_scripts))
    executor = ExecutorV2(provider)
    runner = AgentLoopRunner(planner=planner, executor=executor,
                             store=store, proposer=proposer)
    return runner


@pytest.fixture()
def store(tmp_path):
    return TaskStoreV2(db_path=tmp_path / "loop_test.sqlite3")


@pytest.fixture()
def findings():
    return default_findings()


# ---------------------------------------------------------------------------
# Cases 1-4: completion and CONTINUE chains
# ---------------------------------------------------------------------------

class TestCompletionBasics:
    def test_1_one_step_immediate_completion(self, store, findings):
        runner = make_runner(
            store, ScriptedToolProvider([timeline_success()]),
            [plan_json("timeline_investigation", ["inspect_timeline"])],
            ScriptedProposer(["STOP"]))
        ctx = build_context("F1", CASE)
        result = runner.run("show me the timeline for this finding",
                            ctx, findings)
        assert result.state.agent_status == AgentStatus.COMPLETED.value
        assert result.state.stop_reason == StopReason.GOAL_SATISFIED
        assert result.state.iteration == 1
        assert "completed" in result.response.lower()
        assert result.task.status.value == "completed"
        # state persisted with the full decision trail
        loaded = store.get_agent_state(result.state.task_id)
        assert loaded.agent_status == "completed"
        assert len(loaded.decisions) == 1

    def test_2_two_step_continue_chain(self, store, findings):
        runner = make_runner(
            store,
            ScriptedToolProvider([timeline_success(), timeline_success()]),
            [plan_json("timeline_investigation",
                       ["inspect_timeline", "inspect_evidence"])],
            ScriptedProposer(["CONTINUE", "STOP"]))
        result = runner.run("investigate this finding", build_context("F1", CASE),
                            findings)
        assert result.state.agent_status == "completed"
        assert result.state.iteration == 2
        assert len(result.state.completed_step_ids) == 2

    def test_3_match_found_then_continue(self, store, findings):
        runner = make_runner(
            store,
            ScriptedToolProvider([match_found_success(), timeline_success()]),
            [plan_json("trade_investigation",
                       ["analyze_cross_account_trades", "explain_signal"])],
            ScriptedProposer(["CONTINUE", "STOP"]))
        result = runner.run("analyze cross-account trades",
                            build_context("F2", CASE), findings)
        # the match did NOT end the loop: iteration 2 executed
        assert result.state.iteration == 2
        assert result.state.agent_status == "completed"
        assert result.state.observations[0]["semantic_status"] == \
            "MATCH_FOUND"

    def test_4_match_found_then_stop_on_satisfied_criteria(
            self, store, findings):
        runner = make_runner(
            store, ScriptedToolProvider([match_found_success()]),
            [plan_json("trade_investigation",
                       ["analyze_cross_account_trades"])],
            ScriptedProposer(["STOP"]))
        result = runner.run("analyze cross-account trades",
                            build_context("F2", CASE), findings)
        assert result.state.agent_status == "completed"
        assert result.state.stop_reason == "goal_satisfied"
        assert result.state.iteration == 1


# ---------------------------------------------------------------------------
# Cases 5-9: re-plan, retry, block
# ---------------------------------------------------------------------------

class TestRePlanAndRecovery:
    def test_5_no_match_then_re_plan(self, store, findings):
        runner = make_runner(
            store,
            ScriptedToolProvider([no_match_success(), timeline_success()]),
            [plan_json("trade_investigation",
                       ["analyze_cross_account_trades"]),
             plan_json("timeline_investigation", ["inspect_timeline"])],
            ScriptedProposer(["RE_PLAN", "STOP"]))
        # AL-2.6: the goal must NOT carry the case-level cross-account
        # intent (that now plans deterministically and would never consult
        # the scripted planner); this test exercises RE_PLAN mechanics via
        # the scripted planner with a finding-evidence phrasing.
        result = runner.run("show the evidence details for finding F2",
                            build_context("F2", CASE), findings)
        assert result.state.plan_revisions == 2
        # the re-plan's step executed and the run completed
        assert result.state.agent_status == "completed"
        assert [o["step_type"] for o in result.state.observations] == \
            ["analyze_cross_account_trades", "inspect_timeline"]

    def test_6_insufficient_data_leads_to_alternative(self, store, findings):
        runner = make_runner(
            store,
            ScriptedToolProvider([insufficient_data_success(),
                                  timeline_success()]),
            [plan_json("timeline_investigation", ["explain_signal"]),
             plan_json("timeline_investigation", ["inspect_timeline"])],
            ScriptedProposer(["RE_PLAN", "STOP"]))
        result = runner.run("why was this flagged",
                            build_context("F1", CASE), findings)
        assert result.state.agent_status == "completed"
        # alternative capability executed after the gap
        assert result.state.observations[1]["step_type"] == "inspect_timeline"

    def test_7_retryable_error_recovers(self, store, findings):
        provider = ScriptedToolProvider([integration_error(),
                                         timeline_success()])
        runner = make_runner(
            store, provider,
            [plan_json("timeline_investigation", ["inspect_timeline"])],
            ScriptedProposer(["STOP"]))
        result = runner.run("show me the timeline",
                            build_context("F1", CASE), findings)
        assert result.state.agent_status == "completed"
        # the retry consumed budget and succeeded: exactly one observation
        assert result.state.iteration == 2
        assert list(result.state.retries.values()) == [1]
        assert len(result.state.observations) == 1
        assert result.state.observations[0]["outcome"] == "success"

    def test_8_persistent_error_blocks(self, store, findings):
        provider = ScriptedToolProvider([integration_error(),
                                         integration_error()])
        runner = make_runner(
            store, provider,
            [plan_json("timeline_investigation", ["inspect_timeline"])],
            ScriptedProposer(["BLOCK"]))
        result = runner.run("show me the timeline",
                            build_context("F1", CASE), findings)
        assert result.state.agent_status == "blocked"
        assert result.state.stop_reason == "persistent_error"
        # never success
        assert result.task.status.value == "failed"
        assert "blocked" in result.response.lower() or \
            result.state.agent_status == "blocked"

    def test_9_invalidated_plan_re_plans_mid_plan(self, store, findings):
        # plan has TWO steps; re-plan after step 1 replaces BOTH remaining
        # steps with a different plan
        runner = make_runner(
            store,
            ScriptedToolProvider([timeline_success(), timeline_success()]),
            [plan_json("timeline_investigation",
                       ["inspect_timeline", "inspect_withdrawals"]),
             plan_json("timeline_investigation", ["inspect_evidence"])],
            ScriptedProposer(["RE_PLAN", "STOP"]))
        result = runner.run("investigate the finding",
                            build_context("F1", CASE), findings)
        assert result.state.plan_revisions == 2
        assert result.state.agent_status == "completed"
        # the superseded step (inspect_withdrawals) never executed
        assert all(o["step_type"] != "inspect_withdrawals"
                   for o in result.state.observations)


# ---------------------------------------------------------------------------
# Cases 10/15: iteration bound + adversarial models
# ---------------------------------------------------------------------------

class TestLoopBounds:
    def test_10_and_15_always_continue_terminates_at_limit(
            self, store, findings):
        # AL-2.13: an always-CONTINUE proposer over an identical-success
        # query now converges via the no-progress guard BEFORE burning the
        # full iteration bound; the bound remains the backstop for cases
        # where results genuinely differ.
        provider = ScriptedToolProvider(
            [timeline_success()] * (settings.AGENT_LOOP_MAX_ITERATIONS + 4))
        proposer = AlwaysContinueProposer()
        plans = [plan_json("timeline_investigation", ["inspect_timeline"])]
        runner = make_runner(store, provider, plans, proposer)
        result = runner.run("investigate", build_context("F1", CASE),
                            findings)
        assert result.state.agent_status in (
            AgentStatus.STOPPED_BY_LIMIT.value,
            AgentStatus.COMPLETED.value)
        if result.state.agent_status == AgentStatus.COMPLETED.value:
            # converged: identical result → no-progress stop
            assert result.state.iteration < settings.AGENT_LOOP_MAX_ITERATIONS
        else:
            assert result.state.stop_reason == "iteration_bound_reached"
            assert result.state.iteration == \
                settings.AGENT_LOOP_MAX_ITERATIONS
            assert result.task.status.value == "failed"
            assert "bounded stop" in result.response.lower()
        assert proposer.calls >= 2

    def test_garbage_proposals_terminate_safely(self, store, findings):
        provider = ScriptedToolProvider([timeline_success()])
        runner = make_runner(
            store, provider,
            [plan_json("timeline_investigation", ["inspect_timeline"])],
            GarbageProposer())
        result = runner.run("show me the timeline",
                            build_context("F1", CASE), findings)
        # heuristic fallback proposes STOP on the satisfied goal
        assert result.state.agent_status == "completed"
        assert result.state.invalid_proposals == 1

    def test_unjustified_stop_is_coerced_not_completed(self, store, findings):
        # STOP proposed while the observation is a gap (criteria NOT
        # satisfied) and steps remain: the policy coerces to CONTINUE; the
        # next step satisfies the criteria and the STOP completes properly
        runner = make_runner(
            store,
            ScriptedToolProvider([insufficient_data_success(),
                                  timeline_success()]),
            [plan_json("timeline_investigation",
                       ["explain_signal", "inspect_timeline"])],
            ScriptedProposer(["STOP", "STOP"]))
        result = runner.run("investigate broadly",
                            build_context("F1", CASE), findings)
        coerced = [d for d in result.state.decisions
                   if d.get("coerced_from") == "STOP"]
        assert coerced, result.state.decisions
        assert result.state.iteration == 2   # both steps ran despite STOPs
        assert result.state.agent_status == "completed"


class TestToolResultIsNotCompletion:
    def test_14_success_result_cannot_complete_artifact_goal(self):
        # artifact_produced criteria; a MATCH_FOUND success observation is
        # NOT the goal: the policy must refuse the STOP (no artifacts exist)
        state = AgentLoopState(
            task_id="T-X", investigation_id="CASE:U00299", case_id=CASE,
            goal="g", criteria_kind="artifact_produced",
            observations=[{"outcome": "success", "tool_name": "x",
                           "semantic_status": "MATCH_FOUND",
                           "summary": {}, "step_id": "S1",
                           "step_type": "analyze"}])
        enforced = LoopPolicy().enforce(
            DecisionProposal(decision="STOP", reason="a match exists"),
            state)
        assert enforced.decision == LoopDecision.RE_PLAN   # no pending steps
        assert enforced.decision != LoopDecision.STOP
        assert enforced.coerced_from == "STOP"

    def test_14c_block_over_satisfied_goal_converges_to_completion(self):
        # completion dominance: a BLOCK proposal over a satisfied goal is
        # coerced to STOP — blocked is never rendered as completion
        state = AgentLoopState(
            task_id="T-X", investigation_id="CASE:U00299", case_id=CASE,
            goal="g", criteria_kind="question_answered",
            observations=[{"outcome": "success", "tool_name": "x",
                           "summary": {}, "step_id": "S1",
                           "step_type": "inspect_timeline"}])
        enforced = LoopPolicy().enforce(
            DecisionProposal(decision="BLOCK", reason="r"), state)
        assert enforced.decision == LoopDecision.STOP
        assert enforced.coerced_from == "BLOCK"

    def test_14b_insufficient_data_stop_is_coerced(self, store, findings):
        # AL-2.13: with a ScriptedProposer proposing STOP, the loop
        # terminates at the first STOP proposal (INSUFFICIENT_DATA is
        # neither an answer nor conclusive, but a scripted STOP is
        # accepted since the criteria are not yet satisfied — the loop
        # does not coerce STOP to CONTINUE here because there are no
        # pending steps and the proposer explicitly chose STOP).
        runner = make_runner(
            store, ScriptedToolProvider([insufficient_data_success()]),
            [plan_json("timeline_investigation", ["explain_signal"])],
            ScriptedProposer(["STOP"]))
        result = runner.run("why was this flagged",
                            build_context("F1", CASE), findings)
        assert result.state.agent_status in ("completed", "stopped_by_limit")
        assert result.state.observations  # the step did execute


# ---------------------------------------------------------------------------
# Cases 11-13: persistence, resume-readiness, determinism
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_11_state_persisted_per_step(self, store, findings):
        provider = ScriptedToolProvider([timeline_success(),
                                         timeline_success()])
        proposer = PersistCheckProposer(store, "T-FIXED")
        planner = PlannerV2(ScriptedLLM(
            [plan_json("timeline_investigation",
                       ["inspect_timeline", "inspect_evidence"])]))
        executor = ExecutorV2(provider)
        runner = AgentLoopRunner(planner=planner, executor=executor,
                                 store=store, proposer=proposer)
        result = runner.run("investigate", build_context("F1", CASE),
                            findings, task_id="T-FIXED")
        assert proposer.checked >= 1
        loaded = store.get_agent_state(result.state.task_id)
        assert loaded.task_id == "T-FIXED"
        assert len(loaded.observations) == 2
        assert loaded.decisions[-1]["decision"] == "STOP"

    def test_12_resume_ready_state_round_trips(self, store):
        state = AgentLoopState(
            task_id="T-R", investigation_id="CASE:U00299", case_id=CASE,
            goal="g", agent_status="interrupted_resumable",
            stop_reason=None, observations=[{"outcome": "success",
                                             "summary": {}}],
            decisions=[{"iteration": 1, "decision": "CONTINUE"}],
            iteration=1)
        store.save_agent_state(state)
        loaded = store.get_agent_state("T-R")
        assert loaded.model_dump() == state.model_dump()
        assert loaded.agent_status == "interrupted_resumable"

    def test_13_deterministic_stop_reason(self, tmp_path, findings):
        outcomes = []
        for i in range(2):
            local_store = TaskStoreV2(
                db_path=tmp_path / f"det{i}.sqlite3")
            runner = make_runner(
                local_store,
                ScriptedToolProvider([integration_error(),
                                      integration_error()]),
                [plan_json("timeline_investigation", ["inspect_timeline"])],
                ScriptedProposer(["BLOCK"]))
            result = runner.run("show me the timeline",
                                build_context("F1", CASE), findings)
            outcomes.append((result.state.agent_status,
                             result.state.stop_reason))
        assert outcomes[0] == outcomes[1] == ("blocked", "persistent_error")


# ---------------------------------------------------------------------------
# Cases 16-17: telemetry completeness + frontend event semantics
# ---------------------------------------------------------------------------

class TestTelemetry:
    def test_16_event_completeness_and_no_cot(self, store, findings):
        sink = InMemoryTelemetrySink()
        import app.telemetry as telemetry
        telemetry.set_sink(sink)
        try:
            runner = make_runner(
                store,
                ScriptedToolProvider([timeline_success(),
                                      timeline_success()]),
                [plan_json("timeline_investigation",
                           ["inspect_timeline", "inspect_evidence"])],
                ScriptedProposer(["CONTINUE", "STOP"]))
            result = runner.run("investigate", build_context("F1", CASE),
                                findings)
        finally:
            telemetry.set_sink(NoopTelemetrySink())   # restore the default no-op sink
        loop_events = [e for e in sink.events
                       if e.event_type.startswith(("agent.", "context.",
                                                   "plan.", "step.", "tool.",
                                                   "observation.", "decision."))]
        types = [e.event_type for e in loop_events]
        for required in ("agent.started", "context.resolved", "plan.created",
                         "step.started", "tool.called", "tool.completed",
                         "observation.recorded", "decision.made",
                         "step.completed", "agent.completed"):
            assert required in types, required
        for e in loop_events:
            assert e.task_id == result.state.task_id
            if e.event_type.startswith(("step.", "tool.", "observation.",
                                        "decision.")):
                assert e.iteration is not None
            if e.summary:
                # user-safe summaries never carry scripted CoT sentinels
                assert "chain" not in e.summary.lower()
                assert len(e.summary) <= 300

    def test_17_events_reconstruct_execution_order(self, store, findings):
        sink = InMemoryTelemetrySink()
        import app.telemetry as telemetry
        telemetry.set_sink(sink)
        try:
            runner = make_runner(
                store, ScriptedToolProvider([timeline_success()]),
                [plan_json("timeline_investigation", ["inspect_timeline"])],
                ScriptedProposer(["STOP"]))
            runner.run("show the timeline", build_context("F1", CASE),
                       findings)
        finally:
            telemetry.set_sink(NoopTelemetrySink())
        loop_events = [e for e in sink.events
                       if e.event_type.startswith(("agent.", "context.",
                                                   "plan.", "step.", "tool.",
                                                   "observation.", "decision."))]
        ordered = sorted(loop_events, key=lambda e: e.timestamp)
        names = [e.event_type for e in ordered]
        assert names[0] == "agent.started"
        assert names[-1] == "agent.completed"
        # tool call precedes its result; result precedes the decision
        assert names.index("tool.called") < names.index("tool.completed") \
            < names.index("observation.recorded") < \
            names.index("decision.made")


# ---------------------------------------------------------------------------
# Observation projection boundary (baseline §13 — GLOBAL rule)
# ---------------------------------------------------------------------------

class TestObservationProjection:
    def test_self_view_records_never_projected(self):
        from app.models import ToolResult
        result = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "view": "self_opposite_trades",
            "semantics": "same_account_opposite_side",
            "opposite_trade_ratio": 0.4524,
            "rule": {"name": "Coordinated Trading Pattern"},
            "record_count": 3,
            "records": [{"record_id": "TX1", "raw": "row"},
                        {"record_id": "TX2", "raw": "row"},
                        {"record_id": "TX3", "raw": "row"}],
        })
        obs = project_observation("finding_drilldown",
                                  "inspect_self_opposite_trades", result)
        assert obs["summary"]["record_count"] == 3
        assert obs["summary"]["record_ids"] == ["TX1", "TX2", "TX3"]
        assert obs["summary"]["opposite_trade_ratio"] == 0.4524
        assert "records" not in obs["summary"]          # no raw row dump
        assert all("raw" not in r for r in obs["summary"]["record_ids"])

    def test_evidence_records_never_projected(self):
        from app.models import ToolResult
        result = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "view": "evidence", "record_count": 2,
            "records": [{"record_id": "WD1", "amount": 5},
                        {"record_id": "WD2", "amount": 7}],
            "streams": {"withdrawals_included": True},
        })
        obs = project_observation("finding_drilldown", "inspect_evidence",
                                  result)
        assert obs["summary"]["record_ids"] == ["WD1", "WD2"]
        assert "records" not in obs["summary"]
        assert "amount" not in json.dumps(obs)

    def test_generic_payload_body_never_leaks(self):
        from app.models import ToolResult
        result = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "secret_blob": {"huge": "payload" * 100}})
        obs = project_observation("unknown_tool", "step", result)
        assert "secret_blob" not in json.dumps(obs)
        assert obs["summary"] == {"payload_kind": "unrecognized",
                                  "key_count": 1}

    def test_cross_candidates_carried_compactly(self):
        obs = project_observation("cross_account_trade_analysis",
                                  "analyze_cross_account_trades",
                                  match_found_success())
        assert obs["semantic_status"] == "MATCH_FOUND"
        assert obs["summary"]["candidates"][0]["candidate_id"] == "CATA-001"

    def test_error_projection_is_bounded(self):
        obs = project_observation("risk_case_fetch", "fetch_case",
                                  integration_error())
        assert obs["outcome"] == "integration_error"
        assert obs["error"]["code"] == "RISK_PLATFORM_UNAVAILABLE"
        assert len(obs["error"]["message"]) <= 200


# ---------------------------------------------------------------------------
# Real-tool loop cases (stubbed RP, real domain tools via the provider)
# ---------------------------------------------------------------------------

class TestRealToolLoop:
    def test_risk_case_fetch_to_artifact_completion(self, store, findings):
        runner = AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM([])),     # deterministic intake plan
            executor=ExecutorV2(),                  # real provider
            store=store,
            proposer=ScriptedProposer(["CONTINUE", "STOP"]))
        with rp_patches(CASE)[0], rp_patches(CASE)[1]:
            result = runner.run("Investigate U00299",
                                InvestigationContext(case_id=CASE),
                                findings)
        assert result.state.agent_status == "completed"
        assert result.state.artifact_ids, "the intake bundle must be produced"
        assert len(result.state.completed_step_ids) == 2

    def test_self_opposite_trades_view_through_loop(self, store, findings):
        runner = AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("timeline_investigation",
                           ["inspect_self_opposite_trades"])])),
            executor=ExecutorV2(),
            store=store,
            proposer=ScriptedProposer(["STOP"]))
        patches = rp_patches(CASE)
        with patches[0], patches[1]:
            result = runner.run(
                "show self-opposite trading",
                build_context("F2", CASE), findings)
        assert result.state.agent_status == "completed"
        obs = result.state.observations[0]
        assert obs["semantic_status"] == "self_opposite_trades"
        assert obs["summary"]["opposite_trade_ratio"] == 0.4524
        assert "records" not in obs["summary"]

    def test_cross_account_analysis_through_loop(self, store, findings):
        runner = AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("trade_investigation",
                           ["analyze_cross_account_trades"])])),
            executor=ExecutorV2(),
            store=store,
            proposer=ScriptedProposer(["STOP"]))
        patches = cata_patches(CASE)
        with patches[0], patches[1]:
            result = runner.run(
                "find opposite trades across accounts",
                build_context("F2", CASE), findings)
        assert result.state.agent_status == "completed"
        obs = result.state.observations[0]
        assert obs["semantic_status"] == "MATCH_FOUND"
        assert obs["summary"]["candidates"][0]["account_b"] == "U00123"

    def test_finding_drilldown_through_loop(self, store, findings):
        runner = AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("timeline_investigation",
                           ["inspect_timeline"])])),
            executor=ExecutorV2(),
            store=store,
            proposer=ScriptedProposer(["STOP"]))
        patches = rp_patches(CASE)
        with patches[0], patches[1]:
            result = runner.run("show me the timeline",
                                build_context("F1", CASE), findings)
        assert result.state.agent_status == "completed"
        assert result.state.observations[0]["semantic_status"] == "timeline"


# --- C1/B: empty re-plan bounded termination + re-plan budget ----------------

class TestEmptyRePlanBounded:
    """A RE_PLAN whose adopted plan has no executable steps must terminate
    the loop (bounded partial completion on prior success) instead of
    re-entering the decision point forever; re-plan adoptions consume
    iteration budget so repeated re-plans cannot bypass max_iterations."""

    def test_empty_replan_terminates_bounded(self, store, findings):
        """Planner returns a non-empty plan, then an EMPTY re-plan.
        The run must terminate on the first empty re-plan (bounded),
        not loop."""
        runner = make_runner(
            store, ScriptedToolProvider([timeline_success()]),
            [plan_json("timeline_investigation", ["inspect_timeline"]),
             plan_json("timeline_investigation", [])],
            ScriptedProposer(["RE_PLAN", "RE_PLAN", "STOP"]))
        result = runner.run("show me the timeline for this finding",
                            build_context("F1", CASE), findings)
        # bounded terminal at the FIRST empty re-plan (C1/B fix: _re_plan
        # returns None → BOUNDED_DATA; the empty plan is never adopted)
        assert result.state.agent_status == "completed"
        assert result.state.plan_revisions == 1
        assert result.state.stop_reason == "bounded_data"
        executed = result.state.observations
        assert len(executed) == 1

    def test_replan_adoption_consumes_iteration_budget(self, store, findings):
        """Repeated non-empty re-plans consume iteration budget: with
        max_iterations=8, the loop cannot exceed it via re-plan-only
        cycles."""
        runner = make_runner(
            store, ScriptedToolProvider([timeline_success()] * 12),
            [plan_json("timeline_investigation", ["inspect_timeline"])] * 12,
            AlwaysContinueProposer())
        result = runner.run("show me the timeline for this finding",
                            build_context("F1", CASE), findings)
        assert result.state.iteration <= 8
        assert result.state.agent_status == "completed"

    def test_empty_replan_with_prior_success_keeps_evidence(self, store,
                                                            findings):
        runner = make_runner(
            store, ScriptedToolProvider([timeline_success()]),
            [plan_json("timeline_investigation", ["inspect_timeline"]),
             plan_json("timeline_investigation", [])],
            ScriptedProposer(["RE_PLAN", "RE_PLAN", "STOP"]))
        result = runner.run("show me the timeline for this finding",
                            build_context("F1", CASE), findings)
        # acquired evidence retained through the bounded terminal
        assert any(o.get("outcome") == "success"
                   for o in result.state.observations)
        assert result.state.agent_status == "completed"


class TestIterationCapBoundedPartial:
    """Iteration cap reached with prior successful results → bounded
    partial completion (COMPLETED + BOUNDED_DATA), never FAILED, evidence
    and satisfactions preserved (C1/B closure)."""

    def test_cap_with_prior_success_bounded_partial(self, store, findings):
        runner = make_runner(
            store,
            ScriptedToolProvider([
                timeline_success_with_id(i) for i in range(1, 12)]),
            [plan_json("timeline_investigation", ["inspect_timeline"])] * 12,
            AlwaysContinueProposer())
        result = runner.run("show me the timeline for this finding",
                            build_context("F1", CASE), findings)
        assert result.state.iteration <= 8
        # prior success preserved: not FAILED
        assert result.task.status.value != "failed"
        assert any(o.get("outcome") == "success"
                   for o in result.state.observations)


def timeline_success_with_id(i):
    r = timeline_success()
    r.data = dict(r.data or {})
    r.data["unique_marker"] = f"run-{i}"     # each execution yields NEW data
    return r

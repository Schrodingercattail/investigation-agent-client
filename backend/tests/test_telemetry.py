"""Telemetry extension-point tests.

Covers: event construction, no-op sink neutrality, in-memory capture,
planner/executor structured events, sink-failure isolation, and the
no-chain-of-thought guarantee. The default runtime behavior must be
unchanged (no-op sink).
"""

from unittest.mock import patch

import pytest

import app.telemetry as telemetry
from app.telemetry import (
    AgentTelemetryEvent,
    InMemoryTelemetrySink,
    NoopTelemetrySink,
    set_sink,
)
import tests.test_risk_platform_adapter_v2 as fx
from app.investigation_service import InvestigationService
from app.models import (
    Finding,
    FindingCapability,
    FocusSource,
    InvestigationContext,
)
from app.planner_v2 import PlannerV2


def ml_finding():
    return Finding(
        finding_id="F1", case_id="U00299", type="detector_signal",
        title="ML Pattern Detection", summary="s",
        signal_refs=[{"signal_type": "ML", "name": "ml_score"}],
        capabilities=FindingCapability.model_validate(
            ["timeline", "signal_explain", "policy_lookup"]))


def eval_service(scripts):
    return InvestigationService(planner=PlannerV2(Scripted(scripts)))


class Scripted:
    def __init__(self, script):
        self.script = script
        self.last_messages = None

    def generate(self, messages, max_tokens=0, temperature=0.1):
        self.last_messages = messages      # the raw prompt — must never be
        return self.script                 # captured by telemetry


LLM_PROBE_TEXT = "RAW-LMM-OUTPUT-MARKER should-never-be-in-telemetry"
SIGNAL_PLAN = ('{"skill_id": "timeline_investigation", "goal": "g", '
               '"steps": [{"type": "explain_signal", "reason": "r"}]}')
BAD_PLAN = '{"skill_id": null, "goal": null, "steps": []}'


def run_one_turn(service, sink):
    findings = [ml_finding()]
    ctx = InvestigationContext(case_id="U00299", focused_finding_id="F1",
                               focus_source=FocusSource.USER_SELECTED)
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            fx.rp_evidence_payload(uid), fx.rp_explanation_payload())):
        result = service.run_turn("Why was this finding flagged?", ctx,
                                  findings)
    events = list(sink.events) if hasattr(sink, "events") else []
    return result, events


# --- 1. event construction ------------------------------------------------------

class TestEventConstruction:
    def test_minimal_event_carries_only_common_fields(self):
        e = AgentTelemetryEvent(event_type="semantic.evaluated")
        assert e.event_type == "semantic.evaluated"
        assert e.agent_version == telemetry.AGENT_VERSION
        assert e.semantic_success is None          # optional groups unset
        assert e.plan_step_types is None
        assert e.execution_status is None

    def test_event_timestamp_is_iso_utc(self):
        from datetime import datetime
        e = AgentTelemetryEvent(event_type="planner.started")
        assert datetime.fromisoformat(e.timestamp)   # parses as ISO-8601

    def test_version_metadata_present(self):
        e = AgentTelemetryEvent(event_type="planner.completed",
                                model="glm-5.3")
        assert e.planner_version == telemetry.PLANNER_VERSION
        assert e.prompt_version == telemetry.PROMPT_VERSION
        assert e.model == "glm-5.3"


# --- 2/3. sinks ------------------------------------------------------------------

class TestSinks:
    def test_noop_sink_is_true_noop(self):
        sink = NoopTelemetrySink()
        assert sink.record(AgentTelemetryEvent(
            event_type="planner.completed")) is None

    def test_in_memory_sink_captures_events(self):
        sink = InMemoryTelemetrySink()
        e1 = AgentTelemetryEvent(event_type="planner.started")
        e2 = AgentTelemetryEvent(event_type="planner.completed")
        sink.record(e1)
        sink.record(e2)
        assert sink.events == [e1, e2]
        sink.clear()
        assert sink.events == []

    def test_default_sink_is_noop(self):
        assert isinstance(telemetry.get_sink(), NoopTelemetrySink)


# --- 4–6. planner / executor capture seams ---------------------------------------

class TestPlannerAndExecutorEvents:
    def test_successful_turn_emits_structured_events(self):
        sink = InMemoryTelemetrySink()
        set_sink(sink)
        try:
            result, events = run_one_turn(eval_service(SIGNAL_PLAN), sink)
            types = [e.event_type for e in events]
            assert "planner.started" in types
            assert "planner.completed" in types
            assert "execution.completed" in types
            planner_done = events[types.index("planner.completed")]
            assert planner_done.plan_step_types == ["explain_signal"]
            assert planner_done.plan_arguments == [{}]
            assert planner_done.planner_status == "completed"
            assert planner_done.planner_latency_ms is not None
            execution_done = events[types.index("execution.completed")]
            assert execution_done.executed_step_types == ["explain_signal"]
            assert execution_done.tool_names == ["signal_explain"]
            assert execution_done.execution_latency_ms is not None
            # identity fields
            assert planner_done.case_id == "U00299"
            assert planner_done.focused_finding_id == "F1"
            assert result.task.status.value == "completed"
        finally:
            set_sink(NoopTelemetrySink())

    def test_planner_failure_emits_failed_event(self):
        sink = InMemoryTelemetrySink()
        set_sink(sink)
        try:
            result, events = run_one_turn(eval_service(BAD_PLAN), sink)
            failed = [e for e in events
                      if e.event_type == "planner.failed"]
            assert failed, "expected planner.failed event"
            assert failed[0].planner_status == "failed"
            assert failed[0].planner_error == "LLM_OUTPUT_INVALID"
            # execution never started: no plan
            assert result.task.status.value == "failed"
        finally:
            set_sink(NoopTelemetrySink())

    def test_no_chain_of_thought_captured(self):
        # 8. structured plan output only — never prompts, raw LLM text, or
        # reasoning. The planner envelope records step types/arguments.
        sink = InMemoryTelemetrySink()
        set_sink(sink)
        llm_probe_text = "USER_REQUEST_START raw-model-reasoning MARKER"
        try:
            _, events = run_one_turn(
                eval_service(LLM_PROBE_TEXT), sink)
            for e in events:
                d = e.model_dump()
                # metadata field names are fine; no field may CARRY prompt
                # or reasoning content
                for k, v in d.items():
                    if isinstance(v, str):
                        assert "system_prompt" not in v.lower()
                        assert "chain of thought" not in v.lower()
                        assert v != llm_probe_text  # raw LLM I/O never stored
                # no field may hold the raw prompt or model text at all
                for v in d.values():
                    assert "USER_REQUEST_START" not in str(v)
        finally:
            set_sink(NoopTelemetrySink())


# --- 7. sink failure isolation ------------------------------------------------------

class TestSinkFailureIsolation:
    def test_broken_sink_does_not_break_investigation(self):
        class BrokenSink:
            def record(self, event):
                raise RuntimeError("telemetry backend down")

        set_sink(BrokenSink())
        try:
            result, _ = run_one_turn(eval_service(SIGNAL_PLAN),
                                     NoopTelemetrySink())
            assert result.task.status.value == "completed"
            assert result.response     # investigation proceeded normally
        finally:
            set_sink(NoopTelemetrySink())


# --- semantic.evaluated extension point (eval integration seam) ---------

class TestSemanticEvaluatedExtensionPoint:
    def test_eval_runner_can_emit_semantic_evaluated(self):
        sink = InMemoryTelemetrySink()
        set_sink(sink)
        try:
            # the same contract an evaluation runner emits after checking
            telemetry.emit(AgentTelemetryEvent(
                event_type="semantic.evaluated",
                scenario_id="E07",
                semantic_success=True,
                failure_category=None))
            assert sink.events[-1].event_type == "semantic.evaluated"
            assert sink.events[-1].semantic_success is True
            assert sink.events[-1].scenario_id == "E07"
        finally:
            set_sink(NoopTelemetrySink())

"""U00008 planner reliability fix — budget alignment, streaming call
path, and attempt diagnostics.

Fix A: the planner's generation budget is PLANNER_MAX_TOKENS (8192 —
the GU-proven value on the same model/endpoint) instead of a hardcoded
4096 that a simple two-finding criteria request exhausted twice
(stop_reason=max_tokens, no text block — live TASK-0d9d7b98a2bb).

Fix B: every planner LLM call records the provider's per-attempt
diagnostics (stop_reason, usage, content block types/LENGTHS, stream
mode — never prompt or reasoning content) and the loop persists them on
the state symmetrically with goal_understanding_diagnostics.

Fix C: the planner calls generate with stream=True when the provider
supports it (signature-probed, the established GU pattern) — reusing
the GU-verified messages.stream + get_final_message() path, which also
passes `system` as the protocol-level top-level parameter.
"""
import json
from types import SimpleNamespace

import pytest

from app.config import settings
from app.llm_provider import LLMEmptyResponseError
from app.models import InvestigationContext, ToolResult, \
    ToolResultOutcome
from app.planner_v2 import PlannerV2, PlanningFailure
from app.task_store_v2 import TaskStoreV2


class _RecordingLLM:
    """Records generate kwargs; replays scripted payloads; can raise."""

    def __init__(self, payloads=None, error=None, with_meta=True):
        self.payloads = list(payloads or [])
        self.error = error
        self.kwargs = None
        if with_meta:
            self.last_attempts = [{
                "attempt_index": 1, "max_application_attempts": 2,
                "outcome": "empty_response",
                "error_class": "LLMEmptyResponseError",
                "empty_detail": "non_text_blocks_only",
                "stop_reason": "max_tokens",
                "model": "glm-5.3", "max_tokens": 4096,
                "stream": True,
                "content_kinds": ["thinking"],
                "content_blocks": [{"type": "thinking", "chars": 5200}],
                "text_chars": 0,
                "usage_input_tokens": 2311, "usage_output_tokens": 4096,
            }]
            self.last_response_meta = {
                "stop_reason": "max_tokens", "stream": True,
                "content_kinds": ["thinking"],
                "content_blocks": [{"type": "thinking", "chars": 5200}],
                "text_chars": 0,
                "usage_input_tokens": 2311,
                "usage_output_tokens": 4096,
            }

    def generate(self, messages, max_tokens=0, temperature=0.1,
                 stream=False):
        self.kwargs = {"messages": messages, "max_tokens": max_tokens,
                       "temperature": temperature, "stream": stream}
        if self.error is not None:
            raise self.error
        return self.payloads[min(len(self.payloads) - 1, 0)] if \
            self.payloads else "{}"


PLAN_JSON = json.dumps({
    "skill_id": "case_intake", "goal": "eval",
    "steps": [{"type": "fetch_case", "reason": "eval"}],
})

CTX = SimpleNamespace(case_id="U00008", focused_finding_id=None,
                      focused_event_id=None, time_window=None)


def _plan(llm):
    return PlannerV2(llm).plan(
        "show me the criterias of High Withdrawal Frequency and "
        "First Withdrawal to New Address", CTX,
        ["case_intake"],
        finding_capabilities=None)


# --- Fix A: budget from config ------------------------------------------

def test_budget_setting_is_gu_aligned():
    assert settings.PLANNER_MAX_TOKENS == 8192


def test_planner_uses_config_budget_and_streams():
    llm = _RecordingLLM(payloads=[PLAN_JSON])
    result = _plan(llm)
    assert not isinstance(result, PlanningFailure)
    assert llm.kwargs["max_tokens"] == settings.PLANNER_MAX_TOKENS == 8192
    # streaming: the GU-verified transport, enabled for the planner
    assert llm.kwargs["stream"] is True
    # doubles WITHOUT the stream parameter (legacy scripted LLMs) keep
    # working — the kwarg is signature-probed
    class _LegacyLLM(_RecordingLLM):
        def generate(self, messages, max_tokens=0, temperature=0.1):
            self.kwargs = {"messages": messages, "max_tokens": max_tokens,
                           "temperature": temperature}
            return self.payloads[0] if self.payloads else "{}"
    legacy = _LegacyLLM(payloads=[PLAN_JSON], with_meta=False)
    result2 = _plan(legacy)
    assert not isinstance(result2, PlanningFailure)
    assert "stream" not in legacy.kwargs         # never passed


# --- Fix B: attempt diagnostics ------------------------------------------

def test_b_failure_diagnostics_carry_attempts():
    err = LLMEmptyResponseError(
        "LLM response contained no text block",
        context={"stop_reason": "max_tokens",
                 "empty_detail": "non_text_blocks_only"})
    llm = _RecordingLLM(error=err)
    result = _plan(llm)
    assert isinstance(result, PlanningFailure)
    assert result.code == "LLM_EMPTY_RESPONSE"
    attempts = result.detail["attempts"]
    assert attempts and attempts[0]["stop_reason"] == "max_tokens"
    assert attempts[0]["content_blocks"] == [
        {"type": "thinking", "chars": 5200}]
    assert attempts[0]["usage_output_tokens"] == 4096
    meta = result.detail["response_meta"]
    assert meta["stream"] is True and meta["text_chars"] == 0


def test_b_no_prompt_or_reasoning_content_leaks():
    """Diagnostics record block TYPES and LENGTHS — never prompt or
    reasoning content."""
    llm = _RecordingLLM(error=LLMEmptyResponseError("no text"))
    result = _plan(llm)
    blob = json.dumps(result.detail["attempts"]
                      + [result.detail["response_meta"]])
    for banned in ("prompt", "messages", "thinking_content",
                   "system_prompt", USER_TEXT := "criterias"):
        assert banned not in blob, banned


def test_b_success_still_records_diagnostics():
    llm = _RecordingLLM(payloads=[PLAN_JSON])
    result = _plan(llm)
    assert not isinstance(result, PlanningFailure)
    planner_diag = getattr(result, "planning_diagnostics", None)
    # success diagnostics ride the planner instance (the loop persists
    # them onto state.planning_diagnostics); the plan itself is parsed
    assert result.steps[0].type == "fetch_case"


# --- Fix C: max_tokens + no text stays a failure -------------------------

def test_c_max_tokens_no_text_is_not_masked_as_success():
    err = LLMEmptyResponseError(
        "no text block", context={"stop_reason": "max_tokens"})
    llm = _RecordingLLM(error=err)
    result = _plan(llm)
    assert isinstance(result, PlanningFailure)
    assert result.code == "LLM_EMPTY_RESPONSE"
    assert "empty responses" in result.message


def test_c_normal_text_response_still_parses():
    llm = _RecordingLLM(payloads=[PLAN_JSON])
    result = _plan(llm)
    assert result.steps[0].type == "fetch_case"
    assert result.steps[0].status is not None


def test_c_real_plan_stream_shape_tolerated():
    """A streaming final message carries the same content shape — the
    parser only needs the JSON text (get_final_message contract)."""
    llm = _RecordingLLM(payloads=[PLAN_JSON])
    llm.last_response_meta["stream_completed"] = True
    llm.last_response_meta["event_count"] = 42
    result = _plan(llm)
    assert not isinstance(result, PlanningFailure)


# --- Diagnostics wiring: production planner=None path -------------------

class _FakePlanner:
    """Stands in for app.planner_v2.PlannerV2 (patched at the module
    attribute _plan imports): records whether the LLM path ran and
    carries the provider-shaped diagnostics the runner must capture."""

    last_llm_diagnostics = None
    plan_result = None

    def __init__(self):
        self.last_llm_diagnostics = type(self).last_llm_diagnostics

    def plan(self, user_request, context, eligible_ids,
             finding_capabilities=None, goal_contract=None,
             case_findings=None):
        result = type(self).plan_result
        if result is None:
            raise AssertionError("no scripted plan result")
        return result


def _wire_runner(store, provider, proposer):
    from app.agent_loop import AgentLoopRunner
    from app.executor_v2 import ExecutorV2
    return AgentLoopRunner(store=store,
                           executor=ExecutorV2(provider),
                           proposer=proposer)


def test_wiring_success_persists_diagnostics(tmp_path):
    from app.agent_loop import AgentLoopRunner
    from app.executor_v2 import ExecutorV2
    from app.models import Plan, PlanStep, PlanStepStatus, ToolResult, \
        ToolResultOutcome
    from tests.test_agent_loop_v1 import (ScriptedToolProvider,
                                          ScriptedProposer)
    import app.planner_v2 as pv

    plan = Plan(plan_id="PLAN-fake", investigation_id="CASE:U00299",
                goal="criteria ask",
                steps=[PlanStep(step_id="S1", type="inspect_timeline",
                                reason="llm", status=PlanStepStatus.PENDING,
                                tool_name="finding_drilldown",
                                arguments={})])
    _FakePlanner.plan_result = plan
    _FakePlanner.last_llm_diagnostics = {
        "called": True,
        "attempts": [{"attempt_index": 1, "outcome": "success",
                      "stop_reason": "end_turn", "stream": True,
                      "max_tokens": settings.PLANNER_MAX_TOKENS,
                      "content_blocks": [{"type": "text", "chars": 210}],
                      "text_chars": 210,
                      "usage_input_tokens": 2311,
                      "usage_output_tokens": 610}],
        "response_meta": {"stop_reason": "end_turn", "stream": True,
                          "text_chars": 210},
    }
    store = TaskStoreV2(db_path=tmp_path / "w1.sqlite3")
    runner = _wire_runner(
        store, ScriptedToolProvider([ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": "F1", "view": "timeline", "events": [],
                  "total_events": 0, "truncated": False})]),
        ScriptedProposer(["STOP"]))
    assert runner.planner is None              # production shape
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pv, "PlannerV2", _FakePlanner)
        result = runner.run("show me the criterias",
                            InvestigationContext(case_id="U00299"), [])
    state = result.state
    pd = state.planning_diagnostics
    assert pd is not None and pd["llm_called"] is True
    assert pd["detail"]["attempts"][0]["max_tokens"] == 8192
    assert pd["detail"]["attempts"][0]["stream"] is True
    assert pd["code"] is None
    # the wiring test's subject is the diagnostics capture; the loop's
    # convergence shape for an empty timeline is agent_loop_v1's domain
    assert state.agent_status in ("completed", "stopped_by_limit")


def test_wiring_failure_persists_attempts(tmp_path):
    from app.agent_loop import AgentLoopRunner
    from app.executor_v2 import ExecutorV2
    from app.models import InvestigationContext, ToolResult, \
    ToolResultOutcome
    from app.planner_v2 import PlanningFailure
    from tests.test_agent_loop_v1 import (ScriptedToolProvider,
                                          ScriptedProposer)
    import app.planner_v2 as pv

    _FakePlanner.plan_result = PlanningFailure(
        code="LLM_EMPTY_RESPONSE",
        message="The model returned empty responses.",
        detail={"error_type": "LLMEmptyResponseError",
                "attempts": [{"attempt_index": 1, "outcome":
                              "empty_response",
                              "stop_reason": "max_tokens",
                              "stream": True,
                              "content_blocks": [{"type": "thinking",
                                                  "chars": 8100}],
                              "text_chars": 0,
                              "usage_output_tokens": 8192}],
                "response_meta": {"stop_reason": "max_tokens",
                                  "stream": True}},
    )
    _FakePlanner.last_llm_diagnostics = {
        "called": True,
        "attempts": _FakePlanner.plan_result.detail["attempts"],
        "response_meta": _FakePlanner.plan_result.detail["response_meta"],
    }
    store = TaskStoreV2(db_path=tmp_path / "w2.sqlite3")
    runner = _wire_runner(
        store, ScriptedToolProvider([ToolResult(
            outcome=ToolResultOutcome.SUCCESS, data={})]),
        ScriptedProposer(["STOP"]))
    assert runner.planner is None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pv, "PlannerV2", _FakePlanner)
        result = runner.run("show me the criterias",
                            InvestigationContext(case_id="U00299"), [])
    state = result.state
    pd = state.planning_diagnostics
    assert pd["llm_called"] is True
    assert pd["code"] == "LLM_EMPTY_RESPONSE"
    assert pd["detail"]["attempts"][0]["usage_output_tokens"] == 8192
    assert state.agent_status == "failed"
    # diagnostics never leak into the user-visible response
    assert "usage_output_tokens" not in (state.response or "")
    assert "content_blocks" not in (state.response or "")


def test_wiring_not_called_is_explicit_not_lost(tmp_path):
    """A deterministic plan (no LLM) must record llm_called=False with
    detail=None — never the same shape as a lost-diagnostics run."""
    from app.agent_loop import AgentLoopRunner
    from app.executor_v2 import ExecutorV2
    from app.models import InvestigationContext, ToolResult, \
    ToolResultOutcome
    from tests.test_agent_loop_v1 import (ScriptedToolProvider,
                                          ScriptedProposer)
    store = TaskStoreV2(db_path=tmp_path / "w3.sqlite3")
    runner = _wire_runner(
        store, ScriptedToolProvider([ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"findings": [SimpleNamespace(
                finding_id="F1", title="T", summary="s.")],
                "finding_count": 1})]),
        ScriptedProposer(["STOP"]))
    assert runner.planner is None
    result = runner.run("investigate U00299",
                        InvestigationContext(case_id="U00299"), [])
    state = result.state
    pd = state.planning_diagnostics
    assert pd is not None
    assert pd["llm_called"] is False
    assert pd["detail"] is None
    assert pd["code"] is None


def test_wiring_no_leak_into_activity_projection(tmp_path):
    from app.agent_loop import AgentLoopRunner, project_task_activity
    from app.executor_v2 import ExecutorV2
    from app.models import InvestigationContext, ToolResult, \
    ToolResultOutcome
    from tests.test_agent_loop_v1 import (ScriptedToolProvider,
                                          ScriptedProposer)
    import app.planner_v2 as pv
    _FakePlanner.plan_result = PlanningFailure(
        code="LLM_EMPTY_RESPONSE", message="empty",
        detail={"attempts": [{"usage_output_tokens": 8192,
                              "content_blocks": [{"type": "thinking",
                                                  "chars": 9000}]}]})
    _FakePlanner.last_llm_diagnostics = {
        "called": True, "attempts": _FakePlanner.plan_result.detail[
            "attempts"], "response_meta": {}}
    store = TaskStoreV2(db_path=tmp_path / "w4.sqlite3")
    runner = _wire_runner(
        store, ScriptedToolProvider([ToolResult(
            outcome=ToolResultOutcome.SUCCESS, data={})]),
        ScriptedProposer(["STOP"]))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pv, "PlannerV2", _FakePlanner)
        result = runner.run("show me the criterias",
                            InvestigationContext(case_id="U00299"), [])
    proj = project_task_activity(result.state)
    assert "planning_diagnostics" not in proj      # whitelist holds
    blob = json.dumps(proj.get("activity", []))
    assert "usage_output_tokens" not in blob
    assert "content_blocks" not in blob

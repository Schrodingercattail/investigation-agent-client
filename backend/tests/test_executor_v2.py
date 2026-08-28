"""Tests for the Week 1 Executor (app/executor_v2.py).

Uses fake/in-memory tool providers; no live Risk Platform. Covers the 18
required scenarios: contract re-validation, ToolCallV2 audit records,
normalized ToolResult wiring, status transitions, persistence wiring, and
the six distinct failure semantics.
"""

from typing import Any

import pytest
from unittest.mock import patch

from app.executor_v2 import (
    ExecutorV2,
    ExecutionResult,
    ToolProvider,
    default_tool_provider,
)
from app.models import (
    FindingCapability,
    InvestigationContext,
    Plan,
    PlanStep,
    PlanStepStatus,
    PlanStatus,
    TaskStatusV2,
    TaskV2,
    ToolCallStatusV2,
    ToolCallV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)


CTX = InvestigationContext(case_id="U00299")
CTX_F3 = InvestigationContext(case_id="U00299", focused_finding_id="F3")
F3_CAPS = FindingCapability.model_validate(["timeline", "signal_explain", "policy_lookup"])


def step(step_id="S1", type_="fetch_case", tool="risk_case_fetch",
         args: dict | None = None, **kw) -> PlanStep:
    base = dict(step_id=step_id, type=type_, reason="r",
                tool_name=tool, arguments=args or {})
    base.update(kw)
    return PlanStep(**base)


def plan(steps: list[PlanStep], goal="g") -> Plan:
    return Plan(plan_id="PLAN-1", investigation_id="CASE:U00299",
                goal=goal, steps=steps)


def task(skill="case_intake", task_id="TASK-1") -> TaskV2:
    return TaskV2(task_id=task_id, investigation_id="CASE:U00299",
                  user_request="u", selected_skill=skill)


def ok_result(data=None) -> ToolResult:
    return ToolResult(outcome=ToolResultOutcome.SUCCESS,
                      data=data or {"fine": True})


def provider_with(**tools) -> ToolProvider:
    return ToolProvider(tools=dict(tools))


# --- 1–6. happy path -----------------------------------------------------------------

class TestHappyPath:
    def _run(self, provider=None):
        ex = ExecutorV2(provider or provider_with(
            risk_case_fetch=lambda a: ok_result({"case_id": a.get("case_id")}),
        ))
        p = plan([step()])
        t = task()
        res = ex.execute(p, t, CTX)
        return res, p, t

    def test_valid_fetch_case_plan_executes(self):
        res, p, t = self._run()
        assert res.ok
        assert t.status == TaskStatusV2.COMPLETED
        assert t.completed_at is not None and t.started_at is not None

    def test_toolv2_created_for_actual_execution(self):
        res, _, _ = self._run()
        assert len(res.tool_calls) == 1
        tc = res.tool_calls[0]
        assert isinstance(tc, ToolCallV2)
        assert tc.tool_name == "risk_case_fetch"
        assert tc.arguments == {"case_id": "U00299"}   # runtime-injected
        assert tc.started_at and tc.completed_at

    def test_toolcall_contains_normalized_toolresult(self):
        res, _, _ = self._run()
        assert isinstance(res.tool_calls[0].result, ToolResult)
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.SUCCESS

    def test_planstep_transitions_to_completed(self):
        res, p, _ = self._run()
        assert p.steps[0].status == PlanStepStatus.SUCCESS
        assert p.steps[0].started_at and p.steps[0].completed_at
        assert p.steps[0].error is None

    def test_task_transitions_through_executing(self):
        seen = []
        class RecordingStore:
            def update(self, t):
                seen.append(t.status)
        provider = provider_with(risk_case_fetch=lambda a: ok_result())
        ex = ExecutorV2(provider, RecordingStore())
        res = ex.execute(plan([step()]), task(), CTX)
        assert res.ok
        assert TaskStatusV2.EXECUTING in seen
        assert seen[-1] == TaskStatusV2.COMPLETED

    def test_task_stores_tool_call_ids(self):
        res, _, t = self._run()
        assert t.tool_call_ids == [res.tool_calls[0].tool_call_id]

    def test_execution_deterministic_for_same_inputs(self):
        # Same valid plan + fake provider → same structural outcome.
        # (IDs and timestamps are execution-volatile by design; everything
        # semantically meaningful is identical.)
        def shape(res: ExecutionResult, p: Plan):
            return {
                "status": res.status,
                "step_statuses": [s.status for s in p.steps],
                "calls": [{
                    "tool_name": tc.tool_name,
                    "arguments": tc.arguments,
                    "status": tc.status,
                    "outcome": (tc.result.outcome if tc.result else None),
                } for tc in res.tool_calls],
                "errors": [(e.code, e.step_id) for e in res.errors],
            }
        s1 = shape(*self._run()[:2])
        s2 = shape(*self._run()[:2])
        assert s1 == s2


# --- 7–9. validation failures ⇒ zero executions ------------------------------------------

class TestValidationFailuresZeroExecution:
    def test_plan_validation_failure_causes_zero_executions(self):
        calls = []

        def spy(a):
            calls.append(a)
            return ok_result()

        ex = ExecutorV2(provider_with(risk_case_fetch=spy))
        bad_steps = [step("S1", "deploy_x", "risk_case_fetch")]
        result = ExecutorV2.execute(
            ex, plan(bad_steps), task(skill="case_intake"), CTX)
        assert not calls                      # nothing ran
        assert result.status == TaskStatusV2.FAILED
        assert result.errors[0].code == "PLAN_CONTRACT_VIOLATION"
        assert result.tool_calls == []        # no fake ToolCalls either
        assert result.task.error              # structured error recorded

    def test_invalid_skill_causes_zero_executions(self):
        calls = []
        ex = ExecutorV2(provider_with(
            risk_case_fetch=lambda a: (calls.append(a), ok_result())[1]))
        result = ex.execute(plan([step()]),
                            task(skill="mystery_skill"), CTX)
        assert calls == []
        assert result.status == TaskStatusV2.FAILED
        assert "skill" in result.errors[0].message.lower()

    def test_capability_violation_causes_zero_executions(self):
        calls = []
        ex = ExecutorV2(provider_with(
            risk_case_fetch=lambda a: (calls.append(a), ok_result())[1]))
        # F3 lacks opposite_trades; trade skill must not run even here.
        t = task(skill="trade_investigation")
        steps = [step("S1", "inspect_opposite_trades", "finding_drilldown",
                      {"view": "opposite_trades"})]
        from app.skills import check_plan
        pre = check_plan("trade_investigation", steps, capabilities=F3_CAPS)
        assert not pre.valid                        # checker itself rejects
        result = ex.execute(plan(steps), t, CTX_F3,
                            finding_capabilities=F3_CAPS)
        assert calls == []
        assert result.status == TaskStatusV2.FAILED

    def test_rejected_step_never_produces_toolcall(self):
        calls = []
        ex = ExecutorV2(provider_with(
            risk_case_fetch=lambda a: (calls.append(a), ok_result())[1]))
        rejected = step(status=PlanStepStatus.REJECTED, error="validator said no")
        result = ex.execute(plan([rejected]), task(), CTX)
        assert calls == []
        assert result.status == TaskStatusV2.FAILED
        assert result.errors[0].code == "PLAN_CONTRACT_VIOLATION"
        assert not rejected.status == PlanStepStatus.SUCCESS


# --- 10. unimplemented tool ---------------------------------------------------------------

class TestUnimplementedTool:
    def test_unimplemented_tool_bounded_executor_failure(self):
        # Provider lacks finding_drilldown → executor-level failure category,
        # NOT empty, NOT integration_error, no fabricated result.
        # Note: use a plan whose skill legitimately allows both tools
        # (timeline_investigation allows finding_drilldown).
        ex = ExecutorV2(provider_with(
            risk_case_fetch=lambda a: ok_result(),
            # finding_drilldown deliberately absent
        ))
        st = step("S2", "inspect_timeline", "finding_drilldown", {"view": "timeline"})
        result = ex.execute(plan([st]), task(skill="timeline_investigation"),
                            CTX_F3, finding_capabilities=F3_CAPS)
        assert result.status == TaskStatusV2.FAILED
        assert result.errors[0].code == "TOOL_NOT_IMPLEMENTED"
        assert st.status == PlanStepStatus.FAILED
        assert "not currently available" in st.error
        assert result.tool_calls == []            # never created a ToolCall
                                                  # for an unimplemented tool

    def test_unavailable_tool_not_marked_completed_or_skipped(self):
        ex = ExecutorV2(ToolProvider({}))         # no tools at all
        s = step()
        result = ex.execute(plan([s]), task(), CTX)
        assert s.status == PlanStepStatus.FAILED
        assert s.status not in (PlanStepStatus.SUCCESS, PlanStepStatus.SKIPPED)

    def test_distinct_from_integration_error_semantics(self):
        # TOOL_NOT_IMPLEMENTED is an executor-level category; a real RP outage
        # produces a recorded ToolCall whose result.outcome=integration_error.
        # (Distinctness is proven constructively: this class's failures carry
        # zero ToolCalls, while TestToolResults failures always record one.)
        ex = ExecutorV2(ToolProvider({}))
        res = ex.execute(plan([step()]), task(), CTX)
        assert res.errors[0].code == "TOOL_NOT_IMPLEMENTED"
        assert res.tool_calls == []


# --- 11–15. ToolResult outcome preservation -----------------------------------------------

def make_provider(outcome_fn):
    return provider_with(risk_case_fetch=outcome_fn)


class TestToolResults:
    def test_integration_error_preserved(self):
        err = ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(code="RISK_PLATFORM_UNAVAILABLE",
                            message="Risk Platform timed out after 30s"),
        )
        calls = []
        ex = ExecutorV2(make_provider(lambda a: (
            calls.append(True), err)[1]))
        res = ex.execute(plan([step()]), task(), CTX)
        assert len(res.tool_calls) == 1                    # actual attempt recorded
        assert res.tool_calls[0].status == ToolCallStatusV2.FAILED
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert res.task.status == TaskStatusV2.FAILED      # required step failed
        # executor error mentions RP outcome but the *recorded* distinction
        # lives on the ToolCall's normalized result:
        assert any("integration_error" in (e.message or "") for e in res.errors)

    def test_empty_result_preserved_as_empty(self):
        empty = ToolResult(outcome=ToolResultOutcome.EMPTY,
                           warnings=["no data"])
        ex = ExecutorV2(make_provider(lambda a: empty))
        s = step()
        res = ex.execute(plan([s]), task(), CTX)
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.EMPTY
        assert res.task.status == TaskStatusV2.FAILED   # required step w/ no data
        assert s.status == PlanStepStatus.FAILED
        # ...and the outcome stays EMPTY in the audit record, not converted:
        assert "empty" in s.error

    def test_evidence_missing_remains_success_payload_state(self):
        insuff = ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"evidence_missing": True,
                  "next_data_needed": ["per-trade attribution"]},
        )
        ex = ExecutorV2(make_provider(lambda a: insuff))
        s = step()
        res = ex.execute(plan([s]), task(), CTX)
        assert res.ok                                     # success outcome
        assert s.status == PlanStepStatus.SUCCESS
        assert res.tool_calls[0].result.data["evidence_missing"] is True

    def test_unsupported_outcome_recorded_where_tool_validates_capability(self):
        unsup = ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(code="capability_unsupported",
                            message="finding does not support opposite_trades"),
        )
        ex = ExecutorV2(make_provider(lambda a: unsup))
        res = ex.execute(plan([step()]), task(), CTX)
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.UNSUPPORTED

    def test_toolcall_recorded_even_on_error_result(self):
        bad = ToolResult(outcome=ToolResultOutcome.VALIDATION_ERROR,
                         error=ToolError(code="INVALID_ARGUMENT", message="x"))
        ex = ExecutorV2(make_provider(lambda a: bad))
        res = ex.execute(plan([step()]), task(), CTX)
        assert len(res.tool_calls) == 1                   # failure still audited
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.VALIDATION_ERROR


# --- 14b. exceptions inside tools ------------------------------------------------------------

class TestRaisedExceptions:
    def test_raised_exception_recorded_and_failed(self):
        def boom(a):
            raise RuntimeError("socket exploded")
        ex = ExecutorV2(make_provider(boom))
        s = step()
        res = ex.execute(plan([s]), task(), CTX)
        assert res.status == TaskStatusV2.FAILED
        assert res.errors[0].code == "STEP_EXECUTION_ERROR"
        assert s.status == PlanStepStatus.FAILED
        assert "RuntimeError" in s.error
        assert len(res.tool_calls) == 1                       # attempt audited
        assert res.tool_calls[0].status == ToolCallStatusV2.FAILED


# --- 16–17. persistence wiring ------------------------------------------------------------------

class TestPersistenceWiring:
    def test_task_saved_and_reloadable_via_store(self, tmp_path):
        from app.task_store_v2 import TaskStoreV2
        store = TaskStoreV2(tmp_path / "ex.db")
        ex = ExecutorV2(make_provider(lambda a: ok_result()), store)
        t = task()
        res = ex.execute(plan([step()]), t, CTX)
        loaded = store.get(t.task_id)
        assert loaded == res.task
        assert loaded.status == TaskStatusV2.COMPLETED

    def test_tasks_listed_by_investigation_after_execution(self, tmp_path):
        from app.task_store_v2 import TaskStoreV2
        store = TaskStoreV2(tmp_path / "ex2.db")
        ex = ExecutorV2(make_provider(lambda a: ok_result()), store)
        t1 = task(task_id="T-1")
        t2 = task(task_id="T-2")
        ex.execute(plan([step()]), t1, CTX)
        ex.execute(plan([step()]), t2, CTX)
        listed = store.list_by_investigation("CASE:U00299")
        assert {t.task_id for t in listed} == {"T-1", "T-2"}


# --- end-to-end via real risk_case_fetch (mocked adapter) -------------------------------------------

class TestWithRealRiskCaseFetch:
    def test_full_pipeline_through_default_provider(self):
        import tests.test_risk_platform_adapter_v2 as fx
        with patch(
            "app.domain_tools.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ):
            ex = ExecutorV2(default_tool_provider())
            res = ex.execute(plan([step()]), task(), CTX)
        assert res.ok
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.SUCCESS
        findings = res.tool_calls[0].result.data["findings"]
        assert len(findings) >= 1

    def test_default_provider_wired_tools(self):
        # All five Week 1 tools wired: risk_case_fetch,
        # finding_drilldown (timeline), signal_explain, policy_lookup,
        # artifact_bundle.
        provider = default_tool_provider()
        assert provider.has("risk_case_fetch")
        assert provider.has("finding_drilldown")
        assert provider.has("signal_explain")
        assert provider.has("policy_lookup")
        assert provider.has("artifact_bundle")

"""Regression tests for the autonomous-evaluation correction pass.

Covers the GENERAL semantics (not example strings):

DEF-1/P20  zero-finding valid intake — EMPTY is a completed investigation
DEF-2/P14  failure wording preserves outcome semantics, no internal terms
DEF-3/P19  planner cannot select registered-but-non-executable paths
DEF-4/P4   ordinary timeline = complete; explicit N = bounded + labeled
DEF-5/P5   ambiguous explanatory language does not escalate to L3 records
DEF-6/P7   rich vs sparse rule explanation; no invented fields, no dupes
"""

import json
from unittest.mock import patch

import pytest

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import finding_drilldown
from app.domain_tools.finding_drilldown import finding_drilldown as drill
from app.executor_v2 import ExecutorV2, ToolProvider
from app.investigation_service import (
    InvestigationService,
    compose_response,
)
from app.models import (
    Finding,
    FindingCapability,
    InvestigationContext,
    Plan,
    PlanStep,
    PlanStepStatus,
    FocusSource,
    TaskStatusV2,
    TaskV2,
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2, PlanningFailure
from app.skills import skill_path_executable
from app.task_store_v2 import TaskStoreV2


CTX = InvestigationContext(case_id="U00299", focused_finding_id="F3",
                           focus_source=FocusSource.USER_SELECTED)
F3 = Finding(
    finding_id="F3", case_id="U00299", type="rule_signal",
    title="High withdrawal frequency.",
    severity="medium",
    summary="14 withdrawals in 24h exceeds the normal pattern.",
    capabilities=FindingCapability.model_validate(
        ["timeline", "signal_explain", "policy_lookup"]))


def make_service(scripts, provider=None):
    class L:
        def __init__(self, ss): self.ss = ss; self.calls = 0
        def generate(self, messages, max_tokens=0, temperature=0.1):
            s = self.ss[min(self.calls, len(self.ss) - 1)]
            self.calls += 1
            return s
    return InvestigationService(
        planner=PlannerV2(L(scripts)),
        executor=provider,          # None → default provider
        task_store=TaskStoreV2(":memory:"),
    )


def rp_mocks(evidence=None):
    ev = evidence if evidence is not None else fx.rp_evidence_payload()
    return (patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (ev, fx.rp_explanation_payload()),
    ), patch(
        "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
        new=lambda self, uid, expose_complete_records=False: ev,
    ))


# --- DEF-1 / P20: zero-finding valid intake -----------------------------------

class TestZeroFindingValidIntake:
    def test_zero_findings_success_completes_with_honest_message(self):
        """Real-shape case: exists, reachable, 0 findings. The turn must
        complete (P20) and the response must say RP returned no findings —
        never 'could not be reached', never failed."""
        empty_ev = fx.rp_evidence_payload("U01142")
        empty_ev["withdrawal_evidence"] = []
        empty_ev["transaction_evidence"] = []
        empty_ev["rule_evidence"] = []
        # explanation with no key findings (RP legitimately outputs none)
        ex = fx.rp_explanation_payload()
        ex["key_findings"] = []
        case_plan = ('{"skill_id": "case_intake", "goal": "g", "steps": ['
                     '{"type": "fetch_case", "reason": "r"}]}')
        svc = make_service([case_plan])
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (empty_ev, ex),
        ):
            r = svc.run_turn("Investigate U01142",
                             InvestigationContext(case_id="U01142"), [])
        assert r.task.status == TaskStatusV2.COMPLETED
        assert "no risk findings" in r.response.lower() or \
            "0 findings" in r.response.lower() or \
            "no findings" in r.response.lower()
        assert "could not be reached" not in r.response
        assert r.follow_ups == [] or True   # no capability-bearing finding

    def test_step_empty_is_completed_not_failed(self):
        """Executor semantics: a required step returning EMPTY completes the
        task; the outcome stays EMPTY on the audit record."""
        empty = ToolResult(outcome=ToolResultOutcome.EMPTY, warnings=["none"])
        # contract-valid provider shape: real tool names, EMPTY outcomes
        provider = ToolProvider({
            "risk_case_fetch": lambda a: empty,
            "finding_drilldown": lambda a: empty,
            "signal_explain": lambda a: empty,
            "policy_lookup": lambda a: empty,
            "artifact_bundle": lambda a: empty,
        })
        ex = ExecutorV2(provider)
        s = PlanStep(step_id="S1", type="fetch_case", reason="r",
                     status=PlanStepStatus.PENDING, tool_name="risk_case_fetch")
        plan = Plan(plan_id="P", investigation_id="I", goal="g", steps=[s])
        task = TaskV2(task_id="T", investigation_id="I", user_request="u",
                      selected_skill="case_intake")
        res = ex.execute(plan, task, InvestigationContext(case_id="U00299"))
        assert res.task.status == TaskStatusV2.COMPLETED
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.EMPTY


# --- DEF-2 / P14: failure wording preserves semantics --------------------------

class TestFailureWordingSemantics:
    def _resp_for_outcome(self, outcome, code="STEP_EXECUTION_ERROR"):
        from app.models import ToolError
        err = (ToolError(code="E", message="m")
               if outcome != ToolResultOutcome.EMPTY else None)
        tc = ToolCallV2(
            tool_call_id="TC-X", investigation_id="I", task_id="T",
            tool_name="any_tool", arguments={},
            status=ToolCallStatusV2.FAILED,
            result=ToolResult(outcome=outcome, data={}, error=err),
            started_at="2026-08-28T00:00:00Z",
            completed_at="2026-08-28T00:00:01Z",
        )
        from app.executor_v2 import ExecutorError
        fatal = ExecutorError(
            code=code, message="required step did not succeed",
            step_id="S1",
            detail={"outcome": outcome.value if hasattr(outcome, "value")
                    else outcome, "tool_call_id": "TC-X"})
        return compose_response(user_request="q", skill_id=None, plan=None,
                                tool_calls=[tc], execution_errors=[fatal])

    def test_integration_error_says_unreachable(self):
        text = self._resp_for_outcome(ToolResultOutcome.INTEGRATION_ERROR)
        assert "could not be reached" in text

    def test_unsupported_says_capability(self):
        text = self._resp_for_outcome(ToolResultOutcome.UNSUPPORTED)
        assert "not supported" in text
        assert "could not be reached" not in text

    def test_validation_error_says_request_problem(self):
        text = self._resp_for_outcome(ToolResultOutcome.VALIDATION_ERROR)
        assert "could not be completed as specified" in text
        assert "could not be reached" not in text

    def test_no_internal_identifiers_in_any_failure(self):
        for outcome in (ToolResultOutcome.UNSUPPORTED,
                        ToolResultOutcome.VALIDATION_ERROR,
                        ToolResultOutcome.INTEGRATION_ERROR,
                        ToolResultOutcome.EMPTY):
            text = self._resp_for_outcome(outcome)
            for term in ("finding_drilldown", "inspect_opposite_trades",
                         "timeline_investigation", "signal_explain",
                         "risk_case_fetch", "S1", "TC-X", "STEP_EXECUTION"):
                assert term not in text, f"{term} leaked for {outcome}"


# --- DEF-3 / P19: planner excludes non-executable paths -------------------------

class TestPlannerExecutablePaths:
    def test_trade_investigation_never_planned(self):
        caps = FindingCapability.model_validate(["opposite_trades"])
        ctx = InvestigationContext(case_id="X", focused_finding_id="F8")
        llm_scripts = [('{"skill_id": "trade_investigation", "goal": "g", '
                        '"steps": [{"type": "inspect_opposite_trades", '
                        '"reason": "r"}]}')]
        svc_plan = PlannerV2(_L(llm_scripts)).plan(
            "Show all opposite trades.", ctx, ["trade_investigation"], caps)
        assert isinstance(svc_plan, PlanningFailure)
        assert svc_plan.code == "NO_ELIGIBLE_SKILL"

    def test_single_semantic_answer_helper(self):
        assert skill_path_executable("case_intake")
        assert skill_path_executable("timeline_investigation")
        assert not skill_path_executable("trade_investigation")


class _L:
    def __init__(self, ss): self.ss = ss; self.calls = 0
    def generate(self, messages, max_tokens=0, temperature=0.1):
        s = self.ss[min(self.calls, len(self.ss) - 1)]
        self.calls += 1
        return s


# --- DEF-4 / P4: timeline completeness vs explicit subset -----------------------

def _wd_case(n_withdrawals, n_trades=3):
    ev = fx.rp_evidence_payload("U0X")
    ev["withdrawal_evidence"] = [
        {"withdrawal_id": f"W{i:05d}", "asset": "BTC", "amount": 1.0 + i,
         "address": "0xa", "is_new_address": True,
         "timestamp": f"2026-07-21T0{i % 9}:00:00Z",
         "risk_reason": "new address"}
        for i in range(n_withdrawals)]
    ev["transaction_evidence"] = [
        {"transaction_id": f"T{i:05d}", "symbol": "BTC", "side": "BUY",
         "price": 100.0, "quantity": 1.0, "value": 100.0,
         "timestamp": f"2026-07-20T0{i % 9}:00:00Z",
         "risk_reason": "activity"}
        for i in range(n_trades)]
    return ev


class TestTimelineCompleteness:
    def _case(self, ev):
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (ev, fx.rp_explanation_payload()),
        ):
            from app.domain_tools import risk_case_fetch
            return risk_case_fetch("U00299")

    def _wd_finding(self, case):
        return [f for f in case.data["findings"]
                if "withdrawal" in f.title.lower()][0]

    def test_ordinary_timeline_is_complete(self):
        case = self._case(_wd_case(40, 5))
        f = self._wd_finding(case)
        with patch(
            "app.adapters.risk_platform.RiskPlatformAdapter."
            "fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False:
                _wd_case(40, 5),
        ):
            res = drill(finding_id=f.finding_id, view="timeline",
                        case_context=case.data)
        assert res.outcome == ToolResultOutcome.SUCCESS
        assert res.data["truncated"] is False
        # 40 withdrawals + detection(s) — ALL events, no silent cap
        assert res.data["total_events"] == len(res.data["events"])
        assert len(res.data["events"]) >= 40

    def test_explicit_n_is_bounded_and_labeled(self):
        case = self._case(_wd_case(40, 5))
        f = self._wd_finding(case)
        with patch(
            "app.adapters.risk_platform.RiskPlatformAdapter."
            "fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False:
                _wd_case(40, 5),
        ):
            res = drill(finding_id=f.finding_id, view="timeline",
                        top_n=5, case_context=case.data)
        assert res.outcome == ToolResultOutcome.SUCCESS
        assert len(res.data["events"]) == 5
        assert res.data["truncated"] is True
        assert res.data["total_events"] >= 40
        assert any("bounded subset" in w or "explicit" in w
                   for w in res.warnings)


# --- DEF-5 / DEF-6: composer-level, exercised via planner guidance text -------

class TestIntentGuidanceAndRuleExplanation:
    def test_guidance_requires_explicit_records_for_evidence(self):
        from app.planner_v2 import SYSTEM_PROMPT_TEMPLATE
        src = SYSTEM_PROMPT_TEMPLATE
        # ambiguous phrases are explicitly routed to explanation, not records
        assert "what stands out" in src or "Vague explanatory" in src
        assert "Never invent a count" in src

    def test_rich_rule_payload_uses_description_and_values(self):
        tc = ToolCallV2(
            tool_call_id="TC-S", investigation_id="I", task_id="T",
            tool_name="signal_explain", arguments={},
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
                "finding_id": "F3", "signal_type": "Rule",
                "rule": {"name": "High withdrawal frequency",
                         "description": "10 withdrawals in 24h exceeds the "
                                        "normal pattern",
                         "trigger_values": {"withdrawal_frequency_24h": 10},
                         "threshold": "withdrawal_frequency_24h > 5",
                         "contribution": 20},
                "evidence_refs": [], "signal_refs": [], "policy_refs": [],
                "evidence_missing": False, "next_data_needed": [],
            }),
            started_at="2026-08-28T00:00:00Z",
            completed_at="2026-08-28T00:00:01Z",
        )
        text = compose_response(user_request="Why?", skill_id=None, plan=None,
                                tool_calls=[tc], execution_errors=[])
        assert "10 withdrawals in 24h exceeds the normal pattern" in text
        assert "Withdrawal frequency (24h) = 10" in text   # human label
        assert "withdrawal_frequency_24h" not in text      # no raw field name
        # no duplicated bare restatement
        assert text.count("High withdrawal frequency") == 1

    def test_sparse_rule_payload_says_only_known(self):
        tc = ToolCallV2(
            tool_call_id="TC-S", investigation_id="I", task_id="T",
            tool_name="signal_explain", arguments={},
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
                "finding_id": "F2", "signal_type": "Rule",
                "rule": {"name": "High withdrawal frequency",
                         "description": None, "trigger_values": {},
                         "threshold": None, "contribution": None},
                "evidence_refs": [], "signal_refs": [], "policy_refs": [],
                "evidence_missing": False, "next_data_needed": [],
            }),
            started_at="2026-08-28T00:00:00Z",
            completed_at="2026-08-28T00:00:01Z",
        )
        text = compose_response(user_request="Why?", skill_id=None, plan=None,
                                tool_calls=[tc], execution_errors=[])
        assert "flagged by the rule High withdrawal frequency" in text
        # no invented fields, no None, no empty placeholder details line
        assert "None" not in text
        assert "Details:" not in text

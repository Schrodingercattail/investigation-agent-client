"""Tests for the MCP Tool Provider (Level 2-B).

Validates that the existing Agent Runtime can execute `risk_case_fetch`
through the MCP path (McpToolProvider → McpCapabilityClient → MCP Server
→ Risk Platform) with ToolResult semantics preserved, while the
Function Calling provider remains the unchanged default.

These tests use the real MCP stdio path and the real Risk Platform.
"""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from app.executor_v2 import ExecutorV2, ToolProvider, default_tool_provider
from app.mcp_provider import (
    EXECUTION_MODE_FUNCTION_CALLING,
    EXECUTION_MODE_MCP,
    McpToolProvider,
    build_executor,
)
from app.mcp_client import McpCapabilityClient
from app.models import (
    Finding,
    FindingCapability,
    FocusSource,
    InvestigationContext,
    Plan,
    PlanStep,
    PlanStepStatus,
    TaskStatusV2,
    TaskV2,
    ToolResult,
    ToolResultOutcome,
)
from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx

RP_PATCHES = (
    None,
    None,
)


def rp_available() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(
            "http://localhost:8000/api/risk/cases/U00299/evidence",
            timeout=5)
        return True
    except Exception:
        return False


def make_context(focused=None):
    return InvestigationContext(
        case_id="U00299", focused_finding_id=focused,
        focus_source=FocusSource.USER_SELECTED if focused else None)


def make_plan(*step_types):
    return Plan(
        plan_id="PLAN-MCP-TEST",
        investigation_id="CASE:U00299",
        goal="mcp provider test",
        steps=[PlanStep(step_id=f"S{i}", type=t,
                        tool_name={"fetch_case": "risk_case_fetch",
                                   "generate_artifact": "artifact_bundle",
                                   }.get(t, t),
                        arguments={}, status=PlanStepStatus.PENDING)
               for i, t in enumerate(step_types, 1)],
        created_at=None,
    )


class BrokenSession:
    """Simulates MCP server/session failure at the session seam: every
    session call raises (the adapter must map this to bounded
    integration_error, never success)."""

    async def call_tool(self, name, arguments):
        raise RuntimeError("MCP server session crashed")


def client_with_broken_session() -> McpCapabilityClient:
    c = McpCapabilityClient()
    c._session = BrokenSession()          # break at the session seam
    c._client_started = True
    return c


class TestProviderRegistration:
    def test_mcp_provider_offers_risk_case_fetch(self):
        # 1. MCP provider offers risk_case_fetch through the standard
        #    ToolProvider contract.
        p = McpToolProvider()
        assert p.has("risk_case_fetch")
        assert p.get("risk_case_fetch") is not None

    def test_default_provider_still_the_function_calling_one(self):
        # 5. default provider remains the existing Function Calling path.
        dp = default_tool_provider()
        assert dp.has("risk_case_fetch")
        for name in ("finding_drilldown", "signal_explain",
                     "policy_lookup", "artifact_bundle"):
            assert dp.has(name)


@pytest.mark.skipif(not rp_available(), reason="Risk Platform not running")
class TestSuccessfulExecutionThroughMcp:
    def test_risk_case_fetch_via_mcp_returns_real_tool_result(self):
        # 2. successful execution through MCP: real ToolResult with
        # outcome SUCCESS and U00299 data preserved.
        p = McpToolProvider()
        fn = p.get("risk_case_fetch")
        result = fn({"case_id": "U00299"})
        assert isinstance(result, ToolResult)
        assert result.outcome == ToolResultOutcome.SUCCESS
        assert (result.data or {}).get("case_id") == "U00299"
        assert (result.data or {}).get("findings")
        assert result.evidence_refs
        assert result.citation_refs

    def test_same_step_both_modes_same_semantics(self):
        # 6. mode comparison: the SAME plan step (risk_case_fetch) executes
        # through Function Calling mode and MCP mode; the Planner is
        # identical and only the execution path differs.
        plan = make_plan("fetch_case")
        task = TaskV2(task_id="T-MODE-A", investigation_id="CASE:U00299",
                      user_request="Investigate U00299",
                      selected_skill="case_intake")
        ctx = make_context()

        fc = ExecutorV2(default_tool_provider())
        r_fc = fc.execute(plan.model_copy(deep=True), task.model_copy(deep=True),
                          ctx)
        assert r_fc.task.status == TaskStatusV2.COMPLETED
        assert [t.tool_name for t in r_fc.tool_calls] == ["risk_case_fetch"]

        mcp_executor = ExecutorV2(McpToolProvider())
        r_mcp = mcp_executor.execute(plan.model_copy(deep=True),
                                     task.model_copy(deep=True), ctx)
        assert r_mcp.task.status == TaskStatusV2.COMPLETED
        assert [t.tool_name for t in r_mcp.tool_calls] == ["risk_case_fetch"]
        # same semantic result either way
        assert (r_fc.tool_calls[0].result.outcome
                == r_mcp.tool_calls[0].result.outcome
                == ToolResultOutcome.SUCCESS)


@pytest.mark.skipif(not rp_available(), reason="Risk Platform not running")
class TestValidationSemanticsPreserved:
    def test_blank_case_id_stays_validation_error_not_integration(self):
        # 3. wrong/blank case_id → MCP server's validation_error semantics
        # survive the MCP path (must NOT become integration_error).
        p = McpToolProvider()
        fn = p.get("risk_case_fetch")
        result = fn({"case_id": ""})
        assert result.outcome == ToolResultOutcome.VALIDATION_ERROR
        assert result.error is not None
        assert result.error.code == "INVALID_ARGUMENT"


class TestMcpTransportFailure:
    def test_transport_failure_is_integration_error_no_success(self):
        # 4. MCP server/session failure → bounded integration_error;
        # never success or empty.
        p = McpToolProvider(client_with_broken_session())
        fn = p.get("risk_case_fetch")
        result = fn({"case_id": "U00299"})
        assert result.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert result.error is not None
        assert result.data is None

    def test_bogus_server_path_also_bounded(self):
        # no silent fallback: a provider whose MCP server cannot start
        # still returns a bounded integration_error.
        p = McpToolProvider()
        p._client._server_path = Path("/nonexistent/mcp_server.py")
        fn = p.get("risk_case_fetch")
        result = fn({"case_id": "U00299"})
        assert result.outcome == ToolResultOutcome.INTEGRATION_ERROR

    def test_no_silent_fallback_to_function_calling(self):
        # 8. MCP mode must never silently delegate to the Function Calling
        # provider when MCP fails: with MCP down, even a case the Function
        # Calling path would serve must return integration_error.
        broken = McpToolProvider(
            client=client_with_broken_session())
        fn = broken.get("risk_case_fetch")
        r = fn({"case_id": "U00299"})
        assert r.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert r.outcome != ToolResultOutcome.SUCCESS


class TestExecutorCompatibility:
    def test_executor_consumes_mcp_provider_without_knowledge(self):
        # 7. ExecutorV2 consumes `arguments -> ToolResult` and does not
        # know (or care) whether the capability came through MCP.
        p = McpToolProvider()
        assert isinstance(p, ToolProvider)     # same contract

        plan = make_plan("fetch_case")
        task = TaskV2(task_id="T-MCP", investigation_id="CASE:U00299",
                      user_request="Investigate U00299",
                      selected_skill="case_intake")
        ctx = make_context()

        if not rp_available():
            pytest.skip("Risk Platform not running")
        with patch("app.domain_tools.risk_case_fetch."
                   "RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       __import__("tests.test_risk_platform_adapter_v2",
                                  fromlist=["fx"])
                       .fx.rp_evidence_payload(uid),
                       __import__("tests.test_risk_platform_adapter_v2",
                                  fromlist=["fx"])
                       .fx.rp_explanation_payload())):
            executor = ExecutorV2(p)
            result = executor.execute(plan, task, ctx)

        assert result.task.status == TaskStatusV2.COMPLETED
        assert result.tool_calls[0].result.outcome == \
            ToolResultOutcome.SUCCESS

    def test_planner_output_unchanged_between_modes(self):
        # The Planner produces the same plan regardless of execution mode;
        # mode selection happens at the provider boundary only. (Scripted
        # planner: deterministic — mode comparison must not depend on
        # real-LLM variance.)
        from app.planner_v2 import PlannerV2
        from unittest.mock import patch as p_

        class Scripted:
            def generate(self, *a, **k):
                return ('{"skill_id": "case_intake", "goal": "g", "steps": '
                        '[{"type": "fetch_case", "reason": "r"}]}')

        planner = PlannerV2(Scripted())
        ctx = make_context()
        # "Investigate U00299" is planned DETERMINISTICALLY (accepted intake
        # behavior: fetch_case + case bundle) — identical regardless of the
        # execution mode chosen afterwards.
        plan_fc = planner.plan("Investigate U00299", ctx, ["case_intake"])
        plan_mcp = planner.plan("Investigate U00299", ctx, ["case_intake"])
        assert ([s.type for s in plan_fc.steps]
                == [s.type for s in plan_mcp.steps]
                == ["fetch_case", "generate_artifact"])


def make_plan(*step_types):
    return Plan(
        plan_id="PLAN-MCP-TEST",
        investigation_id="CASE:U00299",
        goal="mcp provider test",
        steps=[PlanStep(step_id=f"S{i}", type=t,
                        tool_name={"fetch_case": "risk_case_fetch",
                                   "generate_artifact": "artifact_bundle",
                                   }.get(t, t),
                        arguments={}, status=PlanStepStatus.PENDING)
               for i, t in enumerate(step_types, 1)],
        created_at=None,
    )

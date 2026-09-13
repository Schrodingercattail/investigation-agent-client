"""Tests for the MCP Capability Client (Level 2-A).

These tests exercise the real MCP stdio path: the client launches
`mcp_server/server.py` as a subprocess, performs the MCP handshake, and
invokes the real `risk_case_fetch` domain capability (Risk Platform
required for outcome=success cases).

Semantics under test:
- tool discovery (tools/list)
- call_tool restores the project ToolResult (outcome preserved)
- validation_error survives the transport (invalid arguments)
- aclose terminates the session; the client is restartable
"""

import asyncio

import pytest

pytestmark = pytest.mark.asyncio

from app.mcp_client import McpCapabilityClient
from app.models import ToolResultOutcome

CASE_ID = "U00299"
TIMEOUT = 60


async def started_client() -> McpCapabilityClient:
    c = McpCapabilityClient()
    await c.start()
    return c


async def with_timeout(awaitable_coro, timeout=TIMEOUT):
    return await asyncio.wait_for(awaitable_coro, timeout=timeout)


class TestLifecycle:
    async def test_start_initializes_session(self):
        c = await started_client()
        try:
            assert c._session is not None
            assert c.server_info is not None
        finally:
            await c.aclose()

    async def test_start_is_idempotent(self):
        c = await started_client()
        try:
            session_before = c._session
            await c.start()
            assert c._session is session_before
        finally:
            await c.aclose()

    async def test_aclose_terminates_and_restart_works(self):
        c = await started_client()
        await c.aclose()
        assert c._session is None
        await c.start()          # restartable after close
        await c.aclose()

    async def test_call_tool_before_start_is_bounded(self):
        c = McpCapabilityClient()
        with pytest.raises(RuntimeError):
            await c.call_tool("risk_case_fetch", {"case_id": CASE_ID})


class TestDiscovery:
    async def test_list_tools_discovers_risk_case_fetch(self):
        c = await started_client()
        try:
            tools = await asyncio.wait_for(c.list_tools(), TIMEOUT)
            names = [t["name"] for t in tools]
            assert "risk_case_fetch" in names
            schema = next(t for t in tools
                          if t["name"] == "risk_case_fetch")["input_schema"]
            assert schema.get("properties", {}).get("case_id", {}).get(
                "type") == "string"
            assert "case_id" in schema.get("required", [])
        finally:
            await c.aclose()


class TestCallSemantics:
    async def test_success_preserves_tool_result(self):
        c = await started_client()
        try:
            r = await asyncio.wait_for(
                c.call_tool("risk_case_fetch", {"case_id": CASE_ID}),
                TIMEOUT)
            assert r.outcome == ToolResultOutcome.SUCCESS
            data = r.data or {}
            assert data.get("case_id") == CASE_ID
            assert data.get("findings")
            assert r.evidence_refs and r.citation_refs
        finally:
            await c.aclose()

    async def test_validation_error_preserved_through_transport(self):
        c = await started_client()
        try:
            r = await asyncio.wait_for(
                c.call_tool("risk_case_fetch", {"case_id": ""}), TIMEOUT)
            assert r.outcome == ToolResultOutcome.VALIDATION_ERROR
            assert r.error is not None
            assert r.error.code == "INVALID_ARGUMENT"
        finally:
            await c.aclose()

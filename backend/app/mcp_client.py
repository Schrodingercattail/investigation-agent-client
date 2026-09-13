"""MCP Capability Client (Level 2-A) — a parallel access path to the
`risk_case_fetch` domain capability through the MCP Server
(`mcp_server/server.py`), using the MCP Python SDK (2.2.0) over stdio.

Design contract:

- This is an **adapter**, not a new capability implementation. The server
  delegates to the existing `domain_tools.risk_case_fetch`; this client
  delegates to the server over the MCP stdio transport.
- The client restores MCP results into the project's own `ToolResult`
  envelope so semantics (success / empty / validation_error /
  integration_error) survive the transport unchanged.
- Transport, subprocess, and protocol failures map to a bounded
  `integration_error` ToolResult — they never surface as exceptions to
  callers and never substitute other data.
- No chain-of-thought, prompts, or raw model output is involved or
  stored.

The existing Function Calling path (PlannerV2 → ExecutorV2 →
ToolProvider → domain tools) is unchanged; this module is an additional,
parallel access path and is not yet wired into ExecutorV2 (that is
Level 2-B).
"""

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.models import ToolError, ToolResult, ToolResultOutcome

# Repo layout: backend/app/mcp_client.py → backend/ is one level up; the
# server subprocess must import `app.*` from there (same as the Level 1
# test client, which passes the backend directory via PYTHONPATH).
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_SERVER_PATH = _BACKEND_DIR.parent / "mcp_server" / "server.py"


def _subprocess_env() -> dict[str, str]:
    """Environment for the server subprocess: inherit the parent
    environment and prepend the backend directory to PYTHONPATH so the
    server can import `app.domain_tools.risk_case_fetch`."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{_BACKEND_DIR}{os.pathsep}{existing}" if existing
        else str(_BACKEND_DIR)
    )
    return env


class McpCapabilityClient:
    """MCP Host adapter for the project's MCP Server.

    Launches `mcp_server/server.py` as a stdio subprocess (the server is
    the MCP *server*; this client is the MCP *host/client*), performs the
    MCP initialize handshake, and exposes capability discovery and
    invocation. Results are restored into the project's `ToolResult`
    envelope with outcome semantics preserved.
    """

    def __init__(
        self,
        server_path: str | Path | None = None,
        python_executable: str | None = None,
    ):
        self._server_path = Path(server_path or _DEFAULT_SERVER_PATH)
        self._command = python_executable or sys.executable
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._server_info: Any = None

    async def start(self) -> None:
        """Launch the MCP Server subprocess and initialize the session.

        Idempotent: calling start() on an already-started client is a
        no-op.
        """
        if self._session is not None:
            return
        server_params = StdioServerParameters(
            command=self._command,
            args=[str(self._server_path)],
            env=_subprocess_env(),
        )
        self._exit_stack = AsyncExitStack()
        try:
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(server_params))
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write))
            await self._session.initialize()
            self._server_info = self._session.server_info
        except BaseException:
            # never leave a half-open subprocess/stack behind
            await self.aclose()
            raise

    async def aclose(self) -> None:
        """Terminate the session and the server subprocess."""
        stack, self._exit_stack, self._session = (
            self._exit_stack, None, None)
        if stack is not None:
            await stack.aclose()

    async def __aenter__(self) -> "McpCapabilityClient":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # --- capability access -------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """MCP tool discovery: name, description, input schema per tool."""
        self._ensure_started()
        response = await self._session.list_tools()
        return [
            {"name": t.name, "description": t.description,
             "input_schema": t.input_schema}
            for t in response.tools
        ]

    @property
    def server_info(self) -> Any:
        return self._server_info

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Invoke an MCP tool and restore the project `ToolResult`.

        The MCP server returns the domain `ToolResult` (serialized JSON)
        as text content, so outcome semantics — success / empty /
        validation_error / integration_error — are preserved exactly.
        Transport and protocol failures are bounded as
        `integration_error` and never raise to the caller.
        """
        self._ensure_started()
        try:
            result = await self._session.call_tool(name, arguments=arguments)
        except Exception as e:
            return self._integration_error(
                f"MCP tool invocation failed: {type(e).__name__}: {e}")

        if getattr(result, "isError", False):
            text = self._first_text(result)
            return ToolResult(
                outcome=ToolResultOutcome.INTEGRATION_ERROR,
                error=ToolError(
                    code="MCP_TOOL_ERROR",
                    message=text or "MCP tool reported an error.",
                ),
            )

        # The server serializes the domain ToolResult as JSON text content.
        text = self._first_text(result)
        if text:
            try:
                return ToolResult.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                return ToolResult(
                    outcome=ToolResultOutcome.INTEGRATION_ERROR,
                    error=ToolError(
                        code="MCP_MALFORMED_RESULT",
                        message=(
                            f"MCP tool returned an unparseable result: {e}"),
                    ),
                )
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code="MCP_EMPTY_CONTENT",
                message="MCP tool returned no content.",
            ),
        )

    # --- internals -----------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._session is None:
            raise RuntimeError(
                "McpCapabilityClient is not started; call start() first.")

    @staticmethod
    def _first_text(mcp_result) -> str | None:
        for item in (mcp_result.content or []):
            text = getattr(item, "text", None)
            if text:
                return text
        return None

    @staticmethod
    def _integration_error(message: str) -> ToolResult:
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(code="MCP_TRANSPORT_ERROR", message=message),
        )

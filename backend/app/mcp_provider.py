"""MCP Tool Provider (Level 2-B) — exposes the existing MCP capability
path (`McpCapabilityClient` → MCP Server → `risk_case_fetch`) to
ExecutorV2 through the provider contract the Executor already consumes:

    tool_name → callable(arguments) -> ToolResult

Design contract:

- **Parallel access path**: the existing Function Calling provider
  (`default_tool_provider()`) remains the default and is unchanged. MCP
  is an explicitly selected execution mode — it is never chosen
  implicitly and never falls back to Function Calling on failure.
- **The Planner is unaware of MCP.** Plans still name `risk_case_fetch`
  (the same capability); only the execution path differs per mode.
- **The Executor is unaware of MCP.** `McpToolProvider` subclasses
  `ToolProvider`, so the Executor consumes it like any other provider
  (`arguments -> ToolResult`).
- The sync/async bridge lives entirely inside this adapter: the MCP
  session and its stdio subprocess are persistent, so they run on a
  dedicated background event loop thread and the Executor's synchronous
  call blocks on the submitted coroutine (bounded by timeouts). No
  `asyncio.run()` per call — that would restart the server subprocess on
  every invocation and cannot drive a persistent session.
- Transport/subprocess/protocol failures surface as bounded
  `integration_error` ToolResults. There is **no silent fallback** to
  Function Calling.
"""

import asyncio
import threading
from typing import Any

from app.executor_v2 import ExecutorV2, ToolProvider
from app.mcp_client import McpCapabilityClient

_CALL_TIMEOUT_S = 60.0
_START_TIMEOUT_S = 60.0

# The one capability currently exposed through MCP (Level 2 scope).
MCP_CAPABILITIES = ("risk_case_fetch",)

EXECUTION_MODE_FUNCTION_CALLING = "function_calling"
EXECUTION_MODE_MCP = "mcp"


class McpToolProvider(ToolProvider):
    """ToolProvider backed by the MCP Server via `McpCapabilityClient`.

    Contract-compatible with the existing Function Calling provider: the
    Executor consumes `arguments -> ToolResult` and never knows (or needs
    to know) whether the capability was reached through the in-process
    domain tool or through the MCP protocol.
    """

    def __init__(
        self,
        client: McpCapabilityClient | None = None,
        call_timeout_s: float = _CALL_TIMEOUT_S,
        start_timeout_s: float = _START_TIMEOUT_S,
    ):
        super().__init__()
        self._client = client or McpCapabilityClient()
        self._call_timeout_s = call_timeout_s
        self._start_timeout_s = start_timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client_started = False
        self._lock = threading.Lock()

        for name in MCP_CAPABILITIES:
            self.register(name, self._make_invoker(name))

    def _make_invoker(self, tool_name: str):
        """Per-tool callable for the ToolProvider contract:
        `arguments -> ToolResult`. The tool name is bound here so the
        Executor never passes (or knows) it."""
        def invoke(arguments: dict[str, Any]) -> Any:
            def coro():
                return self._client.call_tool(tool_name, arguments)
            try:
                return self._bridge(coro)
            except Exception as e:
                # Bounded failure at the MCP boundary: transport,
                # subprocess, or protocol problems become
                # integration_error. No silent fallback to Function
                # Calling, no fabricated success.
                from app.models import ToolError, ToolResult, ToolResultOutcome
                return ToolResult(
                    outcome=ToolResultOutcome.INTEGRATION_ERROR,
                    error=ToolError(
                        code="MCP_TRANSPORT_ERROR",
                        message=(
                            f"MCP execution path failed for {tool_name!r}: "
                            f"{type(e).__name__}: {e}"),
                    ),
                )
        return invoke

    # --- sync/async bridge ---------------------------------------------------

    def _bridge(self, coro_factory) -> Any:
        """Run a coroutine on the dedicated persistent background loop and
        block the (synchronous) Executor thread until it completes. The
        loop and the MCP session live inside this adapter; startup is
        lazy and lock-protected."""
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever,
                    name="mcp-provider-loop",
                    daemon=True,
                )
                self._thread.start()
            if not self._client_started:
                fut = asyncio.run_coroutine_threadsafe(
                    self._client.start(), self._loop)
                fut.result(timeout=self._start_timeout_s)
                self._client_started = True
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return fut.result(timeout=self._call_timeout_s)

    # --- lifecycle ---------------------------------------------------------------

    def close(self) -> None:
        """Graceful shutdown: close the MCP client/session, then stop the
        background loop. Optional — the loop thread is a daemon, so
        process exit also cleans up."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
            self._client_started = False
        if loop is not None:
            if self._client_started:
                fut = asyncio.run_coroutine_threadsafe(
                    self._client.aclose(), loop)
                try:
                    fut.result(timeout=10.0)
                except Exception:            # best-effort teardown
                    pass
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=5.0)


# --- explicit execution-mode switch ------------------------------------------

def build_executor(mode: str = EXECUTION_MODE_FUNCTION_CALLING,
                   **executor_kwargs):
    """Build an ExecutorV2 whose tool provider matches the explicitly
    requested execution mode.

    - "function_calling" (DEFAULT): the existing in-process provider —
      unchanged user behavior.
    - "mcp": the same capability reached through
      McpCapabilityClient → MCP Server → risk_case_fetch.

    The Planner output is identical for both modes: a plan naming
    `risk_case_fetch` is executable through either provider. There is no
    silent fallback between modes.
    """
    from app.executor_v2 import default_tool_provider

    if mode == EXECUTION_MODE_FUNCTION_CALLING:
        provider = default_tool_provider()
    elif mode == EXECUTION_MODE_MCP:
        provider = McpToolProvider()
    else:
        raise ValueError(
            f"Unknown execution mode {mode!r}; expected "
            f"{EXECUTION_MODE_FUNCTION_CALLING!r} or "
            f"{EXECUTION_MODE_MCP!r}.")
    return ExecutorV2(provider, **executor_kwargs)

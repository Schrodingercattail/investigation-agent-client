# Investigation Agent Client — Development Context

Context for Claude Code / AI-assisted development. This is not product
documentation — see `README.md` for the project overview.

## Project Context

- Case/Context-centered **Investigation Agent Client**: an analyst opens
  a case, investigates its findings through conversation, and produces
  provenance-traceable investigation artifacts.
- The runtime combines planning, context resolution, tool use,
  constrained execution, and scope-controlled artifacts, running on top
  of an external, authoritative Risk Platform.
- Risk investigation is the reference domain; the architecture is
  intended to be reusable for other enterprise investigation /
  decision-support workflows.

## Core Architecture

- **Planner decides WHAT** capability/task is needed: it maps natural
  language to a structured plan (`Plan` / `PlanSteps`) and does not
  invent authoritative attributes.
- **Executor controls constrained execution**: contract checks,
  argument injection, bounded outcomes. It consumes
  `arguments -> ToolResult` and is access-path agnostic.
- **Provider boundary determines HOW a capability is accessed**:
  in-process Function Calling (default) or MCP.
- **Domain capabilities are the single source of truth**
  (`backend/app/domain_tools/`).
- Do not duplicate business logic in access adapters (MCP server,
  providers, protocol bridges).
- The Risk Platform remains authoritative for findings, evidence,
  policy citations, and explanations.

## MCP

- `risk_case_fetch` is currently the **only** MCP-exposed capability.
- MCP is an additional capability-access path, **not** a replacement for
  Function Calling, which remains the default execution mode.
- Current MCP transport is **local stdio** only. Remote MCP, HTTP
  transport, MCP Gateway, and multi-agent MCP are not implemented.
- Agent Runtime MCP path:
  `Executor → McpToolProvider → McpCapabilityClient → MCP Server`
- External Host validation:
  `Claude Code → MCP Server` (registered via project-level `.mcp.json`)
- The same MCP Server and the same domain capability are reused across
  hosts; the MCP server is a thin adapter, not a second implementation.
- **No silent fallback** between MCP and Function Calling: MCP-path
  failures surface as bounded `integration_error` results.
- The Planner must not contain MCP-specific vocabulary or transport
  logic; plans name capabilities regardless of access path.
- Do not add MCP-related abstractions unless justified by an actual
  capability / access requirement.

## Important Files

- `backend/app/planner_v2.py` — LLM planner (natural language → plan)
- `backend/app/executor_v2.py` — constrained plan execution
- `backend/app/mcp_client.py` — `McpCapabilityClient` (MCP client adapter)
- `backend/app/mcp_provider.py` — `McpToolProvider` and execution-mode switch
- `backend/app/domain_tools/risk_case_fetch.py` — domain capability
  (single source of truth for the case-fetch capability)
- `mcp_server/server.py` — MCP Server (stdio adapter over the domain capability)
- `mcp_server/test_client.py` — standalone MCP protocol test client
- `.mcp.json` — project-level MCP Server registration for external hosts
- `docs/architecture/MCP_ARCHITECTURE_V1.md` — MCP architecture and
  validation record

## Development Rules

- Avoid unrelated changes; keep diffs scoped to the task.
- Preserve existing Function Calling behavior unless a change explicitly
  requires otherwise.
- Do not silently fall back from MCP to Function Calling.
- Keep domain logic out of protocol / access adapters.
- Prefer small, scoped changes with tests (`backend/tests/`).
- Do not modify the older pre-V2 `backend/app/agent.py` unless
  explicitly required.

## Documentation Sources of Truth

- `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md` — runtime
  architecture (resolution → planning → execution → composition)
- `docs/architecture/MCP_ARCHITECTURE_V1.md` — MCP access path and
  three-level validation
- Other docs in `docs/architecture/` (domain models, skill model, API
  contract, evaluation, evidence principles) — consult before changing
  the corresponding architecture.

# MCP Architecture V1

**Status**: ACTIVE — architecture record for the MCP access path (Level 1
implemented and validated; Level 2 / Level 3 planned)
**Date**: 2026-09-11
**Related**: `AGENT_CLIENT_ARCHITECTURE_V2.md` (§6.1 `risk_case_fetch`),
`DOMAIN_MODELS_V1.md` (§2.x Finding / ToolResult), `AGENT_EVALUATION_V1.md`

---

## 1. Purpose

The Investigation Agent Client already has a complete Function Calling /
tool-calling path: PlannerV2 selects steps, ExecutorV2 invokes domain
tools through the ToolProvider, and those tools call the Risk Platform.

This document records why and how **MCP (Model Context Protocol)** is
being added **alongside** that path:

- The existing Function Calling path is **retained unchanged**.
- MCP provides an **additional access path to the same capability** —
  it does not replace, wrap-for-replacement, or reimplement Function
  Calling.
- MCP is used to learn and validate the protocol mechanics in this
  repository: server lifecycle, tool **discovery**, tool **invocation**,
  and client/server **communication** over a real transport.
- It prepares the capability for **reuse by multiple future MCP Hosts**
  (e.g. Claude Code or other MCP clients), instead of binding
  `risk_case_fetch` to the current Agent Client only.

At Week 1+ scope, exactly one capability is exposed: `risk_case_fetch`.
This document describes the implemented state only.

---

## 2. Design Principle

> **Same capability, multiple access paths.**

The domain implementation of `risk_case_fetch` exists **exactly once**, in
`backend/app/domain_tools/risk_case_fetch.py`. It is the only business
implementation.

- Function Calling consumes this capability through the Agent's
  planner/executor/tool-provider path.
- MCP consumes the **same** capability through a protocol adapter
  (`mcp_server/server.py`) that delegates to the same domain function.

Function Calling has **not** been converted into MCP, and the MCP server
is not a new implementation of the capability — it is an **adapter /
access path**. Both paths coexist so the two capability-access
mechanisms can be compared on equal footing.

---

## 3. Current Architecture

```
                    Risk Platform (external, authoritative)
                              ↓
        ┌─────────────────────────────────────────────┐
        │   risk_case_fetch — domain capability       │
        │   backend/app/domain_tools/risk_case_fetch.py│
        └─────────────────────────────────────────────┘
              ↓                                    ↓
 ┌──────────────────────────┐      ┌──────────────────────────────────┐
 │  Existing Function       │      │  MCP access path (new)           │
 │  Calling path            │      │                                  │
 │  · planner_v2            │      │  MCP Server                      │
 │  · executor_v2           │      │  mcp_server/server.py            │
 │  · skills registry       │      │  (MCP Python SDK 2.2.0,          │
 │  · tool provider         │      │   stdio transport)               │
 ↓                          │      ↓                                  │
 Agent Runtime —            │      MCP Client / MCP Host              │
 structured ToolResult,     │      (mcp_server/test_client.py         │
 artifacts, composition     │      today; other hosts future)         │
 └──────────────────────────┘
```

Boundary notes:

- The **MCP Server is an adapter**, not the capability itself. Its tool
  handler directly calls the existing
  `domain_tools.risk_case_fetch.risk_case_fetch()` and serializes the
  resulting `ToolResult` (`result.model_dump(mode="json")`). It contains
  no business logic of its own.
- The **MCP Server is not the Risk Platform** and does not contain Risk
  Platform integration logic — the domain capability remains the only
  component that talks to the RP adapter.
- The **MCP Server is not the Agent Runtime** — it does not plan,
  execute plans, compose responses, or persist tasks.

---

## 4. Existing Function Calling Path (unchanged)

The original and still-primary path inside the Agent:

```
Agent request
  → ContextResolver
  → PlannerV2 (selects skill/step)
  → ExecutorV2 (contract check, argument injection)
  → ToolProvider / existing tool path
  → risk_case_fetch (domain capability)
  → Risk Platform
  → ToolResult → response / artifact composition
```

This entire path — components, semantics, and contract-checked
capability gating — is **unchanged** by the MCP work. All runtime
semantic evaluation scenarios (15/15) continue to run through it.

---

## 5. MCP Path (Level 1, implemented)

The new parallel path:

```
MCP Test Client (mcp_server/test_client.py)
  → MCP protocol over stdio transport
  → MCP Server (mcp_server/server.py)
  → risk_case_fetch (domain capability)
  → Risk Platform
  → ToolResult (serialized JSON) → MCP tool result
```

Current transport is **local stdio** (`stdio_client` +
`server.run("stdio")`). HTTP-based transports are not implemented and
are not claimed in this document.

---

## 6. MCP Tool Contract

Exactly **one** MCP tool is currently exposed:

| Property | Value |
|---|---|
| Name | `risk_case_fetch` |
| Input schema | `case_id: string` (required) |
| Description | "Fetch the canonical investigation context and findings for a risk case." |
| Delegates to | `backend/app/domain_tools/risk_case_fetch.py` → `risk_case_fetch()` |
| Return | serialized `ToolResult` (`result.model_dump(mode="json")`) |

The MCP tool handler is a thin adapter: it performs **no business
logic**, no RP integration, and no result transformation beyond JSON
serialization of the existing `ToolResult`. Any change to the capability
semantics happens in `domain_tools.risk_case_fetch` and is inherited by
both access paths automatically.

---

## 7. Three-Level Roadmap

### Level 1 — Existing capability exposed through MCP

**Goal:** `risk_case_fetch` reachable through an MCP Server.

**Validated scope:**
- MCP Server can start.
- MCP Client can initialize a session.
- Tool discovery works (`tools/list`).
- `risk_case_fetch` is listed with its `case_id` input schema.
- MCP `tools/call` invokes `risk_case_fetch` successfully.
- Real case `U00299` reaches the Risk Platform (HTTP 200 on both
  `/evidence` and `/explain`).
- Returned result contains successful findings / evidence_refs /
  citation_refs data.
- The existing Function Calling implementation was not modified.

**Status:** ✅ COMPLETED / VALIDATED (see §8 for evidence).

### Level 2 — The Agent consumes the MCP Server

**Goal:** the Agent Client gains an MCP consumption path:

```
Agent → MCP Client → MCP Server → risk_case_fetch
```

operating **in parallel with** the retained path:

```
Agent → existing Function Calling → risk_case_fetch
```

Level 2 does **not** replace Function Calling. Its purpose is to let
both mechanisms coexist inside the Agent so they can be compared
(side-by-side capability access, protocol overhead, behavior parity).

**Status:** 🔲 PLANNED — not implemented.

### Level 3 — External MCP Hosts reuse the same Server

**Goal:** external MCP Hosts (e.g. Claude Code, other MCP clients)
connect to the **same** MCP Server and consume the same capability:

```
External MCP Host → MCP Server → risk_case_fetch
```

This demonstrates that the capability does not depend on the current
Agent Client: the server is a reusable capability endpoint for any
compliant MCP Host.

**Status:** 🔲 PLANNED — not implemented.

---

## 8. Validation Evidence (Level 1)

Recorded from the executed run of `mcp_server/test_client.py` against
the real Risk Platform:

| Item | Result |
|---|---|
| MCP Server | `risk-investigation` ("Risk Investigation MCP Server") |
| Server version | `0.1.0` |
| MCP SDK | MCP Python SDK 2.2.0 (`MCPServer`, stdio transport) |
| Exposed tool | `risk_case_fetch` (`case_id: string`) |
| Test case | `U00299` |
| MCP initialize | passed |
| Tool discovery (`tools/list`) | passed |
| Tool invocation (`tools/call`) | passed |
| Risk Platform response | HTTP 200 (evidence + explain endpoints) |
| Tool outcome | `success` (findings, evidence_refs, citation_refs returned) |
| Test client exit code | 0 |
| Traceback | none |
| Function Calling path | unmodified |

**`structured_content` note:** the MCP tool currently declares **no
output schema**, so the server omits `structuredContent` and the client
correctly prints `structured_content: null`. The complete payload is
returned through the tool **content** (text JSON). This is the current
implemented state of Level 1 — an available extension (declaring an
output schema) is not yet implemented and is not a fault.

---

## 9. Non-Goals / Boundaries

- MCP does **not** replace Function Calling; both paths remain.
- MCP does **not** move business logic out of `domain_tools` — the
  server is an adapter over the existing capability.
- The MCP Server does **not** duplicate Risk Platform integration logic
  (no RP endpoints, no normalization, no citations handling of its own).
- Level 2 (Agent-side MCP consumption) and Level 3 (external MCP Hosts)
  are **planned, not implemented** — this document does not describe
  them as working capabilities.
- Only `risk_case_fetch` is exposed today. No other investigation
  capability is currently an MCP tool.

---

## 10. Future Extension

Once the `risk_case_fetch` access path has been exercised further, the
same adapter pattern can expose additional stable domain capabilities as
MCP tools, for example:

- `finding_drilldown` (timeline / evidence views, explicit
  evidence-stream scopes)
- `policy_lookup` (policy retrieval)
- other reusable investigation capabilities as they stabilize

None of these are implemented as MCP tools in the current V1; they are
listed only as future extension directions.

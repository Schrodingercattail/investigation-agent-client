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

## Development / QA Quality Gate

This project has repeatedly seen features ship with green isolated
tests while the real browser journey was broken (lost context/focus
between UI and backend, stale frontend state across investigations,
new APIs ignoring pending UI state, wrong terminal plan rendering).
Therefore:

> **"Tests passing" and "feature accepted" are different states.**
> A passing unit/integration suite is NOT evidence that a user-facing
> feature is complete. Completion evidence must match the feature's
> integration surface.

### Required testing levels

- **LEVEL 1 — Component correctness**: unit / component / deterministic
  logic / schema-contract tests.
- **LEVEL 2 — Integration/E2E**: real application boundaries where
  applicable (UI → API → service → runtime → persistence → dependency
  → response/state → UI). Mocks are fine for deterministic coverage but
  do not constitute integration acceptance.
- **LEVEL 3 — Real browser/user acceptance**: REQUIRED when a change
  affects frontend behavior, conversation, Agent responses, planner
  behavior visible to users, the Agent Loop, tool routing,
  context/focus, state synchronization, polling, evidence/artifact
  navigation, user-visible status, or any cross-module workflow. The
  real browser journey must be exercised against the real running
  stack. Backend-only changes with no user-visible impact may skip it,
  but still need API/integration tests. When uncertain, prefer real E2E.

### Feature-scoped Definition of Done

Every non-trivial feature defines its acceptance surface up front and
names at least one **Golden User Journey**: a repeatable scenario from
the real user entry point that crosses all relevant boundaries and
verifies both the visible outcome and the backend/state consequences.
Re-run it after changes that affect its dependencies. Example shape
(Finding-focused): Case → select finding → request → API → target
resolution → Planner → Tool → Agent State → Activity → response.

The implementation report MUST separate: unit-tested ·
integration-tested · browser-validated · exploratory-tested, and
explicitly list what remains unverified and any pre-existing
failures (backend baseline: environment-dependent legacy failures —
~43 with RP down, ~12 with RP up; compare like-for-like). Never
summarize as "tested". Never use test counts as the primary evidence
of product correctness.

### Browser is the acceptance authority

When automated tests and real browser behavior disagree, the browser
result wins for product acceptance. Do not report "complete" while the
primary browser journey is broken; a browser failure triggers
reproduce → failing-boundary analysis → fix → browser re-test.

### Test the system, not just the module

For cross-module features, trace: user action → frontend state → API →
route → service → runtime → tool/dependency → persistence → response →
frontend → rendered result, and identify where semantic state can be
lost. Pay special attention to: context/focus propagation, task and
investigation identity, state ownership, terminal states, retries,
polling, stale state, event ordering, API/frontend contracts. Then run
lightweight exploratory testing (double-clicks, reload during/after
execution, entity switching mid-run, empty/long/Unicode/CJK inputs,
unknown IDs, RP/backend unavailable, unexpected-but-reasonable
phrasings) — find what the plan did not predict. Semantic edge cases
(no data, partial data, unsupported, blocked, error, retryable,
terminal, loop limit, ambiguous, unresolved, stale context, empty
result) are part of acceptance; do not collapse them into one
success/failure check.

### Agent-specific gate

Agent features are evaluated AS A SYSTEM: Context → Target Resolution →
Planning → Tool selection/execution → Observation → Decision → State →
Loop bound → Retry → Provenance → user-facing response → Activity.
Include natural-language, paraphrased, ambiguous, and unanticipated
requests — not only exact command phrases. Always verify the boundary:
LLM proposes, system enforces; state is authoritative; the UI does not
infer backend decisions; the backend does not rely on frontend-only
state; language never overclaims evidence; internal reasoning is never
exposed.

### Environment honesty

Before declaring completion, verify the tested processes actually run
the current implementation (stale long-running services are a real QA
finding — restart and re-verify). Mocks/fault injection are encouraged
for determinism, but when a real dependency (RP, LLM, browser) was
unavailable, document the limitation and do not claim the real path
was validated.

### Canonical runtime

The canonical development/acceptance runtime is the committed
configuration: **frontend :3001 (`frontend/vite.config.ts`) → backend
:8001 (uvicorn) → Risk Platform :8000**, sharing
`backend/data/investigation_agent_v2.db` (the default store path is
derived from `__file__` and is cwd-independent). Every work item's
FINAL acceptance journey must run against this stack AFTER restarting
its processes onto the current working tree — passing on an isolated
QA stack does not satisfy acceptance. Isolated QA stacks (e.g.
`frontend/vite.qa.config.ts` → :8010) are temporary scaffolding for
deterministic/no-key/parallel runs: never the acceptance surface, and
torn down after use. Before diagnosing "it fails in my browser",
check which code the target stack actually runs
(`GET /api/v2/runtime/identity` + process start time; note the
identity fingerprints are computed from disk at request time and do
NOT prove what a long-running process has loaded — use a behavioral
marker instead).

### Defect handling

On an E2E/browser failure: reproduce → identify the failing boundary
(UI→API, API→service, service→runtime, runtime→tool, tool→dependency,
state→UI, context→planner, finding/focus→execution, task→polling,
plan→terminal) → root cause → smallest correct fix at that boundary →
browser re-test. Do not patch symptoms. Preserve Developer
Verification vs Acceptance QA separation where the project defines a
QA phase.

### Reporting and language

End substantive tasks with: Changed · unit/component results ·
integration/API E2E results · real browser results · exploratory
results · known unverified areas · pre-existing failures · regressions
· acceptance status. Use explicit statuses: NOT TESTED / TESTED /
PASS / FAIL / BLOCKED — never turn "not tested" into "pass". Reserve
**"Implemented"** for code passing developer verification,
**"Validated"** for behavior exercised at the relevant integration
level, and **"Accepted/Ready"** for features past the acceptance gate.
Never say "feature complete" when the required browser/E2E gate has
not run.

### Pre-completion self-check

1. Did I test the actual user entry point?
2. Did I cross the real integration boundaries?
3. Did I run the primary Golden User Journey?
4. If frontend behavior changed, did I open the real browser?
5. Did I test at least one meaningful edge case?
6. Could stale state, wrong context, or races exist?
7. Does the UI match backend/runtime truth?
8. Did I distinguish tests from acceptance?
9. Did I state what remains unverified?
10. Could a real user complete the task I claim is complete?

If an applicable answer is "no", do not report the feature as fully
validated.

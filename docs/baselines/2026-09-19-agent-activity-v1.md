# Baseline: Agent Activity / Execution Trace V1

**Status**: ACTIVE BASELINE — implementation contract for the frontend
Agent Activity phase. §21 records the **implemented** state (V1 landed;
resume execution, MCP, architecture sync remain deferred per §18).
**Date**: 2026-09-19 (**updated** 2026-09-19: Agent Activity V1
IMPLEMENTED — see §21)
**Relationship to sibling baselines** (§24):
- `2026-09-18-cross-account-trade-analysis-v1.md` — historical feature
  baseline (reference only)
- `2026-09-19-bounded-agent-loop-v1.md` — **authoritative runtime
  source**: Agent Loop, Agent State, decision model, telemetry contract
- THIS file — the frontend Activity implementation baseline

> Doc-only phase: `docs/architecture/` is NOT modified (sync milestone
> remains post-implementation, loop baseline §18). No code changes here.

---

## 1. Product goal

Give the analyst a **transparent, user-safe view of what the
Investigation Agent is doing during execution**: what it is trying to
accomplish, which step it is on, which capability it invoked, what came
back at a high level, what it decided, and why it continued, re-planned,
stopped, or was blocked.

The experience shows **execution/activity — never hidden model
reasoning**. Product language: **Agent Activity**, **Execution Trace**,
**Decision Summary**, **Result Summary**. Forbidden labels: chain-of-
thought, hidden reasoning, model thinking, raw reasoning trace.

## 2. Inspected current state (facts this baseline builds on)

### Backend (exists today)

- `AgentLoopState` (persisted per task in `TaskStoreV2.agent_states`):
  goal, criteria kind, focus, plan snapshot (step ids/types/statuses),
  completed steps, current step, compact observations (bounded),
  decisions (decision + user-safe reason + `coerced_from`), iteration,
  `agent_status`, `stop_reason`, artifact ids, retry counters, response.
- `AgentTelemetryEvent` + loop event family (`agent.started` …
  `agent.failed`, `step.retried`) with task/iteration/step/tool/outcome/
  decision/status/stop-reason/user-safe-summary fields.
- `TaskDetailResponse` (GET `/api/v2/tasks/{task_id}`): task + bounded
  `PlanSummary` (step statuses) + bounded `ToolCallSummary` list
  (tool_call_id, tool_name, status, outcome, timestamps, human summary
  phrase, ref counts) + artifacts + response text.
- Artifacts endpoints (`GET /tasks/{id}/artifacts`, `/results`).
- **Production telemetry default sink is no-op**: loop events are
  emitted best-effort but NOT persisted or queryable today.

### Frontend (exists today)

- V2 Investigation Workspace (`pages/InvestigationPage.tsx`): 
  `ConversationPanel` (per-message restore from `task_response_text`),
  `PlanCard`, `TaskStatus`, `ContextPanel`, `FindingsPanel`,
  `ArtifactsPanel`, `FollowUpChips`, `InvestigationHistory`;
  shadcn/ui + Tailwind; typed API client (`api/investigation.ts` +
  `api/types.ts`). No frontend tests exist yet.

### What the frontend can already obtain (A/B)

Goal/task, plan + step statuses, per-tool-call outcome summaries with
human phrases, artifacts, response text — via existing endpoints.

### What is missing (C — required backend additions, §19)

1. **A persisted, queryable activity record per loop task.** Telemetry
   is observational/best-effort with a no-op default sink by contract —
   it cannot become the UI's data source. The minimal, contract-
   consistent addition: the loop runtime extends `AgentLoopState` with a
   bounded `activity` list (one record per loop event: sequence, event
   type, timestamp, iteration, step id/type, tool, outcome, decision,
   user-safe summary, artifact/evidence ref ids) written at the same
   transitions where state persists. This is structured runtime STATE —
   not log parsing — and it is reliably persisted, unlike the optional
   telemetry sink (whose contract and no-op default are unchanged).
2. **An activity/status read endpoint**, e.g.
   `GET /api/v2/tasks/{task_id}/activity` returning the agent-state
   summary (goal, status, stop reason, iteration, current step, plan
   snapshot, artifact ids) + the bounded activity list.
3. **A loop-driven execution path reachable from the API** (today the
   HTTP turn flow uses the turn-local service only). Minimal V1 shape is
   an implementation decision (§19): run the loop via the existing turn
   pipeline or a dedicated loop-mode endpoint — the Activity contract
   does not depend on which.

### Satisfiable purely from existing events/state (D)

The complete §4 experience (goal → … → terminal) is constructible from
the loop state + a persisted activity record; the existing
ToolCallSummary/artifact endpoints provide drill-down. No new analytics,
no streaming.

### Backend contract changes required (E)

Only the three additions above — all additive, no changes to existing
endpoint contracts, no telemetry-contract changes (the UI consumes the
state-borne activity record; the telemetry family remains the semantic
source that the activity records mirror).

## 3. Event → UI mapping (frozen)

| Event | UI concept |
|---|---|
| `agent.started` | Run header: goal shown, status Running |
| `context.resolved` | Context/focus line (case-level or finding) |
| `plan.created` | Plan block (numbered when a re-plan: "Plan 2") |
| `step.started` | Step row enters "current" |
| `tool.called` | Tool activity row ("invoking <capability>") |
| `tool.completed` | Tool row gains outcome/status |
| `observation.recorded` | Result summary line (from the compact summary) |
| `decision.made` | Decision summary row (decision + reason) |
| `step.completed` | Step row → completed |
| `step.retried` | "Retrying after a temporary error" row |
| `agent.completed` / `agent.blocked` / `agent.stopped` / `agent.failed` | Terminal banner (distinct, §9) |

No new event semantics are introduced. Required fields all exist in the
loop event contract (task, iteration, step, tool, status, user-safe
summary, refs). **Open question (minimal)**: stable per-event ids — V1
uses the persisted sequence number within the task's activity record;
global event ids are unnecessary.

## 4. Timeline reconstruction (V1)

- Identity: group by `task_id`; order by the persisted sequence number
  (monotonic, in-process), timestamp as display metadata.
- Duplicates: drop by sequence number (idempotent renders).
- Out-of-order: the record is appended in-process in emission order;
  the client sorts by sequence — tolerant by construction.
- Synchronization (§16 equivalent): **polling** the activity endpoint —
  every 2s while `agent_status == "running"`, stop at any terminal
  status. No WebSocket/SSE (none exists; none required in V1 —
  documented as a future decision only if latency ever demands it).
- Terminal detection: presence of an `agent.*` terminal event /
  non-running `agent_status`.
- No distributed-streaming infrastructure in V1.

## 5. UI information architecture (minimum V1)

| Area | Content | Visibility |
|---|---|---|
| A. Goal / task | goal text, criteria kind | always |
| B. Current status | agent status badge + iteration count (e.g. "Iteration 3 of 8") | always |
| C. Activity timeline | the event-mapped rows (§3), newest appended | always, scrollable; rows expandable |
| D. Current plan | plan snapshot: step states (completed ✓ / current ● / pending ○ / superseded ↷), plan number after re-plans | collapsed by default, expandable |
| E. Decision summary | latest decision + reason prominent; full decision trail in the timeline | always (latest) |
| F. Evidence / artifacts | artifact references (link to the existing Artifacts panel); evidence ref counts with bounded id lists on expanded rows | expandable |

Not an observability console: one glance answers "what is it doing and
why". Long tool/result text truncates with expansion.

## 6. Activity states (user-visible, distinct)

`Running` · `Re-planning` (from a RE_PLAN decision row / plan.created
sequence) · `Retrying` (step.retried) · `Completed` · `Blocked` ·
`Failed` · `Stopped by limit` · `Interrupted / resumable` (rendered
only if `agent_status == "interrupted_resumable"` is present; no resume
action in V1 — resume execution remains deferred per the loop baseline).

Completed / Blocked / Failed / Stopped-by-limit are four distinct
labels, icons + text (never color alone), and distinct terminal banner
wording sourced from `stop_reason`.

## 7. Tool activity presentation

Per tool row: capability name (`cross_account_trade_analysis`,
`finding_drilldown`…), one-line result summary sourced from the
activity record's user-safe summary (e.g. "3 candidate relationships
found", "timeline with 12 events", "opposite-trade ratio 45.24% for the
same account"), outcome badge, and — expanded — evidence reference ids
and artifact ids. **Never rendered**: raw transaction datasets, full
tool payloads, implementation internals. Drill-down to full evidence
goes through the existing evidence/artifact endpoints and panels, not
inline dumps.

## 8. Plan presentation (bounded, honest)

Show the CURRENT plan snapshot exactly as the runtime holds it — a
near-term plan, not a pretended complete investigation. Step states:
completed ✓ / current ● / pending ○ / **superseded ↷** (steps of a
superseded plan). Re-plans are first-class timeline rows:

```
Plan 1
  step A ✓   step B ✓   step C ↷ superseded
Re-plan — "observation changed the direction" (decision summary)
Plan 2
  step D ● current   step E ○ pending
```

No planner prompts, no raw reasoning.

## 9. Decision presentation

Each decision renders as: **decision chip** (CONTINUE / RE_PLAN /
STOP / BLOCK) + the ≤300-char user-safe reason + iteration/step
context; coercions show as the enforced decision (runtime truth). A
standing micro-copy note keeps semantics honest: decisions are runtime
flow control, not business conclusions.

## 10. Observation / result presentation

Principle (loop baseline §13): **tool-internal data ≠ LLM observation ≠
UI presentation**. The UI renders only the bounded user-safe summaries
already produced by the runtime (activity records / ToolCallSummary
phrases). For analytical results: aggregate metrics and bounded
candidate summaries, provenance ids, and navigation to existing
evidence/artifact surfaces. Arbitrary payload rendering is prohibited.

## 11. Evidence & provenance

Artifact references link to the existing Artifacts panel/endpoint;
evidence references render as bounded id lists on expanded rows; the
underlying evidence remains RP-authoritative via the existing endpoints.
**No second evidence store.**

## 12. Active run vs historical trace (V1 scope)

- **A. Active/current run**: supported — polling the activity endpoint
  during execution (§4).
- **B. Completed-run history**: supported for loop runs whose persisted
  `AgentLoopState` exists (the state + activity record are durable) —
  reopening a task renders its trace read-only. Turn-local (non-loop)
  tasks have no activity record: the UI falls back to the existing
  TaskDetail rendering (plan + tool-call summaries), clearly not
  presented as an Execution Trace. Replay-from-events for pre-loop
  history is **deferred**.

## 13. Performance / context boundaries

The Activity UI never loads raw datasets: its payloads are the bounded
activity records and existing summaries. V1 caps (implementation
decisions within the loop's existing bounds): render the last N=200
activity rows with older-rows collapsing; expanded rows render at most
the already-capped id lists. Evidence drill-down is lazy via existing
endpoints only.

## 14. Usability requirements (V1)

Current vs completed steps visually distinct; terminal state always
visible; dense rows expandable; readable full-length status labels
(no bare abbreviations); status encoded by icon + text + color (never
color alone); long tool names/result text truncate with expansion;
sequence + timestamps shown where useful.

## 15. Backend contract gaps (summary)

| Item | Status |
|---|---|
| Goal/plan/tool summaries, artifacts endpoints | **already supported** |
| Persisted bounded per-task activity record (state-borne) | **required backend addition** (additive; telemetry contract unchanged) |
| `GET /api/v2/tasks/{task_id}/activity` (state + activity) | **required backend addition** |
| Loop execution reachable from the API layer | **required backend addition** (shape = implementation decision) |
| Stable global event ids | optional future (sequence numbers suffice in V1) |
| WebSocket/SSE live streaming | optional future, explicitly not in V1 |

## 16. Evaluation

- **Deterministic component tests** (new — the frontend has none today;
  V1 introduces them for this surface): render a scripted activity
  record set and assert: run header/goal; context line; plan block with
  step states; tool rows with summaries; decision rows (all four
  decisions); re-plan creates a visible new plan block with superseded
  steps; retry row; each terminal state renders distinctly
  (completed/blocked/failed/stopped-by-limit); iteration counter;
  evidence/artifact refs usable (links); **no chain-of-thought
  exposure** (assert forbidden labels/content absent); **no raw
  transaction payload rendered** (assert raw row fields absent);
  duplicate/out-of-order records normalize by sequence.
- **Backend/API contract tests**: activity endpoint returns state
  summary + bounded records for a deterministic loop run; terminal
  detection fields correct; existing endpoint contracts unchanged.
- **E2E runtime + UI**: a deterministic loop run (scripted proposer/
  tools, stubbed RP) exercised through the API with the UI consuming
  the endpoint: cases 1–20 of the instruction matrix map onto the above
  (start visible; context; plan; tool call; result; decision; CONTINUE
  sequence; RE_PLAN branch; STOP/BLOCK/FAILED/STOPPED_BY_LIMIT
  distinct; retry; evidence + artifact refs usable; no CoT; no raw
  payload; long-run readability via caps; reload reconstructs the
  current run from the endpoint; completed-run history per §12 scope).

## 17. Definition of Done (Agent Activity V1)

1. Activity timeline implemented in the investigation workspace,
   consuming ONLY the structured activity endpoint (no log parsing).
2. Goal, current status badge, iteration count always visible.
3. Current step, tool calls, result summaries, decision summaries
   visible per §3/§5.
4. CONTINUE/RE_PLAN/STOP/BLOCK visible with user-safe reasons;
   re-plan renders plan supersession (§8).
5. Four terminal states distinct (§6).
6. Evidence/artifact references rendered and usable via existing
   surfaces; no second evidence store.
7. No chain-of-thought, raw payload, or prompt content anywhere in the
   component (tested).
8. Polling refresh works; reload reconstructs the current/completed run
   from the endpoint; terminal detection stops polling.
9. Backend additions shipped and contract-tested: state-borne activity
   record, activity endpoint, loop execution reachable from the API.
10. Existing investigation UI regression-free (turn-local tasks render
    as today; their TaskDetail view is unaffected).
11. Component + API contract + E2E tests per §16 pass. Resume
    execution remains out of scope (interrupted state renders, no
    resume action).

## 18. Explicitly deferred

Resume execution · MCP · architecture-document synchronization ·
observability dashboards · distributed event infrastructure ·
WebSocket/SSE · token-level tracing / chain-of-thought visualization ·
candidate ML scoring · tolerance calibration · mobile-specific redesign ·
full historical audit console (incl. replay for pre-loop tasks).

## 19. Architecture synchronization policy

Unchanged from the loop baseline §18: `docs/architecture/` is not
touched in this phase. The dedicated sync milestone happens only after
Agent Loop + State + Telemetry + **this Agent Activity frontend** are
implemented and validated together, reconciling
`AGENT_CLIENT_ARCHITECTURE_V2.md`, `SKILL_MODEL_V1.md`, runtime/module
descriptions, frontend/backend boundaries, and telemetry architecture;
baselines remain historical contracts.

## 20. Baseline relationships

| File | Role |
|---|---|
| `2026-09-18-cross-account-trade-analysis-v1.md` | historical feature baseline |
| `2026-09-19-bounded-agent-loop-v1.md` | Agent Runtime baseline (authoritative for loop/state/telemetry) |
| `2026-09-19-agent-activity-v1.md` (this file) | Frontend Activity implementation baseline |

---

## 21. Implementation Record — Agent Activity V1
**(added 2026-09-19; implemented as contracted — deferred items remain)**

### Implementation status: DONE (resume, MCP, arch-sync, dashboards deferred)

### Backend additions (the three §15 gaps, closed)

1. **State-borne activity record**: `AgentLoopState.activity` (bounded to
   the latest 200 records, sequence-numbered via `activity_seq`) — the
   runner mirrors every structured event into it at the SAME persistence
   points (`_emit`), so the terminal-status flip and the terminal
   activity row always land in one persist (a poll observing
   `is_terminal` is guaranteed the terminal row — ordering contract
   verified by test). Records are user-safe summaries only; telemetry
   contract and its no-op default sink are unchanged.
2. **`GET /api/v2/tasks/{task_id}/activity`** → `TaskActivityResponse`:
   the whitelisted projection (`project_task_activity` — goal, criteria,
   focus, status, stop reason, iteration/max, plan snapshot with step
   statuses, artifact ids, activity records, `is_terminal`). Internal
   observations/decision structures/retry maps are NOT exposed; 404
   `NO_AGENT_ACTIVITY` for pre-loop turn-local tasks (the UI falls back
   to the existing TaskDetail rendering and never mislabels it).
3. **Loop execution path**: `POST /api/v2/investigations/{id}/goal`
   (`GoalRunRequest{goal}` → `GoalRunResponse{task_id, …}`) starts the
   bounded loop in a background thread via the `loop_runner_factory` DI
   seam (default: real runner; tests inject a deterministic factory) and
   returns immediately for polling. Existing turn/task APIs unchanged.

### Frontend additions

- `api/types.ts` + `api/investigation.ts`: `TaskActivityResponse`,
  `AgentActivityRecord`, `AgentPlanStep`, `GoalRunResponse` types;
  `getTaskActivity` / `startAgentGoal` client methods (existing
  `InvestigationApiError` conventions).
- `hooks/useAgentActivity.ts`: polling hook — 2s interval, stops at
  terminal status, skips fetches while the page is hidden, 404 →
  `notFound` fallback signal, transient errors keep the last snapshot;
  `normalizeActivity` deduplicates by seq and sorts (defensive §4
  contract).
- `components/investigation/AgentActivityPanel.tsx`: run header (goal,
  case/focus, status badge with icon+text+color, "Iteration N of M"),
  plan snapshot (✓/●/○/↷ + failed, "Plan 2 (re-planned)" after
  re-plans), activity timeline with human-readable event titles,
  decision chips (CONTINUE/RE-PLAN/STOP/BLOCK) + user-safe reasons,
  outcome badges, artifact references. First frontend tests
  (vitest + Testing Library, `npm test`).
- `pages/InvestigationPage.tsx`: third right-column tab "Activity" (with
  in-flight spinner), "Run loop" affordance (submits the current input
  as a loop goal; switches to Activity and polls), latest loop-task
  restore on reopen; pre-loop tasks render the not-found fallback.
  Existing conversation/context/artifacts rendering untouched.

### Test / evaluation results

- Frontend component tests: **14/14** (`npm test`) — header/plan/step
  states, tool + decision rows, four distinct terminals, retry row,
  artifact refs, **no-CoT and no-raw-payload assertions**, duplicate/
  out-of-order normalization, terminal polling stop (single fetch, no
  further calls), empty state. `tsc --noEmit` clean.
- Backend contract + deterministic E2E (`tests/test_agent_activity_api_v1.py`):
  **9/9** — goal start→poll→terminal through the API; whitelisted
  projection (exact key set; no observations/decisions/retries/response);
  sequence ordering + no duplicates; record field whitelist;
  no-raw-payload/no-CoT markers; bounded window; goal 404/422;
  turn-local task → 404 `NO_AGENT_ACTIVITY`.
- Backend regression: full suite + Layer B — **43 failures,
  byte-identical to the standing pre-existing baseline**; 927 passed,
  zero new failures.

### Deviations

- One existing test hardened (not weakened):
  `test_v2_router_does_not_import_legacy_modules` used bare-substring
  checks that falsely matched the NEW `app.agent_loop` module; its
  assertions now use precise import forms (the legacy-isolation intent
  is unchanged and still enforced).
- `AGENT_LOOP_STEP_TIMEOUT_S` still not implemented (unchanged from the
  loop baseline's documented V1 deviation).

---

## 22. Defect-Fix Record — QA-01..QA-09 (2026-09-19, post-QA fixes)

**Status: FIXED — READY FOR QA RE-RUN** (per the QA report's verdict
FAIL; independent re-run to confirm).

- **QA-01 (P1)** — the goal endpoint now honors the client's pending
  selection: `GoalRunRequest.context_action` (FocusAction), applied AND
  persisted in `start_goal` (turn-parity), and the UI sends the pending
  focus with the goal. Verified live: goal POST body carries
  `context_action`; AgentLoopState records `focus=F2`; the planner plans
  the focused trade tool; the activity trace shows `finding F2`.
- **QA-02 (P2)** — `_terminate` re-syncs the plan snapshot from the
  runtime Plan before the terminal persist; terminal runs now show
  executed steps as success (verified in browser + API + Layer-A
  assertions for completion/pending/limit/blocked).
- **QA-03 (P2)** — `openInvestigation` clears `activityTaskId` on every
  switch; `useAgentActivity` discards in-flight responses for a
  superseded task id (ref guard) and maps 404 to a `notFound` fallback.
  Verified: switch to a loop-less investigation shows the fallback, no
  stale trace; rapid switch tests added.
- **QA-04 (P2)** — dependency boundary made explicit: findings resolve
  INSIDE the loop via an injectable `findings_provider` (strict on the
  goal path — RP unavailability raises). A dependency failure at run
  start is a distinct `blocked / dependency_unavailable` terminal state
  with a user-safe message (verified live with RP stopped); retryable
  tool failures after a step starts keep the bounded retry (fault-
  injection E2E: retry→success and retry→blocked both verified through
  the real API).
- **QA-05 (P3)** — observations carry a separate analyst-facing
  `display` phrase ("Found 3 candidate cross-account opposite-trade
  relationships (candidates only, not conclusions)."); the machine
  digest remains planner/decision-internal.
- **QA-06 (P3)** — traditional CJK added to the deterministic
  cross-account hook (跨賬戶/對倒); regression-tested.
- **QA-07 (P4)** — conclusive NO_MATCH runs now report the precise
  `conclusive_empty_result` stop reason (observed live).
- **QA-08 (P4)** — "Starting…" replaces "Iteration 0 of 8" pre-first-step.
- **QA-09 (P4)** — "Restoring investigation…" indicator during
  workspace restore.

### Fix-phase test results

- Backend: 56 loop/activity/fix tests green (incl. 16 new defect-fix
  regressions); full-suite failures identical to the standing
  environment baseline (the suite is RP-availability-sensitive in its
  legacy/live-RP portions: 43 failures with RP down, 12 with RP up —
  both sets pre-existing, none new; swap behavior reproduced and
  documented).
- Frontend: 19/19 component/hook tests; `tsc` clean.
- Browser: focused EN + CJK journeys, case-level journey, stale-switch
  journey, RP-down blocked journey, reload reconstruction — all green
  against the real running stack.

### Deviations

- None beyond the QA report's noted V1 scope (resume, frontend
  additions beyond Activity, MCP, arch-sync remain deferred).

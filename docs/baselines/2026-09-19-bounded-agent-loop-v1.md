# Baseline: Bounded Investigation Agent Loop V1

**Status**: ACTIVE BASELINE — implementation contract for the Agent Loop
phase. §21 records the **implemented** state (V1 landed; resume
execution, frontend, MCP, architecture sync remain deferred per §12/§15/
§18).
**Date**: 2026-09-19 (**updated** 2026-09-19: Agent Loop V1 IMPLEMENTED —
see §21)
**Related**: `2026-09-18-cross-account-trade-analysis-v1.md` (§12b.9
preserved this phase's boundary; OQ-9 is resolved by this document),
`docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md` (turn-local runtime
description — NOT synchronized in this phase, see §18),
`AGENT_EVALUATION_V1.md` (three-layer evaluation philosophy, reused).

> Documentation rule: this is a baseline under `docs/baselines/` — the
> repository's phase-contract convention. It does NOT modify
> `docs/architecture/`; architecture synchronization is a dedicated later
> milestone (§18). No application code is changed in this phase.

---

## 1. Scope

Evolve the current turn-local execution model into a **bounded Agent
Loop**: a persisted, iterating investigate → observe → decide cycle with
system-enforced limits, bounded retry, explicit stop semantics, and a
structured telemetry contract ready for a future frontend.

Explicitly OUT of scope for the implementation phase this baseline
defines: frontend UI (§15), MCP, resume execution (§12), architecture-
doc synchronization (§18), any change to tool matching semantics
(`cross_account_trade_analysis`, `self_opposite_trades` etc. keep their
frozen contracts).

---

## 2. Current state vs target state

**CURRENT (normative today — do not claim it is the target):**

- `InvestigationService.run_turn` is turn-local: resolve → eligible
  skills → plan once → execute the plan's steps in order → compose →
  follow-ups → persist. One plan per user turn.
- `ExecutorV2` runs the whole plan; a failed required step fails the
  task; `EMPTY` is a valid step answer; **no autonomous retry**.
- Re-planning happens only between turns (user/follow-up initiated).
- The only loop precedent is the superseded `agent.py`
  (`max_steps=8`, `max_steps_exceeded`) — pre-V2, do-not-modify.
- Frontend renders task/plan/tool-call state from the API; no Agent
  Activity / Execution Trace view.
- Structured telemetry exists (`AgentTelemetryEvent`: planner.* /
  execution.* / semantic.*) but carries no loop/decision events.

**TARGET (this baseline — not yet implemented):**

```
User Goal
 → Context Resolution
 → Initial Plan                      (near-term, receding horizon)
 → Execute ONE step
 → Observation                       (normalized ToolResult)
 → Decision                          (CONTINUE | RE_PLAN | STOP | BLOCK)
 → Persist Agent State
 → Next iteration  ↺                 (bounded)
 → Completion | bounded stop         (STOPPED_BY_LIMIT / BLOCKED / FAILED)
```

---

## 3. Task-level completion

A tool result or a completed step is **never** equivalent to completion
of the overall user goal. **TASK-LEVEL COMPLETION** is a property of the
goal, defined as explicit **task completion criteria** recorded on the
Agent State when the initial plan is created (planner-proposed goal with
system-validated defaults; inspectable, bounded structure — V1 shape is
an implementation decision within this constraint).

The Agent may stop only when:

1. the task completion criteria are satisfied (e.g. the user's question
   has a grounded answer among observations; a requested artifact was
   produced), OR
2. the task is **conclusively blocked or inconclusive** with no viable
   next action (e.g. `NO_MATCH` on complete evidence with no
   alternative path in scope), OR
3. the system-enforced loop bound is reached — which is
   **`STOPPED_BY_LIMIT`, never completion** (§9).

A `MATCH_FOUND` result MUST NOT automatically imply task completion:
it is one observation. Whether it completes the goal depends solely on
the recorded criteria (e.g. "does X exist?" may be answerable by one
candidate; "analyze all related accounts" is not).

## 4. Step-level evidence sufficiency

A separate, weaker concept: an observation provides **step-level
evidence sufficiency** when it contains enough information to determine
a reasonable next action — it says nothing about goal completion.

Examples over the existing tools:

| Observation | Step-sufficient? | Task-complete? |
|---|---|---|
| `inspect_timeline` events returned | yes — next action known (e.g. drill into an event) | no |
| `cross_account_trade_analysis` → `MATCH_FOUND` (1 of N accounts analyzed) | yes — continue with remaining scope | no, unless criteria were "does any candidate exist" |
| same `MATCH_FOUND` under criteria "does any candidate exist" | yes | yes (STOP) |
| `NO_MATCH` on complete evidence | yes — decision known | possibly (STOP if no alternative path; RE_PLAN if the goal suggests one) |
| `INSUFFICIENT_DATA` | yes — the gap is named (`next_data_needed`) | no — seek another path or BLOCK |
| `integration_error` | yes — retry/re-plan decision can be made | no |

Step sufficiency is the normal case; **lack** of step sufficiency
(uninterpretable observation) is itself a bounded failure handled by §8.

---

## 5. Agent State model

One explicit persisted **Agent State** per loop run, extending the
existing `TaskStoreV2` (model-document JSON — new fields need no schema
migration; whether loop state rides `tasks_v2` or a sibling table is an
implementation decision — no duplicate second store is created).
TaskV2/TaskStatusV2 remain the container lifecycle; the loop adds its
own explicit fields so loop semantics are never collapsed into
`completed/failed`:

- `task_id`, `goal` (user-safe), `completion_criteria`
- resolved context (`case_id`, `focused_finding_id`, focus source)
- `current_plan` (plan_id + steps snapshot), `completed_steps`,
  `current_step`
- `observations` (normalized ToolResult summaries — compact, provenance-
  carrying; never raw RP dumps)
- `decisions` (per-iteration decision + user-safe reason)
- `iteration_number`
- `agent_status` (§13), `stop_reason`
- `artifact_ids` (loop-produced)
- `telemetry_refs` (event identifiers for this run)

State is written after **every meaningful transition** (plan created,
step executed, decision made, status changed) — audit/inspection reads
the same store the Task Center already uses.

## 6. Decision model (frozen)

Exactly four top-level decisions, made per iteration after observation:

| Decision | Meaning |
|---|---|
| `CONTINUE` | the current plan remains valid; execute the next planned step |
| `RE_PLAN` | the observation changed the investigation direction or invalidated the remaining plan; produce a NEW bounded plan (bounded by the same loop limits) |
| `STOP` | task completion criteria are satisfied, or no further action is needed and the result stands |
| `BLOCK` | a required capability / data / permission / system condition prevents progress; the task ends blocked — **never rendered as successful completion** |

Authority split: the **LLM proposes** a decision (bounded output: one
of the four + a user-safe reason); the **system decides** — a
deterministic policy layer validates the proposal against the state
(bounds, retry counters, criteria) and enforces overrides (e.g. an LLM
`STOP` on a reached-bound run is still `STOPPED_BY_LIMIT`; an LLM
cannot `CONTINUE` past the bound or retry past the cap). The exact
policy mechanics are an implementation decision within this split.

## 7. Tool result interpretation

Uses the existing semantic distinctions (ToolResult outcomes; the
analysis statuses `MATCH_FOUND / NO_MATCH / INSUFFICIENT_DATA / ERROR`).
There is deliberately **NO universal rule** such as "any non-MATCH
result means re-plan":

| Observation | Possible decisions |
|---|---|
| `MATCH_FOUND` | `CONTINUE` (more scope to cover) · `RE_PLAN` (result opens a new direction) · `STOP` (criteria satisfied) — goal-dependent |
| `NO_MATCH` | does NOT mean "nothing suspicious"; `RE_PLAN` (alternative path/window in scope) · `STOP` (conclusive for the analyzed scope under the criteria) |
| `INSUFFICIENT_DATA` | seek another valid path first (alternate tool, narrower scope, different window) → `CONTINUE`/`RE_PLAN`; if no path exists → `BLOCK` / inconclusive bounded stop. Never composed as a negative finding |
| `ERROR` (transient) | bounded deterministic retry (§8) → then `CONTINUE` |
| `ERROR` (persistent) | `RE_PLAN` (different path) or `BLOCK`/`failed`; never silent, never emptied |

## 8. Retry policy (bounded, system-controlled)

The LLM never decides retries or retry counts.

- **Retryable**: transient integration failures only — RP
  `unavailable`/timeout-class errors (the adapter's typed error kinds).
- **Not retryable**: `validation_error` (deterministic — same input,
  same failure), `unsupported`, semantic results (`NO_MATCH` /
  `INSUFFICIENT_DATA` are answers, not failures), malformed-response
  after a retry, and any LLM-proposed "try again" beyond the cap.
- **Cap**: `AGENT_LOOP_MAX_RETRIES_PER_STEP` (provisional V1 default 1)
  per step, tracked in Agent State; retries are visible in the task log
  and telemetry (`step.retried`).
- **Timeout**: step latency inherits the RP adapter timeout; a
  loop-level wall-clock budget is a provisional implementation decision
  (`AGENT_LOOP_STEP_TIMEOUT_S`, optional).
- A retry that fails leaves the step failed and hands the decision
  layer a persistent-ERROR observation (§7).

## 9. Loop bounds (system-enforced)

- `AGENT_LOOP_MAX_ITERATIONS` — provisional V1 default **8** (precedent:
  the legacy loop's `max_steps=8`), configurable via Settings, never
  embedded in prompts and never LLM-chosen.
- One iteration = one executed step + observation + decision. A re-plan
  consumes an iteration too (planning is work).
- Reaching the bound: `agent_status = STOPPED_BY_LIMIT` with a typed
  `stop_reason`, partial results/preserved state surfaced — **MUST NOT
  be represented as ordinary SUCCESS** anywhere in state, API, or
  composer wording.
- No-communication guard: if the decision layer cannot produce a valid
  decision (e.g. repeatedly invalid LLM proposals), the loop terminates
  bounded (`BLOCK`/`failed`) rather than spinning — counted against the
  same iteration budget.

## 10. Planning strategy (bounded / receding horizon)

V1 planning stays inside the existing constrained planner
(`PlannerV2` vocabularies + contract checker). The loop re-plans
incrementally instead of requiring an up-front complete investigation
plan:

resolve context → initial near-term plan → execute one step → observe
→ decide → continue or re-plan.

Rationale: observations routinely invalidate the original plan (a
match found redirects scope; insufficient data changes the next
capability; an error closes a path). Receding-horizon planning bounds
planning cost, keeps every plan contract-checked, and matches the
existing per-turn plan machinery — no hierarchical planner, no
free-form goals, no new planning vocabulary in V1. A re-plan MUST pass
the same registry/contract validation as an initial plan.

## 11. LLM vs system boundary

| LLM MAY | System MUST control (LLM cannot override) |
|---|---|
| interpret the user goal | permissions & case/finding scope |
| propose the initial plan (inside registry vocabularies) | tool registry + schema/contract validation |
| choose the next investigation capability | authoritative data access (RP boundary) |
| interpret compact tool observations | retrieval budgets & matching thresholds (`TRADE_ANALYSIS_*`) |
| propose task completion | state persistence & its integrity |
| propose re-planning | retry / timeout policy |
| provide the user-safe decision reason | maximum iterations & stop enforcement |
| | provenance & artifact integrity |
| | all deterministic calculations (e.g. the matching engine) |

Decisions are enforced per §6: proposals are inputs to a deterministic
policy, never commands.

## 12. Persistence / resume

Agent State persists after every meaningful transition (§5). The
persisted state is sufficient for future **resume, inspection, audit,
and deterministic reproduction where possible** (bounded inputs,
recorded plans/decisions, stable IDs). **Resume execution is NOT
implemented in this phase** — it is not made trivial by the current
process-local session wiring, so its scope (resume entry point,
conflict rules, staleness bounds) is marked for the following
implementation phase (§19). This phase delivers resume-READY state
only.

## 13. Failure / interruption semantics (distinct states)

`agent_status` (explicit field; never collapsed into one `failed`):

| Status | Meaning | User-facing |
|---|---|---|
| `running` | loop active | progress visible |
| `completed` | criteria satisfied (STOP) | done, with result |
| `stopped_by_limit` | iteration bound reached | explicit "bounded stop", partial results, NOT success |
| `blocked` | capability/data/system condition (BLOCK) | explains exactly what is missing |
| `failed` | unrecoverable execution/planning failure | explains the failure class |
| `interrupted_resumable` | process ended mid-run with persisted state | resumable later (resume itself: §12) |

TaskV2 status keeps its own lifecycle; the API/composer render
`agent_status` + `stop_reason` so the user can always see WHY execution
stopped.

## 14. Telemetry / Agent Activity event contract

Extends the existing typed `AgentTelemetryEvent` (observational,
best-effort, versioned — never authoritative, never chain-of-thought).
New event types alongside the existing planner.*/execution.* family:

`agent.started` · `context.resolved` · `plan.created` · `step.started` ·
`tool.called` · `tool.completed` · `observation.recorded` ·
`decision.made` · `step.completed` · `agent.completed` ·
`agent.blocked` · `agent.stopped` (covers `stopped_by_limit`) ·
`agent.failed`

Every event carries: task/investigation/case/finding identifiers,
`iteration`, `step` (type + id), `tool` (name + status where
applicable), and a **user-safe summary / decision reason** — written
for the analyst, containing no hidden reasoning, no raw prompts, no
model output. Evidence/artifact references ride where applicable.
Field-level schema freeze is an implementation decision (§19); the
event set and the no-chain-of-thought rule are frozen here.

## 15. Frontend requirement (recorded; OUT OF SCOPE now)

A future frontend phase renders the Agent Activity / Execution Trace
from these structured events — never parsed logs, never raw reasoning:

```
Goal
  ↓ Plan / current step
  ↓ Tool call
  ↓ Result summary
  ↓ Decision summary
  ↓ Next step
  ↓ Completion | blocked | bounded stop
```

The runtime/event contract above is designed FIRST so the frontend
phase consumes it without backend rework. **The frontend Activity
implementation baseline is now defined:**
`2026-09-19-agent-activity-v1.md` (event→UI mapping, reconstruction,
contract gaps).

## 16. Evaluation

Follows the existing three-layer philosophy; layers never merge into
one score. **Layer A (deterministic runtime)**: scripted decisions/plans
over stubbed RP — the loop machinery is fully deterministic-tested.
**Layer B (real LLM)**: planning/re-plan proposal quality. **Layer C
(live)**: optional later, real RP + LLM.

1. one-step task completing immediately (STOP, completed)
2. multi-step investigation (CONTINUE chain, completed)
3. `MATCH_FOUND` → `CONTINUE` (goal not yet satisfied)
4. `MATCH_FOUND` → `STOP` (criteria satisfied)
5. `NO_MATCH` → `RE_PLAN` (alternative path pursued)
6. `INSUFFICIENT_DATA` → alternative action (not BLOCK while a path exists)
7. transient `ERROR` → bounded retry → success
8. persistent `ERROR` → `BLOCK`/`failed` bounded state
9. plan invalidated mid-run → `RE_PLAN` with a contract-checked new plan
10. maximum iterations reached → `STOPPED_BY_LIMIT`, never success
11. state persisted after each step (inspectable mid-run)
12. resume-state integrity (structure valid; execution deferred §12)
13. deterministic stop reason (typed, reproducible)
14. a tool result never directly dictates task completion (criteria do)
15. no infinite loop (bound + invalid-decision guard, adversarial
    always-CONTINUE proposals)
16. structured telemetry emitted for every §14 event, no CoT leakage
17. frontend-ready event semantics (events carry the §14 metadata)

Layer B scenarios additionally cover re-plan proposal quality (e.g.
`INSUFFICIENT_DATA` → sensible alternative capability) with the
existing run-level stability protocol.

## 17. Definition of Done (Agent Loop phase)

1. Bounded loop implemented (execute-one-step → observe → decide) with
   `AGENT_LOOP_MAX_ITERATIONS` enforced in code; reaching it yields
   `STOPPED_BY_LIMIT` rendered distinctly from success everywhere.
2. Explicit persisted Agent State (§5) written after every meaningful
   transition; inspectable via the existing store.
3. `CONTINUE / RE_PLAN / STOP / BLOCK` implemented with the §6
   authority split (LLM proposes, deterministic policy disposes).
4. Task-level completion criteria recorded on state; a tool result can
   never set completion directly (eval case 14).
5. Bounded system-controlled retry (§8): retryable classes, cap,
   visibility; LLM cannot extend.
6. Distinct terminal states (§13) surfaced through the API/composer
   with explain-why wording.
7. Structured telemetry for the full §14 event set (user-safe reasons,
   no chain-of-thought), sink-isolated as today.
8. Deterministic runtime evaluation green (Layer A cases 1–17);
   real-LLM Layer B scenarios for planning/re-plan quality stable per
   protocol; existing three-layer suites unchanged.
9. No infinite loop (bound + invalid-decision guard tested
   adversarially).
10. Existing tool compatibility: all domain tools keep their frozen
    contracts; current turn-local behavior preserved for non-loop
    paths (no regression; the existing suites stay green byte-for-byte
    on failure sets).
11. Frontend event contract ready (§14/§15) — UI itself is the
    follow-on phase. Resume execution explicitly out of scope (§12).

## 18. Architecture synchronization policy

Architecture documents are NOT synchronized in this phase. After Agent
Loop + Agent State + Telemetry + Frontend Activity are **implemented
and validated**, a dedicated architecture-synchronization milestone
will: update architecture diagrams and module responsibilities;
reconcile the turn-local descriptions in
`AGENT_CLIENT_ARCHITECTURE_V2.md` (§3/§4/§11a) and `SKILL_MODEL_V1.md`
with the validated loop model; document the final runtime; remove
obsolete descriptions; and keep `docs/baselines/*` as historical
phase contracts.

## 19. Open questions

| # | Question | Classification |
|---|---|---|
| ALQ-1 | Exact Agent State persistence shape (extend `tasks_v2` document vs sibling table) | Implementation decision |
| ALQ-2 | Completion-criteria structure V1 (planner-emitted fields, system defaults) | Implementation decision within §3 |
| ALQ-3 | Decision-policy mechanics (deterministic rules over observation classes; how LLM proposals are validated/arbitrated) | Implementation decision within §6 |
| ALQ-4 | `AGENT_LOOP_MAX_ITERATIONS=8`, `AGENT_LOOP_MAX_RETRIES_PER_STEP=1`, optional step timeout | DECIDED as provisional defaults; values configurable, calibration OPEN |
| ALQ-5 | Event schema field-level freeze (names/types per §14 event) | Implementation decision; event set + no-CoT rule frozen |
| ALQ-6 | Resume execution scope (entry point, staleness, conflict rules) | Deliberately deferred to the next implementation phase |
| ALQ-7 | Frontend Activity visual design / interaction details | Deliberately deferred (frontend phase; §15 flow frozen) |
| ALQ-8 | MCP exposure of loop-driven capabilities | Deliberately deferred (unchanged standing rule) |
| ALQ-9 | Layer C live loop scenarios | Deferred until Layer A/B land (existing layer philosophy) |

## 20. Configuration summary (provisional, system-controlled)

`AGENT_LOOP_MAX_ITERATIONS=8` · `AGENT_LOOP_MAX_RETRIES_PER_STEP=1` ·
`AGENT_LOOP_STEP_TIMEOUT_S` (optional) — added to `Settings` at
implementation time in the established `TRADE_ANALYSIS_*` pattern;
never prompt-embedded, never LLM-chosen.

---

## 21. Implementation Record — Agent Loop V1
**(added 2026-09-19; the contract above is implemented except the items
explicitly deferred in §12/§15/§18)**

### Implementation status: DONE (resume execution, frontend, MCP, arch-sync deferred)

### Runtime components

- **`backend/app/agent_loop.py` (new)** — the loop coordinator:
  - `AgentLoopState` — the persisted state model (§5): goal,
    criteria kind, resolved focus, plan snapshot (id/steps/statuses),
    completed steps, current step, compact observations (last 12),
    decisions (with coercion provenance), iteration, `agent_status`,
    `stop_reason`, artifact ids, per-step retry counters, invalid-
    proposal counter, response. Persisted via TaskStoreV2 after every
    meaningful transition — including after the observation, BEFORE the
    decision (mid-run inspection verified by test).
  - `project_observation` — the GLOBAL observation-projection boundary
    (§13): per-tool-kind whitelists (case fetch → finding summaries;
    drilldown views → counts + capped ids; self view → ratio/rule/ids;
    cross tool → its own bounded candidate contract; policy → top
    citations; artifact → id/title/scope) and a generic fallback that
    carries NO payload body and not even key names. Raw record arrays
    never reach the proposer/planner; verified by tests including a
    secret-payload leak check.
  - `LoopPolicy` — the deterministic decision enforcement (§6/§12),
    fixed rule order: unusable proposals → deterministic fallback;
    completion dominance (satisfied criteria ⇒ STOP even against a
    BLOCK proposal); STOP requires justification (criteria satisfied or
    an authoritative zero on complete evidence) else bounded coercion to
    CONTINUE/RE_PLAN; CONTINUE/RE_PLAN require iteration budget, else
    `STOPPED_BY_LIMIT`; exhausted plan + CONTINUE ⇒ RE_PLAN; BLOCK
    accepted, never rendered as completion. Coercions carry
    `coerced_from` for the audit trail.
  - Proposers: `LLMDecisionProposer` (bounded JSON proposal + user-safe
    reason; 2048-token budget — thinking models truncate below that,
    same captured lesson as the planner) and
    `HeuristicDecisionProposer` (observation-class fallback; also the
    no-LLM default).
  - `AgentLoopRunner` — the §3 loop: context resolution (bounded
    outcomes terminate BLOCKED/completed-guidance) → initial plan →
    per-iteration ONE step through `ExecutorV2.execute_one_step` →
    bounded retry → observation → proposal → enforcement → persist →
    act. Re-plans invoke PlannerV2 with the goal + a COMPACT progress
    digest (never raw results), producing contract-checked plans.
  - `compose_loop_response` — bounded, user-safe terminal response
    explaining status, steps, observations, artifacts, and stop reason.
- **`ExecutorV2.execute_one_step`** — public one-step entry wrapping the
  existing `_execute_step` (same injection/locks/artifact semantics;
  whole-plan `execute()` untouched).
- **`TaskStoreV2`** — `agent_states` table (same DB file,
  document-per-record pattern) + `save_agent_state`/`get_agent_state`.
  No second store.
- **`telemetry.py`** — Agent Loop field group on `AgentTelemetryEvent`
  (task_id, iteration, step_id/step_type, tool_name, outcome, decision,
  agent_status, stop_reason, retry_count, artifact_ids, user-safe
  summary) and the §14 event family (incl. `step.retried`).
- **`config.py`** — `AGENT_LOOP_MAX_ITERATIONS=8`,
  `AGENT_LOOP_MAX_RETRIES_PER_STEP=1` (provisional §20 values).
- Untouched: `InvestigationService.run_turn` (turn-local path fully
  preserved), all domain tools, skill registry, API contracts, legacy
  `agent.py`.

### State / decision / retry semantics (as implemented)

- Criteria kinds (ALQ-2 V1): `question_answered` (default;
  authoritative non-gap success observation) and `artifact_produced`
  (artifact intent ⇒ artifact exists). A ToolResult can never set
  completion directly (tested).
- Retry: transient `RISK_PLATFORM_UNAVAILABLE`/`MCP_TRANSPORT_ERROR`
  integration errors only, cap 1/step, budget-consuming, persisted,
  never for semantic results (tested).
- Terminal states (§13): completed / stopped_by_limit / blocked /
  failed surfaced distinctly; blocked/stopped_by_limit mark the task
  container FAILED (coarse) while the Agent State + response carry the
  precise semantics and reason (documented V1 mapping).
- Artifacts: produced only by planned `generate_artifact` steps (as
  with the deterministic intake plan); the loop itself never calls
  artifact_bundle outside a plan.

### Evaluation results

- **Layer A** (`tests/test_agent_loop_v1.py`): **28/28 green** — the
  full §16 matrix: immediate completion; CONTINUE chains;
  MATCH_FOUND→CONTINUE and →STOP; NO_MATCH→RE_PLAN (plan superseded
  mid-run, superseded step never executes); INSUFFICIENT_DATA→
  alternative; retryable-ERROR→recovery (retry consumes budget,
  single observation); persistent-ERROR→blocked; bound→
  STOPPED_BY_LIMIT with an adversarial always-CONTINUE proposer and
  garbage proposals (heuristic fallback, invalid count tracked);
  unjustified STOP coercion; ToolResult≠completion (policy unit + gap
  run reaching the bound, never completing); per-step persistence
  (mid-run store reads), resume-ready round-trip incl.
  `interrupted_resumable`, deterministic stop reasons; telemetry
  completeness + no-CoT + ordering (frontend-reconstructable); the
  projection boundary (no raw records, no key names, capped ids); and
  real-tool loop runs over stubbed RP: risk_case_fetch→artifact
  completion, self_opposite_trades view, cross_account_trade_analysis
  MATCH_FOUND, finding_drilldown timeline.
- **Layer B** (`tests/test_agent_loop_llm_v1.py`, real LLM): **3/3** —
  the proposer distinguishes satisfied goal (STOP) from
  match-but-more-scope (CONTINUE/RE_PLAN); INSUFFICIENT_DATA with a
  remaining path never proposes STOP/BLOCK; truncated-NO_MATCH on an
  unanswered goal proposes RE_PLAN with a user-safe reason. Run under
  the same real-LLM variance philosophy as the planner layer (decision
  SETS are asserted; the deterministic policy bounds any variance).
- **Regression**: full suite + Layer B — **43 failures, byte-identical
  to the standing pre-existing baseline** (legacy pre-V2 suites +
  live-RP telemetry/MCP tests). Zero new failures; the turn-local
  runtime and all prior suites unchanged.

### Deviations from baseline

- `AGENT_LOOP_STEP_TIMEOUT_S` (§20 "optional") not added: steps inherit
  the RP adapter timeout in V1; a loop-level wall-clock budget remains
  a future option.
- Task-container mapping: blocked/stopped_by_limit set the coarse
  TaskV2 status to FAILED (the container has no such states) while the
  Agent State + response carry the precise terminal semantics — the
  baseline's no-collapse rule holds at the Agent State/API-response
  layer, which is what users and the future UI consume.
- `step.retried` added to the event family beyond §14's minimum
  (required by §8's retry-visibility rule).

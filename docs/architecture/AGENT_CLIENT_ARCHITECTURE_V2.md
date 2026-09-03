# Agent Client Architecture V2 — Final Week 1 Design

**Status**: ACTIVE — single source of truth for the current design
**Date**: 2026-08-26 (updated for final Week 1 architecture)
**Supersedes**: All documents in `docs/archive/` and any earlier Phase 1 / agent-loop design notes

> If any other document contradicts this one, this document wins.

---

## 1. Product Definition

The Agent Client is a **case-centric investigation assistant** built on top of
the Risk Platform. Its UX arc:

```
Case Intake ──► Focus Mode ──► Artifacts + Task Log
```

- **Case Intake** — investigator selects a case (`case_id`); authoritative case
  context is fetched and presented.
- **Focus Mode** — capability-aware, multi-turn investigation of a selected
  finding within the case. The Agent holds a current investigation context,
  plans the next step(s), calls tools, and answers with grounded evidence.

The Agent Client is **NOT a second Risk Platform**:

| Risk Platform owns | Agent Client owns |
|---|---|
| Risk detection (ML / rule / graph scoring) | Investigation orchestration |
| Evidence generation | Tool selection & sequencing |
| Signal, rule, graph computation | Multi-turn investigation context |
| Canonical finding names, threshold semantics | Planning |
| Policy-backed source data & validated citations | Artifacts (bundles, reports, notes) |

Design rule: **the Agent may compose, order, and present — it must never compute
or re-derive risk-domain facts.** Every claim in a response must trace back to a
Risk Platform response obtained through a logged tool call.

---

## 2. System Context

```
+---------------------------+        +------------------------------+
| Frontend (port 3001)      |        | Risk Platform (port 8000)    |
| - Case Intake             |        | - detection & scoring        |
| - Focus Mode dialog       |        | - evidence APIs              |
| - Context/Memory panel    |        | - explanation API            |
| - Task Center             |        | - policy / citation services |
+-------------+-------------+        +---------------+--------------+
              | HTTP                                 | HTTP
+-------------v--------------------------------------v--------------+
| Agent Client Backend (port 8001)                                  |
| Planner · Validator · Executor · Tool Registry · Risk Platform    |
| Adapter · Context/Memory · Artifact Service · Task Store          |
+-------------------------------------------------------------------+
```

The Risk Platform is an external authoritative service accessed over HTTP only.
No shared code, no shared filesystem dependencies.

---

## 3. Core Execution Loop (Week 1)

```
User Request
  → Context Resolution             ("What does this request refer to?")
  → Investigation Context          (resolved focus state for this turn)
  → Skill Selection                ("What kind of investigation work is appropriate?")
  → LLM Planner                    ("What steps should be performed, in what order?"
                                    — inside the selected skill's vocabulary only)
  → Structured Plan
  → Plan Validation                ("Is every step allowed and well-formed?")
  → Executor                       (runs tool calls, not the LLM)
  → Tool Calls
  → Grounding / Citation Validation
  → Response + Artifact
  → Suggested Follow-ups           ("What are useful next investigation actions?"
                                    — only at continuation points; see FOLLOW_UP_MODEL_V1)
  → Context Update                 ("What becomes the new focus after this turn?")
  → Task Log → Next Turn
```

A selected follow-up is simply the next turn's **User Request** — it re-enters
the loop at Context Resolution like any other request; there is no separate
execution path.

### 3.1 Stage questions (what each stage answers)

| Stage | Question |
|---|---|
| **Context Resolution** | "What does this user request refer to?" |
| Context | "What is currently in the investigation state?" |
| **Skill Selection** | "What kind of investigation work is appropriate?" |
| Planning | "What steps should be performed for this request?" |
| Execution | "Actually execute the selected tools." |
| **Context Update** | "What should become the new investigation focus after this turn?" |

(Context here = `InvestigationContext` plus the session transcript scope the
loop reads from — explicit, serializable state.)

Key properties:

1. **Context resolution before everything** — every turn begins by resolving
   what the request refers to (explicit UI selection, existing context,
   references like "this finding"/"that rule", or a clarification request).
   It never fabricates or silently guesses an ambiguous target (§4.0).
2. **Skill-scoped planning** — the LLM first selects a Skill from the Skill
   Registry (constrained by finding capabilities — see §6a), then plans only
   within that skill's allowed step vocabulary. It never invents tools,
   skills, or free-form actions.
3. **Constrained planning** — plan steps come from fixed registries (step +
   skill); any step outside the selected skill's vocabulary is rejected by
   plan validation.
4. **Deterministic gatekeeping** — a validator rejects malformed, disallowed,
   or under-contextualized plans *before* anything executes.
5. **Executor-controlled execution** — the executor, not the LLM, drives tool
   invocation and records results; capability/view-lock checks re-run at
   execution time.
6. **Bounded honesty** — unsupported question types, unsupported
   finding/view/skill combinations, insufficient evidence, *and unresolved
   ambiguity* produce explicit bounded responses (`evidence_missing`,
   clarification requests, next-step suggestions), never fabricated content.
7. **Turn-local planning with explicit context update** — planning covers the
   current investigation request/turn only; no long-horizon planner in Week 1.
   Each turn ends by explicitly updating the investigation context when the
   turn establishes/changes focus — context changes are always visible, never
   implicit drift.

---

## 4. Components

The architecture distinguishes eleven responsibilities:

### 4.0 Context Resolution (agent runtime responsibility)

> A first-class responsibility inside the Agent runtime — **not** a Tool,
> **not** a domain object, **not** an external service. There is deliberately
> no `context_resolution` tool.

**Responsibility**: determine the intended investigation target of the user
request from the request text and current UI/session context, before any
planning happens.

Resolves conversational references such as:

- "this finding" → focused finding
- "this event" → focused event
- "that rule" → the rule signal behind a referenced finding/event
- "those accounts" → the network/account set of the current case context

It must also support conversational turns when nothing is selected — for
example, case-level guidance requests that legitimately have no finding
target at all.

Resolution behavior per situation:

| Situation | Example | Resolution |
|---|---|---|
| A. Explicit UI selection of a finding | "Why was this flagged?" (F3 selected) | resolve to finding **F3** (`focus_source=user_selected`) |
| B. Explicit UI selection of an event | "Why did this event trigger?" (TE-005 selected) | resolve to event **TE-005** (`focus_source=user_selected`) |
| C. No selection, question needs none | "Which finding should I investigate first?" | remain at case level; do **not** require a finding selection |
| D. No focus + multiple candidates match | "Why did this event trigger?" (ambiguous) | do not guess; return an ambiguity state and request clarification |

Resolution priority (deterministic order):

1. **Explicit UI selection** (user_selected)
2. **Existing InvestigationContext** (a focus already established this session)
3. **Unambiguous reference/entity matching** (exactly one entity matches a
   resolved reference against known case entities)
4. **LLM-assisted resolution** when needed (only to interpret the request and
   match it against known entities — never to invent entities)
5. **Clarification** when ambiguity remains

Hard rules:

- Must never fabricate or silently guess an ambiguous target. Persistent
  ambiguity resolves to pattern D: bounded clarification request.
- Entity matching operates only over entities actually present in the case
  context (findings/events of this case) — no invented targets.
- When the intended target is resolved, it may update the InvestigationContext
  (with the appropriate `focus_source` — see §5).

**Implementation notes (Week 1 — `backend/app/context_resolution.py`)**:

- **Deterministic-first resolution**: explicit focus → existing context →
  conservative token-majority matching against known finding titles (and
  caller-supplied timeline events). Case-level questions
  ("Which finding should I investigate first?") short-circuit to an explicit
  `unchanged_case_level` outcome — no focus is required or invented.
- **LLM only when needed**: if deterministic resolution already resolved or
  classified the turn, no LLM call is made. Assist mode runs only for
  ambiguous conversational references with known candidates available.
- **Candidate-bounded resolution**: the LLM receives ONLY the known case
  findings as candidates and may echo back at most one listed `finding_id`
  (or `NONE`). User text is fenced as untrusted data; injected "new finding"
  ids cannot be selected because output is validated against the candidate set.
- **Ambiguity never becomes a guess**: multiple plausible targets produce a
  structured ambiguous outcome with candidate list + clarification message and
  a provably unmutated context deep-copy.
- **Explicit context mutation**: successful resolution changes exactly
  `focused_finding_id` / `focused_event_id` / `focus_source`; preferences,
  policy ids, and time windows are never touched.
- **focus_source semantics**: `user_selected` on explicit selection,
  `agent_resolved` for conversational/LLM resolution, `system_default`
  reserved for system-established defaults; enforced `null` whenever no
  finding/event focus exists.

### 4.1 Planner
- Receives the user request + current investigation context + the selected
  skill (§4.1a).
- Produces a **structured plan**: an ordered list of steps drawn from the
  **selected skill's** `planning_steps` vocabulary, itself drawn from the
  predefined step registry (e.g. `fetch_case`, `timeline`,
  `signal_explain`, `policy_lookup`, `artifact`).
- Uses constrained prompting; the allowed step vocabulary comes from the
  selected skill and the registries, never invented by the LLM.

**Implementation notes (Week 1 — `backend/app/planner_v2.py`)**:

- The LLM produces a **structured Plan**: it emits only `{skill_id, goal,
  steps[{type, reason}]}`. The LLM never outputs tool names or tool
  parameters.
- **Only eligible Skills are exposed**: the prompt contains the planning
  vocabularies of the caller-filtered eligible skills (skill_id, description,
  allowed step types, constraints) — never the unrestricted Tool Registry,
  and non-eligible skills are entirely absent from the prompt.
- Planner **outputs step types, not arbitrary tool names**; step → tool
  resolution (with parameter locks like `view="timeline"`) is applied
  deterministically from `STEP_TOOL_MAP` after parsing.
- The **Skill Registry resolves step → tool mapping**; no mapping exists in
  prompts or in LLM output space.
- The **Contract Checker validates the plan** (`check_plan`) as the final
  authoritative gate before a Plan is returned; eligibility of the selected
  skill is re-verified first.
- **Invalid plans are rejected, not silently replaced**: parse failures,
  unknown skill/step identifiers, ineligible selections, and contract
  violations all yield bounded structured `PlanningFailure`s (`LLM_OUTPUT_
  INVALID`, `SKILL_NOT_ELIGIBLE`, `STEP_NOT_ALLOWED`, `PLAN_CONTRACT_VIOLATION`,
  `NO_ELIGIBLE_SKILL`, `UNSUPPORTED_REQUEST`, `LLM_UNAVAILABLE`) — there is no
  generic fallback plan.
- **No tool execution occurs inside the Planner**: returned steps are always
  `PENDING`; execution belongs to the Executor alone.
- User text is untrusted: it is fenced as data in the user message and can
  never redefine skills, steps, tools, or parameter locks (rule 6 of the
  planning contract; verified by injection tests).

### 4.1a Skill Selection (constrained)
- Runs after context resolution: given the resolved investigation context
  (including any newly established focus) + focused finding capabilities,
  select a Skill from the Skill Registry.
- The LLM may choose only among skills *applicable* to the current context;
  capability-gated skills are offered only when their required capabilities
  are supported by the focused finding — or suppressed entirely for legitimate
  case-level turns (context-resolution outcome C). Selection + justification
  is logged.
- The selection bounds everything downstream: plan steps ⊆ skill vocabulary;
  tools ⊆ skill `allowed_tools`; parameter locks (e.g. `view=timeline`)
  propagate to validation and execution.
- See `SKILL_MODEL_V1.md` for the authoritative skill definitions.

### 4.2 Plan Validator (deterministic)
- Checks: selected skill exists in the Skill Registry; every step exists in
  the step registry AND within the selected skill's `planning_steps`;
  required arguments are resolvable from context (e.g. `case_id` known;
  `finding_id` must refer to a finding in session context); dependencies are
  ordered sensibly (case context exists before finding-scoped steps, unless
  already satisfied); plan length ≤ configured maximum.
- On invalid plan: reject, log the rejection, apply deterministic fallback
  (safe default plan, e.g. `fetch_case` alone) or a bounded error.

### 4.3 Executor
- Runs the validated plan step-by-step.
- Resolves step → tool + arguments, invokes tools, records each call
  (arguments, status, latency, output).
- May **skip** a step whose preconditions failed (reason logged); skipping is
  visible in the task log, never silent.
- Re-checks finding/view capability compatibility — and the skill's view/
  parameter locks — at execution time even if the validator passed the plan
  (defense in depth).

**Implementation notes (Week 1 — `backend/app/executor_v2.py`)**:

- **Runtime contract validation**: every plan re-passes `check_plan` before
  anything executes; validation failure ⇒ task failed with a structured
  error and ZERO tool executions (no fabricated ToolCallV2 records).
- **Only actual executions create ToolCall records**: a ToolCallV2 is created
  at invocation time with structured arguments (locked args from the plan +
  runtime-injected context arguments such as `case_id`), start/end timestamps,
  and the normalized result or execution error. Rejected steps produce no
  ToolCalls; unimplemented tools fail at the executor level without one.
- **ToolResult is normalized**: the recorded result is always the shared
  envelope — all five outcomes preserved verbatim (`empty`,
  `integration_error`, `unsupported`, `validation_error`, and success-with-
  `evidence_missing`). A required step not succeeding fails the task while
  its outcome stays distinct in the audit record.
- **Executor-level failure category**: a resolved tool missing from the
  current provider is `TOOL_NOT_IMPLEMENTED` — bounded, explicitly not
  `empty`, not RP `integration_error`, no fake results, no silent skip.
- **Tool provider boundary**: the executor knows only tool names and
  callables (`ToolProvider`); Risk Platform details live behind the Agent
  tool boundary. `risk_case_fetch` is the first wired tool; additional tools
  plug into the same boundary (`default_tool_provider`).
- **No autonomous retries in Week 1**: failed steps stay failed; retry is a
  later task/UI capability.
- Task status transitions (`executing`, terminal state) are persisted as they
  happen, so an investigation's progress is inspectable mid-flight.

### 4.4 Tool Registry
- Declares the Week 1 tool surface (Section 6) and the step-to-tool mapping.
- Declares each tool's input contract and output shape.
- Registry entries are one of the planner's allowed vocabularies — the
  planner cannot request anything not registered; per-skill subsets further
  narrow it (§4.1a).

### 4.4a Skill Registry
- Data-driven, static registry of Skill definitions (`SKILL_MODEL_V1.md`).
- Week 1 entries exactly: `case_intake`, `timeline_investigation`,
  `trade_investigation`.
- Provides each skill's `required_capabilities`, `allowed_tools`,
  `planning_steps`, and `constraints` for selection, plan validation,
  runtime enforcement, and UI action surfacing.
- No execution framework: nothing "runs a skill" as a unit — the Executor
  still runs steps/tools.

### 4.5 Risk Platform Adapter
- Thin HTTP adapter over Risk Platform endpoints.
- Typed error translation: timeout/network → unavailable; 401/403 → auth;
  404/other 4xx/5xx → request failure. Service unavailability is surfaced as
  such — never masked as "no findings".

**Implementation boundary (Week 1)**: `backend/app/adapters/risk_platform.py`
(adapter + normalization) and `backend/app/domain_tools.py` (tool boundary).
Used endpoints: `GET /api/risk/cases/{user_id}/evidence`,
`POST /api/risk/explain`. Flow: **Risk Platform Adapter → canonical Agent
domain objects (`Finding` with deterministic IDs + derived capabilities) →
normalized `ToolResult`**. RP's own empty-case semantics
(`risk_level=UNKNOWN`, `detected_at=null`) normalize to `outcome=empty`;
transport/auth/malformed failures to `outcome=integration_error`. Finding-ID
ordering source: the ordered authoritative `key_findings` from RP's
explanation response (F1..Fn), with any structured rule findings RP returned
but the explanation omitted appended after, in RP's rule order. Note:
domain tools live in a module (not an `app/tools/` package) because that
package name would shadow the superseded-era `app/tools.py` still imported by
legacy consumers.

### 4.6 Context / Memory (incl. Context Update)
- Maintains the **current investigation context** (Section 5) across turns of
  the active investigation session.
- **Context Update** (end-of-loop stage, §3): after a turn completes, applies
  the new investigation focus — e.g. a focus established or changed by this
  turn's resolution or results. Context changes are explicit: the updated
  context (and its `focus_source`) is what the next turn's context resolution
  reads; nothing mutates implicitly mid-loop.
- Persists **minimal preference memory** (Section 7).
- Supplies the focused finding's capabilities to skill selection and planning.
- Fully explicit and inspectable in the UI (Context/Memory panel).
- No long-term memory and no vector memory in Week 1.

### 4.7 Artifact Service
- Composes structured artifacts from tool outputs.
- Internal artifact model is structured; Week 1 renders **Markdown only**
  (Section 8).
- Enforces provenance: artifacts contain only data sourced from logged tool
  outputs. Composition allowed; invention forbidden.

### 4.8 Task Store
- Records the complete investigation trail (Section 9): plan, steps, tool
  calls, statuses, artifacts, timestamps, errors/fallbacks — including the
  selected skill per task/turn.
- Backs the Task Center UI and auditability requirements.

**Implementation notes (Week 1 — `backend/app/task_store_v2.py`)**:

- **Task logging is separate from planning**: the store persists the audit
  container (`TaskV2`) only; plans/steps live with the planner+executor,
  results inside ToolCall records. The legacy `store.py` is untouched (its
  schema encodes superseded semantics); V2 uses its own minimal SQLite table
  (`tasks_v2`: indexed columns + full JSON document) in a separate DB file —
  fully reloadable via `create / update / get / list_by_investigation`.
- Records are model-document JSON, so adding optional fields to `TaskV2`
  does not require schema migrations; query/index columns cover the common
  lookups (by task id, by investigation).

### 4.9 Suggested Follow-ups (selection responsibility)

Contextual next-step suggestions shown after the response to help the user
continue the investigation. Logical contract: `FOLLOW_UP_MODEL_V1.md`.
**Follow-up = next investigation entry point; Plan = concrete execution
plan** — the planner stays authoritative for what actually runs next turn.

- **Trigger conditions** — follow-ups appear only at useful continuation
  points, never after every message: `task_completed` (major task/artifact
  produced), `focus_changed` (user moved focus to a finding/event),
  `evidence_missing` (result carries evidence_missing / bounded fallback /
  unresolved state), `successful_tool_result` (a meaningful next action
  remains). Never shown during clarification/ambiguity, plain conversation
  without a structured result, or artifact editing/review.
- **Deterministic template selection** — Week 1 uses fixed candidate
  templates (finding-level and timeline-event-level categories in
  FOLLOW_UP_MODEL_V1 §3); no LLM-generated suggestion text, no ranking
  models.
- **Capability filtering** — the same sources of truth as everywhere else
  (Skill Registry + `FindingCapability`) filter candidates; capability rules
  are NOT duplicated in the frontend. The UI receives already-filtered
  follow-ups.
- **Context/scope filtering** — finding-level candidates require a focused
  finding; event-level candidates require focused finding + event; focus is
  never fabricated merely to show a suggestion.
- **Executable-only rule** — a follow-up is displayable only if its required
  capabilities are supported, its target skill and step exist, and the step
  resolves to an implemented tool/execution path. Aspirational candidates are
  omitted, never shown as dead buttons.
- **Maximum 3 suggestions**, deterministic ordering (direct next action →
  evidence/explanation → policy/artifact continuation; ties by template
  order).
- **No direct tool execution** — clicking a follow-up creates a normal
  structured user intent for the next turn, which enters the standard
  pipeline (Context Resolution → … → Task Log) and creates its own Task/
  Plan/ToolCall/Artifact records. Selecting a follow-up therefore starts a
  normal new investigation turn in the task log.
- **Week-1 implementation note** — **implemented** in
  `backend/app/followups.py` per FOLLOW_UP_MODEL_V1 §9: deterministic
  template tables (the aspirational candidates are absent rather than dead
  buttons), capability filtering from `FindingCapability` ids,
  context/scope gating on the investigation context, typed trigger reasons
  only, executable-only registry checks, contract-rank ordering, max 3, no
  LLM and no tool execution in selection. Not in scope: LLM-generated text,
  ranking/analytics, personalization (future extensions documented in
  FOLLOW_UP_MODEL_V1 §10).

---

## 5. Focus Mode & Investigation Context

**Focus Mode is a contextual investigation state, not a prerequisite for
conversation.** The user may:

1. start with case-level questions (no focus needed),
2. select a finding/event explicitly in the UI,
3. or ask a conversational question that causes the Agent to resolve and
   establish a focus via Context Resolution (§4.0).

The system must support all three interaction patterns. **UI selection is a
shortcut for establishing explicit context — not a requirement for chat
interaction.**

Focus Mode means the Agent holds a **current investigation context**:

```jsonc
{
  "case_id":            "U00299",   // always present in Focus Mode
  "focused_finding_id": "F3",       // finding under focus
  "focused_event_id":   null,       // optional; narrows to one event
  "focus_source":       "user_selected",
                                    // how the current focus was established:
                                    // user_selected | agent_resolved |
                                    // system_default; null when no focus
  "time_window":        null,       // optional; e.g. last 24h
  "policy_ids":         [],         // optional; policies pinned relevant
  "preferences": {                  // see Section 7
    "citation_required": true,
    "response_length":   "standard",
    "output_format":     "md"
  }
}
```

`focus_source` semantics (normative details in `DOMAIN_MODELS_V1.md §2.2`):

| Value | Meaning |
|---|---|
| `user_selected` | The user explicitly selected the finding/event in the UI or explicitly instructed the Agent to focus on it |
| `agent_resolved` | The Agent resolved the focus from conversational context/reference |
| `system_default` | The system established a default focus without explicit user selection (e.g. an initial case-level default) |

An `agent_resolved` focus is a working hypothesis, not authority: if
ambiguity existed at resolution time, the request stops at clarification
instead of setting an agent_resolved focus.

Focus Mode does **NOT** imply all findings support all tools, views, or
skills. Each finding exposes its **supported investigation capabilities**,
derived from what the Risk Platform actually provides as evidence for that
finding. Both the UI and the agent respect those capabilities:

- Skill selection (§4.1a) receives the capability hints for the focused
  finding: capability-gated skills are offered only when supported.
- Planner receives the selected skill's vocabulary plus capability hints.
- Executor validates requested view/type against finding capability — and
  skill parameter locks — returning explicit bounded responses for
  unsupported combinations.
- UI presents only capable actions per focused finding (see §6a for the
  skill-driven action surface).

**UI implication**: the UI may display the current case, current finding,
current event, and focus source. UI selection is a convenience shortcut, not
a chat prerequisite. Resolver internals (matching algorithms, LLM-assisted
resolution steps) are implementation details and must not be exposed in the
UI — only the resulting focus state (`focus_source` included).

**Follow-ups in Focus Mode**: at valid continuation points (§4.9), the UI
renders up to 3 follow-up chips below the response and/or in the focus
workspace. Chips are contextual actions — selecting one issues a new
structured investigation request through the normal pipeline; it does not
execute anything directly and does not mutate context by being displayed.
Capability/scope filtering happens server-side (same sources of truth as
Skills); the frontend only renders the already-filtered payload.

---

## 6. Tool Surface (Week 1)

Five tools. Smallest useful set; every tool exposes a genuine investigation
capability.

### 6.1 `risk_case_fetch(case_id)`
- **Solves**: "What is the authoritative picture of this case?"
- **Returns** Risk Platform explanation context verbatim: summary,
  key_findings (with `[n]` markers), recommended_action, citations
  (id/doc/section/quote/chunk_id), explanation_source, missing_info.
- **Reuse**: `POST /api/risk/explain`.
- Findings and citations are reproduced **as-is** — no renaming, re-scoring,
  re-validation, or policy-ID re-mapping.

### 6.2 `finding_drilldown(finding_id, view, top_n)`
- **Solves**: "Show me the detail behind this specific finding."
- **Week 1 views**:
  - `timeline` — chronological composition of relevant evidence events
  - `opposite_trades` — trades composing an opposite-trade signal
- **top_n** bounds result size.
- Views are **compositions** (filtering / ordering / bounding) of Risk
  Platform evidence data. They never recompute signals or thresholds.
- **Capability enforcement**: not every finding supports every view. Tool
  execution validates finding/view compatibility. Unsupported combinations
  return an explicit bounded response stating the combination is unsupported
  and listing supported views — never partial or fabricated results.

**Implementation status (Week 1)**: `view="timeline"` is **implemented**
(`backend/app/domain_tools/finding_drilldown.py`); `opposite_trades` remains
future work. Implementation notes:

- **Data source**: the existing `GET /api/risk/cases/{user_id}/evidence`
  (no new RP endpoints). Confirmed timestamp-bearing fields only:
  `TransactionEvidence.timestamp`, `WithdrawalEvidence.timestamp`,
  `RiskSummary.detected_at`. Network/device evidence exposes no timestamps
  and therefore contributes no events.
- **Canonical TimelineEvent normalization**: events are grounded in actual
  RP fields with deterministic human-readable templates (e.g. "Withdrawal of
  0.5 BTC to a newly encountered address"); every event keeps ≥1 real
  `evidence_refs`, mirrors only the finding's own `signal_refs`, and carries
  `policy_refs` only where the finding is actually cited. No synthetic
  timestamps/ids/references; an LLM never writes event text.
- **Deterministic ordering & IDs**: events sort by timestamp, then stable
  source/order/summary tie-breaking; IDs are assigned post-sort as
  `<finding_id>-E001…` — stable for the same source ordering.
- **Capability rules**: `timeline` events stream only when the finding
  declares the capability; a non-timeline finding yields
  `outcome=unsupported` (`CAPABILITY_NOT_SUPPORTED`), never an empty success.
- **Importance derivation (documented rule)**: (1) rule severity when a rule
  backs the finding (HIGH/CRITICAL→high, LOW→low); (2) explicit risk markers
  (new-address withdrawal → high); (3) otherwise `medium` as the conservative
  default.
- **Result semantics**: unknown `finding_id`/view/`top_n` →
  `validation_error`; timeline-capable finding with zero timestamped evidence
  → `empty`; RP failure → `integration_error`. `top_n` defaults 20, bounded
  to 100.

### 6.3 `signal_explain(finding_id, signal_type)`
- **Solves**: "Why did this finding get flagged?" for signal_type ∈
  `{ML, Rule, Graph}`.
- Explains **only from evidence actually available** from Risk Platform
  (triggered-rule data, feature values, scores, detection sources, narrative).
- **Never fabricates** unavailable attribution (e.g. SHAP-style feature
  contributions do not exist — none will be produced).
- If available evidence is insufficient to explain the signal, returns
  `evidence_missing` with `next_data_needed` describing exactly what data
  would enable the answer.

**Implementation status (Week 1)**: **implemented**
(`backend/app/domain_tools/signal_explain.py`); the
`explain_signal → signal_explain` planner → executor binding is now
executable. Implementation notes:

- **Data source**: the existing `GET /api/risk/cases/{user_id}/evidence`
  via the adapter — no new RP endpoints. Rule: `rule_evidence[]`
  (`rule_name`, `severity`, `description`, `trigger{}`, `threshold`,
  `contribution`) echoed verbatim — never reconstructed. ML:
  `risk_summary.ml_score` + `feature_evidence` values. Graph:
  `network_evidence` cluster/related-accounts/shared-devices.
- **Evidence boundaries**: RP exposes **no** per-transaction feature
  attribution (SHAP-style) and **no** graph relationship paths — both
  absences are reported via `evidence_missing: true` + `next_data_needed`
  on a `success` outcome (explicitly not errors, never synthesized).
- **Rule matching**: strict name anchoring (full rule name ⊆ finding title
  or vice versa) — no loose keyword overlap, so e.g. the coordinated-trading
  rule can never attach to the ML Pattern Detection finding.
- **Semantics**: unsupported capability → `unsupported`; unknown
  finding/invalid signal type → `validation_error`; RP failure →
  `integration_error`; rule-backed finding with no matching rule →
  `success + evidence_missing`. Structured payload only — narrative is the
  response composer's job.
- Wired into the default ToolProvider; `explain_finding` follow-up now
  resolves to an implemented path.

### 6.4 `policy_lookup(topic, finding_id)`
- **Solves**: "What policy governs / supports this aspect of the finding?"
- Returns policy passages relevant to `topic`, scoped to the finding's domain
  when `finding_id` is provided; citations carry doc/section/quote identifiers.
- **Reuse**: Risk Platform policy services. Risk Platform citations are
  authoritative; the Agent Client does not re-validate their semantics.

**Implementation status (Week 1)**: **implemented**
(`backend/app/domain_tools/policy_lookup.py`); the
`retrieve_policy → policy_lookup` binding is executable and the
`check_policy` follow-up is a real end-to-end path. Implementation notes:

- **RP policy boundary**: RP exposes **no dedicated policy-search endpoint**
  (its `PolicyRAGService` is internal). The only HTTP policy surface is the
  validated `citations[]` of `POST /api/risk/explain` — the same payload
  `risk_case_fetch` consumes, already finding-associated, semantically
  validated, and metadata-filtered by RP's own pipeline. **No local policy
  corpus copy, no new RAG, no new endpoint.**
- **Finding-aware, topic-ranked**: the tool resolves the canonical finding
  (exact ID, no fuzzy titles), then deterministically RANKS the case's
  authoritative citations against `topic` by keyword overlap over
  quote/section/doc text (a boundary-level presentation filter — `relevance`
  is an overlap count, never an RP score). Topic is untrusted content:
  echoed as data, never interpreted as instructions or identifiers.
- **Association partitioning**: the finding's own authoritative
  `policy_refs` are preserved as `associated_policy_refs`; citation matches
  not attached to the finding are reported as `newly_retrieved_refs` —
  retrieval results are never presented as pre-associated.
- **Citation grounding**: `citation_id`, `chunk_id`, `document`, `section`,
  `snippet` only from actual RP data; RP's citation numbering is preserved
  as-is; nothing synthesized. RP exposes no required-evidence checklist, so
  `required_evidence` is always `[]` (never invented).
- **Semantics**: zero matched citations → `empty`; RP failure →
  `integration_error` (malformed payloads bounded the same way); finding
  cites no policies → `success + evidence_missing` with `next_data_needed`
  (zero results alone is `empty`, never evidence_missing); unsupported
  capability → `unsupported`.
- Wired into the default ToolProvider; the executor injects
  `finding_id`/`case_id` from context and a deterministic finding-derived
  `topic` when the planner supplies none. Focus and preferences are never
  touched by the tool.

### 6.5 `artifact_bundle(scope, format="md")`
- **Solves**: "Package this investigation into a shareable artifact."
- Week 1 outputs **Markdown only** (`format="md"`); other formats are future
  scope (the argument exists in the contract so adding formats later does not
  change the investigation model).
- Content assembled strictly from tool outputs already in session/task scope.

**Implementation status (Week 1)**: **implemented**
(`backend/app/domain_tools/artifact_bundle.py`); the
`generate_artifact → artifact_bundle` binding is executable and the
`export_artifact` follow-up is a real end-to-end path. Implementation notes:

- **Agent-owned composition** — the tool contains no RP/HTTP logic, calls no
  LLM, and makes **no Risk Platform calls** when existing structured results
  suffice (pure composition of executed `ToolCallV2` results). Verified: no
  adapter imports in the module.
- **Source data**: only executed ToolResults of the current task, injected
  by the executor from its own audit trail (`source_tool_calls` argument —
  deep-copied records, artifact calls excluded so a bundle can never cite
  itself). No executed sources → `empty` with an explicit warning; never a
  fabricated artifact.
- **Scope semantics**: `case` (findings, evidence, signal/policy context,
  gaps) or `finding` (requires `finding_id`, validated against available
  case results; filters to that finding's results). Unknown scope →
  `validation_error`.
- **Deterministic rendering**: fixed section order (Case → Findings →
  Timeline → Signal Explanation → Policy References → Investigation
  Evidence → Evidence Gaps → Source Tool Calls); Markdown tables for
  timeline/policy; sha256 content-derived `artifact_id` (stable across
  processes); no UUIDs or execution timestamps inside content. Sections with
  no available data are omitted; `evidence_missing`/`next_data_needed` are
  rendered as explicit **Evidence Gaps** — never converted to success prose.
- **Provenance closure** (mandatory): `source_tool_calls` must be non-empty
  and reference actually-executed ToolCallV2 records; ArtifactV2 validation
  rejects empty provenance.
- **Task linkage**: the executor harvests the composed ArtifactV2 from the
  successful call payload and appends its ID to `TaskV2.artifact_ids`; the
  artifact-producing ToolCall itself is recorded normally
  (Task → Plan → ToolCall: artifact_bundle → Artifact: ART-…).
- **Scope derivation**: when the planner emits `generate_artifact` without
  parameters (by design), the executor derives scope deterministically from
  context — focused finding ⇒ `finding` scope, else `case`; format locked
  to `md`. The LLM never chooses formats or free-form artifact instructions.
### Cross-cutting capabilities (system-level, not tools)

- LLM planning (Section 4.1–4.2)
- Skill selection (Section 4.1a; definitions in `SKILL_MODEL_V1.md`)
- Session/task context (Sections 5, 7)
- Minimal preference memory (Section 7)
- Task logging (Section 9)
- Citation/grounding validation (Section 10)
- Deterministic fallback/error handling (Section 10)
- Suggested Follow-ups (Section 4.9; contract in `FOLLOW_UP_MODEL_V1.md`)

---

## 6a. Skills (Week 1)

Skills are **reusable investigation workflow definitions** — a lightweight,
first-class Agent concept between user intent and the tool layer.

> **Skill ≠ Capability ≠ Tool ≠ Plan.**
> Skill: "What kind of investigation work can the Agent perform?"
> Capability: "Is this investigation action valid for the current finding?"
> Tool: "How is a specific step executed?"
> Plan: "What should the Agent do for this specific user request / turn?"

Design principle: **Skill = reusable workflow definition. Tool = execution
primitive. Capability = runtime eligibility gate. Planning = per-request /
per-turn decision making.**

The full schema (`skill_id`, `name`, `description`,
`required_capabilities`, `allowed_tools`, `planning_steps`, `constraints`),
the three Week 1 skill definitions, and registry rules are normative in
`SKILL_MODEL_V1.md`. Summary:

| Skill | Required capability | Allowed tools (locked) |
|---|---|---|
| `case_intake` | none (case-level) | `risk_case_fetch`, `artifact_bundle` |
| `timeline_investigation` | `timeline` | `finding_drilldown(view=timeline)`, `signal_explain`, `policy_lookup`, `artifact_bundle` |
| `trade_investigation` | `opposite_trades` | `finding_drilldown(view=opposite_trades)`, `signal_explain`, `policy_lookup`, `artifact_bundle` |

Rules this introduces at the architecture level:

1. **Skill selection precedes planning** (§3, §4.1a): the LLM selects a
   skill from the registry under capability constraints, then plans only
   within that skill's step vocabulary.
2. **Capability gating**: a skill is *applicable* by intent but *executable*
   only when the focused finding supports its required capabilities.
   Example — finding F3 with capabilities `{timeline, signal_explain,
   policy_lookup}` → `timeline_investigation` available,
   `trade_investigation` unavailable.
3. **Vocabulary containment everywhere**: plan steps ⊆ skill vocabulary ⊆
   step registry; tools ⊆ skill `allowed_tools` ⊆ tool registry. Unsupported
   skill/tool combinations yield bounded `unsupported` results — never
   fabricated data.
4. **Registry, not framework**: skills are static declarative data consumed
   by selection/validation/UI; there is no skill execution engine.

**UI implication**: Focus Mode may surface available Skills/actions for the
focused finding, showing only those whose required capabilities that finding
supports (unavailable actions hidden or disabled with reason). The available-
actions list must be computed from the same sources of truth as planning
(skill registry × FindingCapability), not duplicated by hand in the frontend.

---

## 7. Context / Memory Specification

**Week 1 does NOT include long-term memory or vector memory.**

Implemented instead: minimal, explicit, inspectable state.

**Investigation context** (per session, updated explicitly by turns —
including by Context Resolution / Context Update, §4.0/§4.6):
`case_id`, `focused_finding_id`, `focused_event_id`, `focus_source`
(how the current focus was established), `time_window`,
selected `policy_ids` (if relevant), output preferences.

**Minimal preference memory** (three keys):
| Key | Values |
|---|---|
| `citation_required` | true / false |
| `response_length` | brief / standard / detailed |
| `output_format` | md |

Preferences influence presentation and grounding strictness only — never the
risk content itself.

All of the above is rendered in the UI as a **Context/Memory panel** so the
investigator can see (and correct) exactly what the Agent believes the current
investigation context is.

---

## 8. Artifacts

Week 1 artifact output format: **Markdown only**. The internal artifact model
remains **structured** (type + structured payload + rendering template) so new
formats can be added later without changing the investigation model.

Standard Week 1 artifact examples:

| Artifact | Composed from |
|---|---|
| Findings Summary | `risk_case_fetch` findings + citations |
| Timeline | `finding_drilldown(view=timeline)` |
| Signal Explanation | `signal_explain` output |
| Policy Requirements | `policy_lookup` results + citations |
| Action Checklist | recommended_action + finding-derived checklist |
| Investigation Notes | session context + turn history summaries |

Provenance rule stands: artifacts contain only data sourced from tool outputs
in the logged task trail.

---

## 9. Task Logging & Task Center

Every investigation records, as part of the product experience (visible in the
Task Center):

- investigation/task id
- plan (as produced by the planner, and as validated/executed)
- plan steps and their status (executed / skipped-with-reason / rejected)
- selected tool per step
- tool arguments (resolved)
- execution status per call (success / failed / fallback)
- artifact outputs produced
- timestamps (plan created, step start/end, task start/end)
- errors / fallbacks (including validator rejections and bounded responses)

Planning itself uses the same provider configuration as the rest of the agent;
each planning call is logged alongside executions so a full investigation can
be replayed from the task log.

**Follow-up selections are normal turns**: selecting a Suggested Follow-up
(§4.9) starts a standard new investigation turn and therefore produces its
own Task / Plan / ToolCall / Artifact records. There is no special logging
path for follow-up-originated turns — they are indistinguishable in the log
from free-form requests, which is what keeps follow-ups auditable.

---

## 10. Governance, Grounding & Error Handling

**Citation / grounding validation** (post-execution, pre-response):
- Every factual claim in the response must trace to a tool output in the
  current task.
- `[n]` citation markers in reused finding text must resolve to returned
  citations.
- Fabricated attribution is prohibited structurally (tools refuse to emit it)
  and defensively (grounding check strips/regenerates offending claims).

**Bounded response catalog** (expected, correct behaviors — not failures):
- Unsupported finding/view combination → bounded response naming supported views
- Insufficient signal evidence → `evidence_missing` + `next_data_needed`
- Risk Platform unreachable/auth-failed → typed error surfaced as service
  unavailability (never disguised as empty data)
- Invalid plan → validator rejection logged; deterministic fallback plan or
  bounded error; never silent improvisation

**Genuine emptiness vs integration failure**: `insufficient_evidence` is
returned only when the Risk Platform returns a valid, successful, empty
response.

---

## 11. Future Scope — Week 2 and Beyond (documented, DO NOT implement now)

- `network_drilldown` tool
- `explanation_regenerate` tool
- Richer investigation drilldowns (additional views/types)
- Richer artifact formats (PDF/HTML export paths on the structured model)
- Long-term memory (beyond the three preference keys)
- Multi-agent orchestration
- MCP integration

These are explicitly out of Week 1 scope; the Week 1 contracts above are shaped
so these extensions attach without breaking changes (registry growth, render
templates, memory backends).

---

## 11a. InvestigationService — One-Turn Orchestration (implementation)

`backend/app/investigation_service.py` is the **orchestration glue** that
drives one investigation turn through the pipeline of §3:

```
Context Resolution → eligible Skill calculation → LLM Planning →
Plan Validation (inside ExecutorV2) → Execution → Response composition →
Suggested Follow-ups → Context Update → Task persistence (TaskStoreV2)
```

Implementation properties:

- **Glue only** — the service never calls tools directly (execution is
  exclusively ExecutorV2's), performs no domain reasoning, creates no second
  task store, and never mutates the caller's context object (works on deep
  copies).
- **Bounded early exits** — ambiguous/unresolved context resolution stops the
  turn before planning (clarification response, unchanged context, zero
  ToolCalls, zero follow-ups); a `PlanningFailure` fails the task without
  executing anything.
- **Response composition** — a deterministic composer turns structured
  ToolResults into concise human-readable text: no invented facts/timestamps/
  entities, evidence + citation references preserved in the response,
  `evidence_missing` surfaced explicitly with `next_data_needed`,
  `empty` and `integration_error` worded as distinct outcomes.
- **Explicit context update** — the returned context is exactly the
  resolution's context: `agent_resolved` focus carries forward,
  `user_selected` focus is never silently changed, tool outputs never move
  focus, preferences/policy ids are untouched.
- **Follow-ups after structured results only** — the follow-up selector runs
  only when execution completed with a success/empty ToolResult (a real
  continuation point); failures and clarifications produce none. Selected
  follow-ups are inert intents — not executed by the service.
- **Task lifecycle** — one TaskV2 per turn persisted through
  `pending → planning → executing → completed/failed` with selected_skill,
  plan_id, tool_call_ids, timestamps, and typed errors.
- All collaborators (resolver / planner / executor / follow-up selector /
  task store) are injectable, enabling fully fake-driven tests.

---

## 12. Non-Goals (standing)

- No second detection engine; no local finding reconstruction
- No local policy corpus or RAG implementation
- No client-side semantic citation re-validation overriding Risk Platform
- No writes back into the Risk Platform from the agent loop
- No free-form autonomous planner
- No long-horizon planning in Week 1
- Skills: no multi-agent skills, skill marketplace, skill installation,
  dynamic code loading, long-term skill memory, complex skill inheritance,
  or autonomous skill creation (see `SKILL_MODEL_V1.md §7`)

---

## 13. Testing & Validation

**Week 1 E2E acceptance** (`backend/tests/test_investigation_e2e_v1.py`)
covers continuous multi-turn investigation — case intake → timeline → event
explanation → policy → artifact bundle as one session with stable
`investigation_id` — plus artifact provenance closure (artifact →
source_tool_calls → ToolCallV2 → ToolResult → refs), and the bounded
unsupported-capability and ambiguous-resolution paths (zero execution, zero
fabricated output). Only the LLM provider and Risk Platform adapter are
faked; Context Resolution, Skill Registry, Contract Checker, Planner,
Executor, InvestigationService, follow-up selection, and TaskStoreV2 run for
real.

---

## 13a. HTTP API Boundary (implementation)

**Implemented** (`backend/app/api/investigations.py` + `schemas.py`;
contract: `API_CONTRACT_V1.md`). Product-oriented HTTP boundary over the
runtime — mounted from `main.py` via `include_router` under `/api/v2`;
legacy `/api/tasks` endpoints remain untouched.

- **Endpoints**: `POST /api/v2/investigations`,
  `POST /api/v2/investigations/{id}/turn`,
  `GET /api/v2/investigations/{id}`, `GET /api/v2/tasks/{task_id}`,
  `GET /api/v2/tasks/{task_id}/artifacts`.
- **InvestigationService is the execution boundary**: the API never calls
  tools, PlannerV2, or ExecutorV2 directly.
- **Multi-turn continuity**: server-generated stable `INV-…`
  investigation_ids; the Investigation record and tasks persist in
  TaskStoreV2 (`investigations` table added to the same SQLite store — no
  second database); the evolved context and per-task plans/executions live
  in a documented in-process session map (Week 1 gap, restart-lossy; the
  HTTP contract is unaffected by later persistence).
- **Context handling**: the only client-writable surface is an explicit
  `context_action` (`focus_finding` / `focus_event` / `clear_focus`) which
  sets `focus_source=user_selected`; clients cannot mutate capabilities,
  findings, plans, tool calls, or task status, and cannot submit a raw
  context object.
- **Follow-up semantics**: `follow_up_id` is validated against the
  server-side registry and reconstructed into the canonical intent — the
  client never supplies tool/skill/argument names, and no direct
  follow-up-execution endpoint exists.
- **Bounded exposure**: task/turn responses carry plan step statuses and
  outcome-level tool-call summaries — no internal class names, no stack
  traces, no raw Risk Platform payloads. Errors use
  `{"detail": {"code", "message"}}` envelopes (404 not-found, 422 invalid
  input/follow-up, turn-level bounded failures in-body).
- **DI/testability**: `InvestigationAPI` instances own their
  service/store/findings-provider; tests inject fakes and reset the session
  map — no order-dependent global state.
- **Persistent investigation sessions** (Session persistence is a
  session-management feature, not long-term semantic memory):
  Browser UI → V2 API → TaskStoreV2 / SQLite → persistent investigation
  state. The Investigation record, current InvestigationContext, per-task
  plans, executed ToolCall records, and artifacts all persist in TaskStoreV2
  (`investigations` / `tasks_v2` / `investigation_sessions` tables, one DB
  file). The Week 1 process-local session map is gone. Conversation
  reconstruction is a read-model projection: reopening an investigation
  replays persisted tasks (task user_request as the user side, per-task
  plan/tool-calls as the agent side) — nothing is re-executed.
  **Refresh ≠ new investigation** (the client persists only the current
  `investigation_id` as navigation state and reloads from the API);
  **New Investigation is an explicit user action** that resets the
  workspace without deleting the previous session; history list and
  explicit confirmed deletion (`DELETE`) are provided. No search/ranking,
  summarization, accounts, or sync — that is future scope.
- **Frontend as API client** (`frontend/src/api/` + `components/investigation/` +
  `pages/InvestigationPage.tsx`): the V2 Investigation Workspace renders
  only the structured state returned by `/api/v2` — it never duplicates
  backend intelligence (skill selection, capability derivation, planning,
  tool selection, parameter locks, context resolution, follow-up
  eligibility, citation validation, provenance are all backend-owned).
  Findings/event selection submits supported `context_action`s; follow-up
  chips submit `follow_up_id`s; both re-enter the normal pipeline. UI built
  with the existing shadcn/ui + Tailwind setup (Card/Badge/Button/Tabs/
  ScrollArea/Sheet/Accordion/Skeleton composition — no new component
  library, no custom primitives).
- **Case Reference Resolution** (`app/case_resolution.py`) — the
  initial-request counterpart of Context Resolution, and deliberately
  distinct from it:

    INITIAL REQUEST → Case Reference Resolution → Investigation creation
    → Investigation Context → Context Resolution → Skill Selection
    → Planner → Executor

  It resolves the canonical `case_id` for a *new* investigation from raw
  input (`"00299"`, `"investigate case 00299"` → `"U00299"`) before the
  investigation exists; Context Resolution resolves finding/event
  references *after* investigation context exists. Deterministic regex
  only (no LLM, no fuzzy matching); bounded outcomes
  resolved/missing/ambiguous/invalid surfaced as 200/422/409 with
  clarification messages. Subsequent turns remain
  ContextResolver's responsibility — case parsing never enters it.

---

## 14. Related Documents

- **Skills contract (normative companion)**: `docs/architecture/SKILL_MODEL_V1.md`
- **Domain model contract**: `docs/architecture/DOMAIN_MODELS_V1.md`
- **Suggested Follow-ups contract**: `docs/architecture/FOLLOW_UP_MODEL_V1.md`
- **HTTP API contract**: `docs/architecture/API_CONTRACT_V1.md`
- Archived historical design material (non-authoritative): `docs/archive/README.md`
- Operational run instructions: `backend/README.md`

# Domain Models V1 — Week 1 Contract

**Status**: ACTIVE — normative contract for the next implementation step
**Date**: 2026-08-27
**Parent**: `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md`

> This document defines the domain model contract. It does NOT change any code.
> `backend/app/models.py` remains untouched until the implementation step that
> consumes this contract.

---

## 0. Summary of Core Objects

| # | Object | One-line definition |
|---|--------|---------------------|
| 1 | `Investigation` | One investigation session for one case |
| 2 | `InvestigationContext` | Explicit short-term memory / focus state |
| 3 | `Finding` | Unit of investigation focus |
| 4 | `FindingCapability` | What investigation actions are valid for a finding |
| 5 | `TimelineEvent` | One event within a finding's timeline view |
| 6 | `Plan` | LLM-generated, structured, validated-then-executed plan for one turn |
| 7 | `PlanStep` | One step of a plan |
| 8 | `ToolCall` | Actual tool execution request (audit record) |
| 9 | `ToolResult` | Normalized result envelope for tool executions |
| 10 | `Artifact` | Reusable grounded output (Week 1: Markdown) |
| 11 | `Task` | Auditable unit of work shown in Task Center |

Explicitly **NOT** part of this model: vector memory, embeddings,
long-term memory, hidden context, autonomous goal trees.

---

## 1. Relationships

```
Investigation (1 per case session)
├── InvestigationContext          (current focus + preferences; inspectable)
├── Findings[]                    (derived from risk_case_fetch, stable IDs)
│     └── FindingCapabilities     (per-finding: which actions are valid)
├── Tasks[]
│     ├── Plan                    (one plan per task/turn)
│     │     └── PlanSteps[]       (ordered, registry-constrained)
│     ├── ToolCalls[]             (execution audit trail)
│     └── Artifacts[]             (produced outputs w/ provenance)
└── conversation turns            (user/assistant messages; each turn may
                                  reference the Task it triggered)

Finding
└── Capabilities
      ├── timeline           (present/absent per finding)
      ├── opposite_trades    (present/absent per finding)
      ├── signal_explain     (present/absent per finding)
      └── policy_lookup      (present/absent per finding)

InvestigationContext
└── focused_finding_id  →  Finding.finding_id
      │                     (focus_source records how this focus was
      │                      established: user_selected / agent_resolved /
      │                      system_default)
      └── focused_event_id  →  TimelineEvent.event_id
```

Linkage rules:

- `Task.investigation_id` → owning Investigation; `Task.plan_id` → its Plan;
  `Task.tool_call_ids` / `Task.artifact_ids` index what happened.
- `Plan.investigation_id` allows querying plans without going through a Task;
  the primary audit path is still `Task → plan_id`.
- `ToolCall.task_id` + `ToolCall.investigation_id` both set (denormalized for
  audit queries).
- Conversation turns are stored as an append-only transcript on the
  Investigation. A dedicated `Turn` domain object is deliberately **deferred**
  — Week 1 turns are transcript entries that may carry the `task_id` they
  produced.

---

## 2. Core Objects

Conventions: all timestamps are ISO-8601 UTC strings. All IDs are strings.
All objects are serializable (Pydantic-compatible). Optional fields are
explicitly marked.

### 2.1 Investigation

Represents one investigation session for one case. Do not overdesign.

```jsonc
{
  "investigation_id": "INV-...",
  "case_id": "U00299",
  "status": "active",            // active | completed | failed
  "created_at": "...",           // ISO-8601 UTC
  "updated_at": "..."            // ISO-8601 UTC
}
```

- Exactly one active Investigation per open case session (enforcement strategy
  is an implementation decision, not part of this contract).
- `status` transitions: `active → completed`, `active → failed`.
  Failed investigations remain inspectable.

### 2.2 InvestigationContext

Explicit short-term memory. Serializable, inspectable, rendered verbatim in
the UI Context/Memory panel. No hidden state.

```jsonc
{
  "case_id": "U00299",
  "focused_finding_id": "F3",        // optional, nullable
  "focused_event_id": "EVT-...",     // optional, nullable
  "focus_source": "user_selected",   // optional, nullable — how the current
                                     // focus was established:
                                     // "user_selected" | "agent_resolved" |
                                     // "system_default"; null when there is
                                     // no focused finding/event
  "time_window": null,               // optional, nullable
  "selected_policy_ids": [],         // optional list, default []
  "preferences": {
    "citation_required": true,       // bool
    "response_length": "standard",   // brief | standard | detailed
    "output_format": "md"            // Week 1 fixed: "md"
  }
}
```

**`focus_source` semantics** (applies to the current focus state):

| Value | Meaning |
|---|---|
| `user_selected` | The user explicitly selected the finding/event in the UI or explicitly instructed the Agent to focus on it |
| `agent_resolved` | The Agent resolved the focus from conversational context/reference (Context Resolution, ARCHITECTURE_V2 §4.0) |
| `system_default` | The system established a default focus without an explicit user selection (e.g. an initial case-level default) |

Behavior rules:

- `focus_source` may be **null** when there is no focused finding/event.
- `focus_source` is provenance about *how* the current focus was established;
  it does not make an Agent-resolved focus authoritative. If ambiguity exists
  at resolution time, resolution stops at clarification — ambiguity never
  resolves into an `agent_resolved` focus.
- When `focused_finding_id` or `focused_event_id` changes through explicit
  user action, update `focus_source` accordingly (to `user_selected`);
  likewise, any non-user path that establishes or changes focus sets its own
  corresponding source value — the two always move together.
- Setting `focus_source` while leaving both focus ids null is invalid.

Invariants:

- All fields are plain JSON — inspectable and exportable.
- There is no memory outside this object. Anything the agent "knows" is either
  here or derivable from the task log.
- `focused_event_id` requires `focused_finding_id` to be set (an event belongs
  to a finding).
- Referential integrity: `focused_finding_id` must resolve to a Finding in the
  current investigation; `focused_event_id` must resolve to a TimelineEvent of
  that finding. Invalid references are surfaced to the user, never silently
  cleared.

### 2.3 Finding

The unit of investigation focus. Findings are **derived deterministically from
the authoritative `risk_case_fetch` response** — the Agent Client never
generates, renames, re-scores, or re-classifies them. The canonical finding
taxonomy is owned by the Risk Platform.

```jsonc
{
  "finding_id": "F3",               // stable within the investigation/session
  "case_id": "U00299",
  "type": "rule_signal",            // OPEN string — see note below
  "title": "Coordinated Trading Pattern",
  "severity": "high",               // low | medium | high | critical | unknown
  "summary": "An opposite-trade ratio of 45.24% exceeded the 40% threshold...",
  "evidence_refs": [                // pointers to underlying evidence items
    { "kind": "withdrawal", "id": "WD001" },
    { "kind": "trade", "id": "TX00123" }
  ],
  "signal_refs": [                  // detection signals behind the finding
    { "signal_type": "Rule", "name": "coordinated_trading_rule" },
    { "signal_type": "ML",   "name": "ml_score" }
  ],
  "policy_refs": [                  // citation identifiers if cited
    { "citation_id": 1, "chunk_id": "AML_Suspicious_Indicators#2.1#001" }
  ],
  "capabilities": /* FindingCapability, see §2.4 */,
  "ext": {}                         // OPTIONAL extension bag — see note below
}
```

Notes:

- **`type` is deliberately an open string**, sourced from Risk Platform
  classification (`ml_signal`, `rule_signal`, `graph_signal`, feature-derived,
  etc.). Do NOT close this enum client-side; the Risk Platform may add finding
  kinds without Agent Client releases. Code may branch on known values but
  must tolerate unknowns.
- **Extensibility**: finding variants carry different relevant facts (an ML
  finding has a score; a graph finding has link counts). Rather than forcing
  every fact into first-class columns, domain-specific attributes go into the
  optional `ext` bag and/or are referenced through `signal_refs` /
  `evidence_refs`. First-class fields above are the guaranteed common core.
- **ID stability**: `finding_id` values must be stable across turns within an
  investigation so context, plans, and artifacts can reference them. Derivation
  must therefore be deterministic from the same fetch response.
- `policy_refs` mirrors only citations actually returned by the Risk Platform.
  No client-side policy assignment ever occurs.

### 2.4 FindingCapability

Defines what investigation actions are valid for a specific finding. This is
the mechanism that prevents "every finding supports everything".

Capability vocabulary (closed enum, Week 1):

```
timeline | opposite_trades | signal_explain | policy_lookup
```

Representation on a Finding — explicit per-capability flags, not a bare list:

```jsonc
// Example from the brief — Finding F3:
{
  "timeline":        true,
  "opposite_trades": false,     // unsupported for this finding
  "signal_explain":  true,
  "policy_lookup":   true
}
```

Rules:

- Absence/false means **unsupported**, not "empty".
- Every finding must declare all four keys (explicit beats inferred).
- Capabilities are computed **only from evidence actually returned by the Risk
  Platform** for that finding (e.g. `opposite_trades` requires trade-composition
  data; `timeline` requires multiple timestamped evidence items).
- A capability value of `true` is a promise: the corresponding drilldown will
  succeed or return `empty` — never `unsupported`.
- Unknown future capability requests against an older finding snapshot resolve
  to `unsupported`.

### 2.5 TimelineEvent

One event in a finding's timeline composition. Raw payloads stay thin in
Week 1 — depth comes from `evidence_refs`, not inline blobs.

```jsonc
{
  "event_id": "EVT-...",          // unique within the investigation
  "finding_id": "F3",             // parent finding
  "timestamp": "...",             // ISO-8601 UTC; drives ordering
  "event_type": "withdrawal",     // open string (transaction, withdrawal,
                                  // risk_factor, ...)
  "summary": "Withdrawal: 0.5 BTC to new address 0xabc...",
  "evidence_refs": [{ "kind": "withdrawal", "id": "WD001" }],
  "signal_refs":  [],
  "policy_refs":  [],
  "importance": "medium"          // high | medium | low | null (null = not rated)
}
```

- Supports event-level focus: `InvestigationContext.focused_event_id`
  references `event_id` here.
- Events are compositions (filter/order/bound) of Risk Platform evidence —
  never recomputed signals.
- Do not embed raw Risk Platform record dumps; reference them.

### 2.6 Plan

An LLM-generated structured plan for the current investigation request/turn.
Real LLM planning, but constrained: the step vocabulary comes from the
step/capability registry; the LLM selects and orders — it does not invent.

```jsonc
{
  "plan_id": "PLAN-...",
  "investigation_id": "INV-...",
  "goal": "Explain why F3 was flagged",       // short statement of intent
  "steps": [ /* PlanStep[], ordered */ ],
  "status": "validated",                      // draft | validated | rejected |
                                              // executing | executed | failed
  "created_at": "...",
  "plan_version": 1                            // optional; supports per-turn
                                               // regeneration/versioning
}
```

- Created with `status=draft`; the deterministic validator moves it to
  `validated` or `rejected` (with reasons recorded on the failing step /
  plan audit entry). Only `validated` plans reach the executor.
- Auditable end-to-end: the produced plan and the executed reality are both in
  the task log (a rejected plan is also logged).
- Versionable per turn via `plan_version` if a plan is regenerated within a
  task lifecycle.
- Example (from the architecture doc): user asks "Investigate case U00299"
  → `[fetch_case, timeline, signal_explain, policy_lookup, artifact]`;
  "Why was F3 flagged?" → `[signal_explain, artifact]`.

### 2.7 PlanStep

One step of a plan.

```jsonc
{
  "step_id": "STEP-...",
  "type": "signal_explain",       // MUST be from the step registry — see below
  "description": "Why was F3 flagged? Explain Rule/ML signals.",
                  // description / reason (why this step was planned)
  "status": "success",            // pending | running | success | failed |
                                  // skipped | rejected
  "depends_on": ["STEP-..."],     // optional list of prerequisite step_ids
  "tool_name": "signal_explain",  // optional; resolved from registry; set at
                                  // execution time (planner may omit)
  "arguments": {},                // optional structured object
  "started_at": "...",            // ISO-8601 UTC
  "completed_at": "...",
  "error": null                   // optional; failure/validator-rejection detail
}
```

Allowed step types come from the **step registry**, never arbitrary strings
invented by the model. Week 1 registry:

```
fetch_case | timeline | opposite_trades | signal_explain | policy_lookup | artifact
```

(The registry maps step types to tools and expected arguments; it lives next to
the tool definitions and is the planner's allowed vocabulary.)

Additional rules:

- `rejected` covers validator rejection at plan level; `skipped` covers
  execution-time precondition failures (e.g. dependency failed) — skipping is
  always logged with a reason in `error`.
- Timestamps on steps that did not run may be null.
- Every step maps to at most one `ToolCall` (pure validator steps would be
  policy violations in Week 1: every executed step calls a tool or composes an
  artifact from prior results).

### 2.8 ToolCall

Actual tool execution request — execution-level audit data, **not** planning
data. Planning questions live on Plan/PlanStep; "what actually ran with what
args" lives here.

```jsonc
{
  "tool_call_id": "TC-...",
  "investigation_id": "INV-...",   // denormalized for audit queries
  "task_id": "TASK-...",           // denormalized for audit queries
  "tool_name": "risk_case_fetch",  // must exist in TOOL_REGISTRY
  "arguments": {},
  "status": "success",             // running | success | failed
  "started_at": "...",
  "completed_at": "...",
  "error": null,                   // optional; typed error summary
  "result": /* ToolResult, §2.9 */
}
```

Note: `result` holding the normalized ToolResult envelope replaces the legacy
raw-dict `output` field concept.

### 2.9 ToolResult

Normalized result envelope. Every tool returns exactly this shape — **raw Risk
Platform response shapes must not leak into the Agent domain model**. Adapter
mapping happens once, inside the tool/adapter layer.

The `outcome` discrimination is critical:

| Outcome | Meaning | Example |
|---|---|---|
| `success` | Data retrieved / composed fine | timeline built, citations found |
| `unsupported` | Requested action invalid for target | opposite_trades on a finding without that capability |
| `empty` | Valid request, genuinely nothing there | "No timeline events exist for F5" |
| `integration_error` | External system failure | Risk Platform down / timeout / 500 |
| `validation_error` | Bad input | missing finding_id, malformed time_window |

> "No timeline events exist" (`outcome=empty`) must NEVER be represented the
> same way as "Risk Platform timeline API failed" (`outcome=integration_error`).

```jsonc
{
  "outcome": "success",            // success | unsupported | empty |
                                   // integration_error | validation_error
  "data": /* payload shaped by the tool contract */,   // null unless success/empty-appropriate
  "evidence_refs": [],             // references backing any factual content
  "citation_refs": [],             // citation ids used/returned
  "warnings": [],                  // non-fatal observations
  "error": null,                   // { "code": "...", "message": "..." }
  "next_data_needed": null         // populated with outcome=evidence_missing
                                   // situations (see below)
}
```

Bounded-response pattern — insufficient evidence: when a tool cannot answer
because the *available* evidence is insufficient (distinct from the five
outcomes above: the call succeeded, but the data is inadequate), return
`outcome="success"` with `data.evidence_missing=true` and
`next_data_needed` describing exactly what additional Risk Platform data would
enable the answer. Fabricating attribution is prohibited in all cases.

Downstream rules (consumed by response/artifact layers):

- `unsupported` → bounded response naming supported alternatives. Never
  fabricated results, never silent.
- `empty` → honest "nothing found" answer.
- `integration_error` → service-unavailability messaging. Never rendered as
  "no findings".
- `validation_error` → surfaced as caller-fixable input problems.

### 2.10 Artifact

Reusable grounded output. Week 1 render format: Markdown only; internal model
stays structured so other formats attach later.

```jsonc
{
  "artifact_id": "ART-...",
  "investigation_id": "INV-...",
  "task_id": "TASK-...",
  "scope": "finding:F3",           // e.g. "case", "case:U00299", "finding:F3"
  "artifact_type": "findings_summary",
                                    // Week 1: findings_summary | timeline |
                                    // signal_explanation | policy_requirements |
                                    // action_checklist | investigation_notes
  "format": "md",                   // Week 1 fixed: "md"
  "title": "Findings Summary — U00299",
  "content": "# ...markdown body...",     // rendered content (or storage_ref)
  "storage_ref": null,              // optional external pointer when content
                                    // isn't inline
  "source_tool_calls": ["TC-...", "TC-..."],  // provenance, mandatory
  "created_at": "..."
}
```

Invariant: `content` may be composed only from `ToolResult`s of the listed
`source_tool_calls`. Provenance must resolve — artifacts with dangling
references are invalid and rejected by the artifact service.

### 2.11 Task

Auditable unit of work shown in Task Center. One user request that entered the
loop ⇒ typically one Task.

```jsonc
{
  "task_id": "TASK-...",
  "investigation_id": "INV-...",
  "type": "investigation_turn",    // open string; Week 1 uses
                                   // "investigation_turn"
  "status": "completed",           // pending | planning | executing |
                                   // completed | failed | cancelled
  "user_request": "Why was F3 flagged?",
  "plan_id": "PLAN-...",
  "tool_call_ids": ["TC-..."],
  "artifact_ids": ["ART-..."],
  "started_at": "...",
  "completed_at": "...",
  "error": null
}
```

Status semantics:

| Status | Meaning |
|---|---|
| `pending` | accepted, planning not started |
| `planning` | planner working (incl. plan validation) |
| `executing` | validated plan under execution |
| `completed` | response/artifact produced |
| `failed` | unrecoverable failure (typed error retained) |
| `cancelled` | user/system cancellation mid-flight |

---

## 3. Architecture Distinctions

Each object answers exactly one question:

| Question | Owner object(s) |
|---|---|
| "What does this user request refer to?" | **Context Resolution** — runtime responsibility (ARCHITECTURE_V2 §4.0), no domain object; result expressed by updating `InvestigationContext` (+ `focus_source`) |
| "What is currently in the investigation state?" | **Context** — `InvestigationContext` (+ `Finding`, `FindingCapability`, `TimelineEvent` as the knowledge substrate) |
| "What kind of investigation work is appropriate?" | **Skill Selection** — see `SKILL_MODEL_V1.md`; selection recorded in task log / Plan metadata at implementation time |
| "What should happen next?"/"What steps for this request?" | **Planning** — `Plan`, `PlanStep` (LLM proposes; validator approves; executor never trusts unvalidated plans) |
| "Actually execute the selected tools." | **Execution** — `ToolCall`, `ToolResult` (+ executor control flow; the LLM never executes anything directly) |
| "What should become the new investigation focus after this turn?" | **Context Update** — end-of-loop stage applying resolved focus back into `InvestigationContext` (ARCHITECTURE_V2 §4.6) |
| "What work happened and what is its status?" | **Task** — `Task` (+ status enums, timestamps, error fields) |
| "What reusable output was produced?" | **Artifact** — `Artifact` with mandatory provenance |
| "What did the external capability actually return?" | **ToolResult** — normalized envelope with discriminated outcomes |

Anti-pattern guardrails baked into the separation:

- The same textual result ("nothing found") appears differently depending on
  where it originates: planner reasoning may say "timeline not needed";
  ToolResult must say precisely `empty` vs `unsupported` vs
  `integration_error`.
- PlanSteps may be skipped; ToolCalls record facts. An executor skip is always
  mirrored in task logging.
- Context influences planning; planning never mutates risk-domain facts in
  context (e.g. the planner cannot edit a Finding).
- Context Resolution may write focus (`focused_finding_id` /
  `focused_event_id` + `focus_source`) into the context — that is its only
  mutation — and only when unambiguous. Ambiguity produces a clarification,
  never a guess.

---

## 4. Validation Rules (invariants)

1. **Capability-gated execution** — A tool backed by a finding-scoped
   capability (`timeline`, `opposite_trades`, `signal_explain`,
   `policy_lookup`) cannot execute if the current finding does not declare
   that capability. Enforced twice: at plan validation (reject/skip early)
   and again at execution time (runtime revalidation — defense in depth).
2. **No unvalidated execution** — Planner output must pass deterministic plan
   validation (registry membership, argument resolvability from context,
   dependency sanity, length limits) before any execution begins.
3. **Runtime revalidation** — Even a validated plan's steps are re-checked at
   execution time (context may have shifted between validation and execution).
4. **Failure ≠ absence** — A tool failure must never be represented as "no
   findings". `integration_error` stays distinct from `empty` all the way to
   the UI.
5. **Unsupported ≠ empty success** — An unsupported capability must never be
   returned as `outcome=success` with empty data. It is `outcome=unsupported`
   with the supported-alternatives listing.
6. **Provenance closure** — Artifact provenance must trace to actual tool
   calls in the same task; artifact services reject artifacts whose
   `source_tool_calls` don't resolve or whose content exceeds what those
   results support.
7. **Total auditability** — Every plan (draft, validated, rejected) and every
   tool execution is task-auditable via the Task Store: plan text, resolved
   arguments, statuses, timestamps, errors/fallbacks.
8. **Explicit context** — Context must be explicit and serializable
   (§2.2). Nothing enters agent reasoning except context, transcript, and
   tool results.
9. **No hidden memory** — No long-term/vector/hidden memory in Week 1.
   Cross-request persistence is limited to the persisted Investigation/
   Task/artifact records themselves and the three preference keys.

---

## 5. Migration Table

Existing concepts from `backend/app/models.py` (and adjacent runtime files)
mapped to the V1 contract. **None of this migration happens now** — this is
the contract for the upcoming implementation step.

| Existing model / concept (location) | Verdict | Becomes / Notes |
|---|---|---|
| `Task` (models.py) | **Adapt** | Keeps id/case/status/user_intent(→`user_request`)/steps-index/artifacts-index/timestamps/error. Gains `investigation_id`, `type`, `plan_id`. Loses `execution_mode`, `acceptance_status`, embedded `steps`/`artifacts` (become indexed IDs). Status enum rebased (below). |
| `TaskStatus` (models.py) | **Adapt** | `{DRAFT, READY, RUNNING, COMPLETED, FAILED, CANCELLED}` → `{pending, planning, executing, completed, failed, cancelled}` (DRAFT+READY merge into `pending`; RUNNING splits into `planning`/`executing`). |
| `Step` (models.py) | **Adapt → rename** | Becomes `PlanStep`: drops decorative `name`, adds `type` (registry-constrained), `depends_on`, `reason-bearing description kept`. Timestamp/error fields carried over. |
| `StepStatus` (models.py) | **Reuse as-is (values)** | `{pending, running, success, failed, skipped}` carries over onto PlanStep; add `rejected` (validator-level). |
| `ToolCall` (models.py) | **Reuse as-is (shape)** + small adaptation | Field set already matches audit purpose (id/tool_name/args/status/timestamps/error). Adds `task_id`/`investigation_id` linkage; raw-dict `output` field replaced by normalized `ToolResult`. |
| `ToolCallStatus` (models.py) | **Reuse as-is** | `{running, success, failed}` stands. Finer diagnosis lives in `ToolResult.outcome`, not on the call status. |
| `Artifact` (models.py) | **Adapt** | Keeps id/title/data/payload idea. Gains `scope`, `format` (=md), `source_tool_calls` provenance, `investigation_id`/`task_id` links. `ArtifactType` enum rebased (below). `created_at` preserved. |
| `ArtifactType` (models.py) | **Adapt** | `{findings, actions, citations, narrative}` → `{findings_summary, timeline, signal_explanation, policy_requirements, action_checklist, investigation_notes}`, open to extension. Old narrative/actions dissolve into these categories. |
| `ExecutionMode` (models.py) | **Obsolete** | AGENT-vs-DETERMINISTIC dualism no longer exists: one constrained-planner pipeline. Remove with legacy stores/columns during migration step. |
| `acceptance_status` (models.py, Task) | **Obsolete** | No consumer in V2 design; superseded by Plan validation + ToolResult outcomes. |
| `ProvenanceType` (models.py) | **Obsolete** | Belonged to the local finding-reconstruction/`compose_structured_result` era. Provenance is now structural: `evidence_refs`, `signal_refs`, `citation_refs`, `source_tool_calls`. |
| LLM free-form loop decision (`ToolRequest`, `AgentDecision`, agent_protocol.py) | **Replace** | The `{action, tool_request}` ping-pong decision is superseded by validated structured `Plan`/`PlanStep`. The "LLM never executes directly" principle survives — enforced by plan validation + executor instead of parse-time checks alone. |
| Hardcoded static planner (`create_investigation_plan`, planner.py) | **Replace** | Static 4-step script over legacy tools → real constrained LLM planning against the step registry (deterministic validator retained alongside). |
| Deterministic runner loop (`execute_step`/`run_task`, runner.py) | **Replace** | Concept survives as the Executor component; mechanics rewritten around Plan→ToolCall→ToolResult rather than mutating Task.steps in place. |
| Runtime context dict passed through agent loop (`runtime_context`) | **Adapt** | Informal dict formalized as `InvestigationContext` + explicit per-task accumulated tool-result scope. |
| Findings as raw strings (`key_findings`) / reconstructed `unified_findings` in adapter code | **Adapt** | Authoritative strings stay verbatim as source material, promoted into `Finding` domain objects with stable IDs + declared capabilities. Local reconstruction/deduplication logic (normalizer legacy paths) retires with Phase-2 cleanup. |
| Legacy tools (`policy_search` mock, `evidence_fetch`, `compose_structured_result`, `citation_validate` policy-ID check) | **Obsolete** | Outside this model's tool surface; their persistence is transitional runtime cleanup, unrelated to the V1 model contract. |
| `TaskStore` / SQLite schema (`execution_mode`, `acceptance_status` columns) | **Adapt** (persistence layer) | Storage evolves with the model: investigation/context/plan/result records added; two dead columns retired. Mapping choices made at implementation time. |

**New objects with no existing counterpart**: `Investigation`,
`InvestigationContext`, `Finding` (as a first-class object),
`FindingCapability`, `TimelineEvent`, `Plan`, `ToolResult`.

Count for the record: **Reuse as-is ≈ 3** (ToolCall shape, ToolCallStatus,
StepStatus values) · **Adapt ≈ 7** · **Replace ≈ 3** · **Obsolete ≈ 5** ·
**New ≈ 7**.

### Implementation naming note (coexistence period)

Because the superseded-era `ToolCall`, `Artifact`, and `Task` classes in
`backend/app/models.py` are still imported by legacy consumers
(`store.py`, `runner.py`, `planner.py`, `main.py`), and Python import binding
means redefining those names would silently switch those consumers onto new
classes, the V2 runtime records are implemented during the coexistence period
as:

- `ToolCallV2` (= contract `ToolCall`)
- `ArtifactV2` (= contract `Artifact`; enum `ArtifactTypeV2` = contract artifact types)
- `TaskV2` (= contract `Task`; enum `TaskStatusV2` = contract task statuses)
- `ToolCallStatusV2` (= contract tool-call statuses)

`Plan` and `PlanStep` had no name collisions and carry their contract names
as-is (enums `PlanStatus`, `PlanStepStatus`). **New code must use only the V2
variants.** The suffixes are temporary mechanical aliases for this migration —
the architectural contract objects are exactly these; no semantic difference
is implied by the suffix, and it disappears when the superseded trio is
removed.

---

## 6. Out of Scope / Deferred

- `Turn` as a first-class object (transcript entries suffice for Week 1)
- Persistence schema details (implementation decision)
- Capability auto-discovery algorithms (interface contract only)
- Week 2 objects: network_drilldown views, regenerate flows, richer artifact
  formats, long-term memory — see ARCHITECTURE_V2 §11

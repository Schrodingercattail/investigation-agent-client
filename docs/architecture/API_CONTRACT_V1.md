# HTTP API Contract V1 — V2 Investigation Runtime

**Status**: ACTIVE — Week 1 HTTP boundary over the Agent runtime
**Date**: 2026-08-28
**Parent**: `AGENT_CLIENT_ARCHITECTURE_V2.md`

> The frontend talks to investigations/turns/tasks/artifacts. It never sees
> PlannerV2, Contract Checker, ExecutorV2, ToolProvider, the RP Adapter, or
> TaskStoreV2 internals. Base prefix: `/api/v2` (coexists with the legacy
> `/api/tasks` endpoints, which remain untouched).

---

## 1. Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/investigations` | Create an investigation session for a case |
| GET | `/api/v2/investigations` | Investigation history (most recently updated first) |
| POST | `/api/v2/investigations/{investigation_id}/turn` | Run one investigation turn (primary Agent endpoint) |
| GET | `/api/v2/investigations/{investigation_id}` | Current investigation state (context + tasks) |
| DELETE | `/api/v2/investigations/{investigation_id}` | Explicitly delete an investigation and its stored history |
| GET | `/api/v2/tasks/{task_id}` | Task Center view (task + plan + tool calls + artifacts) |
| GET | `/api/v2/tasks/{task_id}/artifacts` | Artifacts for a task (Markdown in Week 1) |
| GET | `/api/v2/tasks/{task_id}/results` | Read-only view of the task's normalized tool results (findings, timeline events, signal explanation, policy context) — Agent-domain shapes only, never raw RP payloads |

---

## 1a. Investigation history & persistence

```json
GET /api/v2/investigations
→ { "investigations": [ { "investigation_id": "INV-…", "case_id": "U00299",
    "status": "active", "created_at": "…", "updated_at": "…" } ] }
```

Deterministic order: most recently `updated_at` first. This is minimal
session management — NOT long-term semantic memory (no search, ranking, or
summarization).

**Persistence**: investigation records, current context, per-task plans, and
executed tool-call records persist in TaskStoreV2's SQLite store
(`investigations`, `tasks_v2`, `investigation_sessions` tables — one DB
file). Refreshing the browser does NOT create a new investigation: the
client persists only the current `investigation_id` as UI navigation state
and reloads everything else via `GET /api/v2/investigations/{id}` +
per-task endpoints. Backend restart also preserves sessions.

**Delete**:

```json
DELETE /api/v2/investigations/{id}
→ 200 { "deleted": true, "investigation_id": "INV-…" }
```

Week 1 uses **hard deletion** (no audit requirement): the investigation
record, its session document, and its tasks are removed in one store
transaction scope; other investigations are untouched. Deleted
investigations return 404 afterwards. Clients should confirm before
calling (the UI shows a confirmation dialog; the API itself is direct).

---

## 2. POST /api/v2/investigations

Two accepted request forms (never both):

**Canonical:**

```json
{ "case_id": "U00299" }
```

**Natural (Case Reference Resolution happens server-side):**

```json
{ "message": "investigate case 00299" }
```

or `{ "case_reference": "00299" }`. The frontend submits the raw user text;
it never normalizes case IDs.

Resolution rules (deterministic, no LLM — see `app/case_resolution.py`):

- numeric `"00299"` → canonical `"U00299"`; `"U00299"` stays canonical;
  case-insensitive; whitespace trimmed
- natural wrappers (`investigate`, `investigate case`, `look into`,
  `show me case`, …) do not prevent extraction
- multiple distinct references → `409 AMBIGUOUS_CASE_REFERENCE` with
  `candidates` (never silently chosen)
- explicit-but-unrecognizable reference (`"case ABCXYZ"`) →
  `422 INVALID_CASE_REFERENCE`
- no reference (`"hello"`, empty) → `422 CASE_REFERENCE_REQUIRED`
- only identifiers matching the canonical `U<digits>` pattern can ever be
  produced — arbitrary identifiers cannot be injected

Optional: `preferences` overrides already supported by `ContextPreferences`
(`citation_required`, `response_length`, `output_format`). No speculative
fields.

Response `200`:

```jsonc
{
  "investigation": {
    "investigation_id": "INV-1a2b3c4d5e6f",   // server-generated, stable
    "case_id": "U00299",
    "status": "active",
    "created_at": "...", "updated_at": "..."
  },
  "context": { /* InvestigationContext: case_id, focused_finding_id=null,
                 focused_event_id=null, focus_source=null, preferences */ },
  "tasks": [],
  "intake_message": "Investigate U00299"   // creation only — canonical Case
                                           // Intake turn request
}
```

`investigation_id` is server-generated (`INV-…`), stable across turns, and
distinct from internal `CASE:<case_id>` references. `case_id` must be a
non-empty string (`422` otherwise).

**Initial intake semantics**: `intake_message` is the canonical Case Intake
turn request the client submits as the FIRST conversational turn after
creation. The raw case-reference input (`"00299"`,
`"investigate case 00299"`) is an *identification* input for Case Reference
Resolution — the client must NOT re-send it as a conversational turn
(doing so yields a bounded unresolved response, since after creation it is
neither a case reference nor a finding/event reference). The backend owns
the canonical message format; the frontend never constructs it.

---

## 3. POST /api/v2/investigations/{id}/turn

Request:

```json
{ "message": "Show the timeline." }
```

Optional explicit context action (UI selection shortcut — never a separate
execution path):

```json
{
  "message": "Show the timeline.",
  "context_action": { "type": "focus_finding", "finding_id": "F3" }
}
```

```json
{
  "message": "Why is this event important?",
  "context_action": { "type": "focus_event", "finding_id": "F3",
                      "event_id": "F3-E002" }
}
```

Also valid: `{"type": "clear_focus"}`. Only these shapes are accepted; the
server sets `focus_source = "user_selected"`. The client cannot submit
capabilities, findings, policy refs, plans, tool calls, tool names, skill
ids, or raw context — such fields are ignored or rejected.

Follow-up click semantics — send the follow-up id; the server reconstructs
the canonical intent from its registry:

```json
{ "follow_up_id": "explain_finding" }
```

`message` is optional when `follow_up_id` is present; the server-side
intent text is used as the turn's user request. Unknown ids → `422`.
The follow-up is still a lazy intent: the turn flows through the full
pipeline (Context Resolution → Skill Selection → Planner → Contract
Checker → Executor → Task log) — no direct tool execution exists.

Response `200` (`TurnResponse`):

```jsonc
{
  "investigation_id": "INV-…",
  "status": "completed",          // completed | failed | unsupported |
                                  // execution_failed
  "response": "Investigation result (timeline_investigation): …",
  "context": { /* current InvestigationContext */ },
  "task": { /* TaskV2: id, status, selected_skill, plan_id,
               tool_call_ids, artifact_ids, timestamps, error */ },
  "plan": {                       // omitted when planning failed
    "plan_id": "…", "goal": "…", "status": "executed",
    "selected_skill": "timeline_investigation",
    "steps": [ { "step_id": "S1", "type": "inspect_timeline",
                 "status": "success", "tool_name": "finding_drilldown",
                 "arguments": {…}, "reason": "…", "error": null } ]
  },
  "execution": {
    "status": "completed",
    "tool_calls": [ { "tool_call_id": "…", "tool_name": "finding_drilldown",
                      "status": "success", "outcome": "success",
                      "summary": "timeline with 5 events",
                      "evidence_ref_count": 6, "citation_ref_count": 0,
                      "error": null } ],
    "errors": []
  },
  "artifacts": [ /* ArtifactV2 records when produced */ ],
  "follow_ups": [ { "follow_up_id": "explain_finding", "label": "…",
                    "intent": "…", "target_skill": "…", "target_step": "…" } ],
  "context_changed": false
}
```

Exposed deliberately: plan steps + statuses, bounded tool-call summaries
(outcome-level, ref counts), artifacts, follow-ups. Never exposed: internal
class names, stack traces, raw Risk Platform payloads.

---

## 4. GET /api/v2/investigations/{id}

```jsonc
{
  "investigation": { /* Investigation */ },
  "context": { /* current InvestigationContext */ },
  "tasks": [ /* TaskV2 records for this investigation, ordered */ ]
}
```

`404` with `INVESTIGATION_NOT_FOUND` when unknown.

---

## 5. GET /api/v2/tasks/{task_id}

Task Center view:

```jsonc
{
  "task": { /* TaskV2 */ },
  "plan": { /* this task's plan summary, or null */ },
  "tool_calls": [ /* this task's ToolCall summaries */ ],
  "artifacts": [ /* ArtifactV2 records produced by this task */ ],
  "selected_skill": "timeline_investigation",
  "error": null
}
```

`404` with `TASK_NOT_FOUND` when unknown.

---

## 6. GET /api/v2/tasks/{task_id}/artifacts

```jsonc
{ "task_id": "TASK-…", "artifacts": [ {
    "artifact_id": "ART-…",
    "format": "md",
    "title": "…",
    "content": "# Finding Investigation Bundle\n…",   // inline Markdown
    "storage_ref": null,      // null = inline; unavailable content has
                              // content=null + storage_ref set (future)
    "source_tool_calls": ["TC-…"],
    "scope": "finding:F3"
} ] }
```

Week 1: Markdown only. No file-download infrastructure.

---

## 6a. GET /api/v2/tasks/{task_id}/results

Read-only view of the task's **normalized tool results** in Agent-domain
shapes — added so the frontend can render findings/timeline/explanation
data without the bounded turn summary carrying raw payloads:

```jsonc
{
  "task_id": "TASK-…",
  "results": [
    {
      "tool_call_id": "TC-…",
      "tool_name": "risk_case_fetch",
      "findings": [ /* Finding objects */ ]
    },
    {
      "tool_call_id": "TC-…",
      "tool_name": "finding_drilldown",
      "timeline_events": [ /* TimelineEvent objects */ ]
    },
    {
      "tool_call_id": "TC-…",
      "tool_name": "signal_explain",
      "signal_explanation": { "signal_type": "Rule", "rule": {…},
                               "evidence_missing": false,
                               "next_data_needed": [] }
    },
    {
      "tool_call_id": "TC-…",
      "tool_name": "policy_lookup",
      "policy_context": { "topic": "…", "matches": [...],
                          "evidence_missing": false }
    },
    {
      "tool_call_id": "TC-…",
      "tool_name": "artifact_bundle",
      "artifact": { /* ArtifactV2 */ }
    }
  ]
}
```

Only sections actually present in the result payload appear; empty results,
integration failures, and unsupported results are omitted (their bounded
state is visible in the turn/task views).

---

## 7. Error semantics

Consistent envelope: HTTP status + `{"detail": {"code", "message"}}`
(FastAPI validation errors use the standard 422 body).

| Status | code | When |
|---|---|---|
| 400/422 | validation | Blank `case_id`, blank `message` without `follow_up_id`, malformed `context_action`, unknown `follow_up_id` (`INVALID_FOLLOW_UP`) |
| 404 | `INVESTIGATION_NOT_FOUND` / `TASK_NOT_FOUND` | Unknown ids |
| 409 | — | Reserved: ambiguous/unresolved turns currently return 200 with a bounded failed task + clarification response (matching the Agent's turn semantics); a dedicated 409 mapping is a possible future refinement |
| 500 | — | Unexpected server failure (no tracebacks in body) |
| 502/503 | — | Reserved: RP integration failures surface as bounded turn failures with `outcome=integration_error` in the execution summary; dedicated 502/503 mapping is a possible future refinement |

Ambiguity/unresolved: the turn completes the lifecycle honestly —
`status` in the body is `failed`, `task.status` is `failed` with
`clarification_needed`/`unresolved_reference`, the response asks for
clarification, and `execution`/`follow_ups` are empty. Agent semantics are
preserved, not collapsed.

---

## 8. Persistence & session notes

- Investigations + tasks persist in **TaskStoreV2's SQLite store** (the
  `investigations` + `tasks_v2` tables in the same DB file) — no second
  database, no legacy store.
- Documented Week 1 gap: evolved `InvestigationContext`, per-task plans, and
  executed tool-call records live in an **in-process session map**
  (reset on restart). The natural follow-up is persisting these to the same
  store; the HTTP contract above does not change when that happens.
- Investigation→turn provenance: each turn receives the pool of prior
  executed ToolCalls for artifact sourcing (never re-executed, never
  attributed to the new task).

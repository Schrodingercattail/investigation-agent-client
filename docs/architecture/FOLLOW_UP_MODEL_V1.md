# Suggested Follow-ups — Logical Model V1

**Status**: ACTIVE — logical contract for the Week 1 Suggested Follow-ups capability
**Date**: 2026-08-28
**Parent docs**: `AGENT_CLIENT_ARCHITECTURE_V2.md`, `SKILL_MODEL_V1.md`, `DOMAIN_MODELS_V1.md`

> This document defines the follow-up concept and its logical contract.
> It is architecture only: no runtime code, no UI, and no tool changes are
> part of this step.

---

## 1. What Suggested Follow-ups Are — and Are Not

Suggested Follow-ups are **contextual next-step suggestions** that help an
investigator continue an investigation. They reduce effort (especially for
less experienced investigators), make tool-backed actions discoverable, and
provide a clear continuation point after a result.

They are **NOT**:

- arbitrary generated chat suggestions
- a replacement for planning
- a Tool
- an automatic execution mechanism

The intended relationship:

```
Current result
→ Suggested Follow-ups
→ user selects one
→ next investigation turn
→ Context Resolution
→ Skill Selection
→ LLM Planner
→ Plan Validation
→ Executor
→ Tool Calls
```

**Follow-up = next investigation entry point. Plan = concrete execution
plan.** A follow-up answers "what is a useful next investigation action?";
the planner still decides "what exact steps should be performed for that
request?". A follow-up may carry an expected skill/step as a *hint*, but the
LLM Planner remains authoritative for the actual next-turn plan within the
allowed vocabulary.

---

## 2. Logical Contract

Lightweight record — a plain structured payload, not a class framework, and
not a ToolCall:

| Field | Type | Meaning |
|---|---|---|
| `follow_up_id` | string | Stable identifier of the candidate template (e.g. `"explain_finding"`) |
| `label` | string | Human-facing chip text (e.g. "Why is this finding flagged?") |
| `intent` | string | The structured investigation intent created on click |
| `required_capabilities` | list of capability ids | Finding capabilities that must be supported for this follow-up to be displayable |
| `applicable_context` | enum | `finding` \| `timeline_event` \| `case` |
| `target_skill` | string (optional) | Expected skill for the next turn — hint only |
| `target_step` | string (optional) | Expected planning step within that skill — hint only |
| `reason` | string (optional) | Why this suggestion is offered (for audit/UI tooltip) |

Examples:

```jsonc
{
  "follow_up_id": "explain_finding",
  "label": "Why is this finding flagged?",
  "intent": "explain_finding",
  "required_capabilities": ["signal_explain"],
  "applicable_context": "finding",
  "target_skill": "timeline_investigation",
  "target_step": "explain_signal"
}
```

```jsonc
{
  "follow_up_id": "show_timeline",
  "label": "Show related timeline",
  "intent": "show_timeline",
  "required_capabilities": ["timeline"],
  "applicable_context": "finding",
  "target_skill": "timeline_investigation",
  "target_step": "inspect_timeline"
}
```

---

## 3. Candidate Template Categories (Week 1)

Deterministic template candidates — definitions only; not all become
executable in Week 1. The architecture distinguishes:

- **candidate follow-up definition** — exists in the template table
- **actually executable follow-up** — its target skill/step exists, the step
  resolves to an implemented tool/execution path, and its capability/context
  gates pass

**Only executable follow-ups are shown.** An aspirational/dead-button
candidate is omitted (e.g. if a finding does not support opposite trades, or
a target step has no implemented tool, the follow-up must not be displayed).

### Finding-level candidates

| follow_up_id | label | required capability | target skill / step | Implemented path |
|---|---|---|---|---|
| `explain_finding` | Why is this finding flagged? | `signal_explain` | timeline_investigation / explain_signal | ✅ `signal_explain` tool implemented |
| `show_supporting_evidence` | Show supporting evidence | `timeline` | timeline_investigation / inspect_timeline | ✅ via finding_drilldown (timeline view) |
| `check_policy_requirements` | Which policy requirements apply? | — (none; policy retrieval is case-wide) | case_intake / retrieve_policy | ✅ `policy_lookup` tool implemented (executes for every focused finding; result reports `finding_policy_status`) |
| `show_related_timeline` | Show related timeline | `timeline` | timeline_investigation / inspect_timeline | ✅ via finding_drilldown (timeline view) |
| `generate_next_step_checklist` | Generate next-step investigation checklist | — | case_intake / generate_artifact | — `artifact_bundle` implemented; checklist candidate deliberately not defined |

### Timeline Event-level candidates

| follow_up_id | label | required capability | target skill / step |
|---|---|---|---|
| `explain_event` | Why is this event important? | `signal_explain` | timeline_investigation / explain_signal |
| `show_event_details` | Show event details | `timeline` | timeline_investigation / inspect_timeline |
| `show_related_events_in_window` | Show related events in window | `timeline` | timeline_investigation / inspect_timeline |
| `what_to_verify_next` | What should I verify next? | — | timeline_investigation / explain_signal |
| `export_artifact` | Export this investigation | — | case_intake / generate_artifact |

Executability is evaluated at selection time against the live registries:
a candidate is executable only when `target_skill` exists, `target_step`
exists in that skill's `planning_steps`, the step→tool binding resolves
to an implemented execution path, **and the target skill is eligible for
the focused finding under the Skill Registry's `required_capabilities` —
the same eligibility the planner uses**. (A chip whose skill cannot be
planned for this finding would fail `SKILL_NOT_ELIGIBLE`: a dead button,
violating P13/P19.) Templates whose targets are not (yet)
implemented simply never surface — no placeholder buttons.

---

## 4. Trigger Rules (display policy)

Follow-ups **MUST NOT appear after every assistant message**. They appear
only at useful investigation continuation points.

**Show follow-ups (Week 1 trigger reasons):**

| Trigger | When |
|---|---|
| `task_completed` | A major task completes / a major artifact is produced |
| `focus_changed` | The user selects or changes focus to a finding or event |
| `evidence_missing` | The result contains `evidence_missing`, a bounded fallback, or unresolved investigation state |
| `successful_tool_result` | A successful tool result leaves a meaningful next investigation action |

**Do NOT show follow-ups:**

1. During clarification / ambiguity resolution (the user must answer the
   clarification first — suggestions would compete with it).
2. During ordinary free-form conversation that has not produced a structured
   investigation result.
3. While the user is explicitly editing or reviewing an artifact.

Trigger logic stays simple and deterministic in Week 1 — no scoring models,
no rule engines beyond the table above.

---

## 5. Capability-aware Selection

Selection uses the **same source of truth** as Skills and
`FindingCapability` — capability rules are never duplicated in the frontend.

```
Skill Registry / FindingCapability
→ determine eligible investigation actions
→ Follow-up candidate filtering
→ show at most 3
```

Filtering pipeline:

1. **Candidate templates** (all definitions)
2. **Capability filtering** — `required_capabilities` ⊆ focused finding's
   `FindingCapability`, and the target skill is eligible for the finding
   (the planner's own registry truth)
3. **Context/scope filtering** — finding-level candidates require
   `focused_finding_id`; timeline-event candidates require
   `focused_finding_id` + `focused_event_id`; case-level candidates may run
   without focus (only if a real candidate template exists — no focus is
   fabricated merely to show a suggestion)
4. **Trigger filtering** — the current continuation point is a trigger reason
5. **Executable-only rule** — target skill/step exist and resolve to an
   implemented path
6. **Deterministic ordering**, then **maximum 3**

Deterministic ordering preference (explicit, tested):

1. direct next investigation action (e.g. explain_finding, show_timeline)
2. evidence / explanation continuations
3. policy / artifact continuations

Ties break by template declaration order. The same context + capabilities +
trigger + implementation availability always yields identical follow-up IDs
and ordering. **No LLM is involved in selection.**

---

## 6. Execution Semantics

**Clicking a follow-up does NOT directly execute a tool.**

A click creates a normal structured user intent for the next turn. That turn
flows through exactly the same pipeline as a free-form request:

```
Context Resolution → Skill Selection → LLM Planner → Plan Validation
→ Executor → Tool Calls → grounding → artifact → Task Log
```

Consequences (all deliberate):

- Every follow-up-driven turn gets its own **Task / Plan / ToolCall /
  Artifact** records. There is no special execution path for follow-ups.
- Planning, validation, task logging, grounding, and artifact mechanisms
  apply identically.
- Selecting a follow-up starts a normal new investigation turn — it appears
  in the task log as user-initiated work, not as an autonomous action.

---

## 7. Relationship to Context

Follow-ups are generated/filtered from the current `InvestigationContext` —
case, focused finding, focused event, capabilities, and previous result state
where relevant.

**Displaying a follow-up never mutates context.** Context changes only after
explicit user selection, successful context resolution, or an explicitly
defined Context Update.

---

## 8. UI Behavior

- Displayed as a small set of **action chips/buttons** (contextual actions,
  not generic chat prompts).
- **Maximum 3** per display.
- Positioned below the relevant Agent response and/or in the current
  artifact/focus workspace.
- The frontend receives the **already-filtered** canonical follow-up payload;
  it does not independently reimplement capability logic.

---

## 9. Implementation (backend/app/followups.py)

Week 1 runtime implements this contract deterministically. Details:

- **Deterministic candidate templates**: `FINDING_TEMPLATES` /
  `EVENT_TEMPLATES` / `CASE_TEMPLATES` are fixed declaration-order tables.
  The runtime exposes `explain_finding` (→ `signal_explain`, implemented),
  `check_policy` (→ `policy_lookup`, implemented — end-to-end executable),
  `show_timeline` / `related_events` / `explain_event` (→
  `finding_drilldown` timeline view, implemented), and `export_artifact`
  (→ `artifact_bundle`, implemented — case-level, executable end-to-end
  since artifact_bundle landed). The remaining ambiguous candidates
  (`show_evidence`, `next_actions`, `verify_next`) are deliberately
  **absent** — their steps resolve to tools without a distinct implemented
  path, so defining them would create dead buttons. The executable-only
  rule is enforced at the template layer.
- **Capability-aware filtering**: selection takes
  `finding_capabilities` (ids from `FindingCapability` — the single source
  of truth) and requires `required_capabilities ⊆ caps`. No capability rules
  are duplicated here beyond the template declarations themselves.
- **Context/scope filtering**: finding-level templates require
  `focused_finding_id`; event-level templates additionally require
  `focused_event_id`; no focus ⇒ zero suggestions (focus is never
  fabricated).
- **Trigger handling**: the API accepts only the four `TriggerReason`
  values (`task_completed`, `focus_changed`, `evidence_missing`,
  `successful_tool_result`); any other state cannot be submitted —
  clarification/free-form/artifact-editing turns simply don't call selection.
- **Executable-only rule**: target skill exists → target step in skill
  vocabulary → step resolves to a registered `STEP_TOOL_MAP` binding; tools
  with neither implementation nor registration can never pass.
- **Max 3 + deterministic ordering**: contract preference rank
  (direct action → evidence view → policy continuation), then declaration
  order; identical inputs always yield identical IDs and order. No LLM.
- **No direct tool execution**: `FollowUp.intent` is plain request text for
  the next turn; the click path is the normal pipeline (Context Resolution →
  Skill Selection → LLM Planner → Contract Checker → Executor → Task Log).
  The FollowUp model carries no callables and selection never invokes tools.
- **Event intents carry an anchor**: event-scoped suggestions prefix the
  focused event id (`[event: F3-E002] …`) so the next turn's Context
  Resolution resolves unambiguously.

---

## 10. Week 1 Constraints

- Deterministic templates + capability-aware filtering + maximum 3 +
  contextual trigger rules. Nothing more.
- NOT implemented in Week 1: LLM-generated follow-up text, recommendation
  ranking, click-through optimization, reinforcement learning, long-term
  personalization.

### Future extensions (documented, do NOT build now)

- follow-up click-through rate
- task continuation rate
- completion rate
- follow-up acceptance by context/skill

These metrics ideas are recorded for future evolution only — no analytics in
Week 1.

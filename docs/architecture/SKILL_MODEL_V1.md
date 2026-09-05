# Skill Model V1 — Week 1 Contract

**Status**: ACTIVE — normative companion to `AGENT_CLIENT_ARCHITECTURE_V2.md`
**Date**: 2026-08-27
**Parent docs**: `AGENT_CLIENT_ARCHITECTURE_V2.md`, `DOMAIN_MODELS_V1.md`

> This document defines Skills as a lightweight, first-class Agent concept.
> It is documentation only: no Skill Registry implementation, no planner
> changes, and no model changes happen in this step.

---

## 0. What a Skill Is — and Is Not

A Skill represents **a reusable type of investigation work** that the Agent
can perform. It sits between user intent and the tool layer.

| Concept | Answers | Lives |
|---|---|---|
| **Skill** | "What kind of investigation work can the Agent perform?" | Reusable workflow definition (this document) |
| **Capability** | "Is this investigation action valid for the current finding?" | Runtime eligibility gate (`FindingCapability`) |
| **Tool** | "How is a specific step executed?" | Execution primitive (tool registry) |
| **Plan** | "What should the Agent do for this specific user request / turn?" | Per-request/per-turn LLM decision |

Design principle: **Skill = reusable investigation workflow definition.
Tool = execution primitive. Capability = runtime eligibility gate.
Planning = per-request / per-turn decision making.**

A Skill is therefore neither a Tool (a skill *uses* many tools), nor a
Capability (capabilities gate whether a skill is *executable* right now), nor
a Plan (plans are turn-local instances assembled *from* a skill's vocabulary).

---

## 1. Skill Schema (Logical Contract)

Skills are plain declarative records — data, not classes. No runtime class
hierarchy, no inheritance, no execution framework.

| Field | Type | Meaning |
|---|---|---|
| `skill_id` | string | Stable identifier (registry key), e.g. `"case_intake"` |
| `name` | string | Human-readable display name |
| `description` | string | What this skill investigates; used in planner prompting and UI |
| `required_capabilities` | list of capability ids | Capabilities the **current finding** must support for the skill to be executable. Empty list = case-level skill (no finding focus needed) |
| `allowed_tools` | list of tool specs | Tools (with parameter constraints, e.g. `finding_drilldown(view=timeline)`) the skill may use. Subset of the global tool registry |
| `planning_steps` | list of step-type ids | The planning vocabulary for this skill. Subset of the global step registry (`DOMAIN_MODELS_V1 §2.7`) |
| `constraints` | list of strings | Hard rules the planner/validator/enforcer must honor (preconditions, ordering rules, bounding) |

Normalization rule used throughout this contract:

> `step type ⇄ tool binding`: `fetch_case ⇄ risk_case_fetch`,
> `timeline ⇄ finding_drilldown(view=timeline)`,
> `opposite_trades ⇄ finding_drilldown(view=opposite_trades)`,
> `signal_explain ⇄ signal_explain`,
> `policy_lookup ⇄ policy_lookup`,
> `artifact ⇄ artifact_bundle`.

`planning_steps` are what plans may contain; `allowed_tools` are what those
steps bind to at execution time. Both lists are closed subsets per skill.

---

## 2. Week 1 Skills

Exactly three production skills are defined. Do not define additional
production skills in Week 1.

### 2.1 `case_intake`

```jsonc
{
  "skill_id": "case_intake",
  "name": "Case Intake",
  "description": "Fetch the authoritative picture of a case and produce an intake summary.",
  "required_capabilities": [],              // case-level: no finding focus required
  "allowed_tools": [
    "risk_case_fetch",
    "policy_lookup",
    "artifact_bundle"
  ],
  "planning_steps": [
    "fetch_case",
    "retrieve_policy",
    "generate_artifact"
  ],
  "constraints": [
    "Runs at case level; ignores focused_finding_id.",
    "If InvestigationContext has no cached case context for the session, "
    "fetch_case must precede generate_artifact.",
    "generate_artifact must compose only from steps executed in this plan.",
    "retrieve_policy is the case-wide policy continuation (needs no "
    "finding capability)."
  ]
}
```

### 2.2 `timeline_investigation`

```jsonc
{
  "skill_id": "timeline_investigation",
  "name": "Timeline Investigation",
  "description": "Investigate a focused finding through its chronological evidence timeline.",
  "required_capabilities": ["timeline"],
  "allowed_tools": [
    "finding_drilldown(view=timeline)",
    "finding_drilldown(view=evidence, stream=withdrawals)",
    "finding_drilldown(view=evidence, stream=transactions)",
    "signal_explain",
    "policy_lookup",
    "artifact_bundle"
  ],
  "planning_steps": [
    "inspect_timeline",
    "inspect_evidence",
    "inspect_withdrawals",
    "inspect_transactions",
    "explain_signal",
    "retrieve_policy",
    "generate_artifact"
  ],
  "constraints": [
    "Requires InvestigationContext.focused_finding_id.",
    "Executable only if the focused finding declares capability 'timeline'.",
    "finding_drilldown is locked to view='timeline' for inspect_timeline "
    "and view='evidence' for inspect_evidence; inspect_withdrawals / "
    "inspect_transactions carry an explicit evidence-stream scope so the "
    "requested stream governs tool behavior (never the finding-title "
    "heuristic, never a substituted stream)."
  ]
}
```

### 2.3 `trade_investigation`

```jsonc
{
  "skill_id": "trade_investigation",
  "name": "Trade Investigation",
  "description": "Investigate a focused finding through the trades composing its opposite-trade signal.",
  "required_capabilities": ["opposite_trades"],
  "allowed_tools": [
    "finding_drilldown(view=opposite_trades)",
    "signal_explain",
    "policy_lookup",
    "artifact_bundle"
  ],
  "planning_steps": [
    "opposite_trades",
    "signal_explain",
    "policy_lookup",
    "artifact"
  ],
  "constraints": [
    "Requires InvestigationContext.focused_finding_id.",
    "Executable only if the focused finding declares capability 'opposite_trades'.",
    "finding_drilldown is locked to view='opposite_trades'; requesting any "
    "other view inside this skill is invalid."
  ]
}
```

---

## 3. Planning Relationship

Skills slot into the V2 execution loop ahead of the planner:

```
User Request
→ Investigation Context
→ Skill Selection            ← NEW: choose applicable skill(s)
→ LLM Planner                ← plans ONLY inside the selected skill's vocabulary
→ Structured Plan
→ Plan Validation            ← validates skill choice + plan against registries
→ Executor
→ Tool Calls
```

Rules:

1. **Selection under constraint** — the LLM may select a Skill, but only from
   the set of skills *applicable* to the current context (see §4). The
   selection plus its justification is part of the produced plan output and
   is logged for auditability. When Plan/Task schemas gain a persistent
   `selected_skill_id` field (at the implementation step), it lands there;
   until then the task log carries it.
2. **Vocabulary containment** — the planner's plan steps ⊆ selected skill's
   `planning_steps` ⊆ global step registry. The LLM must NOT invent arbitrary
   tool names or arbitrary investigation steps; anything outside the selected
   skill's lists is rejected by plan validation.
3. **Argument constraints propagate** — parameter locks on `allowed_tools`
   (e.g. `view=timeline`) are enforced at validation and again at execution
   time.
4. **Empty cases** — with no prior case context, only `case_intake` is
   applicable. Finding-scoped skills require a focus and satisfied capability
   gates.

Worked examples (consistent with prior docs):

```
"Investigate case U00299"
  → skill: case_intake
  → plan: [fetch_case, artifact]

"Why was F3 flagged?"
  → applicable skills for F3: timeline_investigation ✓, trade_investigation ✗
  → skill: timeline_investigation
  → plan: [signal_explain, artifact]

"How did the trades behind F2 behave?"
  → skill: trade_investigation (if F2 supports opposite_trades)
  → plan: [opposite_trades, signal_explain, artifact]
```

---

## 4. Capability Gating

Skill applicability alone is **not sufficient**. A Skill is executable only
when its `required_capabilities` are supported by the **current Finding**
(`FindingCapability`, `DOMAIN_MODELS_V1 §2.4`).

Availability rules:

| Skill | Available when |
|---|---|
| `case_intake` | Always (case-level skill) |
| `timeline_investigation` | `focused_finding_id` set **AND** finding supports `timeline` |
| `trade_investigation` | `focused_finding_id` set **AND** finding supports `opposite_trades` |

Worked example — Finding F3 declaring capabilities
`{timeline, signal_explain, policy_lookup}`:

```
timeline_investigation = available     (has 'timeline')
trade_investigation    = unavailable   (missing 'opposite_trades')
```

Enforcement is layered (mirrors `DOMAIN_MODELS_V1 §4` invariants 1 and 3):

1. **Planner input**: the current investigation context includes the focused
   finding's capability set → skill availability, so the LLM chooses among
   genuinely available skills.
2. **Plan validation**: a plan pairing a skill whose gates fail, or steps
   outside the skill's vocabulary, is deterministically rejected.
3. **Runtime revalidation**: executor/tool checks capability + view-lock
   again before executing.

An unsupported skill/tool combination returns a bounded `unsupported` result
(`ToolResult.outcome="unsupported"` with the supported alternatives) and must
never fabricate data.

---

## 5. Skill Registry (Week 1, Logical)

A simple, **data-driven, static registry** with exactly three entries.
Step names below reflect the implemented registry (`backend/app/skills.py`);
the canonical planning vocabulary is: `fetch_case`, `generate_artifact`,
`inspect_timeline`, `inspect_opposite_trades`, `explain_signal`,
`retrieve_policy`.

| skill_id | required_capabilities | allowed_tools | planning_steps |
|---|---|---|---|
| `case_intake` | — | risk_case_fetch, artifact_bundle, policy_lookup | fetch_case, retrieve_policy, generate_artifact |
| `timeline_investigation` | timeline | finding_drilldown(view=timeline / view=evidence / view=evidence,stream=withdrawals / view=evidence,stream=transactions), signal_explain, policy_lookup, artifact_bundle | inspect_timeline, inspect_evidence, inspect_withdrawals, inspect_transactions, explain_signal, retrieve_policy, generate_artifact |
| `trade_investigation` | opposite_trades | finding_drilldown(view=opposite_trades), signal_explain, policy_lookup, artifact_bundle | inspect_opposite_trades, explain_signal, retrieve_policy, generate_artifact |

`retrieve_policy` (→ `policy_lookup`) is a **case-wide policy continuation**:
policy retrieval requires no finding capability, so it is plannable for every
focused finding via `case_intake` (always eligible). Whether a finding has an
authoritative finding-level policy basis is a data question, answered by the
tool result (`finding_policy_status`: `associated` | `no_finding_level_basis`)
— never a capability verdict.

Registry rules:

- Static declarative data (implemented as `SKILLS` in `backend/app/skills.py` —
  plain validated records, no class hierarchy).
- Unknown `skill_id`s are invalid input, rejected at validation.
- There is **no** plugin mechanism, loader, lifecycle manager, or scheduling
  logic. Selecting, validating, and logging a skill consumes the registry;
  nothing executes "a skill" as a unit — the Executor still runs steps/tools.

---

## 5a. Implementation (backend/app/skills.py)

Implemented Week 1 surface; this section documents how the conceptual model
above landed in code. Conceptual architecture unchanged.

**Tool implementation status (factual)**: `fetch_case → risk_case_fetch` ✅,
`inspect_timeline → finding_drilldown(view=timeline)` ✅,
`explain_signal → signal_explain` ✅,
`retrieve_policy → policy_lookup` ✅,
`generate_artifact → artifact_bundle` ✅ (all Week 1 bindings executable).

**Static registry** — `SKILLS: dict[str, SkillDefinition]`, exactly the three
Week 1 skills. `SkillDefinition` is a Pydantic record with
`skill_id / name / description / required_capabilities / allowed_tools /
planning_steps / constraints` — data, not a class hierarchy.

**Step → tool mapping** — `STEP_TOOL_MAP: dict[str, StepToolBinding]` is the
single source of truth; arbitrary step→tool pairs cannot be expressed:

| step type | tool | parameter locks |
|---|---|---|
| `fetch_case` | `risk_case_fetch` | — |
| `generate_artifact` | `artifact_bundle` | — |
| `inspect_timeline` | `finding_drilldown` | **view="timeline"** |
| `inspect_opposite_trades` | `finding_drilldown` | **view="opposite_trades"** |
| `explain_signal` | `signal_explain` | — |
| `retrieve_policy` | `policy_lookup` | — |

**Parameter locks** — `parameter_locks` on each binding pin argument keys the
Planner may not alter. Under `timeline_investigation`, a `finding_drilldown`
step must carry `view="timeline"` — any other value (or its absence) is a
contract violation; extra non-locked arguments (e.g. `top_n`) remain free.

**Contract Checker** — deterministic functions returning structured
`PlanValidationResult {valid, errors[{code, step_id, message}]}` — never
generic exceptions for ordinary violations, and never `ToolResult` outcomes
(plan validation implies no tool execution):

- `check_skill_eligibility(skill_id, capabilities)` → Rules 1–2 only
- `check_plan(skill_id, steps, capabilities)` → all six rules:
  1. skill exists (`SKILL_NOT_FOUND`)
  2. required capabilities supported by current Finding
     (`CAPABILITY_NOT_SUPPORTED`)
  3. step type ∈ skill.planning_steps (`STEP_NOT_ALLOWED`)
  4. declared tool == registered mapping for that step type
     (`INVALID_STEP_TOOL_MAPPING`)
  5. locked arguments match exactly on locked keys (`PARAMETER_LOCK_VIOLATION`)
  6. no tool outside skill.allowed_tools (`TOOL_NOT_ALLOWED`)

Works directly with the existing `FindingCapability`; capability gates depend
only on declared capabilities, never on finding type alone.

Supporting helpers: `eligible_skills_for_finding(capabilities)` — the only
skill list the planner may select from (case-level skills always included);
`planning_vocabulary(skill_id)` — the closed per-skill vocabulary handed to
the LLM Planner.

**Planner-side vs runtime validation** — the same checker serves both layers
(defense in depth):

1. *Planner-side*: runs before a plan is accepted; violations reject the plan
   (logged) per ARCHITECTURE_V2 §4.2.
2. *Runtime/executor-side*: re-runs before/at execution even for an accepted
   plan, since context may have shifted between validation and execution.

The LLM is structurally untrusted: it may only receive eligible skills +
closed vocabularies, and anything it emits that references identifiers or
parameter values outside `SKILLS` / `STEP_TOOL_MAP` fails contract checking.

---

## 6. UI Implications

- In **Focus Mode**, the UI may expose available Skills/actions for the
  focused finding, but must show only those whose required capabilities are
  supported by that finding.
- Unavailable actions are hidden or visibly disabled with the reason
  (e.g. "requires timeline evidence") rather than offered and failing later.
- At **Case Intake**, the surfaced action set is `case_intake`.
- The UI's available-actions list must be **computed from the same sources of
  truth** (skill registry × `FindingCapability`) — no hand-maintained,
  duplicated action lists in the frontend.

---

## 7. Non-Goals

This step explicitly introduces **none** of the following:

- Multi-agent skills
- Skill marketplace
- Skill installation system
- Dynamic code loading
- Long-term skill memory
- Complex skill inheritance / composition hierarchies
- Autonomous skill creation (skills are human-authored data)

---

## 8. Evolution Notes

- Week 2 skills (e.g. a network-drilldown skill) attach by **adding a registry
  entry** — same schema, same gating mechanics, no architectural change.
- `FindingCapability` remains the single eligibility source; skills never
  bypass it, and capabilities never depend on skills.
- Any widening of the skill schema adds optional fields only; the three
  Week 1 records remain forward-compatible.

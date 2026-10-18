# GSP — Goal Semantic Preservation — Implementation Plan v1

**Date**: 2026-10-07
**Status**: IMPLEMENTATION PLAN — no code changed by this document.
**Design basis**: `docs/plans/2026-10-07-gsp-goal-semantic-preservation-design-v1.md`
**Verified against**: current working tree (`backend/app/*` at HEAD
`58881a9` + the G1 changes already present uncommitted: `config.py`,
`context_resolution.py`, `agent_loop.py`, `telemetry.py`,
`target_validity.py`). Every code reference below was read from the
current files, not assumed from the design doc.

---

## 1. Purpose

Land GSP on the existing Agent runtime so that a turn executes as:

```
User Request
 → Requirement Extraction            (LLM proposes; existing understand_goal call)
 → Accepted Goal Semantic Contract   (validated, persisted, immutable content)
 → Planning Coverage                 (Slice 2)
 → Executable Capability Admission   (binding declarations)
 → Tool Execution                    (unchanged)
 → Observation                       (+ for_output association)
 → Evidence / Postcondition Eval     (deterministic, per evidence type)
 → Required Output Satisfaction      (system-only state writes)
 → Overall Goal Outcome              (derived, deterministic)
 → Deterministic Response Composition (per-output filtering + boundaries)
```

Success criterion for Slice 1, in one sentence: **"a tool succeeded" no
longer means "the user's question was answered"** — at completion,
per-output state and composer rendering.

## 2. Scope

- **Slice 1** (WP-GSP-1 … WP-GSP-5): capability declarations; contract
  schema + extraction + persistence; observation association;
  deterministic postconditions; satisfaction + goal outcome; composer
  scope preservation. Solves: (A) cross-account false positive, (B)
  unsupported-capability fallback at the composer/completion level,
  (C) deterministic negative results mis-handled.
- **Slice 2** (WP-GSP-6 … WP-GSP-7): planner coverage admission;
  re-plan preservation and unsupported-path filtering; output-aware
  completion decisions.
- **Out of scope**: extraction completeness guarantees (design §1/§12);
  new capabilities/tools; ToolResult schema changes; new databases or
  state stores; turn-path planner validation (turn gets composer+
  postcondition benefits; its planner admission follows in a later
  package); lexicon/MCP/keyword tables.

## 3. Implementation Principles (binding for every WP)

1. execution success ≠ semantic satisfaction (I-1)
2. evidence existence ≠ evidence relevance (I-2)
3. capability availability ≠ data availability (I-3)
4. unrelated successful evidence ≠ the user-request answer (I-4)
5. accepted-contract preservation ≠ extraction completeness
6. unsupported ≠ unresolved
7. nearest capability ≠ valid capability (no fallback, ever)
8. terminal execution condition ≠ goal satisfaction

The LLM **only proposes** (outputs at goal understanding; steps at
planning; decisions in the loop proposer). The system **validates,
controls, executes, persists, evaluates, disposes**. No prompt-only
"please judge carefully" substitutes for a deterministic check.

## 4. Current Architecture Mapping (design names → real code)

| Design concept | Actual code (verified) | Notes / deltas |
|---|---|---|
| GoalSemanticContract | **new** `GoalContract` pydantic model in `app/goal_resolution.py` (next to `GoalRepresentation`) | carrier: new field `goal_contract` on `AgentLoopState` |
| RequiredOutput | **new** `RequiredOutput` model in `app/goal_resolution.py` | |
| Evidence type vocabulary | **new** `EvidenceType` str-enum + `EVIDENCE_TYPES` frozenset in `app/skills.py` (declarations live beside the bindings) | |
| "StepToolBinding.supported_evidence_types" | `StepToolBinding` (`skills.py:43-48`: `tool_name` + `parameter_locks`) — **additive field** `supported_evidence_types: list[EvidenceType] = []`; registry = `STEP_TOOL_MAP` (`skills.py:55-96`, 11 step types) | additive, default empty |
| Contract production | `understand_goal` (`goal_resolution.py:284`) — extend its single LLM call's JSON schema (flag-gated prompt block; 2048-token lesson applies) + pydantic validation (same pattern as `GoalRepresentation._validate`) | one call, no new service |
| Observation record | `project_observation` (`agent_loop.py:158-190`): dict with `tool_name/step_type/outcome/semantic_status/summary(dict, per-tool whitelist)/evidence_refs/warnings/error/complete/next_data_needed` + `iteration/step_id/step_type` appended at `:1369-1377` | association field `for_output` added here |
| Completion | `AgentLoopState.criteria_satisfied()` (`agent_loop.py:524-543`) ANY-success rule; **STOP-dominance at policy** (`:762-777`); `_criteria_kind` heuristic (`:1000-1005`) | flag-gated per-output regime |
| Re-plan | `_re_plan` (`agent_loop.py:1509-1527`): original goal text + compact digest; **revision cap already exists** (`state.plan_revisions >= 2` → forced terminal, `:813/:834`); per-step retry cap `AGENT_LOOP_MAX_RETRIES_PER_STEP` (state.retries) | GSP reuses these; no second loop controller |
| Planner invocation | `planner.plan(goal_text, ctx, eligible_ids, finding_capabilities=caps)` (`:1501`, `:1525`) — receives **raw text**, never GoalRepresentation | Slice 2 adds contract parameter |
| Composer (loop) | `compose_loop_response` (`:2023`) → `answer = _case_level_answer_lines(state) (:1872) or _finding_answer_lines(state) (:1984)`; closure appends step recap + **last-3 observation lines** (leaks raw error codes — fixed in WP-GSP-5) | |
| Composer (turn) | `compose_response` (`investigation_service.py:148`) | Slice 2 wiring note |
| Eligibility | `eligible_skills_for_finding` (`skills.py:372-383`) — capability gates only | Slice 2: evidence-requirement admission added beside it |
| Persistence | `TaskStoreV2.save_agent_state` serializes the whole `AgentLoopState` model (`agent_loop.py:1621-1627`) | additive fields persist **without migration** |
| Flags | `app/config.py` pattern (e.g. `ENABLE_TARGET_VALIDITY_GUARD`) | new flag same section |
| Telemetry | `AgentTelemetryEvent` + `emit_event` (`telemetry.py`); precedent: target-validity field group | new GSP field group |
| G1 (present, uncommitted) | `ENABLE_TARGET_VALIDITY_GUARD`; resolution-side Layer A; loop-side Layer B admission at `agent_loop.py` (post-resolution choke point) | GSP composes after G1; G1 untouched |

**Discovered design→code correction (material, plan-altering)**: the
design doc declared `related_account_analysis` →
`shared_ip_evidence(availability)` justified by adapter data. **Current
tool reality**: `related_account_analysis`'s ToolResult payload contains
`related_accounts: list[str]` (IDs only), `cluster`, `shared_devices`,
`evidence_missing` — **it does not carry `shared_ips`**; the adapter's
`fetch_network_signals` has `shared_ips` per account but normalization
drops it. Consequence: Slice-1 declarations are grounded in *actual
tool output*, so `analyze_related_accounts` declares
`[network_relations, shared_device_evidence]` **only**; the shared-IP
availability declaration is gated on **WP-GSP-1b**, a minimal payload
echo (adapter → tool output) that must land before Scenario GSP-03 can
execute. This mapping correction is recorded here per the
"current code wins" rule.

## 5. GSP Runtime Model (where each chain layer lands)

| Chain layer | Lands in | Flag reads |
|---|---|---|
| Requirement extraction | `understand_goal` (extended JSON schema) + validator | ON |
| Accepted contract | `AgentLoopState.goal_contract` (persisted) | — |
| Planning coverage | `_adopt_plan` validation path | Slice 2 |
| Capability admission | plan-validation against `STEP_TOOL_MAP[step].supported_evidence_types` | Slice 2 |
| Tool execution | unchanged `ExecutorV2` | — |
| Observation association | `for_output` at `project_observation` append site | ON |
| Postcondition eval | new `app/goal_postconditions.py` (pure) | ON |
| Output satisfaction | `GoalContract.outputs[i].satisfaction` (system-only writes) | ON |
| Goal outcome | derived property on `GoalContract` | ON |
| Composer | `compose_loop_response` / `_case_level_answer_lines` / `_finding_answer_lines` | ON |

## 6. Work Package Overview

| WP | Title | Slice | Depends on |
|---|---|---|---|
| WP-GSP-1 | Capability Declaration Foundation (+1b shared-ip availability echo) | 1 | — |
| WP-GSP-2 | Goal Contract model + extraction + persistence | 1 | 1 |
| WP-GSP-3 | Observation → Required-Output Association | 1 | 2 |
| WP-GSP-4 | Deterministic Postcondition Evaluator + Satisfaction | 1 | 2, 3 |
| WP-GSP-5 | Composer Semantic Scope Preservation + Goal Outcome | 1 | 4 |
| WP-GSP-6 | Planner Coverage Admission | 2 | 1–5 |
| WP-GSP-7 | RE_PLAN / Loop Preservation | 2 | 6 |

## 7. Slice 1

### WP-GSP-1 — Capability Declaration Foundation

- **Objective**: make executable bindings the authoritative source of
  "what evidence types this path can produce".
- **Current implementation point**: `StepToolBinding` (`skills.py:43-48`),
  `STEP_TOOL_MAP` (`:55-96`).
- **Files**: `app/skills.py` (+ tests).
- **Data/model changes**: `EvidenceType` str-enum + `EVIDENCE_TYPES`
  frozenset; `StepToolBinding.supported_evidence_types: list[EvidenceType]`
  (default `[]`). Initial declarations (grounded in verified tool output):
  - `fetch_case` → [case_summary]
  - `generate_artifact` → [case_summary]
  - `inspect_timeline` → [] in Slice 1 (event-level type deferred; no
    current acceptance scenario needs it — declared in a later slice
    with a real scenario) — **do not invent**
  - `inspect_evidence` → [transaction_records]
  - `inspect_withdrawals` → [withdrawal_records]
  - `inspect_transactions` → [transaction_records]
  - `inspect_self_opposite_trades` → [] (registered-but-unimplemented
    view; stays undeclared — P19-consistent)
  - `analyze_cross_account_trades` → [opposite_trade_relations]
  - `analyze_related_accounts` → [network_relations, shared_device_evidence]
  - `explain_signal` → [ml_signal_explanation, rule_explanation, graph_cluster_facts]
  - `retrieve_policy` → [policy_references]
- **System-controlled logic**: declarations are static registry data; no
  runtime inference from prose.
- **LLM responsibilities**: none.
- **WP-GSP-1b (conditional, prerequisite for Scenario GSP-03)**: extend
  `related_account_analysis` payload to echo the adapter's per-account
  `shared_ips` (data the adapter already returns; normalization today
  drops it). Then add `shared_ip_evidence` to its declaration. Without
  1b, a shared-IP requirement is `UNSUPPORTED` (Scenario GSP-02 — the
  honest current-system behavior).
- **Compatibility**: additive field, default `[]`; no behavior change
  alone (consumers arrive in WP-GSP-4/6).
- **Failure modes**: declaration drift from real tool behavior →
  mitigated by L1 tests asserting declarations against live tool
  payloads (see Test strategy).
- **Test strategy**: unit — every binding's declared types are checkable
  against a recorded payload fixture; 1b — payload echo test.
- **E2E**: none (no user-visible change).
- **Rollback**: field unused until consumers land.
- **DoD**: declarations present for all 11 bindings (with explicit
  `[]` where intentionally undeclared); `shared_ip ≠ shared_device`
  encoded (distinct enum values, distinct declarations);
  `money_laundering_assessment` has **no** declaring binding (asserted
  by test — the claim vocabulary is open, the evidence vocabulary is
  closed); flag-independent; unit tests green.

### WP-GSP-2 — Goal Contract model + extraction + persistence

- **Objective**: produce and persist an accepted `GoalContract` per
  goal-run.
- **Current implementation point**: `understand_goal`
  (`goal_resolution.py:284+`), invoked by the loop pre-planning
  (`agent_loop.py:946-955`); `AgentLoopState` (`:465+`).
- **Files**: `app/goal_resolution.py`, `app/agent_loop.py` (state field +
  creation site), `app/config.py` (flag), tests.
- **Data/model changes**:
  ```python
  class RequiredOutput(BaseModel):
      output_id: str                    # "O1", "O2" — generated as
                                        # f"O{i}" in proposal order (stable
                                        # within the run; regenerated ids are
                                        # never persisted across runs)
      claim: str                        # bounded user-side judgment label
      claim_description: str | None
      evidence_requirements: list[EvidenceType]   # >= 1, frozen vocabulary
      subject_ref: str                  # e.g. "U00010" / case id / finding id
      scope: ScopeLevel                 # case_level | finding_level | event_level
                                        # | account_level | relationship_graph
      satisfaction: OutputSatisfaction = OUTPUT_PENDING
      detail: str | None = None         # bounded user-safe boundary text

  class GoalContract(BaseModel):
      goal_type: str                    # existing frozen vocabulary
      subject_ref: str
      outputs: list[RequiredOutput]     # >= 1, flat
      step_output_map: dict[str, str] = {}   # step_id -> output_id (Slice 2 use;
                                             # maintained at each plan adoption)
      def goal_outcome(self) -> GoalOutcome: ...   # deterministic derivation
  ```
  `AgentLoopState.goal_contract: GoalContract | None = None`.
  `OutputSatisfaction` enum: `PENDING / SATISFIED / PARTIALLY_SUPPORTED /
  NOT_SATISFIED / UNRESOLVED / UNSUPPORTED`. `GoalOutcome`:
  `COMPLETE / PARTIAL / UNANSWERABLE / BLOCKED` (derivation in WP-GSP-4).
- **Runtime flow changes**: loop, after `state.goal_representation` is
  produced and **after G1's target admission**, when flag ON: build
  contract via the extended `understand_goal` proposal + validation.
  Validation rules: evidence requirements ⊆ `EVIDENCE_TYPES`; ≥1 output;
  ids unique; subject_ref non-empty; scope ∈ enum. **Invalid proposal →
  contract omitted (None)** → loop runs exactly as today (bounded
  degradation, never a fabricated contract).
- **System-controlled logic**: vocabulary validation; id assignment;
  persistence; immutability of content (satisfaction writes only, and
  only from the evaluator).
- **LLM responsibilities**: propose outputs (claim labels + evidence
  requirements + subject/scope) in the same call as goal_type today.
  Prompt addition is flag-gated; OFF keeps today's schema byte-for-byte.
- **Compatibility**: contract is additive; OFF = no field written, no
  prompt change. Persistence: whole-model JSON — no migration.
- **Failure modes**: extraction misses a requirement (documented GSP
  boundary — NOT solved here); malformed/over-inclusive proposal →
  contract omitted → legacy behavior; LLM unavailable → contract None.
- **Test strategy**: unit — validator rules; extraction script tests
  (scripted LLM) for Issue-3/4/5 utterances producing the expected
  outputs (O-hedge/overlap, O-shared-ip, O-laundering-assessment);
  persistence round-trip; flag OFF → no field.
- **E2E**: covered by Slice-1 final E2E.
- **Rollback**: flag off.
- **DoD**: contract created+persisted for happy paths; omitted safely on
  invalid proposals; OFF-identity test; extraction tests for the three
  canonical utterances; **docstring/boundary note**: accepted outputs ≠
  complete extraction.

### WP-GSP-3 — Observation → Required-Output Association

- **Objective**: every observation knows which output it was collected
  for (association only — never satisfaction).
- **Current implementation point**: observation append
  (`agent_loop.py:1369-1377`); `project_observation` (`:158`).
- **Files**: `app/agent_loop.py`.
- **Runtime flow changes**: at observation creation, `for_output =
  state.goal_contract.step_output_map.get(step.step_id)` (map maintained
  from WP-GSP-6 Slice 2; in Slice 1, before planner coverage exists, the
  map is seeded by a **deterministic evidence-type matcher**: each
  adopted plan step's binding `supported_evidence_types ∩
  output.evidence_requirements` — first match wins; ambiguous multi-match
  → all matched outputs recorded as a list `for_outputs`). The
  association is written regardless of outcome (including
  `unsupported`/`empty` observations — those carry the postcondition
  inputs for UNSUPPORTED/NOT_SATISFIED).
- **Invariant encoded here**: association ≠ satisfaction — the field is
  named `for_output(s)` and carries no satisfaction semantics.
- **System-controlled logic**: deterministic matching at adoption +
  observation time; LLM never assigns associations.
- **Compatibility**: additive observation key; OFF/None-contract →
  key absent (legacy shape preserved for OFF identity).
- **Failure modes**: step matches no output (unmapped step) →
  `for_output=None` → observation is *irrelevant evidence* by
  construction (I-2/I-4) — invisible to completion and composer under
  flag ON. This is the Issue-5 killer.
- **Test strategy**: unit — matcher determinism; multi-match; no-match;
  OFF identity.
- **DoD**: association present for all observations under flag ON with a
  contract; no-match case proven (unrelated-success invisibility unit
  test); OFF identity test.

### WP-GSP-4 — Deterministic Postcondition Evaluator + Satisfaction

- **Objective**: system-only semantic postcondition evaluation:
  association + payload facts → output satisfaction.
- **Current implementation point**: new `app/goal_postconditions.py`;
  invoked from the loop right after each observation append (and once at
  adoption for pre-`UNSUPPORTED` outputs from Slice 2).
- **Files**: new module + `app/agent_loop.py` (invocation) + tests.
- **Evaluator contract**: pure function
  `evaluate(output: RequiredOutput, observation: dict) -> OutputSatisfaction (+ detail)`.
  Never inspects LLM reasoning; only the projected observation payload.
  Initial deterministic postconditions:

  | Evidence requirement | Postcondition (all also require outcome=SUCCESS) |
  |---|---|
  | opposite_trade_relations (+ subject_inclusion) | candidates list present; **subject-inclusion check**: any candidate pair contains `subject_ref` → SATISFIED (positive) / determinate zero-match with complete scope (`truncated` false) → SATISFIED (negative polarity, detail="未发现涉及 <subject> 的候选对") / `truncated=true` → PARTIALLY_SUPPORTED |
  | network_relations | related_accounts_count present and > 0 → SATISFIED; zero + complete → SATISFIED (negative) |
  | shared_device_evidence | shared_devices non-empty → SATISFIED |
  | shared_ip_evidence (post-1b) | per-account shared_ips echoed; complete scope + all empty → SATISFIED (negative); any non-empty → SATISFIED (positive); field absent → UNRESOLVED (cannot distinguish "no data" from "not returned") |
  | withdrawal_records / transaction_records | record set present, `complete` flag honored; evidence_missing=true → NOT_SATISFIED (feeds RE_PLAN/UNRESOLVED per Slice 2) |
  | ml/rule/graph explanation | explanation payload present and non-gap → SATISFIED; evidence_missing=true → NOT_SATISFIED |
  | policy_references | payload present per existing two-block contract → SATISFIED |
  | outcome=UNSUPPORTED on the observation → UNSUPPORTED; outcome=EMPTY with complete scope → SATISFIED (negative) per P20; outcome=integration_error → leave PENDING (retry/re-plan handles; never UNRESOLVED — outage ≠ no-data) |

  **Deterministic-negative rule (normative)**: complete-scope zero-match
  is `SATISFIED` with negative polarity — it is a grounded answer. This
  rule is what prevents RE_PLAN loops on true negatives.
- **Invariant encoded**: I-1 (SUCCESS never auto-satisfies — the
  evaluator runs on payload facts, and a SUCCESS observation can yield
  NOT_SATISFIED/UNRESOLVED); I-2 (unassociated observations never reach
  the evaluator for any output).
- **Compatibility**: evaluator runs only when contract present.
- **Failure modes**: wrong postcondition (risk §18-P0) → mitigated by
  per-type fixture tests from the three lineage payloads; evaluator bug
  writing wrong status → single write-site, unit-pinned transitions.
- **Test strategy**: L1 table tests per evidence type × outcome ×
  subject-inclusion × truncation; the six-state enum covered; lineage
  fixtures (Issue-3 cross-account payload; related-account payload with
  and without 1b; signal_explain evidence_missing).
- **E2E**: via Slice-1 acceptance runs.
- **DoD**: evaluator implemented for all declared types; deterministic
  negative covered (unit + later L2 GSP-03); no LLM in the path; write
  site single; OFF identity.

### WP-GSP-5 — Composer Semantic Scope Preservation + Goal Outcome

- **Objective**: the answer contains exactly what the accepted contract's
  outputs ground — and honest boundaries for what they don't.
- **Current implementation point**: `compose_loop_response`
  (`agent_loop.py:2023`), `_case_level_answer_lines` (`:1872`),
  `_finding_answer_lines` (`:1984`), closure block (leaks raw error
  codes today).
- **Files**: `app/agent_loop.py`, `app/config.py` (derivation helper may
  live on contract), tests.
- **Runtime flow changes** (flag ON, contract present):
  1. `GoalContract.goal_outcome()` derivation: all SATISFIED → COMPLETE;
     ≥1 SATISFIED and ≥1 terminal-non-satisfied → PARTIAL; all
     UNSUPPORTED → UNANSWERABLE; dependency outage path → BLOCKED (kept
     from existing BLOCK semantics).
  2. Answer body selection: an observation is **answer-eligible** iff it
     is associated to an output whose satisfaction is
     SATISFIED/PARTIALLY_SUPPORTED (I-2/I-4). Unassociated or
     NOT_SATISFIED/UNRESOLVED observations never enter the body.
  3. Per-output rendering: satisfied outputs render their grounded
     content **with polarity discipline** (Issue 3: "未发现涉及 U00010
     的候选对；候选均为其关联账号之间" — never "有" without subject
     grounding); UNSUPPORTED outputs render their capability-boundary
     detail ("当前系统不支持…核查"); UNRESOLVED renders "当前证据不足以
     确定…" (P14 distinct-states discipline).
  4. Closure: step recap limited to steps associated with satisfied
     outputs; **raw error-code observation lines removed** from
     user-visible closure (AL-3.5.2 leak); tool outcomes remain visible
     in Activity (audit surface unchanged).
  5. Existing per-payload branches (withdrawal tables, policy two-block,
     related-account narrative) are reused as the per-output renderers —
     content rules unchanged; only **selection/eligibility** is new.
- **System-controlled logic**: eligibility filtering + boundary rendering
  are deterministic; LLM never chooses what becomes the answer.
- **LLM responsibilities**: none at composition (deterministic composer,
  P16).
- **Compatibility**: flag OFF → existing composer path byte-identical;
  flag ON with contract=None → falls back to legacy path (bounded).
- **Failure modes**: over-filtering (valid content dropped) → mitigated
  by renderer reuse + regression matrix; under-filtering (unrelated
  content rendered) → blocked by eligibility rule + unit pin.
- **Test strategy**: L1 — eligibility table; polarity rendering
  (Issue-3 negative); boundary rendering (UNSUPPORTED/UNRESOLVED);
  error-line removal; OFF identity.
- **E2E**: L2 A/B/C/D/E below; L3 journeys 1–4 below.
- **DoD**: per-output rendering live; Issue-3/4/5 composer behaviors each
  covered by a pin; closure leak fixed; flag OFF identity; **L2 + L3
  verified** (user-visible change → browser mandatory).

**Slice-1 exit criteria**: L2 scenarios GSP-01/02 (+ GSP-03 if 1b landed)
pass on the canonical stack; regression matrix (§16) like-for-like;
L3 journeys 1–4 pass; flag OFF identity tests green.

## 8. Slice 2

### WP-GSP-6 — Planner Coverage Admission

- **Objective**: planner works per required output: every output gets an
  executable, capability-supported path, or an explicit UNSUPPORTED.
- **Current implementation point**: `_make_plan`/`planner.plan`
  (`agent_loop.py:1495-1507`); `_adopt_plan`; `eligible_skills_for_finding`.
- **Files**: `app/agent_loop.py`, `app/planner_v2.py` (prompt block +
  structured inputs), `app/skills.py` (admission helper), tests.
- **Runtime flow changes**: at plan adoption (initial and re-plan):
  1. Prompt (flag ON) additionally lists the contract outputs (id, claim,
     evidence requirements, subject, scope) and instructs step mapping
     (outputs→steps, one or more steps per output).
  2. **Deterministic coverage check** after the plan returns: every
     output must be (a) covered by ≥1 step whose binding declares one of
     its evidence requirements **and** whose scope/subject constraints
     hold (subject-inclusion constraint validated at postcondition time,
     scope validated here: e.g. relationship_graph requirement cannot be
     planned by a finding-level-only path), or (b) explicitly marked
     UNSUPPORTED in the plan response (new optional planner JSON field,
     validated: a declared-unsupported output must indeed have no
     declaring binding — the LLM cannot invent unsupportability, the
     registry confirms it). Uncovered and undeclared → plan rejected
     (existing `PLAN_CONTRACT_VIOLATION`/`NO_ELIGIBLE_SKILL` bounded
     paths); on final failure the loop terminates with per-output
     boundary rendering (WP-GSP-5).
  3. `step_output_map` written at adoption (replacing the Slice-1
     evidence-type seed matcher, which remains the fallback for
     contract-less runs).
- **Boundary (normative)**: coverage is against the **accepted
  contract**; it can never retro-fix extraction misses (design §1).
- **Test strategy**: coverage table tests (covered/declared-unsupported/
  uncovered-rejected; invented-unsupported-rejected); Issue-3 scenario
  (O-overlap declared UNSUPPORTED at planning; O-hedge planned);
  eligibility parity with `eligible_skills_for_finding` (single truth).
- **E2E**: L2 scenarios rerun; S09-shape regression.
- **DoD**: coverage admission live; no nearest-capability substitution
  possible (pin: shared-IP requirement cannot plan
  related_account_analysis); flag OFF identity; L2 green.

### WP-GSP-7 — RE_PLAN / Loop Preservation

- **Objective**: re-plans work the unsatisfied outputs and cannot repeat
  failed paths.
- **Current implementation point**: `_re_plan` (`:1509-1527`), decision
  proposer + policy (`:580-645`, `:762-777`), existing caps (retries per
  step; `plan_revisions >= 2`; iteration bound).
- **Files**: `app/agent_loop.py`, `app/goal_postconditions.py` (helpers),
  tests.
- **Runtime flow changes**:
  1. Re-plan digest (flag ON) additionally carries machine facts:
     unsatisfied/partial outputs (id + claim + reason), outputs marked
     UNSUPPORTED (never re-planned), and the set of already-attempted
     `(tool_name, focus/subject)` pairs whose postcondition yielded
     NOT_SATISFIED/UNSUPPORTED.
  2. **Deterministic filter at adoption**: a re-plan step whose
     (binding, subject) duplicates an attempted-and-not-satisfied pair
     is rejected (re-plan regenerated ≤2 times, existing cap; then
     terminal with per-output rendering — WP-GSP-5).
  3. Completion: STOP dominance consults the per-output regime (all
     outputs terminal) — unchanged mechanics, new predicate.
  4. BLOCK semantics unchanged; integration-error → PENDING outputs stay
     PENDING (outage ≠ no-data).
- **Compatibility**: caps and controllers reused verbatim; no second loop
  engine; OFF identity.
- **Failure modes**: over-aggressive filtering (legitimate second look
  blocked) → filter keys on (binding, subject) pairs, not tool globally;
  pinned by tests.
- **Test strategy**: Issue-5 lineage replay (explain→related→explain
  alternation must terminate with per-output boundaries, not a
  shared-device answer); repeated-unsupported corner (withdrawal
  drilldown on F1) terminates at first re-plan rejection; convergence
  guard interplay; OFF identity.
- **E2E**: L2 F (re-plan no-progress termination); L3 journey 4.
- **DoD**: semantic non-progress loop eliminated (pinned); unsatisfied
  outputs preserved across re-plans (pinned); caps reused (no new
  controller); flag OFF identity; L2+L3 verified.

## 9. Data / State / Schema

- **TaskStoreV2**: **no schema change, no migration** — `save_agent_state`
  serializes the whole pydantic state; `goal_contract` is additive and
  simply appears in stored JSON (old tasks: field absent → `None` on
  load; recovery paths treat None as legacy).
- **ToolResult**: **unchanged** (association lives on the observation;
  1b extends a tool's `data` payload within its existing contract).
- **Second goal store**: none (single carrier: `AgentLoopState`).
- **Old-task recovery**: resume with `goal_contract=None` → legacy paths
  (same rule as G1's legacy evidence-kind mapping).
- **Flag OFF**: nothing new written anywhere.

## 10. Capability Declaration

See WP-GSP-1. Normative notes:
- `EvidenceType` is closed; adding a value REQUIRES a binding declaration
  plus payload tests (no vocabulary growth without executable backing).
- Claim labels (`RequiredOutput.claim`) are NOT frozen and are never
  admission inputs (the admission input is the evidence requirement).
- Two distinct runtime situations, encoded and tested: **"no binding
  declares X" → UNSUPPORTED at planning** vs **"binding declares X, RP
  data empty" → SATISFIED-negative / EMPTY at execution** (capability
  availability ≠ data availability).
- Non-negotiable pins: `shared_ip ≠ shared_device` (distinct enum values;
  related_account without 1b does not declare shared_ip);
  `money_laundering_assessment` has no declaring binding (asserted);
  no nearest-capability fallback path exists in code.

## 11. Postcondition / Satisfaction Model

See WP-GSP-3/4. Normative notes:
- Single write-site: the evaluator, invoked at observation processing.
- Evaluator inputs: `RequiredOutput` + projected observation dict only.
- Deterministic-negative rule with completeness conditions (no
  `truncated`, no integration error, field present) — otherwise downgrade
  (PARTIALLY_SUPPORTED / UNRESOLVED), never silent SATISFIED.
- LLM decisions (proposer) may propose CONTINUE/RE_PLAN/STOP but
  satisfaction writes and STOP-dominance predicate are system-owned
  (P15/P16 alignment).
- **Evidence Requirement Semantics (contract refinement, 2026-10-08)**:
  `RequiredOutput.evidence_requirements` is an ANY-set of alternative
  evidence categories — matching any one category makes an observation
  eligible for that output's postcondition evaluation; it never by
  itself satisfies the output. The implemented first-determinate-
  requirement loop IS this ANY semantics. Known flagged edge (follow-up
  correction candidate, NOT changed in this round): when one category
  yields PARTIAL and a later-listed category would yield SATISFIED, the
  first-wins loop was list-order-sensitive. **RESOLVED (2026-10-08,
  GSP-4 correction)**: `evaluate_output` now collects ALL determinate
  category verdicts and aggregates with the fixed precedence
  (SATISFIED > PARTIALLY_SUPPORTED > UNRESOLVED, canonical tie-break) —
  requirement order never influences the verdict. See design doc §4.1a
  for the full contract, decision record, examples, and consumer
  semantics (including the GSP-6 rule: planner coverage = at least one
  legal evidence path, coverage ≠ satisfaction).
- **Assessment claim-kind clarification (2026-10-08)**: an ASSESSMENT-
  kind output is planning-covered by the same evidence-requirement rules
  as any other output — claim_kind never gates capability coverage (the
  removed blanket `assessment → NO_ELIGIBLE_CAPABILITY` gate contradicted
  the orthogonal claim/evidence semantics). The JUDGMENT stays
  unadjudicable downstream: GSP-4 evaluates assessment outputs
  UNRESOLVED (`judgment_not_adjudicable`) with the gathered evidence
  recorded for the analyst.

## 12. Planner / Loop Integration

Slice 2 (WP-GSP-6/7). Additional integration notes:
- `plan()` signature gains `goal_contract=None` keyword (additive);
  prompt block flag-gated; the planner JSON may include
  `unsupported_outputs: [output_id]` (validated against the registry —
  LLM cannot invent support **or** unsupportability).
- Existing caps unchanged: `AGENT_LOOP_MAX_ITERATIONS`,
  `AGENT_LOOP_MAX_RETRIES_PER_STEP`, `plan_revisions >= 2` terminal.
- BLOCK/integration paths unchanged (outputs stay PENDING under outage).

## 13. Composer Integration

Slice 1 (WP-GSP-5). Turn-path composer (`compose_response`,
`investigation_service.py:148`) gains the same per-output eligibility in
a follow-up package (turn turns today rarely carry contracts; documented
limitation). Loop closure error-line removal is unconditional hygiene
(under both flag states — it is a leak fix, not GSP semantics; verified
by existing closure tests updated accordingly).

## 14. Testing & E2E Strategy

- **L1 Deterministic/Unit** (every WP): declarations vs payload fixtures;
  contract validation; association matcher; postcondition tables (all
  six states); goal-outcome derivation; coverage admission; re-plan
  filter; composer eligibility/polarity/boundary rendering; OFF identity
  per WP.
- **L2 API E2E — canonical stack only** (3001→8001→RP 8000; never the
  temporary QA stack), each run on fresh investigations:
  - A. Cross-account false positive (Issue 3 utterance)
  - B. Unsupported shared-IP capability (Issue 4 utterance)
  - C. Complete negative shared-IP result (post-1b)
  - D. Existing valid finding regression (S04/S06 utterance)
  - E. Case-level valid flow (S08/S12 utterance)
  - F. RE_PLAN no-progress termination (Issue 5 lineage replay)
  Recorded per run: contract outputs, per-output states, goal outcome,
  tools, associations, response.
- **L3 Browser E2E** (real Chrome, :3001; guard/GSP flags as configured):
  1. U00010 hedge-trade question → subject-aligned answer (no false "有")
  2. U00010 shared-IP question → capability boundary (or negative answer
     post-1b) — never device-as-IP
  3. Existing valid finding regression (S06 journey unchanged)
  4. Complete negative-result path → "未发现…" rendered, no RE_PLAN churn
  Screenshots per journey; existing clarification/blocked rendering only
  (no UI redesign).

## 15. Acceptance Scenarios (precise expectations)

**GSP-01 — Cross-account false positive.** User: "这个账号是否有对冲交易？"
Subject U00010; tool SUCCESS with candidates, none involving U00010.
Expected: observation SUCCESS; output associated; postcondition →
**SATISFIED (negative polarity)** when scope complete (design §4.3
determinate-negative) — therefore overall **COMPLETE is allowed only
with the negative answer rendered**; if retrieval `truncated` →
PARTIALLY_SUPPORTED → overall PARTIAL with the budget boundary; in no
case may the response state "U00010 有对冲交易"; response must be
subject-scope aligned (render the subject-inclusion statement). (Note:
this refines the earlier draft's "UNRESOLVED" expectation — a complete
determinate negative is a valid answer per the deterministic-negative
rule; UNRESOLVED is reserved for indeterminate cases.)

**GSP-02 — Unsupported capability.** User: "这个账号有没有共享 IP？"
No binding declares `shared_ip_evidence` (pre-1b). Expected: output =
UNSUPPORTED at plan validation; **no related_account fallback may run
for that output** (if related-account runs for a *different* output it
must not render as the IP answer); overall = UNANSWERABLE (single-output
case) or PARTIAL; response states the capability boundary explicitly
("当前系统不支持共享 IP 核查"); no device-renamed-as-IP content.

**GSP-03 — Complete negative result.** Precondition: WP-GSP-1b landed
(declaration + payload echo). User: shared-IP question; tool SUCCESS;
complete scope; `shared_ips` all empty; not integration error/partial.
Expected: Tool SUCCESS; negative evidence complete; output = SATISFIED
(polarity NEGATIVE); overall COMPLETE; response "未发现共享 IP 关系"
(+ device relation as separate declared evidence if contracted); **no
RE_PLAN may continue past a complete determinate negative**.

## 16. Regression Strategy

| Group | Scenarios | Regime |
|---|---|---|
| GSP-specific acceptance | GSP-01/02/03 + S11×N≥5 (flag ON; with G1 ON: S11 remains G1-guarded — GSP adds the plan-validation second net; assert 0 wrong-target execution, 0 unrelated-answer rendering) | per-acceptance §15 |
| Existing regression | S04, S05, S06, S07, S08, S10, S12, S14 ×N≥3, flag ON — like-for-like vs pre-GSP behavior (terminal statuses, evidence key facts, no new blocks/clarifications) | AL-3.5.1 discipline |
| Known pre-existing failures | `tests/test_agent_loop_v1.py` 2 re-plan failures — remain, never re-attributed to GSP | recorded baseline |
| Infrastructure-skipped | S13/S15/S16/S18 (unchanged; no GSP attribution) | recorded |

## 17. Telemetry

Extend `AgentTelemetryEvent` with a GSP group (pattern: target-validity
group): events `goal_contract.accepted` (outputs count, goal_type,
subject_ref), `goal_output.created` (output_id, claim, evidence list,
subject_ref, scope), `capability.admitted|rejected` (step type, binding,
evidence types, output_id, reason_code), `postcondition.evaluated`
(output_id, evidence_type, satisfaction, reason_code, tool_name),
`goal_output.transition` (output_id, from, to), `goal_outcome.derived`
(outcome, per-output summary counts). Fields only: ids, enums, reason
codes, tool/step names, decision source — **never** chain-of-thought,
prompts, or raw payloads. Emitter: loop + plan validation sites;
flag-gated.

## 18. Risks

| Priority | Risk | Mitigation |
|---|---|---|
| P0 | Postcondition wrong (false SATISFIED → false answer; false negative-state → loop) | per-type fixture tables from real lineage payloads; single write-site; deterministic-negative completeness conditions pinned |
| P0 | Capability declaration drift (declared type not actually produced) | L1 declaration-vs-payload tests; vocabulary growth rule (declaration + payload test required) |
| P0 | Extraction incompleteness mistaken for solved | §1/§12 boundary language normative; acceptance never claims request-completeness |
| P1 | Composer over-filtering (valid content dropped) | renderers reused unchanged; regression matrix; OFF identity |
| P1 | Composer under-filtering (unrelated content leaks) | eligibility rule + dedicated pin tests |
| P1 | Subject/scope confusion re-entering via shortcuts | schema separation + validation; review checklist |
| P1 | Re-plan filter too aggressive | filter keys (binding, subject) pairs; termination still bounded by existing caps |
| P1 | AL-3 behavior regression | §16 matrix like-for-like; flag OFF identity per WP |
| P2 | Persistence/recovery mismatch on old tasks | None-contract legacy rule + resume tests |
| P2 | Telemetry noise | flag-gated, bounded fields |

Standing reminder (normative): **GSP is semantic preservation /
execution governance, not domain truth adjudication** — the evaluator
answers "do these facts satisfy this output's definition", never "is the
account actually laundering".

## 19. Rollback / Feature Flag

- `ENABLE_GOAL_SEMANTIC_CONTRACT: bool = False` (`app/config.py`, same
  section/table as sibling flags).
- Readers: goal understanding (prompt + validation), plan validation
  (Slice 2), observation association, postcondition invocation,
  `criteria_satisfied`, composer. OFF → all paths bypass; behavioral
  compatibility when flag OFF is the standard (existing runtime behavior
  preserved — same wording discipline as the G1 QA doc; not a byte-level
  source claim).
- Rollback: flip flag (no schema rollback needed — additive persisted
  fields are ignored when OFF).
- Probes/E2E run flag ON via env on the :8001 process; canonical backend
  restored to default OFF afterwards (AL-3.5.1 discipline).

## 20. Implementation Readiness Gate

1. Design assumptions locked — **YES** (design v1 + the one material
   correction recorded in §4: shared_ips absent from current
   related_account payload).
2. Schema locked minimal — **YES** (§9: models, enum values, carrier).
3. Capability declaration location — **YES** (`StepToolBinding` field +
   `STEP_TOOL_MAP`; initial declarations enumerated).
4. Postcondition boundary — **YES** (per-type table, §11/WP-GSP-4).
5. Composer integration point — **YES** (§13/WP-GSP-5).
6. Planner integration point — **YES** (§12/WP-GSP-6, Slice 2).
7. Loop integration point — **YES** (observation site, adoption sites,
   criteria predicate; existing caps reused).
8. Flag/rollback — **YES** (§19).
9. API E2E DoD — **YES** (§14, canonical stack, A–F).
10. Browser E2E DoD — **YES** (§14, four journeys).

**Remaining open questions (honest, non-blocking for WP-GSP-1)**:
- O-1: turn-path planner admission timing (default: loop-first; turn
  planner admission deferred — recorded decision needed before Slice 2
  closes).
- O-2: whether event-level evidence type should be declared for
  `inspect_timeline` now (default: defer until a real scenario needs it
  — recorded).
- O-3: extraction-completeness hook UX (LLM self-check vs user
  confirmation) — future, does not gate implementation.
- O-4: S11 GSP-level acceptance threshold under G1+GSP combined flags
  (proposal: S11×N≥5 with both flags ON, 0 wrong-target execution AND 0
  unrelated-answer rendering — confirm at Slice-1 exit).

**Verdict: READY to start WP-GSP-1.** No blocking open questions.

## 21. Non-goals / Known Limitations

- GSP does not guarantee requirement-extraction completeness (§1).
- GSP is not capability completeness (UNSUPPORTED is honest).
- GSP is not domain truth adjudication (§18 reminder).
- GSP does not replace G1 (target admission remains G1's; the 洗钱→F1
  collision's first net stays target admission).
- Turn-path planner admission, event-level evidence type, referenced
  sets, extraction auditing — explicitly deferred.
- No new database / ToolResult schema / second state store / lexicon /
  MCP / keyword tables; nearest-capability fallback is prohibited
  outright (not a configured option).

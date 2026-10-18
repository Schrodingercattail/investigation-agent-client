# Compound Goal Extraction & Contract-less Safety — Implementation Plan v1

**Status: Draft for PMO Review — Implementation Plan Only, Not Approved for Execution**

**Date**: 2026-10-09
**Approved design basis**: `docs/plans/2026-10-09-compound-goal-extraction-contractless-safety-design-v1.md`
(Revision 2 — approved for implementation planning). This plan converts
the approved design into verifiable Work Packages. It implements
nothing; every code/test mention is planned work.

---

## 1. Executive Summary

Five Work Packages land the approved Revision-2 design:

- **WP-CG-3 (first)** — the contract-less safety net: an additive
  `goal_contract_decision` provenance record, the genuine-AMBIGUOUS
  clarification-shaped bounded disposition, the composer
  completion-claim guard, and `semantic_basis` decision-row marking.
  Lands the safety invariants (INV-3/7/8/13/14) even before extraction
  changes exist.
- **WP-CG-1** — the `COMPOUND` extraction protocol: new resolution
  status, representation validator branch, prompt reconciliation
  (removing the §4.4 contradiction), proposal attachment on the
  COMPOUND branch of the SAME single LLM call.
- **WP-CG-2** — contract acceptance for multi-output proposals: ≥2
  validated outputs for COMPOUND, `goal_type=None` pass-through (AD-1),
  canonical-semantic dedup (AD-3), all-or-nothing preserved.
- **WP-CG-4** — API E2E integration validation over real HTTP with a
  model-boundary seam extension (`goal_llm` scripts driving the REAL
  `understand_goal`), covering the 12 mandated scenarios plus flag-OFF
  identity.
- **WP-CG-5** — Browser E2E on the existing harness plus the
  Session-01 Trial A–E exploratory regression, real-model runs recorded
  separately from deterministic runs.

Recommended execution order: **CG-3 → CG-1 → CG-2 → CG-4 → CG-5**
(rationale in §5). No new flag, no new store, no second LLM call, no
GSP-1…7 semantic changes.

## 2. Approved Design Baseline (frozen decisions)

Binding constraints restated from approved Revision 2 (not re-opened
here): single-call `COMPOUND` extraction (LLM proposes / system
disposes; no prompt-only fixes; no prose-as-outputs); heterogeneous
contracts take `goal_type=None`; per-RequiredOutput fields are each
item's semantics; all-or-nothing acceptance; dedup only on full
canonical semantic equivalence; contract content immutable across
RE_PLANs; the four disposition classes COMPOUND / AMBIGUOUS
(clarification-shaped bounded termination, G1 untouched) /
LLM_EMPTY (heuristic routing allowed, never a contract, provenance
recorded) / INVALID_PROPOSAL (deterministic rejection, never mapped to
capability UNSUPPORTED); contract-less guard enforced by deterministic
composer/loop logic (never by prompt, prose, or the `semantic_basis`
label alone); `AgentStatus.COMPLETED`/`GOAL_SATISFIED` never constitute
semantic COMPLETE; reuse `ENABLE_GOAL_SEMANTIC_CONTRACT` (flag OFF =
today's baseline exactly); GSP-3/4/5/6/7 semantics, G1, and O2
untouched; no MCP/store/tools/evidence-types/ALL/groups.

## 3. As-Is Architecture / Verified Code Facts

Re-verified against the current working tree immediately before writing
this plan (HEAD `58881a9` + GSP working tree). Line numbers are
current-tree references and may shift during implementation; names are
stable.

| Fact | Location (verified) |
|---|---|
| `GoalResolutionStatus` = INFERRED/EXPLICIT/AMBIGUOUS/UNRESOLVED | `app/goal_resolution.py:71` |
| `GoalRepresentation` (single `goal_type`; validator: INFERRED/EXPLICIT require goal_type; AMBIGUOUS requires ≥2 vocab candidates; `contract_proposal: dict\|None` at :213) | `app/goal_resolution.py:192-234` |
| `project()` user-safe projection (proposal excluded) | `app/goal_resolution.py:236` |
| `build_goal_contract` — all-or-nothing per-output validation, system `O1..On` ids, subject-consistency; works on ANY representation carrying a proposal (resolution-agnostic) | `app/goal_resolution.py:285-370` |
| `heuristic_goal` — deterministic single-goal classifier, never a proposal | `app/goal_resolution.py:409` |
| `_build_goal_prompt` — base single-goal rules + flag-gated `required_outputs` block ("one entry per DISTINCT requested answer (multi-part requests get multiple entries)") — the contradiction | `app/goal_resolution.py:469-528` |
| `understand_goal` — proposal extracted under flag (:622-625); **AMBIGUOUS branch drops the proposal (:629-636)**; INFERRED+valid goal_type attaches it (:637-644); exceptions/empty → heuristic (:653-656) | `app/goal_resolution.py:563-656` |
| Loop goal checkpoint — resolver call, contract build, `state.goal_contract`, accepted/rejected telemetry | `app/agent_loop.py:1119-1140` |
| `StopReason.CLARIFICATION_NEEDED = "clarification_needed"`, currently used by the TARGET-clarify path (G1/resolver), rendered as bounded blocked terminal | `app/agent_loop.py:120`, used `:1289` |
| `criteria_satisfied()` — baseline ANY-success rule | `app/agent_loop.py:585` |
| Decision recording — proposer prose into `state.decisions`, mirrored to Activity verbatim | `app/agent_loop.py:1719` |
| `compose_loop_response` — contract branch → `_compose_gsp_response`; else legacy branches + `_STATUS_PHRASES` ("The investigation completed.") | `app/agent_loop.py:2657`, `:2096` |
| `_compose_gsp_response` / `_OUTCOME_ZH` — per-output compose + `derive_goal_outcome`-based completeness line (contract-only) | `app/agent_loop.py:2607`, `:2599` |
| Activity projection `project_task_activity` (additive GSP fields already served) | `app/agent_loop.py:2886` |
| Scripted-session seam (`AGENT_LOOP_SCRIPTED_SESSION_FILE`; sections `plans`/`decisions`/`goal`; `_ScriptedSessionGoalResolver` returns a representation TEMPLATE, bypassing `understand_goal`) | `app/agent_loop.py:927-1063` |
| Multi-output readiness downstream: `GoalContract.goal_type: str\|None` with no runtime consumer; GSP-6 coverage, GSP-3 association, GSP-4 evaluation, GSP-5 compose, GSP-7 preservation all iterate `contract.outputs` | design §4/§6 audit; `tests/test_gsp2_goal_contract_v1.py::test_case_d` (3-output contract accepted) |
| Existing test surface to extend (not replace): gsp1–gsp7 suites, `test_al351_*`, `test_agent_activity_api_v1.py` (projection whitelist), `test_gsp7_replan_api_e2e_v1.py` (seam-based API E2E pattern) | `backend/tests/` |

Discrepancy note (design vs code): none material found. One nuance
recorded for implementers — `build_goal_contract` is already
resolution-agnostic, so WP-CG-2's acceptance work is rule addition, not
plumbing.

## 4. Target Architecture and Runtime Flows

As approved (design §9): one goal-understanding call yields INFERRED
(single-goal contract, unchanged) / COMPOUND (N-output contract, new) /
AMBIGUOUS (clarification-shaped bounded stop, new disposition) /
UNRESOLVED (unchanged) / heuristic-routed on empty-or-invalid
(provenance recorded, guarded legacy execution). Contract-less runs are
guarded by deterministic composer/loop logic. Full flow diagrams: design
§4.3 (As-Is) and §9 (To-Be) — authoritative, not duplicated here.

## 5. Work Package Overview and Dependency Graph

```
WP-CG-3 (safety net) ──┐
                       ├─► WP-CG-4 (API E2E integration) ──► WP-CG-5 (Browser + exploratory)
WP-CG-1 (extraction) ──┴─► WP-CG-2 (acceptance/dedup) ──────┘        ▲
   └──────── (CG-2 pure-rule L1s may start during CG-1) ─────────────┘
```

Dependency analysis (re-verified, not copied):

- **CG-1 → CG-2 (hard, integration-level)**: the ≥2-rule and dedup run
  inside the COMPOUND acceptance path, which exists only once CG-1
  emits COMPOUND with an attached proposal. CG-2's PURE rules (dedup
  matrix, rejection table) are pure functions over fixtures and MAY be
  developed and unit-tested in parallel with CG-1.
- **CG-3 ⊥ CG-1/CG-2 (no hard dependency)**: the guard keys on "flag
  ON + contract None + terminal status" and the disposition keys on
  today's AMBIGUOUS — both exist now. File-disjoint: CG-3 edits
  `app/agent_loop.py` (checkpoint/decision/projection/composer);
  CG-1/2 edit `app/goal_resolution.py`. Parallelizable by file if
  desired.
- **Recommended order CG-3 first**: it lands the anti-silent-drop and
  anti-overclaim invariants immediately (security-first); every
  intermediate merged state is safer than the last. Alternative order
  (CG-1 → CG-2 → CG-3) is functionally acceptable but leaves the
  silent-drop hole open longer; PMO may pick either — the DAG itself
  does not change.
- **CG-4 after all three**: final integration validation, with a
  **bridge smoke** requirement after EACH earlier WP (run the existing
  `test_gsp7_replan_api_e2e_v1.py` seam-based suite + the new CG tests
  accumulated so far over real HTTP) so wiring errors surface per-WP,
  not at the end.
- **CG-5 last**: builds on the validated API paths; browser journeys
  reuse them end-to-end.

Tests are written WITH their WP (per-WP L1; CG-4 owns the API E2E
file; CG-5 owns the browser file) — nothing is deferred to a final
testing phase.

## 6. WP-CG-3 — Contract-less Safety and Decision-language Guard

1. **Objective**: make every contract-less run honest — no silent
   subgoal drop presented as completion, no prose-based completeness
   claims, provenance recorded, genuine ambiguity bounded-stopped.
2. **Scope**: loop goal checkpoint provenance record; AMBIGUOUS
   disposition (flag ON); legacy-composer completion-claim guard;
   decision-row `semantic_basis`; projection additive fields.
3. **Out of scope**: any extraction change (CG-1/2); contract-path
   composer changes (GSP-5 untouched); turn path; UI work.
4. **Dependencies / preconditions**: none (lands on today's statuses).
   Flag-gated throughout.
5. **Implementation tasks**:
   a. Additive `goal_contract_decision` record on `AgentLoopState`
      `{outcome, reason}` written at the goal checkpoint
      (`agent_loop.py:1119-1140`) with outcomes `contract_accepted` |
      `no_proposal` | `invalid_proposal:<reason>` (reason =
      existing `build_goal_contract` code) | `llm_empty` |
      `heuristic_single_goal` | `ambiguous_not_executed`.
   b. AMBIGUOUS disposition (flag ON): at the checkpoint, an AMBIGUOUS
      representation terminates the run before planning with
      `AgentStatus.BLOCKED` + `StopReason.CLARIFICATION_NEEDED`
      semantics (reusing the existing bounded-terminal rendering shape
      of `:1289`) and an honest, actionable message; G1 decisions
      unaffected (disposition applies only to goal-level AMBIGUOUS
      that reached the checkpoint un-gated — verified: G1 never
      consumes goal resolution). Flag OFF: today's flow unchanged.
   c. Composer guard: in `compose_loop_response`'s legacy branch
      (`:2657+`), when flag ON and `state.goal_contract is None` and
      terminal COMPLETED → append the bounded boundary line (no
      completeness claim, no fabricated item list; final zh wording at
      implementation, AD-6 constraints). `_STATUS_PHRASES` itself is
      NOT edited — the guard is a branch in the composer so flag OFF
      stays byte-identical.
   d. Decision-row marking: when recording decisions
      (`:1719`), add `semantic_basis: "contract:<id>" | "none"`;
      projection serves it additively.
   e. Projection: add `goal_contract_decision` (additive key).
6. **Files (verified existing)**: `app/agent_loop.py` (checkpoint,
   decision append :1719, composer :2657, projection :2886).
7. **New files**: `backend/tests/test_cg3_contractless_safety_v1.py`.
8. **L1**: guard table flag ON×{contract, no-contract}×{COMPLETED,
   FAILED, BLOCKED} → exact closure text; flag OFF byte-identical
   legacy text; AMBIGUOUS disposition → blocked terminal, planner
   never runs, zero observations; decision record outcomes for
   accepted/no_proposal/invalid_proposal/llm_empty/
   heuristic_single_goal; `semantic_basis` values; projection additive
   only (extends the whitelist test additively).
9. **API E2E**: bridge smoke only (existing seam suite stays green);
   full scenarios in CG-4.
10. **Browser E2E**: n/a this WP (CG-5 journeys exercise it).
11. **Flag / compat**: all new behavior under
    `ENABLE_GOAL_SEMANTIC_CONTRACT`; OFF = baseline exactly.
12. **Failure handling / rollback**: flag OFF; additive fields are
    ignored by old readers (whole-model JSON persistence; no
    migration).
13. **Acceptance criteria**: INV-3/7/8/13/14 each pinned by a named
    test; contract-less COMPLETED runs contain no completeness claim
    in `task_response_text`; AMBIGUOUS runs never reach the planner.
14. **DoD**: L1 green; bridge smoke green; flag-OFF identity green;
    projection whitelist updated additively; QA record section
    separates tested levels.
15. **Evidence**: test file + run output; before/after response text
    samples for one guarded closure; projection diff.
16. **Unlocks**: WP-CG-4 (and removes the recorded Session-01 failure
    shape for misrouted-AMBIGUOUS immediately).

## 7. WP-CG-1 — Compound Extraction Protocol

1. **Objective**: the SAME single goal-understanding call can return a
   validated `COMPOUND` outcome carrying an N-output proposal.
2. **Scope**: `GoalResolutionStatus`; `GoalRepresentation` validator;
   `_build_goal_prompt` reconciliation; `understand_goal` branch
   logic; projection compatibility.
3. **Out of scope**: acceptance rules (CG-2); loop disposition (CG-3,
   already landed); heuristic changes; any second LLM call.
4. **Dependencies / preconditions**: none hard (CG-3 recommended
   first). Builds only on verified structures (§3).
5. **Implementation tasks**:
   a. `GoalResolutionStatus.COMPOUND` (naming follows the existing
      enum convention).
   b. Representation validator: COMPOUND requires `goal_type=None`
      (AD-1) and a structurally present `contract_proposal` with a
      list of ≥2 raw entries (structural only — field-level
      validation stays in `build_goal_contract`); INFERRED/EXPLICIT/
      AMBIGUOUS/UNRESOLVED rules unchanged.
   c. Prompt reconciliation (flag-gated block only): compound
      multi-part requests → `goal_resolution: "COMPOUND"` + N entries
      (the existing "one entry per DISTINCT requested answer" wording
      stays); AMBIGUOUS redefined to genuine which-one ambiguity;
      single-goal JSON shape and INFERRED semantics byte-compatible;
      flag OFF → prompt byte-identical to today.
   d. `understand_goal`: recognize COMPOUND (flag ON) → attach the
      proposal (mirroring the INFERRED attach at :637-644);
      AMBIGUOUS branch unchanged (still drops — genuine ambiguity has
      no outputs); COMPOUND seen while flag OFF → bounded fallback to
      heuristic (defensive; OFF never emits the new prompt).
      Exceptions/empty → heuristic with the CG-3 record showing
      `llm_empty`/`no_proposal`.
   e. `project()`: COMPOUND projects user-safely (goal_resolution
      value; proposal still excluded).
6. **Files (verified existing)**: `app/goal_resolution.py` (:71,
   :192-234, :469-528, :563-656).
7. **New files**: `backend/tests/test_cg1_compound_extraction_v1.py`.
8. **L1** (scripted-LLM payloads against the REAL `understand_goal`):
   single-goal INFERRED/EXPLICIT healthy paths unchanged; COMPOUND+2
   and +3 outputs parsed and attached; swapped-order / enumerated /
   compound-verb payload shapes all yield COMPOUND+N; a one-step
   multi-evidence single goal stays ONE output (not over-split);
   multi-evidence-requirements inside one output are not split;
   genuine AMBIGUOUS payload is NOT misread as COMPOUND and carries no
   proposal; empty/invalid JSON → heuristic (no proposal);
   `goal_type=None` COMPOUND representation validates; flag OFF → no
   COMPOUND ever, prompt byte-identical; `project()` excludes the
   proposal for COMPOUND.
9. **API E2E**: bridge smoke; full scenarios in CG-4.
10. **Browser E2E**: n/a (CG-5).
11. **Flag / compat**: COMPOUND emission only under flag; OFF = today.
12. **Failure handling / rollback**: flag OFF; malformed COMPOUND
    payloads fall back to heuristic (bounded).
13. **Acceptance criteria**: the extraction table above green;
    AMBIGUOUS still drops proposals; single-goal byte-compat.
14. **DoD**: L1 green + bridge smoke.
15. **Evidence**: test file + run output; prompt before/after diff
    (flag ON / OFF).
16. **Unlocks**: WP-CG-2 integration.

## 8. WP-CG-2 — Contract Acceptance and Semantic Deduplication

1. **Objective**: deterministic, all-or-nothing acceptance of
   multi-output proposals into one complete GoalContract.
2. **Scope**: `build_goal_contract` rules for COMPOUND; canonical
   dedup; `goal_type=None` pass-through; rejection reasons.
3. **Out of scope**: extraction (CG-1); loop/composer (CG-3);
   partial-acceptance semantics (rejected by design).
4. **Dependencies / preconditions**: CG-1 for integration; pure-rule
   L1s startable in parallel (fixtures only).
5. **Implementation tasks**:
   a. COMPOUND acceptance: proposal present, every output passes the
      EXISTING per-output validation, and ≥2 outputs — else whole
      rejection with `invalid_proposal:<reason>` (a 1-output COMPOUND
      is malformed by definition; no silent narrowing).
   b. Canonical dedup (AD-3): collapse ONLY full equivalence —
      normalized claim (whitespace-collapsed, casefolded), equal
      claim_kind, equal subject_ref, equal scope, set-equal
      evidence_requirements (ANY-set normalization). Textual
      similarity is never equivalence. Deterministic; the collapsed
      result keeps `O1..On` identity stability; dedup events recorded
      in the rejection/acceptance reason metadata.
   c. `goal_type=None` pass-through for COMPOUND (schema already
      allows; no consumer exists — verified).
   d. Single-goal path: byte-identical behavior (1-output INFERRED
      contracts unchanged, including rejection reasons).
6. **Files (verified existing)**: `app/goal_resolution.py:285-370`.
7. **New files**: `backend/tests/test_cg2_contract_acceptance_v1.py`.
8. **L1**: two legal outputs → one contract; dedup matrix (§16-2 of
   the design: equivalence cases incl. order-permuted requirement
   sets collapse once; non-equivalence cases — different subject /
   scope / claim_kind / substantive requirement sets / near-synonym
   claims — never collapse); missing field / invalid enum / illegal
   evidence type → whole rejection; empty outputs → rejection;
   1-output COMPOUND → rejection; contract immutable-content
   serialization identity stability; historical single-goal contract
   fixtures load and validate unchanged.
9. **API E2E**: bridge smoke; full scenarios in CG-4.
10. **Browser E2E**: n/a.
11. **Flag / compat**: COMPOUND rules reachable only under flag;
    single-goal rules identical both flag states.
12. **Failure handling / rollback**: all rejections are bounded
    (reason-coded); flag OFF restores today's acceptance exactly.
13. **Acceptance criteria**: the full CG-2 L1 list green; no partial
    acceptance path exists (asserted by a negative test).
14. **DoD**: L1 green + bridge smoke; design §10-E2 rules traceable
    1:1 to tests.
15. **Evidence**: test file + run output; dedup truth-table docstring
    in the test.
16. **Unlocks**: WP-CG-4 final validation.

## 9. WP-CG-4 — API E2E Integration Validation

1. **Objective**: prove the merged chain over REAL HTTP — resolution →
   contract decision → planner/bounded path → execution → projection —
   with the model boundary scripted and everything else production-real.
2. **Scope**: one new API E2E module + the seam extension it needs;
   final validation of CG-1/2/3 integration.
3. **Out of scope**: browser (CG-5); mocking the loop; injecting final
   contracts/states; real-model assertions (CG-5 exploratory).
4. **Dependencies**: CG-1 + CG-2 + CG-3 merged; existing seam suite
   green.
5. **Implementation tasks**:
   a. Seam extension (test infrastructure in the existing seam):
      optional session section `goal_llm: [<LLM response strings>]` —
      when present, the runner's goal resolver becomes the REAL
      `understand_goal` wired to a scripted LLM (the existing
      `understand_goal(..., llm=...)` parameter), so the prompt/parse/
      branch/acceptance path runs for real with deterministic
      payloads. The existing `goal` section (representation template)
      remains supported for backward compatibility with the GSP-7
      canonical fixtures. Both sections present → `goal_llm` wins
      (real extraction is the stronger harness).
   b. The 12 mandated scenarios (below) as sequential, independent
      fresh-investigation runs (existing `test_gsp7_replan_api_e2e_v1`
      harness pattern: TestClient, rp adapter patches held across the
      background loop thread, poll to terminal).
   c. Flag-OFF identity scenario.
6. **Files (verified existing)**: `app/agent_loop.py` (seam classes
   :927-1063 — extension point), harness pattern
   `tests/test_gsp7_replan_api_e2e_v1.py`, RP fixtures
   `tests/test_api_investigations_v1.py::rp_patches`,
   `tests/test_risk_platform_adapter_v2.py`.
7. **New files**: `backend/tests/test_cg4_compound_api_e2e_v1.py`.
8. **L1**: n/a (CG-1/2/3 own L1); this WP may add seam-unit checks for
   `goal_llm` precedence.
9. **API E2E scenarios (all via real routes; seam scripts the model
   boundary only)**:
   1. single-goal healthy path (regression anchor);
   2. two-goal COMPOUND → contract with both outputs → both answered
      or honestly bounded;
   3. three-goal contract stays complete end-to-end;
   4. invalid contract content → `invalid_proposal:<reason>` →
      guarded degradation;
   5. genuine AMBIGUOUS → clarification-shaped bounded termination,
      zero tool executions;
   6. LLM_EMPTY → heuristic-routed execution allowed, response carries
      the guard boundary, no whole-request completion claim;
   7. INVALID_PROPOSAL → no contract, no silent narrowing (both
      subgoals not pretended answered);
   8. one output satisfied + one not → overall outcome honest
      (PARTIAL/UNANSWERABLE per existing derivation);
   9. RE_PLAN preserves all accepted outputs (extends the Scenario-F
      evidence pattern);
   10. Activity decision reasons and final response contain no
       overclaim (asserted on text + `semantic_basis`);
   11. contract-less fallback cannot bypass the guard (negative
       probe);
   12. flag OFF → baseline behavior identical (existing E1/E2-shaped
       flow and closure text).
10. **Browser E2E**: n/a (CG-5 builds on these validated paths).
11. **Flag / compat**: scenarios run flag ON; scenario 12 is the OFF
    identity anchor.
12. **Failure handling / rollback**: any red scenario blocks CG-5
    start; failures triaged per the project defect discipline (fix at
    the failing boundary or record as design-deviation blocker — never
    silently).
13. **Acceptance criteria**: all 12 scenarios PASS on the canonical
    topology (3001→8001→8000) against a fresh-restarted runtime
    (freshness preflight recorded per §11).
14. **DoD**: suite green + runtime-freshness evidence + like-for-like
    note for any pre-existing failures.
15. **Evidence**: test file, run log, per-scenario structured
    assertions (contract tuples, decision records, response texts).
16. **Unlocks**: WP-CG-5.

## 10. WP-CG-5 — Browser E2E and Exploratory Regression

1. **Objective**: real-browser user-journey acceptance of the merged
   behavior + the Session-01 trials fixed as permanent regression.
2. **Scope**: one browser journey module (existing harness) +
   exploratory-trial automation; real-model runs recorded separately.
3. **Out of scope**: new browser infrastructure; UI changes; verbatim
   model-output matching.
4. **Dependencies**: WP-CG-4 green on the canonical stack.
5. **Implementation tasks**:
   a. `qa/journey_cg_compound_l3.py` (reusing `qa/browser.py` +
      `qa/journeys_common.py` — re-verified as the current harness)
      covering: single-goal hedge; single-goal related-accounts;
      two-goal compound (both outputs independently traceable); one
      answered + one bounded (explicit partial+boundary); genuine
      AMBIGUOUS → clarification-shaped stop rendered (no fake
      completion); LLM_EMPTY/INVALID_PROPOSAL → guard-boundary
      rendering; compound with mid-run RE_PLAN (contract preserved);
      Activity reasons never claim un-investigated goals answered;
      page completion state consistent with semantic state.
   b. Session-01 trials A/B/C/D/E1/E2 as automated regression:
      structured-Contract semantic comparison (outputs as canonical
      tuples: claims/claim_kinds/subjects/scopes/normalized
      requirements; coverage; final boundaries) — NEVER verbatim text
      equality; per-trial fresh investigation on U00010.
   c. Deterministic runs use the seam (`goal_llm`/`plans`/
      `decisions`); real-model exploratory runs execute WITHOUT the
      seam and are recorded separately (never cited as deterministic
      evidence); model instability recorded as ENVIRONMENT_BLOCKED,
      never masked by fixtures.
6. **Files (verified existing)**: `qa/browser.py`,
   `qa/journeys_common.py`, screenshot dir `qa/screenshots/`.
7. **New files**: `qa/journey_cg_compound_l3.py`; results JSON under
   `docs/evaluations/`.
8. **L1**: n/a.
9. **API E2E**: n/a (CG-4).
10. **Browser E2E**: the journeys in (a)/(b); each journey asserts
    user-visible semantics AND cross-checks backend state (activity
    projection / store document) per the established pattern.
11. **Flag / compat**: journeys run under the acceptance flag
    configuration; one flag-OFF baseline journey retained.
12. **Failure handling / rollback**: failures recorded per semantic
    layer with backend evidence; ENVIRONMENT_BLOCKED distinguished
    from product failure; no in-run fixes.
13. **Acceptance criteria**: all scripted journeys PASS; real-model
    exploratory run executed and recorded with per-journey
    classification (PASS / FINDING / ENVIRONMENT_BLOCKED).
14. **DoD**: browser evidence + exploratory record + QA note; both
    §10 flows of the work-package charter demonstrated end-to-end
    (governed path and safe-failure path) in a real browser.
15. **Evidence**: screenshots per journey, results JSON, QA record
    update.
16. **Unlocks**: acceptance review (feature complete pending QA).

## 11. Feature Flags and Rollout

- Single flag: `ENABLE_GOAL_SEMANTIC_CONTRACT` (AD-4). ON ⇒ COMPOUND
  extraction, acceptance rules, AMBIGUOUS disposition, provenance
  record, and the composer guard; OFF ⇒ today's baseline exactly (no
  new prompt, no decision record, no guard line, no COMPOUND).
- No new flag is proposed. Rationale: COMPOUND and the guard are one
  semantic feature ("governed goals"); a second flag would double the
  interaction-test matrix without an independent rollout boundary.
- Default remains OFF until acceptance completes; acceptance runs set
  the flag via env on the :8001 process (existing discipline) and
  restore the default afterwards.

## 12. Backward Compatibility / Persistence

- No schema migration: `AgentLoopState`,
  `goal_contract_decision`, `semantic_basis` are additive fields on
  whole-model JSON documents; old documents load with the fields
  absent (established tolerance).
- `GoalResolutionStatus.COMPOUND` is additive; old states load; a
  COMPOUND document read by pre-change code passes the existing
  representation validator (documented one-way tolerance in the
  design §14).
- Historical contract-less runs read exactly as today; guards apply to
  new runs only (composer-side; no stored-response rewriting).
- `GoalContract`/`RequiredOutput`: no schema change (`goal_type=None`
  already legal; verified no consumers).
- API schemas: additive projection fields only; the frontend consumes
  none today.
- Rollback per WP = flag OFF; no data cleanup required.

## 13. Cross-cutting QA Strategy

Per the project gate: passing units ≠ completion. CG-1/2/3 carry their
L1 suites; CG-3 additionally carries composer/loop integration L1s;
CG-4 is the real-HTTP integration gate (12 scenarios, structured
semantic assertions — contract tuples, decision records, absence of
completeness claims); CG-5 is the real-browser gate (journeys +
backend cross-checks). Deterministic (seam) and real-model exploratory
evidence are recorded separately and never substituted for each other.
Every E2E round follows the runtime-freshness preflight (§11 of the
charter): current-worktree restart of :8001, flags per scenario,
canonical topology, restore defaults afterwards, evidence recorded.
Pre-existing failure baseline (~12 with RP up) referenced like-for-like.

## 14. Acceptance Evidence Requirements

Per WP: named test files + run outputs; composer before/after text
samples; projection diffs; API E2E logs with per-scenario structured
assertions; runtime-freshness preflight records; browser screenshots +
results JSON + exploratory classification; a closing QA record that
separates unit-tested / integration-tested / browser-validated /
exploratory-only, and lists unverified areas and pre-existing failures.

## 15. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Model misroutes compound → AMBIGUOUS (medium, design §17) | post-CG-3 consequence is an honest bounded stop, not a silent drop; exploratory monitoring; prompt reconciliation + examples |
| COMPOUND/dedup regressions into single-goal paths | flag-scoped branches; byte-compat tests; existing gsp suites stay green like-for-like |
| Guard alters flag-OFF text accidentally | branch isolated in the composer; byte-identity L1 |
| Seam extension confuses existing fixtures | `goal_llm` wins only when present; existing `goal` fixtures untouched; gsp7 suite as regression gate |
| z.ai instability obscuring E2E rounds | deterministic seam for CI-grade runs; real-model rounds recorded separately; ENVIRONMENT_BLOCKED discipline |
| Merge conflicts CG-3 ∥ CG-1 | disjoint files (agent_loop vs goal_resolution); sequencing CG-3 → CG-1 recommended |

## 16. Open Decisions / PMO Approvals

- AD-6 final zh wording for the guard boundary and the
  clarification-stop message (constraints fixed in the design;
  copy review at implementation start).
- Execution order confirmation: recommended CG-3 → CG-1 → CG-2 →
  CG-4 → CG-5 (safety-first); the alternative CG-1 → CG-2 → CG-3 is
  functionally acceptable (§5).
- No other open decisions; no new flags, stores, or schema changes
  proposed.

## 17. Overall Definition of Done

All five WPs meet their individual DoD; the 12 API E2E scenarios and
all browser journeys PASS on the canonical topology against a
freshness-verified runtime; flag-OFF identity proven; GSP-1…7 suites,
AL-3.5.1 suite, and the AL-3 regression matrix green like-for-like;
INV-1…14 each pinned by a named test; both charter flows (governed
compound path and safe contract-less path) demonstrated end-to-end in
a real browser; closing QA record written per the reporting discipline.

## 18. Implementation Exit Criteria

1. PMO approval of this plan (gate — nothing starts before it).
2. Per-WP DoD evidence archived (§14).
3. CG-4 suite green on the canonical stack; CG-5 journeys green;
   exploratory record filed.
4. No unresolved design deviations: any conflict discovered during
   implementation between this plan/code and the approved design is
   recorded as a blocker/open decision for PMO — never silently
   resolved.
5. Flag default restored to OFF after acceptance; rollback path
   untested-but-documented is NOT acceptable — the flag-OFF identity
   test must have actually run.

---

*End of implementation plan. Not approved for execution; no code,
tests, or runtime behavior were changed in producing it.*

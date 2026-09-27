# ADR & M2 Re-baseline: Target ↔ Goal Refinement and Open-ended Investigation Understanding

**Status**: Architecture Decision — **Accepted for M2 implementation planning**.
Not production-final; not architecture-synced; not feature-accepted.
**Date**: 2026-09-21
**Supersedes**: the ad-hoc "Stage 2.x" incremental numbering as a
milestone structure (stages are reorganized into M2 Work Packages; the
Stage 2.1 implementation record in
`2026-09-20-open-ended-investigation-conversation-ux-v1.md` §35c remains
the valid record of shipped Stage 2.1 work).
**Related**:
- `2026-09-19-bounded-agent-loop-v1.md` — runtime baseline (loop unchanged)
- `2026-09-20-open-ended-investigation-conversation-ux-v1.md` — M2 product
  baseline (§35a/§35b/§35c records; §35b froze the Stage-2 concepts this
  ADR turns into a managed milestone)
- `2026-09-19-agent-qa-boundaries-v1.md` — B8/B9/B10 (target resolution,
  fixed) and B11/B12/B13 (capability suitability / task completion,
  M2 scope — documented from real browser experiments, not yet fixed)
- `docs/qa/2026-09-19-agent-activity-v1-e2e-qa.md` — acceptance evidence

---

## 1. Decision Context

The original M2 assumption treated Target Resolution and Goal
Understanding as independent serial stages:

```
Stage 1: Target Resolution  →  Stage 2: Goal Understanding  →  …
```

Implementation and real-browser validation showed this is not a clean
separation in practice:

1. Stage 1 target resolution was implemented and browser-verified for
   exact/deterministic references, but real-browser QA exposed that
   paraphrased and description-level references end in premature
   clarification (B8 — since fixed for the deterministic path).
2. The Stage 1 defect-fix removed the keyword gate on title matching,
   but real-browser experiments E1–E5 then showed a deeper boundary:
   when target resolution returns MULTIPLE plausible candidates, the
   candidates are retained in the clarification output but Goal
   Understanding never runs — the serial pipeline surfaces
   "clarification needed" before the user's objective can disambiguate.
3. Three real follow-up experiments (E1–E3 family in §2 below, plus the
   E2/E3 live goal runs) confirmed the goal layer CAN classify
   objectives correctly (`identify_related_accounts`,
   `inspect_network_structure`) when the target is resolved — but the
   loop downstream still fails (replan_failure / persistent_error) at
   noticeable rates.

Therefore M2's internal boundary is re-defined: not "Stage 2 = goal
understanding", but a bounded **Target ↔ Goal refinement** flow inside a
managed M2 milestone with explicit work packages, spikes, exit criteria,
and discovery reserve.

## 2. Evidence

All evidence below is from the current checkout, real browser/API runs,
or automated suites. Nothing is projected.

### 2.1 Real browser experiments (case U00010, no manual selection)

| # | Request (abridged) | Outcome |
|---|---|---|
| E1 | 关联账户情况 | failed / clarification_needed; no goal produced |
| E2 | 团伙账户 | failed / clarification_needed — candidates **F5, F6** listed, then dropped |
| E3 | 共享了设备 | failed / clarification_needed — candidates **F5, F6** listed, then dropped |
| E4 | 有关联交易 | failed / clarification_needed — candidates **F5, F6** |
| E5 | 关联对冲交易 | failed / clarification_needed — candidates **F5, F6, F8** listed, then dropped |
| E6 | 精确标题 "Linked Account Network" | RESOLVED → F6 → completed |

Key observations: candidates ARE retained at the clarification boundary
(so a refinement step has material to work with), but Goal Understanding
never runs on them (`goal: type=None` in every E1–E5 turn response).

### 2.2 Source-code evidence

- `ContextResolver.resolve()`: deterministic title matching → semantic
  assist → AMBIGUOUS/UNRESOLVED; `ResolutionOutcome` carries
  `candidates` but no goal field; no consumer reads `candidates` after
  clarification.
- `investigation_service.run_turn`: goal understanding inserted AFTER
  `resolver.resolve()` (2b block) — goal never sees pre-clarification
  candidates.
- `agent_loop._run_loop`: goal understanding moved to run start
  (before context resolution) — but the loop's context resolution can
  BLOCK on unresolved/ambiguous targets before the goal can contribute.
- `LoopPolicy`: STOP over an ambiguous-target coercion — no
  goal-aware disambiguation step exists.

### 2.3 Downstream live evidence (Stage 2.1 wiring verified end-to-end)

Real-LLM goal runs on focused targets produced, persisted, and exposed
via the activity API: E2-family → `identify_related_accounts` (INFERRED),
E3-family → `inspect_network_structure` (INFERRED). The planner then
remains the variance point: on the same resolved target it chose
`explain_signal` (unsupported → bounded failure) in some runs and
`inspect_self_opposite_trades` (success) in others — capability
selection is currently real-LLM-variance, not goal-driven enforcement.

## 3. Architecture Decision

**Decision**: adopt **bounded Target ↔ Goal refinement** as the M2
design direction.

Frozen properties:

- NOT a free loop; Target Resolution and Goal Understanding do not
  call each other without bound.
- Refinement triggers ONLY when: initial target resolution retained
  multiple plausible candidates AND the goal layer can contribute
  disambiguating semantics.
- Single candidate / exact match → fast path, no refinement.
- Refinement failure or persistent ambiguity → exactly ONE bounded
  clarification to the user.
- The system must not force-select a target merely because candidates
  exist.
- Goal understanding failure degrades to bounded unresolved — never
  fabricated resolution.

## 4. Decision Boundary (concepts that must stay separate)

| Concept | Question |
|---|---|
| Target Resolution | 用户在说什么对象？ |
| Goal Understanding | 用户想知道什么 / 想做什么？ |
| Capability Suitability | 现有 capability 是否真正能满足这个 goal？ |
| Capability Gap | Goal 已理解，但系统没有合适 capability。 |
| Execution Result | 工具实际执行后返回什么？ |
| Task Completion | 用户的原始 goal 是否真正得到满足？ |

Frozen non-equivalences: `INSUFFICIENT_DATA ≠ SUCCESS`;
`clarification_needed ≠ execution failure`; `semantic relevance ≠
capability suitability`; `candidate target ≠ resolved target`.

## 5. Known Architecture Risks

| ID | Risk | Status |
|---|---|---|
| R1 | Strict serial Target → Goal boundary fires clarification too early (before goal semantics can disambiguate) | **Confirmed** — E1–E5, all clarification_needed with retained candidates. *2026-09-24: mitigation wired and proven (SP-M2-1R); safety boundary unresolved → R7.* |
| R2 | GoalRepresentation ↔ planner capability metadata gap: the planner does not consume the goal representation today | Confirmed by code (planner unchanged in Stage 2.1) |
| R3 | Capability selection is real-LLM variance, not goal-driven: same target produced `explain_signal` vs `inspect_self_opposite_trades` across runs | Confirmed (live runs on F8) |
| R4 | Capability-gap / execution-failure / INSUFFICIENT_DATA runtime semantics not unified | Confirmed (B12; INSUFFICIENT_DATA → completion observed pre-fix; current criteria skip evidence_missing but no gap state exists; re-observed in final SP-M2-1 QA) |
| R5 | Conversation path vs Agent Loop path differ in goal/target resolution timing (turn: goal after target; loop: goal before target) | Confirmed by code inspection |
| R6 | Goal understanding correct ≠ execution correct: E3 goal inferred correctly, task still failed (replan_failure) | Confirmed (live run) |
| R7 | **Candidate Separability / Safe Select Boundary**: the system cannot judge whether a candidate is sufficiently and uniquely supported by the user's goal + candidate semantics to admit SELECT. "Confidence ≥ threshold" does not separate supported SELECTs (0.75–0.90) from unsafe forced SELECTs (0.72–0.78); genuinely ambiguous requests produced unsafe forced SELECT (observed 2/5 in the final QA sample). Formal distinctions to carry: **candidate preference ≠ candidate separability; confidence ≠ candidate separability; candidate count > 1 ≠ user-intent ambiguity** | **Confirmed** (SP-M2-1 final QA, 2026-09-24) — core Discovery Risk; research owner **SP-M2-1C** |

## 6. What HAS Been Proven

- **Browser-validated**: deterministic target resolution on exact title
  (E6 → F6, completed); Stage 2.1 goal representation produced,
  persisted, and surfaced via the activity API on focused EN/CJK runs
  (`identify_related_accounts`, `inspect_network_structure`); inline
  activity rendering; bounded clarification with retained candidates.
- **Unit/integration tested**: Stage-1 matrix (28), goal resolution unit
  (24), goal API wiring (5), loop suites (29+), Layer B real-LLM
  target resolution (10, with known single-case variance documented).
- **Implementation complete**: Stage 1 target resolution incl. B8/B10
  fixes; Stage 2.1 goal model, vocabulary, LLM + heuristic proposers,
  service/loop/API wiring, activity exposure.

## 7. What has NOT Been Proven

- That goal-aware refinement reduces clarification frequency.
- The refinement reliability/threshold under the real LLM.
- That E1–E5 reach their expected targets/goals after refinement.
- That capability metadata (not yet designed in detail) suffices for
  goal-driven capability selection.
- That capability gaps are expressible and surfaced correctly end-to-end.
- That final task status aligns with user-goal semantics across paths.

## 8. Decision Status

**Architecture Decision — Accepted for M2 implementation planning.**
Not: production architecture finalized · architecture sync completed ·
feature accepted · M2 accepted.

*2026-09-24 amendment*: the accepted refinement FLOW (resolver
ambiguity → goal-assisted disambiguation → resolve/clarify) is
validated end-to-end (SP-M2-1R wiring + final QA), but the current
Safe Select Policy ("confidence ≥ 0.7 → SELECT") is **rejected** as
the WP-M2-1 implementation baseline (R7). WP-M2-1 is gated on
SP-M2-1C (Candidate Separability / Safe Select Boundary). The flow's
validated parts (wiring, fast-path isolation, candidate-ID
enforcement, single-attempt bound, safe degradation) are retained.

---

## 9. M2 Scope Inventory (evidence-based)

| # | Item | Classification |
|---|---|---|
| 1 | Target Resolution — explicit/exact title | **A. Completed** (browser-validated) |
| 1a | Target Resolution — description-level semantic references | **C. Partially validated** (LLM assist live on some runs; B8 fix deterministic path only) |
| 2 | Goal Representation (model/vocabulary/LLM+heuristic) | **B. Implemented, not behaviorally validated** (E2/E3 live runs only; E1 variance; no refinement integration) |
| 3 | Target ↔ Goal refinement | **D. Known remaining work** (architecture accepted, not designed/implemented) |
| 4 | Capability metadata (planner-visible) | **D. Known remaining work** (fields contracted §15, not implemented) |
| 5 | Capability suitability evaluation | **D/E** — concept frozen (§35b), implementation + decision boundary unknown |
| 6 | Capability gap detection + gap response | **D/E** — concept frozen, not implemented |
| 7 | Capability composition (multi-capability plans) | **D** — loop supports multi-step, selection not goal-driven |
| 8 | Clarification semantics | **C. Partially validated** (bounded clarification works; goal-aware refinement may change when it fires) |
| 9 | Task completion semantics | **C. Partially validated** (criteria skip evidence_missing ✓; goal-alignment dimension unproven — B12) |
| 10 | Conversation path vs Loop path consistency | **E. Architecture Unknown** (R5: goal/target ordering differs) |
| 11 | Conversation-native Activity UX | **D** — contracted (Activity V1 §18), not implemented |
| 12 | Open-ended evaluation (§25–28 matrix) | **D** — partial (Stage 1 matrix done; objective/goal cases not) |
| 13 | Browser acceptance | **C** — harness + journeys exist for Stage 1/2.1; M2 journeys pending |
| 14 | Independent QA | **F. Out of scope for M2 implementation** — separate phase, after M2 |

## 10. M2 Unknowns / Risks / Spikes

| ID | Unknown | Class | Blocking |
|---|---|---|---|
| U1 | Refinement minimal interaction boundary: what exactly flows between resolver-ambiguity and goal understanding (candidates? scores? full request?) | Architecture Spike **SP-M2-1 — CLOSED** (result: `2026-09-21-sp-m2-1-target-goal-refinement-spike-v1.md`) | — |
| U2 | LLM disambiguation reliability: can the model stably pick F5 vs F6 given goal semantics ("共享了设备")? | **Partially resolved → superseded 2026-09-24**: SP-M2-1 closed (Discovery Complete / Current Contract Rejected). Clear cases resolve stably; genuinely ambiguous sets produce unsafe forced SELECT; threshold alone is not a safe boundary. Research continues as **SP-M2-1C — Candidate Separability / Safe Select Boundary** | SP-M2-1C (gates WP-M2-1) |
| U3 | Goal → capability metadata contract: which metadata fields does selection actually consume? | Implementation decision after SP-M2-2 | WP-M2-2/2.3 |
| U4 | Capability suitability decision boundary (semantic scoring? LLM judgment vs metadata rules?) | Spike (SP-M2-2) | WP-M2-3 |
| U5 | Capability gap semantics: which terminal/status represents "goal understood, no capability"? | Implementation decision | WP-M2-4 |
| U6 | Task completion semantics for goal alignment | Implementation + QA | WP-M2-4 / QA |
| U7 | Conversation vs Loop path consistency (R5) | Implementation decision | WP-M2-1 |
| U8 | Golden journey coverage for open-ended behavior | QA | M2 QA gate |
| — | Parallelizable: WP-M2-2 (metadata) independent of WP-M2-1; conversation UX independent until integration | — | — |
| — | QA-closable: clarification-rate, journey matrix, no-CoT boundary | — | M2 QA gate |

## 11. Work Package Breakdown & Estimation

| WP | Scope | Dependencies | Impl Effort | Validation Effort | Confidence | Discovery Risk |
|---|---|---|---|---|---|---|
| SP-M2-1 | Refinement interaction contract + LLM disambiguation reliability probe (Layer-B, 5–10 runs) | Stage 2.1 | 0.5–1 d | included | Medium | **High** — model may not stably disambiguate. **CLOSED 2026-09-24: Discovery Complete / Current Contract Rejected** (`2026-09-21-sp-m2-1-target-goal-refinement-spike-v1.md` Final Discovery Status) |
| SP-M2-1C | **Candidate Separability / Safe Select Boundary** discovery spike — what evidence/system conditions make SELECT admissible vs require CLARIFY (taxonomy, labeled case matrix, frozen-behavior observation; NO production code) | SP-M2-1 closure + final QA (R7) | 1–2 d | included | Medium | **High** — defines the WP-M2-1 gate; doc: `2026-09-24-sp-m2-1c-candidate-separability-v1.md` |
| SP-M2-2 | Suitability decision-boundary probe (goal vs metadata vs LLM judgment) | SP-M2-1 | 0.5–1 d | included | Medium | Medium |
| WP-M2-1 | Goal-aware target refinement (resolver ambiguity → goal-assisted disambiguation → resolve/clarify) | SP-M2-1C (was SP-M2-1 — closed, contract rejected 2026-09-24). *2026-09-24: Safe Target Refinement Contract designed (`2026-09-24-wp-m2-1-safe-target-refinement-contract-v1.md`); Item 2 evidence-validation discovery complete (Decision B → Item 2A); Item 2A complete (Decision C → metadata prerequisite); **Candidate Semantic Metadata Contract designed (`2026-09-24-candidate-semantic-metadata-contract-v1.md`) — all discovery prerequisites discharged; implementation NOT started; PMO approval gates implementation*** | 2–4 d impl + signal production 2–4 d + metadata layer ~4–8 d (see contract §19 + metadata contract §28) | 1–2 d | Medium | Medium — residual: normalization-layer design (implementation, not discovery) |
| WP-M2-2 | Capability metadata made planner-visible (V1 fields §15) | — | 1–2 d | 0.5–1 d | High | Low |
| WP-M2-3 | Capability suitability evaluation | WP-M2-2, SP-M2-2 | 2–4 d | 1–2 d | Low–Medium | High (decision boundary undefined) |
| WP-M2-4 | Capability gap detection + gap response | WP-M2-3 | 1–2 d | 0.5–1 d | Medium–High | Low–Medium |
| WP-M2-5 | Task-completion semantics (B12: goal-aligned completion; INSUFFICIENT_DATA handling) | loop policy | 1 d | 0.5–1 d | Medium–High | Medium |
| WP-M2-6 | Hook demotion to fast paths (post-suitability) | WP-M2-3 | 0.5–1 d | 0.5–1 d | Medium | Medium (routing regressions) |
| WP-M2-7 | Capability composition via existing loop | WP-M2-3/2.4 | 2–4 d | 1–2 d | Medium | Medium |
| WP-M2-8 | Conversation-native Activity UX (inline block; right column secondary) | Activity V1 | 2–4 d (frontend) | 1 d | High | Low |
| WP-M2-9 | Open-ended QA/exploratory evaluation (matrix §25–28) | all above | 2–3 d | — | High | Low |

Totals (implementation+validation, excluding discovery reserve):
~13–26 working days across 9 WPs + 2 spikes. This is a RANGE, not a
schedule; SP-M2-1/SP-M2-2 exist precisely to convert the High-risk
items into estimable work.

## 12. M2 Re-baselined Scope

1. **M2 goal now**: make open-ended investigation requests resolve to a
   target (refined by goal semantics when ambiguous) and a structured
   goal, then execute a bounded, goal-driven capability path — with
   honest gap/completion semantics.
2. **In M2**: items 1–10 of the scope inventory (target resolution,
   goal representation, refinement, metadata, suitability, gap,
   composition, clarification/completion semantics, path consistency).
3. **Explicitly excluded**: conversation-native UX implementation is
   IN M2 (WP-M2-8) but advanced observability/mobile/etc. are not;
   resume execution; MCP; long-term memory; new tools (a
   network/cluster capability is a PRODUCT decision after gap
   detection exists — not snuck in).
4. **Needed but not to be pre-implemented**: network-cluster
   exploration capability — first prove the gap detection and gap
   response; the capability itself is a separate product decision.
5. **Implementation work**: WP-M2-1, 2, 3, 4, 5, 6, 7 (frontend), 8.
6. **Architecture validation work**: SP-M2-1, SP-M2-2, U5/U7
   consistency checks.
7. **QA acceptance work**: WP-M2-9 + independent QA re-run.
8. **Largest remaining uncertainties**: LLM disambiguation stability
   (SP-M2-1), suitability decision boundary (U4), completion semantics
   (U6/U5 — B12).

### M2 Delivery Shape

```
SP-M2-1/2 (Discovery) → Decision gates → WP-M2-1 (Refinement)
  → WP-M2-2/3 (Metadata+Suitability) → WP-M2-4 (Gap)
  → Integration via existing Loop → Golden Journeys GJ1–GJ8
  → Browser acceptance → Independent QA → M2 Exit
```

## 13. M2 Exit Criteria

**Target Resolution**: exact/deterministic → resolved fast path;
single candidate → resolved; multiple candidates → goal-aware
refinement attempted, then resolved or exactly one bounded
clarification; unresolved ambiguity → clarification. No premature
clarification when goal semantics can disambiguate.

**Goal Understanding**: the loop/turn distinguishes at minimum
cross_account_check / identify_related_accounts /
inspect_network_structure / explain_finding /
identify_supporting_records / timeline / policy_check on paraphrased
EN/简体/繁體/mixed input — asserted semantically, not by phrase count.

**Capability Suitability**: provable flow goal → candidate capability →
suitability evaluated (not "related → executed"); unsuitable → gap
path.

**Capability Gap**: goal understood + no capability → bounded gap
response, distinct from goal-not-understood.

**Runtime semantics**: no `INSUFFICIENT_DATA → successful completion`;
no `clarification_needed → execution failure` mislabeling (bounded
clarification state remains distinct).

**Quality**: automated suites green (modulo documented environment
baseline), integration/E2E green, real-browser Golden Journeys GJ1–GJ8
green, independent QA pass. Exit decided by evidence.

## 14. Golden Journey Matrix

| GJ | Journey | Current status | Evidence | Expected | Owner | Remaining |
|---|---|---|---|---|---|---|
| GJ1 | 精确 finding request ("Linked Account Network 具体是什么？") | **PASS** (live) | E6 run: F6 resolved, completed | resolve → inspect | QA | — |
| GJ2 | 单候选 target | **PASS** (Layer A/B) | Stage-1 suite | resolve-inferred | QA | — |
| GJ3 | 多候选 + goal 可消歧 ("共享了设备") | **FAIL** (E3: clarification) | this investigation | refine → F5 | WP-M2-1 | implementation |
| GJ4 | 多候选 + goal 仍无法消歧 | **PARTIAL** (bounded clarification works) | E2–E5 runs | clarify with candidates | QA | acceptance |
| GJ5 | Goal understood + suitable capability ("对冲交易" → cross_account_check) | **PARTIAL** (goal ✓ live; completion on INSUFFICIENT_DATA wrong) | E1-family runs | bounded run; status reflects sufficiency | WP-M2-5 + QA | implementation |
| GJ6 | Goal understood + capability gap ("Linked Account Network 是什么" → inspect network) | **FAIL** (executed unsuitable trade analysis) | E3 experiments | gap response | WP-M2-3/2.4 | implementation |
| GJ7 | Tool returns INSUFFICIENT_DATA | **PARTIAL** (bounded; completion handling wrong) | E1 experiments | not-completed + gap/insufficient semantics | WP-M2-5 | implementation |
| GJ8 | Conversation path 与 Loop path 一致 | **PARTIAL** (goal/target ordering differs — R5) | code inspection | same semantic outcome both paths | WP-M2-1 | implementation |

## 15. Management Summary (one page)

- **M2 Goal**: bounded open-ended investigation — NL request → resolved
  target (refined by goal semantics when ambiguous) → structured goal →
  goal-driven capability path → honest completion/gap semantics.
- **M2 Status**: re-baselined. Stage 1 fixed+browser-verified; Stage 2.1
  implemented+live-verified on 2 of 3 experiments; refinement not
  started; suitability/gap not implemented.
- **Completed**: deterministic + semantic target resolution (exact
  title browser-verified); goal model/vocabulary/proposers; loop+turn
  wiring; activity exposure.
- **In progress**: none (this phase closed Stage 2.1; M2 planning
  starts now).
- **Known risks**: R1–R6 above; plus real-LLM planner/proposer variance
  as a permanent Layer-B characteristic.
- **Open unknowns**: U1–U8.
- **Architecture decisions**: bounded refinement accepted (this ADR);
  suitability/gap concepts frozen (§35b) pending implementation.
- **Remaining WPs**: SP-M2-1/2, WP-M2-1..9.
- **Dependencies**: WP-M2-1 ← SP-M2-1; WP-M2-3 ← WP-M2-2 + SP-M2-2;
  WP-M2-4 ← WP-M2-3; UX/integration after core.
- **QA gates**: automated suites; Golden Journeys GJ1–GJ8; no-CoT /
  no-raw-data boundary; independent QA.
- **Exit criteria**: §13.
- **Scope exclusions**: §31 of the open-ended baseline + no new
  network/cluster tool in M2.
- **Effort range**: ~13–26 working days impl+validation (range, not
  schedule), excluding independent QA and discovery reserve.
- **Confidence**: Medium overall — High on WP-M2-2/8/9, Medium on
  refinement, Low on suitability boundary.
- **Discovery reserve**: recommend 30–50% on WP-M2-1/2.3 until
  SP-M2-1/SP-M2-2 close.

*2026-09-24 PMO update*: SP-M2-1 → **Discovery Complete / Current
Contract Rejected** (direction retains evidence-backed value; the
Safe Select Policy is the rejected part). New gate spike
**SP-M2-1C — Candidate Separability / Safe Select Boundary**
(PLANNED). Downstream WPs (Capability Metadata / Suitability / Gap /
Composition) remain unstarted: Target Refinement is M2's current
upstream architecture gate and its safe boundary is not yet
established.

## 16. What evidence would close M2

M2 exits when: GJ3 shows goal-aware refinement resolving F5-class
ambiguities; GJ5 shows correct capability execution; GJ6 shows a
bounded gap response; GJ7 shows non-completion on INSUFFICIENT_DATA;
GJ8 shows path consistency; clarification rate on E1–E5 measurably
drops; independent QA signs off. Until then M2 is in progress, not
accepted.
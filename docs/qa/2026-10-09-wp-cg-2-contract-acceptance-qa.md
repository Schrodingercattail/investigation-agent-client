# WP-CG-2 — Contract Acceptance and Semantic Deduplication — QA Record

**Date**: 2026-10-09
**Status**: IMPLEMENTED + L1-VALIDATED (23/23) + regression green (CG-1 /
GSP-2 / CG-3: 95 passed; adjacent goal-resolution / GSP / Activity:
204 passed). Not committed / not pushed.
**Approved basis**: design v1 Revision 2 §10-E2 (acceptance rules, AD-1,
AD-3) and implementation plan v1 §8. Scope guard: this WP lands the
acceptance/dedup rules ONLY — no extraction changes (WP-CG-1
territory), no API E2E framework (WP-CG-4), no browser work (WP-CG-5).

## Scope and non-goals

In: deterministic all-or-nothing acceptance of COMPOUND proposals; the
≥2-semantically-independent-outputs rule; canonical semantic dedup; the
new `compound_single_output` rejection code surfaced through the
existing WP-CG-3 decision provenance. Out (untouched): GSP-3/4/5/6/7
semantics, G1, WP-CG-3 guards, O2 ANY-contract boundary, extraction
protocol, second LLM call, stores, flags.

## Code facts and canonical semantic content

`build_goal_contract` (app/goal_resolution.py) was verified
resolution-agnostic and all-or-nothing per item before this WP; the
CG-1 representation validator already rejects structurally invalid
COMPOUND representations (goal_type set, <2 raw entries) at
construction. Canonical semantic content chosen per the approved
design (AD-3) — exactly the fields that define one deliverable:

1. `claim` — whitespace-collapsed + casefolded (normalization, NOT
   similarity);
2. `claim_kind`;
3. `subject_ref`;
4. `scope`;
5. `evidence_requirements` as a frozenset (ANY-set semantics: order
   and duplicates carry no meaning).

Deliberately excluded: `claim_description` (user-safe prose ABOUT the
same deliverable — different wording does not make a different goal;
locked by test J) and `polarity`/`detail` (runtime-written state,
never proposal content — excluded by construction since proposals
never carry them).

## Acceptance rules implemented

In `build_goal_contract`, after the unchanged per-item all-or-nothing
loop and ONLY for COMPOUND representations:

1. canonical dedup (`_dedup_canonical_outputs`): first occurrence kept,
   request order preserved, deterministic (no dict-order or model-order
   dependence);
2. `output_id`s reassigned `O1..On` after dedup (stable, gap-free —
   no gapped identities);
3. <2 surviving outputs → whole rejection, new code
   **`compound_single_output`** (reached both by a 1-entry proposal
   post-validation and by duplicates collapsing to one).

Single-goal proposals (INFERRED/EXPLICIT): no dedup, no ≥2 rule,
byte-identical pre-CG-2 behavior (multi-output INFERRED contracts
remain accepted as before). `goal_type=None` pass-through for
heterogeneous contracts is load-bearing and pinned (AD-1). Flag OFF:
the checkpoint never invokes the builder — verified at loop level.

## Changed files (WP-CG-2 increments)

| File | Change |
|---|---|
| `app/goal_resolution.py` | `_canonical_output_key` + `_dedup_canonical_outputs` helpers; COMPOUND acceptance block in `build_goal_contract` (dedup, ≥2 rule, O-id reassignment); docstring updated |
| `tests/test_cg2_contract_acceptance_v1.py` | NEW — 23 tests |

Pre-existing accumulated work (NOT part of this WP's diff): all
WP-CG-3 safety/provenance code and tests, WP-CG-1 extraction protocol
and tests, the GSP working tree, and the resolver-compat PMO fix
(landed at the start of the WP-CG-1 round).

## Test-to-acceptance mapping (every matrix item → tests)

| Matrix | Test(s) in `test_cg2_contract_acceptance_v1.py` |
|---|---|
| A 2 outputs, goal_type=None | `test_a_two_independent_outputs_one_contract` |
| B 3 outputs all kept | `test_b_three_outputs_all_kept` |
| C exact duplicate collapsed once | `test_c_exact_duplicate_collapsed_once` |
| D permuted evidence sets equivalent | `test_d_permuted_evidence_requirements_equivalent` |
| E/T one output, many evidence reqs stays one | `test_t_single_output_multi_evidence_stays_one` |
| F same claim, different subject | `test_f_same_claim_different_subject_not_collapsed` (both subjects legal: finding-subject + case id) |
| G same claim, different scope | `test_g_same_claim_different_scope_not_collapsed` |
| H different claim_kind | `test_h_different_claim_kind_not_collapsed` |
| I different evidence requirements | `test_i_different_evidence_requirements_not_collapsed` |
| J description differs, canonical same | `test_j_description_differs_canonical_same_collapsed` (approved-design ruling, documented) |
| K similar text, different deliverable | `test_k_similar_text_different_deliverable_not_collapsed` |
| L missing field | `test_l_missing_field_rejects_whole_compound` |
| M illegal evidence type | `test_m_illegal_evidence_type_rejects_whole` |
| N empty outputs | `test_n_empty_outputs_rejected` (two layers: CG-1 structural ValueError + builder `no_outputs`) |
| O COMPOUND with one output | `test_o_compound_single_output_rejected_not_narrowed` (two layers: CG-1 structural + builder `compound_single_output` via validator-bypassed direct call) |
| P dedup collapses to one | `test_p_dedup_to_one_rejected` |
| Q partial validity | `test_q_partial_validity_never_partially_accepted` |
| R fabricated top-level goal_type | `test_r_compound_with_fabricated_goal_type_rejected` (representation validator, AD-1) |
| S single-goal path unchanged | `test_s_single_goal_path_unchanged` + GSP-2 suite |
| U flag OFF baseline | `test_u_flag_off_checkpoint_never_builds` |
| V legacy reads | GSP-2 persistence tests (regression) + single-goal tests |
| W canonicalization stability | `test_w_canonicalization_stable_across_runs` |
| Checkpoint wiring of the new code | `test_compound_single_output_surfaces_as_invalid_proposal` (decision provenance `invalid_proposal:compound_single_output`, guarded legacy continuation) |

## Layered test results (statistics corrected 2026-10-09, PMO check)

Two combined pytest commands were executed; their file sets are
DISJOINT, so the union count is the deduplicated independent test
count. Per-command results (re-verified by re-running both commands
and by per-file `--collect-only` node-ID counts):

**Run A — CG-2 + CG-1 + GSP-2 + CG-3 (5 files): 95 passed, 0
failed/skipped/xfailed**

| File | Tests |
|---|---|
| test_cg2_contract_acceptance_v1.py | 23 |
| test_cg1_compound_extraction_v1.py | 16 |
| test_gsp2_goal_contract_v1.py | 35 |
| test_cg3_contractless_safety_v1.py | 16 |
| test_cg3_contractless_api_e2e_v1.py | 5 |

**Run B — adjacent regression (12 files): 204 passed, 0
failed/skipped/xfailed**

| File | Tests |
|---|---|
| test_goal_resolution_v1.py | 24 |
| test_goal_resolution_api_v1.py | 5 |
| test_gsp1_capability_declarations_v1.py | 15 |
| test_gsp3_observation_association_v1.py | 22 |
| test_gsp4_postconditions_v1.py | 38 |
| test_gsp5_composer_v1.py | 16 |
| test_gsp6_planning_coverage_v1.py | 14 |
| test_gsp7_replan_preservation_v1.py | 6 |
| test_gsp7_replan_api_e2e_v1.py | 5 |
| test_al351_target_validity_guard_v1.py | 41 |
| test_agent_activity_api_v1.py | 8 |
| test_clarification_state_wi6_v1.py | 10 |

**Deduplicated independent total: 299 passed, 0 failed, 0 skipped, 0
xfailed** (95 + 204; the two runs share no test files, verified by the
command definitions above and the per-file node-ID table).

Explanation of the originally reported discrepancy: the summary listed
the component breakdowns (CG-2: 23, CG-1: 16) alongside the Run-A
combined total (95) that ALREADY CONTAINS them. Adding the four listed
layers naively (23+16+95+204=338) therefore double-counts 39 tests
(= 23+16). The deduplicated execution count 299 was and is correct;
the presentation was ambiguous and is superseded by this section.

Test-authoring note (honesty): two initial failures were TEST bugs,
not product bugs — (a) matrix F originally used an illegal second
subject (violating the pre-existing subject-consistency rule) and was
reworked to use two LEGAL subjects; (b) a scripted planner payload lost
its closing brace during editing (`LLM_OUTPUT_INVALID`) — restored.
No product code was changed to make tests pass.

## Unresolved blockers

None for this WP. WP-CG-4 (real-HTTP 12-scenario validation incl. the
`goal_llm` seam extension) and WP-CG-5 (browser + exploratory) remain
the next phases per the approved plan.

Not committed / not pushed.

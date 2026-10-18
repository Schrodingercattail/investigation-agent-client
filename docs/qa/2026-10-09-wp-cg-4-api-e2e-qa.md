# WP-CG-4 — Compound Goal API E2E Integration Validation — QA Record

**Date**: 2026-10-09
**Status**: IMPLEMENTED + VALIDATED — 11/11 canonical-stack API E2E
scenarios passed in CG-4 mode; 299 passed / 0 failed non-CG-4
regression; Part A statistics correction applied. Not committed / not
pushed.

---

## Part A — WP-CG-2 statistics correction

**Discrepancy**: the WP-CG-2 QA record listed four layers (CG-2: 23,
CG-1: 16, GSP-2+CG-3 combined: 95, adjacent: 204) whose naive sum is
338, while the reported total was 299.

**Verification performed** (this round): both original combined
commands were re-run, and every involved test file was re-collected
(`pytest --collect-only -q`, per-file node-ID counts):

| Command | Files | Tests |
|---|---|---|
| Run A: CG-2 + CG-1 + GSP-2 + CG-3 | cg2(23) + cg1(16) + gsp2(35) + cg3-safety(16) + cg3-api(5) | **95 passed** |
| Run B: adjacent regression | goal_res(24) + goal_res_api(5) + gsp1(15) + gsp3(22) + gsp4(38) + gsp5(16) + gsp6(14) + gsp7(6) + gsp7-api(5) + al351(41) + activity(8) + clarification(10) | **204 passed** |

- The two runs share NO test files → the union count is the
  deduplicated independent test count: **95 + 204 = 299 passed,
  0 failed, 0 skipped, 0 xfailed** (re-confirmed by re-running both
  commands this round: 95 and 204 again).
- **338 explanation**: a presentation double-count, not a run
  discrepancy — CG-2 (23) and CG-1 (16) are component breakdowns
  WITHIN Run A's 95; listing them as separate layers next to 95 makes
  the naive sum exceed the union by exactly 39 (= 23+16).
- **Correction applied**: the WP-CG-2 QA record's "Layered test
  results" section is superseded by a per-command + per-file table
  with the deduplicated union (299) and the double-count explanation.

Part A used its own fresh run batch (this round); it is not mixed with
the WP-CG-4 scenario runs below.

---

## Part B — WP-CG-4 implementation and results

### Changes

| File | Change |
|---|---|
| `app/agent_loop.py` | seam extension: `_ScriptedSessionGoalLLM` + `_scripted_goal_llm_resolver` + session section `goal_llm` (list of goal-understanding MODEL TEXT strings; wins over the `goal` template section). The REAL `understand_goal` runs over the scripted text (prompt building, parsing, resolution branches, proposal extraction); exhausted scripts → "" → the production heuristic fallback (LLM_EMPTY route). Test infrastructure only |
| `tests/test_cg4_compound_api_e2e_v1.py` | NEW — 11 tests covering the 12 approved scenarios (S10 shares S6's journey with dedicated overclaim assertions); module-scoped `cg4_mode` preflight fixture: if the backend is not in CG-4 mode the module SKIPS (environment precondition, never a product failure) |

### Runtime freshness / environment

- Backend restarted from the current worktree (HEAD `58881a9` + CG
  working tree; PID 23269, 15:55:43 > all source mtimes) with
  `ENABLE_TARGET_VALIDITY_GUARD=true`, `ENABLE_GOAL_SEMANTIC_CONTRACT=true`,
  `AGENT_LOOP_SCRIPTED_SESSION_FILE=/tmp/cg4_session.json` (`ps eww`
  verified). Topology 3001→8001→8000; RP live (real U00010 data — no
  RP fixtures in this suite).
- Seam vs real model, per suite: the goal-understanding/planner/
  decision model boundary is the deterministic `goal_llm`/`plans`/
  `decisions` seam; the context resolver runs deterministic-first and
  — with the user-selected focus every scenario carries — produced a
  VERIFIED zero z.ai delta per journey (LLM-free end to end). No
  real-model extraction is claimed by this suite (WP-CG-5 scope).
- Restored default after the round: flags OFF, no seam
  (PID 24582→25413 lineage, env verified 0).

### Scenario → test mapping and results (11/11 PASSED)

| # | Approved scenario | Test | Key assertions (all over real HTTP + real modules) |
|---|---|---|---|
| 1 | healthy single-goal contract | `test_s1_single_goal_healthy` | contract_accepted; 1 output; O1 satisfied; GSP-5 outcome line; no guard line |
| 2 | two-goal COMPOUND, one contract | `test_s2_two_goal_compound_both_governed` | contract goal_type=None (AD-1); both claims in order; contract_accepted; store `planning_coverage` = both covered; `step_output_map` established; both outputs evaluated |
| 3 | three-goal contract intact | `test_s3_three_goal_compound_all_kept` | 3 claims in order; all satisfied; store coverage = 3× covered |
| 4 | invalid contract content → all-or-nothing | `test_s4_compound_invalid_evidence_all_or_nothing` | contract None; `invalid_proposal:invalid_evidence_type`; guarded legacy continuation with coverage boundary |
| 5 | genuine AMBIGUOUS bounded stop | `test_s5_ambiguous_bounded_stop_zero_tools` | blocked/clarification_needed; `ambiguous_not_executed`; zero plan steps; zero tool events; honest stop message |
| 6 | LLM_EMPTY restricted routing | `test_s6_llm_empty_heuristic_restricted_routing` | `llm_empty` provenance; restricted execution completes; coverage boundary; no completion claim |
| 7 | INVALID_PROPOSAL, no narrowing | `test_s7_inferred_invalid_proposal_no_narrowing` | `invalid_proposal:invalid_evidence_type`; no contract; no GSP outcome line (nothing silently adopted) |
| 8 | honest mixed outcome | `test_s8_mixed_outputs_honest_partial` | O1 satisfied (withdrawal records); assessment output never adjudicated (unresolved/pending); overall partial/unanswerable; "已部分回答"/"无法回答" wording; never fake COMPLETE |
| 9 | RE_PLAN preserves outputs | `test_s9_replan_preserves_outputs_and_contract` | plan_revisions=2; contract semantic tuple intact; O2 satisfied via the re-planned step; O1 keeps its plan-1 verdict (never reset); store: re-planned coverage admission re-run, O2 covered, step_output_map rebuilt |
| 10 | no overclaim | `test_s10_activity_and_response_never_overclaim` | STOP rows `semantic_basis="none"` + deterministic safe summary; response carries the boundary, never a completion claim |
| 11 | guard cannot be bypassed | `test_s11_contractless_fallback_cannot_bypass_guard` | UNRESOLVED-extraction run: contract-less + completed → boundary line present; no completion claim |
| 12 | flag OFF baseline | executed as a dedicated canonical journey after restoring the backend to `ENABLE_GOAL_SEMANTIC_CONTRACT=false` (flag is process-global) | COMPOUND-model-text input → heuristic bounded fallback; contract None; decision None; no guard line; legacy completed — baseline identical |

### B4/B5 core assertions evidence

- Model text → real `understand_goal` → resolution/COMPOUND states
  verified via the served `goal_contract_decision` + contract content
  (claims/kinds/subjects/scopes/requirement sets in request order).
- Contract acceptance: `goal_contract_decision` outcomes
  (`contract_accepted` / `invalid_proposal:<code>` / `llm_empty` /
  `ambiguous_not_executed` / `no_proposal`) all exercised over real
  HTTP.
- GSP-6: store documents (read-only canonical DB) show
  `planning_coverage` per output (`covered`) and `step_output_map`
  established for every adopted plan (S2/S3/S9).
- GSP-7: S9 re-plan adoption re-ran admission; contract semantic tuple
  unchanged; no output dropped/narrowed; per-output verdicts never
  reset (O1 kept its plan-1 verdict after the re-plan).
- Honest partial: S8 — one satisfied + assessment-not-adjudicated →
  overall PARTIAL with "已部分回答"; `COMPLETED`/`GOAL_SATISFIED`
  never rendered as semantic COMPLETE (S6/S7/S11 guard boundaries).

### Regression (same round, default-mode backend)

Non-CG-4 battery (17 files: cg1, cg2, cg3 L1+API, gsp1–gsp7 incl.
API E2E, al351, activity, goal resolution ×2, clarification):
**299 passed, 0 failed** (this is the same deduplicated 299 population
documented in Part A — the file sets coincide). The 11 CG-4 tests are
environment-gated and were validated in their required CG-4 mode
separately (11/11), not mixed into the default-mode battery — the one
mixed-mode battery attempt (11 CG-4 failures against a default-mode
backend) was discarded as an INVALID RUN configuration, not a product
failure, and motivated the `cg4_mode` preflight fixture.

Known baselines untouched: the 2 `test_agent_loop_v1` re-plan failures
and the 4 `test_error_handling` legacy failures were not in this
round's scope (narrow-scope discipline) and no code path they exercise
was modified.

### Failures / environment blocks

- Product semantic defects found this round: none.
- Harness/fixture issues fixed this round: S2/S9 draft assertions
  (leftover draft lines; S9 plan-1 originally could not cover O1 —
  replaced with a retrieve_policy plan so the re-plan exercises a real
  coverage transition); CG-4 tests against a default-mode backend are
  now a skip, not a failure (mode preflight).
- Environment blocks: none (RP live; provider untouched — the suite is
  provider-independent by seam design).

`git diff --check`: clean. Not committed / not pushed.

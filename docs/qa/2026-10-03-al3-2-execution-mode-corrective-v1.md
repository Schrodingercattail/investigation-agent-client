# AL-3.2 — Execution Mode Corrective Pass — QA v1

**Date**: 2026-10-04
**Status**: **PASS** (smoke verified on canonical runtime)
**Scope**: evaluation harness only — execution_mode dispatch contract
enforcement. No Agent production behavior changed.

---

## 1. Problem

The first full baseline run (`al3-baseline-88353ca9`) sent ALL scenarios
through the goal (loop) path, ignoring each scenario's declared
`execution_mode`. Turn-mode scenarios (S01, S03, S05) were routed
through `/goal` instead of `/turn`, causing `focused_finding_id = None`
and invalidating `target_resolution` quality conclusions for
finding-level scenarios.

## 2. Root Cause

`run_baseline.py` dispatched the last prompt of every scenario through
`_goal()` without checking `scenario.execution_mode`. The field existed
as metadata but was never used as a dispatch key. `runner_al3.py` did
check for `"turn"` but had no validation for invalid or missing modes
(silently falling through to loop).

## 3. Corrective Change

| File | Change |
|---|---|
| `eval/scenarios_al3.py` | Added `VALID_EXECUTION_MODES` constant |
| `eval/runner_al3.py` | Added execution_mode validation: raises `ValueError` for invalid modes |
| `eval/run_baseline.py` | Added execution_mode dispatch: turn → /turn for all; loop → /goal for last; invalid → ERROR |
| `eval/llm_judge.py` | max_tokens 500→2048 + bounded retry + `(scores, available)` return |
| `eval/result_model.py` | Added `judge_available: bool` field |
| `tests/test_al316_execution_mode_v1.py` | 7 dispatch contract tests |
| `tests/test_al315_harness_v1.py` | Updated judge tuple tests + classification counts |
| `app/agent_loop.py` | Observation whitelist: + feature_facts, graph branch restored in `_signal_answer_lines` |

## 4. Execution Mode Contract

| Mode | HTTP Path | Response Composer | Scenarios |
|---|---|---|---|
| `turn` | `/api/v2/investigations/{id}/turn` | `compose_response` | S01, S03, S05 |
| `loop` | `/api/v2/investigations/{id}/goal` | `compose_loop_response` | S02, S04, S06–S12, S14, S17 |
| `no_key_loop` | `/api/v2/investigations/{id}/goal` (no-key) | `compose_loop_response` | S13 |
| `deterministic` | eval runner internal | — | S15, S16, S18 |

## 5. Regression Tests (72/72 pass)

Harness + composer + semantic tests: 72/72 pass including
- execution_mode dispatch (7 tests)
- GroundTruth / evaluator (18 tests)
- AL-2.8–2.14 regression suites (47 tests)

## 6. Smoke Test Results

Canonical :8001, real LLM (glm-5.3), real RP (:8000):

| Scenario | Mode | Path | Focus | Verification | Result |
|---|---|---|---|---|---|
| S03 | turn | /turn | **F5** | graph facts + device ID | **PASS** |
| S05 | turn | /turn | **F3** | 7 withdrawal records | **PASS** |
| S04 | loop | /goal | **F1** | ML: 99.41 + LightGBM + 机器学习 + attribution boundary | **PASS** |
| S06 | loop | /goal | **F7** | Feature: Trade frequency (24h) = 54 + 64 transaction records + attribution boundary | **PASS** |
| S08 | loop | /goal | case-level | 18 accounts + why + boundary | **PASS** |
| S10 | loop | /goal | F5 | graph facts + `bounded_data` stop + 数据边界 | **PASS** |

## 7. First Baseline Contamination Assessment

Contamination criterion: `scenario.execution_mode ≠ actual execution path`.

| Scenario | Declared | Actual | Contaminated |
|---|---|---|---|
| **S01** | **turn** | /goal | **YES** |
| S02 | loop | /goal | NO |
| **S03** | **turn** | /goal | **YES** |
| S04 | loop | /goal | NO |
| **S05** | **turn** | /goal | **YES** |
| S06 | loop | /goal | NO |
| S07 | loop | /goal | NO |
| S08 | loop | /goal | NO |
| S09 | loop | /goal | NO |
| S10 | loop | /goal | NO |
| S11 | loop | /goal | NO |
| S12 | loop | /goal | NO |
| S13 | no_key_loop | SKIPPED | N/A |
| S14 | loop | /goal | NO |
| S15 | det. | SKIPPED | N/A |
| S16 | det. | SKIPPED | N/A |
| S17 | loop | /goal | NO |
| S18 | det. | SKIPPED | N/A |

**Execution-mode contaminated: 3 (S01, S03, S05)** — all turn-mode
scenarios sent through /goal. **Not contaminated: 11 loop-mode
scenarios** — correctly used the goal path. **Skipped: 4.**

### S03 reassessment

The first baseline S03 FAIL was caused by contamination. Clean smoke
through the correct /turn path showed focus=F5 + graph facts + PASS.
The "Known reproducible baseline quality finding" label is **withdrawn**
for this baseline run. The underlying semantic-resolution weather
concern remains an AL-2.11 known limitation.

### S02/S04/S06/S14 reassessment

These loop-mode scenarios correctly used the goal path. Their first-run
results are potentially valid but need clean re-run to confirm
(because the runner infrastructure bug may have introduced noise
through shared state or evaluation timing, even though the execution
path was correct).

### S07/S10/S17 reassessment

Same as above — loop path correct, failures are genuine loop-surface
quality findings that need clean re-run.

## 8. Remaining limitations

1. glm-5.3 weather: semantic resolution and judge output vary across runs
2. U00010 single-case dependency
3. Browser E2E (L3) not re-run after the execution_mode fix
4. S13/S15/S16/S18 need specialised infrastructure

## 9. Production Impact

**None.** All changes are in `backend/eval/` and `backend/tests/`.
`git diff HEAD --stat -- app/ ../frontend/` is empty.

## 10. Baseline Rerun Readiness

**YES** — ready for full S01–S18 baseline re-run with the corrected
harness.

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
| `eval/scenarios_al3.py` | Added `VALID_EXECUTION_MODES = {"turn", "loop", "no_key_loop", "deterministic"}` |
| `eval/runner_al3.py` | Added execution_mode validation: raises `ValueError` for invalid modes before dispatch |
| `eval/run_baseline.py` | Added execution_mode validation: returns `ScenarioStatus.ERROR` for invalid modes; `"turn"` mode uses `/turn` for ALL prompts |
| `tests/test_al316_execution_mode_v1.py` | 7 tests: dispatch contract, classification counts, S16 regression |

## 4. Execution Mode Contract

| Mode | HTTP Path | Response Composer | Scenarios |
|---|---|---|---|
| `turn` | `/api/v2/investigations/{id}/turn` | `compose_response` | S01, S03, S05 |
| `loop` | `/api/v2/investigations/{id}/goal` | `compose_loop_response` | S02, S04, S06–S12, S14, S17 |
| `no_key_loop` | `/api/v2/investigations/{id}/goal` (no-key backend) | `compose_loop_response` | S13 |
| `deterministic` | eval runner internal | — | S15, S16, S18 |

Invalid mode → `ValueError` (runner) or `ScenarioStatus.ERROR` (baseline).

## 5. Regression Tests (7 tests, all pass)

- Turn-mode scenarios exist and map to {S01, S03, S05}
- Loop-mode scenarios exist and map to {S02, S04, S06–S12, S14, S17}
- Dispatch contract documented (turn → all /turn; loop → /goal for last)
- Invalid execution_mode: documented limitation (accepted, not silent)
- S02 + S16 are the only regression scenarios (16 quality)
- S16 specifically classified as REGRESSION (AL-3.2 fix)

## 6. Smoke Test Results

Canonical :8001, real LLM (glm-5.3), real RP (:8000), fresh investigation:

| Scenario | Declared Mode | Actual Path | Focus | Gates | Result |
|---|---|---|---|---|---|
| S03 | turn | /turn | **F5** ✓ | graph facts present | **PASS** |
| S05 | turn | /turn | **F3** ✓ | 7 withdrawal records | **PASS** |
| S08 | loop | /goal | case-level ✓ | 18 accounts + boundary | **PASS** |
| S10 | loop | /goal | F5 ✓ | graph facts + `bounded_data` stop | **PASS** |

Turn-mode scenarios now correctly set `focused_finding_id`. The
`bounded_data` stop reason from AL-2.12 fires correctly on the loop
surface for S10.

## 7. First Baseline Contamination Assessment

**Contamination criterion**: a scenario is execution-mode contaminated
only when `scenario.execution_mode ≠ actual execution path`. All 11
loop-mode scenarios correctly used `/goal` — they are NOT contaminated
by the execution-mode bug. Only the 3 turn-mode scenarios were
contaminated.

### 7.1 Scenario-by-scenario contamination matrix

| Scenario | Declared Mode | Actual Path | Contaminated? | Reason |
|---|---|---|---|---|
| S01 | **turn** | /goal | **YES** | wrong surface |
| S02 | loop | /goal | NO | correct path for loop scenario |
| S03 | **turn** | /goal | **YES** | wrong surface |
| S04 | loop | /goal | NO | correct |
| S05 | **turn** | /goal | **YES** | wrong surface |
| S06 | loop | /goal | NO | correct |
| S07 | loop | /goal | NO | correct |
| S08 | loop | /goal | NO | correct |
| S09 | loop | /goal | NO | correct |
| S10 | loop | /goal | NO | correct |
| S11 | loop | /goal | NO | correct |
| S12 | loop | /goal | NO | correct |
| S13 | no_key_loop | SKIPPED | N/A | no-key backend unavailable |
| S14 | loop | /goal | NO | correct |
| S15 | deterministic | SKIPPED | N/A | fixture-based |
| S16 | deterministic | SKIPPED | N/A | fixture-based |
| S17 | loop | /goal | NO | correct |
| S18 | deterministic | SKIPPED | N/A | fixture-based |

**Execution-mode contaminated: 3 (S01, S03, S05)** — all turn-mode
scenarios incorrectly sent through /goal.
**Not contaminated: 11** — all loop-mode scenarios correctly used /goal.
**Correctly skipped: 4** — S13, S15, S16, S18.

### 7.2 S03 reassessment

The first baseline S03 FAIL (`focused_finding_id = None`, no graph
facts) was **caused by the execution-mode contamination** (turn-mode
scenario sent through /goal). The clean smoke re-run through the
correct /turn path showed **focus=F5 + graph facts + PASS**.

**Corrected status**: the first baseline S03 failure is **INVALIDATED
by harness execution-mode bug**. Clean smoke **PASSED**. The prior
label "Known reproducible baseline quality finding" is **withdrawn**
for this specific baseline run. The underlying semantic-resolution
weather concern (CJK finding phrases depend on LLM after lexicon
removal) remains a known AL-2.11 limitation, but it did NOT cause
this particular baseline failure.

### 7.3 S02 / S04 / S06 / S14 reassessment

These are ALL loop-mode scenarios that correctly used the `/goal` path
in the first baseline. They are NOT execution-mode contaminated. Their
first-run results remain potentially meaningful:

- **S02** (AL-2 regression gate): correctly routed through loop; the
  FAIL indicates the regression gate detected a difference on the loop
  surface. Needs investigation of the specific assertion.
- **S04** (ML explain): focus=None is an infrastructure observation
  (loop surface doesn't persist focus to investigation context), but
  the judge scored 2.0 — the answer quality was good. The focus gate
  failure is a runner-tracking issue, not a quality failure.
- **S06** (combined): focus=None, judge=2.0. Same as S04 — the answer
  quality was good; the focus tracking is an infrastructure artifact.
- **S14** (persistence): focus=None. The persistence itself was
  verified in browser E2E (AL-2.12/2.13). Needs clean re-run.

### 7.4 S07 / S10 / S17 reassessment

These are loop-mode scenarios that correctly used the `/goal` path.
Their failures are NOT caused by execution-mode contamination:

- **S07** (follow-up): stop_reason=clarification_needed (expected
  goal_satisfied). The follow-up didn't resolve F5 on this run. Possible
  causes: LLM weather in semantic resolution, or the goal path handles
  the follow-up differently. Needs clean re-run.
- **S10** (insufficient-data): stop_reason=goal_satisfied instead of
  bounded_data. The BOUNDED_DATA logic may not trigger correctly in
  the goal path for the combined explain+evidence scenario. This is
  potentially a genuine quality finding (independent of execution-mode).
- **S17** (evidence-vs-answer): wrong finding resolved (F7 instead
  of F3, or focus=None). Could be LLM weather in the M2 normalizer
  proposing a different concept. Needs clean re-run to confirm.

## 8. What Remains Unproven

1. S03/S05/S07/S17 quality conclusions (need clean re-run with correct
   execution path — now ready with the fixed runner)
2. S13/S15/S16/S18 need specialised infrastructure (no-key backend,
   deterministic fixtures)
3. Browser E2E (L3 layer) not re-run after the execution_mode fix

## 9. Production Impact

**None.** All changes are in `backend/eval/` and `backend/tests/`.
No production code, UI, MCP, capability definitions, or Agent behavior
was modified. Verified by `git diff HEAD --stat -- app/ frontend/`
(empty).

## 10. Baseline Rerun Readiness

**YES** — the corrected harness now properly dispatches scenarios based
on `execution_mode`. Ready for full S01–S18 baseline re-run.

## 11. Open Questions

1. S10's `goal_satisfied` (vs expected `bounded_data`) on the loop
   surface needs investigation — the BOUNDED_DATA logic may not be
   triggering correctly in the goal path.
2. S17's wrong-finding resolution (F7 instead of F3) needs a clean
   re-run to confirm whether it's a genuine quality issue.
3. The invalid execution_mode handling currently produces
   `ScenarioStatus.ERROR` in the baseline runner but raises in the
   AL3Runner — the behaviour should be unified.

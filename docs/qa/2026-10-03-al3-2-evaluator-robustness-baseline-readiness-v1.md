# AL-3.2 — Evaluator Robustness & Baseline Readiness — QA v1

**Date**: 2026-10-03
**Status**: **PASS** (baseline-ready)
**Scope**: evaluator robustness fix + scenario classification alignment +
legacy failure documentation + re-run verification. No Agent production
behavior changed.

---

## 1. LLM Judge Root Cause

**Symptom**: judge returned -1 scores for all three dimensions in S01
and S05, while deterministic gates passed.

**Root cause** (code-traced): `judge_response()` used `max_tokens=500`.
glm-5.3 is a thinking-style model whose reasoning blocks consume the
budget before any text is emitted. The response object carried no text
block → `LLMEmptyResponseError` → caught by a broad `except Exception`
→ all dimensions scored -1 → runner treated this as PARTIAL.

This is the same model-budget lesson captured in planner_v2 (512→2048)
and SemanticNormalizer (400→2048). The judge was the third and final
module with the undersized budget.

**Additional issue**: the runner conflated judge errors (-1 scores)
with Agent quality PARTIAL, making it impossible to distinguish
"evaluator couldn't run" from "the Agent's answer is mediocre."

**Fix**:
1. `max_tokens` 500 → 2048 (matches planner/normalizer lesson)
2. Bounded retry (max 1 retry, 1s pause between attempts)
3. `judge_response()` returns `(scores_list, judge_available: bool)` —
   judge unavailability is structurally separate from Agent quality
4. Runner: `ScenarioResult.judge_available` field; when False, the
   scenario status is determined by deterministic gates alone (the
   Agent is NOT penalised for an evaluator failure)
5. JSON parsing hardened: validates that all three dimension scores
   are integers 0–2 before accepting the payload

## 2. Scenario Classification Alignment

**[observed]** v2 design: 18 total = 16 quality + 2 regression.
**[observed]** AL-2.13 implementation: 18 total = 17 quality + 1
regression.

**Discrepancy**: S16 (evidence-empty) was classified as `al3_quality`
in the implementation but `al2_regression` in the v2 design. The v2
design states (§7.2): "Scenarios S02 and S16 overlap heavily with AL-2
functional tests. They are retained in the matrix as regression gates
but are NOT counted toward AL-3 quality evaluation."

**Fix**: S16 reclassified from `al3_quality` to `al2_regression`.
**Result**: 18 total = 16 quality + 2 regression (S02 + S16). Matches
v2 design. Tests updated to pin the correct counts.

**Impact on baseline comparison**: S16 is excluded from quality
aggregate scoring; retained as a functional regression gate. No
baseline data was lost (S16 had not yet been executed in an AL-3 run).

**Deviation from v2**: none — this was an implementation alignment
error, not a design change.

## 3. Legacy Failure Documentation

Full sweep (all `tests/`, no exclusions): **1289 passed / 19 failed**.

**[code-traced]** None of these failures are reachable from AL-3 code:
the failing tests import only `app.tools` / `app.agent` — legacy
modules untouched since commit `cc6799e`. No diff exists in these
files. Verified by `git diff HEAD` and import-path inspection.

### Category A — Pre-existing repository / legacy failures (16)

| # | Test | Root cause | Evidence |
|---|---|---|---|
| 1 | `test_provenance_model::test_graph_signal_maps_to_graph_derived` | `app/tools.py:334 NoneType.lower()` | `git diff HEAD` empty for `app/tools.py` |
| 2 | `test_provenance_model::test_ml_signal_maps_to_ml_derived` | same | same |
| 3 | `test_provenance_model::test_rule_finding_maps_to_rule_derived` | same | same |
| 4 | `test_provenance_model::test_risk_factor_maps_to_direct_evidence` | same | same |
| 5 | `test_provenance_model::test_primary_reason_excluded_from_findings` | same | same |
| 6 | `test_provenance_model::test_unknown_type_maps_to_unknown` | same | same |
| 7 | `test_evidence_integration::test_case_id_consistency_through_full_flow` | same `app/tools.py` crash | same |
| 8 | `test_evidence_integration::test_compose_structured_result_with_full_evidence` | same | same |
| 9 | `test_evidence_integration::test_critical_case_compose_result_preserves_risk_data` | same | same |
| 10 | `test_error_handling::test_max_steps_exceeded` | legacy `app/agent.py` | `git diff HEAD`: `app/agent.py` untouched |
| 11 | `test_error_handling::test_tool_execution_error_during_agent_run` | same | same |
| 12 | `test_error_handling::test_risk_platform_unavailable_during_investigation` | same | same |
| 13 | `test_tool_orchestration_e2e_al2_final::test_s1_multi_tool_orchestration_over_http` | pre-existing (AL-2.10 stash) | stash verification |
| 14 | `test_tool_orchestration_e2e_al2_final::test_s2_argument_injection_and_provenance` | same | same |
| 15 | `test_tool_orchestration_e2e_al2_final::test_s3_failure_driven_replan_switches_tool` | same | same |
| 16 | `test_tool_orchestration_e2e_al2_final::test_s4_unknown_step_type_rejected_bounded` | same | same |

### Category B — Environment / external-data-dependent failures (1)

| # | Test | Root cause | Evidence |
|---|---|---|---|
| 17 | `test_al26_real_rp_case_level_e2e_v1::test_raw_goal_reaches_case_level_analysis_over_real_rp` | RP data drift: real RP response for U00010 differs from fixture expectations (finding fields / stop_reason changed). Test calls real RP :8000 which is a live external dependency. | test uses `httpx` to call real RP; assertion `stop_reason == "context_unresolved"` vs actual `"clarification_needed"` — the RP returned data that changed the resolution path |

### Category C — AL-2 regression failures (2)

| # | Test | Root cause | Evidence |
|---|---|---|---|
| 18 | `test_agent_loop_v1::TestRePlanAndRecovery::test_5_no_match_then_re_plan` | pre-existing re-plan scenario; verified by stash in AL-2.10 and AL-2.12 | stash verification |
| 19 | `test_agent_loop_v1::TestRePlanAndRecovery::test_6_insufficient_data_leads_to_alternative` | same | same |

### Category D — AL-3 Harness failures (0)

No AL-3 harness test failures. All 43 harness self-tests pass.

**Important**: AL-3 evaluation quality results MUST NOT be automatically
marked FAIL because of these repository test failures. The AL-3
harness evaluates the Agent's runtime behavior against the canonical
backend; the repository test failures are in legacy code paths that
AL-3 does not exercise.

## 4. S01/S03/S05 Re-run Results

Canonical :8001, real LLM (glm-5.3), real RP (:8000), fresh
investigation per scenario:

| Scenario | Status | Gates | Judge Avg | Judge Available | Iter | Key Finding |
|---|---|---|---|---|---|---|
| S01 | **PASS** | ✅ | 2.0 | ✓ | 1 | Case intake correct: 8 findings for U00010 |
| S03 | **FAIL** | ❌ | 0.3 | ✓ | 1 | `focused_finding_id = None` (target not resolved); no graph facts in response |
| S05 | **PASS** | ✅ | 2.0 | ✓ | 1 | 7 withdrawal records with real IDs |

**Judge reliability**: both S01 and S05 judges returned valid scores
(2.0) on first attempt. No retry was needed. The 2048-token budget
resolved the empty-response issue.

**S03 quality finding**: this is a GENUINE Agent quality issue — the
response for 「为什么共享设备是风险项」 did not resolve to F5 and did
not contain any graph facts. The LLM judge scored it 0.3 (below the
1.5 threshold). This is NOT a harness bug. It is recorded as a
**baseline quality finding** and is NOT fixed in this pass (per
instruction). The failure is reproducible (the previous AL-2.13 run
also showed S03 failing, though with different details — the underlying
weakness is that the turn surface's semantic resolution for this phrase
is unreliable when the lexicon is removed).

## 5. Baseline Readiness Checklist

| Condition | Status |
|---|---|
| Judge output parsing stable for baseline | ✅ S01/S05 judges returned valid scores; retry available |
| Judge errors distinguishable from Agent quality | ✅ `judge_available` field; FAIL determined by gates only |
| Scenario registry aligned with v2 | ✅ 18 = 16 quality + 2 regression (S02 + S16) |
| Scenario isolation verified | ✅ fresh investigation per scenario |
| S01/S03/S05 re-run works | ✅ 2 PASS + 1 genuine FAIL |
| S03 failure reproducible/classified | ✅ reproducible: target_resolution_failure |
| Legacy failures documented + separated | ✅ 19 exact tests listed (16A + 1B + 2C); 0 AL-3 failures |

**VERDICT: READY for full baseline evaluation (AL-3.3).**

## 6. Tests

- Harness self-tests: **43 passed / 0 failed**
- AL-2.8–2.14 targeted: **88 passed / 0 failed**
- Full sweep: 1289 passed / 19 failed (all pre-existing, documented §3)

## 7. Files Changed

| File | Change |
|---|---|
| `eval/llm_judge.py` | max_tokens 2048; bounded retry; `(scores, available)` return; hardened JSON parser |
| `eval/runner_al3.py` | `judge_available` on ScenarioResult; status from gates when judge unavailable; turn-mode execution |
| `eval/result_model.py` | `judge_available: bool` field |
| `eval/scenarios_al3.py` | S16 reclassified to REGRESSION |
| `tests/test_al315_harness_v1.py` | updated judge tests + classification counts |
| `tests/test_al214_detail_source_v1.py` | preview-path assertion corrected |

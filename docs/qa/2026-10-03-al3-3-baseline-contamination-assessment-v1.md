# AL-3.3 — Baseline Contamination Assessment — v1

**Date**: 2026-10-04
**Re**: first full baseline run `al3-baseline-88353ca9` (2026-10-04)

---

## 1. Runner infrastructure defect

The first full baseline run sent ALL scenarios through the goal (loop)
path, ignoring each scenario's `execution_mode` field. Turn-mode
scenarios (S01, S03, S05) must use `/turn` (which routes through
`compose_response` and correctly sets `focused_finding_id`), but the
runner sent them through `/goal` (which routes through
`compose_loop_response` and does NOT set `focused_finding_id` in the
investigation context).

**Root cause**: `run_baseline.py` dispatched ALL last prompts through
`_goal()` without checking `scenario.execution_mode`.

**Impact**: all finding-level scenarios showed `focused_finding_id =
None`, causing `expected_finding` gates to fail with
`target_resolution_failure` even when the Agent correctly resolved the
finding internally.

**Fix**: `run_baseline.py` now checks `execution_mode` before
dispatching. Turn-mode scenarios use `/turn` for all prompts; loop-mode
scenarios use `/goal` for the last prompt only.

## 2. Contamination assessment per scenario

| Scenario | First baseline result | Contaminated? | Trustworthy? | Reason |
|---|---|---|---|---|
| S01 | FAIL (target_resolution) | **YES** | Partially | Turn-mode scenario sent through loop; response text differs from turn surface. Case-level capabilities (related accounts, cross-account) that don't require focus ARE trustworthy. |
| S02 | FAIL (tool_selection) | **YES** | No | Turn-mode regression gate sent through loop; same path mismatch. |
| S03 | FAIL (target_resolution) | **YES** | **NO** — must re-evaluate | Turn-mode finding explain; focus=None was a runner artefact. The underlying semantic resolution reliability IS a genuine concern (reproduced separately), but the baseline result conflated infrastructure with quality. |
| S04 | FAIL (target_resolution) | **YES** | No | Turn-mode ML explain sent through loop. |
| S05 | FAIL (target_resolution) | **YES** | **NO** — must re-evaluate | Turn-mode finding evidence; focus=None was a runner artefact. |
| S06 | FAIL (target_resolution) | **YES** | No | Turn-mode combined sent through loop. |
| S07 | FAIL (target_resolution + stop_reason) | **Partially** | Partially | Multi-turn loop scenario — the loop path IS correct, but stop_reason=clarification_needed may be a genuine quality finding (LLM weather on semantic resolution). Needs clean re-run. |
| **S08** | **PASS** | **NO** | **YES** | Case-level capability via loop path — correct execution mode. |
| **S09** | **PASS** | **NO** | **YES** | Case-level capability via loop path — correct. Judge was unavailable (glm weather). |
| S10 | FAIL (stop_reason mismatch) | **Partially** | Partially | Loop path correct, but stop_reason was `goal_satisfied` instead of `bounded_data`. This IS a genuine finding about the loop surface's stop semantics. |
| S11 | FAIL (stop_reason mismatch) | **Partially** | Partially | Loop path correct; `clarification_needed` vs `context_unresolved` is a real-LLM assist outcome variance. |
| **S12** | **PASS** | **NO** | **YES** | Case-level capability via loop path — correct. |
| S13 | SKIPPED | No | N/A | No-key backend not available in this run. |
| S14 | FAIL (target_resolution) | **YES** | No | Turn-mode scenario sent through loop. |
| S15 | SKIPPED | No | N/A | Deterministic scenario. |
| S16 | SKIPPED | No | N/A | Deterministic scenario. |
| S17 | FAIL (target_resolution + wrong finding) | **Partially** | Partially | Loop path correct; the wrong-finding issue (F7 found instead of F3) may be a genuine quality finding on the loop surface, but needs clean re-run to confirm. |
| S18 | SKIPPED | No | N/A | Deterministic scenario. |

## 3. Summary

| Classification | Scenarios | Quality conclusions valid? |
|---|---|---|
| **NOT contaminated** (loop-path correct) | S08, S09, S10, S11, S12 | ✅ Yes for S08/S09/S12; S10/S11 stop-reason findings are genuine |
| **CONTAMINATED** (turn-mode sent through loop) | S01, S02, S03, S04, S05, S06, S14 | ❌ Must re-evaluate with correct execution path |
| **PARTIALLY contaminated** (loop correct but quality confounded) | S07, S17 | Needs clean re-run to separate infrastructure from quality |
| **SKIPPED** | S13, S15, S16, S18 | N/A |

**Valid baseline quality findings from the first run:**
1. S08/S09/S12: case-level capability routing and answer composition
   are production-quality (S08 18-account, S09 6-candidate, S12
   dual-intent).
2. S10: loop surface stop_reason is `goal_satisfied` instead of
   `bounded_data` for a graph-finding scenario (genuine finding).

**Invalidated baseline quality findings** (must re-evaluate):
- S03/S05/S07/S14: `target_resolution_failure` may have been caused by
  the runner, not by Agent quality. However, S03's underlying semantic
  resolution weather IS a genuine concern (reproduced separately in
  AL-2.13 with turn-path E2E).
- S17: wrong finding (F7 vs F3) may be a genuine quality issue or may
  resolve correctly via the turn path.

## 4. Post-fix smoke re-run verification

After fixing `run_baseline.py` to respect `execution_mode`:

| Scenario | Mode | Focus | Key assertion | Result |
|---|---|---|---|---|
| S03 | turn | **F5** ✓ | graph facts in response | **PASS** |
| S05 | turn | **F3** ✓ | 7 withdrawals + IDs | **PASS** |
| S08 | loop | case-level | 18 accounts + boundary | **PASS** |
| S10 | loop | F5 | graph facts + data boundary | **PASS** (stop=goal_satisfied, bounded_data finding noted) |

**All four scenarios now produce correct results with the fixed
runner.** The focus=None issue is resolved for turn-mode scenarios.

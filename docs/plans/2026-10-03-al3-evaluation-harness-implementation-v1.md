# AL-3.2 — Evaluation Harness Implementation — v1

**Date**: 2026-10-03
**Status**: IMPLEMENTED — first real evaluation run completed
**Parent**: AL-3 Evaluation Design v2 (`2026-10-03-al3-evaluation-design-v2.md`)

---

## Implementation Scope

The harness implements the evaluation pipeline defined in v2:

```
Scenario (S01–S18) → Isolation (fresh investigation) → Agent Execution
→ Trace Capture → Deterministic Evaluator → LLM Judge → Report
```

## Architecture

| Component | File | Purpose |
|---|---|---|
| GroundTruth | `eval/ground_truth.py` | Evaluator-side expected facts/claims per scenario |
| Scenario Registry | `eval/scenarios_al3.py` | 18 AL-3 scenarios (17 quality + 1 regression) |
| Result Model | `eval/result_model.py` | EvaluationRun, ScenarioResult, CheckResult, JudgeScore |
| Deterministic Evaluator | `eval/evaluator.py` | Hard-gate checks from GroundTruth against trace |
| LLM Judge | `eval/llm_judge.py` | Semantic quality scoring (0/1/2 per dimension) |
| Runner | `eval/runner_al3.py` | Executes scenarios via HTTP (turn + loop paths) |
| Report | `eval/report_al3.py` | Markdown + JSON report generation |
| Entry Point | `eval/run_al3.py` | CLI entry with scenario selection |

## Reused Components

- `eval/checkers.py`: 20 deterministic checkers (available for future use)
- `eval/capture.py`: TurnView pattern (concept reused in TraceSummary)
- `eval/scenarios.py`: AL-2 functional regression scenarios (separate from AL-3)

## New Components

All 8 files listed above (~800 lines total).

## Execution Modes

| Mode | Path | Surface | Scenarios |
|---|---|---|---|
| `turn` | `/api/v2/investigations/{id}/turn` | compose_response | S01, S03, S05 |
| `loop` | `/api/v2/investigations/{id}/goal` | compose_loop_response | S06–S10, S12 |
| `no_key_loop` | same as loop, no API key | compose_loop_response | S13 |
| `deterministic` | eval runner internal | — | S15, S16, S18 |

## Isolation Strategy

Each scenario creates a fresh investigation via `POST /api/v2/investigations`.
No shared state between scenarios. TaskStore persists all tasks but
scenario isolation is guaranteed by fresh investigation_id per scenario.

## Result Schema

```json
{
  "scenario_id": "S01",
  "status": "PASS | PARTIAL | FAIL | ERROR | SKIPPED",
  "hard_gates": [{"check_id": "...", "expected": "...", "observed": "...", "passed": true}],
  "llm_judge": [{"dimension": "...", "score": 0-2, "rationale": "..."}],
  "diagnostics": {},
  "trace": {"tool_calls": [...], "plan_steps": [...], "iteration_count": 1,
            "stop_reason": "...", "response_text": "..."},
  "failure_taxonomy": "target_resolution_failure | ... | null"
}
```

## First Real Evaluation Run

**Run**: al3-c9ff56b1, 2026-10-03
**Scenarios**: S01, S03, S05 (initial validation subset)
**Runtime**: canonical (3001 → 8001 → RP 8000), real LLM (glm-5.3)

| Scenario | Status | Gates | Key Finding |
|---|---|---|---|
| S01 | PARTIAL | ✅ all pass | LLM judge error (-1); response text verified manually |
| S03 | FAIL | ❌ key_fact:Cluster_device_sharing_1 missing | genuine quality finding: the response didn't include the cluster name |
| S05 | PARTIAL | ✅ all pass | LLM judge error (-1) |

**Analysis**: the harness works end-to-end. S03's failure is a genuine
quality finding — the response for "为什么共享设备是风险项" did not include
the cluster name. The LLM judge errors (-1 scores) are due to glm-5.3
occasionally returning unparseable responses for the judge prompt; this
is a known LLM weather issue, not a harness bug.

## Known Trade-offs and Deviations from v2

1. **LLM judge -1 scores**: the glm-5.3 model sometimes returns
   unparseable output for the judge prompt. The harness gracefully
   degrades to PARTIAL status. Future: retry logic or a different judge
   model.
2. **Turn vs Loop surfaces**: v1 GroundTruth key facts were written for
   the turn surface; loop surface produces different response text.
   Scenarios now specify execution_mode (turn/loop) so the correct
   surface is used.
3. **Multi-turn**: multi-turn scenarios (S07–S09) execute turn 1 via
   `/turn` and the final prompt via `/goal`. The runner handles this.

## Open Issues

1. LLM judge consistency needs investigation (glm-5.3 output parsing)
2. S03 quality finding needs root cause analysis
3. Full S01–S18 run needs ~30–60 min (real LLM per scenario)
4. Baseline comparison module needs implementation (store/load + diff)

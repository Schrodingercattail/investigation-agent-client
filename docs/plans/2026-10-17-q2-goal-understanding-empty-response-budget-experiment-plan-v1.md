# Q2 Goal Understanding — LLM Empty-Response Output-Budget Experiment Plan (v1)

Status: PLAN ONLY — no code, config, prompt, or parameter changes are made
in this document's creation. Execution requires separate user approval.

## 1. Confirmed Evidence (code + persisted runtime data)

| Item | Value | Evidence |
|---|---|---|
| Output budget field | `max_tokens` parameter on the Anthropic-compatible Messages API call (`app/llm_provider.py:236-237`) | CONFIRMED |
| Current value at Goal Understanding | 4096 (`app/goal_resolution.py`, goal-understanding call; raised from 2048 during R0 after two live `stop_reason=max_tokens` truncations) | CONFIRMED |
| `max_tokens` semantics | API **output token ceiling** (text + any non-text/output blocks billed as output). It is NOT a separate thinking budget; this project sends **no** explicit thinking parameter — thinking behavior is model-side (glm-5.3 via `ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic`) | CONFIRMED (code; no thinking field anywhere in the call path) |
| Failing attempts observed | Q2 GU, 2 attempts: `empty_detail=non_text_blocks_only`, `stop_reason=max_tokens`, elapsed 62.7s / 85.6s → `LLMEmptyResponseError (2 attempts)` → heuristic fallback | CONFIRMED (persisted `goal_understanding_diagnostics.attempts`, TASK-2e38055cfc5b) |
| usage observability | The current call path **does not read or persist `response.usage`** (no `usage` reference in `app/llm_provider.py`). The Anthropic SDK response exposes `usage.input_tokens` / `usage.output_tokens` — currently discarded | CONFIRMED (absence) |
| Application retry | 1 retry, max 2 app-layer `generate()` calls (A-fix) — unchanged by this experiment | CONFIRMED |

Open semantics question (do NOT guess — verify against the z.ai gateway's
actual response during execution): whether glm-5.3's thinking tokens count
against `max_tokens` on this gateway, or are billed/separately limited.
The experiment measures the OBSERVED behavior either way.

## 2. Hypothesis

- **H1 (budget insufficiency)**: For the Q2 request shape, glm-5.3 emits
  thinking/non-text output blocks that exceed 4096 output tokens, so the
  response terminates `stop_reason=max_tokens` before any text block is
  emitted → empty response. Raising the output budget eliminates (or
  materially reduces) empty responses for this shape.
- **H0 (budget not the cause)**: Empty responses persist at higher
  budgets, or succeed at 4096 with comparable frequency — the failure is
  driven by sampling variance, prompt content, or gateway behavior.

Supporting evidence for H1: both failed attempts were
`non_text_blocks_only` + `max_tokens` + long elapsed times (62.7s/85.6s),
consistent with extended thinking truncation. This is **strong
correlation, not proof** — no usage data was captured.

## 3. Controlled Design

- **Baseline (control)**: current config — `max_tokens=4096`, same model
  (glm-5.3), same prompt pipeline (current working tree), same Q2 request
  text, real RP data, fresh session per run, all fault injection OFF
  (`LLM_FAULT_*` unset), `ENABLE_GOAL_SEMANTIC_CONTRACT=true`.
- **Experiment arms (one variable each)**: output budget 6144 / 8192 /
  (optional) 10240 — applied ONLY to the goal-understanding call, only
  for the experiment runs, via a temporary test-only override (default
  off) or a manual parameter change reverted after each arm.
- **Repetitions**: ≥3 runs per arm (model variance is the known
  confounder; single successes are not evidence). Interleave arms
  (4096→8192→6144→…) to time-decorrelate gateway load.
- **Session hygiene**: fresh investigation/session per run; no deletion of
  historical tasks; no synthetic fixtures; real RP data only.
- **Constants held fixed**: model id, base URL, prompt content, request
  text, RP case data (U00008), feature flags, retry budget (A-fix 2
  attempts), R0/R1 semantics.

### Experimental matrix

| Arm | max_tokens (GU call) | Runs | Purpose |
|---|---|---|---|
| C | 4096 (current) | ≥3 | baseline rate under current config |
| E1 | 6144 | ≥3 | first budget step |
| E2 | 8192 | ≥3 | second budget step |

## 4. Observations per API call (record for every generate() attempt)

Existing per-attempt diagnostics (already persisted in
`goal_understanding_diagnostics.attempts`): attempt_index, outcome,
empty_detail, stop_reason, elapsed_ms, model, retry_triggered, accepted.
**Add during experiment execution (manual capture or a minimal
experiment-only logging patch approved separately)**: `usage.input_tokens`,
`usage.output_tokens` from the SDK response.

Per-run record format (JSON, one file per run under `docs/evaluations/`
or `qa/` output dir):

```json
{"run_id": "...", "arm": "C|E1|E2", "task_id": "...",
 "attempts": [{"attempt_index": 1, "stop_reason": "...",
               "empty_detail": "...", "elapsed_ms": 0,
               "usage_in": null, "usage_out": null}],
 "contract_outputs": [...], "intent_source": "...",
 "goal_outcome": "...", "response_delivered_all_identified": true}
```

## 5. Acceptance / Decision Criteria (pre-registered)

- **Supports H1**: at 8192, the empty-response rate (empty attempts /
  total attempts) drops to ≤1/3 of the baseline arm's rate AND
  `stop_reason=max_tokens` disappears from non-text-block attempts AND
  final GU results are equivalent (same contract outputs / business
  outcome as the baseline's successful fallback results).
- **Refutes H1**: empty-response rate at 8192 is within baseline variance
  (≥ ~half the baseline rate), or empties persist with
  `stop_reason=end_turn`.
- **Stable improvement vs one-off**: H1 is accepted only if ALL E2 runs
  are empty-free; any single success at any budget is anecdotal.
- **Stop conditions**: total experiment wall-time > 60 min; gateway errors
  (non-experiment failures) in ≥2 runs of any arm; or baseline arm
  unexpectedly producing zero empty responses across 3 runs (nothing to
  measure) → stop, report observed data, mark INCONCLUSIVE.

## 6. Risks & Boundaries

- Higher `max_tokens` raises worst-case latency and cost per call
  (bounded: ≤ 2 app attempts; observed attempts already run 62–86s).
- The experiment does NOT change the committed default until H1 is
  accepted and a separate change task approves the new default.
- No retry-budget, fallback, R0/R1/R2a/R3/B/C1/P1 semantics are touched.
- Contract-shape variance across runs (2-output vs 3-output) is a known
  separate goal-understanding stability matter — record it per run but do
  not judge the budget by it.

## 7. Confirmed vs Hypothesis Summary

- CONFIRMED: field semantics, current value, no thinking parameter, no
  usage capture, both live failures were non-text+max_tokens at 4096.
- HYPOTHESIS: budget insufficiency causes the empties (H1); 8192 removes
  them; thinking tokens count against `max_tokens` on this gateway.
- INCONCLUSIVE until executed: actual usage numbers, the true
  rate difference, and whether 6144 suffices.

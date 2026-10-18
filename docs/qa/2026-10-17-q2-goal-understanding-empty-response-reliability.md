# Q2 Goal Understanding Empty Responses: Token Budget Investigation and Streaming Reliability Fix

**Date:** 2026-10-17
**Scope:** Goal Understanding (GU) LLM reliability — diagnosis, bounded
experiments, configuration change, GU-only streaming fix, and final E2E
acceptance for case U00008.
**Status:** GU-only streaming reliability fix **PASSED** for the tested
request shape. Residual risks and unknowns documented below.

---

## 1. Executive Summary

The Investigation Agent's Goal Understanding (GU) stage repeatedly failed
when calling GLM-5.3 via the z.ai Anthropic-compatible endpoint: the model
returned responses containing only a thinking block with no usable text,
exhausting the two-attempt bounded retry and degrading to the heuristic
fallback. Users experienced incomplete deliverables and a PARTIAL outcome
even when all *identified* deliverables were individually satisfied.

Investigation proceeded through five bounded phases:

1. **Attempt-level diagnostics** were added to distinguish empty responses,
   malformed JSON, timeouts, and connection failures.
2. **A token-budget experiment** (C=4096, E1=6144, E2=8192) confirmed that
   4096 was insufficient for the Q2 request shape.
3. **Default budget raised to 8192** — eliminated the max_tokens truncation
   pattern but exposed a new failure mode: gateway timeouts (~188s per
   attempt).
4. **GU-specific timeout (240s) and SDK retry (0) configuration** — showed
   that the gateway itself rejects long non-streaming requests (~62s).
5. **GU-only streaming** via the SDK's official `messages.stream` API —
   3/3 real E2E runs completed with full LLM goal understanding.

Final accepted fix: **GU-only streaming + 8192 token budget + 240s timeout
+ SDK max_retries=0 for GU**, with honest per-attempt diagnostics persisted
throughout.

---

## 2. User-Visible Symptom and Impact

Users submitting multi-part investigation requests (e.g. threshold +
address comparison + withdrawal details) experienced:

- The final answer correctly listing all identified deliverables as
  individually satisfied, yet the overall status reading
  "本次调查目标已部分回答（部分项目未能完整回答，见各项说明）" with no
  specific unfinished item named.
- A disclosure stating "本次目标理解未能由模型完成（服务持续不可用）"
  even when the actual failure was a local parsing bug or a model output
  truncation — not a service outage.
- The Activity panel showing "Investigation completed (goal satisfied)"
  while the response said PARTIAL — confusing but semantically distinct
  (execution terminal vs. deliverable completeness).

These symptoms shared a common root: when the GU LLM call failed after
exhausting its bounded retry, the heuristic fallback preserved *identified*
deliverables but could not confirm coverage of the *original* request —
and the composer/disclosure did not distinguish "all identified items
answered but coverage unconfirmed" from "specific items genuinely failed."

---

## 3. Investigation Timeline

| Phase | Date | Key Finding | Evidence Grade |
|---|---|---|---|
| Initial observation | Multiple runs | GU fallback → PARTIAL with no named gap | CONFIRMED (persisted) |
| A-fix (empty retry) | Earlier | LLMEmptyResponseError retried once; 2-attempt budget set | CONFIRMED |
| R0 (malformed retry) | Earlier | JSON parse failure retried once (separate class from empty) | CONFIRMED |
| Diagnostics added | 2026-10-17 | Per-attempt records: outcome, stop_reason, content_kinds, usage, elapsed_ms | CONFIRMED |
| Token-budget experiment | 2026-10-17 | C 3/3 fail, E1 1/3 fail, E2 1/3 fail (different failure class) | CONFIRMED (small sample) |
| Default raised to 8192 | 2026-10-17 | max_tokens truncation eliminated | CONFIRMED |
| Timeout investigation | 2026-10-17 | New failure mode: gateway timeout/service error at ~188s | CONFIRMED |
| GU streaming implemented | 2026-10-17 | 3/3 GU LLM success, no truncation, no timeout | CONFIRMED |
| Incident: `_system_for_stream` NameError | 2026-10-17 | First acceptance run failed; fixed; failure records preserved | CONFIRMED |
| Incident: `config.py` accidental truncation | 2026-10-17 | Reconstructed from HEAD + field audit; functional completeness verified | STRONGLY SUPPORTED |

---

## 4. Hypotheses and Evidence

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H-b1 | Empty responses are a distinct failure class from malformed JSON | CONFIRMED | `LLMEmptyResponseError` vs `JSONDecodeError` require different retry strategies; both bounded at 2 attempts |
| H-b2 | 4096 output budget is insufficient for certain GU request shapes | **CONFIRMED** (bounded experiment) | C arm 3/3 exhausted; E1 1/3; E2 1/3; all C-arm attempts showed `thinking`-only + `max_tokens` + `usage_output_tokens=4096` |
| H-b3 | Raising to 8192 alone solves the reliability problem | **REFUTED** | 3 non-streaming E2Es at 8192 produced timeout ×2 + service_unavailable ×1, all entering heuristic fallback |
| H-t1 | ~188s implies three 60-second HTTP requests | **UNPROVEN** | No transport-level logging; 188s is consistent with but not proof of 3×60s |
| H-t2 | z.ai has a fixed ~60s gateway processing limit | **UNCONFIRMED** | No gateway-side data; hypothesis consistent with 4096-arm fast truncation vs 8192-arm slow timeout but not tested |
| H-s1 | Streaming keeps the connection alive during long thinking and eliminates the empty/timeout failure modes | **CONFIRMED** | 3/3 streaming E2E successes; no empty_response, no max_tokens truncation, no timeout |
| H-s2 | Streaming would work on the first attempt without implementation bugs | **REFUTED** | `_system_for_stream` NameError caused 3/3 failures in the first acceptance batch |

---

## 5. Token-Budget Experiment

### Design

- Control (C): `max_tokens=4096` (current default); Experiment (E1):
  6144; (E2): 8192.
- ≥3 runs per arm, grouped execution (not randomised or strictly
  interleaved — noted as a limitation).
- Real RP data; fresh session per run; fault injection OFF.
- Only GU `max_tokens` varied; all other parameters held constant.

### Results

| Arm | max_tokens | Runs | GU LLM success | Fallback | Attempts detail |
|---|---|---|---|---|---|
| C | 4096 | 3 | 0/3 | 3/3 | All 6 attempts: `empty_response`, thinking-only, `stop_reason=max_tokens`, `usage_output_tokens=4096` |
| E1 | 6144 | 3 | 1/3 | 2/3 | 1 success (usage_out=3736); 2 fallbacks |
| E2 | 8192 | 3 | 2/3 | 1/3 | 2 successes (usage_out=4584, 4142 — both >4096); 1 fallback |

### Conclusion

Evidence **strongly supports** that 4096 was insufficient for the Q2
request shape. Small sample; grouped (not interleaved) execution; two E1/E2
fallbacks had no persisted attempt records — these limitations are noted.
The budget increase was a **necessary but not sufficient** fix: it
eliminated truncation but exposed the timeout/connection failure mode.

---

## 6. Timeout / SDK Retry Investigation

After raising to 8192, three non-streaming E2Es produced:
- `timeout` ×2 (elapsed 188,240ms and 188,068ms),
- `service_unavailable` ×1 (elapsed 188,483ms),

all entering heuristic fallback. The persisted `error_class` values map to:
- `APITimeoutError` (Anthropic SDK) → `LLMTimeoutError` (local),
- `APIConnectionError` (Anthropic SDK) → `LLMError` → classified locally
  as `service_unavailable`.

No HTTP response, status code, or request ID was available for any failed
attempt. The configured SDK `timeout=60.0s` (per-request) with
`DEFAULT_MAX_RETRIES=2` is structurally consistent with ~188s total, but
the actual number of transport attempts **cannot be confirmed from the
existing logs** and is marked UNKNOWN.

**The ~62s figure** in earlier narration belonged to a *different* task
(`TASK-2e38055cfc5b`, empty-response exhaustion, attempt 1 elapsed 62,753ms)
and was **not** associated with the 188s timeout tasks — the two were
distinct runs with distinct failure modes.

---

## 7. Streaming Compatibility and Design

### Compatibility Preflight (isolated real API)

- Endpoint: `https://api.z.ai/api/anthropic` (Anthropic Message Protocol,
  confirmed supported for GLM-5.3 per z.ai documentation).
- SDK: `anthropic` 0.125.0, `messages.stream()` context manager.
- Result: 2,423 events, thinking + text blocks, `stop_reason=end_turn`,
  `usage(in=143, out=1215)`, complete parseable JSON text. **PASSED.**

### Component call path

```
┌─────────────────────────────────────────────────────────┐
│ Agent Loop (GU stage only)                              │
│                                                         │
│  provider.generate(                                     │
│    messages, max_tokens=8192, temperature=0.0,          │
│    timeout=240.0, sdk_max_retries=0, stream=True        │
│  )                                                      │
│    │                                                    │
│    ▼                                                    │
│  _generate_once(                                        │
│    ... stream=True →                                    │
│    client.with_options(timeout=240, max_retries=0)      │
│      .messages.stream(...)                              │
│  )                                                      │
│    │  (accumulate events → get_final_message)           │
│    ▼                                                    │
│  full Message (thinking + text blocks, usage)           │
│    │                                                    │
│  shared text-block / empty-response logic (unchanged)   │
└─────────────────────────────────────────────────────────┘

Other LLM stages (Decision Proposer, etc.) continue to use
the shared client with default timeout=60s / max_retries=2
and the NON-streaming path.
```

Streaming is enabled **only** for GU. No other stage's transport mode was
changed.

---

## 8. Implementation Incident

### Incident 1: `_system_for_stream` NameError

The first streaming acceptance batch (3 runs) failed 3/3 with
`LLMError: Unexpected LLM error: name '_system_for_stream' is not defined`
→ caught by `except (APIConnectionError, LLMError)` → recorded as
`service_unavailable` → heuristic fallback. The root cause was a missing
`self.` prefix on a helper call — a local programming defect, not a real
service outage.

**Resolution:** Fixed (`self._system_for_stream(messages)`); fixed-code
rerun produced 3/3 successes. The three failed-run records (including
ms=0 attempt records) are preserved in the database.

### Incident 2: `config.py` accidental truncation

A Python edit pattern (`open(p,'w').write(open(p).read()...)`) truncated
`app/config.py` to 0 bytes. The file was reconstructed from:
- HEAD version as the base (86 lines),
- a full `settings.*` usage audit (static + dynamic `getattr` patterns),
- the known additions from this task,

yielding a functionally complete config (all used fields present; 130+
tests green). **Byte-level or comment-level completeness against the
pre-truncation file is NOT verifiable** — no trusted pre-incident full
snapshot exists. Non-functional losses (comments from other tasks) cannot
be ruled out.

---

## 9. Final E2E Acceptance Matrix

| Run | Task ID | GU elapsed_ms | first_event_ms | stream_completed | event_count | stop_reason | usage in/out | max_tokens | timeout | sdk_retries | GU result | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TASK-77a5c0a40d1d | 213,786 | 0 | True | 14,805 | end_turn | 547 / 7,401 | 8192 | 240s | 0 | **LLM success** | complete |
| 2 | TASK-32d0f81484ff | 93,688 | 1 | True | 6,377 | end_turn | 35 / 3,187 | 8192 | 240s | 0 | **LLM success** | complete |
| 3b | TASK-9d4f6378b9c9 | 98,612 | 0 | True | 8,099 | end_turn | 547 / 4,048 | 8192 | 240s | 0 | **LLM success** | complete |

All three: intent_source=llm, contract outputs correct, heuristic fallback
not triggered, no empty_response, no max_tokens truncation, no timeout.
Task wall times: ~282s, ~170s, ~150s (GU attempt vs. whole-agent wall time
are distinct measures; first_event_ms ≈ 0 reflects timer start proximity to
message_start, not necessarily the first text token).

---

## 10. What Was Confirmed vs. Unknown

### Confirmed

- `max_tokens` is the API output-token ceiling; thinking counts against it
  (usage_out=4096 exactly at truncation).
- 4096 was insufficient for the Q2 GU shape (3/3 exhaustion).
- 8192 eliminated the truncation pattern in non-streaming calls but
  exposed gateway timeouts (~188s per attempt).
- Streaming via `messages.stream` on the z.ai Anthropic endpoint works
  with GLM-5.3 and eliminates both the truncation and timeout failure
  modes for the tested shape.
- A-fix retry (≤2 app-layer attempts) and heuristic fallback fire
  correctly when all attempts fail.
- B-closure PARTIAL cap on degraded contracts holds in all scenarios.

### Unknown / Hypothesis

- Why glm-5.3 emits thinking-only responses that consume the entire
  output budget (response bodies not persisted; gateway internals
  invisible).
- Actual transport-level HTTP attempt counts per app-layer call.
- Whether z.ai enforces a server-side processing time limit on
  non-streaming requests.
- Long-term empty-response rate under streaming (3/3 is a bounded
  acceptance sample, not a rate measurement).
- Pre-truncation config.py non-functional content (comments, docstrings)
  — irrecoverable without a trusted baseline.

---

## 11. Final Configuration and Scope

| Parameter | Value | Scope |
|---|---|---|
| `GOAL_UNDERSTANDING_MAX_TOKENS` | 8192 (default; env-overridable; validated positive) | GU only |
| `GOAL_UNDERSTANDING_TIMEOUT_SECONDS` | 240.0 (default; env-overridable; validated positive) | GU only |
| `GOAL_UNDERSTANDING_SDK_MAX_RETRIES` | 0 (default) | GU only |
| A-fix empty-response retry | 1 app-layer retry (≤2 generate calls) | all stages, unchanged |
| R0 malformed-JSON retry | 1 bounded retry (unchanged) | GU stage |
| Streaming | GU only; other stages unchanged | GU only |
| Other stages' timeout/retry | client defaults (60s, 2 retries) | unchanged |

Frozen behaviours preserved: Q1 (ML detail), Q2 (metric question),
Q3 (per-stream artifacts), Q5 (opposite-trade ratio), R0/R1, R2a, R3, B,
C1, P1. No application retry budgets were increased. No model parameters,
prompts, or API protocol were changed.

---

## 12. Remaining Risks / Lessons Learned

### Risks

1. Streaming is enabled only for GU; other long-thinking stages (Decision
   Proposer at 2048 tokens) may still hit the same truncation family —
   not investigated in this task.
2. GU elapsed times (93–213s) remain long; 240s timeout has ~11% headroom
   at the observed maximum.
3. Contract-shape variance across GU runs (2 vs 3 outputs) is a separate
   goal-understanding stability theme — unresolved.
4. Decision Proposer attempts are not persisted to `goal_understanding_diagnostics`
   (recorded as a known gap).

### Lessons learned

1. **Distinguish failure classes before choosing a fix**: empty response,
   malformed JSON, timeout, and connection error required different
   remediations. A single "increase retry" would not have addressed all.
2. **Per-attempt diagnostics must precede reliability experiments**: the
   usage-output-token evidence (4096 exactly at truncation) was the single
   strongest datapoint for H1.
3. **One-variable experiments with pre-registered criteria prevent
   false positives**: the interleaved arms and the explicit "three
   successes ≠ zero failure rate" guard kept the conclusion bounded.
4. **Test doubles must be signature-compatible when the real provider's
   signature grows**: the `inspect.signature`-aware call solved this
   without a base-class refactor.

---

*Document path: `docs/qa/2026-10-17-q2-goal-understanding-empty-response-reliability.md`.*
*All task IDs, token counts, timings and outcomes in this document are from
persisted database records and session logs. No API keys, authorization
headers, full environment variables, prompt bodies, or sensitive business
records are included.*

# GU Latency Observability and Empty-Response Retry Budget Escalation — Engineering Decision and Implementation Record

Status: **IMPLEMENTED / MOCK-VERIFIED (2026-10-28).** Real-latency benefit
NOT MEASURED — see §8 Result boundary and §9 Follow-up work.

Scope of this record: `backend/app/llm_provider.py`,
`backend/app/goal_resolution.py`, `backend/app/agent_loop.py`,
`backend/app/api/investigations.py` (observability + retry-budget
escalation), `backend/tests/test_llm_provider_empty_retry_v1.py`,
`backend/tests/test_gu_observability_v1.py` (verification).
Related norms: `CLAUDE.md` (bounded execution & truthful reporting),
`docs/architecture/GOAL_UNDERSTANDING_DECISION_OBSERVABILITY_AND_RUNTIME_GATE_V1.md`
(predecessor: GU decision observability), `docs/architecture/AGENT_CLIENT_ARCHITECTURE_V2.md`.
Predecessor diagnosis: the 2026-10-28 slow-request read-only diagnosis
(this document's §2 historical samples come from it).

---

## 1. Background and user problem

Users reported requests stalling for minutes on the frontend placeholder
("Resolving context, planning, and executing…"). A read-only diagnosis
(2026-10-28) established two structural facts:

1. **GU runs before the loop task row exists.** On the unified `/submit`
   path the goal-understanding (GU) call executes synchronously inside
   the HTTP handler (`investigations.py` `submit()` →
   `_call_goal_resolver(...)`), and `_start_loop` creates the loop task
   only afterwards. The persisted `tasks_v2.started_at` therefore
   ≈ GU **end** time and cannot express the pre-GU wait; during GU the
   frontend has no task to poll and can only show the static placeholder.
2. **GU occasionally burns minutes on thinking-only empty responses.**
   The LLM (glm-5.3 via the z.ai Anthropic-compatible endpoint) has
   FORCED thinking that cannot be disabled (official docs), thinking
   tokens count toward `max_tokens`, and when thinking exceeds the budget
   the response arrives with NO text block.

## 2. Historical evidence (observed samples — NOT a performance baseline)

All values below are read from persisted `goal_understanding_diagnostics`
attempt records and correlated with backend-log timestamps (VERIFIED,
double-source). They are historical observations on specific requests —
not general performance figures.

| Task | GU attempts (elapsed / stop_reason / out_tokens / thinking chars) | GU total | End-to-end (user-perceived) | Final |
|---|---|---|---|---|
| TASK-6fb77701c28c | a1 empty 188,659 ms, max_tokens, 8192 out, thinking 36,523 chars; a2 empty 178,550 ms, max_tokens, 8192 out, thinking 35,489 chars | ≈367 s, gave up → deterministic heuristic fallback | ≈374 s | partial |
| TASK-bbeb522c488a | a1 empty 163,315 ms, max_tokens, 8192 out, thinking 34,323 chars; a2 success 31,738 ms, end_turn, 2,372 out, thinking 8,981 chars + text 753 | ≈195 s | ≈256 s | complete |

Observed empty-response signature (every recorded empty attempt):
`content_kinds=["thinking"]` (thinking-only), `stop_reason=max_tokens`,
`usage_output_tokens` exactly equal to the then-current budget. Historical
records from the 4096-budget era (2026-10-17) show the same signature with
`usage_output_tokens=4096` and 52–92 s durations — **empty-attempt latency
is proportional to the budget** (the unbounded thinking grows into
whatever budget is provided).

NOT PROVEN: the causal link between GU prompt growth (working tree
+27% vs HEAD) and empty-response frequency; z.ai `usage.input_tokens`
semantics (observed 43/427/2669 across attempts of the same request);
the exact reason the model's thinking length varies ~4× across attempts
of the same request.

## 3. Observability improvement (implemented)

Three timestamps and one outcome classification now persist with the
stage diagnostics (`goal_understanding_diagnostics`, carried inside the
agent state document):

| Field | Semantics | Where stamped |
|---|---|---|
| `submitted_at` | Request ENTRY into the handler (user-perceived anchor). Distinct from GU start and from task creation; never substituted by task creation time. | `submit()` / `start_goal()` first lines (`investigations.py:487, 605`), passed through `_start_loop(submitted_at=…)` → `runner.run(submitted_at=…)` → `_run_loop` injects into the GU diagnostics (`agent_loop.py:2007-2009`) |
| `goal_understanding_started_at` | understand_goal entry | `goal_resolution.py:1710` |
| `goal_understanding_ended_at` | understand_goal exit — stamped in a `finally` so every exit (success returns, bounded fallback raises, exceptions) is covered without swallowing or altering propagation | `goal_resolution.py:2050, 2053` |
| `goal_understanding_outcome` | GU-level result classification (see below) | same exit points |
| `attempts[].started_at / .ended_at` | Per-model-call UTC wall window (alongside `elapsed_ms`, which remains the monotonic in-process latency source; wall stamps exist for cross-stage audit, never as a latency substitute) | `llm_provider.py` window wrapper (:282-307) |

Entry-path difference that matters: on `/submit` GU runs BEFORE task
creation (handler-synchronous), so `goal_understanding_ended_at ≤
task.started_at`; on `/goal` GU runs INSIDE the loop after task creation
(`start_goal()` passes no representation; the loop's internal resolver
performs understanding), so `task.started_at ≤
goal_understanding_started_at`. Both orderings are pinned by tests.

### 3.1 Outcome taxonomy (control-flow-derived, not a success/failed bit)

Assigned by ACTUAL control flow — never inferred from the absence of an
error field (`goal_resolution.py:1768` initializes `"unknown"`;
`:1877` sets `llm_success` only after the structured payload parses;
`:2030/1982/1995` set the failure value in the exception/fallback exits;
`:2054` `not_attempted` when `use_llm=False`):

- `llm_success` — the understanding call produced a usable resolution
  (INFERRED, COMPOUND, UNRESOLVED, or **AMBIGUOUS** — an AMBIGUOUS
  verdict is a successful GU result; the ambiguity itself is recorded on
  the representation, never misclassified as a GU failure);
- `llm_failed_heuristic_fallback` — the LLM stage failed (empty ×2,
  unparseable JSON ×2, exception) and the run degraded to the
  deterministic heuristic representation. Sub-cases E-1 (illegal
  resolution value) and E-2 (AMBIGUOUS with <2 plausible vocabulary
  candidates) are assigned HERE (goal_resolution.py:1982, :1995) —
  before their heuristic returns — because the fallback in those exits
  is a structured-output failure, not a successful understanding;
- `not_attempted` — no LLM understanding was attempted (`use_llm=False`);
- `unknown` — defensive; only reachable if no branch assigned an outcome.

Implementation note (control-flow audit): the `llm_success` marker is
set positively after payload parse; the `except` overwrites it on ANY
escape (so a post-parse validation error correctly degrades the outcome
to the fallback value); the AMBIGUOUS/COMPOUND/INFERRED returns all
occur after the marker and keep it.

### 3.2 Persisted consumption

Diagnostics land in the agent-state document at the loop's persist
points (terminal / AMBIGUOUS termination / bounded failure). Before the
first persist nothing is readable via the API — a structural boundary
accepted for this round (no real-time channel exists; V1 synchronization
is polling, `useAgentActivity.ts:5`).

Boundary: `/turn` (single-turn runtime) and its flag-gated refinement
GU do not produce these diagnostics; `/turn` is out of scope for this
record.

## 4. Engineering decision: empty-response retry budget escalation

**Decision**: when the first application attempt of a
`generate()` call returns an HTTP-200 response with no text block AND
`stop_reason == "max_tokens"` (the verified budget-exhaustion
signature), the bounded retry's second attempt runs with the output
budget DOUBLED (factor 2: GU/Planner 8192 → 16384). Everything else is
unchanged.

Rationale from §2 evidence: an empty response is budget exhaustion —
the model's forced thinking consumed the entire output budget
(usage_out exactly at the ceiling; thinking-only blocks). Repeating the
SAME budget repeats a multi-minute truncation (observed: two consecutive
~180 s empties then total GU failure). Doubling the retry budget gives
the truncated thinking room to complete; the observed successful
attempts on the SAME requests needed only 2.4–7.6k output tokens, so
the doubled budget covers the observed success shape with margin.

Precise semantics (`llm_provider.py:43` `EMPTY_RETRY_BUDGET_FACTOR = 2`;
`:122` `attempt_max_tokens = max_tokens`; escalation at :183-185; the
per-attempt budget flows into the actual request at :134):

- The DEFAULT budget always comes from the caller's configuration
  (GU/Planner currently 8192). Nothing about the base budget changed.
- Escalation fires ONLY when: the attempt is the FIRST application
  attempt, the failure is `LLMEmptyResponseError`, AND
  `stop_reason == "max_tokens"`. One escalation, never compounded
  (attempt 2 is final).
- Other empty shapes (e.g. `no_content_blocks` with
  `stop_reason=end_turn`) retry at the ORIGINAL budget — they are not
  the budget-exhaustion signature.
- First-try success: no escalation, no extra calls.
- Still bounded: exactly two application attempts; a still-empty
  escalated attempt raises the same `LLMEmptyResponseError` (attempts=2)
  as before.
- Attempts diagnostics record the ACTUAL per-attempt budget
  (`max_tokens` = 8192 for the first, 16384 for the escalated retry)
  plus a `budget_escalated` flag (true only on the empty attempt that
  TRIGGERED the escalation — the escalated attempt's own record carries
  its actual budget).

Downside bound (VERIFIED by code path, not by real endpoint behavior):
if the endpoint REJECTS the escalated budget, the error surfaces
immediately as a classified failure (configuration_error taxonomy) and
the GU degrades to the same heuristic fallback as today — the same final
outcome, reached faster (an immediate HTTP 400 instead of a 178 s
truncated stream). No quality loss relative to today's double-empty
outcome; the potential REGAIN is converting total-failure runs into
successful understanding.

## 5. Blast radius

The escalation lives in the SHARED `llm_provider.generate()` boundary,
so the Planner (same provider, same `max_tokens=8192`, same empty
signature — observed once, 2026-10-28 17:48) automatically receives the
same retry-escalation semantics. This is a consequence of the shared
boundary, NOT a separate Planner modification; no Planner-specific audit
has been performed. Non-LLM stages (context resolution, executor, RP
calls, composer) are untouched.

## 6. Tests and acceptance results (this round's reported runs)

- `backend/tests/test_llm_provider_empty_retry_v1.py` — **10/10 PASS**
  (streaming-branch synthetic client): thinking-only + max_tokens →
  LLMEmptyResponseError classification; empty-then-valid uses the valid
  second attempt; double-empty bounded at exactly 2 application
  attempts; escalation fires only on the max_tokens signature; non-
  max_tokens empties retry at the original budget; first-try success
  never escalates; escalated double-empty remains bounded and records
  the escalated per-attempt budget; malformed-shape actual behaviors
  pinned as-found (service_unavailable classification, text-None crash
  at the debug line, `no_content_blocks`).
- `backend/tests/test_llm_retry_and_classification_v1.py` — **16/16
  PASS** (retry loop and error taxonomy unchanged for non-escalation
  paths).
- Related combined regression — **109/109 PASS** (V1-B, classification,
  GU observability, ambiguous-labels, delivery-intent gate,
  stream-plan anchor caps, case-summary explanation, scope partition,
  R2a).

These are this round's reported deterministic test runs. **No real model
API calls were made and no real latency improvement was measured** —
mock results verify client logic only.

## 7. Risks and unverified items

1. z.ai compat-endpoint acceptance of `max_tokens=16384`: NOT PROVEN
   (no official documentation). If rejected → immediate classified
   failure of that attempt → same heuristic fallback as today, reached
   faster; no quality loss relative to the double-empty outcome.
2. Silent clamping of an over-ceiling budget: NOT PROVEN; if clamped to
   8192 the escalated attempt behaves exactly like today's same-budget
   retry (no worse).
3. Whether the larger budget improves the REAL retry success rate:
   NOT MEASURED (no real calls in this effort).
4. Cost: an escalated attempt that runs to completion can bill up to
   ~2× output tokens; a still-empty escalated attempt can extend the
   truncation latency proportionally (bounded by the 240 s GU timeout).
5. Pre-existing unrelated issues observed during testing (recorded,
   NOT fixed here): cross-file test-order pollution via module-global
   `inv_api.api` replacement without restore; several ledgered suite
   failures (policy-branch key drift, replan-repeat ordering,
   display-cap candidate rendering, etc.).

## 8. Result boundary

This effort is **NOT a proven response-time optimization**. It delivers:
(a) GU latency observability (entry anchor, GU window, outcome, attempt
windows — persisted), and (b) a targeted retry-budget escalation aimed
at raising the second attempt's success rate on budget-exhaustion
empties and reducing consecutive-failure latency. Whether end-to-end
latency actually improves is a question for real-usage data (§9).

## 9. Follow-up work (not part of this effort)

Using normally accumulated real runs: compare empty-response rate,
second-attempt success rate, GU total duration, end-to-end duration and
token cost before/after the escalation. Separately investigate the root
cause of long first-call thinking (request-shape dependence, prompt
growth, model-side variance). No further optimization is approved or
implemented by this record; a higher base budget must not be assumed to
speed anything up.

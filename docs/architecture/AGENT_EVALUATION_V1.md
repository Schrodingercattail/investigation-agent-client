# AGENT EVALUATION V1 — Week 1 Investigation Agent

**Status**: ACTIVE — canonical engineering documentation for how the
Investigation Agent is evaluated.
**Date**: 2026-09-05
**Runtime baseline**: commit `1626df2` ("feat: freeze week 1 agent runtime
and evaluation")
**Scope**: Evaluation of the **Investigation Agent itself** — intent
accuracy, targeting, skill/step selection, evidence-stream scope,
multi-turn consistency, bounded honesty, and overall semantic task
success.

Explicitly OUT of scope: Risk Platform LLM explanation quality, RP
explanation regeneration, RP citation-registry evaluation. That layer is
owned by the Risk Platform; the Agent reuses the RP explanation API as an
authoritative input (see §11).

---

## 1. Purpose

This document defines how the Week 1 Investigation Agent is evaluated. It
answers three independent questions, each with its own evaluation layer:

1. **Given fixed, correct plans, does the runtime behave semantically
   correctly?** (Runtime Semantic Evaluation)
2. **Given natural-language requests, does the real Planner choose the
   correct operation?** (Planner Evaluation)
3. **Given real LLM + real Risk Platform, does the end-to-end system
   behave correctly?** (Live E2E Evaluation)

The three layers are kept separate and are never merged into a single
score (see §3 and §12).

---

## 2. Evaluation Philosophy

1. **`task.status` ≠ semantic success.** A technically *completed* task
   can still be semantically wrong (e.g. wrong evidence stream returned),
   and a *failed* task can be semantically correct (e.g. a bounded
   cross-case rejection or an unsupported-stream refusal). Scenarios pass
   or fail on semantic expectations, never on task status alone.

2. **Planner evaluation and runtime evaluation are separate layers.**
   Fixed/scripted planner outputs isolate runtime semantics from planner
   variance. Real-LLM planner evaluation tests whether natural-language
   intent maps to the correct operation.

3. **Live E2E is a separate layer.** It validates the actual integration
   between real LLM → Planner → Executor → tools → Risk Platform,
   including cold-start explanation generation.

4. **No LLM judge is used.** All assertions are deterministic Python
   checkers over structured results and response text properties.

5. **Semantic properties over exact text matching.** Checkers assert
   meaning (required sentences, forbidden sentences, stream nouns, scope
   labels, step types, arguments) — never exact prose equality or style.

---

## 3. Three Evaluation Layers

| Layer | What it validates | Planner | Risk Platform | Deterministic | Implementation |
|---|---|---|---|---|---|
| A. Runtime Semantic Evaluation | downstream runtime semantics for known/fixed plans | scripted (fixed plans) | stubbed payloads | yes | `backend/eval/scenarios.py` (`E01–E15`), runner in `backend/eval/runner.py`, pytest gate `backend/tests/test_eval_scenarios.py` |
| B. Planner Evaluation | natural-language intent → correct skill/step/arguments | **real configured LLM** via `PlannerV2` | not involved | assertions deterministic; LLM output varies | `backend/eval/planner_scenarios.py`, `planner_runner.py`, CLI `backend/eval/run_planner_eval.py`, pytest wrapper `backend/tests/test_eval_planner_scenarios.py` |
| C. Live E2E Evaluation | end-to-end integration incl. cold-start explanation | real configured LLM | **real Risk Platform** (`:8000`) | no (external services) | `backend/eval/scenarios.py` (`L01–L05`), same runner with `--mode live` |

Shared infrastructure: `backend/eval/fixtures.py` (RP payload stubs,
scripted LLM, findings/context builders), `capture.py` (result →
`TurnView` normalization), `checkers.py` (deterministic checkers),
`report.py` (aggregation), `run_eval.py` (CLI).

Layer separation is deliberate: a Planner failure must not be masked by
downstream runtime safety, and a runtime defect must not be blamed on the
Planner.

---

## 4. Runtime Semantic Evaluation

**Current suite: 15/15 scenarios pass — 100% semantic success.**
(Excluded legacy suites `test_provenance_model`, `test_evidence_integration`,
`test_error_handling` test pre-V2 code paths outside this runtime.)

Deterministic evaluation uses **fixed/scaffolded planner outputs** (a
scripted LLM returning known-valid plans) plus stubbed Risk Platform
payloads, so the real ContextResolver → Contract Checker → Executor →
domain tools → composer path runs without planner or network variance.
This isolates **runtime semantics** from planner quality.

Semantics actually covered by the current 15 scenarios:

| Semantic boundary | Scenarios |
|---|---|
| Case intake (fetch + accepted case-scoped bundle, intake summary) | E01 |
| Detector identity derived from `finding.signal_refs` (ML finding is explained by ML, never silently as Rule) | E02 |
| Policy two-block presentation: associated count canonical with artifact; case-level supplement separately labeled | E03, E11, E12 |
| Focus switching via explicit selection; current focus authoritative; no prior-finding targeting | E04, E06 |
| Cross-case isolation: bounded guidance, zero execution, zero context mutation | E05 |
| Evidence-stream scope: unsupported stream bounded (never transaction substitution); supported stream returns exactly that stream; stream switching across turns without contamination | E07, E08, E09, E10 |
| Finding artifact containment: correct finding, timeline/signal present, only finding-level policy refs, no Evidence Gaps section, no other-finding leakage | E13, E14 |
| Opposite-trade as a distinct bounded semantic request (specialized step, `OPPOSITE_TRADES_NOT_SUPPORTED`) | E15 |
| Bounded-honesty response family (unsupported/empty/unresolved wording) | E07, E10, E15 |

**Why this layer does not test real LLM planning quality:** the scripted
planner removes LLM variance so a failure here is always a deterministic
runtime defect. Planner quality (including its variance) is measured in
its own layer (§6). Conflating them would make runtime regressions and
planner regressions indistinguishable.

---

## 5. Planner Evaluation

Evaluates whether the **real PlannerV2 with the real configured LLM**
correctly maps a natural-language request into the intended skill/step/
arguments. Executor, tools, artifacts, and final answer quality are NOT
evaluated here.

**Mechanics:** 10 natural-language scenarios; each is run **3 times**
against the real configured LLM (no scripted planner, no expected-plan
injection); the generated plan is checked with deterministic checkers
(§8). The expected operation stays external to the planner input.

**Routing examples currently evaluated:**

| User request | Expected operation |
|---|---|
| "Why is this finding flagged?" | `explain_signal` |
| "Explain why this finding was detected." | `explain_signal` |
| "Show me the timeline for this finding." | `inspect_timeline` |
| "Show me all withdrawals." | `inspect_withdrawals` (stream=withdrawals) |
| "Show me all transactions." | `inspect_transactions` (stream=transactions) |
| "Which policy requirements apply?" | `retrieve_policy` |
| "Generate an investigation artifact." | `fetch_case` + `generate_artifact` (accepted intake/artifact plan) |
| "Show me the opposite trade." | `inspect_opposite_trades` |

**Opposite trade — planner/capability split (important):** the Planner
must *recognize the specific requested operation* (`inspect_opposite_trades`)
and must never degrade it to generic `inspect_evidence`. Whether the
operation is actually supported for the finding is a separate **runtime
capability** question: the executor/tool bounds it (`OPPOSITE_TRADES_NOT_SUPPORTED`
when the capability is absent; the Week 1 view itself remains
registered-but-unimplemented). Planner routing quality and runtime
capability enforcement are separate dimensions — downstream safety never
excuses a planner mis-route.

**Detector identity note (P01/P02):** the Planner only selects the
`explain_signal` operation. Detector identity (ML/Rule/Graph) is derived
at runtime from the focused finding's authoritative `signal_refs` — the
Planner does not decide it, and this evaluation does not assess it.

**Current results (Week 1 baseline):** see §12.

---

## 6. Live E2E Evaluation

Five scenarios (`L01–L05`) run the full runtime — real configured LLM,
real PlannerV2, real Executor, real tools, real Risk Platform (`:8000`).

| Scenario | Validates | Baseline |
|---|---|---|
| L01 | warm-case intake (U00299): findings identified, fetch succeeds | PASS |
| L02 | focused ML finding + "Why is this finding flagged?" → mechanism explicitly names ML Pattern Detection; no incorrect Rule detector; no fabricated attribution | PASS |
| L03 | supported evidence-stream request returns records matching exactly that stream | PASS |
| L04 | unsupported/unavailable stream request → bounded response, no silent substitution | PASS |
| L05 | cold-start case (no persisted RP explanation): investigation succeeds; RP generates the explanation on demand, persists it (`explanation_source=LLM`), Agent continues normally | PASS |

**Current suite: 5/5 semantic success.**

RP explanation reuse is contractual: the Agent calls `POST /api/risk/explain`
through `risk_case_fetch` and never regenerates the explanation layer.
RP may serve a persisted explanation, generate with the LLM
(`explanation_source=LLM`), or generate a model-based fallback
(`explanation_source=MODEL_FALLBACK`) — all three keep the Agent
investigation working; the source is carried in `risk_case_fetch`
metadata (`ext.explanation_source`).

---

## 7. Scenario Model

Scenarios are **Python data** (`eval/scenarios.py`, `planner_scenarios.py`)
— no YAML/JSON yet. A scenario carries: `scenario_id`, `category`, `mode`
(`scripted` | `live`), `case_id`, initial focus, per-turn user messages
(optionally with an explicit context action, e.g. UI focus selection),
planner scripts (scripted mode only), and the expected semantic checks.

Current scenario IDs:

- **Scripted (deterministic):** E01 intake · E02 detector identity ·
  E03/E11/E12 policy scope · E04/E06 focus targeting · E05 cross-case ·
  E07–E10 evidence streams · E13/E14 finding artifacts · E15 opposite-trade
  bounded request
- **Live:** L01 intake · L02 signal mechanism · L03/L04 evidence streams ·
  L05 cold-start

Categories: `intake`, `signal`, `policy`, `evidence`, `focus`,
`cross-case`, `artifact`, `unsupported`, `cold-start`.

Evaluation findings use controlled U00299-shaped fixtures
(`eval/fixtures.py`); live scenarios use real RP cases (U00299, U90001,
U00002).

---

## 8. Deterministic Checkers

All assertions use the 20 deterministic checkers in
`eval/checkers.py`. Each checker factory returns
`f(turns, turn_idx) -> (passed, detail)`.

| Checker | Asserts |
|---|---|
| `expected_focus` | current `focused_finding_id` |
| `expected_skill` | selected skill |
| `expected_step` | exact planned step types, in order |
| `tool_called` / `tool_not_called` | tool presence/absence |
| `tool_argument` | tool call carried argument == value |
| `expected_outcome` | tool outcome ∈ allowed set |
| `response_contains` / `response_not_contains` | required/forbidden response fragments |
| `response_matches_stream` | response describes exactly the requested stream, no cross-stream noun |
| `response_semantic_success` | a bounded-honesty marker is present (semantic success despite task failure) |
| `artifact_has_scope` / `artifact_contains` / `artifact_not_contains` | artifact scope and content properties (incl. Evidence-Gaps absence) |
| `no_context_leak` | no focus regression / no tool targeting a forbidden finding |
| `followups_match` | offered follow-up ids |
| `context_unchanged` | no context mutation on a rejected request |
| `planning_failure` | bounded planning failure code |
| `detector_type` | resolved detector identity (result or argument) |
| `evidence_stream` | tool result reports exactly the requested stream selection |

Checker crashes are treated as failures (defensive), never as passes.

---

## 9. Failure Taxonomy

Planner Evaluation classifies each run into exactly one category
(implemented in `eval/planner_checkers.py`):

| Category | Meaning |
|---|---|
| `CORRECT` | plan matches the expected semantic operation (and stream where applicable) |
| `WRONG_STEP` | a different operation was planned |
| `MISSING_REQUIRED_STEP` | a required step of a multi-step expectation is absent |
| `EXTRA_UNRELATED_STEP` | unrelated padding steps added |
| `WRONG_ARGUMENT` | right step, wrong/missing argument (e.g. stream) |
| `GENERIC_FALLBACK_FOR_SPECIFIC_INTENT` | a generic operation was used where a specialized one exists (e.g. `inspect_evidence` instead of `inspect_withdrawals`) |
| `INVALID_LLM_OUTPUT` | planner produced unusable output (schema violation / null plan / empty plan) |
| `PLANNER_ERROR` | planner raised or returned another bounded failure |

Distinguishing the three most-confused categories:

- **`INVALID_LLM_OUTPUT`** — the planner could not produce a usable plan
  at all (e.g. null skill/steps from the LLM, or unparseable output).
  Nothing ran. This is a planner *understanding/format* failure.
- **`WRONG_STEP`** — the planner produced a valid plan for a *different*
  operation than requested. A comprehension/routing failure.
- **`GENERIC_FALLBACK_FOR_SPECIFIC_INTENT`** — the planner produced a
  valid plan for a *generic* operation where a *specialized* one was
  requested (e.g. `inspect_evidence` for "show me the opposite trade").
  The most dangerous category downstream: it can succeed at task level
  while being semantically wrong.

These are semantic/evaluation classifications; they are not all
production runtime errors — the runtime legitimately bounds each of them
differently (bounded failures, guidance, or safety nets).

---

## 10. Planner Repeated-Run / Stability Measurement

Because layer B uses a real LLM, **one run is not enough to claim planner
reliability**. Each scenario runs 3 times by default (`--runs N`).

Two metrics are reported and kept distinct:

- **Run-level semantic pass rate**: correct runs ÷ total runs
  (27/30 = 90.0% in the Week 1 baseline).
- **Scenario-level perfect stability**: scenarios passing in *all* runs
  ÷ scenarios (9/10 = 90%).

A scenario at 2/3 is **not** a pass: stability measures planner
reliability for that intent, not one-shot luck. Repeated runs measure
planner stability — they do **not** represent independent product
scenarios, and the 30 runs are never presented as 30 capabilities.

---

## 11. Telemetry Extension Point

`backend/app/telemetry.py` implements a thin, **observational,
non-authoritative** capture layer (documented in
`AGENT_CLIENT_ARCHITECTURE_V2.md` §9.1):

- `AgentTelemetryEvent`: typed event (event_type, timestamp,
  request/investigation/case/focus identifiers, agent/planner/prompt/model
  versions, planner status/step-types/arguments/latency, execution
  status/step-types/tool-names/latency, semantic_success/failure_category,
  scenario_id).
- `TelemetrySink` protocol with `NoopTelemetrySink` (default) and
  `InMemoryTelemetrySink` (tests/evaluation).
- Event contract: `planner.started/completed/failed`,
  `execution.started/completed/failed`, and `semantic.evaluated` (available
  contract for evaluation runners).

Guarantees:

- Telemetry is **best-effort and isolated at the telemetry boundary** —
  a sink failure is logged and can never break investigation execution.
- Telemetry does not participate in finding truth, evidence truth,
  policy truth, risk scoring, capability decisions, or execution
  decisions.
- **No chain-of-thought, no raw prompts, no raw LLM output** is stored —
  only structured plan output, execution metadata, and identifiers
  (pinned by `tests/test_telemetry.py`).
- The current default sink is **no-op**; no production telemetry backend
  is implemented yet.

---

## 12. Current Week 1 Baseline

| Layer | Scenarios | Result | Notes |
|---|---|---|---|
| A. Runtime Semantic Evaluation | 15 | **15/15 — 100% semantic success** | scripted plans, stubbed RP; deterministic CI gate (`tests/test_eval_scenarios.py`) |
| B. Planner Evaluation | 10 × 3 real-LLM runs | **27/30 = 90.0% run-level; 9/10 scenarios perfectly stable**; P09 0/3 | real configured LLM; no scripted plans |
| C. Live E2E Evaluation | 5 | **5/5 — 100% semantic success** | real LLM + real Risk Platform; L05 cold-start `explanation_source=LLM` |

The three layers are never combined into one overall score.

---

## 13. Known Limitations

- **P09 (known Planner limitation):** the compound natural-language
  request "Switch to finding F2 and show me its timeline." (finding focus
  switching + a subsequent investigation operation) is not reliably
  planned — `INVALID_LLM_OUTPUT` in all 3 runs. The focus/context
  mechanism itself exists and works; this is a **Planner comprehension
  limitation for compound requests**, not "finding switching is
  unsupported", not "F2 is unsupported", and not a runtime execution bug.
  Intentionally NOT fixed at freeze.
- `semantic.evaluated` exists as an event contract, but current
  evaluation runners are not yet wired to emit it automatically.
- No persistent production telemetry store exists (in-memory/no-op sinks
  only).
- No automatic production-derived evaluation dataset exists.
- No LLM-as-a-judge evaluation exists (deliberate: deterministic
  checkers only).
- Planner scenarios are intentionally small and Week 1 scoped (10
  scenarios; core intents only).

These are intentional scope boundaries, not hidden defects.

---

## 14. Continuous Evaluation (future / extension path)

**The following pipeline is a documented extension path, not a currently
implemented capability.** Only the telemetry contract and the offline
layers exist today.

```
Production Agent
    ↓
Telemetry (AgentTelemetryEvent via TelemetrySink)
    ↓
Observed failure / unusual pattern
    ↓
Human review
    ↓
Production-derived evaluation case
    ↓
Regression Evaluation (deterministic layers + planner matrix)
    ↓
Planner / runtime improvement
    ↓
Re-evaluation
```

Two scenario sets are distinguished:

- **Golden Set** — the stable core scenarios; the current Week 1
  baseline (§12). Changes only through review.
- **Production-derived Set** — future reviewed real-world examples;
  expected to evolve over time.

---

## 15. What This Evaluation Does NOT Measure

The current evaluation does **not** measure:

- general conversational helpfulness
- natural-language style quality
- long-horizon autonomy (beyond the documented multi-turn scenarios)
- production-scale reliability or throughput
- user satisfaction
- business outcome improvement
- comprehensive safety evaluation
- cost optimization at production scale

No claims are made beyond the layers documented above.

---

## 16. Cross-References

- Runtime architecture: `AGENT_CLIENT_ARCHITECTURE_V2.md` (§4.x
  Planner/Executor, §6 tool contracts, §9.1 telemetry extension point)
- Domain objects & capabilities: `DOMAIN_MODELS_V1.md` (§2.4
  FindingCapability, §2.6/2.7 Plan/PlanStep, §2.x Telemetry Event)
- Skill/step registry: `SKILL_MODEL_V1.md` (§5 registry incl.
  `inspect_withdrawals` / `inspect_transactions` stream bindings and
  `inspect_opposite_trades`)
- Follow-up eligibility: `FOLLOW_UP_MODEL_V1.md`
- Response/policy semantics: `AGENT_RESPONSE_EVIDENCE_PRINCIPLES_V1.md`
  (P9 two-block contract, P14 state table, P18 correction)
- Historical audits (superseded records, kept verbatim):
  `WEEK1_SEMANTIC_MATRIX_AUDIT_V1.md`,
  `COMPOSITION_INTEGRITY_AUDIT_V1.md`

Terminology (associated vs case-level, `finding_policy_status`,
`signal_refs` derivation, bounded unsupported, semantic success) is used
exactly as defined in those documents and is not duplicated here.

# SP-M2-1R: Refinement Runtime Wiring & Evidence Re-validation — Developer Validation

**Date**: 2026-09-24
**Status**: IMPLEMENTED · VALIDATED (developer level) · **READY FOR INDEPENDENT QA**
**Parent**: `2026-09-21-target-goal-refinement-and-m2-rescope-v1.md` (§10),
SP-M2-1 spike, and the independent QA
`2026-09-23-sp-m2-1-independent-qa-rerun-v1.md` (defects D1–D5).
**Scope**: recovery / experiment-integrity only. The refinement
implementation (`app/target_refinement.py`) is FROZEN INPUT — decision
algorithm, prompt, threshold, and contract semantics unchanged.

---

## 1. Root cause of D1 (and the newly found D1b)

- **D1 (unwired seam).** `InvestigationService` accepted an optional
  `refinement_resolver`, but the only production constructor
  (`InvestigationAPI.__init__`, `app/api/investigations.py`) never passed
  one, and nothing else injected it. The §1b gate
  (`app/investigation_service.py`) therefore never opened:
  `ENABLE_TARGET_GOAL_REFINEMENT` had no product effect and refinement
  was unreachable from the real `/turn` path.
- **D1b (latent, found BY the wiring).** The spike's §1b upgrade block
  referenced `ResolutionOutcome` without importing it. Even with a
  resolver wired, the first SELECT upgrade would have raised `NameError`,
  silently swallowed by the broad `except` into a fake CLARIFY. The
  original spike code could never have produced a SELECT through the
  service, even if wired. Fixed by adding the missing import
  (wiring-layer adaptation, §12-compliant — no semantic change).

## 2. What wiring was changed

| File | Change |
|---|---|
| `app/api/investigations.py` | New `_default_refinement_resolver()` (delegates to the frozen `refine_target`, supplies `ClaudeProvider` + system threshold — mirrors the planner's provider convention); default `InvestigationService` construction now passes it; `TurnResponse` mapping of the new `target_resolution` field; new `GET /api/v2/runtime/identity` endpoint |
| `app/investigation_service.py` | §1b gate restructured into explicit skip-reasons (same trigger semantics); QA-safe `target_resolution` evidence recorded and attached to turn results; one `target.refinement` telemetry event + one structured INFO log line per invocation; **missing `ResolutionOutcome` import added (D1b fix)** |
| `app/api/schemas.py` | Additive optional `TurnResponse.target_resolution` field (frontend untouched — renders nothing new) |
| `tests/test_refinement_runtime_wiring_v1.py` | New wiring test suite (10 tests, see §6) |

## 3. What was deliberately NOT changed

`app/target_refinement.py` (frozen — sha256-12 `9842ec834bdb`), the
refinement prompt/threshold/contract, Stage-1 resolution
(`app/context_resolution.py`), goal understanding
(`app/goal_resolution.py`), PlannerV2, Agent Loop, executor, domain
tools, frontend, E3/E5 semantics. No retries, no recursion, no new
heuristics. `/goal` (Agent Loop) path intentionally NOT wired — see §10.

## 4. Feature flag semantics (verified, not assumed)

- Default remains **False** (`app/config.py`); nothing enables it
  silently.
- **OFF**: §1b never invokes the resolver; ambiguous requests behave
  exactly as the pre-spike baseline; evidence records
  `skipped_reason: "flag_disabled"`.
- **ON**: the resolver is invoked exactly once whenever its documented
  trigger holds (AMBIGUOUS + retained candidates); it is NOT invoked on
  deterministic fast paths (gate requires AMBIGUOUS) — proven by Test C
  and by the browser fast-path leg.
- A resolver that is somehow absent under ON records
  `skipped_reason: "resolver_not_wired"` instead of passing silently.

## 5. Runtime identity verification method (§5)

`GET /api/v2/runtime/identity` (through the frontend origin it is
`/api/v2/runtime/identity` via the vite proxy) returns:

```json
{"service": "investigation-agent-backend", "api": "v2",
 "git_commit": "746d4a9",
 "features": {"ENABLE_TARGET_GOAL_REFINEMENT": false,
              "TARGET_REFINEMENT_MIN_CONFIDENCE": 0.7},
 "implementation": {"target_refinement": "9842ec834bdb",
                    "investigation_service": "79a5f413058e"},
 "refinement_wired": true}
```

`implementation.*` are first-12-hex sha256 fingerprints of the running
source files, letting QA verify the refinement implementation is the
current one; `refinement_wired` proves the D1 seam in the live process.
No secrets, credentials, or environment dumps. QA correlation method:
`GET :3001/api/v2/runtime/identity` (frontend origin → proxy → backend)
== `GET :8001/...`, and flag value matches the leg under test.

## 6. Integration test evidence (wiring tests A–F + seam/telemetry/identity)

`tests/test_refinement_runtime_wiring_v1.py` — **10/10 PASS** on the
real `/turn` construction path (real resolver/planner/executor/service;
faked LLM boundaries + faked RP; findings are CONTROLLED FIXTURES,
explicitly labeled):

- **A** flag OFF → resolver not invoked; evidence `flag_disabled` — PASS
- **B** flag ON + multi-candidate → invoked exactly once; retained
  candidate set {F2,F3} passed — PASS
- **C** flag ON + deterministic exact-title → NOT invoked; fast path — PASS
- **D** SELECT → resolution upgraded (`refined_select`, focus set,
  downstream planning/execution completed; single attempt) — PASS
- **E** CLARIFY → clarification turn, no target persisted — PASS
- **F** fabricated target id → system-rejected, focus never set,
  evidence preserves the raw proposal — PASS
- default-construction seam wires the frozen resolver (the D1 assertion)
  — PASS
- end-to-end through the DEFAULT seam with the REAL frozen
  `refine_target` (LLM scripted) — PASS
- `target.refinement` telemetry event recorded — PASS
- runtime identity endpoint contract (fields, fingerprints, flag,
  no secret-shaped keys) — PASS

## 7. Real browser self-validation (§10) — real UI, case U00010, no manual selection

Runtime proof was captured three independent ways per leg: (1) the
fetch-captured `/turn` response `target_resolution` field from inside the
real page, (2) `runtime/identity` fetched through the frontend origin,
(3) structured server log lines. Screenshots:
`qa/screenshots/6x-selfval-{OFF,ON}-*.png`.

| Leg | Scenario | Visible outcome | Runtime evidence |
|---|---|---|---|
| OFF | multi-candidate (关联情况) | clarification listing F5/F6; no focus | `kind=ambiguous, invoked=false, skipped=flag_disabled`; server log: 0 refinement lines; identity flag=false |
| OFF | exact title (Linked Account Network) | resolved F6, completed | `kind=resolved, invoked=false` |
| ON | multi-candidate | clarification listing F5/F6; no focus | **`kind=refined_clarify, invoked=true, decision=CLARIFY`**, reason grounded in goal semantics ("…cannot uniquely distinguish [shared-device link] from [18-account network]"); server log `target.refinement decision=CLARIFY target=None confidence=0.0 candidates=2`; identity flag=true |
| ON | exact title | resolved F6, completed | `kind=resolved, invoked=false` — fast path untouched under ON |

The ON multi-candidate leg proves the full chain required by §10:
candidates existed (2 considered) → goal was available (consumed inside
§1b; reflected in the goal-grounded reason) → refinement invoked →
CLARIFY returned → final behavior (bounded clarification) matched the
decision. Note: the model's actual decision on this request was CLARIFY
(safe). A SELECT through the browser was not forced — SELECT is proven
at integration level (Test D) and the decision itself belongs to the
next independent QA.

## 8. OFF vs ON comparison (§11)

| Scenario | Flag OFF | Flag ON | Evidence of refinement |
|---|---|---|---|
| Multi-candidate (这个账号有哪些关联情况) | clarification {F5,F6}, `kind=ambiguous` | clarification {F5,F6}, `kind=refined_clarify` | **invoked=true, decision=CLARIFY** (API field + telemetry + server log) |
| Exact-title fast path (Linked Account Network) | resolved F6, `kind=resolved` | resolved F6, `kind=resolved` | correctly none (fast path; gate requires AMBIGUOUS) |

OFF ≠ ON on the qualifying scenario (the failure the QA defined for this
recovery) while the fast path is intentionally identical.

## 9. Real RP data used (§6)

System-level browser evidence used live Risk Platform case **U00010**
(findings F1–F8 as served by the real RP at localhost:8000). Live titles
verified 2026-09-24, differing from the SP-M2-1R brief's recollection:
F5 "Shared Device Relationships.", F6 "Linked Account Network.",
F7 "High Trading Frequency.", **F8 "Opposite Trade Ratio." (not
"Relative Trade Ratio")**. No synthetic findings were created or
modified. Controlled fixtures appear ONLY in the wiring tests and are
labeled as fixtures there.

## 10. Remaining known limitations

1. **`/goal` (Agent Loop) path does not run refinement.** The spike
   contract scopes §1b to `run_turn`; the loop path has its own
   resolution/goal timing (R5/U7). Documented as a known limitation, not
   expanded (§9 of the brief).
2. The refinement decision on the qualifying real-RP request was CLARIFY
   in the observed run; whether it *should* resolve, and whether
   SELECT/CLARIFY behavior on E3/E5-class requests is correct, is
   exactly the semantic question deferred to the next independent QA /
   architecture decision (§7 of the brief: no optimization performed).
3. Real-LLM output usability remains fragile (empty/truncated responses
   observed across the QA and regression runs); the frozen bounded
   degradation absorbs it into safe CLARIFYs, and it will add noise to
   stability measurements.
4. `target_resolution` is additive API surface the frontend does not yet
   render — QA consumes it via the API/browser-network capture.

## 11. Test execution summary (§13)

- Refinement contract (`test_target_refinement_v1`), target resolution
  (`test_context_resolution`, `test_target_resolution_stage1_v1`), goal
  resolution (`test_goal_resolution_v1`, `test_goal_resolution_api_v1`):
  **93 PASS**
- `/turn` integration + adjacent regression (`test_api_investigations_v1`,
  `test_investigation_service`, `test_investigation_e2e_v1`,
  `test_refinement_runtime_wiring_v1`, followups, ux_routing): **146 PASS**
- Full backend suite: **1066 passed, 15 failed, 1 skipped, 1 xfailed.**
  Failure classification (verified, not assumed): 13 legacy
  environment-dependent failures (`test_error_handling` 4,
  `test_evidence_integration` 3, `test_provenance_model` 6) reproduce
  **identically with the SP-M2-1R diff stashed** — pre-existing; 1
  real-LLM endpoint flake (`test_agent_loop_llm_v1`) passed on rerun; 2
  real-LLM stability tests (`test_target_refinement_llm_v1` E5 — failed
  identically in the 2026-09-23 independent QA before SP-M2-1R existed —
  and `test_target_resolution_llm_v1` ambiguous-set) are the documented
  LLM-variance family. **No regressions introduced by SP-M2-1R.**

## 12. Semantic refinement issues intentionally deferred (follow-up risks)

- E3-class {F5,F6} candidate sets: the frozen refiner's confidence sits
  at the threshold boundary and its target choice disagreed with the
  recorded spike evidence (QA D2/D4). Unchanged here; belongs to the
  WP-M2-1 contract-revision decision.
- CLARIFY decisions drop model confidence by frozen design; QA should
  not read evidence `confidence=0.0` on CLARIFY as a system fault.
- The `/goal` path refinement bypass (§10.1) may surface as a
  conversation-vs-loop inconsistency (R5) and is queued for U7 work.

## 13. Verdict

The existing refinement experiment is now genuinely wired into the real
product `/turn` path, the flag meaningfully controls it, OFF/ON are
distinguishable at the outcome level AND at the evidence level
(API field + telemetry + server log + runtime identity), and all
system-level evidence above was generated from the real Risk Platform
case U00010 through the real browser. The evidence is sufficient for a
new independent QA to reproduce and judge the system-level contract.

**Status: IMPLEMENTED · VALIDATED (developer level) · READY FOR
INDEPENDENT QA.** Independent QA has NOT run; no H1/acceptance claims are
made here.

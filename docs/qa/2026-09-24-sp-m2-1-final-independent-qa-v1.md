# SP-M2-1 / SP-M2-1R Final Independent QA Report

**Date**: 2026-09-24
**Type**: Independent acceptance QA (read-only; no code/test/prompt/
threshold changes; flag switched only as the brief requires)
**Subject**: the frozen Goal-aware Target Refinement implementation
(`app/target_refinement.py`, sha256-12 `9842ec834bdb`, mtime unchanged
since 2026-09-21) as wired into the real product runtime by SP-M2-1R.
**Question**: can it safely and stably help Target Resolution
disambiguate natural-language requests?
**Final verdict**: **FAIL** (acceptance) — one pass-condition is not met:
repeatable unsafe forced selection on genuinely ambiguous candidate sets.
**H1 Evidence Status**: **B — H1 partially supported.**

---

## 1. QA Environment

- Backend `uvicorn app.main:app` @ 127.0.0.1:8001, restarted per leg
  with explicit `ENABLE_TARGET_GOAL_REFINEMENT=false|true`; process
  start verified after all source mtimes; restored to default (OFF) at
  QA end. Real Risk Platform @ localhost:8000 (up). Real LLM endpoint
  (backend/.env). Real frontend (vite @ localhost:3001) + real headless
  Chrome via the repo CDP harness (read-only reuse). QA drivers in
  `/tmp/qa_final/`; evidence JSONs + 23 screenshots referenced below.
- Isolation design: every scenario run used a NEW investigation on the
  same case (no manual finding selection, no focus carry-over between
  runs), same request text, same backend version, same RP.

## 2. Runtime Identity Evidence

Verified FIRST, per brief §4 — all checks passed (not BLOCKED):

- `GET /api/v2/runtime/identity` (direct and via frontend origin —
  identical): `git_commit 746d4a9`, `refinement_wired: true`,
  fingerprints `target_refinement 9842ec834bdb` /
  `investigation_service 79a5f413058e`, flag value matched the active
  leg at every step (false during OFF leg, true during ON legs).
- Fingerprints recomputed from disk match the running process; the
  frozen refinement file's mtime (Sep 21 15:45) predates all wiring
  changes — the implementation under test is the spike-era frozen one.
- Backend PID 80036 started 12:24:10, after the last source mtime
  (12:23:23) — not a stale process.

## 3. Baseline OFF / Experimental ON

| Check | OFF | ON |
|---|---|---|
| E3 (qualifying multi-candidate) | clarification {F5,F6}; `invoked=false, skipped=flag_disabled` | refinement **invoked** every time Stage-1 retained candidates (5/5); SELECT F6 ×5 |
| AMB (genuine ambiguity) | clarification {F5,F6}; not invoked | invoked 5/5: 1 reasoned CLARIFY, 2 fallback CLARIFYs, **2 SELECTs** |
| E6 (exact-title fast path) | resolved F6, not invoked | resolved F6, not invoked (identical — correct) |

Per brief §16: OFF vs ON difference is proven at the PATH level with
runtime evidence (`target_resolution.refinement.invoked`), not merely
by UI text; server log carries matching structured lines
(`target.refinement decision=... candidates=...`; 16 INFO invocations +
3 module warnings in the ON leg, 0 refinement lines in the OFF log).
One OFF-leg anomaly recorded: E5 under OFF returned UNRESOLVED (Stage-1
LLM variance; Stage-1 is identical in both legs and unrelated to the
flag).

## 4. Real RP Data Verification

Live U00010 re-fetched at QA time: 8 findings; F5 "Shared Device
Relationships.", F6 "Linked Account Network.", F7 "High Trading
Frequency.", **F8 "Opposite Trade Ratio."** — no drift from the
developer-validation record; the brief's "Relative Trade Ratio"
reminder matches the already-corrected record. All system-level
evidence below is live-RP; controlled fixtures were used only in the
automated wiring/contract suites (labeled there).

## 5. Browser Scenario Matrix (flag ON, real UI, no manual selection)

| Req | Candidates seen | Refinement | Decision / target / conf | Final state | Downstream |
|---|---|---|---|---|---|
| E1 关联账户情况 | {F5,F6} | invoked | SELECT F6, 0.75 | focus F6 (agent resolved), completed | case_intake listing (goal→capability gap, §11) |
| E2 团伙账户 | {F5,F6} | invoked | SELECT F6, 0.85 | focus F6 | planning_failure:LLM_OUTPUT_INVALID |
| E4 有关联交易 | {F5,F6} | invoked | SELECT F6, 0.88 | focus F6 | planning_failure:LLM_UNAVAILABLE |
| E6 exact title | — (deterministic) | not invoked | — | focus F6, completed | timeline skill |

(`results_on_matrix.json`; screenshots `7x-qa-ONM-*.png`. E3/E5 matrix
entries are the stability runs' first rows.)

## 6. Repeated-run Stability Matrix (5 fresh runs each, flag ON)

| Scenario | Run-by-run (decision / target / conf) | Outcome class (§15) |
|---|---|---|
| **E3** 共享了设备 | r1–r5: SELECT F6 — 0.80, 0.78, 0.82, 0.86, 0.85 | **Correct & Stable** (target-consistent; rationale consistent: "asks which ACCOUNTS → F6 enumerates the 18 connected accounts; F5 only the link mechanism") |
| **E5** 关联对冲交易 | r1 SELECT F8 0.90; r2 CLARIFY (unusable output); r3, r4 Stage-1 self-resolved F8 (refinement not triggered); r5 CLARIFY (reasoned: F8 is an aggregate, does not identify accounts) | **Correct but Unstable** (no wrong target; decision AND trigger-path vary — Stage-1 sometimes resolves by itself) |
| **AMB** 有哪些关联情况 | r1 CLARIFY (reasoned: both candidates plausible); r2 SELECT F6 0.78; r3 SELECT F6 0.72; r4, r5 CLARIFY (unusable output) | **Incorrect and Unstable** — 2/5 unsafe forced SELECTs on a genuinely ambiguous request |

All 15 runs: `results_on_stability.json`, screenshots `7x-qa-ONS-*.png`.

## 7. Genuine Ambiguity Safety Test

Request: "这个账户有哪些关联情况？" — deliberately broad; candidate
semantics checked FIRST: F5 (link mechanism) and F6 (network
enumeration) are both direct answers to "association situation"; the
request carries no noun that distinguishes them. Correct behavior:
CLARIFY. Observed: CLARIFY 3/5 (one for the right reason, two because
the LLM output was unusable — safe but not for semantic reasons),
**SELECT F6 2/5 at confidence 0.72–0.78** — the refiner accepted a
marginal preference as unique identification, inventing a specific goal
("识别关联账户") the request does not state. Classified:
**Unsafe forced SELECT, repeatable (40% of runs)**, and consistent with
the 2026-09-23 QA's Layer-B finding (force-selection on {F5,F6}) and
the spike's original 2/3 instability.

## 8. Refinement Contract Verification

All frozen-contract checks verified live and by suite:
candidate-ID enforcement (fabricated id rejected — wiring Test F),
system-enforced threshold, single attempt per turn (16 invocations/16
turns, no recursion), no tool selection by refinement, no CoT/prompt
exposure in any evidence channel, malformed output → safe CLARIFY
(exercised 3× live: module warning + `decision=CLARIFY,
reason="refinement output unusable"`, turn degraded to the normal
clarification path, no crash), deterministic exact-title fast path
intact under both flags. Contract suites: **93 PASS**
(refinement contract, context resolution, Stage-1, goal resolution,
goal API). Wiring suite + `/turn` integration: **94 PASS, 1 FLAKY**
(`test_unresolved_reference_stops_before_planning` — default resolver
calls the real LLM; pass/fail/pass on 3 identical reruns; pre-existing,
unrelated to refinement — recorded, not fixed).

## 9. Target Correctness Analysis

Semantic judgments against live candidate descriptions (per brief §8:
neither "≥2 candidates → clarify correct" nor "selected → correct"):

- **E3 → F6: supported.** The request asks *which accounts*; F6's
  description enumerates the connected accounts (via shared devices);
  F5 describes the link mechanism (1 shared device). 5/5 with
  consistent rationale. Caveat recorded: the spike's recorded Layer-B
  evidence claimed E3 → F5 3/3 — target identity for E3 has differed
  across evidence rounds; current product behavior is internally
  consistent and semantically defensible.
- **E5 → F8 (r1): supported.** "对冲交易" maps to the only trade-pattern
  candidate, F8 Opposite Trade Ratio, at 0.90. The r5 reasoned CLARIFY
  ("asks for accounts; F8 is an aggregate") is also defensible — the
  variance is between two safe readings, not a wrong pick.
- **E1/E2/E4 → F6: supported** (requests ask about associated/gang/related
  accounts → F6 is the account enumeration; 0.75–0.88).
- **AMB → CLARIFY: correct** when it happened for semantic reasons;
  **AMB → F6: unsupported** (see §7).
- Confidence alone does NOT discriminate: unsafe SELECTs sit at
  0.72/0.78 while supported SELECTs sit at 0.75–0.90. The 0.7 threshold
  admits both. Threshold-marginal SELECTs (0.70–0.80) were individually
  reviewed; 5 of 9 SELECTs were marginal (E1, E3 r1/r2, AMB r2/r3) —
  2 unsafe (AMB), 3 supported (E1, E3).

## 10. Unsafe Selection Analysis

Tally over the 16 refinement invocations (flag ON, real RP, real UI):

| Class | Count | Cases |
|---|---|---|
| Correct / supported SELECT | 9 | E1, E2, E4, E3×5, E5 r1 |
| Reasoned safe CLARIFY | 2 | E5 r5, AMB r1 |
| Invalid-output safely rejected (fallback CLARIFY) | 3 | E5 r2, AMB r4, AMB r5 |
| **Unsafe forced SELECT** | **2** | **AMB r2 (0.78), AMB r3 (0.72)** |
| Fabricated target prevented (live) | n/a — 0 fabricated proposals occurred live; rejection path proven by wiring Test F | |
| Runtime errors | 0 (all failures were bounded task-level outcomes) | |

Core answer to brief §14 ("模型只是略微偏向某个 candidate，就被系统接受？"):
**Yes — demonstrated.** On the genuinely ambiguous request, slight
preference (0.72–0.78 vs threshold 0.70) became a definitive resolution
twice in five runs. This is the failure class the frozen single-call
design was already flagged as unable to prevent (spike decision,
2026-09-21; QA D4, 2026-09-23) — now proven at product level with live
data.

## 11. Runtime / Task Semantics Observations

Recorded per brief §19 as separate defects; NOT fixed:

1. `clarification_needed` still maps to **task FAILED** (every clarify
   in this QA) — pre-existing.
2. **INSUFFICIENT_DATA → task COMPLETED**: E5 r3/r4 (Stage-1
   self-resolved F8 → cross_account_trade_analysis returned
   "could not reach a reliable conclusion: evidence incomplete" while
   the task showed completed) — the known B12/WP-M2-5 defect.
3. **Goal→capability gap**: after a refined SELECT, the planner mapped
   E1/AMB-r2/r3 to case-level `case_intake` (generic findings listing —
   not an answer about the focused target) and failed
   `LLM_OUTPUT_INVALID` 5/5 on E3 and once on E2. Refinement set the
   target correctly; the downstream capability path is outside this
   spike's scope (WP-M2-2/2.3/2.5 territory). Per brief §18 these
   downstream outcomes were NOT counted as refinement results.
4. LLM endpoint fragility: 3 unusable refinement outputs + 1
   `LLM_UNAVAILABLE` planner failure + Stage-1 E5 UNRESOLVED variance
   (OFF leg) in ~40 real calls — absorbed by bounded designs every time.

## 12. Regression Results

- Contract/resolution/goal suites: **93 PASS**.
- Wiring + `/turn` integration: **94 PASS**, 1 FLAKY (real-LLM resolver
  test; passes on rerun; predates SP-M2-1R; not related to refinement).
- Browser behavior == API/runtime evidence in every recorded run
  (fetch-captured `/turn` payloads vs visible UI vs server log) —
  no test-vs-browser divergence found anywhere.

## 13. Defects Found

- **D-QA3-1 (HIGH, safety)**: repeatable unsafe forced selection on
  genuinely ambiguous candidate sets — 2/5 live (0.72/0.78 vs threshold
  0.70); consistent with all prior evidence rounds. The frozen
  single-call contract cannot distinguish "clearly unique" from
  "slightly preferred".
- **D-QA3-2 (MEDIUM, stability)**: E5 trigger-path instability — Stage-1
  sometimes self-resolves the same request (2/5), bypassing refinement
  entirely; decision flips between SELECT and reasoned CLARIFY on
  identical nominal conditions.
- **D-QA3-3 (MEDIUM, robustness)**: LLM endpoint output fragility
  (3/16 refinement calls unusable) — safely absorbed, but it is a
  non-semantic CLARIFY source that will pollute clarification-rate
  metrics.
- Pre-existing (recorded, not fixed): §11 items 1–3; flaky resolver test.

## 14. Evidence Limitations

- Sample sizes are small (5 runs/scenario; single case U00010; one LLM
  endpoint/model; one QA session). Percentages are indicative, not
  rates.
- GoalRepresentation is not exposed on ambiguity-path turns (runtime
  records `goal=null`; the goal consumed inside §1b is only observable
  through the refinement reason text) — a deliberate exposure bound.
- E5's Stage-1 variance means "refinement stability for E5" is partially
  confounded with Stage-1 stability; both were measured as observed.
- OFF-leg browser coverage was E3/E5/AMB/E6 (not E1/E2/E4); sufficient
  for the §16 proof but the full E1–E6 matrix was recorded under ON.

## 15. Final Verdict

**FAIL** (acceptance).

Pass-condition check (brief §24): conditions 1, 2, 3, 8, 9, 10 **met**
(refinement proven executed; identity verifiable; E3/E5 judgeable; fast
path intact; browser/API/log consistent; no tuning or fixtures used).
Condition 4 met (no systematic mis-selection on resolvable requests).
**Condition 5 partially met** (genuinely ambiguous requests clarify
3/5 — but for the wrong reason in 2 of those), **condition 6 FAILED**
(repeatable unsafe forced selection: 2/5, cross-confirmed by three
independent evidence rounds), **condition 7 partially met** (E3 stable
and correct; E5/AMB unstable). A single failed necessary condition
prevents PASS.

What the QA **did** establish, positively: the recovery (SP-M2-1R) is
sound — the frozen refinement genuinely executes in the real `/turn`
runtime under flag control, with trustworthy identity and evidence
channels; on semantically distinguishable multi-candidate requests
(E3/E1/E2/E4/E5-r1) it selects supported targets consistently, degrades
safely on unusable outputs, and never breaks the deterministic fast
path. The defect is narrow, precisely characterized, and exactly the
one the spike's own decision predicted: safety on equal-plausibility
candidate sets.

## 16. H1 Evidence Status

**B — H1 partially supported.**

H1 claimed: with the user's complete request plus retained candidates,
goal understanding can uniquely resolve the target with high
confidence. The evidence now covers the full chain at product level:
when request semantics do distinguish the candidates, refinement
resolves correctly and stably (E3 — the very pair once considered
ambiguous — 5/5, with coherent per-run rationale; E5-r1 0.90). When
they do not (AMB), the model sometimes reports high confidence anyway
(0.72–0.78) and the frozen system-enforced threshold does not catch it.
"Partially supported" is unchanged from the spike's own conclusion —
but it is now grounded in live, auditable, product-path evidence
instead of module-level probes, and the remaining risk is quantified
(~40% forced-selection on a deliberately ambiguous request) rather
than anecdotal.

## 17. Recommendation for Next PMO Decision

1. Do **not** accept the current frozen refinement as the WP-M2-1
   baseline behavior. The evidence basis for proceeding exists ONLY
   with the already-identified contract revision made a hard
   prerequisite: a system-level mechanism that steers to CLARIFY when
   candidates are equally plausible (the threshold at 0.7 demonstrably
   does not separate the two cases), without weakening the validated
   parts (candidate-ID enforcement, single attempt, safe degradation,
   fast-path isolation — all confirmed working).
2. WP-M2-1 estimation should treat as in-scope: the ambiguity guard,
   the E5 Stage-1/refinement trigger-path inconsistency (D-QA3-2), and
   a decision on exposing goal context on clarification turns.
3. The clarification→FAILED and INSUFFICIENT_DATA→COMPLETED semantics
   (§11.1–2) remain separate confirmed defects for their recorded work
   packages; they were repeatedly re-observed under the new path.
4. LLM-endpoint output fragility (D-QA3-3) should be factored into any
   clarification-rate acceptance metrics (distinguish
   fallback-CLARIFY from semantic-CLARIFY — the `target_resolution`
   evidence field already enables this).

---

**QA integrity statement**: no code, test, prompt, threshold, or
configuration change was made by this QA (flag toggling and service
restarts only, restored to default OFF at the end). All evidence
artifacts: `/tmp/qa_final/results_{off,on_matrix,on_stability}.json`,
`/tmp/qa_final/agent_on.log`, screenshots `qa/screenshots/7x-qa-*.png`.
Statuses used: PASS / FAIL / BLOCKED / NOT TESTED / NOT PROVEN per the
brief; independent QA has executed; no M2 completion claim is made.

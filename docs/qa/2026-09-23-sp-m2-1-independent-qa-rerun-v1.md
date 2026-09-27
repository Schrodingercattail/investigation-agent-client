# Independent QA Re-Run: SP-M2-1 Goal-Aware Target Refinement

**Date**: 2026-09-23
**Type**: Independent QA re-run (validation only — no fixes, no prompt/threshold
changes, no doc changes by this QA)
**Subject**: SP-M2-1 spike as recorded in
`docs/baselines/2026-09-21-sp-m2-1-target-goal-refinement-spike-v1.md`
(parent: `2026-09-21-target-goal-refinement-and-m2-rescope-v1.md` §10)
**Overall verdict**: **FAIL** — the spike does not behave according to its
recorded contract at the system level.
**Spike outcome (§12 scale)**: **C — SPIKE CONTRACT FAILS**
(module-level bounded contract holds; system-level activation, stability,
and the recorded evidence do not).

---

## 0. QA-integrity note (process finding)

The implementation-side session immediately preceding this QA modified
`backend/app/api/investigations.py` DURING the QA (wiring
`refinement_resolver` into the default API service, referencing an
undefined `_make_llm`), despite the QA brief's explicit "do not modify
code". Those mid-QA edits were **reverted before this QA executed** so
that the as-shipped state was validated. The underlying discovery (the
refinement resolver is not wired into the product — Defect D1) is
reported here as a defect, not fixed.

## 1. Test environment

- Backend: `uvicorn app.main:app` @ 127.0.0.1:8001, restarted per leg
  with explicit `ENABLE_TARGET_GOAL_REFINEMENT=false|true`; process start
  times verified **after** all relevant source mtimes (environment
  honesty check). Restored to default (flag OFF, env var absent) after QA.
- Risk Platform: real RP @ localhost:8000, up, case **U00010** (findings
  F1–F8) — real dependency used, not mocked.
- LLM: real Claude endpoint via `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`
  (backend/.env). No LLM mocks in Layer-B legs.
- Frontend: real vite dev server @ localhost:3001; real headless Chrome
  via the repo's CDP QA harness (`qa/browser.py`, read-only reuse).
- Code state: uncommitted M2 working tree as of 2026-09-23 ~15:32, with
  the mid-QA edits reverted (see §0). QA driver scripts live in
  `/tmp/qa_sp_m2_1/` (no repo changes by QA; report file excepted).
- Feature flag defaults verified: `ENABLE_TARGET_GOAL_REFINEMENT=False`,
  `TARGET_REFINEMENT_MIN_CONFIDENCE=0.7` (`app/config.py:50-51`).

## 2. Baseline vs experimental comparison (§3)

12 fresh investigations (6 scenarios × 2 legs), each = intake turn +
scenario turn, so no scenario contaminates another via carried focus.

| Scenario | Flag OFF | Flag ON | Identical? |
|---|---|---|---|
| E1 关联账户情况 | clarification {F5,F6} | clarification {F5,F6} | yes |
| E2 团伙账户 | clarification {F5,F6} | clarification {F5,F6} | yes |
| E3 共享了设备 | clarification {F5,F6} | clarification {F5,F6} | yes |
| E4 有关联交易 | clarification {F5,F6} | clarification {F5,F6} | yes |
| E5 关联对冲交易 | clarification {F5,F6,F8} | clarification {F5,F6,F8} | yes |
| E6 Linked Account Network 具体 | resolved F6 → completed | resolved F6 → completed | yes |

- **OFF preserves baseline: PASS.**
- **ON activates the bounded refinement path: FAIL (Defect D1).** E5 is
  exactly the AMBIGUOUS-with-retained-candidates case §1b exists for;
  under ON it behaves identically to OFF. ON-leg server log contains
  zero refinement activity. Root cause (code-level, read-only
  inspection): `app/api/investigations.py:97-102` is the only production
  constructor of `InvestigationService` and passes no
  `refinement_resolver`; no production code injects one; the gate at
  `app/investigation_service.py:915-918` therefore never opens. The flag
  has **no product effect**; §1b is unreachable dead code in the app.
- **Fast-path non-interference: PASS.** E6 (exact title) resolves via the
  deterministic path in both legs (gate requires AMBIGUOUS; refinement
  structurally cannot fire).
- Not reproduced: the implementation session's reported ON-leg E6
  `planning_failure:LLM_OUTPUT_INVALID` (E6 completed → F6 in this QA's
  ON leg; prior observation was single-run and did not recur).

## 3. Browser scenario matrix (§4) — real UI, case U00010, no manual selection, flag ON

| Scenario | Initial candidate set | GoalRepresentation | Refinement decision | Browser-visible result | Fabricated target? | Contract followed? |
|---|---|---|---|---|---|---|
| E1 | {F5, F6} | **none surfaced** (goal=None) | NOT EXECUTED | bounded clarification card; task failed/clarification_needed | no | yes (safe clarify) |
| E2 | {F5, F6} | none surfaced | NOT EXECUTED | same | no | yes |
| E3 | {F5, F6} | none surfaced | NOT EXECUTED | same | no | yes |
| E4 | {F5, F6} | none surfaced | NOT EXECUTED | same | no | yes |
| E5 | {F5, F6, F8} | none surfaced | NOT EXECUTED | clarification card lists F5/F6/F8; task failed | no | yes |
| E6 | exact-title, no candidates | inspect_network_structure (llm) | NOT EXECUTED (n/a — deterministic path) | completed; plan timeline_investigation 1/1; Context focus F6 "Resolved from conversation"; header "agent resolved" | no | yes |

Screenshots: `qa/screenshots/50-qa-intake-u00010.png`,
`5x-qa-e1.png` … `5x-qa-e6.png` (E5 and E6 visually reviewed).
Browser results equal the API results leg-for-leg — browser and API agree;
there is no test-vs-browser split here: **both** show no refinement.

The spike's recorded browser claims (e.g. E5 → focus F8 via refinement)
**cannot manifest** in this build (D1). Note: browser leg was run under
flag ON only; OFF-leg browser equality is inferred from the API
OFF==ON equality, not separately browsed (declared limitation).

## 4. Stability matrix (§5) — Layer B (the only layer where refinement executes), real LLM, 5 runs/scenario

| Scenario | Candidate set | Result | vs recorded evidence |
|---|---|---|---|
| E3 app-retained | {F5,F6} | **5/5 SELECT F6** conf 0.72–0.85 | recorded: SELECT **F5** 3/3 → **target contradicted** |
| E3 spike condition (full set) | F1–F8 | 5/5 SELECT F6 conf 0.78–0.82 | same contradiction |
| E3 spike-harness exact (CAND-1..8) | CAND-1..8 | 3/3 SELECT CAND-6 = F6 conf 0.78–0.80 | same contradiction — 13/13 F6 today |
| E5 app-retained | {F5,F6,F8} | 4/5 SELECT F8 conf 0.70–0.75; 1/5 CLARIFY (LLM empty-output error) | recorded 3/3 SELECT → stability **not reproduced** |
| E5 spike condition | F1–F8 | 5/5 SELECT F8 conf 0.74–0.82 | directionally consistent |
| Ambiguous, real pair | {F7, F8} | 5/5 SELECT F8 conf 0.85–0.92 | see §5 |
| Ambiguous, spike's synthetic fixture | {F1 "Trading Ratio", F2 "Opposite Trade Ratio"} | 5/5 SELECT F1 conf 0.72–0.88 | recorded 2/3 SELECT / 1/3 CLARIFY → instability **not reproduced** |

No averaging: every run is recorded in `/tmp/qa_sp_m2_1/results_layerb.json`.

## 5. Negative / ambiguous case (§6)

Independent semantic verification, not accepting the recorded 2/3 result:

- **Real pair {F7 High Trading Frequency, F8 Opposite Trade Ratio}** with
  “展开一下交易比例的证据”: F8 is the only ratio-named finding and its
  description is the ratio evidence; selection of F8 at 0.85–0.92 is
  **correct disambiguation** — this pair is semantically resolvable, not
  genuinely ambiguous. No safety failure here.
- **The spike's synthetic pair** ("Trading Ratio" vs "Opposite Trade
  Ratio"): the request noun “交易比例” literally names F1; 5/5 SELECT F1
  is defensible. The recorded 2/3 instability looks like threshold-
  boundary variance, not proof of genuine ambiguity. Note: U00010
  contains **no "Trading Ratio" finding** — the recorded ambiguous-case
  evidence was collected on a synthetic fixture while the spike doc
  labels Layer B "real LLM + real RP" (Defect D3).
- **Where the safety failure actually appears: E3 {F5, F6}.** This pair
  is genuinely contestable — the spike's own browser-acceptance row
  treats F5/F6 as not uniquely distinguishable ("bounded clarification —
  correct"); Stage 1 retains both as candidates; the recorded Layer-B run
  picked F5; today's re-run picks F6 13/13 at 0.72–0.85. Both findings
  answer "which accounts shared devices" (F5 = the relationship, F6 = the
  network derived from it). The refinement nonetheless **force-selects
  F6** every time at threshold-marginal confidence. Per the brief's
  criterion (forced selection where candidates cannot safely be
  distinguished), this is classified a **safety/contract concern
  (Defect D4)** — and it manifests *consistently*, which is arguably
  worse than the intermittent failure the spike documented. The
  counter-reading (F6 as "the accounts" enumeration) is noted for
  fairness; the disagreement between the spike's own layers and runs is
  itself the evidence that the boundary is not anchored.

## 6. Contract checks (§7)

| Check | Result | Evidence |
|---|---|---|
| Only candidate IDs selectable | PASS | `target_refinement.py:140-150`; all Layer-B SELECTs ∈ candidate set |
| Fabricated IDs rejected | PASS | code + passing unit test |
| Threshold system-enforced | PASS | clamp + check at `:134,151-154`; LLM cannot bypass |
| Below-threshold → CLARIFY | PASS | code + passing unit test |
| Malformed output safe | **PASS (exercised live)** | 2 real occurrences today (empty text; unterminated JSON @ char 36) → both CLARIFY, no crash |
| Exactly one attempt | PASS at module level | single call in §1b, no loop; vacuous in app (never fires — D1) |
| No recursion | PASS | code inspection |
| Refinement selects no tool | PASS | `RefinementDecision` has no tool field; §1b only upgrades resolution |
| No new target inventable | PASS | double enforcement: module allowed-ids + service re-check `investigation_service.py:932-934` |
| No unrestricted case data to LLM | PASS | prompt = fenced request + user-safe goal projection + candidate id/title/summary only |
| Deterministic exact-title works | PASS | E6 in both flag legs + browser |

## 7. Boundary check (§8)

Target Resolution (Stage 1) / Goal Understanding (Stage 2.1) / Target
Refinement (§1b) / Capability Selection (planner, outside spike):
responsibilities hold at module level. The chain is **broken at the
wiring seam** (D1): goal and refinement stages never execute in the
product path. Downstream planner/tool correctness (E6) was not counted
as refinement evidence.

## 8. Regression results (§9)

| Suite | Result |
|---|---|
| test_target_refinement_v1 + test_context_resolution + test_goal_resolution_v1 | **PASS** (59) |
| test_api_investigations_v1 + test_investigation_service + test_goal_resolution_api_v1 | **PASS** (74) |
| test_investigation_e2e_v1 + test_target_resolution_stage1_v1 | **PASS** (45) |
| test_target_refinement_llm_v1 (Layer B, real LLM) | **FAIL** — E5-hedging `['CLARIFY','CLARIFY','SELECT']`; 2/3 runs produced unusable LLM output (empty text; truncated JSON); E3 + network-structure PASS |

The failing test is the spike's own checked-in stability harness: the
repo's own suite currently fails on the exact scenario the spike reported
as 3/3 stable.

## 9. Defects found

- **D1 — CRITICAL (integration)**: Goal-aware target refinement is
  unreachable in the shipped product. `refinement_resolver` is never
  injected; `ENABLE_TARGET_GOAL_REFINEMENT=true` has no product effect;
  §1b is dead code. All recorded spike claims of flag-gated activation
  (including "Service integration" in the spike's own §2) fail as
  shipped.
- **D2 — HIGH (evidence/reliability)**: The recorded Layer-B stability
  evidence is not reproducible. E3: recorded SELECT **F5** 3/3; re-run
  SELECT **F6** 13/13 across three conditions. E5: recorded 3/3 SELECT;
  today 4/5 (app condition) and the repo's own pytest fails
  (2/3 CLARIFY, driven by unusable LLM outputs). Confidence values sit
  at 0.70–0.85 — threshold-marginal, so run-to-run variance crosses the
  0.7 boundary.
- **D3 — MEDIUM (evidence fidelity)**: The ambiguous-case stability
  evidence was collected on a synthetic candidate fixture ("Trading
  Ratio" / "Opposite Trade Ratio") that does not exist in real U00010,
  while the spike doc labels the Layer-B table "real LLM + real RP".
- **D4 — MEDIUM (safety/contract)**: Consistent force-selection (F6,
  13/13, 0.72–0.85) on the contestable E3 {F5,F6} pair that the spike's
  own evidence treats as not uniquely distinguishable. The H1 risk
  ("model forces a pick on equally plausible candidates") is confirmed,
  in a persistent rather than intermittent form.
- **D5 — LOW (process)**: Mid-QA code modification by the
  implementation-side session (see §0); reverted before validation.
- **Observation**: no GoalRepresentation is surfaced to the user on
  clarification turns (`goal=None` in TurnResponse for E1–E5) — the goal
  context is absent exactly where the refinement decision would matter.

## 10. Evidence

- `/tmp/qa_sp_m2_1/results_flag_OFF.json`, `results_flag_ON.json` (API
  legs, full TurnResponse records)
- `/tmp/qa_sp_m2_1/results_browser.json` + `qa/screenshots/50-…`,
  `5x-qa-e1…e6.png` (browser matrix)
- `/tmp/qa_sp_m2_1/results_layerb.json` (35 recorded refinement runs)
- `/tmp/qa_agent_off.log`, `/tmp/qa_agent_on.log` (server logs; ON log
  has zero refinement activity)
- pytest outputs quoted in §8; code references in §2/§6/§9.

## 11. Overall verdict

| QA area | Verdict |
|---|---|
| §3 OFF preserves baseline | **PASS** |
| §3 ON activates refinement | **FAIL** (D1) |
| §3 deterministic fast-path isolation | **PASS** |
| §4 browser E1–E5 refinement behavior | **FAIL** (cannot manifest; safe clarify only) |
| §4 browser E6 deterministic resolution | **PASS** |
| §5 stability of recorded results | **FAIL** (D2) |
| §6 ambiguous-case safety (real + synthetic pairs) | **PASS** (resolvable; correct selection) |
| §6 E3 {F5,F6} forced selection | **FAIL** (D4, safety/contract) |
| §7 module contract checks | **PASS** (11/11) |
| §9 deterministic regression suites | **PASS** (178) |
| §9 real-LLM stability suite | **FAIL** (E5) |

**Overall: FAIL.** "Tests passing" (all deterministic suites green) and
feature behavior (refinement never executes; recorded stability evidence
not reproducible) diverge exactly as the project quality gate warns.

## 12. Spike decision

**C — SPIKE CONTRACT FAILS** (with an explicit split of what did and did
not validate):

Validated: the bounded module contract itself (candidate restriction,
fabrication rejection, system-enforced threshold, safe degradation under
real malformed outputs, single-attempt/no-recursion/no-tool boundaries,
deterministic fast path untouched).

Not validated: (1) system-level activation — the spike's own §2
"Service integration" does not exist in the shipped wiring; (2) H1's
stability claims — target identity (E3 F5) and stability (E5 3/3) are
not reproducible, and the checked-in stability test fails today; (3) the
disambiguation-quality claim — on the one genuinely contestable pair
observed (E3 {F5,F6}), the refiner force-selects persistently at
threshold-marginal confidence.

**Has the evidence reduced the High Discovery Risk enough to proceed to
a formal Target Refinement work package? No.** What remains unknown:
whether goal-aware refinement can reliably distinguish "clearly unique"
from "equally plausible" candidate sets at a defensible confidence
boundary (today's confidences sit on the threshold); what the correct
product behavior is for overlapping findings (F5 vs F6) — select,
clarify, or answer from both; whether the LLM endpoint's output
usability (3 unusable outputs in ~36 calls) is stable enough to build
on; and how the refinement integrates with Stage 1's own
ambiguity judgment, which it currently overrides. Per the brief, no fix
is proposed here; these are the questions WP-M2-1 must answer before
implementation.

---

### Reporting appendix (per CLAUDE.md)

- **Changed by this QA**: nothing in code/tests/docs; reverted the
  implementation session's mid-QA edits to `app/api/investigations.py`
  (restoring the as-shipped state); added this report file; QA drivers
  in /tmp.
- **Unit/component**: 59 PASS · **Integration/API**: 119 PASS ·
  **Real browser**: 6/6 scenarios executed, results in §3 (PASS as
  safe-clarify; FAIL as refinement activation) · **Layer-B real-LLM**:
  35 runs + pytest 2 PASS / 1 FAIL.
- **Unverified**: OFF-leg browser parity (inferred from API equality);
  behavior against a different LLM endpoint/model; long-run drift.
- **Pre-existing failures**: environment-dependent legacy backend
  failures per CLAUDE.md baseline (~12 with RP up) — not re-baselined
  here; the targeted suites above were green.
- **Regressions caused by this QA**: none (read-only validation).
- **Acceptance status**: NOT accepted. Statuses used: PASS / FAIL /
  NOT EXECUTED / NOT REPRODUCED as defined in the brief.

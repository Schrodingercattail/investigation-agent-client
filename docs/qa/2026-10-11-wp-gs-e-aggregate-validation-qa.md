# WP-GS-E — Aggregate Validation & Open-Ended Exploratory Testing — QA Report

**Date**: 2026-10-11
**Status**: COMPLETE — fixed acceptance matrix 21/21 with credible
current-worktree evidence (incl. browser re-runs); open exploratory
program executed (9 charters, real experiments); 2 in-scope defects
fixed and regression-verified; 6 findings documented for PMO
decisions/awareness; canonical runtime restored. Not committed / not
pushed.
**Approved basis**: Implementation Plan §10.1 (21-scenario matrix) +
§10.2 (coverage-incomplete semantic mapping), design v1 §14, GS-A..GS-D
QA records.
**Worktree**: HEAD `58881a9` + the accumulated GS-A..GS-E working tree
(preserved intact; no commit/stash/reset at any point).

---

## 1. Baseline verification (per instruction §二)

- Git: HEAD `58881a9`, 169 dirty entries (all preserved), 0 stash —
  snapshotted at start and unchanged in kind at end.
- Runtime preflight: canonical topology verified live
  (frontend :3001 → backend :8001 → RP :8000); backend identity +
  process start times checked before every config swap; flags and seam
  env read from `ps eww` (never assumed).
- Known infra issues re-verified in the CURRENT tree before any
  matrix work (see §4): the settings-flag leak reproduced (2-test
  minimal repro), RP 429 behavior reconfirmed.
- Test files were never assumed executed: every matrix row cites an
  execution batch from this GS-E round or a traceable same-worktree
  artifact from the GS-D round (same session, same tree).

## 2. Main line A — fixed acceptance matrix (§10.1, 21 scenarios)

Scenario tables were read from the plan section itself (not
reconstructed). Evidence key: current-worktree execution batches from
this round; GS-D-round artifacts are same-tree (executed within this
working session) and are cited with their artifact paths.

| # | Scenario | Config | Result | Evidence |
|---|---|---|---|---|
| 1 | 9 withdrawals / 9 distinct | adapter fixture (marked boundary) + seam; real chain | **PASS** | `tests/test_cg_gs_e_matrix_v1.py::test_s1` (batch log `wp-gse-final-batch-matrix-exploratory.log`); browser: `cgsd-J1` (live 7-distinct, same semantics) |
| 2 | 9 withdrawals / 1 shared | fixture + seam | **PASS** | matrix `test_s2` (SATISFIED negative, 全部 9 笔…同一目的地) |
| 3 | mixed repeats (4+3+2) | fixture + seam | **PASS** | matrix `test_s3` (共指向 3 个不同目的地; groups rendered) |
| 4 | partial identity (M/9) | fixture + seam | **PASS** | GS-D API E2E partial 3/9 + browser J3 + 7/7 state cross-check (`wp-cgsd-j3-state-crosscheck-20261011.json`) |
| 5 | ambiguous/unclassified address shapes | fixture (bech32/base58/0X-upper/unclassified/case-variant) | **PASS** | matrix `test_s5`: case-sensitive recorded-identifier comparison (`0XDEADBEEF` ≠ `0xdeadbeef` → 6 groups), descriptive shapes never decide equality, no on-chain claim |
| 6 | identity grouping provenance | any of 1-4 | **PASS** | browser J1/J3: every group lists authoritative record IDs (`cgsd-J1-full-facts-conclusion.png`, `cgsd-J3-partial-identity.png`) |
| 7 | retrieval ok, comparison facts absent | L1 defense + real-path equivalents | **PASS** (with documented mapping) | L1 suppression tests (retrieval NEVER satisfies a faceted output); real-path notes: GS-B attaches the block to every successful withdrawal retrieval (invariant), so the exact letter is defense-in-depth; reachable real shapes: J2 (facts unavailable → PENDING, no overclaim) + authoritative-zero E2E (determinate negative) |
| 8 | proposal omits facet on comparison request | seam retrieval-only contract; detector fires | **PASS** | GS-C API E2E `full_path` (current tree, this round) + browser `journey_cgsc_l3.py J1` re-run today (`wp-cgsc-browser-results-20261011T075223Z.json`) |
| 9 | negative: unqualified whole-request claim is a FAILURE | same, asserted negatively | **PASS** | same tests assert 本次调查目标已完整回答 ABSENT with the coverage disclosure paired |
| 10 | genuine retrieval-only stays supported | retrieval goal text + plain contract | **PASS** | GS-D plain-contract E2E + browser J2 control (today) + GS-A suites |
| 11 | invalid facet member rejected | seam proposal with unknown facet | **PASS** | matrix `test_s11`: `invalid_proposal:invalid_postcondition_facet` → guarded disclosure, no contract, no completion claim |
| 12 | RE_PLAN no-new-information, comparison unresolved | plans ×3 + CONTINUE ×3 | **PASS** | GS-D replan E2E (partial 9/9 stays PARTIALLY_SUPPORTED through convergence; completed terminal + partial outcome) + GS-C replan suite |
| 13 | flag ON, valid contract | all facet runs | **PASS** | GS-D/GS-E facet suites |
| 14 | flag ON, contract missing | seam LLM_EMPTY | **PASS** | GS-A `flag_on_contractless` (this round's batches) |
| 15 | flag OFF canonical safety | flags OFF | **PASS** | GS-A `flag_off_contractless` + GS-C `flag_off_guard_active` (current tree) |
| 16 | isolated legacy-baseline path | DI seam only | **PASS** | GS-A completion-guard suite (legacy wording under injection + negative probe unreachable from canonical paths); batch `wp-gse-final-batch-gs.log` |
| 17 | completion-bearing surfaces consistent | cross-surface | **PASS** | GS-D J3 7/7 cross-check (page = Activity = task = per-output/GoalOutcome) + GS-E C8 (reload + history reopen consistent) + coarse-status boundary tests (CG-3) |
| 18 | single-output regression | existing suites | **PASS** | GS-A case3/case4 + GS-D plain + CG suites (252-batch) |
| 19 | compound / GSP-6 / GSP-7 regression | own canonical configs | **PASS** | CG-4 11/11, CG-5 6/6 (this round), GSP-6/7 suites (252-batch) |
| 20 | partial/unresolved rendering parity | API/Activity/Composer/browser | **PASS** | matrix `test_s20` (per-output ↔ overall ↔ closure ↔ decision pairing) + J3 cross-check + GS-C pairing |
| 21 | detector positives/near-negatives/paraphrases REPORTED | labeled set | **PASS** (coverage reported, not asserted complete) | `docs/evaluations/wp-gse-detector-coverage-report-20261011T070427Z.json`: 19 cases, 2 paraphrase gaps REPORTED (finding F-07); real-model mitigation evidence RM2 |

**Matrix execution batches (all current worktree)**:
`docs/evaluations/wp-gse-final-batch-matrix-exploratory.log` (10/10),
`wp-gse-final-batch-gs.log` (GS families 84 passed),
`wp-gse-final-batch-gsp-cg.log` (GSP+CG 252 passed), CG-4/CG-5 own-
config runs (11/11, 6/6, this round), browser artifacts
`wp-cgsc-browser-results-20261011T075223Z.json`,
`wp-cgsd-browser-results-*` (J1/J2/J3). RP 429 windows were handled
per runbook: affected runs show `dependency_unavailable` shapes, were
re-run after backoff, and were never counted as product results; two
starved-tail batches were re-executed standalone (documented in the
logs above by their green final runs).

## 3. Main line B — open exploratory program

### 3.1 Charters (self-derived from code/architecture analysis; §3.2 of the task)

| Charter | Unknown failure mode sought | Why the architecture permits it | Existing coverage gap | Method | Outcome |
|---|---|---|---|---|---|
| C1 detector vs is_new_address semantics | address-equality answer given to a new/old-address question | detector vocabulary vs GS-B's `is_new_address`-is-never-identity rule | detector tests covered retrieval near-negatives only | pure detector probe over novel phrasings | **No defect**: new-address phrasings NOT detected (F-02 boundary recorded) |
| C2b case-level comparison vs finding-scoped facet machinery | case-level comparison request the facet machinery cannot serve, silently mis-planned | coverage rule and GSP-3 association are type-based; eligibility is scope-based | no test runs a faceted contract at case_level scope | API journey, fixture block | **Finding F-03**: plan rejected → intake fallback → honest PENDING/unanswerable (mechanism probable: finding-scoped skill ineligibility; evidence: adopted plan shape) |
| C3 mid-run dependency outage | outage AFTER findings resolution misreported as domain result | QA-04 guards run start only; executor outage path distinct | no test injected post-resolution adapter failure | adapter raises `RiskPlatformError` mid-loop | **No defect**: integration-error observation never becomes a verdict; output stays pending; bounded terminal; no fabricated data |
| C4 unrelated scalar conflict vs facet verdict | a conflict on an unrelated key discards a well-grounded comparison | GSP-4 conflict norm downgrades ANY satisfied verdict at the single write point | conflicts were tested pre-facets only | pure evaluator experiment | **Finding F-04** (by-design conservatism, pinned as behavior): SATISFIED → UNRESOLVED `conflicting_observations` |
| C5 turn-after-loop semantics | conversational turn contradicts or overclaims vs the loop verdict | turn path is contract-semantic-free by design | turn wording asserted claim-free, but never probed AFTER a faceted run | real HTTP turn on the RM2 investigation | **Finding F-05** (known boundary): turn answers from per-step evidence; no goal claims, no verdict surfacing |
| C6 concurrent goal runs | interleaved state corruption across tasks/threads | two loop threads share store + session read model | all suites are sequential | two simultaneous real-model goal POSTs | **No defect**: distinct tasks, independent honest terminals, no cross-corruption (`wp-gse-c6-concurrent-*.json`) |
| C7 real-model novel-language session | what the REAL model proposes for detector-gap/negation/new-address inputs | layered defense assumes model facet proposal covers detector gaps | CG-5 RM set pre-dates GS-D | 4 no-seam real-model journeys (`wp-gse-realmodel-probes-*.json`) | **RM2: layered defense VALIDATED** (model proposed the facet on a detector-gap phrasing; GS-D answered); RM4 negation respected; RM1/RM3 llm_empty → honest fallbacks (F-06) |
| C8 reload/reopen mid-run and after | reload loses the run or the semantic state | frontend polls; restore rides the session read model | no browser test reloaded mid-LOOP-run | real Chrome reload during a real-model run + history reopen | **No defect**: run completed server-side; page restored the (evidence-only, llm_empty) response; history reopen consistent (`wp-gse-c8-reload-results-*.json`) |
| C9 repeat goal runs / task immutability | a second run mutates the first task's served state | shared store + session keyed by task id | no test re-ran a goal on the same backend twice | two sequential journeys | **No defect**: first task's served response immutable after the second run |

### 3.2 Real experiments beyond the charters (open follow-through)

- The matrix fixtures accidentally produced three MALFORMED blocks
  (duplicated record IDs across groups from a segmented test helper) —
  the GS-D `_destination_block_consistent` gate rejected ALL THREE on
  the real production path (`comparison_facts_absent`). This is
  incidental but strong evidence the consistency gate does its job.
- S21 detector report: 19-case labeled set → 2 paraphrase coverage
  gaps REPORTED (F-07), with real-model mitigation evidence (RM2).

## 4. Test-infrastructure baseline (task §四)

- **Flag leak — REPRODUCED then FIXED (F-01)**. Root cause: three
  harness defects, not one: `test_cg3_contractless_safety_v1.py::
  test_llm_empty_vs_heuristic_single_goal` called `_runner(store)`
  without monkeypatch (bare `settings... = True`, never restored);
  `test_cg_gs_a_api_e2e_v1.py::_journey` accepted `flag=` and never
  applied it (order-dependent silently); `test_cg_gs_c_api_e2e_v1.py::
  _journey` set the flag without restoring. Fixes: monkeypatch
  passed through; flag applied + restored in all GS-A/GS-C journeys.
  Verification: the previously failing order pair now green
  (20 passed); the full GS+matrix+exploratory set green across mixed
  orders; the largest single batch of the day (252) includes
  cg3_safety mid-batch. GS-D's harness already set/restored via
  monkeypatch (no change needed).
- **RP 429 — reconfirmed as an external dependency behavior**, not
  product: logged `Risk Platform rejected request (429)` bursts;
  affected journeys terminal-shape `dependency_unavailable` (honest
  BLOCKED, never fabricated data); all affected runs retried after
  backoff and passed; starved-tail batches re-executed standalone.
  Discipline: spacing ≥60-240s between journey batches; no unbounded
  retries; 429 never counted as product PASS or product FAIL.

## 5. Findings register (triaged per task §六)

| ID | Class | Summary | Evidence | Disposition |
|---|---|---|---|---|
| F-01 | Test infrastructure defect — **FIXED** | global GSP-flag leak via three harness defects (deterministic order dependence) | minimal repro + fix batches (§4) | fixed in this round; regression-verified |
| F-02 | Known design limitation (+ Product decision required for future scope) | is_new_address-class questions ("都提到了新地址吗") are outside the comparison vocabulary AND have no retrieval deliverable; real-model run ends in honest `planning_failure` | C1 probe + RM3 | documented; candidate future WP (product decision) |
| F-03 | Known limitation (capability gap; Product decision required) | a CASE-level comparison request cannot be served by the finding-scoped facet machinery: plan rejection → intake fallback → faceted output honest PENDING, outcome unanswerable; user's question unanswered without fabrication | C2b journey (pinned in `test_cg_gs_e_exploratory_v1.py`) | documented; candidate future WP (case-wide comparison support) |
| F-04 | Known design limitation (by-design conservatism; behavior pinned) | an unrelated scalar conflict (`record_count`) downgrades a unanimous, well-grounded facet verdict to `conflicting_observations` — the approved GSP-4 single-write-point norm applied to the new facet verdicts | C4 experiment + pinning test | documented for PMO awareness; no change |
| F-05 | Known design limitation (approved boundary) | turn responses never surface loop-established verdicts (per-step evidence only; no goal claims — INV-C holds) | C5 real turn probe | documented; cross-surface semantic integration is future scope (design §9.2.1) |
| F-06 | External dependency observation | real-model gateway returned unusable text (`llm_empty`) on 5/7 calls today under load; EVERY fallback was bounded and honest (heuristic single-goal, restricted routing, planning_failure); consistent with the documented thinking-model token-budget lesson | RM1/RM3/C8/C6 artifacts | monitor; model-layer budget tuning outside GS-E scope |
| F-07 | Detector coverage gaps — REPORTED (OQ-6, OQ-4 governance) | 2 paraphrases miss the frozen vocabulary ("都发到了一个地址", "same wallet or not") | S21 report + RM2 | NOT fixed here (vocabulary additions are governed); practical mitigation evidenced: the real model proposed the facet for the gap phrasing (layer 1) |
| F-08 | Product hygiene — **FIXED** | duplicate `"rows_truncated"` key in the projected observation summary (latent confusion; second value silently won) | code inspection | removed; 66- and 252-test batches green |

**Positive validations** (no-defect findings): GS-B consistency gate
rejected three independently malformed blocks on the real path;
layered defense (model proposal over detector gaps) validated; reload
and history-reopen recovery; concurrency isolation; task immutability
under repeat runs; negation respected; turn path never overclaims;
llm_empty fallbacks honest in every observed case.

## 6. Fixes and regression this round

- F-01 (harness isolation): 3 test files; verified by order-pair and
  full-batch runs (20 passed; 84 passed GS batch; 252 passed GSP+CG
  batch).
- F-08 (duplicate key): 1 line; verified by the 66-test affected
  batch + the 252/84/10 batches.
- No product semantic changes were made in GS-E. GS-A..GS-D
  invariants re-verified on the combined system: completion-claim
  guard (flag-independent), coverage pairing (outcome + disclosure),
  facet facts-decide semantics, contract immutability across re-plans,
  cross-surface consistency (matrix rows 8/9/13-20).

## 7. Completion judgment (task §十一)

1. Matrix: 21/21 with credible results — PASS on current-worktree
   evidence; S7 carries an explicit real-path mapping note; S21
   coverage REPORTED with gap list. No BLOCKED items at close.
2. Exploration was genuinely open (9 self-derived charters; real
   API/browser/real-model experiments; open follow-through on
   surprises — e.g. the accidental consistency-gate validations).
3. Charters, evidence chains, and triage are traceable to artifacts.
4. In-scope defects (F-01, F-08) fixed + regression-verified;
   out-of-scope findings carry impact + PMO decisions (F-02/F-03/
   F-05) or monitoring (F-06/F-07).
5. GS-A..GS-D invariants hold on the combined system (matrix rows
   13-20 + positive validations).
6. Real API and real browser results agree (matrix rows 1/4/6/8/9/20;
   GS-D J3 cross-check; C8 restore consistency).
7. Runtime restored: canonical flags OFF, no seam, health 200
   (verified at close).
8. PASS / FAIL / NOT RUN / BLOCKED / known-limitation distinctions
   are explicit throughout this report.
9. Reliability is NOT claimed beyond evidence: F-02/F-03/F-05/F-06/
   F-07 are documented boundaries with owners/decisions pending.

**GS-E is COMPLETE within its authorized scope**; the documented
findings requiring product decisions (F-02, F-03, F-05; F-07
governance) are ready for PMO review and are NOT blockers for the
GS-A..GS-D acceptance statement, which this round confirms on the
combined system.

Not committed / not pushed.

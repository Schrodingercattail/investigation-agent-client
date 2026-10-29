# Phase-1 Remediation Report — U00010 API E2E Failures + F1 False Positive

**Status**: API-E2E remediation COMPLETE (browser E2E explicitly NOT started
in this batch). References
`docs/qa/2026-10-21-u00010-cross-language-acceptance-triage-v1.md` (§6
repair plan R1–R7) and the recorded failing run
`docs/evaluations/u00010-triage-e2e-20261021T122055Z.json`.

**Workspace safety**: no git reset/checkout/clean/stash; nothing staged,
committed, or pushed; HEAD remains `a52cb89`. The original triage report
and the original failing E2E JSON are unmodified.

---

## 0. Environment finding that re-based this phase (mandatory reading)

The 20:20 (12:20Z) recorded E2E ran against a **stale backend**: uvicorn
:8001 had started 15:37, BEFORE every R1–R7 edit (18:53–19:54). Per the
project's environment-honesty rule the run was re-based first:

- Re-run A (21:45, tree as of 21:07, R1–R7 loaded):
  `u00010-triage-e2e-20261021T134535Z.json` — 16/16 "PASS" but several
  hollow (F1 criteria still 「尚未产生可判定的结果」 with zero steps
  completed; F2 still network-substituted; F3 policy unresolved).
- All further diagnosis below is against live runs on restarted
  processes; the stale-run findings (unanchored re-plan step, missing
  preview metadata, unresolved policy projection) that motivated the
  R-fixes were artifacts of the stale process or real gaps, as separated
  per item below.

## 1. Root causes and fixes

### F1 — criteria false positive (runner + two real backend gaps)

**Evidence**: re-run A response: both criteria outputs 「该项尚未产生可判
定的结果」, "Steps completed:" empty; store
`agent_states[TASK-59c5e6f14f48]`: plan = stream plan (2 files) +
coverage-appended `explain_signal` with `arguments={}` (failed
INVALID_ARGUMENT ×2, across a re-plan that reproduced the same shape);
`goal_contract` = 1 FUSED criteria output + 1 file output.

**Root causes (two layers)**:
1. Backend: the deterministic stream plan early-returns the file half and
   drops the criteria half; the GSP-6 coverage supplement re-adds the
   criteria leg as a **bare unanchored finding-level step** (case-level
   subject → no subject pin) at BOTH supplement sites (initial adoption
   + re-plan) → INVALID_ARGUMENT → honest-unresolved → loop exhaustion.
2. Runner: `F1_1/F1_2` tested only the EN marker "No determinable
   result" + the presence of the word 提现 — the zh unresolved face
   「该项尚未产生可判定的结果」 passed.

**Fix** (`backend/app/agent_loop.py`):
- New `_anchored_supplement_steps()`: coverage-appended finding-level
  steps resolve their `finding_id` from the OUTPUT they serve via the
  planner's own `_output_target_finding` (title/domain governance, never
  guessed); an output whose claim names SEVERAL explain domains gets one
  anchored step per matched finding (fused claims answer per finding).
  Both the initial-adoption and re-plan supplement sites now use it.
- New `_repair_artifact_bindings_per_output()` at adoption + per-output
  refactor of `_revise_artifact_source_bindings` (see F4).
- Runner (`qa/u00010_triage_e2e.py`): F1_1/F1_2 now require per-finding
  rule semantics (命中规则/阈值/threshold markers) and FAIL on any
  unresolved marker (`尚未产生可判定的结果`, `当前证据不足以确定该项`,
  `No determinable result`, `无法回答该请求`); F1_4 requires the
  persisted files to carry the correct record families (W-ids / T-ids).

**Final-run evidence**: F1 answer renders real rule semantics — 「命中规
则: High withdrawal frequency / 阈值: withdrawal_frequency_24h > 5」 and
the new-account counterpart; two persisted artifacts
`ART-b3c39b6c5b` (1,097 chars, 7 W-records, 0 T-records) and
`ART-4e7159c7a9` (6,659 chars, 64 T-records, 0 W-records) — per-stream
file scoping intact (§9). F1_1–F1_4 PASS on content.

### F2_1 — citation ask answered by network attribution

**Evidence**: original run: contract had ONLY a network_relations output;
answer led with 「未发现…共享 IP」+ 18-account list. First phase-1 run
(15:06Z): citation leg planned and rendered (「该发现直接适用 1 条政策引
用: AML_Suspicious_Indicators.md … 原文摘录 [3]」) but the contract ALSO
carried the network output → network list still rendered → F2_1 FAIL.

**Root cause (guard ordering)**: `_apply_output_guards` ran BEFORE
`_merge_with_candidate`, so the merge re-admitted the candidate's
network output for a citation ask (the 关联账号 phrase scopes the
citations; it never demands the list).

**Fix** (`backend/app/goal_resolution.py` + `investigation_concepts.py`):
- New bounded matcher `is_policy_citation_original_text_request()`
  (zh 引文原文/政策引文/政策原文…, EN original/full text of citations…).
- New aspect `policy_citation_original_text` → canonical
  `_policy_citation_output` (policy_references; document/section/原文摘
  录 semantics) emitted by `deterministic_contract_proposal` FIRST.
- Guards now re-apply to the FINAL merged set (LLM path) and to the
  heuristic-path proposal; in a citation ask the network-retrieval
  output is dropped (journey contract §7 F2/F3).
- Population→finding anchoring vocabulary: `_EXPLAIN_DOMAINS` gains
  (「关联账号相关」, "linked account") so the population-scoped policy
  step anchors to THIS case's linked-account finding (U00010 → F6
  "Linked Account Network", exactly-one title-element governance).

**Final-run evidence**: F2/F3 answers lead with the citation — document +
section + `[3]` + 原文摘录 (bounded RP excerpt); no network list; no
unresolved marker. F2_1/F3_1 PASS on content. F5 (same GSP render path)
renders the associated citation.

### F4_3 — hedge artifact was the plain transaction table

**Evidence**: original run `TASK-ffcbb78972bb`: plan
[inspect_transactions(F1), set_trades, related_accounts,
generate_artifact(source_step_ids=["S1"])] — all outputs covered, so
coverage appended NOTHING and the inserted-steps-only revision (R2) never
ran. (The 21:45 re-run passed F4_3 only via a different plan shape +
the union rule — LLM variance, not a fix.)

**Fix** (`backend/app/agent_loop.py`): `_repair_artifact_bindings_per_
output()` runs at EVERY initial adoption: an artifact's DECLARED sources
identify the contract output(s) it serves; only those outputs' uncovered
requirements pull in producer steps. `_revise_artifact_source_bindings`
(now per-output) keeps the union only for artifacts with no declared
sources. The withdrawal file of a multi-stream plan can never gain the
transaction stream or rule-explanation steps (U00010 F1 scoping, §9).

**Final-run evidence**: `ART-ae49442700` (9,421 chars, store-verified) =
"Cross-Account Trade Evidence — U00029" with the Candidate
Cross-Account Relationships section, 7 × CATA-001…007, account pairs,
transaction ids, matching-criteria block — never the plain table.
R7 authorization gating untouched (authorized file asks keep the
artifact; investigation-only asks do not create one — pinned by
`test_u00029_hedge_artifact_scope_v1.py`).

### F6_2 — zh scope labels

**Root cause**: stale process (R5 was already in the tree; the 15:37
process predated it). No code change needed in this phase. **Final-run
evidence**: zh explain answer labels the preview (图证据样例，非完整清
单 + 已展示 5 / 18) alongside the cluster count 19. F6_1/F6_2 PASS.

### Runner-wide

All acceptance checks now validate semantic completion (unresolved
markers fail the check; content markers required), per CLAUDE.md
"persisted results and provenance, never execution success". The weak
passes discovered en route (F3_1/F5_1 passing on their own goal echo)
are fixed by the same strictness.

## 2. Executed tests (commands, exit codes, evidence)

- Unit (phase-1 target suites, cwd `backend`):
  `pytest tests/test_u00029_hedge_artifact_scope_v1.py
  tests/test_u00010_compound_triage_v1.py
  tests/test_u00010_citation_contract_v1.py tests/test_goal_resolution_v1.py
  tests/test_gsp6_planning_coverage_v1.py tests/test_case_isolation_v1.py
  tests/test_cross_case_invariant_v1.py
  tests/test_response_language_consistency_v1.py
  tests/test_al213_response_semantics_v1.py -q`
  → **191 passed, exit 0** — `docs/evaluations/u00010-phase1-unit-20261021T145703Z.log`
- Unit (after guard-order fix): same citation/hedge/triage/goal suites →
  **56 passed, 1 failed (pre-existing, see §4), exit 1** —
  `docs/evaluations/u00010-phase1-guardfix-unit-20261021T151448Z.log`
- Real API E2E #1 (phase-1 backend, 15:06Z): **15/16 PASS, F2_1 FAIL,
  exit 1** — `u00010-triage-e2e-20261021T150622Z.json` + run log
  `u00010-phase1-e2e-run1-20261021T150622Z.log` (initial failure
  recorded, then fixed — not hidden).
- Real API E2E #2 FINAL (backend restarted 23:13 local onto the final
  tree; live RP :8000, live glm via z.ai, no synthetic fixtures):
  **16/16 PASS, exit 0** —
  `docs/evaluations/u00010-triage-e2e-20261021T152437Z.json` + full run
  log `u00010-triage-e2e-run-20261021T152437Z.log`. Persisted-content
  checks (artifact ids, record families, CATA candidates, citation
  document/section/原文摘录) were additionally verified directly against
  the store/API as recorded in §1.

## 3. Old vs new API E2E

| Journey | 12:20Z (recorded) | 15:06Z (phase-1 #1) | 15:24Z (FINAL) |
|---|---|---|---|
| F1 criteria (O1/O2) | false-positive PASS | PASS (semantic) | **PASS (semantic)** |
| F1 records/files | PASS (weak) | PASS | **PASS (per-stream persisted)** |
| F2_1 citation | FAIL | FAIL (guard order) | **PASS** |
| F3 policy | weak PASS | PASS | **PASS (citation content)** |
| F4_3 artifact | FAIL | PASS (variance) | **PASS (binding repair)** |
| F5 associated citation | false-positive PASS | PASS | **PASS (content)** |
| F6 scope labels | FAIL | PASS | **PASS** |
| F7 timeline scoped | PASS | PASS | **PASS** |

## 4. Pre-existing failures recorded (NOT introduced by this phase; not
fixed here)

1. `tests/test_agent_loop_al1_v1.py::test_re_plan_produces_genuinely_
   different_plan` — fails on the working tree, passes at HEAD
   `a52cb89` (bisected: independent of this phase's edits). Root: the
   R1–R7 tree makes the scripted step SUCCEED (skills/executor mock
   drift), so the re-plan premise never triggers. Isolation-recommended.
2. `tests/test_goal_resolution_api_v1.py::TestTurnPathGoal::
   test_turn_response_carries_structured_goal` — subject_ref U00010 vs
   expected F6; fails with HEAD `goal_resolution.py` too (R1–R7 tree).
3. `tests/test_gsp1_capability_declarations_v1.py::
   test_declaration_table_pinned` — STEP_TOOL_MAP contains
   `analyze_related_account_set_trades`, absent from the pinned table;
   fails at HEAD too (pinned table never updated when the step landed).
4. The full `pytest tests/` battery was aborted (network-bound legacy
   files hang in this environment); like-for-like baseline applies
   (~43 fail with RP down / ~12 with RP up, project record).

## 5. Status per affected journey (this batch = API E2E only)

- F1 (zh compound dual-criteria + dual-file): **Validated (API E2E,
  semantic + persisted)** — NOT browser-validated in this batch.
- F2 (citation original text): **Validated (API E2E)** — browser NOT
  TESTED.
- F3 (explicit policy citation): **Validated (API E2E)** — browser NOT
  TESTED.
- F4 (hedge + details + file, U00029): **Validated (API E2E, persisted
  artifact content verified)** — browser NOT TESTED.
- F5 (policy chip): **Validated (API E2E)** — browser NOT TESTED.
- F6 (explain scope labels): **Validated (API E2E)** — browser NOT
  TESTED (Radix tabs require real-pointer interaction per project QA
  notes).
- F7 (timeline): **Validated (API E2E)** — browser NOT TESTED.
- §10 project closure stop condition: NOT MET — requires the §7
  regressions green in BOTH languages (this batch ran the zh journeys;
  the EN mirrors are exercised by the unit/vocabulary tests only), two
  consecutive API E2E runs per journey, and one browser E2E per journey.
  This batch provides ONE final green API run per zh journey + the
  15:06Z partial-run evidence; EN-journey API runs and browser E2E are
  the next batch (explicitly out of scope here per the task).

## 6. Residual risks / unknowns

- LLM plan-shape variance remains (the 21:45 run vs 15:24 run took
  different deterministic branches); the adoption-time repairs now make
  every observed shape converge, but a third shape could still surface —
  the strengthened runner checks are the net.
- F2's contract still allows the LLM to emit ADDITIONAL legitimate
  outputs; only guard-violating (network-in-citation-ask) outputs are
  dropped deterministically.
- The al1 re-plan test failure (§4.1) suggests the deterministic
  stream/evidence specialization may over-fire on explain phrasings
  that merely contain a stream noun — flagged for the next triage, not
  changed here (no evidence of a user-visible failure in the live
  journeys).

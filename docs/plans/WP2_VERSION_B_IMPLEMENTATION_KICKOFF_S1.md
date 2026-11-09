# WP-2 Version B — Implementation Kickoff: S1 (Prerequisite Remediation & Evidence)

- **Status (S7 acceptance archive, 2026-10-31): S1 ACCEPTED — CLOSED;
  S2 CONDITIONALLY ACCEPTED (retained — mixed-target / multi-target
  conditions remain open); S3 ACCEPTED — CLOSED; S4 ACCEPTED —
  CLOSED; S5 ACCEPTED — CLOSED (user accepted the enumerated
  residual risks and verification gaps; closure does NOT mean all
  original verification items are PROVEN, that S2 is fully
  accepted, or that unproven capabilities are implemented — see
  §13.9); S6 ACCEPTED — CLOSED (WITH EXPLICIT DEFERRED ITEM: real
  mixed PASS/FAIL Browser E2E deferred to future multi-target
  execution capability — see §13.10); S7 ACCEPTANCE ARCHIVE —
  ACCEPTED — CLOSED (final status summary §13.11). Design=DESIGN
  FROZEN; Case Intake Ruling=OPTION A — ACCEPTED (both surfaces).
  Version B overall verification NOT COMPLETE / overall acceptance
  NOT ACCEPTED. All S1 evidence remains current-behavior (pre-B).
  Evidence: `docs/evaluations/wp2-s5-evidence.json`,
  `docs/evaluations/wp2-s6-browser-e2e.json`,
  `docs/evaluations/wp2-s6-preflight.json`; stage records §13.1–
  §13.11.
- **Date**: 2026-10-31
- **Prepared by**: Claude Code (plan only; no implementation performed)
- **Approval boundary**: user approval of this document authorizes S1
  execution ONLY. It does not authorize S2, does not authorize any
  Version B admission implementation, and expires on any scope change
  that is not re-approved.

## 1. Purpose and Approval Boundary

S1 is the first implementation stage of the approved WP-2 Version B
stage plan (S1→S2→S3→(S4∥S5)→S6→S7, defined in the authoritative
design document's Implementation Preparation entry). S1 delivers ONLY
the prerequisite remediations and evidence items that later stages
depend on:

- D1: the bilingual clarification-text inversion (product defect in the
  CURRENT production clarify path, shared by the future Version B
  clarify terminal);
- D2: a real-API GU-fault-injection E2E that proves the HSG branch can
  be triggered and observed through the real HTTP surface under the
  CURRENT production (A-gate) behavior (currently zero real-API
  evidence; the existing live script has no injection mechanism). This
  is current-behavior evidence only — it does not verify any Version B
  admission behavior (see §4 PRE-02);
- D3: ineffective test assertions (tautology + bilingual tolerance);
- D4: explicit feature-flag pinning for affected tests;
- Case Intake strip-vs-test runtime determination: produce the runtime
  evidence and submit the product ruling to the user. NO behavior
  change is implemented in S1.

Approval of this document authorizes: the file changes declared in §3,
the test executions declared in §5/§9 (including starting/restarting
the canonical runtime stack per the project's mandatory preflight
gates), and the Case Intake determination run. It does NOT authorize
S2, any admission-service implementation, any design change, or any
work on other work packages. S2 requires a separate, explicit user
authorization after S1 acceptance (§13).

## 2. Frozen Design Baseline

- Sole authoritative design document:
  `docs/architecture/R2_GOAL_COVERAGE_AND_DELIVERY_SAFETY_V1.md`,
  Phase 3 section "WP-2: HSG candidate_set 交付准入
  （Version B · DESIGN FROZEN）", finalized 2026-10-31 (including the
  P3 verification-boundary final revision). Baseline identification
  and the stage plan are recorded in that section's Implementation
  Preparation entry.
- S1 modifies NO frozen design ruling. All Version B NORMATIVE rules
  and Final Rulings 1-4 remain untouched. If S1 execution reveals that
  a task cannot be completed without changing frozen design, the Stop
  Condition in §10 applies (stop, report, user adjudication).
- During S1 — and until Version B implementation is accepted — the
  CURRENT production behavior remains: Phase 1 G7''-B blanket HSG
  blocking and the Version A planner unconditional gate. S1 must not
  weaken or bypass either.

## 3. S1 Scope

### In scope (declared file changes)

| # | Path | Change |
|---|---|---|
| F1 | `backend/app/agent_loop.py` | ONE line: `:2820` conditional `if not _zh else` → `if _zh else` (D1). No other edit in this file. |
| F2 | `backend/tests/test_wp2_planning_clarify_v1.py` | D3 assertion repair (`:66-67` tautology removal, meaningful message assertions; `:173-174` language-pinned served-text assertions) + D4 explicit flag pinning. No other edit. |
| F3 | `backend/tests/test_wp2_hsg_gate_api_v1.py` | NEW file: GU-fault-injection real-API E2E (D2), modeled on the accepted Phase 1 pattern `test_delivery_gate_g7_api_v1.py` (TestClient real HTTP face + class-level `ClaudeProvider.generate` injection reproducing the audited connection failure). |
| F4 | `docs/evaluations/wp2-s1-*.json|log` | NEW evidence records (preflight, run logs, determination evidence). |
| F5 | This kickoff document | Status updates only (proposal → in progress → completed/blocked; ruling log). |

### Explicitly out of scope

See §11. Anything not listed in §3 "In scope" is out of scope by
default, including: S2 admission service, /turn changes, P3 digest
implementation, Case Intake behavior changes, CLAUDE.md, any other
work package.

## 4. Prerequisite Inventory

Authoritative wording is quoted from the design document's
Implementation Prerequisites list (①-⑧). Status values: OPEN / IN S1 /
RESOLVED-S1 (only after S1 acceptance) / DEFERRED (later stage).

### PRE-01 (①) — D1 bilingual clarification inversion

- Authoritative wording: "D1 澄清文案语言反转（agent_loop.py:2813-2827；
  B 澄清路径共用，须先修）".
- Current status: OPEN — defect confirmed by source reading (three
  independent verifications, 2026-10-30/31).
- Source location: `backend/app/agent_loop.py:2813` sets
  `_zh = _turn_lang(state) == "zh"`; `:2814-2827` selects the ENGLISH
  text under `if not _zh else` — i.e. a zh turn receives the English
  clarify message and an en turn receives the Chinese message. The
  correct per-turn language contract: response language is decided by
  the CURRENT user request only (zh turn → zh text; en turn → en text;
  undeterminable (None) → zh historical default face, per
  `_turn_lang`, agent_loop.py:4975-4981).
- Known issue: reachable on the canonical submit path whenever
  GU falls back to heuristic_single_goal on an artifact ask.
- In S1: YES (task T1).
- Owner: Claude Code (fix + tests); User: stage acceptance.
- Verification: (a) component test asserting the exact clarify text
  per language; (b) real-API E2E zh and en scenarios via the T4
  harness with NEGATIVE markers (zh scenario must contain 交付/请补充
  markers AND must NOT contain the EN-only marker "delivery-generation
  (artifact) goal"; en scenario the mirror image). Unit tests alone
  are NOT sufficient (DoD §8.4).
- Evidence: diff + component test output + API E2E evidence records.
- Pass criteria: both language scenarios assert the CORRECT language
  with negative markers; zero regressions in the touched test file.
- Blocks: S2 planning consumes the shared clarify path; T4 evidence.

### PRE-02 (②) — D2 HSG branch has zero real-API evidence

- Authoritative wording: "D2 HSG 分支零真实 API 证据（8 次 live 全
  contract_accepted，脚本无注入机制）".
- Current status: OPEN. The existing `qa/wp2_live_planning_clarify.py`
  performs a plain POST + poll with NO fault injection; all 8 recorded
  live runs exercised `contract_accepted`. The Phase 1 precedent
  (`backend/tests/test_delivery_gate_g7_api_v1.py`) demonstrates the
  accepted injection pattern: FastAPI TestClient (real HTTP API face →
  service → GU fallback chain → planner → loop → persist) + class-level
  `ClaudeProvider.generate` injection of the audited connection error.
- In S1: YES (task T4) — the user's S1 DoD requires GU injection to be
  proven via REAL API E2E; unit tests or direct internal-function calls
  are NOT accepted.
- Verification requirements:
  - The test drives the real API request path into the Agent Loop
    (TestClient → `POST /api/v2/investigations/{id}/submit`), not an
    internal runner call.
  - Injection: class-level `ClaudeProvider.generate` fault reproducing
    the audited transport failure, driving the real GU fallback to the
    heuristic decision.
  - HSG-path proof: assert the persisted
    `goal_contract_decision.outcome == "heuristic_single_goal"` — the
    decision record, not a narrative claim.
  - Terminal assertions: `blocked` + `clarification_needed` +
    `goal_outcome=partial`, zero tool executions, zero artifacts,
    clarify framing present, no artifact masquerade. These assertions
    verify the CURRENT production A-gate behavior — they are S1
    current-behavior evidence, NOT Version B behavior verification.
  - GU-success control scenario (`contract_accepted`): completes
    normally with NO clarify framing. Precise evidentiary scope — it
    proves (a) the harness discriminates the two GU outcomes (the
    injected scenario really drives HSG via the recorded decision; the
    uninjected path really succeeds), and (b) under CURRENT production
    the contract_accepted artifact delivery is gate-free. It proves
    NOTHING about Version B admission behavior, the A-gate retirement,
    or bound-target delivery — those are S2/S3 implementation facts
    and S5 verification with their own assertions.
  - S1/S5 separation (explicit): the S1 HSG-scenario assertions assert
    today's A-gate clarify outcome; when Version B lands, S5 REWRITES
    these scenarios to assert B admission outcomes (bound target
    delivers; unbound target clarifies) and the A-gate retirement. The
    S1 harness contributes the injection mechanism, the real-API face,
    and the evidence conventions — NOT its HSG-scenario assertions.
    No S1 evidence may be cited as Version B acceptance evidence.
  - Language scenarios (zh + en) ride on this harness (PRE-01); their
    assertions likewise verify the CURRENT clarify text per language.
  - Mandatory gates: `qa/preflight_runtime.py` verdict FRESH before
    the run, with target-feature declaration and feature-evidence
    verification per the project's Runtime Verification Gate.
- Evidence: preflight record, run log with commands and exit codes,
  JSON evidence file, persisted-state excerpts (decision outcome).
  Evidence labels: "current-behavior (pre-B)" — never Version B
  acceptance evidence.
- Blocks: D1 language acceptance; S2/S5 reuse is limited to the
  injection mechanism, API face, and evidence conventions (see the
  S1/S5 separation above).

### PRE-03 (③) — D3 ineffective assertions

- Authoritative wording: "D3 断言恒真/双语容忍
  （test_wp2_planning_clarify_v1.py:66-67/:173-174）".
- Current status: OPEN — `:66-67` ends with `or res.code == res.code`
  (a tautology that voids the message assertion); `:173-174` accepts
  EITHER language's clarify text
  (`"artifact" in served.lower() or "交付" in served or "澄清" in
  served`), so the D1 inversion cannot be detected by this test.
- In S1: YES (task T2). Repair rule: every assertion must be able to
  fail on the known defect class — message content asserted without
  tautologies; served-text assertions language-pinned (zh markers iff
  the turn is zh; en markers iff en; never a bilingual OR).
- Owner: Claude Code. Verification: mutation check — with the D1 fix
  temporarily reverted (in a scratch worktree run or by reasoning
  recorded in the report), the repaired assertions must FAIL; the
  report must state this falsifiability argument explicitly.
- Blocks: T4 evidence quality; D1 acceptance.

### PRE-04 (④) — D4 tests depend on ambient .env flag

- Authoritative wording: "D4 测试依赖 .env cwd 相对加载
  （config.py:128）——B 测试须显式钉 flag".
- Current status: OPEN — `backend/.env:9 ENABLE_GOAL_SEMANTIC_CONTRACT=
  true` is loaded only when pytest runs with cwd=backend; config
  default is False. `TestLoopIntegration` in
  `test_wp2_planning_clarify_v1.py` sets NO flag and passes only under
  the ambient flag-ON environment; under flag OFF the test fails
  (fetch_case would execute).
- In S1: YES (task T3). Rule: every S1 test explicitly pins the flag
  via `monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
  True/False)` as its scenario requires (matching the established
  g7/u00047 convention). Env-independence proof: run the touched test
  files with `ENABLE_GOAL_SEMANTIC_CONTRACT=false` forced in the
  process environment → all must still pass (monkeypatch overrides
  env); record the exact commands and exit codes.
- Blocks: T2/T4 evidence validity.

### PRE-05 (⑤) — Case Intake strip-vs-test runtime adjudication

- Authoritative wording: "Case Intake flag-ON 下 strip 门
  （agent_loop.py:3786-3796；intake 提案无 delivery ⇒ detail_list 默认，
  goal_resolution.py:201/:560）与 test_case_intake_answer_v1.py:250-268
  断言冲突——运行时未裁决（NOT PROVEN），为独立实施前置证据缺口".
- Current status: OPEN — statically irreconcilable; runtime behavior
  NOT PROVEN. Under flag ON + accepted intake contract (all outputs
  detail_list), the U00004 strip gate removes the intake artifact
  step; the test asserts artifacts are produced. Which one reflects
  production is undetermined.
- In S1: YES — as EVIDENCE + USER RULING only (tasks T5). S1 runs the
  determination (real-API probe + corroborating pytest run), submits
  the evidence with BOTH product options, and obtains the user's
  explicit ruling. NO behavior change and NO test-expectation change
  is implemented in S1. The two options, for the record:
  - Option A (strip wins): intake delivers the conversational overview
    only; `test_artifact_and_answer_coexist` is recalibrated (its
    expectation updated to the ruled behavior) in a LATER approved
    task.
  - Option B (bundle wins): the intake artifact generation is exempted
    from the detail_list strip (a scoped U00004-gate amendment) — a
    design-level change requiring separate approval; NOT part of S1.
  - Neither option may be implemented or marked passed before the user
    rules. A failing intake test during the determination run is
    classified "pre-existing, pending user adjudication" — recorded,
    not fixed, not waived.
- Owner: Claude Code (evidence); **User (ruling — product decision)**.
- Blocks: S2 planning (intake exemption interactions) and any later
  touch of the intake journey.

### PRE-06 (⑥) — /turn anti-bypass API E2E

- Authoritative wording: "⑥ /turn 方案 A 已冻结（见上），其防绕过
  API E2E 未做".
- In S1: NO — DEFERRED to S5. Reason: the /turn anti-bypass E2E
  asserts the SHARED ADMISSION SERVICE behavior, which does not exist
  until S3. Running it in S1 would test a non-existent mechanism.
  Remains in the full prerequisite ledger; NOT declared passed early.

### PRE-07 (⑦) — Implementation authorization

- Authoritative wording: "⑦ 本设计冻结后仍需用户实施授权".
- In S1: the kickoff approval IS the S1-scoped authorization (this
  document). Overall implementation authorization proceeds stage by
  stage per §13. Not a task.

### PRE-08 (⑧) — P3 acceptance matrix

- Authoritative wording: "⑧ P3 验收矩阵…摘要覆盖实际参与渲染的
  records…五类拒绝全为零发布…provenance 精确匹配五字段用例".
- In S1: NO — DEFERRED to S4/S5. Reason: the matrix exercises the
  candidate-render verification pipeline, which is implemented in S2
  and wired in S3. Premature in S1. Remains in the ledger.

## 5. Task Breakdown

### T1 — D1 fix: per-turn clarify language

- Prerequisite: PRE-01.
- Problem/source: `agent_loop.py:2814-2827` inverted conditional
  (see PRE-01).
- Scope: the single conditional at `:2820` (`if not _zh else` →
  `if _zh else`). NOTHING else in the file.
- Not included: composer WP-2 branch fallback hardening (deferred;
  recorded as a noted hardening item, not silently dropped), any other
  message.
- Owner: Claude Code.
- Depends on: none.
- Steps: (1) apply F1; (2) add/adjust component assertions (with T2);
  (3) run the touched test file; (4) zh/en real-API scenarios via T4.
- DoD: component + real-API evidence per PRE-01; diff limited to F1.
- Evidence: diff, test outputs, API E2E records (zh + en, with
  negative markers).
- On failure: Stop Condition §10.8 (classify; no assertion weakening).
- Blocking S1: YES.

### T2 — D3 fix: meaningful, language-pinned assertions

- Prerequisite: PRE-03.
- Scope: F2 assertion repair only (`:66-67`, `:173-174`, and any
  assertion in the file that cannot fail on the D1 defect class).
- Not included: new test scenarios beyond language pinning (T4 adds
  scenarios in its own file).
- Owner: Claude Code.
- Depends on: none (executed with T1 for the falsifiability check).
- Steps: remove the tautology; make the planner-message assertion
  substantive; language-pin the served-text assertions; run the file;
  falsifiability argument (assertions fail with D1 reverted).
- DoD: PRE-03 verification rule satisfied; file passes under pinned
  flags (T3).
- Evidence: diff + falsifiability note + run output.
- Blocking S1: YES.

### T3 — D4 fix: explicit flag pinning + env-independence proof

- Prerequisite: PRE-04.
- Scope: F2 flag pinning (monkeypatch) for `TestLoopIntegration` and
  the language scenarios; applies to all S1 tests.
- Not included: changing `.env`, config defaults, or other test files'
  flag handling beyond S1 scope.
- Owner: Claude Code.
- Depends on: none.
- Steps: pin flags per scenario; run the touched files twice — pinned
  run AND env-forced-false run; record commands + exit codes.
- DoD: both runs pass; env-independence demonstrated.
- Evidence: command transcripts with exit codes.
- Blocking S1: YES.

### T4 — D2: GU-injection real-API E2E (F3)

- Prerequisite: PRE-02. Depends on: T1 (language correctness), T3
  (flag pinning).
- Scope: NEW `backend/tests/test_wp2_hsg_gate_api_v1.py` per the
  Phase 1 injection pattern; scenarios: (i) zh HSG injected — asserts
  the CURRENT A-gate clarify outcome in a Chinese-facing turn, (ii) en
  HSG injected — the mirror with English clarify, (iii) GU-success
  control (contract_accepted) — normal delivery, no clarify framing
  (limited proof scope per PRE-02). Plus the mandatory preflight gate
  record and one recorded execution against the canonical stack
  (backend :8001 → RP :8000), services restarted onto the current
  working tree per CLAUDE.md.
- Not included: Version B admission behavior verification (S1 asserts
  CURRENT production behavior only — the A-gate clarify under HSG;
  B-path and A-gate-retirement verification belongs to S5 with its
  own, different assertions); /turn; any S2 content.
- Owner: Claude Code.
- DoD: HSG-path proof via persisted decision outcome (current-behavior
  evidence); terminal assertions per PRE-02 (current A-gate behavior);
  zh/en language correctness with negative markers; control scenario
  green WITHIN its limited proof scope; preflight verdict FRESH;
  evidence files labeled "current-behavior (pre-B)". The DoD is
  synchronized with the S1/S5 separation: nothing in this task's
  evidence may be cited as Version B acceptance evidence.
- Evidence: preflight record, run log (commands + exit codes), JSON
  evidence, persisted-state excerpt.
- Blocking S1: YES.

### T5 — Case Intake runtime determination + user ruling submission

- Prerequisite: PRE-05. Depends on: none.
- Scope: (i) one real-API determination probe on the canonical stack
  (fresh investigation, canonical intake message, poll terminal, read
  task artifact_ids + agent_states strip diagnostics + decision);
  (ii) corroborating pytest run of `test_case_intake_answer_v1.py`
  under the flag-ON environment; (iii) evidence write-up presenting
  Option A vs Option B (PRE-05) with a recommendation-free statement
  of the product question; (iv) submit to user; (v) record the ruling
  verbatim in this document's Ruling Log (§13.1) once given.
- Not included: any behavior or test-expectation change; any option
  implementation.
- Owner: Claude Code (evidence); **User (ruling)**.
- DoD: determination evidence complete and unambiguous; ruling
  obtained and recorded; zero changes from this task.
- Blocking S1: YES — S1 cannot be reported COMPLETE without the
  recorded ruling (the ruling is the deliverable; its consequences are
  later stages).

### T6 — S1 Completion Report

- Prerequisite: all of T1-T5.
- Scope: assemble §12's report; no code.
- Owner: Claude Code; acceptance: User.
- Blocking S1: YES (S1 is not COMPLETE until the report is delivered;
  acceptance is the user's act, per §13).

## 6. Ownership and Responsibilities

- **Claude Code**: executes approved tasks within the declared file
  scope; writes and runs tests; produces all evidence; reports
  failures truthfully; has NO authority over product semantics, scope
  changes, or stage acceptance.
- **User**: product-semantics rulings (Case Intake Option A/B; any
  ambiguity in §10); scope-change approval; S1 acceptance (§13 gate);
  S2 authorization.
- **ChatGPT**: may review this plan, the Completion Report, and
  evidence for consistency and completeness; may flag risks; must NOT
  make product decisions, must NOT approve stages, must NOT authorize
  scope changes. All rulings and acceptances are reserved to the user.

## 7. Dependencies and Execution Order

T1 ∥ T2 ∥ T3 → T4 (needs T1+T2+T3) → T5 (independent; may run in
parallel with T1-T4) → T6 (needs all). The canonical-stack runs (T4,
T5) follow the project's mandatory preflight sequence; service
restarts use the documented startup command with required env relayed
without writing secrets to disk.

## 8. Definition of Done (S1)

S1 is DONE only when ALL of the following hold — no partial credit,
no softened checks:

1. Every in-S1 defect (D1, D3, D4) has an explicit fix or explicit
   resolution recorded; none silently dropped.
2. D1/D3/D4 each have their own test evidence per §4.
3. GU injection is proven via REAL API E2E (TestClient HTTP face,
   persisted decision outcome) — unit tests or internal calls do not
   satisfy this item.
4. User-visible language output carries real-API evidence for BOTH zh
   and en turns with negative markers; unit tests do not substitute.
5. Case Intake: the user's explicit ruling is obtained and recorded;
   no related behavior change is implemented before the ruling; the
   item is NOT marked passed without the ruling.
6. Every S1 test explicitly pins the feature flag; env-independence
   is demonstrated by a forced-false run.
7. Every test result is traceable to the actual execution environment,
   command, exit code, and assertion output.
8. No test is reported PASS without a recorded run.
9. No historical test evidence is reused as S1 acceptance evidence.
10. No Version B admission implementation is introduced; S2 is not
    started (§13).
11. All changes are confined to the declared file scope (§3); any
    additional file requirement triggers §10.
12. The Completion Report (§12) is delivered with the change list,
    diff summary, test evidence, and open items.

If any task cannot meet its verification bar, it is marked BLOCKED —
the DoD is never lowered to let a task pass.

## 9. Verification Evidence Requirements

- Every real-stack run: `qa/preflight_runtime.py` verdict FRESH with
  target-feature declared BEFORE the run and feature-evidence
  verified at report time (project Runtime Verification Gate); run
  log with working-tree identity (HEAD + `git status --porcelain`
  hash), service identity, restart evidence, probe marker, start time.
- Test runs: exact command, cwd, exit code, pass/fail counts, and for
  D4 the forced-env run.
- API E2E: JSON evidence file under `docs/evaluations/` (project
  convention) + persisted-state excerpt proving the asserted fields
  (decision outcome, goal_outcome, artifact emptiness).
- All evidence files referenced by path in the Completion Report;
  evidence produced before S1 or by earlier work packages is labeled
  historical and carries no acceptance weight.

## 10. Stop Conditions

Any of the following ⇒ immediately stop the affected task (and S1 if
global), do not widen scope, do not weaken assertions, do not start
the next stage; submit the issue, evidence, impact, and the decision
request:

1. A task requires changing frozen Version B design (any NORMATIVE
   rule or Final Ruling 1-4).
2. A task requires touching WP-1, Phase 1/2, or other work packages.
3. The Case Intake product behavior requires a ruling that the user
   has not yet given (stop = do not implement; record and wait).
4. GU injection cannot reliably prove the HSG path was triggered
   (e.g., decision outcome does not record heuristic_single_goal).
5. The real API E2E cannot be executed or its evidence is insufficient
   (preflight BLOCKED/STALE, stack unavailable, non-reproducible).
6. D1-D4 turn out to involve scope beyond the approved S1 boundary
   (e.g., the inversion fix requires composer changes beyond F1).
7. A required change touches unauthorized files or feature flags.
8. A test fails and the fix is outside the approved S1 scope.
9. The workspace shows unauthorized or unexplainable changes.
10. Any ambiguity arises that requires a user product decision.

## 11. Explicit Out-of-Scope

S2 (shared admission service — design or code); /turn anti-bypass
implementation or E2E; P3 digest/provenance implementation or matrix;
mixed-terminal state implementation or Browser E2E; Case Intake
behavior changes or test recalibration (ruling consequences belong to
a later approved task); CLAUDE.md; WP-1 / Phase 1 / Phase 2; any
design-document edits; `.env` or config defaults; any file outside §3.

## 12. S1 Completion Report Requirements

Claude Code must deliver a standalone S1 Completion Report containing:

- A. Per-task status (T1-T6: COMPLETED / BLOCKED, with dates).
- B. Item-by-item DoD acceptance results (§8.1-8.12).
- C. Changed files with exact diffs (and files explicitly NOT changed).
- D. Actual executed test commands (cwd, command, exit code).
- E. API E2E / Browser E2E evidence (paths; browser N/A in S1 —
  recorded as such, never claimed).
- F. Failure and retry records (every failure classified per the
  project defect-handling discipline; no silent retries).
- G. Actual effective feature-flag state per run (pinned value +
  forced-env proof).
- H. Open items (incl. the Case Intake ruling consequences for later
  stages; composer fallback hardening note from T1).
- I. Confirmation whether frozen design or other work packages were
  touched (expected: NO; any deviation = Stop Condition).
- J. Final recommended status (one of §13's states).

## 13. User Acceptance Gate Before S2

1. S1 is COMPLETE only when the Completion Report (§12) is delivered.
2. S1 is ACCEPTED only by the user's explicit review and approval of
   that report. Claude must NOT declare S1 accepted; Claude must NOT
   start S2 upon completing S1; S1 test passage does NOT auto-trigger
   S2; S1 and S2 must never be executed within one continuous task.
3. S2's shared admission service must NOT be pre-built inside S1.
4. User silence or non-response is NOT approval. Without an explicit
   approval, the state remains AWAITING USER ACCEPTANCE and no
   further stage work proceeds.

### 13.1 Ruling Log (filled during S1)

| Date | Item | User ruling (verbatim) | Recorded by |
|---|---|---|---|
| 2026-10-31 | Case Intake strip-vs-test (Option A/B) | **Decision: OPTION A — KEEP EXISTING STRIP BEHAVIOR (ACCEPTED).** Product contract: (1) Case Intake remains the restricted case-level FINDINGS_SUMMARY overview entry; (2) Case Intake does NOT co-deliver an artifact bundle; (3) existing strip behavior unchanged; (4) no bundle restoration; (5) no U00004 gate revision, no Case Intake product redesign; (6) old tests calibrated to this contract (not the product bent to old tests). Runtime basis: `docs/evaluations/wp2-s1-case-intake-determination.json` (S1 determination: decision=contract_accepted, output delivery=detail_list, S2 stripped, zero artifacts). Calibration: `test_artifact_and_answer_coexist` → `test_option_a_intake_overview_without_artifact_bundle` (PASS); the three composer-wording-drift failures remain ISOLATED pre-existing items (not Option A mismatches, untouched). | User (decision); Claude Code (record + calibration) |
| 2026-10-31 | **Resume (S5 Block D) — Decision 1: OPTION B** | An ADMITTED delivery plan keeps its identity and content continuity across Resume. Resume must NOT let the proposer's new plan SILENTLY REPLACE the originally admitted plan. Resume still performs the required admission-state and provenance re-verification. If the delivery plan genuinely must change, that is handled as a NEW plan change and re-admitted IN FULL. No new user-goal-change workflow is designed in this ruling. **Implementation: DONE (2026-10-31, user-authorized minimal fix — see §13.8); real-API E2E PROVEN (admitted-plan continuity + fail-closed both directions, tests/test_wp2_b_resume_option_b_v1.py).** | User (decision); Claude Code (record + implementation) |
| 2026-10-31 | **/turn Case Intake (S5 Block H3) — Decision 2: OPTION A boundary applies to both surfaces** | The Option A product boundary remains in force; /turn and /submit need not share internal implementation or historical behavior; no bundle restoration and no delivery-capability expansion due to path differences; BOTH paths must satisfy the strip / no-artifact-bundle-co-delivery boundary; the U00004 gate is not modified. | User (decision); Claude Code (record + evidence check) |

### 13.2 S1 Execution Log (appended during execution)

- 2026-10-31: user approved the revised kickoff (D2 evidence-logic
  revision included). Status → IN PROGRESS.
- T1 DONE: `backend/app/agent_loop.py:2820` `if not _zh else` →
  `if _zh else` (single line; whole-file uniqueness verified before
  edit).
- T2 DONE: `test_wp2_planning_clarify_v1.py` — tautology removed;
  planner-message assertion made substantive; loop test parametrized
  zh/en with language markers + anti-markers.
- T3 DONE: explicit `ENABLE_GOAL_SEMANTIC_CONTRACT` pinning in the
  loop test; env-independence proven (forced-false run 7/7 PASS).
- Mutation check DONE: with D1 reverted, both language-pinned loop
  tests FAIL (2 failed) → re-applied → 7/7 PASS. Falsifiability is
  empirical, not merely argued.
- T4 DONE: `tests/test_wp2_hsg_gate_api_v1.py` NEW; 3/3 PASS after
  three classified harness-side fixes (scenario tuple ordering; first-
  submission intake substitution; understand_goal bounded-fallback
  representation overriding the goal seam → api.goal_resolver raising
  stub per api.submit's designed exception path). All evidence labeled
  current-behavior (pre-B).
- T5 EVIDENCE DONE / RULING PENDING: canonical-stack probe after
  preflight STALE→restart→FRESH (PID 33752); determination recorded
  (see Ruling Log).
- T6 DONE: S1 Completion Report delivered in the execution turn.
- Regression touch: `tests/test_delivery_gate_g7_v1.py` 12/12 PASS
  (smallest sufficient set for the D1 clarify-text change; no other
  test asserts the clarify phrases).
- No Stop Condition triggered. No frozen design touched. No file
  outside the declared scope modified.

### 13.3 S1 Closeout (2026-10-31, user-directed administrative task)

- User ruling recorded: Case Intake = **OPTION A — ACCEPTED** (see
  Ruling Log; six-point product contract as stated by the user).
- Test contract calibrated under Option A ONLY:
  `tests/test_case_intake_answer_v1.py` — module docstring contract
  line updated; `test_artifact_and_answer_coexist` renamed to
  `test_option_a_intake_overview_without_artifact_bundle` with the
  Option A assertions (overview delivered, zero bundles on task
  container + loop state, `artifact_bundle` tool payload never
  consumed, `delivery_intent_gate` strip record auditable). Product
  implementation: ZERO changes. The three composer-wording-drift
  failures (`:245/:310/:331` pre-calibration line numbers) remain
  ISOLATED pre-existing items — untouched, not Option A mismatches.
- Verification: `python -m pytest tests/test_case_intake_answer_v1.py
  -v` (cwd=backend) → exit 1, **4 passed / 3 failed** (calibration
  flipped the targeted test to PASS; the 3 isolated failures remain by
  design; previously-passing tests unaffected). Log:
  `docs/evaluations/wp2-s1-closeout-intake-calibration.log`.
- S1 is CLOSED. S2 has NOT started and is NOT authorized. All S1
  evidence keeps its current-behavior (pre-B) label.

### 13.4 S3 execution record (2026-10-31, user-authorized)

- **Implemented**: Agent Loop admission wiring (after all post-adoption
  transforms, before any artifact execution, initial+resume paths);
  /turn admission wiring (hsg_only=False — every no-contract artifact
  delivery gated, no bypass); Version A unconditional HSG planner gate
  RETIRED (planner plans normally; goal_contract_decision parameter
  removed); G7'' gate None-B/None-C deferral for ADMITTED (injected)
  HSG steps — check hoisted BEFORE the None-C branch (category-heuristic
  preemption fixed); invalid-target stripping pre-execution with
  ADMISSION_INVALID_TARGET decision records; all-invalid → frozen
  clarify terminal (zero execution); defensive mixed mechanics
  (stop_reason override + composer section — structurally unreachable
  under V1 pairing, NOT PROVEN); expected_* injection reaching the tool
  (executor dispatch whitelist extended — S2 integration gap found and
  fixed).
- **Verified (S3-scoped)**: valid-binding loop delivery within boundary
  (scope=candidate_set:<ref>, content=seeded candidates, dual
  verification echo); no-ref fail-closed (zero execution, reason
  surfaced); /turn anti-bypass (no-ref → no artifact + clarification);
  /turn valid delivery within boundary; Case Intake passthrough;
  None-C/UNKNOWN/criteria non-regression (g7 12/12); contract-path
  candidate export regression zero-change (anaphoric suite).
- **NOT PROVEN / carried**: mixed-target independent outcomes (V1
  structural — carried to S5 DoD per §13.5); same-run unpersisted-set
  E2E; mixed-terminal Browser rendering; S5 full matrices; S6 browser.
- **Recorded pre-existing limitation (not fixed)**: the zh R2a
  records-stream ask fails PLAN-TIME contract validation on the legacy
  /turn planning environment (unrelated to admission; S3 /turn tests
  use the EN deterministic ask).
- Verification: 90 passed / 0 failed final run (3 isolated pre-existing
  composer items deselected per the Option A closeout, recorded NOT
  fixed); evidence `docs/evaluations/wp2-s3-evidence.json`.

### 13.5 S2 acceptance calibration & stage DoD carry-over (2026-10-31,
user-directed)

- S2 implementation status: **COMPLETED** (shared admission service +
  tool verification layer). S2 acceptance: **CONDITIONALLY ACCEPTED**
  — evidence gap retained. Version B overall verification: NOT
  COMPLETE. This calibration does NOT mean mixed-target behavior has
  passed.
- Calibration (recorded in `docs/evaluations/wp2-s2-evidence.json`
  §acceptance_calibration): per-target binding resolution = PASS;
  per-target verdict computation = PASS; **mixed-target independent
  outcomes = NOT PROVEN** (test_per_target_independence does not
  substitute — it exercises resolve_binding directly; V1 pairing rules
  make multi-target plans structurally all-invalid, so a mixed
  PASS/FAIL scenario is not constructible under V1); **multi-target
  step support = NOT SUPPORTED (V1 limitation)**. These four statements
  are distinct and must not be merged into a blanket PASS.
- Carry-over to S3: per-target verdict computation must not be
  reported as mixed-target behavior; the S3 report must state whether
  a real mixed PASS/FAIL execution path is constructible; if out of
  scope → NOT IN SCOPE / NOT PROVEN; V1 multi-target support must not
  be extended to close the gap.
- **Carry-over to S5 E2E DoD** (addition to the stage plan's S5 line;
  clarified 2026-10-31 during S4 — this is a DoD clarification, not an
  execution of S5). The real-API E2E stage MUST cover ALL of the
  following; nothing below may be dropped, weakened, or replaced:

  **A. Mixed-target independent outcomes** — in a frozen-design-legal
  scenario with at least one valid and one invalid target:
  (1) the valid target delivers ONLY within its own candidate_set
  boundary; (2) the invalid target is independently rejected or
  clarified/blocked; (3) one target's binding never authorizes
  another; (4) an invalid target never cancels another valid target's
  authorization; (5) no cross-target / cross-session /
  cross-investigation authorization leakage; (6) delivered content
  matches the corresponding target's candidate_set boundary.
  Retained evidence facts: S2 per-target binding resolution = PASS;
  S2 per-target verdict computation = PASS; mixed-target independent
  outcomes = NOT PROVEN; V1 multi-target step = NOT SUPPORTED.
  If V1 structure cannot construct a legal mixed PASS/FAIL scenario →
  report NOT PROVEN / NOT SUPPORTED and escalate the structural
  limitation to the user; never fabricate a non-frozen scenario,
  relax assertions, or extend V1 multi-target step support.

  **B. HSG scenario matrix** — the frozen HSG scenario matrix (not a
  single happy path): valid binding, each applicable invalid-binding
  class, no-binding, and non-delivery HSG behavior.

  **C. Cross-session / cross-investigation** — binding, candidate_set
  and source_task_id ownership constraints; zero authorization
  inheritance across sessions or investigations.

  **D. Resume** — the resume path neither loses, wrongly inherits, nor
  bypasses admission state and the required verification information
  (re-verification of bound_ready on resume).

  **E. Same-run fail-closed** — a candidate_set generated in the
  CURRENT run but not yet persisted stays fail-closed; persistence
  timing must not be changed.

  **F. /turn anti-bypass** — HSG + valid/invalid binding via /turn,
  PLUS the three requirements added 2026-10-31 (see G below items 1-2
  and 3's /turn portion).

  **G. P3 digest / provenance dual verification** — loop-side expected
  digest; tool-side post-clean actual digest over the
  render-participating records; digest comparison BEFORE Markdown
  rendering and ArtifactV2 construction; post-verification rendering
  uses the same verified records; provenance exactly matching the
  admission-verified binding; digest_mismatch / provenance_mismatch
  rejected BEFORE construction; rejection ⇒ zero artifact publication
  and zero delivered marking. Records digest and provenance binding
  verification are two INDEPENDENT dimensions — neither substitutes
  for the other.

  **H. Added 2026-10-31 (user-directed, three new S5-E2E
  requirements):**

  **H1. Loop other invalid-binding classes + explicit admission
  assertions.** Beyond the existing missing-binding loop API E2E, add
  real Loop API E2E for the other applicable invalid-binding classes
  (per the frozen rules and the S2 service tests), including but not
  limited to: ref not in the current session; duplicate set_ref;
  source_task_id not belonging to the current investigation; empty or
  malformed candidates; non-finite values and any other input the
  frozen rules require to fail closed. Never fabricate scenarios just
  to count tests. Every applicable scenario must verify: (1) the
  request really enters HTTP API → Agent Loop main path; (2) the
  shared deterministic admission service really runs; (3) the invalid
  target does not enter artifact execution; (4) no corresponding
  artifact is produced; (5) nothing is marked delivered; (6) the
  response status and clarification behavior match the frozen design.
  Explicit assertions required: the admission rejection reason; the
  ADMISSION_INVALID_TARGET decision record (or the frozen-design
  equivalent audit record); the rejected target's final state. Zero
  observations / zero artifacts alone must no longer be used to infer
  the admission reason or non-delivery.

  **H2. Non-ARTIFACT /turn passthrough.** Real /turn API E2E proving
  non-ARTIFACT requests do not accidentally enter candidate_set
  artifact admission: cover applicable non-ARTIFACT requests (plain
  questions and other frozen-design-legal non-delivery asks). Verify:
  (1) the request really goes through /turn; (2) hsg_only=False does
  NOT wrongly trigger artifact delivery admission; (3) the original
  response behavior is preserved; (4) no spurious admission rejection
  or clarification appears; (5) the HSG artifact-delivery admission
  rules are unchanged.

  **H3. /turn Case Intake exemption (Option A).** Real /turn API E2E
  proving the Case Intake exemption under the user's Option A ruling:
  Case Intake is the restricted case-level FINDINGS_SUMMARY overview
  entry; it is NOT a candidate_set export; it requires NO
  candidate_set binding; the existing strip behavior stands; no
  artifact bundle co-delivery; no bundle restoration; no U00004 gate
  change; no Case Intake redesign. Verify: (1) the /turn Case Intake
  request really enters the service path; (2) it is recognized as the
  exempt path; (3) no candidate_set binding is demanded; (4) Option A
  strip behavior is preserved; (5) no artifact bundle is produced;
  (6) the overview response is delivered normally; (7) the existing
  Case Intake product contract is unchanged.

  **Handling rules for NOT PROVEN / NOT SUPPORTED**: if V1 cannot
  construct a frozen-design-legal scenario for any item, report NOT
  PROVEN / NOT SUPPORTED honestly, escalate the structural limitation
  to the user, and never fabricate scenarios, relax assertions, or
  extend V1 scope.

### 13.6 S4 execution record (2026-10-31, user-authorized)

- **S5 DoD expansion (Part A)**: the S5 carry-over in §13.5 restructured
  into the explicit S5 E2E DoD blocks A–H — original requirements
  preserved verbatim (A mixed-target six-point + retained evidence
  facts + NOT PROVEN escalation rules; B HSG matrix; C cross-session/
  investigation; D resume; E same-run fail-closed; F /turn
  anti-bypass; G P3 dual verification) plus the three user-directed
  additions (H1 loop other invalid-binding classes + explicit
  admission-reason/decision-record assertions — zero observations/
  artifacts alone no longer sufficient; H2 non-ARTIFACT /turn
  passthrough; H3 /turn Case Intake exemption under Option A).
- **Matrix completion (Part B)**: new
  `tests/test_wp2_b_s4_matrix_v1.py` (9 tests) + one enforcement point
  added to the admission service (`_entry_wellformed`: duplicate /
  missing candidate_id fail closed — `duplicate_candidate_id` /
  `malformed_entry`; the frozen Final Ruling 2 matrix required this
  failure mode, which previously had no enforcement point). Matrix
  mapping with per-item results in
  `docs/evaluations/wp2-s4-evidence.json` §matrix_mapping: FR1 pairing
  completion (2refs×2steps ambiguous both; multi-step no-ref),
  FR2 identity (duplicate/missing candidate_id; shared-leg-tid no
  false reject; null-vs-absent both fail), P3 leg-mutation classes
  (swap → digest_mismatch; add/remove → field_incomplete legacy
  pre-digest rejection), rejected-export zero-delivery-marking
  assertion.
- **Verification**: S4 file 9/9 PASS; full frozen-matrix run (10 files,
  3 isolated Option-A-closeout composer items deselected per the S1
  closeout — recorded NOT fixed): **99 passed / 0 failed**, exit 0.
  NOT PROVEN (carried): mixed-target independent outcomes (S5),
  same-run unpersisted-set E2E (S5), resume E2E (S5), mixed-terminal
  browser rendering (S6). NOT IN SCOPE: S5/S6/S7.
- No Stop Condition triggered. No frozen design change. No product
  behavior change outside the single admission-gate enforcement point
  required by the frozen matrix. S3 remains ACCEPTED — CLOSED.

### 13.10 S6 record (2026-10-31, user-authorized with APPROVED SCOPE
ADJUSTMENT)

- **User-approved S6 scope adjustment (Step 1 record; original S6 DoD
  preserved above — "S6 mixed 组合 mapping 单测 + 真实 Browser E2E" —
  NOT deleted)**:
  - A. Mixed-outcome mapping UNIT tests: PROVEN at the frontend-mapping
    layer only (synthetic constructed inputs; does NOT claim backend
    execution can produce mixed outcomes).
  - B. Real Browser E2E for states V1 CAN produce: representative
    legal-delivery / Option-A-intake / non-intake scenarios via the
    real user journey (frontend UI) + real backend.
  - C. REAL mixed PASS/FAIL Browser E2E: **DEFERRED — future acceptance
    item**, awaiting multi-target execution capability. NOT PASS / NOT
    PROVEN / NOT part of this S6 completion. The deferral is a V1
    structural limitation (user-accepted in the S5 closeout), NOT an
    implementation defect of this stage, and does NOT mean the
    verification is done.
- S2 remains CONDITIONALLY ACCEPTED throughout S6.
- Execution record: see §13.10.1 (mapping unit tests) and §13.10.2
  (browser E2E) below once completed.

#### 13.10.1 Mapping unit tests — DONE (2026-10-31)

- File: `frontend/src/components/investigation/__tests__/
  activityPresentation.s6.test.ts` (5 tests) — real mapping function
  `resolveActivityPresentation`; **5/5 PASS** (vitest, exit 0).
  Proven: the mixed terminal (completed + clarification_needed +
  partial) maps to tone=partial with the clarification hint — never
  success; field-order insensitivity; the adjacent contract combos
  (blocked/bounded/capped) unchanged. Scope honesty: frontend mapping
  ONLY — no claim about backend mixed execution.
- Regression: AgentActivityBlock.test.tsx 21 tests + S6 file 5 = 26/26
  PASS (vitest, exit 0).

#### 13.10.2 Real Browser E2E — DONE for V1-achievable states
(2026-10-31)

- Script: `qa/wp2_s6_browser_e2e.py` (+ `qa/wp2_s6_j2_retry.py` single
  variance re-measurement); evidence
  `docs/evaluations/wp2-s6-browser-e2e.json` (+ j2-retry file);
  preflight `docs/evaluations/wp2-s6-preflight.json` verdict FRESH.
- Journey 1 (Case Intake Option A, /submit UI, real GU + real loop):
  **PROVEN — REAL BROWSER E2E** — overview delivered (findings
  enumerated in the served answer), ZERO bundle (TurnResponse.artifacts
  / task.artifact_ids / session artifacts / GET /tasks/{id}/artifacts
  all empty), artifact_bundle never executed, tone rendered.
- Journey 2 (legal delivery, U00047 compound ask, /submit UI, real GU
  contract path): first run hit live GU proposal variance (no
  file_export output → bounded partial, no artifact — pre-existing
  variance, NOT an S6 regression; zero product changes in S6); single
  authorized re-measurement **PASS — REAL BROWSER E2E**: ART delivered
  (task.artifact_ids + endpoint n=1 + answer ART- reference),
  tone=partial, no gate framing.
- Journey 3 (observational, U00010 records ask): completed /
  tone=success / zero artifacts recorded (live GU variance →
  detail_list → Option A strip → overview-only — additional V1-state
  rendering evidence); the browser card-capture window failed
  (scaffolding) → the rendered-card capture for THIS run = NOT PROVEN
  (backend facts recorded).
- Deferred (NOT PROVEN, user-approved §13.10 item C): REAL mixed
  PASS/FAIL Browser E2E — awaiting multi-target execution capability.

**S6 FINAL (adjusted DoD): A mapping unit tests PROVEN; B
V1-achievable-state Browser E2E PROVEN (intake Option A + legal
delivery); C mixed Browser E2E DEFERRED — NOT PROVEN. S2 remains
CONDITIONALLY ACCEPTED; S1/S3/S4/S5 closures unchanged; S7 NOT
STARTED — NOT AUTHORIZED.**

### 13.7 S5 execution record (2026-10-31, user-authorized)

- **Executed** (real HTTP API E2E; TestClient real face; injected model
  boundary; real admission/loop//turn/executor/tool/persistence;
  flags pinned): Blocks B/C/H1 — HSG admission matrix, 8 invalid
  classes + no-auto-bind + valid-in-boundary (explicit selection
  disambiguates), each with the H1 explicit assertion set
  (ADMISSION_INVALID_TARGET record, reason in served response, zero
  execution/artifact/export-observation, stripped delivery step in the
  persisted plan) — **PROVEN**; Block C cross-investigation —
  **PROVEN**; Block E same-run fail-closed — **PROVEN** (non-first
  submission, scripted analysis+export plan: admission refuses before
  the analysis runs; zero artifacts; session candidate_sets absent);
  Block F /turn anti-bypass — **RE-VERIFIED PROVEN in S5 runs** (no-ref
  refused; bogus-ref refused; valid-ref delivers in boundary); Block G
  dual verification — **PROVEN on the real delivery chain (pass
  direction, dual echo persisted)**; the rejection directions remain
  tool-level PROVEN (S2/S4) — the real admission never emits wrong
  expectations, so rejection-through-the-real-chain is structurally
  reachable only via tool-level tests (documented boundary); Block H2
  non-ARTIFACT /turn passthrough — **PROVEN**; Block H3 /turn intake
  exemption — **PROVEN for the exemption semantics**; SURFACE ASYMMETRY
  RECORDED: the /turn legacy surface retains pre-existing ungated
  intake behavior (a bundle can appear on /turn; pre-dates Version B,
  unchanged by S3) — FLAGGED FOR USER ADJUDICATION; Block A mixed —
  **NOT PROVEN** (V1 structural, no fabricated scenarios).
- **RESOLVED (2026-10-31, user-authorized Resume Option B fix — see
  §13.8)**: Block D resume-VALID-delivery direction — **PROVEN — REAL
  API E2E** (`tests/test_wp2_b_resume_option_b_v1.py`): the resumed run
  continues the SAME admitted plan (plan identity/content continuous,
  plan_revisions=0, no RE_PLAN adoption), the delivery lands within the
  seeded boundary, admission re-verification + provenance
  re-verification execute and persist (dual echo + binding-derived
  provenance), and the cross-investigation fail-closed direction is
  independently PROVEN (ADMISSION_INVALID_TARGET record, zero
  execution/artifacts). The earlier BLOCKED analysis (proposer RE_PLAN
  replacing the admitted plan) was reproduced and root-caused; the
  fail-closed guard behavior itself was design-consistent.
- **Results**: `tests/test_wp2_b_s5_e2e_v1.py` 17 passed / 1 failed
  (the escalated Block D item); full regression 116 passed / 1 failed
  (same item) / 3 deselected (Option A closeout isolated items,
  recorded NOT fixed). Evidence:
  `docs/evaluations/wp2-s5-evidence.json`.
- Version B overall verification remains NOT COMPLETE pending: Block A
  mixed (structural), Block D valid-delivery (escalated), S6 Browser,
  S7 archive.

### 13.7.1 S5 acceptance calibration & decisions recorded (2026-10-31,
user-directed; Part A/B of the S5 closeout authorization)

- **Decision 1 (Resume = OPTION B)** recorded in the Ruling Log (§13.1)
  AND as a frozen design rule in the authoritative document's Phase 3
  Version B section. Meaning: an admitted delivery plan keeps identity
  and content continuity across Resume; the proposer must not silently
  replace it; resume still re-verifies admission state and provenance;
  a genuine plan change is a NEW plan change requiring FULL
  re-admission; no new goal-change workflow is designed. S5 status
  unchanged: resume fail-closed = PROVEN (real API E2E); resume
  valid-delivery = **BLOCKED / NOT PROVEN** (post-resume RE_PLAN
  produces a new plan; G7'' None-C backstop blocks it); the user has
  ruled Option B but the code is NOT fixed — an S5-residual
  implementation item for a future authorized stage, NOT rewritten as
  PASS.
- **Decision 2 (/turn Case Intake = OPTION A on both surfaces)**
  recorded in the Ruling Log (§13.1) and calibrated against the S5 H3
  evidence (see §13.7.2 below).
- **S4 acceptance**: with both calibration questions clarified (P3
  evidence grades; the delivery_admission delta accounting), S4 is
  recorded **ACCEPTED — CLOSED** (historical test results unchanged).
- **S5 acceptance**: Implementation COMPLETED; Verification PARTIALLY
  COMPLETE; Acceptance = **AWAITING USER ACCEPTANCE / BLOCKED BY OPEN
  ITEMS** (the six open items listed in §13.7.2).

#### 13.7.2 Calibrated S5 evidence grades (Blocks A–H) and open items

| Block | Calibrated grade | Basis |
|---|---|---|
| A mixed-target | **NOT PROVEN**; V1 multi-target step **NOT SUPPORTED** | frozen V1 target/step pairing structure — a structural limitation, NOT a test failure |
| B HSG matrix | **PROVEN (real API)** | 8 invalid classes + no-auto-bind + valid-in-boundary, real /submit → loop |
| C cross-session/investigation | **PROVEN (real API)** | cross_investigation + ref_not_in_session rejected, no leakage |
| D resume | fail-closed **PROVEN (real API)**; valid-delivery **BLOCKED / NOT PROVEN** | tied to Decision 1; awaits an authorized minimal implementation + re-verification |
| E same-run fail-closed | referencing an unpersisted set and being refused = **PROVEN (real API)**; the STRONGER same-run create-then-export scenario = **NOT PROVEN** | the two must not be merged into full same-run coverage |
| F /turn anti-bypass | **PROVEN (real API)** | no-ref / bogus-ref refused; valid delivers in boundary |
| G P3 dual verification | pass-direction dual echo persisted = **PROVEN (real API)**; tamper-rejection at tool layer = **PROVEN (S2/S4)**; tamper-rejection via REAL API = **NOT PROVEN**; gate independence = component/tool PROVEN | tool-layer rejections must not be reported as real-API tamper rejections |
| H1 loop invalid classes | **PROVEN (real API)** | explicit reasons + ADMISSION_INVALID_TARGET records |
| H2 non-ARTIFACT /turn | **PROVEN (real API)** | question ask passthrough |
| H3 /turn intake exemption | exemption semantics **PROVEN (real API)**; the /turn-surface bundle co-delivery = **RUNTIME-CONFIRMED CONTRACT VIOLATION (real API forensics) → RESOLVED by the authorized minimal strip fix; post-fix compliance E2E 3/3 PASS** | see below + the forensics record |

- **H3 evidence check (Decision 2, minimal read-only)**: the S5 H3
  test asserts ONLY the exemption (no admission rejection, no
  ADMISSION_INVALID_TARGET); it does NOT assert the artifact outcome
  (the artifact list was printed, not captured). Static
  execution-path analysis: the /turn intake plan deterministically
  contains generate_artifact and NOTHING strips it on /turn (the
  U00004 gate is loop-only) ⇒ an intake bundle co-delivered with the
  overview on the /turn surface is CONFIRMED BY CONSTRUCTION at the
  code level; the RUNTIME confirmation is NOT PROVEN (no captured
  persisted evidence). Classification per the user's rule B: the
  /turn-surface co-delivery **does not match the Option A boundary as
  now stated** → recorded as a **DEFECT (static-analysis confirmed) /
  runtime NOT PROVEN**, escalated for an authorized evidence run and
  fix decision. The exemption semantics themselves stay PROVEN.
  **/turn runtime forensics update (2026-10-31, Phase-1 evidence
  authorization)**: the static DEFECT judgment is now RUNTIME-CONFIRMED
  — real /turn API E2E (`tests/test_wp2_b_s5_turn_intake_forensics_v1
  .py`) proves: the intake plan contains generate_artifact and it
  EXECUTED (no strip point exists on /turn); the bundle
  (findings_summary, case:U00010, "Investigation Evidence Bundle") was
  PUBLISHED and DELIVERED (TurnResponse.artifacts + task.artifact_ids +
  session artifacts + /tasks/{id}/artifacts all consistent); the
  overview and the bundle are CO-DELIVERED. The bundle is a generic
  case-level FINDINGS_SUMMARY (NOT a candidate_set export — that
  boundary is not implicated). Verdict: **CONTRACT VIOLATION — REAL
  API E2E PROVEN** (violates the Decision-2 boundary "both paths must
  satisfy strip / no-artifact-bundle-co-delivery", co-delivery item).
  Escalated for an authorized minimal fix (direction: equivalent
  intake-strip handling on /turn); ZERO code changed in the forensics
  phase. Evidence: `docs/evaluations/wp2-s5-turn-intake-forensics.json`.
- **P3 zero-delivered grade**: ArtifactV2 pre-construction rejection =
  PROVEN; zero artifact publication = PROVEN; zero delivered marking =
  **STRUCTURALLY PROVEN** — no existing test reads and asserts an
  independent persisted delivered-status field; zero
  candidate_set_export observations must NOT be equated to an
  independent delivered-field assertion. S5's real-API rejection
  scenarios assert the persisted absence of export observations and
  artifacts (the strongest current form); an independent delivered
  status field does not exist in the runtime.
- **Open items (retained, none closed)**: (1) resume valid-delivery —
  BLOCKED / NOT PROVEN, requires a Decision-1 minimal implementation
  + re-verification; (2) mixed-target — NOT PROVEN / NOT SUPPORTED
  (V1); (3) same-run create-then-export — NOT PROVEN; (4) Block G
  real-API tamper-rejection — NOT PROVEN; (5) P3 independent
  delivered-field assertion — STRUCTURALLY PROVEN until such a field
  exists; (6) H3 /turn-surface co-delivery — DEFECT (static) / NOT
  PROVEN (runtime), escalated.

### 13.8 Resume Option B fix — execution record (2026-10-31,
user-authorized minimal fix)

- **Root cause (reproduced, evidence in the S5 debug runs)**: on resume
  of an admitted plan whose read-only fetch had ALREADY succeeded, the
  injected artifact step executed with `source_tool_calls` empty (no
  in-run calls exist to cite) → the legacy
  `no_source_tool_call_provenance` rejection → no artifact → criteria
  unmet → the deterministic proposer RE_PLANned into an R2a-shaped
  replacement plan → its uninjected artifact step was then fail-closed
  blocked by G7'' None-C. The fail-closed guard behavior itself was
  design-consistent; the gap was the B-path provenance source.
- **Minimal fix (tool, B path only)**:
  `artifact_bundle._render_candidate_set_export` — when the B admission
  injected `expected_binding`, the export's provenance is the BINDING
  ENVELOPE (`ADMISSION-BINDING:<set_ref>@<source_task_id>/<delivery_
  target_id>`); the legacy no_source_tool_call_provenance rejection
  applies only to the non-B path. NO weakening: Gate 2 (five-field
  provenance exact match) and Gate 1 (digest) still run first; the
  binding envelope is the STRONGER authorization chain (verified
  against the admission record).
- **E2E (real HTTP API, `tests/test_wp2_b_resume_option_b_v1.py`, 2/2
  PASS)**: scenario 1 — resume of the admitted plan (read-only fetch
  done, artifact step injected+pending): the SAME plan continues
  (identity/content continuous, plan_revisions=0, no RE_PLAN adoption,
  no inspect_transactions replacement), the artifact delivers within
  the seeded boundary (scope + TX content), dual verification echoes
  persist, binding-derived provenance on the artifact; scenario 2 —
  the same midstate with a FOREIGN-task-owned set: admission
  re-validation rejects (cross_investigation), the delivery step is
  stripped pre-execution (ADMISSION_INVALID_TARGET record + reason in
  the response), zero artifacts.
- **Regression**: full WP2-B run (12 suites, 3 Option-A-closeout items
  deselected per the S1 closeout): **119 passed / 0 failed**, exit 0.
- Product files touched: `app/domain_tools/artifact_bundle.py`
  (provenance source block only). NOT touched: /turn, Case Intake,
  U00004 gate, P3 gate semantics, planner, composer, S2/S3 ranges.

### 13.9 S5 closeout & S6/S7 dependency review (2026-10-31,
user-directed)

**S5 CLOSEOUT (user accepted the enumerated residual risks; S5 closed
at the current V1 scope)**: 用户接受当前列明的残余风险与验证缺口；S5
按当前 V1 范围关闭。关闭不代表所有原始验证项均已 PROVEN，不代表 S2
完全验收，也不代表未证明能力已经实现。

- Final S5 state: Implementation COMPLETED; Verification PARTIALLY
  COMPLETE; Acceptance = **user-accepted closure with enumerated
  residual risks**. Evidence grades UNCHANGED (see §13.7.2 table).
- PRESERVED residual risks / gaps (grades unchanged): (A) mixed-target
  independent outcomes = NOT PROVEN; V1 multi-target step = NOT
  SUPPORTED; (B) same-run create-then-export = NOT PROVEN (V1
  fail-closed); (C) P3 real-API tamper-rejection = NOT PROVEN
  (tool-layer PROVEN; pass-direction dual echo real-API PROVEN);
  (D) delivered independent status field = absent; zero delivered =
  STRUCTURALLY PROVEN only; (E) three isolated composer-wording-drift
  failures = still failing, untouched; (F) S2 remains CONDITIONALLY
  ACCEPTED (not upgraded).
- PRESERVED completed work: Resume Option B fix — real-API E2E 2/2
  (admitted-plan continuity + cross-investigation fail-closed);
  /turn Case Intake Option A minimal strip fix — real-API E2E 3/3
  (post-fix compliance); pre-fix violation evidence preserved;
  all other S5 real-API PROVEN evidence retained.

**S6/S7 DEPENDENCY REVIEW** (authoritative stage definitions: R2 doc
Implementation Preparation entry — S6 = mixed 组合 mapping 单测 + 真实
Browser E2E; S7 = 验收归档; S6 依赖 S3 终态):

| S5 residual | S6 dep | S7 dep | basis |
|---|---|---|---|
| mixed NOT PROVEN / multi-target NOT SUPPORTED | **HARD BLOCKER (partial)** — S6's Browser E2E DoD item requires a live mixed PASS/FAIL scenario; V1 pairing structurally cannot construct one (per-admission single target ⇒ all-valid or all-invalid; the mixed completed+clarification terminal is unreachable) | S7 archive records whatever S6 delivers — no independent dependency; if S6 is re-scoped, S7 archives the re-scoped result | escalate: the Browser item needs either a V1 extension (product decision) or an S6 scope decision |
| mixed mapping UNIT test | constructible WITHOUT the live scenario (pure function over a synthetic state) — but it is S6 scope work, gated by the same blocker decision | — | gated |
| same-run create-then-export NOT PROVEN | NO DEPENDENCY (S6 = frontend rendering; same-run is admission visibility, S5 E2E scope) | NO DEPENDENCY (S7 archives; the V1 fail-closed restriction is recorded) | — |
| P3 real-API tamper NOT PROVEN | NO DEPENDENCY (S6 renders statuses; the P3 gates are tool-layer, S2/S4 PROVEN) | NO DEPENDENCY (S7 records) | — |
| delivered STRUCTURALLY PROVEN only | SOFT: S6 browser assertions must NOT assert a delivered-status field (none exists); mapping derives from status/stop_reason/outcome only | SOFT: same boundary for S7 archival assertions | boundary noted |
| three composer drift failures | SOFT/NON-BLOCKING: /submit loop-composer items, pre-existing isolated; S6 scenarios must not require them fixed | none | retained |
| S2 CONDITIONALLY ACCEPTED | NO DEPENDENCY for S6 execution (the S2 conditional IS the mixed structural limit — same root as the Browser blocker) | none | S2 status unchanged by S5 closeout (user-directed) |

**S6 VERDICT: 情况 B — HARD BLOCKER exists** (the real Browser E2E DoD
item requires a live mixed scenario that V1 structurally cannot
construct). Per the user's rules: S6 implementation does NOT start;
the blocker and the minimal decision options are escalated:
(i) re-scope S6 to mapping-unit-tests + Browser E2E of the
CONSTRUCTIBLE terminals (blocked/clarify rendering) with the mixed
combo covered at unit level only (browser deferred to V2) — a scope
decision for the user; or (ii) authorize a V1 extension to enable
legal mixed scenarios (product decision, larger blast radius); or
(iii) keep S6 BLOCKED. S7 NOT STARTED — NOT AUTHORIZED.

### 13.12 Goal-overreach fix — execution record (2026-10-31,
user-authorized; outside the S1–S7 stage closures)

- Root cause: the GU LLM's free-text claim_description introduced
  delivery semantics ("Export ... as a downloadable artifact") the
  user never expressed; no deterministic layer validated the coined
  claim wording. Fix: (1) GSP prompt hardening (delivery-semantics
  prohibition); (2) deterministic delivery-overreach sanitizer in
  build_goal_contract (claim/claim_description/claim_description_en,
  word-boundary strip of user-absent delivery words; user-requested
  unsupported modes preserved verbatim + bounded detail note).
- Evidence: unit 8/8 (Cases A-E); REAL-API E2E 1/1 (injected GU
  carrying the real violating wording; persisted contract + served
  answer clean); REAL BROWSER E2E PASS (live stack); full regression
  226/0. Records: docs/evaluations/wp2-goal-overreach-evidence.json,
  wp2-goal-overreach-browser.json. Scope: English delivery vocabulary
  only; Chinese variants unaudited (recorded).

### 13.11 S7 acceptance archive — FINAL STATUS (2026-10-31,
user-authorized)

**WP-2 Version B 当前计划阶段（S1–S6）已全部验收关闭；S7 验收归档本条
即归档完成。以下为本项目最终状态汇总（供后续复盘、展示与继续开发引
用）。归档不等于：S2 已完全验收、所有 deferred / NOT PROVEN 项已实现
或已验证、或整体 Version B 验收已完成。**

#### Final acceptance matrix (S1–S7)

| Stage | Implementation | Verification | Acceptance | Residual / deferred |
|---|---|---|---|---|
| S1 前置修复 | COMPLETED | D1/D3/D4 测试 + D2 真实 API E2E | **ACCEPTED — CLOSED** | 无（D 系列闭合） |
| S2 共享准入服务 | COMPLETED | 组件/服务层矩阵 30+ PROVEN | **CONDITIONALLY ACCEPTED** | mixed-target independent outcomes NOT PROVEN（V1 结构性） |
| S3 loop + /turn 接线 + A 门退役 | COMPLETED | API E2E + 回归 PROVEN | **ACCEPTED — CLOSED** | resume 交互（后由 §13.8 处理） |
| S4 冻结矩阵补全 | COMPLETED | 9/9 + 全矩阵 99/0 | **ACCEPTED — CLOSED** | duplicate-candidate_id 单门位置已记录 |
| S5 真实 API E2E | COMPLETED | PARTIALLY COMPLETE | **ACCEPTED — CLOSED（用户接受列明残余风险）** | 见下方残余清单 |
| S6 混合映射 + Browser（调整后范围） | COMPLETED | A 映射单测 5/5 + B Browser E2E PROVEN；C mixed Browser **DEFERRED / NOT PROVEN** | **ACCEPTED — CLOSED (WITH EXPLICIT DEFERRED ITEM)** | 真实 mixed Browser E2E 待 multi-target 能力 |
| S7 验收归档 | COMPLETED（本条） | 归档一致性检查通过 | **ACCEPTED — CLOSED** | — |

#### 项目级限制与残余（全部保留有效，不得视为已解决）

1. S2 CONDITIONALLY ACCEPTED——mixed-target independent outcomes
   NOT PROVEN；V1 multi-target step NOT SUPPORTED。
2. 真实 mixed PASS/FAIL Browser E2E：**DEFERRED / NOT PROVEN**，等待
   multi-target execution 能力；不计入已完成能力。
3. 同 run 内先创建 candidate_set 再导出：NOT PROVEN（V1 fail-closed
   边界保持）。
4. P3 真实 API 篡改拒绝：NOT PROVEN（工具层 PROVEN；通过方向真实
   API 双 echo PROVEN——不得将工具层升级为真实 API 证据）。
5. delivered 独立状态字段：缺失；零 delivered = STRUCTURALLY PROVEN
   （非独立字段断言）。
6. 三项 composer 措辞漂移隔离失败：仍存在（历史已知项，未修复）。
7. D1/D2/D3/D4 的 S1 证据为 current-behavior (pre-B)，不作为
   Version B 验收证据。
8. /turn 表面 intake bundle 共存 DEFECT：已经授权修复 RESOLVED
   （strip 接入 + 真实 API E2E 3/3）；修复前违规证据保留对照。

#### Deferred / NOT PROVEN 汇总（供未来继续开发）

- Resume Option B：语义已冻结并实施（§13.8），真实 API E2E 2/2
  PROVEN；无未决实现项。
- Mixed multi-target execution + 真实 mixed Browser E2E：待 V2 规划。
- 同 run 集合持久化写者所有权（v2）：待规划。
- /turn intake 表面（若需与 /submit 完全一致）：已由授权 strip 修
  复关闭，无遗留。
  【后续】该阻塞已按用户批准的 S6 范围调整解除（§13.10）：真实 mixed
  Browser E2E 延期（item C），映射单测与 V1 可达状态 Browser E2E
  已执行，S6 ACCEPTED — CLOSED（WITH EXPLICIT DEFERRED ITEM）。

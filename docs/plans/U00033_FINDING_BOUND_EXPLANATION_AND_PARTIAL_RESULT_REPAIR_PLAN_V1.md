# U00033 — Finding-Bound Explanation & Partial-Result Repair Plan V1

- **Status: PLAN CALIBRATED — AWAITING USER APPROVAL** (Rev 2 — Plan
  Calibration 2026-11-01: four calibration blocks incorporated into
  P0-1/P0-2/P0-3/P1; planning only — zero code/test/config changes in
  this or any round)
- **Date**: 2026-11-01
- **Authority**: Option A + Decision 1/2 (frozen); WP-2 Version B stage
  archive (kickoff §13.11); this plan covers a NEW defect found AFTER
  the S1–S7 archive, outside the stage closures — it does NOT reopen
  S1–S6 or alter any archived acceptance state.

---

## A. Problem Statement

Real user request (single submission, live stack):
「关联账号为什么是风险项？相关政策原文发我」

Persisted outcome (TASK-e21f49969484 / INV-c672f7a8a4ca, 2026-11-01):

- Contract ACCEPTED (GC-935ff975), COMPOUND:
  - O1 explanation, evidence=`rule_explanation`,
    scope=`finding_level`, subject_ref=`U00033` — **pending** (never
    satisfied);
  - O2 retrieval, evidence=`policy_references`, subject_ref=`U00033`,
    scope=`case_level` — **satisfied** ✓.
- Executed: `fetch_case` SUCCESS (8 findings incl. **F5 "Shared Device
  Relationships." / F6 "Linked Account Network."**, both graph_signal,
  both carrying §5.1 policy_refs) → `retrieve_policy` **SUCCESS**
  (args `finding_id="F6"` — the §5.1 citation retrieved) →
  `explain_signal` **FAILED ×3** (`INVALID_ARGUMENT: Missing required
  argument(s): finding_id`) → `analyze_related_accounts` SUCCESS (12
  linked accounts, shared device DEV1L71883DLR,
  cluster_device_sharing_4, cluster_risk_score=80.0, role=spoke).
- Terminal: BLOCKED / no_viable_action / goal_outcome=**blocked**;
  **task FAILED**; `state.response` = the LLM-authored BLOCK reason
  ("The explanatory tool failed ... **no evidence or policy text was
  gathered**...").

**Direct root cause**: the planner bound `finding_id="F6"` on the
policy step but omitted it on the explain_signal step; the executor's
only fallback (`context.focused_finding_id`) was None; the step failed
the required-args validation BEFORE any tool call.

**Amplifiers**:
1. RE_PLAN ×2 regenerated the same missing-finding_id explain step
   (the digest carries failures but not a resolvable finding binding);
2. the same missing argument caused THREE blind attempts with no
   clarify/degrade/anchor step between them;
3. the BLOCK decision reason was LLM-authored with a FACTUAL ERROR
   ("no evidence or policy text was gathered" — the policy text WAS
   retrieved and rendered in the per-output answer) and became
   `state.response`, contradicting the actually-composed per-output
   answer (693 chars: O1 honest pending + O2 full §5.1 citation);
4. the terminal derived `blocked`/`unanswerable` instead of the
   partial-satisfaction semantics (表 D row 3).

**User-visible consequence**: an answerable, evidence-backed question
was reported as a total failure, with the successfully retrieved
policy citation presented alongside a summary claiming nothing was
gathered.

**NOT the root cause** (verified): the GU evidence type
(`rule_explanation` is a reasonable classification for a rule-basis
ask); signal_explain's tool capability (it explains graph findings —
F6 carries `signal_explain` in its own capabilities); the policy
retrieval pipeline (it succeeded); the Case Intake Option A ruling
(not implicated — this investigation is not a Case Intake).

## B. Root Cause Mapping

| # | Root cause | File / function / location | Evidence |
|---|---|---|---|
| RC-1 | LLM planner omitted `finding_id` on the explain_signal step while binding it on the policy step | live plan → session tool_calls args (`policy_lookup args {"finding_id": "F6"}` vs explain attempts failing) — planner_v2 LLM plan path (`_user_prompt` + LLM call :1050-1075) | PROVEN (persisted args) |
| RC-2 | The plan prompt gives the LLM **no findings list** (`_user_prompt`: only case_id/focused_finding_id/focused_event_id/skills/request; planner_v2.py:161-181) — the LLM cannot reliably bind finding ids for finding-level steps | planner_v2.py:161-181 | PROVEN (source) |
| RC-3 | The executor's only fallback is `focused_finding_id`, which is **never established** for asks that don't name a finding title (by design — context resolution sets focus on title/explicit match only) | executor_v2.py:280-281; persisted `focused_finding_id=None` | PROVEN |
| RC-4 | The deterministic anchoring machinery (`_anchored_supplement_steps` + `_output_target_finding` + `_EXPLAIN_DOMAINS`) resolves outputs→findings but runs ONLY on coverage-SUPPLEMENT-inserted steps; LLM-planned steps are exempt (`step_covers_output` short-circuits the supplement) | agent_loop.py `_anchored_supplement_steps`; planning_coverage.py:308-354 (`if any(step_covers_output...)`) | PROVEN (source) |
| RC-5 | Domain vocabulary gap: the `linked_account` request elements require 「关联账号相关」/「关联账号网络」/EN "linked account" — the real ask's 「关联账号为何/为什么」 and 「关联账号（population）」 forms match NONE, so even the supplement anchoring could not resolve O1 | investigation_concepts.py:412-449 | PROVEN (source) |
| RC-6 | Blind repetition: the same missing-finding_id step was re-planned twice with no guard | RE_PLAN digest (planner_v2 re-plan path) + loop decision records | PROVEN |
| RC-7 | The served `state.response` = the un-verified LLM BLOCK reason (factual error) instead of the composed per-output answer | agent_loop.py:3532-3538 (BLOCK path passes `enforced.reason` as response); agent_states.response vs turn_responses[tid] (693-char composed answer WITH the citation) | PROVEN |

Amplifier (declared): GU produced O1 with `scope=finding_level` but
`subject_ref=U00033` (case) — an internal scope/subject inconsistency
in the GU output. Not fixed here (GU calibration is out of scope per
§五); the anchoring fix does not depend on it.

## C. Proposed Minimal Changes

### P0-1 — Finding binding for un-anchored finding-level steps
(reuses the EXISTING deterministic anchoring machinery — no new
resolver)

- **Files**: `backend/app/agent_loop.py` (plan-adoption path, after
  `_adopt_plan`, alongside the existing admission/coverage wiring) +
  `backend/app/planner_v2.py` (`_EXPLAIN_DOMAINS` vocabulary only).
- **Change**: at plan adoption, for every finding-level step
  (`_FINDING_LEVEL_STEP_TYPES` — explain_signal included) whose
  arguments lack `finding_id`, resolve the target with the EXISTING
  `_output_target_finding(output, user_words, case_findings)` against
  the current case findings, where `output` is the contract output the
  step serves. Bind ONLY on an unambiguous resolution (exactly-one
  finding); otherwise leave unanchored → the step fails fast into the
  clarify/degrade path (P0-2), never a guessed id.
- **Vocabulary addition (required for this case)**: add the bare
  request elements `("linked_account", "关联账号", "linked account")`
  and `("linked_account", "linked accounts", "linked account")` (EN
  bare form) to `_EXPLAIN_DOMAINS`, so 「关联账号为何…」 resolves to the
  linked-account finding F6 via the governed title-element tier
  (exactly-one check: F6's title carries "Linked Account"; F5's
  carries "Shared Device" → unambiguous). This is the anchoring
  layer's OWN vocabulary (investigation_concepts) — NOT the GU
  evidence enum, NOT a new registry entry.
- **Preserves**: no focus is forced (focused_finding_id stays None —
  the binding is per-step, not a global focus); retrieve_policy /
  artifact steps unaffected; other findings/tools unaffected.
- **Side effects**: bounded — a step may bind a finding the user did
  not name; mitigated by the exactly-one resolution rule + the answer
  labeling (case-observed facts vs general principles).
- **Why not "force focused_finding_id=F6"**: that masks the planner
  binding gap globally and changes every tool's fallback semantics —
  explicitly forbidden.

#### P0-1 Calibration (2026-11-01 — anchoring covers EXISTING steps)

- **The gap this pins**: the deterministic anchoring
  (`_anchored_supplement_steps` + `_output_target_finding`) runs ONLY
  on coverage-SUPPLEMENT-inserted steps. LLM-planned finding-level
  steps short-circuit the supplement via
  `planning_coverage.step_covers_output` (type match ⇒ "covered" ⇒
  skip) and enter the Executor UN-ANCHORED — exactly the failed run's
  explain_signal shape.
- **Calibrated rule**: Finding anchoring applies to BOTH step origins:
  (a) supplement-inserted steps (as already designed) and (b)
  EXISTING adopted steps that lack `finding_id` — both anchored at
  PLAN ADOPTION, i.e. the anchoring/validation completes BEFORE any
  step enters the Executor. No finding-level step reaches the
  Executor without its binding resolved (bound, or fail-closed to
  clarify/drop per P0-2).
- **Identity consistency (retrieve_policy ↔ explain_signal serving
  the same explanation task)**: both steps resolve their finding via
  the SAME `_output_target_finding` resolution (single resolver, same
  case-findings input) and the resolved identity is pinned at plan
  level — consistency is GUARANTEED by the deterministic resolver,
  never delegated to the LLM. If two steps of the same explanation
  task resolve to DIFFERENT findings → plan-level inconsistency →
  fail-closed to clarify (never two findings silently).
- **Supplement vs existing-step distinction (kept explicit)**:
  supplement steps are INSERTED with the anchor; existing adopted
  steps are ANNOTATED in place. Same resolver, same exactly-one
  discipline, two application sites — the minimal change covers both.
- **No hardcoding**: F6/F5/「关联账号」 never appear in code; resolution
  flows through the governed domain/title/exactly-one tiers only.
- **No global focus**: `focused_finding_id` stays None; the binding is
  per-step argument injection at plan level.

### P0-2 — Missing-arg guard + RE_PLAN gap carry
- **Files**: `backend/app/executor_v2.py` (validate-then-refuse
  already exists — add NO new rejection) and
  `backend/app/agent_loop.py` (RE_PLAN digest + one-retry discipline).
- **Change**:
  1. A finding-level step that fails
     `INVALID_ARGUMENT(missing finding_id)` is NOT eligible for
     identical re-planning: the RE_PLAN digest gains an explicit
     `unresolved_parameter_gaps` line (step type + missing arg +
     the resolved-or-absent target), and the re-plan prompt instructs
     the model to bind the parameter or omit the step.
  2. If the re-plan repeats the same gap, the step is dropped
     (fail-closed) and the bounded partial answer composes — the
     "同一缺参步骤不得重复生成" rule.
- **Preserves**: RE_PLAN semantics otherwise unchanged; no new tools.
- **Side effects**: a genuinely unfixable explanation degrades to the
  bounded general explanation + policy quote — exactly the Option A /
  Decision-1-compliant degradation.

#### P0-2 Calibration (2026-11-01 — gap carry into RE_PLAN & suppression
precision)

- **How the gap enters the RE_PLAN digest (PROVEN data path)**: the
  failed explain_signal attempt ALREADY produces an `execution_error`
  observation (`INVALID_ARGUMENT: Missing required argument(s):
  finding_id`) and a decision record. The RE_PLAN digest builder gains
  ONE deterministic line per unresolved gap, sourced ONLY from those
  failed-step records (never LLM-invented):
  `unresolved_parameter_gaps: [{"step_type": "explain_signal",
  "serving_outputs": ["O1"], "missing": ["finding_id"],
  "resolved_finding": null, "failed_attempts": N}]`.
- **Digest records per gap**: original step type; the contract
  output(s) it serves; the missing parameter; the resolved Finding
  identity (or null); the failed-attempt count.
- **How the next planner uses it**: the re-plan prompt presents the
  gap as a CONSTRAINT — bind the parameter or omit/degrade the step.
  A re-plan that regenerates the same gap is dropped at adoption
  (fail-closed; the "同一缺参步骤不得重复生成" rule).
- **Gap classification (fail-closed throughout)**:
  (a) resolvable from existing context (the finding exists in THIS
  case's findings — bind deterministically);
  (b) genuinely ambiguous (multiple candidate findings) → user
  clarification path;
  (c) unsolvable (no such finding anywhere) → stop retrying; degrade
  to the bounded answer.
- **Suppression precision**: only steps matching (same step type +
  same missing parameter + same unresolved reason) are suppressed;
  every OTHER step (legal acquisition steps, the policy step, other
  findings' steps) is untouched. Material context changes (new
  findings persisted, new focus) legitimately reopen planning — the
  suppression keys on the GAP, not on the topic.

### P0-3 — Partial-result composition is canonical at the blocked
terminal
- **Files**: `backend/app/agent_loop.py` (BLOCK terminal response
  selection).
- **Change**: at the BLOCK terminal, the user-visible response is the
  COMPOSED per-output answer (the composer already renders O2's
  policy citation and O1's honest pending — observed in the 693-char
  turn response). The LLM-authored decision reason is retained in the
  decision/activity records (audit) but is no longer the served
  answer; any factual claim in it ("nothing was gathered") is
  replaced by the composer's observation-grounded summary.
- **Preserves**: the decision trail (audit) unchanged; the composed
  answer already existed in this run — this change only makes it the
  canonical served text.
- **Side effects**: the task container still reads FAILED for blocked
  family terminals (unchanged container semantics); the UI continues
  to render the composed answer.

#### P0-3 Calibration (2026-11-01 — the composed answer must CONSUME
the successful analysis; four evidence layers kept distinct)

- **Run-proven fact (PROVEN)**: in the failed run the
  `analyze_related_accounts` observation was WRITTEN to
  agent_states.observations, but with `for_outputs: []` — associated
  to NO output — so the per-output composer never consumed it, and
  the cluster facts (12 linked accounts, shared device
  DEV1L71883DLR, cluster_device_sharing_4, cluster_risk_score=80.0)
  NEVER appeared in the served answer. Merely serving the previous
  compose_loop_response does NOT solve this.
- **Minimal composer capability (in-scope per the user's Calibration
  3.3)**: for an explanation-kind output (claim_kind=explanation)
  whose serving analysis observation exists but carries NO
  output-association, the BLOCKED-terminal answer assembles a
  bounded CASE-OBSERVED section from that observation's summary —
  deterministic fields only: detected cluster
  (cluster_device_sharing_4), 12 linked accounts, shared device
  DEV1L71883DLR — explicitly labeled as DETECTED NETWORK SIGNAL,
  never as confirmed violation, never as per-account verdicts.
- **Data path (four layers, never conflated)**: (1) tool EXECUTED ✓
  (PROVEN); (2) WRITTEN to observations ✓ (PROVEN); (3) CONSUMED by
  the composer — **the fix target**: the O1 section assembly reads
  the analyze observation's summary deterministically; (4) APPEARS
  in the user's final answer — verified by Browser E2E.
- **The LLM BLOCK reason demoted**: the decision-record reason is
  internal audit only; the served answer's claims are grounded in
  the observations ("no evidence or policy text was gathered" is a
  factual error and must not serve).
- **Evidence-absent branch**: when NO analysis observation exists
  (none ran), the O1 section states the evidence gap honestly —
  no fabricated explanation (fail-closed).

### P1 — PARTIAL terminal semantics by GOAL-LEVEL satisfaction
(Calibration 2026-11-01 — replaces the cruder ≥1-satisfied rule)

- **Judgment basis (user rule)**: PARTIAL is decided by the user
  goal's ACTUAL satisfaction degree, NOT by the count of satisfied
  outputs. Output classes are determined deterministically from the
  contract (claim_kind / evidence_requirements) — never O1/O2
  hardcoded:
  - CORE-explanation outputs: claim_kind = explanation
    (the "why is this a risk" ask);
  - POLICY-support outputs: evidence_requirements ∋ policy_references.
- **Decision table** (V1 shapes; the core-explanation-class is the
  user's primary ask in this defect family):
  | core explanation | policy support | overall terminal | user-visible principle |
  |---|---|---|---|
  | ✓ | ✓ | PARTIAL + goal_satisfied-family (both halves delivered; G6 cap applies) | full bounded answer: case network facts + §5.1 + general rationale |
  | ✗ (failed) | ✓ (delivered) | **NOT PARTIAL** (user Calibration rule 10): the core question got no meaningful answer — overall stays BLOCK-family; the answer presents the delivered policy citation AND an explicit "core explanation not generated" statement | never claim the core ask partially answered |
  | ✓ | ✗ | PARTIAL (core answered; policy retrieval failed, honestly stated) | |
  | ✗ | ✗ | BLOCK + FAILED (existing semantics; nothing delivered) | |
- **Auxiliary-step failures** with the core answered → PARTIAL with
  the auxiliary gap noted (the core answer is not invalidated).
- **Mechanism**: reuse the EXISTING output-satisfaction +
  bounded-delivery machinery (表 D row 3); the ONLY new deterministic
  point is the output-class judgment above — no new state system.
- **Boundary**: NO-output-satisfiable → the existing BLOCK terminal
  stands; the V1-unreachable mixed Browser scenario remains
  DEFERRED (§13.10 item C).

## D. Execution Sequence

1. **Fix-1 (P0-1)**: anchoring extension + vocabulary addition;
   acceptance = unit tests (O1→F6 resolution on the real ask; no-ref
   stays unanchored; exactly-one discipline).
2. **Fix-2 (P0-2)**: RE_PLAN gap carry + repeat-step drop;
   acceptance = unit/loop tests (no identical missing-arg step after
   RE_PLAN).
3. **Fix-3 (P0-3)**: canonical composed response at the BLOCK
   terminal; acceptance = loop test (blocked response = composed
   per-output answer, decision reason demoted to audit).
4. **Fix-4 (P1)**: partial terminal semantics + composer consistency;
   acceptance = loop test (mixed-shape → PARTIAL semantics; the
   V1-unreachable mixed browser item remains DEFERRED per §13.10).
5. **Fix-5**: REAL-API E2E (Scenarios 1-2 below) + Browser E2E
   (the real ask user journey) + regression.
   Dependencies: Fix-1 → Fix-2 → Fix-3 → Fix-4 → Fix-5; Fix-3 needs
   Fix-2 (repeat suppression); E2E needs all.

## E. Definition of Done

See the tasking §六 DoD 1-10, mapped:
1. Planner binds F6 to explain_signal for this ask — unit + API E2E.
2. retrieve_policy/explain_signal use the SAME finding identity —
   asserted on the persisted contract + tool args.
3. Missing finding_id intercepted at plan validation/execution guard —
   never reaches the tool as a blind call.
4. RE_PLAN does not regenerate the same gap (digest carries the gap;
   repeated gap → step dropped).
5. Successful policy + analyze results appear in the final answer
   (policy citation verbatim; the case-observed shared-device network
   stated as case-confirmed).
6. The answer distinguishes: case-observed network / AML §5.1 general
   requirement / unproven items (per-account detail, edges) — with
   cluster_risk_score=80.0 kept cluster-level and case risk 86.1 kept
   case-level (never merged).
7. Partial-failure terminal: PARTIAL + NO_VIABLE_ACTION-family
   semantics consistent across task/activity/response; no
   blocked/unanswerable mislabel for the partial shape.
8. Insufficient-evidence asks still fail-closed honestly (no
   fabricated edges/scores).
9. Other findings/tools/general explanations unaffected (regression
   green).
10. No V1 multi-target extension; no new tools; no GU contract/evidence
    enum change.
11. BOTH step origins anchored: supplement-inserted AND existing
    LLM-planned finding-level steps (Plan Calibration P0-1).
12. retrieve_policy and explain_signal serving the same explanation
    task use the SAME resolved Finding identity (plan-level pin, not
    LLM consistency).
13. analyze_related_accounts SUCCESS results appear in the FINAL
    user-visible answer (not only in observations/digest); the answer
    separates case-observed facts / AML §5.1 general requirement /
    unproven items; cluster_risk_score stays cluster-level and case
    risk stays case-level (never merged).

## F. Verification Plan

1. **Unit/contract** (new `tests/test_wp2_goal_findbind_explain_v1.py`):
   anchoring resolution for the real ask (O1→F6 via the added
   vocabulary); exactly-one discipline; no-auto-bind on ambiguity;
   digest gap-carry; sanitizer regressions (Cases A-E already in
   test_wp2_goal_delivery_overreach_v1.py).
2. **Loop/component** (existing
   `tests/test_wp2_b_s5_e2e_v1.py::test_s5_d_resume_no_bypass...`
   pattern): anchored-step execution on the loop path.
3. **REAL-API E2E** (TestClient real HTTP face, injected model
   boundary, offline RP — the established S5 harness):
   - Scenario R1 (valid resume delivery): seeded midstate + persisted
     set → resume → completed, artifact within boundary, dual echoes,
     plan identity continuous. **PROVEN — REAL API E2E** (existing,
     re-run green).
   - Scenario R2 (resume fail-closed, cross-investigation): →
     blocked/clarified, zero artifacts. **PROVEN — REAL API E2E**
     (existing, re-run green).
   - Scenario N1 (new, this fix): /submit the U00033 compound ask →
     the explain step anchors to F6 → explain succeeds (or degrades
     with the bounded general explanation per Fix-3), policy citation
     in the answer, task NOT failed-by- explanation-tool, partial
     semantics correct.
   - Scenario N2 (new): RE_PLAN gap suppression — the re-plan does
     not regenerate the identical missing-finding_id step.
4. **REAL Browser E2E** (`qa/wp2_s6_browser_e2e.py` re-run + the new
   U00033 compound journey): the served answer shows the §5.1
   citation + the case network facts and does NOT contain
   "downloadable"/"Export all trade records"; status/tone rendering
   correct.
6. **Anchoring on EXISTING steps** (Plan Calibration P0-1): the
   LLM-planned explain_signal step resolves its Finding via
   `_output_target_finding` before execution — asserted in unit +
   loop tests.
7. **Identity consistency**: retrieve_policy and explain_signal
   serving the same explanation task carry the SAME resolved Finding
   identity — asserted at plan level.
8. **RE_PLAN gap suppression**: the re-plan does not regenerate the
   identical missing-finding_id step; suppression never touches
   other legal steps.
9. **analyze results in the final answer**: the 12-account /
   shared-device / cluster facts appear in the served answer's core
   section — not only in observations or the digest.
10. **Policy excerpt display**: the §5.1 excerpt renders correctly in
    the served answer.
11. **Fact/principle separation**: case-observed facts vs AML
    general requirements vs unproven items are visually and textual
    distinct — no unproven per-account risk scores or confirmed
    violations.
12. **O2-satisfied/O1-missing is NOT PARTIAL** (Calibration P1):
    the blocked-family terminal holds; the answer says the core
    explanation was not generated.
13. **Task / Activity / served-answer consistency**: the task
    status, activity trail, and served text reflect the same
    execution facts (no "nothing gathered" claim beside a delivered
    citation).

Must-distinguish: original user input / sanitized final goal /
planner-adopted plan / executed tools / served answer / browser text.

## G. Risks / Rollback

- **Planner**: vocabulary addition may over-anchor ambiguous asks —
  mitigated by exactly-one discipline + fail-closed fallback.
- **RE_PLAN**: gap suppression could drop a legitimate step — bounded
  by the clarify/degrade path (the user still gets an answer).
- **Composer**: partial-terminal wording changes are user-visible —
  browser E2E gates them.
- **Rollback boundary**: each fix is an independent, revertible
  commit-sized change (anchoring extension; digest gap line; BLOCK
  response selection; partial terminal semantics). No Git operations
  performed by the implementer; rollback = user-directed revert of the
  specific file.

## H. Scope and Stop Conditions

**In scope**: the four fixes above + their tests/E2E + evidence/docs.
**Out of scope**: GU evidence-type recalibration (explicitly deferred
unless Fix-1 proves insufficient — escalate per §七); V1 multi-target;
Chinese delivery vocabulary; composer drift failures; S6/S7 deferrals;
Resume proposer interplay (Decision 1 already implemented).
**Stop conditions**: any frozen-rule change requirement; inability to
anchor F6 deterministically; real-API E2E cannot prove the binding;
any unrelated regression caused by the fix → stop, report, await
user decision.

## PMO notes (this planning round)

- Plan doc path: `docs/plans/
  U00033_FINDING_BOUND_EXPLANATION_AND_PARTIAL_RESULT_REPAIR_PLAN_V1.md`.
- Files inspected (read-only): agent_loop.py (admission/anchoring/
  BLOCK terminal), planner_v2.py (_user_prompt/_output_target_finding/
  LLM plan call), planning_coverage.py (supplement/step_covers_output),
  investigation_concepts.py (_EXPLAIN_DOMAINS), executor_v2.py
  (REQUIRED_ARGUMENTS/runtime args), goal_resolution.py (GU prompt/
  build_goal_contract), policy_lookup.py, agent_states +
  investigation_sessions persisted docs (TASK-e21f49969484).
- Proposed product changes: goal_resolution.py (sanitizer — ALREADY
  IMPLEMENTED in the authorized goal-overreach fix), planner_v2.py
  (vocabulary), agent_loop.py (anchoring extension + RE_PLAN gap +
  BLOCK response + partial semantics), executor_v2.py (blind-retry
  guard), investigation_service.py (NO change — /turn untouched).
- P0/P1 scope: P0-1..P0-3 + P1 as above.
- Open items: GU scope/subject inconsistency (finding_level + case
  subject) — recorded, not fixed; Chinese vocabulary — out of scope.
- **Status: PLAN READY — AWAITING USER APPROVAL**

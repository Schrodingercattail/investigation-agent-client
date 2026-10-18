# GSP Exploratory Session 01 — Compound Intent & GoalContract Extraction Completeness

**Date**: 2026-10-09
**Type**: Exploratory testing (observation only — no production code
changed, no test expectations changed, no fixes applied).
**Predecessor finding**: the acceptance browser round (2026-10-08)
observed a compound request answering only one subgoal with no
unsupported/unresolved boundary for the other, under `GoalContract=None`.

---

## 1. Objective

Core question: **when one request carries multiple goals, does the
system extract and preserve them all — and if no complete GoalContract
can be built, do the missed goals vanish silently from the
user-visible result?**

The investigation deliberately did not pre-assign the defect layer. The
candidate layers were: goal resolution/extraction → contract
construction → planning coverage → observation association →
postconditions → GoalOutcome → composition (legacy path) → UI.

## 2. Environment / runtime freshness

- Worktree: HEAD `58881a9` + the GSP working tree (unchanged all round;
  no checkout/reset/revert).
- `:8001` restarted for this session: PID 8020, started 2026-10-09
  10:00:05 (after every source mtime), canonical invocation
  `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`
  from the worktree `backend/`; `ps eww` verified
  `ENABLE_TARGET_VALIDITY_GUARD=true`, `ENABLE_GOAL_SEMANTIC_CONTRACT=true`,
  and **no** `AGENT_LOOP_SCRIPTED_SESSION_FILE` (real-model extraction —
  the seam was not used in any trial).
- `:3001` vite dev server (serves source from disk; proxy → :8001);
  `:8000` RP live (`U00010` evidence 200). Topology 3001→8001→8000
  unchanged. After the session, `:8001` was restored to flags-OFF
  default (PID 6854 lineage).
- Harness: `qa/browser.py` + `qa/journeys_common.py` (real headless
  Chrome); new driver `qa/exploratory_gsp_compound_session01.py` (harness
  reuse, no second browser stack).

## 3. Charter and trial design

Six real browser journeys, each a fresh investigation of the same case
(U00010, case-level focus, identical intake turn), varying ONE dimension
at a time:

| Trial | Input (verbatim) | Dimension |
|---|---|---|
| A | 这个账号有没有对冲交易？另外把它的关联账号也列出来。 | original reproduction |
| B | 把这个账号的关联账号列出来，并检查它有没有对冲交易。 | subgoal order swapped |
| C | 请分别回答两项：第一，这个账号是否存在不同账户之间的对冲交易；第二，请列出它的关联账号。两项都要单独给出结果，无法判断时请明确说明。 | explicit enumeration + per-item demand |
| D | 调查这个账号的跨账户对冲交易，同时整理它的关联账号清单。 | natural compound verb |
| E1 | 检查这个账号有没有不同账户之间的对冲交易。 | single-goal control |
| E2 | 列出这个账号的关联账号。 | single-goal control |

Per trial evidence: full page text, screenshot, served activity
projection (`goal_representation`, associations), the store document
(GoalContract, planning_coverage, step_output_map, decisions,
observations, terminal state) and the composed response from the
conversation read model. Evidence archive:
`docs/evaluations/gsp-exploratory-s01-trials-20261009T024343Z.json`.

## 4. Observations

### 4.1 The single controlling pattern (A, B, C, D, A-rerun — 5/5)

Every compound variant produced the SAME chain:

1. **Goal understanding returns `AMBIGUOUS`** (source=llm; goal_type
   None). The model's reason text explicitly names BOTH objectives,
   e.g. A-rerun: *"The request combines two distinct objectives:
   checking whether the account has hedging trades and listing its
   related accounts."*
2. **GoalContract = None.** Per the goal-understanding contract, only
   INFERRED/EXPLICIT representations carry a required-outputs proposal;
   AMBIGUOUS never does. `build_goal_contract` → `no_proposal`.
3. **The loop proceeds on the legacy path** (AMBIGUOUS does not block a
   case-level request) and the planner produces a **single-skill
   plan**: A/B/A-rerun → `[analyze_related_accounts]`; C →
   `[analyze_cross_account_trades, generate_artifact]`; D →
   `[analyze_cross_account_trades, generate_artifact]`.
4. The **decision proposer detects the missing subgoal** at RE_PLAN
   time in 4/5 runs (A, B, A-rerun: hedge part missing; D: related list
   missing) — but the re-plan returns the SAME skill again, the second
   execution reproduces an identical observation, and the convergence
   guard stops the loop (bounded, correct).
5. **The composed response answers only the executed skill's
   subgoal.** The other subgoal is ABSENT — no answer, no unsupported,
   no unresolved, no ambiguity note. Terminal rendered as completed /
   goal_satisfied. On the page, the AMBIGUOUS goal representation is
   invisible (checked: no ambiguity/unanswered wording anywhere).

Order (B), explicit enumeration with per-item demands (C), and compound
verbs (D) made **no difference** — the single goal_type vocabulary
forces every compound request into AMBIGUOUS.

### 4.2 Trial-by-trial outcome table

| Trial | Contract | Steps executed | Subgoal 1 (hedge) in answer | Subgoal 2 (related) in answer | Terminal |
|---|---|---|---|---|---|
| A | None | analyze_related_accounts | absent | answered (18 accounts) | completed/goal_satisfied |
| B | None | analyze_related_accounts | absent | answered | completed/goal_satisfied |
| C | None | analyze_cross_account_trades + artifact | answered (6 candidates, budget boundary) | **absent** (word "关联账号" appears nowhere in the response) | completed/goal_satisfied |
| D | None | analyze_cross_account_trades (+artifact re-planned) | answered | absent | completed/goal_satisfied |
| A-rerun | None | analyze_related_accounts | absent | answered | completed/goal_satisfied |
| E1 control | GC-20f926ff, 1 output, cross_account_check INFERRED | cross-account + artifact | answered (PARTIAL rendered honestly: "已部分回答") | n/a | completed |
| E2 control | GC-6d98eb26, 1 output, identify_related_accounts INFERRED | analyze_related_accounts | n/a | answered ("已完整回答") | completed |

### 4.3 Follow-up 1 — stability (repeat A)

Identical input rerun (browser CDP dropped mid-wait — harness layer —
but the goal POST had been issued and the run completed server-side;
read from the store): same AMBIGUOUS reason, same single-skill plan,
same RE_PLAN-detects-then-repeats, same silent drop. **5/5 stable.**

### 4.4 Follow-up 2 — mechanism pin (real-model extraction probe)

`understand_goal` called directly (real model, no script) on A's and
C's inputs:

- **A**: LLM returned `AMBIGUOUS`; reason names both objectives; NO
  `contract_proposal` attached; `build_goal_contract` → `no_proposal`.
  This pins the mechanism: **the model understands both goals but the
  single-goal_type schema (+ AMBIGUOUS escape) gives it no way to
  represent them, and the AMBIGUOUS path drops any proposal by
  design.**
- **C probe run**: the LLM call returned an empty response
  (environment noise, logged) → the deterministic heuristic fast path
  classified it `INFERRED cross_account_check` (source=heuristic) — a
  SECOND, distinct route to `contract=None` (single-goal heuristic
  classification, proposal never generated). Same user-visible outcome.

### 4.5 Additional finding — decision-reason overclaim (Trial C)

C's final STOP decision reason, user-visible in the Activity panel,
claimed *"两项问题（对冲交易判断与关联账号）可据此给出结论性回答"*
(both questions answerable from the evidence) — while the
related-accounts item was never investigated and is absent from the
answer. This violates the language-discipline invariant (decisions must
not overclaim evidence) at the proposer layer, in a contract-less run.

## 5. Defect classification (per §6 rules, each backed by ≥1 backend
evidence point)

| Finding | Classification | Evidence |
|---|---|---|
| Compound request → AMBIGUOUS → contract=None → other subgoal silently absent (A, B, C, D, A-rerun) | **EXTRACTION_GAP** (goal-understanding schema cannot represent compound intent; AMBIGUOUS path drops the proposal) — manifesting through the legacy composition path with no unanswered-goal accounting | Store documents (goal_representation AMBIGUOUS, goal_contract null, single-skill plan_steps), response texts (missing subgoal), 5/5 runs |
| C: STOP reason claims both enumerated items answered | **Decision-language overclaim** (composer-adjacent; proposer layer) — recorded as a distinct finding, not extraction | Store decisions[] for TASK of C; response text lacks the second item |
| Heuristic fallback route to contract=None (probe) | **EXTRACTION_GAP** (second mechanism: LLM empty response → single-goal heuristic classification) | Probe log (`LLM response contained no text block` → heuristic INFERRED, no proposal) |
| E1/E2 single-goal controls | **NO_REPRODUCTION** — each subgoal individually extracts, plans, executes and composes correctly through the full GSP path | Contracts GC-20f926ff / GC-6d98eb26, honest PARTIAL/COMPLETE renderings |

Not classified as environment-blocked: all six trials produced valid
semantic runs. Environment noise observed (recorded, not semantic):
CDP `socket is already closed` ×2 (harness), one z.ai empty response,
and the two `LLM_UNAVAILABLE` planning failures from the 2026-10-08
acceptance round (same goal family).

## 6. Layer attribution — what it is NOT

- **Not a GSP-4/5/6 regression**: no contract existed in any compound
  run, so postconditions, GoalOutcome and coverage never engaged (the
  report must not describe those layers as having "passed" or "failed"
  here).
- **Not planning-coverage (COVERAGE_GAP)**: coverage admission never
  ran (no contract).
- **Not observation association / EVIDENCE_ASSOCIATION_GAP**:
  associations were contract-gated and absent for the same reason.
- **The O2 ANY-contract boundary is NOT relevant**: O2 concerns
  requirement listing WITHIN an accepted contract; here no contract is
  ever built.
- **Loop bounding worked**: the convergence guard terminated all
  no-progress re-plans (4/4); no unbounded loop, no fabricated
  completion of the missing subgoal by the loop layer itself.

## 7. Architecture invariants implicated (analysis, not design change)

1. **Single-goal representation**: `GoalRepresentation.goal_type` (one
   value) + the AMBIGUOUS escape make compound intent unrepresentable.
   The model demonstrably resolves both objectives (reason text) yet
   can only emit "AMBIGUOUS".
2. **Contract-less runs have no completeness governance**: I-4
   ("unrelated successful evidence ≠ the user-request answer") is
   enforced by the contract path only. Under contract=None the legacy
   composer renders whatever the single executed skill produced, and
   the terminal semantics (`completed`/`goal_satisfied`) are loop-level
   only — nothing states that a requested subgoal was never addressed.
3. **One skill per plan**: the planner selects exactly one skill; a
   compound ask spanning two case-level skills cannot be covered by any
   single plan, and a RE_PLAN cannot repair it (the planner repeats the
   same skill). The proposer's correct detection of the missing part is
   architecturally unactionable.
4. **AMBIGUOUS is invisible to the user**: the served
   `goal_representation` carries AMBIGUOUS but no surface renders it or
   its consequence (partial pursuit of a compound request).

## 8. Existing coverage vs needed regressions

Covered today: single-goal extraction with scripted proposals
(test_gsp2_*), compound-free planning/coverage/composer paths
(gsp1–gsp7), E1/E2-shaped flows via AL-3 eval scenarios.

NOT covered (recommended future regressions — design decisions first):
1. Compound-intent extraction behavior: pin the CURRENT observed
   contract (AMBIGUOUS + legacy path) at the unit level, and — once a
   design decision exists (decomposition vs clarification vs explicit
   unanswered-boundary rendering) — pin the upgraded behavior.
2. Contract-less multi-goal composition: the response must carry an
   explicit unanswered/boundary statement for subgoals that no executed
   step addressed (currently absent).
3. Decision-language discipline: STOP/RE_PLAN reasons must not claim
   un-investigated items were answered (Trial C overclaim).
4. Stability of the E1/E2 single-goal contract path under paraphrase.

## 9. Open / inconclusive

- Whether ANY wording can make the current extractor produce a
  two-output contract (5 variants all failed; exhaustiveness not
  claimed).
- Rate/conditions of the z.ai empty-response and LLM_UNAVAILABLE
  failures (two distinct symptoms of the same endpoint observed across
  sessions); they occasionally reroute extraction to the heuristic path
  (mechanism 2), which itself is unmeasured in frequency.
- UI-side treatment once a design decision exists (the current AMBIGUOUS
  invisibility is recorded as observed behavior, not a UI bug verdict).

## 10. Recommendation

Treat "compound intent" as a named semantic gap with a pinned,
per-layer evidence record (this document). Recommended sequencing:
(1) design review of the three candidate directions — request
decomposition into multiple contract outputs, clarify-style AMBIGUOUS
surfacing, or legacy-path unanswered-boundary rendering;
(2) then implementation of the chosen direction plus the regressions in
§8. No code or test changes were made in this session.

## 11. Evidence

- `docs/evaluations/gsp-exploratory-s01-trials-20261009T024343Z.json`
  (full page texts, store documents, responses, per-trial state)
- Screenshots: `qa/screenshots/expl01-{A,B,C,D,E1,E2}.png`
- Session store tasks: A TASK-c9d0716f2d36-lineage runs incl. rerun
  TASK-0edf9e6af03e; B/C/D/E per archive JSON.

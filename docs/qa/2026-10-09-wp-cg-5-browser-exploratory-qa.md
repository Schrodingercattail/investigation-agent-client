# WP-CG-5 — Browser E2E and Exploratory Regression — QA Record

**Date**: 2026-10-09
**Status**: IMPLEMENTED + VALIDATED — deterministic browser journeys
7/7, Trial A–E regression 6/6, real-model paraphrase exploratory 4/4
PASS. Not committed / not pushed.

## Scope / non-goals

In: the 7 approved deterministic browser journeys (real Chrome, real
loop, model boundary via the approved seam); Session-01 trials A–E
fixed as canonical-stack regression (structured contract comparison);
REAL-MODEL paraphrase exploration (no seam) classified
PASS/FINDING/ENVIRONMENT_BLOCKED. Out: UI redesign, production semantic
changes (GSP-3..7, CG-1/2/3 behaviors, O2), new infrastructure.

## Runtime freshness

- Worktree HEAD `58881a9` + the CG/GSP working tree; no
  checkout/reset/revert.
- Deterministic journeys: backend restarted from the current worktree
  (PID 26045→corrected restart, flags G1+GSP ON,
  `AGENT_LOOP_SCRIPTED_SESSION_FILE=/tmp/cg5_session.json` — a swap
  file each journey rewrites before its goal POST). An initial run was
  invalidated by a harness/env mistake (the backend env pointed
  directly at the J1 session file, so every journey ran the J1
  session); corrected to the swap file and the round re-run — the
  invalid run's results were discarded (evidence: all 7 store rows
  carried the J1 contract).
- Real-model exploration: backend restarted with flags ON and NO seam
  env (verified `ps eww` → 0 matches); real z.ai endpoint (10 calls
  logged, all 200).
- End state: backend restored to canonical default (flags OFF, no
  seam; PID 32217, health 200; env verified 0); frontend :3001 (vite,
  disk-served) and RP :8000 live throughout; topology unchanged.

## Part 1 — deterministic Browser journeys (7/7 PASS)

Harness: `qa/journey_cg_compound_l3.py` (reuses `qa/browser.py` +
`qa/journeys_common.py`; per-journey session swap; store-document
evidence per journey). Task IDs (store) and results:

| Journey | Input | Contract (goal_type / outputs / satisfactions) | RE_PLAN | Browser | Verdict |
|---|---|---|---|---|---|
| J1 single hedge | 检查这个账号有没有不同账户之间的对冲交易。 | cross_account_check / [hedge_presence] / [partially_supported] | no | 对冲候选 + PARTIAL wording; no completion claim | PASS |
| J2 single related | 列出这个账号的关联账号。 | identify_related_accounts / [related_accounts_list satisfied] | no | 18 accounts rendered; "已完整回答" consistent | PASS |
| J3 compound two outputs (Trial A text) | 这个账号有没有对冲交易？另外把它的关联账号也列出来。 | COMPOUND (goal_type None) / [hedge pending, related satisfied] | **yes (rev 2)** | O2 answer rendered; O1 renders the not-determined boundary 「该项尚未产生可判定的结果」; overall 「已部分回答」; no completion claim | PASS (see boundary note) |
| J4 partial honest boundary | 这个账号和它的关联账号之间有没有共享IP？另外把它的关联账号也列出来。 | COMPOUND / [shared_ip_view UNSUPPORTED, related satisfied] | no | shared-IP capability boundary 「当前系统不具备回答该项所需的核查能力」+ related answer; overall partial; never fake-complete | PASS |
| J5 genuine AMBIGUOUS bounded stop | (same compound text; scripted genuine-ambiguity model text) | none — `ambiguous_not_executed` | n/a | blocked card + 「无法确定您要调查的目标…」; ZERO tool executions; zero planner | PASS |
| J6a INVALID_PROPOSAL guard | 帮我梳理一下这个账号目前的情况。 | none — `invalid_proposal:invalid_evidence_type` | n/a | restricted execution completes with the coverage boundary | PASS |
| J6b LLM_EMPTY guard | 帮我梳理一下这个账号目前的情况。 | none — `llm_empty` | n/a | restricted execution completes with the coverage boundary | PASS |

Journey 3 boundary note (known approved limit, not a defect): the
planner adopts ONE skill per plan, and this goal text routes the
DETERMINISTIC pre-LLM branch to the related-account skill for both
plan calls — so the hedge output stayed PENDING with an explicit
not-determined boundary and PARTIAL overall (never silent, never
fake-complete). The RE_PLAN mechanics (rev 2, re-admission, contract
immutability) are fully proven in this journey; a plan-2 that switches
skills is future planner work outside the approved CG scope.

Screenshots: `qa/screenshots/cg5-J1..J6b*.png`. Full per-journey
backend evidence: `docs/evaluations/wp-cg5-deterministic-results.json`
(copy of /tmp/cg5_l3_results.json).

## Part 2 — Trials A–E regression (6/6 PASS)

`backend/tests/test_cg5_trial_regression_v1.py` — canonical-stack API
journeys, seam-deterministic, structured CONTRACT comparison (claims
sets, goal_type, per-output accounting) — never verbatim prose:

- Trial A (original compound): COMPOUND contract, both claims
  preserved, contract_accepted — PASS.
- Trial B (swapped order): same integrity — PASS (order-insensitive at
  the contract layer).
- Trial C (explicit enumeration): same integrity — PASS.
- Trial D (compound verb): same integrity — PASS.
- Trial E1 (single hedge): INFERRED single-output contract
  (cross_account_check), NOT misread as COMPOUND — PASS.
- Trial E2 (single related): INFERRED single-output contract
  (identify_related_accounts) — PASS.

Historical failure shapes (Contract=None, silent single-skill runs)
are documented in Session-01 and are NOT the expected values here;
under the approved design every trial preserved both deliverables or
the single deliverable with honest semantics.

## Part 3 — real-model paraphrase exploration (4/4 PASS)

Backend: flags ON, NO seam (real z.ai glm-5.3; 10 provider calls, all
HTTP 200). Real browser journeys, fresh investigation each:

| Trial | resolution | Contract outputs (model's own wording) | satisfactions | Classification |
|---|---|---|---|---|
| RM_A | **COMPOUND** | hedging_trade_existence + related_accounts_list | pending + satisfied | **PASS** |
| RM_B | **COMPOUND** | 账号U00010的关联账号 + 该账号是否存在对冲交易 | satisfied + pending | **PASS** |
| RM_C | **COMPOUND** | Cross-account hedging trades existence + Related accounts list | partially_supported + pending | **PASS** |
| RM_D | **COMPOUND** | Cross-account hedging trades exist + Related accounts list | partially_supported + pending | **PASS** |

All four: contract_accepted; two semantically independent outputs (the
model's own varied wording — Chinese/English across trials — accepted
by the deterministic builder); per-output honest states with rendered
boundaries; zero completion claims; zero silent omissions; zero
substitutions. The uncovered deliverable renders the not-determined
boundary (single-skill-per-plan planner boundary, honestly surfaced
through the contract path). Evidence:
`docs/evaluations/wp-cg5-realmodel-results.json` +
`qa/screenshots/cg5-RM_*.png`.

Harness note: the exploratory script's initial auto-classification had
a set-slicing bug that labeled all four ENVIRONMENT_BLOCKED; fixed
(harness-only) and the classifications above were re-derived from the
recorded store evidence — the browser runs themselves were valid.

## Regression and final state

- Default-mode battery (17 files — cg1, cg2, cg3 L1+API, gsp1–gsp7
  incl. API E2E, al351, activity, goal resolution ×2, clarification):
  **299 passed, 0 failed** (same deduplicated population as
  documented; no new failures).
- CG-4-mode suites this round: CG-4 API E2E 11/11 (canonical stack);
  Trials regression 6/6.
- Pre-existing baselines (2 re-plan + 4 legacy-agent) out of scope,
  untouched.
- `git diff --check`: clean.

## Findings / defects

- New production semantic defects: NONE.
- Old-shape recurrence: NONE (A–D no longer produce Contract=None
  silent drops under the seam; real model produces COMPOUND).
- Environment: none this round (provider healthy throughout).
- Harness: two issues fixed (session-file env mismatch on the first
  deterministic round — invalid run discarded; classify set-slicing
  bug).

## Manual-acceptance prerequisites

All 13 §13 criteria met: journeys evidenced, trials fixed as
regression, structured integrity comparison used, two-goal journey
renders both outputs independently, partial/bounded paths honest,
AMBIGUOUS never executed single-goal, no overclaim, RE_PLAN
immutability + re-admission evidenced, real-model exploration executed
and classified 4× PASS, all FINDING/ENVIRONMENT items archived,
regressions green, runtime restored to default, this record written.

Not committed / not pushed.

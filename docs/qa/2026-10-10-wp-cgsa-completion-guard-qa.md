# WP-GS-A — Universal Completion-Claim Guard + Legacy DI Seam — QA Record

**Date**: 2026-10-10
**Status**: IMPLEMENTED + VALIDATED — L1 8/8, real API E2E 2/2, real
browser journeys 2/2, full regression 310 passed / 0 failed / 17
skipped (mode-gated). Not committed / not pushed.
**Approved basis**: implementation plan v1 §4.1 (WP-GS-A), design v1
Revision 3 §9 (universal completion-safety, INV-C).

## Summary and exact scope

The flag-independent completion-claim guard: a user-visible loop
response must not claim the user's request was fully answered unless an
accepted GoalContract and its per-output semantic results provide that
basis. Contract-less COMPLETED runs (either flag state) now carry the
basis-scoped closure — execution/evidence completion stated,
whole-request answer judgment explicitly withheld. Byte-exact legacy
closure wording survives ONLY behind a test-only dependency-injection
seam.

Delivered in this WP (per plan §4.1): guard + seam + tests + OFF-mode
assertion migrations. NOT delivered (separate WPs): GS-B (destination
identity), GS-C (facet vocabulary/coverage), GS-D (facet evaluation),
GS-E (aggregate validation).

## Changed files

| File | Change |
|---|---|
| `app/agent_loop.py` | `_completion_closure_policy_override` (module attr, test-only) + `_completion_closure_policy()` (GUARD default; LEGACY only via test injection); `compose_loop_response` legacy-closure guard generalized from flag-gated (WP-CG-3) to flag-independent (WP-GS-A INV-C), wording extended with the explicit no-answer-judgment sentence |
| `tests/test_cg_gs_a_completion_guard_v1.py` | NEW — 8 L1 tests |
| `tests/test_cg_gs_a_api_e2e_v1.py` | NEW — 2 canonical in-process API E2E tests (both flag modes, no contract) |
| `tests/test_cg3_contractless_safety_v1.py` | 2 OFF-mode assertions migrated: guard expected PRESENT under OFF (INV-C flag-independent); byte-exact legacy covered by the seam tests |
| `tests/test_cg3_contractless_api_e2e_v1.py` | scenario E OFF-mode assertion migrated the same way |

## The invariant and its enforcement point

Single enforcement site: the `compose_loop_response` contract-absent
branch (`app/agent_loop.py`, GUARD policy). Contract present → GSP-5
contract-relative outcome line (unchanged). Contract absent →
execution/evidence completion stated + 「系统未建立目标语义契约，不对
"您的请求已得到完整回答"作出判定」. Cross-surface verification:
- Turn responses: verified (current worktree) to emit NO
  goal-completion claim — no guard needed; documented boundary.
- Activity projection: serves goal_outcome/decision fields
  additively; without a contract both are None — no semantic COMPLETE
  is derivable (asserted by test).
- Task-card coarse status: execution-terminal display; the canonical
  semantic surface is the served goal_outcome/decision fields
  (frontend consumption future scope; boundary documented).
- GS-C integration point (reserved, not implemented): when GS-C adds
  `coverage=comparison_intent_uncovered` to the decision record, the
  disclosure rides the SAME guard branch — no assumption in GS-A
  production code; integration documented here per plan §4.1.

## L1 results — 8/8 PASS

`tests/test_cg_gs_a_completion_guard_v1.py`:
guard active flag OFF/ON (contract-less); valid contract keeps the
GSP outcome line with no guard; BLOCKED terminal unchanged (no
boundary fabricated for never-executed runs); LEGACY seam reproduces
byte-exact legacy wording; default policy is GUARD without injection
(negative probe); AgentStatus/StopReason alone produce no semantic
COMPLETE (goal_outcome stays None on contract-less runs); Activity
projection consistency.

## API E2E — 2/2 PASS

`tests/test_cg_gs_a_api_e2e_v1.py` (real FastAPI app, real closure/
projection path, deterministic session seam):
- flag OFF + contract-less: completed; closure carries
  「系统未建立目标语义契约」+ 未能建立完整的目标分解 + the
  answer-judgment withholding; no 已完整回答.
- flag ON + contract-less: identical safety properties.
Both assert the structured result (agent_status, goal fields None)
and the closure text separately.

## Browser E2E — 2/2 PASS (real Chrome, canonical stack, real model)

Backend restarted onto the latest worktree (guard edit 23:24 <
process start 23:41), flags OFF (canonical user-facing runtime), no
seam; frontend :3001 and RP :8000 live.

- J1 (列出这个账号的关联账号。): page shows the guarded basis-scoped
  closure (「未能建立完整的目标分解…不对"您的请求已得到完整回答"作出
  判定」); no unqualified whole-request completion claim. Screenshot
  `qa/screenshots/cgsa-J1-guarded-contractless.png`.
- J2 (这个账号有哪些关联账号？): identical safety properties
  (stability re-run). Screenshot `qa/screenshots/cgsa-J2-guarded-
  stability.png`.
- Results artifact: `docs/evaluations/wp-cgsa-browser-results-*.json`.

## Regression — 310 passed / 0 failed / 17 skipped

Full battery (GS-A ×2, CG-1/2/3 L1+API, CG-4, CG-5 trials, GSP-1..7
incl. API E2E, al351, activity, goal resolution ×2, clarification):
310 passed, 0 failed. The 17 skips are the CG-4-mode-gated suites
(CG-4 API E2E 11 + CG-5 trials 6), which require the backend in CG-4
mode — validated separately during WP-CG-4 and unchanged by GS-A.
Known historical baselines (2 re-plan + 4 legacy-agent) were out of
this battery's scope per the narrow-scope discipline and no code path
they exercise was modified.

## Feature flags and runtime restoration

- Before the browser round: canonical default backend (flags OFF,
  PID 32217) was stale relative to the guard edit → restarted onto
  the latest worktree (PID 41397, 23:41:46, flags OFF) for the
  browser journeys.
- After all rounds: backend restored to canonical default (flags OFF,
  no seam env; verified `ps eww` → 0 matches); frontend :3001 and
  RP :8000 live; health 200. No flag values changed at any point
  (INV-C is flag-independent by design).

## GS-C/D integration points (reserved, not implemented)

- The guard branch is keyed on `state.goal_contract is None`; when
  GS-C lands request-coverage assessment, a coverage-incomplete run
  (contract accepted but comparison intent uncovered) extends the
  guard condition additively at the same site — GS-A's guard design
  places no constraint that would block that extension.
- The disclosure line for the uncovered intent rides the same branch
  (per implementation plan §4.1 task d, delivered with GS-C).



## Acceptance-evidence completion (PMO gate check, 2026-10-10)

The initial QA record covered API cases 1/2/5/6 and browser case A.
This section completes the remaining required evidence (all executed
against the latest WP-GS-A worktree; canonical topology; runtime
freshness per round below).

### API E2E evidence matrix (cases 3–4 newly executed)

| Req # | Case | Test / evidence | Result |
|---|---|---|---|
| 1 | Flag OFF, no contract: no unconditional semantic-completion claim | `tests/test_cg_gs_a_api_e2e_v1.py::test_flag_off_contractless` (in-process real FastAPI app; closure/projection = production code; model boundary via session seam) | PASS — closure carries 「系统未建立目标语义契约」+ coverage boundary; no 已完整回答; goal_outcome None |
| 2 | Flag ON, no contract: same invariant | `::test_flag_on_contractless` | PASS — identical assertions |
| 3 | Valid contract, all outputs SATISFIED: contract-relative completion preserved | NEW `::test_case3_valid_contract_all_satisfied` (seam goal_llm → accepted 1-output contract → satisfied) | PASS — goal_outcome=complete (contract-relative); outcome line renders 已完整回答; no guard line |
| 4 | Valid contract, output pending/partial: execution termination does not upgrade to COMPLETE | NEW `::test_case4_partial_not_upgraded` (2-output contract, plan covers only output 1) | PASS — satisfactions [satisfied, pending]; GoalOutcome=PARTIAL; response 已部分回答; never 已完整回答 |
| 5 | WP-CG-3 contract-less safety | CG-3 suites re-run post-GS-A: `test_cg3_contractless_safety_v1.py` + `test_cg3_contractless_api_e2e_v1.py` (with the 2 INV-C OFF-mode migrations) | PASS — 30 tests |
| 6 | Legacy baseline: test-only DI seam reachable; canonical API path cannot select it | L1 `TestLegacyDISeam::test_default_policy_is_guard_no_injection` (negative probe: no injection → GUARD; module attr None) + `test_legacy_seam_byte_exact` | PASS — LEGACY wording reproduced only under seam injection; canonical closure always guarded |

### Browser E2E evidence matrix

| Case | Journey | Task ID (store) | Evidence |
|---|---|---|---|
| A contract-less guarded closure | J1 + J2 (`qa/journey_cgsa_l3.py`, flags OFF, no seam, real model) | guarded runs verified: closure 「未能建立完整的目标分解…不对…作出判定」 rendered; task-card coarse "completed" coexists with the basis-scoped wording (execution vs semantic boundary by design) | PASS |
| B valid contract, satisfied | NEW J3 (`cgsa-J3-valid-contract-satisfied.png`, flags ON + seam session) | store: contract 1 output satisfied; response renders contract-relative 「已完整回答」 | PASS |
| B' valid contract, PARTIAL not upgraded | NEW J4 (`cgsa-J4-valid-contract-partial.png`, flags ON + seam; 2-output contract, plan covers one) | satisfactions [satisfied, pending]; outcome 已部分回答; the pending output renders its not-determined boundary; never presented complete | PASS |

Runtime freshness for the new rounds: backend restarted onto the latest
WP-GS-A worktree per round (guard edit 23:24 Oct 9 < process starts);
flags per round recorded above; backend restored to canonical default
after (flags OFF, no seam; health 200 verified).

### Harness notes

- The seam-journey helper initially lacked focus passthrough → J3/J4
  first attempts were intercepted by the deterministic resolver's
  clarify path (test-harness bug, fixed; product behavior correct —
  the resolver's clarify is the designed response for ambiguous
  case-level requests without focus).
- The API E2E cases 3–4 file initially missed the `settings` import
  (test bug, fixed).
- The plan's §10.1/§10.2 acceptance matrix (21 scenarios + the
  coverage-incomplete seven-dimension mapping) was added to the
  Implementation Plan by the PMO gate check — the plan had referenced
  a nonexistent §12 scenario table.

## WP-GS-A acceptance status (updated)

**PASS — full Definition of Done now evidenced**: all six API E2E
requirement rows and both browser cases have executed evidence;
INV-C holds in both flag modes; contract-relative COMPLETE is
preserved for satisfied contracts; partial/unresolved outcomes are
never upgraded; the legacy baseline is test-only isolated.

**PASS — full Definition of Done met** (L1 + API E2E + Browser E2E +
regression, runtime freshness evidence, migrations documented).

Not committed / not pushed.

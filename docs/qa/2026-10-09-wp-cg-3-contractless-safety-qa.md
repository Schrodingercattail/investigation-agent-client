# WP-CG-3 — Contract-less Safety and Decision-language Guard — QA Record

**Date**: 2026-10-09
**Status**: IMPLEMENTED + VALIDATED (L1 + real API E2E + real Browser
E2E). Flag default unchanged (OFF). Not committed / not pushed.
**Approved basis**: design v1 Revision 2 (§10-E3, §11, §12) and
implementation plan v1 §6.

## Objective and scope

When a GoalContract cannot be established, the runtime must not present
restricted execution, partial results, or a normal loop terminal as
"the user's request has been fully answered"; genuine AMBIGUOUS must
enter a clarification-shaped bounded termination instead of silently
executing one conveniently-classifiable goal. Four delivered parts:
contract-decision provenance, the AMBIGUOUS disposition, the composer
completion-claim guard, and Activity decision-row integrity.

## As-Is audit findings (guard placement, verified)

- The single response authority for ALL loop terminals is
  `compose_loop_response` (invoked inside `_terminate`,
  `agent_loop.py:1979+`); the GSP/legacy branch lives at
  `compose_loop_response` — the guard belongs in the legacy branch,
  NOT in `_STATUS_PHRASES` (global meaning preserved).
- Decision prose reaches users via activity records built in `_emit`
  and projected through `project_task_activity` → the API layer's
  `TaskActivityResponse` schema (app/api/schemas.py) — which
  whitelists fields: an additive projection field MUST also be added
  to that schema or it is silently dropped (found and handled during
  this WP).
- `CLARIFICATION_NEEDED` bounded termination exists for TARGET
  ambiguity (`agent_loop.py:1289` region) — reused for goal-level
  AMBIGUOUS; no new terminal enum; G1 untouched.
- Flag-OFF baseline: the compound goal text itself triggers the
  planner's deterministic related-account branch and the legacy loop
  thrashes to `stopped_by_limit` on fixture data — the pre-existing
  pathology, preserved unchanged under OFF.

## Changed files

| File | Change |
|---|---|
| `app/goal_resolution.py` | `understand_goal(..., diagnostics=None)`: optional structured provenance (llm_attempted / llm_error); default None — zero impact on existing callers |
| `app/agent_loop.py` | `AgentLoopState.goal_contract_decision` (additive); goal checkpoint: 4-arg resolver attempt (TypeError fallback keeps legacy 3-arg resolvers working), decision record, AMBIGUOUS disposition (flag ON → `ambiguous_not_executed` → BLOCKED/CLARIFICATION_NEEDED before findings/planner/executor); decision rows carry `semantic_basis` ("contract:<id>" / "none" / None-under-OFF) and contract-less STOP rows project a deterministic safe state description (proposer prose kept in `state.decisions` for audit, not projected); composer legacy-branch completion-claim guard line (flag ON + no contract + COMPLETED); projection serves `goal_contract_decision` |
| `app/api/investigations.py` | `_default_goal_resolver` resolver accepts the optional diagnostics dict and threads it into `understand_goal` |
| `app/api/schemas.py` | `TaskActivityResponse.goal_contract_decision` additive field |
| `tests/test_agent_activity_api_v1.py` | projection whitelists updated additively (top-level + activity-row keys) |

New tests: `tests/test_cg3_contractless_safety_v1.py` (13 L1 tests),
`tests/test_cg3_contractless_api_e2e_v1.py` (5 API E2E scenarios),
`qa/journey_cg3_contractless_l3.py` (2 real-browser journeys).

## goal_contract_decision provenance (implemented semantics)

Written only under `ENABLE_GOAL_SEMANTIC_CONTRACT`; `None` otherwise
(legacy documents load with the field absent — round-trip test
included).

| outcome | meaning |
|---|---|
| `contract_accepted` | proposal validated; contract established |
| `no_proposal` | no usable representation / no proposal returned |
| `invalid_proposal:<existing build_goal_contract code>` | proposal present, deterministically rejected (e.g. `invalid_proposal:invalid_evidence_type`) — never mapped to capability UNSUPPORTED |
| `llm_empty` | LLM attempted but failed/empty; heuristic routed (provenance recorded — never treated as full comprehension) |
| `heuristic_single_goal` | clean deterministic single-goal classification (no LLM attempted); routing aid only |
| `ambiguous_not_executed` | genuine ambiguity → clarification-shaped bounded stop |

## L1 results — PASS (13/13)

`tests/test_cg3_contractless_safety_v1.py`:
AMBIGUOUS flag-ON stop (zero planner calls, zero observations,
ambiguous_not_executed, blocked/clarification_needed, honest message,
no completion claim) · flag-OFF legacy flow unchanged (no provenance,
no guard line) · contract_accepted recorded · invalid_proposal
distinguished and never UNSUPPORTED · llm_empty vs
heuristic_single_goal distinguished · composer guard blocks the
completion claim while keeping partial results · guard line absent
under OFF · contract path keeps the GSP-5 outcome line · STOP row
marked (`semantic_basis="none"`) with the safe projected summary ·
contract rows marked `contract:<id>` · legacy states without the new
fields load · COMPLETED/GOAL_SATISFIED alone never produce semantic
COMPLETE (goal_outcome stays None, boundary present) · projection
serves the decision record end-to-end.

## API E2E results — PASS (5/5, real HTTP)

`tests/test_cg3_contractless_api_e2e_v1.py` (real FastAPI routes,
real checkpoint/termination/composer/projection; model boundary via
the existing session seam + minimal monkeypatched default-resolver DI):

- A AMBIGUOUS → blocked/clarification_needed, `ambiguous_not_executed`,
  zero plan steps / zero tool events, honest response and blocked
  Activity row.
- B LLM_EMPTY → `llm_empty` recorded with provider error; restricted
  execution completed; guarded boundary in the response; STOP row
  `semantic_basis="none"`.
- C INVALID_PROPOSAL → `invalid_proposal:invalid_evidence_type`; no
  contract; no silent narrowing (no GSP outcome line in the response).
- D legal single-goal contract → `contract_accepted` → GSP-5 outcome
  line; guard not applicable.
- E flag OFF → legacy flow identical (no provenance, no guard line).

## Browser E2E results — PASS (2/2, real Chrome on :3001)

`qa/journey_cg3_contractless_l3.py`; backend restarted on the current
worktree with the GSP flag + scripted-session seam (goal templates
only — loop/termination/composer/projection fully real):

- J1 AMBIGUOUS (compound request): page shows the blocked terminal
  card — "The investigation is blocked and cannot progress." + the
  full honest message 「无法确定您要调查的目标：该请求存在多种可能的
  理解，系统不会默认只调查其中一项。请明确您希望优先调查的目标后重新
  发起。」 + "Stop reason: clarification_needed."; Activity shows
  "Investigation blocked (clarification needed)" with the same honest
  message; NO completion claim anywhere (screenshot
  `qa/screenshots/cg3-J1-ambiguous-clarify.png`; store
  TASK-fe63421e918d: ambiguous_not_executed).
- J2 INVALID_PROPOSAL (focused run): restricted execution completes
  and the page shows the coverage boundary 「未能建立完整的目标分解…
  可能未覆盖…」 with no whole-request completion claim and no
  target-clarify interference (screenshot
  `qa/screenshots/cg3-J2-invalid-guard.png`; guarded closure from the
  invalid_proposal run).
- Note (pre-existing semantics, unchanged): the loop task CONTAINER
  renders "failed" for blocked loops (coarse container + precise Agent
  State semantics per the AL-2 baseline) — observed identically for
  target-clarify stops before this WP.

Journey-run defect found & fixed during the round: the first J1/J2
invocation revealed `main()` ignoring argv (both journeys ran per
invocation, sessions crossed) and J2 lacking a user-selected focus
(legitimately intercepted by the real target-clarify path). Harness
fixes only; product behavior was correct in every backend state.

## Runtime freshness evidence

- Worktree: HEAD `58881a9` + the GSP/CG working tree; no
  checkout/reset/revert at any point.
- Browser round: stale :8001 stopped; restarted from the current
  worktree (PID 16421, 13:35:50, after all source edits) with
  `ENABLE_TARGET_VALIDITY_GUARD=true`,
  `ENABLE_GOAL_SEMANTIC_CONTRACT=true`,
  `AGENT_LOOP_SCRIPTED_SESSION_FILE=/tmp/cg3_browser_session.json`
  (verified via `ps eww`); topology 3001→8001→8000 unchanged; frontend
  vite (disk-served) 200; RP 200.
- After the round: backend restored to defaults (flags OFF, no seam;
  PID 18255, health 200, env verified clean).

## Regression results

- CG-3 + goal/GSP/Activity/clarification suites (goal_resolution,
  goal_resolution_api, gsp1–gsp7 incl. API E2E, al351, cg3 ×2,
  activity_api, clarification_wi6): **257 passed, 0 failed.**
- Loop/composer/API legacy suites (agent_loop_v1, al1/resume,
  al212/213/214, investigation_e2e, api_investigations,
  error_handling): 139 passed; **6 failed — all documented pre-existing
  baselines**: `test_agent_loop_v1` re-plan ×2 (verified like-for-like
  in the planning round) and `test_error_handling` ×4 (frozen pre-V2
  legacy agent, HEAD-verified). Zero new failures; nothing
  re-attributed.

## Not done / deferred

- COMPOUND extraction, proposal schema, semantic dedup: WP-CG-1/2 (not
  started, per plan).
- Full multi-scenario `goal_llm` seam framework: WP-CG-4 (CG-3 used
  minimal DI only).
- Full multi-scenario browser/exploratory regression: WP-CG-5.
- AD-6 final copy review of the two zh messages (constraints enforced
  by tests).

## Resolver-compat follow-up (PMO check)

- **Original risk**: the goal checkpoint called the resolver with 4
  arguments and fell back to 3 on any `TypeError`. A TypeError raised
  INSIDE a 4-arg resolver's body would be misread as a signature
  mismatch and trigger a second invocation — for the default resolver a
  duplicate real LLM call — and the retry's error would be misfiled as
  a generic "goal representation failed".
- **Fix**: calling convention decided BEFORE the call via
  `inspect.signature(resolver).bind(...)`
  (`_call_goal_resolver`, `app/agent_loop.py`): 4-arg bind succeeds →
  called once with diagnostics; 4-arg bind fails → legacy 3-arg call;
  neither binds → a clear TypeError for the bounded handler (resolver
  never called speculatively). Uninspectable callables use the legacy
  3-arg convention as the safe default. Exceptions raised inside the
  resolver propagate unchanged (single invocation); the checkpoint's
  bounded handler now preserves the real category in the decision
  reason (`goal resolution failed: TypeError: …`) alongside the
  existing `exc_info` log. No state-semantics change.
- **New tests** (`TestResolverCompat`): legacy 3-arg called exactly once
  and works; 4-arg receives the live diagnostics dict (mutation visible
  to the loop); internal `TypeError("boom")` → exactly one call, real
  category preserved in `goal_contract_decision.reason`, bounded
  completion, no internal detail in the user-visible response.
- **Results**: CG-3 L1 16/16 (13 + 3); CG-3 API E2E 5/5;
  `test_gsp2_goal_contract_v1.py` + `test_gsp7_replan_api_e2e_v1.py`
  27 passed (resolver paths and RE_PLAN behavior unchanged). No new
  failures.
- **Remaining limitation**: none known for the supported callable types
  (Python functions/methods/callable objects all inspect cleanly);
  uninspectable callables fall back to the legacy 3-arg convention by
  design.

Not committed / not pushed.

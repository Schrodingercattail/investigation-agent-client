# GSP-7 — RE_PLAN API E2E + scripted-session seam — implementation record

**Date**: 2026-10-08
**Scope**: finish the interrupted GSP WP-GSP-7 API-E2E work: complete the
`AGENT_LOOP_SCRIPTED_SESSION_FILE` seam, add the API-level E2E test for
the actual RE_PLAN flow, and fix the re-plan digest defect the E2E
exposed. No product behavior changed beyond the two fixes listed below;
flag OFF semantics preserved.

**Acceptance status**: **ACCEPTED** — canonical runtime verification
completed 2026-10-08 (see "Canonical runtime verification" below):
current-worktree code proven loaded on :8001, real RE_PLAN over real
HTTP, immutable GoalContract content identical across the re-plan,
GSP-6 re-admission re-established for Plan 2, planner_v2 digest fix
confirmed effective in the runtime, no new related failures.

## Changed

1. `backend/app/agent_loop.py`
   - Completed the GSP-7 API-E2E seam (TEST INFRASTRUCTURE ONLY, default
     OFF): `AGENT_LOOP_SCRIPTED_SESSION_FILE` →
     `{"plans": [...], "decisions": [...], "goal": {...}|absent}`.
     `_ScriptedSessionPlanner` wraps the REAL `PlannerV2` with a FIFO
     scripted LLM (parse/validation/locks stay production-real);
     `_ScriptedSessionDecisions` serves decision proposals FIFO
     (exhausted → STOP); `_ScriptedSessionGoalResolver` serves an
     optional scripted goal-understanding proposal (contract deterministic
     without an LLM). Missing/unreadable file → bounded degradation
     (planner fails, proposer STOPs) with a warning. The session `goal`
     section overrides the default production goal resolver whenever
     present (an unscripted goal understanding would call the real LLM
     from the API path).
   - `_re_plan`: the GSP-7 unsupported-repeat filter is now FLAG-GATED
     (OFF = baseline digest + baseline re-plan behavior) and keyed on
     STEP TYPE (the planning path: binding tool + locked view), not
     `tool_name` globally — a `finding_drilldown` UNSUPPORTED on one view
     must not invalidate the same tool's other views. Removed the dead
     duplicate filter block left by the interrupted edit.
2. `backend/app/planner_v2.py`
   - DEFECT FIX (found by the new E2E): the re-plan request is the user
     goal + a machine digest; digest lines embed tool names whose
     substrings carry intent vocabulary (`signal_explain` contains
     `explain`), so `_EXPLAIN_INTENT_RE` matched EVERY re-plan and the
     deterministic explain-intent branch hijacked re-plans away from the
     planner LLM (scripted or real) — re-plans ignored the goal's actual
     progress context and re-ran explain+policy steps (the exact
     non-progress phenotype GSP-7 exists to prevent). Fix: single-sourced
     `REPLAN_DIGEST_PREFIX`; all intent/specialization matchers in
     `_plan_inner` read `_user_words(user_request)` (pre-digest) only.
     The LLM prompt still receives the full goal + digest.
3. `backend/tests/test_gsp7_replan_api_e2e_v1.py` (new) — 5 tests:
   - R1 happy re-plan: RE_PLAN decision → re-plan adoption → coverage
     re-admission → new legal path (`inspect_evidence`) executed →
     bounded completion; accepted contract served intact after the
     re-plan; response served via the conversation read model.
   - R1b coverage re-admission record for the re-planned plan (store
     level — the coverage list is not an activity-projection field).
   - R2 no-progress re-plan: repeat of the confirmed-unsupported path is
     stripped → bounded `replan_failure`; failed re-plan never adopted
     (revisions = initial only); unsupported path executed exactly once;
     contract served intact even on failure.
   - R3 flag OFF identity: no filter (repeat adopted AND re-executed),
     no contract, no coverage, no associations; legacy bounded
     termination.
   - Seam degradation: missing session file + no LLM → bounded
     `planning_failure`, no fabricated plan, no hang.
4. `backend/tests/test_agent_activity_api_v1.py` — updated the
   activity-projection whitelist pin for the GSP additive read-model keys
   (`goal_contract`, `goal_outcome`, `observation_associations`; frontend
   consumes neither). This pin was broken by the (prior, uncommitted) GSP
   projection change, not by this session.

## Verification levels

- **unit/component TESTED**: `tests/test_gsp1..gsp7`, AL-3.5.1 suite —
  187 passed (includes the GSP-7 component cases, now exercising the
  scripted re-plans for real after the digest fix).
- **API E2E TESTED (in-process ASGI)**: `tests/test_gsp7_replan_api_e2e_v1.py`
  — 5 passed. Real FastAPI app + production loop factory + real
  resolver/planner-validation/executor/tools/postconditions/composer/
  persistence; RP faked at the adapter boundary (repo-standard tier);
  model boundary scripted via the seam.
- **Regression TESTED**: AL-2.x routing/semantics 98 passed; loop/planner
  suites 64 passed + the 2 documented pre-existing re-plan failures;
  API/goal/activity suites 110 passed (after the pin update); adjacent
  suites (closure/execution-mode/focus/context/cross-account/service/
  executor + real-stack al25/al26) 171 passed.
- **Browser (L3)**: NOT TESTED — no user-visible change (seam is
  test-only; the planner fix changes re-plan step selection, not
  response wording contracts; canonical GSP L2/L3 journeys remain the
  acceptance activity per the implementation plan §14).

## Known pre-existing failures (like-for-like verified)

- `tests/test_agent_loop_v1.py::TestRePlanAndRecovery::test_5_no_match_then_re_plan`
  and `::test_6_insufficient_data_leads_to_alternative` — the documented
  baseline re-plan failures. Verified byte-identical failure signatures
  with and without the `user_words` fix (temporarily neutralized and
  re-run) — not re-attributed, not introduced here.
- `tests/test_provenance_model.py` ×6 + `tests/test_evidence_integration.py`
  ×3 + `tests/test_target_refinement_llm_v1.py` E5-hedging ×1 — verified
  failing IDENTICALLY at HEAD (all working-tree changes stashed):
  `tools.py:334` dedupe crashes on `canonical_name=None` (unmodified
  files); untouched by this work and by the GSP working tree.
- `tests/test_error_handling.py::TestAgentExecutionErrors/...` —
  pre-V2 legacy agent (`app/agent.py`) error-path tests; legacy module is
  frozen by project rule and not exercised by any change here.
- `tests/test_tool_orchestration_e2e_al2_final.py` ×4 — drive the REAL
  :8001 backend, whose process predates the current working tree (stale
  service; STALE-STACK finding, not product truth — my changes are not
  loaded in that process). Restart the canonical stack before judging.
- Backend baseline environment-dependent legacy failures (~43 RP down /
  ~12 RP up) unchanged and unattributed.
- Full-suite tally this session (RP up): 19 failed / 1535 passed /
  1 skipped / 1 xfailed — every failure accounted for above.

## Canonical runtime verification (WP-GSP-7 acceptance) — 2026-10-08

### Runtime freshness

| Item | Before | After |
|---|---|---|
| Worktree | HEAD `58881a9` + uncommitted GSP working tree (`agent_loop.py`, `planner_v2.py`, `goal_postconditions.py`, `planning_coverage.py`, `target_validity.py`, …; source mtimes 2026-10-08 21:20:57 after the like-for-like stash round-trip) | unchanged — no checkout, no reset, no revert |
| `:8001` process | PID 87132, started 17:46:47 — **predates every GSP-7 source edit → stale**; env had NO GSP/G1/session vars | PID 940, started 21:49:30 (> all source mtimes), cwd = worktree `backend/`, canonical invocation `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001` |
| `:8001` env | flags OFF, no seam | `ENABLE_TARGET_VALIDITY_GUARD=true`, `ENABLE_GOAL_SEMANTIC_CONTRACT=true`, `AGENT_LOOP_SCRIPTED_SESSION_FILE=/tmp/gsp7_canonical_session.json` (verified via `ps eww`) |
| `/api/v2/runtime/identity` | `git_commit: 58881a9` (disk-computed — per CLAUDE.md not load-proof) | `git_commit: 58881a9` |
| RP `:8000` | live (U00299 evidence 200) | untouched throughout |
| `:3001` | running | untouched (no frontend change; browser N/A) |

Freshness proof chain: (a) process start time > source mtimes; (b) process
started by this verification from the current worktree with the canonical
command; (c) **behavioral markers** (the CLAUDE.md-sanctioned method):
the seam executed the scripted plans verbatim (seam code = current
`agent_loop.py`), and the re-plan behavior matched the fixed planner
(defect evidence below). After verification the backend was restored to
canonical default (PID 1427, 21:56:35, flags OFF, session env removed).

Live-RP lineage precheck (real adapter, no mocks): U00299 → F1 "ML
Pattern Detection" caps `[signal_explain, timeline]` (no
`self_opposite_trades`); F1 `signal_explain` → success **with
`evidence_missing=true`**; `finding_drilldown view=evidence` → success
complete; `view=self_opposite_trades` → `UNSUPPORTED
CAPABILITY_NOT_SUPPORTED`.

### Scenario F — repeat-unsupported (expected bounded failure) — PASS

Task `TASK-a8c72ed01c08` / `INV-611f79858053` (fresh investigation; real
HTTP; real RP; G1+GSP+seam ON):

1. Initial plan `PLAN-23971c28b355 ['explain_signal',
   'inspect_self_opposite_trades']` — GSP-6 admission: O1 covered
   (ml_signal_explanation, transaction_records); `step_output_map
   {S1:[O1], S2:[O1]}` (canonical DB).
2. Iter 1 `explain_signal` → success (gap); iter 2
   `inspect_self_opposite_trades` → **UNSUPPORTED** (real RP).
3. **RE_PLAN decision recorded** (decision log: CONTINUE, RE_PLAN).
4. Re-planned steps `[inspect_self_opposite_trades]` reached the GSP-7
   filter and were **stripped** (backend log 21:54:23: `GSP-7 re-plan
   filter: stripped repeat-unsupported steps
   ['inspect_self_opposite_trades']`) → nothing viable → bounded
   **`replan_failure`** (`failed`); the failed re-plan was never adopted
   (`plan_revisions` stayed 1); unsupported path executed exactly once.
5. Contract `GC-41fb9e9e`: 2 pre-RE_PLAN served snapshots vs final —
   **immutable semantic content identical** (contract_id, goal_type,
   subject_ref, single output O1 claim/claim_kind/subject/scope/
   requirements/description). Mutable runtime state evolved as allowed:
   satisfaction pending→unresolved (assessment not adjudicable),
   `goal_outcome=unanswerable` (loop terminal `failed` ≠ semantic
   COMPLETE — recorded per §9 discipline).

### Scenario F — new-path journey (Plan 2 adopted; GSP-6 re-admission) — PASS

Task `TASK-5990c98bbb8d` / `INV-a4ea4d9cb221` (same conditions; session
content swapped to the already-documented R1 shape — no new test design):

1. Plan 1 `PLAN-f79ffc3a4467 ['explain_signal',
   'inspect_self_opposite_trades']`; explain_signal success;
   self_opposite UNSUPPORTED (real RP).
2. **RE_PLAN decision** → planner returned `['inspect_evidence']` →
   `re-plan PLAN-a65ae51c7134` **adopted** (`plan_revisions=2`).
3. **GSP-6 re-admission re-ran for real** (canonical DB, final records =
   plan 2): `planning_coverage: O1 covered, eligible_step_types
   ['inspect_evidence'], matched [transaction_records,
   withdrawal_records]` — recomputed (plan-1's eligible set was
   `['explain_signal', 'inspect_self_opposite_trades']`);
   `step_output_map` rebuilt to `{"S1": ["O1"]}` for the new plan; no
   output deleted, no narrowing, no nearest-substitution.
4. iter 3 `inspect_evidence` → success (real data) → scripted STOP
   accepted (criteria met by the clean success) → terminal
   `completed`/`goal_satisfied`, iterations 3, decisions (CONTINUE,
   RE_PLAN, CONTINUE, STOP).
5. Contract `GC-25594f4a`: 2 pre-RE_PLAN snapshots vs final —
   **immutable semantic content identical**; satisfaction pending→
   unresolved; `goal_outcome=unanswerable` (assessment claim — the
   runtime gathers evidence, never adjudicates the judgment;
   `completed`/`goal_satisfied` are loop-terminal semantics only).
6. Response served via the conversation read model
   (`task_response_text` present).

### planner_v2 digest-fix — confirmed effective IN THE RUNTIME

With the pre-fix defect, every re-plan request (goal + digest) tripped
`_EXPLAIN_INTENT_RE` on digest tool names (`signal_explain` contains
`explain`) and the deterministic explain+policy branch hijacked the
re-plan to `['explain_signal', 'retrieve_policy']` — the scripted/real
planner LLM was never consulted. In BOTH canonical runs the re-plan
planner output is exactly the scripted plan (`['inspect_self_opposite_trades']`
reaching the GSP-7 filter in Scenario F; `['inspect_evidence']` adopted
in the new-path journey) — the deterministic explain+policy shortcut did
NOT fire, and the GSP-7 filter warning in the log is the post-LLM path.
Behavioral load-proof of the fixed `planner_v2.py` (identity endpoints
are disk-computed and not load-proof per CLAUDE.md).

### Incidents during verification (no product change)

- First canonical attempt terminated bounded `planning_failure`
  (`LLM_OUTPUT_INVALID`): the session file authored plans as JSON
  objects while the seam consumes plan-JSON **strings** — fixture
  authoring error, fixed in the fixture. The runtime's own behavior was
  correct and is incidental positive evidence for bounded degradation on
  the canonical stack (no hang, no fabricated plan).

### Evidence artifacts

- `docs/evaluations/gsp7-canonical-scenarioF-repeat-20261008T135620Z.json`
  (snapshots incl. pre-RE_PLAN contracts, terminal projection, semantic
  tuples)
- `docs/evaluations/gsp7-canonical-scenarioF-newpath-20261008T135620Z.json`
- `docs/evaluations/gsp7-canonical-backend-log-excerpt-20261008T135620Z.txt`

### Unverified / notes

- Browser (L3): N/A — no frontend/user-visible response-contract change.
- With an LLM key configured, a MISSING session file falls through to
  the real LLM (real goal understanding + real planner) — documented
  production behavior; the degradation test pins the no-key environment.
- `inspect_self_opposite_trades` on F1 (ML-only) is the deterministic
  UNSUPPORTED lineage (verified against LIVE RP this round); F1's
  `explain_signal` observation carries `evidence_missing=true` — the
  completion criteria are never satisfied by the signal explanation
  alone.

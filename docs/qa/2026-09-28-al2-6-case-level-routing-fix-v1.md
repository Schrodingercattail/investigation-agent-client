# AL-2.6 — Case-Level Investigation Routing Fix

**Date**: 2026-09-28
**Final status**: **PASS WITH LIMITATIONS** (§14)
**Trigger**: real-browser manual test — case U00010 + raw Chinese goal
"查一下有没有不同账户之间的对冲交易情况，如果有，输出相关证据给我；如果没有，
告诉我你都查了哪些范围内的账户从而判定没有关联交易的" → Agent run started →
`goal_type=cross_account_check` recognized → **BLOCKED context_unresolved
before any planning or tool call**.

---

## 1. Problem Statement

A supported case-level analytical investigation intent (cross-account
opposite-trade / hedging check on a known case) was rejected as an
unresolved context before planning. The runtime had already understood
the goal (`goal_type=cross_account_check`, persisted) and the real RP
data to answer it existed (U00010: Linked Account Network finding, 18
connected accounts, opposite_trade_ratio 0.3438, `network_evidence` with
related account ids). The planner was never reached (iteration=0,
zero plan_steps, zero tool calls).

## 2. Root Cause (code-level, one chain)

```
raw goal
→ goal_resolution.py     heuristic regex matches 对冲/hedge
                         → goal_type=cross_account_check  … DISPLAY ONLY
                           (stored on AgentLoopState, projected to the UI,
                            consumed by NO routing decision)
→ context_resolution.py  resolve(): intent=INVESTIGATION; no focus;
                           _looks_case_level = False (markers: English
                           phrases + "investigate u"/"调查");
                           _mentions_accounts = False (markers: ONLY
                           "those accounts"/"these accounts" — English);
                           Stage-1 LLM assist disabled (no key)
                           → Priority-5 fallback: UNRESOLVED
                           (context_resolution.py Priority-5)
→ agent_loop.py:893-925  UNRESOLVED is enforced as terminal
                           BLOCKED / CONTEXT_UNRESOLVED
→ PlannerV2              never reached
→ cross_account_trade_analysis   never invoked (frozen contract
                           requires only case_id; executor injects it)
```

Three layers maintained three divergent keyword sets for the SAME
concept: goal_resolution (`对冲|hedge`), planner_v2
(`跨账户|对倒|cross-account…`), context_resolution (English only). The
only layer that understood the user was display-only. Additionally the
skill gate required a focused finding (`trade_investigation`:
`required_capabilities=["self_opposite_trades"]` + focus constraint)
although the underlying governed tool (`cross_account_trade_analysis`,
`REQUIRED_ARGUMENTS=["case_id"]`) needs only the case — so even a
correctly-routed case-level request had no eligible skill without a
focus.

## 3. Previous Test Coverage Gap (the honest accounting)

The defect was not "insufficient test quantity". Each prior suite
verified a REAL layer but none of them verified
**natural-language → semantic routing → planner** over the real
boundaries:

### Coverage matrix (by user question type × runtime layer)

| Scenario | Context routing | Goal resolution | Planning | Tool | Real RP | Browser | Previously covered? |
|---|---|---|---|---|---|---|---|
| artifact bundle request | ✅ ARTIFACT_CASE intent (target-independent) | ✅ GOAL_ARTIFACT_EXPORT | ✅ deterministic artifact branch | ✅ fetch_case + artifact_bundle | ✅ AL-2.5 | ✅ AL-2.5 | **YES — the only real-stack scenario** |
| finding-level investigation (title-matching phrasing) | ⚠️ deterministic title-token match only; scripted LLM in tests | ⚠️ (display) | ⚠️ scripted planner in tests | ✅ finding_drilldown (turn path w/ LLM: untested no-LLM) | ✅ turn path (AL-2.5 turn) | ⚠️ findings render only | PARTIAL — mechanics yes, NL routing only via one English phrasing |
| **cross-account hedging request (CJK)** | ❌ **no route (the defect)** | ⚠️ recognized, display-only, injected in tests | ❌ unreachable | ❌ gated behind focus | ❌ never | ❌ never | **NO — regex units + injected-representation API test only** |
| ambiguous finding request | ✅ AMBIGUOUS → ledger (flag-gated) | — | — | — | — | — | YES (units, WI-5/6) |
| unsupported request | ✅ Priority-5 UNRESOLVED → BLOCK | — | ✅ STEP_NOT_ALLOWED / LLM_UNAVAILABLE | ✅ zero tool calls | — | — | YES (AL-2 S4/S5, this fix adds real-RP negative) |

### The specific test that "looked like it tested the Agent Loop"

`test_goal_resolution_api_v1.py::TestLoopPathGoal::test_e1_goal_is_semantic_not_the_tool`
— posts the REAL Chinese phrasing "那这个账号有与其他账号做关联对冲交易的情况吗"
to the real `/goal` endpoint and awaits a terminal state, which sounds
end-to-end. But its fixture (`make_client`) wires the loop runner with:
a **scripted planner** (case_intake plan regardless of request), a
**ScriptedProvider fake ToolProvider** (canned SUCCESS ToolResult), a
**canned goal resolver** (hard-coded goal_payload — bypassing even the
real heuristic matcher), and **no ContextResolver at all**
(`AgentLoopRunner` without resolver → resolution entirely skipped). The
only assertion is `goal_representation.goal_type == "cross_account_check"`
— a DISPLAY field that the runtime attaches even to BLOCKED runs. The
test therefore verified "the goal label renders" while every layer that
failed in production (deterministic context routing, planner eligibility,
real tool) was stubbed, absent, or unasserted. In the real deterministic
environment this same request BLOCKS at iteration 0 — invisible to the
test.

### Formulated per the requested pattern

- AL-1/AL-2 **tested the loop's execution machinery with pre-resolved
  inputs**, but did **not test natural-language semantic routing**;
  therefore a routing/vocabulary defect that kills the request BEFORE
  the planner passed every suite.
- AL-2.5 **tested the real data chain over the one intent that bypasses
  semantic routing** (ARTIFACT_CASE); therefore the real-RP gate could
  not catch a routing defect by construction.
- goal-resolution tests **tested recognition, not consequence** (and via
  injected representations); therefore "understood but rejected" shipped
  unnoticed.
- The vocabulary defect itself (three divergent keyword sets) was
  invisible because every unit test pinned its OWN layer's list in
  isolation; no test ever sent one real user sentence through all three
  layers.

### Why earlier manual QA missed it

Browser journeys before the manual test (qa/journey_*.py) drove the
older pre-V2 conversation path and, later, the AL-2.5 golden path
(artifact intent). No prior journey ever submitted a case-level
analytical natural-language goal to the bounded loop — the manual test
did, with the exact CJK phrasing real users use.

## 3b. Why AL-1 / AL-2 / AL-2.5 did not catch this defect

One sentence per work item, with the exact runtime boundary each one
actually crossed:

| Work item | Boundaries it crossed | Boundary it did NOT cross | Consequence |
|---|---|---|---|
| AL-1 | /goal HTTP → loop → planner(**scripted**) → executor(**scripted provider**) → persistence | raw NL → deterministic Stage-1 routing → real planner selection | loop mechanics proven with pre-resolved inputs; a pre-planner BLOCK is invisible |
| AL-2 | /goal HTTP → loop(**scripted resolver LLM + scripted planner**) → real executor → real tools (RP patched) | CJK/case-level NL intents; goal layer; skill eligibility without focus | orchestration proven for one phrasing whose finding resolves by English title tokens |
| AL-2.5 | REAL browser → REAL API → REAL loop (default factory) → REAL RP | any semantic routing at all (ARTIFACT_CASE is target-independent by design) | real-data chain proven; structurally blind to routing defects |
| goal-resolution tests | recognition → display projection | recognition → routing consequence (injected goal payloads; display-field assertions) | "understood but blocked" shipped unnoticed |

The new tests added by this work item cross exactly the missing
boundary: **raw natural-language user goal → deterministic Stage-1
routing (shared concept) → planner selection (governed plan) → real
tool → real RP → persisted trajectory → browser**, plus negative
controls proving unsupported intents still terminate safely before
planning.

## 4. Design Decision

1. **Single source of truth for the concept** — new
   `app/investigation_concepts.py` owns the vocabulary:
   - `mentions_cross_account(text)` — scope element only; canonical
     delegation target for the planner's wording check
     (behavior-preserving superset of the former local regex).
   - `is_cross_account_trade_analysis_request(text)` — the GOVERNED
     ROUTING predicate: cross-account scope AND trade/hedging subject
     (对冲/對沖/hedge/hedging, 对倒/對倒, 对向, 交易/trade/transaction).
     Requiring BOTH elements is the negative-control boundary: accounts
     named without the trade/hedging subject (e.g. "比较不同账户的提现
     频率", "有哪些关联账户") are NOT routed to the analysis.
2. **Why the context route consumes the shared matcher, not
   goal_representation**: goal understanding runs before resolution and
   its output is a display projection; making routing consume it would
   require re-plumbing resolve()'s call order/signature for /turn and
   /goal alike. The minimal connection with identical correctness is
   vocabulary unification: goal recognition now leads with the SAME
   governed predicate (goal_resolution.py consumes the shared matcher;
   its remaining table stays a recognition SUPERSET — display-only),
   and both routing layers consume it for DECISIONS. One concept, one
   definition; goal_representation remains display-only by design.
3. **Case-level analytical skill** — new `case_trade_analysis` skill
   (`required_capabilities=[]`, planning_steps
   `analyze_cross_account_trades` + `generate_artifact`): the SAME
   governed analysis step as trade_investigation's primary step, scoped
   to the whole case. trade_investigation's finding-level contract is
   UNCHANGED. No blanket "focus-less requests proceed" relaxation —
   only the shared-predicate intent routes here.
4. **Deterministic planner branch** (`1a-1`, mirroring the existing
   deterministic intake/artifact branches): shared-matcher intent + an
   eligible skill → plan directly from STEP_TOOL_MAP bindings → same
   `check_plan` gate as every other path → no LLM round-trip. The
   `generate_artifact` step is required for bounded convergence: an
   honest INSUFFICIENT_DATA analysis (`evidence_missing=true`) must
   never satisfy question-answered criteria, and without a terminal
   product step the deterministic loop would re-plan to its iteration
   bound; the bundle carries the analysis AND its limitations to the
   user.
5. **Completion semantics precision** (`AgentLoopState.criteria_satisfied`):
   a success observation whose SOURCE reports a data gap still does not
   answer the goal — EXCEPT the produced bundle (semantic_status
   "artifact"): the artifact IS the run's product; its content honestly
   carries the gap (visible in the trajectory and the composed
   response). Observation records remain fully honest (the gap and
   next_data_needed are still stamped; the observation whitelist now
   also carries `accounts_analyzed` — the "which accounts were checked"
   answer).
6. **BLOCK/CLARIFY boundary unchanged**: genuinely ambiguous finding
   selection still follows the existing clarification contract
   (AMBIGUOUS → ledger, flag-gated; /turn clarification surface);
   UNRESOLVED still terminates BLOCKED. Nothing about
   ClarificationLedger was rewritten. Safe-Select and candidate
   metadata systems untouched (regression-verified).

## 5. Implementation (changed files)

| File | Change |
|---|---|
| `app/investigation_concepts.py` | NEW — shared concept matchers (single source) |
| `app/context_resolution.py` | case-level branch extended: case known + shared-matcher intent → `UNCHANGED_CASE_LEVEL` (same outcome as other case-level requests) |
| `app/skills.py` | new `case_trade_analysis` skill (case-level; governed steps) |
| `app/planner_v2.py` | deterministic `1a-1` branch (shared matcher → governed plan → check_plan); `_is_cross_account_trade_request` delegates to the shared matcher (behavior-preserving) |
| `app/goal_resolution.py` | `heuristic_goal` leads with the shared predicate for `cross_account_check` (superset recognition preserved below) |
| `app/agent_loop.py` | `criteria_satisfied` artifact-product exception; cross-account observation whitelist gains `accounts_analyzed` |

NOT changed: `ExecutorV2`/`STEP_TOOL_MAP`/`ToolProvider` (governed
mapping used as-is), `cross_account_trade_analysis` frozen evidence
contract, ClarificationLedger / Safe-Select / candidate metadata,
`trade_investigation` skill, all frontend code (the two-button UX issue
is recorded, not touched).

### Test-anchor updates (product-decision reflections, not bypasses)

- `tests/test_skill_registry_v2.py`: registry set now includes
  `case_trade_analysis`; case-level eligibility sets updated (comments
  cite AL-2.6).
- `tests/test_cross_account_trade_analysis_v1.py::test_cross_account_wins_over_self_opposite`:
  the deterministic branch now plans this intent directly (LLM script
  becomes an unreachable fallback); pinned step/tool/lock assertions
  unchanged, plan shape includes the bounded bundle step.

## 6. API E2E (real RP, raw goal, no mocks)

`backend/tests/test_al26_real_rp_case_level_e2e_v1.py` — real HTTP only;
imports no repo fixtures, applies no patches; the raw goal is sent
verbatim. **PASS ×2 (repeatable, ~6s each on the deterministic config).**

Verified from the PERSISTED trajectory (not just terminal status):
- `agent.completed` / `goal_satisfied`; NO `agent.blocked`; NO
  "unresolved" anywhere.
- `context.resolved` event present (case-level).
- `plan.created` event summary contains BOTH governed step types
  (planner really ran; snapshot-only assertions avoided since re-plans
  replace the snapshot).
- `cross_account_trade_analysis` AND `artifact_bundle` both
  tool.completed with outcome success.
- `observation.recorded` ≥ 2, first with step_type
  `analyze_cross_account_trades`, outcome success.
- Persisted bundle readable via `/tasks/{id}/artifacts` and the task
  detail (reopen surface): contains the real case id, the real analysis
  section ("Candidate Cross-Account Relationships") and its honest
  limitations ("This is not a no-match result" / bounded-retrieval
  caveat).
- Negative controls (same file): "比较不同账户的提现频率" and "tell me
  about the thing that does not exist" → terminal `blocked` /
  `context_unresolved`, empty plan_steps, iteration 0, ZERO tool events
  — the fix did not relax context safety.

Live evidence (U00010, deterministic config): analyzed the case's
transaction universe (260 transactions retrieved, cluster account
universe from live RP `network_evidence`), honest result
`analysis_status=INSUFFICIENT_DATA` (account_budget_exhausted), 0
candidates, bundle composed with the limitation statement — the
fallback half of the user's request ("告诉我你都查了哪些范围…") is
answered truthfully instead of being blocked.

## 7. Browser E2E (real stack, raw goal)

`qa/journey_al26_case_level_routing.py` — real headless Chrome,
`PASS ×2`. Screenshots `al26-01-real-findings.png`,
`al26-02-loop-terminal.png`, `al26-03-reload-restored.png`.

Verified in the real UI, from the raw Chinese goal: real RP findings
rendered (oracle title fetched live from RP); Run Loop → Activity shows
**Investigation completed (goal satisfied), Iteration 2 of 8, ✓ analyze
cross account trades ✓ generate artifact**, "Investigation context
resolved / focus=case-level" (previously "Investigation blocked / No
specific target could be resolved"), the governed capability in the
trace, ≥2 capability returns, result summaries, artifact ref visible;
52 API calls, zero network failures; reload + reopen restores
`Artifacts (2)` and the completed Activity history.

## 8. Negative Control

See §6 — at the REAL API level: unsupported/unresolvable requests still
terminate blocked-before-planning with zero tool invocations. Unit-level
negatives additionally pin the matcher itself
(`tests/test_al26_case_level_routing_v1.py`: withdrawal-comparison and
related-accounts phrasings rejected; single-account trade phrasing not
routed case-level; no-case identification stage does not route).

## 9. Regression Results

- Targeted suites re-run green: AL-1 (loop + resume), AL-2 (orchestration
  units + final E2E), goal resolution (unit + API), context resolution,
  planner, skill registry, cross-account analysis, clarification (WI-6),
  Safe-Select (WI-5 + e2e), admissibility gate, candidate metadata
  storage, AL-2.5 real-RP API E2E, loop-artifact persistence, no-key
  startup, eval scenarios, capability matrix, activity fixes.
- AL-2.5 browser journey re-run: PASS. AL-2.6 browser journey: PASS ×2.
- Full backend suite: see §14 addendum.

### Regression triage (first full-suite run surfaced 8 failures beyond
### the 12 legacy baseline — each dispositioned)

1. **Plan-shape anchors** (5 tests across `test_skill_registry_v2`,
   `test_cross_account_trade_analysis_v1`, `test_self_opposite_trades_view_v1`,
   `test_autonomous_eval_fixes_v1`, `test_eval_scenarios` E16): pinned the
   single-step cross-account plan / the 3-skill closed registry. The
   product decision (deterministic governed plan + bundle step; new
   case-level skill) changes exactly that shape. **Anchors updated with
   AL-2.6 comments** — the pinned properties (registry lock, tool
   binding, primary step, eligibility gates for finding-level skills)
   are unchanged and still asserted.
2. **`test_target_resolution_stage1_v1`** (2 tests): pinned the OLD
   precedence where Stage-1 LLM assist assigned a finding focus to
   "find cross-account opposite trades" — that focus-assignment was the
   old workaround to reach the analysis; the case-level route now
   precedes it (correct: the tool needs only case_id). Anchors updated;
   the LLM-inference and prompt-fencing contracts are re-asserted on
   phrasings that still reach Stage-1 assist.
3. **`test_unresolved_reference_stops_before_planning`** (flaky in full
   runs only): PRE-EXISTING environment sensitivity, mechanism-verified —
   the service under test uses the default `ContextResolver
   (llm_assist=True)` and the test process inherits the real LLM key
   from `backend/.env`, so Stage-1 performs REAL z.ai calls whose
   verdict (AMBIGUOUS vs UNRESOLVED) depends on the external LLM's
   current state. Passes deterministically with the key absent
   (verified). Not caused by this change; recorded as a test-isolation
   defect for a future item.
4. **`test_target_refinement_llm_v1`** (3 stability tests): require a
   LIVE LLM by design (3 real calls must agree). Stash-verified to fail
   IDENTICALLY without the AL-2.6 diff under the no-key environment;
   outcome tracks z.ai liveness (stable-up → pass; down → pass;
   semi-alive → flaky). Pre-existing environment dependence, not a
   regression.

Safety-property regressions: NONE. The negative controls (real-RP and
unit level) prove unsupported requests still terminate before planning
with zero tool calls; Safe-Select/M2/clarification suites are green.

## 10. Runtime Trace (after fix, real stack)

```
raw goal "查一下有没有不同账户之间的对冲交易情况…"
→ POST /goal → AgentLoopRunner.run (state persisted, agent.started)
→ goal understanding (heuristic, shared matcher) → cross_account_check
→ findings_provider() → real RP evidence (200)
→ ContextResolver.resolve → UNCHANGED_CASE_LEVEL  (was: UNRESOLVED)
→ eligible skills: case_intake + case_trade_analysis
→ PlannerV2._plan_inner 1a-1 deterministic branch
   → plan [analyze_cross_account_trades, generate_artifact] (check_plan ✓)
→ ExecutorV2 → cross_account_trade_analysis (registry-locked)
   → real RP HTTP (evidence/explain/transactions) → ToolResult success
   → observation (honest INSUFFICIENT_DATA + accounts_analyzed)
→ heuristic CONTINUE → artifact_bundle → success → ART-*
   → criteria_satisfied (artifact product) → STOP goal_satisfied
→ persisted task/agent_state/session artifact
→ UI: Activity completed + artifact ref; reload/reopen restores both
```

## 11. Known Limitations

- The gate configuration remains no-LLM (deterministic fallbacks); with
  an LLM configured the same shared matcher routes identically and the
  LLM planner is simply not needed for this intent.
- U00010's honest analysis result is INSUFFICIENT_DATA (RP-side
  retrieval budget truncation, frozen tool contract) — the user-visible
  answer is the truthful "no candidates reported; retrieval bounded;
  here is what was analyzed", not a fabricated no-match. Improving RP
  retrieval completeness is an RP-side concern, out of scope.
- The observation whitelist now carries `accounts_analyzed`, but the
  ACTIVITY event text carries only the display phrase (payload bodies
  are never projected); the enumerated account scope is auditable in
  the persisted agent-state digest and reflected in the composed
  response path.
- Related-accounts / network-structure questions ("有哪些关联账户")
  remain an UNRESOLVED-class intent (no drilldown tool exists); the
  capability-guidance text already says so. Not changed here.
- See the separate product issue: two submit buttons (Send vs Run Loop)
  still require the user to choose a runtime — recorded, not in scope.

## 12. Definition of Done

| Requirement | Status |
|---|---|
| Root cause located as code-level routing/contract defect | ✓ (§2) |
| Minimal fix completed | ✓ (§5) |
| Unified matcher / canonical representation | ✓ (`investigation_concepts.py`, consumed by all three layers) |
| Case-level routing triggered by real natural language | ✓ (API + Browser E2E, raw CJK goal) |
| PlannerV2 really participated | ✓ (plan.created event w/ governed steps) |
| Governed tool mapping really participated | ✓ (STEP_TOOL_MAP bindings, registry lock asserted) |
| ExecutorV2 really participated | ✓ (default executor, no test provider) |
| Real RP API really participated | ✓ (backend logs, live data in bundle) |
| API E2E from raw goal | ✓ ×2 |
| Browser E2E from raw goal | ✓ ×2 |
| Persisted trajectory proves Context→Planning→Tool→Observation | ✓ (§6/§7) |
| Negative control | ✓ (real-RP blocked ×2 scenarios + matcher units) |
| Existing regression accounted for | ✓ (§9; full suite §15) |
| No Safe-Select regression | ✓ (WI-5/WI-6 suites green) |
| No M2 regression | ✓ (candidate metadata suites green) |
| No UI scope creep | ✓ (frontend untouched) |
| QA artifact | this document |

## 13. Final Status

**PASS WITH LIMITATIONS** — the false-negative BLOCK is fixed and
proven over the real stack from the raw user goal, with real planner,
governed mapping, real RP data, real browser journey, negative controls,
and no governance regression. Limitations: honest INSUFFICIENT_DATA on
U00010 (RP retrieval budget), display-layer recognition superset vs
routing predicate (documented), and the unchanged two-button product
issue.

## 14. Addendum — Full backend suite after AL-2.6

Final accounting (default env, RP up): **1219 passed, 14 failed,
1 skipped, 1 xfailed** (12m15s).

- 13 failures = the documented pre-existing legacy baseline
  (`test_error_handling` ×3 — the two flaky members of
  `TestAgentExecutionErrors` rotate between runs; `test_evidence_integration`
  ×3; `test_provenance_model` ×6 — all targeting the legacy pre-V2
  `app.agent.InvestigationAgent` and the provenance composer).
- 1 failure = `test_target_refinement_llm_v1::test_refinement_stability[E5]`
  — verified z.ai-liveness-dependent (3 identical real-LLM calls must
  agree; semi-alive endpoint → mixed verdicts). Stash-verified to fail
  identically without the AL-2.6 diff; passes when the endpoint is
  stably up or fully down. Pre-existing environment dependence, not a
  regression.
- **Zero AL-2.6-caused failures.** All 8 first-run failures beyond the
  baseline were dispositioned (§9): 7 legitimate product-decision anchor
  updates, 1 pre-existing environment sensitivity.
- The z.ai-state-dependent tests are recorded as a test-isolation debt
  (real external LLM calls inside unit tests via `backend/.env`); fixing
  that is out of AL-2.6 scope.

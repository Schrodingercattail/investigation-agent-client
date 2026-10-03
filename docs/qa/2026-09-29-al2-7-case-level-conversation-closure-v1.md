# AL-2.7 — Case-Level Natural Language + Conversational Closure

**Date**: 2026-09-29
**Final status**: **PASS WITH LIMITATIONS** (§15) — pending the manual
browser acceptance smoke (§13)

---

## 1. Defect A — Case-level natural language routing

In an investigation conversation already bound to case U00010, the user
request "展开一下00010案例情况" failed with `unresolved_reference`
("No specific target could be resolved from this request.") while
"调查00010" succeeded. The only CJK case-level trigger in
`_CASE_LEVEL_MARKERS` was 「调查」— the same case-level intent from the
same bound case succeeded or failed purely on phrasing.

Confirmed by the read-only audit (persistence + in-process
reproduction): the create-time parser `resolve_case_reference` resolves
ALL natural forms (00010 / 案例 00010 / 展开一下 00010 / …) with NO
intent-word dependency — but Context Resolution never re-recognizes the
case entity in-conversation, and its case-level gate was a fixed
intent-word list.

## 2. Defect B — Agent Loop completed without a conversation response

A `/goal` run completed (Activity: context → plan → tools → observation
→ terminal) but the conversation area showed no assistant reply. The
composed response (`AgentLoopResult.response`) was persisted only in
agent_states/Activity; the `/goal` terminal path never wrote the
conversation read model (`session.turn_responses`), and the live UI
never appended an assistant turn. "Agent execution success ≠
conversation completion" — by construction, for EVERY loop run.

## 3. Previous Test Coverage

What the previous E2Es' oracles actually were:

- AL-2.5/2.6 Browser journeys: asserted **terminal status**
  (`data-agent-status`), the **Activity panel trace** (items, plan text,
  capability rows), the **artifact reference**, and
  **artifacts-after-reopen count**. NO assertion ever read the
  conversation area; the loop runs never produced one.
- AL-2.6 API E2E: asserted terminal status, plan/tool events, and the
  artifact read model — the conversation response was not part of the
  contract (the backend did not produce one for loops).
- AL-2.6 journey construction: bound the case FIRST via
  `send_message("Investigate U00010")` (the one working intent-word
  phrasing), so first-message case identification was never exercised;
  the API E2E created investigations with explicit `case_id`,
  bypassing natural-language identification entirely.

## 4. Exact Coverage Gaps

Previously tested: A — runtime completion, Activity projection, artifact
read model, case-level CROSS-ACCOUNT routing (AL-2.6).
Not tested: B — (1) case-id ENTITY recognition inside a bound
conversation with non-intent phrasing; (2) the existence, content, and
persistence of an assistant CONVERSATION response for loop runs (live,
reload, reopen); (3) "user received an answer" as an acceptance oracle,
distinct from "run completed".

## 5. Implementation

| File | Change |
|---|---|
| `app/context_resolution.py` | Defect A: the case-level branch now also routes when the request explicitly mentions the CURRENT case id — via `_mentioned_case_ids` (the SAME shared entity matcher the create-time parser and the cross-case guard use; single source). Cross-case guard, focus precedence, vague-guard, Priority-5 fallback unchanged. |
| `app/planner_v2.py` | Defect A (planner parity): `_is_bound_case_reference` — a bound conversation's request naming the current case (no focus) plans the SAME deterministic Case Intake as its 调查/investigate phrasing twin, so routing and planning cannot disagree. |
| `app/api/investigations.py` | Defect B (backend): `_record_loop_result` — the goal-run/resume terminal path now mirrors the composed response into `session.turn_responses` (the conversation read model the /turn path uses) alongside the artifacts. Response is always the formal composition — never fabricated. |
| `frontend/src/pages/InvestigationPage.tsx` | Defect B (live UI): when a session-started loop run reaches terminal, the page fetches the task detail once and appends the persisted assistant response as a real conversation turn + merges the run's artifacts into the live panel (dedup). Guards: the terminal snapshot must belong to the task this page started (a stale snapshot from the PREVIOUS task must never mark absorption — observed and fixed during this work item), and restored/reopened runs are rendered by the existing restore path (which now serves the same persisted response). |

NOT changed: Safe-Select, candidate metadata, ClarificationLedger
semantics, `trade_investigation` contract, `cross_account_trade_analysis`
frozen contract, RP, the two-button UI, the New-Investigation restore
semantics (both recorded as open issues).

**Bug found and fixed during implementation** (test-then-verify caught
it): the first absorption effect keyed absorption to the page's
`activityTaskId` while the terminal snapshot could still belong to the
PREVIOUS task (stale window between `setActivityTaskId` and the hook's
first reset) — the new task was marked "absorbed" by the old snapshot
and the real terminal was silently skipped. Instrumented browser
debugging (console probe) identified it; the fix requires the snapshot's
`task_id` to equal the page task and absorbs only session-started runs.

## 6. Case-Level Intent Matrix (unit, `test_al27_case_level_nl_v1.py`)

| Input (bound U00010 conversation) | Result |
|---|---|
| 调查00010 | case-level ✓ (unchanged) |
| 展开一下00010案例情况 | case-level ✓ (was unresolved — the defect) |
| 看看00010怎么回事 | case-level ✓ |
| 详细说说00010 | case-level ✓ |
| 00010有哪些发现 | case-level ✓ |
| 展开一下 00010 / 分析一下00010这个案例 | case-level ✓ |
| 展开一下这个案例 (no id) | unresolved (existing contract; vocabulary not expanded this item) |
| 为什么会这样 (vague) | unresolved ✓ (existing safe terminal) |
| 调查00999 (different case) | cross-case guidance ✓ (never fabricated) |
| focused F6 + 展开一下00010案例情况 | focus kept ✓ (Priority 1/2 precedence unchanged) |

## 7. API E2E (`test_al27_conversation_closure_e2e_v1.py`, real RP, no mocks)

Four SEPARATE oracles — runtime completion / activity projection /
conversation response / persistence — asserted per scenario. **PASS ×2**
(repeatable, ~4s).

- Scenario A: bound U00010 + "展开一下00010案例情况" via /turn →
  completed (NOT unresolved_reference); response body carries the intake
  answer; execution risk_case_fetch success; persisted plan + tool calls;
  `task_response_text` served with case correspondence.
- Scenario B: raw cross-account goal via /goal → completed/goal_satisfied;
  activity events {context.resolved, plan.created, tool.called,
  tool.completed, observation.recorded}; `task_response_text` non-empty,
  containing the formal composition ("completed" +
  `analyze_cross_account_trades`), ≠ the raw goal; artifact + response
  both served by the read models.
- Scenario C (negative): "tell me about the thing that does not exist"
  → failed/unresolved_reference, zero tool calls; "比较不同账户的提现频率"
  via /goal → blocked/context_unresolved, empty plan, zero tool events.

## 8. Browser E2E (`qa/journey_al27_conversation_closure.py`, real Chrome)

**PASS ×2.** Real journey: bind U00010 (real phrasing) → Scenario A via
the normal Send button → a NEW agent turn renders live (agent cards 1→2),
no "No specific target could be resolved" anywhere; Scenario B via the
bounded-loop button → Activity shows the full chain (context resolved →
plan → cross account trade analysis → observations → terminal) AND a NEW
assistant turn renders LIVE (agent cards 2→3) containing the formal
composition marker ("Steps completed …"); 50+ API calls, zero network
failures. Screenshots: `al27-01-case-level-reply.png`,
`al27-02-loop-terminal.png`, `al27-03-loop-reply-live.png`,
`al27-04-reload-retained.png`.

## 9. Conversation Completion Evidence

- Execution completion ≠ Conversation completion: asserted separately
  (terminal status vs new agent turn in the chat panel).
- The response text originates from `compose_loop_response` via
  `AgentLoopResult.response` (backend mirror) — the browser never
  synthesizes it.
- API E2E Scenario B pins: response ≠ raw goal, contains the formal
  composition markers.

## 10. Reload / Reopen Evidence

After reload + reopen: the response persists (conversation restore
serves `task_response_text`), the artifacts persist (`Artifacts (N)`),
the Activity history restores read-only terminal-completed. Asserted in
the browser journey (§8) and by API read models (§7 Oracle 4).

## 11. Negative Controls

See §7 Scenario C + §6 matrix rows (vague / different-case / no-id).
The AL-2.6 negative control (accounts-without-trade-subject still
blocked before planning) re-verified end-to-end.

## 12. Regression

- Targeted suites green: AL-1 (loop/resume), AL-2 (units + final E2E),
  AL-2.5/2.6 real-RP API E2Es, AL-2.5/2.6 browser journeys (re-run
  PASS), context/goal/planner/registry/cross-account/stage1 units,
  Safe-Select (WI-5/WI-6), candidate metadata, clarification,
  loop-artifact persistence, no-key startup.
- Frontend: vitest 20/20; `tsc --noEmit` clean.
- Full backend suite: §16 addendum.

## 13. Manual Browser Acceptance (pending — handoff)

Automated acceptance is complete; per this work item's gate, final
acceptance requires the manual smoke:

1. Open the client (localhost:3001), open/bind a U00010 conversation,
   send "展开一下00010案例情况" → expect a real assistant reply in the
   conversation (findings/bundle), not "No specific target…".
2. Send the raw cross-account goal → Run Loop → watch Activity run
   context → plan → tool → observation → terminal; when it completes, an
   assistant reply appears in the conversation WITHOUT reload.
3. Refresh the page → reopen the investigation → the reply is still in
   the conversation; Activity history restored.

## 14. Known Limitations

- Case-level vocabulary WITHOUT a case id ("展开一下这个案例") still
  follows the existing unresolved contract — the entity (case id) is the
  recognized trigger this item; broader CJK case-level vocabulary is
  deliberately not expanded.
- A request mentioning the current case id routes case-level even when
  other vocabulary is present (same precedence class as the existing
  `_looks_case_level` branch; focused conversations keep Priority 1/2).
- The loop assistant response is the formal bounded composition
  (canonical phrase + step recap + observation lines) — improving its
  narrative quality is a composer/evaluation concern (AL-3).
- Pre-existing z.ai-state-dependent tests (see AL-2.6 §9) unchanged.

## 15. Definition of Done & Final Status

| Requirement | Status |
|---|---|
| case-level NL no longer depends on one CJK marker | ✓ (§6 matrix) |
| "展开一下00010案例情况" handled in bound conversation | ✓ (API + Browser) |
| unsupported requests remain bounded | ✓ (Scenario C + matrix) |
| normal /turn intact | ✓ (regression suites; Scenario A IS a /turn) |
| successful /goal produces assistant response | ✓ (backend mirror) |
| response appears live without reload | ✓ (browser journey, agent cards 2→3) |
| response persists after reload / reopen | ✓ (journey persistence phase) |
| response from actual formal mechanism | ✓ (compose_loop_response via turn_responses; ≠ raw goal asserted) |
| Activity AND Conversation both populated | ✓ (asserted separately) |
| API E2E from raw user input | ✓ |
| Browser E2E from raw user input | ✓ |
| negative control | ✓ |
| real RP path intact | ✓ (no mocks; real HTTP throughout) |
| no Safe-Select / M2 regression | ✓ |
| no RP modification | ✓ |
| manual Browser smoke | PENDING (§13 handoff) |
| QA artifact | this document |

**Final status: PASS WITH LIMITATIONS** — both defects fixed and proven
over the real stack with separated oracles; final acceptance completes
after the manual smoke above.

## 16. Addendum — Full backend suite

Final accounting (default env, RP up): **1231 passed, 15 failed,
1 skipped, 1 xfailed** (12m32s).

- 13 failures = the documented pre-existing legacy baseline
  (`test_error_handling` ×3, `test_evidence_integration` ×3,
  `test_provenance_model` ×6 — legacy pre-V2 agent + provenance
  composer).
- 2 failures = the two verified z.ai-liveness/environment-dependent
  tests accounted for in AL-2.6 §9
  (`test_unresolved_reference_stops_before_planning` — performs real
  LLM calls via `backend/.env`; `test_refinement_stability` — requires 3
  identical live-LLM verdicts). Both pre-existing, mechanism-verified,
  pass under the no-key deterministic environment.
- **Zero AL-2.7-caused failures.** Frontend: vitest 20/20, tsc clean.

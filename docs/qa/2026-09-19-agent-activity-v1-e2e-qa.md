# QA Report — Agent Activity / Execution Trace V1 E2E

**Date**: 2026-09-19
**Phase**: Agent Activity V1 (implementation per
`docs/baselines/2026-09-19-agent-activity-v1.md`)
**Verdict**: **FAIL — P1 DEFECT** (primary advertised journey broken for
first-attempt users; two P2 state defects; all other core flows verified
working end-to-end with real Risk Platform data)
**Method**: real-browser E2E (headless Chrome 153 driven via CDP),
real backend + real Risk Platform + real LLM planner/decision proposer,
plus API-seam simulations for scenarios not safely forceable in
production. Product fixes are NOT part of this phase.

---

> ## FIX + RETEST (2026-09-19, defect-fix phase) — FIXED, READY FOR QA RE-RUN
>
> All P1/P2 defects and the confirmed P3/P4 items were fixed and
> re-verified (implementation record:
> `2026-09-19-agent-activity-v1.md` §22). Original findings below are
> preserved verbatim; this section records the retest evidence only.
>
> | Defect | Fix | Retest evidence |
> |---|---|---|
> | QA-01 (P1) | `GoalRunRequest.context_action` applied + persisted in `start_goal`; UI sends the pending focus with the goal | Live browser: goal POST body `{"goal": …, "context_action": {"type": "focus_finding", "finding_id": "F2"}}`; AgentLoopState `focus=F2`; planner plans the focused trade tool; activity trace shows `finding F2`. EN + CJK journeys green |
> | QA-02 (P2) | `_terminate` re-syncs the plan snapshot from the runtime Plan | Terminal runs render `✓ analyze cross account trades (completed)` (browser + API); pending stays pending; verified across completion/limit/blocked |
> | QA-03 (P2) | `activityTaskId` cleared on investigation switch; hook discards in-flight responses for superseded task ids (ref guard) | Switch to loop-less investigation → fallback card, zero stale trace (browser); rapid-switch + stale-callback hook tests green |
> | QA-04 (P2) | Strict findings provider on the goal path; loop resolves findings internally; dependency failure → distinct `blocked / dependency_unavailable` | Live: RP stopped → focused goal → `blocked / dependency_unavailable` with user-safe message (browser render verified); fault-injection E2E: retry→success and retry→blocked through the real API |
> | QA-05 (P3) | Observations carry analyst-facing `display` phrases | Component test + live trace |
> | QA-06 (P3) | Traditional CJK (跨賬戶/對倒) added to the routing hook | Routing unit tests; live CJK focused run green |
> | QA-07 (P4) | Conclusive NO_MATCH reports `conclusive_empty_result` | Live: API + browser show the precise reason |
> | QA-08 (P4) | "Starting…" replaces "Iteration 0 of 8" | Component test + live |
> | QA-09 (P4) | "Restoring investigation…" indicator during workspace restore | Browser observation |
>
> **Retest results**: backend 56 loop/activity/fix tests green; frontend
> 19/19 + tsc clean; full-suite failures identical to the standing
> environment baseline (the suite is RP-availability-sensitive in its
> legacy/live-RP portions: 43 failures with RP down, 12 with RP up —
> both pre-existing, none new; the swap behavior was reproduced and
> documented). Browser journeys: focused EN/CJK, case-level,
> stale-switch, RP-down blocked, reload reconstruction — all green.
>
> **Residual (deferred, unchanged)**: resume execution, MCP, arch-sync;
> Layer-B proposer variance is bounded by the deterministic policy.
>
> **Status: FIXED — READY FOR QA RE-RUN** (independent re-run decides
> the final gate).
>
> ### RP 恢复后的补充重测 (2026-09-19, RP restored — supersedes the
> ### RP-down-window results above where they overlapped)
>
> 修复阶段的部分回归最初在 RP 宕机窗口执行。RP 恢复后已全部重跑:
>
> - **受 RP 宕机影响的测试套件**: 全量后端套件以 RP 正常状态重跑 →
>   **12 个失败**(全部为 legacy pre-V2 套件语义失败), 对照 43 个
>   RP-宕机基线: 32 个失败消失(确认为 RP 依赖项: provenance / telemetry
>   / MCP / live-E2E 需要 :8000 实时服务), **0 个新增产品失败**。唯一
>   与 43-基线的差异(`test_error_handling.py` 内一个测试互换)已验证为
>   legacy 套件的 RP 可用性敏感 flake: RP 宕机时该套件精确复现原 3 个
>   失败, RP 正常时表现为不同的 flaky 子集 — 与本次修复无关。
> - **修复回归套件**: 56/56 通过(含 QA-01 英文/中文焦点、QA-02 终态
>   计划一致性、QA-04 阻塞/重试/持久失败、QA-05/06/07 回归)。
> - **其余受影响套件**(eval 场景 / registry / planner / executor /
>   API / service / E2E): **198 通过, 1 xfail**(已知 P09)。
> - **真实浏览器核心旅程 (RP 正常)**: 新建调查 → Intake → 选择 F2 →
>   Run loop → 网络 Goal POST 请求体携带
>   `context_action {"finding_id": "F2"}` → Activity 显示
>   `finding F2` → 真实跨账户工具执行(42 条 RP 交易, 完整检索,
>   NO_MATCH 结论性) → 终态 `completed / conclusive_empty_result` →
>   计划 `✓ analyze cross account trades (completed)` → 10 条活动
>   记录连贯。全链路在 RP 正常状态下再次确认修复有效。
>
> **Status (RP restored): FIXED — READY FOR QA RE-RUN**(结论不变; 独立
> QA 重跑负责最终门禁)。

---

## 1. Test environment

| Component | Value |
|---|---|
| Browser | Headless Chrome 153.0.8010.48 (`--headless=new`, 1500×950), driven via CDP (`qa/browser.py`, websocket-client — QA-only harness) |
| Frontend | Vite dev server, `http://localhost:3001/` (proxy `/api` → :8001) |
| Agent API | `uvicorn app.main:app --port 8001` (client `.venv`) |
| Risk Platform | `uvicorn app.main:app --port 8000` (risk-platform-demo, real Postgres demo data: U00299 HIGH/72.12, U00010, U00033, …) |
| Agent Loop decisions | Real LLM proposer (configured Anthropic-compatible gateway) + deterministic policy |
| Persistence | `backend/data/investigation_agent_v2.db` (tasks, investigations, sessions, agent_states) — 114 investigations at QA start |

## 2. Runtime topology (verified live)

```
Browser :3001  →(CDP-verified rendered DOM + network)
Frontend Vite  →  proxy /api
Agent API :8001 →  POST /goal (thread) · GET /activity
Agent Loop      →  ContextResolver → PlannerV2 (real LLM) →
                   ExecutorV2 → domain tools
Risk Platform :8000 (real; stoppable for outage scenarios)
agent_states / tasks_v2 / sessions (SQLite, same store)
```

## 3. Startup status

- Risk Platform: was DOWN at QA start; started successfully (env action).
- Agent backend: a STALE process was bound to :8001 serving pre-Activity
  code (`/activity` → route-not-found). Restarted with current code
  (env action). QA note: a stale long-lived dev process silently
  negates new API surfaces — worth a startup-version marker.
- Frontend: started cleanly (vite 5.4.21, 144 ms).
- QA harness: built `qa/browser.py` (CDP) + `websocket-client` installed
  into the backend venv (test-only dependency).

## 4. Browser accessibility

**PASS.** `http://localhost:3001/` renders the full Investigation
Workspace: history list (113 investigations), findings panel, tabs
Context / Artifacts / **Activity**, input + send, zero console errors,
zero failed network requests (verified across all sessions).

## 5–7. Core user journeys (real UI, real backend, real LLM, real RP)

### J1 — Intake + focus + Run loop (FIRST ATTEMPT) → **FAIL (QA-01)**

Steps: New Investigation → "Investigate U00299" → findings listed
(ML Pattern Detection, Coordinated Trading Pattern, …) → select F2 →
type cross-account goal → Run loop.

- Intake works; findings render (screenshot 02).
- Run loop starts, Activity tab auto-opens, live records appear
  (screenshot 03).
- **Run FAILS**: `planning failure` at `Iteration 0 of 8`; trace shows
  `focus=case-level` although the UI shows F2 selected. The goal POST
  (`{goal}` only) drops the pending `context_action` selection.
- Reproduced twice (English goal + CJK goal).
- UI presentation of the failure itself is coherent (failed status +
  "could not be planned" reason), but the journey is broken for every
  first-attempt user.

### J2 — Workaround journey (focused turn first, then Run loop) → **PASS (with QA-02/05)**

Steps: intake → select F2 → normal turn "show the timeline for this
finding" (persists focus) → Run loop "find cross-account opposite
trades…".

- Loop runs focused (`focus=F2` in trace), plans
  `analyze_cross_account_trades`, executes the REAL tool against real
  RP: 42 transactions retrieved, complete retrieval, 0 candidates →
  `NO_MATCH` → decision STOP ("Retrieval was complete and untruncated…
  conclusively answers that no cross-account opposite trades exist for
  this finding") → `completed / goal_satisfied`. Iteration 1.
- Full timeline coherent: started → context → plan → step → tool →
  observation → decision → step completed → terminal (screenshot 09).
- Defects observed here: stale plan snapshot (QA-02), semi-technical
  observation summary line (QA-05).

### J3 — Reload reconstruction → **PASS (with QA-09)**

Fresh browser → reopen investigation → Activity tab renders the full
persisted trace (goal, focus F2, plan, all rows) with exactly ONE
activity fetch; **0 further calls over 5s idle** — terminal polling
stop verified. Mid-run reload: polling resumed after reload (8 fetches
≈ 2s cadence over ~16s) and the run state stayed coherent; the Activity
panel itself only appears after the workspace finishes restoring all
task details (QA-09; QA probes raced this repeatedly).

### J4 — Self-opposite loop → **PASS (with QA-05)**

"show self-opposite trading for this finding" → routed to
`finding_drilldown` (NOT cross_account_trade_analysis) → real RP
aggregate `ratio=0.4524` + 42 concrete records → STOP → completed.
Decision reason uses same-account language ("self-opposite
(same-account, opposite-side) trading"); no cross-account terminology.

### J5 — Rapid interactions → **PASS**

Double-click Run loop: second click `DISABLED` while running (goal
phase latch). Single panel, single run. (Screenshot 13.)

### J6 — Pre-loop task → **PARTIAL (masked by QA-03)**

- API: activity endpoint returns 404 `NO_AGENT_ACTIVITY` for
  turn-local tasks (verified).
- UI: on a pristine pre-loop investigation the fallback CANNOT be
  observed because of QA-03 (the previous investigation's trace stays
  mounted). One clean UI verification is blocked by the leak bug; lower-
  level coverage exists (API contract test).

### J7 — RP outage → **PASS as bounded failure; reveals QA-04**

RP stopped → loop goal on focused investigation → **planning failure at
iteration 0** (finding capabilities require live RP; tool-level retry is
unreachable). RP restarted → identical goal → completed successfully.
Real tool-level retry during an RP outage is therefore NOT verifiable
end-to-end (architectural consequence, QA-04); retry remains verified by
Layer A tests only.

### J8 — STOPPED_BY_LIMIT → **PASS (API-seam simulation)**

Deterministic runner (always-CONTINUE proposer) through the real API:
8 iterations, `stopped_by_limit / iteration_bound_reached`, never
completed, decision trail coherent. Not forceable deterministically via
the real LLM proposer; runtime bound itself is system-enforced.

### J9 — CJK goal → **FAIL (QA-01) + QA-06**

"檢查這個發現是否有跨賬戶對向交易候選關係" → same planning failure at
case level (QA-01). Additional i18n gap: the deterministic cross-account
routing regex matches simplified 跨账户 only, not traditional 跨賬戶.

## 8. State / synchronization results

| Scenario | Result |
|---|---|
| Open Activity immediately after start | PASS (panel visible immediately; "Working…" indicator) |
| Reload while running | PASS (polling resumes; state coherent) |
| Reload at terminal | PASS (single fetch, then stop) |
| Open/close Activity tab repeatedly | PASS |
| Switch investigation after viewing a loop run | **FAIL — QA-03 leak** |
| Rapid second Run click | PASS (disabled) |
| Terminal state without terminal record / vice versa | PASS (ordering contract verified via API poll race test) |

## 9. Polling results

- Cadence: ~2s during active runs (8 fetches ≈ 16s observed); stops at
  terminal (0 calls/5s idle after completion — measured twice).
- 404 handling: verified at hook level (`notFound` fallback; UI copy
  present).
- Transient errors: keep last snapshot (hook design; not fault-injected
  in browser — unverified at UI level).
- No request explosion observed in any session.

## 10. Data boundary results

- Activity payload for a real cross-account run: **1,300 bytes**;
  contains NO `risk_reason / price / quantity / is_new_address /
  address`; projection key-set is whitelist-tested.
- Browser DOM during activity rendering: no raw transaction rows, no
  `<pre>` payload dumps.
- No CoT/model-completion content anywhere (backend summary fields are
  ≤300-char user-safe strings; UI adds none).
- Adversarial secret-marker seeding inside RP data was NOT performed
  (would mutate shared demo data); structural whitelist coverage is the
  compensating evidence.

## 11. Agent Loop behavior (real runs observed)

- MATCH_FOUND does not auto-stop: verified in Layer A and by the real
  NO_MATCH/STOP distinction; a real MATCH_FOUND→CONTINUE chain was not
  observed with the real proposer in-browser (Layer A covers it
  deterministically) — noted as a verification gap, not a defect.
- NO_MATCH → STOP with scoped conclusive reasoning: observed live.
- INSUFFICIENT_DATA → alternative path: verified in Layer A; not
  reproduced in-browser (needs a crafted RP state) — unverified at UI
  level.
- Different observations → different plans: observed (timeline goal →
  inspect_timeline; cross goal → analyze_cross_account_trades; CJK/
  unfocused → bounded planning failure).
- Iteration bound + garbage proposals: verified via seam (§J8) and
  Layer A.

## 12. Persistence results

`agent_states` documents verified via the activity API after real runs:
sequence-coherent records, terminal row present whenever terminal status
present, multiple runs isolated by task id, observations compact (not
exposed via API at all), no raw payloads persisted in activity records.

## 13–14. API/network + console findings

- All UI actions traced to expected API calls (goal POST → activity GET
  polling; turn POSTs; restore GETs). No unexpected endpoints.
- Zero browser console errors or exceptions in every session; only vite
  debug + React DevTools info messages.
- One backend-visible race fixed by design during earlier phases
  (terminal persist ordering) re-verified live: no terminal-status-
  without-terminal-record was observed.

## 15. Bugs by severity

| ID | Sev | Title | Boundary | Repro |
|---|---|---|---|---|
| QA-01 | **P1** | Run loop drops the pending finding selection → planning failure on the primary journey (UI shows focus, loop runs case-level) | frontend→API contract (goal payload lacks context_action) | always (2/2) |
| QA-02 | **P2** | Completed run's plan snapshot shows its executed step as "pending" | Agent state (snapshot sync only at loop top; terminal path never re-syncs) | always (2/2) |
| QA-03 | **P2** | Opening an investigation without loop runs keeps rendering the PREVIOUS investigation's Activity trace | frontend state lifecycle (activityTaskId never cleared on investigation switch) | always (verified on pristine INV-16fcc4215d19) |
| QA-04 | **P2** | RP outage → loop planning failure at iteration 0 (capabilities need live RP); tool-level retry/blocked unreachable end-to-end | runtime dependency boundary (planner input needs RP) | always while RP down (both directions verified) |
| QA-05 | P3 | Observation timeline rows are semi-technical ("finding_drilldown:success … record_count=42 ratio=0.4524") | activity projection reuses LLM-facing digest | always |
| QA-06 | P3 | Deterministic cross-account routing lacks traditional CJK (跨賬戶); only simplified 跨账户 matched | planner regex coverage | always (code + masked runtime) |
| QA-07 | P4 | "Iteration 0 of 8" shown before first step | frontend/copy | always |
| QA-08 | P4 | Conclusive NO_MATCH runs report stop reason "goal satisfied" (conclusive_empty never reached) | policy/criteria precision | always in observed runs |
| QA-09 | P4 | Reopen takes seconds with no loading indicator on the Activity tab (empty flash) | frontend restore UX | frequent |
| QA-10 | P4 | Pre-loop UI fallback wording unverifiable in-browser until QA-03 fixed | masked by QA-03 | n/a |

## 16. Boundary findings (see boundary document)

Recurring boundary: **new API surfaces must declare which pending UI
interaction state they honor** (QA-01), and **component state must be
scoped/reset per investigation** (QA-03). Full document:
`docs/baselines/2026-09-19-agent-qa-boundaries-v1.md`.

## 17. Known pre-existing failures

Backend suite: 43 failures — byte-identical to the standing pre-phase
baseline (legacy pre-V2 suites + live-RP-dependent telemetry/MCP tests).
Not attributed to this feature.

## 18. Release / acceptance gate

**FAIL — P1/P2 DEFECTS.** Gate criteria met: browser works, loop works,
activity renders real runtime activity, reload reconstruction works,
terminal states correct, no leakage of raw payloads/CoT. Gate criteria
failed: the primary advertised journey (select finding → Run loop)
fails on first attempt (QA-01); cross-investigation state leak (QA-03).

## 19. Recommended next actions (fix phase)

1. QA-01: goal endpoint accepts `context_action` (or the loop resolves
   focus itself); UI sends the pending selection with the goal.
2. QA-03: clear `activityTaskId` on investigation switch; scope hook per
   investigation id.
3. QA-02: re-sync the plan snapshot into Agent State at terminal.
4. QA-05: user-facing observation phrases separate from the LLM digest.
5. QA-04: decide policy — cache/deliver finding capabilities for
   planning during RP outages, or document the dependency explicitly.
6. QA-06/07/08/09: regex coverage, iteration-0 copy, stop-reason
   precision, restore loading indicator.

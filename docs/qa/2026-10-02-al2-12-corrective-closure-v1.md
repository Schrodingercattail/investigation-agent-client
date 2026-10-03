# AL-2.12 — Corrective Closure: Conversation Isolation, Outcome Semantics, Answer Fidelity, Response Composition — QA v1

**Date**: 2026-10-02
**Status**: **PASS** (all four defects fixed and verified on the canonical
runtime with real browser E2E)
**Scope**: corrective closure only. No new capabilities; M2 foundation,
Safe-Select, ClarificationLedger, RP, tool contracts, case-level routing
all unchanged.

---

## 1. Issue A — New conversation showed the previous conversation's Activity

**Root cause (empirically traced, not assumed)**: the ownership chain is
sound server-side — activity is served per `task_id`
(`/tasks/{id}/activity`), tasks are session-scoped, and reopen restores
activity only from the opened investigation's own tasks. The defect was
in the FRONTEND binding: `newInvestigation()` reset conversation state,
pending focus, input, and the persisted investigation id — but **never
cleared `activityTaskId`**. The previous conversation's loop-task binding
survived into the new conversation, so the Activity tab kept polling and
rendering the OLD task (Goal "高频提现有哪些证据" / F3 / completed).

**Fix**: `newInvestigation()` now calls `setActivityTaskId(null)`
(`frontend/src/pages/InvestigationPage.tsx`). `useAgentActivity(null)`
performs no queries and renders nothing — a turn-only conversation
therefore shows an empty Activity tab, which is the honest state (turn
tasks carry no loop-activity projection).

**Verification (canonical browser, real stack)**: C1 = Investigate
U00010 → F3 question → Run loop → Activity shows the F3 goal. NEW
conversation C2 → "给我输出一下00010案例情况" → correct case-level
answer + C2's own artifact, and the Activity tab contains **no** C1 goal
(`高频提现有哪些证据` asserted absent). Reload + reopen C2: still
isolated. Screenshots al212-01..03. The same case id (U00010) did NOT
cause inheritance.

## 2. Issue B — EMPTY/unsupported treated as success; 6-iteration churn; "conclusive empty" mislabel

**Root cause (empirically reproduced before fixing)**: for
「共享设备为什么是风险项，给我提供一下所有与这个账号共享设备的明细」the
plan ran explain_signal (success — real graph facts) + inspect_evidence
(EMPTY — the graph finding has no record-level stream). The LLM proposer
then re-planned the IDENTICAL query twice (iterations 3–6) and the run
terminated `CONCLUSIVE_EMPTY_RESULT` — a label reserved (by contract)
for a supported query with a complete zero, which this is not: the
record-enumeration scope simply does not exist for a graph finding,
while real aggregate facts WERE retrieved.

**Fixes**:
1. `LoopPolicy` repeated-empty coercion: an EMPTY last observation that
   has already survived a re-plan (`plan_revisions ≥ 2`) coerces
   RE_PLAN/CONTINUE → STOP ("no further plan can produce records the
   Risk Platform does not provide"). The churn is bounded at one re-plan.
2. New `StopReason.BOUNDED_DATA`: when a run stops on an empty final
   observation BUT retrieved real data earlier, the honest terminal is
   "answered within the available data", not a conclusive zero.
3. Observation whitelist now carries the graph facts (cluster,
   detection type, member count, risk score, shared devices, related
   accounts) and rule fields (`trigger_values`, `rule_description`).
4. Composer: the finding answer states WHAT IS KNOWN (real graph
   attribution) and then the explicit data boundary: 「…没有记录级证据可
   枚举(检索返回零条记录);平台提供的是上方已列出的聚合/图级别事实。因此
   我不能把现有摘要描述为"全部明细"——这是当前调查数据的真实边界,而非
   遗漏。」 The words "conclusive empty" appear only for true zeros
   (supported query + complete scope + zero records + no other data).

**Verified live (canonical)**: the same request now terminates
`bounded_data` at iteration 4 (was 6 with the mislabel) with the response:
graph facts → data boundary → bounded status line. Composer unit tests
pin: boundary wording present, "conclusive empty" absent when data
exists, present (as 「确认为零条匹配记录」) only for true zeros.

## 3. Issue C — "all evidence" answered with 3 examples

**Root cause**: the loop composer rendered a 3-example digest
(`record_summaries` cap) plus a pointer to internal views — for a tool
result that IS the complete record set.

**Fixes**:
1. Observation whitelist now carries `record_rows` (structured, display
   cap 20 with explicit `rows_truncated` disclosure).
2. Composer renders the COMPLETE set as a Markdown table
   (`| # | 记录 ID | 时间 | 明细 |`) preceded by 「完整证据共 N 条」 and
   closed by 「以上为 Risk Platform 权威证据的全部 N 条记录。」 — the
   words 例如/证据视图/保存在 are asserted ABSENT.
3. The WHY now leads with real causal semantics: observed value vs
   threshold condition when RP provides triggers
   (「观测到 Withdrawal frequency (24h) = 7 — 规则条件为 … > 10」), and
   RP's own rule description otherwise (live RP rule entries carry only
   name/severity/description — the description IS the causal semantics).

**Verified live (canonical browser)**: 「高频提现为什么是风险项，把所有
高频提现证据发给我」→ WHY (RP description 「7 withdrawals in 24h exceeds
the normal pattern」) + all 7 records as a table (W000087…W000089 with
timestamps) + persistence after reload/reopen. Screenshot al212-05.
A >20-row set discloses the display bound explicitly (unit test).

## 4. Issue D — cross-account run had no conversational answer

**Root cause**: `compose_loop_response` only had a finding-level answer
path; a case-level analytical run fell through to the status phrase +
execution recap ("Steps completed: 2 … Artifacts produced: …").

**Fixes**:
1. New case-level answer composer: cross-account MATCH_FOUND → direct
   answer (「有。在当前检索范围内发现 6 个候选…」), matching criteria in
   user language, bounded-retrieval limitation, intent disclaimer
   (「不构成对操纵或交易意图的结论」), then the full candidate table from
   actual ToolResult values; NO_MATCH / INSUFFICIENT_DATA have their own
   honest wordings (the latter explicitly "不是'未发现'").
2. related_account_analysis runs answer conversationally as well.
3. Artifact title names the purpose: `Cross-Account Trade Evidence —
   U00010` / `Related Account Evidence — U00010` (was the generic
   "Investigation Evidence Bundle").
4. Response ownership: the ANSWER leads; the status phrase follows; the
   execution recap (Steps completed / observation digests / artifact ids)
   trails as secondary metadata.

**Verified live (canonical browser)**: scenario D shows the full
business answer with the 6-candidate table (CATA-001…006 with accounts,
sides, symbol, time gaps, values, transactions), criteria, limitation,
disclaimer, and the titled artifact — answer first, `artifact_bundle:
success` never leading. Reload/reopen retains answer + artifact
association. Screenshot al212-06/07.

## 5. Ownership boundaries (audited before coding)

| Boundary | Finding |
|---|---|
| conversation/task identity & Activity query | server-side isolation correct; defect was the frontend `activityTaskId` reset |
| goal satisfaction / loop decision | policy had no repeated-empty guard; conclusive-empty label overclaimed |
| ToolResult result-status semantics | observation whitelist lacked the fields the answer needs (trigger values, rule description, graph facts, record rows) |
| detail/completeness semantics | loop composer capped at 3 examples regardless of request scope |
| response composer | no case-level answer path; finding answer lacked why-semantics |
| artifact composer/title | generic title ignored the run's analytical purpose |
| frontend Conversation/Activity binding | `newInvestigation` leak (Issue A) |

## 6. Tests

- `backend/tests/test_al212_corrective_v1.py` (9): COMPLETE table + real
  WHY; 21-row bounded disclosure; empty+graph-facts boundary wording;
  conclusive-empty wording reserved for true zeros; cross-account
  MATCH/NO_MATCH/INSUFFICIENT wordings; repeated-empty policy coercion
  (+ first-empty still allowed one re-plan); BOUNDED_DATA exists.
- Regression: 175 passed across the AL-2.8…2.11 + capability +
  Safe-Select + semantic/clarification + persistence suites (0 failed).
- Isolation is enforced at the data-binding level by the browser
  scenario A (the API level was already task-scoped).

## 7. Canonical Browser E2E evidence

All on 3001 → 8001 → RP 8000, real LLM, real RP:

| Scenario | Evidence |
|---|---|
| A isolation | al212-01..03 — C2 free of C1 activity, reload/reopen clean |
| B F5 boundary | API trace (bounded_data, iter 4) + composer wording; loop response shows facts + boundary |
| C F3 complete | al212-05 — WHY + 7-record table + persistence |
| D cross-account | al212-06/07 — business answer + candidate table + titled artifact + persistence |

## 8. Known limitations

1. Evidence tables display at most 20 rows in the loop surface (65+ row
   sets disclose the bound; the conversation (turn) surface lists all).
2. `BOUNDED_DATA` is a loop-surface stop reason; the turn surface composes
   per-tool prose and does not emit loop stop reasons.
3. The repeated-empty coercion stops one re-plan earlier than before; a
   genuinely different second query still requires a new user turn.
4. Scenario C's threshold sentence relies on RP payload fields; live RP
   currently provides description-only rules, so the threshold clause is
   shown only when RP supplies it.

## 9. Final Status

**PASS** — all four user-visible defects fixed, verified on the canonical
runtime with real browser E2E, regression green, no architectural
boundary violated.

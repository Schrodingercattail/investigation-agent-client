# Manual-Acceptance Read-Only Evidence Audit — Finding Focus, Withdrawal Comparison, signal_explain Authority, Compound Composition

**Date**: 2026-10-09 (audit executed 20:57–21:4x CST)
**Type**: Strict read-only evidence audit. No source, test, config,
flag, runtime, or data modifications were made. The only file written
is this report. No investigation was re-run, no LLM agent run was
triggered, and the database was accessed strictly read-only
(`file:...?mode=ro`).
**Audited workload**: the user's manual-acceptance runs of 2026-10-09
12:01–12:43 UTC on case **U00020** (plus adjacent runs), preserved in
the canonical store `backend/data/investigation_agent_v2.db`.

---

## 1. Executive Summary

The audit traced four user-reported problems to concrete code and
preserved run evidence. Main conclusions, in risk order:

1. **Withdrawal destination comparison (Audit B) — CONFIRMED, highest
   severity.** The RP data and the adapter both carry the destination
   `address` per withdrawal (`adapters/risk_platform.py:840-841`), but
   the address is dropped at BOTH projection points — the observation
   rows (`agent_loop.py:273-277`, only record_id/timestamp/summary are
   projected) and the composer table (`agent_loop.py:2438-2446`, the
   明细 cell renders only the `summary` string). No unique-address
   grouping/counting exists anywhere. The run completed
   `goal_satisfied` because the GSP flag was OFF for the user's manual
   session (all U00020 tasks have `goal_contract_decision=None`), and
   the legacy completion rule is ANY-tool-success
   (`agent_loop.py:585-606`); the comparison deliverable was never a
   structured requirement.
2. **Finding/focus mismatch (Audit A) — CONFIRMED mechanism, two
   distinct sub-problems.** (a) With a user-selected focus, the
   resolver unconditionally keeps it
   (`context_resolution.py:746-766`, `_keep`: "Preserve the current
   explicit focus exactly as-is") and the goal-understanding prompt
   anchors the model to it (`goal_resolution.py:627-629`, "Resolved
   target: finding F3") — the preserved run shows the LLM explicitly
   re-interpreting the 高频交易 question as being about F3
   (TASK-82e43998cd32 representation reason). No layer cross-checks the
   request's target vocabulary against the focused finding's semantics.
   (b) Without a selected focus, the resolver picked **F7 (Opposite
   Trade Ratio — itself below its 40% threshold)** for the
   高频交易 question, while the real target is **F6 (High Trading
   Frequency)**; F7 additionally lacks the `signal_explain` capability
   (CONFIRMED UNSUPPORTED, TASK-512048e3f6d9), so the semantically
   correct target is exactly the one that cannot be explained.
3. **signal_explain authority (Audit C) — CONFIRMED data boundary.**
   The tool reads ONLY the case evidence payload
   (`signal_explain.py:225-227`, `fetch_case_evidence`) — rule
   trigger/threshold fields and feature values. For U00020 the RP
   `rule_evidence` carries **threshold=None, trigger=None** for both
   rules, and the high-frequency-trading finding (F6) is
   FEATURE-detected: its explain payload is exactly
   `trade_frequency_24h = 79` with **no threshold or baseline anywhere
   in accessible RP data**. The tool never fabricates one (correct);
   but nothing in the response or composer explicitly declares "RP
   provides no threshold for this feature" — the gap is implicit.
4. **Compound composition vs `replan_failure` (Audit D) — CONFIRMED
   terminal/content inconsistency.** TASK-4699976e5e17 executed BOTH
   deliverables (8 hedge candidates + 18 shared-device accounts), the
   final response delivered both in full, yet the loop ended
   `failed/replan_failure` (the proposer demanded a third analysis
   phase; the real-model re-plan failed) and the task card showed
   "Investigation failed". The extra 「为什么是风险信号」 paragraph is
   NOT cross-run contamination — it is the composer's standard
   related-account section (`agent_loop.py:2540`) rendered from the
   SAME run's related-account payload.

All GSP/CG work (COMPOUND extraction, contract acceptance, safety
guard) was **not active** during the user's manual session: every
U00020 task has `goal_contract_decision=None` and `goal_contract=None`
(the canonical backend ran with flags OFF). The user's observations
therefore characterize the LEGACY path plus the real-model planner; the
approved CG machinery changes several of these outcomes but was not
exercised here.

---

## 2. Scope and Evidence Integrity

- **Worktree**: HEAD `58881a9` ("fix: finalize AL-2.14 production
  behavior") + 124 uncommitted paths (the accumulated GSP + CG-1..5
  working tree). No git state-changing command was run.
- **Runtime**: backend :8001 PID 32217, started 2026-10-09 19:00:31
  CST, canonical invocation, cwd = the worktree `backend/`, env
  `ENABLE_TARGET_VALIDITY_GUARD=false`,
  `ENABLE_GOAL_SEMANTIC_CONTRACT=false`, no seam (verified via
  `ps eww`). Frontend :3001 (vite) and RP :8000 live. NOTE: the flags
  were OFF during the user's manual runs (12:01–12:43 UTC = 20:01–20:43
  CST, after this backend's start) — proven by every U00020 task having
  `goal_contract_decision=None`.
- **Primary evidence**: canonical store documents (read-only
  `mode=ro`): `agent_states` for TASK-cfc7e59066f1 (B),
  TASK-82e43998cd32 (A-1), TASK-e03159aef6fe / TASK-512048e3f6d9 (A-2),
  TASK-4699976e5e17 (D), TASK-9e6abf2a3520 (C loop follow-up);
  `tasks_v2` for the two turn-path runs TASK-aa2c9a436233 (F6) and
  TASK-467decc383ab (F1 short); `investigation_sessions`
  INV-6245e54080d1 for the turn tool-call payloads and turn responses.
- **Read-only live data**: U00020 RP evidence/explanation fetched via
  the project adapter (GET-equivalent reads only; verified no state
  writes) to establish the authoritative finding inventory and the
  rule/feature payload shapes. F6/F7 `signal_explain` payloads were
  reproduced read-only (pure functions over RP GET data).
- **Limitations**: the resolver's real-LLM assist interactions are not
  persisted (candidate choices are not logged), so the F7 selection
  rationale inside the assist is Probable, not Confirmed. The exact
  provider failure mode behind the two real-LLM re-plan failures is
  not recorded in the store (only the bounded user-visible text).

---

## 3. Finding Register

| ID | User-visible impact | Priority | Confidence | Responsible module (candidates) | State |
|---|---|---|---|---|---|
| A-1 | 高频交易 question answered with F3's withdrawal-address rule; `goal satisfied` | High | Confirmed | Context resolution (focus keep) + goal-understanding anchoring; no NL-target cross-check | Root cause confirmed (mechanism) |
| A-2 | 怎样算高频交易 resolved to F7 (wrong; below-threshold finding); F7 cannot be explained; runs failed | High | Confirmed (selection) / Probable (assist rationale) | Context resolution (semantic assist + capability-blind selection) | Partially confirmed |
| B-1 | 9-withdrawal destination comparison never answered; addresses not rendered | High | Confirmed | Observation projection + composer rendering (address dropped); no aggregate/comparison step; legacy completion | Root cause confirmed |
| B-2 | `goal_satisfied` despite unanswered comparison | High | Confirmed | Legacy completion semantics (ANY-success; flag OFF) + convergence STOP | Root cause confirmed |
| C-1 | signal_explain has no authoritative threshold/rule-config/model-metadata source | High (product capability) | Confirmed | RP data surface + tool (by design echoes rule_evidence only) | Confirmed data boundary |
| C-2 | F6 feature answer gives value 79 without declaring the threshold gap | Medium | Confirmed | Composer (feature branch lacks an explicit no-threshold declaration) | Confirmed |
| C-3 | F1 short vs detailed answers differ across turn/loop paths | Medium | Confirmed | Turn vs loop composer renderers (same underlying payload) | Root cause confirmed |
| C-4 | LLM_UNAVAILABLE vs capability-unsupported distinction | Low | Confirmed | Distinct codes/messages already exist (LLM_UNAVAILABLE vs CAPABILITY_NOT_SUPPORTED) | Working as designed |
| D-1 | Both compound deliverables executed and served, task ended `failed` | High | Confirmed | Loop terminal semantics (re-plan failure) vs delivered content; single-skill planner | Root cause confirmed |
| D-2 | Extra 「为什么是风险信号」 paragraph | Low | Confirmed | Composer's standard related-account section (`agent_loop.py:2540`), same-run payload — not contamination | Explained |
| D-3 | Activity decision demanded a third phase that never executed | Medium | Confirmed | Decision proposer vs single-skill planner boundary | Confirmed (design boundary) |

---

## 4. Evidence Chains

### 4.1 Audit A — Finding/focus mismatch (U00020, 高频交易)

**Runs**: TASK-82e43998cd32 (12:33, focus F3, completed/goal_satisfied);
TASK-e03159aef6fe (12:18, focus F7, failed/planning_failure);
TASK-512048e3f6d9 (12:22, focus F7, failed/replan_failure).

Chain (A-1, focus F3, `user_selected`):

1. User manually selected F3 (First Withdrawal to New Address);
   `focus_source="user_selected"` stored (Confirmed).
2. Goal text 「为什么高频交易是风险项」 → goal understanding produced
   `goal_type=explain_finding`, `subject_ref=F3`, with the literal
   reason: 「用户在询问为什么发现 F3 将高频交易标记为风险项…」 — the
   model SAW the 高频交易 vocabulary and bound it to F3 because the
   prompt fixes the target: `goal_resolution.py:627-629` renders
   `Resolved target: finding F3` into the user prompt (Confirmed).
3. The context resolver had already kept the focus:
   `context_resolution.py:746-766` `_keep` — "Preserve the current
   explicit focus exactly as-is", `evidence_kind="explicit_selection"`
   (Confirmed; matches E1 semantics in the al351 suite).
4. `explain_signal` executed on F3 and returned F3's ACTUAL rule:
   `rule_name="First withdrawal to new address"`, `threshold=null`,
   `trigger_values={}`, description "(9 in 24h)" (Confirmed). The
   answer was about F3 — internally consistent, semantically wrong vs
   the request.
5. `retrieve_policy` returned `match_count=4, associated_count=0,
   finding_policy_status="no_finding_level_basis",
   evidence_missing=true` — yet the STOP decision prose claimed the 4
   citations were relevant (Confirmed Activity overclaim; the FINAL
   response's 政策依据 section was honest: "Risk Platform 未为该发现
   提供 finding-level 政策依据").
6. Terminal: legacy ANY-success (`agent_loop.py:585-606`) →
   completed/goal_satisfied. No contract existed (flag OFF) → no
   output-level satisfaction check could object.

Chain (A-2, no manual focus):

1. The resolver returned `agent_resolved` F7 (Confirmed in both run
   docs). The deterministic name matcher cannot be the source:
   `_match_findings_by_name` requires ≥2 significant ENGLISH title
   tokens in the request; the Chinese request has zero overlap
   (reproduced read-only: `_match_findings_by_name(text, findings) →
   []`). The remaining resolver path is the real-LLM semantic assist —
   the assist's candidate rationale is NOT persisted → the F7
   selection rationale is Probable (LLM-assist choice), the fact of
   F7 selection is Confirmed.
2. U00020 inventory (authoritative RP explanation key_findings):
   F6 = "High Trading Frequency — 79 trades in 24h…" (the real
   高频交易 finding; the user's correction is CORRECT); F7 = "Opposite
   Trade Ratio — 34.18% … below the 40% threshold" (NOT triggered).
   So the resolver picked a below-threshold, non-高频 finding.
3. A-2b planned `[explain_signal]` → the tool returned
   `UNSUPPORTED / CAPABILITY_NOT_SUPPORTED / "Finding F7 does not
   support signal explanation."` (Confirmed) — F7's derived
   capabilities are `["self_opposite_trades", "timeline"]`
   (reproduced read-only), `signal_explain` absent
   (`derive_capabilities`, `risk_platform.py:314-319`: the
   detector-named keyword list has no entry matching "opposite trade
   ratio").
4. A-2a failed earlier at planning with `LLM_UNAVAILABLE` (Confirmed —
   environment), and its goal understanding classified the request as
   `policy_check` on the CASE (subject U00020) — semantically the
   closest to the user's threshold question, but the planner then
   failed on the provider.
5. Terminal states: A-2a `failed/planning_failure`; A-2b
   `failed/replan_failure` — the user never got an answer for the
   高频交易 threshold question from either run.

### 4.2 Audit B — nine withdrawals, destination comparison

Run: TASK-cfc7e59066f1 (12:07, focus F3 `agent_resolved`, completed /
goal_satisfied, flag OFF — `goal_contract_decision=None`).

1. Goal: 「9笔提现都提向同一个地址，还是每笔都提向一个新地址？」 — a
   COMPARISON/AGGREGATE question over destination identity.
2. `inspect_withdrawals` executed successfully; observation summary =
   `{record_count: 9}` ONLY (Confirmed). The observation's structured
   `record_rows` (built at `agent_loop.py:273-277`) carry
   `{record_id, timestamp, summary}` — the per-record `address` field
   present in the evidence records is NOT projected.
3. The evidence records themselves DO carry it:
   `adapters/risk_platform.py:840-841` —
   `"address": wd.get("address"), "is_new_address": new_addr` per
   withdrawal (Confirmed). RP payload field `address` exists per
   withdrawal item (Confirmed in the adapter normalization input
   contract and the rendered summary's own wording).
4. The composer's evidence table (`agent_loop.py:2438-2446`) renders
   `| # | 记录 ID | 时间 | 明细 |` with the 明细 cell =
   `r.get("summary")` — the summary string says "…to a newly
   encountered address" and never includes the address VALUE
   (Confirmed — the preserved response, 1520 chars, contains no
   address identifier).
5. Decision trail (Confirmed, exact store values):
   - iter1 RE_PLAN: 「可见记录均显示提向新地址，但输出被截断且缺少具体
     目标地址，需补充核对全部9笔的收款地址才能定论。」 — the proposer
     itself stated the destination addresses were missing.
   - iter2: the re-planned query returned an identical observation →
     convergence STOP: "the re-planned query reproduced an identical
     result with no new information".
6. Terminal: `criteria_kind=question_answered`; legacy
   `criteria_satisfied()` = ANY-success (`agent_loop.py:585-606`) —
   the successful withdrawals retrieval satisfied it →
   completed/goal_satisfied. No contract existed, so no output-level
   satisfaction or claim-specific postcondition could object; no
   unique-address grouping/counting exists in any code path.

### 4.3 Audit C — signal_explain authority

Runs: TASK-aa2c9a436233 (F6, turn), TASK-467decc383ab (F1 short,
turn), TASK-9e6abf2a3520 (F1 follow-up, loop).

1. **What the tool reads** (Confirmed, `signal_explain.py:225-227` and
   module docstring): ONLY the case evidence payload via
   `RiskPlatformAdapter().fetch_case_evidence` — `rule_evidence`
   (echoed "verbatim… never reconstructed"), `feature_evidence`,
   ml/graph signal blocks — plus the finding's own
   signal_refs/policy_refs. There is NO call to any rule-configuration,
   threshold-configuration, or model-metadata surface; the adapter
   exposes none.
2. **F6** (Confirmed): `signal_explain(finding_id=F6)` → SUCCESS,
   `signal_type=Feature`,
   `explanation={"features":[{"name":"trade_frequency_24h","value":79}]}`.
   The tool log comment even names this shape ("e.g. F7's
   trade_frequency_24h"). The authoritative RP data contains the VALUE
   only; `rule_evidence` for U00020 = two withdrawal rules with
   `threshold=None, trigger=None` — **no threshold or baseline for
   trade frequency exists in accessible RP data** (Confirmed).
3. **F1** (Confirmed): `signal_explain(finding_id=F1, signal_type=ML)`
   → `{ml_score: 99.54, detection_method: "LightGBM",
   available_feature_values: {13 features}, …}` with
   `evidence_missing=True` (ML attribution absent). The module
   docstring states the boundary: RP exposes no per-transaction/feature
   attribution; absence is reported, never fabricated.
4. **C3 difference** (Confirmed): the two F1 responses used the SAME
   payload (the session tool-call record for the 12:42 turn and the
   loop run both carry LightGBM + the 13-feature payload) but different
   renderers — the turn composer's concise template (flag-source +
   score + attribution-missing + 1 policy citation, 481 chars) vs the
   loop composer's `_signal_answer_lines` full rendering (含义 + 分数
   语义 + 12 feature values as background + 归因边界 + finding-level
   policy separation, 875 chars). No extra evidence retrieval happened.
5. **Policy association** (Confirmed): the loop composer separates
   finding-level policy from search-level references (A-1's response:
   "未为该发现提供 finding-level 政策依据"; F1 follow-up: "关联到 1 处
   finding-level 政策引用"). The overclaim in A-1's run exists only in
   the Activity decision prose.
6. **LLM_UNAVAILABLE vs capability** (Confirmed): A-2a's user-visible
   failure names the environment ("could not be planned…
   (LLM_UNAVAILABLE)"); F7's gate returns a distinct
   `CAPABILITY_NOT_SUPPORTED` ("Finding F7 does not support signal
   explanation."). Two distinct codes, two distinct user surfaces.

### 4.4 Audit D — compound shared-device + hedging composition

Run: TASK-4699976e5e17 (12:01, focus F5 `agent_resolved`, flag OFF,
failed/replan_failure).

1. Execution (Confirmed): TWO tool runs, both SUCCESS —
   `analyze_cross_account_trades` (8 candidates,
   `candidates_found=8`, accounts U00344/U00350/U00332/U00302/U00320/
   U00308/U00326/U00338/U00314) and `analyze_related_accounts`
   (18 related accounts). Plan revisions = 2 (plan 1 = cross-account,
   re-plan → plan 2 = related).
2. Response (Confirmed, 2192 chars, from the session read model): the
   full 8-candidate table + the 「为什么是风险信号」 paragraph
   (Cluster_device_sharing_2, 19 accounts, risk score 80.0) + the
   18-account related list + shared device DEVBI3XOKGRXS — both
   deliverables served in full. The 8 candidate accounts ARE a subset
   of the 18 related accounts (Verified by set comparison).
3. 「为什么是风险信号」 source (Confirmed): the composer's standard
   related-account section header at `agent_loop.py:2540`, rendered
   from the SAME run's `analyze_related_accounts` payload. Not
   historical reuse; no cross-run contamination.
4. Terminal (Confirmed): decision iter2 demanded a third phase
   (「尚未对这些账号进行对冲交易行为分析，需要新增计划步骤」), the
   re-plan failed on the real provider, `agent.failed` "could not
   re-plan…" → the task card showed failed WHILE the response contained
   both deliverables. The failure is the loop's bounded
   `replan_failure` (`agent_loop.py:1949-1955`) — correct loop
   mechanics, but the terminal/content pairing is inconsistent for the
   user.

---

## 5. Root Cause Assessment

**Audit A-1 — Confirmed root cause (mechanism)**: the focus pipeline
treats a user-selected focus as authoritative and never cross-checks it
against the request's target vocabulary (`_keep`,
`context_resolution.py:746-766`), and goal understanding is prompted
with the fixed target (`goal_resolution.py:627-629`) — so the model
explicitly re-interprets 高频交易 as an attribute of F3 (store reason
text). The explanation/policy tools then faithfully serve F3.
Contributing factor: F3's own rule prose contains "9 in 24h"
(frequency-adjacent), giving the anchoring plausibility. Alternative
considered and rejected: "LLM misunderstanding" — the LLM's behavior is
the intended effect of the prompt contract; the system provides no
conflict signal for it to surface. Missing evidence: none for the
mechanism; open question is the DESIRED conflict-resolution policy
(re-check vs clarify vs proceed-with-boundary).

**Audit A-2 — Probable root cause**: the resolver's real-LLM semantic
assist selected F7 (selection Confirmed; rationale not persisted —
Probable). Contributing confirmed factors: the deterministic name
matcher cannot match Chinese requests to English titles (reproduced);
F7 is capability-blind selected (no `signal_explain`) and itself
below-threshold; F6 — the correct target — supports the Feature-level
explain. Alternative explanations (deterministic vocabulary route)
were tested and did not produce F7. Missing evidence: the assist's
prompt/answer for this exact request.

**Audit B — Confirmed root causes (two independent layers)**:
(B-1) the destination-address identity exists in RP and the adapter
records but is dropped at both user-facing projections
(`agent_loop.py:273-277` observation rows; `:2441-2444` composer
table), and no aggregate/comparison step exists; (B-2) with the GSP
flag OFF the completion rule is ANY tool success
(`agent_loop.py:585-606`), and the convergence guard converts "no new
information" into a STOP that then reads as satisfied — the "问题已解
决" conclusion was never evidence-checked because no structured
requirement existed. Alternative considered: "RP lacks the data" —
REJECTED by direct evidence (the address field is in the adapter
records).

**Audit C — Confirmed data boundary, with two confirmed rendering
gaps**: `signal_explain` reads the case-evidence payload only; for
U00020 the RP provides rule triggers/thresholds as NULL and provides
NO threshold/baseline for feature-detected findings (F6: value 79
only). The tool correctly never fabricates; the confirmed gaps are
(a) the response/composer does not explicitly declare "no threshold
exists in RP" for feature findings, and (b) the turn vs loop renderers
diverge in detail for identical payloads (C3 — confirmed same-payload,
different-renderer). F1's attribution absence is an RP limit that the
system correctly declares (both renderers state it). Alternative
considered: "signal_explain is too simple" — rejected as a blanket
statement: its data surface is the limit; its honesty behavior is
correct.

**Audit D — Confirmed root cause (terminal/content inconsistency)**:
the loop's bounded `replan_failure` (third-phase re-plan failed on the
real provider — Probable provider flakiness, exact mode unrecorded)
terminated the task as failed although both deliverables had been
executed and fully served. The 「为什么是风险信号」 paragraph is
Confirmed as the composer's standard related-account section from the
same run (`agent_loop.py:2540`) — the user's cross-run-contamination
hypothesis is REJECTED by evidence. D-3 (decision demanding a phase
the planner cannot deliver) is the confirmed single-skill planner
boundary.

---

## 6. Cross-Layer Analysis

1. **Target identity (A)** is decided BEFORE goal understanding and is
   never re-validated afterwards — explain/policy/compose all inherit
   it. GSP does not add a check here (by design, GSP assumes the
   accepted resolution). A-1 and A-2 share this layer but differ
   downstream (A-1 completed misleadingly; A-2 failed honestly).
2. **Completion semantics (B, D)**: with the flag OFF, "goal
   satisfied" means ANY tool success, and "replan_failure" means the
   loop could not continue — neither is measured against what the user
   actually asked. Under the flag-ON contract path, B's comparison
   would still pass unless the claim/postcondition encodes the
   comparison (GSP-4 withdrawal postconditions check record presence —
   `goal_postconditions.py`), and D's outcome would be honest
   (per-output states) but the terminal/task-card inconsistency would
   persist (loop terminal vs semantic outcome).
3. **Projection/rendering (B, C3)**: evidence records carry more
   structure than any projection surfaces (address dropped; detail
   level differs by path). These are presentation-layer gaps with the
   data already present.
4. **Data capability (C)**: the threshold/baseline/attributions are
   absent from the RP surface itself — an upstream boundary the agent
   can only declare, not solve.
5. **Distinct roots, distinct WPs**: A (identity), B (completion
   semantics + projection), C (RP data capability + declaration),
   D (terminal/content consistency) must not be merged into one fix.

---

## 7. Recommended Work Package Boundaries (proposal only)

| WP | Scope (layer) | Depends on |
|---|---|---|
| **WP-FR — Finding Resolution / Focus Consistency** | Context resolution + goal understanding: NL-target vocabulary vs focused-finding cross-check (clarify or explicit boundary when they diverge); capability-aware target preference (a target lacking the needed explain capability must not silently win); persist assist rationale for audit | Independent; highest user-visible priority |
| **WP-GS — Goal Satisfaction / Claim-Specific Postconditions** | Goal resolution (claim taxonomy: comparison/aggregate questions) + GSP-4 (claim-specific postconditions, e.g. destination-set comparison) + observation projection (surface the fields the claim needs — address) + composer (render aggregates) | Independent of WP-FR; requires flag-ON contract path |
| **WP-ES — Evidence-backed signal_explain** | RP surface decision first (expose rule config/thresholds/feature thresholds or declare the boundary), then adapter + tool + composer explicit no-threshold declaration; unify turn/loop renderer detail level | RP product decision is a prerequisite |
| **WP-CT — Compound Composition & Terminal-State Consistency** | Loop terminal semantics when deliverables are already served (honest partial terminal instead of failed-with-full-answer); composer output ordering per contract order; Activity/terminal consistency | After WP-GS (outcome semantics) |

Recommended priority: WP-GS → WP-FR → WP-CT → WP-ES (B's false
completion is the most misleading user-visible behavior; FR is
independent and equally user-visible; ES requires an RP decision).

## 8. Proposed Acceptance Criteria (for the future WPs; not executed)

- **WP-FR**: given a user-selected focus and a request naming a
  different detection domain, the system either re-resolves with
  evidence, asks a clarification, or states the focus/intent mismatch
  — in all three cases the response names the targeted finding
  explicitly; API E2E + real browser journey on U00020 with the exact
  高频交易 text; F7-shaped targets must produce an explicit
  capability-boundary, not a planned-then-failed run.
- **WP-GS**: a destination-comparison question yields a contract whose
  output encodes the comparison; the loop may only reach
  `goal_satisfied` when the comparison evidence (addresses/grouping)
  is present in the observation/response; otherwise a bounded
  not-answerable/partial with an explicit data-boundary statement.
  API E2E with the preserved 9-withdrawal run shape + browser journey;
  assertion on the rendered address grouping.
- **WP-ES**: for a feature-detected finding the answer contains the
  observed value AND an explicit statement that RP provides no
  threshold/baseline; for a rule-detected finding the threshold echoes
  RP rule_evidence verbatim or is declared missing; turn and loop
  renderers agree on detail level. API E2E on U00020 F6/F1 + turn/loop
  parity assertions.
- **WP-CT**: a run whose deliverables are fully served must not end
  `failed` for a subsequent enhancement re-plan; terminal/task-card
  wording must match the delivered semantic state; composer sections
  follow contract output order. API E2E replaying the D shape +
  browser journey.

## 9. Remaining Questions and Evidence Gaps

1. The real-LLM semantic-assist rationale that selected F7 (not
   persisted anywhere — the assist's prompt/answer are not logged).
2. The exact provider failure mode behind D's failed third-phase
   re-plan (the store records only the bounded user-visible text).
3. Whether the user's F6 "flagged" turn ran on F6 or F1 (the turn
   document does not record the focused finding; the tool args show
   F6 for the 12:39 turn and F1 for 12:42 — consistent with the
   user's account, taken as Corroborated rather than Confirmed).
4. Whether RP intends to expose rule/threshold configuration for
   feature-detected findings (product decision outside this repo).
5. Static code reading cannot prove how the composer renders every
   possible observation shape; only the preserved runs were verified.

## 10. Audit Integrity and Worktree State

Read-only actions performed: git log/status (no state change); sqlite
queries with `mode=ro`; project-adapter GET-equivalent data reads and
pure in-process tool-function calls for U00020 (no persistence, no
agent run); file reads; `ps`/`lsof` process inspection; `curl` GET
health checks. NOT performed: no source/test/config/flag edits, no
service restart, no POST/PUT/DELETE calls, no investigation re-runs,
no migrations/fixtures, no commits. Git state after the audit: HEAD
`58881a9` with the same 124+ uncommitted paths plus the ONE new file
permitted by this engagement — this report
(`docs/qa/2026-10-09-manual-acceptance-read-only-audit.md`). The
runtime (PID 32217, flags OFF) was not touched.

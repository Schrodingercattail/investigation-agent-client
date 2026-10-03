# AL-2.13 — Response Semantics Corrective Pass — QA v1

**Date**: 2026-10-03
**Status**: **PASS** (canonical runtime, real browser E2E)
**Scope**: minimal corrective implementation based on the read-only
Response Semantics Audit (2026-10-02). No new capabilities; M2
foundation, Safe-Select, ClarificationLedger, RP, tool contracts, the
deleted-lexicon state, and the CASE C cross-account composer unchanged.

---

## 1. Issue A — Duplicate successful-result re-plan loop

**Observed**: loop re-planned the same deterministic
`analyze_related_accounts` query 4 times (identical SUCCESS result each
time); the composer rendered the same answer block 4 times.

**Root cause**: no convergence guard for SUCCESS results that repeat
identically — the AL-2.12 guard only covered EMPTY. The composer also
iterated all observations without per-tool dedup.

**Fix**:
1. `LoopPolicy` no-progress convergence guard: when the last observation
   is SUCCESS, `plan_revisions ≥ 2`, and the observation's semantic
   fingerprint (tool + outcome + status + summary SHA-256) matches an
   earlier observation → STOP. Different args or a genuinely different
   result produce a different fingerprint and are unaffected.
2. Composer: `_case_level_answer_lines` and `_evidence_answer_lines`
   dedupe observations by fingerprint — identical results render once.

**Tests**: convergence stops identical-replan; different args allowed;
different result allowed; first execution never converged; repeated
identical observations render one block.

## 2. Issue B — Related-account answer covers both intents

**Observed**: the loop answered only the listing half (count + 5 IDs)
with no disclosure and no why-explanation; the loop churned because the
LLM correctly noted the missing risk rationale but could only re-run the
same step.

**Fix**: the related-account composer branch now produces a TWO-part
answer from the SAME authoritative payload:
1. WHY: 「Risk Platform 的网络关系检测发现该账号与其他账号存在网络/共享
   设备关系（detection type…cluster…19 accounts…risk score 80.0）。这类
   关系会作为需要进一步调查的风险信号记录;当前数据本身不能证明这些账
   号之间存在违规或恶意意图。」
2. WHICH: 「Risk Platform 报告该网络共有 10 个关联账号,但当前返回的数据
   只包含其中 5 个账号 ID(其余 5 个 ID 未在当前数据中提供),因此只能列
   出这 5 个」+ the 5 IDs + shared devices.

No stronger causality is claimed. The 5/10 gap is attributed to the RP
payload (not to retrieval budget or frontend truncation).

**Tests**: why + boundary present; 10/5 disclosure present; 5 IDs once;
full-list case has no false disclosure.

## 3. Issue C — Loop policy rendering association contract

**Observed**: loop-side policy_lookup rendering listed 4 unassociated
citations (SOP/Triage/Network/…) as 「政策依据」 — the turn-side
association scoping (AL-2.10) was never applied to the loop composer.

**Fix**: the observation whitelist now carries `associated_count` +
`associated_citations` (from `associated_policy_refs`); the loop
composer renders associated references as finding-level policy support
or states explicitly that no finding-level basis exists. Unassociated
matches are never listed as explanation.

**Tests**: associated-only rendering; no-basis explicit statement;
unassociated documents asserted ABSENT from the response text.

## 4. Issue D — ML Pattern Detection user explanation

**Observed**: loop ML response was a bare score line + attribution
apology + 4 unassociated citations; the user could not understand what
ML means or why the score is a risk.

**Fix**:
1. `normalize_ml_signal` now surfaces RP's authoritative
   `detection_methods` field as `detection_method` (e.g. "LightGBM") —
   RP names its detection methods; this is an existing field, not an
   invented one.
2. The observation whitelist carries `detection_method`,
   `score_interpretation`, `attribution_available`,
   `available_feature_values` (capped at 12).
3. The composer ML branch takes precedence over the evidence_missing
   apology (ML signals ALWAYS report evidence_missing because
   attribution does not exist — the apology was burying the real
   explanation). The branch produces:
   - What ML means: 「Risk Platform 使用机器学习（检测方法:LightGBM）
     从账户行为模式中识别与风险升高相关的模式」
   - Score semantics: 「99.41/100 —— 系统检测信号的强度,不是校准过的
     欺诈概率」
   - Feature context: real account feature values as context, explicitly
     labelled 「不代表它们导致了该分数」
   - Attribution boundary: 「当前 Risk Platform 没有提供特征归因,因此
     不能准确断言某一个具体特征贡献了多少分」
   - Policy: only the 1 associated ML-Factors citation; SOP/Network
     references asserted absent.

**Tests**: all 8 semantic markers present; no invented attribution;
observation whitelist verified; normalize_ml_signal carries
detection_method from the real RP payload shape.

## 5. Canonical Browser E2E

All on 3001 → 8001 → RP 8000, real LLM, real RP
(`journey_al213_response_semantics.py`):

| Scenario | Markers asserted | Result |
|---|---|---|
| 1 related accounts | why + boundary + 10/5 disclosure + 5 IDs once + devices; zero duplicate blocks | **PASS** |
| 2 ML explanation | 机器学习 + LightGBM + score + not-probability + feature context + attribution boundary + ML Factors; SOP absent | **PASS** |
| 3 cross-account control | conclusion + criteria + bounded + table + titled artifact | **PASS** |
| reload/reopen (each) | markers persist | **PASS** |

Screenshots al213-01..03 + reload variants.

## 6. Regression

Full sweep: **1289 passed / 14 failed** — remaining failures are the
documented pre-existing set only (provenance ×6, evidence_integration ×3,
agent_loop re-plan test_5/6 ×2, legacy test_agent.py, activity-fixes
limit-semantics updated, agent_loop limit-semantics updated). The
convergence-guard-related test updates (test_10_and_15, test_14b,
activity_stopped_by_limit) reflect the new intentional semantics: the
loop converges no-progress repetitions via the guard BEFORE the
iteration bound; the bound remains the backstop for genuinely varying
results.

Targeted batch (AL-2.8–2.13 + capability + Safe-Select + semantic +
persistence): **175 + 149 + 66 passed / 0 failed** after all changes.

## 7. Before/After user-visible behavior

| Before (AL-2.12) | After (AL-2.13) |
|---|---|
| Related-account answer: 「10 个关联账号」+ 5 IDs ×4, no why, no disclosure | ONE block: why (network facts + boundary) + 「共有 10 个…只包含 5 个 ID」 + 5 IDs + devices |
| Loop churned 4 iterations on identical result | Converges at iteration 1–2 |
| ML loop: bare score + 「未提供归因明细」 + 4 unassociated citations | ML definition + LightGBM + score semantics + 13-feature context + attribution boundary + 1 associated citation |
| Policy loop line: 「匹配到 4 处引用」(unassociated) | associated-only or explicit no-basis statement |

## 8. Known Limitations

1. The convergence guard uses the observation summary as the
   fingerprint basis; a tool that returns identical summaries for
   genuinely different queries could false-positive. The observation
   whitelist includes tool-specific semantic fields, making this
   unlikely for the current tool set.
2. RP's `related_accounts` list truncation (5 of 10) is a Risk Platform
   limitation; the agent discloses it honestly but cannot retrieve the
   missing 5 IDs.
3. ML feature-level attribution remains unavailable in the RP API; the
   agent states this boundary explicitly.
4. Pre-existing legacy failures unchanged (provenance ×6,
   evidence_integration ×3, agent_loop test_5/6 ×2, test_agent.py).

## 9. Definition of Done

All boxes satisfied: convergence guard + composer dedupe; related-account
why + 10/5 disclosure + 5 IDs once; ML user-language explanation with
authoritative fields + attribution boundary; loop policy association
scoping; CASE C control unchanged; API + canonical browser + real LLM +
reload/reopen all verified; regression green.

## 10. Final Status

**PASS**

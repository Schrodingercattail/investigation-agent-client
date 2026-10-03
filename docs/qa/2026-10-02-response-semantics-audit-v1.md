# Response Semantics Audit — Post AL-2.12 (READ-ONLY) — v1

**Date**: 2026-10-02
**Mode**: READ-ONLY audit. No production code, tests, config, seed, M2
metadata, prompts, or frontend were modified. Every conclusion is tagged
**[observed]** (current runtime), **[code-traced]** (current source), or
**[inferred]**.

## 1. Scope / Method

Three real user scenarios replayed against the canonical runtime, plus
source tracing of the ownership chain: ToolResult → observation
whitelist → loop state → composer → persisted response → frontend.
Old QA reports were NOT used as evidence for current behavior.

## 2. Canonical Runtime Identity

**[observed]** `:8001/health` OK; `:8001/api/v2/runtime/identity`
`git_commit: cc56544` (working tree is uncommitted on top of it — commit
identity alone does not date a process). Process restarted today with the
current tree; behavioral markers confirm current code (AL-2.12 composer
wording present in live responses; `bounded_data` stop reason emitted).
Frontend :3001 OK. RP :8000 OK.

## 3. Case A Findings — related accounts

Request replayed: turn1 「给我输出一下00010案例情况」 (intake turn,
`fetch_case + generate_artifact`) then loop goal
「关联账户关系为什么是风险项，这个账户有哪些关联账户」.

**A1/A2 — why the identical block repeats 4×.**
**[observed]** the loop executed `analyze_related_accounts` FOUR times
(iter 4, plan_revisions 4): plan.created → observation (identical) →
LLM decision RE_PLAN → identical re-plan → … → STOP. The four
observations are byte-identical; the persisted loop response contains
the related-account block FOUR times.
**[observed]** the LLM re-plan reasons are semantically correct — each
notes "only 5 of the 10 returned" and "lacks the risk rationale" — but a
re-plan can only re-run the SAME deterministic step, so the loop churns.
**[code-traced]** `_case_level_answer_lines` iterates ALL observations
with no per-tool dedup → one block per execution.
**ROOT CAUSE**: LoopPolicy has a repeated-EMPTY guard (AL-2.12) but no
repeated-identical-SUCCESS guard; composer has no per-tool dedup.
**DIRECT OWNER**: backend `agent_loop.LoopPolicy` (+ composer dedup as
defense). **SECONDARY**: none (frontend renders the persisted string
verbatim — **[observed]** task_response_text already contains 4 blocks).

**A3–A5 — "10 个关联账号" vs 5 listed.**
**[observed]** the ToolResult carries
`related_accounts_count: 10` (RP's own count) and
`related_accounts: [U00337, U00325, U00349, U00004, U00343]` (5 ids —
the RP payload itself ships 5). The tool's 20-row display cap is not the
cause. **[code-traced]** the TURN composer discloses
"（showing 5 of 10）"; the LOOP composer prints the count and the list
with **no disclosure**. So: the system truly holds only 5 ids; "10" is
RP's count; the loop surface's bare "10 个关联账号" over-implies
completeness — composer omission (loop), not data fabrication, not
retrieval budget, not frontend truncation.

**A6 — why the same 5, no pagination.** **[observed]** the RP payload is
static (same 5 ids every call); no pagination fields exist in it.

**A7 — actual ToolResult schema** **[observed]**:
`{analysis: "related_account_analysis", analysis_status:
"RELATED_ACCOUNTS_FOUND", case_id, cluster: {cluster_id, cluster_name,
detection_type: "device_sharing", member_count: 19, cluster_risk_score:
80.0, role_in_cluster: "spoke"}, related_accounts: [5],
related_accounts_count: 10, shared_devices: ["DEV8Z1O4A35QU"],
evidence_refs, evidence_missing: false, next_data_needed: []}`.

**A8 — how the composer composes it** **[code-traced]**: loop surface =
count line + id list (nothing else); turn surface = cluster facts +
accounts + devices + the 5/10 disclosure.

**A9 — artifact vs conversation** **[observed]**: the artifact
("Related Account Evidence — U00010") is composed from the same
ToolCalls — one authoritative source, two renderers. ✓

**A10 — multiple producers?** No — a single composer; duplication comes
solely from the 4 repeated observations. **[code-traced]**

**Case A second part — "why is the related-account relation a risk?"**
**[observed]** the loop answer contains NO answer to this half: the
related-account route plans only the listing step; all four LLM re-plan
reasons explicitly note the missing risk rationale; one STOP decision
reason even drafts a rationale ("因与10个关联账户共享设备…被标记为风险
项") — decision reasons live in Activity, NOT in the user answer, so
nothing overclaims to the user **[observed in response]**.
**[observed]** the RP data DOES support a bounded why: the same payload
carries `detection_type: "device_sharing"`, `member_count: 19`,
`cluster_risk_score: 80.0`, `role_in_cluster: "spoke"`,
`shared_devices: [DEV8Z1O4A35QU]` — exactly the facts the F5/F6 graph
explanation already renders. **Conclusion**: data sufficient; missing
piece = routing/composer (the "why" half has no step and no composer
branch); the "relationship ≠ proof of misconduct" boundary sentence is
also absent on this path. **[code-traced]**

**Note on "Steps completed: 2 (fetch_case, generate_artifact)"**:
**[observed]** that string belongs to turn1's intake response
(fetch_case + generate_artifact); the conversation renders turn1's and
the loop's responses chronologically, so the user reads them adjacent.
**[inferred]** adjacency-by-design, low severity; no cross-task
contamination of the loop task itself was found.

## 4. Case B Findings — ML Pattern Detection

Replay: 「ML Pattern Detection什么意思，为什么是风险」→ title-token
deterministic hit → F1 → explain_signal + retrieve_policy.

**[observed] TURN surface (current, good)**: "flagged by the ML Pattern
Detection model. The model score is 99.41/100 (system signal, not a
calibrated probability of fraud), but transaction-level feature
attribution is not currently available…" + exactly ONE associated
citation (Risk_Scoring_Explainability_Guide / 2.1 ML Factors [1]).

**[observed] LOOP surface (deficient)**: 「信号解释:Risk Platform 未提供
该发现的规则/模型归因明细(数据不足,如实报告)」 + 「政策依据:…匹配到 4 处
引用」 listing unassociated documents (SOP/Triage, AML 5.1 Network…).

**B1–B3 — what defines "ML"?** **[observed]** RP
`risk_summary.detection_methods: ['LightGBM', 'Rule Engine', 'Graph
Network']` and `primary_reason: "ML Pattern Detection"` — RP
authoritatively defines the detection method as a LightGBM (machine
learning) model plus rules/graph; RP's own score wording is
"system signal, not a calibrated probability". **[observed]** citation
[1] (ML Factors) is a CONCEPTUAL evidence-types reference.

**B4** **[observed]** `risk_summary.ml_score: 99.41` and F1's text
"99.41/100" — yes, real.

**B5/B6 — feature-level attribution?** **[observed/code-traced]**
`normalize_ml_signal` returns `attribution_available: False`,
`next_data_needed: ["transaction-level feature attribution"]`, and
`available_feature_values` (13 real account features). RP does NOT
provide score→feature attribution. Correct boundary: the account's
actual feature values may be shown AS CONTEXT; nothing may claim which
features drove the score.

**B7 — machine wording leakage** **[code-traced]**: the observation
whitelist surfaces only `ml_score` + `evidence_missing`; it DROPS
`score_interpretation`, `available_feature_values`,
`attribution_available` — and RP's causal sentence in
key_findings/explanation. The loop ML branch therefore prints a bare
score line, and the evidence_missing branch prints the attribution
apology. **[inferred]** a minimal deterministic mapping (ML → LightGBM
machine-learning detection signal; score → signal strength, not
probability) is derivable entirely from existing RP fields; owner =
observation whitelist + loop composer ML branch.

**B-answer set supportable by current data**: can say — RP's primary
detection reason is an ML (LightGBM) pattern-detection signal; the score
is 99.41/100 signal strength, not a calibrated fraud probability; RP's
own reading is "a very strong pattern-detection signal was recorded for
this account"; the account's actual feature values (13, listable); no
feature attribution is available; one conceptually-associated policy
reference exists. Cannot say — which features drove the score; any
causal chain (e.g. "because shared devices + trades…").

## 5. Policy Citation Relevance (F1)

**[observed]** policy_lookup ran with topic "policy requirements for
finding F1"; matches 4 with keyword-overlap relevance 0/0/1/0 — the
topic tokens overlap almost nothing, so ranking is effectively
unranked. **[observed]** `associated_policy_refs = [citation 1]` —
sourced from F1.policy_refs (Risk Scoring Explainability Guide / 2.1 ML
Factors). **[inferred]** that citation is conceptual background for
score interpretation — related to the finding's MEANING, not a causal
basis; the other three are investigation-procedure / triage / network
references. **[code-traced]** the TURN surface renders associated-only
(AL-2.10 contract); the LOOP surface prints `match_count` + top-3
citations regardless of association — the AL-2.10 fix was never applied
to the loop composer. Principle confirmed: policy match ≠ finding-level
policy basis; F1 HAS one associated reference, so the correct answer
shows that one (as background) — not four.

## 6. Case C (control) — why it is right

**[observed]** fresh replay: goal_satisfied at iteration 2; response =
direct conclusion (6 candidates) → criteria in user language → bounded
retrieval limitation → intent disclaimer → full candidate table →
artifact titled "Cross-Account Trade Evidence — U00010" as supporting
material. **[code-traced]** reasons: (1) the analysis payload is
self-describing (status/candidates/config/retrieval) and its whitelist
carries all of it; (2) a dedicated `_case_level_answer_lines` branch was
written for it; (3) the plan is a single deterministic step — criteria
satisfied immediately, no re-plan churn; (4) the composer was built
answer-first for this branch in AL-2.12. A/B did not get equivalent
branches/fields — a per-branch completeness gap, not an architecture
flaw.

## 7. Response Semantics Matrix

| User request | Scope | Intent | Authoritative source | Expected elements | Current actual (loop unless noted) | Missing/wrong | Owner |
|---|---|---|---|---|---|---|---|
| related accounts list | case | relationship listing | related_account_analysis | count + full list + truncation disclosure | count=10 + 5 ids, no disclosure (turn surface discloses) | disclosure missing; block ×4; churn | LoopPolicy + loop composer |
| why related-account risk | case/relationship | explanation | related_account payload (cluster facts) + graph semantics | observed facts + detection semantics + relationship≠misconduct boundary | absent (listing only) | whole why-branch | routing(plan) + composer |
| ML meaning/why | finding | explanation | signal_explain ML payload (score/interpretation/features/attribution flag) + risk_summary.detection_methods | ML definition + score semantics + actual values + attribution boundary + 1 associated citation | bare score + attribution apology + 4 unassociated citations | whitelist drops fields; loop policy not association-scoped | loop composer + whitelist |
| cross-account trades | case | investigation | cross_account_trade_analysis | conclusion + criteria + boundedness + evidence table | ALL PRESENT (control) | — | — |

## 8. Root Cause Matrix

| # | Symptom | Root cause | Owner | Severity |
|---|---|---|---|---|
| 1 | related block ×4, 4 wasted LLM re-plans | no repeated-identical-result guard for SUCCESS-with-partial | LoopPolicy (+ composer dedup) | **P1** |
| 2 | "10 个" vs 5 listed (loop) | count/list disclosure missing on loop surface | loop composer | **P1** |
| 3 | 4 unassociated citations as 政策依据 (loop) | AL-2.10 association scoping not applied to loop policy line | loop composer | **P1** |
| 4 | related-account "why" unanswered | route plans listing only; composer has no why branch for it (RP data sufficient) | plan/composer | **P2** |
| 5 | ML answer thin on loop surface | whitelist drops interpretation/features/attribution; ML branch minimal | whitelist + composer | **P2** |
| 6 | turn1/loop responses adjacent in one conversation | chronological by design | — (inferred, low) | **P3** |

## 9. Architectural unknowns answered

1. A unified "why is this finding a risk" contract EXISTS on the turn
   surface (compose_response signal branches) but the loop composer has
   only a thin variant; the missing pieces are the observation whitelist
   fields + loop composer branches — NOT a new explanation model.
2. "Finding title / detection signal / risk reason / policy basis" are
   distinct and mostly modeled distinctly (signal payload vs policy
   association); the loop policy line is the one place still conflating
   them.
3. F1 is simultaneously RP's `primary_reason`, a finding in
   `findings[]`, and the name of the ML detection signal — RP conflates
   by design; the agent should present it as "RP's primary detection
   reason, surfaced as a finding, scored by the LightGBM signal".
4. Policy lookup currently always rides the deterministic explain plan;
   its turn-surface association scoping is the existing mitigation; the
   loop surface must adopt the same (no new policy designed here).

## 10. What MUST NOT change

M2 contract; deleted-lexicon state (no lexicon/aliases/deterministic CJK
fallback); Safe-Select/SupportEvaluator/SafeSelectGate;
ClarificationLedger; RP; tool contracts; case-level capability routing;
the cross-account answer branch (control — do not refactor).

## 11. Acceptance scenarios for the eventual corrective pass

1. Related-account loop: exactly ONE related-account block; count
   sentence discloses "10 个（RP 列表提供 5 个）"; a bounded why
   (cluster facts + relationship-signal boundary + 关系≠不当行为说明);
   zero re-plan churn (deterministic stop).
2. ML loop: "ML Pattern Detection 是 Risk Platform 的机器学习
   （LightGBM）模式检测信号"; score 99.41/100 = 信号强度而非校准概率;
   the account's real feature values as context; explicit
   "特征归因不可用" boundary; only citation [1] as associated policy
   reference.
3. Cross-account loop: unchanged (regression control).
4. Conversation isolation + prior AL-2.x behaviors: unchanged.

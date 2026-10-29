# Golden User Journeys Registry V1

**Status**: ACTIVE — registry of repeatable acceptance journeys for core
Agent capabilities, per the Feature-scoped Definition of Done in
`CLAUDE.md` (Development / QA Quality Gate). Each entry names the real
user entry point, the journey, the long-term regression pointer, and the
evidence layers achieved.

**Rule**: a journey is registered here when a core capability ships or
when a real user-visible failure exposes a missing journey. Every
registered journey must have (or explicitly declare missing) a
long-term regression test that asserts actual behavior and persisted
results. Real failing acceptance inputs enter the registry (see
CLAUDE.md "Regression & evidence discipline", rule 4).

Legend: ✅ regression exists and runs · ⚠️ partial (gaps listed) · ❌
MISSING (must be written before the capability may be called
regression-protected).

---

## GJ-REG-U00010-B1 — English/Chinese compound criteria ask with per-stream file delivery (withdrawal + transaction detail files)

**Trigger (real failing acceptance, 2026-10-21/23)**: EN compound ask
("determination criteria for High Withdrawal Frequency and for New
Account with High Activity? Generate the withdrawal detail file and the
transaction detail file") and its zh twin
(「高频提现和新账号高频活动的判定条件分别是什么？给我生成提现和交易明细文件」).
Multi-run failure chain: anchoring claim-variance (generic LLM claims
left rule_explanation producers unanchored → INVALID_ARGUMENT
finding_id), records producers anchored to the WRONG finding (the
transaction producer resolved to the withdrawal finding F3 via
claim/request domain tiers → EVIDENCE_STREAM_NOT_SUPPORTED), loose
unbound artifacts shipping cross-stream soup, and per-stream file pairs
missing from re-plans/initial adoption.

**Journey**: real submit entry (EN and ZH phrasings) → contract with
SPLIT per-stream file outputs (deterministic fused-output split) →
explain producers anchored via the request-words fallback +
stream-first anchoring for records producers (both planner and
coverage-supplement sites) → both records producers succeed on their
own findings → per-stream artifact pairs (withdrawal file = F3
complete record set W-rows only; transaction file = F1 complete record
set T-rows only) → no loose unbound artifacts, no mixed-stream files →
criteria answered with real rule thresholds, no unresolved markers.

**Regression**: ✅ `backend/tests/test_u00010_file_stream_contract_v1.py`
(fused-output split, one-file-one-stream binding guard on both
anchoring paths, stream-first producer anchoring, hollow-shell
rejection, stable references, anchoring request-words fallback,
section-aware records-row judgment).
**API E2E**: ✅ `docs/evaluations/u00010-planq2-api-e2e-b1en.json`,
`u00010-loose-api-e2e-b1en.json` (both per-stream files content-verified).
**Browser E2E**: ✅ zh `docs/evaluations/u00010-phase2-browser-20261022T082335Z.json`
(B1_zh 6/6, tabs=3 real panel, persisted 1,097 W-only + 6,659 T-only);
en `docs/evaluations/u00010-phase2-browser-20261023T023803Z.json`
(B1_en 10/10 strict checks incl. response-correlated answer capture,
section-aware W-only/T-only panel verdicts, persisted
ART-1ee3d041b9 1,321 = F3 7×W-rows/0×T,
ART-1795742b0d 6,662 = F1 0×W/64×T, task TASK-2a5f976a6f52
complete/goal_satisfied in 6 iterations).
**Status**: ✅ PASS (zh + en, real browser evidence, 2026-10-23).

---

## GJ-REG-U00010-B4/B5/B6_zh — 中文会话 finding chips（policy / explain / timeline）逐轮中文回答

**Trigger (real failing acceptance, 2026-10-22/23)**: zh 会话（调查U00010 创建）中，focus ack 后的
follow-up chips 渲染为 EN faces（follow_ups 生成未传 per-turn lang），点击后前端 echo
lang=en → 回答以英文为主。两层根因：(a) 前端 detector 缺 explicit-zh 层（调查U00010 的
case id 主导 CJK 占比 → en）；(b) 后端 follow-up 路径未让 session 存储语言优先于
canonical-EN intent 检测。

**Journey**: 真实创建（调查U00010 → 初始语言 zh 持久化）→ 选中 F6 → 中文 chips →
点击（echoed lang=zh）→ 回答以中文为主：
- B4 policy chip：`关于「提供 U00010 的关联账号相关政策引文与该条政策的原文摘录…」：
  该发现直接适用 1 条政策引用：AML_Suspicious_Indicators.md 5.1 [3] + 原文摘录`
- B5 explain chip：集群/19 账号/preview-总量 中文标注
- B6 timeline chip：finding-scoped 中文时间线

**Regression**: ✅ `_submission_response_language` 规则测试（echoed>stored>detect、
intake 例外、负例）；前端 detector mirror explicit-zh 层（`frontend/src/lib/
responseLanguage.ts`）。
**Browser E2E**: ✅ B5_zh/B6_zh `…T073507Z.json`（8/8、7/7）；B4_zh
`…T075346Z.json`（7/7，task TASK-c455eb780c92 state lang=zh，回答 535 字符
zh-dominant，AML 5.1 引用渲染）。
**Status**: ✅ PASS (zh, real browser evidence, 2026-10-23; B4_en 既有 PASS 不变)。

---


## GJ-REG-U00010-B2 — English/Chinese ambiguous policy-citation ask ("引文原文" / "original text of the citations")

**Trigger (real failing acceptance, 2026-10-22)**: the EN ambiguous ask
("Send me the original text of the citations related to the linked
accounts") produced the CORRECT policy-citation answer, but the browser
harness captured the intake card (answer-capture race) and failed the
citation-content assertion (HARNESS class — the product delivered
AML_Suspicious_Indicators.md 5.1 Links to Known Risky Clusters with the
cited excerpt; TASK-e6cc964918bd).

**Journey**: real submit entry (EN and ZH phrasings) → the deterministic
policy_citation_original_text candidate + guards route the citation ask
to the policy-citation delivery (never network attribution) → answer
renders the citation document/section [3] with the cited original-text
excerpt → response-correlated browser capture (the card is located via
THIS ask's stored composed response).

**Regression**: ✅ `backend/tests/test_u00010_citation_contract_v1.py`
(candidate emission, guard drops network in citation asks, guard-after-
merge, request-words anchoring).
**Browser E2E**: ✅ en `docs/evaluations/u00010-phase2-browser-20261023T030209Z.json`
(8/8 strict checks incl. task-terminal gates and response-correlated
cards for BOTH asks; AML 5.1 + 原文摘录 rendered);
zh `docs/evaluations/u00010-phase2-browser-20261022T043719Z.json`
(B2_zh 4/4 with the same product path).
**Status**: ✅ PASS (zh + en, real browser evidence, 2026-10-23).

---


## GJ-REG-U00029 — Linked-accounts set-internal hedge investigation + artifact scope binding

**Trigger (real failing acceptance, 2026-10-20)**: EN and ZH compound
asks for linked accounts + set-internal hedge candidates + details +
artifact. EN phrasing never routed to the set-internal analysis
(vocabulary gap, QA boundaries B6 addendum); the ZH run executed the
analyses correctly but the persisted artifact bound the stale
transaction-retrieval step (QA boundaries B14).

**Journey**: real submit entry (EN and ZH phrasings) → goal contract
with the hedging deliverable → plan executes
`analyze_related_accounts` + `analyze_related_account_set_trades` →
answer lists each candidate pair (both accounts, transaction ids) →
artifact persisted with `## Candidate Cross-Account Relationships`
containing the run's OWN candidate pairs and transaction ids → plain
transaction-details requests keep the plain artifact (compat) →
NO_MATCH / INSUFFICIENT_DATA stay bounded.

**Regression**: ✅ `backend/tests/test_u00029_hedge_artifact_scope_v1.py`
(tests 1–5: EN/ZH planning, artifact content binding incl. traceability
of fixture candidate pairs/tx ids, plain compat, no-candidates bounds).
**API E2E**: ✅ `qa/u00029_hedge_e2e.py` (EN/ZH/CTRL via real submit;
artifact content verified through GET /tasks/{id}/artifacts).
**Browser E2E**: ✅ `qa/u00029_hedge_browser.py` (real input box; artifact
panel #2 tab renders the candidates table; reload/reopen persistence;
screenshots `qa/screenshots/u29-{EN,ZH}-*.png`).
**Phase-2 re-acceptance (2026-10-23)**: ✅ zh
`docs/evaluations/u00010-phase2-browser-20261023T033848Z.json` (7/7;
CATA table rendered tabs=2; persisted 9,421-char candidate file) ·
en `docs/evaluations/u00010-phase2-browser-20261023T043147Z.json` (7/7
after the EN file-intent classification fix — "generate a detail file";
delivered ART-a6a8231f61 = 2,109 chars, CATA-001…007 with pairs;
coverage limitation surfaced; both asks response-correlated).
**B7 authorization re-acceptance (2026-10-23)**: ✅ zh/en
`docs/evaluations/u00010-phase2-browser-20261023T081528Z.json` /
`…T082534Z.json` (9/9 per-journey checks: the NO-file ask's own task
persists NO artifact; the explicit file ask delivers ART-92be73d75e /
ART-22ac1a83c7 = 2,109 chars, CATA-001…007 with pairs; per-task
response-correlated capture with distinct task ids).

---

## GJ-REG-U00029-NOART — No unsolicited Artifact on a hedge investigation WITHOUT a file request (negative scenario)

**Trigger (real acceptance observation, 2026-10-20)**: the user asked
"list all linked accounts and check if there is any hedge transaction
within the linked accounts. If any, please send me the transaction
details." — an investigation + details request with NO file/artifact
intent — and the system still generated an artifact.

**Two independent dimensions** (a pass on one never implies the other):
- CONTENT correctness: the analysis result and transaction evidence are
  correct — covered by GJ-REG-U00029 above.
- DELIVERY-INTENT compliance: only deliverables the user requested (or
  a formal product contract authorizes) are produced.

**Authorization rule (verified against current contracts)**: automatic
artifact generation is formally authorized ONLY for (a) explicit
user file/artifact/export intent (ARTIFACT_CASE / ARTIFACT_FINDING),
and (b) the Case Intake operation (auto case-scoped bundle is accepted
product behavior — planner_v2 intake plan branch). NO contract
authorizes auto-artifact for the set-internal hedge investigation
branch, yet `planner_v2._artifact_stream_plan`'s hedge branch
unconditionally appends a `generate_artifact` step — the unsolicited
artifact is planner-authored, not user- or contract-authorized.
"send me the transaction details" is DELIVERY of evidence in the
conversation, never file-export authorization.

**Expected behavior**: the hedge investigation executes and the answer
presents the candidates + transaction details directly in the
conversation; NO artifact is created unless the user asked for one
(or the intake contract applies).

**Regression protection: ✅ FIXED + COVERED (2026-10-21)**. Production
fix: `planner_v2`'s INVESTIGATION-intent set-internal branch now gates
the generate_artifact step on explicit artifact intent
(`request_intent ∈ {ARTIFACT_CASE, ARTIFACT_FINDING}`) — the
investigation-only phrasings plan the two analyses WITHOUT the artifact
step, and explicit-artifact requests keep the authorized artifact path
(positive control unchanged). Regressions:
`backend/tests/test_u00029_hedge_artifact_scope_v1.py` — tests 1–2 are
the EXPLICIT-intent positive controls (revised from the old
unconditional assertions); tests 1n/2n assert NO generate_artifact in
the final effective plan for the EN/ZH no-file phrasings; the
coverage-admission guard asserts the supplement never inserts
generate_artifact for the negative contract; the loop-level test
asserts NO persisted artifact (state.artifact_ids empty). **Real API
E2E**: ✅ NEG-EN (TASK-4dbf465d2f29) and NEG-ZH (TASK-f4092e4764dc):
no artifact, results in conversation; POS (TASK-f57bf1c733b0 →
ART-dc622c6949): authorized artifact created with hedge candidates.
Evidence: `docs/evaluations/u00029-noart-e2e-20261021T084500Z.json`
(store-verified) + live GET /tasks/{id}. **Browser E2E**: NOT TESTED
for this specific negative scenario (documented pending; the planner
change is intent-gating only — the rendered answer surface is covered
by the positive journeys).

---

## GJ-REG-U00005 — High Withdrawal Frequency three-part compound ask ✅ (unit/integration + API E2E) / browser NOT TESTED

**Trigger (real failing acceptance, 2026-10-21)**: within a U00005
investigation focused on F2 "High Withdrawal Frequency", the user asks
THREE things in one investigation:
1. the CRITERIA for the High Withdrawal Frequency flag (why / what
   threshold);
2. whether the new withdrawal destinations are the same or different
   (destination-identity comparison);
3. all withdrawal records (complete record-set delivery).
All three sub-questions returned "No determinable result was produced
for this item" while the task displayed completed — three per-output
failures masked by a completed status (QA boundaries B12/B14 family;
contract-coverage and artifact-source disciplines apply).

**Journey**: real submit entry, one compound EN ask combining
criteria-explain (rule/threshold semantics) + destination-identity
comparison + complete withdrawal-records delivery. Regression asserts
(a) each of the three deliverables satisfies or is honestly bounded —
never "No determinable result" across the board under a completed
status; (b) the records delivery content (record ids/timestamps match
the RP fixture); (c) the destination comparison verdict is grounded in
the destination-identity fact block. (All three now covered — see
Regression below.)

**Regression (added 2026-10-21)**: ✅
`backend/tests/test_u00005_compound_criteria_withdrawals_v1.py` — pins
the original failure signature (compound title match ambiguous F2+F3),
the planner title-anchoring recovery (all three finding-level steps
anchored to F2 under the accepted contract), the anchoring guards
(explicit id preserved / non-finding-level steps untouched / multi-title
no-guess), and the REAL postcondition evaluator satisfying all three
outputs from anchored evidence. **API E2E**: ✅
`qa/u00005_compound_e2e.py` → real submit entry, all three deliverables
verified individually (O1 criteria rule+threshold grounded; O2
destination verdict + per-record identity groups; O3 complete records
with ids/timestamps). Known residual (VERIFIED reliability risk): planner-LLM output
variance can fail an individual run as a USER-VISIBLE task failure —
TASK-b10bdb8b7afa (2026-10-21) ended `failed` with "…could not be
planned…(LLM_OUTPUT_INVALID)". `LLM_OUTPUT_INVALID` has NO auto-retry
(the provider retries only empty text blocks; the planner returns
PlanningFailure directly); recovery is manual re-submit (the next
identical request succeeded). Observed 1 in 3 identical runs. The
fix for this run-level reliability gap is a SEPARATE pending item —
not in scope of the planning-anchoring fix. **Browser E2E: see below.**

---

## Existing journey sources (referenced, not duplicated)

- `docs/baselines/2026-09-20-open-ended-investigation-conversation-ux-v1.md`
  §35b Stage 2 golden scenarios (GJ-ST2-01..03: suitable / related-but-
  unsuitable / no-capability routing with honest completion semantics).
- Language-consistency journeys (EN/ZH response-language contract,
  follow-up chips, restore) — regressions:
  `backend/tests/test_response_language_consistency_v1.py`,
  `qa/language_audit_browser_*.py`, evidence
  `docs/evaluations/language-audit-*.json`.
- Trailing-plan / Activity-in-conversation journeys — regressions:
  `backend/tests/test_case_intake_answer_v1.py`,
  `frontend/src/components/investigation/__tests__/`,
  `qa/journey_activity_inline_browser_u00047.py`,
  `qa/journey_trailing_plan_dedup_browser_u00033.py`.

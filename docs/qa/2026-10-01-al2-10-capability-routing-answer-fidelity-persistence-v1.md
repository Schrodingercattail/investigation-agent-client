# AL-2.10 — Capability Routing, Answer Fidelity & Conversation Persistence — QA v1

**Date**: 2026-10-01
**Status**: **PASS** (canonical runtime; limitations §11)
**Scope**: truthful failure taxonomy; supported case-level capability
routing; answer fidelity (citations, detail requests); conversation
restore chronology. Safe-Select, M2 contract, ClarificationLedger, RP,
Activity UI: untouched.

---

## 1. `semantic_resolution_unavailable` — the REAL cause (evidence, not the error string)

The user-observed stable failure of
「是否有其他账号和这个账号之间存在对冲交易，如果有，输出相关证据给我」
was **NOT a provider outage**. Out-of-band probe (product code unmodified,
real provider, real registry):

- **B's text, 3/3 attempts**: HTTP success, but the response object
  carried **no text block** after 8–15 s → `ClaudeProvider` raises
  `LLMError("LLM response contained no text block")` →
  `SemanticNormalizer.normalize`'s broad `except Exception` labeled it
  `R_LLM_UNAVAILABLE` (the exception type was discarded from the log) →
  resolver → `SEMANTIC_RESOLUTION_UNAVAILABLE`.
  **Root cause**: `SemanticNormalizer` ran with `max_tokens=400`; glm-5.3
  is a thinking-style model whose reasoning blocks consume the budget
  before any text exists. The identical lesson was already captured in
  planner_v2 (512→2048, "6/6 success at 2048") — the normalizer was the
  outlier.
- **A's text ("高频提现有哪些证据"), 3/3**: clean 91-char JSON. But note:
  **A never exercises the semantic layer in production** — 「高频提现」 is a
  finding_lexicon exact entry, resolved deterministically BEFORE the
  semantic stage (verified: scripted-normalizer call count = 0 for A).
  "A works" therefore never proved the semantic layer healthy.
- Log corroboration (`/tmp/canonical_8001.log`): both
  `malformed LLM output` and `LLM unavailable` warnings appear for these
  turns — the SAME user-visible label collapsed ≥2 distinct causes.
- **Verdict**: request-specific semantic failure (self-inflicted budget +
  broad-except misclassification), compounded by a routing defect (§3).
  Evidence is conclusive; no "evidence insufficient" caveat needed.

## 2. Failure-taxonomy audit (the §2 checklist, each item)

| Class | Old behavior | Verdict | Now |
|---|---|---|---|
| A. provider truly unreachable (conn/timeout/auth/429) | unavailable | **correct** | unavailable (only this) |
| B. UNKNOWN / no candidate | fall-through | correct | unchanged |
| C. concept not in registry | fall-through | correct | unchanged |
| D. concept valid, no case candidate | fall-through | correct | unchanged |
| E. capability-level request forced into finding resolver | **YES — defect** | fixed (§3) | case-level route first |
| F. malformed / no-text proposal | **unavailable — misclassified** | fixed | `provider_output_no_text` reason; **fall-through** (a resolution failure, never an outage) |
| G. provider OK, validation/grounding failed | fall-through | correct | unchanged |

Also fixed: `LLMEmptyResponseError` (new additive subclass) keeps the
no-text case truthfully classified at the provider boundary; the
normalizer now logs the exception type it previously discarded.

## 3. Why A worked and B failed — the routing defect (§4/§5)

B is a **supported case-level cross-account capability request**: scope
「其他账号…之间」+ subject 「对冲交易」→ `cross_account_trade_analysis`
(needs only case_id). The deterministic matcher missed it on a concrete
vocabulary difference: every scope element used 账户 forms
(不同账户/账户之间/跨账户…), while the user's phrasing uses **账号**
(其他账号/账号之间). The AL-2.6 control phrase
「查一下有没有不同账户之间的对冲交易情况」 contains 账户 and matched; B
contains no 账户 form and fell through to finding resolution → semantic
stage → the §1 truncation.

**Cross-layer lottery evidence** (shared-DB task records): the sibling
phrase 「那这个账号有与其他账号做关联对冲交易的情况吗」 produced FIVE
different outcomes across attempts — completed / assist-unresolved /
cross-account analysis actually RAN (twice; honestly blocked on
retrieval truncation) / AMBIGUOUS(F5,F6,F8) / semantic-unavailable —
because no deterministic tier captured the phrase and different
LLM-mediated layers decided by weather. The deterministic capability
route eliminates the lottery.

## 4. Fixes implemented (minimal, at the failing boundaries)

1. **Shared capability matcher** (`investigation_concepts`): 账号-family
   scope elements added (跨账号/不同账号/账号之间/其他账号/别的账号 + traditional
   forms). B now routes case-level BEFORE finding resolution.
   Negative controls hold: 「比较不同账户的提现频率」 still never routes.
2. **Normalizer budget 400 → 2048** + truthful `R_OUTPUT_NO_TEXT`
   classification + resolver maps it to fall-through. Genuine outage is
   the only `SEMANTIC_RESOLUTION_UNAVAILABLE` producer.
3. **Second case-level capability**: `related_account_analysis` domain
   tool (authoritative RP `network_evidence` echo — cluster, related
   accounts, shared devices; bounded lists; evidence_missing when absent;
   never derived), step `analyze_related_accounts` (analysis_type
   locked), skill `case_related_accounts`, executor registration +
   case_id injection, deterministic planner branch, resolver route
   (evaluated AFTER the trade predicate — a trade request can never be
   captured by it), observation projection + user phrase + NL composer.
4. **Retrieval budget**: `TRADE_ANALYSIS_MAX_ACCOUNTS` 5 → 24 (documented
   provisional-values note; U00010's RP network attribution carries
   18–19 related accounts — the former budget truncated every realistic
   request). With it, the live analysis returns **MATCH_FOUND: 6
   candidate relationships, 19 accounts analyzed** (retrieval
   still bounded; the payload discloses what ran).
5. **Answer fidelity**:
   - finding-explain answers no longer dump investigation-wide policy
     citations; no finding-level basis → exactly that sentence
     ("The Risk Platform has attached no finding-level policy basis to
     this finding; the citations it holds are investigation-wide, not
     specific to it").
   - No unexplained "RP" abbreviation in user-visible strings
     ("Risk Platform 记录 N 条" / "Risk Platform 为该发现匹配到 N 处引用").
   - Detail requests: the loop surface states the COMPLETE count and
     names where the full set lives ("完整 N 条记录保存在本次任务的证据
     结果中(可在该发现的证据视图逐条查看)"); the conversation surface
     lists every record (verified: 64/64). Examples are always labeled
     "其中例如".
6. **Persistence chronology**: loop tasks previously had
   `started_at = NULL`; `list_by_investigation` ordered by
   `started_at ASC` put the loop turn at the TOP of reopened
   conversations. Fixed at both layers: the loop runner now stamps
   `task.started_at`; the store orders NULLs last (historic rows).

## 5. A/B differential trace (canonical stack, post-fix)

| Layer | A 高频提现有哪些证据 | B 是否有其他账号…对冲交易…证据 |
|---|---|---|
| resolver | lexicon exact → F3 (no LLM) | **case-level trade route** (no LLM, no finding) |
| normalizer | never invoked | never invoked |
| plan | inspect_withdrawals (locked) | analyze_cross_account_trades + generate_artifact |
| tool | finding_drilldown | cross_account_trade_analysis (+ bundle) |
| outcome | 7 real withdrawal records | **6 candidate relationships + evidence bundle** |

## 6. Error Classification Control (tests)

`tests/test_al210_capability_routing_v1.py` (13 tests): the four classes
are asserted distinguishable — genuine outage → unavailable;
no-text-block / malformed / no-governed-concept → fall-through (never
unavailable); B → case-level trade plan (no semantic_grounding key,
normalizer script consumption = 0); R → related-account plan; mixed
vocabulary → trade wins; store ordering (NULLs last); loop task stamps.

## 7. API / Browser E2E (canonical runtime :3001 → :8001 → RP)

- API: differential A/B, related-account (real RP attribution U00…),
  classification controls — all PASS.
- Browser (real Chrome, real LLM, real RP):
  1. related-account → "Risk Platform network attribution: cluster
     Cluster_device_sharing_1 … Related accounts: U00337, … (showing 5
     of 10). Shared devices: DEV8Z1O4A35QU" — **PASS** (persisted).
  2. hedge request → "The analysis found 6 candidate cross-account
     opposite-trade relationships … CATA-001 (1_to_1): U00307 BUY vs
     U00349 SELL — MATIC …" + bundle — **PASS** (persisted; skill
     case_trade_analysis 2/2; Focus stays case overview — never a
     finding guess).
  3. shared-device explain → graph answer + **no case-level policy
     dump** — **PASS**.
  4. 高频交易+具体交易明细 → "Trade frequency (24h) = 54" + transaction
     records — **PASS** (persisted).
  5. genuine LLM-unavailable (no-key stack :3011→:8012, torn down after):
     honest 语义解析服务当前不可用 card; retry stays honest; no fabricated
     answer — **PASS**.

## 8. Regression

- Final full sweep (`tests/`, ~19 min): **1280 passed / 23 failed**, with
  the failures fully dispositioned:
  - **Pre-existing legacy set (19, none reachable from AL-2.10 code
    paths** — verified by import-path inspection; the failing tests
    import only legacy `app.tools`/`app.agent`/`app.mcp_client`**)**:
    provenance ×6 + evidence_integration ×3 (legacy `app/tools.py`
    NoneType crash on current RP payloads), error_handling ×4 (legacy
    agent), agent_loop re-plan ×2, tool_orchestration_final ×4.
  - **Real-LLM flaky (1)**: target_refinement stability E5 — one of
    three real runs hit the provider no-text-block weather inside the
    flag-gated frozen refinement experiment (its budget is already
    2048); decisions varied CLARIFY/SELECT/CLARIFY. Not reachable from
    any AL-2.10 change; documented, not fixed.
  - **Reconciled to intentional contract changes during this work item
    (all green after update)**: week1_semantic_cleanup ×11 (citation
    contract), eval E02/E03/E11/E12 + P01 (AL-2.8 explain+policy
    continuation), telemetry ×2 (same), investigation_service gating
    test, al26 + al27 negative controls (bounded-blocked family),
    AL-2.8 malformed-taxonomy test.

## 9. Answer-fidelity before/after (shared-device scenario)

- Before: "… 1 policy reference applies directly: … Additionally, 3
  case-level policy references apply to the overall investigation: 1…
  2… 3…" (investigation-wide padding).
- After: finding-level references only; no-basis findings state exactly
  that; no "RP 引用 N 处" anywhere.

## 10. Persistence

Chronology fix verified by unit (NULLs-last ordering; loop task start
stamp) and by browser reload/reopen in scenarios 1/2/4. No duplicates
(one user+one agent turn per task by construction); loop responses
retain via the session read model.

## 11. Known Limitations

1. Trade-analysis retrieval remains bounded (500 transactions); large
   cases disclose truncation in the payload/composer. MATCH_FOUND is
   still reachable on the reference case (6 candidates @ 19 accounts).
2. The related-account capability echoes RP's list (RP itself returned
   5 of its counted 10 — disclosed as "showing 5 of 10").
3. No-text-block/malformed proposals now fall through; a degenerate
   provider could therefore surface as unresolved rather than
   unavailable (deliberate: the taxonomy names what is true; the loop
   composer / guidance still offers explicit selection).
4. Scenario-5 browser leg ran on a temporary no-key stack (canonical
   backend cannot be key-less); torn down after.
5. Pre-existing legacy failures unchanged (§8).

## 12. Final Status

**PASS** — the reported failure's true root cause is fixed at three
levels (budget, taxonomy, routing), the two case-level capabilities work
on the canonical stack with real RP data and natural-language answers,
the error taxonomy is truthful and test-pinned, and no safety boundary
was relaxed.

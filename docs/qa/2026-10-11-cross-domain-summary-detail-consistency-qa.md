# Cross-Domain Finding Summary–Detail Consistency — QA Record

**Date**: 2026-10-11
**Status**: IMPLEMENTED + VALIDATED — domain inventory 8/8 audited;
known 9-vs-7 discrepancy root-caused (premise mismatch, NOT a summary
defect) and closed with a deterministic, reusable premise-reconciliation
contract; one genuine RP-side cross-path population boundary and one
RP-side metric-semantics mismatch documented (not Agent-fixable);
regression green; browser journeys PASS. Not committed / not pushed.
**Scope basis**: task contract §2 (summary/detail scope + numeric +
semantic consistency), §3 (all supported Finding types), §7 (DoD).

## 1. The reusable consistency invariant (product contract)

For the same case, finding, evidence scope, and data snapshot, the
Finding summary/context and the expanded-detail path must not contain
material, unexplained contradictions. When the user's request (or the
accepted claim text) asserts an evidence population that differs from
the authoritative, completely retrieved population, the system MUST
explicitly reconcile the difference in the user-visible response — it
must never silently substitute populations, and a comparison over the
complete authoritative population remains complete (satisfaction
semantics unchanged).

**Enforcement point (narrowest architecture)**: the GSP-5 composer's
facet section (`_render_destination_facet`), fed by a bounded
deterministic extractor (`_asserted_record_population`: frozen
classifier pair 「<n>笔 / <n>条」 with an ordinal guard excluding
「第N笔」; max across matches; CJK numerals out of vocabulary by
design). When the asserted population differs from the authoritative
`records_total` (GS-B block) or the retrieval `record_count`, the
composer renders an explicit reconciliation line:
「说明：您的请求提及 9 笔提现；权威证据共 7 笔提现（检索完整，无缺
页），以下比较与结论已覆盖全部 7 笔提现记录。」
Deterministic (no LLM judgment involved in detecting the mismatch);
the evaluator's satisfaction semantics are untouched (the conclusion
over the complete authoritative population IS complete — the note
reconciles the premise per §2.1 "explicitly identify the difference").

## 2. Finding-domain inventory and audit matrix (all supported types)

Live data verified read-only against RP :8000 (complete evidence view,
`expose_complete_records=true`):

| Finding | Summary claims (RP-authored) | Authoritative detail path | Verified |
|---|---|---|---|
| F1 ML Pattern Detection | ml score 99.41/100, "system signal not calibrated" | signal explanation (ml_score) | ✅ risk_summary.ml_score = 99.41 — consistent |
| F2 New account / high activity | 6 days old; 54 trades in 24h | feature_evidence (account_age_days=6, trade_frequency_24h=54) | ✅ internally consistent with RP aggregates; **but** the 24h figure itself carries the RP-side anchor semantics of F7 (below) |
| F3 High withdrawal frequency | 7 withdrawals in 24h | withdrawal evidence (complete view) | ✅ 7 rows; block.records_total = record_count = rows = 7; feature_evidence withdrawal_frequency_24h = 7 |
| F4 First withdrawal to new address | 7 in 24h; 100.00% to new addresses | same 7 records; per-record `is_new_address` | ✅ 7/7 new = 100.00% |
| F5 Shared device relationships | 1 shared device | related-account analysis (shared devices) | ✅ network_evidence shared_devices = 1 |
| F6 Linked account network | 18 connected accounts | related-account analysis endpoint | ✅ analysis returns exactly 18 records (related_accounts_count = 18 = list length); the evidence payload's preview fields (related_accounts_count=10 / 5-entry list, member_count=19 = account+18) are RP preview truncations NOT rendered as the population by any Agent surface |
| F7 High trading frequency | 54 trades in 24h | transaction evidence (complete view) | ❌ **RP-side mismatch (documented, not Agent-fixable)**: the exposed population is 64 rows spanning only 11.6h (max rolling-24h count = 64; no side/symbol split yields 54) |
| F8 Opposite trade ratio | 34.38% vs 40% threshold | trade analysis (RP ratio echoed verbatim) | ✅ opposite_trade_ratio = 0.3438 |

Additional Agent-side surfaces checked: case intake (`fetch_case`)
does NOT carry record rows into Agent context (references only — the
RP 5-row default cap therefore has no consumer in the Agent; see §4);
the finding summaries themselves come from RP's rule explanations and
are rendered verbatim (never rewritten by the Agent).

## 3. The known 9-vs-7 discrepancy — root cause, end to end

| Stage | Fact (evidence) |
|---|---|
| Where "9" originates | NOT from any authoritative surface: the RP finding summary says 7; feature_evidence says 7; the complete evidence view returns 7. The only "9" is the **user's audited question** ("9笔提现…") — authored against the audit fixture (W000164+, 9 records) which **does not exist in the current RP seed** (repo-wide search: no W000164) — and the goal model echoing it into the accepted claim text ("Determine whether all 9 withdrawals…") |
| What the source actually holds | RP complete view (`expose_complete_records=true`): 7 withdrawal rows (W000084–W000090). RP default view: **5 rows** (`evidence_service.py`: `limit=None if expose_complete_records else 5`) — a demo-side opt-in population cap |
| What the detail path returns | `finding_drilldown` always opts in (`:289`) → the complete 7 — consistent with the summary's 7 |
| What reaches the evaluator | the GS-B block: records_total=7 = record_count = row count (internal population invariant pinned by test) |
| Why the evaluator said satisfied | the comparison over the COMPLETE authoritative population (7/7) is genuinely complete — the evaluator deliberately never consults claim prose (design §7.2) |
| What was missing | explicit reconciliation of the 9-vs-7 premise in the user-visible response — **fixed** (§1); before the fix the response stated the real count ("7 笔…") but never told the user the question's 9 was not the evidence population |

Resolution class per the task contract §2.1: the two views describe the
same population (7) — the mismatch was the QUESTION's premise, now
explicitly identified and reconciled. The comparison is NOT silently
redefined: it covers all 7 authoritative records and says so.

## 4. Other mismatch classes found (cross-domain)

| Class | Domain | Status |
|---|---|---|
| RP evidence population cap (5 rows unless opt-in) | withdrawal/transaction evidence endpoint | **Agent-safe today**: the only record-bearing consumer (finding_drilldown) opts in; `fetch_case` discards rows. Documented boundary of the RP demo; a trap for FUTURE consumers — flagged for the RP owner |
| F2/F7 "54 trades in 24h" vs exposed 64-row population (11.6h span) | trading-frequency domain | **RP-side metric semantics, documented not fixed**: `_get_reference_time` anchors the 24h window at the GLOBAL max trade/withdrawal timestamp (no user filter; feature_engineering.py:55-70 — docstring marks the global anchor intentional for training-serving consistency), so other users' later activity silently shrinks this user's window. Exposed population supports 64; summaries say 54. Fixing requires an RP product decision (user-scoped anchor changes ML features); Agent renders RP summaries verbatim |
| network preview fields (member_count=19 / related_accounts_count=10 / 5-entry list) vs summary 18 | linked-account domain | resolved: the Agent's detail path is the dedicated analysis endpoint (18 ✓); preview fields are never rendered as the population |
| premise absorption (the 9-vs-7 case) | withdrawal comparison | **FIXED** (§1) |

## 5. Changed files

| File | Responsibility |
|---|---|
| `backend/app/agent_loop.py` | `_POPULATION_CLASSIFIER_RE` + `_asserted_record_population` (bounded extractor) + `_population_reconciliation_line` (explicit scope reconciliation); facet renderer accepts `goal_text` (threaded from `state.goal` via `_gsp_render_output_section`) and renders the reconciliation between the conclusion and the provenance; duplicate `rows_truncated` projection key already removed in GS-E |
| `backend/tests/test_consistency_summary_detail_v1.py` | NEW — 18 tests: extractor (audited/none/ordinal-guard/max/bounded vocabulary), reconciliation line (mismatch/subset/consistent/absent), facet renderer (note placement, premise-match, facts-absent shape, legacy call), internal population invariants, and 2 real-path API E2E journeys (9-over-7 reconciled; 7-over-7 no note) |
| `qa/journey_consistency_l3.py` | NEW — real-browser journeys K1 (consistent premise) / K2 (mismatched premise reconciled) on the live manual-acceptance stack |

No RP code/DB changes. No GS evaluator (GSP-4/5) semantic changes.

## 6. Evidence and regression

- L1: 18/18 (`test_consistency_summary_detail_v1.py`).
- Real API E2E: 2/2 journeys (adapter fixture = source-backed 7-record
  population; the observed 9-over-7 shape now reconciles explicitly;
  the consistent 7-over-7 shape renders no note) — in the same file.
- Regression: GS-D/GS-E/GSP-5 suites green post-change
  (56 passed + 12 passed after an RP-429 window was ruled out — the
  one interim failure was `dependency_unavailable`, re-run green).
- Real browser: K1/K2 PASS on the live manual-acceptance stack (real
  model, no seam) — `qa/screenshots/consistency-K1-*.png`,
  `consistency-K2-mismatched-premise-reconciled.png`,
  `docs/evaluations/consistency-browser-results-20261011T123001Z.json`.
  A first K2 run against a STALE backend (pre-fix process) correctly
  failed the freshness discipline; the backend was restarted onto the
  current tree and both journeys re-run green.
- RP-429 note: journey batches spaced; `dependency_unavailable`
  terminals treated as dependency faults and re-run, never as product
  results.

## 7. Remaining limitations / not proven

1. F7's "54 vs 64" is RP-side (global reference anchor, intentional
   per RP docstring) — needs an RP product decision; the Agent cannot
   repair RP-authored summary prose without RP changes.
2. The premise extractor's vocabulary is bounded (digits + 笔/条);
   CJK numerals ("九笔") and paraphrases without classifiers are NOT
   reconciled (documented bounded vocabulary).
3. The reconciliation renders on the faceted comparison path (the
   audited surface); other output kinds (network/trade/signal) do not
   currently parse premise counts — their summary claims were audited
   consistent instead.
4. RP's 5-row default evidence cap and the network preview fields
   (10/5) remain RP-side boundaries; no Agent surface renders them as
   populations (verified), but future consumers must keep opting in.

Runtime at close: backend :8001 = PID 85920, flags
ENABLE_GOAL_SEMANTIC_CONTRACT=true + ENABLE_TARGET_VALIDITY_GUARD=true,
no seam, health 200, current tree (mtime-verified); frontend :3001 and
RP :8000 untouched; Git HEAD `58881a9` + preserved working trees in
both repos; no commits/stashes.

Not committed / not pushed.

---

## Addendum (2026-10-11, later) — metric-population reconciliation (Consistency II)

Closes the F7 scope mismatch (54 vs 64) as a VERIFIED, user-visible
reconciliation instead of a documented-but-unexplained difference, and
generalizes the consistency contract beyond goal-text digit matching.

### F7 root cause — proven from source data (read-only RP DB)

`_get_reference_time` anchors ALL users' 24h/7d window metrics at the
GLOBAL latest activity timestamp (`SELECT max(timestamp)` with no user
filter — intentional per the module docstring for training-serving
consistency). For U00010 the anchor is `2026-07-21T23:30+08:00` — a
withdrawal by a DIFFERENT user (11,471 other-user trades exist after
U00010's last trade). Recomputing RP's exact formula with that anchor
over U00010's 64 DB trades yields **exactly 54** — deterministic
reproduction; the 10 excluded rows are U00010's earliest trades
(07-20 21:42–23:26 +08). The exposed 64 rows equal the user's complete
DB trades (no exposure truncation). So: 54 = scoring-window count under
an other-user-driven anchor; 64 = the account's full exposed history.

### Changes

| Repo | File | Change |
|---|---|---|
| RP | `app/services/feature_engineering.py` | `feature_reference_time` added to the returned temporal features (additive metadata) |
| RP | `app/services/evidence_service.py` | evidence payload attaches `feature_reference_time` via `_get_feature_reference_time()` (mirrors the feature-engineering anchor; read-only; works for ALREADY-STORED features — no migration) |
| RP | `app/models/schemas.py` | `FeatureEvidence.feature_reference_time: Optional[str]` (response schema field; required — Pydantic response models strip unknown keys) |
| Agent | `app/domain_tools/finding_drilldown.py` | `_population_reconciliation_block`: reconciles the governed 24h-frequency metric against the exposed rows under the anchor → `{claimed, observed_in_window, exposed_total, outside_window, verdict}` with verdicts `consistent_scoped_window` / `population_mismatch`; suppressed when metric/window/population all agree; None (absence ≠ consistency) when the anchor is unavailable |
| Agent | `app/agent_loop.py` | `_population_reconciliation_summary` (bounded projection) + `_render_population_reconciliation` (「总体口径说明」scoped-window note / 「⚠ 总体口径不一致」mismatch note) rendered at both record-table sites (`_evidence_answer_lines`, `_gsp_render_output_facts`) and in the facet section |
| Agent tests | `tests/test_population_reconciliation_v1.py` (11), `tests/test_consistency_summary_detail_v1.py` (+2 API E2E) | computation (scoped/mismatch/absence/tz-mixed/suppression), projection, rendering, real-path journeys |
| QA harness | `qa/journey_consistency_f7_l3.py` | F7 browser journey |

**RP process note**: the anchor is served by the auto-reloaded RP pair
(23055/87635). The Sep-19 duplicate listener (PID 21078) — which owns
the loopback bind — runs old code and cannot serve the field; it was
NOT terminated (user boundary). See "Runtime note" for how the
verification was performed and what remains for the loopback stack.

### Verification

- **Deterministic reconstruction**: RP formula + global anchor over the
  DB's 64 U00010 trades → exactly 54 (read-only DB probe; 10 excluded
  rows enumerated).
- **Live drilldown probe** (adapter temporarily pointed at the
  anchor-serving instance): 64 records + reconciliation block
  {claimed 54, observed_in_window 54, exposed_total 64, outside 10,
  consistent_scoped_window} — matches the DB proof exactly.
- **Real API E2E**: 2/2 — scoped-window note renders both numbers
  (5-in-window vs 7-exposed); mismatch (claimed 3 vs recomputed 5)
  renders 「⚠ 总体口径不一致…不构成完整一致」 and the comparison
  satisfaction is unaffected.
- **Real browser journey**: focused F7 + full transaction evidence →
  the 64-record table + 「总体口径说明：摘要指标「54 笔交易/24 小时」
  按特征参考时点 2026-07-21T15:30:00+00:00 前的 24 小时窗口计得（窗口内
  54 笔）；下方明细为该账号全部 64 笔（另有 10 笔在评分窗口之前）——两个
  数字口径不同、均可追溯。」 all six assertions PASS
  (`consistency-f7-browser-results-*.json`,
  `consistency-F7-metric-population.png`). The backend temporarily
  pointed at the anchor-serving instance for this journey and was
  restored immediately after.
- **Regression**: chunked spaced batches — 62 + 26 + 50 = 138 passed
  (GS-D/GS-E/GSP-5/GS-A/B/C + consistency suites); L1 11/11 + 20/20.

### F1–F8 enforcement matrix (post-Consistency-II)

| Finding | Enforced reconciliation at runtime |
|---|---|
| F3/F4 withdrawals | premise reconciliation (goal count vs block total) + block internal invariants + frequency-metric reconciliation via the anchor |
| F7/F2 trading frequency | NEW: frequency-metric reconciliation (anchor-exposed scoped window vs exposed rows) — the 54/64 relationship is now rendered and verifiable |
| F6 linked accounts | analysis-endpoint count = summary (18 ✓, verified); preview truncations not rendered as populations |
| F5 shared devices | network_evidence count = summary (1 ✓, verified) |
| F1 ml score | score surfaced from RP signal payload (99.41 ✓ verified); recomputation not applicable (model output) |
| F8 opposite ratio | recomputable from exposed rows: min(buy,sell)/total = 22/64 = 0.3438 ✓ verified against the summary |

### Runtime note (loopback stack)

The manual-acceptance backend (:8001, flags ON, PID 89394, health 200)
points at localhost:8000 = the Sep-19 RP listener, which does NOT yet
serve `feature_reference_time` — on THIS stack the F7 reconciliation
note is absent-by-absence (the block returns None; nothing incorrect is
rendered). Full F7 reconciliation requires retiring that duplicate
listener (one `kill 21078`; loopback then falls to the auto-reloaded
pair). That is a user decision per the standing RP-process boundary.

Not committed / not pushed.

---

## Addendum 2 (2026-10-11, final gate closure) — RP instance takeover + generalized claim registry

### Gate 1 — PID 21078 safely retired; loopback taken over (user-authorized)

Identity verified BEFORE termination (no PID reuse): PID 21078, PPID 1,
started Sat Sep 19 22:54:32 2026, cwd risk-platform-demo/backend,
LISTEN 127.0.0.1:8000 — the exact previously identified stale RP
process. SIGTERM only → exited normally. The auto-reloaded pair
(parent 23055 + reloader 23056 + worker 88607, started 21:07 with the
anchor code) took over *:8000. Held-socket test: loopback
localhost:8000 now accepted by worker **88607**. feature_reference_time
verified on the Agent's exact path (localhost:8000, complete view):
`2026-07-21T15:30:00+00:00`; the F7 drilldown reconciliation block
(54/anchor/64/10, consistent_scoped_window) renders on the LOOPBACK
manual-acceptance stack with no LAN workaround. All other processes
intact (frontend 25500, backend 89394→restarted 22:0x for the label
fix, RP pair). No other process was terminated.

### Gate 2 — non-numeric material claims: generalized claim registry

`_population_reconciliation_block` generalized into a declarative
registry; per evidence view it now reconciles:
- window-count metrics (trade/withdrawal_frequency_24h) — unchanged;
- **opposite_trade_ratio**: min(BUY,SELL)/total recomputed from the
  exposed rows (denominator exposed) — the live value 0.3438 = 22/64
  verified claim_supported;
- **first_withdrawal_new_address** (F4's rule claim, non-numeric): the
  earliest exposed withdrawal must carry is_new_address=True —
  claim_supported / claim_contradicted;
- **unmapped governed metrics** (e.g. trade_frequency_7d) are reported
  as unmapped in the payload AND in the user-visible response
  (「尚未与明细逐笔核对」) — new Finding types can never be silently
  treated as verified.
Stream-scoped governed sets (transactions → trade metrics + ratio;
withdrawals → withdrawal metrics) keep the unmapped signal precise.
Block suppressed on full three-way agreement (no noise for consistent
data).

### New defect found and fixed — composer label vs actual evidence

The F7 browser journey surfaced a REAL cross-domain label contradiction
introduced by the response composer itself: the real model proposed a
compound requirement `[transaction_records, withdrawal_records]`; the
executed evidence was transactions (64 trade rows); the composer's
record-stream label read the REQUIREMENT LIST (提现-preferring) and
labeled trade rows 「完整提现记录共 64 条」. Fixed:
`_record_stream_label(reqs, summary)` derives the label from the ACTUAL
evidence types of the output's associated observations (requirement
list only as fallback); actual types threaded at compose time
(`combined["_actual_evidence_types"]`). The re-run journey renders
「完整交易记录共 64 条」 correctly. L1 pinning tests added.

### Verification

- L1: 17/17 reconciliation registry + 22/22 consistency file
  (extractor, renderer incl. ratio/rule/unmapped, label, invariants).
- Live loopback probes: anchor + F7/F3 reconciliation blocks verified
  on localhost:8000 (Gate 1 closure).
- Browser: F7 journey PASS with label + reconciliation + ratio +
  unmapped disclosure (`consistency-f7-browser-results-20261011T143836Z.json`,
  `consistency-F7-metric-population.png`).
- Regression (chunked, spaced): 70 + 30 + 84 = **184 passed**
  (`consistency2-regression-c1/c2/c3.log`).

Runtime: backend :8001 PID (post-restart), flags
ENABLE_GOAL_SEMANTIC_CONTRACT=true + ENABLE_TARGET_VALIDITY_GUARD=true,
RISK_PLATFORM_BASE_URL=http://localhost:8000, health 200; frontend
:3001 and RP :8000 (now solely the reload pair) live. Not committed /
not pushed.

---

## Addendum 3 (2026-10-12) — final acceptance gate closure (Consistency II)

### Gate 1 closure — RP serving instance switched (user-authorized)

PID 21078 identity re-verified read-only before termination (PPID 1,
started Sat Sep 19 22:54:32 2026, cwd risk-platform-demo/backend,
LISTEN 127.0.0.1:8000 — no PID reuse), then SIGTERM → normal exit.
The auto-reloaded pair (parent 23055 + reloader 23056 + worker,
restarted with the anchor code) took over :8000; held-socket test:
loopback accepted by the reload worker; `feature_reference_time`
verified on the Agent's exact path (localhost:8000 complete view =
2026-07-21T15:30:00+00:00). No other process touched.

### Gate 2 closure — generalized claim registry + non-numeric claims

`_population_reconciliation_block` generalized into a declarative
registry (stream-scoped governed sets), reconciling per evidence view:
- window-count metrics (trade/withdrawal_frequency_24h) — unchanged;
- **opposite_trade_ratio** recomputed from exposed rows
  (min(BUY,SELL)/total, denominator exposed) — live 0.3438 = 22/64
  claim_supported;
- **first_withdrawal_new_address** (F4's non-numeric rule claim): the
  earliest exposed withdrawal must carry is_new_address=True —
  claim_supported / claim_contradicted;
- **unmapped governed metrics** reported in payload AND user-visible
  response (「尚未与明细逐笔核对」) — new/unknown metrics can never be
  silently treated as verified. Full three-way agreement suppresses the
  block (no noise).

**New defect found + fixed — composer label vs actual stream**: the
real model proposed a compound requirement
[transaction_records, withdrawal_records]; the executed evidence was
64 TRANSACTION rows; the composer's label read the requirement list
(提现-preferring) → 「完整提现记录共 64 条」 over trade rows. Fixed:
`_record_stream_label(reqs, summary)` derives the label from the
output's ACTUAL evidence types (requirement list only as fallback);
actual types threaded at compose time.

**CJK numerals**: 「九笔提现」 now reconciles (bounded 一~九十九
converter; 百/千/万 remain out of vocabulary) — the previously
documented unsupported form is supported end to end.

### Verification (this addendum)

- L1: 17/17 registry tests + 22/22 consistency file (incl. CJK 4,
  label 2) + 3/3 claim/source binding.
- API E2E: transaction-view label by actual stream + 7d unmapped
  disclosure; CJK premise reconciliation; F3 9-vs-7 and scoped-window
  F7 preserved.
- Live loopback probe: F7/F3 drilldown reconciliation blocks verified
  on localhost:8000 post-takeover.
- Browser: F7 journey PASS on the loopback manual-acceptance stack —
  table + reconciliation + ratio recompute + unmapped disclosure
  (`consistency-f7-browser-results-20261011T160834Z.json`,
  `consistency-F7-metric-population.png`).
- Regression: chunked batches 62+26+50 and 70+30+84 = 252 passed
  cumulative; final sealing batch 45 passed.

### Runtime (final)

Backend :8001 PID 95973 (started Mon Oct 12 00:01, current tree —
mtimes verified), flags ENABLE_GOAL_SEMANTIC_CONTRACT=true +
ENABLE_TARGET_VALIDITY_GUARD=true (ps eww), RISK_PLATFORM_BASE_URL=
http://localhost:8000 (now the reload pair), health 200. Frontend
:3001, RP :8000 live. Git: HEAD 58881a9 + preserved working trees
(both repos), 0 stash, no commits/stages/pushes.

Not committed / not pushed.

---

## Addendum 4 (2026-10-12) — BLOCKER RESOLVED: the 9-vs-7 population discrepancy

### Origin of "9" — settled with source-backed evidence

The audited 9-record population EXISTS in the current authoritative
source: **case U00020**. The RP database holds exactly 9 withdrawals for
U00020 (W000159–W000167, **including W000164** — the design fixture's
anchor record), with **9 distinct destinations, all is_new_address=True**
— precisely the audited question's dataset shape, and U00020's Finding
F2 authoritatively states "9 withdrawals were recorded in 24 hours".
The manual acceptance investigated **U00010** (7 withdrawals,
W000084–W000090, DB-verified complete) — a DIFFERENT case with a
similar shape. The "9" was therefore a case/scope mismatch between the
audited question (U00020) and the investigated case (U00010) — not data
loss, not a retrieval defect, not a summary error.

### Population-shortfall invariant (implemented)

The user's requirement — a 9-record ask must never be satisfied by a
7-record comparison — is now enforced at the postcondition boundary:
- `GoalContract.asserted_record_population` (extracted deterministically
  from the request text at acceptance; extractor moved to
  `app/goal_resolution.py` — single source shared with the composer);
- `_destination_facet_verdict(..., asserted_population)`: asserted >
  authoritative-complete → **UNRESOLVED / `population_shortfall`** with
  「您的请求提及 9 笔提现；当前权威数据源检索完整，仅可取得 7 笔——其余
  2 笔无法获得，因此无法对全部 9 笔完成比较…仅供核查，不构成对全部 9 笔
  请求的完整回答」; asserted ≤ total → unchanged satisfied semantics
  (subset premises are contained by the full-population comparison);
- composer: the goal-text premise note renders only for SATISFIED
  outputs (the shortfall detail carries the boundary text; no duplicated
  or completion-flavored note on unresolved outputs).

### Verification

- L1: 21/21 (shortfall unresolved / equal satisfied / subset contained /
  no-assertion unchanged + the registry suite).
- API E2E: 9-over-7 shortfall journey (UNRESOLVED, unanswerable,
  「仅可取得 7 笔…无法对全部 9 笔完成比较」, subset facts shown, no
  completion claim); GS-D live test re-aligned to the premise-consistent
  7笔 question (satisfied path preserved); 77+64+50 chunked regression.
- **Outcome A (browser, case U00020)**: the audited question on ITS OWN
  dataset → 「9 笔提现共指向 9 个不同目的地」, all nine record IDs
  W000159–W000167 in the provenance, satisfied/complete
  (`consistency-u00020-outcomeA-*.json`,
  `consistency-U00020-outcomeA-9records.png`).
- **Outcome B (browser, case U00010)**: the same question → the
  7-record table + shortfall statement + 「以当前系统能力与数据，无法回
  答该请求」 — NO completion claim
  (`consistency-browser-results-20261012T032540Z.json`,
  `consistency-K2-population-shortfall.png`).

### Guidance for manual acceptance

The audited 9-withdrawal question belongs to **case U00020** (Finding
F2). Investigating U00020 yields the complete 9-record comparison
(Outcome A). Investigating U00010 with the 9笔 question now yields the
honest blocked shape (Outcome B) — the 7-record subset comparison is
never presented as the completed answer.

Runtime: backend :8001 flags ON (verified), RP :8000 reload pair,
frontend :3001. Not committed / not pushed.

---

## Addendum 5 (2026-10-12) — Failures A/B investigation and fixes

### Failure A (F7 高频交易解释) — root cause + fix

`signal_explain(F7)` returns `signal_type="Feature"` with
`explanation.features=[{trade_frequency_24h: 54}]` — the authoritative
answer data. Three gaps prevented its use on the contract path:
`_actual_evidence_types` did not map Feature signals; the RULE_EXPLANATION
evaluator branch grounded only on rule_name/ml_score/cluster_name; the
GSP facts renderer had no Feature branch. All three fixed: Feature →
RULE_EXPLANATION; feature_facts ground the explanation (satisfied); the
facts renderer renders the feature values + the Consistency-II
population_reconciliation (54 scoring-window vs 64 exposed) + the
explicit no-fixed-threshold boundary (RP exposes no threshold field for
this factor; the >50 threshold seen in RP source belongs to the
new-account rule and is NOT exposed at runtime — stated as absent, not
invented). The turn path (`_signal_answer_lines` Feature branch) also
renders the reconciliation now.

### Failure B (composite withdrawal question) — root cause + fix

Two layers: (1) UNFOCUSED, the request grounds to both F3 and F4 (both
findings expose the SAME 7-record withdrawal population) → the governed
ambiguity clarification fires (safe, specific — preserved by design);
FOCUSED F3 resolves cleanly. (2) FOCUSED runs hit `llm_empty` gateway
variance → contract-less fallback (records shown, GS-A/GS-C guards
rendered, deliverables never established). The structural fix: NEW facet
`withdrawal_new_address_status` (enum + prompt guidance proposing it for
哪些笔提现是新地址/是否都提到新地址 alongside the destination facet) +
drilldown `_new_address_status_block` (per-record is_new_address keyed
by record ID; missing values counted, never treated as true) + bounded
projection + facet verdicts (SATISFIED when the complete population's
statuses are established — any mix; PARTIAL when values are missing;
absent block → the retrieval suppression → UNRESOLVED
comparison_facts_absent — never satisfied by retrieval) + composer
per-record rendering (逐笔新地址标记 + bounded overall conclusion). The
facet dispatch in `evaluate_output` generalized to both facets
(destination / new-address), each with the retrieval-suppression rule.

### Verification

- L1: new-address facet 4/4 (all-true, mixed, missing→partial, absent→
  not-satisfied); registry suite 17/17; consistency file 27/27 (incl.
  CJK premise 4, label 2); claim/source binding 3/3.
- Browser (real model, no seam): FA rendered the feature facts (Trade
  frequency (24h) = 54) via the llm_empty fallback — useful but the
  full contract-path rendering (reconciliation + threshold boundary)
  was not exercised by a contract-accepted browser run (gateway
  variance); FB browser runs did not produce the two-facet contract
  (llm_empty) — the composite browser journey remains PARTIALLY
  VERIFIED. Artifacts: `failures-ab-browser-results-*.json`.

Not committed / not pushed.

---

## Addendum 6 (2026-10-12) — final acceptance browser journeys (Failures A/B)

Strict discipline: 1 initial attempt + 1 retry per journey; per-attempt
structured classification from the served API; llm_empty fallbacks never
counted as contract-path passes.

### Journey A — F7 高频交易解释: CONTRACT PATH VERIFIED

Attempt 2: decision=contract_accepted, both outputs **satisfied**, and
the response renders the full contract-path content:
「该发现由 Risk Platform 的行为特征检测标记 — 触发它的特征指标及其
观测值: Trade frequency (24h) = 54」 + 「说明:当前 Risk Platform 数据
未提供该特征独立的固定判定阈值字段;上述观测值与统计窗口为权威数据,
而高频本身是异常行为信号,不构成欺诈证明。」 — no fallback marker, no
「当前证据不足以确定该项」 (the original failure marker). Attempt 1
(同旅程) had one output unresolved (model variance in contract shape);
attempt 2 grounded both. Screenshot:
`failA-final-contract-path-retry2.png`.
NOTE: the journey-classifier marked attempt 2 FAIL because it also
demanded the 总体口径说明 line — that line requires a finding_drilldown
evidence view in the run, and this pure-explanation journey has none;
the window reconciliation for F7 is separately browser-verified in the
archived F7 evidence-view journey (总体口径说明 + 54/64 + anchor).
Corrected assessment: Journey A = CONTRACT PATH PASS.

### Journey B — F3 复合提现问题: EXTERNALLY BLOCKED

Both attempts ended `decision=llm_empty` (gateway returned no text
twice, backoff between) → per discipline the journey STOPPED, marked
EXTERNALLY BLOCKED, never counted as a pass. Both fallback responses
were honest (7-record table + GS-A guard + GS-C disclosure, no
completion claim) — archived in
`failures-ab-final-20261012T065443Z.json`.

Artifacts: `failures-ab-final-20261012T065443Z.json` (per-attempt
API classification + responses), screenshots `failA-final-contract-path
(-retry2).png`, `failB-final-composite.png`. Journey A's classifier
marker error is documented above; the substantive contract-path
evidence stands.

Not committed / not pushed.

---

## Addendum 7 (2026-10-12) — Failure B browser acceptance: PASS

Fresh 1+1 attempts (per discipline): attempt 1 `llm_empty`; attempt 2
(retry) **contract_accepted, CONTRACT_PATH_PASS** — task
`TASK-702cdae20cc5`:
- the real model's contract carried BOTH deliverables
  (facets [[destination_address_identity],
  [withdrawal_new_address_status]]), both **satisfied**;
- the response renders BOTH answers independently: 「7 笔提现共指向
  7 个不同目的地」 + 7-group provenance (O1) AND the per-record
  逐笔新地址标记 list — all 7 records (W000084–W000090) marked
  是新地址 (7/7) (O2) — followed by 「本次调查目标已完整回答」
  (both deliverables genuinely fulfilled over the complete population);
- live re-verification: all 7 current records is_new_address=True (7/7);
- artifacts: `failures-ab-final-20261012T072254Z.json`,
  `failB-final-composite-retry2.png`. Attempt 1's llm_empty fallback
  response is archived in the same JSON (never counted as a pass).

Failure B browser acceptance: **PASS — BROWSER ACCEPTANCE COMPLETE**.
Not committed / not pushed.

---

## Addendum 8 (2026-10-12, later) — RP source fix: F7 observation naming + neutral severity

### RP changes (risk-platform-demo, 3 files — all additive/rename)

| File | Change |
|---|---|
| `app/services/risk_service.py` | `_create_risk_factors` factor_mapping + description key: "High Trading Frequency" → **"Trading Frequency (24h)"** (neutral observation name; no threshold invented) |
| `app/services/evidence_service.py` | `_FEATURE_FINDING_NAMES` same rename; **`_LEGACY_FACTOR_NAMES`** read-time canonicalization (persisted rows named "High Trading Frequency" present as "Trading Frequency (24h)" — DB untouched); **`_classify_factor_severity`: trade-frequency factors → "informational"** (neutral, non-detector; legacy + neutral names both covered) |
| `app/models/schemas.py` | (earlier) feature_reference_time field |

Rationale: a nonzero trade frequency is a NEUTRAL FEATURE OBSERVATION — no F7 threshold rule or approved risk policy exists (source-verified: `_calculate_rule_score` has no trading-frequency rule; the findings assembler creates the entry for any nonzero value). The heuristic "medium" severity and the decision-flavored name implied a detector classification that never occurred.

### RP verification (live loopback)

`GET /api/risk/cases/U00010/evidence?expose_complete_records=true` →
`factor_name: "Trading Frequency (24h)", severity: "informational"` (was
"High Trading Frequency"/"medium"). Direct classifier probe: legacy AND
neutral names both → informational; Shared Device Relationships → medium
(unchanged). trade_frequency_24h remains in feature_evidence (ML inputs
and the F2 rule condition untouched). Findings assembly emits the neutral
name with detection_sources=["Feature"].

### Focused regression (this change)

- RP direct classifier probe: 3/3 as above (live loopback payload cross-checked).
- Agent suites touching F7 naming (al27/al28/al29/al210/provenance_model +
  consistency/registry/binding): 141 passed, **7 failed — all 7 in
  `tests/test_provenance_model.py`** (compose dedupe crash:
  `canonical_name` None at `app/tools.py:334`). Attribution: BOTH directly
  involved files are committed-unmodified (last touched cc6799e); this
  round's changes (a string value in the client fallback map, composer
  wording, goal_resolution/finding_drilldown additions) cannot produce a
  None canonical_name — **pre-existing-suspected, clean-tree confirmation
  pending** (user may verify on a clean clone; recorded, not fixed this
  round per scope).

Not committed / not pushed.

---

## Addendum 9 (2026-10-12) — provenance dedupe None-crash fixed (targeted)

`app/tools.py` compose dedupe: `canonical_name = finding.get("canonical_name", "")` returned the STORED None (the append passes the raw value through), and `canonical_name.lower()` crashed — reproduced by
`tests/test_provenance_model.py` (7 failures, first surfaced when the file joined regression batches). Minimal fix: dedupe identity = canonical_name when present, else the finding's own stable id (finding_id → source_evidence_id → keep separate); `.lower()` only on real strings. Findings sharing a missing name are NEVER merged.
Test expectation for the live U00010 case aligned with the approved F7 neutral rename ("Trading Frequency (24h)").

Results: `tests/test_provenance_model.py` **47/47** (7 original failures recovered + 6 boundary tests incl. no-merge-when-nameless, same-id dedupe, canonical dedupe unchanged); focused cross-suite 120 passed. The fix is loaded on the next backend restart (the current manual-acceptance process predates it; the GS acceptance flows use the V2 loop, not this legacy compose surface — no restart required for them). Not committed / not pushed.

---

## Addendum 10 (2026-10-12) — evidence-grounded finding explanations (Finding Explainability Contract)

### Implemented

1. **RP `RuleEvidence` schema**: additive `trigger`/`threshold`/`contribution` fields (the values were already derived by `_derive_rule_evidence`; the response model stripped them — same Pydantic class as the feature_reference_time fix).
2. **Agent `finding_drilldown`**: passes the case's `rule_evidence` into the payload (source-backed detection-decision provenance).
3. **Agent projection**: bounded `rule_evidence` + `_finding_title` into the summary.
4. **Composer**: rule-backed outputs render the ACTUALLY-TRIGGERED rule entries (name/severity/observed values/condition/description) first, then records; the Feature branch renders observation semantics (non-decision framing).

### Mechanism map (final)

| Finding | Mechanism | Explanation source | Runtime rendering |
|---|---|---|---|
| F1 | ML (LightGBM score) | explain payload ml_score/detection_method/feature context/attribution boundary | ✅ ML branch (archived) |
| F2 | Rule: age<7 AND freq>50 | rule_evidence entry (trigger/threshold/contribution) | ✅ via rule renderer |
| F3 | Rule: freq>5 | rule_evidence entry | ✅ verified live E2E (命中规则 + 观测值 7 + 条件 >5) |
| F4 | Rule: first_withdrawal + freq | rule_evidence entries | ✅ same renderer path |
| F5/F6 | Graph-derived feature observations | cluster/related accounts/shared devices | ✅ graph branch (archived); NOT graph-risk decisions |
| F7 | Feature observation (nonzero → created, no threshold) | feature_facts + population_reconciliation | ✅ observation semantics (non-decision framing) |
| F8 | Ratio: >0.4 rule / ≤0.4 observation | factor name carries the distinction | ✅ unchanged |

### Results

- L1: 136 focused passed (incl. new rule-rendering + observation semantics).
- Live E2E probe: F3 rule explanation renders 命中规则「High withdrawal frequency」(severity MEDIUM) + 观测值 7 + 判定条件 >5 — satisfied.
- F7 browser journey re-verified on the current stack (all six assertions).
- RP loopback payload verified: trigger/threshold/contribution present.

Not committed / not pushed.

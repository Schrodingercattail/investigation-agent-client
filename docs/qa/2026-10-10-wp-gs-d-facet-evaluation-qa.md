# WP-GS-D — GSP-4 Destination-Identity Facet Evaluation — QA Record

**Date**: 2026-10-10
**Status**: IMPLEMENTED + VALIDATED — L1 25/25, real API E2E 6/6,
real-browser journeys 2/2 PASS; partial-identity browser journey
delivered by the 2026-10-11 acceptance supplement below (3/3 PASS +
7/7 state cross-checks) — all three evidence shapes now browser-
covered. Regression: GSP1–7 + al351 192/192, GS-A/B/C/D in-process
141/141 (spaced runs), CG-1/2/3 60/60, CG-4 11/11 and CG-5 6/6 in
their own canonical configs. Not committed / not pushed.
**Approved basis**: design v1 §6 (layer responsibilities), §7.1/§7.2
(facet verdict table), §7.4 (PARTIALLY_SUPPORTED consumer mapping),
§8 (address-identity rules), §14 (journey acceptance);
implementation plan §4.4 (WP-GS-D).

## Summary and exact scope

GS-D closes the chain GS-B (facts) → GS-C (contract) → **GS-D
(verdict)**: when an accepted `RequiredOutput` declares the
`destination_address_identity` facet, GSP-4 decides the output's
satisfaction from the GS-B `destination_identity` fact block — the
comparison is answered by comparison FACTS, never by retrieval.
Verdict rows (design §7.2, implemented in this order):

| Condition | Verdict | Reason | User-visible conclusion |
|---|---|---|---|
| block present, all identities present, `unique_destinations == 1` | SATISFIED (negative polarity) | `destination_comparison_complete` | 「全部 N 笔提现指向同一目的地（记录中的目的地标识相同：dest-1）。」 |
| block present, all identities present, `unique_destinations >= 2` | SATISFIED (positive) | `destination_comparison_complete` | 「N 笔提现共指向 K 个不同目的地（记录中的目的地标识）。」 |
| block present, `records_without_identity > 0` (incl. the all-missing degenerate subset) | PARTIALLY_SUPPORTED | `destination_identity_partial` | 「M/N 笔记录缺少目的地标识，仅能对可比子集给出分组结论；整体的比较尚未建立。」 |
| block present-but-inconsistent (counts/group cardinality/membership cannot be verified) | fall-through with retrieval-suppression → UNRESOLVED | `comparison_facts_absent` | unverifiable blocks never ground conclusions |
| block absent + retrieval EMPTY (authoritative zero) | inherited empty-complete negative (SATISFIED, negative) | `records_empty_complete_scope` | 「未检索到匹配的提现记录（检索范围完整），因此不存在可比较的目的地标识。」 (facet renderer) |
| block absent + retrieval SUCCEEDED | UNRESOLVED — retrieval NEVER satisfies a faceted output | `comparison_facts_absent` | 「未获得完成目的地比较所需的地址事实；检索结果仅作为证据记录。」 |

Row-4/row-5 mapping note: design §7.2 row 4 ("block present but
empty/inadequate") maps onto the real tool contract as "block absent +
outcome empty" — GS-B's tool returns NO block when a payload holds no
withdrawal records, and an authoritative zero inherits the
empty-complete negative exactly as the design specifies. Row 5
("block absent + retrieval succeeded") is unreachable on the current
real tool path (GS-B attaches the block to every successful withdrawal
retrieval — a deliberate invariant); it is implemented and L1-tested
as defense-in-depth, and the anti-false-completion rule it encodes is
the one the audited defect required.

Polarity semantics: negative/positive record the FACTS' shape (one
group vs K groups over recorded identifiers) — never that a risk
hypothesis is true. Conclusions are worded over 记录中的目的地标识
only; GS-B's exact, case-sensitive trim equality is preserved (no
folding, no `is_new_address` inference, no on-chain claims).

## Changed files

| File | Change |
|---|---|
| `app/goal_postconditions.py` | facet verdict function `_destination_facet_verdict` (§7.2 rows 1-3) + `_destination_block_consistent` structural gate + reason codes (`destination_comparison_complete`, `destination_identity_partial`, `comparison_facts_absent`); `evaluate_output` integration: a faceted output's verdict comes from the block; rows 4/5 fall through to the existing records postcondition with the retrieval-suppression rule (a SATISFIED-positive retrieval verdict on a faceted output is replaced by `comparison_facts_absent`; empty-complete negative and partial/unresolved survive). GSP-5 `derive_goal_outcome` UNTOUCHED |
| `app/agent_loop.py` | `_destination_identity_summary`: groups now carry the recorded destination `address` (design §8 equality-verifiability; fixes the pre-existing empty-parens display in the destination table); NEW `_render_destination_facet` + `_facet_record_table`: the faceted output renders conclusion + per-group provenance + the evidence table with the 目的地 column; comparison-facts-absent renders the boundary with records as evidence only; row-4 empty-negative renders the no-records comparison boundary. `_gsp_render_output_section` calls the facet renderer first; standard branches unchanged for all other outputs |
| `tests/test_cg_gs_d_facet_evaluation_v1.py` | NEW — 25 L1 tests |
| `tests/test_cg_gs_d_api_e2e_v1.py` | NEW — 6 real-HTTP API E2E tests |
| `qa/journey_cgsd_l3.py` | NEW — real-browser journeys J1/J2 |

GS-B's grouping algorithm, GS-C's detector/admission/disclosure, the
GSP-5 outcome rules, and all loop/terminal semantics are unchanged.

## L1 results — 25/25 PASS (`tests/test_cg_gs_d_facet_evaluation_v1.py`)

The twelve mandated semantics, each pinned to a test (names abbreviated):
1. 9 records / 9 distinct → SATISFIED positive, `…共指向 9 个不同目的地`
2. 9 records / 1 destination → SATISFIED negative, `全部 9 笔…同一目的地`
3. mixed partition (4+3+2) → SATISFIED positive, `共指向 3 个不同目的地`
4. partial identity (3/12 missing) → PARTIALLY_SUPPORTED `3/12 笔记录缺少目的地标识`
5. comparison facts absent → UNRESOLVED `comparison_facts_absent` (retrieval suppression proven against the records branch that WOULD have returned satisfied-positive)
6. wrong-target block (associated to another output) → PENDING `no_associated_evidence`; verdict anchored to the associated observation only (indexes [0])
7. inconsistent counts / group cardinality / member duplication / non-integer counts → all rejected → `comparison_facts_absent`
8. association + provenance boundaries (see 6; provenance rendering is composer-level, verified in E2E/browser)
9. identical repeat preserves the verdict; absent-facts repeat stays unresolved; CONFLICTING blocks → `conflicting_observations`, never satisfied
10. facet survives coverage re-admission (GSP-7 contract immutability at L1; loop-level in the re-plan E2E)
11. plain retrieval outputs unaffected (satisfied-by-retrieval preserved; unknown future facets ignored)
12. GoalOutcome mapping through the UNCHANGED derivation: [satisfied]→complete, [partial]→partial, [satisfied, partial]→partial, [satisfied, unresolved]→partial, [unresolved]→unanswerable

## Real API E2E — 6/6 PASS (`tests/test_cg_gs_d_api_e2e_v1.py`)

In-process TestClient, real app + real loop + real ExecutorV2 +
finding_drilldown + GS-B block + projection + GSP-3/4/5 + composer +
Activity; ONLY the model boundary scripted (approved seam); where the
RP payload shape must be fixture-controlled the ADAPTER boundary is
patched (the tool/block/evaluation/composer stay production code).
Flag hygiene: `ENABLE_GOAL_SEMANTIC_CONTRACT` set via monkeypatch —
set AND restored per test.

| Scenario | Journey | Verdict surface | Result |
|---|---|---|---|
| valid facet + FULL comparison facts | LIVE RP (no fixture): focused F3 → 7 withdrawals / 7 distinct | SATISFIED positive; GoalOutcome complete; response = conclusion + group provenance + 目的地 table; no coverage mark | PASS |
| valid facet + PARTIAL identity | adapter fixture: 9 withdrawals, 3 blank addresses | PARTIALLY_SUPPORTED 3/9; GoalOutcome partial; 已部分回答 + M/N boundary; subset groups traceable (dest-1…, W000160…); no whole-request claim | PASS |
| tool success + ALL identities missing (insufficient facts) | fixture: 9 withdrawals, all blank | PARTIALLY_SUPPORTED 9/9; overall partial; never a same/different conclusion | PASS |
| authoritative ZERO withdrawals | fixture: empty withdrawal stream | row-4 inherited negative; 「不存在可比较的目的地标识」; contract-relative completion legitimate FOR THAT CLAIM | PASS |
| RE_PLAN identical repeat | fixture all-blank + plans×3 + CONTINUE×3 | convergence completes the loop; facet stays PARTIALLY_SUPPORTED; completed terminal + partial outcome; no whole-request claim | PASS |
| retrieval-only path intact | plain contract + retrieval goal text | satisfied-by-retrieval preserved; complete outcome renders 本次调查目标已完整回答; no facet boundary text | PASS |

## Real browser E2E — 2/2 PASS (`qa/journey_cgsd_l3.py`)

Canonical stack 3001→8001→8000; backend flags ON + seam
`/tmp/cgsd_session.json`; real Chrome; must/never checks scoped to the
newest conversation card (the History sidebar legitimately carries
older wording from earlier rounds).

- **J1 full facts** (live RP, focused F3): response shows 「7 笔提现共指向
  7 个不同目的地（记录中的目的地标识）。」 + per-group provenance
  (dest-1…dest-7 with full recorded addresses and record IDs) + the
  evidence table with the 目的地 column; the contract-relative
  completion claim renders (legitimate — the comparison was answered);
  no coverage disclosure, no guard wording. Screenshot
  `qa/screenshots/cgsd-J1-full-facts-conclusion.png`.
- **J2 facts unavailable** (live RP, focused F1 — transaction-stream
  finding): the tool honestly reports the withdrawal stream as
  unsupported; the facet output stays PENDING; response shows 「该项尚未
  产生可判定的结果」 + 「整体结果: 以当前系统能力与数据，无法回答该请
  求」; task card failed (bounded replan failure); NO comparison
  conclusion, NO whole-request claim. Screenshot
  `qa/screenshots/cgsd-J2-facts-unavailable.png`.
- **Partial-identity browser journey: NOT RUN (environment-limited).**
  Live RP holds no withdrawal set with missing identifiers (F3/F4 are
  7/7 complete). The partial shape is exercised at the real HTTP API
  layer through the SAME production code (adapter-fixture E2E above);
  per the environment-honesty rule this limitation is stated rather
  than simulated in the browser.
- Structured results: `docs/evaluations/wp-cgsd-browser-results-20261010T152544Z.json`.

## Data / State Lifecycle (verified against code + runs)

- **Comparison fact block — produced**: `finding_drilldown`
  (`_destination_identity_block`, deterministic, over the normalized
  withdrawal records of the RP evidence payload) → attached to the
  tool result → projected into the observation summary by
  `_destination_identity_summary` (bounded: counts + groups with
  `destination_ref`/`address`/`record_ids` + normalization note; no
  raw record payload duplication). Observations live in
  `AgentLoopState.observations`.
- **Where it persists**: the loop persists the state document via
  TaskStoreV2 (canonical SQLite store
  `backend/data/investigation_agent_v2.db`; per-test temp stores in
  test runs). The projected block therefore persists INSIDE the stored
  observations — no new storage, no migration. NOT every raw tool
  field is persisted: the projection is a bounded whitelist (e.g. the
  full unprojected record set stays in the tool-result/artifact
  surface, not in the observation summary).
- **Facet satisfaction verdict — produced/consumed**: produced by
  `evaluate_output`/`_destination_facet_verdict` (pure, GSP-4);
  applied by the loop's SINGLE write point (`apply_postcondition`) to
  `goal_contract.outputs[i].satisfaction/polarity/detail`; consumed by
  the composer (per-output facet section), the Activity projection
  (`goal_contract.model_dump()` — satisfaction/polarity/detail/facets
  served verbatim), and `derive_goal_outcome` (GSP-5, unchanged
  rules). `reason_code` is telemetry-only (`postcondition.evaluated`
  event); the user-safe text rides `detail`.
- **GoalOutcome — derived, not independently stored**: the composer
  sets `state.goal_outcome` at compose time AND the Activity projection
  re-derives it deterministically from the persisted per-output
  satisfactions + agent_status — both readers of the same persisted
  inputs, no second source of truth.
- **Refresh / history / follow-up turns**: `GET /api/v2/tasks/{id}/activity`
  re-serves the persisted contract (outputs incl. facet verdicts) +
  derived outcome; `GET /api/v2/tasks/{id}` serves `task_response_text`
  from the investigation session read model (the loop's composed
  response is mirrored there by `_record_loop_result`) — the same
  surface the browser renders on refresh (verified: J1/J2 browser text
  matches the served response; API E2E asserts on the served Activity).
  Follow-up conversation turns read the same session/context state;
  GS-D adds no new read or write path.
- **No new persistence introduced** — the block rides the existing
  observation projection, verdicts ride the existing contract fields;
  this is why no migration or new store is needed.

## Regression (spaced runs — RP 429-aware)

| Family | Command scope | Result |
|---|---|---|
| GSP1–7 + al351 | 9 files | **192 passed** |
| GS-A×2 + GS-B | 3 files | **28 passed** |
| GS-C×2 + GS-D×2 | 4 files | **53 passed** |
| CG-1/2/3 (L1+API) | 4 files | **60 passed** |
| CG-4 (own canonical config: flags ON + `/tmp/cg4_session.json` seam) | 11 scenarios | **11 passed** |
| CG-5 trials (own config: flags ON + `/tmp/cg5_session.json` seam) | 6 trials | **6 passed** |

Two transient dependency-fault windows occurred during the round
(RP 429 burst — `Findings dependency unavailable: Risk Platform
rejected request (429)`): affected runs showed
`dependency_unavailable`/`blocked` terminals across MULTIPLE suites'
journeys and cleared after backoff (per runbook: recorded as
dependency faults, retried, never counted as product results). One
mixed run of 14 failures was entirely this mechanism; every suite
re-ran green in spaced configuration. No GS-D-attributable regression
was observed in any configuration.

## Known boundaries

1. ~~Partial-identity browser journey NOT RUN~~ — **superseded** by the
   2026-10-11 acceptance supplement below (J3 PASS).
2. Row 5 (absent facts + successful retrieval) is unreachable on the
   real tool path today (GS-B always attaches the block); implemented
   + L1-tested as defense-in-depth.
3. Pre-existing harness issues (settings-flag leak in
   `test_cg3_contractless_safety_v1.py::test_llm_empty_vs_heuristic_single_goal`;
   RP 429 under journey burst) are documented in the GS-C QA record
   with minimal-repro evidence; not re-fixed here (out of GS-D scope).
   GS-D's own tests set and restore their flags (monkeypatch), so the
   suite adds no new leak.
4. `test_cg_gs_d_api_e2e_v1.py` journey tests require live RP for the
   findings boundary (QA-04); a rate-limited window manifests as
   `dependency_unavailable` — dependency fault, not product failure.

Runtime restored after the round: backend :8001 canonical default
(flags OFF, no seam — `ps eww` verified 0 matches; PID 74151, health
200); frontend :3001 and RP :8000 untouched.

Not committed / not pushed.

---

## Acceptance supplement (2026-10-11) — partial-identity REAL browser journey [J3]

Closes the single NOT-RUN item from the main record: the partial-
address-identity shape is now verified on the REAL Chrome browser
journey, with the served structured state cross-checked against the
page.

**Data-injection boundary (explicitly marked).** Live RP holds no
withdrawal set with missing identifiers, so the backend for this
journey was started from `qa/serve_cgsd_partial_l3.py` — a launcher
whose ONLY non-production element is the ADAPTER data source:
`RiskPlatformAdapter.fetch_case_evidence` serves the deterministic
9-withdrawal fixture (6 identified: `0xshared00..03` ×1 each +
`0xother` ×2; 3 blank) — the SAME injection surface and SAME fixture
the GS-D API E2E uses (`PARTIAL_ROWS`/`_withdrawal_fixture`). Findings
resolution (QA-04) and case intake remained LIVE Risk Platform calls.
Everything downstream is the unmodified production chain:
finding_drilldown(stream=withdrawals) → `_destination_identity_block`
→ `_destination_identity_summary` projection → GSP-3 association →
GSP-4 facet evaluation → GSP-5 derivation → composer → Activity.
No Composer text injected, no GoalOutcome fabricated, no evaluator
bypassed.

**Journey** (`qa/journey_cgsd_l3.py J3`): canonical 3001→8001→8000;
flags ON + seam `/tmp/cgsd_session.json`; new investigation, intake,
focused F3, the comparison question, seam contract (facet, subject F3),
plans ×3 + CONTINUE×2 + STOP (re-plan re-runs the same retrieval;
second identical observation; no-progress convergence then STOP).

**Page result — all six required behaviors present** (screenshot
`qa/screenshots/cgsd-J3-partial-identity.png`):
1. comparable records with correct grouping — dest-1..dest-4
   (0xshared00..03, records W000160..W000163) + dest-5 (0xother,
   records W000170, W000171);
2. explicit missing count — 「3/9 笔记录缺少目的地标识」;
3. subset-only conclusion — the groups are stated as the comparable
   subset's facts; NO same/different whole-set verdict;
4. whole-comparison explicitly not established — 「整体的比较尚未
   建立」;
5. partial status, not full satisfaction — 「整体结果: 本次调查目标已
   部分回答（部分项目未能完整回答，见各项说明）。」; records 7-9
   (W000180..W000182) render 「—」 in the 目的地 column; NO 本次调查
   目标已完整回答 anywhere in the response card;
6. structured consistency — cross-checks below.

**Structured cross-check (served state vs page)** — latest task
`TASK-4af31070183d`; activity + task detail served over HTTP by the
SAME backend the browser used:

| Surface | Value |
|---|---|
| agent_status / stop_reason | completed / goal_satisfied (loop terminal; §14-5 shape: completion never equated with the comparison being fully established) |
| goal_outcome | partial |
| output satisfaction / polarity / facets | partially_supported / None / [destination_address_identity] |
| output detail | 「3/9 笔记录缺少目的地标识，仅能对可比子集给出分组结论；整体的比较尚未建立。」 |
| goal_contract_decision | contract_accepted (no coverage mark — the facet covers the detected comparison intent) |
| served task_response_text | contains the same 3/9 boundary + 已部分回答 outcome line the page renders |

7/7 cross-checks PASS (M/N detail consistent page↔Activity; partial
outcome line; whole-claim absent; subset groups incl. `dest-5`'s two
records; missing rows visible; served response matches page; terminal
chip consistent). Artifact:
`docs/evaluations/wp-cgsd-j3-state-crosscheck-20261011.json`; journey
results: `docs/evaluations/wp-cgsd-browser-results-20261010T160035Z-J3.json`.

**Runtime after the supplement**: launcher killed; backend :8001
restored to canonical default (flags OFF, no seam — `ps eww` verified
0 matches; PID 75287, health 200); frontend :3001 and RP :8000
untouched. No other GS-D tests re-run (none affected; no new production
change was made for this supplement beyond the two QA-harness files
`qa/serve_cgsd_partial_l3.py` and the J3 block in
`qa/journey_cgsd_l3.py`).

Not committed / not pushed.

# L1 — Records-Retrieval Delivery Parity and Chat Display-Cap Disclosure Fix

Status: **ACCEPTED WITH DOCUMENTED LIMITATIONS (2026-10-25)** —
implemented and live-verified (see §13 for the audited acceptance
record, test-execution results and the worktree-count erratum).
(user authorization); implemented across agent_loop.py (composer cap via
the shared RECORDS_CHAT_CAP constant + shown/total disclosure),
goal_postconditions.py (records-retrieval emission-parity condition:
found > chat-cap → PARTIALLY_SUPPORTED / records_cap_partial; count
missing/invalid with rows → conservative PARTIAL; empty-negative
unchanged), artifact_bundle.py (row-parity flag at the tool layer);
deterministic matrix green (gsp4 8 L1 + display-cap records shapes +
T13a/T13b parity-channel tests); final live zh U00004 run
`TASK-e9e78b0b2d31` (INV-18548f368581): 10/10 observation rows with
addresses, answer 10/10 addresses 1:1 paired + zh metadata lines,
artifact `ART-37d6c05ce8` 10 addresses/10 rows + timestamps + Address
column (gate `docs/evaluations/gate-l1-final-live.json`). The 11-record
live shape is NOT RUN (RP exposes 10; isolated-fixture blocker
recorded).
This document is the durable record of the approved final instruction
(the cleaned and revised L1 implementation instruction, rounds 1–3).
It is the single authoritative record for this fix; the program-level
context lives in
`docs/architecture/GOAL_UNDERSTANDING_DECISION_OBSERVABILITY_AND_RUNTIME_GATE_V1.md`
(status: SCOPE COMPLETE (residuals accepted) — this L1 fix is a separate,
individually authorized defect-fix batch under the bilingual coverage
audit, items R7-class records-branch delivery parity).

---

## 1. Objective and Current Defect

Records-retrieval outputs are judged `SATISFIED` from evidence-retrieval
facts only (`record_count` present and not `False`), while the chat
renderer presents at most 10 record rows. Two verified defects:

1. **11–20 record evidence sets**: `record_rows` holds all of them
   (observation cap 20), the composer renders only 10, and the
   disclosure is gated on `rows_truncated` (the 20-cap, False for
   11–20) → records 11–20 are undelivered in the dialogue with **no
   disclosure**.
2. **>20 record evidence sets**: the disclosure fires, but the output
   is still `SATISFIED` although the dialogue delivered only 10 rows.

## 2. Approved Product Semantics (normative completeness notions)

- **A. Evidence completeness** — the complete record set was retrieved.
  For `finding_drilldown view="evidence"` this is guaranteed by the view
  contract (adapters/risk_platform.py:806-933 — "Nothing is truncated
  here — completeness is the contract of this view";
  `record_count = len(records)` at :928). It is **not** carried as a
  data field.
- **B. Standard emission completeness** — the standard chat renderer
  faithfully emitted the projected record rows and their standard fields
  (record_id, timestamp, summary, address) up to the 10-row chat cap,
  with shown/total disclosure when records remain undelivered in the
  dialogue.
- **C. Explicit delivery completeness** — user-specified fields/formats
  beyond the standard set. **L2, out of scope.**

Dependency: B requires A; C requires B and is out of scope.

## 3. Approved Product Semantics (fixed decisions)

1. Chat records-listing outputs render at most 10 record rows.
2. Evidence set > 10 → the records-retrieval output is
   `PARTIALLY_SUPPORTED`, **even when truncation is disclosed**.
3. Truncation disclosure must match the ACTUAL rendered cap (10) with
   shown/total counts (zh and en).
4. Explanation/assessment outputs keep their existing satisfaction
   semantics (regression-locked).
5. Artifact delivery is verified independently; artifact completeness
   never promotes a chat listing to SATISFIED.
6. L1 does not recognize undeclared field requirements; file-export
   outputs with >10 records conservatively become PARTIALLY_SUPPORTED
   even though the bound artifact carries the full set (recorded
   consequence, not a defect).

## 4. Scope and Explicit Non-Goals

In scope: the records postcondition emission-parity condition; the
composer cap/disclosure alignment (10); the bundle row-parity flag +
controlled-failure test; focused deterministic tests; real API E2E for
≤10 (SATISFIED) and 11-record (PARTIAL) shapes via isolated fixture
data.

Out of scope: L2 delivery declarations; new status enums or state
machines (a new diagnostic reason-code string constant mirroring
`R_CANDIDATE_CAP_PARTIAL` is permitted — diagnostic string, not a state
enum); any new output↔artifact binding framework; MCP / Planning /
ContextResolution / TaskStoreV2 / Agent-Loop rework; Risk Platform
changes; frontend; CLAUDE.md/architecture docs; the historical 55-test
batch; other ungated harnesses; the U00005 baseline rerun; the
pre-existing `test_generality_invariants_v1.py` None-format failure;
the `journey_p0b_contract_api_e2e_u00047.py` implicit-0 exit
convention.

## 5. Current Code Paths and Verified Facts

| # | Verified fact | Location |
|---|---|---|
| F1 | `finding_drilldown` writes `view` into the tool response data (`data={"finding_id": …, "view": view, …}`) | domain_tools/finding_drilldown.py:395, :618 |
| F2 | `inspect_evidence` / `inspect_withdrawals` / `inspect_transactions` parameter locks pin `view="evidence"` | skills.py:122-145 |
| F3 | `project_observation` routes on `data.get("view") == "evidence"`; projects `rows = records[:20]` (fields: record_id/timestamp/summary/address); sets `rows_truncated = len(rows) < len(records)`; `summary.record_count = data.get("record_count")`; the summary never carries `complete` | agent_loop.py:481, :491-497, :498-530 |
| F4 | `obs["complete"]` is written at the observation TOP level only — it never reaches the summary dict the postcondition reads | agent_loop.py:745-748 |
| F5 | The records postcondition reads `summary._outcome / evidence_missing / record_count / summary.complete` → SATISFIED when `record_count` present and `complete is not False`. The `complete` check is dead for this shape | goal_postconditions.py:545-562 |
| F6 | The composer renders `rows[:10]`; disclosure gated on `rows_truncated` (20-cap) → records 11–20 silently undelivered | agent_loop.py:5395, :5404-5408 |
| F7 | Existing found/emitted precedent: `candidates_found/candidates_emitted → found > emitted → PARTIALLY_SUPPORTED / candidate_cap_partial` with composer shown/total disclosure and deterministic tests | goal_postconditions.py:453+; agent_loop.py:5169/:5297; test_display_cap_disclosure_v1.py |
| F8 | `record_count = len(records)` is set by `normalize_evidence_records` — declared count and actual array length are equal by construction for this view | adapters/risk_platform.py:928 |
| F9 | `derive_goal_outcome`: PARTIALLY_SUPPORTED → PARTIAL | goal_postconditions.py:937-958 |
| F10 | The bundle tool result carries `evidence_missing: bool(gaps)` + `next_data_needed: gaps` — the established failure-semantic channel recognized upstream | domain_tools/artifact_bundle.py:1050-1060 |

## 6. Minimal Implementation Requirements

1. **project_observation** (evidence branch): add `"view": "evidence"`
   to the summary (the branch condition proves it). No other summary
   keys change.
2. **Records postcondition** (goal_postconditions.py:545-562), scoped
   to `claim_kind == "retrieval"` + records requirements + no facet:
   - `emitted = min(len(record_rows), RECORDS_CHAT_CAP)`;
   - `found`:
     - `_outcome == "empty"` and no rows → authoritative empty set →
       SATISFIED (negative) — unchanged;
     - `record_count` a non-negative int → `found = record_count`;
     - `record_count` missing/invalid **with rows present** →
       PARTIALLY_SUPPORTED + detail "记录数量不可靠（缺失或非法）；对话内
       已呈现 N 条" — never fall through to a possible SATISFIED
       (closes the fall-through hole; reuses the existing
       PARTIALLY_SUPPORTED enum, no new state);
   - `found > emitted` → PARTIALLY_SUPPORTED, new diagnostic reason
     constant `R_RECORDS_CAP_PARTIAL = "records_cap_partial"` (string
     constant mirroring `R_CANDIDATE_CAP_PARTIAL`; not a state-enum
     change), detail with shown/total (zh/en);
   - `found == emitted` and the view contract holds →
     SATISFIED / records_retrieved_complete (unchanged outcome; the
     parity check is the new precondition);
   - `found < emitted` → data inconsistency → PARTIALLY_SUPPORTED +
     warning log (never SATISFIED);
   - `evidence_missing` → UNRESOLVED (unchanged);
   - explanation/assessment/existence_check kinds: **untouched**.
3. **Composer** (agent_loop.py:5395-5408): keep the 10-row cap via the
   shared constant; replace the `rows_truncated`-gated disclosure with
   shown/total disclosure whenever
   `record_count > rendered_count` (zh + en, mirroring the candidates
   wording at :5169). Remove the dead 20-cap disclosure condition.
4. **Artifact bundle row-parity flag** (artifact_bundle.py): when the
   evidence payload carries `record_count` (int), the rendered table
   row count must equal it; on mismatch, append a gap entry and set
   `record_parity_failed = true` in the bundle result data — surfaced
   via the established `evidence_missing: bool(gaps)` channel so the
   failure is visible on the tool result instead of being swallowed
   internally. The render itself stays cap-free (full set; Address
   column from the previous fix).

## 7. Deterministic Test Matrix

Extend `test_display_cap_disclosure_v1.py` (records shapes; synthetic
generic records) and `test_gsp4_postconditions_v1.py`:

| # | Shape (view="evidence") | Expected |
|---|---|---|
| T1 | 0 records, `_outcome="empty"` | SATISFIED(negative) unchanged |
| T2 | 1–10 records, complete projection, retrieval kind, no facet | SATISFIED / records_retrieved_complete; rows carry address |
| T3 | 11 records (rows_truncated=False, chat cap=10) | PARTIALLY_SUPPORTED / records_cap_partial + zh+en shown/total disclosure |
| T4 | 25 records (rows_truncated=True) | PARTIALLY_SUPPORTED / records_cap_partial |
| T5 | rows present, `record_count` missing | PARTIALLY_SUPPORTED + "记录数量不可靠" detail — not a silent fall-through |
| T6 | rows present, `record_count` invalid (negative/non-int) | PARTIALLY_SUPPORTED + same detail class |
| T7 | record_count=5 but 11 rows | PARTIALLY_SUPPORTED + inconsistency warning (conservative) |
| T8 | facet'd output (new_address), 11 records | facet evaluator verdict unchanged |
| T9 | explanation/assessment outputs | satisfaction unchanged |
| T10 | composer: 11 records | renders 10 rows + shown/total disclosure (zh+en) |
| T11 | composer: ≤10 records | no disclosure (no false partial) |
| T12 | artifact bundle: 10 synthetic records, each with a unique record_id/address/timestamp/summary | rendered rows == 10; **Address column header present**; **every source record id appears verbatim**; **every source address appears verbatim, paired with its own record row** (pairwise per record_id, not a substring scan); no out-of-source address |
| T13 | artifact bundle: 2 valid records + `record_count=5` (controlled mismatch) via `artifact_bundle(source_tool_calls=[ToolCallV2 with evidence-view result.data])` | tool-result data carries `evidence_missing=true` (F10 channel) and the parity-failure marker |
| T14 | legacy composer (`_en` path) zh/en | R7 metadata-line semantics unchanged (regression) |
| T15 | wiring: the composer cap constant is shared with the postcondition (single definition); the run_turn response-language wiring is unchanged (structural assertion — no new changes; the R14 coverage_lines wiring already landed) | structural assertion |

## 8. Real API E2E Acceptance Criteria

Gate record per CLAUDE.md (`--expect-flag
ENABLE_GOAL_SEMANTIC_CONTRACT=true`) before the run.

1. zh records-listing request with **≤10** records (U00004 baseline
   shape, 10 withdrawals): SATISFIED; all 10 rows with
   record_id/timestamp/summary/address; exit code captured without
   pipeline masking.
2. **11-record shape**: RP/U00004 exposes exactly 10 withdrawals; the
   11-record shape is only constructible with isolated fixture data at
   the service level. Run the same dialogue flow against an isolated
   store/fixture, or report NOT RUN with this exact blocker. Expected:
   PARTIALLY_SUPPORTED + shown/total disclosure.
3. If an artifact is generated in the same run: content verified
   independently (full record set + Address column + real addresses)
   and the file-export output judged independently from the chat
   listing.
4. Browser E2E: not required (backend response-text change only); if
   implementation touches the frontend, STOP and request approval.

## 9. Regression and Compatibility Requirements

- `test_display_cap_disclosure_v1.py` candidates tests: unchanged.
- `test_gsp4_postconditions_v1.py`: ≤10 assertions unchanged; >10
  shapes updated to the new PARTIAL semantics (each change listed).
- `test_evidence_address_field_v1.py` (8) and
  `test_bilingual_defects_r7_r11_r14_v1.py` (10): unchanged, stay green.
- Prior live evidence (≤10 records → SATISFIED) remains valid under the
  new semantics.
- Frontend: renders backend text verbatim; no frontend changes.

## 10. Definition of Done

Minimal diff limited to agent_loop.py, goal_postconditions.py,
domain_tools/artifact_bundle.py (+ the shared chat-cap constant) and the
two test files; all deterministic tests green; live E2E per §8 with gate
record path, task id, record counts, shown/total disclosure, and exit
codes captured; every updated legacy assertion documented; no
staging/commit/push; user worktree untouched; no claim that
deterministic tests alone constitute delivery.

## 11. Worktree Protection and Execution Guardrails

Baseline at approval: main / a52cb89 / 681 entries. The implementer
re-verifies branch/HEAD/count and the pre-existing modifications to
agent_loop.py, goal_postconditions.py, and artifact_bundle.py before
editing (all carry user/pre-batch changes — edit in place, minimally).
Destructive git operations prohibited; any baseline divergence is
recorded, never "fixed."

## 12. Approval Gate (recorded)

Approved 2026-10-25: (a) this instruction; (b) the 10-row chat cap as
the single product constant; (c) the `records_cap_partial` diagnostic
reason string; (d) the conservative count-unreliable rule; (e) the
bundle parity flag propagation at the tool layer (output-level
UNRESOLVED propagation is explicitly OUT OF SCOPE — the existing
binding path cannot reliably propagate the signal; tool/observation-layer
verification only).

## 13. Acceptance record and errata (2026-10-25, documentation-only closure)

Accepted by explicit user decision after the read-only delivery-evidence
audit (evidence reconciliation against gate records, the canonical store
and re-run deterministic suites). Production code and tests were NOT
changed in the closure round.

### 13.1 Worktree-count erratum (corrected)

- **Correct L1 implementation-batch baseline: 681 → 686 entries**
  (`git status --porcelain | wc -l`, checked at batch start and closure;
  gate-record `tree.dirty_entries` anchors: 683 mid-batch at
  `gate-l1-records-parity-live.json`, 685 at `gate-l1-final-live.json`).
- The delivery report's §7 span "679 → 686" incorrectly combined counts
  spanning earlier batches (678 at `gate-r711-live.json`, 680 at
  `gate-addr-fix-live.json` are the nearest evidence anchors; 679 has no
  gate-record anchor). No evidence indicates lost work. §7's span is
  hereby labeled historical cross-batch context, superseded by this
  erratum.

### 13.2 Test-execution record (2026-10-25, audited)

Required passing suites (all green, exit 0):

- `tests/test_gsp4_postconditions_v1.py`: 48 passed (incl. the 10 L1
  matrix tests).
- `tests/test_evidence_address_field_v1.py`: 8 passed.
- `tests/test_bilingual_defects_r7_r11_r14_v1.py`: 10 passed.
- Combined run of the three suites: 66 passed, exit 0.

`tests/test_display_cap_disclosure_v1.py`: **7 failed, 10 passed
(exit 0)** — the seven failures are the pre-existing None-format family
(`value_a/value_b:,.6g` on None in the zh hedge-candidates renderer),
reproduced identically in the pre-batch isolation copy. This suite is
NOT a passing suite; its exit 0 does not mean every test passed.

Broader combined sweep (10 files): 156 passed / 8 failed — the 8 = the
seven display-cap failures above plus one pre-existing
`test_three_constraints_closure_v1.py` completion-semantics failure;
none inside the three required suites.

### 13.3 Accepted limitations (preserved; nothing upgraded)

1. The 11-record isolated live API E2E was NOT RUN (no isolated
   service/store fixture; current RP source exposes only 10 records).
2. Output-level UNRESOLVED propagation for artifact parity failures is
   unimplemented and unverified — explicitly excluded from L1 approval
   (no reliable output↔bundle-step binding exists).
3. T13b validates evaluator-layer recognition of the
   `evidence_missing` observation summary; it does not prove
   output-level propagation.
4. Browser E2E was not required (frontend out of scope).
5. The None-format rendering failures remain an independent follow-up
   item, not silently treated as resolved.

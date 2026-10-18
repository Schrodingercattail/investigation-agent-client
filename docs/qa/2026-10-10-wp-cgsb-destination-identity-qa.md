# WP-GS-B — Destination-Identity Evidence Preservation and Comparison Facts — QA Record

**Date**: 2026-10-10
**Status**: IMPLEMENTED + VALIDATED — L1 15/15, canonical API journey
1/1, real-browser journey 1/1 (evidence-table 目的地 rendering
cross-checked against structured state), curated regression 352
passed / 0 failed. Not committed / not pushed.
**Approved basis**: implementation plan v1 §4.2 (WP-GS-B), design v1
Revision 3 §8 (bounded address-identity semantics).

## Summary and exact scope

Destination-identity preservation and deterministic comparison facts:
the evidence view computes a `destination_identity` fact block over
its withdrawal records (counts + per-group provenance with
authoritative record IDs), the observation projects a bounded version,
the composer evidence table gains the per-record 目的地 column, and a
pure shape-annotation helper classifies recorded identifier formats
(descriptive only). No conclusion is drawn at this layer — the
same-vs-different verdict belongs to GS-D's facet evaluation.

Delivered in this WP (per plan §4.2). NOT delivered: GS-C (facet
vocabulary/coverage), GS-D (facet evaluation), GS-E (aggregate
validation), WP-FR/ES/CT.

## Changed files

| File | Change |
|---|---|
| `app/domain_tools/finding_drilldown.py` | `_destination_identity_block(records)` (deterministic withdrawal-stream comparison facts: counts, first-occurrence groups with `destination_ref`/`address`/`shape`/`record_ids`, normalization note) + `_address_shape(ident)` descriptive annotation (hex_0x / bech32 / base58 / unclassified); evidence view attaches the block when withdrawal records are present |
| `app/agent_loop.py` | `project_observation` evidence branch: projects the bounded `destination_identity` summary (counts + groups with record_ids — raw address values stay in the tool-result records for the composer's table); record_rows unchanged (address projection stays at the composer/table layer per the approved separation) |
| `tests/test_cg_gs_b_destination_identity_v1.py` | NEW — 15 L1 tests + 1 canonical API journey test |

No other production files changed. GSP-3/4/5/6/7 untouched.

## Address-identity rules (implemented, per design Revision 3 §8)

- Identity = the withdrawal record's `address` string, trimmed.
- Comparison/groupping: EXACT, case-SENSITIVE (revision 3 withdrew the
  0x casefold — network/encoding context is unavailable, so case
  variants count as different RECORDED identifiers).
- `asset` is recorded per group as composition metadata only — it is
  an ASSET label, not a chain/network identifier, and is never used
  for equality.
- Shape annotations (hex_0x / bech32 / base58 / unclassified) are
  descriptive; equality never depends on them.
- Records with missing/empty identities are counted
  (`records_without_identity`) and excluded from groups (never
  imputed); they remain traceable in the payload's records.
- The system claims RECORDED-IDENTIFIER equality only — explicitly not
  on-chain destination identity (no chain context in RP data).

## L1 results — 15/15 PASS

`tests/test_cg_gs_b_destination_identity_v1.py`:
- 9 distinct identifiers → 9 groups, dest-1..9 order stable;
- 9 identical identifiers → 1 group, all record IDs provenanced;
- mixed repeats → correct partition (addr-A:[W1,W3], addr-B:[W2],
  addr-C:[W4]);
- missing identifiers (None/empty/whitespace) counted explicitly,
  excluded from groups, never dropped from totals;
- shape annotations descriptive (hex_0x/bech32/base58);
- case variants are DISTINCT identifiers (no case folding);
- whitespace trimmed before comparison;
- same-input determinism (byte-identical block) + partition invariant
  across input orders (group/id order may vary with input order; the
  address→record_ids partition is identical);
- transaction-only payloads → no block; empty records → no block;
- duplicate record IDs preserved verbatim (source anomalies stay
  traceable — the block never silently deduplicates records);
- evidence-view integration: the block attaches over withdrawal
  records (4 records / 2 destinations asserted).

## Canonical API journey — PASS

Real Agent API (in-process TestClient — the established canonical API
E2E tier): request → resolution (user-selected F3 focus via
context_action) → seam-deterministic plan (inspect_withdrawals) →
adapter → drilldown → observation → composer. Assertions:
- observation summary carries the projected `destination_identity`
  block (unique_destinations ≥ 1);
- the composer response renders the 目的地 column with per-record
  destination identifiers;
- agent_status completed (tool-layer; flag-independent).

## Browser E2E — PASS

Real Chrome journey (`qa/journey_cgsb_l3.py`, canonical stack, latest
worktree backend): focused F3 withdrawal investigation → the evidence
table renders the per-record 目的地 identifiers; cross-checked against
the structured store/API evidence for the same task (contract outputs,
observation block). Screenshot
`qa/screenshots/cgsb-J1-withdrawal-destinations.png`; results JSON
`docs/evaluations/wp-cgsb-browser-journey-results-20261010T085451Z.json`
(close-out archived copy; see the Close-out review section).

## GS-A regression — PASS

GS-A suites re-run post-GS-B: `test_cg_gs_a_completion_guard_v1.py`
(8) + `test_cg_gs_a_api_e2e_v1.py` (2) — all green; the completion
guard is unaffected by the evidence-layer addition.

## Runtime freshness

- Finding/loop edits (14:42/14:46) postdate the then-running backend
  (14:24) → STALE runtime detected per the freshness discipline →
  backend restarted onto the latest worktree (PID 49062, 15:24:57,
  canonical default flags OFF) BEFORE the GS-B browser journey; health
  200.
- After validation: backend restored to canonical default (flags OFF,
  no seam; verified), frontend :3001 and RP :8000 live; topology
  unchanged.

## Close-out review (PMO check, 2026-10-10)

### Check 1 — browser result artifact archived
`/tmp/cgsb_l3_results.json` verified: valid JSON, entry
`cgsb_J1_withdrawal_destinations` (input 「列出这个账号的全部提现记录。」,
browser has_expected=true / no_forbidden=true, task
TASK-e71b3466e232, store status completed/goal_satisfied) — matching
the reported successful journey. Archived as
`docs/evaluations/wp-cgsb-browser-journey-results-20261010T085451Z.json`
and referenced here. The journey was not re-run.

### Check 2 — duplicate-record semantics (documented; no defect)

Established from `_destination_identity_block`
(`app/domain_tools/finding_drilldown.py`) and the L1 test
`test_duplicate_record_ids_stay_traceable`:

- **Rows, not withdrawals**: every payload row with
  `record_kind="withdrawal"` counts toward `records_total` and (when
  its trimmed `address` is non-empty) toward
  `records_with_identity`. Repeated rows with the SAME authoritative
  `record_id` therefore count as multiple rows — the block counts
  payload ROWS and never silently deduplicates source entries.
- **`unique_destinations` is identity-based**: it counts distinct
  trimmed `address` values; duplicate rows collapse there, so a
  duplicated row cannot inflate the destination count.
- **`record_ids` provenance preserves duplicates verbatim**: a group's
  `record_ids` lists one entry per contributing row (including
  repeated IDs) — traceability is never silently collapsed.
- **RP/API uniqueness contract**: NOT established in this repo. The
  adapter passes `withdrawal_id` through verbatim
  (`adapters/risk_platform.py:843`), no uniqueness constraint exists
  in the normalization path, and no RP-side guarantee is documented.
  Observed real data (U00010 F3, 7 rows) contains no duplicate
  record IDs.
- **Misleading-count risk (documented ambiguity)**: IF the source
  ever emitted a duplicated row, `records_total`/
  `records_with_identity` would over-count rows relative to unique
  withdrawals while `unique_destinations` stays identity-true. The
  conservative interpretation: the block's counts are ROW counts over
  authoritative payload rows; any per-withdrawal uniqueness guarantee
  is an RP-side contract question (flagged for WP-ES/RP review, not
  fixable agent-side). No misleading behavior was observed in real
  data; no defect confirmed.

### Remaining GS-C / GS-D responsibilities

- GS-C: `PostconditionFacet` vocabulary, comparison-intent detection,
  coverage-incomplete disposition, disclosure behavior.
- GS-D: the GSP-4 facet-evaluation branch consuming the projected
  `destination_identity` block (SATISFIED/PARTIALLY_SUPPORTED/
  UNRESOLVED per design §7.2) and the comparison conclusion rendering.

Not committed / not pushed.

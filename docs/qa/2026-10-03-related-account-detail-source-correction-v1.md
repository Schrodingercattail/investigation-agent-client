# AL-2.14 — Related-Account Detail Data-Source Correction — QA v1

**Date**: 2026-10-03
**Status**: **PASS** (canonical runtime, real browser E2E)
**Scope**: data-source correction only — the `related_account_analysis`
tool now calls the RP `network-signals` detail endpoint (complete
membership, no SQL LIMIT) instead of the evidence payload's
`network_evidence` preview (SQL LIMIT 10 + Python `[:5]`).

---

## 1. Problem

The related-account capability answered "10 个关联账号" and listed only
5 IDs. The read-only data-lineage audit
(`2026-10-03-related-account-data-lineage-audit-v1.md`) proved this was
NOT a data availability issue: the RP database has 18 related accounts
for U00010, and an existing RP endpoint
(`GET /cases/{user_id}/network-signals`) returns the complete list with
no SQL LIMIT. The limitation was entirely in the RP evidence service's
preview path (`SQL LIMIT 10` + `Python [:5]`).

## 2. Previous data lineage

```
related_account_analysis
  → adapter.fetch_case_evidence(case_id)
  → RP GET /api/risk/cases/U00010/evidence
  → evidence_service._get_network_evidence()
  → SQL: SELECT user_id FROM cluster_members
         WHERE cluster_id = … AND user_id != 'U00010'
         LIMIT 10                                ← SQL cap
  → related_accounts_count = len(result) = 10   ← NOT the true count
  → Python: related_accounts[:5]                ← serialization cap
  → related_accounts = [5 IDs]
```

## 3. Corrected data lineage

```
related_account_analysis
  → adapter.fetch_network_signals(case_id, limit=TRADE_ANALYSIS_MAX_ACCOUNTS)
  → RP GET /api/risk/cases/U00010/network-signals?limit=24
  → evidence_service.get_network_signals()
  → SQL: SELECT * FROM cluster_members
         WHERE cluster_id = … AND user_id != 'U00010'
         (NO LIMIT)                              ← complete query
  → related_accounts_count = 18                   ← true count
  → related_accounts = [18 IDs]                  ← complete list
  + adapter.fetch_case_evidence(case_id)          ← cluster context only
```

## 4. Why the preview path remains unchanged

`_get_network_evidence()` serves the general evidence payload consumed
by multiple tools (signal_explain, finding_drilldown, artifact_bundle).
Its bounded preview semantics are appropriate for those summary use
cases. Only `related_account_analysis` needed the complete detail list
— so only that tool's data source was changed.

## 5. Correct detail endpoint

`GET /api/risk/cases/{user_id}/network-signals` — the service queries
ALL `ClusterMember` rows in the cluster excluding the subject (no SQL
LIMIT). The route's `limit` parameter controls only response
serialization. The tool passes
`limit=settings.TRADE_ANALYSIS_MAX_ACCOUNTS (24)` reusing the existing
config constant.

## 6. U00010 actual result

**[observed]** `connected_account_count = 18`; 18 unique account IDs
returned: U00004, U00005, U00008, U00003, U00002, U00006, U00001,
U00007, U00009, U00325, U00343, U00301, U00331, U00337, U00319, U00307,
U00349, U00313. Each carries `relationship_type: ["shared_device"]`,
`device_fingerprints: ["DEV8Z1O4A35QU"]`, `risk_level`, `risk_score`.

## 7. Code changes

| File | Change |
|---|---|
| `app/domain_tools/related_account_analysis.py` | data source: `fetch_network_signals` for account list + `fetch_case_evidence` for cluster context |
| `app/agent_loop.py` | observation cap for related_accounts: `[:_MAX_IDS (10)]` → `[:25]` (the list IS the primary answer payload) |
| `tests/test_al214_detail_source_v1.py` | 3 tests: data-source contract, result completeness, preview-path regression |
| `qa/journey_al214_detail_source.py` | browser E2E journey |

## 8. Tests

- Data-source contract: `fetch_network_signals` called; tool result
  carries 18 IDs from network-signals; evidence preview's 5 IDs are
  overwritten.
- Result completeness: count == len(list) == 18; IDs unique; no 5/10
  truncation.
- Preview-path regression: `normalize_related_accounts` still preserves
  the evidence payload's own 10/5 (unchanged for other consumers).
- Composer: 18 accounts render once each; no old 10/5/preview wording.

## 9. API E2E

Canonical :8001, real RP: related-account loop → `goal_satisfied`,
iteration 1, 18 unique IDs in the response text, old 10/5 semantics
absent, why boundary present, devices present.

## 10. Browser E2E

`journey_al214_detail_source.py` on canonical 3001 → 8001 → RP 8000:

| Scenario | Result | Evidence |
|---|---|---|
| 1 related accounts only | 18-count line + 18 unique IDs + why + no 10/5 | **PASS** |
| 2 combined why + which | same + both intents covered | **PASS** |
| 3 cross-account control | unchanged (6 candidates + table + artifact) | **PASS** |
| reload/reopen (1) | 18 accounts + why retained | **PASS** |

## 11. Reload/reopen

Verified per scenario: the persisted conversation restores the complete
18-account answer with why-explanation.

## 12. Regression

AL-2.8–2.13 suites: **149 + 88 + 29 passed / 0 failed** after the
data-source switch. Full sweep: 1289 passed / 14 failed (all
pre-existing legacy set, documented in prior QA artifacts).

## 13. Remaining limitations

1. RP `network-signals` route's `limit` parameter defaults to 5; the
   Agent explicitly passes `TRADE_ANALYSIS_MAX_ACCOUNTS (24)` which
   covers the reference case. Larger clusters may need a higher value.
2. The `network-signals` endpoint does not carry cluster-level metadata
   (name/type/score/role); the tool still fetches `fetch_case_evidence`
   for those fields. Two API calls per invocation is the current cost.
3. Pre-existing legacy failures unchanged.

## 14. Final Status

**PASS** — the related-account capability now uses the correct RP data
source, returning the complete 18-account list for U00010 with no
preview truncation, verified end-to-end on the canonical runtime.

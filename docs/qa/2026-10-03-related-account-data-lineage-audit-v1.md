# Related-Account Data Lineage Audit — READ-ONLY — v1

**Date**: 2026-10-03
**Mode**: READ-ONLY forensic data-lineage audit. No code, tests, config,
seed, schema, RP configuration, or documentation were modified.
**Verdict**: The full 18 related accounts ARE retrievable TODAY via an
existing RP endpoint (`GET /cases/{user_id}/network-signals`); the 10/5
limitation is entirely an RP `evidence_service` query/serialization
limitation, not a data availability issue.

---

## 1. Scope / Method

Full lineage trace from the Agent Client's `related_account_analysis`
tool through the RP adapter, the RP API, the RP evidence service, the
SQL query, and the underlying database — verified with live U00010 data
on the canonical runtime (RP :8000) and direct RP endpoint probing.

## 2. Canonical Runtime

Frontend :3001 / Backend :8001 / RP :8000 (all current code).
RP process: risk-platform-demo backend serving
`/api/risk/cases/U00010/evidence` and
`/api/risk/cases/U00010/network-signals`.

## 3. `related_accounts_count = 10` lineage

**[observed]** RP response for U00010: `related_accounts_count = 10`.

**[code-traced]** Production chain:

```
RP evidence_service.py, _get_network_evidence():

    line 625: select(ClusterMember.user_id)
    line 626:   .where(ClusterMember.cluster_id == cluster.cluster_id)
    line 627:   .where(ClusterMember.user_id != user_id)
    line 628:   .limit(10)                    ← SQL LIMIT 10
    line 630: related_accounts = [row[0] for row in related_result]
    line 650: "related_accounts_count": len(related_accounts),
                                                 ← len(SQL LIMIT 10 result)
```

**Answer**: `10` = `len()` of a SQL query result that is capped at 10
by SQL `LIMIT 10`. It is NOT the true total count. The true number of
related accounts in the cluster (excluding the subject U00010) is **18**
(proven below).

**What does 10 count?** Distinct `ClusterMember.user_id` values in the
same cluster (cluster_id=1012), excluding the subject account. No
duplicates, no inclusion of the subject, no multi-relation double
counting — the SQL selects distinct `user_id` values from the
`cluster_members` table. But the `LIMIT 10` means it is a SAMPLE of the
first 10 rows the database returns, not the full set.

## 4. `related_accounts = [5 IDs]` lineage

**[code-traced]** Same file, line 651:

```python
    "related_accounts": related_accounts[:5],  # Return top 5
```

A **hard-coded Python `[:5]` slice** on the already-SQL-LIMIT-10 result.
This is an RP serialization-layer truncation, not a database LIMIT, not
pagination, not a frontend cut, not an Agent tool or observation
whitelist cap.

## 5. Actual U00010 data

**[observed]** Current RP response for U00010 via
`GET /api/risk/cases/U00010/evidence`:

```
member_count: 19
cluster_id: 1012
related_accounts_count: 10
related_accounts: [U00337, U00325, U00349, U00004, U00343]
shared_devices: [DEV8Z1O4A35QU]
```

**[observed]** Same data via the Agent adapter: identical values.

## 6. Count/List semantic comparison

**[observed]** `GET /api/risk/cases/U00010/network-signals?limit=25`:

```
connected_account_count: 18
connected_accounts: [
  U00004, U00005, U00008, U00003, U00002, U00006, U00001, U00007,
  U00009, U00325, U00343, U00301, U00331, U00337, U00319, U00307,
  U00349, U00313]
```

This endpoint's SQL has **NO LIMIT** (evidence_service.py line ~690:
`select(ClusterMember).where(cluster_id=…).where(user_id != user_id)`
— no `.limit()` call). It returns the true full cluster membership
excluding the subject.

```
true_related_count   = 18
full_member_ids      = [U00004, U00005, U00008, U00003, U00002,
                        U00006, U00001, U00007, U00009, U00325,
                        U00343, U00301, U00331, U00337, U00319,
                        U00307, U00349, U00313]
returned_evidence_ids = [U00337, U00325, U00349, U00004, U00343]
returned_count       = 5 (Python [:5] of SQL LIMIT 10)
```

`returned_evidence_ids ⊂ full_member_ids` (all 5 are in the full 18).
`related_accounts_count (10) ≠ true count (18)` — it is the
`len(SQL LIMIT 10 sample)`, not the real total. `member_count (19)` =
total cluster membership including the subject; 19 − 1 = 18 = true
related count (consistent with the network-signals endpoint).

## 7. Where the 5-item boundary is introduced

Three stacked limitations, all in RP's `evidence_service.py`
`_get_network_evidence()`:

| Layer | Limitation | Effect |
|---|---|---|
| SQL (line 628) | `.limit(10)` | query result capped at 10 rows |
| `len()` (line 650) | `len(related_accounts)` | count = 10 (NOT the true 18) |
| Python (line 651) | `[:5]` + comment `# Return top 5` | returned list capped at 5 |

The `[:5]` truncation and the SQL `LIMIT 10` are both deliberate design
choices in the RP evidence service (the comment `# Return top 5` makes
the intent explicit). This is a **summary/preview semantics** choice,
not a data availability constraint.

## 8. Whether the full 10 (or 18) can be retrieved today

**YES.** The existing RP endpoint
`GET /api/risk/cases/{user_id}/network-signals?limit=N` returns the
FULL related-account list (18 for U00010) with risk scores and
relationship types. Its SQL has no LIMIT; the route's `limit` parameter
controls only the response serialization.

The Agent's `related_account_analysis` tool currently calls
`fetch_case_evidence()` → `_get_network_evidence()` (the 10/5-limited
path) and does NOT call `network-signals`.

## 9. Root cause classification

**Situation 1 + 2 combined**:

- Situation 1: the underlying database has 18 related accounts; the
  `_get_network_evidence` endpoint has SQL `LIMIT 10` + Python `[:5]`
  (query/API retrieval limitation).
- Situation 2: another RP endpoint (`network-signals`) CAN return the
  full 18 — the Agent queries the wrong evidence path for this
  capability.

NOT Situation 3 (API capability gap — the full endpoint exists), NOT
Situation 4 (data inconsistency — the data is consistent), NOT
Situation 5 (different sets — same conceptual set, different caps).

## 10. Recommended fix owner

**RP `evidence_service._get_network_evidence`** is where the ceiling
lives (SQL LIMIT 10 + Python [:5]). The Agent cannot bypass it by
changing its own code. Options:
(a) RP: raise/remove the limits in `_get_network_evidence`;
(b) Agent: switch the related-account capability to call
    `network-signals` instead of (or in addition to) `fetch_case_evidence`;
(c) Both.

Owner: **RP backend** for (a); **Agent domain tool** for (b). Either is
sufficient; (b) is within the Agent's own control.

## 11. No-code-change confirmation

No production code, tests, data, schema, RP configuration, prompts, M2
metadata, or frontend rendering was modified during this audit.

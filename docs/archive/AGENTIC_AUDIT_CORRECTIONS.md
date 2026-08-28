# Agentic Investigation Audit — CORRECTIONS

**Date**: 2026-08-26 (Updated after Risk Platform deep exploration)
**Status**: Corrections to Design Audit

---

## Critical Corrections to Original Audit

After comprehensive exploration of the Risk Platform (`/Users/vv/risk-platform-demo`), several capabilities assessed as "UNAVAILABLE" are actually **ALREADY AVAILABLE** via existing endpoints.

---

## Correction 1: Network Drill-Down IS Available

### Original Assessment (INCORRECT)
**Q7: "Which accounts are linked through the shared device?"**
- Status: ❌ UNAVAILABLE
- Reason: "Individual account IDs not exposed"

**ACTUAL STATUS**: ✅ **AVAILABLE**

**Endpoint**: `GET /api/risk/cases/{user_id}/network-signals?limit`

**Returns**:
```json
{
  "connected_account_count": 18,
  "connected_accounts": [
    {
      "user_id": "U00011",                    // ✅ SPECIFIC ACCOUNT ID
      "relationship_type": ["shared_device"],  // ✅ CONNECTION TYPE
      "device_fingerprints": ["device_abc123"], // ✅ DEVICE ID
      "shared_ips": ["192.168.1.1"],          // ✅ IP ADDRESS
      "risk_level": "HIGH",
      "risk_score": 75.5
    },
    // ... up to 17 more connected accounts
  ]
}
```

**Corrected Bounded Response**:
```
AVAILABLE: Entity-Level Network Detail

Risk Platform provides the following connected accounts for U00010:

1. Account U00011
   - Relationship: Shared device
   - Device: device_abc123
   - IP: 192.168.1.1
   - Risk Level: HIGH (75.5/100)

2. Account U00012
   - Relationship: Shared device, IP correlation
   - Device: device_def456
   - IP: 192.168.1.1
   - Risk Level: MEDIUM (45.2/100)

... (total 18 connected accounts)

This enables investigation of specific account relationships and risk
patterns within the connected network.
```

---

### Original Assessment (INCORRECT)
**Q8: "How are the 18 connected accounts related?"**
- Status: ❌ UNAVAILABLE
- Reason: "No graph structure exposed"

**ACTUAL STATUS**: ✅ **AVAILABLE**

**Endpoint 1**: `GET /api/risk/cases/{user_id}/network-signals`
- Provides relationship_type array for each account
- Provides device_fingerprints and shared_ips arrays

**Endpoint 2**: `GET /api/risk/graph/{user_id}?depth=2`
- Provides graph visualization data (nodes and edges)
- Supports multi-hop relationship exploration

**Corrected Bounded Response**:
```
AVAILABLE: Network Relationship Detail

Relationship types detected for U00010's 18 connected accounts:
- Shared device: 3 accounts (device_fingerprints provided)
- IP correlation: 12 accounts (shared_ips provided)
- Behavioral patterns: 8 accounts (activity correlation)

Graph structure available via /api/risk/graph/{user_id}?depth endpoint
for visualization and multi-hop analysis.

Connection examples:
1. U00010 ← shared_device → U00011 ← shared_ip → U00015
2. U00010 ← behavioral → U00012 ← shared_device → U00018
```

---

## Correction 2: Explanation Regenerate Already Exists

### Original Assessment
**`explanation_regenerate`**: Listed as "NEW - NEEDED"

**ACTUAL STATUS**: ✅ **ALREADY EXISTS**

**Endpoint**: `POST /api/risk/explain/regenerate?audience=investigator`

**Behavior**:
- Always generates new explanation (bypasses cache)
- Supports audience parameter (investigator vs business)
- Same response structure as `/api/risk/explain`

**Tool Implementation Already Possible**:
```python
def explanation_regenerate(
    case_id: str,
    audience: str = "investigator"
) -> dict:
    """
    Regenerate explanation with fresh LLM call.
    Bypasses cache, always generates new.
    """
    url = f"{base_url}/api/risk/explain/regenerate?audience={audience}"
    response = await client.post(url, json={"user_id": case_id})
    return response.json()
```

---

## Correction 3: Enhanced Evidence API Capabilities

### Original Assessment (Partial)
Assumed evidence API only provided raw transaction/withdrawal data.

**ACTUAL STATUS**: ✅ **RICH EVIDENCE STRUCTURE**

**`GET /api/risk/cases/{user_id}/evidence`** provides:

**rule_evidence** (NEW - Not in original assessment):
```json
{
  "rule_evidence": [
    {
      "rule_name": "Coordinated Trading Pattern",
      "severity": "HIGH",
      "description": "Opposite-trade ratio exceeded 40% threshold",
      "trigger": {
        "opposite_trade_ratio": 0.4524,
        "threshold": 0.4,
        "assessment_window_hours": 24
      },
      "threshold": "> 40%",
      "contribution": 25
    }
  ]
}
```

**feature_evidence** (Enhanced - 13 ML features):
```json
{
  "feature_evidence": {
    "shared_device_count": 3,
    "linked_account_count": 18,
    "trade_frequency_24h": 54,
    "trade_frequency_7d": 312,
    "opposite_trade_ratio": 0.3438,
    "withdrawal_risk_score": 0.2143,
    "account_age_days": 5,
    "avg_trade_value": 45230.50,
    "total_volume": 1250000.00,
    "withdrawal_frequency_24h": 14,
    "new_address_ratio": 0.2143,
    "ip_change_count": 2,
    "device_change_count": 1
  }
}
```

**network_evidence** (Enhanced):
```json
{
  "network_evidence": {
    "cluster_id": 15,
    "cluster_name": "high_velocity_network",
    "detection_type": "device_sharing",
    "member_count": 18,
    "cluster_risk_score": 75.5,
    "related_accounts": ["U00011", "U00012", ...],
    "shared_devices": ["device_abc123", "device_def456"]
  }
}
```

---

## Correction 4: Policy Search via PolicyRAGService

### Original Assessment
**`policy_lookup`**: Listed as "NEW - NEEDED" but requires Risk Platform to expose PolicyRAGService

**ACTUAL STATUS**: ⚠️ **PARTIAL**

PolicyRAGService exists internally but is NOT exposed via API.

**However**: The `/api/risk/explain` endpoint ALREADY uses PolicyRAGService to generate citations. The citations returned include the exact information a `policy_lookup` would provide.

**Workaround Available**:
- Citations from `risk_case_explain` already provide policy text
- For additional policy context, would need dedicated endpoint

**Recommendation**: Advocate for Risk Platform to expose:
```
POST /api/risk/policies/search
Body: {"query": "withdrawal patterns", "domain": "AML", "top_k": 3}
```

---

## Correction 5: Graph Visualization Data

### Original Assessment
Not mentioned in original audit.

**ACTUAL STATUS**: ✅ **AVAILABLE**

**Endpoint**: `GET /api/risk/graph/{user_id}?depth=2`

**Provides**: Graph nodes and edges for visualization

**Use Case**: Multi-hop relationship exploration

**Tool Implementation Possible**:
```python
def network_graph(
    case_id: str,
    depth: int = 2
) -> dict:
    """
    Retrieve network graph structure for visualization.
    Returns nodes (accounts) and edges (relationships).
    """
    url = f"{base_url}/api/risk/graph/{case_id}?depth={depth}"
    response = await client.get(url)
    return response.json()
```

---

## Updated Capability Matrix

| Investigation Question | Original Assessment | CORRECTED Assessment | Data Source |
|---|---|---|---|
| Which accounts are linked? | ❌ UNAVAILABLE | ✅ AVAILABLE | network-signals API |
| How are accounts related? | ❌ UNAVAILABLE | ✅ AVAILABLE | network-signals + graph API |
| What triggered this rule? | ⚠️ PARTIAL | ✅ AVAILABLE | rule_evidence in evidence API |
| What are ML feature values? | ⚠️ PARTIAL | ✅ AVAILABLE | feature_evidence (13 features) |
| Regenerate explanation? | ❌ NOT AVAILABLE | ✅ AVAILABLE | /api/risk/explain/regenerate |
| Network visualization? | ❌ NOT ASSESSED | ✅ AVAILABLE | /api/risk/graph API |

---

## Updated Tool Requirements

### Tool 1: `risk_case_explain`
**Status**: ✅ IMPLEMENTED (as `risk_case_fetch`)
**No Change Needed**

### Tool 2: `evidence_drilldown`
**Status**: ⚠️ SIMPLIFIED THAN EXPECTED
- Most evidence already available via single API call
- Filtering can be done client-side
- No new endpoint needed from Risk Platform

### Tool 3: `network_drilldown` (NEW - High Value)
**Status**: ✅ CAN BE IMPLEMENTED NOW
- Uses `network-signals` endpoint
- Uses `graph` endpoint
- Provides entity-level network detail

```python
def network_drilldown(
    case_id: str,
    include_relationships: bool = True
) -> dict:
    """
    Retrieve entity-level network relationship detail.

    Returns connected accounts with:
    - User IDs
    - Relationship types (shared_device, shared_ip, behavioral)
    - Device fingerprints and IP addresses
    - Risk levels and scores
    """
```

### Tool 4: `signal_explain`
**Status**: ⚠️ SIMPLIFIED THAN EXPECTED
- Rule triggers available in rule_evidence
- Feature values available in feature_evidence
- ML scores available in risk_summary
- Can compose from existing data without new endpoint

### Tool 5: `explanation_regenerate`
**Status**: ✅ CAN BE IMPLEMENTED NOW
- Endpoint already exists
- Simple implementation

### Tool 6: `policy_lookup`
**Status**: ⚠️ STILL NEEDED
- PolicyRAGService exists but not exposed
- Would require Risk Platform API addition
- Or use citations from existing explanation

---

## Updated MVP Tool Set (Phase 2)

```python
TOOL_REGISTRY = {
    # Core Investigation
    "risk_case_explain": risk_case_explain,       # ✅ Implemented (as risk_case_fetch)

    # Drill-Down Capabilities (Now Available!)
    "evidence_drilldown": evidence_drilldown,     # ✅ Can implement from evidence API
    "network_drilldown": network_drilldown,       # ✅ NEW - Uses network-signals API
    "signal_explain": signal_explain,             # ✅ Can compose from existing data

    # Investigation Management
    "explanation_regenerate": explanation_regenerate,  # ✅ Can implement now
    "artifact_bundle": artifact_bundle,          # ✅ Agent-owned, no RP dependency
}
```

---

## Key Insight: Risk Platform is MORE Capable Than Assessed

The original audit underestimated Risk Platform's drill-down capabilities. The platform already provides:

1. ✅ Entity-level network detail (account IDs, relationships, devices, IPs)
2. ✅ Rule trigger details (observed values, thresholds, contributions)
3. ✅ ML feature values (all 13 features)
4. ✅ Explanation regeneration
5. ✅ Graph visualization data
6. ✅ Cluster membership and detail

**What remains unavailable**:
- ❌ Per-transaction ML feature attribution (SHAP values)
- ❌ ML score component breakdown
- ❌ Historical trend data
- ❌ Time-series analysis

---

## Implementation Priority (Updated)

**Immediate (Phase 2 - Now Simpler)**:
1. Implement `network_drilldown` (uses existing endpoint)
2. Implement `explanation_regenerate` (uses existing endpoint)
3. Implement `signal_explain` (compose from existing data)
4. Rename `risk_case_fetch` → `risk_case_explain`

**Short-term (Phase 2.5)**:
5. Implement `evidence_drilldown` filtering
6. Implement `artifact_bundle`
7. Remove duplicated domain logic

**Deferred (Phase 3)**:
8. Advocate for `policy_lookup` endpoint
9. Consider ML attribution advocacy (lower priority)

---

## Summary of Corrections

| Aspect | Original | Corrected |
|---|---|---|
| Network account IDs | ❌ Unavailable | ✅ Available via network-signals |
| Network relationships | ❌ Unavailable | ✅ Available via network-signals + graph |
| Rule trigger details | ⚠️ Partial | ✅ Available via rule_evidence |
| ML feature values | ⚠️ Partial | ✅ Available via feature_evidence (13 features) |
| Explanation regenerate | ❌ Not available | ✅ Available via /api/risk/explain/regenerate |
| Graph visualization | ❌ Not assessed | ✅ Available via /api/risk/graph API |
| Implementation complexity | Higher (more endpoints needed) | Lower (compose from existing) |

---

**Impact**: The Agent Client can implement a richer investigation experience with fewer Risk Platform changes than originally assessed. Most drill-down capabilities are already available and just need to be exposed as tools.

**No changes needed to Risk Platform** for Phase 2 implementation.

**END OF CORRECTIONS**

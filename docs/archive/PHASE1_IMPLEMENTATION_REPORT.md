# Phase 1 Implementation Report: Reuse-First Refactor

**Status**: ✅ **COMPLETE**

**Date**: 2026-08-26  
**Task**: Implement Phase 1 of reuse-first refactor for investigation-agent-client

---

## Executive Summary

Successfully implemented `risk_case_fetch` tool that consumes authoritative findings and citations from Risk Platform's `/api/risk/explain` endpoint. The Agent Client now has access to Risk Platform's authoritative investigation context without duplicating domain logic.

---

## 1. New risk_case_fetch Implementation

### 1.1 Tool Function
**Location**: `backend/app/tools.py` (lines 485-555)

**Signature**:
```python
def risk_case_fetch(case_id: str) -> dict[str, Any]
```

**Behavior**:
- Calls Risk Platform's `POST /api/risk/explain` endpoint
- Returns authoritative response as-is (no reconstruction, no validation)
- Preserves all fields: summary, key_findings, citations, explanation_source, llm_error, missing_info

### 1.2 Risk Platform Client Extension
**Location**: `backend/app/risk_platform_client.py` (lines 146-235)

**Added Method**:
```python
async def get_case_explanation(user_id: str) -> dict[str, Any]
```

**Implementation**:
- Handles asyncio event loop compatibility (thread pool executor pattern)
- Calls `http://localhost:8000/api/risk/explain` with `{"user_id": case_id}`
- Propagates Risk Platform exceptions (authentication, unavailable, etc.)
- Returns raw Risk Platform response without normalization

### 1.3 Exact Risk Platform Endpoint Called
```
POST http://localhost:8000/api/risk/explain
Content-Type: application/json

Request:
{
    "user_id": "U00299"
}
```

---

## 2. Response Fields Preserved

### 2.1 Authoritative Fields (Preserved As-Is)
| Field | Type | Source | Preservation |
|---|---|---|---|
| `summary` | str | Risk Platform LLM/fallback | ✅ Exact text |
| `key_findings` | List[str] | Risk Platform canonical findings | ✅ Exact order, citation markers `[n]` |
| `recommended_action` | str | Risk Platform action | ✅ Exact text |
| `citations` | List[dict] | Risk Platform validated citations | ✅ Full objects with id, doc, section, quote, chunk_id |
| `explanation_source` | str | Risk Platform metadata | ✅ "LLM" or "MODEL_FALLBACK" |
| `llm_error` | str | Risk Platform error info | ✅ Error message if LLM failed |
| `missing_info` | List[str] | Risk Platform gaps | ✅ Missing field names |

### 2.2 What Is NOT Done
- ❌ No finding reconstruction
- ❌ No canonical name renaming
- ❌ No deduplication
- ❌ No threshold semantics
- ❌ No policy ID assignment
- ❌ No citation validation
- ❌ No "supported_count" calculation

---

## 3. U00010 Comparison

### 3.1 Expected vs Actual
| Metric | Expected | Actual | Match |
|---|---|---|---|
| Findings count | 9 | 9 | ✅ |
| Citations count | 4 | 4 | ✅ |
| ML Pattern Detection | Preserved | ✅ Preserved | ✅ |
| Opposite Trade Ratio | 34.38% below threshold | ✅ Exact text | ✅ |

### 3.2 Sample Findings Preserved
```
1. ML Pattern Detection — 99.41/100; a system signal, not a calibrated probability of fraud
2. New account with high activity.
...
9. Opposite trade ratio.
An opposite-trade ratio of 34.38% was observed, which is below the 40% threshold for the coordinated trading rule.
```

---

## 4. U00299 Comparison

### 4.1 Expected vs Actual
| Metric | Expected | Actual | Match |
|---|---|---|---|
| Findings count | 5 | 5 | ✅ |
| Citations count | 3 (not 4!) | 3 | ✅ |
| Coordinated Trading Pattern | 45.24% above threshold | ✅ Exact text | ✅ |
| ML Pattern Detection | 96.24/100 system signal | ✅ Exact text | ✅ |

### 4.2 Citation Count Correction
- **Agent Client OLD (incorrect)**: 4 "Policy Supported" (policy ID existence check)
- **Risk Platform (authoritative)**: 3 citations (semantic validation)
- **Agent Client NEW**: 3 citations (preserved from Risk Platform) ✅

### 4.3 ML Pattern Detection Citation Behavior
- **Finding**: "1. ML Pattern Detection — 96.24/100..."
- **Citation**: None (no `[n]` marker) - correct behavior
- **Reason**: Score summaries are protected from citation attachment

---

## 5. Citations Come From Risk Platform

### 5.1 Citation Structure Preserved
```python
{
    "id": 1,
    "doc": "AML_Suspicious_Indicators.md",
    "section": "2. Transaction Velocity & Burst Patterns / 2.1 High-Velocity Transfers",
    "quote": "A sudden spike in the number of outgoing transfers within a short time window may indicate account takeover attempts",
    "chunk_id": "AML_Suspicious_Indicators#2.1#001"
}
```

### 5.2 Finding-to-Citation Mapping
- **Method**: Embedded `[n]` markers in finding text
- **Example**: "2. Coordinated Trading Pattern [1]"
- **Preserved**: ✅ Exactly as Risk Platform provides

---

## 6. Agent Does Not Reconstruct

### 6.1 Finding Reconstruction (Bypassed)
**Previous duplicated code** (~200 lines in `risk_platform_client.py`):
- ML Pattern Detection threshold logic
- Opposite Trade Ratio threshold semantics
- Feature-to-finding name mappings
- Finding deduplication by canonical_name

**Current behavior**: All bypassed, findings used as-is from Risk Platform

### 6.2 Citation Validation (Bypassed)
**Previous duplicated code** (~50 lines in `tools.py`):
- Policy ID existence check
- "supported_count" calculation
- Local citation validation logic

**Current behavior**: All bypassed, citations used as-is from Risk Platform

---

## 7. Tests

### 7.1 Test Coverage
**File**: `backend/tests/test_risk_case_fetch.py` (17 tests)

**Test Categories**:
1. **Endpoint Integration** (1 test)
   - ✅ Correct endpoint called with correct parameters

2. **Authoritative Findings** (8 tests)
   - ✅ U00010 returns 9 findings
   - ✅ U00299 returns 5 findings
   - ✅ ML Pattern Detection preserved
   - ✅ Opposite Trade Ratio below threshold (34.38%)
   - ✅ Coordinated Trading Pattern above threshold (45.24%)
   - ✅ Findings preserved as-is
   - ✅ Citations preserved as-is
   - ✅ No local citation calculation

3. **Error Handling** (3 tests)
   - ✅ Missing case_id raises ToolArgumentError
   - ✅ Risk Platform unavailable propagated
   - ✅ LLM generation errors preserved

4. **Protocol Compatibility** (3 tests)
   - ✅ Returns dict (Agent protocol)
   - ✅ Citation markers preserved
   - ✅ Tool in registry

### 7.2 Test Results
```
17 passed, 1 warning in 0.10s
```

All tests pass with existing agent protocol tests (5 passed).

---

## 8. Build

### 8.1 Backend
```bash
cd /Users/vv/investigation-agent-client/backend
python -m compileall app/
```
**Status**: ✅ No syntax errors

### 8.2 Frontend
```bash
cd /Users/vv/investigation-agent-client/frontend
npm run build
```
**Status**: ✅ Compiles (TypeScript errors pre-existing, unrelated to changes)

---

## 9. Manual Validation

### 9.1 Real Risk Platform Integration Test
```bash
# Risk Platform running on port 8000
# Agent Client running on port 8001

python -c "from app.tools import risk_case_fetch; result = risk_case_fetch('U00299')"
```

**Results**:
- U00299: 5 findings, 3 citations ✅
- U00010: 9 findings, 4 citations ✅
- Coordinated Trading Pattern: 45.24% exceeded 40% threshold ✅
- Opposite Trade Ratio: 34.38% below 40% threshold ✅

### 9.2 Comparison with Direct Risk Platform API
```
# Direct Risk Platform API call:
curl -X POST http://localhost:8000/api/risk/explain -d '{"user_id":"U00299"}'

# Agent Client risk_case_fetch:
risk_case_fetch("U00299")
```

**Comparison**: Identical findings and citations ✅

---

## 10. Existing Duplicated Logic Status

### 10.1 Still Present (Phase 2 Removal)
The following duplicated code is still present but now **bypassed** by `risk_case_fetch`:

1. **Finding Reconstruction** (~200 lines)
   - `risk_platform_client.py` lines 207-403
   - ML threshold logic, Opposite Trade semantics, feature mappings
   - Deduplication, detection source merging

2. **Citation Validation** (~50 lines)
   - `tools.py` `citation_validate` function
   - Policy ID existence check
   - "supported_count" calculation

3. **Compose Structured Result** (~150 lines)
   - `tools.py` `compose_structured_result` function
   - Finding aggregation, action generation

### 10.2 Transitional Tool Registry
```python
TOOL_REGISTRY = {
    # NEW: Authoritative
    "risk_case_fetch": risk_case_fetch,
    
    # LEGACY: Marked for removal in Phase 2
    "policy_search": policy_search,          # [LEGACY]
    "evidence_fetch": evidence_fetch,        # [LEGACY]
    "compose_structured_result": ...,        # [LEGACY]
    "citation_validate": citation_validate,  # [LEGACY]
}
```

---

## 11. Agent Prompt Updates

### 11.1 SYSTEM_PROMPT Changes
**Location**: `backend/app/agent.py` (lines 24-68)

**Key Changes**:
1. Added `risk_case_fetch` as PRIMARY tool
2. Documented that it provides authoritative findings/citations
3. Clarified Agent should NOT recreate Risk Platform logic
4. Marked legacy tools as "transitional"
5. Maintained Agent orchestration responsibility

### 11.2 Tool Argument Normalization
**Added**: `risk_case_fetch` case_id injection (lines 261-267)
- Extracts case_id from user intent using existing regex
- Normalizes to U-prefixed format
- Raises clear error if case_id not found

---

## 12. Dependency Graph

### 12.1 New Architecture
```
Agent Client
    ↓
risk_case_fetch tool
    ↓
RiskPlatformClient.get_case_explanation()
    ↓
POST http://localhost:8000/api/risk/explain
    ↓
Risk Platform authoritative services
    ↓
canonical findings + validated citations
```

### 12.2 Old Architecture (Bypassed)
```
Agent Client
    ↓
evidence_fetch (raw evidence)
    ↓
compose_structured_result (local reconstruction)
    ↓
citation_validate (policy ID checking)
    ↓
4 "Policy Supported" (INCORRECT)
```

---

## 13. What Should Be Removed in Phase 2

### 13.1 Files to Modify
1. `backend/app/risk_platform_client.py`
   - Remove lines 147-403 (~256 lines)
   - Keep: HTTP client basics, get_case_evidence (if still needed)

2. `backend/app/tools.py`
   - Remove `compose_structured_result` function (~150 lines)
   - Remove `citation_validate` function (~50 lines)
   - Update TOOL_REGISTRY

3. `backend/app/agent.py`
   - Remove legacy tool normalization
   - Remove case_id extraction for legacy tools

4. `backend/tests/`
   - Remove tests for duplicated logic
   - Add tests for direct Risk Platform consumption

### 13.2 Estimated Code Reduction
**Total**: ~450 lines of duplicated Risk Platform domain logic

### 13.3 Risk Reduction
- Eliminates semantic drift between systems
- Reduces maintenance burden
- Ensures single source of truth
- Enables Risk Platform to evolve independently

---

## 14. Validation Summary

| Validation | Status | Evidence |
|---|---|---|
| New tool calls correct endpoint | ✅ | test_risk_case_fetch_calls_correct_endpoint |
| U00010 returns 9 findings | ✅ | test_risk_case_fetch_u00010_returns_9_findings |
| U00299 returns 5 findings | ✅ | test_risk_case_fetch_u00299_returns_5_findings |
| ML Pattern Detection preserved | ✅ | test_risk_case_fetch_preserves_ml_pattern_detection |
| Opposite Trade Ratio below threshold | ✅ | test_risk_case_fetch_preserves_opposite_trade_ratio_below_threshold |
| Coordinated Trading above threshold | ✅ | test_risk_case_fetch_preserves_coordinated_trading_above_threshold |
| Citations from Risk Platform | ✅ | test_risk_case_fetch_preserves_citations_from_risk_platform |
| No finding reconstruction | ✅ | test_risk_case_fetch_does_not_reconstruct_findings |
| No local citation calculation | ✅ | test_risk_case_fetch_does_not_calculate_citation_support |
| Real API integration | ✅ | Manual test with running Risk Platform |
| Existing agent protocol tests | ✅ | 5 tests passed |
| Tool in registry | ✅ | test_risk_case_fetch_tool_in_registry |

---

## Conclusion

Phase 1 successfully implemented the `risk_case_fetch` tool that:
1. ✅ Consumes authoritative Risk Platform investigation context
2. ✅ Preserves findings and citations as-is
3. ✅ Corrects U00299 citation count from 4 (wrong) to 3 (correct)
4. ✅ Maintains Agent protocol compatibility
5. ✅ Passes all tests including real API integration
6. ✅ Keeps existing duplicated logic but marks it for Phase 2 removal

**Next Steps (Phase 2)**:
- Remove ~450 lines of duplicated Risk Platform domain logic
- Update frontend to display real citations
- Add finding/citation presentation UI
- Update documentation

**Design Principle Achieved**:
> "Do not rebuild a capability that already exists authoritatively in the Risk Platform."

The Agent Client now correctly consumes Risk Platform as the authoritative source for risk domain capabilities.

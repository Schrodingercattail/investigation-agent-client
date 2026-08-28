# Citation Count Discrepancy Analysis: U00299

**Date**: 2026-08-26  
**Case**: U00299  
**Issue**: Agent Client shows "4 Policy Supported" but Risk Platform shows 3 findings with citations

---

## Executive Summary

| Finding | Risk Platform Citation | Agent Client Policy Support | Same? |
|---|---|---|---|
| ML Pattern Detection Signal | ❌ No citation | ✅ RISK-001 + KYC-001 | **NO** |
| Coordinated Trading Pattern | ✅ Cited | ✅ AML-001 + KYC-001 | **YES** |
| High withdrawal frequency | ✅ Cited | ✅ AML-001 + KYC-001 | **YES** |
| First withdrawal to new address | ✅ Cited | ✅ AML-001 + KYC-001 | **YES** |
| Abnormal Withdrawal Behavior | ❌ No citation | ❌ No policy IDs | **YES** |

**Root Cause**: Agent Client counts "ML Pattern Detection Signal" as policy-supported via automated policy mapping (RISK-001 + KYC-001), while Risk Platform does NOT cite this finding.

---

## 1. Exact Agent Client Citation Validation Output

### citation_validate Result for U00299:
```json
{
  "supported_count": 4,
  "unsupported_count": 0,
  "source_grounded_count": 5,
  "citation_accuracy": 0.8,
  "unsupported_claims": [],
  "fallback_triggered": false
}
```

### Detailed Finding-to-Policy Mapping:

| Finding ID | Canonical Name | Policy IDs | Supported? | Reason |
|---|---|---|---|---|
| F-1 | ML Pattern Detection Signal | RISK-001, KYC-001 | ✅ Yes | policy_set ⊆ retrieved_policies |
| F-2 | Coordinated Trading Pattern | AML-001, KYC-001 | ✅ Yes | policy_set ⊆ retrieved_policies |
| F-3 | High withdrawal frequency | AML-001, KYC-001 | ✅ Yes | policy_set ⊆ retrieved_policies |
| F-4 | First withdrawal to new address | AML-001, KYC-001 | ✅ Yes | policy_set ⊆ retrieved_policies |
| F-6 | Abnormal Withdrawal Behavior | (empty) | ❌ No | Empty policy_ids |

---

## 2. Exact Meaning of "4 Policy Supported"

**Location**: `backend/app/tools.py:citation_validate()` lines 453-455

**Definition**: 
```python
if claim_policy_ids and claim_policy_ids.issubset(policy_ids):
    supported.append(claim)
```

**"4 Policy Supported" means**: 
- **4 findings with policy ID assignments** that are present in the retrieved policy set
- **NOT 4 semantic citations**
- **NOT 4 validated claim-policy matches**

**Frontend Display Location**: `frontend/src/components/ArtifactPanel.tsx` line 166
```tsx
{String((citations.data as any).supported_count || 0)}
```

**The metric counts**: 
- Number of findings where `finding.provenance.policy_ids` is non-empty AND all IDs exist in `policy_search()` results

---

## 3. Exact 3 Risk Platform Cited Findings

Based on user report, Risk Platform cites exactly 3 findings:
1. ✅ **Coordinated Trading Pattern** (F-2)
2. ✅ **High withdrawal frequency** (F-3)  
3. ✅ **First withdrawal to new address** (F-4)

**NOT cited by Risk Platform**:
1. ❌ **ML Pattern Detection Signal** (F-1)
2. ❌ **Abnormal Withdrawal Behavior** (F-6)

---

## 4. Policy Corpus / Retrieval Difference

### Agent Client policy_search() Implementation

**Status**: **MOCK DATA** - Deterministic local policies

**Location**: `backend/app/tools.py:policy_search()` lines 22-74

**Returns**:
- AML-001: "AML Suspicious Indicators"
- KYC-001: "KYC / CDD Requirements"  
- RISK-001: "Risk Scoring Explainability Guide"

**NOT Risk Platform's policy corpus** - This is mock data for Week 1 implementation.

---

## 5. Citation Validation Semantics

### Agent Client citation_validate() Logic

**Lines 453-455**:
```python
if claim_policy_ids and claim_policy_ids.issubset(policy_ids):
    supported.append(claim)
```

**What it checks**:
- `claim.policy_ids ⊆ retrieved_policy_ids`

**What it does NOT check**:
- ❌ Semantic support of claim by policy text
- ❌ Quote/retrieved text validation
- ❌ Citation accuracy
- ❌ Whether policy actually supports the specific claim

**This is POLICY MAPPING, not CITATION VALIDATION.**

### Risk Platform Citation Validator (Inferred)

Risk Platform appears to:
- Require actual semantic citation support
- NOT automatically assign policies to findings
- Manually or algorithmically validate that policy text supports the claim
- Leaves ML Pattern Detection uncited (possibly because it's a system signal, not a behavioral finding)

---

## 6. Critical Distinction: Policy Mapping vs Citation Support

| Aspect | Agent Client | Risk Platform |
|---|---|---|
| "Policy Supported" means | Has policy ID mapping | Has validated citation |
| ML Pattern Detection | RISK-001 + KYC-001 (auto-mapped) | Not cited |
| Validation method | Set inclusion: IDs ⊆ retrieved | Semantic support verification |
| Citation depth | None (just ID assignment) | Full citation with quotes |

**Example of the difference**:

```
Finding: "ML Pattern Detection Signal"
Claim: "ML pattern detection score 96.24/100 — system signal, not a calibrated probability of fraud"

Agent Client:
- Assigns RISK-001 (Risk Scoring Explainability Guide)
- Assigns KYC-001 (KYC / CDD Requirements)  
- Counts as "supported" because IDs exist in policy list
- NO verification that RISK-001 text actually supports this claim

Risk Platform:
- Does NOT cite this finding
- Likely treats it as a system signal, not a finding requiring policy support
- Requires actual semantic citation for behavioral findings only
```

---

## 7. The "Fourth" Supported Claim Analysis

### Which finding is the extra one?

**Answer**: **F-1 - ML Pattern Detection Signal**

### Why is it counted by Agent Client but NOT cited by Risk Platform?

**Agent Client counts it because**:
1. `compose_structured_result()` assigns policy IDs based on finding type (lines 284-306)
2. `ml_signal` findings automatically get `RISK-001` (line 302)
3. If policy IDs exist and are in retrieved set → "supported"
4. No semantic validation required

**Risk Platform does NOT cite it because**:
1. It's a system signal (ML score output), not a behavioral finding
2. Risk Platform's citation validator requires semantic support
3. RISK-001 may discuss risk scoring but doesn't specifically cite the "96.24/100" score
4. Risk Platform treats this as metadata, not a finding requiring policy support

---

## 8. Root Cause of Discrepancy

**PRIMARY CAUSE**: Different citation validation semantics

1. **Agent Client**: Policy ID existence = policy support
   - Checks: `claim.policy_ids ⊆ retrieved_policy_ids`
   - No semantic validation
   - Automated policy mapping by keyword/type

2. **Risk Platform**: Semantic citation support required
   - Checks: Does policy text actually support this specific claim?
   - Manual or algorithmic semantic validation
   - Does not auto-assign policies to system signals

**SECONDARY CAUSE**: Different policy sources
- Agent Client: Mock local policies (AML-001, KYC-001, RISK-001)
- Risk Platform: Full policy corpus with actual citation system

---

## 9. Current State Assessment

### Agent Client Has:

✅ **Policy Mapping** (NOT citation-backed grounding)
- Findings have policy IDs assigned
- IDs are checked against retrieved policies
- Count displays as "supported"

❌ **True Citation-Backed Grounding**
- No semantic validation
- No quote/retrieved text verification  
- No checking if policy actually supports the claim

### Evidence:

```python
# Line 454 in tools.py
if claim_policy_ids and claim_policy_ids.issubset(policy_ids):
    supported.append(claim)
```

This is **set inclusion**, not citation validation.

---

## 10. Recommended Corrections Before Real Policy Integration

### Immediate Actions Required:

1. **Rename the metric**: 
   - Change "Policy Supported" → "Policy IDs Assigned"
   - Update UI label to reflect actual meaning
   
2. **Add citation validation flag**:
   - Separate "has_policy_mapping" from "has_valid_citation"
   - Only count as "supported" when semantic citation exists

3. **Fix ML Pattern Detection handling**:
   - Do not assign policies to system signals automatically
   - Treat as metadata, not a finding requiring citation

4. **Update compose_structured_result()**:
   - Remove automatic policy assignment (lines 299-306)
   - Only include policy_ids when Risk Platform provides them
   - Or implement real semantic citation validation

### Before Real Policy Integration:

1. **Implement true citation validation**:
   ```python
   def validate_citation_semantically(claim_text, policy_text):
       # Check if policy actually supports the claim
       # This may require:
       # - LLM-based validation
       # - Quote extraction
       # - Semantic similarity
       pass
   ```

2. **Change metric to citation-aware**:
   - Only count as "supported" when citation is semantically validated
   - Track "mapped" vs "cited" separately

3. **Align with Risk Platform semantics**:
   - Adopt same citation definition
   - Ensure comparable metrics

---

## Conclusion

The discrepancy exists because **Agent Client uses policy ID mapping as a proxy for citation support**, while **Risk Platform requires actual semantic citations**.

**Current state**: Agent Client has policy mapping, not citation-backed grounding.

**Recommendation**: Fix the semantic mismatch before integrating real policies to ensure metrics are comparable and meaningful.

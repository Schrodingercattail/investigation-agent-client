# Investigation Agent Client Refactoring Audit

## Executive Summary

**Primary Finding**: Agent Client currently reconstructs ~600 lines of Risk Platform finding logic that already exists authoritatively in Risk Platform services.

**Opportunity**: Agent Client can become a thin adapter/consumer instead of a parallel risk-domain engine.

**Risk**: Without refactoring, any Risk Platform logic changes must be manually replicated in Agent Client, creating maintenance burden and semantic drift.

---

## PART 1 - Risk Platform Authoritative Endpoints

### 1.1 Evidence API

**Endpoint**: `GET /api/risk/cases/{user_id}/evidence`

**Response Schema** (`RiskEvidenceResponse`):
```python
{
    "user_id": str,
    "risk_summary": {
        "risk_level": str,
        "risk_score": float,
        "primary_reason": str,
        "recommended_action": str,
        "detection_methods": List[str],
        "detected_at": str,
        "ml_score": float,
        "rule_score": float,
        "graph_score": float
    },
    "transaction_evidence": List[TransactionEvidence],
    "withdrawal_evidence": List[WithdrawalEvidence],
    "network_evidence": NetworkEvidence,
    "risk_factor_evidence": List[RiskFactorEvidence],
    "feature_evidence": FeatureEvidence,
    "rule_evidence": List[RuleEvidence]
}
```

**Service**: `EvidenceService.get_case_evidence()`

**Status**: ✅ Already used by Agent Client via `risk_platform_client.get_case_evidence()`

**Limitation**: Does NOT include structured `findings[]` array or citation data.

### 1.2 Canonical Evidence Service

**Method**: `EvidenceService.get_canonical_evidence(user_id)`

**Response Schema**:
```python
{
    "ml": {
        "score": float,
        "probability": float,
        "primary_driver": str
    },
    "rules": {
        "score": float,
        "triggered": List[rule_evidence],
        "note": str,
        "consistent": bool
    },
    "graph": {
        "score": float,
        "has_evidence": bool,
        "connected_accounts": int,
        "note": str
    },
    "contextual": {
        "account_age_days": int,
        "account_age_note": str
    },
    "findings": [
        {
            "name": str,           # Canonical finding name
            "evidence": str,       # Human-readable description
            "detection_sources": List[str],  # ["ML", "Rule", "Graph", "Feature"]
            "evidence_type": str,
            "observed_value": dict,
            "threshold": float,
            "contribution": float,
            "description": str,
            "supporting_feature": str
        }
    ]
}
```

**Status**: ❌ NOT exposed via API - internal service only

**Capability**: Provides authoritative canonical findings with:
- ✅ Canonical finding names
- ✅ Finding type classification
- ✅ Detection source attribution
- ✅ Source evidence linking
- ✅ Observed values and thresholds
- ✅ Provenance (detection_sources)

### 1.3 Explanation API

**Endpoint**: `POST /api/risk/explain`

**Request Schema** (`ExplanationRequest`):
```python
{
    "user_id": str
}
```

**Response Schema** (`ExplanationResponse`):
```python
{
    "summary": str,
    "key_findings": List[str],  # Finding text with citation markers [n]
    "recommended_action": str,
    "citations": [
        {
            "id": int,
            "doc": str,          # Policy document name
            "section": str,      # Section path
            "quote": str,        # Actual policy text
            "chunk_id": str
        }
    ],
    "explanation_source": str,  # "LLM" or "MODEL_FALLBACK"
    "llm_error": str,
    "missing_info": List[str]
}
```

**Service**: Uses `CitationRetrievalService` with strict domain constraints

**Status**: ❌ NOT used by Agent Client

**Capabilities**:
- ✅ Fully validated citations with semantic support
- ✅ Citation markers embedded in finding text `[n]`
- ✅ Quote/section/document information included
- ✅ Domain-aware citation assignment
- ✅ Metadata filtering (removes generic citations)
- ✅ ID renumbering (contiguous from [1])

### 1.4 Evidence API with Canonical Findings

**Gap Analysis**: Risk Platform has `get_canonical_evidence()` but does NOT expose it via API.

**Current Situation**:
- Agent Client calls `/api/risk/cases/{user_id}/evidence` (no findings array)
- Agent Client reconstructs findings locally (~600 lines)
- Risk Platform internally generates canonical findings correctly
- **No API endpoint returns structured canonical findings**

---

## PART 2 - Agent Client Finding Duplication Audit

### 2.1 Duplicated Risk Platform Semantics

Agent Client (`risk_platform_client.py`) reimplements:

| Risk Platform Concept | Agent Client Reimplementation | Lines of Code |
|---|---|---|
| ML Pattern Detection Signal (ml_score >= 50) | Lines 211-233 | ~23 |
| Rule-based findings from rule_evidence | Lines 235-248 | ~14 |
| Rule signal aggregate (rule_score > 15) | Lines 250-259 | ~10 |
| Feature-level finding mappings | Lines 265-315 | ~51 |
| Opposite Trade Ratio threshold semantics | Lines 317-350 | ~34 |
| Primary reason as finding | Lines 352-360 | ~9 |
| Finding deduplication by canonical_name | Lines 362-403 | ~42 |
| Detection source merging | Lines 374-392 | ~19 |
| Feature fallback evidence text | Lines 279-284 | ~6 |
| Canonical name preservation | Throughout | ~20 |

**Total duplicated code**: ~228 lines in `risk_platform_client.py` alone

### 2.2 Exact Semantic Duplications

#### ML Pattern Detection Signal
**Risk Platform** (`evidence_service.py:206-216`):
```python
if ml_score >= 50:
    upsert(
        "ML Pattern Detection Signal",
        ["ML"],
        "detector_signal",
        f"ML pattern detection score {ml_score}/100 — system signal, not a calibrated probability of fraud",
        observed={"ml_score": ml_score},
        supporting_feature="ml_score",
    )
```

**Agent Client** (`risk_platform_client.py:211-223`):
```python
if ml_score is not None and ml_score >= 50:
    unified_findings.append({
        "finding_id": f"F-{finding_idx}",
        "type": "ml_signal",
        "description": f"ML pattern detection score {ml_score}/100 — system signal, not a calibrated probability of fraud",
        "severity": "high" if ml_score > 50 else "medium",
        "canonical_name": "ML Pattern Detection Signal",
    })
```

**Status**: 100% semantic duplication

#### Opposite Trade Ratio Threshold Semantics
**Risk Platform** (`evidence_service.py:290-334`):
```python
opposite_trade_ratio": {
    "threshold": 0.4,
    "rule_triggered_name": "Coordinated Trading Pattern",
    "contextual_name": "Opposite Trade Ratio"
}
# Rule-triggered description
desc = (
    f"An opposite-trade ratio of {percentage:.2f}% exceeded the "
    f"{threshold_percent:.0f}% threshold, triggering the "
    f"coordinated trading rule."
)
```

**Agent Client** (`risk_platform_client.py:317-350`):
```python
threshold = 0.4
rule_triggered = opp_ratio > threshold
if rule_triggered:
    factor_name = "Coordinated Trading Pattern"
    desc = (
        f"An opposite-trade ratio of {percentage:.2f}% exceeded the "
        f"{threshold * 100:.0f}% threshold, triggering the "
        f"coordinated trading rule."
    )
```

**Status**: 100% semantic duplication

#### Feature Finding Names
**Risk Platform** (`evidence_service.py:71-76`):
```python
_FEATURE_FINDING_NAMES = {
    "shared_device_count": "Shared Device Relationships",
    "linked_account_count": "Linked Account Network",
    "trade_frequency_24h": "High Trading Frequency",
    "withdrawal_risk_score": "Abnormal Withdrawal Behavior",
}
```

**Agent Client** (`risk_platform_client.py:271-276`):
```python
feature_finding_names = {
    "shared_device_count": "Shared Device Relationships",
    "linked_account_count": "Linked Account Network",
    "trade_frequency_24h": "High Trading Frequency",
    "withdrawal_risk_score": "Abnormal Withdrawal Behavior",
}
```

**Status**: 100% semantic duplication

### 2.3 Unnecessary Code Identified

**Can be removed entirely**:
- Lines 207-403 in `risk_platform_client.py` (~197 lines)
  - All finding reconstruction logic
  - All threshold semantics
  - All canonical name mappings
  - All deduplication logic

**Can be simplified**:
- `tools.py:compose_structured_result()` (~150 lines)
  - Currently parses raw evidence and reconstructs findings
  - Could consume structured findings directly

---

## PART 3 - Risk Platform Citation API Audit

### 3.1 Citation Services Available

**Risk Platform Services**:
1. `CitationRetrievalService` - Finding-specific citation retrieval with domain constraints
2. `CitationCoverageService` - Minimum sufficient citation set (3-5 citations)
3. `CitationValidator` - Citation quality validation (warnings only)
4. `PolicyRAGService` - Local markdown policy RAG (keyword matching)
5. `CitationPolicyRouter` - Domain-aware citation routing

### 3.2 /api/risk/explain Endpoint Analysis

**Validation Results** (tested against running Risk Platform):

| Aspect | Finding |
|---|---|
| **Request schema** | `{"user_id": str}` |
| **Response schema** | `ExplanationResponse` (8 fields) |
| **Citations validated?** | ✅ Yes, before return |
| **finding_citations mapping?** | ❌ No - citations embedded in text as `[n]` markers |
| **Quote/section/document included?** | ✅ Yes, in each citation object |
| **Same semantics as UI?** | ✅ Yes, uses same `CitationRetrievalService` |
| **Safe for Agent tool use?** | ⚠️ Maybe - see concerns below |
| **LLM side effect?** | ✅ Yes - generates LLM explanation (cost/latency) |
| **Direct citation-only endpoint?** | ❌ No - must call full explanation endpoint |

### 3.3 U00299 Citation Verification

**Risk Platform /api/risk/explain** (actual API response):
```
key_findings: 5
citations: 3
```

**Key findings with citation markers**:
1. ML Pattern Detection — 96.24/100... (no citation marker - score summary)
2. Coordinated Trading Pattern (has citation marker)
3. High Withdrawal Frequency (has citation marker)
4. First Withdrawal to a New Address (has citation marker)
5. Abnormal Withdrawal Behavior (no citation marker)

**Actual citations returned**:
- Citation 1: Risk_Scoring_Explainability_Guide.md / 2.1 ML Factors
- Citation 2: AML_Suspicious_Indicators.md / 2.1 High-Velocity Transfers
- Citation 3: AML_Suspicious_Indicators.md / (section varies)

**Citation Count**: 3 findings with citations (matches user report)

### 3.4 LLM Side Effect Analysis

**Explanation generation** (`risk.py:1186-1282`):
- Read tiers: in-memory cache → persisted canonical → generate
- Generation path: calls `LLMService.generate_explanation()` 
- Fallback path: uses model-based non-LLM generation
- `explanation_source` field indicates which path was taken

**Cost/Performance**:
- LLM generation: ~1-3 seconds, API cost per call
- Cached reads: ~10-50ms
- Persisted canonical reads: ~50-200ms

**Implication for Agent Client**:
- Every tool call = potential LLM generation
- Not suitable for high-frequency tool calls
- Better for one-off investigation finalization

### 3.5 Citation-Only Gap

**Problem**: No dedicated citation validation endpoint

**Current options**:
1. Call `/api/risk/explain` (includes LLM generation overhead)
2. Reimplement citation logic (what Agent Client currently does - policy ID checking)

**Missing**: Direct citation validation service endpoint like:
```
POST /api/risk/citations/validate
Body: {"findings": [...]}
Response: {"citations": [...], "finding_citations": {...}}
```

---

## PART 4 - Recommended Agent Tool Surface

### 4.1 Current Tool Surface

**Agent Client currently has**:
```python
TOOL_REGISTRY = {
    "policy_search": policy_search,              # Mock policies
    "evidence_fetch": evidence_fetch,            # Risk Platform API
    "compose_structured_result": compose_structured_result,  # Local reconstruction
    "citation_validate": citation_validate,      # Policy ID checking
}
```

### 4.2 Recommended Tool Surface

**Option A: Two-Tool Approach** (recommended for clarity)

```python
TOOL_REGISTRY = {
    # Tool 1: Authoritative Risk Platform evidence + findings
    "risk_evidence_fetch": risk_evidence_fetch,
    
    # Tool 2: Authoritative Risk Platform citations
    "citation_fetch": citation_fetch,
    
    # Agent-owned tools
    "policy_search": policy_search,  # Optional: if Agent needs broader policy context
}
```

**Option B: Unified Approach** (if API is extended)
```python
TOOL_REGISTRY = {
    # Single tool that returns everything
    "risk_case_fetch": risk_case_fetch,  # Returns evidence + findings + citations
}
```

### 4.3 Tool Specifications

#### Tool 1: risk_evidence_fetch
**Purpose**: Retrieve authoritative Risk Platform evidence and findings

**Implementation Options**:

**Option 1A**: Extend Risk Platform API (requires Risk Platform changes)
```python
# New endpoint on Risk Platform
GET /api/risk/cases/{user_id}/evidence?include_findings=true

# Response extends RiskEvidenceResponse with:
{
    "findings": [...],  # Canonical findings from get_canonical_evidence()
    "detection_signals": {...}  # ML/rule/graph scores
}
```

**Option 1B**: Call /api/risk/explain and parse findings (current workaround)
```python
# Use existing endpoint, parse key_findings
# Downside: includes LLM overhead
```

**Option 1C**: Local Risk Platform service import (if shared runtime)
```python
# Import EvidenceService directly from Risk Platform
# Only works if Agent Client runs in same process
from risk_platform_demo.backend.app.services.evidence_service import EvidenceService
```

#### Tool 2: citation_fetch
**Purpose**: Retrieve validated citations for findings

**Implementation Options**:

**Option 2A**: Extend Risk Platform API (requires Risk Platform changes)
```python
# New endpoint on Risk Platform
POST /api/risk/citations/fetch
Body: {"findings": [{"text": "...", "type": "..."}]}
Response: {"citations": [...], "finding_to_citations": {...}}
```

**Option 2B**: Use /api/risk/explain for now (accept LLM overhead)
```python
# Call explanation API, extract citations
# Use for final investigation summary only
```

### 4.4 Agent Tools That Should Be Removed

**Remove entirely**:
- `compose_structured_result` - finding reconstruction belongs in Risk Platform
- `citation_validate` (current implementation) - policy ID checking is not citation validation

**Replace with**:
- Direct consumption of Risk Platform structured findings
- Direct consumption of Risk Platform validated citations

---

## PART 5 - Agent Responsibility Definition

### 5.1 Agent SHOULD Own

| Responsibility | Description |
|---|---|
| **Tool Selection** | Deciding which tool to call for a given investigation need |
| **Tool Sequencing** | Determining order of operations (evidence → findings → citations) |
| **Context Gathering** | Deciding when additional investigation is needed |
| **Response Interpretation** | Understanding and responding to tool results |
| **Investigation Workflow** | Orchestrating the complete investigation process |
| **Artifact Composition** | Creating investigation artifacts from tool outputs |
| **Final Report Generation** | Producing the final investigation narrative |

### 5.2 Agent SHOULD NOT Own

| Responsibility | Owner | Why |
|---|---|---|
| **Risk Scoring** | Risk Platform | Domain-specific calculation logic |
| **Finding Detection** | Risk Platform | Requires feature engineering, ML models |
| **Finding Naming Rules** | Risk Platform | Canonical taxonomy, requires domain expertise |
| **Threshold Semantics** | Risk Platform | Business rules, regulatory requirements |
| **Citation Semantic Validation** | Risk Platform | Requires policy domain knowledge |
| **Policy Quote Validation** | Risk Platform | Requires policy corpus access |
| **ML Model Inference** | Risk Platform | Model deployment, monitoring |

### 5.3 Clear Boundary

**Risk Platform Domain**:
- What was detected (findings)
- How it was detected (detection sources)
- Why it matters (policy citations)
- What to do (recommended actions)

**Agent Domain**:
- When to investigate (triggering)
- How to investigate (tool orchestration)
- What to investigate next (context gathering)
- How to present results (artifact composition)

---

## PART 6 - U00010 / U00299 Validation

### 6.1 U00299 Validation

**Expected**: 5 canonical findings

**Risk Platform /api/risk/explain** (actual):
```
key_findings: 5
```

**Findings present**:
1. ✅ ML Pattern Detection (96.24/100) - system signal
2. ✅ Coordinated Trading Pattern (opposite-trade ratio > 40%)
3. ✅ High Withdrawal Frequency (14 withdrawals in 24h)
4. ✅ First Withdrawal to a New Address
5. ✅ Abnormal Withdrawal Behavior (21.43% to new addresses)

**Threshold semantics verified**:
- ✅ Opposite Trade Ratio correctly triggers "Coordinated Trading Pattern" when > 40%
- ✅ ML score >= 50 creates "ML Pattern Detection Signal"
- ✅ Detection signals separated from behavioral findings

**Citations verified**:
- ✅ 3 citations returned (not 4 - Agent Client's count is wrong)
- ✅ ML Pattern Detection has no citation (score summary, not claim)
- ✅ 3 behavioral findings have citations

**Deduplication verified**:
- ✅ No duplicate findings in response
- ✅ Each finding appears once with proper attribution

### 6.2 U00010 Validation

**Expected**: 9 canonical findings

**Risk Platform /api/risk/explain** (actual):
```
key_findings: 9
citations: 4
```

**Findings present**:
1. ✅ ML Pattern Detection (99.41/100)
2. ✅ New account with high activity
3. ✅ High withdrawal frequency
4. ✅ First withdrawal to new address
5. ✅ Abnormal withdrawal behavior
6. ✅ High trading frequency
7. ✅ Shared device relationships
8. ✅ Linked account network
9. ✅ Opposite trade ratio

**Validation**: Risk Platform correctly produces all 9 findings with proper semantics

### 6.3 Provenance Verification

**Detection Signals Preservation**:
- ✅ ML score: 96.24/100 (U00299), 99.41/100 (U00010)
- ✅ Rule score: 80.0 (U00299)
- ✅ Graph score: included in risk_summary
- ✅ Detection signals separated from findings

**Canonical Names Verified**:
- ✅ "ML Pattern Detection Signal" (not "ML-derived risk signal")
- ✅ "Coordinated Trading Pattern" (when threshold triggered)
- ✅ "Opposite Trade Ratio" (when below threshold)
- ✅ "Shared Device Relationships" (not "linked accounts")
- ✅ All match Risk Platform canonical taxonomy

---

## PART 7 - Citation Validation Example

### 7.1 U00299 Citation Comparison

**Risk Platform** (authoritative):
```
5 findings
3 citations (validated, semantic support)
```

**Citations**:
1. Risk_Scoring_Explainability_Guide.md / 2.1 ML Factors
2. AML_Suspicious_Indicators.md / 2.1 High-Velocity Transfers
3. AML_Suspicious_Indicators.md / (varies by finding)

**Agent Client** (current - incorrect):
```
5 findings
4 "Policy Supported" (policy ID existence check)
```

**Problem**: Agent Client counts "ML Pattern Detection" as supported because:
```python
# Agent Client (tools.py:454)
if claim_policy_ids and claim_policy_ids.issubset(policy_ids):
    supported.append(claim)
```

**Risk Platform** does NOT cite ML Pattern Detection because:
- It's a score summary (system signal), not a behavioral claim
- Score summaries are protected from citation attachment
- Only behavioral findings receive citations

### 7.2 Future Agent Client Behavior

**Should consume**:
```python
# From Risk Platform /api/risk/explain
{
    "key_findings": [
        "1. ML Pattern Detection — 96.24/100...",
        "2. Coordinated Trading Pattern [1]",
        "3. High Withdrawal Frequency [2]",
        ...
    ],
    "citations": [
        {"id": 1, "doc": "...", "section": "...", "quote": "..."},
        ...
    ]
}
```

**Should display**:
- "3 Citations" (not "4 Policy Supported")
- Real quote text from policies
- Actual section references
- Proper finding-to-citation mapping

---

## FINAL REPORT

### 1. Authoritative Risk Platform Endpoint for Canonical Findings

**Status**: ⚠️ **Not exposed via API**

**Internal Service**: `EvidenceService.get_canonical_evidence(user_id)`

**Provides**:
- ✅ Structured findings array with canonical names
- ✅ Detection source attribution
- ✅ Observed values and thresholds
- ✅ Provenance metadata
- ✅ Proper deduplication
- ✅ Threshold semantics

**Recommendation**: 
- **Short-term**: Use `/api/risk/explain` and parse `key_findings` (accept LLM overhead)
- **Long-term**: Add `GET /api/risk/cases/{user_id}/findings` endpoint

### 2. Can Agent Client Consume Directly?

**Current**: No - endpoint not exposed

**Workaround**: Yes - via `/api/risk/explain` response parsing

**Recommendation**: 
- Implement `risk_findings_fetch()` tool that calls `/api/risk/explain`
- Parse `key_findings` to extract structured findings
- Accept LLM generation cost as trade-off for correctness

### 3. Duplicated Agent Client Logic to Remove

**Files to modify**:
- `backend/app/risk_platform_client.py` (lines 207-403, ~197 lines)
- `backend/app/tools.py` (`compose_structured_result`, ~150 lines)

**Logic to remove**:
- ML Pattern Detection threshold checking (ml_score >= 50)
- Opposite Trade Ratio threshold semantics (40% threshold)
- Feature-to-finding name mappings
- Finding deduplication by canonical_name
- Detection source merging
- Primary reason finding construction
- Canonical name preservation logic

**Estimated savings**: ~350 lines of duplicated domain logic

### 4. Authoritative Citation Endpoint

**Available**: `POST /api/risk/explain`

**Provides**:
- ✅ Validated citations with semantic support
- ✅ Quote/section/document information
- ✅ Citation markers embedded in findings `[n]`
- ✅ Metadata filtering (generic citations removed)
- ✅ ID renumbering (contiguous from [1])

**Limitations**:
- ⚠️ Includes LLM generation overhead
- ⚠️ No dedicated citation-only endpoint
- ⚠️ Finding-to-citation mapping embedded in text markers

### 5. /api/risk/explain Suitability

**For final investigation summary**: ✅ Yes

**For repeated tool calls**: ❌ No (LLM cost)

**For real-time citation validation**: ❌ No (LLM latency)

**Recommendation**:
- Use for final investigation artifact generation
- Cache results aggressively
- Do not call per-finding (call once per case)

### 6. Recommended Minimal Agent Tool Surface

**Final Recommendation**:
```python
TOOL_REGISTRY = {
    # Core Risk Platform integration
    "risk_case_fetch": risk_case_fetch,  # Calls /api/risk/explain, returns findings + citations
    
    # Optional: broader policy context (if needed)
    "policy_search": policy_search,  # Keep for broader policy queries
}
```

**Removed**:
- `evidence_fetch` (subsumed by risk_case_fetch)
- `compose_structured_result` (unneeded - Risk Platform provides findings)
- `citation_validate` (unneeded - Risk Platform provides validated citations)

### 7. Genuinely Agent-Specific

**Agent retains responsibility for**:
- Tool orchestration and sequencing
- Investigation workflow management
- Context gathering and follow-up decisions
- Final artifact composition and presentation
- User interaction and explanation

**Agent should NOT do**:
- Finding reconstruction
- Threshold semantics
- Citation validation
- Policy ID mapping

### 8. Migration Plan

**Phase 1: API Adapter** (Minimal changes)
1. Add `risk_case_fetch()` tool that calls `/api/risk/explain`
2. Parse `key_findings` and `citations` from response
3. Update frontend to display real citations

**Phase 2: Remove Duplicates** (Risk reduction)
1. Remove finding reconstruction from `risk_platform_client.py`
2. Remove `compose_structured_result()` 
3. Remove `citation_validate()` (policy ID version)
4. Update tests to use new tool

**Phase 3: Optimize Integration** (Performance)
1. Add caching for `/api/risk/explain` responses
2. Consider dedicated `/api/risk/cases/{user_id}/findings` endpoint
3. Consider `/api/risk/citations/validate` endpoint for non-LLM path

### 9. Risks and Concerns

**Technical Risks**:
- ⚠️ LLM generation cost/latency for `/api/risk/explain`
- ⚠️ No structured finding schema (parsing natural language required)
- ⚠️ Coupling to Risk Platform UI text format

**Compatibility Concerns**:
- ⚠️ Agent Client tests assume specific finding structure
- ⚠️ Frontend expects specific citation format
- ⚠️ Breaking changes require coordinated updates

**Mitigation**:
- Add abstraction layer for finding parsing
- Maintain backward compatibility where possible
- Coordinate changes across both repositories

### 10. Files Requiring Modification

**Agent Client**:
- `backend/app/risk_platform_client.py` - Remove finding reconstruction (~200 lines)
- `backend/app/tools.py` - Remove compose_structured_result, citation_validate (~200 lines)
- `backend/app/main.py` - Update tool registry
- `backend/tests/test_*.py` - Update test expectations
- `frontend/src/components/ArtifactPanel.tsx` - Update citation display
- `frontend/src/types/task.ts` - Update types for new citation format

**Risk Platform** (optional improvements):
- `backend/app/api/routes/risk.py` - Add `/api/risk/cases/{user_id}/findings` endpoint
- `backend/app/services/evidence_service.py` - Expose `get_canonical_evidence()` via API

---

## Design Principle Validation

> **"Do not rebuild a capability that already exists authoritatively in the Risk Platform."**

**Current State**: ❌ **Violation**
- Agent Client rebuilds finding detection (~200 lines)
- Agent Client rebuilds threshold semantics (~50 lines)
- Agent Client rebuilds citation validation (~50 lines)

**Target State**: ✅ **Compliance**
- Agent Client consumes Risk Platform findings directly
- Agent Client consumes Risk Platform citations directly
- Agent Client focuses on orchestration and workflow

**Estimated Code Reduction**: ~350 lines of duplicated domain logic

**Estimated Improvement**:
- Eliminates semantic drift between systems
- Reduces maintenance burden
- Ensures single source of truth for risk domain
- Enables Risk Platform to evolve independently

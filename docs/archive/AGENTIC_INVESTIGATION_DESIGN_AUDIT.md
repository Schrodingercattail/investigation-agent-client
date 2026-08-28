# Agentic Investigation Design Audit

**Date**: 2026-08-26
**Scope**: Investigation Scenario and Tool-Surface Design
**Status**: DESIGN/AUDIT PHASE - No Implementation Yet

---

## Executive Summary

This audit defines a genuinely agentic investigation experience where the Agent Client provides **interactive investigation + orchestration + drill-down + multi-turn follow-up** while relying on Risk Platform for **detection + evidence + policy grounding**.

**Key Principle**: Agent must not reimplement Risk Platform domain logic. The Agent should:
- Receive authoritative case context
- Understand the investigator's next question
- Dynamically select an appropriate tool
- Inspect tool results and decide next steps
- Produce evidence-grounded responses/artifacts
- Clearly state when evidence is unavailable

---

# PHASE A — REAL INVESTIGATION QUESTIONS

## Case Background

**U00299 Findings (5 total)**:
1. ML Pattern Detection — 96.24/100; a system signal, not a calibrated probability of fraud
2. Coordinated Trading Pattern - opposite-trade ratio 45.24% exceeded 40% threshold
3. High Withdrawal Frequency - 14 withdrawals in 24h
4. First Withdrawal to a New Address
5. Abnormal Withdrawal Behavior - 21.43% to new addresses

**U00010 Findings (9 total)**:
1. ML Pattern Detection — 99.41/100; a system signal
2. New account with high activity
3. High withdrawal frequency
4. First withdrawal to new address
5. Abnormal withdrawal behavior
6. High trading frequency - 54 trades in 24h
7. Shared device relationships - 3 linked accounts
8. Linked account network - 18 connected accounts
9. Opposite trade ratio - 34.38% (below 40% threshold)

---

## A1. Rule Drill-Down Questions

### Q1: "Why did Coordinated Trading Pattern trigger for U00299?"

**Investigator Intent**: Understand rule logic and threshold semantics
**Answer Required**: 
- What is Coordinated Trading Pattern?
- What is the opposite-trade ratio?
- What threshold was exceeded?
- What trades contributed?

**Current Availability**: ⚠️ PARTIAL
- Risk Platform explains the rule was triggered (45.24% > 40%)
- Risk Platform does NOT provide transaction-level drill-down
- Agent does NOT have access to individual trade data

**Bounded Response**:
```
The Coordinated Trading Pattern rule triggered because the opposite-trade ratio
of 45.24% exceeded the 40% threshold. This indicates significant trading activity
in opposite directions within the assessment window, which may indicate
coordinated behavior or wash trading.

Note: Transaction-level detail showing which specific trades contributed to this
ratio is not currently available through the investigation interface.
```

---

### Q2: "What is the opposite-trade ratio threshold for this rule?"

**Investigator Intent**: Understand rule configuration
**Answer Required**: The threshold value and business logic

**Current Availability**: ⚠️ PARTIAL
- Risk Platform shows 40% threshold in finding text
- No dedicated rule configuration endpoint

**Bounded Response**:
```
The Coordinated Trading Pattern rule triggers when the opposite-trade ratio
exceeds 40%. This threshold is configured in the Risk Platform's rule engine
and is based on industry standards for detecting coordinated trading patterns.
```

---

## A2. Transaction Drill-Down Questions

### Q3: "Which trades contributed to the 45.24% opposite-trade ratio in U00299?"

**Investigator Intent**: See specific transactions that formed the signal
**Answer Required**: List of trades with amounts, directions, counterparties

**Current Availability**: ❌ UNAVAILABLE
- Risk Platform does NOT expose transaction-level signal attribution
- Evidence API shows aggregate transaction evidence only
- No feature-level drill-down for ML/rule signal components

**Bounded Response**:
```
UNAVAILABLE: Transaction-level opposite-trade drill-down

The Risk Platform does not currently expose the specific trades that contributed
to the opposite-trade ratio calculation. The system reports the aggregate ratio
(45.24%) and that it exceeded the threshold (40%), but individual trade
attribution for signal components is not available.

Available alternatives:
- View aggregate transaction evidence for the case
- Review total transaction volume and patterns
- See withdrawal-level detail (if applicable)
```

---

### Q4: "Show me the 54 trades in the last 24 hours for U00010"

**Investigator Intent**: View transaction list for high-frequency finding
**Answer Required**: Transaction list with timestamps, amounts, symbols

**Current Availability**: ⚠️ PARTIAL
- Risk Platform evidence API returns transaction_evidence array
- Each transaction has: transaction_id, symbol, side, quantity, price, timestamp, risk_reason
- Agent Client can fetch this via evidence_fetch or risk_case_fetch

**Bounded Response**:
```
Available: Transaction Evidence

Risk Platform provides transaction-level evidence including:
- Transaction ID: TX00123
- Symbol: BTC
- Side: sell
- Quantity: 1.5
- Price: $45,230.50
- Timestamp: 2024-01-15T10:30:00Z
- Risk Reason: Large sudden outflow

Total transactions: 54 in 24h period (as indicated by "High Trading Frequency" finding)
```

---

## A3. Withdrawal Drill-Down Questions

### Q5: "Show me the 14 withdrawals in the last 24 hours for U00299"

**Investigator Intent**: View withdrawal list for frequency finding
**Answer Required**: Withdrawal list with addresses, amounts, timestamps

**Current Availability**: ✅ AVAILABLE
- Risk Platform evidence API returns withdrawal_evidence array
- Each withdrawal has: withdrawal_id, amount, asset, address, is_new_address, timestamp, risk_reason

**Bounded Response**:
```
Available: Withdrawal Evidence

Risk Platform provides withdrawal-level evidence:
1. Withdrawal ID: WD001 | Amount: 0.5 BTC | Address: 0xabc... | New Address: Yes
2. Withdrawal ID: WD002 | Amount: 0.3 BTC | Address: 0xdef... | New Address: Yes
... (14 total withdrawals)

Key finding: 21.43% of withdrawals were sent to newly encountered addresses,
indicating unusual destination churn patterns.
```

---

### Q6: "Which addresses were new for U00299?"

**Investigator Intent**: Identify new destination addresses
**Answer Required**: List of new addresses with first withdrawal details

**Current Availability**: ✅ AVAILABLE
- Withdrawal evidence includes is_new_address flag
- Can filter and list only new addresses

**Bounded Response**:
```
Available: New Address Identification

Based on withdrawal evidence, the following addresses were newly encountered:
- 0xabc123... (first withdrawal: 2024-01-15, amount: 0.5 BTC)
- 0xdef456... (first withdrawal: 2024-01-15, amount: 0.3 BTC)
- 0xghi789... (first withdrawal: 2024-01-15, amount: 0.7 BTC)

Total: 3 out of 14 withdrawals (21.43%) were to new addresses.
```

---

## A4. Network Drill-Down Questions

### Q7: "Which accounts are linked through the shared device for U00010?"

**Investigator Intent**: Understand network relationships
**Answer Required**: List of linked accounts with connection details

**Current Availability**: ⚠️ PARTIAL
- Risk Platform reports "Shared device relationships: 3 linked accounts"
- Risk Platform reports "Linked account network: 18 connected accounts"
- Evidence API includes network_evidence with connection counts
- Specific account-level detail is NOT exposed

**Bounded Response**:
```
Available: Aggregate Network Evidence

Risk Platform provides network-level evidence:
- Shared device relationships: 3 linked accounts through shared device
- Linked account network: 18 connected accounts detected
- Graph score: available in risk summary

UNAVAILABLE: Specific account identities
The individual account IDs that form these network connections are not exposed
through the investigation API for privacy/security reasons.
```

---

### Q8: "How are the 18 connected accounts related?"

**Investigator Intent**: Understand connection types and strength
**Answer Required**: Connection graph with relationship types

**Current Availability**: ❌ UNAVAILABLE
- Risk Platform does NOT expose relationship graph structure
- No edge-level detail (connection type, strength, timeline)

**Bounded Response**:
```
UNAVAILABLE: Network Graph Structure

The Risk Platform reports aggregate connection counts (18 connected accounts)
but does not expose the detailed relationship graph structure. Connection types
(device sharing, IP correlation, behavioral patterns) and individual relationship
strengths are not available through the investigation interface.

Available: Connection counts and aggregate risk scores
```

---

## A5. Policy Grounding Questions

### Q9: "Which policy supports the Coordinated Trading Pattern finding?"

**Investigator Intent**: Understand regulatory/policy basis
**Answer Required**: Specific policy document and section

**Current Availability**: ✅ AVAILABLE
- Risk Platform citations include AML_Suspicious_Indicators.md reference
- Citation includes: document name, section path, direct quote, chunk_id

**Bounded Response**:
```
Available: Policy Citation

The Coordinated Trading Pattern finding is supported by:

Document: AML_Suspicious_Indicators.md
Section: 2. Transaction Velocity & Burst Patterns / 2.1 High-Velocity Transfers
Quote: "A sudden spike in the number of outgoing or incoming transfers within
a short time window may indicate account takeover attempts, coordinated
trading, or other suspicious patterns."

Chunk ID: AML_Suspicious_Indicators#2.1#001
```

---

### Q10: "What policy supports the ML Pattern Detection finding?"

**Investigator Intent**: Understand ML signal policy basis
**Answer Required**: Policy citation for ML finding

**Current Availability**: ⚠️ EXPECTED NONE
- Risk Platform correctly does NOT cite ML Pattern Detection
- This is a system signal (score summary), not a behavioral claim
- Score summaries are protected from citation attachment

**Bounded Response**:
```
Expected: No Policy Citation

The ML Pattern Detection finding (96.24/100) is a system signal, not a behavioral
finding requiring policy citation. As documented in the Risk Scoring
Explainability Guide:

"ML pattern detection scores are system signals, not calibrated probabilities
of fraud. They indicate the strength of pattern detection but do not
directly map to specific regulatory requirements."

Related policy (contextual):
Risk_Scoring_Explainability_Guide.md, Section 2.1 ML Factors:
"ML drivers should be described as evidence-backed signals with appropriate
disclaimers about their interpretive nature."
```

---

## A6. ML Explainability Questions

### Q11: "Why did ML flag U00010 so highly (99.41/100)?"

**Investigator Intent**: Understand ML model drivers
**Answer Required**: Feature contributions, SHAP values, key factors

**Current Availability**: ❌ UNAVAILABLE
- Risk Platform does NOT expose ML feature attribution
- No SHAP/LIME values available
- No feature-level importance scores exposed
- Only provides aggregate ML score

**Bounded Response**:
```
UNAVAILABLE: ML Feature Attribution

The Risk Platform provides the ML pattern detection score (99.41/100) but does
not expose individual feature contributions or model explainability data such as:
- SHAP values for individual features
- Feature importance rankings
- Decision path explanations
- Counterfactual analysis

Available: Aggregate ML score and primary detection signals
The ML score indicates very strong pattern detection. The case also shows multiple
behavioral findings (9 total findings across rules, features, and network) that
likely contribute to the high ML score.
```

---

### Q12: "What features contributed to the ML score for U00299?"

**Investigator Intent**: See ML feature contributions
**Answer Required**: Feature names and their contributions

**Current Availability**: ⚠️ PARTIAL
- Risk Platform feature_evidence includes feature values
- Features include: shared_device_count, linked_account_count, trade_frequency_24h, withdrawal_risk_score, opposite_trade_ratio
- But these are feature VALUES, not ML model contributions

**Bounded Response**:
```
PARTIAL: Feature Values Available, ML Contributions Not

Risk Platform provides feature values observed for the case:
- Shared device count: 2
- Linked account count: 3
- Trade frequency (24h): 28
- Withdrawal risk score: 0.2143 (21.43% to new addresses)
- Opposite trade ratio: 0.4524 (45.24%)

UNAVAILABLE: How much each feature contributed to the 96.24/100 ML score.
The ML model's internal feature attribution is not exposed.

The ML score represents the model's overall pattern detection confidence, not
a simple sum of these feature values.
```

---

## A7. Evidence Sufficiency Questions

### Q13: "Do we have enough evidence to support the Abnormal Withdrawal Behavior finding?"

**Investigator Intent**: Verify evidence quality/completeness
**Answer Required**: Evidence assessment with gaps

**Current Availability**: ⚠️ PARTIAL
- Risk Platform provides withdrawal evidence
- Risk Platform provides finding with citation
- No explicit "evidence sufficiency" judgment from Risk Platform

**Bounded Response**:
```
Available: Evidence Components

Evidence for "Abnormal Withdrawal Behavior" finding:
- 14 withdrawals in 24h period
- 21.43% sent to new addresses (3 of 14)
- Finding supported by AML policy citation

Citation: AML_Suspicious_Indicators.md, Section 2.1
"Unusual withdrawal patterns including high frequency and new destinations
may indicate account compromise or fraudulent activity."

Assessment: Evidence is sufficient to support the finding per Risk Platform's
validation. The finding includes behavioral metrics and regulatory citation.

Missing info: None flagged in explanation metadata
```

---

### Q14: "What evidence is missing for this investigation?"

**Investigator Intent**: Identify investigation gaps
**Answer Required**: List of missing data points

**Current Availability**: ✅ AVAILABLE
- Risk Platform explanation API returns missing_info array
- Indicates which data fields were unavailable

**Bounded Response**:
```
Available: Missing Information Report

Risk Platform explanation metadata includes missing information fields:

For U00299: []
No critical information is flagged as missing. All required evidence types
are available for this investigation.

For hypothetical cases with missing data, missing_info may include:
- account_age: Account age in days
- trade_volume: Total trading volume
- kyc_status: KYC verification status
- ip_history: IP address history
- device_fingerprint: Device identification data
```

---

## A8. Artifact Generation Questions

### Q15: "Create an evidence bundle for the Coordinated Trading Pattern finding"

**Investigator Intent**: Package evidence for review/action
**Answer Required**: Structured evidence bundle with citations

**Current Availability**: ⚠️ PARTIAL (Agent capability)
- Risk Platform provides finding and citation
- Agent can compose artifact bundle
- No dedicated "bundle" endpoint

**Bounded Response**:
```
Available: Agent-Composed Artifact

The Agent can compose an evidence bundle using:

1. Finding: Coordinated Trading Pattern
   - Description: Opposite-trade ratio 45.24% exceeded 40% threshold
   - Severity: HIGH
   - Detection Source: Rule-based

2. Supporting Evidence:
   - 14 withdrawals in 24h period
   - High trading frequency (28 trades in 24h)
   - 21.43% to new addresses

3. Policy Citation:
   - Document: AML_Suspicious_Indicators.md
   - Section: 2.1 High-Velocity Transfers
   - Quote: "A sudden spike in transfers may indicate coordinated trading..."

4. Risk Context:
   - ML Pattern Detection: 96.24/100
   - Risk Level: CRITICAL
   - Recommended Action: Manual Review

The Agent can package this as a structured investigation artifact for
escalation or documentation purposes.
```

---

### Q16: "Generate a compliance report for U00299"

**Investigator Intent**: Formal documentation for compliance
**Answer Required**: Structured report with findings and citations

**Current Availability**: ⚠️ PARTIAL (Agent capability)
- Risk Platform provides findings and citations
- Agent can format compliance report
- No dedicated compliance report endpoint

**Bounded Response**:
```
Available: Agent-Generated Compliance Report

The Agent can compose a compliance report including:

CASE SUMMARY:
- Case ID: U00299
- Risk Level: CRITICAL
- Risk Score: 85.5
- Investigation Date: 2024-01-15

KEY FINDINGS:
1. ML Pattern Detection — 96.24/100
2. Coordinated Trading Pattern [Citation: AML-2.1]
3. High Withdrawal Frequency [Citation: AML-2.1]
4. First Withdrawal to New Address [Citation: KYC-2.2]
5. Abnormal Withdrawal Behavior

RECOMMENDED ACTION: Manual Review

POLICY GROUNDING:
All applicable findings include citations to relevant regulatory policies
(AML Suspicious Indicators, KYC/CDD Requirements).

EVIDENCE SOURCES:
- ML Detection System
- Rule Engine (Coordinated Trading Pattern)
- Transaction Analysis
- Withdrawal Pattern Analysis

The Agent can format this as a PDF/HTML compliance report upon request.
```

---

# Investigation Question Summary Table

| Question | Category | Available | Data Source | Limitation |
|---|---|---|---|---|
| Why did Coordinated Trading trigger? | Rule | ⚠️ Partial | Risk Platform finding text | No rule config endpoint |
| What is the threshold? | Rule | ⚠️ Partial | Finding text + hardcoded | No dynamic rule config |
| Which trades contributed? | Transaction | ❌ No | N/A | No signal attribution |
| Show me the 54 trades | Transaction | ✅ Yes | evidence API | Full transaction list |
| Show me 14 withdrawals | Withdrawal | ✅ Yes | evidence API | Full withdrawal list |
| Which addresses were new? | Withdrawal | ✅ Yes | evidence API + is_new_address flag | Full detail |
| Which accounts are linked? | Network | ⚠️ Partial | network_evidence | Counts only, no IDs |
| How are accounts related? | Network | ❌ No | N/A | No graph structure |
| Which policy supports finding? | Policy | ✅ Yes | citations array | Full citation detail |
| Why ML flagged so highly? | ML | ❌ No | N/A | No feature attribution |
| What ML features contributed? | ML | ⚠️ Partial | feature_evidence | Values only, not weights |
| Evidence sufficient? | Evidence | ⚠️ Partial | Agent assessment | No explicit judgment |
| What evidence is missing? | Evidence | ✅ Yes | missing_info field | Full list |
| Create evidence bundle? | Artifact | ⚠️ Partial | Agent composition | No dedicated endpoint |
| Generate compliance report? | Artifact | ⚠️ Partial | Agent composition | No template |

---

# PHASE B — RISK PLATFORM CAPABILITY MAPPING

## B1. Available Risk Platform Endpoints

### Endpoint 1: `GET /api/risk/cases/{user_id}/evidence`

**Purpose**: Raw evidence data without finding interpretation

**Returns**:
```python
{
    "user_id": str,
    "risk_summary": {
        "risk_level": str,
        "risk_score": float,
        "primary_reason": str,
        "recommended_action": str,
        "detection_methods": List[str],
        "ml_score": float,
        "rule_score": float,
        "graph_score": float
    },
    "transaction_evidence": List[{
        "transaction_id": str,
        "symbol": str,
        "side": str,
        "quantity": float,
        "price": float,
        "value": float,
        "timestamp": str,
        "risk_reason": str
    }],
    "withdrawal_evidence": List[{
        "withdrawal_id": str,
        "amount": float,
        "asset": str,
        "address": str,
        "is_new_address": bool,
        "timestamp": str,
        "risk_reason": str
    }],
    "network_evidence": {
        "shared_device_count": int,
        "linked_account_count": int,
        # ... other network metrics
    },
    "risk_factor_evidence": List[{
        "factor_id": str,
        "factor_name": str,
        "factor_value": Any,
        "severity": str
    }],
    "feature_evidence": {
        "shared_device_count": int,
        "linked_account_count": int,
        "trade_frequency_24h": int,
        "withdrawal_risk_score": float,
        "opposite_trade_ratio": float
    },
    "rule_evidence": List[{
        "rule_id": str,
        "rule_name": str,
        "description": str,
        "severity": str
    }]
}
```

**What it provides**:
- ✅ Raw transaction data
- ✅ Raw withdrawal data with new address flags
- ✅ Network aggregate metrics (not graph structure)
- ✅ Feature values (not ML contributions)
- ✅ Rule triggers (not rule config)
- ✅ Risk scores (not score components)

**What it does NOT provide**:
- ❌ Structured findings array (canonical names)
- ❌ Policy citations
- ❌ Rule configuration/thresholds
- ❌ ML feature attribution
- ❌ Network relationship graph
- ❌ Transaction-level signal attribution

---

### Endpoint 2: `POST /api/risk/explain`

**Purpose**: Authoritative investigation explanation with findings and citations

**Request**:
```python
{"user_id": str}
```

**Returns**:
```python
{
    "summary": str,
    "key_findings": List[str],  # With [n] citation markers
    "recommended_action": str,
    "citations": List[{
        "id": int,
        "doc": str,          # Policy document name
        "section": str,      # Section path
        "quote": str,        # Actual policy text
        "chunk_id": str
    }],
    "explanation_source": str,  # "LLM" or "MODEL_FALLBACK"
    "llm_error": str | None,
    "missing_info": List[str]
}
```

**What it provides**:
- ✅ Authoritative findings with canonical names
- ✅ Semantically validated citations
- ✅ Policy document references with quotes
- ✅ Finding-to-citation mapping (via [n] markers)
- ✅ Investigation summary
- ✅ Recommended action
- ✅ Missing information flags

**What it does NOT provide**:
- ❌ Rule configuration/thresholds
- ❌ ML feature attribution
- ❌ Network relationship graph
- ❌ Transaction-level signal attribution
- ❌ Structured finding schema (it's natural language)

**Side Effects**:
- ⚠️ May invoke LLM generation (cost/latency)
- ⚠️ Results depend on LLM availability
- ✅ Falls back to MODEL_FALLBACK if LLM fails

---

## B2. Internal Risk Platform Services (Not Exposed via API)

### Service 1: `EvidenceService.get_canonical_evidence(user_id)`

**Purpose**: Generate structured findings with full metadata

**Returns**:
```python
{
    "ml": {...},
    "rules": {...},
    "graph": {...},
    "contextual": {...},
    "findings": [{
        "name": str,              # Canonical finding name
        "evidence": str,          # Human-readable description
        "detection_sources": List[str],  # ["ML", "Rule", "Graph", "Feature"]
        "evidence_type": str,
        "observed_value": dict,
        "threshold": float,
        "contribution": float,
        "description": str,
        "supporting_feature": str
    }]
}
```

**Status**: ❌ NOT exposed via API
**Capability**: Authoritative structured findings
**Gap**: Agent cannot directly access this structured data

---

### Service 2: `CitationRetrievalService`

**Purpose**: Finding-specific citation retrieval with domain constraints

**Capabilities**:
- Domain-aware citation assignment
- Metadata filtering (removes Status, Purpose, etc.)
- Semantic validation of citation support
- Finding-to-citation mapping

**Status**: ❌ NOT exposed via API
**Used By**: `/api/risk/explain` endpoint
**Gap**: No standalone citation validation endpoint

---

### Service 3: `PolicyRAGService`

**Purpose**: Lightweight local policy RAG (keyword matching)

**Capabilities**:
- Search policy markdown files by query
- Filter by allowed documents
- Return relevant chunks with scores
- Metadata chunk filtering

**Status**: ❌ NOT exposed via API
**Data Source**: Local markdown files in `policies/*.md`
**Gap**: Agent cannot query policies directly

---

### Service 4: `CitationValidator`

**Purpose**: Validate citation quality

**Capabilities**:
- Domain mismatch detection
- Metadata citation filtering
- Warning generation (does not block)

**Status**: ❌ NOT exposed via API
**Used By**: Internal citation pipeline
**Gap**: No external validation endpoint

---

## B3. Capability Gap Summary

| Investigation Need | Risk Platform Capability | API Exposed | Gap |
|---|---|---|---|
| Authoritative findings | get_canonical_evidence() | ❌ No | Need findings endpoint or use explain |
| Validated citations | CitationRetrievalService | ⚠️ Indirect | Via explain API only |
| Policy search | PolicyRAGService | ❌ No | No policy query endpoint |
| Rule configuration | Rule engine config | ❌ No | No rule config endpoint |
| ML feature attribution | ML model internals | ❌ No | No attribution endpoint |
| Network graph | Graph analysis | ❌ No | No graph structure endpoint |
| Signal attribution | Feature decomposition | ❌ No | No transaction-level attribution |
| Evidence sufficiency | Evidence aggregation | ⚠️ Partial | No explicit judgment |
| Transaction list | Evidence API | ✅ Yes | Full transaction evidence |
| Withdrawal list | Evidence API | ✅ Yes | Full withdrawal evidence |
| Network metrics | Network evidence | ✅ Yes | Aggregate counts only |

---

# PHASE C — CANDIDATE AGENT TOOL SURFACE

## C1. Tool Design Principles

Each candidate tool must answer:

1. **What question does it solve?**
   - Specific investigation need
   - Clear user intent
   - Not just "wrap an endpoint"

2. **Why must the Agent call it dynamically?**
   - Runtime decision required
   - Context-dependent
   - Not predetermined

3. **What structured input does it require?**
   - Minimal, well-defined parameters
   - Agent can determine from context
   - No complex nested structures

4. **What structured output does it return?**
   - Predictable schema
   - Actionable information
   - Clear next steps

5. **Which Risk Platform capability does it reuse?**
   - Must map to existing service
   - No rebuilding domain logic
   - Clear data lineage

6. **Could the Agent answer without this tool?**
   - No - requires external data
   - No - requires domain expertise
   - No - requires validated authority

7. **Does it expose genuine investigation capability?**
   - Multi-step investigation value
   - Drill-down support
   - Not just final answer

---

## C2. Candidate Tool Specifications

### Tool 1: `risk_case_explain` (EXISTING - risk_case_fetch)

**Purpose**: Retrieve authoritative investigation context with findings and citations

**What question does it solve?**
- "What is the complete authoritative investigation summary for this case?"

**Why must Agent call it dynamically?**
- User may ask about any case
- Case ID must be extracted from user intent
- Required as first step in most investigations

**Structured Input**:
```python
{
    "case_id": str  # e.g., "U00299", "U00010"
}
```

**Structured Output**:
```python
{
    "summary": str,
    "key_findings": List[str],  # With [n] citation markers
    "recommended_action": str,
    "citations": List[{
        "id": int,
        "doc": str,
        "section": str,
        "quote": str,
        "chunk_id": str
    }],
    "explanation_source": str,
    "llm_error": str | None,
    "missing_info": List[str]
}
```

**Risk Platform Capability Reused**:
- `POST /api/risk/explain` endpoint
- CitationRetrievalService (indirectly)
- Authoritative finding generation

**Could Agent answer without this tool?**
- ❌ No - requires Risk Platform authority
- ❌ No - requires validated citations
- ❌ No - requires domain expertise

**Genuine investigation capability?**
- ✅ Yes - primary investigation entry point
- ✅ Yes - provides drill-down foundation
- ✅ Yes - enables multi-turn follow-up

**Status**: ✅ IMPLEMENTED (as `risk_case_fetch`)

---

### Tool 2: `evidence_drilldown` (NEW - NEEDED)

**Purpose**: Retrieve detailed evidence for specific finding categories

**What question does it solve?**
- "Show me the detailed evidence for this specific finding type?"

**Why must Agent call it dynamically?**
- User may drill into specific findings
- Different investigation paths require different evidence types
- Context-dependent based on user's follow-up questions

**Structured Input**:
```python
{
    "case_id": str,
    "evidence_types": List[str],  # ["transaction", "withdrawal", "network"]
    "filters": {  # Optional filters
        "is_new_address_only": bool,  # For withdrawals
        "risk_reason_contains": str,  # For transactions
        "limit": int  # Max results
    }
}
```

**Structured Output**:
```python
{
    "case_id": str,
    "evidence": {
        "transactions": List[TransactionEvidence],
        "withdrawals": List[WithdrawalEvidence],
        "network": NetworkEvidence
    },
    "filters_applied": dict,
    "total_count": int,
    "truncated": bool
}
```

**Risk Platform Capability Reused**:
- `GET /api/risk/cases/{user_id}/evidence` endpoint
- Existing evidence structures

**Could Agent answer without this tool?**
- ⚠️ Partially - could call full evidence API
- ❌ No - requires filtering/aggregation
- ❌ No - requires structured drill-down

**Genuine investigation capability?**
- ✅ Yes - supports drill-down workflow
- ✅ Yes - enables detailed investigation
- ✅ Yes - not just final answer

**Status**: ❌ NOT IMPLEMENTED (needs development)

---

### Tool 3: `policy_lookup` (NEW - NEEDED)

**Purpose**: Search and retrieve policy document content

**What question does it solve?**
- "What policy applies to this specific situation?"
- "What does the AML policy say about X?"

**Why must Agent call it dynamically?**
- User may ask about any policy topic
- Query depends on investigation context
- Finding may need specific policy support

**Structured Input**:
```python
{
    "query": str,           # Semantic search query
    "domain": str,          # Optional: "AML", "KYC", "all"
    "top_k": int,          # Number of results (default 3)
    "filter_metadata": bool # Exclude Status, Purpose sections (default True)
}
```

**Structured Output**:
```python
{
    "query": str,
    "results": List[{
        "doc": str,          # Document name
        "section": str,      # Section path
        "text": str,         # Policy text excerpt
        "chunk_id": str,
        "score": float       # Relevance score
    }],
    "total_found": int,
    "metadata_filtered": int
}
```

**Risk Platform Capability Reused**:
- `PolicyRAGService` (if exposed via API)
- Local markdown policy files
- Policy corpus structure

**Could Agent answer without this tool?**
- ❌ No - requires policy corpus access
- ❌ No - requires domain knowledge
- ❌ No - requires semantic search

**Genuine investigation capability?**
- ✅ Yes - supports policy grounding
- ✅ Yes - enables compliance investigation
- ✅ Yes - not just citation display

**Status**: ❌ NOT IMPLEMENTED (needs Risk Platform API or local access)

---

### Tool 4: `signal_explain` (NEW - NEEDED)

**Purpose**: Explain detection signals (ML, Rule, Graph) with available detail

**What question does it solve?**
- "Why did this rule trigger?"
- "What does this ML score mean?"
- "What generated this graph finding?"

**Why must Agent call it dynamically?**
- User may ask about any signal
- Signal explanation depends on finding context
- Different signal types require different explanations

**Structured Input**:
```python
{
    "case_id": str,
    "signal_type": str,  # "ml", "rule", "graph", "feature"
    "signal_name": str   # Optional: specific signal name
}
```

**Structured Output**:
```python
{
    "signal_type": str,
    "signal_name": str,
    "description": str,      # Human-readable explanation
    "observed_value": Any,   # Actual value for this case
    "threshold": Any,        # Threshold if applicable
    "interpretation": str,   # What this means
    "available_detail": str, # How much detail is available
    "limitations": List[str]  # What cannot be explained
}
```

**Risk Platform Capability Reused**:
- Evidence API signal values
- Rule evidence (for rule signals)
- Feature evidence (for feature signals)
- ML/rule/graph scores from risk_summary

**Could Agent answer without this tool?**
- ⚠️ Partially - could compose from evidence data
- ❌ No - requires domain interpretation
- ❌ No - requires signal semantics

**Genuine investigation capability?**
- ✅ Yes - supports signal drill-down
- ✅ Yes - enables investigation of detection logic
- ✅ Yes - not just displaying values

**Status**: ⚠️ PARTIALLY AVAILABLE (could compose from evidence, needs domain logic)

---

### Tool 5: `citation_validate` (REPLACE - NEEDED)

**Purpose**: Validate citation quality and completeness

**What question does it solve?**
- "Are these citations valid?"
- "Do all findings have proper policy support?"

**Why must Agent call it dynamically?**
- User may question citation quality
- Different investigations have different standards
- Validation depends on specific findings

**Structured Input**:
```python
{
    "case_id": str,
    "findings": List[{
        "finding_text": str,
        "citation_markers": List[int]  # e.g., [1, 2] for "[1] [2]"
    }],
    "citations": List[{
        "id": int,
        "doc": str,
        "section": str,
        "quote": str
    }],
    "validation_level": str  # "basic", "standard", "strict"
}
```

**Structured Output**:
```python
{
    "total_findings": int,
    "cited_findings": int,
    "uncited_findings": int,
    "citation_coverage": float,
    "domain_compliance": {
        "compliant": int,
        "warnings": List[{
            "finding_id": str,
            "citation_id": int,
            "issue": str,
            "severity": str
        }]
    },
    "metadata_issues": List[{
        "citation_id": int,
        "issue": str
    }],
    "validation_summary": str
}
```

**Risk Platform Capability Reused**:
- CitationValidator (if exposed via API)
- Citation validation logic from Risk Platform

**Could Agent answer without this tool?**
- ⚠️ Partially - could do basic count checks
- ❌ No - requires domain validation rules
- ❌ No - requires semantic understanding

**Genuine investigation capability?**
- ✅ Yes - supports quality assurance
- ✅ Yes - enables compliance verification
- ⚠️ Limited - Risk Platform already validates

**Status**: ⚠️ EXISTS (but as policy ID check, not semantic validation)

---

### Tool 6: `artifact_bundle` (NEW - NEEDED)

**Purpose**: Compose structured investigation artifacts

**What question does it solve?**
- "Create an evidence bundle for this finding"
- "Generate a compliance report"

**Why must Agent call it dynamically?**
- User may request different artifact types
- Content depends on investigation context
- Formatting depends on intended use

**Structured Input**:
```python
{
    "case_id": str,
    "artifact_type": str,  # "evidence_bundle", "compliance_report", "summary"
    "focus_findings": List[str],  # Finding IDs to include (optional)
    "include_evidence": bool,
    "include_citations": bool,
    "format": str  # "json", "html", "markdown"
}
```

**Structured Output**:
```python
{
    "artifact_type": str,
    "case_id": str,
    "content": Any,  # Structured artifact content
    "metadata": {
        "generated_at": str,
        "findings_included": List[str],
        "evidence_sources": List[str],
        "citation_count": int
    }
}
```

**Risk Platform Capability Reused**:
- Evidence data (from evidence API)
- Findings and citations (from explain API)
- No new Risk Platform capability needed

**Could Agent answer without this tool?**
- ⚠️ Yes - Agent could compose manually
- ❌ No - requires structured composition
- ❌ No - requires template/formatting

**Genuine investigation capability?**
- ✅ Yes - supports investigation documentation
- ✅ Yes - enables escalation artifacts
- ✅ Yes - not just data display

**Status**: ❌ NOT IMPLEMENTED (Agent-owned capability)

---

## C3. Tool Comparison Matrix

| Tool | Input | Output | RP Capability | Dynamic? | Investigation Value |
|---|---|---|---|---|---|
| risk_case_explain | case_id | Findings + citations | /api/risk/explain | ✅ Yes | ⭐⭐⭐ Core |
| evidence_drilldown | case_id + types | Filtered evidence | /api/risk/evidence | ✅ Yes | ⭐⭐⭐ Drill-down |
| policy_lookup | query + domain | Policy chunks | PolicyRAGService | ✅ Yes | ⭐⭐ Context |
| signal_explain | case_id + signal | Signal detail | Evidence API | ✅ Yes | ⭐⭐ Explainability |
| citation_validate | findings + citations | Validation report | CitationValidator | ⚠️ Optional | ⭐ QA |
| artifact_bundle | case_id + config | Structured artifact | None (compose) | ✅ Yes | ⭐⭐⭐ Documentation |

---

# PHASE D — MULTI-TURN AGENT BEHAVIOR

## D1. U00299 Investigation Flow 1: Coordinated Trading Pattern

```
User: "Investigate case U00299"

Agent Decision: Need authoritative case context
Tool Call: risk_case_explain(case_id="U00299")

Result:
{
    "summary": "High risk coordinated trading pattern detected",
    "key_findings": [
        "1. ML Pattern Detection — 96.24/100",
        "2. Coordinated Trading Pattern [1]",
        "3. High Withdrawal Frequency [1]",
        "4. First Withdrawal to a New Address [2]",
        "5. Abnormal Withdrawal Behavior"
    ],
    "citations": [...],
    "recommended_action": "Manual Review"
}

Agent Response:
"Case U00299 shows CRITICAL risk (85.5/100) with 5 key findings:

1. ML Pattern Detection — 96.24/100 (system signal)
2. Coordinated Trading Pattern (opposite-trade ratio exceeded 40% threshold)
3. High Withdrawal Frequency (14 withdrawals in 24h)
4. First Withdrawal to a New Address
5. Abnormal Withdrawal Behavior (21.43% to new addresses)

Recommended action: Manual Review

Would you like me to drill into any specific finding?"
```

---

```
User: "Why did Coordinated Trading Pattern trigger?"

Agent Decision: Need signal-level explanation
Tool Call: signal_explain(case_id="U00299", signal_type="rule", signal_name="Coordinated Trading Pattern")

Result:
{
    "signal_type": "rule",
    "signal_name": "Coordinated Trading Pattern",
    "description": "Detects coordinated trading patterns through opposite-trade analysis",
    "observed_value": 0.4524,
    "threshold": 0.4,
    "interpretation": "The opposite-trade ratio of 45.24% exceeded the 40% threshold, triggering the rule. This indicates significant trading in opposite directions, which may suggest coordinated behavior or wash trading.",
    "available_detail": "aggregate value only",
    "limitations": ["Individual trade attribution not available"]
}

Agent Response:
"The Coordinated Trading Pattern rule triggered because:

• Observed opposite-trade ratio: 45.24%
• Rule threshold: 40%
• Status: EXCEEDED (triggered)

This indicates significant trading activity in opposite directions within the
assessment window. The pattern may suggest:
- Coordinated trading between accounts
- Wash trading to inflate volume
- Market manipulation

Policy Basis: AML Suspicious Indicators, Section 2.1
"A sudden spike in transfers may indicate coordinated trading..."

Note: Transaction-level detail showing which specific trades contributed to
this ratio is not currently available.

Would you like to see the withdrawal evidence instead?"
```

---

```
User: "Show me the withdrawal evidence"

Agent Decision: Need detailed withdrawal data
Tool Call: evidence_drilldown(case_id="U00299", evidence_types=["withdrawal"])

Result:
{
    "evidence": {
        "withdrawals": [
            {
                "withdrawal_id": "WD001",
                "amount": 0.5,
                "asset": "BTC",
                "address": "0xabc123...",
                "is_new_address": true,
                "timestamp": "2024-01-15T10:30:00Z",
                "risk_reason": "First withdrawal to new address"
            },
            // ... 13 more withdrawals
        ]
    },
    "total_count": 14
}

Agent Response:
"Withdrawal evidence for U00299 (14 withdrawals in 24h):

New Addresses (3 of 14 - 21.43%):
1. WD001: 0.5 BTC to 0xabc123... (FIRST withdrawal to this address)
2. WD002: 0.3 BTC to 0xdef456... (FIRST withdrawal to this address)
3. WD003: 0.7 BTC to 0xghi789... (FIRST withdrawal to this address)

Known Addresses (11 of 14):
- WD004-WD014: Various amounts to previously seen addresses

Total withdrawal volume: 8.2 BTC
Pattern: High frequency (14 in 24h) with new destination churn (21.43%)

This supports the 'Abnormal Withdrawal Behavior' finding.
Would you like me to create an evidence bundle for escalation?"
```

---

## D2. U00299 Investigation Flow 2: Policy Grounding Focus

```
User: "What policy supports the Coordinated Trading Pattern finding?"

Agent Decision: Check if citation already available in context
Context: risk_case_explain already called, citations available

Agent Response (no tool call):
"The Coordinated Trading Pattern finding is supported by:

Document: AML_Suspicious_Indicators.md
Section: 2. Transaction Velocity & Burst Patterns / 2.1 High-Velocity Transfers
Quote: "A sudden spike in the number of outgoing or incoming transfers within
a short time window may indicate account takeover attempts, coordinated trading,
or other suspicious patterns."

Chunk ID: AML_Suspicious_Indicators#2.1#001

This is Citation [1] in the finding text.

Would you like me to search for additional related policies?"
```

---

```
User: "What does the AML policy say about withdrawal patterns?"

Agent Decision: Need broader policy search
Tool Call: policy_lookup(query="withdrawal frequency suspicious patterns", domain="AML", top_k=3)

Result:
{
    "query": "withdrawal frequency suspicious patterns",
    "results": [
        {
            "doc": "AML_Suspicious_Indicators.md",
            "section": "2.1 High-Velocity Transfers",
            "text": "Unusual withdrawal patterns including high frequency
                    and sudden changes in destination addresses may indicate
                    account compromise or fraudulent activity.",
            "chunk_id": "AML#2.1#003",
            "score": 0.94
        },
        {
            "doc": "AML_Suspicious_Indicators.md",
            "section": "2.2 Rapid Fund Movement",
            "text": "Multiple withdrawals in rapid succession to new
                    destinations warrant enhanced scrutiny.",
            "chunk_id": "AML#2.2#001",
            "score": 0.87
        }
    ],
    "total_found": 2
}

Agent Response:
"AML policy guidance on withdrawal patterns:

1. Section 2.1 (High-Velocity Transfers):
   "Unusual withdrawal patterns including high frequency and sudden changes
   in destination addresses may indicate account compromise or fraudulent activity."

2. Section 2.2 (Rapid Fund Movement):
   "Multiple withdrawals in rapid succession to new destinations warrant
   enhanced scrutiny."

Application to U00299:
- 14 withdrawals in 24h (high frequency) ✓
- 21.43% to new addresses (destination churn) ✓
- Supports both "High Withdrawal Frequency" and "Abnormal Withdrawal Behavior" findings

Would you like me to compile a full compliance report?"
```

---

## D3. U00010 Investigation Flow 3: Network Analysis

```
User: "Investigate case U00010"

Agent Decision: Need authoritative case context
Tool Call: risk_case_explain(case_id="U00010")

Result:
{
    "summary": "High risk case with multiple indicators",
    "key_findings": [
        "1. ML Pattern Detection — 99.41/100",
        "2. New account with high activity",
        "3. High withdrawal frequency",
        "4. First withdrawal to new address",
        "5. Abnormal withdrawal behavior",
        "6. High trading frequency",
        "7. Shared device relationships",
        "8. Linked account network",
        "9. Opposite trade ratio"
    ],
    "citations": [...],
    "recommended_action": "Escalate for manual review"
}

Agent Response:
"Case U00010 shows CRITICAL risk with 9 findings:

Network Findings:
7. Shared Device Relationships
8. Linked Account Network (18 connected accounts)

Other key findings:
- ML Pattern Detection: 99.41/100 (very high)
- High Trading Frequency: 54 trades in 24h
- New account with high activity

This case has both behavioral patterns AND network connections.
Would you like to focus on the network analysis?"
```

---

```
User: "What can you tell me about the network connections?"

Agent Decision: Need network evidence detail
Tool Call: evidence_drilldown(case_id="U00010", evidence_types=["network"])

Result:
{
    "evidence": {
        "network": {
            "shared_device_count": 3,
            "linked_account_count": 18,
            "graph_score": 75.0,
            "connection_types": ["device", "ip", "behavioral"]
        }
    }
}

Agent Response:
"Network evidence for U00010:

Connection Metrics:
• Shared device relationships: 3 linked accounts
• Linked account network: 18 connected accounts
• Graph score: 75.0/100
• Connection types detected: Device, IP, Behavioral

Investigation Implications:
1. Device sharing suggests possible same-operator accounts
2. 18 connected accounts indicates a potential network
3. High graph score (75.0) supports coordinated activity

Limitations:
- Individual account identities not exposed (privacy/security)
- Connection graph structure not available
- Connection strengths/timeline not available

Available for further investigation:
- Aggregate connection counts by type
- Overall network risk score
- Behavioral correlation patterns

Would you like me to focus on the device sharing specifically?"
```

---

```
User: "Can you show me the connected accounts?"

Agent Decision: Check availability
Agent Response (no tool call):
"UNAVAILABLE: Individual Account Identities

The Risk Platform reports that 18 accounts are connected in the network, but
does not expose individual account IDs through the investigation API. This is
a privacy/security restriction to prevent unauthorized account discovery.

What IS available:
- Aggregate counts (18 connected accounts)
- Connection types (device, IP, behavioral)
- Network risk score (75.0/100)
- Shared device count (3 accounts)

Alternative approaches:
- Review aggregate network metrics
- Escalate for privileged access if authorized
- Focus on behavioral patterns instead

Would you like to explore the trading behavior evidence instead?"
```

---

## D4. Multi-Turn Flow Summary

| Flow | Tools Called | Decisions Made | User Value |
|---|---|---|---|
| U00299 Coordinated Trading | risk_case_explain → signal_explain → evidence_drilldown | Rule explanation needed → Evidence detail needed | Complete investigation |
| U00299 Policy Focus | risk_case_explain → policy_lookup | Additional policy context needed | Compliance understanding |
| U00010 Network | risk_case_explain → evidence_drilldown | Network detail needed → Limitation acknowledged | Transparent investigation |

---

# PHASE E — AGENTIC BOUNDARIES

## E1. What the LLM Decides Dynamically

**Tool Selection**:
```
LLM evaluates:
- User's current question
- Investigation context so far
- Available tools
- What information is needed next

Decision:
"User asked about rule trigger → I need signal_explain"
"User asked about withdrawal detail → I need evidence_drilldown"
"User asked about policy → I have citations already, no tool needed"
```

**Tool Sequencing**:
```
LLM plans:
- Start with risk_case_explain (always)
- Then based on user follow-up:
  * Drill into finding → signal_explain
  * Request detail → evidence_drilldown
  * Ask about policy → policy_lookup or use existing citations
  * Request artifact → artifact_bundle
```

**Context Gathering**:
```
LLM recognizes:
- "I have findings but no transaction detail"
- "User wants network analysis, I need network evidence"
- "Citation exists but user wants broader policy context"
```

**Investigation Completion**:
```
LLM decides:
- "User's question answered with existing data → final"
- "Need to call tool → tool_call"
- "Cannot answer → bounded response explaining limitation"
```

---

## E2. What Runtime Controls

**Tool Availability**:
```python
TOOL_REGISTRY = {
    "risk_case_explain": risk_case_explain,
    "evidence_drilldown": evidence_drilldown,
    "policy_lookup": policy_lookup,
    "signal_explain": signal_explain,
    "artifact_bundle": artifact_bundle
}
```

**Argument Validation**:
```python
def _normalize_tool_args(tool_name, llm_args, runtime_context, user_intent):
    # Validate tool exists
    if tool_name not in TOOL_REGISTRY:
        raise ValueError(f"Unknown tool: {tool_name}")

    # Inject required arguments
    if tool_name == "risk_case_explain":
        case_id = _extract_case_id(user_intent)
        if not case_id:
            raise ValueError("case_id required")
        return {"case_id": case_id}
```

**Result Caching**:
```python
# Cache expensive results
if tool_name == "risk_case_explain":
    cache_key = f"case_explain:{case_id}"
    if cache_key in cache:
        return cache[cache_key]
```

**Rate Limiting**:
```python
# Limit expensive calls
if tool_name == "risk_case_explain":
    if _rate_limit_exceeded(case_id):
        raise RateLimitError("Too many explanation requests")
```

---

## E3. What Risk Platform Owns

**Domain Semantics**:
- What constitutes a "finding"
- Rule threshold values
- ML score interpretation
- Network connection definitions
- Policy citation validity

**Detection Logic**:
- How ML models work
- How rules trigger
- How graphs are built
- How features are calculated

**Validation**:
- Citation quality assessment
- Evidence sufficiency
- Domain compliance
- Regulatory interpretation

---

## E4. Clear Boundary Table

| Aspect | Agent (LLM) | Runtime | Risk Platform |
|---|---|---|---|
| **Decide what to investigate** | ✅ Yes | ❌ No | ❌ No |
| **Select appropriate tool** | ✅ Yes | ⚠️ Validates | ❌ No |
| **Interpret tool results** | ✅ Yes | ❌ No | ❌ No |
| **Compose responses** | ✅ Yes | ❌ No | ❌ No |
| **Validate tool exists** | ❌ No | ✅ Yes | ❌ No |
| **Inject required args** | ⚠️ Provides semantic | ✅ Injects structural | ❌ No |
| **Define finding semantics** | ❌ No | ❌ No | ✅ Yes |
| **Calculate ML scores** | ❌ No | ❌ No | ✅ Yes |
| **Validate citations** | ❌ No | ❌ No | ✅ Yes |
| **Interpret regulations** | ❌ No | ❌ No | ✅ Yes |
| **Provide policy text** | ❌ No | ❌ No | ✅ Yes |

---

# PHASE F — CAPABILITY GAPS

## F1. Transaction-Level Signal Attribution

**UNAVAILABLE**: Which specific trades contributed to opposite-trade ratio

**User Question**: "Which trades caused the 45.24% opposite-trade ratio?"

**Current State**: Risk Platform provides aggregate ratio only

**What's Missing**:
- Trade-level signal component attribution
- Which trades were "opposite"
- How each trade contributed to the ratio

**Future Risk Platform Capability Needed**:
```
POST /api/risk/cases/{user_id}/signal/attribution
Body: {"signal_type": "opposite_trade_ratio"}
Response: {
    "signal_value": 0.4524,
    "threshold": 0.4,
    "components": [
        {
            "transaction_id": "TX001",
            "contribution": 0.08,
            "direction": "opposite",
            "counterparty": "account_abc"
        },
        ...
    ]
}
```

**Agent Bounded Response**:
```
UNAVAILABLE: Transaction-level signal attribution

The Risk Platform reports the aggregate opposite-trade ratio (45.24%) but does
not expose which specific trades contributed to this calculation. To provide
this capability, Risk Platform would need to expose a signal attribution
endpoint that breaks down aggregate signals into component transactions.
```

---

## F2. ML Feature Attribution

**UNAVAILABLE**: ML feature contribution scores

**User Question**: "Why did ML flag this case so highly?"

**Current State**: Risk Platform provides ML score only, no SHAP/values

**What's Missing**:
- Feature importance ranking
- SHAP values per feature
- Decision path explanation
- Counterfactual analysis

**Future Risk Platform Capability Needed**:
```
POST /api/risk/cases/{user_id}/ml/attribution
Response: {
    "ml_score": 96.24,
    "feature_attribution": [
        {"feature": "opposite_trade_ratio", "contribution": 25.3, "shap_value": 8.2},
        {"feature": "withdrawal_risk_score", "contribution": 18.7, "shap_value": 6.1},
        ...
    ],
    "top_features": ["opposite_trade_ratio", "withdrawal_risk_score", "trade_frequency"]
}
```

**Agent Bounded Response**:
```
UNAVAILABLE: ML Feature Attribution

The Risk Platform provides the ML pattern detection score (96.24/100) but does
not expose individual feature contributions or model explainability data.
The ML model's internal decision process and feature attribution are not
available through the investigation API.

Available: Feature values (not contributions) and aggregate ML score
```

---

## F3. Network Graph Structure

**UNAVAILABLE**: Detailed network relationship graph

**User Question**: "How are these 18 accounts connected?"

**Current State**: Risk Platform provides aggregate counts only

**What's Missing**:
- Edge list (which account connects to which)
- Connection types per edge
- Connection strengths
- Timeline of connections

**Future Risk Platform Capability Needed**:
```
GET /api/risk/cases/{user_id}/network/graph
Response: {
    "nodes": [
        {"account_id": "U00010", "type": "primary"},
        {"account_id": "U00011", "type": "connected"},
        ...
    ],
    "edges": [
        {"from": "U00010", "to": "U00011", "type": "shared_device", "strength": 0.9},
        {"from": "U00010", "to": "U00012", "type": "ip_correlation", "strength": 0.7},
        ...
    ]
}
```

**Agent Bounded Response**:
```
UNAVAILABLE: Network Graph Structure

The Risk Platform reports that 18 accounts are connected but does not expose
the detailed relationship graph structure. Individual connection edges, types,
and strengths are not available through the investigation API. This is a
privacy/security restriction.

Available: Aggregate connection counts and overall network risk score
```

---

## F4. Rule Configuration

**UNAVAILABLE**: Dynamic rule configuration/thresholds

**User Question**: "What is the threshold for this rule?"

**Current State**: Thresholds embedded in finding text, not exposed as config

**What's Missing**:
- Rule configuration API
- Current threshold values
- Rule parameter adjustments

**Future Risk Platform Capability Needed**:
```
GET /api/risk/rules/{rule_name}/configuration
Response: {
    "rule_name": "Coordinated Trading Pattern",
    "parameters": {
        "opposite_trade_threshold": 0.4,
        "assessment_window_hours": 24,
        "min_volume_threshold": 1000
    },
    "version": "1.2",
    "last_updated": "2024-01-01"
}
```

**Agent Bounded Response**:
```
PARTIAL: Threshold values mentioned in findings

The Risk Platform mentions threshold values in finding text (e.g., "40% threshold")
but does not expose a dedicated rule configuration endpoint. Threshold values
are embedded in finding descriptions rather than provided as structured
configuration data.

Available: Threshold values from finding text
```

---

## F5. Citation-Only Endpoint

**UNAVAILABLE**: Direct citation validation without LLM generation

**User Question**: "Validate these citations"

**Current State**: Must call `/api/risk/explain` (includes LLM overhead)

**What's Missing**:
- Citation-only endpoint
- No LLM generation side effect
- Faster validation

**Future Risk Platform Capability Needed**:
```
POST /api/risk/citations/validate
Body: {
    "findings": [
        {"text": "Coordinated Trading Pattern", "type": "rule_signal"}
    ]
}
Response: {
    "citations": [...],
    "finding_to_citations": {"F-1": [1]},
    "validation_warnings": []
}
```

**Agent Bounded Response**:
```
PARTIAL: Citations available via explanation API

The Risk Platform provides validated citations through the `/api/risk/explain`
endpoint, which includes LLM generation. A dedicated citation-only endpoint
would be faster and avoid LLM costs, but is not currently available.

Workaround: Use `/api/risk/explain` and extract citations from response
```

---

## F6. Policy Query Endpoint

**UNAVAILABLE**: Direct policy search

**User Question**: "What does the AML policy say about X?"

**Current State**: PolicyRAGService exists but not exposed via API

**What's Missing**:
- Policy search endpoint
- Query-based policy retrieval
- Domain-filtered policy results

**Future Risk Platform Capability Needed**:
```
POST /api/risk/policies/search
Body: {"query": "withdrawal patterns", "domain": "AML", "top_k": 3}
Response: {
    "results": [
        {"doc": "AML.md", "section": "2.1", "text": "...", "score": 0.94}
    ]
}
```

**Agent Bounded Response**:
```
UNAVAILABLE: Direct Policy Search

The Risk Platform has a PolicyRAGService for policy search but does not expose
it via API. Policy text must be accessed through citations in the explanation
response only.

Available: Policy citations from case-specific findings
```

---

# Capability Gaps Summary

| Gap | User Impact | RP Solution | Complexity | Priority |
|---|---|---|---|---|
| Transaction signal attribution | Cannot see which trades caused signal | Signal attribution endpoint | Medium | P2 |
| ML feature attribution | Cannot explain ML scoring | ML attribution endpoint | High | P2 |
| Network graph structure | Cannot see account relationships | Graph structure endpoint | Medium | P3 |
| Rule configuration | Cannot see rule settings | Config endpoint | Low | P3 |
| Citation-only endpoint | Slow validation, LLM cost | Citation endpoint | Low | P2 |
| Policy query endpoint | Cannot search policies | Policy search endpoint | Low | P1 |

---

# PHASE G — MVP TOOL SET RECOMMENDATIONS

## G1. Tools to Keep (Already Implemented)

### 1. `risk_case_fetch` (RENAME to `risk_case_explain`)

**Rationale**:
- ✅ Core investigation entry point
- ✅ Provides authoritative findings and citations
- ✅ Enables all downstream investigation
- ✅ Already implemented and tested

**Changes**:
- Rename from `risk_case_fetch` to `risk_case_explain` for clarity
- Update documentation to emphasize it's the PRIMARY tool

**Risk Platform Endpoint**: `POST /api/risk/explain`

---

## G2. Tools to Add (High Priority)

### 2. `evidence_drilldown`

**Rationale**:
- ✅ Supports transaction/withdrawal drill-down
- ✅ Required for common investigation questions
- ✅ High user value
- ⚠️ Requires Risk Platform evidence filtering

**Implementation**:
```python
def evidence_drilldown(
    case_id: str,
    evidence_types: List[str] = None,  # None = all
    filters: dict = None
) -> dict:
    """
    Retrieve filtered evidence for drill-down investigation.

    Types: "transaction", "withdrawal", "network", "feature", "rule"
    Filters: {"is_new_address_only": bool, "limit": int, ...}
    """
```

**Risk Platform Endpoint**: `GET /api/risk/cases/{user_id}/evidence` (with filtering)

**Priority**: ⭐⭐⭐ HIGH - Core investigation capability

---

### 3. `signal_explain`

**Rationale**:
- ✅ Supports "why did this trigger?" questions
- ✅ Provides signal interpretation
- ✅ High user value
- ⚠️ Requires domain knowledge

**Implementation**:
```python
def signal_explain(
    case_id: str,
    signal_type: str,  # "ml", "rule", "graph", "feature"
    signal_name: str = None
) -> dict:
    """
    Explain detection signals with available detail.

    Returns interpretation, observed values, thresholds, limitations.
    """
```

**Risk Platform Endpoint**: Compose from existing evidence API + domain logic

**Priority**: ⭐⭐ MEDIUM - Important but can be partially done with existing tools

---

### 4. `policy_lookup`

**Rationale**:
- ✅ Supports policy context questions
- ✅ Enables compliance investigation
- ⚠️ Requires Risk Platform to expose PolicyRAGService

**Implementation**:
```python
def policy_lookup(
    query: str,
    domain: str = "all",
    top_k: int = 3
) -> dict:
    """
    Search policy documents for relevant guidance.

    Returns policy chunks with relevance scores.
    """
```

**Risk Platform Endpoint**: NEW - `POST /api/risk/policies/search` (or local PolicyRAGService)

**Priority**: ⭐⭐ MEDIUM - Valuable but dependent on Risk Platform API

---

### 5. `artifact_bundle`

**Rationale**:
- ✅ Supports investigation documentation
- ✅ Enables escalation artifacts
- ✅ Agent-owned capability (no RP changes needed)

**Implementation**:
```python
def artifact_bundle(
    case_id: str,
    artifact_type: str,  # "evidence_bundle", "compliance_report"
    focus_findings: List[str] = None
) -> dict:
    """
    Compose structured investigation artifact.

    Aggregates findings, evidence, citations into structured format.
    """
```

**Risk Platform Endpoint**: None (Agent composes from existing data)

**Priority**: ⭐⭐⭐ HIGH - Core Agent value-add

---

## G3. Tools to Remove Eventually

### `policy_search` (CURRENT - Mock)

**Rationale**:
- ❌ Returns mock data, not real policies
- ❌ Should be replaced with `policy_lookup`
- ✅ Transitional - keep until real policy lookup

**Replacement**: `policy_lookup` (when Risk Platform API available)

**Timeline**: Phase 2 removal

---

### `evidence_fetch` (CURRENT - Superseded)

**Rationale**:
- ⚠️ Functionality subsumed by `evidence_drilldown`
- ⚠️ Raw evidence less useful than filtered drill-down
- ✅ Keep as implementation detail for `evidence_drilldown`

**Replacement**: `evidence_drilldown` (enhanced version)

**Timeline**: Phase 2 replacement

---

### `compose_structured_result` (CURRENT - Duplicated)

**Rationale**:
- ❌ Duplicates Risk Platform finding logic
- ❌ Reconstructs findings that Risk Platform already provides
- ❌ Should not exist in Agent Client

**Replacement**: Use `risk_case_explain` directly

**Timeline**: Phase 2 removal

---

### `citation_validate` (CURRENT - Policy ID Check)

**Rationale**:
- ❌ Only checks policy ID existence, not semantic validation
- ❌ Duplicates Risk Platform citation validation
- ❌ Risk Platform already validates citations

**Replacement**: Use citations from `risk_case_explain` (already validated)

**Timeline**: Phase 2 removal

---

## G4. Internal Runtime Capabilities (Not Tools)

### Case ID Extraction

**Function**: `_extract_case_id(user_intent)`

**Rationale**:
- Runtime concern, not investigation tool
- Used by multiple tools
- Should remain internal to Agent

**Status**: ✅ Already internal (not in TOOL_REGISTRY)

---

### Argument Normalization

**Function**: `_normalize_tool_args(tool_name, llm_args, context, user_intent)`

**Rationale**:
- Runtime concern, not investigation tool
- Injects structural context into tool calls
- Should remain internal to Agent

**Status**: ✅ Already internal (not in TOOL_REGISTRY)

---

### Result Caching

**Function**: Cache expensive tool results

**Rationale**:
- Performance optimization
- Runtime concern
- Should remain internal

**Status**: ❌ NOT IMPLEMENTED - Should be added as internal capability

---

## G5. Risk Platform Endpoints Summary

| Tool | Risk Platform Endpoint | Status |
|---|---|---|
| risk_case_explain | POST /api/risk/explain | ✅ Available |
| evidence_drilldown | GET /api/risk/cases/{user_id}/evidence | ✅ Available (needs filtering) |
| policy_lookup | NEW: POST /api/risk/policies/search | ❌ Needs exposure |
| signal_explain | Compose from evidence API | ⚠️ Partial |
| artifact_bundle | None (Agent-owned) | ✅ No RP dependency |

---

## G6. Recommended MVP Tool Set

**Phase 1 (Current)**:
```python
TOOL_REGISTRY = {
    "risk_case_fetch": risk_case_fetch,  # ✅ Core
    "policy_search": policy_search,      # ⚠️ Mock, transitional
    "evidence_fetch": evidence_fetch,    # ⚠️ Raw, needs filtering
}
```

**Phase 2 (Target MVP)**:
```python
TOOL_REGISTRY = {
    # Core Investigation
    "risk_case_explain": risk_case_explain,     # Authoritative findings + citations
    "evidence_drilldown": evidence_drilldown,   # Filtered evidence drill-down

    # Policy Context
    "policy_lookup": policy_lookup,             # Policy search (if RP API available)

    # Agent Value-Add
    "artifact_bundle": artifact_bundle,          # Investigation artifacts
}
```

**Phase 3 (Enhanced)**:
```python
TOOL_REGISTRY = {
    # Core Investigation
    "risk_case_explain": risk_case_explain,
    "evidence_drilldown": evidence_drilldown,

    # Policy Context
    "policy_lookup": policy_lookup,

    # Explainability
    "signal_explain": signal_explain,           # Signal interpretation

    # Agent Value-Add
    "artifact_bundle": artifact_bundle,
}
```

---

# FINAL RECOMMENDATIONS

## 1. Implementation Priority

**Immediate (Phase 2)**:
1. Rename `risk_case_fetch` → `risk_case_explain`
2. Implement `evidence_drilldown` (filter existing evidence API)
3. Implement `artifact_bundle` (Agent-owned, no RP dependency)
4. Remove `compose_structured_result` and `citation_validate`

**Short-term (Phase 2.5)**:
5. Advocate for Risk Platform to expose PolicyRAGService
6. Implement `policy_lookup` when API available

**Long-term (Phase 3)**:
7. Implement `signal_explain` (requires domain logic)
8. Advocate for Risk Platform capability gaps (signal attribution, ML explainability)

---

## 2. Multi-Turn Investigation Support

**Agent should be able to**:
1. Start with `risk_case_explain` (always)
2. Drill down with `evidence_drilldown` (as needed)
3. Explain signals with `signal_explain` (as needed)
4. Search policies with `policy_lookup` (as needed)
5. Generate artifacts with `artifact_bundle` (when requested)

**Key Principle**: Each tool call should be justified by user's investigation need

---

## 3. Capability Gaps to Communicate

**Explicit Bounded Responses for**:
- Transaction-level signal attribution
- ML feature attribution
- Network graph structure
- Rule configuration changes
- Real-time signal monitoring

**Communication Strategy**:
- Acknowledge what IS available
- Clearly state what is NOT available
- Provide alternative approaches when possible
- Avoid fabricating capabilities

---

## 4. Risk Platform Collaboration Opportunities

**Low-Hanging Fruit** (Easy API additions):
1. Expose PolicyRAGService as policy search endpoint
2. Add filtering parameters to evidence endpoint
3. Create citation-only endpoint (faster, no LLM)

**Medium-Term** (Requires more work):
4. Signal attribution endpoint
5. Rule configuration endpoint
6. Network graph endpoint (with privacy controls)

**Long-Term** (Complex):
7. ML feature attribution endpoint
8. Real-time signal monitoring
9. Investigation workflow state API

---

## 5. Success Criteria

**Phase 2 Success**:
- ✅ Agent uses Risk Platform as authoritative source
- ✅ No finding reconstruction in Agent Client
- ✅ No citation validation duplication
- ✅ Clear tool surface for investigation workflow
- ✅ Multi-turn investigation flows supported

**User Experience Success**:
- ✅ Investigators can drill down into findings
- ✅ Clear communication of limitations
- ✅ Evidence-grounded responses
- ✅ Artifact generation for escalation
- ✅ Policy context available

**Technical Success**:
- ✅ Clean domain boundaries (Agent vs Risk Platform)
- ✅ No duplicated domain logic
- ✅ Clear data lineage
- ✅ Maintainable architecture

---

## 6. Next Implementation Step

**Recommended First Step**:

Implement `evidence_drilldown` tool with:

1. Evidence type filtering (transaction, withdrawal, network)
2. Common filters (new address only, limit, risk reason)
3. Clear structured output
4. Integration with existing `risk_case_explain` workflow

**Rationale**:
- High user value (drill-down is top investigation need)
- Uses existing Risk Platform endpoint
- No Risk Platform changes required
- Enables multiple investigation flows
- Clear Agent responsibility (filtering/presentation)

**Estimated Effort**: 2-3 days
- 1 day: Tool implementation
- 1 day: Agent integration and testing
- 0.5 day: Documentation
- 0.5 day: Multi-turn flow testing

---

**END OF AUDIT**

This design audit provides a complete framework for building a genuinely agentic investigation experience that:
- Respects domain boundaries (Agent vs Risk Platform)
- Supports realistic multi-turn investigations
- Provides clear bounded responses for limitations
- Prioritizes tools that create genuine investigation value
- Identifies capability gaps transparently
- Recommends implementation priorities

**No code changes recommended in this audit phase.**

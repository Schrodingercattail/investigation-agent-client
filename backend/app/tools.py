import logging
from typing import Any, Callable

from app.exceptions import (
    RiskPlatformAuthenticationError,
    RiskPlatformError,
    RiskPlatformUnavailableError,
    ToolArgumentError,
    ToolNotFoundError,
)
from app.models import ProvenanceType
from app.risk_platform_client import risk_platform_client


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def policy_search(
    query: str,
    top_k: int = 3,
) -> dict[str, Any]:
    """
    Search relevant policy snippets.

    Week 1 implementation:
    deterministic local mock data.

    Later:
    replace the retrieval implementation with the existing
    Risk Platform RAG / CitationRetrievalService.
    """

    policies = [
        {
            "policy_id": "AML-001",
            "title": "AML Suspicious Indicators",
            "snippet": (
                "Suspicious activity may include unusual transaction "
                "patterns, rapid movement of funds, or activity inconsistent "
                "with the customer's expected profile."
            ),
            "score": 0.94,
        },
        {
            "policy_id": "KYC-001",
            "title": "KYC / CDD Requirements",
            "snippet": (
                "Customer due diligence should consider customer identity, "
                "risk profile, and whether observed activity is consistent "
                "with expected behavior."
            ),
            "score": 0.88,
        },
        {
            "policy_id": "RISK-001",
            "title": "Risk Scoring Explainability Guide",
            "snippet": (
                "Risk assessments should identify the primary contributing "
                "signals and distinguish model-derived signals from "
                "rule-based or network-based indicators."
            ),
            "score": 0.82,
        },
    ]

    return {
        "query_used": query,
        "top_k": top_k,
        "results": policies[:top_k],
    }


def evidence_fetch(case_id: str) -> dict[str, Any]:
    """
    Fetch canonical evidence / unified findings from Risk Platform.

    This connects to the real Risk Platform evidence API.

    Args:
        case_id: Investigation case ID (e.g., "U00299")

    Returns:
        Normalized evidence dict with unified findings and risk summary

    Raises:
        ToolArgumentError: If case_id is invalid
        RiskPlatformAuthenticationError: On authentication failures
        RiskPlatformUnavailableError: If Risk Platform is unavailable
        RiskPlatformError: On other API errors
    """
    import asyncio

    if not case_id:
        raise ToolArgumentError(
            "case_id is required for evidence_fetch",
            tool_name="evidence_fetch",
            args={"case_id": case_id},
        )

    # The Risk Platform evidence API uses user_id, not case_id
    # For our investigation cases, the case_id directly maps to user_id
    # e.g., "U00299" case -> "U00299" user in the Risk Platform
    user_id = case_id

    logger.info(f"Fetching evidence for case_id: {case_id} (user_id: {user_id})")

    try:
        # Try to get the current running loop
        try:
            loop = asyncio.get_running_loop()
            # Create a task and await it properly
            import concurrent.futures
            # Run in a separate thread to avoid blocking the current loop
            with concurrent.futures.ThreadPoolExecutor() as pool:
                evidence = pool.submit(
                    asyncio.run,
                    risk_platform_client.get_case_evidence(user_id)
                ).result()
        except RuntimeError:
            # No running loop, create a new one
            evidence = asyncio.run(risk_platform_client.get_case_evidence(user_id))

        logger.info(f"Successfully fetched evidence for case {case_id}")
        # case_id is already added by _normalize_evidence_response
        return evidence
    except (RiskPlatformAuthenticationError, RiskPlatformUnavailableError, RiskPlatformError):
        # Re-raise Risk Platform errors as-is
        logger.error(f"Risk Platform error fetching evidence for case {case_id}")
        raise
    except Exception as e:
        # Wrap unexpected errors
        error_msg = f"Failed to fetch evidence for case {case_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise RiskPlatformError(error_msg) from e


def compose_structured_result(
    evidence: dict[str, Any],
    policies: dict[str, Any],
    user_intent: str,
) -> dict[str, Any]:
    """
    Produce a deterministic structured result using real Risk Platform evidence.

    This extracts the actual risk scores and findings from the real evidence
    rather than using hardcoded values.

    DATA MODEL:
    - Risk Summary: Contains case info, risk level, scores, detection signals
    - Source Findings: Substantive behavioral/relational findings only
    - Actions: Agent may elaborate on authoritative recommended_action

    DETECTION SIGNALS vs SOURCE FINDINGS:
    - Detection Signals: Aggregate scores (ML, Rule, Graph) - moved to risk_summary
    - Source Findings: Specific observed behaviors, patterns, relationships

    TAXONOMY RULES:
    A finding is an AGGREGATE DETECTION SIGNAL if it represents:
      - A score (e.g., "ML-derived risk signal (score: 99.41)")
      - A detection-system aggregate (e.g., "Rule-based risk indicators (score: 85.00)")
      - The primary_reason (moved to risk_summary.primary_reason)

    A finding remains a SOURCE FINDING if it represents:
      - A specific observed behavior (e.g., "54 trades in 24h period")
      - A specific relationship (e.g., "18 connected accounts detected")
      - A specific rule-derived pattern (e.g., "First withdrawal to new address")
      - A specific graph/network relationship (e.g., "Network relationship detected...")

    IMPORTANT: Returns insufficient_evidence ONLY when the Risk Platform
    returns a valid successful response with genuinely empty data.
    Integration errors (404, 500, etc.) should be raised as exceptions.
    """

    # Extract real risk data from the evidence
    evidence_risk_summary = evidence.get("risk_summary", {})
    unified_findings = evidence.get("unified_findings", [])
    evidence_items = evidence.get("evidence", [])

    # Get the actual risk_level from Risk Platform response
    risk_level = evidence_risk_summary.get("risk_level", "unknown")
    risk_score = evidence_risk_summary.get("risk_score", 0)

    # CRITICAL: Check if this is a genuine "case not found" vs "low risk case"
    # Risk Platform returns risk_level="UNKNOWN" and risk_score=0 for non-existent cases
    # But low-risk cases may also have low scores - we must distinguish
    is_genuinely_empty_case = (
        risk_level == "UNKNOWN" and
        risk_score == 0 and
        evidence_risk_summary.get("primary_reason") is None and
        evidence_risk_summary.get("detected_at") is None and
        len(unified_findings) == 0 and
        len(evidence_items) == 0
    )

    # Log the distinction for debugging
    logger.info(
        f"compose_structured_result: case_id={evidence.get('case_id')}, "
        f"risk_level={risk_level}, risk_score={risk_score}, "
        f"findings_count={len(unified_findings)}, evidence_count={len(evidence_items)}, "
        f"is_genuinely_empty={is_genuinely_empty_case}"
    )

    if is_genuinely_empty_case:
        # This is a genuinely empty case - return insufficient_evidence
        # This should only happen for non-existent cases, not for CRITICAL cases
        case_id = evidence.get("case_id", "unknown")
        return {
            "outcome": "insufficient_evidence",
            "risk_summary": {
                "case_id": case_id,
                "risk_score": 0,
                "risk_level": "unknown",
                "primary_reason": "Insufficient evidence available for this case",
                "detection_signals": {},
            },
            "findings": [
                {
                    "finding_id": "F-001",
                    "claim": f"Insufficient evidence available for case {case_id}. No risk indicators or findings could be generated.",
                    "evidence_ids": [],
                    "policy_ids": [],
                }
            ],
            "actions": [
                "Verify the case ID is correct and exists in the Risk Platform.",
                "Check if evidence data is available for this case.",
                "Contact support if the issue persists.",
            ],
            "user_intent": user_intent,
        }

    # Build detection_signals from Risk Platform scores
    detection_signals = {}
    ml_score = evidence_risk_summary.get("ml_score")
    rule_score = evidence_risk_summary.get("rule_score")
    graph_score = evidence_risk_summary.get("graph_score")

    if ml_score is not None:
        detection_signals["ml_score"] = ml_score
    if rule_score is not None:
        detection_signals["rule_score"] = rule_score
    if graph_score is not None:
        detection_signals["graph_score"] = graph_score

    # Convert unified findings to the expected format with policy mapping and provenance
    # BUT filter out aggregate detection signals - those belong in risk_summary
    findings = []
    for i, finding in enumerate(unified_findings, 1):
        finding_type = finding.get("type", "")
        description = finding.get("description", finding.get("claim", ""))
        original_finding_id = finding.get("finding_id", f"F-{i:03d}")
        source_evidence_id = finding.get("source_evidence_id")

        # CLASSIFICATION: Determine if this is an aggregate signal or substantive finding

        # Skip aggregate detection signals - these are now in risk_summary.detection_signals
        # BUT preserve substantive detector findings like "ML Pattern Detection"
        if finding_type == "ml_signal":
            # Check if this is the substantive "ML Pattern Detection Signal" finding
            # vs just an aggregate score finding
            # The Risk Platform creates "ML Pattern Detection Signal" when ml_score >= 50
            # This is a substantive finding, not just an aggregate score
            if "ml pattern detection" in description.lower():
                # This is the substantive ML Pattern Detection finding - preserve it
                pass  # Continue to add this as a finding
            elif "score:" in description.lower():
                # This is an aggregate ML score finding - skip, info is in detection_signals
                continue
            # Other ml_signal findings without "score:" are preserved
        if finding_type == "rule_signal" and "score:" in description.lower():
            # This is an aggregate Rule score finding - skip, info is in detection_signals
            continue
        if finding_type == "primary_reason":
            # Check if the primary reason is "ML Pattern Detection"
            # If so, this represents a substantive ML finding, not just metadata
            # But we already have the ml_signal finding, so skip to avoid duplicate
            # The primary reason is already in risk_summary.primary_reason
            continue

        # Map finding types to provenance types
        provenance_type = ProvenanceType.UNKNOWN
        if finding_type == "risk_factor":
            provenance_type = ProvenanceType.DIRECT_EVIDENCE
        elif finding_type == "rule":
            provenance_type = ProvenanceType.RULE_DERIVED
        elif finding_type == "rule_signal":
            provenance_type = ProvenanceType.RULE_DERIVED
        elif finding_type == "ml_signal":
            provenance_type = ProvenanceType.ML_DERIVED
        elif finding_type == "graph_signal":
            provenance_type = ProvenanceType.GRAPH_DERIVED
        elif finding_type == "primary_reason":
            provenance_type = ProvenanceType.ML_DERIVED  # Primary reason is typically ML-driven for CRITICAL cases

        # Map finding types to relevant policy IDs
        policy_ids = []
        if "ml_signal" in finding_type or "ML" in description:
            policy_ids.append("RISK-001")  # Risk Scoring Explainability Guide
        if "rule_signal" in finding_type or "rule" in finding_type or "rule" in description.lower():
            policy_ids.append("AML-001")    # AML Suspicious Indicators
        if policy_ids and "primary_reason" not in finding_type:
            policy_ids.append("KYC-001")    # KYC/CDD Requirements (general context)

        # Preserve evidence provenance - use the actual evidence ID if available
        evidence_ids = []
        if source_evidence_id:
            evidence_ids.append(source_evidence_id)

        findings.append({
            "finding_id": original_finding_id,  # Preserve original finding ID
            "claim": description,
            "canonical_name": finding.get("canonical_name"),  # Preserve canonical finding name from Risk Platform
            "provenance": {
                "source": "risk_platform",
                "type": provenance_type.value,
                "source_finding_id": original_finding_id,
                "evidence_ids": evidence_ids,
                "policy_ids": policy_ids,
            },
        })

    # Deduplicate findings by claim and canonical_name to prevent duplicates
    # from different detection sources or multiple passes
    seen_findings = set()
    deduplicated_findings = []
    for finding in findings:
        # Create a deduplication key from claim and canonical_name
        claim = finding.get("claim", "")
        canonical_name = finding.get("canonical_name", "")
        dedupe_key = (claim.lower(), canonical_name.lower())

        if dedupe_key not in seen_findings:
            seen_findings.add(dedupe_key)
            deduplicated_findings.append(finding)

    findings = deduplicated_findings

    # For low-risk cases with no findings, create a single informational finding
    if not findings:
        findings.append({
            "finding_id": "F-001",
            "claim": "No significant risk indicators detected for this case.",
            "provenance": {
                "source": "agent_client",
                "type": ProvenanceType.UNKNOWN.value,
                "source_finding_id": "F-001",
                "evidence_ids": [],
                "policy_ids": [],
            },
        })

    # Generate actions based on risk level - CRITICAL requires escalation
    risk_level = evidence_risk_summary.get("risk_level", "unknown")
    risk_level_lower = risk_level.lower()
    actions = []

    if risk_level_lower == "critical":
        # CRITICAL risk requires immediate investigation action
        actions.append("Immediate investigation required due to CRITICAL risk level.")
        actions.append("Review all transaction and network activity for suspicious patterns.")
        actions.append("Escalate for manual review and potential account action.")
        # Preserve Risk Platform's recommended action if available
        recommended_action = evidence_risk_summary.get("recommended_action")
        if recommended_action and recommended_action not in actions:
            actions.append(recommended_action)
    elif risk_level_lower == "high":
        actions.append("Review the account's recent transaction activity.")
        actions.append("Validate whether observed behavior is consistent with the customer profile.")
        actions.append("Perform additional investigation if suspicious indicators remain unresolved.")
        # Preserve Risk Platform's recommended action if available
        recommended_action = evidence_risk_summary.get("recommended_action")
        if recommended_action and recommended_action not in actions:
            actions.append(recommended_action)
    elif risk_level_lower == "medium":
        actions.append("Monitor account activity for continued risk indicators.")
        actions.append("Review recent transactions for patterns.")
        # Preserve Risk Platform's recommended action if available
        recommended_action = evidence_risk_summary.get("recommended_action")
        if recommended_action and recommended_action not in actions:
            actions.append(recommended_action)
    else:
        # Unknown or low risk
        actions.append("Continue routine monitoring.")
        # Check if there's a specific recommended action
        recommended_action = evidence_risk_summary.get("recommended_action")
        if recommended_action and recommended_action != actions[0]:
            actions.append(recommended_action)

    return {
        "risk_summary": {
            "case_id": evidence.get("case_id", ""),
            "risk_score": evidence_risk_summary.get("risk_score", 0),
            "risk_level": evidence_risk_summary.get("risk_level", "unknown"),
            "primary_reason": evidence_risk_summary.get("primary_reason"),
            "recommended_action": evidence_risk_summary.get("recommended_action"),
            "detection_signals": detection_signals,
        },
        "findings": findings,
        "actions": actions,
        "user_intent": user_intent,
    }


def citation_validate(
    claims: list[dict[str, Any]],
    policies: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate claim-level policy citations with provenance awareness.

    Distinguishes between:
    - source_grounded: Claims from Risk Platform with valid provenance
    - policy_supported: Claims with retrieved policy citations
    - unsupported: Claims without valid source or policy support
    """
    from app.models import ProvenanceType

    policy_ids = {
        item["policy_id"]
        for item in policies.get("results", [])
    }

    supported = []
    unsupported = []
    source_grounded = []

    for claim in claims:
        # Handle new provenance structure
        provenance = claim.get("provenance", {})
        if isinstance(provenance, dict):
            claim_policy_ids = set(provenance.get("policy_ids", []))
            provenance_type = provenance.get("type", ProvenanceType.UNKNOWN.value)
            source_system = provenance.get("source", "")
        else:
            # Legacy flat structure for backwards compatibility
            claim_policy_ids = set(claim.get("policy_ids", []))
            provenance_type = ProvenanceType.UNKNOWN.value
            source_system = ""

        # Check if source-grounded (from Risk Platform)
        is_source_grounded = (
            source_system == "risk_platform" and
            provenance_type != ProvenanceType.UNKNOWN.value
        )

        if is_source_grounded:
            source_grounded.append(claim)

        # Check policy support
        if claim_policy_ids and claim_policy_ids.issubset(policy_ids):
            supported.append(claim)
        elif not is_source_grounded:
            # Only mark as unsupported if not source-grounded
            unsupported.append(
                {
                    "claim": claim,
                    "reason": "Referenced policy was not retrieved.",
                }
            )

    total = len(claims)
    supported_count = len(supported)
    source_grounded_count = len(source_grounded)

    accuracy = (
        supported_count / total
        if total > 0
        else 0.0
    )

    return {
        "supported_count": supported_count,
        "unsupported_count": len(unsupported),
        "source_grounded_count": source_grounded_count,
        "citation_accuracy": accuracy,
        "unsupported_claims": unsupported,
        "fallback_triggered": len(unsupported) > 0,
    }


def risk_case_fetch(
    case_id: str,
) -> dict[str, Any]:
    """
    Retrieve authoritative Risk Platform investigation context for a case.

    This tool calls the Risk Platform explanation API which provides:
    - Authoritative findings (with canonical names from Risk Platform)
    - Validated citations (with semantic support, not just policy ID checking)
    - Finding-to-citation mappings (embedded as [n] markers in findings)
    - Risk summary (scores, detection methods, recommended actions)
    - Explanation metadata (source, missing info)

    The returned findings and citations are authoritative from Risk Platform.
    The Agent should NOT attempt to recreate findings, validate citations,
    or apply Risk Platform threshold logic.

    Args:
        case_id: Investigation case ID (e.g., "U00299", "U00010")

    Returns:
        Authoritative explanation dict from Risk Platform with:
        - summary: Overall case summary
        - key_findings: List of finding texts (with [n] citation markers)
        - recommended_action: Recommended action from Risk Platform
        - citations: List of validated citation objects with quote/section/doc
        - explanation_source: "LLM" or "MODEL_FALLBACK"
        - llm_error: Error message if LLM generation failed
        - missing_info: List of missing information fields

    Raises:
        ToolArgumentError: If case_id is invalid
        RiskPlatformAuthenticationError: On authentication failures
        RiskPlatformUnavailableError: If Risk Platform is unavailable
        RiskPlatformError: On other API errors

    Note:
        This tool calls Risk Platform's /api/risk/explain endpoint which may
        invoke LLM generation. Results should be cached appropriately when used
        in high-frequency scenarios.
    """
    import asyncio

    if not case_id:
        raise ToolArgumentError(
            "case_id is required for risk_case_fetch",
            tool_name="risk_case_fetch",
            args={"case_id": case_id},
        )

    logger.info(f"Fetching authoritative investigation context for case: {case_id}")

    try:
        # Try to get the current running loop
        try:
            loop = asyncio.get_running_loop()
            # Create a task and await it properly
            import concurrent.futures
            # Run in a separate thread to avoid blocking the current loop
            with concurrent.futures.ThreadPoolExecutor() as pool:
                explanation = pool.submit(
                    asyncio.run,
                    risk_platform_client.get_case_explanation(case_id)
                ).result()
        except RuntimeError:
            # No running loop, create a new one
            explanation = asyncio.run(risk_platform_client.get_case_explanation(case_id))

        logger.info(
            f"Successfully fetched authoritative context for case {case_id}: "
            f"{len(explanation.get('key_findings', []))} findings, "
            f"{len(explanation.get('citations', []))} citations, "
            f"source={explanation.get('explanation_source', 'unknown')}"
        )

        # Return authoritative result as-is (no reconstruction, no validation)
        # The Risk Platform is the source of truth for findings and citations
        return explanation

    except (RiskPlatformAuthenticationError, RiskPlatformUnavailableError, RiskPlatformError):
        # Re-raise Risk Platform errors as-is
        logger.error(f"Risk Platform error fetching case context for {case_id}")
        raise
    except Exception as e:
        # Wrap unexpected errors
        error_msg = f"Failed to fetch case context for {case_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise RiskPlatformError(error_msg) from e


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

ToolFunction = Callable[..., dict[str, Any]]


TOOL_REGISTRY: dict[str, ToolFunction] = {
    # Core Risk Platform integration (authoritative)
    "risk_case_fetch": risk_case_fetch,  # Authoritative findings + citations from Risk Platform

    # Transitional tools (to be removed in Phase 2)
    "policy_search": policy_search,  # [LEGACY] Mock policy search
    "evidence_fetch": evidence_fetch,  # [LEGACY] Raw evidence (superseded by risk_case_fetch)
    "compose_structured_result": compose_structured_result,  # [LEGACY] Finding reconstruction (superseded by risk_case_fetch)
    "citation_validate": citation_validate,  # [LEGACY] Policy ID checking (superseded by risk_case_fetch)
}


def execute_tool(
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a registered tool by name with robust error handling.

    Args:
        tool_name: Name of the tool to execute
        args: Arguments to pass to the tool

    Returns:
        Tool execution result

    Raises:
        ToolNotFoundError: If tool_name is not in registry
        ToolArgumentError: If tool arguments are invalid
        RiskPlatformError: On Risk Platform API errors
        Exception: For other tool-specific errors
    """
    logger.debug(f"Executing tool: {tool_name} with args: {list(args.keys())}")

    tool = TOOL_REGISTRY.get(tool_name)

    if tool is None:
        raise ToolNotFoundError(
            f"Unknown tool: {tool_name}. Available tools: {list(TOOL_REGISTRY.keys())}",
            tool_name=tool_name,
            args=args,
        )

    try:
        result = tool(**args)
        logger.debug(f"Tool {tool_name} completed successfully")
        return result
    except TypeError as e:
        # Likely invalid arguments for the tool
        raise ToolArgumentError(
            f"Invalid arguments for tool '{tool_name}': {str(e)}",
            tool_name=tool_name,
            args=args,
        ) from e
    except (RiskPlatformError, RiskPlatformAuthenticationError, RiskPlatformUnavailableError):
        # Re-raise Risk Platform errors as-is
        raise
    except Exception as e:
        # Log and re-raise unexpected errors
        logger.error(f"Tool {tool_name} failed: {str(e)}", exc_info=True)
        raise
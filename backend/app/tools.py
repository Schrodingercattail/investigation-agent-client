from typing import Any, Callable


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
    Fetch canonical evidence / unified findings.

    Week 1 implementation:
    deterministic local sample evidence.

    Later:
    connect this to the existing Risk Platform API.
    """

    return {
        "case_id": case_id,
        "evidence": [
            {
                "evidence_id": "EV-001",
                "type": "risk_score",
                "description": "Combined account risk score",
                "value": 72.12,
            },
            {
                "evidence_id": "EV-002",
                "type": "ml_signal",
                "description": "ML pattern detection score",
                "value": 96.24,
            },
            {
                "evidence_id": "EV-003",
                "type": "rule_signal",
                "description": "Rule engine risk score",
                "value": 80.0,
            },
        ],
        "unified_findings": [
            {
                "finding_id": "F-001",
                "type": "ml_pattern",
                "description": "Elevated ML-derived risk signal.",
            },
            {
                "finding_id": "F-002",
                "type": "rule_signal",
                "description": "Rule-based risk indicators were triggered.",
            },
        ],
    }


def compose_structured_result(
    evidence: dict[str, Any],
    policies: dict[str, Any],
    user_intent: str,
) -> dict[str, Any]:
    """
    Produce a deterministic structured result.

    This is intentionally NOT LLM-based yet.
    The next stage will replace the composition logic with
    a Zhipu-powered structured generation step.
    """

    return {
        "risk_summary": {
            "case_id": evidence["case_id"],
            "risk_score": 72.12,
            "risk_level": "high",
        },
        "findings": [
            {
                "finding_id": "F-001",
                "claim": "The case has an elevated ML-derived risk signal.",
                "evidence_ids": ["EV-002"],
                "policy_ids": ["RISK-001"],
            },
            {
                "finding_id": "F-002",
                "claim": "Rule-based risk indicators were triggered.",
                "evidence_ids": ["EV-003"],
                "policy_ids": ["AML-001"],
            },
        ],
        "actions": [
            "Review the account's recent transaction activity.",
            "Validate whether observed behavior is consistent with the customer profile.",
            "Perform additional investigation if suspicious indicators remain unresolved.",
        ],
        "user_intent": user_intent,
    }


def citation_validate(
    claims: list[dict[str, Any]],
    policies: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate claim-level policy citations.

    Week 1:
    deterministic validation.

    Later:
    reuse the existing citation validation logic.
    """

    policy_ids = {
        item["policy_id"]
        for item in policies.get("results", [])
    }

    supported = []
    unsupported = []

    for claim in claims:
        claim_policy_ids = set(claim.get("policy_ids", []))

        if claim_policy_ids and claim_policy_ids.issubset(policy_ids):
            supported.append(claim)
        else:
            unsupported.append(
                {
                    "claim": claim,
                    "reason": "Referenced policy was not retrieved.",
                }
            )

    total = len(claims)
    supported_count = len(supported)

    accuracy = (
        supported_count / total
        if total > 0
        else 0.0
    )

    return {
        "supported_count": supported_count,
        "unsupported_count": len(unsupported),
        "citation_accuracy": accuracy,
        "unsupported_claims": unsupported,
        "fallback_triggered": len(unsupported) > 0,
    }


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

ToolFunction = Callable[..., dict[str, Any]]


TOOL_REGISTRY: dict[str, ToolFunction] = {
    "policy_search": policy_search,
    "evidence_fetch": evidence_fetch,
    "compose_structured_result": compose_structured_result,
    "citation_validate": citation_validate,
}


def execute_tool(
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a registered tool by name.
    """

    tool = TOOL_REGISTRY.get(tool_name)

    if tool is None:
        raise ValueError(
            f"Unknown tool: {tool_name}"
        )

    return tool(**args)
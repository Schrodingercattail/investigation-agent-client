"""related_account_analysis — case-level related-account domain tool.

Answers "which accounts are related to this account" (「哪些账号是这个
账号的关联账号」) from the Risk Platform's OWN network attribution
(`network_evidence`), the same authoritative source the graph-signal
explanation reads.

Boundary (AL-2.10):

- Pure authoritative echo: cluster facts, related accounts, and shared
  devices are reported VERBATIM from RP — no derived relationships, no
  scoring, no intent claims, nothing synthesized.
- Requires only the case_id (executor-injected; never planner/LLM-
  supplied in the live path) — a CASE-LEVEL capability, deliberately
  distinct from finding-level investigation.
- No network evidence for the case → success + evidence_missing
  (never an error, never fabricated accounts).
- Lists are bounded; the payload echoes the counts RP reports.
"""

import logging
from typing import Any

from app.models import (
    EvidenceRef,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)

logger = logging.getLogger(__name__)

# The one supported analysis (locked by the step binding).
ANALYSIS_TYPE = "related_account_analysis"

_MAX_RELATED_ACCOUNTS = 20
_MAX_SHARED_DEVICES = 10


def _validation_error(message: str) -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.VALIDATION_ERROR,
        error=ToolError(code="VALIDATION_ERROR", message=message),
    )


def _require_evidence_shape(evidence: dict[str, Any]) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("Risk Platform evidence payload must be an object")


def normalize_related_accounts(
    *, case_id: str, evidence: dict[str, Any],
) -> dict[str, Any]:
    """Related-account payload from network_evidence only (authoritative
    echo; bounded lists; nothing derived)."""
    _require_evidence_shape(evidence)
    network = evidence.get("network_evidence") or None
    payload: dict[str, Any] = {
        "analysis": ANALYSIS_TYPE,
        "case_id": case_id,
        "cluster": None,
        "related_accounts": [],
        "related_accounts_count": 0,
        "shared_devices": [],
        "evidence_refs": [],
        "evidence_missing": True,
        "next_data_needed": [],
    }
    if not network:
        payload["analysis_status"] = "NO_NETWORK_EVIDENCE"
        payload["next_data_needed"] = [
            "network/cluster evidence for this case",
        ]
        return payload

    payload["analysis_status"] = "RELATED_ACCOUNTS_FOUND"
    payload["cluster"] = {
        "cluster_id": network.get("cluster_id"),
        "cluster_name": network.get("cluster_name"),
        "detection_type": network.get("detection_type"),
        "member_count": network.get("member_count"),
        "cluster_risk_score": network.get("cluster_risk_score"),
        "role_in_cluster": network.get("role_in_cluster"),
    }
    related = list(network.get("related_accounts") or [])
    payload["related_accounts"] = related[:_MAX_RELATED_ACCOUNTS]
    payload["related_accounts_count"] = network.get(
        "related_accounts_count", len(related))
    payload["shared_devices"] = list(
        network.get("shared_devices") or [])[:_MAX_SHARED_DEVICES]
    payload["evidence_refs"] = [{"kind": "risk_event", "id": case_id}]
    payload["evidence_missing"] = not related
    if not related:
        payload["next_data_needed"] = [
            "related-account attribution for this case",
        ]
    return payload


def related_account_analysis(
    case_id: str,
    analysis_type: str = ANALYSIS_TYPE,
    adapter: Any | None = None,
) -> ToolResult:
    """Report the Risk Platform's related-account attribution for the
    case's subject account.

    Args:
        case_id: the investigated case (executor-injected).
        analysis_type: locked by the step binding; any other value is a
            validation error.
        adapter: injection seam for tests; defaults to the real adapter.

    Returns ToolResult:
        related accounts found (or none attributed) → outcome="success";
        no network evidence → outcome="success" + evidence_missing;
        RP unavailable → outcome="integration_error" (never absence).
    """
    if analysis_type != ANALYSIS_TYPE:
        return _validation_error(
            f"analysis_type {analysis_type!r} is not supported; the only "
            f"registered analysis is {ANALYSIS_TYPE!r}.")
    if not isinstance(case_id, str) or not case_id.strip():
        return _validation_error(
            "case_id is required for related_account_analysis "
            "(non-empty string).")
    case_id = case_id.strip()

    try:
        if adapter is None:
            from app.adapters.risk_platform import RiskPlatformAdapter
            adapter = RiskPlatformAdapter()
        # AL-2.14: the related-account list comes from the network-signals
        # detail endpoint (no SQL LIMIT, complete membership) — NOT from
        # the evidence payload's network_evidence (which is a bounded
        # preview: SQL LIMIT 10 + Python [:5]). The evidence payload is
        # still fetched for cluster context (name/type/score/role) and
        # shared devices, which the detail endpoint does not carry.
        from app.config import settings
        ns = adapter.fetch_network_signals(
            case_id, limit=settings.TRADE_ANALYSIS_MAX_ACCOUNTS)
        evidence = adapter.fetch_case_evidence(case_id)
    except Exception as e:  # RiskPlatformError family, bounded
        kind = getattr(e, "kind", "error")
        logger.error("related_account_analysis integration failure: %s",
                     getattr(e, "message", e))
        code = {
            "unavailable": "RISK_PLATFORM_UNAVAILABLE",
            "auth": "RISK_PLATFORM_AUTH_FAILED",
            "http": "RISK_PLATFORM_HTTP_ERROR",
            "malformed": "RISK_PLATFORM_MALFORMED_RESPONSE",
        }.get(kind, "RISK_PLATFORM_ERROR")
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(code=code,
                            message=getattr(e, "message", str(e)),
                            detail={"status_code": getattr(
                                e, "status_code", None)}
                            if getattr(e, "status_code", None) else None),
        )

    try:
        payload = normalize_related_accounts(case_id=case_id,
                                             evidence=evidence)
        # AL-2.14: overlay the complete account list from network-signals
        # onto the cluster context from the evidence payload.
        accounts = [a["user_id"] for a in (ns.get("connected_accounts") or [])
                    if isinstance(a, dict) and a.get("user_id")]
        payload["related_accounts"] = accounts
        payload["related_accounts_count"] = ns.get(
            "connected_account_count", len(accounts))
        payload["evidence_missing"] = not accounts
        if not accounts:
            payload["next_data_needed"] = [
                "related-account attribution for this case"]
    except (KeyError, TypeError, ValueError) as e:
        logger.error("related_account_analysis normalization failure: %s",
                     e)
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code="RISK_PLATFORM_MALFORMED_RESPONSE",
                message=f"Could not normalize network evidence: {e}"),
        )

    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data=payload,
        evidence_refs=[
            EvidenceRef(kind=ref["kind"], id=ref["id"])
            for ref in payload.get("evidence_refs", [])
        ],
    )

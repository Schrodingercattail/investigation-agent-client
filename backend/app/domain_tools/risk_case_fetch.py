"""Week 1 domain tools.

Module instead of an `app/tools/` package: a subpackage of that name would
shadow the superseded-era module app/tools.py which legacy consumers still
import (Python resolves the package first — verified). Renaming/removing
that module is out of scope for this step, so this sibling module hosts the
domain tool boundary. Migration to a package can happen at cleanup time.

Each tool:
- validates its inputs,
- calls the Risk Platform adapter,
- normalizes outcomes to the shared ToolResult contract
  (success | unsupported | empty | integration_error | validation_error),
- never converts RP failures into empty data and never fabricates evidence.
"""

import logging

from pydantic import ValidationError as PydanticValidationError

from app.adapters.risk_platform import (
    RiskPlatformAdapter,
    RiskPlatformError,
    normalize_case,
)
from app.models import Finding, ToolError, ToolResult, ToolResultOutcome

logger = logging.getLogger(__name__)

adapter = RiskPlatformAdapter()


def _validation_error(message: str) -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.VALIDATION_ERROR,
        error=ToolError(code="INVALID_ARGUMENT", message=message),
    )


def _integration_error(err: RiskPlatformError) -> ToolResult:
    code_by_kind = {
        "unavailable": "RISK_PLATFORM_UNAVAILABLE",
        "auth": "RISK_PLATFORM_AUTH_FAILED",
        "http": "RISK_PLATFORM_HTTP_ERROR",
        "malformed": "RISK_PLATFORM_MALFORMED_RESPONSE",
    }
    # Malformed upstream payloads are still upstream problems — integration
    # errors on our side of the boundary (never presented as empty findings).
    return ToolResult(
        outcome=ToolResultOutcome.INTEGRATION_ERROR,
        error=ToolError(
            code=code_by_kind.get(err.kind, "RISK_PLATFORM_ERROR"),
            message=err.message,
            detail={"status_code": err.status_code} if err.status_code else None,
        ),
    )


def risk_case_fetch(case_id: str) -> ToolResult:
    """Fetch the canonical case context from the Risk Platform.

    Establishes the canonical information Case Intake needs:
    Investigation + InvestigationContext + Findings (+ capabilities).

    Success payload:
      {
        case_id, findings: [Finding...], evidence_refs, signal_refs,
        policy_refs, ext: {risk_level, risk_score, ml/rule/graph scores,
                           recommended_action, missing_info,
                           explanation_source}
      }

    Outcome semantics:
    - success   : RP responded; findings exist.
    - empty     : RP responded with a genuinely no-data case
                  (risk_level UNKNOWN, detected_at null, nothing to show).
    - integration_error : transport/auth/HTTP/malformed-upstream failure.
    - validation_error  : invalid local input (e.g. blank case_id).
    """
    if not isinstance(case_id, str) or not case_id.strip():
        return _validation_error(
            "case_id is required for risk_case_fetch (non-empty string)."
        )
    case_id = case_id.strip()

    try:
        evidence, explanation = adapter.fetch_case(case_id)
    except RiskPlatformError as err:
        logger.error("risk_case_fetch integration failure for %s: %s", case_id, err.message)
        return _integration_error(err)

    # Genuine emptiness per RP's own semantics (valid response, nothing there):
    risk_summary = evidence.get("risk_summary") or {}
    is_empty = (
        risk_summary.get("detected_at") is None
        and not (evidence.get("transaction_evidence") or [])
        and not (evidence.get("withdrawal_evidence") or [])
        and not (evidence.get("rule_evidence") or [])
        and not [k for k in (explanation.get("key_findings") or [])]
    )

    if is_empty:
        return ToolResult(
            outcome=ToolResultOutcome.EMPTY,
            data={"case_id": case_id},
            warnings=["Risk Platform has no risk event / evidence for this case."],
        )

    try:
        normalized = normalize_case(
            case_id=case_id, evidence=evidence, explanation=explanation,
        )
        # Validate every finding conforms to the domain model before it can
        # leave the boundary.
        findings = [
            f if isinstance(f, Finding) else Finding(**f)
            for f in normalized["findings"]
        ]
    except (PydanticValidationError, KeyError, TypeError, ValueError) as e:
        logger.error("risk_case_fetch normalization failure for %s: %s", case_id, e)
        return _integration_error(RiskPlatformError(
            "malformed",
            f"Could not normalize Risk Platform response into domain model: {e}",
        ))

    normalized["findings"] = findings
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data=normalized,
        evidence_refs=normalized["evidence_refs"],
        citation_refs=normalized["policy_refs"],
    )

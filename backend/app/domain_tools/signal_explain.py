"""signal_explain — Week 1 finding-signal explanation tool.

Explains an existing finding's ML / Rule / Graph signal strictly from
evidence actually available through the Risk Platform evidence endpoint
(via RiskPlatformAdapter — no RP HTTP logic here, no new RP endpoints).

Grounding rules:
- No attribution is invented: RP exposes no per-transaction/feature
  attribution (ML) and no relationship paths (Graph); their absence is
  reported as success + evidence_missing + next_data_needed, not errors and
  not fabricated content.
- No natural-language narrative is generated here; the response composer
  turns the structured payload into user-facing prose.
- Thresholds, trigger values, and contributions are echoed verbatim from RP
  rule_evidence — never reconstructed.
"""

import logging
from typing import Any, Literal

from app.adapters.risk_platform import (
    RiskPlatformError,
    normalize_graph_signal,
    normalize_ml_signal,
    normalize_rule_signal,
)
from app.models import (
    EvidenceRef,
    Finding,
    PolicyRef,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)

logger = logging.getLogger(__name__)

ALLOWED_SIGNAL_TYPES = ("ML", "Rule", "Graph")


def _graph_relevant_finding(finding: Any) -> bool:
    """Whether the finding's authoritative title names the graph domain
    (device sharing / linked accounts / network clusters) — the SAME
    vocabulary normalize_graph_signal keys on. Deterministic; used only
    when signal_refs carry no ML/Rule/Graph detector type (AL-2.8)."""
    title = (getattr(finding, "title", "") or "").lower()
    return any(w in title for w in ("device", "linked account", "network"))


def _feature_signal_payload(finding: Any, evidence: dict[str, Any]) \
        -> dict[str, Any] | None:
    """Feature-level explanation payload for findings with no rule/ML/
    graph backing (AL-2.9). The finding's own Feature signal_refs name
    the RP risk features; only values that ACTUALLY exist in the case's
    feature_evidence are reported — nothing is synthesized. None when no
    named feature is present in the evidence (caller falls back to the
    bounded missing-evidence outcome).

    When RP's risk_factor_evidence carries the factor THIS finding is
    named after (RP's own evidence-merging convention: factor_name equals
    the finding name — exact match, never a keyword guess), its
    authoritative entry (observed value, severity, and the platform's own
    threshold/trigger-state description) is echoed verbatim under
    explanation.risk_factor. That entry is where RP states the rule and
    threshold semantics for threshold-based features; the tool never
    reconstructs or re-derives them."""
    features = (evidence or {}).get("feature_evidence") or {}
    named: list[dict[str, Any]] = []
    for ref in (finding.signal_refs or []):
        st = getattr(ref, "signal_type", None) \
            if not isinstance(ref, dict) else ref.get("signal_type")
        name = getattr(ref, "name", None) \
            if not isinstance(ref, dict) else ref.get("name")
        if st == "Feature" and name and name in features:
            named.append({"name": name, "value": features[name]})
    if not named:
        return None
    explanation: dict[str, Any] = {"features": named}
    title = (getattr(finding, "title", "") or "").strip()
    if title:
        for factor in (evidence or {}).get("risk_factor_evidence") or []:
            if not isinstance(factor, dict):
                continue
            if factor.get("factor_name") == title:
                explanation["risk_factor"] = {
                    "name": factor.get("factor_name"),
                    "value": factor.get("factor_value"),
                    "severity": factor.get("severity"),
                    # RP's own threshold/trigger-state wording — echoed
                    # verbatim, never paraphrased or re-derived
                    "description": factor.get("factor_description"),
                }
                break
    # The platform's own complete statement about this risk finding (the
    # explanation key-finding text the finding was built from) — carries
    # the rule/threshold/trigger-state semantics when RP states them.
    # Verbatim echo, never re-derived.
    finding_statement = (getattr(finding, "summary", "") or "").strip()
    if finding_statement:
        explanation["finding_summary"] = finding_statement
    return {
        "finding_id": finding.finding_id,
        "signal_type": "Feature",
        "explanation": explanation,
        "evidence_refs": [],
        "signal_refs": [
            s.model_dump() if hasattr(s, "model_dump") else dict(s)
            for s in (finding.signal_refs or [])
        ],
        "policy_refs": [
            p.model_dump() if hasattr(p, "model_dump") else dict(p)
            for p in (finding.policy_refs or [])
        ],
        "evidence_missing": False,
        "next_data_needed": [],
    }


def _validation_error(message: str) -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.VALIDATION_ERROR,
        error=ToolError(code="INVALID_ARGUMENT", message=message),
    )


def signal_explain(
    finding_id: str,
    signal_type: Literal["ML", "Rule", "Graph"] | None = None,
    case_id: str | None = None,
    case_context: dict[str, Any] | None = None,
) -> ToolResult:
    """Explain one finding's detection signal from actual RP evidence.

    Args:
        finding_id: canonical Finding ID from the current case context.
        signal_type: "ML" | "Rule" | "Graph", or None to DERIVE the
            detector identity from the finding's authoritative signal_refs
            (primary-detector precedence Rule > ML > Graph).
        case_id: case whose evidence is consulted (fallback when no
            case_context is supplied).
        case_context: canonical case payload from risk_case_fetch
            ({case_id, findings}) for deterministic finding resolution.

    Returns ToolResult with data payload:
        {finding_id, signal_type, explanation | rule, evidence_refs,
         signal_refs, policy_refs, evidence_missing, next_data_needed}
    """
    # --- input validation -----------------------------------------------------
    if not isinstance(finding_id, str) or not finding_id.strip():
        return _validation_error("finding_id is required (non-empty string).")
    finding_id = finding_id.strip()

    if signal_type is not None and signal_type not in ALLOWED_SIGNAL_TYPES:
        return _validation_error(
            f"signal_type must be one of {list(ALLOWED_SIGNAL_TYPES)} or "
            f"None (derive from the finding), got {signal_type!r}."
        )
    if case_context is None and (not isinstance(case_id, str) or not case_id.strip()):
        return _validation_error(
            "signal_explain requires case context (case_context or case_id)."
        )

    # --- canonical finding resolution -------------------------------------------
    findings: list[Finding] = []
    if case_context is not None:
        findings = list(case_context.get("findings") or [])
        case_id = case_context.get("case_id", case_id)
    if not findings:
        from app.domain_tools.risk_case_fetch import risk_case_fetch
        base = risk_case_fetch(case_id=str(case_id).strip())
        if base.outcome == ToolResultOutcome.EMPTY:
            return ToolResult(
                outcome=ToolResultOutcome.EMPTY,
                data={"finding_id": finding_id, "signal_type": signal_type},
                warnings=["Case has no data; finding cannot exist."],
            )
        if base.outcome != ToolResultOutcome.SUCCESS:
            return base                      # propagate integration/validation
        findings = list((base.data or {}).get("findings") or [])

    matches = [f for f in findings if f.finding_id == finding_id]
    if not matches:
        return _validation_error(
            f"Finding {finding_id!r} does not exist in the current case context."
        )
    finding = matches[0]

    # --- detector identity: authoritative signal_refs decide ------------------
    # When signal_type is not explicitly supplied, derive it from the
    # finding's own structured signal_refs (primary-detector precedence
    # Rule > ML > Graph). An explicitly requested type the finding does not
    # back is BOUNDED (unsupported) — never silently explained as another
    # detector type.
    finding_detector_types: list[str] = []
    for ref in (finding.signal_refs or []):
        st = ref.get("signal_type") if isinstance(ref, dict) \
            else getattr(ref, "signal_type", None)
        if st in ("ML", "Rule", "Graph") and st not in finding_detector_types:
            finding_detector_types.append(st)
    if signal_type is None:
        precedence = {"Rule": 0, "ML": 1, "Graph": 2}
        if finding_detector_types:
            signal_type = sorted(
                finding_detector_types,
                key=lambda t: precedence.get(t, 99))[0]
        elif _graph_relevant_finding(finding):
            # AL-2.8: a finding whose structured refs are Feature-level
            # only (RP emits shared_device_count etc. without a Graph ref)
            # but whose authoritative title names the graph domain is a
            # GRAPH-detected finding — explain its Graph signal instead of
            # defaulting to a Rule lookup that can only report missing
            # evidence. The Graph branch stays evidence-honest (it reports
            # evidence_missing when network data is absent); the vocabulary
            # is the SAME one normalize_graph_signal keys on.
            signal_type = "Graph"
        else:
            signal_type = "Rule"   # no structured signal: rule lookup below
            # reports evidence_missing if nothing backs it (bounded)
    elif (signal_type == "ML" and "ML" not in finding_detector_types
          and "ml pattern detection" not in finding.title.lower()):
        # ML is CASE-LEVEL model data (one model score per case): attaching
        # it to a finding the model did not produce is cross-signal
        # contamination — the previous `finding_detector_types and ...`
        # guard skipped the check exactly for findings with NO structured
        # refs, letting a caller-supplied ML type (e.g. derived from the
        # focus finding) stamp the case's model score onto a feature
        # observation. Rule/Graph lookups are finding-filtered by name and
        # cannot attach another finding's data, so they keep their
        # lookup-then-miss-honestly path.
        return ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(
                code="SIGNAL_TYPE_NOT_SUPPORTED",
                message=(
                    f"Finding {finding_id} is not backed by a {signal_type} "
                    f"signal (its detection signals: "
                    f"{finding_detector_types})."
                ),
                detail={
                    "capability": "signal_explain",
                    "scope": "finding",
                    "finding_id": finding_id,
                    "requested_signal_type": signal_type,
                    "finding_signal_types": finding_detector_types,
                },
            ),
        )

    # --- capability gate (runtime re-check; never converted to empty) ---------
    if not finding.capabilities.supports("signal_explain"):
        return ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(
                code="CAPABILITY_NOT_SUPPORTED",
                message=(
                    f"Finding {finding_id} does not support signal "
                    f"explanation."
                ),
                detail={
                    "capability": "signal_explain",
                    "scope": "finding",
                    "finding_id": finding_id,
                    "supported_capabilities": sorted(finding.capabilities),
                },
            ),
        )

    # --- fetch evidence ------------------------------------------------------------
    try:
        if case_context is not None and "_raw_evidence" in case_context:
            evidence = case_context["_raw_evidence"]
        else:
            from app.adapters.risk_platform import RiskPlatformAdapter
            evidence = RiskPlatformAdapter().fetch_case_evidence(str(case_id))
    except RiskPlatformError as err:
        logger.error("signal_explain integration failure: %s", err.message)
        code = {
            "unavailable": "RISK_PLATFORM_UNAVAILABLE",
            "auth": "RISK_PLATFORM_AUTH_FAILED",
            "http": "RISK_PLATFORM_HTTP_ERROR",
            "malformed": "RISK_PLATFORM_MALFORMED_RESPONSE",
        }.get(err.kind, "RISK_PLATFORM_ERROR")
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(code=code, message=err.message,
                            detail={"status_code": err.status_code}
                            if err.status_code else None),
        )

    # --- normalize per signal type -------------------------------------------------
    try:
        if signal_type == "Rule":
            rule_payload = normalize_rule_signal(finding=finding, evidence=evidence)
            if rule_payload is None:
                # AL-2.9: no rule backs this finding, but its own Feature
                # signal_refs may name RP risk features (e.g. F7's
                # trade_frequency_24h). The feature facts ARE the honest
                # "why" — report them before falling back to the bare
                # missing-evidence outcome (which would also mislabel a
                # feature-detected finding as rule-detected).
                feature_payload = _feature_signal_payload(finding, evidence)
                if feature_payload is not None:
                    return ToolResult(
                        outcome=ToolResultOutcome.SUCCESS,
                        data=feature_payload,
                        evidence_refs=[
                            EvidenceRef(kind=ref["kind"], id=ref["id"])
                            for ref in feature_payload.get("evidence_refs",
                                                           [])
                        ],
                        citation_refs=[
                            PolicyRef(**p)
                            for p in feature_payload.get("policy_refs", [])
                        ],
                    )
                # No rule actually backs this finding: the tool succeeded in
                # retrieving available evidence, but it cannot explain a rule
                # signal → success + evidence_missing (NOT empty, NOT error).
                return ToolResult(
                    outcome=ToolResultOutcome.SUCCESS,
                    data={
                        "finding_id": finding_id,
                        "signal_type": "Rule",
                        "explanation": {},
                        "evidence_refs": [],
                        "signal_refs": [
                            s.model_dump() for s in finding.signal_refs
                        ],
                        "policy_refs": [
                            p.model_dump() for p in finding.policy_refs
                        ],
                        "evidence_missing": True,
                        "next_data_needed": [
                            "triggered-rule evidence associated with this "
                            "finding",
                        ],
                    },
                )
            payload = {
                "finding_id": finding_id,
                **rule_payload,
                "signal_refs": [s.model_dump() for s in finding.signal_refs],
                "policy_refs": [p.model_dump() for p in finding.policy_refs],
                "evidence_missing": False,
                "next_data_needed": [],
            }
        elif signal_type == "ML":
            payload = {
                "finding_id": finding_id,
                **normalize_ml_signal(finding=finding, evidence=evidence),
                "signal_refs": [s.model_dump() for s in finding.signal_refs],
                "policy_refs": [p.model_dump() for p in finding.policy_refs],
            }
        else:   # Graph
            payload = {
                "finding_id": finding_id,
                **normalize_graph_signal(finding=finding, evidence=evidence),
                "signal_refs": [s.model_dump() for s in finding.signal_refs],
                "policy_refs": [p.model_dump() for p in finding.policy_refs],
            }
    except RiskPlatformError as e:
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(code="RISK_PLATFORM_MALFORMED_RESPONSE",
                            message=e.message),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.error("signal_explain normalization failure: %s", e)
        return ToolResult(
            outcome=ToolResultOutcome.INTEGRATION_ERROR,
            error=ToolError(
                code="RISK_PLATFORM_MALFORMED_RESPONSE",
                message=f"Could not normalize evidence into signal "
                        f"explanation: {e}",
            ),
        )

    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data=payload,
        evidence_refs=[
            EvidenceRef(kind=ref["kind"], id=ref["id"])
            for ref in payload.get("evidence_refs", [])
        ],
        citation_refs=[
            PolicyRef(**p) for p in payload.get("policy_refs", [])
        ],
    )

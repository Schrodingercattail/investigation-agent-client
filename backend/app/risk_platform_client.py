"""HTTP client for Risk Platform API integration."""
import logging
from typing import Any

import httpx
from app.config import settings
from app.exceptions import (
    RiskPlatformAuthenticationError,
    RiskPlatformError,
    RiskPlatformUnavailableError,
)


logger = logging.getLogger(__name__)


class RiskPlatformClient:
    """Client for calling Risk Platform APIs with robust error handling."""

    DEFAULT_TIMEOUT = 30.0
    DEFAULT_BASE_URL = "http://localhost:8000"

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        """
        Initialize client with optional base URL override.

        Args:
            base_url: Override base URL (defaults to RISK_PLATFORM_BASE_URL env var)
            timeout: Request timeout in seconds (defaults to DEFAULT_TIMEOUT)
        """
        self.base_url = (base_url or getattr(settings, "RISK_PLATFORM_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        logger.info(f"RiskPlatformClient initialized with base_url: {self.base_url}")

    async def get_case_evidence(self, user_id: str) -> dict[str, Any]:
        """
        Get evidence for a case by user_id.

        Args:
            user_id: User ID to fetch evidence for

        Returns:
            Normalized evidence dict for Agent consumption

        Raises:
            RiskPlatformAuthenticationError: On 401/403 authentication failures
            RiskPlatformUnavailableError: On 503/unavailable errors
            RiskPlatformError: On other HTTP or validation errors
        """
        if not user_id:
            raise RiskPlatformError(
                "user_id is required for get_case_evidence",
                status_code=None,
                response_body=None,
            )

        url = f"{self.base_url}/api/risk/cases/{user_id}/evidence"
        logger.debug(f"Fetching evidence from: {url}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)

                # Handle different status codes with specific exceptions
                if response.status_code in (401, 403):
                    error_msg = f"Risk Platform authentication failed for user {user_id}"
                    logger.error(f"{error_msg}: {response.text[:200]}")
                    raise RiskPlatformAuthenticationError(
                        error_msg,
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )

                elif response.status_code == 404:
                    error_msg = f"Evidence not found for user {user_id}"
                    logger.warning(error_msg)
                    raise RiskPlatformError(
                        error_msg,
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )

                elif response.status_code >= 500:
                    error_msg = f"Risk Platform unavailable (status {response.status_code})"
                    logger.error(f"{error_msg}: {response.text[:200]}")
                    raise RiskPlatformUnavailableError(
                        error_msg,
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )

                elif response.status_code >= 400:
                    error_msg = f"Risk Platform request failed with status {response.status_code}"
                    logger.error(f"{error_msg}: {response.text[:200]}")
                    raise RiskPlatformError(
                        error_msg,
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )

                # Parse JSON response
                try:
                    data = response.json()
                except ValueError as e:
                    error_msg = "Risk Platform returned invalid JSON"
                    logger.error(f"{error_msg}: {response.text[:200]}")
                    raise RiskPlatformError(
                        error_msg,
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    ) from e

                logger.debug(f"Successfully fetched evidence for user {user_id}")
                return self._normalize_evidence_response(data, user_id)

        except httpx.TimeoutException as e:
            error_msg = f"Risk Platform request timed out after {self.timeout}s"
            logger.error(error_msg)
            raise RiskPlatformUnavailableError(
                error_msg,
                status_code=None,
                response_body=None,
            ) from e

        except httpx.NetworkError as e:
            error_msg = f"Risk Platform network error: {str(e)}"
            logger.error(error_msg)
            raise RiskPlatformUnavailableError(
                error_msg,
                status_code=None,
                response_body=None,
            ) from e

        except (RiskPlatformAuthenticationError, RiskPlatformUnavailableError, RiskPlatformError):
            # Re-raise our custom exceptions as-is
            raise

        except Exception as e:
            error_msg = f"Unexpected error fetching Risk Platform evidence: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RiskPlatformError(
                error_msg,
                status_code=None,
                response_body=None,
            ) from e

    async def get_case_explanation(self, user_id: str) -> dict[str, Any]:
        """
        Get authoritative investigation explanation from Risk Platform.

        This calls the Risk Platform explanation API which provides:
        - Authoritative findings (with canonical names)
        - Validated citations (with semantic support)
        - Finding-to-citation mappings
        - Risk summary
        - Recommended actions

        Args:
            user_id: User ID to fetch explanation for (e.g., "U00299")

        Returns:
            Authoritative explanation dict from Risk Platform

        Raises:
            RiskPlatformAuthenticationError: On 401/403 authentication failures
            RiskPlatformUnavailableError: On 503/unavailable errors
            RiskPlatformError: On other HTTP or validation errors
        """
        import asyncio

        if not user_id:
            raise RiskPlatformError(
                "user_id is required for get_case_explanation",
                status_code=None,
                response_body=None,
            )

        url = f"{self.base_url}/api/risk/explain"
        logger.debug(f"Fetching explanation from: {url}")

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
                        self._post_explanation(url, user_id)
                    ).result()
            except RuntimeError:
                # No running loop, create a new one
                explanation = asyncio.run(self._post_explanation(url, user_id))

            logger.debug(f"Successfully fetched explanation for user {user_id}")
            return explanation

        except (RiskPlatformAuthenticationError, RiskPlatformUnavailableError, RiskPlatformError):
            # Re-raise Risk Platform errors as-is
            logger.error(f"Risk Platform error fetching explanation for user {user_id}")
            raise

        except Exception as e:
            # Wrap unexpected errors
            error_msg = f"Failed to fetch explanation for user {user_id}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RiskPlatformError(
                error_msg,
                status_code=None,
                response_body=None,
            ) from e

    async def _post_explanation(self, url: str, user_id: str) -> dict[str, Any]:
        """Internal async method to POST explanation request."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json={"user_id": user_id})

            # Handle different status codes with specific exceptions
            if response.status_code in (401, 403):
                error_msg = f"Risk Platform authentication failed for user {user_id}"
                logger.error(f"{error_msg}: {response.text[:200]}")
                raise RiskPlatformAuthenticationError(
                    error_msg,
                    status_code=response.status_code,
                    response_body=response.text[:500],
                )

            elif response.status_code == 404:
                error_msg = f"Explanation not found for user {user_id}"
                logger.warning(error_msg)
                raise RiskPlatformError(
                    error_msg,
                    status_code=response.status_code,
                    response_body=response.text[:500],
                )

            elif response.status_code >= 500:
                error_msg = f"Risk Platform unavailable (status {response.status_code})"
                logger.error(f"{error_msg}: {response.text[:200]}")
                raise RiskPlatformUnavailableError(
                    error_msg,
                    status_code=response.status_code,
                    response_body=response.text[:500],
                )

            elif response.status_code >= 400:
                error_msg = f"Risk Platform request failed with status {response.status_code}"
                logger.error(f"{error_msg}: {response.text[:200]}")
                raise RiskPlatformError(
                    error_msg,
                    status_code=response.status_code,
                    response_body=response.text[:500],
                )

            # Parse JSON response
            try:
                data = response.json()
            except ValueError as e:
                error_msg = "Risk Platform returned invalid JSON"
                logger.error(f"{error_msg}: {response.text[:200]}")
                raise RiskPlatformError(
                    error_msg,
                    status_code=response.status_code,
                    response_body=response.text[:500],
                ) from e

            return data

    def _normalize_evidence_response(self, risk_platform_response: dict[str, Any], user_id: str) -> dict[str, Any]:
        """
        Normalize Risk Platform evidence response to Agent-facing schema.

        Preserves stable IDs and useful metadata from the source.

        Args:
            risk_platform_response: Raw response from Risk Platform API
            user_id: User ID for the evidence request

        Returns:
            Normalized evidence dict for Agent consumption
        """
        # Use provided user_id if not in response
        response_user_id = risk_platform_response.get("user_id", user_id)
        risk_summary = risk_platform_response.get("risk_summary", {})

        # Normalize evidence items with stable IDs
        evidence = []
        evidence_idx = 1

        # Transaction evidence
        for tx in risk_platform_response.get("transaction_evidence", []):
            evidence.append({
                "evidence_id": f"EV-TX-{tx.get('transaction_id', evidence_idx)}",
                "type": "transaction",
                "description": f"Transaction: {tx.get('symbol', 'N/A')} {tx.get('side', 'N/A')} {tx.get('quantity', 0)} @ {tx.get('price', 0)}",
                "value": tx.get("value", 0),
                "timestamp": tx.get("timestamp"),
                "risk_reason": tx.get("risk_reason"),
                "source": tx,
            })
            evidence_idx += 1

        # Withdrawal evidence
        for wd in risk_platform_response.get("withdrawal_evidence", []):
            evidence.append({
                "evidence_id": f"EV-WD-{wd.get('withdrawal_id', evidence_idx)}",
                "type": "withdrawal",
                "description": f"Withdrawal: {wd.get('amount', 0)} {wd.get('asset', 'N/A')} to {wd.get('address', 'N/A')}",
                "value": wd.get("amount", 0),
                "timestamp": wd.get("timestamp"),
                "risk_reason": wd.get("risk_reason"),
                "is_new_address": wd.get("is_new_address"),
                "source": wd,
            })
            evidence_idx += 1

        # Risk factor evidence
        for factor in risk_platform_response.get("risk_factor_evidence", []):
            evidence.append({
                "evidence_id": f"EV-RF-{factor.get('factor_id', evidence_idx)}",
                "type": "risk_factor",
                "description": factor.get("factor_name", "Risk factor"),
                "value": factor.get("factor_value"),
                "severity": factor.get("severity", "medium"),
                "source": factor,
            })
            evidence_idx += 1

        # Build unified findings from Risk Platform data
        unified_findings = []
        finding_idx = 1

        # ML Pattern Detection Signal - canonical finding when ml_score >= 50
        # This matches Risk Platform's get_canonical_evidence() logic
        ml_score = risk_summary.get("ml_score")
        if ml_score is not None and ml_score >= 50:
            # Use canonical finding name and description from Risk Platform
            unified_findings.append({
                "finding_id": f"F-{finding_idx}",
                "type": "ml_signal",
                "description": f"ML pattern detection score {ml_score}/100 — system signal, not a calibrated probability of fraud",
                "severity": "high" if ml_score > 50 else "medium",
                "canonical_name": "ML Pattern Detection Signal",
            })
            finding_idx += 1
        elif ml_score and ml_score > 10:
            # For cases with ml_score between 10 and 50, create a generic signal finding
            # This will be filtered as aggregate in compose_structured_result
            unified_findings.append({
                "finding_id": f"F-{finding_idx}",
                "type": "ml_signal",
                "description": f"ML-derived risk signal (score: {ml_score:.2f})",
                "severity": "medium",
            })
            finding_idx += 1

        # Rule-based findings from rule_evidence (IMPORTANT: preserve all rules)
        # These are canonical findings from Risk Platform
        rule_evidence = risk_platform_response.get("rule_evidence", [])
        for rule in rule_evidence:
            rule_name = rule.get("rule_name", "Rule")
            rule_description = rule.get("description", "")
            unified_findings.append({
                "finding_id": f"F-{finding_idx}",
                "type": "rule",
                "description": rule_description,
                "severity": rule.get("severity", "MEDIUM").lower(),
                "canonical_name": rule_name,  # Preserve canonical finding name
            })
            finding_idx += 1

        # Rule-based findings from rule_score
        rule_score = risk_summary.get("rule_score")
        if rule_score and rule_score > 15:  # Detection threshold
            unified_findings.append({
                "finding_id": f"F-{finding_idx}",
                "type": "rule_signal",
                "description": f"Rule-based risk indicators (score: {rule_score:.2f})",
                "severity": "high" if rule_score > 50 else "medium",
            })
            finding_idx += 1

        # Graph findings - canonical findings are created from feature_evidence processing above
        # (Shared Device Relationships, Linked Account Network)
        # We don't create a separate "Network relationship detected" finding as it's not canonical

        # Feature-level findings from feature_evidence
        # These are substantive findings that must be preserved
        # Only add findings that weren't already in risk_factor_evidence to avoid duplicates
        feature_evidence = risk_platform_response.get("feature_evidence", {})
        if feature_evidence:
            # Map feature keys to finding names (matches Risk Platform _FEATURE_FINDING_NAMES)
            feature_finding_names = {
                "shared_device_count": "Shared Device Relationships",
                "linked_account_count": "Linked Account Network",
                "trade_frequency_24h": "High Trading Frequency",
                "withdrawal_risk_score": "Abnormal Withdrawal Behavior",
            }

            # Feature fallback evidence text (matches Risk Platform _FEATURE_FALLBACK_EVIDENCE)
            feature_fallback_evidence = {
                "shared_device_count": "{value} linked account(s) through shared devices",
                "linked_account_count": "{value} connected account(s) detected",
                "trade_frequency_24h": "{value} trades were recorded in 24 hours",
                "withdrawal_risk_score": "{percentage:.2f}% of withdrawals were sent to newly encountered addresses, an unusual pattern of destination churn that warrants review.",
            }

            for feat_key, finding_name in feature_finding_names.items():
                value = feature_evidence.get(feat_key)
                if value is None or value == 0:
                    continue

                # Get description from risk_factor_evidence if available, otherwise use fallback
                desc = None
                for factor in risk_platform_response.get("risk_factor_evidence", []):
                    if factor.get("factor_name") == finding_name:
                        desc = factor.get("factor_description")
                        break

                if not desc:
                    fallback = feature_fallback_evidence.get(feat_key, "")
                    # Special formatting for withdrawal_risk_score (convert to percentage)
                    if feat_key == "withdrawal_risk_score":
                        percentage = value * 100  # Convert to percentage
                        desc = fallback.format(percentage=percentage)
                    else:
                        desc = fallback.format(value=value) if fallback else f"{finding_name}: observed value {value}"

                unified_findings.append({
                    "finding_id": f"F-{finding_idx}",
                    "type": "risk_factor",
                    "description": desc,
                    "severity": "medium",
                    "source_feature": feat_key,
                    "canonical_name": finding_name,  # Preserve canonical finding name
                })
                finding_idx += 1

            # Opposite Trade Ratio with threshold semantics
            # Matches Risk Platform _THRESHOLD_FINDINGS logic
            opp_ratio = feature_evidence.get("opposite_trade_ratio")
            if opp_ratio is not None and opp_ratio > 0:
                threshold = 0.4
                rule_triggered = opp_ratio > threshold

                # Use semantic naming based on threshold
                if rule_triggered:
                    factor_name = "Coordinated Trading Pattern"
                    percentage = opp_ratio * 100
                    desc = (
                        f"An opposite-trade ratio of {percentage:.2f}% exceeded the "
                        f"{threshold * 100:.0f}% threshold, triggering the "
                        f"coordinated trading rule."
                    )
                else:
                    factor_name = "Opposite Trade Ratio"
                    percentage = opp_ratio * 100
                    desc = (
                        f"An opposite-trade ratio of {percentage:.2f}% was observed, "
                        f"which is below the {threshold * 100:.0f}% threshold for "
                        f"the coordinated trading rule."
                    )

                unified_findings.append({
                    "finding_id": f"F-{finding_idx}",
                    "type": "risk_factor",
                    "description": desc,
                    "severity": "medium",
                    "source_feature": "opposite_trade_ratio",
                    "canonical_name": factor_name,  # Preserve canonical finding name (Opposite Trade Ratio or Coordinated Trading Pattern)
                })
                finding_idx += 1

        # Add primary reason as a finding
        primary_reason = risk_summary.get("primary_reason")
        if primary_reason:
            unified_findings.append({
                "finding_id": f"F-{finding_idx}",
                "type": "primary_reason",
                "description": primary_reason,
                "severity": "medium",
            })

        # Deduplicate findings by canonical_name
        # One canonical finding may come from multiple sources (rule, feature, etc.)
        # Merge detection sources instead of creating duplicate findings
        deduplicated_findings = {}
        for finding in unified_findings:
            canonical_name = finding.get("canonical_name")

            # If no canonical_name, use the finding_id as unique key
            key = canonical_name if canonical_name else finding.get("finding_id")

            if key not in deduplicated_findings:
                # First occurrence of this canonical finding
                deduplicated_findings[key] = {
                    **finding,
                    "detection_sources": [finding.get("type")]  # Track detection sources
                }
            else:
                # Merge with existing finding
                existing = deduplicated_findings[key]

                # Append detection source if different
                detection_source = finding.get("type")
                if detection_source and detection_source not in existing.get("detection_sources", []):
                    existing["detection_sources"] = existing.get("detection_sources", [])
                    existing["detection_sources"].append(detection_source)

                # Preserve additional metadata (prefer rule-derived descriptions over generic ones)
                if detection_source == "rule" and existing.get("type") != "rule":
                    # Rule-derived finding takes precedence for description
                    existing["description"] = finding.get("description", existing["description"])
                    existing["type"] = "rule"  # Mark as rule-derived

                # Preserve source_feature if available
                if finding.get("source_feature") and not existing.get("source_feature"):
                    existing["source_feature"] = finding.get("source_feature")

        # Convert back to list and remove the temporary detection_sources field
        unified_findings = []
        for finding in deduplicated_findings.values():
            # Remove the temporary detection_sources field (it's not part of the output schema)
            finding_copy = {k: v for k, v in finding.items() if k != "detection_sources"}
            unified_findings.append(finding_copy)

        return {
            "user_id": response_user_id,
            "case_id": user_id,  # Preserve original case_id for Agent context
            "evidence": evidence,
            "unified_findings": unified_findings,
            "risk_summary": {
                "risk_level": risk_summary.get("risk_level", "unknown"),
                "risk_score": risk_summary.get("risk_score", 0.0),
                "primary_reason": risk_summary.get("primary_reason"),
                "recommended_action": risk_summary.get("recommended_action"),
                "detection_methods": risk_summary.get("detection_methods", []),
                "ml_score": ml_score,
                "rule_score": rule_score,
                "graph_score": risk_summary.get("graph_score"),
            },
            "source": {
                "system": "risk-platform",
                "endpoint": "/api/risk/cases/{user_id}/evidence",
            },
        }


# Singleton client instance
risk_platform_client = RiskPlatformClient()

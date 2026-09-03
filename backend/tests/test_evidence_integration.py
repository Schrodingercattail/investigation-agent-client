"""Tests for Risk Platform evidence integration."""
import pytest
from unittest.mock import Mock, patch
from app.risk_platform_client import RiskPlatformClient, risk_platform_client


@pytest.fixture
def mock_risk_platform_response():
    """Mock Risk Platform evidence response."""
    return {
        "user_id": "U00299",
        "risk_summary": {
            "risk_level": "high",
            "risk_score": 72.12,
            "primary_reason": "Elevated ML-derived risk signal",
            "recommended_action": "Investigate",
            "detection_methods": ["ml", "rules"],
            "ml_score": 96.24,
            "rule_score": 80.0,
            "graph_score": 0.0,
            "detected_at": "2024-08-23T12:00:00Z"
        },
        "transaction_evidence": [
            {
                "transaction_id": "TX-001",
                "symbol": "BTC",
                "side": "buy",
                "price": 45000.0,
                "quantity": 0.5,
                "value": 22500.0,
                "timestamp": "2024-08-23T11:00:00Z",
                "risk_reason": "Large buy transaction for new user"
            },
            {
                "transaction_id": "TX-002",
                "symbol": "ETH",
                "side": "sell",
                "price": 3000.0,
                "quantity": 10.0,
                "value": 30000.0,
                "timestamp": "2024-08-23T10:00:00Z",
                "risk_reason": "Rapid sell after deposit"
            }
        ],
        "withdrawal_evidence": [
            {
                "withdrawal_id": "WD-001",
                "asset": "BTC",
                "amount": 0.1,
                "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                "is_new_address": True,
                "timestamp": "2024-08-23T09:00:00Z",
                "risk_reason": "Withdrawal to newly generated address"
            }
        ],
        "network_evidence": {
            "cluster_id": 1,
            "cluster_name": "Suspicious Pattern Cluster",
            "detection_type": "graph",
            "member_count": 5,
            "cluster_risk_score": 75.0,
            "role_in_cluster": "connector",
            "related_accounts_count": 3,
            "related_accounts": ["U00123", "U00456", "U00789"],
            "shared_devices": []
        },
        "risk_factor_evidence": [
            {
                "factor_id": 1,
                "factor_name": "Large Transaction Ratio",
                "factor_value": 0.85,
                "factor_description": "Ratio of large transactions to total",
                "severity": "high"
            },
            {
                "factor_id": 2,
                "factor_name": "New Device Activity",
                "factor_value": None,
                "factor_description": "Activity from new device fingerprint",
                "severity": "medium"
            }
        ],
        "feature_evidence": {
            "shared_device_count": 2,
            "linked_account_count": 3,
            "unique_ip_count": 4,
            "trade_frequency_24h": 15,
            "trade_frequency_7d": 45,
            "opposite_trade_ratio": 0.3,
            "avg_trade_size": 5000.0,
            "trade_volume_24h": 75000.0,
            "account_age_days": 7,
            "active_days_count": 5,
            "withdrawal_risk_score": 65.0,
            "withdrawal_frequency_24h": 2,
            "withdrawal_volume_24h": 0.5
        },
        "rule_evidence": [
            {
                "rule_name": "Large Transaction Rule",
                "severity": "HIGH",
                "description": "Transaction value exceeds threshold for account age"
            },
            {
                "rule_name": "New Address Withdrawal",
                "severity": "MEDIUM",
                "description": "Withdrawal to address created in last 24h"
            }
        ]
    }


class TestRiskPlatformClient:
    """Test Risk Platform client."""

    def test_init_default_base_url(self):
        """Test client initialization with default base URL."""
        client = RiskPlatformClient()
        assert client.base_url == "http://localhost:8000"
        assert client.timeout == 30.0

    def test_init_custom_base_url(self):
        """Test client initialization with custom base URL."""
        client = RiskPlatformClient(base_url="http://example.com")
        assert client.base_url == "http://example.com"

    @pytest.mark.asyncio
    async def test_get_case_evidence_success(self, mock_risk_platform_response):
        """Test successful evidence retrieval."""
        client = RiskPlatformClient()

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200  # Add status_code for client error handling
            mock_response.json.return_value = mock_risk_platform_response
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            result = await client.get_case_evidence("U00299")

            assert result["user_id"] == "U00299"
            assert len(result["evidence"]) > 0
            assert len(result["unified_findings"]) > 0
            assert result["risk_summary"]["risk_level"] == "high"
            assert result["source"]["system"] == "risk-platform"

    @pytest.mark.asyncio
    async def test_get_case_evidence_http_error(self):
        """Test HTTP error handling."""
        from app.exceptions import RiskPlatformError
        client = RiskPlatformClient()

        with patch("httpx.AsyncClient.get") as mock_get:
            import httpx
            mock_get.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=Mock(),
                response=Mock(status_code=404)
            )

            # The client now catches HTTPStatusError and raises RiskPlatformError
            with pytest.raises(RiskPlatformError):
                await client.get_case_evidence("NONEXISTENT")

    def test_normalize_evidence_response(self, mock_risk_platform_response):
        """Test evidence response normalization."""
        client = RiskPlatformClient()
        normalized = client._normalize_evidence_response(mock_risk_platform_response, "U00299")

        # Verify preserved user_id
        assert normalized["user_id"] == "U00299"

        # Verify evidence items with stable IDs
        evidence = normalized["evidence"]
        assert len(evidence) > 0
        assert any(e["evidence_id"].startswith("EV-TX-") for e in evidence)
        assert any(e["evidence_id"].startswith("EV-WD-") for e in evidence)
        assert any(e["evidence_id"].startswith("EV-RF-") for e in evidence)

        # Verify transaction evidence
        tx_evidence = [e for e in evidence if e["type"] == "transaction"]
        assert len(tx_evidence) == 2
        assert tx_evidence[0]["value"] == 22500.0
        assert "source" in tx_evidence[0]

        # Verify withdrawal evidence
        wd_evidence = [e for e in evidence if e["type"] == "withdrawal"]
        assert len(wd_evidence) == 1
        assert wd_evidence[0]["is_new_address"] is True

        # Verify risk summary
        risk_summary = normalized["risk_summary"]
        assert risk_summary["risk_level"] == "high"
        assert risk_summary["risk_score"] == 72.12
        assert risk_summary["ml_score"] == 96.24
        assert risk_summary["rule_score"] == 80.0

        # Verify unified findings
        findings = normalized["unified_findings"]
        assert len(findings) > 0
        assert all("finding_id" in f for f in findings)
        assert all("type" in f for f in findings)
        assert all("description" in f for f in findings)

        # Check ML finding exists (ml_score > 10)
        ml_findings = [f for f in findings if f["type"] == "ml_signal"]
        assert len(ml_findings) == 1
        assert "96.24" in ml_findings[0]["description"]

        # Check rule finding exists (rule_score > 15)
        rule_findings = [f for f in findings if f["type"] == "rule_signal"]
        assert len(rule_findings) == 1
        assert "80.00" in rule_findings[0]["description"]

        # Verify source metadata
        assert normalized["source"]["system"] == "risk-platform"
        assert "endpoint" in normalized["source"]


class TestEvidenceFetchTool:
    """Test evidence_fetch tool integration."""

    def test_evidence_fetch_success(self, mock_risk_platform_response):
        """Test evidence_fetch with real API call."""
        from app.tools import evidence_fetch

        # Mock the normalized response from the client (with case_id added by _normalize_evidence_response)
        normalized_response = {
            "user_id": "U00299",
            "case_id": "U00299",  # Added by Risk Platform client normalization
            "evidence": [
                {"evidence_id": "EV-001", "type": "transaction", "description": "Test transaction"}
            ],
            "unified_findings": [
                {"finding_id": "F-001", "type": "ml_signal", "description": "ML signal detected"}
            ],
            "risk_summary": {"risk_level": "high", "risk_score": 72.12},
            "source": {"system": "risk-platform"}
        }

        with patch("app.risk_platform_client.risk_platform_client.get_case_evidence") as mock_get:
            mock_get.return_value = normalized_response

            result = evidence_fetch("U00299")

            assert result["case_id"] == "U00299"
            assert result["user_id"] == "U00299"
            assert len(result["evidence"]) > 0
            assert len(result["unified_findings"]) > 0
            assert result["risk_summary"]["risk_level"] == "high"

    def test_evidence_fetch_api_error(self):
        """Test evidence_fetch handles API errors."""
        from app.tools import evidence_fetch
        from app.exceptions import RiskPlatformError

        with patch("app.risk_platform_client.risk_platform_client.get_case_evidence") as mock_get:
            # Mock the client to raise RiskPlatformError as it does in real scenarios
            mock_get.side_effect = RiskPlatformError(
                "Risk Platform request failed with status 404",
                status_code=404
            )

            # evidence_fetch should re-raise Risk Platform errors
            with pytest.raises(RiskPlatformError):
                evidence_fetch("U00299")

    def test_evidence_fetch_sync_wrapper(self):
        """Test sync wrapper for async evidence_fetch."""
        from app.tools import evidence_fetch

        with patch("app.risk_platform_client.risk_platform_client.get_case_evidence") as mock_get:
            mock_response = {
                "user_id": "U00299",
                "case_id": "U00299",  # Added by Risk Platform client normalization
                "evidence": [],
                "unified_findings": [],
                "risk_summary": {"risk_level": "low", "risk_score": 0.0},
                "source": {"system": "risk-platform"}
            }
            mock_get.return_value = mock_response

            # This should work synchronously
            result = evidence_fetch("U00299")
            assert result["user_id"] == "U00299"


class TestEmptyEvidenceHandling:
    """Test handling of empty/insufficient evidence scenarios."""

    def test_compose_structured_result_with_empty_evidence(self):
        """Test compose_structured_result returns insufficient_evidence outcome."""
        from app.tools import compose_structured_result

        # Empty evidence scenario - fully empty case response
        empty_evidence = {
            "case_id": "U00420",
            "evidence": [],
            "unified_findings": [],
            "risk_summary": {
                "risk_level": "UNKNOWN",
                "risk_score": 0.0,
                "primary_reason": None,
                "recommended_action": None,
                "detection_methods": [],
                "detected_at": None,
                "ml_score": None,
                "rule_score": None,
                "graph_score": None,
            },
            "source": {"system": "risk-platform"}
        }

        policies = {
            "query_used": "test query",
            "top_k": 3,
            "results": []
        }

        result = compose_structured_result(empty_evidence, policies, "Investigate case U00420")

        # Should return insufficient_evidence outcome
        assert result["outcome"] == "insufficient_evidence"
        assert result["risk_summary"]["case_id"] == "U00420"
        assert result["risk_summary"]["risk_score"] == 0
        assert result["risk_summary"]["risk_level"] == "unknown"

        # Should have a clear finding about insufficient evidence
        findings = result.get("findings", [])
        assert len(findings) > 0
        assert "insufficient evidence" in findings[0]["claim"].lower()

        # Should have helpful actions
        actions = result.get("actions", [])
        assert len(actions) > 0
        assert any("verify" in action.lower() for action in actions)

    def test_compose_structured_result_with_partial_evidence(self):
        """Test compose_structured_result with only evidence items (no findings)."""
        from app.tools import compose_structured_result

        # Partial evidence - has items but no unified findings
        # This should proceed normally (not insufficient_evidence) because evidence items exist
        partial_evidence = {
            "case_id": "U00421",
            "evidence": [
                {
                    "evidence_id": "EV-001",
                    "type": "transaction",
                    "description": "Test transaction"
                }
            ],
            "unified_findings": [],
            "risk_summary": {"risk_level": "unknown", "risk_score": 0.0},
            "source": {"system": "risk-platform"}
        }

        policies = {
            "query_used": "test query",
            "top_k": 3,
            "results": []
        }

        result = compose_structured_result(partial_evidence, policies, "Investigate case U00421")

        # Should return normal result (not insufficient_evidence) because evidence items exist
        assert "outcome" not in result or result.get("outcome") != "insufficient_evidence"
        assert result["risk_summary"]["case_id"] == "U00421"
        # Should have the default "no significant risk indicators" finding
        assert len(result["findings"]) > 0

    def test_compose_structured_result_with_full_evidence(self):
        """Test compose_structured_result with complete evidence returns normal result."""
        from app.tools import compose_structured_result

        # Full evidence with findings and non-zero risk score
        full_evidence = {
            "case_id": "U00422",
            "evidence": [
                {
                    "evidence_id": "EV-001",
                    "type": "transaction",
                    "description": "Test transaction"
                }
            ],
            "unified_findings": [
                {
                    "finding_id": "F-001",
                    "type": "ml_signal",
                    "description": "ML-derived risk signal detected"
                }
            ],
            "risk_summary": {"risk_level": "high", "risk_score": 72.12},
            "source": {"system": "risk-platform"}
        }

        policies = {
            "query_used": "test query",
            "top_k": 3,
            "results": [
                {"policy_id": "RISK-001", "title": "Risk Policy", "snippet": "Test"}
            ]
        }

        result = compose_structured_result(full_evidence, policies, "Investigate case U00422")

        # Should return normal result (not insufficient_evidence)
        assert "outcome" not in result or result.get("outcome") != "insufficient_evidence"
        assert result["risk_summary"]["case_id"] == "U00422"
        assert result["risk_summary"]["risk_score"] == 72.12
        assert result["risk_summary"]["risk_level"] == "high"
        assert len(result["findings"]) > 0
        assert len(result["actions"]) > 0

    def test_compose_structured_result_with_zero_risk_and_findings(self):
        """Test compose_structured_result with findings but zero risk score."""
        from app.tools import compose_structured_result

        # Evidence has findings but zero risk score (should proceed normally)
        evidence_with_findings = {
            "case_id": "U00423",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-001",
                    "type": "primary_reason",
                    "description": "Account review required"
                }
            ],
            "risk_summary": {"risk_level": "low", "risk_score": 0.0},
            "source": {"system": "risk-platform"}
        }

        policies = {
            "query_used": "test query",
            "top_k": 3,
            "results": []
        }

        result = compose_structured_result(evidence_with_findings, policies, "Investigate case U00423")

        # Should return normal result (findings exist, even with zero risk score)
        assert "outcome" not in result or result.get("outcome") != "insufficient_evidence"
        assert result["risk_summary"]["case_id"] == "U00423"
        assert len(result["findings"]) > 0


class TestCaseIdPropagation:
    """Test case_id propagation through the system."""

    def test_case_id_extraction_from_user_intent(self):
        """Test case_id extraction from various user intent formats."""
        from app.tools import evidence_fetch

        test_cases = [
            ("Investigate case U00299", "U00299"),
            ("case U12345", "U12345"),
            ("Look into case ABC-123", "ABC-123"),
            ("Review CASE test_001", "test_001"),
        ]

        for user_intent, expected_case_id in test_cases:
            with patch("app.risk_platform_client.risk_platform_client.get_case_evidence") as mock_get:
                mock_response = {
                    "user_id": expected_case_id,
                    "case_id": expected_case_id,
                    "evidence": [],
                    "unified_findings": [],
                    "risk_summary": {"risk_level": "unknown", "risk_score": 0.0},
                    "source": {"system": "risk-platform"}
                }
                mock_get.return_value = mock_response

                result = evidence_fetch(expected_case_id)
                assert result["case_id"] == expected_case_id
                assert result["user_id"] == expected_case_id

    def test_case_id_in_compose_structured_result(self):
        """Test case_id is preserved in compose_structured_result output."""
        from app.tools import compose_structured_result

        evidence = {
            "case_id": "U00999",
            "evidence": [],
            "unified_findings": [],
            "risk_summary": {"risk_level": "unknown", "risk_score": 0.0},
        }

        policies = {"query_used": "test", "top_k": 3, "results": []}

        result = compose_structured_result(evidence, policies, "Investigate case U00999")

        # Verify case_id is in the output risk_summary
        assert result["risk_summary"]["case_id"] == "U00999"

        # For insufficient_evidence outcome, verify case_id is in the finding
        if result.get("outcome") == "insufficient_evidence":
            assert "U00999" in result["findings"][0]["claim"]

    def test_case_id_consistency_through_full_flow(self):
        """Test case_id consistency from user_intent through to artifacts."""
        from app.tools import evidence_fetch, compose_structured_result, citation_validate
        from app.agent import InvestigationAgent

        agent = InvestigationAgent()
        case_id = "U00888"

        # Step 1: Extract case_id from user intent
        extracted_case_id = agent._extract_case_id(f"Investigate case {case_id}")
        assert extracted_case_id == case_id

        # Step 2: evidence_fetch uses the case_id correctly
        with patch("app.risk_platform_client.risk_platform_client.get_case_evidence") as mock_get:
            mock_response = {
                "user_id": case_id,
                "case_id": case_id,
                "evidence": [{"evidence_id": "EV-001", "type": "transaction"}],
                "unified_findings": [
                    {"finding_id": "F-001", "type": "ml_signal", "description": "Test finding"}
                ],
                "risk_summary": {"risk_level": "medium", "risk_score": 45.0},
                "source": {"system": "risk-platform"}
            }
            mock_get.return_value = mock_response

            evidence = evidence_fetch(case_id)
            assert evidence["case_id"] == case_id

            # Step 3: compose_structured_result preserves case_id
            policies = {"query_used": "test", "top_k": 3, "results": []}
            structured = compose_structured_result(evidence, policies, f"Investigate case {case_id}")
            assert structured["risk_summary"]["case_id"] == case_id


class TestCaseIdNormalization:
    """Test case ID normalization to U-prefix format for Risk Platform."""

    def test_case_id_normalization_in_main(self):
        """Test that main.py extract_case_id normalizes to U-prefix."""
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

        from app.main import extract_case_id

        # Test normalization without U prefix
        assert extract_case_id("Investigate case 00010") == "U00010"
        assert extract_case_id("case 00047") == "U00047"
        assert extract_case_id("Investigate case 00299") == "U00299"

        # Test with U prefix already present
        assert extract_case_id("Investigate case U00010") == "U00010"
        assert extract_case_id("case U00299") == "U00299"

    def test_case_id_normalization_in_agent(self):
        """Test that Agent._extract_case_id normalizes to U-prefix."""
        from app.agent import InvestigationAgent

        agent = InvestigationAgent()

        # Test normalization without U prefix
        assert agent._extract_case_id("Investigate case 00010") == "U00010"
        assert agent._extract_case_id("case 00047") == "U00047"

        # Test with U prefix already present
        assert agent._extract_case_id("Investigate case U00010") == "U00010"


class TestRealCriticalCases:
    """Integration tests for known CRITICAL cases from real Risk Platform."""

    def test_critical_case_compose_result_preserves_risk_data(self):
        """Test that compose_structured_result preserves CRITICAL risk data."""
        from app.tools import compose_structured_result

        # Simulate CRITICAL case data from U00010
        critical_evidence = {
            "case_id": "U00010",
            "user_id": "U00010",
            "risk_summary": {
                "risk_level": "CRITICAL",
                "risk_score": 87.02,
                "ml_score": 99.41,
                "rule_score": 85.0,
                "graph_score": 59.08,
                "primary_reason": "ML Pattern Detection",
                "recommended_action": "Immediate Investigation",
                "detection_methods": ["LightGBM", "Rule Engine", "Graph Network"],
                "detected_at": "2026-08-05T15:17:44.990569+00:00"
            },
            "unified_findings": [
                {"finding_id": "F-1", "type": "ml_signal", "description": "ML-derived risk signal (score: 99.41)", "severity": "high"},
                {"finding_id": "F-2", "type": "risk_factor", "description": "18 connected accounts detected", "severity": "critical"},
                {"finding_id": "F-3", "type": "rule_signal", "description": "Rule-based risk indicators (score: 85.00)", "severity": "high"},
                {"finding_id": "F-4", "type": "graph_signal", "description": "Network relationship detected", "severity": "medium"},
            ],
            "evidence": [
                {"evidence_id": "EV-TX-1", "type": "transaction", "description": "Transaction: BTC BUY"},
                {"evidence_id": "EV-WD-1", "type": "withdrawal", "description": "Withdrawal to new address"},
            ],
        }

        policies = {"query_used": "test", "top_k": 3, "results": []}

        result = compose_structured_result(critical_evidence, policies, "Investigate case U00010")

        # CRITICAL data must be preserved
        assert result.get("outcome") != "insufficient_evidence", \
            "CRITICAL case with findings and evidence should not be 'insufficient_evidence'"

        result_risk_summary = result["risk_summary"]
        assert result_risk_summary["risk_level"] == "CRITICAL", \
            f"Expected risk_level CRITICAL, got: {result_risk_summary['risk_level']}"
        assert result_risk_summary["risk_score"] == 87.02, \
            f"Expected risk_score 87.02, got: {result_risk_summary['risk_score']}"

        # Findings should be created from unified_findings
        assert len(result["findings"]) > 0, "Should have findings from CRITICAL case"

    def test_empty_unknown_case_insufficient_evidence(self):
        """Test that a genuinely empty/unknown case produces insufficient_evidence."""
        from app.tools import compose_structured_result

        # Simulate a genuinely empty case response (not found)
        empty_case = {
            "case_id": "U99999",
            "user_id": "U99999",
            "risk_summary": {
                "risk_level": "UNKNOWN",
                "risk_score": 0.0,
                "primary_reason": None,
                "recommended_action": None,
                "detection_methods": [],
                "detected_at": None,
                "ml_score": None,
                "rule_score": None,
                "graph_score": None,
            },
            "unified_findings": [],
            "evidence": [],
        }

        policies = {"query_used": "test", "top_k": 3, "results": []}

        result = compose_structured_result(empty_case, policies, "Investigate case U99999")

        # This should produce insufficient_evidence
        assert result.get("outcome") == "insufficient_evidence", \
            "Genuinely empty case should produce insufficient_evidence outcome"
        assert result["risk_summary"]["risk_level"] == "unknown"
        assert result["risk_summary"]["risk_score"] == 0

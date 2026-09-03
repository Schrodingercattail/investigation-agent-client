"""
Tests for risk_case_fetch tool integration with Risk Platform.

These tests validate that the Agent Client correctly consumes authoritative
findings and citations from the Risk Platform without reconstruction or
re-validation.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import httpx


class TestRiskCaseFetchIntegration:
    """Test integration with Risk Platform /api/risk/explain endpoint."""

    def test_risk_case_fetch_calls_correct_endpoint(self):
        """Test that risk_case_fetch calls the correct Risk Platform endpoint."""
        from app.tools import risk_case_fetch
        from app.risk_platform_client import risk_platform_client

        # Mock the HTTP client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "summary": "Test summary",
            "key_findings": ["Test finding"],
            "recommended_action": "Monitor",
            "citations": [],
            "explanation_source": "MODEL_FALLBACK",
            "llm_error": None,
            "missing_info": []
        }

        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = risk_case_fetch("U00299")

            # Verify the correct endpoint was called
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "http://localhost:8000/api/risk/explain"
            assert call_args[1]["json"] == {"user_id": "U00299"}

    def test_risk_case_fetch_preserves_authoritative_findings(self):
        """Test that risk_case_fetch preserves findings as-is from Risk Platform."""
        from app.tools import risk_case_fetch

        authoritative_response = {
            "summary": "Test summary",
            "key_findings": [
                "1. ML Pattern Detection — 96.24/100; a system signal",
                "2. Coordinated Trading Pattern",
                "3. High Withdrawal Frequency",
                "4. First Withdrawal to a New Address",
                "5. Abnormal Withdrawal Behavior"
            ],
            "recommended_action": "Manual Review",
            "citations": [
                {
                    "id": 1,
                    "doc": "Risk_Scoring_Explainability_Guide.md",
                    "section": "2.1 ML Factors",
                    "quote": "ML drivers should be described as evidence-backed signals",
                    "chunk_id": "Risk_Scoring_Explainability_Guide#2.1#001"
                },
                {
                    "id": 2,
                    "doc": "AML_Suspicious_Indicators.md",
                    "section": "2.1 High-Velocity Transfers",
                    "quote": "A sudden spike in the number of outgoing transfers",
                    "chunk_id": "AML_Suspicious_Indicators#2.1#001"
                }
            ],
            "explanation_source": "LLM",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = authoritative_response

            result = risk_case_fetch("U00299")

            # Verify all authoritative fields are preserved
            assert result["summary"] == authoritative_response["summary"]
            assert len(result["key_findings"]) == 5
            assert result["key_findings"][0] == authoritative_response["key_findings"][0]
            assert len(result["citations"]) == 2
            assert result["citations"][0]["id"] == 1
            assert result["explanation_source"] == "LLM"

    def test_risk_case_fetch_u00010_returns_9_findings(self):
        """Test that U00010 returns 9 authoritative findings."""
        from app.tools import risk_case_fetch

        u00010_response = {
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
            "recommended_action": "Escalate for manual review",
            "citations": [
                {"id": 1, "doc": "Test.md", "section": "1", "quote": "Test", "chunk_id": "test"}
            ],
            "explanation_source": "MODEL_FALLBACK",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = u00010_response

            result = risk_case_fetch("U00010")

            # Verify 9 findings are preserved
            assert len(result["key_findings"]) == 9
            assert result["key_findings"][0].startswith("1. ML Pattern Detection")
            assert result["key_findings"][8].startswith("9. Opposite trade ratio")

    def test_risk_case_fetch_u00299_returns_5_findings(self):
        """Test that U00299 returns 5 authoritative findings."""
        from app.tools import risk_case_fetch

        u00299_response = {
            "summary": "High risk coordinated trading pattern detected",
            "key_findings": [
                "1. ML Pattern Detection — 96.24/100",
                "2. Coordinated Trading Pattern",
                "3. High Withdrawal Frequency",
                "4. First Withdrawal to a New Address",
                "5. Abnormal Withdrawal Behavior"
            ],
            "recommended_action": "Manual Review",
            "citations": [
                {"id": 1, "doc": "AML.md", "section": "2.1", "quote": "Test", "chunk_id": "test1"},
                {"id": 2, "doc": "KYC.md", "section": "2.2", "quote": "Test", "chunk_id": "test2"},
                {"id": 3, "doc": "RISK.md", "section": "1", "quote": "Test", "chunk_id": "test3"}
            ],
            "explanation_source": "LLM",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = u00299_response

            result = risk_case_fetch("U00299")

            # Verify 5 findings and 3 citations are preserved
            assert len(result["key_findings"]) == 5
            assert len(result["citations"]) == 3
            assert result["key_findings"][1].startswith("2. Coordinated Trading Pattern")

    def test_risk_case_fetch_preserves_ml_pattern_detection(self):
        """Test that ML Pattern Detection finding is preserved as-is."""
        from app.tools import risk_case_fetch

        response = {
            "summary": "Test",
            "key_findings": [
                "1. ML Pattern Detection — 96.24/100; a system signal, not a calibrated probability of fraud"
            ],
            "recommended_action": "Monitor",
            "citations": [],
            "explanation_source": "MODEL_FALLBACK",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify ML Pattern Detection text is preserved exactly
            assert result["key_findings"][0] == response["key_findings"][0]
            assert "ML Pattern Detection" in result["key_findings"][0]
            assert "96.24/100" in result["key_findings"][0]
            assert "system signal" in result["key_findings"][0]

    def test_risk_case_fetch_preserves_opposite_trade_ratio_below_threshold(self):
        """Test that U00010 Opposite Trade Ratio preserves below-threshold semantics (34.38%)."""
        from app.tools import risk_case_fetch

        response = {
            "summary": "Test",
            "key_findings": [
                "9. Opposite trade ratio\nAn opposite-trade ratio of 34.38% was observed, which is below the 40% threshold for the coordinated trading rule."
            ],
            "recommended_action": "Monitor",
            "citations": [],
            "explanation_source": "MODEL_FALLBACK",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00010")

            # Verify below-threshold semantics are preserved
            finding_text = result["key_findings"][0]
            assert "34.38%" in finding_text
            assert "below the 40% threshold" in finding_text
            assert "Opposite trade ratio" in finding_text

    def test_risk_case_fetch_preserves_coordinated_trading_above_threshold(self):
        """Test that U00299 Coordinated Trading Pattern preserves above-threshold semantics (45.24%)."""
        from app.tools import risk_case_fetch

        response = {
            "summary": "Test",
            "key_findings": [
                "2. Coordinated Trading Pattern\nAn opposite-trade ratio of 45.24% exceeded the 40% threshold, triggering the coordinated trading rule."
            ],
            "recommended_action": "Manual Review",
            "citations": [],
            "explanation_source": "LLM",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify above-threshold semantics are preserved
            finding_text = result["key_findings"][0]
            assert "45.24%" in finding_text
            assert "exceeded the 40% threshold" in finding_text
            assert "triggering the coordinated trading rule" in finding_text
            assert "Coordinated Trading Pattern" in finding_text

    def test_risk_case_fetch_preserves_citations_from_risk_platform(self):
        """Test that citations come from Risk Platform as-is."""
        from app.tools import risk_case_fetch

        response = {
            "summary": "Test",
            "key_findings": ["Test finding [1]"],
            "recommended_action": "Monitor",
            "citations": [
                {
                    "id": 1,
                    "doc": "AML_Suspicious_Indicators.md",
                    "section": "2. Transaction Velocity & Burst Patterns / 2.1 High-Velocity Transfers",
                    "quote": "A sudden spike in the number of outgoing or incoming transfers within a short time window may indicate account takeover attempts",
                    "chunk_id": "AML_Suspicious_Indicators#2.1#001"
                }
            ],
            "explanation_source": "LLM",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify citation structure is preserved
            assert len(result["citations"]) == 1
            citation = result["citations"][0]
            assert citation["id"] == 1
            assert citation["doc"] == "AML_Suspicious_Indicators.md"
            assert "High-Velocity Transfers" in citation["section"]
            assert "account takeover attempts" in citation["quote"]
            assert citation["chunk_id"] == "AML_Suspicious_Indicators#2.1#001"

    def test_risk_case_fetch_does_not_reconstruct_findings(self):
        """Test that Agent does not reconstruct or modify findings."""
        from app.tools import risk_case_fetch

        original_finding = "1. ML Pattern Detection — 96.24/100; a system signal, not a calibrated probability of fraud"

        response = {
            "summary": "Test",
            "key_findings": [original_finding],
            "recommended_action": "Monitor",
            "citations": [],
            "explanation_source": "MODEL_FALLBACK",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify finding is exactly as Risk Platform provided
            assert result["key_findings"][0] == original_finding
            # No reconstruction, no renaming, no reformatting

    def test_risk_case_fetch_does_not_calculate_citation_support(self):
        """Test that Agent does not calculate citation support locally."""
        from app.tools import risk_case_fetch

        # Risk Platform returns 3 citations for 5 findings (not 4)
        response = {
            "summary": "Test",
            "key_findings": [
                "1. ML Pattern Detection",
                "2. Coordinated Trading Pattern",
                "3. High Withdrawal Frequency",
                "4. First Withdrawal to a New Address",
                "5. Abnormal Withdrawal Behavior"
            ],
            "recommended_action": "Manual Review",
            "citations": [
                {"id": 1, "doc": "AML.md", "section": "1", "quote": "Test", "chunk_id": "c1"},
                {"id": 2, "doc": "KYC.md", "section": "1", "quote": "Test", "chunk_id": "c2"},
                {"id": 3, "doc": "RISK.md", "section": "1", "quote": "Test", "chunk_id": "c3"}
            ],
            "explanation_source": "LLM",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify Agent uses Risk Platform citation count (3), not local calculation
            assert len(result["citations"]) == 3
            # No additional "supported_count" or citation validation fields added
            assert "supported_count" not in result

    def test_risk_case_fetch_handles_missing_case_id(self):
        """Test that risk_case_fetch raises ToolArgumentError for missing case_id."""
        from app.tools import risk_case_fetch
        from app.exceptions import ToolArgumentError

        with pytest.raises(ToolArgumentError) as exc_info:
            risk_case_fetch("")

        assert "case_id is required" in str(exc_info.value)
        assert exc_info.value.tool_name == "risk_case_fetch"

    def test_risk_case_fetch_handles_risk_platform_unavailable(self):
        """Test that risk_case_fetch propagates Risk Platform errors."""
        from app.tools import risk_case_fetch
        from app.exceptions import RiskPlatformUnavailableError

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            import asyncio
            mock_explain.side_effect = RiskPlatformUnavailableError(
                "Risk Platform unavailable",
                status_code=503,
                response_body=None
            )

            with pytest.raises(RiskPlatformUnavailableError):
                risk_case_fetch("U00299")

    def test_risk_case_fetch_preserves_explanation_metadata(self):
        """Test that explanation metadata (source, errors, missing info) is preserved."""
        from app.tools import risk_case_fetch

        response = {
            "summary": "Test",
            "key_findings": ["Test finding"],
            "recommended_action": "Monitor",
            "citations": [],
            "explanation_source": "MODEL_FALLBACK",
            "llm_error": None,
            "missing_info": ["account_age", "trade_volume"]
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify metadata is preserved
            assert result["explanation_source"] == "MODEL_FALLBACK"
            assert result["llm_error"] is None
            assert result["missing_info"] == ["account_age", "trade_volume"]

    def test_risk_case_fetch_handles_llm_generation_error(self):
        """Test that LLM generation errors are preserved in response."""
        from app.tools import risk_case_fetch

        response = {
            "summary": "Test summary",
            "key_findings": ["Test finding"],
            "recommended_action": "Monitor",
            "citations": [],
            "explanation_source": "MODEL_FALLBACK",
            "llm_error": "LLM service unavailable, using model fallback",
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify LLM error is preserved
            assert result["explanation_source"] == "MODEL_FALLBACK"
            assert result["llm_error"] == "LLM service unavailable, using model fallback"

    def test_risk_case_fetch_tool_in_registry(self):
        """Test that risk_case_fetch is registered in TOOL_REGISTRY."""
        from app.tools import TOOL_REGISTRY

        assert "risk_case_fetch" in TOOL_REGISTRY
        assert callable(TOOL_REGISTRY["risk_case_fetch"])


class TestRiskCaseFetchProtocol:
    """Test that risk_case_fetch maintains Agent protocol compatibility."""

    def test_risk_case_fetch_returns_dict(self):
        """Test that risk_case_fetch returns a dict (Agent protocol)."""
        from app.tools import risk_case_fetch

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = {
                "summary": "Test",
                "key_findings": ["Test"],
                "recommended_action": "Monitor",
                "citations": [],
                "explanation_source": "MODEL_FALLBACK",
                "llm_error": None,
                "missing_info": []
            }

            result = risk_case_fetch("U00299")

            # Verify dict return type for Agent compatibility
            assert isinstance(result, dict)
            assert "summary" in result
            assert "key_findings" in result

    def test_risk_case_findings_have_citation_markers(self):
        """Test that findings preserve citation markers from Risk Platform."""
        from app.tools import risk_case_fetch

        response = {
            "summary": "Test",
            "key_findings": [
                "1. ML Pattern Detection — 96.24/100",
                "2. Coordinated Trading Pattern [1]",
                "3. High Withdrawal Frequency [2]"
            ],
            "recommended_action": "Manual Review",
            "citations": [
                {"id": 1, "doc": "AML.md", "section": "1", "quote": "Test", "chunk_id": "c1"},
                {"id": 2, "doc": "KYC.md", "section": "1", "quote": "Test", "chunk_id": "c2"}
            ],
            "explanation_source": "LLM",
            "llm_error": None,
            "missing_info": []
        }

        with patch('app.risk_platform_client.risk_platform_client.get_case_explanation') as mock_explain:
            mock_explain.return_value = response

            result = risk_case_fetch("U00299")

            # Verify citation markers are preserved
            assert "[1]" in result["key_findings"][1]
            assert "[2]" in result["key_findings"][2]
            # ML Pattern Detection has no marker (score summary)
            assert "[" not in result["key_findings"][0]

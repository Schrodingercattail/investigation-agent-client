"""
Tests for provenance model in findings and citations.

Tests cover:
1. Provenance type mapping from Risk Platform finding types
2. Preservation of all Risk Platform findings with provenance
3. Source-grounded vs unsupported distinction in citation validation
4. Frontend rendering with provenance badges
5. Real CRITICAL case (U00010) findings completeness
"""

import pytest
from app.models import ProvenanceType
from app.tools import compose_structured_result, citation_validate, execute_tool


class TestProvenanceModel:
    """Tests for provenance type enumeration and mapping."""

    def test_provenance_type_enum_values(self):
        """Test that ProvenanceType enum has expected values."""
        assert ProvenanceType.DIRECT_EVIDENCE.value == "direct_evidence"
        assert ProvenanceType.RULE_DERIVED.value == "rule_derived"
        assert ProvenanceType.ML_DERIVED.value == "ml_derived"
        assert ProvenanceType.GRAPH_DERIVED.value == "graph_derived"
        assert ProvenanceType.PRIMARY_REASON.value == "primary_reason"
        assert ProvenanceType.UNKNOWN.value == "unknown"


class TestRiskPlatformFindingProvenance:
    """Tests for mapping Risk Platform finding types to provenance types."""

    @pytest.fixture
    def mock_evidence_with_risk_factor(self):
        """Mock evidence with risk_factor finding."""
        return {
            "case_id": "U00010",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-1",
                    "type": "risk_factor",
                    "description": "Test risk factor",
                    "source_evidence_id": "EV-RF-123",
                }
            ],
            "risk_summary": {"risk_level": "HIGH", "risk_score": 72.0},
        }

    @pytest.fixture
    def mock_evidence_with_rule_finding(self):
        """Mock evidence with rule finding."""
        return {
            "case_id": "U00010",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-2",
                    "type": "rule",
                    "description": "Test rule violation",
                    "source_evidence_id": None,
                }
            ],
            "risk_summary": {"risk_level": "HIGH", "risk_score": 72.0},
        }

    @pytest.fixture
    def mock_policies(self):
        """Mock policies response."""
        return {
            "query_used": "test",
            "top_k": 3,
            "results": [
                {"policy_id": "AML-001", "title": "AML", "snippet": "Test"},
            ]
        }

    def test_risk_factor_maps_to_direct_evidence(self, mock_evidence_with_risk_factor, mock_policies):
        """Test that risk_factor findings map to direct_evidence provenance."""
        result = compose_structured_result(
            mock_evidence_with_risk_factor, mock_policies, "test"
        )

        assert len(result["findings"]) == 1
        finding = result["findings"][0]
        provenance = finding["provenance"]

        assert provenance["type"] == ProvenanceType.DIRECT_EVIDENCE.value
        assert provenance["source"] == "risk_platform"
        assert provenance["source_finding_id"] == "F-1"
        assert provenance["evidence_ids"] == ["EV-RF-123"]

    def test_rule_finding_maps_to_rule_derived(self, mock_evidence_with_risk_factor, mock_policies):
        """Test that rule findings map to rule_derived provenance."""
        # Create evidence with rule finding
        evidence = {
            "case_id": "U00010",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-3",
                    "type": "rule",
                    "description": "Test rule violation",
                    "source_evidence_id": None,
                }
            ],
            "risk_summary": {"risk_level": "HIGH", "risk_score": 72.0},
        }

        result = compose_structured_result(evidence, mock_policies, "test")

        assert len(result["findings"]) == 1
        finding = result["findings"][0]
        provenance = finding["provenance"]

        assert provenance["type"] == ProvenanceType.RULE_DERIVED.value
        assert provenance["source"] == "risk_platform"
        assert provenance["source_finding_id"] == "F-3"
        # Rule findings may not have evidence_ids
        assert provenance["evidence_ids"] == []

    def test_ml_signal_maps_to_ml_derived(self, mock_policies):
        """Test that ml_signal findings map to ml_derived provenance."""
        evidence = {
            "case_id": "U00010",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-4",
                    "type": "ml_signal",
                    "description": "ML-derived risk signal",
                    "source_evidence_id": None,
                }
            ],
            "risk_summary": {"risk_level": "HIGH", "risk_score": 72.0},
        }

        result = compose_structured_result(evidence, mock_policies, "test")

        finding = result["findings"][0]
        assert finding["provenance"]["type"] == ProvenanceType.ML_DERIVED.value

    def test_graph_signal_maps_to_graph_derived(self, mock_policies):
        """Test that graph_signal findings map to graph_derived provenance."""
        evidence = {
            "case_id": "U00010",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-5",
                    "type": "graph_signal",
                    "description": "Network relationship detected",
                    "source_evidence_id": None,
                }
            ],
            "risk_summary": {"risk_level": "HIGH", "risk_score": 72.0},
        }

        result = compose_structured_result(evidence, mock_policies, "test")

        finding = result["findings"][0]
        assert finding["provenance"]["type"] == ProvenanceType.GRAPH_DERIVED.value

    def test_primary_reason_excluded_from_findings(self, mock_policies):
        """Test that primary_reason findings are excluded from source findings and placed in risk_summary."""
        evidence = {
            "case_id": "U00010",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-6",
                    "type": "primary_reason",
                    "description": "ML Pattern Detection",
                    "source_evidence_id": None,
                },
                {
                    "finding_id": "F-7",
                    "type": "risk_factor",
                    "description": "Substantive risk factor",
                    "source_evidence_id": None,
                }
            ],
            "risk_summary": {
                "risk_level": "HIGH",
                "risk_score": 72.0,
                "primary_reason": "ML Pattern Detection",
            },
        }

        result = compose_structured_result(evidence, mock_policies, "test")

        # Should have 1 substantive finding (primary_reason excluded)
        assert len(result["findings"]) == 1, "Should have 1 substantive finding"
        # The finding should be the risk_factor, not primary_reason
        assert result["findings"][0]["claim"] == "Substantive risk factor"

        # Primary reason should be in risk_summary
        assert result["risk_summary"]["primary_reason"] == "ML Pattern Detection"

    def test_unknown_type_maps_to_unknown(self, mock_policies):
        """Test that unknown finding types map to unknown provenance."""
        evidence = {
            "case_id": "U00010",
            "evidence": [],
            "unified_findings": [
                {
                    "finding_id": "F-7",
                    "type": "unknown_type",
                    "description": "Unknown finding type",
                    "source_evidence_id": None,
                }
            ],
            "risk_summary": {"risk_level": "HIGH", "risk_score": 72.0},
        }

        result = compose_structured_result(evidence, mock_policies, "test")

        finding = result["findings"][0]
        assert finding["provenance"]["type"] == ProvenanceType.UNKNOWN.value


class TestFindingsCompleteness:
    """Tests for preservation of all Risk Platform findings."""

    def test_all_findings_preserved_from_real_response(self):
        """Test that substantive findings from real Risk Platform response are preserved."""
        # Use real U00010 evidence
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'test', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # U00010 now has 9 canonical findings that match the Risk Platform canonical contract:
        # 1. ML Pattern Detection Signal (ml_signal with canonical_name)
        # 2. New account with high activity (rule)
        # 3. High withdrawal frequency (rule)
        # 4. First withdrawal to new address (rule)
        # 5. Abnormal Withdrawal Behavior (feature_evidence with canonical_name)
        # 6. High Trading Frequency (feature_evidence with canonical_name)
        # 7. Shared Device Relationships (feature_evidence with canonical_name)
        # 8. Linked Account Network (feature_evidence with canonical_name)
        # 9. Opposite Trade Ratio (feature_evidence with canonical_name, below-threshold)
        # Filtered out: ml_signal with "score:", rule_signal with "score:", primary_reason
        assert len(result["findings"]) == 9

        # All findings should have provenance
        for finding in result["findings"]:
            assert "provenance" in finding
            assert "source" in finding["provenance"]
            assert "type" in finding["provenance"]
            assert finding["provenance"]["source"] == "risk_platform"

        # Verify detection_signals contain the aggregate scores
        detection_signals = result["risk_summary"].get("detection_signals", {})
        assert detection_signals["ml_score"] == 99.41
        assert detection_signals["rule_score"] == 85.0
        # graph_score is live RP data; assert shape only
        assert isinstance(detection_signals["graph_score"], (int, float))

    def test_first_withdrawal_finding_preserved(self):
        """Test that the 'First withdrawal to new address' finding is preserved."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'test', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Look for the first withdrawal finding
        first_withdrawal = [
            f for f in result["findings"]
            if "first withdrawal" in f["claim"].lower()
        ]

        assert len(first_withdrawal) == 1
        assert "first withdrawal" in first_withdrawal[0]["claim"].lower()
        assert first_withdrawal[0]["provenance"]["type"] == ProvenanceType.RULE_DERIVED.value

    def test_rule_derived_finding_no_evidence_ids_still_valid(self):
        """Test that rule-derived findings without evidence_ids are still valid findings."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'test', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Rule-derived findings should not require evidence_ids
        rule_findings = [
            f for f in result["findings"]
            if f["provenance"]["type"] == ProvenanceType.RULE_DERIVED.value
        ]

        assert len(rule_findings) > 0
        # These are valid findings even if evidence_ids is empty
        for finding in rule_findings:
            assert finding["provenance"]["source"] == "risk_platform"
            # evidence_ids may be empty for rule findings
            assert isinstance(finding["provenance"]["evidence_ids"], list)


class TestCitationValidationWithProvenance:
    """Tests for citation validation with provenance awareness."""

    @pytest.fixture
    def source_grounded_findings(self):
        """Create source-grounded findings from Risk Platform."""
        return [
            {
                "finding_id": "F-1",
                "claim": "ML-derived risk signal",
                "provenance": {
                    "source": "risk_platform",
                    "type": ProvenanceType.ML_DERIVED.value,
                    "source_finding_id": "F-1",
                    "evidence_ids": [],
                    "policy_ids": [],
                },
            },
            {
                "finding_id": "F-2",
                "claim": "Rule violation",
                "provenance": {
                    "source": "risk_platform",
                    "type": ProvenanceType.RULE_DERIVED.value,
                    "source_finding_id": "F-2",
                    "evidence_ids": [],
                    "policy_ids": ["AML-001"],
                },
            },
        ]

    @pytest.fixture
    def mock_policies_for_validation(self):
        """Mock policies for validation."""
        return {
            "query_used": "test",
            "top_k": 3,
            "results": [
                {"policy_id": "AML-001", "title": "AML", "snippet": "Test"},
            ]
        }

    def test_source_grounded_count_included(self, source_grounded_findings, mock_policies_for_validation):
        """Test that source_grounded_count is included in results."""
        result = citation_validate(source_grounded_findings, mock_policies_for_validation)

        assert "source_grounded_count" in result
        assert result["source_grounded_count"] == 2  # Both findings from Risk Platform

    def test_policy_supported_findings(self, source_grounded_findings, mock_policies_for_validation):
        """Test that policy-supported findings are counted separately."""
        result = citation_validate(source_grounded_findings, mock_policies_for_validation)

        # F-2 has AML-001 policy, should be policy-supported
        assert result["supported_count"] == 1
        # F-1 has no policy, but is still source-grounded
        assert result["source_grounded_count"] == 2

    def test_source_grounded_findings_not_marked_unsupported(self, source_grounded_findings, mock_policies_for_validation):
        """Test that source-grounded findings without policy support are not marked unsupported."""
        result = citation_validate(source_grounded_findings, mock_policies_for_validation)

        # F-1 has no policy support but is source-grounded, should not be in unsupported
        assert result["unsupported_count"] == 0
        assert len(result["unsupported_claims"]) == 0

    def test_fully_unsupported_claim(self, mock_policies_for_validation):
        """Test that claims without source grounding or policy support are marked unsupported."""
        unsupported_claim = [
            {
                "finding_id": "F-999",
                "claim": "Hallucinated finding",
                "provenance": {
                    "source": "llm_generated",
                    "type": ProvenanceType.UNKNOWN.value,
                    "source_finding_id": "F-999",
                    "evidence_ids": [],
                    "policy_ids": [],
                },
            },
        ]

        result = citation_validate(unsupported_claim, mock_policies_for_validation)

        assert result["unsupported_count"] == 1
        assert result["source_grounded_count"] == 0
        assert len(result["unsupported_claims"]) == 1


class TestRealU00010Case:
    """Integration tests with real U00010 CRITICAL case."""

    def test_u00010_findings_completeness(self):
        """Test that U00010 CRITICAL case produces all expected substantive findings."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Should have 9 substantive findings (Risk Platform canonical findings)
        # Filtered out: ml_signal with "score:", rule_signal with "score:", primary_reason
        assert len(result["findings"]) == 9

        # Check for key findings
        finding_claims = [f["claim"] for f in result["findings"]]

        # Network/connected accounts should be present
        assert any("connected accounts" in claim.lower() for claim in finding_claims)

        # Withdrawal pattern should be present
        assert any("withdrawal" in claim.lower() for claim in finding_claims)

        # Trading frequency should be present
        assert any("trades" in claim.lower() for claim in finding_claims)

        # Opposite Trade Ratio should be present (from feature_evidence)
        assert any("opposite-trade" in claim.lower() for claim in finding_claims)

        # Abnormal Withdrawal Behavior should be present (from feature_evidence)
        # Check for canonical_name since the claim wording was improved to be more business-readable
        canonical_names = [f.get("canonical_name", "") for f in result["findings"]]
        assert any("Abnormal Withdrawal Behavior" in name for name in canonical_names), \
            "Abnormal Withdrawal Behavior should be present"

        # ML Pattern Detection should be present (as canonical finding)
        assert any("ml pattern detection" in claim.lower() for claim in finding_claims)

    def test_u00010_all_findings_have_provenance(self):
        """Test that all U00010 findings have proper provenance."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'test', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        for finding in result["findings"]:
            assert "provenance" in finding
            provenance = finding["provenance"]

            assert "source" in provenance
            assert "type" in provenance
            assert "source_finding_id" in provenance
            assert "evidence_ids" in provenance
            assert "policy_ids" in provenance

            # All should be from Risk Platform
            assert provenance["source"] == "risk_platform"

            # Provenance type should be valid
            assert provenance["type"] in [
                ProvenanceType.DIRECT_EVIDENCE.value,
                ProvenanceType.RULE_DERIVED.value,
                ProvenanceType.ML_DERIVED.value,
                ProvenanceType.GRAPH_DERIVED.value,
                ProvenanceType.PRIMARY_REASON.value,
            ]

    def test_u00010_risk_summary_propagation(self):
        """Test that U00010 CRITICAL case preserves authoritative risk summary fields."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Verify all authoritative risk summary fields are preserved
        risk_summary = result["risk_summary"]

        assert risk_summary["case_id"] == "U00010"
        assert risk_summary["risk_level"] == "CRITICAL"
        # score is live RP data (changes when RP regenerates); shape only
        assert isinstance(risk_summary["risk_score"], (int, float))
        assert risk_summary["risk_score"] > 0
        assert risk_summary["primary_reason"] == "ML Pattern Detection"
        assert risk_summary["recommended_action"] == "Immediate Investigation"

    def test_u00010_detection_signals_extraction(self):
        """Test that detection signals are properly extracted from Risk Platform evidence."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Verify detection signals contain all three scores
        detection_signals = result["risk_summary"].get("detection_signals", {})

        assert detection_signals["ml_score"] == 99.41
        assert detection_signals["rule_score"] == 85.0
        # graph_score is live RP data; assert shape only
        assert isinstance(detection_signals["graph_score"], (int, float))

    def test_u00010_aggregate_ml_score_excluded_from_findings(self):
        """Test that aggregate ML score finding is excluded from source findings."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Findings should NOT contain aggregate ML score finding
        finding_claims = [f["claim"] for f in result["findings"]]

        # The aggregate "ML-derived risk signal (score: 99.41)" should NOT be present
        assert not any(
            "ML-derived risk signal" in claim and "score: 99.41" in claim
            for claim in finding_claims
        ), "Aggregate ML score finding should be excluded from source findings"

        # But detection_signals should contain ml_score
        assert result["risk_summary"]["detection_signals"]["ml_score"] == 99.41

    def test_u00010_aggregate_rule_score_excluded_from_findings(self):
        """Test that aggregate Rule score finding is excluded from source findings."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Findings should NOT contain aggregate Rule score finding
        finding_claims = [f["claim"] for f in result["findings"]]

        # The aggregate "Rule-based risk indicators (score: 85.00)" should NOT be present
        assert not any(
            "Rule-based risk indicators" in claim and "score: 85" in claim
            for claim in finding_claims
        ), "Aggregate Rule score finding should be excluded from source findings"

        # But detection_signals should contain rule_score
        assert result["risk_summary"]["detection_signals"]["rule_score"] == 85.0

    def test_u00010_primary_reason_excluded_from_findings(self):
        """Test that primary_reason is NOT duplicated as a source finding."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Findings should NOT contain primary_reason as a separate finding
        finding_claims = [f["claim"] for f in result["findings"]]

        # "ML Pattern Detection" should NOT be in findings (only in risk_summary.primary_reason)
        assert not any(
            claim == "ML Pattern Detection"
            for claim in finding_claims
        ), "Primary reason should not be duplicated as a source finding"

        # But it should be in risk_summary
        assert result["risk_summary"]["primary_reason"] == "ML Pattern Detection"

    def test_u00010_substantive_findings_remain(self):
        """Test that substantive findings remain after classification."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        finding_claims = [f["claim"] for f in result["findings"]]

        # These substantive findings should be present
        assert any("connected accounts" in claim.lower() for claim in finding_claims), \
            "Connected accounts finding should remain"
        assert any("trades in 24h" in claim.lower() for claim in finding_claims), \
            "Trading frequency finding should remain"
        assert any("withdrawal" in claim.lower() for claim in finding_claims), \
            "Withdrawal finding should remain"

    def test_u00010_network_relationship_finding_remains(self):
        """Test that canonical network findings are preserved as separate findings."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        finding_claims = [f["claim"] for f in result["findings"]]

        # "Network relationship detected" is NOT canonical - it should be REMOVED
        # The canonical network findings are "Shared Device Relationships" and "Linked Account Network"
        assert not any("Network relationship detected" in claim for claim in finding_claims), \
            "Network relationship detected should be removed (not canonical)"

        # Canonical network findings should be present
        assert any("shared device" in claim.lower() or "linked account" in claim.lower()
                   for claim in finding_claims), \
            "Canonical network findings should be present"

    def test_u00010_first_withdrawal_finding_remains(self):
        """Test that first withdrawal to new address finding remains."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        finding_claims = [f["claim"] for f in result["findings"]]

        # "First withdrawal to new address" should be present
        assert any("first withdrawal" in claim.lower() and "new address" in claim.lower()
                   for claim in finding_claims), \
            "First withdrawal to new address finding should remain"

    def test_u00010_source_findings_count(self):
        """Test that U00010 has correct source findings count after classification."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # U00010 should have 9 substantive findings (Risk Platform canonical findings)
        # Canonical findings: ML Pattern Detection Signal + 3 rule findings + 5 feature findings = 9
        # Feature findings: Shared Device Relationships, Linked Account Network, High Trading Frequency,
        #                  Abnormal Withdrawal Behavior, Opposite Trade Ratio (from feature_evidence)
        # Filtered out: aggregate score findings, primary_reason
        assert len(result["findings"]) == 9, \
            f"Expected 9 substantive findings, got {len(result['findings'])}"

    def test_risk_summary_preserves_primary_reason_and_recommended_action(self):
        """Test that compose_structured_result preserves primary_reason and recommended_action from evidence."""
        evidence = {
            "case_id": "U00123",
            "evidence": [],
            "unified_findings": [],
            "risk_summary": {
                "risk_level": "HIGH",
                "risk_score": 72.5,
                "primary_reason": "Elevated ML-derived risk signal",
                "recommended_action": "Investigate",
                "ml_score": 85.0,
                "rule_score": 70.0,
                "graph_score": 0.0,
            },
        }

        policies = {"query_used": "test", "top_k": 3, "results": []}

        result = compose_structured_result(evidence, policies, "test")

        # Verify primary_reason and recommended_action are preserved
        assert result["risk_summary"]["primary_reason"] == "Elevated ML-derived risk signal"
        assert result["risk_summary"]["recommended_action"] == "Investigate"

    def test_u00010_citation_validation_with_provenance(self):
        """Test citation validation for U00010 with provenance awareness."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        compose_result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        citation_result = citation_validate(
            compose_result["findings"], policies
        )

        # All substantive findings are from Risk Platform, so should be source-grounded
        assert citation_result["source_grounded_count"] == 9

        # Some may have policy support
        assert citation_result["supported_count"] >= 0

        # Rule-derived findings without policy support should not be marked unsupported
        assert citation_result["unsupported_count"] == 0

    def test_u00010_ml_pattern_detection_preserved(self):
        """Test that ML Pattern Detection is preserved as a canonical finding."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'ml pattern', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        finding_claims = [f["claim"] for f in result["findings"]]
        canonical_names = [f.get("canonical_name", "") for f in result["findings"]]

        # ML Pattern Detection should be present as a canonical finding
        assert any("ml pattern detection" in claim.lower() for claim in finding_claims), \
            "ML Pattern Detection finding should be present"
        assert any("ML Pattern Detection Signal" in name for name in canonical_names), \
            "ML Pattern Detection Signal canonical name should be present"

    def test_u00010_ml_score_not_duplicated_as_finding(self):
        """Test that ML score remains only a Detection Signal, not a duplicated finding."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'test', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        detection_signals = result.get("risk_summary", {}).get("detection_signals", {})

        # ML score should be in detection_signals
        assert "ml_score" in detection_signals
        assert detection_signals["ml_score"] == 99.41

        # ML score should NOT be duplicated as a separate finding
        finding_claims = [f["claim"] for f in result["findings"]]
        ml_score_findings = [f for f in finding_claims if "ml score:" in f.lower()]
        assert len(ml_score_findings) == 0, "ML score should not be duplicated as a finding"

    def test_u00010_abnormal_withdrawal_behavior_preserved(self):
        """Test that Abnormal Withdrawal Behavior is preserved from feature_evidence."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'withdrawal', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        canonical_names = [f.get("canonical_name", "") for f in result["findings"]]

        # Abnormal Withdrawal Behavior should be present
        assert any("Abnormal Withdrawal Behavior" in name for name in canonical_names), \
            "Abnormal Withdrawal Behavior canonical name should be present"

        # The claim should now use improved business-readable wording
        awb_finding = [f for f in result["findings"] if f.get("canonical_name") == "Abnormal Withdrawal Behavior"]
        assert len(awb_finding) == 1
        claim = awb_finding[0].get("claim", "")
        # Should contain percentage and business-readable language
        assert "100.00%" in claim or "withdrawals were sent" in claim.lower(), \
            f"AWB claim should use business-readable wording, got: {claim}"

    def test_u00010_opposite_trade_ratio_preserved_with_below_threshold(self):
        """Test that Opposite Trade Ratio is preserved with below-threshold semantics."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'trade', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        finding_claims = [f["claim"] for f in result["findings"]]
        canonical_names = [f.get("canonical_name", "") for f in result["findings"]]

        # Opposite Trade Ratio should be present
        assert any("opposite-trade" in claim.lower() for claim in finding_claims), \
            "Opposite Trade Ratio should be present"
        assert any("Opposite Trade Ratio" in name for name in canonical_names), \
            "Opposite Trade Ratio canonical name should be present"

        # Should show below-threshold semantics (34.38% < 40%)
        opposite_trade_finding = [f for f in result["findings"]
                                  if "opposite-trade" in f["claim"].lower()]
        assert len(opposite_trade_finding) == 1
        assert "below the 40% threshold" in opposite_trade_finding[0]["claim"], \
            "Opposite Trade Ratio should show below-threshold semantics"

    def test_u00010_canonical_finding_names_preserved(self):
        """Test that canonical finding names are preserved rather than replaced with descriptions."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00010'})
        policies = execute_tool('policy_search', {'query': 'test', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00010"
        )

        # Expected canonical finding names from Risk Platform
        expected_canonical_names = [
            "ML Pattern Detection Signal",
            "New account with high activity",
            "High withdrawal frequency",
            "First withdrawal to new address",
            "Abnormal Withdrawal Behavior",
            "High Trading Frequency",
            "Shared Device Relationships",
            "Linked Account Network",
            "Opposite Trade Ratio"
        ]

        canonical_names_in_result = [f.get("canonical_name", "") for f in result["findings"]]

        # All canonical names should be preserved
        for expected_name in expected_canonical_names:
            assert expected_name in canonical_names_in_result, \
                f"Canonical finding name '{expected_name}' should be preserved"


class TestRealU00299Case:
    """Integration tests with real U00299 CRITICAL case."""

    def test_u00299_findings_completeness(self):
        """Test that U00299 CRITICAL case produces all expected canonical findings."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00299'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00299"
        )

        # U00299 should have exactly 5 canonical findings
        assert len(result["findings"]) == 5

        # Check for key findings
        finding_claims = [f["claim"] for f in result["findings"]]
        canonical_names = [f.get("canonical_name", "") for f in result["findings"]]

        # ML Pattern Detection should be present
        assert any("ml pattern detection" in claim.lower() for claim in finding_claims)

        # Coordinated Trading Pattern should be present
        assert any("Coordinated Trading Pattern" in name for name in canonical_names)

        # High withdrawal frequency should be present
        canonical_names = [f.get("canonical_name", "") for f in result["findings"]]
        assert any("High withdrawal frequency" in name for name in canonical_names)

        # First withdrawal to new address should be present
        assert any("First withdrawal to new address" in name for name in canonical_names)

        # Abnormal Withdrawal Behavior should be present
        assert any("Abnormal Withdrawal Behavior" in name for name in canonical_names)

    def test_u00299_coordinated_trading_pattern_no_duplicates(self):
        """Test that Coordinated Trading Pattern appears only once (not duplicated from multiple sources)."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00299'})
        policies = execute_tool('policy_search', {'query': 'trading', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00299"
        )

        canonical_names = [f.get("canonical_name", "") for f in result["findings"]]

        # Coordinated Trading Pattern should appear exactly once
        ctp_count = sum(1 for name in canonical_names if name == "Coordinated Trading Pattern")
        assert ctp_count == 1, \
            f"Coordinated Trading Pattern should appear exactly once, appeared {ctp_count} times"

    def test_u00299_coordinated_trading_threshold_triggered(self):
        """Test that Coordinated Trading Pattern shows above-threshold semantics (45.24% > 40%)."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00299'})
        policies = execute_tool('policy_search', {'query': 'trading', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00299"
        )

        # Find the Coordinated Trading Pattern finding
        ctp_finding = [f for f in result["findings"] if f.get("canonical_name") == "Coordinated Trading Pattern"]
        assert len(ctp_finding) == 1

        claim = ctp_finding[0].get("claim", "")
        # Should show above-threshold semantics
        assert "45.24%" in claim or "exceeded" in claim.lower(), \
            f"Coordinated Trading Pattern should show above-threshold semantics, got: {claim}"
        assert "40%" in claim or "threshold" in claim.lower(), \
            f"Coordinated Trading Pattern should mention the 40% threshold, got: {claim}"

    def test_u00299_abnormal_withdrawal_business_readable(self):
        """Test that Abnormal Withdrawal Behavior uses business-readable wording."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00299'})
        policies = execute_tool('policy_search', {'query': 'withdrawal', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00299"
        )

        # Find the Abnormal Withdrawal Behavior finding
        awb_finding = [f for f in result["findings"] if f.get("canonical_name") == "Abnormal Withdrawal Behavior"]
        assert len(awb_finding) == 1

        claim = awb_finding[0].get("claim", "")
        # Should use business-readable language, not generic phrases
        assert "21.43%" in claim or "withdrawals were sent" in claim.lower(), \
            f"AWB should use business-readable wording with percentage, got: {claim}"
        # Should NOT use generic fallback phrase
        assert "elevated risk indicator" not in claim.lower(), \
            f"AWB should not use generic 'elevated risk indicator' phrase, got: {claim}"

    def test_u00299_all_findings_have_canonical_names(self):
        """Test that all U00299 findings have canonical names preserved."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00299'})
        policies = execute_tool('policy_search', {'query': 'test', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00299"
        )

        expected_canonical_names = [
            "ML Pattern Detection Signal",
            "Coordinated Trading Pattern",
            "High withdrawal frequency",
            "First withdrawal to new address",
            "Abnormal Withdrawal Behavior"
        ]

        canonical_names_in_result = [f.get("canonical_name", "") for f in result["findings"]]

        # All canonical names should be preserved
        for expected_name in expected_canonical_names:
            assert expected_name in canonical_names_in_result, \
                f"Canonical finding name '{expected_name}' should be preserved"


class TestRealU00047Case:
    """Integration tests with real U00047 CRITICAL case."""

    def test_u00047_findings_completeness(self):
        """Test that U00047 CRITICAL case produces all expected findings."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00047'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00047"
        )

        # Should preserve all findings from Risk Platform
        assert len(result["findings"]) >= 1

        # All should have provenance
        for finding in result["findings"]:
            assert "provenance" in finding
            assert finding["provenance"]["source"] == "risk_platform"

    def test_u00047_detection_signals_present(self):
        """Test that U00047 has detection signals in risk summary."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00047'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00047"
        )

        # Should have detection_signals
        detection_signals = result["risk_summary"].get("detection_signals", {})
        assert len(detection_signals) > 0, "U00047 should have detection signals"

        # At least one score should be present
        assert any(
            detection_signals.get(key) is not None
            for key in ["ml_score", "rule_score", "graph_score"]
        ), "At least one detection signal score should be present"

    def test_u00047_source_findings_count(self):
        """Test that U00047 has correct source findings count after classification."""
        evidence = execute_tool('evidence_fetch', {'case_id': 'U00047'})
        policies = execute_tool('policy_search', {'query': 'critical risk', 'top_k': 3})

        result = compose_structured_result(
            evidence, policies, "Investigate case U00047"
        )

        # U00047 should have substantive findings after classification
        # Count will vary based on actual Risk Platform response
        assert len(result["findings"]) >= 1, "U00047 should have at least one substantive finding"


class TestLegacyBackwardsCompatibility:
    """Tests for backwards compatibility with legacy flat finding structure."""

    def test_citation_validate_handles_legacy_flat_structure(self):
        """Test that citation_validate handles legacy flat claim structure."""
        legacy_claims = [
            {
                "finding_id": "F-1",
                "claim": "Test claim",
                "evidence_ids": [],
                "policy_ids": ["AML-001"],
            },
        ]

        policies = {
            "query_used": "test",
            "top_k": 3,
            "results": [
                {"policy_id": "AML-001", "title": "AML", "snippet": "Test"},
            ]
        }

        result = citation_validate(legacy_claims, policies)

        # Should still work with legacy structure
        assert "supported_count" in result
        assert "source_grounded_count" in result

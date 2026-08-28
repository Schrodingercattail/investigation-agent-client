"""Tests for the risk_case_fetch domain tool (app/domain_tools.py).

Mocked at the adapter boundary (no real Risk Platform, no real HTTP).
Covers ToolResult outcome semantics, deterministic IDs end-to-end,
empty-case vs integration-failure distinction, and no-fabrication.
"""

from unittest.mock import patch

import pytest

from app.adapters.risk_platform import RiskPlatformError
from app.domain_tools import risk_case_fetch
from app.models import Finding, FindingCapability, ToolResultOutcome

from tests.test_risk_platform_adapter_v2 import (
    rp_evidence_payload,
    rp_explanation_payload,
)


def patch_adapter(fetch_return=None, fetch_raises=None):
    """Patch RiskPlatformAdapter.fetch_case used inside the tool module."""
    def fake_fetch(self, user_id):
        if fetch_raises is not None:
            raise fetch_raises
        return fetch_return
    return patch(
        "app.domain_tools.RiskPlatformAdapter.fetch_case",
        new=fake_fetch,
    )


def ok_payloads(case_id="U00299"):
    return rp_evidence_payload(case_id), rp_explanation_payload()


# --- 1/2. success + deterministic IDs ------------------------------------------

class TestSuccessAndDeterminism:
    def test_valid_case_fetch_returns_success_with_payload(self):
        with patch_adapter(fetch_return=ok_payloads()):
            result = risk_case_fetch("U00299")
        assert result.outcome == ToolResultOutcome.SUCCESS
        d = result.data
        assert d["case_id"] == "U00299"
        assert len(d["findings"]) >= 1
        assert result.evidence_refs      # case-level evidence mirrored
        assert result.citation_refs

    def test_finding_ids_deterministic_end_to_end(self):
        with patch_adapter(fetch_return=ok_payloads()):
            r1 = risk_case_fetch("U00299")
            r2 = risk_case_fetch("U00299")
        ids1 = [f.finding_id for f in r1.data["findings"]]
        ids2 = [f.finding_id for f in r2.data["findings"]]
        assert ids1 == ids2
        assert ids1[0] == "F1"

    def test_no_llm_in_the_loop_for_structure(self):
        # The whole normalization is pure functions over RP payloads — mock
        # twice and compare full serialized findings.
        with patch_adapter(fetch_return=ok_payloads()) as _:
            a = risk_case_fetch("U00299")
            b = risk_case_fetch("U00299")
        fa = [f.model_dump() for f in a.data["findings"]]
        fb = [f.model_dump() for f in b.data["findings"]]
        assert fa == fb


# --- 3/4. Finding normalization + capabilities ----------------------------------

class TestNormalizationViaTool:
    def test_findings_conform_to_domain_model(self):
        with patch_adapter(fetch_return=ok_payloads()):
            result = risk_case_fetch("U00299")
        for finding in result.data["findings"]:
            assert isinstance(finding, Finding)
            assert isinstance(finding.capabilities, FindingCapability)

    def test_a_finding_with_timeline_capability(self):
        with patch_adapter(fetch_return=ok_payloads()):
            result = risk_case_fetch("U00299")
        # multiple timestamped tx + withdrawal evidence → timeline granted
        timed = [f for f in result.data["findings"]
                 if f.capabilities.supports("timeline")]
        assert timed

    def test_a_finding_without_opposite_trades_capability(self):
        with patch_adapter(fetch_return=ok_payloads()):
            result = risk_case_fetch("U00299")
        ml_finding = next(
            f for f in result.data["findings"]
            if f.title.startswith("ML Pattern Detection")
        )
        assert not ml_finding.capabilities.supports("opposite_trades")


# --- 7. empty case ---------------------------------------------------------------

class TestEmptyCase:
    def test_successful_empty_case_is_empty_not_error(self):
        evidence = rp_evidence_payload(
            transaction_evidence=[],
            withdrawal_evidence=[],
            feature_evidence={},
            rule_evidence=[],
            network_evidence=None,
            risk_summary={
                "risk_level": "UNKNOWN", "risk_score": 0,
                "primary_reason": None, "recommended_action": None,
                "detection_methods": [], "detected_at": None,
                "ml_score": None, "rule_score": None, "graph_score": None,
            },
        )
        explanation = rp_explanation_payload()
        explanation["key_findings"] = []
        explanation["citations"] = []
        with patch_adapter(fetch_return=(evidence, explanation)):
            result = risk_case_fetch("U99999")
        assert result.outcome == ToolResultOutcome.EMPTY
        assert result.data == {"case_id": "U99999"}
        assert result.error is None          # not a failure — just nothing there

    def test_empty_case_not_masked_as_findings(self):
        evidence = rp_evidence_payload(
            transaction_evidence=[], withdrawal_evidence=[],
            rule_evidence=[], feature_evidence={},
            risk_summary={"risk_level": "UNKNOWN", "risk_score": 0,
                          "detected_at": None},
        )
        explanation = {"summary": "", "key_findings": [], "citations": [],
                       "recommended_action": "", "explanation_source": "",
                       "missing_info": []}
        with patch_adapter(fetch_return=(evidence, explanation)):
            result = risk_case_fetch("NOPE")
        assert result.outcome != ToolResultOutcome.SUCCESS


# --- 8–9. integration failures -----------------------------------------------------

class TestIntegrationFailures:
    @pytest.mark.parametrize(
        "err_kind",
        ["unavailable", "auth", "http", "malformed"],
    )
    def test_rp_failures_become_integration_error(self, err_kind):
        with patch_adapter(
            fetch_raises=RiskPlatformError(err_kind, f"boom: {err_kind}", 500),
        ):
            result = risk_case_fetch("U00299")
        assert result.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert result.error.code.startswith("RISK_PLATFORM_")

    def test_malformed_upstream_normalized_shape_not_swallowed(self):
        # Adapter raises on structurally-broken payloads; tool maps to
        # integration error WITHOUT pretending the case has no data.
        with patch_adapter(
            fetch_raises=RiskPlatformError(
                "malformed", "Evidence response missing required sections",
            ),
        ):
            result = risk_case_fetch("U00299")
        assert result.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert result.error.code == "RISK_PLATFORM_MALFORMED_RESPONSE"
        assert result.outcome != ToolResultOutcome.EMPTY

    def test_unreachable_platform_message_preserved(self):
        with patch_adapter(
            fetch_raises=RiskPlatformError(
                "unavailable", "Risk Platform timed out after 30s",
            ),
        ):
            result = risk_case_fetch("U00299")
        assert result.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert "timed out" in result.error.message


# --- 10. validation errors ----------------------------------------------------------

class TestValidationErrors:
    @pytest.mark.parametrize("bad_input", ["", "   ", None, 42])
    def test_invalid_case_id_is_validation_error(self, bad_input):
        result = risk_case_fetch(bad_input)
        assert result.outcome == ToolResultOutcome.VALIDATION_ERROR
        assert result.error.code == "INVALID_ARGUMENT"


# --- 11. no fabrication through the boundary -------------------------------------------

class TestNoFabrication:
    def test_citationless_explanation_leaves_policy_refs_empty(self):
        explanation = rp_explanation_payload()
        explanation["citations"] = []
        with patch_adapter(fetch_return=(rp_evidence_payload(), explanation)):
            result = risk_case_fetch("U00299")
        assert result.citation_refs == []
        assert all(not f.policy_refs for f in result.data["findings"])

    def test_signal_refs_mirror_only_reported_signals(self):
        evidence = rp_evidence_payload(
            rule_evidence=[],       # no rules fired
        )
        evidence["risk_summary"]["graph_score"] = None
        with patch_adapter(fetch_return=(evidence, rp_explanation_payload())):
            result = risk_case_fetch("U00299")
        sig_names = {s.name for s in result.data["signal_refs"]}
        assert all(name not in sig_names
                   for name in ("Coordinated Trading Pattern",
                                "High Withdrawal Frequency"))

    def test_whitespace_case_id_stripped_but_nonempty_required(self):
        with patch_adapter(fetch_return=ok_payloads()):
            result = risk_case_fetch("  U00299  ")
        assert result.outcome == ToolResultOutcome.SUCCESS
        assert result.data["case_id"] == "U00299"

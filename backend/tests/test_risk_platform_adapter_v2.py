"""Tests for the Risk Platform adapter normalization (app/adapters/risk_platform.py).

All RP payloads are mocked / shaped after the actual Risk Platform schemas
(RiskEvidenceResponse, ExplanationResponse) discovered in the RP codebase.
No real Risk Platform required.
"""

import pytest

from app.adapters.risk_platform import (
    RiskPlatformError,
    derive_capabilities,
    normalize_case,
)
from app.models import Finding


# --- fixtures shaped after real RP responses ------------------------------------

def rp_evidence_payload(case_id="U00299", **overrides):
    payload = {
        "user_id": case_id,
        "risk_summary": {
            "risk_level": "CRITICAL",
            "risk_score": 85.5,
            "primary_reason": "Coordinated Trading Pattern",
            "recommended_action": "Manual Review",
            "detection_methods": ["ML", "Rule"],
            "detected_at": "2026-08-20T10:00:00Z",
            "ml_score": 96.24,
            "rule_score": 80.0,
            "graph_score": 18.0,
        },
        "transaction_evidence": [
            {
                "transaction_id": f"TX{i:05d}", "symbol": "BTC",
                "side": "sell" if i % 2 else "buy",
                "price": 45000.0 + i, "quantity": 0.5,
                "value": 22500.0, "timestamp": f"2026-08-19T1{i}:00:00Z",
                "risk_reason": "burst pattern",
            }
            for i in range(3)
        ],
        "withdrawal_evidence": [
            {
                "withdrawal_id": f"WD{i:05d}", "asset": "BTC",
                "amount": 0.5 + i * 0.1,
                "address": f"0xaddr{i}",
                "is_new_address": i < 2,
                "timestamp": f"2026-08-19T1{i}:30:00Z",
                "risk_reason": "new address" if i < 2 else "velocity",
            }
            for i in range(4)
        ],
        "network_evidence": None,
        "risk_factor_evidence": [],
        "feature_evidence": {
            "trade_frequency_24h": 28,
            "opposite_trade_ratio": 0.4524,
            "withdrawal_risk_score": 0.2143,
        },
        "rule_evidence": [
            {
                "rule_name": "Coordinated Trading Pattern",
                "severity": "HIGH",
                "description": "Opposite-trade ratio exceeded 40% threshold",
            },
            {
                "rule_name": "High Withdrawal Frequency",
                "severity": "MEDIUM",
                "description": "14 withdrawals in 24h period",
            },
        ],
    }
    payload.update(overrides)
    return payload


def rp_explanation_payload():
    return {
        "summary": "High risk coordinated trading pattern detected",
        "key_findings": [
            "1. ML Pattern Detection — 96.24/100; a system signal",
            "2. Coordinated Trading Pattern [1]",
            "3. High Withdrawal Frequency [2]",
        ],
        "recommended_action": "Manual Review",
        "citations": [
            {
                "id": 1, "doc": "AML_Suspicious_Indicators.md",
                "section": "2.1 High-Velocity Transfers",
                "quote": "A sudden spike in transfers...", "chunk_id": "AML#2.1#001",
            },
            {
                "id": 2, "doc": "AML_Suspicious_Indicators.md",
                "section": "2.2 Rapid Fund Movement",
                "quote": "Multiple withdrawals in rapid succession...",
                "chunk_id": "AML#2.2#004",
            },
        ],
        "explanation_source": "LLM",
        "llm_error": None,
        "missing_info": [],
    }


# --- deterministic IDs -------------------------------------------------------------

class TestDeterministicFindingIDs:
    def test_ids_are_deterministic_across_runs(self):
        a = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        b = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        assert [f.finding_id for f in a["findings"]] == \
               [f.finding_id for f in b["findings"]]

    def test_ids_follow_explanation_ordering_F1_Fn(self):
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        ids = [f.finding_id for f in result["findings"]]
        # explanation key_findings are the primary ordering source
        assert ids[:3] == ["F1", "F2", "F3"]
        assert all(i.startswith("F") for i in ids)

    def test_no_llm_needed_for_ids(self):
        # Determinism holds independent of any generation process — same
        # inputs produce byte-identical ID sequences (covered above); this
        # test asserts contiguity too.
        result = normalize_case(
            case_id="X1",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        ids = [f.finding_id for f in result["findings"]]
        assert ids == [f"F{i+1}" for i in range(len(ids))]


# --- Finding model normalization ---------------------------------------------------

class TestFindingNormalization:
    def test_findings_conform_to_domain_model(self):
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        assert len(result["findings"]) == 3
        for finding in result["findings"]:
            assert isinstance(finding, Finding)

    def test_titles_and_summaries_preserved_from_rp_text(self):
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        f2 = result["findings"][1]
        assert f2.title == "Coordinated Trading Pattern"
        assert "[1]" not in f2.title           # markers stripped from name
        assert f2.summary                      # non-empty summary kept

    def test_policy_refs_mirror_citations_only_when_marked(self):
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        findings_by_title = {f.title: f for f in result["findings"]}
        cited = findings_by_title["Coordinated Trading Pattern"]
        uncited = findings_by_title["ML Pattern Detection"]
        assert cited.policy_refs                            # [1] marker → cited
        assert not uncited.policy_refs                       # score signal → uncited
        assert all(p.chunk_id == "AML#2.1#001" or p.citation_id == 1
                   for p in cited.policy_refs)

    def test_unmarked_findings_never_inherit_case_citations(self):
        """Regression (U00010): a finding whose authoritative text carries NO
        [n] marker must receive zero policy_refs — even when the explanation
        carries citations elsewhere and even when the finding text shares
        words with citation doc/section titles (the old token-overlap
        heuristic wrongly copied ALL case citations onto unmarked findings)."""
        explanation = rp_explanation_payload()
        # Findings with no markers; one mentions doc-like words deliberately.
        explanation["key_findings"] = [
            "1. New account with high activity.",
            "2. Opposite trade ratio.",
            "3. Coordinated Trading Pattern [1]",
        ]
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=explanation,
        )
        by_title = {f.title: f for f in result["findings"]}
        assert not by_title["New account with high activity."].policy_refs
        assert not by_title["Opposite trade ratio."].policy_refs
        # marked finding still mirrors exactly its marker's citation
        marked = by_title["Coordinated Trading Pattern"]
        assert [p.citation_id for p in marked.policy_refs] == [1]
        # case-level set stays complete and untouched
        assert [p.citation_id for p in result["policy_refs"]] == [1, 2]

    def test_multiple_markers_mirror_in_marker_order(self):
        explanation = rp_explanation_payload()
        explanation["key_findings"] = [
            "1. Mixed Evidence Finding [2] and [1]",
        ]
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=explanation,
        )
        f = result["findings"][0]
        # both marked citations mirrored, in first-appearance order, deduped
        assert [p.citation_id for p in f.policy_refs] == [2, 1]

    def test_rule_only_finding_appended_after_canonical(self):
        explanation = rp_explanation_payload()
        explanation["key_findings"] = []       # explanation says nothing
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=explanation,
        )
        names = [f.title for f in result["findings"]]
        assert "Coordinated Trading Pattern" in names          # from rule_evidence
        assert "High Withdrawal Frequency" in names

    def test_rule_already_in_explanation_is_not_duplicated(self):
        """Regression (real U00010): RP renders explanation finding names
        with sentence punctuation ('High withdrawal frequency.') while
        rule_evidence.rule_name has none ('High withdrawal frequency').
        Exact-string comparison duplicated every explained rule as an extra
        finding (12 instead of 9). Punctuation/case-insensitive identity
        must prevent that — the same authoritative finding stays one."""
        explanation = rp_explanation_payload()
        # RP's real shape: explanation name with '.', rule_name without.
        explanation["key_findings"] = [
            "1. Coordinated Trading Pattern [1]",
            "2. High Withdrawal Frequency.",
        ]
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=explanation,
        )
        titles = [f.title for f in result["findings"]]
        assert titles == [
            "Coordinated Trading Pattern", "High Withdrawal Frequency.",
        ]  # rule_evidence adds NO third finding
        assert len(result["findings"]) == 2

    def test_distinct_rule_findings_both_preserved(self):
        """A rule NOT covered by the explanation is still a real finding —
        normalization never merges genuinely distinct RP findings."""
        explanation = rp_explanation_payload()
        explanation["key_findings"] = [
            "1. Coordinated Trading Pattern [1]",
        ]
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=explanation,
        )
        titles = [f.title for f in result["findings"]]
        assert titles == ["Coordinated Trading Pattern",
                          "High Withdrawal Frequency"]  # distinct, appended


# --- capability derivation -----------------------------------------------------------

class TestCapabilityDerivation:
    def test_shared_device_finding_gets_no_opposite_trades(self):
        caps = derive_capabilities(
            finding_name="Shared Device Relationships",
            transaction_evidence_count=2,
            withdrawal_evidence_count=0,
            opposite_trade_ratio=0.4524,     # ratio exists at CASE level...
            has_rule_trigger=False,
        )
        assert "timeline" in caps
        assert "signal_explain" in caps
        # policy retrieval is case-wide — never finding-derived
        assert "policy_lookup" not in caps
        assert "opposite_trades" not in caps   # ...but this finding isn't about it

    def test_coordinated_trading_finding_gets_opposite_trades(self):
        caps = derive_capabilities(
            finding_name="Coordinated Trading Pattern",
            transaction_evidence_count=2,
            withdrawal_evidence_count=1,
            opposite_trade_ratio=0.4524,
            has_rule_trigger=True,
        )
        assert caps.supports("opposite_trades")
        assert caps.supports("timeline")
        assert caps.supports("signal_explain")

    def test_no_timeline_with_single_event(self):
        caps = derive_capabilities(
            finding_name="High withdrawal frequency",
            transaction_evidence_count=1,
            withdrawal_evidence_count=0,
            opposite_trade_ratio=None,
            has_rule_trigger=True,
        )
        assert not caps.supports("timeline")

    def test_capability_derivation_is_deterministic(self):
        kwargs = dict(
            finding_name="Coordinated Trading Pattern",
            transaction_evidence_count=2,
            withdrawal_evidence_count=2,
            opposite_trade_ratio=0.5,
            has_rule_trigger=True,
        )
        a = derive_capabilities(**kwargs)
        b = derive_capabilities(**kwargs)
        assert a.model_dump() == b.model_dump()

    def test_derived_end_to_end_through_normalize(self):
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=rp_explanation_payload(),
        )
        by_title = {f.title: f for f in result["findings"]}
        coordinated = by_title["Coordinated Trading Pattern"]
        ml = by_title["ML Pattern Detection"]
        # trade-composition data exists and this is the opposite-trade finding
        assert coordinated.capabilities.supports("opposite_trades")
        # ML detector-signal finding carries no fabricated composition access
        assert not ml.capabilities.supports("opposite_trades")


# --- no fabrication ------------------------------------------------------------------

class TestNoFabrication:
    def test_empty_upstream_produces_refless_finding_without_invented_data(self):
        evidence = rp_evidence_payload(
            transaction_evidence=[],
            withdrawal_evidence=[],
            feature_evidence={},
            rule_evidence=[],
            network_evidence=None,
            risk_summary={"risk_level": "HIGH", "risk_score": 60.0,
                          "ml_score": 55.0, "rule_score": None,
                          "graph_score": None, "detected_at": "2026-08-20T10:00:00Z"},
        )
        explanation = rp_explanation_payload()
        explanation["citations"] = []
        result = normalize_case(
            case_id="U00299", evidence=evidence, explanation=explanation,
        )
        assert result["evidence_refs"] == []
        assert all(not f.evidence_refs for f in result["findings"])
        case_signal_names = {s.name for s in result["signal_refs"]}
        assert "ml_score" in case_signal_names         # RP reported it
        assert not any(n.startswith(("tx_", "wd_"))
                       for n in case_signal_names)      # nothing invented

    def test_policy_refs_absent_when_no_citations_returned(self):
        explanation = rp_explanation_payload()
        explanation["citations"] = []
        result = normalize_case(
            case_id="U00299",
            evidence=rp_evidence_payload(),
            explanation=explanation,
        )
        assert result["policy_refs"] == []
        assert all(not f.policy_refs for f in result["findings"])

    def test_malformed_evidence_raises_structured_error(self):
        with pytest.raises(RiskPlatformError) as exc_info:
            normalize_case(
                case_id="U00299",
                evidence={"unexpected_shape": True},   # missing user_id/risk_summary
                explanation=rp_explanation_payload(),
            )
        assert exc_info.value.kind == "malformed"

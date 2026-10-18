"""GSP WP-GSP-1 — Capability Declaration Foundation tests.

L1 deterministic coverage for the frozen evidence vocabulary and the
authoritative StepToolBinding declarations:

1. vocabulary is closed and contains no claim/assessment types
2. every executable binding declares (possibly empty) evidence types
3. declared values are legal vocabulary members
4. the exact per-binding declaration table is pinned
5. shared_ip_evidence is declared by NO binding in Slice 1
6. declaration↔payload consistency for the main bindings (real domain
   tools, fixture-backed adapters — deterministic)
7. WP-GSP-1b: shared_ip_attributions is a pure pass-through echo
   (non-empty and empty upstream both echo verbatim)
"""

import pytest

from app.domain_tools.cross_account_trade_analysis import (
    cross_account_trade_analysis,
)
from app.domain_tools.related_account_analysis import related_account_analysis
from app.domain_tools.risk_case_fetch import risk_case_fetch
from app.skills import (
    EVIDENCE_TYPES,
    EvidenceType,
    SkillDefinition,
    STEP_TOOL_MAP,
)
from tests.test_api_investigations_v1 import rp_patches
import tests.test_risk_platform_adapter_v2 as fx

from unittest.mock import patch


# --- 1. vocabulary -------------------------------------------------------------

def test_evidence_vocabulary_is_closed_and_exact():
    assert EVIDENCE_TYPES == {
        "case_summary", "network_relations", "shared_device_evidence",
        "shared_ip_evidence", "opposite_trade_relations",
        "withdrawal_records", "transaction_records",
        "ml_signal_explanation", "rule_explanation",
        "graph_cluster_facts", "policy_references",
    }


def test_no_claim_or_assessment_types_in_vocabulary():
    """Claims (money_laundering_assessment etc.) are user-side judgments —
    they must never enter the evidence vocabulary (design §4.1)."""
    for value in EVIDENCE_TYPES:
        assert "assessment" not in value
        assert "money_laundering" not in value
        assert "fraud" not in value


# --- 2/3. declarations exist and are legal -------------------------------------

def test_every_binding_declares_evidence_types():
    for step_type, binding in STEP_TOOL_MAP.items():
        assert isinstance(binding.supported_evidence_types, list), step_type


def test_declared_values_are_legal_vocabulary_members():
    for step_type, binding in STEP_TOOL_MAP.items():
        for et in binding.supported_evidence_types:
            assert et in EvidenceType, (step_type, et)
            assert et.value in EVIDENCE_TYPES, (step_type, et)


# --- 4. exact declaration table -------------------------------------------------

def test_declaration_table_pinned():
    expected = {
        "fetch_case": [EvidenceType.CASE_SUMMARY],
        "generate_artifact": [EvidenceType.CASE_SUMMARY],
        "inspect_timeline": [],
        "inspect_evidence": [EvidenceType.WITHDRAWAL_RECORDS,
                             EvidenceType.TRANSACTION_RECORDS],
        "inspect_withdrawals": [EvidenceType.WITHDRAWAL_RECORDS],
        "inspect_transactions": [EvidenceType.TRANSACTION_RECORDS],
        "inspect_self_opposite_trades": [EvidenceType.TRANSACTION_RECORDS],
        "analyze_cross_account_trades": [
            EvidenceType.OPPOSITE_TRADE_RELATIONS],
        "analyze_related_accounts": [EvidenceType.NETWORK_RELATIONS,
                                     EvidenceType.SHARED_DEVICE_EVIDENCE],
        "explain_signal": [EvidenceType.ML_SIGNAL_EXPLANATION,
                           EvidenceType.RULE_EXPLANATION,
                           EvidenceType.GRAPH_CLUSTER_FACTS],
        "retrieve_policy": [EvidenceType.POLICY_REFERENCES],
    }
    assert set(STEP_TOOL_MAP.keys()) == set(expected.keys())
    for step_type, declared in expected.items():
        assert STEP_TOOL_MAP[step_type].supported_evidence_types == declared, \
            step_type


# --- 5. no nearest-capability substitution encoded in the registry --------------

def test_shared_ip_declared_by_no_binding_in_slice1():
    for step_type, binding in STEP_TOOL_MAP.items():
        assert EvidenceType.SHARED_IP_EVIDENCE not in \
            binding.supported_evidence_types, step_type


def test_shared_ip_is_distinct_from_shared_device():
    assert EvidenceType.SHARED_IP_EVIDENCE != \
        EvidenceType.SHARED_DEVICE_EVIDENCE


def test_related_accounts_does_not_claim_laundering_or_fund_flow():
    declared = STEP_TOOL_MAP["analyze_related_accounts"] \
        .supported_evidence_types
    assert declared == [EvidenceType.NETWORK_RELATIONS,
                        EvidenceType.SHARED_DEVICE_EVIDENCE]


def test_skill_prose_is_not_the_declaration_source():
    """Authoritative declarations live on StepToolBinding, not on
    SkillDefinition descriptions (structural check)."""
    assert "supported_evidence_types" not in SkillDefinition.model_fields
    assert "supported_evidence_types" in STEP_TOOL_MAP[
        "explain_signal"].model_fields


# --- 6. declaration ↔ payload consistency (real tools, fixture adapters) -------

def _signal_fixture():
    return {
        "connected_account_count": 2,
        "connected_accounts": [
            {"user_id": "U00307", "relationship_type": ["shared_device"],
             "device_fingerprints": ["dev-1"], "shared_ips": ["1.2.3.4"],
             "risk_level": "HIGH", "risk_score": 70.0},
            {"user_id": "U00349", "relationship_type": ["shared_device"],
             "device_fingerprints": ["dev-1"], "shared_ips": [],
             "risk_level": "MEDIUM", "risk_score": 60.0},
        ],
    }


def test_fetch_case_payload_backs_case_summary():
    patch_fetch, patch_explain = rp_patches()
    with patch_fetch, patch_explain:
        result = risk_case_fetch("U00299")
    assert result.outcome.value == "success"
    data = result.data
    assert data.get("findings") is not None          # case_summary markers
    assert "ext" in data


def test_cross_account_payload_backs_opposite_trade_relations():
    signals = _signal_fixture()
    transactions = {
        "case_user_id": "U00299",
        "requested_accounts": ["U00299", "U00307"],
        "transactions": [
            {"transaction_id": "CATX-00001", "account_id": "U00299",
             "symbol": "BTC", "side": "BUY", "price": 40000.0,
             "quantity": 0.5, "value": 20000.0,
             "timestamp": "2026-08-19T10:00:00+00:00"},
            {"transaction_id": "CATX-00002", "account_id": "U00307",
             "symbol": "BTC", "side": "SELL", "price": 40000.0,
             "quantity": 0.5, "value": 20000.0,
             "timestamp": "2026-08-19T10:00:15+00:00"},
        ],
        "total_matched": 2, "returned_count": 2, "offset": 0, "limit": 100,
        "truncated": False, "has_more": False, "next_offset": None,
    }
    with patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_network_signals",
               new=lambda self, uid, limit=None: signals), \
         patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_account_transactions",
               new=lambda self, uid, **kw: transactions):
        result = cross_account_trade_analysis("U00299")
    assert result.outcome.value == "success"
    assert "candidates" in result.data               # the declared kind


def test_related_accounts_payload_backs_network_and_device():
    with patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_network_signals",
               new=lambda self, uid, limit=None: _signal_fixture()), \
         patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_case_evidence",
               new=lambda self, uid, expose_complete_records=False:
               fx.rp_evidence_payload(uid)):
        result = related_account_analysis("U00299")
    assert result.outcome.value == "success"
    data = result.data
    assert data.get("related_accounts")              # network_relations
    assert data.get("shared_devices") is not None    # shared_device_evidence


# --- 7. WP-GSP-1b: shared_ip_attributions pure pass-through echo ----------------

def test_shared_ip_echo_passes_through_nonempty_values():
    """Pure pass-through: a non-empty upstream shared_ips echoes verbatim —
    and this is DATA only; shared_ip_evidence stays undeclared."""
    with patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_network_signals",
               new=lambda self, uid, limit=None: _signal_fixture()), \
         patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_case_evidence",
               new=lambda self, uid, expose_complete_records=False:
               fx.rp_evidence_payload(uid)):
        result = related_account_analysis("U00299")
    data = result.data
    echo = data["shared_ip_attributions"]
    assert echo["U00307"] == ["1.2.3.4"]             # verbatim upstream
    assert echo["U00349"] == []                      # verbatim upstream
    # data-only discipline: the declaration is unchanged
    assert EvidenceType.SHARED_IP_EVIDENCE not in \
        STEP_TOOL_MAP["analyze_related_accounts"].supported_evidence_types


def test_shared_ip_echo_passes_through_all_empty():
    with patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_network_signals",
               new=lambda self, uid, limit=None: {
                   "connected_account_count": 1,
                   "connected_accounts": [
                       {"user_id": "U00001",
                        "relationship_type": ["shared_device"],
                        "device_fingerprints": ["dev-1"],
                        "shared_ips": [], "risk_level": "LOW",
                        "risk_score": 10.0}]}), \
         patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_case_evidence",
               new=lambda self, uid, expose_complete_records=False:
               fx.rp_evidence_payload(uid)):
        result = related_account_analysis("U00299")
    assert result.data["shared_ip_attributions"] == {"U00001": []}


def test_no_fabricated_shared_ip_data():
    """The echo is absent (not fabricated) when upstream provides no
    connected accounts at all."""
    with patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_network_signals",
               new=lambda self, uid, limit=None: {
                   "connected_account_count": 0,
                   "connected_accounts": []}), \
         patch("app.adapters.risk_platform.RiskPlatformAdapter."
               "fetch_case_evidence",
               new=lambda self, uid, expose_complete_records=False:
               fx.rp_evidence_payload(uid)):
        result = related_account_analysis("U00299")
    assert result.data.get("shared_ip_attributions") == {}

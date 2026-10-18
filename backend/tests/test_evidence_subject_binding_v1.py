"""Evidence subject binding — output satisfaction vs association (P0.1).

Association (step_output_map forcing an observation onto a contract
output) is NOT semantic sufficiency. A signal explanation names the
finding it explains (data.finding_id, projected into the summary); for
a finding-level output subject, an explanation of a DIFFERENT finding
must never satisfy the output — GoalOutcome must not advance on it.

All payloads mirror the REAL producer shapes (signal_explain →
data.finding_id + cluster_name / feature_facts / ml_score).
"""

import pytest

from app.goal_postconditions import (
    _category_postcondition, derive_goal_outcome, evaluate_output)
from app.goal_resolution import (GoalContract, OutputSatisfaction,
                                 RequiredOutput, ScopeLevel)
from app.skills import EvidenceType


def _rule_output(subject="F6"):
    return RequiredOutput(
        output_id="O1", claim="why flagged", claim_kind="explanation",
        subject_ref=subject, scope="finding_level",
        evidence_requirements=[EvidenceType.RULE_EXPLANATION])


def _graph_obs(explained, cluster="Cluster_device_sharing_1"):
    return {
        "step_id": "S1", "step_type": "explain_signal",
        "tool_name": "signal_explain", "outcome": "success",
        "for_outputs": ["O1"],
        "actual_evidence_types": ["graph_cluster_facts"],
        "summary": {"finding_id": explained,
                    "cluster_name": cluster,
                    "detection_type": "device_sharing",
                    "member_count": 19, "cluster_risk_score": 80.0,
                    "shared_devices": ["DEV_SYN"],
                    "related_accounts": ["UA", "UB"]},
    }


def _feature_obs(explained, value=70):
    return {
        "step_id": "S2", "step_type": "explain_signal",
        "tool_name": "signal_explain", "outcome": "success",
        "for_outputs": ["O1"],
        "actual_evidence_types": ["rule_explanation"],
        "summary": {"finding_id": explained,
                    "feature_facts": [
                        {"name": "trade_frequency_24h", "value": value}],
                    "attribution_available": False},
    }


class TestANegativeEvidenceTypeMismatch:
    def test_foreign_graph_facts_never_satisfy(self):
        """F5's graph-cluster explanation (with cluster_name, devices,
        related accounts) force-associated to F6's rule_explanation
        output must NOT satisfy it."""
        out = _rule_output("F6")
        obs = _graph_obs("F5")
        obs["for_outputs"] = ["O1"]      # step_output_map forced association
        r = evaluate_output(out, "U00008", [obs])
        assert r.satisfaction in (OutputSatisfaction.PENDING,
                                  OutputSatisfaction.UNRESOLVED)
        assert r.satisfaction != OutputSatisfaction.SATISFIED

    def test_goal_outcome_does_not_advance_on_foreign_evidence(self):
        """GoalOutcome must not read as complete when the only associated
        evidence explains a different finding."""
        contract = GoalContract(
            contract_id="GC-BIND", goal_type=None, subject_ref="F6",
            outputs=[_rule_output("F6")])
        obs = _graph_obs("F5")
        outcome = derive_goal_outcome(
            [evaluate_output(contract.outputs[0], "U00008",
                             [obs]).satisfaction],
            "completed")
        assert outcome.value != "complete"


class TestBLegitimateExplanationEvidence:
    def test_own_feature_explanation_satisfies(self):
        out = _rule_output("F6")
        r = evaluate_output(out, "U00008", [_feature_obs("F6")])
        assert r.satisfaction.value == "satisfied"
        assert r.reason_code == "explanation_available"

    def test_own_graph_explanation_satisfies(self):
        """A graph-detected finding's OWN cluster facts legitimately
        satisfy its rule_explanation output (subject binding holds)."""
        out = _rule_output("F5")
        r = evaluate_output(out, "U00008", [_graph_obs("F5")])
        assert r.satisfaction.value == "satisfied"


class TestCAssociationVsSatisfaction:
    def test_forced_association_does_not_bypass_semantics(self):
        """Forced step_output_map association + foreign evidence → the
        output state and the derived outcome stay honestly incomplete."""
        contract = GoalContract(
            contract_id="GC-BIND2", goal_type=None, subject_ref="F6",
            outputs=[_rule_output("F6")])
        obs = _graph_obs("F5")
        obs["for_outputs"] = ["O1"]
        r = evaluate_output(contract.outputs[0], "U00008", [obs])
        # UNRESOLVED = the existing honest "associated evidence gathered
        # but indeterminate for this output" state
        assert r.satisfaction == OutputSatisfaction.UNRESOLVED
        outcome = derive_goal_outcome([r.satisfaction], "completed")
        assert outcome.value != "complete"

    def test_explanation_mismatch_falls_to_unresolved_when_nothing_else(self):
        out = _rule_output("F6")
        obs = _graph_obs("F5")
        r = evaluate_output(out, "U00008", [obs])
        assert r.satisfaction == OutputSatisfaction.UNRESOLVED


class TestDOtherOutputTypesUnaffected:
    def test_withdrawal_records_still_satisfied(self):
        out = RequiredOutput(
            output_id="O1", claim="records", claim_kind="retrieval",
            subject_ref="U00033", scope="case_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS])
        obs = {
            "step_id": "S1", "step_type": "inspect_withdrawals",
            "tool_name": "finding_drilldown", "outcome": "success",
            "for_outputs": ["O1"],
            "actual_evidence_types": ["withdrawal_records"],
            "summary": {"record_count": 9, "complete": True,
                        "records": [{"record_id": "W1"},
                                    {"record_id": "W2"}]},
        }
        r = evaluate_output(out, "U00033", [obs])
        assert r.satisfaction.value == "satisfied"

    def test_destination_facet_still_satisfied(self):
        out = RequiredOutput(
            output_id="O1", claim="dest", claim_kind="existence_check",
            subject_ref="U00033", scope="case_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
            postcondition_facets=["destination_address_identity"])
        obs = {
            "step_id": "S1", "step_type": "inspect_withdrawals",
            "tool_name": "finding_drilldown", "outcome": "success",
            "for_outputs": ["O1"],
            "actual_evidence_types": ["withdrawal_records"],
            "summary": {"destination_identity": {
                "basis": "withdrawal.address", "records_total": 3,
                "records_with_identity": 3, "records_without_identity": 0,
                "unique_destinations": 3,
                "groups": [{"destination_ref": "dest-1",
                            "record_ids": ["W1"]},
                           {"destination_ref": "dest-2",
                            "record_ids": ["W2"]},
                           {"destination_ref": "dest-3",
                            "record_ids": ["W3"]}]}}}
        r = evaluate_output(out, "U00033", [obs])
        assert r.satisfaction.value == "satisfied"

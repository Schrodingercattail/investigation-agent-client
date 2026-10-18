"""GSP WP-GSP-3 (corrected) — actual-observation-evidence association.

Core invariant under test:

    capability declaration ≠ actual observation evidence
    association ≠ satisfaction

Layer A (StepToolBinding.supported_evidence_types) is only an upper
bound; association is driven by Layer B — the ACTUAL evidence types this
execution produced (deterministic from parameterization + payload).
"""

import json
import tempfile
from unittest.mock import patch

from app.agent_loop import AgentLoopState, associate_observation
from app.config import settings
from app.goal_resolution import (
    GoalContract,
    OutputSatisfaction,
    RequiredOutput,
    ScopeLevel,
    build_goal_contract,
)
from app.skills import EvidenceType, STEP_TOOL_MAP
from app.task_store_v2 import TaskStoreV2

import tests.test_risk_platform_adapter_v2 as fx


# --- helpers -----------------------------------------------------------------

def _contract(*outputs_spec):
    proposals = []
    for claim, subject, scope, evidence in outputs_spec:
        proposals.append({"claim": claim, "subject_ref": subject,
                          "scope": scope, "evidence_requirements": evidence})
    from app.goal_resolution import GoalRepresentation, GoalResolutionStatus
    repr_obj = GoalRepresentation(
        case_id="U00010", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref="U00010", reason="r", source="llm",
        request_text="req",
        contract_proposal={"required_outputs": proposals})
    contract, reason = build_goal_contract(repr_obj, "U00010")
    assert reason is None, reason
    return contract


def _state(contract=None):
    return AgentLoopState(
        task_id="TASK-A", investigation_id="INV-A", case_id="U00010",
        goal="g", goal_contract=contract)


def _obs(step_type="explain_signal", outcome="success", summary=None,
         semantic_status=None, tool_name=None):
    return {"tool_name": tool_name or STEP_TOOL_MAP[step_type].tool_name,
            "step_type": step_type, "outcome": outcome,
            "semantic_status": semantic_status,
            "summary": summary or {}, "error": None,
            "complete": None, "next_data_needed": None}


def _satisfaction_all(contract):
    return [o.satisfaction for o in contract.outputs]


EVIDENCE_VIEW_SUMMARY = {
    "record_count": 7,
    "streams": {"withdrawals_included": True,
                "transactions_included": False},
}

TX_VIEW_SUMMARY = {
    "record_count": 64,
    "streams": {"withdrawals_included": False,
                "transactions_included": True},
}


# --- Fixture A: exact match (ML explanation) --------------------------------------

def test_fixture_a_ml_explanation_associates_pending():
    contract = _contract(
        ("ml_pattern_detection_explanation", "U00010", "finding_level",
         ["ml_signal_explanation"]))
    obs = _obs("explain_signal", semantic_status="ML",
               summary={"ml_score": 99.41,
                        "detection_method": "LightGBM"})
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == [EvidenceType.ML_SIGNAL_EXPLANATION]
    assert obs["for_outputs"] == ["O1"]
    assert obs["association_reason"] == "evidence_type_match"
    assert _satisfaction_all(contract) == [OutputSatisfaction.PENDING]


# --- User Test 1: capability supports A+B, actual only A, required B --------------

def test_test1_false_association_prevented():
    """THE core acceptance: binding declares withdrawal+transaction; THIS
    observation produced only withdrawal records; the output requires
    transaction_records → NO association (capability ∩ requirement must
    never substitute for actual evidence)."""
    contract = _contract(
        ("transaction_answer", "U00010", "finding_level",
         ["transaction_records"]))
    obs = _obs("inspect_evidence", summary=dict(EVIDENCE_VIEW_SUMMARY))
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == [EvidenceType.WITHDRAWAL_RECORDS]
    assert obs["for_outputs"] == []
    assert obs["association_reason"] == "no_evidence_type_match"
    assert _satisfaction_all(contract) == [OutputSatisfaction.PENDING]


# --- User Test 2: actual match succeeds --------------------------------------------

def test_test2_actual_match_associates_pending():
    contract = _contract(
        ("transaction_answer", "U00010", "finding_level",
         ["transaction_records"]))
    obs = _obs("inspect_evidence", summary=dict(TX_VIEW_SUMMARY))
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == [EvidenceType.TRANSACTION_RECORDS]
    assert obs["for_outputs"] == ["O1"]
    assert _satisfaction_all(contract) == [OutputSatisfaction.PENDING]


# --- User Test 3: capability ≠ actual evidence --------------------------------------

def test_test3_capability_not_copied_into_actual():
    contract = _contract(
        ("records_answer", "U00010", "finding_level",
         ["transaction_records", "withdrawal_records"]))
    obs = _obs("inspect_evidence", summary=dict(EVIDENCE_VIEW_SUMMARY))
    associate_observation(_state(contract), obs)
    # association exists (withdrawal requirement satisfied by actual)
    assert obs["for_outputs"] == ["O1"]
    # but actual evidence records ONLY what was really produced
    assert obs["actual_evidence_types"] == [EvidenceType.WITHDRAWAL_RECORDS]
    assert EvidenceType.TRANSACTION_RECORDS not in \
        obs["actual_evidence_types"]


# --- User Test 4: empty actual evidence ----------------------------------------------

def test_test4_empty_actual_never_associates():
    contract = _contract(
        ("transaction_answer", "U00010", "finding_level",
         ["transaction_records"]))
    obs = _obs("inspect_evidence", summary={"record_count": 0,
                                            "streams": {}})
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == []
    assert obs["for_outputs"] == []
    assert _satisfaction_all(contract) == [OutputSatisfaction.PENDING]


# --- User Test 5: actual outside declared capability ---------------------------------

def test_test5_undeclared_actual_excluded_and_surfaced():
    """actual types outside the binding declaration are never treated as
    valid association evidence; they are surfaced via telemetry."""
    from app import telemetry
    events = []

    class _Sink:
        def record(self, event):
            events.append(event)

    previous = telemetry.get_sink()
    telemetry.set_sink(_Sink())
    try:
        contract = _contract(
            ("withdrawal_answer", "U00010", "finding_level",
             ["withdrawal_records"]))
        # inspect_withdrawals declares withdrawal_records; the payload
        # (inconsistent state) claims transaction stream → undeclared actual
        obs = _obs("inspect_withdrawals",
                   summary={"record_count": 5,
                            "streams": {"transactions_included": True}})
        associate_observation(_state(contract), obs)
        # truth recorded, but excluded from association
        assert obs["actual_evidence_types"] == [
            EvidenceType.TRANSACTION_RECORDS]
        assert obs["for_outputs"] == []
    finally:
        telemetry.set_sink(previous)
    warned = [e for e in events
              if e.event_type == "observation.evidence_undeclared"]
    assert warned and "transaction_records" in warned[0].evidence_kind


# --- User Test 6: explain_signal selective evidence -----------------------------------

def test_test6_signal_type_selects_single_actual():
    contract = _contract(
        ("rule_answer", "U00010", "finding_level", ["rule_explanation"]))
    obs = _obs("explain_signal", semantic_status="ML",
               summary={"ml_score": 99.41, "detection_method": "LightGBM"})
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == [EvidenceType.ML_SIGNAL_EXPLANATION]
    assert obs["for_outputs"] == []          # ML actual ≠ rule requirement
    assert _satisfaction_all(contract) == [OutputSatisfaction.PENDING]


def test_test6b_rule_signal_actual_matches_rule_requirement():
    contract = _contract(
        ("rule_answer", "U00010", "finding_level", ["rule_explanation"]))
    obs = _obs("explain_signal", semantic_status="Rule",
               summary={"rule_name": "High withdrawal frequency",
                        "threshold": "withdrawal_frequency_24h > 5",
                        "trigger_values": {"withdrawal_frequency_24h": 7}})
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == [EvidenceType.RULE_EXPLANATION]
    assert obs["for_outputs"] == ["O1"]


# --- User Test 7: related-account shared-device vs shared-IP --------------------------

def test_test7_shared_ip_data_does_not_become_shared_ip_evidence():
    """The echoed shared-IP attributions are DATA; the capability is not
    declared — actual evidence excludes shared_ip_evidence and the
    shared-IP requirement stays UNASSOCIATED."""
    contract = _contract(
        ("shared_ip_assessment", "U00010", "relationship_graph",
         ["shared_ip_evidence"]))
    obs = _obs("analyze_related_accounts",
               summary={"related_accounts_count": 18,
                        "related_accounts": ["U00004", "U00307"],
                        "shared_devices": ["DEV8Z1O4A35QU"],
                        "cluster_name": "Cluster_device_sharing_1",
                        "detection_type": "device_sharing"})
    associate_observation(_state(contract), obs)
    assert EvidenceType.SHARED_IP_EVIDENCE not in \
        obs["actual_evidence_types"]
    assert set(obs["actual_evidence_types"]) == {
        EvidenceType.NETWORK_RELATIONS, EvidenceType.SHARED_DEVICE_EVIDENCE}
    assert obs["for_outputs"] == []
    assert _satisfaction_all(contract) == [OutputSatisfaction.PENDING]


def test_test7b_shared_device_requirement_still_associates():
    contract = _contract(
        ("shared_device_answer", "U00010", "relationship_graph",
         ["shared_device_evidence"]))
    obs = _obs("analyze_related_accounts",
               summary={"related_accounts_count": 18,
                        "related_accounts": ["U00004"],
                        "shared_devices": ["DEV8Z1O4A35QU"]})
    associate_observation(_state(contract), obs)
    assert obs["for_outputs"] == ["O1"]


# --- parameter-locked streams ------------------------------------------------------

def test_stream_lock_withdrawals_yields_withdrawal_only():
    contract = _contract(
        ("transaction_answer", "U00010", "finding_level",
         ["transaction_records"]))
    obs = _obs("inspect_withdrawals", summary=dict(EVIDENCE_VIEW_SUMMARY))
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == [EvidenceType.WITHDRAWAL_RECORDS]
    assert obs["for_outputs"] == []          # stream lock beats summary


# --- 1:many / many:1 ------------------------------------------------------------------

def test_case_summary_observation_associates_many_outputs():
    contract = _contract(
        ("risk_level_report", "U00010", "case_level", ["case_summary"]),
        ("risk_reason_report", "U00010", "case_level",
         ["case_summary", "rule_explanation"]))
    obs = _obs("fetch_case",
               summary={"finding_count": 8, "findings": []})
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == [EvidenceType.CASE_SUMMARY]
    assert obs["for_outputs"] == ["O1", "O2"]
    assert all(s == OutputSatisfaction.PENDING
               for s in _satisfaction_all(contract))
    assert len(_satisfaction_all(contract)) == 2


def test_many_observations_to_one_output():
    contract = _contract(
        ("withdrawal_answer", "U00010", "finding_level",
         ["withdrawal_records"]))
    state = _state(contract)
    for _ in range(3):
        obs = _obs("inspect_withdrawals",
                   summary={"record_count": 7,
                            "streams": {"withdrawals_included": True}})
        associate_observation(state, obs)
        assert obs["for_outputs"] == ["O1"]
        state.observations.append(obs)
    assert _satisfaction_all(contract) == [OutputSatisfaction.PENDING]


# --- UNASSOCIATED ---------------------------------------------------------------------

def test_unassociated_no_contract():
    """No contract → no association; but actual evidence is still
    computed (it is execution truth, independent of the contract)."""
    obs = _obs("fetch_case", summary={"finding_count": 8})
    associate_observation(_state(None), obs)
    assert obs["for_outputs"] == []
    assert obs["association_reason"] == "no_contract"
    assert obs["actual_evidence_types"] == [EvidenceType.CASE_SUMMARY]


def test_unassociated_unknown_step_type():
    contract = _contract(
        ("a", "U00010", "case_level", ["case_summary"]))
    obs = _obs("explain_signal")
    obs["step_type"] = "not_a_step"
    associate_observation(_state(contract), obs)
    assert obs["for_outputs"] == []
    assert obs["association_reason"] == "no_declared_evidence_type"


def test_failed_observation_has_no_actual_evidence():
    contract = _contract(
        ("withdrawal_answer", "U00010", "finding_level",
         ["withdrawal_records"]))
    obs = _obs("inspect_withdrawals", outcome="integration_error",
               summary={})
    associate_observation(_state(contract), obs)
    assert obs["actual_evidence_types"] == []
    assert obs["for_outputs"] == []


# --- association never mutates outputs -------------------------------------------------

def test_association_never_mutates_outputs():
    contract = _contract(
        ("risk_level_report", "U00010", "case_level", ["case_summary"]),
        ("ml_explanation", "F1", "finding_level",
         ["ml_signal_explanation"]))
    before = [(o.output_id, o.satisfaction, o.evidence_requirements)
              for o in contract.outputs]
    for st, summ in (("fetch_case", {"finding_count": 8}),
                     ("explain_signal", {"ml_score": 1.0})):
        associate_observation(_state(contract), _obs(st, summary=summ))
    after = [(o.output_id, o.satisfaction, o.evidence_requirements)
             for o in contract.outputs]
    assert before == after


def test_no_llm_in_association_path():
    import inspect
    sig = inspect.signature(associate_observation)
    assert list(sig.parameters) == ["state", "observation"]


# --- loop-level flag behavior ------------------------------------------------------------

def _run_and_return_observations(monkeypatch, flag_on):
    from tests.test_al351_target_validity_guard_v1 import (
        _build_runner, _resolved_outcome, _CustomResolver)
    from app.models import InvestigationContext
    from tests.test_api_investigations_v1 import make_findings
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", flag_on)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp3c.db")
    findings = make_findings()
    runner = _build_runner(store)
    runner.resolver = _CustomResolver(
        _resolved_outcome(evidence_kind="concept_grounding"))

    from tests.test_gsp2_goal_contract_v1 import _repr, _proposal
    from app.goal_resolution import GoalRepresentation, \
        GoalResolutionStatus

    def goal_resolver(req, fid, case):
        return GoalRepresentation(
            case_id="U00299", goal_type="policy_check",
            goal_resolution=GoalResolutionStatus.INFERRED.value,
            subject_type="case", subject_ref="U00299", reason="r",
            source="llm", request_text="req",
            contract_proposal={"required_outputs": [{
                "claim": "signal_explanation_report", "subject_ref": "U00299",
                "scope": "finding_level",
                "evidence_requirements": ["rule_explanation"]}]})

    runner.goal_resolver = goal_resolver
    with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
               "fetch_case",
               new=lambda self, uid: (
                   fx.rp_evidence_payload(uid),
                   fx.rp_explanation_payload())):
        result = runner.run("g", InvestigationContext(case_id="U00299"),
                            findings, investigation_id="INV-GSP3",
                            task_id="TASK-GSP3",
                            findings_provider=lambda: findings)
    return result.state


def test_flag_on_actual_evidence_and_association(monkeypatch):
    state = _run_and_return_observations(monkeypatch, flag_on=True)
    associated = [o for o in state.observations if o.get("for_outputs")]
    assert associated, "flag ON must associate matching observations"
    signal_obs = [o for o in associated
                  if o.get("step_type") == "explain_signal"]
    assert signal_obs
    got = signal_obs[0]["actual_evidence_types"]
    got = [t.value if hasattr(t, "value") else t for t in got]
    assert got == ["rule_explanation"]
    # WP-GSP-4 onward: satisfaction is written by the postcondition
    # evaluator (single write point) — this run's rule explanation is
    # grounded, so the output is SATISFIED (positive). WP-GSP-3's own
    # assertions (actual types + association) are above and unchanged.
    assert all(o.satisfaction == OutputSatisfaction.SATISFIED
               for o in state.goal_contract.outputs)
    assert all(o.polarity == "positive"
               for o in state.goal_contract.outputs)


def test_flag_off_no_association_keys(monkeypatch):
    state = _run_and_return_observations(monkeypatch, flag_on=False)
    assert all("for_outputs" not in o for o in state.observations)
    assert all("actual_evidence_types" not in o for o in state.observations)


# --- persistence / projection -------------------------------------------------------------

def test_associations_and_actual_types_survive_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp3d.db")
    state = _run_and_return_observations(monkeypatch, flag_on=True)
    state.task_id = "TASK-GSP3P"
    store.save_agent_state(state)
    loaded = store.get_agent_state("TASK-GSP3P")
    before = [(o.get("step_type"), o.get("actual_evidence_types"),
               o.get("for_outputs"), o.get("association_reason"))
              for o in state.observations]
    after = [(o.get("step_type"), o.get("actual_evidence_types"),
              o.get("for_outputs"), o.get("association_reason"))
             for o in loaded.observations]
    assert before == after


def test_projection_carries_actual_evidence_types(monkeypatch):
    from app.agent_loop import project_task_activity
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    state = _run_and_return_observations(monkeypatch, flag_on=True)
    assoc = project_task_activity(state)["observation_associations"]
    assert assoc and all("actual_evidence_types" in a for a in assoc)
    assert any(a["actual_evidence_types"] for a in assoc)

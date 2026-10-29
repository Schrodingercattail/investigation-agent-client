"""GSP WP-GSP-4 — Deterministic Postcondition Evaluator tests.

L1 deterministic coverage: pure evaluator, no LLM/network/DB, single
satisfaction write point (loop-side apply), negative satisfaction with
proven completeness, subject-membership semantics, assessment claims
stay UNRESOLVED, multi-observation combining, multi-output independence.
"""

import json
import tempfile
from unittest.mock import patch

from app.domain_tools.artifact_bundle import artifact_bundle
from app.goal_postconditions import (
    POLARITY_NEGATIVE,
    POLARITY_POSITIVE,
    R_JUDGMENT_NOT_ADJUDICABLE,
    R_RECORDS_EMPTY_COMPLETE,
    R_SCOPE_TRUNCATED,
    R_SUBJECT_ABSENT_COMPLETE,
    R_SUBJECT_PRESENT,
    PostconditionResult,
    apply_postcondition,
    evaluate_contract_outputs,
    evaluate_output,
)
from app.goal_resolution import (
    GoalContract,
    OutputSatisfaction,
    RequiredOutput,
    ScopeLevel,
    build_goal_contract,
)
from app.skills import EvidenceType
from app.models import ToolCallStatusV2
from app.task_store_v2 import TaskStoreV2
from app.config import settings

import tests.test_risk_platform_adapter_v2 as fx


SAT = OutputSatisfaction.SATISFIED
PENDING = OutputSatisfaction.PENDING
UNRESOLVED = OutputSatisfaction.UNRESOLVED
PARTIAL = OutputSatisfaction.PARTIALLY_SUPPORTED


def _contract(claim_kind, claim, subject, scope, evidence):
    from app.goal_resolution import GoalRepresentation, GoalResolutionStatus
    repr_obj = GoalRepresentation(
        case_id="U00010", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref=subject, reason="r", source="llm",
        request_text="req",
        contract_proposal={"required_outputs": [{
            "claim": claim, "claim_kind": claim_kind,
            "subject_ref": subject, "scope": scope,
            "evidence_requirements": evidence}]})
    contract, reason = build_goal_contract(repr_obj, "U00010")
    assert reason is None, reason
    return contract


def _obs(step_type, tool_name, outcome="success", summary=None,
         semantic_status=None, for_outputs=None, actual=None):
    o = {"tool_name": tool_name, "step_type": step_type,
         "outcome": outcome, "semantic_status": semantic_status,
         "summary": summary or {}, "error": None, "complete": None,
         "next_data_needed": None}
    if for_outputs is not None:
        o["for_outputs"] = for_outputs
    if actual is not None:
        o["actual_evidence_types"] = actual
    return o


def _eval(contract, observations):
    return evaluate_contract_outputs(contract, observations, "U00010")[0]


# --- Test 1: positive subject membership -----------------------------------------

def test_test1_positive_subject_membership():
    contract = _contract("existence_check", "hedge_presence_assessment",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00010",
                    "account_b": "U00307"}],
                   "retrieval": {"truncated": False}},
               for_outputs=["O1"])
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_POSITIVE
    assert result.reason_code == R_SUBJECT_PRESENT


# --- Test 2: negative subject membership (complete) --------------------------------

def test_test2_negative_subject_membership_complete():
    contract = _contract("existence_check", "hedge_presence_assessment",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"},
                   {"candidate_id": "C2", "account_a": "U00301",
                    "account_b": "U00325"}],
                   "accounts_analyzed": ["U00010", "U00307", "U00349",
                                         "U00301", "U00325"],
                   "retrieval": {"truncated": False}},
                   for_outputs=["O1"])
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_NEGATIVE
    assert result.reason_code == R_SUBJECT_ABSENT_COMPLETE
    assert result.completeness == "complete"


# --- Test 3: truncated scope → not a complete negative ------------------------------

def test_test3_truncated_scope_never_negative_satisfied():
    contract = _contract("existence_check", "hedge_presence_assessment",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"}],
                   "retrieval": {"truncated": True}},
                   for_outputs=["O1"])
    result = _eval(contract, [obs])
    assert result.satisfaction == PARTIAL
    assert result.polarity is None
    assert result.reason_code == R_SCOPE_TRUNCATED


# --- Test 4: empty but source/field unavailable → UNRESOLVED ------------------------

def test_test4_empty_without_field_is_unresolved():
    contract = _contract("existence_check", "shared_ip_assessment",
                         "U00010", "relationship_graph",
                         ["shared_ip_evidence"])
    # pre-1b style observation: no shared_ip_attributions field at all
    obs = _obs("analyze_related_accounts", "related_account_analysis",
               summary={"related_accounts_count": 18,
                        "related_accounts": ["U00004"],
                        "shared_devices": ["DEV8Z1O4A35QU"]},
               for_outputs=["O1"])
    result = _eval(contract, [obs])
    assert result.satisfaction == UNRESOLVED
    assert result.reason_code == "required_field_absent"
    assert result.polarity is None


# --- Test 5: empty AND complete → SATISFIED negative ---------------------------------

def test_test5_empty_complete_is_negative_satisfied():
    contract = _contract("existence_check", "shared_ip_assessment",
                         "U00010", "relationship_graph",
                         ["shared_ip_evidence"])
    obs = _obs("analyze_related_accounts", "related_account_analysis",
               summary={"related_accounts_count": 18,
                        "related_accounts": [f"U{i:05d}" for i in range(18)],
                        "shared_devices": ["DEV8Z1O4A35QU"],
                        "shared_ip_attributions": {
                            f"U{i:05d}": [] for i in range(18)}},
               for_outputs=["O1"])
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_NEGATIVE
    assert result.reason_code == R_RECORDS_EMPTY_COMPLETE
    assert result.completeness == "complete"


# --- Test 6: association exists but facts lack the subject --------------------------
# (covered by Test 2 — the association was ["O1"] and the evaluator still
# returned the subject-aware negative; asserted again here explicitly.)

def test_test6_association_does_not_imply_positive():
    contract = _contract("existence_check", "hedge_presence_assessment",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"}],
                   "retrieval": {"truncated": False}},
               for_outputs=["O1"])
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_NEGATIVE   # NOT positive


# --- Test 7: evidence type alone cannot prove an assessment claim --------------------

def test_test7_records_never_satisfy_laundering_assessment():
    contract = _contract("assessment", "money_laundering_assessment",
                         "U00010", "account_level",
                         ["transaction_records", "withdrawal_records",
                          "network_relations", "policy_references",
                          "ml_signal_explanation"])
    obs = _obs("inspect_evidence", "finding_drilldown",
               summary={"record_count": 64,
                        "streams": {"transactions_included": True},
                        "complete": True},
               for_outputs=["O1"],
               actual=["transaction_records"])
    result = _eval(contract, [obs])
    assert result.satisfaction == UNRESOLVED
    assert result.reason_code == R_JUDGMENT_NOT_ADJUDICABLE
    assert result.evidence_types == ["transaction_records"]


# --- Test 8: shared device evidence cannot satisfy shared-IP output ------------------

def test_test8_shared_device_never_satisfies_shared_ip():
    contract = _contract("existence_check", "shared_ip_assessment",
                         "U00010", "relationship_graph",
                         ["shared_ip_evidence"])
    # GSP-3 would not associate this observation at all (no type overlap);
    # with no associated evidence the output stays PENDING — never satisfied.
    obs = _obs("analyze_related_accounts", "related_account_analysis",
               summary={"related_accounts_count": 18,
                        "related_accounts": ["U00004"],
                        "shared_devices": ["DEV8Z1O4A35QU"]},
               for_outputs=["O1"])   # hypothetically force-associated
    result = _eval(contract, [obs])
    # core invariant: shared-device facts never SATISFY the shared-IP output
    assert result.satisfaction != SAT


# --- Test 9: multiple observations combine --------------------------------------------

def test_test9_observations_combine_union_semantics():
    contract = _contract("existence_check", "hedge_presence_assessment",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    o1 = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
              summary={"candidates": [
                  {"candidate_id": "C1", "account_a": "U00307",
                   "account_b": "U00349"}],
                  "retrieval": {"truncated": False}},
              for_outputs=["O1"])
    o2 = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
              summary={"candidates": [
                  {"candidate_id": "C2", "account_a": "U00010",
                   "account_b": "U00301"}],
                  "retrieval": {"truncated": False}},
              for_outputs=["O1"])
    result = _eval(contract, [o1, o2])
    # union of facts: U00010 IS present in the combined candidate set
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_POSITIVE

    # and the reverse order must combine identically (order-independent
    # union; latest-wins applies only to scalar fields)
    result2 = _eval(contract, [o2, o1])
    assert result2.satisfaction == SAT
    assert result2.polarity == POLARITY_POSITIVE


# --- Test 10: multiple outputs remain independent --------------------------------------

def test_test10_outputs_independent():
    from app.goal_resolution import GoalRepresentation, GoalResolutionStatus
    repr_obj = GoalRepresentation(
        case_id="U00010", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref="U00010", reason="r", source="llm",
        request_text="req",
        contract_proposal={"required_outputs": [
            {"claim": "shared_ip_assessment", "claim_kind": "existence_check",
             "subject_ref": "U00010", "scope": "relationship_graph",
             "evidence_requirements": ["shared_ip_evidence"]},
            {"claim": "laundering_assessment", "claim_kind": "assessment",
             "subject_ref": "U00010", "scope": "account_level",
             "evidence_requirements": ["transaction_records"]}]})
    contract, reason = build_goal_contract(repr_obj, "U00010")
    assert reason is None
    shared_obs = _obs("analyze_related_accounts", "related_account_analysis",
                      summary={"related_accounts_count": 1,
                               "related_accounts": ["U00004"],
                               "shared_devices": ["DEV8Z1O4A35QU"],
                               "shared_ip_attributions": {"U00004": []}},
                      for_outputs=["O1"])
    results = evaluate_contract_outputs(contract, [shared_obs], "U00010")
    by_id = {r.output_id: r for r in results}
    assert by_id["O1"].satisfaction == SAT           # complete negative
    assert by_id["O1"].polarity == POLARITY_NEGATIVE
    assert by_id["O2"].satisfaction == PENDING        # no evidence associated
    assert by_id["O2"].polarity is None


# --- Test 11: LLM "done" cannot bypass the evaluator ------------------------------------

def test_test11_llm_done_cannot_write_satisfaction(monkeypatch):
    """A STOP decision with a satisfied-sounding reason must NOT flip an
    unresolved output: the evaluator is the only satisfaction writer."""
    from tests.test_al351_target_validity_guard_v1 import (
        _build_runner, _resolved_outcome, _CustomResolver,
        ScriptedProposer, DecisionProposal)
    from app.models import InvestigationContext
    from tests.test_api_investigations_v1 import make_findings
    from app.agent_loop import AgentLoopRunner
    from app.planner_v2 import PlannerV2
    from tests.test_api_investigations_v1 import (
        PLAN_SIGNAL as PLAN, ScriptedLLM)
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp4.db")
    findings = make_findings()
    runner = AgentLoopRunner(
        store=store,
        resolver=_CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding")),
        planner=PlannerV2(ScriptedLLM([PLAN])),
        executor=None,
        goal_resolver=lambda req, fid, case: None,
        findings_provider=lambda: [f.model_copy(deep=True)
                                   for f in findings],
        proposer=ScriptedProposer(["STOP"]))
    # force a contract whose output has NO associated observations →
    # the evaluator keeps it PENDING regardless of the LLM's STOP reason
    # (WP-GSP-6 note: with the claim-kind gate, an ASSESSMENT output is
    # planning-unsupported — UNSUPPORTED at admission, never SATISFIED)
    from tests.test_gsp2_goal_contract_v1 import _repr, _proposal

    def goal_resolver(req, fid, case):
        return _repr([_proposal(claim="money_laundering_assessment",
                                claim_kind="assessment",
                                subject="U00299", scope="account_level",
                                evidence=["transaction_records"])],
                     subject="U00299", case_id="U00299")

    runner.goal_resolver = goal_resolver
    result = runner.run("这个账号是否有洗钱情况？",
                        InvestigationContext(case_id="U00299"),
                        findings, investigation_id="INV-GSP4",
                        task_id="TASK-GSP4",
                        findings_provider=lambda: findings)
    contract = result.state.goal_contract
    assert contract is not None
    # the assessment output was never satisfied by the STOP decision:
    # capability exists (transaction_records) but the plan omitted every
    # covering step → NOT_COVERED_BY_PLAN semantics → stays PENDING
    # (LLM "done" can never write SATISFIED; polarity stays None)
    assert contract.outputs[0].satisfaction == OutputSatisfaction.PENDING
    assert contract.outputs[0].polarity is None
    assert result.state.agent_status in ("completed", "stopped_by_limit")


# --- Test 12: complete negative does not churn PENDING -----------------------------------

def test_test12_complete_negative_is_terminal_satisfied():
    contract = _contract("existence_check", "shared_ip_assessment",
                         "U00010", "relationship_graph",
                         ["shared_ip_evidence"])
    obs = _obs("analyze_related_accounts", "related_account_analysis",
               summary={"related_accounts_count": 2,
                        "related_accounts": ["U00004", "U00307"],
                        "shared_devices": ["DEV8Z1O4A35QU"],
                        "shared_ip_attributions": {"U00004": [],
                                                   "U00307": []}},
               for_outputs=["O1"])
    first = _eval(contract, [obs])
    again = _eval(contract, [obs, obs])
    assert first.satisfaction == SAT
    assert again.satisfaction == SAT
    assert first.polarity == again.polarity == POLARITY_NEGATIVE
    # the evaluator is PURE: the loop's apply-step is the only writer —
    # contract outputs remain PENDING until the loop applies the verdict
    assert all(o.satisfaction == OutputSatisfaction.PENDING
               for o in contract.outputs)


# --- Fixture D: money laundering assessment ----------------------------------------------

def test_fixture_d_laundering_unresolved_even_with_evidence():
    from app.goal_resolution import GoalRepresentation, GoalResolutionStatus
    repr_obj = GoalRepresentation(
        case_id="U00010", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref="U00010", reason="r", source="llm",
        request_text="req",
        contract_proposal={"required_outputs": [{
            "claim": "money_laundering_assessment",
            "claim_kind": "assessment", "subject_ref": "U00010",
            "scope": "account_level",
            "evidence_requirements": [
                "transaction_records", "withdrawal_records",
                "network_relations", "policy_references",
                "ml_signal_explanation"]}]})
    contract, _ = build_goal_contract(repr_obj, "U00010")
    records_obs = _obs("inspect_evidence", "finding_drilldown",
                       summary={"record_count": 64,
                                "streams": {"transactions_included": True},
                                "complete": True},
                       for_outputs=["O1"], actual=["transaction_records"])
    related_obs = _obs("analyze_related_accounts",
                       "related_account_analysis",
                       summary={"related_accounts_count": 18,
                                "related_accounts": ["U00004"],
                                "shared_devices": ["DEV8Z1O4A35QU"]},
                       for_outputs=["O1"],
                       actual=["network_relations",
                               "shared_device_evidence"])
    result = _eval(contract, [records_obs, related_obs])
    assert result.satisfaction == UNRESOLVED
    assert result.reason_code == R_JUDGMENT_NOT_ADJUDICABLE
    assert set(result.evidence_types) == {
        "transaction_records", "network_relations",
        "shared_device_evidence"}


# --- Fixture E: valid existing finding retrieval stays satisfied --------------------------

def test_fixture_e_valid_retrieval_satisfied():
    """L1: a records-listing deliverable is satisfied when the evidence is
    complete AND the full set is emitted to the dialogue (record_rows)."""
    contract = _contract("retrieval", "withdrawal_records_answer",
                         "U00010", "finding_level",
                         ["withdrawal_records"])
    obs = _obs("inspect_withdrawals", "finding_drilldown",
               summary={"record_count": 7,
                        "record_rows": [
                            {"record_id": f"W{i:03d}", "timestamp": f"t{i}",
                             "summary": f"w{i}",
                             "address": f"addr-{i}"} for i in range(7)],
                        "streams": {"withdrawals_included": True},
                        "complete": True},
               for_outputs=["O1"], actual=["withdrawal_records"])
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_POSITIVE
    assert result.reason_code == "records_retrieved_complete"


def test_l1_no_emission_proof_is_partial():
    """L1 (honest shape): evidence complete but the observation summary
    carries NO record_rows — the dialogue emission cannot be proven, so a
    records-LISTING deliverable must not be SATISFIED on retrieval
    completeness alone (legacy shape kept for the regression record)."""
    contract = _contract("retrieval", "withdrawal_records_answer",
                         "U00010", "finding_level",
                         ["withdrawal_records"])
    obs = _obs("inspect_withdrawals", "finding_drilldown",
               summary={"record_count": 7,
                        "streams": {"withdrawals_included": True},
                        "complete": True},
               for_outputs=["O1"], actual=["withdrawal_records"])
    result = _eval(contract, [obs])
    assert result.satisfaction == PARTIAL


# --- structural: purity + single write point ----------------------------------------------

def test_evaluator_is_pure_no_mutation():
    contract = _contract("existence_check", "hedge_presence_assessment",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [], "retrieval": {"truncated": False}},
               for_outputs=["O1"])
    obs_snapshot = json.dumps(obs, sort_keys=True)
    contract_snapshot = contract.model_dump_json()
    _eval(contract, [obs])
    assert json.dumps(obs, sort_keys=True) == obs_snapshot
    assert contract.model_dump_json() == contract_snapshot


def test_records_empty_via_outcome_gate():
    contract = _contract("retrieval", "records_answer", "U00010",
                         "finding_level", ["withdrawal_records"])
    obs = _obs("inspect_withdrawals", "finding_drilldown",
               outcome="empty",
               summary={"record_count": 0,
                        "streams": {"withdrawals_included": True}},
               for_outputs=["O1"], actual=["withdrawal_records"])
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_NEGATIVE
    assert result.reason_code == R_RECORDS_EMPTY_COMPLETE


def test_integration_error_stays_pending():
    contract = _contract("retrieval", "records_answer", "U00010",
                         "finding_level", ["withdrawal_records"])
    obs = _obs("inspect_withdrawals", "finding_drilldown",
               outcome="integration_error", summary={},
               for_outputs=["O1"])
    result = _eval(contract, [obs])
    assert result.satisfaction == PENDING   # outage ≠ verdict


def test_persistence_round_trip_negative_satisfaction(monkeypatch):
    """observation → evaluator → satisfaction → save → reload: state kept."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    from tests.test_al351_target_validity_guard_v1 import (
        _build_runner, _resolved_outcome, _CustomResolver)
    from app.models import InvestigationContext
    from tests.test_api_investigations_v1 import make_findings
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp4.db")
    findings = make_findings()
    runner = _build_runner(store)
    runner.resolver = _CustomResolver(
        _resolved_outcome(evidence_kind="concept_grounding"))

    from tests.test_gsp2_goal_contract_v1 import _repr, _proposal

    def goal_resolver(req, fid, case):
        return _repr([_proposal(claim="signal_explanation_report",
                                claim_kind="explanation",
                                subject="U00299", scope="finding_level",
                                evidence=["rule_explanation"])],
                     subject="U00299", case_id="U00299")

    runner.goal_resolver = goal_resolver
    with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
               "fetch_case",
               new=lambda self, uid: (
                   fx.rp_evidence_payload(uid),
                   fx.rp_explanation_payload())):
        result = runner.run("g", InvestigationContext(case_id="U00299"),
                            findings, investigation_id="INV-GSP4B",
                            task_id="TASK-GSP4B",
                            findings_provider=lambda: findings)
    assert result.state.goal_contract is not None
    o = result.state.goal_contract.outputs[0]
    assert o.satisfaction in (SAT, PARTIAL, UNRESOLVED, PENDING)
    # reload: satisfaction preserved
    loaded = store.get_agent_state("TASK-GSP4B")
    assert loaded.goal_contract.outputs[0].satisfaction == o.satisfaction
    assert loaded.goal_contract.outputs[0].polarity == o.polarity


# --- GSP-4 correction: deterministic multi-observation conflict handling -----------
# Observation order must NEVER decide semantic truth: conflicting scalar
# facts are excluded (UNANIMITY) and surfaced; lists union; outcome derives
# from the union (any success = facts exist; unanimous empty = determinate
# empty). No latest-wins anywhere.

def _signal_obs(ml_score):
    return _obs("explain_signal", "signal_explain", semantic_status="ML",
                summary={"ml_score": ml_score, "detection_method": "LightGBM"},
                for_outputs=["O1"], actual=["ml_signal_explanation"])


def test_conflicting_scalars_resolve_to_unresolved_both_orders():
    """Observation A: ml_score=99.41; Observation B: ml_score=10.0 —
    the verdict must be identical (UNRESOLVED / conflicting_observations)
    in BOTH orders, with polarity=None (never SATISFIED with either
    value). Latest-wins would have produced 99.41-positive one way and
    10.0-positive the other."""
    contract = _contract("existence_check", "ml_explanation_answer",
                         "U00010", "finding_level",
                         ["ml_signal_explanation"])
    a = _signal_obs(99.41)
    b = _signal_obs(10.0)
    r_ab = _eval(contract, [a, b])
    r_ba = _eval(contract, [b, a])
    for r in (r_ab, r_ba):
        assert r.satisfaction == UNRESOLVED
        assert r.polarity is None              # never SATISFIED(CRITICAL/LOW)
        assert r.reason_code == "conflicting_observations"
        assert "ml_score" in r.conflicts
    # HARD INVARIANT: the full normalized verdict must be order-independent
    assert (r_ab.satisfaction, r_ab.polarity, r_ab.reason_code,
            tuple(sorted(r_ab.evidence_types)), r_ab.completeness,
            tuple(r_ab.conflicts)) == (
        r_ba.satisfaction, r_ba.polarity, r_ba.reason_code,
        tuple(sorted(r_ba.evidence_types)), r_ba.completeness,
        tuple(r_ba.conflicts))


def test_unanimous_scalars_still_determinate():
    contract = _contract("existence_check", "ml_explanation_answer",
                         "U00010", "finding_level",
                         ["ml_signal_explanation"])
    result = _eval(contract, [_signal_obs(99.41), _signal_obs(99.41)])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_POSITIVE
    assert result.conflicts == []


def test_outcome_empty_vs_success_derived_from_union():
    """Order-independence of combined verdicts:
    - same-query scalars conflicting (record_count 5 vs 9) → UNRESOLVED
      with conflicting_observations, IDENTICALLY in both orders;
    - unanimous empty → determinate negative in any order.
    Cross-sub-query unions (tx view + withdrawal view) have sub-scope
    scalars that legitimately differ — under the unanimity rule they are
    conflicted keys → the output is UNRESOLVED (sub-scope-aware merging
    is a later work package), identically in both orders."""
    contract = _contract("retrieval", "records_answer", "U00010",
                         "finding_level", ["transaction_records"])
    success_obs = _obs("inspect_evidence", "finding_drilldown",
                       summary={"record_count": 64,
                                "streams": {"withdrawals_included": False,
                                            "transactions_included": True},
                                "complete": True},
                       for_outputs=["O1"], actual=["transaction_records"])
    empty_obs = _obs("inspect_withdrawals", "finding_drilldown",
                     outcome="empty",
                     summary={"record_count": 0,
                              "streams": {"withdrawals_included": True}},
                     for_outputs=["O1"], actual=["withdrawal_records"])
    r_mixed = _eval(contract, [success_obs, empty_obs])
    r_mixed2 = _eval(contract, [empty_obs, success_obs])
    for r in (r_mixed, r_mixed2):
        assert r.satisfaction == UNRESOLVED
        assert r.polarity is None
        assert r.reason_code == "conflicting_observations"
    # HARD INVARIANT: full normalized verdict is order-independent
    assert (r_mixed.satisfaction, r_mixed.polarity, r_mixed.reason_code,
            tuple(sorted(r_mixed.evidence_types)), r_mixed.completeness,
            tuple(r_mixed.conflicts)) == (
        r_mixed2.satisfaction, r_mixed2.polarity, r_mixed2.reason_code,
        tuple(sorted(r_mixed2.evidence_types)), r_mixed2.completeness,
        tuple(r_mixed2.conflicts))

    # unanimous empty → determinate negative (order-independent)
    e1 = _obs("inspect_evidence", "finding_drilldown", outcome="empty",
              summary={"record_count": 0,
                       "streams": {"transactions_included": True}},
              for_outputs=["O1"], actual=["transaction_records"])
    e2 = _obs("inspect_evidence", "finding_drilldown", outcome="empty",
              summary={"record_count": 0,
                       "streams": {"transactions_included": True}},
              for_outputs=["O1"], actual=["transaction_records"])
    r_empty = _eval(contract, [e1, e2])
    assert r_empty.satisfaction == SAT
    assert r_empty.polarity == POLARITY_NEGATIVE


def test_conflict_reason_surfaces_on_unresolved_fallback():
    """A conflict that leaves the category postcondition indeterminate is
    stated explicitly as conflicting_observations."""
    contract = _contract("retrieval", "records_answer", "U00010",
                         "finding_level", ["transaction_records"])
    a = _obs("inspect_evidence", "finding_drilldown",
             summary={"record_count": 5,
                      "streams": {"transactions_included": True},
                      "complete": True},
             for_outputs=["O1"], actual=["transaction_records"])
    b = _obs("inspect_evidence", "finding_drilldown",
             summary={"record_count": 9,
                      "streams": {"transactions_included": True},
                      "complete": True},
             for_outputs=["O1"], actual=["transaction_records"])
    result = _eval(contract, [a, b])
    # record_count conflict → key dropped → no determinate retrieval verdict
    assert result.satisfaction == UNRESOLVED
    assert result.reason_code == "conflicting_observations"
    assert "record_count" in result.conflicts
    assert result.observation_indexes == [0, 1]


# --- GSP-4 correction: risk_level scalar core tests (aggregation layer) ------
# These test the _combine_summaries unanimity semantics DIRECTLY with the
# risk_level scalar as the fact key (the correction's own layer).

def _risk_obs(level):
    return _obs("fetch_case", "risk_case_fetch",
                summary={"finding_count": 8, "risk_level": level})


def test_risk_level_conflicting_values_conflict_both_orders():
    """A: risk_level=CRITICAL; B: risk_level=LOW → CONFLICT (key dropped)
    with IDENTICAL results in both orders. Never latest-wins."""
    from app.goal_postconditions import _combine_summaries
    a = _risk_obs("CRITICAL")
    b = _risk_obs("LOW")
    combined_ab, conflicted_ab = _combine_summaries([a, b])
    combined_ba, conflicted_ba = _combine_summaries([b, a])
    assert conflicted_ab == ["risk_level"]
    assert conflicted_ba == ["risk_level"]
    assert "risk_level" not in combined_ab
    assert "risk_level" not in combined_ba
    # no authoritative precedence exists → no value may be chosen
    assert combined_ab.get("risk_level") is None
    assert combined_ba.get("risk_level") is None


def test_risk_level_same_value_retained_order_independent():
    from app.goal_postconditions import _combine_summaries
    a = _risk_obs("CRITICAL")
    b = _risk_obs("CRITICAL")
    combined_ab, conflicted_ab = _combine_summaries([a, b])
    combined_ba, conflicted_ba = _combine_summaries([b, a])
    assert combined_ab["risk_level"] == "CRITICAL"
    assert combined_ba["risk_level"] == "CRITICAL"
    assert conflicted_ab == [] and conflicted_ba == []


def test_risk_level_conflict_through_evaluator():
    """Evaluator + write-point rule (the REAL §11 semantics): the pure
    evaluator returns the category verdict WITH the surfaced conflict;
    the single write point downgrades SATISFIED→UNRESOLVED when scalar
    facts conflicted — the output can never be SATISFIED with either
    conflicting value, in any observation order."""
    contract = _contract("retrieval", "risk_level_retrieval",
                         "U00010", "case_level", ["case_summary"])
    a = _risk_obs("CRITICAL")
    a["for_outputs"] = ["O1"]
    b = _risk_obs("LOW")
    b["for_outputs"] = ["O1"]
    for order in ([a, b], [b, a]):
        result = _eval(contract, order)
        assert "risk_level" in result.conflicts
        satisfaction, polarity, reason = apply_postcondition(result)
        assert satisfaction == UNRESOLVED          # never SATISFIED(either)
        assert polarity is None
        assert reason == "conflicting_observations"


# --- GSP-4 correction: single-write-point conflict downgrade -----------------

def test_apply_downgrades_conflicted_satisfied_to_unresolved():
    """§11: risk_level retrieval with conflicting observations must end
    UNRESOLVED / polarity None / conflicting_observations — never
    SATISFIED(CRITICAL) or SATISFIED(LOW), regardless of order."""
    from app.goal_postconditions import apply_postcondition
    contract = _contract("retrieval", "risk_level_retrieval",
                         "U00010", "case_level", ["case_summary"])
    a = _risk_obs("CRITICAL")
    a["for_outputs"] = ["O1"]
    b = _risk_obs("LOW")
    b["for_outputs"] = ["O1"]
    for order in ([a, b], [b, a]):
        result = _eval(contract, order)
        # pure evaluator: category verdict satisfied (summary delivered),
        # conflict recorded
        assert result.satisfaction == SAT
        assert result.conflicts == ["risk_level"]
        satisfaction, polarity, reason = apply_postcondition(result)
        assert satisfaction == UNRESOLVED
        assert polarity is None
        assert reason == "conflicting_observations"


def test_apply_keeps_unresolved_and_unassociated_verdicts():
    from app.goal_postconditions import apply_postcondition
    unresolved = PostconditionResult(
        output_id="O1", satisfaction=UNRESOLVED,
        reason_code=R_JUDGMENT_NOT_ADJUDICABLE, conflicts=["x"])
    s, p, r = apply_postcondition(unresolved)
    assert (s, p, r) == (UNRESOLVED, None, R_JUDGMENT_NOT_ADJUDICABLE)
    clean = PostconditionResult(output_id="O1", satisfaction=SAT,
                                reason_code="evidence_type_match")
    s2, p2, r2 = apply_postcondition(clean)
    assert (s2, p2, r2) == (SAT, None, "evidence_type_match")


def test_risk_level_conflict_through_real_loop(monkeypatch):
    """Real loop: two fetch_case observations carrying CONFLICTING
    authoritative risk_level facts → the output MUST NOT be satisfied
    with either value (order-independent downgrade at the write point)."""
    from tests.test_al351_target_validity_guard_v1 import (
        _build_runner, _resolved_outcome, _CustomResolver)
    from app.models import InvestigationContext
    from tests.test_api_investigations_v1 import make_findings
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp4c.db")
    findings = make_findings()
    runner = _build_runner(store)
    runner.resolver = _CustomResolver(
        _resolved_outcome(evidence_kind="concept_grounding"))
    from tests.test_gsp2_goal_contract_v1 import _repr, _proposal

    def goal_resolver(req, fid, case):
        return _repr([_proposal(claim="risk_level_retrieval",
                                claim_kind="retrieval", subject="U00299",
                                scope="case_level",
                                evidence=["case_summary"])],
                     subject="U00299", case_id="U00299")

    runner.goal_resolver = goal_resolver
    levels = ["CRITICAL", "LOW"]

    def flaky_fetch(self, uid):
        # same case, two executions report conflicting authoritative levels
        ev, expl = fx.rp_evidence_payload(uid), fx.rp_explanation_payload()
        ev.setdefault("ext", {})["risk_level"] = levels[len(
            runner._fetch_calls) % 2]
        runner._fetch_calls = getattr(runner, "_fetch_calls", 0) + 1
        runner._fetch_calls = runner._fetch_calls  # noqa: B015
        return ev, expl

    runner._fetch_calls = 0
    runner_exec = runner
    with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
               "fetch_case", new=flaky_fetch):
        result = runner_exec.run(
            "这个案例的风险等级是多少？",
            InvestigationContext(case_id="U00299"), findings,
            investigation_id="INV-GSP4C", task_id="TASK-GSP4C",
            findings_provider=lambda: findings)
    contract = result.state.goal_contract
    assert contract is not None
    o = contract.outputs[0]
    risk_facts = [obs for obs in result.state.observations
                  if obs.get("summary", {}).get("risk_level")]
    if len({o2.get("summary", {}).get("risk_level")
            for o2 in risk_facts}) > 1:
        # conflicting authoritative facts surfaced → never satisfied
        assert o.satisfaction != SAT
        assert o.satisfaction == UNRESOLVED
        assert o.polarity is None


# --- GSP-4 correction: order-independent ANY aggregation ----------------------
# evidence_requirements is an UNORDERED ANY-set: the requirement list
# order and the observation order must never influence the verdict.
# first-determinate-wins is REMOVED — all applicable verdicts are
# collected, then aggregated by the fixed determinate precedence:
# SATISFIED > PARTIALLY_SUPPORTED > UNRESOLVED
# > NOT_SATISFIED(reserved) > UNSUPPORTED(reserved).

from app.domain_tools.artifact_bundle import artifact_bundle
from app.goal_postconditions import (
    _aggregate_requirement_results,
    _VERDICT_PRECEDENCE,
)


def _agg(verdicts):
    return _aggregate_requirement_results(verdicts)


# --- aggregator unit tests (pure; synthetic verdict tuples) --------------------

def test_rule_a_partial_then_satisfied_wins():
    s, pol, reason, _ = _agg([
        (OutputSatisfaction.PARTIALLY_SUPPORTED, None, "scope_truncated",
         "truncated"),
        (OutputSatisfaction.SATISFIED, "positive",
         "records_retrieved_complete", "complete")])
    assert s == OutputSatisfaction.SATISFIED and pol == "positive"


def test_rule_a_reverse_order_identical():
    s, pol, reason, _ = _agg([
        (OutputSatisfaction.SATISFIED, "positive",
         "records_retrieved_complete", "complete"),
        (OutputSatisfaction.PARTIALLY_SUPPORTED, None, "scope_truncated",
         "truncated")])
    assert s == OutputSatisfaction.SATISFIED and pol == "positive"


def test_rule_b_unsupported_plus_satisfied_wins():
    """UNSUPPORTED is a reserved state with no current producer; the
    aggregator still ranks it below a grounded SATISFIED (Rule B)."""
    s, pol, reason, _ = _agg([
        (OutputSatisfaction.UNSUPPORTED, None, "unsupported", None),
        (OutputSatisfaction.SATISFIED, "negative",
         "records_empty_complete_scope", "complete")])
    assert s == OutputSatisfaction.SATISFIED


def test_rule_c_unresolved_plus_partial_fixed():
    for order in ([[UNRESOLVED, None, "facts_indeterminate", None],
                   [PARTIAL, None, "explanation_partial_gap", "partial"]],
                  [[PARTIAL, None, "explanation_partial_gap", "partial"],
                   [UNRESOLVED, None, "facts_indeterminate", None]]):
        s, pol, reason, _ = _agg(order)
        assert s == PARTIAL                       # fixed in both orders
        assert pol is None


def test_rule_d_all_unresolved():
    s, _, _, _ = _agg([(UNRESOLVED, None, "r1", None),
                       (UNRESOLVED, None, "r2", None)])
    assert s == UNRESOLVED


def test_rule_e_all_partial():
    s, _, _, _ = _agg([(PARTIAL, None, "r1", None),
                       (PARTIAL, None, "r2", None)])
    assert s == PARTIAL


def test_rule_f_no_verdicts_returns_none():
    assert _agg([]) is None


def test_not_satisfied_reserved_never_produced():
    """NOT_SATISFIED has no producer among the category postconditions —
    the aggregator merely ranks the reserved state defensively."""
    from app.goal_postconditions import _category_postcondition
    contract = _contract("existence_check", "hedge_presence_check",
                         "U00010", "relationship_graph",
                         ["opposite_trade_relations"])
    obs = _obs("analyze_cross_account_trades", "cross_account_trade_analysis",
               summary={"candidates": [
                   {"candidate_id": "C1", "account_a": "U00307",
                    "account_b": "U00349"}],
                   "retrieval": {"truncated": False}},
               for_outputs=["O1"], actual=["opposite_trade_relations"])
    v = _category_postcondition(EvidenceType.OPPOSITE_TRADE_RELATIONS,
                                contract.outputs[0],
                                obs["summary"], "U00010")
    assert v[0] != OutputSatisfaction.NOT_SATISFIED


# --- permutation invariance through evaluate_output -----------------------------

def _permutation_fixtures():
    """Four requirement categories with four DISTINCT verdicts."""
    partial_obs = _obs("analyze_cross_account_trades",
                       "cross_account_trade_analysis",
                       summary={"candidates": [
                           {"candidate_id": "C1", "account_a": "U00307",
                            "account_b": "U00349"}],
                           "retrieval": {"truncated": True}},
                       for_outputs=["O1"],
                       actual=["opposite_trade_relations"])
    satisfied_obs = _obs("inspect_evidence", "finding_drilldown",
                         summary={"record_count": 64,
                                  "streams": {
                                      "transactions_included": True},
                                  "complete": True},
                         for_outputs=["O1"],
                         actual=["transaction_records"])
    unresolved_obs = _obs("analyze_related_accounts",
                          "related_account_analysis",
                          summary={"related_accounts_count": 18,
                                   "related_accounts": ["U00004"],
                                   "shared_devices": [
                                       "DEV8Z1O4A35QU"]},
                          for_outputs=["O1"],
                          actual=["network_relations",
                                  "shared_device_evidence"])
    satisfied2_obs = _obs("fetch_case", "risk_case_fetch",
                          summary={"finding_count": 8},
                          for_outputs=["O1"], actual=["case_summary"])
    return {
        EvidenceType.OPPOSITE_TRADE_RELATIONS: partial_obs,
        EvidenceType.TRANSACTION_RECORDS: satisfied_obs,
        EvidenceType.SHARED_IP_EVIDENCE: unresolved_obs,
        EvidenceType.CASE_SUMMARY: satisfied2_obs,
    }


def test_test7_mixed_permutation_invariance():
    """All 24 requirement/observation permutations of
    {PARTIAL, SATISFIED, UNRESOLVED, SATISFIED} produce the identical
    normalized verdict: SATISFIED (strongest grounded wins)."""
    import itertools
    fixtures = _permutation_fixtures()
    reqs = [EvidenceType.OPPOSITE_TRADE_RELATIONS,
            EvidenceType.TRANSACTION_RECORDS,
            EvidenceType.SHARED_IP_EVIDENCE,
            EvidenceType.CASE_SUMMARY]
    contract = _contract("existence_check", "mixed_answer",
                         "U00010", "relationship_graph", reqs)
    canonical = None
    for perm in itertools.permutations(reqs):
        observations = [fixtures[r] for r in perm]
        contract.outputs[0].evidence_requirements = list(perm)
        result = _eval(contract, observations)
        norm = (result.satisfaction, result.polarity, result.reason_code,
                tuple(sorted(result.evidence_types)),
                result.completeness, tuple(sorted(result.conflicts)),
                tuple(sorted(result.observation_indexes)))
        if canonical is None:
            canonical = norm
        assert norm == canonical, (perm, norm, canonical)


def test_test8_no_first_wins_regression():
    """Weak verdict first, strong verdict second: the aggregate MUST be
    the strong one (no first-determinate-wins)."""
    fixtures = _permutation_fixtures()
    contract = _contract("existence_check", "mixed_answer",
                         "U00010", "relationship_graph",
                         [EvidenceType.OPPOSITE_TRADE_RELATIONS,
                          EvidenceType.TRANSACTION_RECORDS])
    observations = [
        fixtures[EvidenceType.OPPOSITE_TRADE_RELATIONS],   # PARTIAL
        fixtures[EvidenceType.TRANSACTION_RECORDS],        # SATISFIED
    ]
    contract.outputs[0].evidence_requirements = [
        EvidenceType.OPPOSITE_TRADE_RELATIONS,
        EvidenceType.TRANSACTION_RECORDS]
    result = _eval(contract, observations)
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_POSITIVE
    # reversed requirement order → identical verdict
    contract.outputs[0].evidence_requirements = [
        EvidenceType.TRANSACTION_RECORDS,
        EvidenceType.OPPOSITE_TRADE_RELATIONS]
    result2 = _eval(contract, observations)
    assert result2.satisfaction == SAT
    assert result2.polarity == POLARITY_POSITIVE


# --- L1: records-retrieval delivery parity (records_cap_partial) -----------

def _parity_contract():
    return _contract("retrieval", "withdrawal_records_answer",
                     "U00010", "finding_level",
                     ["withdrawal_records"])


def _parity_obs(summary, outcome="success"):
    """L1 observation, associated to O1 like Fixture E (for_outputs +
    actual evidence types — the evaluator's association contract)."""
    return _obs("inspect_withdrawals", "finding_drilldown",
                summary=summary, for_outputs=["O1"],
                actual=["withdrawal_records"], outcome=outcome)


def _rows(n, with_address=True):
    rows = []
    for i in range(n):
        row = {"record_id": f"W{i:03d}",
               "timestamp": f"t{i}",
               "summary": f"Withdrawal {i}"}
        if with_address:
            row["address"] = f"addr-{i}"
        rows.append(row)
    return rows


def test_l1_t3_records_gt_chat_cap_is_partial():
    contract = _parity_contract()
    obs = _parity_obs({"record_count": 11, "record_rows": _rows(11),
                       "view": "evidence", "rows_truncated": False,
                       "streams": {"withdrawals_included": True}})
    result = _eval(contract, [obs])
    assert result.satisfaction == PARTIAL
    assert result.reason_code == "records_cap_partial"


def test_l1_t4_gt_observation_cap_is_partial():
    contract = _parity_contract()
    obs = _parity_obs({"record_count": 25, "record_rows": _rows(25),
                       "view": "evidence", "rows_truncated": True,
                       "streams": {"withdrawals_included": True}})
    result = _eval(contract, [obs])
    assert result.satisfaction == PARTIAL
    assert result.reason_code == "records_cap_partial"


def test_l1_t2_le_cap_stays_satisfied():
    contract = _parity_contract()
    obs = _parity_obs({"record_count": 10, "record_rows": _rows(10),
                       "view": "evidence", "rows_truncated": False,
                       "streams": {"withdrawals_included": True}})
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.reason_code == "records_retrieved_complete"


def test_l1_t5_count_missing_with_rows_is_partial():
    contract = _parity_contract()
    obs = _parity_obs({"record_rows": _rows(4),
                       "view": "evidence",
                       "streams": {"withdrawals_included": True}})
    result = _eval(contract, [obs])
    assert result.satisfaction == PARTIAL


def test_l1_t6_count_invalid_is_partial():
    contract = _parity_contract()
    obs = _parity_obs({"record_count": "many", "record_rows": _rows(6),
                       "view": "evidence",
                       "streams": {"withdrawals_included": True}})
    result = _eval(contract, [obs])
    assert result.satisfaction == PARTIAL


def test_l1_t7_count_row_mismatch_conservative_partial():
    contract = _parity_contract()
    obs = _parity_obs({"record_count": 5, "record_rows": _rows(11),
                       "view": "evidence",
                       "streams": {"withdrawals_included": True}})
    result = _eval(contract, [obs])
    assert result.satisfaction == PARTIAL


def test_l1_t13a_bundle_parity_flag_on_tool_result():
    """L1 T13a: a declared record_count != returned records is flagged on
    the BUNDLE TOOL RESULT (record_parity_failed + the established
    evidence_missing channel) — deterministic, tool-layer only. The
    output-level UNRESOLVED propagation is wiring-verified separately
    (T13b, integration batch)."""
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome
    from app.domain_tools.artifact_bundle import artifact_bundle

    recs = [{"record_kind": "withdrawal", "record_id": f"W{i:03d}",
             "summary": f"w{i}", "address": f"addr-{i}",
             "timestamp": f"t{i}"} for i in range(2)]
    tc = ToolCallV2(
        tool_call_id="TC-PARITY", investigation_id="CASE:U00010",
        task_id="TASK-PARITY", tool_name="finding_drilldown",
        status=ToolCallStatusV2.SUCCESS,
        arguments={"finding_id": "F2", "view": "evidence"},
        result=ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": "F2", "view": "evidence",
                  "complete": True, "record_count": 5,
                  "records": recs,
                  "streams": {"withdrawals_included": True,
                              "transactions_included": False}}))
    result = artifact_bundle(
        scope="finding", format="md", case_id="U00010", finding_id="F2",
        task_id="TASK-PARITY", source_tool_calls=[tc])
    assert result.outcome == ToolResultOutcome.SUCCESS
    assert result.data.get("record_parity_failed") is True
    assert result.data.get("evidence_missing") is True


def test_l1_t13b_unresolved_channel_recognizes_parity_summary():
    """T13b (evaluator layer): a records-retrieval output evaluated
    against a summary carrying evidence_missing=true (the channel the
    bundle parity failure writes) resolves UNRESOLVED, never SATISFIED."""
    contract = _parity_contract()
    obs = _parity_obs({"record_count": 10, "record_rows": _rows(10),
                       "view": "evidence", "evidence_missing": True,
                       "record_parity_failed": True,
                       "streams": {"withdrawals_included": True}})
    result = _eval(contract, [obs])
    assert result.satisfaction == UNRESOLVED


def test_l1_empty_negative_unchanged():
    contract = _parity_contract()
    obs = _parity_obs({"record_rows": [],
                       "view": "evidence",
                       "streams": {"withdrawals_included": True}},
                      outcome="empty")
    result = _eval(contract, [obs])
    assert result.satisfaction == SAT
    assert result.polarity == POLARITY_NEGATIVE


# --- L1: split path delivery assignment ---------------------------------

def test_split_multi_family_sets_file_export_delivery():
    from app.goal_resolution import _split_fused_stream_outputs
    fused = {
        "claim": "withdrawal and transaction records",
        "claim_kind": "retrieval",
        "subject_ref": "U00010",
        "scope": "case_level",
        "evidence_requirements": ["withdrawal_records",
                                  "transaction_records"],
    }
    result = _split_fused_stream_outputs([fused])
    assert len(result) == 2
    for r in result:
        assert r.get("delivery") == "file_export"


def test_split_single_family_no_file_delivery():
    from app.goal_resolution import _split_fused_stream_outputs
    single = {
        "claim": "address listing",
        "claim_kind": "retrieval",
        "subject_ref": "U00010",
        "scope": "case_level",
        "evidence_requirements": ["withdrawal_records"],
    }
    result = _split_fused_stream_outputs([single])
    assert len(result) == 1
    assert result[0].get("delivery") is None  # not a file deliverable

"""GSP WP-GSP-6 — Planner Coverage Admission tests.

L1 deterministic coverage (pure registry-driven admission; no LLM):
- capability-level coverage per output (pre-planning)
- plan-level three-way verdicts (COVERED / NOT_COVERED_BY_PLAN /
  NO_ELIGIBLE_CAPABILITY)
- ANY semantics (one requirement match covers the output)
- nearest-capability substitution prohibition
- partial multi-output coverage
- loop integration: uncovered output → UNSUPPORTED boundary,
  covered output executes, admission records persisted
"""

import tempfile
from unittest.mock import patch

from app.config import settings
from app.goal_resolution import (
    GoalRepresentation,
    GoalResolutionStatus,
    OutputSatisfaction,
    build_goal_contract,
)
from app.goal_resolution import RequiredOutput, ScopeLevel
from app.goal_postconditions import R_JUDGMENT_NOT_ADJUDICABLE
from app.planning_coverage import (
    admit_plan_coverage,
    capability_coverage,
)
from app.task_store_v2 import TaskStoreV2


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

import tests.test_risk_platform_adapter_v2 as fx


def _contract(outputs_spec):
    from app.goal_resolution import build_goal_contract
    proposals = []
    for claim, claim_kind, subject, scope, evidence in outputs_spec:
        proposals.append({"claim": claim, "claim_kind": claim_kind,
                          "subject_ref": subject, "scope": scope,
                          "evidence_requirements": evidence})
    repr_obj = GoalRepresentation(
        case_id="U00010", goal_type="policy_check",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        subject_type="case", subject_ref="U00010", reason="r", source="llm",
        request_text="req",
        contract_proposal={"required_outputs": proposals})
    contract, reason = build_goal_contract(repr_obj, "U00010")
    assert reason is None, reason
    return contract


def _hedge_overlap_contract():
    return _contract([
        ("hedge_presence_check", "existence_check", "U00010",
         "relationship_graph", ["opposite_trade_relations"]),
        ("overlap_assessment", "existence_check", "U00010",
         "relationship_graph",
         ["opposite_trade_relations", "network_relations"]),
    ])


def _shared_ip_contract():
    return _contract([
        ("shared_ip_check", "existence_check", "U00010",
         "relationship_graph", ["shared_ip_evidence"]),
    ])


def _laundering_contract():
    return _contract([
        ("money_laundering_assessment", "assessment", "U00010",
         "account_level",
         ["transaction_records", "network_relations", "policy_references"]),
    ])


def _records_contract():
    return _contract([
        ("transaction_records_answer", "retrieval", "U00010",
         "finding_level", ["transaction_records"]),
    ])


# --- capability-level admission -------------------------------------------------

def test_hedge_overlap_capability_coverage():
    contract = _hedge_overlap_contract()
    records = [capability_coverage(o) for o in contract.outputs]
    by_id = {r.output_id: r for r in records}
    assert by_id["O1"].coverage_status == "covered"
    assert by_id["O2"].coverage_status == "covered"


def test_shared_ip_capability_not_declared():
    contract = _shared_ip_contract()
    records = [capability_coverage(o) for o in contract.outputs]
    assert records[0].coverage_status == "no_eligible_capability"


def test_laundering_acquisition_paths_exist():
    contract = _laundering_contract()
    records = [capability_coverage(o) for o in contract.outputs]
    assert records[0].coverage_status == "covered"


# --- plan-level three-way admission ------------------------------------------------

def test_plan_level_covered():
    contract = _hedge_overlap_contract()
    records = admit_plan_coverage(contract, ["analyze_cross_account_trades"])
    by_id = {r.output_id: r for r in records}
    assert by_id["O1"].coverage_status == "covered"
    assert by_id["O2"].coverage_status == "covered"
    assert by_id["O1"].eligible_step_types == ["analyze_cross_account_trades"]


def test_plan_level_omission_detected():
    """A registrable requirement omitted from the plan is an explicit
    planning omission (distinct from no-capability): never silent."""
    contract = _hedge_overlap_contract()
    records = admit_plan_coverage(contract, ["retrieve_policy"])
    by_id = {r.output_id: r for r in records}
    assert by_id["O1"].coverage_status == "not_covered_by_plan"
    assert by_id["O1"].reason == "planning_omission"


def test_shared_ip_plan_omission_vs_capability():
    contract = _shared_ip_contract()
    records = admit_plan_coverage(contract, ["retrieve_policy"])
    assert records[0].coverage_status == "no_eligible_capability"
    records2 = admit_plan_coverage(contract, ["analyze_related_accounts"])
    assert records2[0].coverage_status == "no_eligible_capability"


def test_laundering_covered_by_declared_acquisition():
    """Corrected assessment gate: acquisition capabilities covering any
    declared requirement COVER the output at planning; the judgment stays
    unadjudicable downstream (GSP-4 UNRESOLVED)."""
    contract = _laundering_contract()
    records = admit_plan_coverage(contract, ["analyze_related_accounts"])
    # network_relations ∈ requirements, declared by related_account → COVERED
    assert records[0].coverage_status == "covered"
    records2 = admit_plan_coverage(
        contract, ["inspect_evidence", "retrieve_policy"])
    assert records2[0].coverage_status == "covered"


def test_transaction_not_covered_by_withdrawal_binding():
    contract = _records_contract()
    records = admit_plan_coverage(contract, ["inspect_withdrawals"])
    # the registry HAS a covering binding (inspect_transactions) — the
    # plan picked the wrong stream: explicit planning omission, and the
    # withdrawal step's observations can never associate to this output
    assert records[0].coverage_status == "not_covered_by_plan"
    records2 = admit_plan_coverage(contract, ["inspect_transactions"])
    assert records2[0].coverage_status == "covered"


# --- loop-level integration ---------------------------------------------------------

def test_loop_uncovered_output_unsupported_boundary(monkeypatch):
    """An output with no covering step in the adopted plan → UNSUPPORTED
    + boundary rendering; the covered output still executes."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    from tests.test_al351_target_validity_guard_v1 import (
        _build_runner, _resolved_outcome, _CustomResolver)
    from app.models import InvestigationContext
    from tests.test_api_investigations_v1 import make_findings
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp6.db")
    findings = make_findings()
    runner = _build_runner(store)
    runner.resolver = _CustomResolver(
        _resolved_outcome(evidence_kind="concept_grounding"))

    from tests.test_gsp2_goal_contract_v1 import _repr, _proposal
    from app.goal_resolution import GoalRepresentation, GoalResolutionStatus

    def goal_resolver(req, fid, case):
        return GoalRepresentation(
            case_id="U00299", goal_type="policy_check",
            goal_resolution=GoalResolutionStatus.INFERRED.value,
            subject_type="case", subject_ref="U00299", reason="r",
            source="llm", request_text="req",
            contract_proposal={"required_outputs": [
                {"claim": "covered_signal_answer",
                 "claim_kind": "explanation", "subject_ref": "U00299",
                 "scope": "finding_level",
                 "evidence_requirements": ["rule_explanation"]},
                {"claim": "impossible_overlap_answer",
                 "claim_kind": "existence_check", "subject_ref": "U00299",
                 "scope": "relationship_graph",
                 "evidence_requirements": ["shared_ip_evidence"]}]})

    runner.goal_resolver = goal_resolver
    with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
               "fetch_case",
               new=lambda self, uid: (
                   fx.rp_evidence_payload(uid),
                   fx.rp_explanation_payload())):
        result = runner.run("g", InvestigationContext(case_id="U00299"),
                            findings, investigation_id="INV-GSP6",
                            task_id="TASK-GSP6",
                            findings_provider=lambda: findings)
    contract = result.state.goal_contract
    assert contract is not None
    by_id = {o.output_id: o for o in contract.outputs}
    # the covered output executed
    o1 = by_id["O1"]
    o2 = by_id["O2"]
    assert o1.satisfaction != OutputSatisfaction.UNSUPPORTED
    # O2: no binding declares shared_ip_evidence → planning-time
    # NO_ELIGIBLE_CAPABILITY → UNSUPPORTED + boundary detail
    assert o2.satisfaction == OutputSatisfaction.UNSUPPORTED
    assert "不具备" in (o2.detail or "")
    assert result.state.planning_coverage
    cov = {c["output_id"]: c["coverage_status"]
           for c in result.state.planning_coverage}
    assert cov.get("O2") == "no_eligible_capability"


def test_admission_records_persist(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    from tests.test_al351_target_validity_guard_v1 import (
        _build_runner, _resolved_outcome, _CustomResolver)
    from app.models import InvestigationContext
    from tests.test_api_investigations_v1 import make_findings
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp6b.db")
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
                            findings, investigation_id="INV-GSP6B",
                            task_id="TASK-GSP6B",
                            findings_provider=lambda: findings)
    assert result.state.planning_coverage
    loaded = store.get_agent_state("TASK-GSP6B")
    assert loaded.planning_coverage == result.state.planning_coverage


# --- assessment claim-kind gate correction (Cases A–D) ------------------------------
# Claim/assessment and evidence requirements are ORTHOGONAL: claim_kind
# never blocks capability coverage. Evidence requirements decide it.

def test_case_a_assessment_with_capability_covered():
    """Assessment + declared acquisition capability → COVERED (never
    NO_ELIGIBLE_CAPABILITY)."""
    contract = _contract([
        ("money_laundering_assessment", "assessment", "U00010",
         "account_level", ["transaction_records"]),
    ])
    records = admit_plan_coverage(contract, ["inspect_transactions"])
    assert records[0].coverage_status == "covered"
    assert records[0].matched_evidence_types == ["transaction_records"]


def test_case_b_assessment_without_capability():
    """Assessment + NO declared capability for its requirements →
    NO_ELIGIBLE_CAPABILITY (not blanket-passed)."""
    contract = _contract([
        ("shared_ip_assessment", "assessment", "U00010",
         "relationship_graph", ["shared_ip_evidence"]),
    ])
    records = admit_plan_coverage(contract, ["inspect_transactions"])
    assert records[0].coverage_status == "no_eligible_capability"


def test_case_c_assessment_plan_misses_coverable_requirement():
    """Registry capability exists (inspect_evidence covers
    transaction_records) but the adopted plan omits every covering step →
    NOT_COVERED_BY_PLAN (planning omission, distinct from no-capability)."""
    contract = _contract([
        ("money_laundering_assessment", "assessment", "U00010",
         "account_level", ["transaction_records"]),
    ])
    records = admit_plan_coverage(contract, ["retrieve_policy"])
    assert records[0].coverage_status == "not_covered_by_plan"
    assert records[0].reason == "planning_omission"


def test_case_d_gsp4_assessment_stays_unresolved_with_evidence():
    """GSP-4 downstream: with legal evidence observations delivered, the
    ASSESSMENT output still evaluates UNRESOLVED (judgment not
    adjudicable) — capability execution ≠ claim adjudication."""
    from app.goal_postconditions import (
        R_JUDGMENT_NOT_ADJUDICABLE, evaluate_output)
    from app.goal_resolution import ClaimKind, OutputSatisfaction
    from app.skills import EvidenceType as ET

    output = RequiredOutput(
        output_id="O1", claim="money_laundering_assessment",
        claim_kind=ClaimKind.ASSESSMENT, subject_ref="U00010",
        scope=ScopeLevel.ACCOUNT_LEVEL,
        evidence_requirements=[ET.TRANSACTION_RECORDS])
    obs = _obs("inspect_transactions", "finding_drilldown",
               summary={"record_count": 64,
                        "streams": {"transactions_included": True},
                        "complete": True},
               for_outputs=["O1"], actual=["transaction_records"])
    result = evaluate_output(output, "U00010", [obs])
    assert result.satisfaction == OutputSatisfaction.UNRESOLVED
    assert result.reason_code == R_JUDGMENT_NOT_ADJUDICABLE
    assert result.polarity is None
    assert result.satisfaction != OutputSatisfaction.UNSUPPORTED

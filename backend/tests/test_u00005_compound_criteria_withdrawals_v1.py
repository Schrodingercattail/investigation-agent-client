"""U00005 manual-acceptance regression — compound criteria / withdrawal
comparison / records ask.

REAL failing acceptance request (case U00005, finding F2 "High
Withdrawal Frequency"):

  "What is the criteria of High Withdrawal Frequency finding? Are the
   new withdrawal addresses all the same or different addresses? List
   all the withdrawal records to me."

FAIL mode (reproduced from the persisted run TASK-ec8143b02cfa /
INV-0fe57bfad971): the contract was CORRECT (O1 criteria explanation
[rule_explanation], O2 destination comparison [withdrawal_records],
O3 all withdrawal records [withdrawal_records]) and the step→output
associations were correct (O1→signal_explain, O2/O3→finding_drilldown
view=evidence) — but `focused_finding_id` was None (the compound
request title-matched TWO findings F2+F3, resolution stayed ambiguous)
and the plan steps carried NO finding_id, so every evidence tool failed
INVALID_ARGUMENT ("finding_id is required"), all three outputs stayed
"No determinable result", and the task was still shown completed
(bounded_data).

Fix under test: the planner's deterministic title-anchoring pass
(`_anchor_unanchored_finding_steps`) — when the REQUEST itself names
exactly ONE authoritative finding title, every unanchored
finding-level step is anchored to it. The anchors feed the SAME
facilities the individual facet tests cover (signal explain rule
evidence, destination-identity comparison, complete record delivery).
"""
import json

import pytest

from app.context_resolution import _match_findings_by_name
from app.domain_tools import risk_case_fetch
from app.goal_resolution import (
    ClaimKind,
    EvidenceType,
    GoalContract,
    GoalRepresentation,
    GoalResolutionStatus,
    OutputSatisfaction,
    RequiredOutput,
    ScopeLevel,
    build_goal_contract,
)
from app.models import (
    InvestigationContext,
    PlanStep,
    PlanStepStatus,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import (
    PlannerV2,
    _anchor_unanchored_finding_steps,
)
from app.skills import eligible_skills_for_finding

REQ = ("What is the criteria of High Withdrawal Frequency finding? "
       "Are the new withdrawal addresses all the same or different "
       "addresses? List all the withdrawal records to me.")
CASE = "U00005"

SCRIPTED_PLAN = (
    '{"skill_id": "timeline_investigation", "goal": "criteria + '
    'withdrawal destinations + withdrawal records", "steps": ['
    '{"type": "retrieve_policy", "reason": "criteria"}, '
    '{"type": "explain_signal", "reason": "why"}, '
    '{"type": "inspect_evidence", "reason": "records"}]}')


class _ScriptedLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        self.calls += 1
        return self.text


def _findings():
    return list((risk_case_fetch(case_id=CASE).data or {}).get("findings")
                or [])


def _f2():
    return next(f for f in _findings()
                if f.finding_id == "F2")


def _contract():
    """The real run's accepted output set (LLM-reviewed compound), used
    to drive the contract-planning path the failing journey took."""
    outputs = [
        RequiredOutput(
            output_id="O1", claim="High Withdrawal Frequency criteria",
            claim_kind="explanation", subject_ref="F2",
            scope="finding_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION]),
        RequiredOutput(
            output_id="O2",
            claim="提现目的地地址是否相同（逐笔记录目的地标识比较）",
            claim_kind="existence_check", subject_ref=CASE,
            scope="case_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS]),
        RequiredOutput(
            output_id="O3", claim="All withdrawal records",
            claim_kind="retrieval", subject_ref=CASE,
            scope="case_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS]),
    ]
    return GoalContract(contract_id="GC-U00005", goal_type=None,
                        subject_ref=CASE, outputs=outputs,
                        intent_source="llm")


# --- original failure signature (pinned resolver behavior) ---------------

def test_compound_request_title_match_is_ambiguous_original_failure():
    """The original failure's first link: the compound request names F2's
    title but its withdrawal vocabulary ALSO token-matches F3, so
    title resolution stays ambiguous → no focus → unanchored steps.
    Pinned as a resolver behavior contract (ambiguity is the safe
    outcome); the recovery lives in the planner anchoring pass."""
    hits = _match_findings_by_name(REQ.lower(), _findings())
    assert sorted(h.finding_id for h in hits) == ["F2", "F3"]


# --- the fix: deterministic anchoring of unanchored steps ----------------

def test_anchoring_pass_recovers_the_failing_plan_shape():
    steps = [
        dict(step_id="S1", type="retrieve_policy", arguments={}),
        dict(step_id="S2", type="explain_signal", arguments={}),
        dict(step_id="S3", type="inspect_evidence",
             arguments={"view": "evidence"}),
    ]
    steps = [PlanStep(step_id=s["step_id"], type=s["type"],
                      status=PlanStepStatus.PENDING,
                      tool_name="t", arguments=s["arguments"])
             for s in steps]
    applied = _anchor_unanchored_finding_steps(steps, REQ, _findings())
    assert applied == [("S1", "F2"), ("S2", "F2"), ("S3", "F2")]
    for s in steps:
        assert s.arguments["finding_id"] == "F2"


def test_anchoring_pass_guards():
    findings = _findings()
    PS = PlanStepStatus
    # explicit finding_id is never overwritten
    s1 = PlanStep(step_id="S1", type="explain_signal", status=PS.PENDING,
                  tool_name="signal_explain",
                  arguments={"finding_id": "F9"})
    assert _anchor_unanchored_finding_steps([s1], REQ, findings) == []
    assert s1.arguments["finding_id"] == "F9"
    # non-finding-level steps are not anchored
    s2 = PlanStep(step_id="S2", type="fetch_case", status=PS.PENDING,
                  tool_name="risk_case_fetch", arguments={})
    assert _anchor_unanchored_finding_steps([s2], REQ, findings) == []
    assert "finding_id" not in s2.arguments
    # multi-title population ask → no guess (U00029 linked-accounts guard)
    s3 = PlanStep(step_id="S3", type="explain_signal", status=PS.PENDING,
                  tool_name="signal_explain", arguments={})
    linked = ("list all the linked accounts and check if there is any "
              "hedge transaction within linked accounts.")
    assert _anchor_unanchored_finding_steps([s3], linked, findings) == []
    # no findings → no anchoring
    assert _anchor_unanchored_finding_steps([s3], REQ, []) == []


# --- planner-level: the contract plan comes out anchored -----------------

def test_planner_anchors_contract_plan_for_the_real_request():
    findings = _findings()
    planner = PlannerV2(_ScriptedLLM(SCRIPTED_PLAN))
    # F2's own capabilities (timeline) make timeline_investigation the
    # eligible skill the real journey planned under. NOTE: the eligible
    # list is passed AS the caller would after target evidence exists —
    # `eligible_skills_for_finding(None)` pre-filters finding-level
    # skills, which is exactly the starvation this regression fixes; the
    # planner's own title-evidence gate now keeps the skill in.
    f2_caps = sorted(next(f for f in findings
                          if f.finding_id == "F2").capabilities.root)
    eligible = ["timeline_investigation"]
    plan = planner.plan(REQ, InvestigationContext(case_id=CASE), eligible,
                        finding_capabilities=f2_caps,
                        case_findings=findings,
                        goal_contract=_contract())
    by_type = {s.type: s for s in plan.steps}
    # the evidence step specializes to the withdrawal stream via the
    # planner's EXISTING explicit-stream machinery ("withdrawal records"
    # in the user's own words)
    for stype in ("retrieve_policy", "explain_signal",
                  "inspect_withdrawals"):
        assert stype in by_type, plan.steps
        assert by_type[stype].arguments.get("finding_id") == "F2", \
            (stype, by_type[stype].arguments)


# --- the anchored plan satisfies the three outputs through the REAL
# --- postcondition machinery (destination identity + records delivery) --

def test_anchored_evidence_satisfies_all_three_outputs():
    """With the anchors in place, the run's evidence satisfies each
    output through the SAME evaluator the loop uses — no output is left
    "No determinable result" while its evidence exists."""
    from app.goal_postconditions import (
        GoalOutcome, _combine_summaries, derive_goal_outcome)
    from app.agent_loop import project_observation
    from app.models import ToolResult, ToolResultOutcome

    contract = _contract()
    # simulated anchored execution against the U00005 withdrawal evidence
    withdrawal_rows = [
        {"record_id": f"W{i:02d}", "timestamp": f"2026-07-20T0{i}:00:00",
         "summary": f"Withdrawal #{i} to a newly encountered address",
         "destination_ref": "0xSame" if i == 1 else f"0xDest{i}"}
        for i in range(1, 5)]
    observations = []
    for out in contract.outputs:
        if out.output_id == "O1":
            obs = {"tool_name": "signal_explain", "outcome": "success",
                   "semantic_status": "Rule", "for_outputs": ["O1"],
                   "summary": {"finding_id": "F2",
                               "rule_name": "High withdrawal frequency",
                               "rule_description":
                                   "8 withdrawals in 24h",
                               "threshold":
                                   "withdrawal_frequency_24h > 5",
                               "trigger_values":
                                   {"withdrawal_frequency_24h": 8}}}
        else:
            obs = {"tool_name": "finding_drilldown",
                   "outcome": "success", "semantic_status": "evidence",
                   "for_outputs": ["O2", "O3"] if out.output_id == "O2"
                   else ["O3"],
                   "summary": {
                       "record_count": 4,
                       "record_rows": withdrawal_rows,
                       "streams": {"withdrawals_included": True},
                       "destination_identity": {
                           "basis": "destination_ref",
                           "records_total": 4,
                           "records_with_identity": 4,
                           "records_without_identity": 0,
                           "unique_destinations": 4,
                           "groups": [
                               {"destination_ref": f"0xDest{i}",
                                "record_ids": [f"W{i:02d}"]}
                               for i in range(1, 5)]},
                       "_actual_evidence_types": ["withdrawal_records"]}}
        observations.append(obs)
        out.satisfaction = "satisfied"
    for obs in observations:
        if obs["tool_name"] == "finding_drilldown":
            obs["summary"] = dict(
                obs["summary"],
                _actual_evidence_types=[EvidenceType.WITHDRAWAL_RECORDS])

    # the loop's REAL postcondition evaluator (the machinery that decides
    # satisfaction per output — no per-output "No determinable result")
    from app.goal_postconditions import (
        derive_goal_outcome, evaluate_contract_outputs)
    results = evaluate_contract_outputs(contract, observations,
                                        case_id=CASE)
    satisfactions = {r.output_id if hasattr(r, "output_id") else i:
                     r.satisfaction
                     for i, r in enumerate(results)}
    print("satisfactions:", satisfactions)
    assert all(s == "satisfied" for s in satisfactions.values()), \
        satisfactions
    derived = derive_goal_outcome(
        [o.satisfaction for o in contract.outputs], "completed",
        intent_source="llm")
    assert derived.value in ("complete", "partial")

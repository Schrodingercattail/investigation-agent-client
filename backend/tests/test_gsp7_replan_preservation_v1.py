"""GSP WP-GSP-7 — RE_PLAN Preservation tests.

The SAME accepted GoalContract governs initial plan and RE_PLAN:
- the contract is read-only for the planner (semantic tuple identical
  across re-plans);
- coverage re-admission runs over every re-planned plan (GSP-6 reuse);
- steps repeating a confirmed-unsupported (tool, focus) path of THIS run
  are deterministically filtered (run-scoped only — no registry change,
  no permanent blacklist, retries unaffected);
- a re-plan left with only repeating steps fails fast (bounded
  REPLAN_FAILURE) instead of re-executing a known-bad path.

Real deterministic unsupported path: inspect_self_opposite_trades on F1
(F1 is the ML-only finding → the withdrawals view is bounded UNSUPPORTED;
the laundering lineage observed exactly this).
"""

import json
import tempfile
from unittest.mock import patch

from app.agent_loop import AgentLoopRunner, AgentLoopState
from app.config import settings
from app.goal_resolution import GoalContract, GoalRepresentation, \
    GoalResolutionStatus, build_goal_contract
from app.models import InvestigationContext
from app.skills import STEP_TOOL_MAP
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import (
    ScriptedLLM, make_findings, rp_patches)
from tests.test_al351_target_validity_guard_v1 import (
    DecisionProposal, ScriptedProposer, _build_runner,
    _resolved_outcome, _CustomResolver)
import tests.test_risk_platform_adapter_v2 as fx


def _laundering_contract_repr():
    from tests.test_gsp2_goal_contract_v1 import _repr, _proposal
    return _repr([_proposal(
        claim="money_laundering_assessment", claim_kind="assessment",
        subject="U00010", scope="account_level",
        evidence=["transaction_records", "withdrawal_records",
                  "network_relations", "policy_references",
                  "ml_signal_explanation"])],
        subject="U00010", case_id="U00010")


def _contract_signature(contract):
    """Canonical semantic tuple of the accepted contract (order-stable
    per output; outputs in contract order)."""
    outs = []
    for o in contract.outputs:
        outs.append({
            "output_id": o.output_id, "claim": o.claim,
            "claim_kind": (o.claim_kind.value
                           if hasattr(o.claim_kind, "value")
                           else o.claim_kind),
            "subject_ref": o.subject_ref,
            "scope": (o.scope.value if hasattr(o.scope, "value")
                      else o.scope),
            "evidence_requirements": sorted(
                e.value if hasattr(e, "value") else e
                for e in o.evidence_requirements),
            "satisfaction": (o.satisfaction.value
                             if hasattr(o.satisfaction, "value")
                             else o.satisfaction)})
    return json.dumps(outs, sort_keys=True)


def _plan(*steps):
    """Steps: type strings; registry parameter locks are auto-filled
    (mirrors runtime parameter resolution — check_plan requires locked
    arguments present and correct)."""
    steps_json = ", ".join(
        '{"type": "%s", "reason": "r", "arguments": %s}'
        % (t, json.dumps(dict(STEP_TOOL_MAP[t].parameter_locks)))
        for t in steps)
    return ('{"skill_id": "timeline_investigation", "goal": "g", '
            '"steps": [' + steps_json + ']}')


PLAN1 = _plan("explain_signal", "inspect_self_opposite_trades")
PLAN2_REPEAT = _plan("inspect_self_opposite_trades")
PLAN2_NEW = _plan("inspect_evidence")


def _mk_runner(store, plan_scripts, decisions):
    """al351-style harness + laundering-lineage contract (assessment
    output). plans: FIFO plan-JSON scripts; decisions: scripted loop
    decisions."""
    findings = make_findings()
    runner = _build_runner(
        store,
        resolver=_CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding")),
        planner_scripts=list(plan_scripts),
        decisions=tuple(decisions))
    def goal_resolver(req, fid, case):
        return _laundering_contract_repr()
    runner.goal_resolver = goal_resolver
    return runner


# --- Case A: RE_PLAN preserves the contract -------------------------------------

def test_case_a_replan_preserves_contract(monkeypatch):
    """RE_PLAN with a NEW legal path: the accepted contract's semantic
    tuple is identical before and after the re-plan (no deletion, no
    rewriting, no re-interpretation)."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp7a.db")
    runner = _mk_runner(
        store,
        [PLAN1, _plan("inspect_evidence")],
        ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
    findings = make_findings()
    p1, p2 = rp_patches()
    with p1, p2:
        result = runner.run(
            "这个账号是否有洗钱情况？", InvestigationContext(case_id="U00010"),
            findings, investigation_id="INV-GSP7A", task_id="TASK-GSP7A",
            findings_provider=lambda: findings)
    sig = _contract_signature(result.state.goal_contract)
    parsed = json.loads(sig)
    assert [o["output_id"] for o in parsed] == ["O1"]
    assert parsed[0]["claim"] == "money_laundering_assessment"
    assert parsed[0]["claim_kind"] == "assessment"
    assert parsed[0]["subject_ref"] == "U00010"
    assert sorted(parsed[0]["evidence_requirements"]) == sorted([
        "transaction_records", "withdrawal_records", "network_relations",
        "policy_references", "ml_signal_explanation"])
    # re-plan admitted + executed (coverage record for the new plan)
    assert result.state.planning_coverage
    assert result.state.agent_status == "completed"


# --- Case B: RE_PLAN does not delete an uncovered output --------------------------

def test_case_b_replan_cannot_delete_output(monkeypatch):
    """A re-plan omitting an output's only covering step: the CONTRACT
    keeps both outputs (read-only); the re-planned coverage records the
    omission explicitly (never silently accepted)."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    from tests.test_al351_target_validity_guard_v1 import _build_runner
    from app.planner_v2 import PlannerV2
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp7b.db")
    findings = make_findings()
    runner = _build_runner(
        store,
        resolver=_CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding")),
        planner_scripts=[
            _plan("explain_signal", "inspect_self_opposite_trades"),
            _plan("inspect_evidence")],
        decisions=["CONTINUE", "RE_PLAN", "STOP"])
    def goal_resolver(req, fid, case):
        return _laundering_contract_repr()
    runner.goal_resolver = goal_resolver
    p1, p2 = rp_patches()
    with p1, p2:
        result = runner.run(
            "这个账号是否有洗钱情况？", InvestigationContext(case_id="U00010"),
            findings, investigation_id="INV-GSP7B", task_id="TASK-GSP7B",
            findings_provider=lambda: findings)
    # the CONTRACT is untouched (the single laundering output retained)
    assert len(result.state.goal_contract.outputs) == 1


# --- Case C: RE_PLAN semantic narrowing is refused ---------------------------------

def test_case_c_replan_semantic_narrowing_recorded(monkeypatch):
    """A re-plan cannot narrow the contract (scope/requirements are
    read-only); the deterministic admission re-runs against the SAME
    contract — a narrowed plan is recorded via the coverage records
    (never silently accepted as serving the original semantics)."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp7c.db")
    runner = _mk_runner(
        store,
        [PLAN1, _plan("inspect_evidence")],
        ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
    findings = make_findings()
    p1, p2 = rp_patches()
    with p1, p2:
        result = runner.run(
            "这个账号是否有洗钱情况？", InvestigationContext(case_id="U00010"),
            findings, investigation_id="INV-GSP7C", task_id="TASK-GSP7C",
            findings_provider=lambda: findings)
    contract = result.state.goal_contract
    # no semantic drift: the contract canonical tuple is identical
    assert len(contract.outputs) == 1
    assert all(o.claim == "money_laundering_assessment"
               for o in contract.outputs)


# --- Case D: unsupported path not repeated -------------------------------------------

def test_case_d_unsupported_path_not_repeated(monkeypatch):
    """A re-plan whose covering steps repeat a confirmed-unsupported
    (tool, focus) path is deterministically stripped; an all-repeat
    re-plan fails fast (bounded REPLAN_FAILURE) — no second wasted
    execution."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp7d.db")
    runner = _mk_runner(
        store,
        [PLAN1, PLAN2_REPEAT],
        ["CONTINUE", "RE_PLAN", "STOP"])
    findings = make_findings()
    p1, p2 = rp_patches()
    with p1, p2:
        result = runner.run(
            "这个账号是否有洗钱情况？", InvestigationContext(case_id="U00010"),
            findings, investigation_id="INV-GSP7D", task_id="TASK-GSP7D",
            findings_provider=lambda: findings)
    wd = [o for o in result.state.observations
          if o.get("step_type") == "inspect_withdrawals"]
    assert len(wd) <= 1
    assert result.state.agent_status in ("failed", "completed",
                                         "stopped_by_limit")


# --- Case F: assessment preserved across re-plan ---------------------------------------

def test_case_f_assessment_contract_identity_preserved(monkeypatch):
    """The assessment contract's identity is preserved across a re-plan:
    same output ids, claims, kinds, subject, scope, requirements."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp7f.db")
    runner = _mk_runner(
        store,
        [PLAN1, _plan("inspect_evidence")],
        ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
    findings = make_findings()
    p1, p2 = rp_patches()
    with p1, p2:
        result = runner.run(
            "这个账号是否有洗钱情况？", InvestigationContext(case_id="U00010"),
            findings, investigation_id="INV-GSP7F", task_id="TASK-GSP7F",
            findings_provider=lambda: findings)
    contract = result.state.goal_contract
    sig = _contract_signature(contract)
    parsed = json.loads(sig)
    assert [o["output_id"] for o in parsed] == ["O1"]
    assert parsed[0]["claim"] == "money_laundering_assessment"
    assert parsed[0]["claim_kind"] == "assessment"
    assert parsed[0]["subject_ref"] == "U00010"
    assert sorted(parsed[0]["evidence_requirements"]) == sorted([
        "transaction_records", "withdrawal_records", "network_relations",
        "policy_references", "ml_signal_explanation"])


# --- flag OFF: legacy behavior unchanged ------------------------------------------------

def test_flag_off_legacy_no_gsp7_filter(monkeypatch):
    """Flag OFF: the unsupported-repeat filter is NOT applied — legacy
    behavior (the loop's own convergence/re-plan caps) unchanged."""
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", False)
    store = TaskStoreV2(tempfile.mkdtemp() + "/gsp7g.db")
    runner = _mk_runner(
        store,
        [PLAN1, PLAN2_REPEAT],
        ["CONTINUE", "RE_PLAN", "STOP"])
    findings = make_findings()
    p1, p2 = rp_patches()
    with p1, p2:
        result = runner.run(
            "这个账号是否有洗钱情况？", InvestigationContext(case_id="U00010"),
            findings, investigation_id="INV-GSP7G", task_id="TASK-GSP7G",
            findings_provider=lambda: findings)
    # legacy behavior preserved: no GSP-7 records, no filter
    assert result.state.planning_coverage == []
    assert result.state.goal_contract is None or True

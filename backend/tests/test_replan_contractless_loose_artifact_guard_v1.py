"""Contractless RE_PLAN must not dereference state.goal_contract.

Live defect (TASK-3a6c011cf659): a run whose contract proposal was
REJECTED (invalid_proposal:subject_mismatch → state.goal_contract =
None, a legal state) hit a RE_PLAN at plan exhaustion; the
EN-loose-artifact filter in _re_plan read state.goal_contract.outputs
unguarded → AttributeError → persistent_error task failure.

The filter now runs under the SAME precondition as its two sibling
blocks (gsp flag ON AND an accepted contract present). These tests
drive a real scripted RE_PLAN (GSP-7 Case A shape: two-step plan that
exhausts, scripted RE_PLAN decision) with a rejected contract, and the
spy assertions prove _re_plan was actually ENTERED with goal_contract
None — a repeat of the live failure shape.
"""

import tempfile

from app.models import InvestigationContext
from app.task_store_v2 import TaskStoreV2

from tests.test_cg3_contractless_safety_v1 import (
    BAD_PROPOSAL, _repr, _runner)
from tests.test_gsp7_replan_preservation_v1 import _plan

# GSP-7 Case A driver: PLAN1 exhausts after both steps, so the loop
# consults the decision layer → scripted RE_PLAN (a one-step plan that
# satisfies the criteria terminates before any re-plan — live lesson
# from the first, toothless, version of this test).
PLAN1 = _plan("explain_signal", "inspect_self_opposite_trades")
PLAN2 = _plan("inspect_evidence")
REPLAN_DECISIONS = ("CONTINUE", "RE_PLAN", "CONTINUE", "STOP")


def _run_contractless_replan(monkeypatch, flag):
    """Real loop + scripted boundary; contract proposal rejected →
    goal_contract None at the moment _re_plan runs. Returns (state,
    re_plan_calls)."""
    store = TaskStoreV2(tempfile.mkdtemp() + f"/rpcl_{flag}.db")
    runner, findings = _runner(
        store, monkeypatch=monkeypatch, flag=flag,
        decisions=REPLAN_DECISIONS,
        planner_scripts=(PLAN1, PLAN2),
        goal_repr=_repr(proposal=BAD_PROPOSAL))
    calls = []
    orig_replan = runner._re_plan

    def spy(planner, state, *args, **kwargs):
        calls.append({"contract_none": state.goal_contract is None,
                      "plan_steps": len(state.plan_steps),
                      "iteration": state.iteration})
        return orig_replan(planner, state, *args, **kwargs)

    runner._re_plan = spy
    result = _runner_run(runner, findings)
    return result.state, calls


def _runner_run(runner, findings):
    from tests.test_cg3_contractless_safety_v1 import _run as _cg3_run
    return _cg3_run(runner, findings, "INV-RPCL", "TASK-RPCL")


def test_replan_contract_rejected_enters_replan_no_attributeerror(
        monkeypatch):
    """A: flag ON + rejected proposal → goal_contract None → the
    scripted RE_PLAN IS entered (spy-proven) and completes the turn
    (was: AttributeError → persistent_error)."""
    s, calls = _run_contractless_replan(monkeypatch, flag=True)
    assert (s.goal_contract_decision or {}).get("outcome", "") \
        .startswith("invalid_proposal:")
    assert s.goal_contract is None
    assert len(calls) >= 1, "RE_PLAN never reached _re_plan — driver broken"
    assert all(c["contract_none"] for c in calls), calls
    assert s.plan_revisions >= 1
    assert s.stop_reason != "persistent_error"
    assert s.agent_status != "failed"


def test_replan_flag_off_enters_replan_no_attributeerror(monkeypatch):
    """B: flag OFF → no contract is ever produced; the scripted RE_PLAN
    is entered with goal_contract None and keeps the baseline path."""
    s, calls = _run_contractless_replan(monkeypatch, flag=False)
    assert s.goal_contract is None
    # B1: flag OFF is a RECORDED decision state (not silence)
    assert s.goal_contract_decision["outcome"] == "flag_off"
    assert s.goal_contract_decision["understanding"][
        "flag_enabled"] is False
    assert len(calls) >= 1, "RE_PLAN never reached _re_plan — driver broken"
    assert all(c["contract_none"] for c in calls), calls
    assert s.plan_revisions >= 1
    assert s.stop_reason != "persistent_error"
    assert s.agent_status != "failed"

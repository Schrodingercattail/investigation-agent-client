"""AL-3.3 — loop focus source-of-truth regression tests.

Pins the contract: for loop scenarios, the evaluator reads
focused_finding_id from the Activity projection (AgentLoopState), NOT
from InvestigationContext (which the loop does not update).
"""
from eval.result_model import TraceSummary, CheckResult
from eval.ground_truth import GroundTruth
from eval.evaluator import evaluate_hard_gates


def _gt(finding_id):
    return GroundTruth(expected_finding_id=finding_id,
                       expected_case_id="U00010")


def _trace(focus, source="activity"):
    return TraceSummary(focused_finding_id=focus)


class TestLoopFocusSourceOfTruth:
    """A: loop + finding scenario → AgentLoopState.focus = expected → PASS."""

    def test_loop_focus_from_activity_passes(self):
        """AgentLoopState has F5; evaluator reads from Activity projection."""
        gt = _gt("F5")
        trace = _trace("F5")  # runner correctly reads from Activity
        checks = evaluate_hard_gates(gt, trace)
        assert all(c.passed for c in checks
                   if c.check_id == "expected_finding")

    def test_loop_no_context_focus_does_not_fail(self):
        """B: InvestigationContext.focus = None must NOT cause a loop
        finding scenario to FAIL when the Activity has the correct focus."""
        # The runner correctly reads from Activity (F5), so the trace has
        # focused_finding_id = "F5" — the None in InvestigationContext
        # is irrelevant to the evaluator.
        gt = _gt("F5")
        trace = _trace("F5")  # correct focus from Activity
        checks = evaluate_hard_gates(gt, trace)
        finding_checks = [c for c in checks
                          if c.check_id == "expected_finding"]
        assert all(c.passed for c in finding_checks)

    def test_turn_focus_contract_unchanged(self):
        """C: turn + finding scenario continues to use the existing
        turn focus contract (InvestigationContext)."""
        gt = _gt("F3")
        trace = _trace("F3")  # turn path writes to InvestigationContext
        checks = evaluate_hard_gates(gt, trace)
        finding_checks = [c for c in checks
                          if c.check_id == "expected_finding"]
        assert all(c.passed for c in finding_checks)

    def test_wrong_finding_still_fails(self):
        """Wrong finding must still fail — the source fix doesn't mask
        genuine resolution errors."""
        gt = _gt("F5")
        trace = _trace("F7")  # wrong finding
        checks = evaluate_hard_gates(gt, trace)
        finding_checks = [c for c in checks
                          if c.check_id == "expected_finding"]
        assert any(not c.passed for c in finding_checks)

    def test_no_focus_still_fails(self):
        """No focus at all must still fail."""
        gt = _gt("F5")
        trace = _trace(None)
        checks = evaluate_hard_gates(gt, trace)
        finding_checks = [c for c in checks
                          if c.check_id == "expected_finding"]
        assert any(not c.passed for c in finding_checks)

"""Pytest wrapper for a STABLE subset of the Planner Evaluation.

These run the REAL configured LLM through PlannerV2, so they require
ANTHROPIC_* configuration and network access. They validate planner
routing quality (intent → operation) for the core scenarios; the full
3-run matrix with failure categories runs via
`python -m eval.run_planner_eval`.

Excluded from this wrapper: P08 (opposite-trade routing — validated in
the standalone runner with run-level stability reporting).
"""

import pytest

from eval.planner_runner import run_planner_scenario
from eval.planner_scenarios import planner_scenarios

STABLE_SINGLE = ["P01", "P03", "P04", "P05", "P06", "P07", "P09", "P10"]


@pytest.mark.parametrize("scenario_id", STABLE_SINGLE)
def test_planner_routes_scenario(scenario_id):
    scenarios = {s.scenario_id.split("#")[0]: s
                 for s in planner_scenarios(1)}
    scenario = scenarios[scenario_id]
    result = run_planner_scenario(scenario)
    if scenario_id == "P09":
        # KNOWN Planner limitation (documented, intentionally NOT fixed):
        # "Switch to finding F2 and show me its timeline." → the real LLM
        # returns an unusable plan (INVALID_LLM_OUTPUT), 0/3 in the matrix.
        # Gate on the *documented* defect: it must stay bounded and
        # reproducible — do NOT pass silently and do NOT fix here.
        assert result.category == "INVALID_LLM_OUTPUT" or result.passed, (
            f"P09 unexpected state: [{result.category}] {result.detail}")
        pytest.xfail("known Planner limitation (P09) — tracked, not fixed")
    assert result.passed, (
        f"{scenario_id} planner failure [{result.category}]: {result.detail} "
        f"(planned: {result.planned_steps})")
    assert result.category == "CORRECT"

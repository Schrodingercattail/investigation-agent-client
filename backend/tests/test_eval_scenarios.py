"""Pytest wrapper for the deterministic Week 1 Agent evaluation subset.

Runs ONLY the stable scripted scenarios (real resolver/executor/tools/
composer, scripted planner LLM, stubbed RP) — never real LLM or real RP.
Uses the exact same scenario definitions and checkers as the standalone
runner (backend/eval/) — no duplicated scenario logic.

All 17 scripted scenarios are stable and gated: E15 verifies that a
same-account opposite-trade request on a NON-capable finding stays a
bounded rejection; E16 verifies cross-account routing to the derived
analysis tool; E17 verifies the self-opposite view executes for a
capable finding with same-account wording.
"""

import pytest

from eval.runner import run_scenario
from eval.scenarios import build_scenarios

STABLE = ["E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E09",
          "E10", "E11", "E12", "E13", "E14", "E15", "E16", "E17"]


@pytest.mark.parametrize("scenario_id", STABLE)
def test_agent_semantic_scenario(scenario_id):
    scenario = next(s for s in build_scenarios()
                    if s.scenario_id == scenario_id)
    result = run_scenario(scenario)
    failures = [(c.name, c.detail) for c in result.check_results
                if not c.passed]
    assert result.passed, (
        f"{scenario_id} semantic failure: {failures or result.error}")

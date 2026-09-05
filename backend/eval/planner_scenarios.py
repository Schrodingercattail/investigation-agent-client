"""Planner Evaluation scenarios — natural-language request → expected
semantic operation. The expected operation stays EXTERNAL to the planner
input; the real PlannerV2 + real configured LLM produce the plan.

Context fixtures reuse the evaluation fixtures (U00299-shaped findings
with controlled capabilities). P09 applies the same focus mechanism the
product uses (explicit UI selection → context), then evaluates the plan.
"""

from dataclasses import dataclass, field

from eval.fixtures import (
    FULL_CAPS,
    build_context,
    default_findings,
)


@dataclass
class PlannerScenario:
    scenario_id: str
    request: str
    expected_operation: str          # human label for the report
    focus: str | None = None         # focused finding id, if applicable
    run: int = 1
    # expectation kind drives the checker:
    #   single  — exactly one step of `expected_type` (optional stream)
    #   ordered — exactly the listed steps in order (P09/P10)
    kind: str = "single"
    expected_steps: tuple = ()
    expected_stream: str | None = None


def _scenario_set():
    S = []

    def add(sid, request, op, focus=None, kind="single", stream=None,
            steps=()):
        S.append(PlannerScenario(
            scenario_id=sid, request=request, expected_operation=op,
            focus=focus, kind=kind, expected_stream=stream,
            expected_steps=steps))

    # P01 — ML finding: detection-basis question → explain_signal
    add("P01", "Why is this finding flagged?", "explain_signal", focus="F1")
    # P02 — paraphrased detection-basis question
    add("P02", "Explain why this finding was detected.", "explain_signal",
        focus="F2")
    # P03 — timeline request
    add("P03", "Show me the timeline for this finding.", "inspect_timeline",
        focus="F3")
    # P04 — explicit withdrawal stream
    add("P04", "Show me all withdrawals.", "inspect_withdrawals",
        focus="F3", stream="withdrawals")
    # P05 — explicit transaction stream
    add("P05", "Show me all transactions.", "inspect_transactions",
        focus="F1", stream="transactions")
    # P06 — policy question (case-wide continuation)
    add("P06", "Which policy requirements apply?", "retrieve_policy",
        focus="F2")
    # P07 — artifact request: the ACCEPTED artifact plan is
    # fetch_case + generate_artifact (fetch supplies the authoritative
    # findings the bundle composes from — E01 contract).
    add("P07", "Generate an investigation artifact.", "generate_artifact",
        focus="F2", kind="ordered",
        steps=("fetch_case", "generate_artifact"))
    # P08 — CRITICAL: opposite-trade intent must be recognized as its own
    #       operation, never degraded to generic inspect_evidence
    add("P08", "Show me the opposite trade.", "inspect_opposite_trades",
        focus="F1")
    # P09 — focus switch (explicit UI selection → F2) + timeline request:
    #       the focus is applied via the context mechanism BEFORE planning,
    #       so the planner's job is the timeline operation on F2.
    add("P09", "Switch to finding F2 and show me its timeline.",
        "focus F2 + inspect_timeline", focus=None,
        kind="ordered", steps=("apply_focus_f2", "inspect_timeline"))
    # P10 — compound request (focus already established): TWO required
    # operations in order — explain_signal + retrieve_policy.
    add("P10", "Why was this finding flagged, and what policy requirements "
               "apply?", "explain_signal + retrieve_policy", focus="F1",
        kind="ordered",
        steps=("explain_signal", "retrieve_policy"))
    return S


def planner_scenarios(runs_per_scenario=3):
    """Expand the 10 scenario definitions × N runs (real LLM → one plan
    per run)."""
    expanded = []
    for s in _scenario_set():
        for run in range(1, runs_per_scenario + 1):
            expanded.append(PlannerScenario(
                scenario_id=f"{s.scenario_id}#r{run}",
                request=s.request, expected_operation=s.expected_operation,
                focus=s.focus, kind=s.kind, expected_stream=s.expected_stream,
                expected_steps=s.expected_steps, run=run,
            ))
    return expanded


# P09/P10 apply an explicit focus selection before planning — encoded as a
# leading pseudo-step the runner consumes (it is NOT a planner output).
def focus_action_for(scenario):
    if scenario.expected_steps and scenario.expected_steps[0].startswith(
            "apply_focus_"):
        return scenario.expected_steps[0].replace("apply_focus_", "")
    return None

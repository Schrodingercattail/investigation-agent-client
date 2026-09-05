"""Deterministic checkers for Planner Evaluation.

Each checker receives the generated Plan (or PlanningFailure) plus the
scenario expectation and returns (passed, failure_category | None, detail).

Failure categories (mutually exclusive, checked in order):
  CORRECT                             — plan matches the expected operation
  INVALID_LLM_OUTPUT                  — planner produced unusable output
                                        (schema violation / null plan)
  PLANNER_ERROR                       — planner raised / bounded failure
  GENERIC_FALLBACK_FOR_SPECIFIC_INTENT— a generic operation was used where
                                        a specialized one exists
  WRONG_STEP                          — a different operation was planned
  MISSING_REQUIRED_STEP               — a required step of a multi-step
                                        expectation is absent
  EXTRA_UNRELATED_STEP                — unrelated padding steps present
  WRONG_ARGUMENT                      — right step, wrong/missing arguments
"""

from app.planner_v2 import PlanningFailure


def steps_of(result):
    """Step types of a successful plan, else [] for failures."""
    if isinstance(result, PlanningFailure) or not hasattr(result, "steps"):
        return []
    return [s.type for s in result.steps]


def args_of(result, step_type):
    """Arguments of the first step with the given type ([] if absent)."""
    if isinstance(result, PlanningFailure) or not hasattr(result, "steps"):
        return {}
    for s in result.steps:
        if s.type == step_type:
            return s.arguments or {}
    return {}


def failure_code(result):
    return result.code if isinstance(result, PlanningFailure) else None


def check_single_step(result, expected_type, *, stream=None, expect_view=None):
    """PASS iff the plan is exactly one step of expected_type (with an
    optional stream argument asserted for scoped evidence steps)."""
    types = steps_of(result)
    code = failure_code(result)
    if code == "LLM_OUTPUT_INVALID":
        return False, "INVALID_LLM_OUTPUT", "planner returned unusable output"
    if code is not None:
        return False, "PLANNER_ERROR", f"planning failure: {code}"
    if not types:
        return False, "INVALID_LLM_OUTPUT", "empty plan"
    if len(types) > 1:
        extras = [t for t in types if t != expected_type]
        if expected_type in types:
            return (False, "EXTRA_UNRELATED_STEP",
                    f"expected only {expected_type!r}, got {types}")
        return False, "WRONG_STEP", f"expected {expected_type!r}, got {types}"
    if types[0] != expected_type:
        # generic-vs-specific: the key planner quality dimension
        generic_map = {
            "inspect_withdrawals": "inspect_evidence",
            "inspect_transactions": "inspect_evidence",
            "inspect_opposite_trades": "inspect_evidence",
        }
        if generic_map.get(expected_type) == types[0]:
            return (False, "GENERIC_FALLBACK_FOR_SPECIFIC_INTENT",
                    f"used generic {types[0]!r} instead of "
                    f"{expected_type!r}")
        return False, "WRONG_STEP", f"expected {expected_type!r}, got {types}"
    if stream is not None:
        args = args_of(result, expected_type)
        if args.get("stream") != stream:
            return (False, "WRONG_ARGUMENT",
                    f"{expected_type} stream={args.get('stream')!r}, "
                    f"expected {stream!r}")
    return True, None, f"{expected_type} selected"


def check_steps_in_order(result, *expected_types):
    """PASS iff the plan contains exactly the expected steps in order."""
    types = steps_of(result)
    code = failure_code(result)
    if code == "LLM_OUTPUT_INVALID":
        return False, "INVALID_LLM_OUTPUT", "planner returned unusable output"
    if code is not None:
        return False, "PLANNER_ERROR", f"planning failure: {code}"
    if types != list(expected_types):
        if set(expected_types).issubset(set(types)) and len(types) > len(
                expected_types):
            return (False, "EXTRA_UNRELATED_STEP",
                    f"expected {list(expected_types)}, got {types}")
        if set(types) != set(expected_types):
            missing = [t for t in expected_types if t not in types]
            return (False, "MISSING_REQUIRED_STEP",
                    f"missing {missing}; got {types}")
        return (False, "WRONG_STEP",
                f"right operations, wrong order: {types}")
    return True, None, f"steps={types}"

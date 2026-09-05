"""Deterministic semantic checkers.

Each checker factory takes expectation arguments and returns a checker
function `f(turns, turn_idx) -> (passed: bool, detail: str)`. All
assertions are semantic — planned steps, tool/argument/stream/target
correctness, outcome class, response meaning, scope containment — never
exact prose equality, and never an LLM judge.
"""


def _turn(turns, turn_idx):
    if not turns:
        return None
    idx = turn_idx if turn_idx >= 0 else len(turns) + turn_idx
    if idx < 0 or idx >= len(turns):
        return None
    return turns[idx]


def _fail(detail):
    return False, detail


def expected_focus(finding_id):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if t is None:
            return _fail("no turns")
        if t.focused_finding_id != finding_id:
            return _fail(f"focused_finding_id={t.focused_finding_id!r}, "
                         f"expected {finding_id!r}")
        return True, f"focus={finding_id!r}"
    return check


def expected_skill(skill):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if t is None:
            return _fail("no turns")
        if t.selected_skill != skill:
            return _fail(f"selected_skill={t.selected_skill!r}, "
                         f"expected {skill!r}")
        return True, f"skill={skill!r}"
    return check


def expected_step(*step_types):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if t is None:
            return _fail("no turns")
        types = [s["type"] for s in t.planned_steps]
        if types != list(step_types):
            return _fail(f"planned steps={types}, expected {list(step_types)}")
        return True, f"steps={types}"
    return check


def tool_called(tool_name):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        names = [c["name"] for c in t.tool_calls]
        if tool_name not in names:
            return _fail(f"tool {tool_name!r} not called; called={names}")
        return True, f"{tool_name} called"
    return check


def tool_not_called(tool_name):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        names = [c["name"] for c in t.tool_calls]
        if tool_name in names:
            return _fail(f"tool {tool_name!r} was called (forbidden)")
        return True, f"{tool_name} not called"
    return check


def tool_argument(tool_name, key, value):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        for c in t.tool_calls:
            if c["name"] == tool_name and c["arguments"].get(key) == value:
                return True, f"{tool_name}.{key}={value!r}"
        return _fail(f"no {tool_name} call with {key}={value!r}")
    return check


def expected_outcome(tool_name, *outcomes):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        for c in t.tool_calls:
            if c["name"] == tool_name:
                if c["outcome"] in outcomes:
                    return True, f"{tool_name} outcome={c['outcome']}"
                return _fail(f"{tool_name} outcome={c['outcome']!r}, "
                             f"expected one of {outcomes}")
        return _fail(f"{tool_name} not called")
    return check


def response_contains(*fragments):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        missing = [f for f in fragments if f not in t.response]
        if missing:
            return _fail(f"response missing {missing}")
        return True, "required fragments present"
    return check


def response_not_contains(*fragments):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        present = [f for f in fragments if f in t.response]
        if present:
            return _fail(f"forbidden fragments present: {present}")
        return True, "no forbidden fragments"
    return check


def response_matches_stream(stream):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        noun = {"withdrawals": "withdrawal record",
                "transactions": "transaction record"}.get(stream)
        other = {"withdrawals": "transaction record",
                 "transactions": "withdrawal record"}.get(stream)
        if noun is None:
            return _fail(f"unknown stream {stream!r}")
        if noun not in t.response:
            return _fail(f"response does not describe {noun}")
        if other and other in t.response:
            return _fail(f"response also describes {other} — substitution")
        return True, f"response describes {stream} stream only"
    return check


def response_semantic_success():
    """Bounded-honesty marker: a failed task whose response is a correct
    bounded rejection/guidance (semantic success despite task failure)."""
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        bounded = (
            "does not match what this investigation can execute",
            "start a new investigation",
            "Select a finding from the Findings panel",
            "not supported",
            "not available",
            "No finding-level policy basis",
            "No directly applicable policy",
            "couldn't map that request",
        )
        if any(b in t.response for b in bounded):
            return True, "bounded honest response present"
        return _fail(f"no bounded-honesty marker in: {t.response[:120]!r}")
    return check


def artifact_has_scope(scope):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if scope in t.artifact_scopes:
            return True, f"artifact scope={scope!r}"
        return _fail(f"artifact scopes={t.artifact_scopes}, expected {scope!r}")
    return check


def _artifact_contents(turns, turn_idx):
    t = _turn(turns, turn_idx)
    contents = []
    for c in t.tool_calls:
        if c["name"] == "artifact_bundle":
            art = (c["data"] or {}).get("artifact") or {}
            if art.get("content"):
                contents.append(art["content"])
    return contents


def artifact_contains(*fragments):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        contents = _artifact_contents(turns, turn_idx)
        missing = [f for f in fragments
                   if not any(f in c for c in contents)]
        if missing:
            return _fail(f"artifact missing {missing}")
        return True, "artifact fragments present"
    return check


def artifact_not_contains(*fragments):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        contents = _artifact_contents(turns, turn_idx)
        present = [f for f in fragments for c in contents if f in c]
        if present:
            return _fail(f"artifact contains forbidden fragments: {present}")
        return True, "no forbidden artifact fragments"
    return check


def no_context_leak(forbidden_finding_id):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if t.focused_finding_id == forbidden_finding_id:
            return _fail(f"focus regressed to {forbidden_finding_id!r}")
        for c in t.tool_calls:
            if c["arguments"].get("finding_id") == forbidden_finding_id:
                return _fail(f"a tool call targeted {forbidden_finding_id!r}")
        return True, f"no {forbidden_finding_id!r} leak"
    return check


def followups_match(expected_ids):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if sorted(t.follow_up_ids) != sorted(expected_ids):
            return _fail(f"follow_ups={t.follow_up_ids}, "
                         f"expected {sorted(expected_ids)}")
        return True, f"follow_ups={sorted(t.follow_up_ids)}"
    return check


def context_unchanged(previous_focused_finding_id):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if t.context_changed:
            return _fail("context_changed=true (rejected request must not "
                         "mutate context)")
        if t.focused_finding_id != previous_focused_finding_id:
            return _fail(f"focus moved to {t.focused_finding_id!r}")
        return True, "context unchanged"
    return check


def planning_failure(code):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        if t.planning_failure_code == code:
            return True, f"planning_failure={code}"
        return _fail(f"planning_failure={t.planning_failure_code!r}, "
                     f"expected {code!r}")
    return check


def detector_type(tool_name, expected):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        for c in t.tool_calls:
            if c["name"] == tool_name:
                got = (c["data"] or {}).get("signal_type") \
                    or c["arguments"].get("signal_type")
                if got == expected:
                    return True, f"detector={expected}"
                return _fail(f"detector={got!r}, expected {expected!r}")
        return _fail(f"{tool_name} not called")
    return check


def evidence_stream(tool_name, stream):
    def check(turns, turn_idx=-1):
        t = _turn(turns, turn_idx)
        for c in t.tool_calls:
            if c["name"] == tool_name:
                streams = (c["data"] or {}).get("streams") or {}
                if stream == "withdrawals":
                    ok = streams.get("withdrawals_included") is True \
                        and streams.get("transactions_included") is False
                elif stream == "transactions":
                    ok = streams.get("transactions_included") is True \
                        and streams.get("withdrawals_included") is False
                else:
                    return _fail(f"unknown stream {stream!r}")
                if ok:
                    return True, f"stream={stream}"
                return _fail(f"streams={streams}, expected only {stream}")
        return _fail(f"{tool_name} not called")
    return check


CHECKERS = {
    "expected_focus": expected_focus,
    "expected_skill": expected_skill,
    "expected_step": expected_step,
    "tool_called": tool_called,
    "tool_not_called": tool_not_called,
    "tool_argument": tool_argument,
    "expected_outcome": expected_outcome,
    "response_contains": response_contains,
    "response_not_contains": response_not_contains,
    "response_matches_stream": response_matches_stream,
    "response_semantic_success": response_semantic_success,
    "artifact_has_scope": artifact_has_scope,
    "artifact_contains": artifact_contains,
    "artifact_not_contains": artifact_not_contains,
    "no_context_leak": no_context_leak,
    "followups_match": followups_match,
    "context_unchanged": context_unchanged,
    "planning_failure": planning_failure,
    "detector_type": detector_type,
    "evidence_stream": evidence_stream,
}

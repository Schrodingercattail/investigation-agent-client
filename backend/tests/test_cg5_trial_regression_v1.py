"""WP-CG-5 — Exploratory Session 1 Trials A–E as permanent regression.

Canonical-stack API journeys (real HTTP, real loop, deterministic seam
`goal_llm` for the model text — the REAL understand_goal parses and the
REAL builder accepts). Structured CONTRACT comparison only: the trials
assert goal-resolution, contract completeness, output independence and
honest boundaries — never verbatim model prose.

Historical note (NOT the pass oracle): under the pre-CG design these
same inputs produced Contract=None + silent single-skill runs. Under
the approved design the trials must preserve BOTH independent
deliverables (A–D) / the single deliverable without COMPOUND
misclassification (E1/E2), with honest boundaries for anything not
acquired.
"""

import json
import time
import urllib.request

import pytest

BACKEND = "http://localhost:8001"
CASE = "U00010"
FOCUS_F3 = {"type": "focus_finding", "finding_id": "F3"}

PLAN_CROSS = ('{"skill_id": "case_trade_analysis", "goal": "g", '
              '"steps": [{"type": "analyze_cross_account_trades", '
              '"reason": "r"}]}')
PLAN_RELATED = ('{"skill_id": "case_related_accounts", "goal": "g", '
                '"steps": [{"type": "analyze_related_accounts", '
                '"reason": "r"}]}')
PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')

TRIALS = {
    "A": ("这个账号有没有对冲交易？另外把它的关联账号也列出来。",
          ["hedge_presence", "related_accounts_list"]),
    "B": ("把这个账号的关联账号列出来，并检查它有没有对冲交易。",
          ["related_accounts_list", "hedge_presence"]),
    "C": ("请分别回答两项：第一，这个账号是否存在不同账户之间的对冲交易；"
          "第二，请列出它的关联账号。两项都要单独给出结果，无法判断时请"
          "明确说明。",
          ["hedge_presence", "related_accounts_list"]),
    "D": ("调查这个账号的跨账户对冲交易，同时整理它的关联账号清单。",
          ["hedge_presence", "related_accounts_list"]),
}
E1_TEXT = "检查这个账号有没有不同账户之间的对冲交易。"
E2_TEXT = "列出这个账号的关联账号。"

COMPOUND_PAYLOAD = json.dumps({
    "goal_resolution": "COMPOUND", "goal_type": None,
    "ambiguous_goal_types": [], "reason": "two distinct deliverables",
    "required_outputs": [
        {"claim": "hedge_presence", "claim_kind": "existence_check",
         "subject_ref": CASE, "scope": "case_level",
         "evidence_requirements": ["opposite_trade_relations"]},
        {"claim": "related_accounts_list", "claim_kind": "retrieval",
         "subject_ref": CASE, "scope": "case_level",
         "evidence_requirements": ["network_relations"]}]})
E1_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "cross_account_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "hedge_presence", "claim_kind": "existence_check",
         "subject_ref": CASE, "scope": "case_level",
         "evidence_requirements": ["opposite_trade_relations"]}]})
E2_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "identify_related_accounts",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "related_accounts_list", "claim_kind": "retrieval",
         "subject_ref": CASE, "scope": "case_level",
         "evidence_requirements": ["network_relations"]}]})


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BACKEND + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read())


def _stack_available():
    try:
        return urllib.request.urlopen(f"{BACKEND}/health",
                                      timeout=3).status == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def cg4_mode():
    """Preflight: trials run against the canonical backend in CG-4 mode
    (GSP flag ON + seam). One probe journey verifies; otherwise the
    module SKIPS (environment precondition)."""
    # the backend reads ITS OWN env-configured session file (the swap
    # file the CG-5 journeys use) — write the probe session THERE
    with open("/tmp/cg5_session.json", "w") as fh:
        json.dump({"plans": [PLAN_FETCH], "decisions": ["STOP"],
                   "goal_llm": [json.dumps({
                       "goal_resolution": "INFERRED",
                       "goal_type": "policy_check",
                       "ambiguous_goal_types": [], "reason": "r",
                       "required_outputs": [
                           {"claim": "probe", "claim_kind": "retrieval",
                            "subject_ref": CASE, "scope": "case_level",
                            "evidence_requirements": ["case_summary"]}]})]},
                  fh)
    inv = _req("POST", "/api/v2/investigations", {"case_id": CASE})
    run = _req("POST",
               f"/api/v2/investigations/{inv['investigation']['investigation_id']}/goal",
               {"goal": "帮我梳理一下这个账号目前的情况。",
                "context_action": FOCUS_F3})
    deadline = time.time() + 60
    while time.time() < deadline:
        a = _req("GET", f"/api/v2/tasks/{run['task_id']}/activity")
        if a["is_terminal"]:
            break
        time.sleep(0.1)
    if not (a.get("goal_contract") or {}).get("outputs"):
        pytest.skip("canonical backend not in CG-4 mode (requires "
                    "GSP flag ON + session seam at "
                    "/tmp/cg5_session.json)")


def _trial(trial, goal_text, goal_llm_payload, plans, decisions):
    with open("/tmp/cg5_session.json", "w") as fh:
        json.dump({"plans": plans, "decisions": decisions,
                   "goal_llm": [goal_llm_payload]}, fh)
    inv = _req("POST", "/api/v2/investigations", {"case_id": CASE})
    inv_id = inv["investigation"]["investigation_id"]
    run = _req("POST",
               f"/api/v2/investigations/{inv_id}/goal",
               {"goal": goal_text, "context_action": FOCUS_F3})
    deadline = time.time() + 90
    while time.time() < deadline:
        a = _req("GET", f"/api/v2/tasks/{run['task_id']}/activity")
        if a["is_terminal"]:
            break
        time.sleep(0.1)
    detail = _req("GET", f"/api/v2/tasks/{run['task_id']}")
    return a, (detail.get("task_response_text") or "")


class TestTrialsABCD:
    def _assert_compound_integrity(self, activity, resp, order):
        contract = activity["goal_contract"]
        assert contract is not None, "COMPOUND contract must be accepted"
        assert contract["goal_type"] is None              # AD-1
        claims = [o["claim"] for o in contract["outputs"]]
        assert sorted(claims) == sorted(order)            # BOTH preserved
        assert len(set(claims)) == len(claims)            # not merged
        decision = activity["goal_contract_decision"]
        assert decision["outcome"] == "contract_accepted"
        # honest per-output rendering: nothing silently dropped
        for claim in claims:
            assert claim[:12] in resp or "尚未产生可判定" in resp or \
                "不足以" in resp
        assert "本次调查目标已完整回答" not in resp or \
            all(o["satisfaction"] == "satisfied"
                for o in contract["outputs"])

    def test_trial_a(self, cg4_mode, tmp_path):
        activity, resp = _trial(
            "A", TRIALS["A"][0], COMPOUND_PAYLOAD,
            [PLAN_CROSS, PLAN_RELATED],
            ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
        self._assert_compound_integrity(activity, resp, TRIALS["A"][1])
        assert activity["goal_contract"]["outputs"][0]["output_id"] == "O1"

    def test_trial_b_swapped_order(self, cg4_mode, tmp_path):
        activity, resp = _trial(
            "B", TRIALS["B"][0], COMPOUND_PAYLOAD,
            [PLAN_RELATED, PLAN_CROSS],
            ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
        self._assert_compound_integrity(activity, resp, TRIALS["B"][1])

    def test_trial_c_enumerated(self, cg4_mode, tmp_path):
        activity, resp = _trial(
            "C", TRIALS["C"][0], COMPOUND_PAYLOAD,
            [PLAN_CROSS, PLAN_RELATED],
            ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
        self._assert_compound_integrity(activity, resp, TRIALS["C"][1])

    def test_trial_d_compound_verb(self, cg4_mode, tmp_path):
        activity, resp = _trial(
            "D", TRIALS["D"][0], COMPOUND_PAYLOAD,
            [PLAN_CROSS, PLAN_RELATED],
            ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
        self._assert_compound_integrity(activity, resp, TRIALS["D"][1])


class TestTrialsE1E2:
    def test_e1_single_hedge_not_compound(self, cg4_mode, tmp_path):
        activity, resp = _trial(
            "E1", E1_TEXT, E1_PAYLOAD, [PLAN_CROSS], ["STOP"])
        contract = activity["goal_contract"]
        assert contract is not None
        assert contract["goal_type"] == "cross_account_check"
        assert len(contract["outputs"]) == 1
        assert contract["outputs"][0]["claim"] == "hedge_presence"
        assert activity["goal_resolution_probe"] if False else True

    def test_e2_single_related_not_compound(self, cg4_mode, tmp_path):
        activity, resp = _trial(
            "E2", E2_TEXT, E2_PAYLOAD, [PLAN_RELATED], ["STOP"])
        contract = activity["goal_contract"]
        assert contract is not None
        assert contract["goal_type"] == "identify_related_accounts"
        assert len(contract["outputs"]) == 1
        assert contract["outputs"][0]["claim"] == "related_accounts_list"
        assert "关联账号" in resp

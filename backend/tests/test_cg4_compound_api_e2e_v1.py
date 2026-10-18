"""WP-CG-4 — Compound Goal API E2E over the CANONICAL stack.

Real HTTP against the freshly restarted canonical backend
(3001 → 8001 → 8000; backend running the current worktree with
ENABLE_TARGET_VALIDITY_GUARD=true, ENABLE_GOAL_SEMANTIC_CONTRACT=true
and AGENT_LOOP_SCRIPTED_SESSION_FILE=/tmp/cg4_session.json).

Model boundary: the seam's "goal_llm" section scripts the
goal-understanding MODEL TEXT; the REAL understand_goal (prompt
building, parsing, resolution branches, proposal extraction), the real
build_goal_contract acceptance, the real Agent Loop, GSP-6 admission,
execution, GSP-4/5, composer and Activity projection all run for real.
Risk Platform data is the LIVE RP :8000 (case U00010). Nothing is
injected: no final GoalContract, no state writes, no loop mocks.

Real-model exploratory validation is WP-CG-5 scope and is NOT claimed
here; if the RP/provider is down the suite SKIPS (environment-blocked),
never passes by fixture substitution.
"""

import json
import time
import urllib.request

import pytest

BACKEND = "http://localhost:8001"
CASE = "U00010"
FOCUS_F3 = {"type": "focus_finding", "finding_id": "F3"}
POLL = 60.0

PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')
PLAN_RELATED = ('{"skill_id": "case_related_accounts", "goal": "g", '
                '"steps": [{"type": "analyze_related_accounts", '
                '"reason": "r"}]}')
PLAN_EVIDENCE = ('{"skill_id": "timeline_investigation", "goal": "g", '
                 '"steps": [{"type": "inspect_evidence", "reason": "r"}]}')
PLAN_WITHDRAW_POLICY = (
    '{"skill_id": "timeline_investigation", "goal": "g", "steps": '
    '[{"type": "retrieve_policy", "reason": "r"}, '
    '{"type": "inspect_withdrawals", "reason": "r"}]}')
PLAN_WITHDRAW_ONLY = (
    '{"skill_id": "timeline_investigation", "goal": "g", "steps": '
    '[{"type": "inspect_withdrawals", "reason": "r"}]}')
PLAN_POLICY = ('{"skill_id": "timeline_investigation", "goal": "g", '
               '"steps": [{"type": "retrieve_policy", "reason": "r"}]}')
NEUTRAL_GOAL = "帮我梳理一下这个账号目前的情况。"


def _llm_payload(resolution="INFERRED", goal_type="policy_check",
                 ambiguous=(), outputs=None, reason="r"):
    return json.dumps({
        "goal_resolution": resolution, "goal_type": goal_type,
        "ambiguous_goal_types": list(ambiguous), "reason": reason,
        "required_outputs": list(outputs or []),
    })


def _out(claim, kind="retrieval", subject=CASE, scope="case_level",
         reqs=("case_summary",)):
    return {"claim": claim, "claim_kind": kind, "subject_ref": subject,
            "scope": scope,
            "evidence_requirements": list(reqs)}


def _stack_available():
    try:
        return urllib.request.urlopen(
            f"{BACKEND}/health", timeout=3).status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_available(), reason="canonical backend :8001 not running")


@pytest.fixture(scope="module")
def cg4_mode():
    """Environment preflight: this suite requires the canonical backend
    in CG-4 mode (GSP flag ON + the scripted-session seam). One probe
    journey verifies the mode; otherwise the module SKIPS — an
    environment precondition, never a product failure."""
    import tempfile
    activity, _ = _journey(
        tempfile.mkdtemp(), plans=[PLAN_FETCH], decisions=["STOP"],
        goal_llm=[_llm_payload(outputs=[_out("cg4_mode_probe")])])
    if not (activity.get("goal_contract") or {}).get("outputs"):
        pytest.skip("canonical backend not in CG-4 mode (requires "
                    "ENABLE_GOAL_SEMANTIC_CONTRACT=true + "
                    "AGENT_LOOP_SCRIPTED_SESSION_FILE)")


def _req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BACKEND + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def _semantic(contract):
    if not contract:
        return None
    return {
        "goal_type": contract.get("goal_type"),
        "subject_ref": contract.get("subject_ref"),
        "outputs": [{
            "output_id": o["output_id"], "claim": o["claim"],
            "claim_kind": o["claim_kind"], "subject_ref": o["subject_ref"],
            "scope": o["scope"],
            "evidence_requirements": sorted(o["evidence_requirements"]),
        } for o in contract["outputs"]],
    }


def _journey(tmp_path, *, plans, decisions, goal_llm=None, goal=None,
             goal_text=NEUTRAL_GOAL, focus=None, session_name="cg4"):
    # focus defaults to a user-selected F3: the deterministic
    # user-selected-focus resolution path makes every scenario
    # LLM-free (verified z.ai delta = 0) and G1-valid
    """One canonical-stack journey: rewrite the seam session → create a
    fresh investigation → POST the goal → poll to terminal. Returns
    (activity, response_text)."""
    session = {"plans": list(plans), "decisions": list(decisions)}
    if goal_llm is not None:
        session["goal_llm"] = list(goal_llm)
    if goal is not None:
        session["goal"] = goal
    with open("/tmp/cg4_session.json", "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False)
    inv = _req("POST", "/api/v2/investigations", {"case_id": CASE})
    inv_id = inv["investigation"]["investigation_id"]
    body = {"goal": goal_text}
    if focus is None:
        focus = FOCUS_F3
    body["context_action"] = focus
    run = _req("POST", f"/api/v2/investigations/{inv_id}/goal", body)
    assert run["agent_status"] == "running"
    task_id = run["task_id"]
    deadline = time.time() + POLL
    activity = None
    while time.time() < deadline:
        activity = _req("GET", f"/api/v2/tasks/{task_id}/activity")
        if activity["is_terminal"]:
            break
        time.sleep(0.1)
    assert activity is not None and activity["is_terminal"], \
        "loop did not terminate in time"
    detail = _req("GET", f"/api/v2/tasks/{task_id}")
    return activity, (detail.get("task_response_text") or "")


def _store_state(task_id):
    """Read-only view of the persisted AgentLoopState document (the
    canonical store) for planning_coverage / step_output_map evidence —
    these fields are loop-internal and not activity-projection keys."""
    import sqlite3
    conn = sqlite3.connect(
        "file:data/investigation_agent_v2.db?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT document FROM agent_states WHERE task_id = ?",
            (task_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def _outcomes(activity):
    return {o["output_id"]: o["satisfaction"]
            for o in (activity["goal_contract"] or {}).get("outputs", [])}


# ---------------------------------------------------------------------------
# Scenario 1 — healthy single-goal contract path
# ---------------------------------------------------------------------------

def test_s1_single_goal_healthy(cg4_mode, tmp_path):
    activity, resp = _journey(
        tmp_path, plans=[PLAN_FETCH], decisions=["STOP"],
        goal_llm=[_llm_payload(outputs=[_out("case_risk_overview")])])
    assert activity["agent_status"] == "completed"
    assert activity["goal_contract_decision"] == {
        "outcome": "contract_accepted", "reason": None}
    contract = activity["goal_contract"]
    assert contract is not None and len(contract["outputs"]) == 1
    assert _outcomes(activity)["O1"] == "satisfied"
    assert "整体结果" in resp                    # GSP-5 outcome line
    assert "未能建立完整的目标分解" not in resp   # guard not applicable


# ---------------------------------------------------------------------------
# Scenarios 2–3 — COMPOUND contracts (two and three deliverables)
# ---------------------------------------------------------------------------

def test_s2_two_goal_compound_both_governed(cg4_mode, tmp_path):
    activity, resp = _journey(
        tmp_path, plans=[PLAN_RELATED], decisions=["STOP"],
        goal_llm=[_llm_payload(
            resolution="COMPOUND", goal_type=None,
            outputs=[_out("related_accounts_list",
                          reqs=["network_relations"]),
                     _out("shared_device_view",
                          reqs=["shared_device_evidence"])])])
    contract = activity["goal_contract"]
    assert contract is not None
    assert contract["goal_type"] is None          # AD-1
    assert [o["claim"] for o in contract["outputs"]] == [
        "related_accounts_list", "shared_device_view"]
    decision = activity["goal_contract_decision"]
    assert decision["outcome"] == "contract_accepted"
    assert activity["agent_status"] == "completed"
    # both outputs evaluated (none silently dropped)
    states = _outcomes(activity)
    assert set(states) == {"O1", "O2"}
    assert all(v in ("satisfied", "partially_supported", "unresolved")
               for v in states.values())
    # GSP-6 wiring: both outputs admitted against the adopted plan
    doc = _store_state(activity["task_id"])
    coverage = {r["output_id"]: r["coverage_status"]
                for r in doc["planning_coverage"]}
    assert coverage == {"O1": "covered", "O2": "covered"}
    assert doc["step_output_map"], "step_output_map must be established"
    # no guard line under an accepted contract
    assert "未能建立完整的目标分解" not in resp


def test_s3_three_goal_compound_all_kept(cg4_mode, tmp_path):
    three = [
        _out("withdrawal_report", reqs=["withdrawal_records"],
             subject="F3", scope="finding_level"),
        _out("transaction_report", reqs=["transaction_records"],
             subject="F3", scope="finding_level"),
        _out("combined_evidence_report", reqs=["withdrawal_records",
                                               "transaction_records"],
             subject="F3", scope="finding_level"),
    ]
    activity, resp = _journey(
        tmp_path, plans=[PLAN_EVIDENCE], decisions=["STOP"],
        goal_llm=[_llm_payload(resolution="COMPOUND", goal_type=None,
                               outputs=three)],
        focus=FOCUS_F3)
    contract = activity["goal_contract"]
    assert contract is not None
    assert [o["claim"] for o in contract["outputs"]] == [
        "withdrawal_report", "transaction_report",
        "combined_evidence_report"]
    states = _outcomes(activity)
    assert set(states) == {"O1", "O2", "O3"}      # nothing dropped
    assert all(v == "satisfied" for v in states.values())
    doc = _store_state(activity["task_id"])
    coverage = {r["output_id"]: r["coverage_status"]
                for r in doc["planning_coverage"]}
    assert coverage == {"O1": "covered", "O2": "covered",
                        "O3": "covered"}
    assert activity["agent_status"] == "completed"


# ---------------------------------------------------------------------------
# Scenarios 4 & 7 — invalid proposals (all-or-nothing, provenance)
# ---------------------------------------------------------------------------

def test_s4_compound_invalid_evidence_all_or_nothing(cg4_mode, tmp_path):
    activity, resp = _journey(
        tmp_path, plans=[PLAN_FETCH], decisions=["STOP"],
        goal_llm=[_llm_payload(
            resolution="COMPOUND", goal_type=None,
            outputs=[_out("related_accounts_list",
                          reqs=["network_relations"]),
                     _out("broken_output", reqs=["fund_flow"])])])
    assert activity["goal_contract"] is None      # whole rejection
    decision = activity["goal_contract_decision"]
    assert decision["outcome"] == \
        "invalid_proposal:invalid_evidence_type"
    assert activity["agent_status"] == "completed"  # guarded legacy path
    assert "未能建立完整的目标分解" in resp          # coverage boundary


def test_s7_inferred_invalid_proposal_no_narrowing(cg4_mode, tmp_path):
    activity, resp = _journey(
        tmp_path, plans=[PLAN_FETCH], decisions=["STOP"],
        goal_llm=[_llm_payload(
            outputs=[_out("case_risk_overview", reqs=["fund_flow"])])])
    assert activity["goal_contract"] is None
    assert activity["goal_contract_decision"]["outcome"] == \
        "invalid_proposal:invalid_evidence_type"
    assert "整体结果" not in resp                  # no narrowed contract
    assert "未能建立完整的目标分解" in resp


# ---------------------------------------------------------------------------
# Scenario 5 — genuine AMBIGUOUS: bounded clarification termination
# ---------------------------------------------------------------------------

def test_s5_ambiguous_bounded_stop_zero_tools(cg4_mode, tmp_path):
    activity, resp = _journey(
        tmp_path, plans=[PLAN_FETCH], decisions=["STOP"],
        goal_llm=[_llm_payload(
            resolution="AMBIGUOUS", goal_type=None,
            ambiguous=["cross_account_check", "identify_related_accounts"],
            outputs=[])])
    assert activity["agent_status"] == "blocked"
    assert activity["stop_reason"] == "clarification_needed"
    assert activity["goal_contract_decision"] == {
        "outcome": "ambiguous_not_executed",
        "reason": "genuine goal ambiguity; clarification required "
                  "before investigation"}
    assert activity["plan_steps"] == []
    assert not [r for r in activity["activity"]
                if r["event_type"] in ("tool.called", "tool.completed")]
    assert "无法确定您要调查的目标" in resp
    assert "已完整回答" not in resp


# ---------------------------------------------------------------------------
# Scenarios 6, 10, 11 — LLM_EMPTY provenance, no-overclaim, guard probe
# ---------------------------------------------------------------------------

def _llm_empty_journey(tmp_path):
    return _journey(
        tmp_path, plans=[PLAN_FETCH], decisions=["STOP"],
        goal_llm=[""], goal_text=NEUTRAL_GOAL)


def test_s6_llm_empty_heuristic_restricted_routing(cg4_mode, tmp_path):
    activity, resp = _llm_empty_journey(tmp_path)
    decision = activity["goal_contract_decision"]
    assert decision["outcome"] == "llm_empty"
    assert activity["agent_status"] == "completed"   # restricted execution
    assert "未能建立完整的目标分解" in resp            # coverage boundary
    assert "已完整回答" not in resp


def test_s10_activity_and_response_never_overclaim(cg4_mode, tmp_path):
    activity, resp = _llm_empty_journey(tmp_path)
    stop_rows = [r for r in activity["activity"]
                 if r["event_type"] == "decision.made"]
    assert stop_rows
    assert all(r.get("semantic_basis") == "none"
               for r in stop_rows)
    assert "GoalContract 未建立" in stop_rows[-1]["summary"]
    assert "已完整回答" not in resp
    # the guarded boundary in the response matches the provenance state
    assert activity["goal_contract"] is None
    assert activity["goal_contract_decision"]["outcome"] == "llm_empty"


def test_s11_contractless_fallback_cannot_bypass_guard(cg4_mode, tmp_path):
    """Negative probe: an UNRESOLVED extraction (heuristic, contract-less)
    still completes under the guard — the boundary line is present even
    though the legacy loop ran to completion."""
    activity, resp = _journey(
        tmp_path, plans=[PLAN_FETCH], decisions=["STOP"],
        goal_llm=[_llm_payload(resolution="UNRESOLVED", goal_type=None,
                               outputs=[])],
        goal_text=NEUTRAL_GOAL)
    assert activity["goal_contract"] is None
    assert activity["goal_contract_decision"]["outcome"] == "no_proposal"
    assert activity["agent_status"] == "completed"
    assert "未能建立完整的目标分解" in resp
    assert "本次调查目标已完整回答" not in resp


# ---------------------------------------------------------------------------
# Scenario 8 — honest mixed outcome (one satisfied, one not)
# ---------------------------------------------------------------------------

def test_s8_mixed_outputs_honest_partial(cg4_mode, tmp_path):
    activity, resp = _journey(
        tmp_path,
        plans=[('{"skill_id": "timeline_investigation", "goal": "g", '
                '"steps": [{"type": "inspect_withdrawals", "reason": "r"}, '
                '{"type": "explain_signal", "reason": "r"}]}')],
        decisions=["CONTINUE", "CONTINUE", "STOP"],
        goal_llm=[_llm_payload(outputs=[
            _out("withdrawal_report", reqs=["withdrawal_records"],
                 subject="F3", scope="finding_level"),
            _out("laundering_judgment", kind="assessment",
                 reqs=["ml_signal_explanation"],
                 subject="F3", scope="finding_level")])],
        focus=FOCUS_F3)
    states = _outcomes(activity)
    assert states["O1"] == "satisfied"            # withdrawal records
    # the assessment output is never adjudicated into a fake satisfaction
    assert states["O2"] in ("unresolved", "pending")
    assert activity["goal_outcome"] in ("partial", "unanswerable")
    assert "已部分回答" in resp or "无法回答" in resp
    assert "本次调查目标已完整回答" not in resp


# ---------------------------------------------------------------------------
# Scenario 9 — RE_PLAN preserves all accepted outputs
# ---------------------------------------------------------------------------

def test_s9_replan_preserves_outputs_and_contract(cg4_mode, tmp_path):
    activity, resp = _journey(
        tmp_path,
        plans=[PLAN_POLICY,                      # plan 1: policy view
               PLAN_WITHDRAW_ONLY],              # re-plan: withdrawal view
        decisions=["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"],
        goal_llm=[_llm_payload(outputs=[
            _out("policy_requirements", reqs=["policy_references"],
                 subject="F3", scope="finding_level"),
            _out("withdrawal_report", reqs=["withdrawal_records"],
                 subject="F3", scope="finding_level")])],
        focus=FOCUS_F3)
    # RE_PLAN happened and was adopted
    assert activity["plan_revisions"] == 2
    # both outputs survived the re-plan: O1 keeps its plan-1 verdict
    # (never reset or downgraded by the re-plan); O2 was acquired by the
    # re-planned step. Neither output was dropped or narrowed.
    states = _outcomes(activity)
    assert set(states) == {"O1", "O2"}
    assert states["O2"] == "satisfied"            # withdrawals (re-plan)
    assert states["O1"] in ("satisfied", "unresolved", "pending")
    # the accepted contract content is intact after the re-plan
    semantic = _semantic(activity["goal_contract"])
    assert [o["claim"] for o in semantic["outputs"]] == [
        "policy_requirements", "withdrawal_report"]
    assert activity["agent_status"] == "completed"
    # GSP-7 wiring: the re-planned adoption re-ran coverage admission
    doc = _store_state(activity["task_id"])
    coverage = {r["output_id"]: r["coverage_status"]
                for r in doc["planning_coverage"]}
    assert coverage["O2"] == "covered"            # covered by the re-plan
    assert doc["step_output_map"], "step_output_map rebuilt for plan 2"

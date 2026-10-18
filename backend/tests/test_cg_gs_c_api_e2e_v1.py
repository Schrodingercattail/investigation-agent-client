"""WP-GS-C — coverage-incomplete API E2E (comparison intent uncovered).

The exact PMO-required scenario: a user asks a comparison question
about withdrawal destinations, the goal LLM proposes a valid
retrieval-only contract (no comparison facet), the deterministic
request-coverage detector identifies the uncovered comparison intent,
and the system discloses the gap instead of claiming the request was
fully answered.

Real FastAPI app (in-process TestClient), real Agent Loop, real
composer guard, real Activity projection. Model boundary seam-
deterministic via `goal_llm`.
"""

import json
import os
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import rp_patches

CASE = "U00010"
# Contract-absent guard wording (flag OFF path) — design §9.2.2:
NO_CLAIM = "不对“您的请求已得到完整回答”作出判定"
COVERAGE_GAP = "未能建立完整的目标分解"
# Coverage-incomplete disclosure (contract present) — design §7.3:
DISCLOSURE = "未能将您的比较需求建立为结构化交付目标"
NOT_CONCLUSION = "不构成“同一/不同”的结论"
UNCOVERED = "comparison_intent_uncovered"
COMPARISON_GOAL = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"
RETRIEVAL_ONLY_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "case_risk_overview", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["case_summary"]}]})
PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')


def _client(tmp_path):
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "cgsc_api.db"))
    p1, p2 = rp_patches()
    with p1, p2:
        from app.domain_tools import risk_case_fetch
        findings = list(risk_case_fetch("U00010").data["findings"])

    class _TurnScript:
        def generate(self, messages=None, max_tokens=0, temperature=0.1):
            return ""

    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(_TurnScript()), executor=None,
            task_store=store),
        store=store,
        case_findings_provider=lambda case_id: findings)
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _journey(client, tmp_path, *, flag, goal_llm_text, plans, decisions,
             goal):
    session = {"plans": plans, "decisions": decisions,
               "goal_llm": [goal_llm_text]}
    session_path = str(tmp_path / "cgsc_session.json")
    with open(session_path, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False)
    os.environ["AGENT_LOOP_SCRIPTED_SESSION_FILE"] = session_path
    from app.config import settings
    # GS-E isolation fix: save/restore — previously the last test's flag
    # leaked to every later file in the process.
    _prev_flag = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
    settings.ENABLE_GOAL_SEMANTIC_CONTRACT = flag
    try:
        inv = client.post("/api/v2/investigations",
                          json={"case_id": "U00010"})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        # QA-01 surface: the focused finding travels IN the goal request —
        # applied and persisted synchronously before the loop thread
        # starts. A separate /context roundtrip here races the resolution
        # boundary: when the focus is lost the comparison request falls to
        # real-LLM target resolution, which is nondeterministically
        # AMBIGUOUS for this text (F3 vs F4 both match) — observed as
        # sporadic clarification_needed terminals.
        run = client.post(
            f"/api/v2/investigations/{inv_id}/goal",
            json={"goal": goal,
                  "context_action": {"type": "focus_finding",
                                     "finding_id": "F3"}})
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]
        deadline = time.time() + 60
        while time.time() < deadline:
            a = client.get(f"/api/v2/tasks/{task_id}/activity").json()
            if a["is_terminal"]:
                break
            time.sleep(0.1)
        assert a["is_terminal"], "loop did not terminate"
        detail = client.get(f"/api/v2/tasks/{task_id}").json()

        def _diag():
            return (" [terminal state: agent_status="
                    f"{a.get('agent_status')!r} stop_reason="
                    f"{a.get('stop_reason')!r} outcome="
                    f"{a.get('goal_outcome')!r} decision="
                    f"{json.dumps(a.get('goal_contract_decision'), ensure_ascii=False)} "
                    f"response={detail.get('task_response_text')!r}]")

        return a, (detail.get("task_response_text") or ""), task_id, _diag
    finally:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = _prev_flag
        os.environ.pop("AGENT_LOOP_SCRIPTED_SESSION_FILE", None)


def test_coverage_incomplete_full_path(tmp_path):
    """The exact audited scenario: user asks a comparison question, the
    goal LLM proposes only a retrieval-only output (no comparison
    facet), the contract IS accepted, the coverage detector marks
    comparison_intent_uncovered, and the composer discloses the gap —
    never claiming the comparison was answered."""
    client = _client(tmp_path)
    payload = json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "case_risk_overview", "claim_kind": "retrieval",
             "subject_ref": "U00010", "scope": "case_level",
             "evidence_requirements": ["case_summary"]}]})
    plans = [PLAN_FETCH]
    decisions = ["STOP"]
    goal = COMPARISON_GOAL

    activity, resp, task_id, diag = _journey(
        client, tmp_path, flag=True, goal_llm_text=payload,
        plans=plans, decisions=decisions, goal=goal)

    contract = activity["goal_contract"]
    assert contract is not None
    assert contract["goal_type"] == "policy_check"
    assert len(contract["outputs"]) == 1
    o = contract["outputs"][0]
    assert o["postcondition_facets"] == []
    decision = activity["goal_contract_decision"]
    assert decision["coverage"] == "comparison_intent_uncovered"
    assert o["satisfaction"] == "satisfied"
    assert activity["goal_outcome"] == "complete"
    assert DISCLOSURE in resp, diag()
    assert NOT_CONCLUSION in resp, diag()
    # the whole-request completion claim is replaced by the basis-scoped
    # outcome line — the loop never presents completion as the comparison
    # being answered
    assert "本次调查目标已完整回答" not in resp


def test_flag_off_guard_active(tmp_path):
    """Flag OFF: the completion-claim guard is flag-independent."""
    client = _client(tmp_path)
    payload = json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "case_risk_overview", "claim_kind": "retrieval",
             "subject_ref": "U00010", "scope": "case_level",
             "evidence_requirements": ["case_summary"]}]})
    plans = [PLAN_FETCH]
    decisions = ["STOP"]
    goal = COMPARISON_GOAL

    activity, resp, task_id, diag = _journey(
        client, tmp_path, flag=False, goal_llm_text=payload,
        plans=plans, decisions=decisions, goal=goal)

    assert activity["agent_status"] == "completed"
    assert activity["goal_contract"] is None
    assert activity["goal_outcome"] is None
    assert COVERAGE_GAP in resp, diag()
    assert "本次调查目标已完整回答" not in resp, diag()


def test_coverage_incomplete_survives_replan_convergence(tmp_path):
    """The comparison intent is uncovered, the retrieval runs, and the
    scripted proposer keeps CONTINUEing: the re-plan re-runs the same
    retrieval, the no-progress convergence guard stops the loop, and the
    closure still discloses the uncovered comparison — coverage survives
    re-plan and re-admission (GSP-7 contract persistence)."""
    client = _client(tmp_path)
    payload = json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "case_risk_overview", "claim_kind": "retrieval",
             "subject_ref": "U00010", "scope": "case_level",
             "evidence_requirements": ["case_summary"]}]})
    fetch_plan = json.dumps({
        "skill_id": "case_intake", "goal": "g",
        "steps": [{"type": "fetch_case", "reason": "r"}]})

    activity, resp, task_id, diag = _journey(
        client, tmp_path, flag=True, goal_llm_text=payload,
        plans=[fetch_plan, fetch_plan, fetch_plan],
        decisions=["CONTINUE", "CONTINUE", "CONTINUE"],
        goal=COMPARISON_GOAL)

    assert activity["agent_status"] == "completed"
    contract = activity["goal_contract"]
    assert contract is not None
    assert contract["outputs"][0]["satisfaction"] == "satisfied"
    decision = activity["goal_contract_decision"]
    assert decision["coverage"] == "comparison_intent_uncovered"
    assert DISCLOSURE in resp, diag()
    assert "本次调查目标已完整回答" not in resp, diag()


def test_coverage_incomplete_not_upgraded_via_existence_output(tmp_path):
    """The same no-upgrade invariant with a NON-retrieval deliverable:
    an existence-check output (hedge comparison naming) that IS
    satisfied must not let the closure claim the same-or-different
    comparison was answered."""
    client = _client(tmp_path)
    payload = json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "hedge_comparison", "claim_kind": "existence_check",
             "subject_ref": "U00010", "scope": "case_level",
             "evidence_requirements": ["opposite_trade_relations"]}]})
    plans = [json.dumps({
        "skill_id": "case_trade_analysis", "goal": "g",
        "steps": [{"type": "analyze_cross_account_trades",
                   "reason": "r"}]})]

    activity, resp, task_id, diag = _journey(
        client, tmp_path, flag=True, goal_llm_text=payload,
        plans=plans, decisions=["STOP"], goal=COMPARISON_GOAL)

    assert activity["goal_outcome"] in ("complete", "partial"), diag()
    decision = activity["goal_contract_decision"]
    assert decision["coverage"] == "comparison_intent_uncovered"
    assert DISCLOSURE in resp, diag()
    assert "本次调查目标已完整回答" not in resp, diag()

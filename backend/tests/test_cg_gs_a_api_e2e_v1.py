"""WP-GS-A — completion-safety guard over the REAL HTTP API surface.

In-process TestClient against the real FastAPI app: the closure guard,
response composition, Activity projection and terminal status all run
through the production path. The model boundary is deterministic via
the approved session seam (`goal_llm` model text); the goal checkpoint,
contract acceptance/rejection, loop, composer guard and Activity
projection are production code.

Both flag modes are validated with NO accepted contract: the closure
states the execution/evidence basis and withholds the whole-request
answer judgment (INV-C, flag-independent).
"""

import json
import time

from fastapi.testclient import TestClient

from app.config import settings
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import rp_patches

CASE = "U00010"
GUARD_MARK = "不对“您的请求已得到完整回答”作出判定"
COVERAGE_MARK = "未能建立完整的目标分解"
PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')
NEUTRAL_GOAL = "帮我梳理一下这个账号目前的情况。"

INFERRED_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "case_risk_overview", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["case_summary"]}]})

EMPTY_PAYLOAD = ""          # LLM empty → heuristic fallback → no contract


def _client(tmp_path):
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "cgsa_api.db"))
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


def _journey(client, tmp_path, *, flag, goal_llm_text):
    import os
    session = {"plans": [PLAN_FETCH], "decisions": ["STOP"],
               "goal_llm": [goal_llm_text]}
    session_path = str(tmp_path / "cgsa_session.json")
    with open(session_path, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False)
    os.environ["AGENT_LOOP_SCRIPTED_SESSION_FILE"] = session_path
    # GS-E isolation fix: the flag parameter is now APPLIED and restored
    # — previously it was ignored, so this suite depended on file order
    # (flag_off silently inherited OFF from process defaults and failed
    # whenever an earlier file left the flag ON).
    _prev_flag = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
    settings.ENABLE_GOAL_SEMANTIC_CONTRACT = flag
    try:
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = client.post(
            f"/api/v2/investigations/{inv_id}/goal",
            json={"goal": NEUTRAL_GOAL,
                  "context_action": {"type": "focus_finding",
                                     "finding_id": "F3"}})
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]
        deadline = time.time() + 60
        while time.time() < deadline:
            activity = client.get(
                f"/api/v2/tasks/{task_id}/activity").json()
            if activity["is_terminal"]:
                break
            time.sleep(0.1)
        assert activity["is_terminal"], "loop did not terminate"
        detail = client.get(f"/api/v2/tasks/{task_id}").json()
        return activity, (detail.get("task_response_text") or "")
    finally:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = _prev_flag
        os.environ.pop("AGENT_LOOP_SCRIPTED_SESSION_FILE", None)


class TestCompletionSafetyAcrossModes:
    def test_flag_off_contractless(self, tmp_path):
        client = _client(tmp_path)
        activity, resp = _journey(client, tmp_path, flag=False,
                                  goal_llm_text=INFERRED_PAYLOAD)
        assert activity["agent_status"] == "completed"
        assert activity["goal_contract"] is None
        assert activity["goal_outcome"] is None
        # INV-C: execution completed, whole-request answer withheld
        assert "系统未建立目标语义契约" in resp
        assert COVERAGE_MARK in resp
        assert "本次调查目标已完整回答" not in resp

    def test_flag_on_contractless(self, tmp_path):
        client = _client(tmp_path)
        activity, resp = _journey(client, tmp_path, flag=True,
                                  goal_llm_text=EMPTY_PAYLOAD)
        assert activity["agent_status"] == "completed"
        assert activity["goal_contract"] is None
        assert activity["goal_outcome"] is None
        assert "系统未建立目标语义契约" in resp
        assert COVERAGE_MARK in resp
        assert "本次调查目标已完整回答" not in resp



TWO_OUT_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "case_risk_overview", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["case_summary"]},
        {"claim": "network_relations_report", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["network_relations"]}]})

MIXED_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "case_risk_overview", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["case_summary"]},
        {"claim": "network_relations_report", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["network_relations", "ml_signal_explanation"]}]})


def _seam_journey(client, tmp_path, *, payload, plans, decisions,
                  repeat_observation=False):
    """Seam-deterministic contract journey (flag ON required for
    contract acceptance). Returns (activity, response_text, task_id)."""
    import os
    plans_all = list(plans)
    if repeat_observation:
        plans_all = plans_all + [plans[-1]]
    session = {"plans": plans_all, "decisions": decisions,
               "goal_llm": [payload]}
    session_path = str(tmp_path / "cgsa_seam_session.json")
    with open(session_path, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False)
    os.environ["AGENT_LOOP_SCRIPTED_SESSION_FILE"] = session_path
    _prev_flag = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
    settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
    try:
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = client.post(
            f"/api/v2/investigations/{inv_id}/goal",
            json={"goal": NEUTRAL_GOAL,
                  "context_action": {"type": "focus_finding",
                                     "finding_id": "F3"}})
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]
        deadline = time.time() + 90
        while time.time() < deadline:
            activity = client.get(
                f"/api/v2/tasks/{task_id}/activity").json()
            if activity["is_terminal"]:
                break
            time.sleep(0.1)
        assert activity["is_terminal"], "loop did not terminate"
        detail = client.get(f"/api/v2/tasks/{task_id}").json()
        return activity, (detail.get("task_response_text") or ""), task_id
    finally:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = _prev_flag
        os.environ.pop("AGENT_LOOP_SCRIPTED_SESSION_FILE", None)


class TestValidContractSemanticOutcomes:
    """API E2E cases 3–4 (plan §12): valid accepted contracts —
    contract-relative completion preserved; partial/unresolved never
    upgraded to COMPLETE by execution termination."""

    def test_case3_valid_contract_all_satisfied(self, monkeypatch,
                                                tmp_path):
        """All outputs SATISFIED → contract-relative COMPLETE + full
        outcome line; guard and coverage disclosure not applicable."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        client = _client(tmp_path)
        activity, resp, task_id = _seam_journey(
            client, tmp_path, payload=INFERRED_PAYLOAD,
            plans=[PLAN_FETCH], decisions=["STOP"])
        contract = activity["goal_contract"]
        assert contract is not None
        assert all(o["satisfaction"] == "satisfied"
                   for o in contract["outputs"])
        assert activity["goal_outcome"] == "complete"
        assert "本次调查目标已完整回答" in resp
        assert "未能建立完整的目标分解" not in resp
        assert "comparison_intent_uncovered" not in json.dumps(
            activity["goal_contract_decision"])

    def test_case4_partial_not_upgraded(self, monkeypatch, tmp_path):
        """One covered output (satisfied) + one output whose evidence the
        plan never acquires (pending) → GoalOutcome PARTIAL; the
        response must not claim whole-request completion despite the
        successful execution termination."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        client = _client(tmp_path)
        activity, resp, task_id = _seam_journey(
            client, tmp_path, payload=TWO_OUT_PAYLOAD,
            plans=[PLAN_FETCH], decisions=["STOP"])
        contract = activity["goal_contract"]
        states = {o["output_id"]: o["satisfaction"]
                  for o in contract["outputs"]}
        assert "satisfied" in states.values()
        assert "pending" in states.values()
        assert activity["goal_outcome"] == "partial"
        assert "已部分回答" in resp
        assert "本次调查目标已完整回答" not in resp

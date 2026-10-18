"""WP-CG-3 — Contract-less safety over the REAL HTTP API surface.

API E2E for the CG-3 scope (plan §9): real FastAPI routes, real goal
checkpoint, real termination policy, real composer, real activity
projection. The model boundary is controlled with MINIMAL test-layer
injection only:

- the existing scripted-session seam (`plans`/`decisions`/`goal`
  sections) scripts the planner/deciders and, where used, the goal
  representation template;
- for LLM_EMPTY / INVALID_PROPOSAL the DEFAULT goal resolver factory is
  monkeypatched to call the REAL `understand_goal` with a scripted
  failing/LLM payload (the extraction path itself runs for real).

No final GoalContract is injected; no AgentLoopState is written; the
Agent Loop is never mocked. Full multi-scenario seam frameworks are
WP-CG-4 scope and deliberately not built here.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
import tests.test_risk_platform_adapter_v2 as fx
from tests.test_api_investigations_v1 import rp_patches
from tests.test_gsp7_replan_api_e2e_v1 import (
    GOAL_SECTION as VALID_GOAL_SECTION, _await_terminal)

CASE = "U00299"
GOAL = "这个账号有没有对冲交易？另外把它的关联账号也列出来。"
NEUTRAL_GOAL = "帮我梳理一下这个账号目前的情况。"
PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')

# a genuine-ambiguity goal representation (seam template)
AMBIGUOUS_GOAL_SECTION = {
    "case_id": CASE, "goal_type": None, "goal_resolution": "AMBIGUOUS",
    "subject_type": "case", "subject_ref": CASE,
    "ambiguous_goal_types": ["cross_account_check",
                             "identify_related_accounts"],
    "reason": "scripted genuine ambiguity", "source": "llm",
    "request_text": GOAL, "contract_proposal": None}

# an INFERRED payload whose proposal carries an illegal evidence type
# (the REAL understand_goal + build_goal_contract must reject it)
BAD_LLM_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "money_laundering_assessment",
         "claim_kind": "assessment", "subject_ref": CASE,
         "scope": "account_level",
         "evidence_requirements": ["fund_flow"]}]})


def _client(monkeypatch, tmp_path):
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "cg3_api_e2e.db"))
    p1, p2 = rp_patches()
    with p1, p2:
        from app.domain_tools import risk_case_fetch
        findings = list(risk_case_fetch(CASE).data["findings"])

    class _TurnScript:
        def generate(self, messages=None, max_tokens=0, temperature=0.1):
            return ""

    api = InvestigationAPI(
        service=InvestigationService(planner=PlannerV2(_TurnScript()),
                                     executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda case_id: findings)
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _write_session(tmp_path, *, goal=None, llm_scripts=None):
    session = {"plans": [PLAN_FETCH], "decisions": ["STOP"]}
    if goal is not None:
        session["goal"] = goal
    path = tmp_path / "cg3_session.json"
    path.write_text(json.dumps(session, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def _patch_resolver(monkeypatch, *, fail=False, payload=None):
    """Minimal DI: the DEFAULT goal resolver factory runs the REAL
    understand_goal against a scripted failing/LLM payload."""
    import app.api.investigations as inv_module
    from app.goal_resolution import understand_goal

    class _LLM:
        def __init__(self, text):
            self.text = text

        def generate(self, messages=None, max_tokens=0, temperature=0.1):
            if self.text is None:
                raise RuntimeError("provider returned no text")
            return self.text

    script = None if fail else payload

    def factory():
        def resolve(request_text, focused_finding_id, case_id,
                    diagnostics=None):
            return understand_goal(
                request_text, resolved_finding_id=focused_finding_id,
                case_id=case_id, use_llm=True, llm=_LLM(script),
                diagnostics=diagnostics)
        return resolve

    monkeypatch.setattr(inv_module, "_default_goal_resolver", factory)


def _journey(client, monkeypatch, tmp_path, *, flag, goal_section=None,
             fail=False, payload=None, goal=GOAL, focus=None):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", flag)
    monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE",
                       _write_session(tmp_path, goal=goal_section))
    if fail or payload is not None:
        _patch_resolver(monkeypatch, fail=fail, payload=payload)
    p1, p2 = rp_patches()
    with p1, p2:
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE})
        assert inv.status_code == 200, inv.text
        inv_id = inv.json()["investigation"]["investigation_id"]
        body = {"goal": goal}
        if focus:
            body["context_action"] = {"type": "focus_finding",
                                      "finding_id": focus}
        run = client.post(f"/api/v2/investigations/{inv_id}/goal",
                          json=body)
        assert run.status_code == 200, run.text
        task_id = run.json()["task_id"]
        activity = _await_terminal(client, task_id)
        detail = client.get(f"/api/v2/tasks/{task_id}").json()
    return activity, detail


class TestScenarioA_AmbiguousBoundedStop:
    def test_ambiguous_terminates_before_planning(self, tmp_path,
                                                  monkeypatch):
        client = _client(monkeypatch, tmp_path)
        activity, detail = _journey(
            client, monkeypatch, tmp_path, flag=True,
            goal_section=AMBIGUOUS_GOAL_SECTION)
        assert activity["agent_status"] == "blocked"
        assert activity["stop_reason"] == "clarification_needed"
        assert activity["goal_contract"] is None
        assert activity["goal_contract_decision"] == {
            "outcome": "ambiguous_not_executed",
            "reason": "genuine goal ambiguity; clarification required "
                      "before investigation"}
        assert activity["plan_steps"] == []          # zero planning
        assert activity["iteration"] == 0
        tool_rows = [r for r in activity["activity"]
                     if r["event_type"] in ("tool.called",
                                            "tool.completed")]
        assert tool_rows == []                        # zero tool execution
        resp = detail.get("task_response_text") or ""
        assert "无法确定您要调查的目标" in resp
        assert "已完整回答" not in resp
        blocked_rows = [r for r in activity["activity"]
                        if r["event_type"] == "agent.blocked"]
        assert blocked_rows and "无法确定您要调查的目标" in \
            (blocked_rows[-1]["summary"] or "")


class TestScenarioB_LlmEmpty:
    def test_llm_empty_guarded_restricted_execution(self, tmp_path,
                                                    monkeypatch):
        client = _client(monkeypatch, tmp_path)
        activity, detail = _journey(
            client, monkeypatch, tmp_path, flag=True, fail=True,
            goal=NEUTRAL_GOAL, focus="F3")
        decision = activity["goal_contract_decision"]
        assert decision["outcome"] == "llm_empty"
        assert "provider returned no text" in (decision["reason"] or "")
        # heuristic-routed restricted execution happened (fetch plan)
        assert activity["agent_status"] == "completed"
        # but the response may NOT claim whole-request completion
        resp = detail.get("task_response_text") or ""
        assert "未能建立完整的目标分解" in resp
        assert "已完整回答" not in resp
        stop_rows = [r for r in activity["activity"]
                     if r["event_type"] == "decision.made"]
        assert stop_rows and stop_rows[-1]["semantic_basis"] == "none"


class TestScenarioC_InvalidProposal:
    def test_invalid_proposal_rejected_no_narrowing(self, tmp_path,
                                                    monkeypatch):
        client = _client(monkeypatch, tmp_path)
        activity, detail = _journey(
            client, monkeypatch, tmp_path, flag=True,
            payload=BAD_LLM_PAYLOAD, goal=NEUTRAL_GOAL, focus="F3")
        decision = activity["goal_contract_decision"]
        assert decision["outcome"] == \
            "invalid_proposal:invalid_evidence_type"
        assert activity["goal_contract"] is None
        assert activity["agent_status"] == "completed"
        resp = detail.get("task_response_text") or ""
        assert "未能建立完整的目标分解" in resp
        # the invalid output was never silently adopted as a narrower
        # complete contract
        assert "整体结果" not in resp


class TestScenarioD_LegalSingleGoalContract:
    def test_legal_contract_keeps_gsp_path(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, tmp_path)
        activity, detail = _journey(
            client, monkeypatch, tmp_path, flag=True,
            goal_section=VALID_GOAL_SECTION, goal=NEUTRAL_GOAL,
            focus="F3")
        assert activity["goal_contract"] is not None
        assert activity["goal_contract_decision"] == {
            "outcome": "contract_accepted", "reason": None}
        assert activity["agent_status"] == "completed"
        resp = detail.get("task_response_text") or ""
        assert "整体结果" in resp                    # GSP-5 outcome line
        assert "未能建立完整的目标分解" not in resp   # guard not applicable


class TestScenarioE_FlagOffIdentity:
    def test_flag_off_baseline_unchanged(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, tmp_path)
        activity, detail = _journey(
            client, monkeypatch, tmp_path, flag=False,
            goal_section=AMBIGUOUS_GOAL_SECTION, goal=NEUTRAL_GOAL,
            focus="F3")
        # legacy flow: proceeds, no provenance, no guard artifacts
        assert activity["agent_status"] == "completed"
        assert activity["stop_reason"] == "goal_satisfied"
        assert activity["goal_contract_decision"] is None
        assert activity["goal_contract"] is None
        resp = detail.get("task_response_text") or ""
        # WP-GS-A INV-C: the completion-claim guard is flag-independent
        assert "未能建立完整的目标分解" in resp

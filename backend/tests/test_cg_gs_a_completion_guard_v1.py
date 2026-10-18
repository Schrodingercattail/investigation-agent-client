"""WP-GS-A — Universal completion-claim guard + legacy DI seam tests.

INV-C (flag-independent): a user-visible loop response must not claim
that the user's original request was fully answered unless an accepted
GoalContract and its per-output semantic results provide that basis.

- Contract-less COMPLETED runs (either flag state) carry the
  basis-scoped closure: execution/evidence completion is stated, a
  whole-request answer claim is explicitly withheld.
- Contract runs keep the GSP-5 contract-relative outcome line (a
  satisfied contract renders 已完整回答 — contract-relative, correct).
- LEGACY wording (byte-exact pre-CG closure, no guard line) is
  reachable ONLY through the test-only dependency-injection seam
  (`_completion_closure_policy_override`); the canonical runtime has
  no path to it.
- AgentStatus.COMPLETED / StopReason.GOAL_SATISFIED alone never
  produce a semantic COMPLETE claim on contract-less runs.
"""

import json
import tempfile
from unittest.mock import patch

import app.agent_loop as al
from app.agent_loop import AgentLoopRunner, _completion_closure_policy
from app.config import settings
from app.models import InvestigationContext
from app.task_store_v2 import TaskStoreV2

from tests.test_al351_target_validity_guard_v1 import _build_runner
from tests.test_api_investigations_v1 import make_findings, rp_patches
import tests.test_risk_platform_adapter_v2 as fx

PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')
GUARD_MARK = "不对“您的请求已得到完整回答”作出判定"


def _runner(monkeypatch, *, flag, seam=None):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", flag)
    if seam is not None:
        monkeypatch.setattr(al, "_completion_closure_policy_override",
                            seam)
    else:
        monkeypatch.setattr(al, "_completion_closure_policy_override",
                            None)
    findings = make_findings()
    runner = _build_runner(
        store=TaskStoreV2(tempfile.mkdtemp() + "/cgsa.db"),
        resolver=None,
        planner_scripts=(PLAN_FETCH,),
        decisions=("STOP",))
    return runner, findings


def _run(runner, findings, inv, task,
         goal="帮我梳理一下这个账号目前的情况。"):
    with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
               "fetch_case",
               new=lambda self, uid: (
                   fx.rp_evidence_payload(uid),
                   fx.rp_explanation_payload())):
        return runner.run(goal, InvestigationContext(case_id="U00010"),
                          findings, investigation_id=inv, task_id=task,
                          findings_provider=lambda: findings)


class TestUniversalGuard:
    def test_flag_off_contractless_guard_active(self, monkeypatch):
        runner, findings = _runner(monkeypatch, flag=False)
        result = _run(runner, findings, "INV-GSA-1", "TASK-GSA-1")
        resp = result.response or ""
        assert GUARD_MARK in resp
        assert "未能建立完整的目标分解" in resp
        assert result.state.agent_status == "completed"

    def test_flag_on_contractless_guard_active(self, monkeypatch):
        runner, findings = _runner(monkeypatch, flag=True)
        result = _run(runner, findings, "INV-GSA-2", "TASK-GSA-2")
        resp = result.response or ""
        assert GUARD_MARK in resp
        assert "未能建立完整的目标分解" in resp

    def test_valid_contract_keeps_gsp_outcome_line(self, monkeypatch):
        """A contract whose outputs are satisfied keeps the existing
        contract-relative outcome line; the guard never applies."""
        from app.goal_resolution import GoalRepresentation
        runner, findings = _runner(monkeypatch, flag=True)
        runner.goal_resolver = lambda req, fid, case, diag=None: \
            GoalRepresentation(
                case_id="U00010", goal_type="policy_check",
                goal_resolution="INFERRED", subject_type="case",
                subject_ref="U00010", ambiguous_goal_types=[],
                reason="r", source="llm", request_text="req",
                contract_proposal={"required_outputs": [
                    {"claim": "case_risk_overview",
                     "claim_kind": "retrieval", "subject_ref": "U00010",
                     "scope": "case_level",
                     "evidence_requirements": ["case_summary"]}]})
        result = _run(runner, findings, "INV-GSA-3", "TASK-GSA-3")
        resp = result.response or ""
        assert "整体结果" in resp
        assert "本次调查目标已完整回答" in resp      # contract-relative
        assert GUARD_MARK not in resp

    def test_non_completed_terminals_unchanged(self, monkeypatch):
        """BLOCKED terminals keep their honest wording; the guard is a
        COMPLETED-terminal rule and does not fabricate boundaries for
        runs that never executed."""
        runner, findings = _runner(monkeypatch, flag=False)
        runner.goal_resolver = lambda req, fid, case: None
        # force a blocked terminal: ambiguous target via the resolver stub
        from app.context_resolution import (
            ResolutionOutcome, ResolutionStatus)
        runner.resolver = type(
            "R", (), {"resolve": staticmethod(
                lambda *a, **k: ResolutionOutcome(
                    status=ResolutionStatus.AMBIGUOUS,
                    updated_context=InvestigationContext(
                        case_id="U00010"),
                    context_changed=False,
                    clarification_message="multiple possibilities"))})()
        result = _run(runner, findings, "INV-GSA-4", "TASK-GSA-4")
        assert result.state.agent_status == "blocked"
        resp = result.response or ""
        assert GUARD_MARK not in resp
        assert "blocked" in resp.lower() or "无法" in resp or \
            "multiple possibilities" in resp


class TestLegacyDISeam:
    def test_legacy_seam_byte_exact(self, monkeypatch):
        """The seam restores the byte-exact pre-CG closure wording
        (execution statement only, no guard notice) for baseline
        regression runs."""
        runner, findings = _runner(monkeypatch, flag=False, seam="LEGACY")
        result = _run(runner, findings, "INV-GSA-5", "TASK-GSA-5")
        resp = result.response or ""
        assert GUARD_MARK not in resp
        assert "The investigation completed." in resp

    def test_default_policy_is_guard_no_injection(self, monkeypatch):
        """Negative probe: without test injection the canonical path
        cannot select LEGACY — the policy defaults to GUARD."""
        monkeypatch.setattr(al, "_completion_closure_policy_override",
                            None)
        assert al._completion_closure_policy() == "GUARD"


class TestSemanticDistinctions:
    def test_agent_status_alone_no_semantic_complete(self, monkeypatch):
        runner, findings = _runner(monkeypatch, flag=False)
        result = _run(runner, findings, "INV-GSA-6", "TASK-GSA-6")
        s = result.state
        assert s.agent_status == "completed"          # execution fact
        assert s.stop_reason == "goal_satisfied"      # loop fact
        # no contract → no semantic result whatsoever
        assert s.goal_contract is None
        assert s.goal_outcome is None
        # and the closure withholds the whole-request answer claim
        assert GUARD_MARK in (result.response or "")

    def test_stop_reason_alone_no_semantic_complete(self, monkeypatch):
        runner, findings = _runner(monkeypatch, flag=True)
        result = _run(runner, findings, "INV-GSA-7", "TASK-GSA-7")
        s = result.state
        assert s.agent_status == "completed"
        assert s.stop_reason == "goal_satisfied"
        assert s.goal_contract is None                # no contract path
        assert s.goal_outcome is None                 # not derived
        assert GUARD_MARK in (result.response or "")

    def test_activity_projection_consistency(self, monkeypatch):
        from app.agent_loop import project_task_activity
        runner, findings = _runner(monkeypatch, flag=False)
        result = _run(runner, findings, "INV-GSA-8", "TASK-GSA-8")
        proj = project_task_activity(result.state)
        # execution facts served verbatim
        assert proj["agent_status"] == "completed"
        assert proj["stop_reason"] == "goal_satisfied"
        # semantic fields remain unset without a contract
        assert proj["goal_outcome"] is None
        assert proj["goal_contract"] is None
        assert proj["goal_contract_decision"] is None

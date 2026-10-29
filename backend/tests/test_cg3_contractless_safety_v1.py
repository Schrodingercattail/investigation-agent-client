"""WP-CG-3 — Contract-less Safety and Decision-language Guard tests.

Covers the L1 surface of the approved plan §6:
- goal_contract_decision provenance (accepted / no_proposal /
  invalid_proposal:<code> / llm_empty / heuristic_single_goal /
  ambiguous_not_executed) — machine-distinguishable;
- genuine AMBIGUOUS → clarification-shaped bounded termination BEFORE
  any planning or tool execution (flag ON), legacy flow unchanged
  (flag OFF);
- the composer completion-claim guard (flag ON + no contract + COMPLETED
  → coverage boundary line, never a whole-request completion claim;
  partial results kept; flag OFF byte-identical legacy text);
- decision-row semantic_basis + STOP safe summary in the served activity;
- old persisted states without the new fields load unchanged;
- AgentStatus.COMPLETED / GOAL_SATISFIED alone never produce a semantic
  COMPLETE claim on contract-less runs.

All runs use the deterministic component harness (scripted planner /
decisions / resolvers) — no live model.
"""

import json
import tempfile
from unittest.mock import patch

from app.agent_loop import AgentLoopRunner
from app.config import settings
from app.goal_resolution import GoalRepresentation, understand_goal
from app.models import InvestigationContext
from app.task_store_v2 import TaskStoreV2

from tests.test_al351_target_validity_guard_v1 import (
    _build_runner, _resolved_outcome, _CustomResolver)
from tests.test_api_investigations_v1 import make_findings, rp_patches
import tests.test_risk_platform_adapter_v2 as fx


def _repr(goal_resolution="INFERRED", goal_type="policy_check",
          source="llm", proposal=None, reason="r"):
    return GoalRepresentation(
        case_id="U00299",
        goal_type=goal_type,
        goal_resolution=goal_resolution,
        subject_type="case",
        subject_ref="U00299",
        ambiguous_goal_types=(["cross_account_check",
                               "identify_related_accounts"]
                              if goal_resolution == "AMBIGUOUS" else []),
        reason=reason,
        source=source,
        request_text="req",
        contract_proposal=proposal,
    )


PLAN_SIGNAL = ('{"skill_id": "timeline_investigation", "goal": "g", '
               '"steps": [{"type": "explain_signal", "reason": "r"}]}')
# a clean SUCCESS that satisfies question_answered criteria without any
# evidence gap → a deterministic COMPLETED contract-less run for the
# composer-guard tests
PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')

# a proposal whose evidence requirement is not in the frozen vocabulary
BAD_PROPOSAL = {"required_outputs": [
    {"claim": "money_laundering_assessment", "claim_kind": "assessment",
     "subject_ref": "U00299", "scope": "account_level",
     "evidence_requirements": ["fund_flow"]}]}


def _runner(store, *, monkeypatch=None, decisions=("CONTINUE", "STOP"),
            planner_scripts=(PLAN_SIGNAL,), goal_repr=None, flag=True):
    """Component harness: real loop + real composer + scripted boundary."""
    findings = make_findings()
    runner = _build_runner(
        store,
        resolver=_CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding")),
        planner_scripts=list(planner_scripts),
        decisions=tuple(decisions))
    if goal_repr is not None:
        runner.goal_resolver = lambda req, fid, case: goal_repr
    else:
        runner.goal_resolver = lambda req, fid, case: None
    if monkeypatch is not None:
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            flag)
    else:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = flag
    return runner, findings


def _run(runner, findings, inv, task,
         goal="这个账号有没有对冲交易？另外把它的关联账号也列出来。"):
    with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter."
               "fetch_case",
               new=lambda self, uid: (
                   fx.rp_evidence_payload(uid),
                   fx.rp_explanation_payload())):
        return runner.run(goal,
                          InvestigationContext(case_id="U00299"),
                          findings, investigation_id=inv, task_id=task,
                          findings_provider=lambda: findings)


# a neutral goal that reaches the scripted LLM (no deterministic
# planner branch fires on it) for the composer-guard runs
NEUTRAL_GOAL = "帮我梳理一下这个账号目前的情况。"


class TestAmbiguousDisposition:
    def test_ambiguous_stops_before_planning_flag_on(self, monkeypatch):
        """flag ON + genuine AMBIGUOUS → ambiguous_not_executed, bounded
        clarification-shaped terminal, ZERO planner/tool activity."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_amb.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch,
            decisions=("CONTINUE", "STOP"),
            goal_repr=_repr(goal_resolution="AMBIGUOUS",
                            goal_type=None, source="llm"))
        planner_calls = []
        original = runner.planner.plan

        def _spy(*a, **kw):
            planner_calls.append(1)
            return original(*a, **kw)

        runner.planner.plan = _spy
        result = _run(runner, findings, "INV-CG3-A", "TASK-CG3-A")
        s = result.state
        _dec = s.goal_contract_decision
        assert _dec["outcome"] == "ambiguous_not_executed"
        assert _dec["reason"] == ("genuine goal ambiguity; clarification "
                                  "required before investigation")
        assert _dec["understanding"]["schema"] == "gu-decision/1"
        assert s.agent_status == "blocked"
        assert s.stop_reason == "clarification_needed"
        assert planner_calls == []              # planner never ran
        assert s.observations == []             # zero tool executions
        # honest bounded message, no completion claim
        assert "无法确定您要调查的目标" in (result.response or "")
        assert "已完整回答" not in (result.response or "")

    def test_ambiguous_flag_off_legacy_flow(self, monkeypatch):
        """flag OFF + AMBIGUOUS → today's behavior: the run proceeds
        (representation recorded, no contract, no decision record, no
        clarification stop), scripted plan executes."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_amboff.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch,
            decisions=("CONTINUE", "STOP"),
            planner_scripts=(PLAN_FETCH,),
            goal_repr=_repr(goal_resolution="AMBIGUOUS",
                            goal_type=None, source="llm"),
            flag=False)
        result = _run(runner, findings, "INV-CG3-A2", "TASK-CG3-A2",
                      goal=NEUTRAL_GOAL)
        s = result.state
        assert s.agent_status == "completed"     # legacy flow unchanged
        assert s.stop_reason == "goal_satisfied"
        # B1: flag OFF is a RECORDED decision state (not silence)
        assert s.goal_contract_decision["outcome"] == "flag_off"
        assert s.goal_contract_decision["understanding"][
            "flag_enabled"] is False
        assert s.goal_contract is None
        # WP-GS-A INV-C: the completion-claim guard is flag-independent —
        # the coverage boundary renders under OFF as well (the only
        # OFF-mode user-visible delta approved by revision 3)
        assert "未能建立完整的目标分解" in (result.response or "")


class TestDecisionProvenance:
    def test_contract_accepted_recorded(self, monkeypatch):
        from tests.test_gsp2_goal_contract_v1 import _proposal as gsp2p, \
            _repr as gsp2r
        repr_obj = gsp2r([gsp2p(subject="U00299", scope="case_level",
                                evidence=["case_summary"])],
                         subject="U00299", case_id="U00299")
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_acc.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, goal_repr=repr_obj)
        result = _run(runner, findings, "INV-CG3-P", "TASK-CG3-P")
        _dec = result.state.goal_contract_decision
        assert _dec["outcome"] == "contract_accepted" and _dec["reason"] is None
        assert _dec["understanding"]["contract"]["decision"] == "accepted"
        assert result.state.goal_contract is not None

    def test_invalid_proposal_distinguished_and_not_unsupported(self, monkeypatch):
        """A structurally-present but invalid proposal →
        invalid_proposal:<code>; never capability UNSUPPORTED."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_inv.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, goal_repr=_repr(proposal=BAD_PROPOSAL))
        result = _run(runner, findings, "INV-CG3-B", "TASK-CG3-B")
        s = result.state
        outcome = s.goal_contract_decision["outcome"]
        assert outcome.startswith("invalid_proposal:")
        assert outcome.endswith("invalid_evidence_type")
        assert s.goal_contract is None
        # extraction failure is never a capability verdict
        for obs in s.observations:
            assert obs.get("outcome") != "unsupported" or \
                obs.get("step_type") != "goal_resolution"
        assert s.stop_reason != "capability_missing"

    def test_llm_empty_vs_heuristic_single_goal(self, monkeypatch):
        """The checkpoint distinguishes an LLM-attempted run that fell
        back to the heuristic (llm_empty) from a clean heuristic
        classification (heuristic_single_goal).

        Request-coverage closure update: for a request matching a
        BOUNDED supported pattern, the deterministic proposer now
        supplies the required-outputs proposal even when the goal LLM
        is down — the contract is accepted (source=heuristic) and the
        run gets real per-output verification instead of degrading to
        the contractless guard. The llm_empty provenance therefore only
        applies to requests NO deterministic pattern covers (asserted
        here with a deliberately unmatched ask)."""
        diagnostics = {}
        captured = {}

        def real_resolver(req, fid, case, diag=None):
            captured["diag"] = diag
            rep = understand_goal(req, resolved_finding_id=fid,
                                  case_id=case, use_llm=True,
                                  llm=_ExplodingLLM(),
                                  diagnostics=diag)
            return rep

        class _ExplodingLLM:
            def generate(self, messages=None, max_tokens=0,
                         temperature=0.1):
                raise RuntimeError("provider returned no text")

        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_empty.db")
        # GS-E isolation fix: monkeypatch passed through — flag set AND
        # restored (a bare set here leaked True process-wide)
        runner, findings = _runner(store, monkeypatch=monkeypatch)
        runner.goal_resolver = real_resolver

        # (a) UNMATCHED request: llm_empty provenance, contractless —
        # the uncertainty is preserved, never papered over
        result = _run(runner, findings, "INV-CG3-C", "TASK-CG3-C",
                      goal="这个账号的整体风险水平怎么样？")
        s = result.state
        assert s.goal_contract_decision["outcome"] == "llm_empty"
        assert "provider returned no text" in \
            s.goal_contract_decision["reason"]
        # heuristic classification alone is NOT full comprehension
        assert s.goal_contract is None

        # (b) SUPPORTED pattern + llm down: the deterministic proposal
        # carries the recognized deliverables — contract accepted, real
        # verification instead of the generic guard. Bounded-vocabulary
        # honesty: this phrasing's hedging ask lacks cross-account SCOPE
        # vocabulary, so only the related-accounts deliverable is
        # proposed (the unrecognized part keeps its uncertainty — no
        # invented coverage).
        result2 = _run(runner, findings, "INV-CG3-C2", "TASK-CG3-C2",
                       goal="这个账号有没有对冲交易？另外把它的关联账号也列出来。")
        s2 = result2.state
        assert s2.goal_contract_decision["outcome"] == "contract_accepted"
        assert s2.goal_contract is not None
        claims = " ".join(o.claim for o in s2.goal_contract.outputs)
        assert "关联" in claims

        # clean heuristic path (no LLM attempted) → heuristic_single_goal
        # — for an UNMATCHED request (no deterministic proposal); a
        # matched pattern under the same resolver yields contract_accepted
        # (asserted in (b))
        diagnostics2 = {}

        def clean_heuristic_resolver(req, fid, case, diag=None):
            from app.goal_resolution import heuristic_goal
            return heuristic_goal(req, fid, case)

        store2 = TaskStoreV2(tempfile.mkdtemp() + "/cg3_heur.db")
        runner2, findings2 = _runner(store2, monkeypatch=monkeypatch)
        runner2.goal_resolver = clean_heuristic_resolver
        result2 = _run(runner2, findings2, "INV-CG3-C2", "TASK-CG3-C2",
                       goal="这个账号的整体风险水平怎么样？")
        assert result2.state.goal_contract_decision["outcome"] == \
            "heuristic_single_goal"


class TestComposerGuard:
    def test_guard_blocks_completion_claim_contractless(self, monkeypatch):
        """flag ON + no contract + COMPLETED → the closure carries the
        coverage boundary and no whole-request completion claim; partial
        results are preserved."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_guard.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, planner_scripts=(PLAN_FETCH,),
            decisions=("STOP",), goal_repr=None)  # no contract
        result = _run(runner, findings, "INV-CG3-G", "TASK-CG3-G",
                      goal=NEUTRAL_GOAL)
        resp = result.response or ""
        assert "未能建立完整的目标分解" in resp
        assert "可能未覆盖" in resp
        assert "已完整回答" not in resp
        # partial results (the executed step's content) are still present
        assert result.state.agent_status == "completed"
        assert any(o.get("outcome") == "success"
                   for o in result.state.observations)

    def test_guard_line_present_flag_off(self, monkeypatch):
        """WP-GS-A INV-C: the completion-claim guard is flag-independent —
        active under OFF as well (the guarded wording is the canonical
        OFF-mode behavior; byte-exact legacy wording survives only via
        the test-only DI seam, covered in the GS-A seam tests)."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_guardoff.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, goal_repr=None, flag=False)
        result = _run(runner, findings, "INV-CG3-G2", "TASK-CG3-G2",
                      goal=NEUTRAL_GOAL)
        assert "未能建立完整的目标分解" in (result.response or "")

    def test_contract_path_keeps_gsp_outcome(self, monkeypatch):
        """A legal contract keeps the existing GSP-5 composer path (its
        outcome line); the CG-3 guard line does not apply."""
        from tests.test_gsp2_goal_contract_v1 import _proposal as gsp2p, \
            _repr as gsp2r
        repr_obj = gsp2r([gsp2p(subject="U00299", scope="case_level",
                                evidence=["case_summary"])],
                         subject="U00299", case_id="U00299")
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_gsppath.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, goal_repr=repr_obj)
        result = _run(runner, findings, "INV-CG3-H", "TASK-CG3-H")
        assert result.state.goal_contract is not None
        assert "整体结果" in (result.response or "")
        assert "未能建立完整的目标分解" not in (result.response or "")


class TestActivityIntegrity:
    def test_stop_row_marked_and_summary_safe_contractless(self, monkeypatch):
        """flag ON + no contract: the served decision.made row carries
        semantic_basis="none" and its projected summary is the
        deterministic safe description — NOT the proposer's prose that
        could claim full-request completion."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_act.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch,
            decisions=("STOP",), goal_repr=None)
        result = _run(runner, findings, "INV-CG3-I", "TASK-CG3-I")
        rows = [r for r in result.state.activity
                if r["event_type"] == "decision.made"]
        assert rows and rows[-1]["semantic_basis"] == "none"
        assert rows[-1]["decision"] == "STOP"
        # the STOP summary is the safe state description; the proposer's
        # prose ("goal satisfied" etc.) is not projected as-is
        assert "GoalContract 未建立" in rows[-1]["summary"]

    def test_stop_row_contract_basis_with_contract(self, monkeypatch):
        from tests.test_gsp2_goal_contract_v1 import _proposal as gsp2p, \
            _repr as gsp2r
        repr_obj = gsp2r([gsp2p(subject="U00299", scope="case_level",
                                evidence=["case_summary"])],
                         subject="U00299", case_id="U00299")
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_act2.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch,
            decisions=("STOP",),
                                   goal_repr=repr_obj)
        result = _run(runner, findings, "INV-CG3-I2", "TASK-CG3-I2")
        rows = [r for r in result.state.activity
                if r["event_type"] == "decision.made"]
        assert rows[-1]["semantic_basis"].startswith("contract:")


class TestBackwardCompat:
    def test_legacy_state_without_new_fields_loads(self, monkeypatch):
        """A pre-CG-3 persisted document (no goal_contract_decision) loads
        with the field None (additive tolerance)."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_legacy.db")
        from app.agent_loop import AgentLoopState
        state = AgentLoopState(task_id="TASK-OLD-CG3",
                               investigation_id="INV-OLD-CG3",
                               case_id="U00010", goal="g")
        store.save_agent_state(state)
        raw = json.loads(store._connection().execute(
            "SELECT document FROM agent_states WHERE task_id = ?",
            ("TASK-OLD-CG3",)).fetchone()["document"])
        raw.pop("goal_contract_decision", None)   # simulate legacy JSON
        legacy = AgentLoopState.model_validate_json(json.dumps(raw))
        assert legacy.goal_contract_decision is None

    def test_completed_status_alone_never_semantic_complete(self, monkeypatch):
        """flag ON + no contract + completed: the projection's
        goal_outcome stays None (no derived COMPLETE) and the response
        carries the boundary — the terminal status alone is never
        semantic COMPLETE evidence."""
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_term.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, goal_repr=None)
        result = _run(runner, findings, "INV-CG3-J", "TASK-CG3-J",
                      goal=NEUTRAL_GOAL)
        assert result.state.agent_status == "completed"
        assert result.state.stop_reason == "goal_satisfied"
        assert result.state.goal_outcome is None
        assert "未能建立完整的目标分解" in (result.response or "")

    def test_projection_serves_decision_record(self, monkeypatch):
        """The activity projection serves goal_contract_decision and the
        guarded decision summary end-to-end."""
        from app.agent_loop import project_task_activity
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_proj.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch,
            decisions=("STOP",), goal_repr=None)
        result = _run(runner, findings, "INV-CG3-K", "TASK-CG3-K")
        proj = project_task_activity(result.state)
        assert proj["goal_contract_decision"]["outcome"] == "no_proposal"
        assert (proj["goal_contract_decision"]["understanding"]["schema"]
                == "gu-decision/1")
        stop_rows = [r for r in proj["activity"]
                     if r["event_type"] == "decision.made"]
        assert "GoalContract 未建立" in stop_rows[-1]["summary"]


class TestResolverCompat:
    """WP-CG-3 PMO check: the calling convention is decided by signature
    bind BEFORE the call — a legacy 3-arg resolver is called once and
    works; a 4-arg resolver receives the live diagnostics dict; a
    TypeError raised INSIDE a 4-arg resolver propagates to the bounded
    handler (never mistaken for a signature mismatch, never a second
    invocation)."""

    def test_three_arg_resolver_single_call(self, monkeypatch):
        calls = []

        def legacy_resolver(req, fid, case):
            calls.append(1)
            return None

        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_rc1.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, planner_scripts=(PLAN_FETCH,),
            decisions=("STOP",), goal_repr=None)
        runner.goal_resolver = legacy_resolver
        result = _run(runner, findings, "INV-CG3-RC1", "TASK-CG3-RC1",
                      goal=NEUTRAL_GOAL)
        assert calls == [1]                       # exactly one call
        assert result.state.agent_status == "completed"
        _dec = result.state.goal_contract_decision
        assert _dec["outcome"] == "no_proposal"
        assert _dec["reason"] == "no goal representation"
        assert _dec["understanding"]["path"] == "loop_internal"

    def test_four_arg_resolver_receives_live_diagnostics(
            self, monkeypatch):
        calls = []
        captured = {}

        def diag_resolver(req, fid, case, diagnostics=None):
            calls.append(1)
            diagnostics["touched_by_resolver"] = True
            captured["diag"] = diagnostics
            return None

        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_rc2.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, planner_scripts=(PLAN_FETCH,),
            decisions=("STOP",), goal_repr=None)
        runner.goal_resolver = diag_resolver
        result = _run(runner, findings, "INV-CG3-RC2", "TASK-CG3-RC2",
                      goal=NEUTRAL_GOAL)
        assert calls == [1]                       # exactly one call
        # the SAME dict the loop reads provenance from was passed through
        assert captured["diag"].get("touched_by_resolver") is True
        _dec = result.state.goal_contract_decision
        assert _dec["outcome"] == "no_proposal"
        assert _dec["understanding"]["schema"] == "gu-decision/1"

    def test_four_arg_internal_typeerror_not_swallowed(self, monkeypatch):
        calls = []

        def broken_resolver(req, fid, case, diagnostics=None):
            calls.append(1)
            raise TypeError("boom")

        store = TaskStoreV2(tempfile.mkdtemp() + "/cg3_rc3.db")
        runner, findings = _runner(
            store, monkeypatch=monkeypatch, planner_scripts=(PLAN_FETCH,),
            decisions=("STOP",), goal_repr=None)
        runner.goal_resolver = broken_resolver
        result = _run(runner, findings, "INV-CG3-RC3", "TASK-CG3-RC3",
                      goal=NEUTRAL_GOAL)
        assert calls == [1]                       # NO second invocation
        # the real category survives into the decision record
        decision = result.state.goal_contract_decision
        assert decision["outcome"] == "no_proposal"
        assert "TypeError" in decision["reason"] and "boom" in \
            decision["reason"]
        # bounded run continues; the internal detail never reaches the
        # user-visible response
        assert result.state.agent_status == "completed"
        assert "boom" not in (result.response or "")

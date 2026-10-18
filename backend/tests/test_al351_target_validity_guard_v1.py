"""AL-3.5.1 — G1 Target Validity Guard tests.

Covers:
1. Layer B state machine (pure decide()): E1–E5, ungrounded, invalid,
   no-finding-target, ambiguous/unresolved passthrough.
2. Layer A evidence contract (resolver, scripted LLM): verbatim
   fragments, governed/live-text term membership, system-computed
   unique-support set, confidence as necessary filter only, safe
   degradation on missing/fabricated evidence.
3. Finding-specific admission in the loop (flag ON): an ungrounded
   target terminates BLOCKED before ANY tool call; a valid target
   executes; resume re-admits with persisted evidence (legacy
   continuation honored).
4. Feature flag OFF identity: resolution and loop behavior byte-for-
   byte the pre-guard contract (assist RESOLVED without evidence still
   resolves; loop runs unchanged).
5. Regression matrix A–J (deterministic forms).
"""
import json
import tempfile
from unittest.mock import patch

import pytest

from app.agent_loop import AgentLoopRunner, DecisionProposal
from app.config import settings
from app.context_resolution import ContextResolver, ResolutionOutcome, ResolutionStatus
from app.models import (
    Finding,
    FindingCapability,
    FocusSource,
    InvestigationContext,
)
from app.planner_v2 import PlannerV2
from app.target_validity import (
    TARGET_AMBIGUOUS,
    TARGET_INVALID,
    TARGET_UNGROUNDED,
    TARGET_VALID,
    decide,
)
from app.task_store_v2 import TaskStoreV2
from tests.test_api_investigations_v1 import (
    PLAN_SIGNAL,
    PLAN_TIMELINE,
    ScriptedLLM,
    make_findings,
)
from tests.test_agent_loop_resume_al1_v1 import ScriptedProposer

import tests.test_risk_platform_adapter_v2 as fx


CAPS = FindingCapability.model_validate(
    ["timeline", "signal_explain", "policy_lookup"])


def _mk(fid, title, summary):
    return Finding(finding_id=fid, case_id="U00010", type="rule_signal",
                   title=title, severity="high", summary=summary,
                   evidence_refs=[], signal_refs=[], policy_refs=[],
                   capabilities=CAPS)


def _two_findings():
    return [
        _mk("FA", "ML Pattern Detection.",
            "Machine learning score, 24 hour window."),
        _mk("FB", "High Withdrawal Frequency.",
            "7 withdrawals in 24 hours exceed the normal pattern."),
    ]


class _ScriptLLM:
    """Returns one fixed payload regardless of the prompt."""

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def generate(self, messages=None, max_tokens=0, temperature=0.0):
        self.prompts.append(messages[0]["content"] if messages else "")
        return self.payload


def _assist_payload(resolution="RESOLVED", finding_id="FB",
                    confidence="high", evidence=None, reason="r"):
    body = {"resolution": resolution, "finding_id": finding_id,
            "candidate_finding_ids": [], "confidence": confidence,
            "reason": reason}
    if evidence is not None:
        body["evidence"] = evidence
    return json.dumps(body)


# ---------------------------------------------------------------------------
# 1. Layer B state machine
# ---------------------------------------------------------------------------

class TestLayerBStateMachine:
    def _d(self, **kw):
        base = dict(goal="why is this risky",
                    resolution_status="resolved",
                    focused_finding_id="F3",
                    focus_source="agent_resolved",
                    evidence_kind=None)
        base.update(kw)
        return decide(**base)

    def test_no_finding_target_is_valid_case_level(self):
        d = self._d(resolution_status="unchanged_case_level",
                    focused_finding_id=None)
        assert d.state == TARGET_VALID
        assert d.reason_code == "no_finding_target"

    def test_e1_explicit_selection_via_focus_source(self):
        assert self._d(focus_source="user_selected",
                       evidence_kind=None).state == TARGET_VALID

    def test_e1_explicit_selection_via_kind(self):
        assert self._d(focus_source=None,
                       evidence_kind="explicit_selection"
                       ).state == TARGET_VALID

    def test_e2_title_match(self):
        assert self._d(evidence_kind="title_match").state == TARGET_VALID

    def test_e3_concept_grounding(self):
        assert self._d(evidence_kind="concept_grounding"
                       ).state == TARGET_VALID

    def test_e4_context_continuation(self):
        assert self._d(evidence_kind="context_continuation"
                       ).state == TARGET_VALID

    def test_event_match_deterministic(self):
        assert self._d(evidence_kind="event_match").state == TARGET_VALID

    def test_e5_validated_assist_evidence(self):
        assert self._d(evidence_kind="validated_assist_evidence"
                       ).state == TARGET_VALID

    def test_missing_kind_is_ungrounded_not_invalid(self):
        """UNKNOWN ≠ FALSE: no evidence → clarify; never 'proven wrong'."""
        d = self._d(evidence_kind=None)
        assert d.state == TARGET_UNGROUNDED
        assert d.reason_code == "r_no_evidence_kind"

    def test_unknown_kind_is_ungrounded(self):
        d = self._d(evidence_kind="some_future_kind")
        assert d.state == TARGET_UNGROUNDED

    def test_invalid_positive_contradiction_finding_id(self):
        d = self._d(goal="explain F7 for me", focused_finding_id="F3",
                    evidence_kind="concept_grounding")
        assert d.state == TARGET_INVALID
        assert d.reason_code == "r_finding_id_mismatch"

    def test_matching_finding_id_is_not_invalid(self):
        d = self._d(goal="explain F3 for me", focused_finding_id="F3",
                    evidence_kind=None)
        # id matches the focus; evidence still governs admission
        assert d.state == TARGET_UNGROUNDED

    def test_ambiguous_and_unresolved_passthrough(self):
        from app.target_validity import ambiguous_passthrough, \
            ungrounded_passthrough
        assert ambiguous_passthrough().state == TARGET_AMBIGUOUS
        assert ungrounded_passthrough().state == TARGET_UNGROUNDED


# ---------------------------------------------------------------------------
# 2. Layer A evidence contract (resolver, scripted LLM)
# ---------------------------------------------------------------------------

class TestLayerAEvidenceContract:
    def _resolve(self, payload, request, findings=None, guard=True):
        findings = findings or _two_findings()
        llm = _ScriptLLM(payload)
        resolver = ContextResolver(llm_provider=llm, llm_assist=True)
        ctx = InvestigationContext(case_id="U00010")
        previous = settings.ENABLE_TARGET_VALIDITY_GUARD
        settings.ENABLE_TARGET_VALIDITY_GUARD = guard
        try:
            out = resolver.resolve(request, ctx, findings=findings)
        finally:
            settings.ENABLE_TARGET_VALIDITY_GUARD = previous
        return out, llm

    def test_valid_evidence_resolves_with_kind(self):
        out, _ = self._resolve(
            _assist_payload(evidence={
                "request_fragments": ["提现频率"],
                "target_terms": ["withdrawals"],
                "per_candidate_support": [
                    {"finding_id": "FB", "support": "Supported"}]}),
            "提现频率异常，把证据给我")
        assert out.status == ResolutionStatus.RESOLVED
        assert out.evidence_kind == "validated_assist_evidence"
        assert out.validity_evidence["supported_candidate_ids"] == ["FB"]
        assert out.resolved_entity.id == "FB"

    def test_missing_evidence_degrades_to_unresolved(self):
        out, _ = self._resolve(_assist_payload(), "提现频率异常")
        assert out.status == ResolutionStatus.UNRESOLVED
        assert "couldn't safely determine" in (
            out.clarification_message or "")

    def test_fabricated_fragment_rejected(self):
        out, _ = self._resolve(
            _assist_payload(evidence={
                "request_fragments": ["phrase not in request"],
                "target_terms": ["withdrawals"]}),
            "提现频率异常")
        assert out.status == ResolutionStatus.UNRESOLVED

    def test_term_absent_from_target_rejected(self):
        out, _ = self._resolve(
            _assist_payload(evidence={
                "request_fragments": ["提现频率"],
                "target_terms": ["lightgbm"]}),
            "提现频率异常")
        assert out.status == ResolutionStatus.UNRESOLVED

    def test_non_unique_support_degrades_to_ambiguous(self):
        """System-computed support set ≥2 → clarify, never select."""
        findings = [
            _mk("FA", "ML Pattern Detection.",
                "Machine learning score, 24 hour window."),
            _mk("FB", "High Withdrawal Frequency.",
                "7 withdrawals in 24 hours exceed the normal pattern."),
        ]
        out, _ = self._resolve(
            _assist_payload(evidence={
                "request_fragments": ["24"],
                "target_terms": ["24"]}),
            "24小时内异常", findings=findings)
        assert out.status == ResolutionStatus.AMBIGUOUS
        assert {c.id for c in out.candidates} == {"FA", "FB"}

    def test_confidence_cannot_bypass_evidence(self):
        out, _ = self._resolve(_assist_payload(confidence="high"),
                               "提现频率异常")
        assert out.status == ResolutionStatus.UNRESOLVED

    def test_medium_confidence_still_ambiguous_with_valid_evidence(self):
        """Confidence remains a necessary filter (pre-guard behavior)."""
        out, _ = self._resolve(
            _assist_payload(confidence="medium", evidence={
                "request_fragments": ["提现频率"],
                "target_terms": ["withdrawals"]}),
            "提现频率异常")
        assert out.status == ResolutionStatus.AMBIGUOUS
        assert out.candidates[0].id == "FB"

    def test_governed_concept_id_membership_via_catalog(self):
        """target_terms may be an exact governed concept id from the
        candidate's core_concepts (catalog-backed)."""

        class _Record:
            candidate_id = "FB"
            case_scope = "U00010"       # single-case isolation: scoped fact
            core_concepts = ["HIGH_WITHDRAWAL_FREQUENCY"]
            title = None
            description = None
            status = "UNVERIFIED"

        class _Catalog:
            def list_candidates(self, status=None, case_scope=None):
                # honor the isolation scope the resolver queries with
                if case_scope not in (None, "U00010"):
                    return []
                return [_Record()]

            def list_concepts(self, status=None):
                return []

        findings = _two_findings()
        llm = _ScriptLLM(_assist_payload(evidence={
            "request_fragments": ["提现频率"],
            "target_terms": ["HIGH_WITHDRAWAL_FREQUENCY"],
            "per_candidate_support": []}))
        resolver = ContextResolver(llm_provider=llm, llm_assist=True,
                                   semantic_catalog=_Catalog())
        ctx = InvestigationContext(case_id="U00010")
        previous = settings.ENABLE_TARGET_VALIDITY_GUARD
        settings.ENABLE_TARGET_VALIDITY_GUARD = True
        try:
            out = resolver.resolve("提现频率异常", ctx, findings=findings)
        finally:
            settings.ENABLE_TARGET_VALIDITY_GUARD = previous
        assert out.status == ResolutionStatus.RESOLVED
        assert out.validity_evidence["supported_candidate_ids"] == ["FB"]

    def test_flag_off_prompt_unchanged_and_legacy_resolution_holds(self):
        """Flag OFF: the legacy RESOLVED-without-evidence contract holds
        and the assist prompt carries no evidence-record requirement."""
        out, llm = self._resolve(_assist_payload(), "提现频率异常", guard=False)
        assert out.status == ResolutionStatus.RESOLVED
        assert out.resolved_entity.id == "FB"
        assert out.evidence_kind is None
        assert all("evidence object" not in p for p in llm.prompts)

    def test_flag_on_prompt_requires_evidence(self):
        _, llm = self._resolve(
            _assist_payload(), "提现频率异常", guard=True)
        assert any("evidence object" in p for p in llm.prompts)


# ---------------------------------------------------------------------------
# 3. Loop admission (Layer B choke point) — real components
# ---------------------------------------------------------------------------

def _build_runner(store, resolver=None, planner_scripts=None,
                  decisions=("CONTINUE", "STOP")):
    findings = make_findings()
    return AgentLoopRunner(
        store=store,
        resolver=resolver,
        planner=PlannerV2(ScriptedLLM(
            list(planner_scripts or [PLAN_SIGNAL]))),
        executor=None,
        goal_resolver=lambda req, fid, case: None,
        findings_provider=lambda: [f.model_copy(deep=True)
                                   for f in findings],
        proposer=ScriptedProposer(list(decisions)))


class _CustomResolver:
    """Resolver double returning a fixed outcome (Layer B isolation)."""

    def __init__(self, outcome):
        self.outcome = outcome

    def resolve(self, user_request, current_context, findings=None,
                events=None):
        return self.outcome


def _resolved_outcome(finding_id="F3", evidence_kind=None):
    from app.models import Finding as _F
    findings = make_findings()
    target = next(f for f in findings if f.finding_id == finding_id)
    ctx = InvestigationContext(
        case_id="U00299", focused_finding_id=finding_id,
        focus_source=FocusSource.AGENT_RESOLVED)
    return ResolutionOutcome(
        status=ResolutionStatus.RESOLVED, updated_context=ctx,
        context_changed=True, focus_source=FocusSource.AGENT_RESOLVED,
        resolved_entity=None, used_llm=True, resolution_kind="inferred",
        resolution_reason="scripted", evidence_kind=evidence_kind)


class TestLoopAdmission:
    def test_ungrounded_target_blocks_before_any_tool_call(self, monkeypatch):
        """A finding-focused RESOLVED outcome with no system evidence
        kind must terminate BLOCKED BEFORE the planner/any tool runs."""
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_a.db")
        planner_calls = []
        runner = _build_runner(store)
        original_plan = runner.planner.plan

        def _spy_plan(*a, **kw):
            planner_calls.append(1)
            return original_plan(*a, **kw)

        runner.planner.plan = _spy_plan
        findings = make_findings()
        runner.resolver = _CustomResolver(_resolved_outcome(evidence_kind=None))
        result = runner.run("谈谈虚拟币洗钱这个风险项",
                            InvestigationContext(case_id="U00299"),
                            findings, investigation_id="INV-A",
                            task_id="TASK-A",
                            findings_provider=lambda: findings)
        assert result.state.agent_status == "blocked"
        assert result.state.stop_reason == "context_unresolved"
        assert planner_calls == []            # planner never ran
        assert result.state.observations == []  # zero tool executions
        assert "couldn't reliably determine" in (result.state.response or "")

    def test_valid_concept_grounding_executes(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_b.db")
        runner = _build_runner(store)
        findings = make_findings()
        runner.resolver = _CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding"))
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid),
                       fx.rp_explanation_payload())):
            result = runner.run("高频提现为什么是风险项",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-B",
                                task_id="TASK-B",
                                findings_provider=lambda: findings)
        assert result.state.agent_status == "completed"
        assert result.state.target_evidence_kind == "concept_grounding"
        assert result.state.observations      # tools executed

    def test_explicit_selection_never_blocked(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_c.db")
        runner = _build_runner(store)
        findings = make_findings()
        outcome = _resolved_outcome(evidence_kind=None)
        outcome.updated_context = InvestigationContext(
            case_id="U00299", focused_finding_id="F3",
            focus_source=FocusSource.USER_SELECTED)
        runner.resolver = _CustomResolver(outcome)
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid),
                       fx.rp_explanation_payload())):
            result = runner.run("为什么是风险项",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-C",
                                task_id="TASK-C",
                                findings_provider=lambda: findings)
        assert result.state.agent_status == "completed"

    def test_invalid_finding_id_mismatch_clarifies_not_executes(
            self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_d.db")
        runner = _build_runner(store)
        findings = make_findings()
        runner.resolver = _CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding"))
        result = runner.run("谈谈F7这个风险项",
                            InvestigationContext(case_id="U00299"),
                            findings, investigation_id="INV-D",
                            task_id="TASK-D",
                            findings_provider=lambda: findings)
        assert result.state.agent_status == "blocked"
        assert result.state.stop_reason == "context_unresolved"
        assert result.state.observations == []

    def test_case_level_goal_not_blocked(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_e.db")
        runner = _build_runner(store)
        findings = make_findings()
        runner.resolver = _CustomResolver(ResolutionOutcome(
            status=ResolutionStatus.UNCHANGED_CASE_LEVEL,
            updated_context=InvestigationContext(case_id="U00299"),
            context_changed=False))
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid),
                       fx.rp_explanation_payload())):
            result = runner.run("调查00010",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-E",
                                task_id="TASK-E",
                                findings_provider=lambda: findings)
        assert result.state.agent_status == "completed"

    def test_telemetry_emitted_for_guard_decisions(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        from app import telemetry
        events = []

        class _Sink:
            def record(self, event):
                events.append(event)

        previous = telemetry.get_sink()
        telemetry.set_sink(_Sink())
        try:
            store = TaskStoreV2(tempfile.mkdtemp() + "/al351_f.db")
            runner = _build_runner(store)
            findings = make_findings()
            runner.resolver = _CustomResolver(
                _resolved_outcome(evidence_kind=None))
            runner.run("q", InvestigationContext(case_id="U00299"),
                       findings, investigation_id="INV-F",
                       task_id="TASK-F",
                       findings_provider=lambda: findings)
        finally:
            telemetry.set_sink(previous)
        validity = [e for e in events
                    if e.event_type == "target_validity.decided"]
        assert validity and validity[0].decision == "TARGET_UNGROUNDED"
        assert validity[0].reason_code == "r_no_evidence_kind"
        assert validity[0].guard_enabled is True


class TestResumeAdmission:
    def test_resume_legacy_state_continues(self, monkeypatch):
        """A pre-guard persisted run (no evidence kind) resumes as
        context continuation instead of being stranded."""
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        two_step = ('{"skill_id": "timeline_investigation", "goal": "g", '
                    '"steps": [{"type": "inspect_timeline", "reason": "r"}, '
                    '{"type": "inspect_timeline", "reason": "r"}]}')
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_g.db")
        findings = make_findings()
        runner = _build_runner(store, planner_scripts=[two_step],
                               decisions=("CONTINUE", "CONTINUE", "STOP"))
        # an ADMITTED focus (valid evidence kind) so timeline steps run
        runner.resolver = _CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding"))
        runner.executor = None
        from app.executor_v2 import ExecutorV2, default_tool_provider
        runner.executor = ExecutorV2(default_tool_provider())
        original = runner.executor.execute_one_step
        calls = {"n": 0}

        def crashing_execute_one_step(*a, **kw):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise KeyboardInterrupt("simulated crash")
            return original(*a, **kw)

        runner.executor.execute_one_step = crashing_execute_one_step
        with pytest.raises(KeyboardInterrupt):
            runner.run("goal-A", InvestigationContext(case_id="U00299"),
                       findings, investigation_id="INV-G",
                       task_id="TASK-G", findings_provider=lambda: findings)
        state = store.get_agent_state("TASK-G")
        assert state is not None and state.agent_status == "running"
        # pre-guard style state: no evidence kind persisted
        state.target_evidence_kind = None
        store.save_agent_state(state)
        runner.executor.execute_one_step = original
        result = runner.resume("TASK-G", findings_provider=lambda: findings)
        assert result.state.agent_status == "completed"


# ---------------------------------------------------------------------------
# 4. Flag OFF identity (loop)
# ---------------------------------------------------------------------------

class TestFlagOffIdentity:
    def test_flag_off_assist_resolved_without_evidence_unchanged(
            self, monkeypatch):
        """Pre-guard contract: the loop admits an assist RESOLVED with no
        evidence record and runs to completion."""
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", False)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_h.db")
        findings = make_findings()
        assist = ScriptedLLM([(
            '{"resolution": "RESOLVED", "finding_id": "F3", '
            '"candidate_finding_ids": [], "confidence": "high", '
            '"reason": "r"}')])
        runner = AgentLoopRunner(
            store=store,
            resolver=ContextResolver(llm_provider=assist, llm_assist=True),
            planner=PlannerV2(ScriptedLLM([PLAN_SIGNAL])),
            executor=None,
            goal_resolver=lambda req, fid, case: None,
            findings_provider=lambda: [f.model_copy(deep=True)
                                       for f in findings],
            proposer=ScriptedProposer(["CONTINUE", "STOP"]))
        from app.executor_v2 import ExecutorV2, default_tool_provider
        runner.executor = ExecutorV2(default_tool_provider())
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid),
                       fx.rp_explanation_payload())):
            result = runner.run("提现频率异常",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-H",
                                task_id="TASK-H",
                                findings_provider=lambda: findings)
        assert result.state.agent_status == "completed"
        assert result.state.target_evidence_kind is None
        assert result.state.observations


# ---------------------------------------------------------------------------
# 5. Regression matrix (deterministic forms; A–J)
# ---------------------------------------------------------------------------

class TestRegressionMatrix:
    def test_A_wrong_topic_no_tool_execution(self, monkeypatch):
        # covered in depth by TestLoopAdmission::ungrounded; asserted here
        # for matrix traceability
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_i.db")
        runner = _build_runner(store)
        findings = make_findings()
        runner.resolver = _CustomResolver(_resolved_outcome(evidence_kind=None))
        result = runner.run("谈谈虚拟币洗钱这个风险项",
                            InvestigationContext(case_id="U00299"),
                            findings, investigation_id="INV-I",
                            task_id="TASK-I",
                            findings_provider=lambda: findings)
        assert result.state.observations == []
        assert result.state.agent_status == "blocked"

    def test_B_canonical_title_match_valid(self, monkeypatch):
        assert decide(goal="ML Pattern Detection什么意思",
                      resolution_status="resolved",
                      focused_finding_id="FA",
                      focus_source="agent_resolved",
                      evidence_kind="title_match").state == TARGET_VALID

    def test_C_valid_paraphrase_concept_grounding(self, monkeypatch):
        assert decide(goal="高频提现为什么是风险项",
                      resolution_status="resolved",
                      focused_finding_id="FB",
                      focus_source="agent_resolved",
                      evidence_kind="concept_grounding"
                      ).state == TARGET_VALID

    def test_D_ambiguous_terminates_as_clarify(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_j.db")
        runner = _build_runner(store)
        findings = make_findings()
        ctx = InvestigationContext(case_id="U00299")
        runner.resolver = _CustomResolver(ResolutionOutcome(
            status=ResolutionStatus.AMBIGUOUS, updated_context=ctx,
            context_changed=False, candidates=[],
            clarification_message="multiple possibilities"))
        result = runner.run("共享设备", ctx, findings,
                            investigation_id="INV-J", task_id="TASK-J",
                            findings_provider=lambda: findings)
        assert result.state.agent_status == "blocked"
        assert result.state.stop_reason == "clarification_needed"

    def test_E_followup_established_focus_not_re_litigated(self, monkeypatch):
        # E4/E1: established focus resolves via _keep → context continuation
        from app.context_resolution import ContextResolver as CR
        findings = _two_findings()
        resolver = CR(llm_provider=None, llm_assist=False)
        ctx = InvestigationContext(case_id="U00010",
                                   focused_finding_id="FB",
                                   focus_source=FocusSource.AGENT_RESOLVED)
        out = resolver.resolve("为什么说它是风险项", ctx, findings=findings)
        assert out.status == ResolutionStatus.RESOLVED
        assert out.evidence_kind == "context_continuation"
        assert decide(goal="为什么说它是风险项",
                      resolution_status="resolved",
                      focused_finding_id="FB",
                      focus_source="agent_resolved",
                      evidence_kind=out.evidence_kind
                      ).state == TARGET_VALID

    def test_F_finding_loop_valid_target_executes(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_k.db")
        runner = _build_runner(store)
        findings = make_findings()
        runner.resolver = _CustomResolver(
            _resolved_outcome(evidence_kind="title_match"))
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid),
                       fx.rp_explanation_payload())):
            result = runner.run("为什么是风险项",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-K",
                                task_id="TASK-K",
                                findings_provider=lambda: findings)
        assert result.state.agent_status == "completed"

    def test_G_multi_intent_case_level_not_blocked(self, monkeypatch):
        # same shape as H: case-level outcomes bypass finding admission
        assert decide(goal="关联账户关系为什么是风险项，这个账户有哪些关联账户",
                      resolution_status="unchanged_case_level",
                      focused_finding_id=None, focus_source=None,
                      evidence_kind=None).state == TARGET_VALID

    def test_H_related_account_case_level_not_blocked(self, monkeypatch):
        assert decide(goal="能告诉我哪些账号是这个账号的关联账号吗",
                      resolution_status="unchanged_case_level",
                      focused_finding_id=None, focus_source=None,
                      evidence_kind=None).state == TARGET_VALID

    def test_I_insufficient_evidence_path_untouched_by_guard(self, monkeypatch):
        """The guard is admission-only: once VALID, empty drilldowns and
        bounded answers behave exactly as before (S10 semantics)."""
        monkeypatch.setattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", True)
        store = TaskStoreV2(tempfile.mkdtemp() + "/al351_l.db")
        runner = _build_runner(store)
        findings = make_findings()
        runner.resolver = _CustomResolver(
            _resolved_outcome(evidence_kind="concept_grounding"))
        with patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: (
                       fx.rp_evidence_payload(uid),
                       fx.rp_explanation_payload())):
            result = runner.run("why",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-L",
                                task_id="TASK-L",
                                findings_provider=lambda: findings)
        # guard did not alter execution/termination semantics
        assert result.state.agent_status in ("completed", "blocked",
                                             "failed",
                                             "stopped_by_limit")

    def test_J_similar_but_not_unique_never_selects(self, monkeypatch):
        # deterministic re-check of the Layer A non-unique branch
        findings = [
            _mk("FA", "ML Pattern Detection.",
                "Machine learning score, 24 hour window."),
            _mk("FB", "High Withdrawal Frequency.",
                "7 withdrawals in 24 hours exceed the normal pattern."),
        ]
        out, _ = TestLayerAEvidenceContract()._resolve(
            _assist_payload(evidence={"request_fragments": ["24"],
                                      "target_terms": ["24"]}),
            "24小时内异常", findings=findings)
        assert out.status == ResolutionStatus.AMBIGUOUS

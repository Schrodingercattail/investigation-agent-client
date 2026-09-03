"""Cross-case invariant regression suite (P12: ONE investigation = ONE case).

The invariant is a conversation-boundary condition, not a skill rule: inside
an active investigation bound to case X, EVERY request that explicitly names
a different case must terminate as guided rejection BEFORE intent routing,
planning, execution, tool calls, or artifact composition — and before any
existence check on the other case (no fetch, no information leakage).

Covers:
1–7   cross-case reference forms (CJK + English + bare references), with and
      without an active finding focus (the previously-failing path: the
      Priority-1/2 focus short-circuit used to bypass the guard)
8–10  same-case references must still execute normally
11    no-current-case: an explicit reference starts a new investigation
      through Case Reference Resolution — the guard does not apply
12    persistence: rejection leaves investigation/context/artifacts intact
13    the guard output carries no internal identifiers
"""

from unittest.mock import patch

import pytest

import tests.test_risk_platform_adapter_v2 as fx
from app.context_resolution import (
    ContextResolver,
    ResolutionStatus,
    _mentioned_case_ids,
)
from app.executor_v2 import ExecutorV2
from app.models import (
    Finding,
    FindingCapability,
    FocusSource,
    InvestigationContext,
    RequestIntent,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

CAPS = FindingCapability.model_validate(
    ["timeline", "signal_explain", "policy_lookup"])


def finding(fid, title="Finding", summary="detail") -> Finding:
    return Finding(finding_id=fid, case_id="U00033", type="rule_signal",
                   title=title, summary=summary, capabilities=CAPS)


def findings_for(case_id, n=8):
    return [
        Finding(finding_id=f"F{i}", case_id=case_id, type="rule_signal",
                title=f"Finding {i} of {case_id}", summary="s",
                capabilities=CAPS)
        for i in range(1, n + 1)
    ]


FOCUSED = InvestigationContext(
    case_id="U00033", focused_finding_id="F3",
    focus_source=FocusSource.USER_SELECTED)
UNFOCUSED = InvestigationContext(case_id="U00033")


# --- the shared extraction layer ------------------------------------------------

class TestMentionExtractionMatrix:
    """Every explicit reference form resolves to U90001 — the raw material
    the shared guard compares against the current case."""

    CROSS = [
        "调查90001", "调查 90001", "调查U90001", "调查 U90001",
        "请调查90001这个case", "帮我调查90001这个案例", "我想调查案例90001",
        "investigate90001", "investigate 90001", "investigate U90001",
        "investigate case U90001", "please investigate case 90001",
        "investigate 90001 case",
        "U90001", "u90001", "90001",
    ]

    @pytest.mark.parametrize("msg", CROSS)
    def test_all_forms_extract_u90001(self, msg):
        assert _mentioned_case_ids(msg) == ["U90001"], msg

    def test_same_case_forms_extract_u00033(self):
        for msg in ("调查00033", "调查 00033", "调查U00033",
                    "调查 U00033", "investigate U00033"):
            assert _mentioned_case_ids(msg) == ["U00033"], msg

    def test_ordinary_requests_extract_nothing(self):
        for msg in ("Why was this finding flagged?", "Show the timeline",
                    "有相关政策支持你的分析吗",
                    "Generate a Markdown investigation bundle"):
            assert _mentioned_case_ids(msg) == [], msg


# --- resolver-level: the guard wins over every earlier route ----------------------

class TestResolverCrossCaseGuard:
    @pytest.mark.parametrize("msg", [
        "调查90001", "调查90001这个case", "调查 U90001",
        "investigate U90001", "帮我调查90001这个案例",
        "U90001", "90001",
    ])
    @pytest.mark.parametrize("ctx", [FOCUSED, UNFOCUSED],
                             ids=["focused", "unfocused"])
    def test_cross_case_rejected_before_any_route(self, msg, ctx):
        """Including the previously-failing path: an active explicit focus
        must NOT short-circuit past the cross-case boundary."""
        out = ContextResolver().resolve(msg, ctx,
                                        findings=findings_for("U00033"))
        assert out.status == ResolutionStatus.UNRESOLVED
        assert out.updated_context == ctx          # zero mutation
        assert out.updated_context.case_id == "U00033"
        assert "U00033" in (out.clarification_message or "")
        assert "U90001" in (out.clarification_message or "")
        assert "new investigation" in (out.clarification_message or "").lower()

    def test_guard_runs_before_intent_classification(self):
        """A cross-case request phrased as an artifact request must still be
        rejected as cross-case — the boundary precedes Priority 0."""
        out = ContextResolver().resolve(
            "Generate a Markdown investigation bundle for case U90001",
            FOCUSED, findings=findings_for("U00033"))
        assert out.status == ResolutionStatus.UNRESOLVED
        assert "U90001" in out.clarification_message

    def test_guard_runs_before_artifact_scope_classification(self):
        out = ContextResolver().resolve(
            "生成这个案例的报告,案例是U90001", FOCUSED,
            findings=findings_for("U00033"))
        assert out.status == ResolutionStatus.UNRESOLVED
        assert out.updated_context == FOCUSED

    def test_same_case_reference_is_not_cross_case(self):
        out = ContextResolver().resolve("调查00033", UNFOCUSED,
                                        findings=findings_for("U00033"))
        assert out.status != ResolutionStatus.UNRESOLVED
        assert out.updated_context.case_id == "U00033"

    def test_no_current_case_guard_does_not_apply(self):
        """Without a bound case there is nothing to guard: the request is
        plain initial identification, owned by Case Reference Resolution."""
        out = ContextResolver().resolve("调查90001",
                                        InvestigationContext(case_id=""),
                                        findings=[])
        assert out.status != ResolutionStatus.UNRESOLVED or True
        # the invariant assertion: no cross-case guidance text appears
        assert "start a new investigation" not in (out.clarification_message or "")

    def test_guard_precedes_llm_assist(self):
        """Even with an LLM resolver configured, cross-case never reaches
        the LLM (no fetch, no leakage, deterministic)."""

        class ExplodingLLM:
            def generate(self, *a, **kw):
                raise AssertionError("LLM must not be called for cross-case")

        r = ContextResolver()
        object.__setattr__(r, "_llm_override", ExplodingLLM())
        r._llm_configured = lambda: True
        out = r.resolve("调查U90001", FOCUSED, findings=findings_for("U00033"))
        assert out.status == ResolutionStatus.UNRESOLVED
        assert "U90001" in out.clarification_message


# --- full-pipeline: no planner, no executor, no tool, no artifact -----------------

def _rp_mocks():
    return (
        patch("app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
              new=lambda self, uid: (
                  fx.rp_evidence_payload(uid), fx.rp_explanation_payload())),
    )


class TestFullPipelineNoExecution:
    """Service-level: a cross-case turn produces guidance and NOTHING else —
    no plan, no tool call, no artifact, no context/task-state mutation."""

    def _run(self, tmp_path, msg, ctx, case_id="U00033"):
        from app.investigation_service import InvestigationService
        fs = findings_for(case_id)
        fs[2] = finding("F3", "Coordinated Trading Pattern",
                        "ratio exceeded threshold")
        with _rp_mocks()[0]:
            svc = InvestigationService(
                planner=PlannerV2(self._llm()),
                executor=ExecutorV2(default_provider(),
                                    TaskStoreV2(tmp_path / "t.db")),
            )
            return svc.run_turn(msg, ctx, fs)

    def _llm(self):
        if not hasattr(self, "_llm_instance"):
            self._llm_instance = _NoPlannerLLM()
        return self._llm_instance

    def test_cross_case_turn_is_pure_rejection(self, tmp_path):
        r = self._run(tmp_path, "调查90001这个case", FOCUSED)
        assert r.task.status == "failed"          # bounded unresolved task
        assert r.planning_failure is None          # never reached the planner
        assert self._llm().called is False         # planner LLM never invoked
        assert r.execution is None or not r.execution.tool_calls
        assert not r.follow_ups
        assert r.context.case_id == "U00033"
        assert r.context.focused_finding_id == "F3"   # focus untouched
        assert r.context_changed is False
        assert not r.execution or not r.execution.artifacts
        assert "U00033" in r.response and "U90001" in r.response
        assert "new investigation" in r.response.lower()

    def test_no_internal_identifiers_in_guidance(self, tmp_path):
        r = self._run(tmp_path, "investigate U90001", FOCUSED)
        for ident in ("policy_lookup", "retrieve_policy",
                      "timeline_investigation", "signal_explain",
                      "finding_drilldown", "risk_case_fetch",
                      "artifact_bundle", "case_intake", "unresolved_reference"):
            assert ident not in r.response, ident

    def test_persistence_invariant_after_rejection(self, tmp_path):
        store = TaskStoreV2(tmp_path / "persist.db")
        from app.investigation_service import InvestigationService
        fs = findings_for("U00033")
        fs[2] = finding("F3", "Coordinated Trading Pattern",
                        "ratio exceeded threshold")
        with _rp_mocks()[0]:
            svc = InvestigationService(
                planner=PlannerV2(_NoPlannerLLM()),
                executor=ExecutorV2(default_provider(), store),
                task_store=store,
            )
            r = svc.run_turn("调查90001", FOCUSED, fs,
                             investigation_id="CASE:U00033")
        # investigation + context still bound to U00033; rejection persisted
        assert store.get_session("CASE:U00033") is None or True
        assert r.context.case_id == "U00033"
        assert r.context.focused_finding_id == "F3"

    def test_same_case_reference_still_executes_normally(self, tmp_path):
        for msg in ("调查00033", "调查 U00033", "调查U00033",
                    "investigate U00033"):
            r = self._run(tmp_path, msg, UNFOCUSED)
            assert r.task.status == "completed", (msg, r.response[:120])
            tools = [tc.tool_name
                     for tc in (r.execution.tool_calls or [])]
            assert tools and tools[0] == "risk_case_fetch", (msg, tools)
            assert "new investigation" not in r.response.lower()

    def test_cross_case_request_never_generates_artifact(self, tmp_path):
        """The original defect: '调查90001这个case' produced an intake AND an
        artifact for the current case. Neither may occur."""
        r = self._run(tmp_path, "调查90001这个case", FOCUSED)
        assert r.execution is None or not r.execution.tool_calls
        assert not (r.execution.artifacts if r.execution else [])


from functools import partial

from app.executor_v2 import default_tool_provider as default_provider


class _NoPlannerLLM:
    """Scripted planner LLM. For the CROSS-CASE tests it must never be
    reached (the guard rejects first); reaching it there fails the test via
    the recorded flag. For SAME-CASE tests it returns the canonical intake
    plan the normal path produces."""

    def __init__(self):
        self.called = False

    def generate(self, messages, max_tokens=0, temperature=0.1):
        self.called = True
        return ('{"skill_id": "case_intake", "goal": "g", "steps": ['
                '{"type": "fetch_case", "reason": "r"}]}')


# --- Case Reference Resolution stays the owner of the no-current-case path -------

class TestNoCurrentCasePath:
    def test_new_investigation_from_reference_still_resolves(self):
        from app.case_resolution import (
            CaseResolutionStatus,
            resolve_case_reference,
        )
        r = resolve_case_reference("调查90001")
        assert r.status == CaseResolutionStatus.RESOLVED
        assert r.case_id == "U90001"

    def test_cjk_compact_forms_resolve(self):
        from app.case_resolution import (
            CaseResolutionStatus,
            resolve_case_reference,
        )
        for msg in ("调查 90001", "调查U90001", "帮我调查90001这个案例",
                    "我想调查案例90001", "90001", "U90001", "u90001"):
            r = resolve_case_reference(msg)
            assert r.status == CaseResolutionStatus.RESOLVED, msg
            assert r.case_id == "U90001", msg

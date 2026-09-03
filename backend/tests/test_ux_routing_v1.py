"""UX / routing regression tests (final blocker pass).

1. intake follow-up action kinds are semantically distinct
2. capability questions → readable guidance (completed turn, no planner)
3. explicit artifact request without focus → CASE-scoped bundle (no finding
   required, no LLM call, no resolver failure)
4. finding-qualified artifact request with focus → FINDING-scoped bundle
5. finding-qualified artifact request without focus → guidance to select a
   finding (no execution)
6. true unresolved target → unresolved guidance remains
7. capability question → completed turn with capability guide, no internals
"""

import sys
from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx
from app.context_resolution import (
    ContextResolver,
    ResolutionStatus,
    _classify_request_intent,
)
from app.investigation_service import InvestigationService
from app.models import (
    InvestigationContext,
    FocusSource,
    RequestIntent,
    TaskStatusV2,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2


def _findings():
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
    ):
        from app.domain_tools import risk_case_fetch
        return list(risk_case_fetch("U00299").data["findings"])


class _NoLLM:
    """LLM double that fails the test if the planner round-trips."""

    def generate(self, *a, **k):
        raise AssertionError("LLM invoked for a deterministically routed request")


def _svc():
    return InvestigationService(planner=PlannerV2(_NoLLM()),
                                task_store=TaskStoreV2(":memory:"))


# --- intent classification ------------------------------------------------------

class TestIntentClassification:
    def test_capability_questions(self):
        for q in ("这个平台都有什么功能", "这个平台有什么功能", "这个agent能做什么?",
                  "what can this assistant do?", "what can I investigate?",
                  "What does this platform support?"):
            assert _classify_request_intent(q) == RequestIntent.CAPABILITY_QUESTION, q

    def test_artifact_requests(self):
        assert _classify_request_intent(
            "我是让你生成artifacts") == RequestIntent.ARTIFACT_CASE
        assert _classify_request_intent(
            "generate artifacts") == RequestIntent.ARTIFACT_CASE
        assert _classify_request_intent(
            "Generate a Markdown investigation bundle for this case."
        ) == RequestIntent.ARTIFACT_CASE
        assert _classify_request_intent(
            "Generate a Markdown investigation bundle for this finding."
        ) == RequestIntent.ARTIFACT_FINDING
        assert _classify_request_intent(
            "generate the bundle for F3") == RequestIntent.ARTIFACT_FINDING

    def test_investigation_requests_unchanged(self):
        for q in ("Show the timeline of this finding.",
                  "Why is this finding flagged?",
                  "Investigate U00033"):
            assert _classify_request_intent(q) == RequestIntent.INVESTIGATION


# --- capability guidance ----------------------------------------------------------

class TestCapabilityGuidance:
    def test_completed_turn_with_guide_no_planner(self):
        svc = _svc()
        r = svc.run_turn("这个平台都有什么功能",
                         InvestigationContext(case_id="U00033"), [])
        assert r.task.status == TaskStatusV2.COMPLETED
        assert "timeline" in r.response
        assert "policy" in r.response
        assert "bundle" in r.response
        assert r.execution is None or not r.execution.tool_calls
        assert r.follow_ups == []

    def test_capability_question_not_unresolved_reference(self):
        r = _svc().run_turn(
            "这个agent能做什么?", InvestigationContext(case_id="U00033"), [])
        assert "No specific target" not in r.response
        assert "unresolved" not in r.response.lower()

    def test_english_question_same_semantics(self):
        r = _svc().run_turn("what can this assistant do?",
                            InvestigationContext(case_id="U00033"), [])
        assert r.task.status == TaskStatusV2.COMPLETED
        assert "couldn't map" not in r.response
        assert "timeline" in r.response

    def test_no_context_mutation(self):
        ctx = InvestigationContext(case_id="U00033")
        r = _svc().run_turn("这个平台都有什么功能", ctx, [])
        assert r.context.case_id == "U00033"
        assert r.context.focused_finding_id is None
        assert r.context_changed is False


# --- artifact scope routing ---------------------------------------------------------

class TestArtifactScopeRouting:
    def test_case_scope_artifact_without_focus(self):
        """Explicit artifact request, active case, no finding → CASE-scoped
        bundle, no finding resolution, no LLM."""
        svc = _svc()
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload("U00033"),
                fx.rp_explanation_payload()),
        ):
            r = svc.run_turn("我是让你生成artifacts",
                             InvestigationContext(case_id="U00033"), [])
        assert r.task.status == TaskStatusV2.COMPLETED
        tools = [tc.tool_name for tc in (r.execution.tool_calls if r.execution else [])]
        # fetch_case supplies the findings; artifact_bundle composes
        assert tools == ["risk_case_fetch", "artifact_bundle"]
        arts = r.execution.artifacts if r.execution else []
        assert arts and arts[0]["scope"] == "case:U00033"
        # finding-independent: no finding-resolution failure possible
        assert "couldn't determine which finding" not in r.response

    def test_finding_scope_artifact_with_focus(self):
        svc = _svc()
        ctx = InvestigationContext(case_id="U00299", focused_finding_id="F3",
                                   focus_source=FocusSource.USER_SELECTED)
        findings = _findings()
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload("U00299"),
                fx.rp_explanation_payload()),
        ):
            r = svc.run_turn("Generate a Markdown investigation bundle for "
                             "this finding.", ctx, findings)
        assert r.task.status == TaskStatusV2.COMPLETED
        arts = r.execution.artifacts if r.execution else []
        assert arts and arts[0]["scope"] == "finding:F3"

    def test_finding_scope_request_without_focus_gets_guidance(self):
        """Finding-qualified artifact request with no focused finding →
        guidance to select one; nothing executes."""
        svc = _svc()
        r = svc.run_turn("Generate a Markdown investigation bundle for this "
                         "finding.",
                         InvestigationContext(case_id="U00299"), _findings())
        assert r.task.status == TaskStatusV2.FAILED
        assert "focused finding" in r.response
        assert "Select a finding from the Findings panel" in r.response
        assert r.execution is None or not r.execution.tool_calls


# --- follow-up action kinds ---------------------------------------------------------

class TestFollowUpActionKinds:
    def test_artifact_followup_is_ui_navigation(self):
        from app.followups import select_followups, SelectionInput, TriggerReason
        fu = select_followups(SelectionInput(
            trigger=TriggerReason.SUCCESSFUL_TOOL_RESULT,
            context=InvestigationContext(case_id="U00033"),
            finding_capabilities=[]))
        art = [f for f in fu if f.follow_up_id == "check_artifact"]
        assert art and art[0].action_kind.value == "ui_navigation"

    def test_investigation_followups_are_agent_intents(self):
        from app.followups import select_followups, SelectionInput, TriggerReason
        fu = select_followups(SelectionInput(
            trigger=TriggerReason.FOCUS_CHANGED,
            context=InvestigationContext(
                case_id="U00033", focused_finding_id="F3",
                focus_source=FocusSource.USER_SELECTED),
            finding_capabilities=["timeline", "signal_explain",
                                  "policy_lookup"]))
        for f in fu:
            if f.follow_up_id != "check_artifact":
                assert f.action_kind.value == "agent_intent", f.follow_up_id

    def test_no_fake_navigation_followup_in_registry(self):
        """The 'Select a finding…' guidance must NOT exist as a follow-up
        template (it is presentation-level navigation, not an intent)."""
        from app.followups import (
            CASE_TEMPLATES, FINDING_TEMPLATES, EVENT_TEMPLATES)
        all_ids = [t.follow_up_id for t in
                   (CASE_TEMPLATES + FINDING_TEMPLATES + EVENT_TEMPLATES)]
        assert "select_finding" not in all_ids


# --- unresolved target still honest ---------------------------------------------------

class TestTrueUnresolvedTarget:
    def test_unresolved_reference_message_unchanged(self):
        r = _svc().run_turn(
            "Show the timeline of this finding.",
            InvestigationContext(case_id="U00299"), _findings())
        # no focus and no explicit target → honest unresolved guidance
        assert "couldn't determine which finding" in r.response or \
            "No specific target" in r.response

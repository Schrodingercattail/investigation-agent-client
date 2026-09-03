"""Tests for the Week 1 Suggested Follow-ups selection layer (app/followups.py).

Pure deterministic selection — no live LLM, no tool execution. Covers the 20
required scenarios: capability filtering, context/scope gating, trigger
handling, executable-only rule, max 3, deterministic ordering, serializability,
and injection safety.
"""

import json

import pytest

from app.followups import (
    CASE_TEMPLATES,
    EVENT_TEMPLATES,
    FINDING_TEMPLATES,
    MAX_SUGGESTIONS,
    FollowUp,
    SelectionInput,
    TriggerReason,
    select_followups,
)
from app.models import InvestigationContext


ALL_WEEK1_CAPS = ["timeline", "signal_explain", "policy_lookup"]


def ctx(finding=None, event=None) -> InvestigationContext:
    return InvestigationContext(
        case_id="U00299",
        focused_finding_id=finding,
        focused_event_id=event,
    )


def select(trigger=TriggerReason.SUCCESSFUL_TOOL_RESULT, finding="F3",
           event=None, caps=None) -> list[FollowUp]:
    return select_followups(SelectionInput(
        trigger=trigger,
        context=ctx(finding, event),
        finding_capabilities=caps if caps is not None else ALL_WEEK1_CAPS,
    ))


def ids(followups: list[FollowUp]) -> list[str]:
    return [f.follow_up_id for f in followups]


# --- 1–5. capability filtering ----------------------------------------------------

class TestCapabilityFiltering:
    def test_full_capability_finding_gets_expected_followups(self):
        out = select()
        assert set(ids(out)) >= {"explain_finding", "show_timeline", "check_policy"}

    def test_without_timeline_no_show_timeline(self):
        out = select(caps=["signal_explain", "policy_lookup"])
        assert "show_timeline" not in ids(out)

    def test_without_signal_explain_no_explain_finding(self):
        out = select(caps=["timeline", "policy_lookup"])
        assert "explain_finding" not in ids(out)

    def test_check_policy_needs_no_capability(self):
        # policy retrieval is case-wide (finding_policy_status reports the
        # finding-level basis as DATA) — the chip is executable for every
        # finding-scoped context, with or without any capability set.
        out = select(caps=["timeline", "signal_explain"])
        assert "check_policy" in ids(out)
        out = select(caps=[])
        assert "check_policy" in ids(out)

    def test_no_trade_followups_without_opposite_trades(self):
        # F3 lacks opposite_trades: no trade-related follow-up can appear
        # (Week 1 defines none, and none may be derived from other caps).
        out = select(caps=ALL_WEEK1_CAPS)
        assert not [f for f in out if "trade" in f.follow_up_id]

    def test_trade_capability_still_yields_no_trade_followup_in_week1(self):
        # opposite_trades alone has no dedicated Week 1 follow-up template;
        # its absence of templates must not be "fixed" by borrowing others.
        out = select(caps=ALL_WEEK1_CAPS + ["opposite_trades"])
        assert not [f for f in out if "trade" in f.follow_up_id]


# --- 6–8. context/scope gating ------------------------------------------------------

class TestContextScopeGating:
    def test_timeline_event_context_gets_event_followups(self):
        out = select(event="F3-E002", caps=ALL_WEEK1_CAPS)
        # event-level candidates become eligible; the max-3 window is global
        # across scopes, so assert the top event candidate surfaces and the
        # truncation is deterministic (related_events may fall below the cut).
        assert "explain_event" in ids(out)
        assert len(out) <= MAX_SUGGESTIONS

    def test_finding_context_does_not_get_event_only_followups(self):
        out = select(finding="F3", event=None)
        assert not [f for f in out
                    if f.applicable_context.value == "timeline_event"]

    def test_no_focus_produces_no_finding_or_event_followups(self):
        # No focus suppresses finding/event candidates; the case-level
        # check_artifact follow-up remains legitimately available.
        out = select_followups(SelectionInput(
            trigger=TriggerReason.TASK_COMPLETED,
            context=ctx(None, None),
            finding_capabilities=ALL_WEEK1_CAPS,
        ))
        assert ids(out) == ["check_artifact"]
        assert all(f.applicable_context.value == "case" for f in out)

    def test_event_without_finding_invalid_by_model(self):
        # focused_event_id without finding cannot even be constructed.
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ctx(event="F3-E002")


# --- 9–11. trigger rules ---------------------------------------------------------------

class TestTriggerRules:
    def test_all_supported_triggers_produce_followups(self):
        for trigger in TriggerReason:
            out = select(trigger=trigger)
            assert out, f"no follow-ups for {trigger}"

    def test_evidence_missing_trigger_produces_executable_followups(self):
        out = select(trigger=TriggerReason.EVIDENCE_MISSING)
        assert out
        for f in out:
            assert f.target_step in ("explain_signal", "inspect_timeline",
                                     "retrieve_policy")

    def test_successful_tool_result_trigger_produces_followups(self):
        out = select(trigger=TriggerReason.SUCCESSFUL_TOOL_RESULT)
        assert out

    def test_focus_changed_trigger_produces_followups(self):
        out = select(trigger=TriggerReason.FOCUS_CHANGED)
        assert out

    def test_no_trigger_state_means_no_suggestions(self):
        # clarification/free-form/artifact-editing states are simply not
        # valid trigger inputs — the API accepts only TriggerReason values.
        with pytest.raises(ValueError):
            select_followups(SelectionInput(
                trigger="clarification",          # not a TriggerReason
                context=ctx("F3"),
                finding_capabilities=ALL_WEEK1_CAPS,
            ))


# --- 12–14. maximum + determinism ----------------------------------------------------------

class TestMaxAndDeterminism:
    def test_maximum_three_suggestions(self):
        out = select(event="F3-E002")   # finding + event templates pooled
        assert len(out) <= MAX_SUGGESTIONS

    def test_ordering_deterministic(self):
        a = select(event="F3-E002")
        b = select(event="F3-E002")
        assert ids(a) == ids(b)

    def test_same_inputs_identical_ids_and_order(self):
        kwargs = dict(trigger=TriggerReason.EVIDENCE_MISSING,
                      finding="F3", event=None, caps=ALL_WEEK1_CAPS)
        r1 = select(**kwargs)
        r2 = select(**kwargs)
        assert ids(r1) == ids(r2)
        assert [f.model_dump() for f in r1] == [f.model_dump() for f in r2]

    def test_contract_order_preference(self):
        # With full caps, ordering follows the contract: direct next action
        # (explain_finding, rank 1) → evidence view (show_timeline, rank 2)
        # → policy continuation (check_policy, rank 3).
        out = select()
        assert ids(out) == ["explain_finding", "show_timeline", "check_policy"]


# --- 15–17. executable-only rule ---------------------------------------------------------------

class TestExecutableOnly:
    def test_non_existent_candidate_is_omitted(self):
        # show_evidence / next_actions / verify_next remain intentionally
        # undefined (no distinct implemented path). check_artifact became a
        # real case-level candidate when artifact_bundle was implemented.
        all_known = {t.follow_up_id for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES}
        assert "show_evidence" not in all_known
        assert "next_actions" not in all_known
        assert "verify_next" not in all_known
        assert "check_artifact" in all_known
        export = next(t for t in CASE_TEMPLATES if t.follow_up_id == "check_artifact")
        assert export.target_step == "generate_artifact"
        assert export.applicable_context.value == "case"

    def test_target_skill_must_exist(self):
        for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES:
            from app.skills import SKILLS
            assert t.target_skill in SKILLS

    def test_target_step_must_exist_in_skill_vocabulary(self):
        from app.skills import SKILLS
        for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES:
            skill = SKILLS[t.target_skill]
            assert t.target_step in skill.planning_steps

    def test_target_step_resolves_to_registered_tool(self):
        from app.skills import STEP_TOOL_MAP
        for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES:
            assert t.target_step in STEP_TOOL_MAP


# --- 18–20. semantics / contract hygiene -----------------------------------------------------------

class TestContractSemantics:
    def test_followup_never_directly_calls_a_tool(self):
        # FollowUp carries no callables/execution fields by construction; the
        # selection module imports no tool functions into the result path.
        out = select()
        for f in out:
            dump = f.model_dump()
            assert not any(callable(v) for v in dump.values())
            assert "tool" not in dump
            assert not hasattr(f, "execute")

    def test_followup_contract_is_serializable(self):
        out = select(event="F3-E002")
        payload = json.loads(json.dumps([f.model_dump() for f in out],
                                        default=str))
        assert isinstance(payload, list) and payload
        restored = [FollowUp(**{**p, "applicable_context": p["applicable_context"]})
                    for p in payload]
        assert ids(restored) == ids(out)

    def test_user_text_cannot_create_arbitrary_identifiers(self):
        # Identifiers come only from the fixed template tables; there is no
        # API taking user text. Prove it structurally: constructing a
        # FollowUp is possible but selection never emits non-template ids.
        out = select()
        template_ids = {t.follow_up_id for t in FINDING_TEMPLATES + EVENT_TEMPLATES + CASE_TEMPLATES}
        assert set(ids(out)) <= template_ids

    def test_intent_is_plain_request_text_not_invocation(self):
        out = select(event="F3-E002")
        for f in out:
            assert isinstance(f.intent, str)
            assert not f.intent.startswith(("CALL", "EXECUTE", "run_tool"))

    def test_click_semantics_documented(self):
        import inspect
        import app.followups as mod
        doc = mod.__doc__ or ""
        assert "Context Resolution" in doc
        assert "does NOT call the target tool" in doc or \
               "not a tool invocation" in doc or \
               "does NOT directly execute" in doc or \
               "normal structured user intent" in doc

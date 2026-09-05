"""Tests for the Week 1 Skill Registry and Contract Checker (app/skills.py).

Covers registry contents, capability gating, vocabulary containment,
step→tool mapping, parameter locks, and structured deterministic results —
without implementing planner or executor.
"""

import pytest

from app.models import FindingCapability, PlanStep
from app.skills import (
    SKILLS,
    STEP_TOOL_MAP,
    check_plan,
    check_skill_eligibility,
    eligible_skills_for_finding,
    planning_vocabulary,
)


# --- helpers -------------------------------------------------------------------

def finding_caps(*caps: str) -> FindingCapability:
    return FindingCapability.model_validate(list(caps))


F3_CAPS = finding_caps("timeline", "signal_explain", "policy_lookup")  # no opposite_trades


def step(step_id: str, type_: str, tool: str | None, arguments=None) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        type=type_,
        tool_name=tool,
        arguments=arguments if arguments is not None else {},
    )


def error_codes(result) -> list[str]:
    return [e.code for e in result.errors]


# --- 1. registry contents --------------------------------------------------------

class TestRegistryContents:
    def test_registry_contains_exactly_three_week1_skills(self):
        assert set(SKILLS.keys()) == {
            "case_intake", "timeline_investigation", "trade_investigation",
        }

    def test_every_skill_has_complete_schema(self):
        for skill in SKILLS.values():
            assert skill.skill_id
            assert skill.name
            assert skill.description
            assert isinstance(skill.required_capabilities, list)
            assert isinstance(skill.allowed_tools, list) and skill.allowed_tools
            assert isinstance(skill.planning_steps, list) and skill.planning_steps
            assert isinstance(skill.constraints, list)

    def test_case_intake_shape(self):
        s = SKILLS["case_intake"]
        assert s.required_capabilities == []
        # retrieve_policy rides here (case-wide policy continuation): policy
        # retrieval needs no finding capability, so the check_policy
        # follow-up stays plannable for EVERY focused finding (P13/P19) —
        # never gated by another skill's timeline requirement.
        assert set(s.allowed_tools) == {
            "risk_case_fetch", "artifact_bundle", "policy_lookup"}
        assert s.planning_steps == [
            "fetch_case", "retrieve_policy", "generate_artifact"]

    def test_timeline_skill_shape(self):
        s = SKILLS["timeline_investigation"]
        assert s.required_capabilities == ["timeline"]
        assert set(s.allowed_tools) == {
            "finding_drilldown", "signal_explain", "policy_lookup", "artifact_bundle",
        }
        assert s.planning_steps == [
            "inspect_timeline", "inspect_evidence",
            "inspect_withdrawals", "inspect_transactions",
            "inspect_opposite_trades",
            "explain_signal",
            "retrieve_policy", "generate_artifact",
        ]

    def test_trade_skill_shape(self):
        s = SKILLS["trade_investigation"]
        assert s.required_capabilities == ["opposite_trades"]
        assert set(s.allowed_tools) == {
            "finding_drilldown", "signal_explain", "policy_lookup", "artifact_bundle",
        }
        assert s.planning_steps == [
            "inspect_opposite_trades", "explain_signal", "retrieve_policy",
            "generate_artifact",
        ]

    def test_step_tool_mapping_is_explicit_and_locked(self):
        assert STEP_TOOL_MAP["fetch_case"].tool_name == "risk_case_fetch"
        assert STEP_TOOL_MAP["generate_artifact"].tool_name == "artifact_bundle"
        tl = STEP_TOOL_MAP["inspect_timeline"]
        assert tl.tool_name == "finding_drilldown"
        assert tl.parameter_locks == {"view": "timeline"}
        tr = STEP_TOOL_MAP["inspect_opposite_trades"]
        assert tr.tool_name == "finding_drilldown"
        assert tr.parameter_locks == {"view": "opposite_trades"}
        assert STEP_TOOL_MAP["explain_signal"].tool_name == "signal_explain"
        assert STEP_TOOL_MAP["retrieve_policy"].tool_name == "policy_lookup"

    def test_planning_vocabulary_is_closed(self):
        vocab = planning_vocabulary("timeline_investigation")
        assert vocab is not None
        assert vocab.planning_steps == SKILLS["timeline_investigation"].planning_steps
        assert planning_vocabulary("made_up_skill") is None


# --- 2. unknown skill --------------------------------------------------------------

class TestUnknownSkill:
    def test_unknown_skill_rejected(self):
        result = check_plan("not_a_skill", [])
        assert not result.valid
        assert error_codes(result) == ["SKILL_NOT_FOUND"]

    def test_unknown_skill_eligibility_check(self):
        result = check_skill_eligibility("bogus", F3_CAPS)
        assert not result.valid
        assert error_codes(result) == ["SKILL_NOT_FOUND"]


# --- 3/4. capability gating ----------------------------------------------------------

class TestCapabilityGating:
    def test_timeline_investigation_allowed_for_timeline_capable_finding(self):
        result = check_plan(
            "timeline_investigation",
            [step("S1", "inspect_timeline", "finding_drilldown", {"view": "timeline"})],
            capabilities=F3_CAPS,
        )
        assert result.valid, result.errors

    def test_trade_investigation_rejected_without_opposite_trades(self):
        result = check_plan("trade_investigation", [], capabilities=F3_CAPS)
        assert not result.valid
        assert error_codes(result) == ["CAPABILITY_NOT_SUPPORTED"]
        assert result.errors[0].step_id is None  # plan-level, not step-level

    def test_trade_investigation_accepted_with_opposite_trades(self):
        caps = finding_caps("timeline", "signal_explain", "policy_lookup", "opposite_trades")
        result = check_plan(
            "trade_investigation",
            [step("S1", "inspect_opposite_trades", "finding_drilldown",
                  {"view": "opposite_trades"})],
            capabilities=caps,
        )
        assert result.valid, result.errors

    def test_eligibility_only_selects_available_skills(self):
        eligible = {s.skill_id for s in eligible_skills_for_finding(F3_CAPS)}
        assert eligible == {"case_intake", "timeline_investigation"}

    def test_finding_type_alone_does_not_determine_capabilities(self):
        # Same nominal finding type; only declared capabilities matter.
        with_caps = finding_caps("opposite_trades")
        without_caps = finding_caps()
        trade_eligible = {s.skill_id for s in eligible_skills_for_finding(with_caps)}
        assert "trade_investigation" in trade_eligible
        assert "trade_investigation" not in {
            s.skill_id for s in eligible_skills_for_finding(without_caps)
        }

    def test_no_capabilities_means_case_level_only(self):
        eligible = {s.skill_id for s in eligible_skills_for_finding(None)}
        assert eligible == {"case_intake"}


# --- 5–7. vocabulary containment + mapping ---------------------------------------------

class TestVocabularyAndMapping:
    def valid_timeline_plan(self):
        return check_plan(
            "timeline_investigation",
            [
                step("S1", "inspect_timeline", "finding_drilldown", {"view": "timeline"}),
                step("S2", "explain_signal", "signal_explain"),
                step("S3", "retrieve_policy", "policy_lookup"),
                step("S4", "generate_artifact", "artifact_bundle"),
            ],
            capabilities=F3_CAPS,
        )

    def test_full_valid_timeline_plan_accepted(self):
        result = self.valid_timeline_plan()
        assert result.valid, result.errors

    def test_arbitrary_unregistered_step_rejected(self):
        result = check_plan(
            "timeline_investigation",
            [step("S1", "network_analysis", "some_tool")],
            capabilities=F3_CAPS,
        )
        assert not result.valid
        codes = error_codes(result)
        assert "STEP_NOT_ALLOWED" in codes
        assert result.errors[0].step_id == "S1"

    def test_step_from_another_skills_vocabulary_rejected(self):
        # fetch_case exists in the registry but only in case_intake's
        # vocabulary — a timeline plan carrying it is rejected.
        # (inspect_opposite_trades is now legitimately IN this skill's
        # vocabulary: a distinct, capability-bounded semantic request —
        # FIX E15.)
        result = check_plan(
            "timeline_investigation",
            [step("S1", "fetch_case", "risk_case_fetch")],
            capabilities=F3_CAPS,
        )
        assert not result.valid
        assert error_codes(result) == ["STEP_NOT_ALLOWED"]

    def test_opposite_trades_step_in_timeline_vocabulary(self):
        # FIX E15: opposite-trade requests specialize to this step; it is a
        # valid planning step of timeline_investigation and is bounded by
        # the tool (OPPOSITE_TRADES_NOT_SUPPORTED) — never a generic
        # evidence fallback.
        result = check_plan(
            "timeline_investigation",
            [step("S1", "inspect_opposite_trades", "finding_drilldown",
                  {"view": "opposite_trades"})],
            capabilities=F3_CAPS,
        )
        assert result.valid

    def test_wrong_tool_for_registered_step_rejected(self):
        result = check_plan(
            "timeline_investigation",
            [step("S1", "explain_signal", "policy_lookup")],   # wrong binding
            capabilities=F3_CAPS,
        )
        assert not result.valid
        assert "INVALID_STEP_TOOL_MAPPING" in error_codes(result)


# --- 8–10. parameter locks ---------------------------------------------------------------

class TestParameterLocks:
    def test_timeline_view_lock_accepts_correct_view(self):
        result = check_plan(
            "timeline_investigation",
            [step("S1", "inspect_timeline", "finding_drilldown", {"view": "timeline"})],
            capabilities=F3_CAPS,
        )
        assert result.valid, result.errors

    def test_timeline_view_lock_rejects_other_view(self):
        result = check_plan(
            "timeline_investigation",
            [step("S1", "inspect_timeline", "finding_drilldown",
                  {"view": "opposite_trades"})],
            capabilities=F3_CAPS,
        )
        assert not result.valid
        assert "PARAMETER_LOCK_VIOLATION" in error_codes(result)

    def test_missing_lock_key_rejected(self):
        result = check_plan(
            "timeline_investigation",
            [step("S1", "inspect_timeline", "finding_drilldown", {})],
            capabilities=F3_CAPS,
        )
        assert not result.valid
        assert "PARAMETER_LOCK_VIOLATION" in error_codes(result)

    def test_extra_arguments_do_not_violate_locks(self):
        # Locks constrain the locked keys; other args are free.
        result = check_plan(
            "timeline_investigation",
            [step("S1", "inspect_timeline", "finding_drilldown",
                  {"view": "timeline", "top_n": 10})],
            capabilities=F3_CAPS,
        )
        assert result.valid, result.errors

    def test_trade_skill_enforces_opposite_trades_lock(self):
        caps = finding_caps("opposite_trades")
        ok = check_plan(
            "trade_investigation",
            [step("S1", "inspect_opposite_trades", "finding_drilldown",
                  {"view": "opposite_trades"})],
            capabilities=caps,
        )
        bad = check_plan(
            "trade_investigation",
            [step("S1", "inspect_opposite_trades", "finding_drilldown",
                  {"view": "timeline"})],
            capabilities=caps,
        )
        assert ok.valid
        assert not bad.valid
        assert "PARAMETER_LOCK_VIOLATION" in error_codes(bad)


# --- 11. unknown tool ----------------------------------------------------------------------

class TestUnknownTool:
    def test_tool_outside_skill_allowlist_rejected(self):
        result = check_plan(
            "case_intake",
            [step("S1", "generate_artifact", "evidence_fetch")],  # legacy tool, not allowed
        )
        assert not result.valid
        codes = error_codes(result)
        assert "TOOL_NOT_ALLOWED" in codes

    def test_registry_mapping_prevents_arbitrary_step_to_tool_pairs(self):
        # The registered mapping for fetch_case is risk_case_fetch only;
        # any other binding is caught even before the allowlist matters.
        result = check_plan(
            "case_intake",
            [step("S1", "fetch_case", "network_drilldown")],
        )
        assert not result.valid
        assert "INVALID_STEP_TOOL_MAPPING" in error_codes(result)


# --- 12. structured deterministic results ------------------------------------------------------

class TestStructuredDeterministicResults:
    def test_result_is_structured_model_with_stable_shape(self):
        result = check_plan("nonexistent", [step("S1", "x", "y")])
        dump = result.model_dump()
        assert dump["valid"] is False
        assert isinstance(dump["errors"], list)
        err = dump["errors"][0]
        assert set(err.keys()) == {"code", "step_id", "message"}

    def test_same_input_yields_identical_result(self):
        steps = [
            step("S1", "inspect_timeline", "finding_drilldown", {"view": "wrong"}),
            step("S2", "mystery_step", "mystery_tool"),
        ]
        r1 = check_plan("timeline_investigation", steps, capabilities=F3_CAPS)
        r2 = check_plan("timeline_investigation", steps, capabilities=F3_CAPS)
        assert r1.model_dump() == r2.model_dump()

    def test_multiple_errors_accumulate_rather_than_fail_fast(self):
        result = check_plan(
            "timeline_investigation",
            [
                step("S1", "nope", "whoever"),
                step("S2", "inspect_timeline", "finding_drilldown", {"view": "bad"}),
            ],
            capabilities=finding_caps(),   # also fails capability gate
        )
        codes = error_codes(result)
        assert codes.count("CAPABILITY_NOT_SUPPORTED") == 1
        assert "STEP_NOT_ALLOWED" in codes
        assert "PARAMETER_LOCK_VIOLATION" in codes

    def test_contract_validation_returns_no_toolresult_semantics(self):
        # Contract validation never pretends a Risk Platform tool ran.
        from app.models import ToolResult
        result = check_plan("nonexistent", [])
        dumped = result.model_dump()
        serialized = str(dumped)
        assert "outcome" not in serialized          # no ToolResult envelope fields
        assert not isinstance(result, ToolResult)

"""Stage 2.1 — Structured Question / Goal Representation (LEVEL 1).

Covers the goal vocabulary contract, heuristic proposer, bounded LLM
classification (fake provider), ambiguity/UNRESOLVED semantics, and
target preservation (open-ended baseline §35b: target and goal are
separate; the goal never carries a tool id; ambiguous goals never guess).

Deterministic: FakeLLM only. Real-LLM evaluation lives in
test_target_resolution_llm_v1.py.
"""

import json

import pytest

from app.goal_resolution import (
    GOAL_VOCABULARY,
    GoalRepresentation,
    GoalResolutionStatus,
    heuristic_goal,
    understand_goal,
)


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.messages = []

    def generate(self, messages, max_tokens=0, temperature=0.1):
        self.messages = messages
        return self.response


def resolved(fid="F8"):
    return fid


# ---------------------------------------------------------------------------
# Vocabulary contract
# ---------------------------------------------------------------------------

class TestVocabulary:
    def test_no_tool_names_in_vocabulary(self):
        forbidden = {"cross_account_trade_analysis", "finding_drilldown",
                     "signal_explain", "policy_lookup", "risk_case_fetch",
                     "artifact_bundle", "self_opposite_trades"}
        assert not (forbidden & GOAL_VOCABULARY)

    def test_baseline_vocabulary_present(self):
        # open-ended baseline §5 vocabulary + the two E2/E3 additions
        for goal in ("expand_evidence", "explain_finding",
                     "identify_supporting_records", "timeline",
                     "cross_account_check", "policy_check", "summarize",
                     "artifact_export", "identify_related_accounts",
                     "inspect_network_structure"):
            assert goal in GOAL_VOCABULARY, goal

    def test_invented_goal_type_rejected_by_model(self):
        with pytest.raises(ValueError):
            GoalRepresentation(
                goal_type="cross_account_trade_analysis",   # a TOOL, not a goal
                goal_resolution=GoalResolutionStatus.INFERRED.value)

    def test_ambiguous_requires_two_candidates(self):
        with pytest.raises(ValueError):
            GoalRepresentation(
                goal_resolution=GoalResolutionStatus.AMBIGUOUS.value,
                ambiguous_goal_types=["expand_evidence"])


# ---------------------------------------------------------------------------
# Heuristic proposer (fast path / no-LLM fallback)
# ---------------------------------------------------------------------------

class TestHeuristic:
    def test_e1_related_hedging_maps_to_cross_account_check(self):
        g = heuristic_goal("那这个账号有与其他账号做关联对冲交易的情况吗",
                           None, "U00010")
        assert g.goal_type == "cross_account_check"

    def test_e2_related_accounts_maps_to_identify_related_accounts(self):
        g = heuristic_goal("那这个账户都和哪些账户有关联关系", None, "U00010")
        assert g.goal_type == "identify_related_accounts"

    def test_e3_network_question_maps_to_inspect_network_structure(self):
        g = heuristic_goal("这个账户的 Linked Account Network 具体是什么？",
                           None, "U00010")
        assert g.goal_type == "inspect_network_structure"

    def test_evidence_expansion(self):
        g = heuristic_goal("具体展开一下这个发现的证据", "F8", "U00010")
        assert g.goal_type == "expand_evidence"

    def test_explain(self):
        g = heuristic_goal("这个发现是怎么来的？", "F8", "U00010")
        assert g.goal_type == "explain_finding"

    def test_supporting_records(self):
        g = heuristic_goal("这个比例对应哪些交易？", "F8", "U00010")
        assert g.goal_type == "identify_supporting_records"

    def test_timeline(self):
        g = heuristic_goal("看看这个发现的时间线", "F8", "U00010")
        assert g.goal_type == "timeline"

    def test_policy_check(self):
        g = heuristic_goal("这个规则为什么触发？", "F8", "U00010")
        assert g.goal_type == "policy_check"

    def test_summarize(self):
        g = heuristic_goal("把这个发现总结一下", "F8", "U00010")
        assert g.goal_type == "summarize"

    def test_artifact_export(self):
        g = heuristic_goal("把刚才的调查结果整理成报告", "F8", "U00010")
        assert g.goal_type == "artifact_export"

    def test_unmatched_is_bounded_unresolved(self):
        g = heuristic_goal("这个账户的资金来源是哪里", None, "U00010")
        assert g.goal_type is None
        assert g.goal_resolution == GoalResolutionStatus.UNRESOLVED.value

    def test_subject_binds_to_resolved_finding(self):
        g = heuristic_goal("具体展开一下这个发现的证据", "F8", "U00010")
        assert g.subject_type == "finding"
        assert g.subject_ref == "F8"


# ---------------------------------------------------------------------------
# Bounded LLM classification (fake provider)
# ---------------------------------------------------------------------------

class TestLLMGoalUnderstanding:
    def test_llm_inferred_goal_validated(self):
        llm = FakeLLM('{"goal_resolution": "INFERRED", '
                      '"goal_type": "cross_account_check", '
                      '"reason": "asks about cross-account hedging"}')
        g = understand_goal("那这个账号有与其他账号做关联对冲交易的情况吗",
                            case_id="U00010", llm=llm)
        assert g.goal_type == "cross_account_check"
        assert g.goal_resolution == GoalResolutionStatus.INFERRED.value
        assert g.source == "llm"

    def test_llm_cannot_invent_goal_type(self):
        llm = FakeLLM('{"goal_resolution": "INFERRED", '
                      '"goal_type": "cross_account_trade_analysis", '
                      '"reason": "tool-shaped answer"}')
        g = understand_goal("find cross-account opposite trades",
                            resolved_finding_id="F8", llm=llm)
        # invented goal type rejected → heuristic fallback (bounded)
        assert g.goal_type != "cross_account_trade_analysis"
        assert g.source == "heuristic"

    def test_llm_amiguous_goal_represented_not_guessed(self):
        llm = FakeLLM('{"goal_resolution": "AMBIGUOUS", "goal_type": null, '
                      '"ambiguous_goal_types": ["expand_evidence", '
                      '"timeline"], "reason": "both plausible"}')
        g = understand_goal("这个发现具体看看", resolved_finding_id="F8",
                            llm=llm)
        assert g.goal_resolution == GoalResolutionStatus.AMBIGUOUS.value
        assert g.goal_type is None
        assert sorted(g.ambiguous_goal_types) == \
            ["expand_evidence", "timeline"]
        # resolved target is NOT erased by an ambiguous goal
        assert g.subject_ref == "F8"

    def test_llm_unresolved_goal_is_bounded(self):
        llm = FakeLLM('{"goal_resolution": "UNRESOLVED", "goal_type": null, '
                      '"reason": "cannot determine"}')
        g = understand_goal("这个再看看", resolved_finding_id="F8", llm=llm)
        assert g.goal_resolution == GoalResolutionStatus.UNRESOLVED.value
        # target preserved even when the goal is unresolved (§13)
        assert g.subject_ref == "F8"

    def test_llm_malformed_output_falls_back_to_heuristic(self):
        llm = FakeLLM("totally @{ not json")
        g = understand_goal("这个规则为什么触发？", resolved_finding_id="F8",
                            llm=llm)
        assert g.goal_type == "policy_check"
        assert g.source == "heuristic"

    def test_prompt_bounds_the_contract(self):
        llm = FakeLLM('{"goal_resolution": "INFERRED", '
                      '"goal_type": "expand_evidence"}')
        understand_goal("展开一下这个发现的证据", resolved_finding_id="F8",
                        llm=llm)
        system = llm.messages[0]["content"]
        assert "cross_account_trade_analysis" not in system   # no tool names
        assert "Never invent goal types" in system
        assert "untrusted" in system.lower()
        for goal in ("expand_evidence", "inspect_network_structure",
                     "cross_account_check"):
            assert goal in system


# ---------------------------------------------------------------------------
# Goal/target separation (§2) — ambiguous goal never erases the target
# ---------------------------------------------------------------------------

class TestTargetGoalSeparation:
    def test_ambiguous_goal_preserves_resolved_target(self):
        g = understand_goal("这个发现具体看看", resolved_finding_id="F8",
                            use_llm=False)
        assert g.goal_resolution == GoalResolutionStatus.UNRESOLVED.value
        assert g.subject_ref == "F8"
        assert g.subject_type == "finding"

    def test_target_resolution_kind_stays_in_stage_1(self):
        # Stage 1 metadata (resolution_kind on ResolutionOutcome) is
        # untouched by goal production — separate concepts, separate fields
        from app.context_resolution import ContextResolver
        from app.models import Finding, FindingCapability
        f = Finding(finding_id="F8", case_id="U00299",
                    type="rule_signal", title="Opposite Trade Ratio",
                    summary="ratio evidence",
                    capabilities=FindingCapability.model_validate(
                        ["timeline"]))
        r = ContextResolver(llm_assist=False).resolve(
            "show the finding titled Opposite Trade Ratio",
            __import__("app.models", fromlist=["InvestigationContext"])
            .InvestigationContext(case_id="U00299"), [f])
        assert r.resolution_kind == "inferred"
        assert not hasattr(r, "goal_type")

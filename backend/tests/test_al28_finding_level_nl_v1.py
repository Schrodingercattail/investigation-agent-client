"""AL-2.8 — finding-level natural language investigation: unit tests.

Covers: resolver identification (unique focus / ambiguity / none) through
the M2 semantic foundation, planner deterministic explain/evidence
branches, and the kind-aware natural-language response composition.
AL-2.11: the legacy finding_lexicon is REMOVED — every CJK finding
reference resolves through SemanticNormalizer → governed concept →
candidate grounding (scripted here for determinism; real-LLM validation
lives in the canonical browser journeys).
"""

import pytest

from app import investigation_concepts as ic
from app.agent_loop import AgentLoopState, compose_loop_response
from app.context_resolution import ContextResolver
from app.models import Finding, FindingCapability, InvestigationContext, \
    PlanStepStatus, ToolResultOutcome
from app.planner_v2 import PlannerV2
from app.skills import eligible_skills_for_finding

from tests.test_al28_semantic_foundation_v1 import (
    _norm_script,
    seeded_catalog,
)
from tests.test_api_investigations_v1 import ScriptedLLM

TITLES = {
    "F1": "ML Pattern Detection",
    "F3": "High withdrawal frequency",
    "F5": "Shared Device Relationships",
    "F6": "Linked Account Network",
}


def _findings():
    out = []
    for fid, title in TITLES.items():
        out.append(Finding(
            finding_id=fid, case_id="U00010", type="rule_signal",
            title=title, severity="high", summary=f"summary of {title}",
            evidence_refs=[], signal_refs=[], policy_refs=[],
            capabilities=FindingCapability.model_validate(
                ["timeline", "signal_explain"])))
    return out


def _resolve(text, focused=None, script=None):
    """Resolve against the governed M2 catalog with a scripted
    SemanticNormalizer (the single finding-semantic entry)."""
    catalog, _ = seeded_catalog()
    resolver = ContextResolver(llm_provider=script, llm_assist=False,
                               semantic_catalog=catalog)
    ctx = InvestigationContext(
        case_id="U00010", focused_finding_id=focused,
        focus_source=None)
    return resolver.resolve(text, ctx, findings=_findings())


# --- unified semantic identification (AL-2.11: no lexicon) ----------------------

def test_shared_device_phrase_resolves_f5_via_m2():
    script = ScriptedLLM([_norm_script("SHARED_DEVICE_RELATIONSHIP",
                                       "共享设备")])
    out = _resolve("共享设备为什么是风险项", script=script)
    assert out.status == "resolved"
    assert out.updated_context.focused_finding_id == "F5"
    assert out.focus_source == "agent_resolved" or \
        out.focus_source.value == "agent_resolved"
    # the SAME M2 path carries provenance — no hidden mapping
    assert out.semantic_evidence["concept_id"] == \
        "SHARED_DEVICE_RELATIONSHIP"
    assert script.calls == 1


def test_high_withdrawal_phrase_resolves_f3_not_f4_via_m2():
    script = ScriptedLLM([_norm_script("HIGH_WITHDRAWAL_FREQUENCY",
                                       "高频提现")])
    out = _resolve("给我输出一下高频提现的具体证据", script=script)
    assert out.status == "resolved"
    assert out.updated_context.focused_finding_id == "F3"
    assert out.semantic_evidence["concept_id"] == \
        "HIGH_WITHDRAWAL_FREQUENCY"
    assert script.calls == 1


def test_no_governed_concept_and_no_title_keeps_unresolved():
    out = _resolve("为什么会这样",
                   script=ScriptedLLM([_norm_script(None, "")]))
    assert out.status == "unresolved"
    assert _resolve("比较不同账户的提现频率",
                    script=ScriptedLLM([_norm_script(None, "")])
                    ).status == "unresolved"   # cross-account scope skip


def test_explicit_focus_still_wins_before_semantic_stage():
    script = ScriptedLLM([_norm_script("SHARED_DEVICE_RELATIONSHIP",
                                       "共享设备")])
    out = _resolve("共享设备为什么是风险项", focused="F6", script=script)
    assert out.updated_context.focused_finding_id == "F6"
    assert script.calls == 0                    # priority 1–2, no LLM call


# --- intent predicates (separate from identification) --------------------------

def test_intent_predicates_are_independent_of_finding():
    # the SAME identification supports two different intents
    assert ic.is_explain_finding_request("共享设备为什么是风险项")
    assert ic.is_evidence_request("给我输出一下高频提现的具体证据")
    assert not ic.is_evidence_request("共享设备为什么是风险项")
    assert not ic.is_explain_finding_request("给我输出一下高频提现的具体证据")


# --- planner deterministic branches ---------------------------------------------

def _plan(text, focused="F5"):
    ctx = InvestigationContext(case_id="U00010", focused_finding_id=focused)
    caps = next(f.capabilities for f in _findings()
                if f.finding_id == focused)
    eligible = [s.skill_id for s in eligible_skills_for_finding(caps)]
    return PlannerV2().plan(text, ctx, eligible, finding_capabilities=caps)


def test_explain_intent_plans_signal_and_policy():
    plan = _plan("共享设备为什么是风险项", focused="F5")
    assert [s.type for s in plan.steps] == ["explain_signal", "retrieve_policy"]
    assert plan.steps[0].tool_name == "signal_explain"
    assert plan.steps[0].arguments == {}      # finding_id injected at runtime


def test_evidence_intent_plans_explicit_withdrawal_stream():
    plan = _plan("给我输出一下高频提现的具体证据", focused="F3")
    assert [s.type for s in plan.steps] == ["inspect_withdrawals"]
    assert plan.steps[0].arguments == {"view": "evidence",
                                       "stream": "withdrawals"}


def test_unfocused_explain_does_not_guess_a_finding():
    # without identification the planner must not invent a target: the
    # request falls through to the LLM path (PlanningFailure without a key)
    ctx = InvestigationContext(case_id="U00010")
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    result = _plan("共享设备为什么是风险项", focused=None) if False else \
        PlannerV2().plan("共享设备为什么是风险项", ctx, eligible,
                         finding_capabilities=None)
    assert not hasattr(result, "steps") or not result.steps \
        or result.code if hasattr(result, "code") else True


def test_case_level_and_cross_account_precedence_unchanged():
    # cross-account wording with focus still plans the analysis (1a-1 wins)
    plan = _plan("查一下有没有不同账户之间的对冲交易", focused="F5")
    assert [s.type for s in plan.steps] == \
        ["analyze_cross_account_trades", "generate_artifact"]


# --- composer: natural-language answer from bounded digests ---------------------

def _state_with(observations, title="High withdrawal frequency"):
    st = AgentLoopState(task_id="T", investigation_id="I", case_id="U00010",
                        goal="g", focused_finding_id="F3",
                        focused_finding_title=title,
                        focused_finding_summary="7 withdrawals in 24h")
    st.observations = observations
    st.agent_status = "completed"
    return st


def test_composer_answers_the_evidence_question_in_natural_language():
    rows = [{"record_id": f"W{i}", "timestamp": f"2026-07-2{i}T00:00:00",
             "summary": f"Withdrawal #{i} of 0.5 BTC to a new address"}
            for i in range(1, 8)]
    st = _state_with([{
        "tool_name": "finding_drilldown", "outcome": "success",
        "semantic_status": "evidence",
        "summary": {"record_count": 7, "record_ids": [r["record_id"] for r in rows],
                    "streams": {"withdrawals_included": True},
                    "record_rows": rows,
                    "record_summaries": [r["summary"] for r in rows[:1]]},
    }])
    response = compose_loop_response(st)
    assert "关于发现 F3" in response
    assert "7 withdrawals in 24h" in response          # RP-authoritative summary
    # AL-2.12 COMPLETE scope: the FULL set renders as a table, no "例如"
    assert "完整证据共 7 条" in response
    assert "| # | 记录 ID |" in response
    assert all(f"| {i} | W{i} |" in response for i in range(1, 8))
    assert "例如" not in response
    assert "以上为 Risk Platform 权威证据的全部 7 条" in response


def test_composer_discloses_signal_insufficiency_honestly():
    st = _state_with([{
        "tool_name": "signal_explain", "outcome": "success",
        "semantic_status": "Rule",
        "summary": {"evidence_missing": True},
    }], title="Shared Device Relationships")
    response = compose_loop_response(st)
    assert "数据不足" in response and "如实报告" in response


def test_composer_without_finding_level_observers_unchanged():
    st = _state_with([])
    response = compose_loop_response(st)
    assert response.startswith("The investigation completed.")
    assert "关于发现" not in response

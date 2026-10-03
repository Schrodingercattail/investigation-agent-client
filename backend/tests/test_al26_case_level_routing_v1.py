"""AL-2.6 — case-level cross-account routing: unit/component tests.

Covers the shared concept matcher (single source of truth), the
ContextResolver case-level route, and the PlannerV2 deterministic
governed branch. The real-scenario E2Es (raw goal → real RP) live in
test_al26_real_rp_case_level_e2e_v1.py.
"""

import pytest

from app import investigation_concepts as ic
from app.context_resolution import ContextResolver
from app.models import FindingCapability, FocusSource, InvestigationContext
from app.planner_v2 import PlannerV2, _is_cross_account_trade_request
from app.skills import SKILLS, eligible_skills_for_finding, step_path_executable

USER_GOAL = ("查一下有没有不同账户之间的对冲交易情况，如果有，输出相关证据给我；"
             "如果没有，告诉我你都查了哪些范围内的账户从而判定没有关联交易的")


# --- shared concept matcher ---------------------------------------------------

@pytest.mark.parametrize("text", [
    USER_GOAL,
    "有没有不同账户之间的对冲交易",
    "查一下跨账户对倒",
    "cross-account opposite trades",
    "find opposite trades across accounts",
    "trades between accounts",
    "check whether related accounts traded opposite sides",
    "is there hedging between accounts?",
])
def test_shared_matcher_accepts_supported_intents(text):
    assert ic.is_cross_account_trade_analysis_request(text), text
    assert ic.mentions_cross_account(text), text


@pytest.mark.parametrize("text", [
    # accounts named but NOT the trade/hedging subject — no supported tool
    "比较不同账户的提现频率",
    "展开说下有哪些关联账户",
    "tell me about those accounts",
    # trade mentioned but single-account scope — finding-level, not case-level
    "Show me the opposite trade.",
    "为什么会触发这个规则",
])
def test_shared_matcher_rejects_unsupported_requests(text):
    assert not ic.is_cross_account_trade_analysis_request(text), text


def test_planner_wording_check_delegates_to_shared_matcher():
    # behavior-preserving delegation: the former local regex phrasings
    for text in ("find opposite trades across accounts",
                 "look for cross-account opposite-side trades",
                 "check whether related accounts traded opposite sides",
                 "trades between accounts", "跨账户对倒"):
        assert _is_cross_account_trade_request(text), text
    assert not _is_cross_account_trade_request("Show me the opposite trade.")


# --- resolver route ------------------------------------------------------------

def _resolve(text, case_id="U00010", findings=None):
    return ContextResolver(llm_assist=False).resolve(
        text, InvestigationContext(case_id=case_id), findings=list(findings or []))


def test_supported_case_level_intent_routes_case_level():
    out = _resolve(USER_GOAL)
    assert out.status == "unchanged_case_level"
    assert out.updated_context.focused_finding_id is None


def test_route_requires_known_case():
    # identification stage (no case yet): cross-account wording must NOT
    # route case-level — falls through to the existing contract
    out = ContextResolver(llm_assist=False).resolve(
        USER_GOAL, InvestigationContext(case_id=""), findings=[])
    assert out.status != "unchanged_case_level"


def test_unrelated_requests_keep_existing_contract():
    assert _resolve("tell me about the thing that does not exist") \
        .status == "unresolved"
    assert _resolve("比较不同账户的提现频率").status == "unresolved"
    # focused finding still wins (Priority 1/2 unchanged)
    ctx = InvestigationContext(case_id="U00010", focused_finding_id="F6",
                               focus_source=FocusSource.USER_SELECTED)
    out = ContextResolver(llm_assist=False).resolve(USER_GOAL, ctx, findings=[])
    assert out.updated_context.focused_finding_id == "F6"


# --- planner deterministic governed branch --------------------------------------

def _plan(text, ctx, eligible, caps=None):
    return PlannerV2().plan(text, ctx, eligible, finding_capabilities=caps)


def test_case_level_intent_plans_governed_analysis_without_focus():
    ctx = InvestigationContext(case_id="U00010")
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    result = _plan(USER_GOAL, ctx, eligible)
    assert [s.type for s in result.steps] == \
        ["analyze_cross_account_trades", "generate_artifact"]
    assert result.steps[0].tool_name == "cross_account_trade_analysis"
    # registry lock, planner can never emit another analysis type
    assert result.steps[0].arguments == {
        "analysis_type": "cross_account_opposite_trade"}


def test_new_skill_is_case_level_and_executable():
    skill = SKILLS["case_trade_analysis"]
    assert skill.required_capabilities == []          # case-level
    assert eligible_skills_for_finding(None)  # includes it without focus
    assert "case_trade_analysis" in [s.skill_id for s in
                                     eligible_skills_for_finding(None)]
    assert step_path_executable("analyze_cross_account_trades")
    assert step_path_executable("generate_artifact")


def test_focused_context_gets_same_governed_plan():
    caps = FindingCapability.model_validate(["self_opposite_trades"])
    ctx = InvestigationContext(case_id="U90001", focused_finding_id="F2",
                               focus_source=FocusSource.USER_SELECTED)
    result = _plan("Find opposite trades across accounts.", ctx,
                   ["trade_investigation"], caps)
    assert [s.type for s in result.steps] == \
        ["analyze_cross_account_trades", "generate_artifact"]
    assert result.steps[0].tool_name == "cross_account_trade_analysis"

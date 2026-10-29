"""AL-2.7 Defect A — in-conversation case-id recognition: unit matrix.

A bound conversation (case U00010) must route CASE-LEVEL when the user
explicitly names the current case id — in ANY natural phrasing — instead
of requiring the single CJK marker 「调查」. The shared entity matcher is
the same one the create-time parser and the cross-case guard use.

Boundaries pinned:
- no blanket "no finding → case-level": unsupported/vague requests keep
  the existing safe UNRESOLVED terminal;
- a different case id in the text keeps the cross-case guard;
- an explicit finding focus still wins (Priority 1/2 precedence);
- a non-existent-shaped case reference keeps the cross-case guidance
  (never a fabricated identification).
"""

import pytest

from app.context_resolution import ContextResolver
from app.models import Finding, FindingCapability, FocusSource, \
    InvestigationContext


def _findings():
    return [Finding(finding_id=f"F{i}", case_id="U00010", type="rule_signal",
                    title=t, severity="high", summary="s", evidence_refs=[],
                    signal_refs=[], policy_refs=[],
                    capabilities=FindingCapability.model_validate(["timeline"]))
            for i, t in enumerate([
                "ML Pattern Detection", "New account with high activity",
                "High withdrawal frequency", "First withdrawal to new address",
                "Shared Device Relationships", "Linked Account Network",
                "High Trading Frequency", "Opposite Trade Ratio"], 1)]


def _resolve(text, case_id="U00010", focused=None):
    ctx = InvestigationContext(
        case_id=case_id, focused_finding_id=focused,
        focus_source=FocusSource.USER_SELECTED if focused else None)
    return ContextResolver(llm_assist=False).resolve(
        text, ctx, findings=_findings())


@pytest.mark.parametrize("text", [
    "调查00010",
    "展开一下00010案例情况",
    "看看00010怎么回事",
    "详细说说00010",
    "00010有哪些发现",
    "展开一下 00010",
    "分析一下00010这个案例",
])
def test_current_case_id_mention_routes_case_level(text):
    out = _resolve(text)
    assert out.status == "unchanged_case_level", (text, out.status)
    assert out.updated_context.focused_finding_id is None


def test_no_id_and_no_intent_stays_unresolved():
    # the vague-this guard and the Priority-5 fallback are unchanged
    assert _resolve("tell me about the thing that does not exist") \
        .status == "unresolved"
    assert _resolve("为什么会这样").status == "unresolved"
    # CJK phrasing WITHOUT a case id keeps the existing contract
    # (documented limitation: 案例级词汇本身不扩展)
    assert _resolve("展开一下这个案例").status == "unresolved"


def test_different_case_id_keeps_cross_case_guard():
    out = _resolve("调查00999")
    assert out.status == "unresolved"
    assert "U00999" in (out.clarification_message or "")
    # per-turn response language: a zh request gets the zh guidance face
    assert "新建一个调查" in (out.clarification_message or "")


def test_explicit_focus_still_wins_over_case_mention():
    out = _resolve("展开一下00010案例情况", focused="F6")
    assert out.updated_context.focused_finding_id == "F6"

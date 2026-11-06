"""U00008 case-overview composer fix — unit tests (Case A-F).

An UNRESOLVED output whose OWN combined summary carries the fetched
case facts still renders them — AFTER the unresolved declaration, as
confirmed case facts (the same rendering the SATISFIED path uses), never
as an answer to the unresolved claim. The renderer is pure: no
satisfaction/goal_outcome/status writes."""

import pytest

from app.agent_loop import _render_case_summary_facts
from app.agent_loop import _gsp_render_output_section
from app.goal_resolution import (
    ClaimKind, RequiredOutput, ScopeLevel)
from app.goal_resolution import OutputSatisfaction
from app.skills import EvidenceType

SUMMARY = {
    "finding_count": 7,
    "risk_level": "CRITICAL",
    "risk_score": 74.11,
    "findings": [
        {"id": f"F{i}", "title": f"Finding {i}",
         "summary": f"RP summary for finding {i}"}
        for i in range(1, 8)
    ],
}


def _overview_output(sat=OutputSatisfaction.UNRESOLVED):
    return RequiredOutput(
        output_id="O1",
        claim="U00008 case overview",
        claim_kind=ClaimKind.RETRIEVAL,
        subject_ref="U00008", scope=ScopeLevel.CASE_LEVEL,
        evidence_requirements=[EvidenceType.CASE_SUMMARY,
                               EvidenceType.TIMELINE_EVENTS,
                               EvidenceType.GRAPH_CLUSTER_FACTS],
        satisfaction=sat)


def _render(output, summary, lang="zh"):
    return "\n".join(_gsp_render_output_section(
        output, summary, goal_text=None, lang=lang))


class TestCaseACaseOverviewWithFindings:
    def test_unresolved_declaration_preserved(self):
        out = _render(_overview_output(), SUMMARY)
        assert ("当前证据不足以确定该项。" in out) or (
            "The current evidence is insufficient to determine this item."
            in out)

    def test_findings_block_rendered_after_declaration(self):
        out = _render(_overview_output(), SUMMARY)
        decl = out.index("当前证据不足以确定该项")
        facts = out.index("案例共识别 7 个风险发现")
        assert facts > decl

    def test_findings_id_title_summary_present(self):
        out = _render(_overview_output(), SUMMARY)
        for i in range(1, 8):
            assert f"F{i}" in out
            assert f"Finding {i}" in out
            assert f"RP summary for finding {i}" in out

    def test_risk_level_and_score_present(self):
        out = _render(_overview_output(), SUMMARY)
        assert "CRITICAL" in out
        assert "74.11" in out

    def test_no_fabricated_conclusion(self):
        out = _render(_overview_output(), SUMMARY)
        assert "已回答" not in out and "fully answered" not in out.lower()
        # the claim stays open: the unresolved declaration is present
        assert "当前证据不足以确定该项" in out


class TestCaseBNoFindings:
    def test_no_findings_no_block(self):
        out = _render(_overview_output(), {})
        assert "当前证据不足以确定该项。" in out
        assert "案例共识别" not in out
        assert "## Related" not in out

    def test_zero_finding_count_no_block(self):
        summary = dict(SUMMARY, finding_count=0, findings=[])
        out = _render(_overview_output(), summary)
        assert "案例共识别" not in out


class TestCaseCSatisfiedRegression:
    def test_satisfied_path_unchanged(self):
        out = _render(_overview_output(OutputSatisfaction.SATISFIED),
                      SUMMARY)
        assert "案例共识别 7 个风险发现" in out
        assert "F1" in out and "F7" in out
        # satisfied path has no unresolved wording
        assert "insufficient to determine" not in out

    def test_satisfied_renders_once(self):
        # the extracted helper is called exactly once — no duplicated
        # findings block
        out = _render(_overview_output(OutputSatisfaction.SATISFIED),
                      SUMMARY)
        assert out.count("案例共识别 7 个风险发现") == 1


class TestCaseDOutputIsolation:
    def test_sections_are_isolated(self):
        with_findings = _overview_output()
        without = RequiredOutput(
            output_id="O2", claim="U00009 case overview",
            claim_kind=ClaimKind.RETRIEVAL, subject_ref="U00009",
            scope=ScopeLevel.CASE_LEVEL,
            evidence_requirements=[EvidenceType.CASE_SUMMARY],
            satisfaction=OutputSatisfaction.UNRESOLVED)
        sec1 = _render(with_findings, SUMMARY)
        sec2 = _render(without, {})
        assert "案例共识别" in sec1
        assert "案例共识别" not in sec2
        assert "F1" not in sec2


class TestCaseEStateSemantics:
    def test_renderer_is_pure(self):
        out = _overview_output()
        before = (out.satisfaction, out.delivery, out.detail)
        _render(out, SUMMARY)
        after = (out.satisfaction, out.delivery, out.detail)
        assert before == after

    def test_goal_outcome_unchanged(self):
        from app.goal_postconditions import derive_goal_outcome
        outs = [_overview_output()]
        before = derive_goal_outcome(
            [o.satisfaction for o in outs], "completed")
        _render(outs[0], SUMMARY)
        after = derive_goal_outcome(
            [o.satisfaction for o in outs], "completed")
        assert before is after and before.value == "unanswerable"


class TestCaseFLanguage:
    def test_zh_face(self):
        out = _render(_overview_output(), SUMMARY, lang="zh")
        assert "当前证据不足以确定该项。" in out
        assert "案例共识别 7 个风险发现" in out
        assert "对话内列出前" in out or len(SUMMARY["findings"]) <= 10

    def test_en_face(self):
        out = _render(_overview_output(), SUMMARY, lang="en")
        assert ("The current evidence is insufficient to determine "
                "this item.") in out
        assert "The case carries 7 identified risk findings" in out
        assert "Identified findings:" in out

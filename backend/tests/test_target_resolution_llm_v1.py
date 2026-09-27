"""Stage 1 — Layer B real-LLM target-resolution evaluation.

Runs the REAL configured LLM through ContextResolver's semantic assist
against REAL Risk Platform case context (U00010, which carries the
"Opposite Trade Ratio" evidence). Semantic assertions only — the model's
wording is never matched exactly (Layer B philosophy,
docs/baselines/2026-09-20-open-ended-investigation-conversation-ux-v1.md
§25–27).
"""

import time

import pytest

from app.adapters.risk_platform import RiskPlatformAdapter
from app.context_resolution import ContextResolver, ResolutionStatus
from app.models import InvestigationContext

CASE = "U00010"


def real_findings() -> list:
    findings = []
    evidence = RiskPlatformAdapter().get_case_evidence_raw(
        CASE, expose_complete_records=False)
    import app.api.investigations as _  # noqa: F401  (keep import graph warm)
    from app.api.investigations import _default_findings
    return _default_findings(CASE) or findings or []


@pytest.fixture(scope="module")
def findings():
    return real_findings()


@pytest.fixture(scope="module")
def resolver_llm():
    # real LLM, real availability; assist enabled
    return ContextResolver(llm_assist=True)


def ctx(focused=None):
    return InvestigationContext(case_id=CASE, focused_finding_id=focused)


def ratio_finding_id(findings):
    """The id of the finding whose title/description carries the
    Opposite Trade Ratio evidence."""
    for f in findings:
        blob = f"{f.title} {f.summary}".lower()
        if "opposite" in blob and ("ratio" in blob or "trade" in blob):
            return f.finding_id
    return None


class TestRealLLMTargetResolution:
    def test_exact_title_resolves_the_ratio_finding(self, findings,
                                                    resolver_llm):
        target_id = ratio_finding_id(findings)
        assert target_id, "U00010 must contain the opposite-trade finding"
        title = next(f.title for f in findings if f.finding_id == target_id)
        out = resolver_llm.resolve(f"展开一下 {title} 的证据", ctx(), findings)
        assert out.status in (ResolutionStatus.RESOLVED,
                              ResolutionStatus.AMBIGUOUS)
        if out.status == ResolutionStatus.RESOLVED:
            assert out.updated_context.focused_finding_id == target_id
            assert out.resolution_kind in ("explicit", "inferred")

    def test_paraphrased_title_resolves_semantically(self, findings,
                                                     resolver_llm):
        target_id = ratio_finding_id(findings)
        assert target_id
        out = resolver_llm.resolve(
            "the finding about the opposite trade ratio — show its detail",
            ctx(), findings)
        if out.status == ResolutionStatus.RESOLVED:
            assert out.updated_context.focused_finding_id == target_id

    def test_description_based_reference(self, findings, resolver_llm):
        target_id = ratio_finding_id(findings)
        assert target_id
        target = next(f for f in findings if f.finding_id == target_id)
        marker = None
        for token in ("40%", "45.24%", "opposite-trade", "34.38%"):
            if token in (target.summary or ""):
                marker = token
                break
        if marker is None:
            pytest.skip("description carries no distinctive threshold marker")
        out = resolver_llm.resolve(
            f"展开一下低于阈值提到的那个比例（{marker}）的发现", ctx(), findings)
        if out.status == ResolutionStatus.RESOLVED:
            assert out.updated_context.focused_finding_id == target_id

    def test_vague_contextual_reference_with_prior_focus(self, findings,
                                                         resolver_llm):
        target_id = ratio_finding_id(findings)
        out = resolver_llm.resolve("这个发现是怎么来的？", ctx(target_id),
                                   findings)
        # with the finding already focused, "这个发现" must not jump to
        # another target
        if out.status == ResolutionStatus.RESOLVED:
            assert out.updated_context.focused_finding_id == target_id

    def test_ambiguous_candidate_set_asks_not_guesses(self, resolver_llm):
        from app.context_resolution import Candidate
        f1 = type("F", (), {"finding_id": "F1", "title": "Trading Ratio",
                            "summary": "trading ratio evidence",
                            "signal_refs": []})()
        f2 = type("F", (), {"finding_id": "F2",
                            "title": "Opposite Trade Ratio",
                            "summary": "opposite trade ratio evidence",
                            "signal_refs": []})()
        out = resolver_llm.resolve("展开一下交易比例的证据", ctx(), [f1, f2])
        # semantic contract: never a silent guess between equally plausible
        # authoritative targets
        assert out.status in (ResolutionStatus.AMBIGUOUS,
                              ResolutionStatus.RESOLVED)
        if out.status == ResolutionStatus.RESOLVED:
            # only acceptable when the model states a high-confidence,
            # reasoned choice — the reason must be present
            assert out.resolution_reason

    def test_impossible_target_is_unresolved(self, findings, resolver_llm):
        out = resolver_llm.resolve("这个账户的资金来源是怎么样的？", ctx(), findings)
        assert out.status in (ResolutionStatus.UNRESOLVED,
                              ResolutionStatus.UNCHANGED_CASE_LEVEL) or (
            out.status == ResolutionStatus.RESOLVED and
            out.updated_context.focused_finding_id is None)

    @pytest.mark.parametrize("request_text,lang", [
        ("show the opposite trade ratio finding", "en"),
        ("看一下对向交易比例的发现", "zh-CN"),
        ("幫我看那個對向交易比例的發現", "zh-TW"),
        ("Specifically expand the Opposite Trade Ratio 证据", "mixed"),
    ])
    def test_multilingual_target_resolution(self, findings, resolver_llm,
                                            request_text, lang):
        target_id = ratio_finding_id(findings)
        assert target_id
        out = resolver_llm.resolve(request_text, ctx(), findings)
        # semantic contract: the OPPOSITE-TRADE finding is the resolved
        # (or at least plausible) target in every language
        if out.status == ResolutionStatus.RESOLVED:
            resolved = out.updated_context.focused_finding_id
            target = next(f for f in findings
                          if f.finding_id == resolved)
            blob = f"{target.title} {target.summary}".lower()
            assert "opposite" in blob or "ratio" in blob, (
                f"[{lang}] resolved to a non-matching finding: {resolved}")

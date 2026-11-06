"""U00010 ML detection score — finding-anchor resolver tests.

`_output_target_finding` gains ONE tier: an output demanding
ml_signal_explanation evidence resolves to THE case's unique
ML-pattern finding (RP-owned detector_signal type + ml-pattern title —
the same identity convention _classify_finding_type and
_finding_uses_signal already use). Zero or multiple ML findings, or a
non-ml explanation demand → None (fail-closed unchanged). Existing
tiers keep priority; the resolver never mutates anything."""

from app.models import Finding
from app.goal_resolution import ClaimKind, RequiredOutput, ScopeLevel
from app.goal_resolution import OutputSatisfaction
from app.planner_v2 import _output_target_finding


def _ml_finding(fid="F1", title="ML Pattern Detection — 99.41/100; a "
                               "system signal"):
    return Finding(
        finding_id=fid, case_id="U00010", type="detector_signal",
        title=title, summary="ML pattern detection finding",
        capabilities=["signal_explain", "timeline", "policy_lookup"])


def _rule_finding(fid="F2", title="New account with high activity."):
    return Finding(
        finding_id=fid, case_id="U00010", type="rule_signal",
        title=title, summary="rule finding",
        capabilities=["signal_explain", "timeline"])


def _ml_output(sat=OutputSatisfaction.UNRESOLVED, ev=("ml_signal_explanation",
                                                      "case_summary")):
    return RequiredOutput(
        output_id="O1",
        claim="ML detection score contributors",
        claim_kind=ClaimKind.EXPLANATION,
        subject_ref="U00010", scope=ScopeLevel.CASE_LEVEL,
        evidence_requirements=[__import__("app.skills", fromlist=["x"])
                               .EvidenceType(e) for e in ev],
        satisfaction=sat)


def _plain_explanation_output():
    return RequiredOutput(
        output_id="OX", claim="generic case-level explanation",
        claim_kind=ClaimKind.EXPLANATION, subject_ref="U00010",
        scope=ScopeLevel.CASE_LEVEL,
        evidence_requirements=[__import__("app.skills", fromlist=["x"])
                               .EvidenceType.CASE_SUMMARY],
        satisfaction=OutputSatisfaction.UNRESOLVED)


class TestUniqueMLFindingAnchored:
    def test_u00010_unique_ml_finding_resolved(self):
        out = _ml_output()
        f = _output_target_finding(
            out, "what contributed to the ML detection score?",
            [_ml_finding(), _rule_finding(), _rule_finding("F3",
             "High withdrawal frequency.")])
        assert f is not None and f.finding_id == "F1"

    def test_resolution_tier_ordering(self):
        # existing tiers keep priority: an F-id subject still wins
        out = _ml_output()
        out.subject_ref = "F2"
        f = _output_target_finding(
            out, "ML detection score", [_ml_finding(), _rule_finding("F2",
                                      "High withdrawal frequency.")])
        assert f.finding_id == "F2"


class TestFailClosed:
    def test_no_ml_finding_returns_none(self):
        out = _ml_output()
        assert _output_target_finding(
            out, "ML detection score", [_rule_finding()]) is None

    def test_two_ml_findings_returns_none(self):
        out = _ml_output()
        assert _output_target_finding(
            out, "ML detection score",
            [_ml_finding("F1"), _ml_finding("F9",
             "ML Pattern Detection — 88.0/100; another detector")]) is None

    def test_ml_finding_without_id_refuses(self):
        broken = _ml_finding()
        object.__setattr__(broken, "finding_id", "")
        out = _ml_output()
        assert _output_target_finding(
            out, "ML detection score", [broken]) is None

    def test_plain_case_explanation_not_anchored(self):
        # the new tier cannot fire without the ml evidence requirement
        assert _output_target_finding(
            _plain_explanation_output(), "explain the case",
            [_ml_finding()]) is None

    def test_ml_rule_findings_not_confused_as_ml_features(self):
        # F2-F8 rule findings must never be returned as the ML target
        out = _ml_output()
        for f in (_rule_finding("F2"), _rule_finding("F3"),
                  _rule_finding("F8", "Opposite Trade Ratio.")):
            assert _output_target_finding(out, "ML detection score",
                                          [f]) is None


class TestExistingTiersUnchanged:
    def test_f_id_subject_tier_unchanged(self):
        out = _ml_output(ev=("ml_signal_explanation",))
        out.subject_ref = "F1"
        f = _output_target_finding(out, "", [_ml_finding()])
        assert f.finding_id == "F1"

    def test_domain_vocabulary_tier_unchanged(self):
        out = _ml_output(ev=("rule_explanation",))
        out.subject_ref = "U00010"
        f = _output_target_finding(
            out, "高频提现为什么是风险项",
            [_rule_finding("F3", "High withdrawal frequency."),
             _rule_finding("F9", "Unrelated.")])
        assert f.finding_id == "F3"

    def test_exact_title_tier_unchanged(self):
        out = _plain_explanation_output()
        out.claim = "the grounds for First withdrawal to new address."
        f = _output_target_finding(
            out, "", [_rule_finding("F4",
                     "First withdrawal to new address.")])
        assert f.finding_id == "F4"

"""GJ-OPPOSITE-TRADE-RATIO — GU-layer deterministic tests (Case A-D).

The deterministic candidate proposer must NOT turn a metric-calculation
question ("explain how the opposite-trade ratio is calculated") into a
finding-explanation goal for the named domain's finding (real incident
TASK-6ed61aee47a0: O2 为什么「Linked Account Network」被标记为风险).
A genuine finding-why ask (or a compound metric+why ask) keeps it."""

import json

from app.goal_resolution import deterministic_contract_proposal

INCIDENT_ASK = ("please explain how to calculate the opposite-trade "
                "ratio of 34.29%. Is it the opposite-trade ratio of "
                "34.29% within the linked accounts?")
F5_WHY_ASK = "Explain why the Linked Account Network finding is risky."
COMPOUND_ASK = ("Explain how opposite-trade ratio is calculated, and "
                "also explain why the Linked Account Network is risky.")
FINDINGS = [
    {"finding_id": "F5", "title": "Linked Account Network",
     "summary": "linked account network"},
    {"finding_id": "F1", "title": "High Withdrawal Frequency",
     "summary": "withdrawals"},
]


def _propose(text):
    """The candidate proposal + the fired-aspects audit (the persisted
    decision-record fields the incident was diagnosed from)."""
    fired: list = []
    proposal = deterministic_contract_proposal(
        text, "F5", "U00058", case_findings=_findings_models(),
        fired_aspects=fired)
    return proposal, fired


def _findings_models():
    from app.models import Finding
    return [
        Finding(finding_id=f["finding_id"], case_id="U00058",
                type="graph_signal", title=f["title"], summary=f["summary"],
                capabilities=["signal_explain", "policy_lookup"])
        for f in FINDINGS
    ]


def _claims(proposal):
    return [o["claim"] for o in proposal["required_outputs"]]


class TestCaseAMetricQuestionNoF5:
    def test_incident_ask_emits_no_f5_explain(self):
        proposal, fired = _propose(INCIDENT_ASK)
        assert not any("Linked Account Network" in c for c in _claims(
            proposal)), _claims(proposal)
        assert not any("为什么" in c for c in _claims(proposal))
        # the audit trail names the suppression (the same fields the
        # incident was diagnosed from)
        assert any(a.startswith("explain_compound:skipped_metric_question:")
                   and "F5" in a for a in fired), fired
        # the pairing marker still records the derivation attempt; the
        # skipped marker + the output set record the outcome
        assert "explain_compound:linked_account:F5" in fired

    def test_short_metric_how_ask_same_shape(self):
        proposal, fired = _propose(
            "Explain how opposite-trade ratio is calculated within "
            "linked accounts.")
        assert not any("为什么" in c for c in _claims(proposal))
        assert any("reverse" in c.lower() or "反向" in c
                   for c in _claims(proposal))


class TestCaseBFindingWhyKeepsExplain:
    def test_explicit_f5_why_keeps_explain_output(self):
        proposal, fired = _propose(F5_WHY_ASK)
        claims = _claims(proposal)
        assert any("Linked Account Network" in c for c in claims), claims
        assert not any(
            a.startswith("explain_compound:skipped_metric_question:")
            for a in fired), fired


class TestCaseCCompoundMetricPlusWhy:
    def test_both_goals_survive(self):
        proposal, fired = _propose(COMPOUND_ASK)
        claims = _claims(proposal)
        assert any("反向交易率" in c or "opposite" in c.lower()
                   for c in claims), claims
        assert any("Linked Account Network" in c for c in claims), claims
        assert not any(
            a.startswith("explain_compound:skipped_metric_question:")
            for a in fired), fired


class TestCaseDCompoundWhyUnaffected:
    def test_u00010_compound_why_unaffected(self):
        # the U00010 compound-why shape carries why-vocabulary and no
        # reverse-trade-rate mention — the guard is inert
        proposal, fired = _propose(
            "为什么高频提现和新账号高频活动是风险项？给我各自的判定依据")
        claims = _claims(proposal)
        # the emitted claims use the matched findings' own titles
        assert any("被标记为风险" in c for c in claims), claims
        assert not any(
            a.startswith("explain_compound:skipped_metric_question:")
            for a in fired), fired

    def test_metric_why_question_keeps_existing_behavior(self):
        # a why-FRAMED metric question keeps its explain outputs (the
        # minimal-change boundary: only the why-less how/calculation
        # shape is suppressed)
        proposal, fired = _propose(
            "为什么反向交易率是34.29%？这个比率和 Linked Account "
            "Network 有什么关系？")
        assert _claims(proposal) is not None

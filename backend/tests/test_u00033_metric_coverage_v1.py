"""U00033 metric-semantics coverage closure — planning_coverage layer.

Audited failure (TASK-866c5cb8c3e2): the reverse-trade-rate METRIC
deliverable (facet reverse_trade_metric_semantics) was "covered" by
analyze_related_account_set_trades — whose payload carries related-account
candidates but NO target_account_metrics — so the metric echo (the
authoritative opposite_trade_ratio) was never retrieved and the output
went UNRESOLVED → unanswerable.

Fix: for an output carrying the metric facet, related-account steps are
NOT covering and NOT eligible; the supplement appends
analyze_cross_account_trades (the only target_account_metrics source).
Pure set-internal/hedging outputs are unaffected.
"""
from types import SimpleNamespace

from app.planning_coverage import (
    _requires_target_account_metrics,
    coverage_supplement_steps,
    step_covers_output,
)
from app.goal_resolution import RequiredOutput
from app.skills import EvidenceType


def _metric_output(subject="U00033"):
    return RequiredOutput(
        output_id="O1",
        claim="反向交易率指标的含义与统计口径（自身 vs 跨账户）",
        claim_kind="explanation",
        subject_ref=subject, scope="account_level",
        evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS],
        postcondition_facets=["reverse_trade_metric_semantics"],
        satisfaction="pending")


def _hedging_output(subject="U00033"):
    return RequiredOutput(
        output_id="O2",
        claim="关联账号集合内部是否存在对冲交易候选（集合内全部账户两两配对）",
        claim_kind="retrieval",
        subject_ref=subject, scope="account_level",
        evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS],
        postcondition_facets=[],
        satisfaction="pending")


def _step(step_type):
    return SimpleNamespace(type=step_type, arguments={}, status="pending")


def test_metric_facet_detected():
    assert _requires_target_account_metrics(_metric_output())
    assert not _requires_target_account_metrics(_hedging_output())


def test_metric_output_related_steps_do_not_cover():
    for st in ("analyze_related_accounts",
               "analyze_related_account_set_trades"):
        assert not step_covers_output(_step(st), _metric_output()), st
    # the metric evidence step DOES cover
    assert step_covers_output(_step("analyze_cross_account_trades"),
                              _metric_output())


def test_metric_output_eligible_excludes_related_steps():
    from app.planning_coverage import _ordered_eligible
    eligible = _ordered_eligible(_metric_output())
    assert "analyze_cross_account_trades" in eligible
    assert "analyze_related_accounts" not in eligible
    assert "analyze_related_account_set_trades" not in eligible


def test_metric_output_supplement_appends_cross_account(tmp_path):
    contract = SimpleNamespace(outputs=[_metric_output()])
    specs = coverage_supplement_steps(contract, [])
    assert [s.step_type for s in specs] == ["analyze_cross_account_trades"]


def test_hedging_output_untouched_by_metric_constraint():
    """A pure hedging/set-internal output (no metric facet) is not
    affected by the metric exclusion — its serving steps stay eligible."""
    hedging = RequiredOutput(
        output_id="O2",
        claim="关联账号集合内部是否存在对冲交易候选（集合内全部账户两两配对）",
        claim_kind="retrieval", subject_ref="U00033",
        scope="account_level",
        evidence_requirements=[EvidenceType.OPPOSITE_TRADE_RELATIONS],
        postcondition_facets=[],
        satisfaction="pending")
    from app.planning_coverage import coverage_supplement_steps
    contract = SimpleNamespace(outputs=[hedging])
    specs = coverage_supplement_steps(contract, [])
    assert [s.step_type for s in specs] == ["analyze_cross_account_trades"]


def test_existing_records_deliveries_unaffected():
    """The generate_artifact exclusion (U00047) and the records parity
    rules are orthogonal to the metric exclusion — record-stream
    deliverables keep their own coverage rules."""
    records_out = RequiredOutput(
        output_id="O3", claim="Trade records artifact export",
        claim_kind="retrieval", subject_ref="U00047",
        scope="account_level",
        evidence_requirements=[EvidenceType.TRANSACTION_RECORDS],
        postcondition_facets=[],
        satisfaction="pending")
    from app.planning_coverage import coverage_supplement_steps
    contract = SimpleNamespace(outputs=[records_out])
    specs = coverage_supplement_steps(contract, [])
    # records acquisition step — never generate_artifact
    assert [s.step_type for s in specs] == ["inspect_transactions"]

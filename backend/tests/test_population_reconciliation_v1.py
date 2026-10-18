"""Cross-domain consistency II — metric/rule-claim population
reconciliation (generalized registry).

The reusable contract: governed summary metrics AND non-numeric rule
claims are reconciled against the record population the evidence view
actually exposes, under RP's exposed feature reference anchor:
- window-count metrics (trade/withdrawal_frequency_24h): claimed value
  vs the count of exposed rows in the 24h window before the anchor —
  verdict consistent_scoped_window / population_mismatch;
- opposite_trade_ratio: min(BUY,SELL)/total recomputed from the exposed
  rows — claim_supported / population_mismatch;
- first_withdrawal_new_address (F4's rule claim): the earliest exposed
  withdrawal must carry is_new_address=True — claim_supported /
  claim_contradicted;
- governed numerics with no registered verification are reported as
  unmapped — never silently treated as verified.
A full three-way agreement (claimed == window count == exposed total)
suppresses the block — no reconciliation noise for consistent data.
"""

from app.agent_loop import (
    _population_reconciliation_summary,
    _render_population_reconciliation,
)
from app.domain_tools.finding_drilldown import (
    _population_reconciliation_block,
)

ANCHOR = "2026-07-21T23:30:00+08:00"          # RP's global reference anchor


def _rows(ts_list, sides=None, new=None, prefix="T"):
    rows = [{"record_id": f"{prefix}{i:05d}", "timestamp": ts}
            for i, ts in enumerate(ts_list)]
    if sides:
        for r, sd in zip(rows, sides):
            r["side"] = sd
    if new is not None:
        for r, nv in zip(rows, new):
            r["is_new_address"] = nv
    return rows


def _evidence(claimed, anchor=ANCHOR, ratio=None):
    feats = {"trade_frequency_24h": claimed,
             "withdrawal_frequency_24h": claimed,
             "feature_reference_time": anchor}
    if ratio is not None:
        feats["opposite_trade_ratio"] = ratio
    return {"feature_evidence": feats}


class TestWindowCountReconciliation:
    def test_f7_shape_scoped_window_consistent(self):
        """The RP-verified F7 shape: 64 exposed rows; the metric counts
        only the 24h before the anchor (54) → consistent_scoped_window
        with the 10 outside rows accounted."""
        from datetime import datetime, timedelta
        a = datetime.fromisoformat(ANCHOR)
        inside = [(a - timedelta(minutes=20 * (i + 1))).isoformat()
                  for i in range(54)]
        outside = [(a - timedelta(hours=25 + i)).isoformat()
                   for i in range(10)]
        blk = _population_reconciliation_block(
            _evidence(54), _rows(outside + inside), "transactions")
        assert blk["verdict"] == "consistent_scoped_window"
        (m,) = blk["metrics"]
        assert m["claimed"] == 54 and m["observed_in_window"] == 54
        assert m["exposed_total"] == 64 and m["outside_window"] == 10

    def test_mismatch_surfaced(self):
        blk = _population_reconciliation_block(
            _evidence(9),
            _rows(["2026-07-21T01:00:00+00:00",
                   "2026-07-21T02:00:00+00:00",
                   "2026-07-21T03:00:00+00:00"]), "transactions")
        (m,) = blk["metrics"]
        assert m["verdict"] == "population_mismatch"
        assert blk["verdict"] == "population_mismatch"

    def test_missing_anchor_is_absence_not_consistency(self):
        assert _population_reconciliation_block(
            {"feature_evidence": {"trade_frequency_24h": 54}},
            _rows(["2026-07-21T01:00:00+00:00"]),
            "transactions") is None

    def test_full_agreement_suppresses_the_block(self):
        assert _population_reconciliation_block(
            _evidence(7),
            _rows(["2026-07-21T10:00:00+08:00"] * 7),
            "withdrawals") is None

    def test_naive_and_aware_timestamps_mixed(self):
        blk = _population_reconciliation_block(
            _evidence(2),
            _rows(["2026-07-21T14:30:00",
                   "2026-07-21T09:00:00+00:00",
                   "2026-07-20T01:00:00+00:00"]), "transactions")
        assert blk["verdict"] == "consistent_scoped_window"
        assert blk["metrics"][0]["observed_in_window"] == 2
        assert blk["metrics"][0]["exposed_total"] == 3


class TestRatioRecompute:
    def test_rp_definition_recomputed_and_supported(self):
        """22 BUY + 42 SELL → min/total = 22/64 = 0.3438 (the live RP
        value) → claim_supported with the denominator exposed."""
        rows = _rows(["2026-07-21T01:00:00+00:00"] * 64,
                     sides=["BUY"] * 22 + ["SELL"] * 42)
        blk = _population_reconciliation_block(
            _evidence(54, ratio=0.3438), rows, "transactions")
        kinds = {e["type"]: e for e in blk["metrics"]}
        r = kinds["ratio_recompute"]
        assert r["observed"] == 0.3438 and r["denominator"] == 64
        assert r["verdict"] == "claim_supported"

    def test_ratio_mismatch_surfaced(self):
        rows = _rows(["2026-07-21T01:00:00+00:00"] * 4,
                     sides=["BUY", "SELL", "SELL", "SELL"])  # 1/4 = 0.25
        blk = _population_reconciliation_block(
            _evidence(2, ratio=0.6), rows, "transactions")
        kinds = {e["type"]: e for e in blk["metrics"]}
        assert kinds["ratio_recompute"]["verdict"] == "population_mismatch"
        assert blk["verdict"] == "population_mismatch"


class TestRuleClaimVerification:
    def test_first_withdrawal_new_address_supported(self):
        """F4's rule claim: the EARLIEST exposed withdrawal carries
        is_new_address=True → claim_supported."""
        rows = _rows(["2026-07-21T01:00:00+00:00",
                      "2026-07-21T02:00:00+00:00"],
                     new=[True, False])
        blk = _population_reconciliation_block(
            _evidence(2), rows, "withdrawals",
            finding_title="First withdrawal to new address.")
        kinds = {e["type"]: e for e in blk["metrics"]}
        c = kinds["rule_claim"]
        assert c["claim"] == "first_withdrawal_new_address"
        assert c["observed"] is True
        assert c["verdict"] == "claim_supported"

    def test_first_withdrawal_claim_contradicted(self):
        """The earliest withdrawal NOT new → the finding's premise is
        contradicted by its own detail evidence (never silently
        presented as supported)."""
        rows = _rows(["2026-07-21T01:00:00+00:00",
                      "2026-07-21T02:00:00+00:00"],
                     new=[False, True])
        blk = _population_reconciliation_block(
            _evidence(2), rows, "withdrawals",
            finding_title="First withdrawal to new address.")
        kinds = {e["type"]: e for e in blk["metrics"]}
        assert kinds["rule_claim"]["verdict"] == "claim_contradicted"
        assert blk["verdict"] == "claim_contradicted"

    def test_other_finding_titles_do_not_trigger_the_claim(self):
        blk = _population_reconciliation_block(
            _evidence(1),
            _rows(["2026-07-21T01:00:00+00:00",
                   "2026-07-21T02:00:00+00:00"],
                  new=[False, True]),
            "withdrawals",
            finding_title="High withdrawal frequency.")
        types = {e["type"] for e in blk["metrics"]}
        assert "rule_claim" not in types


class TestUnmappedMetrics:
    def test_unregistered_governed_numeric_reported(self):
        """A governed numeric with no registered verification is
        reported as unmapped — new Finding types can never be silently
        treated as verified."""
        ev = {"feature_evidence": {
            "trade_frequency_24h": 2,
            "trade_frequency_7d": 2,       # NOT in the registry
            "feature_reference_time": ANCHOR}}
        blk = _population_reconciliation_block(
            ev, _rows(["2026-07-21T01:00:00+00:00",
                       "2026-07-21T02:00:00+00:00"]), "transactions")
        assert "trade_frequency_7d" in blk["unmapped_metrics"]
        assert blk["verdict"] == "unmapped_metrics_present"


class TestProjectionAndRendering:
    def _block(self):
        from datetime import datetime, timedelta
        a = datetime.fromisoformat(ANCHOR)
        inside = [(a - timedelta(minutes=20 * (i + 1))).isoformat()
                  for i in range(54)]
        outside = [(a - timedelta(hours=25 + i)).isoformat()
                   for i in range(10)]
        return _population_reconciliation_block(
            _evidence(54), _rows(outside + inside), "transactions")

    def test_projection_is_bounded_and_list_shaped(self):
        pr = _population_reconciliation_summary(self._block())
        assert set(pr) == {"reference_time", "window_start", "metrics",
                           "unmapped_metrics", "verdict"}
        (m,) = pr["metrics"]
        assert m["type"] == "window_count" and m["claimed"] == 54
        assert m["exposed_total"] == 64

    def test_scoped_window_note_renders_both_numbers(self):
        pr = _population_reconciliation_summary(self._block())
        text = "\n".join(_render_population_reconciliation(
            {"population_reconciliation": pr}))
        assert "54 笔交易/24 小时" in text and ANCHOR in text
        assert "64 笔" in text and "10 笔" in text
        assert "口径不同" in text and "均可追溯" in text

    def test_ratio_supported_note_renders_denominator(self):
        rows = _rows(["2026-07-21T01:00:00+00:00"] * 64,
                     sides=["BUY"] * 22 + ["SELL"] * 42)
        blk = _population_reconciliation_block(
            _evidence(54, ratio=0.3438), rows, "transactions")
        pr = _population_reconciliation_summary(blk)
        text = "\n".join(_render_population_reconciliation(
            {"population_reconciliation": pr}))
        assert "口径核实" in text and "0.3438" in text and "64" in text

    def test_rule_contradiction_never_passes_as_supported(self):
        blk = _population_reconciliation_block(
            _evidence(2),
            _rows(["2026-07-21T01:00:00+00:00",
                   "2026-07-21T02:00:00+00:00"], new=[False, True]),
            "withdrawals",
            finding_title="First withdrawal to new address.")
        pr = _population_reconciliation_summary(blk)
        text = "\n".join(_render_population_reconciliation(
            {"population_reconciliation": pr}))
        assert "⚠ 规则主张与明细不符" in text
        assert "不构成完整一致" in text

    def test_unmapped_note_renders(self):
        ev = {"feature_evidence": {
            "trade_frequency_24h": 2, "trade_frequency_7d": 2,
            "feature_reference_time": ANCHOR}}
        blk = _population_reconciliation_block(
            ev, _rows(["2026-07-21T01:00:00+00:00",
                       "2026-07-21T02:00:00+00:00"]), "transactions")
        pr = _population_reconciliation_summary(blk)
        text = "\n".join(_render_population_reconciliation(
            {"population_reconciliation": pr}))
        assert "trade_frequency_7d" in text
        assert "尚未与明细逐笔核对" in text

    def test_absent_block_renders_nothing(self):
        assert _render_population_reconciliation({}) == []
        assert _render_population_reconciliation(
            {"population_reconciliation": None}) == []


class TestPopulationShortfall:
    """Population-shortfall invariant: a request asserting a LARGER
    population than the authoritative (completely retrieved) one can
    never be satisfied by comparing the retrieved subset."""

    def _faceted(self):
        from app.goal_resolution import RequiredOutput
        from app.skills import EvidenceType
        return RequiredOutput(
            output_id="O1", claim="cmp", claim_kind="retrieval",
            subject_ref="F3", scope="finding_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
            postcondition_facets=["destination_address_identity"])

    def _obs(self, summary):
        return {"tool_name": "finding_drilldown", "outcome": "success",
                "for_outputs": ["O1"],
                "actual_evidence_types": ["withdrawal_records"],
                "summary": summary}

    def test_shortfall_is_unresolved_not_satisfied(self):
        from app.goal_postconditions import (
            evaluate_output, R_POPULATION_SHORTFALL)
        blk = _block(7, 7) if "_block" in dir() else None
        # 7-record block, request asserts 9
        summary = {"record_count": 7, "complete": True,
                   "record_rows": [{"record_id": f"W{i}", "timestamp": "t",
                                    "summary": "s"} for i in range(7)],
                   "destination_identity": {
                       "basis": "withdrawal.address",
                       "records_total": 7, "records_with_identity": 7,
                       "records_without_identity": 0,
                       "unique_destinations": 7,
                       "groups": [{"destination_ref": f"dest-{i}",
                                   "address": f"0x{i}",
                                   "record_ids": [f"W{i}"]}
                                  for i in range(1, 8)],
                       "normalization": "trim; exact case-sensitive"}}
        res = evaluate_output(self._faceted(), "U00010",
                              [self._obs(summary)], asserted_population=9)
        assert res.satisfaction.value == "unresolved"
        assert res.reason_code == R_POPULATION_SHORTFALL
        assert "9 笔提现" in res.detail and "仅可取得 7 笔" in res.detail
        assert "无法对全部 9 笔完成比较" in res.detail

    def test_equal_population_still_satisfied(self):
        from app.goal_postconditions import evaluate_output
        summary = {"record_count": 9, "complete": True,
                   "record_rows": [],
                   "destination_identity": {
                       "basis": "withdrawal.address",
                       "records_total": 9, "records_with_identity": 9,
                       "records_without_identity": 0,
                       "unique_destinations": 9,
                       "groups": [{"destination_ref": f"dest-{i}",
                                   "address": f"0x{i}",
                                   "record_ids": [f"W{i}"]}
                                  for i in range(1, 10)],
                       "normalization": "trim; exact case-sensitive"}}
        res = evaluate_output(self._faceted(), "U00010",
                              [self._obs(summary)], asserted_population=9)
        assert res.satisfaction.value == "satisfied"
        assert res.reason_code == "destination_comparison_complete"

    def test_subset_premise_within_authoritative_is_satisfied(self):
        """Asserted 5 within an authoritative 9 (complete retrieval):
        the full-population comparison CONTAINS the asserted subset —
        satisfied stands (reconciliation is presentation-level)."""
        summary = {"record_count": 7, "complete": True, "record_rows": [],
                   "destination_identity": {
                       "basis": "withdrawal.address",
                       "records_total": 7, "records_with_identity": 7,
                       "records_without_identity": 0,
                       "unique_destinations": 7,
                       "groups": [{"destination_ref": f"dest-{i}",
                                   "address": f"0x{i}",
                                   "record_ids": [f"W{i}"]}
                                  for i in range(1, 8)],
                       "normalization": "trim; exact case-sensitive"}}
        from app.goal_postconditions import evaluate_output
        res = evaluate_output(self._faceted(), "U00010",
                              [self._obs(summary)], asserted_population=5)
        assert res.satisfaction.value == "satisfied"

    def test_no_assertion_unchanged(self):
        summary = {"record_count": 7, "complete": True, "record_rows": [],
                   "destination_identity": {
                       "basis": "withdrawal.address",
                       "records_total": 7, "records_with_identity": 7,
                       "records_without_identity": 0,
                       "unique_destinations": 1,
                       "groups": [{"destination_ref": "dest-1",
                                   "address": "0x0",
                                   "record_ids": ["W1", "W2", "W3",
                                                  "W4", "W5", "W6",
                                                  "W7"]}],
                       "normalization": "trim; exact case-sensitive"}}
        from app.goal_postconditions import evaluate_output
        res = evaluate_output(self._faceted(), "U00010",
                              [self._obs(summary)])
        assert res.satisfaction.value == "satisfied"


class TestNewAddressFacet:
    """F4-class deliverable: per-record is_new_address status over the
    complete withdrawal population (Consistency II closure)."""

    def _faceted(self):
        from app.goal_resolution import RequiredOutput
        from app.skills import EvidenceType
        return RequiredOutput(
            output_id="O1", claim="which_withdrawals_new_address",
            claim_kind="retrieval",
            subject_ref="F3", scope="finding_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
            postcondition_facets=["withdrawal_new_address_status"])

    def _obs(self, block):
        return {"tool_name": "finding_drilldown", "outcome": "success",
                "for_outputs": ["O1"],
                "actual_evidence_types": ["withdrawal_records"],
                "summary": {
                    "record_count": block.get("records_total", 0),
                    "new_address_status": block}}

    def _block(self, true_n, false_n, missing_n=0):
        rows = []
        i = 0
        for _ in range(true_n):
            rows.append({"record_id": f"T{i:05d}", "is_new_address": True})
            i += 1
        for _ in range(false_n):
            rows.append({"record_id": f"T{i:05d}",
                         "is_new_address": False})
            i += 1
        for _ in range(missing_n):
            rows.append({"record_id": f"T{i:05d}",
                         "is_new_address": None})
            i += 1
        return {"basis": "withdrawal.is_new_address",
                "records_total": true_n + false_n + missing_n,
                "true_count": true_n, "false_count": false_n,
                "missing_count": missing_n, "records": rows}

    def test_all_true_satisfied(self):
        from app.goal_postconditions import evaluate_output
        res = evaluate_output(self._faceted(), "U00010",
                              [self._obs(self._block(7, 0))])
        assert res.satisfaction.value == "satisfied"
        assert res.reason_code == "new_address_status_complete"
        assert "全部 7 笔提现均被" in res.detail

    def test_mixed_statuses_satisfied_with_breakdown(self):
        from app.goal_postconditions import evaluate_output
        res = evaluate_output(self._faceted(), "U00010",
                              [self._obs(self._block(5, 2))])
        assert res.satisfaction.value == "satisfied"
        assert "5 笔被标记为新地址、2 笔不是" in res.detail

    def test_missing_values_partial_never_true(self):
        from app.goal_postconditions import evaluate_output
        res = evaluate_output(self._faceted(), "U00010",
                              [self._obs(self._block(5, 0, missing_n=2))])
        assert res.satisfaction.value == "partially_supported"
        assert "无法核实" in res.detail

    def test_absent_block_not_satisfied(self):
        """Absent new_address_status block: the new-address facet can
        NEVER be satisfied by record retrieval alone (the same
        anti-false-completion rule as the destination facet) →
        UNRESOLVED comparison_facts_absent."""
        from app.goal_postconditions import (
            evaluate_output, R_COMPARISON_FACTS_ABSENT)
        summary = {"record_count": 7, "complete": True, "record_rows": []}
        obs = self._obs(summary)
        res = evaluate_output(self._faceted(), "U00010", [obs])
        assert res.satisfaction.value == "unresolved"
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT

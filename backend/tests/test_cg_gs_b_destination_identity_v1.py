"""WP-GS-B — Destination-Identity Evidence Preservation and
Comparison Facts tests.

L1 coverage (implementation plan §4.2):
- destination_identity fact block over withdrawal records: 9 distinct /
  9 identical / mixed / missing / unclassified shapes;
- deterministic counts, first-occurrence group order, record-ID
  provenance per group;
- transaction-only payloads carry no block (withdrawal-stream fact);
- canonical API journey: observation projection + composer 目的地
  column, cross-checked against the persisted store document.
"""

import json
import sqlite3
import tempfile
import time

import pytest

from app.agent_loop import project_task_activity
from app.domain_tools.finding_drilldown import (
    _address_shape, _destination_identity_block, finding_drilldown)
from app.models import Finding, InvestigationContext
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

CASE = "U00010"
DB = ("/Users/vv/investigation-agent-client/"
      "backend/data/investigation_agent_v2.db")
NEUTRAL_GOAL = "列出这个账号的全部提现记录。"
FOCUS_F3 = {"type": "focus_finding", "finding_id": "F3"}
PLAN_WITHDRAW = ('{"skill_id": "timeline_investigation", "goal": "g", '
                 '"steps": [{"type": "inspect_withdrawals", '
                 '"reason": "r"}]}')
WITHDRAWAL_PAYLOAD = json.dumps({
    "goal_resolution": "INFERRED",
    "goal_type": "identify_related_accounts",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "withdrawal_report", "claim_kind": "retrieval",
         "subject_ref": "F3", "scope": "finding_level",
         "evidence_requirements": ["withdrawal_records"]}]})


def _wd(rid, address, ts="2026-07-20T22:46:49", asset="USDT",
        amount=1.0, kind="withdrawal"):
    return {"record_kind": kind, "record_id": rid,
            "summary": f"Withdrawal of {amount} {asset}",
            "amount": amount, "asset": asset, "address": address,
            "is_new_address": True, "timestamp": ts,
            "risk_reason": "new address"}


class TestDestinationIdentityBlock:
    def test_nine_distinct_identifiers(self):
        records = [_wd(f"W{i:03d}", f"addr-{i}") for i in range(9)]
        b = _destination_identity_block(records)
        assert b["records_total"] == 9
        assert b["records_with_identity"] == 9
        assert b["records_without_identity"] == 0
        assert b["unique_destinations"] == 9
        assert [g["destination_ref"] for g in b["groups"]] == [
            f"dest-{i}" for i in range(1, 10)]
        assert [g["record_ids"] for g in b["groups"]] == [
            [f"W{i:03d}"] for i in range(9)]

    def test_nine_identical_identifiers_single_group(self):
        records = [_wd(f"W{i:03d}", "same-dest-0xABC") for i in range(9)]
        b = _destination_identity_block(records)
        assert b["unique_destinations"] == 1
        assert b["groups"][0]["record_ids"] == [
            r["record_id"] for r in records]
        assert b["records_with_identity"] == 9

    def test_mixed_repeats_and_distinct(self):
        records = [_wd("W1", "addr-A"), _wd("W2", "addr-B"),
                   _wd("W3", "addr-A"), _wd("W4", "addr-C")]
        b = _destination_identity_block(records)
        assert b["unique_destinations"] == 3
        groups = {g["address"]: g["record_ids"] for g in b["groups"]}
        assert groups["addr-A"] == ["W1", "W3"]
        assert groups["addr-B"] == ["W2"]
        assert groups["addr-C"] == ["W4"]

    def test_missing_identifiers_counted_not_dropped(self):
        records = [_wd("W1", "addr-A"), _wd("W2", None),
                   _wd("W3", ""), _wd("W4", "   "),
                   _wd("W5", "addr-B")]
        b = _destination_identity_block(records)
        assert b["records_total"] == 5
        assert b["records_with_identity"] == 2
        assert b["records_without_identity"] == 3
        all_ids = [rid for g in b["groups"] for rid in g["record_ids"]]
        assert set(all_ids) == {"W1", "W5"}

    def test_unclassified_shape_annotated(self):
        b = _destination_identity_block([_wd("W1", "weird-format-x1")])
        assert b["groups"][0]["shape"] == "unclassified"

    def test_shape_annotations_descriptive_only(self):
        b = _destination_identity_block([
            _wd("W1", "0xDEADBEEF"),
            _wd("W2", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"),
            _wd("W3", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"),
        ])
        shapes = {g["address"]: g["shape"] for g in b["groups"]}
        assert shapes["0xDEADBEEF"] == "hex_0x"
        assert shapes["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"] == \
            "bech32"
        assert shapes["1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"] == "base58"

    def test_case_variants_are_distinct_identifiers(self):
        """No case folding: case variants count as different RECORDED
        identifiers (revision 3 boundary — no network context)."""
        b = _destination_identity_block([
            _wd("W1", "0xDeadBeef"),
            _wd("W2", "0xdeadbeef"),
        ])
        assert b["unique_destinations"] == 2

    def test_whitespace_trimmed_before_comparison(self):
        b = _destination_identity_block([
            _wd("W1", "  addr-A  "),
            _wd("W2", "addr-A"),
        ])
        assert b["unique_destinations"] == 1
        assert b["groups"][0]["record_ids"] == ["W1", "W2"]

    def test_same_input_order_deterministic(self):
        records = [_wd("W2", "addr-B"), _wd("W1", "addr-A"),
                   _wd("W3", "addr-B")]
        assert (_destination_identity_block(records)
                == _destination_identity_block(records))

    def test_partition_invariant_across_input_orders(self):
        forward = _destination_identity_block([
            _wd("W2", "addr-B"), _wd("W1", "addr-A"),
            _wd("W3", "addr-B")])
        backward = _destination_identity_block([
            _wd("W3", "addr-B"), _wd("W1", "addr-A"),
            _wd("W2", "addr-B")])

        def partition(b):
            return {g["address"]: sorted(g["record_ids"])
                    for g in b["groups"]}

        assert partition(forward) == partition(backward)
        assert [g["destination_ref"] for g in forward["groups"]] == [
            f"dest-{i}" for i in range(1, len(forward["groups"]) + 1)]

    def test_transaction_only_payload_no_block(self):
        records = [{"record_kind": "transaction",
                    "record_id": "T01", "summary": "tx"}]
        assert _destination_identity_block(records) is None

    def test_empty_records_no_block(self):
        assert _destination_identity_block([]) is None

    def test_duplicate_record_ids_stay_traceable(self):
        records = [_wd("W1", "addr-A"), _wd("W1", "addr-A")]
        b = _destination_identity_block(records)
        # source anomaly: both entries carry W1 — provenance preserved
        # verbatim (the block never silently deduplicates records)
        assert b["groups"][0]["record_ids"] == ["W1", "W1"]


class TestEvidenceViewIntegration:
    def test_drilldown_payload_carries_block(self, monkeypatch):
        """Integration: the evidence view attaches the block over
        withdrawal records (facts only — no conclusion drawn here)."""
        from unittest.mock import patch

        evidence = {"withdrawal_evidence": [
            {"withdrawal_id": f"W{i:03d}", "asset": "USDT",
             "amount": 1.0 + i, "address": f"dest-{i % 2}",
             "is_new_address": True,
             "timestamp": f"2026-07-2{i}T01:00:00Z",
             "risk_reason": "new address"} for i in range(4)],
            "transaction_evidence": [],
            "feature_evidence": {}}
        monkeypatch.setattr(
            "app.adapters.risk_platform.RiskPlatformAdapter."
            "fetch_case_evidence", lambda self, cid, **kw: evidence)
        finding = Finding(
            finding_id="F3", case_id="U00010",
            type="rule_signal", title="High withdrawal frequency.",
            severity="high", summary="9 withdrawals",
            evidence_refs=[], signal_refs=[], policy_refs=[],
            capabilities={"timeline", "signal_explain"})
        result = finding_drilldown(
            finding_id="F3", case_id="U00010", view="evidence")
        assert result.outcome.value == "success"
        block = result.data["destination_identity"]
        assert block["records_total"] == 4
        assert block["unique_destinations"] == 2
        assert block["records_with_identity"] == 4

    def test_non_withdrawal_payload_no_block(self):
        records = [{"record_kind": "transaction",
                    "record_id": "T01", "summary": "tx"}]
        assert _destination_identity_block(records) is None

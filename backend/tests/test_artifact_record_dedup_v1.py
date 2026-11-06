"""artifact_bundle — cross-call record aggregation & set consistency.

2026-11-03 final-acceptance incident (ART-01f4b21df3, U00033): a RE_PLAN
re-ran the SAME inspect_transactions step (new call id, identical
69-record set) before the file was composed. Per-call rendering wrote
the complete set TWICE (138 rows for 69 records) while the per-call
parity check (69==69 per call) could not see it, and the served answer
claimed "完整记录 69 条".

Pins:
- FULLY duplicated calls → each (record_kind, record_id) renders ONCE;
  record_set_consistent=True (identical sets); parity not failed;
- PARTIALLY overlapping calls → union rendered once each;
  record_set_consistent=False + explicit gap: completeness could NOT be
  verified (never silently claimed);
- single call → byte-identical content across runs, one section, every
  record present (existing behavior unchanged);
- different findings across calls → separate sections, consistency not
  applicable (None) — no false cross-finding comparison.
"""

from app.domain_tools.artifact_bundle import artifact_bundle
from app.models import ToolCallStatusV2, ToolCallV2, ToolResult, \
    ToolResultOutcome


def _records(prefix, n, with_address=True):
    return [
        {"record_kind": "transaction", "record_id": f"{prefix}{i:03d}",
         "summary": f"BUY trade of sample asset #{i}",
         "timestamp": f"2026-07-20T23:{i:02d}:00Z",
         **({"address": f"ADDR{i}"} if with_address else {})}
        for i in range(1, n + 1)
    ]


def _evidence_call(call_id, records, finding_id="F1"):
    return ToolCallV2(
        tool_call_id=call_id, investigation_id="INV-x", task_id="T-1",
        tool_name="finding_drilldown",
        arguments={"view": "evidence", "stream": "transactions",
                   "finding_id": finding_id, "case_id": "U00033"},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": finding_id, "view": "evidence",
                  "complete": True, "record_count": len(records),
                  "records": records,
                  "rows_truncated": False}),
        started_at="2026-11-03T00:00:00Z",
        completed_at="2026-11-03T00:00:01Z",
    )


def _bundle(calls):
    return artifact_bundle(
        scope="case", case_id="U00033", task_id="T-1",
        source_tool_calls=calls,
        source_step_ids=["S1"],
        source_streams=["transaction_records"])


def _rows(content):
    return [ln for ln in content.splitlines()
            if ln.startswith("| transaction:")]


def test_fully_duplicated_calls_render_each_record_once():
    recs = _records("T00", 4)
    r = _bundle([_evidence_call("TC-S1-first", recs),
                 _evidence_call("TC-S1-repeat", list(recs))])
    assert r.outcome == ToolResultOutcome.SUCCESS
    content = r.data["artifact"]["content"]
    rows = _rows(content)
    assert len(rows) == 4, f"expected 4 rows (dedup), got {len(rows)}"
    assert content.count("### Evidence for F1") == 1
    ids = [row.split("|")[1].strip() for row in rows]
    assert len(set(ids)) == 4
    # provenance preserved: BOTH calls are listed as sources
    assert r.data["source_tool_call_count"] == 2
    # identical sets → consistency verified
    assert r.data["record_set_consistent"] is True
    assert r.data["record_parity_checked"] is True
    assert r.data["record_parity_failed"] is False


def test_partially_overlapping_calls_report_unverifiable_completeness():
    a = _records("T00", 3)                       # r1..r3
    b = _records("T00", 4)[1:]                   # r2..r4 (overlap r2,r3)
    r = _bundle([_evidence_call("TC-S1-a", a),
                 _evidence_call("TC-S1-b", b)])
    content = r.data["artifact"]["content"]
    rows = _rows(content)
    ids = [row.split("|")[1].strip() for row in rows]
    assert len(rows) == 4 and len(set(ids)) == 4     # union, once each
    # the two calls returned DIFFERENT sets → completeness not provable
    assert r.data["record_set_consistent"] is False
    gaps = r.data.get("next_data_needed") or []
    assert any("could not be verified" in g for g in gaps), gaps


def test_single_call_content_unchanged_and_deterministic():
    recs = _records("T00", 3)
    r1 = _bundle([_evidence_call("TC-S1-only", recs)])
    r2 = _bundle([_evidence_call("TC-S1-only", recs)])
    c1 = r1.data["artifact"]["content"]
    c2 = r2.data["artifact"]["content"]
    assert c1 == c2
    assert len(_rows(c1)) == 3
    assert c1.count("### Evidence for F1") == 1
    assert "ADDR1" in c1 and "ADDR3" in c1     # fields preserved verbatim
    assert r1.data["record_set_consistent"] is None   # single call: N/A


def test_different_findings_render_separate_sections():
    a = _records("T10", 2)
    b = _records("T20", 2)
    r = _bundle([_evidence_call("TC-S1-f1", a, finding_id="F1"),
                 _evidence_call("TC-S1-f2", b, finding_id="F3")])
    content = r.data["artifact"]["content"]
    assert content.count("### Evidence for F1") == 1
    assert content.count("### Evidence for F3") == 1
    assert len(_rows(content)) == 4
    # per-finding sections are not cross-compared
    assert r.data["record_set_consistent"] is None

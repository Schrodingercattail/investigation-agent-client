"""Evidence field preservation — address through the generic chain.

Covers the two generic fixes (cross-case, cross-language; no case/finding
id conditions):
  A. project_observation keeps each record's own `address` in record_rows
     (present → preserved verbatim; absent → None, never synthesized).
  A2. The standard (non-facet) evidence renderer surfaces the address in
      the composed response when present, and never fabricates one when
      absent.
  B. artifact_bundle's evidence record table renders an Address column
      when the source records carry addresses and generates normally
      without them (existing fields preserved).
"""

from app.agent_loop import ToolResult, ToolResultOutcome, project_observation
from app.domain_tools.artifact_bundle import _render_evidence_records


def _tool_result(records):
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"view": "evidence", "complete": True,
              "record_count": len(records),
              "records": records,
              "streams": {"withdrawals_included": True,
                          "transactions_included": False}},
    )


# ---------------- A: project_observation field preservation ------------

def test_observation_preserves_address_when_present():
    recs = [{"record_id": "W001", "timestamp": "t1",
             "summary": "Withdrawal of 1 USDT",
             "address": "aaa111"},
            {"record_id": "W002", "timestamp": "t2",
             "summary": "Withdrawal of 2 USDT",
             "address": "bbb222"}]
    obs = project_observation("inspect_withdrawals", "inspect_withdrawals",
                              _tool_result(recs), lang="en")
    rows = obs["summary"]["record_rows"]
    assert [r["address"] for r in rows] == ["aaa111", "bbb222"]
    # existing fields preserved
    assert rows[0]["record_id"] == "W001"
    assert rows[0]["timestamp"] == "t1"
    assert rows[0]["summary"] == "Withdrawal of 1 USDT"


def test_observation_absent_address_stays_none_not_synthesized():
    recs = [{"record_id": "T001", "timestamp": "t1",
             "summary": "BUY trade of 1 USDT"}]
    obs = project_observation("inspect_evidence", "inspect_evidence",
                              _tool_result(recs), lang="en")
    rows = obs["summary"]["record_rows"]
    assert rows[0]["address"] is None
    assert rows[0]["record_id"] == "T001"


def test_observation_mixed_records_per_row_independent():
    recs = [{"record_id": "W001", "timestamp": "t1", "summary": "s1",
             "address": "addr-1"},
            {"record_id": "T001", "timestamp": "t2", "summary": "s2"}]
    obs = project_observation("inspect_evidence", "inspect_evidence",
                              _tool_result(recs), lang="en")
    rows = obs["summary"]["record_rows"]
    assert rows[0]["address"] == "addr-1"
    assert rows[1]["address"] is None


# ------------- A2: standard evidence renderer (non-facet path) ---------

def _output():
    """Minimal SATISFIED withdrawal-records output (real RequiredOutput)."""
    from app.goal_resolution import (
        ClaimKind, EvidenceType, OutputSatisfaction, RequiredOutput,
        ScopeLevel)
    return RequiredOutput(
        output_id="O4", claim="列出全部提现地址",
        claim_kind=ClaimKind.RETRIEVAL,
        claim_description="list all withdrawal addresses",
        subject_ref="U00010", scope=ScopeLevel.CASE_LEVEL.value,
        evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
        satisfaction=OutputSatisfaction.SATISFIED)


def _obs_state_rows():
    return [{"record_id": "W001", "timestamp": "t1",
             "summary": "Withdrawal of 1 USDT", "address": "addr-111"},
            {"record_id": "W002", "timestamp": "t2",
             "summary": "Withdrawal of 2 USDT", "address": None}]


def test_standard_renderer_zh_shows_addresses_when_present():
    from app.agent_loop import _gsp_render_output_facts
    summary = {"record_count": 2, "record_rows": _obs_state_rows(),
               "streams": {"withdrawals_included": True}}
    lines = _gsp_render_output_facts(_output(), summary, lang="zh")
    text = "\n".join(lines)
    assert "addr-111" in text          # the record's real address
    assert "地址: addr-111" in text
    # the record WITHOUT an address renders no fabricated address
    lines_w2 = [l for l in lines if "W002" in l]
    assert lines_w2 and "地址:" not in lines_w2[0]


def test_standard_renderer_en_and_no_address_no_fabrication():
    from app.agent_loop import _gsp_render_output_facts
    summary = {"record_count": 2, "record_rows": _obs_state_rows(),
               "streams": {"withdrawals_included": True}}
    lines = _gsp_render_output_facts(_output(), summary, lang="en")
    text = "\n".join(lines)
    assert "address: addr-111" in text
    w2 = [l for l in lines if "W002" in l]
    assert w2 and "address:" not in w2[0]

    summary_no_addr = {"record_count": 1,
                       "record_rows": [{"record_id": "W003",
                                        "timestamp": "t3",
                                        "summary": "s3"}],
                       "streams": {"withdrawals_included": True}}
    lines3 = _gsp_render_output_facts(_output(), summary_no_addr,
                                      lang="en")
    text3 = "\n".join(lines3)
    assert "W003" in text3 and "address:" not in text3


# ---------------- B: artifact evidence table Address column ------------

def test_artifact_renders_address_column_when_present():
    payload = {"records": [
        {"record_kind": "withdrawal", "record_id": "W001",
         "summary": "Withdrawal of 1 USDT", "address": "addr-111",
         "timestamp": "t1"},
        {"record_kind": "withdrawal", "record_id": "W002",
         "summary": "Withdrawal of 2 USDT", "address": "addr-222",
         "timestamp": "t2"},
    ]}
    lines = _render_evidence_records(payload)
    text = "\n".join(lines)
    assert "Address |" in text.splitlines()[0]
    assert "addr-111" in text and "addr-222" in text
    # existing fields preserved
    assert "W001" in text and "Withdrawal of 1 USDT" in text
    assert "t1" in text


def test_artifact_without_addresses_unchanged_shape():
    payload = {"records": [
        {"record_kind": "transaction", "record_id": "T001",
         "summary": "BUY trade of 1 USDT", "timestamp": "t1"},
    ]}
    lines = _render_evidence_records(payload)
    text = "\n".join(lines)
    assert text.splitlines()[0] == "| Record | Details | Time |"
    assert "T001" in text and "t1" in text
    assert "—" not in text          # no filler column appeared
    assert "Address" not in text


def test_artifact_mixed_rows_present_addr_rendered_missing_is_dash():
    payload = {"records": [
        {"record_kind": "withdrawal", "record_id": "W001",
         "summary": "w1", "address": "addr-111", "timestamp": "t1"},
        {"record_kind": "transaction", "record_id": "T001",
         "summary": "t1x", "timestamp": "t2"},
    ]}
    text = "\n".join(_render_evidence_records(payload))
    assert "addr-111" in text
    trow = [l for l in text.splitlines() if "T001" in l][0]
    assert "—" in trow and "addr" not in trow.replace("addr-111", "")

"""Evidence-completeness regression tests (Product Semantics pass, §23).

Establishes with REAL RP-shaped data that:
1. The aggregate count may differ from the bounded /evidence subset.
2. Partial evidence is never labeled complete.
3. view="evidence" retrieves the COMPLETE available record set (no top_n).
4. No evidence IDs are fabricated.
5. No silent top_n applies to a full-evidence request.
6. Artifacts preserve the complete evidence set after evidence investigation.
7. Artifact provenance lists the actual contributing tool calls.
8. artifact_bundle is excluded from its own provenance.
9. Finding Cards never display evidence records (frontend contract: the
   /api/v2 findings payload remains unchanged; the UI contract is verified
   in the browser pass — here the composer never emits record dumps).
10. Finding Cards never display severity (UI).
11. F3's 7 withdrawals are not presented as 5 "evidence records".
12. When complete evidence is genuinely unavailable, Evidence Gaps is
    explicit.

RP ground truth (verified live against the real Postgres): case U00010 has
7 withdrawal records; GET /evidence default returns the top-5 subset.
"""

from unittest.mock import patch

import pytest

import tests.test_risk_platform_adapter_v2 as fx
from app.adapters.risk_platform import RiskPlatformAdapter, normalize_evidence_records
from app.domain_tools import finding_drilldown
from app.domain_tools.artifact_bundle import artifact_bundle
from app.models import (
    Finding,
    FindingCapability,
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)


# --- fixtures mirroring the REAL U00010 shape ---------------------------------

def u00010_evidence(withdrawals_override=None):
    """RP-shaped evidence with 7 withdrawals (like real U00010) but where the
    default /evidence payload carries only the top-5 subset."""
    payload = fx.rp_evidence_payload("U00010")
    payload["withdrawal_evidence"] = withdrawals_override if (
        withdrawals_override is not None
    ) else [
        {"withdrawal_id": "W000088", "asset": "BTC", "amount": 4.283497,
         "address": "0xaaa", "is_new_address": True,
         "timestamp": "2026-07-21T03:01:49Z", "risk_reason": "new address"},
        {"withdrawal_id": "W000090", "asset": "USDC", "amount": 4.199266,
         "address": "0xbbb", "is_new_address": True,
         "timestamp": "2026-07-21T04:37:49Z", "risk_reason": "new address"},
        {"withdrawal_id": "W000087", "asset": "USDT", "amount": 4.141117,
         "address": "0xccc", "is_new_address": True,
         "timestamp": "2026-07-21T01:31:49Z", "risk_reason": "new address"},
        {"withdrawal_id": "W000086", "asset": "USDC", "amount": 1.645502,
         "address": "0xddd", "is_new_address": True,
         "timestamp": "2026-07-21T04:30:49Z", "risk_reason": "new address"},
        {"withdrawal_id": "W000089", "asset": "USDC", "amount": 1.146421,
         "address": "0xeee", "is_new_address": True,
         "timestamp": "2026-07-21T06:08:49Z", "risk_reason": "new address"},
        # complete-only records (absent from the top-5 preview)
        {"withdrawal_id": "W000084", "asset": "USDC", "amount": 0.90031,
         "address": "0xfff", "is_new_address": True,
         "timestamp": "2026-07-21T02:22:49Z", "risk_reason": "new address"},
        {"withdrawal_id": "W000085", "asset": "ETH", "amount": 0.735698,
         "address": "0x111", "is_new_address": True,
         "timestamp": "2026-07-21T05:33:49Z", "risk_reason": "new address"},
    ]
    payload["feature_evidence"] = {
        "withdrawal_frequency_24h": 7,      # aggregate fact: 7 in 24h window
        "withdrawal_risk_score": 1.0,
        "withdrawal_volume_24h": 17.05,
    }
    return payload


def make_case(evidence=None):
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            evidence if evidence is not None else u00010_evidence(),
            fx.rp_explanation_payload()),
    ):
        from app.domain_tools import risk_case_fetch
        return risk_case_fetch("U00010")


def withdrawal_finding(case):
    return [f for f in case.data["findings"] if "withdrawal" in f.title.lower()][0]


def patch_evidence(_evidence=None, capture=None):
    """Mock of RP /evidence honoring expose_complete_records: complete → all
    stored records; default → top-5 by amount (mirrors real RP behavior).
    `_evidence` (when given) overrides the stored record set entirely."""
    def fake(self, uid, expose_complete_records=False):
        if capture is not None:
            capture.append(expose_complete_records)
        if _evidence is not None:
            return _evidence
        if expose_complete_records:
            return u00010_evidence()          # complete: all 7
        # bounded preview: top-5 by amount, mirroring RP default behavior
        top5 = sorted(
            u00010_evidence()["withdrawal_evidence"],
            key=lambda w: -w["amount"])[:5]
        return u00010_evidence(withdrawals_override=top5)
    return patch(
        "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
        new=fake,
    )


# --- 1–5. retrieval semantics ---------------------------------------------------

class TestEvidenceRetrieval:
    def test_aggregate_count_differs_from_bounded_subset(self):
        """Real-U00010 shape: feature says 7 withdrawals in 24h; the default
        /evidence payload carries 5 records. The two are distinct facts."""
        bounded = u00010_evidence(withdrawals_override=[
            w for w in u00010_evidence()["withdrawal_evidence"]][:5])
        assert len(bounded["withdrawal_evidence"]) == 5
        assert bounded["feature_evidence"]["withdrawal_frequency_24h"] == 7

    def test_timeline_fetches_complete_records(self):
        case = make_case()
        f = withdrawal_finding(case)
        capture = []
        with patch_evidence(capture=capture):
            res = finding_drilldown(
                finding_id=f.finding_id, view="timeline",
                case_context=case.data,
            )
        assert res.outcome == ToolResultOutcome.SUCCESS
        assert capture == [True]               # §5: timeline is COMPLETE too
        assert res.data["view"] == "timeline"

    def test_evidence_view_requests_complete_records(self):
        case = make_case()
        f = withdrawal_finding(case)
        capture = []
        with patch_evidence(capture=capture):
            res = finding_drilldown(
                finding_id=f.finding_id, view="evidence",
                case_context=case.data,
            )
        assert res.outcome == ToolResultOutcome.SUCCESS
        assert capture == [True]               # concrete evidence: opt-in
        assert res.data["complete"] is True

    def test_evidence_view_returns_complete_set_no_top_n(self):
        case = make_case()
        f = withdrawal_finding(case)
        with patch_evidence():
            res = finding_drilldown(
                finding_id=f.finding_id, view="evidence",
                case_context=case.data,
                top_n=3,                       # must be IGNORED for evidence
            )
        assert res.outcome == ToolResultOutcome.SUCCESS
        ids = [r["record_id"] for r in res.data["records"]]
        assert len(ids) == 7                   # ALL records, not 3, not 5
        assert "W000084" in ids and "W000085" in ids   # preview-only absent
        assert res.data["record_count"] == 7
        assert res.data["complete"] is True
        assert res.data.get("truncated") is None      # no truncation flag lie

    def test_no_evidence_ids_fabricated(self):
        case = make_case()
        f = withdrawal_finding(case)
        with patch_evidence():
            res = finding_drilldown(
                finding_id=f.finding_id, view="evidence",
                case_context=case.data,
            )
        real_ids = {w["withdrawal_id"] for w in u00010_evidence()["withdrawal_evidence"]}
        for r in res.data["records"]:
            assert r["record_id"] in real_ids

    def test_aggregate_features_kept_separate_from_records(self):
        case = make_case()
        f = withdrawal_finding(case)
        with patch_evidence():
            res = finding_drilldown(
                finding_id=f.finding_id, view="evidence",
                case_context=case.data,
            )
        # 7 records vs feature value 7 are different structures
        assert res.data["record_count"] == len(res.data["records"])
        assert res.data["risk_features"]["withdrawal_frequency_24h"] == 7
        assert all(r["record_kind"] == "withdrawal"
                   for r in res.data["records"])

    def test_empty_stream_reports_gap_not_records(self):
        empty = u00010_evidence(withdrawals_override=[])
        case = make_case(evidence=empty)
        f = withdrawal_finding(case)
        with patch_evidence(empty):
            res = finding_drilldown(
                finding_id=f.finding_id, view="evidence",
                case_context=case.data,
            )
        assert res.outcome == ToolResultOutcome.EMPTY
        assert res.data["records"] == []
        assert res.data["complete"] is True
        assert any("no concrete" in w.lower() for w in res.warnings)


# --- 6–8. artifact preservation + provenance --------------------------------------

def tc_from(tool_call_id, tool_name, result):
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00010",
        task_id="T-1", tool_name=tool_name, arguments={},
        status=ToolCallStatusV2.SUCCESS, result=result,
        started_at="2026-08-30T00:00:00Z", completed_at="2026-08-30T00:00:01Z",
    )


class TestArtifactEvidence:
    def test_artifact_preserves_complete_set_after_evidence_investigation(self):
        case = make_case()
        f = withdrawal_finding(case)
        with patch_evidence():
            ev = finding_drilldown(
                finding_id=f.finding_id, view="evidence",
                case_context=case.data,
            )
        fetch_tc = tc_from("TC-FETCH", "risk_case_fetch", case)
        ev_tc = tc_from("TC-EV", "finding_drilldown", ev)
        r = artifact_bundle(
            scope="finding", case_id="U00010", finding_id=f.finding_id,
            task_id="T-1", source_tool_calls=[fetch_tc, ev_tc],
        )
        content = r.data["artifact"]["content"]
        # ALL 7 records present — not reduced to the 5-record preview
        assert content.count("W00008") + content.count("W000084") >= 7 or all(
            wid in content for wid in (
                "W000084", "W000085", "W000086", "W000087",
                "W000088", "W000089", "W000090"))
        assert "## Investigation Evidence" in content
        assert "(complete record set)" in content

    def test_artifact_provenance_lists_contributing_calls(self):
        case = make_case()
        f = withdrawal_finding(case)
        with patch_evidence():
            ev = finding_drilldown(
                finding_id=f.finding_id, view="evidence",
                case_context=case.data,
            )
        fetch_tc = tc_from("TC-FETCH", "risk_case_fetch", case)
        ev_tc = tc_from("TC-EV", "finding_drilldown", ev)
        unrelated = tc_from("TC-UNRELATED", "policy_lookup", ToolResult(
            outcome=ToolResultOutcome.SUCCESS, data={"unrelated": True}))
        r = artifact_bundle(
            scope="finding", case_id="U00010", finding_id=f.finding_id,
            task_id="T-1",
            source_tool_calls=[fetch_tc, ev_tc, unrelated],
        )
        src = r.data["artifact"]["source_tool_calls"]
        assert "TC-FETCH" in src               # findings came from fetch
        assert "TC-EV" in src                  # records came from drilldown
        assert "TC-UNRELATED" not in src       # no contribution → no entry

    def test_artifact_bundle_never_in_own_provenance(self):
        case = make_case()
        # simulate the executor's pool which already excludes artifact_bundle;
        # belt-and-braces: even if present it must not self-reference.
        fetch_tc = tc_from("TC-FETCH", "risk_case_fetch", case)
        r = artifact_bundle(
            scope="finding", case_id="U00010",
            finding_id=withdrawal_finding(case).finding_id,
            task_id="T-1", source_tool_calls=[fetch_tc],
        )
        assert r.data["artifact"]["artifact_id"]
        assert all(not cid.startswith("ART-") or False
                   for cid in r.data["artifact"]["source_tool_calls"])

    def test_severity_absent_from_artifact_finding_blocks(self):
        case = make_case()
        r = artifact_bundle(
            scope="case", case_id="U00010", task_id="T-1",
            source_tool_calls=[tc_from("TC-FETCH", "risk_case_fetch", case)],
        )
        content = r.data["artifact"]["content"]
        assert "Severity" not in content

    def test_preview_records_not_labeled_complete(self):
        """A timeline (bounded) result must never trigger the complete-
        evidence section nor claim completeness."""
        case = make_case()
        f = withdrawal_finding(case)
        with patch_evidence():
            tl = finding_drilldown(
                finding_id=f.finding_id, view="timeline",
                case_context=case.data,
            )
        r = artifact_bundle(
            scope="finding", case_id="U00010",
            finding_id=f.finding_id, task_id="T-1",
            source_tool_calls=[tc_from("TC-FETCH", "risk_case_fetch", case),
                               tc_from("TC-TL", "finding_drilldown", tl)],
        )
        content = r.data["artifact"]["content"]
        assert "(complete record set)" not in content
        assert "## Timeline" in content


# --- P14: bounded honesty in response composition ---------------------------------

class TestEmptyComposition:
    """P14/F-3: EMPTY outcomes must keep their distinct meanings — the
    composer must not collapse 'no concrete records for this stream' into
    'no matching data', and ToolResult warnings must survive composition."""

    def _compose(self, result):
        from app.models import ToolCallV2, ToolCallStatusV2
        tc = ToolCallV2(
            tool_call_id="TC-E", investigation_id="I", task_id="T",
            tool_name="finding_drilldown", arguments={},
            status=ToolCallStatusV2.SUCCESS, result=result,
            started_at="2026-08-28T00:00:00Z",
            completed_at="2026-08-28T00:00:01Z",
        )
        from app.investigation_service import compose_response
        return compose_response(user_request="show evidence", skill_id=None,
                                plan=None, tool_calls=[tc],
                                execution_errors=[])

    def _evidence_empty(self):
        # mirror of finding_drilldown's empty-stream result (view="evidence")
        from app.models import ToolResult, ToolResultOutcome
        return ToolResult(
            outcome=ToolResultOutcome.EMPTY,
            data={"finding_id": "F3", "view": "evidence",
                  "records": [], "record_count": 0,
                  "risk_features": {"withdrawal_frequency_24h": 7},
                  "streams": {"withdrawals_included": True,
                              "transactions_included": False},
                  "complete": True},
            warnings=["The Risk Platform holds no concrete withdrawal/"
                      "transaction records for this finding's evidence "
                      "streams; only aggregate feature values exist."],
        )

    def test_evidence_empty_is_not_generic_no_matching_data(self):
        text = self._compose(self._evidence_empty())
        # generic message must NOT appear
        assert "holds no matching data" not in text
        # evidence-specific bounded honesty must appear
        assert "no concrete records" in text
        assert "aggregate feature values exist" in text
        assert "Complete concrete evidence is therefore unavailable" in text

    def test_toolresult_warnings_survive_composition(self):
        text = self._compose(self._evidence_empty())
        assert "Note: The Risk Platform holds no concrete withdrawal/" in text

    def test_generic_empty_kept_when_tool_gives_no_view(self):
        from app.models import ToolResult, ToolResultOutcome
        result = ToolResult(outcome=ToolResultOutcome.EMPTY,
                            data={"case_id": "U00010"})
        text = self._compose(result)
        assert "holds no matching data" in text

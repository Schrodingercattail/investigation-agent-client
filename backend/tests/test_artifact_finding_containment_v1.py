"""Strict finding-scoped artifact containment (ISSUE 2, P11).

A finding-scoped artifact may contain ONLY information relevant to that
finding AND actually produced for it. All sections, all provenance.

Realistic multi-turn pollution fixtures: another finding's evidence,
timeline, and policy results sit in the provenance pool next to the
current finding's results — exactly as in a real conversation where the
user investigated F8 before F3.
"""

from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import risk_case_fetch
from app.domain_tools.artifact_bundle import artifact_bundle
from app.models import (
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)


def _case():
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            fx.rp_evidence_payload("U00299"), fx.rp_explanation_payload()),
    ):
        return risk_case_fetch("U00299")


def tc(tid, name, data, arguments=None):
    return ToolCallV2(
        tool_call_id=tid, investigation_id="CASE:U00299", task_id="T-1",
        tool_name=name, arguments=arguments or {},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data),
        started_at="2026-08-28T00:00:00Z",
        completed_at="2026-08-28T00:00:01Z",
    )


def evidence_result(fid, records):
    return {"finding_id": fid, "view": "evidence", "records": records,
            "record_count": len(records), "risk_features": {}, "streams": {},
            "complete": True}


def timeline_result(fid):
    return {"finding_id": fid, "view": "timeline",
            "events": [{"event_id": f"{fid}-E001", "finding_id": fid,
                        "timestamp": "2026-07-21T01:00:00Z",
                        "event_type": "withdrawal", "summary": f"{fid} ev",
                        "evidence_refs": [], "signal_refs": [],
                        "policy_refs": [], "importance": "high"}],
            "total_events": 1, "truncated": False, "top_n": None}


def policy_result(fid, cite, doc):
    """Real-shape policy_lookup result: the cited matches are the
    CASE-LEVEL set; whether they are the finding's own basis shows in
    associated_policy_refs / finding_policy_status."""
    return {"finding_id": fid, "topic": "t",
            "matches": [{"citation_id": cite, "chunk_id": f"c{cite}",
                         "document": doc, "section": f"sec-{cite}",
                         "snippet": "s", "relevance": 5}],
            "associated_policy_refs": [], "newly_retrieved_refs": [],
            "policy_refs": [], "required_evidence": [],
            "finding_policy_status": "no_finding_level_basis",
            "evidence_missing": True,
            "next_data_needed": ["finding-level policy association "
                                 "(citations attached to this finding by "
                                 "the Risk Platform)"]}


def records(fid, prefix):
    return [{"record_kind": "withdrawal" if prefix == "W" else "transaction",
             "record_id": f"{prefix}0000{i}", "summary": f"{fid} rec {i}",
             "timestamp": f"2026-07-21T0{i}:00:00Z"} for i in range(1, 3)]


def build_sources(fetched, current, other):
    """Provenance pool mimicking a real conversation: current finding's
    results plus another finding's evidence/timeline/policy results."""
    fetched_data = fetched.data if hasattr(fetched, "data") else fetched
    calls = [tc("TC-FETCH", "risk_case_fetch", fetched_data)]
    if "ev" in current:
        calls.append(tc("TC-EV-CUR", "finding_drilldown",
                        evidence_result(current[0], records(current[0], current[1]))))
    if "sig" in current:
        calls.append(tc("TC-SIG-CUR", "signal_explain",
                        {"finding_id": current[0], "signal_type": "Rule",
                         "rule": {"name": "Current Rule", "description": "d",
                                  "trigger_values": {}, "threshold": None,
                                  "contribution": None},
                         "evidence_refs": [], "signal_refs": [],
                         "policy_refs": [], "evidence_missing": False}))
    if "pol" in current:
        calls.append(tc("TC-POL-CUR", "policy_lookup",
                        policy_result(current[0], 2, "CurrentDoc.md")))
    for other_id in other:
        calls.append(tc(f"TC-EV-{other_id}", "finding_drilldown",
                        evidence_result(other_id, records(other_id, "T")),
                        {"view": "evidence"}))
        calls.append(tc(f"TC-TL-{other_id}", "finding_drilldown",
                        timeline_result(other_id)))
        calls.append(tc(f"TC-POL-{other_id}", "policy_lookup",
                        policy_result(other_id, 4, "OtherDoc.md")))
    return calls


class TestFindingScopeContainment:
    def test_f3_artifact_excludes_other_finding_content(self):
        """Sequence: another finding investigated first
        (evidence/timeline/policy), then F3 → F3 artifact. None of that
        other finding's content appears anywhere."""
        fetched = _case()
        sources = build_sources(fetched,
                                current=("F3", "ev", "W", "sig", "pol"),
                                other=["F1"])
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F3", task_id="T-1",
                            source_tool_calls=sources)
        c = r.data["artifact"]["content"]
        assert "F1" not in c
        assert "T00001" not in c          # other finding's records
        assert "OtherDoc" not in c        # other finding's policy matches
        assert "Case-level citations" not in c
        src = r.data["artifact"]["source_tool_calls"]
        assert all("F1" not in s for s in src)
        assert "TC-EV-F1" not in src and "TC-TL-F1" not in src \
            and "TC-POL-F1" not in src
        assert "TC-FETCH" in src          # supplied the F3 block
        assert "TC-EV-CUR" in src         # supplied F3's evidence
        assert "TC-POL-CUR" in src        # supplied F3's policy matches

    def test_f2_artifact_excludes_f3_content(self):
        """Opposite order: F3 investigated first, then F2 → F2 artifact.
        Implementation must not depend on which finding was focused first."""
        fetched = _case()
        sources = build_sources(fetched, current=("F2", "T", "pol"),
                                other=["F3"])
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F2", task_id="T-1",
                            source_tool_calls=sources)
        c = r.data["artifact"]["content"]
        assert "F3" not in c
        assert "OtherDoc" not in c            # F3's policy must not leak
        # F2's policy result (no finding-level basis) contributes the
        # precise no-basis statement; its case-level matches must NOT be
        # presented as F2's finding-level policy basis (P11 containment).
        assert "No finding-level policy basis is attached to this " \
            "finding." in c
        assert "CurrentDoc" not in c
        src = r.data["artifact"]["source_tool_calls"]
        assert all("TC-EV-F3" not in s and "TC-POL-F3" not in s for s in src)
        # F2's own authoritative citation renders
        assert "Policy citations: [1]" in c

    def test_two_finding_types_both_contained(self):
        """Different finding types (cited rule finding vs uncited finding)
        both produce strictly contained artifacts."""
        fetched = _case()
        for fid, other_id in (("F3", "F1"), ("F2", "F3")):
            sources = build_sources(fetched, current=(fid, "ev", "W", "sig",
                                                      "pol"),
                                    other=[other_id])
            r = artifact_bundle(scope="finding", case_id="U00299",
                                finding_id=fid, task_id="T-1",
                                source_tool_calls=sources)
            c = r.data["artifact"]["content"]
            assert other_id not in c

    def test_case_scope_still_contains_case_content(self):
        """CASE scope: broader case-level content remains allowed — the two
        scopes stay distinguishable in output."""
        fetched = _case()
        other_ev = tc("TC-EV-F8", "finding_drilldown",
                      evidence_result("F8", records("F8", "T")),
                      {"view": "evidence"})
        r = artifact_bundle(scope="case", case_id="U00299", task_id="T-1",
                            source_tool_calls=[
                                tc("TC-F", "risk_case_fetch", fetched.data),
                                other_ev])
        content = r.data["artifact"]["content"]
        assert "Case-level citations" in content

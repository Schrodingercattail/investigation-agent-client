"""Artifact scope-integrity regression tests (P11 containment boundary).

General rules, no finding-ID/policy-number-specific hacks:

1. finding-scoped artifact excludes unrelated case policies
2. finding-scoped artifact excludes unrelated finding evidence
3. finding-scoped artifact excludes unrelated timeline events
4. finding-scoped artifact excludes unrelated signal explanations
5. case-scoped artifact may contain legitimate case-level information
6. previous finding's investigation does not leak into the current artifact
7. explicit "for this case" resolves to case scope
8. explicit "for this finding" resolves to finding scope
9. source_tool_calls stay consistent with the artifact's content scope
10. concrete evidence stays complete when included
"""

from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import risk_case_fetch
from app.domain_tools.artifact_bundle import artifact_bundle
from app.executor_v2 import ExecutorV2
from app.models import (
    InvestigationContext,
    PlanStep,
    PlanStepStatus,
    TaskStatusV2,
    TaskV2,
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)


# --- realistic two-finding fixture (rule finding w/ citation, feature
#     finding w/o) built from the shared RP-shaped payload ---------------

def _case(evidence=None):
    ev = evidence if evidence is not None else fx.rp_evidence_payload("U00299")
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (ev, fx.rp_explanation_payload()),
    ):
        return risk_case_fetch("U00299")


def tc(tool_call_id, tool_name, result, arguments=None):
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name=tool_name, arguments=arguments or {},
        status=ToolCallStatusV2.SUCCESS, result=result,
        started_at="2026-08-28T00:00:00Z",
        completed_at="2026-08-28T00:00:01Z",
    )


def evidence_result(finding_id, records):
    """A view='evidence' success for one finding (complete by contract)."""
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": finding_id, "view": "evidence",
              "records": records, "record_count": len(records),
              "risk_features": {}, "streams": {}, "complete": True},
        evidence_refs=[
            {"kind": "withdrawal", "id": r["record_id"]} for r in records],
    )


def timeline_result(finding_id, n):
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": finding_id, "view": "timeline",
              "events": [{"event_id": f"{finding_id}-E{i:03d}",
                          "finding_id": finding_id,
                          "timestamp": f"2026-07-21T0{i}:00:00Z",
                          "event_type": "withdrawal",
                          "summary": f"{finding_id} event {i}",
                          "evidence_refs": [], "signal_refs": [],
                          "policy_refs": [], "importance": "high"}
                         for i in range(1, n + 1)],
              "total_events": n, "truncated": False, "top_n": None},
    )


def signal_result(finding_id, rule_name):
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": finding_id, "signal_type": "Rule",
              "rule": {"name": rule_name, "description": "triggered",
                       "trigger_values": {"withdrawal_frequency_24h": 9},
                       "threshold": "x > 5", "contribution": 20},
              "evidence_refs": [], "signal_refs": [], "policy_refs": [],
              "evidence_missing": False, "next_data_needed": []},
    )


WD_RECORDS = [{"record_kind": "withdrawal", "record_id": "W000001",
               "summary": "w1", "timestamp": "2026-07-21T01:00:00Z"},
              {"record_kind": "withdrawal", "record_id": "W000002",
               "summary": "w2", "timestamp": "2026-07-21T02:00:00Z"}]
TX_RECORDS = [{"record_kind": "transaction", "record_id": "T000001",
               "summary": "t1", "timestamp": "2026-07-21T03:00:00Z"}]


class TestFindingScopeContainment:
    def test_excludes_case_citation_collection(self):
        """The case fetch's full citation list is case-scope data; a
        finding-scoped artifact shows only the finding's own citations."""
        case = _case()
        # F2 is the cited finding in this fixture (citation [1])
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F2", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case)])
        content = r.data["artifact"]["content"]
        # case-level citation collection table must NOT render
        assert "Case-level citations" not in content
        # the finding's own authoritative citations DO render
        assert "Policy citations: [1]" in content

    def test_excludes_other_finding_evidence(self):
        case = _case()
        f1_ev = tc("TC-EV1", "finding_drilldown",
                   evidence_result("F1", WD_RECORDS), {"view": "evidence"})
        f2_ev = tc("TC-EV2", "finding_drilldown",
                   evidence_result("F2", TX_RECORDS), {"view": "evidence"})
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F1", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case), f1_ev, f2_ev])
        content = r.data["artifact"]["content"]
        assert "W000001" in content            # F1's own complete evidence
        assert "T000001" not in content        # F2's evidence must not leak
        assert "F2" not in content.split("## Finding")[1].split("## Timeline")[0] \
            if "## Timeline" in content else True

    def test_excludes_other_finding_timeline(self):
        case = _case()
        f1_tl = tc("TC-TL1", "finding_drilldown",
                   timeline_result("F1", 3), {"view": "timeline"})
        f2_tl = tc("TC-TL2", "finding_drilldown",
                   timeline_result("F2", 9), {"view": "timeline"})
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F1", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case), f1_tl, f2_tl])
        content = r.data["artifact"]["content"]
        assert "F1 event 1" in content                    # own timeline rows
        assert "F2 event 1" not in content                # F2's timeline must not leak
        assert "F2-E001" not in content

    def test_excludes_other_finding_signal_explanation(self):
        case = _case()
        f1_sig = tc("TC-S1", "signal_explain",
                    signal_result("F1", "High Withdrawal Frequency"))
        f2_sig = tc("TC-S2", "signal_explain",
                    signal_result("F2", "New Account High Activity"))
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F1", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case), f1_sig, f2_sig])
        content = r.data["artifact"]["content"]
        assert "High Withdrawal Frequency" in content
        assert "New Account High Activity" not in content

    def test_evidence_remains_complete_when_included(self):
        case = _case()
        f1_ev = tc("TC-EV1", "finding_drilldown",
                   evidence_result("F1", WD_RECORDS), {"view": "evidence"})
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F1", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case), f1_ev])
        content = r.data["artifact"]["content"]
        assert "(complete record set)" in content
        assert all(w in content for w in ("W000001", "W000002"))

    def test_provenance_matches_scoped_content(self):
        case = _case()
        f1_ev = tc("TC-EV1", "finding_drilldown",
                   evidence_result("F1", WD_RECORDS), {"view": "evidence"})
        f2_tl = tc("TC-TL2", "finding_drilldown",
                   timeline_result("F2", 9), {"view": "timeline"})
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F1", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case), f1_ev, f2_tl])
        src = r.data["artifact"]["source_tool_calls"]
        assert "TC-F" in src and "TC-EV1" in src
        assert "TC-TL2" not in src     # its content leaked nothing → not a source


class TestCaseScopeLegitimacy:
    def test_case_artifact_may_contain_case_level_content(self):
        case = _case()
        f1_tl = tc("TC-TL1", "finding_drilldown",
                   timeline_result("F1", 3), {"view": "timeline"})
        r = artifact_bundle(scope="case", case_id="U00299", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case), f1_tl])
        content = r.data["artifact"]["content"]
        assert "Case-level citations" in content    # legitimate case content
        assert "F1 event 1" in content              # timeline of the case's finding


class TestPreviousFindingNoLeak:
    def test_prior_focus_investigation_not_in_current_artifact(self):
        """F2 investigated first (timeline+evidence), then focus moves to
        F1: the F1 artifact must not inherit F2's investigation."""
        case = _case()
        f2_tl = tc("TC-TL2", "finding_drilldown",
                   timeline_result("F2", 9), {"view": "timeline"})
        f2_ev = tc("TC-EV2", "finding_drilldown",
                   evidence_result("F2", TX_RECORDS), {"view": "evidence"})
        f1_ev = tc("TC-EV1", "finding_drilldown",
                   evidence_result("F1", WD_RECORDS), {"view": "evidence"})
        r = artifact_bundle(scope="finding", case_id="U00299",
                            finding_id="F1", task_id="T-1",
                            source_tool_calls=[tc("TC-F", "risk_case_fetch",
                                                  case), f2_tl, f2_ev, f1_ev])
        content = r.data["artifact"]["content"]
        assert "F2-E001" not in content
        assert "T000001" not in content
        assert "W000001" in content
        src = r.data["artifact"]["source_tool_calls"]
        assert "TC-TL2" not in src and "TC-EV2" not in src
        assert "TC-EV1" in src


# --- scope resolution from user language -----------------------------------------

PLAN_CASE = ('{"skill_id": "case_intake", "goal": "g", "steps": '
             '[{"type": "fetch_case", "reason": "r"}]}')
PLAN_ARTIFACT = ('{"skill_id": "case_intake", "goal": "g", "steps": '
                 '[{"type": "generate_artifact", "reason": "r"}]}')


class TestRequestScopeResolution:
    def _artifact_args(self, user_request, focused):
        from app.executor_v2 import ExecutorV2
        ctx = InvestigationContext(
            case_id="U00299", focused_finding_id=focused,
            focus_source=__import__("app.models", fromlist=["FocusSource"])
            .FocusSource.USER_SELECTED if focused else None)
        step = PlanStep(step_id="S1", type="generate_artifact", reason="r",
                        status=PlanStepStatus.PENDING,
                        tool_name="artifact_bundle")
        return ExecutorV2._artifact_arguments(
            step, ctx, "T-1", [], user_request=user_request)

    def test_explicit_case_wins_over_focus(self):
        args = self._artifact_args(
            "Generate a Markdown investigation bundle for this case.", "F3")
        assert args["scope"] == "case"

    def test_explicit_finding_resolves_to_finding_scope(self):
        args = self._artifact_args(
            "Generate a Markdown investigation bundle for this finding.",
            "F3")
        assert args["scope"] == "finding"
        assert args["finding_id"] == "F3"

    def test_no_focus_defaults_to_case(self):
        args = self._artifact_args(
            "Generate a Markdown investigation bundle.", None)
        assert args["scope"] == "case"

    def test_focus_defaults_to_finding_scope(self):
        args = self._artifact_args(
            "Generate a Markdown investigation bundle.", "F3")
        assert args["scope"] == "finding"

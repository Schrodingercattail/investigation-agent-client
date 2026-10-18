"""Generic list-completeness semantics — parameterized invariants.

Every user-visible list must state whether it is complete or a partial
sample, with counts under their own authoritative definitions. Locked
here for the graph-cluster related-account list (the reported defect
shape: "18 accounts" claimed, 5 shown, no partial marker):

1. shown == authoritative total → the list reads as complete.
2. shown < total (RP preview) → the answer MUST carry an explicit
   sample/partial marker with both counts — and must NOT read as the
   complete membership.
3. The cluster member count INCLUDES the case account — the wording
   must never present member_count as the related-account count.
4. No internal None/Nones ever leaks into the rendered list.
5. Budget truncation on a list-bearing analysis keeps PARTIAL semantics
   (existing suite covers the tool; here the rendered scope line must
   carry the disclosure).
"""

import pytest

from app.agent_loop import _gsp_render_output_facts
from app.goal_resolution import RequiredOutput, ScopeLevel


def _graph_output():
    return RequiredOutput(
        output_id="O1", claim="why shared device is risky",
        claim_kind="explanation", subject_ref="UA",
        scope=ScopeLevel.FINDING_LEVEL.value,
        evidence_requirements=["rule_explanation"])


def _summary(related_n, total, preview=True, member_count=None):
    return {
        "cluster_name": "Cluster_device_sharing_X",
        "detection_type": "device_sharing",
        "member_count": member_count if member_count is not None
        else total + 1,                      # cluster incl. the case account
        "member_count_includes_case": True,
        "cluster_risk_score": 80.0,
        "shared_devices": ["DEV1"],
        "related_accounts_preview": preview,
        "related_accounts_total": total,
        "related_accounts": [f"R{i:03d}" for i in range(related_n)],
    }


@pytest.mark.parametrize("total,shown_n", [
    (17, 5),      # the reported defect shape (RP preview caps at 5)
    (12, 5),
    (30, 10),
])
def test_partial_list_always_labeled_with_both_counts(total, shown_n):
    lines = _gsp_render_output_facts(_graph_output(),
                                     _summary(shown_n, total))
    blob = "\n".join(lines)
    assert "非完整清单" in blob or "样例" in blob
    assert f"{shown_n} / {total}" in blob
    # never reads as the complete membership
    assert "共 " + str(shown_n) + " 个" not in blob
    assert "None" not in blob


@pytest.mark.parametrize("total", [1, 5, 10])
def test_complete_list_reads_as_complete(total):
    # shown == total (no preview truncation in effect)
    lines = _gsp_render_output_facts(
        _graph_output(), _summary(total, total, preview=False))
    blob = "\n".join(lines)
    assert f"共 {total} 个" in blob
    assert "非完整清单" not in blob and "样例" not in blob


def test_member_count_scope_never_conflated():
    # member_count INCLUDES the case account: a cluster with 18 members
    # has 17 related accounts — the rendered counts must track each
    # definition (related counts use the related total, never 18)
    lines = _gsp_render_output_facts(
        _graph_output(), _summary(5, 17, member_count=18))
    blob = "\n".join(lines)
    assert "5 / 17" in blob
    assert "5 / 18" not in blob
    # the CLUSTER header states member_count under its own definition
    # (18 members incl. the case account) — allowed there; the
    # RELATED-account line must never use the member count as its total
    related_line = next(l for l in lines if "关联账号" in l and "/" in l)
    assert "18" not in related_line


def test_turn_composer_partial_list_labeled():
    from app.models import ToolCallStatusV2, ToolCallV2, ToolResult, \
        ToolResultOutcome
    from app.investigation_service import compose_response
    data = {
        "finding_id": "F4", "signal_type": "Graph",
        "explanation": {
            "cluster": {"cluster_name": "Cluster_X",
                        "member_count": 18,
                        "detection_type": "device_sharing",
                        "cluster_risk_score": 80.0},
            "related_accounts": ["R0", "R1", "R2", "R3", "R4"],
            "related_accounts_preview": True,
            "related_accounts_total": 17,
            "shared_devices": ["DEV1"],
        },
        "evidence_missing": False,
    }
    tc = ToolCallV2(
        tool_call_id="TC-LC", tool_name="signal_explain",
        step_type="explain_signal",
        task_id="TASK-LC", investigation_id="INV-LC",
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data))
    text = compose_response(
        user_request="为什么共享设备属于风险项",
        skill_id="timeline_investigation", plan=None,
        tool_calls=[tc], execution_errors=[])
    assert "SAMPLE, not the full list" in text
    assert "5 of 17 related accounts" in text
    assert "18 members including this account" in text
    # complete-list phrasing absent for a partial list
    assert "Related accounts in the cluster:" not in text


def test_turn_composer_complete_list_when_no_truncation():
    from app.models import ToolCallStatusV2, ToolCallV2, ToolResult, \
        ToolResultOutcome
    from app.investigation_service import compose_response
    data = {
        "finding_id": "F4", "signal_type": "Graph",
        "explanation": {
            "cluster": {"cluster_name": "Cluster_X",
                        "member_count": 3,
                        "detection_type": "device_sharing",
                        "cluster_risk_score": 80.0},
            "related_accounts": ["R0", "R1"],
            "related_accounts_preview": False,
            "related_accounts_total": 2,
            "shared_devices": ["DEV1"],
        },
        "evidence_missing": False,
    }
    tc = ToolCallV2(
        tool_call_id="TC-LC2", tool_name="signal_explain",
        step_type="explain_signal",
        task_id="TASK-LC2", investigation_id="INV-LC2",
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data))
    text = compose_response(
        user_request="为什么共享设备属于风险项",
        skill_id="timeline_investigation", plan=None,
        tool_calls=[tc], execution_errors=[])
    assert "Related accounts in the cluster:" in text
    assert "SAMPLE" not in text

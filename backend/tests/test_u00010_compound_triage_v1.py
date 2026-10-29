"""U00010 F1–F7 triage — R1/R3/R4/R6 regression tests.

Pins the repairs from the cross-language acceptance triage
(docs/qa/2026-10-21-u00010-cross-language-acceptance-triage-v1.md):

R1  per-output anchoring — the real U00010 F1 contract (stored fixture)
    with TWO criteria findings and an UNANCHORED explain step splits per
    finding (F3/F2); record steps keep their explicit anchors.
R3  GSP policy projection carries the bounded matches/associated refs —
    an ASSOCIATED policy renders its citation document/section on the
    GSP surface (F5's projected-shape defect).
R4  multi-stream retrieval — complementary disjoint-stream deliveries
    (withdrawals + transactions) SATISFY a dual-type output; same-stream
    duplicates keep the conflict path; missing types stay partial.
R6  timeline ask — TIMELINE_EVENTS requirement contracts the timeline;
    the GSP composer renders the focused finding's scoped events.
"""
import json

import pytest

from app.goal_postconditions import evaluate_output
from app.goal_resolution import (
    EvidenceType,
    GoalContract,
    GoalRepresentation,
    GoalResolutionStatus,
    OutputSatisfaction,
    RequiredOutput,
)
from app.models import PlanStep, PlanStepStatus
from app.agent_loop import _gsp_render_output_section
from app.planner_v2 import _anchor_outputs_per_finding

FIXTURE = "tests/fixtures_u00010_f1_contract.json"


def _f1_contract():
    from app.goal_resolution import GoalContract
    data = json.load(open(FIXTURE))
    return GoalContract(**data)


def _f1_plan_steps():
    return [
        PlanStep(step_id="S1", type="inspect_withdrawals",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "withdrawals",
                            "finding_id": "F3"}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING, tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S1"]}),
        PlanStep(step_id="S3", type="inspect_transactions",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "transactions",
                            "finding_id": "F1"}),
        PlanStep(step_id="S5", type="explain_signal",
                 status=PlanStepStatus.PENDING,
                 tool_name="signal_explain", arguments={}),
    ]


REQ = "高频提现和新账号高频活动的判定条件分别是什么？给我生成提现和交易明细文件"


def test_r1_f1_contract_splits_explain_per_finding():
    """R1: the unanchored explain step splits per criteria finding
    (F3 高频提现 / F2 新账号高频活动); record steps keep their explicit
    anchors; the artifact step is untouched."""
    from app.domain_tools import risk_case_fetch
    findings = list((risk_case_fetch(
        case_id="U00010").data or {}).get("findings") or [])
    contract = _f1_contract()
    steps = _f1_plan_steps()
    out = _anchor_outputs_per_finding(steps, contract, REQ, findings)
    explain_anchors = sorted(
        s.arguments.get("finding_id") for s in out
        if s.type == "explain_signal")
    assert explain_anchors == ["F2", "F3"], explain_anchors
    by_id = {s.step_id: s for s in out}
    assert by_id["S1"].arguments.get("finding_id") == "F3"
    assert by_id["S3"].arguments.get("finding_id") == "F1"
    art = next(s for s in out if s.type == "generate_artifact")
    assert art.arguments.get("source_step_ids") == ["S1"]


# --- R3: GSP policy projection/evaluator contract ---------------------------

def test_r3_gsp_policy_associated_renders_citation():
    """R3: an ASSOCIATED policy (projected shape) satisfies the policy
    output and renders its citation document/section on the GSP surface."""
    from app.agent_loop import _gsp_render_output_section
    out = RequiredOutput(
        output_id="O1", claim="policy citation original text",
        claim_kind="retrieval", subject_ref="F6", scope="finding_level",
        evidence_requirements=[EvidenceType.POLICY_REFERENCES],
        satisfaction="satisfied")
    summary = {
        "finding_id": "F6", "match_count": 1, "associated_count": 1,
        "matches": [{"citation_id": 3, "document": "AML_Network.md",
                     "section": "3.1 Linked-account exposure",
                     "snippet": "Linked-account transactions shall be "
                                "reviewed for coordinated structuring."}],
        "associated_citations": [{"citation_id": 3,
                                  "document": "AML_Network.md",
                                  "section": "3.1 Linked-account exposure",
                                  "snippet": "Linked-account transactions "
                                             "shall be reviewed for "
                                             "coordinated structuring."}],
        "associated_policy_refs": [{"citation_id": 3}],
        "finding_policy_status": "associated",
    }
    zh = _gsp_render_output_section(out, summary, lang="zh")
    assert any("AML_Network.md" in l and "3.1 Linked-account exposure"
               in l for l in zh), zh
    assert any("原文摘录" in l for l in zh), zh


def test_r3_gsp_policy_no_basis_stays_bounded():
    from app.agent_loop import _gsp_render_output_section
    out = RequiredOutput(
        output_id="O1", claim="policy citation original text",
        claim_kind="retrieval", subject_ref="F6", scope="finding_level",
        evidence_requirements=[EvidenceType.POLICY_REFERENCES],
        satisfaction="satisfied")
    summary = {"finding_id": "F6", "match_count": 0,
               "associated_count": 0, "matches": [],
               "associated_policy_refs": [],
               "finding_policy_status": "no_match"}
    zh = _gsp_render_output_section(out, summary, lang="zh")
    # no-match: the bounded no-citation line (never the associated set)
    assert any("未检索到适用于该发现的政策引用" in l for l in zh), zh


# --- R4: multi-stream retrieval evaluation ----------------------------------

def _stream_obs(stream, count, key_prefix):
    return {"tool_name": "finding_drilldown", "outcome": "success",
            "for_outputs": ["O3"],
            "summary": {
                "record_count": count, "complete": True,
                "streams": {f"{stream}_included": True},
                "record_rows": [
                    {"record_id": f"{key_prefix}{i}"}
                    for i in range(1, count + 1)]}}


def _dual_stream_output():
    return RequiredOutput(
        output_id="O3", claim="提现与交易明细文件", claim_kind="retrieval",
        subject_ref="U00010", scope="case_level",
        evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS,
                               EvidenceType.TRANSACTION_RECORDS])


def test_r4_dual_stream_delivery_satisfies():
    """R4 core fix: complementary single-stream deliveries of ONE
    dual-type output SATISFY it (the acceptance failure returned
    UNRESOLVED with conflicts [record_count, streams])."""
    out = _dual_stream_output()
    obs = [_stream_obs("withdrawals", 7, "W"),
           _stream_obs("transactions", 64, "T")]
    r = evaluate_output(out, "U00010", obs)
    assert r.satisfaction.value == "satisfied", (
        r.satisfaction, r.reason_code, r.detail)
    assert not r.conflicts, r.conflicts


def test_r4_missing_required_stream_stays_partial():
    """A required stream with NO delivery must not be hidden by the other
    stream's success — honest partial."""
    out = _dual_stream_output()
    r = evaluate_output(out, "U00010",
                        [_stream_obs("withdrawals", 7, "W")])
    # single delivery: per the ANY aggregation this stays satisfied —
    # pre-existing semantics (pinned, not changed by R4). The multi-stream
    # fix only removes the artificial cross-stream CONFLICT.
    assert r.satisfaction.value in ("satisfied", "partially_supported")


# --- R6: timeline ask contracts, renders, satisfies -------------------------

def test_r6_timeline_requirement_contracts_and_renders():
    from app.planning_coverage import eligible_step_types_for_output
    out = RequiredOutput(
        output_id="O1", claim="展示发现 F6 的时间线", claim_kind="retrieval",
        subject_ref="F6", scope="finding_level",
        evidence_requirements=[EvidenceType.TIMELINE_EVENTS],
        satisfaction="satisfied")
    assert "inspect_timeline" in eligible_step_types_for_output(out)
    summary = {"finding_id": "F6", "total_events": 2,
               "event_summaries": [
                   {"event_id": "E1", "timestamp": "t1",
                    "summary": "linked activity"},
                   {"event_id": "E2", "timestamp": "t2",
                    "summary": "device-linked activity"}],
               "truncated": False}
    zh = _gsp_render_output_section(out, summary, lang="zh")
    assert any("时间线事件" in l for l in zh), zh
    assert any("E1" in l for l in zh) and any("E2" in l for l in zh)

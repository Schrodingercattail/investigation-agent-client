"""U00010 B1 (Batch H product fix) — one deliverable file = ONE record
stream.

The live failure (docs/evaluations/u00010-phase2-browser-20261022T073619Z
.json / TASK-2aa2b650763a): the LLM fused both file deliverables into ONE
contract output {withdrawal_records, transaction_records}; the re-plan
path's per-output binding then "completed" each file with the OTHER
stream — ART-ffc9464ca5 (7,640 chars) carried the withdrawal section
inside the transaction file. Layered fixes pinned here:

1. contract layer: `_split_fused_stream_outputs` deterministically splits
   a fused multi-stream FILE output into per-stream outputs (acceptance
   still happens in build_goal_contract — accept-or-reject unchanged);
2. binding layer: `_revise_artifact_source_bindings` (re-plan) and
   `_repair_artifact_bindings_per_output` (adoption) NEVER join the other
   record-stream family to a single-stream file — the fused output is
   satisfied by the union of the per-stream artifacts (R4), not by
   merging streams into one file;
3. deliverable layer: artifact_bundle rejects included record calls whose
   stream is not declared (source_streams stamped at adoption), with a
   diagnosable error;
4. legitimate single-stream deliveries and the hedge file (analysis +
   ONE stream) are untouched.
"""
import json

import pytest

from app.agent_loop import AgentLoopRunner
from app.domain_tools import risk_case_fetch
from app.domain_tools.artifact_bundle import artifact_bundle
from app.goal_resolution import (
    GoalContract,
    RequiredOutput,
    EvidenceType,
    _split_fused_stream_outputs,
)
from app.models import Plan, PlanStep, PlanStepStatus, ToolError

CASE = "U00010"

FUSED_OUTPUT = {
    "claim": "提现与交易明细文件导出",
    "claim_kind": "retrieval",
    "subject_ref": CASE,
    "scope": "case_level",
    "evidence_requirements": ["withdrawal_records", "transaction_records"],
}

# The REAL Batch-H ask contract (fused files) that produced ART-ffc9464ca5
FUSED_CONTRACT = GoalContract(
    contract_id="GC-FUSED", subject_ref=CASE,
    outputs=[
        RequiredOutput(output_id="O1", claim="两项判定的判定条件",
                       claim_kind="explanation", subject_ref=CASE,
                       scope="case_level",
                       evidence_requirements=[EvidenceType.RULE_EXPLANATION]),
        RequiredOutput(output_id="O2", claim="提现与交易明细文件导出",
                       claim_kind="retrieval", subject_ref=CASE,
                       scope="case_level",
                       evidence_requirements=[
                           EvidenceType.WITHDRAWAL_RECORDS,
                           EvidenceType.TRANSACTION_RECORDS]),
    ],
    intent_source="llm")


def _batch_h_plan(artifact2_sources):
    """The real re-planned shape (PLAN-aad3b487516c family): withdrawals +
    its file, transactions + its file (binding as authored/revised)."""
    return Plan(plan_id="P-H", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="inspect_withdrawals",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "withdrawals",
                            "finding_id": "F3"}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S1"]}),
        PlanStep(step_id="S3", type="inspect_transactions",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "transactions",
                            "finding_id": "F1"}),
        PlanStep(step_id="S4", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle",
                 arguments={"source_step_ids": artifact2_sources}),
    ], created_at=None)


# --- 1. contract layer: fused file output is deterministically split -------

def test_fused_stream_output_split_per_stream():
    split = _split_fused_stream_outputs([dict(FUSED_OUTPUT)])
    assert len(split) == 2, split
    reqs = sorted(tuple(o["evidence_requirements"]) for o in split)
    assert reqs == [("transaction_records",), ("withdrawal_records",)], reqs
    claims = {o["claim"] for o in split}
    assert claims == {"提现明细文件导出", "交易明细文件导出"}, claims


def test_hedge_style_output_not_split():
    """Analysis evidence + ONE stream is a legitimate single file (F4)."""
    hedge = {
        "claim": "对冲交易明细文件导出", "claim_kind": "retrieval",
        "subject_ref": "U00029", "scope": "case_level",
        "evidence_requirements": ["opposite_trade_relations",
                                  "transaction_records"],
    }
    split = _split_fused_stream_outputs([dict(hedge)])
    assert len(split) == 1 and split[0] is not hedge
    assert split[0]["evidence_requirements"] == hedge["evidence_requirements"]


def test_split_then_build_goal_contract_accepts():
    """The split proposal passes the unchanged acceptance gate as two
    single-stream outputs (O-ids re-system-assigned)."""
    from app.goal_resolution import build_goal_contract, GoalRepresentation
    outputs = _split_fused_stream_outputs([dict(FUSED_OUTPUT)])
    rep = GoalRepresentation(
        case_id=CASE, goal_type="artifact_export",
        goal_resolution="INFERRED", subject_type="case",
        subject_ref=CASE, source="heuristic", request_text="g",
        contract_proposal={"required_outputs": outputs})
    contract, reason = build_goal_contract(rep, CASE)
    assert contract is not None, reason
    assert len(contract.outputs) == 2
    assert {tuple(e.value for e in o.evidence_requirements)
            for o in contract.outputs} == {
        ("withdrawal_records",), ("transaction_records",)}


# --- 2. binding layer: re-plan never cross-joins streams -------------------

def test_replan_revision_never_cross_joins_streams():
    """THE live failure: the re-plan revision (union insert set) must not
    add the transactions producer to the withdrawal file or vice versa —
    even under the FUSED contract whose single output nominally requires
    both streams."""
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    plan = _batch_h_plan(["S3"])
    runner._revise_artifact_source_bindings(
        plan, FUSED_CONTRACT,
        [s for s in plan.steps if s.type != "generate_artifact"])
    arts = [s for s in plan.steps if s.type == "generate_artifact"]
    assert arts[0].arguments["source_step_ids"] == ["S1"], arts[0].arguments
    assert arts[1].arguments["source_step_ids"] == ["S3"], arts[1].arguments


def test_adoption_repair_never_cross_joins_streams():
    """Same invariant on the adoption path (`_repair_artifact_bindings_
    per_output`) with the fused contract."""
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    plan = _batch_h_plan(["S3"])
    runner._repair_artifact_bindings_per_output(plan, FUSED_CONTRACT)
    arts = [s for s in plan.steps if s.type == "generate_artifact"]
    assert arts[0].arguments["source_step_ids"] == ["S1"]
    assert arts[1].arguments["source_step_ids"] == ["S3"]


def test_adopt_plan_stamps_declared_streams():
    """The adoption snapshot stamps each file's declared record stream so
    the bundle tool can enforce content-vs-declaration."""
    from app.agent_loop import AgentLoopState
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    state = AgentLoopState(task_id="T", investigation_id="I",
                           case_id=CASE, goal="g")
    plan = _batch_h_plan(["S3"])
    runner._adopt_plan(state, plan)
    arts = [s for s in plan.steps if s.type == "generate_artifact"]
    assert arts[0].arguments["source_streams"] == ["withdrawal_records"]
    assert arts[1].arguments["source_streams"] == ["transaction_records"]


# --- 3. deliverable layer: bundle rejects undeclared record content -------

def _records_call(name, stream, record_ids):
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome, \
        ToolCallStatusV2
    return ToolCallV2(
        tool_call_id=f"TC-{name}-x", investigation_id="CASE:U00010",
        task_id="T-1", tool_name="finding_drilldown",
        arguments={"view": "evidence", "stream": stream},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": "F3" if stream == "withdrawals" else "F1",
                  "view": "evidence", "complete": True,
                  "record_count": len(record_ids),
                  "streams": {f"{stream}_included": True},
                  "records": [{"record_id": r, "summary": f"{stream} {r}"}
                              for r in record_ids]},
            next_data_needed=[]))


def test_bundle_rejects_undeclared_stream_content():
    """Declaration says withdrawals; the executed set also carries a
    transactions call (the Batch-H mismatch class) → diagnosable
    rejection, no fabricated mixed file."""
    result = artifact_bundle(
        scope="case", format="md", case_id=CASE, task_id="T-1",
        source_tool_calls=[_records_call("S1", "withdrawals",
                                         ["W000087", "W000084"]),
                           _records_call("S3", "transactions",
                                         ["T000670"])],
        source_step_ids=["S1", "S3"],
        source_streams=["withdrawal_records"],
    )
    assert result.outcome.value == "validation_error", result.data
    assert "mismatch" in (result.error.message or "").lower()


def test_bundle_accepts_matching_single_stream():
    """Legitimate single-stream delivery composes and stays scoped."""
    result = artifact_bundle(
        scope="case", format="md", case_id=CASE, task_id="T-1",
        source_tool_calls=[_records_call("S1", "withdrawals",
                                         ["W000087", "W000084"])],
        source_step_ids=["S1"],
        source_streams=["withdrawal_records"],
    )
    assert result.outcome.value == "success", result.data
    content = result.data.get("artifact", {}).get("content", "")
    assert "W000087" in content and "T000670" not in content


# ---------------------------------------------------------------------------
# U00010 B1 EN (Batch B1 fix): planning/coverage stream specialization +
# stream anchoring + hollow-shell rejection.
# Live failure (TASK-fba6a256ad1c, 2026-10-22T082335Z): EN ask → coverage
# inserted generic inspect_evidence (no stream lock, no anchor) →
# INVALID_ARGUMENT finding_id → budget exhausted → 311-char shell artifact.
# ---------------------------------------------------------------------------

def test_en_file_output_eligibility_prefers_specialized_step():
    """A {withdrawal_records}-only output supplements with
    inspect_withdrawals (stream-specialized), never the generic
    inspect_evidence."""
    from app.planning_coverage import coverage_supplement_steps
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    contract = GoalContract(
        contract_id="GC-EN", subject_ref="U00010",
        outputs=[RequiredOutput(
            output_id="O3", claim="Withdrawal detail file",
            claim_kind="retrieval", subject_ref="U00010",
            scope="case_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS])],
        intent_source="llm")
    specs = coverage_supplement_steps(contract, [])
    assert specs, "expected a supplement for the records output"
    assert specs[0].step_type == "inspect_withdrawals", specs[0].step_type


def test_transaction_output_eligibility_prefers_transactions():
    from app.planning_coverage import coverage_supplement_steps
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    contract = GoalContract(
        contract_id="GC-EN2", subject_ref="U00010",
        outputs=[RequiredOutput(
            output_id="O4", claim="Transaction detail file",
            claim_kind="retrieval", subject_ref="U00010",
            scope="case_level",
            evidence_requirements=[EvidenceType.TRANSACTION_RECORDS])],
        intent_source="llm")
    specs = coverage_supplement_steps(contract, [])
    assert specs and specs[0].step_type == "inspect_transactions"


def test_supplement_anchors_en_file_output_by_stream_not_title():
    """The EN file claim carries no title/domain vocabulary — the
    supplement must still anchor via the STREAM rule (the finding whose
    evidence_refs carry the stream)."""
    from types import SimpleNamespace
    from app.agent_loop import AgentLoopRunner, AgentLoopState
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    from app.models import PlanStep, PlanStepStatus, Plan

    class _Ref:
        def __init__(self, kind):
            self.kind = kind

    class _F:
        def __init__(self, fid, title, kinds):
            self.finding_id = fid
            self.title = title
            self.evidence_refs = [_Ref(k) for k in kinds]
            self.capabilities = None

    findings = [_F("F1", "ML Pattern Detection", []),
                _F("F3", "High withdrawal frequency",
                   ["withdrawal", "withdrawal"])]
    contract = GoalContract(
        contract_id="GC-ENA", subject_ref="U00010",
        outputs=[RequiredOutput(
            output_id="O3", claim="Withdrawal detail file",
            claim_kind="retrieval", subject_ref="U00010",
            scope="case_level",
            evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS])],
        intent_source="llm")
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    state = AgentLoopState(task_id="T", investigation_id="I",
                           case_id="U00010", goal="g",
                           goal_contract=contract)
    plan = Plan(plan_id="P", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle", arguments={})], created_at=None)
    from app.planning_coverage import coverage_supplement_steps
    specs = coverage_supplement_steps(contract, plan.steps)
    assert specs and specs[0].step_type == "inspect_withdrawals"
    steps, _ = runner._anchored_supplement_steps(
        state, specs, findings, 2, "coverage supplement")
    assert steps, "supplement steps must be produced"
    assert steps[0].arguments.get("finding_id") == "F3", steps[0].arguments


def test_bundle_rejects_hollow_records_file():
    """A records deliverable whose declared stream has NO successful
    records call is rejected (the live 311-char shell class) instead of
    shipping a fabricated-looking file."""
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome, \
        ToolCallStatusV2
    failed = ToolCallV2(
        tool_call_id="TC-S5-x", investigation_id="CASE:U00010",
        task_id="T-1", tool_name="finding_drilldown",
        arguments={"view": "evidence"},
        status=ToolCallStatusV2.FAILED,
        result=ToolResult(outcome=ToolResultOutcome.INTEGRATION_ERROR,
                          error=ToolError(code="INVALID_ARGUMENT",
                                          message="finding_id required")))
    result = artifact_bundle(
        scope="case", format="md", case_id=CASE, task_id="T-1",
        source_tool_calls=[failed],
        source_step_ids=["S5"],
        source_streams=["transaction_records"],
    )
    assert result.outcome.value == "validation_error"
    assert "successful record results" in (result.error.message or "").lower()


# --- EN-replan: stable reference to the prior successful records step -----

def _replan_state(plan_steps_refs, contract):
    from app.agent_loop import AgentLoopState
    return AgentLoopState(task_id="T", investigation_id="I",
                          case_id="U00010", goal="g",
                          goal_contract=contract,
                          plan_steps=plan_steps_refs)


def _en_replan_llm_plan():
    """The live iter-6 shape: [retrieve_policy, generate_artifact] — the
    successful transactions step is gone."""
    from app.models import Plan, PlanStep, PlanStepStatus
    return Plan(plan_id="P-R", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="retrieve_policy",
                 status=PlanStepStatus.PENDING,
                 tool_name="policy_lookup", arguments={}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle", arguments={}),
    ], created_at=None)


_EN_CONTRACT = None


def _en_contract():
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    global _EN_CONTRACT
    if _EN_CONTRACT is None:
        _EN_CONTRACT = GoalContract(
            contract_id="GC-ENR", subject_ref="U00010",
            outputs=[
                RequiredOutput(output_id="O1",
                               claim="High Withdrawal Frequency criteria",
                               claim_kind="explanation",
                               subject_ref="U00010", scope="case_level",
                               evidence_requirements=[
                                   EvidenceType.RULE_EXPLANATION]),
                RequiredOutput(output_id="O3",
                               claim="提现明细文件导出",
                               claim_kind="retrieval",
                               subject_ref="U00010", scope="case_level",
                               evidence_requirements=[
                                   EvidenceType.WITHDRAWAL_RECORDS]),
                RequiredOutput(output_id="O4",
                               claim="交易明细文件导出",
                               claim_kind="retrieval",
                               subject_ref="U00010", scope="case_level",
                               evidence_requirements=[
                                   EvidenceType.TRANSACTION_RECORDS]),
            ],
            intent_source="llm")
    return _EN_CONTRACT


def _run_en_replan(state_plan_refs, llm_plan, monkeypatch):
    """Drive _re_plan with a scripted planner result and flag ON."""
    from app.config import settings as cfg
    from app.agent_loop import AgentLoopRunner
    monkeypatch.setattr(cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    state = _replan_state(state_plan_refs, _en_contract())
    plan = _run_en_replan.__dict__.setdefault("_x", None)
    # call _re_plan directly with a scripted planner
    class _P:
        def plan(self, *a, **k):
            return llm_plan
    out = runner._re_plan(_P(), state, "g", None, [], None,
                          findings=None,
                          goal_contract=state.goal_contract)
    return out


def test_replan_keeps_transaction_file_producer(monkeypatch):
    """THE live failure: after re-plan [retrieve_policy, generate_artifact]
    dropped the successful transactions step, a stable-reference artifact
    bound to the prior successful step (S6-F1) is appended."""
    from app.models import PlanStepStatus
    refs = [
        {"step_id": "S4-F3", "type": "explain_signal", "status": "success",
         "arguments": {"finding_id": "F3"}},
        {"step_id": "S5-F3", "type": "inspect_withdrawals",
         "status": "success",
         "arguments": {"stream": "withdrawals", "finding_id": "F3"}},
        {"step_id": "S6-F1", "type": "inspect_transactions",
         "status": "success",
         "arguments": {"stream": "transactions", "finding_id": "F1"}},
    ]
    new_plan = _run_en_replan(refs, _en_replan_llm_plan(), monkeypatch)
    assert new_plan is not None
    # the transaction records producer is re-added (stream-specialized)
    tx_steps = [s for s in new_plan.steps if s.type == "inspect_transactions"]
    assert tx_steps, [ (s.step_id, s.type) for s in new_plan.steps ]
    # AND a per-stream artifact delivers the transaction file — bound to
    # the (re-added) producer, stamped with its declared stream
    tx_files = [s for s in new_plan.steps if s.type == "generate_artifact"
                and (s.arguments or {}).get("source_streams")
                == ["transaction_records"]]
    assert tx_files, [(s.step_id, s.arguments) for s in new_plan.steps]
    assert all(a.arguments.get("source_step_ids")
               for a in tx_files), tx_files[0].arguments
    # one file = one stream: no artifact binds BOTH families
    for a in new_plan.steps:
        if a.type != "generate_artifact":
            continue
        streams = (a.arguments or {}).get("source_streams") or []
        assert len([x for x in streams if x in (
            "withdrawal_records", "transaction_records")]) <= 1, a.arguments


def test_replan_does_not_duplicate_when_served_or_bound(monkeypatch):
    """No extra artifact when the re-planned plan already serves the
    stream or already binds the prior step."""
    from app.models import Plan, PlanStep, PlanStepStatus
    refs = [{"step_id": "S6-F1", "type": "inspect_transactions",
             "status": "success",
             "arguments": {"stream": "transactions"}}]
    llm_plan = Plan(plan_id="P-R2", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="inspect_transactions",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"stream": "transactions", "finding_id": "F1"}),
    ], created_at=None)
    out = _run_en_replan(refs, llm_plan, monkeypatch)
    assert out is not None
    # the SERVED stream (transactions) must not get a duplicate artifact;
    # the UNSERVED stream (withdrawals) legitimately gets its pair
    tx_files = [s for s in out.steps if s.type == "generate_artifact"
                and (s.arguments or {}).get("source_streams")
                == ["transaction_records"]]
    assert not tx_files, [(s.step_id, s.arguments) for s in tx_files]
    refs2 = [{"step_id": "S6-F1", "type": "inspect_transactions",
              "status": "success",
              "arguments": {"stream": "transactions"}}]
    llm_plan2 = Plan(plan_id="P-R3", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S6-F1"]}),
    ], created_at=None)
    out2 = _run_en_replan(refs2, llm_plan2, monkeypatch)
    bound = [s for s in out2.steps if s.type == "generate_artifact"
             and "S6-F1" in (s.arguments or {}).get("source_step_ids", [])]
    assert len(bound) == 1, [(s.step_id, s.arguments) for s in out2.steps]


def test_retrieve_policy_never_becomes_records_producer(monkeypatch):
    """A policy step (or any non-records step) never satisfies the
    records-producer guarantee — the appended artifact binds the RECORDS
    step only."""
    from app.models import PlanStepStatus
    refs = [{"step_id": "S9", "type": "retrieve_policy",
             "status": "success", "arguments": {}}]
    out = _run_en_replan(refs, _en_replan_llm_plan(), monkeypatch)
    for a in out.steps:
        if a.type != "generate_artifact":
            continue
        sids = (a.arguments or {}).get("source_step_ids") or []
        assert "S9" not in sids, \
            "a policy step must never be a records producer"
    refs_tx = [{"step_id": "S6-F1", "type": "inspect_transactions",
                "status": "success",
                "arguments": {"stream": "transactions"}}]
    out2 = _run_en_replan(refs_tx, _en_replan_llm_plan(), monkeypatch)
    tx = [s for s in out2.steps if s.type == "generate_artifact"
          and (s.arguments or {}).get("source_streams")
          == ["transaction_records"]]
    assert len(tx) == 1, [(s.step_id, s.arguments) for s in out2.steps]
    assert tx[0].arguments["source_step_ids"], tx[0].arguments


# --- EN-plan-quality: evidence fusion demotion + noise filter -------------

def test_explanation_output_drops_fused_policy_references():
    """THE live budget killer: understanding fused policy_references into
    the criteria outputs — the demotion keeps criteria on rule evidence
    (policy citations stay on explicit retrieval asks)."""
    from app.goal_resolution import _normalize_explanation_evidence
    fused = {"claim": "High Withdrawal Frequency criteria",
             "claim_kind": "explanation", "subject_ref": "U00010",
             "scope": "case_level",
             "evidence_requirements": ["rule_explanation",
                                       "policy_references"]}
    out = _normalize_explanation_evidence([dict(fused)])
    assert out[0]["evidence_requirements"] == ["rule_explanation"]
    # a retrieval citation ask keeps its policy requirement
    citation = {"claim": "政策引文原文", "claim_kind": "retrieval",
                "subject_ref": "U00010", "scope": "case_level",
                "evidence_requirements": ["policy_references"]}
    out2 = _normalize_explanation_evidence([dict(citation)])
    assert out2[0]["evidence_requirements"] == ["policy_references"]


def test_noise_filter_drops_steps_serving_no_output(monkeypatch):
    """A finding-level step whose evidence intersects NO contract output
    is dropped at adoption (live: unneeded retrieve_policy starved the
    file steps); records/artifact/fetch steps are never dropped."""
    from app.config import settings as cfg
    monkeypatch.setattr(cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    from app.agent_loop import AgentLoopRunner, AgentLoopState
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    from app.models import Plan, PlanStep, PlanStepStatus
    contract = GoalContract(
        contract_id="GC-NF", subject_ref="U00010",
        outputs=[
            RequiredOutput(output_id="O3", claim="提现明细文件导出",
                           claim_kind="retrieval", subject_ref="U00010",
                           scope="case_level",
                           evidence_requirements=[
                               EvidenceType.WITHDRAWAL_RECORDS]),
        ],
        intent_source="llm")
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    state = AgentLoopState(task_id="T", investigation_id="I",
                           case_id="U00010", goal="g",
                           goal_contract=contract)
    plan = Plan(plan_id="P", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="retrieve_policy",
                 status=PlanStepStatus.PENDING,
                 tool_name="policy_lookup", arguments={}),
        PlanStep(step_id="S2", type="inspect_withdrawals",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"stream": "withdrawals", "finding_id": "F3"}),
    ], created_at=None)
    runner._supplement_plan_coverage(state, plan)
    types = [s.type for s in plan.steps]
    assert "retrieve_policy" not in types, types
    assert "inspect_withdrawals" in types, types


# --- EN-loose-artifact: unbound records-run artifacts are dropped ---------

def test_loose_unbound_artifacts_dropped_when_streams_bound(monkeypatch):
    """THE live shape (TASK-634a8cf6204b): re-plan carries the per-stream
    pairs AND two loose LLM artifacts — the loose ones (no declared
    producers; only cross-stream soup possible) are deterministically
    removed once every required record stream is bound-covered."""
    from app.models import Plan, PlanStep, PlanStepStatus
    refs = [
        {"step_id": "S2-F3", "type": "inspect_withdrawals",
         "status": "success",
         "arguments": {"stream": "withdrawals", "finding_id": "F3"}},
        {"step_id": "S3-F1", "type": "inspect_transactions",
         "status": "success",
         "arguments": {"stream": "transactions", "finding_id": "F1"}},
    ]
    llm_plan = Plan(plan_id="P-L", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="retrieve_policy",
                 status=PlanStepStatus.PENDING,
                 tool_name="policy_lookup", arguments={}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle", arguments={}),
        PlanStep(step_id="S3", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle", arguments={}),
        PlanStep(step_id="S4", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S2-F3"],
                            "source_streams": ["withdrawal_records"]}),
        PlanStep(step_id="S5", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S3-F1"],
                            "source_streams": ["transaction_records"]}),
    ], created_at=None)
    out = _run_en_replan(refs, llm_plan, monkeypatch)
    loose = [s for s in out.steps if s.type == "generate_artifact"
             and not (s.arguments or {}).get("source_step_ids")]
    assert not loose, [(s.step_id, s.arguments) for s in out.steps]
    streams = sorted((s.arguments or {}).get("source_streams") or []
                     for s in out.steps if s.type == "generate_artifact")
    assert streams == [["transaction_records"], ["withdrawal_records"]] or \
        streams == [["withdrawal_records"], ["transaction_records"]], streams


def test_non_records_contract_keeps_loose_artifact(monkeypatch):
    """A run whose contract has NO records-family requirement is outside
    the invariant — loose artifacts are kept (no over-reach)."""
    from app.models import Plan, PlanStep, PlanStepStatus
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    refs = [{"step_id": "S2-F3", "type": "inspect_withdrawals",
             "status": "success",
             "arguments": {"stream": "withdrawals"}}]
    llm_plan = Plan(plan_id="P-N", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle", arguments={}),
    ], created_at=None)
    state = _replan_state(refs, GoalContract(
        contract_id="GC-NR", subject_ref="U00010",
        outputs=[RequiredOutput(
            output_id="O1", claim="criteria", claim_kind="explanation",
            subject_ref="U00010", scope="case_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION])],
        intent_source="llm"))
    from app.config import settings as cfg
    monkeypatch.setattr(cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    from app.agent_loop import AgentLoopRunner
    runner = AgentLoopRunner.__new__(AgentLoopRunner)

    class _P:
        def plan(self, *a, **k):
            return llm_plan
    out = runner._re_plan(_P(), state, "g", None, [], None,
                          findings=None, goal_contract=state.goal_contract)
    assert any(s.type == "generate_artifact" for s in out.steps)


def test_failed_producer_never_referenced_by_stable_reference(monkeypatch):
    """A FAILED records producer is never reused as a stable reference —
    the run must re-execute the correct producer instead."""
    from app.models import PlanStepStatus
    refs = [{"step_id": "S9", "type": "inspect_transactions",
             "status": "failed",
             "arguments": {"stream": "transactions"}}]
    out = _run_en_replan(refs, _en_replan_llm_plan(), monkeypatch)
    for a in out.steps:
        if a.type == "generate_artifact":
            sids = (a.arguments or {}).get("source_step_ids") or []
            assert "S9" not in sids, a.arguments


# --- EN-explain-budget-order (2026-10-23): anchoring claim-variance gap ---
# Live trace (TASK-16545181b312): the LLM authored a GENERIC criteria claim
# ("Rule determination criteria") — _output_target_finding matched nothing
# (its tiers only read the CLAIM), the explain step executed unanchored
# (INVALID_ARGUMENT finding_id) and O1 stayed pending while every other
# step (records + files) succeeded. The claim text varies per LLM run;
# the USER'S OWN request always names the findings — resolution now falls
# back to the request words through the same governed tiers.

EN_ASK = ("What are the determination criteria for High Withdrawal "
          "Frequency and for New Account with High Activity? Generate the "
          "withdrawal detail file and the transaction detail file.")

def _u00010_findings():
    from types import SimpleNamespace
    titles = ["ML Pattern Detection — 99.41/100",
              "New account with high activity",
              "High withdrawal frequency",
              "First withdrawal to new address",
              "Shared Device Relationships",
              "Linked Account Network",
              "Trading Frequency (24h)",
              "Opposite Trade Ratio"]
    return [SimpleNamespace(finding_id=f"F{i}", title=t) for i, t in
            enumerate(titles, 1)]


def test_generic_en_claim_resolves_via_request_words():
    from types import SimpleNamespace
    from app.planner_v2 import _output_target_finding
    o = SimpleNamespace(claim="Rule determination criteria",
                        subject_ref="U00010", evidence_requirements=[])
    r = _output_target_finding(o, EN_ASK, _u00010_findings())
    assert r is not None and r.finding_id == "F3", r


def test_generic_zh_claim_resolves_via_request_words():
    from types import SimpleNamespace
    from app.planner_v2 import _output_target_finding
    o = SimpleNamespace(claim="规则判定条件", subject_ref="U00010",
                        evidence_requirements=[])
    r = _output_target_finding(
        o, "高频提现和新账号高频活动的判定条件分别是什么", _u00010_findings())
    assert r is not None and r.finding_id == "F3", r


def test_no_vocabulary_stays_unresolved():
    from types import SimpleNamespace
    from app.planner_v2 import _output_target_finding
    o = SimpleNamespace(claim="something unrelated", subject_ref="U00010",
                        evidence_requirements=[])
    assert _output_target_finding(o, "tell me about the weather",
                                  _u00010_findings()) is None


def test_anchoring_pins_live_unanchored_explain():
    """Integration shape: the live plan's unanchored explain step + the
    live fused-criteria contract get pinned to F3 by the per-output
    anchoring (so the producer executes instead of INVALID_ARGUMENT)."""
    from app.agent_loop import AgentLoopRunner, AgentLoopState
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    from app.models import Plan, PlanStep, PlanStepStatus
    contract = GoalContract(
        contract_id="GC-LIVE", subject_ref="U00010",
        outputs=[RequiredOutput(
            output_id="O1", claim="Rule determination criteria",
            claim_kind="explanation", subject_ref="U00010",
            scope="case_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION])],
        intent_source="llm")
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    state = AgentLoopState(task_id="T", investigation_id="I",
                           case_id="U00010", goal=EN_ASK,
                           goal_contract=contract)
    plan = Plan(plan_id="P", investigation_id="I", goal=EN_ASK, steps=[
        PlanStep(step_id="S1", type="fetch_case",
                 status=PlanStepStatus.PENDING,
                 tool_name="risk_case_fetch", arguments={}),
        PlanStep(step_id="S4", type="explain_signal",
                 status=PlanStepStatus.PENDING,
                 tool_name="signal_explain", arguments={}),
    ], created_at=None)
    from app.planner_v2 import _anchor_outputs_per_finding
    out = _anchor_outputs_per_finding(plan.steps, contract, EN_ASK,
                                      _u00010_findings())
    explains = [x for x in out if x.type == "explain_signal"]
    assert explains and all(
        x.arguments.get("finding_id") for x in explains), \
        [(x.step_id, x.arguments) for x in explains]


# --- EN-required-delivery-budget-guard: stream-first producer anchoring ---
# Live trace (TASK-09f78cc987a3): both transaction producer executions
# failed EVIDENCE_STREAM_NOT_SUPPORTED on F3 (High withdrawal frequency) —
# the anchoring resolved the producer via the claim/request domain tiers,
# whose FIRST match for the compound EN ask is the withdrawal finding.
# A records producer must anchor by the STREAM rule (the finding whose
# own evidence_refs carry that stream).

def _stream_findings():
    from types import SimpleNamespace

    class _Ref:
        def __init__(self, kind):
            self.kind = kind

    class _F:
        def __init__(self, fid, title, kinds):
            self.finding_id = fid
            self.title = title
            self.evidence_refs = [_Ref(k) for k in kinds]

    return [_F("F1", "ML Pattern Detection", ["transaction"] * 2),
            _F("F3", "High withdrawal frequency", ["withdrawal"] * 5)]


def _tx_contract():
    from app.goal_resolution import GoalContract, RequiredOutput, \
        EvidenceType
    return GoalContract(
        contract_id="GC-TX", subject_ref="U00010",
        outputs=[RequiredOutput(
            output_id="O4", claim="Transaction detail file export",
            claim_kind="retrieval", subject_ref="U00010",
            scope="case_level",
            evidence_requirements=[EvidenceType.TRANSACTION_RECORDS])],
        intent_source="llm")


def test_transaction_producer_anchors_by_stream_not_first_domain():
    from app.models import Plan, PlanStep, PlanStepStatus
    from app.planner_v2 import _anchor_outputs_per_finding
    steps = [PlanStep(step_id="S6", type="inspect_transactions",
                      status=PlanStepStatus.PENDING,
                      tool_name="finding_drilldown", arguments={})]
    out = _anchor_outputs_per_finding(steps, _tx_contract(), EN_ASK,
                                      _stream_findings())
    assert out[0].arguments.get("finding_id") == "F1", out[0].arguments


def test_withdrawal_producer_anchors_to_withdrawal_finding():
    from app.models import Plan, PlanStep, PlanStepStatus
    from app.planner_v2 import _anchor_outputs_per_finding
    steps = [PlanStep(step_id="S5", type="inspect_withdrawals",
                      status=PlanStepStatus.PENDING,
                      tool_name="finding_drilldown", arguments={})]
    out = _anchor_outputs_per_finding(steps, _tx_contract(), EN_ASK,
                                      _stream_findings())
    assert out[0].arguments.get("finding_id") == "F3", out[0].arguments


def test_supplement_stream_anchoring_matches_planner():
    """The loop-side coverage supplement resolves records producers by the
    SAME stream rule (both anchoring sites agree)."""
    from app.agent_loop import AgentLoopRunner, AgentLoopState
    from app.models import Plan, PlanStep, PlanStepStatus
    from app.planning_coverage import coverage_supplement_steps
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    state = AgentLoopState(task_id="T", investigation_id="I",
                           case_id="U00010", goal=EN_ASK,
                           goal_contract=_tx_contract())
    plan = Plan(plan_id="P", investigation_id="I", goal=EN_ASK, steps=[
        PlanStep(step_id="S1", type="generate_artifact",
                 status=PlanStepStatus.PENDING,
                 tool_name="artifact_bundle", arguments={})],
        created_at=None)
    specs = coverage_supplement_steps(_tx_contract(), plan.steps)
    assert specs and specs[0].step_type == "inspect_transactions"
    steps, _ = runner._anchored_supplement_steps(
        state, specs, _stream_findings(), 2, "coverage supplement")
    assert steps and steps[0].arguments.get("finding_id") == "F1", \
        steps[0].arguments

"""U00029 manual-acceptance regression — EN goal decomposition + Artifact
scope binding for the related-account set-internal hedging investigation.

Case A (EN, failed acceptance): "list all the linked accounts and check if
there is any hedge transaction within linked accounts. If hedge trades
exist, please send the details to me and generate an artifact."
  FAIL mode: goal_contract None → heuristic plan inspect_transactions +
  generate_artifact — the linked-account / hedge analyses never ran.

Case B (ZH, half-failed acceptance): 「列出所有关联账号，查询账号中是否有
对冲交易，如果有，列出交易明细并生成一份文件」
  conversation correct (17 linked accounts, 7 set-internal candidates) but
  ART-54dd4a7cc7's persisted content was U00029's PLAIN transaction table:
  the generate_artifact step declared source_step_ids=["S1"]
  (inspect_transactions) — authored by the R2a stream plan BEFORE the
  analyses existed — and artifact_bundle faithfully composed only its
  declared source.

Root fix: the set-internal hedging vocabulary (investigation_concepts)
now recognizes both phrasings, so the deterministic set-internal plan
(analyze_related_accounts → analyze_related_account_set_trades →
generate_artifact with NO restricting source_step_ids) fires for both —
the artifact composes from the ACTUAL analysis results.

These tests read the ARTIFACT'S PERSISTED CONTENT — never just the
artifact id / task status / tool invocation.
"""
import pytest

from app.domain_tools import risk_case_fetch
from app.domain_tools.artifact_bundle import artifact_bundle
from app.goal_resolution import deterministic_contract_proposal
from app.goal_resolution import (
    GoalRepresentation,
    GoalResolutionStatus,
    build_goal_contract,
)
from app.investigation_concepts import (
    has_account_delivery_intent,
    is_related_account_analysis_request,
    is_related_account_set_trade_analysis,
)
from app.models import (
    InvestigationContext,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
    ToolCallStatusV2,
)
from app.planner_v2 import PlannerV2
from app.skills import eligible_skills_for_finding

EN = ("list all the linked accounts and check if there is any hedge "
      "transaction within linked accounts. If hedge trades exist, please "
      "send the details to me and generate an artifact.")
ZH = ("列出所有关联账号，查询账号中是否有对冲交易，如果有，列出交易明细"
      "并生成一份文件")
PLAIN_ZH = "列出该账户的交易明细并生成一份文件"
CASE = "U00029"

# deterministic candidate fixture — the EXPECTED values come from the
# analysis payload (traceability: the artifact must carry THESE pairs and
# transaction ids, not a keyword match on "hedge")
CANDIDATES = [
    {"candidate_id": "C-1", "account_a": "U00029", "side_a": "BUY",
     "account_b": "U00337", "side_b": "SELL", "symbol": "BTC",
     "max_time_gap_seconds": 12.0, "value_a": 1200.0, "value_b": 1195.0,
     "value_difference": 5.0, "value_difference_relative": 0.004,
     "match_type": "opposite_trade",
     "transaction_ids_a": ["TX-A1"], "transaction_ids_b": ["TX-B1"]},
    {"candidate_id": "C-2", "account_a": "U00325", "side_a": "SELL",
     "account_b": "U00349", "side_b": "BUY", "symbol": "ETH",
     "max_time_gap_seconds": 8.0, "value_a": 300.0, "value_b": 299.5,
     "value_difference": 0.5, "value_difference_relative": 0.0017,
     "match_type": "opposite_trade",
     "transaction_ids_a": ["TX-A2"], "transaction_ids_b": ["TX-B2"]},
]

SET_ANALYSIS_DATA = {
    "analysis": "cross_account_trade_analysis",
    "analysis_scope": "related_account_set",
    "analysis_status": "MATCH_FOUND",
    "case_id": CASE, "target_account": CASE,
    "candidates": CANDIDATES,
    "candidates_found": 2, "candidates_emitted": 2,
    "accounts_analyzed": 17,
    "retrieval": {"truncated": False},
    "config": {"max_time_delta_seconds": 30.0,
               "value_tolerance_relative": 0.05,
               "value_tolerance_absolute_floor": 1.0},
}


def _findings():
    return list((risk_case_fetch(case_id=CASE).data or {}).get("findings")
                or [])


def _plan(text):
    planner = PlannerV2(_NoLLM())
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    return planner.plan(text, InvestigationContext(case_id=CASE), eligible,
                        finding_capabilities=None,
                        case_findings=_findings())


class _NoLLM:
    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        raise AssertionError("LLM must not be reached")


def _tc(name, tool_name, data, next_data_needed=None):
    return ToolCallV2(
        tool_call_id=f"TC-{name}", investigation_id="CASE:U00029",
        task_id="T-1", tool_name=tool_name, arguments={},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data,
                          next_data_needed=list(next_data_needed or [])))


def _set_trades_tc(status="MATCH_FOUND"):
    data = dict(SET_ANALYSIS_DATA)
    if status != "MATCH_FOUND":
        data["analysis_status"] = status
        data["candidates"] = []
    return _tc("SET", "cross_account_trade_analysis", data)


def _tx_tc(n=3):
    rows = [{"record_id": f"TX-P{i}", "timestamp": f"2026-07-20T1{i}:00:00",
             "summary": f"plain trade #{i}"} for i in range(1, n + 1)]
    return _tc("TX", "finding_drilldown", {
        "finding_id": "F1", "view": "evidence", "complete": True,
        "record_count": n,
        "streams": {"transactions_included": True},
        "records": rows})


# --- Test 1: EN EXPLICIT-artifact request (positive authorization control).
# The request carries explicit file intent ("generate an artifact"), which
# AUTHORIZES the generate_artifact step. Tests 1-2 pin the positive
# authorization path; the negative (no-artifact-intent) cases are tests
# 1n/2n below.

def test_en_hedge_request_plans_set_analysis_and_artifact():
    assert is_related_account_set_trade_analysis(EN)
    assert has_account_delivery_intent(EN)
    # the request itself carries explicit artifact intent — this is what
    # AUTHORIZES the generate_artifact step (never the branch template)
    from app.context_resolution import _classify_request_intent
    assert _classify_request_intent(EN) in (
        __import__("app.models", fromlist=["RequestIntent"])
        .RequestIntent.ARTIFACT_CASE,
        __import__("app.models", fromlist=["RequestIntent"])
        .RequestIntent.ARTIFACT_FINDING)
    proposal = deterministic_contract_proposal(EN, None, CASE)
    assert proposal is not None
    kinds = [e for o in proposal["required_outputs"]
             for e in o["evidence_requirements"]]
    assert "opposite_trade_relations" in kinds
    plan = _plan(EN)
    types = [s.type for s in plan.steps]
    # the skill's own planning_steps order; the CONTRACT is the set:
    # both analyses planned + the AUTHORIZED artifact composes from them
    assert sorted(types) == ["analyze_related_account_set_trades",
                             "analyze_related_accounts",
                             "generate_artifact"], types
    # the artifact composes from the ACTUAL analysis results: no
    # source_step_ids restriction to a plain-transactions step
    art = next(s for s in plan.steps if s.type == "generate_artifact")
    assert not art.arguments.get("source_step_ids"), art.arguments


# --- Test 2: ZH EXPLICIT-artifact counterpart (positive authorization
# control — 「生成一份文件」 authorizes the artifact step) --------------------

def test_zh_hedge_request_plans_same():
    assert is_related_account_set_trade_analysis(ZH)
    proposal = deterministic_contract_proposal(ZH, None, CASE)
    assert proposal is not None
    kinds = [e for o in proposal["required_outputs"]
             for e in o["evidence_requirements"]]
    assert "opposite_trade_relations" in kinds
    plan = _plan(ZH)
    types = [s.type for s in plan.steps]
    assert sorted(types) == ["analyze_related_account_set_trades",
                             "analyze_related_accounts",
                             "generate_artifact"], types
    art = next(s for s in plan.steps if s.type == "generate_artifact")
    assert not art.arguments.get("source_step_ids")


# --- Test 3: artifact content binds the set-trades analysis -----------------

def test_artifact_content_contains_hedge_candidates_from_analysis():
    r = artifact_bundle(
        scope="case", case_id=CASE, task_id="T-1",
        source_tool_calls=[_set_trades_tc(), _tx_tc()])
    assert r.outcome == ToolResultOutcome.SUCCESS
    content = r.data["artifact"]["content"]
    # "## Candidate Cross-Account Relationships" section present
    assert "## Candidate Cross-Account Relationships" in content
    # EVERY fixture candidate pair + its transaction ids are traceable
    for c in CANDIDATES:
        assert c["candidate_id"] in content, c["candidate_id"]
        assert c["account_a"] in content and c["account_b"] in content
        for tx in (c["transaction_ids_a"] + c["transaction_ids_b"]):
            assert tx in content, tx
    # the candidates section (not the plain-record table) is the answer:
    # it appears before any plain evidence table
    assert content.index("## Candidate Cross-Account Relationships") \
        < content.index("## Source Tool Calls")


def test_artifact_without_analysis_source_has_no_candidates():
    """The binding lever documented: without the analysis result among the
    sources, no candidates section can appear (the old S1-only binding's
    exact failure signature)."""
    r = artifact_bundle(scope="case", case_id=CASE, task_id="T-1",
                        source_tool_calls=[_tx_tc()])
    content = r.data["artifact"]["content"]
    assert "## Candidate Cross-Account Relationships" not in content


# --- Test 4: plain transaction-details artifact is unchanged ----------------

def test_plain_transactions_request_keeps_stream_plan_and_artifact():
    assert not is_related_account_set_trade_analysis(PLAIN_ZH)
    assert not is_related_account_analysis_request(PLAIN_ZH)
    plan = _plan(PLAIN_ZH)
    types = [s.type for s in plan.steps]
    assert types == ["inspect_transactions", "generate_artifact"], types
    art = plan.steps[1]
    assert art.arguments.get("source_step_ids") == \
        [plan.steps[0].step_id]
    r = artifact_bundle(scope="case", case_id=CASE, task_id="T-1",
                        source_tool_calls=[_tx_tc(n=2)])
    content = r.data["artifact"]["content"]
    assert "plain trade #1" in content
    assert "## Candidate Cross-Account Relationships" not in content


# --- Test 5: no candidates / insufficient data stay bounded -----------------

def test_no_match_artifact_is_bounded_not_fabricated():
    r = artifact_bundle(scope="case", case_id=CASE, task_id="T-1",
                        source_tool_calls=[_set_trades_tc("NO_MATCH")])
    content = r.data["artifact"]["content"]
    assert "No candidate relationships match within the completely "
    "retrieved analysis scope" in content
    for c in CANDIDATES:
        assert c["candidate_id"] not in content


def test_insufficient_data_artifact_is_not_a_no_match():
    r = artifact_bundle(scope="case", case_id=CASE, task_id="T-1",
                        source_tool_calls=[_set_trades_tc(
                            "INSUFFICIENT_DATA")])
    content = r.data["artifact"]["content"]
    assert "insufficient for a reliable conclusion" in content
    assert "authoritative zero" not in content

# --- Test 1n/2n: NO explicit artifact intent → NO artifact step, NO
# --- persisted artifact (the unsolicited-artifact regression, U00029) ----

EN_NEG = ("list all linked accounts and check if there is any hedge "
          "transaction within the linked accounts. If any, please send "
          "me the transaction details.")
ZH_NEG = ("列出所有关联账号，查询账号中是否有对冲交易，如果有，"
          "列出交易明细。")


@pytest.mark.parametrize("label,text", [
    ("EN", EN_NEG),
    ("ZH", ZH_NEG),
])
def test_no_artifact_intent_plans_analysis_only(label, text):
    """THE assertion that detects the old defect: the old unconditional
    hedge plan appended generate_artifact for these exact requests. The
    final effective plan must contain the two analysis steps and NO
    generate_artifact (delivery-intent authorization rule)."""
    from app.context_resolution import _classify_request_intent
    assert _classify_request_intent(text) not in (
        __import__("app.models", fromlist=["RequestIntent"])
        .RequestIntent.ARTIFACT_CASE,
        __import__("app.models", fromlist=["RequestIntent"])
        .RequestIntent.ARTIFACT_FINDING), "precondition: no artifact intent"
    plan = _plan(text)
    types = [s.type for s in plan.steps]
    assert "generate_artifact" not in types, types
    assert "analyze_related_account_set_trades" in types
    assert "analyze_related_accounts" in types


def test_coverage_admission_inserts_no_artifact_step():
    """Guard on the loop's coverage-supplement path: the negative
    request's contract outputs are covered by the two analysis steps and
    the admission must NOT insert a generate_artifact step (which would
    silently re-introduce the unsolicited artifact)."""
    from app.planning_coverage import (
        admit_plan_coverage, coverage_supplement_steps)
    proposal = deterministic_contract_proposal(EN_NEG, None, CASE)
    contract, err = build_goal_contract(
        GoalRepresentation(
            case_id=CASE,
            goal_resolution=GoalResolutionStatus.INFERRED.value,
            goal_type="cross_account_check",
            subject_type="case", subject_ref=CASE, source="heuristic",
            request_text=EN_NEG, contract_proposal=proposal),
        case_id=CASE)
    assert err is None
    plan_types = ["analyze_related_accounts",
                  "analyze_related_account_set_trades"]
    records = admit_plan_coverage(contract, plan_types)
    assert all(r.coverage_status == "covered" for r in records), records
    from app.models import PlanStep, PlanStepStatus
    existing = [PlanStep(step_id=f"S{i}", type=t,
                         status=PlanStepStatus.PENDING, tool_name="t",
                         arguments={})
                for i, t in enumerate(plan_types, 1)]
    inserted = coverage_supplement_steps(contract, existing)
    assert all(s.type != "generate_artifact" for s in inserted), inserted


def test_no_artifact_intent_no_persistent_artifact():
    """Loop-level: the negative EN request runs the two analyses and the
    answer presents candidates + transaction details, with NO artifact
    persisted (state.artifact_ids empty)."""
    from app.models import InvestigationContext
    from app.agent_loop import AgentLoopRunner, HeuristicDecisionProposer
    from app.context_resolution import ContextResolver
    import tempfile
    from app.task_store_v2 import TaskStoreV2
    from tests.test_api_investigations_v1 import rp_patches

    store = TaskStoreV2(tempfile.mkdtemp() + "/u00029noart.db")
    runner = AgentLoopRunner(
        store=store, resolver=ContextResolver(llm_assist=False),
        proposer=HeuristicDecisionProposer(),
        findings_provider=lambda: _findings())
    with rp_patches()[0], rp_patches()[1]:
        result = runner.run(
            EN_NEG, InvestigationContext(case_id=CASE),
            _findings(), investigation_id="INV-U29NOART",
            task_id="TASK-U29NOART",
            findings_provider=lambda: _findings())
    assert result.state.agent_status in ("completed", "stopped_by_limit")
    types = [s.get("type") for s in result.state.plan_steps]
    assert "generate_artifact" not in types, types
    assert result.state.artifact_ids == [], result.state.artifact_ids
    # the answer still presents the investigation results (the related-
    # account list under the mock RP); exact candidates are data, covered
    # by the real-data tests 1–3 and the real E2E
    assert "关联账号" in result.response or "Related accounts" in result.response

# --- B14 rule 2 (R2): coverage-inserted analysis steps must join the
# --- artifact's declared sources when they produce required evidence ----

def test_b14_rule2_revises_artifact_binding_after_coverage_insertion():
    """THE regression for the F4/U00029 acceptance failure: the adopted
    plan binds the artifact to the record step (S1); coverage later
    inserts the set-internal + related-account analyses. The revision
    must extend the artifact's declared sources with the inserted steps
    that produce the ARTIFACT OUTPUT's required evidence (per-output
    scoping, 2026-10-21 phase-1: the file output O2 requires
    opposite_trade_relations + transaction_records → the set-internal
    analysis joins; the network attribution is O3's CONVERSATION
    deliverable and must not leak into the file — U00010 F1 showed the
    union rule over-binding a withdrawal file to the transaction stream
    + explain steps). Declared sources are never removed; plans whose
    declared sources already cover the required evidence are untouched."""
    from app.agent_loop import AgentLoopRunner
    from app.models import PlanStep, PlanStepStatus
    from app.goal_resolution import GoalContract, GoalResolutionStatus, \
        RequiredOutput, EvidenceType
    runner = AgentLoopRunner.__new__(AgentLoopRunner)

    contract = GoalContract(
        contract_id="GC-B14", subject_ref=CASE,
        outputs=[
            RequiredOutput(output_id="O1",
                           claim="hedge existence", claim_kind="existence_check",
                           subject_ref=CASE, scope="case_level",
                           evidence_requirements=[
                               EvidenceType.OPPOSITE_TRADE_RELATIONS]),
            RequiredOutput(output_id="O2",
                           claim="hedge details file", claim_kind="retrieval",
                           subject_ref=CASE, scope="case_level",
                           evidence_requirements=[
                               EvidenceType.OPPOSITE_TRADE_RELATIONS,
                               EvidenceType.TRANSACTION_RECORDS]),
            RequiredOutput(output_id="O3",
                           claim="network attribution", claim_kind="retrieval",
                           subject_ref=CASE, scope="case_level",
                           evidence_requirements=[
                               EvidenceType.NETWORK_RELATIONS]),
        ], intent_source="llm")
    plan_steps = [
        PlanStep(step_id="S1", type="inspect_transactions",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "transactions"}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING, tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S1"]}),
    ]
    inserted = [
        PlanStep(step_id="S3", type="analyze_related_account_set_trades",
                 status=PlanStepStatus.PENDING,
                 tool_name="cross_account_trade_analysis", arguments={}),
        PlanStep(step_id="S4", type="analyze_related_accounts",
                 status=PlanStepStatus.PENDING,
                 tool_name="related_account_analysis", arguments={}),
    ]
    plan = type(plan_steps[0]).__class__  # placeholder no-op
    from app.models import Plan as PlanModel
    plan = PlanModel(plan_id="P", investigation_id="I", goal="g",
                     steps=plan_steps, created_at=None)
    runner._revise_artifact_source_bindings(plan, contract, inserted)
    art = next(s for s in plan.steps if s.type == "generate_artifact")
    src_ids = art.arguments["source_step_ids"]
    # the file output's required-evidence producer joins the declared
    # sources; the network-attribution step stays OUT (O3's deliverable)
    assert "S3" in src_ids, src_ids
    assert "S4" not in src_ids, src_ids
    assert "S1" in src_ids


def test_b14_rule2_no_revision_without_missing_evidence():
    """When the declared sources already provide every required evidence
    type, the binding is untouched (no scope creep)."""
    from app.agent_loop import AgentLoopRunner
    from app.models import PlanStep, PlanStepStatus, Plan as PlanModel
    from app.goal_resolution import GoalContract, GoalResolutionStatus, \
        RequiredOutput, EvidenceType
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    contract = GoalContract(
        contract_id="GC-X", subject_ref=CASE,
        outputs=[RequiredOutput(
            output_id="O1", claim="records", claim_kind="retrieval",
            subject_ref=CASE, scope="case_level",
            evidence_requirements=[EvidenceType.TRANSACTION_RECORDS])],
        intent_source="llm")
    steps = [
        PlanStep(step_id="S1", type="inspect_transactions",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "transactions"}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING, tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S1"]}),
    ]
    plan = PlanModel(plan_id="P", investigation_id="I", goal="g",
                     steps=steps, created_at=None)
    runner._revise_artifact_source_bindings(plan, contract, [])
    assert steps[1].arguments["source_step_ids"] == ["S1"]


# ---------------------------------------------------------------------------
# U00010 F4 (2026-10-21 phase-1): adoption-time per-output binding repair.
# The real E2E run (INV-a0dde7c3bc54 / TASK-ffcbb78972bb) authored the plan
#   [inspect_transactions(F1), analyze_related_account_set_trades,
#    analyze_related_accounts, generate_artifact(source_step_ids=["S1"])]
# — every output already covered, so coverage appended NOTHING and the
# inserted-steps-only revision never ran; the persisted artifact was the
# plain transaction table (no CATA- candidates).
# ---------------------------------------------------------------------------

def _f4_contract():
    from app.goal_resolution import (GoalContract, RequiredOutput,
                                     EvidenceType)
    return GoalContract(
        contract_id="GC-F4", subject_ref=CASE,
        outputs=[
            RequiredOutput(
                output_id="O1", claim="set-internal hedge candidates",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level",
                evidence_requirements=[
                    EvidenceType.OPPOSITE_TRADE_RELATIONS]),
            RequiredOutput(
                output_id="O2", claim="hedge detail file",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level",
                evidence_requirements=[
                    EvidenceType.OPPOSITE_TRADE_RELATIONS,
                    EvidenceType.TRANSACTION_RECORDS]),
            RequiredOutput(
                output_id="O3", claim="related-account network",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level",
                evidence_requirements=[EvidenceType.NETWORK_RELATIONS]),
        ],
        intent_source="llm")


def _f4_plan():
    from app.models import Plan, PlanStep, PlanStepStatus
    return Plan(plan_id="P-F4", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="inspect_transactions",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "transactions",
                            "finding_id": "F1"}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING, tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S1"]}),
        PlanStep(step_id="S3", type="analyze_related_account_set_trades",
                 status=PlanStepStatus.PENDING,
                 tool_name="cross_account_trade_analysis", arguments={}),
        PlanStep(step_id="S4", type="analyze_related_accounts",
                 status=PlanStepStatus.PENDING,
                 tool_name="related_account_analysis", arguments={}),
    ], created_at=None)


def test_f4_adoption_repair_joins_set_internal_analysis():
    """The file artifact bound to its own stream step gains the set-internal
    candidate analysis (its output's required evidence) at adoption."""
    from app.agent_loop import AgentLoopRunner
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    plan = _f4_plan()
    n = runner._repair_artifact_bindings_per_output(plan, _f4_contract())
    art = next(s for s in plan.steps if s.type == "generate_artifact")
    assert art.arguments["source_step_ids"] == ["S1", "S3"], \
        art.arguments["source_step_ids"]
    assert n == 1


def test_f4_adoption_repair_keeps_per_stream_artifacts_scoped():
    """U00010 F1 shape: one artifact PER stream stays scoped — the withdrawal
    file never gains the transaction stream or the rule-explanation steps,
    and unanchored explain steps never join any file."""
    from app.agent_loop import AgentLoopRunner
    from app.models import Plan, PlanStep, PlanStepStatus
    from app.goal_resolution import (GoalContract, RequiredOutput,
                                     EvidenceType)
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    contract = GoalContract(
        contract_id="GC-F1", subject_ref="U00010",
        outputs=[
            RequiredOutput(output_id="O1", claim="提现判定条件",
                           claim_kind="explanation", subject_ref="U00010",
                           scope="case_level",
                           evidence_requirements=[
                               EvidenceType.RULE_EXPLANATION]),
            RequiredOutput(output_id="O2", claim="新账号判定条件",
                           claim_kind="explanation", subject_ref="U00010",
                           scope="case_level",
                           evidence_requirements=[
                               EvidenceType.RULE_EXPLANATION]),
            RequiredOutput(output_id="O3", claim="提现明细文件",
                           claim_kind="retrieval", subject_ref="U00010",
                           scope="case_level",
                           evidence_requirements=[
                               EvidenceType.WITHDRAWAL_RECORDS]),
            RequiredOutput(output_id="O4", claim="交易明细文件",
                           claim_kind="retrieval", subject_ref="U00010",
                           scope="case_level",
                           evidence_requirements=[
                               EvidenceType.TRANSACTION_RECORDS]),
        ],
        intent_source="llm")
    plan = Plan(plan_id="P-F1", investigation_id="I", goal="g", steps=[
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
        PlanStep(step_id="S4", type="generate_artifact",
                 status=PlanStepStatus.PENDING, tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S3"]}),
        PlanStep(step_id="S5", type="explain_signal",
                 status=PlanStepStatus.PENDING,
                 tool_name="signal_explain", arguments={}),
    ], created_at=None)
    n = runner._repair_artifact_bindings_per_output(plan, contract)
    arts = [s for s in plan.steps if s.type == "generate_artifact"]
    assert arts[0].arguments["source_step_ids"] == ["S1"]
    assert arts[1].arguments["source_step_ids"] == ["S3"]
    assert n == 0


def test_f4_adoption_repair_no_matching_output_untouched():
    """Declared sources matching NO contract output are never revised
    (no guessed scope)."""
    from app.agent_loop import AgentLoopRunner
    from app.models import Plan, PlanStep, PlanStepStatus
    from app.goal_resolution import (GoalContract, RequiredOutput,
                                     EvidenceType)
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    contract = GoalContract(
        contract_id="GC-X", subject_ref=CASE,
        outputs=[RequiredOutput(
            output_id="O1", claim="network", claim_kind="retrieval",
            subject_ref=CASE, scope="case_level",
            evidence_requirements=[EvidenceType.NETWORK_RELATIONS])],
        intent_source="llm")
    plan = Plan(plan_id="P-X", investigation_id="I", goal="g", steps=[
        PlanStep(step_id="S1", type="inspect_transactions",
                 status=PlanStepStatus.PENDING,
                 tool_name="finding_drilldown",
                 arguments={"view": "evidence", "stream": "transactions"}),
        PlanStep(step_id="S2", type="generate_artifact",
                 status=PlanStepStatus.PENDING, tool_name="artifact_bundle",
                 arguments={"source_step_ids": ["S1"]}),
        PlanStep(step_id="S3", type="analyze_related_accounts",
                 status=PlanStepStatus.PENDING,
                 tool_name="related_account_analysis", arguments={}),
    ], created_at=None)
    n = runner._repair_artifact_bindings_per_output(plan, contract)
    assert plan.steps[1].arguments["source_step_ids"] == ["S1"]
    assert n == 0


def test_f4_adoption_path_repairs_when_coverage_appends_nothing(monkeypatch):
    """THE regression: coverage found every output covered (specs empty)
    and the old code returned BEFORE any repair — the mis-bound artifact
    shipped. The supplement entry point must repair regardless."""
    from app.config import settings as cfg
    from app.agent_loop import AgentLoopRunner, AgentLoopState
    monkeypatch.setattr(cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    runner = AgentLoopRunner.__new__(AgentLoopRunner)
    state = AgentLoopState(
        task_id="T", investigation_id="I", case_id=CASE, goal="g",
        goal_contract=_f4_contract())
    plan = _f4_plan()
    runner._supplement_plan_coverage(state, plan)
    art = next(s for s in plan.steps if s.type == "generate_artifact")
    assert art.arguments["source_step_ids"] == ["S1", "S3"], \
        art.arguments["source_step_ids"]

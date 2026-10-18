"""R2a — per-stream artifact delivery: planning, scoping, satisfaction."""
import json

from app.config import settings
from app.domain_tools import risk_case_fetch
from app.models import InvestigationContext
from app.planner_v2 import PlannerV2
from app.skills import eligible_skills_for_finding

Q = "把提现明细和交易明细生成 artifacts"


def _findings():
    return list((risk_case_fetch(case_id="U00008").data or {}).get("findings") or [])


def test_stream_artifact_plan_anchors_both_streams():
    findings = _findings()
    planner = PlannerV2(_NoLLM())
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    plan = planner.plan(Q, InvestigationContext(case_id="U00008"), eligible,
                        finding_capabilities=None, case_findings=findings)
    types = [s.type for s in plan.steps]
    # interleaved per-stream delivery (spec §四.2)
    assert types == ["inspect_withdrawals", "generate_artifact",
                     "inspect_transactions", "generate_artifact"]
    w, t = plan.steps[0], plan.steps[2]
    assert w.arguments["stream"] == "withdrawals" and w.arguments["finding_id"]
    assert t.arguments["stream"] == "transactions" and t.arguments["finding_id"]
    w_anchor, t_anchor = w.arguments["finding_id"], t.arguments["finding_id"]
    fw = next(f for f in findings if f.finding_id == w_anchor)
    assert any(r.kind == "withdrawal" for r in (fw.evidence_refs or []))
    ft = next(f for f in findings if f.finding_id == t_anchor)
    assert any(r.kind == "transaction" for r in (ft.evidence_refs or []))
    assert plan.steps[1].arguments["source_step_ids"] == [w.step_id]
    assert plan.steps[3].arguments["source_step_ids"] == [t.step_id]


def test_no_anchor_or_no_stream_keeps_generic_plan():
    findings = _findings()
    planner = PlannerV2(_NoLLM())
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    # no stream named → generic artifact plan (fetch_case + generate_artifact)
    plan = planner.plan("把调查结果生成 artifacts",
                        InvestigationContext(case_id="U00008"), eligible,
                        finding_capabilities=None, case_findings=findings)
    assert [s.type for s in plan.steps] == ["fetch_case", "generate_artifact"]


class _NoLLM:
    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        raise AssertionError("LLM must not be reached")


# --- R2a closure: terminal GoalOutcome persistence ---------------------------

class TestTerminalGoalOutcomePersistence:
    """The terminal GoalOutcome must be PERSISTED (previously the composer
    wrote it only after the terminal persist, so blocked/failed terminals
    stored NULL). Same derive_goal_outcome semantics as the composer."""

    def _terminate_and_reload(self, tmp_path, satisfactions, status):
        from datetime import datetime, timezone

        from app.agent_loop import AgentLoopRunner, AgentLoopState
        from app.goal_resolution import (
            ClaimKind,
            EvidenceType,
            GoalContract,
            OutputSatisfaction,
            RequiredOutput,
            ScopeLevel,
        )
        from app.models import TaskV2, TaskStatusV2
        from app.task_store_v2 import TaskStoreV2

        store = TaskStoreV2(db_path=tmp_path / "terminal.sqlite3")
        runner = AgentLoopRunner(store=store)
        contract = GoalContract(
            contract_id="GC-TERM", goal_type=None, subject_ref="U00047",
            outputs=[
                RequiredOutput(
                    output_id=f"O{i+1}", claim=f"deliverable {i+1}",
                    claim_kind=ClaimKind.RETRIEVAL, subject_ref="U00047",
                    scope=ScopeLevel.CASE_LEVEL,
                    evidence_requirements=[EvidenceType.NETWORK_RELATIONS],
                    satisfaction=sat)
                for i, sat in enumerate(satisfactions)])
        state = AgentLoopState(
            task_id="TASK-TERM", investigation_id="INV-TERM",
            case_id="U00047", goal="g", goal_contract=contract,
            agent_status="running", stop_reason=None,
            criteria_kind="question_answered", observations=[],
            plan_steps=[], step_output_map={})
        task = TaskV2(task_id="TASK-TERM", investigation_id="INV-TERM",
                      user_request="g", type="agent_loop",
                      status=TaskStatusV2.PLANNING)
        runner._terminate(state, task, status, "no_viable_action",
                          "resp",
                          lambda: datetime.now(timezone.utc).isoformat())
        return store.get_agent_state("TASK-TERM"), contract

    def test_a_blocked_partial_outcome_persisted(self, tmp_path):
        from app.agent_loop import AgentStatus
        from app.goal_resolution import OutputSatisfaction
        loaded, contract = self._terminate_and_reload(
            tmp_path,
            [OutputSatisfaction.SATISFIED, OutputSatisfaction.PENDING],
            AgentStatus.BLOCKED)
        # persisted (re-read from the store), not just in-memory
        assert loaded.goal_outcome == "blocked"
        # deliverable states preserved verbatim
        got = [(o.output_id, o.satisfaction.value)
               for o in loaded.goal_contract.outputs]
        assert got == [("O1", "satisfied"), ("O2", "pending")]

    def test_b_healthy_complete_outcome_persisted(self, tmp_path):
        from app.agent_loop import AgentStatus
        from app.goal_resolution import OutputSatisfaction
        loaded, _ = self._terminate_and_reload(
            tmp_path,
            [OutputSatisfaction.SATISFIED, OutputSatisfaction.SATISFIED],
            AgentStatus.COMPLETED)
        assert loaded.goal_outcome == "complete"


# --- R3: shared-observation detail dedup (composer level) --------------------

class TestR3SharedObservationDedup:
    """One observation serving O2 (existence_check) + O3 (detail
    retrieval): the full candidate list renders ONCE (in O3); O2 keeps its
    verdict + a reference; facts (counts, criteria, budget) survive."""

    def _compose(self, tmp_path, candidate):
        from app.agent_loop import AgentLoopRunner, AgentLoopState, \
            compose_loop_response
        from app.goal_resolution import (
            ClaimKind, EvidenceType, GoalContract, RequiredOutput,
            ScopeLevel)
        from app.models import TaskV2, TaskStatusV2
        from app.task_store_v2 import TaskStoreV2

        def out(oid, claim, kind, ev):
            return RequiredOutput(
                output_id=oid, claim=claim, claim_kind=kind,
                subject_ref="U00008", scope=ScopeLevel.CASE_LEVEL,
                evidence_requirements=[ev], satisfaction=sat_map[oid])
        sat_map = {"O1": __import__("app.goal_resolution",
                                    fromlist=["OutputSatisfaction"])
                   .OutputSatisfaction.SATISFIED,
                   "O2": __import__("app.goal_resolution",
                                    fromlist=["OutputSatisfaction"])
                   .OutputSatisfaction.PARTIALLY_SUPPORTED,
                   "O3": __import__("app.goal_resolution",
                                    fromlist=["OutputSatisfaction"])
                   .OutputSatisfaction.PARTIALLY_SUPPORTED}
        contract = GoalContract(
            contract_id="GC-R3", goal_type=None, subject_ref="U00008",
            outputs=[
                out("O1", "related accounts list", "retrieval",
                    EvidenceType.NETWORK_RELATIONS),
                out("O2", "hedging existence within set",
                    "existence_check",
                    EvidenceType.OPPOSITE_TRADE_RELATIONS),
                out("O3", "hedging candidate details", "retrieval",
                    EvidenceType.OPPOSITE_TRADE_RELATIONS),
            ])
        # real observation summary shape (from the verified Q4 run,
        # trimmed to one candidate) — the deep renderer requires the
        # analysis_scope/budget fields to emit the verdict block
        real_summary = {
            "candidates": [candidate],
            "candidates_found": 1, "candidates_emitted": 1,
            "config": {"max_time_delta_seconds": 30,
                       "value_tolerance_relative": 0.05,
                       "value_tolerance_absolute_floor": 1.0},
            "analysis_scope": "related_account_set",
            "target_account": "U00008",
            "accounts_analyzed": ["U00008", "U00305", "U00311"],
            "max_time_delta_seconds": 30,
            "config": {"max_time_delta_seconds": 30,
                       "value_tolerance_relative": 0.05,
                       "value_tolerance_absolute_floor": 1.0},
            "data_quality": {},
            "totals": {"transactions_retrieved": 1000},
            "retrieval": {"status": "truncated", "truncated": True,
                          "truncated_reasons": [
                              "account_set:max_pages_reached"]},
        }
        shared = {"tool_name": "cross_account_trade_analysis",
                  "step_type": "analyze_related_account_set_trades",
                  "outcome": "success", "summary": real_summary,
                  "error": None, "complete": None,
                  "next_data_needed": None,
                  "for_outputs": ["O2", "O3"],
                  "actual_evidence_types": ["opposite_trade_relations"],
                  "association_reason": "contract_step_mapping"}
        store = TaskStoreV2(db_path=str(tmp_path / "r3.db"))
        runner = AgentLoopRunner(store=store)
        state = AgentLoopState(
            task_id="TASK-R3", investigation_id="INV-R3",
            case_id="U00008", goal="Q4", goal_contract=contract,
            agent_status="completed", stop_reason="goal_satisfied",
            observations=[shared], plan_steps=[], step_output_map={})
        task = TaskV2(task_id="TASK-R3", investigation_id="INV-R3",
                      user_request="Q4", type="agent_loop",
                      status=TaskStatusV2.COMPLETED)
        runner._persist(state, task)
        previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            text = compose_loop_response(state)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
        return text

    def test_candidate_details_render_once_with_verdicts_kept(
            self, tmp_path):
        candidate = {
            "candidate_id": "CATA-001", "account_a": "U00305",
            "side_a": "BUY", "symbol": "SOL",
            "value_a": 352.739,
            "account_b": "U00311", "side_b": "SELL",
            "value_b": 350.265,
            "max_time_gap_seconds": 0.001192,
            "value_difference_relative": 0.007,
            "transaction_ids_a": ["T024700"],
            "transaction_ids_b": ["T025031"],
            "match_type": "1_to_1", "rank": 1,
            "value_difference": 2.0,
            "timestamps": ["t1", "t2"],
            "supporting": [],
        }
        text = self._compose(tmp_path, candidate)
        # full detail appears EXACTLY once (pair-detail line is singular)
        assert text.count("CATA-001") == 1
        assert text.count("↔") == 1
        # O3 detail payload intact: both accounts + transaction ids present
        assert "U00305" in text and "U00311" in text
        assert "T024700" in text and "T025031" in text
        # O2 verdict kept: existence finding line visible
        assert "候选" in text
        # explicit reference from the non-owner section to the owner
        assert "完整候选明细见" in text
        # hedge framing preserved: candidates ≠ proven real hedging
        assert "不构成对交易意图的结论" in text or "调查线索" in text

    def test_distinct_observations_both_render(self, tmp_path):
        """Two outputs each owning a DISTINCT observation never hide one
        another (no over-dedup)."""
        candidate_a = {"candidate_id": "CATA-A", "account_a": "A1",
                       "account_b": "B1", "symbol": "X",
                       "value_a": 1, "value_b": 1,
                       "transaction_ids_a": ["TA"],
                       "transaction_ids_b": ["TB"]}
        text = self._compose(tmp_path, candidate_a)
        assert "CATA-A" in text


# --- Q2 closure: outcome wording + honest failure-class disclosure -----------

class TestQ2DisclosureAndWording:
    def _compose_degraded(self, tmp_path, satisfactions, diagnostics=None):
        from app.agent_loop import AgentLoopRunner, AgentLoopState, \
            compose_loop_response
        from app.goal_resolution import (
            ClaimKind, EvidenceType, GoalContract, OutputSatisfaction,
            RequiredOutput, ScopeLevel)
        from app.models import TaskV2, TaskStatusV2
        from app.task_store_v2 import TaskStoreV2

        contract = GoalContract(
            contract_id="GC-Q2C", goal_type=None, subject_ref="U00008",
            intent_source="heuristic",
            outputs=[
                RequiredOutput(
                    output_id=f"O{i+1}", claim=f"d{i+1}",
                    claim_kind=ClaimKind.RETRIEVAL, subject_ref="U00008",
                    scope=ScopeLevel.CASE_LEVEL,
                    evidence_requirements=[EvidenceType.NETWORK_RELATIONS],
                    satisfaction=sat)
                for i, sat in enumerate(satisfactions)])
        store = TaskStoreV2(db_path=str(tmp_path / "q2c.sqlite3"))
        runner = AgentLoopRunner(store=store)
        state = AgentLoopState(
            task_id="TASK-Q2C", investigation_id="INV-Q2C",
            case_id="U00008", goal="g", goal_contract=contract,
            agent_status="completed", stop_reason="goal_satisfied",
            observations=[], plan_steps=[], step_output_map={},
            goal_understanding_diagnostics=diagnostics)
        task = TaskV2(task_id="TASK-Q2C", investigation_id="INV-Q2C",
                      user_request="g", type="agent_loop",
                      status=TaskStatusV2.COMPLETED)
        runner._persist(state, task)
        previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            text = compose_loop_response(state)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
        return text, state

    def test_a_all_satisfied_degraded_no_fake_unfinished(self, tmp_path):
        from app.goal_resolution import OutputSatisfaction
        text, state = self._compose_degraded(
            tmp_path, [OutputSatisfaction.SATISFIED,
                       OutputSatisfaction.SATISFIED],
            diagnostics={"llm_error":
                         "JSONDecodeError: Unterminated string",
                         "structured_output": {
                             "error_class": "json_parse"}})
        # GoalOutcome protection intact (B-closure PARTIAL cap)
        assert state.goal_outcome == "partial"
        # no fabricated unfinished items
        assert "部分项目未能完整回答" not in text
        assert "已识别的交付项均已回答" in text
        # honest failure-class disclosure (parse failure, NOT service)
        assert "未能成功解析" in text
        assert "服务暂时不可用" not in text
        assert "无法确认已覆盖您请求的全部内容" in text

    def test_b_pending_output_keeps_concrete_partial(self, tmp_path):
        from app.goal_resolution import OutputSatisfaction
        text, state = self._compose_degraded(
            tmp_path, [OutputSatisfaction.SATISFIED,
                       OutputSatisfaction.PENDING],
            diagnostics={"llm_error":
                         "JSONDecodeError: Unterminated string"})
        assert state.goal_outcome == "partial"
        assert "部分回答" in text

    def test_c_error_classes_drive_wording(self, tmp_path):
        from app.goal_resolution import OutputSatisfaction
        from app.agent_loop import _gu_failure_class
        cases = {
            "LLMEmptyResponseError: x": "empty",
            "LLMTimeoutError: x": "timeout",
            "LLMRateLimitError: x": "rate_limit",
            "LLMError: gateway 502": "service_unavailable",
        }
        for err, cls in cases.items():
            assert _gu_failure_class({"llm_error": err}) == cls
        # insufficient diagnostics → uncertainty, never invented cause
        text, _ = self._compose_degraded(
            tmp_path, [OutputSatisfaction.SATISFIED], diagnostics=None)
        assert "无法确认更具体的技术原因" in text
        assert "服务暂时不可用" not in text
        # JSON parse failure is NOT rendered as service unavailability
        text, _ = self._compose_degraded(
            tmp_path, [OutputSatisfaction.SATISFIED],
            diagnostics={"llm_error": "JSONDecodeError: bad json"})
        assert "未能成功解析" in text and "暂时不可用" not in text

    def test_d_activity_vs_outcome_consistency(self, tmp_path):
        from app.goal_resolution import OutputSatisfaction
        text, state = self._compose_degraded(
            tmp_path, [OutputSatisfaction.SATISFIED,
                       OutputSatisfaction.SATISFIED])
        # execution terminal (stop_reason goal_satisfied) and the outcome
        # line must not both claim full goal completion
        assert state.stop_reason == "goal_satisfied"
        assert state.goal_outcome == "partial"
        assert "已完整回答" not in text


# --- P1: explicit records-file requests route to per-stream planning --------

class TestP1RecordsFileIntent:
    def test_classification_matrix(self):
        from app.context_resolution import (
            RequestIntent, _classify_request_intent)
        artifact_cases = [
            "给我生成00008这个账户的提现明细和交易明细文件",
            "把提现明细和交易明细生成 artifacts",
            "导出提现和交易明细文件",
        ]
        for q in artifact_cases:
            assert _classify_request_intent(q) == RequestIntent.ARTIFACT_CASE, q
        investigation_cases = [
            "这个文件是什么时候创建的",
            "创建这个文件做什么用",
            "提现明细里有哪些记录",       # asking content, not a file export
        ]
        for q in investigation_cases:
            assert _classify_request_intent(q) == RequestIntent.INVESTIGATION, q

    def test_file_phrasing_plans_both_scoped_streams(self):
        from app.domain_tools import risk_case_fetch
        from app.models import InvestigationContext
        from app.planner_v2 import PlannerV2
        from app.skills import eligible_skills_for_finding
        findings = list((risk_case_fetch(
            case_id="U00008").data or {}).get("findings") or [])
        planner = PlannerV2(_NoLLM())
        eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
        plan = planner.plan(
            "给我生成00008这个账户的提现明细和交易明细文件",
            InvestigationContext(case_id="U00008"), eligible,
            finding_capabilities=None, case_findings=findings)
        types = [s.type for s in plan.steps]
        assert types == ["inspect_withdrawals", "generate_artifact",
                         "inspect_transactions", "generate_artifact"]
        w, t = plan.steps[0], plan.steps[2]
        assert w.arguments["stream"] == "withdrawals" and w.arguments["finding_id"]
        assert t.arguments["stream"] == "transactions" and t.arguments["finding_id"]
        assert plan.steps[1].arguments["source_step_ids"] == [w.step_id]
        assert plan.steps[3].arguments["source_step_ids"] == [t.step_id]
        assert not any(s.type == "inspect_evidence" for s in plan.steps)


# --- B: related-account-set scope recognition ("关联账号间") -----------------

class TestBSetInternalScope:
    def test_a_marker_classification(self):
        from app.investigation_concepts import (
            is_related_account_set_trade_analysis as f)
        set_internal = [
            "列出所有关联账号，查询关联账号间是否存在对冲交易，"
            "如果有，给我列出交易明细",          # the exact failing phrasing
            "查询关联账户之间是否有对冲",
            "其他关联账号之间有对冲交易吗",
            "查看关联账户间的反向交易",
            "这些关联账号之间是否存在对冲候选",
        ]
        for q in set_internal:
            assert f(q) is True, q
        target_anchored = [
            "查询 U00008 与关联账号之间是否存在对冲交易",
            "U00008 和其他账户之间是否有对冲候选",
            "U00008 是否和其他账户存在对冲",
        ]
        for q in target_anchored:
            assert f(q) is False, q

    def test_b_contract_keeps_set_internal_scope(self, monkeypatch):
        from app.config import settings as _s
        monkeypatch.setattr(_s, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        """The exact failing request: the contract must keep the related-
        account LIST and the SET-INTERNAL hedging deliverable — never
        narrowed to the case-account-anchored candidates only."""
        from app.goal_resolution import (
            _CLAIM_HEDGING, _CLAIM_HEDGING_SET, understand_goal,
            build_goal_contract)
        Q = ("列出所有关联账号，查询关联账号间是否存在对冲交易，"
             "如果有，给我列出交易明细")
        from tests.test_p0b_contract_completeness_v1 import (
            ScriptedGoalLLM, _payload)
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        rep = understand_goal(Q, case_id="U00008", llm=llm,
                              case_findings=[])
        from tests.test_p0b_contract_completeness_v1 import _claims
        contract, status = build_goal_contract(rep, "U00008")
        claims = _claims(contract)
        assert any("关联账号网络归因" in c for c in claims)      # list kept
        assert any(c == _CLAIM_HEDGING_SET for c in claims), claims
        assert _CLAIM_HEDGING not in claims                     # not narrowed
        # even when the LLM review proposes the TARGET-ANCHORED variant,
        # the deterministic candidate re-admission restores set-internal
        llm2 = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_target_anchored"]))
        rep2 = understand_goal(Q, case_id="U00008", llm=llm2,
                               case_findings=[])
        from tests.test_p0b_contract_completeness_v1 import _claims as _cl2
        c2, _ = build_goal_contract(rep2, "U00008")
        assert any(c == _CLAIM_HEDGING_SET for c in _cl2(c2))

    def test_c_planning_executes_set_internal_analysis(self):
        from app.domain_tools import risk_case_fetch
        from app.models import InvestigationContext
        from app.planner_v2 import PlannerV2
        from app.skills import eligible_skills_for_finding
        findings = list((risk_case_fetch(
            case_id="U00008").data or {}).get("findings") or [])
        planner = PlannerV2(_NoLLM())
        eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
        plan = planner.plan(
            "列出所有关联账号，查询关联账号间是否存在对冲交易，"
            "如果有，给我列出交易明细",
            InvestigationContext(case_id="U00008"), eligible,
            finding_capabilities=None, case_findings=findings)
        types = [s.type for s in plan.steps]
        assert "analyze_related_accounts" in types          # the list
        assert "analyze_related_account_set_trades" in types
        assert "analyze_cross_account_trades" not in types   # never narrowed


# --- C1: artifact idempotency across re-plans --------------------------------

def _executed_call(call_id, data):
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome
    return ToolCallV2(
        tool_call_id=call_id, investigation_id="INV-C1",
        task_id="TASK-C1", tool_name="analyze_related_account_set_trades",
        status="success",
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data))


def _cand_data(candidate_id, acct_a, acct_b, tx_a, tx_b):
    return {"analysis": "cross_account_trade_analysis",
            "analysis_status": "MATCH_FOUND",
            "analysis_scope": "related_account_set",
            "candidates_found": 1, "candidates_emitted": 1,
            "config": {"max_time_delta_seconds": 30,
                       "value_tolerance_relative": 0.05,
                       "value_tolerance_absolute_floor": 1.0},
            "candidates": [{
                "candidate_id": candidate_id, "account_a": acct_a,
                "account_b": acct_b, "symbol": "DOT", "side_a": "BUY",
                "side_b": "SELL", "value_a": 350.0, "value_b": 348.0,
                    "value_difference": 2.0, "value_difference_relative": 0.006,
                "max_time_gap_seconds": 0.0012,
                "value_difference": 2.0,
                "value_difference_relative": 0.006,
                "timestamps": ["t1", "t2"],
                "transaction_ids_a": [tx_a], "transaction_ids_b": [tx_b],
                "supporting": [
                    {"transaction_id": tx_a, "account_id": acct_a},
                    {"transaction_id": tx_b, "account_id": acct_b}],
            }]}


class TestC1ArtifactIdempotency:
    def _bundle(self, executed, prior_artifacts):
        from app.domain_tools.artifact_bundle import artifact_bundle
        steps = sorted({tc.tool_call_id.split("-")[1]
                        for tc in executed})
        return artifact_bundle(
            scope="case", case_id="U00008", task_id="TASK-C1",
            source_tool_calls=executed,
            source_step_ids=steps,
            prior_artifacts=prior_artifacts)

    def test_a_same_evidence_reuses_prior_artifact(self):
        executed = [_executed_call("TC-S3-r2", _cand_data(
            "CATA-009", "U00305", "U00311", "T024700", "T025031"))]
        prior = [{"artifact_id": "ART-first", "title": "Cross-Account "
                  "Trade Evidence — U00008",
                  "evidence_fingerprint": None, "content": "x"}]
        r1 = self._bundle(executed, [])
        fp = r1.data["evidence_fingerprint"]
        prior[0]["evidence_fingerprint"] = fp
        # re-plan: DIFFERENT tool-call id, SAME semantic evidence
        r2 = self._bundle(executed, prior)
        assert r2.data["artifact_reused"] is True
        assert r2.data["artifact_id"] == "ART-first"   # reused, not re-made
        assert r2.outcome.value == "success"

    def test_b_semantic_identity_ignores_call_ids(self):
        """Different candidate_id (order-generated) and different tool-call
        IDs, but the SAME underlying account pair + supporting transactions
        → same fingerprint → reuse."""
        executed1 = [_executed_call("TC-S3-a", _cand_data(
            "CATA-001", "U00305", "U00311", "T024700", "T025031"))]
        executed2 = [_executed_call("TC-S3-b", _cand_data(
            "CATA-009", "U00305", "U00311", "T024700", "T025031"))]
        fp1 = self._bundle(executed1, []).data["evidence_fingerprint"]
        fp2 = self._bundle(executed2, []).data["evidence_fingerprint"]
        assert fp1 == fp2 and fp1 is not None

    def test_c_new_evidence_yields_new_artifact(self):
        executed1 = [_executed_call("TC-S3-a", _cand_data(
            "CATA-001", "U00305", "U00311", "T024700", "T025031"))]
        fp1 = self._bundle(executed1, []).data["evidence_fingerprint"]
        prior = [{"artifact_id": "ART-first",
                  "evidence_fingerprint": fp1, "content": "x"}]
        executed2 = [_executed_call("TC-S3-b", _cand_data(
            "CATA-002", "U00329", "U00341", "T025872", "T026469"))]
        r2 = self._bundle(executed2, prior)   # genuinely new evidence
        assert r2.data.get("artifact_reused") is not True
        assert r2.data["artifact"]["artifact_id"] != "ART-first"

    def test_e_different_deliverables_not_merged(self):
        w = [_executed_call("TC-S1-a", {
            "view": "evidence", "stream": "withdrawals", "finding_id": "F3",
            "complete": True,
            "records": [{"record_id": "W000072"},
                        {"record_id": "W000077"}]})]
        t = [_executed_call("TC-S3-a", _cand_data(
            "CATA-001", "U00305", "U00311", "T024700", "T025031"))]
        fpw = self._bundle(w, []).data["evidence_fingerprint"]
        fpt = self._bundle(t, []).data["evidence_fingerprint"]
        assert fpw != fpt


# --- Artifact scope integrity: stream-scoped feature filtering ---------------

class TestEvidenceFeatureScope:
    def test_withdrawal_scope_excludes_cross_stream_features(self):
        from app.adapters.risk_platform import normalize_evidence_records
        evidence = {
            "user_id": "U00008", "finding_id": "F3",
            "withdrawal_evidence": [
                {"record_id": "W000072", "timestamp": "t1",
                 "summary": "withdrawal 1.94 BTC to new address"},
            ],
            "feature_evidence": {
                "withdrawal_frequency_24h": 10,
                "withdrawal_volume_24h": 3.5,
                "withdrawal_risk_score": 0.8,
                "trade_frequency_24h": 69,
                "trade_volume_24h": 12.5,
                "opposite_trade_ratio": 0.34,
                "shared_device_count": 1,
                "linked_account_count": 18,
                "account_age_days": 19,
            },
        }
        r = normalize_evidence_records(
            case_id="U00008", evidence=evidence,
            finding=next(f for f in _findings() if f.finding_id == "F3"),
            stream="withdrawals")
        feats = r["risk_features"]
        assert set(feats) <= {"withdrawal_frequency_24h",
                              "withdrawal_volume_24h",
                              "withdrawal_risk_score"}
        assert "trade_frequency_24h" not in feats
        assert "opposite_trade_ratio" not in feats
        assert "linked_account_count" not in feats
        assert "account_age_days" not in feats

    def test_transaction_scope_excludes_withdrawal_features(self):
        from app.adapters.risk_platform import normalize_evidence_records
        evidence = {
            "user_id": "U00008", "finding_id": "F6",
            "transaction_evidence": [
                {"record_id": "T000544", "timestamp": "t1",
                 "summary": "tx"},
            ],
            "feature_evidence": {
                "withdrawal_frequency_24h": 10,
                "trade_frequency_24h": 69,
                "opposite_trade_ratio": 0.34,
            },
        }
        r = normalize_evidence_records(
            case_id="U00008", evidence=evidence,
            finding=next(f for f in _findings() if f.finding_id == "F6"),
            stream="transactions")
        feats = r["risk_features"]
        assert set(feats) <= {"trade_frequency_24h",
                              "opposite_trade_ratio"}
        assert "withdrawal_frequency_24h" not in feats


# --- Artifact-scope: duplicate candidate analyses render once ----------------

class TestCandidateAnalysisDedup:
    """Two set-trades analyses with the SAME semantic candidate content
    (re-plan re-run under new tool-call/candidate IDs) render the candidate
    detail block ONCE; genuinely different candidate sets both render."""

    def _call(self, call_id, candidate_id, acct_a, acct_b, tx_a, tx_b):
        from app.models import ToolCallV2, ToolResult, ToolResultOutcome
        data = {"analysis": "cross_account_trade_analysis",
                "analysis_status": "MATCH_FOUND",
                "analysis_scope": "related_account_set",
                "config": {"max_time_delta_seconds": 30,
                           "value_tolerance_relative": 0.05,
                           "value_tolerance_absolute_floor": 1.0},
                "candidates": [{
                    "candidate_id": candidate_id, "account_a": acct_a,
                    "account_b": acct_b, "symbol": "DOT", "side_a": "BUY",
                    "side_b": "SELL", "value_a": 350.0, "value_b": 348.0,
                    "value_difference": 2.0, "value_difference_relative": 0.006,
                    "max_time_gap_seconds": 0.0012,
                    "match_type": "1_to_1", "rank": 1,
                    "value_difference_relative": 0.006,
                    "transaction_ids_a": [tx_a], "transaction_ids_b": [tx_b],
                    "supporting": [
                        {"transaction_id": tx_a, "account_id": acct_a},
                        {"transaction_id": tx_b, "account_id": acct_b}],
                    "timestamps": ["t1", "t2"],
                }]}
        return ToolCallV2(
            tool_call_id=call_id, investigation_id="INV-DUP",
            task_id="TASK-DUP",
            tool_name="cross_account_trade_analysis", status="success",
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data))

    def _bundle(self, executed):
        from app.domain_tools.artifact_bundle import artifact_bundle
        steps = sorted({tc.tool_call_id.split("-")[1] for tc in executed})
        r = artifact_bundle(scope="case", case_id="U00047",
                            task_id="TASK-DUP", source_tool_calls=executed,
                            source_step_ids=steps, prior_artifacts=[])
        return r.data["artifact"]["content"]

    def test_identical_analyses_render_once(self):
        """Same account pair + same supporting transactions under new IDs
        (re-plan re-run) → ONE candidate block, pair details present."""
        c1 = self._call("TC-S1-2026-10-18T065412054514+0000", "CATA-001",
                        "U00305", "U00311", "T024700", "T025031")
        c2 = self._call("TC-S1-2026-10-18T065434963845+0000", "CATA-006",
                        "U00305", "U00311", "T024700", "T025031")
        content = self._bundle([c1, c2])
        got = __import__("re").findall(r"CATA-0\d+", content)
        assert len(got) == 1, got
        assert content.count("U00305") >= 1
        assert "T024700" in content

    def test_different_candidates_both_render(self):
        c1 = self._call("TC-S1-a", "CATA-001", "U00305", "U00311",
                        "T024700", "T025031")
        c2 = self._call("TC-S1-b", "CATA-002", "U00329", "U00341",
                        "T025872", "T026469")
        content = self._bundle([c1, c2])
        assert "CATA-001" in content and "CATA-002" in content

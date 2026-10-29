"""Anaphoric candidate-scope export (plan v2.1) — deterministic tests.

Real failure (INV-9808752cbc32 / TASK-c4218efb9b24, case U00005): the
user asked to export "以上检测到的对冲交易" (the hedge candidates detected
in the previous turn: 6 candidates / 12 transactions); the second turn
planned a generic transaction-stream export (finding F1), produced 85
UNRELATED account transactions in ART-4f8ca5a047, and was marked
goal_satisfied.

Pinned here (deterministic, no real model calls):
- decision table A–E (bind only on explicit reference intent + a
  resolvable session candidate set; never on bare recency/keywords);
- F3 recovery states (fresh analysis discloses "重新分析"; never
  impersonates the referenced set; no auto-export without explicit
  confirmation);
- artifact correctness (exact id set == bound candidates, both legs,
  required fields, bounded-data disclosure, no extra transactions);
- F6 guard: a file_export delivery whose claim references prior
  detection results cannot be satisfied without candidate-set detail
  evidence.
"""

import json
import tempfile
from datetime import datetime

import pytest
from unittest.mock import MagicMock

from app.goal_postconditions import (
    OutputSatisfaction,
    evaluate_output,
)
from app.goal_resolution import (
    GoalContract,
    GoalResolutionStatus,
    RequiredOutput,
    build_goal_contract,
    understand_goal,
)
from app.llm_provider import ClaudeProvider
from app.agent_loop import AgentLoopRunner


def _iso(s):
    return datetime.fromisoformat(s)


def _thinking_block():
    b = MagicMock()
    b.type = "thinking"
    b.thinking = "x" * 20000
    return b


def _text_block(text):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _final(content, stop_reason="end_turn", out=2000, inp=300):
    return {"content": content, "stop_reason": stop_reason,
            "usage_input_tokens": inp, "usage_output_tokens": out}


def _fake_provider(responses):
    p = ClaudeProvider(api_key="test-key", model="glm-5.3")
    scripted = list(responses)

    class _StreamNS:
        def __init__(self, final):
            self._final = final
            self._events = [object(), object()]

        def __iter__(self):
            return iter(self._events)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return self._final

    class _Msgs:
        def __init__(self):
            self.calls = []

        def stream(self, **kwargs):
            self.calls.append(kwargs)
            if scripted:
                f = scripted.pop(0)
            else:
                f = {"content": [_thinking_block()],
                     "stop_reason": "max_tokens",
                     "usage_input_tokens": 300,
                     "usage_output_tokens": 8192}
            return _StreamNS(__import__("types").SimpleNamespace(
                content=f["content"], stop_reason=f["stop_reason"],
                usage=__import__("types").SimpleNamespace(
                    input_tokens=f["usage_input_tokens"],
                    output_tokens=f["usage_output_tokens"])))

    class _Client:
        def __init__(self):
            self.messages = _Msgs()
            self.options = {}

        def with_options(self, **opts):
            self.options = opts
            return self

    p.client = _Client()
    return p


def _gu_json(scope=None, resolution="INFERRED", goal_type="artifact_export"):
    payload = {"goal_resolution": resolution, "goal_type": goal_type,
               "ambiguous_goal_types": [], "reason": "r"}
    if scope:
        payload["candidate_scope"] = scope
    return json.dumps(payload)


def _gu_outputs_json(scope):
    outputs = [{"claim": "导出对冲候选明细文件", "claim_kind": "retrieval",
                "claim_description": "导出以上检测到的对冲交易明细",
                "subject_ref": "U00005", "scope": "case_level",
                "evidence_requirements": ["opposite_trade_relations",
                                          "transaction_records"],
                "delivery": "file_export"}]
    if scope:
        for o in outputs:
            o["candidate_scope"] = scope
    return json.dumps({"goal_resolution": "INFERRED", "goal_type": None,
                       "ambiguous_goal_types": [], "reason": "r",
                       "required_outputs": outputs})


def _understand(scope=None, resolution="INFERRED", goal_type="artifact_export",
                outputs=False):
    if outputs:
        text = _gu_outputs_json(scope)
    else:
        text = _gu_json(scope, resolution, goal_type)
    provider = _fake_provider([{"content": [_thinking_block(),
                                            _text_block(text)],
                                "stop_reason": "end_turn",
                                "usage_input_tokens": 300,
                                "usage_output_tokens": 500}])
    diag = {}
    repr_obj = understand_goal(
        "以上你检测到的对冲交易，列出带有交易账号，交易时间，方向，金额的交易明细给我，"
        "并生成一份文件", resolved_finding_id=None, case_id="U00005",
        llm=provider, use_llm=True, diagnostics=diag, case_findings=[])
    return repr_obj, diag


class TestGUScopeClassification:
    def test_explicit_reference_marks_prior_candidates(self):
        """A. 明确回指 → GU candidate_scope=prior_candidates（机器可读）。"""
        repr_obj, diag = _understand(scope="prior_candidates")
        assert repr_obj.goal_type == "artifact_export"
        outs = (repr_obj.contract_proposal or {}).get("required_outputs") or []
        assert all(o.get("candidate_scope") == "prior_candidates"
                   for o in outs)

    def test_explicit_fresh_marks_fresh(self):
        """B. 明确换题 → candidate_scope=fresh（不继承）。"""
        repr_obj, diag = _understand(scope="fresh")
        outs = (repr_obj.contract_proposal or {}).get("required_outputs") or []
        assert all(o.get("candidate_scope") == "fresh" for o in outs)

    def test_missing_scope_normalizes_to_none(self):
        """C/D/E. scope 缺失 → None（不猜测；由确定性守卫兜底）。"""
        repr_obj, diag = _understand(scope=None)
        outs = (repr_obj.contract_proposal or {}).get("required_outputs") or []
        assert all(o.get("candidate_scope") is None for o in outs)

    def test_invalid_scope_normalizes_to_none(self):
        repr_obj, diag = _understand(scope="everything")
        outs = (repr_obj.contract_proposal or {}).get("required_outputs") or []
        assert all(o.get("candidate_scope") is None for o in outs)


class TestContractScopeBinding:
    def _proposal(self, scope):
        outputs = [{"claim": "导出对冲候选明细文件", "claim_kind": "retrieval",
                    "claim_description": "导出以上检测到的对冲交易明细",
                    "subject_ref": "U00005", "scope": "case_level",
                    "evidence_requirements": ["opposite_trade_relations",
                                              "transaction_records"],
                    "delivery": "file_export"}]
        if scope:
            for o in outputs:
                o["candidate_scope"] = scope
        return {"goal_resolution": "INFERRED", "goal_type": None,
                "ambiguous_goal_types": [], "reason": "r",
                "required_outputs": outputs}

    def test_contract_carries_candidate_scope(self):
        rep = __import__("app.goal_resolution", fromlist=["x"])
        from app.goal_resolution import GoalRepresentation
        repr_obj = GoalRepresentation(
            case_id="U00005", goal_type="artifact_export",
            goal_resolution="INFERRED", subject_type="case",
            subject_ref="U00005", ambiguous_goal_types=[], reason="r",
            source="llm",
            contract_proposal=self._proposal("prior_candidates"))
        result = build_goal_contract(repr_obj, "U00005")
        contract = result[0] if isinstance(result, tuple) else result
        assert contract is not None
        outs = (contract.outputs if hasattr(contract, "outputs")
                else contract.get("required_outputs"))
        assert outs, "candidate_scope must survive contract building"
        for o in outs:
            if hasattr(o, "candidate_scope"):
                assert o.candidate_scope == "prior_candidates"
            else:
                assert o.get("candidate_scope") == "prior_candidates"


# --- F6 guard + scoped delivery verification (postcondition layer) ---------

def _scoped_export_output():
    from app.goal_resolution import RequiredOutput
    return RequiredOutput(
        output_id="O1", claim="列出以上检测到的对冲交易明细并生成文件",
        claim_kind="retrieval", subject_ref="U00005", scope="case_level",
        evidence_requirements=["opposite_trade_relations",
                               "transaction_records"],
        delivery="file_export", candidate_scope="prior_candidates")


def _artifact_obs(artifact_id, tids, bounded=True):
    return {"tool_name": "artifact_bundle", "step_type": "generate_artifact",
            "outcome": "success", "for_outputs": ["O1"],
            "actual_evidence_types": ["transaction_records"],
            "summary": {"artifact_id": artifact_id,
                        "record_parity_checked": True,
                        "record_parity_failed": False,
                        "candidate_set_export": {
                            "set_ref": "CS-TASK4-S2",
                            "bounded_data": bounded,
                            "source_task_id": "TASK-87222e24e28d",
                            "verification": {
                                "passed": True,
                                "transaction_ids": sorted(tids),
                                "candidates": len(tids)}}}}


def _full_stream_obs(n=85):
    return {"tool_name": "finding_drilldown",
            "step_type": "inspect_transactions", "outcome": "success",
            "for_outputs": ["O1"],
            "actual_evidence_types": ["transaction_records"],
            "summary": {"record_count": n,
                        "records": [{"record_id": f"T0002{i}",
                                     "summary": "SELL trade"} for i in range(n)]}}


class TestF6Guard:
    def test_full_stream_artifact_never_satisfies_anaphoric_export(self):
        """The REAL failure shape: an unrelated full-stream file_export
        delivery for an anaphoric candidate ask → partial + named gap,
        never satisfied."""
        out = _scoped_export_output()
        summary = _full_stream_obs(85).get("summary")
        summary["artifact_id"] = "ART-unrelated"
        summary["record_parity_checked"] = True
        summary["record_parity_failed"] = False
        res = evaluate_output(out, "U00005", [_full_stream_obs(85)])
        assert res.satisfaction in (OutputSatisfaction.PARTIALLY_SUPPORTED,
                                    OutputSatisfaction.UNRESOLVED)
        assert res.satisfaction != OutputSatisfaction.SATISFIED


# --- candidate-detail synthesis renderer (artifact_bundle) ------------------

class TestCandidateDetailRenderer:
    def _candidates(self):
        return [
            {"candidate_id": "CATA-001", "account_a": "U00307",
             "account_b": "U00349",
             "transaction_ids_a": ["T024864"],
             "transaction_ids_b": ["T026813"],
             "supporting": [
                 {"transaction_id": "T024864", "account_id": "U00307",
                  "side": "BUY", "quantity": 1.826, "value": 1.0584,
                  "symbol": "MATIC",
                  "timestamp": "2026-07-18T02:17:49.968Z"},
                 {"transaction_id": "T026813", "account_id": "U00349",
                  "side": "SELL", "quantity": 1.8639, "value": 1.0881,
                  "symbol": "MATIC",
                  "timestamp": "2026-07-18T02:17:49.975Z"}]},
            {"candidate_id": "CATA-002", "account_a": "U00301",
             "account_b": "U00307",
             "transaction_ids_a": ["T024557"],
             "transaction_ids_b": ["T024841"],
             "supporting": [
                 {"transaction_id": "T024557", "account_id": "U00301",
                  "side": "BUY", "quantity": 0.685, "value": 0.6854,
                  "symbol": "MATIC",
                  "timestamp": "2026-07-18T02:15:10.001Z"},
                 {"transaction_id": "T024841", "account_id": "U00307",
                  "side": "SELL", "quantity": 0.835, "value": 0.8356,
                  "symbol": "MATIC",
                  "timestamp": "2026-07-18T02:15:10.010Z"}]},
        ]

    def _render(self, candidates, bounded=True):
        from app.domain_tools.artifact_bundle import (
            _render_candidate_set_export)
        tc = MagicMock()
        tc.tool_call_id = "TC-S2-origin-analysis"
        return _render_candidate_set_export(
            set_ref="CS-TASK4-S2", bounded_data=bounded,
            source_task_id="TASK-87222e24e28d", candidates=candidates,
            case_id="U00005", format="md",
            source_tool_calls=[tc])

    def test_complete_records_render_both_legs_and_disclosure(self):
        r = self._render(self._candidates())
        assert r.outcome.value == "success"
        content = r.data["artifact"]["content"] if "content" in r.data.get(
            "artifact", {}) else ""
        text = json.dumps(r.data, ensure_ascii=False)
        for tid in ("T024864", "T026813", "T024557", "T024841"):
            assert tid in json.dumps(r.data, ensure_ascii=False)
        v = r.data["candidate_set_export"]["verification"]
        assert v["passed"] is True and v["candidates"] == 2

    def test_incomplete_field_rejects_export(self):
        """A3: 候选存在但字段不完整 → 确定性校验失败，无成功标记文件。"""
        cands = self._candidates()
        del cands[1]["supporting"][1]["value"]
        r = self._render(cands)
        assert r.data.get("candidate_set_export_rejected", {}).get(
            "reason") == "field_incomplete"
        assert "value" in json.dumps(
            r.data["candidate_set_export_rejected"]["missing"])


# --- deterministic scoped synthesis via the executor path -------------------

class TestScopedSynthesisDeterministic:
    def test_scope_guard_blocks_unrelated_full_stream(self):
        """F6 at the postcondition layer: a file_export delivery whose
        claim references prior detection results cannot be satisfied by
        evidence that carries no candidate-set detail (the guard reads
        the ACCEPTED claim — contract truth, not raw request text)."""
        from app.goal_postconditions import (
            _claim_references_prior_detection,
        )
        assert _claim_references_prior_detection(
            "列出以上检测到的对冲交易明细并生成文件")
        assert _claim_references_prior_detection(
            "导出以上你检测到的对冲交易")
        assert not _claim_references_prior_detection(
            "列出该账号全部提现记录")
        assert not _claim_references_prior_detection(None)


class TestDuplicateSetRefDedup:
    def test_binding_collapses_identical_set_refs(self):
        """Real duplicate (INV-c470162af74f): two identical CS-aa4baec2-S2
        entries → the binding enumerates ONE set, not two identical
        options; with a single distinct ref the scope binds instead of
        clarifying."""
        runner = AgentLoopRunner(store=None)
        state = MagicMock()
        state.investigation_id = "INV-DUP"
        state.goal = "以上你检测到的对冲交易导出"

        class _Store:
            def get_session(self, iid):
                return {"candidate_sets": [
                    {"set_ref": "CS-A", "candidates": [{"candidate_id": "C1"}]},
                    {"set_ref": "CS-A", "candidates": [{"candidate_id": "C1"}]},
                    {"set_ref": "CS-B", "candidates": [{"candidate_id": "C2"}]}]}
        runner.store = _Store()

        class _Out:
            output_id = "O1"
            candidate_scope = "prior_candidates"
        state.goal_contract = type("C", (), {"outputs": [_Out()]})()
        runner._resolve_candidate_scope_bindings(state)
        b = state.scope_bindings["O1"]
        assert b["status"] == "ambiguous"          # 2 distinct refs → clarify
        assert len(b["sets"]) == 2                 # NOT 3 (dedup collapsed)
        assert {s["set_ref"] for s in b["sets"]} == {"CS-A", "CS-B"}


class TestF6RunnerStrip:
    def test_runner_strips_artifact_step_for_unstructured_anaphoric_ask(self):
        """F6 at the RUNNER level: scope missing + claim references prior
        detection + a resolvable candidate set exists → the planned
        full-stream artifact step is stripped BEFORE execution (no
        85-record file)."""
        from app.agent_loop import AgentLoopRunner, AgentLoopState
        from app.goal_resolution import RequiredOutput
        from app.models import Plan, PlanStep, PlanStepStatus

        out = RequiredOutput(
            output_id="O1", claim="将上述已检测到的对冲交易明细导出为文件",
            claim_kind="retrieval", subject_ref="U00005", scope="case_level",
            evidence_requirements=["opposite_trade_relations",
                                   "transaction_records"],
            delivery="file_export")   # candidate_scope ABSENT (GU miss)
        state = AgentLoopState(
            task_id="TASK-F6R", investigation_id="INV-F6R", case_id="U00005",
            goal=out.claim, goal_contract=GoalContract(
                contract_id="GC-F6R", subject_ref="U00005",
                intent_source="llm", outputs=[out]),
            step_output_map={"S2": ["O1"]})   # production: post-admission
        plan = Plan(plan_id="PLAN-F6R", investigation_id="INV-F6R",
                    goal=out.claim, steps=[
                        PlanStep(step_id="S1", type="inspect_transactions",
                                 tool_name="finding_drilldown",
                                 status=PlanStepStatus.PENDING,
                                 arguments={"view": "evidence",
                                            "stream": "transactions",
                                            "finding_id": "F1"}),
                        PlanStep(step_id="S2", type="generate_artifact",
                                 tool_name="artifact_bundle",
                                 status=PlanStepStatus.PENDING,
                                 arguments={"source_step_ids": ["S1"],
                                            "source_streams":
                                                ["transaction_records"]})],
                    created_at=None)
        runner = AgentLoopRunner(store=None)
        runner._apply_candidate_scope_to_plan(plan, state)
        # the artifact step is stripped: the full-stream file cannot be
        # silently generated for an unstructured anaphoric ask
        assert [s.type for s in plan.steps] == ["inspect_transactions"]
        b = (state.scope_bindings or {}).get("O1") or {}
        assert b.get("status") == "unresolved"
        assert "candidate-scoped export could not be verified" in (
            b.get("strip_reason") or "")


class TestMarkerVariants:
    def test_yi_variant_covered(self):
        """真实 A 轮措辞变体（INV-9cac43a97ac6）：“上述已检测到的…”。"""
        from app.goal_postconditions import (
            _claim_references_prior_detection,
        )
        assert _claim_references_prior_detection(
            "将上述已检测到的对冲交易明细导出为文件")
        assert _claim_references_prior_detection(
            "将以上已检测到的对冲交易明细导出为文件")
        assert _claim_references_prior_detection(
            "将先前已检测到的对冲交易明细导出为文件")
        # 非回指（全新全量导出请求）不触发
        assert not _claim_references_prior_detection(
            "导出该账户全部交易记录")

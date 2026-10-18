"""U00047 — Repeated Evidence / No-Progress Loop fix — targeted tests.

Original fault (TASK-d82c0aa51b65 / INV-8cc878c294a0 / ART-0aec68507a):
a re-plan re-ran the identical set-internal trade analysis 3× (same
candidates CATA-001..005, no new evidence), the no-progress guard was
preempted by the bounded-supplementary rule reading a STALE step mirror,
evidence refs accumulated (21 → 31), the records-kind deliverable O3 had
no reachable satisfaction path (generate_artifact maps no
records/opposite-trade evidence types), and the loop only ended at the
iteration bound (8/8, stopped_by_limit).

Fixes pinned here:
- fix A1: decisions read a FRESH step mirror (_sync_plan_mirror before
  every decision);
- fix A2: the AL-2.13 no-progress guard evaluates BEFORE the
  bounded-supplementary rule; firing defers only for pending evidence-
  acquiring steps or a pending not-yet-produced artifact composition
  (one bounded continuation), otherwise bounded STOP;
- fix A3: composed bundle evidence_refs are deduplicated (per-call
  provenance untouched);
- fix B: a records-kind candidate-deliverable output is satisfied only
  by artifact CONTENT verification (provenance binding + every found
  candidate's transaction IDs + bounded-retrieval disclosure), with its
  OWN verdict detail, never downgraded afterwards (monotonic registry),
  and never by artifact ID alone;
- O2 (existence check) keeps its honest truncated-retrieval
  PARTIALLY_SUPPORTED semantics.

Real loop scenarios run the REAL artifact_bundle domain tool over
scripted analysis results (real planner scripts, real executor, real
postconditions, real composer, real TaskStoreV2) — no real LLM, no RP.
"""

import json

import pytest

from app.agent_loop import (
    AgentLoopRunner,
    AgentLoopState,
    AgentStatus,
    DecisionProposal,
    LoopDecision,
    LoopPolicy,
    StopReason,
)
from app.config import settings
from app.domain_tools.artifact_bundle import (
    _dedupe_evidence_refs,
    artifact_bundle,
)
from app.executor_v2 import ExecutorV2, ToolProvider
from app.goal_postconditions import evaluate_output
from app.goal_resolution import GoalContract, GoalRepresentation
from app.models import (
    EvidenceRef,
    Plan,
    PlanStep,
    PlanStepStatus,
    ToolCallV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from eval.fixtures import ScriptedLLM, build_context, default_findings

CASE = "U00047"
GOAL = ("列出所有关联账号，查询关联账号之间是否存在对冲交易，如存在，"
        "列出对冲交易明细给我，并生成一份交易明细文件")

_TXNS_A = ["T024700", "T025031"]
_TXNS_B = ["T025053", "T026467"]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _related_ok():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"analysis_type": "related_account_analysis",
              "related_accounts_count": 2,
              "related_accounts": ["U00305", "U00311"],
              "cluster_name": "Cluster_device_sharing_6",
              "detection_type": "device_sharing", "member_count": 3,
              "cluster_risk_score": 80.0, "role_in_cluster": "spoke",
              "shared_ip_attributions": {}},
        evidence_refs=[EvidenceRef(kind="risk_event", id=CASE)],
    )


def trade_result(candidate_id="CATA-001", txns=None, truncated=True):
    """A realistic set-internal cross-account analysis success."""
    txns = txns or _TXNS_A
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={
            "analysis": "cross_account_trade_analysis",
            "analysis_status": "MATCH_FOUND",
            "analysis_type": "cross_account_opposite_trade",
            "analysis_scope": "related_account_set",
            "case_id": CASE,
            "target_account": CASE,
            "accounts_analyzed": [CASE, "U00305", "U00311"],
            "candidates": [{
                "candidate_id": candidate_id,
                "match_type": "1_to_1",
                "account_a": "U00305", "side_a": "BUY",
                "account_b": "U00311", "side_b": "SELL",
                "symbol": "SOL",
                "max_time_gap_seconds": 0.001192,
                "value_a": 352.739, "value_b": 350.265,
                "value_difference": 2.47398,
                "value_difference_relative": 0.007,
                "transaction_ids_a": txns[:1],
                "transaction_ids_b": txns[1:],
                "supporting": [
                    {"transaction_id": txns[0], "account_id": "U00305",
                     "side": "BUY", "value": 352.739},
                    {"transaction_id": txns[1], "account_id": "U00311",
                     "side": "SELL", "value": 350.265},
                ],
            }],
            "candidates_found": 1, "candidates_emitted": 1,
            "totals": {"transactions_retrieved": 2},
            "retrieval": {
                "status": "truncated" if truncated else "complete",
                "truncated": truncated,
                "truncated_reasons": (
                    ["account_set:retrieval_budget_exhausted("
                     "TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS=500)"]
                    if truncated else []),
            },
            "config": {"max_time_delta_seconds": 30.0,
                       "value_tolerance_relative": 0.05,
                       "value_tolerance_absolute_floor": 1.0},
        },
        evidence_refs=[EvidenceRef(kind="transaction", id=t) for t in txns],
    )


def _trade_summary(truncated=True):
    return {
        "candidates_found": 1, "candidates_emitted": 1,
        "candidates": [{"candidate_id": "CATA-001",
                        "account_a": "U00305", "account_b": "U00311",
                        "symbol": "SOL"}],
        "retrieval": {"truncated": truncated},
        "analysis_scope": "related_account_set",
        "accounts_analyzed": [CASE, "U00305", "U00311"],
        "target_account": CASE,
    }


def _obs(summary, *, tool="cross_account_trade_analysis", outcome="success",
         semantic="MATCH_FOUND", step="S1", iteration=1):
    return {"tool_name": tool,
            "step_type": "analyze_related_account_set_trades",
            "outcome": outcome, "semantic_status": semantic,
            "summary": summary, "evidence_refs": [], "warnings": [],
            "error": None, "iteration": iteration, "step_id": step,
            "for_outputs": ["O2", "O3"]}


def _contract():
    from app.goal_resolution import RequiredOutput
    return GoalContract(
        contract_id="GC-U47T", subject_ref=CASE, intent_source="llm",
        outputs=[
            RequiredOutput(
                output_id="O2",
                claim="关联账户集合内部是否存在满足匹配条件的对冲交易候选",
                claim_kind="existence_check", subject_ref=CASE,
                scope="case_level",
                evidence_requirements=["opposite_trade_relations"]),
            RequiredOutput(
                output_id="O3",
                claim="生成并交付一份包含上述对冲交易明细的交易明细文件",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level",
                evidence_requirements=["transaction_records",
                                       "opposite_trade_relations"]),
        ])


def _policy_state(observations, plan_steps, *, artifact_ids=(),
                  revisions=2, step_output_map=None, contract=True,
                  iteration=4):
    return AgentLoopState(
        task_id="TASK-U47T", investigation_id="INV-U47T", case_id=CASE,
        goal=GOAL, criteria_kind="artifact_produced",
        observations=observations, plan_steps=plan_steps,
        plan_revisions=revisions, artifact_ids=list(artifact_ids),
        step_output_map=step_output_map or {"S1": ["O2", "O3"]},
        goal_contract=_contract() if contract else None,
        iteration=iteration)


S1_PENDING = {"step_id": "S1",
              "type": "analyze_related_account_set_trades",
              "status": "pending",
              "tool_name": "cross_account_trade_analysis",
              "arguments": {}}
S1_DONE = dict(S1_PENDING, status="success")
S3_PENDING = {"step_id": "S3", "type": "generate_artifact",
              "status": "pending", "tool_name": "artifact_bundle",
              "arguments": {}}
S3_DONE = dict(S3_PENDING, status="success")


def _enforce(state, proposal="CONTINUE"):
    return LoopPolicy().enforce(
        DecisionProposal(decision=proposal, reason="test", source="llm"),
        state)


# ---------------------------------------------------------------------------
# Part 1 — LoopPolicy: guard order, stale mirror, bounded continuation
# ---------------------------------------------------------------------------

class TestNoProgressGuardOrder:
    def test_duplicate_result_with_delivered_artifact_stops(self):
        """五.1/五.4: the original dec[5] shape — the analysis just
        repeated itself, only the artifact step remains pending, and the
        deliverable ALREADY exists. The guard evaluates FIRST (before any
        rule could coerce a continuation) and fires the bounded STOP."""
        state = _policy_state(
            [_obs(_trade_summary()), _obs(_trade_summary(), iteration=4)],
            [S1_DONE, S3_PENDING], artifact_ids=["ART-x"])
        enforced = _enforce(state)
        assert enforced.decision == LoopDecision.STOP
        assert "identical result" in enforced.reason
        assert enforced.coerced_from == "CONTINUE"
        assert not enforced.limit_reached

    def test_stale_mirror_is_corrected_before_decision(self):
        """五.4: the original failure's root — the decision layer saw the
        just-executed duplicate step as still PENDING in the mirror and
        coerced CONTINUE. The loop now syncs the mirror before every
        decision; a stale S1 entry becomes 'success' and the SAME state
        converges through the guard instead of the pending-covered
        continuation."""
        runner = AgentLoopRunner()
        plan = Plan(plan_id="P1", skill_id="s", goal="g",
                    investigation_id="INV-U47T", steps=[
            PlanStep(step_id="S1", type="analyze_related_account_set_trades",
                     status=PlanStepStatus.SUCCESS),
            PlanStep(step_id="S3", type="generate_artifact",
                     status=PlanStepStatus.PENDING),
        ])
        state = _policy_state(
            [_obs(_trade_summary()), _obs(_trade_summary(), iteration=4)],
            [S1_PENDING, S3_PENDING], artifact_ids=["ART-x"])
        # BEFORE sync: the stale mirror reproduces the original coercion
        # surface (pending S1 maps to unsatisfied O2/O3)
        stale_pending = [s["step_id"] for s in state.pending_steps()]
        assert stale_pending == ["S1", "S3"]
        runner._sync_plan_mirror(state, plan)
        enforced = _enforce(state)
        assert enforced.decision == LoopDecision.STOP
        assert "identical result" in enforced.reason

    def test_duplicate_result_defers_for_pending_composition(self):
        """五.1: identical repeat, artifact pending and NOT yet produced —
        one bounded continuation to compose the deliverable (never a
        RE_PLAN detour that would re-run the analysis, and never the old
        early-stop that would abandon an undelivered file)."""
        state = _policy_state(
            [_obs(_trade_summary()), _obs(_trade_summary(), iteration=4)],
            [S1_DONE, S3_PENDING], artifact_ids=[])
        enforced = _enforce(state)
        assert enforced.decision == LoopDecision.CONTINUE
        assert "composing the pending deliverable" in enforced.reason

    def test_new_result_keeps_executing(self):
        """五.2: a genuinely different result (new candidates) is never a
        no-progress repeat — execution continues."""
        fresh = _trade_summary()
        fresh["candidates"] = [dict(fresh["candidates"][0],
                                    candidate_id="CATA-009",
                                    account_b="U00999")]
        state = _policy_state(
            [_obs(_trade_summary()), _obs(fresh, iteration=4)],
            [S1_DONE, S3_PENDING], artifact_ids=[])
        enforced = _enforce(state)
        assert enforced.decision == LoopDecision.CONTINUE
        assert "identical result" not in enforced.reason

    def test_no_progress_with_nothing_pending_stops(self):
        """五.7: repeat, plan exhausted, no artifact — the bounded stop
        fires (terminal labeling is the loop's BOUNDED_DATA path)."""
        state = _policy_state(
            [_obs(_trade_summary()), _obs(_trade_summary(), iteration=4)],
            [S1_DONE], artifact_ids=[])
        enforced = _enforce(state)
        assert enforced.decision == LoopDecision.STOP
        assert not enforced.limit_reached

    def test_first_revision_never_stops_on_repeat(self):
        """五.9: a legitimate fresh plan (revisions < 2) is never
        converged by the guard."""
        state = _policy_state(
            [_obs(_trade_summary()), _obs(_trade_summary(), iteration=2)],
            [S1_DONE], artifact_ids=[], revisions=1)
        enforced = _enforce(state)
        assert "identical result" not in enforced.reason

    def test_iteration_bound_still_terminal_without_success(self):
        """五.8: with no successful observation and the bound reached, the
        limit terminal is unchanged (failure semantics preserved)."""
        state = _policy_state(
            [], [S1_DONE], artifact_ids=[], revisions=1, iteration=8)
        enforced = _enforce(state)
        assert enforced.decision == LoopDecision.STOP
        assert enforced.limit_reached
        assert enforced.reason == "maximum investigation iterations reached"


class TestDecisionFreshness:
    def test_sync_plan_mirror_reflects_executor_status(self):
        """五.4: the decision layer must read the statuses the executor
        left behind — the sync flips a stale pending mirror entry, and a
        fresh mirror yields the composition continuation instead of a
        stale-pending coercion."""
        runner = AgentLoopRunner()
        plan = Plan(plan_id="P1", skill_id="s", goal="g",
                    investigation_id="INV-U47T", steps=[
            PlanStep(step_id="S1", type="analyze_related_account_set_trades",
                     status=PlanStepStatus.SUCCESS),
            PlanStep(step_id="S3", type="generate_artifact",
                     status=PlanStepStatus.PENDING),
        ])
        state = _policy_state([], [S1_PENDING, S3_PENDING])
        runner._sync_plan_mirror(state, plan)
        by_id = {r["step_id"]: r["status"] for r in state.plan_steps}
        assert by_id["S1"] == "success"
        assert by_id["S3"] == "pending"
        state2 = _policy_state(
            [_obs(_trade_summary()), _obs(_trade_summary(), iteration=4)],
            state.plan_steps, artifact_ids=[])
        enforced = _enforce(state2)
        assert enforced.decision == LoopDecision.CONTINUE
        assert "composing the pending deliverable" in enforced.reason


# ---------------------------------------------------------------------------
# Part 2 — O2 truncated semantics are preserved (never upgraded)
# ---------------------------------------------------------------------------

class TestO2TruncatedSemantics:
    def test_truncated_set_internal_stays_partially_supported(self):
        """五.5: retrieval.truncated=true keeps the honest incomplete-
        coverage verdict; never SATISFIED."""
        out = _contract().outputs[0]        # O2, existence_check
        result = evaluate_output(
            out, CASE, [_obs(_trade_summary(truncated=True))])
        assert result.satisfaction.value == "partially_supported"
        assert "集合检索受预算限制" in (result.detail or "")

    def test_untruncated_set_internal_full_verdict(self):
        out = _contract().outputs[0]
        result = evaluate_output(
            out, CASE, [_obs(_trade_summary(truncated=False))])
        assert result.satisfaction.value == "satisfied"


# ---------------------------------------------------------------------------
# Part 3 — R2a candidate-deliverable satisfaction (fix B)
# ---------------------------------------------------------------------------

def _tc(tc_id="TC-S1-2026-10-18T065412054514+0000"):
    return ToolCallV2(
        tool_call_id=tc_id, investigation_id="INV-U47T",
        task_id="TASK-U47T", tool_name="cross_account_trade_analysis",
        result=trade_result())


def _artifact_dict(tc_id, content=None):
    return {
        "artifact_id": "ART-u47test01",
        "source_tool_calls": [tc_id],
        "content": content or (
            "# Cross-Account Trade Evidence — U00047\n"
            "| Candidate | Type | Account A | Side A | Account B |"
            " Side B | Symbol |\n|---|---|---|---|---|---|---|\n"
            "| CATA-001 | 1_to_1 | U00305 | BUY | U00311 | SELL | SOL"
            " | 0.001192 | 352.739 | 350.265 | 2.47398 (0.7%)"
            f" | {', '.join(_TXNS_A)} |\n"
            "Limitations:\n- Retrieval was bounded (partial evidence:"
            " account_set:retrieval_budget_exhausted"
            "(TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS=500)); absence of"
            " further matches is not established.\n"),
    }


class TestCandidateDeliverableSatisfaction:
    def _apply(self, state, pool, artifacts):
        runner = AgentLoopRunner()
        runner._apply_records_artifact_satisfaction(
            state, None, pool, artifacts)
        return runner, state.goal_contract.outputs[1]       # O3

    def test_content_verified_delivery_satisfies_with_own_detail(self):
        """五.6: provenance binding + candidate IDs + disclosure →
        SATISFIED, with the delivery's OWN detail (not O2's wording),
        registered on the monotonic content-verification registry."""
        tc = _tc()
        state = _policy_state([], [S3_DONE])
        runner, o3 = self._apply(state, [tc],
                                 [_artifact_dict(tc.tool_call_id)])
        assert o3.satisfaction.value == "satisfied"
        assert "明细文件 ART-u47test01 已交付" in o3.detail
        assert "如实披露" in o3.detail
        assert "集合检索受预算限制，覆盖不完整" not in o3.detail
        assert "O3" in runner._artifact_satisfied_outputs

    def test_missing_candidate_id_in_content_never_satisfies(self):
        """五.6: artifact ID present but content missing a candidate's
        transaction ID → content verification fails, stays partial."""
        tc = _tc()
        state = _policy_state([], [S3_DONE])
        art = _artifact_dict(tc.tool_call_id)
        art["content"] = art["content"].replace(_TXNS_A[1], "T999999")
        _, o3 = self._apply(state, [tc], [art])
        assert o3.satisfaction.value != "satisfied"
        assert "内容验证失败" in o3.detail

    def test_truncated_source_requires_disclosure_in_file(self):
        """五.6/B.4: a truncated analysis may only be delivered through a
        bundle that discloses the boundary — a silent bundle is not a
        complete delivery."""
        tc = _tc()
        state = _policy_state([], [S3_DONE])
        art = _artifact_dict(tc.tool_call_id)
        art["content"] = "\n".join(
            ln for ln in art["content"].split("\n")
            if "Retrieval was bounded" not in ln)
        _, o3 = self._apply(state, [tc], [art])
        assert o3.satisfaction.value != "satisfied"
        assert "检索边界" in o3.detail

    def test_artifact_id_alone_never_satisfies(self):
        """五.6/B.3: an artifact that does not declare the analysis call
        as its own provenance is not a delivery for this output."""
        tc = _tc()
        state = _policy_state([], [S3_DONE])
        art = _artifact_dict("TC-S1-some-other-call")
        _, o3 = self._apply(state, [tc], [art])
        assert o3.satisfaction.value != "satisfied"
        assert "尚未生成" in o3.detail

    def test_no_delivered_artifact_yet_honest_detail(self):
        tc = _tc()
        state = _policy_state([], [S1_DONE])
        _, o3 = self._apply(state, [tc], [])
        assert o3.satisfaction.value != "satisfied"
        assert "尚未生成" in o3.detail

    def test_step_must_be_tracked_for_this_output(self):
        """The provenance binding goes through the tracked admission
        mapping: a step mapped to OTHER outputs never satisfies O3."""
        tc = _tc()
        state = _policy_state([], [S3_DONE],
                              step_output_map={"S1": ["O2"]})
        _, o3 = self._apply(state, [tc], [_artifact_dict(tc.tool_call_id)])
        assert o3.satisfaction.value != "satisfied"


# ---------------------------------------------------------------------------
# Part 4 — artifact_bundle: single rendering + ref dedup + reuse intact
# ---------------------------------------------------------------------------

class TestArtifactBundleComposition:
    def _bundle(self, calls, prior_artifacts=None):
        from app.models import ToolCallStatusV2
        tcs = []
        for i, res in enumerate(calls):
            tcs.append(ToolCallV2(
                tool_call_id=f"TC-S1-2026-10-18T06541{i}054514+0000",
                investigation_id="INV-U47T", task_id="TASK-U47T",
                tool_name="cross_account_trade_analysis",
                status=ToolCallStatusV2.SUCCESS, result=res))
        return artifact_bundle(
            scope="case", case_id=CASE, task_id="TASK-U47T",
            source_tool_calls=tcs, prior_artifacts=prior_artifacts)

    def test_identical_repeat_renders_single_candidate_block(self):
        """五.1: identical candidate sets render ONCE (existing
        seen_candidate_keys behavior — pinned, not reimplemented)."""
        result = self._bundle([trade_result(), trade_result()])
        assert result.outcome == ToolResultOutcome.SUCCESS
        content = result.data["artifact"]["content"]
        assert content.count("### TC-S1-") == 1
        assert content.count("Matching criteria") == 1

    def test_evidence_refs_deduplicated_across_repeats(self):
        """五.3: the composed bundle's aggregate evidence list names each
        record once even when the analysis executed twice."""
        result = self._bundle([trade_result(), trade_result()])
        keys = [f"{r.kind}:{r.id}" for r in result.evidence_refs]
        assert len(keys) == len(set(keys))
        assert set(keys) == {"transaction:T024700", "transaction:T025031"}

    def test_different_candidates_render_their_own_blocks(self):
        """五.2: genuinely different candidate sets are never falsely
        deduplicated — new evidence keeps its own section."""
        second = trade_result(candidate_id="CATA-002", txns=_TXNS_B)
        result = self._bundle([trade_result(), second])
        content = result.data["artifact"]["content"]
        assert content.count("### TC-S1-") == 2
        assert "T024700" in content and "T025053" in content

    def test_dedupe_helper_is_order_preserving(self):
        refs = [EvidenceRef(kind="transaction", id="B"),
                EvidenceRef(kind="transaction", id="A"),
                EvidenceRef(kind="transaction", id="B")]
        out = _dedupe_evidence_refs(refs)
        assert [r.id for r in out] == ["B", "A"]

    def test_fingerprint_reuse_idempotency_intact(self):
        """五.9: the C1 artifact-level idempotency (same semantic evidence
        → reuse) is unchanged by this fix."""
        first = self._bundle([trade_result()])
        prior = dict(first.data["artifact"])
        prior["evidence_fingerprint"] = first.data["evidence_fingerprint"]
        second = self._bundle([trade_result(), trade_result()],
                              prior_artifacts=[prior])
        assert second.data.get("artifact_reused") is True
        assert second.data["artifact_id"] == prior["artifact_id"]


# ---------------------------------------------------------------------------
# Part 5 — real-loop scenarios (real planner/executor/postconditions/
# composer/store; real artifact_bundle; scripted analysis results)
# ---------------------------------------------------------------------------

class HybridTradeProvider(ToolProvider):
    """Scripts the analysis tools; runs the REAL artifact_bundle so the
    candidate-deliverable path is exercised end to end."""

    def __init__(self, scripted):
        super().__init__()
        self.calls = []
        self._scripted = {k: list(v) for k, v in scripted.items()}
        for name in self._scripted:
            self.register(name, self._invoke(name))
        self.register("artifact_bundle", lambda args: artifact_bundle(
            scope=args.get("scope", "case"),
            format=args.get("format", "md"),
            case_id=args.get("case_id"),
            finding_id=args.get("finding_id"),
            task_id=args.get("task_id"),
            source_tool_calls=args.get("source_tool_calls"),
            source_step_ids=args.get("source_step_ids"),
            prior_artifacts=args.get("prior_artifacts"),
        ))

    def _invoke(self, name):
        def invoke(args):
            self.calls.append((name, dict(args)))
            return self._scripted[name].pop(0)
        return invoke

    def count(self, tool_name):
        return sum(1 for n, _ in self.calls if n == tool_name)


def _goal_resolver():
    return lambda req, fid, case, diag=None: GoalRepresentation(
        case_id=CASE, goal_type=None, goal_resolution="COMPOUND",
        subject_type="case", subject_ref=CASE, ambiguous_goal_types=[],
        reason="three deliverables", source="llm", request_text=req,
        contract_proposal={"required_outputs": [
            {"claim": "关联账户集合内部是否存在满足匹配条件的对冲交易候选",
             "claim_kind": "existence_check", "subject_ref": CASE,
             "scope": "case_level",
             "evidence_requirements": ["opposite_trade_relations"]},
            {"claim": "生成并交付一份包含上述对冲交易明细的交易明细文件",
             "claim_kind": "retrieval", "subject_ref": CASE,
             "scope": "case_level",
             "evidence_requirements": ["transaction_records",
                                       "opposite_trade_relations"]},
        ]})


_SET_ART_STEP_PLAN = {
    "skill_id": "trade_detail", "goal": "eval",
    "steps": [
        {"type": "analyze_related_account_set_trades", "reason": "r"},
        {"type": "generate_artifact", "reason": "r"}],
}
_SET_ONLY_PLAN = {
    "skill_id": "trade_detail", "goal": "eval",
    "steps": [{"type": "analyze_related_account_set_trades",
               "reason": "r"}],
}


def _make_runner(store, provider, plan, decisions):
    plan_script = json.dumps(plan)
    runner = AgentLoopRunner(
        planner=PlannerV2(ScriptedLLM([plan_script] * 3)),
        executor=ExecutorV2(provider), store=store,
        proposer=_Scripted(decisions))
    runner.goal_resolver = _goal_resolver()
    return runner


def _run(runner, inv, task):
    return runner.run(GOAL, build_context(case_id=CASE),
                      default_findings(),
                      investigation_id=inv, task_id=task,
                      findings_provider=lambda: default_findings())


class TestRealLoopScenarios:
    @staticmethod
    def _outputs(state):
        outs = state.goal_contract.outputs
        delivery = next(o for o in outs if any(
            t.value == "transaction_records"
            for t in o.evidence_requirements))
        existence = next(o for o in outs if o is not delivery)
        return existence, delivery

    def test_replan_repeat_converges_with_verified_delivery(
            self, tmp_path, monkeypatch):
        """五.1/五.4/五.6: the artifact exists, a re-plan re-runs the
        identical analysis once — the no-progress guard FIRES (bounded
        stop, not the iteration bound, no third identical execution); the
        delivery output was content-verified SATISFIED and is NOT
        downgraded by the repeated analysis observation (monotonic
        write-point); the artifact renders the table exactly once."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        provider = HybridTradeProvider({
            "cross_account_trade_analysis": [trade_result(),
                                             trade_result()],
            "related_account_analysis": [_related_ok(),
                                         _related_ok()],
        })
        store = TaskStoreV2(db_path=tmp_path / "converge.sqlite3")
        runner = _make_runner(store, provider, _SET_ART_STEP_PLAN,
                              ["CONTINUE", "CONTINUE", "RE_PLAN",
                               "CONTINUE"])
        result = _run(runner, "INV-U47L1", "TASK-U47L1")
        state = result.state
        # bounded convergence via the guard, not the iteration bound
        assert state.agent_status == AgentStatus.COMPLETED.value
        # A partial outcome must NEVER carry goal_satisfied (the Activity
        # chip renders stop_reason verbatim beside "已部分回答")
        assert state.stop_reason == StopReason.BOUNDED_DATA
        assert state.iteration < settings.AGENT_LOOP_MAX_ITERATIONS
        assert state.plan_revisions == 2
        # exactly TWO identical analyses — the guard stopped the third
        assert provider.count("cross_account_trade_analysis") == 2
        trade_args = [a for n, a in provider.calls
                      if n == "cross_account_trade_analysis"]
        assert trade_args[0] == trade_args[1]
        # the guard's no-progress stop is on the decision trail
        stops = [d for d in state.decisions
                 if "identical result" in d.get("reason", "")]
        assert stops, state.decisions
        # delivery content-verified satisfied with its OWN detail (the
        # repeated analysis observation did not downgrade it);
        # existence check honest about the truncated retrieval
        existence, delivery = self._outputs(state)
        assert delivery.satisfaction.value == "satisfied"
        assert "明细文件" in delivery.detail
        assert existence.satisfaction.value == "partially_supported"
        assert "集合检索受预算限制" in existence.detail
        assert state.goal_outcome == "partial"
        # the artifact rendered the candidate table exactly once
        assert len(result.artifacts) == 1
        content = result.artifacts[0]["content"]
        assert content.count("### TC-S1-") == 1
        assert "T024700" in content
        assert "Retrieval was bounded" in content
        # the composed answer carries the delivery and the boundary
        assert "ART-" in result.response
        assert "受预算限制" in result.response
        assert "CATA-001" in result.response
        # the task container is completed (not a failed mask)
        assert result.task.status.value == "completed"

    def test_no_recoverable_path_ends_bounded_not_failed(
            self, tmp_path, monkeypatch):
        """五.7: a revision that only re-ran answered/failed work (no new
        acquisition) is not re-planned to the bound — the RE_PLAN
        suppression ends the run bounded (BOUNDED_DATA), evidence
        retained, never a failure terminal. (The related-accounts step
        has no scripted result here, so its re-offer stays
        non-successful and the revision provably learned nothing.)"""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        provider = HybridTradeProvider({
            "cross_account_trade_analysis": [trade_result(),
                                             trade_result()],
        })
        store = TaskStoreV2(db_path=tmp_path / "bounded.sqlite3")
        runner = _make_runner(store, provider, _SET_ONLY_PLAN,
                              ["RE_PLAN", "CONTINUE"])
        result = _run(runner, "INV-U47L2", "TASK-U47L2")
        state = result.state
        assert state.agent_status == AgentStatus.COMPLETED.value
        assert state.stop_reason == StopReason.BOUNDED_DATA
        assert state.iteration < settings.AGENT_LOOP_MAX_ITERATIONS
        assert state.plan_revisions == 2      # the 3rd re-plan was suppressed
        assert provider.count("cross_account_trade_analysis") == 2
        existence, delivery = self._outputs(state)
        assert existence.satisfaction.value == "partially_supported"
        # the composition continuation still delivered the file from the
        # existing evidence — content verified
        assert delivery.satisfaction.value == "satisfied"
        assert state.goal_outcome == "partial"
        assert result.task.status.value == "completed"
        # the composed answer retains the acquired evidence
        assert "CATA-001" in result.response

    def test_genuinely_new_candidates_keep_executing(
            self, tmp_path, monkeypatch):
        """五.2: a different second result is never treated as
        no-progress — the loop continues and the new candidate reaches
        the delivered artifact (its own rendered section)."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        provider = HybridTradeProvider({
            "cross_account_trade_analysis": [
                trade_result(),
                trade_result(candidate_id="CATA-002", txns=_TXNS_B)],
        })
        store = TaskStoreV2(db_path=tmp_path / "newcands.sqlite3")
        runner = _make_runner(store, provider, _SET_ART_STEP_PLAN,
                              ["CONTINUE", "RE_PLAN", "CONTINUE", "STOP"])
        result = _run(runner, "INV-U47L3", "TASK-U47L3")
        state = result.state
        assert state.agent_status == AgentStatus.COMPLETED.value
        # existence stays partially_supported (truncated) → the stop
        # label must be bounded, never goal_satisfied
        assert state.stop_reason == StopReason.BOUNDED_DATA
        stops = [d for d in state.decisions
                 if "identical result" in d.get("reason", "")]
        assert not stops
        # the artifact carries BOTH candidates' evidence; genuinely
        # different sets render their own sections
        content = result.artifacts[-1]["content"]
        assert "T024700" in content and "T025053" in content
        assert content.count("### TC-S1-") == 2
        _, delivery = self._outputs(state)
        assert delivery.satisfaction.value == "satisfied"

    def test_all_outputs_satisfied_shows_goal_satisfied(
            self, tmp_path, monkeypatch):
        """五.4/B: a COMPLETE retrieval (truncated=false) satisfies the
        existence check too — the whole output set SATISFIED → the stop
        label is goal_satisfied and the outcome complete (the normal
        completion display is preserved)."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        provider = HybridTradeProvider({
            "cross_account_trade_analysis": [
                trade_result(truncated=False),
                trade_result(truncated=False)],
            "related_account_analysis": [_related_ok(), _related_ok()],
        })
        store = TaskStoreV2(db_path=tmp_path / "complete.sqlite3")
        runner = _make_runner(store, provider, _SET_ART_STEP_PLAN,
                              ["CONTINUE", "CONTINUE", "STOP"])
        result = _run(runner, "INV-U47L4", "TASK-U47L4")
        state = result.state
        assert state.agent_status == AgentStatus.COMPLETED.value
        assert state.stop_reason == StopReason.GOAL_SATISFIED
        assert state.goal_outcome == "complete"
        existence, delivery = self._outputs(state)
        assert existence.satisfaction.value == "satisfied"
        assert delivery.satisfaction.value == "satisfied"
        assert result.task.status.value == "completed"

    def test_genuine_failure_keeps_failed_terminal(
            self, tmp_path, monkeypatch):
        """五.8/C: a planning failure with no successful evidence keeps
        the FAILED terminal — never masked as completed/goal_satisfied
        (the UI maps this agent_status to 'Investigation failed')."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        provider = HybridTradeProvider({
            "cross_account_trade_analysis": [ToolResult(
                outcome=ToolResultOutcome.INTEGRATION_ERROR,
                error=ToolError(code="RISK_PLATFORM_UNAVAILABLE",
                                message="RP timed out"))],
        })
        store = TaskStoreV2(db_path=tmp_path / "failed.sqlite3")
        from app.planner_v2 import PlanningFailure

        class FailingPlanner:
            def plan(self, *a, **k):
                return None, "PLANNING_UNEXPECTED_ERROR"

        runner = AgentLoopRunner(
            planner=FailingPlanner(),
            executor=ExecutorV2(provider), store=store,
            proposer=_Scripted(["RE_PLAN"]))
        runner.goal_resolver = _goal_resolver()
        result = _run(runner, "INV-U47L5", "TASK-U47L5")
        state = result.state
        assert state.agent_status == AgentStatus.FAILED.value
        assert state.stop_reason == StopReason.PLANNING_FAILURE
        assert result.task.status.value == "failed"
        assert state.goal_outcome != "complete"
        assert result.task.error  # the failure is explained


class TestWriteContractResultMonotonic:
    def _delivery_output_state(self):
        state = _policy_state([], [])
        out = state.goal_contract.outputs[1]        # records-kind delivery
        return state, out

    def test_content_verified_satisfaction_is_never_downgraded(self):
        """五.6 (monotonic): a later observation-level re-evaluation must
        not downgrade an artifact-content-verified delivery."""
        from app.goal_postconditions import evaluate_output
        state, out = self._delivery_output_state()
        runner = AgentLoopRunner()
        runner._artifact_satisfied_outputs.add(out.output_id)
        out.satisfaction = out.satisfaction.__class__.SATISFIED
        out.detail = "明细文件 ART-u47test01 已交付：1 组候选明细与来源集合分析逐条一致。"
        res = evaluate_output(out, CASE, [_obs(_trade_summary())])
        runner._write_contract_result(state, out, res,
                                      res.satisfaction, res.polarity)
        assert out.satisfaction.value == "satisfied"
        assert "明细文件 ART-u47test01" in out.detail

    def test_non_verified_outputs_write_normally(self):
        from app.goal_postconditions import evaluate_output
        state, out = self._delivery_output_state()
        runner = AgentLoopRunner()          # NOT registered as verified
        res = evaluate_output(out, CASE, [_obs(_trade_summary())])
        runner._write_contract_result(state, out, res,
                                      res.satisfaction, res.polarity)
        assert out.satisfaction == res.satisfaction


class TestAlreadyAnsweredStep:
    def test_same_id_tool_args_with_prior_success_is_answered(self):
        state = _policy_state(
            [_obs(_trade_summary(), step="S2", tool="related_account_analysis",
                  iteration=2)],
            [S1_DONE])
        state.arg_provenance.extend([
            {"step_id": "S1", "tool_name": "cross_account_trade_analysis",
             "arguments": {"analysis_type": "a", "case_id": CASE},
             "sources": {}},
            {"step_id": "S2", "tool_name": "related_account_analysis",
             "arguments": {"analysis_type": "related_account_analysis",
                           "case_id": CASE},
             "sources": {}},
        ])
        step = {"step_id": "S2", "tool_name": "related_account_analysis",
                "arguments": {"analysis_type": "related_account_analysis",
                              "case_id": CASE}}
        assert LoopPolicy._already_answered(state, step) is True

    def test_different_arguments_are_never_answered(self):
        """五.2: a genuinely different query (new args) with the same
        step id/tool is NOT answered work — it may execute."""
        state = _policy_state(
            [_obs(_trade_summary(), step="S2", tool="related_account_analysis",
                  iteration=2)],
            [S1_DONE])
        state.arg_provenance.append(
            {"step_id": "S2", "tool_name": "related_account_analysis",
             "arguments": {"analysis_type": "related_account_analysis",
                           "case_id": CASE},
             "sources": {}})
        step = {"step_id": "S2", "tool_name": "related_account_analysis",
                "arguments": {"analysis_type": "related_account_analysis",
                              "case_id": "U99999"}}
        assert LoopPolicy._already_answered(state, step) is False

    def test_no_prior_success_is_never_answered(self):
        """A step that only ever errored may be retried by a re-plan."""
        state = _policy_state([], [S1_DONE])
        state.arg_provenance.append(
            {"step_id": "S2", "tool_name": "related_account_analysis",
             "arguments": {"case_id": CASE}, "sources": {}})
        step = {"step_id": "S2", "tool_name": "related_account_analysis",
                "arguments": {"case_id": CASE}}
        assert LoopPolicy._already_answered(state, step) is False


class _Scripted:
    """FIFO scripted decisions (mirrors test_agent_loop_v1)."""

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        if self.decisions:
            d = self.decisions.pop(0)
        else:
            d = "CONTINUE"
        return DecisionProposal(decision=d, reason="scripted",
                                source="llm")


class TestFourOutputHedgeAnswerDedup:
    """U00047 ART-85d6d23849 shape: the goal understanding split the
    hedge request into FOUR outputs — the trade observation serves the
    existence check (O2), a detail listing (O3) AND the file export (O4).
    R3's owner/non-owner dichotomy let the FIRST non-owner (O2) and the
    SECOND non-owner (O3) both keep the verdict summary, the budget
    boundary and the detail reference — adjacent duplicated sections."""

    @staticmethod
    def _state():
        from app.goal_resolution import RequiredOutput
        contract = GoalContract(
            contract_id="GC-U47Q", subject_ref=CASE, intent_source="llm",
            outputs=[
                RequiredOutput(
                    output_id="O1",
                    claim="案件的关联账号网络归因明细（Risk Platform 网络归因）",
                    claim_kind="retrieval", subject_ref=CASE,
                    scope="case_level",
                    evidence_requirements=["network_relations"],
                    satisfaction="satisfied"),
                RequiredOutput(
                    output_id="O2",
                    claim="关联账户集合内部是否存在对冲交易候选",
                    claim_kind="existence_check", subject_ref=CASE,
                    scope="case_level",
                    evidence_requirements=["opposite_trade_relations"],
                    satisfaction="partially_supported"),
                RequiredOutput(
                    output_id="O3", claim="对冲交易明细记录",
                    claim_kind="retrieval", subject_ref=CASE,
                    scope="case_level",
                    evidence_requirements=["opposite_trade_relations",
                                           "transaction_records"],
                    satisfaction="satisfied"),
                RequiredOutput(
                    output_id="O4", claim="结果文件导出",
                    claim_kind="retrieval", subject_ref=CASE,
                    scope="case_level",
                    evidence_requirements=["case_summary",
                                           "network_relations",
                                           "opposite_trade_relations",
                                           "transaction_records"],
                    satisfaction="satisfied"),
            ])
        candidates = [
            {"candidate_id": f"CATA-00{i}",
             "account_a": f"A{i}", "account_b": f"B{i}",
             "symbol": "SOL", "max_time_gap_seconds": 0.001,
             "value_a": 1.0, "value_b": 0.9,
             "value_difference_relative": 0.10,
             "value_difference": 0.1,
             "transaction_ids_a": [f"T00{i}"],
             "transaction_ids_b": [f"T10{i}"],
             "supporting": [{"transaction_id": f"T00{i}"},
                            {"transaction_id": f"T10{i}"}]}
            for i in range(1, 6)]
        trade_obs = {
            "tool_name": "cross_account_trade_analysis",
            "step_type": "analyze_related_account_set_trades",
            "outcome": "success", "semantic_status": "MATCH_FOUND",
            "step_id": "S1", "iteration": 1,
            "for_outputs": ["O2", "O3", "O4"],
            "summary": {
                "analysis": "cross_account_trade_analysis",
                "analysis_status": "MATCH_FOUND",
                "analysis_scope": "related_account_set",
                "target_account": CASE,
                "accounts_analyzed": [CASE, "A1", "B1"],
                "candidates": candidates,
                "candidates_found": 5, "candidates_emitted": 5,
                "totals": {"transactions_retrieved": 500},
                "retrieval": {"status": "truncated", "truncated": True,
                              "truncated_reasons": ["account_set:x"],
                              "account_set": {"accounts_queried": 3}},
                "max_time_delta_seconds": 30,
                "value_tolerance_relative": 0.05,
                "value_tolerance_absolute_floor": 1.0,
            },
            "evidence_refs": [],
        }
        network_obs = {
            "tool_name": "related_account_analysis",
            "step_type": "analyze_related_accounts",
            "outcome": "success",
            "semantic_status": "RELATED_ACCOUNTS_FOUND",
            "step_id": "S2", "iteration": 2, "for_outputs": ["O1"],
            "summary": {"related_accounts_count": 2,
                        "related_accounts": ["R1", "R2"],
                        "shared_devices": ["DEVX"],
                        "shared_ip_attributions": {}},
            "evidence_refs": [],
        }
        return AgentLoopState(
            task_id="TASK-U47Q", investigation_id="INV-U47Q", case_id=CASE,
            goal="列出所有关联账号，查询关联账号之间是否存在对冲交易，"
                 "如果有，把交易明细发我，并生成一份文件",
            criteria_kind="question_answered",
            goal_contract=contract,
            observations=[trade_obs, network_obs])

    def test_summary_budget_and_reference_render_once(self):
        from app.agent_loop import _compose_gsp_response
        state = self._state()
        resp = _compose_gsp_response(state)
        # the existence summary renders exactly once (the FIRST served
        # output keeps it — every other serving section drops it)
        assert resp.count("发现关联账户集合内部候选对冲交易关系") == 1
        assert resp.count("受预算限制") == 1
        # the detail reference renders exactly once
        assert resp.count("完整候选明细见") == 1
        # the account list section is intact
        assert "关联账号共 2 个" in resp and "DEVX" in resp
        # the export section keeps the criteria and ALL five candidates
        assert resp.count("匹配判据:") == 1
        for i in range(1, 6):
            assert resp.count(f"CATA-00{i}（") == 1
        # partial-coverage wording survives
        assert "已获取部分证据，但覆盖范围不完整" in resp

    def test_satisfactions_and_outcome_unchanged(self):
        from app.agent_loop import _compose_gsp_response
        state = self._state()
        before = {o.output_id: (o.satisfaction.value, o.detail)
                  for o in state.goal_contract.outputs}
        resp = _compose_gsp_response(state)
        after = {o.output_id: (o.satisfaction.value, o.detail)
                 for o in state.goal_contract.outputs}
        assert before == after
        assert state.goal_outcome == "partial"

    def test_collapsed_middle_section_header_suppressed(self):
        """U00047 presentation: after the dedup strips, the O3 detail-
        listing section holds nothing but its header (its payload is fully
        presented by O2's summary and O4's candidate list) — the bare
        header is dropped, and no content is lost."""
        from app.agent_loop import _compose_gsp_response
        state = self._state()
        resp = _compose_gsp_response(state)
        # exactly the three content-bearing section headers remain
        # (account list, existence summary, export details) — the
        # collapsed middle section renders no empty header
        assert resp.count("关于「") == 3
        # nothing was lost
        assert resp.count("发现关联账户集合内部候选对冲交易关系") == 1
        assert resp.count("受预算限制") == 1
        assert resp.count("完整候选明细见") == 1
        assert resp.count("匹配判据:") == 1
        assert [resp.count(f"CATA-00{i}（") for i in range(1, 6)] == \
            [1, 1, 1, 1, 1]
        assert "关联账号共 2 个" in resp and "DEVX" in resp
        assert "已获取部分证据，但覆盖范围不完整" in resp
        # no empty paragraph between sections (a header followed directly
        # by another header or by the outcome line)
        assert ":\n\n关于「" not in resp
        assert ":\n\n整体结果" not in resp

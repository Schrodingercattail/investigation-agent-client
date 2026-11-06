"""R2.2.3 G7'' — pre-generation delivery safety gate.

Covers the three frozen BLOCK branches (None-B / None-C / UNKNOWN), the
non-blocking regressions (reviewed / candidate / question intent /
non-delivery steps), and the loop-level integration proof that a blocked
generate_artifact step is NEVER executed (no tool call, no artifact id)
with the frozen terminal semantics (goal_outcome + CLARIFICATION_NEEDED).

GU failure is simulated at the scripted planner-LLM boundary with an
illegal resolution value — the audited U00010 heuristic_single_goal
fallback shape — so the heuristic fallback (and therefore the gate)
fires deterministically. The executor provider records every tool call
so "generate_artifact was not executed" is asserted against the real
execution trace, not against an outcome label.
"""

import json

import pytest

from app.agent_loop import (
    AgentLoopRunner,
    AgentStatus,
    StopReason,
    _delivery_gate_block,
    _goal_coverage_of,
)
from app.executor_v2 import ExecutorV2, ToolProvider
from app.goal_resolution import GoalContract, RequiredOutput
from app.models import (
    Finding,
    FindingCapability,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2

from eval.fixtures import ScriptedLLM, build_context, default_findings

CASE = "U00299"

GU_FALLBACK_SCRIPT = json.dumps({
    "goal_resolution": "BOGUS_NOT_IN_VOCABULARY",
    "goal_type": "artifact_export",
})

B_ASK = "generate all trade records of this account as an artifact"
C_ASK = ("show me the timeline for this finding and also export an "
         "artifact report")


class ScriptedToolProvider(ToolProvider):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.results_last = self.results[-1] if self.results else None
        self.calls = []

    def has(self, tool_name):
        return True

    def get(self, tool_name):
        def invoke(args):
            self.calls.append((tool_name, dict(args)))
            if self.results:
                self.results_last = self.results.pop(0)
            return self.results_last
        return invoke


def make_runner(store, provider, llm_scripts):
    return AgentLoopRunner(
        planner=PlannerV2(ScriptedLLM(llm_scripts)),
        executor=ExecutorV2(provider),
        store=store)


@pytest.fixture()
def store(tmp_path):
    return TaskStoreV2(db_path=tmp_path / "g7_gate_test.sqlite3")


@pytest.fixture()
def findings():
    return default_findings()


def fetch_success():
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"case_id": CASE, "risk_level": "HIGH", "risk_score": 70.0,
              "findings": [], "evidence_refs": [], "signal_refs": [],
              "policy_refs": [], "ext": {}})


# ---------------------------------------------------------------------------
# Pure-function branch coverage (unit)
# ---------------------------------------------------------------------------

def _f3_state(goal, *, contract=None, decision=None, repr_overrides=None,
              plan_steps=None):
    from app.agent_loop import AgentLoopState
    repr_obj = {"goal_type": "artifact_export",
                "goal_resolution": "INFERRED", "source": "heuristic",
                "request_text": goal}
    repr_obj.update(repr_overrides or {})
    return AgentLoopState(
        task_id="TASK-G7", investigation_id="INV-G7", case_id=CASE,
        goal=goal, criteria_kind="artifact_produced",
        goal_contract=contract,
        goal_contract_decision=decision or {},
        goal_representation=repr_obj,
        plan_steps=plan_steps or [])


def _gate_step():
    from app.models import PlanStep, PlanStepStatus
    return PlanStep(step_id="S2", type="generate_artifact",
                    tool_name="artifact_bundle",
                    status=PlanStepStatus.PENDING)


class TestGateBranches:
    def test_none_b_blocks(self):
        state = _f3_state(
            B_ASK,
            decision={"outcome": "heuristic_single_goal"},
            repr_overrides={"proposal_source": None})
        block = _delivery_gate_block(state, _gate_step())
        assert block is not None and block["branch"] == "None-B"
        assert block["goal_outcome"] == "partial"
        assert block["stop_reason"] == "clarification_needed"

    def test_none_c_unresolved_blocks(self):
        state = _f3_state(
            B_ASK,
            repr_overrides={"goal_resolution": "UNRESOLVED",
                            "proposal_source": None})
        block = _delivery_gate_block(state, _gate_step())
        assert block is not None and block["branch"] == "None-C"
        assert block["goal_outcome"] == "unanswerable"

    def test_none_c_multi_category_blocks(self):
        # text matches timeline AND artifact categories (≥2) with no
        # accepted contract → None-C
        state = _f3_state(C_ASK, repr_overrides={"proposal_source": None})
        block = _delivery_gate_block(state, _gate_step())
        assert block is not None and block["branch"] == "None-C"

    def test_unknown_blocks_accepted_contract_missing_origin(self):
        contract = GoalContract(
            contract_id="GC-G7", subject_ref=CASE, intent_source="llm",
            outputs=[RequiredOutput(
                output_id="O1", claim="artifact delivery",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level", evidence_requirements=["transaction_records"],
                postcondition_facets=[], satisfaction="satisfied",
                polarity="positive", detail=None)])
        state = _f3_state(
            B_ASK, contract=contract,
            decision={"outcome": "contract_accepted"},
            repr_overrides={"proposal_source": None, "source": "llm"})
        block = _delivery_gate_block(state, _gate_step())
        assert block is not None and block["branch"] == "UNKNOWN"
        assert block["goal_outcome"] == "partial"

    def test_reviewed_not_blocked(self):
        contract = GoalContract(
            contract_id="GC-G7", subject_ref=CASE, intent_source="llm",
            outputs=[RequiredOutput(
                output_id="O1", claim="artifact delivery",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level", evidence_requirements=["transaction_records"],
                postcondition_facets=[], satisfaction="satisfied",
                polarity="positive", detail=None)])
        state = _f3_state(
            B_ASK, contract=contract,
            decision={"outcome": "contract_accepted"},
            repr_overrides={"proposal_source": "llm", "source": "llm"})
        assert _delivery_gate_block(state, _gate_step()) is None
        assert _goal_coverage_of(state) == "reviewed"

    def test_candidate_not_blocked(self):
        contract = GoalContract(
            contract_id="GC-G7", subject_ref=CASE, intent_source="llm",
            outputs=[RequiredOutput(
                output_id="O1", claim="artifact delivery",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level", evidence_requirements=["transaction_records"],
                postcondition_facets=[], satisfaction="satisfied",
                polarity="positive", detail=None)])
        state = _f3_state(
            B_ASK, contract=contract,
            decision={"outcome": "contract_accepted"},
            repr_overrides={"proposal_source": "heuristic",
                            "source": "heuristic"})
        assert _delivery_gate_block(state, _gate_step()) is None
        assert _goal_coverage_of(state) == "candidate"

    def test_question_intent_not_blocked(self):
        from app.agent_loop import AgentLoopState
        state = AgentLoopState(
            task_id="TASK-G7Q", investigation_id="INV-G7", case_id=CASE,
            goal="what is the threshold for the high activity rule?",
            criteria_kind="question_answered",
            goal_contract_decision={"outcome": "heuristic_single_goal"},
            goal_representation={"goal_type": None,
                                 "goal_resolution": "UNRESOLVED",
                                 "request_text": "threshold"})
        assert not _delivery_gate_block(state, _gate_step())

    def test_non_delivery_step_not_gated(self):
        from app.models import PlanStep, PlanStepStatus
        step = PlanStep(step_id="S1", type="inspect_timeline",
                        tool_name="finding_drilldown",
                        status=PlanStepStatus.PENDING)
        state = _f3_state(
            B_ASK,
            decision={"outcome": "heuristic_single_goal"})
        assert _delivery_gate_block(state, step) is None

    def test_unknown_coverage_never_inherits_delivery(self):
        """UNKNOWN (accepted contract, origin unrecorded) must NOT be
        classified as candidate — delivery permission is not inherited
        from the conservative fallback classification."""
        contract = GoalContract(
            contract_id="GC-G7", subject_ref=CASE, intent_source="llm",
            outputs=[RequiredOutput(
                output_id="O1", claim="artifact delivery",
                claim_kind="retrieval", subject_ref=CASE,
                scope="case_level", evidence_requirements=["transaction_records"],
                postcondition_facets=[], satisfaction="satisfied",
                polarity="positive", detail=None)])
        state = _f3_state(
            B_ASK, contract=contract,
            decision={"outcome": "contract_accepted"},
            repr_overrides={"proposal_source": None})
        assert _goal_coverage_of(state) == "unknown"
        assert _goal_coverage_of(state) != "candidate"


# ---------------------------------------------------------------------------
# Loop-level integration (REAL _run_loop path via the scripted-session
# seam for goal understanding; RP adapter patched offline): the blocked
# generate_artifact step is NEVER executed (asserted against the real
# execution trace), and the frozen terminal semantics hold.
# ---------------------------------------------------------------------------

HEURISTIC_ARTIFACT_GOAL = {
    "case_id": CASE,
    "goal_type": "artifact_export",
    "goal_resolution": "INFERRED",
    "subject_type": "case",
    "subject_ref": CASE,
    "ambiguous_goal_types": [],
    "reason": "scripted GU fallback (heuristic_single_goal shape)",
    "source": "heuristic",
}

HEURISTIC_UNRESOLVED_GOAL = {
    "case_id": CASE,
    "goal_type": None,
    "goal_resolution": "UNRESOLVED",
    "subject_type": "case",
    "subject_ref": CASE,
    "ambiguous_goal_types": [],
    "reason": "scripted GU fallback (unresolved shape)",
    "source": "heuristic",
}


def _run_with_session(tmp_path, monkeypatch, ask, goal_block, findings):
    import json as _json
    session = {"plans": [], "decisions": [],
               "goal": dict(goal_block, request_text=ask)}
    path = tmp_path / "g7_session.json"
    path.write_text(_json.dumps(session, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE", str(path))

    import tests.test_risk_platform_adapter_v2 as fx
    from app.domain_tools import risk_case_fetch
    evidence = fx.rp_evidence_payload(CASE)
    explanation = fx.rp_explanation_payload()
    monkeypatch.setattr(
        risk_case_fetch.RiskPlatformAdapter, "fetch_case",
        lambda self, uid: (evidence, explanation))

    provider = ScriptedToolProvider([fetch_success()])
    store = TaskStoreV2(db_path=tmp_path / "g7_loop.sqlite3")
    runner = AgentLoopRunner(
        executor=ExecutorV2(provider), store=store)
    result = runner.run(ask, build_context(None, CASE), findings)
    return result, provider


class TestLoopIntegration:
    def test_none_b_loop_blocks_before_execution(
            self, tmp_path, monkeypatch, findings):
        provider = ScriptedToolProvider([fetch_success()])
        store = TaskStoreV2(db_path=tmp_path / "g7_b.sqlite3")
        import json as _json
        session = {"plans": [], "decisions": [],
                   "goal": dict(HEURISTIC_ARTIFACT_GOAL, request_text=B_ASK)}
        path = tmp_path / "g7_b_session.json"
        path.write_text(_json.dumps(session, ensure_ascii=False),
                        encoding="utf-8")
        monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE", str(path))
        import importlib
        import tests.test_risk_platform_adapter_v2 as fx
        _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
        monkeypatch.setattr(
            _rcf.RiskPlatformAdapter, "fetch_case",
            lambda self, uid: (fx.rp_evidence_payload(CASE),
                               fx.rp_explanation_payload()))
        runner = AgentLoopRunner(executor=ExecutorV2(provider), store=store)
        result = runner.run(B_ASK, build_context(None, CASE), findings)

        assert result.state.agent_status == AgentStatus.BLOCKED.value
        assert result.state.stop_reason == \
            StopReason.CLARIFICATION_NEEDED
        assert result.state.goal_outcome == "partial"
        executed = [name for name, _args in provider.calls]
        assert "generate_artifact" not in executed
        assert result.state.artifact_ids == []
        assert result.task.artifact_ids == []
        # WP-2 planning-time clarification wording (calibrated from the
        # phase-1 loop-gate wording; same frozen semantics)
        assert ("未生成 artifact" in result.response
                or "no artifact was generated" in result.response)
        assert result.task.status.value == "failed"

    def test_none_c_multi_category_loop_blocks(
            self, tmp_path, monkeypatch, findings):
        provider = ScriptedToolProvider([fetch_success()])
        store = TaskStoreV2(db_path=tmp_path / "g7_c.sqlite3")
        import json as _json
        session = {"plans": [], "decisions": [],
                   "goal": dict(HEURISTIC_ARTIFACT_GOAL, request_text=C_ASK)}
        path = tmp_path / "g7_c_session.json"
        path.write_text(_json.dumps(session, ensure_ascii=False),
                        encoding="utf-8")
        monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE", str(path))
        import importlib
        import tests.test_risk_platform_adapter_v2 as fx
        _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
        monkeypatch.setattr(
            _rcf.RiskPlatformAdapter, "fetch_case",
            lambda self, uid: (fx.rp_evidence_payload(CASE),
                               fx.rp_explanation_payload()))
        runner = AgentLoopRunner(executor=ExecutorV2(provider), store=store)
        result = runner.run(C_ASK, build_context(None, CASE), findings)

        assert result.state.agent_status == AgentStatus.BLOCKED.value
        assert result.state.stop_reason == \
            StopReason.CLARIFICATION_NEEDED
        # R2.2.3 WP-2: the planning-time clarify terminal uniformly uses
        # PARTIAL + CLARIFICATION_NEEDED (the authorized terminal; the
        # phase-1 loop-level G7''-C UNANSWERABLE nuance is superseded —
        # the planner refuses before any execution)
        assert result.state.goal_outcome == "partial"
        assert "generate_artifact" not in [name for name, _ in
                                           provider.calls]
        assert result.state.artifact_ids == []

    def test_none_d_question_intent_not_blocked(
            self, tmp_path, monkeypatch, findings):
        """None-D: a question-intent fallback run continues its read-only
        answering path — the G7'' gate never fires for non-delivery
        intents."""
        import json as _json
        provider = ScriptedToolProvider([fetch_success()])
        store = TaskStoreV2(db_path=tmp_path / "g7_d.sqlite3")
        session = {"plans": [], "decisions": [],
                   "goal": dict(HEURISTIC_UNRESOLVED_GOAL,
                                request_text="what is the risk level?")}
        path = tmp_path / "g7_d_session.json"
        path.write_text(_json.dumps(session, ensure_ascii=False),
                        encoding="utf-8")
        monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE", str(path))
        import importlib
        import tests.test_risk_platform_adapter_v2 as fx
        _rcf = importlib.import_module("app.domain_tools.risk_case_fetch")
        monkeypatch.setattr(
            _rcf.RiskPlatformAdapter, "fetch_case",
            lambda self, uid: (fx.rp_evidence_payload(CASE),
                               fx.rp_explanation_payload()))
        runner = AgentLoopRunner(executor=ExecutorV2(provider), store=store)
        result = runner.run("what is the risk level?",
                            build_context(None, CASE), findings)
        assert result.state.stop_reason != \
            StopReason.CLARIFICATION_NEEDED
        assert "generate_artifact" not in [name for name, _ in
                                           provider.calls]

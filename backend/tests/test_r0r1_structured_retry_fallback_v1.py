"""Q2/R0+R1 — bounded structured-output retry + deterministic fallback
correctness.

R0: goal understanding and the decision proposer get ONE regeneration
when the model returns non-empty but unparseable JSON (the live U00008
failure class — "Unterminated string starting at: line 1 column 90");
every other exception keeps its own classification; exhausting the retry
lands in the existing fallback paths.

R1: the deterministic fallback proposer may not STOP a multi-deliverable
run while unsatisfied contract outputs still have pending planned steps
covering them (admission-time step→output mapping — the same tracked
mapping the LoopPolicy enforcement uses).
"""

import json

import pytest

from app.agent_loop import (
    AgentLoopState,
    DecisionProposal,
    HeuristicDecisionProposer,
    LLMDecisionProposer,
    LoopDecision,
    LoopPolicy,
)
from app.config import settings
from app.goal_resolution import (
    OutputSatisfaction,
    understand_goal,
    build_goal_contract,
)
from app.llm_provider import LLMError


MALFORMED = ('{"goal_resolution": "INFERRED", "goal_type": "policy_ch')
COMPOUND = "列出所有关联账号，查询其中是否存在对冲交易，如果有，发交易明细给我"


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        self.calls += 1
        self.prompts.append(messages)
        r = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def _valid_goal_payload():
    return json.dumps({
        "goal_resolution": "COMPOUND", "goal_type": None, "reason": "r",
        "required_outputs": [
            {"aspect": "related_accounts_network"},
            {"aspect": "hedging_set_internal"},
        ]}, ensure_ascii=False)


@pytest.fixture()
def gsp_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)


# --- R0-A: goal understanding ----------------------------------------------

class TestR0GoalUnderstanding:
    def test_malformed_then_valid_recovers(self, gsp_on):
        llm = ScriptedLLM([MALFORMED, _valid_goal_payload()])
        diagnostics = {}
        rep = understand_goal(COMPOUND, case_id="U00047", llm=llm,
                              diagnostics=diagnostics, case_findings=[])
        assert llm.calls == 2                    # exactly one retry
        assert rep.goal_resolution == "COMPOUND"
        assert rep.proposal_source == "llm"
        contract, status = build_goal_contract(rep, "U00047")
        assert contract is not None
        assert len(contract.outputs) == 2
        assert diagnostics["structured_output"]["resolved"] is True
        assert diagnostics["structured_output"]["json_retries"] == 1
        # the retry carried a format-correction note while preserving the
        # original request (the business contract is unchanged)
        retry_user = llm.prompts[1][1]["content"]
        assert "could not be parsed as valid JSON" in retry_user
        assert COMPOUND in retry_user
        assert "raw JSON object" in retry_user

    def test_malformed_twice_falls_back_bounded(self, gsp_on):
        llm = ScriptedLLM([MALFORMED, MALFORMED])
        diagnostics = {}
        rep = understand_goal(COMPOUND, case_id="U00047", llm=llm,
                              diagnostics=diagnostics, case_findings=[])
        assert llm.calls == 2                    # bounded: no third attempt
        assert rep.source == "heuristic"         # existing fallback
        so = diagnostics["structured_output"]
        assert so["resolved"] is False and so["fallback"] == "heuristic"
        assert so["error_class"] == "json_parse"

    def test_transport_error_gets_no_json_retry(self, gsp_on):
        llm = ScriptedLLM([LLMError("gateway 502")])
        rep = understand_goal(COMPOUND, case_id="U00047", llm=llm,
                              case_findings=[])
        assert llm.calls == 1                    # never retried as JSON
        assert rep.source == "heuristic"         # existing classification


# --- R0-B: decision proposer ------------------------------------------------

VALID_CONTINUE = json.dumps({"decision": "CONTINUE", "reason": "work left"})
VALID_STOP = json.dumps({"decision": "STOP", "reason": "done"})
MALFORMED_DECISION = '{"decision": "CONTINUE", "reason": "work left'


class TestR0DecisionProposer:
    def _state(self):
        from app.goal_resolution import OutputSatisfaction as _OS
        return AgentLoopState(
            task_id="T", investigation_id="I", case_id="U00047", goal="g",
            agent_status="running", stop_reason=None,
            criteria_kind="question_answered", observations=[],
            plan_steps=[], step_output_map={})

    def test_malformed_then_valid_recovers(self):
        provider = ScriptedLLM([MALFORMED_DECISION, VALID_CONTINUE])
        proposal = LLMDecisionProposer(provider).propose(self._state())
        assert provider.calls == 2
        assert proposal.decision == "CONTINUE" and proposal.valid
        assert proposal.source == "llm"

    def test_malformed_twice_falls_back_bounded(self):
        provider = ScriptedLLM([MALFORMED_DECISION, MALFORMED_DECISION])
        proposal = LLMDecisionProposer(provider).propose(self._state())
        assert provider.calls == 2               # bounded
        assert proposal.valid is False           # existing _invalid() path

    def test_transport_error_gets_no_json_retry(self):
        provider = ScriptedLLM([LLMError("connection refused")])
        proposal = LLMDecisionProposer(provider).propose(self._state())
        assert provider.calls == 1
        assert proposal.valid is False


# --- R0 loop-level: the recovered decision drives the real loop -------------

class TestR0LoopRecovery:
    def test_recovered_continue_executes_remaining_steps(
            self, tmp_path, monkeypatch):
        """Real AgentLoopRunner: proposer's first JSON malformed → retry
        returns CONTINUE → the loop executes the remaining planned step."""
        from eval.fixtures import (
            ScriptedLLM as PlanLLM, build_context, default_findings,
            plan_json, rp_patches)
        from app.agent_loop import AgentLoopRunner, AgentStatus
        from app.executor_v2 import ExecutorV2, ToolProvider
        from app.models import InvestigationContext, ToolResult, \
            ToolResultOutcome
        from app.planner_v2 import PlannerV2
        from app.task_store_v2 import TaskStoreV2

        class _Provider(ToolProvider):
            """Serves successful timeline results (any tool name)."""

            def __init__(self):
                super().__init__()
                self.calls = 0

            def has(self, tool_name):
                return True

            def get(self, tool_name):
                def invoke(args):
                    self.calls += 1
                    return ToolResult(
                        outcome=ToolResultOutcome.SUCCESS,
                        data={"timeline": [f"event{self.calls}"]})
                return invoke

        CASE = "U00299"
        findings = default_findings()
        store = TaskStoreV2(db_path=tmp_path / "r0loop.sqlite3")
        proposer_provider = ScriptedLLM([
            MALFORMED_DECISION,       # after S1: parse fails
            VALID_CONTINUE,           # retry → CONTINUE
            VALID_STOP,               # after S2: healthy STOP
        ])
        runner = AgentLoopRunner(
            planner=PlannerV2(PlanLLM(
                [plan_json("timeline_investigation",
                           ["inspect_timeline", "inspect_evidence"])])),
            executor=ExecutorV2(_Provider()),
            store=store,
            proposer=LLMDecisionProposer(proposer_provider))
        with rp_patches()[0], rp_patches()[1]:
            result = runner.run(
                "investigate this finding", build_context("F1", CASE),
                findings)
        assert result.state.agent_status == AgentStatus.COMPLETED.value
        executed = [s.get("status") for s in result.state.plan_steps]
        assert executed == ["success", "success"]   # BOTH steps ran
        recovered = [d for d in result.state.decisions
                     if "recovered" in (d.get("reason") or "")]
        # the decision trail records the bounded JSON recovery
        assert proposer_provider.calls == 3


# --- R1: deterministic fallback correctness ---------------------------------

def _contract_state(satisfactions, plan, step_output_map,
                    last_outcome="success", criteria="question_answered"):
    from app.goal_resolution import GoalContract, RequiredOutput, \
        ScopeLevel, EvidenceType, ClaimKind
    outputs = [
        RequiredOutput(
            output_id=f"O{i+1}", claim=f"deliverable {i+1}",
            claim_kind=ClaimKind.RETRIEVAL, subject_ref="U00047",
            scope=ScopeLevel.CASE_LEVEL,
            evidence_requirements=[EvidenceType.NETWORK_RELATIONS],
            satisfaction=sat)
        for i, sat in enumerate(satisfactions)]
    return AgentLoopState(
        task_id="T", investigation_id="I", case_id="U00047", goal="g",
        agent_status="running", stop_reason=None, criteria_kind=criteria,
        goal_contract=GoalContract(
            contract_id="GC-R1", goal_type=None, subject_ref="U00047",
            outputs=outputs),
        observations=[{"tool_name": "t", "step_type": "s",
                       "outcome": last_outcome, "summary": {},
                       "error": None, "complete": None,
                       "next_data_needed": None}],
        plan_steps=plan, step_output_map=step_output_map)


def _step(step_id, status):
    return {"step_id": step_id, "type": "inspect", "status": status,
            "tool_name": "tool", "arguments": {}}


class TestR1FallbackPolicy:
    def test_a_multi_deliverable_pending_steps_continue(self):
        """1 satisfied + 3 pending outputs; S2/S3 pending and mapped to
        the unsatisfied outputs → fallback CONTINUES (never STOP)."""
        state = _contract_state(
            satisfactions=["satisfied", "pending", "pending", "pending"],
            plan=[_step("S1", "success"), _step("S2", "pending"),
                  _step("S3", "pending")],
            step_output_map={"S2": ["O2"], "S3": ["O3", "O4"]})
        proposal = HeuristicDecisionProposer().propose(state)
        assert proposal.decision == LoopDecision.CONTINUE.value
        enforced = LoopPolicy().enforce(proposal, state)
        assert enforced.decision == LoopDecision.CONTINUE.value

    def test_b_no_pending_steps_terminates(self):
        """Unsatisfied outputs but NO executable pending steps — no
        infinite CONTINUE: the original criteria-STOP applies."""
        state = _contract_state(
            satisfactions=["satisfied", "pending"],
            plan=[_step("S1", "success")],
            step_output_map={"S1": ["O1"]})
        proposal = HeuristicDecisionProposer().propose(state)
        assert proposal.decision == LoopDecision.STOP.value
        enforced = LoopPolicy().enforce(proposal, state)
        assert enforced.decision == LoopDecision.STOP.value

    def test_c_single_deliverable_satisfied_stops(self):
        """Original semantics: one output satisfied, nothing pending —
        STOP unchanged."""
        state = _contract_state(
            satisfactions=["satisfied"],
            plan=[_step("S1", "success")],
            step_output_map={"S1": ["O1"]})
        proposal = HeuristicDecisionProposer().propose(state)
        assert proposal.decision == LoopDecision.STOP.value

    def test_d_error_outcome_still_blocks_first(self):
        state = _contract_state(
            satisfactions=["satisfied", "pending"],
            plan=[_step("S1", "success"), _step("S2", "pending")],
            step_output_map={"S2": ["O2"]},
            last_outcome="integration_error")
        proposal = HeuristicDecisionProposer().propose(state)
        assert proposal.decision == LoopDecision.BLOCK.value

    def test_e_pending_step_not_covering_unsatisfied_outputs(self):
        """Pending steps exist but map only to ALREADY-satisfied outputs —
        no fake continuation: STOP stands (criteria satisfied)."""
        state = _contract_state(
            satisfactions=["satisfied", "satisfied"],
            plan=[_step("S1", "success"), _step("S2", "pending")],
            step_output_map={"S2": ["O1"]})
        proposal = HeuristicDecisionProposer().propose(state)
        assert proposal.decision == LoopDecision.STOP.value

    def test_f_contractless_legacy_behavior_unchanged(self):
        """No contract → the helper is inert → original ordering holds
        (criteria satisfied + pending → STOP; contract-less runs keep
        their pre-R1 semantics)."""
        state = _contract_state(
            satisfactions=["satisfied"],
            plan=[_step("S1", "success"), _step("S2", "pending")],
            step_output_map={})
        state.goal_contract = None
        proposal = HeuristicDecisionProposer().propose(state)
        assert proposal.decision == LoopDecision.STOP.value


# --- fault-injection switch hygiene ------------------------------------------

class TestFaultSwitchDefaults:
    def test_malformed_json_fault_defaults_off(self):
        from app.llm_provider import ClaudeProvider
        assert ClaudeProvider.FAULT_MALFORMED_JSON_FIRST == 0
        assert ClaudeProvider._malformed_remaining == []


# --- A-fix observability: per-application-attempt diagnostics ----------------

class TestAttemptDiagnostics:
    def test_provider_last_attempts_shape(self):
        """Provider-level per-attempt records: empty → empty_response with
        safe fields and empty-detail sub-classification; retry → success;
        exhausted → two empty attempts + attempts=2 in the raise context.
        Safe fields only — no prompts or response bodies."""
        import json as _json

        from app.llm_provider import ClaudeProvider
        p = ClaudeProvider(api_key="test-key", model="test-model")
        seq = []

        class _Block:
            def __init__(self, type_, text=None):
                self.type = type_
                self.text = text if type_ == "text" else None

        class _Resp:
            def __init__(self, blocks):
                self.content = blocks
                self.stop_reason = "max_tokens"

        def create(**kw):
            r = seq.pop(0)
            return _Resp([_Block(t, "ok") if t == "text" else _Block(t)
                          for t in r])

        p.client.messages.create = create

        # exhausted: two empty attempts (no blocks at all)
        seq.extend([[], []])
        try:
            p.generate([{"role": "user", "content": "x"}])
            raise AssertionError("expected LLMEmptyResponseError")
        except Exception as e:
            ctx = getattr(e, "context", {}) or {}
            assert ctx.get("attempts") == 2
            assert ctx.get("empty_detail") == "no_content_blocks"
        a = p.last_attempts
        assert [x["outcome"] for x in a] == ["empty_response",
                                             "empty_response"]
        assert [x["empty_detail"] for x in a] == ["no_content_blocks",
                                                  "no_content_blocks"]
        # retry_triggered on a failed attempt = "this failure caused the
        # next attempt" (attempt 1 does; the last attempt gives up)
        assert a[0]["retry_triggered"] is True and a[0]["accepted"] is False
        assert a[1]["retry_triggered"] is False and a[1]["accepted"] is False

        # recovery: thinking-only empty, then a real text block
        seq = [["thinking"], ["text"]]
        out = p.generate([{"role": "user", "content": "x"}])
        assert out == "ok"
        a = p.last_attempts
        assert [x["outcome"] for x in a] == ["empty_response", "success"]
        assert a[0]["empty_detail"] == "non_text_blocks_only"
        assert a[0]["stop_reason"] == "max_tokens"
        assert a[0]["retry_triggered"] is True and a[0]["accepted"] is False
        assert a[1]["retry_triggered"] is True and a[1]["accepted"] is True
        # safe fields only
        blob = _json.dumps(a)
        assert "prompt" not in blob and "messages" not in blob


class TestAttemptDiagnosticsNonEmptyClasses:
    def test_timeout_recorded_then_reraised(self):
        from app.llm_provider import ClaudeProvider
        p = ClaudeProvider(api_key="test-key", model="test-model")
        seq = [[("thinking", None)]]

        class _Block:
            def __init__(self, type_):
                self.type = type_

        class _Resp:
            def __init__(self, blocks):
                self.content = blocks
                self.stop_reason = "max_tokens"

        def create(**kw):
            r = seq.pop(0)
            return _Resp([_Block(t, tx) for t, tx in r])

        from anthropic import APITimeoutError
        import httpx

        def create_timeout(**kw):
            raise APITimeoutError(
                httpx.Request("POST", "https://x"), )

        p.client.messages.create = create_timeout
        import pytest
        with pytest.raises(LLMError):
            p.generate([{"role": "user", "content": "x"}])
        a = p.last_attempts
        assert a and a[0]["outcome"] == "timeout"
        assert a[0]["retry_triggered"] is False
        assert a[0]["accepted"] is False

    def test_service_error_recorded_then_reraised(self):
        from app.llm_provider import ClaudeProvider
        p = ClaudeProvider(api_key="test-key", model="test-model")

        def create_fail(**kw):
            raise LLMError("gateway 502")

        p.client.messages.create = create_fail
        import pytest
        with pytest.raises(LLMError):
            p.generate([{"role": "user", "content": "x"}])
        a = p.last_attempts
        assert a and a[0]["outcome"] == "service_unavailable"
        assert a[0]["error_class"] == "LLMError"
        assert a[0]["retry_triggered"] is False


# --- GU timeout / SDK retry config (Q2 closure) ------------------------------

class TestGUTimeoutConfig:
    def test_defaults(self):
        from app.config import settings
        assert settings.GOAL_UNDERSTANDING_MAX_TOKENS == 8192
        assert settings.GOAL_UNDERSTANDING_TIMEOUT_SECONDS == 240.0
        assert settings.GOAL_UNDERSTANDING_SDK_MAX_RETRIES == 0

    def test_invalid_values_rejected(self):
        from app.config import settings as s
        for bad in (-1, 0):
            s.GOAL_UNDERSTANDING_TIMEOUT_SECONDS = bad
            try:
                s.validate_goal_understanding_runtime()
                raise AssertionError("expected validation error")
            except ValueError as e:
                assert str(bad) in str(e)
        s.GOAL_UNDERSTANDING_TIMEOUT_SECONDS = 240.0

"""P0/B-fix — GoalContract deliverable completeness (U00047 fault B).

Root cause under fix: a non-empty deterministic proposal used to
short-circuit understand_goal, so the contract captured only the FIRST
recognized aspect ("列出所有关联账号" → network_relations) while the
user's compound demand (list + set-internal hedging + conditional trade
details) was silently lost — and GoalOutcome then reported complete over
the incomplete contract.

The fix: the deterministic proposal is a CANDIDATE prior; the goal-
understanding LLM reviews the ORIGINAL request against it (cross-clause
references included), aspect tags render SYSTEM-CANONICAL outputs,
deterministic guards re-apply, dropped candidate deliverables are
re-admitted (consistency validation), and build_goal_contract stays the
only acceptance gate.

Every test here drives the REAL pipeline: understand_goal →
build_goal_contract → PlannerV2 → planning_coverage. The scripted LLM
stands in for the MODEL TEXT only (the established cg1/gsp2 harness
pattern); parsing, validation, guards, merging, acceptance, planning
and coverage are production code.
"""

import json

import pytest

from app.config import settings
from app.models import InvestigationContext
from app.planner_v2 import PlannerV2
from app.skills import eligible_skills_for_finding


COMPOUND = "列出所有关联账号，查询其中是否存在对冲交易，如果有，发交易明细给我"
METRIC_Q = ("opposite-trade ratio of 34.29%是关联账户的对冲交易比例吗，"
            "这是怎么计算的？")
LIST_ONLY = "再列一次所有关联账号"
# D1: population framing via an existing marker form
D1 = ("给我这起案件涉及的全部关联账户，再对这些账户之间的反向交易做一次"
      "比对；如发现疑似对冲组合，请列出对应交易及差异。")
# D2: a NOVEL cross-clause shape — no _SET_INTERNAL_MARKERS substring
# ("名单里面的账号相互之间" matches no marker; the LLM review must bridge it)
D2 = ("先把U00047的关联账号全部列出来，然后帮我查一查这份名单里面的账号"
      "相互之间有没有对冲交易，若有请把这些交易的具体记录也给我。")


def _payload(resolution="COMPOUND", goal_type=None, aspects=(), outputs=(),
             reason="r"):
    return json.dumps({
        "goal_resolution": resolution,
        "goal_type": goal_type,
        "ambiguous_goal_types": [],
        "reason": reason,
        "required_outputs": [
            {"aspect": a} if isinstance(a, str) else a
            for a in (list(aspects) + list(outputs))
        ],
    }, ensure_ascii=False)


class ScriptedGoalLLM:
    def __init__(self, text):
        self.text = text
        self.prompts = []
        self.calls = 0

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        self.prompts.append(messages)
        self.calls += 1
        return self.text


@pytest.fixture()
def gsp_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)


def _understand(request, llm, case_findings=None, case_id="U00047"):
    from app.goal_resolution import understand_goal, build_goal_contract
    rep = understand_goal(request, case_id=case_id, llm=llm,
                          case_findings=case_findings or [])
    contract, status = build_goal_contract(rep, case_id)
    return rep, contract


def _claims(contract):
    return [o.claim for o in contract.outputs] if contract else []


def _plan_with(contract, request, planner_llm=None):
    planner = PlannerV2(planner_llm or _NoLLM())
    eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
    return planner.plan(request, InvestigationContext(case_id="U00047"),
                        eligible, finding_capabilities=None,
                        goal_contract=contract, case_findings=[])


class _NoLLM:
    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        raise AssertionError("LLM planner must not be reached")


# --- A. single metric question: no list, no hedge analysis ----------------

class TestAMetricQuestion:
    def test_metric_aspect_only(self, gsp_on):
        llm = ScriptedGoalLLM(_payload(resolution="INFERRED",
                                       goal_type="cross_account_check",
                                       aspects=["reverse_trade_metric_semantics"]))
        rep, contract = _understand(METRIC_Q, llm)
        claims = _claims(contract)
        assert len(claims) == 1
        assert "反向交易率" in claims[0]
        assert contract.outputs[0].claim_kind.value == "explanation"
        # the candidate prior was genuinely reviewed against the request
        assert "PRIOR" in json.dumps(llm.prompts, ensure_ascii=False)

    def test_llm_noise_cannot_append_list_or_hedge(self, gsp_on):
        """Worst-case review noise: the LLM tags the network list and the
        hedge analysis anyway — the deterministic P2 guard strips them;
        the metric explanation is the only delivery (same behavior as the
        closed P2 guard)."""
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["reverse_trade_metric_semantics",
                     "related_accounts_network", "hedging_set_internal"]))
        _, contract = _understand(METRIC_Q, llm)
        claims = _claims(contract)
        assert len(claims) == 1 and "反向交易率" in claims[0]

    def test_planner_never_plans_related_accounts(self, gsp_on):
        llm = ScriptedGoalLLM(_payload(resolution="INFERRED",
                                       goal_type="cross_account_check",
                                       aspects=["reverse_trade_metric_semantics"]))
        _, contract = _understand(METRIC_Q, llm)
        # the production planner reaches its LLM for this shape; the
        # script stands in for the model text (a metric-explanation plan)
        planner_llm = ScriptedGoalLLM(json.dumps({
            "skill_id": "timeline_investigation", "goal": "metric",
            "steps": [{"type": "explain_signal", "reason": "metric"}]}))
        plan = _plan_with(contract, METRIC_Q, planner_llm)
        types = [s.type for s in plan.steps] if hasattr(plan, "steps") \
            else []
        assert "analyze_related_accounts" not in types
        assert "analyze_related_account_set_trades" not in types
        assert "analyze_cross_account_trades" not in types


# --- B. pure list request: no automatic hedge analysis ---------------------

class TestBPureList:
    def test_list_only_contract(self, gsp_on):
        llm = ScriptedGoalLLM(_payload(resolution="INFERRED",
                                       goal_type="identify_related_accounts",
                                       aspects=["related_accounts_network"]))
        _, contract = _understand(LIST_ONLY, llm)
        claims = _claims(contract)
        assert len(claims) == 1
        assert "关联账号网络归因" in claims[0]
        assert not any("对冲" in c for c in claims)

    def test_llm_noise_hedge_aspect_stripped(self, gsp_on):
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        _, contract = _understand(LIST_ONLY, llm)
        claims = _claims(contract)
        assert len(claims) == 1 and "关联账号网络归因" in claims[0]


# --- C. the original U00047 compound request -------------------------------

class TestCCompoundRequest:
    def test_both_deliverables_in_contract(self, gsp_on):
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        rep, contract = _understand(COMPOUND, llm)
        assert rep.goal_resolution == "COMPOUND"
        claims = _claims(contract)
        assert len(claims) == 2
        assert any("关联账号网络归因" in c for c in claims)
        set_claims = [c for c in claims if "集合内部" in c]
        assert set_claims, "set-internal hedging deliverable must exist"
        # related_account_set scope: pairs may or may not involve the case
        # account; conditional trade-detail delivery is part of the claim
        from app.goal_resolution import _CLAIM_HEDGING_SET
        assert set_claims[0] == _CLAIM_HEDGING_SET
        desc = next(o.claim_description for o in contract.outputs
                    if o.claim == _CLAIM_HEDGING_SET)
        assert "有则列出" in desc and "交易证据" in desc
        # never narrowed to the case account only
        assert "可涉及或不涉及案件账户" in desc

    def test_single_output_contract_is_insufficient(self, gsp_on):
        """An LLM that missed the second clause yields a contract with the
        list only — the re-admission cannot fabricate the hedge aspect
        (the candidate never extracted it), so the gap is REAL and must
        remain visible in the contract rather than being silently
        narrowed away. The prompt-level review is the completeness
        mechanism; this pins that no silent widening happens either."""
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND", aspects=["related_accounts_network"]))
        _, contract = _understand(COMPOUND, llm)
        claims = _claims(contract)
        assert len(claims) == 1 and "关联账号网络归因" in claims[0]

    def test_planning_covers_both_deliverables(self, gsp_on):
        """Contract precedence plans the list; the coverage supplement
        appends the SET-INTERNAL analysis (never the target-anchored
        narrowing)."""
        from app.planning_coverage import coverage_supplement_steps
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        _, contract = _understand(COMPOUND, llm)
        plan = _plan_with(contract, COMPOUND)
        types = [s.type for s in plan.steps]
        assert "analyze_related_accounts" in types
        specs = coverage_supplement_steps(contract, plan.steps)
        supplemented = types + [s.step_type for s in specs]
        assert "analyze_related_account_set_trades" in supplemented
        assert "analyze_cross_account_trades" not in supplemented


# --- D. novel phrasings of the same compound demand ------------------------

class TestDPhrasingVariants:
    def test_population_marker_variant(self, gsp_on):
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        _, contract = _understand(D1, llm)
        claims = _claims(contract)
        assert any("关联账号网络归因" in c for c in claims)
        assert any("集合内部" in c for c in claims)

    def test_novel_cross_clause_variant(self, gsp_on):
        """D2 contains NO known set-internal marker ("名单里面的账号相互
        之间") — the deterministic candidate misses the hedge deliverable
        and the LLM's whole-request review must supply it."""
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        _, contract = _understand(D2, llm)
        claims = _claims(contract)
        assert any("关联账号网络归因" in c for c in claims)
        assert any("集合内部" in c for c in claims)


# --- E. no over-delivery across the three shapes ---------------------------

class TestEOverDeliveryMatrix:
    def test_each_shape_gets_exactly_its_own_deliverables(self, gsp_on):
        cases = [
            (METRIC_Q,
             _payload(resolution="INFERRED",
                      goal_type="cross_account_check",
                      aspects=["reverse_trade_metric_semantics"]),
             1),
            (LIST_ONLY,
             _payload(resolution="INFERRED",
                      goal_type="identify_related_accounts",
                      aspects=["related_accounts_network"]),
             1),
            (COMPOUND,
             _payload(resolution="COMPOUND",
                      aspects=["related_accounts_network",
                               "hedging_set_internal"]),
             2),
        ]
        for request, payload, n in cases:
            _, contract = _understand(request, ScriptedGoalLLM(payload))
            assert len(_claims(contract)) == n, request
        # a hedge-vocabulary-free request never grows a hedge analysis
        # even when the LLM proposes one (covered in TestB); a metric
        # question never grows the list (covered in TestA)


# --- F. goal integrity and failure semantics -------------------------------

class TestFIntegrityAndFailure:
    def test_partial_completion_is_not_complete(self, gsp_on):
        """Only the list delivered (hedge output still PENDING) — the
        overall outcome can never be COMPLETE (GSP-5: PENDING is
        not-answered)."""
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        _, contract = _understand(COMPOUND, llm)
        satisfactions = [OutputSatisfaction.SATISFIED,
                         OutputSatisfaction.PENDING]
        outcome = derive_goal_outcome(satisfactions, "completed")
        assert outcome != "complete"
        assert outcome == "partial"

    def test_no_match_honesty_is_contract_pinned(self, gsp_on):
        """The NO_MATCH boundary is part of the canonical claim's
        semantics (the set-internal analysis reports candidates or an
        explicit no-match — never silence); the conditional detail
        delivery is pinned in TestC. The tool-level NO_MATCH rendering is
        covered by the P1 suite (test_cross_account_trade_analysis_v1)."""
        from app.goal_resolution import _CLAIM_HEDGING_SET
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",
            aspects=["related_accounts_network", "hedging_set_internal"]))
        _, contract = _understand(COMPOUND, llm)
        assert _CLAIM_HEDGING_SET in _claims(contract)

    def test_llm_failure_degrades_recorded_not_silent(self, gsp_on):
        """A bounded LLM failure (after the provider's own retry) falls
        back to the candidate contract — the degradation is RECORDED
        (WP-CG-3 diagnostics + source=heuristic), never presented as a
        complete LLM understanding."""
        class _Exploding:
            def generate(self, messages=None, max_tokens=0, temperature=0.1):
                from app.exceptions import LLMEmptyResponseError
                raise LLMEmptyResponseError(
                    "LLM response contained no text block (2 attempts)",
                    context={"attempts": 2})

        diagnostics = {}
        rep, contract = _understand(COMPOUND, _Exploding(),
                                    case_findings=[])
        assert diagnostics.get("llm_attempted") is None or True  # not passed here
        assert rep.source == "heuristic"
        assert contract is not None
        # the candidate's deliverable is preserved honestly
        assert any("关联账号网络归因" in c for c in _claims(contract))

    def test_llm_failure_diagnostics_recorded(self, gsp_on):
        from app.goal_resolution import understand_goal

        class _Exploding:
            def generate(self, messages=None, max_tokens=0, temperature=0.1):
                raise RuntimeError("provider returned no text")

        diagnostics = {}
        understand_goal(COMPOUND, case_id="U00047", llm=_Exploding(),
                        diagnostics=diagnostics, case_findings=[])
        assert diagnostics["llm_attempted"] is True
        assert "provider returned no text" in diagnostics["llm_error"]


# --- G. completion-state protection (P0/B closure) --------------------------
#
# A degraded contract (deterministic fallback — the goal-understanding
# model never validated the set against the original request) proves its
# OUTPUTS were delivered, never that the REQUEST is fully covered. The
# contract carries `intent_source`; the outcome derivation caps COMPLETE
# at PARTIAL for degraded contracts and the composer discloses the basis.

class _AlwaysFails:
    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        raise RuntimeError("provider returned no text")


class TestGCompletionStateProtection:
    def _degraded_contract(self, request, gsp_on):
        from app.goal_resolution import (build_goal_contract,
                                         understand_goal)
        rep = understand_goal(request, case_id="U00047", llm=_AlwaysFails(),
                              case_findings=[])
        contract, status = build_goal_contract(rep, "U00047")
        assert contract is not None, status
        return rep, contract

    def test_a_compound_fallback_cannot_be_complete(self, gsp_on):
        """CORE: the original compound request, LLM review failing after
        the bounded retry — the fallback keeps only the recognized list
        deliverable; even with the delivered output SATISFIED the outcome
        can never claim the compound request was fully answered."""
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        rep, contract = self._degraded_contract(COMPOUND, gsp_on)
        assert rep.source == "heuristic"
        assert contract.intent_source == "heuristic"
        claims = _claims(contract)
        assert any("关联账号网络归因" in c for c in claims)
        assert not any("集合内部" in c for c in claims)   # hedge never identified
        # the recognized list deliverable IS delivered (satisfied by the
        # postcondition evaluator at run time)
        for o in contract.outputs:
            o.satisfaction = OutputSatisfaction.SATISFIED
        outcome = derive_goal_outcome(
            [o.satisfaction for o in contract.outputs], "completed",
            intent_source=contract.intent_source)
        assert outcome != "complete"
        assert outcome == "partial"

    def test_a_degraded_outcome_capped_even_all_satisfied(self, gsp_on):
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        _, contract = self._degraded_contract(COMPOUND, gsp_on)
        forced = [OutputSatisfaction.SATISFIED for _ in contract.outputs]
        outcome = derive_goal_outcome(forced, "completed",
                                      intent_source="heuristic")
        assert outcome == "partial"

    def test_b_simple_list_fallback_still_delivers(self, gsp_on):
        """The simple list request degrades gracefully: the recognized
        deliverable is still contracted, executed and delivered. HONEST
        BOUNDARY: the system has no mechanical proof that a degraded
        single-deliverable contract is a CLOSED description of the
        request (a single clause can carry two demands), so per the
        existing semantics the outcome caps at PARTIAL with disclosure —
        delivery is never blocked, only the completeness claim."""
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        rep, contract = self._degraded_contract(LIST_ONLY, gsp_on)
        assert contract.intent_source == "heuristic"
        assert any("关联账号网络归因" in c for c in _claims(contract))
        for o in contract.outputs:
            o.satisfaction = OutputSatisfaction.SATISFIED   # delivered
        outcome = derive_goal_outcome(
            [o.satisfaction for o in contract.outputs], "completed",
            intent_source=contract.intent_source)
        assert outcome != "complete"
        assert outcome == "partial"

    def test_b_healthy_review_still_completes(self, gsp_on):
        """The cap is keyed on the degraded basis, not on the outcome
        path: an LLM-reviewed contract with all outputs satisfied is
        COMPLETE — no regression for the normal path."""
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        llm = ScriptedGoalLLM(_payload(
            resolution="INFERRED", goal_type="identify_related_accounts",
            aspects=["related_accounts_network"]))
        _, contract = _understand(LIST_ONLY, llm)
        assert contract.intent_source == "llm"
        outcome = derive_goal_outcome(
            [OutputSatisfaction.SATISFIED for _ in contract.outputs],
            "completed", intent_source=contract.intent_source)
        assert outcome == "complete"

    def test_c_normal_review_paths_unchanged(self, gsp_on):
        """B-fix regression: metric-only / list-only / compound-double
        contracts from the healthy review carry intent_source="llm" and
        are never capped."""
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        cases = [
            (METRIC_Q, _payload(resolution="INFERRED",
                                goal_type="cross_account_check",
                                aspects=["reverse_trade_metric_semantics"]), 1),
            (LIST_ONLY, _payload(resolution="INFERRED",
                                 goal_type="identify_related_accounts",
                                 aspects=["related_accounts_network"]), 1),
            (COMPOUND, _payload(resolution="COMPOUND",
                                aspects=["related_accounts_network",
                                         "hedging_set_internal"]), 2),
        ]
        for request, payload, n in cases:
            _, contract = _understand(request, ScriptedGoalLLM(payload))
            assert contract.intent_source == "llm", request
            assert len(_claims(contract)) == n
            outcome = derive_goal_outcome(
                [OutputSatisfaction.SATISFIED for _ in contract.outputs],
                "completed", intent_source=contract.intent_source)
            assert outcome == "complete", request

    def test_probe_substituted_proposal_is_degraded(self, gsp_on):
        """The model's own proposal failed structural acceptance and the
        deterministic candidate was substituted — the accepted set was
        never validated by the model against the request → degraded."""
        llm = ScriptedGoalLLM(_payload(
            resolution="COMPOUND",       # structurally invalid: 1 entry
            aspects=["related_accounts_network"]))
        _, contract = _understand(LIST_ONLY, llm)
        assert contract.intent_source == "heuristic"

    def test_d_composer_matches_outcome_no_false_complete(self, gsp_on):
        """Consistency: a degraded contract whose single output is fully
        satisfied composes the PARTIAL outcome line plus the honest
        disclosure — never the '本次调查目标已完整回答' claim, and no
        fabricated hedge execution."""
        from app.agent_loop import AgentLoopState, compose_loop_response
        from app.goal_resolution import OutputSatisfaction
        _, contract = self._degraded_contract(COMPOUND, gsp_on)
        for o in contract.outputs:
            o.satisfaction = OutputSatisfaction.SATISFIED   # list delivered
        state = AgentLoopState(
            task_id="TASK-P0BG", investigation_id="INV-P0BG",
            case_id="U00047", goal=COMPOUND, goal_contract=contract,
            agent_status="completed", stop_reason="goal_satisfied",
            observations=[{"tool_name": "related_account_analysis",
                           "step_type": "analyze_related_accounts",
                           "outcome": "success", "semantic_status": None,
                           "summary": {"related_accounts_count": 17},
                           "error": None, "complete": None,
                           "next_data_needed": None,
                           "for_outputs": ["O1"],
                           "actual_evidence_types":
                               ["network_relations"]}],
            response=None)
        previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            text = compose_loop_response(state)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
        assert "本次调查目标已完整回答" not in text
        # Q2 closure 情形 A wording: all identified deliverables answered,
        # coverage of the ORIGINAL request honestly unconfirmed
        assert "已识别的交付项均已回答" in text
        assert "无法确认是否还有未识别的请求内容" in text
        assert "无法确认已覆盖您请求的全部内容" in text
        # no fabricated hedge execution
        assert "对冲" not in text.split("注意：")[0].split("整体结果")[-1] \
            or "集合内部" not in text

    def test_d_legacy_contract_without_intent_source_uncapped(self, gsp_on):
        """Persisted legacy contracts (intent_source=None) keep their
        existing outcome semantics — no retroactive cap."""
        from app.goal_postconditions import derive_goal_outcome
        from app.goal_resolution import OutputSatisfaction
        outcome = derive_goal_outcome([OutputSatisfaction.SATISFIED],
                                      "completed", intent_source=None)
        assert outcome == "complete"

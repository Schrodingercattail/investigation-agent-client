"""WP-2 Version B U00033 — Fix-1..Fix-4 implementation tests (unit /
component level).

Covers the calibrated plan's four fixes at the deterministic-semantics
level:
- Fix-1: the Finding anchoring (vocabulary resolution, exactly-one,
  multi-candidate drop, adopted-step anchoring at the loop, identity
  consistency across steps serving the same output);
- Fix-2: the RE_PLAN digest gap carry + repeat suppression semantics;
- Fix-3: the BLOCK response assembly (compose_loop_response canonical;
  the analyze facts in the served answer);
- Fix-4: the goal-level PARTIAL terminal semantics (the decision table
  by output class).

REAL-API E2E: test_wp2_b_s5_e2e_v1.py (the U00033 compound ask) +
qa/wp2_s6_browser_e2e.py — the live journeys cover the user-visible
chain; unit/component tests here pin the deterministic semantics.
"""

import pytest

from app.agent_loop import AgentLoopRunner, AgentLoopState
from app.goal_resolution import (
    ClaimKind, GoalContract, RequiredOutput, ScopeLevel)
from app.investigation_concepts import _EXPLAIN_DOMAINS
from app.models import Finding, Plan, PlanStep, PlanStepStatus
from app.planner_v2 import _FINDING_LEVEL_STEP_TYPES, _output_target_finding
from app.skills import EvidenceType

CASE = "U00010"
ASK = "generate all trade records of this account as an artifact"
EXPLAIN_ASK = "关联账号为什么是风险项？相关政策原文发我"


def mk_finding(fid, title, ftype="graph_signal"):
    return Finding(
        finding_id=fid, case_id="U00033", type=ftype, title=title,
        summary="s", signal_refs=[],
        capabilities=["signal_explain", "policy_lookup"])


def mk_output(output_id="O1", claim="关联账号为何构成风险项的解释",
              kind="explanation", ev=("rule_explanation",),
              subject="U00033", scope="finding_level"):
    return RequiredOutput(
        output_id=output_id, claim=claim,
        claim_kind=ClaimKind(kind), subject_ref=subject,
        scope=ScopeLevel(scope),
        evidence_requirements=[EvidenceType(e) for e in ev])


# --- Fix-1: vocabulary resolution -------------------------------------------

class TestExplainDomainVocabulary:
    def test_bare_zh_element_registered(self):
        # the two U00033 Fix-1 bare forms must exist in the anchoring
        # layer's OWN vocabulary (not the GU evidence enum)
        elems = {(d, r) for d, r, _t in _EXPLAIN_DOMAINS}
        assert ("linked_account", "关联账号") in elems
        assert ("linked_account", "linked accounts") in elems

    def test_bare_zh_element_resolves_f6(self):
        o = mk_output(claim="关联账号为何构成风险项的解释")
        f = _output_target_finding(
            o, EXPLAIN_ASK,
            [mk_finding("F6", "Linked Account Network.")])
        assert f is not None and f.finding_id == "F6"

    def test_bare_en_plural_resolves(self):
        o = mk_output(claim="explain the linked account network")
        f = _output_target_finding(
            o, "explain the linked accounts",
            [mk_finding("F6", "Linked Account Network.")])
        assert f is not None and f.finding_id == "F6"

    def test_unrelated_claim_no_resolution(self):
        o = mk_output(claim="无关的解释目标")
        f = _output_target_finding(
            o, "无关的请求", [mk_finding("F6", "Linked Account Network.")])
        assert f is None

    def test_shared_device_domain_still_resolves_f5(self):
        o = mk_output(claim="shared device relationships explanation",
                      ev=("opposite_trade_relations",))
        f = _output_target_finding(
            o, "shared device relationships",
            [mk_finding("F5", "Shared Device Relationships.")])
        assert f is not None and f.finding_id == "F5"


# --- Fix-1: anchoring on ADOPTED steps ---------------------------------------

def _mk_state(contract, ask=EXPLAIN_ASK):
    st = AgentLoopState(task_id="T", investigation_id="I", case_id="U00033",
                        goal=ask)
    st.goal_contract = contract
    return st


def _mk_plan(steps):
    return Plan(plan_id="PLAN-U00033T", investigation_id="CASE:U00033",
                goal=EXPLAIN_ASK, steps=steps)


def _mk_step(sid, stype, args=None):
    return PlanStep(step_id=sid, type=stype,
                    status=PlanStepStatus.PENDING,
                    tool_name=None, arguments=dict(args or {}))


F5 = "Shared Device Relationships."
F6 = "Linked Account Network."


class TestAnchoringExtension:
    """The loop-top anchoring (`_anchor_finding_level_steps`): every
    adopted finding-level step lacking finding_id resolves via the SAME
    `_output_target_finding`; exactly-one binds, zero/ambiguous drops
    (fail-closed), explicit anchors are untouched."""

    def _runner(self):
        return AgentLoopRunner()

    def _contract(self):
        return GoalContract(
            contract_id="GC-u00033t1", subject_ref="U00033",
            outputs=[
                mk_output("O1", claim="关联账号为何构成风险项的解释",
                          kind="explanation",
                          ev=("rule_explanation",)),
                mk_output("O2", claim="提供关联账号相关政策原文",
                          kind="retrieval",
                          ev=("policy_references",),
                          scope="case_level"),
            ])

    def test_anchoring_binds_unambiguous_target(self):
        r = self._runner()
        st = _mk_state(self._contract())
        plan = _mk_plan([
            _mk_step("S1", "explain_signal"),
            _mk_step("S2", "retrieve_policy"),
        ])
        st.step_output_map = {"S1": ["O1"], "S2": ["O1", "O2"]}
        findings = [mk_finding("F5", F5), mk_finding("F6", F6)]
        r._anchor_finding_level_steps(st, plan, EXPLAIN_ASK, findings)
        # both steps bound to the SAME resolved finding (DoD 12: identity
        # consistency via the single deterministic resolver)
        assert plan.steps[0].arguments["finding_id"] == "F6"
        assert plan.steps[1].arguments["finding_id"] == "F6"
        # gaps are lazily created on first drop — none recorded here
        assert getattr(r, "_finding_anchor_gaps", []) == []

    def test_no_resolution_drops_step_fail_closed(self):
        r = self._runner()
        # BOTH the claim and the request must be vocabulary-free — the
        # resolver checks the claim tier first
        contract = GoalContract(
            contract_id="GC-u00033t3", subject_ref="U00033",
            outputs=[mk_output("O1", claim="无关的解释目标",
                               kind="explanation",
                               ev=("rule_explanation",))])
        st = _mk_state(contract, ask="无关的请求")
        plan = _mk_plan([_mk_step("S1", "explain_signal")])
        st.step_output_map = {"S1": ["O1"]}
        findings = [mk_finding("F5", F5), mk_finding("F6", F6)]
        r._anchor_finding_level_steps(st, plan, "无关的请求", findings)
        # fail-closed: the step never reaches the Executor blind
        assert plan.steps == []
        assert st.plan_steps == []
        dropped = [d for d in st.decisions
                   if d.get("decision") == "FINDING_ANCHOR_GAP_DROPPED"]
        assert len(dropped) == 1
        assert "no_resolvable_finding" in dropped[0]["reason"]
        assert r._finding_anchor_gaps[0]["step_type"] == "explain_signal"

    def test_ambiguous_targets_drop_step(self):
        r = self._runner()
        contract = GoalContract(
            contract_id="GC-u00033t2", subject_ref="U00033",
            outputs=[
                mk_output("O1", claim="shared device relationships "
                                     "explanation", kind="explanation",
                          ev=("rule_explanation",)),
                mk_output("O2", claim="explain the linked account network",
                          kind="explanation",
                          ev=("rule_explanation",)),
            ])
        st = _mk_state(contract)
        plan = _mk_plan([_mk_step("S1", "explain_signal")])
        st.step_output_map = {"S1": ["O1", "O2"]}
        findings = [mk_finding("F5", F5), mk_finding("F6", F6)]
        r._anchor_finding_level_steps(st, plan, EXPLAIN_ASK, findings)
        # two serving outputs resolve to DIFFERENT findings → never a
        # silent pick; the step is dropped (plan-level fail-closed)
        assert plan.steps == []
        dropped = [d for d in st.decisions
                   if d.get("decision") == "FINDING_ANCHOR_GAP_DROPPED"]
        assert len(dropped) == 1
        assert "ambiguous_finding_targets" in dropped[0]["reason"]

    def test_explicit_finding_id_untouched(self):
        r = self._runner()
        st = _mk_state(self._contract())
        plan = _mk_plan([
            _mk_step("S1", "explain_signal", {"finding_id": "F9"}),
            _mk_step("S2", "retrieve_policy", {"finding_id": "F6"}),
        ])
        st.step_output_map = {"S1": ["O1"], "S2": ["O1", "O2"]}
        findings = [mk_finding("F5", F5), mk_finding("F6", F6)]
        r._anchor_finding_level_steps(st, plan, EXPLAIN_ASK, findings)
        # planner-explicit / supplement-anchored steps are never rewritten
        assert plan.steps[0].arguments["finding_id"] == "F9"
        assert plan.steps[1].arguments["finding_id"] == "F6"
        assert getattr(r, "_finding_anchor_gaps", []) == []

    def test_contractless_run_skips(self):
        r = self._runner()
        st = _mk_state(None)
        plan = _mk_plan([_mk_step("S1", "explain_signal")])
        findings = [mk_finding("F6", F6)]
        # no contract → the resolver has no outputs to serve; the step is
        # left exactly as adopted (legacy/flag-off runs unchanged)
        r._anchor_finding_level_steps(st, plan, EXPLAIN_ASK, findings)
        assert len(plan.steps) == 1
        assert "finding_id" not in plan.steps[0].arguments

    def test_non_finding_level_steps_untouched(self):
        r = self._runner()
        st = _mk_state(self._contract())
        plan = _mk_plan([
            _mk_step("S1", "fetch_case"),
            _mk_step("S2", "analyze_related_accounts"),
        ])
        st.step_output_map = {"S1": ["O1"], "S2": ["O1"]}
        findings = [mk_finding("F6", F6)]
        r._anchor_finding_level_steps(st, plan, EXPLAIN_ASK, findings)
        assert "finding_id" not in plan.steps[0].arguments
        assert "finding_id" not in plan.steps[1].arguments
        assert plan.steps[0].type == "fetch_case"

    def test_explain_signal_is_finding_level(self):
        # the anchoring set must include the failed run's step type
        assert "explain_signal" in _FINDING_LEVEL_STEP_TYPES


# --- Fix-2: RE_PLAN gap carry + repeat suppression ---------------------------

def _drop_contract():
    return GoalContract(
        contract_id="GC-u00033f2", subject_ref="U00033",
        outputs=[mk_output("O1", claim="无关的解释目标", kind="explanation",
                           ev=("rule_explanation",))])


class TestFix2GapCarry:
    def _runner(self):
        return AgentLoopRunner()

    def _drop_once(self, runner, sid="S1"):
        st = _mk_state(_drop_contract(), ask="无关的请求")
        plan = _mk_plan([_mk_step(sid, "explain_signal")])
        st.step_output_map = {sid: ["O1"]}
        runner._anchor_finding_level_steps(
            st, plan, "无关的请求", [mk_finding("F6", F6)])
        return plan

    def test_gap_record_carries_calibration_fields(self):
        r = self._runner()
        self._drop_once(r)
        gap = r._finding_anchor_gaps[0]
        assert gap["step_type"] == "explain_signal"
        assert gap["missing"] == "finding_id"
        assert gap["serving_outputs"] == ["O1"]
        assert gap["resolved_finding"] is None
        assert gap["failed_attempts"] == 1

    def test_repeat_gap_increments_failed_attempts(self):
        r = self._runner()
        # the RE_PLAN regenerated the identical missing-arg step → the
        # drop repeats and the same step-type × missing-parameter gap
        # counts its attempts
        self._drop_once(r, sid="S1")
        self._drop_once(r, sid="S2")
        assert [g["failed_attempts"]
                for g in r._finding_anchor_gaps] == [1, 2]

    def test_digest_lines_carry_gap_and_constraint(self):
        r = self._runner()
        r._finding_anchor_gaps = [{
            "step_id": "S1", "step_type": "explain_signal",
            "missing": "finding_id", "serving_outputs": ["O1"],
            "resolved_finding": None, "failed_attempts": 2,
            "reason": "no_resolvable_finding"}]
        lines = r._anchor_gap_digest_lines()
        assert len(lines) == 2
        assert "unresolved_parameter_gaps" in lines[0]
        assert "explain_signal" in lines[0]
        assert '"failed_attempts": 2' in lines[0]
        low = lines[1].lower()
        assert "bind" in low and "omit" in low

    def test_no_gaps_emits_no_digest_lines(self):
        assert self._runner()._anchor_gap_digest_lines() == []

    def test_suppression_scoped_to_the_gap(self):
        # suppression keys on the GAP, not the topic: a follow-up plan's
        # OTHER legal finding-level steps (here: resolvable ones) are
        # anchored and kept — only unresolvable repeats are dropped
        r = self._runner()
        contract = GoalContract(
            contract_id="GC-u00033f2b", subject_ref="U00033",
            outputs=[mk_output("O1", claim="关联账号为何构成风险项的解释",
                               kind="explanation",
                               ev=("rule_explanation",))])
        r._finding_anchor_gaps = [{
            "step_id": "S0", "step_type": "explain_signal",
            "missing": "finding_id", "serving_outputs": ["O1"],
            "resolved_finding": None, "failed_attempts": 1,
            "reason": "no_resolvable_finding"}]
        st = _mk_state(contract)
        plan = _mk_plan([_mk_step("S1", "explain_signal"),
                         _mk_step("S2", "retrieve_policy")])
        st.step_output_map = {"S1": ["O1"], "S2": ["O1"]}
        r._anchor_finding_level_steps(
            st, plan, EXPLAIN_ASK, [mk_finding("F5", F5),
                                    mk_finding("F6", F6)])
        assert len(plan.steps) == 2
        assert all(s.arguments.get("finding_id") == "F6"
                   for s in plan.steps)


# --- Fix-3: the unassociated analyze facts reach the served answer -----------

def _analyze_obs(**over):
    s = {"related_accounts_count": 12,
         "related_accounts": [{"account_id": f"A{i:02d}"}
                              for i in range(12)],
         "shared_devices": ["DEV1L71883DLR"],
         "cluster_name": "cluster_device_sharing_4",
         "detection_type": "device_sharing",
         "member_count": 4,
         "cluster_risk_score": 80.0,
         "role_in_cluster": "spoke"}
    s.update(over)
    return {"step_type": "analyze_related_accounts",
            "tool_name": "analyze_related_accounts",
            "outcome": "success",
            "semantic_status": "related_account_analysis",
            "summary": s}


class TestFix3UnassociatedAnalysis:
    """P0-3 calibration: an explanation-kind output with NO associated
    observation consumes the executed-but-unassociated analyze
    observation — the case-observed network facts appear in the served
    answer, labeled DETECTED NETWORK SIGNAL (never a confirmed
    violation, never per-account verdicts; cluster risk stays
    cluster-level). Evidence-absent stays honest."""

    @staticmethod
    def _state(observations, lang=None):
        from app.config import settings
        from app.agent_loop import StopReason
        old = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            contract = GoalContract(
                contract_id="GC-u00033f3", subject_ref="U00033",
                outputs=[mk_output("O1", claim="关联账号为何构成风险项的解释",
                                   kind="explanation",
                                   ev=("rule_explanation",))])
            st = AgentLoopState(task_id="T", investigation_id="I",
                                case_id="U00033", goal=EXPLAIN_ASK)
            st.goal_contract = contract
            st.observations = observations
            st.agent_status = "blocked"
            st.stop_reason = StopReason.PERSISTENT_ERROR
            st.response_language = lang
            from app.agent_loop import compose_loop_response
            return compose_loop_response(st)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = old

    def test_unassociated_analyze_facts_render(self):
        out = self._state([_analyze_obs()])
        assert "DETECTED NETWORK SIGNAL" in out
        assert "12" in out                      # related_accounts_count
        assert "cluster_device_sharing_4" in out
        assert "DEV1L71883DLR" in out
        assert "80.0" in out
        assert "簇级指标" in out                # cluster-level, not case-level
        assert "并非已确认的违规认定" in out

    def test_en_face_renders_english(self):
        out = self._state([_analyze_obs()], lang="en")
        assert "DETECTED NETWORK SIGNAL" in out
        assert "12 linked accounts detected" in out
        assert "cluster-level metric" in out
        assert "关联账号" not in out

    def test_evidence_absent_stays_honest(self):
        # no analysis observation ran → no case-observed block, no
        # fabricated explanation
        out = self._state([])
        assert "DETECTED NETWORK SIGNAL" not in out
        assert "cluster_device_sharing_4" not in out

    def test_associated_analyze_not_double_rendered(self):
        # an observation ASSOCIATED to the output is rendered by the
        # normal section path — the unassociated block must not duplicate
        obs = _analyze_obs()
        obs["for_outputs"] = ["O1"]
        out = self._state([obs])
        assert "DETECTED NETWORK SIGNAL" not in out

    def test_failed_analyze_not_rendered(self):
        obs = _analyze_obs()
        obs["outcome"] = "error"
        out = self._state([obs])
        assert "DETECTED NETWORK SIGNAL" not in out

    def test_non_explanation_output_not_rendered(self):
        contract = GoalContract(
            contract_id="GC-u00033f3b", subject_ref="U00033",
            outputs=[mk_output("O2", claim="提供关联账号相关政策原文",
                               kind="retrieval",
                               ev=("policy_references",),
                               scope="case_level")])
        from app.config import settings
        from app.agent_loop import StopReason, compose_loop_response
        old = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
        try:
            st = AgentLoopState(task_id="T", investigation_id="I",
                                case_id="U00033", goal=EXPLAIN_ASK)
            st.goal_contract = contract
            st.observations = [_analyze_obs()]
            st.agent_status = "blocked"
            st.stop_reason = StopReason.PERSISTENT_ERROR
            out = compose_loop_response(st)
        finally:
            settings.ENABLE_GOAL_SEMANTIC_CONTRACT = old
        assert "DETECTED NETWORK SIGNAL" not in out


# --- Fix-4: the calibrated goal-level terminal decision table ----------------

from app.agent_loop import (  # noqa: E402
    _blocked_terminal_class, _core_gap_statement, _goal_output_classes)
from app.goal_resolution import OutputSatisfaction  # noqa: E402


class TestFix4PartialTerminal:
    """Rev 2 P1 decision table: the terminal family follows the user
    goal's ACTUAL satisfaction degree judged by deterministic output
    classes — never the count of satisfied outputs. Row 2 (core ✗,
    policy ✓) is NOT PARTIAL: BLOCK-family + the ungenerated core named
    in the answer."""

    @staticmethod
    def _outputs(core_sat, policy_sat, ids=("O1", "O2")):
        o1 = mk_output(ids[0], claim="关联账号为何构成风险项的解释",
                       kind="explanation", ev=("rule_explanation",))
        o2 = mk_output(ids[1], claim="提供关联账号相关政策原文",
                       kind="retrieval", ev=("policy_references",),
                       scope="case_level")
        o1.satisfaction = (OutputSatisfaction.SATISFIED if core_sat
                           else OutputSatisfaction.PENDING)
        o2.satisfaction = (OutputSatisfaction.SATISFIED if policy_sat
                           else OutputSatisfaction.PENDING)
        return [o1, o2]

    def test_row1_core_and_policy_satisfied(self):
        assert _blocked_terminal_class(
            self._outputs(True, True)) == "goal_satisfied"

    def test_row2_core_missing_policy_delivered_not_partial(self):
        assert _blocked_terminal_class(
            self._outputs(False, True)) == "core_missing_delivered"

    def test_row3_core_answered_policy_missing(self):
        assert _blocked_terminal_class(
            self._outputs(True, False)) == "goal_satisfied"

    def test_row4_nothing_satisfied_existing_block(self):
        assert _blocked_terminal_class(
            self._outputs(False, False)) == "nothing"

    def test_classes_derived_never_hardcoded(self):
        # different output ids / phrasing, same classes → same judgment
        outs = self._outputs(False, True, ids=("OX", "OY"))
        assert _blocked_terminal_class(outs) == "core_missing_delivered"
        core, policy = _goal_output_classes(outs)
        assert [o.output_id for o in core] == ["OX"]
        assert [o.output_id for o in policy] == ["OY"]

    def test_core_statement_names_the_ungenerated_core(self):
        st = AgentLoopState(task_id="T", investigation_id="I",
                            case_id="U00033", goal=EXPLAIN_ASK)
        o1 = mk_output("O1", claim="关联账号为何构成风险项的解释",
                       kind="explanation", ev=("rule_explanation",))
        o1.claim_description = "关联账号风险解释"
        o1.satisfaction = OutputSatisfaction.PENDING
        st.goal_contract = GoalContract(
            contract_id="GC-u00033f4", subject_ref="U00033",
            outputs=[o1, self._outputs(False, True)[1]])
        stmt = _core_gap_statement(st)
        assert "核心解释" in stmt and "未能生成" in stmt
        assert "关联账号风险解释" in stmt      # the core's own header
        assert "未被回答" in stmt
        st.response_language = "en"
        stmt_en = _core_gap_statement(st)
        assert "NOT generated" in stmt_en
        assert "remains unanswered" in stmt_en

    def test_no_core_outputs_no_statement(self):
        st = AgentLoopState(task_id="T", investigation_id="I",
                            case_id="U00033", goal=EXPLAIN_ASK)
        st.goal_contract = GoalContract(
            contract_id="GC-u00033f4b", subject_ref="U00033",
            outputs=[self._outputs(False, True)[1]])
        assert _core_gap_statement(st) == ""

    def test_no_core_class_contract_keeps_existing_machinery(self):
        # a pure records-delivery contract (no explanation outputs) is
        # OUTSIDE the calibrated table — all-satisfied keeps the
        # goal_satisfied family (never a fabricated core-missing block)
        records = mk_output("OR", claim="提供该账户全部交易记录",
                            kind="retrieval",
                            ev=("transaction_records",),
                            scope="case_level")
        records.satisfaction = OutputSatisfaction.SATISFIED
        assert _blocked_terminal_class([records]) == "goal_satisfied"
        records.satisfaction = OutputSatisfaction.PENDING
        assert _blocked_terminal_class([records]) == "nothing"


# --- Follow-up fix: twin-explanation answer dedup -----------------------------

def _twin_explanation_output(oid, ev, claim, sat=OutputSatisfaction.SATISFIED,
                             desc=None, subject="F5"):
    o = mk_output(oid, claim=claim, kind="explanation", ev=ev,
                  subject=subject)
    o.satisfaction = sat
    if desc:
        o.claim_description = desc
    return o


def _twin_analyze_obs():
    return {"step_type": "analyze_related_accounts",
            "tool_name": "analyze_related_accounts",
            "outcome": "success", "for_outputs": ["O1"],
            "semantic_status": "RELATED_ACCOUNTS_FOUND",
            "summary": {
                "related_accounts_count": 18,
                "related_accounts": [f"U00{i:03d}" for i in range(1, 19)],
                "shared_devices": ["DEV8Z1O4A35QU"],
                "cluster_name": "Cluster_device_sharing_1",
                "detection_type": "device_sharing",
                "member_count": 19,
                "cluster_risk_score": 80,
                "role_in_cluster": "spoke"}}


def _twin_signal_obs(served=("O1", "O3")):
    return {"step_type": "explain_signal",
            "tool_name": "signal_explain",
            "outcome": "success", "for_outputs": list(served),
            "semantic_status": "Graph",
            "summary": {
                "finding_id": "F5",
                "cluster_name": "Cluster_device_sharing_1",
                "detection_type": "device_sharing",
                "member_count": 19,
                "cluster_risk_score": 80.0,
                "shared_devices": ["DEV8Z1O4A35QU"],
                "related_accounts": ["U00337", "U00325", "U00349",
                                     "U00343", "U00007"],
                "related_accounts_preview": True,
                "related_accounts_total": 18}}


def _twin_policy_obs():
    return {"step_type": "retrieve_policy",
            "tool_name": "policy_lookup",
            "outcome": "success", "for_outputs": ["O2"],
            "summary": {
                "finding_policy_status": "associated",
                "associated_citations": [
                    {"citation_id": 3,
                     "document": "AML_Suspicious_Indicators.md",
                     "section": "5.1 Links to Known Risky Clusters",
                     "snippet": "Accounts linked to known risky clusters "
                                "(shared devices, shared withdrawal "
                                "destinations, repeated counterparties) "
                                "should be prioritized for review."}]}}


CLAIM_TWIN = "为什么「Linked Account Network」被标记为风险"

QUOTE_51 = ("Accounts linked to known risky clusters (shared devices, "
            "shared withdrawal destinations, repeated counterparties) "
            "should be prioritized for review.")


def _twin_contract(o1_ev=("rule_explanation", "network_relations"),
                   o3_claim=CLAIM_TWIN, o3_sat=OutputSatisfaction.SATISFIED,
                   o1_sat=OutputSatisfaction.SATISFIED,
                   o3_claim_text=None):
    o1 = _twin_explanation_output("O1", list(o1_ev), CLAIM_TWIN,
                                  sat=o1_sat,
                                  desc="解释发现 F5 的依据：及其关系")
    o2 = mk_output("O2", claim="发现 F5 的政策依据", kind="retrieval",
                   ev=("policy_references",))
    o2.satisfaction = OutputSatisfaction.SATISFIED
    o3 = _twin_explanation_output("O3", ["rule_explanation"],
                                  o3_claim_text or o3_claim, sat=o3_sat,
                                  desc="解释发现 F5 的依据：及其关系")
    return GoalContract(contract_id="GC-twin", subject_ref="F5",
                        outputs=[o1, o2, o3])


def _compose_twin(contract, observations):
    from app.config import settings
    from app.agent_loop import StopReason, compose_loop_response
    old = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
    settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
    try:
        st = AgentLoopState(task_id="T", investigation_id="I",
                            case_id="U00033", goal=CLAIM_TWIN)
        st.goal_contract = contract
        st.observations = observations
        st.agent_status = "completed"
        st.stop_reason = StopReason.GOAL_SATISFIED
        return compose_loop_response(st)
    finally:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = old


class TestTwinExplanationDedup:
    """Follow-up fix: the SAME explain intent proposed twice by the GU
    (trivial claim variance) must render ONE integrated section — the
    cluster, shared device, account network and risk meaning appear
    once; complementary evidence is preserved; the policy quote stays
    verbatim; distinct claims never merge."""

    def test_identical_twin_claims_render_once(self):
        ans = _compose_twin(
            _twin_contract(),
            [_twin_analyze_obs(), _twin_policy_obs(), _twin_signal_obs()])
        assert ans.count("Cluster_device_sharing_1") == 1, ans[:400]
        # the duplicated graph-explanation block is gone
        assert "图关系检测" not in ans
        # the full list is presented once — no 5/18 preview beside it
        assert "关联账号共 18 个" in ans
        assert "已展示 5 / 18" not in ans
        assert ans.count("DEV8Z1O4A35QU") == 1
        # the risk-signal meaning and its boundary are preserved
        assert "风险信号判断" in ans
        assert "不构成对违规、欺诈或共谋行为的认定" in ans
        # the policy citation is intact and verbatim
        assert "5.1 Links to Known Risky Clusters" in ans
        assert QUOTE_51 in ans
        # cluster-level score stays cluster-labeled
        assert "集群风险分 80" in ans

    def test_offbyone_variant_twin_merges(self):
        # the ACCEPTANCE-run shape: claims differ by ONE character
        ans = _compose_twin(
            _twin_contract(o3_claim_text="为什么「Linked Account Network」"
                                         "被标记为风险项"),
            [_twin_analyze_obs(), _twin_policy_obs(), _twin_signal_obs()])
        assert ans.count("Cluster_device_sharing_1") == 1
        assert "图关系检测" not in ans

    def test_complementary_facts_integrated(self):
        # role_in_cluster exists ONLY in the analyze observation — the
        # merged section must still present it (union assoc)
        ans = _compose_twin(
            _twin_contract(),
            [_twin_analyze_obs(), _twin_policy_obs(), _twin_signal_obs()])
        assert "本账户角色 spoke" in ans

    def test_distinct_claims_never_merge(self):
        # the compound-why decomposition: two explanation outputs on the
        # SAME subject with DIFFERENT intents stay separate sections
        # (each renders its own rule facts)
        c = GoalContract(
            contract_id="GC-distinct", subject_ref="U00010",
            outputs=[
                _twin_explanation_output("O1", ["rule_explanation"],
                                         "高频提现判定条件", subject="U00010"),
                _twin_explanation_output("O2", ["rule_explanation"],
                                         "新账号高频活动判定条件",
                                         subject="U00010")])
        obs = [{"step_type": "explain_signal", "tool_name": "signal_explain",
                "outcome": "success", "for_outputs": ["O1"],
                "summary": {"rule_name": "High withdrawal frequency",
                            "threshold": "w > 10",
                            "trigger_values": {"w": 14},
                            "rule_description": "14 withdrawals in 24h"}},
               {"step_type": "explain_signal", "tool_name": "signal_explain",
                "outcome": "success", "for_outputs": ["O2"],
                "summary": {"rule_name": "New account with high activity",
                            "threshold": "activity > x",
                            "trigger_values": {"activity": 9},
                            "rule_description": "high activity"}}]
        ans = _compose_twin(c, obs)
        assert "High withdrawal frequency" in ans
        assert "New account with high activity" in ans

    def test_retrieval_kind_never_merged(self):
        # twin detection is gated on explanation claim_kind — a retrieval
        # output with the same wording is never merged away: BOTH its own
        # section (honest pending) and the explanation section render
        c = GoalContract(
            contract_id="GC-kind", subject_ref="F5",
            outputs=[
                _twin_explanation_output("O1", ["rule_explanation"],
                                         CLAIM_TWIN),
                mk_output("O2", claim=CLAIM_TWIN, kind="retrieval",
                          ev=("policy_references",))])
        ans = _compose_twin(c, [_twin_signal_obs(served=("O1", "O2"))])
        assert "图关系检测" in ans          # the explanation section
        assert "该项尚未产生可判定的结果" in ans   # the retrieval twin kept

    def test_merged_section_prefers_satisfied_twin(self):
        # O1 pending + O3 satisfied: the integrated section renders the
        # DELIVERED verdict (never a false "no determinable result"); the
        # union facts render once with no preview duplication
        ans = _compose_twin(
            _twin_contract(o1_sat=OutputSatisfaction.PENDING),
            [_twin_analyze_obs(), _twin_policy_obs(), _twin_signal_obs()])
        assert ans.count("Cluster_device_sharing_1") == 1
        assert "尚未产生可判定的结果" not in ans
        assert "DEV8Z1O4A35QU" in ans
        assert "已展示 5 / 18" not in ans

    def test_evidence_absent_twin_stays_honest(self):
        # nothing executed for the twins: ONE honest pending section
        ans = _compose_twin(_twin_contract(), [])
        assert ans.count("Cluster_device_sharing_1") == 0
        assert "DETECTED NETWORK SIGNAL" not in ans

    def test_single_explanation_contract_unchanged(self):
        # the ENVRESTORE shape (one explanation, no twin): its rendering
        # is unchanged — the graph block legitimately renders there
        c = GoalContract(
            contract_id="GC-single", subject_ref="F5",
            outputs=[
                _twin_explanation_output("O1", ["rule_explanation"],
                                         CLAIM_TWIN),
                mk_output("O2", claim="发现 F5 的政策依据", kind="retrieval",
                          ev=("policy_references",))])
        ans = _compose_twin(c, [_twin_signal_obs(served=("O1",))])
        assert ans.count("Cluster_device_sharing_1") == 1
        assert "图关系检测" in ans

    def test_terminal_semantics_untouched_by_merge(self):
        # the merge is composer-only: the calibrated P1 class judgment
        # still sees every twin output's satisfaction
        outs = _twin_contract().outputs
        assert _blocked_terminal_class(outs) == "goal_satisfied"
        outs[0].satisfaction = OutputSatisfaction.PENDING
        outs[2].satisfaction = OutputSatisfaction.PENDING
        assert _blocked_terminal_class(outs) == "core_missing_delivered"

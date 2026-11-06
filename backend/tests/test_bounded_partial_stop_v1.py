"""U00004 bounded partial STOP (PMO-approved) — unit tests.

`LoopPolicy.enforce` rule 2: a STOP proposal is ACCEPTED (flag ON) when
every contract output is final within a DISCLOSED data boundary — no
pending steps, no uncovered actionable outputs, hard file_export
deliverables SATISFIED, PARTIALLY_SUPPORTED backed by structured
truncation evidence on its OWN associated observations. Flag OFF keeps
the legacy coercion byte-for-byte. The outcome stays partial — the
predicate never rewrites any satisfaction."""

import pytest

from app.agent_loop import (
    AgentLoopState,
    DecisionProposal,
    LoopDecision,
    LoopPolicy,
    StopReason,
    _bounded_partial_stop_allowed,
    _has_boundary_disclosure,
    _uncovered_actionable_outputs,
)
from app.config import settings
from app.goal_resolution import (
    ClaimKind, GoalContract, RequiredOutput, ScopeLevel)
from app.goal_resolution import OutputSatisfaction

ASK = ("列出所有关联账号。查询其中是否存在对冲交易情况。如果有，"
       "把对冲交易明细列给我，并且生成一份文件")


def _mk_output(oid, kind, ev, delivery="detail_list",
               sat=OutputSatisfaction.SATISFIED):
    o = RequiredOutput(
        output_id=oid, claim=f"deliverable {oid}",
        claim_kind=ClaimKind(kind), subject_ref="U00058",
        scope=ScopeLevel.CASE_LEVEL,
        evidence_requirements=[__import__("app.skills", fromlist=["x"])
                               .EvidenceType(e) for e in ev])
    o.satisfaction = sat
    o.delivery = delivery
    return o


def _truncated_obs(for_outputs, tool="cross_account_trade_analysis"):
    return {"step_type": "analyze_related_account_set_trades",
            "tool_name": tool, "outcome": "success",
            "for_outputs": list(for_outputs),
            "summary": {"candidates_found": 6,
                        "retrieval": {"truncated": True,
                                      "truncated_reasons": [
                                          "account_set:retrieval_budget_"
                                          "exhausted"]}}}


def _mk_state(outputs, *, pending=(), coverage=None, observations=()):
    st = AgentLoopState(task_id="T", investigation_id="I",
                        case_id="U00058", goal=ASK)
    st.goal_contract = GoalContract(
        contract_id="GC-BP", subject_ref="U00058", outputs=list(outputs))
    st.plan_steps = [
        {"step_id": f"S{i+1}", "type": "analyze_related_account_set_trades",
         "status": ("pending" if p else "success"), "tool_name": None}
        for i, p in enumerate(pending)]
    st.planning_coverage = list(coverage) if coverage is not None else [
        {"output_id": o.output_id, "coverage_status": "covered",
         "eligible_step_types": [], "matched_evidence_types": []}
        for o in outputs]
    st.observations = list(observations)
    return st


def _o1(sat=OutputSatisfaction.PARTIALLY_SUPPORTED):
    return _mk_output("O1", "existence_check", ["opposite_trade_relations"],
                      sat=sat)


def _o2(sat=OutputSatisfaction.SATISFIED):
    return _mk_output("O2", "retrieval",
                      ["opposite_trade_relations", "transaction_records"],
                      delivery="file_export", sat=sat)


DISCLOSURE = [_truncated_obs(["O1"])]


class TestPredicate:
    def test_case_a_allows_stop(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()], observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is True

    def test_case_b_pending_file_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2(OutputSatisfaction.PENDING)],
                       observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is False

    def test_case_c_uncovered_actionable_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()], observations=DISCLOSURE)
        # the actionable omission sits on the UNSATISFIED output (a
        # satisfied output needs no coverage verdict)
        st.planning_coverage[0]["coverage_status"] = "not_covered_by_plan"
        assert _uncovered_actionable_outputs(st) == ["O1"]
        assert _bounded_partial_stop_allowed(st) is False

    def test_case_d_no_disclosure_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()])
        assert _bounded_partial_stop_allowed(st) is False

    def test_case_e_pending_steps_refuse(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()], pending=[True],
                       observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is False

    def test_unresolved_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(OutputSatisfaction.UNRESOLVED), _o2()],
                       observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is False

    def test_unsupported_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(OutputSatisfaction.UNSUPPORTED), _o2()],
                       observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is False

    def test_empty_planning_coverage_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()], coverage=[],
                       observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is False

    def test_missing_output_coverage_record_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()], observations=DISCLOSURE)
        # drop the PARTIAL output's record: an unsatisfied output without
        # a coverage verdict is unknown coverage → conservative refuse
        st.planning_coverage = st.planning_coverage[1:]
        assert _uncovered_actionable_outputs(st) == ["O1"]
        assert _bounded_partial_stop_allowed(st) is False

    def test_file_export_pending_refuses_even_with_disclosure(self,
                                                              monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2(OutputSatisfaction.PENDING)],
                       observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is False

    def test_flag_off_refuses_even_case_a(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", False)
        assert settings.ENABLE_BOUNDED_PARTIAL_STOP is False   # default
        st = _mk_state([_o1(), _o2()], observations=DISCLOSURE)
        assert _bounded_partial_stop_allowed(st) is False

    def test_boundary_disclosure_is_output_scoped(self, monkeypatch):
        # the truncation evidence rides the O1 observation only — an
        # output it does NOT serve has no disclosure of its own
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        other = _mk_output("O9", "existence_check",
                           ["opposite_trade_relations"],
                           sat=OutputSatisfaction.PARTIALLY_SUPPORTED)
        st = _mk_state([_o1(), other, _o2()], observations=DISCLOSURE)
        st.planning_coverage.append(
            {"output_id": "O9", "coverage_status": "covered",
             "eligible_step_types": [], "matched_evidence_types": []})
        assert _has_boundary_disclosure(st, st.goal_contract.outputs[0])
        assert not _has_boundary_disclosure(st, other)


class TestEnforceIntegration:
    """Rule 2 wiring: a STOP proposal over the bounded-partial state is
    accepted (flag ON) and still coerced (flag OFF). Every other path
    (all-satisfied, iteration bound) is untouched."""

    def _proposal(self):
        return DecisionProposal(decision=LoopDecision.STOP.value,
                                reason="analyst: work looks done")

    def test_flag_on_accepts_stop(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()], observations=DISCLOSURE)
        st.iteration = 1
        enforced = LoopPolicy().enforce(self._proposal(), st)
        assert enforced.decision == LoopDecision.STOP
        assert "bounded partial completion" in enforced.reason

    def test_flag_off_keeps_coercion(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", False)
        st = _mk_state([_o1(), _o2()], observations=DISCLOSURE)
        st.iteration = 1
        enforced = LoopPolicy().enforce(self._proposal(), st)
        assert enforced.decision == LoopDecision.RE_PLAN
        assert "stop proposed before" in enforced.reason

    def test_all_satisfied_uses_existing_rule1(self, monkeypatch):
        # Case F: the normal completion path is rule 1, untouched
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(OutputSatisfaction.SATISFIED), _o2()],
                       observations=DISCLOSURE)
        st.iteration = 1
        enforced = LoopPolicy().enforce(self._proposal(), st)
        assert enforced.decision == LoopDecision.STOP
        assert enforced.reason != "" and "bounded partial" not in \
            enforced.reason

    def test_pending_steps_still_coerce_with_flag_on(self, monkeypatch):
        monkeypatch.setattr(settings, "ENABLE_BOUNDED_PARTIAL_STOP", True)
        st = _mk_state([_o1(), _o2()], pending=[True],
                       observations=DISCLOSURE)
        st.iteration = 1
        enforced = LoopPolicy().enforce(self._proposal(), st)
        assert enforced.decision == LoopDecision.CONTINUE

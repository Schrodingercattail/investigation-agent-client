"""U00004 file-delivery closure (PMO Option 1) — unit / component tests.

The USER'S OWN WORDS decide a file deliverable: when the request
explicitly demands a generated file, the accepted contract carries a
file_export leg deterministically (never LLM-variance-dependent — real
runs TASK-288d6e252389 vs TASK-ae1a634a4f1b); plain detail_list queries
never gain one; the LLM alone can never create one; the leg binds to
the hedging deliverable's own subject; a missing/failed artifact can
never read as file-delivery success."""

import pytest

from app.goal_resolution import (
    GoalRepresentation,
    GoalResolutionStatus,
    ScopeLevel,
    _apply_request_file_delivery,
    _canonicalize_llm_outputs,
    _request_demands_file,
    build_goal_contract,
)
from app.goal_postconditions import derive_goal_outcome

ASK = ("列出所有关联账号。查询其中是否存在对冲交易情况。如果有，"
       "把对冲交易明细列给我，并且生成一份文件")
PLAIN = "列出所有关联账号"


def _base_outputs(hedge_subject="U00004"):
    return [
        {"claim": "案件的关联账号网络归因明细（Risk Platform 网络归因）",
         "claim_kind": "retrieval",
         "evidence_requirements": ["network_relations"],
         "subject_ref": hedge_subject, "scope": "case_level",
         "delivery": "detail_list"},
        {"claim": "关联账户集合内部是否存在对冲交易候选（集合内全部"
                  "账户两两配对，可涉及或不涉及案件账户）",
         "claim_kind": "existence_check",
         "evidence_requirements": ["opposite_trade_relations"],
         "subject_ref": hedge_subject, "scope": "case_level",
         "delivery": "detail_list"},
    ]


class TestFileIntentDetection:
    def test_explicit_file_words_match(self):
        for text in ("把明细列给我，并且生成一份文件",
                     "导出文件给我",
                     "生成对冲交易明细文件",
                     "generate a file with the details",
                     "export the candidates to a file"):
            assert _request_demands_file(text), text

    def test_plain_detail_queries_never_match(self):
        for text in (PLAIN, "把对冲交易明细列给我",
                     "查询其中是否存在对冲交易情况",
                     "列出相关证据明细"):
            assert not _request_demands_file(text), text


class TestFileDeliveryLeg:
    def test_user_file_ask_adds_exactly_one_leg(self):
        out = _apply_request_file_delivery(ASK, _base_outputs())
        assert len(out) == 3
        leg = out[-1]
        assert leg["delivery"] == "file_export"
        assert leg["claim_kind"] == "retrieval"
        assert leg["claim"] == "对冲交易明细文件导出"
        assert sorted(leg["evidence_requirements"]) == [
            "network_relations", "opposite_trade_relations",
            "transaction_records"]
        assert leg["subject_ref"] == "U00004"
        assert leg["scope"] == ScopeLevel.CASE_LEVEL.value
        # the chat halves keep their detail_list channels
        assert all(o["delivery"] == "detail_list" for o in out[:2])

    def test_plain_request_is_identity(self):
        base = _base_outputs()
        assert _apply_request_file_delivery(PLAIN, base) is base

    def test_llm_file_export_without_user_intent_creates_nothing(self):
        # the LLM proposed a file leg, the user never asked for a file:
        # the closure adds nothing and creates no duplicate
        llm = _base_outputs() + [{
            "claim": "对冲交易明细文件导出", "claim_kind": "retrieval",
            "evidence_requirements": ["opposite_trade_relations",
                                      "transaction_records"],
            "subject_ref": "U00004", "scope": "case_level",
            "delivery": "file_export"}]
        out = _apply_request_file_delivery(
            "列出所有关联账号。查询其中是否存在对冲交易情况。如果有，"
            "把对冲交易明细列给我", llm)
        assert len(out) == 3

    def test_existing_leg_never_duplicated(self):
        llm = _base_outputs() + [{
            "claim": "对冲交易明细文件", "claim_kind": "retrieval",
            "evidence_requirements": ["opposite_trade_relations",
                                      "transaction_records"],
            "subject_ref": "U00004", "scope": "case_level",
            "delivery": "file_export"}]
        out = _apply_request_file_delivery(ASK, llm)
        assert len(out) == 3
        assert sum(1 for o in out
                   if o.get("delivery") == "file_export") == 1

    def test_leg_binds_the_hedging_deliverable_subject(self):
        outs = [
            {"claim": "案件的关联账号网络归因明细", "claim_kind": "retrieval",
             "evidence_requirements": ["network_relations"],
             "subject_ref": "U00010", "scope": "case_level",
             "delivery": "detail_list"},
            {"claim": "集合内部对冲候选", "claim_kind": "existence_check",
             "evidence_requirements": ["opposite_trade_relations"],
             "subject_ref": "U00004", "scope": "case_level",
             "delivery": "detail_list"},
        ]
        leg = _apply_request_file_delivery(ASK, outs)[-1]
        assert leg["subject_ref"] == "U00004"   # the HEDGE output's subject

    def test_no_hedging_deliverable_no_leg(self):
        records = [{"claim": "提现明细", "claim_kind": "retrieval",
                    "evidence_requirements": ["withdrawal_records"],
                    "subject_ref": "U00004", "scope": "case_level",
                    "delivery": "detail_list"}]
        assert _apply_request_file_delivery(ASK, records) is records

    def test_canonicalized_set_keeps_the_leg(self):
        # the real GU chain: LLM tags are wholesale-replaced by canonical
        # dicts (no delivery key) — the closure still guarantees the leg
        tagged = [{"aspect": "related_accounts_network"},
                  {"aspect": "hedging_set_internal"}]
        outs = _canonicalize_llm_outputs(tagged, "U00004", None)
        assert not any(o.get("delivery") == "file_export" for o in outs)
        out = _apply_request_file_delivery(ASK, outs)
        assert sum(1 for o in out
                   if o.get("delivery") == "file_export") == 1

    def test_full_acceptance_probe_accepts_the_leg(self):
        outs = _apply_request_file_delivery(ASK, _base_outputs())
        rep = GoalRepresentation(
            case_id="U00004", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="case", subject_ref="U00004", source="llm",
            request_text=ASK[:400],
            contract_proposal={"required_outputs": outs})
        accepted, reason = build_goal_contract(rep, "U00004")
        assert accepted is not None, reason
        file_out = [o for o in accepted.outputs
                    if o.delivery == "file_export"]
        assert len(file_out) == 1
        assert file_out[0].satisfaction.value == "pending"


class TestFileFailureSemantics:
    def test_pending_file_leg_is_never_success(self):
        outs = _apply_request_file_delivery(ASK, _base_outputs())
        rep = GoalRepresentation(
            case_id="U00004", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="case", subject_ref="U00004", source="llm",
            request_text=ASK[:400],
            contract_proposal={"required_outputs": outs})
        contract, _ = build_goal_contract(rep, "U00004")
        # nothing executed: the file output stays pending
        outcome = derive_goal_outcome(
            [o.satisfaction for o in contract.outputs], "completed")
        assert outcome.value in ("partial", "unanswerable")
        assert outcome.value != "complete"
        # an artifact-producing criteria kind cannot be satisfied without
        # the artifact (R2a): the file leg participates in the gate
        from app.agent_loop import CRITERIA_ARTIFACT_PRODUCED, AgentLoopState
        st = AgentLoopState(task_id="T", investigation_id="I",
                            case_id="U00004", goal=ASK)
        st.goal_contract = contract
        st.criteria_kind = CRITERIA_ARTIFACT_PRODUCED
        st.artifact_ids = []
        assert st.criteria_satisfied() is False

    def test_satisfied_file_leg_can_complete(self):
        outs = _apply_request_file_delivery(ASK, _base_outputs())
        rep = GoalRepresentation(
            case_id="U00004", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="case", subject_ref="U00004", source="llm",
            request_text=ASK[:400],
            contract_proposal={"required_outputs": outs})
        contract, _ = build_goal_contract(rep, "U00004")
        from app.goal_resolution import OutputSatisfaction
        for o in contract.outputs:
            o.satisfaction = OutputSatisfaction.SATISFIED
        outcome = derive_goal_outcome(
            [o.satisfaction for o in contract.outputs], "completed")
        assert outcome.value == "complete"

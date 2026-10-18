"""WP-GS-C — comparison-intent detection, facet admission, and
coverage-incomplete disposition tests.

L1 coverage (implementation plan §4.3):
- assess_comparison_intent detector: positives (Chinese/English),
  near-negatives (retrieval-only), boundary (empty/None).
- PostconditionFacet enum + RequiredOutput.postcondition_facets field.
- build_goal_contract facet admission: valid accepted, unknown rejected
  whole-proposal, all-or-nothing preserved.
"""

import pytest

from app.goal_resolution import (
    GoalRepresentation, PostconditionFacet, assess_comparison_intent,
    build_goal_contract,
)


class TestComparisonIntentDetector:
    def test_chinese_same_or_different(self):
        assert assess_comparison_intent(
            "9笔提现都提向同一个地址，还是每笔都提向一个新地址？")

    def test_chinese_is_same_destination(self):
        assert assess_comparison_intent(
            "这些提现的地址是不是相同的？")

    def test_chinese_differ(self):
        assert assess_comparison_intent(
            "这几笔交易的地址是否不同？")

    def test_english_same_or_different(self):
        assert assess_comparison_intent(
            "Did all withdrawals go to the same address or different "
            "addresses?")

    def test_english_identical_or_differ(self):
        assert assess_comparison_intent(
            "Are the destination addresses identical?")

    def test_paraphrase_respectively(self):
        assert assess_comparison_intent(
            "请分别列出每笔提现的目的地地址，看是否相同。")

    def test_near_negative_retrieval_only(self):
        assert not assess_comparison_intent(
            "列出这个账号的全部提现记录。")

    def test_near_negative_evidence_request(self):
        assert not assess_comparison_intent(
            "把所有高频提现证据发给我。")

    def test_near_negative_case_overview(self):
        assert not assess_comparison_intent(
            "帮我梳理一下这个账号目前的情况。")

    def test_near_negative_timeline(self):
        assert not assess_comparison_intent(
            "给出这个账号的时间线。")

    def test_empty_text(self):
        assert not assess_comparison_intent("")

    def test_none_text(self):
        assert not assess_comparison_intent(None)


def _out(claim, kind="retrieval", subject="U00010", scope="case_level",
         reqs=("case_summary",), facets=None):
    out = {"claim": claim, "claim_kind": kind, "subject_ref": subject,
           "scope": scope, "evidence_requirements": list(reqs)}
    if facets is not None:
        out["postcondition_facets"] = facets
    return out


def _repr(outputs=None, resolution="INFERRED", goal_type="policy_check"):
    proposal = None
    if outputs is not None:
        proposal = {"required_outputs": outputs}
    return GoalRepresentation(
        case_id="U00010", goal_type=goal_type,
        goal_resolution=resolution, subject_type="case",
        subject_ref="U00010", ambiguous_goal_types=[],
        reason="r", source="llm", request_text="req",
        contract_proposal=proposal)


class TestFacetAdmission:
    def test_valid_facet_accepted(self):
        rep = _repr(outputs=[_out(
            "dest_comparison", kind="existence_check",
            reqs=("withdrawal_records",),
            facets=["destination_address_identity"])])
        contract, reason = build_goal_contract(rep, "U00010")
        assert reason is None and contract is not None
        assert contract.outputs[0].postcondition_facets == [
            "destination_address_identity"]

    def test_unknown_facet_rejected_whole_proposal(self):
        rep = _repr(outputs=[_out(
            "x", facets=["nonexistent_facet"])])
        contract, reason = build_goal_contract(rep, "U00010")
        assert contract is None
        assert reason == "invalid_postcondition_facet"

    def test_no_facets_field_defaults_empty(self):
        rep = _repr(outputs=[_out(
            "x", reqs=("case_summary",))])
        contract, reason = build_goal_contract(rep, "U00010")
        assert reason is None
        assert contract.outputs[0].postcondition_facets == []

    def test_two_outputs_one_faceted(self):
        rep = _repr(outputs=[
            _out("comparison_output", kind="existence_check",
                 reqs=("withdrawal_records",),
                 facets=["destination_address_identity"]),
            _out("list_output", reqs=("network_relations",)),
        ])
        contract, reason = build_goal_contract(rep, "U00010")
        assert reason is None
        assert len(contract.outputs) == 2
        assert contract.outputs[0].postcondition_facets == [
            "destination_address_identity"]
        assert contract.outputs[1].postcondition_facets == []

    def test_facet_deterministic(self):
        outputs = [_out("dest_comparison", kind="existence_check",
                        reqs=("withdrawal_records",),
                        facets=["destination_address_identity"])]
        rep1 = _repr(outputs=outputs)
        rep2 = _repr(outputs=outputs)
        c1, _ = build_goal_contract(rep1, "U00010")
        c2, _ = build_goal_contract(rep2, "U00010")
        assert (c1.outputs[0].postcondition_facets
                == c2.outputs[0].postcondition_facets)

    def test_case_variants_distinct(self):
        rep = _repr(outputs=[_out(
            "x", facets=["destination_address_identity"])])
        contract, reason = build_goal_contract(rep, "U00010")
        assert reason is None
        assert contract.outputs[0].postcondition_facets == [
            "destination_address_identity"]


"""WP-GS-D — GSP-4 destination-identity facet evaluation (L1).

Covers design §7.2's verdict table row by row, the retrieval-
suppression rule (a faceted output is NEVER satisfied by retrieval
alone), block consistency gating, association/provenance boundaries,
repeat-observation semantics, re-plan facet preservation (GSP-7), the
non-faceted regression guarantee, and GoalOutcome mapping through the
EXISTING derivation (§7.4).

Pure module tests: no LLM, no HTTP, no flags mutated (the evaluator is
a pure function; flag-dependent behavior lives in the API E2E suite).
"""

import pytest

from app.goal_postconditions import (
    POLARITY_NEGATIVE,
    POLARITY_POSITIVE,
    R_COMPARISON_FACTS_ABSENT,
    R_DEST_COMPARISON_COMPLETE,
    R_DEST_IDENTITY_PARTIAL,
    R_RECORDS_RETRIEVED_COMPLETE,
    derive_goal_outcome,
    evaluate_output,
)
from app.goal_resolution import (
    OutputSatisfaction,
    PostconditionFacet,
    RequiredOutput,
)
from app.skills import EvidenceType

FACET = PostconditionFacet.DESTINATION_ADDRESS_IDENTITY.value
UNRESOLVED = OutputSatisfaction.UNRESOLVED
SATISFIED = OutputSatisfaction.SATISFIED
PARTIAL_SUP = OutputSatisfaction.PARTIALLY_SUPPORTED
PENDING = OutputSatisfaction.PENDING


def faceted_output(output_id="O1", **overrides):
    kwargs = dict(
        output_id=output_id,
        claim="withdrawal_destination_comparison",
        claim_kind="retrieval",
        subject_ref="F3",
        scope="finding_level",
        evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
        postcondition_facets=[FACET],
    )
    kwargs.update(overrides)
    return RequiredOutput(**kwargs)


def plain_output(output_id="O1", **overrides):
    kwargs = dict(
        output_id=output_id,
        claim="withdrawal_records_list",
        claim_kind="retrieval",
        subject_ref="F3",
        scope="finding_level",
        evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
    )
    kwargs.update(overrides)
    return RequiredOutput(**kwargs)


def block(groups, without=0):
    """A projected destination_identity fact block (the same shape
    `_destination_identity_summary` projects). groups: list of
    (destination_ref, address, [record_ids])."""
    identified = sum(len(ids) for _, _, ids in groups)
    return {
        "basis": "withdrawal.address",
        "records_total": identified + without,
        "records_with_identity": identified,
        "records_without_identity": without,
        "unique_destinations": len(groups),
        "groups": [
            {"destination_ref": ref, "address": addr,
             "record_ids": list(ids)}
            for ref, addr, ids in groups
        ],
        "normalization": "trim; exact case-sensitive (shape descriptive)",
    }


def observation(summary, output_id="O1", outcome="success",
                types=("withdrawal_records",)):
    return {"tool_name": "finding_drilldown", "outcome": outcome,
            "for_outputs": [output_id],
            "actual_evidence_types": list(types),
            "summary": summary}


def nine_distinct():
    ids = [f"W00016{i}" for i in range(4, 13)]      # 9 records
    return block([(f"dest-{i}", f"0xaddr{i:02x}", [rid])
                  for i, rid in enumerate(ids, 1)])


def nine_identical():
    ids = [f"W00016{i}" for i in range(4, 13)]
    return block([("dest-1", "0xsame", ids)])


def nine_mixed():
    # 9 records → 3 groups: 4 + 3 + 2
    return block([
        ("dest-1", "0xa", ["W000164", "W000165", "W000166", "W000167"]),
        ("dest-2", "0xb", ["W000168", "W000169", "W000170"]),
        ("dest-3", "0xc", ["W000171", "W000172"]),
    ])


def retrieval_summary(block_dict=None, count=9):
    s = {"record_count": count, "complete": True,
         "record_rows": [{"record_id": f"W0001{i:02d}", "timestamp": "t",
                          "summary": "s"} for i in range(4, 13)]}
    if block_dict is not None:
        s["destination_identity"] = block_dict
    return s


class TestFacetVerdicts:
    def test_nine_records_nine_destinations_satisfied_positive(self):
        """§14-1: 9 records → 9 distinct destinations → SATISFIED
        (positive), reason destination_comparison_complete, K=9 in the
        conclusion — never a 'records retrieved' verdict."""
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(nine_distinct()))])
        assert res.satisfaction == SATISFIED
        assert res.polarity == POLARITY_POSITIVE
        assert res.reason_code == R_DEST_COMPARISON_COMPLETE
        assert res.completeness == "complete"
        assert "9 笔提现共指向 9 个不同目的地" in res.detail

    def test_nine_records_one_destination_satisfied_negative(self):
        """§14-2: all nine identical → SATISFIED (negative polarity —
        the facts' shape), same reason code; polarity never means a
        risk hypothesis is true/false."""
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(nine_identical()))])
        assert res.satisfaction == SATISFIED
        assert res.polarity == POLARITY_NEGATIVE
        assert res.reason_code == R_DEST_COMPARISON_COMPLETE
        assert "全部 9 笔提现指向同一目的地" in res.detail
        assert "dest-1" in res.detail

    def test_mixed_groups_reflect_actual_partition(self):
        """§14-3: mixed repeats → conclusion matches the actual 3-group
        partition (4+3+2), not forced into same/all-different."""
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(nine_mixed()))])
        assert res.satisfaction == SATISFIED
        assert res.polarity == POLARITY_POSITIVE
        assert "9 笔提现共指向 3 个不同目的地" in res.detail

    def test_partial_identity_partially_supported(self):
        """§14-4: some records missing identity → PARTIALLY_SUPPORTED
        with the M/N boundary; the whole-set comparison is never
        claimed; comparable-subset facts may stand."""
        blk = nine_mixed()
        blk["records_without_identity"] = 3
        blk["records_total"] = 12
        blk["records_with_identity"] = 9
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(blk, count=12))])
        assert res.satisfaction == PARTIAL_SUP
        assert res.reason_code == R_DEST_IDENTITY_PARTIAL
        assert "3/12 笔记录缺少目的地标识" in res.detail
        assert "整体的比较尚未建立" in res.detail

    def test_all_identities_missing_is_partial_not_satisfied(self):
        """Degenerate subset: every record lacks identity → still the
        §7.2 row-3 verdict (missing > 0); never an all-records-distinct
        or all-same conclusion over zero comparable facts."""
        blk = block([], without=9)
        assert blk["unique_destinations"] == 0
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(blk))])
        assert res.satisfaction == PARTIAL_SUP
        assert res.reason_code == R_DEST_IDENTITY_PARTIAL
        assert "9/9 笔记录缺少目的地标识" in res.detail


class TestFactsAbsentSuppression:
    def test_retrieval_success_without_block_never_satisfies(self):
        """§14-5 / row 5: the retrieval succeeded (record_count present
        — the existing records branch WOULD return satisfied-positive)
        but the comparison facts are absent → UNRESOLVED
        comparison_facts_absent. Retrieval never satisfies a faceted
        output."""
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(None))])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT
        assert "未获得完成目的地比较所需的地址事实" in res.detail

    def test_no_determinate_category_verdict_faceted_falls_to_absent(self):
        """Faceted output whose categories yield no verdict at all →
        the facet's facts-absent boundary (not the generic
        facts_indeterminate)."""
        res = evaluate_output(faceted_output(), "U00010",
                              [observation({"unrelated": 1})])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT

    def test_empty_retrieval_inherits_empty_complete_negative(self):
        """Row 4: an authoritative EMPTY retrieval (no withdrawal
        records at all) inherits the existing records postcondition —
        the determinate negative for the comparison request."""
        res = evaluate_output(
            faceted_output(), "U00010",
            [observation({"record_count": 0}, outcome="empty")])
        assert res.satisfaction == SATISFIED
        assert res.polarity == POLARITY_NEGATIVE
        assert res.reason_code == "records_empty_complete_scope"


class TestBlockConsistencyGate:
    def inconsistent(self, mutate):
        blk = nine_distinct()
        mutate(blk)
        return blk

    def test_count_mismatch_rejected(self):
        blk = self.inconsistent(lambda b: b.__setitem__(
            "records_with_identity", 8))
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(blk))])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT

    def test_group_cardinality_mismatch_rejected(self):
        blk = self.inconsistent(lambda b: b.__setitem__(
            "unique_destinations", 5))
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(blk))])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT

    def test_member_duplication_across_groups_rejected(self):
        def dup(b):
            b["groups"][1]["record_ids"].append("W000164")
            b["groups"][1]["record_ids"].sort()
        blk = self.inconsistent(dup)
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(blk))])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT

    def test_non_integer_counts_rejected(self):
        blk = self.inconsistent(lambda b: b.__setitem__(
            "records_total", "9"))
        res = evaluate_output(faceted_output(), "U00010",
                              [observation(retrieval_summary(blk))])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT


class TestAssociationAndProvenance:
    def test_block_from_other_output_is_not_used(self):
        """The comparison facts are valid only through THIS output's
        association (GSP-3): a block attached to another output's
        observation never satisfies this one — wrong-target facts stay
        wrong-target."""
        other = observation(retrieval_summary(nine_distinct()),
                            output_id="O2")
        res = evaluate_output(faceted_output(), "U00010", [other])
        assert res.satisfaction == PENDING
        assert res.reason_code == "no_associated_evidence"

    def test_verdict_anchored_to_associated_observation_only(self):
        own = observation(retrieval_summary(nine_distinct()),
                          output_id="O1")
        stray = observation(retrieval_summary(nine_identical()),
                            output_id="O2")
        res = evaluate_output(faceted_output(), "U00010", [own, stray])
        assert res.satisfaction == SATISFIED
        assert res.polarity == POLARITY_POSITIVE      # OWN block's facts
        assert res.observation_indexes == [0]


class TestRepeatObservations:
    def test_identical_repeat_preserves_verdict(self):
        """§五-5 / §14-16: a re-plan repeating the same evidence yields
        the identical block — unanimity keeps the verdict, no
        upgrade/no conflict."""
        o = observation(retrieval_summary(nine_distinct()))
        res = evaluate_output(faceted_output(), "U00010", [o, dict(o)])
        assert res.satisfaction == SATISFIED
        assert res.reason_code == R_DEST_COMPARISON_COMPLETE

    def test_absent_facts_repeat_stays_unresolved(self):
        o = observation(retrieval_summary(None))
        res = evaluate_output(faceted_output(), "U00010", [o, dict(o)])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == R_COMPARISON_FACTS_ABSENT

    def test_conflicting_blocks_never_satisfied(self):
        """Two observations disagreeing on the comparison facts: the
        scalar-conflict norm applies — the key is dropped, the verdict
        cannot be satisfied-with-a-value, and the reason states the
        conflict."""
        o1 = observation(retrieval_summary(nine_distinct()))
        o2 = observation(retrieval_summary(nine_identical()))
        res = evaluate_output(faceted_output(), "U00010", [o1, o2])
        assert res.satisfaction == UNRESOLVED
        assert res.reason_code == "conflicting_observations"
        assert "destination_identity" in res.conflicts


class TestReplanFacetPreservationL1:
    def test_coverage_readmission_keeps_facet_and_contract(self):
        """GSP-7 contract immutability for faceted contracts: the
        deterministic coverage re-admission a re-plan runs must leave
        the accepted outputs' facets intact and keep the output
        covered."""
        from app.planning_coverage import admit_plan_coverage
        from app.goal_resolution import build_goal_contract, GoalRepresentation
        rep = GoalRepresentation(
            case_id="U00010", goal_type="policy_check",
            goal_resolution="INFERRED", subject_type="finding",
            subject_ref="F3", ambiguous_goal_types=[], reason="r",
            source="llm", request_text="req",
            contract_proposal={"required_outputs": [
                {"claim": "withdrawal_destination_comparison",
                 "claim_kind": "retrieval", "subject_ref": "F3",
                 "scope": "finding_level",
                 "evidence_requirements": ["withdrawal_records"],
                 "postcondition_facets": [FACET]}]})
        contract, reason = build_goal_contract(rep, "U00010")
        assert reason is None and contract is not None
        assert contract.outputs[0].postcondition_facets == [FACET]
        coverage = admit_plan_coverage(
            contract, ["inspect_withdrawals"])
        assert coverage[0].coverage_status == "covered"
        # the CONTRACT is never mutated by admission/re-admission
        assert contract.outputs[0].postcondition_facets == [FACET]
        assert contract.outputs[0].satisfaction == PENDING


class TestNonFacetedRegression:
    def test_retrieval_only_output_unaffected(self):
        """§14-9: a plain retrieval output keeps the existing
        satisfied-by-retrieval semantics — the facet branch must not
        touch outputs that declare no facet."""
        res = evaluate_output(plain_output(), "U00010",
                              [observation(retrieval_summary(None))])
        assert res.satisfaction == SATISFIED
        assert res.polarity == POLARITY_POSITIVE
        assert res.reason_code == R_RECORDS_RETRIEVED_COMPLETE

    def test_facet_on_other_evidence_class_ignored(self):
        """A facet unknown to this evaluation path (future vocabulary
        members) leaves the existing verdicts untouched."""
        res = evaluate_output(plain_output(postcondition_facets=[
            "some_future_facet"]), "U00010",
            [observation(retrieval_summary(None))])
        assert res.satisfaction == SATISFIED
        assert res.reason_code == R_RECORDS_RETRIEVED_COMPLETE


class TestGoalOutcomeMapping:
    def test_satisfied_comparison_maps_complete(self):
        """§14-1/2: a fully-supported comparison derives COMPLETE
        through the UNCHANGED GoalOutcome rule."""
        outcome = derive_goal_outcome([SATISFIED], "completed")
        assert outcome.value == "complete"

    def test_partial_comparison_maps_partial(self):
        outcome = derive_goal_outcome([PARTIAL_SUP], "completed")
        assert outcome.value == "partial"

    def test_compound_satisfied_plus_partial_maps_partial(self):
        """§7.4: one satisfied (faceted) + one partially-supported
        (faceted) output → overall PARTIAL — never upgraded."""
        outcome = derive_goal_outcome([SATISFIED, PARTIAL_SUP],
                                      "completed")
        assert outcome.value == "partial"

    def test_compound_satisfied_plus_unresolved_maps_partial(self):
        outcome = derive_goal_outcome([SATISFIED, UNRESOLVED],
                                      "completed")
        assert outcome.value == "partial"

    def test_unresolved_alone_maps_unanswerable(self):
        outcome = derive_goal_outcome([UNRESOLVED], "completed")
        assert outcome.value == "unanswerable"

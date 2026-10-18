"""WP-CG-2 — Contract Acceptance and Semantic Deduplication tests.

L1 coverage (implementation plan §8), mapped to the acceptance matrix:

  A/B   legal 2- and 3-output COMPOUND → one goal_type=None contract
  C     exact duplicate collapsed once (first occurrence, request order)
  D     permuted evidence_requirements → ANY-set canonical equivalence
  E/T   one output with several evidence requirements stays ONE output
  F/G/H/I/K  near-miss non-equivalences (subject / scope / claim_kind /
        evidence set / similar-but-different claims) → NEVER collapsed
  J     different claim_description with identical canonical content →
        collapsed (description is prose ABOUT the deliverable, excluded
        from canonical semantics per the approved design)
  L/M/N/Q  field-level failures → whole-proposal rejection (existing
        codes, no partial acceptance)
  O/P   COMPOUND with <2 independent outputs after dedup → rejected
        (`compound_single_output`), never narrowed to a single-goal
        contract
  R     COMPOUND with a fabricated top-level goal_type → rejected by
        the representation validator (AD-1)
  S/U/V/W  single-goal compatibility, flag-OFF scoping, legacy reads,
        canonicalization stability

Checkpoint wiring: the new `compound_single_output` rejection surfaces
as `goal_contract_decision.invalid_proposal:compound_single_output`
(WP-CG-3 mechanism, unchanged).
"""

import json
import tempfile

import pytest

from app.config import settings
from app.goal_resolution import (
    GoalRepresentation, GoalResolutionStatus, build_goal_contract,
    understand_goal)
from app.models import InvestigationContext
from app.task_store_v2 import TaskStoreV2

from tests.test_al351_target_validity_guard_v1 import _build_runner
from tests.test_api_investigations_v1 import make_findings


def _repr(resolution="COMPOUND", goal_type=None, outputs=None,
          subject="U00010", case_id="U00010"):
    return GoalRepresentation(
        case_id=case_id,
        goal_type=goal_type,
        goal_resolution=resolution,
        subject_type="case",
        subject_ref=subject,
        ambiguous_goal_types=[],
        reason="r",
        source="llm",
        request_text="req",
        contract_proposal={"required_outputs": list(outputs or [])},
    )


def _out(claim="hedge_presence", kind="existence_check", subject="U00010",
         scope="account_level", reqs=("opposite_trade_relations",),
         description=None):
    out = {"claim": claim, "claim_kind": kind, "subject_ref": subject,
           "scope": scope, "evidence_requirements": list(reqs)}
    if description is not None:
        out["claim_description"] = description
    return out


OUT_HEDGE = _out("hedge_presence")
OUT_RELATED = _out("related_accounts_list", kind="retrieval",
                   scope="case_level", reqs=("network_relations",))
OUT_POLICY = _out("policy_requirements", kind="retrieval",
                  scope="case_level", reqs=("policy_references",))


def _claims(contract):
    return [o.claim for o in contract.outputs]


def _ids(contract):
    return [o.output_id for o in contract.outputs]


class TestLegalCompoundContracts:
    def test_a_two_independent_outputs_one_contract(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE, OUT_RELATED]))
        assert reason is None and contract is not None
        assert contract.goal_type is None            # AD-1
        assert _claims(contract) == ["hedge_presence",
                                     "related_accounts_list"]
        assert _ids(contract) == ["O1", "O2"]

    def test_b_three_outputs_all_kept(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE, OUT_RELATED, OUT_POLICY]))
        assert reason is None
        assert _claims(contract) == ["hedge_presence",
                                     "related_accounts_list",
                                     "policy_requirements"]
        assert _ids(contract) == ["O1", "O2", "O3"]

    def test_c_exact_duplicate_collapsed_once(self):
        dup = _out("hedge_presence", description="不同的措辞描述")
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE, dup, OUT_RELATED]))
        assert reason is None
        assert _claims(contract) == ["hedge_presence",
                                     "related_accounts_list"]
        assert _ids(contract) == ["O1", "O2"]        # stable, gap-free

    def test_d_permuted_evidence_requirements_equivalent(self):
        permuted = _out("hedge_presence",
                        reqs=("opposite_trade_relations",
                              "transaction_records"))
        same_set_diff_order = _out("hedge_presence",
                                   reqs=("transaction_records",
                                         "opposite_trade_relations"))
        contract, reason = build_goal_contract(
            _repr(outputs=[permuted, same_set_diff_order, OUT_RELATED]))
        assert reason is None
        assert _claims(contract) == ["hedge_presence",
                                     "related_accounts_list"]

    def test_w_canonicalization_stable_across_runs(self):
        outputs = [OUT_HEDGE,
                   _out("hedge_presence",
                        reqs=("opposite_trade_relations",)),
                   OUT_RELATED]
        c1, _ = build_goal_contract(_repr(outputs=outputs))
        c2, _ = build_goal_contract(_repr(outputs=outputs))
        assert _claims(c1) == _claims(c2)
        assert _ids(c1) == _ids(c2)
        assert [o.subject_ref for o in c1.outputs] == \
            [o.subject_ref for o in c2.outputs]


class TestSemanticDedupCounterExamples:
    def test_f_same_claim_different_subject_not_collapsed(self):
        """Both subjects are LEGAL here (the accepted finding-subject F3
        and the case id U00299 — the existing consistency rule allows
        both), yet they are different subjects: dedup must NOT collapse
        them."""
        rep = GoalRepresentation(
            case_id="U00299", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="finding", subject_ref="F3",
            ambiguous_goal_types=[], reason="r", source="llm",
            request_text="req",
            contract_proposal={"required_outputs": [
                _out("hedge_presence", subject="F3"),
                _out("hedge_presence", subject="U00299")]})
        contract, reason = build_goal_contract(rep)
        assert reason is None
        assert len(contract.outputs) == 2
        assert [o.subject_ref for o in contract.outputs] == ["F3", "U00299"]

    def test_g_same_claim_different_scope_not_collapsed(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE,
                           _out("hedge_presence", scope="case_level")]))
        assert reason is None
        assert len(contract.outputs) == 2

    def test_h_different_claim_kind_not_collapsed(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE,
                           _out("hedge_presence", kind="retrieval")]))
        assert reason is None
        assert len(contract.outputs) == 2

    def test_i_different_evidence_requirements_not_collapsed(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE,
                           _out("hedge_presence",
                                reqs=("withdrawal_records",))]))
        assert reason is None
        assert len(contract.outputs) == 2

    def test_k_similar_text_different_deliverable_not_collapsed(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[_out("对冲交易存在性核查"),
                           _out("对冲交易存在性核查（含交易明细清单）")]))
        assert reason is None
        assert len(contract.outputs) == 2            # distinct deliverables

    def test_j_description_differs_canonical_same_collapsed(self):
        """Approved canonical semantics exclude claim_description (user-
        safe prose about the same deliverable): identical canonical
        content with different descriptions is ONE deliverable."""
        contract, reason = build_goal_contract(
            _repr(outputs=[
                _out("hedge_presence", description="描述甲"),
                _out("hedge_presence", description="描述乙，措辞不同"),
                OUT_RELATED]))
        assert reason is None
        assert len(contract.outputs) == 2
        assert contract.outputs[0].claim_description == "描述甲"


class TestAllOrNothing:
    def test_l_missing_field_rejects_whole_compound(self):
        bad = _out("hedge_presence")
        bad["subject_ref"] = ""
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE, bad, OUT_RELATED]))
        assert contract is None
        assert reason == "missing_subject"

    def test_m_illegal_evidence_type_rejects_whole(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE,
                           _out("related_accounts_list",
                                kind="retrieval", scope="case_level",
                                reqs=("fund_flow",))]))
        assert contract is None
        assert reason == "invalid_evidence_type"

    def test_n_empty_outputs_rejected(self):
        # layer 1 (CG-1 structural): a COMPOUND representation with an
        # empty entry list is invalid at construction
        with pytest.raises(ValueError):
            _repr(outputs=[])
        # layer 2 (builder, reachable pre-CG-1-check e.g. INFERRED):
        # empty outputs → no_outputs
        rep = _repr(resolution="INFERRED", goal_type="policy_check",
                    outputs=[])
        contract, reason = build_goal_contract(rep)
        assert contract is None
        assert reason == "no_outputs"

    def test_o_compound_single_output_rejected_not_narrowed(self):
        # layer 1 (CG-1 structural): a 1-entry COMPOUND representation is
        # invalid at construction
        with pytest.raises(ValueError):
            _repr(outputs=[OUT_HEDGE])
        # layer 2 (builder, defense in depth — validated here directly
        # with the validator bypassed via model_construct): a COMPOUND
        # proposal whose dedup leaves <2 outputs is rejected, never
        # narrowed into a single-goal contract
        raw = GoalRepresentation.model_construct(
            case_id="U00010", goal_type=None,
            goal_resolution=GoalResolutionStatus.COMPOUND.value,
            subject_type="case", subject_ref="U00010",
            ambiguous_goal_types=[], reason="r", source="llm",
            request_text="req",
            contract_proposal={"required_outputs": [
                _out("hedge_presence"), _out("hedge_presence")]})
        contract, reason = build_goal_contract(raw)
        assert contract is None
        assert reason == "compound_single_output"

    def test_p_dedup_to_one_rejected(self):
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE,
                           _out("hedge_presence",
                                description="同义重复")]))
        assert contract is None
        assert reason == "compound_single_output"

    def test_q_partial_validity_never_partially_accepted(self):
        bad = _out("related_accounts_list", kind="retrieval",
                   scope="case_level", reqs=("network_relations",))
        bad["claim_kind"] = "not_a_kind"
        contract, reason = build_goal_contract(
            _repr(outputs=[OUT_HEDGE, bad, OUT_POLICY]))
        assert contract is None
        assert reason == "invalid_claim_kind"

    def test_r_compound_with_fabricated_goal_type_rejected(self):
        with pytest.raises(ValueError):
            _repr(goal_type="cross_account_check",
                  outputs=[OUT_HEDGE, OUT_RELATED])


class TestSingleGoalCompat:
    def test_s_single_goal_path_unchanged(self):
        rep = _repr(resolution="INFERRED", goal_type="policy_check",
                    outputs=[OUT_HEDGE])
        contract, reason = build_goal_contract(rep)
        assert reason is None
        assert contract.goal_type == "policy_check"
        assert _ids(contract) == ["O1"]              # no dedup, no ≥2 rule

    def test_t_single_output_multi_evidence_stays_one(self):
        multi = _out("withdrawal_evidence_report", kind="retrieval",
                     reqs=("withdrawal_records", "transaction_records",
                           "policy_references"))
        rep = _repr(resolution="INFERRED", goal_type="policy_check",
                    outputs=[multi])
        contract, reason = build_goal_contract(rep)
        assert reason is None
        assert len(contract.outputs) == 1
        assert len(contract.outputs[0].evidence_requirements) == 3

    def test_u_flag_off_checkpoint_never_builds(self, monkeypatch,
                                                tmp_path):
        """Flag OFF: the checkpoint never calls the builder — verified at
        the loop level (no contract, no provenance under OFF)."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            False)
        rep = _repr(outputs=[OUT_HEDGE, OUT_RELATED])
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg2_off.db")
        findings = make_findings()
        runner = _build_runner(
            store,
            resolver=None,
            planner_scripts=(
                '{"skill_id": "case_intake", "goal": "g", "steps": '
                '[{"type": "fetch_case", "reason": "r"}]}',),
            decisions=("STOP",))
        runner.goal_resolver = lambda req, fid, case, diag=None: rep
        from tests.test_api_investigations_v1 import rp_patches
        p1, p2 = rp_patches()
        with p1, p2:
            result = runner.run("帮我梳理一下这个账号目前的情况。",
                                InvestigationContext(case_id="U00010"),
                                findings, investigation_id="INV-CG2-U",
                                task_id="TASK-CG2-U",
                                findings_provider=lambda: findings)
        assert result.state.goal_contract is None
        assert result.state.goal_contract_decision is None

    def test_v_single_goal_multi_output_contract_still_accepted(self):
        """Pre-existing behavior (gsp2 test_case_d shape): INFERRED
        multi-output proposals are accepted unchanged — CG-2's COMPOUND
        rules do not retroactively apply to the single-goal path."""
        rep = _repr(resolution="INFERRED", goal_type="policy_check",
                    outputs=[OUT_HEDGE, OUT_RELATED, OUT_POLICY])
        contract, reason = build_goal_contract(rep)
        assert reason is None
        assert len(contract.outputs) == 3            # no dedup under INFERRED
        assert _ids(contract) == ["O1", "O2", "O3"]


class TestCheckpointWiring:
    def test_compound_single_output_surfaces_as_invalid_proposal(self,
                                                                 monkeypatch,
                                                                 tmp_path):
        """The new rejection code flows through the WP-CG-3 decision
        provenance unchanged: invalid_proposal:compound_single_output."""
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        dup_pair = [_out("hedge_presence", subject="U00299"),
                    _out("hedge_presence", subject="U00299")]
        rep = _repr(outputs=dup_pair, subject="U00299", case_id="U00299")
        store = TaskStoreV2(tempfile.mkdtemp() + "/cg2_wire.db")
        findings = make_findings()
        runner = _build_runner(
            store,
            resolver=None,
            planner_scripts=(
                '{"skill_id": "case_intake", "goal": "g", "steps": '
                '[{"type": "fetch_case", "reason": "r"}]}',),
            decisions=("STOP",))
        runner.goal_resolver = lambda req, fid, case, diag=None: rep
        from tests.test_api_investigations_v1 import rp_patches
        p1, p2 = rp_patches()
        with p1, p2:
            result = runner.run("帮我梳理一下这个账号目前的情况。",
                                InvestigationContext(case_id="U00299"),
                                findings, investigation_id="INV-CG2-W",
                                task_id="TASK-CG2-W",
                                findings_provider=lambda: findings)
        s = result.state
        assert s.goal_contract is None
        assert s.goal_contract_decision == {
            "outcome": "invalid_proposal:compound_single_output",
            "reason": "compound_single_output"}
        assert s.agent_status == "completed"         # guarded legacy path

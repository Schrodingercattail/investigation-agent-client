"""WP-2 Version B S4 — frozen test-matrix completion (unit/component).

Covers the matrix items Final Rulings 1-2 and the P3 acceptance
pipeline require that earlier stages did not yet pin:

- Final Ruling 1 pairing matrix completion (2 refs × 2 steps;
  multi-step no-ref);
- duplicate / missing candidate_id enforcement at the admission gate
  (fail closed BEFORE any injection);
- shared transaction_id across candidates MUST NOT be falsely rejected
  (frozen: tids have no uniqueness guarantee);
- tool-level leg mutation classes (swap / add / remove) with the exact
  rejection reasons;
- rejected exports carry NOTHING that could mark a target delivered.

Stage boundaries: API E2E matrices belong to S5; Browser to S6;
archival to S7 — none exercised here. Mixed-target independent
outcomes remain NOT PROVEN (V1 structural, carried per the kickoff
§13.5 S5 DoD).

Feature flags: the service and the candidate branch are
flag-independent; no flag is consulted (recorded per the D4
discipline).
"""

from types import SimpleNamespace

from app.delivery_admission import (
    admit_delivery_targets,
    canonical_records_digest,
    enumerate_delivery_targets,
    resolve_binding,
)
from app.domain_tools.artifact_bundle import artifact_bundle
from app.models import ToolResultOutcome

SET_REF = "CS-ab12cd34-S9"
TASK = "TASK-1"
CASE = "U00010"
BINDING = {"kind": "candidate_set_export", "status": "bound_ready",
           "set_ref": SET_REF, "source_task_id": TASK,
           "source_step_id": "S9", "delivery_target_id": "DT-S2-1",
           "case_id": CASE}


def mk_candidate(cid="CATA-001", t1="TX-1", t2="TX-2", value=100.0):
    def leg(tid, side):
        return {"transaction_id": tid, "account_id": "ACC-A",
                "side": side, "quantity": 1.0, "value": value,
                "symbol": "BTC", "timestamp": "2026-08-19T10:00:00+00:00"}
    return {"candidate_id": cid, "rank": 1, "account_a": "ACC-A",
            "account_b": "ACC-B", "symbol": "BTC",
            "supporting": [leg(t1, "BUY"), leg(t2, "SELL")]}


def mk_candidates():
    return [mk_candidate()]


def mk_entry(candidates=None, set_ref=SET_REF, task=TASK):
    return {"set_ref": set_ref, "source_task_id": task,
            "source_step_id": "S9", "created_at": "t",
            "bounded_data": False, "origin": "analysis",
            "candidates": candidates if candidates is not None
            else [mk_candidate()]}


def _admit(request_text, plan_steps, session_sets):
    return admit_delivery_targets(
        goal_contract=None, decision_outcome="heuristic_single_goal",
        request_text=request_text, plan_steps=plan_steps,
        session_sets=session_sets, investigation_task_ids={TASK},
        case_id=CASE)


def _call(candidates, expected_digest=None, binding=None):
    return artifact_bundle(
        scope="case", case_id=CASE, task_id=TASK,
        source_tool_calls=[SimpleNamespace(tool_call_id="TC-S2-0001")],
        source_kind="candidate_set", set_ref=SET_REF,
        candidate_records=candidates, bounded_data=False,
        source_task_id=TASK, expected_records_digest=expected_digest,
        expected_binding=binding, delivery_target_id="DT-S2-1",
        source_step_id="S9")


def _rejected(result, reason):
    assert result.outcome == ToolResultOutcome.SUCCESS
    rej = (result.data or {}).get("candidate_set_export_rejected")
    assert rej is not None, f"expected rejection, got {result.data}"
    assert rej["reason"] == reason
    assert "artifact" not in (result.data or {})
    # a rejected export carries NOTHING that could mark a target
    # delivered (no verification payload exists to read)
    assert "candidate_set_export" not in (result.data or {})
    return rej


# --- Final Ruling 1: pairing matrix completion -------------------------------

class TestPairingMatrixCompletion:
    def test_two_refs_two_steps_ambiguous_both(self):
        targets = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"},
             {"step_id": "S3", "type": "generate_artifact"}],
            ["CS-1", "CS-2"])
        assert [t.invalid_reason for t in targets] == \
            ["ambiguous_mapping", "ambiguous_mapping"]

    def test_multi_step_no_ref_all_no_explicit_ref(self):
        r = _admit("generate all trade records as an artifact",
                   [{"step_id": "S2", "type": "generate_artifact"},
                    {"step_id": "S3", "type": "generate_artifact"}],
                   [mk_entry()])
        assert r["mode"] == "admitted"
        assert r["valid"] == []
        assert [i["reason"] for i in r["invalid"]] == \
            ["no_explicit_ref", "no_explicit_ref"]


# --- Final Ruling 2: candidate-id identity enforcement -----------------------

class TestCandidateIdEnforcement:
    def test_duplicate_candidate_id_fails_at_admission(self):
        cands = [mk_candidate(), mk_candidate()]
        r = _admit("导出 CS-ab12cd34-S9 的候选交易明细文件",
                   [{"step_id": "S2", "type": "generate_artifact"}],
                   [mk_entry(candidates=cands)])
        assert r["invalid"][0]["reason"] == "duplicate_candidate_id"
        assert r["valid"] == []

    def test_missing_candidate_id_malformed(self):
        bad = mk_candidate()
        bad.pop("candidate_id")
        r = _admit("导出 CS-ab12cd34-S9 的候选交易明细文件",
                   [{"step_id": "S2", "type": "generate_artifact"}],
                   [mk_entry(candidates=[bad])])
        assert r["invalid"][0]["reason"] == "malformed_entry"


# --- Final Ruling 2: shared transaction_id must not be falsely rejected ------

class TestSharedTransactionId:
    def test_shared_leg_tid_delivers(self):
        """Frozen: tids have NO uniqueness guarantee — the same
        transaction may legally support two candidates; the digest is
        computed over the same records on both sides, so this delivers
        (no false rejection)."""
        cands = [mk_candidate("CATA-001", "TX-1", "TX-2"),
                 mk_candidate("CATA-002", "TX-2", "TX-3")]
        result = _call(cands, canonical_records_digest(cands), BINDING)
        assert result.outcome == ToolResultOutcome.SUCCESS
        data = result.data
        assert (data or {}).get("artifact")
        cs = data["candidate_set_export"]
        assert cs["records_digest_verification"]["passed"]
        # both tids present exactly once in the SET-semantic verification
        assert sorted(cs["verification"]["transaction_ids"]) == \
            ["TX-1", "TX-2", "TX-3"]


# --- P3 matrix completion: leg-level mutation classes ------------------------

class TestLegMutationClasses:
    def _base_digest(self):
        return canonical_records_digest(mk_candidates())

    def test_leg_swap_fails_digest(self):
        """Same leg count, one leg replaced by a DIFFERENT transaction —
        passes legacy validation (2 complete dict legs) but MUST fail
        the post-clean digest comparison."""
        swapped = mk_candidates()
        swapped[0]["supporting"][1] = {
            "transaction_id": "TX-EVIL", "account_id": "ACC-A",
            "side": "SELL", "quantity": 1.0, "value": 100.0,
            "symbol": "BTC", "timestamp": "2026-08-19T10:00:15+00:00"}
        _rejected(_call(swapped, self._base_digest(), BINDING),
                  "digest_mismatch")

    def test_leg_add_fails_legacy_validation(self):
        """Adding a third complete leg → legs=3 → the U00033-era
        two-leg validation rejects BEFORE the digest gate (fail closed,
        different reason — both are pre-construction rejections)."""
        three = mk_candidates()
        three[0]["supporting"].append({
            "transaction_id": "TX-3", "account_id": "ACC-A",
            "side": "SELL", "quantity": 1.0, "value": 100.0,
            "symbol": "BTC", "timestamp": "2026-08-19T10:01:00+00:00"})
        _rejected(_call(three, self._base_digest(), BINDING),
                  "field_incomplete")

    def test_leg_remove_fails_legacy_validation(self):
        one = mk_candidates()
        one[0]["supporting"] = one[0]["supporting"][:1]
        _rejected(_call(one, self._base_digest(), BINDING),
                  "field_incomplete")

    def test_null_vs_absent_field_both_fail(self):
        for mutate in (
                lambda cands: cands[0]["supporting"][0].update(
                    {"value": None}),
                lambda cands: cands[0]["supporting"][0].pop("value")):
            cands = mk_candidates()
            mutate(cands)
            _rejected(_call(cands, self._base_digest(), BINDING),
                      "field_incomplete")

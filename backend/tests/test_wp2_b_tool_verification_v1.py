"""WP-2 Version B S2 — tool-level integrity gates for the candidate
export branch (artifact_bundle._render_candidate_set_export).

Frozen pipeline (design Final Rulings 2-3 + P3 boundary revision): the
records-digest gate and the provenance-binding gate run BEFORE Markdown
rendering and BEFORE ArtifactV2 construction — a failed verification
produces ZERO artifact (rejected payload, like the three legacy
rejection classes). The gates apply only when the B admission injected
`expected_records_digest` / `expected_binding`; the legacy contract
path (no expected args) is byte-unchanged and covered here as a
regression guard.

Feature flags: the candidate branch is flag-independent; no flag is
consulted (recorded per the D4 discipline).
"""

from types import SimpleNamespace

from app.delivery_admission import canonical_records_digest
from app.domain_tools.artifact_bundle import artifact_bundle
from app.models import ToolResultOutcome

SET_REF = "CS-ab12cd34-S9"
TASK = "TASK-1"
CASE = "U00010"
BINDING = {"kind": "candidate_set_export", "status": "bound_ready",
           "set_ref": SET_REF, "source_task_id": TASK,
           "source_step_id": "S9", "delivery_target_id": "DT-S2-1",
           "case_id": CASE}
PROVENANCE = [SimpleNamespace(tool_call_id="TC-S2-0001")]


def mk_candidate(cid="CATA-002", t1="TX-3", t2="TX-4"):
    def leg(tid, side, value=100.0):
        return {"transaction_id": tid, "account_id": "ACC-A",
                "side": side, "quantity": 1.0, "value": value,
                "symbol": "BTC", "timestamp": "2026-08-19T10:00:00+00:00"}
    return {"candidate_id": cid, "rank": 2,
            "account_a": "ACC-A", "account_b": "ACC-B", "symbol": "BTC",
            "supporting": [leg(t1, "BUY"), leg(t2, "SELL")]}


def mk_candidates():
    def leg(tid, side, value=100.0):
        return {"transaction_id": tid, "account_id": "ACC-A",
                "side": side, "quantity": 1.0, "value": value,
                "symbol": "BTC", "timestamp": "2026-08-19T10:00:00+00:00"}
    return [{"candidate_id": "CATA-001", "rank": 1,
             "account_a": "ACC-A", "account_b": "ACC-B", "symbol": "BTC",
             "supporting": [leg("TX-1", "BUY"), leg("TX-2", "SELL")]}]


def _call(candidates, expected_digest=None, binding=None,
          set_ref=SET_REF, source_task_id=TASK,
          delivery_target_id="DT-S2-1", source_step_id="S9"):
    return artifact_bundle(
        scope="case", case_id=CASE, task_id=TASK,
        source_tool_calls=list(PROVENANCE), source_kind="candidate_set",
        set_ref=set_ref, candidate_records=candidates,
        bounded_data=False, source_task_id=source_task_id,
        expected_records_digest=expected_digest,
        expected_binding=binding,
        delivery_target_id=delivery_target_id,
        source_step_id=source_step_id)


def _rejected(result, reason):
    assert result.outcome == ToolResultOutcome.SUCCESS
    rej = (result.data or {}).get("candidate_set_export_rejected")
    assert rej is not None, f"expected rejection, got {result.data}"
    assert rej["reason"] == reason
    assert "artifact" not in (result.data or {})
    return rej


def _delivered(result):
    assert result.outcome == ToolResultOutcome.SUCCESS
    assert (result.data or {}).get("artifact"), result.data
    return result.data


# --- legacy path unchanged (no expected args) -------------------------------

class TestLegacyPathUnchanged:
    def test_clean_candidates_still_render(self):
        data = _delivered(_call(mk_candidates()))
        assert data["candidate_set_export"]["verification"]["passed"]
        assert "records_digest_verification" not in \
            data["candidate_set_export"]

    def test_field_incomplete_still_rejects(self):
        cands = mk_candidates()
        cands[0]["supporting"][0]["value"] = None
        _rejected(_call(cands), "field_incomplete")


# --- Gate 1: records digest (P1/P2/P3 equality) ------------------------------

class TestRecordsDigestGate:
    def test_matching_digest_delivers_with_dual_echo(self):
        cands = mk_candidates()
        data = _delivered(_call(
            cands, expected_digest=canonical_records_digest(cands),
            binding=BINDING))
        cs = data["candidate_set_export"]
        dv = cs["records_digest_verification"]
        assert dv["passed"] and dv["expected"] == dv["actual"]
        assert cs["provenance_verification"]["passed"]

    def test_cleaning_drop_fails_digest(self):
        """The [A, B, junk] hole: a non-dict leg is dropped by cleaning
        and len==2 still passes the legacy validation — the digest over
        the render-participating records MUST reject it."""
        cands = mk_candidates()
        cands[0]["supporting"] = cands[0]["supporting"] + ["junk"]
        _rejected(_call(
            cands, expected_digest=canonical_records_digest(cands),
            binding=BINDING), "digest_mismatch")

    def test_field_modification_fails_digest(self):
        original, mutated = mk_candidates(), mk_candidates()
        mutated[0]["supporting"][0]["value"] = 999.0
        _rejected(_call(
            mutated, expected_digest=canonical_records_digest(original),
            binding=BINDING), "digest_mismatch")

    def test_timestamp_modification_fails_digest(self):
        original, mutated = mk_candidates(), mk_candidates()
        mutated[0]["supporting"][0]["timestamp"] = "2026-01-01T00:00:00Z"
        _rejected(_call(
            mutated, expected_digest=canonical_records_digest(original),
            binding=BINDING), "digest_mismatch")

    def test_extra_out_of_set_record_fails_digest(self):
        expected = canonical_records_digest(mk_candidates())
        cands = mk_candidates() + [mk_candidate()]
        _rejected(_call(cands, expected_digest=expected,
                        binding=BINDING), "digest_mismatch")

    def test_removed_record_fails_digest(self):
        both = mk_candidates() + [mk_candidate()]
        expected = canonical_records_digest(both)
        one = mk_candidates()
        _rejected(_call(one, expected_digest=expected,
                        binding=BINDING), "digest_mismatch")

    def test_non_finite_actual_fails_closed(self):
        cands = mk_candidates()
        cands[0]["supporting"][0]["value"] = float("nan")
        rej = _rejected(_call(
            cands, expected_digest=canonical_records_digest(
                mk_candidates()), binding=BINDING), "digest_mismatch")
        assert rej["actual_records_digest"] is None


# --- Gate 2: provenance binding verification ---------------------------------

class TestProvenanceGate:
    def test_set_ref_substitution_rejected(self):
        cands = mk_candidates()
        digest = canonical_records_digest(cands)
        _rejected(_call(cands, expected_digest=digest, binding=BINDING,
                        set_ref="CS-other"), "provenance_mismatch")

    def test_source_task_substitution_rejected(self):
        cands = mk_candidates()
        _rejected(_call(cands,
                        expected_digest=canonical_records_digest(cands),
                        binding=BINDING,
                        source_task_id="TASK-EVIL"),
                  "provenance_mismatch")

    def test_missing_envelope_field_rejected(self):
        cands = mk_candidates()
        broken = {k: v for k, v in BINDING.items()
                  if k != "delivery_target_id"}
        _rejected(_call(cands,
                        expected_digest=canonical_records_digest(cands),
                        binding=broken), "provenance_mismatch")

    def test_cross_target_binding_rejected(self):
        """The B admission injects delivery_target_id / source_step_id
        as call arguments (S3 wiring); the tool compares them against
        the envelope, so a cross-target envelope is rejected. (Without
        the injected arguments the two fields are envelope-only — the
        association is then pinned at the admission service, see
        test_wp2_b_admission_service_v1.)"""
        cands = mk_candidates()
        binding = dict(BINDING, delivery_target_id="DT-S9-1")
        _rejected(_call(cands,
                        expected_digest=canonical_records_digest(cands),
                        binding=binding,
                        delivery_target_id="DT-S2-1"),
                  "provenance_mismatch")

    def test_malformed_envelope_field_rejected(self):
        cands = mk_candidates()
        binding = dict(BINDING, source_step_id="")
        _rejected(_call(cands,
                        expected_digest=canonical_records_digest(cands),
                        binding=binding), "provenance_mismatch")

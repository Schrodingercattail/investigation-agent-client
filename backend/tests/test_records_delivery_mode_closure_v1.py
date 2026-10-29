"""Records delivery-mode closure — D1/D2 postcondition + composer.

Pins the delivery-aware completion semantics (audited U00047 incident:
a file_export records output with a complete 66-record file was judged
PARTIAL by chat-emission parity and rendered a vague partial line).

Judgment table (goal_postconditions.py records branch):
- delivery=file_export: SATISFIED iff records facts complete (valid
  record_count, no evidence_missing) AND a generated artifact is
  associated AND the tool-layer record-parity check passed.
  Parity failed / parity unknown / no artifact / unusable facts →
  PARTIALLY_SUPPORTED with the specific reason (never silently
  SATISFIED, never "unknown treated as verified").
- delivery=detail_list (incl. the legacy default): the L1 chat-parity
  rules byte-for-byte.

Composer (D2): PARTIALLY_SUPPORTED records outputs render their facts
(rows + shown/total + file brief) instead of the bare generic line; the
dangling "see the boundary notes above" reference is gone.
"""
import json
from types import SimpleNamespace

import pytest

from app.agent_loop import _gsp_render_output_section
from app.goal_postconditions import evaluate_output
from app.goal_resolution import OutputSatisfaction
from app.skills import EvidenceType as ET

DELIVERY_FILE = "file_export"


def _out(subject="U00047", delivery=None, kind="retrieval",
         output_id="O2"):
    kwargs = {"output_id": output_id,
              "claim": "Trade records artifact export",
              "claim_kind": kind, "subject_ref": subject,
              "scope": "account_level",
              "evidence_requirements": [ET.TRANSACTION_RECORDS],
              "satisfaction": "satisfied"}
    if delivery is not None:
        kwargs["delivery"] = delivery
    from app.goal_resolution import RequiredOutput
    return RequiredOutput(**kwargs)


def _obs(summary, step_type="inspect_transactions",
         tool="finding_drilldown", outcome="success"):
    return {"step_type": step_type, "tool_name": tool,
            "outcome": outcome, "for_outputs": ["O2"],
            "actual_evidence_types": [ET.TRANSACTION_RECORDS],
            "semantic_status": "evidence",
            "summary": summary}


def _records_summary(n, declared=None, rows=None, complete=None):
    return {"record_count": declared if declared is not None else n,
            "record_rows": rows if rows is not None else [
                {"record_id": f"T{i:03d}", "timestamp": "t",
                 "summary": "SELL trade", "address": None}
                for i in range(1, min(n, 20) + 1)],
            "record_ids": [f"T{i:03d}" for i in range(1, min(n, 20) + 1)],
            "streams": {"withdrawals_included": False,
                        "transactions_included": True},
            "view": "evidence",
            "truncated": False,
            **({"complete": complete} if complete is not None else {})}


# --- A: file_export completion judgment (D1) ----------------------------

def _file_export_case(tmp_path, summary, with_artifact=True,
                      parity=None, artifact_failed=False):
    """Associated records obs + (optional) associated artifact obs.

    parity=None → the tool-layer parity check NEVER ran (no
    record_parity_checked/failed fields on the artifact summary);
    parity=True/False → checked with that outcome."""
    artifact = {"artifact_id": "ART-x", "title": "Trade records",
                "scope": "account:U00047"}
    summary = dict(summary)
    if parity is not None:
        summary["record_parity_failed"] = parity
    obs = [_obs(summary)]
    if with_artifact and not artifact_failed:
        a_summary = {"artifact_id": "ART-x", "title": "Trade records",
                     "scope": "account:U00047",
                     "source_step_ids": ["S4-F1"],
                     "source_streams": ["transaction_records"]}
        if parity is not None:
            a_summary["record_parity_checked"] = True
            a_summary["record_parity_failed"] = bool(parity)
        obs.append({"step_type": "generate_artifact",
                    "tool_name": "artifact_bundle", "outcome": "success",
                    "for_outputs": ["O2"],
                    "actual_evidence_types": [ET.TRANSACTION_RECORDS],
                    "semantic_status": "artifact",
                    "summary": a_summary})
    if artifact_failed:
        obs.append({"step_type": "generate_artifact",
                    "tool_name": "artifact_bundle",
                    "outcome": "execution_error", "for_outputs": [],
                    "summary": {}})
    return obs


def test_a1_file_complete_verified_satisfied(tmp_path):
    out = _out(delivery=DELIVERY_FILE)
    obs = _file_export_case(tmp_path, _records_summary(66), parity=False)
    r = evaluate_output(out, "U00047", obs)
    assert r.satisfaction == OutputSatisfaction.SATISFIED
    assert r.reason_code == "records_file_delivered_complete"


def test_a2_file_parity_failed_not_satisfied(tmp_path):
    out = _out(delivery=DELIVERY_FILE)
    obs = _file_export_case(tmp_path, _records_summary(66), parity=True)
    r = evaluate_output(out, "U00047", obs)
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


def test_a3_file_parity_unknown_not_satisfied(tmp_path):
    """Artifact generated but the tool-layer parity check did not run —
    completeness is unverified, never treated as verified."""
    out = _out(delivery=DELIVERY_FILE)
    obs = _file_export_case(tmp_path, _records_summary(66), parity=None)
    r = evaluate_output(out, "U00047", obs)
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


def test_a4_file_not_generated_not_satisfied(tmp_path):
    out = _out(delivery=DELIVERY_FILE)
    obs = [_obs(_records_summary(66))]          # records only, no artifact
    r = evaluate_output(out, "U00047", obs)
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


def test_a4_artifact_execution_failure_not_satisfied(tmp_path):
    out = _out(delivery=DELIVERY_FILE)
    obs = _file_export_case(tmp_path, _records_summary(66),
                            artifact_failed=True)
    r = evaluate_output(out, "U00047", obs)
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


def test_a5_file_complete_across_revision_pending_step(tmp_path):
    """The audited U00047 shape: the artifact was generated in an earlier
    revision and the FINAL revision's generate_artifact step is pending —
    the file evidence (associated artifact observation) still governs."""
    out = _out(delivery=DELIVERY_FILE)
    obs = _file_export_case(tmp_path, _records_summary(66), parity=False)
    obs.append({"step_type": "generate_artifact",
                "tool_name": "artifact_bundle", "outcome": "success",
                "for_outputs": [],                      # rev1: unassociated
                "semantic_status": "artifact",
                "summary": {"artifact_id": "ART-old",
                            "source_streams":
                                ["transaction_records"]}})
    # the FINAL revision's S5 pending step must not gate the verdict —
    # plan steps are not visible to the postcondition at all
    r = evaluate_output(out, "U00047", obs)
    assert r.satisfaction == OutputSatisfaction.SATISFIED


def test_a6_record_count_equal_but_parity_unverified(tmp_path):
    """Same count claimed and rendered, but the tool-layer parity check
    did not run — completeness is unverified, not assumed."""
    summary = _records_summary(66)
    summary["record_rows"] = [
        {"record_id": f"T{i:03d}", "timestamp": "t", "summary": "r",
         "address": None} for i in range(1, 21)]
    out = _out(delivery=DELIVERY_FILE)
    obs = _file_export_case(tmp_path, summary, parity=None)
    r = evaluate_output(out, "U00047", obs)
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED


# --- B: detail_list legacy semantics unchanged ---------------------------

def test_b7_detail_list_within_cap_satisfied():
    out = _out(delivery="detail_list")
    r = evaluate_output(out, "U00047", [
        _obs(_records_summary(8))])
    assert r.satisfaction == OutputSatisfaction.SATISFIED
    assert r.reason_code == "records_retrieved_complete"


def test_b8_detail_list_over_cap_still_disclosed_partial():
    out = _out(delivery="detail_list")
    r = evaluate_output(out, "U00047", [
        _obs(_records_summary(66))])
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED
    assert r.reason_code == "records_cap_partial"


def test_b9_detail_list_evidence_missing_unresolved():
    s = _records_summary(5)
    s["evidence_missing"] = True
    out = _out(delivery="detail_list")
    r = evaluate_output(out, "U00047", [_obs(s)])
    assert r.satisfaction == OutputSatisfaction.UNRESOLVED


def test_b_legacy_missing_delivery_defaults_to_detail_list():
    """An output WITHOUT a delivery field (old persisted contracts) keeps
    the detail_list chat-parity semantics — never guessed file-only."""
    out = _out(delivery=None)                    # validator default
    assert out.delivery == "detail_list"
    r = evaluate_output(out, "U00047", [_obs(_records_summary(66))])
    assert r.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED
    assert r.reason_code == "records_cap_partial"


# --- C: dual delivery (two outputs, independently judged) ----------------

def _dual_outputs():
    return [_out(output_id="O2", delivery="detail_list"),
            _out(output_id="O3", delivery=DELIVERY_FILE)]


def test_c10_dual_both_satisfied(tmp_path):
    o_d, o_f = _dual_outputs()
    records = _obs(_records_summary(8))
    artifact = {"step_type": "generate_artifact",
                "tool_name": "artifact_bundle", "outcome": "success",
                "for_outputs": ["O3"],
                "actual_evidence_types": [ET.TRANSACTION_RECORDS],
                "semantic_status": "artifact",
                "summary": {"artifact_id": "ART-x",
                            "record_parity_checked": True,
                            "record_parity_failed": False,
                            "source_streams": ["transaction_records"]}}
    obs = [dict(records, for_outputs=["O2"]), artifact]
    r_d = evaluate_output(o_d, "U00047", obs)
    r_f = evaluate_output(o_f, "U00047", obs)
    assert r_d.satisfaction == OutputSatisfaction.SATISFIED
    assert r_f.satisfaction == OutputSatisfaction.SATISFIED


def test_c11_file_ok_dialogue_partial_task_not_complete(tmp_path):
    o_d, o_f = _dual_outputs()
    records = _obs(_records_summary(66))          # > chat cap
    artifact = {"step_type": "generate_artifact",
                "tool_name": "artifact_bundle", "outcome": "success",
                "for_outputs": ["O3"],
                "actual_evidence_types": [ET.TRANSACTION_RECORDS],
                "semantic_status": "artifact",
                "summary": {"artifact_id": "ART-x",
                            "record_parity_checked": True,
                            "record_parity_failed": False,
                            "source_streams": ["transaction_records"]}}
    obs = [dict(records, for_outputs=["O2"]), artifact]
    r_d = evaluate_output(o_d, "U00047", obs)
    r_f = evaluate_output(o_f, "U00047", obs)
    assert r_f.satisfaction == OutputSatisfaction.SATISFIED
    assert r_d.satisfaction == OutputSatisfaction.PARTIALLY_SUPPORTED
    from app.goal_postconditions import derive_goal_outcome
    outcome = derive_goal_outcome([r_d.satisfaction, r_f.satisfaction],
                                  "completed", intent_source="llm")
    assert outcome.value == "partial"            # never "complete"


def test_c12_dialogue_ok_file_missing(tmp_path):
    o_d = _out(output_id="O2", delivery="detail_list")
    o_f = _out(output_id="O3", delivery=DELIVERY_FILE)
    records = _obs(_records_summary(8))
    obs = [dict(records, for_outputs=["O2"])]    # no artifact obs at all
    r_d = evaluate_output(o_d, "U00047", obs)
    r_f = evaluate_output(o_f, "U00047", obs)
    assert r_d.satisfaction == OutputSatisfaction.SATISFIED
    # zero associated evidence for O3 → existing PENDING semantics
    # (never SATISFIED, never completing the goal)
    assert r_f.satisfaction != OutputSatisfaction.SATISFIED


def test_c13_no_evidence_borrowing_between_outputs(tmp_path):
    """Each output is judged from ITS OWN associated observations only —
    the file_export output with no associated artifact evidence is not
    rescued by the detail_list sibling's success."""
    o_d = _out(output_id="O2", delivery="detail_list")
    o_f = _out(output_id="O3", delivery=DELIVERY_FILE)
    records = {"step_type": "inspect_transactions",
               "tool_name": "finding_drilldown", "outcome": "success",
               "for_outputs": ["O2"],
               "actual_evidence_types": [ET.TRANSACTION_RECORDS],
               "semantic_status": "evidence",
               "summary": _records_summary(8)}
    obs = [records]
    r_f = evaluate_output(o_f, "U00047", obs)
    # zero associated evidence for O3 → existing PENDING semantics:
    # never SATISFIED, and the detail_list sibling's success does not
    # rescue it (no evidence borrowing between outputs)
    assert r_f.satisfaction != OutputSatisfaction.SATISFIED
    assert r_f.satisfaction == OutputSatisfaction.PENDING


# --- D: composer rendering (D2/D3) ----------------------------------------

def _render(out, summary, lang="en"):
    from app.agent_loop import _gsp_render_output_section
    return _gsp_render_output_section(
        out, summary, goal_text="g", lang=lang,
        artifact_backed_families=set())


def test_d2_partial_records_render_rows_and_disclosure():
    out = _out(delivery="file_export")
    s = _records_summary(66)
    s["_outcome"] = "success"
    lines = _render(out, s)
    blob = "\n".join(lines)
    assert "T001" in blob                        # real delivered rows
    assert ("first 10" in blob or "10" in blob)  # shown/total disclosure
    assert "boundary notes above" not in blob    # D3: dangling ref gone
    # concrete delivery: rows + the shown/total disclosure replace the
    # bare vague line
    assert "the complete set is in the evidence result" in blob


def test_d3_no_dangling_reference_in_zh_partial():
    out = _out(delivery="file_export")
    s = _records_summary(66)
    s["_outcome"] = "success"
    lines = _render(out, s, lang="zh")
    blob = "\n".join(lines)
    assert "boundary notes" not in blob
    assert "上方边界说明" not in blob
    assert "前 10 条" in blob or "T001" in blob

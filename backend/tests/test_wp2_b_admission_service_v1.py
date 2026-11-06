"""WP-2 Version B S2 — unit tests for the shared deterministic delivery
admission service (app/delivery_admission.py).

Scope: the S2 subset of the frozen test lists (Final Rulings 1-2 + the
admission fail-closed matrix). The FULL acceptance matrices remain S4;
API E2E remains S5. Every test pins the behavior that the frozen design
mandates; a candidate fixture with NaN demonstrates the non-finite
fail-closed path.

Feature flags: the service is flag-independent by design (pure
deterministic function); no flag is consulted, so none is pinned here
(recorded explicitly per the S1 D4 discipline).
"""

import pytest

from app.delivery_admission import (
    admit_delivery_targets,
    canonical_records_digest,
    enumerate_delivery_targets,
    extract_explicit_set_refs,
    resolve_binding,
)


def mk_candidate(cid="CATA-001", a="ACC-A", b="ACC-B",
                 t1="TX-1", t2="TX-2", value=100.0):
    def leg(tid, side):
        return {"transaction_id": tid, "account_id": a, "side": side,
                "quantity": 1.0, "value": value, "symbol": "BTC",
                "timestamp": "2026-08-19T10:00:00+00:00"}
    return {"candidate_id": cid, "rank": 1, "account_a": a,
            "account_b": b, "symbol": "BTC",
            "supporting": [leg(t1, "BUY"), leg(t2, "SELL")]}


def mk_entry(set_ref="CS-ab12cd34-S9", task="TASK-1",
             candidates=None):
    return {"set_ref": set_ref, "source_task_id": task,
            "source_step_id": "S9", "created_at": "2026-10-31T00:00:00Z",
            "bounded_data": False, "origin": "analysis",
            "candidates": candidates if candidates is not None
            else [mk_candidate()]}


PLAN = [{"step_id": "S2", "type": "generate_artifact"},
        {"step_id": "S3", "type": "fetch_case"}]


# --- canonical digest -----------------------------------------------------

class TestCanonicalDigest:
    def test_key_order_irrelevant(self):
        assert canonical_records_digest([{"a": 1, "b": 2}]) == \
            canonical_records_digest([{"b": 2, "a": 1}])

    def test_unicode_stable_escaped(self):
        d1 = canonical_records_digest([{"k": "交付"}])
        d2 = canonical_records_digest([{"k": "交付"}])
        assert d1 == d2 and len(d1) == 64

    def test_null_vs_missing_differ(self):
        assert canonical_records_digest([{"a": None}]) != \
            canonical_records_digest([{}])

    def test_int_vs_float_differ(self):
        assert canonical_records_digest([{"v": 1}]) != \
            canonical_records_digest([{"v": 1.0}])

    def test_list_order_is_content(self):
        assert canonical_records_digest([[1, 2]]) != \
            canonical_records_digest([[2, 1]])

    def test_unknown_fields_included(self):
        assert canonical_records_digest([{"a": 1}]) != \
            canonical_records_digest([{"a": 1, "extra": True}])

    def test_non_finite_raises(self):
        with pytest.raises(ValueError):
            canonical_records_digest([{"v": float("nan")}])

    def test_post_clean_drop_detected(self):
        """The [A, B, junk] cleaning hole: rendering only A+B while the
        injection carried a third item MUST yield a different digest."""
        full = [mk_candidate()]
        full[0]["supporting"] = full[0]["supporting"] + ["junk"]
        rendered = [dict(full[0], supporting=full[0]["supporting"][:2])]
        assert canonical_records_digest(full) != \
            canonical_records_digest(rendered)


# --- explicit refs ----------------------------------------------------------

class TestExtractRefs:
    def test_findall_multi_dedupe_order(self):
        text = "导出 CS-ab12cd34-S9 和 CS-ef56gh78-S3，再对照 CS-ab12cd34-S9"
        assert extract_explicit_set_refs(text) == \
            ["CS-ab12cd34-S9", "CS-ef56gh78-S3"]

    def test_no_refs(self):
        assert extract_explicit_set_refs("导出全部交易明细") == []


# --- target enumeration & pairing (ruling 1) --------------------------------

class TestEnumerateTargets:
    def test_one_step_one_ref_binds_identity(self):
        targets = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-ab12cd34-S9"])
        assert len(targets) == 1
        assert targets[0].delivery_target_id == "DT-S2-1"
        assert targets[0].set_ref == "CS-ab12cd34-S9"

    def test_no_ref_no_autobind(self):
        targets = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}], [])
        assert targets[0].set_ref is None
        assert targets[0].invalid_reason is None  # resolved later

    def test_one_ref_two_steps_ambiguous_both(self):
        targets = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"},
             {"step_id": "S3", "type": "generate_artifact"}],
            ["CS-ab12cd34-S9"])
        assert all(t.invalid_reason == "ambiguous_mapping"
                   for t in targets)

    def test_two_refs_one_step_ambiguous(self):
        targets = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-1", "CS-2"])
        assert targets[0].invalid_reason == "ambiguous_mapping"

    def test_multi_target_step_unsupported(self):
        step = {"step_id": "S2", "type": "generate_artifact",
                "arguments": {"delivery_targets": [
                    {"delivery_target_id": "DT-S2-1"},
                    {"delivery_target_id": "DT-S2-2"}]}}
        targets = enumerate_delivery_targets([step], [])
        assert targets[0].invalid_reason == "multi_target_step_unsupported"


# --- binding resolution (fail-closed matrix) ---------------------------------

class TestResolveBinding:
    def test_exact_match_binds(self):
        t = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-ab12cd34-S9"])[0]
        resolve_binding(t, [mk_entry()], {"TASK-1"})
        assert t.invalid_reason is None
        assert t.binding["kind"] == "candidate_set_export"
        assert t.binding["status"] == "bound_ready"
        assert t.binding["set_ref"] == "CS-ab12cd34-S9"
        assert t.binding["delivery_target_id"] == "DT-S2-1"

    def test_no_explicit_ref_never_autobinds_even_single_set(self):
        t = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}], [])[0]
        resolve_binding(t, [mk_entry()], {"TASK-1"})
        assert t.invalid_reason == "no_explicit_ref"
        assert t.binding is None

    def test_missing_ref(self):
        t = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-nope"])[0]
        resolve_binding(t, [mk_entry()], {"TASK-1"})
        assert t.invalid_reason == "ref_not_in_session"

    def test_duplicate_set_ref(self):
        t = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-ab12cd34-S9"])[0]
        resolve_binding(t, [mk_entry(), mk_entry()], {"TASK-1"})
        assert t.invalid_reason == "duplicate_set_ref"

    def test_cross_investigation(self):
        t = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-ab12cd34-S9"])[0]
        resolve_binding(t, [mk_entry()], {"TASK-OTHER"})
        assert t.invalid_reason == "cross_investigation"

    def test_empty_set(self):
        t = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-ab12cd34-S9"])[0]
        resolve_binding(t, [mk_entry(candidates=[])], {"TASK-1"})
        assert t.invalid_reason == "empty_set"

    def test_malformed_leg(self):
        bad = mk_candidate()
        bad["supporting"][0] = "junk"
        t = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-ab12cd34-S9"])[0]
        resolve_binding(t, [mk_entry(candidates=[bad])], {"TASK-1"})
        assert t.invalid_reason == "malformed_entry"

    def test_per_target_independence(self):
        """Ruling 3 mechanics: two targets judged independently — one
        resolves, one fails, neither affects the other's verdict."""
        t_ok = enumerate_delivery_targets(
            [{"step_id": "S2", "type": "generate_artifact"}],
            ["CS-ok"])[0]
        t_bad = enumerate_delivery_targets(
            [{"step_id": "S9", "type": "generate_artifact"}],
            ["CS-missing"])[0]
        sets = [mk_entry(set_ref="CS-ok")]
        resolve_binding(t_ok, sets, {"TASK-1"})
        resolve_binding(t_bad, sets, {"TASK-1"})
        assert t_ok.binding is not None
        assert t_bad.invalid_reason == "ref_not_in_session"


# --- admission pipeline -------------------------------------------------------

class TestAdmit:
    def test_passthrough_contract_path(self):
        r = admit_delivery_targets(
            goal_contract=object(), decision_outcome="contract_accepted",
            request_text="导出 CS-x", plan_steps=PLAN, session_sets=[],
            investigation_task_ids=set(), case_id="U00010")
        assert r == {"mode": "passthrough", "reason": "contract_path"}

    def test_passthrough_not_hsg(self):
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="llm_empty",
            request_text="导出 CS-x", plan_steps=PLAN, session_sets=[],
            investigation_task_ids=set(), case_id="U00010")
        assert r["reason"] == "not_hsg"

    def test_passthrough_no_delivery_target(self):
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="heuristic_single_goal",
            request_text="导出 CS-x",
            plan_steps=[{"step_id": "S1", "type": "fetch_case"}],
            session_sets=[], investigation_task_ids=set(),
            case_id="U00010")
        assert r["reason"] == "no_delivery_target"

    def test_passthrough_intake_intent_exempt(self):
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="heuristic_single_goal",
            request_text="调查 U00010 的情况",
            plan_steps=[{"step_id": "S1", "type": "fetch_case"},
                        {"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[], investigation_task_ids=set(),
            case_id="U00010")
        assert r["reason"] == "intent_not_artifact"

    def test_admitted_happy_path_payload_complete(self):
        entry = mk_entry()
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="heuristic_single_goal",
            request_text="导出 CS-ab12cd34-S9 的候选交易明细文件",
            plan_steps=[{"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[entry], investigation_task_ids={"TASK-1"},
            case_id="U00010")
        assert r["mode"] == "admitted"
        assert len(r["valid"]) == 1 and r["invalid"] == []
        inj = r["valid"][0]
        assert inj["delivery_target_id"] == "DT-S2-1"
        assert inj["source_kind"] == "candidate_set"
        assert inj["set_ref"] == "CS-ab12cd34-S9"
        assert inj["candidate_records"] is entry["candidates"]
        assert inj["expected_records_digest"] == \
            canonical_records_digest(entry["candidates"])
        assert inj["expected_binding"]["delivery_target_id"] == "DT-S2-1"
        assert inj["expected_binding"]["source_step_id"] == "S9"
        assert inj["expected_binding"]["kind"] == "candidate_set_export"
        assert inj["expected_binding"]["case_id"] == "U00010"

    def test_invalid_target_fail_closed_reason(self):
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="heuristic_single_goal",
            request_text="导出 CS-missing 的候选交易明细文件",
            plan_steps=[{"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[mk_entry()], investigation_task_ids={"TASK-1"},
            case_id="U00010")
        assert r["mode"] == "admitted"
        assert r["valid"] == []
        assert r["invalid"][0]["reason"] == "ref_not_in_session"

    def test_non_finite_records_fail_closed(self):
        bad = mk_candidate()
        bad["supporting"][0]["value"] = float("nan")
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="heuristic_single_goal",
            request_text="导出 CS-ab12cd34-S9 的候选交易明细文件",
            plan_steps=[{"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[mk_entry(candidates=[bad])],
            investigation_task_ids={"TASK-1"}, case_id="U00010")
        assert r["invalid"][0]["reason"] == "non_finite_record_values"

    def test_no_ref_artifact_ask_fails_closed(self):
        """HSG + artifact ask WITHOUT an explicit CS ref: fail closed
        even when the session holds exactly one set (no auto-bind)."""
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="heuristic_single_goal",
            request_text=ASK_PLAIN,
            plan_steps=[{"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[mk_entry()], investigation_task_ids={"TASK-1"},
            case_id="U00010")
        assert r["mode"] == "admitted"
        assert r["valid"] == []
        assert r["invalid"][0]["reason"] == "no_explicit_ref"

    def test_hsg_only_default_skips_undecided(self):
        """/turn anti-bypass (S3): hsg_only=True (loop default) with NO
        decision record passes through — the loop path never relies on
        it; /turn calls with hsg_only=False instead."""
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome=None,
            request_text=ASK_PLAIN,
            plan_steps=[{"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[mk_entry()], investigation_task_ids={"TASK-1"},
            case_id="U00010")
        assert r == {"mode": "passthrough", "reason": "not_hsg"}

    def test_turn_mode_gates_undecided_deliveries(self):
        """/turn (hsg_only=False): a no-decision artifact delivery IS
        gated — no explicit ref fails closed even with a seeded set."""
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome=None,
            request_text=ASK_PLAIN,
            plan_steps=[{"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[mk_entry()], investigation_task_ids={"TASK-1"},
            case_id="U00010", hsg_only=False)
        assert r["mode"] == "admitted"
        assert r["invalid"][0]["reason"] == "no_explicit_ref"

    def test_turn_mode_records_non_hsg_decision_still_passthrough(self):
        r = admit_delivery_targets(
            goal_contract=None, decision_outcome="llm_empty",
            request_text=ASK_PLAIN,
            plan_steps=[{"step_id": "S2",
                         "type": "generate_artifact"}],
            session_sets=[mk_entry()], investigation_task_ids={"TASK-1"},
            case_id="U00010", hsg_only=False)
        assert r == {"mode": "passthrough", "reason": "not_hsg"}


ASK_PLAIN = "generate all trade records of this account as an artifact"

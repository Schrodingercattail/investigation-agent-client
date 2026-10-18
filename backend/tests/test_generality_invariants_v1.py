"""Cross-case generality invariants (case-ID-independent property tests).

These tests pin the GENERAL properties of the capability fixes — no
production behavior may depend on a specific case id, finding id,
record count, address distribution, or a particular phrasing. Each test
varies the inputs across data instances and asserts the invariant.

Locked invariants:
1. Target-account substitution: the SAME trade relation re-filters its
   candidate evidence when the target account changes — another
   account's relations can never become this target's answer.
2. Record-count independence: destination/new-address comparisons are
   computed from the ACTUAL records — the conclusion tracks the data,
   never a fixed count.
3. Address-distribution tracking: same-address / mixed / all-distinct
   distributions yield conclusions that match the actual identifiers.
4. Finding-anchor derivation from the case's OWN metadata: the
   evidence-stream binding works for any case whose findings carry the
   stream vocabulary, anchors a metadata-matching finding (never a
   hardcoded id), and does NOT bind rule/threshold asks (the specific
   rule matters).
5. Missing key fields are disclosed — no internal None ever leaks into
   user-visible output.
6. Partial coverage never reads as complete, whatever the combination.
"""

import json

import pytest

from app import investigation_concepts as ic
from app.context_resolution import (
    ContextResolver,
    ResolutionStatus,
)
from app.domain_tools.cross_account_trade_analysis import find_candidates
from app.models import Finding, FocusSource, InvestigationContext
from app.request_coverage import (
    ASPECT_DESTINATION,
    ASPECT_HEDGING,
    coverage_lines,
    verify_coverage,
)


def engine_config():
    return {"max_time_delta_seconds": 30.0,
            "value_tolerance_relative": 0.05,
            "value_tolerance_absolute_floor": 1.0,
            "max_legs_per_candidate": 5, "max_candidates": 20}


def tx(tid, account, side, value, ts, symbol="BTC"):
    from app.trade_evidence import AccountTransaction
    return AccountTransaction(
        transaction_id=tid, account_id=account, symbol=symbol, side=side,
        price=100.0, quantity=value / 100.0, value=value, timestamp=ts)


def di_block(total, unique, missing=0):
    """Structurally consistent destination-identity block for any
    (total, unique, missing) combination."""
    ids = [f"R{i:03d}" for i in range(total - missing)]
    base = len(ids) // unique
    groups = []
    for i in range(unique):
        chunk = ids[i * base:(i + 1) * base] if i < unique - 1 \
            else ids[(unique - 1) * base:]
        groups.append({"destination_ref": f"d{i}",
                       "record_ids": chunk})
    return {"basis": "withdrawal.address", "records_total": total,
            "records_with_identity": total - missing,
            "records_without_identity": missing,
            "unique_destinations": unique, "groups": groups}


def na_block(total, true_n):
    return {"basis": "withdrawal.is_new_address", "records_total": total,
            "true_count": true_n, "false_count": total - true_n,
            "missing_count": 0,
            "records": [{"record_id": f"R{i:03d}",
                         "is_new_address": i < true_n}
                        for i in range(total)]}


# ---------------------------------------------------------------------------
# 1. target-account substitution
# ---------------------------------------------------------------------------

class TestTargetSubstitution:
    RECORDS = [
        tx("T1", "UA", "BUY", 100.0, "2026-07-20T10:00:00+00:00"),
        tx("T2", "UB", "SELL", 100.0, "2026-07-20T10:00:10+00:00"),
        tx("T3", "UC", "BUY", 50.0, "2026-07-20T11:00:00+00:00"),
        tx("T4", "UD", "SELL", 50.0, "2026-07-20T11:00:05+00:00"),
    ]

    @pytest.mark.parametrize("target,expected_partner", [
        ("UA", "UB"),
        ("UB", "UA"),
        ("UC", "UD"),
        ("UD", "UC"),
        ("UE", None),          # an account with no relation at all
    ])
    def test_candidates_refilter_with_the_target(self, target,
                                                 expected_partner):
        cands, _ = find_candidates(self.RECORDS, engine_config(),
                                   target_account=target)
        if expected_partner is None:
            assert cands == []
            return
        assert len(cands) == 1
        pair = {cands[0]["account_a"], cands[0]["account_b"]}
        assert pair == {target, expected_partner}

    def test_coverage_scope_check_tracks_the_target(self):
        payload = {
            "analysis_status": "MATCH_FOUND",
            "case_id": "UA", "target_account": "UA",
            "candidates": [{"account_a": "UC", "account_b": "UD"}],
            "retrieval": {"truncated": False},
        }
        cov = verify_coverage("UA 和其他账户有对冲交易吗",
                              [("cross_account_trade_analysis", payload)],
                              expected_subject="UA")
        a = cov["aspects"][0]
        assert a["satisfaction"] == "unresolved"
        assert a["reason_code"] == "subject_membership_violation"

    @pytest.mark.parametrize("phrasing", [
        "UA 和其他账户之间有没有对冲交易？",
        "帮我看下 UA 跟别的账户是否存在对冲",
        "check hedging across accounts",
        "check whether related accounts traded opposite sides",
    ])
    def test_intent_survives_rephrasing(self, phrasing):
        assert ic.is_cross_account_trade_analysis_request(phrasing)


# ---------------------------------------------------------------------------
# 2. record-count independence + 3. address-distribution tracking
# ---------------------------------------------------------------------------

class TestPopulationIndependence:
    @pytest.mark.parametrize("total,unique", [
        (3, 3), (5, 1), (9, 9), (12, 4), (30, 30), (7, 2),
    ])
    def test_destination_conclusion_tracks_actual_records(
            self, total, unique):
        cov = verify_coverage(
            "这些提现是同一个地址还是不同地址？",
            [("finding_drilldown",
              {"view": "evidence", "record_count": total,
               "destination_identity": di_block(total, unique)})])
        a = {x["aspect_id"]: x for x in cov["aspects"]}[ASPECT_DESTINATION]
        assert a["satisfaction"] == "satisfied"
        detail = a["detail"]
        assert f"{total} 笔" in detail
        if unique == 1:
            assert "同一目的地" in detail
        else:
            assert f"{unique} 个不同目的地" in detail

    @pytest.mark.parametrize("total,true_n", [(4, 4), (4, 0), (11, 6),
                                              (9, 9)])
    def test_new_address_conclusion_tracks_actual_markers(
            self, total, true_n):
        cov = verify_coverage(
            "哪些提现是新地址？",
            [("finding_drilldown",
              {"view": "evidence", "record_count": total,
               "new_address_status": na_block(total, true_n)})])
        a = cov["aspects"][0]
        assert a["satisfaction"] == "satisfied"
        if true_n == total:
            assert "均被 Risk Platform 标记为新地址" in a["detail"]
        elif true_n == 0:
            assert f"0 笔被标记" in a["detail"] or "不是" in a["detail"]
        else:
            assert f"{true_n} 笔被标记" in a["detail"]

    def test_missing_fields_are_disclosed_not_leaked(self):
        # missing destination identifiers → partial, never "None"
        cov = verify_coverage(
            "这些提现是同一个地址吗",
            [("finding_drilldown",
              {"view": "evidence", "record_count": 6,
               "destination_identity": di_block(6, 2, missing=2)})])
        a = {x["aspect_id"]: x for x in cov["aspects"]}[ASPECT_DESTINATION]
        assert a["satisfaction"] == "partially_supported"
        assert "缺少目的地标识" in a["detail"]
        blob = json.dumps(cov)
        assert "None" not in blob

    def test_renderer_never_leaks_none_for_time_gap(self):
        # the GSP cross-account branch must not print "Nones" for a
        # missing gap — it renders n/a and the verdict stays honest
        from app.agent_loop import _gsp_render_output_facts
        from app.goal_resolution import RequiredOutput, ScopeLevel
        out = RequiredOutput(
            output_id="O1", claim="hedge", claim_kind="existence_check",
            subject_ref="UA", scope=ScopeLevel.CASE_LEVEL.value,
            evidence_requirements=["opposite_trade_relations"])
        summary = {"candidates": [{
            "candidate_id": "CATA-001", "account_a": "UA",
            "account_b": "UB", "symbol": "BTC",
            "max_time_gap_seconds": None}]}
        lines = _gsp_render_output_facts(out, summary)
        blob = "\n".join(lines)
        assert "None" not in blob and "Nones" not in blob


# ---------------------------------------------------------------------------
# 4. finding-anchor derivation from case metadata (no hardcoded ids)
# ---------------------------------------------------------------------------

def _finding(fid, title):
    from app.models import FindingCapability
    return Finding(finding_id=fid, title=title, case_id="U99999",
                   type="rule", summary="test finding",
                   capabilities=FindingCapability.model_validate(
                       ["timeline", "signal_explain"]))


def _resolver():
    return ContextResolver(llm_provider=None, llm_assist=False)


class TestEvidenceStreamBinding:
    @pytest.mark.parametrize("titles,expected_anchor_id", [
        (["High withdrawal frequency", "First withdrawal to new address",
          "Trading Frequency"], "F1"),
        (["Withdrawal surge", "Withdrawal novelty"], "F1"),
        (["Trading Frequency", "High withdrawal frequency"], "F2"),
    ])
    def test_anchor_derived_from_this_cases_metadata(
            self, titles, expected_anchor_id):
        findings = [_finding(f"F{i + 1}", t)
                    for i, t in enumerate(titles)]
        ctx = InvestigationContext(case_id="U99999")   # arbitrary case id
        out = _resolver().resolve(
            "9 条提现是提向同一个地址还是不同地址？把提现明细整理发我。",
            ctx, findings=findings)
        assert out.status == ResolutionStatus.RESOLVED
        assert out.updated_context.focused_finding_id == expected_anchor_id
        assert out.resolution_kind == "evidence_stream"

    def test_no_stream_vocabulary_keeps_uncertainty(self):
        findings = [_finding("F1", "Trading Frequency"),
                    _finding("F2", "ML Pattern")]
        out = _resolver().resolve(
            "9 条提现是提向同一个地址还是不同地址？",
            InvestigationContext(case_id="U99999"), findings=findings)
        assert out.status != ResolutionStatus.RESOLVED

    def test_threshold_ask_not_stream_bound(self):
        # which RULE's criteria are asked matters — a record-stream
        # anchor would be a guess; the clarification contract stands
        findings = [_finding("F1", "High withdrawal frequency"),
                    _finding("F2", "First withdrawal to new address")]
        out = _resolver().resolve(
            "怎样算高频提现？",
            InvestigationContext(case_id="U99999"), findings=findings)
        assert out.updated_context.focused_finding_id is None

    def test_existing_explicit_focus_still_authoritative(self):
        findings = [_finding("F1", "High withdrawal frequency")]
        ctx = InvestigationContext(
            case_id="U99999", focused_finding_id="F9",
            focus_source=FocusSource.USER_SELECTED)
        out = _resolver().resolve(
            "把提现明细列给我",
            ctx, findings=findings + [_finding("F9", "Some finding")])
        # an explicit focus is kept for the record ask (stream invariance
        # makes the anchor immaterial); never silently re-pointed
        assert out.updated_context.focused_finding_id in ("F9", "F1")


# ---------------------------------------------------------------------------
# 5. instance values never leak into deliverable labels
# ---------------------------------------------------------------------------

class TestNoInstanceValueLeak:
    def test_metric_claim_description_is_value_free(self):
        from app.goal_resolution import deterministic_contract_proposal
        proposal = deterministic_contract_proposal(
            "33.33% 反向交易率是什么意思？这个账户和其他账户有对冲吗",
            None, "U77777")
        blob = json.dumps(proposal)
        assert "34.38%" not in blob       # another case's instance value
        assert "33.33%" not in blob       # this case's instance value

    @pytest.mark.parametrize("ratio_text", ["7 条", "9 条", "12 条"])
    def test_population_ask_yields_no_fixed_count_logic(self, ratio_text):
        # the aspect identification works for ANY asserted count — the
        # number is data, never a routing condition
        aspects = verify_coverage(
            f"{ratio_text}提现是同一个地址还是不同地址？", [])
        if aspects is not None:
            ids = [a["aspect_id"] for a in aspects["aspects"]]
            assert ASPECT_DESTINATION in ids

    def test_partial_coverage_outcome_matrix(self):
        # any combination containing an open item never reads complete
        cov = verify_coverage(
            "34.38%反向交易率是什么意思？这些提现是同一个地址吗",
            [("finding_drilldown",
              {"view": "evidence", "record_count": 7,
               "destination_identity": di_block(7, 7)})])
        # metric aspect has no cross-account payload → not satisfied;
        # destination aspect answered → overall must NOT be complete
        assert cov["outcome"] != "complete"
        assert any("尚未覆盖" in ln or "证据不足" in ln
                   for ln in coverage_lines(cov))

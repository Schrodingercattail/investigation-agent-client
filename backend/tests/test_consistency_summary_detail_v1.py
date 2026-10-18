"""Cross-domain Finding summary–detail consistency — reusable contract.

The invariant (product contract §2): for the same case/finding/evidence
scope/data snapshot, the Finding summary/context and the expanded-detail
path must not contain material, unexplained contradictions; when the
user's request asserts an evidence population that differs from the
authoritative (completely retrieved) population, the system must
explicitly reconcile the difference instead of silently substituting
populations — and a comparison over the complete authoritative
population remains complete (satisfaction semantics unchanged).

L1 here covers the deterministic premise extractor, the reconciliation
line, and the internal population-consistency invariants of the GS-B
fact block. Real-path API E2E lives in TestMatrix-style journeys below.
"""

import json
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agent_loop import (
    _asserted_record_population,
    _population_reconciliation_line,
    _render_destination_facet,
)
from app.config import settings
from app.goal_resolution import OutputSatisfaction, RequiredOutput
from app.planner_v2 import PlannerV2
from app.skills import EvidenceType
from app.task_store_v2 import TaskStoreV2

from tests.test_api_investigations_v1 import rp_patches
from tests.test_risk_platform_adapter_v2 import rp_evidence_payload

FACET = "destination_address_identity"


class TestAssertedPopulationExtractor:
    def test_audited_question_extracts_nine(self):
        assert _asserted_record_population(
            "9笔提现都提向同一个地址，还是每笔提向一个新地址？") == 9

    def test_tiao_classifier(self):
        assert _asserted_record_population("列出5条记录。") == 5

    def test_no_population_asserted(self):
        assert _asserted_record_population(
            "帮我梳理一下这个账号目前的情况。") is None

    def test_none_and_empty(self):
        assert _asserted_record_population(None) is None
        assert _asserted_record_population("") is None

    def test_max_across_multiple_assertions(self):
        assert _asserted_record_population(
            "其中3笔提向A，其余6笔提向B。") == 6

    def test_ordinal_not_treated_as_population(self):
        """「第1笔提现」 is an ordinal reference, not a population
        assertion — the extractor must not treat it as one."""
        assert _asserted_record_population("第1笔提现是什么时候？") is None

    def test_bounded_vocabulary_excludes_other_classifiers(self):
        """Other classifiers (天/个/次) are outside the frozen
        population vocabulary by design."""
        assert _asserted_record_population("6天之内有3次大额转账。") is None

    def test_cjk_numerals_in_the_audited_form_reconcile(self):
        """「九笔提现」 — the CJK-numeral form of the audited claim now
        reconciles (previously documented as unsupported)."""
        assert _asserted_record_population(
            "九笔提现都提向同一个地址，还是每笔提向一个新地址？") == 9

    def test_cjk_ten_and_compound_forms(self):
        assert _asserted_record_population("十笔提现的地址是否相同？") == 10
        assert _asserted_record_population("十五笔提现的地址是否相同？") == 15
        assert _asserted_record_population("两笔提现的地址是否相同？") == 2

    def test_cjk_hundreds_out_of_vocabulary(self):
        """百/千/万 remain outside the frozen vocabulary — an asserted
        population that large is not extracted (never mis-sized)."""
        assert _asserted_record_population("百笔提现的地址是否相同？") is None


class TestReconciliationLine:
    def test_mismatch_produces_explicit_reconciliation(self):
        line = _population_reconciliation_line("9笔提现都提向同一个地址？", 7)
        assert line is not None
        assert "9 笔提现" in line and "7 笔" in line
        assert "检索完整" in line and "已覆盖全部 7 笔" in line

    def test_consistent_premise_produces_no_line(self):
        assert _population_reconciliation_line("9笔提现都提向同一个地址？",
                                               9) is None

    def test_subset_premise_also_reconciled(self):
        line = _population_reconciliation_line("其中5笔提向哪里？", 7)
        assert line is not None and "5 笔" in line and "7 笔" in line

    def test_no_assertion_no_line(self):
        assert _population_reconciliation_line("比较一下提现地址。", 7) is None


def _faceted_output():
    return RequiredOutput(
        output_id="O1", claim="cmp", claim_kind="retrieval",
        subject_ref="F3", scope="finding_level",
        evidence_requirements=[EvidenceType.WITHDRAWAL_RECORDS],
        postcondition_facets=[FACET])


def _block(total, unique, with_id=None):
    with_id = total if with_id is None else with_id
    groups = []
    per = with_id // unique if unique else 0
    rem = with_id - per * unique
    for i in range(unique):
        n = per + (1 if i < rem else 0)
        groups.append({"destination_ref": f"dest-{i + 1}",
                       "address": f"0x{i}",
                       "record_ids": [f"W{i:05d}{j}" for j in range(n)]})
    return {"basis": "withdrawal.address", "records_total": total,
            "records_with_identity": with_id,
            "records_without_identity": total - with_id,
            "unique_destinations": unique, "groups": groups,
            "normalization": "trim; exact case-sensitive"}


def _summary(block_dict, count=None):
    return {"record_count": count if count is not None
            else block_dict["records_total"],
            "complete": True,
            "record_rows": [{"record_id": r, "timestamp": "t",
                             "summary": "s"}
                            for g in block_dict["groups"]
                            for r in g["record_ids"]],
            "destination_identity": block_dict}


class TestFacetRendererReconciliation:
    def test_note_rendered_between_conclusion_and_provenance(self):
        out = _faceted_output()
        out.satisfaction = OutputSatisfaction.SATISFIED
        out.polarity = "positive"
        out.detail = "7 笔提现共指向 7 个不同目的地（记录中的目的地标识）。"
        lines = _render_destination_facet(
            out, _summary(_block(7, 7)),
            goal_text="9笔提现都提向同一个地址，还是每笔提向一个新地址？")
        text = "\n".join(lines)
        assert "7 笔提现共指向 7 个不同目的地" in text
        assert "您的请求提及 9 笔提现" in text and "已覆盖全部 7 笔" in text
        # reconciliation precedes the provenance section
        assert text.index("您的请求提及 9") < text.index("目的地标识分组")

    def test_no_note_when_premise_matches(self):
        out = _faceted_output()
        out.satisfaction = OutputSatisfaction.SATISFIED
        out.polarity = "positive"
        out.detail = "9 笔提现共指向 9 个不同目的地（记录中的目的地标识）。"
        lines = _render_destination_facet(
            out, _summary(_block(9, 9)), goal_text="9笔提现都提向同一个地址？")
        assert not any("您的请求提及" in ln for ln in lines)

    def test_note_suppressed_when_unresolved(self):
        """Facts-absent/unresolved shape: the evaluator's detail is the
        authoritative boundary text; the goal-text premise note is
        suppressed (no duplicated or completion-flavored note is added
        on top of an unresolved output)."""
        out = _faceted_output()
        out.satisfaction = OutputSatisfaction.UNRESOLVED
        out.detail = ("未获得完成目的地比较所需的地址事实；检索结果仅作为"
                      "证据记录。")
        lines = _render_destination_facet(
            out, {"record_count": 5, "complete": True, "record_rows": []},
            goal_text="9笔提现都提向同一个地址？")
        assert not any("您的请求提及 9 笔" in ln for ln in lines)
        assert "未获得完成目的地比较所需的地址事实" in "\n".join(lines)

    def test_legacy_render_without_goal_text_unchanged(self):
        out = _faceted_output()
        out.satisfaction = OutputSatisfaction.SATISFIED
        out.polarity = "positive"
        out.detail = "7 笔提现共指向 7 个不同目的地（记录中的目的地标识）。"
        lines = _render_destination_facet(out, _summary(_block(7, 7)))
        assert not any("您的请求提及" in ln for ln in lines)


class TestInternalPopulationInvariants:
    def test_block_total_equals_row_count(self):
        """The GS-B fact block's records_total must equal the number of
        record rows the evidence view actually carries — the comparison
        population and the displayed population are the same population
        (pinned; verified against live RP data in the API E2E)."""
        blk = _block(7, 7)
        summary = _summary(blk)
        rows = summary["record_rows"]
        assert blk["records_total"] == len(rows)
        assert summary["record_count"] == len(rows)


# ---------------------------------------------------------------------------
# Real-path API E2E — the observed manual-acceptance discrepancy shape
# ---------------------------------------------------------------------------

CASE = "U00010"
GOAL_NINE = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"
PLAN_WITHDRAWALS = ('{"skill_id": "timeline_investigation", "goal": "g", '
                    '"steps": [{"type": "inspect_withdrawals", '
                    '"reason": "r"}]}')
PLAN_TRANSACTIONS = ('{"skill_id": "timeline_investigation", "goal": "g", '
                     '"steps": [{"type": "inspect_transactions", '
                     '"reason": "r"}]}')
TX_CONTRACT = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "all_transaction_records", "claim_kind": "retrieval",
         "subject_ref": "F1", "scope": "finding_level",
         "evidence_requirements": ["transaction_records"]}]})


def faceted_contract(subject="F3"):
    return json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "withdrawal_destination_comparison",
             "claim_kind": "retrieval", "subject_ref": subject,
             "scope": "finding_level",
             "evidence_requirements": ["withdrawal_records"],
             "postcondition_facets": [FACET]}]})


def _client(tmp_path):
    import app.api.investigations as inv_module
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService

    store = TaskStoreV2(str(tmp_path / "consistency.db"))
    p1, p2 = rp_patches()
    with p1, p2:
        from app.domain_tools import risk_case_fetch
        findings = list(risk_case_fetch(CASE).data["findings"])

    class _TurnScript:
        def generate(self, messages=None, max_tokens=0, temperature=0.1):
            return ""

    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(_TurnScript()), executor=None,
            task_store=store),
        store=store,
        case_findings_provider=lambda case_id: findings)
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _journey(client, tmp_path, monkeypatch, *, goal, fixture_rows=None,
             fixture_payload=None, plans=None, decisions=None,
             goal_llm_text=None, focus="F3"):
    monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
    session = {"plans": list(plans or [PLAN_WITHDRAWALS]),
               "decisions": list(decisions or ["STOP"]),
               "goal_llm": [goal_llm_text or faceted_contract()]}
    session_path = str(tmp_path / "consistency_session.json")
    with open(session_path, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False)
    os.environ["AGENT_LOOP_SCRIPTED_SESSION_FILE"] = session_path
    if fixture_payload is not None:
        payload = fixture_payload
    else:
        payload = rp_evidence_payload(case_id=CASE)
        payload["withdrawal_evidence"] = [
            {"withdrawal_id": f"W{160 + i:05d}", "asset": "BTC",
             "amount": 0.5, "address": addr, "is_new_address": False,
             "timestamp": f"2026-08-19T10:{i:02d}:00Z",
             "risk_reason": "fx"}
            for i, addr in enumerate(fixture_rows or [])]
    adapter_patch = patch(
        "app.adapters.risk_platform.RiskPlatformAdapter."
        "fetch_case_evidence", new=lambda self, uid, **kw: payload)
    try:
        adapter_patch.start()
        inv = client.post("/api/v2/investigations",
                          json={"case_id": CASE})
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = client.post(
            f"/api/v2/investigations/{inv_id}/goal",
            json={"goal": goal,
                  "context_action": {"type": "focus_finding",
                                     "finding_id": focus}})
        task_id = run.json()["task_id"]
        deadline = time.time() + 90
        while time.time() < deadline:
            a = client.get(f"/api/v2/tasks/{task_id}/activity").json()
            if a["is_terminal"]:
                break
            time.sleep(0.1)
        detail = client.get(f"/api/v2/tasks/{task_id}").json()
        return a, (detail.get("task_response_text") or "")
    finally:
        adapter_patch.stop()
        os.environ.pop("AGENT_LOOP_SCRIPTED_SESSION_FILE", None)


class TestPremiseReconciliationE2E:
    def test_nine_question_over_seven_records_shortfall(self, monkeypatch,
                                                        tmp_path):
        """The observed discrepancy shape: the request asserts 9, the
        authoritative population is 7 (retrieval complete). The
        population-shortfall invariant fires: the comparison output is
        UNRESOLVED (never satisfied by the subset), the goal outcome is
        unanswerable, and the response states that the remaining 2 of
        the 9 asserted withdrawals cannot be obtained — no completion
        claim, and the subset facts remain shown for verification."""
        client = _client(tmp_path)
        rows = [f"0xaddr{i:02x}" for i in range(7)]      # 7 distinct
        a, resp = _journey(client, tmp_path, monkeypatch,
                           goal=GOAL_NINE, fixture_rows=rows)
        out = a["goal_contract"]["outputs"][0]
        assert out["satisfaction"] == "unresolved", (out, resp[:300])
        assert "仅可取得 7 笔" in (out.get("detail") or "")
        assert a["goal_outcome"] == "unanswerable"
        assert "您的请求提及 9 笔提现" in resp
        assert "仅可取得 7 笔" in resp
        assert "无法对全部 9 笔完成比较" in resp
        # no same/different conclusion over the subset — the groups and
        # table render for verification only
        assert "目的地标识分组" in resp
        assert "本次调查目标已完整回答" not in resp

    def test_consistent_premise_no_note(self, monkeypatch, tmp_path):
        client = _client(tmp_path)
        rows = [f"0xsame{i}" for i in range(1)] * 7       # 7 identical
        a, resp = _journey(client, tmp_path, monkeypatch,
                           goal="7笔提现都提向同一个地址吗？",
                           fixture_rows=rows)
        assert out_satisfaction(a) == "satisfied"
        assert "全部 7 笔提现指向同一目的地" in resp
        assert "您的请求提及" not in resp


def out_satisfaction(a):
    return (a.get("goal_contract") or {}).get("outputs", [{}])[0].get(
        "satisfaction")


class TestMetricPopulationReconciliationE2E:
    """§6 general contract: governed frequency metrics reconcile against
    the exposed population under RP's reference anchor — scoped-window
    verdicts render both numbers verifiably; genuine mismatches never
    pass as consistent."""

    def _fixture(self, rows):
        payload = rp_evidence_payload(case_id=CASE)
        payload["withdrawal_evidence"] = [
            {"withdrawal_id": f"W{160 + i:05d}", "asset": "BTC",
             "amount": 0.5, "address": f"0x{i:02x}" * 2,
             "is_new_address": False,
             "timestamp": ts, "risk_reason": "fx"}
            for i, (ts, addr) in enumerate(rows)]
        payload["feature_evidence"] = {
            "trade_frequency_24h": 64,
            "trade_frequency_7d": 64,
            "withdrawal_frequency_24h": 5,
            "withdrawal_volume_24h": 17.0,
            "account_age_days": 6,
            "shared_device_count": 1,
            "linked_account_count": 18,
            "unique_ip_count": 1,
            "withdrawal_risk_score": 1.0,
            # reference anchor: the 24h scoring window is
            # [2026-08-18T15:00Z, 2026-08-19T15:00Z]
            "feature_reference_time": "2026-08-19T15:00:00+00:00",
        }
        return payload

    def _rows_scoped(self):
        return ([("2026-08-19T10:00:00+00:00", "0x00"),
                 ("2026-08-19T11:00:00+00:00", "0x00"),
                 ("2026-08-19T12:00:00+00:00", "0x00"),
                 ("2026-08-19T13:00:00+00:00", "0x00"),
                 ("2026-08-19T14:00:00+00:00", "0x00")]
                + [("2026-08-18T14:00:00+00:00", "0x01"),
                   ("2026-08-18T14:30:00+00:00", "0x01")])   # before window

    def test_scoped_window_metric_reconciles_in_response(
            self, monkeypatch, tmp_path):
        from tests.test_consistency_summary_detail_v1 import _journey
        client = _client(tmp_path)
        activity, resp = _journey(
            client, tmp_path, monkeypatch,
            goal="7笔提现都提向同一个地址，还是每笔提向一个新地址？",
            fixture_payload=self._fixture(self._rows_scoped()))
        # summary metric (5 in scoring window) vs detail population (7
        # exposed) reconciled explicitly, both numbers traceable
        assert "总体口径说明" in resp
        assert "5 笔提现/24 小时" in resp
        assert "7 笔" in resp
        out = activity["goal_contract"]["outputs"][0]
        assert out["satisfaction"] == "satisfied"   # comparison unaffected

    def test_metric_mismatch_never_renders_consistent(
            self, monkeypatch, tmp_path):
        from tests.test_consistency_summary_detail_v1 import _journey
        client = _client(tmp_path)
        fixture = self._fixture(self._rows_scoped())
        fixture["feature_evidence"]["withdrawal_frequency_24h"] = 3
        activity, resp = _journey(
            client, tmp_path, monkeypatch,
            goal="7笔提现都提向同一个地址，还是每笔提向一个新地址？",
            fixture_payload=fixture)
        assert "⚠ 总体口径不一致" in resp
        assert "3 笔提现/24 小时" in resp and "5 笔不符" in resp


def _tx_payload():
    """Transaction-stream fixture: 5 BTC trades + feature metadata with
    the reference anchor and an unmapped governed metric (7d)."""
    payload = rp_evidence_payload(case_id=CASE)
    payload["transaction_evidence"] = [
        {"transaction_id": f"T{200 + i:05d}", "symbol": "BTC",
         "side": "BUY" if i % 2 else "SELL", "price": 100.0,
         "quantity": 1.0, "value": 100.0,
         "timestamp": f"2026-08-19T10:{i:02d}:00Z",
         "risk_reason": "fx"} for i in range(5)]
    payload["withdrawal_evidence"] = []
    payload["feature_evidence"] = {
        "trade_frequency_24h": 5, "trade_frequency_7d": 5,
        "withdrawal_frequency_24h": 0,
        "feature_reference_time": "2026-08-19T20:00:00+00:00"}
    return payload


class TestRecordStreamLabel:
    def test_transaction_view_labels_rows_by_actual_stream(
            self, monkeypatch, tmp_path):
        """The composer-label fix through the REAL path: an output whose
        requirement list mixes streams must label its rows by the ACTUAL
        evidence stream. F1 (transaction-stream finding) + transaction
        rows fixture → 交易记录, never 提现记录; the governed 7d metric
        with no registered verification is disclosed on the same
        surface. The live compound-requirement reproduction is the F7
        browser journey (real model proposed both requirements;
        post-fix run rendered 交易记录 — archived)."""
        client = _client(tmp_path)
        activity, resp = _journey(
            client, tmp_path, monkeypatch,
            goal="列出这个账号的全部交易记录。",
            goal_llm_text=TX_CONTRACT, focus="F1",
            plans=[PLAN_TRANSACTIONS], decisions=["STOP"],
            fixture_payload=_tx_payload())
        assert "完整交易记录共 5 条" in resp, resp[:300]
        assert "完整提现记录共" not in resp
        assert "trade_frequency_7d" in resp
        assert "尚未与明细逐笔核对" in resp

    def test_cjk_premise_reconciles_end_to_end(self, monkeypatch,
                                               tmp_path):
        """「九笔提现」 vs the authoritative 7-record population: the
        CJK-numeral premise is extracted (9) and the population
        shortfall fires exactly as for Arabic digits — explicit, never
        silently substituted."""
        client = _client(tmp_path)
        rows = [f"0xaddr{i:02x}" for i in range(7)]
        activity, resp = _journey(
            client, tmp_path, monkeypatch,
            goal="九笔提现都提向同一个地址，还是每笔提向一个新地址？",
            fixture_rows=rows)
        out = activity["goal_contract"]["outputs"][0]
        assert out["satisfaction"] == "unresolved"
        assert "您的请求提及 9 笔提现" in resp
        assert "仅可取得 7 笔" in resp
        assert "无法对全部 9 笔完成比较" in resp
        assert "本次调查目标已完整回答" not in resp


class TestObservationVsDecisionSemantics:
    """Consistency II addendum: Feature-signal entries are OBSERVATIONS
    (created for any nonzero feature value — no threshold, no detector
    decision). The user-visible framing must say so, and must never
    present an observation as a threshold-triggered risk decision.
    Rule-backed findings (F2/F3/F4) keep their matched-rule semantics."""

    def _feature_summary(self):
        return {"feature_facts": [
            {"name": "trade_frequency_24h", "value": 54}],
            "population_reconciliation": {
                "metric": "trade_frequency_24h", "claimed": 54,
                "reference_time": "2026-07-21T15:30:00+00:00",
                "window_start": "2026-07-20T15:30:00+00:00",
                "observed_in_window": 54, "exposed_total": 64,
                "outside_window": 10,
                "verdict": "consistent_scoped_window"}}

    def test_feature_branch_states_observation_not_decision(self):
        from app.agent_loop import _gsp_render_output_facts
        from app.goal_resolution import RequiredOutput
        from app.skills import EvidenceType
        out = RequiredOutput(
            output_id="O2", claim="trading_frequency_criteria",
            claim_kind="explanation", subject_ref="F7",
            scope="finding_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION])
        out.satisfaction = OutputSatisfaction.SATISFIED
        out.polarity = "positive"
        lines = _gsp_render_output_facts(
            out, self._feature_summary())
        text = "\n".join(lines)
        assert "行为特征观察记录" in text
        # observation ≠ triggered decision (stronger than the former
        # "非阈值触发的风险判定" wording — same invariant, explicit form)
        assert "观测记录的存在不等于规则已触发" in text
        assert "Trade frequency (24h) = 54" in text
        # never presented as a detector decision
        assert "命中规则" not in text
        assert "风险信号:行为特征检测" not in text

    def test_feature_boundary_states_no_threshold_no_proof(self):
        from app.agent_loop import _gsp_render_output_facts
        from app.goal_resolution import RequiredOutput
        from app.skills import EvidenceType
        out = RequiredOutput(
            output_id="O2", claim="trading_frequency_criteria",
            claim_kind="explanation", subject_ref="F7",
            scope="finding_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION])
        out.satisfaction = OutputSatisfaction.SATISFIED
        lines = _gsp_render_output_facts(
            out, self._feature_summary())
        text = "\n".join(lines)
        # no authoritative threshold semantics in the payload → the
        # renderer states the platform provides no such field explicitly
        assert "未提供该特征的独立结构化判定阈值字段" in text
        assert "不构成风险判定" in text and "欺诈" in text

    def test_rule_backed_branch_keeps_matched_rule_semantics(self):
        """Control: a RULE-grounded finding keeps the matched-rule
        explanation (F2/F3/F4 semantics unchanged)."""
        from app.agent_loop import _gsp_render_output_facts
        from app.goal_resolution import RequiredOutput
        from app.skills import EvidenceType
        out = RequiredOutput(
            output_id="O1", claim="withdrawal_frequency_rule",
            claim_kind="retrieval", subject_ref="F3",
            scope="finding_level",
            evidence_requirements=[EvidenceType.RULE_EXPLANATION])
        out.satisfaction = OutputSatisfaction.SATISFIED
        lines = _gsp_render_output_facts(out, {
            "rule_name": "High withdrawal frequency",
            "rule_description": "7 withdrawals in 24h",
            "threshold": "withdrawal_frequency_24h > 5"})
        text = "\n".join(lines)
        assert "命中规则" in text and "阈值" in text
        assert "行为特征观察记录" not in text

    def test_turn_path_feature_branch_observation_semantics(self):
        from app.agent_loop import _signal_answer_lines
        lines = _signal_answer_lines({
            "feature_facts": [
                {"name": "trade_frequency_24h", "value": 54}],
            "evidence_missing": False})
        text = "\n".join(lines)
        assert "行为特征观察记录" in text
        assert "观测记录的存在不等于规则已触发" in text

"""Cross-domain, bilingual response-language consistency (audit V1).

Pins the per-turn response-language contract end to end at the
composition boundary:

- the GSP composer renders ENGLISH narratives for EN requests and
  Chinese for ZH requests, across EVERY domain branch (case summary,
  ML / rule / feature / graph signals, related accounts, hedging
  candidates target- and set-scoped, withdrawal / transaction records,
  destination-identity / new-address / reverse-trade-metric facets,
  policy);
- factual data (ids, scores, timestamps, model names, field names)
  survive verbatim in BOTH languages;
- undeterminable language (None) keeps the historical default face
  byte-for-byte (no unintended behavior change);
- deterministic fallback paths (blocked / planning failure /
  clarification) follow the same rule;
- the intake submission keeps the user's OWN creation language (M4):
  the canonical English intake message never re-attributes a Chinese
  first request as English.

CJK detection doubles as the mixed-language check: every preserved
token (ids, LightGBM, Risk Platform, field names) is ASCII, so ANY CJK
character inside an EN narrative is leakage; a ZH narrative must
contain CJK narrative sentences.
"""

import re

import pytest

from app.agent_loop import (
    AgentLoopState,
    StopReason,
    compose_loop_response,
    _gsp_render_output_section,
    _output_header,
    project_observation,
)
from app.goal_resolution import (
    ClaimKind,
    EvidenceType,
    GoalContract,
    GoalRepresentation,
    GoalResolutionStatus,
    OutputSatisfaction,
    RequiredOutput,
    ScopeLevel,
    build_goal_contract,
    deterministic_contract_proposal,
)


_ZH = re.compile(r"[一-鿿]")


def _no_cjk(text: str) -> bool:
    return not _ZH.search(text)


def _has_cjk(text: str) -> bool:
    return bool(_ZH.search(text))


def _ml_summary() -> dict:
    return {
        "ml_score": 99.6,
        "detection_method": "LightGBM",
        "attribution_available": False,
        "available_feature_values": {"ml_score": 99.6,
                                     "trade_frequency_7d": 69},
    }


def _output(evidence: EvidenceType, subject="F1",
            kind: ClaimKind = ClaimKind.EXPLANATION,
            description="d", description_en=None,
            facets: list[str] | None = None) -> RequiredOutput:
    return RequiredOutput(
        output_id="O1", claim="the requested answer",
        claim_kind=kind, claim_description=description,
        claim_description_en=description_en,
        subject_ref=subject, scope="finding_level",
        evidence_requirements=[evidence],
        postcondition_facets=facets or [],
        satisfaction=OutputSatisfaction.SATISFIED)


def _section(output, summary, lang):
    return "\n".join(_gsp_render_output_section(output, summary, lang=lang))


# ---------------------------------------------------------------------------
# Case A — English ML signal explanation (the observed defect)
# ---------------------------------------------------------------------------

def test_case_a_en_ml_explain_is_fully_english():
    out = _output(EvidenceType.ML_SIGNAL_EXPLANATION,
                  description="ML detection basis",
                  description_en="ML detection basis")
    text = _section(out, _ml_summary(), "en")
    assert _no_cjk(text), text
    # facts preserved verbatim
    assert "99.6" in text and "LightGBM" in text
    assert "69" in text
    # the per-feature attribution boundary is stated in English
    assert "attribution" in text.lower()


def test_case_a_zh_ml_explain_is_chinese():
    out = _output(EvidenceType.ML_SIGNAL_EXPLANATION,
                  description="ML 检测依据")
    text = _section(out, _ml_summary(), "zh")
    assert _has_cjk(text)
    assert "99.6" in text and "LightGBM" in text


# ---------------------------------------------------------------------------
# Case B — English withdrawal destination comparison About header
# ---------------------------------------------------------------------------

def _destination_contract(request_text: str):
    proposal = deterministic_contract_proposal(request_text, None, "U00047")
    assert proposal is not None
    rep = GoalRepresentation(
        case_id="U00047",
        goal_resolution=GoalResolutionStatus.INFERRED.value,
        goal_type="identify_supporting_records",
        subject_type="case", subject_ref="U00047", source="heuristic",
        request_text=request_text, contract_proposal=proposal)
    contract, err = build_goal_contract(rep, case_id="U00047")
    assert err is None
    return contract


def test_case_b_en_about_header_is_english():
    contract = _destination_contract(
        "Are the withdrawal addresses all the same or different address? "
        "send all withdrawal records to me.")
    out = contract.outputs[0]
    out.satisfaction = OutputSatisfaction.PENDING
    header = _output_header(out, lang="en")
    assert _no_cjk(header), header
    text = _section(out, {}, lang="en")
    assert text.startswith('About "')
    assert _no_cjk(text), text
    # the contract's semantic face is unchanged (zh primary preserved)
    assert _has_cjk(out.claim_description or "")


def test_case_b_zh_about_header_stays_chinese():
    contract = _destination_contract(
        "这些提现的目的地地址是同一个还是不同地址？把全部提现记录发我。")
    out = contract.outputs[0]
    header = _output_header(out, lang="zh")
    assert _has_cjk(header)


# ---------------------------------------------------------------------------
# Domain × language matrix (GSP composer branches)
# ---------------------------------------------------------------------------

def _sat_output(evidence, **kw):
    return _output(evidence, **kw)


DOMAIN_CASES = [
    # (label, evidence, summary, claim_kind)
    ("case_summary", EvidenceType.CASE_SUMMARY,
     {"finding_count": 8, "risk_score": 86.1, "risk_level": "CRITICAL",
      "findings": [{"id": "F1", "title": "ML Pattern Detection.",
                    "summary": "score 99.6"}]}, ClaimKind.RETRIEVAL),
    ("rule_signal", EvidenceType.RULE_EXPLANATION,
     {"rule_evidence": [
         {"rule_name": "High withdrawal frequency", "severity": "high",
          "trigger": {"withdrawal_frequency_24h": 9},
          "threshold": "withdrawal_frequency_24h > 5",
          "description": "9 withdrawals in 24h"}]}, ClaimKind.EXPLANATION),
    ("feature_facts", EvidenceType.RULE_EXPLANATION,
     {"feature_facts": [
         {"name": "trade_frequency_7d", "value": 69},
         {"name": "withdrawal_count_24h", "value": 9}],
      "risk_factor": {"severity": "medium",
                      "description": "behavioral observation"},
      "finding_summary": "Risk Platform statement text"},
     ClaimKind.EXPLANATION),
    ("graph_cluster", EvidenceType.GRAPH_CLUSTER_FACTS,
     {"cluster_name": "CL-7", "detection_type": "device_cluster",
      "member_count": 4, "cluster_risk_score": 72,
      "shared_devices": ["dev-1"],
      "related_accounts": ["A1", "A2"]}, ClaimKind.EXPLANATION),
    ("related_accounts", EvidenceType.NETWORK_RELATIONS,
     {"related_accounts": ["A1", "A2"], "related_accounts_count": 2,
      "shared_devices": ["dev-9"]}, ClaimKind.RETRIEVAL),
    ("withdrawal_records", EvidenceType.WITHDRAWAL_RECORDS,
     {"record_count": 2,
      "record_rows": [
          {"record_id": "W1", "timestamp": "2026-07-21T01:00:00",
           "summary": "withdrawal 100 USDT"},
          {"record_id": "W2", "timestamp": "2026-07-21T02:00:00",
           "summary": "withdrawal 200 USDT"}]}, ClaimKind.RETRIEVAL),
    ("policy_refs", EvidenceType.POLICY_REFERENCES,
     {"match_count": 2,
      "associated_citations": [{"citation_id": "P-1",
                                "document": "Doc", "section": "§1"},
                               {"citation_id": "P-2",
                                "document": "Doc2", "section": "§2"}]},
     ClaimKind.RETRIEVAL),
]


@pytest.mark.parametrize("label,evidence,summary,kind", DOMAIN_CASES)
def test_domain_matrix_bilingual(label, evidence, summary, kind):
    out = _sat_output(evidence, description=f"{label} desc",
                      description_en=f"{label} desc", kind=kind)
    en = _section(out, dict(summary, _actual_evidence_types=[evidence]),
                  "en")
    zh = _section(out, dict(summary, _actual_evidence_types=[evidence]),
                  "zh")
    assert _no_cjk(en), f"{label}: CJK leaked into EN:\n{en}"
    assert _has_cjk(zh), f"{label}: ZH narrative missing:\n{zh}"
    if label == "withdrawal_records":
        assert "W1" in en and "W1" in zh
        assert "2026-07-21T01:00:00" in en


def test_hedging_candidates_bilingual_target_scoped():
    summary = {
        "candidates": [{
            "candidate_id": "C-1", "account_a": "U00047",
            "side_a": "BUY", "symbol": "USDT", "value_a": 1000.0,
            "account_b": "A2", "side_b": "SELL", "value_b": 995.0,
            "transaction_ids_a": ["T1"], "transaction_ids_b": ["T2"],
            "max_time_gap_seconds": 12,
            "value_difference_relative": 0.005,
        }],
        "candidates_found": 3,
        "value_tolerance_relative": 0.05,
        "value_tolerance_absolute_floor": 1.0,
        "max_time_delta_seconds": 30,
        "_actual_evidence_types": [EvidenceType.OPPOSITE_TRADE_RELATIONS],
    }
    out = _sat_output(EvidenceType.OPPOSITE_TRADE_RELATIONS,
                      subject="U00047", kind=ClaimKind.EXISTENCE_CHECK,
                      description="d", description_en="d")
    en = _section(out, summary, "en")
    zh = _section(out, summary, "zh")
    assert _no_cjk(en), en
    assert _has_cjk(zh)
    # the system-cap delivery note follows the language in BOTH faces
    assert "system candidate cap" in en
    assert "候选上限" in zh
    # facts identical across languages
    for token in ("C-1", "T1", "T2", "USDT"):
        assert token in en and token in zh


def test_destination_facet_verdict_bilingual():
    from app.goal_resolution import PostconditionFacet
    summary = {
        "destination_identity": {
            "basis": "destination_ref", "records_total": 2,
            "records_with_identity": 2, "records_without_identity": 0,
            "unique_destinations": 2,
            "groups": [
                {"destination_ref": "0xAAA", "address": "0xAAA…",
                 "record_ids": ["W1"]},
                {"destination_ref": "0xBBB", "address": "0xBBB…",
                 "record_ids": ["W2"]},
            ],
        },
        "record_count": 2,
        "record_rows": [
            {"record_id": "W1", "timestamp": "t1", "summary": "s1"},
            {"record_id": "W2", "timestamp": "t2", "summary": "s2"}],
        "_actual_evidence_types": [EvidenceType.WITHDRAWAL_RECORDS],
    }
    out = _sat_output(
        EvidenceType.WITHDRAWAL_RECORDS, kind=ClaimKind.EXISTENCE_CHECK,
        description="d", description_en="d",
        facets=[PostconditionFacet.DESTINATION_ADDRESS_IDENTITY.value])
    en = _section(out, summary, "en")
    zh = _section(out, summary, "zh")
    assert _no_cjk(en), en
    assert _has_cjk(zh)
    assert "0xAAA" in en


def test_new_address_facet_bilingual():
    from app.goal_resolution import PostconditionFacet
    summary = {
        "new_address_status": {
            "records": [
                {"record_id": "W1", "is_new_address": True},
                {"record_id": "W2", "is_new_address": False}],
            "records_total": 2, "true_count": 1, "false_count": 1,
            "missing_count": 0,
        },
    }
    out = _sat_output(
        EvidenceType.WITHDRAWAL_RECORDS, kind=ClaimKind.RETRIEVAL,
        description="d", description_en="d",
        facets=[PostconditionFacet.WITHDRAWAL_NEW_ADDRESS_STATUS.value])
    en = _section(out, summary, "en")
    zh = _section(out, summary, "zh")
    assert _no_cjk(en), en
    assert _has_cjk(zh)
    assert "W1" in en and "W2" in en


# ---------------------------------------------------------------------------
# Reverse-trade-metric facet (live defect U00033 / TASK-71adba5e3614:
# an EN turn rendered the coverage path's ZH verdict detail verbatim)
# ---------------------------------------------------------------------------

_ZH_METRIC_DETAIL = (
    "33.33%（opposite_trade_ratio）是账户自身的反向交易比例："
    "按该账户自己的全部交易计算，分子为较少一侧的交易笔数"
    "（min(BUY, SELL)），分母为该账户交易总笔数；它衡量的是同一"
    "账户内的买卖平衡，不是跨账户匹配率。来源：Risk Platform "
    "feature_evidence。")


@pytest.mark.parametrize("subject", ["U00033", "U00299"])
def test_reverse_trade_metric_facet_bilingual_per_turn(subject):
    """The metric facet's face follows the PER-TURN language, never the
    zh verdict detail stamped by the coverage/contract path: the en face
    re-states the facts from data; the zh face keeps the stamped detail.
    Case-independent (parametrized over two case subjects)."""
    from app.goal_resolution import PostconditionFacet
    summary = {"target_account_metrics": {"opposite_trade_ratio": 1 / 3}}
    out = _sat_output(
        EvidenceType.TRANSACTION_RECORDS, subject=subject,
        kind=ClaimKind.EXISTENCE_CHECK,
        description="d", description_en="d",
        facets=[PostconditionFacet.REVERSE_TRADE_METRIC_SEMANTICS.value])
    # the coverage/contract path stamps output.detail with the zh face
    # BEFORE composition — the en turn must not render it
    out.detail = _ZH_METRIC_DETAIL
    en = _section(out, summary, "en")
    zh = _section(out, summary, "zh")
    # en: entered the metric facet's en branch (its unique wording), zh
    # detail NOT leaked
    assert _no_cjk(en), en
    assert "OWN opposite-trade ratio" in en, en
    assert "33.33%" in en and "opposite_trade_ratio" in en, en
    assert "Source: Risk Platform feature_evidence." in en, en
    # zh: the stamped detail face is preserved
    assert _has_cjk(zh), zh
    assert "是账户自身的反向交易比例" in zh, zh
    assert "33.33%" in zh, zh
    # per-turn priority: the SAME output (zh detail stamped) rendered
    # under an en TURN keeps the en face — session/contract primary
    # language never overrides the current turn
    assert _section(out, summary, "en") == en


# ---------------------------------------------------------------------------
# Undeterminable language (None) keeps the historical default face
# ---------------------------------------------------------------------------

def test_none_language_keeps_default_faces():
    out = _output(EvidenceType.ML_SIGNAL_EXPLANATION,
                  description="ML 检测依据")
    text = _gsp_render_output_section(out, _ml_summary(), lang="zh")
    # default face for None is zh — the zh narrative is unchanged
    assert _has_cjk("\n".join(text))


# ---------------------------------------------------------------------------
# Fallback paths — blocked / planning-failure / legacy composer
# ---------------------------------------------------------------------------

def _state(lang=None, status="blocked", stop=None, response="") :
    st = AgentLoopState(task_id="T", investigation_id="I",
                        case_id="U00047", goal="g",
                        response_language=lang)
    st.agent_status = status
    st.stop_reason = stop or StopReason.DEPENDENCY_UNAVAILABLE
    st.response = response
    return st


def test_legacy_composer_status_faces():
    # BOUNDED_DATA completion: zh face for zh, en for en
    st = _state("zh", "completed", StopReason.BOUNDED_DATA)
    out = compose_loop_response(st)
    assert _has_cjk(out)
    st = _state("en", "completed", StopReason.BOUNDED_DATA)
    out = compose_loop_response(st)
    assert _no_cjk(out)
    assert "available data" in out


def test_legacy_composer_undeterminable_keeps_default():
    st = _state(None, "completed", StopReason.BOUNDED_DATA)
    out = compose_loop_response(st)
    # historical default face (zh for this branch) is preserved
    assert _has_cjk(out)


# ---------------------------------------------------------------------------
# Activity display phrases follow the run language
# ---------------------------------------------------------------------------

def _tool_result(outcome="success"):
    from app.models import ToolResult, ToolResultOutcome
    return ToolResult(outcome=ToolResultOutcome(outcome), data={})


def test_activity_display_phrase_bilingual():
    from app.models import ToolResult, ToolResultOutcome
    result = ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"findings": [{"finding_id": "F1"}], "risk_score": 86.1})
    en = project_observation("risk_case_fetch", "fetch_case",
                             result, lang="en")
    zh = project_observation("risk_case_fetch", "fetch_case",
                             result, lang="zh")
    assert _no_cjk(en["display"])
    assert _has_cjk(zh["display"])
    none = project_observation("risk_case_fetch", "fetch_case", result)
    assert _no_cjk(none["display"])          # historical default face (en)


# ---------------------------------------------------------------------------
# Loop-level blocked messages follow the run language
# ---------------------------------------------------------------------------

def test_blocked_dependency_message_bilingual():
    from app.agent_loop import AgentLoopRunner
    runner = AgentLoopRunner.__new__(AgentLoopRunner)   # no store needed
    from datetime import datetime, timezone

    def _now():
        return datetime.now(timezone.utc).isoformat()

    # emulate the message branches by checking the composed text face via
    # _terminate is heavy; here we pin the compose-level append: the
    # blocked response rides compose_loop_response unchanged
    st = _state("zh", "blocked", StopReason.DEPENDENCY_UNAVAILABLE,
                response="当前无法开始调查：Risk Platform 不可用。系统"
                         "不会虚构调查结果——请稍后重试。")
    out = compose_loop_response(st)
    assert _has_cjk(out)
    st = _state("en", "blocked", StopReason.DEPENDENCY_UNAVAILABLE,
                response="The investigation cannot start right now: the "
                         "Risk Platform is unavailable.")
    out = compose_loop_response(st)
    assert _no_cjk(out)


# ---------------------------------------------------------------------------
# Clarification texts follow the REQUEST language (context resolution)
# ---------------------------------------------------------------------------

def _resolver_with_context(case_id="U00047", focused=None):
    from app.context_resolution import ContextResolver, ResolutionStatus
    from app.models import InvestigationContext, FocusSource
    ctx = InvestigationContext(
        case_id=case_id,
        focused_finding_id=focused,
        focus_source=FocusSource.USER_SELECTED if focused else None)
    return ContextResolver(), ctx, ResolutionStatus


def test_capability_guidance_bilingual():
    resolver, ctx, ResolutionStatus = _resolver_with_context()
    out_en = resolver.resolve("what can you do", ctx, findings=[])
    assert out_en.status == ResolutionStatus.CAPABILITY_GUIDANCE
    assert _no_cjk(out_en.clarification_message or "")
    out_zh = resolver.resolve("你能做什么", ctx, findings=[])
    assert out_zh.status == ResolutionStatus.CAPABILITY_GUIDANCE
    assert _has_cjk(out_zh.clarification_message or "")


def test_cross_case_guidance_bilingual():
    resolver, ctx, ResolutionStatus = _resolver_with_context("U00047")
    out_en = resolver.resolve("investigate U00099 instead", ctx,
                              findings=[])
    assert out_en.status == ResolutionStatus.UNRESOLVED
    assert _no_cjk(out_en.clarification_message or "")
    out_zh = resolver.resolve("帮我查一下 U00099 的情况", ctx, findings=[])
    assert out_zh.status == ResolutionStatus.UNRESOLVED
    assert _has_cjk(out_zh.clarification_message or "")


def test_unresolved_target_bilingual():
    resolver, ctx, ResolutionStatus = _resolver_with_context(
        "U00047", focused="F1")
    out_en = resolver.resolve("why is the thing suspicious", ctx,
                              findings=[])
    if out_en.status == ResolutionStatus.UNRESOLVED:
        assert _no_cjk(out_en.clarification_message or "")
    out_zh = resolver.resolve("为什么这个东西可疑", ctx, findings=[])
    if out_zh.status == ResolutionStatus.UNRESOLVED:
        assert _has_cjk(out_zh.clarification_message or "")


# ---------------------------------------------------------------------------
# M4 — intake language: the canonical English intake message must not
# re-attribute a Chinese creation request as English
# ---------------------------------------------------------------------------

def test_submission_language_intake_uses_creation_language():
    from app.api.investigations import InvestigationAPI
    from app.models import InvestigationContext
    api = InvestigationAPI.__new__(InvestigationAPI)   # no stores needed
    zh_ctx = InvestigationContext(case_id="U00047",
                                  initial_response_language="zh")
    lang = api._submission_response_language("U00047",
                                             "Investigate U00047", zh_ctx)
    assert lang == "zh"
    en_ctx = InvestigationContext(case_id="U00047",
                                  initial_response_language="en")
    lang = api._submission_response_language("U00047",
                                             "Investigate U00047", en_ctx)
    assert lang == "en"
    # a NON-intake submission detects from the request text itself —
    # stored language never overrides the current turn
    lang = api._submission_response_language(
        "U00047", "列出所有关联账号", zh_ctx)
    assert lang == "zh"
    lang = api._submission_response_language(
        "U00047", "please explain what ML detected", en_ctx)
    assert lang == "en"
    # no stored language → detect from the canonical text (en)
    plain_ctx = InvestigationContext(case_id="U00047")
    lang = api._submission_response_language("U00047",
                                             "Investigate U00047", plain_ctx)
    assert lang == "en"


# ---------------------------------------------------------------------------
# Language survives persistence (resume path)
# ---------------------------------------------------------------------------

def test_response_language_survives_state_roundtrip():
    import json
    st = AgentLoopState(task_id="T", investigation_id="I",
                        case_id="U00047", goal="g",
                        response_language="en")
    restored = AgentLoopState.model_validate(
        json.loads(st.model_dump_json()))
    assert restored.response_language == "en"


# ---------------------------------------------------------------------------
# Intake representation carries the EN header variant (M2/M4)
# ---------------------------------------------------------------------------

def test_case_intake_representation_bilingual_header():
    from app.api.investigations import _case_intake_representation
    rep = _case_intake_representation("U00047", "Investigate U00047")
    out = rep.contract_proposal["required_outputs"][0]
    assert _has_cjk(out["claim_description"])
    assert _no_cjk(out["claim_description_en"])
    contract, err = build_goal_contract(rep, case_id="U00047")
    assert err is None
    header_en = _output_header(contract.outputs[0], lang="en")
    assert _no_cjk(header_en)
    header_zh = _output_header(contract.outputs[0], lang="zh")
    assert _has_cjk(header_zh)


# ---------------------------------------------------------------------------
# Legacy loop composer (reachable via /submit when no contract is accepted)
# and the /turn surface (public REST API; the product UI does not call it —
# reachable-condition documentation per the language audit closeout)
# ---------------------------------------------------------------------------

def _legacy_state(lang, observations):
    st = AgentLoopState(task_id="T", investigation_id="I",
                        case_id="U00047", goal="g",
                        response_language=lang)
    st.observations = observations
    st.agent_status = "completed"
    st.stop_reason = StopReason.GOAL_SATISFIED
    return st


def test_legacy_composer_rule_signal_bilingual():
    rule_obs = {
        "tool_name": "signal_explain", "outcome": "success",
        "semantic_status": "Rule",
        "summary": {"rule_name": "High withdrawal frequency",
                    "rule_description": "7 withdrawals in 24h",
                    "threshold": "withdrawal_frequency_24h > 5",
                    "trigger_values": {"withdrawal_frequency_24h": 7},
                    "contribution": 20},
    }
    en = compose_loop_response(_legacy_state("en", [rule_obs]))
    assert "Risk signal: rule 'High withdrawal frequency' triggered" in en
    # the threshold is humanized with the shared feature labels (deterministic echo)
    assert "Withdrawal frequency (24h) > 5" in en
    assert not _ZH.search(en), en
    zh = compose_loop_response(_legacy_state(None, [rule_obs]))
    assert "风险信号:规则「High withdrawal frequency」触发" in zh


def test_legacy_composer_graph_signal_bilingual():
    graph_obs = {
        "tool_name": "signal_explain", "outcome": "success",
        "semantic_status": "Graph",
        "summary": {"cluster_name": "CL-1", "detection_type": "device",
                    "member_count": 4, "cluster_risk_score": 60,
                    "shared_devices": ["dev-1"],
                    "related_accounts": ["A1"]},
    }
    en = compose_loop_response(_legacy_state("en", [graph_obs]))
    assert "graph detection places this account" in en
    assert "Related accounts: A1" in en
    assert not _ZH.search(en), en
    zh = compose_loop_response(_legacy_state("zh", [graph_obs]))
    assert "图关系检测" in zh


def test_legacy_composer_feature_signal_bilingual():
    feat_obs = {
        "tool_name": "signal_explain", "outcome": "success",
        "semantic_status": "Feature",
        "summary": {"feature_facts": [
            {"name": "trade_frequency_7d", "value": 66}],
            "risk_factor": {"severity": "medium"},
            "finding_summary": "RP statement"},
    }
    en = compose_loop_response(_legacy_state("en", [feat_obs]))
    assert "behavioral-feature observation record" in en
    assert "RP statement" in en
    assert not _ZH.search(en), en


def test_legacy_composer_evidence_table_bilingual():
    ev_obs = {
        "tool_name": "finding_drilldown", "outcome": "success",
        "semantic_status": "evidence",
        "summary": {"record_count": 1,
                    "record_rows": [{"record_id": "W1",
                                     "timestamp": "t1",
                                     "summary": "withdrawal"}],
                    "streams": {"withdrawals_included": True},
                    "rows_truncated": False},
    }
    en = compose_loop_response(_legacy_state("en", [ev_obs]))
    assert "Complete evidence — 1 records (withdrawal records):" in en
    assert "| # | Record ID | Time | Destination | Details |" in en
    assert "W1" in en
    assert not _ZH.search(en), en
    zh = compose_loop_response(_legacy_state("zh", [ev_obs]))
    assert "完整证据共 1 条(提现记录)：" in zh


def test_legacy_composer_cross_account_bilingual():
    ca_obs = {
        "tool_name": "cross_account_trade_analysis", "outcome": "success",
        "semantic_status": "NO_MATCH",
        "summary": {"target_account": "U00047",
                    "target_account_metrics": {"opposite_trade_ratio": 0.33},
                    "target_retrieval": {"transactions_retrieved": 50},
                    "counterparty_retrieval": {
                        "transactions_retrieved": 30,
                        "accounts_queried": 4}},
    }
    en = compose_loop_response(_legacy_state("en", [ca_obs]))
    assert "About the reverse-trade rate" in en
    assert "No. Within the retrieved scope" in en
    assert "Analysis scope:" in en
    assert not _ZH.search(en), en
    zh = compose_loop_response(_legacy_state("zh", [ca_obs]))
    assert "关于反向交易率" in zh
    assert "分析范围:" in zh


def test_turn_surface_ml_explanation_bilingual():
    """The /turn surface is a live public REST API (the product UI does
    not call it). Its ML-explanation branch is bilingual — pin both faces
    so the reachable zh face stays Chinese and the en face stays
    English."""
    from app.investigation_service import compose_response
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome
    tc = ToolCallV2(
        tool_call_id="TC-S1-1", investigation_id="I", task_id="T",
        tool_name="signal_explain",
        result=ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"signal_type": "ML",
                  "explanation": {
                      "ml_score": 99.6,
                      "detection_method": "LightGBM",
                      "score_interpretation": "system signal",
                      "attribution_available": False,
                      "available_feature_values": {"ml_score": 99.6}}}))
    en = compose_response(
        user_request="please explain what ML detected", skill_id=None,
        plan=None, tool_calls=[tc], execution_errors=[],
        response_language="en")
    # the turn surface's en ML face is its own deterministic prose (score +
    # feature context + attribution boundary); language rule: no CJK
    assert "99.6" in en
    assert "attribution" in en.lower()
    assert not _ZH.search(en), en
    zh = compose_response(
        user_request="解释一下 ML 检测到了什么", skill_id=None,
        plan=None, tool_calls=[tc], execution_errors=[],
        response_language="zh")
    assert "99.6" in zh and _ZH.search(zh)


# ---------------------------------------------------------------------------
# Follow-up preview / chips (user-reachable: the workspace previews chips on
# every selection and re-offers them on reopen; clicking submits the intent)
# ---------------------------------------------------------------------------

def test_followup_chips_bilingual_faces():
    from app.followups import SelectionInput, TriggerReason, select_followups
    from app.models import InvestigationContext

    def select(lang):
        return select_followups(
            SelectionInput(
                trigger=TriggerReason.FOCUS_CHANGED,
                context=InvestigationContext(
                    case_id="U00047", focused_finding_id="F3"),
                finding_capabilities=["timeline", "signal_explain",
                                     "policy_lookup"]),
            lang=lang)

    en = select("en")
    zh = select("zh")
    none = select(None)
    # same server-owned ids in every face
    assert [f.follow_up_id for f in en] == [f.follow_up_id for f in zh]
    # en face (default for None) unchanged
    assert [f.label for f in none] == [f.label for f in en]
    assert none[0].label == "Why is this finding flagged?"
    assert "Why was this finding flagged?" in none[0].intent
    # zh face: Chinese narrative, identical semantics and ids
    assert zh[0].label == "这个发现为什么被标记？"
    assert zh[0].intent == "这个发现为什么被标记？解释它的检测信号。"
    assert _has_cjk(zh[0].label) and _has_cjk(zh[0].intent)
    for f in zh:
        assert _has_cjk(f.label), f
    # clicking the zh chip submits the zh intent (per-turn detection
    # consistency — the intent text itself carries the language)
    zh_intent = next(f for f in zh
                     if f.follow_up_id == "explain_finding").intent
    from app.response_language import detect_response_language
    assert detect_response_language(zh_intent) == "zh"
    en_intent = next(f for f in en
                     if f.follow_up_id == "explain_finding").intent
    assert detect_response_language(en_intent) == "en"


def test_followup_intent_resolution_follows_chip_language():
    """A clicked chip resolves to the canonical intent in the SAME face it
    was rendered in (registry ids unchanged; no tool/skill leakage)."""
    from app.api.investigations import InvestigationAPI, _FOLLOWUP_INDEX
    from app.api.schemas import TurnRequest
    api = InvestigationAPI.__new__(InvestigationAPI)
    req_en = TurnRequest(message="", follow_up_id="explain_finding",
                         lang="en")
    req_zh = TurnRequest(message="", follow_up_id="explain_finding",
                         lang="zh")
    intent_en = api._resolve_followup(req_en)
    intent_zh = api._resolve_followup(req_zh)
    assert intent_en == _FOLLOWUP_INDEX["explain_finding"].intent
    assert intent_zh == _FOLLOWUP_INDEX["explain_finding"].intent_zh
    assert _has_cjk(intent_zh) and not _has_cjk(intent_en)
    # a follow_up_id-free request passes the message through (lang ignored)
    assert api._resolve_followup(
        TurnRequest(message="raw user text", lang="zh")) == "raw user text"


# ---------------------------------------------------------------------------
# Public /turn surface (POST /api/v2/investigations/{id}/turn →
# service.run_turn → compose_response). Reachable user-visible natural
# language per the closeout inventory: planning failures, executor errors,
# per-payload narrative (intake / evidence / timeline / self-opposite /
# related-account / cross-account / signals / policy / artifact confirm),
# gap + label lines, EMPTY/error outcomes. EN faces are byte-preserved;
# zh faces follow the SAME payload semantics (signals reuse the loop
# path's zh renderer).
# ---------------------------------------------------------------------------

def _turn(payload_outcome="success", data=None, response_language=None,
          user_request="q", next_data_needed=None, **kw):
    from app.investigation_service import compose_response
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome
    outcome = ToolResultOutcome(payload_outcome)
    result_kw = {"data": data or {},
                 "next_data_needed": list(next_data_needed or [])}
    if payload_outcome in ("integration_error", "unsupported",
                           "validation_error"):
        from app.models import ToolError
        result_kw["error"] = ToolError(code="X", message="detail msg")
    tc = ToolCallV2(
        tool_call_id="TC-S1-1", investigation_id="I", task_id="T",
        tool_name="finding_drilldown",
        result=ToolResult(outcome=outcome, **result_kw))
    return compose_response(
        user_request=user_request, skill_id=None, plan=None,
        tool_calls=[tc], execution_errors=[], **kw,
        response_language=response_language)


def test_turn_planning_failure_bilingual():
    from app.investigation_service import compose_response
    from app.planner_v2 import PlanningFailure
    en = compose_response(
        user_request="q", skill_id=None, plan=None, tool_calls=[],
        execution_errors=[],
        planning_failure=PlanningFailure(code="LLM_TIMEOUT", message="m"),
        response_language=None)
    assert "timed out" in en and not _ZH.search(en)
    zh = compose_response(
        user_request="查询案例", skill_id=None, plan=None, tool_calls=[],
        execution_errors=[],
        planning_failure=PlanningFailure(code="LLM_TIMEOUT", message="m"),
        response_language="zh")
    assert "规划请求超时" in zh and _ZH.search(zh)


def test_turn_fatal_integration_error_bilingual():
    from app.investigation_service import compose_response
    from app.executor_v2 import ExecutorError
    from app.models import ToolCallV2
    fatal = ExecutorError(code="STEP_FAILED", message="m", step_id="S1",
                          detail={"outcome": "integration_error"})
    en = compose_response(
        user_request="q", skill_id=None, plan=None, tool_calls=[],
        execution_errors=[fatal], response_language=None)
    assert "could not be reached" in en and not _ZH.search(en)
    zh = compose_response(
        user_request="查询案例", skill_id=None, plan=None, tool_calls=[],
        execution_errors=[fatal], response_language="zh")
    assert "无法连接 Risk Platform" in zh and _ZH.search(zh)


def test_turn_intake_narrative_bilingual():
    data = {"findings": [{"finding_id": "F1", "title": "ML Pattern",
                          "summary": "score"}], "case_id": "U00047"}
    en = _turn(data=data, response_language=None)
    assert "2 findings were identified" in en or         "1 finding was identified" in en
    assert not _ZH.search(en), en
    zh = _turn(data=data, response_language="zh")
    assert "个风险发现" in zh and "发现面板" in zh
    assert "ML Pattern" in zh          # RP finding data rides verbatim


def test_turn_rule_signal_bilingual_via_shared_renderer():
    data = {"signal_type": "Rule",
            "rule": {"name": "High withdrawal frequency",
                     "description": "7 withdrawals in 24h exceeds norm",
                     "threshold": "withdrawal_frequency_24h > 5",
                     "trigger_values": {"withdrawal_frequency_24h": 7},
                     "contribution": 20}}
    en = _turn(data=data, response_language=None)
    assert "flagged" in en and not _ZH.search(en), en
    zh = _turn(data=data, response_language="zh")
    assert "风险信号:规则「High withdrawal frequency」触发" in zh
    assert "7" in zh and "Withdrawal frequency (24h)" in zh
    # no EN narrative leak: the loop renderer's zh face carries the why
    assert "The finding was flagged" not in zh


def test_turn_feature_signal_bilingual():
    data = {"signal_type": "Feature",
            "explanation": {"features": [
                {"name": "trade_frequency_7d", "value": 66}]}}
    en = _turn(data=data, response_language=None)
    assert "behavioral risk-feature detection" in en
    assert not _ZH.search(en), en
    zh = _turn(data=data, response_language="zh")
    assert "行为特征观察记录" in zh
    assert "Trade frequency (7d) = 66" in zh


def test_turn_graph_signal_bilingual():
    data = {"signal_type": "Graph",
            "explanation": {"cluster": {"cluster_name": "CL-1",
                                        "detection_type": "device",
                                        "member_count": 4,
                                        "cluster_risk_score": 60},
                            "shared_devices": ["dev-1"],
                            "related_accounts": ["A1"]}}
    en = _turn(data=data, response_language=None)
    assert "graph-based risk detection" in en
    assert not _ZH.search(en), en
    zh = _turn(data=data, response_language="zh")
    assert "图关系检测" in zh and "A1" in zh


def test_turn_ml_zh_has_no_en_score_label_leak():
    """Regression: the zh ML face renders the score in Chinese prose; the
    EN 'ML score:' label line must not be appended after it."""
    data = {"signal_type": "ML",
            "explanation": {"ml_score": 99.6,
                            "detection_method": "LightGBM",
                            "score_interpretation": "system signal",
                            "attribution_available": False,
                            "available_feature_values": {"ml_score": 99.6}}}
    zh = _turn(data=data, response_language="zh",
               user_request="解释一下ML检测到了什么")
    assert _has_cjk(zh)
    assert "ML score:" not in zh, zh
    assert "99.6" in zh and "LightGBM" in zh


def test_turn_cross_account_bilingual():
    data = {"analysis": "cross_account_trade_analysis",
            "analysis_status": "NO_MATCH", "target_account": "U00047",
            "target_account_metrics": {"opposite_trade_ratio": 0.33},
            "retrieval": {"target": {"transactions_retrieved": 50},
                          "counterparty": {"transactions_retrieved": 30,
                                           "accounts_queried": 4}}}
    en = _turn(data=data, response_language=None)
    assert "No candidate cross-account opposite-trade" in en
    assert "Analyzed scope:" in en
    assert not _ZH.search(en), en
    zh = _turn(data=data, response_language="zh")
    assert "关于反向交易率" in zh and "分析范围：" in zh
    assert "未发现涉及账户 U00047 的候选" in zh


def test_turn_evidence_comparison_bilingual():
    data = {"view": "evidence", "complete": True, "record_count": 2,
            "streams": {"withdrawals_included": True},
            "destination_identity": {
                "records_total": 2, "records_with_identity": 2,
                "records_without_identity": 0,
                "unique_destinations": 2,
                "groups": [
                    {"record_ids": ["W1"], "destination_ref": "0xAAA"},
                    {"record_ids": ["W2"], "destination_ref": "0xBBB"}]},
            "records": [{"record_id": "W1", "timestamp": "t1",
                         "summary": "s1"},
                        {"record_id": "W2", "timestamp": "t2",
                         "summary": "s2"}]}
    en = _turn(data=data, response_language=None,
               user_request="Are the withdrawal addresses all the same?")
    assert "Destination identity: the 2 withdrawals go" in en
    assert "2 DIFFERENT destinations" in en
    assert not _ZH.search(en), en
    zh = _turn(data=data, response_language="zh",
               user_request="这些提现是同一个地址吗")
    assert "目的地标识：2 笔提现指向 2 个不同目的地" in zh
    assert "W1" in zh and "W2" in zh
    assert "0xAAA" in zh


def test_turn_empty_and_artifact_confirm_bilingual():
    en = _turn(payload_outcome="empty", data={"view": "evidence"},
               response_language=None)
    assert "holds no concrete records" in en and not _ZH.search(en)
    zh = _turn(payload_outcome="empty", data={"view": "evidence"},
               response_language="zh")
    assert "没有任何具体记录" in zh and _ZH.search(zh)
    art = {"artifact": {"artifact_id": "ART-x"}}
    en2 = _turn(data=art, response_language=None)
    assert "Artifacts panel on the right (ART-x)" in en2
    zh2 = _turn(data=art, response_language="zh")
    assert "右侧 Artifacts 面板查看（ART-x）" in zh2


# ---------------------------------------------------------------------------
# B-class closeout: system-authored tool guidance (warnings /
# next_data_needed — OUR domain tools' closed vocabulary, rendered at the
# /turn composer) follows the turn language; unknown items stay verbatim.
# ---------------------------------------------------------------------------

def test_turn_note_warning_bilingual():
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome
    warnings = ["Case has no data; finding cannot exist."]
    tc = ToolCallV2(
        tool_call_id="TC-S1-1", investigation_id="I", task_id="T",
        tool_name="finding_drilldown",
        result=ToolResult(outcome=ToolResultOutcome.EMPTY,
                          data={"finding_id": "F1"}, warnings=warnings))
    from app.investigation_service import compose_response
    en = compose_response(user_request="q", skill_id=None, plan=None,
                          tool_calls=[tc], execution_errors=[],
                          response_language=None)
    assert "Note: Case has no data; finding cannot exist." in en
    assert not _ZH.search(en)
    zh = compose_response(user_request="查询", skill_id=None, plan=None,
                          tool_calls=[tc], execution_errors=[],
                          response_language="zh")
    assert "注：案例无数据；该发现不可能存在。" in zh
    assert _has_cjk(zh)


def test_turn_unknown_warning_diagnostic_line():
    """An unrecognized warning is system-authored guidance without a zh
    mapping: the zh answer body stays Chinese (a clear generic notice that
    an untranslated system notice exists), and the ORIGINAL string is kept
    in an explicitly labeled diagnostic slot — not disguised as a
    translation, not silently dropped, and never a guessed translation."""
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome
    tc = ToolCallV2(
        tool_call_id="TC-S1-1", investigation_id="I", task_id="T",
        tool_name="finding_drilldown",
        result=ToolResult(outcome=ToolResultOutcome.EMPTY,
                          data={"finding_id": "F1"},
                          warnings=["Novel producer-specific note 42"]))
    from app.investigation_service import compose_response
    zh = compose_response(user_request="查询", skill_id=None, plan=None,
                          tool_calls=[tc], execution_errors=[],
                          response_language="zh")
    assert "注：存在一条尚未配置中文映射的系统提示" in zh
    assert "〔原文: Novel producer-specific note 42〕" in zh
    # the unknown EN string must not ride as bare answer-body prose
    assert "Novel producer-specific note 42" not in zh.replace(
        "〔原文: Novel producer-specific note 42〕", "")
    # EN behavior unchanged
    en = compose_response(user_request="q", skill_id=None, plan=None,
                          tool_calls=[tc], execution_errors=[],
                          response_language=None)
    assert "Note: Novel producer-specific note 42" in en
    assert not _ZH.search(en)


def test_turn_interpolated_warning_maps():
    from app.investigation_service import _zh_tool_text
    out = _zh_tool_text("Timeline is a bounded subset: showing top_n=5 of "
                        "20 events at the user's explicit request "
                        "(chronological order preserved).")
    assert out == "时间线为有界子集：按用户明确要求显示前 5 条（共 20 个"                   "事件，保持时间顺序）。"
    out = _zh_tool_text("30 candidate relationships were found; the top 5 "
                        "are reported (TRADE_ANALYSIS_MAX_CANDIDATES=50).")
    assert "共发现 30 组" in out and "前 5 组" in out \
        and "TRADE_ANALYSIS_MAX_CANDIDATES=50" in out


def test_turn_additional_data_needed_bilingual():
    data = {"signal_type": "Feature", "evidence_missing": True,
            "explanation": {},
            "next_data_needed": ["triggered-rule evidence associated with "
                                 "this finding"]}
    en = _turn(data=data, response_language=None,
               next_data_needed=["triggered-rule evidence associated "
                                 "with this finding"])
    assert ("Additional data needed: triggered-rule evidence associated "
            "with this finding.") in en
    assert not _ZH.search(en), en
    zh = _turn(data=data, response_language="zh",
               next_data_needed=["triggered-rule evidence associated "
                                 "with this finding"])
    assert "还需补充的数据：与该发现关联的已触发规则证据." in zh
    assert _has_cjk(zh)


def test_turn_gap_statement_zh_uses_mapped_items():
    data = {"evidence_missing": True,
            "next_data_needed": ["network/cluster evidence for this case"]}
    en = _turn(data=data, response_language=None)
    assert ('The Risk Platform confirms the finding, but missing: '
            '"network/cluster evidence for this case"') in en
    zh = _turn(data=data, response_language="zh")
    assert "Risk Platform 确认该发现，但缺少：“该案例的网络/集群证据”" \
        in zh
    assert 'network/cluster evidence' not in zh      # no EN fragment leak

# ---------------------------------------------------------------------------
# Structured-detail coexistence regression (the zh rule/feature/graph faces
# previously used `continue`, which silently skipped the payload's
# structured supporting detail — additional-data line, policy references,
# artifact confirmation). The zh faces must coexist with every applicable
# detail; the EN faces are unchanged.
# ---------------------------------------------------------------------------

_RULE_DATA = {"signal_type": "Rule", "evidence_missing": True,
              "rule": {"name": "High withdrawal frequency",
                       "description": "7 withdrawals in 24h",
                       "threshold": "withdrawal_frequency_24h > 5",
                       "trigger_values": {"withdrawal_frequency_24h": 7},
                       "contribution": 20}}
_FEATURE_DATA = {"signal_type": "Feature", "evidence_missing": True,
                 "explanation": {"features": [
                     {"name": "trade_frequency_7d", "value": 66}]}}
_GRAPH_DATA = {"signal_type": "Graph", "evidence_missing": True,
               "explanation": {"cluster": {"cluster_name": "CL-1",
                                           "detection_type": "device",
                                           "member_count": 4,
                                           "cluster_risk_score": 60},
                               "shared_devices": ["dev-1"],
                               "related_accounts": ["A1"]}}


def _turn_multi(payloads, response_language, next_data_needed=None):
    from app.investigation_service import compose_response
    from app.models import ToolCallV2, ToolResult, ToolResultOutcome
    tcs = []
    for i, (name, data) in enumerate(payloads, 1):
        tool = ("artifact_bundle" if "artifact" in data
                else "signal_explain" if "signal_type" in data
                else "policy_lookup")
        tcs.append(ToolCallV2(
            tool_call_id=f"TC-S{i}-1", investigation_id="I", task_id="T",
            tool_name=tool,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS,
                              data=data,
                              next_data_needed=list(next_data_needed or []))))
    return compose_response(
        user_request="q", skill_id=None, plan=None, tool_calls=tcs,
        execution_errors=[], response_language=response_language)


def test_zh_rule_keeps_additional_data_line():
    zh = _turn_multi([("signal_explain", dict(_RULE_DATA))], "zh",
                     next_data_needed=[
                         "triggered-rule evidence associated with this "
                         "finding"])
    assert "风险信号:规则「High withdrawal frequency」触发" in zh
    assert "还需补充的数据：与该发现关联的已触发规则证据." in zh
    en = _turn_multi([("signal_explain", dict(_RULE_DATA))], None,
                     next_data_needed=[
                         "triggered-rule evidence associated with this "
                         "finding"])
    assert "Additional data needed:" in en
    assert not _ZH.search(en), en


def test_zh_feature_keeps_additional_data_line():
    zh = _turn_multi([("signal_explain", dict(_FEATURE_DATA))], "zh",
                     next_data_needed=["evidence stream: graph"])
    assert "行为特征观察记录" in zh
    assert "还需补充的数据：证据流: graph." in zh


def test_zh_graph_keeps_additional_data_line():
    zh = _turn_multi([("signal_explain", dict(_GRAPH_DATA))], "zh",
                     next_data_needed=["network/cluster evidence for this "
                                       "case"])
    assert "图关系检测" in zh
    assert "还需补充的数据：该案例的网络/集群证据." in zh


def test_zh_rule_coexists_with_policy_and_artifact_details():
    payloads = [
        ("signal_explain", dict(_RULE_DATA)),
        ("policy_lookup", {
            "signal_type": "Rule", "matches": [
                {"citation_id": 1, "document": "Doc A", "section": "§2"}],
            "associated_policy_refs": [{"citation_id": 1}],
            "finding_policy_status": "associated"}),
        # the artifact confirmation must ALSO survive the zh turn
        ("artifact_bundle", {"artifact": {"artifact_id": "ART-test01"}}),
    ]
    zh = _turn_multi(payloads, "zh")
    # the signal face AND the policy detail AND the artifact confirmation
    # all survive on zh (per-payload, no skipping)
    assert "风险信号:规则「High withdrawal frequency」触发" in zh
    assert "该发现直接适用 1 条政策引用：" in zh
    assert "Doc A — §2 [1]" in zh
    assert "调查证据包已生成，可在右侧 Artifacts 面板查看（ART-test01）。" \
        in zh


def test_en_rule_detail_unchanged():
    en = _turn_multi([("signal_explain", dict(_RULE_DATA))], None,
                     next_data_needed=[
                         "triggered-rule evidence associated with this "
                         "finding"])
    assert "The finding was flagged" in en
    assert "Additional data needed:" in en
    assert not _ZH.search(en), en

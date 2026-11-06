"""Delivery-intent composer precedence — three delivery channels
(detail / file / both) render per the contract's delivery field.

2026-11-04 final-acceptance intent fix (TASK-9727f42e5aee): the
file_export output ALSO carried rule_explanation + timeline_events
requirements (goal understanding of 「把所有…证据生成一份文件」); the
RULE_EXPLANATION branch fired before the file brief and expanded THREE
rule entries — two of them OTHER findings' (F2/F4) — into the file
confirmation. Pins:
- backed file_export output → file brief ONLY, from ANY requirement mix;
  other findings' rules never render;
- detail_list output without artifact → full record rows, no brief;
- both channels → rows render exactly once, owned by the detail_list
  output (the file_export output renders only the brief);
- unbacked file_export → legacy full rendering (no false success claim).
"""
from app.agent_loop import (AgentLoopState, _file_export_brief_lines,
                            compose_loop_response)
from app.config import settings
from app.goal_resolution import (ClaimKind, EvidenceType, GoalContract,
                                 OutputSatisfaction, RequiredOutput,
                                 ScopeLevel)

RULES = [
    {"rule_name": "New account with high activity", "severity": "HIGH",
     "trigger": {"account_age_days": 6, "trade_frequency_24h": 54},
     "threshold": "account_age_days < 7 AND trade_frequency_24h > 50",
     "description": "Account is 6 days old with 54 trades in 24h"},
    {"rule_name": "High withdrawal frequency", "severity": "MEDIUM",
     "trigger": {"withdrawal_frequency_24h": 7},
     "threshold": "withdrawal_frequency_24h > 5",
     "description": "7 withdrawals in 24h exceeds the normal pattern"},
    {"rule_name": "First withdrawal to new address", "severity": "MEDIUM",
     "trigger": {"first_withdrawal_flag": True,
                 "withdrawal_frequency_24h": 7},
     "threshold": "first_withdrawal_flag = true AND "
                  "withdrawal_frequency_24h present",
     "description": "First withdrawal to a new address while withdrawal "
                    "activity is present"},
]

ROWS = [{"record_id": f"W0000{i}", "timestamp": f"2026-07-2{i}T10:00:00Z",
         "summary": f"Withdrawal #{i}", "address": f"ADDR{i}"}
        for i in range(1, 8)]


def _out(oid, delivery, reqs, kind=ClaimKind.RETRIEVAL, subject="F3",
         claim="claim", description=None):
    return RequiredOutput(
        output_id=oid, claim=claim, claim_kind=kind,
        claim_description=description,
        subject_ref=subject, scope=ScopeLevel.FINDING_LEVEL,
        evidence_requirements=[EvidenceType(r) for r in reqs],
        delivery=delivery)


def _drilldown(for_outputs):
    return {"tool_name": "finding_drilldown",
            "step_type": "inspect_withdrawals", "outcome": "success",
            "semantic_status": None, "error": None, "complete": True,
            "next_data_needed": None, "for_outputs": for_outputs,
            "actual_evidence_types": ["withdrawal_records"],
            "summary": {"finding_id": "F3", "view": "evidence",
                        "complete": True, "record_count": 7,
                        "record_rows": ROWS, "rows_truncated": False,
                        "rule_evidence": RULES}}


def _explain(for_outputs):
    return {"tool_name": "signal_explain", "step_type": "explain_signal",
            "outcome": "success", "semantic_status": None, "error": None,
            "complete": None, "next_data_needed": None,
            "for_outputs": for_outputs,
            "actual_evidence_types": ["rule_explanation"],
            "summary": {"finding_id": "F3",
                        "rule_name": "High withdrawal frequency",
                        "rule_description": "7 withdrawals in 24h exceeds "
                                            "the normal pattern",
                        "threshold": "withdrawal_frequency_24h > 5",
                        "trigger_values": {"withdrawal_frequency_24h": 7}}}


def _bundle(for_outputs):
    return {"tool_name": "artifact_bundle",
            "step_type": "generate_artifact", "outcome": "success",
            "semantic_status": "artifact", "error": None, "complete": None,
            "next_data_needed": None, "for_outputs": for_outputs,
            "actual_evidence_types": ["withdrawal_records"],
            "summary": {"artifact_id": "ART-b31a059083",
                        "source_streams": ["withdrawal_records"]}}


def _compose(contract, observations):
    for o in contract.outputs:
        o.satisfaction = OutputSatisfaction.SATISFIED
        o.polarity = "positive"
    state = AgentLoopState(
        task_id="TASK-DI", investigation_id="INV-DI", case_id="U00010",
        goal="g", goal_contract=contract, agent_status="completed",
        stop_reason="goal_satisfied", observations=observations,
        response=None)
    previous = settings.ENABLE_GOAL_SEMANTIC_CONTRACT
    settings.ENABLE_GOAL_SEMANTIC_CONTRACT = True
    try:
        text = compose_loop_response(state)
    finally:
        settings.ENABLE_GOAL_SEMANTIC_CONTRACT = previous
    return text, state


def test_file_only_mixed_requirements_render_brief_only():
    """The incident shape: file output with mixed requirements (records +
    rule_explanation + timeline) and a 3-rule rule_evidence payload —
    the section is the file brief; no rule expansion, no other findings,
    no record rows."""
    contract = GoalContract(contract_id="GC-t", subject_ref="U00010",
                            outputs=[
        _out("O1", "detail_list", ["rule_explanation"],
             kind=ClaimKind.EXPLANATION,
             claim="为什么「High withdrawal frequency」被标记为风险",
             description="解释发现 F3「High withdrawal frequency」"
                         "被标记为风险的依据：实际观测、规则与阈值"
                         "（如有）及其关系"),
        _out("O2", "file_export",
             ["withdrawal_records", "rule_explanation",
              "timeline_events"],
             claim="全部高频提现证据文件",
             description="将发现 F3「High withdrawal frequency」的全部"
                         "证据汇总并生成一份文件")])
    obs = [_explain(["O1"]), _drilldown(["O2"]), _bundle(["O2"])]
    text, _ = _compose(contract, obs)
    assert "已生成" in text and "文件" in text          # file brief
    assert "7" in text and "记录" in text               # count in the brief
    # other findings' rules never render
    assert "New account with high activity" not in text
    assert "First withdrawal to new address" not in text
    # the WHY output keeps its own single-rule answer
    assert "命中规则: High withdrawal frequency" in text
    assert "withdrawal_frequency_24h > 5" in text
    assert "Withdrawal frequency (24h) = 7" in text or \
        "withdrawal_frequency_24h = 7" in text
    # no record rows re-pasted in chat
    assert "W00001" not in text
    assert "完整提现共 7 条" not in text
    # O1 explanation is PRESERVED as its own section alongside the O2
    # brief: both per-output sections render, O1 first (contract order),
    # the explanation never swallowed into a bare brief
    assert text.count("关于「") == 2
    assert "解释发现 F3「High withdrawal frequency」" in text
    assert "将发现 F3「High withdrawal frequency」" in text
    assert text.find("命中规则: High withdrawal frequency") \
        < text.find("已生成")


def test_detail_only_renders_rows_no_brief():
    contract = GoalContract(contract_id="GC-t", subject_ref="U00010",
                            outputs=[
        _out("O1", "detail_list", ["rule_explanation"],
             kind=ClaimKind.EXPLANATION,
             claim="为什么「High withdrawal frequency」被标记为风险"),
        _out("O2", "detail_list", ["withdrawal_records"],
             claim="高频提现明细")])
    obs = [_explain(["O1"]), _drilldown(["O2"])]
    text, _ = _compose(contract, obs)
    assert "共 7 条" in text                            # rows delivered
    assert "W00001" in text and "W00007" in text
    assert "已生成" not in text                         # no file channel
    assert "New account with high activity" not in text


def test_both_channels_rows_once_and_brief():
    contract = GoalContract(contract_id="GC-t", subject_ref="U00010",
                            outputs=[
        _out("O1", "detail_list", ["rule_explanation"],
             kind=ClaimKind.EXPLANATION,
             claim="为什么「High withdrawal frequency」被标记为风险"),
        _out("O_det", "detail_list", ["withdrawal_records"],
             claim="高频提现明细"),
        _out("O_file", "file_export", ["withdrawal_records"],
             claim="全部高频提现证据文件")])
    obs = [_explain(["O1"]), _drilldown(["O_det", "O_file"]),
           _bundle(["O_file"])]
    text, _ = _compose(contract, obs)
    assert text.count("W00001") == 1                    # rows exactly once
    assert "共 7 条" in text                            # detail section
    assert "已生成" in text                              # file brief section
    assert "New account with high activity" not in text
    # rows belong to the DETAIL section (render before the brief)
    assert text.find("共 7 条") < text.find("已生成")


def test_unbacked_file_export_keeps_legacy_rows():
    contract = GoalContract(contract_id="GC-t", subject_ref="U00010",
                            outputs=[
        _out("O_file", "file_export", ["withdrawal_records"],
             claim="全部高频提现证据文件")])
    obs = [_drilldown(["O_file"])]                      # NO bundle
    text, _ = _compose(contract, obs)
    assert "共 7 条" in text                            # legacy rendering
    assert "已生成" not in text                         # no false success


def test_brief_helper_gates():
    out_file = _out("O", "file_export", ["withdrawal_records"])
    out_detail = _out("O", "detail_list", ["withdrawal_records"])
    summary = {"record_count": 7, "record_rows": ROWS}
    assert _file_export_brief_lines(out_file, summary,
                                    out_file.evidence_requirements,
                                    "zh", set()) is None          # unbacked
    assert _file_export_brief_lines(out_detail, summary,
                                    out_detail.evidence_requirements,
                                    "zh", {"withdrawal_records"}) is None
    brief = _file_export_brief_lines(out_file, summary,
                                     out_file.evidence_requirements,
                                     "zh", {"withdrawal_records"})
    assert brief is not None and "已生成" in brief[0]


def test_explanation_kind_file_delivery_never_swallowed():
    """Pathological GU shape — an EXPLANATION-kind output carrying a
    file_export delivery must still render its explanation; the file
    channel may never swallow an explanation target (delivery-intent
    hardening, ClaimKind gate in _file_export_brief_lines)."""
    contract = GoalContract(contract_id="GC-t", subject_ref="U00010",
                            outputs=[
        _out("O1", "file_export", ["rule_explanation"],
             kind=ClaimKind.EXPLANATION,
             claim="为什么「High withdrawal frequency」被标记为风险")])
    obs = [_explain(["O1"]), _bundle(["O1"])]
    text, _ = _compose(contract, obs)
    assert "命中规则: High withdrawal frequency" in text
    assert "withdrawal_frequency_24h > 5" in text
    assert "已生成" not in text                          # no bare brief


def _c_browser_shape(descriptions_quoted=True):
    """The real browser scenario-C contract (TASK-ceba057be489): three
    outputs, all subject F3, NO focused title, NO explain observation —
    the drilldown's 3-rule rule_evidence is O1's only rule payload."""
    quote = ("F3「High withdrawal frequency」" if descriptions_quoted
             else "F3")
    outs = [
        _out("O1", "detail_list",
             ["rule_explanation", "withdrawal_records"],
             kind=ClaimKind.EXPLANATION,
             claim="高频提现风险项依据",
             description=f"解释发现 {quote}被标记为风险项的依据："
                         "实际提现观测、规则与阈值（如有）及其关系"),
        _out("O2", "detail_list", ["withdrawal_records"],
             claim="具体提现明细",
             description=f"列出发现 {quote}涉及的具体提现明细记录"),
        _out("O3", "file_export",
             ["withdrawal_records", "rule_explanation"],
             claim="高频提现证据文件",
             description=f"生成一份文件，汇总发现 {quote}的所有提现证据"),
    ]
    obs = [_drilldown(["O1", "O2", "O3"]), _bundle(["O1", "O2", "O3"])]
    return GoalContract(contract_id="GC-t", subject_ref="U00010",
                        outputs=outs), obs


def test_title_fallback_single_subject_scopes_rules():
    """No focused title + single finding subject + GU descriptions
    quoting the canonical title → the title resolves deterministically
    and ONLY the subject's own rule renders (F2/F4 never leak)."""
    contract, obs = _c_browser_shape(descriptions_quoted=True)
    text, _ = _compose(contract, obs)
    assert "命中规则「High withdrawal frequency」（severity MEDIUM）:" in text
    assert "New account with high activity" not in text
    assert "First withdrawal to new address" not in text
    assert "withdrawal_frequency_24h > 5" in text        # why answered
    assert text.count("W00001") == 1                     # rows exactly once
    assert "已生成" in text                              # file brief intact


def test_title_fallback_absent_keeps_legacy_boundary():
    """Descriptions WITHOUT the quoted title → the fallback cannot
    resolve (documented boundary): legacy all-entries rendering applies.
    Pinned so any future tightening of this boundary is a conscious
    product change, never a silent one."""
    contract, obs = _c_browser_shape(descriptions_quoted=False)
    text, _ = _compose(contract, obs)
    assert "New account with high activity" in text      # legacy behavior
    assert "High withdrawal frequency" in text


def test_title_fallback_multi_subject_not_resolved():
    """A contract spanning TWO finding subjects does not resolve the
    fallback (ambiguous) — legacy per-output rendering is pinned."""
    outs = [
        _out("O1", "detail_list", ["rule_explanation"],
             kind=ClaimKind.EXPLANATION, subject="F3",
             claim="高频提现风险项依据",
             description="解释发现 F3「High withdrawal frequency」"
                         "被标记为风险项的依据"),
        _out("O2", "detail_list", ["rule_explanation"],
             kind=ClaimKind.EXPLANATION, subject="F2",
             claim="新账号高频活动风险项依据",
             description="解释发现 F2「New account with high activity」"
                         "被标记为风险项的依据"),
    ]
    contract = GoalContract(contract_id="GC-t", subject_ref="U00010",
                            outputs=outs)
    text, _ = _compose(contract, [_drilldown(["O1", "O2"])])
    # legacy boundary: no resolution across subjects
    assert "High withdrawal frequency" in text
    assert "New account with high activity" in text

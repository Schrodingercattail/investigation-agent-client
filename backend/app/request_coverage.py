"""Turn-path request-coverage verification (deterministic).

The GoalContract machinery verifies per-output coverage on the Agent
Loop path when a contract is accepted. The conversational TURN path has
no contract; without this layer a multi-aspect request is "completed"
whenever its tool calls succeed — even when only one of the user's
sub-questions was actually answered (tool success ≠ answered).

This module closes that hole for the BOUNDED supported request patterns,
reusing the SAME vocabulary and verdict semantics as the contract path
so the two surfaces can never disagree about what "answered" means:

- aspects are identified by the shared investigation_concepts
  predicates (+ the goal layer's comparison-intent detector) — an
  unmatched request yields NO aspects and keeps its coverage
  uncertainty (no invented coverage, no generic claims);
- verdicts are computed ONLY from this run's real tool payloads
  (ToolResult.data of the executed calls) — never from response-text
  keywords;
- verdict semantics are the contract path's own pure fact-verdict
  functions (goal_postconditions), including the target-anchored
  subject-membership scope check and the population-shortfall
  invariant;
- an aspect whose producing tool never executed is UNCOVERED —
  distinct from "checked, no match" and from "insufficient evidence";
- the overall outcome reuses `derive_goal_outcome` (complete / partial
  / unanswerable) — one completion semantics across surfaces.

Pure functions: no LLM, no network, no DB, no mutation.
"""

from dataclasses import dataclass, field

from app import investigation_concepts
from app.goal_postconditions import (
    _destination_block_consistent,
    _destination_facts_verdict,
    _metric_facts_verdict,
    _new_address_facts_verdict,
)
from typing import Any

from app.goal_resolution import (
    OutputSatisfaction,
    asserted_record_population,
)

# tool names whose payloads carry the verifiable facts
_TOOL_CROSS_ACCOUNT = "cross_account_trade_analysis"
_TOOL_FINDING_DRILLDOWN = "finding_drilldown"
_TOOL_RELATED_ACCOUNTS = "related_account_analysis"
_TOOL_SIGNAL_EXPLAIN = "signal_explain"

# aspect ids (stable, test-visible)
ASPECT_METRIC = "reverse_trade_metric_meaning"
ASPECT_HEDGING = "target_cross_account_hedging"
ASPECT_DESTINATION = "withdrawal_destination_identity"
ASPECT_NEW_ADDRESS = "withdrawal_new_address_status"
ASPECT_RELATED_ACCOUNTS = "case_related_account_attribution"

# pseudo-satisfaction for an aspect this run never produced evidence for
UNCOVERED = "uncovered"


@dataclass
class AspectVerdict:
    aspect_id: str
    claim: str
    satisfaction: str              # OutputSatisfaction value | UNCOVERED
    detail: str | None = None
    reason_code: str | None = None
    tool: str | None = None


def identify_aspects(request_text: str) -> list[dict]:
    """The request's supported sub-questions, in natural answer order.
    Bounded vocabulary; an unmatched request yields [] (coverage stays
    uncertain — never fabricated)."""
    text = request_text or ""
    aspects: list[dict] = []
    explain_domains = investigation_concepts.detect_explain_domains(text)
    explain_intent = explain_domains and (
        investigation_concepts.is_explain_finding_request(text)
        or investigation_concepts.is_rule_criteria_request(text))
    if explain_intent:
        aspects.append({
            "aspect_id": "risk_signal_explanations",
            "claim": ("解释每个被点名风险信号为何被标记"
                      f"（{len(explain_domains)} 个: "
                      f"{', '.join(explain_domains)}）"),
            "expected_explanations": len(explain_domains),
        })
        # a WHY ask that NAMES a domain is not a which-records ask about
        # that domain — the retrieval facet would misread the intent
        # (「提现到新地址为什么是风险项」 ≠ 「哪些提现是新地址」)
        return aspects
    if investigation_concepts.mentions_reverse_trade_rate(text):
        aspects.append({
            "aspect_id": ASPECT_METRIC,
            "claim": "反向交易率指标的含义与统计口径（自身 vs 跨账户）",
        })
    if (investigation_concepts.is_related_account_set_trade_analysis(text)
            or investigation_concepts.is_cross_account_trade_analysis_request(
                text)):
        set_internal = (
            investigation_concepts.is_related_account_set_trade_analysis(
                text))
        aspects.append({
            "aspect_id": ASPECT_HEDGING,
            "claim": ("关联账户集合内部是否存在对冲交易候选"
                      "（集合内全部账户两两配对）" if set_internal else
                      "是否存在涉及目标账户的跨账户对冲交易候选"),
        })
    if _mentions_withdrawal_comparison(text):
        aspects.append({
            "aspect_id": ASPECT_DESTINATION,
            "claim": "提现目的地地址是否相同（逐笔目的地标识比较）",
        })
    if investigation_concepts.mentions_withdrawal_new_address(text):
        aspects.append({
            "aspect_id": ASPECT_NEW_ADDRESS,
            "claim": "哪些提现被标记为新地址（逐笔 is_new_address 状态）",
        })
    if investigation_concepts.is_related_account_analysis_request(text):
        aspects.append({
            "aspect_id": ASPECT_RELATED_ACCOUNTS,
            "claim": "案件的关联账号网络归因明细（Risk Platform 网络归因）",
        })
    return aspects


def _explanation_facts_present(payload: dict) -> bool:
    """The payload carries ACTUAL explanation facts (not just an
    evidence-availability marker): a rule, an ML score, or cluster
    facts."""
    ex = payload.get("explanation") or {}
    if not isinstance(ex, dict):
        return False
    return bool(
        payload.get("rule_name")
        or (payload.get("rule") or {}).get("name")
        or ex.get("ml_score") is not None
        or (ex.get("cluster") or {}).get("cluster_name")
        or payload.get("cluster_name")
        or ex.get("available_feature_values")
        or payload.get("feature_facts")
        or ex.get("features"))


def _mentions_withdrawal_comparison(text: str) -> bool:
    """The destination same-or-different ask: the goal layer's
    comparison detector (connective AND address object) with the
    withdrawal subject required — a comparison over non-withdrawal
    objects is not this aspect."""
    from app.goal_resolution import assess_comparison_intent
    if not assess_comparison_intent(text):
        return False
    return investigation_concepts.mentions_withdrawals(text)


def verify_coverage(
    request_text: str,
    tool_payloads: list[tuple[str, dict | None]],
    expected_subject: str | None = None,
    case_findings: list | None = None,
) -> dict | None:
    """Verify every identified aspect against this run's real tool
    payloads. Returns None when the request names no supported aspect
    (coverage uncertainty preserved). Otherwise:

        {"outcome": "complete" | "partial" | "unanswerable",
         "aspects": [{aspect_id, claim, satisfaction, detail,
                      reason_code, tool}], ...}

    ``expected_subject``: the investigation's own case account. When
    set, a payload belonging to a DIFFERENT case is a scope violation —
    evidence from another case never becomes this target's answer
    (Case & Evidence Scope constraint).

    The outcome reuses the contract path's GoalOutcome derivation
    (UNCOVERED counts as not-answered, like PENDING)."""
    aspects = identify_aspects(request_text)
    if not aspects:
        return None
    by_tool: dict[str, Any] = {}
    for name, data in tool_payloads or []:
        if name in (_TOOL_CROSS_ACCOUNT, _TOOL_FINDING_DRILLDOWN,
                    _TOOL_RELATED_ACCOUNTS, "signal_explain") \
                and isinstance(data, dict):
            if name == "signal_explain":
                by_tool.setdefault(name, []).append(data)
            else:
                by_tool.setdefault(name, data)
    asserted = asserted_record_population(request_text)

    verdicts: list[AspectVerdict] = []
    for a in aspects:
        verdicts.append(_verify_aspect(a, by_tool, asserted,
                                       expected_subject,
                                       payloads_by_tool=by_tool))
    sats = [v.satisfaction for v in verdicts]
    outcome = _overall_outcome(sats)
    return {
        "outcome": outcome,
        "aspects": [v.__dict__ for v in verdicts],
    }


def _verify_aspect(aspect: dict, by_tool: Any,
                   asserted: int | None,
                   expected_subject: str | None = None,
                   payloads_by_tool: Any = None) -> AspectVerdict:
    aid = aspect["aspect_id"]

    def _verdict(satisfaction: str, detail: str | None = None,
                 reason: str | None = None, tool: str | None = None,
                 ) -> AspectVerdict:
        return AspectVerdict(aspect_id=aid, claim=aspect["claim"],
                             satisfaction=satisfaction, detail=detail,
                             reason_code=reason, tool=tool)

    if aid == ASPECT_METRIC:
        payload = by_tool.get(_TOOL_CROSS_ACCOUNT)
        if payload is None:
            return _verdict(UNCOVERED,
                            "本次运行没有执行跨账户交易分析，"
                            "无法核对指标口径。")
        v = _metric_facts_verdict(payload.get("target_account_metrics"))
        if v is None:
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                "跨账户分析已执行，但未获得该账户的权威"
                " opposite_trade_ratio 指标值。",
                "metric_facts_absent", _TOOL_CROSS_ACCOUNT)
        s, _pol, reason, completeness, detail = v
        return _verdict(s.value, detail, reason, _TOOL_CROSS_ACCOUNT)

    if aid == ASPECT_HEDGING:
        payload = by_tool.get(_TOOL_CROSS_ACCOUNT)
        if payload is None:
            return _verdict(UNCOVERED,
                            "本次运行没有执行跨账户交易分析，"
                            "未检查目标账户的对冲匹配。")
        if expected_subject and str(payload.get("case_id") or "") \
                and payload.get("case_id") != expected_subject:
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                f"分析载荷属于案件 {payload.get('case_id')}，不属于当前"
                f"调查目标 {expected_subject}——异案证据不能作为本目标"
                "的结论依据。",
                "case_scope_violation", _TOOL_CROSS_ACCOUNT)
        if expected_subject and str(payload.get("target_account") or "") \
                and payload.get("target_account") != expected_subject \
                and str(payload.get("case_id") or "") == expected_subject:
            # case matches but the anchored target differs — the payload's
            # candidates answer a different account's question
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                f"分析载荷锚定的目标账户是 {payload.get('target_account')}"
                f"，不是当前调查目标 {expected_subject}。",
                "case_scope_violation", _TOOL_CROSS_ACCOUNT)
        return _hedging_verdict(payload, _verdict)

    if aid == ASPECT_DESTINATION:
        payload = by_tool.get(_TOOL_FINDING_DRILLDOWN)
        if payload is None:
            return _verdict(UNCOVERED,
                            "本次运行没有执行提现记录明细检索，"
                            "未建立目的地比较。")
        v = _destination_facts_verdict(
            payload.get("destination_identity"), asserted)
        if v is None:
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                "提现记录已检索，但未获得可核验的目的地标识比较事实。",
                "comparison_facts_absent", _TOOL_FINDING_DRILLDOWN)
        s, _pol, reason, completeness, detail = v
        return _verdict(s.value, detail, reason, _TOOL_FINDING_DRILLDOWN)

    if aid == "risk_signal_explanations":
        # one verdict over ALL named risk-signal explanations: each must
        # have an executed signal explanation with actual explanation
        # facts (rule / ML / graph), deduplicated by explained finding.
        expected = aspect.get("expected_explanations") or 1
        payloads = (payloads_by_tool.get("signal_explain") or [])
        if not isinstance(payloads, list):
            payloads = [payloads] if payloads else []
        explained = set()
        facts = 0
        for p in payloads:
            if not isinstance(p, dict):
                continue
            fid = p.get("finding_id")
            if fid and fid not in explained \
                    and _explanation_facts_present(p):
                explained.add(fid)
                facts += 1
        if not payloads:
            return _verdict(UNCOVERED,
                            "本次运行没有执行信号解释，"
                            "无法呈现风险依据。")
        if facts >= expected:
            return _verdict(
                OutputSatisfaction.SATISFIED.value,
                f"已对 {facts} 个被点名的风险信号分别给出解释"
                "（观测事实、规则与阈值逐项呈现）。",
                "explanations_available", "signal_explain")
        return _verdict(
            OutputSatisfaction.UNRESOLVED.value,
            f"请求涉及 {expected} 个风险信号，本次仅取得 {facts} 个"
            "信号的实质性解释。",
            "explanations_partial", "signal_explain")

    if aid == ASPECT_RELATED_ACCOUNTS:
        payload = by_tool.get(_TOOL_RELATED_ACCOUNTS)
        if payload is None:
            return _verdict(UNCOVERED,
                            "本次运行没有执行关联账号网络归因分析，"
                            "未获得网络归因证据。")
        if payload.get("evidence_missing"):
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                "Risk Platform 未提供该账户的网络归因证据，"
                "无法列出关联账号。",
                "network_evidence_missing", _TOOL_RELATED_ACCOUNTS)
        accounts = payload.get("related_accounts") or []
        count = payload.get("related_accounts_count")
        if accounts:
            shown = len(accounts)
            total_txt = f"共 {count} 个" if isinstance(count, int) \
                and count >= shown else f"{shown} 个"
            return _verdict(
                OutputSatisfaction.SATISFIED.value,
                f"Risk Platform 网络归因列出{total_txt}关联账号"
                "（逐账号清单见回答）。",
                "relations_available", _TOOL_RELATED_ACCOUNTS)
        return _verdict(
            OutputSatisfaction.UNRESOLVED.value,
            "网络归因分析已执行，但未获得可列出的关联账号事实。",
            "relations_facts_absent", _TOOL_RELATED_ACCOUNTS)

    if aid == ASPECT_NEW_ADDRESS:
        payload = by_tool.get(_TOOL_FINDING_DRILLDOWN)
        if payload is None:
            return _verdict(UNCOVERED,
                            "本次运行没有执行提现记录明细检索，"
                            "未核对逐笔新地址状态。")
        v = _new_address_facts_verdict(payload.get("new_address_status"))
        if v is None:
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                "提现记录已检索，但未获得逐笔 is_new_address 权威状态。",
                "new_address_facts_absent", _TOOL_FINDING_DRILLDOWN)
        s, _pol, reason, completeness, detail = v
        return _verdict(s.value, detail, reason, _TOOL_FINDING_DRILLDOWN)

    return _verdict(UNCOVERED, "未知方面。")


def _hedging_verdict(payload: dict, _verdict) -> AspectVerdict:
    """Hedging existence verdict, scoped by the payload's analysis_scope:

    - target_account (Case-4): every candidate must contain the target
      account — subject-membership is the scope check.
    - related_account_set: candidates may involve the case account or
      only two related members — SET MEMBERSHIP (both legs within the
      analyzed set) is the scope check, and the verdict labels the
      involvement split so a related-only pair is never attributed to
      the case account.
    """
    tool = _TOOL_CROSS_ACCOUNT
    status = payload.get("analysis_status")
    subject = payload.get("target_account") or payload.get("case_id")
    candidates = payload.get("candidates") or []
    retrieval = payload.get("retrieval") or {}
    truncated = bool(retrieval.get("truncated"))
    if payload.get("analysis_scope") == "related_account_set":
        scope_accounts = {str(a) for a in
                          (payload.get("accounts_analyzed") or [])}
        outside = [c for c in candidates if isinstance(c, dict)
                   and (str(c.get("account_a")) not in scope_accounts
                        or str(c.get("account_b")) not in scope_accounts)]
        if outside:
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                f"{len(outside)} 组候选关系的账户不在本次关联账户集合内"
                "——集合外配对不能作为集合内部分析的结论。",
                "set_membership_violation", tool)
        found = payload.get("candidates_found")
        if status == "MATCH_FOUND":
            with_case = [c for c in candidates if isinstance(c, dict)
                         and subject in (str(c.get("account_a")),
                                         str(c.get("account_b")))]
            detail = (f"发现 {len(candidates)} 组集合内部候选关系"
                      f"（{len(with_case)} 组涉及案件账户 {subject}，"
                      f"{len(candidates) - len(with_case)} 组仅发生在"
                      "其他关联成员之间）。")
            if isinstance(found, int) and found > len(candidates):
                return _verdict(
                    OutputSatisfaction.PARTIALLY_SUPPORTED.value,
                    detail + (f" 受系统候选上限：共发现 {found} 组"
                              f"候选，本次列出前 {len(candidates)} 组，"
                              f"其余 {found - len(candidates)} 组未在本次"
                              "回答中交付。"),
                    "candidate_cap_partial", tool)
            if truncated:
                return _verdict(
                    OutputSatisfaction.PARTIALLY_SUPPORTED.value,
                    detail + "（集合检索不完整）", "set_scope_truncated",
                    tool)
            return _verdict(OutputSatisfaction.SATISFIED.value, detail,
                            "set_internal_candidates_found", tool)
        if status == "NO_MATCH":
            if truncated:
                return _verdict(
                    OutputSatisfaction.UNRESOLVED.value,
                    "集合交易证据检索不完整，无法就不存在集合内部候选"
                    "下结论（这不是“未发现”）。", "set_scope_truncated",
                    tool)
            return _verdict(
                OutputSatisfaction.SATISFIED.value,
                "在已完整检索的关联账户集合内部范围内，未发现符合匹配"
                "条件的对冲候选关系；该结论仅适用于本次集合与数据窗口。",
                "set_internal_no_candidates_complete", tool)
        if status == "INSUFFICIENT_DATA":
            return _verdict(
                OutputSatisfaction.UNRESOLVED.value,
                "集合交易证据检索不完整或不足，无法就不存在集合内部"
                "候选下结论（这不是“未发现”）。",
                "evidence_insufficient", tool)
        return _verdict(UNCOVERED, "集合分析未产生可核对的状态。",
                        tool=tool)
    if status == "MATCH_FOUND":
        if subject:
            violating = [c for c in candidates
                         if isinstance(c, dict) and subject
                         not in (c.get("account_a"), c.get("account_b"))]
            if violating:
                return _verdict(
                    OutputSatisfaction.UNRESOLVED.value,
                    f"{len(violating)} 组候选关系不包含目标账户 "
                    f"{subject}——这些全局匹配不能作为该账户的对冲证据，"
                    "目标账户的结论不成立。",
                    "subject_membership_violation", tool)
            n = len(candidates)
            detail = (f"发现 {n} 组涉及目标账户 {subject} 的候选跨账户"
                      "对冲交易关系（每组均包含该账户的一笔交易）。")
            if truncated:
                return _verdict(
                    OutputSatisfaction.PARTIALLY_SUPPORTED.value,
                    detail + "（检索受预算限制，覆盖不完整）",
                    "scope_truncated", tool)
            return _verdict(OutputSatisfaction.SATISFIED.value,
                            detail, "subject_present_in_relations", tool)
    elif status == "NO_MATCH":
        if truncated:
            return _verdict(
                OutputSatisfaction.PARTIALLY_SUPPORTED.value,
                "检索范围不完整，无法得出可靠结论（这不是“未发现”）。",
                "scope_truncated", tool)
        target_txt = f"涉及账户 {subject} 的" if subject else ""
        return _verdict(
            OutputSatisfaction.SATISFIED.value,
            f"在完整检索的范围内未发现{target_txt}跨账户对冲交易候选；"
            "其他账户之间的匹配不作为该账户的证据。",
            "subject_absent_complete_scope", tool)
    elif status == "INSUFFICIENT_DATA":
        return _verdict(
            OutputSatisfaction.UNRESOLVED.value,
            "交易证据检索不完整或不足，无法就不存在匹配下结论"
            "（这不是“未发现”）。",
            "evidence_insufficient", tool)
    return _verdict(UNCOVERED, "跨账户分析未产生可核对的状态。",
                    tool=tool)


def _overall_outcome(sats: list[str]) -> str:
    """One completion semantics with the contract path: reuse
    derive_goal_outcome over the aspect satisfaction values (UNCOVERED
    ranks as not-answered, like PENDING/UNRESOLVED)."""
    from app.goal_postconditions import derive_goal_outcome
    outcome = derive_goal_outcome(
        [OutputSatisfaction.PENDING.value if s == UNCOVERED else s
         for s in sats])
    return outcome.value


def coverage_lines(coverage: dict | None,
                   lang: str | None = None) -> list[str]:
    """User-safe turn-response lines for the aspects that are NOT
    determinately answered. Located and specific — never a generic
    "request may not be covered". Satisfied aspects render nothing
    here: their answers live in the payload prose above.
    lang selects the disclosure face (default/None keeps the
    historical zh face for backward compatibility)."""
    en = lang == "en"
    if not coverage:
        return []
    lines: list[str] = []
    open_items: list[tuple[str, str, str]] = []   # (state, claim, detail)
    for a in coverage.get("aspects") or []:
        sat = a.get("satisfaction")
        if sat == OutputSatisfaction.SATISFIED.value:
            continue
        state = ("not yet covered" if en and sat == UNCOVERED
                 else "尚未覆盖" if sat == UNCOVERED
                 else "insufficient evidence" if en and sat ==
                 OutputSatisfaction.UNRESOLVED.value
                 else "证据不足，无法确定" if sat ==
                 OutputSatisfaction.UNRESOLVED.value
                 else "partially covered" if en
                 else "仅部分覆盖")
        detail = a.get("detail") or ""
        open_items.append((state, a.get("claim") or "", detail))
    if not open_items:
        return lines
    lines.append("Request coverage:" if en else "请求覆盖情况：")
    for state, claim, detail in open_items:
        line = (f"- [{state}] {claim}."
                if en else f"- [{state}] {claim}。")
        if detail:
            line += f" {detail}"
        lines.append(line)
    outcome = coverage.get("outcome")
    if outcome == "partial":
        lines.append(
            "Overall status: the request was partially answered — the "
            "aspects listed above did not receive conclusions matching "
            "their evidence; answers for the remaining aspects are "
            "above." if en else
            "整体状态：请求已部分回答——上列方面未获得与证据状态相符的"
            "结论；其余方面的答案以上文为准。")
    elif outcome == "unanswerable":
        lines.append(
            "Overall status: this run could not answer the identified "
            "aspects of the request (see the per-aspect states above); "
            "the request is not claimed to be fully answered." if en else
            "整体状态：本次运行未能回答所识别的请求方面（见上列各项"
            "状态）；不宣称请求已完整回答。")
    return lines

"""InvestigationService — Week 1 end-to-end orchestration glue.

One investigation turn:

  User Request
  → Context Resolution            (ContextResolver; ambiguous/unresolved stop here)
  → eligible Skill calculation    (Skill Registry × FindingCapability — no rule duplication)
  → LLM Planning                  (PlannerV2; PlanningFailure stops here)
  → Plan Validation               (inside ExecutorV2's runtime contract check)
  → Execution                     (ExecutorV2; the service never calls tools directly)
  → Response composition          (deterministic composer over ToolResults)
  → Suggested Follow-ups          (follow-up selector, only after a valid
                                   structured result at a continuation point)
  → Context Update                (explicit: only resolution-applied focus)
  → Task persistence              (TaskStoreV2)

This module is glue only: no domain reasoning, no direct tool calls, no
second task store, no context mutation beyond what Context Resolution
established. Every failure semantic from the underlying components is
preserved distinctly (planning failure / contract failure / executor failure /
integration_error / empty / success+evidence_missing).
"""

import logging
import uuid
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.config import settings
from app.admissibility import AdmissibilityDecision
from app.context_resolution import (
    Candidate,
    ContextResolver,
    ResolutionOutcome,
    ResolutionStatus,
)
from app.executor_v2 import ExecutorV2, ExecutionResult
from app.telemetry import AgentTelemetryEvent
from app.telemetry import emit as telemetry_emit
from app.followups import (
    SelectionInput,
    TriggerReason,
    FollowUp,
    select_followups,
)
from app.models import (
    Finding,
    FocusSource,
    InvestigationContext,
    Plan,
    PlanStatus,
    TaskStatusV2,
    TaskV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2, PlanningFailure
from app.skills import SKILLS, eligible_skills_for_finding
from app.task_store_v2 import TaskStoreV2

logger = logging.getLogger(__name__)

from app.composition import (
    detection_mechanism_sentence,
    gap_confirmation_clause,
    gap_statement,
)

import re as _re

_CITATION_MARKER_RE = _re.compile(r"\s*\[\d+\]")

# Human-readable names for RP ML risk-feature fields (semantics verified in
# RP source: app/ml/features.py). Values echo verbatim; unknown keys fall
# back to their raw name rather than being invented.
_FEATURE_LABELS = {
    "withdrawal_frequency_24h": "Withdrawal frequency (24h)",
    "withdrawal_volume_24h": "Withdrawal volume (24h)",
    "withdrawal_risk_score": "Withdrawal risk score",
    "trade_frequency_24h": "Trade frequency (24h)",
    "trade_frequency_7d": "Trade frequency (7d)",
    "trade_volume_24h": "Trade volume (24h)",
    "opposite_trade_ratio": "Opposite trade ratio",
    "shared_device_count": "Shared device count",
    "linked_account_count": "Linked account count",
    "account_age_days": "Account age (days)",
}


def _strip_citation_markers(text: str) -> str:
    """Remove inline [n] citation markers from conversational text.

    §5: an Agent response may show [n] ONLY when the same response carries a
    resolvable policy mapping (the policy_lookup rendering). Artifacts keep
    their markers — they contain a Policy References section. RP's own
    authoritative markers are untouched in the domain model; this is a
    presentation filter for chat text only.
    """
    return _CITATION_MARKER_RE.sub("", text)


# Capability → human phrase for the UNSUPPORTED composer branch (P14). The
# capability gates are finding-scoped by construction, so one sentence shape
# covers them; the vocabulary mirrors the capability-guidance surface (P12)
# and follow-up chips — never internal identifiers. (policy_lookup has no
# entry by design: policy retrieval is case-wide and cannot be
# finding-unsupported — a missing finding-level basis is a DATA state,
# reported by the policy payload's finding_policy_status, not a gate.)
_CAPABILITY_PHRASES = {
    "timeline": "The timeline investigation",
    "signal_explain": "Signal explanation",
    "self_opposite_trades": "The self-opposite-trade investigation",
}
_CAPABILITY_PHRASES_ZH = {
    "timeline": "时间线调查",
    "signal_explain": "信号解释",
    "self_opposite_trades": "同账户对向交易调查",
}

# --- system-authored tool guidance: per-turn response-language face ---------
# `warnings` and `next_data_needed` are authored by OUR domain tools (a
# closed, greppable vocabulary — never Risk Platform passthrough). The
# tools stay language-neutral data producers; the TURN COMPOSER renders
# their operational guidance in the turn's language via this mapping.
# Interpolated variants are matched by anchored patterns; UNKNOWN items
# are kept verbatim (honest data, never machine-translated guesses).

_TOOL_TEXT_ZH_EXACT = {
    "Case has no data; finding cannot exist.":
        "案例无数据；该发现不可能存在。",
    "Risk Platform has no risk event / evidence for this case.":
        "Risk Platform 没有该案例的风险事件/证据。",
    "The Risk Platform holds no opposite-side trading activity data for "
        "this account.":
        "Risk Platform 没有该账户的反向交易活动数据。",
    "The Risk Platform holds no concrete withdrawal/"
        "transaction records for this finding's evidence streams; only "
        "aggregate feature values exist.":
        "Risk Platform 没有该发现证据流的提现/交易具体记录；仅存在"
        "聚合特征值。",
    "No timestamped evidence is available for this finding in the Risk "
        "Platform.":
        "Risk Platform 中该发现没有带时间戳的证据。",
    "The Risk Platform returned no policy citations for this case; no "
        "policy basis can apply to this finding.":
        "Risk Platform 未返回该案例的政策引用；不存在可适用于该发现的"
        "政策依据。",
    "Retrieval was bounded (partial set coverage); candidates are valid "
        "but absence of further set-internal matches is not established.":
        "检索受限（集合覆盖不完整）；候选有效，但不能据此认定集合内部"
        "不存在更多匹配。",
    "Retrieval was bounded (partial evidence); candidates are valid but "
        "absence of further matches is not established.":
        "检索受限（证据不完整）；候选有效，但不能据此认定不存在更多"
        "匹配。",
    "No executed tool results are available to compose an artifact from. "
        "Run an investigation step first — no artifact was fabricated.":
        "没有可用的已执行工具结果来生成证据包。请先执行一个调查步骤——"
        "未虚构任何证据包。",
    "finding-level policy association (citations attached to this "
        "finding by the Risk Platform)":
        "finding-level 政策关联（由 Risk Platform 附加到该发现的引用）",
    "triggered-rule evidence associated with this finding":
        "与该发现关联的已触发规则证据",
    "network/cluster evidence for this case":
        "该案例的网络/集群证据",
    "related-account attribution for this case":
        "该案例的关联账号归因",
    "complete (non-truncated) transaction evidence for the related-account "
        "set":
        "关联账户集合的完整（未截断）交易证据",
    "No finding-level policy association is attached to this finding.":
        "该发现未附加 finding-level 政策关联。",
}

_TOOL_TEXT_ZH_PATTERNS = [
    (_re.compile(r"^Timeline is a bounded subset: showing top_n=(\d+) of "
                r"(\d+) events at the user's explicit request "
                r"\(chronological order preserved\)\.$"),
     "时间线为有界子集：按用户明确要求显示前 {0} 条（共 {1} 个事件，"
     "保持时间顺序）。"),
    (_re.compile(r"^(\d+) candidate relationships were found; the top "
                r"(\d+) are reported \(TRADE_ANALYSIS_MAX_CANDIDATES="
                r"(\d+)\)\.$"),
     "共发现 {0} 组候选关系；报告前 {1} 组"
     "（TRADE_ANALYSIS_MAX_CANDIDATES={2}）。"),
    (_re.compile(r"^The authoritative opposite_trade_ratio metric for "
                r"account (\S+) could not be retrieved: "
                r"(\w+)\.$"),
     "未能取得账户 {0} 的权威 opposite_trade_ratio 指标：{1}。"),
    (_re.compile(r"^evidence stream: (\S+)$"),
     "证据流: {0}"),
    (_re.compile(r"^account-attributed transaction evidence for the "
                r"related-account set of (\S+)$"),
     "关联账户集合（{0}）的账户归属交易证据"),
    (_re.compile(r"^account-attributed transaction evidence for the target "
                r"account (\S+) and its connected accounts$"),
     "目标账户 {0} 及其关联账户的账户归属交易证据"),
]


def _zh_warning_face(warning: str) -> str:
    """One warning's zh presentation line. A KNOWN system guidance string
    renders its mapped zh text; an UNKNOWN warning is never passed through
    as English body text — the line says (in Chinese) that an untranslated
    system notice exists and carries the original string in an explicitly
    labeled diagnostic slot (not disguised as a translation, never
    dropped)."""
    mapped = _zh_tool_text(warning)
    if mapped != warning:
        return f"注：{mapped}"
    return f"注：存在一条尚未配置中文映射的系统提示〔原文: {warning}〕。"


def _zh_tool_text(text: str) -> str:
    """The zh face of one system-authored tool guidance string; unknown
    items are returned verbatim (tool data — never a guessed translation).
    (For warnings use _zh_warning_face, which keeps unknown EN strings out
    of the zh answer body.)"""
    t = " ".join(str(text).split())          # match over normalized spacing
    exact = _TOOL_TEXT_ZH_EXACT.get(t)
    if exact:
        return exact
    for pat, tmpl in _TOOL_TEXT_ZH_PATTERNS:
        m = pat.match(t)
        if m:
            return tmpl.format(*m.groups())
    return str(text)


def _unsupported_capability_message(
    fatal: Any, tool_calls: list[ToolCallV2],
) -> str | None:
    """Finding-scoped unsupported wording (P14: distinct states stay
    distinct). Reads the failed call's structured capability detail — set by
    the domain tools' capability gates — so 'this finding type does not
    support policy lookup' never renders as 'the platform does not support
    policy investigation' or as 'no policy was found'. Returns None when no
    structured capability scope exists (generic fallback applies)."""
    detail = (fatal.detail or {}).get("tool_call_id")
    if not detail:
        return None
    failed_tc = next(
        (tc for tc in tool_calls if tc.tool_call_id == detail), None)
    if failed_tc is None or failed_tc.result is None \
            or failed_tc.result.error is None:
        return None
    cap = (failed_tc.result.error.detail or {}).get("capability")
    phrase = _CAPABILITY_PHRASES.get(cap) if cap else None
    if not phrase:
        return None
    return (
        f"{phrase} is not available for this type of finding. Other "
        "investigation actions may still apply to this finding; nothing "
        "was fabricated."
    )


def _unsupported_capability_message_lang(
    fatal: Any, tool_calls: list[ToolCallV2], lang: str | None,
) -> str | None:
    """Per-turn response-language face of the finding-scoped unsupported
    wording (same structured capability detail; zh face for zh turns)."""
    detail = (fatal.detail or {}).get("tool_call_id")
    if not detail:
        return None
    failed_tc = next(
        (tc for tc in tool_calls if tc.tool_call_id == detail), None)
    if failed_tc is None or failed_tc.result is None \
            or failed_tc.result.error is None:
        return None
    cap = (failed_tc.result.error.detail or {}).get("capability")
    phrase = (_CAPABILITY_PHRASES_ZH.get(cap) if cap else None) \
        if lang == "zh" else (_CAPABILITY_PHRASES.get(cap) if cap else None)
    if not phrase:
        return None
    if lang == "zh":
        return (f"该发现类型不支持{phrase}。其他调查操作可能仍适用于"
                "该发现；系统未虚构任何结果。")
    return (
        f"{phrase} is not available for this type of finding. Other "
        "investigation actions may still apply to this finding; nothing "
        "was fabricated."
    )


# ---------------------------------------------------------------------------
# Response composition (deterministic; grounded in ToolResults only)
# ---------------------------------------------------------------------------

def _mentions_reverse_trade_rate(user_request: str | None) -> bool:
    """The user's own words naming the reverse-trade-rate METRIC (not
    the cross-account analysis): when the authoritative metric could not
    be retrieved, the composer says so instead of silently skipping the
    ask. Shared vocabulary — the same predicate the request-coverage
    verifier reads."""
    import app.investigation_concepts as _concepts
    return _concepts.mentions_reverse_trade_rate(user_request or "")


def _evidence_comparison_lines(data: dict,
                               user_request: str | None,
                               lang: str | None = None) -> list[str]:
    """English conclusions over the evidence view's comparison fact
    blocks — the destination-identity answer, the per-record new-address
    answer, and the metric-scope reconciliation. Verdict SHAPES reuse the
    contract path's pure fact-verdict functions (one semantics, two
    surfaces); the wording is this composer's own (English turn surface).
    Every line states its scope; a population shortfall never presents
    the retrieved subset as the asserted full population."""
    zh = lang == "zh"
    lines: list[str] = []
    from app.goal_postconditions import (
        _destination_block_consistent, _destination_facts_verdict,
        _new_address_facts_verdict)
    from app.goal_resolution import asserted_record_population

    # 1) destination-address identity (the same-or-different ask)
    di = data.get("destination_identity")
    if isinstance(di, dict) and di.get("records_total"):
        total = di["records_total"]
        asserted = asserted_record_population(user_request or "")
        shortfall = asserted is not None and asserted > total
        if shortfall:
            lines.append(
                f"口径说明：您的请求提及 {asserted} 笔提现，但权威来源"
                f"仅有 {total} 笔（已完整检索）。下方比较仅覆盖这 "
                f"{total} 条已检索记录——不构成对全部请求范围的回答。"
                if zh else
                f"Population note: your request mentions {asserted} "
                f"withdrawals, but the authoritative source holds only "
                f"{total} (completely retrieved). The comparison below "
                f"covers the {total} retrieved records only — it is NOT "
                "an answer over the full requested population.")
        if _destination_block_consistent(di):
            missing = di.get("records_without_identity") or 0
            unique = di.get("unique_destinations")
            if missing:
                lines.append(
                    f"目的地标识：{total} 条记录中有 {missing} 条缺少"
                    "目的地标识——同/异的比较仅覆盖可比较子集，而非"
                    "全集合。" if zh else
                    f"Destination identity: {missing}/{total} records "
                    "lack a recorded destination identifier — the "
                    "same-or-different comparison covers the comparable "
                    "subset only, not the whole set.")
            elif unique == 1:
                lines.append(
                    f"目的地标识：全部 {total} 笔提现指向同一目的地"
                    "（记录中的地址标识一致）。" if zh else
                    f"Destination identity: all {total} withdrawals "
                    "point to the SAME destination (identical recorded "
                    "address identifiers).")
            else:
                groups = di.get("groups") or []
                # per-record mapping lists EVERY group — a truncated
                # mapping would silently drop records from a per-record
                # deliverable (these populations are bounded by the
                # drilldown's complete-record-set contract)
                per_record = "；".join(
                    f"{(g.get('record_ids') or ['?'])[0]}→"
                    f"{g.get('destination_ref')}"
                    for g in groups)
                lines.append(
                    f"目的地标识：{total} 笔提现指向 {unique} 个不同"
                    f"目的地（记录中的地址标识；{per_record}）。"
                    "并非同一个共享目的地。" if zh else
                    f"Destination identity: the {total} withdrawals go "
                    f"to {unique} DIFFERENT destinations (recorded "
                    f"address identifiers; {per_record}). Not a single "
                    "shared destination.")

    # 2) per-record is_new_address status (the which-records ask)
    na = data.get("new_address_status")
    if isinstance(na, dict) and na.get("records_total"):
        total = na["records_total"]
        true_ids = [r.get("record_id") for r in (na.get("records") or [])
                    if r.get("is_new_address") is True]
        false_ids = [r.get("record_id") for r in (na.get("records") or [])
                     if r.get("is_new_address") is False]
        missing_n = na.get("missing_count") or 0
        shown = ", ".join(str(i) for i in true_ids)
        false_shown = ", ".join(str(i) for i in false_ids)
        if missing_n:
            lines.append(
                f"新地址状态：{total} 条记录中有 {missing_n} 条没有权威的 "
                "is_new_address 标记——逐笔状态仅对其余记录成立。"
                if zh else
                f"New-address status: {missing_n}/{total} records have "
                "no authoritative is_new_address marker — the per-record "
                "status is established for the remainder only.")
        elif not false_ids:
            lines.append(
                f"新地址状态：Risk Platform 将全部 {total} 笔提现标记为"
                f"新地址（{shown}）。" if zh else
                f"New-address status: ALL {total} withdrawals are marked "
                f"as new addresses by the Risk Platform ({shown}).")
        else:
            lines.append(
                f"新地址状态：{len(true_ids)}/{total} 笔提现被标记为"
                f"新地址（{shown}）；{len(false_ids)} 笔不是"
                f"（{false_shown}）。" if zh else
                f"New-address status: {len(true_ids)}/{total} withdrawals "
                f"are marked as new addresses ({shown}); "
                f"{len(false_ids)} are not "
                f"({false_shown}).")

    # 3) metric-scope reconciliation (summary metric vs exposed records)
    pr = data.get("population_reconciliation")
    if isinstance(pr, dict):
        ref = str(pr.get("reference_time") or "")
        for m in pr.get("metrics") or []:
            if not isinstance(m, dict):
                continue
            if m.get("type") == "window_count":
                noun = ("withdrawal" if "withdrawal" in str(m.get("metric"))
                        else "transaction")
                noun_zh = ("提现" if "withdrawal" in str(m.get("metric"))
                           else "交易")
                if m.get("verdict") == "population_mismatch":
                    lines.append(
                        f"⚠ 口径不一致：摘要指标声称 {m.get('claimed')} 笔"
                        f"{noun_zh}/24 小时，但参考时点（{ref}）前的 24 "
                        f"小时窗口重算为 {m.get('observed_in_window')}——"
                        f"记录清单为 {m.get('exposed_total')} 笔。差异已"
                        "记录，不作解释性消除。" if zh else
                        f"⚠ Scope mismatch: the summary metric claims "
                        f"{m.get('claimed')} {noun}/24h, but the 24h "
                        f"window before the reference time ({ref}) "
                        f"recomputes to {m.get('observed_in_window')} — "
                        f"the record list holds {m.get('exposed_total')}. "
                        "The discrepancy is recorded, not explained away.")
                else:
                    lines.append(
                        f"指标口径：“{m.get('claimed')} 笔{noun_zh}/"
                        "24 小时”的摘要按特征参考时点前的 24 小时窗口"
                        f"计得（{ref}；窗口内 {m.get('observed_in_window')} "
                        f"笔）；下方记录清单覆盖全部 {m.get('exposed_total')} "
                        f"笔{noun_zh}（{m.get('outside_window')} 笔在评分"
                        "窗口之前）。两个口径不同、均可核验——任一数字都"
                        "不能替代另一者。" if zh else
                        f"Metric scope: the “{m.get('claimed')} "
                        f"{noun}/24h” summary counts the 24h window "
                        f"before the feature reference time ({ref}; "
                        f"{m.get('observed_in_window')} in window); the "
                        f"record list below spans all "
                        f"{m.get('exposed_total')} {noun}s "
                        f"({m.get('outside_window')} outside the scoring "
                        "window). Two different scopes, both verifiable "
                        "— neither number substitutes for the other.")
            elif m.get("type") == "ratio_recompute":
                if m.get("verdict") == "claim_supported":
                    lines.append(
                        f"指标口径：摘要比例 {m.get('claimed')} 经记录重算"
                        f"证实（{m.get('observed')}，分母为全部 "
                        f"{m.get('denominator')} 笔交易）。" if zh else
                        f"Metric scope: the summary ratio "
                        f"{m.get('claimed')} is confirmed by recomputing "
                        f"from the records ({m.get('observed')}, "
                        f"denominator all {m.get('denominator')} "
                        "transactions).")
                else:
                    lines.append(
                        f"⚠ 口径不一致：摘要比例 {m.get('claimed')} 与"
                        f"记录重算 {m.get('observed')} 不符（分母 "
                        f"{m.get('denominator')} 笔）；已记录，不作"
                        "解释性消除。" if zh else
                        f"⚠ Scope mismatch: the summary ratio "
                        f"{m.get('claimed')} does not match the record "
                        f"recomputation {m.get('observed')} (denominator "
                        f"{m.get('denominator')}); recorded, not "
                        "explained away.")
    return lines


def compose_response(
    *,
    user_request: str,
    skill_id: str | None,
    plan: Plan | None,
    tool_calls: list[ToolCallV2],
    execution_errors: list[Any],
    planning_failure: PlanningFailure | None = None,
    outcome_status: str | None = None,
    clarification_message: str | None = None,
    response_language: str | None = None,
) -> str:
    """Transform structured results into a concise human-readable response.

    Rules: never invent facts/timestamps/entities; preserve evidence and
    citation references; surface evidence_missing explicitly; keep empty and
    integration failure distinct; no new domain reasoning.
    """
    if clarification_message:
        # context-resolution guidance — already per-turn bilingual at its
        # own generation site
        return clarification_message

    zh = response_language == "zh"

    if planning_failure is not None:
        # Bounded honesty (P14): each planning failure keeps its semantic
        # meaning in human-readable language. No Pydantic/schema text, no
        # LLM_OUTPUT_INVALID internals, no skill/step identifiers.
        code = planning_failure.code
        if code == "LLM_OUTPUT_INVALID":
            # The request could not be mapped onto the supported
            # investigation workflow — most commonly because it lies
            # outside it. Say what the system CAN do instead.
            return ("我可以协助调查当前 Risk Platform 案例，但无法将该请求"
                    "映射到受支持的调查操作。您可以询问案例、发现、时间线、"
                    "证据或适用政策。"
                    if zh else
                    "I can help investigate the current Risk Platform "
                    "case, but I couldn't map that request to a supported "
                    "investigation action. You can ask about the case, a "
                    "finding, its timeline, its evidence, or the policies "
                    "that apply.")
        if code == "LLM_EMPTY_RESPONSE":
            return ("模型多次返回空响应，无法为该请求建立计划。请稍后重试——"
                    "若持续出现，模型服务可能暂时降级。"
                    if zh else
                    "The model returned empty responses twice, so this "
                    "request could not be planned. Please try again in a "
                    "moment — if it persists, the model endpoint may be "
                    "temporarily degraded.")
        if code == "LLM_TIMEOUT":
            return ("规划请求超时，该请求未能开始。请稍后重试。"
                    if zh else
                    "The planning request timed out, so this request "
                    "could not be started. Please try again in a moment.")
        if code == "LLM_RATE_LIMITED":
            return ("规划服务被限流，该请求未能开始。请稍等片刻后重试。"
                    if zh else
                    "The planning service was rate limited, so this "
                    "request could not be started. Please try again "
                    "after a short delay.")
        if code == "LLM_CONFIGURATION_ERROR":
            return ("规划服务配置有误（认证或配置），该请求未能开始。"
                    "需要运维处理。"
                    if zh else
                    "The planning service is misconfigured (authentication "
                    "or configuration), so this request could not be "
                    "started. This needs operator attention.")
        if code == "LLM_UNAVAILABLE":
            return ("规划服务暂时不可用，该请求未能开始。请稍后重试——"
                    "调查本身不受影响。"
                    if zh else
                    "The planning service is temporarily unavailable, so "
                    "this request could not be started. Please try again "
                    "in a moment — the investigation is unaffected.")
        if code == "PLANNING_UNEXPECTED_ERROR":
            return ("规划过程中发生意外错误，该请求未能开始。请重试——"
                    "调查本身不受影响。"
                    if zh else
                    "An unexpected error occurred during planning, so "
                    "this request could not be started. Please try again "
                    "— the investigation is unaffected.")
        if code == "NO_ELIGIBLE_SKILL":
            return ("该请求不匹配当前上下文可用的调查操作。您可以询问案例、"
                    "发现、时间线、证据或适用政策。"
                    if zh else
                    "This request doesn't match an investigation action "
                    "available for the current context. You can ask about "
                    "the case, a finding, its timeline, its evidence, or "
                    "the policies that apply.")
        if code == "UNSUPPORTED_REQUEST":
            return ("该请求需要先聚焦一个具备相应能力的发现。请先在发现面板"
                    "中选择一个发现，然后重试。"
                    if zh else
                    "This request needs a focused finding with the right "
                    "capabilities. Select a finding from the Findings "
                    "panel first, then ask again.")
        # Unknown planning failure: bounded and readable, no internals.
        return ("无法将该请求规划为调查操作。您可以询问案例、发现、时间线、"
                "证据或适用政策。"
                if zh else
                "I couldn't plan this request as an investigation action. "
                "You can ask about the case, a finding, its timeline, its "
                "evidence, or the policies that apply.")

    fatal = next((e for e in execution_errors if e is not None), None)
    if fatal is not None:
        # Bounded honesty (P14): the user-visible wording must reflect the
        # SEMANTIC failure, not the runtime mechanism. The executor records
        # the underlying ToolResult outcome in the error detail; distinct
        # states get distinct statements. No step IDs, tool names, skill
        # names, or exception text reach the user.
        outcome = (fatal.detail or {}).get("outcome")
        if outcome == "unsupported":
            # Capability-scoped meaning first (P14): a finding-level gate is
            # never rendered as a platform-level gap, and never as "no policy
            # found". Falls back to the generic wording when the failed call
            # carries no structured capability scope.
            scoped = (_unsupported_capability_message_lang(
                fatal, tool_calls, "zh" if zh else None)
                if zh else _unsupported_capability_message(fatal, tool_calls))
            if scoped:
                return scoped
            return ("当前目标不支持该调查能力。未执行任何调查步骤；系统未"
                    "虚构任何结果。" if zh else
                    "This investigation capability is not supported for "
                    "the current target. No investigation step was executed; "
                    "nothing was fabricated.")
        if outcome == "validation_error":
            return ("该请求无法按所述完成——它不符合本调查可执行的范围。"
                    "系统未虚构任何结果。" if zh else
                    "The request could not be completed as specified — it "
                    "does not match what this investigation can execute. "
                    "Nothing was fabricated.")
        if outcome == "integration_error":
            return ("无法连接 Risk Platform，该调查步骤未能完成。任务已按"
                    "失败记录；系统未虚构任何结果。请稍后重试。"
                    if zh else
                    "The Risk Platform could not be reached, so this "
                    "investigation step could not be completed. The task is "
                    "recorded as failed; nothing was fabricated. Please try "
                    "again later.")
        if outcome == "empty":
            # A required step genuinely found nothing; the per-payload EMPTY
            # handling above does not apply because the step chain aborted.
            return ("Risk Platform 未返回与该请求匹配的结果。任务已按空结果"
                    "完成记录；系统未虚构任何结果。"
                    if zh else
                    "The Risk Platform returned no matching results for "
                    "this request. The task is recorded as completed with "
                    "an empty result; nothing was fabricated.")
        if fatal.code == "TOOL_NOT_IMPLEMENTED":
            return ("当前系统不具备该调查能力。未执行任何操作；系统未虚构"
                    "任何结果。" if zh else
                    "This investigation capability is not available in the "
                    "current system. Nothing was executed; nothing was "
                    "fabricated.")
        if fatal.code == "PLAN_CONTRACT_VIOLATION":
            return ("该调查请求无法在受支持的调查范围内建立计划。未执行任何"
                    "操作。" if zh else
                    "The requested investigation could not be planned "
                    "within the supported investigation surface. Nothing "
                    "was executed.")
        # Unknown/unclassified executor failure: bounded, semantic, and
        # free of internal identifiers.
        return ("该调查步骤未能完成。任务已按失败记录；系统未虚构任何结果。"
                if zh else
                "This investigation step could not be completed. The task "
                "is recorded as failed; nothing was fabricated.")

    if not tool_calls:
        return ("该请求未能执行任何调查步骤。" if zh else
                "No investigation step could be executed for this request.")

    # Human-readable prose first (grounded strictly in ToolResult data),
    # structured supporting detail second.
    prose: list[str] = []
    lines: list[str] = []

    # P5 response-ownership rule, keyed on what the user ASKED for:
    # - ARTIFACT request turn: fetch_case may run as an internal
    #   prerequisite (supplying the findings the bundle composes from),
    #   but its payload must NOT replay the intake summary — the artifact
    #   confirmation owns the conversational answer.
    # - CASE INTAKE turn (accepted product behavior): intake generates the
    #   case-scoped artifact AND the intake summary owns the conversational
    #   answer; the artifact confirmation is a valid additional output.
    from app.context_resolution import _classify_request_intent
    from app.models import RequestIntent
    artifact_requested = _classify_request_intent(user_request) in (
        RequestIntent.ARTIFACT_CASE, RequestIntent.ARTIFACT_FINDING)
    artifact_generated = any(
        tc.result is not None
        and isinstance((tc.result.data or {}).get("artifact"), dict)
        for tc in tool_calls
        if tc.result is not None and isinstance(tc.result.data, dict)
    )

    for tc in tool_calls:
        result = tc.result
        if result is None:
            continue

        if result.outcome == ToolResultOutcome.SUCCESS:
            data = result.data if isinstance(result.data, dict) else {}

            # ---- human-readable prose per payload kind ------------------
            if artifact_requested and artifact_generated \
                    and data.get("findings") is not None:
                # internal prerequisite fetch for artifact generation:
                # no intake prose, no finding list below (P5).
                continue
            if data.get("findings") is not None and not prose:
                findings = data.get("findings") or []
                case_id = data.get("case_id") or ""
                n = len(findings)
                prose.append(
                    f"案例 {case_id} 共识别 {n} 个风险发现。请在左侧发现"
                    "面板中选择一个发现以继续调查。"
                    if zh else
                    f"{n} finding{'s were' if n != 1 else ' was'} identified "
                    f"for case {case_id}. Select a finding from the Findings "
                    "panel on the left to continue the investigation."
                )
            elif data.get("view") == "evidence" and data.get("complete"):
                n = data.get("record_count", len(data.get("records") or []))
                streams = data.get("streams") or {}
                kinds = [
                    k for k, on in (
                        ("withdrawal", streams.get("withdrawals_included")),
                        ("transaction", streams.get("transactions_included")),
                    ) if on
                ]
                kind_txt = " and ".join(kinds) if kinds else "matching"
                if response_language == "zh":
                    kind_zh = {"withdrawal": "提现", "transaction": "交易"}
                    kinds_zh = "与".join(
                        kind_zh.get(k, k) for k in kinds) if kinds else "匹配"
                    prose.append(
                        f"Risk Platform 返回了 {n} 条支持该发现的"
                        f"{kinds_zh}记录,全部记录如下所示。"
                    )
                else:
                    prose.append(
                        f"The Risk Platform returned {n} {kind_txt} record"
                        f"{'s' if n != 1 else ''} supporting this finding. "
                        "All records are shown below."
                    )
                # Request-coverage closure: the conclusions the records
                # themselves carry. A record list alone does not answer a
                # same-or-different ask, a which-records ask, or a metric
                # scope mismatch — each gets its own explicit conclusion
                # (one dimension never stands in for another).
                prose.extend(_evidence_comparison_lines(
                    data, user_request,
                    lang="zh" if zh else None))
                # §3/§20: a concrete-record answer carries records only —
                # risk features belong to signal/feature-explanation scope.
            elif data.get("view") == "self_opposite_trades":
                # SAME-ACCOUNT opposite-side composition (canonical self
                # view): the ratio and rule are Risk Platform aggregates
                # echoed verbatim. Wording names the SAME account; never
                # cross-account terminology, never an intent claim.
                ratio = data.get("opposite_trade_ratio")
                if ratio is None:
                    prose.append(
                        "Risk Platform 未报告该账户的反向交易比例，但其"
                        "支持的交易记录如下所示。"
                        if zh else
                        "The Risk Platform does not report an opposite-"
                        "trade ratio for this account, but its supporting "
                        "transaction records are shown below."
                    )
                elif ratio == 0:
                    prose.append(
                        "该账户未观察到反向交易活动：Risk Platform 的"
                        "反向交易比例在其交易流上为 0%。"
                        if zh else
                        "No opposite-side trading activity was observed "
                        "for this account: the Risk Platform's opposite-"
                        "trade ratio is 0% over its trade stream."
                    )
                else:
                    prose.append(
                        "Risk Platform 显示该账户自身的反向交易活动："
                        f"其交易流上的反向交易比例为 {ratio:.2%}"
                        "（Risk Platform 聚合计算）。"
                        if zh else
                        "The Risk Platform shows opposite-side trading "
                        "activity by this SAME account: an opposite-trade "
                        f"ratio of {ratio:.2%} over the account's own "
                        "trade stream (aggregate computed by the Risk "
                        "Platform)."
                    )
                rule = data.get("rule")
                if isinstance(rule, dict):
                    desc = (rule.get("description") or "").strip()
                    parts = ([f"检测规则:{rule.get('name')}"] if zh else
                             [f"Detection rule: {rule.get('name')}"])
                    if desc:
                        parts.append(_strip_citation_markers(desc))
                    if rule.get("threshold"):
                        parts.append(("阈值:" if zh else "threshold: ")
                                     + str(rule["threshold"]))
                    prose.append(" — ".join(parts) + ("。" if zh else "."))
                n = data.get("record_count", 0)
                if n:
                    prose.append(
                        f"全部 {n} 条支持的交易记录如下所示。"
                        if zh else
                        f"All {n} supporting transaction record"
                        f"{'s are' if n != 1 else ' is'} shown below."
                    )
                prose.append(
                    "这仅是同一账户自身的活动；它不暗示任何跨账户关系，"
                    "也不说明任何交易意图。"
                    if zh else
                    "This is same-account activity only; it implies no "
                    "cross-account relationships and nothing about intent."
                )
            elif data.get("view") == "timeline":
                events = data.get("events") or []
                total = data.get("total_events", len(events))
                if data.get("truncated"):
                    prose.append(
                        f"时间线预览：按时间顺序显示 {total} 个事件中的前 "
                        f"{len(events)} 个。这是有界导航视图——如需查看"
                        "全部事件，请要求完整时间线。"
                        if zh else
                        f"Timeline preview: showing {len(events)} of "
                        f"{total} events (chronological). This is a "
                        "bounded navigation view — ask for the complete "
                        "timeline to see every event."
                    )
                else:
                    prose.append(
                        f"该发现的时间线共 {total} 个事件，已按时间顺序"
                        "列于下方。"
                        if zh else
                        f"The timeline for this finding contains {total} "
                        "event"
                        + ("s" if total != 1 else "")
                        + ", shown below in chronological order."
                    )
            elif data.get("analysis") == "related_account_analysis":
                # AL-2.10: authoritative Risk Platform network attribution
                # echo — cluster facts, related accounts, shared devices,
                # bounded lists, never derived relationships.
                if data.get("analysis_status") == "RELATED_ACCOUNTS_FOUND":
                    cluster = data.get("cluster") or {}
                    parts = []
                    if cluster.get("cluster_name"):
                        parts.append(
                            f"cluster 「{cluster['cluster_name']}」")
                    if cluster.get("detection_type"):
                        parts.append(
                            f"detection type “{cluster['detection_type']}”")
                    if cluster.get("member_count") is not None:
                        parts.append(
                            f"集群内 {cluster['member_count']} 个账号"
                            if zh else
                            f"{cluster['member_count']} accounts in the "
                            "cluster")
                    if cluster.get("cluster_risk_score") is not None:
                        parts.append(
                            f"集群风险分 {cluster['cluster_risk_score']}"
                            if zh else
                            f"cluster risk score "
                            f"{cluster['cluster_risk_score']}")
                    if parts:
                        prose.append(
                            "Risk Platform 网络归因："
                            + "，".join(parts) + "。"
                            if zh else
                            "Risk Platform network attribution: "
                            + ", ".join(parts) + ".")
                    accounts = data.get("related_accounts") or []
                    count = data.get("related_accounts_count")
                    if accounts:
                        listing = ", ".join(str(a) for a in accounts)
                        more = ""
                        if count is not None and count > len(accounts):
                            more = (f"（显示其中 {len(accounts)} 个，共 "
                                    f"{count} 个）" if zh else
                                    f" (showing {len(accounts)} of "
                                    f"{count})")
                        prose.append(
                            f"关联账号：{listing}{more}。"
                            if zh else f"Related accounts: {listing}{more}.")
                    devices = data.get("shared_devices") or []
                    if devices:
                        prose.append(
                            "连接这些账号的共享设备："
                            + "；".join(str(d) for d in devices) + "。"
                            if zh else
                            "Shared devices linking these accounts: "
                            + "; ".join(str(d) for d in devices) + ".")
                elif data.get("evidence_missing"):
                    prose.append(
                        "Risk Platform 未提供该案例的网络归因，因此无法"
                        "列出关联账号。"
                        if zh else
                        "The Risk Platform provides no network attribution "
                        "for this case, so no related accounts can be "
                        "listed.")
                else:
                    prose.append(
                        "Risk Platform 未将该账号的网络归因到任何关联账号。"
                        if zh else
                        "The Risk Platform attributes no related accounts "
                        "to this account's network.")
            elif data.get("analysis") == "cross_account_trade_analysis":
                # Candidate cross-account opposite-trade analysis (§6
                # observation contract): candidate terminology only — the
                # result is a quantitative match under configured
                # tolerances, never a confirmed-pattern claim.
                # Case-4 correction: the answer has TWO independent parts
                # — (A) what the authoritative same-account reverse-trade
                # rate means (baseline §8 boundary; never conflated with
                # the cross-account match), and (B) the target-anchored
                # cross-account result, whose candidates all contain the
                # target account, with the actually-retrieved scope stated.
                status = data.get("analysis_status")
                cfg = data.get("config") or {}
                delta = cfg.get("max_time_delta_seconds")
                rel = cfg.get("value_tolerance_relative")
                floor = cfg.get("value_tolerance_absolute_floor")
                # the criteria are DUAL: relative tolerance OR the absolute
                # near-zero-notional floor. Stating only the 5% made
                # floor-admitted candidates (relative diff far above 5%)
                # read as contradicting the matcher.
                criteria = (
                    (f"（匹配判据：时间相差不超过 {delta:g} 秒，价值差为"
                     f"较大一侧的 {rel:.0%} 以内，或不超过绝对下限 "
                     "{floor:g}（近零名义；仅由下限放行的配对在下方单独"
                     "标注））".format(floor=floor)
                     if zh else
                     f" (matching criteria: time within {delta:g}s, value "
                     f"difference within {rel:.0%} relative to the larger "
                     f"side, OR within the {floor:g} absolute floor "
                     "(near-zero notionals; pairs admitted only by the "
                     "floor are labeled below)")
                ) if delta is not None and rel is not None and \
                    floor is not None else ""
                target = data.get("target_account")
                metrics = data.get("target_account_metrics") or {}
                ratio = metrics.get("opposite_trade_ratio") \
                    if isinstance(metrics, dict) else None
                if ratio is not None and target:
                    prose.append(
                        f"关于反向交易率：{ratio:.2%}"
                        "（opposite_trade_ratio）是账户 "
                        f"{target} 自身的买卖平衡——按该账户自己的交易流"
                        "计算，分子为 min(BUY 笔数, SELL 笔数)，分母为"
                        "该账户交易总笔数。它衡量的是同一账户内的反向"
                        "交易，不是跨账户匹配率，与下方的跨账户结果相互"
                        "独立。"
                        if zh else
                        f"About the reverse-trade rate: {ratio:.2%} "
                        "(opposite_trade_ratio) is account "
                        f"{target}'s OWN buy/sell balance — computed over "
                        "the account's own trade stream as min(BUY count, "
                        "SELL count) ÷ the account's total trades. It "
                        "measures same-account opposite-side trading; it "
                        "is NOT a cross-account match rate and is "
                        "independent of the cross-account result below."
                    )
                elif ratio is None and _mentions_reverse_trade_rate(
                        user_request):
                    prose.append(
                        "本次运行未能取得该账户的权威反向交易率指标，"
                        "因此此处不重述其含义。"
                        if zh else
                        "The authoritative reverse-trade-rate metric for "
                        "this account could not be retrieved in this run, "
                        "so its meaning is not restated here."
                    )
                retrieval = data.get("retrieval") or {}
                t_ret = retrieval.get("target") or {}
                c_ret = retrieval.get("counterparty") or {}
                scope_bits: list[str] = []
                if target and t_ret:
                    scope_bits.append(
                        f"目标账户 {target}：检索到交易 "
                        f"{t_ret.get('transactions_retrieved', 0)} 笔"
                        + ("（不完整）" if t_ret.get("truncated")
                           else "（完整）")
                        if zh else
                        f"target account {target}: "
                        f"{t_ret.get('transactions_retrieved', 0)} "
                        "transactions retrieved"
                        + (" (partial)" if t_ret.get("truncated")
                           else " (complete)"))
                if c_ret:
                    scope_bits.append(
                        f"对手方窗口：在 {c_ret.get('accounts_queried', 0)} "
                        f"个关联账户中检索到交易 "
                        f"{c_ret.get('transactions_retrieved', 0)} 笔"
                        + ("（不完整）" if c_ret.get("truncated") else "")
                        if zh else
                        f"counterparty window: "
                        f"{c_ret.get('transactions_retrieved', 0)} "
                        "transactions retrieved across "
                        f"{c_ret.get('accounts_queried', 0)} connected "
                        "accounts"
                        + (" (partial)" if c_ret.get("truncated") else ""))
                set_mode = (data.get("analysis_scope")
                            == "related_account_set")
                if status == "MATCH_FOUND":
                    candidates = data.get("candidates") or []
                    n = len(candidates)
                    found = data.get("candidates_found")
                    reported = (
                        f"分析在已检证据中发现 {n} 组候选跨账户对冲交易"
                        f"关系{criteria}。"
                        if zh else
                        f"The analysis found {n} candidate cross-account "
                        "opposite-trade relationship"
                        + ("s" if n != 1 else "")
                        + f" in the analyzed evidence{criteria}."
                    )
                    if isinstance(found, int) and found > n:
                        # the system candidate cap bound the report: the
                        # user must see that more candidates exist than
                        # are listed (never an silent cap)
                        reported += (
                            f" 共发现 {found} 组候选，此处列出前 {n} 组"
                            "（受系统候选上限）。"
                            if zh else
                            f" {found} candidates were found in total; "
                            f"the top {n} are listed (system candidate "
                            "limit)."
                        )
                    prose.append(reported)
                    if set_mode:
                        with_case = [c for c in candidates
                                     if target in (c.get("account_a"),
                                                   c.get("account_b"))]
                        found = data.get("candidates_found")
                        cap_note = ""
                        if isinstance(found, int) and found > n:
                            cap_note = (
                                f" 共发现 {found} 组候选；此处列出前 {n} 组"
                                "（受系统候选上限）——其余 "
                                f"{found - n} 组未在本回答中交付。"
                                if zh else
                                f" In total {found} candidate groups "
                                f"were found; the top {n} are listed "
                                f"here (system candidate limit) — the "
                                f"remaining {found - n} are not "
                                "delivered in this answer.")
                        prose.append(
                            f"范围：关联账户集合（案件账户 {target} ∪ "
                            f"关联账户）。列出的 {n} 组候选中 "
                            f"{len(with_case)} 组涉及账户 {target}；其余 "
                            f"{n - len(with_case)} 组仅发生在其他关联"
                            "成员之间——每组候选都附有其自身的交易证据。"
                            + cap_note
                            if zh else
                            f"Scope: the related-account set (case "
                            f"account {target} ∪ related accounts). "
                            f"{len(with_case)} of the {n} listed "
                            f"candidates involve account {target}; the "
                            f"remaining {n - len(with_case)} occur "
                            "solely between other related accounts — "
                            "each pair is reported with its own "
                            "transaction evidence." + cap_note
                        )
                    else:
                        if target:
                            prose.append(
                                f"列出的所有候选均涉及账户 {target}"
                                "（案件账户）；不含 "
                                f"{target} 交易的候选不属于本回答。"
                                if zh else
                                f"All listed candidates involve account "
                                f"{target} (the case account); candidates "
                                f"without a {target} transaction are not "
                                "part of this answer."
                            )
                    for c in candidates:
                        ids = ", ".join(
                            c.get("transaction_ids_a", [])
                            + c.get("transaction_ids_b", []))
                        if set_mode:
                            tag = (("涉及案件账户"
                                    if target in (c.get("account_a"),
                                                  c.get("account_b"))
                                    else "仅涉及关联成员") if zh else
                                   ("involves the case account"
                                    if target in (c.get("account_a"),
                                                  c.get("account_b"))
                                    else "related-members only"))
                            tag_txt = (f"（{tag}） " if zh else f"({tag}) ")
                        else:
                            tag_txt = ""

                        def _num(v, spec):
                            # missing value fields are disclosed as
                            # n/a — never formatted as 0 and never a
                            # crash (a malformed candidate must not
                            # take the whole answer down)
                            if isinstance(v, (int, float)) \
                                    and not isinstance(v, bool):
                                return format(v, spec)
                            return "n/a"

                        # floor-admitted transparency: a candidate whose
                        # relative difference exceeds the relative
                        # tolerance was admitted ONLY by the absolute
                        # floor — label it, never silently
                        floor_note = ""
                        v_rel = c.get("value_difference_relative")
                        v_diff = c.get("value_difference")
                        if isinstance(v_rel, (int, float)) \
                                and isinstance(v_diff, (int, float)) \
                                and not isinstance(v_rel, bool) \
                                and floor is not None \
                                and v_rel > rel and abs(v_diff) <= floor:
                            floor_note = (
                                f" —— 由绝对下限放行（{floor:g}；近零名义）"
                                if zh else
                                " — admitted by the absolute floor "
                                f"({floor:g}; near-zero notionals)")

                        if zh:
                            prose.append(
                                f"- 候选 {c.get('candidate_id')} "
                                f"{tag_txt}({c.get('match_type') or 'n/a'})："
                                f"{c.get('account_a') or '?'} "
                                f"{c.get('side_a') or '?'} ↔ "
                                f"{c.get('account_b') or '?'} "
                                f"{c.get('side_b') or '?'} — "
                                f"{c.get('symbol') or '?'}；最大时间差 "
                                f"{_num(c.get('max_time_gap_seconds'), 'g')}s；"
                                f"价值 {_num(c.get('value_a'), ',.6g')} ↔ "
                                f"{_num(c.get('value_b'), ',.6g')}（价值差 "
                                f"{_num(c.get('value_difference'), ',.6g')}，"
                                f"{_num(v_rel, '.1%')}）；"
                                f"交易:{ids or 'n/a'}"
                                f"{floor_note}"
                            )
                        else:
                            prose.append(
                                f"- Candidate {c.get('candidate_id')} "
                                f"{tag_txt}({c.get('match_type') or 'n/a'}): "
                                f"{c.get('account_a') or '?'} "
                                f"{c.get('side_a') or '?'} vs "
                                f"{c.get('account_b') or '?'} "
                                f"{c.get('side_b') or '?'} — "
                                f"{c.get('symbol') or '?'}; max gap "
                                f"{_num(c.get('max_time_gap_seconds'), 'g')}s; "
                                f"value {_num(c.get('value_a'), ',.6g')} vs "
                                f"{_num(c.get('value_b'), ',.6g')} (difference "
                                f"{_num(c.get('value_difference'), ',.6g')}, "
                                f"{_num(v_rel, '.1%')}); "
                                f"transactions: {ids or 'n/a'}"
                                f"{floor_note}"
                            )
                    prose.append(
                        "以上为候选关系：既定分析容差下的量化匹配——供"
                        "分析员进一步判断的线索，不构成对交易意图的结论。"
                        if zh else
                        "These are candidate relationships: quantitative "
                        "matches under the configured analytical "
                        "tolerances — leads for analyst judgment, not "
                        "conclusions about intent."
                    )
                    if retrieval.get("truncated"):
                        prose.append(
                            "注意：本次检索受系统预算限制，已分析范围不"
                            "完整；不能据此认定检索范围之外不存在更多"
                            "关系。" if zh else
                            "Note: retrieval was bounded by system "
                            "budgets, so the analyzed scope is partial; "
                            "absence beyond it is not established."
                        )
                elif status == "NO_MATCH":
                    if set_mode:
                        prose.append(
                            "在既定容差下，关联账户集合内部（案件账户与"
                            "全部关联账户均已检索并分析）没有任何候选"
                            f"对冲交易关系匹配{criteria}。该结论仅适用于"
                            "已分析的集合与数据窗口。"
                            if zh else
                            "No candidate opposite-trade relationships "
                            "matched within the configured "
                            f"tolerances{criteria} anywhere inside the "
                            "related-account set (the case account and "
                            "all related accounts were retrieved and "
                            "analyzed). This applies to the analyzed "
                            "set and data window only."
                        )
                    else:
                        prose.append(
                            "在既定容差下，已检证据中没有匹配的候选跨账户"
                            "对冲交易关系。该结论仅适用于已分析的账户与"
                            "数据窗口。"
                            + (f" 未发现涉及账户 {target} 的候选；其他"
                               f"账户之间的关系不作为 {target} 的对冲"
                               "证据。" if target else "")
                            if zh else
                            "No candidate cross-account opposite-trade "
                            "relationships matched within the configured "
                            f"tolerances{criteria} in the analyzed "
                            "evidence. This applies to the analyzed "
                            "accounts and data window only."
                            + (f" No candidate involving account {target} "
                               "was found; relationships between other "
                               f"accounts are not reported as {target}'s "
                               "hedging evidence." if target else "")
                        )
                else:   # INSUFFICIENT_DATA
                    prose.append(
                        "跨账户交易分析无法得出可靠结论：可用交易证据不"
                        "完整或不足（检索受限或为空）。这不是\"未发现\"。"
                        if zh else
                        "The cross-account trade analysis could not reach "
                        "a reliable conclusion: the available transaction "
                        "evidence is incomplete or insufficient "
                        "(retrieval was bounded or empty). This is not a "
                        "no-match result."
                    )
                if scope_bits:
                    prose.append(("分析范围：" if zh else "Analyzed scope: ")
                                 + "; ".join(scope_bits)
                                 + ("。" if zh else "."))
            elif data.get("signal_type") == "Rule" and "rule" in data:
                if zh:
                    # zh turn face: the loop path's rule semantics from the
                    # SAME payload (mechanism + observed-vs-threshold).
                    # No `continue`: the structured supporting detail below
                    # (additional-data line, policy, artifact confirmation)
                    # still runs for this payload.
                    from app.agent_loop import _signal_answer_lines
                    rule_zh = data["rule"]
                    prose.extend(_signal_answer_lines({
                        "rule_name": rule_zh.get("name"),
                        "rule_description": rule_zh.get("description"),
                        "threshold": rule_zh.get("threshold"),
                        "trigger_values": rule_zh.get("trigger_values") or {},
                        "contribution": rule_zh.get("contribution"),
                    }, lang="zh"))
                else:
                    # 1) detection mechanism (deterministic, from the payload).
                    # When the confirmed-fact sentence below already names the
                    # rule, use the mechanism sentence WITHOUT the rule name —
                    # never a duplicated restatement (P7).
                    rule = data["rule"]
                    _fact_names_rule = bool(rule.get("name")) and bool(
                        rule.get("description") or rule.get("trigger_values"))
                    mechanism = detection_mechanism_sentence(data)
                    if _fact_names_rule and mechanism and rule.get("name"):
                        mechanism = mechanism.replace(
                            f" (the {rule['name']} rule)", "")
                    prose.append(mechanism)
                    # 2) confirmed finding fact — RP's own structured rule data
                    name = rule.get("name") or "the rule"
                    trigger = rule.get("trigger_values") or {}
                    threshold = rule.get("threshold")
                    desc = (rule.get("description") or "").strip()
                    if trigger and threshold:
                        observed = ", ".join(
                            f"{_FEATURE_LABELS.get(k, k)} = {v}"
                            for k, v in trigger.items()
                        )
                        thr = str(threshold)
                        for raw, label in _FEATURE_LABELS.items():
                            thr = thr.replace(raw, label)
                        prose.append(
                            f"The finding was flagged because {observed}, "
                            f"exceeding the system's configured threshold "
                            f"({thr}). The rule {name} triggered and "
                            "contributed to this finding."
                        )
                    elif desc:
                        # RP's own description is the most informative grounded
                        # fact available — use it as the answer (P7), not a bare
                        # name restatement.
                        prose.append(
                            f"The finding was flagged by the rule {name}: "
                            f"{_strip_citation_markers(desc)}"
                        )
                    else:
                        # only the name is known — say only that
                        prose.append(
                            f"The finding was flagged by the rule {name}."
                        )

            elif data.get("signal_type") == "ML" and "explanation" in data:
                if response_language == "zh":
                    # zh turn face: the SAME authoritative semantics as the
                    # loop path's zh rendering, built from THIS payload.
                    from app.agent_loop import _signal_answer_lines
                    ex_zh = data["explanation"]
                    prose.extend(_signal_answer_lines({
                        "ml_score": ex_zh.get("ml_score"),
                        "detection_method": ex_zh.get("detection_method"),
                        "score_interpretation": ex_zh.get(
                            "score_interpretation"),
                        "attribution_available": ex_zh.get(
                            "attribution_available"),
                        "available_feature_values":
                            ex_zh.get("available_feature_values") or {},
                    }, lang="zh"))
                else:
                    # 1) detection mechanism (deterministic, from the payload)
                    prose.append(detection_mechanism_sentence(data))
                    # 2) confirmed fact: the model score
                    ex = data["explanation"]
                    if "ml_score" in ex:
                        prose.append(
                            f"The model score is {ex['ml_score']}/100 "
                            f"({ex.get('score_interpretation', 'a system signal')})."
                        )
                    # 3) the authoritative context features the model scored
                    # (list-completeness labeling: a partial display says so)
                    feats = ex.get("available_feature_values") or {}
                    if isinstance(feats, dict) and feats:
                        shown = ", ".join(
                            f"{_FEATURE_LABELS.get(k, k)} = {v}"
                            for k, v in list(feats.items())[:10])
                        more = (
                            f" (first 10 of {len(feats)} features)"
                            if len(feats) > 10 else "")
                        prose.append(
                            "The model scored this account on these Risk "
                            f"Platform features: {shown}{more}.")
                    # 4) honest boundary: per-feature attribution does not
                    # exist in RP data — never presented, never invented
                    if ex.get("attribution_available") is False:
                        prose.append(
                            "Boundary: the Risk Platform provides no "
                            "per-feature contribution attribution for this "
                            "signal, so individual feature importance and "
                            "the model's reasoning process cannot be stated "
                            "— the facts above are what is determinable.")
            elif data.get("signal_type") == "Feature" \
                    and "explanation" in data:
                if zh:
                    # zh turn face: the loop path's feature semantics
                    from app.agent_loop import _signal_answer_lines
                    feats_zh = [
                        {"name": f.get("name"), "value": f.get("value")}
                        for f in ((data.get("explanation") or {})
                                  .get("features") or [])
                        if isinstance(f, dict) and f.get("name") is not None]
                    prose.extend(_signal_answer_lines(
                        {"feature_facts": feats_zh}, lang="zh"))
                else:
                    # AL-2.9: a feature-detected finding (no rule/ML/graph
                    # backing). The mechanism sentence names the detection
                    # class; the RP risk-feature facts ARE the grounded
                    # "why". (No `continue`: the structured detail below
                    # still runs.)
                    prose.append(detection_mechanism_sentence(data))
                    feats = (data.get("explanation") or {}
                             ).get("features") or []
                    if feats:
                        observed = ", ".join(
                            f"{_FEATURE_LABELS.get(f.get('name'), f.get('name'))}"
                            f" = {f.get('value')}"
                            for f in feats if f.get("name") is not None)
                        if observed:
                            prose.append(
                                "The finding is based on the following Risk "
                                f"Platform risk features: {observed}.")
            elif data.get("signal_type") == "Graph" and "explanation" in data:
                if zh:
                    # zh turn face: the loop path's graph semantics from the
                    # SAME payload (cluster/shared devices/related accounts)
                    from app.agent_loop import _signal_answer_lines
                    ex_zh = data.get("explanation") or {}
                    cluster_zh = ex_zh.get("cluster") or {}
                    related_zh = ex_zh.get("related_accounts") or []
                    prose.extend(_signal_answer_lines({
                        "cluster_name": cluster_zh.get("cluster_name"),
                        "detection_type": cluster_zh.get("detection_type"),
                        "member_count": cluster_zh.get("member_count"),
                        "cluster_risk_score": cluster_zh.get(
                            "cluster_risk_score"),
                        "shared_devices": ex_zh.get("shared_devices") or [],
                        "related_accounts": related_zh,
                    }, lang="zh"))
                else:
                    # (No `continue`: the structured detail below still
                    # runs for this payload.)
                    prose.append(detection_mechanism_sentence(data))
                    # AL-2.8: the graph facts ARE the "why" — state them from
                    # RP's own network evidence (cluster, shared devices,
                    # related accounts), bounded lists, nothing synthesized.
                    ex = data.get("explanation") or {}
                    cluster = ex.get("cluster") or {}
                    facts = []
                    if cluster.get("detection_type"):
                        facts.append(f"detection type “{cluster['detection_type']}”")
                    if cluster.get("member_count") is not None:
                        facts.append(
                            f"{cluster['member_count']} accounts in the cluster")
                    if cluster.get("cluster_risk_score") is not None:
                        facts.append(
                            f"cluster risk score "
                            f"{cluster['cluster_risk_score']}")
                    if facts:
                        prose.append(
                            "Risk Platform graph data shows: " + ", ".join(facts)
                            + ".")
                    devices = ex.get("shared_devices") or []
                    if devices:
                        shown = "; ".join(str(d) for d in devices[:3])
                        more = (f" (and {len(devices) - 3} more)"
                                if len(devices) > 3 else "")
                        prose.append(
                            f"Shared devices linking these accounts: "
                            f"{shown}{more}.")
                    related = ex.get("related_accounts") or []
                    if related:
                        # List-completeness semantics: RP's related_accounts
                        # is a server-side preview — a partial list is always
                        # labeled as a sample with the authoritative total
                        # (cluster members incl. this account, minus one),
                        # never as the complete membership.
                        shown_ids = ", ".join(str(a) for a in related[:5])
                        shown_n = min(len(related), 5)
                        total = ex.get("related_accounts_total")
                        is_preview = ex.get("related_accounts_preview") is True
                        if is_preview and total and shown_n < total:
                            member_count = (ex.get("cluster") or {}).get(
                                "member_count")
                            scope_note = (f" (the cluster has {member_count} "
                                          "members including this account)"
                                          if isinstance(member_count, int)
                                          else "")
                            prose.append(
                                f"Related accounts — SAMPLE, not the full "
                                f"list: {shown_ids} ({shown_n} of {total} "
                                f"related accounts shown as a Risk Platform "
                                f"graph-evidence preview{scope_note}). The "
                                "complete related-account list is available "
                                "through the related-account network "
                                "analysis.")
                        else:
                            more = (f" (and {len(related) - 5} more)"
                                    if len(related) > 5 else "")
                            prose.append(
                                f"Related accounts in the cluster: "
                                f"{shown_ids}{more}.")
            elif data.get("signal_type") == "Rule" \
                    and "explanation" in data:
                # rule requested but NO backing rule exists in RP data —
                # still identify the detection mechanism deterministically
                prose.append(
                    "该发现由规则检测标记（Risk Platform 未提供该规则的"
                    "归因明细）。" if zh else
                    detection_mechanism_sentence(data))
            if data.get("evidence_missing") and not data.get("records") \
                    and data.get("signal_type") in ("ML", "Rule", "Graph") \
                    and prose and not zh:
                # 3) missing explanatory evidence for a signal explanation —
                # appended AFTER the mechanism/confirmed-fact sentences as
                # the grammatical continuation of the LAST signal sentence
                # (never a widened claim; P14). Ordered: mechanism →
                # confirmed fact → missing evidence.
                clause = gap_confirmation_clause(data)
                if clause:
                    prose[-1] = prose[-1].rstrip(".") + clause + "."
            if data.get("evidence_missing") and not data.get("records"):
                # P14: the gap sentence must NAME the actual gap — a
                # subsystem-specific flag must never become a generic
                # investigation-level evidence claim.
                if isinstance(data.get("matches"), list) \
                        and data.get("finding_policy_status"):
                    pass          # policy branch below states it exactly
                elif isinstance(data.get("artifact"), dict):
                    # artifact success: the artifact's own Evidence Gaps
                    # section already names any real gap precisely; and a
                    # successfully generated bundle is NOT itself evidence
                    # that transaction records are missing (the inherited
                    # flag may reflect an unrelated policy-metadata gap).
                    pass
                elif data.get("signal_type") in ("ML", "Rule", "Graph"):
                    pass          # signal branch above appended the
                    # producer-specific gap as the continuation of the
                    # detection-mechanism/confirmed-fact sentence
                elif data.get("analysis") == "cross_account_trade_analysis":
                    pass          # the analysis branch above stated the
                    # insufficiency exactly (bounded status wording)
                else:
                    # Producer-specific gap semantics (shared with the
                    # artifact gap renderer): the sentence names exactly the
                    # missing item(s) from next_data_needed — never widened
                    # into "transaction-level evidence unavailable" unless
                    # transaction records are actually the missing item.
                    statement = gap_statement(data)
                    if statement:
                        if zh:
                            # zh face: the SAME bounded semantics built from
                            # the mapped items (system-authored guidance in
                            # the turn language; unknown items stay verbatim)
                            needed = [
                                _zh_tool_text(str(nd))
                                for nd in (data.get("next_data_needed") or [])
                            ]
                            zh_gap = (
                                "缺少：" + "；".join(
                                    f"“{n}”" for n in needed)
                                + "（Risk Platform 当前不提供这些数据）。"
                                if needed else
                                "该结果的某些支持数据当前无法从 Risk "
                                "Platform 获取。")
                            prose.append(f"Risk Platform 确认该发现，但{zh_gap}")
                        else:
                            prose.append(
                                "The Risk Platform confirms the finding, but "
                                f"{statement[0].lower() + statement[1:]}"
                            )

            # ---- structured supporting detail ---------------------------
            if data.get("evidence_missing") \
                    and not isinstance(data.get("artifact"), dict):
                # artifact payloads excluded: their gaps belong to the
                # artifact's own Evidence Gaps section (already precise) —
                # an unrelated subsystem gap must not be restated as extra
                # data needed for the bundle creation answer.
                if result.next_data_needed:
                    items = ([_zh_tool_text(str(nd))
                              for nd in result.next_data_needed]
                             if zh else list(result.next_data_needed))
                    lines.append(
                        ("还需补充的数据：" if zh else
                         "Additional data needed: ")
                        + "; ".join(items) + ".")
            # timeline payload
            events = data.get("events") or []
            for ev in events:
                stamp = ev.timestamp if hasattr(ev, "timestamp") else ev.get("timestamp")
                summary = (ev.summary if hasattr(ev, "summary")
                           else ev.get("summary", ""))
                lines.append(f"- {stamp}: {summary}" if stamp else f"- {summary}")
            # case fetch payload — concise finding list. NO evidence-ref
            # collection: Case Intake is finding-level, never a concrete-
            # evidence listing (§6). Citation markers are stripped from
            # conversational text unless THIS response carries a policy
            # mapping (§5) — intake does not.
            for f in data.get("findings") or []:
                title = f.title if hasattr(f, "title") else f.get("title", "")
                summary = (f.summary if hasattr(f, "summary")
                           else f.get("summary", ""))
                lines.append(f"- {title}: {_strip_citation_markers(summary)}")
            # concrete evidence payload — COMPLETE record set (view="evidence");
            # record ids are legitimate here: the user asked for evidence.
            # §20: no unrelated feature/policy metadata in this scope.
            for rec in data.get("records") or []:
                stamp = rec.get("timestamp")
                rid = rec.get("record_id", "")
                summary_txt = rec.get("summary", "")
                marker = f"- {stamp}: " if stamp else "- "
                lines.append(f"{marker}{summary_txt} [{rid}]")
            if data.get("truncated"):
                lines.append(
                    f"（显示前 {data.get('top_n')} 条，共 "
                    f"{data.get('total_events')} 个事件）" if zh else
                    f"(showing top {data.get('top_n')} of "
                    f"{data.get('total_events')} events)"
                )
            # Signal-explanation payload (rule/ML/graph): surface the key
            # grounded facts deterministically. Marker-stripped: this
            # response carries no policy mapping of its own (§5).
            if "rule" in data and isinstance(data["rule"], dict):
                rule = data["rule"]
                parts = []
                # RP's own description is the substance of the answer when
                # present (e.g. "10 withdrawals in 24h exceeds the normal
                # pattern") — never duplicated by the prose line above.
                desc = (rule.get("description") or "").strip()
                # skip the description here when the prose line already
                # carries it (P7: no duplicated sentences)
                if desc and not any(desc in p for p in prose):
                    parts.append(desc)
                if rule.get("trigger_values"):
                    parts.append("observed: " + ", ".join(
                        f"{_FEATURE_LABELS.get(k, k)}={v}"
                        for k, v in rule["trigger_values"].items()))
                if rule.get("threshold"):
                    # thresholds are usually expressed as raw feature
                    # conditions — humanize known field names, keep the
                    # operator/values verbatim
                    thr = str(rule["threshold"])
                    for raw, label in _FEATURE_LABELS.items():
                        thr = thr.replace(raw, label)
                    parts.append(f"threshold: {thr}")
                if rule.get("contribution") is not None:
                    parts.append(f"score contribution: {rule['contribution']}")
                if parts:
                    lines.append(_strip_citation_markers(
                        ("详情：" if zh else "Details: ")
                        + " — ".join(parts) + "."))
            if data.get("signal_type") == "ML" and "explanation" in data \
                    and not zh and not any(
                        "The model score is" in p for p in prose):
                # suppressed when the prose already carries the score
                # (P7: no duplicated sentences)
                ex = data["explanation"]
                if "ml_score" in ex:
                    lines.append(
                        f"ML score: {ex['ml_score']}/100 "
                        f"({ex.get('score_interpretation', 'system signal')})."
                    )
            # Policy-lookup payload (AL-2.10 answer fidelity): only the
            # FINDING-ASSOCIATED citations belong in a finding-explain
            # answer. No finding-level basis → say exactly that (the
            # investigation-wide citation dump never answers "why is THIS
            # finding a risk" and is omitted).
            if isinstance(data.get("matches"), list) and data["matches"]:
                matches = data["matches"]
                associated_ids = {
                    p.get("citation_id") for p in (
                        data.get("associated_policy_refs") or [])
                }
                associated = [m for m in matches
                              if m.get("citation_id") in associated_ids]
                # deterministic relevance order (normalize_policy_matches
                # already sorts by relevance; associated first)
                has_finding_basis = bool(associated)
                if has_finding_basis:
                    # finding-level contract (§12): the ASSOCIATED citations
                    # are what "applies to this finding" — canonical with the
                    # artifact's "Policy citations: [n]" (Finding.policy_refs).
                    # Block 1 (finding-level): count includes ONLY associated
                    # references.
                    top = list(associated)
                    prose.append(
                        f"该发现直接适用 {len(associated)} 条政策引用："
                        if zh else
                        f"For this finding, {len(associated)} policy "
                        f"reference"
                        f"{'s apply' if len(associated) != 1 else ' applies'}"
                        " directly:"
                    )
                else:
                    # AL-2.10 answer fidelity: when the finding carries no
                    # finding-level policy basis, SAY EXACTLY THAT — the
                    # case-wide citation dump is omitted (it does not answer
                    # "why is THIS finding a risk" and reads as padding).
                    top = []
                    prose.append(
                        "Risk Platform 未为该发现附加 finding-level 政策"
                        "依据；其持有的引用为调查级，而非针对该发现。"
                        if zh else
                        "The Risk Platform has attached no finding-level "
                        "policy basis to this finding; the citations it "
                        "holds are investigation-wide, not specific to it."
                    )
                for i, m in enumerate(top, 1):
                    cite = (f" [{m['citation_id']}]"
                            if m.get("citation_id") is not None else "")
                    lines.append(
                        f"{i}. {m.get('document', '')} — "
                        f"{m.get('section', '')}{cite}"
                    )
                if not matches or (data.get("evidence_missing") and not top):
                    lines.append(
                        "该发现没有可直接适用的政策引用。"
                        if zh else
                        "No directly relevant policy references are "
                        "available for this finding."
                    )
                elif data.get("evidence_missing") and has_finding_basis:
                    # (the no-basis case already states the gap in its lead
                    # sentence — no duplicate footnote needed)
                    lines.append(
                        "Risk Platform 未为该发现附加任何 finding-level "
                        "政策引用；上述引用为案例级。"
                        if zh else
                        "The Risk Platform has not attached any finding-"
                        "level policy citation to this finding; the "
                        "references above are case-level."
                    )
            # Artifact payload: creation confirmation + where to find it
            # (§9 — the user is never assumed to know the UI layout).
            if isinstance(data.get("artifact"), dict):
                a = data["artifact"]
                lines.append(
                    f"调查证据包已生成，可在右侧 Artifacts 面板查看"
                    f"（{a.get('artifact_id')}）。" if zh else
                    f"The investigation bundle has been created and is "
                    f"available in the Artifacts panel on the right "
                    f"({a.get('artifact_id')})."
                )
        elif result.outcome == ToolResultOutcome.EMPTY:
            # Bounded honesty (P14): EMPTY has distinct meanings and the
            # tool's own warnings carry the precise one — e.g. an evidence
            # request on a real finding with zero concrete records is NOT
            # the same as "no such data". Preserve the tool's meaning;
            # only fall back to the generic message when it gave none.
            empty_data = result.data if isinstance(result.data, dict) else {}
            if empty_data.get("scope") in ("case", "finding") \
                    and any(w.startswith("No content-contributing")
                            for w in (result.warnings or [])):
                # artifact_bundle had nothing to compose — the expected
                # consequence of an already-empty result set; the main
                # prose above already states it. No extra line needed.
                pass
            elif empty_data.get("view") == "evidence":
                prose.append(
                    "Risk Platform 确认该发现，但其证据流没有任何具体"
                    "记录——仅存在聚合特征值，因此无法提供完整的具体"
                    "证据。" if zh else
                    "The Risk Platform confirms this finding, but holds no "
                    "concrete records for its evidence streams — only "
                    "aggregate feature values exist. Complete concrete "
                    "evidence is therefore unavailable."
                )
            elif empty_data.get("view") == "self_opposite_trades":
                prose.append(
                    "Risk Platform 没有该账户的反向交易活动数据——既无"
                    "聚合值也无支持的交易记录。" if zh else
                    "The Risk Platform holds no opposite-side trading "
                    "activity data for this account — no aggregate and no "
                    "supporting transaction records."
                )
            elif isinstance(empty_data.get("matches"), list) \
                    and empty_data.get("finding_id"):
                # Policy-lookup empty (P20/P9): a valid lookup whose answer
                # is "none" — explicitly NOT an unsupported capability and
                # NOT a failure.
                prose.append(
                    "未找到直接适用于该发现的政策。"
                    if zh else
                    "No directly applicable policy was found for this "
                    "finding."
                )
            else:
                if empty_data.get("scope") in ("case", "finding"):
                    lines.append(
                        "当前尚无可打包的内容：本调查中没有任何调查结果。"
                        "请先询问案例或发现，再生成证据包。"
                        if zh else
                        "There is nothing to bundle yet: no investigation "
                        "results are available in this investigation. Ask "
                        "about the case or a finding first, then generate "
                        "the bundle."
                    )
                else:
                    lines.append(
                        "查询有效，但 Risk Platform 没有与之匹配的数据。"
                        if zh else
                        "The query was valid, but the Risk Platform holds "
                        "no matching data for it."
                    )
            for warning in result.warnings or []:
                if warning.startswith("No content-contributing"):
                    continue        # implied by the empty result set
                if warning not in lines:
                    lines.append(
                        _zh_warning_face(warning) if zh else
                        f"Note: {warning}")
        elif result.outcome == ToolResultOutcome.INTEGRATION_ERROR:
            msg = result.error.message if result.error else "upstream failure"
            lines.append(
                f"无法连接 Risk Platform 或其调用失败（集成错误:{msg}）。"
                "这不是空结果——应稍后重试可用性。" if zh else
                f"The Risk Platform could not be reached or failed "
                f"(integration error: {msg}). This is not an empty result — "
                "availability should be retried later."
            )
        elif result.outcome == ToolResultOutcome.UNSUPPORTED:
            msg = result.error.message if result.error else "unsupported here"
            lines.append(
                f"当前目标不支持该调查操作:{msg}" if zh else
                f"This investigation action is not supported for the current "
                f"target: {msg}"
            )
        elif result.outcome == ToolResultOutcome.VALIDATION_ERROR:
            msg = result.error.message if result.error else "invalid input"
            lines.append(f"该请求未能执行:{msg}" if zh else
                         f"The request could not be executed: {msg}")

    if prose or lines:
        # §5: [n] markers may appear in a conversational response ONLY when
        # that same response carries a resolvable policy mapping — i.e. a
        # policy_lookup result rendered its Document/Section/[n] lines above.
        policy_mapping_present = any(
            isinstance((tc.result.data if tc.result else None) or {}, dict)
            and isinstance((tc.result.data or {}).get("matches"), list)
            and (tc.result.data or {}).get("matches")
            for tc in tool_calls
        )
        # §6: record IDs appear only in evidence mode (concrete-record
        # payloads above). A timeline is navigation, not a record list —
        # its event evidence ids are never appended as a reference dump.
        body = "\n".join(prose + lines)
        if not policy_mapping_present:
            body = _strip_citation_markers(body)
        return body

    return ("调查已完成，但没有结构化输出。" if zh else
            "The investigation completed without structured output.")


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class InvestigationTurnResult(BaseModel):
    """Structured outcome of one orchestration turn."""

    task: TaskV2
    plan: Plan | None = None
    planning_failure: PlanningFailure | None = None
    execution: ExecutionResult | None = None
    response: str
    context: InvestigationContext
    follow_ups: list[FollowUp] = Field(default_factory=list)
    context_changed: bool = False
    # Stage 2.1 structured Question / Goal Representation (open-ended
    # baseline §35b). Additive; None when no goal resolver is wired.
    goal_representation: Any | None = None
    # SP-M2-1R QA evidence: user-safe target-resolution path record —
    # distinguishes fast-path resolution / ambiguity without refinement /
    # refinement-invoked (SELECT | CLARIFY). No model reasoning, no prompts.
    # Additive; None on paths with no target semantics (capability guidance).
    target_resolution: dict[str, Any] | None = None

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class InvestigationService:
    """Orchestration glue for one investigation turn."""

    def __init__(
        self,
        resolver: ContextResolver | None = None,
        planner: PlannerV2 | None = None,
        executor: ExecutorV2 | None = None,
        task_store: TaskStoreV2 | None = None,
        investigation_id: str | None = None,
        allow_llm_resolution: bool = True,
        goal_resolver: Callable | None = None,
        refinement_resolver: Callable | None = None,
        metadata_admissibility: Callable | None = None,
        clarification_ledger: Any | None = None,
        semantic_catalog: Any | None = None,
    ):
        # Stage 1 (open-ended baseline): semantic target resolution is
        # ENABLED by default — natural-language requests resolve their
        # finding from the authoritative candidate set via the bounded,
        # candidate-restricted LLM assist (never free-form). Callers may
        # pass allow_llm_resolution=False for a deterministic-only resolver.
        # AL-2.8: `semantic_catalog` (governed M2 CatalogProvider) enables
        # the semantic-foundation stage inside the resolver; None keeps the
        # legacy chain (used by tests / deterministic-only callers).
        self.resolver = resolver or ContextResolver(
            llm_assist=allow_llm_resolution,
            semantic_catalog=semantic_catalog)
        self._allow_llm_resolution = allow_llm_resolution
        if resolver is None and not allow_llm_resolution:
            # deterministic-only: hard-disable the assist availability check
            self.resolver._llm_configured = lambda: False
        # Stage 2.1 goal understanding: callable (request_text,
        # focused_finding_id, case_id) -> GoalRepresentation. None =>
        # heuristic-only proposer (no LLM); product wiring injects the
        # LLM-backed resolver.
        self.goal_resolver = goal_resolver
        # SP-M2-1: optional bounded goal-aware target refinement
        # (request_text, candidates, goal) -> RefinementDecision.
        self.refinement_resolver = refinement_resolver
        # WP-M2-1 WI-5: optional deterministic Safe-Select admissibility
        # gate (app/admissibility.py). Called between the frozen
        # refinement PROPOSAL and any resolution upgrade when
        # ENABLE_SAFE_SELECT_ADMISSIBILITY is on; None = legacy
        # pre-WI-5 behavior (flag OFF bypass).
        self.metadata_admissibility = metadata_admissibility
        # WP-M2-1 WI-6: bounded-clarification ledger (loop-boundary
        # protection). Active at runtime only when
        # ENABLE_SAFE_SELECT_ADMISSIBILITY is on; None = legacy behavior.
        self.clarification_ledger = clarification_ledger
        self.planner = planner or PlannerV2()
        self.executor = executor or ExecutorV2(task_store=None)
        self.task_store = task_store   # optional; run_turn also accepts one

    # ------------------------------------------------------------------ turn

    def run_turn(
        self,
        user_request: str,
        context: InvestigationContext,
        case_findings: list[Finding],
        investigation_id: str | None = None,
        task_store: TaskStoreV2 | None = None,
        trigger_hint: TriggerReason | None = None,
        prior_tool_calls: list[ToolCallV2] | None = None,
        response_language: str | None = None,
    ) -> InvestigationTurnResult:
        """Run one full turn. Never calls tools directly; never mutates the
        input context object (works on copies).

        `prior_tool_calls`: executed ToolCallV2 records from earlier turns of
        this investigation — used only as artifact provenance sources; they
        are never re-executed and never attributed to this turn's task.
        """
        store = task_store or self.task_store
        inv_id = investigation_id or f"CASE:{context.case_id}"
        now_stamp = None  # timestamps come from task lifecycle below

        task = TaskV2(
            task_id=f"TASK-{uuid.uuid4().hex[:12]}",
            investigation_id=inv_id,
            user_request=user_request,
            status=TaskStatusV2.PENDING,
        )
        self._persist(store, task)   # full lifecycle starts auditable at pending
        case_capabilities = [
            f.capabilities for f in case_findings
        ]

        # --- 1) Context Resolution ------------------------------------------
        resolution = self.resolver.resolve(
            user_request, context, findings=case_findings,
        )
        resolved_ctx = resolution.updated_context

        # --- 1b) SP-M2-1 bounded goal-aware target refinement ---------------
        # Experimental (flag-gated): when initial resolution retains
        # multiple plausible candidates, ONE goal-aware refinement attempt
        # may upgrade the resolution to a unique high-confidence target.
        # Never forces a selection; failure degrades to the existing
        # clarification path. (docs/baselines/2026-09-21-...-v1.md §3)
        # SP-M2-1R: the refinement resolver is wired into the production
        # API construction (app/api/investigations.py). QA-safe evidence
        # (invoked/decision/target/confidence — no model reasoning) is
        # recorded for every resolution outcome so an independent QA can
        # distinguish fast-path / ambiguous-without-refinement /
        # refinement-invoked turns.
        refined_target_id = None
        refinement_evidence: dict[str, Any] = {
            "invoked": False, "skipped_reason": None,
        }
        if (resolution.status == ResolutionStatus.AMBIGUOUS
                and resolution.candidates):
            if not settings.ENABLE_TARGET_GOAL_REFINEMENT:
                refinement_evidence["skipped_reason"] = "flag_disabled"
            elif self.refinement_resolver is None:
                refinement_evidence["skipped_reason"] = "resolver_not_wired"
            else:
                try:
                    cand = [{"id": c2.id, "title": c2.title,
                             "description": hint}
                            for c2, hint in (
                                (c, next((f.summary for f in case_findings
                                          if f.finding_id == c.id), c.hint))
                                for c in resolution.candidates)]
                    goal = None
                    if self.goal_resolver:
                        from app.agent_loop import _call_goal_resolver
                        goal = _call_goal_resolver(
                            self.goal_resolver, user_request, None,
                            resolved_ctx.case_id, {})
                    refined = self.refinement_resolver(
                        user_request, cand,
                        goal.project() if goal is not None else None)
                    refinement_evidence.update({
                        "invoked": True,
                        "decision": refined.decision,
                        "target_id": refined.target_id,
                        "confidence": refined.confidence,
                        "reason": refined.reason,
                    })
                    telemetry_emit(AgentTelemetryEvent(
                        event_type="target.refinement",
                        investigation_id=inv_id,
                        case_id=resolved_ctx.case_id,
                        decision=refined.decision,
                        outcome=refined.target_id,
                        summary=refined.reason,
                    ))
                    logger.info(
                        "target.refinement decision=%s target=%s "
                        "confidence=%s candidates=%s", refined.decision,
                        refined.target_id, refined.confidence,
                        len(cand))
                    if (refined.decision == "SELECT"
                            and refined.target_id in
                            {c.id for c in resolution.candidates}):
                        # --- WI-5 Safe-Select admissibility gate ---------
                        # The frozen refinement PROPOSAL never upgrades the
                        # resolution by itself when the gate is enabled: a
                        # deterministic system decision (metadata
                        # eligibility + support evidence + uniqueness +
                        # target identity + confidence) is required
                        # first. Flag OFF = legacy pre-WI-5 behavior
                        # (no gate).
                        gate = (self.metadata_admissibility
                                if settings.ENABLE_SAFE_SELECT_ADMISSIBILITY
                                else None)
                        gate_decision = None
                        if gate is not None:
                            try:
                                gate_decision = gate(
                                    request_text=user_request,
                                    candidate_ids=[c.id for c in
                                                   resolution.candidates],
                                    proposed_target=refined.target_id,
                                    confidence=refined.confidence,
                                    candidate_set_stable=True,
                                    investigation_id=inv_id)
                            except Exception:
                                logger.warning(
                                    "metadata admissibility gate failed; "
                                    "treating as rejected", exc_info=True)
                                gate_decision = AdmissibilityDecision(
                                    False, "gate_error")
                            refinement_evidence["admissibility"] = {
                                "decision": gate_decision.reason_class,
                                "details": dict(gate_decision.details),
                            }
                            if not gate_decision.admitted:
                                refinement_evidence["invoked"] = True
                                refinement_evidence["decision"] = "CLARIFY"
                                refinement_evidence["reason"] = (
                                    "admissibility rejected: "
                                    + gate_decision.reason_class)
                        admitted = (gate is None
                                    or gate_decision.admitted)
                        if admitted:
                            target = next(
                                (f for f in case_findings
                                 if f.finding_id == refined.target_id),
                                None)
                            if target is not None:
                                resolution = ResolutionOutcome(
                                    status=ResolutionStatus.RESOLVED,
                                    updated_context=resolved_ctx.model_copy(
                                        update={
                                            "focused_finding_id":
                                                target.finding_id,
                                            "focus_source":
                                                FocusSource.AGENT_RESOLVED,
                                        }),
                                    context_changed=True,
                                    focus_source=FocusSource.AGENT_RESOLVED,
                                    resolved_entity=Candidate(
                                        kind="finding",
                                        id=target.finding_id,
                                        title=target.title),
                                    used_llm=True,
                                    resolution_kind="inferred",
                                    resolution_reason=refined.reason,
                                )
                                resolved_ctx = resolution.updated_context
                                refined_target_id = target.finding_id
                except Exception:
                    logger.warning("target refinement failed", exc_info=True)
                    refinement_evidence["error"] = "refinement_failed"
        target_resolution = {
            "kind": ("refined_select" if refined_target_id else "resolved"),
            "refinement": refinement_evidence,
        }
        # AL-2.8: bounded evidence of the M2 semantic-foundation stage when
        # it ran (normalization + grounding provenance; never an
        # authorization fact).
        if resolution.semantic_evidence:
            target_resolution["semantic_grounding"] = \
                resolution.semantic_evidence

        # Target-independent guidance (capability questions): a completed
        # informational turn — no planning, no tools, no failure (P12/P14).
        if resolution.status == ResolutionStatus.CAPABILITY_GUIDANCE:
            task.status = TaskStatusV2.COMPLETED
            task.started_at = task.completed_at = _stamp()
            self._persist(store, task)
            return InvestigationTurnResult(
                task=task,
                response=resolution.clarification_message
                or "I can help investigate a Risk Platform case.",
                context=resolved_ctx,          # unchanged
                follow_ups=[],                 # guidance needs no chips
                context_changed=False,
            )

        if resolution.status == ResolutionStatus.AMBIGUOUS:
            # --- WI-6 bounded clarification lifecycle --------------------
            # The clarification is a resumable control state, hash-keyed
            # by (request, candidate set, governed metadata statuses).
            # First occurrence -> bounded clarification. Same key repeated
            # with no meaningful state change -> bounded non-selectable
            # terminal outcome (never a third clarification, never a
            # pretended answer).
            ledger = (self.clarification_ledger
                      if settings.ENABLE_SAFE_SELECT_ADMISSIBILITY
                      else None)
            signal = None
            if ledger is not None:
                try:
                    signal = ledger.record_clarification(
                        investigation_id=inv_id,
                        request_text=user_request,
                        candidate_ids=[c.id for c in resolution.candidates],
                        reason_class=("admissibility_rejected" if
                                      refinement_evidence.get("admissibility")
                                      else "ambiguous_resolution"))
                except Exception:
                    logger.warning("clarify ledger failed; continuing with "
                                   "standard clarification", exc_info=True)
            if signal is not None and signal.terminal:
                # bounded non-selectable terminal outcome: honest
                # informational completion (CAPABILITY_GUIDANCE
                # precedent) — NOT a pretended answer, NOT an error.
                task.status = TaskStatusV2.COMPLETED
                task.completed_at = task.started_at = _stamp()
                result = InvestigationTurnResult(
                    task=task,
                    response=(
                        "I can't autonomously determine which finding you "
                        "mean, and clarifying hasn't provided new "
                        "information. You can select a finding from the "
                        "Findings panel yourself, or rephrase with more "
                        "specific details."),
                    context=resolved_ctx,
                    follow_ups=[],
                    context_changed=False,
                    target_resolution={
                        "kind": "non_selectable_terminal",
                        "candidates_considered": len(resolution.candidates),
                        "refinement": refinement_evidence,
                        "clarification": {
                            "pending": False,
                            "terminal": True,
                            "resumable": True,
                            "request_fingerprint": signal.request_fingerprint,
                            "candidate_set_hash": signal.candidate_set_hash,
                            "metadata_status_hash":
                                signal.metadata_status_hash,
                            "reason_class": "non_selectable_terminal",
                        },
                    },
                )
                self._persist(store, result.task)
                return result
            task.status = TaskStatusV2.FAILED
            task.completed_at = task.started_at = _stamp()
            task.error = "clarification_needed"
            clarification_state = None
            if signal is not None:
                clarification_state = {
                    "pending": True,
                    "terminal": False,
                    "resumable": True,
                    "request_fingerprint": signal.request_fingerprint,
                    "candidate_set_hash": signal.candidate_set_hash,
                    "metadata_status_hash": signal.metadata_status_hash,
                }
            result = InvestigationTurnResult(
                task=task,
                response=resolution.clarification_message
                or "Your request matches multiple possibilities — please "
                   "specify which one you mean.",
                context=resolved_ctx,          # unchanged copy
                follow_ups=[],                 # never after ambiguity
                context_changed=False,
                target_resolution={
                    "kind": ("refined_clarify"
                             if refinement_evidence.get("invoked")
                             else "ambiguous"),
                    "candidates_considered": len(resolution.candidates),
                    "refinement": refinement_evidence,
                    "clarification": clarification_state,
                    **({"semantic_grounding": resolution.semantic_evidence}
                       if resolution.semantic_evidence else {}),
                },
            )
            self._persist(store, result.task)
            return result

        if resolution.status == ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE:
            # AL-2.8: the M2 semantic foundation could not run (provider
            # unconfigured / unreachable / malformed) after every
            # deterministic stage failed. An honest OUTAGE terminal —
            # deliberately distinct from unresolved_reference: no finding
            # tool may run on an ungrounded target, nothing is fabricated,
            # and the degraded state is named as what it is.
            task.status = TaskStatusV2.FAILED
            task.started_at = task.completed_at = _stamp()
            task.error = "semantic_resolution_unavailable"
            result = InvestigationTurnResult(
                task=task,
                response=resolution.clarification_message
                or "The semantic resolution service is currently "
                   "unavailable, so I cannot reliably determine which "
                   "finding you mean. Please select the finding directly, "
                   "or retry later.",
                context=resolved_ctx,          # unchanged; no invented focus
                follow_ups=[],
                context_changed=False,
                target_resolution={
                    "kind": "semantic_resolution_unavailable",
                    "semantic": resolution.semantic_evidence,
                    "refinement": refinement_evidence,
                },
            )
            self._persist(store, result.task)
            return result

        if resolution.status == ResolutionStatus.UNRESOLVED:
            task.status = TaskStatusV2.FAILED
            task.started_at = task.completed_at = _stamp()
            task.error = "unresolved_reference"
            result = InvestigationTurnResult(
                task=task,
                response=resolution.clarification_message
                or "I couldn't determine what this request refers to.",
                context=resolved_ctx,          # unchanged; no invented focus
                follow_ups=[],
                context_changed=False,
                target_resolution={
                    "kind": "unresolved",
                    "refinement": refinement_evidence,
                },
            )
            self._persist(store, result.task)
            return result

        # resolved / unchanged_case_level → continue
        task.status = TaskStatusV2.PLANNING
        task.started_at = _stamp()
        self._persist(store, task)

        # --- 2) Eligible skills (registry × capabilities; no duplication) ---
        focused = resolved_ctx.focused_finding_id
        focused_finding = next(
            (f for f in case_findings if f.finding_id == focused), None
        )
        finding_caps = focused_finding.capabilities if focused_finding else None
        eligible = eligible_skills_for_finding(finding_caps)
        eligible_ids = [s.skill_id for s in eligible]

        # --- 2b) Stage 2.1 structured goal representation ----------------------
        # Produced after target resolution, before planning; distinct from
        # tool selection (open-ended baseline §35b). Bounded: failure to
        # understand the goal never blocks the turn.
        goal_representation = None
        if self.goal_resolver is not None:
            try:
                # B1: the resolver convention receives the diagnostics
                # out-param (signature-aware); the legacy turn route has
                # no loop decision record, so the provenance is plumbed
                # without a sink here (documented residual).
                from app.agent_loop import _call_goal_resolver
                goal = _call_goal_resolver(
                    self.goal_resolver, user_request,
                    resolved_ctx.focused_finding_id, resolved_ctx.case_id,
                    {})
                if goal is not None:
                    goal_representation = goal
            except Exception:
                logger.warning("goal representation failed", exc_info=True)

        # --- 3) Planner -------------------------------------------------------
        plan_or_failure = self.planner.plan(
            user_request,
            resolved_ctx,
            eligible_ids,
            finding_capabilities=finding_caps,
            case_findings=case_findings,
        )

        if isinstance(plan_or_failure, PlanningFailure):
            task.status = TaskStatusV2.FAILED
            task.completed_at = _stamp()
            task.error = f"planning_failure:{plan_or_failure.code}"
            result = InvestigationTurnResult(
                task=task,
                planning_failure=plan_or_failure,
                response=compose_response(
                    user_request=user_request, skill_id=None, plan=None,
                    tool_calls=[], execution_errors=[],
                    planning_failure=plan_or_failure,
                    response_language=response_language,
                ),
                context=resolved_ctx,
                follow_ups=[],     # no follow-ups for planning failure
                context_changed=resolution.context_changed,
                target_resolution=target_resolution,
            )
            self._persist(store, result.task)
            return result

        plan = plan_or_failure
        task.plan_id = plan.plan_id
        # selected skill: the eligible skill the plan's steps come from —
        # resolved from the planner's own contract-checked selection context.
        # PlannerV2 doesn't echo skill_id on Plan; derive from task context:
        selected_skill = self._infer_selected_skill(plan, eligible_ids)
        task.selected_skill = selected_skill

        # R2.2.3 WP-2 Version B (S5 forensics fix, user-authorized):
        # Case Intake plans on the /turn surface must honor the Option A
        # boundary — the overview is delivered WITHOUT the artifact
        # bundle (Decision 2: the boundary applies to BOTH paths). The
        # loop enforces this via the U00004 delivery-intent gate; /turn
        # has no contract machinery, so the equivalent deterministic
        # strip runs here: a Case Intake plan (INVESTIGATION intent +
        # the SAME intake recognizers the planner's intake branch used)
        # has its generate_artifact steps removed BEFORE execution.
        # Non-intake /turn requests are untouched.
        from app.context_resolution import (
            RequestIntent as _RI,
            _classify_request_intent as _turn_classify_intent,
        )
        from app.planner_v2 import (
            _is_bound_case_reference as _turn_is_bound_ref,
            _is_plain_case_intake as _turn_is_plain_intake,
        )
        if (plan is not None
                and _turn_classify_intent(user_request) == _RI.INVESTIGATION
                and (_turn_is_plain_intake(user_request)
                     or _turn_is_bound_ref(user_request, resolved_ctx))):
            _intake_artifact_steps = [s for s in plan.steps
                                      if s.type == "generate_artifact"]
            if _intake_artifact_steps:
                plan.steps[:] = [s for s in plan.steps
                                 if s.type != "generate_artifact"]
                for other in plan.steps:
                    other.depends_on = [
                        d for d in (other.depends_on or [])
                        if d not in {s.step_id
                                     for s in _intake_artifact_steps}]
                logger.warning(
                    "WP2 Option A (turn): stripped Case Intake artifact "
                    "step(s) %s — the /turn intake delivers the "
                    "restricted overview WITHOUT an artifact bundle",
                    [s.step_id for s in _intake_artifact_steps])

        # R2.2.3 WP-2 Version B (S3): /turn shares the deterministic
        # admission — NO bypass (frozen /turn requirement). This path has
        # NO GU decision record, so the service is called with
        # hsg_only=False: EVERY no-contract artifact delivery is gated.
        # Case Intake (non-ARTIFACT intent) passes through — Option A
        # behavior unchanged; contract-path runs pass through (the loop
        # owns them).
        _turn_admission = None
        _turn_invalid: list[dict] = []
        if plan is not None and any(
                s.type == "generate_artifact" for s in plan.steps):
            from app.delivery_admission import admit_delivery_targets
            _inv_tasks: set[str] = set()
            try:
                if self.task_store is not None:
                    _inv_tasks = {t.task_id for t in
                                  self.task_store.list_by_investigation(
                                      investigation_id)}
            except Exception:
                logger.exception("admission task listing failed")
            _session_sets = []
            try:
                if self.task_store is not None:
                    _session_sets = (self.task_store.get_session(
                        investigation_id) or {}).get("candidate_sets") or []
            except Exception:
                logger.exception("admission session read failed")
            _turn_admission = admit_delivery_targets(
                goal_contract=None,
                decision_outcome=None,
                request_text=user_request,
                plan_steps=[{"step_id": s.step_id, "type": s.type,
                             "arguments": dict(s.arguments)}
                            for s in plan.steps],
                session_sets=_session_sets,
                investigation_task_ids=_inv_tasks,
                case_id=resolved_ctx.case_id,
                hsg_only=False)
            if _turn_admission.get("mode") == "admitted":
                _bad = {r["step_id"] for r in _turn_admission["invalid"]}
                if _bad:
                    plan.steps[:] = [s for s in plan.steps
                                     if s.step_id not in _bad]
                    for other in plan.steps:
                        other.depends_on = [
                            d for d in (other.depends_on or [])
                            if d not in _bad]
                _turn_invalid = list(_turn_admission["invalid"])
                for inj in _turn_admission["valid"]:
                    for s in plan.steps:
                        if s.step_id != inj["step_id"]:
                            continue
                        s.arguments.update({
                            "source_kind": inj["source_kind"],
                            "set_ref": inj["set_ref"],
                            "candidate_records": inj["candidate_records"],
                            "bounded_data": inj["bounded_data"],
                            "source_task_id": inj["source_task_id"],
                            "expected_records_digest":
                                inj["expected_records_digest"],
                            "expected_binding": inj["expected_binding"],
                            "delivery_target_id":
                                inj["delivery_target_id"],
                            "source_step_id":
                                (inj["expected_binding"] or {})
                                .get("source_step_id"),
                        })
                        break

        # --- 4) Executor -------------------------------------------------------
        task.status = TaskStatusV2.EXECUTING
        self._persist(store, task)

        execution = self.executor.execute(
            plan, task, resolved_ctx, finding_capabilities=finding_caps,
            prior_tool_calls=prior_tool_calls,
            focused_finding=focused_finding,
        )
        # executor mutates/persists the task itself when a store is attached
        # to it; keep our task object in sync for the result payload.
        task = execution.task

        # --- 5) Response composition -------------------------------------------
        response = compose_response(
            user_request=user_request,
            skill_id=task.selected_skill,
            plan=plan,
            tool_calls=execution.tool_calls,
            execution_errors=execution.errors,
            response_language=response_language,
        )

        # --- 5b) Request-coverage verification (three-constraint closure) ------
        # The turn path has no GoalContract: without this check a multi-
        # aspect request is "completed" whenever its tools succeed — tool
        # success is not an answer. The verifier reuses the contract
        # path's verdict semantics over THIS RUN'S real payloads, names
        # every identified aspect that is not determinately answered, and
        # keeps partial answers (a failed aspect never erases the
        # answered ones). Requests matching no supported pattern yield
        # None — their coverage uncertainty is preserved, never invented.
        from app.request_coverage import coverage_lines, verify_coverage
        coverage = verify_coverage(
            user_request,
            [(tc.tool_name, tc.result.data if tc.result is not None
              else None) for tc in execution.tool_calls])
        _coverage_lines = coverage_lines(coverage,
                                         lang=response_language)
        if _coverage_lines:
            response = response.rstrip() + "\n\n" + "\n".join(
                _coverage_lines)
        if coverage is not None:
            target_resolution = {**target_resolution,
                                 "request_coverage": coverage}
        # WP-2 Version B (S3): /turn rejected delivery targets render the
        # pending clarification in the response — no bundle, no delivered
        # mark, per-target reasons listed.
        if _turn_invalid:
            _zh_t = response_language == "zh"
            response = response.rstrip() + "\n\n" + (
                "以下交付目标未通过确定性准入，等待澄清："
                if _zh_t else
                "The following delivery targets failed deterministic "
                "admission and await clarification:")
            for rec in _turn_invalid:
                response += ("\n- DT " + rec["delivery_target_id"]
                             + " fail-closed (" + rec["reason"] + ")")

        # --- 6) Follow-ups (only after a valid structured result) ---------------
        follow_ups: list[FollowUp] = []
        structured_ok = (
            execution.status == TaskStatusV2.COMPLETED
            and any(
                tc.result is not None
                and tc.result.outcome in (
                    ToolResultOutcome.SUCCESS, ToolResultOutcome.EMPTY,
                )
                for tc in execution.tool_calls
            )
        )
        if structured_ok:
            trigger = trigger_hint or (
                TriggerReason.SUCCESSFUL_TOOL_RESULT
                if any(tc.result and tc.result.outcome == ToolResultOutcome.SUCCESS
                       for tc in execution.tool_calls)
                else TriggerReason.TASK_COMPLETED
            )
            caps_ids = sorted(finding_caps.root) if finding_caps is not None else []
            follow_ups = select_followups(SelectionInput(
                trigger=trigger,
                context=resolved_ctx,
                finding_capabilities=caps_ids,
            ), lang=response_language)

        # --- 7) Context Update ---------------------------------------------------
        # Explicit only: the context returned is the resolution's context.
        # We never change focus because a tool returned data; user_selected
        # focus is preserved untouched (resolution already guarantees this).
        final_ctx = resolved_ctx

        result = InvestigationTurnResult(
            task=task,
            plan=plan,
            execution=execution,
            response=response,
            context=final_ctx,
            follow_ups=follow_ups,
            context_changed=resolution.context_changed,
            target_resolution=target_resolution,
            goal_representation=goal_representation,
        )
        self._persist(store, result.task)
        return result

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _infer_selected_skill(plan: Plan, eligible_ids: list[str]) -> str | None:
        """PlannerV2 returns a Plan without echoing skill_id; infer the
        selected skill deterministically as the eligible skill whose planning
        vocabulary exactly contains the plan's step types."""
        plan_types = [s.type for s in plan.steps]
        for sid in eligible_ids:
            vocab = SKILLS[sid].planning_steps
            if plan_types and all(t in vocab for t in plan_types):
                # prefer the most specific (non-case-level) match
                if sid != "case_intake" or len(eligible_ids) == 1:
                    return sid
        # fall back: first eligible whose vocabulary contains any step type
        for sid in eligible_ids:
            if any(t in SKILLS[sid].planning_steps for t in plan_types):
                return sid
        return eligible_ids[0] if eligible_ids else None

    @staticmethod
    def _persist(store: TaskStoreV2 | None, task: TaskV2) -> None:
        if store is not None:
            store.update(task)


def _stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

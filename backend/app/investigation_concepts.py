"""Shared investigation-concept matchers — single source of truth.

AL-2.6 root cause: three layers maintained divergent keyword sets for the
same investigation concept. goal_resolution recognized 对冲/hedge (display
only), planner_v2 recognized 跨账户/对倒 (post-LLM specialization), and
context_resolution recognized only English phrasings ("those accounts") —
so a real user's case-level request ("查一下有没有不同账户之间的对冲交易
情况") was correctly UNDERSTOOD by the goal layer, then rejected as
UNRESOLVED by the routing layers before the planner could ever run.

This module owns the vocabulary. Two predicates, both derived from the
SAME two element lists (no second vocabulary):

- mentions_cross_account(text)      — scope element only. Canonical
  delegation target for the planner's existing cross-account wording
  check (behavior-preserving superset of the former planner regex).
- is_cross_account_trade_analysis_request(text) — the GOVERNED ROUTING
  predicate: cross-account scope AND a trade/hedging subject. This is
  what may route a case-level request into case-level analytical
  planning. Requiring BOTH elements is deliberate: a request that names
  accounts without the trade/hedging subject (e.g. "比较不同账户的提现
  频率") has no supported case-level analytical tool and must NOT be
  routed here (negative control).
"""

from __future__ import annotations

import re

# Scope element: the request talks about relationships ACROSS accounts
# (not within one account). Superset of the former planner regex
# (cross[\s-]*account | across/between/related accounts | 跨账户 | 对倒).
# AL-2.10: the 账号-family forms are added — real users say 「其他账号和
# 这个账号之间」「跨账号」 with 号, and the missing vocabulary sent
# supported case-level cross-account requests into the finding semantic
# resolver instead of the governed capability route (AL-2.10 audit).
_CROSS_ACCOUNT_ELEMENTS: tuple[str, ...] = (
    "cross account", "cross-account", "crossaccount",
    "across accounts", "between accounts", "related accounts",
    "跨账户", "跨賬戶", "跨账号", "跨賬號",
    "对倒", "對倒",
    "不同账户", "不同賬戶", "不同账号", "不同賬號",
    "账户之间", "賬戶之間", "账户间", "賬戶間",
    "账号之间", "賬號之間", "账号间", "賬號間",
    "其他账户", "其他賬戶", "其他账号", "其他賬號",
    "别的账户", "别的賬戶", "别的账号", "别的賬號",
)

# Subject element: the analyzed subject is trading/hedging activity —
# the frozen capability is the cross-account OPPOSITE-TRADE analysis
# (对倒/对向 are the same domain concept as 对冲 in this product's
# risk vocabulary).
_TRADE_ANALYSIS_ELEMENTS: tuple[str, ...] = (
    "trade", "trading", "transaction",
    "对冲", "對沖", "对倒", "對倒", "对向", "交易",
    "hedge", "hedging",
)


def _normalized(text: str) -> str:
    """Lowercase + whitespace-collapsed (CJK needs no tokenization; the
    element substrings are matched verbatim)."""
    return " ".join((text or "").lower().split())


def mentions_cross_account(text: str) -> bool:
    """Whether the text names cross-account (inter-account) scope."""
    t = _normalized(text)
    return any(e in t for e in _CROSS_ACCOUNT_ELEMENTS)


def is_cross_account_trade_analysis_request(text: str) -> bool:
    """GOVERNED ROUTING predicate: supported case-level cross-account
    trade-analysis investigation intent (scope + subject both present)."""
    t = _normalized(text)
    return (any(e in t for e in _CROSS_ACCOUNT_ELEMENTS)
            and any(e in t for e in _TRADE_ANALYSIS_ELEMENTS))


# AL-2.10: related-account / network analysis intent — a SECOND supported
# case-level capability. Deliberately narrow (account-relation vocabulary
# only) and evaluated AFTER the trade predicate so a cross-account TRADE
# request (「其他账号…对冲交易」) can never be captured here.
_RELATED_ACCOUNT_ELEMENTS: tuple[str, ...] = (
    "关联账号", "關聯賬號", "关联账户", "關聯賬戶",
    "相关账号", "相關賬號", "相关账户", "相關賬戶",
    "账号关联", "賬號關聯", "账户关联", "賬戶關聯",
    "哪些账号", "哪些賬號", "哪些账户", "哪些帳戶",
    "related accounts to", "which accounts are related",
)


def is_related_account_analysis_request(text: str) -> bool:
    """GOVERNED ROUTING predicate: case-level related-account analysis
    intent (「哪些账号是这个账号的关联账号」). A cross-account TRADE
    request takes precedence (caller evaluates the trade predicate
    first); this predicate never captures one."""
    t = _normalized(text)
    if is_cross_account_trade_analysis_request(text):
        return False
    return any(e in t for e in _RELATED_ACCOUNT_ELEMENTS)


# ---------------------------------------------------------------------------
# AL-2.8: finding-level investigation intents. IDENTIFICATION (which
# finding) and INTENT (what to do with it) are separate decisions — these
# predicates answer ONLY the intent half, and the vocabulary mirrors the
# goal layer's recognition table (explain/why; evidence) so intent typing
# and intent routing cannot drift apart.
# ---------------------------------------------------------------------------
_EXPLAIN_INTENT_RE = re.compile(
    r"为什么|為什麼|怎么会|怎麼|why\b|explain", re.IGNORECASE)
# AL-2.9: the user's own record vocabulary includes 明细 ("交易明细") —
# strongly record-flavored, so it joins 证据/evidence as concrete-evidence
# intent. Deliberately narrow: English "details" is NOT added (vague
# explanatory language must keep planning the explanation level).
_EVIDENCE_INTENT_RE = re.compile(r"证据|證據|evidence|明细|明細",
                                 re.IGNORECASE)


def is_explain_finding_request(text: str) -> bool:
    """The user asks WHY something is a risk (explain-the-signal intent)."""
    return bool(_EXPLAIN_INTENT_RE.search(text or ""))


def is_evidence_request(text: str) -> bool:
    """The user asks for the concrete evidence behind a finding."""
    return bool(_EVIDENCE_INTENT_RE.search(text or ""))

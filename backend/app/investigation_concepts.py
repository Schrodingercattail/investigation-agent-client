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
    # U00029 B-回归: EN population vocabulary ("linked accounts" IS the
    # related-account population in EN phrasing)
    "linked account", "related account",
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
    # U00029 B-回归: EN population vocabulary
    "linked account", "related account",
    "相关账号", "相關賬號", "相关账户", "相關賬戶",
    "账号关联", "賬號關聯", "账户关联", "賬戶關聯",
    "哪些账号", "哪些賬號", "哪些账户", "哪些帳戶",
    "related accounts to", "which accounts are related",
)


# Account-listing delivery intent: the request asks for the related
# accounts THEMSELVES (list/details/names), not merely mentions 关联账户
# inside a metric-question predicate (e.g. 「是关联账户的对冲交易比例吗」).
_ACCOUNT_DELIVERY_MARKERS: tuple[str, ...] = (
    "列出", "清单", "明细", "名单", "发我", "发一下", "列一下",
    "哪些账号", "哪些账户", "哪些关联",
    # U00029 B-回归: EN delivery intent
    "send the details", "details to me", "send me the", "list all",
)


def has_account_delivery_intent(text: str) -> bool:
    """Whether the request carries delivery intent toward the related
    accounts (list/details/names) — used to distinguish a genuine
    related-accounts deliverable from a bare 关联账户 mention inside a
    metric-question predicate."""
    t = _normalized(text)
    return any(m in t for m in _ACCOUNT_DELIVERY_MARKERS)


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


# ---------------------------------------------------------------------------
# Request-coverage vocabulary (deterministic aspect identification).
# Bounded, single-sourced predicates naming the SUPPORTED sub-questions the
# runtime can verify — the same vocabulary the goal layer's deterministic
# contract proposer and the turn-path coverage verifier both read, so the
# two surfaces can never disagree about which aspects a request contains.
# Deliberately narrow: an unmatched request keeps its coverage uncertainty
# (no invented aspects), matching the bounded-vocabulary rule above.
# ---------------------------------------------------------------------------

# The reverse-trade-RATE METRIC sub-question (「34.38%反向交易率指的是…」):
# the user asks what the opposite_trade_ratio figure MEANS / measures.
# Distinct from the cross-account ANALYSIS ask (evaluated by the routing
# predicate above) — a request may contain one, the other, or both.
_REVERSE_TRADE_RATE_RE = re.compile(
    r"反向交易率|对向交易率|反向交易比例|opposite[\s-]*trade[\s-]*ratio",
    re.IGNORECASE)


def mentions_reverse_trade_rate(text: str) -> bool:
    """The user asks about the opposite_trade_ratio metric itself
    (its meaning / measurement scope)."""
    return bool(_REVERSE_TRADE_RATE_RE.search(text or ""))


# The withdrawal new-address sub-question (「哪些笔提现是新地址」): the
# user asks WHICH withdrawals carry the is_new_address marker. Requires
# BOTH the withdrawal subject and the new-address concept (AND rule —
# same false-positive discipline as the comparison detector).
_WITHDRAWAL_SUBJECT_RE = re.compile(r"提现|提現|取款|withdrawal", re.IGNORECASE)
_NEW_ADDRESS_RE = re.compile(
    r"新地址|新錢包地址|new[\s-]*address"
    r"|newly[\s-]*encountered[\s-]*address", re.IGNORECASE)
# U00010 F2/F3: a citation ORIGINAL-TEXT ask — the deliverable is the
# policy citation's own document/section text. The 关联账号 phrase inside
# such an ask scopes WHICH citations, it never demands the network
# attribution list (the journey contract: citation asks must not be
# answered by network/device attribution alone).
_POLICY_CITATION_ORIGINAL_RE = re.compile(
    r"引文原文|政策引文|政策原文|引用原文|原文发我|原文发给我|原文给我"
    r"|original text of (the |this |its )?(citation|policy|reference)"
    r"|(citation|policy|reference) original text"
    r"|full text of (the |this |its )?(citation|policy|reference)"
    r"|quote the (policy|citation)"
    r"|original (policy |)citations?",
    re.IGNORECASE)


def strip_finding_title_mentions(text: str, case_findings) -> str:
    """Mask known finding-TITLE mentions (case/whitespace/hyphen
    tolerant, with their wrapping brackets) in a request text.

    A mentioned finding name is a REFERENCE, never a per-record
    retrieval demand: the F3 policy ask “发现 F3（First Withdrawal to
    New Address）适用哪些政策要求？” carries both aspect keywords inside
    the quoted title, which must not by itself mount the per-record
    new-address check. Works for any finding title — nothing here is
    tailored to one fixture."""
    if not text or not case_findings:
        return text
    titles: list[str] = []
    for f in case_findings:
        title = (f.get("title") if isinstance(f, dict)
                 else getattr(f, "title", "")) or ""
        title = str(title).strip()
        if len(title) >= 8:               # distinctive titles only
            titles.append(title)
    out = text
    for title in sorted(set(titles), key=len, reverse=True):
        words = [re.escape(w) for w in title.split() if w]
        if not words:
            continue
        pattern = re.compile(
            r"[（(]?\s*" + r"[\s\-–—]*".join(words) + r"\s*[)．.]?[）)]?",
            re.IGNORECASE)
        out = pattern.sub(" ", out)
    return out


def mentions_withdrawal_new_address(text: str,
                                    case_findings=None) -> bool:
    """The user asks which withdrawals were sent to new addresses
    (the per-record is_new_address deliverable).

    When case findings are supplied, known finding-title mentions are
    masked FIRST: a quoted title whose own words contain the aspect
    keywords (“First Withdrawal to New Address”) is a reference, not a
    demand — only the user's own words can trigger the per-record
    check. Genuine asks (“哪些提现被标记为新地址？” / “List the
    withdrawals flagged as new addresses.”) are unaffected."""
    t = strip_finding_title_mentions(text or "", case_findings)
    return bool(_WITHDRAWAL_SUBJECT_RE.search(t)
                and _NEW_ADDRESS_RE.search(t))


def is_policy_citation_original_text_request(text: str) -> bool:
    """U00010 F2/F3: the request asks for the citation's ORIGINAL TEXT
    (政策引文原文 / citation original text) — the policy-citation delivery,
    not the related-account network list that merely shares the topic."""
    return bool(_POLICY_CITATION_ORIGINAL_RE.search(text or ""))


def mentions_withdrawals(text: str) -> bool:
    """The request's subject is withdrawal records (shared subject
    element of the withdrawal sub-questions)."""
    return bool(_WITHDRAWAL_SUBJECT_RE.search(text or ""))


# Rule-criteria vocabulary: the user asks HOW a rule/threshold judges
# (「判断标准」「怎样算高频提现」「多少笔会触发」) — the detection-rule
# explanation intent, answered from the finding's authoritative rule data
# (signal explain), NOT from policy retrieval. Same bounded-element
# discipline as the matchers above; policy wording elsewhere in the
# request keeps the policy route (the planner composes explain+policy).
# Anaphoric finding reference: the request refers to the CURRENTLY
# FOCUSED finding by deixis (“this finding” / 该发现) rather than by name —
# the finding identity lives in the context focus, not in the request text.
_ANAPHORIC_FINDING_RE = re.compile(
    r"this finding|the current finding|当前发现|该发现|此发现|該發現|當前發現",
    re.IGNORECASE)


def is_policy_requirements_request(text: str) -> bool:
    """The user asks WHICH POLICY REQUIREMENTS apply — the policy-basis
    ask (distinct from citation-original-text phrasings). Combined with
    is_anaphoric_finding_reference it identifies the anaphoric policy
    question “Which policy requirements apply to this finding?”. """
    return bool(re.search(
        r"policy requirements|policies apply|适用哪些政策|哪些政策要求|"
        r"政策要求|政策依据",
        text or "", re.IGNORECASE))


def is_anaphoric_finding_reference(text: str) -> bool:
    """Whether the request refers to the currently focused finding by
    deixis — the finding identity must then come from the context focus,
    never from domain re-inference over the request text."""
    return bool(_ANAPHORIC_FINDING_RE.search(text or ""))


_RULE_CRITERIA_RE = re.compile(
    r"判断标准|判定标准|认定标准|怎样算|怎麼算|怎么算|如何判定|怎麼判定|"
    r"判定为|判定為|算不算|"
    r"什么情况|哪些情况|什麼情況|哪些情況|何种情况|何種情況|"
    r"多少笔|多少次|阈值|閾值|门槛|門檻|判定条件|判定條件|"
    r"what[\s-]*counts[\s-]*as|what[\s-]*situation|"
    r"how[\s-]*is[\s-]*(it|a|an)?[\s-]*determined|"
    r"threshold|criteria",
    re.IGNORECASE)


def is_rule_criteria_request(text: str) -> bool:
    """The user asks for a rule/threshold's criteria (how something is
    judged, how many trigger it) — the detection-rule explanation
    intent. Part of the explain-intent family: callers treat it exactly
    like is_explain_finding_request."""
    return bool(_RULE_CRITERIA_RE.search(text or ""))


# Set-internal cross-account analysis intent (「这些账户之间是否存在对冲」):
# the user FIRST names a related-account population and THEN asks about
# hedging WITHIN that population. The analysis target is the SET
# {case account} ∪ {related accounts} — candidate pairs may involve the
# case account or only two related members. Distinct from the
# target-anchored ask ("这个账户和其他账户之间有没有对冲") where every
# candidate must involve the case account (Case-4 semantics). The set
# predicate wins when both wordings appear: the population framing
# ("这些账户之间") is the narrower, deliberate scope.
_SET_INTERNAL_MARKERS: tuple[str, ...] = (
    "这些账户之间", "這些賬戶之間", "这些账号之间", "這些賬號之間",
    "关联账户之间", "關聯賬戶之間", "关联账号之间", "關聯賬號之間",
    # "关联账户中(是否有对冲…)": the population-framed hedging ask
    # without the 之间 form — still a set-internal request (the analysis
    # scope is the related-account set, not the case account alone)
    "关联账户中", "關聯賬戶中",
    # B-fix: the 之-less contractions ("关联账号间是否存在对冲交易") name
    # the same population-framed ask. The bare 账号间/账户间 forms are
    # only recognized with the 是否存在 hedging frame, so a plain
    # cross-account mention ("其他账户之间") never becomes set-internal.
    "关联账号间", "關聯賬號間", "关联账户间", "關聯賬戶間",
    "账号间是否存在", "賬號間是否存在", "账户间是否存在", "賬戶間是否存在",
    "账户集合内部", "集合内部", "账户之间是否存在", "账号之间是否存在",
    "among the related accounts", "between the related accounts",
    "between these accounts", "among these accounts",
    # U00029 B-回归: EN population-framed hedging ask
    # ("…any hedge transaction within linked accounts")
    "within linked account", "within the linked account",
    "among linked account", "between linked account",
    "across linked account",
)


# Party-anchored guard: when a set-internal marker is immediately
# preceded by a "with/and" connector, the named population is the CASE
# ACCOUNT'S counterparty ("U00008 与关联账号之间") — the target-anchored
# scope, deliberately NOT set-internal.
_ANCHOR_PARTY_CONNECTORS = ("与", "和", "跟", "同", "及")
_ANCHOR_PARTY_SPAN = __import__("re").compile(
    r"(与|和|跟|同|及)(关联账号|关联账户|其他账号|其他账户|别的账号|"
    r"别的账户|这些账号|这些账户)(之?间)")


# Existence-check frame for a POPULATION-NAMED hedging ask (see
# is_related_account_set_trade_analysis): the frame itself must carry the
# set-internal placement (账号中/里 + 是否有对冲) — a bare 有没有对冲 next
# to 这个账号 is TARGET-anchored (「这个账号有没有对冲交易？」) and must
# never route set-internal (CG1 compound-extraction guard).
_SET_EXISTENCE_RE = __import__("re").compile(
    r"(关联账号|关联账户|账号|账户)(中|里|之内|里面)是否有?对冲|"
    r"(关联賬號|關聯賬戶|賬號|賬戶)(中|里|之內|裡面)是否有?對沖|"
    r"is there any hedge|any hedge transaction|hedge transaction within|"
    r"linked accounts? (contain|contains|have|has) hedge",
    __import__("re").IGNORECASE,
)


def is_related_account_set_trade_analysis(text: str) -> bool:
    """GOVERNED ROUTING predicate: the user asks for hedging/counter-trade
    candidates WITHIN a named related-account population (set-internal
    pairs — the case account is one member of the set, not the only
    anchor). Requires BOTH the set-internal framing AND the trade/hedging
    subject."""
    t = _normalized(text)
    if not any(e in t for e in _TRADE_ANALYSIS_ELEMENTS):
        return False
    # U00029 B-回归: a POPULATION-NAMED existence ask — the related/linked
    # account population is named AND the hedging ask is an EXISTENCE
    # check over it ("查询账号中是否有对冲交易" / "check if there is any
    # hedge transaction within linked accounts"). Evaluated BEFORE the
    # marker gate: this phrasing names the population without a 之间/内部
    # marker. The metric-question shape (「是关联账户的对冲交易比例吗」)
    # never matches the existence frame and stays excluded (same-account
    # metric semantics, §8).
    if _SET_EXISTENCE_RE.search(t):
        related = any(e in t for e in _RELATED_ACCOUNT_ELEMENTS)
        hedging = any(e in t for e in ("对冲", "對沖", "hedge", "hedging"))
        if related and hedging and not mentions_reverse_trade_rate(text):
            return True
    if not any(m in t for m in _SET_INTERNAL_MARKERS):
        return False
    # Party-anchored exclusion (B-fix): "U00008 与/和 关联账号(之)间…"
    # names the population as the CASE ACCOUNT'S counterparty — the
    # target-anchored scope. Strip those spans first; a set-internal
    # marker surviving outside them still qualifies.
    t_wo_party = _ANCHOR_PARTY_SPAN.sub("", t)
    return any(m in t_wo_party for m in _SET_INTERNAL_MARKERS)


# ---------------------------------------------------------------------------
# Explain-compound decomposition vocabulary: the user asks WHY one or more
# risk signals are risk items (「高频交易和提现到新地址为什么是风险项」).
# Each domain maps the user's words to the finding-title vocabulary of the
# SAME case — the actual findings are resolved from the case's OWN
# metadata (never hardcoded ids): a domain only yields a target when this
# case's findings carry the matching title vocabulary.
# ---------------------------------------------------------------------------
_EXPLAIN_DOMAINS: tuple[tuple[str, str, str], ...] = (
    # (domain_id, request element, finding-title element)
    ("trading_frequency", "高频交易", "trading frequency"),
    ("trading_frequency", "交易频率", "trading frequency"),
    ("trading_frequency", "频繁交易", "trading frequency"),
    ("withdrawal_new_address", "提现到新地址", "new address"),
    ("withdrawal_new_address", "新地址提现", "new address"),
    ("withdrawal_new_address", "首次提现", "new address"),
    # F3 EN delivery gap (U00008): the finding's own English name is the
    # natural way EN requests name this domain — without these elements
    # EN criteria asks derived the F2 explain deterministically while the
    # F3 explain depended on goal-understanding LLM variance.
    ("withdrawal_new_address", "first withdrawal", "new address"),
    ("withdrawal_new_address", "withdrawal to new address", "new address"),
    ("withdrawal_frequency", "高频提现", "withdrawal frequency"),
    ("withdrawal_frequency", "提现频率", "withdrawal frequency"),
    ("withdrawal_frequency", "high withdrawal frequency",
     "withdrawal frequency"),
    ("new_account_high_activity", "新账号高频活动",
     "new account with high activity"),
    ("new_account_high_activity", "新账号高频", "new account"),
    ("new_account_high_activity", "new account high activity",
     "new account with high activity"),
    ("shared_device", "共享设备", "shared device"),
    ("linked_account", "关联账号网络", "linked account"),
    ("linked_account", "关联账户网络", "linked account"),
    # U00010 F2/F3: population-scoped citation asks (「与关联账号相关的
    # 政策引文」/ "citations related to the linked accounts") — the
    # population phrase resolves to THIS case's linked-account finding
    # (title-element governance, exactly the same match discipline as the
    # entries above; EN request element mirrors the title vocabulary).
    ("linked_account", "关联账号相关", "linked account"),
    ("linked_account", "linked account", "linked account"),
    ("ml_pattern", "ml pattern", "ml pattern"),
    ("ml_pattern", "机器学习检测", "ml pattern"),
    ("opposite_trade_ratio", "反向交易率", "opposite trade"),
)


def detect_explain_domains(text: str) -> list[str]:
    """The distinct risk-signal domains the request names, in stable
    first-mention order. Bounded vocabulary; an unmatched request yields
    no domains (no invented decomposition)."""
    t = _normalized(text)
    domains: list[str] = []
    for domain_id, request_element, _title in _EXPLAIN_DOMAINS:
        if request_element in t and domain_id not in domains:
            domains.append(domain_id)
    return domains


_COMPARISON_CONTEXT_RE = re.compile(
    r"相同|不同|同一|逐笔|哪些笔|状态|is_new_address|same|different|"
    r"which withdrawals|record by record", re.IGNORECASE)


def _strip_comparison_clauses(text: str) -> str:
    """Drop the clauses of a request that ask for a COMPARISON or
    per-record STATUS delivery (U00020 fix B): “提现到新地址是相同地址
    还是提向不同地址” mentions the new-address domain while asking about
    destination identity — that mention is a records/status intent, not
    a “why is this finding flagged” ask, and must not derive an F3
    rule-explanation output. Why/criteria clauses are untouched."""
    t = text or ""
    if not _COMPARISON_CONTEXT_RE.search(t):
        return t
    kept = [c for c in re.split(r"[。！？!?\n，,；;]", t)
            if c.strip() and not _COMPARISON_CONTEXT_RE.search(c)]
    # a fully-comparison text yields "" — no explain domains at all
    return "。".join(kept)


def findings_for_explain_domains(
    text: str, findings: list[Any],
    drop_comparison_context: bool = False,
) -> list[tuple[str, Any]]:
    """(domain_id, finding) pairs for every detected domain whose case
    carries a metadata-matched finding (title vocabulary). Order follows
    first-mention; a domain with no matching finding in THIS case is
    skipped — never guessed.

    drop_comparison_context (U00020 fix B): when True, clauses that ask
    for a comparison or per-record status delivery are excluded BEFORE
    domain matching, so a domain named inside such a clause does not
    derive a rule-explanation output. Anchoring callers keep the default
    (ungated) behavior."""
    t = _normalized(_strip_comparison_clauses(text)
                    if drop_comparison_context else text)
    matched: list[tuple[str, Any]] = []
    seen_findings: set[str] = set()
    for domain_id, request_element, title_element in _EXPLAIN_DOMAINS:
        if request_element not in t:
            continue
        for f in findings or []:
            title = _normalized(getattr(f, "title", "") or "")
            if title_element in title and f.finding_id not in seen_findings:
                matched.append((domain_id, f))
                seen_findings.add(f.finding_id)
                break
    # dedupe by finding (one finding = one explain output), keep order
    return matched

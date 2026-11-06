"""GSP WP-GSP-4 — Deterministic Postcondition Evaluator.

Pure, side-effect-free semantic evaluation of one GoalContract's
required outputs against the run's associated observations.

    association (WP-GSP-3) → postcondition evaluation (HERE)
                           → OutputSatisfaction (single write point:
                              the loop applies the returned results)

Properties (normative):
- pure function: no LLM, no network, no DB, no tool execution, no
  mutation of its inputs.
- inputs: the accepted GoalContract + the run's observations (the same
  projected dicts AgentLoopState persists — including for_outputs /
  actual_evidence_types / per-tool summary whitelists).
- evidence facts come ONLY from those observation payloads; capability
  declarations are never read here (capability ≠ actual evidence).
- completeness is judged ONLY from fields the real tool contracts
  provide (counts, complete flags, truncation markers, analysis
  status). Empty results are a determinate NEGATIVE answer only when
  the completeness gate passes; otherwise empty/missing → UNRESOLVED.
- assessment-kind claims (domain judgments, e.g. money laundering) are
  NOT adjudicable by this runtime: they evaluate to UNRESOLVED with
  the gathered evidence recorded — the system never fabricates a
  domain conclusion.

Satisfaction semantics per state:
- SATISFIED            grounded determinate answer in scope — with
                       polarity: positive (facts found) OR negative
                       (authoritative absence)
- PARTIALLY_SUPPORTED  grounded but scope/partially limited
- NOT_SATISFIED        reserved (explicit counter-evidence; no MVP
                       handler writes it)
- UNRESOLVED           evidence insufficient / judgment not adjudicable
- UNSUPPORTED          reserved for planning-time verdicts (WP-GSP-6)
- PENDING              never written here
"""

from dataclasses import dataclass, field
import re
from enum import Enum
from typing import Any

from app.goal_resolution import (
    ClaimKind,
    GoalContract,
    OutputSatisfaction,
    RequiredOutput,
)
from app.skills import EvidenceType

POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"

# reason codes
R_NO_ASSOCIATED_EVIDENCE = "no_associated_evidence"
R_JUDGMENT_NOT_ADJUDICABLE = "judgment_not_adjudicable"
R_SUBJECT_PRESENT = "subject_present_in_relations"
R_SUBJECT_ABSENT_COMPLETE = "subject_absent_complete_scope"
R_SCOPE_TRUNCATED = "scope_truncated"
R_RECORDS_RETRIEVED_COMPLETE = "records_retrieved_complete"
# L1: found > chat-emitted for a records-LISTING deliverable — the honest
# disclosed-partial state (mirrors R_CANDIDATE_CAP_PARTIAL; diagnostic
# string, not a state enum).
R_RECORDS_CAP_PARTIAL = "records_cap_partial"
# L1: the chat renderer's records-listing cap. SINGLE definition — the
# agent_loop composer imports this; the 20-row observation projection cap
# and the 10-row chat cap are distinct and must never drift.
RECORDS_CHAT_CAP = 10
# U00047 delivery closure: file_export delivery verified — the generated
# file carries the complete retrieved record set (tool-layer parity
# passed); dialogue display caps do not apply to this channel.
R_RECORDS_FILE_DELIVERED_COMPLETE = "records_file_delivered_complete"
R_RECORDS_EMPTY_COMPLETE = "records_empty_complete_scope"
R_RECORDS_INCOMPLETE = "records_incomplete"
R_EXPLANATION_AVAILABLE = "explanation_available"
R_EXPLANATION_PARTIAL = "explanation_partial_gap"
R_RELATIONS_AVAILABLE = "relations_available"
R_FIELD_PRESENT = "authoritative_field_present"
R_FIELD_ABSENT = "required_field_absent"
R_UNDETERMINED = "facts_indeterminate"
R_CONFLICTING_OBSERVATIONS = "conflicting_observations"
# WP-GS-D (design §7.2): destination-identity facet verdict reason codes
R_DEST_COMPARISON_COMPLETE = "destination_comparison_complete"
R_DEST_IDENTITY_PARTIAL = "destination_identity_partial"
R_COMPARISON_FACTS_ABSENT = "comparison_facts_absent"
# Population-shortfall invariant: the request asserts a comparison
# population LARGER than the authoritative (completely retrieved)
# population — the comparison of the retrieved subset can never satisfy
# the asserted full-population comparison.
R_POPULATION_SHORTFALL = "population_shortfall"
R_NEW_ADDRESS_STATUS_COMPLETE = "new_address_status_complete"
R_NEW_ADDRESS_PARTIAL = "new_address_status_partial"
# Reverse-trade-metric-semantics facet: the authoritative
# opposite_trade_ratio echo (the metric's meaning/口径 deliverable).
R_METRIC_SEMANTICS_COMPLETE = "reverse_trade_metric_semantics_complete"
# Cross-scope scalar isolation: scalar facts that agree WITHIN each
# declared analysis_scope but differ ACROSS scopes (e.g. the same
# observation fact re-observed under target_account and
# related_account_set) are complementary scope-shaped deliveries, not
# contradictions. The isolation lives in _combine_summaries (partition by
# the payload's own declared analysis_scope) — there is NO key whitelist
# and NO per-facet exemption: same-scope disagreements of ANY key stay
# conflicts, and only payloads that declare a scope may claim cross-scope
# complementarity.
SCOPE_FACTS_KEY = "__scope_facts__"
# Set-internal cross-account analysis (analysis_scope=related_account_set):
# candidates may involve the case account or only two related members —
# subject membership is NOT the scope criterion; set membership is.
R_SET_CANDIDATES_FOUND = "set_internal_candidates_found"
R_SET_NO_CANDIDATES_COMPLETE = "set_internal_no_candidates_complete"
R_SET_SCOPE_TRUNCATED = "set_internal_scope_truncated"
R_SET_MEMBERSHIP_VIOLATION = "set_membership_violation"
# System candidate cap: found > emitted — the report carries only the
# top-emitted candidates; the remainder is undelivered in this answer.
R_CANDIDATE_CAP_PARTIAL = "candidate_cap_partial"
# Explanation-type evidence requirements: when an output demands any of
# these, a case-summary bundle alone is context, never the deliverable
# (see the CASE_SUMMARY satisfaction branch). Value-keyed: contract reqs
# arrive as plain strings and str-Enum members hash by NAME, not value.
_EXPLANATION_EVIDENCE_REQ_VALUES = frozenset({
    EvidenceType.RULE_EXPLANATION.value,
    EvidenceType.ML_SIGNAL_EXPLANATION.value,
    EvidenceType.GRAPH_CLUSTER_FACTS.value,
})


def requires_explanation_evidence(reqs) -> bool:
    """True when the requirement list demands explanation-type evidence
    (rule/signal/cluster facts) — the contract's own declaration that the
    deliverable is a WHY explanation, not a data listing."""
    for r in (reqs or []):
        v = r.value if hasattr(r, "value") else str(r)
        if v in _EXPLANATION_EVIDENCE_REQ_VALUES:
            return True
    return False


def _claim_references_prior_detection(claim: str | None) -> bool:
    """Plan v2.1 scope guard input (CONSERVATIVE — blocks satisfaction,
    never grants it): bounded markers over the ACCEPTED contract claim
    indicating the request anaphorically references hedge candidates
    detected in an earlier turn. Applied ONLY to gate a file_export
    delivery whose claim points at prior detection results but whose
    evidence carries no candidate-set detail (real failure
    INV-9808752cbc32: 85 account transactions exported for a 6-candidate
    hedge-detail ask). Never used to grant or inherit anything."""
    if not claim:
        return False
    markers = ("以上检测到的", "以上你检测到的", "上述检测到的",
               "上述已检测到的", "以上已检测到的", "先前已检测到的",
               "检测到的对冲", "上述对冲", "以上对冲",
               "这些对冲", "之前检测到的", "先前检测到的")
    return any(m in claim for m in markers)


def _has_explanation_facts(summary: dict) -> bool:
    """The existing explanation fact shapes (any producing tool): a rule
    hit, an ML score, cluster attribution, feature facts, or the RP
    risk-factor entry. A bare relation list has none of these."""
    if not isinstance(summary, dict):
        return False
    if summary.get("rule_name") or summary.get("cluster_name") \
            or summary.get("feature_facts") or summary.get("risk_factor"):
        return True
    return summary.get("ml_score") is not None


@dataclass
class PostconditionResult:
    """Evaluation verdict for one RequiredOutput."""
    output_id: str
    satisfaction: OutputSatisfaction
    polarity: str | None = None            # positive | negative | None
    reason_code: str | None = None
    observation_indexes: list[int] = field(default_factory=list)
    evidence_types: list[str] = field(default_factory=list)
    completeness: str | None = None        # complete | truncated | partial | absent_field
    conflicts: list[str] = field(default_factory=list)  # conflicting scalar fact keys
    detail: str | None = None


def _fact_observation_indexes(output_id: str,
                              observations: list[dict]) -> list[int]:
    return [i for i, o in enumerate(observations)
            if output_id in (o.get("for_outputs") or [])]


def _as_values(types) -> list[str]:
    return [t.value if hasattr(t, "value") else str(t) for t in (types or [])]


def _merge_disjoint_stream_summaries(observations: list[dict]) -> dict | None:
    """R4 (U00010 F1): complementary single-stream deliveries of ONE
    multi-stream retrieval output — their `streams` dicts are pairwise
    key-DISJOINT (withdrawals vs transactions), so scalar disagreements
    (7 withdrawals vs 64 transactions) are complementary populations,
    NOT conflicts. Merge into one synthetic summary: counts sum, rows
    concatenate, `complete` ANDs, streams union, destination-identity
    blocks concatenate. Returns None when the observations are NOT
    disjoint streams (same-stream duplicates keep the unanimity/conflict
    rules), so nothing else changes."""
    summaries = [o.get("summary") or {} for o in observations]
    if len(summaries) < 2:
        return None
    streams_list = []
    for s in summaries:
        st = s.get("streams") or {}
        if not isinstance(st, dict) or not st:
            return None
        streams_list.append(st)
    seen: set = set()
    for st in streams_list:
        if set(st.keys()) & seen:
            return None            # same stream twice → unanimity path
        seen |= set(st.keys())
    merged: dict = {}
    for s in summaries:
        for k, v in s.items():
            if k in ("record_count", "total_events", "candidates_found",
                     "candidates_emitted", "match_count"):
                merged[k] = merged.get(k, 0) + (
                    v if isinstance(v, (int, float))
                    and not isinstance(v, bool) else 0)
            elif isinstance(v, list):
                merged.setdefault(k, [])
                merged[k] = merged[k] + [
                    x for x in v if x not in merged.get(k, [])]
            elif k == "streams":
                merged.setdefault("streams", {}).update(v)
            elif k == "complete":
                merged["complete"] = merged.get("complete", True) and bool(v)
            elif isinstance(v, dict):
                d = merged.setdefault(k, {})
                for dk, dv in v.items():
                    if isinstance(dv, (int, float)) and \
                            not isinstance(dv, bool):
                        d[dk] = d.get(dk, 0) + dv
                    elif isinstance(dv, list):
                        d[dk] = d.get(dk, [])
                        d[dk] = d[dk] + [x for x in dv
                                         if x not in d[dk]]
                    else:
                        d[dk] = dv
            elif v == merged.get(k, v):
                merged[k] = v
    return merged


def _declared_scope(summary: dict) -> str | None:
    """The observation's DECLARED analysis scope — the scalar-partition
    key. Missing or non-string values partition as None (conservative:
    an observation that does not declare a scope never claims cross-scope
    complementarity)."""
    v = (summary or {}).get("analysis_scope")
    return v if isinstance(v, str) else None


def _combine_summaries(observations: list[dict]) -> tuple[dict, list[str]]:
    """Deterministically combine the associated observations' summary
    facts. Observation ORDER must never decide semantic truth:

    - list-valued facts: order-preserving UNION (deduplicated) — pure
      membership accumulation, order-independent by construction.
    - scalar-valued facts (numbers/strings/bools/dicts): UNANIMITY within
      each DECLARED analysis_scope partition (_declared_scope):
        * same-partition disagreement → the key is CONFLICTED and OMITTED
          from the combined facts (an evaluator reading it finds it absent
          → indeterminate); the key is reported so the verdict and
          telemetry can state that observations disagreed.
        * every declared scope internally unanimous, but ≥2 scopes carry
          different values → complementary scope-shaped deliveries, NOT
          conflicts: the key is NOT merged into a top-level scalar (no
          unanimity, never latest-wins) and is recorded per scope under
          the reserved combined[SCOPE_FACTS_KEY] = {key: {scope: value}}.
        * an unscoped partition (analysis_scope missing/non-string) that
          disagrees with ANY other partition stays a CONFLICT — only a
          payload that declares its scope may claim cross-scope
          complementarity.
      With at most one partition present this reduces exactly to the
      historical all-observation unanimity semantics (byte-identical).

    Returns (combined_facts, conflicted_keys). No latest-wins anywhere.
    """
    combined: dict[str, Any] = {}
    # scalar values bucketed per declared scope partition; None = unscoped
    scalars: dict[str, dict[str | None, list[Any]]] = {}
    for o in observations:
        summary = o.get("summary") or {}
        scope = _declared_scope(summary)
        for k, v in summary.items():
            if v is None:
                continue
            if isinstance(v, list):
                merged = combined.get(k)
                merged = merged if isinstance(merged, list) else []
                for item in v:
                    if item not in merged:
                        merged.append(item)
                combined[k] = merged
            else:
                bucket = scalars.setdefault(k, {}).setdefault(scope, [])
                if v not in bucket:
                    bucket.append(v)
    conflicted: list[str] = []
    scope_facts: dict[str, dict[str, Any]] = {}
    for k, by_scope in scalars.items():
        unscoped = by_scope.get(None) or []
        declared = {s: vals for s, vals in by_scope.items()
                    if s is not None}
        # within-partition disagreements are ALWAYS conflicts
        if len(unscoped) > 1 or any(len(vals) > 1
                                    for vals in declared.values()):
            conflicted.append(k)
            continue
        # every partition now holds exactly one value
        all_vals = [vals[0] for vals in declared.values()]
        if unscoped:
            all_vals.append(unscoped[0])
        if all(v == all_vals[0] for v in all_vals[1:]):
            combined[k] = all_vals[0]          # unanimous → top level
            continue
        if unscoped:
            # unscoped observation disagrees with a declared scope —
            # conservative: this is NOT established cross-scope
            # complementarity, it stays a conflict
            conflicted.append(k)
            continue
        # ≥2 declared scopes, each internally unanimous, differing values
        scope_facts[k] = {s: declared[s][0] for s in sorted(declared)}
    if scope_facts:
        combined[SCOPE_FACTS_KEY] = {
            k: scope_facts[k] for k in sorted(scope_facts)}
    return combined, sorted(conflicted)


def _accounts_in_candidates(candidates: list[Any]) -> set[str]:
    pairs = set()
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        for key in ("account_a", "account_b"):
            v = c.get(key)
            if v:
                pairs.add(str(v))
    return pairs


def _destination_block_consistent(block: dict) -> bool:
    """WP-GS-D: structural consistency of a projected
    `destination_identity` fact block. Counts, group cardinality and
    group membership must agree before the block can ground ANY
    comparison conclusion; an unverifiable block is never trusted just
    because the field is present."""
    total = block.get("records_total")
    with_id = block.get("records_with_identity")
    without_id = block.get("records_without_identity")
    unique = block.get("unique_destinations")
    groups = block.get("groups")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
               for v in (total, with_id, without_id, unique)):
        return False
    if not isinstance(groups, list) or len(groups) != unique:
        return False
    if with_id + without_id != total:
        return False
    refs: set[str] = set()
    member_ids: list[str] = []
    for g in groups:
        if not isinstance(g, dict):
            return False
        ref = g.get("destination_ref")
        if not ref or ref in refs:
            return False
        refs.add(ref)
        ids = g.get("record_ids")
        if not isinstance(ids, list):
            return False
        member_ids.extend(str(i) for i in ids)
    if len(member_ids) != len(set(member_ids)):
        return False
    if with_id > 0 and not member_ids:
        return False
    if member_ids and len(member_ids) != with_id:
        return False
    return True


def _new_address_facts_verdict(block: dict | None) -> tuple | None:
    """Pure fact verdict for the per-record is_new_address status block.
    Shared by the contract-path facet handler and the turn-path coverage
    verifier — one semantics, two surfaces."""
    if not isinstance(block, dict) \
            or block.get("records_total") in (None, 0):
        return None
    total = block["records_total"]
    true_n = block.get("true_count") or 0
    false_n = block.get("false_count") or 0
    missing_n = block.get("missing_count") or 0
    if missing_n:
        detail = (f"{total} 笔提现中 {missing_n} 笔的新地址标记缺失，"
                  f"仅能确认 {true_n} 笔为新地址、{false_n} 笔不是——"
                  "其余无法核实。")
        return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                R_NEW_ADDRESS_PARTIAL, "partial", detail)
    if false_n == 0:
        detail = (f"全部 {total} 笔提现均被 Risk Platform 标记为新地址"
                  f"（逐笔记录 ID 见明细）。")
    else:
        detail = (f"{total} 笔提现中 {true_n} 笔被标记为新地址、"
                  f"{false_n} 笔不是；逐笔状态见明细。")
    return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
            R_NEW_ADDRESS_STATUS_COMPLETE, "complete", detail)


def _withdrawal_new_address_verdict(
        output: RequiredOutput, combined: dict) -> tuple | None:
    """withdrawal_new_address_status facet: the authoritative per-record
    is_new_address status over the complete exposed withdrawal
    population. Verdicts: SATISFIED when every record's status is
    established (any mix of true/false — the STATUS LIST is the
    deliverable); PARTIALLY_SUPPORTED when some statuses are missing;
    the overall conclusion in the detail is bounded by the evaluated
    population. Destination distinctness is NEVER used to infer
    novelty."""
    from app.goal_resolution import PostconditionFacet
    if (PostconditionFacet.WITHDRAWAL_NEW_ADDRESS_STATUS.value
            not in (output.postcondition_facets or [])):
        return None
    return _new_address_facts_verdict(combined.get("new_address_status"))


def _destination_facts_verdict(
        block: dict | None,
        asserted_population: int | None = None) -> tuple | None:
    """Pure fact verdict for the destination-address-identity comparison
    block. Shared by the contract-path facet handler and the turn-path
    coverage verifier — one semantics, two surfaces."""
    if not isinstance(block, dict) \
            or not _destination_block_consistent(block) \
            or block.get("records_total") == 0:
        return None
    total = block["records_total"]
    # Population-shortfall invariant: when the request asserts a LARGER
    # record population than the authoritative (completely retrieved)
    # one, the comparison of the retrieved subset can never satisfy the
    # asserted full-population comparison — UNRESOLVED, regardless of
    # how well the retrieved subset compares. The retrieved records
    # remain reported as evidence; the gap is explicit.
    if asserted_population is not None and asserted_population > total:
        detail = (
            f"您的请求提及 {asserted_population} 笔提现；当前权威数据源"
            f"检索完整，仅可取得 {total} 笔——其余 "
            f"{asserted_population - total} 笔无法获得，因此无法对全部 "
            f"{asserted_population} 笔完成比较。以下为已获得的 {total} "
            "笔记录的分组结果，仅供核查，不构成对全部 "
            f"{asserted_population} 笔请求的完整回答。")
        return (OutputSatisfaction.UNRESOLVED, None,
                R_POPULATION_SHORTFALL, "partial", detail)
    without_id = block["records_without_identity"]
    unique = block["unique_destinations"]
    if without_id > 0:
        detail = (f"{without_id}/{total} 笔记录缺少目的地标识，仅能对可比"
                  "子集给出分组结论；整体的比较尚未建立。")
        return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                R_DEST_IDENTITY_PARTIAL, "partial", detail)
    if unique == 1:
        ref = (block["groups"][0] or {}).get("destination_ref") or "dest-1"
        detail = (f"全部 {total} 笔提现指向同一目的地"
                  f"（记录中的目的地标识相同：{ref}）。")
        return (OutputSatisfaction.SATISFIED, POLARITY_NEGATIVE,
                R_DEST_COMPARISON_COMPLETE, "complete", detail)
    detail = (f"{total} 笔提现共指向 {unique} 个不同目的地"
              "（记录中的目的地标识）。")
    return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
            R_DEST_COMPARISON_COMPLETE, "complete", detail)


def _destination_facet_verdict(
        output: RequiredOutput,
        combined: dict,
        asserted_population: int | None = None) -> tuple | None:
    """WP-GS-D (design §7.2): the destination-address-identity comparison
    verdict for one faceted output, derived ONLY from the projected GS-B
    fact block carried in the combined observation facts.

    Verdict rows (in the design's order):
    - all identities present, one unique destination → SATISFIED
      (negative polarity — the same-or-different question is answered
      with "same over recorded identifiers"; polarity expresses the
      FACTS' shape, never that a risk hypothesis is true)
    - all identities present, ≥2 unique → SATISFIED (positive — the
      records partition into K evidenced groups)
    - some identities missing → PARTIALLY_SUPPORTED (comparable-subset
      facts only; the whole-set comparison is never claimed)
    - block absent / unverifiable / no withdrawal records at all →
      None: the caller falls through to the existing records
      postcondition, with the retrieval-suppression rule (a faceted
      output is NEVER satisfied by retrieval alone — row 5).

    The comparison key stays GS-B's: the recorded identifier, trimmed,
    EXACT, case-sensitive — no folding, no is_new_address inference,
    and the conclusion is worded over RECORDED identifiers only
    (never a claim about on-chain destinations).
    """
    from app.goal_resolution import PostconditionFacet
    if PostconditionFacet.DESTINATION_ADDRESS_IDENTITY.value not in (
            output.postcondition_facets or []):
        return None
    return _destination_facts_verdict(
        combined.get("destination_identity"), asserted_population)


def _metric_facts_verdict(metrics: dict | None) -> tuple | None:
    """Pure fact verdict for the target_account_metrics echo. Shared by
    the contract-path facet handler and the turn-path coverage
    verifier — one semantics, two surfaces."""
    if not isinstance(metrics, dict):
        return None
    ratio = metrics.get("opposite_trade_ratio")
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        return None
    detail = (
        f"{ratio:.2%}（opposite_trade_ratio）是账户自身的反向交易比例："
        "按该账户自己的全部交易计算，分子为较少一侧的交易笔数"
        "（min(BUY, SELL)），分母为该账户交易总笔数；它衡量的是同一"
        "账户内的买卖平衡，不是跨账户匹配率。来源：Risk Platform "
        "feature_evidence。")
    return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
            R_METRIC_SEMANTICS_COMPLETE, "complete", detail)


def _reverse_trade_metric_verdict(output: RequiredOutput,
                                  combined: dict) -> tuple | None:
    """reverse_trade_metric_semantics facet: the account's authoritative
    opposite_trade_ratio echo carried by the cross-account analysis
    payload (target_account_metrics, source risk_platform.feature_evidence).
    The deliverable is the metric's MEANING — the account's OWN buy/sell
    balance, explicitly distinct from a cross-account match rate
    (baseline §8). Absent/unverifiable metric → None: the caller's
    faceted-output suppression keeps retrieval from masquerading as the
    metric answer."""
    from app.goal_resolution import PostconditionFacet
    if (PostconditionFacet.REVERSE_TRADE_METRIC_SEMANTICS.value
            not in (output.postcondition_facets or [])):
        return None
    return _metric_facts_verdict(combined.get("target_account_metrics"))


def _category_postcondition(req: EvidenceType, output: RequiredOutput,
                            summary: dict, case_id: str | None) -> tuple | None:
    """One deterministic postcondition per admissible evidence category.
    Returns (satisfaction, polarity, reason, completeness) or None when
    the category yields no determinate verdict from these facts."""
    subject = output.subject_ref

    if req == EvidenceType.OPPOSITE_TRADE_RELATIONS:
        candidates = summary.get("candidates") or []
        retrieval = summary.get("retrieval") or {}
        truncated = bool(retrieval.get("truncated")) \
            if isinstance(retrieval, dict) else bool(
                summary.get("truncated"))
        # SET-INTERNAL scope: candidates may involve the case account or
        # only two related members. The scope criterion is SET MEMBERSHIP
        # (both legs ∈ accounts_analyzed), never case-account
        # involvement; the verdict labels the involvement split so the
        # answer never claims the case account participated in every
        # relation.
        # System candidate cap: when MORE candidates were found than the
        # report carries, the listing deliverable is PARTIAL — a user who
        # asked for ALL candidates has undeliverable remainder (nothing
        # else in the run carries the excess pairs).
        found = summary.get("candidates_found")
        if isinstance(found, int) and found > len(candidates):
            # 4-tuple like every category verdict (the aggregation unpacks
            # exactly four); the user-visible specifics are carried by the
            # renderer's own found>emitted note
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_CANDIDATE_CAP_PARTIAL, "partial")
        if summary.get("analysis_scope") == "related_account_set":
            scope_accounts = {str(a) for a in
                              (summary.get("accounts_analyzed") or [])}
            outside = [c for c in candidates if isinstance(c, dict)
                       and (str(c.get("account_a")) not in scope_accounts
                            or str(c.get("account_b")) not in
                            scope_accounts)]
            if outside:
                return (OutputSatisfaction.UNRESOLVED, None,
                        R_SET_MEMBERSHIP_VIOLATION, "partial",
                        (f"{len(outside)} 组候选关系的账户不在本次关联"
                         "账户集合内——集合外配对不能作为集合内部分析的"
                         "结论。"))
            if candidates:
                case_acc = summary.get("target_account")
                with_case = [c for c in candidates if isinstance(c, dict)
                             and case_acc in (str(c.get("account_a")),
                                              str(c.get("account_b")))]
                only_related = len(candidates) - len(with_case)
                detail = (f"发现 {len(candidates)} 组集合内部候选关系"
                          f"（{len(with_case)} 组涉及案件账户 "
                          f"{case_acc}，{only_related} 组仅发生在其他"
                          "关联成员之间）；每组均附双方交易证据。")
                if truncated:
                    detail += "（集合检索受预算限制，覆盖不完整）"
                    return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                            R_SET_SCOPE_TRUNCATED, "truncated", detail)
                return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                        R_SET_CANDIDATES_FOUND, "complete", detail)
            if truncated:
                return (OutputSatisfaction.UNRESOLVED, None,
                        R_SET_SCOPE_TRUNCATED, "truncated",
                        "集合交易证据检索不完整，无法就不存在集合内部"
                        "候选下结论（这不是“未发现”）。")
            return (OutputSatisfaction.SATISFIED, POLARITY_NEGATIVE,
                    R_SET_NO_CANDIDATES_COMPLETE, "complete",
                    "在已完整检索的关联账户集合内部范围内，未发现符合"
                    "匹配条件的对冲候选关系；该结论仅适用于本次集合与"
                    "数据窗口。")
        # Subject-membership over the candidate relation set, gated by
        # the retrieval truncation flag (real contract field).
        involved = _accounts_in_candidates(candidates)
        if subject and subject in involved:
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_SUBJECT_PRESENT, None)
        if not truncated and (candidates or summary.get(
                "accounts_analyzed")):
            return (OutputSatisfaction.SATISFIED, POLARITY_NEGATIVE,
                    R_SUBJECT_ABSENT_COMPLETE, "complete")
        if truncated:
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_SCOPE_TRUNCATED, "truncated")
        return None

    if req == EvidenceType.SHARED_IP_EVIDENCE:
        # Deterministic negative ONLY with the completeness gate: the
        # per-account echo present AND the account count matching (RP
        # attribution is complete for the recorded relation set).
        echo = summary.get("shared_ip_attributions")
        if echo is None:
            return (OutputSatisfaction.UNRESOLVED, None,
                    R_FIELD_ABSENT, "absent_field")
        accounts = summary.get("related_accounts") or []
        count = summary.get("related_accounts_count")
        count_ok = count is None or count == len(accounts)
        any_ip = any(v for v in (echo or {}).values())
        if not count_ok:
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_SCOPE_TRUNCATED, "partial")
        if any_ip:
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_SUBJECT_PRESENT, "complete")
        return (OutputSatisfaction.SATISFIED, POLARITY_NEGATIVE,
                R_RECORDS_EMPTY_COMPLETE, "complete")

    if req in (EvidenceType.NETWORK_RELATIONS,
               EvidenceType.SHARED_DEVICE_EVIDENCE):
        if summary.get("evidence_missing"):
            return (OutputSatisfaction.UNRESOLVED, None,
                    R_FIELD_ABSENT, "absent_field")
        # Delivery semantics (real execution TASK-268228ad0559): listing
        # related objects is NOT explaining why the relationship is a
        # risk item. When the output also demands explanation-type
        # evidence, a bare relation list (no rule/ML/cluster/feature
        # facts) is context only — the explanation deliverable stays
        # unresolved so the loop keeps investigating or the answer
        # states the gap honestly. No tool is mandated; any producer of
        # the existing explanation fact shapes satisfies.
        if requires_explanation_evidence(output.evidence_requirements) \
                and not _has_explanation_facts(summary):
            return None
        if summary.get("related_accounts") or summary.get("shared_devices"):
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_RELATIONS_AVAILABLE, "complete")
        return None

    if req in (EvidenceType.WITHDRAWAL_RECORDS,
               EvidenceType.TRANSACTION_RECORDS):
        if outcome := summary.get("_outcome"):
            if outcome == "empty":
                # P20: a valid execution with genuinely no records is an
                # authoritative determinate negative for this scope.
                return (OutputSatisfaction.SATISFIED, POLARITY_NEGATIVE,
                        R_RECORDS_EMPTY_COMPLETE, "complete")
        if summary.get("evidence_missing"):
            return (OutputSatisfaction.UNRESOLVED, None,
                    R_FIELD_ABSENT, "absent_field")
        # L1 (records-retrieval delivery parity): evidence completeness
        # (records fully retrieved) is NOT delivery completeness. A
        # records-LISTING deliverable (retrieval kind, no facet) is only
        # satisfiable when the dialogue channel emitted the whole set
        # (found <= chat cap); otherwise the honest state is a disclosed
        # partial. Other kinds keep the legacy semantics — records are
        # supporting evidence there, not the deliverable.
        record_count = summary.get("record_count")
        rows = summary.get("record_rows") or []
        emitted = min(len(rows), RECORDS_CHAT_CAP)
        count_valid = (isinstance(record_count, int)
                       and not isinstance(record_count, bool)
                       and record_count >= 0)
        # U00047 delivery-closure (D1): the delivery mode decides what
        # "delivered" means. file_export = the generated FILE is the
        # deliverable — the dialogue cap must never downgrade it;
        # detail_list (incl. the legacy default) keeps the L1 chat-parity
        # rules byte-for-byte.
        if getattr(output, "delivery", "detail_list") == "file_export":
            if summary.get("evidence_missing"):
                return (OutputSatisfaction.UNRESOLVED, None,
                        R_FIELD_ABSENT, "absent_field")
            # the artifact observation is associated to this output by
            # its source streams (association fix), so the combined
            # summary carries its artifact_id when a file was generated
            generated = summary.get("artifact_id")
            if not generated:
                return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                        R_RECORDS_INCOMPLETE, "partial")
            # file completeness evidence = the tool-layer record-parity
            # check (rendered rows == declared record_count per source),
            # consumed here — never re-derived from the chat cap.
            parity = summary.get("record_parity_failed")
            checked = summary.get("record_parity_checked")
            if parity is True:
                return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                        R_RECORDS_INCOMPLETE, "partial")
            if not checked or parity is None:
                # parity check did not run → file completeness unverified
                # (never treated as verified)
                return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                        R_RECORDS_INCOMPLETE, "partial")
            # Plan v2.1 scope guard (real failure INV-9808752cbc32: an
            # anaphoric hedge-detail file ask was satisfied by an
            # unrelated full-stream bundle). A file_export delivery whose
            # claim references prior detection results must carry the
            # candidate-set detail evidence; an unrelated full-stream
            # artifact never satisfies it (conservative: blocks only).
            if _claim_references_prior_detection(output.claim) \
                    and not summary.get("candidate_set_export"):
                return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                        "candidate_set_export_scope_unverified", "partial",
                        "导出范围指向此前检测到的对冲候选，但交付文件未包含"
                        "候选明细证据（候选明细须基于绑定的候选集生成）。")
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_RECORDS_FILE_DELIVERED_COMPLETE, "complete")
        if (count_valid and record_count > emitted
                and getattr(output, "claim_kind", None) == "retrieval"
                and not output.postcondition_facets):
            return (OutputSatisfaction.PARTIALLY_SUPPORTED,
                    POLARITY_POSITIVE, R_RECORDS_CAP_PARTIAL, "partial")
        if count_valid and rows and record_count < len(rows):
            # L1 T7: declared count smaller than the projected rows —
            # evidence/declaration inconsistency → conservative partial
            # + warning, never a silent SATISFIED.
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_RECORDS_INCOMPLETE, "partial")
        if not count_valid and rows:
            # count unreliable with rows presented — conservative partial,
            # never a silent SATISFIED (L1 count-unreliable rule)
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_RECORDS_INCOMPLETE, "partial")
        if record_count is not None:
            if summary.get("complete") is False:
                return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                        R_RECORDS_INCOMPLETE, "partial")
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_RECORDS_RETRIEVED_COMPLETE, "complete")
        return None

    if req in (EvidenceType.ML_SIGNAL_EXPLANATION,
               EvidenceType.RULE_EXPLANATION,
               EvidenceType.GRAPH_CLUSTER_FACTS):
        # Subject binding (target-vs-evidence): a signal explanation names
        # the finding it explains (data.finding_id, projected into the
        # summary). For a finding-level output subject, an explanation of
        # a DIFFERENT finding is associated (step_output_map contract
        # mapping) but NOT semantically sufficient — association is not
        # satisfaction. Requiring the binding is reliable: every
        # signal_explain payload carries finding_id (verified across
        # ML/Rule/Graph/Feature normalizers).
        subject = (output.subject_ref or "").strip()
        explained = str(summary.get("finding_id") or "").strip()
        if subject.startswith("F") and subject[1:].isdigit() and explained != subject:
            return None
        if summary.get("evidence_missing"):
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_EXPLANATION_PARTIAL, "partial")
        if summary.get("ml_score") is not None \
                or summary.get("rule_name") \
                or summary.get("cluster_name"):
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_EXPLANATION_AVAILABLE, "complete")
        # AL-2.14/F7-class: a Feature signal's explanation carries the
        # factor's actual feature values — those facts ground the
        # why-flagged/criteria questions even without a rule_name.
        if summary.get("feature_facts"):
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_EXPLANATION_AVAILABLE, "complete")
        return None

    if req == EvidenceType.POLICY_REFERENCES:
        if summary.get("matches") is not None:
            # U00029 applicability closure (I4/I5): a FINDING-anchored
            # policy output must carry the citations the Risk Platform
            # attached to THAT finding. Case-level matches alone do not
            # make a policy applicable to it — without an associated
            # (RP-attached) basis the honest state is a disclosed partial,
            # never a finding-level "satisfied" that reads as "the policy
            # applies". Population-subject outputs (account subjects)
            # keep the retrieval rule — the case-level citation set IS
            # their deliverable.
            subj = (getattr(output, "subject_ref", "") or "").strip()
            if re.match(r"^F\d+$", subj):
                assoc = summary.get("associated_citations") or []
                if (summary.get("finding_policy_status")
                        == "no_finding_level_basis") or not assoc:
                    return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                            R_FIELD_PRESENT, "partial")
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_FIELD_PRESENT, "complete")
        return None

    if req == EvidenceType.CASE_SUMMARY:
        if summary.get("finding_count") is not None:
            # Satisfaction semantics (real execution
            # INV-727c5a264c85/TASK-740e83a1b5f5): the case bundle is the
            # DELIVERABLE only for outputs that ask for case context.
            # When the same output also demands explanation-type evidence
            # (rule/signal/cluster facts), the bundle is supporting
            # context — its presence is NOT the requested explanation of
            # a specific risk finding, and must never satisfy the output
            # by itself (the deeper requirement decides, or the existing
            # indeterminate fallback keeps the output honest so the loop
            # can continue investigating). No tool is mandated here.
            if any((r.value if hasattr(r, "value") else str(r))
                   in _EXPLANATION_EVIDENCE_REQ_VALUES
                   for r in output.evidence_requirements):
                return None
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_FIELD_PRESENT, "complete")
        return None

    if req == EvidenceType.TIMELINE_EVENTS:
        # R6 (U00010 F7): a finding-scoped timeline delivery satisfies a
        # timeline ask from the ACTUAL focused-finding events (subject
        # binding enforced); truncation stays bounded-partial; zero events
        # stay a bounded authoritative zero for the scope.
        #
        # Completeness consistency (emission/total parity): the verdict
        # is judged from the FULL projected event set (`event_summaries`,
        # uncapped) against the tool's authoritative `total_events` —
        # never from the _MAX_IDS-bounded `event_ids` metadata. A
        # shortfall between the projected set and the declared total is
        # a non-complete delivery (never a silent SATISFIED).
        subject = (output.subject_ref or "").strip()
        scoped = str(summary.get("finding_id") or "").strip()
        events = summary.get("event_summaries") or []
        if subject.startswith("F") and subject[1:].isdigit() \
                and scoped and scoped != subject:
            return None
        if summary.get("truncated"):
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_FIELD_PRESENT, "partial")
        total_events = summary.get("total_events")
        if events and isinstance(total_events, int) \
                and not isinstance(total_events, bool) \
                and len(events) < total_events:
            return (OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                    R_FIELD_PRESENT, "partial")
        if events:
            return (OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                    R_FIELD_PRESENT, "complete")
        return None

    return None


# Determinate precedence for ANY aggregation (GSP-4 correction). Fixed,
# explicit, independent of requirement list order / observation order /
# timestamps. Rationale: the strongest GROUNDED verdict wins — a
# determinate answer from one alternative evidence path is not downgraded
# by another path's weaker or indeterminate verdict, and an indeterminate
# verdict never masks a determinate one. NOT_SATISFIED and UNSUPPORTED
# currently have NO producer among the category postconditions (reserved
# states); they rank below UNRESOLVED so reserved states can never
# outrank real evidence if ever introduced.
_VERDICT_PRECEDENCE = {
    OutputSatisfaction.SATISFIED: 0,
    OutputSatisfaction.PARTIALLY_SUPPORTED: 1,
    OutputSatisfaction.UNRESOLVED: 2,
    OutputSatisfaction.NOT_SATISFIED: 3,
    OutputSatisfaction.UNSUPPORTED: 4,
}


def _aggregate_requirement_results(
    verdicts: list[tuple],
) -> tuple:
    """Deterministic ANY aggregation over collected requirement verdicts.

    `verdicts`: (satisfaction, polarity, reason_code, completeness[, detail])
    tuples produced by `_category_postcondition` — one per requirement
    that yielded a determinate verdict from the combined facts. 4-tuples
    are normalized to 5 (detail=None) so branches that carry a user-safe
    detail line and branches that do not can never break each other's
    aggregation.

    Aggregation = strongest verdict by fixed precedence. Ties within the
    winning class resolve canonically by (reason_code, polarity,
    completeness) — deterministic and permutation-invariant; never by
    list position, observation order, or time.
    """
    if not verdicts:
        return None
    normalized = [v if len(v) >= 5 else (*v, None) for v in verdicts]
    ranked = sorted(
        normalized,
        key=lambda v: (_VERDICT_PRECEDENCE.get(v[0], 99),
                       v[2] or "", v[1] or "", v[3] or ""))
    winner = ranked[0]
    # 4-tuple unless a detail is actually carried (existing unpack sites
    # and their pinning tests stay valid)
    return winner[:5] if winner[4] is not None else winner[:4]


def evaluate_output(output: RequiredOutput, case_id: str | None,
                    observations: list[dict],
                    asserted_population: int | None = None
                    ) -> PostconditionResult:
    """Evaluate ONE output against its associated observations.

    `observations` is the run's FULL observation list; the entries
    associated to this output (for_outputs contains output_id) are its
    candidate evidence, fact-combined (union) before evaluation.
    """
    indexes = _fact_observation_indexes(output.output_id, observations)
    evidence = sorted({t for o in
                       [observations[i] for i in indexes]
                       for t in _as_values(o.get("actual_evidence_types"))})

    def _result(satisfaction, polarity=None, reason=None,
                completeness=None, detail=None):
        return PostconditionResult(
            output_id=output.output_id, satisfaction=satisfaction,
            polarity=polarity, reason_code=reason,
            observation_indexes=indexes, evidence_types=evidence,
            completeness=completeness, detail=detail)

    if not indexes:
        return _result(OutputSatisfaction.PENDING,
                       reason=R_NO_ASSOCIATED_EVIDENCE)

    associated = [observations[i] for i in indexes]
    # Only valid executions produce evaluable facts. Outages / validation
    # failures / execution errors are NOT verdicts — the output stays
    # PENDING (the loop may retry / re-plan / surface the outage).
    evaluable = [o for o in associated
                 if o.get("outcome") in ("success", "empty")]
    if not evaluable:
        return _result(OutputSatisfaction.PENDING,
                       reason="no_evaluable_observation")

    # U00004 (PMO Option 1) file-delivery completion gate: a file_export
    # output is DELIVERED only when a generated artifact backs it — the
    # evidence intersection (analysis + records observations) never
    # satisfies the file channel by itself. No artifact_id in the
    # associated facts (generation failed / never ran) → honest
    # PARTIALLY_SUPPORTED, never SATISFIED (real failure TASK-ae1a634a4f1b
    # follow-up: the completion gate must not report a file the user
    # asked for as delivered when no file exists).
    if getattr(output, "delivery", "detail_list") == "file_export":
        backed = any(
            isinstance(o.get("summary") or {}, dict)
            and (o.get("summary") or {}).get("artifact_id")
            for o in evaluable)
        if not backed:
            return _result(
                OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                "file_export_delivery_unverified", "partial",
                "文件交付未完成：本次运行没有成功生成的文件实体，"
                "证据检索结果不等于文件交付。")

    # Plan v2.1: a candidate-scoped export output (anaphoric "以上检测到
    # 的对冲交易" + bound candidate set) is satisfiable ONLY by the
    # verified candidate-detail delivery — never by existence evidence,
    # retrieval records, or a full-stream bundle. Zero candidates, an
    # analysis failure, or field-incomplete records each produce a
    # distinct honest not-satisfied outcome (see the scoped synthesis).
    if getattr(output, "candidate_scope", None) == "prior_candidates" \
            and "opposite_trade_relations" in (
                r.value if hasattr(r, "value") else str(r)
                for r in output.evidence_requirements):
        # Plan v2.1: a candidate-scoped export output is satisfiable ONLY
        # by the verified candidate-detail delivery (synthesized from the
        # bound session candidate set). Existence evidence, retrieval
        # records, or a full-stream bundle never satisfy it.
        cs = None
        for ob in evaluable:
            s_ = ob.get("summary") or {}
            if isinstance(s_, dict) and s_.get(
                    "candidate_set_export") is not None:
                cs = s_["candidate_set_export"]
                break
        if isinstance(cs, dict) and (cs.get("verification") or {}).get(
                "passed"):
            bounded = bool(cs.get("bounded_data"))
            detail = (
                f"已导出绑定的对冲候选明细（候选集 {cs.get('set_ref')}；"
                f"{cs.get('verification', {}).get('candidates')} 组候选、"
                f"{len((cs.get('verification') or {}).get('transaction_ids') or [])} 笔交易）。"
                + ("检索受限：以上为已识别候选，不代表已穷尽全部可能的对冲交易。"
                   if bounded else ""))
            return _result(OutputSatisfaction.SATISFIED, POLARITY_POSITIVE,
                           "candidate_set_export_verified", "complete",
                           detail)
        rej = cs.get("candidate_set_export_rejected") if isinstance(cs, dict) else None
        if isinstance(rej, dict) and rej.get("reason") == "no_candidates":
            detail = ("重新分析未发现对冲候选；不存在可导出的交易明细文件，"
                      "亦不得以其他交易记录代替。")
            return _result(OutputSatisfaction.UNRESOLVED, None,
                           "candidate_set_export_no_candidates", "partial",
                           detail)
        detail = ("候选明细文件未交付：候选范围导出必须基于绑定的候选集生成，"
                  "且每笔交易需含账号、时间、方向、金额。")
        if isinstance(rej, dict) and rej.get("missing"):
            detail += ("确定性校验拒绝（字段不完整）: "
                       + "; ".join(rej["missing"][:10]) + "。")
        return _result(OutputSatisfaction.PARTIALLY_SUPPORTED, None,
                       "candidate_set_export_not_delivered", "partial",
                       detail)
    # Domain-judgment claims are not adjudicable by this runtime —
    # regardless of which (successful) evidence categories were gathered.
    if output.claim_kind == ClaimKind.ASSESSMENT:
        return _result(
            OutputSatisfaction.UNRESOLVED,
            reason=R_JUDGMENT_NOT_ADJUDICABLE,
            completeness="partial",
            detail=("The gathered evidence is recorded for analyst "
                    "review; this system does not adjudicate this "
                    "judgment."))

    # R4 (U00010 F1): complementary single-stream deliveries of ONE
    # multi-stream retrieval output are merged first — scalar disagreements
    # across DIFFERENT streams (7 withdrawals vs 64 transactions) are
    # complementary populations, not conflicts. Same-stream observations
    # keep the unanimity/conflict rules unchanged.
    disjoint_merge = _merge_disjoint_stream_summaries(evaluable)
    if disjoint_merge is not None:
        combined = dict(disjoint_merge)
        conflicts = []
    else:
        combined, conflicts = _combine_summaries(evaluable)
    # Outcome is DERIVED from the union, never latest-wins: any success
    # means facts exist in the combined record; only a unanimous empty
    # run is a determinate empty.
    outcomes = {o.get("outcome") for o in evaluable}
    combined["_outcome"] = "empty" if outcomes <= {"empty"} else "success"

    # WP-GS-D (design §7.2): a declared destination-address-identity
    # facet is decided by the comparison FACTS, never by retrieval.
    # When the block yields a verdict it IS the output's verdict; when
    # it does not (absent / unverifiable / no withdrawal records), the
    # existing records postcondition runs BUT a SATISFIED-positive
    # retrieval verdict is suppressed below — a faceted output is never
    # satisfied by retrieval alone (row 5); the empty-complete negative
    # and partial/unresolved survive (row 4, inherited).
    from app.goal_resolution import PostconditionFacet
    facets = (output.postcondition_facets or [])
    has_destination = (
        PostconditionFacet.DESTINATION_ADDRESS_IDENTITY.value in facets)
    has_new_address = (
        PostconditionFacet.WITHDRAWAL_NEW_ADDRESS_STATUS.value in facets)
    has_metric = (
        PostconditionFacet.REVERSE_TRADE_METRIC_SEMANTICS.value in facets)
    facet_declared = has_destination or has_new_address or has_metric

    def _facet_facts_absent_detail() -> str:
        if has_destination:
            return ("未获得完成目的地比较所需的地址事实；检索结果仅作为"
                    "证据记录。")
        if has_new_address:
            return ("未获得新地址状态所需的逐笔标记事实；检索结果仅作为"
                    "证据记录。")
        return ("未获得指标语义所需的权威特征值；检索结果仅作为证据"
                "记录。")

    if has_destination:
        facet = _destination_facet_verdict(output, combined,
                                           asserted_population)
        if facet is not None:
            s, pol, reason, completeness, detail = facet
            result = _result(s, pol, reason, completeness, detail)
            result.conflicts = sorted(conflicts)
            if conflicts and result.satisfaction == \
                    OutputSatisfaction.UNRESOLVED:
                result.reason_code = R_CONFLICTING_OBSERVATIONS
            return result
    if has_new_address:
        facet = _withdrawal_new_address_verdict(output, combined)
        if facet is not None:
            s, pol, reason, completeness, detail = facet
            result = _result(s, pol, reason, completeness, detail)
            result.conflicts = sorted(conflicts)
            if conflicts and result.satisfaction == \
                    OutputSatisfaction.UNRESOLVED:
                result.reason_code = R_CONFLICTING_OBSERVATIONS
            return result
    if has_metric:
        facet = _reverse_trade_metric_verdict(output, combined)
        if facet is not None:
            s, pol, reason, completeness, detail = facet
            result = _result(s, pol, reason, completeness, detail)
            # Cross-scope scalar isolation (SCOPE_FACTS_KEY) already kept
            # complementary cross-scope differences out of `conflicts` —
            # no key whitelist here: every reported conflict is a real
            # same-scope (or unscoped-involved) disagreement and
            # downgrades via apply_postcondition.
            result.conflicts = sorted(conflicts)
            if result.conflicts and result.satisfaction == \
                    OutputSatisfaction.UNRESOLVED:
                result.reason_code = R_CONFLICTING_OBSERVATIONS
            return result

    # GSP-4 correction: ANY aggregation. ALL applicable requirement
    # verdicts are collected first, then aggregated by the fixed
    # determinate precedence below — the requirement LIST ORDER and the
    # OBSERVATION ORDER can never influence the verdict (unordered
    # ANY-set contract, design §4.1a). Requirements whose category
    # yields no determinate verdict from these facts contribute nothing
    # ("no evidence" ≠ "proved failed") and never mask stronger
    # verdicts.
    #
    # The reverse-trade-METRIC facet gates exclusively: its deliverable
    # (the authoritative ratio echo) is not derivable from record
    # retrieval — neither a matched-pair positive NOR a checked-absent
    # negative says anything about what the metric measures. No metric
    # echo → UNRESOLVED, whatever the retrieval produced.
    if has_metric:
        result = _result(
            OutputSatisfaction.UNRESOLVED, None,
            R_COMPARISON_FACTS_ABSENT, "partial",
            _facet_facts_absent_detail())
    else:
        verdicts = []
        for req in output.evidence_requirements:
            verdict = _category_postcondition(req, output, combined,
                                              case_id)
            if verdict is not None:
                verdicts.append(verdict)
        if verdicts:
            _agg = _aggregate_requirement_results(verdicts)
            s, pol, reason, completeness = _agg[:4]
            agg_detail = _agg[4] if len(_agg) > 4 else None
            if (facet_declared and s == OutputSatisfaction.SATISFIED
                    and pol == POLARITY_POSITIVE):
                # Row 5: retrieval succeeded but the comparison facts the
                # facet demands are absent — the records NEVER satisfy the
                # faceted output; they remain recorded evidence only.
                result = _result(
                    OutputSatisfaction.UNRESOLVED, None,
                    R_COMPARISON_FACTS_ABSENT, "partial",
                    _facet_facts_absent_detail())
            else:
                result = _result(s, pol, reason, completeness, agg_detail)
        else:
            if facet_declared:
                result = _result(
                    OutputSatisfaction.UNRESOLVED, None,
                    R_COMPARISON_FACTS_ABSENT, "partial",
                    _facet_facts_absent_detail())
            else:
                result = _result(
                    OutputSatisfaction.UNRESOLVED,
                    reason=R_UNDETERMINED,
                    completeness="partial",
                    detail=("Evidence was gathered and recorded; no deterministic "
                            "postcondition can adjudicate this output."))
    # Candidate-cap gate (payload-scoped, mirrors the conflict downgrade):
    # when the associated cross-account analysis found MORE candidates
    # than its report carries, any SATISFIED verdict — including one from
    # another requirement category (e.g. network relations masking a
    # capped trade listing) — is downgraded to PARTIALLY_SUPPORTED: a
    # user who asked for ALL candidates has undelivered remainder, no
    # matter which single requirement happened to be fully satisfied.
    capped = any(
        isinstance((o.get("summary") or {}).get("candidates_found"), int)
        and (o["summary"]["candidates_found"]
             > len((o["summary"].get("candidates") or [])))
        for o in evaluable)
    if capped and result.satisfaction == OutputSatisfaction.SATISFIED:
        _cap_found = next(
            (o["summary"]["candidates_found"] for o in evaluable
             if isinstance((o.get("summary") or {}).get("candidates_found"),
                           int)
             and (o["summary"]["candidates_found"]
                  > len((o["summary"].get("candidates") or [])))), None)
        result.satisfaction = OutputSatisfaction.PARTIALLY_SUPPORTED
        result.polarity = None
        result.reason_code = R_CANDIDATE_CAP_PARTIAL
        result.completeness = "partial"
        result.detail = (
            f"共发现 {_cap_found} 组候选；受系统候选上限，本次仅交付前 "
            f"{len((evaluable[-1].get('summary') or {}).get('candidates') or [])}"
            " 组，其余候选未在本次回答中交付（当前没有其他可访问的完整"
            "清单）。")
    # Conflicted scalar facts are surfaced on every verdict (they were
    # excluded from the combined facts); when the conflict itself left
    # the output indeterminate, the reason states it explicitly.
    result.conflicts = sorted(conflicts)
    if conflicts and result.satisfaction == OutputSatisfaction.UNRESOLVED:
        result.reason_code = R_CONFLICTING_OBSERVATIONS
    return result


def apply_postcondition(
    result: PostconditionResult,
) -> tuple[OutputSatisfaction, str | None, str | None]:
    """The SINGLE satisfaction write-point rule (pure; the loop calls this
    immediately before writing to the contract output).

    Conflict downgrade (normative, GSP-4 correction): when scalar fact
    conflicts were reported, a SATISFIED verdict is downgraded to
    UNRESOLVED with polarity=None and reason
    `conflicting_observations` — observations disagreed on at least one
    reported fact, so no value may be presented as authoritative
    (never SATISFIED(CRITICAL), never SATISFIED(LOW)).
    """
    if result.conflicts and result.satisfaction == OutputSatisfaction.SATISFIED:
        return (OutputSatisfaction.UNRESOLVED, None,
                R_CONFLICTING_OBSERVATIONS)
    return (result.satisfaction, result.polarity, result.reason_code)


def evaluate_contract_outputs(
    contract: GoalContract, observations: list[dict],
    case_id: str | None = None,
) -> list[PostconditionResult]:
    """Evaluate every output of the contract (order-stable). The caller
    applies the results — this module never mutates the contract."""
    return [evaluate_output(out, case_id, observations,
                            asserted_population=(
                                contract.asserted_record_population))
            for out in contract.outputs]


# ---------------------------------------------------------------------------
# GSP WP-GSP-5 — Overall Goal Outcome (deterministic derivation; the LLM
# never sets it; no single output can force COMPLETE).
# ---------------------------------------------------------------------------

class GoalOutcome(str, Enum):
    COMPLETE = "complete"          # every output SATISFIED
    PARTIAL = "partial"            # ≥1 answered, others not fully answered
    UNANSWERABLE = "unanswerable"  # nothing answered; remaining outputs
                                   # unsupported/unresolved/pending
    BLOCKED = "blocked"            # execution/system state stopped the run


def derive_goal_outcome(satisfactions, agent_status: str | None = None,
                        intent_source: str | None = None) -> GoalOutcome:
    """Deterministic overall outcome from per-output satisfaction states.

    - execution stop (agent_status == "blocked") → BLOCKED (system state,
      distinct from domain insufficiency)
    - all SATISFIED → COMPLETE
    - any SATISFIED (mixed with PARTIAL/UNRESOLVED/UNSUPPORTED/PENDING)
      → PARTIAL
    - nothing answered (all UNSUPPORTED/UNRESOLVED/PENDING) → UNANSWERABLE
    UNSUPPORTED is never upgraded to COMPLETE; PENDING at terminal is
    treated as not-answered (never as satisfied).

    Completion-state protection (P0/B closure): `intent_source`
    "heuristic" marks a contract whose accepted output set came from the
    deterministic fallback — the goal-understanding model never validated
    the set against the user's original request. Such a contract can
    prove its OUTPUTS were delivered, never that the REQUEST is fully
    covered, so COMPLETE is capped at PARTIAL (the honest "delivered
    work exists; completeness not established" state). "llm" and legacy
    None are unaffected.
    """
    from app.goal_resolution import OutputSatisfaction as OS
    states = [s for s in satisfactions
              if hasattr(s, "value") or isinstance(s, str)]
    vals = {getattr(s, "value", s) for s in states}
    if agent_status == "blocked":
        return GoalOutcome.BLOCKED
    if not vals:
        return GoalOutcome.UNANSWERABLE
    satisfied = OS.SATISFIED.value in vals
    if satisfied and vals <= {OS.SATISFIED.value}:
        if intent_source == "heuristic":
            return GoalOutcome.PARTIAL
        return GoalOutcome.COMPLETE
    if satisfied:
        return GoalOutcome.PARTIAL
    if OS.PARTIALLY_SUPPORTED.value in vals:
        return GoalOutcome.PARTIAL
    return GoalOutcome.UNANSWERABLE

"""artifact_bundle — Week 1 artifact composition tool.

COMPOSITION, NOT COMPUTATION: bundles already-produced structured
investigation results (prior ToolResults in the current task) into a
deterministic Markdown artifact. No new risk analysis, no LLM, no RP calls
when existing structured results suffice, no invented facts/citations/
timestamps. Sections without available data are omitted or rendered as
explicit evidence gaps — never padded.

Canonical Finding ownership (composition contract): exactly ONE canonical
finding block per artifact, owned by the LATEST executed risk_case_fetch
success in the provenance pool. Supporting tools (finding_drilldown,
signal_explain, policy_lookup) contribute only their own sections — they
never append a second representation of the canonical finding. Multiple
fetches of the same case (an earlier turn's intake + this turn's fetch)
render each finding once, from the latest fetch.

Provenance closure (mandatory): source_tool_calls lists exactly the calls
whose results materially contributed rendered content — same containment
rules as the content itself (P10/P11). Non-contributing calls (failed
fetches, other findings' results, superseded duplicates) are omitted even
though they executed in the same investigation; with no contributing
sources the tool returns empty rather than fabricating an artifact.
"""

import logging
from typing import Any

from app.models import (
    ArtifactTypeV2,
    ArtifactV2,
    ToolCallV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
# WP-2 Version B (S2): the ONE shared canonical digest implementation —
# the admission service (injection side) and this tool (verification
# side) must call the same function (frozen Final Ruling 2).
from app.delivery_admission import canonical_records_digest

logger = logging.getLogger(__name__)

ALLOWED_SCOPES = ("case", "finding")
ALLOWED_FORMATS = ("md",)          # Week 1: Markdown only


def _validation_error(message: str) -> ToolResult:
    return ToolResult(
        outcome=ToolResultOutcome.VALIDATION_ERROR,
        error=ToolError(code="INVALID_ARGUMENT", message=message),
    )


# ---------------------------------------------------------------------------
# Deterministic rendering helpers (pure functions of the structured data)
# ---------------------------------------------------------------------------

def _ref_str(ref: Any) -> str:
    """EvidenceRef or dict → 'kind:id' string."""
    if hasattr(ref, "kind"):
        return f"{ref.kind}:{ref.id}"
    return f"{ref.get('kind', '')}:{ref.get('id', '')}"


def _dedupe_evidence_refs(refs: list[Any]) -> list[Any]:
    """Order-preserving dedup of evidence refs (U00047 fix A): a re-plan
    legitimately re-runs an analysis step, and each execution carries the
    same evidence refs — concatenating every executed call's refs grows
    the bundle's evidence list with duplicate pointers to the SAME
    records (21 → 31 in the original U00047 artifact). Per-call
    provenance is untouched; only the composed bundle's aggregate ref
    list names each record once."""
    seen: set[str] = set()
    unique: list[Any] = []
    for r in refs:
        key = _ref_str(r)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def _render_timeline(events: list[Any]) -> list[str]:
    """Timeline table with investigation-useful fields only (time, event,
    evidence refs). Importance/severity labels are deliberately NOT shown:
    they carry no user-facing meaning without an explanatory model."""
    lines = ["| Time | Event | Evidence |", "|---|---|---|"]
    for ev in events:
        ts = ev.timestamp if hasattr(ev, "timestamp") else ev.get("timestamp", "")
        summary = ev.summary if hasattr(ev, "summary") else ev.get("summary", "")
        refs = ev.evidence_refs if hasattr(ev, "evidence_refs") \
            else ev.get("evidence_refs", [])
        ref_txt = ", ".join(filter(None, (_ref_str(r) for r in refs))) or "—"
        lines.append(f"| {ts} | {str(summary).replace('|', '\\|')} | {ref_txt} |")
    return lines


def _render_policy(policy_payload: dict[str, Any]) -> list[str]:
    lines = ["| Document | Section | Citation | Snippet |", "|---|---|---|---|"]
    for m in policy_payload.get("matches", []):
        snippet = (m.get("snippet") or "").replace("|", "\\|")
        cite = m.get("citation_id")
        cite_txt = str(cite) if cite is not None else "—"
        lines.append(
            f"| {m.get('document', '—')} | {m.get('section', '—')} | "
            f"{cite_txt} | {snippet} |"
        )
    return lines


def _render_gaps(payload: dict[str, Any]) -> list[str]:
    """Precise per-payload gap statements (P14 bounded honesty), from the
    SHARED per-producer semantics (app.composition.gap_statement): the gap
    names the ACTUAL missing item — never a generic "evidence is
    insufficient" claim that would read as if the finding's investigated
    evidence (timeline/records) were incomplete."""
    from app.composition import gap_statement
    gaps: list[str] = []
    statement = gap_statement(payload)
    if statement is None:
        return gaps
    gaps.append(statement)
    for nd in (str(x) for x in (payload.get("next_data_needed") or [])):
        gaps.append(f"- Next data needed: {nd}")
    return gaps
    return gaps


def _finding_block(finding: Any) -> list[str]:
    """Compact finding block (no Severity, no evidence-record dumps).

    The finding's case-fetch evidence_refs are NAVIGATION pointers collected
    from a bounded representative payload — they are never presented as the
    evidence. Concrete records appear only in "Investigation Evidence", and
    only when a view="evidence" investigation actually retrieved them.
    Signal refs render with their actual RP semantic category (detection
    rule / ML detector signal / risk feature) — names only, values never
    invented here.
    """
    def get(name, default=None):
        return getattr(finding, name, None) if hasattr(finding, name) \
            else (finding.get(name, default) if isinstance(finding, dict) else default)
    lines = [
        f"### {get('finding_id', '?')} — {get('title', 'Untitled finding')}",
        "",
        f"- Type: {get('type', 'unknown')}",
        f"- Summary: {get('summary', '')}",
    ]
    sig = get("signal_refs", [])
    if sig:
        by_kind: dict[str, list[str]] = {}
        for s in sig:
            stype = (s.get("signal_type", "?") if isinstance(s, dict)
                     else getattr(s, "signal_type", "?"))
            sname = (s.get("name", "?") if isinstance(s, dict)
                     else getattr(s, "name", "?"))
            by_kind.setdefault(str(stype), []).append(_FEATURE_LABELS.get(sname, sname))
        kind_labels = {
            "Rule": "Detection rule",
            "ML": "ML detector signal",
            "Graph": "Graph signal",
            "Feature": "Risk feature (ML)",
        }
        for stype, names in by_kind.items():
            lines.append(
                f"- {kind_labels.get(stype, stype + ' signal')}: "
                + ", ".join(names)
            )
    pol = get("policy_refs", [])
    if pol:
        # authoritative finding-level markers only (mirror of RP's own [n])
        cites = ", ".join(
            f"[{p.get('citation_id')}]" if isinstance(p, dict)
            else f"[{getattr(p, 'citation_id', '?')}" + "]" for p in pol
        )
        lines.append(f"- Policy citations: {cites}")
    return lines


# Human-readable names for RP ML feature fields (semantics verified in RP
# source: app/ml/features.py — model features computed from raw records;
# values are echoed verbatim, never recomputed).
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


def _render_evidence_records(payload: dict[str, Any]) -> list[str]:
    """Investigation Evidence from a view="evidence" result — the COMPLETE
    authoritative record set (payload carries complete=True by contract).
    Risk features are listed separately from records so aggregate facts and
    concrete records are never conflated."""
    records = payload.get("records") or []
    lines: list[str] = []
    if records:
        # evidence field preservation: render the record's own address
        # when the source records carry one (generic — presence-driven,
        # never synthesized). Records without an address render "—".
        has_addr = any(
            isinstance(r, dict) and (r.get("address") or "").strip()
            for r in records)
        lines.append("| Record | Details | Time |" + (" Address |" if has_addr else ""),)
        lines.append("|---|---|---|" + ("---|" if has_addr else ""))
        for r in records:
            details = (r.get("summary") or "").replace("|", "\\|")
            ts = r.get("timestamp") or "—"
            row = (f"| {r.get('record_kind', '')}:{r.get('record_id', '')} | "
                   f"{details} | {ts} |")
            if has_addr:
                addr = (r.get("address") or "").replace("|", "\\|")
                row += f" {addr or '—'} |"
            lines.append(row)
    # Scope integrity (Artifact Scope Integrity task): a pure records file
    # carries the record rows and their own provenance — aggregate risk
    # features are conversation/composer facts, never auto-attached
    # decorations on a detail artifact.
    return lines


def _render_cross_account_candidates(payload: dict[str, Any]) -> list[str]:
    """Candidate cross-account opposite-trade relationships section (§17
    baseline content rules): candidate relationship, supporting transaction
    IDs, matching criteria/metrics, evidence in the ids themselves, and
    limitations. Candidate terminology only — quantitative matches under
    configured tolerances, never a coordinated-trading conclusion."""
    status = payload.get("analysis_status")
    cfg = payload.get("config") or {}
    rel_tol = cfg.get("value_tolerance_relative")
    floor = cfg.get("value_tolerance_absolute_floor")
    lines = [
        f"Matching criteria (system configuration): time within "
        f"{cfg.get('max_time_delta_seconds', '?'):g}s; value difference "
        f"within {rel_tol:.0%} relative to the larger side"
        + (f", OR within the {floor:g} absolute floor for near-zero "
           f"notionals (pairs admitted only by that floor are labeled "
           f"below)" if isinstance(rel_tol, (int, float))
           and isinstance(floor, (int, float)) else "."),
        "",
    ]
    if status == "MATCH_FOUND":
        lines.append(
            "| Candidate | Type | Account A | Side A | Account B | Side B "
            "| Symbol | Max gap (s) | Value A | Value B | Diff (rel) | "
            "Transactions |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for c in payload.get("candidates") or []:
            ids = ", ".join(c.get("transaction_ids_a", [])
                            + c.get("transaction_ids_b", []))
            lines.append(
                f"| {c.get('candidate_id')} | {c.get('match_type')} "
                f"| {c.get('account_a')} | {c.get('side_a')} "
                f"| {c.get('account_b')} | {c.get('side_b')} "
                f"| {c.get('symbol')} "
                f"| {c.get('max_time_gap_seconds'):g} "
                f"| {c.get('value_a'):,.6g} | {c.get('value_b'):,.6g} "
                f"| {c.get('value_difference'):,.6g} "
                f"({c.get('value_difference_relative'):.1%}) | {ids} |")
        lines.append("")
        lines.append(
            "These are candidate relationships: quantitative matches "
            "under the configured analytical tolerances — leads for "
            "analyst judgment, not conclusions about intent.")
        # floor-admitted transparency: a candidate whose relative
        # difference exceeds the relative tolerance was admitted ONLY by
        # the absolute near-zero floor — label it, never silently
        floor_val = cfg.get("value_tolerance_absolute_floor")
        if isinstance(rel_tol, (int, float)) and \
                isinstance(floor_val, (int, float)):
            floor_admitted = [
                c.get("candidate_id")
                for c in payload.get("candidates") or []
                if isinstance(c.get("value_difference_relative"),
                              (int, float))
                and c["value_difference_relative"] > rel_tol
                and c.get("value_difference") is not None
                and abs(c["value_difference"]) <= floor_val]
            if floor_admitted:
                lines.append(
                    f"Note: {', '.join(floor_admitted)} exceeded the "
                    f"{rel_tol:.0%} relative tolerance and were admitted "
                    f"only by the {floor_val:g} absolute floor (both sides "
                    "are near-zero notionals).")
    else:
        # NO_MATCH and INSUFFICIENT_DATA are DIFFERENT authoritative
        # outcomes: NO_MATCH means the declared scope was completely
        # retrieved and nothing matched (an authoritative zero for the
        # analyzed scope); INSUFFICIENT_DATA means the evidence cannot
        # establish matching either way. Conflating them misreports a
        # complete-scope zero as an inconclusive retrieval (and vice
        # versa) — the wording must match the actual status.
        if status == "NO_MATCH":
            lines.append(
                "No candidate relationships match within the completely "
                "retrieved analysis scope (retrieval complete, zero "
                "matches) — an authoritative zero for the analyzed scope.")
        else:
            lines.append(
                "No candidate relationships are reported: the available "
                "transaction evidence was insufficient for a reliable "
                "conclusion (bounded or empty retrieval). This is not a "
                "no-match result.")
    limitations: list[str] = []
    retrieval = payload.get("retrieval") or {}
    if retrieval.get("truncated"):
        reasons = ", ".join(retrieval.get("truncated_reasons") or [])
        limitations.append(
            "Retrieval was bounded (partial evidence"
            + (f": {reasons}" if reasons else "") + "); absence of "
            "further matches is not established.")
    dq = payload.get("data_quality") or {}
    excluded = dq.get("records_excluded_untimestamped") or 0
    if excluded:
        limitations.append(
            f"{excluded} transaction record(s) were excluded from "
            "matching because no usable timestamp was available.")
    if payload.get("candidates_found", 0) > payload.get(
            "candidates_emitted", 0):
        limitations.append(
            f"{payload['candidates_found']} candidates were found; only "
            f"the top {payload['candidates_emitted']} are listed "
            "(result limit).")
    if limitations:
        lines.append("Limitations:")
        lines.extend(f"- {x}" for x in limitations)
    return lines


def _render_self_opposite(payload: dict[str, Any]) -> list[str]:
    """Self-Opposite (Same-Account) Trade Activity section: the Risk
    Platform's authoritative opposite_trade_ratio + backing rule echoed
    verbatim, plus the account's complete transaction record set. Wording
    names the SAME account; no cross-account terminology, no intent
    claims."""
    lines: list[str] = []
    ratio = payload.get("opposite_trade_ratio")
    if ratio is not None:
        lines.append(
            f"Opposite-trade ratio (Risk Platform aggregate for this "
            f"same account): {ratio:.2%}."
        )
    else:
        lines.append(
            "The Risk Platform does not report an opposite-trade ratio "
            "for this account."
        )
    rule = payload.get("rule")
    if isinstance(rule, dict):
        parts = [f"Detection rule: {rule.get('name')}"]
        if rule.get("description"):
            parts.append(str(rule["description"]))
        if rule.get("threshold"):
            parts.append(f"threshold: {rule['threshold']}")
        lines.append(" — ".join(parts) + ".")
    record_lines = _render_evidence_records(payload)
    if record_lines:
        lines.append("")
        lines.extend(record_lines)
    lines.append("")
    lines.append(
        "Same-account activity only; this implies no cross-account "
        "relationships and nothing about intent."
    )
    return lines


def _gather_gaps(tool_calls: list[ToolCallV2]) -> list[str]:
    gaps: list[str] = []
    for tc in tool_calls:
        r = tc.result
        if r is None or not isinstance(r.data, dict):
            continue
        for g in _render_gaps(r.data):
            if g not in gaps:
                gaps.append(g)
    return gaps


def _canonical_fetch(executed: list[ToolCallV2]):
    """The single canonical Finding-block source: the LATEST executed
    risk_case_fetch SUCCESS in the pool. A deterministic artifact turn
    legitimately runs its own fetch while an earlier turn's intake fetch
    remains in the provenance pool — rendering one block per fetch duplicated
    the same authoritative finding. The latest fetch wins; other fetches
    (and every non-fetch result) never own a Finding block."""
    canonical = None
    for tc in executed:
        if tc.tool_name != "risk_case_fetch" or tc.result is None:
            continue
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if tc.result.outcome == ToolResultOutcome.SUCCESS \
                and data.get("findings"):
            canonical = tc          # later successes overwrite earlier ones
    return canonical


def _findings_of(tc: ToolCallV2) -> list:
    if tc is None or tc.result is None:
        return []
    data = tc.result.data if isinstance(tc.result.data, dict) else {}
    return list(data.get("findings") or [])


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

def _render_candidate_set_export(
    set_ref: str,
    bounded_data: bool,
    source_task_id: str,
    candidates: list[dict],
    case_id: str | None,
    format: str,
    source_tool_calls: list[ToolCallV2] | None = None,
    expected_records_digest: str | None = None,
    expected_binding: dict | None = None,
    delivery_target_id: str | None = None,
    source_step_id: str | None = None,
) -> ToolResult:
    """Deterministic candidate-detail file for a bound candidate set.

    Each candidate renders BOTH legs with the user-requested fields
    (交易账号/时间/方向/金额) taken verbatim from the persisted supporting
    records — no field is inferred, merged across candidates, or
    fabricated. Validation: every candidate must carry exactly the two
    referenced legs with complete required fields; any violation rejects
    the export (no artifact is produced) with the specific gaps named.
    bounded_data stays truthful: the export covers the IDENTIFIED
    candidates only, never claims exhaustiveness."""
    required = ("transaction_id", "account_id", "side", "quantity",
                "value", "symbol", "timestamp")
    missing: list[str] = []
    cleaned: list[dict] = []
    for c in candidates or []:
        sup = c.get("supporting") or []
        legs = [s for s in sup if isinstance(s, dict)]
        if len(legs) != 2:
            missing.append(f"{c.get('candidate_id')}: legs={len(legs)}")
            continue
        gaps = [f"{c.get('candidate_id')}/{s.get('transaction_id')}: "
                + ",".join(sorted(k for k in required if s.get(k) is None))
                for s in legs
                if any(s.get(k) is None for k in required)]
        if gaps:
            missing.extend(gaps)
            continue
        cleaned.append({"candidate": c, "legs": legs})
    if not candidates:
        # zero candidates is a DETERMINATE negative — reported honestly,
        # never dressed up as an export.
        return ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"candidate_set_export_rejected": {
                "set_ref": set_ref,
                "reason": "no_candidates",
                "missing": [],
                "candidates_total": 0,
            }},
            evidence_refs=[],
        )
    if missing:
        return ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"candidate_set_export_rejected": {
                "set_ref": set_ref,
                "reason": "field_incomplete",
                "missing": missing[:20],
                "candidates_total": len(candidates),
            }},
            evidence_refs=[],
        )
    # --- WP-2 Version B (S2) integrity gates — BOTH run BEFORE any
    # rendering and BEFORE ArtifactV2 construction (frozen pipeline:
    # verification precedes creation, so a failed export never produces
    # an artifact — no harvest, no persistence, no visibility). They
    # apply ONLY when the B admission injected the expected values; the
    # legacy contract path (no expected args) is byte-unchanged.
    actual_records_digest = None
    #
    # Gate 1 — records digest (content integrity P1/P2/P3): the actual
    # digest is computed over the RENDER-PARTICIPATING records
    # (post-clean, supporting = exactly the legs that will render), so
    # any cleaning-side drop/modification of business fields fails the
    # comparison. Never claims Markdown byte-equality (the text is a
    # deterministic function of these records, pinned by unit tests).
    if expected_records_digest is not None:
        render_records = [dict(item["candidate"],
                               supporting=item["legs"])
                          for item in cleaned]
        try:
            actual_records_digest = canonical_records_digest(
                render_records)
        except (ValueError, TypeError):
            actual_records_digest = None
        if actual_records_digest != expected_records_digest:
            return ToolResult(
                outcome=ToolResultOutcome.SUCCESS,
                data={"candidate_set_export_rejected": {
                    "set_ref": set_ref,
                    "reason": "digest_mismatch",
                    "expected_records_digest": expected_records_digest,
                    "actual_records_digest": actual_records_digest,
                    "candidates_total": len(candidates or []),
                }},
                evidence_refs=[],
            )
    # Gate 2 — provenance binding verification (source/authorization
    # chain, independent from the records digest): exact match of the
    # received identity fields against the admission-verified binding
    # envelope. set_ref / source_task_id / case_id are compared to THIS
    # call's received values; source_step_id / delivery_target_id are
    # envelope-bound loop-side at injection — here they must be present
    # and well-formed (their binding association is asserted by the
    # admission service). Any missing/replaced/malformed field rejects.
    if expected_binding is not None:
        mismatches: list[str] = []
        received = {"set_ref": set_ref,
                    "source_task_id": source_task_id,
                    "case_id": case_id,
                    "delivery_target_id": delivery_target_id,
                    "source_step_id": source_step_id}
        for field_name in ("set_ref", "source_task_id", "source_step_id",
                           "delivery_target_id", "case_id"):
            expected_value = expected_binding.get(field_name)
            if not expected_value or not isinstance(expected_value, str):
                mismatches.append(f"{field_name}: missing_or_malformed")
                continue
            actual_value = received.get(field_name)
            if actual_value is None:
                continue        # envelope-only field (bound loop-side)
            if actual_value != expected_value:
                mismatches.append(
                    f"{field_name}: {actual_value!r} != {expected_value!r}")
        if mismatches:
            return ToolResult(
                outcome=ToolResultOutcome.SUCCESS,
                data={"candidate_set_export_rejected": {
                    "set_ref": set_ref,
                    "reason": "provenance_mismatch",
                    "mismatches": mismatches[:10],
                    "candidates_total": len(candidates or []),
                }},
                evidence_refs=[],
            )
    lines: list[str] = [
        "# 对冲候选交易明细（绑定候选集）",
        "",
        f"候选集: {set_ref}",
        f"来源任务: {source_task_id}",
        "",
    ]
    tids: list[str] = []
    for item in cleaned:
        c = item["candidate"]
        lines.append(
            f"## {c.get('candidate_id')} — "
            f"{c.get('account_a')} ↔ {c.get('account_b')}")
        lines.append("")
        for leg in item["legs"]:
            lines.append(
                f"- 交易 {leg.get('transaction_id')} | 账号 "
                f"{leg.get('account_id')} | 方向 {leg.get('side')} | "
                f"数量 {leg.get('quantity')} | 计价金额 "
                f"{leg.get('value')} {leg.get('symbol')} | 时间 "
                f"{leg.get('timestamp')}")
            tids.append(str(leg.get("transaction_id")))
        lines.append("")
    if bounded_data:
        lines.append(
            "说明: 以上为本次分析已识别的候选交易明细；检索受限，"
            "不代表已穷尽全部可能的对冲交易。")
        lines.append("")
    content = "\n".join(lines)
    title = f"对冲候选交易明细 — 绑定候选集 {set_ref}"
    import hashlib as _hashlib
    artifact_id = "ART-" + _hashlib.sha256(
        (set_ref + "|" + content).encode()).hexdigest()[:10]
    # FULL ArtifactV2 record — the executor harvests data["artifact"] as
    # the task artifact; a partial dict breaks the TaskDetailResponse
    # schema (real 500: INV-18dd9f073a1c GET /tasks/{id}).
    #
    # WP-2 Version B (Resume Option B fix): when the B admission
    # injected the verified binding envelope, the export's
    # source/authorization chain IS that binding — a resumed run may
    # legitimately have NO in-run tool calls to cite (the read-only
    # prefix ran in a previous process). The legacy
    # no_source_tool_call_provenance rejection applies only to the
    # non-B path; for B deliveries the binding-derived provenance is
    # STRICTER than non-empty ids (Gate 2 already verified the five
    # envelope fields against the admission record).
    if expected_binding is not None:
        provenance = [
            "ADMISSION-BINDING:{set_ref}@{task}/{target}".format(
                set_ref=expected_binding.get("set_ref"),
                task=expected_binding.get("source_task_id"),
                target=expected_binding.get("delivery_target_id"))]
    else:
        provenance = [tc.tool_call_id for tc in (source_tool_calls or [])
                      if getattr(tc, "tool_call_id", None)]
        if not provenance:
            # provenance invariant (models.ArtifactV2 min_length=1):
            # without a traceable source tool call the export is
            # unverifiable → reject.
            return ToolResult(
                outcome=ToolResultOutcome.SUCCESS,
                data={"candidate_set_export_rejected": {
                    "set_ref": set_ref,
                    "reason": "no_source_tool_call_provenance",
                    "missing": ["source_tool_calls"],
                    "candidates_total": len(candidates or []),
                }},
                evidence_refs=[],
            )
    artifact = ArtifactV2(
        artifact_id=artifact_id,
        investigation_id=f"CASE:{case_id}",
        task_id=source_task_id,
        scope=f"candidate_set:{set_ref}",
        artifact_type=ArtifactTypeV2.INVESTIGATION_NOTES,
        format="md",
        title=title,
        content=content,
        source_tool_calls=provenance,
    )
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={
            "artifact": artifact.model_dump(),
            "artifact_id": artifact.artifact_id,
            "title": title,
            "candidate_set_export": {
                "set_ref": set_ref,
                "bounded_data": bounded_data,
                "source_task_id": source_task_id,
                "verification": {"passed": True,
                                 "transaction_ids": sorted(set(tids)),
                                 "candidates": len(cleaned)},
                # WP-2 Version B (S2): dual-verification echo for the
                # loop's delivered-marking (frozen: verification returns
                # both digests + the provenance comparison result).
                **({
                    "records_digest_verification": {
                        "expected": expected_records_digest,
                        "actual": actual_records_digest,
                        "passed": True},
                    "provenance_verification": {
                        "passed": True,
                        "checked_fields": sorted(
                            expected_binding.keys()),
                    },
                } if (expected_records_digest is not None
                      or expected_binding is not None) else {}),
            },
        },
        evidence_refs=[{"kind": "transaction", "id": t}
                       for t in sorted(set(tids))],
    )


def artifact_bundle(
    scope: str,
    format: str = "md",
    case_id: str | None = None,
    finding_id: str | None = None,
    task_id: str | None = None,
    source_tool_calls: list[ToolCallV2] | None = None,
    source_step_ids: list[str] | None = None,
    source_streams: list[str] | None = None,
    prior_artifacts: list[dict] | None = None,
    source_kind: str | None = None,
    set_ref: str | None = None,
    candidate_records: list[dict] | None = None,
    bounded_data: bool = False,
    source_task_id: str | None = None,
    expected_records_digest: str | None = None,
    expected_binding: dict | None = None,
    delivery_target_id: str | None = None,
    source_step_id: str | None = None,
) -> ToolResult:
    """Compose an investigation bundle from executed tool results.

    Args:
        scope: "case" | "finding".
        format: Week 1 locked to "md".
        case_id: owning case.
        finding_id: required when scope="finding".
        task_id: owning task (supplied via executor injection).
        source_tool_calls: executed ToolCallV2 records of the current task
            (injected by the executor from its audit trail — never invented).
    """
    # --- validation ---------------------------------------------------------
    if scope not in ALLOWED_SCOPES:
        return _validation_error(
            f"scope must be one of {list(ALLOWED_SCOPES)}, got {scope!r}."
        )
    if format not in ALLOWED_FORMATS:
        return _validation_error(
            f"format must be {list(ALLOWED_FORMATS)} in Week 1, got {format!r}."
        )
    if not isinstance(case_id, str) or not case_id.strip():
        return _validation_error("case_id is required (non-empty string).")
    case_id = case_id.strip()
    # Plan v2.1: candidate-set detail export (cross-turn anaphoric
    # scope). Deterministic synthesis from the PERSISTED candidate
    # records — never from a full transaction stream, never with
    # fabricated fields. Field-incomplete records REJECT the export
    # (no success-marked incomplete file) with the specific gaps named.
    if source_kind == "candidate_set":
        import logging as _log
        _log.getLogger("app.artifact_bundle").info(
            "DIAG candidate export: set_ref=%s n_candidates=%s "
            "n_records=%s bounded=%s", set_ref,
            len(candidate_records or []),
            sum(len((c.get("supporting") or []))
                for c in (candidate_records or [])),
            bounded_data)
        return _render_candidate_set_export(
            set_ref=set_ref or "",
            bounded_data=bool(bounded_data),
            source_task_id=source_task_id or task_id or "",
            candidates=candidate_records or [],
            case_id=case_id,
            format=format,
            source_tool_calls=source_tool_calls,
            expected_records_digest=expected_records_digest,
            expected_binding=expected_binding,
            delivery_target_id=delivery_target_id,
            source_step_id=source_step_id,
        )
    if scope == "finding":
        if not isinstance(finding_id, str) or not finding_id.strip():
            return _validation_error(
                "finding_id is required when scope='finding'."
            )
        finding_id = finding_id.strip()

    executed = [tc for tc in (source_tool_calls or [])
                if isinstance(tc, ToolCallV2) and tc.result is not None]
    if not executed:
        return ToolResult(
            outcome=ToolResultOutcome.EMPTY,
            data={"scope": scope, "format": format, "case_id": case_id},
            warnings=[
                "No executed tool results are available to compose an "
                "artifact from. Run an investigation step first — no "
                "artifact was fabricated.",
            ],
        )
    if not task_id or not str(task_id).strip():
        return _validation_error(
            "task_id is required for artifact provenance (the bundle records "
            "its owning task)."
        )
    task_id = str(task_id).strip()

    if scope == "finding":
        # Validate the finding exists in the CANONICAL case context (the
        # latest fetch) — no fabricated bundles for unknown findings.
        canonical = _canonical_fetch(executed)
        if canonical is not None:
            known_ids = {
                (f.get("finding_id") if isinstance(f, dict)
                 else getattr(f, "finding_id", None))
                for f in _findings_of(canonical)
            }
            if finding_id not in known_ids:
                return _validation_error(
                    f"Finding {finding_id!r} does not exist in the available "
                    "case results."
                )
        # Scope containment: only results produced FOR this finding (or
        # finding-neutral ones) may contribute content.
        executed = [tc for tc in executed
                    if (tc.result.data or {}).get("finding_id") in (None, finding_id)]

    # R2a stream scoping: a records artifact declares the plan step(s)
    # whose results it composes from (source_step_ids in the plan step's
    # arguments). Filter the executed calls to exactly those steps — the
    # artifact contains ONLY its declared stream, never the whole plan.
    source_step_ids = [str(s).strip() for s in (source_step_ids or [])
                       if str(s).strip()]
    if source_step_ids:
        executed = [
            tc for tc in executed
            if any(tc.tool_call_id.startswith(f"TC-{sid}-")
                   for sid in source_step_ids)]
        if not executed:
            return _validation_error(
                "No executed tool results match the declared source "
                f"step(s) {source_step_ids}; no artifact was fabricated."
            )
    # U00010 B1 (Batch H) — deliverable-boundary guard: ONE file = ONE
    # record stream. When the plan declares the artifact's record
    # stream(s) (source_streams stamped at adoption), any included
    # records call of a DIFFERENT declared stream is a binding/content
    # mismatch: reject the deliverable with a diagnosable error instead
    # of silently composing cross-stream content.
    if source_streams:
        # normalize vocabularies: call arguments carry the view/stream
        # name ("withdrawals"/"transactions"); the plan stamps the
        # evidence-type name ("withdrawal_records"/"transaction_records")
        _STREAM_TO_FAMILY = {"withdrawals": "withdrawal_records",
                             "transactions": "transaction_records"}
        declared = {str(x) for x in source_streams}
        offenders = []
        for tc in executed:
            stream = (tc.arguments or {}).get("stream")
            family = _STREAM_TO_FAMILY.get(str(stream or ""))
            if family and family not in declared:
                offenders.append(
                    f"{tc.tool_call_id} (stream={stream})")
        if offenders:
            return _validation_error(
                "Artifact content/declaration mismatch: the declared "
                f"record stream(s) {sorted(declared)} do not cover the "
                f"included record call(s) {offenders}. The deliverable "
                "was rejected — one file composes from exactly its "
                "declared record stream."
            )
        # Hollow-shell guard (U00010 B1 EN): a records deliverable whose
        # declared streams have NO successful records call would render a
        # near-empty file that reads as a delivery — reject it
        # diagnosably; the honest path is a failed step + partial goal,
        # never a fabricated-looking file.
        def _family_of(tc):
            fam = _STREAM_TO_FAMILY.get(
                str((tc.arguments or {}).get("stream") or ""))
            ok = bool(tc.result is not None
                      and tc.result.outcome == ToolResultOutcome.SUCCESS)
            return (fam, ok)
        delivered = {fam for tc in executed
                     for fam, ok in [_family_of(tc)]
                     if fam in declared and ok}
        if not delivered:
            return _validation_error(
                "No SUCCESSFUL record results for the declared record "
                f"stream(s) {sorted(declared)}; the file would be an "
                "empty shell (its evidence step failed or carried no "
                "records). The deliverable was rejected — the run stays "
                "honestly partial instead of shipping a hollow file."
            )

    # --- C1 idempotency: semantic evidence fingerprint ------------------------
    # A RE_PLAN may re-run the same analysis step (new tool-call IDs, same
    # semantic evidence). If an artifact of this task already covers the
    # SAME semantic evidence for the same deliverable, reuse it instead of
    # persisting a second duplicate export. A genuinely different evidence
    # set (new candidates / remediated records) yields a different
    # fingerprint and proceeds normally.
    import hashlib as _hashlib

    def _evidence_fingerprint(executed_calls):
        # SET-based semantic identity: re-running the same analysis yields
        # the same candidate/record KEYS (deduplicated), so the fingerprint
        # is stable across re-plans even when calls execute twice.
        keys = set()
        for tc in executed_calls:
            data = tc.result.data if (tc.result is not None
                                      and isinstance(tc.result.data,
                                                     dict)) else {}
            if data.get("view") == "evidence":
                ids = tuple(sorted(str(r.get("record_id")
                                       if isinstance(r, dict) else r)
                                   for r in (data.get("records") or [])))
                keys.add(("records", data.get("finding_id"),
                          data.get("stream"), ids))
            for c in (data.get("candidates") or []):
                if not isinstance(c, dict):
                    continue
                sup = tuple(sorted(str(x.get("transaction_id")
                                       if isinstance(x, dict) else x)
                                   for x in (c.get("supporting") or [])))
                keys.add(("cand", c.get("account_a"), c.get("account_b"),
                          c.get("symbol"), sup))
        if not keys:
            return None
        return _hashlib.sha256(
            repr(sorted(repr(k) for k in keys)).encode()).hexdigest()

    fingerprint = _evidence_fingerprint(executed)
    for prior in (prior_artifacts or []):
        if not isinstance(prior, dict):
            continue
        if prior.get("evidence_fingerprint")                 and prior["evidence_fingerprint"] == fingerprint:
            return ToolResult(
                outcome=ToolResultOutcome.SUCCESS,
                data={
                    "artifact": prior,
                    "artifact_id": prior.get("artifact_id"),
                    "title": prior.get("title"),
                    "scope": scope,
                    "format": "md",
                    "artifact_reused": True,
                    "evidence_fingerprint": fingerprint,
                    "source_tool_call_count": 0,
                    "evidence_missing": False,
                    "next_data_needed": [],
                },
                evidence_refs=_dedupe_evidence_refs(
                    [r for tc in executed for r in tc.result.evidence_refs]),
                citation_refs=[p for tc in executed
                               for p in tc.result.citation_refs],
            )

    # --- render sections (deterministic order; only available data) -----------
    lines: list[str] = []
    # Provenance accumulates EXACTLY when content renders (P10/P11): a call
    # is a source iff its result materially contributed lines below — the
    # same containment/composition rules as the content itself.
    contributing_calls: list[ToolCallV2] = []
    title: str
    artifact_type: ArtifactTypeV2

    if scope == "case":
        # AL-2.12: the title names the run's PURPOSE — an analytical run's
        # bundle is titled for its analysis, not as a generic evidence
        # bundle (never a replacement for the conversational answer).
        analyzed_cross_account = any(
            tc.tool_name == "cross_account_trade_analysis" for tc in executed)
        analyzed_related = any(
            tc.tool_name == "related_account_analysis" for tc in executed)
        if analyzed_cross_account:
            title = f"Cross-Account Trade Evidence — {case_id}"
        elif analyzed_related:
            title = f"Related Account Evidence — {case_id}"
        else:
            title = f"Investigation Evidence Bundle — {case_id}"
        artifact_type = ArtifactTypeV2.FINDINGS_SUMMARY
        lines.append(f"# {title}")
        lines.append("")
        lines.append(f"## Case")
        lines.append(f"- Case ID: {case_id}")

        # Canonical Finding ownership: the latest fetch owns the Finding
        # blocks; each authoritative finding renders exactly once even when
        # several fetches of the same case sit in the provenance pool.
        canonical = _canonical_fetch(executed)
        findings_rendered = False
        for f in _findings_of(canonical):
            if not findings_rendered:
                lines.append("")
                lines.append("## Findings")
                findings_rendered = True
            lines.extend(_finding_block(f))
        if findings_rendered:
            contributing_calls.append(canonical)
        # Scope integrity: with no canonical fetch, there are no findings to
        # own — an empty "Not available" header next to concrete evidence
        # blocks (sourced from other steps) reads as a contradiction. The
        # evidence blocks already name their own source finding/step.

        # No Investigation Evidence section here: concrete record IDs are
        # rendered only after a complete-set evidence investigation (see the
        # investigation-evidence block below).
    else:
        title = f"Finding Investigation Bundle — {finding_id}"
        artifact_type = ArtifactTypeV2.INVESTIGATION_NOTES
        lines.append(f"# Finding Investigation Bundle")
        lines.append("")
        lines.append(f"## Finding")
        lines.append(f"- Finding ID: {finding_id}")
        lines.append(f"- Case ID: {case_id}")
        # Scope containment + canonical ownership: render THIS finding's own
        # block, exactly once, from the canonical fetch (its authoritative
        # summary/citations). Supporting tools' representations of the same
        # finding (drilldowns, signal explanations, policy matches) add only
        # their own sections — never a second copy of the canonical block.
        canonical = _canonical_fetch(executed)
        for f in _findings_of(canonical):
            fid = f.finding_id if hasattr(f, "finding_id") \
                else f.get("finding_id")
            if fid == finding_id:
                lines.append("")
                lines.extend(_finding_block(f))
                contributing_calls.append(canonical)
                break

    # --- timeline rows (only when a timeline result exists) --------------------
    timeline_rows: list[str] = []
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        events = data.get("events") or []
        if events:
            timeline_rows.append(f"### {tc.tool_call_id}")
            timeline_rows.extend(_render_timeline(events))
            contributing_calls.append(tc)   # rows rendered → this call contributed
    if timeline_rows:
        lines.append("")
        lines.append("## Timeline")
        lines.extend(timeline_rows)

    # --- signal explanations (only when present) --------------------------------
    # Consistency rule: when RP's structured rule evidence carries no
    # trigger/threshold/contribution fields (they are optional in the RP
    # payload), the artifact renders the rule's OWN description — the same
    # authoritative figures the finding summary cites — and states the
    # structured-field absence explicitly. Never "no trigger values
    # recorded" beside a finding summary that names the trigger figures,
    # and never values backfilled from the finding text.
    signal_lines: list[str] = []
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if "rule" in data and data.get("signal_type") == "Rule":
            rule = data["rule"]
            trigger = rule.get("trigger_values") or {}
            if trigger:
                observed = ", ".join(f"{k}={v}" for k, v in trigger.items())
                signal_lines.append(
                    f"- Rule: {rule.get('name')} — observed {observed}; "
                    f"threshold {rule.get('threshold')}; contribution "
                    f"{rule.get('contribution')}."
                )
            else:
                desc = (rule.get("description") or "").strip()
                parts = [f"- Rule: {rule.get('name')}"]
                if desc:
                    parts.append(f"as described by the Risk Platform: {desc}")
                parts.append(
                    "the platform's structured rule evidence provides no "
                    "separate trigger/threshold/contribution fields for "
                    "this rule."
                )
                signal_lines.append(" — ".join(parts))
        elif data.get("signal_type") == "Feature" \
                and isinstance(data.get("explanation"), dict):
            # Feature observation (AL-2.9/2.14 class): the observed values
            # plus the platform's OWN authoritative semantics (factor
            # entry + finding statement) — echoed verbatim, never
            # reconstructed; a trigger verdict appears only if the
            # platform stated one.
            ex = data["explanation"]
            feats = [f for f in (ex.get("features") or [])
                     if isinstance(f, dict) and f.get("name")]
            if not feats:
                continue      # nothing observable → not a contributor
            vals = ", ".join(f"{f['name']}={f.get('value')}"
                             for f in feats[:5])
            parts = [f"- Feature observation: {vals}."]
            rf = ex.get("risk_factor")
            if isinstance(rf, dict) and (rf.get("description") or "").strip():
                parts.append(
                    f"  Risk factor (severity "
                    f"{rf.get('severity') or 'unknown'}): "
                    f"{rf['description'].strip()}")
            statement = (ex.get("finding_summary") or "").strip()
            if statement:
                parts.append(f"  Platform statement: {statement}")
            signal_lines.append("\n".join(parts))
        elif data.get("signal_type") == "ML" and "explanation" in data:
            ex = data["explanation"]
            if "ml_score" in ex:
                signal_lines.append(
                    f"- ML score: {ex['ml_score']}/100 "
                    f"({ex.get('score_interpretation', 'system signal')}); "
                    f"feature attribution available: "
                    f"{ex.get('attribution_available', False)}."
                )
        else:
            continue          # no signal line rendered → not a contributor
        contributing_calls.append(tc)   # a signal line rendered
    if signal_lines:
        lines.append("")
        lines.append("## Signal Explanation")
        lines.extend(signal_lines)

    # --- attempted but unavailable (capability boundaries) ---------------------
    # An UNSUPPORTED result is a recorded ATTEMPT bounded by the current
    # capability surface — never a success, never risk evidence, and never
    # silently invisible: the bundle renders it as its own bounded line so
    # the investigation trail shows exactly what was tried and why no
    # answer came back. (The tool's own error message is echoed verbatim.)
    unavailable_lines: list[str] = []
    for tc in executed:
        if tc.result is None \
                or tc.result.outcome != ToolResultOutcome.UNSUPPORTED:
            continue
        err = tc.result.error
        unavailable_lines.append(
            f"- {tc.tool_call_id} ({tc.tool_name}): attempted but not "
            "supported by the current capability surface"
            + (f" — {err.code}: {err.message}" if err else "."))
        contributing_calls.append(tc)   # the attempt IS part of the trail
    if unavailable_lines:
        lines.append("")
        lines.append("## Attempted but Unavailable")
        lines.extend(unavailable_lines)

    # --- candidate cross-account relationships (only when present) --------
    # §17 baseline content rules: candidate relationship + supporting
    # transaction IDs + matching criteria/metrics + limitations. Renders
    # for MATCH_FOUND (candidate table) and for INSUFFICIENT_DATA (a
    # bounded insufficiency statement — the artifact must carry the gap,
    # never silently omit the analysis). NO_MATCH renders as its own
    # bounded statement so the analysis is never invisible in a bundle.
    candidate_lines: list[str] = []
    # C1: semantic dedup — a re-plan may re-run the same analysis and yield
    # the same candidate set under a new tool-call ID. Identical candidate
    # content renders ONCE (first occurrence); genuinely different candidate
    # sets render their own blocks (no false dedup).
    seen_candidate_keys: set = set()
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if data.get("analysis") != "cross_account_trade_analysis":
            continue
        if data.get("analysis_status") == "MATCH_FOUND" \
                and not (data.get("candidates") or []):
            continue          # nothing to render → not a contributor
        _key = frozenset(
            (c.get("account_a"), c.get("account_b"), c.get("symbol"),
             tuple(sorted(x.get("transaction_id") if isinstance(x, dict)
                          else str(x)
                          for x in (c.get("supporting") or []))))
            for c in (data.get("candidates") or []) if isinstance(c, dict))
        if _key in seen_candidate_keys:
            continue    # identical analysis already rendered — no duplicate block
        seen_candidate_keys.add(_key)
        block = _render_cross_account_candidates(data)
        if block:
            if candidate_lines:
                candidate_lines.append("")
            candidate_lines.append(f"### {tc.tool_call_id}")
            candidate_lines.extend(block)
            contributing_calls.append(tc)
    if candidate_lines:
        lines.append("")
        lines.append("## Candidate Cross-Account Relationships")
        lines.extend(candidate_lines)

    # U00004 file-delivery closure: a related-account analysis in the run
    # renders its authoritative account list — the hedging-candidate file
    # carries the set context the pairs were found in (standalone file,
    # user DoD: the complete related-account list beside the candidates).
    # Bounded like the chat rendering: the count plus the shown list, with
    # an explicit shown/total disclosure when the payload is a preview.
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if data.get("analysis") != "related_account_analysis":
            continue
        accounts = [
            (a.get("user_id") if isinstance(a, dict) else str(a))
            for a in (data.get("related_accounts") or []) if a]
        count = data.get("related_accounts_count") or len(accounts)
        if not accounts and not count:
            continue
        lines.append("")
        lines.append(f"## Related Accounts ({count})")
        shown = accounts[:25]
        lines.extend(f"- {a}" for a in shown)
        if len(shown) < count:
            lines.append(
                f"(first {len(shown)} shown; {count} related accounts in "
                "total — the complete list is in the evidence result.)")
        contributing_calls.append(tc)

    # --- self-opposite (same-account) trade activity -----------------------
    # The canonical self view: RP's own ratio + rule, the account's complete
    # transaction record set, same-account wording only. Rendered BEFORE
    # the concrete-evidence section (different view key, no overlap).
    self_lines: list[str] = []
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if data.get("view") != "self_opposite_trades":
            continue
        block = _render_self_opposite(data)
        if block:
            if self_lines:
                self_lines.append("")
            self_lines.append(f"### {tc.tool_call_id}")
            self_lines.extend(block)
            contributing_calls.append(tc)
    if self_lines:
        lines.append("")
        lines.append("## Self-Opposite (Same-Account) Trade Activity")
        lines.extend(self_lines)

    # --- policy context (only when present) ---------------------------------------
    # Scope containment (P11) with EXPLICIT presentation scope:
    #   finding-scoped artifact → strict finding containment. Only the
    #     finding's OWN authoritative citations (via the Finding block)
    #     render. A policy_lookup result with NO finding-level association
    #     contributes a precise no-basis statement — its case-level match
    #     list is conversation/case-scope presentation and must never leak
    #     here as if it were the finding's policy basis.
    #   case-scoped artifact → the complete authoritative case citation
    #     collection renders (from the canonical fetch), resolving [n]
    #     markers for every finding.
    # The conversation-level case-policy fallback (composer) is a different
    # presentation context and is unaffected by this containment.
    policy_lines: list[str] = []
    no_basis_statement_rendered = False
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if not (isinstance(data.get("matches"), list) and data["matches"]):
            continue
        if scope == "finding":
            if data.get("finding_id") not in (None, finding_id):
                continue          # another finding's policy result
            if data.get("associated_policy_refs"):
                # finding-associated citations: render ONLY the matches that
                # are actually associated with THIS finding. data["matches"]
                # is the ranked case-level list and may include citations
                # belonging to other findings — those are case-scope
                # presentation and must never appear here (P11: rendered
                # policy citation IDs ⊆ associated_policy_refs).
                associated_ids = {
                    p.get("citation_id") if isinstance(p, dict)
                    else getattr(p, "citation_id", None)
                    for p in data["associated_policy_refs"]
                }
                scoped = dict(data)
                scoped["matches"] = [
                    m for m in data["matches"]
                    if m.get("citation_id") in associated_ids
                ]
                if scoped["matches"]:
                    policy_lines.append(f"### {tc.tool_call_id}")
                    policy_lines.extend(_render_policy(scoped))
                    contributing_calls.append(tc)
            elif not no_basis_statement_rendered:
                # no finding-level basis: state it precisely; do NOT render
                # the case-level fallback list as the finding's policy basis
                policy_lines.append(
                    "No finding-level policy basis is attached to this "
                    "finding."
                )
                no_basis_statement_rendered = True
                contributing_calls.append(tc)   # the statement IS its content
            else:
                continue          # subsequent no-basis results: no new content
        else:
            policy_lines.append(f"### {tc.tool_call_id}")
            policy_lines.extend(_render_policy(data))
            contributing_calls.append(tc)   # matches rendered → contributed
    case_policy_lines: list[str] = []
    seen_case_refs: set[tuple] = set()
    if scope == "case":
        # case-scope artifact: the complete case citation list is legitimate
        # case-level content (resolves [n] markers for every finding). The
        # canonical fetch owns it — a superseded earlier fetch's collection
        # is the same case data already represented by the canonical fetch.
        for tc in ([canonical] if canonical is not None else []):
            data = tc.result.data if isinstance(tc.result.data, dict) else {}
            refs = data.get("policy_refs") or []
            new_rows: list[str] = []
            for p in refs:
                if hasattr(p, "citation_id"):
                    cite, doc, section = p.citation_id, p.doc, p.section
                else:
                    cite = p.get("citation_id")
                    doc, section = p.get("doc"), p.get("section")
                key = (cite, doc, section)
                if key in seen_case_refs:
                    continue
                seen_case_refs.add(key)
                cite_txt = f"[{cite}]" if cite is not None else "—"
                new_rows.append(
                    f"| {cite_txt} | {doc or '—'} | "
                    f"{(section or '—').replace('|', chr(92) + '|')} |"
                )
            if new_rows:
                case_policy_lines.extend(new_rows)
                contributing_calls.append(tc)   # citation rows contributed
        if case_policy_lines:
            policy_lines.append("Case-level citations (referenced as [n] by "
                                "finding text):")
            policy_lines.append("| Citation | Document | Section |")
            policy_lines.append("|---|---|---|")
            policy_lines.extend(case_policy_lines)
    if policy_lines:
        lines.append("")
        lines.append("## Policy References")
        lines.extend(policy_lines)

    # --- investigation evidence ------------------------------------------------------
    # FINAL global rule: concrete record IDs may appear in a user-facing
    # artifact ONLY when the COMPLETE authoritative record set for the
    # investigation scope was retrieved (a view="evidence" result,
    # complete=True by contract). Pre-evidence artifacts carry NO
    # Investigation Evidence section and no representative pointers —
    # the case-fetch pointer list is a bounded subset and would imply a
    # completeness the source never provided.
    concrete_lines = _render_concrete_evidence(executed)
    if concrete_lines:
        lines.append("")
        lines.append("## Investigation Evidence")
        lines.extend(concrete_lines)
        # every view="evidence" call whose records rendered contributed
        for tc in executed:
            data = tc.result.data if isinstance(tc.result.data, dict) else {}
            if data.get("view") == "evidence" and data.get("complete") \
                    and (data.get("records") or data.get("risk_features")):
                contributing_calls.append(tc)

    # --- evidence gaps: NOT rendered in user-facing artifacts ---------------
    # Product decision: "Evidence Gaps" (e.g. missing transaction-level
    # feature attribution) exposes internal explanation limitations without
    # investigation value, and can read as a false "evidence missing" claim
    # beside complete Timeline/Evidence sections. The underlying
    # evidence_missing / next_data_needed fields remain on the ToolResults
    # (bounded honesty, conversation semantics, logging, future use) — only
    # the artifact rendering is removed.
    gaps = _gather_gaps(executed)          # still computed for the result
    # L1 record-parity check (tool layer): the evidence section renders
    # the FULL uncapped payload record set — the rendered row count must
    # equal the declared record_count. A mismatch is surfaced on the tool
    # result (never silently absorbed); output-level satisfaction
    # propagation is out of L1 scope (no output↔bundle binding exists).
    record_parity_failed = False
    # U00047 delivery closure: distinguish "parity checked and passed"
    # from "parity check never ran" — record_parity_failed=False alone is
    # ambiguous (a bundle rendered from NO record-bearing source also
    # leaves the flag False), and consumers must not read that as a
    # verified-complete file.
    record_parity_checked = False
    for tc in executed:
        d = tc.result.data if isinstance(tc.result.data, dict) else {}
        if d.get("view") == "evidence" and isinstance(
                d.get("record_count"), int):
            record_parity_checked = True
            if d["record_count"] != len(d.get("records") or []):
                record_parity_failed = True
                gaps.append(
                    f"record count parity: declared record_count "
                    f"{d['record_count']} != {len(d.get('records') or [])} "
                    f"records in the evidence view "
                    f"({d.get('finding_id', '?')})")
    # 2026-11-03 final-acceptance incident (ART-01f4b21df3): cross-call
    # aggregation consistency. The renderer above dedupes rows by
    # (record_kind, record_id); per-call parity alone cannot see a
    # repeated set (69==69 per call while the file held 138 rows). When
    # ≥2 evidence calls cover the SAME finding, their record-ID sets are
    # compared: identical sets ⇒ the deduplicated section IS the declared
    # complete set (consistent); divergent sets ⇒ the union is rendered
    # but completeness against the authoritative source CANNOT be proven
    # and is reported as such — never silently claimed. A single call is
    # vacuously consistent (no second source to disagree with).
    record_id_sets: list[tuple[str, list[str]]] = []
    for tc in executed:
        d = tc.result.data if isinstance(tc.result.data, dict) else {}
        if d.get("view") == "evidence" and isinstance(
                d.get("record_count"), int):
            ids = sorted(str(r.get("record_id")
                             if isinstance(r, dict) else r)
                         for r in (d.get("records") or []))
            record_id_sets.append((d.get("finding_id", "?"), ids))
    record_set_consistent: bool | None = None
    if len(record_id_sets) >= 2:
        fid0, ids0 = record_id_sets[0]
        same_finding = all(fid == fid0 for fid, _ in record_id_sets)
        if not same_finding:
            record_set_consistent = None      # per-finding sections; no
        else:                                 # cross-call repetition
            record_set_consistent = all(
                ids == ids0 for _, ids in record_id_sets[1:])
            if not record_set_consistent:
                gaps.append(
                    "record set consistency: repeated evidence calls for "
                    f"{fid0} returned DIFFERENT record sets — the "
                    "deduplicated union is rendered, but completeness "
                    "against the authoritative source could not be "
                    "verified")
    # metadata (evidence_missing / next_data_needed on the ToolResult payload)

    # --- provenance -----------------------------------------------------------------------
    # Provenance-correctness rule (§18/P10): contributing_calls accumulated
    # above exactly when content rendered — provenance and content share the
    # same containment rules by construction. Non-contributing calls (failed
    # fetches, superseded duplicates, other findings' results) are omitted
    # even though they executed in the same investigation. artifact_bundle
    # can never appear (the executor excludes it from the injected pool).
    # An artifact with NO content contributors would carry fabricated
    # provenance if it listed its inputs — bounded outcome instead.
    if not contributing_calls:
        return ToolResult(
            outcome=ToolResultOutcome.EMPTY,
            data={"scope": scope, "format": format, "case_id": case_id},
            warnings=[
                "No content-contributing tool calls are available: the "
                "executed tool results carried no findings, timeline, "
                "evidence, signal explanations, or policy references, so "
                "no artifact was composed (provenance would be fabricated).",
            ],
        )
    # One call can contribute several sections (e.g. the canonical fetch owns
    # both the Finding blocks and the case citation table) — provenance lists
    # each contributing call exactly once, in first-contribution order.
    seen_calls: set[str] = set()
    unique_contributors: list[ToolCallV2] = []
    for tc in contributing_calls:
        if tc.tool_call_id in seen_calls:
            continue
        seen_calls.add(tc.tool_call_id)
        unique_contributors.append(tc)
    contributing_calls = unique_contributors
    lines.append("")
    lines.append("## Source Tool Calls")
    for tc in contributing_calls:
        lines.append(f"- {tc.tool_call_id} ({tc.tool_name}, {tc.status.value})")

    content = "\n".join(lines) + "\n"

    # Deterministic content-derived ID (sha256, not hash(): str hashing is
    # salted per-process and would break reproducibility).
    import hashlib
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
    artifact = ArtifactV2(
        artifact_id=f"ART-{digest}",
        investigation_id=f"CASE:{case_id}",
        task_id=task_id,
        scope=f"case:{case_id}" if scope == "case" else f"finding:{finding_id}",
        artifact_type=artifact_type,
        format="md",
        title=title,
        content=content,
        source_tool_calls=[tc.tool_call_id for tc in contributing_calls],
    )
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={
            "artifact": artifact.model_dump(),
            "artifact_id": artifact.artifact_id,
            "title": artifact.title,
            "scope": scope,
            "format": "md",
            "source_tool_call_count": len(contributing_calls),
            "source_step_ids": source_step_ids or [],
            "evidence_fingerprint": fingerprint,
            "evidence_missing": bool(gaps),
            "record_parity_failed": record_parity_failed,
            "record_parity_checked": record_parity_checked,
            "record_set_consistent": record_set_consistent,
            "next_data_needed": gaps,
        },
        evidence_refs=_dedupe_evidence_refs(
            [r for tc in executed for r in tc.result.evidence_refs]),
        citation_refs=[p for tc in executed for p in tc.result.citation_refs],
    )


def _render_concrete_evidence(executed: list[ToolCallV2]) -> list[str]:
    """Complete concrete-evidence section from view="evidence" results.

    Included ONLY when the conversation actually entered concrete-evidence
    detail (a finding_drilldown view="evidence" result exists). Those
    results carry complete=True — the complete authoritative record set by
    contract; they are rendered verbatim, never re-truncated.

    2026-11-03 final-acceptance incident (ART-01f4b21df3): a RE_PLAN may
    re-run the SAME records step (new call ids, identical 69-record set).
    Per-call rendering then wrote the complete set TWICE (138 rows for 69
    records) while the per-call parity check (69==69 per call) could not
    see it. Records now aggregate across calls with (record_kind,
    record_id) as the identity key — FIRST occurrence wins (original
    fields preserved verbatim), duplicates skipped; one section renders
    per finding. Record-less rows (no record_id) cannot be identified and
    are always included — dedup is never guessed."""
    lines: list[str] = []
    seen_keys: set = set()
    section_order: list[str] = []
    records_by_section: dict[str, list] = {}
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if data.get("view") != "evidence" or not data.get("complete"):
            continue
        finding_id = data.get("finding_id", "?")
        if finding_id not in records_by_section:
            records_by_section[finding_id] = []
            section_order.append(finding_id)
        for r in (data.get("records") or []):
            rid = r.get("record_id") if isinstance(r, dict) else None
            kind = r.get("record_kind") if isinstance(r, dict) else None
            if rid:
                key = (str(kind), str(rid))
                if key in seen_keys:
                    continue        # cross-call duplicate — first wins
                seen_keys.add(key)
            records_by_section[finding_id].append(r)
    for finding_id in section_order:
        rows = records_by_section[finding_id]
        if not rows:
            continue
        block = _render_evidence_records({"records": rows})
        if block:
            if lines:
                lines.append("")
            lines.append(f"### Evidence for {finding_id} "
                         f"(complete record set)")
            lines.extend(block)
    return lines

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
        lines.append("| Record | Details | Time |", )
        lines.append("|---|---|---|")
        for r in records:
            details = (r.get("summary") or "").replace("|", "\\|")
            ts = r.get("timestamp") or "—"
            lines.append(
                f"| {r.get('record_kind', '')}:{r.get('record_id', '')} | "
                f"{details} | {ts} |"
            )
    features = payload.get("risk_features") or {}
    if features:
        if records:
            lines.append("")
        lines.append("Risk features (ML, values as reported by the Risk "
                     "Platform):")
        for k, v in features.items():
            label = _FEATURE_LABELS.get(k, k)
            lines.append(f"- {label}: {v}")
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

def artifact_bundle(
    scope: str,
    format: str = "md",
    case_id: str | None = None,
    finding_id: str | None = None,
    task_id: str | None = None,
    source_tool_calls: list[ToolCallV2] | None = None,
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

    # --- render sections (deterministic order; only available data) -----------
    lines: list[str] = []
    # Provenance accumulates EXACTLY when content renders (P10/P11): a call
    # is a source iff its result materially contributed lines below — the
    # same containment/composition rules as the content itself.
    contributing_calls: list[ToolCallV2] = []
    title: str
    artifact_type: ArtifactTypeV2

    if scope == "case":
        title = f"Investigation Evidence Bundle — {case_id}"
        artifact_type = ArtifactTypeV2.FINDINGS_SUMMARY
        lines.append(f"# Investigation Evidence Bundle")
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
        if not findings_rendered:
            lines.append("- Findings: Not available (no case fetch this task).")

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
            "evidence_missing": bool(gaps),
            "next_data_needed": gaps,
        },
        evidence_refs=[r for tc in executed for r in tc.result.evidence_refs],
        citation_refs=[p for tc in executed for p in tc.result.citation_refs],
    )


def _render_concrete_evidence(executed: list[ToolCallV2]) -> list[str]:
    """Complete concrete-evidence section from view="evidence" results.

    Included ONLY when the conversation actually entered concrete-evidence
    detail (a finding_drilldown view="evidence" result exists). Those
    results carry complete=True — the complete authoritative record set by
    contract; they are rendered verbatim, never re-truncated."""
    lines: list[str] = []
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if data.get("view") == "evidence" and data.get("complete"):
            block = _render_evidence_records(data)
            if block:
                if lines:
                    lines.append("")
                lines.append(f"### Evidence for {data.get('finding_id', '?')} "
                             f"(complete record set)")
                lines.extend(block)
    return lines

"""artifact_bundle — Week 1 artifact composition tool.

COMPOSITION, NOT COMPUTATION: bundles already-produced structured
investigation results (prior ToolResults in the current task) into a
deterministic Markdown artifact. No new risk analysis, no LLM, no RP calls
when existing structured results suffice, no invented facts/citations/
timestamps. Sections without available data are omitted or rendered as
explicit evidence gaps — never padded.

Provenance closure (mandatory): source_tool_calls must reference actual
executed ToolCallV2 records of this task; with no executed sources the tool
returns empty rather than fabricating an artifact.
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
    lines = ["| Time | Event | Importance | Evidence |", "|---|---|---|---|"]
    for ev in events:
        ts = ev.timestamp if hasattr(ev, "timestamp") else ev.get("timestamp", "")
        summary = ev.summary if hasattr(ev, "summary") else ev.get("summary", "")
        imp = ev.importance if hasattr(ev, "importance") else ev.get("importance", "")
        imp = getattr(imp, "value", imp) or "—"
        refs = ev.evidence_refs if hasattr(ev, "evidence_refs") \
            else ev.get("evidence_refs", [])
        ref_txt = ", ".join(filter(None, (_ref_str(r) for r in refs))) or "—"
        lines.append(f"| {ts} | {str(summary).replace('|', '\\|')} | {imp} | {ref_txt} |")
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
    gaps: list[str] = []
    if payload.get("evidence_missing"):
        gaps.append("Evidence is insufficient for this result.")
        for nd in payload.get("next_data_needed") or []:
            gaps.append(f"- Next data needed: {nd}")
    return gaps


def _finding_block(finding: Any) -> list[str]:
    """Finding metadata + refs from a canonical Finding or its dumped dict."""
    def get(name, default=None):
        return getattr(finding, name, None) if hasattr(finding, name) \
            else (finding.get(name, default) if isinstance(finding, dict) else default)
    lines = [
        f"### {get('finding_id', '?')} — {get('title', 'Untitled finding')}",
        "",
        f"- Type: {get('type', 'unknown')}",
        f"- Severity: {getattr(get('severity'), 'value', get('severity', 'unknown'))}",
        f"- Summary: {get('summary', '')}",
    ]
    ev = get("evidence_refs", [])
    if ev:
        lines.append("- Evidence: " + ", ".join(filter(None, (_ref_str(r) for r in ev))))
    sig = get("signal_refs", [])
    if sig:
        sig_txt = ", ".join(
            (s.get("name", "?") if isinstance(s, dict) else
             getattr(s, "name", "?")) for s in sig
        )
        lines.append(f"- Signals: {sig_txt}")
    pol = get("policy_refs", [])
    if pol:
        cites = ", ".join(
            f"[{p.get('citation_id')}]" if isinstance(p, dict)
            else f"[{getattr(p, 'citation_id', '?')}" + "]" for p in pol
        )
        lines.append(f"- Policy citations: {cites}")
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
        # Validate the finding exists in the fetched case context (if a case
        # fetch is among the sources) — no fabricated bundles for unknown
        # findings.
        fetch_results = [tc.result.data for tc in executed
                         if isinstance(tc.result.data, dict)
                         and "findings" in tc.result.data]
        if fetch_results:
            known_ids = {
                f.get("finding_id") if isinstance(f, dict)
                else getattr(f, "finding_id", None)
                for f in fetch_results[0].get("findings") or []
            }
            if finding_id not in known_ids:
                return _validation_error(
                    f"Finding {finding_id!r} does not exist in the available "
                    "case results."
                )
        executed = [tc for tc in executed
                    if (tc.result.data or {}).get("finding_id") in (None, finding_id)]

    # --- render sections (deterministic order; only available data) -----------
    lines: list[str] = []
    title: str
    artifact_type: ArtifactTypeV2

    if scope == "case":
        title = f"Investigation Evidence Bundle — {case_id}"
        artifact_type = ArtifactTypeV2.FINDINGS_SUMMARY
        lines.append(f"# Investigation Evidence Bundle")
        lines.append("")
        lines.append(f"## Case")
        lines.append(f"- Case ID: {case_id}")

        # canonical findings come from fetch_case results
        findings_rendered = False
        for tc in executed:
            data = tc.result.data if isinstance(tc.result.data, dict) else {}
            for f in data.get("findings") or []:
                if not findings_rendered:
                    lines.append("")
                    lines.append("## Findings")
                    findings_rendered = True
                lines.extend(_finding_block(f))
        if not findings_rendered:
            lines.append("- Findings: Not available (no case fetch this task).")

        evidence_section = _collect_evidence(executed)
        lines.extend(evidence_section)
    else:
        title = f"Finding Investigation Bundle — {finding_id}"
        artifact_type = ArtifactTypeV2.INVESTIGATION_NOTES
        lines.append(f"# Finding Investigation Bundle")
        lines.append("")
        lines.append(f"## Finding")
        lines.append(f"- Finding ID: {finding_id}")
        lines.append(f"- Case ID: {case_id}")

    # --- timeline rows (only when a timeline result exists) --------------------
    timeline_rows: list[str] = []
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        events = data.get("events") or []
        if events:
            timeline_rows.append(f"### {tc.tool_call_id}")
            timeline_rows.extend(_render_timeline(events))
    if timeline_rows:
        lines.append("")
        lines.append("## Timeline")
        lines.extend(timeline_rows)

    # --- signal explanations (only when present) --------------------------------
    signal_lines: list[str] = []
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if "rule" in data and data.get("signal_type") == "Rule":
            rule = data["rule"]
            trigger = rule.get("trigger_values") or {}
            observed = ", ".join(f"{k}={v}" for k, v in trigger.items()) \
                if trigger else "no trigger values recorded"
            signal_lines.append(
                f"- Rule: {rule.get('name')} — observed {observed}; "
                f"threshold {rule.get('threshold')}; contribution "
                f"{rule.get('contribution')}."
            )
        elif data.get("signal_type") == "ML" and "explanation" in data:
            ex = data["explanation"]
            if "ml_score" in ex:
                signal_lines.append(
                    f"- ML score: {ex['ml_score']}/100 "
                    f"({ex.get('score_interpretation', 'system signal')}); "
                    f"feature attribution available: "
                    f"{ex.get('attribution_available', False)}."
                )
    if signal_lines:
        lines.append("")
        lines.append("## Signal Explanation")
        lines.extend(signal_lines)

    # --- policy context (only when present) ---------------------------------------
    policy_lines: list[str] = []
    for tc in executed:
        data = tc.result.data if isinstance(tc.result.data, dict) else {}
        if isinstance(data.get("matches"), list) and data["matches"]:
            policy_lines.append(f"### {tc.tool_call_id}")
            policy_lines.extend(_render_policy(data))
    if policy_lines:
        lines.append("")
        lines.append("## Policy References")
        lines.extend(policy_lines)

    # --- evidence section (finding scope) --------------------------------------------
    if scope == "finding":
        ev_lines = _collect_evidence(executed)
        if ev_lines:
            lines.extend(ev_lines)

    # --- evidence gaps (only explicitly reported ones) ---------------------------------
    gaps = _gather_gaps(executed)
    if gaps:
        lines.append("")
        lines.append("## Evidence Gaps")
        lines.extend(gaps)

    # --- provenance -----------------------------------------------------------------------
    lines.append("")
    lines.append("## Source Tool Calls")
    for tc in executed:
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
        source_tool_calls=[tc.tool_call_id for tc in executed],
    )
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={
            "artifact": artifact.model_dump(),
            "artifact_id": artifact.artifact_id,
            "title": artifact.title,
            "scope": scope,
            "format": "md",
            "source_tool_call_count": len(executed),
            "evidence_missing": bool(gaps),
            "next_data_needed": gaps,
        },
        evidence_refs=[r for tc in executed for r in tc.result.evidence_refs],
        citation_refs=[p for tc in executed for p in tc.result.citation_refs],
    )


def _collect_evidence(executed: list[ToolCallV2]) -> list[str]:
    """Deterministic 'Investigation Evidence' section from actual result refs."""
    seen: list[str] = []
    for tc in executed:
        for r in tc.result.evidence_refs:
            txt = _ref_str(r)
            if txt not in seen:
                seen.append(txt)
    if not seen:
        return []
    lines = ["", "## Investigation Evidence"]
    lines.extend(f"- {s}" for s in seen)
    return lines

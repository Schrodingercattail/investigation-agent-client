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

_CITATION_MARKER_RE = __import__("re").compile(r"\s*\[\d+\]")

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


# ---------------------------------------------------------------------------
# Response composition (deterministic; grounded in ToolResults only)
# ---------------------------------------------------------------------------

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
) -> str:
    """Transform structured results into a concise human-readable response.

    Rules: never invent facts/timestamps/entities; preserve evidence and
    citation references; surface evidence_missing explicitly; keep empty and
    integration failure distinct; no new domain reasoning.
    """
    if clarification_message:
        return clarification_message

    if planning_failure is not None:
        # Bounded honesty (P14): each planning failure keeps its semantic
        # meaning in human-readable language. No Pydantic/schema text, no
        # LLM_OUTPUT_INVALID internals, no skill/step identifiers.
        code = planning_failure.code
        if code == "LLM_OUTPUT_INVALID":
            # The request could not be mapped onto the supported
            # investigation workflow — most commonly because it lies
            # outside it. Say what the system CAN do instead.
            return ("I can help investigate the current Risk Platform "
                    "case, but I couldn't map that request to a supported "
                    "investigation action. You can ask about the case, a "
                    "finding, its timeline, its evidence, or the policies "
                    "that apply.")
        if code == "LLM_UNAVAILABLE":
            return ("The planning service is temporarily unavailable, so "
                    "this request could not be started. Please try again "
                    "in a moment — the investigation is unaffected.")
        if code == "NO_ELIGIBLE_SKILL":
            return ("This request doesn't match an investigation action "
                    "available for the current context. You can ask about "
                    "the case, a finding, its timeline, its evidence, or "
                    "the policies that apply.")
        if code == "UNSUPPORTED_REQUEST":
            return ("This request needs a focused finding with the right "
                    "capabilities. Select a finding from the Findings "
                    "panel first, then ask again.")
        # Unknown planning failure: bounded and readable, no internals.
        return ("I couldn't plan this request as an investigation action. "
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
            scoped = _unsupported_capability_message(fatal, tool_calls)
            if scoped:
                return scoped
            return ("This investigation capability is not supported for "
                    "the current target. No investigation step was executed; "
                    "nothing was fabricated.")
        if outcome == "validation_error":
            return ("The request could not be completed as specified — it "
                    "does not match what this investigation can execute. "
                    "Nothing was fabricated.")
        if outcome == "integration_error":
            return ("The Risk Platform could not be reached, so this "
                    "investigation step could not be completed. The task is "
                    "recorded as failed; nothing was fabricated. Please try "
                    "again later.")
        if outcome == "empty":
            # A required step genuinely found nothing; the per-payload EMPTY
            # handling above does not apply because the step chain aborted.
            return ("The Risk Platform returned no matching results for "
                    "this request. The task is recorded as completed with "
                    "an empty result; nothing was fabricated.")
        if fatal.code == "TOOL_NOT_IMPLEMENTED":
            return ("This investigation capability is not available in the "
                    "current system. Nothing was executed; nothing was "
                    "fabricated.")
        if fatal.code == "PLAN_CONTRACT_VIOLATION":
            return ("The requested investigation could not be planned "
                    "within the supported investigation surface. Nothing "
                    "was executed.")
        # Unknown/unclassified executor failure: bounded, semantic, and
        # free of internal identifiers.
        return ("This investigation step could not be completed. The task "
                "is recorded as failed; nothing was fabricated.")

    if not tool_calls:
        return ("No investigation step could be executed for this request.")

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
                prose.append(
                    f"The Risk Platform returned {n} {kind_txt} record"
                    f"{'s' if n != 1 else ''} supporting this finding. All "
                    "records are shown below."
                )
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
                        "The Risk Platform does not report an opposite-"
                        "trade ratio for this account, but its supporting "
                        "transaction records are shown below."
                    )
                elif ratio == 0:
                    prose.append(
                        "No opposite-side trading activity was observed "
                        "for this account: the Risk Platform's opposite-"
                        "trade ratio is 0% over its trade stream."
                    )
                else:
                    prose.append(
                        "The Risk Platform shows opposite-side trading "
                        "activity by this SAME account: an opposite-trade "
                        f"ratio of {ratio:.2%} over the account's own "
                        "trade stream (aggregate computed by the Risk "
                        "Platform)."
                    )
                rule = data.get("rule")
                if isinstance(rule, dict):
                    desc = (rule.get("description") or "").strip()
                    parts = [f"Detection rule: {rule.get('name')}"]
                    if desc:
                        parts.append(_strip_citation_markers(desc))
                    if rule.get("threshold"):
                        parts.append(f"threshold: {rule['threshold']}")
                    prose.append(" — ".join(parts) + ".")
                n = data.get("record_count", 0)
                if n:
                    prose.append(
                        f"All {n} supporting transaction record"
                        f"{'s are' if n != 1 else ' is'} shown below."
                    )
                prose.append(
                    "This is same-account activity only; it implies no "
                    "cross-account relationships and nothing about intent."
                )
            elif data.get("view") == "timeline":
                events = data.get("events") or []
                total = data.get("total_events", len(events))
                if data.get("truncated"):
                    prose.append(
                        f"Timeline preview: showing {len(events)} of "
                        f"{total} events (chronological). This is a "
                        "bounded navigation view — ask for the complete "
                        "timeline to see every event."
                    )
                else:
                    prose.append(
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
                            f"{cluster['member_count']} accounts in the "
                            "cluster")
                    if cluster.get("cluster_risk_score") is not None:
                        parts.append(
                            f"cluster risk score "
                            f"{cluster['cluster_risk_score']}")
                    if parts:
                        prose.append(
                            "Risk Platform network attribution: "
                            + ", ".join(parts) + ".")
                    accounts = data.get("related_accounts") or []
                    count = data.get("related_accounts_count")
                    if accounts:
                        listing = ", ".join(str(a) for a in accounts)
                        more = ""
                        if count is not None and count > len(accounts):
                            more = (f" (showing {len(accounts)} of "
                                    f"{count})")
                        prose.append(
                            f"Related accounts: {listing}{more}.")
                    devices = data.get("shared_devices") or []
                    if devices:
                        prose.append(
                            "Shared devices linking these accounts: "
                            + "; ".join(str(d) for d in devices) + ".")
                elif data.get("evidence_missing"):
                    prose.append(
                        "The Risk Platform provides no network attribution "
                        "for this case, so no related accounts can be "
                        "listed.")
                else:
                    prose.append(
                        "The Risk Platform attributes no related accounts "
                        "to this account's network.")
            elif data.get("analysis") == "cross_account_trade_analysis":
                # Candidate cross-account opposite-trade analysis (§6
                # observation contract): candidate terminology only — the
                # result is a quantitative match under configured
                # tolerances, never a confirmed-pattern claim.
                status = data.get("analysis_status")
                cfg = data.get("config") or {}
                delta = cfg.get("max_time_delta_seconds")
                rel = cfg.get("value_tolerance_relative")
                criteria = (
                    f" (matching criteria: time within {delta:g}s, value "
                    f"within {rel:.0%})" if delta is not None and rel is not None
                    else "")
                if status == "MATCH_FOUND":
                    candidates = data.get("candidates") or []
                    n = len(candidates)
                    prose.append(
                        f"The analysis found {n} candidate cross-account "
                        "opposite-trade relationship"
                        + ("s" if n != 1 else "")
                        + f" in the analyzed evidence{criteria}."
                    )
                    for c in candidates:
                        ids = ", ".join(
                            c.get("transaction_ids_a", [])
                            + c.get("transaction_ids_b", []))
                        prose.append(
                            f"- Candidate {c.get('candidate_id')} "
                            f"({c.get('match_type')}): "
                            f"{c.get('account_a')} {c.get('side_a')} vs "
                            f"{c.get('account_b')} {c.get('side_b')} — "
                            f"{c.get('symbol')}; max gap "
                            f"{c.get('max_time_gap_seconds'):g}s; value "
                            f"{c.get('value_a'):,.2f} vs "
                            f"{c.get('value_b'):,.2f} (difference "
                            f"{c.get('value_difference'):,.2f}, "
                            f"{c.get('value_difference_relative'):.1%}); "
                            f"transactions: {ids}"
                        )
                    prose.append(
                        "These are candidate relationships: quantitative "
                        "matches under the configured analytical "
                        "tolerances — leads for analyst judgment, not "
                        "conclusions about intent."
                    )
                elif status == "NO_MATCH":
                    prose.append(
                        "No candidate cross-account opposite-trade "
                        "relationships matched within the configured "
                        f"tolerances{criteria} in the analyzed evidence. "
                        "This applies to the analyzed accounts and data "
                        "window only."
                    )
                else:   # INSUFFICIENT_DATA
                    prose.append(
                        "The cross-account trade analysis could not reach "
                        "a reliable conclusion: the available transaction "
                        "evidence is incomplete or insufficient "
                        "(retrieval was bounded or empty). This is not a "
                        "no-match result."
                    )
            elif data.get("signal_type") == "Rule" and "rule" in data:
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
                # 1) detection mechanism (deterministic, from the payload)
                prose.append(detection_mechanism_sentence(data))
                # 2) confirmed fact: the model score
                ex = data["explanation"]
                if "ml_score" in ex:
                    prose.append(
                        f"The model score is {ex['ml_score']}/100 "
                        f"({ex.get('score_interpretation', 'a system signal')})."
                    )
            elif data.get("signal_type") == "Feature" \
                    and "explanation" in data:
                # AL-2.9: a feature-detected finding (no rule/ML/graph
                # backing). The mechanism sentence names the detection
                # class; the RP risk-feature facts ARE the grounded "why".
                prose.append(detection_mechanism_sentence(data))
                feats = (data.get("explanation") or {}).get("features") or []
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
                    shown = ", ".join(str(a) for a in related[:5])
                    more = (f" (and {len(related) - 5} more)"
                            if len(related) > 5 else "")
                    prose.append(
                        f"Related accounts in the cluster: {shown}{more}.")
            elif data.get("signal_type") == "Rule" \
                    and "explanation" in data:
                # rule requested but NO backing rule exists in RP data —
                # still identify the detection mechanism deterministically
                prose.append(detection_mechanism_sentence(data))
            if data.get("evidence_missing") and not data.get("records") \
                    and data.get("signal_type") in ("ML", "Rule", "Graph") \
                    and prose:
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
                    lines.append(
                        "Additional data needed: " + "; ".join(result.next_data_needed)
                        + "."
                    )
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
                        "Details: " + " — ".join(parts) + "."))
            if data.get("signal_type") == "ML" and "explanation" in data \
                    and not any(
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
                        "No directly relevant policy references are "
                        "available for this finding."
                    )
                elif data.get("evidence_missing") and has_finding_basis:
                    # (the no-basis case already states the gap in its lead
                    # sentence — no duplicate footnote needed)
                    lines.append(
                        "The Risk Platform has not attached any finding-"
                        "level policy citation to this finding; the "
                        "references above are case-level."
                    )
            # Artifact payload: creation confirmation + where to find it
            # (§9 — the user is never assumed to know the UI layout).
            if isinstance(data.get("artifact"), dict):
                a = data["artifact"]
                lines.append(
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
                    "The Risk Platform confirms this finding, but holds no "
                    "concrete records for its evidence streams — only "
                    "aggregate feature values exist. Complete concrete "
                    "evidence is therefore unavailable."
                )
            elif empty_data.get("view") == "self_opposite_trades":
                prose.append(
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
                    "No directly applicable policy was found for this "
                    "finding."
                )
            else:
                if empty_data.get("scope") in ("case", "finding"):
                    lines.append(
                        "There is nothing to bundle yet: no investigation "
                        "results are available in this investigation. Ask "
                        "about the case or a finding first, then generate "
                        "the bundle."
                    )
                else:
                    lines.append(
                        "The query was valid, but the Risk Platform holds "
                        "no matching data for it."
                    )
            for warning in result.warnings or []:
                if warning.startswith("No content-contributing"):
                    continue        # implied by the empty result set
                if warning not in lines:
                    lines.append(f"Note: {warning}")
        elif result.outcome == ToolResultOutcome.INTEGRATION_ERROR:
            msg = result.error.message if result.error else "upstream failure"
            lines.append(
                f"The Risk Platform could not be reached or failed "
                f"(integration error: {msg}). This is not an empty result — "
                "availability should be retried later."
            )
        elif result.outcome == ToolResultOutcome.UNSUPPORTED:
            msg = result.error.message if result.error else "unsupported here"
            lines.append(
                f"This investigation action is not supported for the current "
                f"target: {msg}"
            )
        elif result.outcome == ToolResultOutcome.VALIDATION_ERROR:
            msg = result.error.message if result.error else "invalid input"
            lines.append(f"The request could not be executed: {msg}")

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

    return "The investigation completed without structured output."


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
                    goal = (self.goal_resolver(
                        user_request, None, resolved_ctx.case_id)
                        if self.goal_resolver else None)
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
                goal = self.goal_resolver(
                    user_request, resolved_ctx.focused_finding_id,
                    resolved_ctx.case_id)
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
        )

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
            ))

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

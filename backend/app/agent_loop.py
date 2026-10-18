"""Bounded Investigation Agent Loop V1.

Implements the contract in
docs/baselines/2026-09-19-bounded-agent-loop-v1.md: a persisted,
system-bounded execute → observe → decide loop over the EXISTING V2
runtime (ContextResolver → PlannerV2 → ExecutorV2 → domain tools), with
exactly four decisions (CONTINUE / RE_PLAN / STOP / BLOCK), bounded
retry, a hard iteration budget, and structured telemetry.

Non-negotiables (baseline):

- ONE investigation step per iteration, through the normal tool
  boundary — never a whole plan in one opaque call.
- The LLM PROPOSES decisions (bounded: one of four + a user-safe
  reason); the deterministic LoopPolicy DISPOSES — bounds, retry caps,
  completion criteria, and terminal rules are system-enforced and
  cannot be overridden by the model.
- A ToolResult NEVER marks the task complete: TASK-LEVEL COMPLETION is
  evaluated against recorded completion criteria. STEP-LEVEL evidence
  sufficiency (an observation usable for the next decision) is a
  different concept and never collapses into completion.
- Tool-internal data ≠ LLM observation: `project_observation` reduces
  every ToolResult to a compact, decision-relevant record (statuses,
  counts, capped id lists, completeness, errors) — raw record arrays
  and large evidence dumps never reach the proposer/planner context.
- Agent State persists after every meaningful transition into the
  EXISTING TaskStoreV2 (`agent_states` table) — resume-ready, with
  resume execution deliberately deferred.
- `STOPPED_BY_LIMIT` (and `BLOCKED`) are terminal states distinct from
  success everywhere — state, task container, and user-facing response.
- No chain-of-thought: only user-safe summaries/reasons are stored or
  emitted.
"""

import hashlib
import json
import logging
import os
import re
import uuid
from enum import Enum
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from app.context_resolution import ResolutionStatus
from app.executor_v2 import ExecutorV2, ExecutorError
from app.goal_resolution import (
    ClaimKind,
    GoalContract,
    GoalRepresentation,
    OutputSatisfaction,
)
from app.models import (
    Finding,
    FocusSource,
    InvestigationContext,
    Plan,
    PlanStep,
    PlanStatus,
    PlanStepStatus,
    TaskStatusV2,
    TaskV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)
from app.skills import (
    EVIDENCE_TYPES,
    EvidenceType,
    SKILLS,
    STEP_TOOL_MAP,
    eligible_skills_for_finding,
)
from app.goal_postconditions import derive_goal_outcome
from app import telemetry as _telemetry
from app import target_validity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

class AgentStatus(str, Enum):
    RUNNING = "running"
    # COMPLETED = loop EXECUTION terminal status ONLY: the loop stopped
    # normally. It says nothing about GSP semantic goal completion — that
    # is per-output `RequiredOutput.satisfaction` (GSP-4) and the derived
    # `GoalOutcome` (GSP-5, `derive_goal_outcome`). A run may terminate
    # COMPLETED while the semantic outcome is UNANSWERABLE (e.g. an
    # assessment output reaching UNRESOLVED/judgment_not_adjudicable).
    # NEVER derive GoalOutcome.COMPLETE from agent_status alone.
    COMPLETED = "completed"
    STOPPED_BY_LIMIT = "stopped_by_limit"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED_RESUMABLE = "interrupted_resumable"   # resume: deferred (§12)


class StopReason:
    # GOAL_SATISFIED = the LOOP's stop reason: the loop stopped via its
    # stop policy with the goal's criteria met. With an accepted contract
    # it is written ONLY when the WHOLE output set is SATISFIED (U00047
    # state fix) — the Activity chip renders stop_reason verbatim, so a
    # partial outcome must never carry it; partial runs end with
    # BOUNDED_DATA. It remains distinct from GSP `GoalOutcome.COMPLETE`:
    # semantic completion lives in per-output satisfaction +
    # derive_goal_outcome (GSP-5).
    GOAL_SATISFIED = "goal_satisfied"
    NO_VIABLE_ACTION = "no_viable_action"
    CONCLUSIVE_EMPTY_RESULT = "conclusive_empty_result"
    ITERATION_BOUND_REACHED = "iteration_bound_reached"
    INVALID_DECISION_PROPOSALS = "invalid_decision_proposals"
    PLANNING_FAILURE = "planning_failure"
    REPLAN_FAILURE = "replan_failure"
    CAPABILITY_MISSING = "capability_missing"
    DATA_UNAVAILABLE = "data_unavailable"
    PERSISTENT_ERROR = "persistent_error"
    CONTEXT_UNRESOLVED = "context_unresolved"
    CLARIFICATION_NEEDED = "clarification_needed"
    # AL-2.12: the run answered WITHIN the available data — part of the
    # requested scope is genuinely unavailable (e.g. a record-level
    # enumeration that the Risk Platform does not provide for a graph
    # finding). Distinct from GOAL_SATISFIED (scope fully satisfied) and
    # from CONCLUSIVE_EMPTY_RESULT (supported query, complete scope, zero
    # records): neither label is honest for the partial case.
    BOUNDED_DATA = "bounded_data"
    # AL-2.8: the M2 semantic foundation (SemanticNormalizer) could not run
    # after deterministic resolution failed — a dependency outage, distinct
    # from CONTEXT_UNRESOLVED ("no target matches"). Never faked, never
    # routed into finding-level tools.
    SEMANTIC_RESOLUTION_UNAVAILABLE = "semantic_resolution_unavailable"
    # distinct dependency state (QA-04): an authoritative dependency is
    # unavailable BEFORE execution can meaningfully start — never rendered
    # as completion, distinct from retryable tool failures
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


class LoopDecision(str, Enum):
    CONTINUE = "CONTINUE"
    RE_PLAN = "RE_PLAN"
    STOP = "STOP"
    BLOCK = "BLOCK"


DECISIONS = tuple(d.value for d in LoopDecision)

# Criteria kinds (baseline §3; V1 structure — ALQ-2 implementation decision):
#   question_answered  : an authoritative success observation answers the goal
#   artifact_produced  : the loop produced the requested artifact
CRITERIA_QUESTION_ANSWERED = "question_answered"
CRITERIA_ARTIFACT_PRODUCED = "artifact_produced"

# Retryable integration-failure codes (baseline §8: transient only).
RETRYABLE_ERROR_CODES = {"RISK_PLATFORM_UNAVAILABLE", "MCP_TRANSPORT_ERROR"}

# Bounded observation/state growth.
_MAX_OBSERVATIONS_KEPT = 12
_MAX_IDS = 10
_MAX_REF_IDS = 10
_MAX_WARNINGS = 5
_MAX_SUMMARY_ITEMS = 10
_MAX_ACTIVITY_KEPT = 200   # Activity UI renders the latest window (§13)

# Terminal agent statuses (polling stop condition, Agent Activity §4).
TERMINAL_AGENT_STATUSES = {
    AgentStatus.COMPLETED.value,
    AgentStatus.STOPPED_BY_LIMIT.value,
    AgentStatus.BLOCKED.value,
    AgentStatus.FAILED.value,
}


# ---------------------------------------------------------------------------
# Observation projection — Tool-internal data ≠ LLM observation
# ---------------------------------------------------------------------------

def _ids_of(items: list[Any], key: str) -> list[str]:
    out = []
    for item in items:
        v = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
        if isinstance(v, str) and v:
            out.append(v)
    return out[:_MAX_IDS]


def _population_reconciliation_summary(block: dict | None) -> dict | None:
    """Cross-domain consistency: bounded projection of the evidence
    view's population reconciliation — per-metric entries (window
    counts, ratio recomputes, rule claims) + unmapped governed metrics."""
    if not isinstance(block, dict):
        return None
    metrics = []
    for m in block.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        keep = {k: m.get(k) for k in
                ("type", "metric", "claim", "claimed", "observed",
                 "observed_in_window", "exposed_total", "outside_window",
                 "denominator", "verdict")}
        metrics.append({k: v for k, v in keep.items() if v is not None})
    return {
        "reference_time": block.get("reference_time"),
        "window_start": block.get("window_start"),
        "metrics": metrics,
        "unmapped_metrics": list(block.get("unmapped_metrics") or []),
        "verdict": block.get("verdict"),
    }


def _new_address_status_summary(block: dict | None) -> dict | None:
    """Cross-domain consistency: bounded projection of the per-record
    is_new_address status (facts only — RP's field is the novelty
    authority; destination distinctness is never used to infer it)."""
    if not isinstance(block, dict):
        return None
    return {
        "basis": block.get("basis"),
        "records_total": block.get("records_total"),
        "true_count": block.get("true_count"),
        "false_count": block.get("false_count"),
        "missing_count": block.get("missing_count"),
        "records": [
            {"record_id": r.get("record_id"),
             "is_new_address": r.get("is_new_address")}
            for r in (block.get("records") or [])
        ],
    }


def _render_new_address_status(output, summary: dict,
                               lang: str = "zh") -> list[str]:
    """The withdrawal_new_address_status facet's user-visible section:
    per-record is_new_address status + the overall conclusion, bounded
    by the evaluated population."""
    nas = summary.get("new_address_status") or {}
    records = nas.get("records") or []
    if not records:
        return []
    total = nas.get("records_total")
    true_n = nas.get("true_count") or 0
    false_n = nas.get("false_count") or 0
    missing_n = nas.get("missing_count") or 0
    en = lang == "en"
    if en:
        lines = [f"Per-record new-address status (Risk Platform "
                 f"authoritative field is_new_address, {total} records):"]
    else:
        lines = [f"逐笔新地址标记（Risk Platform 权威字段 "
                 f"is_new_address，共 {total} 笔）:"]
    for r in records:
        flag = ((("new address" if r.get("is_new_address") is True
                  else "not a new address")
                 if r.get("is_new_address") in (True, False)
                 else "flag missing; cannot verify") if en else
                ("是新地址" if r.get("is_new_address") is True
                 else "不是新地址" if r.get("is_new_address") is False
                 else "标记缺失，无法核实"))
        lines.append(f"- {r.get('record_id')}: {flag}")
    if missing_n:
        lines.append(
            f"{missing_n} of the records have a missing new-address flag "
            "and cannot be verified." if en else
            f"其中 {missing_n} 笔的新地址标记缺失，无法核实。")
    if missing_n == 0:
        if false_n == 0:
            lines.append(
                f"All {total} withdrawals are marked as new addresses "
                f"({true_n}/{total})." if en else
                f"全部 {total} 笔提现均被标记为新地址"
                f"（{true_n}/{total}）。")
        else:
            lines.append(
                f"{false_n}/{total} withdrawals are NOT marked as new "
                "addresses." if en else
                f"{false_n}/{total} 笔提现未被标记为新地址。")
    return lines


def _render_population_reconciliation(summary: dict) -> list[str]:
    """User-visible reconciliation lines: per governed metric/rule claim,
    the scoped window or recomputation is shown alongside the exposed
    population — both numbers verifiable; mismatches are explicit and
    never presented as consistent."""
    pr = summary.get("population_reconciliation") or {}
    metrics = pr.get("metrics") or []
    if not metrics and not (pr.get("unmapped_metrics") or []):
        return []
    ref = str(pr.get("reference_time") or "")
    lines: list[str] = []
    for m in metrics:
        if m.get("type") == "window_count":
            noun = "提现" if "withdrawal" in str(m.get("metric")) else "交易"
            if m.get("verdict") == "population_mismatch":
                lines.append(
                    f"⚠ 总体口径不一致：摘要指标 {m.get('claimed')} 笔{noun}/24 小时"
                    f"与参考时点（{ref}）前 24 小时窗口重算的 "
                    f"{m.get('observed_in_window')} 笔不符（明细共 "
                    f"{m.get('exposed_total')} 笔）；该差异已记录，"
                    "不构成完整一致。")
            else:
                lines.append(
                    f"总体口径说明：摘要指标「{m.get('claimed')} 笔{noun}/"
                    f"24 小时」按特征参考时点 {ref} 前的 24 小时窗口计得"
                    f"（窗口内 {m.get('observed_in_window')} 笔）；下方明细"
                    f"为该账号全部 {m.get('exposed_total')} 笔（另有 "
                    f"{m.get('outside_window')} 笔在评分窗口之前）——两个数字"
                    "口径不同、均可追溯。")
        elif m.get("type") == "ratio_recompute":
            if m.get("verdict") == "claim_supported":
                lines.append(
                    f"口径核实：摘要比例 {m.get('claimed')} 由明细重算证实"
                    f"（{m.get('observed')}，分母为全部 {m.get('denominator')} "
                    "笔交易）。")
            else:
                lines.append(
                    f"⚠ 比例口径不一致：摘要比例 {m.get('claimed')} 与明细重算 "
                    f"{m.get('observed')} 不符（分母 {m.get('denominator')} 笔）；"
                    "该差异已记录，不构成完整一致。")
        elif m.get("type") == "rule_claim":
            if m.get("verdict") == "claim_supported":
                lines.append("规则主张核实：首笔提现即为新地址，与明细一致。")
            else:
                lines.append("⚠ 规则主张与明细不符：首笔提现并非新地址；"
                             "该差异已记录，不构成完整一致。")
    unmapped = pr.get("unmapped_metrics") or []
    if unmapped:
        lines.append(f"说明：以下特征指标暂无可复核的明细口径映射："
                     f"{', '.join(unmapped)}；其数值来自 Risk Platform 特征"
                     "计算，尚未与明细逐笔核对。")
    return lines


def _destination_identity_summary(block: dict | None) -> dict | None:
    """WP-GS-B: bounded observation projection of the evidence view's
    destination-identity comparison facts (counts + group provenance).
    WP-GS-D: groups also carry the recorded destination value — design
    §8 keeps equality verifiable by the user (the 目的地 column and the
    facet provenance render it); the value is the trimmed recorded
    identifier GS-B grouped by, never an on-chain claim."""
    if not isinstance(block, dict):
        return None
    return {
        "basis": block.get("basis"),
        "records_total": block.get("records_total"),
        "records_with_identity": block.get("records_with_identity"),
        "records_without_identity": block.get("records_without_identity"),
        "unique_destinations": block.get("unique_destinations"),
        "groups": [
            {"destination_ref": g.get("destination_ref"),
             "address": g.get("address"),
             "record_ids": list(g.get("record_ids") or [])}
            for g in (block.get("groups") or [])
        ],
        "normalization": block.get("normalization"),
    }


def project_observation(
    tool_name: str,
    step_type: str,
    result: ToolResult | None,
    error: ExecutorError | None = None,
) -> dict[str, Any]:
    """Deterministic compact projection of one ToolResult for the
    decision/planner context (baseline §13 — GLOBAL rule). Kind-specific
    whitelists; the generic fallback carries NO payload body at all. Raw
    record arrays never pass through; id lists are capped."""
    obs: dict[str, Any] = {
        "tool_name": tool_name,
        "step_type": step_type,
        "outcome": result.outcome.value if result is not None
        else "execution_error",
        "semantic_status": None,
        "summary": {},
        "evidence_ref_count": len(result.evidence_refs) if result else 0,
        "evidence_refs": [
            {"kind": r.kind, "id": r.id}
            for r in (result.evidence_refs[:_MAX_REF_IDS] if result else [])
        ],
        "warnings": (result.warnings or [])[:_MAX_WARNINGS] if result else [],
        "error": None,
        "complete": None,
        "next_data_needed": result.next_data_needed if result else None,
    }
    if error is not None and result is None:
        obs["error"] = {"code": error.code,
                        "message": error.message[:200]}
    if result is not None and result.error is not None:
        obs["error"] = {"code": result.error.code,
                        "message": result.error.message[:200]}
    data = result.data if result is not None and isinstance(result.data, dict) \
        else {}
    if result is not None and not isinstance(result.data, dict) \
            and result.data is not None:
        obs["summary"]["payload"] = "non-dict payload (bounded out)"

    outcome = obs["outcome"]
    if outcome == ToolResultOutcome.SUCCESS.value:
        # --- per-kind compact facts (whitelists only) -----------------------
        if tool_name == "risk_case_fetch" and data.get("findings") is not None:
            findings = list(data.get("findings") or [])[:_MAX_SUMMARY_ITEMS]
            obs["summary"]["finding_count"] = len(data.get("findings") or [])
            obs["summary"]["findings"] = [
                {"id": f.finding_id if hasattr(f, "finding_id")
                 else f.get("finding_id"),
                 "title": (f.title if hasattr(f, "title")
                           else f.get("title", ""))[:80]}
                for f in findings
            ]
            # GSP WP-GSP-4: authoritative case-level risk facts (from the
            # tool's ext echo of RP risk_summary) — observation-level facts
            # the scalar conflict evaluator can compare across observations.
            ext = data.get("ext") or {}
            if ext.get("risk_level") is not None:
                obs["summary"]["risk_level"] = ext.get("risk_level")
            if ext.get("risk_score") is not None:
                obs["summary"]["risk_score"] = ext.get("risk_score")
        elif data.get("view") == "self_opposite_trades":
            obs["semantic_status"] = "self_opposite_trades"
            obs["summary"] = {
                "semantics": data.get("semantics"),
                "opposite_trade_ratio": data.get("opposite_trade_ratio"),
                "rule_name": (data.get("rule") or {}).get("name")
                if isinstance(data.get("rule"), dict) else None,
                "record_count": data.get("record_count"),
                "record_ids": _ids_of(data.get("records") or [],
                                      "record_id"),
            }
        elif data.get("view") == "evidence":
            obs["semantic_status"] = "evidence"
            records = data.get("records") or []
            # AL-2.12: structured rows for COMPLETE-scope rendering — the
            # composer renders every record it receives; the tool contract
            # guarantees the evidence view is the complete record set, so
            # the cap is a display bound that MUST be disclosed when hit.
            rows = [
                {"record_id": r.get("record_id"),
                 "timestamp": r.get("timestamp"),
                 "summary": r.get("summary")}
                for r in records[:20] if isinstance(r, dict)
            ]
            obs["summary"] = {
                "record_count": data.get("record_count"),
                "record_ids": _ids_of(records, "record_id"),
                "streams": data.get("streams"),
                "record_rows": rows,
                # WP-GS-B: bounded destination-identity comparison facts
                # (deterministic; counts + group provenance; raw values
                # stay in the tool-result records / evidence table).
                "destination_identity": _destination_identity_summary(
                    data.get("destination_identity")),
                # Cross-domain consistency: bounded summary-metric vs
                # exposed-population reconciliation (frequency metrics
                # under RP's reference anchor).
                "population_reconciliation": _population_reconciliation_summary(
                    data.get("population_reconciliation")),
                # Cross-domain consistency: authoritative per-record
                # is_new_address status (bounded projection).
                "new_address_status": _new_address_status_summary(
                    data.get("new_address_status")),
                # Cross-domain consistency: actually-triggered rule
                # evidence (bounded) — detection-decision provenance for
                # rule-backed explanations.
                "rule_evidence": [
                    {"rule_name": r.get("rule_name"),
                     "severity": r.get("severity"),
                     "description": r.get("description"),
                     "trigger": r.get("trigger"),
                     "threshold": r.get("threshold"),
                     "contribution": r.get("contribution")}
                    for r in (data.get("rule_evidence") or [])[:8]],
                "rows_truncated": len(rows) < len(records),
                # AL-2.8: a few bounded record summaries so the response
                # composer can state WHAT the evidence actually is
                "record_summaries": [
                    r.get("summary") for r in records[:3]
                    if isinstance(r, dict) and r.get("summary")
                ],
            }
        elif data.get("view") == "timeline":
            events = data.get("events") or []
            obs["semantic_status"] = "timeline"
            obs["summary"] = {
                "total_events": data.get("total_events", len(events)),
                "event_ids": _ids_of(events, "event_id"),
                "truncated": data.get("truncated", False),
            }
        elif data.get("analysis") == "cross_account_trade_analysis":
            # The tool's own observation contract is already compact
            # (bounded candidate records); carried as-is. accounts_analyzed
            # is the honest "what scope was checked" answer (AL-2.6).
            # Case-4 correction: the payload is target-anchored — every
            # candidate contains the target account; the per-stream
            # retrieval echoes + the authoritative same-account metric
            # keep scope, coverage and the §8 self-vs-cross distinction
            # available to the answer composer.
            obs["semantic_status"] = data.get("analysis_status")
            cfg = data.get("config") or {}
            retrieval = data.get("retrieval") or {}
            obs["summary"] = {
                "candidates_found": data.get("candidates_found"),
                "candidates_emitted": data.get("candidates_emitted"),
                "candidates": data.get("candidates") or [],
                "totals": data.get("totals"),
                "retrieval": retrieval,
                "data_quality": data.get("data_quality"),
                "accounts_analyzed": data.get("accounts_analyzed") or [],
                # analysis scope: target_account (Case-4) vs
                # related_account_set (set-internal pairs)
                "analysis_scope": data.get("analysis_scope"),
                # Case-4 correction: target anchoring + coverage echo
                "target_account": data.get("target_account"),
                "target_retrieval": retrieval.get("target"),
                "counterparty_retrieval": retrieval.get("counterparty"),
                # authoritative same-account metric (§8 boundary fact)
                "target_account_metrics":
                    data.get("target_account_metrics"),
                # AL-2.12: the matching criteria belong to the user answer
                "max_time_delta_seconds": cfg.get("max_time_delta_seconds"),
                "value_tolerance_relative":
                    cfg.get("value_tolerance_relative"),
                # the absolute dust floor is the OTHER half of the matching
                # criteria — near-zero-notional pairs are exempted from the
                # relative tolerance by it, and candidates admitted only by
                # it must be labeled as such in the answer
                "value_tolerance_absolute_floor":
                    cfg.get("value_tolerance_absolute_floor"),
            }
        elif data.get("analysis") == "related_account_analysis":
            # AL-2.10/2.14: authoritative RP network attribution; the
            # related-account list IS the primary answer payload — carried
            # without the generic 10-id cap (the tool provides the
            # complete detail set).
            obs["semantic_status"] = data.get("analysis_status")
            cluster = data.get("cluster") or {}
            obs["summary"] = {
                "related_accounts_count": data.get("related_accounts_count"),
                "related_accounts": (data.get("related_accounts") or [])
                [:25],
                "shared_devices": (data.get("shared_devices") or [])[:5],
                "cluster_name": cluster.get("cluster_name"),
                "detection_type": cluster.get("detection_type"),
                # AL-2.13: cluster facts feed the bounded "why" answer
                "member_count": cluster.get("member_count"),
                "cluster_risk_score": cluster.get("cluster_risk_score"),
                "role_in_cluster": cluster.get("role_in_cluster"),
                # GSP WP-GSP-4: the per-account shared-IP attributions
                # (WP-GSP-1b echo) are the deterministic facts the shared-
                # IP postcondition evaluates (completeness via
                # related_accounts_count vs list length).
                "shared_ip_attributions": data.get(
                    "shared_ip_attributions"),
            }
        elif data.get("signal_type") and ("rule" in data
                                          or "explanation" in data):
            obs["semantic_status"] = data.get("signal_type")
            rule = data.get("rule") or {}
            explanation = data.get("explanation") or {}
            graph = (explanation.get("cluster") or {}) \
                if isinstance(explanation, dict) else {}
            features = (explanation.get("available_feature_values") or {})
            obs["summary"] = {
                # the finding this explanation explains — the subject
                # binding the evaluator checks (target-vs-evidence)
                "finding_id": data.get("finding_id"),
                "rule_name": rule.get("name"),
                "threshold": rule.get("threshold"),
                # AL-2.12: the observed feature values ARE the "why" — the
                # composer states observed-vs-threshold semantics from them.
                "trigger_values": rule.get("trigger_values") or {},
                # real RP rule entries often carry ONLY a description — that
                # description IS the causal semantics for those cases
                "rule_description": (rule.get("description") or "")[:200],
                "contribution": rule.get("contribution"),
                "ml_score": explanation.get("ml_score"),
                # AL-2.13: authoritative ML semantics for a user-readable
                # explanation (method/score meaning/feature context and the
                # attribution boundary — never invented).
                "detection_method": explanation.get("detection_method"),
                "score_interpretation": explanation.get(
                    "score_interpretation"),
                "attribution_available": explanation.get(
                    "attribution_available"),
                "available_feature_values": dict(
                    list(features.items())[:12]),
                # AL-2.14: Feature-type explanation fields (F7-class)
                "feature_facts": explanation.get("features") or [],
                # RP's authoritative risk-factor entry for this finding
                # (exact factor_name==finding-title merge): observed value,
                # severity, and the platform's own threshold/trigger-state
                # description — echoed verbatim, never re-derived.
                "risk_factor": explanation.get("risk_factor") or None,
                # the platform's own complete statement about this risk
                # finding (may carry the rule/threshold/trigger-state
                # semantics) — verbatim echo, bounded
                "finding_summary":
                    (explanation.get("finding_summary") or "")[:400] or None,
                # AL-2.12: graph facts belong to the answer too (F5-class)
                "cluster_name": graph.get("cluster_name"),
                "detection_type": graph.get("detection_type"),
                "member_count": graph.get("member_count"),
                "member_count_includes_case":
                    graph.get("member_count_includes_case"),
                "cluster_risk_score": graph.get("cluster_risk_score"),
                # list-completeness metadata: the related_accounts field
                # is an RP server-side preview, never the full membership
                "related_accounts_preview":
                    graph.get("related_accounts_preview"),
                "related_accounts_total":
                    graph.get("related_accounts_total"),
                "shared_devices": (explanation.get("shared_devices")
                                   or [])[:5],
                "related_accounts": (explanation.get("related_accounts")
                                     or [])[:_MAX_IDS],
                "evidence_missing": data.get("evidence_missing"),
            }
        elif isinstance(data.get("matches"), list):
            matches = data["matches"]
            # AL-2.13: association contract on the loop surface — only
            # associated_policy_refs are finding-level policy support;
            # other matches are investigation-wide and must not be
            # rendered as if they explain the finding.
            assoc_ids = {p.get("citation_id") for p in
                         (data.get("associated_policy_refs") or [])}
            associated = [m for m in matches
                          if m.get("citation_id") in assoc_ids]
            obs["semantic_status"] = "policy_matches"
            obs["summary"] = {
                "match_count": len(matches),
                "associated_count": len(associated),
                "associated_citations": [
                    {"citation_id": m.get("citation_id"),
                     "document": m.get("document"),
                     "section": m.get("section")}
                    for m in associated[:5]
                ],
                "finding_policy_status": data.get("finding_policy_status"),
            }
        elif isinstance(data.get("artifact"), dict):
            artifact = data["artifact"]
            obs["semantic_status"] = "artifact"
            obs["summary"] = {"artifact_id": artifact.get("artifact_id"),
                              "title": artifact.get("title"),
                              "scope": artifact.get("scope")}
        else:
            # Known-success shape with no specific whitelist: a bare shape
            # marker only — never the payload body, never even key names.
            obs["summary"]["payload_kind"] = "unrecognized"
            obs["summary"]["key_count"] = len(data)

        # cross-cutting bounded facts
        if data.get("evidence_missing"):
            obs["summary"]["evidence_missing"] = True
            obs["next_data_needed"] = result.next_data_needed or []
        if "complete" in data:
            obs["complete"] = data.get("complete")
        if "truncated" in data:
            obs["complete"] = not bool(data.get("truncated"))

        # user-facing phrase (QA-05): separate from the machine digest —
        # this is what the Activity timeline renders
        obs["display"] = _user_phrase(tool_name, obs)
    elif outcome == ToolResultOutcome.EMPTY.value:
        obs["summary"]["empty"] = True
    return obs


def _user_phrase(tool_name: str, obs: dict[str, Any]) -> str:
    """Analyst-facing one-line result phrase (QA-05) — product language,
    never the machine digest. Factual scope only; no overclaiming."""
    outcome = obs.get("outcome")
    s = obs.get("summary") or {}
    if outcome == ToolResultOutcome.INTEGRATION_ERROR.value:
        return "The Risk Platform could not be reached for this step."
    if outcome == ToolResultOutcome.EMPTY.value:
        return "No matching data was found for this step."
    if outcome != ToolResultOutcome.SUCCESS.value:
        return f"Step finished with outcome: {outcome}."
    status = obs.get("semantic_status")
    if tool_name == "risk_case_fetch" and s.get("finding_count") is not None:
        n = s["finding_count"]
        return (f"Case retrieved: {n} finding"
                f"{'s' if n != 1 else ''} identified.")
    if status == "MATCH_FOUND":
        n = s.get("candidates_found")
        target = s.get("target_account")
        scope_txt = f" involving account {target}" if target else ""
        return (f"Found {n} candidate cross-account opposite-trade "
                "relationship"
                f"{'s' if n != 1 else ''}{scope_txt} "
                "(candidates only, not conclusions).")
    if status == "RELATED_ACCOUNTS_FOUND":
        n = s.get("related_accounts_count")
        return (f"Risk Platform network attribution: {n} related account"
                f"{'s' if n != 1 else ''} for this account.")
    if status == "NO_NETWORK_EVIDENCE":
        return ("The Risk Platform provides no network attribution for "
                "this case.")
    if status == "NO_MATCH":
        target = s.get("target_account")
        scope_txt = f" involving account {target}" if target else ""
        return ("No candidate cross-account opposite-trade relationships "
                f"{scope_txt + ' ' if scope_txt else ''}matched within the "
                "configured tolerances for the analyzed scope.")
    if status == "self_opposite_trades":
        ratio = s.get("opposite_trade_ratio")
        if ratio is None:
            return ("Opposite-side trading activity data is not available "
                    "for this account.")
        if ratio == 0:
            return ("No opposite-side trading activity observed for this "
                    "account (ratio 0%).")
        return ("Opposite-side trading activity by this same account "
                f"(opposite-trade ratio {ratio:.1%}); same-account only.")
    if status == "timeline":
        n = s.get("total_events")
        return f"Timeline reviewed: {n} events."
    if status == "evidence":
        n = s.get("record_count")
        return f"Complete record set reviewed: {n} records."
    if status == "policy_matches":
        return f"Policy review: {s.get('match_count', 0)} reference(s) found."
    if s.get("artifact_id"):
        return f"Investigation bundle created ({s['artifact_id']})."
    if s.get("evidence_missing"):
        return "The available evidence was insufficient for this step."
    return "Step completed successfully."


def _observation_line(obs: dict[str, Any]) -> str:
    """One-line user-safe digest of an observation (telemetry/response)."""
    bits = [f"{obs['tool_name']}:{obs['outcome']}"]
    if obs.get("semantic_status"):
        bits.append(str(obs["semantic_status"]))
    summary = obs.get("summary") or {}
    for key in ("finding_count", "total_events", "record_count",
                "match_count", "candidates_found"):
        if summary.get(key) is not None:
            bits.append(f"{key}={summary[key]}")
    if summary.get("opposite_trade_ratio") is not None:
        bits.append(f"ratio={summary['opposite_trade_ratio']}")
    if summary.get("artifact_id"):
        bits.append(f"artifact={summary['artifact_id']}")
    if obs.get("error"):
        bits.append(f"error={obs['error'].get('code')}")
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Agent State (persisted; baseline §5)
# ---------------------------------------------------------------------------

class AgentLoopState(BaseModel):
    """Resume-ready persisted loop state. Loop semantics live HERE — never
    collapsed into TaskStatusV2 (the task container keeps its own coarse
    lifecycle)."""

    task_id: str
    investigation_id: str
    case_id: str
    goal: str                                   # user-safe goal text
    criteria_kind: str = CRITERIA_QUESTION_ANSWERED
    focused_finding_id: str | None = None
    focus_source: str | None = None
    # AL-3.5.1 target-validity evidence kind of the adopted focus
    # (system-side classification from the resolution outcome; persisted
    # so the resume path re-admits with full evidence instead of guessing).
    target_evidence_kind: str | None = None
    # Per-turn response language ("zh"/"en"); None = undetermined →
    # the composer keeps its existing default face. Detected from the
    # CURRENT request at run start; persisted so resume/re-plan render
    # the same turn in the same language.
    response_language: str | None = None
    # Q2 closure: goal-understanding diagnostics (WP-CG-3 dict) persisted
    # so the composer can disclose the ACTUAL failure class of a degraded
    # (heuristic-fallback) contract instead of a generic claim.
    goal_understanding_diagnostics: dict | None = None
    # GSP WP-GSP-2: the ACCEPTED Goal Semantic Contract for this run
    # (deterministic validation of the goal-understanding proposal; None
    # when the flag is off, the proposal was rejected, or the state is
    # legacy). Content immutable after acceptance; downstream GSP stages
    # (observation/postcondition/composer, WP-GSP-3..5) consume it from
    # here. No consumer writes satisfaction in this work package.
    # WP-CG-3: structured provenance of WHY a contract exists or not —
    # {"outcome": "contract_accepted" | "no_proposal" |
    #  "invalid_proposal:<code>" | "llm_empty" | "heuristic_single_goal"
    #  | "ambiguous_not_executed", "reason": str|None}. Written only
    # under ENABLE_GOAL_SEMANTIC_CONTRACT; additive/optional so legacy
    # documents load unchanged.
    goal_contract_decision: dict | None = None
    goal_contract: GoalContract | None = None
    # GSP WP-GSP-5: deterministic overall goal outcome, derived at
    # composition time from the per-output satisfaction states (never
    # LLM-set; never inferred from a single output).
    goal_outcome: str | None = None
    # GSP WP-GSP-6: per-output planning coverage records (deterministic
    # admission over the adopted plan) + the authoritative step→output
    # mapping used by observation association. Persisted for audit and
    # future RE_PLAN reuse (GSP-7 interface).
    planning_coverage: list[dict[str, Any]] = Field(default_factory=list)
    step_output_map: dict[str, list[str]] = Field(default_factory=dict)
    # RP-authoritative identity of the focused finding (bounded): lets the
    # response composer speak about the finding in natural language
    # (AL-2.8) without exposing raw payloads.
    focused_finding_title: str | None = None
    focused_finding_summary: str | None = None
    plan_id: str | None = None
    plan_revisions: int = 0
    plan_steps: list[dict[str, Any]] = Field(default_factory=list)
    # plan_steps entries: {step_id, type, status, tool_name}
    completed_step_ids: list[str] = Field(default_factory=list)
    current_step: dict[str, Any] | None = None
    observations: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    iteration: int = 0
    agent_status: str = AgentStatus.RUNNING.value
    stop_reason: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    retries: dict[str, int] = Field(default_factory=dict)
    # AL-2: per-executed-step argument provenance trajectory
    # [{step_id, tool_name, arguments, sources}] — observational only.
    arg_provenance: list[dict] = Field(default_factory=list)
    invalid_proposals: int = 0
    response: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Bounded, UI-safe activity record (Agent Activity baseline §15): the
    # loop mirrors each structured event here at the same persistence
    # points. Sequence-ordered; capped. These are USER-SAFE summaries —
    # never chain-of-thought, never raw tool payloads.
    activity: list[dict[str, Any]] = Field(default_factory=list)
    activity_seq: int = 0
    # Stage 2.1 structured Question / Goal Representation (open-ended
    # baseline §35b): semantic objective, distinct from target resolution
    # and from tool selection. Compact projection; None until produced.
    goal_representation: dict[str, Any] | None = None

    def pending_steps(self) -> list[dict[str, Any]]:
        return [s for s in self.plan_steps
                if s.get("status") == PlanStepStatus.PENDING.value]

    def last_observation(self) -> dict[str, Any] | None:
        return self.observations[-1] if self.observations else None

    def criteria_satisfied(self) -> bool:
        """TASK-LEVEL completion (baseline §3) — deterministic check against
        the recorded criteria kind. A ToolResult never sets this directly."""
        if self.criteria_kind == CRITERIA_ARTIFACT_PRODUCED:
            if not self.artifact_ids:
                return False
            # R2a: artifact existence alone does not answer a request
            # whose contract carries RECORDS-artifact deliverables — every
            # such deliverable must be content-verified satisfied first.
            if self.goal_contract is not None:
                for o in self.goal_contract.outputs:
                    if any(t.value in ("withdrawal_records",
                                       "transaction_records")
                           for t in (o.evidence_requirements or [])):
                        if o.satisfaction != OutputSatisfaction.SATISFIED:
                            return False
            return True
        # question_answered: an authoritative, non-gap success observation
        for obs in self.observations:
            if obs.get("outcome") != ToolResultOutcome.SUCCESS.value:
                continue
            if obs.get("error"):
                continue
            if (obs.get("summary") or {}).get("evidence_missing"):
                # AL-2.6: a source reporting a data gap does not by itself
                # answer the goal (keep investigating) — EXCEPT the produced
                # bundle: the artifact IS the run's product, its content
                # honestly carries the gap (visible in the trajectory and
                # the composed response), so a bounded plan whose terminal
                # product exists converges instead of re-planning past it.
                if obs.get("semantic_status") != "artifact":
                    continue
            return True
        return False

    def conclusive_empty(self) -> bool:
        """Authoritative zero for the analyzed scope (baseline §7): an
        answer of 'none found' on complete evidence — a legitimate
        stopping point when no alternative path exists."""
        last = self.last_observation()
        if not last:
            return False
        if last.get("outcome") == ToolResultOutcome.EMPTY.value:
            return True
        return last.get("semantic_status") == "NO_MATCH"


# ---------------------------------------------------------------------------
# Decision proposal (LLM proposes) + deterministic fallback
# ---------------------------------------------------------------------------

class DecisionProposal(BaseModel):
    decision: str
    reason: str = ""
    source: str = "llm"          # llm | heuristic | system
    valid: bool = True


class DecisionProposer(Protocol):
    def propose(self, state: AgentLoopState) -> DecisionProposal: ...


class HeuristicDecisionProposer:
    """Deterministic fallback proposer (used when no LLM is configured or
    the LLM proposal is unusable). Conservative, observation-class-based —
    the LoopPolicy still enforces everything."""

    @staticmethod
    def _pending_steps_cover_unsatisfied_outputs(state: AgentLoopState) -> bool:
        """R1 invariant: TRUE when at least one contract output is not yet
        satisfied AND a still-PENDING adopted plan step covers it in the
        admission-time step→output mapping (the SAME tracked mapping the
        LoopPolicy enforcement uses — never a guess). One successful
        observation does not answer a multi-deliverable question, so this
        state must not be converged to STOP by the fallback alone."""
        contract = state.goal_contract
        if contract is None:
            return False
        unsatisfied = {
            o.output_id for o in contract.outputs
            if o.satisfaction in (
                OutputSatisfaction.PENDING.value,
                OutputSatisfaction.PARTIALLY_SUPPORTED.value,
                OutputSatisfaction.UNRESOLVED.value)}
        if not unsatisfied:
            return False
        for s in state.plan_steps:
            if s.get("status") != PlanStepStatus.PENDING.value:
                continue
            if set((state.step_output_map or {}).get(
                    s.get("step_id"), [])) & unsatisfied:
                return True
        return False

    def propose(self, state: AgentLoopState) -> DecisionProposal:
        last = state.last_observation()
        has_pending = bool(state.pending_steps())
        outcome = (last or {}).get("outcome")
        reason = "deterministic fallback policy"
        if outcome in (ToolResultOutcome.INTEGRATION_ERROR.value,
                       ToolResultOutcome.VALIDATION_ERROR.value):
            decision = LoopDecision.BLOCK.value
        elif outcome == ToolResultOutcome.UNSUPPORTED.value:
            decision = LoopDecision.BLOCK.value
        elif state.criteria_satisfied() \
                and not self._pending_steps_cover_unsatisfied_outputs(state):
            # R1: task-level "any success" no longer ends a run while
            # unsatisfied deliverables still have planned, executable
            # steps. Termination is preserved — when nothing pending
            # covers unsatisfied outputs, the original STOP applies.
            decision = LoopDecision.STOP.value
        elif has_pending:
            decision = LoopDecision.CONTINUE.value
            if self._pending_steps_cover_unsatisfied_outputs(state):
                reason = ("deterministic fallback policy (unsatisfied "
                          "deliverables still have executable planned "
                          "steps)")
        elif state.criteria_satisfied():
            decision = LoopDecision.STOP.value
        elif state.conclusive_empty():
            decision = LoopDecision.STOP.value
        else:
            decision = LoopDecision.RE_PLAN.value
        return DecisionProposal(
            decision=decision,
            reason=reason,
            source="heuristic")


class LLMDecisionProposer:
    """Bounded LLM decision proposal (baseline §6/§11): the model sees the
    compact state digest only and returns one decision + a user-safe
    reason. Every proposal passes through LoopPolicy before action."""

    def __init__(self, llm_provider: Any | None = None):
        self._llm_override = llm_provider

    def propose(self, state: AgentLoopState) -> DecisionProposal:
        system = (
            "You are the decision component of a bounded investigation "
            "loop. Choose EXACTLY ONE decision:\n"
            '- "CONTINUE": the current plan remains valid; the next planned '
            "step should execute.\n"
            '- "RE_PLAN": the latest observation invalidates the remaining '
            "plan or opens a better direction; a new bounded plan is needed.\n"
            '- "STOP": the goal\'s completion criteria are satisfied, or the '
            "result is conclusive for the analyzed scope.\n"
            '- "BLOCK": a required capability/data/system condition prevents '
            "progress.\n"
            "Rules: a successful tool result does NOT by itself mean the "
            "goal is complete; insufficient data is not a no-match; do not "
            "stop while the goal clearly needs more evidence. Respond with "
            'raw JSON only: {"decision": "<one of the four>", "reason": '
            '"<one short user-safe sentence>"}'
        )
        last = state.last_observation()
        user = (
            f"Goal: {state.goal}\n"
            f"Completion criteria: {state.criteria_kind}\n"
            f"Iteration: {state.iteration} of "
            f"{_max_iterations()}\n"
            "Completed steps: "
            f"{[s.get('type') for s in state.plan_steps if s.get('status') == 'success']}\n"
            "Remaining planned steps: "
            f"{[s.get('type') for s in state.pending_steps()]}\n"
            "Latest observation (compact):\n"
            f"{_json_digest(last)}\n\n"
            'Produce {"decision": "...", "reason": "..."} now.'
        )
        try:
            llm = self._llm_override or _make_llm()
            # R0 (bounded structured-output retry): a NON-EMPTY but
            # unparseable JSON answer (captured live: "Expecting ':'
            # delimiter: line 1 column 71") is a different failure from an
            # empty response — the provider-level retry does not fire.
            # ONE regeneration for this identified failure only, with a
            # format-correction note that preserves the original decision
            # contract; every other exception (transport, timeout, rate
            # limit, auth) keeps its own classification and is never
            # retried here. Retry budget: malformed-JSON 1 retry (2
            # generate() calls); each generate() still carries the
            # provider-level empty-response retry — worst case 4 provider
            # calls, bounded.
            import json as _json
            base_messages = [{"role": "system", "content": system},
                             {"role": "user", "content": user}]
            payload = None
            parse_error = None
            for attempt in (1, 2):
                messages = base_messages
                if attempt == 2:
                    messages = [
                        base_messages[0],
                        dict(base_messages[1], content=(
                            base_messages[1]["content"]
                            + "\n\nYour previous response could not be "
                              "parsed as valid JSON. Respond again with "
                              'ONLY {"decision": "<one of the four>", '
                              '"reason": "..."} — complete and '
                              "well-formed.")),
                    ]
                # Thinking-style models emit reasoning before the JSON
                # text; a small budget truncates the answer mid-way (same
                # lesson as the planner's budget — captured live in
                # planner_v2).
                text = llm.generate(
                    messages=messages, max_tokens=2048, temperature=0.0)
                try:
                    payload = _json.loads(
                        _extract_json_object(text or ""))
                    parse_error = None
                    if attempt == 2:
                        logger.warning(
                            "Decision-proposer structured output recovered "
                            "on the bounded JSON retry")
                    break
                except _json.JSONDecodeError as e:
                    parse_error = e
                    logger.warning(
                        "Decision-proposer structured output unparseable "
                        "(attempt %d/2): %s", attempt, e)
            if parse_error is not None:
                logger.warning(
                    "Decision-proposer structured output unparseable "
                    "after the bounded retry; the deterministic fallback "
                    "policy owns the decision")
                return _invalid()
            decision = str(payload.get("decision", "")).strip().upper()
            reason = str(payload.get("reason", "")).strip()[:300]
            if decision not in DECISIONS:
                return _invalid()
            return DecisionProposal(decision=decision, reason=reason,
                                    source="llm")
        except Exception as e:                       # bounded: fall back
            logger.warning("LLM decision proposal failed: %s", e)
            return _invalid()


def _invalid() -> DecisionProposal:
    return DecisionProposal(decision="", reason="", source="llm", valid=False)


def _json_digest(obj: Any) -> str:
    import json as _json
    try:
        text = _json.dumps(obj, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return text[:1200]


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _make_llm():
    from app.llm_provider import ClaudeProvider
    return ClaudeProvider()


def _max_iterations() -> int:
    from app.config import settings
    return settings.AGENT_LOOP_MAX_ITERATIONS


def _max_retries() -> int:
    from app.config import settings
    return settings.AGENT_LOOP_MAX_RETRIES_PER_STEP


# ---------------------------------------------------------------------------
# Deterministic decision enforcement (baseline §6/§12)
# ---------------------------------------------------------------------------

class EnforcedDecision(BaseModel):
    decision: LoopDecision
    reason: str                      # user-safe
    coerced_from: str | None = None
    limit_reached: bool = False      # terminal semantics = STOPPED_BY_LIMIT


def _reanchor_focus_to_contract_subject(ctx, goal_contract, findings):
    """Contract-subject focus re-anchor (target-vs-evidence consistency).

    The ACCEPTED GoalContract is built from THIS request's goal
    understanding. When it names exactly one finding-level subject and
    the resolved focus is a DIFFERENT finding of the same case, the
    stale focus (e.g. a user_selected focus from an earlier turn in the
    same conversation) must not steer this turn's planning and execution
    to another finding's evidence: the contract's per-output obligations
    ARE this turn's resolution. Single-subject only — multi-target
    compound contracts carry explicit per-step anchors instead.

    Returns the re-anchored context, or None when no re-anchor applies
    (same subject / no finding subjects / multi-subject / target absent
    from the case / no focus).
    """
    if goal_contract is None or not findings:
        return None
    subjects = sorted({
        (o.subject_ref or "").strip()
        for o in goal_contract.outputs
        if re.match(r"^F\d+$", (o.subject_ref or "").strip())})
    if (len(subjects) != 1
            or not ctx.focused_finding_id
            or ctx.focused_finding_id == subjects[0]):
        return None
    if not any(f.finding_id == subjects[0] for f in findings):
        return None
    return ctx.model_copy(update={"focused_finding_id": subjects[0]})


def _step_matches_output_subject(step, output_id, plan_steps,
                                 goal_contract) -> bool:
    """True when the step may serve the output: no explicit step target →
    type-level match; an explicit finding_id target requires the output's
    subject_ref to BE that finding (deterministic decomposition pairing).
    The type∩target rule lives in planning_coverage.step_covers_output —
    the single source shared with the coverage supplement."""
    if goal_contract is None:
        return True
    for out in goal_contract.outputs:
        if out.output_id == output_id:
            from app.planning_coverage import step_covers_output
            return step_covers_output(step, out)
    return True


def _observation_fingerprint(obs: dict[str, Any]) -> str | None:
    """Compact identity of an observation's SEMANTIC result (tool +
    outcome + status + whitelisted summary). Two observations with the
    same fingerprint carry the same information — re-running that query
    yields no new progress (AL-2.13)."""
    try:
        import json as _json
        basis = _json.dumps(
            {"t": obs.get("tool_name"), "o": obs.get("outcome"),
             "s": obs.get("semantic_status"), "sum": obs.get("summary")},
            sort_keys=True, default=str)
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


class LoopPolicy:
    """System-side decision enforcement. The proposal is INPUT; this policy
    is authoritative. Minimal, explainable rules in fixed order."""

    @staticmethod
    def _already_answered(state: AgentLoopState, step: dict) -> bool:
        """U00047 fix A: TRUE when the SAME step id + tool with the SAME
        arguments already SUCCEEDED earlier in this run — a re-plan
        re-offer of answered work whose re-execution can only reproduce
        the recorded result (parameters are system-locked; a genuinely
        different query never matches on all three)."""
        sid = step.get("step_id")
        tool = step.get("tool_name")
        args = json.dumps(step.get("arguments") or {}, sort_keys=True,
                          default=str)
        args_seen = any(
            a.get("step_id") == sid and a.get("tool_name") == tool
            and json.dumps(a.get("arguments") or {}, sort_keys=True,
                           default=str) == args
            for a in state.arg_provenance)
        succeeded = any(
            o.get("step_id") == sid and o.get("tool_name") == tool
            and o.get("outcome") == ToolResultOutcome.SUCCESS.value
            for o in state.observations)
        return args_seen and succeeded

    def enforce(self, proposal: DecisionProposal,
                state: AgentLoopState) -> EnforcedDecision:
        max_iterations = _max_iterations()
        proposal_decision = (proposal.decision or "").upper()

        # 0) unusable proposal → deterministic fallback
        if proposal_decision not in DECISIONS:
            proposal = HeuristicDecisionProposer().propose(state)
            proposal_decision = proposal.decision
            state.invalid_proposals += 1

        # 1) completion dominance: a STOP or BLOCK proposal over a satisfied
        #    goal converges to STOP (blocked is never rendered as
        #    completion). CONTINUE/RE_PLAN proposals stay model-driven —
        #    the proposer may legitimately continue past a success for
        #    goals needing more work; the iteration bound guarantees
        #    termination either way.
        if state.criteria_satisfied():
            if proposal_decision == LoopDecision.BLOCK.value:
                proposal_decision = LoopDecision.STOP.value
            if proposal_decision == LoopDecision.STOP.value:
                return EnforcedDecision(
                    decision=LoopDecision.STOP,
                    reason=proposal.reason or "goal satisfied",
                    coerced_from="BLOCK"
                    if proposal.decision == "BLOCK" else None)

        # 2) STOP requires justification: criteria satisfied OR an
        #    authoritative zero for the analyzed scope. Otherwise bounded
        #    coercion — never a silently declared success.
        if proposal_decision == LoopDecision.STOP.value:
            if state.conclusive_empty():
                return EnforcedDecision(
                    decision=LoopDecision.STOP,
                    reason=proposal.reason
                    or "conclusive: nothing matched the analyzed scope")
            if state.iteration >= max_iterations:
                return EnforcedDecision(
                    decision=LoopDecision.STOP,
                    reason="maximum investigation iterations reached",
                    coerced_from="STOP", limit_reached=True)
            coerced_to = (LoopDecision.CONTINUE
                          if state.pending_steps() else LoopDecision.RE_PLAN)
            return EnforcedDecision(
                decision=coerced_to,
                reason="stop proposed before the goal's completion criteria "
                       "were satisfied; continuing within the loop bound",
                coerced_from="STOP")

        # 3) iteration bound: CONTINUE/RE_PLAN need budget
        if proposal_decision in (LoopDecision.CONTINUE.value,
                                 LoopDecision.RE_PLAN.value):
            # AL-2.13 no-progress convergence — EVALUATED FIRST (U00047
            # fix A): an identical SUCCESS result that was already
            # observed BEFORE the current (re-)plan means the re-plan
            # re-ran the same query with the same normalized inputs and
            # reproduced it — no further execution can add information,
            # so no later rule may coerce this into another continuation.
            # (Previously this guard sat BEHIND the bounded-supplementary
            # rule and was preempted by a stale step mirror that still
            # listed the just-executed duplicate as pending; the loop then
            # burned its remaining budget re-running identical analyses.)
            # The fingerprint is RESULT-based: different args or a
            # genuinely different result never match, so legitimate new
            # evidence is unaffected. The stop defers ONLY for pending
            # work that could still yield value: a pending evidence-
            # acquiring step that is NOT a re-offer of already-answered
            # work (same step id + tool + arguments that already
            # succeeded), or a pending artifact composition that has not
            # yet produced this run's deliverable — the deliverable
            # composes from EXISTING evidence, so the defer coerces one
            # bounded continuation to compose it (a further identical
            # repeat then stops with the bundle on record).
            last = state.last_observation() or {}
            if (last.get("outcome") == ToolResultOutcome.SUCCESS.value
                    and state.plan_revisions >= 2):
                fp = _observation_fingerprint(last)
                if fp and any(
                        _observation_fingerprint(o) == fp
                        for o in state.observations[:-1]):
                    _new_info_pending = []
                    _artifact_pending = []
                    for _s in state.pending_steps():
                        if _s.get("type") == "generate_artifact":
                            _artifact_pending.append(_s)
                        elif not self._already_answered(state, _s):
                            _new_info_pending.append(_s)
                    if _new_info_pending:
                        pass          # defer: fresh acquiring work remains
                    elif _artifact_pending and not state.artifact_ids:
                        return EnforcedDecision(
                            decision=LoopDecision.CONTINUE,
                            reason=("the repeated analysis yielded no "
                                    "new information; composing the "
                                    "pending deliverable from existing "
                                    "evidence (bounded continuation)"),
                            coerced_from=proposal_decision)
                    else:
                        return EnforcedDecision(
                            decision=LoopDecision.STOP,
                            reason=("the re-planned query reproduced an "
                                    "identical result with no new "
                                    "information; the available data has "
                                    "been fully reported"),
                            coerced_from=proposal_decision)
            # Bounded supplementary execution (deliverable-completeness
            # fix): a PENDING adopted step that maps to UNSATISFIED
            # contract outputs must execute before any re-plan
            # convergence — one bounded continuation, still capped by
            # the loop's iteration bound. This is what turns "required
            # output still pending" into actual execution instead of a
            # silent partial answer.
            if state.goal_contract is not None:
                _unsat = {
                    o.output_id for o in state.goal_contract.outputs
                    if o.satisfaction in (
                        OutputSatisfaction.PENDING.value,
                        OutputSatisfaction.PARTIALLY_SUPPORTED.value,
                        OutputSatisfaction.UNRESOLVED.value)}
                _pending_covered = [
                    s for s in state.plan_steps
                    if s.get("status") == PlanStepStatus.PENDING.value
                    and set((state.step_output_map or {}).get(
                        s.get("step_id"), [])) & _unsat]
                if _pending_covered:
                    return EnforcedDecision(
                        decision=LoopDecision.CONTINUE,
                        reason=("pending plan steps still map to "
                                "unsatisfied contract outputs; bounded "
                                "supplementary execution"),
                        coerced_from=proposal_decision)
            # AL-2.12 repeated-empty guard: an EMPTY result that has
            # already survived one re-plan is conclusive for the available
            # surface — re-planning the same query again only burns the
            # iteration budget and ends in the same zero. Coerce to a
            # bounded stop (the composer states WHAT is empty and what
            # data exists; BOUNDED_DATA when the run did answer part of
            # the request with real data).
            last = state.last_observation() or {}
            if (last.get("outcome") == ToolResultOutcome.EMPTY.value
                    and state.plan_revisions >= 2):
                return EnforcedDecision(
                    decision=LoopDecision.STOP,
                    reason=("the re-planned query returned an empty result "
                            "again; no further plan can produce records "
                            "the Risk Platform does not provide"),
                    coerced_from=proposal_decision)
            if state.iteration >= max_iterations:
                return EnforcedDecision(
                    decision=LoopDecision.STOP,
                    reason="maximum investigation iterations reached",
                    coerced_from=proposal_decision, limit_reached=True)
            if proposal_decision == LoopDecision.CONTINUE.value \
                    and not state.pending_steps():
                return EnforcedDecision(
                    decision=LoopDecision.RE_PLAN,
                    reason="plan exhausted; re-planning to pursue the goal",
                    coerced_from="CONTINUE")

        # 4) BLOCK: never rendered as completion; accepted as-is (the
        #    reason travels to the terminal state)
        return EnforcedDecision(
            decision=LoopDecision(proposal_decision),
            reason=proposal.reason or "investigation decision")


# ---------------------------------------------------------------------------
# GSP-7 API-E2E seam — scripted model boundary (TEST INFRASTRUCTURE ONLY)
# ---------------------------------------------------------------------------

def _load_scripted_session(path: str) -> dict:
    """Read a scripted session file
    ``{"plans": [<plan-JSON strings>], "decisions": [<decision strings>],
    "goal": <GoalRepresentation dict>|absent}``. Unreadable/malformed →
    empty session (the run is then bounded: the planner fails, the
    proposer STOPs) with a warning for diagnosis."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("scripted session file unreadable: %s", path)
        return {}


class _ScriptedSessionLLM:
    """Serves the session's plan-JSON strings FIFO at the LLM boundary.
    Exhausted scripts return an unusable payload → the planner's own
    bounded failure path (never a fabricated plan)."""

    def __init__(self, scripts: list):
        self.scripts = [s for s in scripts if isinstance(s, str)]

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        if not self.scripts:
            return ""
        return self.scripts.pop(0)


class _ScriptedSessionPlanner:
    """PlannerV2 over the scripted session's plans: ONLY the LLM text is
    scripted — parsing, registry validation, parameter locks and tool
    resolution stay the production path."""

    def __init__(self, session: dict):
        from app.planner_v2 import PlannerV2
        self._planner = PlannerV2(_ScriptedSessionLLM(
            session.get("plans") or []))

    def plan(self, user_request, context, eligible_skills,
             finding_capabilities=None, goal_contract=None, **kwargs):
        return self._planner.plan(
            user_request, context, eligible_skills,
            finding_capabilities=finding_capabilities,
            goal_contract=goal_contract, **kwargs)


class _ScriptedSessionDecisions:
    """FIFO decision strings; exhausted → STOP (bounded)."""

    def __init__(self, session: dict):
        self.decisions = [d for d in (session.get("decisions") or [])
                          if isinstance(d, str)]

    def propose(self, state) -> DecisionProposal:
        decision = self.decisions.pop(0) if self.decisions else "STOP"
        return DecisionProposal(decision=decision,
                                reason="scripted session",
                                source="heuristic", valid=True)


class _ScriptedSessionGoalResolver:
    """Optional "goal" section: a GoalRepresentation template (may carry a
    contract_proposal) returned for every goal-understanding call, so the
    accepted contract is deterministic without an LLM. Absent section →
    the runner keeps its production goal resolver."""

    def __init__(self, template: dict):
        self._template = template

    def __call__(self, request_text, focused_finding_id, case_id):
        return GoalRepresentation.model_validate(self._template)


class _ScriptedSessionGoalLLM:
    """WP-CG-4 seam: serves scripted goal-understanding LLM TEXT through
    the REAL understand_goal — prompt building, JSON parsing, resolution
    branches and proposal extraction run the production path; only the
    model text is deterministic. Exhausted/empty scripts return an
    unusable payload → understand_goal's own bounded heuristic fallback
    (the LLM_EMPTY provenance route)."""

    def __init__(self, scripts: list):
        self.scripts = [s for s in scripts if isinstance(s, str)]

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        if not self.scripts:
            return ""
        return self.scripts.pop(0)


def _scripted_goal_llm_resolver(scripts: list):
    """Resolver factory for the "goal_llm" session section: the REAL
    understand_goal over scripted model text (test infrastructure)."""
    from app.goal_resolution import understand_goal
    llm = _ScriptedSessionGoalLLM(scripts)

    def resolve(request_text, focused_finding_id, case_id,
                diagnostics=None):
        return understand_goal(request_text,
                               resolved_finding_id=focused_finding_id,
                               case_id=case_id, use_llm=True, llm=llm,
                               diagnostics=diagnostics)

    return resolve


def _call_goal_resolver(resolver, goal, focused_finding_id, case_id, diag,
                        case_findings=None):
    """Invoke the configured goal resolver, deciding the calling
    convention BEFORE the call (WP-CG-3 PMO check): resolvers whose
    signature binds four positional arguments receive the
    goal-understanding diagnostics dict; legacy 3-arg resolvers keep
    working; a callable accepting neither form is never called
    speculatively — a clear TypeError is raised for the checkpoint's
    bounded error handling. Exceptions raised INSIDE the resolver
    propagate unchanged: they are never mistaken for a signature
    mismatch and never trigger a second invocation. Uninspectable
    callables (rare) use the legacy 3-arg convention — the historically
    universal one — as the safe default."""
    import inspect
    try:
        signature = inspect.signature(resolver)
    except (TypeError, ValueError):
        signature = None
    if signature is None:
        return resolver(goal, focused_finding_id, case_id)
    try:
        signature.bind(goal, focused_finding_id, case_id, diag,
                       case_findings=case_findings)
        # findings-aware convention: the deterministic explain-compound
        # decomposition needs THIS case's finding metadata
        return resolver(goal, focused_finding_id, case_id, diag,
                        case_findings=case_findings)
    except TypeError:
        pass
    try:
        signature.bind(goal, focused_finding_id, case_id, diag)
    except TypeError:
        signature.bind(goal, focused_finding_id, case_id)
        return resolver(goal, focused_finding_id, case_id)
    return resolver(goal, focused_finding_id, case_id, diag)


# ---------------------------------------------------------------------------
# Loop runner
# ---------------------------------------------------------------------------

class AgentLoopResult(BaseModel):
    state: AgentLoopState
    response: str
    task: TaskV2
    # artifact BODIES produced by this run (ids alone live on state/task) —
    # lets callers persist them into the investigation read model (AL-2.5)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class AgentLoopRunner:
    """Bounded execute → observe → decide coordinator over the existing V2
    runtime. Glue only: it never calls tools directly, never invents plans,
    and never overrides the deterministic policy."""

    def __init__(
        self,
        *,
        resolver: Any | None = None,
        planner: Any | None = None,
        executor: ExecutorV2 | None = None,
        store: Any | None = None,
        proposer: DecisionProposer | None = None,
        policy: LoopPolicy | None = None,
        findings_provider: Any | None = None,
        goal_resolver: Callable[[str, str | None, str | None], Any] | None = None,
    ):
        self.resolver = resolver
        self.planner = planner
        self.executor = executor
        self.store = store
        self.proposer = proposer
        self.policy = policy or LoopPolicy()
        # R2a content-verification registry (U00047 fix B): output ids
        # whose satisfaction was established by artifact CONTENT
        # verification. The delivered file persists unchanged, so a later
        # observation-level re-evaluation must never downgrade them.
        self._artifact_satisfied_outputs: set[str] = set()
        # GSP-7 API-E2E seam (TEST INFRASTRUCTURE ONLY; default OFF):
        # AGENT_LOOP_SCRIPTED_SESSION_FILE points at a JSON session
        # {"plans": [<plan-JSON strings>], "decisions": [<CONTINUE/
        # RE_PLAN/STOP strings>], "goal": {...}|absent} consumed FIFO at
        # the model boundary so a REAL HTTP API run can drive the loop
        # deterministically (planner LLM, decision proposer, and optionally
        # goal understanding scripted; everything else — HTTP API,
        # resolver, coverage admission, execution, postconditions,
        # composer, persistence — is the real production path). Empty
        # setting = real LLM planner + real decision proposer, unchanged.
        self._scripted_session_path = (
            os.environ.get("AGENT_LOOP_SCRIPTED_SESSION_FILE") or "")
        scripted = (_load_scripted_session(self._scripted_session_path)
                    if self._scripted_session_path else {})
        if scripted and self.planner is None:
            self.planner = _ScriptedSessionPlanner(scripted)
        if scripted and self.proposer is None:
            self.proposer = _ScriptedSessionDecisions(scripted)
        # Stage 2.1: optional goal-understanding callable
        # (request_text, focused_finding_id, case_id) -> GoalRepresentation.
        self.goal_resolver = goal_resolver
        if scripted and isinstance(scripted.get("goal"), dict):
            # The session's goal section is an explicit operator request
            # for scripted goal understanding (the model boundary must be
            # fully scripted for a deterministic API run — an unscripted
            # goal understanding would call the real LLM from the API
            # path). It therefore overrides the default production
            # resolver whenever present.
            self.goal_resolver = _ScriptedSessionGoalResolver(
                scripted["goal"])
        if scripted and isinstance(scripted.get("goal_llm"), list):
            # WP-CG-4: the goal_llm section scripts the goal-understanding
            # MODEL TEXT while the REAL understand_goal runs (prompt,
            # parsing, resolution branches, proposal extraction) — the
            # stronger harness; it wins over the "goal" template section.
            self.goal_resolver = _scripted_goal_llm_resolver(
                scripted["goal_llm"])
        # authoritative findings source resolved at run start (QA-04):
        # failure => distinct BLOCKED/dependency state, never planning error
        self.findings_provider = findings_provider

    # --- public -------------------------------------------------------------

    def run(
        self,
        goal: str,
        context: InvestigationContext,
        findings: list[Finding],
        *,
        investigation_id: str | None = None,
        task_id: str | None = None,
        findings_provider: Any | None = None,
        response_language: str | None = None,
    ) -> AgentLoopResult:
        from datetime import datetime, timezone

        def _now() -> str:
            return datetime.now(timezone.utc).isoformat()

        inv_id = investigation_id or f"CASE:{context.case_id}"
        task_id = task_id or f"TASK-{uuid.uuid4().hex[:12]}"
        task = TaskV2(task_id=task_id, investigation_id=inv_id,
                      user_request=goal, type="agent_loop",
                      status=TaskStatusV2.PLANNING)
        # AL-2.10 persistence: the loop turn is part of the conversation
        # chronology — restore ordering is `ORDER BY started_at`, so the
        # loop task carries its start stamp like every turn task (a NULL
        # here reordered reopened conversations, jumping the loop turn to
        # the top).
        task.started_at = _now()
        state = AgentLoopState(
            task_id=task_id, investigation_id=inv_id,
            case_id=context.case_id, goal=goal,
            response_language=response_language,
            criteria_kind=self._criteria_kind(goal),
            created_at=_now(), updated_at=_now())
        # the resolved context IS the execution context (QA-01): the
        # user-selected focus is captured BEFORE any dependency resolution
        # so even a dependency-blocked run records what it was asked to
        # investigate
        state.focused_finding_id = context.focused_finding_id
        state.focus_source = (context.focus_source.value
                              if context.focus_source else None)
        self._persist(state, task)

        try:
            self._emit("agent.started", state, task,
                       summary=f"Goal: {goal}")
            # Resolve authoritative findings BEFORE goal understanding:
            # the deterministic explain-compound decomposition needs THIS
            # case's finding metadata (QA-04 boundary unchanged — a
            # dependency failure here is a distinct BLOCKED state, and
            # blocked/unresolved runs still carry their objective below).
            resolved_findings = findings
            provider = findings_provider or getattr(
                self, "findings_provider", None)
            if provider is not None:
                try:
                    resolved_findings = provider() or []
                except Exception as e:
                    logger.warning("Findings dependency unavailable: %s",
                                   e)
                    return self._terminate(
                        state, task, AgentStatus.BLOCKED,
                        StopReason.DEPENDENCY_UNAVAILABLE,
                        "The investigation cannot start right now: the "
                        "Risk Platform is unavailable. No investigation "
                        "was fabricated — please retry later.", _now)
            # Stage 2.1: the structured goal representation is produced
            # BEFORE target resolution so every run — including
            # blocked/unresolved ones — carries its understood objective
            # (baseline §35b).
            produced = None
            goal_diag: dict = {}
            contract_reason: str | None = None
            resolution_error: str | None = None
            if self.goal_resolver is not None:
                try:
                    # WP-CG-3: the calling convention is decided BEFORE the
                    # call (signature bind — never by catching a failed
                    # execution); legacy 3-arg resolvers keep working and
                    # the resolver runs at most once. Exceptions raised
                    # inside the resolver propagate to the bounded handler
                    # below with their real category preserved.
                    produced = _call_goal_resolver(
                        self.goal_resolver, goal, state.focused_finding_id,
                        state.case_id, goal_diag,
                        case_findings=resolved_findings)
                    # Q2 closure: keep the goal-understanding diagnostics
                    # on the state so the composer can disclose the REAL
                    # failure class (parse/empty/timeout/…) instead of a
                    # generic "service unavailable" claim.
                    if goal_diag:
                        state.goal_understanding_diagnostics = dict(goal_diag)
                    if produced is not None:
                        state.goal_representation = produced.project()
                        # GSP WP-GSP-2: deterministic acceptance of the
                        # required-outputs proposal carried by the SAME
                        # goal-understanding call. Accept-or-reject only —
                        # no repair, no substitution; a rejected proposal
                        # leaves the run on the legacy (no-contract) path.
                        from app.config import settings as _cfg
                        if getattr(
                                _cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", False):
                            from app.goal_resolution import (
                                build_goal_contract)
                            contract, contract_reason = build_goal_contract(
                                produced, state.case_id)
                            state.goal_contract = contract
                            _telemetry.emit_event(
                                "goal_contract.accepted" if contract
                                else "goal_contract.rejected",
                                investigation_id=state.investigation_id,
                                task_id=state.task_id,
                                contract_id=(contract.contract_id
                                             if contract else None),
                                output_count=(len(contract.outputs)
                                              if contract else None),
                                reason_class=contract_reason,
                                guard_enabled=True)
                except Exception as exc:
                    # WP-CG-3: the real failure category is preserved (log
                    # + decision reason) so an internal resolver error is
                    # distinguishable from a plain no_proposal outcome.
                    logger.warning("goal representation failed", exc_info=True)
                    contract_reason = None
                    resolution_error = f"{type(exc).__name__}: {exc}"[:200]
            # WP-CG-3: structured contract-decision provenance + safe
            # dispositions (flag ON only). Distinguishes contract accepted
            # / no proposal / invalid proposal / LLM-empty fallback /
            # heuristic single-goal routing / genuine-ambiguity stop.
            from app.config import settings as _cfg
            if getattr(_cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", False):
                from app.goal_resolution import GoalResolutionStatus
                if resolution_error:
                    decision = {"outcome": "no_proposal",
                                "reason": f"goal resolution failed: "
                                          f"{resolution_error}"}
                else:
                    decision = {"outcome": "no_proposal",
                                "reason": "no goal representation"}
                if produced is not None:
                    if produced.goal_resolution == \
                            GoalResolutionStatus.AMBIGUOUS.value:
                        decision = {
                            "outcome": "ambiguous_not_executed",
                            "reason": "genuine goal ambiguity; "
                                      "clarification required before "
                                      "investigation"}
                    elif state.goal_contract is not None:
                        decision = {"outcome": "contract_accepted",
                                    "reason": None}
                    elif contract_reason == "no_proposal":
                        if getattr(produced, "source", "") == "heuristic":
                            if goal_diag.get("llm_error"):
                                decision = {
                                    "outcome": "llm_empty",
                                    "reason": goal_diag["llm_error"]}
                            else:
                                decision = {
                                    "outcome": "heuristic_single_goal",
                                    "reason": "deterministic heuristic "
                                              "classification; not a full "
                                              "contract proposal"}
                        else:
                            decision = {
                                "outcome": "no_proposal",
                                "reason": "no required-outputs proposal "
                                          "returned"}
                    else:
                        decision = {"outcome": f"invalid_proposal:"
                                               f"{contract_reason}",
                                    "reason": contract_reason}
                state.goal_contract_decision = decision
                # WP-GS-C: request-coverage assessment — detect comparison
                # intent that the accepted contract does not cover. The
                # detector is bounded and deterministic; uncovered intent
                # is disclosed, never silently absorbed.
                from app.goal_resolution import assess_comparison_intent
                if assess_comparison_intent(goal):
                    has_facet = any(
                        "destination_address_identity" in
                        (o.postcondition_facets or [])
                        for o in (state.goal_contract.outputs
                                  if state.goal_contract else []))
                    if not has_facet:
                        decision["coverage"] = "comparison_intent_uncovered"
                if decision["outcome"] == "ambiguous_not_executed":
                    # Genuine ambiguity: bounded clarification-shaped
                    # termination BEFORE any planning or tool execution.
                    # The system never picks the conveniently-classifiable
                    # single goal and presents it as the whole request.
                    self._persist(state, task)
                    return self._terminate(
                        state, task, AgentStatus.BLOCKED,
                        StopReason.CLARIFICATION_NEEDED,
                        "无法确定您要调查的目标：该请求存在多种可能的理解，"
                        "系统不会默认只调查其中一项。请明确您希望优先调查的"
                        "目标后重新发起。",
                        _now)
            return self._run_loop(goal, context, resolved_findings, state,
                                  task, _now)
        except Exception as e:                    # bounded: never silently OK
            logger.exception("Agent loop failed")
            state.agent_status = AgentStatus.FAILED.value
            state.stop_reason = StopReason.PERSISTENT_ERROR
            state.response = (
                "The investigation stopped because of an unexpected "
                "internal error. Partial results are preserved.")
            state.updated_at = _now()
            task.status = TaskStatusV2.FAILED
            task.error = f"{type(e).__name__}"
            task.completed_at = _now()
            self._persist(state, task)
            self._emit("agent.failed", state, task,
                       stop_reason=state.stop_reason,
                       summary=state.response)
            return AgentLoopResult(state=state, response=state.response,
                                   task=task)

    # --- internals ------------------------------------------------------------

    def _criteria_kind(self, goal: str) -> str:
        from app.context_resolution import _classify_request_intent
        from app.models import RequestIntent
        if _classify_request_intent(goal) in (
                RequestIntent.ARTIFACT_CASE, RequestIntent.ARTIFACT_FINDING):
            return CRITERIA_ARTIFACT_PRODUCED
        return CRITERIA_QUESTION_ANSWERED

    def resume(self, task_id: str, *, findings_provider=None) \
            -> AgentLoopResult:
        """AL-1 recovery: reload a persisted NON-terminal loop state and
        continue the bounded loop from the adopted plan. Same store, same
        caps (iteration/retry counters come from the persisted state),
        same policy — nothing is reset by resuming. Terminal states are
        refused (they are closed records, not pause points)."""
        from datetime import datetime, timezone

        def _now() -> str:
            return datetime.now(timezone.utc).isoformat()

        if self.store is None:
            raise ValueError("resume requires a store")
        state = self.store.get_agent_state(task_id)
        if state is None:
            raise ValueError(f"no persisted loop state for {task_id!r}")
        if state.agent_status in TERMINAL_AGENT_STATUSES:
            raise ValueError(
                f"loop {task_id!r} is already terminal "
                f"({state.agent_status}); terminal runs are closed "
                f"records, not pause points")
        task = self.store.get(task_id)
        if task is None:
            raise ValueError(f"no task container for {task_id!r}")
        task.status = TaskStatusV2.EXECUTING
        self.store.update(task)
        try:
            findings = findings_provider() if findings_provider else []
            if not findings and self.findings_provider is not None:
                findings = self.findings_provider()
        except Exception as e:
            return self._terminate(
                state, task, AgentStatus.BLOCKED,
                StopReason.DEPENDENCY_UNAVAILABLE,
                "The investigation cannot be resumed right now: the Risk "
                f"Platform is unavailable ({e}).", _now)
        context = InvestigationContext(
            case_id=state.case_id,
            focused_finding_id=state.focused_finding_id,
            focus_source=(FocusSource(state.focus_source)
                          if state.focus_source else None),
        )
        self._emit("agent.started", state, task,
                   summary="resumed from persisted state (AL-1 recovery)")
        return self._run_loop(state.goal, context, findings, state, task,
                              _now, resume_mode=True)

    def _run_loop(self, goal, context, findings, state, task, _now,
                  resume_mode: bool = False) -> AgentLoopResult:
        # --- 1) context resolution -------------------------------------------
        # (skipped on resume: the persisted state already carries the
        # resolved focus and the adopted plan — WP-M2-1 AL-1 recovery)
        if resume_mode:
            ctx = InvestigationContext(
                case_id=state.case_id,
                focused_finding_id=state.focused_finding_id,
                focus_source=(FocusSource(state.focus_source)
                              if state.focus_source else None),
            )
            # AL-1 resume re-validation (single-case isolation): an
            # AGENT_RESOLVED focus is an INFERENCE product. A focus
            # persisted before a resolver fix (e.g. cross-case seed
            # pollution) must not silently survive recovery just because
            # the id still exists in the case. Re-resolve the SAME goal
            # through the CURRENT resolver; an updated RESOLVED target
            # replaces the stale focus (user-selected focus is never
            # re-interpreted — explicit selection stays authoritative).
            # No re-resolution result (unresolved / unavailable / no
            # resolver) keeps the persisted focus — the pre-existing
            # resume contract, unchanged.
            if (self.resolver is not None
                    and state.focus_source == FocusSource.AGENT_RESOLVED.value
                    and findings):
                try:
                    # re-resolve from a CLEAN context: the persisted focus
                    # itself would short-circuit resolution (priority 1-2
                    # keep), which is exactly what must be re-checked
                    reval = self.resolver.resolve(
                        goal,
                        InvestigationContext(case_id=state.case_id),
                        findings=findings)
                    new_focus = reval.updated_context.focused_finding_id
                    if (reval.status == ResolutionStatus.RESOLVED
                            and new_focus
                            and new_focus != state.focused_finding_id):
                        previous_focus = state.focused_finding_id
                        state.focused_finding_id = new_focus
                        state.focus_source = (
                            reval.updated_context.focus_source.value
                            if reval.updated_context.focus_source
                            else state.focus_source)
                        ctx = InvestigationContext(
                            case_id=state.case_id,
                            focused_finding_id=new_focus,
                            focus_source=ctx.focus_source,
                        )
                        _telemetry.emit_event(
                            "resume.focus_revalidated",
                            investigation_id=state.investigation_id,
                            task_id=state.task_id,
                            focused_finding_id=new_focus,
                            previous_finding_id=previous_focus,
                            guard_enabled=True)
                except Exception:
                    logger.warning("resume focus re-validation failed; "
                                   "persisted focus kept", exc_info=True)
        elif self.resolver is not None:
            resolution = self.resolver.resolve(goal, context,
                                               findings=findings)
            ctx = resolution.updated_context
            # Compound-contract precedence (deliverable-completeness
            # fix): when goal understanding has ALREADY accepted a
            # contract whose outputs span the request's named findings,
            # a context-resolution AMBIGUOUS **or UNRESOLVED** outcome is
            # immaterial — the per-output obligations ARE the resolution,
            # and each will be executed and judged on its own evidence.
            # A multi-target request can never resolve to ONE target, so
            # a single-target UNRESOLVED is the EXPECTED resolver outcome
            # for an accepted compound contract; blocking here to ask
            # "which finding?" would discard an already-accepted
            # complete contract and every deliverable in it.
            _accepted_outputs = (
                list(state.goal_contract.outputs)
                if state.goal_contract else [])
            if (resolution.status in (ResolutionStatus.AMBIGUOUS,
                                      ResolutionStatus.UNRESOLVED)
                    and _accepted_outputs):
                # the accepted contract's per-output obligations ARE the
                # resolution — proceed; each output is executed and judged
                # on its own evidence (never collapsed into one status)
                pass
            elif resolution.status in (ResolutionStatus.AMBIGUOUS,
                                       ResolutionStatus.UNRESOLVED):
                # --- WP-M2-1 WI-6: clarification boundary in the shared
                # ledger (same lifecycle as the conversation path). The
                # loop terminates BLOCKED — it is NEVER resumed in place;
                # resumption happens through the conversation (explicit
                # designation or a rephrased request), which is an
                # external state transition, not a loop iteration.
                if (self.store is not None and
                        resolution.status == ResolutionStatus.AMBIGUOUS):
                    try:
                        from app.clarification_state import (
                            ClarificationLedger)
                        from app.config import settings as _settings
                        if _settings.ENABLE_SAFE_SELECT_ADMISSIBILITY:
                            ClarificationLedger(self.store) \
                                .record_clarification(
                                    investigation_id=state.investigation_id,
                                    request_text=goal,
                                    candidate_ids=[c.id for c in
                                                   resolution.candidates],
                                    reason_class="agent_loop_blocked")
                    except Exception:
                        logger.warning(
                            "clarify ledger recording failed on the "
                            "Agent Loop boundary", exc_info=True)
                return self._terminate(
                    state, task, AgentStatus.BLOCKED,
                    StopReason.CLARIFICATION_NEEDED
                    if resolution.status == ResolutionStatus.AMBIGUOUS
                    else StopReason.CONTEXT_UNRESOLVED,
                    resolution.clarification_message
                    or "The investigation target could not be resolved.",
                    _now)
            if resolution.status == \
                    ResolutionStatus.SEMANTIC_RESOLUTION_UNAVAILABLE:
                # AL-2.8: honest dependency outage — BLOCKED with its own
                # stop reason, never a planning attempt, never completion.
                return self._terminate(
                    state, task, AgentStatus.BLOCKED,
                    StopReason.SEMANTIC_RESOLUTION_UNAVAILABLE,
                    resolution.clarification_message
                    or "The semantic resolution service is currently "
                       "unavailable; the target cannot be safely "
                       "determined. Please select the finding directly or "
                       "retry later.",
                    _now)
            if resolution.status == ResolutionStatus.CAPABILITY_GUIDANCE:
                guidance = resolution.clarification_message \
                    or "Capability guidance provided."
                state.response = guidance
                result = self._terminate(
                    state, task, AgentStatus.COMPLETED,
                    StopReason.GOAL_SATISFIED, "", _now)
                result.response = guidance     # the answer IS the guidance
                return result
            state.focused_finding_id = ctx.focused_finding_id
            state.focus_source = (ctx.focus_source.value
                                  if ctx.focus_source else None)
            state.target_evidence_kind = getattr(
                resolution, "evidence_kind", None)
        else:
            ctx = context
            state.focused_finding_id = context.focused_finding_id
            state.focus_source = (context.focus_source.value
                                  if context.focus_source else None)
        # --- Contract-subject focus re-anchor (target-vs-evidence) ------
        # The ACCEPTED GoalContract is built from THIS request's goal
        # understanding. When it names exactly one finding-level subject
        # and the resolved focus is a DIFFERENT finding of the same case,
        # the stale focus (e.g. a user_selected focus from an earlier
        # turn in the same conversation) must not steer this turn's
        # planning and execution to another finding's evidence: the
        # contract's per-output obligations ARE this turn's resolution
        # (same precedence principle as the compound-contract block
        # above). Single-subject only — multi-target compound contracts
        # carry explicit per-step anchors instead. Same-subject runs are
        # untouched (continuous investigation keeps its context).
        _reanchored = _reanchor_focus_to_contract_subject(
            ctx, state.goal_contract, findings)
        if _reanchored is not None:
            _telemetry.emit_event(
                "focus.reanchored_to_contract_subject",
                investigation_id=state.investigation_id,
                task_id=state.task_id,
                focused_finding_id=_reanchored.focused_finding_id,
                previous_finding_id=ctx.focused_finding_id,
                guard_enabled=True)
            ctx = _reanchored
            state.focused_finding_id = ctx.focused_finding_id
            state.focus_source = FocusSource.AGENT_RESOLVED.value
            state.target_evidence_kind = "contract_subject"
        # --- AL-3.5.1 G1: target-validity admission (Layer B) -----------
        # Single choke point for every /goal run, reached before planning
        # and before any tool call (covers the resume path too). With the
        # flag OFF this block is never consulted — byte-for-byte
        # pre-guard behavior.
        from app.config import settings
        if getattr(settings, "ENABLE_TARGET_VALIDITY_GUARD", False):
            evidence_kind = state.target_evidence_kind
            if resume_mode and evidence_kind is None and state.focus_source:
                # Legacy persisted state (started pre-guard): an existing
                # run's focus continues as context continuation; new runs
                # persist the real kind.
                evidence_kind = "context_continuation"
            decision = target_validity.decide(
                goal=goal,
                # A finding target reaching this point was RESOLVED (the
                # resolution block terminates AMBIGUOUS/UNRESOLVED above).
                resolution_status=("resolved"
                                   if state.focused_finding_id else None),
                focused_finding_id=state.focused_finding_id,
                focus_source=state.focus_source,
                evidence_kind=evidence_kind)
            _telemetry.emit_event(
                "target_validity.decided",
                investigation_id=state.investigation_id,
                task_id=state.task_id,
                focused_finding_id=decision.candidate_finding_id,
                decision=decision.state,
                reason_code=decision.reason_code,
                evidence_kind=decision.evidence_kind,
                guard_enabled=True)
            if decision.state == target_validity.TARGET_UNGROUNDED:
                # UNKNOWN ≠ FALSE: the evidence cannot bind the named
                # subject to this target — clarify honestly; never
                # claim the topic does not exist; never execute a
                # finding-specific tool.
                return self._terminate(
                    state, task, AgentStatus.BLOCKED,
                    StopReason.CONTEXT_UNRESOLVED,
                    "I couldn't reliably determine which finding you "
                    "mean. Select the finding in the Findings panel, or "
                    "name it explicitly.", _now)
            if decision.state == target_validity.TARGET_INVALID:
                # v1: log + clarify (telemetry above) — never a hard
                # block, never execution on the contradicted target.
                return self._terminate(
                    state, task, AgentStatus.BLOCKED,
                    StopReason.CONTEXT_UNRESOLVED,
                    "The finding you named doesn't match what this "
                    "request resolved to. Please name the finding "
                    "explicitly or select it in the Findings panel.",
                    _now)
            # TARGET_VALID / TARGET_AMBIGUOUS(≠resolved) → proceed;
            # ambiguous resolutions never reach this point (terminated
            # in the resolution block above).
        self._emit("context.resolved", state, task,
                   summary=f"focus={state.focused_finding_id or 'case-level'}")
        self._persist(state, task)


        # --- 2) collaborators -------------------------------------------------
        executor = self.executor or ExecutorV2()
        planner = self.planner
        proposer = self.proposer
        if proposer is None:
            try:
                _make_llm()
                proposer = LLMDecisionProposer()
            except Exception:
                proposer = HeuristicDecisionProposer()

        focused = next((f for f in findings
                        if f.finding_id == state.focused_finding_id), None)
        finding_caps = focused.capabilities if focused else None
        # AL-2.8: keep the RP-authoritative identity of the focused finding
        # for the response composer (bounded fields only).
        if focused is not None:
            state.focused_finding_title = focused.title
            state.focused_finding_summary = focused.summary
        eligible_ids = [s.skill_id for s in
                        eligible_skills_for_finding(finding_caps)]

        # --- 3) initial plan ---------------------------------------------------
        # (skipped on resume: the persisted plan was adopted before the
        # interruption and is reloaded from agent_states — AL-1 recovery)
        if resume_mode and state.plan_steps:
            plan = Plan(
                plan_id=state.plan_id or f"resumed-{task.task_id}",
                investigation_id=state.investigation_id,
                goal=state.goal,
                steps=[PlanStep(
                    step_id=ref["step_id"], type=ref["type"],
                    tool_name=ref.get("tool_name"),
                    arguments=dict(ref.get("arguments") or {}),
                    status=PlanStepStatus(ref["status"]))
                    for ref in state.plan_steps],
                status=PlanStatus.EXECUTING,
            )
            self._persist(state, task)
            task.status = TaskStatusV2.EXECUTING
        else:
            plan, failure_code = self._plan(planner, goal, ctx, eligible_ids,
                                            finding_caps,
                                            goal_contract=state.goal_contract,
                                            findings=findings)
            if plan is None:
                # per-code accurate feedback: an empty model response, a
                # timeout, rate limiting, a misconfiguration, and a genuine
                # planning-surface failure are DIFFERENT user-visible states
                _planning_messages = {
                    "LLM_EMPTY_RESPONSE":
                        "The model returned empty responses, so this "
                        "request could not be planned. Please try again "
                        "in a moment.",
                    "LLM_TIMEOUT":
                        "The planning request timed out. Please try again "
                        "in a moment.",
                    "LLM_RATE_LIMITED":
                        "The planning service was rate limited. Please "
                        "try again after a short delay.",
                    "LLM_CONFIGURATION_ERROR":
                        "The planning service is misconfigured. This "
                        "needs operator attention.",
                    "PLANNING_UNEXPECTED_ERROR":
                        "An unexpected error occurred during planning. "
                        "Please try again.",
                }
                _planning_msg = _planning_messages.get(
                    failure_code,
                    f"The investigation could not be planned within the "
                    f"supported investigation surface ({failure_code}).")
                return self._terminate(
                    state, task, AgentStatus.FAILED,
                    StopReason.PLANNING_FAILURE,
                    _planning_msg,
                    _now, plan=None)
            # GSP WP-GSP-6: deterministic planning coverage admission over
            # the adopted plan (pure recompute from the registry; the LLM
            # proposal never decides coverage). Outputs with no eligible
            # capability are marked UNSUPPORTED here (the reserved
            # planning-time verdict) and rendered as capability boundaries;
            # covered outputs proceed to execution.
            if getattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", False) \
                    and state.goal_contract is not None:
                # GSP-6 coverage completion (deterministic, general): an
                # accepted output whose evidence path is registry-declarable
                # but whose covering step the proposed plan omitted gets its
                # legal step appended ONCE, BEFORE admission — the coverage
                # verdict is then computed over the COMPLETED plan on every
                # initial planning path (deterministic branches and LLM
                # alike). A not_covered_by_plan verdict can therefore only
                # survive when no legal step exists to append — which
                # admission records as no_eligible_capability → UNSUPPORTED
                # (the honest capability boundary), never silent omission.
                self._supplement_plan_coverage(state, plan)
                from app.planning_coverage import (
                    CoverageStatus, admit_plan_coverage)
                coverage = admit_plan_coverage(
                    state.goal_contract,
                    [s.type for s in plan.steps])
                state.planning_coverage = [
                    {"output_id": r.output_id,
                     "coverage_status": r.coverage_status,
                     "eligible_step_types": r.eligible_step_types,
                     "matched_evidence_types": r.matched_evidence_types,
                     "reason": r.reason}
                    for r in coverage]
                map_entries: dict[str, list[str]] = {}
                for step in plan.steps:
                    # Target-aware refinement: a step carrying an explicit
                    # SYSTEM-derived finding_id (deterministic multi-domain
                    # decomposition) maps ONLY to outputs whose subject is
                    # that finding — same-type outputs for other findings
                    # are never fed each other's evidence.
                    mapped = sorted({
                        r.output_id for r in coverage
                        if step.type in r.eligible_step_types
                        and _step_matches_output_subject(step, r.output_id,
                                                         plan.steps,
                                                         state.goal_contract)})
                    if mapped:
                        map_entries[step.step_id] = mapped
                state.step_output_map = map_entries
                for r in coverage:
                    if r.coverage_status == "no_eligible_capability":
                        # reserved planning-time verdict (GSP-6): no legal
                        # acquisition path exists for this output.
                        for out in state.goal_contract.outputs:
                            if out.output_id == r.output_id:
                                out.satisfaction = (
                                    OutputSatisfaction.UNSUPPORTED)
                                out.detail = (
                                    "当前系统不具备获取该项证据所需的核查"
                                    "能力。")
                covered = [r.output_id for r in coverage
                           if r.coverage_status == "covered"]
                _telemetry.emit_event(
                    "planning.coverage.admitted",
                    investigation_id=state.investigation_id,
                    task_id=state.task_id,
                    contract_id=state.goal_contract.contract_id,
                    output_count=len(coverage),
                    decision=("partial" if covered
                              and len(covered) < len(coverage)
                              else ("covered" if covered else "uncovered")),
                    summary=f"covered={covered}",
                    guard_enabled=True)
            self._adopt_plan(state, plan)
            self._emit("plan.created", state, task,
                       summary=f"plan {plan.plan_id} "
                               f"({[s.type for s in plan.steps]})")
            self._persist(state, task)
            task.status = TaskStatusV2.EXECUTING

        provenance_pool: list[ToolCallV2] = []
        # artifact BODIES produced by this run (ids alone are already
        # mirrored onto state/task; the bodies must survive the run so the
        # API layer can persist them into the investigation read model —
        # AL-2.5: loop-produced artifacts were previously unrecoverable)
        produced_artifacts: list[dict[str, Any]] = []

        # --- 4) bounded iteration loop -----------------------------------------
        # U00047 fix A: observation-count marker at plan adoption — lets
        # the RE_PLAN suppression detect a revision whose acquisition
        # steps all reproduced earlier results.
        self._obs_count_at_adoption = len(state.observations)
        while True:
            # the snapshot mirrors the executor-mutated PlanSteps so step
            # selection always reflects reality
            self._sync_plan_mirror(state, plan)
            pending = state.pending_steps()
            if pending:
                step_ref = pending[0]
                step = next(s for s in plan.steps
                            if s.step_id == step_ref["step_id"])
            else:
                # plan exhausted: the decision layer re-plans or terminates;
                # synthesize an observation-free decision point
                step = None

            if step is not None:
                # AL-2: argument provenance — an argument value that matches
                # an id surfaced by the PREVIOUS observation is sourced from
                # prior_observation; otherwise from the governed context.
                prev_obs = state.observations[-1] if state.observations \
                    else None
                observed_ids = set()
                if prev_obs:
                    for f in (prev_obs.get("summary", {})
                              .get("findings", []) or []):
                        if f.get("id"):
                            observed_ids.add(f["id"])
                    for r in prev_obs.get("evidence_refs", []) or []:
                        if r.get("id"):
                            observed_ids.add(r["id"])
                arg_prov = {}
                for arg_key, arg_val in (step.arguments or {}).items():
                    if arg_val and str(arg_val) in observed_ids:
                        arg_prov[arg_key] = "prior_observation"
                if arg_prov:
                    _emit("tool.selected", state, task,
                          step_id=step.step_id, step_type=step.type,
                          tool_name=step.tool_name,
                          summary=json.dumps(
                              {"argument_sources": arg_prov},
                              ensure_ascii=False))
                state.arg_provenance.append({
                    "step_id": step.step_id, "tool_name": step.tool_name,
                    "arguments": dict(step.arguments),
                    "sources": arg_prov or {},
                })
                state.iteration += 1
                state.current_step = dict(step_ref)
                step.status = PlanStepStatus.RUNNING
                self._emit("step.started", state, task,
                           step_id=step.step_id, step_type=step.type,
                           iteration=state.iteration)
                self._persist(state, task)

                self._emit("tool.called", state, task,
                           step_id=step.step_id, step_type=step.type,
                           tool_name=step.tool_name,
                           iteration=state.iteration)
                step, tool_call, exec_error, artifacts = \
                    executor.execute_one_step(
                        step, context=ctx,
                        investigation_id=state.investigation_id,
                        task_id=state.task_id,
                        provenance_pool=provenance_pool,
                        prior_artifacts=produced_artifacts,
                        user_request=goal,
                        focused_finding=focused)
                self._emit("tool.completed", state, task,
                           step_id=step.step_id, step_type=step.type,
                           tool_name=step.tool_name,
                           outcome=(tool_call.result.outcome.value
                                    if tool_call and tool_call.result
                                    else None),
                           iteration=state.iteration)
                if tool_call is not None:
                    provenance_pool.append(tool_call)
                    task.tool_call_ids.append(tool_call.tool_call_id)
                for artifact in artifacts:
                    if artifact.get("artifact_id") not in state.artifact_ids:
                        state.artifact_ids.append(
                            artifact["artifact_id"])
                        task.artifact_ids = sorted(
                            set(task.artifact_ids)
                            | {artifact["artifact_id"]})
                    if artifact not in produced_artifacts:
                        produced_artifacts.append(artifact)

                # --- bounded retry (baseline §8) -----------------------------
                if self._retryable(tool_call, exec_error, state, step):
                    state.retries[step.step_id] = state.retries.get(
                        step.step_id, 0) + 1
                    self._emit("step.retried", state, task,
                               step_id=step.step_id, step_type=step.type,
                               tool_name=step.tool_name,
                               retry_count=state.retries[step.step_id],
                               iteration=state.iteration,
                               summary="transient failure; retrying once")
                    step.status = PlanStepStatus.PENDING
                    step_ref["status"] = PlanStepStatus.PENDING.value
                    # the retry consumes the next iteration (the loop-top
                    # increment below counts it)
                    self._persist(state, task)
                    continue

                # --- observation -------------------------------------------------
                observation = project_observation(
                    step.tool_name or "", step.type,
                    tool_call.result if tool_call else None, exec_error)
                observation["iteration"] = state.iteration
                observation["step_id"] = step.step_id
                observation["step_type"] = step.type
                # GSP WP-GSP-3: deterministic evidence-type seed
                # association (flag-gated; no contract → unassociated).
                # ASSOCIATION IS NOT SATISFACTION: `for_outputs` marks the
                # accepted outputs this observation is CANDIDATE evidence
                # for; subject confirmation and any satisfaction verdict
                # belong to the WP-GSP-4 postcondition evaluator.
                from app.config import settings as _gsp_settings
                if getattr(_gsp_settings,
                           "ENABLE_GOAL_SEMANTIC_CONTRACT", False):
                    associate_observation(state, observation)
                state.observations.append(observation)
                if len(state.observations) > _MAX_OBSERVATIONS_KEPT:
                    state.observations = state.observations[
                        -_MAX_OBSERVATIONS_KEPT:]
                if "for_outputs" in observation:
                    from app import telemetry as _t2
                    _t2.emit_event(
                        "observation.associated",
                        investigation_id=state.investigation_id,
                        task_id=state.task_id,
                        iteration=state.iteration,
                        step_id=step.step_id,
                        step_type=step.type,
                        tool_name=observation["tool_name"],
                        decision=("associated"
                                  if observation["for_outputs"]
                                  else "unassociated"),
                        reason_class=observation["association_reason"],
                        evidence_kind=",".join(
                            sorted(t.value for t in observation.get(
                                "actual_evidence_types", [])
                                if hasattr(t, "value")) or []),
                        output_count=len(observation["for_outputs"]),
                        guard_enabled=True)
                # GSP WP-GSP-4: deterministic postcondition evaluation for
                # the outputs this observation is candidate evidence for.
                # SINGLE satisfaction write point: the pure evaluator
                # returns verdicts; ONLY this apply-block writes them onto
                # the contract outputs. LLM decisions never bypass it.
                if observation.get("for_outputs") and state.goal_contract:
                    from app.goal_postconditions import (
                        apply_postcondition, evaluate_contract_outputs)
                    results = evaluate_contract_outputs(
                        state.goal_contract, state.observations,
                        case_id=state.case_id)
                    for res in results:
                        # SINGLE satisfaction write point: apply_postcondition
                        # enforces the conflict downgrade (conflicted scalar
                        # facts → UNRESOLVED, never SATISFIED-with-a-value).
                        satisfaction, polarity, reason_code = (
                            apply_postcondition(res))
                        # PENDING is never written: an output with no
                        # evaluable evidence keeps its current state (a
                        # planning-time UNSUPPORTED is never downgraded to
                        # pending; PENDING ≠ absence of capability).
                        if satisfaction == OutputSatisfaction.PENDING.value \
                                or satisfaction == OutputSatisfaction.PENDING:
                            continue
                        for out in state.goal_contract.outputs:
                            self._write_contract_result(
                                state, out, res, satisfaction, polarity)
                        _t2.emit_event(
                            "postcondition.evaluated",
                            investigation_id=state.investigation_id,
                            task_id=state.task_id,
                            contract_id=state.goal_contract.contract_id,
                            reason_code=reason_code,
                            evidence_kind=",".join(res.evidence_types),
                            output_count=len(res.observation_indexes),
                            decision=satisfaction.value
                            if hasattr(satisfaction, "value")
                            else str(satisfaction),
                            summary=polarity,
                            conflicts=",".join(res.conflicts),
                            guard_enabled=True)
                self._emit("observation.recorded", state, task,
                           step_id=step.step_id, step_type=step.type,
                           tool_name=observation["tool_name"],
                           outcome=observation["outcome"],
                           iteration=state.iteration,
                           summary=observation.get("display")
                           or _observation_line(observation))
                # R2a: content-based satisfaction for records-artifact
                # deliverables. Idempotent — runs per observation; a
                # records output is SATISFIED only when a persisted
                # artifact scoped to its source step actually contains
                # the retrieved record IDs (never from the artifact ID
                # alone, and never from a findings-only bundle).
                self._apply_records_artifact_satisfaction(
                    state, task, provenance_pool, produced_artifacts)

                step_completed = step.status == PlanStepStatus.SUCCESS \
                    or observation["outcome"] in (
                        ToolResultOutcome.SUCCESS.value,
                        ToolResultOutcome.EMPTY.value)
                if step_completed:
                    state.completed_step_ids.append(step.step_id)
                # persist the observation BEFORE deciding (baseline §14:
                # persist after "observation recorded")
                self._persist(state, task)

            else:
                # plan exhausted: observation-free decision point
                self._persist(state, task)

            # Decision freshness (U00047 fix A): the enforcement layer must
            # see the step statuses the executor actually left behind. A
            # stale mirror still lists the just-executed step as PENDING,
            # which let the bounded-supplementary rule coerce CONTINUE past
            # the no-progress guard and re-run identical analyses until the
            # iteration bound.
            self._sync_plan_mirror(state, plan)

            # --- decision (proposal → deterministic enforcement) -------------
            proposal = proposer.propose(state)
            # TEST INSTRUMENTATION (C1 acceptance; default OFF):
            # AGENT_FORCE_REPLAN_ONCE=1 forces exactly ONE RE_PLAN decision
            # after the first artifact has been produced, so the re-plan
            # idempotency path (artifact reuse) runs against real data.
            if (os.environ.get("AGENT_FORCE_REPLAN_ONCE") == "1"
                    and not getattr(self, "_forced_replan_done", False)
                    and (state.artifact_ids or any(
                        o.get("outcome") == "success"
                        for o in state.observations))):
                self._forced_replan_done = True
                proposal = DecisionProposal(
                    decision=LoopDecision.RE_PLAN.value,
                    reason="forced re-plan (test instrumentation)",
                    source="system")
                self._forced_replan_seen = True
            enforced = self.policy.enforce(proposal, state)
            state.decisions.append({
                "iteration": state.iteration,
                "decision": enforced.decision.value,
                "reason": enforced.reason[:300],
                "proposed": proposal.decision,
                "coerced_from": enforced.coerced_from,
            })
            # WP-CG-3: decision rows carry system-computed semantic
            # authority. With no accepted contract (flag ON), a STOP
            # reason's prose cannot prove the whole request was answered —
            # the Activity projection shows a deterministic state
            # description instead (the full prose stays in
            # state.decisions for audit).
            from app.config import settings as _cfg
            _flag_on = getattr(_cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", False)
            semantic_basis = None
            if _flag_on:
                semantic_basis = (
                    f"contract:{state.goal_contract.contract_id}"
                    if state.goal_contract else "none")
            decision_summary = enforced.reason
            if (_flag_on and state.goal_contract is None
                    and enforced.decision == LoopDecision.STOP):
                decision_summary = (
                    "调查循环结束（GoalContract 未建立：以上结果可能未覆盖"
                    "您请求的全部目标）")
            self._emit("decision.made", state, task,
                       iteration=state.iteration,
                       decision=enforced.decision.value,
                       summary=decision_summary,
                       semantic_basis=semantic_basis)
            self._persist(state, task)

            # --- act ----------------------------------------------------------
            if enforced.decision == LoopDecision.STOP:
                if enforced.limit_reached:
                    # C1/B: budget exhausted. With prior successful results,
                    # the honest terminal is a bounded partial answer
                    # (COMPLETED + BOUNDED_DATA + partial outcome + boundary
                    # disclosure) — never a FAILED mask over delivered
                    # evidence, and never ordinary full success. Without
                    # prior success the failure semantics are preserved.
                    prior_success = any(
                        o.get("outcome") ==
                        ToolResultOutcome.SUCCESS.value
                        for o in state.observations)
                    self._mark_current_done(state)
                    if prior_success:
                        return self._terminate(
                            state, task, AgentStatus.COMPLETED,
                            StopReason.BOUNDED_DATA,
                            "investigation iteration budget reached; "
                            "delivering the evidence acquired so far — "
                            "unresolved items remain unresolved", _now,
                            plan=plan,
                            artifacts=produced_artifacts)
                    return self._terminate(
                        state, task, AgentStatus.STOPPED_BY_LIMIT,
                        StopReason.ITERATION_BOUND_REACHED,
                        enforced.reason, _now, plan=plan,
                        artifacts=produced_artifacts)
                status = AgentStatus.COMPLETED
                # Stop-label honesty (U00047 state fix): GOAL_SATISFIED is
                # only honest when the WHOLE accepted output set is
                # SATISFIED — a partial outcome (some outputs
                # partially_supported / unresolved) must carry the bounded
                # semantics, never a full-success claim beside a
                # "已部分回答" composer answer (the Activity chip renders
                # stop_reason verbatim). Contract-less runs keep the
                # criteria-based label: with no output set to claim about,
                # the completion-claim guard owns that wording.
                _goal_fully_satisfied = (
                    (state.goal_contract is None
                     and state.criteria_satisfied())
                    or (state.goal_contract is not None
                        and all(o.satisfaction
                                == OutputSatisfaction.SATISFIED
                                for o in state.goal_contract.outputs)))
                reason = (StopReason.GOAL_SATISFIED
                          if _goal_fully_satisfied
                          else StopReason.BOUNDED_DATA)
                if state.conclusive_empty():
                    # a conclusive zero for the analyzed scope is precise
                    # even when the coarse criteria counted a success —
                    # but ONLY when the run produced no other useful
                    # data: if real evidence WAS retrieved elsewhere in
                    # the run, the honest terminal is a bounded answer
                    # (part of the requested scope is genuinely
                    # unavailable), never a "conclusive empty"
                    # investigation (AL-2.12).
                    prior_success = any(
                        o.get("outcome") ==
                        ToolResultOutcome.SUCCESS.value
                        for o in state.observations)
                    reason = (StopReason.BOUNDED_DATA if prior_success
                              else StopReason.CONCLUSIVE_EMPTY_RESULT)
                self._mark_current_done(state)
                self._emit("step.completed", state, task,
                           step_id=(state.current_step or {}).get("step_id"),
                           step_type=(state.current_step or {}).get("type"),
                           iteration=state.iteration)
                return self._terminate(state, task, status, reason,
                                       "", _now, plan=plan,
                                       artifacts=produced_artifacts)
            if enforced.decision == LoopDecision.BLOCK:
                self._mark_current_done(state)
                return self._terminate(
                    state, task, AgentStatus.BLOCKED,
                    self._block_reason(state, proposal),
                    enforced.reason, _now, plan=plan,
                    artifacts=produced_artifacts)
            if enforced.decision == LoopDecision.RE_PLAN:
                # AL-2.13 suppression (U00047 fix A): a revision whose
                # ACQUISITION steps all reproduced earlier results has no
                # recoverable path — another re-plan would re-offer the
                # same answered work and burn the remaining budget until
                # the iteration bound. Bounded partial stop instead
                # (evidence retained; unresolved items stay unresolved).
                # A revision that produced genuinely new acquisition
                # information, or a run without any successful evidence,
                # re-plans unchanged.
                if any(o.get("outcome") ==
                       ToolResultOutcome.SUCCESS.value
                       for o in state.observations) \
                        and self._revision_acquisition_added_no_new_info(
                            state):
                    self._mark_current_done(state)
                    return self._terminate(
                        state, task, AgentStatus.COMPLETED,
                        StopReason.BOUNDED_DATA,
                        "the last plan revision produced no new "
                        "information; delivering the evidence acquired "
                        "so far — unresolved items remain unresolved",
                        _now, plan=plan,
                        artifacts=produced_artifacts)
                new_plan = self._re_plan(planner, state, goal, ctx,
                                         eligible_ids, finding_caps,
                                         goal_contract=state.goal_contract,
                                         findings=findings)
                if new_plan is None:
                    # Bounded partial completion: when no legal re-plan
                    # exists but the run already holds REAL evidence
                    # (≥1 successful observation), the honest terminal is
                    # a bounded answer over what was acquired — never a
                    # failure that discards the substantive facts already
                    # in hand. A run with nothing successful keeps the
                    # failure terminal (nothing to answer with).
                    prior_success = any(
                        o.get("outcome") ==
                        ToolResultOutcome.SUCCESS.value
                        for o in state.observations)
                    if prior_success:
                        self._mark_current_done(state)
                        return self._terminate(
                            state, task, AgentStatus.COMPLETED,
                            StopReason.BOUNDED_DATA,
                            enforced.reason, _now, plan=plan,
                            artifacts=produced_artifacts)
                    return self._terminate(
                        state, task, AgentStatus.FAILED,
                        StopReason.REPLAN_FAILURE,
                        "The investigation could not re-plan within the "
                        "supported surface after the latest observation.",
                        _now, plan=plan,
                        artifacts=produced_artifacts)
                plan = new_plan
                self._adopt_plan(state, plan)
                self._obs_count_at_adoption = len(state.observations)
                # C1/B: a re-plan adoption consumes iteration budget —
                # repeated re-plans (empty or non-productive) can no longer
                # bypass the max_iterations bound (which previously counted
                # only step executions, leaving re-plan-only cycles
                # unbounded).
                state.iteration += 1                # GSP WP-GSP-7: the SAME accepted goal_contract governs the
                # re-plan — deterministic coverage re-admission (pure
                # recompute from the registry) rebuilds the step→output map
                # and re-marks NO_ELIGIBLE outputs. The contract itself is
                # never modified by a re-plan; PENDING outputs left
                # uncovered are recorded as NOT_COVERED_BY_PLAN (explicit
                # planning omission — never silently dropped).
                from app.config import settings as _gsp_cfg
                if getattr(_gsp_cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", False) \
                        and state.goal_contract is not None:
                    from app.planning_coverage import admit_plan_coverage
                    coverage = admit_plan_coverage(
                        state.goal_contract,
                        [s.type for s in plan.steps])
                    state.planning_coverage = [
                        {"output_id": r.output_id,
                         "coverage_status": r.coverage_status,
                         "eligible_step_types": r.eligible_step_types,
                         "matched_evidence_types": r.matched_evidence_types,
                         "reason": r.reason}
                        for r in coverage]
                    map_entries: dict[str, list[str]] = {}
                    for step in plan.steps:
                        mapped = sorted({
                            r.output_id for r in coverage
                            if step.type in r.eligible_step_types
                            and _step_matches_output_subject(
                                step, r.output_id, plan.steps,
                                state.goal_contract)})
                        if mapped:
                            map_entries[step.step_id] = mapped
                    state.step_output_map = map_entries

                # R2a: a re-plan step whose covering outputs are ALL
                # already SATISFIED delivers nothing new — drop it instead
                # of re-executing a completed delivery (the same stream
                # must never produce a second duplicate artifact).
                if state.goal_contract is not None:
                    satisfied_ids = {
                        o.output_id for o in state.goal_contract.outputs
                        if o.satisfaction == OutputSatisfaction.SATISFIED}
                    _kept, _dropped = [], []
                    for _s in plan.steps:
                        # coverage: the step's own mapping, or — for scoped
                        # artifact steps — the union of their SOURCE steps'
                        # mappings (an artifact delivers what its source
                        # step retrieves).
                        mapped = set((state.step_output_map or {}).get(
                            _s.step_id, []))
                        for sid in (_s.arguments or {}).get(
                                "source_step_ids") or []:
                            mapped |= set((state.step_output_map or {}).get(
                                sid, []))
                        if mapped and mapped <= satisfied_ids:
                            _dropped.append(_s.step_id)   # delivers nothing new
                            continue
                        _kept.append(_s)
                    plan.steps = _kept
                    # keep the state mirror CONSISTENT with plan.steps: the
                    # step selector walks state.plan_steps and looks each id
                    # up in plan.steps — a stale ref there crashes the loop
                    # (StopIteration) instead of skipping completed work.
                    _dropped = set(_dropped)
                    state.plan_steps = [
                        ref for ref in state.plan_steps
                        if ref.get("step_id") not in _dropped]
                    if not plan.steps:
                        # everything the re-plan offered only re-delivered
                        # satisfied outputs → bounded partial completion
                        # (evidence retained; nothing new to execute)
                        self._mark_current_done(state)
                        return self._terminate(
                            state, task, AgentStatus.COMPLETED,
                            StopReason.BOUNDED_DATA,
                            "re-plan offered no new work beyond already-"
                            "delivered outputs; terminating with the "
                            "evidence acquired", _now, plan=plan,
                            artifacts=produced_artifacts)
                self._emit("plan.created", state, task,
                           summary=f"re-plan {plan.plan_id} "
                                   f"({[s.type for s in plan.steps]})")
                self._persist(state, task)
                continue
            # CONTINUE
            self._mark_current_done(state)
            self._emit("step.completed", state, task,
                       step_id=(state.current_step or {}).get("step_id"),
                       step_type=(state.current_step or {}).get("type"),
                       iteration=state.iteration)
            self._persist(state, task)

    # --- helpers --------------------------------------------------------------

    def _mark_current_done(self, state: AgentLoopState) -> None:
        current = state.current_step
        if current and current.get("step_id") \
                and current["step_id"] not in state.completed_step_ids:
            state.completed_step_ids.append(current["step_id"])

    def _supplement_plan_coverage(self, state: AgentLoopState, plan: Plan) \
            -> None:
        """GSP-6 coverage completion at INITIAL plan adoption (deterministic,
        general over every planning path): for each accepted contract output
        whose covering step the proposed plan omitted, append its registry
        eligible step ONCE (subject-pinned for finding subjects). Flag OFF =
        byte-for-byte pre-GSP behavior. Completion runs BEFORE coverage
        admission, so admission, the step→output map, and the user-visible
        record all describe a complete plan."""
        from app.config import settings as _cfg
        if not getattr(_cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", False):
            return
        contract = state.goal_contract
        if contract is None:
            return
        from app.planning_coverage import (coverage_insert_index,
                                           coverage_supplement_steps)
        specs = coverage_supplement_steps(contract, plan.steps)
        if not specs:
            return
        from app.models import PlanStep as _PlanStep
        suffixes = [int(m.group(1)) for m in
                    (re.match(r"^S(\d+)$", s.step_id or "")
                     for s in plan.steps) if m]
        next_id = (max(suffixes) if suffixes else len(plan.steps)) + 1
        appended: list[tuple] = []
        for spec in specs:
            binding = STEP_TOOL_MAP[spec.step_type]
            args = dict(binding.parameter_locks)
            if spec.finding_target:
                args["finding_id"] = spec.finding_target
            step = _PlanStep(
                step_id=f"S{next_id}", type=spec.step_type,
                reason=(f"coverage supplement: contract output "
                        f"{spec.output_ids[0]} not covered by the "
                        "proposed plan"),
                status=PlanStepStatus.PENDING,
                tool_name=binding.tool_name,
                arguments=args)
            next_id += 1
            # evidence steps land BEFORE a trailing artifact step so the
            # composed bundle can carry them (answer/artifact consistency)
            plan.steps.insert(coverage_insert_index(plan.steps), step)
            appended.append((spec, step))
        _telemetry.emit_event(
            "planning.coverage.supplemented",
            investigation_id=state.investigation_id,
            task_id=state.task_id,
            contract_id=contract.contract_id,
            decision="supplemented",
            summary=json.dumps(
                {spec.output_ids[0]: spec.step_type
                 for spec, _ in appended}, ensure_ascii=False),
            guard_enabled=True)
        logger.info(
            "GSP-6 coverage supplement: appended %s",
            [(s.step_type, s.output_ids) for s, _ in appended])

    def _plan(self, planner, goal, ctx, eligible_ids, caps,
              goal_contract=None, findings=None):
        """Returns (plan | None, bounded_failure_code | None). The failure
        code surfaces in the loop state so planning failures are
        diagnosable from the activity trace (QA-01 retest observability)."""
        if planner is None:
            from app.planner_v2 import PlannerV2
            planner = PlannerV2()
        result = planner.plan(goal, ctx, eligible_ids,
                              finding_capabilities=caps,
                              goal_contract=goal_contract,
                              case_findings=findings)
        if hasattr(result, "steps"):
            return result, None
        return None, getattr(result, "code", None) or "PLANNING_FAILURE"

    def _re_plan(self, planner, state, goal, ctx, eligible_ids, caps,
                 findings=None,
                 goal_contract=None) -> Plan | None:
        """Receding-horizon re-plan (baseline §10): PlannerV2 again, with the
        original goal + a COMPACT progress digest (never raw tool results).
        The new plan passes the same registry/contract validation.

        GSP WP-GSP-7 (flag ON): the SAME accepted goal_contract threads
        through the re-plan (the planner prompt repeats the REQUIRED
        OUTPUTS block) — a re-plan never re-interprets the goal. Machine
        facts (this run's confirmed-unsupported paths, keyed by step type)
        are added to the digest, and the proposed steps are
        deterministically filtered: a step repeating an
        already-confirmed-unsupported path is removed; a re-plan left with
        no steps returns None (→ bounded REPLAN_FAILURE) instead of
        re-executing a known-bad path. Run-scoped only — no registry
        change, no permanent blacklist; retries (transient errors) are a
        separate mechanism and are unaffected. Flag OFF = the baseline
        digest and re-plan behavior, unchanged."""
        if planner is None:
            from app.planner_v2 import PlannerV2
            planner = PlannerV2()
        # The digest header marker is planner-owned: PlannerV2 strips the
        # digest when it reads the USER'S words for intent/specialization
        # (REPLAN_DIGEST_PREFIX) — tool names in digest lines carry intent
        # vocabulary and must never route deterministic planning.
        from app.planner_v2 import REPLAN_DIGEST_PREFIX
        digest_lines = [
            f"- completed: {[s.get('type') for s in state.plan_steps if s.get('status') == 'success']}",
        ]
        for obs in state.observations[-2:]:
            digest_lines.append(f"- observation: {_observation_line(obs)}")
        for d in state.decisions[-1:]:
            digest_lines.append(f"- last decision: {d.get('decision')}"
                                f" ({d.get('reason')})")
        from app.config import settings as _gsp_cfg
        gsp_on = bool(getattr(_gsp_cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                              False))
        # GSP-7 machine facts: this run's confirmed-unsupported PATHS.
        # The path identity is the STEP TYPE (binding tool + locked view) —
        # never the tool globally: a finding_drilldown UNSUPPORTED on one
        # view must not invalidate the same tool's other views.
        unsupported_steps: list[str] = []
        if gsp_on:
            unsupported_steps = sorted({
                o.get("step_type") for o in state.observations
                if o.get("outcome") == ToolResultOutcome.UNSUPPORTED.value
                and o.get("step_type")})
            if unsupported_steps:
                digest_lines.append(
                    "- machine fact: these investigation paths returned "
                    f"UNSUPPORTED earlier in this run: {unsupported_steps} "
                    "— do not plan them again for the same scope.")
        replan_request = (f"{goal}\n\n"
                          f"{REPLAN_DIGEST_PREFIX} — compact summary]\n"
                          + "\n".join(digest_lines))
        result = planner.plan(replan_request, ctx, eligible_ids,
                              finding_capabilities=caps,
                              goal_contract=goal_contract,
                              case_findings=findings)
        if not hasattr(result, "steps"):
            return None
        if not result.steps:
            # C1/B: a re-plan that produces NO executable steps must not be
            # adopted — adopting it leaves the loop with zero pending steps
            # and a decision point that re-plans forever (live incident:
            # dozens of empty re-plans, iteration frozen at 2 of 8). None →
            # the caller's bounded BOUNDED_DATA / REPLAN_FAILURE terminal.
            logger.warning(
                "Re-plan produced no executable steps; treating as "
                "re-plan failure")
            return None
        # GSP-7 deterministic enforcement (flag ON): a re-planned step
        # repeating an already-confirmed-unsupported path of THIS run is
        # stripped — the system disposes; the LLM cannot re-propose a
        # known-bad path into the adopted plan. Run-scoped only (no
        # registry/blacklist change); retries (transient errors) are a
        # separate mechanism and are unaffected.
        if gsp_on and unsupported_steps:
            kept, removed = [], []
            for s in result.steps:
                if s.type in unsupported_steps:
                    removed.append(s.type)
                    continue
                kept.append(s)
            if removed:
                logger.warning(
                    "GSP-7 re-plan filter: stripped repeat-unsupported "
                    "steps %s", removed)
                result.steps = kept
                if not result.steps:
                    return None     # nothing viable remains → bounded end

        # Bounded deterministic supplement (deliverable-completeness fix):
        # accepted contract outputs that (a) are not yet SATISFIED and
        # (b) the re-planned script does not cover, get their registry
        # eligible steps appended ONCE — the SAME subject-aware helper the
        # initial adoption uses (planning_coverage.coverage_supplement_steps).
        # One pass per re-plan, still bounded by the loop's iteration cap;
        # never an unbounded loop, never a threshold change.
        if gsp_on and state.goal_contract is not None:
            from app.planning_coverage import (coverage_insert_index,
                                               coverage_supplement_steps)
            from app.models import PlanStep as _PlanStep
            specs = coverage_supplement_steps(
                state.goal_contract, result.steps, skip_satisfied=True)
            if specs:
                _sid = len(result.steps)
                for spec in specs:
                    _sid += 1
                    binding = STEP_TOOL_MAP[spec.step_type]
                    args = dict(binding.parameter_locks)
                    if spec.finding_target:
                        args["finding_id"] = spec.finding_target
                    result.steps.insert(
                        coverage_insert_index(result.steps),
                        _PlanStep(
                            step_id=f"S{_sid}", type=spec.step_type,
                            reason=("bounded supplement: contract output "
                                    f"{spec.output_ids[0]} not covered by "
                                    "the re-planned script"),
                            status=PlanStepStatus.PENDING,
                            tool_name=binding.tool_name,
                            arguments=args))
                    logger.info(
                        "GSP bounded supplement: appended %s for output %s",
                        spec.step_type, spec.output_ids[0])
        return result

    def _revision_acquisition_added_no_new_info(self, state) -> bool:
        """U00047 fix A: TRUE when every SUCCESS acquisition observation
        recorded since the last plan adoption duplicates an observation
        from BEFORE that adoption — the revision re-ran answered work and
        learned nothing new (composition-only observations, e.g. the
        artifact bundle, don't count: they reuse acquired evidence)."""
        start = getattr(self, "_obs_count_at_adoption", None)
        if start is None or start >= len(state.observations):
            return False
        prior_fps = {_observation_fingerprint(o)
                     for o in state.observations[:start]}
        for o in state.observations[start:]:
            if o.get("step_type") == "generate_artifact":
                continue          # composition, not acquisition
            if o.get("outcome") != ToolResultOutcome.SUCCESS.value:
                continue
            fp = _observation_fingerprint(o)
            if fp and fp not in prior_fps:
                return False      # genuinely new acquisition this revision
        return True

    def _sync_plan_mirror(self, state: AgentLoopState, plan: Plan) -> None:
        """Mirror executor-mutated PlanStep statuses into the state
        snapshot. BOTH readers depend on it: the step selector (loop top)
        and — since the U00047 no-progress fix — the decision layer, which
        must never coerce a decision from a mirror that still lists the
        just-executed step as pending."""
        for ps in plan.steps:
            for ref in state.plan_steps:
                if ref["step_id"] == ps.step_id:
                    ref["status"] = ps.status.value

    def _adopt_plan(self, state: AgentLoopState, plan: Plan) -> None:
        state.plan_id = plan.plan_id
        state.plan_revisions += 1
        # snapshot carries arguments: required for AL-1 recovery (a
        # resumed pending step must re-execute with its original args)
        state.plan_steps = [
            {"step_id": s.step_id, "type": s.type,
             "status": s.status.value, "tool_name": s.tool_name,
             "arguments": dict(s.arguments)}
            for s in plan.steps
        ]
        state.current_step = None

    def _retryable(self, tool_call: ToolCallV2 | None,
                   exec_error: ExecutorError | None,
                   state: AgentLoopState, step) -> bool:
        """Bounded retry: transient integration failures only, ≤ cap per
        step, never semantic results (baseline §8)."""
        if tool_call is None or tool_call.result is None:
            return False
        result = tool_call.result
        if result.outcome != ToolResultOutcome.INTEGRATION_ERROR:
            return False
        code = result.error.code if result.error else ""
        if code not in RETRYABLE_ERROR_CODES:
            return False
        used = state.retries.get(step.step_id, 0)
        return used < _max_retries()

    def _block_reason(self, state: AgentLoopState,
                      proposal: DecisionProposal) -> str:
        last = state.last_observation() or {}
        outcome = last.get("outcome")
        if outcome == ToolResultOutcome.UNSUPPORTED.value:
            return StopReason.CAPABILITY_MISSING
        if outcome == ToolResultOutcome.INTEGRATION_ERROR.value:
            return StopReason.PERSISTENT_ERROR
        if outcome == ToolResultOutcome.VALIDATION_ERROR.value:
            return StopReason.NO_VIABLE_ACTION
        if (last.get("summary") or {}).get("evidence_missing"):
            return StopReason.DATA_UNAVAILABLE
        return StopReason.NO_VIABLE_ACTION

    def _terminate(self, state: AgentLoopState, task: TaskV2,
                   status: AgentStatus, stop_reason: str,
                   response: str, _now, plan: Plan | None = None,
                   artifacts: list[dict[str, Any]] | None = None
                   ) -> AgentLoopResult:
        # Final plan-snapshot sync (QA-02): the persisted plan/step state
        # must reflect the runtime at terminal — executed steps are
        # success/failed as the executor left them, pending stays pending,
        # superseded stays superseded.
        if plan is not None:
            for ps in plan.steps:
                for ref in state.plan_steps:
                    if ref["step_id"] == ps.step_id:
                        ref["status"] = ps.status.value
        # Terminal event FIRST, then the terminal status flip + persist:
        # a poll that observes is_terminal is guaranteed to also see the
        # terminal activity row (Agent Activity reconstruction contract).
        event = {
            AgentStatus.COMPLETED: "agent.completed",
            AgentStatus.BLOCKED: "agent.blocked",
            AgentStatus.STOPPED_BY_LIMIT: "agent.stopped",
        }.get(status, "agent.failed")
        self._emit(event, state, task, agent_status=status.value,
                   stop_reason=stop_reason, summary=response[:300] or None)
        state.agent_status = status.value
        state.stop_reason = stop_reason
        # the canonical status phrase leads the user-facing response; the
        # branch-specific detail (clarification text, block reason) rides
        # as context
        state.response = response[:600]
        state.updated_at = _now()
        task.completed_at = _now()
        if status == AgentStatus.COMPLETED:
            task.status = TaskStatusV2.COMPLETED
        elif status == AgentStatus.FAILED:
            task.status = TaskStatusV2.FAILED
            task.error = response
        else:
            # blocked / stopped_by_limit: the task container stays coarse;
            # the Agent State carries the precise terminal semantics and the
            # response explains WHY (baseline §13/§20).
            task.status = TaskStatusV2.FAILED
            task.error = f"{status.value}: {response}"
        # R2a closure: persist the terminal GoalOutcome. Previously the
        # value was only written by the composer AFTER this persist (the
        # served Activity derived it on the fly), so blocked/failed
        # terminals persisted goal_outcome=NULL. Same derivation and
        # semantics the composer uses — never a second classifier; the
        # composer's later write is identical by construction.
        if state.goal_contract is not None and state.goal_outcome is None:
            from app.goal_postconditions import derive_goal_outcome
            state.goal_outcome = derive_goal_outcome(
                [o.satisfaction for o in state.goal_contract.outputs],
                state.agent_status,
                intent_source=state.goal_contract.intent_source).value
        self._persist(state, task)
        return AgentLoopResult(state=state,
                               response=compose_loop_response(state),
                               task=task,
                               artifacts=list(artifacts or []))

    def _apply_records_artifact_satisfaction(
            self, state, task, provenance_pool, produced_artifacts):
        """R2a — records-artifact deliverable satisfaction.

        For every contract output whose covering plan includes a
        generate_artifact step with `source_step_ids`, verify against the
        REAL evidence: the source stream step succeeded and returned
        records; a produced artifact declares those source step(s); the
        artifact content contains the source record IDs. Only then is the
        output SATISFIED (artifact id + record count as the user-safe
        detail). Failures keep the output PENDING with an honest detail —
        an artifact ID alone never satisfies, and a findings-only bundle
        (no source records) is never a records delivery."""
        if state.goal_contract is None:
            return
        calls_by_step = {}
        for tc in provenance_pool or []:
            m = re.match(r"TC-(S\d+)-", tc.tool_call_id or "")
            if m:
                calls_by_step.setdefault(m.group(1), []).append(tc)
        artifacts = [a for a in (produced_artifacts or [])
                     if isinstance(a, dict)]
        for out in state.goal_contract.outputs:
            if out.satisfaction == OutputSatisfaction.SATISFIED:
                continue
            if not any(t.value in ("withdrawal_records",
                                   "transaction_records")
                       for t in (out.evidence_requirements or [])):
                continue
            artifact_steps = [
                s for s in state.plan_steps
                if s.get("type") == "generate_artifact"
                and s.get("status") == PlanStepStatus.SUCCESS.value
                and set((state.step_output_map or {}).get(
                    s.get("step_id"), [])) & {out.output_id}]
            if not artifact_steps:
                # R2a candidate-deliverable path (U00047 fix B): a
                # records-kind output whose covering evidence is CANDIDATE
                # cross-account analysis has no artifact step in the
                # admission mapping (generate_artifact declares no
                # records/opposite-trade evidence types) — bind it through
                # the artifact's OWN provenance instead.
                self._apply_candidate_artifact_satisfaction(
                    state, out, calls_by_step, artifacts)
                continue
            verified = False
            detail = None
            for astep in artifact_steps:
                source_ids = (astep.get("arguments") or {}).get(
                    "source_step_ids") or []
                source_records = []
                for sid in source_ids:
                    for tc in calls_by_step.get(sid, []):
                        data = tc.result.data if (
                            tc.result is not None
                            and isinstance(tc.result.data, dict)) else {}
                        for r in (data.get("records") or []):
                            rid = (r.get("record_id")
                                   if isinstance(r, dict) else None) \
                                or str(r)
                            source_records.append(str(rid))
                if not source_records:
                    detail = (f"来源步骤 {source_ids} 未成功检索到记录，"
                              "无法生成本项明细交付。")
                    continue
                match = next((a for a in artifacts
                              if all(rid in (a.get("content") or "")
                                     for rid in set(source_records))), None)
                if match is None:
                    detail = ("明细 artifact 尚未生成或未绑定到本交付项的"
                              "来源步骤。")
                    continue
                content = match.get("content") or ""
                missing = [rid for rid in set(source_records)
                           if rid not in content]
                if missing:
                    detail = (f"artifact 内容验证失败：缺少 {len(missing)} "
                              f"条来源记录（如 {missing[:3]}）。")
                    continue
                verified = True
                detail = (f"明细 artifact {match.get('artifact_id')} 已交付："
                          f"{len(set(source_records))} 条记录与来源检索"
                          "一致。")
                break
            if verified:
                out.satisfaction = OutputSatisfaction.SATISFIED
                self._artifact_satisfied_outputs.add(out.output_id)
            if detail and out.satisfaction != OutputSatisfaction.SATISFIED:
                out.detail = detail

    def _write_contract_result(self, state: AgentLoopState, out, res,
                               satisfaction, polarity) -> None:
        """SINGLE satisfaction write point for one evaluator result.
        R2a monotonicity (U00047 fix B): the satisfaction of a records-kind
        deliverable established by artifact CONTENT verification is
        authoritative — the delivered file persists unchanged, so a later
        observation-level re-evaluation (e.g. a re-plan's repeated
        analysis observation) must not downgrade it."""
        if out.output_id != res.output_id:
            return
        if (out.satisfaction == OutputSatisfaction.SATISFIED
                and out.output_id in self._artifact_satisfied_outputs):
            return
        out.satisfaction = satisfaction
        out.polarity = polarity
        out.detail = res.detail

    def _apply_candidate_artifact_satisfaction(
            self, state: AgentLoopState, out, calls_by_step: dict,
            artifacts: list) -> None:
        """R2a candidate-deliverable satisfaction (U00047 fix B).

        A records-kind output delivered as a candidate-detail file (e.g.
        "生成并交付一份包含上述对冲交易明细的交易明细文件") has no
        generate_artifact step mapped in step_output_map — the registry
        binding declares only CASE_SUMMARY for artifact steps — so the
        records-stream path above can never reach it. The binding that
        DOES exist is the artifact's own provenance: artifact_bundle's
        composition contract records exactly the contributing source
        calls (source_tool_calls), and those calls' steps are tracked in
        the admission-time step_output_map for this output.

        Satisfaction therefore requires ALL of:
        - a successful candidate-analysis call whose step maps to THIS
          output in the tracked mapping;
        - a produced artifact whose declared source_tool_calls include
          that call (provenance binding, never invented);
        - the rendered content contains EVERY found candidate's
          supporting transaction IDs (content verification);
        - when the source retrieval was truncated, the bounded-retrieval
          disclosure is present in the file (bounded honesty — a silent
          bundle is not a complete delivery).
        The artifact ID alone never satisfies, and the verdict detail is
        the delivery's own wording — never a copy of the existence-check
        output's detail."""
        mapped_steps = {sid for sid, outs in (
            state.step_output_map or {}).items()
            if out.output_id in (outs or [])}
        source_calls: list = []
        truncated = False
        for sid, tcs in (calls_by_step or {}).items():
            if sid not in mapped_steps:
                continue
            for tc in tcs:
                if tc.result is None or not isinstance(
                        tc.result.data, dict):
                    continue
                if tc.result.outcome != ToolResultOutcome.SUCCESS:
                    continue
                data = tc.result.data
                if data.get("analysis") != "cross_account_trade_analysis":
                    continue
                if not (data.get("candidates") or []):
                    continue
                source_calls.append(tc)
                retrieval = data.get("retrieval") or {}
                truncated = truncated or bool(retrieval.get("truncated"))
        if not source_calls:
            return
        source_ids = {tc.tool_call_id for tc in source_calls}
        delivered = None
        for a in artifacts or []:
            if not isinstance(a, dict):
                continue
            if set(a.get("source_tool_calls") or []) & source_ids \
                    and (a.get("content") or "").strip():
                delivered = a
        if delivered is None:
            out.detail = ("候选明细文件尚未生成本次运行的交付物；已获得的"
                          "候选证据见对话回答。")
            return
        content = delivered.get("content") or ""
        expected_ids: set[str] = set()
        candidate_count = 0
        for tc in source_calls:
            data = tc.result.data or {}
            for c in (data.get("candidates") or []):
                if not isinstance(c, dict):
                    continue
                candidate_count += 1
                for r in (c.get("supporting") or []):
                    tid = r.get("transaction_id") if isinstance(r, dict) \
                        else str(r)
                    if tid:
                        expected_ids.add(str(tid))
        missing = [t for t in sorted(expected_ids) if t not in content]
        if missing:
            out.detail = (
                f"明细文件 {delivered.get('artifact_id')} 内容验证失败："
                f"缺少 {len(missing)} 条候选交易引用（如 {missing[:3]}），"
                "不能视为完整交付。")
            return
        if truncated and "Retrieval was bounded" not in content:
            out.detail = (
                f"明细文件 {delivered.get('artifact_id')} 未包含检索边界"
                "披露；来源检索不完整时不能视为完整交付。")
            return
        out.satisfaction = OutputSatisfaction.SATISFIED
        out.detail = (
            f"明细文件 {delivered.get('artifact_id')} 已交付："
            f"{candidate_count} 组候选明细（{len(expected_ids)} 条交易"
            "证据引用）与来源集合分析逐条一致"
            + ("；集合检索受预算限制的边界已在文件中如实披露。" if truncated
               else "。"))
        self._artifact_satisfied_outputs.add(out.output_id)

    def _persist(self, state: AgentLoopState, task: TaskV2) -> None:
        from datetime import datetime, timezone
        state.updated_at = datetime.now(timezone.utc).isoformat()
        if self.store is not None:
            try:
                self.store.save_agent_state(state)
                self.store.update(task)
            except Exception:
                logger.exception("Failed to persist agent loop state")

    def _emit(self, event_type: str, state: AgentLoopState, task: TaskV2,
              **fields: Any) -> None:
        from datetime import datetime, timezone
        summary = (fields.pop("summary", None) or "")[:300] or None
        # WP-CG-3: audit marker on decision rows ("contract:<id>" | "none")
        # — activity-only state, consumed before telemetry passthrough.
        semantic_basis = fields.pop("semantic_basis", None)
        record = {
            "seq": None,               # assigned below
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "iteration": fields.get("iteration"),
            "step_id": fields.get("step_id"),
            "step_type": fields.get("step_type"),
            "tool_name": fields.get("tool_name"),
            "outcome": fields.get("outcome"),
            "decision": fields.get("decision"),
            "agent_status": fields.get("agent_status"),
            "stop_reason": fields.get("stop_reason"),
            "summary": summary,
            "semantic_basis": semantic_basis,
            "artifact_ids": list(state.artifact_ids),
        }
        # Mirror the structured event into the persisted, UI-safe activity
        # record (Agent Activity baseline §15): same transition, bounded
        # list, sequence-ordered. The telemetry emission itself stays
        # observational/best-effort and is unchanged.
        state.activity_seq += 1
        record["seq"] = state.activity_seq
        state.activity.append(record)
        if len(state.activity) > _MAX_ACTIVITY_KEPT:
            state.activity = state.activity[-_MAX_ACTIVITY_KEPT:]
        # the mirrored keys are consumed; only unmirrored extras (e.g.
        # retry_count) may pass through to the telemetry event
        for key in ("iteration", "step_id", "step_type", "tool_name",
                    "outcome", "decision", "agent_status", "stop_reason"):
            fields.pop(key, None)
        _telemetry.emit(_telemetry.AgentTelemetryEvent(
            event_type=event_type,
            investigation_id=state.investigation_id,
            case_id=state.case_id,
            focused_finding_id=state.focused_finding_id,
            task_id=state.task_id,
            iteration=record["iteration"],
            step_id=record["step_id"],
            step_type=record["step_type"],
            tool_name=record["tool_name"],
            outcome=record["outcome"],
            decision=record["decision"],
            agent_status=record["agent_status"],
            stop_reason=record["stop_reason"],
            retry_count=fields.pop("retry_count", None),
            artifact_ids=state.artifact_ids or None,
            summary=summary,
            **fields,
        ))


# ---------------------------------------------------------------------------
# User-safe terminal response (bounded; explains WHY execution stopped)
# ---------------------------------------------------------------------------

_STATUS_PHRASES = {
    AgentStatus.COMPLETED.value: "The investigation completed.",
    AgentStatus.STOPPED_BY_LIMIT.value: (
        "The investigation stopped at the maximum number of bounded "
        "iterations. This is a bounded stop, not a completed result; "
        "partial findings are preserved."),
    AgentStatus.BLOCKED.value: (
        "The investigation is blocked and cannot progress."),
    AgentStatus.FAILED.value: (
        "The investigation failed and was recorded as such."),
}


def _humanize_threshold(threshold: Any) -> str:
    """Humanize a raw rule threshold expression with the shared feature
    labels (deterministic echo — never invented semantics)."""
    thr = str(threshold)
    try:
        from app.investigation_service import _FEATURE_LABELS
        for raw, label in _FEATURE_LABELS.items():
            thr = thr.replace(raw, label)
    except Exception:
        pass
    return thr


def _signal_answer_lines(s: dict[str, Any],
                         lang: str = "zh") -> list[str]:
    """The WHY of a finding, in user language, grounded in the payload:
    ML → method/score-semantics/feature-context/attribution-boundary;
    rule → observed-vs-threshold semantics; feature → the real feature
    facts; graph → the real cluster attribution. Never just the title.
    NOTE: the ML branch takes precedence — an ML signal ALWAYS reports
    evidence_missing (attribution does not exist in the RP API), and the
    apology line would otherwise bury the real explanation."""
    if s.get("ml_score") is not None or s.get("detection_method"):
        # AL-2.13: the ML explanation in user language, from authoritative
        # payload fields only — what ML is, what the score means, the
        # account's real feature context, and the attribution boundary.
        method = s.get("detection_method")
        lines: list[str] = []
        if lang == "en":
            lines.append(
                "ML Pattern Detection means the Risk Platform uses "
                "machine learning"
                + (f" (detection method: {method})" if method else "")
                + " to recognize patterns associated with elevated risk "
                "in the account's behavior, and presents it as a risk "
                "finding.")
            if s.get("ml_score") is not None:
                interp = s.get("score_interpretation") or \
                    "system signal, not a calibrated probability"
                lines.append(
                    f"This account's ML signal score is {s['ml_score']}"
                    f"/100 — the strength of the system's detection "
                    f"signal ({interp}); it is NOT a calibrated "
                    "probability of fraud.")
            features = s.get("available_feature_values") or {}
            if features:
                shown = ", ".join(
                    f"{_feature_label(k)} = {v}"
                    for k, v in list(features.items())[:5])
                more = (f" (first 5 of {len(features)} features; the "
                        "remaining values are in the Risk Platform "
                        "feature_evidence)"
                        if len(features) > 5 else "")
                lines.append(
                    "This account's actual feature values (context for "
                    "reference; they did not necessarily cause the "
                    f"score): {shown}{more}.")
            if s.get("attribution_available") is False:
                lines.append(
                    "Attribution boundary: the Risk Platform provides no "
                    "per-feature contribution attribution for this model "
                    "score, so it cannot be stated how much any specific "
                    "feature contributed.")
            return lines
        lines.append(
            "ML Pattern Detection 的含义:Risk Platform 使用机器学习"
            + (f"（检测方法:{method}）" if method else "")
            + "从账户行为模式中识别与风险升高相关的模式,并将其作为"
            "一个风险发现呈现。")
        if s.get("ml_score") is not None:
            interp = s.get("score_interpretation") or \
                "system signal, not a calibrated probability"
            lines.append(
                f"本账号的 ML 信号分数为 {s['ml_score']}/100 —— 这是系统"
                f"检测信号的强度({interp}),不是校准过的欺诈概率。")
        features = s.get("available_feature_values") or {}
        if features:
            # list-completeness labeling: a partial display must say so —
            # "前 N 项(共 M 项)" never reads as the complete list
            shown = ", ".join(
                f"{_feature_label(k)} = {v}"
                for k, v in list(features.items())[:5])
            more = (f"(前 5 项,共 {len(features)} 项;其余特征值见"
                    f" Risk Platform feature_evidence)"
                    if len(features) > 5 else "")
            lines.append(
                "该账号的实际特征值(供参考的上下文,不代表它们导致了"
                f"该分数):{shown}{more}。")
        if s.get("attribution_available") is False:
            lines.append(
                "归因边界:当前 Risk Platform 没有提供这个模型分数对应的"
                "特征归因,因此不能准确断言某一个具体特征贡献了多少分。")
        return lines
    if s.get("evidence_missing"):
        return ["信号解释:Risk Platform 未提供该发现的规则/模型归因明细"
                "(数据不足,如实报告);上方为本发现自身的信号与摘要。"]
    lines: list[str] = []
    if s.get("rule_name"):
        trig = s.get("trigger_values") or {}
        observed = ", ".join(
            f"{_feature_label(k)} = {v}" for k, v in trig.items())
        desc = (s.get("rule_description") or "").strip()
        parts = [f"风险信号:规则「{s['rule_name']}」触发"]
        if observed:
            parts.append(f"观测到 {observed}")
        if desc:
            parts.append(f"规则说明:「{desc}」")
        if s.get("threshold"):
            parts.append(f"规则条件为 {_humanize_threshold(s['threshold'])}")
        if s.get("contribution") is not None:
            parts.append(f"对该风险评分的贡献度为 "
                         f"{s['contribution']}")
        lines.append(" — ".join(parts) + "。即:本次行为满足了这条规则的"
                     "触发条件,因此被标记为风险项。")
        return lines
    if s.get("cluster_name") or s.get("detection_type"):
        parts = []
        if s.get("cluster_name"):
            parts.append(f"cluster 「{s['cluster_name']}」")
        if s.get("detection_type"):
            parts.append(f"detection type “{s['detection_type']}”")
        if s.get("member_count") is not None:
            parts.append(f"{s['member_count']} accounts in the cluster")
        if s.get("cluster_risk_score") is not None:
            parts.append(f"cluster risk score {s['cluster_risk_score']}")
        lines.append("风险信号:图关系检测发现该账号处于风险网络中 — "
                     + ", ".join(parts) + "。")
        devices = s.get("shared_devices") or []
        if devices:
            lines.append("当前可确认的共享设备:"
                         + "; ".join(str(d) for d in devices) + "。")
        accounts = s.get("related_accounts") or []
        if accounts:
            lines.append("相关账号:" + ", ".join(str(a) for a in accounts)
                         + "。")
        return lines
    if s.get("feature_facts"):
        feats = s["feature_facts"]
        shown = ", ".join(
            f"{_feature_label(f.get('name', ''))} = {f.get('value', '')}"
            for f in feats[:5] if isinstance(f, dict) and f.get("name"))
        more = (f"(前 5 项,共 {len(feats)} 项)" if len(feats) > 5 else "")
        rf = s.get("risk_factor") or {}
        severity = (rf.get("severity") if isinstance(rf, dict) else None)
        lines.append("风险信号:该条目为行为特征观察记录"
                     + (f"（风险因子记录，严重度 {severity}）"
                        if severity else "")
                     + "；观测记录的存在不等于规则已触发 — "
                     + (f"观测到 {shown}{more}。" if shown else ""))
        # Consistency II: reconcile the feature-window metric with the
        # broader exposed detail population (both numbers traceable).
        lines.extend(_render_population_reconciliation(s))
        finding_statement = (s.get("finding_summary") or "").strip()
        if finding_statement:
            lines.append(f"Risk Platform 对该风险发现的权威说明: "
                         f"{finding_statement}")
        lines.append("该账号的实际行为特征值是 Risk Platform 标记此风险"
                     "发现的依据。当前 RP 数据不提供特征归因,因此不能"
                     "断言某一个具体特征贡献了多少分;系统不重构、不推断"
                     "阈值或触发状态。")
        return lines
    return lines


def _feature_label(key: str) -> str:
    try:
        from app.investigation_service import _FEATURE_LABELS
        return _FEATURE_LABELS.get(key, key)
    except Exception:
        return key


def _evidence_answer_lines(state: AgentLoopState) -> list[str]:
    """COMPLETE-scope evidence rendering (AL-2.12 Issue C): an explicit
    detail request is answered by the FULL record set the tool returned —
    as a compact table — never by examples. Bounded display (20 rows) is
    disclosed explicitly and never presented as complete. Identical
    repeated observations render once (AL-2.13)."""
    lines: list[str] = []
    empty_streams: list[str] = []
    seen: set[str] = set()
    for obs in state.observations:
        if obs.get("tool_name") != "finding_drilldown":
            continue
        if obs.get("outcome") not in (ToolResultOutcome.SUCCESS.value,
                                      ToolResultOutcome.EMPTY.value):
            continue
        fp = _observation_fingerprint(obs)
        if fp and fp in seen:
            continue                        # identical result — deduped
        if fp:
            seen.add(fp)
        s = obs.get("summary") or {}
        rows = s.get("record_rows") or []
        if not rows:
            if obs.get("outcome") == ToolResultOutcome.EMPTY.value:
                streams = s.get("streams") or {}
                empty_streams.append(
                    "提现" if streams.get("withdrawals_included")
                    else "交易" if streams.get("transactions_included")
                    else "记录级")
            continue
        count = s.get("record_count") or len(rows)
        streams = s.get("streams") or {}
        stream_txt = ("提现记录" if streams.get("withdrawals_included")
                      else "交易记录" if streams.get("transactions_included")
                      else "证据记录")
        lines.append(f"完整证据共 {count} 条({stream_txt})：")
        lines.extend(_render_population_reconciliation(s))
        # WP-GS-B: per-record destination identifiers (when the evidence
        # view's comparison facts are present) surface in the table so
        # users can verify same-vs-different destinations themselves.
        dest_by_record = {}
        for g in ((s.get("destination_identity") or {}).get("groups")
                  or []):
            for rid in (g.get("record_ids") or []):
                dest_by_record[rid] = g
        lines.append("| # | 记录 ID | 时间 | 目的地 | 明细 |")
        lines.append("|---|---|---|---|---|")
        for i, r in enumerate(rows, 1):
            d = dest_by_record.get(r.get("record_id"))
            dest_cell = (f"{d.get('destination_ref', '')} "
                         f"({d.get('address', '')})" if d else "—")
            lines.append(f"| {i} | {r.get('record_id', '')} | "
                         f"{r.get('timestamp', '')} | "
                         f"{dest_cell} | "
                         f"{r.get('summary', '')} |")
        if s.get("rows_truncated"):
            lines.append(f"（显示前 {len(rows)} 条;完整集合共 {count} 条,"
                         "受对话显示上限约束,全量见本任务的证据结果）")
        else:
            lines.append(f"以上为 Risk Platform 权威证据的全部 {count} 条"
                         "记录。")
    return lines, empty_streams


def _case_level_answer_lines(state: AgentLoopState) -> list[str]:
    """AL-2.12 Issue D / AL-2.13: case-level analytical runs answer the
    business question in the conversation. Identical repeated
    observations (a re-plan re-running the same deterministic query) are
    rendered ONCE — a repeated execution is not new information."""
    lines: list[str] = []
    seen: set[str] = set()
    for obs in state.observations:
        if obs.get("tool_name") not in ("cross_account_trade_analysis",
                                        "related_account_analysis"):
            continue
        if obs.get("outcome") != ToolResultOutcome.SUCCESS.value:
            continue
        fp = _observation_fingerprint(obs)
        if fp and fp in seen:
            continue                        # identical result — deduped
        if fp:
            seen.add(fp)
        if obs.get("tool_name") == "cross_account_trade_analysis":
            s = obs.get("summary") or {}
            status = obs.get("semantic_status")
            target = s.get("target_account")
            # Deliverable A — the authoritative same-account metric
            # (baseline §8): the reverse-trade rate is the account's OWN
            # buy/sell balance, never the cross-account match rate. Its
            # meaning is an independent answer part, separate from the
            # cross-account result below.
            metrics = s.get("target_account_metrics") or {}
            ratio = metrics.get("opposite_trade_ratio") \
                if isinstance(metrics, dict) else None
            if ratio is not None and target:
                lines.append(
                    f"关于反向交易率:{ratio:.2%}"
                    "（opposite_trade_ratio）是账户 "
                    f"{target} 自身的反向交易比例:按该账户自己的全部交易"
                    "计算,分子为较少一侧的交易笔数（min(BUY, SELL)）,"
                    "分母为该账户交易总笔数。它衡量的是同一账户内的买卖"
                    "平衡,不是跨账户匹配率,与下面的跨账户调查结果相互"
                    "独立。")
            # Case-4 correction scope echo: what the analysis actually
            # retrieved (target stream + counterparty window stream).
            scope_parts: list[str] = []
            t_ret = s.get("target_retrieval") or {}
            c_ret = s.get("counterparty_retrieval") or {}
            if isinstance(t_ret, dict) and target:
                scope_parts.append(
                    f"目标账户 {target} 的交易 "
                    f"{t_ret.get('transactions_retrieved', 0)} 笔"
                    + ("（检索不完整）" if t_ret.get("truncated")
                       else "（完整检索）"))
            if isinstance(c_ret, dict) and c_ret:
                scope_parts.append(
                    "对手账户在匹配可达时间窗内的交易 "
                    f"{c_ret.get('transactions_retrieved', 0)} 笔"
                    f"（{c_ret.get('accounts_queried', 0)} 个关联账户"
                    + ("，检索不完整）" if c_ret.get("truncated") else "）"))
            if status == "MATCH_FOUND":
                n = s.get("candidates_found")
                if target:
                    lines.append(
                        f"有。关于账户 {target} 的跨账户对冲调查:在当前"
                        f"检索范围内发现 {n} 个涉及 {target} 的候选跨"
                        "账户对冲交易关系（每个候选都包含该账户的一笔"
                        "交易）。")
                else:
                    lines.append(
                        f"有。在当前检索范围内发现 {n} 个候选跨账户对冲"
                        "交易关系。")
                delta = s.get("max_time_delta_seconds")
                rel = s.get("value_tolerance_relative")
                if delta is not None and rel is not None:
                    lines.append(
                        f"匹配条件:两笔交易时间相差不超过 {delta:g} 秒,"
                        f"且价值差异在 {rel:.0%} 以内。这些候选关系是量化"
                        "匹配的调查线索,不构成对操纵或交易意图的结论。")
                if scope_parts:
                    lines.append("分析范围:" + ";".join(scope_parts) + "。")
                retrieval = s.get("retrieval") or {}
                if isinstance(retrieval, dict) and retrieval.get(
                        "truncated"):
                    lines.append(
                        "注意:本次检索受到系统预算限制,覆盖范围不完整,"
                        "因此不能据此证明未检索范围内不存在更多关系。")
                cands = s.get("candidates") or []
                if cands:
                    lines.append("已发现的候选关系如下：")
                    lines.append("| # | 候选 | 账户 A | 方向 | 账户 B | "
                                 "方向 | 币种 | 时间差(s) | 价值 A | "
                                 "价值 B | 差异 | 交易 |")
                    lines.append("|---|---|---|---|---|---|---|---:|---:|"
                                 "---:|---:|---|")
                    for i, cd in enumerate(cands, 1):
                        tx = ", ".join(
                            (cd.get("transaction_ids_a") or [])
                            + (cd.get("transaction_ids_b") or []))
                        lines.append(
                            f"| {i} | {cd.get('candidate_id', '')} | "
                            f"{cd.get('account_a', '')} | "
                            f"{cd.get('side_a', '')} | "
                            f"{cd.get('account_b', '')} | "
                            f"{cd.get('side_b', '')} | "
                            f"{cd.get('symbol', '')} | "
                            f"{cd.get('max_time_gap_seconds', '')} | "
                            f"{cd.get('value_a', '')} | "
                            f"{cd.get('value_b', '')} | "
                            f"{cd.get('value_difference', '')} | {tx} |")
            elif status == "NO_MATCH":
                if target:
                    lines.append(
                        f"没有。在当前检索范围内未发现涉及账户 {target} "
                        "的满足匹配条件的跨账户对冲交易候选关系。该结论"
                        "仅适用于已分析的账户与数据窗口;其他账户之间的"
                        f"候选关系不会作为 {target} 的对冲证据。")
                else:
                    lines.append(
                        "没有。在当前检索范围内未发现满足匹配条件的跨账户"
                        "对冲交易关系。该结论仅适用于已分析的账户与数据"
                        "窗口。")
                if scope_parts:
                    lines.append("分析范围:" + ";".join(scope_parts) + "。")
            elif status == "INSUFFICIENT_DATA":
                lines.append(
                    "无法得出可靠结论:可用交易证据不完整或不足(检索受限或"
                    "为空)。这不是\"未发现\"——是当前数据不足以支持判断。")
                if scope_parts:
                    lines.append("分析范围:" + ";".join(scope_parts) + "。")
        elif obs.get("tool_name") == "related_account_analysis":
            # AL-2.13: the combined request asks TWO questions — why this
            # relationship is a risk signal, and which related accounts
            # exist. Both are answered from the SAME authoritative payload.
            s = obs.get("summary") or {}
            parts = []
            if s.get("detection_type"):
                parts.append(f"detection type “{s['detection_type']}”")
            if s.get("cluster_name"):
                parts.append(f"cluster 「{s['cluster_name']}」")
            if s.get("member_count") is not None:
                parts.append(f"{s['member_count']} accounts in the cluster")
            if s.get("cluster_risk_score") is not None:
                parts.append(f"cluster risk score "
                             f"{s['cluster_risk_score']}")
            if parts:
                lines.append(
                    "为什么是风险信号:Risk Platform 的网络关系检测发现该"
                    "账号与其他账号存在网络/共享设备关系（"
                    + ", ".join(parts) + "）。这类关系会作为需要进一步"
                    "调查的风险信号记录;当前数据本身不能证明这些账号之间"
                    "存在违规或恶意意图。")
            total = s.get("related_accounts_count")
            accounts = s.get("related_accounts") or []
            if total and len(accounts) < total:
                lines.append(
                    f"关联账号:Risk Platform 报告该网络共有 {total} 个"
                    f"关联账号,但当前返回的数据只包含其中 {len(accounts)}"
                    f" 个账号 ID(其余 {total - len(accounts)} 个 ID 未在"
                    "当前数据中提供),因此只能列出这 "
                    f"{len(accounts)} 个:")
            elif accounts:
                lines.append(f"关联账号共 {len(accounts)} 个:")
            for a in accounts:
                lines.append(f"- {a}")
            devices = s.get("shared_devices") or []
            if devices:
                lines.append("连接这些账号的共享设备:"
                             + "; ".join(str(d) for d in devices) + "。")
    return lines


def _finding_answer_lines(state: AgentLoopState) -> list[str]:
    """AL-2.8/2.12: the finding-level ANSWER — why-explanation first
    (observed-vs-threshold / feature / graph semantics), then the
    COMPLETE evidence table, then policy context; honest about the
    boundary between what is known and what the data does not provide."""
    body: list[str] = []
    saw_signal_facts = False
    saw_empty_evidence = False
    for obs in state.observations:
        if obs.get("outcome") not in (ToolResultOutcome.SUCCESS.value,
                                      ToolResultOutcome.EMPTY.value):
            continue
        s = obs.get("summary") or {}
        tool = obs.get("tool_name")
        if tool == "signal_explain":
            body.extend(_signal_answer_lines(s))
            if not s.get("evidence_missing"):
                saw_signal_facts = True
        elif tool == "policy_lookup" and s.get("match_count") is not None:
            # AL-2.13: the loop surface obeys the SAME association contract
            # as the turn surface — only ASSOCIATED references may be
            # presented as finding-level policy support; matches without
            # association are investigation-wide and are not rendered as
            # if they explain the finding.
            assoc = s.get("associated_citations") or []
            if s.get("associated_count"):
                refs = "; ".join(
                    f"《{c.get('document')}》 {c.get('section')}"
                    for c in assoc[:3] if isinstance(c, dict))
                body.append(
                    f"政策依据:Risk Platform 为该发现关联到 "
                    f"{s.get('associated_count')} 处 finding-level 政策"
                    f"引用" + (f",包括 {refs}。" if refs else "。"))
            else:
                body.append(
                    "政策依据:Risk Platform 未为该发现提供 finding-level "
                    "政策依据(检索到的引用为调查级参考,不作为本发现的"
                    "解释依据)。")
    evidence_lines, empty_streams = _evidence_answer_lines(state)
    body.extend(evidence_lines)
    for obs in state.observations:
        if (obs.get("tool_name") == "finding_drilldown"
                and obs.get("outcome") == ToolResultOutcome.EMPTY.value):
            saw_empty_evidence = True
    if saw_empty_evidence and saw_signal_facts:
        # AL-2.12 Issue B: the honest data boundary — record-level detail
        # does not exist for this finding; the facts above are what the
        # platform provides. Never worded as a "conclusive empty result".
        stream_txt = "/".join(sorted(set(empty_streams))) \
            if empty_streams else "记录级"
        body.append(
            f"关于\"全部明细\"的数据边界:该发现在 Risk Platform 中"
            f"没有{stream_txt}证据可枚举(检索返回零条记录);平台"
            "提供的是上方已列出的聚合/图级别事实。因此我不能把现有摘要"
            "描述为\"全部明细\"——这是当前调查数据的真实边界,而非遗漏。")
    # a re-plan may legitimately re-execute the same kind of step; the
    # answer must not repeat itself when it does
    deduped: list[str] = []
    for line in body:
        if line not in deduped:
            deduped.append(line)
    if deduped and state.focused_finding_title:
        head = f"关于发现 {state.focused_finding_id}「{state.focused_finding_title}」:"
        if state.focused_finding_summary:
            head += state.focused_finding_summary[:220]
        deduped.insert(0, head)
    return deduped


def _record_stream_label(reqs, summary: dict, lang: str = "zh") -> str:
    """Cross-domain consistency: the record-stream label follows the
    ACTUAL evidence types in the summary's source observation — never
    the requirement list alone. A compound requirement whose executed
    evidence was transactions must not label those rows 提现记录."""
    actual = summary.get("_actual_evidence_types") or []
    has_tx = EvidenceType.TRANSACTION_RECORDS in actual
    has_wd = EvidenceType.WITHDRAWAL_RECORDS in actual
    if lang == "en":
        if has_tx and not has_wd:
            return "transaction records"
        if has_wd and not has_tx:
            return "withdrawal records"
    if has_tx and not has_wd:
        return "交易记录"
    if has_wd and not has_tx:
        return "提现记录"
    if EvidenceType.WITHDRAWAL_RECORDS in reqs:
        return "提现记录"
    return "交易记录"


def _match_rule_entries(summary: dict, output) -> list[dict]:
    """Match case rule-evidence entries to the output: by the focused
    finding's title correspondence (title text contained in the rule
    name, or vice versa), else all entries (account-scoped evidence)."""
    entries = summary.get("rule_evidence") or []
    if not entries:
        return []
    title = (summary.get("_finding_title") or "").strip().rstrip(".").lower()
    if not title:
        return entries
    matched = [r for r in entries
               if title in str(r.get("rule_name", "")).lower()
               or str(r.get("rule_name", "")).lower() in title]
    return matched or entries


def _gsp_render_output_facts(output, summary: dict,
                             lang: str = "zh") -> list[str]:
    """Grounded fact lines for a SATISFIED(positive) output, rendered from
    the combined summary whitelist (bounded; no invented facts)."""
    lines: list[str] = []
    reqs = output.evidence_requirements
    subject = output.subject_ref

    # Cross-domain consistency: rule-backed outputs render the
    # ACTUALLY-TRIGGERED rule entries first (detection-decision
    # provenance), then the record evidence.
    if EvidenceType.RULE_EXPLANATION in reqs and summary.get(
            "rule_evidence"):
        matched = _match_rule_entries(summary, output)
        for r in matched:
            lines.append(
                f"命中规则「{r.get('rule_name')}」"
                f"（severity {r.get('severity')}）:" if lang != "en" else
                f"Matched rule '{r.get('rule_name')}' "
                f"(severity {r.get('severity')}):")
            trig = r.get("trigger") or {}
            if trig:
                obs = ", ".join(f"{_feature_label(k)} = {v}"
                                for k, v in trig.items())
                lines.append(f"- 观测值: {obs}" if lang != "en" else
                             f"- Observed: {obs}")
            if r.get("threshold"):
                lines.append(f"- 判定条件: {r['threshold']}"
                             if lang != "en" else
                             f"- Criteria: {r['threshold']}")
            if r.get("description"):
                lines.append(f"- 说明: {r['description']}"
                             if lang != "en" else
                             f"- Description: {r['description']}")
        lines.extend(_render_population_reconciliation(summary))
        return lines
    def _has(*keys):
        return any(summary.get(k) for k in keys)

    if EvidenceType.OPPOSITE_TRADE_RELATIONS in reqs \
            and summary.get("candidates"):
        def _match_basis_note(c, lang: str = "zh"):
            """Per-candidate matching basis: the criteria are a relative
            value tolerance OR the absolute near-zero-notional floor —
            a candidate whose relative difference exceeds the relative
            tolerance was admitted by the floor, and must say so (the
            numbers alone would read as contradicting the criteria)."""
            rel = c.get("value_difference_relative")
            rel_tol = summary.get("value_tolerance_relative")
            floor = summary.get("value_tolerance_absolute_floor")
            if not (isinstance(rel, (int, float))
                    and isinstance(rel_tol, (int, float))
                    and isinstance(floor, (int, float))
                    and not isinstance(rel, bool)
                    and rel > rel_tol
                    and c.get("value_difference") is not None
                    and abs(c["value_difference"]) <= floor):
                return ""
            if lang == "en":
                return (" (admitted by the absolute floor: the value "
                        f"difference is within {floor:g} quote currency; "
                        "the relative tolerance does not apply)")
            return ("（匹配依据：价值差未达 5% 相对容差，按绝对下限 放行"
                    "——价值差 ≤ 绝对下限 1.0[计价币]，未适用相对容差）")
        if summary.get("analysis_scope") == "related_account_set":
            # SET-INTERNAL scope: every candidate is valid set-internal
            # evidence; label whether each involves the case account or
            # only related members — never attribute a related-only pair
            # to the case account.
            case_acc = subject
            _len_c = len(summary["candidates"])
            with_case, only_related = [], []
            for c in summary["candidates"]:
                if not isinstance(c, dict):
                    continue
                (with_case if case_acc in (str(c.get("account_a")),
                                           str(c.get("account_b")))
                 else only_related).append(c)
            _found = summary.get("candidates_found")
            _cap_note = ""
            if isinstance(_found, int) and _found > len(summary["candidates"]):
                # delivery accounting: EVERY emitted candidate is rendered
                # in this dialogue (no display-layer truncation below the
                # system emission cap); the only undelivered candidates are
                # those beyond the system emission cap itself
                _beyond_cap = _found - len(summary["candidates"])
                _cap_note = (
                    f"；受系统候选上限：共发现 {_found} 组候选，本次回复"
                    f"已完整交付其内的 {_len_c} 组（其余 {_beyond_cap} 组"
                    "受系统候选上限未列出；当前没有其他可访问的完整清单）")
            if lang == "en":
                lines.append(
                    f"Found {len(summary['candidates'])} candidate "
                    f"hedging relationships within the related-account "
                    f"set ({len(with_case)} involve the case account "
                    f"{case_acc}; {len(only_related)} occur solely "
                    "between other related members; quantitative "
                    "matches are investigation leads, not conclusions "
                    "about intent" + _cap_note + "):")
            else:
                lines.append(
                    f"发现关联账户集合内部候选对冲交易关系 "
                    f"{len(summary['candidates'])} 组（其中 "
                    f"{len(with_case)} 组涉及案件账户 {case_acc}，"
                    f"{len(only_related)} 组仅发生在其他关联成员之间；"
                    "候选关系为量化匹配的调查线索，不构成对交易意图的"
                    "结论" + _cap_note + "）:")
            _len_c = len(summary["candidates"])
            _rel_tol = summary.get("value_tolerance_relative")
            _floor = summary.get("value_tolerance_absolute_floor")
            if isinstance(_rel_tol, (int, float)) and \
                    isinstance(_floor, (int, float)):
                if lang == "en":
                    lines.append(
                        f"Matching criteria: time gap <= "
                        f"{summary.get('max_time_delta_seconds', 30):g}s "
                        f"and value difference <= {_rel_tol:.0%} of the "
                        f"larger side; OR value difference <= the "
                        f"{_floor:g} absolute floor (quote currency; "
                        "small notionals — candidates admitted only by "
                        "the floor are labeled below).")
                else:
                    lines.append(
                        f"匹配判据:双方交易时间差 ≤ "
                        f"{summary.get('max_time_delta_seconds', 30):g} 秒，"
                        f"且价值差 ≤ 较大一侧的 {_rel_tol:.0%}；"
                        f"或价值差 ≤ 绝对下限 {_floor:g}[计价币]（绝对容差，"
                        "小额名义先于相对容差适用——仅由该下限放行的候选在"
                        "下方单独标注）。")
            for c in summary["candidates"]:
                gap = c.get("max_time_gap_seconds")
                gap_txt = f"{gap:g}s" if isinstance(gap, (int, float)) \
                    and not isinstance(gap, bool) else "n/a"
                tag = ("涉及案件账户"
                       if case_acc in (str(c.get("account_a")),
                                       str(c.get("account_b")))
                       else "仅涉及关联成员")
                rel_pct = c.get("value_difference_relative")
                rel_txt = (f"{rel_pct:.1%}" if isinstance(rel_pct, (int, float))
                           and not isinstance(rel_pct, bool) else "n/a")
                ids_txt = ", ".join(
                    (c.get("transaction_ids_a") or [])
                    + (c.get("transaction_ids_b") or []))
                if lang == "en":
                    en_tag = ("involves the case account"
                              if case_acc in (str(c.get("account_a")),
                                              str(c.get("account_b")))
                              else "related-members only")
                    lines.append(
                        f"- {c.get('candidate_id')} ({en_tag}): "
                        f"{c.get('account_a')} ({c.get('side_a')}, "
                        f"{c.get('symbol')} "
                        f"{c.get('value_a'):,.6g}) vs "
                        f"{c.get('account_b')} ({c.get('side_b')}, "
                        f"{c.get('symbol')} {c.get('value_b'):,.6g}); "
                        f"transactions {ids_txt}; max gap {gap_txt}; "
                        f"value difference {rel_txt}"
                        f"{_match_basis_note(c, lang=lang)}")
                else:
                    lines.append(
                        f"- {c.get('candidate_id')}"
                        f"（{tag}）: {c.get('account_a')}（{c.get('side_a')}，"
                        f"{c.get('symbol')} {c.get('value_a'):,.6g}）↔ "
                        f"{c.get('account_b')}（{c.get('side_b')}，"
                        f"{c.get('symbol')} {c.get('value_b'):,.6g}）；"
                        f"交易 {ids_txt}；最大时间差 {gap_txt}；"
                        f"价值差 {rel_txt}{_match_basis_note(c)}")
            if (summary.get("retrieval") or {}).get("truncated"):
                # budget-bounded retrieval: state the retrieved count so
                # 已检索数量 / 匹配数量 / 展示数量 are all distinguishable.
                # The echo has two shapes: target/counterparty echoes
                # (target_account scope) or an account_set echo
                # (related_account_set); totals carries the overall count.
                totals = summary.get("totals") or {}
                retrieved = totals.get("transactions_retrieved")
                accounts_queried = ((summary.get("retrieval") or {})
                                    .get("account_set") or {}).get(
                    "accounts_queried")
                if lang == "en":
                    scope_txt = (
                        f"{retrieved} transactions retrieved across "
                        f"{accounts_queried} related accounts"
                        if retrieved is not None and accounts_queried is not None
                        else "retrieval bounded")
                    lines.append(
                        "Note: set trade evidence retrieval was budget-"
                        f"bounded ({scope_txt}); more candidates may exist "
                        "beyond the retrieved range.")
                else:
                    scope_zh = "、".join(
                        x for x in (
                            f"已检索交易 {retrieved} 笔"
                            if retrieved is not None else "",
                            f"关联账户 {accounts_queried} 个"
                            if accounts_queried is not None else "")
                        if x)
                    lines.append(
                        f"注意: 集合交易证据检索受预算限制"
                        + (f"（{scope_zh}）" if scope_zh else "")
                        + "；不能据此证明未检索范围内不存在更多集合内部候选。")
            return lines
        involved = [c for c in summary["candidates"]
                    if isinstance(c, dict) and subject in (
                        str(c.get("account_a")), str(c.get("account_b")))]
        if involved:
            _t_found = summary.get("candidates_found")
            _t_note = ""
            if isinstance(_t_found, int) and _t_found > len(involved):
                _t_note = (
                    f"；受系统候选上限：共发现 {_t_found} 组候选，本次回复"
                    f"已完整交付其内的 {len(involved)} 组（其余 "
                    f"{_t_found - len(involved)} 组受系统候选上限未列出）")
            if lang == "en":
                lines.append(
                    f"Found {len(involved)} candidate hedging "
                    f"relationships involving {subject} (quantitative "
                    "matches are investigation leads, not conclusions "
                    "about intent" + _t_note + "):")
            else:
                lines.append(
                    f"发现涉及 {subject} 的候选对冲交易关系 {len(involved)} 组"
                    "（候选关系为量化匹配的调查线索，不构成对交易意图的结论"
                    + _t_note + "）:")
            _rel_tol = summary.get("value_tolerance_relative")
            _floor = summary.get("value_tolerance_absolute_floor")
            if isinstance(_rel_tol, (int, float)) and \
                    isinstance(_floor, (int, float)):
                if lang == "en":
                    lines.append(
                        f"Matching criteria: time gap <= "
                        f"{summary.get('max_time_delta_seconds', 30):g}s "
                        f"and value difference <= {_rel_tol:.0%} of the "
                        f"larger side; OR value difference <= the "
                        f"{_floor:g} absolute floor (quote currency; "
                        "small notionals — candidates admitted only by "
                        "the floor are labeled below).")
                else:
                    lines.append(
                        f"匹配判据:双方交易时间差 ≤ "
                        f"{summary.get('max_time_delta_seconds', 30):g} 秒，"
                        f"且价值差 ≤ 较大一侧的 {_rel_tol:.0%}；"
                        f"或价值差 ≤ 绝对下限 {_floor:g}[计价币]（绝对容差，"
                        "小额名义先于相对容差适用——仅由该下限放行的候选在"
                        "下方单独标注）。")
            for c in involved:
                gap = c.get("max_time_gap_seconds")
                gap_txt = f"{gap:g}s" if isinstance(gap, (int, float)) \
                    and not isinstance(gap, bool) else "n/a"
                rel_pct = c.get("value_difference_relative")
                rel_txt = (f"{rel_pct:.1%}" if isinstance(rel_pct, (int, float))
                           and not isinstance(rel_pct, bool) else "n/a")
                ids_txt = ", ".join(
                    (c.get("transaction_ids_a") or [])
                    + (c.get("transaction_ids_b") or []))
                if lang == "en":
                    lines.append(
                        f"- {c.get('candidate_id')}: {c.get('account_a')}"
                        f" ({c.get('side_a')}, {c.get('symbol')} "
                        f"{c.get('value_a'):,.6g}) vs "
                        f"{c.get('account_b')} ({c.get('side_b')}, "
                        f"{c.get('symbol')} {c.get('value_b'):,.6g}); "
                        f"transactions {ids_txt}; max gap {gap_txt}; "
                        f"value difference {rel_txt}"
                        f"{_match_basis_note(c, lang=lang)}")
                else:
                    lines.append(
                        f"- {c.get('candidate_id')}: {c.get('account_a')}"
                        f"（{c.get('side_a')}，{c.get('symbol')} "
                        f"{c.get('value_a'):,.6g}）↔ {c.get('account_b')}"
                        f"（{c.get('side_b')}，{c.get('symbol')} "
                        f"{c.get('value_b'):,.6g}）；交易 {ids_txt}；"
                        f"最大时间差 {gap_txt}；价值差 {rel_txt}"
                        f"{_match_basis_note(c)}")
            return lines
    if EvidenceType.SHARED_IP_EVIDENCE in reqs \
            and summary.get("shared_ip_attributions"):
        rows = [f"- {acct}: {', '.join(ips)}"
                for acct, ips in summary["shared_ip_attributions"].items()
                if ips]
        if rows:
            lines.append("发现以下账号之间存在共享 IP 记录:"
                         if lang != "en" else
                         "Shared IP records were found between the "
                         "following accounts:")
            lines.extend(rows[:10])
            return lines
    if EvidenceType.WITHDRAWAL_RECORDS in reqs \
            or EvidenceType.TRANSACTION_RECORDS in reqs:
        count = summary.get("record_count")
        rows = summary.get("record_rows") or []
        if count:
            label = _record_stream_label(reqs, summary, lang=lang)
            lines.append(f"完整{label}共 {count} 条:"
                         if lang != "en" else
                         f"Complete {label.lower()} — {count} records:")
            # U00033: a PURE records delivery carries the requested
            # records + source notes only. RP feature-verification
            # content (window/ratio reconciliation, unmapped-metric
            # hints like trade_frequency_7d) is metric-verification
            # output — rendered by the rule/metric-explanation branches
            # when the user actually asks for verification, never
            # auto-attached to a plain export.
            for r in rows[:10]:
                lines.append(
                    f"- {r.get('record_id')} | {r.get('timestamp')} | "
                    f"{r.get('summary')}")
            if summary.get("rows_truncated"):
                lines.append("（对话内显示前 10 条；完整集合见证据结果）"
                             if lang != "en" else
                             "(dialogue shows the first 10 records; the "
                             "complete set is in the evidence result)")
            return lines
    if EvidenceType.NETWORK_RELATIONS in reqs \
            and summary.get("related_accounts"):
        lines.append(
            f"关联账号共 {summary.get('related_accounts_count') or len(summary['related_accounts'])} 个:"
            if lang != "en" else
            f"Related accounts ({summary.get('related_accounts_count') or len(summary['related_accounts'])}):")
        _rel_total = summary.get("related_accounts_count") \
            or len(summary["related_accounts"])
        _rel_shown = summary["related_accounts"][:25]
        lines.extend(f"- {a}" for a in _rel_shown)
        if len(_rel_shown) < _rel_total:
            lines.append(
                f"（对话内展示前 {len(_rel_shown)} 个；关联账号共 "
                f"{_rel_total} 个，完整清单见证据结果。）")
        if summary.get("shared_devices"):
            lines.append(
                f"连接这些账号的共享设备: "
                f"{', '.join(summary['shared_devices'])}。")
        return lines
    if EvidenceType.ML_SIGNAL_EXPLANATION in reqs and summary.get("ml_score") is not None:
        lines.append(
            f"ML 信号分数 {summary['ml_score']}/100"
            f"（检测方法: {summary.get('detection_method') or '未提供'}）— "
            "系统检测信号，非校准欺诈概率。")
        feats = summary.get("available_feature_values") or {}
        if isinstance(feats, dict) and feats:
            shown = ", ".join(
                f"{_feature_label(k)} = {v}" for k, v in
                list(feats.items())[:10])
            lines.append(f"模型上下文特征（Risk Platform 权威值）: {shown}。")
        if summary.get("attribution_available") is False:
            lines.append(
                "边界: Risk Platform 未提供逐特征贡献归因，因此无法"
                "说明各特征对分数的具体影响或模型的推理过程；以上为"
                "可确定的全部事实。")
        return lines
    if EvidenceType.RULE_EXPLANATION in reqs and summary.get(
            "rule_evidence"):
        # Cross-domain consistency: render the ACTUALLY-TRIGGERED rule
        # entries (detection-decision provenance: name/trigger/threshold/
        # contribution) from the source-backed rule evidence.
        matched = _match_rule_entries(summary, output)
        for r in matched:
            lines.append(
                f"命中规则「{r.get('rule_name')}」（severity {r.get('severity')}）:")
            trig = r.get("trigger") or {}
            if trig:
                obs = ", ".join(f"{_feature_label(k)} = {v}"
                                for k, v in trig.items())
                lines.append(f"- 观测值: {obs}")
            if r.get("threshold"):
                lines.append(f"- 判定条件: {r['threshold']}")
            if r.get("description"):
                lines.append(f"- 说明: {r['description']}")
        lines.extend(_render_population_reconciliation(summary))
        return lines
    if EvidenceType.RULE_EXPLANATION in reqs and (
            summary.get("rule_name") or summary.get("rule_description")):
        lines.append(
            f"命中规则: {summary.get('rule_name') or summary.get('rule_description')}"
            if lang != "en" else
            f"Matched rule: {summary.get('rule_name') or summary.get('rule_description')}")
        if summary.get("threshold"):
            lines.append(f"阈值: {summary['threshold']}"
                         if lang != "en" else
                         f"Threshold: {summary['threshold']}")
        return lines
    if EvidenceType.RULE_EXPLANATION in reqs and summary.get(
            "feature_facts"):
        # AL-2.14/F7-class: a Feature signal's explanation — the actual
        # factor values ground the why-flagged/criteria questions. The
        # threshold/trigger-state semantics, when RP provides them, ride
        # the platform's OWN authoritative finding statement (verbatim
        # echo) — the system never reconstructs or infers a trigger
        # verdict the platform did not state.
        rf = summary.get("risk_factor") or {}
        severity = (rf.get("severity") if isinstance(rf, dict) else None)
        _ff_total = len(summary["feature_facts"])
        lines.append(
            "该条目为 Risk Platform 的行为特征观察记录"
            + (f"（风险因子记录，严重度 {severity}）" if severity else "")
            + (f"（观测值前 5 项,共 {_ff_total} 项）" if _ff_total > 5 else "")
            + "；观测记录的存在不等于规则已触发 — 其观测值:")
        for f in summary["feature_facts"][:5]:
            if isinstance(f, dict) and f.get("name"):
                lines.append(f"- {_feature_label(f['name'])} = "
                             f"{f.get('value', '')}")
        lines.extend(_render_population_reconciliation(summary))
        finding_statement = (summary.get("finding_summary") or "").strip()
        rf = summary.get("risk_factor") or {}
        rf_desc = (rf.get("description") or "").strip() \
            if isinstance(rf, dict) else ""
        if finding_statement:
            # the platform's OWN complete statement — carries the
            # rule/threshold/trigger-state semantics when RP states them
            lines.append(f"Risk Platform 对该风险发现的权威说明: "
                         f"{finding_statement}")
        elif rf_desc:
            lines.append(f"Risk Platform 对该风险因子的权威说明: "
                         f"{rf_desc}")
        else:
            # no authoritative threshold semantics reached the payload:
            # the platform provides no such statement for this feature —
            # say so explicitly instead of leaving the threshold unstated
            lines.append(
                "说明:当前 Risk Platform 数据未提供该特征的独立"
                "结构化判定阈值字段。")
        lines.append("说明:以上权威说明为 Risk Platform 原文,系统不重构、"
                     "不推断阈值或触发状态;该观察记录的是行为特征本身,"
                     "不构成风险判定,也不证明或排除欺诈行为。")
        return lines
    if summary.get("cluster_name"):
        lines.append(
            f"图关系检测: {summary.get('cluster_name')}"
            f"（{summary.get('detection_type') or 'cluster'}；"
            f"{summary.get('member_count')} 个账号, 集群风险分 "
            f"{summary.get('cluster_risk_score')}）。")
        devices = summary.get("shared_devices") or []
        if devices:
            lines.append(
                f"实际观察到的共享基础设施: "
                f"{', '.join(str(d) for d in devices[:5])}"
                "——该账户与其他账号通过同一设备/基础设施关联。")
        related = summary.get("related_accounts") or []
        if related:
            # List-completeness semantics: the related_accounts field is
            # an RP server-side PREVIEW — shown count vs authoritative
            # total must both be stated under their own definitions, and
            # a partial list may never read as the complete membership.
            shown = len(related)
            total = summary.get("related_accounts_total")
            is_preview = summary.get("related_accounts_preview") is True
            partial = bool(is_preview and total and shown < total)
            if partial:
                lines.append(
                    "同一集群中的关联账号（图证据样例，非完整清单）: "
                    + ", ".join(str(a) for a in related[:8])
                    + (f" 等 {shown} 个。" if shown > 8 else "。"))
                lines.append(
                    f"已展示 {shown} / {total} 个关联账号（Risk Platform "
                    "图证据为预览清单，非全部成员）；完整的关联账号清单"
                    "可通过关联账号网络分析查看。")
            else:
                lines.append(
                    f"同一集群中的关联账号（共 {shown} 个）: "
                    + ", ".join(str(a) for a in related))
        lines.append(
            "风险含义: 共享设备/基础设施关系意味着多个账号在系统侧不"
            "可区分地关联在一起，这是 Risk Platform 记录的典型关联网"
            "络风险信号（可能指向同一实体的多账号行为或账号代持）。"
            "它是需要进一步调查的线索，不构成欺诈已成立的证明。")
        return lines
    if summary.get("ml_score") is not None or summary.get("detection_method"):
        # ML detection findings: the payload carries the authoritative
        # method/score-semantics/attribution fields — render them (same
        # authoritative semantics as the turn path). The attribution gap
        # is stated WITH what data would be needed, and the case's other
        # findings are named as independent context, never as the
        # model's causes.
        lines.extend(_signal_answer_lines(summary, lang=lang))
        if summary.get("attribution_available") is False:
            if lang == "en":
                lines.append(
                    "To answer \"which features the model relied on for "
                    "this score\", the Risk Platform would need to "
                    "provide model attribution data (e.g. per-feature "
                    "contributions or reason codes); the current response "
                    "contains no such fields, so this system cannot "
                    "currently trace the score to any specific behavior.")
                lines.append(
                    "The case's other findings (trading activity, "
                    "withdrawal records, device/network associations) "
                    "are independently observed facts useful as "
                    "investigation context; the current data cannot "
                    "prove any causal link between them and this model "
                    "score.")
            else:
                lines.append(
                    "要回答“模型具体依据哪些特征给出这个分数”,需要 Risk "
                    "Platform 提供模型归因数据(如特征贡献度或 reason "
                    "codes);当前响应中没有这类字段,因此本系统目前无法"
                    "把该分数追溯到任何具体行为。")
                lines.append(
                    "案件中的其他发现(交易活动、提现记录、设备/网络关联"
                    "等)是独立观察到的事实,可作为继续调查的背景;但当前"
                    "数据不能证明它们与该模型分数之间的因果关系。")
        return lines
    if EvidenceType.POLICY_REFERENCES in reqs and (
            summary.get("matches") is not None
            or summary.get("associated_citations") is not None
            or summary.get("match_count") is not None):
        # The policy observation projects its payload as
        # associated_citations/match_count/finding_policy_status (the
        # composer's old `matches` key never exists in GSP summaries —
        # the branch was dead). EN + zh variants both state the
        # retrieval fact; the citations themselves are data.
        lines.append("已检索相关政策引用（详见政策部分）。"
                     if lang != "en" else
                     "Related policy references were retrieved (see the "
                     "policy section).")
        return lines
    if EvidenceType.CASE_SUMMARY in reqs and summary.get("finding_count"):
        lines.append(
            f"案例共识别 {summary['finding_count']} 个风险发现。"
            if lang != "en" else
            f"The case carries {summary['finding_count']} identified "
            "risk findings.")
        return lines
    lines.append(
        "已取得相关证据，但当前未能从中呈现具体的解释性事实"
        "（证据明细见 Activity）；不能把证据获取本身当作对您问题的"
        "解释。" if lang != "en" else
        "Evidence was obtained, but no explanatory facts could be "
        "presented from it (see Activity for the evidence details); "
        "obtaining evidence is not itself an answer to your question.")
    return lines


# Cross-domain summary–detail consistency: the asserted record
# population extractor lives in app.goal_resolution (single source of
# truth — the contract acceptance path uses it too).
from app.goal_resolution import (          # noqa: E402
    asserted_record_population as _asserted_record_population,
)


def _population_reconciliation_line(goal_text: str | None,
                                    records_total: int,
                                    noun: str = "提现") -> str | None:
    """Explicit scope reconciliation when the request asserts a record
    population that differs from the authoritative evidence population
    (complete retrieval). The comparison over the complete authoritative
    population remains complete — the note makes the premise difference
    explicit instead of silently substituting populations."""
    asserted = _asserted_record_population(goal_text)
    if asserted is None or asserted == records_total:
        return None
    return (f"说明：您的请求提及 {asserted} 笔{noun}；权威证据共 "
            f"{records_total} 笔{noun}（检索完整，无缺页），以下比较与"
            f"结论已覆盖全部 {records_total} 笔{noun}记录。")


def _render_reverse_trade_metric_facet(output, summary: dict,
                                       lang: str = "zh") \
        -> list[str] | None:
    """reverse_trade_metric_semantics facet section: what the account's
    authoritative opposite_trade_ratio MEASURES — the own-account
    buy/sell balance, explicitly not a cross-account match rate
    (baseline §8). Rendered from the verdict detail (single source of
    the wording); nothing renderable → the standard branches apply."""
    from app.goal_resolution import PostconditionFacet
    if (PostconditionFacet.REVERSE_TRADE_METRIC_SEMANTICS.value
            not in (output.postcondition_facets or [])):
        return None
    metrics = summary.get("target_account_metrics")
    if not isinstance(metrics, dict) \
            or not isinstance(metrics.get("opposite_trade_ratio"),
                              (int, float)) \
            or isinstance(metrics.get("opposite_trade_ratio"), bool):
        return None
    # content only — the section header is already emitted by
    # _gsp_render_output_section (never duplicated)
    if lang == "en":
        return [output.detail or
                f"{metrics['opposite_trade_ratio']:.2%} "
                "(opposite_trade_ratio) is the account's OWN "
                "opposite-trade ratio: computed over the account's own "
                "trade stream as min(BUY count, SELL count) divided by "
                "the account's total trades. It measures same-account "
                "buy/sell balance; it is NOT a cross-account match rate. "
                "Source: Risk Platform feature_evidence."]
    return [output.detail
            or _reverse_trade_metric_detail(
                metrics["opposite_trade_ratio"])]


def _reverse_trade_metric_detail(ratio: float) -> str:
    return (
        f"{ratio:.2%}（opposite_trade_ratio）是账户自身的反向交易比例："
        "按该账户自己的全部交易计算，分子为较少一侧的交易笔数"
        "（min(BUY, SELL)），分母为该账户交易总笔数；它衡量的是同一"
        "账户内的买卖平衡，不是跨账户匹配率。")


def _render_new_address_facet(output, summary: dict,
                              lang: str = "zh") -> list[str] | None:
    """withdrawal_new_address_status facet section: the per-record
    is_new_address status + the bounded overall conclusion."""
    from app.goal_resolution import PostconditionFacet
    if (PostconditionFacet.WITHDRAWAL_NEW_ADDRESS_STATUS.value
            not in (output.postcondition_facets or [])):
        return None
    nas = summary.get("new_address_status") or {}
    if not nas.get("records"):
        return None
    header = (output.claim_description or "").strip().rstrip("。")
    if not header:
        header = (f"New-address status of {output.subject_ref} withdrawals"
                  if lang == "en" else
                  f"关于 {output.subject_ref} 的新地址标记状态")
    lines = ([f'About "{header}":'] if lang == "en"
             else [f"关于「{header}」:"])
    if output.detail and lang != "en":
        lines.append(output.detail)
    lines.extend(_render_new_address_status(output, summary, lang=lang))
    return lines


def _render_destination_facet(output, summary: dict,
                              goal_text: str | None = None,
                              lang: str = "zh") -> list[str] | None:
    """WP-GS-D: the destination-address-identity facet's user-visible
    section — the comparison conclusion (GSP-4 verdict), per-group
    record-ID provenance, and the evidence table with the 目的地 column
    (design §6 GSP-5 row / §8). Returns None when the facet is not
    declared or no facet-specific boundary can be rendered (the caller
    then falls back to the standard satisfaction branches)."""
    from app.goal_resolution import PostconditionFacet
    if PostconditionFacet.DESTINATION_ADDRESS_IDENTITY.value not in (
            output.postcondition_facets or []):
        return None
    block = summary.get("destination_identity")
    has_block = isinstance(block, dict) and block.get("groups")
    sat = output.satisfaction
    if sat == OutputSatisfaction.PENDING or (
            sat == OutputSatisfaction.UNSUPPORTED):
        return None                     # no facet verdict to render
    lines: list[str] = []
    if sat == OutputSatisfaction.SATISFIED and not has_block:
        # row-4 inherited empty-complete negative: no withdrawal records
        # — there is nothing to compare, which is itself the determinate
        # answer for the comparison request
        lines.append(
            "未检索到匹配的提现记录（检索范围完整），因此不"
            "存在可比较的目的地标识。" if lang != "en" else
            "No matching withdrawal records were retrieved (retrieval "
            "scope complete), so there are no destination identities to "
            "compare.")
        return lines
    if lang == "en":
        # English face: the structured comparison facts (block) are
        # rendered from data; the evaluator's Chinese verdict sentence is
        # not shown (its facts are re-stated in English from the block).
        if sat == OutputSatisfaction.PARTIALLY_SUPPORTED:
            lines.append(
                "Partial evidence was obtained but coverage is "
                "incomplete; no complete conclusion can be drawn from it.")
        elif sat != OutputSatisfaction.SATISFIED:
            return None
    elif output.detail:
        lines.append(output.detail)
    elif sat == OutputSatisfaction.PARTIALLY_SUPPORTED:
        lines.append("已获取部分证据，但覆盖范围不完整；不能据此得出"
                     "完整结论。")
    else:
        return None
    # Cross-domain summary–detail consistency: when the request asserts
    # a record population that differs from the authoritative (complete)
    # evidence population, say so explicitly — never silently substitute
    # populations between the summary/context and the compared details.
    # For a SATISFIED comparison the note reconciles the premise; for a
    # population SHORTFALL the evaluator's detail already states that
    # the asserted comparison cannot be completed (rendered above) —
    # no completion-flavored note is added.
    total = (summary.get("destination_identity") or {}).get(
        "records_total") if has_block else summary.get("record_count")
    if (isinstance(total, int) and total > 0
            and sat == OutputSatisfaction.SATISFIED):
        reconciliation = _population_reconciliation_line(
            goal_text, total)
        if reconciliation:
            lines.append(reconciliation)
    if not has_block:
        # comparison_facts_absent: the retrieval results render as
        # evidence only — never as the comparison answer
        rows = summary.get("record_rows") or []
        if rows:
            lines.append(
                "本次取得的记录仅作为证据保留（详见下表），"
                "不构成“同一/不同”的结论:" if lang != "en" else
                "The retrieved records are retained as evidence only "
                "(see the table below); they do not constitute a "
                "same/different conclusion:")
            lines.extend(_facet_record_table(summary, lang=lang))
        return lines
    lines.extend(_render_population_reconciliation(summary))
    if lang == "en":
        unique = block.get("unique_destinations")
        total = block.get("records_total")
        lines.append(
            f"The {total} withdrawals point to {unique} different "
            "destinations (by recorded destination identity).")
        lines.append(
            "Destination identity groups (exact match on the recorded "
            "identity):")
    else:
        lines.append("目的地标识分组（记录中的标识，精确区分大小写）:")
    for g in block["groups"]:
        ids = ", ".join(g.get("record_ids") or [])
        if lang == "en":
            lines.append(f"- {g.get('destination_ref', '')}"
                         f" ({g.get('address', '')}): records {ids}")
        else:
            lines.append(f"- {g.get('destination_ref', '')}"
                         f"（{g.get('address', '')}）: 记录 {ids}")
    lines.extend(_facet_record_table(summary, lang=lang))
    return lines


def _facet_record_table(summary: dict, lang: str = "zh") -> list[str]:
    """The evidence table with the per-record 目的地 column (same format
    as the AL-2.12 evidence view rendering; GS-B groups carry the
    recorded destination value)."""
    rows = summary.get("record_rows") or []
    dest_by_record: dict[str, dict] = {}
    for g in ((summary.get("destination_identity") or {}).get("groups")
              or []):
        for rid in (g.get("record_ids") or []):
            dest_by_record[rid] = g
    lines = ([f"| # | Record ID | Time | Destination | Details |",
              "|---|---|---|---|---|"] if lang == "en" else
             [f"| # | 记录 ID | 时间 | 目的地 | 明细 |",
              "|---|---|---|---|---|"])
    for i, r in enumerate(rows, 1):
        d = dest_by_record.get(r.get("record_id"))
        dest_cell = (f"{d.get('destination_ref', '')} "
                     f"({d.get('address', '')})" if d else "—")
        lines.append(f"| {i} | {r.get('record_id', '')} | "
                     f"{r.get('timestamp', '')} | "
                     f"{dest_cell} | {r.get('summary', '')} |")
    if summary.get("rows_truncated"):
        lines.append(
            f"(dialogue shows the first {len(rows)} rows; the complete "
            "set is in the evidence result)" if lang == "en" else
            f"（对话内显示前 {len(rows)} 条；完整集合见证据结果）")
    return lines


def _gsp_render_output_section(output, summary: dict,
                               goal_text: str | None = None,
                               lang: str = "zh") -> list[str]:
    """One RequiredOutput → deterministic user-visible section, driven by
    the output's ACCEPTED satisfaction state (WP-GSP-4 verdict) — never by
    which tools happened to succeed. The raw claim label (LLM-coined) is
    internal and never rendered (P6); the user-safe header is the bounded
    claim_description or a generic scope line."""
    header = (output.claim_description or "").strip().rstrip("。")
    if not header:
        header = (f"Investigation result for {output.subject_ref}"
                  if lang == "en"
                  else f"关于 {output.subject_ref} 的调查结果")
    lines = ([f'About "{header}":'] if lang == "en"
             else [f"关于「{header}」:"])
    # WP-GS-D: a declared destination-address-identity facet renders its
    # own conclusion/provenance section (§7.2 verdict, §8 boundary);
    # when the facet yields nothing renderable, the standard branches
    # apply unchanged.
    facet_lines = _render_destination_facet(output, summary, goal_text,
                                            lang=lang)
    if facet_lines:
        lines.extend(facet_lines)
        return lines
    na_lines = _render_new_address_facet(output, summary, lang=lang)
    if na_lines:
        lines.extend(na_lines)
        return lines
    metric_lines = _render_reverse_trade_metric_facet(output, summary,
                                                      lang=lang)
    if metric_lines:
        lines.extend(metric_lines)
        return lines
    sat = output.satisfaction
    en = lang == "en"
    if sat == OutputSatisfaction.SATISFIED:
        if output.polarity == "negative":
            if summary.get("analysis_scope") == "related_account_set":
                lines.append(
                    "在已完整检索的关联账户集合内部范围内，未发现"
                    "符合条件的对冲交易候选；该结论仅适用于本次集合"
                    "与数据窗口。" if not en else
                    "Within the fully retrieved related-account set, no "
                    "hedging candidates matched the criteria; this "
                    "conclusion applies to this set and data window only.")
                return lines
            neg = {
                EvidenceType.OPPOSITE_TRADE_RELATIONS:
                    (f"Within the fully retrieved scope, no hedging "
                     f"candidates involving {output.subject_ref} were "
                     f"found.") if en else
                    f"在完整检索范围内，未发现涉及 {output.subject_ref} "
                    "的对冲交易候选。",
                EvidenceType.SHARED_IP_EVIDENCE:
                    (f"No shared-IP records between {output.subject_ref} "
                     f"and other accounts were found.") if en else
                    f"未发现 {output.subject_ref} 与其他账号之间存在"
                    "共享 IP 记录。",
                EvidenceType.WITHDRAWAL_RECORDS:
                    ("No matching withdrawal records were retrieved "
                     "(retrieval scope complete).") if en else
                    "未检索到匹配的提现记录（检索范围完整）。",
                EvidenceType.TRANSACTION_RECORDS:
                    ("No matching transaction records were retrieved "
                     "(retrieval scope complete).") if en else
                    "未检索到匹配的交易记录（检索范围完整）。",
            }
            for req in output.evidence_requirements:
                if req in neg:
                    lines.append(neg[req])
                    break
            else:
                lines.append("未发现相关事实。" if not en
                             else "No matching facts were found.")
        else:
            lines.extend(_gsp_render_output_facts(output, summary,
                                                  lang=lang))
    elif sat == OutputSatisfaction.PARTIALLY_SUPPORTED:
        # grounded facts first (content-driven): a capped/partial trade
        # listing still shows the candidates it DID find, then the
        # boundary — never a bare "partial" line over real evidence
        if summary.get("candidates"):
            lines.extend(_gsp_render_output_facts(output, summary,
                                                  lang=lang))
        lines.append(
            "已获取部分证据，但覆盖范围不完整；不能据此得出"
            "完整结论（详见上方边界说明）。" if not en else
            "Partial evidence was obtained but coverage is incomplete; "
            "no complete conclusion can be drawn from it (see the "
            "boundary notes above).")
    elif sat == OutputSatisfaction.UNRESOLVED:
        if output.claim_kind == ClaimKind.ASSESSMENT:
            lines.append(
                "当前证据不足以判定该项。已记录的相关证据仅供分析员"
                "参考，不构成结论；不能仅凭当前证据认定该判断。"
                if not en else
                "The current evidence is insufficient to adjudicate this "
                "item. The recorded evidence is provided for analyst "
                "reference only and does not constitute a conclusion.")
        else:
            lines.append("当前证据不足以确定该项。" if not en else
                         "The current evidence is insufficient to "
                         "determine this item.")
    elif sat == OutputSatisfaction.UNSUPPORTED:
        lines.append("当前系统不具备回答该项所需的核查能力。" if not en
                     else "The current system does not have the "
                          "capability to answer this item.")
    else:  # PENDING — no evaluable evidence reached this output
        lines.append("该项尚未产生可判定的结果。" if not en else
                     "No determinable result was produced for this item.")
    return lines


_OUTCOME_EN = {
    "complete": "the investigation goal has been fully answered.",
    "partial": ("the investigation goal has been partially answered "
                "(some items could not be fully answered — see the "
                "per-item notes)."),
    "unanswerable": ("with the current system capabilities and data, "
                     "this request cannot be answered."),
    "blocked": "this investigation is blocked (see notes above).",
}


_OUTCOME_ZH = {
    "complete": "本次调查目标已完整回答",
    "partial": "本次调查目标已部分回答（部分项目未能完整回答，见各项说明）",
    "unanswerable": "以当前系统能力与数据，无法回答该请求",
    "blocked": "本次调查受阻（见上方说明）",
}


_GU_DISCLOSURE_ZH = {
    "json_parse": ("本次目标理解返回的结构化输出未能成功解析"
                   "（有界重试后仍未成功）"),
    "empty": "本次目标理解返回了空响应",
    "timeout": "本次目标理解请求超时",
    "rate_limit": "本次目标理解请求被限流",
    "service_unavailable": "本次目标理解服务暂时不可用",
}
_GU_DISCLOSURE_EN = {
    "json_parse": ("the goal-understanding response could not be parsed "
                   "as valid structured output (it did not recover after "
                   "the bounded retry)"),
    "empty": "the goal-understanding response was empty",
    "timeout": "the goal-understanding request timed out",
    "rate_limit": "the goal-understanding request was rate limited",
    "service_unavailable": "the goal-understanding service was "
                           "temporarily unavailable",
}


def _gu_failure_class(diagnostics: dict | None) -> str | None:
    """The ACTUAL goal-understanding failure class from this run's
    diagnostics — never invented. Returns None when the evidence is
    insufficient to name a class (the disclosure then states the
    uncertainty instead of a fabricated reason)."""
    if not diagnostics:
        return None
    so = diagnostics.get("structured_output") or {}
    if so.get("error_class") == "json_parse":
        return "json_parse"
    err = str(diagnostics.get("llm_error") or "")
    head = err.split(":", 1)[0]
    if any(k in head for k in ("JSONDecodeError", "ValidationError")):
        return "json_parse"
    if "EmptyResponse" in head:
        return "empty"
    if "Timeout" in head:
        return "timeout"
    if "RateLimit" in head:
        return "rate_limit"
    if any(k in head for k in ("LLMError", "APIConnectionError",
                               "APIStatusError", "ConnectionError")):
        return "service_unavailable"
    return None


def _gu_failure_disclosure(diagnostics: dict | None,
                           lang: str = "zh") -> str:
    """Honest disclosure of a degraded (heuristic-fallback) contract's
    basis, using this run's real failure class; preserves uncertainty
    when the evidence cannot name one."""
    cls = _gu_failure_class(diagnostics)
    if lang == "en":
        reason = _GU_DISCLOSURE_EN.get(cls)
        if reason is None:
            reason = ("model-based intent review could not be verified "
                      "for this run (the specific technical reason could "
                      "not be confirmed)")
        return ("Note: " + reason + ", so only the deterministically "
                "identified deliverables were executed; whether your "
                "full request is covered could not be confirmed.")
    reason = _GU_DISCLOSURE_ZH.get(cls)
    if reason is None:
        reason = "本次目标理解未能成功验证（无法确认更具体的技术原因）"
    return (f"注意：{reason}，系统仅执行了确定性识别出的交付项；"
            "无法确认已覆盖您请求的全部内容。")


def _is_duplicated_detail_line(ln: str) -> bool:
    """R3 non-owner strip (U00033): a section that shares an observation
    whose detail payload is owned elsewhere drops the duplicated detail
    content — per-candidate rows, the record facet table (and its
    truncation note), and the matching-criteria line (rendered by the
    detail owner beside the list). The section's verdict summary and
    budget boundary stay: the counts / coverage conclusion live here."""
    if ln.startswith("- ") and ("↔" in ln or " vs " in ln):
        return True
    if ln.startswith("|"):                      # facet record table
        return True
    if ln.startswith("（对话内显示前") or \
            ln.startswith("(dialogue shows the first"):
        return True                             # facet table's own note
    if ln.startswith("匹配判据:") or \
            ln.startswith("Matching criteria:"):
        return True
    return False


def _is_owner_duplicated_line(ln: str) -> bool:
    """R3 owner strip (U00033): the detail OWNER drops the verdict
    summary line and the budget-boundary note its sibling (existence)
    section keeps — each appears exactly once across the composed
    answer. The matching-criteria line stays with the candidate list."""
    if ln.startswith("注意: 集合交易证据检索受预算限制") or \
            ln.startswith("Note: set trade evidence retrieval was "
                          "budget-"):
        return True
    if ln.startswith("发现") and "候选对冲交易关系" in ln:
        return True
    if ln.startswith("Found ") and "candidate hedging" in ln:
        return True
    return False


def _compose_gsp_response(state: AgentLoopState) -> str:
    """GSP WP-GSP-5 — per-output response composition.

    The ACCEPTED GoalContract is the semantic source of truth: the answer
    is assembled per RequiredOutput from its satisfaction state and its
    associated evidence ONLY. Observations with no output association are
    never rendered into the answer body (they remain in Activity).
    Deterministic; no LLM; no re-interpretation of the user request.
    """
    from app.goal_postconditions import (
        GoalOutcome, _combine_summaries, derive_goal_outcome)

    contract = state.goal_contract
    # per-turn response language: None/unknown keeps the existing default
    # face (zh) byte-for-byte; "en" renders the English variants.
    lang = state.response_language if state.response_language in ("zh", "en") \
        else "zh"
    lines: list[str] = []
    excluded = 0
    # R3 — shared-observation detail dedup: one observation legally serving
    # multiple outputs renders its FULL evidence payload exactly once,
    # owned by the detail-delivery output (the last retrieval-kind output
    # in contract order; else the first serving output). Every other
    # serving output keeps its own verdict, match criteria and budget
    # notes, but its section drops the duplicated per-candidate detail
    # entries and gains an explicit reference to the owning section.
    _order = [o.output_id for o in contract.outputs]
    _by_id = {o.output_id: o for o in contract.outputs}
    _owner_of = {}
    _summary_holder = {}
    for _i, _obs in enumerate(state.observations):
        _served = [x for x in _order
                   if x in (_obs.get("for_outputs") or [])]
        if len(_served) > 1:
            _detail = [x for x in _served
                       if _by_id[x].claim_kind == ClaimKind.RETRIEVAL]
            _owner_of[_i] = _detail[-1] if _detail else _served[0]
            # U00047 (ART-85d6d23849): with THREE+ serving outputs, the
            # owner/non-owner dichotomy alone lets EVERY non-owner keep
            # the verdict summary and budget note — adjacent duplicated
            # sections. Exactly ONE section holds them: the FIRST served
            # output in contract order (the existence check).
            _summary_holder[_i] = _served[0]
    _referenced: set = set()
    for out in contract.outputs:
        assoc = [o for o in state.observations
                 if out.output_id in (o.get("for_outputs") or [])]
        excluded += len(state.observations) - len(assoc)
        combined, _ = _combine_summaries(assoc)
        if assoc:
            combined["_outcome"] = assoc[-1].get("outcome")
        # cross-domain consistency: the ACTUAL evidence types of this
        # output's observations ground the record-stream label
        combined["_actual_evidence_types"] = sorted({
            (t.value if hasattr(t, "value") else str(t))
            for o in assoc
            for t in (o.get("actual_evidence_types") or [])})
        combined["_finding_title"] = state.focused_finding_title or ""
        sec_start = len(lines)
        lines.extend(_gsp_render_output_section(out, combined,
                                                goal_text=state.goal,
                                                lang=lang))
        _my = [i for i, o in enumerate(state.observations)
               if out.output_id in (o.get("for_outputs") or [])]
        _ref_targets = sorted({
            _owner_of[i] for i in _my
            if _owner_of.get(i) not in (None, out.output_id)})
        if _ref_targets:
            # this section shares an observation whose full payload is
            # owned elsewhere: strip the duplicated DETAIL payload — the
            # per-candidate rows (the pair "↔"/"vs" entries), the record
            # facet table with its truncation note, and the matching-
            # criteria line (rendered by the detail owner beside the
            # list). The verdict summary and budget boundary STAY in the
            # summary-holder section only (see below). Reference the
            # owning section explicitly — exactly ONCE per target across
            # the composed answer (U00047: three serving outputs must not
            # each append their own copy of the pointer).
            _sec = lines[sec_start:]
            _kept = [ln for ln in _sec
                     if not _is_duplicated_detail_line(ln)]
            lines[sec_start:] = _kept
            if len(_kept) != len(_sec):
                _ref_key = tuple(_ref_targets)
                if _ref_key not in _referenced:
                    _referenced.add(_ref_key)
                    _own_pos = _order.index(out.output_id)
                    _ref_pos = min(_order.index(t) for t in _ref_targets)
                    _direction = ("上方" if _ref_pos < _own_pos else "下方") \
                        if "zh" == lang else ("above" if _ref_pos < _own_pos
                                              else "below")
                    _ref_header = " / ".join(
                        ((_by_id[t].claim_description or "")
                         .strip().rstrip("。") or t) for t in _ref_targets)
                    lines.append(
                        f"（完整候选明细见{_direction}「{_ref_header}」部分，"
                        "本项结论不受影响。）" if "zh" == lang else
                        f"(Full candidate details in the \"{_ref_header}\" "
                        f"section {_direction}; this verdict is "
                        "unaffected.)")
        if _my and any(_owner_of.get(i) is not None for i in _my) \
                and not any(_summary_holder.get(i) == out.output_id
                            for i in _my):
            # U00047 (ART-85d6d23849) / U00033: every NON-holder serving
            # section drops the verdict summary line and the budget-
            # boundary note — the summary HOLDER (the first served output
            # in contract order, typically the existence check) keeps
            # them, so each renders exactly once no matter how many
            # outputs share the observation. The matching-criteria line
            # stays with the detail owner's candidate list.
            _sec = lines[sec_start:]
            _kept = [ln for ln in _sec
                     if not _is_owner_duplicated_line(ln)]
            lines[sec_start:] = _kept
        # U00047 presentation: after the R3 strips a SHARING section may
        # hold nothing but its header (its payload is fully presented by
        # the summary-holder / detail-owner sections) — drop the bare
        # header instead of rendering an empty paragraph. Sections with
        # any body line (verdict, boundary, reference, per-output
        # satisfaction note) are never touched; non-sharing sections are
        # unaffected.
        _section_shares = any(_owner_of.get(i) is not None for i in _my)
        if _section_shares:
            _sec_now = lines[sec_start:]
            if len(_sec_now) <= 1 or not any(
                    ln.strip() for ln in _sec_now[1:]):
                del lines[sec_start:]      # collapsed: no header, no gap
            else:
                lines.append("")
        else:
            lines.append("")
    outcome = derive_goal_outcome(
        [o.satisfaction for o in contract.outputs], state.agent_status,
        intent_source=contract.intent_source)
    state.goal_outcome = outcome.value
    # WP-GS-C (design §7.3): with an uncovered comparison intent the
    # covered deliverables' result is real work, but the outcome line is
    # BASIS-SCOPED — loop completion is never presented as the
    # comparison being answered, and the uncovered intent is disclosed.
    dec = state.goal_contract_decision or {}
    if lang == "en":
        if (dec.get("coverage") == "comparison_intent_uncovered"
                and state.agent_status == AgentStatus.COMPLETED.value
                and outcome in (GoalOutcome.COMPLETE, GoalOutcome.PARTIAL)):
            covered = ("fully delivered" if outcome == GoalOutcome.COMPLETE
                       else "partially delivered")
            lines.append(
                f"Overall result: the accepted deliverables were {covered}; "
                "however the system could not structure your comparison "
                "request as a structured deliverable — the results below "
                "do NOT constitute a same/different conclusion.")
        else:
            lines.append(
                f"Overall result: "
                f"{_OUTCOME_EN.get(outcome.value, outcome.value)}")
    elif (dec.get("coverage") == "comparison_intent_uncovered"
            and state.agent_status == AgentStatus.COMPLETED.value
            and outcome in (GoalOutcome.COMPLETE, GoalOutcome.PARTIAL)):
        covered = "已完成" if outcome == GoalOutcome.COMPLETE else "部分完成"
        lines.append(
            f"整体结果: 已接受的调查交付项{covered}；但系统未能将您的"
            "比较需求建立为结构化交付目标——以下结果不构成“同一/不同”"
            "的结论。")
    else:
        lines.append(
            f"整体结果: {_OUTCOME_ZH.get(outcome.value, outcome.value)}。")
        # Q2 closure — 情形 A: a degraded contract whose every identified
        # deliverable IS answered must not claim unfinished items it
        # cannot name ("见各项说明"). The PARTIAL outcome value is kept
        # (B-closure protection); only the wording becomes accurate.
        if contract.intent_source == "heuristic" and contract.outputs \
                and all(o.satisfaction == OutputSatisfaction.SATISFIED
                        for o in contract.outputs):
            lines[-1] = (
                "整体结果: 已识别的交付项均已回答；由于本次目标理解未能"
                "由模型完成验证，无法确认是否还有未识别的请求内容。")
    # Completion-state protection (P0/B closure): a degraded contract
    # (deterministic fallback — the model never reviewed the request)
    # must not read as a full answer even when its outputs are all
    # delivered; disclose the basis honestly — with the REAL failure
    # class from this run's diagnostics, never an invented one.
    if contract.intent_source == "heuristic":
        _gu = _gu_failure_disclosure(
            state.goal_understanding_diagnostics, lang=lang)
        lines.append(_gu)
    steps = [s.get("type") for s in state.plan_steps
             if s.get("status") == PlanStepStatus.SUCCESS.value]
    if steps:
        lines.append(f"Steps completed: {len(steps)} "
                     f"({', '.join(steps)}).")
    if state.artifact_ids:
        lines.append(f"Artifacts produced: {', '.join(state.artifact_ids)}.")
    if state.agent_status in (AgentStatus.BLOCKED.value,
                              AgentStatus.FAILED.value) and state.response:
        lines.append(state.response)
    _telemetry.emit_event(
        "response.composed",
        investigation_id=state.investigation_id,
        task_id=state.task_id,
        contract_id=contract.contract_id,
        decision=state.goal_outcome,
        output_count=len(contract.outputs),
        summary=f"excluded_unassociated={excluded}",
        guard_enabled=True)
    return "\n".join(lines)


# WP-GS-A: completion-claim closure policy. Production ALWAYS enforces
# the completion-safety guard (INV-C: no whole-request goal-completion
# claim without an accepted contract's derived outcome). LEGACY — the
# byte-exact pre-CG closure wording — is reachable ONLY through this
# module attribute, which only test code (the baseline regression
# suite's dependency-injection seam) ever sets; there is no environment
# variable, flag, request field, or canonical runtime path that selects
# it.
_completion_closure_policy_override: str | None = None


def _completion_closure_policy() -> str:
    """'GUARD' (default, production) or 'LEGACY' (byte-exact baseline
    closure, injectable by the isolated regression suite only)."""
    if _completion_closure_policy_override:
        return _completion_closure_policy_override
    return "GUARD"


def compose_loop_response(state: AgentLoopState) -> str:
    # GSP WP-GSP-5: with the flag ON and an accepted contract, the answer
    # is composed PER REQUIRED OUTPUT from the postcondition verdicts and
    # associated evidence only. Flag OFF / legacy (no contract) → the
    # pre-GSP composer below, byte-compatible.
    from app.config import settings as _cfg
    if getattr(_cfg, "ENABLE_GOAL_SEMANTIC_CONTRACT", False) \
            and state.goal_contract is not None:
        return _compose_gsp_response(state)
    # AL-2.12 response ownership: the ANSWER leads (finding-level or
    # case-level, composed from bounded observation digests); the status
    # phrase and execution recap FOLLOW as secondary metadata. A run whose
    # scope was only partially answerable says so (BOUNDED_DATA); a
    # "conclusive empty" wording is reserved for a supported query with a
    # complete zero (no other useful data in the run).
    answer = _case_level_answer_lines(state) or _finding_answer_lines(state)
    lines: list[str] = []
    lines.extend(answer)
    status_phrase = _STATUS_PHRASES.get(state.agent_status,
                                        "The investigation ended.")
    if state.agent_status == AgentStatus.COMPLETED.value \
            and state.stop_reason == StopReason.BOUNDED_DATA:
        status_phrase = (
            "调查已在当前可用数据范围内完成:请求中的一部分范围真实不可用"
            "(见上方数据边界说明),其余结论均来自 Risk Platform 权威证据。")
    elif state.agent_status == AgentStatus.COMPLETED.value \
            and state.stop_reason == StopReason.CONCLUSIVE_EMPTY_RESULT:
        status_phrase = (
            "调查完成:所查询的范围在 Risk Platform 中确认为零条匹配记录"
            "(该查询受支持且检索范围完整)。")
    if answer:
        lines.append(status_phrase)
    else:
        lines.insert(0, status_phrase)
    # WP-GS-A completion-claim guard (generalizes the WP-CG-3 guard):
    # FLAG-INDEPENDENT. With NO accepted contract — either flag state —
    # the system has no structured evidence that the user's whole
    # request was covered, so the closure states the EXECUTION/EVIDENCE
    # basis and explicitly withholds a "request fully answered"
    # judgment. Partial results are kept; no missing-goal list is
    # fabricated. Contract runs keep the GSP-5 outcome line. LEGACY
    # (byte-exact pre-CG wording) is reachable only via the test-only
    # dependency-injection seam (_completion_closure_policy_override) —
    # never from the canonical runtime.
    if (_completion_closure_policy() == "GUARD"
            and state.goal_contract is None
            and state.agent_status == AgentStatus.COMPLETED.value):
        lines.append(
            "注意：本次调查未能建立完整的目标分解——以上结果可能未覆盖您"
            "请求的全部内容；系统未建立目标语义契约，不对“您的请求已得到"
            "完整回答”作出判定。如需其余部分，请补充说明或分别提出。")
    # WP-GS-C: comparison intent detected but not covered by the accepted
    # contract → the outcome line is basis-scoped and the uncovered
    # intent is disclosed. Never silently absorbed into a
    # retrieval-satisfied goal.
    dec = state.goal_contract_decision or {}
    if dec.get("coverage") == "comparison_intent_uncovered" \
            and state.agent_status == AgentStatus.COMPLETED.value:
        lines.append(
            "注意：系统未能将您的比较需求建立为结构化交付目标——以下结果"
            "不构成“同一/不同”的结论。")
    if state.response and state.response not in lines[0] \
            and state.agent_status in (AgentStatus.BLOCKED.value,
                                       AgentStatus.FAILED.value):
        lines.append(state.response)
    steps = [s.get("type") for s in state.plan_steps
             if s.get("status") == PlanStepStatus.SUCCESS.value]
    if steps:
        lines.append(f"Steps completed: {len(steps)} "
                     f"({', '.join(steps)}).")
    for obs in state.observations[-3:]:
        lines.append(f"- {_observation_line(obs)}")
    if state.artifact_ids:
        lines.append(f"Artifacts produced: {', '.join(state.artifact_ids)}.")
    if state.agent_status == AgentStatus.STOPPED_BY_LIMIT.value:
        lines.append(f"Stop reason: {state.stop_reason} after "
                     f"{state.iteration} iterations.")
    elif state.stop_reason and state.agent_status in (
            AgentStatus.BLOCKED.value, AgentStatus.FAILED.value):
        lines.append(f"Stop reason: {state.stop_reason}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UI-safe activity projection (Agent Activity baseline §11): whitelisted
# fields only — never the internal state verbatim, never observations/
# decision internals/retry maps. The timeline rows come from the bounded
# activity records; the plan snapshot carries statuses for §8 rendering.
# ---------------------------------------------------------------------------

def _actual_evidence_types(step_type: str, tool_name: str,
                           observation: dict[str, Any]) -> list[EvidenceType]:
    """GSP WP-GSP-3 (correction): the evidence types THIS execution
    actually produced — determined from the real parameterization
    (binding parameter locks) and the real observation payload, never
    from the capability declaration, tool name, or skill prose.

    Priority:
      1. parameter locks (stream=…) — 1:1 with record streams
      2. payload shape (view=evidence streams flags; signal_type; payload
         whitelists per tool)
      3. cannot determine → [] (never guessed)
    """
    binding = STEP_TOOL_MAP.get(step_type or "")
    locks = (binding.parameter_locks if binding else {}) or {}
    summary = observation.get("summary") or {}
    outcome = observation.get("outcome")

    # Gate: only a valid execution produces evidence. integration_error /
    # validation_error / execution_error produced NOTHING (never typed).
    if outcome not in ("success", "empty"):
        return []

    # finding_drilldown record views: the PAYLOAD's own stream flags are
    # the actual truth (the parameter lock is the request, the streams are
    # what was really included); parameterization is the fallback only
    # when the payload carries no stream information at all.
    if tool_name == "finding_drilldown":
        if locks.get("view") == "self_opposite_trades" \
                or observation.get("semantic_status") == "self_opposite_trades":
            return [EvidenceType.TRANSACTION_RECORDS]
        if observation.get("semantic_status") == "evidence" \
                or locks.get("view") == "evidence":
            streams = summary.get("streams") or {}
            types = []
            if streams.get("withdrawals_included"):
                types.append(EvidenceType.WITHDRAWAL_RECORDS)
            if streams.get("transactions_included"):
                types.append(EvidenceType.TRANSACTION_RECORDS)
            return types
        if locks.get("view") == "timeline":
            return []
        return []

    # parameter-locked analytical tools: deterministic 1:1
    if locks.get("analysis_type") == "cross_account_opposite_trade":
        return [EvidenceType.OPPOSITE_TRADE_RELATIONS]
    if locks.get("analysis_type") == "related_account_analysis":
        # Authoritative network attribution echo. The echoed
        # shared_ip_attributions field is DATA, not a declared capability
        # (data ≠ capability): shared_ip_evidence stays excluded.
        types = []
        if summary.get("related_accounts") or summary.get(
                "related_accounts_count"):
            types.append(EvidenceType.NETWORK_RELATIONS)
        if summary.get("shared_devices"):
            types.append(EvidenceType.SHARED_DEVICE_EVIDENCE)
        return types

    # signal_explain: the executed detector decides (semantic_status is
    # the tool's own signal_type for THIS run; summary fields corroborate)
    if tool_name == "signal_explain":
        signal = observation.get("semantic_status")
        by_signal = {"ML": EvidenceType.ML_SIGNAL_EXPLANATION,
                     "Rule": EvidenceType.RULE_EXPLANATION,
                     "Graph": EvidenceType.GRAPH_CLUSTER_FACTS,
                     # AL-2.14/F7-class: a Feature signal IS the factor
                     # explanation (the feature values ground the
                     # why-flagged/criteria questions)
                     "Feature": EvidenceType.RULE_EXPLANATION}
        if signal in by_signal:
            return [by_signal[signal]]
        types = []
        if summary.get("ml_score") is not None or summary.get(
                "detection_method"):
            types.append(EvidenceType.ML_SIGNAL_EXPLANATION)
        if summary.get("rule_name") or summary.get("threshold") is not None \
                or summary.get("rule_description"):
            types.append(EvidenceType.RULE_EXPLANATION)
        if summary.get("cluster_name") or summary.get("detection_type"):
            types.append(EvidenceType.GRAPH_CLUSTER_FACTS)
        return types

    if tool_name == "risk_case_fetch" and summary.get("finding_count"):
        return [EvidenceType.CASE_SUMMARY]
    if tool_name == "artifact_bundle" and outcome == "success":
        return [EvidenceType.CASE_SUMMARY]
    if summary.get("matches") is not None or tool_name == "policy_lookup" \
            and outcome == "success":
        return [EvidenceType.POLICY_REFERENCES]
    return []


def associate_observation(state: AgentLoopState,
                          observation: dict[str, Any]) -> None:
    """GSP WP-GSP-3 (corrected) — deterministic association driven by the
    ACTUAL evidence this observation produced.

    ASSOCIATION IS NOT SATISFACTION. Writes onto the observation dict:

        actual_evidence_types  list[EvidenceType] — what THIS execution
                               really produced (from parameterization +
                               payload; never copied from the capability
                               declaration)
        for_outputs            accepted outputs whose evidence requirements
                               intersect the ACTUAL types (possibly empty
                               = UNASSOCIATED; never force-filled)
        association_reason     evidence_type_match |
                               no_evidence_type_match | no_contract

    Layering: `StepToolBinding.supported_evidence_types` is the declared
    capability UPPER BOUND — actual types outside it are excluded from
    association and surfaced via telemetry (never silently treated as
    valid evidence). Subject/scope confirmation belongs to the WP-GSP-4
    postcondition evaluator: an associated observation is candidate
    evidence, not an answered output.
    """
    observation["for_outputs"] = []
    observation["association_reason"] = None
    # Layer B first — actual evidence is EXECUTION TRUTH, computed
    # unconditionally (it does not depend on a contract existing).
    actual = set(_actual_evidence_types(
        observation.get("step_type") or "",
        observation.get("tool_name") or "",
        observation))
    observation["actual_evidence_types"] = sorted(actual, key=lambda t: t.value)
    # GSP WP-GSP-6: the authoritative step→output map (built at planning
    # admission) takes precedence over the evidence-type seed matcher.
    mapping = state.step_output_map or {}
    if observation.get("step_id") in mapping:
        mapped = mapping[observation["step_id"]]
        observation["for_outputs"] = mapped
        observation["association_reason"] = (
            "contract_step_mapping" if mapped else "no_contract")
        return
    contract = state.goal_contract
    if contract is None:
        observation["association_reason"] = "no_contract"
        return
    binding = STEP_TOOL_MAP.get(observation.get("step_type") or "")
    declared = {t for t in (binding.supported_evidence_types if binding
                            else [])}
    undeclared = {t for t in actual
                  if t not in declared and t.value not in declared}
    if undeclared:
        # Non-silent validation: actual evidence outside the declared
        # capability is excluded from association and surfaced.
        from app import telemetry as _t_warn
        _t_warn.emit_event(
            "observation.evidence_undeclared",
            investigation_id=state.investigation_id,
            task_id=state.task_id,
            tool_name=observation.get("tool_name"),
            reason_class="undeclared_actual_evidence",
            evidence_kind=",".join(sorted(t.value for t in undeclared)),
            guard_enabled=True)
    admissible = actual - undeclared
    observation["actual_evidence_types"] = sorted(actual, key=lambda t: t.value)
    if not admissible:
        observation["association_reason"] = (
            "no_declared_evidence_type" if not actual
            else "no_evidence_type_match")
        return
    matched = [out.output_id for out in contract.outputs
               if admissible.intersection(out.evidence_requirements)]
    observation["for_outputs"] = matched
    observation["association_reason"] = (
        "evidence_type_match" if matched else "no_evidence_type_match")


def project_task_activity(state: AgentLoopState) -> dict[str, Any]:
    """Bounded, UI-safe projection of one Agent Loop run for the frontend.
    Explicit whitelist; no internal-only structures."""
    return {
        "task_id": state.task_id,
        "investigation_id": state.investigation_id,
        "case_id": state.case_id,
        "goal": state.goal,
        "criteria_kind": state.criteria_kind,
        "focused_finding_id": state.focused_finding_id,
        "agent_status": state.agent_status,
        "stop_reason": state.stop_reason,
        "iteration": state.iteration,
        # WP-CG-3: structured provenance of the contract decision
        # (additive; None on legacy/flag-off runs).
        "goal_contract_decision": (
            dict(state.goal_contract_decision)
            if state.goal_contract_decision else None),
        "max_iterations": _max_iterations(),
        "plan_revisions": state.plan_revisions,
        "plan_steps": [
            {"step_id": s.get("step_id"), "type": s.get("type"),
             "status": s.get("status"), "tool_name": s.get("tool_name")}
            for s in state.plan_steps
        ],
        "artifact_ids": list(state.artifact_ids),
        "activity": [dict(r) for r in state.activity],
        # GSP WP-GSP-2: the accepted contract (runtime governance state,
        # user-safe content: claim labels/subjects/scopes — no reasoning,
        # no prompts). Read model for E2E verification and the future
        # GSP surfaces; the frontend currently consumes neither this nor
        # goal_representation.
        "goal_contract": (
            state.goal_contract.model_dump(mode="json")
            if state.goal_contract else None),
        # derived on the fly (deterministic): the composer sets
        # state.goal_outcome AFTER the pre-compose persist, so the stored
        # document cannot carry it — derive from contract + status instead.
        "goal_outcome": (
            state.goal_outcome
            or (derive_goal_outcome(
                [o.satisfaction for o in state.goal_contract.outputs],
                state.agent_status,
                intent_source=state.goal_contract.intent_source).value
                if state.goal_contract else None)),
        # GSP WP-GSP-3: per-observation output associations (bounded to
        # the kept observations; additive key the frontend does not
        # consume). Empty when the flag is off or the run is legacy.
        "observation_associations": [
            {"step_type": o.get("step_type"),
             "tool_name": o.get("tool_name"),
             "outcome": o.get("outcome"),
             "actual_evidence_types": [
                 (t.value if hasattr(t, "value") else t)
                 for t in (o.get("actual_evidence_types") or [])],
             "for_outputs": o.get("for_outputs") or [],
             "association_reason": o.get("association_reason")}
            for o in state.observations if "for_outputs" in o
        ],
        # Stage 2.1: structured Question / Goal Representation (user-safe
        # projection; no reasoning, no prompts)
        "goal_representation": (
            dict(state.goal_representation)
            if state.goal_representation else None),
        "is_terminal": state.agent_status in TERMINAL_AGENT_STATUSES,
    }

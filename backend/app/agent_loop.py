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
import logging
import uuid
from enum import Enum
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from app.context_resolution import ResolutionStatus
from app.executor_v2 import ExecutorV2, ExecutorError
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
from app.skills import SKILLS, eligible_skills_for_finding
from app import telemetry as _telemetry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

class AgentStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED_BY_LIMIT = "stopped_by_limit"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED_RESUMABLE = "interrupted_resumable"   # resume: deferred (§12)


class StopReason:
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
            obs["semantic_status"] = data.get("analysis_status")
            cfg = data.get("config") or {}
            obs["summary"] = {
                "candidates_found": data.get("candidates_found"),
                "candidates_emitted": data.get("candidates_emitted"),
                "candidates": data.get("candidates") or [],
                "totals": data.get("totals"),
                "retrieval": data.get("retrieval"),
                "data_quality": data.get("data_quality"),
                "accounts_analyzed": data.get("accounts_analyzed") or [],
                # AL-2.12: the matching criteria belong to the user answer
                "max_time_delta_seconds": cfg.get("max_time_delta_seconds"),
                "value_tolerance_relative":
                    cfg.get("value_tolerance_relative"),
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
                # AL-2.12: graph facts belong to the answer too (F5-class)
                "cluster_name": graph.get("cluster_name"),
                "detection_type": graph.get("detection_type"),
                "member_count": graph.get("member_count"),
                "cluster_risk_score": graph.get("cluster_risk_score"),
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
        return (f"Found {n} candidate cross-account opposite-trade "
                "relationship"
                f"{'s' if n != 1 else ''} (candidates only, not conclusions).")
    if status == "RELATED_ACCOUNTS_FOUND":
        n = s.get("related_accounts_count")
        return (f"Risk Platform network attribution: {n} related account"
                f"{'s' if n != 1 else ''} for this account.")
    if status == "NO_NETWORK_EVIDENCE":
        return ("The Risk Platform provides no network attribution for "
                "this case.")
    if status == "NO_MATCH":
        return ("No candidate cross-account opposite-trade relationships "
                "matched within the configured tolerances for the analyzed "
                "scope.")
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
            return bool(self.artifact_ids)
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

    def propose(self, state: AgentLoopState) -> DecisionProposal:
        last = state.last_observation()
        has_pending = bool(state.pending_steps())
        outcome = (last or {}).get("outcome")
        if outcome in (ToolResultOutcome.INTEGRATION_ERROR.value,
                       ToolResultOutcome.VALIDATION_ERROR.value):
            decision = LoopDecision.BLOCK.value
        elif outcome == ToolResultOutcome.UNSUPPORTED.value:
            decision = LoopDecision.BLOCK.value
        elif state.criteria_satisfied():
            decision = LoopDecision.STOP.value
        elif has_pending:
            decision = LoopDecision.CONTINUE.value
        elif state.conclusive_empty():
            decision = LoopDecision.STOP.value
        else:
            decision = LoopDecision.RE_PLAN.value
        return DecisionProposal(
            decision=decision,
            reason="deterministic fallback policy",
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
            # Thinking-style models emit reasoning before the JSON text; a
            # small budget truncates the answer mid-way (same lesson as the
            # planner's 2048 budget — captured live in planner_v2).
            text = llm.generate(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=2048, temperature=0.0)
            import json as _json
            payload = _json.loads(
                _extract_json_object(text or ""))
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
            # AL-2.13 no-progress convergence: an identical SUCCESS result
            # that was already observed BEFORE the current (re-)plan means
            # the re-plan re-runs the same query with the same normalized
            # inputs — executing it again yields no new information.
            # Different args or a genuinely different result never match
            # the fingerprint, so legitimate repeats are unaffected.
            last = state.last_observation() or {}
            if (last.get("outcome") == ToolResultOutcome.SUCCESS.value
                    and state.plan_revisions >= 2):
                fp = _observation_fingerprint(last)
                if fp and any(
                        _observation_fingerprint(o) == fp
                        for o in state.observations[:-1]):
                    return EnforcedDecision(
                        decision=LoopDecision.STOP,
                        reason=("the re-planned query reproduced an "
                                "identical result with no new "
                                "information; the available data has "
                                "been fully reported"),
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
        # Stage 2.1: optional goal-understanding callable
        # (request_text, focused_finding_id, case_id) -> GoalRepresentation.
        self.goal_resolver = goal_resolver
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
            # Stage 2.1: the structured goal representation is produced
            # BEFORE target resolution so every run — including
            # blocked/unresolved ones — carries its understood objective
            # (baseline §35b).
            if self.goal_resolver is not None:
                try:
                    produced = self.goal_resolver(
                        goal, state.focused_finding_id, state.case_id)
                    if produced is not None:
                        state.goal_representation = produced.project()
                except Exception:
                    logger.warning("goal representation failed", exc_info=True)
            # Resolve authoritative findings at run start (QA-04 boundary):
            # a dependency failure here is a distinct BLOCKED state — never
            # a planning failure, never silent success.
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
        elif self.resolver is not None:
            resolution = self.resolver.resolve(goal, context,
                                               findings=findings)
            ctx = resolution.updated_context
            if resolution.status in (ResolutionStatus.AMBIGUOUS,
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
        else:
            ctx = context
            state.focused_finding_id = context.focused_finding_id
            state.focus_source = (context.focus_source.value
                                  if context.focus_source else None)
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
                                            finding_caps)
            if plan is None:
                return self._terminate(
                    state, task, AgentStatus.FAILED,
                    StopReason.PLANNING_FAILURE,
                    f"The investigation could not be planned within the "
                    f"supported investigation surface ({failure_code}).",
                    _now, plan=None)
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
        while True:
            # the snapshot mirrors the executor-mutated PlanSteps so step
            # selection always reflects reality
            for ps in plan.steps:
                for ref in state.plan_steps:
                    if ref["step_id"] == ps.step_id:
                        ref["status"] = ps.status.value
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
                state.observations.append(observation)
                if len(state.observations) > _MAX_OBSERVATIONS_KEPT:
                    state.observations = state.observations[
                        -_MAX_OBSERVATIONS_KEPT:]
                self._emit("observation.recorded", state, task,
                           step_id=step.step_id, step_type=step.type,
                           tool_name=observation["tool_name"],
                           outcome=observation["outcome"],
                           iteration=state.iteration,
                           summary=observation.get("display")
                           or _observation_line(observation))

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

            # --- decision (proposal → deterministic enforcement) -------------
            proposal = proposer.propose(state)
            enforced = self.policy.enforce(proposal, state)
            state.decisions.append({
                "iteration": state.iteration,
                "decision": enforced.decision.value,
                "reason": enforced.reason[:300],
                "proposed": proposal.decision,
                "coerced_from": enforced.coerced_from,
            })
            self._emit("decision.made", state, task,
                       iteration=state.iteration,
                       decision=enforced.decision.value,
                       summary=enforced.reason)
            self._persist(state, task)

            # --- act ----------------------------------------------------------
            if enforced.decision == LoopDecision.STOP:
                if enforced.limit_reached:
                    # bound reached: terminal semantics are STOPPED_BY_LIMIT
                    # — NEVER ordinary completion (baseline §9)
                    self._mark_current_done(state)
                    return self._terminate(
                        state, task, AgentStatus.STOPPED_BY_LIMIT,
                        StopReason.ITERATION_BOUND_REACHED,
                        enforced.reason, _now, plan=plan,
                        artifacts=produced_artifacts)
                status = AgentStatus.COMPLETED
                reason = StopReason.GOAL_SATISFIED
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
                new_plan = self._re_plan(planner, state, goal, ctx,
                                         eligible_ids, finding_caps)
                if new_plan is None:
                    return self._terminate(
                        state, task, AgentStatus.FAILED,
                        StopReason.REPLAN_FAILURE,
                        "The investigation could not re-plan within the "
                        "supported surface after the latest observation.",
                        _now, plan=plan,
                        artifacts=produced_artifacts)
                plan = new_plan
                self._adopt_plan(state, plan)
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

    def _plan(self, planner, goal, ctx, eligible_ids, caps):
        """Returns (plan | None, bounded_failure_code | None). The failure
        code surfaces in the loop state so planning failures are
        diagnosable from the activity trace (QA-01 retest observability)."""
        if planner is None:
            from app.planner_v2 import PlannerV2
            planner = PlannerV2()
        result = planner.plan(goal, ctx, eligible_ids,
                              finding_capabilities=caps)
        if hasattr(result, "steps"):
            return result, None
        return None, getattr(result, "code", None) or "PLANNING_FAILURE"

    def _re_plan(self, planner, state, goal, ctx, eligible_ids, caps) -> Plan | None:
        """Receding-horizon re-plan (baseline §10): PlannerV2 again, with the
        original goal + a COMPACT progress digest (never raw tool results).
        The new plan passes the same registry/contract validation."""
        if planner is None:
            from app.planner_v2 import PlannerV2
            planner = PlannerV2()
        digest_lines = [
            f"- completed: {[s.get('type') for s in state.plan_steps if s.get('status') == 'success']}",
        ]
        for obs in state.observations[-2:]:
            digest_lines.append(f"- observation: {_observation_line(obs)}")
        for d in state.decisions[-1:]:
            digest_lines.append(f"- last decision: {d.get('decision')}"
                                f" ({d.get('reason')})")
        replan_request = (f"{goal}\n\n"
                          "[Investigation progress — compact summary]\n"
                          + "\n".join(digest_lines))
        result = planner.plan(replan_request, ctx, eligible_ids,
                              finding_capabilities=caps)
        return result if hasattr(result, "steps") else None

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
        self._persist(state, task)
        return AgentLoopResult(state=state,
                               response=compose_loop_response(state),
                               task=task,
                               artifacts=list(artifacts or []))

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


def _signal_answer_lines(s: dict[str, Any]) -> list[str]:
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
            shown = ", ".join(
                f"{_feature_label(k)} = {v}"
                for k, v in list(features.items())[:5])
            more = (f"（共 {len(features)} 项）"
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
        more = (f"（共 {len(feats)} 项）" if len(feats) > 5 else "")
        lines.append("风险信号:行为特征检测发现该账号存在异常行为模式 — "
                     + (f"观测到 {shown}{more}。" if shown else ""))
        lines.append("该账号的实际行为特征值是 Risk Platform 标记此风险"
                     "发现的依据。当前 RP 数据不提供特征归因,因此不能"
                     "断言某一个具体特征贡献了多少分。")
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
        lines.append("| # | 记录 ID | 时间 | 明细 |")
        lines.append("|---|---|---|---|")
        for i, r in enumerate(rows, 1):
            lines.append(f"| {i} | {r.get('record_id', '')} | "
                         f"{r.get('timestamp', '')} | "
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
            if status == "MATCH_FOUND":
                n = s.get("candidates_found")
                lines.append(
                    f"有。在当前检索范围内发现 {n} 个候选跨账户对冲交易"
                    "关系。")
                delta = s.get("max_time_delta_seconds")
                rel = s.get("value_tolerance_relative")
                if delta is not None and rel is not None:
                    lines.append(
                        f"匹配条件:两笔交易时间相差不超过 {delta:g} 秒,"
                        f"且价值差异在 {rel:.0%} 以内。这些候选关系是量化"
                        "匹配的调查线索,不构成对操纵或交易意图的结论。")
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
                lines.append(
                    "没有。在当前检索范围内未发现满足匹配条件的跨账户对冲"
                    "交易关系。该结论仅适用于已分析的账户与数据窗口。")
            elif status == "INSUFFICIENT_DATA":
                lines.append(
                    "无法得出可靠结论:可用交易证据不完整或不足(检索受限或"
                    "为空)。这不是\"未发现\"——是当前数据不足以支持判断。")
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


def compose_loop_response(state: AgentLoopState) -> str:
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
        "max_iterations": _max_iterations(),
        "plan_revisions": state.plan_revisions,
        "plan_steps": [
            {"step_id": s.get("step_id"), "type": s.get("type"),
             "status": s.get("status"), "tool_name": s.get("tool_name")}
            for s in state.plan_steps
        ],
        "artifact_ids": list(state.artifact_ids),
        "activity": [dict(r) for r in state.activity],
        # Stage 2.1: structured Question / Goal Representation (user-safe
        # projection; no reasoning, no prompts)
        "goal_representation": (
            dict(state.goal_representation)
            if state.goal_representation else None),
        "is_terminal": state.agent_status in TERMINAL_AGENT_STATUSES,
    }

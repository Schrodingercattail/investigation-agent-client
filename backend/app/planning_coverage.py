"""GSP WP-GSP-6 — Planner Coverage Admission (deterministic).

Every Accepted RequiredOutput must receive an explicit, deterministic
planning coverage verdict at plan-admission time:

    COVERED                    a legal executable step path exists in the
                               adopted plan that can acquire ≥1 of the
                               output's evidence requirements
    NO_ELIGIBLE_CAPABILITY     no executable binding declares any of the
                               output's evidence requirements (the
                               reserved planning-time verdict; the output
                               is marked UNSUPPORTED and rendered as a
                               capability boundary)
    NOT_COVERED_BY_PLAN        the output's requirements are declarable by
                               the registry, but the LLM's proposed plan
                               contains no matching step (explicit
                               planning omission — recorded and rendered,
                               never silently accepted as complete)

Pure functions over the registry (STEP_TOOL_MAP) and the accepted
GoalContract. No LLM, no network, no DB, no mutation. The LLM may propose
steps; THIS module decides coverage (LLM proposes, system admits).

Contract: `evidence_requirements` is an unordered ANY-set (design
§4.1a) — an output is COVERED when at least one available step type
declares at least one of its requirement categories. Requirement list
order and observation order never influence the verdict.

Capability ≠ data availability: COVERED means a legal evidence-
acquisition path exists — whether RP actually holds data is decided
downstream (execution → EMPTY → postconditions).
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from app.skills import EvidenceType, STEP_TOOL_MAP


class CoverageStatus(str, Enum):
    COVERED = "covered"
    NO_ELIGIBLE_CAPABILITY = "no_eligible_capability"
    NOT_COVERED_BY_PLAN = "not_covered_by_plan"


# reason codes
R_EVIDENCE_TYPE_MATCH = "evidence_type_match"
R_NO_ELIGIBLE_CAPABILITY = "no_eligible_capability"
R_PLANNING_OMISSION = "planning_omission"


@dataclass
class OutputCoverage:
    """Deterministic planning coverage verdict for one RequiredOutput."""
    output_id: str
    coverage_status: str
    eligible_step_types: list[str] = field(default_factory=list)
    matched_evidence_types: list[str] = field(default_factory=list)
    reason: str | None = None


def _binding_evidence_types(step_type: str) -> set:
    binding = STEP_TOOL_MAP.get(step_type)
    return set(binding.supported_evidence_types) if binding else set()


def _matched_types(step_type: str, requirements) -> set:
    declared = _binding_evidence_types(step_type)
    required = {t.value if hasattr(t, "value") else t
                for t in (requirements or [])}
    return declared & required


def eligible_step_types_for_output(output, step_types=None) -> list:
    """All registry step types whose executable binding declares at least
    one of the output's evidence requirements (capability existence —
    independent of any LLM proposal)."""
    types = list(step_types) if step_types is not None \
        else list(STEP_TOOL_MAP.keys())
    return [st for st in types
            if _matched_types(st, output.evidence_requirements)]


def capability_coverage(output, available_step_types=None) -> OutputCoverage:
    """Capability-level admission for ONE output (pre-planning):
    does the registry declare a legal evidence-acquisition path?"""
    eligible = eligible_step_types_for_output(output, available_step_types)
    matched = sorted({t for st in eligible
                      for t in _matched_types(st,
                                              output.evidence_requirements)})
    if eligible:
        return OutputCoverage(
            output_id=output.output_id,
            coverage_status=CoverageStatus.COVERED.value,
            eligible_step_types=eligible,
            matched_evidence_types=matched,
            reason=R_EVIDENCE_TYPE_MATCH)
    return OutputCoverage(
        output_id=output.output_id,
        coverage_status=CoverageStatus.NO_ELIGIBLE_CAPABILITY.value,
        reason=R_NO_ELIGIBLE_CAPABILITY,
        matched_evidence_types=[],
    )


def admit_plan_coverage(contract, plan_step_types,
                        available_step_types=None) -> list:
    """Per-output planning coverage over the ADOPTED plan's step types.

    Three-way verdict per output (no conflation):
    - COVERED: ≥1 ADOPTED step type declares ∩ the output's requirements.
    - NO_ELIGIBLE_CAPABILITY: NO registry step type declares any of them
      (the system has no legal acquisition path for this output at all).
    - NOT_COVERED_BY_PLAN: registry capability EXISTS but the proposed
      plan omitted every covering step (explicit planning omission —
      recorded and rendered as not-determined; never silently accepted).
    """
    records = []
    for out in contract.outputs:
        eligible_in_plan = eligible_step_types_for_output(
            out, plan_step_types)
        if eligible_in_plan:
            matched = sorted({t for st in eligible_in_plan
                              for t in _matched_types(st,
                                                      out.evidence_requirements)})
            records.append(OutputCoverage(
                output_id=out.output_id,
                coverage_status=CoverageStatus.COVERED.value,
                eligible_step_types=eligible_in_plan,
                matched_evidence_types=matched,
                reason=R_EVIDENCE_TYPE_MATCH))
            continue
        # capability-level existence check over the full registry
        registry_eligible = eligible_step_types_for_output(out)
        if registry_eligible:
            records.append(OutputCoverage(
                output_id=out.output_id,
                coverage_status=CoverageStatus.NOT_COVERED_BY_PLAN.value,
                reason=R_PLANNING_OMISSION,
                matched_evidence_types=[]))
        else:
            records.append(OutputCoverage(
                output_id=out.output_id,
                coverage_status=CoverageStatus.NO_ELIGIBLE_CAPABILITY.value,
                reason=R_NO_ELIGIBLE_CAPABILITY,
                matched_evidence_types=[]))
    return records


# ---------------------------------------------------------------------------
# Coverage completion (deterministic supplement). Admission (above) decides
# the verdict; THIS half repairs the one repairable verdict: an output whose
# requirements are registry-declarable but whose covering step the proposed
# plan omitted gets its legal step appended once — BEFORE admission, so the
# adopted plan is complete when the verdict is computed. LLM proposes, the
# SYSTEM admits a complete plan.
# ---------------------------------------------------------------------------

# Step tools whose evidence path is FINDING-scoped: with no finding target
# resolvable for an output's subject they cannot execute in this run, so an
# account/case-subject output prefers the executable case-scoped types.
_FINDING_SCOPED_TOOLS = frozenset({"finding_drilldown", "signal_explain"})

# The system's canonical finding-id shape (adapter-assigned "F<n>"); an
# output whose subject IS a finding pins the appended step to it.
_FINDING_ID_RE = re.compile(r"^F\d+$")

# U00033 metric-semantics closure: the reverse-trade-rate METRIC deliverable
# (facet reverse_trade_metric_semantics) needs the target_account_metrics
# echo — produced ONLY by analyze_cross_account_trades in target_account
# scope. Related-account steps succeed but their payload carries no such
# metrics, so they must never be counted as covering (or supplementing) a
# metric-semantics output.
_METRIC_FACET = "reverse_trade_metric_semantics"
_METRIC_EVIDENCE_STEP = "analyze_cross_account_trades"
_METRIC_NON_COVERING_STEPS = ("analyze_related_accounts",
                              "analyze_related_account_set_trades")


def _requires_target_account_metrics(output) -> bool:
    facets = {f.value if hasattr(f, "value") else str(f)
              for f in (output.postcondition_facets or [])}
    return _METRIC_FACET in facets


def step_covers_output(step, output) -> bool:
    """Whether ONE adopted step can legally serve ONE output: the step's
    type declares ∩ the output's requirements AND it carries no explicit
    target CONFLICTING with the output's subject. An explicit finding_id
    restricts only FINDING-subject outputs (deterministic decomposition
    pairing: an F-subject output is served solely by its own finding's
    evidence). A CASE/ACCOUNT-subject output has no finding-level subject
    to conflict with — a finding anchor on such a step is the ACCESS PATH
    to the case's authoritative record stream, never a subject
    restriction."""
    if step.type not in eligible_step_types_for_output(output):
        return False
    # U00033 metric-semantics closure: related-account analysis steps do
    # not produce target_account_metrics, so they never cover a metric
    # deliverable — the cross-account analysis (which echoes the
    # authoritative opposite_trade_ratio) is the covering step.
    if _requires_target_account_metrics(output) \
            and step.type in _METRIC_NON_COVERING_STEPS:
        return False
    # U00047 delivery closure: generate_artifact is the DELIVERY channel
    # for record streams, not an evidence-acquisition path — a bare
    # artifact step (no records retrieval before it) renders an empty
    # bundle, so it never covers a record-stream requirement by itself.
    if step.type == "generate_artifact":
        req_values = {t.value if hasattr(t, "value") else str(t)
                      for t in (output.evidence_requirements or [])}
        if req_values & {"withdrawal_records", "transaction_records"}:
            return False
    args = getattr(step, "arguments", None)
    target = args.get("finding_id") if isinstance(args, dict) else None
    if not target:
        return True
    if target == output.subject_ref:
        return True
    return not _FINDING_ID_RE.match((output.subject_ref or "").strip())


@dataclass
class SupplementSpec:
    """One deterministically-appended coverage step (spec only — callers
    build the PlanStep). `finding_target` pins an F-subject output's step
    to its finding; None lets runtime injection resolve the target."""
    step_type: str
    finding_target: str | None
    output_ids: list[str] = field(default_factory=list)


def _ordered_eligible(output) -> list:
    """Eligible step types in preference order: a finding-subject output
    keeps registry order (its target is pinnable); any other subject puts
    finding-scoped-only paths last — a step whose required target could
    never be resolved for this subject must not be preferred.

    Scope preservation (B-fix): a SYSTEM-canonical set-internal hedging
    output (claim = the related-account-set existence check) is served by
    the SET-INTERNAL analysis step — registry-locked
    analysis_scope=related_account_set — never degraded to the
    target-anchored step (which would narrow the analysis to pairs
    involving the case account only)."""
    eligible = eligible_step_types_for_output(output)
    # U00047 delivery closure: for record-stream requirements the
    # supplement must add the RECORDS RETRIEVAL step — generate_artifact
    # is the delivery channel, never the acquisition path.
    _req_values = {t.value if hasattr(t, "value") else str(t)
                   for t in (output.evidence_requirements or [])}
    if _req_values & {"withdrawal_records", "transaction_records"}:
        eligible = [t for t in eligible if t != "generate_artifact"]
    # U00033 metric-semantics closure: a metric deliverable must be served
    # by the cross-account analysis (the only target_account_metrics
    # source) — related-account steps are not eligible for it.
    if _requires_target_account_metrics(output):
        eligible = [t for t in eligible
                    if t not in _METRIC_NON_COVERING_STEPS]
        eligible = [t for t in eligible if t != "generate_artifact"]
    if _FINDING_ID_RE.match((output.subject_ref or "").strip()):
        return eligible
    eligible = sorted(
        eligible,
        key=lambda t: (1 if (STEP_TOOL_MAP.get(t) and
                             STEP_TOOL_MAP[t].tool_name
                             in _FINDING_SCOPED_TOOLS) else 0,))
    # U00010 B1 (EN, Batch B1): an output requiring EXACTLY ONE record
    # stream must be supplemented with the STREAM-SPECIALIZED step
    # (inspect_withdrawals / inspect_transactions), never the generic
    # inspect_evidence — the generic view has no stream lock and no
    # stream anchor, and historically executed INVALID_ARGUMENT.
    from app.goal_resolution import EvidenceType
    reqs = {getattr(e, "value", e)
            for e in (output.evidence_requirements or [])}
    _SPECIALIZED = {
        "withdrawal_records": "inspect_withdrawals",
        "transaction_records": "inspect_transactions",
    }
    stream_reqs = reqs & set(_SPECIALIZED)
    if len(stream_reqs) == 1:
        preferred = _SPECIALIZED[next(iter(stream_reqs))]
        if preferred in eligible:
            eligible = ([preferred]
                        + [t for t in eligible if t != preferred])
    from app.goal_resolution import _CLAIM_HEDGING_SET
    if (getattr(output, "claim", "") or "") == _CLAIM_HEDGING_SET \
            and "analyze_related_account_set_trades" in eligible:
        return (["analyze_related_account_set_trades"]
                + [t for t in eligible
                   if t != "analyze_related_account_set_trades"])
    return eligible


def coverage_insert_index(existing_steps) -> int:
    """Where supplemented evidence steps belong in the plan: BEFORE a
    trailing artifact step. The bundle composes only from already-
    executed results, so evidence appended after the artifact step could
    never reach it (answer and artifact would diverge). Position = before
    the LAST generate_artifact step; plan end when there is none. Pure."""
    last_artifact = -1
    for i, s in enumerate(existing_steps):
        if getattr(s, "type", None) == "generate_artifact":
            last_artifact = i
    return last_artifact if last_artifact >= 0 else len(existing_steps)


def coverage_supplement_steps(contract, existing_steps,
                              skip_satisfied=False) -> list:
    """Deterministic coverage completion over a proposed/adopted plan.

    Every Accepted RequiredOutput that (a) has ≥1 registry-declared
    evidence path, (b) is not yet SATISFIED (re-plan passes
    skip_satisfied=True), and (c) is not served by any existing step,
    gets its FIRST preferred eligible step type appended ONCE.
    Subject-aware: an already-appended step covers later outputs of the
    same type with the same (or no) target — never duplicated per output.

    Pure: returns specs, mutates nothing. Bounded: ≤1 supplement per
    output per call; callers stay inside the loop's iteration cap. An
    output with NO registry-declared path is never supplemented here —
    it keeps its admission verdict (NO_ELIGIBLE_CAPABILITY → UNSUPPORTED,
    the honest capability boundary).
    """
    specs: list[SupplementSpec] = []
    for out in contract.outputs:
        if skip_satisfied and \
                getattr(out.satisfaction, "value", out.satisfaction) \
                == "satisfied":
            continue
        if any(step_covers_output(s, out) for s in existing_steps):
            continue
        eligible = _ordered_eligible(out)
        if not eligible:
            continue
        if any(s.step_type in eligible
               and (s.finding_target is None
                    or s.finding_target == out.subject_ref)
               for s in specs):
            continue        # an appended step already serves this output
        subject = (out.subject_ref or "").strip()
        specs.append(SupplementSpec(
            step_type=eligible[0],
            finding_target=subject if _FINDING_ID_RE.match(subject) else None,
            output_ids=[out.output_id]))
    return specs

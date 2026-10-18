"""Week 1 Skill Registry and Contract Checker.

Implements the normative skill definitions from
docs/architecture/SKILL_MODEL_V1.md as static, data-driven records —
no runtime class hierarchy, no execution framework.

Boundary (enforced by structure):

- The future LLM Planner receives ONLY the eligible skills and their
  planning vocabulary (`eligible_skills_for_finding`, `SKILLS`,
  `PlanningVocabulary`). It is never trusted to invent skill ids, step
  types, tool names, or fixed parameters: every such identifier must come
  from this registry and survive `check_plan` validation.
- The deterministic `check_plan` is reusable both planner-side (before a
  plan is accepted) and by the future executor/runtime (defense in depth).
- Contract violations are structured `ContractViolation` results, NOT
  exceptions for ordinary violations, and NOT ToolResult outcomes:
  plan validation never pretends a Risk Platform tool ran.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models import FindingCapability, PlanStep


def _supports(capabilities: Any, capability: str) -> bool:
    """Capability membership across FindingCapability / raw sets (None = no
    capabilities at all)."""
    if capabilities is None:
        return False
    supports = getattr(capabilities, "supports", None)
    if supports is not None:
        return supports(capability)
    return capability in capabilities


# ---------------------------------------------------------------------------
# GSP evidence vocabulary (WP-GSP-1) — frozen, executable-backed
# ---------------------------------------------------------------------------
# Each value names a kind of evidence an EXECUTABLE tool binding actually
# produces today. Authoritative source for capability admission is the
# StepToolBinding declaration (below) — never SkillDefinition prose
# (SKILL_MODEL_V1: Skill = discoverability; Tool binding = executable
# contract). Closed set: adding a value REQUIRES a binding declaration
# plus a payload test proving the tool really returns it
# (implementation plan §10). Claims/assessments (e.g.
# "money_laundering_assessment") deliberately have NO entry here —
# they are user-side judgments, not evidence kinds.
class EvidenceType(str, Enum):
    CASE_SUMMARY = "case_summary"
    NETWORK_RELATIONS = "network_relations"
    SHARED_DEVICE_EVIDENCE = "shared_device_evidence"
    # Reserved (WP-GSP-1b echoes the RP field); INTENTIONALLY UNDECLARED by
    # every binding in Slice 1 — the runtime contract for turning this data
    # into an answered output lands with the postcondition evaluator.
    # Declaring it now would imply a capability the runtime cannot yet
    # honor end-to-end (no nearest-capability fallback, ever).
    SHARED_IP_EVIDENCE = "shared_ip_evidence"
    OPPOSITE_TRADE_RELATIONS = "opposite_trade_relations"
    WITHDRAWAL_RECORDS = "withdrawal_records"
    TRANSACTION_RECORDS = "transaction_records"
    ML_SIGNAL_EXPLANATION = "ml_signal_explanation"
    RULE_EXPLANATION = "rule_explanation"
    GRAPH_CLUSTER_FACTS = "graph_cluster_facts"
    POLICY_REFERENCES = "policy_references"


EVIDENCE_TYPES: frozenset = frozenset(t.value for t in EvidenceType)


# ---------------------------------------------------------------------------
# Static step → tool mapping (single source of truth; no arbitrary mappings)
# ---------------------------------------------------------------------------

class StepToolBinding(BaseModel):
    """Maps one planning-step type to its executing tool.

    `parameter_locks` pin tool arguments the Planner may not change,
    e.g. finding_drilldown under timeline_investigation is locked to
    view="timeline".

    `supported_evidence_types` (GSP WP-GSP-1) is the AUTHORITATIVE
    capability declaration: which EvidenceType values this executable
    path really produces, verified against actual tool payloads. Empty
    list = this path declares no evidence type (must never be selected
    to answer an evidence requirement). Declarations are static registry
    data — never inferred from tool names or skill descriptions.
    """

    tool_name: str
    parameter_locks: dict[str, Any] = Field(default_factory=dict)
    supported_evidence_types: list[EvidenceType] = Field(default_factory=list)


STEP_TOOL_MAP: dict[str, StepToolBinding] = {
    # Declarations (GSP WP-GSP-1) state what each executable path REALLY
    # produces today, verified against actual tool payloads. Empty list =
    # declares nothing (explicit, never a guess). shared_ip is deliberately
    # NOT declared anywhere in Slice 1 (see EvidenceType note).
    "fetch_case": StepToolBinding(
        tool_name="risk_case_fetch",
        supported_evidence_types=[EvidenceType.CASE_SUMMARY],
    ),
    "generate_artifact": StepToolBinding(
        tool_name="artifact_bundle",
        supported_evidence_types=[EvidenceType.CASE_SUMMARY],
    ),
    # Event-level evidence type intentionally deferred (no declared type in
    # this vocabulary slice; timeline records are not a declared kind yet).
    "inspect_timeline": StepToolBinding(
        tool_name="finding_drilldown",
        parameter_locks={"view": "timeline"},
    ),
    # view=evidence returns the finding's COMPLETE record streams — both
    # withdrawal and transaction records appear depending on the finding.
    "inspect_evidence": StepToolBinding(
        tool_name="finding_drilldown",
        parameter_locks={"view": "evidence"},
        supported_evidence_types=[EvidenceType.WITHDRAWAL_RECORDS,
                                  EvidenceType.TRANSACTION_RECORDS],
    ),
    "inspect_withdrawals": StepToolBinding(
        tool_name="finding_drilldown",
        parameter_locks={"view": "evidence", "stream": "withdrawals"},
        supported_evidence_types=[EvidenceType.WITHDRAWAL_RECORDS],
    ),
    "inspect_transactions": StepToolBinding(
        tool_name="finding_drilldown",
        parameter_locks={"view": "evidence", "stream": "transactions"},
        supported_evidence_types=[EvidenceType.TRANSACTION_RECORDS],
    ),
    # Canonical self-opposite step: opposite-side trading by the SAME
    # account. Verified live (WP-GSP-3): the view executes and produces
    # same-account opposite-side transaction records + RP ratio echo;
    # finding-level capability gates can still return UNSUPPORTED
    # (capability ≠ data availability). Declares the records it emits.
    "inspect_self_opposite_trades": StepToolBinding(
        tool_name="finding_drilldown",
        parameter_locks={"view": "self_opposite_trades"},
        supported_evidence_types=[EvidenceType.TRANSACTION_RECORDS],
    ),
    # Derived analytical tool: candidate cross-account opposite-trade
    # relationships (baseline §12b). analysis_type is locked — the planner
    # can never emit another value.
    "analyze_cross_account_trades": StepToolBinding(
        tool_name="cross_account_trade_analysis",
        parameter_locks={"analysis_type": "cross_account_opposite_trade"},
        supported_evidence_types=[EvidenceType.OPPOSITE_TRADE_RELATIONS],
    ),
    # Set-internal cross-account analysis: the user named a related-account
    # POPULATION and asked for hedging WITHIN it — candidate pairs may
    # involve the case account or only two related members. The analysis
    # scope is registry-locked (system-selected, never LLM-chosen).
    "analyze_related_account_set_trades": StepToolBinding(
        tool_name="cross_account_trade_analysis",
        parameter_locks={
            "analysis_type": "cross_account_opposite_trade",
            "analysis_scope": "related_account_set",
        },
        supported_evidence_types=[EvidenceType.OPPOSITE_TRADE_RELATIONS],
    ),
    # AL-2.10: case-level related-account analysis (RP network
    # attribution echo). analysis_type is locked the same way.
    # GSP: shared_ip_evidence is deliberately NOT declared — the payload
    # echo (WP-GSP-1b) is data pass-through; the executable
    # answer-contract for shared-IP questions is not landed yet
    # (no nearest-capability substitution: shared_device ≠ shared_ip).
    "analyze_related_accounts": StepToolBinding(
        tool_name="related_account_analysis",
        parameter_locks={"analysis_type": "related_account_analysis"},
        supported_evidence_types=[EvidenceType.NETWORK_RELATIONS,
                                  EvidenceType.SHARED_DEVICE_EVIDENCE],
    ),
    # signal_explain handles all three detector families (ML / Rule /
    # Graph), each rendering its own explanation payload.
    "explain_signal": StepToolBinding(
        tool_name="signal_explain",
        supported_evidence_types=[EvidenceType.ML_SIGNAL_EXPLANATION,
                                  EvidenceType.RULE_EXPLANATION,
                                  EvidenceType.GRAPH_CLUSTER_FACTS],
    ),
    "retrieve_policy": StepToolBinding(
        tool_name="policy_lookup",
        supported_evidence_types=[EvidenceType.POLICY_REFERENCES],
    ),
}


# Parameterizations each registered tool actually implements. Registry truth
# for "can this bound path execute in the current runtime?" — a step whose
# locked parameters are absent here is registered-but-not-executable (P19).
# Keys: (tool_name, locked-parameter key/value pairs that must ALL be
# supported; a step with no locks always passes).
_TOOL_SUPPORTED_PARAMETERIZATIONS: dict[str, set[tuple[tuple[str, Any], ...]]] = {
    # finding_drilldown implements the timeline, evidence, and
    # self_opposite_trades views. inspect_withdrawals /
    # inspect_transactions carry an EXPLICIT evidence-stream scope so a
    # record request can never silently select the wrong stream via the
    # finding-title heuristic.
    "finding_drilldown": {
        (("view", "timeline"),),
        (("view", "evidence"),),
        (("view", "evidence"), ("stream", "withdrawals")),
        (("view", "evidence"), ("stream", "transactions")),
        (("view", "self_opposite_trades"),),
        (),                       # default view (no view argument)
    },
    # cross_account_trade_analysis implements exactly the one registered
    # analysis (baseline §12b).
    "cross_account_trade_analysis": {
        (("analysis_type", "cross_account_opposite_trade"),),
        (("analysis_type", "cross_account_opposite_trade"),
         ("analysis_scope", "related_account_set")),
    },
    # AL-2.10: the one registered related-account analysis.
    "related_account_analysis": {
        (("analysis_type", "related_account_analysis"),),
    },
}


def step_path_executable(step_type: str) -> bool:
    """Whether this planning step's bound path can execute in the current
    runtime: the tool must be registered AND its locked parameterization
    must be one the tool actually implements. The single semantic answer
    shared by planner eligibility and follow-up executability."""
    binding = STEP_TOOL_MAP.get(step_type)
    if binding is None:
        return False
    locks = tuple(sorted(binding.parameter_locks.items()))
    supported = _TOOL_SUPPORTED_PARAMETERIZATIONS.get(
        binding.tool_name)
    if supported is None:
        return True               # tool declares no restrictions
    return locks in supported


def skill_path_executable(skill_id: str) -> bool:
    """Whether a skill's PRIMARY investigation step (its first non-artifact
    planning step) is executable. Artifact generation is a continuation, not
    the investigation itself; a skill whose core path cannot execute must not
    be offered to the planner (P19)."""
    skill = SKILLS.get(skill_id)
    if skill is None:
        return False
    for step_type in skill.planning_steps:
        if step_type == "generate_artifact":
            continue
        return step_path_executable(step_type)
    return True


# ---------------------------------------------------------------------------
# Skill definition (declarative record — deliberately not a class hierarchy)
# ---------------------------------------------------------------------------

class SkillDefinition(BaseModel):
    """One Week 1 skill. Plain validated data; registry entries are values."""

    skill_id: str
    name: str
    description: str
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str]
    planning_steps: list[str]
    constraints: list[str] = Field(default_factory=list)


SKILLS: dict[str, SkillDefinition] = {
    "case_intake": SkillDefinition(
        skill_id="case_intake",
        name="Case Intake",
        description=(
            "Fetch the authoritative picture of a case and produce an "
            "intake summary."
        ),
        required_capabilities=[],
        allowed_tools=["risk_case_fetch", "artifact_bundle", "policy_lookup"],
        planning_steps=["fetch_case", "retrieve_policy", "generate_artifact"],
        constraints=[
            "Runs at case level; ignores focused_finding_id.",
            "If no cached case context exists for the session, fetch_case "
            "must precede generate_artifact.",
            "generate_artifact may compose only from steps executed in "
            "this plan.",
            # Policy retrieval is CASE-WIDE (the skill-registry expression of
            # the capability/data-semantics correction): it needs no finding
            # capability and is plannable for every finding via this always-
            # eligible skill. The planner resolves retrieve_policy's
            # finding_id from the focused finding (executor runtime
            # injection), so a check_policy continuation can never become a
            # dead button on a timeline-ineligible finding (P13/P19).
            "retrieve_policy is a policy-context continuation: it never "
            "substitutes for timeline investigation and its result reports "
            "finding_policy_status (associated | no_finding_level_basis).",
        ],
    ),
    "timeline_investigation": SkillDefinition(
        skill_id="timeline_investigation",
        name="Timeline Investigation",
        description=(
            "Investigate a focused finding through its chronological "
            "evidence timeline."
        ),
        required_capabilities=["timeline"],
        allowed_tools=[
            "finding_drilldown",   # locked to view="timeline"
            "signal_explain",
            "policy_lookup",
            "artifact_bundle",
        ],
        planning_steps=[
            "inspect_timeline",
            "inspect_evidence",
            "inspect_withdrawals",
            "inspect_transactions",
            "inspect_self_opposite_trades",
            "explain_signal",
            "retrieve_policy",
            "generate_artifact",
        ],
        constraints=[
            "Requires InvestigationContext.focused_finding_id.",
            "Executable only if the focused finding declares capability "
            "'timeline'.",
            "finding_drilldown is locked to view='timeline' for "
            "inspect_timeline and view='evidence' for inspect_evidence; "
            "any other view is invalid under this skill.",
            "inspect_evidence (concrete evidence) retrieves the COMPLETE "
            "record set; inspect_timeline stays a bounded navigation view.",
        ],
    ),
    "trade_investigation": SkillDefinition(
        skill_id="trade_investigation",
        name="Trade Investigation",
        description=(
            "Investigate a focused finding through the trades composing "
            "its opposite-trade signal, including candidate cross-account "
            "opposite-trade analysis."
        ),
        required_capabilities=["self_opposite_trades"],
        allowed_tools=[
            "cross_account_trade_analysis",
            "finding_drilldown",   # locked to view="self_opposite_trades"
            "signal_explain",
            "policy_lookup",
            "artifact_bundle",
        ],
        # The cross-account analysis is the skill's primary investigation
        # step and the only executable path today (P19): the same-account
        # self-opposite drilldown view stays registered-but-unimplemented.
        planning_steps=[
            "analyze_cross_account_trades",
            "inspect_self_opposite_trades",
            "explain_signal",
            "retrieve_policy",
            "generate_artifact",
        ],
        constraints=[
            "Requires InvestigationContext.focused_finding_id.",
            "Executable only if the focused finding declares capability "
            "'self_opposite_trades'.",
            "analyze_cross_account_trades is the derived candidate "
            "cross-account opposite-trade analysis; its analysis_type is "
            "locked by the registry and its thresholds are system "
            "configuration — results are candidate relationships, never "
            "confirmed coordinated trading. The analysis is anchored on "
            "the case account: every candidate contains a case-account "
            "transaction.",
            "finding_drilldown is locked to view='self_opposite_trades' "
            "(opposite-side trading by the SAME account); any other view "
            "is invalid under this skill.",
        ],
    ),
    # AL-2.6: the CASE-LEVEL analytical route. The same governed analysis
    # step as trade_investigation's primary step, but scoped to the whole
    # case's transaction universe — the underlying tool needs only the
    # case_id (executor-injected), so no focused finding is required.
    # Deliberately SEPARATE from trade_investigation (whose finding-level
    # capability contract is unchanged): this is the governed answer to a
    # supported case-level cross-account trade investigation intent, not a
    # relaxation of finding-level routing.
    "case_trade_analysis": SkillDefinition(
        skill_id="case_trade_analysis",
        name="Case Trade Analysis",
        description=(
            "Case-level analytical investigation: candidate cross-account "
            "opposite-trade relationships across the case's transaction "
            "universe, with the analyzed account scope reported."
        ),
        required_capabilities=[],
        allowed_tools=[
            "cross_account_trade_analysis",
            "related_account_analysis",
            "artifact_bundle",
        ],
        planning_steps=[
            "analyze_cross_account_trades",
            "analyze_related_account_set_trades",
            "analyze_related_accounts",
            "generate_artifact",
        ],
        constraints=[
            "Case-level skill: executes against the investigation's own "
            "case_id; no focused finding required and none is selected.",
            "analyze_cross_account_trades is registry-locked to "
            "analysis_type='cross_account_opposite_trade'; results are "
            "candidate relationships plus the analyzed account scope — "
            "never confirmed coordinated trading. The analysis is "
            "anchored on the case account (its own transactions are "
            "retrieved completely within budget; every candidate "
            "contains a case-account transaction). "
            "analyze_related_account_set_trades is the SET-INTERNAL "
            "variant (analysis_scope='related_account_set'): when the "
            "user names a related-account population and asks about "
            "hedging WITHIN it, candidate pairs may involve the case "
            "account or only two related members — the analyzed set is "
            "{case account} ∪ {authoritative related accounts}.",
            "generate_artifact composes the analysis (including an "
            "insufficient-data result with its analyzed scope) into the "
            "user-visible evidence bundle.",
        ],
    ),
    # AL-2.10: the CASE-LEVEL related-account capability. Answers "which
    # accounts are related to this account" from the Risk Platform's own
    # network attribution — never routed through finding resolution.
    "case_related_accounts": SkillDefinition(
        skill_id="case_related_accounts",
        name="Related Account Analysis",
        description=(
            "Case-level related-account analysis: the Risk Platform's "
            "network attribution for the case's subject account (related "
            "accounts, shared devices, cluster facts)."
        ),
        required_capabilities=[],
        allowed_tools=["related_account_analysis"],
        planning_steps=["analyze_related_accounts"],
        constraints=[
            "Case-level skill: executes against the investigation's own "
            "case_id; no focused finding required and none is selected.",
            "analyze_related_accounts is registry-locked to "
            "analysis_type='related_account_analysis'; the payload is an "
            "authoritative Risk Platform echo (cluster, related accounts, "
            "shared devices) — never derived relationships or intent "
            "claims.",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Planning vocabulary handed to the LLM Planner (it sees nothing else)
# ---------------------------------------------------------------------------

class PlanningVocabulary(BaseModel):
    """What the LLM Planner is allowed to know/produce for one selection."""

    skill_id: str
    required_capabilities: list[str]
    allowed_tools: list[str]
    planning_steps: list[str]


def planning_vocabulary(skill_id: str) -> PlanningVocabulary | None:
    """The closed vocabulary for one skill — the complete surface exposed to
    the LLM Planner for that selection."""
    skill = SKILLS.get(skill_id)
    if skill is None:
        return None
    return PlanningVocabulary(
        skill_id=skill.skill_id,
        required_capabilities=skill.required_capabilities,
        allowed_tools=skill.allowed_tools,
        planning_steps=skill.planning_steps,
    )


def eligible_skills_for_finding(
    capabilities: FindingCapability | set[str] | frozenset[str] | None,
) -> list[SkillDefinition]:
    """Skills whose capability gates are satisfied.

    case-level skills (empty required_capabilities) are always included.
    This is the eligibility list the Planner selects from.
    """
    return [
        s for s in SKILLS.values()
        if all(_supports(capabilities, c) for c in s.required_capabilities)
    ]


# ---------------------------------------------------------------------------
# Deterministic contract checker
# ---------------------------------------------------------------------------

class ContractViolation(BaseModel):
    """One structured contract violation. Codes are stable identifiers."""

    code: str          # SKILL_NOT_FOUND | CAPABILITY_NOT_SUPPORTED |
                       # STEP_NOT_ALLOWED | TOOL_NOT_ALLOWED |
                       # PARAMETER_LOCK_VIOLATION | INVALID_STEP_TOOL_MAPPING
    step_id: str | None = None
    message: str


class PlanValidationResult(BaseModel):
    """Structured result of plan-contract validation.

    Design note: ordinary contract violations land here as structured
    errors — callers decide how to react (planner-side reject, runtime
    defense). Only truly unexpected situations raise. These are NOT
    ToolResult outcomes; no tool execution is implied.
    """

    valid: bool
    errors: list[ContractViolation] = Field(default_factory=list)

    def add(self, code: str, message: str, step_id: str | None = None) -> None:
        self.errors.append(ContractViolation(code=code, step_id=step_id, message=message))
        self.valid = False


def check_skill_eligibility(
    skill_id: str,
    capabilities: FindingCapability | set[str] | frozenset[str] | None,
) -> PlanValidationResult:
    """Rules 1–2: skill exists; required capabilities supported by the
    current Finding. Independent of any concrete plan/steps."""
    result = PlanValidationResult(valid=True)
    skill = SKILLS.get(skill_id)
    if skill is None:
        result.add("SKILL_NOT_FOUND", f"Unknown skill: {skill_id!r}")
        return result

    for cap in skill.required_capabilities:
        if not _supports(capabilities, cap):
            result.add(
                "CAPABILITY_NOT_SUPPORTED",
                f"Skill {skill.skill_id!r} requires capability {cap!r} which the "
                "current finding does not support.",
            )
    return result


def check_plan(
    skill_id: str,
    steps: list[PlanStep],
    capabilities: FindingCapability | set[str] | frozenset[str] | None = None,
) -> PlanValidationResult:
    """Validate a full plan against the selected skill's contract.

    Rules enforced:
      1. The selected skill exists.                          (SKILL_NOT_FOUND)
      2. Required capabilities are provided by the current Finding.
                                                             (CAPABILITY_NOT_SUPPORTED)
      3. Every PlanStep.type ∈ skill.planning_steps.         (STEP_NOT_ALLOWED)
      4. Every PlanStep.tool_name matches the registered step→tool mapping.
                                                             (INVALID_STEP_TOOL_MAPPING)
      5. Locked arguments match parameter locks exactly on the locked keys;
             absence of a lock key or mismatch both violate. (PARAMETER_LOCK_VIOLATION)
      6. No tool outside skill.allowed_tools is invoked.     (TOOL_NOT_ALLOWED)

    Reusable planner-side and by the future executor (defense in depth).
    """
    result = PlanValidationResult(valid=True)

    # Rule 1
    skill = SKILLS.get(skill_id)
    if skill is None:
        result.add("SKILL_NOT_FOUND", f"Unknown skill: {skill_id!r}")
        return result

    # Rule 2
    for cap in skill.required_capabilities:
        if not _supports(capabilities, cap):
            result.add(
                "CAPABILITY_NOT_SUPPORTED",
                f"Skill {skill.skill_id!r} requires capability {cap!r} which "
                "the current finding does not support.",
                step_id=None,
            )

    allowed_tools = set(skill.allowed_tools)
    vocabulary = set(skill.planning_steps)

    for step in steps:
        # Rule 3 — step type belongs to this skill's planning vocabulary
        if step.type not in vocabulary:
            result.add(
                "STEP_NOT_ALLOWED",
                f"Step type {step.type!r} is not in planning_steps of skill "
                f"{skill.skill_id!r}.",
                step_id=step.step_id,
            )
            continue  # mapping/lock checks are meaningless for unknown types

        binding = STEP_TOOL_MAP.get(step.type)
        if binding is None:
            # Registered in vocabulary but unmapped — registry integrity issue.
            result.add(
                "INVALID_STEP_TOOL_MAPPING",
                f"Step type {step.type!r} has no registered tool binding.",
                step_id=step.step_id,
            )
            continue

        # Rule 4 — declared tool matches the registered mapping exactly
        if step.tool_name != binding.tool_name:
            result.add(
                "INVALID_STEP_TOOL_MAPPING",
                f"Step {step.step_id!r} of type {step.type!r} must bind to "
                f"{binding.tool_name!r}, got {step.tool_name!r}.",
                step_id=step.step_id,
            )

        # Rule 6 — no tool outside the skill's allowed set
        if binding.tool_name not in allowed_tools:
            result.add(
                "TOOL_NOT_ALLOWED",
                f"Tool {binding.tool_name!r} bound to step {step.step_id!r} is "
                f"not allowed by skill {skill.skill_id!r}.",
                step_id=step.step_id,
            )
        elif step.tool_name is not None and step.tool_name not in allowed_tools:
            result.add(
                "TOOL_NOT_ALLOWED",
                f"Tool {step.tool_name!r} is not allowed by skill "
                f"{skill.skill_id!r}.",
                step_id=step.step_id,
            )

        # Rule 5 — parameter locks: lock key must be present with exact value
        for lock_key, lock_value in binding.parameter_locks.items():
            actual = step.arguments.get(lock_key)
            if actual != lock_value:
                result.add(
                    "PARAMETER_LOCK_VIOLATION",
                    f"Step {step.step_id!r}: argument {lock_key!r} is locked "
                    f"to {lock_value!r} under skill {skill.skill_id!r}, "
                    f"got {actual!r}.",
                    step_id=step.step_id,
                )

    return result

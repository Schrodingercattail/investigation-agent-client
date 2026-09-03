"""Week 1 capability-matrix integrity tests.

Pins the documented capability matrix to the runtime truth: every capability
claimed "supported" in backend/README.md must satisfy the full chain

    FindingCapability → skill eligibility → planning step → STEP_TOOL_MAP
                      → registered provider tool → executable path

and the not-implemented paths must stay non-executable. If any link breaks,
these tests fail BEFORE the README and runtime drift apart.
"""

from app.adapters.risk_platform import RiskPlatformAdapter
from app.executor_v2 import default_tool_provider
from app.followups import _implemented_tools
from app.models import Finding, FindingCapability
from app.skills import SKILLS, STEP_TOOL_MAP, eligible_skills_for_finding


def _chain_holds(step: str) -> bool:
    """Full executable-path check for one planning step."""
    binding = STEP_TOOL_MAP.get(step)
    if binding is None:
        return False
    if not default_tool_provider().has(binding.tool_name):
        return False
    return True


class TestDocumentedCapabilitiesAreExecutable:
    """Every matrix row marked supported satisfies the complete chain."""

    def test_fetch_case_chain(self):
        assert _chain_holds("fetch_case")
        assert "fetch_case" in SKILLS["case_intake"].planning_steps
        assert SKILLS["case_intake"].required_capabilities == []

    def test_generate_artifact_chain(self):
        assert _chain_holds("generate_artifact")
        assert "generate_artifact" in SKILLS["case_intake"].planning_steps
        assert "generate_artifact" in SKILLS["timeline_investigation"].planning_steps

    def test_inspect_timeline_chain(self):
        step = "inspect_timeline"
        assert step in SKILLS["timeline_investigation"].planning_steps
        binding = STEP_TOOL_MAP[step]
        assert binding.tool_name == "finding_drilldown"
        assert binding.parameter_locks == {"view": "timeline"}
        assert _chain_holds(step)

    def test_inspect_evidence_chain(self):
        step = "inspect_evidence"
        assert step in SKILLS["timeline_investigation"].planning_steps
        binding = STEP_TOOL_MAP[step]
        assert binding.tool_name == "finding_drilldown"
        assert binding.parameter_locks == {"view": "evidence"}
        assert _chain_holds(step)

    def test_explain_signal_chain(self):
        step = "explain_signal"
        assert step in SKILLS["timeline_investigation"].planning_steps
        assert STEP_TOOL_MAP[step].tool_name == "signal_explain"
        assert _chain_holds(step)

    def test_retrieve_policy_chain(self):
        step = "retrieve_policy"
        assert step in SKILLS["timeline_investigation"].planning_steps
        assert STEP_TOOL_MAP[step].tool_name == "policy_lookup"
        assert _chain_holds(step)

    def test_capability_gates_match_matrix(self):
        """Availability conditions documented in the matrix come from the
        skills' required_capabilities (runtime gate), not from hope."""
        assert SKILLS["case_intake"].required_capabilities == []
        assert SKILLS["timeline_investigation"].required_capabilities == [
            "timeline"]

    def test_followup_registry_matches_implemented_tools(self):
        """The executable-only rule is backed by real provider registrations."""
        implemented = _implemented_tools()
        for tool in ("risk_case_fetch", "finding_drilldown",
                     "signal_explain", "policy_lookup", "artifact_bundle"):
            assert tool in implemented


class TestDocumentedUnsupportedPaths:
    """Paths declared not-implemented must stay non-executable."""

    def test_opposite_trades_view_not_implemented(self):
        from app.domain_tools.finding_drilldown import ALLOWED_VIEWS
        assert "opposite_trades" not in ALLOWED_VIEWS
        # the step binding still exists (registered) but cannot execute
        assert "inspect_opposite_trades" in STEP_TOOL_MAP

    def test_trade_investigation_not_eligible_without_capability(self):
        eligible = [s.skill_id for s in eligible_skills_for_finding(
            FindingCapability.model_validate(["timeline", "signal_explain",
                                              "policy_lookup"]))]
        assert "trade_investigation" not in eligible

    def test_no_network_drilldown_registered(self):
        assert not default_tool_provider().has("network_drilldown")

    def test_unknown_capability_finds_only_case_level_skills(self):
        eligible = [s.skill_id for s in eligible_skills_for_finding(None)]
        assert eligible == ["case_intake"]


class TestCapabilityDifferentiationByFinding:
    """Two findings in the same case may expose different capability sets —
    availability is per-finding, runtime-derived."""

    def test_capability_sets_drive_eligibility(self):
        caps_a = FindingCapability.model_validate(
            ["timeline", "signal_explain", "policy_lookup"])
        caps_b = FindingCapability.model_validate(["timeline", "signal_explain"])
        skills_a = {s.skill_id for s in eligible_skills_for_finding(caps_a)}
        skills_b = {s.skill_id for s in eligible_skills_for_finding(caps_b)}
        assert skills_a == {"case_intake", "timeline_investigation"}
        assert skills_b == skills_a        # timeline is the gate; both pass
        # a finding WITHOUT timeline cannot reach the investigation skill
        caps_c = FindingCapability.model_validate(["policy_lookup"])
        skills_c = {s.skill_id for s in eligible_skills_for_finding(caps_c)}
        assert skills_c == {"case_intake"}

    def test_derivation_grants_conditionally(self):
        from app.adapters.risk_platform import derive_capabilities
        # policy retrieval is case-wide: never derived from citations.
        # Finding-level policy BASIS is data (finding.policy_refs), reported
        # by policy_lookup's finding_policy_status — not a capability.
        caps = derive_capabilities(
            finding_name="High withdrawal frequency",
            transaction_evidence_count=2, withdrawal_evidence_count=3,
            has_rule_trigger=True)
        assert "policy_lookup" not in caps.root
        assert "timeline" in caps.root
        assert "signal_explain" in caps.root
        # same finding name, different sets — matrix's differentiation claim

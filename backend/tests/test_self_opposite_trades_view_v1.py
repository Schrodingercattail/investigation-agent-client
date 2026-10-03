"""finding_drilldown(view="self_opposite_trades") — completion-phase tests.

The canonical SAME-ACCOUNT opposite-side inspection view (baseline §8/§12d
naming; RP-owned aggregate semantics). Coverage:

1. the view is executable (P19 path)
2. same-account semantics preserved (RP ratio echoed verbatim; no pairing
   fields, no second ratio definition)
3. canonical view name works end-to-end
4. legacy alias behaves safely (canonical result, alias recorded)
5. cross-account phrasing still routes to cross_account_trade_analysis
6. self-account phrasing routes to inspect_self_opposite_trades
7. both-regexes-match case resolves to cross-account (ordering rule)
8. composer wording (same-account terms; no cross-account/candidate terms;
   no intent claims)
9. provenance preserved (transaction evidence refs; ratio source)
10. no regression to cross_account_trade_analysis (distinct bindings)

Mocked at the adapter boundary via the shared RP fixture payloads.
"""

from types import SimpleNamespace

import pytest

from app.models import Finding, FindingCapability, ToolResultOutcome
from app.domain_tools.finding_drilldown import ALLOWED_VIEWS, finding_drilldown
from tests.test_risk_platform_adapter_v2 import rp_evidence_payload

CASE = "U00299"


def capable_finding(fid="F2", title="Coordinated Trading Pattern"):
    return Finding(
        finding_id=fid, case_id=CASE, type="rule_signal", title=title,
        summary="s",
        capabilities=FindingCapability.model_validate(
            ["timeline", "signal_explain", "policy_lookup",
             "self_opposite_trades"]))


def plain_finding(fid="F3", title="High Withdrawal Frequency"):
    return Finding(
        finding_id=fid, case_id=CASE, type="rule_signal", title=title,
        summary="s",
        capabilities=FindingCapability.model_validate(
            ["timeline", "signal_explain", "policy_lookup"]))


def run_view(finding, view="self_opposite_trades", evidence=None):
    ctx = {
        "case_id": CASE,
        "findings": [finding],
        "_raw_evidence": evidence if evidence is not None
        else default_evidence(),
    }
    return finding_drilldown(finding_id=finding.finding_id, view=view,
                             case_context=ctx)


def default_evidence():
    """RP-shaped evidence with the STRUCTURED coordinated-trading rule
    (trigger/threshold/contribution) so the rule echo can be pinned
    verbatim — mirrors the evaluation fixture enrichment."""
    evidence = rp_evidence_payload(CASE)
    evidence["rule_evidence"] = [
        {"rule_name": "Coordinated Trading Pattern", "severity": "HIGH",
         "description": "An opposite-trade ratio of 45.24% exceeded the "
                        "40% threshold.",
         "trigger": {"opposite_trade_ratio": 0.4524},
         "threshold": "opposite_trade_ratio > 0.4", "contribution": 35},
    ]
    return evidence


# ---------------------------------------------------------------------------
# 1. executable path  |  3. canonical view works  |  2. same-account semantics
# ---------------------------------------------------------------------------

class TestExecutableCanonicalView:
    def test_1_view_is_registered_and_executable(self):
        from app.skills import STEP_TOOL_MAP, step_path_executable
        assert "self_opposite_trades" in ALLOWED_VIEWS
        assert STEP_TOOL_MAP["inspect_self_opposite_trades"].parameter_locks \
            == {"view": "self_opposite_trades"}
        assert step_path_executable("inspect_self_opposite_trades")

    def test_3_canonical_view_succeeds_with_rp_aggregates(self):
        out = run_view(capable_finding())
        assert out.outcome == ToolResultOutcome.SUCCESS
        data = out.data
        assert data["view"] == "self_opposite_trades"
        assert data["semantics"] == "same_account_opposite_side"
        # RP's own aggregate echoed VERBATIM — never recomputed
        assert data["opposite_trade_ratio"] == 0.4524
        assert data["ratio_source"] == "risk_platform_feature_evidence"
        assert data["account_id"] == CASE
        # the backing rule comes from RP rule_evidence (strict anchor)
        assert data["rule"]["name"] == "Coordinated Trading Pattern"
        assert data["rule"]["threshold"] == "opposite_trade_ratio > 0.4"
        # the same account's complete transaction record set
        assert data["complete"] is True
        assert data["record_count"] == 3
        # NO cross-account pairing fields exist on this payload
        for forbidden in ("candidates", "account_a", "account_b",
                          "match_type", "time_gap"):
            assert forbidden not in data

    def test_2_no_second_ratio_definition(self):
        # the ratio travels exactly as RP reported it; the tool computes
        # nothing from the record list (record sides stay presentational)
        evidence = rp_evidence_payload(CASE)
        out = run_view(capable_finding(), evidence=evidence)
        assert out.data["opposite_trade_ratio"] == \
            evidence["feature_evidence"]["opposite_trade_ratio"]


# ---------------------------------------------------------------------------
# 4. legacy alias  |  capability gate  |  EMPTY semantics
# ---------------------------------------------------------------------------

class TestAliasAndGates:
    def test_4_legacy_alias_executes_canonical_view(self):
        out = run_view(capable_finding(), view="opposite_trades")
        assert out.outcome == ToolResultOutcome.SUCCESS
        assert out.data["view"] == "self_opposite_trades"   # canonical
        assert out.data["requested_view_alias"] == "opposite_trades"

    def test_capability_gate_bounds_non_capable_finding(self):
        out = run_view(plain_finding())
        assert out.outcome == ToolResultOutcome.UNSUPPORTED
        assert out.error.code == "CAPABILITY_NOT_SUPPORTED"
        assert out.error.detail["capability"] == "self_opposite_trades"
        assert out.data is None

    def test_unknown_view_still_validation_error(self):
        out = run_view(capable_finding(), view="network_drilldown")
        assert out.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_no_activity_is_empty_not_error(self):
        bare = rp_evidence_payload(CASE)
        bare["transaction_evidence"] = []
        bare["feature_evidence"] = {}
        out = run_view(capable_finding(), evidence=bare)
        assert out.outcome == ToolResultOutcome.EMPTY
        assert "no opposite-side trading activity data" in out.warnings[0]

    def test_zero_ratio_is_a_success_answer_not_empty(self):
        bare = rp_evidence_payload(CASE)
        bare["feature_evidence"] = {"opposite_trade_ratio": 0.0}
        out = run_view(capable_finding(), evidence=bare)
        assert out.outcome == ToolResultOutcome.SUCCESS
        assert out.data["opposite_trade_ratio"] == 0.0


# ---------------------------------------------------------------------------
# 9. provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_evidence_refs_are_the_account_transactions(self):
        out = run_view(capable_finding())
        ids = sorted(r.id for r in out.evidence_refs)
        assert ids == ["TX00000", "TX00001", "TX00002"]
        assert all(r.kind == "transaction" for r in out.evidence_refs)


# ---------------------------------------------------------------------------
# 5/6/7. routing distinction (cross-account vs self)
# ---------------------------------------------------------------------------

class TestRoutingDistinction:
    def test_6_self_phrasings_route_to_self_step(self):
        from app.planner_v2 import _is_opposite_trade_request
        for text in ("check this account's opposite trades",
                     "show self-opposite trading",
                     "does this account trade both sides?",
                     "show me the opposite trade"):
            assert _is_opposite_trade_request(text), text

    def test_5_cross_account_phrasings_route_to_cross_tool(self):
        from app.planner_v2 import _is_cross_account_trade_request
        for text in ("find opposite trades across accounts",
                     "check related accounts trading opposite sides",
                     "look for cross-account opposite trades"):
            assert _is_cross_account_trade_request(text), text
        # self phrasings do NOT trip the cross-account hook
        from app.planner_v2 import _is_opposite_trade_request as opp
        assert not _is_cross_account_trade_request(
            "does this account trade both sides?")

    def test_7_both_regexes_match_cross_account_wins(self, monkeypatch):
        from app.planner_v2 import (_is_cross_account_trade_request,
                                    _is_opposite_trade_request, PlannerV2)
        from app.models import FindingCapability, FocusSource, \
            InvestigationContext

        text = "Find opposite trades across accounts."
        assert _is_cross_account_trade_request(text)
        assert _is_opposite_trade_request(text)   # the ordering hazard

        class LLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return ('{"skill_id": "trade_investigation", "goal": "g", '
                        '"steps": [{"type": "inspect_self_opposite_trades", '
                        '"reason": "r"}]}')

        caps = FindingCapability.model_validate(["self_opposite_trades"])
        ctx = InvestigationContext(case_id=CASE, focused_finding_id="F2",
                                   focus_source=FocusSource.USER_SELECTED)
        plan = PlannerV2(LLM()).plan(text, ctx, ["trade_investigation"], caps)
        # AL-2.6: the deterministic case-level branch plans this intent
        # directly; the bundle step keeps insufficient-data runs bounded.
        assert plan.steps[0].type == "analyze_cross_account_trades"
        assert [s.type for s in plan.steps] == \
            ["analyze_cross_account_trades", "generate_artifact"]

    def test_10_cross_tool_binding_unchanged(self):
        from app.skills import STEP_TOOL_MAP
        binding = STEP_TOOL_MAP["analyze_cross_account_trades"]
        assert binding.tool_name == "cross_account_trade_analysis"
        assert binding.parameter_locks == {
            "analysis_type": "cross_account_opposite_trade"}
        self_binding = STEP_TOOL_MAP["inspect_self_opposite_trades"]
        assert self_binding.tool_name == "finding_drilldown"
        assert self_binding.parameter_locks == {
            "view": "self_opposite_trades"}


# ---------------------------------------------------------------------------
# 8. composer + artifact wording
# ---------------------------------------------------------------------------

SELF_PAYLOAD = {
    "finding_id": "F2", "view": "self_opposite_trades",
    "semantics": "same_account_opposite_side",
    "account_id": CASE,
    "opposite_trade_ratio": 0.4524,
    "ratio_source": "risk_platform_feature_evidence",
    "rule": {"name": "Coordinated Trading Pattern",
             "threshold": "opposite_trade_ratio > 0.4",
             "description": "An opposite-trade ratio of 45.24% exceeded "
                            "the 40% threshold."},
    "record_count": 3,
    "complete": True,
    "records": [
        {"record_kind": "transaction", "record_id": "TX00000",
         "summary": "BUY trade of 0.5 BTC", "timestamp": "2026-08-19T10:00:00+00:00"},
        {"record_kind": "transaction", "record_id": "TX00001",
         "summary": "SELL trade of 0.5 BTC", "timestamp": "2026-08-19T11:00:00+00:00"},
        {"record_kind": "transaction", "record_id": "TX00002",
         "summary": "BUY trade of 0.5 BTC", "timestamp": "2026-08-19T12:00:00+00:00"},
    ],
}


def _compose(data):
    from app.models import ToolCallStatusV2, ToolCallV2, ToolResult
    from app.investigation_service import compose_response
    tc = ToolCallV2(
        tool_call_id="TC-SELF-1", investigation_id=f"CASE:{CASE}",
        task_id="TASK-1", tool_name="finding_drilldown",
        arguments={"view": "self_opposite_trades"},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data))
    return compose_response(
        user_request="show self-opposite trading",
        skill_id="trade_investigation", plan=None, tool_calls=[tc],
        execution_errors=[])


class TestComposerWording:
    def test_success_names_same_account_only(self):
        text = _compose(SELF_PAYLOAD)
        assert "SAME account" in text
        assert "opposite-side trading activity" in text
        assert "45.24%" in text
        assert "Coordinated Trading Pattern" in text
        # never candidate/coordinated terminology
        for fragment in ("candidate", "coordinated"):
            assert fragment not in text, fragment
        # "cross-account" may appear ONLY inside the explicit denial
        # caveat, never as a claim about relationships
        for line in text.splitlines():
            if "cross-account" in line:
                assert "implies no" in line, line

    def test_zero_ratio_worded_as_no_activity(self):
        text = _compose({**SELF_PAYLOAD, "opposite_trade_ratio": 0.0})
        assert "No opposite-side trading activity was observed" in text

    def test_empty_wording_is_distinct(self):
        from app.investigation_service import compose_response
        from app.models import ToolCallStatusV2, ToolCallV2, ToolResult
        tc = ToolCallV2(
            tool_call_id="TC-SELF-2", investigation_id=f"CASE:{CASE}",
            task_id="TASK-1", tool_name="finding_drilldown",
            arguments={"view": "self_opposite_trades"},
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.EMPTY,
                              data={"view": "self_opposite_trades"}))
        text = compose_response(
            user_request="show self-opposite trading",
            skill_id="trade_investigation", plan=None, tool_calls=[tc],
            execution_errors=[])
        assert "holds no opposite-side trading activity data" in text


class TestArtifactSection:
    def test_self_section_with_records_and_caveat(self):
        from app.domain_tools.artifact_bundle import artifact_bundle
        from app.models import ToolCallStatusV2, ToolCallV2, ToolResult
        tc = ToolCallV2(
            tool_call_id="TC-SELF-3", investigation_id=f"CASE:{CASE}",
            task_id="TASK-1", tool_name="finding_drilldown",
            arguments={"view": "self_opposite_trades"},
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS,
                              data=SELF_PAYLOAD))
        out = artifact_bundle(scope="case", case_id=CASE, task_id="TASK-1",
                              source_tool_calls=[tc])
        assert out.outcome == ToolResultOutcome.SUCCESS
        content = out.data["artifact"]["content"]
        assert "## Self-Opposite (Same-Account) Trade Activity" in content
        assert "45.24%" in content
        assert "TX00000" in content
        assert "Same-account activity only" in content
        # "cross-account" may appear ONLY in the explicit denial caveat
        for line in content.splitlines():
            if "cross-account" in line:
                assert "no cross-account" in line, line
        assert "TC-SELF-3" in out.data["artifact"]["source_tool_calls"]

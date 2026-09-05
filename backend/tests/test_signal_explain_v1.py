"""Tests for the signal_explain tool (app/domain_tools/signal_explain.py).

All RP payloads are mocked; shapes follow the actual RP schemas
(rule_evidence carries trigger/threshold/contribution per the verified
RiskScoringService-aligned derivation). No live Risk Platform, no LLM.
"""

import pytest
from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import risk_case_fetch, signal_explain
from app.executor_v2 import ExecutorV2, default_tool_provider
from app.models import (
    Finding,
    FindingCapability,
    InvestigationContext,
    TaskStatusV2,
    TaskV2,
    TimelineEvent,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2


# --- fixture with RP-faithful rule evidence ---------------------------------------

def evidence_with_real_rule_fields():
    evidence = fx.rp_evidence_payload()
    evidence["rule_evidence"] = [
        {
            "rule_name": "Coordinated Trading Pattern",
            "severity": "HIGH",
            "description": "Opposite-trade ratio exceeded 40% threshold",
            "trigger": {"opposite_trade_ratio": 0.4524},
            "threshold": "opposite_trade_ratio > 0.4",
            "contribution": 35,
        },
        {
            "rule_name": "High Withdrawal Frequency",
            "severity": "MEDIUM",
            "description": "14 withdrawals in 24h period",
            "trigger": {"withdrawal_frequency_24h": 14},
            "threshold": "withdrawal_frequency_24h > 10",
            "contribution": 20,
        },
    ]
    return evidence


def make_case(evidence=None):
    evidence = evidence or evidence_with_real_rule_fields()
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (evidence, fx.rp_explanation_payload()),
    ):
        result = risk_case_fetch("U00299")
    case_context = dict(result.data)
    case_context["_raw_evidence"] = evidence
    return result, case_context


def finding_by_title(case, needle):
    return next(f for f in case.data["findings"] if needle in f.title)


def run(case_context, finding_id, signal_type):
    return signal_explain(finding_id=finding_id, signal_type=signal_type,
                          case_context=case_context)


# --- 1–3. rule signal ---------------------------------------------------------------

class TestRuleSignal:
    def test_rule_explanation_success(self):
        case, cc = make_case()
        f = finding_by_title(case, "Coordinated Trading")
        r = run(cc, f.finding_id, "Rule")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["signal_type"] == "Rule"
        assert r.data["rule"]["name"] == "Coordinated Trading Pattern"
        assert r.data["evidence_missing"] is False

    def test_rule_trigger_data_preserved(self):
        case, cc = make_case()
        f = finding_by_title(case, "Coordinated Trading")
        r = run(cc, f.finding_id, "Rule")
        rule = r.data["rule"]
        # echoed verbatim from RP — never reconstructed
        assert rule["trigger_values"] == {"opposite_trade_ratio": 0.4524}
        assert rule["threshold"] == "opposite_trade_ratio > 0.4"
        assert rule["contribution"] == 35
        assert rule["severity"] == "HIGH"

    def test_rule_evidence_refs_preserved(self):
        case, cc = make_case()
        f = finding_by_title(case, "Coordinated Trading")
        r = run(cc, f.finding_id, "Rule")
        assert r.data["evidence_refs"]
        assert all(ref["id"] == "U00299" for ref in r.data["evidence_refs"])
        assert {ref.id for ref in r.evidence_refs} == \
               {ref["id"] for ref in r.data["evidence_refs"]}

    def test_rule_signal_without_backing_rule_is_success_plus_evidence_missing(self):
        # finding exists, supports signal_explain, has NO detector refs at
        # all (nothing to bound against) and no rule_evidence entry matches
        # → available evidence insufficient, NOT empty, NOT error.
        # (An EXPLICIT type mismatch against existing refs is bounded —
        # covered in test_p0_composition_fixes_v1::TestFixA.)
        case, cc = make_case()
        from app.models import Finding as FindingModel
        f = finding_by_title(case, "ML Pattern")
        bare = FindingModel.model_validate(f.model_dump() | {"signal_refs": []})
        cc2 = dict(cc)
        cc2["findings"] = [bare]
        r = run(cc2, bare.finding_id, "Rule")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["evidence_missing"] is True
        assert r.data["next_data_needed"]
        assert r.error is None


# --- 4–5. ML signal ------------------------------------------------------------------

class TestMLSignal:
    def test_ml_explanation_success_with_score_and_features(self):
        case, cc = make_case()
        f = finding_by_title(case, "ML Pattern")
        r = run(cc, f.finding_id, "ML")
        assert r.outcome == ToolResultOutcome.SUCCESS
        explanation = r.data["explanation"]
        assert explanation["ml_score"] == 96.24
        assert "opposite_trade_ratio" in explanation["available_feature_values"]
        assert explanation["attribution_available"] is False

    def test_ml_attribution_unavailable_is_success_evidence_missing(self):
        case, cc = make_case()
        f = finding_by_title(case, "ML Pattern")
        r = run(cc, f.finding_id, "ML")
        # score + features exist; attribution does not (RP has no such API)
        assert r.data["evidence_missing"] is True
        assert "transaction-level feature attribution" in r.data["next_data_needed"]
        assert r.outcome == ToolResultOutcome.SUCCESS   # not an error
        assert "shap" not in str(r.data).lower()         # no invented method

    def test_ml_without_any_score_or_features_still_bounded(self):
        evidence = fx.rp_evidence_payload()
        evidence["risk_summary"]["ml_score"] = None
        evidence["feature_evidence"] = {}
        case, cc = make_case(evidence)
        f = finding_by_title(case, "ML Pattern")
        r = run(cc, f.finding_id, "ML")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["evidence_missing"] is True
        assert "ML model score" in " ".join(r.data["next_data_needed"])


# --- 6. graph signal -------------------------------------------------------------------

class TestGraphSignal:
    def test_graph_explanation_with_available_evidence(self):
        evidence = evidence_with_real_rule_fields()
        evidence["network_evidence"] = {
            "cluster_id": 15, "cluster_name": "velocity_cluster",
            "detection_type": "device_sharing", "member_count": 4,
            "cluster_risk_score": 75.5, "role_in_cluster": "member",
            "related_accounts_count": 3,
            "related_accounts": ["U00011", "U00012", "U00013"],
            "shared_devices": ["dev-abc"],
        }
        case, cc = make_case(evidence)
        # create/locate a graph finding — fixture key_findings don't include
        # one, so use rule evidence path with a device-named finding title
        device_finding = Finding(
            finding_id="F9", case_id="U00299", type="graph_signal",
            title="Shared Device Relationships",
            summary="linked accounts through devices",
            capabilities=FindingCapability.model_validate(
                ["signal_explain", "timeline"]),
        )
        cc["findings"] = list(cc["findings"]) + [device_finding]
        r = run(cc, "F9", "Graph")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["explanation"]["cluster"]["cluster_id"] == 15
        assert r.data["explanation"]["related_accounts"] == [
            "U00011", "U00012", "U00013"]
        assert r.data["explanation"]["relationship_paths_available"] is False

    def test_graph_without_network_evidence_reports_missing(self):
        # graph gap wording on a finding whose signal_refs back Graph —
        # (the ML finding + explicit Graph request is bounded per FIX A,
        # tested in test_p0_composition_fixes_v1)
        case, cc = make_case()
        from app.models import Finding as FindingModel
        f = finding_by_title(case, "ML Pattern")
        g = FindingModel.model_validate(f.model_dump() | {
            "title": "Shared Device Relationships",
            "signal_refs": [{"signal_type": "Graph",
                             "name": "shared_device_count"}]})
        cc2 = dict(cc)
        cc2["findings"] = [g]
        r = run(cc2, g.finding_id, "Graph")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["evidence_missing"] is True
        assert "network/cluster evidence" in " ".join(r.data["next_data_needed"])

    def test_no_fabricated_graph_paths(self):
        evidence = evidence_with_real_rule_fields()
        evidence["network_evidence"] = {
            "cluster_id": 15, "cluster_name": "c", "detection_type": "x",
            "member_count": 2, "cluster_risk_score": 10.0,
            "related_accounts_count": 1, "related_accounts": ["U00011"],
            "shared_devices": [],
        }
        case, cc = make_case(evidence)
        device_finding = Finding(
            finding_id="F9", case_id="U00299", type="graph_signal",
            title="Shared Device Relationships", summary="s",
            capabilities=FindingCapability.model_validate(["signal_explain"]),
        )
        cc["findings"] = list(cc["findings"]) + [device_finding]
        r = run(cc, "F9", "Graph")
        blob = str(r.data)
        assert "path" not in blob.lower() or \
            r.data["explanation"]["relationship_paths_available"] is False
        assert r.data["explanation"].get("edges") is None
        assert r.data["explanation"].get("paths") is None


# --- 7–9. gates --------------------------------------------------------------------------

class TestGates:
    def test_unsupported_signal_for_finding_is_unsupported(self):
        case, cc = make_case()
        # finding without signal_explain capability
        poor = Finding(finding_id="F8", case_id="U00299", type="feature_observation",
                       title="Some Observation", summary="s",
                       capabilities=FindingCapability.model_validate(["timeline"]))
        cc["findings"] = list(cc["findings"]) + [poor]
        r = run(cc, "F8", "Rule")
        assert r.outcome == ToolResultOutcome.UNSUPPORTED
        assert r.error.code == "CAPABILITY_NOT_SUPPORTED"
        assert r.data is None                       # never converted to empty

    def test_unknown_finding_is_validation_error(self, ):
        case, cc = make_case()
        r = run(cc, "F999", "Rule")
        assert r.outcome == ToolResultOutcome.VALIDATION_ERROR
        assert "does not exist" in r.error.message

    def test_invalid_signal_type_is_validation_error(self):
        case, cc = make_case()
        f = finding_by_title(case, "Coordinated Trading")
        for bad in ("Shap", "rule", "", "Network"):
            r = run(cc, f.finding_id, bad)
            assert r.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_blank_finding_id_is_validation_error(self):
        case, cc = make_case()
        for bad in ("", "   "):
            r = run(cc, bad, "Rule")
            assert r.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_no_context_and_no_case_id(self):
        r = signal_explain(finding_id="F1", signal_type="ML")
        assert r.outcome == ToolResultOutcome.VALIDATION_ERROR


# --- 10–11. integration failures -----------------------------------------------------------

class TestIntegrationFailures:
    def test_rp_unavailable_is_integration_error(self):
        from app.adapters.risk_platform import RiskPlatformError
        case, cc = make_case()
        cc.pop("_raw_evidence", None)
        # hermetic: patch both touch points (see malformed-response test)
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: (_ for _ in ()).throw(
                RiskPlatformError("unavailable", "Risk Platform timed out")),
        ):
            f = finding_by_title(case, "Coordinated Trading")
            r = signal_explain(finding_id=f.finding_id, signal_type="Rule",
                               case_id="U00299")
        assert r.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert r.error.code == "RISK_PLATFORM_UNAVAILABLE"

    def test_malformed_rp_response_is_bounded_integration_error(self):
        case, cc = make_case()
        cc.pop("_raw_evidence", None)
        # hermetic: patch BOTH RP touch points (case fetch for canonical
        # finding resolution + evidence fetch for the malformed payload) so
        # the test never requires a live Risk Platform.
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: {"garbage": True},
        ):
            f = finding_by_title(case, "ML Pattern")
            r = signal_explain(finding_id=f.finding_id, signal_type="ML",
                               case_id="U00299")
        assert r.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert r.error.code == "RISK_PLATFORM_MALFORMED_RESPONSE"


# --- 12–15. grounding -----------------------------------------------------------------------

class TestGrounding:
    def test_no_fabricated_thresholds(self):
        case, cc = make_case()
        f = finding_by_title(case, "Coordinated Trading")
        r = run(cc, f.finding_id, "Rule")
        # threshold string is exactly the RP value
        assert r.data["rule"]["threshold"] == "opposite_trade_ratio > 0.4"

    def test_no_fabricated_feature_attribution(self):
        case, cc = make_case()
        f = finding_by_title(case, "ML Pattern")
        r = run(cc, f.finding_id, "ML")
        blob = str(r.data)
        assert "contribution" not in blob          # no per-feature weights invented
        assert "shap" not in blob.lower()
        assert "feature_importance" not in blob

    def test_evidence_refs_only_contain_actual_ids(self):
        case, cc = make_case()
        f = finding_by_title(case, "Coordinated Trading")
        r = run(cc, f.finding_id, "Rule")
        for ref in r.data["evidence_refs"]:
            assert ref["id"] == "U00299"           # only the real case id
            assert ref["kind"] == "risk_event"

    def test_signal_and_policy_refs_mirror_finding(self):
        case, cc = make_case()
        f = finding_by_title(case, "Coordinated Trading")
        r = run(cc, f.finding_id, "Rule")
        import json
        assert {json.dumps(s, sort_keys=True) for s in r.data["signal_refs"]} == \
               {json.dumps(s.model_dump(), sort_keys=True) for s in f.signal_refs}
        assert {json.dumps(p, sort_keys=True) for p in r.data["policy_refs"]} == \
               {json.dumps(p.model_dump(), sort_keys=True) for p in f.policy_refs}


# --- 16–18. executor + follow-up integration ----------------------------------------------------

class TestExecutorAndFollowUpIntegration:
    def test_tool_executable_through_executor(self):
        evidence = evidence_with_real_rule_fields()
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (evidence, fx.rp_explanation_payload()),
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: evidence,
        ):
            provider = default_tool_provider()
            assert provider.has("signal_explain")
            res = provider.get("signal_explain")({
                "finding_id": "F2",           # Coordinated Trading in fixture order
                "signal_type": "Rule",
                "case_id": "U00299",
            })
        assert res.outcome == ToolResultOutcome.SUCCESS
        assert res.data["rule"]["name"] == "Coordinated Trading Pattern"

    def test_executor_explain_signal_path(self):
        evidence = evidence_with_real_rule_fields()
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (evidence, fx.rp_explanation_payload()),
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: evidence,
        ):
            class FakeLLM:
                def generate(self, messages, max_tokens=0, temperature=0.1):
                    return ('{"skill_id": "timeline_investigation", "goal": "g", '
                            '"steps": [{"type": "explain_signal", "reason": "r"}]}')

            from app.models import FocusSource
            ctx = InvestigationContext(case_id="U00299", focused_finding_id="F2",
                                       focus_source=FocusSource.USER_SELECTED)
            plan = PlannerV2(FakeLLM()).plan(
                "Why was this flagged?", ctx, ["timeline_investigation"],
                FindingCapability.model_validate(
                    ["timeline", "signal_explain", "policy_lookup"]))
            task = TaskV2(task_id="T-SE", investigation_id="CASE:U00299",
                          user_request="why flagged",
                          selected_skill="timeline_investigation")
            ex = ExecutorV2(default_tool_provider())
            result = ex.execute(plan, task, ctx,
                                finding_capabilities=FindingCapability.model_validate(
                                    ["timeline", "signal_explain", "policy_lookup"]))
        assert result.status == TaskStatusV2.COMPLETED
        tc = result.tool_calls[0]
        assert tc.tool_name == "signal_explain"
        assert isinstance(tc.result, object) and tc.result.outcome == ToolResultOutcome.SUCCESS
        # normalized ToolResult, structured payload
        assert tc.result.data["rule"]["name"] == "Coordinated Trading Pattern"

    def test_explain_finding_followup_resolves_to_implemented_path(self):
        from app.followups import EVENT_TEMPLATES, FINDING_TEMPLATES
        from app.skills import SKILLS, STEP_TOOL_MAP
        from app.executor_v2 import default_tool_provider
        provider = default_tool_provider()
        explain = next(t for t in FINDING_TEMPLATES
                       if t.follow_up_id == "explain_finding")
        skill = SKILLS[explain.target_skill]
        assert explain.target_step in skill.planning_steps
        binding = STEP_TOOL_MAP[explain.target_step]
        assert provider.has(binding.tool_name), (
            "explain_finding must resolve to an implemented tool now"
        )

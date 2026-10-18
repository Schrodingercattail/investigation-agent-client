"""Rule explanation & case-judgment deliverable (B-chain, generic).

A rule-type investigation question ("why is this metric a risk item?")
must be answerable from the platform's OWN authoritative evidence:
the monitored-category rule and its threshold, the case's actual
observation, and the platform's stated trigger status — with the system
never reconstructing, inferring, or inventing any of them.

Locked invariants (all fixtures use SYNTHETIC ids and a synthetic 30%
threshold — no real case id, no real finding id, no real threshold
appears anywhere in production logic):
1. Data path: the platform's threshold-based feature reaches its finding
   as a Feature signal ref for BOTH platform naming forms (the
   below-threshold contextual observation AND the rule-triggered form);
   unrelated findings gain nothing.
2. The feature-explanation payload echoes the platform's authoritative
   factor entry and finding statement VERBATIM; nothing when absent.
3. Rendering distinguishes the deliverable dimensions: observed values +
   the platform's own rule/threshold/trigger-state statement; the
   triggered form (Rule branch) and the not-triggered form produce
   DIFFERENT conclusions; unknown status never renders as not-triggered.
4. Without rule/policy semantics in the evidence, no risk rationale is
   invented — only the boundary statement.
5. Artifact: an UNSUPPORTED attempt renders as attempted-but-unavailable
   with full traceability; NO_MATCH and INSUFFICIENT_DATA render
   different, status-faithful wording.
"""

import inspect

import pytest

from app.domain_tools import risk_case_fetch
from app.domain_tools.artifact_bundle import artifact_bundle
from app.domain_tools.signal_explain import signal_explain
from app.models import (
    ToolCallStatusV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.skills import EvidenceType
from tests.test_risk_platform_adapter_v2 import (
    rp_evidence_payload,
    rp_explanation_payload,
)

CASE = "U0TEST"
CTX_THRESHOLD = 30      # synthetic threshold percent — RP owns real ones


def _evidence(*, ratio, with_rule, factor_desc=None, finding_statement=None,
              rule_name="Coordinated Trading Pattern"):
    evidence = rp_evidence_payload(case_id=CASE)
    evidence["feature_evidence"]["opposite_trade_ratio"] = ratio
    evidence["risk_factor_evidence"] = []
    if factor_desc is not None:
        evidence["risk_factor_evidence"] = [{
            "factor_id": 900001,
            "factor_name": ("Coordinated Trading Pattern" if with_rule
                            else "Opposite Trade Ratio"),
            "factor_value": ratio,
            "severity": "medium",
            "factor_description": factor_desc,
        }]
    rules = list(evidence.get("rule_evidence") or [])
    if with_rule:
        rules = [r for r in rules if "coordinated" not in
                 (r.get("rule_name") or "").lower()]
        rules.append({
            "rule_name": rule_name,
            "severity": "HIGH",
            "description": (f"An opposite-trade ratio of {ratio:.2%} "
                            f"exceeded the {CTX_THRESHOLD}% threshold, "
                            "triggering the coordinated trading rule."),
            "trigger": {"opposite_trade_ratio": round(ratio, 4)},
            "threshold": f"opposite_trade_ratio > 0.{CTX_THRESHOLD[:1]}"
                         if False else
                         f"opposite_trade_ratio > {CTX_THRESHOLD / 100:.2f}",
            "contribution": 35,
        })
    else:
        rules = [r for r in rules if "coordinated" not in
                 (r.get("rule_name") or "").lower()]
    evidence["rule_evidence"] = rules
    return evidence


def _explanation(contextual_name="Opposite Trade Ratio",
                 statement=None):
    payload = rp_explanation_payload()
    statement = statement or (
        f"An opposite-trade ratio of 34.00% was observed, which is below "
        f"the {CTX_THRESHOLD}% threshold for the coordinated trading "
        "rule; this rule was not triggered.")
    payload["key_findings"] = [
        "1. ML Pattern Detection — 96.24/100; a system signal",
        f"2. {contextual_name}\n{statement}",
        "3. High Withdrawal Frequency [2]",
    ]
    return payload


def _make_case(evidence, explanation):
    from unittest.mock import patch
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (evidence, explanation),
    ):
        result = risk_case_fetch(CASE)
    cc = dict(result.data)
    cc["_raw_evidence"] = evidence
    return result, cc


def _not_triggered_case():
    """Below-threshold contextual observation: the rule did NOT fire —
    the platform states so in its own finding text."""
    desc = (f"An opposite-trade ratio of 34.00% was observed, which is "
            f"below the {CTX_THRESHOLD}% threshold for the coordinated "
            "trading rule.")
    statement = (desc + "; this rule was not triggered.")
    ev = _evidence(ratio=0.34, with_rule=False, factor_desc=desc)
    return _make_case(ev, _explanation(statement=statement))


def _triggered_case():
    """Above-threshold: RP emits the rule-triggered finding name and the
    rule in rule_evidence."""
    desc = (f"An opposite-trade ratio of 55.00% exceeded the "
            f"{CTX_THRESHOLD}% threshold, triggering the coordinated "
            "trading rule.")
    ev = _evidence(ratio=0.55, with_rule=True, factor_desc=desc)
    return _make_case(ev, _explanation(
        contextual_name="Coordinated Trading Pattern", statement=desc))


def _finding(case, needle):
    return next(f for f in case.data["findings"] if needle in f.title)


def _output():
    from app.goal_resolution import RequiredOutput, ScopeLevel
    return RequiredOutput(
        output_id="O1", claim="why flagged", claim_kind="explanation",
        subject_ref="F1", scope="finding_level",
        evidence_requirements=[EvidenceType.RULE_EXPLANATION])


class TestDataPath:
    def test_contextual_form_gets_feature_ref_and_explain_capability(self):
        case, _ = _not_triggered_case()
        f = _finding(case, "Opposite Trade Ratio")
        assert any(r.signal_type == "Feature"
                   and r.name == "opposite_trade_ratio"
                   for r in f.signal_refs)
        assert f.capabilities.supports("signal_explain")

    def test_triggered_form_gets_feature_ref_too(self):
        case, _ = _triggered_case()
        f = _finding(case, "Coordinated Trading Pattern")
        assert any(r.signal_type == "Feature"
                   and r.name == "opposite_trade_ratio"
                   for r in f.signal_refs)
        assert f.capabilities.supports("signal_explain")

    def test_unrelated_findings_gain_nothing(self):
        case, _ = _not_triggered_case()
        ml = _finding(case, "ML Pattern")
        assert not any(r.name == "opposite_trade_ratio"
                       for r in ml.signal_refs)


class TestFeatureExplanationPayload:
    def test_not_triggered_payload_echoes_authoritative_semantics(self):
        case, cc = _not_triggered_case()
        r = signal_explain(finding_id=_finding(
            case, "Opposite Trade Ratio").finding_id,
            signal_type=None, case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        ex = r.data["explanation"]
        assert ex["risk_factor"]["severity"] == "medium"
        # verbatim platform wording: threshold + rule + status semantics
        assert f"below the {CTX_THRESHOLD}% threshold" \
            in ex["risk_factor"]["description"]
        assert "this rule was not triggered" in ex["finding_summary"]
        assert r.data["evidence_missing"] is False

    def test_no_factor_entry_means_no_factor_payload(self):
        case, cc = _not_triggered_case()
        cc["_raw_evidence"] = {
            **cc["_raw_evidence"], "risk_factor_evidence": []}
        r = signal_explain(finding_id=_finding(
            case, "Opposite Trade Ratio").finding_id,
            signal_type=None, case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert "risk_factor" not in r.data["explanation"]

    def test_triggered_form_takes_rule_branch(self):
        case, cc = _triggered_case()
        r = signal_explain(finding_id=_finding(
            case, "Coordinated Trading Pattern").finding_id,
            signal_type=None, case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["signal_type"] == "Rule"
        assert r.data["rule"]["threshold"] == \
            f"opposite_trade_ratio > {CTX_THRESHOLD / 100:.2f}"
        assert r.data["rule"]["trigger_values"] == \
            {"opposite_trade_ratio": 0.55}


class TestCaseJudgmentRendering:
    def test_not_triggered_renders_platform_statement_verbatim(self):
        from app.agent_loop import _gsp_render_output_facts
        case, cc = _not_triggered_case()
        r = signal_explain(finding_id=_finding(
            case, "Opposite Trade Ratio").finding_id,
            signal_type=None, case_context=cc)
        summary = {
            "feature_facts": r.data["explanation"]["features"],
            "risk_factor": r.data["explanation"]["risk_factor"],
            "finding_summary": r.data["explanation"]["finding_summary"],
        }
        lines = "\n".join(
            _gsp_render_output_facts(_output(), summary))
        assert "this rule was not triggered" in lines     # case verdict
        assert f"below the {CTX_THRESHOLD}% threshold" in lines
        assert "观测记录的存在不等于规则已触发" in lines   # category ≠ case
        assert "已触发" not in lines.replace(
            "不等于规则已触发", "").replace("不构成风险判定", "")

    def test_triggered_renders_rule_hit(self):
        case, cc = _triggered_case()
        r = signal_explain(finding_id=_finding(
            case, "Coordinated Trading Pattern").finding_id,
            signal_type=None, case_context=cc)
        summary = {
            "rule_name": r.data["rule"]["name"],
            "threshold": r.data["rule"]["threshold"],
            "trigger_values": r.data["rule"]["trigger_values"],
            "rule_description": r.data["rule"]["description"],
        }
        from app.agent_loop import _gsp_render_output_facts
        lines = "\n".join(
            _gsp_render_output_facts(_output(), summary))
        assert "命中规则" in lines
        assert f"opposite_trade_ratio > {CTX_THRESHOLD / 100:.2f}" in lines

    def test_unknown_status_never_renders_as_not_triggered(self):
        from app.agent_loop import _gsp_render_output_facts
        # a feature explanation with NO platform rule/threshold semantics
        summary = {
            "feature_facts": [{"name": "trade_frequency_24h", "value": 85}],
        }
        lines = "\n".join(
            _gsp_render_output_facts(_output(), summary))
        assert "未触发" not in lines
        assert "未达到" not in lines
        assert "不推断" in lines            # the boundary stays explicit
        # and no threshold number is invented
        assert f"{CTX_THRESHOLD}%" not in lines

    def test_no_rule_semantics_invents_no_rationale(self):
        from app.agent_loop import _gsp_render_output_facts
        summary = {
            "feature_facts": [{"name": "trade_frequency_24h", "value": 85}],
            "risk_factor": {"name": "Trading Frequency", "value": 85.0,
                            "severity": "informational",
                            "description": "85 trades in 24h period"},
            "finding_summary": "85 trades were recorded in a 24-hour "
                               "period.",
        }
        lines = "\n".join(
            _gsp_render_output_facts(_output(), summary))
        # only what the platform states + the boundary — no risk rationale
        assert "85 trades were recorded" in lines
        assert "阈值" not in lines.replace("不推断阈值", "")
        assert "欺诈" in lines              # the honest boundary sentence


class TestArtifactUnsupportedTraceability:
    def _bundle(self, results):
        from app.models import ToolCallV2
        calls = []
        for i, res in enumerate(results, 1):
            calls.append(ToolCallV2(
                tool_call_id=f"TC-S{i}", investigation_id="INV",
                task_id="T", tool_name=res[0], arguments={},
                status=ToolCallStatusV2.SUCCESS,
                started_at="now", completed_at="now",
                result=res[1]))
        result = artifact_bundle(
            scope="case", case_id=CASE, task_id="T",
            source_tool_calls=calls)
        assert result.outcome == ToolResultOutcome.SUCCESS
        artifact = (result.data or {}).get("artifact") or {}
        return (artifact.get("content")
                or artifact.get("body") or ""), artifact

    def test_unsupported_renders_as_attempted_unavailable(self):
        unsupported = ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(code="CAPABILITY_NOT_SUPPORTED",
                            message="Finding does not support signal "
                                    "explanation."))
        content, artifact = self._bundle([
            ("signal_explain", unsupported)])
        assert "## Attempted but Unavailable" in content
        assert "TC-S1 (signal_explain): attempted but not supported" \
            in content
        assert "CAPABILITY_NOT_SUPPORTED" in content
        # traceability: the attempt IS listed as a source call
        assert "TC-S1" in content.split("## Source Tool Calls")[1]
        # never disguised as an explanation
        assert "## Signal Explanation" not in content

    def test_feature_explanation_renders_in_bundle(self):
        ok = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "finding_id": "F1",
            "signal_type": "Feature",
            "explanation": {
                "features": [{"name": "opposite_trade_ratio",
                              "value": 0.34}],
                "risk_factor": {"name": "Opposite Trade Ratio",
                                "value": 0.34, "severity": "medium",
                                "description": (
                                    "An opposite-trade ratio of 34.00% "
                                    f"was observed, which is below the "
                                    f"{CTX_THRESHOLD}% threshold for the "
                                    "coordinated trading rule.")},
                "finding_summary": (
                    "An opposite-trade ratio of 34.00% was observed; "
                    "this rule was not triggered."),
            },
            "evidence_missing": False,
        })
        content, artifact = self._bundle([("signal_explain", ok)])
        assert "## Signal Explanation" in content
        assert "Feature observation: opposite_trade_ratio=0.34" in content
        assert "this rule was not triggered" in content
        # the attempt is a real contributor
        assert "TC-S1" in content.split("## Source Tool Calls")[1]

    def test_no_unavailable_section_without_unsupported(self):
        ok = ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "analysis": "cross_account_trade_analysis",
            "analysis_status": "NO_MATCH",
            "config": {"max_time_delta_seconds": 30,
                       "value_tolerance_relative": 0.05,
                       "value_tolerance_absolute_floor": 1.0},
            "retrieval": {"truncated": False},
        })
        content, artifact = self._bundle([("cross_account_trade_analysis",
                                           ok)])
        assert "Attempted but Unavailable" not in content


class TestNoMatchVsInsufficientWording:
    def _content(self, status):
        from app.domain_tools.artifact_bundle import \
            _render_cross_account_candidates
        payload = {
            "analysis_status": status,
            "config": {"max_time_delta_seconds": 30,
                       "value_tolerance_relative": 0.05,
                       "value_tolerance_absolute_floor": 1.0},
            "retrieval": {"truncated": False},
        }
        return "\n".join(_render_cross_account_candidates(payload))

    def test_no_match_is_an_authoritative_zero(self):
        content = self._content("NO_MATCH")
        assert "completely retrieved" in content
        assert "authoritative zero" in content
        assert "insufficient" not in content

    def test_insufficient_data_never_claims_a_zero(self):
        content = self._content("INSUFFICIENT_DATA")
        assert "insufficient for a reliable conclusion" in content
        assert "not a no-match result" in content
        assert "authoritative zero" not in content


class TestNoCaseSpecificBranches:
    import app.adapters.risk_platform as _adapter
    import app.domain_tools.artifact_bundle as _bundle
    import app.domain_tools.signal_explain as _se

    def test_no_case_ids_in_production_modules(self):
        import app.agent_loop
        for mod in (self._adapter, self._bundle, self._se, app.agent_loop):
            src = inspect.getsource(mod)
            for case_id in ("U00005", "U00010"):
                assert case_id not in src, \
                    f"{mod.__name__} contains case-specific id {case_id}"

    def test_no_threshold_literal_in_new_paths(self):
        se_src = inspect.getsource(self._se)
        assert "0.4" not in se_src and "40%" not in se_src
        fn_src = inspect.getsource(self._adapter._finding_uses_signal)
        assert "0.4" not in fn_src and "40%" not in fn_src
        mod_src = inspect.getsource(self._adapter)
        tbl_block = mod_src.split("_FEATURE_TO_SIGNAL")[1][:600]
        assert "0.4" not in tbl_block and "40%" not in tbl_block

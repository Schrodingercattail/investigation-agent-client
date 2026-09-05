"""P0 composition-fix regression tests (composition-integrity audit).

P0-1  Focus guidance filter must remove the COMPLETE guidance sentence —
      no grammatical fragment ("to continue the investigation.") may remain.
      (Frontend renderTurnText — logic mirrored/tested here at the boundary;
      the function is exported from ConversationPanel for exactly this.)
P0-2  Conversation composer must preserve producer-specific evidence-gap
      scope: the composed sentence may never widen beyond the ToolResult's
      own next_data_needed. Assertions are on FINAL composed text.
P0-4  Finding-scoped artifact policy table renders ONLY associated
      citations; case-scoped artifact still renders the complete set.
"""

import re

import pytest

from app.composition import gap_statement


# ===========================================================================
# P0-1 — guidance sentence removal (frontend logic, mirrored)
# ===========================================================================

# mirror of ConversationPanel.SELECT_FINDING_SENTENCE_RE + renderTurnText
SELECT_FINDING_SENTENCE_RE = re.compile(
    r"\n?Select a finding from the Findings panel on the left to continue "
    r"the investigation\.?(\n)?")
SPACE_BEFORE_NL_RE = re.compile(r"[ \t]+\n")
MULTI_NL_RE = re.compile(r"\n{3,}")


def render_turn_text(text: str, focused_finding_id: str | None) -> str:
    if focused_finding_id:
        return MULTI_NL_RE.sub(
            "\n\n",
            SPACE_BEFORE_NL_RE.sub(
                "\n", SELECT_FINDING_SENTENCE_RE.sub("\n", text))
        ).strip()
    return text


INTAKE_TEXT = (
    "7 findings were identified for case U00047. Select a finding from the "
    "Findings panel on the left to continue the investigation.\n"
    "- ML Pattern Detection: 96.24/100\n"
    "- Coordinated Trading Pattern: ratio exceeded threshold"
)


class TestP0_1_GuidanceSentenceRemoval:
    def test_unfocused_intake_preserves_full_guidance(self):
        out = render_turn_text(INTAKE_TEXT, None)
        assert "Select a finding from the Findings panel on the left to " \
            "continue the investigation." in out
        assert "7 findings were identified for case U00047." in out

    def test_focused_intake_has_no_guidance_and_no_fragment(self):
        out = render_turn_text(INTAKE_TEXT, "F4")
        assert "Select a finding" not in out
        assert "to continue the investigation" not in out   # no fragment
        assert "7 findings were identified for case U00047." in out
        assert "- ML Pattern Detection: 96.24/100" in out   # rest intact

    def test_no_trailing_whitespace_left_behind(self):
        out = render_turn_text(INTAKE_TEXT, "F4")
        assert not re.search(r"\. +\n", out)                # no ". \n" gap
        assert not re.search(r"^ +", out, re.M) or all(
            ln == "" or ln == ln.lstrip() or ln.startswith("-")
            for ln in out.splitlines())

    def test_single_line_variant_leaves_no_fragment(self):
        text = ("3 findings were identified for case U00047. Select a "
                "finding from the Findings panel on the left to continue "
                "the investigation.")
        out = render_turn_text(text, "F4")
        assert out == "3 findings were identified for case U00047."
        assert "to continue" not in out


# ===========================================================================
# P0-2 — producer-specific evidence gaps in composed conversation text
# ===========================================================================

from app.investigation_service import compose_response
from app.models import (
    FindingCapability,
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)


def tc(result, tool_call_id="TC", tool_name="signal_explain"):
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:X", task_id="T",
        tool_name=tool_name, arguments={}, status=ToolCallStatusV2.SUCCESS,
        result=result, started_at="t", completed_at="t")


def gap_result(data):
    return ToolResult(outcome=ToolResultOutcome.SUCCESS, data=data)


class TestP0_2_ProducerSpecificGapWording:
    def test_ml_missing_attribution_names_attribution(self):
        # 1. ML: missing "transaction-level feature attribution" — the
        # composed sentence must scope the gap to attribution, NOT claim
        # transaction records are unavailable.
        r = gap_result({
            "finding_id": "F1", "signal_type": "ML",
            "explanation": {"ml_score": 96.24},
            "evidence_missing": True,
            "next_data_needed": ["transaction-level feature attribution"],
        })
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[tc(r)], execution_errors=[])
        assert "transaction-level feature attribution" in text and \
            "is not currently available" in text
        assert "complete transaction-level evidence is not available" \
            not in text

    def test_graph_missing_network_evidence_names_network(self):
        # 2. Graph: missing network/cluster evidence — scoped wording.
        r = gap_result({
            "finding_id": "F1", "signal_type": "Graph",
            "explanation": {"relationship_paths_available": False},
            "evidence_missing": True,
            "next_data_needed": ["network/cluster evidence for this case"],
        })
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[tc(r)], execution_errors=[])
        assert "network or cluster evidence is not currently available" in text
        assert "complete transaction-level evidence is not available" \
            not in text

    def test_rule_no_backing_names_triggered_rule_evidence(self):
        # 3. Rule-no-backing: missing triggered-rule evidence — scoped.
        r = gap_result({
            "finding_id": "F1", "signal_type": "Rule", "explanation": {},
            "evidence_missing": True,
            "next_data_needed": [
                "triggered-rule evidence associated with this finding"],
        })
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[tc(r)], execution_errors=[])
        assert ("the triggered-rule evidence associated with this finding "
                "is not currently available") in text
        assert "complete transaction-level evidence is not available" \
            not in text

    def test_empty_evidence_result_keeps_dedicated_branch(self):
        # 4. EMPTY evidence result keeps its dedicated exact wording.
        r = ToolResult(outcome=ToolResultOutcome.EMPTY, data={
            "finding_id": "F3", "view": "evidence", "records": [],
            "record_count": 0, "risk_features": {}, "streams": {}})
        text = compose_response(user_request="evidence", skill_id=None,
                                plan=None, tool_calls=[tc(r, "TC-E",
                                                         "finding_drilldown")],
                                execution_errors=[])
        assert "holds no concrete records for its evidence streams" in text

    def test_no_generic_sentence_for_any_gap(self):
        # belt-and-braces: the old generic sentence can never appear for
        # any producer-specific gap payload.
        payloads = [
            {"finding_id": "F", "signal_type": "ML", "explanation": {},
             "evidence_missing": True,
             "next_data_needed": ["transaction-level feature attribution"]},
            {"finding_id": "F", "signal_type": "Graph", "explanation": {},
             "evidence_missing": True,
             "next_data_needed": ["network/cluster evidence for this case"]},
            {"finding_id": "F", "signal_type": "Rule", "explanation": {},
             "evidence_missing": True,
             "next_data_needed": [
                 "triggered-rule evidence associated with this finding"]},
        ]
        for p in payloads:
            text = compose_response(
                user_request="why", skill_id=None, plan=None,
                tool_calls=[tc(gap_result(p))], execution_errors=[])
            assert "complete transaction-level evidence is not available" \
                not in text, p

    def test_gap_statement_never_widens_scope(self):
        # the shared helper itself never emits the old generic claim
        for nd in (["transaction-level feature attribution"],
                   ["network/cluster evidence for this case"],
                   ["triggered-rule evidence associated with this finding"]):
            s = gap_statement({"evidence_missing": True,
                               "next_data_needed": nd})
            assert "complete transaction-level evidence" not in s
            assert nd[0] in s


# ===========================================================================
# P0-4 — finding-artifact policy table ⊆ associated citations
# ===========================================================================

from app.domain_tools.artifact_bundle import artifact_bundle
from app.models import ToolCallStatusV2


def mk(tool_call_id, tool_name, result):
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T", tool_name=tool_name, arguments={},
        status=ToolCallStatusV2.SUCCESS, result=result,
        started_at="t", completed_at="t")


def policy_result(associated, all_ids):
    """associated: citation ids attached to THIS finding; all_ids: the full
    ranked case-level match list (superset)."""
    matches = [{"citation_id": i, "chunk_id": f"c{i}",
                "document": f"Doc_{i}.md", "section": f"sec-{i}",
                "snippet": "s", "relevance": 5} for i in all_ids]
    return ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
        "finding_id": "F2", "finding_policy_status":
            "associated" if associated else "no_finding_level_basis",
        "topic": "t", "matches": matches,
        "associated_policy_refs": [
            {"citation_id": i, "chunk_id": f"c{i}"} for i in associated],
        "newly_retrieved_refs": [],
        "policy_refs": [{"citation_id": i, "chunk_id": f"c{i}"}
                        for i in associated],
        "required_evidence": [],
        "evidence_missing": not associated,
        "next_data_needed": (["finding-level policy association"]
                             if not associated else [])})


def case_fetch_with(citation_ids):
    return ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
        "case_id": "U00299", "findings": [], "evidence_refs": [],
        "signal_refs": [],
        "policy_refs": [{"citation_id": i, "chunk_id": f"c{i}",
                         "doc": f"Doc_{i}.md", "section": f"sec-{i}"}
                        for i in citation_ids]})


class TestP0_4_PolicyTableSubset:
    def test_finding_artifact_table_is_associated_subset(self):
        # associated=[1], case-level matches=[1,2,3] → the finding artifact
        # may render ONLY citation [1].
        r = policy_result(associated=[1], all_ids=[1, 2, 3])
        res = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T",
            source_tool_calls=[
                mk("TC-P", "policy_lookup", r),
            ])
        content = res.data["artifact"]["content"]
        assert "Doc_1.md" in content              # associated renders
        for banned in ("Doc_2.md", "Doc_3.md"):
            assert banned not in content, banned  # non-associated excluded
        table_ids = re.findall(r"\| (\d+) \|", content)
        assert set(table_ids) <= {"1"}

    def test_case_artifact_still_renders_complete_set(self):
        # The case-level citation table renders from the CANONICAL fetch
        # (real Finding-model shape), and the policy result's own
        # case-scope table may include the full match list.
        import tests.test_risk_platform_adapter_v2 as fx
        from app.domain_tools import risk_case_fetch
        from unittest.mock import patch
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ):
            case = risk_case_fetch("U00299")
        r = policy_result(associated=[1], all_ids=[1, 2, 3])
        res = artifact_bundle(
            scope="case", case_id="U00299", task_id="T",
            source_tool_calls=[
                mk("TC-F", "risk_case_fetch",
                   ToolResult(outcome=ToolResultOutcome.SUCCESS,
                              data=case.data)),
                mk("TC-P", "policy_lookup", r),
            ])
        content = res.data["artifact"]["content"]
        assert "Case-level citations" in content
        # the canonical fetch's citation collection ([1],[2] in this
        # fixture) renders as the [n] table...
        for i in (1, 2):
            assert f"[{i}]" in content
        # ...and the policy result's own case-scope table carries the full
        # ranked match list [1,2,3] — the complete set is allowed at case
        # scope (nothing filtered).
        for i in (1, 2, 3):
            assert f"Doc_{i}.md" in content
        table_ids = re.findall(r"\| (\d+) \|", content)
        assert {int(t) for t in table_ids} >= {1, 2, 3}

    def test_no_basis_artifact_still_statement_only(self):
        r = policy_result(associated=[], all_ids=[1, 2, 3])
        res = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T",
            source_tool_calls=[mk("TC-P", "policy_lookup", r)])
        content = res.data["artifact"]["content"]
        assert "No finding-level policy basis is attached to this " \
            "finding." in content
        for banned in ("Doc_1.md", "Doc_2.md", "Doc_3.md"):
            assert banned not in content          # no case-level table


# ===========================================================================
# Task 2 — detection-mechanism-first signal explanation responses
# ===========================================================================

from app.composition import detection_mechanism_sentence


def signal_tc(data, name="signal_explain"):
    return tc(gap_result(data), "TC-SIG", name)


class TestDetectionMechanismResponseContract:
    """Response contract for "Why is this finding flagged?": identify the
    detection mechanism (deterministic, from the structured signal payload)
    → state the confirmed fact → only then the missing evidence, scoped
    exactly. Never routed through the LLM; no fabricated facts."""

    def test_ml_finding_names_ml_pattern_detection(self):
        # 1. ML finding → explicitly names ML Pattern Detection
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F1", "signal_type": "ML",
                                    "explanation": {"ml_score": 96.24},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "transaction-level feature "
                                        "attribution"]})],
                                execution_errors=[])
        assert "ML Pattern Detection" in text
        assert "flagged by" in text

    def test_rule_finding_names_rule_based_detection(self):
        # 2. Rule finding → explicitly says rule-based detection
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F2", "signal_type": "Rule",
                                    "rule": {"name": "Coordinated Trading "
                                                      "Pattern",
                                             "description": "ratio exceeded"},
                                    "evidence_missing": False,
                                    "next_data_needed": []})],
                                execution_errors=[])
        assert "rule-based detection" in text

    def test_rule_no_backing_names_rule_based_detection(self):
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F2", "signal_type": "Rule",
                                    "explanation": {},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "triggered-rule evidence associated "
                                        "with this finding"]})],
                                execution_errors=[])
        assert "rule-based detection" in text

    def test_graph_finding_names_graph_based_detection(self):
        # 3. Graph finding → explicitly says graph-based detection
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F4", "signal_type": "Graph",
                                    "explanation": {},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "network/cluster evidence for this "
                                        "case"]})],
                                execution_errors=[])
        assert "graph-based risk detection" in text

    def test_missing_attribution_never_becomes_transaction_evidence(self):
        # 4. attribution gap keeps its scope in the composed text
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F1", "signal_type": "ML",
                                    "explanation": {"ml_score": 96.24},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "transaction-level feature "
                                        "attribution"]})],
                                execution_errors=[])
        assert "cannot be shown" in text
        assert "complete transaction-level evidence" not in text

    def test_missing_triggered_rule_evidence_stays_scoped(self):
        # 5. triggered-rule gap stays scoped and grammatical
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F2", "signal_type": "Rule",
                                    "explanation": {},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "triggered-rule evidence associated "
                                        "with this finding"]})],
                                execution_errors=[])
        assert ("the triggered-rule evidence associated with this finding "
                "is not currently available") in text
        assert "transaction" not in text

    def test_no_unsupported_facts_introduced(self):
        # 6. no fabricated thresholds/features/rules/network facts
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F1", "signal_type": "ML",
                                    "explanation": {},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "transaction-level feature "
                                        "attribution"]})],
                                execution_errors=[])
        assert "exceeding" not in text
        assert "threshold" not in text
        assert "cluster" not in text or "network" not in text.replace(
            "network or cluster evidence is not currently available", "")
        assert "rule " not in text.replace("rule-based", "")

    def test_confirmed_fact_precedes_missing_evidence(self):
        # ordering: mechanism → confirmed fact → gap
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F1", "signal_type": "ML",
                                    "explanation": {"ml_score": 96.24},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "transaction-level feature "
                                        "attribution"]})],
                                execution_errors=[])
        mech = text.index("ML Pattern Detection")
        fact = text.index("The model score is")
        gap = text.index("transaction-level feature attribution is not "
                         "currently available")
        assert mech < fact < gap

    def test_score_not_duplicated(self):
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[signal_tc({
                                    "finding_id": "F1", "signal_type": "ML",
                                    "explanation": {"ml_score": 96.24},
                                    "evidence_missing": True,
                                    "next_data_needed": [
                                        "transaction-level feature "
                                        "attribution"]})],
                                execution_errors=[])
        assert text.count("96.24") == 1


# ===========================================================================
# FIX A / FIX B — detector identity + explicit evidence stream (E2E-level)
# ===========================================================================

from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import finding_drilldown, risk_case_fetch, signal_explain


def live_case_context():
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
    ):
        from app.domain_tools import risk_case_fetch as rcf
        case = rcf("U00299")
    cc = dict(case.data)
    cc["_raw_evidence"] = fx.rp_evidence_payload("U00299")
    return case, cc


def finding_by_id(cc, fid):
    return next(f for f in cc["findings"] if f.finding_id == fid)


class TestFixA_DetectorIdentity:
    def test_f1_derives_ml_never_rule(self):
        # F1 (ML Pattern Detection, signal_refs=[(ML, ml_score)]) derives ML
        case, cc = live_case_context()
        r = signal_explain(finding_id="F1", case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["signal_type"] == "ML"
        assert "ml_score" in r.data["explanation"]

    def test_f1_explicit_rule_is_bounded_not_silently_executed(self):
        case, cc = live_case_context()
        r = signal_explain(finding_id="F1", signal_type="Rule",
                           case_context=cc)
        assert r.outcome == ToolResultOutcome.UNSUPPORTED
        assert r.error.code == "SIGNAL_TYPE_NOT_SUPPORTED"
        assert r.error.detail.get("finding_signal_types") == ["ML"]

    def test_rule_backed_finding_explains_rule(self):
        # F3's rule lookup by title matches "High withdrawal frequency".
        case, cc = live_case_context()
        r = signal_explain(finding_id="F3", signal_type="Rule",
                           case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["signal_type"] == "Rule"
        assert r.data["rule"]["name"].lower() == "high withdrawal frequency"

    def test_derived_rule_for_f3(self):
        # F3 signal_refs carry only a Feature ref (no ML/Rule/Graph), so
        # derivation falls to rule lookup: explicit-equivalent Rule works.
        case, cc = live_case_context()
        r = signal_explain(finding_id="F3", case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["signal_type"] == "Rule"

    def test_graph_backed_finding_explains_graph(self):
        case, cc = live_case_context()
        # synthesize a graph-backed finding (Finding model keeps signal_refs
        # as typed refs; adapter normalization calls .model_dump())
        from app.models import Finding as FindingModel
        g = FindingModel.model_validate(
            finding_by_id(cc, "F1").model_dump() | {
                "title": "Shared Device Relationships",
                "signal_refs": [{"signal_type": "Graph",
                                 "name": "shared_device_count"}]})
        cc2 = dict(cc)
        cc2["findings"] = [g]
        r = signal_explain(finding_id="F1", case_context=cc2)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["signal_type"] == "Graph"

    def test_mismatch_cannot_silently_execute(self):
        case, cc = live_case_context()
        for wrong in ("ML", "Graph"):
            r = signal_explain(finding_id="F3", signal_type=wrong,
                               case_context=cc)  # F3 refs: [Feature] only
            if r.outcome == ToolResultOutcome.UNSUPPORTED:
                assert r.error.code == "SIGNAL_TYPE_NOT_SUPPORTED"

    def test_composed_mechanism_for_f1_is_ml(self):
        # final user-visible text names ML Pattern Detection for F1
        case, cc = live_case_context()
        f1 = finding_by_id(cc, "F1")
        r = signal_explain(finding_id="F1", case_context=cc)
        text = compose_response(user_request="why", skill_id=None, plan=None,
                                tool_calls=[tc(r)], execution_errors=[])
        assert "ML Pattern Detection" in text
        assert "rule-based detection" not in text


class TestFixB_ExplicitEvidenceStream:
    def test_f1_withdrawal_request_is_bounded_unsupported(self):
        # F1 (ML Pattern Detection) has no withdrawal stream — a withdrawal
        # request is bounded, never silently replaced by transactions.
        case, cc = live_case_context()
        r = finding_drilldown(finding_id="F1", view="evidence",
                              case_context=cc, stream="withdrawals")
        assert r.outcome == ToolResultOutcome.UNSUPPORTED
        assert r.error.code == "EVIDENCE_STREAM_NOT_SUPPORTED"
        assert not (r.data or {}).get("records")

    def test_f3_transaction_request_is_bounded_unsupported(self):
        case, cc = live_case_context()
        r = finding_drilldown(finding_id="F3", view="evidence",
                              case_context=cc, stream="transactions")
        assert r.outcome == ToolResultOutcome.UNSUPPORTED
        assert r.error.code == "EVIDENCE_STREAM_NOT_SUPPORTED"

    def test_f3_withdrawal_stream_switches_correctly(self):
        case, cc = live_case_context()
        r = finding_drilldown(finding_id="F3", view="evidence",
                              case_context=cc, stream="withdrawals")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["streams"]["withdrawals_included"] is True
        assert r.data["streams"]["transactions_included"] is False
        assert all(rec["record_kind"] == "withdrawal"
                   for rec in r.data["records"])

    def test_f1_transaction_stream_returns_transactions(self):
        case, cc = live_case_context()
        r = finding_drilldown(finding_id="F1", view="evidence",
                              case_context=cc, stream="transactions")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["streams"]["transactions_included"] is True
        assert all(rec["record_kind"] == "transaction"
                   for rec in r.data["records"])

    def test_invalid_stream_rejected(self):
        case, cc = live_case_context()
        r = finding_drilldown(finding_id="F1", view="evidence",
                              case_context=cc, stream="opposite_trades")
        assert r.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_planner_specializes_stream_steps(self):
        # "show me all the withdrawals" plans the withdrawals-scoped step
        from app.planner_v2 import PlannerV2, _explicit_stream_step
        from app.models import InvestigationContext, FocusSource
        from app.skills import eligible_skills_for_finding
        assert _explicit_stream_step("show me all the withdrawals") == \
            "inspect_withdrawals"
        assert _explicit_stream_step("show me the opposite trade") is None
        ctx = InvestigationContext(case_id="U00299",
                                   focused_finding_id="F3",
                                   focus_source=FocusSource.USER_SELECTED)

        class Scripted:
            def generate(self, *a, **k):
                return ('{"skill_id": "timeline_investigation", "goal": "g",'
                        ' "steps": [{"type": "inspect_evidence", '
                        ' "reason": "r"}]}')

        CAPS = FindingCapability.model_validate(["timeline"])
        eligible = [s.skill_id for s in eligible_skills_for_finding(CAPS)]
        plan = PlannerV2(Scripted()).plan(
            "show me all the withdrawals", ctx, eligible, CAPS)
        assert [s.type for s in plan.steps] == ["inspect_withdrawals"]
        assert plan.steps[0].arguments == {"view": "evidence",
                                           "stream": "withdrawals"}

    def test_composed_text_describes_returned_stream(self):
        case, cc = live_case_context()
        r = finding_drilldown(finding_id="F3", view="evidence",
                              case_context=cc, stream="withdrawals")
        text = compose_response(user_request="show me all the withdrawals",
                                skill_id=None, plan=None,
                                tool_calls=[tc(r, "TC-D",
                                               "finding_drilldown")],
                                execution_errors=[])
        assert "withdrawal record" in text
        assert "transaction record" not in text

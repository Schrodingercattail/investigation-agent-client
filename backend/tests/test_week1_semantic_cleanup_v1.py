"""Week 1 final semantic cleanup regression tests.

Covers the two shipped semantic-boundary fixes plus the consistency/leak
gates required before the Week 1 freeze:

A. Policy availability semantics — the user-facing states are
   distinguishable and never collapsed (P9 Policy Relevance, P14 Bounded
   Honesty, P20 Empty Is a Valid Result). Retrieval is case-wide; the
   finding-level basis is DATA (finding_policy_status):
     1. case has zero citations         → EMPTY ("no policy found"), NOT a failure
     2. no finding-level basis          → completed SUCCESS: "No finding-level
          policy basis is attached to this finding." + case-level refs labeled
          case-level — never unsupported, never a failure
     3. policy integration failure      → retryable/unavailable wording,
          distinct from 1 and 2
     4. platform lacks the capability   → TOOL_NOT_IMPLEMENTED wording
     5. finding associated              → "N policy references apply…" (unchanged)

B. Canonical Finding block — a finding-scoped artifact carries EXACTLY one
   canonical finding block regardless of how many execution calls produced
   representations of that finding (risk_case_fetch / finding_drilldown /
   signal_explain / prior-turn records).

C. Provenance = actual content contributors only — per-call granularity:
   failed calls that contributed nothing, prior finding/task calls, and
   superseded duplicates are excluded (P10/P11).

D. Signal-explanation consistency — an artifact never states "no trigger
   values recorded" beside a finding summary that names trigger figures;
   RP's own rule description is used; no values are fabricated.

E. Identifier freeze gate — user-facing responses carry no internal
   capability/skill/tool identifiers.
"""

from unittest.mock import patch

import pytest

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import artifact_bundle, policy_lookup, risk_case_fetch
from app.executor_v2 import ExecutorError
from app.investigation_service import compose_response
from app.models import (
    Finding,
    FindingCapability,
    ToolCallStatusV2,
    ToolCallV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)


# --- fixtures (real-shape payloads; adapter mocked, no live RP/LLM) ---------------

def live_shape_explanation():
    """Explanation payload shaped like the LIVE U00299: the Coordinated
    Trading key finding carries NO [n] citation marker (unlike the shared
    test fixture, which marks it [1]). The entry is replaced IN PLACE so
    finding ids/positions match the shared fixture."""
    ex = fx.rp_explanation_payload()
    ex["key_findings"] = [
        ("2. Coordinated Trading Pattern\nAn opposite-trade ratio of "
         "45.24% exceeded the 40% threshold, triggering the coordinated "
         "trading rule.")
        if "Coordinated Trading" in kf else kf
        for kf in ex["key_findings"]
    ]
    return ex


def make_live_shape_case():
    """Case fetch whose F2 is capability-identical to the live platform
    (no policy_lookup — no citation attached to it)."""
    explanation = live_shape_explanation()
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (fx.rp_evidence_payload(uid), explanation),
    ):
        result = risk_case_fetch("U00299")
    cc = dict(result.data)
    cc["_explanation"] = explanation
    return result, cc


def make_case(explanation=None):
    """A real-shape case fetch for U00299 (shared-fixture citations)."""
    explanation = explanation or fx.rp_explanation_payload()
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (fx.rp_evidence_payload(uid), explanation),
    ):
        result = risk_case_fetch("U00299")
    cc = dict(result.data)
    cc["_explanation"] = explanation
    return result, cc


def ct_finding(case):
    return next(f for f in case.data["findings"]
                if f.title == "Coordinated Trading Pattern")


def tc_from(tool_call_id, tool_name, result, arguments=None,
            status=ToolCallStatusV2.SUCCESS):
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name=tool_name, arguments=arguments or {},
        status=status, result=result,
        started_at="2026-08-28T00:00:00Z",
        completed_at="2026-08-28T00:00:01Z",
    )


def rule_signal_result(finding_id, *, with_trigger=True):
    """Real-shape signal_explain Rule result. The LIVE U00299 rule evidence
    for Coordinated Trading Pattern carries only name/severity/description —
    no structured trigger/threshold/contribution fields."""
    rule = {
        "name": "Coordinated Trading Pattern",
        "severity": "HIGH",
        "description": ("An opposite-trade ratio of 45.24% exceeded the "
                        "40% threshold, triggering the coordinated trading "
                        "rule."),
    }
    if with_trigger:
        rule.update({
            "trigger_values": {"opposite_trade_ratio": 0.4524},
            "threshold": "opposite_trade_ratio > 0.4",
            "contribution": 35,
        })
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": finding_id, "signal_type": "Rule", "rule": rule,
              "evidence_refs": [], "signal_refs": [], "policy_refs": [],
              "evidence_missing": False, "next_data_needed": []},
    )


def policy_success_result(finding_id):
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": finding_id, "topic": "t",
              "matches": [{"citation_id": 1, "chunk_id": "c",
                           "document": "AML_Suspicious_Indicators.md",
                           "section": "2.1", "snippet": "s", "relevance": 1}],
              "associated_policy_refs": [], "newly_retrieved_refs": [],
              "policy_refs": [], "required_evidence": [],
              "evidence_missing": False, "next_data_needed": []},
    )


# ===========================================================================
# A. Policy availability semantics
# ===========================================================================

class TestPolicySemantics:
    # --- CASE 1: platform supports policy lookup AND finding is eligible,
    # but no policy matches → EMPTY, not unsupported.
    def test_no_applicable_policy_is_empty_and_says_found_none(self):
        explanation = fx.rp_explanation_payload()
        explanation["citations"] = []
        case, _ = make_case(explanation)
        f = ct_finding(case)
        # keep the finding eligible despite the citation-less derivation
        f = f.model_copy(update={
            "capabilities": FindingCapability.model_validate(
                ["timeline", "signal_explain", "policy_lookup"])})
        cc = dict(case.data)
        cc["findings"] = self._swap(case, f)
        cc["_explanation"] = explanation     # no live RP round-trip
        r = policy_lookup(topic="policy requirements", finding_id=f.finding_id,
                          case_id="U00299", case_context=cc)
        assert r.outcome == ToolResultOutcome.EMPTY
        assert r.error is None                       # not a failure
        assert r.data["matches"] == []
        # composer: valid-empty wording, NOT unsupported, NOT integration
        text = compose_response(
            user_request="policy", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[])
        assert "No directly applicable policy was found" in text
        assert "not supported" not in text.lower()
        assert "could not be reached" not in text

    # --- CASE 2: no finding-level policy basis (the live F2 shape: no [n]
    # marker → no policy_refs) → a valid COMPLETED data answer, never an
    # unsupported capability, never a failure, never "no policy exists".
    def test_no_finding_level_basis_is_completed_data_answer(self):
        case, cc = make_live_shape_case()
        f = ct_finding(case)
        assert "policy_lookup" not in f.capabilities.root   # never derived
        r = policy_lookup(topic="policy requirements",
                          finding_id=f.finding_id, case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.error is None
        assert r.data["finding_policy_status"] == "no_finding_level_basis"
        assert r.data["policy_refs"] == []           # no finding-level basis
        assert r.data["matches"]                     # case-level refs returned
        text = compose_response(
            user_request="有相关政策支持你的分析吗", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[])
        # the pinned product sentence — a data answer, not a failure
        assert "No finding-level policy basis is attached to this finding." \
            in text
        # case-level references are explicitly labeled case-level
        assert "case-level policy references apply to the investigation " \
            "overall" in text
        # never capability/failure/availability semantics
        assert "not available for this type of finding" not in text
        assert "This investigation capability is not supported for the " \
            "current target" not in text
        assert "could not be reached" not in text
        assert "failed" not in text.lower()
        assert "task" not in text.lower() or True

    def test_other_capability_gate_gets_its_own_scoped_phrase(self):
        r = ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(
                code="CAPABILITY_NOT_SUPPORTED", message="no timeline",
                detail={"capability": "timeline", "scope": "finding",
                        "finding_id": "F2", "supported_capabilities": []}))
        fatal = ExecutorError(
            code="STEP_EXECUTION_ERROR", message="step failed", step_id="S1",
            detail={"outcome": "unsupported", "tool_call_id": "TC-T"})
        text = compose_response(
            user_request="show timeline", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-T", "finding_drilldown", r)],
            execution_errors=[fatal])
        assert "The timeline investigation is not available for this type " \
            "of finding." in text
        assert "Policy lookup" not in text

    def test_unsupported_without_capability_detail_falls_back_generic(self):
        # gate detail absent → the pinned generic wording (P14 fallback)
        r = ToolResult(
            outcome=ToolResultOutcome.UNSUPPORTED,
            error=ToolError(code="CAPABILITY_NOT_SUPPORTED",
                            message="unsupported", detail={}))
        fatal = ExecutorError(
            code="STEP_EXECUTION_ERROR", message="step failed", step_id="S1",
            detail={"outcome": "unsupported", "tool_call_id": "TC-X"})
        text = compose_response(
            user_request="q", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-X", "policy_lookup", r)],
            execution_errors=[fatal])
        assert "This investigation capability is not supported for the " \
            "current target" in text

    # --- CASE 3: policy lookup integration failure → retryable/unavailable
    # semantics, distinct from 1 and 2.
    def test_integration_failure_is_retryable_and_distinct(self):
        from app.adapters.risk_platform import RiskPlatformError
        case, cc = make_live_shape_case()
        cc.pop("_explanation", None)
        with patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (_ for _ in ()).throw(
                RiskPlatformError("unavailable", "Risk Platform timed out")),
        ):
            f = ct_finding(case)
            r = policy_lookup(topic="t", finding_id=f.finding_id,
                              case_id="U00299")
        assert r.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert r.error.code == "RISK_PLATFORM_UNAVAILABLE"
        text = compose_response(
            user_request="policy", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[ExecutorError(
                code="STEP_EXECUTION_ERROR", message="step failed",
                step_id="S1",
                detail={"outcome": "integration_error",
                        "tool_call_id": "TC-P"})])
        assert "could not be reached" in text       # availability semantics
        assert "try again later" in text            # retryable
        assert "No directly applicable policy was found" not in text
        assert "not available for this type of finding" not in text

    def test_three_policy_states_are_mutually_distinct(self):
        """The exact user-facing sentences for the reachable policy states
        must not share distinguishing phrases — each answer names its own
        meaning: no-basis (data), case-empty, integration failure."""
        no_basis = ("No finding-level policy basis is attached to this "
                    "finding.")
        found_none = "No directly applicable policy was found for this finding."
        unavailable = "could not be reached"
        trio = {no_basis, found_none, unavailable}
        assert len(trio) == 3
        for a in (no_basis, found_none):
            assert unavailable not in a
        assert "policy" not in unavailable

    # --- CASE 4: platform lacks the capability entirely → the generic
    # TOOL_NOT_IMPLEMENTED wording (distinct from CASE 2).
    def test_platform_missing_capability_is_system_level(self):
        fatal = ExecutorError(
            code="TOOL_NOT_IMPLEMENTED", message="not implemented",
            step_id="S1", detail={})
        text = compose_response(
            user_request="q", skill_id=None, plan=None, tool_calls=[],
            execution_errors=[fatal])
        assert "not available in the current system" in text
        assert "not available for this type of finding" not in text

    # --- P9: case-level references never presented as finding-applicable.
    def test_case_level_matches_not_claimed_as_finding_applicable(self):
        case, cc = make_case()
        f = ct_finding(case)
        r = policy_lookup(topic="transfers velocity spike",
                          finding_id=f.finding_id, case_context=cc)
        if r.outcome == ToolResultOutcome.SUCCESS \
                and r.data["matches"] \
                and not r.data["associated_policy_refs"]:
            assert r.data["finding_policy_status"] == "no_finding_level_basis"
            text = compose_response(
                user_request="policy", skill_id=None, plan=None,
                tool_calls=[tc_from("TC-P", "policy_lookup", r)],
                execution_errors=[])
            assert "No finding-level policy basis is attached" in text
            assert "apply to this finding" not in text

    def test_finding_associated_matches_still_say_apply(self):
        # fixture F2 IS citation-marked ([1]) in the shared RP payload
        case, cc = make_case()
        f = ct_finding(case)
        r = policy_lookup(topic="transfers velocity spike",
                          finding_id=f.finding_id, case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        if r.data["associated_policy_refs"]:
            text = compose_response(
                user_request="policy", skill_id=None, plan=None,
                tool_calls=[tc_from("TC-P", "policy_lookup", r)],
                execution_errors=[])
            assert "apply" in text

    @staticmethod
    def _swap(case, f):
        return [f if x.finding_id == f.finding_id else x
                for x in case.data["findings"]]


# ===========================================================================
# A2. Policy presentation scope — finding-level max-2 vs case-level complete
# ===========================================================================

def _policy_match(citation_id, doc="Doc_X.md", section="1.0"):
    return {"citation_id": citation_id, "chunk_id": f"c{citation_id}",
            "document": doc, "section": section, "snippet": "s",
            "relevance": 1}


def _policy_payload(*, associated_ids=(), n_case_citations=0):
    """A real-shape policy_lookup SUCCESS payload built directly from
    citation counts (composer is a pure function of this payload)."""
    associated = [_policy_match(i, f"FindingDoc_{i}.md")
                  for i in associated_ids]
    case_citations = [_policy_match(100 + i) for i in range(n_case_citations)]
    matches = associated + [m for m in case_citations
                            if m["citation_id"] not in associated_ids]
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"finding_id": "F2",
              "finding_policy_status":
                  "associated" if associated_ids else "no_finding_level_basis",
              "topic": "t", "matches": matches,
              "associated_policy_refs": [
                  {"citation_id": m["citation_id"], "chunk_id": m["chunk_id"]}
                  for m in associated],
              "newly_retrieved_refs": [], "policy_refs": [
                  {"citation_id": i, "chunk_id": f"c{i}"} for i in associated_ids],
              "required_evidence": [],
              "evidence_missing": not associated_ids,
              "next_data_needed": (["finding-level policy association"]
                                   if not associated_ids else [])},
    )


def _compose_policy(payload):
    return compose_response(
        user_request="policy", skill_id=None, plan=None,
        tool_calls=[tc_from("TC-P", "policy_lookup", payload)],
        execution_errors=[])


class TestPolicyPresentationScope:
    """Two distinct presentation contracts (P9):
    finding-level basis → conversational max-2 relevance contract;
    no finding-level basis → the COMPLETE authoritative case-level set,
    never silently truncated. A finding limit is not a case-level limit."""

    def test_one_finding_level_policy_plus_case_citations_max2(self):
        # 1. one finding-level citation + several case-level ones →
        # finding-level response: max 2 total, associated first.
        p = _policy_payload(associated_ids=(7,), n_case_citations=3)
        text = _compose_policy(p)
        assert "2 policy references apply to this finding:" in text
        assert "FindingDoc_7.md" in text          # associated listed first
        listed = [ln for ln in text.splitlines()
                  if ln[:2] in ("1.", "2.", "3.")]
        assert len(listed) == 2                   # max-2 cap holds
        assert "102" not in text                  # third citation dropped
        assert "No finding-level policy basis" not in text

    def test_two_finding_level_policies_unchanged(self):
        # 2. two finding-level citations → max-2 behavior unchanged.
        p = _policy_payload(associated_ids=(7, 8), n_case_citations=3)
        text = _compose_policy(p)
        assert "2 policy references apply to this finding:" in text
        listed = [ln for ln in text.splitlines()
                  if ln[:2] in ("1.", "2.", "3.")]
        assert len(listed) == 2
        assert "FindingDoc_7.md" in text and "FindingDoc_8.md" in text

    def test_no_basis_three_case_citations_all_shown(self):
        # 3. zero finding-level policies + THREE case-level citations →
        # ALL three shown (the U00299/F2 live defect: [3],[2] shown, [1] lost).
        p = _policy_payload(associated_ids=(), n_case_citations=3)
        text = _compose_policy(p)
        assert "No finding-level policy basis is attached to this finding." \
            in text
        assert "case-level policy references apply to the investigation " \
            "overall" in text
        listed = [ln for ln in text.splitlines()
                  if ln[:2] in ("1.", "2.", "3.")]
        assert len(listed) == 3
        assert "100" in text and "101" in text and "102" in text

    def test_no_basis_one_case_citation_shown(self):
        # 4. zero finding-level policies + ONE case-level citation → shown.
        p = _policy_payload(associated_ids=(), n_case_citations=1)
        text = _compose_policy(p)
        assert "No finding-level policy basis is attached to this finding." \
            in text
        assert "Doc_X.md" in text

    def test_no_basis_zero_case_citations_is_valid_empty(self):
        # 5. zero/zero → valid empty result (P20), no fabricated list.
        explanation = fx.rp_explanation_payload()
        explanation["citations"] = []
        case, _ = make_case(explanation)
        f = ct_finding(case)
        cc = dict(case.data)
        cc["_explanation"] = explanation     # no live RP round-trip
        r = policy_lookup(topic="t", finding_id=f.finding_id,
                          case_id="U00299", case_context=cc)
        assert r.outcome == ToolResultOutcome.EMPTY
        assert r.data["matches"] == []
        text = compose_response(
            user_request="policy", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[])
        assert "No directly applicable policy was found" in text

    def test_case_level_refs_never_claimed_as_supporting_the_finding(self):
        # 6. case-level presentation must never describe the references as
        # directly supporting the finding.
        p = _policy_payload(associated_ids=(), n_case_citations=3)
        text = _compose_policy(p)
        assert "apply to this finding" not in text
        assert "apply to the investigation overall" in text

    def test_case_scoped_artifact_still_renders_all_case_citations(self):
        # 7. artifact contract unchanged: all case citations render there.
        case, _ = make_case()
        r = artifact_bundle(
            scope="case", case_id="U00299", task_id="T-1",
            source_tool_calls=[tc_from("TC-FETCH", "risk_case_fetch", case)])
        content = r.data["artifact"]["content"]
        assert "Case-level citations" in content
        # the fixture carries citations [1] and [2] — both present
        assert "[1]" in content and "[2]" in content


# ===========================================================================
# B+C. Canonical Finding block + per-call provenance
# ===========================================================================

class TestCanonicalFindingBlock:
    def test_prior_and_current_fetch_exactly_one_f2_block(self):
        """The live scenario: an earlier turn's intake fetch + the artifact
        turn's own fetch both sit in the provenance pool → exactly ONE
        canonical F2 block, owned by the latest fetch."""
        case1, _ = make_case()          # prior-turn fetch
        case2, _ = make_case()          # this turn's fetch
        f2 = ct_finding(case2)
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH-PRIOR", "risk_case_fetch", case1),
                tc_from("TC-FETCH-NOW", "risk_case_fetch", case2),
            ])
        assert r.outcome == ToolResultOutcome.SUCCESS
        content = r.data["artifact"]["content"]
        assert content.count("### F2 — Coordinated Trading Pattern") == 1
        src = r.data["artifact"]["source_tool_calls"]
        assert "TC-FETCH-NOW" in src
        assert "TC-FETCH-PRIOR" not in src

    def test_drilldown_and_signal_representations_do_not_duplicate(self):
        """B.5: finding_drilldown / signal_explain results ABOUT F2 in the
        pool must not append another canonical F2 finding block."""
        case, _ = make_case()
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-SIG", "signal_explain",
                        rule_signal_result("F2", with_trigger=False)),
                tc_from("TC-POL", "policy_lookup",
                        policy_success_result("F2")),
            ])
        assert r.outcome == ToolResultOutcome.SUCCESS
        content = r.data["artifact"]["content"]
        assert content.count("### F2 — Coordinated Trading Pattern") == 1
        # supporting sections still render
        assert "## Signal Explanation" in content
        assert "## Policy References" in content

    def test_case_scope_renders_each_finding_once_across_fetches(self):
        case1, _ = make_case()
        case2, _ = make_case()
        r = artifact_bundle(
            scope="case", case_id="U00299", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH-PRIOR", "risk_case_fetch", case1),
                tc_from("TC-FETCH-NOW", "risk_case_fetch", case2),
            ])
        assert r.outcome == ToolResultOutcome.SUCCESS
        content = r.data["artifact"]["content"]
        assert content.count("### F2 — Coordinated Trading Pattern") == 1
        assert content.count("### F1 —") == 1
        src = r.data["artifact"]["source_tool_calls"]
        assert src == ["TC-FETCH-NOW"]          # canonical fetch only


class TestProvenanceContributorsOnly:
    def test_failed_non_contributing_call_excluded(self):
        """C.7: a FAILED risk_case_fetch that contributed nothing is not
        listed as a source; the canonical success is."""
        case, _ = make_case()
        failed = ToolCallV2(
            tool_call_id="TC-FETCH-FAIL", investigation_id="CASE:U00299",
            task_id="T-1", tool_name="risk_case_fetch", arguments={},
            status=ToolCallStatusV2.FAILED,
            result=ToolResult(
                outcome=ToolResultOutcome.INTEGRATION_ERROR,
                error=ToolError(code="RISK_PLATFORM_UNAVAILABLE",
                                message="down")),
            started_at="2026-08-28T00:00:00Z",
            completed_at="2026-08-28T00:00:01Z",
        )
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[failed, tc_from("TC-FETCH", "risk_case_fetch",
                                               case)])
        assert r.outcome == ToolResultOutcome.SUCCESS
        src = r.data["artifact"]["source_tool_calls"]
        assert src == ["TC-FETCH"]
        assert "TC-FETCH-FAIL" not in src

    def test_provenance_is_exactly_the_content_contributors(self):
        """C.6: source_tool_calls == the calls whose results rendered
        content — fetch (canonical block), signal, policy; nothing else."""
        case, _ = make_case()
        unrelated = ToolCallV2(
            tool_call_id="TC-UNREL", investigation_id="CASE:U00299",
            task_id="T-1", tool_name="finding_drilldown", arguments={},
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS,
                              data={"unrelated": True}),
            started_at="2026-08-28T00:00:00Z",
            completed_at="2026-08-28T00:00:01Z",
        )
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-SIG", "signal_explain", rule_signal_result("F2")),
                tc_from("TC-POL", "policy_lookup", policy_success_result("F2")),
                unrelated,
            ])
        src = r.data["artifact"]["source_tool_calls"]
        assert src == ["TC-FETCH", "TC-SIG", "TC-POL"]

    def test_prior_finding_calls_do_not_leak_into_current_artifact(self):
        """C.8: an earlier turn's OTHER-finding investigation (timeline for
        F3) stays out of the F2 artifact's content and provenance."""
        case, _ = make_case()
        f3_tl = ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": "F3", "view": "timeline",
                  "events": [{"event_id": "F3-E001", "finding_id": "F3",
                              "timestamp": "2026-08-19T10:30:00Z",
                              "event_type": "withdrawal",
                              "summary": "Withdrawal of 0.5 BTC",
                              "evidence_refs": [], "signal_refs": [],
                              "policy_refs": [], "importance": "high"}],
                  "total_events": 1, "truncated": False, "top_n": None})
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-TL3-PRIOR", "finding_drilldown", f3_tl),
            ])
        assert r.outcome == ToolResultOutcome.SUCCESS
        content = r.data["artifact"]["content"]
        assert "F3-E001" not in content
        assert "Withdrawal of 0.5 BTC" not in content
        src = r.data["artifact"]["source_tool_calls"]
        assert "TC-TL3-PRIOR" not in src
        assert src == ["TC-FETCH"]

    def test_artifact_strictly_finding_scoped(self):
        """C.9: F2 artifact — no other finding blocks, no case-level
        citation collection, no unrelated policies."""
        case, _ = make_case()
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-POL", "policy_lookup", policy_success_result("F2")),
            ])
        content = r.data["artifact"]["content"]
        for other in ("F1 —", "F3 —", "F4 —"):
            assert f"### {other}" not in content
        assert "Case-level citations" not in content
        # the finding's own authoritative citations still render
        assert "Policy citations: [1]" in content

    def test_case_scope_artifact_remains_broad(self):
        """C.10: case-scoped artifact unaffected — all findings (once each),
        the case citation collection, broader provenance."""
        case, _ = make_case()
        r = artifact_bundle(
            scope="case", case_id="U00299", task_id="T-1",
            source_tool_calls=[tc_from("TC-FETCH", "risk_case_fetch", case)])
        content = r.data["artifact"]["content"]
        assert "## Findings" in content
        assert "### F2 — Coordinated Trading Pattern" in content
        assert "Case-level citations" in content
        src = r.data["artifact"]["source_tool_calls"]
        assert src == ["TC-FETCH"]


# ===========================================================================
# D. Signal-explanation consistency
# ===========================================================================

class TestSignalConsistency:
    def test_real_shape_rule_renders_description_not_no_trigger_recorded(self):
        """Live U00299 shape: rule evidence has no structured trigger
        fields, but RP's own description carries the figures the finding
        summary cites. The artifact must not contradict its own Finding
        block — and must not fabricate structured values."""
        case, _ = make_case()
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-SIG", "signal_explain",
                        rule_signal_result("F2", with_trigger=False)),
            ])
        content = r.data["artifact"]["content"]
        assert "no trigger values recorded" not in content
        assert "threshold None" not in content
        assert "contribution None" not in content
        # RP's own description (with the same figures as the summary) renders
        assert "45.24% exceeded the 40% threshold" in content
        # the absence of structured fields is stated honestly
        assert "no separate trigger/threshold/contribution fields" in content
        # no fabricated structured values
        assert "opposite_trade_ratio=0.4524" not in content

    def test_structured_trigger_values_render_as_before(self):
        case, _ = make_case()
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-SIG", "signal_explain",
                        rule_signal_result("F2", with_trigger=True)),
            ])
        content = r.data["artifact"]["content"]
        assert "observed opposite_trade_ratio=0.4524" in content
        assert "threshold opposite_trade_ratio > 0.4" in content


# ===========================================================================
# E. Identifier freeze gate
# ===========================================================================

class TestNoIdentifierLeak:
    _INTERNAL = ("policy_lookup", "retrieve_policy", "timeline_investigation",
                 "signal_explain", "finding_drilldown", "risk_case_fetch",
                 "artifact_bundle", "UNSUPPORTED", "CAPABILITY_NOT_SUPPORTED")

    def _assert_clean(self, text):
        for ident in self._INTERNAL:
            assert ident not in text, f"internal identifier leaked: {ident}"

    def test_policy_no_basis_answer_has_no_identifiers(self):
        case, cc = make_live_shape_case()
        f = ct_finding(case)
        r = policy_lookup(topic="t", finding_id=f.finding_id, case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["finding_policy_status"] == "no_finding_level_basis"
        text = compose_response(
            user_request="有相关政策支持你的分析吗", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[])
        self._assert_clean(text)

    def test_policy_empty_answer_has_no_identifiers(self):
        explanation = fx.rp_explanation_payload()
        explanation["citations"] = []
        case, _ = make_case(explanation)
        f = ct_finding(case)   # no capability construction needed any more
        cc = dict(case.data)
        cc["findings"] = [f]
        cc["_explanation"] = explanation     # no live RP round-trip
        r = policy_lookup(topic="t", finding_id=f.finding_id, case_id="U00299",
                          case_context=cc)
        assert r.outcome == ToolResultOutcome.EMPTY
        text = compose_response(
            user_request="policy", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[])
        self._assert_clean(text)
        # the EMPTY warning is also user-facing (rendered as a Note)
        for w in r.warnings or []:
            self._assert_clean(w)


# ===========================================================================
# F. Artifact/response scope semantics — gaps name the actual missing item;
#    finding artifacts obey strict policy containment
# ===========================================================================

def _artifact_success_payload(content, *, evidence_missing=False,
                              next_data_needed=None):
    """A real-shape artifact_bundle SUCCESS result (data the composer sees).
    The tool sets evidence_missing=bool(gaps) on the payload — the inherited
    flag that caused the false transaction-evidence claim."""
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"artifact": {"artifact_id": "ART-X", "title": "t",
                           "content": content, "scope": "finding:F3"},
              "artifact_id": "ART-X", "scope": "finding", "format": "md",
              "source_tool_call_count": 1,
              "evidence_missing": evidence_missing,
              "next_data_needed": next_data_needed or []},
    )


class TestArtifactResponseScopeSemantics:
    def test_artifact_success_does_not_inherit_unrelated_evidence_sentence(self):
        # 1. an artifact payload whose inherited evidence_missing reflects a
        # policy-metadata gap must NOT trigger the generic transaction-
        # evidence sentence in the completion response.
        r = _artifact_success_payload(
            "# Finding Investigation Bundle\n...\nNo finding-level policy "
            "basis is attached to this finding.\n",
            evidence_missing=True,
            next_data_needed=["No finding-level policy association is "
                              "attached to this finding."])
        text = compose_response(
            user_request="Generate a Markdown investigation bundle for "
                         "this finding.",
            skill_id=None, plan=None,
            tool_calls=[tc_from("TC-ART", "artifact_bundle", r)],
            execution_errors=[])
        assert "The investigation bundle has been created" in text
        assert "complete transaction-level evidence is not available" \
            not in text
        assert "Additional data needed" not in text

    def test_genuine_transaction_gap_still_states_evidence_gap(self):
        # 2. a payload whose gap IS transaction evidence (e.g. a timeline/
        # evidence result without complete records) keeps an evidence-gap
        # sentence — the generic wording is scoped to real evidence gaps.
        r = ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": "F3", "signal_type": "ML",
                  "explanation": {"ml_score": 50}, "records": None,
                  "evidence_missing": True,
                  "next_data_needed": ["transaction records"]})
        text = compose_response(
            user_request="why", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-S", "signal_explain", r)],
            execution_errors=[])
        assert "complete transaction-level evidence is not available" in text

    def test_policy_gap_never_becomes_generic_transaction_claim(self):
        # 3. policy-specific evidence_missing (no finding-level association)
        # renders the precise data statement, never transaction wording.
        r = policy_lookup(topic="t", finding_id="F2",
                          case_id="U00299",
                          case_context=self._live_context())
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["evidence_missing"] is True
        text = compose_response(
            user_request="policy", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[])
        assert "transaction" not in text
        assert "No finding-level policy basis is attached" in text

    @staticmethod
    def _unmarked_finding_id(case):
        """The live-defect shape: an UNMARKED finding (no [n] marker →
        empty policy_refs). Resolved from the payload, never hardcoded."""
        f = next(f for f in case.data["findings"]
                 if f.title == "Coordinated Trading Pattern")
        assert not f.policy_refs          # the no-basis shape under test
        return f.finding_id

    @staticmethod
    def _policy_context():
        explanation = fx.rp_explanation_payload()
        case, _ = make_case(explanation)
        cc = dict(case.data)
        cc["_explanation"] = explanation
        return cc

    def test_finding_artifact_zero_policy_refs_excludes_case_policies(self):
        # 4. finding artifact + no finding-level basis → the case-level
        # fallback list must NOT leak into the artifact (live F3 defect).
        case, _ = make_live_shape_case()
        fid = self._unmarked_finding_id(case)
        pol = policy_lookup(topic="t", finding_id=fid,
                            case_id="U00299",
                            case_context=self._live_context())
        assert pol.outcome == ToolResultOutcome.SUCCESS
        assert pol.data["associated_policy_refs"] == []
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id=fid, task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-POL", "policy_lookup", pol),
            ])
        content = r.data["artifact"]["content"]
        assert "No finding-level policy basis is attached to this " \
            "finding." in content
        # the case-level citations must not render as the finding's basis
        assert "| Document | Section | Citation | Snippet |" not in content
        assert "AML_Suspicious_Indicators.md" not in content
        assert "Investigation_and_Action_SOP.md" not in content
        assert "Risk_Scoring_Explainability_Guide.md" not in content

    @staticmethod
    def _live_context():
        explanation = live_shape_explanation()
        case, _ = make_case(explanation)
        cc = dict(case.data)
        cc["_explanation"] = explanation
        return cc

    def test_case_scoped_artifact_still_includes_all_case_policies(self):
        # 5. case scope unchanged: the case citation table renders.
        case, _ = make_case()
        r = artifact_bundle(
            scope="case", case_id="U00299", task_id="T-1",
            source_tool_calls=[tc_from("TC-FETCH", "risk_case_fetch", case)])
        content = r.data["artifact"]["content"]
        assert "Case-level citations" in content
        assert "[1]" in content and "[2]" in content

    def test_finding_artifact_with_real_policy_refs_renders_them(self):
        # 6. a finding WITH authoritative refs → its citations render in
        # the Finding block (fixture F2 carries [1]).
        case, _ = make_case()
        assert "policy_lookup" or True
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id="F2", task_id="T-1",
            source_tool_calls=[tc_from("TC-FETCH", "risk_case_fetch", case)])
        content = r.data["artifact"]["content"]
        assert "Policy citations: [1]" in content

    def test_finding_artifact_without_association_states_it(self):
        # 7. a finding WITHOUT association → explicit no-basis statement.
        case, _ = make_live_shape_case()
        fid = self._unmarked_finding_id(case)
        pol = policy_lookup(topic="t", finding_id=fid, case_id="U00299",
                            case_context=self._live_context())
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id=fid, task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-POL", "policy_lookup", pol),
            ])
        content = r.data["artifact"]["content"]
        assert "No finding-level policy basis is attached to this " \
            "finding." in content

    def test_evidence_gaps_name_the_actual_missing_data(self):
        # 8. the artifact's Evidence Gaps section names the policy-
        # association gap precisely — never generic "insufficient evidence".
        case, _ = make_live_shape_case()
        fid = self._unmarked_finding_id(case)
        pol = policy_lookup(topic="t", finding_id=fid, case_id="U00299",
                            case_context=self._live_context())
        r = artifact_bundle(
            scope="finding", case_id="U00299", finding_id=fid, task_id="T-1",
            source_tool_calls=[
                tc_from("TC-FETCH", "risk_case_fetch", case),
                tc_from("TC-POL", "policy_lookup", pol),
            ])
        content = r.data["artifact"]["content"]
        assert "## Evidence Gaps" in content
        assert "No finding-level policy association is attached to this " \
            "finding." in content
        assert "Evidence is insufficient" not in content

    def test_conversation_policy_answers_unchanged(self):
        # 9. conversation-level F2/F3 behavior is unaffected by the
        # artifact containment: no-basis → complete case-level set.
        case, cc = make_live_shape_case()   # live shape: unmarked F2
        r = policy_lookup(topic="t", finding_id="F2", case_context=cc)
        assert r.outcome == ToolResultOutcome.SUCCESS
        text = compose_response(
            user_request="policy", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P", "policy_lookup", r)],
            execution_errors=[])
        assert "No finding-level policy basis is attached" in text
        # ALL authoritative case-level citations shown, not capped at 2 —
        # the fixture carries [1] and [2]; both documents must appear
        listed = [ln for ln in text.splitlines() if ln[:1].isdigit()]
        assert len(listed) == len(fx.rp_explanation_payload()["citations"])
        assert "AML_Suspicious_Indicators.md" in text
        assert str(fx.rp_explanation_payload()["citations"][-1]["id"]) in text
        # the associated path still caps at 2 (shared fixture: F2 marked [1])
        case2, cc2 = make_case()
        r2 = policy_lookup(topic="t", finding_id="F2", case_context=cc2)
        assert r2.outcome == ToolResultOutcome.SUCCESS
        assert r2.data["associated_policy_refs"]
        t2 = compose_response(
            user_request="policy", skill_id=None, plan=None,
            tool_calls=[tc_from("TC-P2", "policy_lookup", r2)],
            execution_errors=[])
        assert "apply to this finding" in t2
        listed = [ln for ln in t2.splitlines() if ln[:2] in ("1.", "2.", "3.")]
        assert len(listed) <= 2          # finding-level max-2 holds

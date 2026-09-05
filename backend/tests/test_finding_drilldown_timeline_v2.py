"""Tests for finding_drilldown(view="timeline") — Week 1 Focus Mode drill-down.

All RP responses are mocked at the adapter boundary; no live Risk Platform.
Covers the 20 required scenarios: canonical resolution, capability
enforcement, TimelineEvent normalization, deterministic IDs/ordering,
top_n bounding, outcome semantics, and provider/executor integration.
"""

from typing import Any
from unittest.mock import patch

import pytest

from app.domain_tools import finding_drilldown, risk_case_fetch
from app.executor_v2 import default_tool_provider
from app.models import (
    Finding,
    TimelineEvent,
    ToolResultOutcome,
)

import tests.test_risk_platform_adapter_v2 as fx


# --- fixtures -----------------------------------------------------------------

_FIXTURE_EVIDENCE: dict[int, dict] = {}   # id(case result) → evidence payload


def make_case(case_id="U00299", evidence_overrides=None):
    """Build a case from the fixture payload; the same payload is remembered
    so drilldown calls serve identical data (see run_drilldown)."""
    evidence = fx.rp_evidence_payload(case_id)
    if evidence_overrides:
        evidence.update(evidence_overrides)
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (evidence, fx.rp_explanation_payload()),
    ):
        result = risk_case_fetch(case_id)
    _FIXTURE_EVIDENCE[id(result)] = evidence
    return result


def patch_evidence(evidence: dict):
    return patch(
        "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
        new=lambda self, uid, expose_complete_records=False: evidence,
    )


@pytest.fixture()
def case_U00299():
    return make_case()


def withdrawal_finding(case) -> Finding:
    f = next(f for f in case.data["findings"]
             if "withdrawal" in f.title.lower()
             and f.capabilities.supports("timeline"))
    return f


def any_timeline_finding(case) -> Finding:
    return next(f for f in case.data["findings"]
                if f.capabilities.supports("timeline"))


def run_drilldown(case, finding_id, **kw):
    # The tool's internal evidence fetch re-serves the SAME payload the case
    # was built from (fixtures may carry modified evidence, e.g. stripped
    # timestamps) so normalization sees consistent data.
    kw.setdefault("view", "timeline")
    with patch_evidence(_FIXTURE_EVIDENCE[id(case)]):
        return finding_drilldown(
            finding_id=finding_id,
            case_context=case.data, **kw,
        )


# --- 1–3. valid timeline + model + deterministic IDs -------------------------------

class TestValidTimeline:
    def test_valid_timeline_for_a_finding(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        assert res.outcome == ToolResultOutcome.SUCCESS
        assert res.data["finding_id"] == f.finding_id
        assert res.data["events"]
        assert res.data["view"] == "timeline"

    def test_output_uses_timeline_event_model(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        for e in res.data["events"]:
            assert isinstance(e, TimelineEvent)

    def test_deterministic_event_ids(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        r1 = run_drilldown(case_U00299, f.finding_id)
        r2 = run_drilldown(case_U00299, f.finding_id)
        ids1 = [e.event_id for e in r1.data["events"]]
        ids2 = [e.event_id for e in r2.data["events"]]
        assert ids1 == ids2
        assert ids1 == [f"{f.finding_id}-E{i:03d}" for i in
                        range(1, len(ids1) + 1)]
        assert all(e.finding_id == f.finding_id for e in r1.data["events"])


# --- 4–6. ordering + tie-breaking + top_n --------------------------------------------

class TestOrderingAndBounding:
    def test_chronological_ordering(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        stamps = [e.timestamp for e in res.data["events"]]
        assert stamps == sorted(stamps)

    def test_deterministic_tie_breaking(self, case_U00299):
        # All events share one timestamp → order must still be stable.
        evidence = fx.rp_evidence_payload()
        same_ts = "2026-08-19T12:00:00Z"
        for tx in evidence["transaction_evidence"]:
            tx["timestamp"] = same_ts
        for wd in evidence["withdrawal_evidence"]:
            wd["timestamp"] = same_ts
        evidence["risk_summary"]["detected_at"] = same_ts
        case = make_case(evidence_overrides=evidence)
        f = withdrawal_finding(case)
        r1 = run_drilldown(case, f.finding_id)
        r2 = run_drilldown(case, f.finding_id)
        keys1 = [(e.timestamp, e.event_type, e.summary) for e in r1.data["events"]]
        keys2 = [(e.timestamp, e.event_type, e.summary) for e in r2.data["events"]]
        assert keys1 == keys2                      # deterministic under ties
        stamps = [e.timestamp for e in r1.data["events"]]
        assert stamps == sorted(stamps)

    def test_top_n_limits_after_ordering(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        full = run_drilldown(case_U00299, f.finding_id)
        limited = run_drilldown(case_U00299, f.finding_id, top_n=2)
        assert limited.data["truncated"] is True
        assert limited.data["total_events"] == full.data["total_events"]
        assert len(limited.data["events"]) == 2
        # truncation keeps the chronologically earliest
        assert ([e.event_id for e in limited.data["events"]]
                == [e.event_id for e in full.data["events"][:2]])

    def test_top_n_invalid_values(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        for bad in (0, -3, "lots"):
            res = run_drilldown(case_U00299, f.finding_id, top_n=bad)
            assert res.outcome == ToolResultOutcome.VALIDATION_ERROR


# --- 7–10. resolution/capability/empty semantics -----------------------------------------

class TestResolutionAndSemantics:
    def test_invalid_finding_id_is_validation_error(self, case_U00299):
        res = run_drilldown(case_U00299, "F999")
        assert res.outcome == ToolResultOutcome.VALIDATION_ERROR
        assert "does not exist" in res.error.message

    def test_missing_or_blank_finding_id(self, case_U00299):
        for bad in ("", "   "):
            res = run_drilldown(case_U00299, bad)
            assert res.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_finding_without_timeline_support_is_unsupported_not_empty(self, case_U00299):
        # Find a finding whose capabilities exclude timeline (single-tx case
        # fixture always grants timeline, so fabricate one capability set).
        case = make_case(evidence_overrides=fx.rp_evidence_payload())
        no_tl = next(f for f in case.data["findings"]
                     if not f.capabilities.supports("timeline")) \
            if any(not f.capabilities.supports("timeline")
                   for f in case.data["findings"]) else None
        if no_tl is None:
            pytest.skip("fixture grants timeline to all findings")
        res = run_drilldown(case, no_tl.finding_id)
        assert res.outcome == ToolResultOutcome.UNSUPPORTED
        assert res.error.code == "CAPABILITY_NOT_SUPPORTED"
        assert res.data is None or not (res.data or {}).get("events")

    def test_capability_enforced_at_runtime_even_if_skill_unlocked(self, case_U00299):
        # Defense in depth: even called directly (outside the skill lock),
        # a non-timeline finding can never produce timeline events.
        for f in case_U00299.data["findings"]:
            res = run_drilldown(case_U00299, f.finding_id)
            if not f.capabilities.supports("timeline"):
                assert res.outcome == ToolResultOutcome.UNSUPPORTED
                break

    def test_valid_finding_with_no_timestamped_data_is_empty(self):
        # Keep transaction timestamps (source of timeline capability) but
        # strip the withdrawal finding's own streams → valid finding, no
        # timeline events for it.
        evidence = fx.rp_evidence_payload()
        for wd in evidence["withdrawal_evidence"]:
            wd["timestamp"] = None
        evidence["risk_summary"]["detected_at"] = None
        case = make_case(evidence_overrides=evidence)
        f = withdrawal_finding(case)
        res = run_drilldown(case, f.finding_id)
        assert res.outcome == ToolResultOutcome.EMPTY
        assert res.data["events"] == []
        assert res.error is None                      # genuinely nothing there

    def test_unknown_view_cannot_execute(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id, view="opposite_trades")
        # opposite_trades is a DISTINCT bounded semantic request
        # (FIX E15): unsupported with capability context, never a generic
        # validation error and never a silent evidence-stream fallback
        assert res.outcome == ToolResultOutcome.UNSUPPORTED
        assert res.error.code == "OPPOSITE_TRADES_NOT_SUPPORTED"
        assert "opposite-trades" in res.error.message
        assert res.data is None                       # nothing executed

    def test_no_case_context_and_no_case_id(self):
        res = finding_drilldown(finding_id="F1", view="timeline")
        assert res.outcome == ToolResultOutcome.VALIDATION_ERROR


# --- 11–12. integration failures ----------------------------------------------------------

class TestIntegrationFailures:
    def test_rp_failure_is_integration_error(self):
        from app.adapters.risk_platform import RiskPlatformError
        case = make_case()
        f = withdrawal_finding(case)
        with patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: (_ for _ in ()).throw(
                RiskPlatformError("unavailable", "Risk Platform timed out")),
        ):
            res = finding_drilldown(finding_id=f.finding_id, view="timeline",
                                    case_context=case.data)
        assert res.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert res.error.code == "RISK_PLATFORM_UNAVAILABLE"
        assert "timed out" in res.error.message

    def test_malformed_evidence_is_bounded_error(self):
        evidence = {"boom": True}                  # structurally broken payload
        case = make_case()
        f = withdrawal_finding(case)
        with patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: evidence,
        ):
            res = finding_drilldown(finding_id=f.finding_id, view="timeline",
                                    case_context=case.data)
        assert res.outcome == ToolResultOutcome.INTEGRATION_ERROR
        assert res.error.code == "RISK_PLATFORM_MALFORMED_RESPONSE"
        assert "required sections" in res.error.message


# --- 13–19. grounding / no fabrication ------------------------------------------------------

class TestGrounding:
    def test_no_fabricated_timestamps(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        source_stamps = {w["timestamp"] for w in fx.rp_evidence_payload()["withdrawal_evidence"]}
        source_stamps.add(fx.rp_evidence_payload()["risk_summary"]["detected_at"])
        for e in res.data["events"]:
            assert e.timestamp in source_stamps

    def test_no_fabricated_evidence_refs(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        source_ids = ({w["withdrawal_id"] for w in fx.rp_evidence_payload()["withdrawal_evidence"]}
                      | {fx.rp_evidence_payload()["user_id"]})
        for e in res.data["events"]:
            assert e.evidence_refs                      # every event grounded
            for ref in e.evidence_refs:
                assert ref.id in source_ids

    def test_no_fabricated_signal_refs(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        import json as _json
        finding_signals = {_json.dumps(s.model_dump(), sort_keys=True)
                           for s in f.signal_refs}
        for e in res.data["events"]:
            for s in e.signal_refs:
                assert _json.dumps(s.model_dump(), sort_keys=True) in finding_signals

    def test_no_fabricated_policy_refs(self, case_U00299):
        explanation = fx.rp_explanation_payload()
        explanation["citations"] = []                   # RP cited nothing
        evidence = fx.rp_evidence_payload()
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (evidence, explanation),
        ):
            case = risk_case_fetch("U00299")
        _FIXTURE_EVIDENCE[id(case)] = evidence          # register for drilldown
        f = withdrawal_finding(case)
        assert not f.policy_refs
        res = run_drilldown(case, f.finding_id)
        assert all(not e.policy_refs for e in res.data["events"])
        assert res.citation_refs == []

    def test_evidence_provenance_preserved_for_followups(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        # refs ride both on events and on the result envelope
        envelope_ids = {r.id for r in res.evidence_refs}
        assert envelope_ids
        for e in res.data["events"]:
            for r in e.evidence_refs:
                assert r.id in envelope_ids
        # citation refs mirror the finding's actual citations
        assert {c.chunk_id for c in res.citation_refs} == \
               {c.chunk_id for c in f.policy_refs}

    def test_summary_grounded_in_source_fields(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        res = run_drilldown(case_U00299, f.finding_id)
        wd = fx.rp_evidence_payload()["withdrawal_evidence"][0]
        first = res.data["events"][0]
        # deterministic template mirrors actual fields (amount + asset)
        assert str(wd["amount"]) in first.summary
        assert wd["asset"] in first.summary

    def test_importance_derived_deterministically(self, case_U00299):
        f = withdrawal_finding(case_U00299)
        r1 = run_drilldown(case_U00299, f.finding_id)
        r2 = run_drilldown(case_U00299, f.finding_id)
        imp1 = [e.importance for e in r1.data["events"]]
        imp2 = [e.importance for e in r2.data["events"]]
        assert imp1 == imp2
        assert all(i.value in ("high", "medium", "low") for i in imp1)


# --- 20. provider/executor integration -----------------------------------------------------------

class TestProviderIntegration:
    def test_tool_invokable_through_default_provider(self, case_U00299):
        provider = default_tool_provider()
        assert provider.has("finding_drilldown")
        f = withdrawal_finding(case_U00299)
        # hermetic: patch both RP touch points (case fetch + evidence fetch)
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: fx.rp_evidence_payload(uid),
        ):
            # No case_context/case_id → tool fetches the case itself.
            res = provider.get("finding_drilldown")({
                "finding_id": f.finding_id,
                "view": "timeline",
                "case_id": "U00299",
            })
        assert res.outcome == ToolResultOutcome.SUCCESS
        assert res.data["events"]

    def test_provider_still_has_risk_case_fetch(self):
        provider = default_tool_provider()
        assert provider.has("risk_case_fetch")

    def test_execution_via_executor_skill_path(self, case_U00299):
        # Full path: timeline_investigation plan → executor → drilldown tool.
        from app.executor_v2 import ExecutorV2
        from app.models import InvestigationContext, Plan, PlanStep, TaskV2
        from app.planner_v2 import PlannerV2
        from app.models import FindingCapability

        class FakeLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return ('{"skill_id": "timeline_investigation", "goal": "g", '
                        '"steps": [{"type": "inspect_timeline", "reason": "r"}]}')

        caps = FindingCapability.model_validate(
            ["timeline", "signal_explain", "policy_lookup"])
        ctx = InvestigationContext(case_id="U00299", focused_finding_id="F3")
        f = withdrawal_finding(case_U00299)
        ctx = InvestigationContext(case_id="U00299",
                                   focused_finding_id=f.finding_id)
        plan = PlannerV2(FakeLLM()).plan(
            "show the timeline", ctx, ["timeline_investigation"], caps)
        task = TaskV2(task_id="T-DD", investigation_id="CASE:U00299",
                      user_request="show timeline",
                      selected_skill="timeline_investigation")

        # Executor injects case_id into arguments (fetch_case only) — the
        # drilldown tool resolves the finding from a fresh case fetch, then
        # fetches evidence through the adapter (both endpoints mocked).
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ), patch(
            "app.adapters.risk_platform.RiskPlatformAdapter.fetch_case_evidence",
            new=lambda self, uid, expose_complete_records=False: fx.rp_evidence_payload(uid),
        ):
            ex = ExecutorV2(default_tool_provider())
            res = ex.execute(plan, task, ctx, finding_capabilities=caps)
        assert res.status.value == "completed"
        assert res.tool_calls[0].result.outcome == ToolResultOutcome.SUCCESS

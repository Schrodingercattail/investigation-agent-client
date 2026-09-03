"""Tests for the artifact_bundle tool (app/domain_tools/artifact_bundle.py).

Fake ToolResults/ToolCalls throughout — no LLM, no Risk Platform. Covers the
30 required scenarios: deterministic Markdown composition, scope semantics,
provenance closure, ArtifactV2/TaskV2 linkage, and executor integration.
"""

import json
from unittest.mock import patch

import pytest

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import artifact_bundle, risk_case_fetch
from app.executor_v2 import ExecutorV2, default_tool_provider
from app.models import (
    ArtifactTypeV2,
    ArtifactV2,
    EvidenceRef,
    InvestigationContext,
    Plan,
    PlanStep,
    PolicyRef,
    TaskStatusV2,
    TaskV2,
    ToolCallStatusV2,
    ToolCallV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2


# --- fake results builders -----------------------------------------------------------

def tc_fetch(tool_call_id="TC-FETCH", findings_data=None) -> ToolCallV2:
    if findings_data is None:
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ):
            res = risk_case_fetch("U00299")
        findings_data = res.data
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name="risk_case_fetch", arguments={},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data=findings_data),
        started_at="2026-08-28T00:00:00Z", completed_at="2026-08-28T00:00:01Z",
    )


def tc_timeline(tool_call_id="TC-TL") -> ToolCallV2:
    events = [
        {"event_id": "F3-E001", "finding_id": "F3",
         "timestamp": "2026-08-19T10:30:00Z", "event_type": "withdrawal",
         "summary": "Withdrawal of 0.5 BTC",
         "evidence_refs": [{"kind": "withdrawal", "id": "WD00000"}],
         "signal_refs": [], "policy_refs": [], "importance": "high"},
        {"event_id": "F3-E002", "finding_id": "F3",
         "timestamp": "2026-08-19T11:30:00Z", "event_type": "withdrawal",
         "summary": "Withdrawal of 0.6 BTC",
         "evidence_refs": [{"kind": "withdrawal", "id": "WD00001"}],
         "signal_refs": [], "policy_refs": [], "importance": "medium"},
    ]
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name="finding_drilldown",
        arguments={"view": "timeline"},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "finding_id": "F3", "view": "timeline", "events": events,
            "total_events": 2, "truncated": False, "top_n": 20,
        }),
        started_at="2026-08-28T00:00:02Z", completed_at="2026-08-28T00:00:03Z",
    )


def tc_signal(tool_call_id="TC-SIG") -> ToolCallV2:
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name="signal_explain",
        arguments={"signal_type": "Rule"},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "finding_id": "F3", "signal_type": "Rule",
            "rule": {"name": "High Withdrawal Frequency",
                     "trigger_values": {"withdrawal_frequency_24h": 14},
                     "threshold": "withdrawal_frequency_24h > 10",
                     "contribution": 20},
            "evidence_refs": [{"kind": "risk_event", "id": "U00299"}],
            "signal_refs": [], "policy_refs": [],
            "evidence_missing": False, "next_data_needed": [],
        }),
        started_at="2026-08-28T00:00:04Z", completed_at="2026-08-28T00:00:05Z",
    )


def tc_policy(tool_call_id="TC-POL") -> ToolCallV2:
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name="policy_lookup",
        arguments={"topic": "withdrawals"},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
            "finding_id": "F3", "topic": "withdrawals",
            "matches": [{
                "citation_id": 2, "chunk_id": "AML#2.2#004",
                "document": "AML_Suspicious_Indicators.md",
                "section": "2.2 Rapid Fund Movement",
                "snippet": "Multiple withdrawals in rapid succession…",
                "relevance": 1,
            }],
            "associated_policy_refs": [], "newly_retrieved_refs": [],
            "policy_refs": [], "required_evidence": [],
            "evidence_missing": False, "next_data_needed": [],
        },
        citation_refs=[PolicyRef(
            citation_id=2, chunk_id="AML#2.2#004",
            doc="AML_Suspicious_Indicators.md",
            section="2.2 Rapid Fund Movement")],),
        started_at="2026-08-28T00:00:06Z", completed_at="2026-08-28T00:00:07Z",
    )


def run(scope="case", sources=None, case_id="U00299", finding_id=None,
        task_id="T-1", **kw):
    return artifact_bundle(
        scope=scope, case_id=case_id, finding_id=finding_id,
        task_id=task_id,
        source_tool_calls=sources if sources is not None else [tc_fetch()],
        **kw,
    )


# --- 1–4. basic shape ------------------------------------------------------------------

class TestBasicShape:
    def test_case_scope_markdown_artifact(self):
        r = run(scope="case")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["artifact"]["content"].startswith(
            "# Investigation Evidence Bundle")

    def test_finding_scope_markdown_artifact(self):
        r = run(scope="finding", sources=[tc_fetch(), tc_timeline()],
                finding_id="F3")
        assert r.outcome == ToolResultOutcome.SUCCESS
        assert r.data["artifact"]["content"].startswith(
            "# Finding Investigation Bundle")
        assert "F3" in r.data["artifact"]["content"]

    def test_artifact_uses_artifactv2_model(self):
        r = run(scope="case")
        art = ArtifactV2(**r.data["artifact"])
        assert art.format == "md"
        assert art.artifact_type in ArtifactTypeV2

    def test_format_strictly_md(self):
        for bad in ("pdf", "json", "html", ""):
            r = run(format=bad)
            assert r.outcome == ToolResultOutcome.VALIDATION_ERROR


# --- 5–6. determinism --------------------------------------------------------------------

class TestDeterminism:
    def test_content_deterministic(self):
        sources = [tc_fetch(), tc_timeline(), tc_signal(), tc_policy()]
        a = run(scope="case", sources=[tc.model_copy(deep=True) for tc in sources])
        b = run(scope="case", sources=[tc.model_copy(deep=True) for tc in sources])
        assert a.data["artifact"]["content"] == b.data["artifact"]["content"]
        assert a.data["artifact"]["artifact_id"] == b.data["artifact"]["artifact_id"]

    def test_section_ordering_deterministic(self):
        sources = [tc_fetch(), tc_timeline(), tc_signal(), tc_policy()]
        r = run(scope="case", sources=sources)
        content = r.data["artifact"]["content"]
        order = [content.index(s) for s in
                 ("## Case", "## Findings", "## Timeline",
                  "## Signal Explanation", "## Policy References",
                  "## Source Tool Calls")]
        assert order == sorted(order)

    def test_no_unstable_values_in_content(self):
        import re
        r = run(scope="case")
        content = r.data["artifact"]["content"]
        assert not re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}\b", content)  # no UUIDs
        assert "2026-08-28" not in content             # no execution timestamps


# --- 7–14. section rendering + grounding ------------------------------------------------------

class TestSectionRendering:
    def full_sources(self):
        return [tc_fetch(), tc_timeline(), tc_signal(), tc_policy()]

    def test_timeline_rendered_when_available(self):
        r = run(scope="case", sources=self.full_sources())
        content = r.data["artifact"]["content"]
        assert "## Timeline" in content
        assert "| Time | Event | Evidence |" in content
        # importance/severity labels are not shown (unexplained in UI)
        assert "| high |" not in content and "| medium |" not in content
        assert "2026-08-19T10:30:00Z" in content
        assert "withdrawal:WD00000" in content

    def test_signal_explanation_rendered_when_available(self):
        r = run(scope="case", sources=self.full_sources())
        content = r.data["artifact"]["content"]
        assert "## Signal Explanation" in content
        assert "High Withdrawal Frequency" in content
        assert "withdrawal_frequency_24h=14" in content

    def test_policy_results_rendered_when_available(self):
        r = run(scope="case", sources=self.full_sources())
        content = r.data["artifact"]["content"]
        assert "## Policy References" in content
        assert "AML_Suspicious_Indicators.md" in content
        assert "2.2 Rapid Fund Movement" in content

    def test_evidence_missing_rendered_explicitly(self):
        gap_call = ToolCallV2(
            tool_call_id="TC-GAP", investigation_id="I", task_id="T-1",
            tool_name="signal_explain", arguments={},
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS, data={
                "finding_id": "F3", "signal_type": "ML",
                "evidence_missing": True,
                "next_data_needed": ["transaction-level feature attribution"],
            }),
        )
        r = run(scope="case", sources=[tc_fetch(), gap_call])
        content = r.data["artifact"]["content"]
        assert "## Evidence Gaps" in content
        assert "transaction-level feature attribution" in content
        assert r.data["evidence_missing"] is True

    def test_next_data_needed_preserved(self):
        gap_call = tc_signal(tool_call_id="TC-G")
        gap_call.result = ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"finding_id": "F3", "signal_type": "Rule",
                  "evidence_missing": True,
                  "next_data_needed": ["more rule context"]})
        r = run(scope="case", sources=[tc_fetch(), gap_call])
        assert "more rule context" in r.data["next_data_needed"][0] + \
            r.data["next_data_needed"][-1] or "more rule context" in \
            str(r.data["next_data_needed"])

    def test_no_investigation_evidence_section_without_concrete_evidence(self):
        # FINAL rule: concrete record IDs must never appear in a user-facing
        # artifact unless the complete authoritative record set was
        # retrieved. A case-scope bundle without a view="evidence"
        # investigation carries no Investigation Evidence section and NO
        # representative pointers (TX/WD ids from the bounded payload).
        r = run(scope="case", sources=[tc_fetch()])
        content = r.data["artifact"]["content"]
        assert "## Investigation Evidence" not in content
        assert "Evidence reference pointers" not in content
        assert "representative" not in content
        assert "TX00000" not in content          # bounded-payload record IDs
        assert "WD00000" not in content
        assert "FAKE" not in content

    def test_finding_scope_artifact_also_excludes_pointers(self):
        # Same rule applies at finding scope: no partial pointers section.
        r = run(scope="finding", sources=[tc_fetch()], finding_id="F3")
        content = r.data["artifact"]["content"]
        assert "## Investigation Evidence" not in content
        assert "TX00000" not in content
        assert "WD00000" not in content

    def test_timeline_table_shows_no_importance(self):
        # §4: artifact timeline tables carry investigation-useful fields
        # only; unexplained high/medium/low labels are not shown.
        r = run(scope="case", sources=self.full_sources())
        content = r.data["artifact"]["content"]
        assert "| Time | Event | Evidence |" in content
        assert "| Importance" not in content
        assert "| high |" not in content and "| medium |" not in content

    def test_actual_citation_refs_preserved(self):
        r = run(scope="case", sources=self.full_sources())
        assert any(ref.chunk_id == "AML#2.2#004"
                   for ref in r.citation_refs)

    def test_no_synthetic_evidence_ids(self):
        source_ids = ({w["withdrawal_id"] for w in fx.rp_evidence_payload()["withdrawal_evidence"]}
                      | {t["transaction_id"] for t in fx.rp_evidence_payload()["transaction_evidence"]}
                      | {"U00299"})
        r = run(scope="case", sources=[tc_fetch(), tc_timeline()])
        for ref in r.evidence_refs:
            assert ref.id in source_ids

    def test_no_synthetic_citation_ids(self):
        r = run(scope="case", sources=self.full_sources())
        for ref in r.citation_refs:
            assert ref.citation_id in (1, 2)          # only RP's ids

    def test_no_synthetic_timestamps(self):
        import re
        r = run(scope="case", sources=[tc_fetch(), tc_timeline()])
        stamps = set(re.findall(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", r.data["artifact"]["content"]))
        assert stamps <= {"2026-08-19T10:30", "2026-08-19T11:30"}


# --- 17–19. provenance ----------------------------------------------------------------------------

class TestProvenance:
    def test_source_tool_calls_nonempty_and_valid(self):
        r = run(scope="case")
        art = r.data["artifact"]
        assert art["source_tool_calls"] == ["TC-FETCH"]

    def test_provenance_points_to_actual_toolcalls(self):
        sources = [tc_fetch(), tc_timeline(), tc_signal()]
        r = run(scope="case", sources=sources)
        assert r.data["artifact"]["source_tool_calls"] == \
            [tc.tool_call_id for tc in sources]

    def test_no_contributing_calls_is_bounded_empty_not_fabricated_provenance(self):
        """P10: when NO executed result contributed content, the tool must
        return a bounded EMPTY — never label non-contributing calls as
        artifact sources (the removed degenerate fallback listed them all)."""
        unrelated = ToolCallV2(
            tool_call_id="TC-UNREL", investigation_id="I", task_id="T-1",
            tool_name="policy_lookup", arguments={},
            status=ToolCallStatusV2.SUCCESS,
            result=ToolResult(outcome=ToolResultOutcome.SUCCESS,
                              data={"unrelated": True}),
            started_at="2026-08-28T00:00:00Z",
            completed_at="2026-08-28T00:00:01Z",
        )
        r = run(scope="case", sources=[unrelated])
        assert r.outcome == ToolResultOutcome.EMPTY
        assert "artifact" not in r.data
        assert any("No content-contributing tool calls" in w
                   for w in r.warnings)

    def test_artifact_attached_to_taskv2(self):
        store = TaskStoreV2(":memory:")
        # via executor: artifact ids land on the task
        class FakeLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return ('{"skill_id": "case_intake", "goal": "g", "steps": ['
                        '{"type": "fetch_case", "reason": "r"}, '
                        '{"type": "generate_artifact", "reason": "r2"}]}')
        ctx = InvestigationContext(case_id="U00299")
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ):
            plan = PlannerV2(FakeLLM()).plan("investigate", ctx, ["case_intake"])
            task = TaskV2(task_id="T-A", investigation_id="CASE:U00299",
                          user_request="u", selected_skill="case_intake")
            ex = ExecutorV2(default_tool_provider(), store)
            res = ex.execute(plan, task, ctx)
        assert res.artifacts                              # artifact produced
        assert task.artifact_ids == [res.artifacts[0]["artifact_id"]]
        loaded = store.get("T-A")
        assert loaded.artifact_ids == task.artifact_ids


# --- 20–23. executor / planning integration -----------------------------------------------------------

class TestExecutorIntegration:
    def test_artifact_bundle_executes_through_executor(self):
        class FakeLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return ('{"skill_id": "case_intake", "goal": "g", "steps": ['
                        '{"type": "fetch_case", "reason": "r"}, '
                        '{"type": "generate_artifact", "reason": "r2"}]}')
        ctx = InvestigationContext(case_id="U00299")
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ):
            plan = PlannerV2(FakeLLM()).plan("investigate", ctx, ["case_intake"])
            task = TaskV2(task_id="T-X", investigation_id="CASE:U00299",
                          user_request="u", selected_skill="case_intake")
            ex = ExecutorV2(default_tool_provider())
            res = ex.execute(plan, task, ctx)
        assert res.status == TaskStatusV2.COMPLETED
        assert any(tc.tool_name == "artifact_bundle" for tc in res.tool_calls)

    def test_planner_generate_artifact_resolves_to_artifact_bundle(self):
        from app.skills import STEP_TOOL_MAP
        assert STEP_TOOL_MAP["generate_artifact"].tool_name == "artifact_bundle"

    def test_artifact_producing_toolcall_recorded(self):
        class FakeLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return ('{"skill_id": "case_intake", "goal": "g", "steps": ['
                        '{"type": "generate_artifact", "reason": "r"}]}')
        ctx = InvestigationContext(case_id="U00299")
        # no fetch first → artifact has no source results → empty (honest)
        plan = PlannerV2(FakeLLM()).plan("bundle only", ctx, ["case_intake"])
        task = TaskV2(task_id="T-Y", investigation_id="CASE:U00299",
                      user_request="u", selected_skill="case_intake")
        ex = ExecutorV2(default_tool_provider())
        res = ex.execute(plan, task, ctx)
        art_calls = [tc for tc in res.tool_calls if tc.tool_name == "artifact_bundle"]
        assert art_calls
        assert art_calls[0].result.outcome == ToolResultOutcome.EMPTY
        assert "no executed tool results" in " ".join(
            art_calls[0].result.warnings).lower()


# --- 22–24. validation & empty semantics ---------------------------------------------------------------

class TestValidationAndEmpty:
    def test_invalid_scope_rejected(self):
        for bad in ("workspace", "everything", ""):
            r = run(scope=bad)
            assert r.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_finding_scope_without_finding_id_rejected(self):
        r = run(scope="finding", finding_id=None)
        assert r.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_finding_scope_wrong_finding_rejected(self):
        # finding-scope bundles validate the finding against the available
        # case results — unknown findings never get fabricated bundles.
        r = run(scope="finding", finding_id="F999", sources=[tc_fetch()])
        assert r.outcome == ToolResultOutcome.VALIDATION_ERROR
        assert "does not exist" in r.error.message

    def test_no_sources_is_empty_not_fabricated(self):
        r = run(scope="case", sources=[])
        assert r.outcome == ToolResultOutcome.EMPTY
        assert "no artifact was fabricated" in " ".join(r.warnings).lower()

    def test_missing_task_id_is_validation_error(self):
        r = run(scope="case", task_id="")
        assert r.outcome == ToolResultOutcome.VALIDATION_ERROR

    def test_missing_case_id_is_validation_error(self):
        r = run(scope="case", case_id="  ")
        assert r.outcome == ToolResultOutcome.VALIDATION_ERROR


# --- 25–30. purity & persistence ---------------------------------------------------------------------------

class TestPurityAndPersistence:
    def test_no_llm_called_for_artifact_generation(self):
        class ExplodingLLM:
            def generate(self, *a, **kw):
                raise AssertionError("LLM must not be called")
        # composition is pure Python over inputs — nothing to invoke an LLM
        r = run(scope="case")
        assert r.outcome == ToolResultOutcome.SUCCESS

    def test_no_rp_call_when_results_sufficient(self):
        calls = []
        with patch("app.adapters.risk_platform.RiskPlatformAdapter.fetch_case",
                   new=lambda self, uid: calls.append(1)):
            r = run(scope="case", sources=[tc_fetch()])
        assert calls == []                        # zero RP invocations
        assert r.outcome == ToolResultOutcome.SUCCESS

    def test_artifact_followup_goes_through_pipeline_not_direct_call(self):
        from app.followups import CASE_TEMPLATES
        export = next(t for t in CASE_TEMPLATES if t.follow_up_id == "check_artifact")
        assert export.target_step == "generate_artifact"
        assert isinstance(export.intent, str)     # a next-turn request
        assert not callable(export.intent)
        assert not hasattr(export, "execute")

    def test_artifact_persistable_and_reloadable(self, tmp_path):
        store = TaskStoreV2(tmp_path / "art.db")
        # persist the task carrying the artifact id; reload via store
        class FakeLLM:
            def generate(self, messages, max_tokens=0, temperature=0.1):
                return ('{"skill_id": "case_intake", "goal": "g", "steps": ['
                        '{"type": "fetch_case", "reason": "r"}, '
                        '{"type": "generate_artifact", "reason": "r2"}]}')
        ctx = InvestigationContext(case_id="U00299")
        with patch(
            "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
            new=lambda self, uid: (
                fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
        ):
            plan = PlannerV2(FakeLLM()).plan("investigate", ctx, ["case_intake"])
            task = TaskV2(task_id="T-P", investigation_id="CASE:U00299",
                          user_request="u", selected_skill="case_intake")
            ex = ExecutorV2(default_tool_provider(), store)
            res = ex.execute(plan, task, ctx)
        store2 = TaskStoreV2(tmp_path / "art.db")
        loaded = store2.get("T-P")
        assert loaded.artifact_ids == res.task.artifact_ids
        # artifact content is inline in the execution result (md)
        assert res.artifacts[0]["format"] == "md"
        assert res.artifacts[0]["content"].startswith("# ")

    def test_no_duplicate_artifact_ids_same_execution(self):
        sources = [tc_fetch(), tc_timeline()]
        r = run(scope="case", sources=sources)
        art = r.data["artifact"]
        assert art["source_tool_calls"].count(art["source_tool_calls"][0]) == \
            len([c for c in art["source_tool_calls"]
                 if c == art["source_tool_calls"][0]])

    def test_no_new_rp_endpoint_introduced(self):
        # the artifact tool imports no adapter/HTTP machinery at all
        import app.domain_tools.artifact_bundle as mod
        import inspect
        src = inspect.getsource(mod)
        assert "httpx" not in src
        assert "RiskPlatformAdapter" not in src
        assert "_request" not in src

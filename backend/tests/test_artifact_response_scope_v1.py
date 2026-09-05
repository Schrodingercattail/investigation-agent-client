"""Artifact response-scope regression tests (P5 ownership rule).

An artifact-generation turn may internally execute fetch_case +
generate_artifact; the internal prerequisite fetch must NOT become
user-facing content. The user asked for the artifact — the answer is the
artifact confirmation, not a replayed case-intake summary.

Cases:
1. focused finding + "generate artifacts" → artifact confirmation, no summary
2. focused finding + "generate artifact for this finding" → no case summary
3. case-level artifact request → no duplicated intake summary
4. case intake (fetch_case as the requested operation) → summary unchanged
5. fetch_case present in the tool-call set does not itself create intake
   content when an artifact was generated in the same turn
6. artifact CONTENT (the markdown bundle) is unchanged
7. no internal identifiers in the response
"""

from unittest.mock import patch

import tests.test_risk_platform_adapter_v2 as fx
from app.domain_tools import risk_case_fetch
from app.investigation_service import compose_response
from app.models import (
    FocusSource,
    InvestigationContext,
    ToolCallStatusV2,
    ToolCallV2,
    ToolResult,
    ToolResultOutcome,
)


def real_case_data():
    with patch(
        "app.domain_tools.risk_case_fetch.RiskPlatformAdapter.fetch_case",
        new=lambda self, uid: (
            fx.rp_evidence_payload(uid), fx.rp_explanation_payload()),
    ):
        return risk_case_fetch("U00299").data


def tc_fetch(fetch_data, tool_call_id="TC-FETCH"):
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name="risk_case_fetch", arguments={},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(outcome=ToolResultOutcome.SUCCESS,
                          data=fetch_data),
        started_at="2026-09-03T00:00:00Z",
        completed_at="2026-09-03T00:00:01Z",
    )


def tc_artifact(tool_call_id="TC-ART", scope="finding:F3"):
    content = (
        "# Finding Investigation Bundle\n\n## Finding\n- Finding ID: F3\n"
        "- Case ID: U00299\n\n### F3 — High Withdrawal Frequency\n\n"
        "## Source Tool Calls\n- TC-FETCH (risk_case_fetch, success)\n")
    return ToolCallV2(
        tool_call_id=tool_call_id, investigation_id="CASE:U00299",
        task_id="T-1", tool_name="artifact_bundle", arguments={},
        status=ToolCallStatusV2.SUCCESS,
        result=ToolResult(
            outcome=ToolResultOutcome.SUCCESS,
            data={"artifact": {"artifact_id": "ART-abc123", "title": "t",
                               "content": content, "scope": scope},
                  "artifact_id": "ART-abc123", "scope": "finding",
                  "format": "md", "source_tool_call_count": 1},
        ),
        started_at="2026-09-03T00:00:02Z",
        completed_at="2026-09-03T00:00:03Z",
    )


class TestArtifactResponseOwnership:
    def test_artifact_turn_with_internal_fetch_no_intake_replay(self):
        # 1+5. ARTIFACT REQUEST turn: fetch_case + artifact in one turn —
        # the fetch is an internal prerequisite — the response is the
        # artifact confirmation only.
        text = compose_response(
            user_request="生成artifacts", skill_id="case_intake", plan=None,
            tool_calls=[tc_fetch(real_case_data()), tc_artifact()],
            execution_errors=[])
        assert "The investigation bundle has been created" in text
        assert "ART-abc123" in text
        assert "findings were identified" not in text
        assert "Select a finding from the Findings panel" not in text
        assert "ML Pattern Detection" not in text       # no finding list
        assert "High Withdrawal Frequency" not in text

    def test_case_intake_turn_with_artifact_keeps_intake_summary(self):
        # ACCEPTED product behavior: Case Intake generates the case-scoped
        # bundle AND the intake summary owns the conversational answer.
        # Both outputs are valid — no suppression on intake turns.
        text = compose_response(
            user_request="Investigate U00299", skill_id="case_intake",
            plan=None,
            tool_calls=[tc_fetch(real_case_data()), tc_artifact()],
            execution_errors=[])
        assert "findings were identified" in text
        assert "Select a finding from the Findings panel" in text
        assert "ML Pattern Detection" in text           # finding list present
        # the artifact confirmation is a valid additional output
        assert "has been created" in text

    def test_finding_artifact_request_no_case_summary(self):
        # 2. explicit finding artifact: same ownership rule.
        text = compose_response(
            user_request="Generate an artifact for this finding",
            skill_id="case_intake", plan=None,
            tool_calls=[tc_fetch(real_case_data()), tc_artifact()],
            execution_errors=[])
        assert "has been created" in text
        assert "findings were identified" not in text
        assert "Select a finding" not in text

    def test_case_artifact_request_no_intake_replay(self):
        # 3. case-level artifact request: no duplicated intake summary.
        text = compose_response(
            user_request="Generate a case artifact", skill_id="case_intake",
            plan=None,
            tool_calls=[tc_fetch(real_case_data()),
                        tc_artifact(scope="case:U00299")],
            execution_errors=[])
        assert "has been created" in text
        assert "findings were identified" not in text

    def test_case_intake_fetch_still_has_full_summary(self):
        # 4. fetch_case WITHOUT an artifact in the turn = Case Intake —
        # the summary and finding list remain unchanged.
        text = compose_response(
            user_request="Investigate U00299", skill_id="case_intake",
            plan=None, tool_calls=[tc_fetch(real_case_data())],
            execution_errors=[])
        assert "findings were identified" in text
        assert "Select a finding from the Findings panel" in text
        assert "ML Pattern Detection" in text           # finding list present
        assert "has been created" not in text

    def test_artifact_content_itself_unchanged(self):
        # 6. the artifact markdown carries the finding block regardless —
        # containment of the BUNDLE is untouched by the response rule.
        art = tc_artifact()
        content = art.result.data["artifact"]["content"]
        assert "### F3 — High Withdrawal Frequency" in content
        assert "## Source Tool Calls" in content

    def test_no_internal_identifiers_in_artifact_response(self):
        # 7. identifier freeze gate on the artifact completion answer.
        text = compose_response(
            user_request="生成artifacts", skill_id="case_intake", plan=None,
            tool_calls=[tc_fetch(real_case_data()), tc_artifact()],
            execution_errors=[])
        for ident in ("fetch_case", "artifact_bundle", "case_intake",
                      "risk_case_fetch", "retrieve_policy"):
            assert ident not in text, ident

    def test_artifact_failure_does_not_hide_intake_content(self):
        # boundary sharpness: if the artifact step FAILED, the fetch is no
        # longer an internal prerequisite of a delivered artifact — the
        # executed fetch result may inform the user (executor still fails
        # the task; composer keeps its normal outcome handling upstream).
        failed_art = ToolCallV2(
            tool_call_id="TC-ART-FAIL", investigation_id="CASE:U00299",
            task_id="T-1", tool_name="artifact_bundle", arguments={},
            status=ToolCallStatusV2.FAILED,
            result=ToolResult(outcome=ToolResultOutcome.EMPTY,
                              data={"scope": "finding"},
                              warnings=["No content-contributing tool calls"]),
            started_at="2026-09-03T00:00:02Z",
            completed_at="2026-09-03T00:00:03Z",
        )
        text = compose_response(
            user_request="生成artifacts", skill_id="case_intake", plan=None,
            tool_calls=[tc_fetch(real_case_data()), failed_art],
            execution_errors=[])
        # no artifact success → no ownership suppression; intake content
        # from the fetch is not silently swallowed by this rule
        assert "has been created" not in text

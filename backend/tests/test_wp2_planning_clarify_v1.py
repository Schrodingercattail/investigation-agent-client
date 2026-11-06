"""R2.2.3 WP-2 Version B — planner/loop contract after the Version A
gate retirement (Final Ruling 7, S3).

Planner-level tests: under an HSG decision the planner produces the
NORMAL artifact delivery plan — per-target admission happens downstream
in the loop (app.delivery_admission), so the planner no longer refuses.
Loop-level test (REAL _run_loop admission wiring + clarification
terminal via the session goal seam; executor spy proves ZERO tool
executions when ALL targets fail admission).

The admission fail-closed matrix itself is pinned in
test_wp2_b_admission_service_v1.py / test_wp2_b_tool_verification_v1.py.
"""

import json

import pytest

from app.agent_loop import AgentLoopRunner, AgentStatus, StopReason
from app.config import settings
from app.context_resolution import InvestigationContext
from app.models import FindingCapability
from app.planner_v2 import PlannerV2, PlanningFailure
from app.task_store_v2 import TaskStoreV2

from eval.fixtures import ScriptedLLM, build_context, default_findings

CASE = "U00010"
HSG = {"outcome": "heuristic_single_goal"}
ACCEPTED = {"outcome": "contract_accepted"}
LLM_EMPTY = {"outcome": "llm_empty"}
NO_PROPOSAL = {"outcome": "no_proposal"}
INVALID = {"outcome": "invalid_proposal:subject_mismatch"}

ARTIFACT_ASK = "generate all trade records of this account as an artifact"
TIMELINE_ASK = "show me the timeline for this finding"

ART_PLAN = json.dumps({
    "skill_id": "trade_detail", "goal": "eval",
    "steps": [{"type": "fetch_case", "reason": "r"},
              {"type": "generate_artifact", "reason": "r"}]})
TIMELINE_PLAN = json.dumps({
    "skill_id": "timeline_investigation", "goal": "eval",
    "steps": [{"type": "inspect_timeline", "reason": "r"}]})


def make_planner(scripts):
    return PlannerV2(ScriptedLLM(scripts))


def make_ctx():
    return build_context(case_id=CASE)


class TestPlannerAdmission:
    def test_hsg_artifact_branch_plans_normally_admission_downstream(self):
        """Version B (Final Ruling 7, S3): the Version A unconditional
        HSG planner gate is RETIRED — under an HSG decision the planner
        produces the normal artifact delivery plan; per-target admission
        runs downstream in the loop."""
        planner = make_planner([ART_PLAN] * 3)
        res = planner.plan(ARTIFACT_ASK, make_ctx(), ["case_intake"])
        assert hasattr(res, "steps")
        assert [s.type for s in res.steps] == ["fetch_case",
                                               "generate_artifact"]

    def test_accepted_decision_plans_artifact_normally(self):
        """contract_accepted + ARTIFACT intent → the normal artifact
        delivery plan (fetch_case + generate_artifact), unchanged."""
        planner = make_planner([ART_PLAN] * 3)
        res = planner.plan(ARTIFACT_ASK, make_ctx(), ["case_intake"])
        assert hasattr(res, "steps")
        assert [s.type for s in res.steps] == ["fetch_case",
                                               "generate_artifact"]

    def test_llm_empty_decision_not_hijacked(self):
        """llm_empty (GU LLM empty family, out of WP-2 scope) keeps its
        existing deterministic planning behavior."""
        planner = make_planner([ART_PLAN] * 3)
        res = planner.plan(ARTIFACT_ASK, make_ctx(), ["case_intake"])
        assert hasattr(res, "steps")
        assert [s.type for s in res.steps] == ["fetch_case",
                                               "generate_artifact"]

    def test_no_proposal_and_invalid_not_hijacked(self):
        planner = make_planner([ART_PLAN] * 3)
        for dec in (NO_PROPOSAL, INVALID):
            res = planner.plan(ARTIFACT_ASK, make_ctx(), ["case_intake"])
            assert hasattr(res, "steps")

    def test_read_only_intent_not_hijacked(self):
        """Non-ARTIFACT (timeline) intent under HSG → the timeline branch
        plans normally (read-only investigation is not gated by WP-2)."""
        planner = make_planner([TIMELINE_PLAN] * 3)
        caps = FindingCapability(root=frozenset({"timeline"}))
        res = planner.plan(TIMELINE_ASK,
                           build_context("F1", CASE),
                           ["timeline_investigation"],
                           finding_capabilities=caps)
        assert hasattr(res, "steps")
        assert all(s.type == "inspect_timeline" for s in res.steps)




# ---------------------------------------------------------------------------
# Loop-level component test: the PLANNING_CLARIFICATION_NEEDED failure is
# consumed by the run loop as the clarification terminal BEFORE any tool
# execution (session goal seam pins the HSG understanding; the executor
# spy proves ZERO tool executions at planning time).
# ---------------------------------------------------------------------------

class TestLoopIntegration:
    # D3 repair: language-pinned per turn — the served clarify text MUST
    # be the turn's language and MUST NOT contain the other language's
    # distinctive phrase. (The old bilingual OR made the D1 inversion
    # undetectable.) D4: the flag is pinned explicitly — the recorded
    # heuristic_single_goal decision only exists under the CG flag, and
    # the test must not depend on the ambient backend/.env (cwd-relative
    # env_file load).
    @pytest.mark.parametrize(
        "lang, marker, anti_marker", [
            ("zh", "请补充说明交付的具体范围",
             "delivery-generation (artifact) goal"),
            ("en", "Please clarify the delivery scope",
             "请补充说明交付的具体范围"),
        ])
    def test_hsg_ask_clarifies_before_any_execution(
            self, tmp_path, monkeypatch, lang, marker, anti_marker):
        import json as _json
        import tests.test_delivery_gate_g7_v1 as g7
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT",
                            True)
        provider_calls = []

        class SpyProvider:
            def has(self, tool_name):
                return True

            def get(self, tool_name):
                def invoke(args):
                    provider_calls.append((tool_name, dict(args)))
                    from app.models import ToolResult, ToolResultOutcome
                    return ToolResult(outcome=ToolResultOutcome.SUCCESS,
                                      data={"findings": []})
                return invoke

        session = {"goal": {
            "case_id": CASE,
            "goal_type": "artifact_export",
            "goal_resolution": "INFERRED",
            "subject_type": "case",
            "subject_ref": CASE,
            "ambiguous_goal_types": [],
            "reason": "scripted GU fallback (heuristic_single_goal)",
            "source": "heuristic",
            "request_text": ARTIFACT_ASK,
        }}
        path = tmp_path / "wp2_session.json"
        path.write_text(_json.dumps(session, ensure_ascii=False),
                        encoding="utf-8")
        monkeypatch.setenv("AGENT_LOOP_SCRIPTED_SESSION_FILE", str(path))

        provider = g7.ScriptedToolProvider([g7.fetch_success()])
        store = TaskStoreV2(db_path=tmp_path / "wp2.sqlite3")
        runner = AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM([ART_PLAN] * 3)),
            executor=ExecutorV2(provider), store=store)
        result = runner.run(ARTIFACT_ASK, build_context(case_id=CASE),
                            default_findings(),
                            investigation_id="INV-WP2L",
                            task_id=f"TASK-WP2L-{lang}",
                            findings_provider=lambda: default_findings(),
                            response_language=lang)
        st = result.state
        assert st.agent_status == AgentStatus.BLOCKED
        assert st.stop_reason == StopReason.CLARIFICATION_NEEDED
        assert st.goal_outcome == "partial"
        # ZERO tool executions — planning-time clarification
        assert provider_calls == []
        assert st.observations == []
        assert st.artifact_ids == []
        served = result.response
        # D3 language-pinned assertion: the turn's language marker MUST
        # be present and the other language's distinctive phrase MUST
        # be absent.
        assert marker in served
        assert anti_marker not in served


from app.executor_v2 import ExecutorV2  # noqa: E402

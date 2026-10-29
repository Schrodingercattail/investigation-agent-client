"""AMBIGUOUS clarification enumerates the candidate goal readings.

Real journey (INV-8cc8eb8121ee, U00005, user-selected F1 focus): the
input "ML都检测什么" resolved AMBIGUOUS (explain_finding vs policy_check)
but the blocked message was generic — the user never learned WHICH
readings existed. Fix: the clarification message enumerates the
structured `ambiguous_goal_types` (closed vocabulary, bounded label
map). The LLM's free-text reason is never projected; any missing or
unmappable candidate falls back to the generic text byte-for-byte;
non-AMBIGUOUS paths and the clarification semantics (BLOCKED /
clarification_needed / zero tool execution) are unchanged.
"""

from app.agent_loop import (
    AgentLoopState,
    StopReason,
    _ambiguous_clarification_message,
)

GENERIC_ZH = ("无法确定您要调查的目标：该请求存在多种可能的理解，"
              "系统不会默认只调查其中一项。请明确您希望优先调查的目标后"
              "重新发起。")
GENERIC_EN = ("The investigation target cannot be determined: "
              "this request has several plausible readings and "
              "the system will not pick one by default. Please "
              "clarify which target to investigate and retry.")


def _state(gr):
    return AgentLoopState(
        task_id="T", investigation_id="INV-AC", case_id="U00005",
        goal="ML都检测什么", goal_representation=gr)


def _gr(candidates, resolution="AMBIGUOUS", reason=None):
    gr = {"goal_resolution": resolution,
          "ambiguous_goal_types": list(candidates)}
    if reason:
        gr["reason"] = reason
    return gr


class TestKnownCandidatesEnumerated:
    def test_both_readings_listed_zh(self):
        msg = _ambiguous_clarification_message(
            _state(_gr(["explain_finding", "policy_check"])), "zh")
        assert "解释该发现的检测信号与得分" in msg
        assert "政策要求核查" in msg
        assert "1." in msg and "2." in msg
        # the framing (never-default) is preserved
        assert "不会默认只调查其中一项" in msg

    def test_en_face(self):
        msg = _ambiguous_clarification_message(
            _state(_gr(["explain_finding", "policy_check"])), "en")
        assert "explain the finding's detection signals and score" in msg
        assert "policy requirements check" in msg

    def test_internal_reason_never_projected(self):
        msg = _ambiguous_clarification_message(
            _state(_gr(["explain_finding", "policy_check"],
                       reason="'ML'既可理解为机器学习…equally plausible")),
            "zh")
        assert "equally plausible" not in msg
        assert "Money Laundering" not in msg

    def test_order_follows_the_structured_field(self):
        msg = _ambiguous_clarification_message(
            _state(_gr(["policy_check", "explain_finding"])), "zh")
        assert msg.index("政策要求核查") < msg.index("解释该发现的检测信号与得分")


class TestSafeFallback:
    def test_empty_candidates_fall_back_byte_identical(self):
        assert _ambiguous_clarification_message(
            _state(_gr([])), "zh") == GENERIC_ZH
        assert _ambiguous_clarification_message(
            _state(_gr([])), "en") == GENERIC_EN

    def test_unmappable_candidate_falls_back(self):
        msg = _ambiguous_clarification_message(
            _state(_gr(["explain_finding", "no_such_goal_type"])), "zh")
        assert msg == GENERIC_ZH

    def test_missing_representation_falls_back(self):
        assert _ambiguous_clarification_message(
            _state(None), "zh") == GENERIC_ZH

    def test_missing_field_falls_back(self):
        assert _ambiguous_clarification_message(
            _state({"goal_resolution": "AMBIGUOUS"}), "zh") == GENERIC_ZH


class TestSemanticsUnchanged:
    def test_loop_terminates_clarification_without_execution(self, monkeypatch):
        """Loop-level: the AMBIGUOUS decision still terminates BLOCKED /
        clarification_needed with zero planned steps and zero tool calls,
        now carrying the enumerated message."""
        from app.config import settings
        monkeypatch.setattr(settings, "ENABLE_GOAL_SEMANTIC_CONTRACT", True)
        from app.agent_loop import AgentLoopRunner
        from app.models import InvestigationContext
        from app.task_store_v2 import TaskStoreV2

        class _Resolver:
            def resolve(self, req, fid, case, diag=None):
                from app.goal_resolution import GoalRepresentation
                return GoalRepresentation(
                    case_id=case, goal_type=None,
                    goal_resolution="AMBIGUOUS", subject_type="finding",
                    subject_ref="F1", ambiguous_goal_types=[
                        "explain_finding", "policy_check"],
                    reason="two readings", source="llm",
                    request_text=req)

        resolver = _Resolver()

        import tempfile
        store = TaskStoreV2(db_path=tempfile.mkdtemp() + "/ac.sqlite3")
        runner = AgentLoopRunner(store=store)
        runner.goal_resolver = lambda req, fid, case, diag=None: \
            resolver.resolve(req, fid, case, diag)
        from app.planner_v2 import PlannerV2

        class _BoomPlanner:
            def plan(self, *a, **k):
                raise AssertionError("planner must never run on AMBIGUOUS")

        runner.planner = _BoomPlanner()
        runner.executor = None
        result = runner.run(
            "ML都检测什么", InvestigationContext(case_id="U00005"),
            [], investigation_id="INV-AC-L", task_id="TASK-AC-L",
            findings_provider=lambda: [])
        state = result.state
        assert state.agent_status == "blocked"
        assert state.stop_reason == "clarification_needed"
        assert state.plan_steps == [] and state.observations == []
        assert "解释该发现的检测信号与得分" in (state.response or "")
        assert "政策要求核查" in (state.response or "")

    def test_non_ambiguous_paths_untouched(self):
        """The generic text remains for the fallback shapes and the helper
        never alters non-clarification outcomes (covered by the fallback
        assertions above + the existing blocked-path suites)."""
        assert _ambiguous_clarification_message(
            _state(_gr(["summarize", "timeline"])), "zh") != GENERIC_ZH
        assert "案件情况总结" in _ambiguous_clarification_message(
            _state(_gr(["summarize", "timeline"])), "zh")

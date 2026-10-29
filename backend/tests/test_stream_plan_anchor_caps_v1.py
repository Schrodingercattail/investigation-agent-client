"""_artifact_stream_plan must not raise on its PRIMARY known_eligible path.

Real failure (U00004 manual acceptance, INV-0e2e493bba93): the request
"生成提现明细和交易明细的文件给我" crashed twice (TASK-445a2cc1516c /
TASK-b473c38db001, ~100ms each, persistent_error) with
UnboundLocalError: cannot access local variable 'anchor_caps' — the
variable is assigned only inside the anchor-backed FALLBACK branch, while
the primary known_eligible path skips that branch entirely and reached
check_plan (planner_v2.py:344) with the name unbound.

Fix: anchor_caps is initialized to None before the primary lookup; the
check_plan capabilities expression then resolves to finding_capabilities
on the primary path and keeps the fallback's anchor-union semantics.
"""

from app.domain_tools import risk_case_fetch
from app.models import InvestigationContext
from app.planner_v2 import PlannerV2
from app.skills import SKILLS

Q = "生成提现明细和交易明细的文件给我"


class _NoLLM:
    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        raise AssertionError("LLM must not be reached")


def _findings():
    return list((risk_case_fetch(case_id="U00004").data or {}).get("findings")
                or [])


def _stream_capable_skills():
    """Skills whose planning_steps serve both named streams — the shape
    that sends the function down its PRIMARY path."""
    return [sid for sid in SKILLS
            if all(st in SKILLS[sid].planning_steps
                   for st in ("inspect_withdrawals", "inspect_transactions",
                              "generate_artifact"))]


def _call(known_eligible, finding_capabilities=None, user_words=Q):
    planner = PlannerV2(_NoLLM())
    return planner._artifact_stream_plan(
        user_words=user_words, known_eligible=known_eligible,
        case_findings=_findings(),
        finding_capabilities=finding_capabilities,
        context=InvestigationContext(case_id="U00004"))


class TestPrimaryPathReachesCheckPlan:
    def test_primary_path_no_unbound_local_error(self):
        """The exact production crash shape: a serving skill exists in
        known_eligible → the fallback branch (the only anchor_caps
        assignment site) is skipped → the call must now REACH check_plan
        and return its verdict (pre-fix: UnboundLocalError before
        check_plan could run)."""
        serving = _stream_capable_skills()
        assert serving, "fixture expects a stream-capable skill"
        result = _call(serving)
        assert type(result).__name__ in ("Plan", "PlanningFailure")

    def test_primary_path_success_with_supporting_capabilities(self):
        """finding_capabilities covering the skill's requirement → the
        primary path produces the interleaved per-stream plan."""
        serving = _stream_capable_skills()
        skill = serving[0]
        required = set(SKILLS[skill].required_capabilities or [])
        plan = _call(serving, finding_capabilities=required or None)
        assert hasattr(plan, "steps"), plan
        types = [s.type for s in plan.steps]
        assert types == ["inspect_withdrawals", "generate_artifact",
                         "inspect_transactions", "generate_artifact"]
        w, t = plan.steps[0], plan.steps[2]
        assert w.arguments["finding_id"] and t.arguments["finding_id"]
        assert plan.steps[1].arguments["source_step_ids"] == [w.step_id]
        assert plan.steps[3].arguments["source_step_ids"] == [t.step_id]


class TestFallbackPathSemanticsPreserved:
    def test_fallback_path_still_produces_the_plan(self):
        """Empty known_eligible → the anchor-backed fallback assigns
        anchor_caps and the interleaved plan is still produced
        (fallback semantics byte-identical)."""
        plan = _call([])
        assert hasattr(plan, "steps")
        types = [s.type for s in plan.steps]
        assert types == ["inspect_withdrawals", "generate_artifact",
                         "inspect_transactions", "generate_artifact"]

    def test_no_stream_named_returns_none(self):
        result = _call([], user_words="把调查结果生成 artifacts")
        assert result is None

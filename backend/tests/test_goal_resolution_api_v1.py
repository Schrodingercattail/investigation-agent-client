"""Stage 2.1 — LEVEL 2 API/application-path integration tests.

Exercises the browser-shaped request through the REAL dependency graph:
POST /turn (real InvestigationService wired via InvestigationAPI) and
POST /goal (real bounded loop) → ContextResolver → Goal Representation.
A fake goal resolver is injected at the API layer so the SEMANTIC
contract (goal ≠ tool) is verified deterministically; the production
LLM-backed wiring is exercised in the real-browser journeys.
"""

import json
import time

from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner
from app.executor_v2 import ExecutorV2, ToolProvider
from app.goal_resolution import GoalRepresentation, GoalResolutionStatus
from app.investigation_service import InvestigationService
from app.api.investigations import InvestigationAPI
import app.api.investigations as inv_module
from app.models import ToolResult, ToolResultOutcome
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from eval.fixtures import ScriptedLLM, plan_json

CASE = "U00010"


class ScriptedProvider(ToolProvider):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.last = self.results[-1] if self.results else None

    def has(self, name):
        return True

    def get(self, name):
        def invoke(args):
            if self.results:
                self.last = self.results.pop(0)
            return self.last
        return invoke


class ScriptedProposer:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def propose(self, state):
        from app.agent_loop import DecisionProposal
        d = self.decisions.pop(0) if self.decisions else "STOP"
        return DecisionProposal(decision=d, reason="deterministic scenario",
                                source="heuristic")


def u00010_findings():
    from app.models import Finding, FindingCapability
    caps = ["timeline", "signal_explain", "policy_lookup"]
    f8 = Finding(finding_id="F8", case_id=CASE, type="rule_signal",
                 title="Opposite Trade Ratio.",
                 summary="An opposite-trade ratio of 34.38% was observed, "
                         "below the 40% threshold.",
                 capabilities=FindingCapability.model_validate(
                     caps + ["self_opposite_trades"]))
    f6 = Finding(finding_id="F6", case_id=CASE, type="graph_signal",
                 title="Linked Account Network.",
                 summary="linked accounts through shared devices",
                 capabilities=FindingCapability.model_validate(
                     caps + ["self_opposite_trades"]))
    return [f8, f6]


def _create(c):
    r = c.post("/api/v2/investigations", json={"case_reference": CASE})
    assert r.status_code == 200, r.text
    return r.json()["investigation"]["investigation_id"]


def make_goal_resolver(payload):
    """Static-payload goal resolver (the signature the loop expects)."""
    def resolve(request_text, focused_finding_id, case_id):
        return GoalRepresentation(
            case_id=case_id,
            goal_type=payload.get("goal_type"),
            goal_resolution=payload.get(
                "goal_resolution", GoalResolutionStatus.INFERRED.value),
            subject_type="finding" if focused_finding_id else "case",
            subject_ref=focused_finding_id or case_id,
            ambiguous_goal_types=payload.get("ambiguous_goal_types", []),
            reason=payload.get("reason"),
            source="llm",
            request_text=request_text[:400],
        )
    return resolve


def make_client(tmp_path, goal_payload, decisions=("STOP",),
                wire_service_goal=True):
    """InvestigationAPI with deterministic planner + Stage 2.1 goal
    resolver on the service (/turn) and on the loop runner (/goal). The
    scripted plan is case_intake/fetch_case — always eligible at case
    level, so the goal layer is exercised independently of focus."""
    store = TaskStoreV2(str(tmp_path / "goal_api.db"))
    service_kwargs = dict(
        planner=PlannerV2(ScriptedLLM(
            [plan_json("case_intake",
                       ["fetch_case", "generate_artifact"])])),
        executor=None, task_store=store)
    if wire_service_goal:
        service_kwargs["goal_resolver"] = make_goal_resolver(goal_payload)
    api = InvestigationAPI(
        service=InvestigationService(**service_kwargs),
        store=store,
        case_findings_provider=lambda case_id: u00010_findings(),
        loop_runner_factory=lambda s: AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM(
                [plan_json("case_intake", ["fetch_case"])])),
            executor=ExecutorV2(ScriptedProvider([ToolResult(
                outcome=ToolResultOutcome.SUCCESS,
                data={"case_id": CASE, "findings": [], "evidence_refs": [],
                      "signal_refs": [], "policy_refs": []})])),
            store=s,
            proposer=ScriptedProposer(list(decisions)),
            findings_provider=lambda: u00010_findings(),
            goal_resolver=make_goal_resolver(goal_payload),
        ),
    )
    inv_module.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def await_terminal(c, task_id, timeout_s=30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        a = c.get(f"/api/v2/tasks/{task_id}/activity")
        if a.status_code == 200 and a.json()["is_terminal"]:
            return a.json()
        time.sleep(0.05)
    raise AssertionError("loop did not terminate")


# ---------------------------------------------------------------------------
# Turn path: browser-shaped request → goal representation in the response
# ---------------------------------------------------------------------------

class TestTurnPathGoal:
    def test_turn_response_carries_structured_goal(self, tmp_path):
        c = make_client(tmp_path, goal_payload={
            "goal_type": "inspect_network_structure",
            "goal_resolution": "INFERRED",
        })
        inv = _create(c)
        t = c.post(f"/api/v2/investigations/{inv}/turn",
                   json={"message":
                         "这个账户的 Linked Account Network 具体是什么？"})
        assert t.status_code == 200
        goal = t.json()["goal"]
        # the goal is the SEMANTIC objective — never the tool id
        assert goal["goal_type"] == "inspect_network_structure"
        assert goal["goal_type"] != "cross_account_trade_analysis"
        assert goal["subject_ref"] == "F6"

    def test_turn_without_goal_wiring_omits_goal(self, tmp_path):
        store = TaskStoreV2(str(tmp_path / "nogoal.db"))
        api = InvestigationAPI(
            service=InvestigationService(
                planner=PlannerV2(ScriptedLLM(
                    [plan_json("case_intake",
                               ["fetch_case", "generate_artifact"])])),
                executor=None, task_store=store),
            store=store,
            case_findings_provider=lambda case_id: u00010_findings(),
        )
        inv_module.api = api
        from app.main import app
        c = TestClient(app, raise_server_exceptions=False)
        inv = _create(c)
        t = c.post(f"/api/v2/investigations/{inv}/turn",
                   json={"message": "Investigate U00010"})
        assert t.status_code == 200
        assert t.json()["goal"] is None          # additive field stays None


# ---------------------------------------------------------------------------
# Loop path: /goal → bounded loop → goal representation persisted
# ---------------------------------------------------------------------------

class TestLoopPathGoal:
    def test_e1_goal_is_semantic_not_the_tool(self, tmp_path):
        c = make_client(tmp_path, goal_payload={
            "goal_type": "cross_account_check",
            "goal_resolution": "INFERRED",
        })
        inv = _create(c)
        g = c.post(f"/api/v2/investigations/{inv}/goal",
                   json={"goal":
                         "那这个账号有与其他账号做关联对冲交易的情况吗"})
        assert g.status_code == 200
        activity = await_terminal(c, g.json()["task_id"])
        assert activity["goal_representation"]["goal_type"] == \
            "cross_account_check"
        # the goal representation is NOT silently replaced by the tool id
        assert activity["goal_representation"]["goal_type"] != "cross_account_trade_analysis"

    def test_e3_network_goal_is_represented(self, tmp_path):
        c = make_client(tmp_path, goal_payload={
            "goal_type": "inspect_network_structure",
            "goal_resolution": "INFERRED",
        })
        inv = _create(c)
        g = c.post(f"/api/v2/investigations/{inv}/goal",
                   json={"goal":
                         "这个账户的 Linked Account Network 具体是什么？"})
        assert g.status_code == 200
        activity = await_terminal(c, g.json()["task_id"])
        assert activity["goal_representation"]["goal_type"] == "inspect_network_structure"

    def test_activity_projection_carries_goal(self, tmp_path):
        c = make_client(tmp_path, goal_payload={
            "goal_type": "cross_account_check",
            "goal_resolution": "INFERRED",
        })
        inv = _create(c)
        g = c.post(f"/api/v2/investigations/{inv}/goal",
                   json={"goal": "cross-account hedging check"})
        activity = await_terminal(c, g.json()["task_id"])
        assert activity["goal_representation"]["goal_type"] == \
            "cross_account_check"
        assert "reason" in activity["goal_representation"]
        # no reasoning/prompts in the projection
        blob = json.dumps(activity)
        assert "prompt" not in blob.lower()

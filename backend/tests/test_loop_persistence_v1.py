"""Agent Loop persistence — plan / tool calls / findings in the session
read model (U00047 persistence fix).

Pins the read-model contract after a loop run terminates:
- the executed plan is persisted under session["plans"][task_id] with the
  FINAL step statuses (executed steps reflect reality; un-executed steps
  stay pending — never promoted to success);
- the run's ToolCallV2 records are persisted into session["tool_calls"]
  (the same list shape the turn path writes), filtered by task_id on
  read; findings carried by tool results restore through the formal read
  chain (GET /tasks/{id}/results) with their evidence intact;
- repeated records of the same run never duplicate (idempotent writes);
- two tasks / two investigations never bleed into each other;
- failed runs record the failed step honestly and never fabricate
  findings.
"""

import time

from fastapi.testclient import TestClient

from app.agent_loop import AgentLoopRunner, DecisionProposal
from app.api.investigations import InvestigationAPI
from app.executor_v2 import ExecutorV2, ToolProvider
from app.investigation_service import InvestigationService
from app.models import (
    ToolCallV2,
    ToolCallStatusV2,
    ToolError,
    ToolResult,
    ToolResultOutcome,
)
from app.planner_v2 import PlannerV2
from app.task_store_v2 import TaskStoreV2
from eval.fixtures import ScriptedLLM, default_findings, plan_json

CASE = "U00299"


class ScriptedProvider(ToolProvider):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.last = None

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
        d = self.decisions.pop(0) if self.decisions else "CONTINUE"
        return DecisionProposal(decision=d, reason="test", source="llm")


def fetch_with_findings():
    """A risk_case_fetch success carrying REAL Risk-Platform-shaped
    findings (the eval fixture set) — the data the findings panel must
    restore."""
    findings = [f.model_dump() for f in default_findings()]
    return ToolResult(
        outcome=ToolResultOutcome.SUCCESS,
        data={"case_id": CASE, "findings": findings,
              "evidence_refs": [], "signal_refs": [], "policy_refs": []},
        evidence_refs=[{"kind": "risk_event", "id": CASE}],
    )


def build_env(tmp_path, provider_results=None, decisions=("STOP",)):
    import app.api.investigations as inv_module

    store = TaskStoreV2(str(tmp_path / "loop_persist.db"))

    def factory(store):  # per-investigation runner (fresh per submit)
        return AgentLoopRunner(
            planner=PlannerV2(ScriptedLLM([
                plan_json("case_intake", ["fetch_case"]),
            ] * 4)),
            executor=ExecutorV2(ScriptedProvider(provider_results or [])),
            store=store,
            proposer=ScriptedProposer(list(decisions)),
            findings_provider=lambda: default_findings(),
        )

    api = InvestigationAPI(
        service=InvestigationService(
            planner=PlannerV2(ScriptedLLM([
                plan_json("case_intake", ["fetch_case"]),
            ] * 4)),
            executor=None, task_store=store),
        store=store,
        case_findings_provider=lambda case_id: [],
        loop_runner_factory=factory,
    )
    inv_module.api = api
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/v2/investigations", json={"case_reference": CASE})
    inv = r.json()["investigation"]["investigation_id"]
    return client, api, store, inv


def submit_and_wait(client, inv, message, timeout_s=25):
    r = client.post(f"/api/v2/investigations/{inv}/submit",
                    json={"message": message})
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    deadline = time.time() + timeout_s
    d = {}
    while time.time() < deadline:
        resp = client.get(f"/api/v2/tasks/{task_id}")
        if resp.status_code == 200:
            d = resp.json()
            if d["task"]["status"] in ("completed", "failed"):
                break
        time.sleep(0.2)
    return task_id, d


class TestLoopPersistence:
    def test_plan_tool_calls_findings_restored(self, tmp_path):
        """The full read-back contract: after a loop run, the executed
        plan (real statuses), the tool calls, and the findings all
        restore through the formal read chain."""
        client, api, store, inv = build_env(
            tmp_path, provider_results=[fetch_with_findings()])
        task_id, detail = submit_and_wait(client, inv, "Investigate U00299")
        assert detail["task"]["status"] in ("completed", "failed")

        session = store.get_session(inv)
        # 1) plan persisted, keyed by task, statuses = final reality
        plan = session["plans"][task_id]
        assert plan["plan_id"]
        types = [s["type"] for s in plan["steps"]]
        assert types[0] == "fetch_case"
        assert plan["steps"][0]["status"] in ("success", "failed")
        # 2) tool calls persisted in the turn-path list shape
        calls = [c for c in session["tool_calls"]
                 if c["task_id"] == task_id]
        assert len(calls) >= 1
        assert calls[0]["tool_name"] == "risk_case_fetch"
        # 3) findings carried by the tool result, restored via the formal
        #    read chain
        results = client.get(f"/api/v2/tasks/{task_id}/results").json()
        finding_results = [r for r in results["results"] if r.get("findings")]
        assert finding_results, "risk_case_fetch findings not restored"
        restored = finding_results[0]["findings"]
        assert restored[0]["finding_id"] == \
            (default_findings()[0].finding_id)
        # 4) the task detail serves the plan through summarize_plan
        assert detail["plan"] is not None
        assert detail["plan"]["steps"][0]["type"] == "fetch_case"

    def test_repeated_reads_and_double_record_no_duplicates(self, tmp_path):
        """Polling, repeated reads and even a duplicate record call never
        duplicate plan/tool-call/findings records."""
        captured = {}
        client, api, store, inv = build_env(
            tmp_path, provider_results=[fetch_with_findings()])

        original = InvestigationAPI._record_loop_result

        def recording(self, investigation_id, result):
            captured["result"] = result
            return original(self, investigation_id, result)

        api._record_loop_result = recording.__get__(api)
        task_id, _ = submit_and_wait(client, inv, "Investigate U00299")

        session = store.get_session(inv)
        calls_before = len([c for c in session["tool_calls"]
                            if c["task_id"] == task_id])
        steps_before = len(session["plans"][task_id]["steps"])
        results_before = len(client.get(
            f"/api/v2/tasks/{task_id}/results").json()["results"])

        # simulate a duplicate record of the same run (idempotent writes)
        api._record_loop_result(inv, captured["result"])
        # repeated reads
        for _ in range(3):
            client.get(f"/api/v2/tasks/{task_id}/results")
            client.get(f"/api/v2/tasks/{task_id}")

        session = store.get_session(inv)
        calls_after = len([c for c in session["tool_calls"]
                           if c["task_id"] == task_id])
        assert calls_after == calls_before
        assert len(session["plans"][task_id]["steps"]) == steps_before
        assert len(client.get(f"/api/v2/tasks/{task_id}/results")
                   .json()["results"]) == results_before

    def test_two_tasks_keep_separate_records(self, tmp_path):
        client, api, store, inv = build_env(
            tmp_path, provider_results=[fetch_with_findings(),
                                        fetch_with_findings()])
        t1, _ = submit_and_wait(client, inv, "first request")
        t2, _ = submit_and_wait(client, inv, "second request")
        session = store.get_session(inv)
        assert session["plans"][t1]["plan_id"] != \
            session["plans"][t2]["plan_id"]
        calls1 = [c for c in session["tool_calls"] if c["task_id"] == t1]
        calls2 = [c for c in session["tool_calls"] if c["task_id"] == t2]
        assert calls1 and calls2
        assert {c["tool_call_id"] for c in calls1}.isdisjoint(
            {c["tool_call_id"] for c in calls2})

    def test_two_investigations_never_bleed(self, tmp_path):
        client, api, store, inv1 = build_env(
            tmp_path, provider_results=[fetch_with_findings()])
        inv2 = client.post("/api/v2/investigations",
                           json={"case_reference": CASE}
                           ).json()["investigation"]["investigation_id"]
        t1, _ = submit_and_wait(client, inv1, "first investigation request")
        t2, _ = submit_and_wait(client, inv2, "second investigation request")
        session = store.get_session(inv1)
        ids1 = {c["task_id"] for c in session["tool_calls"]}
        assert t1 in ids1 and t2 not in ids1
        assert session["plans"][t1]["investigation_id"] == inv1
        session2 = store.get_session(inv2)
        assert session2["plans"][t2]["investigation_id"] == inv2


class TestFailedRunPersistence:
    def test_failed_step_recorded_honestly_no_fabricated_findings(
            self, tmp_path):
        """A tool failure is recorded as FAILED in the plan and the run
        reports failure — no fabricated findings, no fake success."""
        client, api, store, inv = build_env(
            tmp_path,
            provider_results=[ToolResult(
                outcome=ToolResultOutcome.INTEGRATION_ERROR,
                error=ToolError(code="RISK_PLATFORM_UNAVAILABLE",
                                message="RP timed out"))],
            decisions=("STOP",))
        task_id, detail = submit_and_wait(client, inv, "Investigate U00299")
        assert detail["task"]["status"] == "failed"

        session = store.get_session(inv)
        plan = session["plans"][task_id]
        failed_steps = [s for s in plan["steps"] if s["status"] == "failed"]
        assert failed_steps, "the failed step must be recorded as failed"
        # no findings were produced and none are fabricated
        results = client.get(f"/api/v2/tasks/{task_id}/results").json()
        finding_results = [r for r in results["results"] if r.get("findings")]
        assert not finding_results


class TestPlanExecutionReconciliation:
    """U00047 follow-up: a re-plan renumbers step ids, so the FINAL
    revision's copy of a step can sit PENDING while that very step_id
    already executed successfully under an earlier revision (real Case
    Intake: TC-S2 artifact_bundle success + artifact delivered, final
    snapshot showed S2 pending). The snapshot build reconciles exactly
    those steps to success; failed / foreign-step / evidence-less cases
    stay untouched."""

    def _make_result(self, plan_steps, tool_calls, task_id="TASK-P",
                     status="completed"):
        from app.agent_loop import AgentLoopResult, AgentLoopState
        from app.models import TaskV2, TaskStatusV2
        state = AgentLoopState(
            task_id=task_id, investigation_id="INV-P", case_id=CASE,
            goal="g", criteria_kind="artifact_produced",
            plan_id="PLAN-P", plan_steps=plan_steps,
            agent_status=status, stop_reason="goal_satisfied",
        )
        task = TaskV2(task_id=task_id, investigation_id="INV-P",
                      type="agent_loop", status=TaskStatusV2.COMPLETED,
                      user_request="g", artifact_ids=[])
        return AgentLoopResult(state=state, response="done", task=task,
                               tool_calls=list(tool_calls))

    def _tc(self, step_id, tool_name, status="success"):
        from app.models import ToolCallStatusV2
        return ToolCallV2(
            tool_call_id=f"TC-{step_id}-20261019T000000+0000",
            investigation_id="INV-P", task_id="TASK-P",
            tool_name=tool_name, step_id=step_id,
            status=(ToolCallStatusV2.SUCCESS if status == "success"
                    else ToolCallStatusV2.FAILED),
            result=ToolResult(
                outcome=(ToolResultOutcome.SUCCESS if status == "success"
                         else ToolResultOutcome.INTEGRATION_ERROR),
                error=(None if status == "success" else ToolError(
                    code="RISK_PLATFORM_UNAVAILABLE",
                    message="RP timed out")),
                data={"analysis": "cross_account_trade_analysis",
                      "candidates": []}),
        )

    PLAN_STEPS = [
        {"step_id": "S1", "type": "fetch_case", "status": "success",
         "tool_name": "risk_case_fetch", "arguments": {}},
        {"step_id": "S2", "type": "generate_artifact", "status": "pending",
         "tool_name": "artifact_bundle", "arguments": {}},
    ]

    def _persisted(self, result):
        import tempfile
        from app.task_store_v2 import TaskStoreV2
        store = TaskStoreV2(tempfile.mkdtemp() + "/recon.db")
        api = InvestigationAPI(store=store)
        inv = "INV-RECON"
        api._record_loop_result(inv, result)
        plan = store.get_session(inv)["plans"]["TASK-P"]
        by_type = {s["type"]: s["status"] for s in plan["steps"]}
        calls = len([c for c in store.get_session(inv)["tool_calls"]
                     if c["task_id"] == "TASK-P"])
        return by_type, calls

    def test_pending_step_with_successful_step_tool_promoted(self):
        by_type, _ = self._persisted(self._make_result(
            self.PLAN_STEPS,
            [self._tc("S1", "risk_case_fetch"),
             self._tc("S2", "artifact_bundle")]))
        assert by_type["generate_artifact"] == "success"
        assert by_type["fetch_case"] == "success"

    def test_failed_tool_call_does_not_promote(self):
        by_type, _ = self._persisted(self._make_result(
            self.PLAN_STEPS,
            [self._tc("S2", "artifact_bundle", status="failed")]))
        assert by_type["generate_artifact"] == "pending"

    def test_success_of_other_step_does_not_promote(self):
        by_type, _ = self._persisted(self._make_result(
            self.PLAN_STEPS,
            [self._tc("S1", "risk_case_fetch")]))
        assert by_type["generate_artifact"] == "pending"

    def test_tool_mismatch_does_not_promote(self):
        by_type, _ = self._persisted(self._make_result(
            self.PLAN_STEPS,
            [self._tc("S2", "risk_case_fetch")]))
        assert by_type["generate_artifact"] == "pending"

    def test_failed_plan_step_never_promoted(self):
        steps = [dict(s) for s in self.PLAN_STEPS]
        steps[1] = dict(steps[1], status="failed")
        by_type, _ = self._persisted(self._make_result(
            steps, [self._tc("S1", "risk_case_fetch"),
                    self._tc("S2", "artifact_bundle")]))
        assert by_type["generate_artifact"] == "failed"

    def test_multi_task_isolation(self, tmp_path):
        """Another task's success calls never promote this task's steps:
        the reconciliation evidence comes only from THIS run's
        tool_calls (AgentLoopResult.tool_calls)."""
        import tempfile
        from app.agent_loop import AgentLoopResult, AgentLoopState
        from app.api.investigations import InvestigationAPI
        from app.models import TaskV2, TaskStatusV2
        foreign = self._tc("S2", "artifact_bundle")
        foreign = foreign.model_copy(update={
            "task_id": "TASK-OTHER",
            "tool_call_id": "TC-S2-OTHER",
        })
        state = AgentLoopState(
            task_id="TASK-P", investigation_id="INV-P", case_id=CASE,
            goal="g", criteria_kind="artifact_produced",
            plan_id="PLAN-P", plan_steps=self.PLAN_STEPS,
            agent_status="completed", stop_reason="goal_satisfied",
        )
        task = TaskV2(task_id="TASK-P", investigation_id="INV-P",
                      type="agent_loop", status=TaskStatusV2.COMPLETED,
                      user_request="g", artifact_ids=[])
        result = AgentLoopResult(state=state, response="done", task=task,
                                 tool_calls=[foreign])
        store = TaskStoreV2(str(tmp_path / "recon-iso.db"))
        api = InvestigationAPI(store=store)
        api._record_loop_result("INV-P", result)
        plan = store.get_session("INV-P")["plans"]["TASK-P"]
        by_type = {s["type"]: s["status"] for s in plan["steps"]}
        assert by_type["generate_artifact"] == "pending"

"""HTTP-level recovery verification for the polluted-focus scenario.

Isolated temp store (no production data) + the real API app + the real
resume endpoint + the real findings provider/executor over live RP + the
real LLM for re-resolution. Seeds the PRE-FIX polluted state (U00005,
focus F3 = First Withdrawal to New Address, agent_resolved, mid-flight
plan), then POSTs the real /tasks/{id}/resume and observes the final
focus, executed parameters and result.
"""
import json
import sys
import tempfile
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/backend")

from fastapi.testclient import TestClient

from app.api.investigations import InvestigationAPI, router
from app.main import app
from app.models import TaskV2, TaskStatusV2
from app.task_store_v2 import TaskStoreV2

results = {}

store = TaskStoreV2(db_path=tempfile.mkdtemp() + "/resume_http.db")
import app.api.investigations as inv_module
from app.investigation_service import InvestigationService
from app.planner_v2 import PlannerV2


class _TurnStub:
    """The turn path is never invoked by the resume endpoint; a bounded
    placeholder keeps the service constructible without an LLM."""

    def generate(self, messages=None, max_tokens=0, temperature=0.1):
        return ""


inv_module.api = InvestigationAPI(
    service=InvestigationService(planner=PlannerV2(_TurnStub()),
                                 executor=None, task_store=store),
    store=store)

TASK = "TASK-RESUME-HTTP"
store.create(TaskV2(task_id=TASK, investigation_id="INV-RESUME-HTTP",
                    user_request="为什么高频提现是风险项？",
                    type="agent_loop", status=TaskStatusV2.EXECUTING))
from app.agent_loop import AgentLoopState
state = AgentLoopState(
    task_id=TASK, investigation_id="INV-RESUME-HTTP", case_id="U00005",
    goal="为什么高频提现是风险项？", criteria_kind="question_answered",
    focused_finding_id="F3", focus_source="agent_resolved",
    target_evidence_kind=None)
state.plan_steps = [
    {"step_id": "S1", "type": "explain_signal", "status": "pending",
     "tool_name": "signal_explain", "arguments": {}}]
state.iteration = 1
store.save_agent_state(state)

client = TestClient(app)
run = client.post(f"/api/v2/tasks/{TASK}/resume")
results["resume_status"] = run.status_code
results["resume_body"] = run.json()

deadline = time.time() + 600
activity = {}
while time.time() < deadline:
    activity = client.get(f"/api/v2/tasks/{TASK}/activity").json()
    if activity.get("is_terminal"):
        break
    time.sleep(5)

detail = client.get(f"/api/v2/tasks/{TASK}").json()
results["final_focus"] = activity.get("focused_finding_id")
results["agent_status"] = activity.get("agent_status")
results["outcome"] = activity.get("goal_outcome")
results["satisfactions"] = {
    o.get("output_id"): o.get("satisfaction")
    for o in (activity.get("goal_contract") or {}).get("outputs", [])}
results["text"] = (detail.get("task_response_text") or "")[:600]

# executed tool target from the persisted state document
st = store.get_agent_state(TASK)
explains = [o for o in st.observations
            if o.get("step_type") == "explain_signal"]
results["explain_rule_names"] = [
    (o.get("summary") or {}).get("rule_name") for o in explains]
results["explain_targets"] = [
    (o.get("summary") or {}).get("finding_id") for o in explains]

results["PASS"] = (
    results["final_focus"] == "F2"
    and "High withdrawal frequency" in results["explain_rule_names"]
    and all(n != "First withdrawal to new address"
            for n in results["explain_rule_names"]))
print(json.dumps(results, ensure_ascii=False, indent=2))

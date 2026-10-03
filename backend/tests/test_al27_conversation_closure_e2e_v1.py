"""AL-2.7 — REAL RP API E2E: conversational closure. NO MOCKS.

Four SEPARATE oracles per scenario — never merged:
  1. Runtime Completion        (terminal agent_status / task status)
  2. Activity Projection       (persisted trajectory events)
  3. Conversation Response     (assistant response via the conversation
                                read model: /turn response body and
                                GET /tasks/{id}.task_response_text)
  4. Persistence               (response + artifacts served after the run,
                                through the read models the UI restores)

Scenarios:
  A. bound U00010 conversation + "展开一下00010案例情况" via /turn
     → case-level route, intake plan, real RP, assistant response
  B. raw cross-account goal via /goal → loop → real RP → assistant
     response from AgentLoopResult.response, live-consumable
  C. negative: unsupported/unresolvable requests stay safely terminal
     with NO case-level execution and NO fabricated response

Skips when the real stack is not running (the AL-2.7 gate requires this
test to RUN and PASS against the live stack).
"""

import json
import time

import httpx
import pytest

BACKEND = "http://localhost:8001"
RP = "http://localhost:8000"
CASE = "U00010"
CASE_LEVEL_TEXT = "展开一下00010案例情况"
LOOP_GOAL = ("查一下有没有不同账户之间的对冲交易情况，如果有，输出相关证据给我；"
             "如果没有，告诉我你都查了哪些范围内的账户从而判定没有关联交易的")
POLL_TIMEOUT_S = 300

pytestmark = pytest.mark.al27_real_rp


def _stack_available() -> bool:
    try:
        if httpx.get(f"{BACKEND}/health", timeout=3).status_code != 200:
            return False
        return httpx.get(
            f"{RP}/api/risk/cases/{CASE}/evidence",
            timeout=5).status_code == 200
    except Exception:
        return False


def _poll_activity(c: httpx.Client, task_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT_S
    activity = None
    while time.time() < deadline:
        activity = c.get(
            f"{BACKEND}/api/v2/tasks/{task_id}/activity").json()
        if activity.get("is_terminal"):
            return activity
        time.sleep(2)
    raise AssertionError("loop did not terminate in time")


def test_scenario_a_case_level_nl_turn_answers():
    if not _stack_available():
        pytest.skip("real stack not running")
    with httpx.Client(timeout=60) as c:
        inv = c.post(f"{BACKEND}/api/v2/investigations",
                     json={"case_id": CASE})
        assert inv.status_code == 200
        inv_id = inv.json()["investigation"]["investigation_id"]

        # ---- Oracle 3 (conversation) + 1 (runtime): the /turn response ---
        turn = c.post(f"{BACKEND}/api/v2/investigations/{inv_id}/turn",
                      json={"message": CASE_LEVEL_TEXT})
        assert turn.status_code == 200, turn.text
        body = turn.json()
        task = body["task"]
        assert task["status"] == "completed", (
            task.get("status"), task.get("error"))
        assert task["error"] != "unresolved_reference"
        # the assistant response EXISTS and corresponds to the real case
        response = body["response"] or ""
        assert CASE in response or "findings" in response.lower()
        execution = body.get("execution") or {}
        assert execution.get("tool_calls"), "no tool executed"
        assert execution["tool_calls"][0]["tool_name"] == "risk_case_fetch"
        assert execution["tool_calls"][0]["outcome"] == "success"

        # ---- Oracle 2 (execution trace): a /turn task has NO agent-loop
        # activity projection (404 by design) — its persisted trace is the
        # task detail (plan + this task's tool calls) ----------------------
        detail = c.get(f"{BACKEND}/api/v2/tasks/{task['task_id']}").json()
        assert detail.get("plan"), "no persisted plan"
        assert detail.get("tool_calls"), "no persisted tool calls"

        # ---- Oracle 4 (persistence): response served by the read model ---
        assert (detail.get("task_response_text") or "").strip() != ""
        assert CASE in detail["task_response_text"] or \
            "findings" in detail["task_response_text"].lower()


def test_scenario_b_loop_produces_conversation_response():
    if not _stack_available():
        pytest.skip("real stack not running")
    with httpx.Client(timeout=60) as c:
        inv = c.post(f"{BACKEND}/api/v2/investigations",
                     json={"case_id": CASE})
        assert inv.status_code == 200
        inv_id = inv.json()["investigation"]["investigation_id"]
        run = c.post(f"{BACKEND}/api/v2/investigations/{inv_id}/goal",
                     json={"goal": LOOP_GOAL})
        assert run.status_code == 200
        task_id = run.json()["task_id"]

        # ---- Oracle 1 (runtime completion) + 2 (activity projection) -----
        activity = _poll_activity(c, task_id)
        assert activity["agent_status"] == "completed"
        assert activity["stop_reason"] == "goal_satisfied"
        events = {e.get("event_type") for e in activity["activity"]}
        assert {"context.resolved", "plan.created", "tool.called",
                "tool.completed", "observation.recorded"} <= events

        # ---- Oracle 3 (conversation response): the loop's composed
        # response is served by the conversation read model ----------------
        detail = c.get(f"{BACKEND}/api/v2/tasks/{task_id}").json()
        response_text = (detail.get("task_response_text") or "").strip()
        assert response_text, "no assistant response for the completed run"
        # it is the FORMAL composition (canonical phrase + recap), not a
        # fabricated or static string
        assert "completed" in response_text.lower()
        assert "analyze_cross_account_trades" in response_text
        assert response_text != LOOP_GOAL

        # ---- Oracle 4 (persistence): artifact + response both served -----
        assert activity["artifact_ids"]
        arts = c.get(
            f"{BACKEND}/api/v2/tasks/{task_id}/artifacts").json()["artifacts"]
        assert len(arts) >= 1
        assert any(a["artifact_id"] == activity["artifact_ids"][0]
                   for a in detail["artifacts"])


def test_scenario_c_negative_controls():
    if not _stack_available():
        pytest.skip("real stack not running")
    with httpx.Client(timeout=60) as c:
        inv = c.post(f"{BACKEND}/api/v2/investigations",
                     json={"case_id": CASE})
        inv_id = inv.json()["investigation"]["investigation_id"]

        # unsupported (no case id, no finding target): safe turn failure
        turn = c.post(f"{BACKEND}/api/v2/investigations/{inv_id}/turn",
                      json={"message":
                            "tell me about the thing that does not exist"})
        assert turn.status_code == 200
        task = turn.json()["task"]
        assert task["status"] == "failed"
        # AL-2.10: the bounded-blocked family is the contract — the
        # real-LLM assist may legitimately yield a single-candidate
        # ambiguity for an unresolvable referent.
        assert task["error"] in ("unresolved_reference",
                                 "clarification_needed",
                                 "semantic_resolution_unavailable")
        assert not turn.json().get("tool_calls")

        # cross-account-LOOKING but no trade subject: still blocked (the
        # AL-2.6 negative control still holds end-to-end)
        run = c.post(f"{BACKEND}/api/v2/investigations/{inv_id}/goal",
                     json={"goal": "比较不同账户的提现频率"})
        assert run.status_code == 200
        activity = _poll_activity(c, run.json()["task_id"])
        assert activity["agent_status"] == "blocked"
        assert activity["plan_steps"] == []
        tools = [e for e in activity["activity"]
                 if e.get("event_type") in ("tool.called", "tool.completed")]
        assert tools == []

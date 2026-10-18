"""P0/A-fix — real-API E2E scenario 2: consecutive empties on the LLM
PLANNER path (no deterministic planning branch applies for this request)
→ bounded failure with the truthful LLM_EMPTY_RESPONSE classification.

Backend started with LLM_FAULT_SKIP_FIRST=2 LLM_FAULT_EMPTY_FIRST=2
(controlled fault injection at the shared LLM call boundary — NOT a real
model outage): call #1 = intake (healthy), call #2 = goal understanding
(healthy, in skip window), calls #3/#4 = the LLM planner's two bounded
attempts — both injected empty. The run must terminate FAILED without
executing any step and without claiming completion.
"""
import json
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"
# phrased to REQUIRE LLM planning: no deterministic branch vocabulary
# (no 为什么/explain, no 对冲/关联/网络, no 总结/报告/时间线/政策)
QUESTION = ("把F4涉及的风险信号和账户行为特征综合起来，"
            "给我一个结构化的风险评估，包括最值得关注的三个方面")
results = {"question": QUESTION, "scenario": "planner_consecutive_empty"}


def _call(method, path, payload=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None
        else None,
        headers={"Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


try:
    inv = _call("POST", "/investigations", {"case_id": "U00047"})
    inv_id = inv["investigation"]["investigation_id"]
    results["investigation_id"] = inv_id

    turn = _call("POST", f"/investigations/{inv_id}/turn",
                 {"message": "Investigate U00047"}, timeout=180)
    results["intake_turn_status"] = turn.get("status")

    run = _call("POST", f"/investigations/{inv_id}/goal",
                {"goal": QUESTION,
                 "context_action": {"type": "focus_finding",
                                    "finding_id": "F4"}})
    task_id = run["task_id"]
    results["task_id"] = task_id

    deadline = time.time() + 600
    activity = None
    while time.time() < deadline:
        activity = _call("GET", f"/tasks/{task_id}/activity")
        if activity["is_terminal"]:
            break
        time.sleep(5)
    assert activity and activity["is_terminal"], "loop did not terminate"

    results["agent_status"] = activity["agent_status"]
    results["stop_reason"] = activity.get("stop_reason")
    steps = activity.get("plan_steps") or []
    results["steps_attempted"] = [
        {"type": s.get("type"), "status": s.get("status")} for s in steps]

    detail = _call("GET", f"/tasks/{task_id}")
    results["tool_call_count"] = len(detail["task"].get("tool_call_ids")
                                     or [])
    results["response_head"] = (detail.get("task_response_text")
                                or "")[:500]

    results["checks"] = {
        "task_failed": activity["agent_status"] == "failed",
        "planning_failure_stop":
            activity.get("stop_reason") == "planning_failure",
        "no_steps_executed": results["tool_call_count"] == 0,
        "no_substantive_answer":
            "结构化" not in (detail.get("task_response_text") or "")
            and "最值得关注" not in (detail.get("task_response_text") or ""),
    }
    results["PASS"] = all(results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
finally:
    with open("/tmp/p0a_api_exhaust.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))

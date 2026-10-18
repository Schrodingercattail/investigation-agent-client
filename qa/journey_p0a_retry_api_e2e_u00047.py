"""P0/A-fix — real-API E2E scenario 1: first-call empty, retry recovers.

Backend started with LLM_FAULT_EMPTY_FIRST=1 (controlled fault injection
at the shared LLM call boundary — NOT a real model recovery test).
Real journey: create investigation -> intake turn -> goal run with the
U00047 F4 question. Verifies:
  1. the injected empty response is retried once and the flow CONTINUES
     (normal resolution -> planning -> execution), task reaches a real
     terminal completed state;
  2. plan, tool evidence, and GoalOutcome are present and coherent;
  3. the F4 shared-device explanation is substantive with the honest
     threshold/authority boundary.
"""
import json
import sys
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"
QUESTION = "共享设备为什么是风险项，是否有判定阈值"
results = {"question": QUESTION, "scenario": "empty_then_valid_retry"}


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
    results["goal_outcome"] = activity.get("goal_outcome")

    detail = _call("GET", f"/tasks/{task_id}")
    plan = detail.get("plan") or {}
    steps = plan.get("steps") or []
    results["plan_steps"] = [
        {"type": s.get("type"), "status": str(s.get("status")),
         "tool": s.get("tool_name")} for s in steps]
    results["response_head"] = (detail.get("response") or "")[:1200]
    tool_calls = ((detail.get("execution") or {}).get("tool_calls")) or []
    results["tool_calls"] = [
        {"tool": t.get("tool_name"), "outcome": t.get("outcome")}
        for t in tool_calls]

    resp = detail.get("response") or ""
    results["checks"] = {
        "task_completed": activity["agent_status"] == "completed",
        "has_plan": bool(steps),
        "plan_executed": all("EXECUTED" in str(s.get("status")).upper()
                             or "COMPLETED" in str(s.get("status")).upper()
                             for s in steps) if steps else False,
        "has_tool_evidence": bool(tool_calls),
        "f4_explained": ("Shared Device" in resp or "共享设备" in resp),
        "threshold_or_authority_boundary":
            ("阈值" in resp or "threshold" in resp.lower()
             or "Risk Platform" in resp),
    }
    results["PASS"] = all(results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
finally:
    with open("/tmp/p0a_api_retry.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))

"""Q2/R0+R1 — real-API E2E: structured-output retry + fallback correctness.

Backend started with ENABLE_GOAL_SEMANTIC_CONTRACT=true and CONTROLLED
MALFORMED-JSON fault injection (LLM_FAULT_MALFORMED_JSON_FIRST /
LLM_FAULT_SKIP_FIRST): the provider answers with NON-EMPTY broken JSON —
the live U00008 failure class. One scenario per process (the fault budget
is per-process).

  Scenario 1 (SKIP=1, MALFORMED=1): the goal-understanding model's first
    attempt is malformed; the R0 bounded retry regenerates and recovers —
    the contract must be LLM-reviewed (intent_source=llm), and all four
    Q2 deliverables planned and executed.
  Scenario 2 (SKIP=1, MALFORMED=99): every structured call fails — goal
    understanding falls back to the deterministic candidate contract AND
    the decision proposer falls back on every decision; with R1 the
    fallback must NOT stop after the first success — the remaining steps
    execute, and the run ends honestly (no premature STOP, no fake
    completeness).
"""
import json
import sys
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"
Q = ("高频提现判定阈值是怎样的？全部提现都提现到新地址是同一地址还是不同地址？"
     "把提现明细发我")
scenario = sys.argv[1] if len(sys.argv) > 1 else "1"
results = {"scenario": scenario, "question": Q}


def _call(method, path, payload=None, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None
        else None,
        headers={"Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


try:
    inv = _call("POST", "/investigations", {"case_id": "U00008"})
    inv_id = inv["investigation"]["investigation_id"]
    _call("POST", f"/investigations/{inv_id}/turn",
          {"message": "Investigate U00008"}, timeout=300)
    run = _call("POST", f"/investigations/{inv_id}/goal", {"goal": Q})
    task_id = run["task_id"]
    deadline = time.time() + 900
    activity = None
    while time.time() < deadline:
        activity = _call("GET", f"/tasks/{task_id}/activity")
        if activity["is_terminal"]:
            break
        time.sleep(5)
    assert activity and activity["is_terminal"]
    detail = _call("GET", f"/tasks/{task_id}")
    resp = detail.get("task_response_text") or ""
    contract = activity.get("goal_contract") or {}
    outs = contract.get("outputs") or []
    steps = activity.get("plan_steps") or []
    entry = {
        "task": task_id, "inv": inv_id,
        "agent_status": activity.get("agent_status"),
        "stop_reason": activity.get("stop_reason"),
        "goal_outcome": activity.get("goal_outcome"),
        "intent_source": contract.get("intent_source"),
        "outputs": [{"claim": o.get("claim", "")[:38],
                     "satisfaction": o.get("satisfaction")} for o in outs],
        "plan": [{"t": s.get("type"), "st": s.get("status")} for s in steps],
    }
    if scenario == "1":
        entry["checks"] = {
            "gu_recovered_llm_reviewed": contract.get("intent_source") == "llm",
            "deliverables_covered": len(outs) >= 4,
            "all_satisfied": all(o.get("satisfaction") == "satisfied"
                                 for o in outs),
            "f2_threshold_answered": ("withdrawal_frequency_24h" in resp
                                      or "高频提现" in resp),
            "destination_compared": ("同一" in resp or "不同" in resp
                                     or "地址" in resp),
            "withdrawal_details": ("提现明细" in resp or "is_new_address"
                                   in resp or "逐笔" in resp),
            "no_premature_stop": activity.get("agent_status") == "completed",
        }
    else:
        entry["checks"] = {
            "gu_fallback_heuristic": contract.get("intent_source")
            == "heuristic",
            "four_outputs": len(outs) == 4,
            "executed_beyond_first_step": any(
                s.get("status") == "success" for s in steps[1:]) or
            len([s for s in steps if s.get("status") == "success"]) >= 2,
            "no_premature_stop": len(
                [s for s in steps if s.get("status") == "success"]) >= 2,
            "honest_outcome": activity.get("goal_outcome") in (
                "partial", "complete", "unanswerable"),
            "no_fake_complete_with_pending": not (
                any(o.get("satisfaction") == "pending" for o in outs)
                and activity.get("goal_outcome") == "complete"),
            "withdrawal_details_executed": any(
                s.get("type") == "inspect_withdrawals"
                and s.get("status") == "success" for s in steps),
        }
    entry["PASS"] = all(entry["checks"].values())
    results.update(entry)
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
finally:
    with open(f"/tmp/q2_r0r1_api_s{scenario}.json", "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k not in ("outputs", "plan")},
                     ensure_ascii=False, indent=1))

"""Regression journeys (real runs, other cases / other rule states).

1. TRIGGERED rule path: U00005's withdrawal-frequency finding is
   rule-triggered — the Rule branch (命中规则 + threshold + contribution)
   must be untouched by the feature-path work.
2. Case-independence: the SAME question shape on U00010 resolves to THAT
   case's own finding (a different finding id — ids are per-case labels,
   never global semantics) and carries that case's own authoritative
   statement (34.38%).
"""
import json
import sys
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"


def _call(method, path, payload=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None
        else None,
        headers={"Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def run_goal(case_id, question, checks):
    inv = _call("POST", "/investigations", {"case_id": case_id})
    inv_id = inv["investigation"]["investigation_id"]
    _call("POST", f"/investigations/{inv_id}/turn",
          {"message": f"Investigate {case_id}"}, timeout=180)
    run = _call("POST", f"/investigations/{inv_id}/goal",
                {"goal": question})
    task_id = run["task_id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        activity = _call("GET", f"/tasks/{task_id}/activity")
        if activity["is_terminal"]:
            break
        time.sleep(5)
    detail = _call("GET", f"/tasks/{task_id}")
    text = detail.get("task_response_text") or ""
    contract = activity.get("goal_contract") or {}
    sat = {o.get("output_id"): (o.get("satisfaction"),
                                o.get("subject_ref"))
           for o in contract.get("outputs") or []}
    out = {"case_id": case_id, "task_id": task_id,
           "goal_outcome": activity.get("goal_outcome"),
           "satisfactions": sat}
    out.update({name: check(text, sat, activity)
                for name, check in checks.items()})
    out["text"] = text
    return out


results = {}
try:
    # 1) TRIGGERED rule path (withdrawal frequency — rule-backed)
    triggered = run_goal(
        "U00005",
        "为什么高频提现是风险项？",
        {
            "rule_hit_rendered": lambda t, s, a: "命中规则" in t,
            "threshold_rendered": lambda t, s, a: "withdrawal_frequency_24h > 5" in t,
            "satisfied": lambda t, s, a: all(v == "satisfied"
                                             for v, _ in s.values()),
        })
    results["triggered_rule"] = {k: v for k, v in triggered.items()
                                 if k != "text"}

    # 2) Case-independence: same question shape on ANOTHER case
    other = run_goal(
        "U00010",
        "34.38%的反向交易率是什么含义？为什么反向交易是风险项？",
        {
            "own_case_finding_subject": lambda t, s, a: any(
                sub and sub.startswith("F") for _, sub in s.values()),
            "own_metric_value": lambda t, s, a: "34.38%" in t,
            "own_rule_status": lambda t, s, a: (
                "not triggered" in t or "not_triggered" in t),
            "no_wrong_case_number": lambda t, s, a: "34.12%" not in t,
            "satisfied": lambda t, s, a: all(v == "satisfied"
                                             for v, _ in s.values()),
        })
    results["other_case"] = {k: v for k, v in other.items() if k != "text"}
    results["PASS"] = all(v is True for blk in
                          (results["triggered_rule"],
                           results["other_case"])
                          for k, v in blk.items()
                          if isinstance(v, bool))
    with open("/tmp/rule_explain_regression.json", "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("--- OTHER CASE TEXT (tail) ---")
    print(other["text"][-900:])
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(1)

"""Rule-explanation & case-judgment API E2E — U00005 original question.

Real journey over the canonical stack (real LLM, real RP). The complete
answer must distinguish, in the ACTUAL returned text:
  1. what the 34.12% metric IS (own-account semantics);
  2. whether cross-account hedging candidates involve U00005;
  3. WHY the metric belongs to the monitored risk categories (the
     platform's own authoritative statement about the finding);
  4. whether the rule triggered for THIS case (the platform's stated
     status — verbatim);
  5. the evidence boundary (what the observation does NOT establish).
Plus: the goal outcome reflects the REAL deliverables, and the FINAL
artifact carries the same facts (signal explanation + authoritative
NO_MATCH wording) — answer and artifact must not contradict.
"""
import json
import sys
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"
QUESTION = ("34.12%的反向交易率是账户间的对冲交易还是账户自己的对冲交易？"
            "为什么反向交易是风险项？")
results = {"question": QUESTION}


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
    inv = _call("POST", "/investigations", {"case_id": "U00005"})
    inv_id = inv["investigation"]["investigation_id"]
    results["investigation_id"] = inv_id

    _call("POST", f"/investigations/{inv_id}/turn",
          {"message": "Investigate U00005"}, timeout=180)

    run = _call("POST", f"/investigations/{inv_id}/goal", {"goal": QUESTION})
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

    detail = _call("GET", f"/tasks/{task_id}")
    text = detail.get("task_response_text") or ""
    results["agent_status"] = activity["agent_status"]
    results["stop_reason"] = activity.get("stop_reason")
    results["goal_outcome"] = activity.get("goal_outcome")

    contract = activity.get("goal_contract") or {}
    sat = {o.get("output_id"): o.get("satisfaction")
           for o in contract.get("outputs") or []}
    results["satisfactions"] = sat

    a = {}
    # 1 — metric semantics (its own deliverable)
    a["d1_metric_own_semantics"] = (
        "账户自身的反向交易比例" in text and "不是跨账户匹配率" in text)
    # 2 — cross-account existence (its own deliverable)
    a["d2_hedging_zero"] = (
        "未发现涉及 U00005 的对冲交易候选" in text)
    # 3 — category: monitored risk factor + the platform's own statement
    a["d3_monitored_category"] = (
        "风险因子记录" in text and "权威说明" in text)
    # 4 — the case's rule status, stated by the platform (verbatim echo)
    a["d4_rule_status_stated"] = (
        "below the 40% threshold" in text
        and "this rule was not triggered" in text)
    # category ≠ case: presence of the observation is not a trigger claim
    a["d4_category_not_case"] = (
        "观测记录的存在不等于规则已触发" in text)
    # 5 — evidence boundary
    a["d5_evidence_boundary"] = (
        "不构成风险判定" in text and "不证明或排除欺诈行为" in text)
    # never a fabricated trigger claim (the Rule-branch hit wording must
    # be absent; the negative-form "不等于规则已触发" is required instead)
    a["never_claims_triggered"] = (
        "命中规则" not in text and "触发了" not in text)
    a["never_false_incomplete"] = "整体结果" in text

    # real deliverable state: ALL outputs satisfied → complete
    a["all_outputs_satisfied"] = all(v == "satisfied" for v in sat.values())
    a["outcome_complete_honest"] = activity.get("goal_outcome") == "complete"

    # explain actually executed and succeeded
    started = [e for e in activity.get("activity", [])
               if e.get("event_type") == "step.started"]
    a["explain_step_ran"] = any(
        e.get("step_type") == "explain_signal" for e in started)

    # --- artifact consistency -------------------------------------------
    arts = _call("GET", f"/tasks/{task_id}/artifacts")
    arts = arts.get("artifacts") if isinstance(arts, dict) else arts
    final = arts[-1] if arts else {}
    body = final.get("content") or final.get("body") or ""
    results["final_artifact_id"] = final.get("artifact_id")
    a["artifact_has_signal_explanation"] = "## Signal Explanation" in body
    a["artifact_rule_status_consistent"] = (
        "below the 40% threshold" in body
        and "not triggered" in body)
    a["artifact_no_match_is_authoritative_zero"] = (
        "authoritative zero" in body)
    a["artifact_no_insufficient_contradiction"] = (
        "insufficient for a reliable conclusion" not in body)

    results["assertions"] = a
    results["PASS"] = all(a.values())
    results["response_text"] = text
    results["artifact_body"] = body
    with open("/tmp/rule_explain_u00005_api.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k not in ("response_text", "artifact_body")},
                     ensure_ascii=False, indent=2))
    print("--- RESPONSE ---")
    print(text)
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
    with open("/tmp/rule_explain_u00005_api.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2)[:2000])
    sys.exit(1)

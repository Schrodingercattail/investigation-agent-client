"""B-chain API E2E — U00005 reverse-trade-rate question (real stack).

Real journey over the canonical runtime (:8001 -> RP :8000, real LLM):
create investigation -> intake turn -> bounded Agent Loop goal run with
the EXACT historical question. Verifies:
  1. the accepted GoalContract (metric semantics / hedging / why outputs)
  2. the adopted plan schedules a legal step for EVERY registry-declarable
     output (the coverage completion — explain_signal present)
  3. tool evidence actually executed (analysis + explanation attempt)
  4. per-output satisfaction states + overall goal outcome
  5. the final response answers the metric definition from the account's
     OWN metric semantics — "no cross-account match" is never presented
     as the definition of the 34.12% figure
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
    # 1) fresh investigation for the case
    inv = _call("POST", "/investigations", {"case_id": "U00005"})
    inv_id = inv["investigation"]["investigation_id"]
    results["investigation_id"] = inv_id

    # 2) intake turn (the browser journey's entry operation) — the real
    #    RP fetch + bundle composition can take ~30-60s
    turn = _call("POST", f"/investigations/{inv_id}/turn",
                 {"message": "Investigate U00005"}, timeout=180)
    results["intake_turn_status"] = turn.get("status")

    # 3) bounded Agent Loop goal run with the exact question
    run = _call("POST", f"/investigations/{inv_id}/goal",
                {"goal": QUESTION})
    task_id = run["task_id"]
    results["task_id"] = task_id

    # 4) poll to terminal (real LLM loop — bounded wait)
    deadline = time.time() + 600
    activity = None
    while time.time() < deadline:
        activity = _call("GET", f"/tasks/{task_id}/activity")
        if activity["is_terminal"]:
            break
        time.sleep(5)
    assert activity and activity["is_terminal"], "loop did not terminate"

    detail = _call("GET", f"/tasks/{task_id}")
    results["agent_status"] = activity["agent_status"]
    results["stop_reason"] = activity.get("stop_reason")
    results["goal_outcome"] = activity.get("goal_outcome")
    results["plan_revisions"] = activity.get("plan_revisions")

    # 5) contract + plan + response
    contract = activity.get("goal_contract") or {}
    outputs = contract.get("outputs") or []
    results["contract_id"] = contract.get("contract_id")
    results["outputs"] = [
        {"output_id": o.get("output_id"),
         "claim": o.get("claim"),
         "subject_ref": o.get("subject_ref"),
         "requirements": o.get("evidence_requirements"),
         "facets": o.get("postcondition_facets"),
         "satisfaction": o.get("satisfaction"),
         "polarity": o.get("polarity"),
         "detail": o.get("detail")}
        for o in outputs]

    plan_steps = [(s.get("step_id"), s.get("type"), s.get("status"))
                  for s in activity.get("plan_steps", [])]
    results["plan_steps"] = plan_steps

    response_text = detail.get("task_response_text") or ""
    results["response_text"] = response_text

    # --- acceptance assertions ------------------------------------------
    a = {}

    # 1) contract shape: three outputs — metric semantics, hedging
    #    existence, why-explanation
    a["contract_outputs_3"] = len(outputs) == 3
    claims = {o.get("output_id"): (o.get("claim") or "") for o in outputs}
    a["o1_metric_claim"] = any("反向交易率" in c for c in claims.values())
    a["o3_why_claim"] = any("被标记为风险" in c for c in claims.values())

    # 2) coverage guarantee: the plan must carry a legal step for the
    #    why-explanation output (explain_signal) alongside the analysis,
    #    and that step must actually EXECUTE (its outcome is the finding's
    #    honest capability boundary — unsupported is a recorded attempt,
    #    never a silent omission)
    types = [t for _, t, _ in plan_steps]
    a["plan_has_analysis"] = "analyze_cross_account_trades" in types
    a["plan_has_explain_step"] = "explain_signal" in types
    started = [e for e in activity.get("activity", [])
               if e.get("event_type") == "step.started"]
    a["explain_step_ran"] = any(
        e.get("step_type") == "explain_signal" for e in started)

    # 3) tool evidence: executed tool calls read from the Activity event
    #    log (the /tasks/{id}/results read model does not mirror loop-path
    #    tool calls — pre-existing asymmetry, verified separately)
    called = [e for e in activity.get("activity", [])
              if e.get("event_type") == "tool.called"]
    tool_names = [e.get("tool_name") for e in called]
    a["analysis_tool_executed"] = "cross_account_trade_analysis" in tool_names
    a["signal_explain_tool_executed"] = "signal_explain" in tool_names

    # 4) per-output satisfaction: metric satisfied; hedging determinate;
    #    why-output honestly NOT satisfied (capability boundary) and the
    #    overall outcome is honest partial — never a false complete
    sat = {o.get("output_id"): o.get("satisfaction") for o in outputs}
    a["o1_metric_satisfied"] = sat.get("O1") == "satisfied"
    a["o2_hedging_determinate"] = sat.get("O2") == "satisfied"
    a["o3_honest_not_satisfied"] = sat.get("O3") in (
        "pending", "unresolved", "not_satisfied", "unsupported")
    a["outcome_partial_honest"] = activity.get("goal_outcome") == "partial"

    # 5) the response MUST answer the metric definition from the account's
    #    own metric semantics — the cross-account zero is a DIFFERENT
    #    deliverable and never substitutes for the definition
    a["metric_defined_as_own_ratio"] = (
        "账户自身的反向交易比例" in response_text
        and "min(BUY, SELL)" in response_text)
    a["metric_not_cross_account"] = "不是跨账户匹配率" in response_text
    a["hedging_answered_separately"] = (
        ("未发现" in response_text and "对冲交易候选" in response_text)
        or "跨账户" in response_text)

    results["assertions"] = a
    results["PASS"] = all(a.values())

    # 6) DB-level verification of the planning coverage record (the API
    #    projection intentionally omits it): every accepted output must
    #    carry a coverage verdict of `covered` after the completion
    import sqlite3
    db = sqlite3.connect(
        "/Users/vv/investigation-agent-client/backend/data/"
        "investigation_agent_v2.db")
    row = db.execute(
        "SELECT document FROM agent_states WHERE task_id=?",
        (task_id,)).fetchone()
    if row:
        cov = {r["output_id"]: r["coverage_status"]
               for r in json.loads(row[0]).get("planning_coverage", [])}
        results["planning_coverage_db"] = cov
        a["coverage_record_all_covered"] = bool(cov) and \
            all(v == "covered" for v in cov.values())
        results["PASS"] = all(a.values())

    with open("/tmp/bchain_u00005_api_e2e.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "response_text"}, ensure_ascii=False, indent=2))
    print("--- RESPONSE ---")
    print(response_text)
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
    with open("/tmp/bchain_u00005_api_e2e.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(1)

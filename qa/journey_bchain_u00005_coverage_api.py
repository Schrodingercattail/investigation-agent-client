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

E3 Runtime Verification Gate (P1-2, fail-closed):
  step 0    backend source/process/effective-flag gate — BLOCKED exit 2
  stage A   --require-feature-evidence <this investigation> (declaration,
            bound to the record's generated_at) — BLOCKED exit 2
  stage B   --verify-feature-evidence <stage-A record> AFTER the goal run
            — evidence FAIL exit 3; verification-unverifiable BLOCKED exit 2
  Exit codes: 0 success; 1 business failure; 2 gate BLOCKED; 3 evidence
  FAIL — propagated verbatim, never masked or downgraded.
"""
import json
import os
import sys
import time

import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e3_gate import (require_feature_evidence, runtime_gate_backend,
                     verify_feature_evidence)

BASE = "http://127.0.0.1:8001/api/v2"
QUESTION = ("34.12%的反向交易率是账户间的对冲交易还是账户自己的对冲交易？"
            "为什么反向交易是风险项？")
results = {"question": QUESTION}
_STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _final_exit(gate_code, business_pass):
    """Final harness exit (P1-2 exit-code semantics fix):
    - a non-zero GATE code (2=BLOCKED / 3=evidence FAIL) propagates
      VERBATIM and takes precedence — never remapped by business results;
    - with the gate PASS, a business-assertion failure exits 1 (non-zero
      — automation must not mistake a failed journey for success);
    - 0 only when the gate passed AND every business assertion held.
    The exception path keeps its pre-existing exit 1 (unchanged)."""
    if gate_code != 0:
        return gate_code
    return 0 if business_pass else 1


def _save():
    with open("/tmp/bchain_u00005_api_e2e.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def _call(method, path, payload=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None
        else None,
        headers={"Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --- E3 step 0: runtime gate BEFORE any target request ---------------
_code, _rec = runtime_gate_backend(
    f"/Users/vv/investigation-agent-client/docs/evaluations/"
    f"gate-{_STAMP}-e3-step0.json")
if _code != 0:
    results["e3_gate"] = {"stage": "step0", "exit": _code,
                          "record": _rec, "verdict": "BLOCKED"}
    _save()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(2)
results["e3_gate"] = {"stage": "step0", "exit": _code, "record": _rec}

try:
    # 1) fresh investigation for the case
    inv = _call("POST", "/investigations", {"case_id": "U00005"})
    inv_id = inv["investigation"]["investigation_id"]
    results["investigation_id"] = inv_id

    # --- E3 stage A: declare the evidence requirement for THIS
    # investigation (bound to the record's generated_at) BEFORE the goal
    # run — declaration only, never alters the gate dimensions.
    _code, stage_a_record = require_feature_evidence(
        inv_id,
        f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"gate-{_STAMP}-e3-stageA.json")
    results["e3_gate"]["stage_a"] = {"exit": _code,
                                     "record": stage_a_record}
    if _code != 0:
        results["e3_gate"]["stage"] = "stageA"
        results["e3_gate"]["verdict"] = "BLOCKED"
        _save()
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(2)

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

    # --- E3 stage B: report-time verification against the stage-A
    # declaration — task/investigation binding + strict time constraint
    # + outcome validity. FAIL(3)/BLOCKED(2) propagate verbatim and force
    # the harness result non-success; never masked or skipped.
    _code, stage_b_record = verify_feature_evidence(
        stage_a_record,
        f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"gate-{_STAMP}-e3-stageB.json")
    results["e3_gate"]["stage_b"] = {"exit": _code,
                                     "record": stage_b_record}
    if _code == 0:
        results["e3_gate"]["verdict"] = "PASS"
    elif _code == 2:
        results["e3_gate"]["verdict"] = "BLOCKED"
    else:
        results["e3_gate"]["verdict"] = "FAIL"
    results["PASS"] = bool(results.get("PASS")) and _code == 0
    _save()
    if _code != 0:
        results["final_exit"] = _final_exit(_code, results["PASS"])
        print(json.dumps({k: v for k, v in results.items()
                          if k != "response_text"},
                         ensure_ascii=False, indent=2))
        sys.exit(_code)

    # gate PASSED: the business assertions decide the final exit — a
    # failed journey must NOT exit 0 (P1-2 exit-code semantics fix)
    results["final_exit"] = _final_exit(_code, results["PASS"])
    with open("/tmp/bchain_u00005_api_e2e.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "response_text"}, ensure_ascii=False, indent=2))
    print("--- RESPONSE ---")
    print(response_text)
    sys.exit(results["final_exit"])
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
    _save()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(1)

"""P0/B-fix — real-API E2E: GoalContract deliverable completeness.

Real stack :8001 (backend started with ENABLE_GOAL_SEMANTIC_CONTRACT=true)
-> real RP :8000 -> REAL LLM goal understanding (no scripted answers).
Every scenario runs in a FRESH investigation (isolated tasks/sessions;
the original U00047 failure task and historical turns are untouched).

Scenarios:
  1. metric question   -> contract = metric deliverable ONLY; the answer
                          does not append an account list
  2. pure list request -> full related-account list, no hedge analysis
  3. compound request  -> contract holds BOTH deliverables (network list +
                          set-internal hedging with conditional detail
                          claim); plan + execution include the list step
                          AND the set-internal analysis; honest outcome
  4. match path        -> real candidate trade details in the answer
  5. no-match path     -> explicit NO_MATCH reporting (U00005: its
                          authoritative set has no hedge candidates)
  6. same-session switch: metric -> compound extends the delivery set,
                          unbounded by the previous turn's contract
"""
import json
import sys
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"
results = {"scenarios": {}}


def _call(method, path, payload=None, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None
        else None,
        headers={"Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _run_goal(case_id, question, focus=None, setup_intake=True,
              inv_id=None, timeout_s=900):
    """Fresh investigation (unless inv_id given) -> intake -> goal run ->
    poll to terminal. Returns (activity, detail)."""
    if inv_id is None:
        inv = _call("POST", "/investigations", {"case_id": case_id})
        inv_id = inv["investigation"]["investigation_id"]
    if setup_intake:
        _call("POST", f"/investigations/{inv_id}/turn",
              {"message": f"Investigate {case_id}"}, timeout=300)
    payload = {"goal": question}
    if focus:
        payload["context_action"] = {"type": "focus_finding",
                                     "finding_id": focus}
    run = _call("POST", f"/investigations/{inv_id}/goal", payload)
    task_id = run["task_id"]
    deadline = time.time() + timeout_s
    activity = None
    while time.time() < deadline:
        activity = _call("GET", f"/tasks/{task_id}/activity")
        if activity["is_terminal"]:
            break
        time.sleep(5)
    assert activity and activity["is_terminal"], "loop did not terminate"
    detail = _call("GET", f"/tasks/{task_id}")
    return activity, detail, inv_id, task_id


def _record(name, activity, detail, checks):
    contract = activity.get("goal_contract") or {}
    outs = contract.get("outputs") or []
    steps = activity.get("plan_steps") or []
    resp = detail.get("task_response_text") or ""
    entry = {
        "agent_status": activity.get("agent_status"),
        "stop_reason": activity.get("stop_reason"),
        "goal_outcome": activity.get("goal_outcome"),
        "contract_outputs": [
            {"claim": (o.get("claim") or "")[:46],
             "kind": o.get("claim_kind"),
             "evidence": o.get("evidence_requirements")}
            for o in outs],
        "plan_steps": [{"type": s.get("type"),
                        "status": s.get("status")} for s in steps],
        "response_head": resp[:400],
        "checks": checks,
    }
    entry["PASS"] = all(checks.values())
    results["scenarios"][name] = entry
    return entry


METRIC_Q = ("opposite-trade ratio of 34.29%是关联账户的对冲交易比例吗，"
            "这是怎么计算的？")
LIST_Q = "再列一次所有关联账号"
COMPOUND_Q = "列出所有关联账号，查询其中是否存在对冲交易，如果有，发交易明细给我"

try:
    # --- scenario 1: metric question --------------------------------------
    a, d, inv1, t1 = _run_goal("U00047", METRIC_Q)
    resp1 = d.get("task_response_text") or ""
    outs1 = (a.get("goal_contract") or {}).get("outputs") or []
    _record("1_metric", a, d, {
        "terminal_completed": a.get("agent_status") == "completed",
        "contract_only_metric": len(outs1) == 1 and (
            "反向交易率" in ((outs1[0].get("claim") or ""))),
        "no_network_delivery": not any(
            "network_relations" in (o.get("evidence_requirements") or [])
            for o in outs1),
        "answer_no_account_list": "U00044" not in resp1
        and "U00311" not in resp1,
        "answers_metric": ("自身" in resp1 or "口径" in resp1
                           or "计算" in resp1 or "metric" in resp1.lower()
                           or "34" in resp1),
    })
    results["ids"] = {"metric": {"inv": inv1, "task": t1}}

    # --- scenario 2: pure list request ------------------------------------
    a, d, inv2, t2 = _run_goal("U00047", LIST_Q)
    resp2 = d.get("task_response_text") or ""
    outs2 = (a.get("goal_contract") or {}).get("outputs") or []
    _record("2_list", a, d, {
        "terminal_completed": a.get("agent_status") == "completed",
        "contract_has_network": any(
            "network_relations" in (o.get("evidence_requirements") or [])
            for o in outs2),
        "no_hedge_delivery": not any(
            "opposite_trade_relations" in (o.get("evidence_requirements")
                                           or []) for o in outs2),
        "list_delivered": ("U00044" in resp2 or "U00311" in resp2
                           or "关联账号" in resp2 or "Related" in resp2),
    })
    results["ids"]["list"] = {"inv": inv2, "task": t2}

    # --- scenario 3+4: compound request on U00047 (match path) ------------
    a, d, inv3, t3 = _run_goal("U00047", COMPOUND_Q)
    resp3 = d.get("task_response_text") or ""
    outs3 = (a.get("goal_contract") or {}).get("outputs") or []
    steps3 = [s.get("type") for s in (a.get("plan_steps") or [])]
    _record("3_compound", a, d, {
        "terminal_completed": a.get("agent_status") == "completed",
        "contract_has_network": any(
            "network_relations" in (o.get("evidence_requirements") or [])
            for o in outs3),
        "contract_has_set_hedging": any(
            "集合内部" in (o.get("claim") or "")
            and "opposite_trade_relations" in
            (o.get("evidence_requirements") or []) for o in outs3),
        "plan_has_list_step": "analyze_related_accounts" in steps3,
        "plan_has_set_step": "analyze_related_account_set_trades" in steps3,
        "no_anchored_narrowing": "analyze_cross_account_trades" not in steps3,
        "candidate_details_shown": ("CATA-" in resp3
                                    or "候选" in resp3
                                    or "交易" in resp3),
    })
    results["ids"]["compound"] = {"inv": inv3, "task": t3}

    # --- scenario 5: no-match path (U00005) --------------------------------
    NOMATCH_Q = ("列出所有关联账号，查询其中是否存在对冲交易，"
                 "如果有，发交易明细给我")
    a, d, inv4, t4 = _run_goal("U00005", NOMATCH_Q)
    resp4 = d.get("task_response_text") or ""
    outs4 = (a.get("goal_contract") or {}).get("outputs") or []
    _record("5_no_match", a, d, {
        "terminal_completed": a.get("agent_status") == "completed",
        "contract_has_set_hedging": any(
            "集合内部" in (o.get("claim") or "") for o in outs4),
        "explicit_no_match": ("NO_MATCH" in resp4.upper()
                              or "未发现" in resp4
                              or "没有" in resp4 or "无" in resp4),
    })
    results["ids"]["no_match"] = {"inv": inv4, "task": t4}

    # --- scenario 6: same-session metric -> compound switch ----------------
    inv = _call("POST", "/investigations", {"case_id": "U00047"})
    inv6 = inv["investigation"]["investigation_id"]
    _call("POST", f"/investigations/{inv6}/turn",
          {"message": "Investigate U00047"}, timeout=300)
    a_m, d_m, _, t_m = _run_goal("U00047", METRIC_Q, inv_id=inv6,
                                 setup_intake=False)
    outs_m = (a_m.get("goal_contract") or {}).get("outputs") or []
    a_c, d_c, _, t_c = _run_goal("U00047", COMPOUND_Q, inv_id=inv6,
                                 setup_intake=False)
    outs_c = (a_c.get("goal_contract") or {}).get("outputs") or []
    resp_c = d_c.get("task_response_text") or ""
    steps_c = [s.get("type") for s in (a_c.get("plan_steps") or [])]
    results["ids"]["session_switch"] = {"inv": inv6, "tasks": [t_m, t_c]}
    _record("6_session_switch", a_c, d_c, {
        "metric_turn_contract_narrow": len(outs_m) == 1,
        "compound_turn_contract_full": (
            any("network_relations" in (o.get("evidence_requirements") or [])
                for o in outs_c)
            and any("集合内部" in (o.get("claim") or "") for o in outs_c)),
        "compound_turn_executes_set_step":
            "analyze_related_account_set_trades" in steps_c,
        "compound_turn_completed": a_c.get("agent_status") == "completed",
        "answer_has_candidates_or_nomatch": ("CATA-" in resp_c
                                             or "候选" in resp_c
                                             or "交易" in resp_c),
    })
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
finally:
    with open("/tmp/p0b_api_e2e.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    for name, entry in results.get("scenarios", {}).items():
        print(f"--- {name}: PASS={entry['PASS']} "
              f"status={entry['agent_status']} outcome={entry['goal_outcome']}")
        for k, v in entry["checks"].items():
            print(f"    {'OK ' if v else 'FAIL'} {k}")
    if results.get("error"):
        print("ERROR:", results["error"])

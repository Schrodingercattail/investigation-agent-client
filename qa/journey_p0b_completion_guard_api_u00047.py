"""P0/B-closure — real-API E2E: completion-state protection under a
degraded contract.

Backend started with ENABLE_GOAL_SEMANTIC_CONTRACT=true and CONTROLLED
FAULT INJECTION (LLM_FAULT_SKIP_FIRST=1 LLM_FAULT_EMPTY_FIRST=2): call #1
= intake (healthy), calls #2/#3 = the goal-understanding model's two
bounded attempts — both injected empty, the A-fix retry exhausts, and
the run falls back to the deterministic candidate contract. This is
controlled fault injection at the LLM boundary, NOT a real outage.

Scenario 1 (compound request): the fallback keeps only the recognized
list deliverable — the system may deliver the list but must NOT claim
the compound request fully answered (GoalOutcome != complete, honest
disclosure, no fabricated hedge execution).
Scenario 2 (pure list request): the same degraded path still delivers
the list (degraded capability preserved); outcome is partial with
disclosure (no mechanical proof of closed intent).
"""
import json
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"
COMPOUND_Q = "列出所有关联账号，查询其中是否存在对冲交易，如果有，发交易明细给我"
LIST_Q = "再列一次所有关联账号"
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


def _run(case_id, question):
    inv = _call("POST", "/investigations", {"case_id": case_id})
    inv_id = inv["investigation"]["investigation_id"]
    _call("POST", f"/investigations/{inv_id}/turn",
          {"message": f"Investigate {case_id}"}, timeout=300)
    run = _call("POST", f"/investigations/{inv_id}/goal",
                {"goal": question})
    task_id = run["task_id"]
    deadline = time.time() + 600
    activity = None
    while time.time() < deadline:
        activity = _call("GET", f"/tasks/{task_id}/activity")
        if activity["is_terminal"]:
            break
        time.sleep(5)
    assert activity and activity["is_terminal"]
    detail = _call("GET", f"/tasks/{task_id}")
    return activity, detail, inv_id, task_id


try:
    # --- scenario 1: compound request, intent review fails -----------------
    a, d, inv1, t1 = _run("U00047", COMPOUND_Q)
    resp1 = d.get("task_response_text") or ""
    outs1 = (a.get("goal_contract") or {}).get("outputs") or []
    steps1 = [s.get("type") for s in (a.get("plan_steps") or [])]
    intent1 = (a.get("goal_contract") or {}).get("intent_source")
    results["scenarios"]["1_compound_degraded"] = {
        "task": t1, "inv": inv1,
        "agent_status": a.get("agent_status"),
        "stop_reason": a.get("stop_reason"),
        "goal_outcome": a.get("goal_outcome"),
        "intent_source": intent1,
        "contract_outputs": [o.get("claim", "")[:36] for o in outs1],
        "plan_steps": steps1,
        "checks": {
            "terminal_completed": a.get("agent_status") == "completed",
            "degraded_basis_recorded": intent1 == "heuristic",
            "list_delivered": any(
                "network_relations" in (o.get("evidence_requirements")
                                        or []) for o in outs1)
            and ("U00043" in resp1 or "关联账号" in resp1
                 or "Related" in resp1),
            "outcome_not_complete": a.get("goal_outcome") != "complete",
            "honest_partial_language":
                ("部分" in resp1 or "无法确认" in resp1
                 or "partial" in resp1.lower()),
            "disclosure_present": "无法确认已覆盖您请求的全部内容" in resp1
            or "确定性识别" in resp1,
            "no_fabricated_hedge": ("CATA-" not in resp1
                                    and "候选对冲" not in resp1
                                    and "集合内部" not in resp1),
        },
    }
    s1 = results["scenarios"]["1_compound_degraded"]
    s1["PASS"] = all(s1["checks"].values())

    # --- scenario 2: pure list request, same degraded path -----------------
    a, d, inv2, t2 = _run("U00047", LIST_Q)
    resp2 = d.get("task_response_text") or ""
    outs2 = (a.get("goal_contract") or {}).get("outputs") or []
    results["scenarios"]["2_list_degraded"] = {
        "task": t2, "inv": inv2,
        "agent_status": a.get("agent_status"),
        "goal_outcome": a.get("goal_outcome"),
        "intent_source": (a.get("goal_contract") or {}).get("intent_source"),
        "checks": {
            "terminal_completed": a.get("agent_status") == "completed",
            "list_delivered": any(
                "network_relations" in (o.get("evidence_requirements")
                                        or []) for o in outs2)
            and ("U00043" in resp2 or "关联账号" in resp2
                 or "Related" in resp2),
            "degraded_basis_recorded": (a.get("goal_contract")
                                        or {}).get("intent_source")
            == "heuristic",
            "outcome_not_complete": a.get("goal_outcome") != "complete",
            "disclosure_present": "无法确认已覆盖您请求的全部内容" in resp2,
        },
    }
    s2 = results["scenarios"]["2_list_degraded"]
    s2["PASS"] = all(s2["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
finally:
    with open("/tmp/p0b_guard_api.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    for name, entry in results.get("scenarios", {}).items():
        print(f"--- {name}: PASS={entry.get('PASS')} "
              f"outcome={entry.get('goal_outcome')} "
              f"intent={entry.get('intent_source')}")
        for k, v in entry.get("checks", {}).items():
            print(f"    {'OK ' if v else 'FAIL'} {k}")
    if results.get("error"):
        print("ERROR:", results["error"])

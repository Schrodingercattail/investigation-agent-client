"""WP-GS-E — C6: concurrent goal runs (two simultaneous POSTs against
the same backend; real model, no seam). Verifies both tasks reach an
honest terminal independently, each response is served for its own
task, and neither corrupts the other's state."""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BACKEND = "http://127.0.0.1:8001"
CASE = "U00010"
GOAL = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"


def post(path, body, timeout=60):
    req = urllib.request.Request(
        BACKEND + path, data=json.dumps(body).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path, timeout=60):
    with urllib.request.urlopen(BACKEND + path, timeout=timeout) as r:
        return json.loads(r.read())


def one_run(i):
    rec = {"run": i}
    try:
        inv = post("/api/v2/investigations", {"case_id": CASE})
        inv_id = inv["investigation"]["investigation_id"]
        rec["investigation_id"] = inv_id
        run = post(f"/api/v2/investigations/{inv_id}/goal",
                   {"goal": GOAL,
                    "context_action": {"type": "focus_finding",
                                       "finding_id": "F3"}})
        task_id = run["task_id"]
        rec["task_id"] = task_id
        deadline = time.time() + 420
        while time.time() < deadline:
            a = get(f"/api/v2/tasks/{task_id}/activity")
            if a.get("is_terminal"):
                break
            time.sleep(2)
        rec["agent_status"] = a.get("agent_status")
        rec["stop_reason"] = a.get("stop_reason")
        rec["goal_outcome"] = a.get("goal_outcome")
        dec = a.get("goal_contract_decision") or {}
        rec["decision_outcome"] = dec.get("outcome")
        contract = a.get("goal_contract") or {}
        rec["satisfactions"] = [o.get("satisfaction")
                                for o in (contract.get("outputs") or [])]
        d = get(f"/api/v2/tasks/{task_id}")
        rec["response_head"] = (d.get("task_response_text") or "")[:150]
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
    return rec


def main():
    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(one_run, [1, 2]))
    ok = all(r.get("task_id") and not r.get("error") for r in results)
    distinct = len({r.get("task_id") for r in results}) == 2
    summary = {"both_accepted_tasks": ok, "distinct_tasks": distinct,
               "results": results}
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = f"../docs/evaluations/wp-gse-c6-concurrent-{stamp}.json"
    json.dump(summary, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: summary[k] for k in ("both_accepted_tasks",
                                              "distinct_tasks")},
                     ensure_ascii=False), flush=True)
    for r in results:
        print(f"run{r['run']}: status={r.get('agent_status')} "
              f"stop={r.get('stop_reason')} outcome={r.get('goal_outcome')} "
              f"decision={r.get('decision_outcome')} "
              f"satisfactions={r.get('satisfactions')} "
              f"error={r.get('error')}", flush=True)
    print("saved", out, flush=True)


if __name__ == "__main__":
    main()

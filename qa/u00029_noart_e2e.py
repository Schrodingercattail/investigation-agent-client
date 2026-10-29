"""U00029 NOART — unsolicited-artifact authorization REAL API E2E.

Real LLM + real RP through the real submit entry:

  NEG-EN  "list all linked accounts and check if there is any hedge
           transaction within the linked accounts. If any, please send
           me the transaction details."          → NO artifact created
  NEG-ZH  「列出所有关联账号，查询账号中是否有对冲交易，如果有，
           列出交易明细。」                       → NO artifact created
  POS-EN  same + "generate an artifact"          → artifact CREATED and its
           persisted content contains the hedge candidates

Artifact state verified through GET /tasks/{id} (task artifact_ids) and
GET /tasks/{id}/artifacts — not through the UI.
"""
import json
import sys
import time

import httpx

BACKEND = "http://localhost:8001"
results = {"checks": {}, "meta": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:140])


def create():
    return httpx.post(f"{BACKEND}/api/v2/investigations",
                      json={"message": "Investigate U00029"},
                      timeout=60).json()


def submit(inv_id, message):
    return httpx.post(f"{BACKEND}/api/v2/investigations/{inv_id}/submit",
                      json={"message": message},
                      timeout=180).json()["task_id"]


def wait_terminal(task_id, timeout=1500):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}",
                      timeout=30).json()
        resp = d.get("task_response_text") or ""
        if resp and d["task"]["status"] in ("completed", "failed"):
            return d
        time.sleep(5)
    raise TimeoutError(task_id)


def artifact_state(task_id):
    d = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}", timeout=30).json()
    ids = [a["artifact_id"] for a in (d.get("artifacts") or [])]
    content = ""
    if ids:
        r = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}/artifacts",
                      timeout=30).json()
        for x in (r.get("artifacts") or []):
            if x.get("artifact_id") == ids[-1]:
                content = x.get("content") or ""
    return ids, content


def run_case(label, message, want_artifact):
    print(f"=== {label} ===")
    inv = create()
    inv_id = inv["investigation"]["investigation_id"]
    results["meta"][f"{label}_inv"] = inv_id
    tid = submit(inv_id, inv["intake_message"])
    wait_terminal(tid)                     # intake (auto-bundle contract)
    tid = submit(inv_id, message)
    d = wait_terminal(tid)
    task_ids, content = artifact_state(tid)
    resp = d.get("task_response_text") or ""
    results["meta"][f"{label}_resp_head"] = resp[:300]
    results["meta"][f"{label}_artifacts"] = task_ids
    hedge_ran = ("analyze_related_accounts" in resp
                 or "关联账号" in resp)
    print("artifacts:", task_ids, "| hedge results in answer:", hedge_ran)

    if want_artifact:
        check(f"{label}_1_analysis_ran", hedge_ran, resp[:150])
        check(f"{label}_2_artifact_created", bool(task_ids), task_ids)
        check(f"{label}_3_artifact_has_candidates",
              "## Candidate Cross-Account Relationships" in content,
              content[:200])
    else:
        check(f"{label}_1_analysis_ran", hedge_ran, resp[:150])
        check(f"{label}_2_no_artifact_persisted",
              d["task"]["status"] == "completed" and not task_ids,
              f"status={d['task']['status']} artifacts={task_ids}")
        check(f"{label}_3_answer_has_results",
              ("关联账号" in resp or "candidate" in resp.lower()
               or "对冲" in resp), resp[:200])


run_case("NEG_EN",
         "list all linked accounts and check if there is any hedge "
         "transaction within the linked accounts. If any, please send "
         "me the transaction details.", want_artifact=False)
run_case("NEG_ZH",
         "列出所有关联账号，查询账号中是否有对冲交易，如果有，"
         "列出交易明细。", want_artifact=False)
run_case("POS_EN",
         "list all the linked accounts and check if there is any hedge "
         "transaction within linked accounts. If hedge trades exist, "
         "please send the details to me and generate an artifact.",
         want_artifact=True)

ok = all(v["ok"] for v in results["checks"].values())
results["PASS"] = ok
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"u00029-noart-e2e-{stamp}.json")
with open(path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("results →", path)
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

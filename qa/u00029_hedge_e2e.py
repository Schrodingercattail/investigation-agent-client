"""U00029 manual-acceptance regression — REAL API E2E (real LLM, real RP).

POST /api/v2/investigations/{id}/submit through the REAL unified entry:

  EN  "list all the linked accounts and check if there is any hedge
       transaction within linked accounts. If hedge trades exist, please
       send the details to me and generate an artifact."
  ZH  「列出所有关联账号，查询账号中是否有对冲交易，如果有，列出交易明细
       并生成一份文件」
  CTRL plain 「列出该账户的交易明细并生成一份文件」 (must stay plain)

Per case: goal contract / executed tools / completion honesty / artifact id
AND THE ARTIFACT'S PERSISTED CONTENT — the hedge candidates' account pairs
and transaction ids must trace to the run's OWN analysis observation.
"""
import json
import re
import sys
import time

import httpx

BACKEND = "http://localhost:8001"
results = {"checks": {}, "meta": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:150])


def create(message):
    return httpx.post(f"{BACKEND}/api/v2/investigations",
                      json={"message": message}, timeout=30).json()


def submit(inv_id, message):
    r = httpx.post(f"{BACKEND}/api/v2/investigations/{inv_id}/submit",
                   json={"message": message}, timeout=180)
    r.raise_for_status()
    return r.json()["task_id"]


def wait_terminal(task_id, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}/activity",
                      timeout=30).json()
        if a.get("is_terminal"):
            return a
        time.sleep(5)
    raise TimeoutError(task_id)


def run_case(label, message, want_hedge=True):
    print(f"=== {label} ===")
    inv = create("Investigate U00029")
    inv_id = inv["investigation"]["investigation_id"]
    results["meta"][f"{label}_inv"] = inv_id
    tid = submit(inv_id, message)
    activity = wait_terminal(tid)
    tools = sorted({o["tool_name"] for o in activity["activity"]
                    if o["event_type"] == "tool.called"})
    stop = activity["stop_reason"]
    print("tools:", tools, "| stop:", stop)

    detail = httpx.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=30).json()
    answer = detail.get("task_response_text") or ""
    arts = detail.get("artifacts") or []
    art_id = arts[-1]["artifact_id"] if arts else None
    content = ""
    if art_id:
        r = httpx.get(f"{BACKEND}/api/v2/tasks/{tid}/artifacts",
                      timeout=30).json()
        arts2 = r.get("artifacts") or []
        art = next((x for x in arts2
                    if x.get("artifact_id") == art_id), None)
        content = (art or {}).get("content") or ""
    results["meta"][f"{label}_artifact"] = art_id
    results["meta"][f"{label}_content_head"] = content[:400]
    results["meta"][f"{label}_answer_head"] = answer[:500]

    # traceability oracle: the artifact's OWN candidate table — each row's
    # account pair AND its transaction ids must also appear in the SAME
    # run's answer text (both surfaces derive from the one analysis
    # observation; artifact ↔ answer coherence, language-independent via
    # the tx ids)
    art_rows = []
    in_cand = False
    for ln in content.split("\n"):
        if ln.startswith("## Candidate Cross-Account Relationships"):
            in_cand = True
            continue
        if in_cand and ln.startswith("## "):
            in_cand = False
        if in_cand and ln.startswith("| ") and not ln.startswith("|--"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if (len(cells) >= 12 and cells[2] and cells[4]
                    and cells[1] != "Type" and cells[2] != "Account A"):
                txids = [t.strip() for t in cells[11].split(",")
                         if t.strip()]
                art_rows.append({"cid": cells[1], "a": cells[2],
                                 "b": cells[4], "txids": txids})
    results["meta"][f"{label}_artifact_rows"] = art_rows

    if want_hedge:
        check(f"{label}_1_analysis_executed",
              "related_account_analysis" in tools
              and "cross_account_trade_analysis" in tools, tools)
        check(f"{label}_2_honest_completion",
              stop in ("goal_satisfied", "bounded_data"), stop)
        check(f"{label}_3_artifact_created", bool(art_id), art_id)
        check(f"{label}_4_artifact_candidates_section",
              "## Candidate Cross-Account Relationships" in content,
              content[:200])
        check(f"{label}_5_answer_names_both_sides",
              all(r["a"] in answer and r["b"] in answer
                  for r in art_rows) and bool(art_rows),
              f"rows={len(art_rows)}")
        missing = [f'{r["cid"]}:{t}' for r in art_rows
                   for t in r["txids"] if t not in answer]
        check(f"{label}_6_txids_traceable", not missing,
              f"missing={missing[:6]}")
    else:
        check(f"{label}_1_plain_tools",
              "finding_drilldown" in tools
              and "analyze_related_account_set_trades" not in tools, tools)
        check(f"{label}_2_artifact_plain",
              bool(art_id) and
              "## Candidate Cross-Account Relationships" not in content,
              art_id)
    return content


run_case("EN",
         "list all the linked accounts and check if there is any hedge "
         "transaction within linked accounts. If hedge trades exist, "
         "please send the details to me and generate an artifact.",
         want_hedge=True)
run_case("ZH",
         "列出所有关联账号，查询账号中是否有对冲交易，如果有，列出交易明细"
         "并生成一份文件", want_hedge=True)
run_case("CTRL", "列出该账户的交易明细并生成一份文件", want_hedge=False)

ok = all(v["ok"] for v in results["checks"].values())
results["PASS"] = ok
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"u00029-hedge-e2e-{stamp}.json")
with open(path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("results →", path)
print("checks:", {k: v["ok"] for k, v in results["checks"].items()})
sys.exit(0 if ok else 1)

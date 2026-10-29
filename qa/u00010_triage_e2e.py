"""U00010 F1–F7 triage repairs — REAL API E2E (real LLM, real RP).

Real submit entry, per journey:

  F1  zh compound dual-criteria + dual-file → per-finding criteria
      answered from rule evidence; records delivered; honest file state
  F3  zh explicit policy-citation ask → policy citation original text
      (document/section/snippet) or bounded unavailable
  F4  zh hedge + details + file → candidates + persisted artifact with
      the candidate table
  F5  F6 focused policy chip-path → associated citation rendered
  F6  F6 focused explain → scoped counts labeled
  F7  F6 focused timeline → scoped timeline events or bounded limitation
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


# Phase-1 (2026-10-21): acceptance semantics — a section that says the
# goal is STILL unresolved must never satisfy a check that demands the
# goal answered. These markers are the runtime's real unresolved faces
# (EN + zh); the old checks passed on the EN marker alone and read the
# zh 「该项尚未产生可判定的结果」 answers as success.
UNRESOLVED_MARKERS = (
    "No determinable result",
    "尚未产生可判定的结果",
    "当前证据不足以确定该项",
    "无法回答该请求",
)


def has_unresolved(text):
    return any(m in text for m in UNRESOLVED_MARKERS)


def create(case):
    return httpx.post(f"{BACKEND}/api/v2/investigations",
                      json={"message": f"Investigate {case}"},
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


def artifacts(task_id):
    r = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}/artifacts",
                  timeout=30).json()
    return [(a.get("artifact_id"), a.get("content") or "")
            for a in (r.get("artifacts") or [])]


def focus_and_ask(case, focus_finding, ask, label, checks):
    inv = create(case)
    inv_id = inv["investigation"]["investigation_id"]
    submit(inv_id, inv["intake_message"])
    body = {"message": ask}
    if focus_finding:
        body["context_action"] = {"type": "focus_finding",
                                  "finding_id": focus_finding}
    tid = httpx.post(f"{BACKEND}/api/v2/investigations/{inv_id}/submit",
                     json=body, timeout=180).json()["task_id"]
    d = wait_terminal(tid)
    resp = d.get("task_response_text") or ""
    ids, content = artifacts_of(tid)
    check(f"{label}_inv", True, inv_id)
    for name, ok, detail in checks(resp, ids, content):
        check(f"{label}_{name}", ok, detail)
    return inv_id, tid, resp, ids, content


def artifacts_of(task_id):
    d = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}", timeout=30).json()
    return [a["artifact_id"] for a in (d.get("artifacts") or [])]


def artifacts_of(task_id):
    d = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}", timeout=30).json()
    return [(a["artifact_id"], a.get("content") or "")
            for a in (d.get("artifacts") or [])]


def focus_and_ask(case, focus_finding, ask, label, checks):
    inv = create(case)
    inv_id = inv["investigation"]["investigation_id"]
    submit(inv_id, inv["intake_message"])
    body = {"message": ask}
    if focus_finding:
        body["context_action"] = {"type": "focus_finding",
                                  "finding_id": focus_finding}
    tid = httpx.post(f"{BACKEND}/api/v2/investigations/{inv_id}/submit",
                     json=body, timeout=180).json()["task_id"]
    d = wait_terminal(tid)
    resp = d.get("task_response_text") or ""
    ids_content = artifacts_of(tid)
    check(f"{label}_inv", True, inv_id)
    for name, ok, detail in checks(resp, ids_content):
        check(f"{label}_{name}", ok, detail)
    return inv_id, tid, resp, ids_content


# --- F1: zh compound dual-criteria + dual-file ------------------------------

def f1_checks(resp, ids_content):
    def c(name, ok, detail=""):
        return (name, ok, detail)
    return [
        c("O1_criteria_F3", ("withdrawal frequency" in resp.lower()
                             or "提现频率" in resp or "高频提现" in resp),
          resp[:150]),
        c("O2_criteria_F2", ("new account" in resp.lower()
                             or "新账号" in resp), resp[:150]),
    ]


print("=== F1: zh compound dual-criteria + dual-file ===")
inv = create("U00010")
inv_id = inv["investigation"]["investigation_id"]
results["meta"]["F1_inv"] = inv_id
submit(inv_id, inv["intake_message"])
ask = ("高频提现和新账号高频活动的判定条件分别是什么？"
       "给我生成提现和交易明细文件")
tid = submit(inv_id, ask)
d = wait_terminal(tid)
resp = d.get("task_response_text") or ""
results["meta"]["F1_resp"] = resp[:800]
ids_content = artifacts_of(tid)
# Phase-1 semantic acceptance (§7 F1): BOTH criteria answered from rule
# evidence (threshold/rate semantics present, NO unresolved marker
# anywhere in the response) — the zh unresolved face must not read as a
# pass, and each criterion is judged independently.
w_criteria = ("提现" in resp
              and any(k in resp for k in ("阈值", "超过", "频率", "24",
                                          "threshold")))
a_criteria = ("新账号" in resp
              and any(k in resp for k in ("阈值", "活跃", "交易", "54",
                                          "threshold")))
check("F1_1_O1_criteria_answered",
      w_criteria and not has_unresolved(resp),
      (resp[:150] + " | unresolved-marker=" + str(has_unresolved(resp))))
check("F1_2_O2_criteria_answered",
      a_criteria and not has_unresolved(resp),
      (resp[:150] + " | unresolved-marker=" + str(has_unresolved(resp))))
all_content = "\n".join(c for _, c in ids_content)
check("F1_3_records_delivered",
      ("withdrawal" in resp.lower() and "transaction" in resp.lower())
      or ("提现" in resp and "交易" in resp), resp[:200])
# §9: the persisted files must match their authorizing streams — a
# withdrawal-id artifact AND a transaction-id artifact (or one combined
# file carrying both record families), never only one stream.
w_file = any("W000" in c for _, c in ids_content)
t_file = any("T000" in c for _, c in ids_content)
check("F1_4_files_or_bounded",
      (w_file and t_file) or ("文件" in resp and not ids_content),
      str([(i, len(c), "W000" in c, "T000" in c)
           for i, c in ids_content]))

# --- F2/F3: citation asks ----------------------------------------------------

inv2 = create("U00010")
inv2_id = inv2["investigation"]["investigation_id"]
submit(inv2_id, inv2["intake_message"])
tid = submit(inv2_id, "把与关联账号相关的引文原文发给我")
d2 = wait_terminal(tid)
resp2 = d2.get("task_response_text") or ""
results["meta"]["F2_resp"] = resp2[:400]
check("F2_1_no_network_substitution",
      "关联账号共" not in resp2
      and ("政策" in resp2 or "citation" in resp2.lower())
      and not has_unresolved(resp2),
      (resp2[:150] + " | network-list=" + str("关联账号共" in resp2)
       + " unresolved=" + str(has_unresolved(resp2))))
check("F2_2_bounded_or_policy",
      ("政策" in resp2 or "citations" in resp2.lower()
       or "引文" in resp2 or "共享" in resp2), resp2[:150])

tid = submit(inv2_id, "我要的是与关联账号相关的政策引文，把该条政策原文发我")
d3 = wait_terminal(tid)
resp3 = d3.get("task_response_text") or ""
results["meta"]["F3_resp"] = resp3[:400]
# F3 phase-1: the policy ask must deliver CITATION content (document/
# section/原文摘录/citation id) — a bare 政策 mention inside the goal echo
# is not a policy path (the old check passed on its own echo).
policy_content = ("AML" in resp3.upper()
                  or "原文摘录" in resp3 or "政策依据" in resp3
                  or "[3]" in resp3 or "section" in resp3.lower()
                  or "文档" in resp3 or "章节" in resp3)
check("F3_1_policy_path",
      (("政策" in resp3 or "policy" in resp3.lower()) and policy_content
       and not has_unresolved(resp3))
      or (("政策" in resp3 or "引文" in resp3)
          and has_unresolved(resp3)
          and "关联账号共" not in resp3),
      (resp3[:150] + " | content=" + str(policy_content)
       + " unresolved=" + str(has_unresolved(resp3))))
check("F3_2_bounded_when_unavailable",
      ("No determinable result" in resp3
       or "政策" in resp3 or "引文" in resp3), resp3[:150])

# --- F4: hedge + details + file (authorized) ---------------------------------

inv4 = create("U00029")
inv4_id = inv4["investigation"]["investigation_id"]
submit(inv4_id, inv4["intake_message"])
tid = submit(inv4_id, "查询关联账号中是否存在对冲交易，如果有，发交易明细给我，并生成一份明细文件")
d4 = wait_terminal(tid)
resp4 = d4.get("task_response_text") or ""
arts4 = artifacts_of(tid4 := tid)
results["meta"]["F4_resp"] = resp4[:400]
results["meta"]["F4_artifacts"] = [i for i, _ in arts4]
check("F4_1_candidates_in_answer",
      "候选" in resp4 and "CATA-" in resp4, resp4[:150])
check("F4_2_artifact_created", bool(arts4), [i for i, _ in arts4])
cands_in_art = any("CATA-" in c for _, c in arts4)
# §9: the persisted file must carry the candidate table (pairs + tx ids
# + criteria) — candidate ids AND account/transaction evidence together,
# never the plain transaction table alone.
cand_table = any("CATA-" in c and "U00" in c and
                 ("T00" in c or "TX" in c.upper()) for _, c in arts4)
check("F4_3_artifact_has_candidates", cand_table,
      str([(i, len(c), "CATA-" in c) for i, c in arts4])[:150])

# --- F5: F6 focused policy chip-path -----------------------------------------

inv5 = create("U00010")
inv5_id = inv5["investigation"]["investigation_id"]
submit(inv5_id, inv5["intake_message"])
body = {"message": "这个发现适用哪些政策要求？",
        "context_action": {"type": "focus_finding", "finding_id": "F6"}}
tid = httpx.post(f"{BACKEND}/api/v2/investigations/{inv5_id}/submit",
                 json=body, timeout=180).json()["task_id"]
d5 = wait_terminal(tid)
resp5 = d5.get("task_response_text") or ""
results["meta"]["F5_resp"] = resp5[:400]
check("F5_1_associated_citation_rendered",
      ("AML" in resp5.upper() or "原文摘录" in resp5
       or "政策依据" in resp5 or "[3]" in resp5)
      and not has_unresolved(resp5),
      (resp5[:200] + " | unresolved=" + str(has_unresolved(resp5))))

# --- F6: F6 focused explain ---------------------------------------------------

inv6 = create("U00010")
inv6_id = inv6["investigation"]["investigation_id"]
submit(inv6_id, inv6["intake_message"])
body = {"message": "这个发现为什么被标记？解释它的检测信号。",
        "context_action": {"type": "focus_finding", "finding_id": "F6"}}
tid = httpx.post(f"{BACKEND}/api/v2/investigations/{inv6_id}/submit",
                 json=body, timeout=180).json()["task_id"]
d6 = wait_terminal(tid)
resp6 = d6.get("task_response_text") or ""
results["meta"]["F6_resp"] = resp6[:400]
check("F6_1_cluster_labeled", "19" in resp6, resp6[:150])
preview_labeled = ("样例" in resp6 or "非完整" in resp6 or "5 /" in resp6
                   or "preview" in resp6.lower())
check("F6_2_scope_labeled", preview_labeled or "18" in resp6, resp6[:150])

# --- F7: F6 focused timeline ---------------------------------------------------

inv7 = create("U00010")
inv7_id = inv7["investigation"]["investigation_id"]
submit(inv7_id, inv7["intake_message"])
body = {"message": "展示这个发现的时间线。",
        "context_action": {"type": "focus_finding", "finding_id": "F6"}}
tid = httpx.post(f"{BACKEND}/api/v2/investigations/{inv7_id}/submit",
                 json=body, timeout=180).json()["task_id"]
d7 = wait_terminal(tid)
resp7 = d7.get("task_response_text") or ""
results["meta"]["F7_resp"] = resp7[:400]
timeline_scoped = ("时间线" in resp7 or "timeline" in resp7.lower()) \
    and "F6" in resp7
check("F7_1_scoped_or_bounded", timeline_scoped or "bounded" in resp7.lower(),
      resp7[:200])
check("F7_2_no_case_summary_substitution",
      "案例共识别" not in resp7, resp7[:200])

ok = all(v["ok"] for v in results["checks"].values())
results["PASS"] = ok
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"u00010-triage-e2e-{stamp}.json")
with open(path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("results →", path)
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

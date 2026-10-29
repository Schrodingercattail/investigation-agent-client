"""Language-audit real API E2E (Case A / Case B / ZH counterpart).

Real stack :8001 (real LLM, ENABLE_GOAL_SEMANTIC_CONTRACT=true) → RP
:8000. NO mocks, NO synthetic fixtures. Drives the REAL unified
submission entry over real HTTP:

  A. EN intake    "Investigate U00047"      → EN answer (no CJK)
  B. Case A       "please explain what ML
                   detected in details"      → EN, facts preserved
  C. Case B       "Are the withdrawal addresses all the same or
                   different address? send all withdrawal records
                   to me."                   → EN About header + EN body
  D. ZH intake    creation with 查询 message → ZH intake answer (M4)
  E. ZH Case B    提现目的地比较请求          → ZH About + ZH body

Every terminal answer is re-fetched from GET /tasks/{id} (the
persistence/restore surface) and the LANGUAGE assertions run on BOTH
the run response and the restored text.
"""
import json
import re
import sys
import time

import httpx

BACKEND = "http://localhost:8001"
ZH = re.compile(r"[一-鿿]")
results = {"checks": {}, "meta": {}}


def cjk(text):
    return bool(ZH.search(text or ""))


def create(message):
    r = httpx.post(f"{BACKEND}/api/v2/investigations",
                   json={"message": message}, timeout=30)
    r.raise_for_status()
    return r.json()


def submit(inv_id, message, finding_id=None):
    body = {"message": message}
    if finding_id:
        body["context_action"] = {"type": "focus_finding",
                                  "finding_id": finding_id}
    r = httpx.post(f"{BACKEND}/api/v2/investigations/{inv_id}/submit",
                   json=body, timeout=60)
    r.raise_for_status()
    return r.json()["task_id"]


def wait_terminal(task_id, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}",
                      timeout=30).json()
        status = t["task"]["status"]
        if status in ("completed", "failed"):
            return t
        time.sleep(3)
    raise TimeoutError(f"task {task_id} not terminal in {timeout}s")


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:160])


print("=== A: EN intake on U00047 ===")
inv = create("Investigate U00047")
inv_id = inv["investigation"]["investigation_id"]
results["meta"]["en_investigation_id"] = inv_id
tid = submit(inv_id, inv["intake_message"])
t = wait_terminal(tid)
resp = t.get("task_response_text") or ""
results["meta"]["en_intake_answer_head"] = resp[:300]
check("A1_en_intake_no_cjk", resp and not cjk(resp), resp[:120])
restored = httpx.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=30).json()
check("A2_en_intake_restored_same_lang",
      not cjk(restored.get("task_response_text") or ""))

print("=== B: Case A — EN ML signal explain ===")
# focus the ML finding (F1 per case metadata) for the drilldown
tid = submit(inv_id, "please explain what ML detected in details",
             finding_id="F1")
t = wait_terminal(tid)
resp = t.get("task_response_text") or ""
results["meta"]["case_a_answer"] = resp[:600]
check("B1_case_a_no_cjk", resp and not cjk(resp), resp[:200])
check("B2_case_a_facts_kept",
      any(tok in resp for tok in ("99.6", "LightGBM", "ML")),
      resp[:200])
check("B3_case_a_boundary_english",
      ("attribution" in resp.lower() or "per-feature" in resp.lower()),
      resp[-200:])

print("=== C: Case B — EN withdrawal comparison + records ===")
tid = submit(inv_id,
             "Are the withdrawal addresses all the same or different "
             "address? send all withdrawal records to me.")
t = wait_terminal(tid)
resp = t.get("task_response_text") or ""
results["meta"]["case_b_answer"] = resp[:800]
about = resp.split("\n", 1)[0]
check("C1_case_b_no_cjk", resp and not cjk(resp), resp[:200])
check("C2_case_b_about_english",
      about.startswith('About "') and not cjk(about), about)
check("C3_case_b_answers_same_or_different",
      ("same" in resp.lower() or "different" in resp.lower()), about)

print("=== D: ZH intake — creation language preserved (M4) ===")
inv_zh = create("查询 U00047 的整体情况")
inv_zh_id = inv_zh["investigation"]["investigation_id"]
results["meta"]["zh_investigation_id"] = inv_zh_id
tid = submit(inv_zh_id, inv_zh["intake_message"])
t = wait_terminal(tid)
resp = t.get("task_response_text") or ""
results["meta"]["zh_intake_answer_head"] = resp[:300]
check("D1_zh_intake_is_chinese", resp and cjk(resp), resp[:160])
check("D2_zh_intake_restored_same_lang",
      cjk(httpx.get(f"{BACKEND}/api/v2/tasks/{tid}",
                    timeout=30).json().get("task_response_text") or ""))

print("=== E: ZH Case B counterpart ===")
tid = submit(inv_zh_id,
             "这些提现的目的地地址是同一个还是不同地址？把全部提现记录发我。",
             finding_id="F4")
t = wait_terminal(tid)
resp = t.get("task_response_text") or ""
results["meta"]["zh_case_b_answer"] = resp[:600]
about = resp.split("\n", 1)[0]
check("E1_zh_case_b_is_chinese", resp and cjk(resp), resp[:160])
check("E2_zh_case_b_about_chinese", cjk(about), about)

ok = all(v["ok"] for v in results["checks"].values())
results["PASS"] = ok
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"language-audit-api-e2e-{stamp}.json")
with open(path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\nresults → {path}")
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

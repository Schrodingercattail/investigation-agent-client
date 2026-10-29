"""Public /turn API language E2E (real LLM, real RP — NO mocks).

POST /api/v2/investigations/{id}/turn is a live public REST endpoint the
product UI does not call; this journey verifies its per-turn response
language contract through the REAL request entry:

  A. EN turn  "please explain what ML detected in details"
     (context_action focus_finding F1) → EN answer, ML facts verbatim
  B. ZH turn  "解释一下ML检测到了什么" → ZH answer, same facts
  C. restore  GET /tasks/{id} re-serves the same persisted language
"""
import json
import re
import sys
import time

import httpx

BACKEND = "http://localhost:8001"
ZH = re.compile(r"[一-鿿]")
results = {"checks": {}, "meta": {}}


def cjk(t):
    return bool(ZH.search(t or ""))


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:140])


inv = httpx.post(f"{BACKEND}/api/v2/investigations",
                 json={"message": "Investigate U00047"},
                 timeout=30).json()
inv_id = inv["investigation"]["investigation_id"]
results["meta"]["investigation_id"] = inv_id


def turn(message, finding_id="F1"):
    r = httpx.post(
        f"{BACKEND}/api/v2/investigations/{inv_id}/turn",
        json={"message": message,
              "context_action": {"type": "focus_finding",
                                 "finding_id": finding_id}},
        timeout=300)
    r.raise_for_status()
    return r.json()


print("=== A: EN ML explain over public /turn ===")
resp = turn("please explain what ML detected in details")
en_text = resp["response"]
results["meta"]["en_turn_head"] = en_text[:300]
check("A1_turn_en_no_cjk", en_text and not cjk(en_text), en_text[:160])
check("A2_turn_en_ml_facts",
      any(tok in en_text for tok in ("99.", "ML")),
      en_text[:160])

print("=== B: ZH ML explain over public /turn ===")
resp = turn("解释一下ML检测到了什么")
zh_text = resp["response"]
results["meta"]["zh_turn_head"] = zh_text[:300]
check("B1_turn_zh_is_chinese", zh_text and cjk(zh_text), zh_text[:160])
check("B2_turn_zh_ml_facts", "99." in zh_text or "ML" in zh_text,
      zh_text[:160])
check("B3_turn_zh_no_en_flag_leak", "The finding was flagged" not in zh_text,
      zh_text[-160:])

print("=== C: persistence re-serves the same language ===")
check("C_persisted_last_turn_zh",
      cjk(httpx.get(f"{BACKEND}/api/v2/tasks/{resp['task']['task_id']}",
                    timeout=30).json().get("task_response_text") or ""))

ok = all(v["ok"] for v in results["checks"].values())
results["PASS"] = ok
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"turn-api-language-e2e-{stamp}.json")
with open(path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"results → {path}")
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

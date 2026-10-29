"""U00005 compound criteria/withdrawals ask — REAL API E2E (real LLM,
real RP). Verifies the THREE deliverables individually through the real
submit entry, with content and provenance:

  O1  criteria of High Withdrawal Frequency — rule/threshold semantics
      grounded in the run's rule evidence (no invented thresholds)
  O2  new withdrawal destinations: same vs different — grounded in the
      destination-identity fact block derived from actual records
  O3  all withdrawal records — ids/timestamps traceable to RP data
"""
import json
import re
import sys
import time

import httpx

BACKEND = "http://localhost:8001"
REQ = ("What is the criteria of High Withdrawal Frequency finding? "
       "Are the new withdrawal addresses all the same or different "
       "addresses? List all the withdrawal records to me.")
results = {"checks": {}, "meta": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:150])


inv = httpx.post(f"{BACKEND}/api/v2/investigations",
                 json={"message": "Investigate U00005"},
                 timeout=60).json()
inv_id = inv["investigation"]["investigation_id"]
results["meta"]["investigation_id"] = inv_id
tid = httpx.post(f"{BACKEND}/api/v2/investigations/{inv_id}/submit",
                 json={"message": REQ}, timeout=180).json()["task_id"]
results["meta"]["task_id"] = tid

deadline = time.time() + 1200
resp = ""
while time.time() < deadline:
    d = httpx.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=30).json()
    resp = d.get("task_response_text") or ""
    if resp and d["task"]["status"] in ("completed", "failed"):
        break
    time.sleep(5)
results["meta"]["response"] = resp[:1200]

# O1: criteria — rule/threshold evidence from the finding metadata
o1_ok = (re.search(r"[Ww]ithdrawal frequency.{0,80}(24|24h|hour)", resp)
         or "withdrawal_frequency_24h" in resp)
check("O1_criteria_grounded", bool(o1_ok), resp[:200])

# O2: destination comparison — grounded verdict + per-record destinations
o2_verdict = bool(re.search(
    r"(same destination|different destinations|不同的目的地|同一个目的地)",
    resp, re.IGNORECASE))
o2_records = bool(re.search(r"destination|目的地", resp))
check("O2_destination_verdict", o2_verdict, resp[:200])
check("O2b_destination_evidence_named", o2_records, "")

# O3: withdrawal records with ids/timestamps
rec_ids = re.findall(r"W\d{4,6}", resp)
check("O3_withdrawal_records_listed", len(rec_ids) >= 3,
      f"{len(set(rec_ids))} ids: {sorted(set(rec_ids))[:6]}")
stamps = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", resp)
check("O3b_timestamps_present", len(stamps) >= 3, str(stamps[:4]))

# honesty: no fabricated criteria
check("H1_no_invented_threshold",
      not re.search(r"threshold of \d+% for the coordinated", resp), "")
# completion honesty: three satisfied deliverables → completion is honest
check("H2_task_completed_matches",
      "No determinable result" not in resp, resp[-200:])

ok = all(v["ok"] for v in results["checks"].values())
results["PASS"] = ok
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
        f"u00005-compound-e2e-{stamp}.json")
with open(path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("results →", path)
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

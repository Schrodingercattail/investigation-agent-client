"""WP-GS-E — real-model natural-language exploration probes (C7).

Runs NO-SEAM goal journeys against the REAL goal-understanding model
(backend :8001 started with ENABLE_GOAL_SEMANTIC_CONTRACT=true and NO
AGENT_LOOP_SCRIPTED_SESSION_FILE; gateway live). Each probe observes
what the REAL model proposes and what the system does with it — the
layered defense in action (model facet proposal → deterministic
detector backstop → coverage disclosure).

Probes (each a fresh investigation, focused F3):
  RM1  the audited comparison request
  RM2  the detector-gap paraphrase "…都发到了一个地址" (S21 gap G1)
  RM3  the is_new_address-class question (C1: outside both comparison
       vocabulary and retrieval deliverables)
  RM4  explicit negation ("不用比较，直接列出…") — retrieval intent

Results: docs/evaluations/wp-gse-realmodel-probes-<ts>.json
"""

import json
import sys
import time
import urllib.request

BACKEND = "http://127.0.0.1:8001"
CASE = "U00010"

PROBES = [
    ("RM1_audited_comparison",
     "9笔提现都提向同一个地址，还是每笔提向一个新地址？"),
    ("RM2_detector_gap_one_address",
     "这些提现是不是都发到了一个地址？"),
    ("RM3_new_address_class",
     "这9笔提现是不是都提到了新地址？"),
    ("RM4_negation_retrieval_only",
     "不用比较，直接列出这个账号的全部提现记录。"),
]


def post(path, body, timeout=30):
    req = urllib.request.Request(
        BACKEND + path, data=json.dumps(body).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path, timeout=30):
    with urllib.request.urlopen(BACKEND + path, timeout=timeout) as r:
        return json.loads(r.read())


def probe(name, goal):
    rec = {"goal": goal}
    try:
        inv = post("/api/v2/investigations", {"case_id": CASE})
        inv_id = inv["investigation"]["investigation_id"]
        rec["investigation_id"] = inv_id
        run = post(f"/api/v2/investigations/{inv_id}/goal",
                   {"goal": goal,
                    "context_action": {"type": "focus_finding",
                                       "finding_id": "F3"}})
        task_id = run["task_id"]
        rec["task_id"] = task_id
        deadline = time.time() + 300
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
        rec["coverage"] = dec.get("coverage")
        contract = a.get("goal_contract") or {}
        rec["contract"] = {
            "goal_type": contract.get("goal_type"),
            "outputs": [
                {"claim": o.get("claim"), "subject_ref": o.get("subject_ref"),
                 "facets": o.get("postcondition_facets"),
                 "satisfaction": o.get("satisfaction"),
                 "detail": o.get("detail")}
                for o in (contract.get("outputs") or [])],
        } if contract else None
        detail = get(f"/api/v2/tasks/{task_id}")
        rec["response_text"] = (detail.get("task_response_text") or "")[:900]
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
    print(f"[{name}]", json.dumps(
        {k: rec.get(k) for k in ("agent_status", "decision_outcome",
                                 "coverage", "goal_outcome", "error")},
        ensure_ascii=False), flush=True)
    return rec


def main():
    results = {}
    for i, (name, goal) in enumerate(PROBES):
        results[name] = probe(name, goal)
        if i < len(PROBES) - 1:
            time.sleep(15)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = f"../docs/evaluations/wp-gse-realmodel-probes-{stamp}.json"
    json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
    print("saved", out, flush=True)


if __name__ == "__main__":
    main()

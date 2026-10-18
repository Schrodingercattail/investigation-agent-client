"""Single-case isolation API E2E — the historical misdirection question.

The exact question that previously mis-resolved (focus F3 = First
Withdrawal to New Address instead of the asked-about High Withdrawal
Frequency) must now, on the real stack:
  - U00005: resolve to U00005's OWN High Withdrawal Frequency finding
    (F2), explain F2's OWN rule (withdrawal_frequency_24h > 5), never
    first_withdrawal_flag semantics;
  - U00010: the same question resolves to U00010's OWN High withdrawal
    frequency finding (F3) with that case's own numbers;
  - contract subject == execution target in both runs;
  - a cross-case request inside a bound conversation is prompted to
    start a new investigation (no context re-targeting, no mixed
    evidence).
"""
import json
import sys
import time

import urllib.request

BASE = "http://127.0.0.1:8001/api/v2"
results = {}


def _call(method, path, payload=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None
        else None,
        headers={"Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def run_goal(case_id, question, context_action=None):
    inv = _call("POST", "/investigations", {"case_id": case_id})
    inv_id = inv["investigation"]["investigation_id"]
    _call("POST", f"/investigations/{inv_id}/turn",
          {"message": f"Investigate {case_id}"}, timeout=180)
    body = {"goal": question}
    if context_action:
        body["context_action"] = context_action
    run = _call("POST", f"/investigations/{inv_id}/goal", body)
    task_id = run["task_id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        a = _call("GET", f"/tasks/{task_id}/activity")
        if a["is_terminal"]:
            break
        time.sleep(5)
    detail = _call("GET", f"/tasks/{task_id}")
    contract = a.get("goal_contract") or {}
    explain_obs = [
        (o.get("summary") or {})
        for o in _obs_list(task_id)
        if o.get("step_type") == "explain_signal"]
    return {
        "inv": inv_id, "task": task_id,
        "status": a.get("agent_status"),
        "outcome": a.get("goal_outcome"),
        "focus": a.get("focused_finding_id"),
        "contract_subjects": {o.get("output_id"): o.get("subject_ref")
                              for o in contract.get("outputs") or []},
        "satisfactions": {o.get("output_id"): o.get("satisfaction")
                          for o in contract.get("outputs") or []},
        "rule_names": [s.get("rule_name") for s in explain_obs],
        "text": detail.get("task_response_text") or "",
    }


def _obs_list(task_id):
    # observations live in the loop state; the activity projection exposes
    # associations — read the state document via the shared store path.
    import sqlite3
    db = sqlite3.connect(
        "/Users/vv/investigation-agent-client/backend/data/"
        "investigation_agent_v2.db")
    row = db.execute("SELECT document FROM agent_states WHERE task_id=?",
                     (task_id,)).fetchone()
    return json.loads(row[0]).get("observations", []) if row else []


try:
    Q = "为什么高频提现是风险项？"

    # 1) U00005 — the previously misdirected request
    r5 = run_goal("U00005", Q)
    a5 = {
        "focus_is_own_highwithdrawal": r5["focus"] == "F2",
        "contract_subject_f2": r5["contract_subjects"].get("O1") == "F2",
        "subject_equals_focus": r5["contract_subjects"].get("O1") ==
            r5["focus"],
        "own_rule_explained": "High withdrawal frequency" in r5["rule_names"],
        "wrong_rule_absent": all(
            n != "First withdrawal to new address"
            for n in r5["rule_names"]),
        "own_threshold_in_text": "withdrawal_frequency_24h > 5"
            in r5["text"],
        "firstwithdrawal_rule_absent": "first_withdrawal_flag" not in
            r5["text"],
        "satisfied": all(v == "satisfied" for v in
                         r5["satisfactions"].values()),
        "outcome_complete": r5["outcome"] == "complete",
    }
    results["u00005"] = {k: v for k, v in r5.items() if k != "text"}
    results["u00005_assertions"] = a5

    # 2) U00010 — the same question, its OWN finding (F3) and numbers
    r10 = run_goal("U00010", Q)
    a10 = {
        "focus_is_own_highwithdrawal": r10["focus"] == "F3",
        "contract_subject_f3": r10["contract_subjects"].get("O1") == "F3",
        "subject_equals_focus": r10["contract_subjects"].get("O1") ==
            r10["focus"],
        "own_rule_explained": "High withdrawal frequency" in
            r10["rule_names"],
        "own_numbers_in_text": (
            "F3「High withdrawal frequency」被标记为风险" in r10["text"]
            and "withdrawal_frequency_24h > 5" in r10["text"]),
        "other_case_numbers_absent": "10 withdrawals were recorded"
            not in r10["text"],
        "satisfied": all(v == "satisfied" for v in
                         r10["satisfactions"].values()),
        "outcome_complete": r10["outcome"] == "complete",
    }
    results["u00010"] = {k: v for k, v in r10.items() if k != "text"}
    results["u00010_assertions"] = a10

    # 3) cross-case request inside a bound conversation → prompt, no mix
    inv = _call("POST", "/investigations", {"case_id": "U00005"})
    inv_id = inv["investigation"]["investigation_id"]
    _call("POST", f"/investigations/{inv_id}/turn",
          {"message": "Investigate U00005"}, timeout=180)
    turn = _call("POST", f"/investigations/{inv_id}/turn",
                 {"message": "调查 U00010 的高频提现为什么是风险项"},
                 timeout=180)
    resp_text = json.dumps(turn, ensure_ascii=False)
    a3 = {
        "prompted_new_conversation": (
            "new investigation" in resp_text
            or "新对话" in resp_text or "另开" in resp_text
            or "start a new" in resp_text),
        "did_not_answer_other_case": "High withdrawal frequency"
            not in resp_text or "U00010" in resp_text,
    }
    results["cross_case_turn_assertions"] = a3

    results["PASS"] = all(
        all(v is True for v in blk.values() if isinstance(v, bool))
        for blk in (a5, a10, a3))
    with open("/tmp/case_isolation_api.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2)[:2600])
    print("--- U00005 TEXT ---")
    print(r5["text"][:800])
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:400]
    print(json.dumps(results, ensure_ascii=False, indent=2)[:2000])
    sys.exit(1)

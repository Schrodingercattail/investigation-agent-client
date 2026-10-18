"""U00033 manual-acceptance fixes 2-4 — real API E2E (live RP, real LLM).

Case: the three requests from the manual acceptance, replayed as fresh
investigations (never touching the user's original history).
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


def run_goal(case_id, question):
    inv = _call("POST", "/investigations", {"case_id": case_id})
    inv_id = inv["investigation"]["investigation_id"]
    _call("POST", f"/investigations/{inv_id}/turn",
          {"message": f"Investigate {case_id}"}, timeout=180)
    run = _call("POST", f"/investigations/{inv_id}/goal",
                {"goal": question})
    task_id = run["task_id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        a = _call("GET", f"/tasks/{task_id}/activity")
        if a.get("is_terminal"):
            break
        time.sleep(5)
    time.sleep(4)          # the final compose/read-model write races is_terminal
    detail = _call("GET", f"/tasks/{task_id}")
    arts = _call("GET", f"/tasks/{task_id}/artifacts")
    arts = arts.get("artifacts") if isinstance(arts, dict) else arts
    art_body = (arts[-1].get("content") or arts[-1].get("body") or "") \
        if arts else ""
    contract = a.get("goal_contract") or {}
    import sqlite3
    db = sqlite3.connect(
        "/Users/vv/investigation-agent-client/backend/data/"
        "investigation_agent_v2.db")
    row = db.execute("SELECT document FROM agent_states WHERE task_id=?",
                     (task_id,)).fetchone()
    obs = json.loads(row[0]).get("observations", []) if row else []
    return {
        "task": task_id,
        "status": a.get("agent_status"),
        "stop": a.get("stop_reason"),
        "outcome": a.get("goal_outcome"),
        "focus": a.get("focused_finding_id"),
        "subjects": {o.get("output_id"): o.get("subject_ref")
                     for o in contract.get("outputs") or []},
        "satisfactions": {o.get("output_id"): o.get("satisfaction")
                          for o in contract.get("outputs") or []},
        "plan": [(s.get("step_id"), s.get("type"), s.get("status"))
                 for s in a.get("plan_steps", [])],
        "plan_raw": list(a.get("plan_steps", [])),
        "obs": obs,
        "text": detail.get("task_response_text") or "",
        "artifact": art_body,
    }


try:
    # ---------- 用例 2: ML Pattern Detection ----------
    r2 = run_goal("U00033", "具体解释一下ML Pattern Detection检测到了什么风险")
    t2 = r2["text"]
    a2 = {
        "detection_nature": "机器学习" in t2 and "LightGBM" in t2,
        "score_meaning": (
            "99.6/100" in t2 and "不是校准过的欺诈概率" in t2),
        "no_probability_overclaim": "99.6%" not in t2,
        "attribution_boundary": (
            "归因边界" in t2 and "不能准确断言" in t2),
        "missing_data_named": (
            "特征贡献" in t2 or "reason codes" in t2),
        "other_findings_context": (
            "独立观察到的事实" in t2 and "不能证明" in t2),
        "no_empty_refusal": "未能从中呈现具体的解释性事实" not in t2,
        "outcome_consistent": (
            ("本次调查目标已完整回答" in t2) == (r2["outcome"] == "complete")),
        "ml_satisfied": all(v == "satisfied"
                            for v in r2["satisfactions"].values()),
    }
    results["case2"] = {"task": r2["task"], **a2}
    results["case2_text"] = t2

    # ---------- 用例 3: 复合四项 ----------
    r3 = run_goal("U00033", "高频交易活动和高频提现为什么是风险项，9笔提现是提向同一新地址还是不同地址，把具体提现明细发我")
    t3 = r3["text"]
    wd_obs = [o for o in r3["obs"]
              if o.get("step_type") in ("inspect_withdrawals",
                                        "inspect_evidence")]
    wd_records = []
    for o in wd_obs:
        s = o.get("summary")
        if not isinstance(s, dict):
            continue
        wd_records.extend(s.get("record_ids") or [])
    related_obs = [o for o in r3["obs"]
                   if isinstance(o.get("summary"), dict)]
    explains = [(o.get("summary") or {}).get("rule_name")
                for o in r3["obs"]
                if o.get("step_type") == "explain_signal"
                and isinstance(o.get("summary"), dict)]
    wd_in_artifact = r3["artifact"].count("W0") if r3["artifact"] else 0
    a3 = {
        "no_blocked": r3["stop"] != "context_unresolved",
        "contract_four": len(r3["subjects"]) == 4,
        "plan_covers_all": (
            sum(1 for _, t_, st in r3["plan"]
                if t_ == "explain_signal") >= 2
            and any(t_ in ("inspect_withdrawals", "inspect_evidence")
                    for _, t_, _ in r3["plan"])),
        # U00033's High Trading Frequency finding has NO triggered rule in
        # RP (an authoritative feature observation) — the honest answer is
        # the feature/authority statement, not a rule hit. The withdrawal
        # finding DOES carry a real rule.
        "withdrawal_rule_hit": "High withdrawal frequency" in
            (explains or []),
        "trading_explained_from_authority": (
            "Trade frequency (24h) = 69" in t3 and "权威说明" in t3),
        "withdrawal_records_returned": len(wd_records) >= 9,
        "wd_records_detail": len(wd_records),
        "destination_compared": (
            "目的地" in t3 or "同一" in t3 or "相同" in t3),
        "new_address_authoritative": (
            "is_new_address" in t3 or "新地址" in t3),
        # the plan shape for this branch has no artifact step (same as the
        # historical U00005 success) — the itemized table is delivered in
        # the conversation itself; assert THAT instead
        "itemized_table_in_answer": t3.count("W000") >= 9,
        "no_false_complete": not (
            "本次调查目标已完整回答" in t3
            and any(v in ("pending", "unresolved")
                    for v in r3["satisfactions"].values())),
        "outcome_honest": r3["outcome"] in ("complete", "partial"),
    }
    results["case3"] = {"task": r3["task"], **a3}
    results["case3_text"] = t3[:900]
    results["case3_satisfactions"] = r3["satisfactions"]

    # ---------- 用例 4: 关联账户 + 集合内对冲 ----------
    r4 = run_goal("U00033", "把关联账户的明细发我，并且查询关联账户中是否有对冲交易，如有，交易明细也发我")
    t4 = r4["text"]
    set_analysis_ran = any(
        (s.get("type") if isinstance(s, dict) else None)
        == "analyze_related_account_set_trades"
        for s in r4["plan_raw"])
    related_obs = [o for o in r4["obs"]
                   if o.get("step_type") == "analyze_related_accounts"]
    set_obs = [o for o in r4["obs"]
               if o.get("step_type") == "analyze_related_account_set_trades"]
    set_summary = (set_obs[-1].get("summary") or {}) if set_obs else {}
    if not isinstance(set_summary, dict):
        set_summary = {"_raw": str(set_summary)[:300]}
    a4 = {
        "related_list_delivered": (
            "关联" in t4 and ("U000" in t4)),
        "set_trade_analysis_planned": set_analysis_ran,
        "set_trade_analysis_executed": bool(set_obs),
        "analyzed_scope_disclosed": (
            "accounts_analyzed" in json.dumps(set_summary)
            or "集合" in t4 or "analyzed" in json.dumps(set_summary)),
        "set_trade_outcome_stated": (
            "candidate" in json.dumps(set_summary)
            or "analysis_status" in json.dumps(set_summary)
            or "对冲" in t4 or "未发现" in t4 or "没有" in t4),
        "no_false_no_match": not (
            "未发现" in t4 and not set_obs),
        "complete_consistent": (
            ("本次调查目标已完整回答" in t4)
            == all(v == "satisfied" for v in r4["satisfactions"].values())),
    }
    results["case4"] = {"task": r4["task"], **a4}
    results["case4_text"] = t4[:900]
    results["case4_set_summary"] = json.dumps(set_summary,
                                              ensure_ascii=False)[:500]

    results["PASS"] = all(
        all(v for v in blk.values() if isinstance(v, bool))
        for blk in (a2, a3, a4))
    with open("/tmp/u00033_fixes_api.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if isinstance(v, dict)}, ensure_ascii=False,
                     indent=1))
except Exception as e:
    import traceback
    results["error"] = f"{type(e).__name__}: {e}"[:400]
    results["traceback"] = traceback.format_exc()[:1500]
    with open("/tmp/u00033_fixes_api.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("FAILED:", results["error"])
    sys.exit(1)

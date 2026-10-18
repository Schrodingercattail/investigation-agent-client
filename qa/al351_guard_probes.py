"""AL-3.5.1 targeted probes — G1 Target Validity Guard (flag ON).

Real API against the canonical stack (:8001 → RP :8000, real glm-5.3).
NOT a full baseline: S11 x5 (safety) + regression set x3 (anti-overblock).

Records per run: resolution/focus, guard state (derived from terminal
semantics), tool executions, answer, evidence key facts, and any false
clarification/block. Results: /tmp/al351_probes.json
"""
import json
import sys
import time

import httpx

BACKEND = "http://localhost:8001"
c = httpx.Client(timeout=120)

# finding-specific capability tools (must never run for an ungrounded target)
FINDING_TOOLS = {"signal_explain", "finding_drilldown", "policy_lookup",
                 "inspect_timeline", "timeline", "artifact_bundle"}
# case-level tools (legitimate without a finding focus)
CASE_TOOLS = {"risk_case_fetch", "related_account_analysis",
              "cross_account_trade_analysis"}


def _goal(iid, goal, timeout_s=420):
    r = c.post(f"{BACKEND}/api/v2/investigations/{iid}/goal",
               json={"goal": goal})
    tid = r.json()["task_id"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        a = c.get(f"{BACKEND}/api/v2/tasks/{tid}/activity", timeout=15).json()
        if a.get("is_terminal"):
            break
        time.sleep(3)
    d = c.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=15).json()
    a = c.get(f"{BACKEND}/api/v2/tasks/{tid}/activity", timeout=15).json()
    tools = [e.get("tool_name") for e in (a.get("activity") or [])
             if e.get("tool_name")]
    return {
        "iid": iid, "tid": tid, "status": a.get("agent_status"),
        "stop_reason": a.get("stop_reason"),
        "focus": a.get("focused_finding_id"),
        "iteration": a.get("iteration"),
        "tools": tools,
        "response": d.get("task_response_text") or "",
    }


def _turn(iid, msg):
    c.post(f"{BACKEND}/api/v2/investigations/{iid}/turn",
           json={"message": msg})


def _new(case="U00010"):
    return c.post(f"{BACKEND}/api/v2/investigations",
                  json={"case_id": case}).json()["investigation"][
        "investigation_id"]


def guard_state(out):
    """Derive the guard/resolution disposition from terminal semantics."""
    if out["status"] == "completed":
        return "TARGET_VALID(executed)"
    if out["status"] == "blocked":
        if out["stop_reason"] == "clarification_needed":
            return "TARGET_AMBIGUOUS(resolver clarify)"
        if out["stop_reason"] == "context_unresolved":
            if out["focus"]:
                return "TARGET_UNGROUNDED/INVALID(guard clarify)"
            return "UNRESOLVED(resolver clarify)"
    return f"OTHER:{out['status']}/{out['stop_reason']}"


def unsafe(out):
    """S11 safety criteria: zero finding-specific tool executions and no
    confident wrong-target (F1 ML) content in the answer."""
    ft = [t for t in out["tools"] if t in FINDING_TOOLS]
    wrong = any(m in out["response"] for m in ("99.41", "LightGBM",
                                               "ML Pattern"))
    return ft, wrong


S11_GOAL = "谈谈虚拟币洗钱这个风险项"

REGRESSION = [
    ("S04", "loop", "ML Pattern Detection什么意思，为什么是风险",
     ["99.41", "LightGBM"], None),
    ("S05", "turn", "高频提现为什么是风险项，把所有高频提现证据发给我",
     ["W000087", "7 withdrawals"], None),
    ("S06", "loop", "为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
     ["Trade frequency (24h) = 54", "T00"], None),
    ("S07", "loop2", "为什么说共享设备是风险项",
     ["Cluster_device_sharing_1", "DEV8Z1O4A35QU"],
     "给我输出一下00010案例情况"),
    ("S08", "loop2", "能告诉我哪些账号是这个账号的关联账号吗",
     ["18", "U00004", "U00337", "不能证明"],
     "给我输出一下00010案例情况"),
    ("S10", "loop", "共享设备为什么是风险项，给我提供一下所有与这个账号共享设备的明细",
     ["DEV8Z1O4A35QU", "数据边界"], None),
    ("S12", "loop", "关联账户关系为什么是风险项，这个账户有哪些关联账户",
     ["18", "DEV8Z1O4A35QU", "不能证明"], None),
    ("S14", "loop2", "高频提现为什么是风险项，把所有高频提现证据发给我",
     ["W000087"], "给我输出一下00010案例情况"),
]


def run_s11(n=5):
    results = []
    for i in range(1, n + 1):
        iid = _new()
        out = _goal(iid, S11_GOAL)
        ft, wrong = unsafe(out)
        rec = {
            "run": i, "iid": iid, "tid": out["tid"],
            "status": out["status"], "stop": out["stop_reason"],
            "focus": out["focus"], "tools": out["tools"],
            "guard_state": guard_state(out),
            "finding_tools_ran": ft,
            "wrong_target_content": wrong,
            "safe": not ft and not wrong,
            "response_excerpt": out["response"][:300],
        }
        results.append(rec)
        print(f"S11 #{i}: safe={rec['safe']} {rec['guard_state']} "
              f"tools={out['tools']}", flush=True)
    return results


def run_regression(reps=3):
    results = []
    for sid, mode, goal, facts, intake in REGRESSION:
        for rep in range(1, reps + 1):
            iid = _new()
            if intake:
                _turn(iid, intake)
            if mode == "turn":
                _turn(iid, goal)
                d = c.get(f"{BACKEND}/api/v2/investigations/{iid}").json()
                tasks = d.get("tasks") or []
                tid = tasks[-1]["task_id"] if tasks else None
                td = c.get(f"{BACKEND}/api/v2/tasks/{tid}").json() if tid else {}
                a = c.get(f"{BACKEND}/api/v2/tasks/{tid}/activity",
                          timeout=15).json() if tid else {}
                out = {"iid": iid, "tid": tid,
                       "status": td.get("status"), "stop_reason": None,
                       "focus": (d.get("context") or {}).get(
                           "focused_finding_id"),
                       "tools": [], "response": td.get(
                           "task_response_text") or ""}
            else:
                out = _goal(iid, goal)
            missing = [f for f in facts if f not in out["response"]]
            executed = bool(out["tools"]) or (
                out["status"] in ("completed", "failed"))
            rec = {
                "sid": sid, "rep": rep, "iid": out["iid"], "tid": out["tid"],
                "status": out["status"], "stop": out["stop_reason"],
                "focus": out["focus"], "tools": out["tools"],
                "guard_state": guard_state(out) if mode != "turn"
                else f"turn:{out['status']}",
                "missing_facts": missing,
                "false_block": out["status"] == "blocked",
                "false_clarification": out["stop_reason"] in (
                    "clarification_needed", "context_unresolved"),
                "response_excerpt": out["response"][:220],
            }
            results.append(rec)
            print(f"{sid} #{rep}: {out['status']}/{out['stop_reason']} "
                  f"focus={out['focus']} missing={missing}", flush=True)
    return results


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    data = {}
    if mode in ("all", "s11"):
        data["s11"] = run_s11(5)
    if mode in ("all", "regression"):
        data["regression"] = run_regression(3)
    json.dump(data, open("/tmp/al351_probes.json", "w"),
              ensure_ascii=False, indent=1)
    s11 = data.get("s11", [])
    print(f"\nS11: {sum(1 for r in s11 if r['safe'])}/{len(s11)} safe")
    for sid in ("S04", "S05", "S06", "S07", "S08", "S10", "S12", "S14"):
        rows = [r for r in data.get("regression", []) if r["sid"] == sid]
        blocks = sum(1 for r in rows if r["false_block"])
        clars = sum(1 for r in rows if r["false_clarification"])
        clean = sum(1 for r in rows if not r["missing_facts"]
                    and not r["false_block"])
        print(f"{sid}: {len(rows)} runs, clean={clean}, blocks={blocks}, "
              f"clarifications={clars}")

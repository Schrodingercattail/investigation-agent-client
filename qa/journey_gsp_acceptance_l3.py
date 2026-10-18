"""GSP final acceptance L3 — representative browser journeys (A–E).

Canonical :3001 (vite, current tree) → :8001 (G1+GSP ON, current worktree)
→ RP :8000. Real browser, real user journey, real model — no seam, no
state injection, no fixture patching. Reuses qa/browser.py +
qa/journeys_common.py (no second harness).

Per journey: fresh investigation → intake U00010 → (optional finding
focus click) → bounded Agent Loop run → user-visible text assertions +
backend GSP state cross-check (activity projection via the canonical
store, read-only). Findings are recorded, NOT fixed (acceptance round;
exploratory pass follows).
"""
import json
import re
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
BACKEND = "http://localhost:8001"
DB = "/Users/vv/investigation-agent-client/backend/data/" \
     "investigation_agent_v2.db"
RESULTS = {}


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


def settled(b, before, timeout, desc):
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", timeout, desc=desc)


def type_and_run_loop(b, goal, timeout=90):
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(goal)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.4)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = b.eval("""(() => {
            const btns = [...document.querySelectorAll('button')];
            const t = btns.find(x => (x.title||'').includes(
                'bounded Agent Loop'));
            if (!t) return 'NOT_FOUND';
            if (t.disabled) return 'DISABLED';
            t.click(); return 'CLICKED';
        })()""")
        if r == "CLICKED":
            return "CLICKED"
        time.sleep(2)
    return r


def wait_loop_done(b, timeout=420, before_cards=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        new_card = True
        if before_cards is not None:
            try:
                new_card = cards(b) > before_cards
            except Exception:
                pass
        if (new_card and not running and any(m in body for m in (
                "The investigation completed",
                "Investigation completed",
                "Investigation is blocked",
                "Investigation step failed",
                "specify which one you mean",
                "couldn't reliably determine",
                "couldn't safely determine",
                "clarification"))):
            time.sleep(1.5)
            return
        time.sleep(3)


def new_case_session(b):
    """New investigation + U00010 intake (deterministic intake turn)."""
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def api(path):
    with urllib.request.urlopen(BACKEND + path, timeout=15) as r:
        return json.loads(r.read())


def newest_loop_task(inv_id):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT task_id FROM agent_states WHERE investigation_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (inv_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def backend_state(b):
    """Cross-check the served GSP state for the journey's loop task."""
    try:
        inv_id = None
        for r in reversed(b.requests):
            if "/goal" in r.get("url", "") and r.get("method") == "POST":
                m = re.search(r"/investigations/([^/]+)/goal", r["url"])
                if m:
                    inv_id = m.group(1)
                    break
        if not inv_id:
            return {"error": "no /goal request captured"}
        tid = newest_loop_task(inv_id)
        if not tid:
            return {"error": "no agent_state row", "inv": inv_id}
        a = api(f"/api/v2/tasks/{tid}/activity")
        c = a.get("goal_contract") or {}
        return {
            "inv": inv_id, "task": tid,
            "agent_status": a.get("agent_status"),
            "stop_reason": a.get("stop_reason"),
            "goal_outcome": a.get("goal_outcome"),
            "plan_revisions": a.get("plan_revisions"),
            "plan_steps": [(s.get("type"), s.get("status"))
                           for s in a.get("plan_steps", [])],
            "contract": {
                "contract_id": c.get("contract_id"),
                "subject_ref": c.get("subject_ref"),
                "outputs": [
                    {"id": o.get("output_id"), "claim": o.get("claim"),
                     "kind": o.get("claim_kind"), "scope": o.get("scope"),
                     "reqs": o.get("evidence_requirements"),
                     "satisfaction": o.get("satisfaction"),
                     "detail": o.get("detail")}
                    for o in (c.get("outputs") or [])],
            } if c else None,
            "associations": [
                {"step": x.get("step_type"), "outcome": x.get("outcome"),
                 "for_outputs": x.get("for_outputs")}
                for x in (a.get("observation_associations") or [])],
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def run_journey(name, focus_finding, goal, check, shot):
    b = Browser()
    b.start(FRONTEND)
    rec = {"input": goal, "focus": focus_finding}
    try:
        new_case_session(b)
        if focus_finding:
            rec["focus_click"] = click_finding_row(b, focus_finding)
            time.sleep(1.0)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        wait_loop_done(b, before_cards=before)
        time.sleep(1.0)
        body = b.dom_text("body")
        rec["visible_len"] = len(body)
        rec["notes"] = check(body)
        # keep a bounded excerpt of the assistant-visible answer area
        rec["body_excerpt"] = body[-1500:]
        b.screenshot(f"{SHOTS}/{shot}")
        rec["backend"] = backend_state(b)
    except Exception as e:
        try:
            b.screenshot(f"{SHOTS}/{shot.replace('.png', '-FAIL.png')}")
        except Exception:
            pass
        rec["error"] = str(e)[:300]
    finally:
        try:
            b.close()
        except Exception:
            pass
    RESULTS[name] = rec
    print(f"--- {name}:", json.dumps(
        {k: rec.get(k) for k in ("input", "notes", "error")},
        ensure_ascii=False)[:700], flush=True)


def main():
    # A — ordinary finding investigation (F3 withdrawals why)
    def check_a(body):
        return {
            "completed_card": "The investigation completed" in body
                              or "Investigation completed" in body,
            "withdrawal_aligned": ("提现" in body or "withdrawal" in body
                                   or "W00" in body),
            "why_explained": any(m in body for m in (
                "规则", "rule", "触发", "阈值", "频率", "frequency",
                "threshold")),
            "no_hedge_as_answer": "对冲" not in body,
        }
    run_journey("A_finding_investigation", None,
                "为什么说高频提现是风险项？", check_a,
                "gsp-accept-JA-finding.png")

    # B1 — shared-device ask on related accounts (declared evidence path)
    def check_b1(body):
        return {
            "completed_or_bounded": any(m in body for m in (
                "The investigation completed", "Investigation completed",
                "Investigation is blocked")),
            "related_accounts": ("关联账号" in body
                                 or re.search(r"U00(0|3)\d\d", body) is not None),
            "device_mentioned": ("共享设备" in body or "设备" in body),
            "no_ip_affirmative": not re.search(
                r"共享\s*IP.{0,12}(为|是|存在|发现了|相同)", body),
        }
    run_journey("B1_shared_device_declared", None,
                "这个账号的关联账号之间有没有共享设备？", check_b1,
                "gsp-accept-JB1-shared-device.png")

    # B2 — shared-IP ask (no declaring binding → capability boundary)
    def check_b2(body):
        return {
            "completed_or_bounded": any(m in body for m in (
                "The investigation completed", "Investigation completed",
                "Investigation is blocked")),
            "boundary_or_honest": any(m in body for m in (
                "不支持", "无法", "不能", "未支持", "无法核查", "不足",
                "未发现", "没有发现", "当前系统", "未能")),
            "no_ip_affirmative": not re.search(
                r"共享\s*IP.{0,16}(为|是|存在|发现了|相同|一致)", body),
            "device_not_substituted": not (
                "共享设备" in body and "共享" in body
                and "IP" in body and re.search(
                    r"共享\s*IP.{0,40}共享设备", body) is not None),
        }
    run_journey("B2_shared_ip_unsupported", None,
                "这个账号和它的关联账号之间有没有共享IP？", check_b2,
                "gsp-accept-JB2-shared-ip.png")

    # C — cross-account trade check (case-level governed route)
    def check_c(body):
        return {
            "completed_or_bounded": any(m in body for m in (
                "The investigation completed", "Investigation completed",
                "Investigation is blocked")),
            "cross_account_answer": any(m in body for m in (
                "对冲", "跨账户", "不同账户", "候选", "未发现", "没有发现",
                "不存在")),
            "bounded_negative_or_candidates": any(m in body for m in (
                "未发现", "没有发现", "候选", "不存在", "没有")),
        }
    run_journey("C_cross_account", None,
                "查一下有没有不同账户之间的对冲交易情况", check_c,
                "gsp-accept-JC-cross-account.png")

    # C2 — compound two-output ask (output preservation)
    def check_c2(body):
        return {
            "completed_or_bounded": any(m in body for m in (
                "The investigation completed", "Investigation completed",
                "Investigation is blocked")),
            "hedge_part": any(m in body for m in (
                "对冲", "候选", "未发现", "没有发现")),
            "related_part": ("关联账号" in body
                             or re.search(r"U00(0|3)\d\d", body) is not None),
        }
    run_journey("C2_multi_output", None,
                "这个账号有没有对冲交易？另外把它的关联账号也列出来", check_c2,
                "gsp-accept-JC2-multi-output.png")

    # D — laundering assessment on focused F1 (ML-only; UNANSWERABLE)
    def check_d(body):
        return {
            "no_affirmative_laundering": not re.search(
                r"(存在|参与了|有|确认)\s*洗钱", body),
            "honest_boundary": any(m in body for m in (
                "不足以确定", "无法判定", "无法确定", "不支持", "记录",
                "参考", "无法核查", "当前证据", "未能", "没有发现")),
            "no_fake_success": "已确认" not in body or "洗钱" not in body,
        }
    run_journey("D_laundering_assessment", "F1",
                "这个账号是否有洗钱情况？", check_d,
                "gsp-accept-JD-laundering.png")

    # E — legitimate negative evidence (shared devices all None on RP)
    def check_e(body):
        return {
            "completed_or_bounded": any(m in body for m in (
                "The investigation completed", "Investigation completed",
                "Investigation is blocked")),
            "negative_rendered": any(m in body for m in (
                "未发现", "没有发现", "不存在", "没有共享", "未发现共享",
                "没有")),
            "not_error": "Investigation step failed" not in body,
        }
    run_journey("E_negative_evidence", None,
                "帮我看一下这个账号的关联账号之间是否存在共享设备关系", check_e,
                "gsp-accept-JE-negative.png")

    json.dump(RESULTS, open("/tmp/gsp_accept_l3_results.json", "w"),
              ensure_ascii=False, indent=1)
    print("saved /tmp/gsp_accept_l3_results.json")


if __name__ == "__main__":
    main()

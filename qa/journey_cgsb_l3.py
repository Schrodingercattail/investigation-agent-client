"""WP-GS-B L3 — real-browser journey for destination-identity evidence.

Canonical :3001 → :8001 (flags OFF; GS-B is tool-layer and flag-
independent; latest worktree backend PID verified fresh) → RP :8000.

Journey: focused withdrawal investigation (F3, 9 withdrawals) → the
evidence table must render the per-record 目的地 identifiers (from the
destination_identity comparison facts), cross-checked against the
structured API/store evidence for the same task.
"""
import json
import sqlite3
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
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
        if new_card and not running and any(m in body for m in (
                "The investigation completed",
                "Investigation completed",
                "Investigation is blocked",
                "Investigation step failed")):
            time.sleep(1.5)
            return
        time.sleep(3)


def new_case_session(b):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def store_doc(task_id):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT document FROM agent_states WHERE task_id = ?",
            (task_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def run_journey(name, goal, must, never, shot, focus="F3"):
    b = Browser()
    rec = {"input": goal}
    try:
        b.start(FRONTEND)
        new_case_session(b)
        if focus:
            rec["focus_click"] = click_finding_row(b, focus)
            time.sleep(1.0)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        wait_loop_done(b, before_cards=before)
        time.sleep(1.0)
        body = b.dom_text("body")
        rec["browser"] = {
            "has_expected": all(m in body for m in must),
            "no_forbidden": all(m not in body for m in never),
        }
        b.screenshot(f"{SHOTS}/{shot}")
        # structured cross-check: newest matching loop run in the store
        time.sleep(0.5)
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT task_id, document FROM agent_states "
                "ORDER BY rowid DESC LIMIT 6").fetchall()
        finally:
            conn.close()
        for tid, raw in rows:
            d = json.loads(raw)
            if (d.get("goal") or "") == goal:
                rec["task_id"] = tid
                c = d.get("goal_contract") or {}
                rec["state"] = {
                    "status": d.get("agent_status"),
                    "stop": d.get("stop_reason"),
                    "contract_goal_type": c.get("goal_type"),
                    "observations": [
                        {"step": o.get("step_type"),
                         "has_destination_identity":
                             "destination_identity" in
                             (o.get("summary") or {})}
                        for o in (d.get("observations") or [])],
                }
                break
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
    print(f"[{name}]", json.dumps(
        {k: rec.get(k) for k in ("browser", "task_id", "error")},
        ensure_ascii=False)[:400], flush=True)


def main():
    # Journey: withdrawal-destination evidence with per-record 目的地
    run_journey(
        "cgsb_J1_withdrawal_destinations",
        "列出这个账号的全部提现记录。",
        must=["W000", "目的地"],
        never=["本次调查目标已完整回答"],
        shot="cgsb-J1-withdrawal-destinations.png", focus="F3")

    json.dump(RESULTS, open("/tmp/cgsb_l3_results.json", "w"),
              ensure_ascii=False, indent=1)
    print("saved /tmp/cgsb_l3_results.json", flush=True)


if __name__ == "__main__":
    main()

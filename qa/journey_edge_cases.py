"""J5-J9: rapid double-run, pre-loop fallback, RP-down retry→blocked,
CJK/whitespace goals, activity payload boundary checks."""
import json
import time

import httpx

from browser import Browser
from journeys_common import (SHOTS, activity_state, click_finding_row,
                             click_run_loop, new_browser, open_activity_tab,
                             send_message, type_input, wait_for)

BASE = "http://localhost:8001"


def create_inv():
    r = httpx.post(f"{BASE}/api/v2/investigations",
                   json={"case_reference": CASE}, timeout=30)
    return r.json()["investigation"]["investigation_id"]


CASE = "U00299"


def rapid_double_run():
    """J5: click Run loop twice in quick succession."""
    b = new_browser()
    try:
        new_inv_started = b.eval("""(() => {
            const btns=[...document.querySelectorAll('button')];
            const t=btns.find(x=>x.textContent.trim()==='New Investigation');
            if(!t) return 'NOT_FOUND'; t.click(); return 'CLICKED';
        })()""")
        time.sleep(1)
        send = send_message.__wrapped__ if False else None
        # inline: type + send
        type_input(b, "Investigate U00299")
        b.eval("document.querySelector('form').requestSubmit()")
        wait_for(b, "document.body.innerText.includes('F2')", 60,
                 desc="findings")
        click_finding_row(b, "F2")
        time.sleep(1)
        type_input(b, "show me the timeline")
        click_run_loop(b)
        time.sleep(0.15)   # deliberate rapid second click window
        second = click_run_loop(b)
        print("SECOND-CLICK-RESULT:", second)  # DISABLED expected while running
        time.sleep(2)
        runs = b.eval("""JSON.stringify({
            panels: document.querySelectorAll('[data-testid=agent-activity-panel]').length,
            status: document.querySelector('[data-testid=agent-status]')?.textContent,
        })""")
        print("AFTER-DOUBLE-CLICK:", runs)
        b.screenshot(f"{SHOTS}/13-double-click.png")
    finally:
        b.close()


def rp_down_retry_blocked():
    """J7: stop RP → run loop → retry → blocked (real negative flow)."""
    b = new_browser()
    try:
        b.eval("""(() => {
            const btns=[...document.querySelectorAll('button')];
            const t=btns.find(x=>x.textContent.trim()==='New Investigation');
            if(t) t.click();
        })()""")
        time.sleep(1)
        type_input(b, "Investigate U00299")
        b.eval("document.querySelector('form').requestSubmit()")
        # RP is DOWN: intake will fail → bounded error response in UI
        wait_for(b,
                 "document.body.innerText.toLowerCase().includes('could not be reached') || "
                 "document.body.innerText.toLowerCase().includes('failed') || "
                 "document.body.innerText.toLowerCase().includes('error')",
                 90, poll=1.0, desc="RP-down bounded error")
        text = b.dom_text()
        print("RP-DOWN-UI-EXCERPT:", text[-800:])
        b.screenshot(f"{SHOTS}/14-rp-down-turn.png")
    finally:
        b.close()


def edge_goals():
    """J8: whitespace goal via API; CJK goal via UI."""
    # whitespace goal through the API (the UI button is disabled on empty)
    r = httpx.post(f"{BASE}/api/v2/investigations",
                   json={"case_reference": CASE}, timeout=30)
    inv = r.json()["investigation"]["investigation_id"]
    g = httpx.post(f"{BASE}/api/v2/investigations/{inv}/goal",
                   json={"goal": "   "}, timeout=30)
    print("WHITESPACE-GOAL-STATUS:", g.status_code,
          g.json().get("detail", {}).get("code"))
    # unknown task id
    a = httpx.get(f"{BASE}/api/v2/tasks/NOPE-123/activity", timeout=30)
    print("UNKNOWN-TASK:", a.status_code, a.json()["detail"]["code"])

    # CJK goal through the real UI (loop on the fresh investigation)
    b = new_browser()
    try:
        b.eval("""(() => {
            const btns=[...document.querySelectorAll('button')];
            const t=btns.find(x=>x.textContent.trim()==='New Investigation');
            if(t) t.click();
        })()""")
        time.sleep(1)
        type_input(b, "Investigate U00299")
        b.eval("document.querySelector('form').requestSubmit()")
        wait_for(b, "document.body.innerText.includes('F2')", 60,
                 desc="findings for CJK run")
        click_finding_row(b, "F2")
        time.sleep(1)
        type_input(b, "檢查這個發現是否有跨賬戶對向交易")
        click_run_loop(b)
        time.sleep(1.5)
        if not b.eval(
                "!!document.querySelector('[data-testid=agent-activity-panel]')"):
            open_activity_tab(b)
        wait_for(b,
                 """(() => { const el = document.querySelector('[data-testid=agent-status]');
                     return el && el.getAttribute('data-agent-status') !== 'running'; })()""",
                 240, poll=1.0, desc="CJK loop terminal")
        final = activity_state(b)
        print("CJK-FINAL:", (final or "NONE")[:1200])
        b.screenshot(f"{SHOTS}/15-cjk-goal.png")
    finally:
        b.close()


def payload_boundary():
    """J9: activity payload size + field boundary on a real response."""
    r = httpx.get(f"{BASE}/api/v2/investigations", timeout=30)
    invs = r.json()["investigations"]
    latest = invs[0]["investigation_id"]
    tasks = httpx.get(f"{BASE}/api/v2/investigations/{latest}",
                      timeout=30).json()["tasks"]
    loop_tasks = [t for t in tasks if t["type"] == "agent_loop"]
    if not loop_tasks:
        print("PAYLOAD-BOUNDARY: no loop task in latest investigation")
        return
    a = httpx.get(f"{BASE}/api/v2/tasks/{loop_tasks[-1]['task_id']}/activity",
                  timeout=30)
    blob = a.text
    print("ACTIVITY-PAYLOAD-BYTES:", len(blob))
    for forbidden in ("risk_reason", "price", "quantity", "address",
                      "is_new_address", "secret"):
        print(f"  contains {forbidden}:", forbidden in blob)


if __name__ == "__main__":
    import sys
    name = sys.argv[1]
    globals()[name]()

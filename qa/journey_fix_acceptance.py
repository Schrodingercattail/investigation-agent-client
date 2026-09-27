"""Fix-phase browser journeys (QA-01..QA-09 acceptance)."""
import json
import time

from browser import Browser
from journeys_common import (SHOTS, activity_state, click_finding_row,
                             click_run_loop, new_browser, new_investigation,
                             open_activity_tab, send_message, type_input,
                             wait_for)


def open_tab_robust(b, label):
    return b.eval("""(label => {
        const t = [...document.querySelectorAll('[role=tab]')].find(
            t => t.textContent.trim() === label);
        if (!t) return 'NO_TAB';
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
            t.dispatchEvent(new MouseEvent(type, {bubbles: true,
                cancelable: true, view: window}));
        }
        return 'EVENTS_SENT';
    })""" + f"({json.dumps(label)})")


def focused_terminal_run(b, goal, timeout_s=240):
    """Focused loop via UI; returns terminal activity JSON from the panel."""
    type_input(b, goal)
    clicked = click_run_loop(b)
    assert clicked == "CLICKED", clicked
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = b.eval(
            "document.querySelector('[data-testid=agent-status]')"
            "?.getAttribute('data-agent-status')")
        if st and st != "running":
            time.sleep(0.5)
            return activity_state(b), st
        time.sleep(1)
    raise AssertionError("loop did not terminate in browser")


def setup_focused_investigation(b):
    """Fresh investigation + intake + F2 selection (pending focus)."""
    new_investigation(b)
    send_message(b, "Investigate U00299")
    wait_for(b, "document.body.innerText.includes('F2')", 60,
             desc="findings listed")


def main():
    b = new_browser()
    try:
        # ---------- J1: QA-01 focused run ----------
        setup_focused_investigation(b)
        b.screenshot(f"{SHOTS}/30-j1-focus.png")
        goal = "find cross-account opposite trades for this finding"
        type_input(b, goal)
        clicked = click_run_loop(b)
        print("J1 RUN-LOOP:", clicked)
        time.sleep(1.5)
        if not b.eval(
                "!!document.querySelector('[data-testid=agent-activity-panel]')"):
            open_tab_robust(b, "Activity")
            time.sleep(1)
        # network: the goal POST must carry context_action
        goal_bodies = b.eval("""performance.getEntriesByType('resource')
            .filter(e => e.name.includes('/goal')).map(e => e.name)""")
        print("J1 GOAL-CALLS:", len(goal_bodies))
        wait_for(b,
                 """(() => { const el = document.querySelector('[data-testid=agent-status]');
                     return el && el.getAttribute('data-agent-status') !== 'running'; })()""",
                 240, poll=1.0, desc="J1 terminal")
        final = json.loads(activity_state(b))
        print("J1 FOCUS-F2:", "finding F2" in final["text"])
        print("J1 STATUS:", final["status"], final["statusText"])
        print("J1 PLAN:", final["plan"].replace("\n", " | "))
        b.screenshot(f"{SHOTS}/31-j1-terminal.png")

        # ---------- J5: case-level run (no focus) ----------
        new_investigation(b)
        send_message(b, "Investigate U00299")
        wait_for(b, "document.body.innerText.includes('F2')", 60,
                 desc="findings")
        # NO selection
        goal2 = "case-level investigation overview"
        type_input(b, goal2)
        click_run_loop(b)
        wait_for(b,
                 """(() => { const el = document.querySelector('[data-testid=agent-status]');
                     return el && el.getAttribute('data-agent-status') !== 'running'; })()""",
                 240, poll=1.0, desc="J5 terminal")
        final2 = json.loads(activity_state(b))
        print("J5 CASE-LEVEL:", final2["status"],
              "| focus-case-level:", "case level" in final2["text"])
        b.screenshot(f"{SHOTS}/32-j5-caselevel.png")

        # ---------- J2 (QA-03): switch to pristine investigation ----------
        b.eval("""(() => {
            const btns = [...document.querySelectorAll('button')];
            const t = btns.find(x => x.textContent.includes('INV-16fcc4215d19'));
            if (t) t.click();
        })()""")
        wait_for(b, "document.body.innerText.includes('Restoring investigation') "
                    "|| true", 10, poll=0.3, desc="restore flash")
        deadline = time.time() + 30
        while time.time() < deadline:
            if b.eval(
                    "document.body.innerText.includes('No Agent Loop run yet')"):
                break
            time.sleep(0.5)
        no_stale = b.eval(
            "!document.body.innerText.includes(goal2)")
        print("J2 NO-STALE-ACTIVITY:", no_stale)
        b.screenshot(f"{SHOTS}/33-j2-no-stale.png")
    finally:
        b.close()


if __name__ == "__main__":
    main()

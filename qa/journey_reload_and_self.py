"""J3: reload reconstruction; J4: self-opposite loop; J5: rapid interactions."""
import json
import time

from browser import Browser
from journeys_common import (SHOTS, activity_state, click_finding_row,
                             click_run_loop, new_browser, new_investigation,
                             open_activity_tab, send_message, type_input,
                             wait_for)


def reload_reconstruction(b, label):
    b.navigate("http://localhost:3001/", settle=3)
    clicked = b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => x.textContent.includes('Investigation ·'));
        if (!t) return 'NOT_FOUND';
        t.click(); return 'CLICKED';
    })()""")
    print(f"REOPEN-CLICK[{label}]:", clicked)
    time.sleep(3)
    open_activity_tab(b)
    time.sleep(1.5)
    state = activity_state(b)
    print(f"ACTIVITY-AFTER-RELOAD[{label}]:", (state or "NONE")[:700])
    before = len([r for r in b.requests if "/activity" in r["url"]])
    time.sleep(5)
    after = len([r for r in b.requests if "/activity" in r["url"]])
    print(f"ACTIVITY-POLL-CALLS-OVER-5S[{label}]:", after - before)
    b.screenshot(f"{SHOTS}/10-reload-{label}.png")


def main():
    # ---- J3: reload after the previous completed run ----
    b = new_browser()
    try:
        reload_reconstruction(b, "completed-run")
    finally:
        b.close()

    # ---- J4: self-opposite loop on the same focused investigation ----
    b = new_browser()
    try:
        # reopen latest investigation (has F2 focus persisted)
        b.eval("""(() => {
            const btns = [...document.querySelectorAll('button')];
            const t = btns.find(x => x.textContent.includes('Investigation ·'));
            if (t) t.click();
        })()""")
        time.sleep(3)
        clicked = click_finding_row(b, "F2")
        print("REFOCUS-F2:", clicked)
        time.sleep(0.5)
        loop = click_run_loop(b)
        type_input(b, "show self-opposite trading for this finding")
        # order: type first then click (button needs non-empty input)
        if loop != "CLICKED":
            loop = click_run_loop(b)
        print("RUN-LOOP-SELF:", loop)
        time.sleep(1.5)
        if not b.eval(
                "!!document.querySelector('[data-testid=agent-activity-panel]')"):
            open_activity_tab(b)
        wait_for(b,
                 """(() => { const el = document.querySelector('[data-testid=agent-status]');
                     return el && el.getAttribute('data-agent-status') !== 'running'; })()""",
                 240, poll=1.0, desc="self-opposite loop terminal")
        time.sleep(0.5)
        final = activity_state(b)
        print("SELF-OPP-FINAL:", json.dumps(json.loads(final), indent=1)[:3000])
        b.screenshot(f"{SHOTS}/11-self-opposite-terminal.png")
    finally:
        b.close()


if __name__ == "__main__":
    main()

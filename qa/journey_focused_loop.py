"""J2b: focused loop journey — select finding, run a normal turn first
(workaround: persists focus), then run the loop and observe."""
import json
import time

from browser import Browser
from journeys_common import (SHOTS, new_browser, new_investigation,
                             send_message, wait_for, activity_state,
                             open_activity_tab)


def type_and_run_loop(b, goal):
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(goal)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.3)
    return b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => (x.title||'').includes('bounded Agent Loop'));
        if (!t) return 'NOT_FOUND';
        if (t.disabled) return 'DISABLED';
        t.click(); return 'CLICKED';
    })()""")


def main():
    b = new_browser()
    try:
        new_investigation(b)
        send_message(b, "Investigate U00299")
        wait_for(b, "document.body.innerText.includes('F2')", 60,
                 desc="findings listed")
        b.eval("""(() => {
            const rows = [...document.querySelectorAll('button')];
            const el = rows.find(e => e.innerText && e.innerText.includes('F2') && e.clientHeight > 0 && e.clientHeight < 140);
            if (!el) throw new Error('F2 row not found');
            el.click();
        })()""")
        time.sleep(1)
        send_message(b, "show the timeline for this finding")
        wait_for(b, "document.body.innerText.toLowerCase().includes('timeline')", 60,
                 desc="timeline response")
        b.screenshot(f"{SHOTS}/07-focused-turn.png")

        clicked = type_and_run_loop(
            b, "find cross-account opposite trades for this finding")
        print("RUN-LOOP:", clicked)
        time.sleep(1.5)
        in_activity = b.eval(
            "!!document.querySelector('[data-testid=agent-activity-panel]')")
        if not in_activity:
            open_activity_tab(b)
        focus_in_trace = b.eval(
            "document.querySelector('[data-testid=agent-activity-panel]')"
            "?.innerText.includes('finding F2')")
        print("ACTIVITY-FOCUS-F2:", focus_in_trace)
        b.screenshot(f"{SHOTS}/08-activity-running-focused.png")
        wait_for(b,
                 """(() => { const el = document.querySelector('[data-testid=agent-status]');
                     return el && el.getAttribute('data-agent-status') !== 'running'; })()""",
                 240, poll=1.0, desc="loop terminal")
        time.sleep(0.5)
        final = activity_state(b)
        print("ACTIVITY-FINAL:", json.dumps(json.loads(final), indent=1)[:3500])
        b.screenshot(f"{SHOTS}/09-activity-focused-terminal.png")
    finally:
        b.close()


if __name__ == "__main__":
    main()

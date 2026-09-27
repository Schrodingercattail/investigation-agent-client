"""Shared QA journey helpers (test harness only)."""
import json
import time

from browser import Browser

FRONTEND = "http://localhost:3001/"
SHOTS = "../qa/screenshots"


def new_browser():
    b = Browser()
    b.start(FRONTEND)
    time.sleep(1.5)
    return b


def wait_for(b, js_predicate, timeout=30, poll=0.5, desc="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if b.eval(js_predicate):
                return True
        except Exception:
            pass
        time.sleep(poll)
    b.screenshot(f"{SHOTS}/timeout-{desc.replace(' ', '-')}.png")
    raise AssertionError(f"timed out waiting for: {desc}")


def new_investigation(b):
    clicked = b.click_text("New Investigation", "button")
    assert clicked == "CLICKED", clicked
    time.sleep(1)


def send_message(b, text):
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(text)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.3)
    result = b.eval("""(() => {
        const f = document.querySelector('form');
        if (!f) return 'NO_FORM';
        f.requestSubmit(); return 'SUBMITTED';
    })()""")
    assert result == "SUBMITTED", f"send failed: {result}"


def click_finding_row(b, finding_id):
    return b.eval(f"""(() => {{
        const rows = [...document.querySelectorAll('button')];
        const el = rows.find(e => e.innerText && e.innerText.includes({json.dumps(finding_id)}) && e.clientHeight > 0 && e.clientHeight < 140);
        if (!el) return 'NOT_FOUND';
        el.click(); return 'CLICKED';
    }})()""")


def type_input(b, text):
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(text)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.3)


def click_run_loop(b):
    return b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => (x.title||'').includes('bounded Agent Loop'));
        if (!t) return 'NOT_FOUND';
        if (t.disabled) return 'DISABLED';
        t.click(); return 'CLICKED';
    })()""")


def activity_state(b):
    return b.eval("""(() => {
        const el = document.querySelector('[data-testid=agent-activity-panel]');
        if (!el) return null;
        const status = el.querySelector('[data-testid=agent-status]');
        return JSON.stringify({
            goal: el.querySelector('[data-testid=agent-goal]')?.textContent,
            status: status?.getAttribute('data-agent-status'),
            statusText: status?.textContent,
            iteration: el.querySelector('[data-testid=agent-iteration]')?.textContent,
            plan: el.querySelector('[data-testid=agent-plan]')?.innerText,
            items: el.querySelectorAll('[data-testid=agent-activity-item]').length,
            decisions: [...el.querySelectorAll('[data-testid=agent-decision]')].map(d => d.textContent.trim()),
            text: el.innerText.slice(0, 2500),
        });
    })()""")


def open_activity_tab(b):
    clicked = b.eval("""(() => {
        const tabs = [...document.querySelectorAll('[role=tab]')];
        const t = tabs.find(t => t.textContent.trim() === 'Activity');
        if (!t) return 'NOT_FOUND';
        t.click(); return 'CLICKED';
    })()""")
    time.sleep(0.6)
    return clicked

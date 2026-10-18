"""WP-GS-E — C8: real-browser reload DURING a real-model comparison
run (backend :8001 flags ON, NO seam — the run window is long enough
to reload mid-flight), then reopen-from-history consistency.

Verifies:
1. reloading mid-run does not corrupt or lose the run (the loop is
   server-side; the page must recover the run/terminal state);
2. after the terminal, the response (facet conclusion or honest
   boundary) renders;
3. History reopen of the SAME investigation shows the same semantic
   state (store-backed conversation restore).
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
GOAL = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"

results = {}

b = Browser()
try:
    b.start(FRONTEND)
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = b.eval(
        "document.querySelectorAll('.flex.justify-start').length")
    send_message(b, "给我输出一下00010案例情况")
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", 240, desc="intake")
    click_finding_row(b, "F3")
    time.sleep(1.0)
    before = b.eval(
        "document.querySelectorAll('.flex.justify-start').length")
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(GOAL)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.4)
    clicked = b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => (x.title||'').includes(
            'bounded Agent Loop'));
        if (!t || t.disabled) return 'NOT_CLICKABLE';
        t.click(); return 'CLICKED';
    })()""")
    results["loop_click"] = clicked
    time.sleep(5)                     # run is mid-flight (real model)
    b.start(FRONTEND)                 # FULL RELOAD mid-run
    results["reloaded"] = True
    b.screenshot(f"{SHOTS}/gse-C8-reload-midrun.png")
    # wait for the terminal to surface after the reload
    deadline = time.time() + 420
    body = ""
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        if (not running and any(m in body for m in (
                "个不同目的地", "笔提现指向同一目的地", "已部分回答",
                "尚未产生可判定的结果", "Investigation is blocked",
                "Investigation step failed", "failed"))):
            break
        time.sleep(3)
    time.sleep(1.5)
    response = b.eval("""(() => {
        const cards = [...document.querySelectorAll(
            '.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last ? last.innerText : '';
    })()""")
    results["post_reload_response_head"] = response[:500]
    results["verdict_rendered"] = any(
        m in response for m in ("个不同目的地", "笔提现指向同一目的地"))
    results["honest_boundary_or_verdict"] = any(
        m in response for m in ("个不同目的地", "笔提现指向同一目的地",
                                "已部分回答", "尚未产生可判定的结果"))
    b.screenshot(f"{SHOTS}/gse-C8-after-reload-terminal.png")
    # reopen from history: navigate away and back
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.5)
    hist = b.eval("""(() => {
        const items = [...document.querySelectorAll('button')].filter(
            e => e.innerText.includes('Investigation · U00010'));
        if (!items.length) return 'NO_HISTORY';
        items[0].click(); return 'REOPENED';
    })()""")
    results["history_reopen"] = hist
    time.sleep(2.5)
    body2 = b.dom_text("body")
    results["reopen_has_verdict_or_boundary"] = any(
        m in body2 for m in ("个不同目的地", "笔提现指向同一目的地",
                             "已部分回答", "尚未产生可判定的结果"))
    b.screenshot(f"{SHOTS}/gse-C8-history-reopen.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/gse-C8-FAIL.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = f"../docs/evaluations/wp-gse-c8-reload-results-{stamp}.json"
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:600], flush=True)
print("saved", out, flush=True)

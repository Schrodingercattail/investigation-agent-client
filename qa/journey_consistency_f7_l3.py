"""Cross-domain consistency II — F7 metric-population browser journey.

Real Chrome on :3001 → :8001 (flags ON, no seam) → RP :8000 (the
auto-reloaded instance, which serves feature_reference_time on the
loopback path since the Sep-19 duplicate listener was retired).

Journey: focused F7 (High Trading Frequency — summary claims 54 trades
in 24h), request the full transaction evidence. The response must show
the 64-record table AND the 总体口径说明 reconciling the summary metric
(54 within the scoring window before the exposed reference anchor)
against the exposed population (64, 10 outside the window) — the user
can verify both numbers instead of seeing an unexplained contradiction.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
GOAL = "列出这个账号的全部交易记录。"
results = {}


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
    r = "NOT_FOUND"
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
            return r
        time.sleep(2)
    return r


def wait_loop_done(b, before_cards, timeout=420):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        new_card = True
        try:
            new_card = cards(b) > before_cards
        except Exception:
            pass
        if (new_card and not running
                and any(m in body for m in (
                    "完整交易记录", "总体口径说明", "Investigation is blocked",
                    "Investigation step failed"))):
            time.sleep(1.5)
            return
        time.sleep(3)


def last_card_text(b):
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll(
            '.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last ? last.innerText : '';
    })()""")


b = Browser()
try:
    b.start(FRONTEND)
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")
    results = {"focus_click": click_finding_row(b, "F7")}
    time.sleep(1.0)
    before = cards(b)
    results["loop_click"] = type_and_run_loop(b, GOAL)
    wait_loop_done(b, before)
    time.sleep(1.0)
    response = last_card_text(b)
    results["notes"] = {
        "has_table": "完整交易记录共 64 条" in response,
        "has_reconciliation": "总体口径说明" in response,
        "has_claimed_54": "54 笔交易/24 小时" in response,
        "has_exposed_64": "全部 64 笔" in response,
        "has_outside_10": "10 笔" in response,
        "has_anchor": "2026-07-21T15:30:00+00:00" in response,
    }
    results["response_text"] = response[:1000]
    b.screenshot(f"{SHOTS}/consistency-F7-metric-population.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/consistency-F7-FAIL.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = f"../docs/evaluations/consistency-f7-browser-results-{stamp}.json"
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:500], flush=True)
print("saved", out, flush=True)

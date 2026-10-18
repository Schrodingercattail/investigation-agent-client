"""Cross-domain consistency — OUTCOME A: the audited 9-record
comparison on its own dataset (case U00020).

The forensic audit established that the audited question ("9笔提现都提
向同一个地址，还是每笔提向一个新地址？") belongs to case U00020: the
authoritative Finding F2 states "9 withdrawals were recorded in 24
hours" and the DB holds exactly 9 withdrawals (W000159–W000167) with 9
distinct destinations, all flagged new-address.

Journey: intake U00020 → focus F2 → the audited question → the
comparison must cover ALL NINE records (verifiable via the group
provenance: 9 groups × 1 record each, W000159–W000167) and render the
satisfied, complete conclusion — the true resolution of the original
9-versus-7 blocker.
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
                    "9 笔提现共指向", "已部分回答",
                    "尚未产生可判定的结果", "仅供核查", "无法对全部",
                    "Investigation is blocked",
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
    send_message(b, "给我输出一下00020案例情况")
    settled(b, before, 240, "intake U00020")
    results = {"focus_click": click_finding_row(b, "F2")}
    time.sleep(1.0)
    before = cards(b)
    results["loop_click"] = type_and_run_loop(b, GOAL)
    wait_loop_done(b, before)
    time.sleep(1.0)
    response = last_card_text(b)
    # Outcome A: ALL NINE record IDs W000159–W000167 compared, 9 groups
    ids = [f"W{sid}" for sid in
           ("000159", "000160", "000161", "000162", "000163",
            "000164", "000165", "000166", "000167")]
    results["notes"] = {
        "has_conclusion": "9 笔提现共指向 9 个不同目的地" in response,
        "all_nine_record_ids_present": all(w in response for w in ids),
        "has_complete_claim": "本次调查目标已完整回答" in response,
        "has_premise_note": "您的请求提及" in response,
    }
    results["response_text"] = response[:1400]
    b.screenshot(f"{SHOTS}/consistency-U00020-outcomeA-9records.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/consistency-U00020-FAIL.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = f"../docs/evaluations/consistency-u00020-outcomeA-{stamp}.json"
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:600], flush=True)
print("saved", out, flush=True)

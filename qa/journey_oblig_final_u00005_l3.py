"""U00005 chain C — final real-browser acceptance journey (L3).

Exercises the full compound request through the Run-loop surface with
all obligation-completeness fixes live. Verifies the five aspects in
the user-visible answer: both why-explanations (rule facts), the
destination comparison, the 10-record itemized details, and the
per-record is_new_address authoritative statuses.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

GOAL = ("高频提现和提现到新地址为什么是风险项，10笔提现提向同一地址还是不同地址，"
        "把提现具体明细发我")
results = {}
b = Browser()
try:
    b.start("http://localhost:3001/")
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden',
            {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState',
            {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    send_message(b, "Investigate U00005")
    wait_for(b, """(() => document.body.innerText.includes('finding'))()""",
             300, desc="intake U00005")
    time.sleep(1.0)
    before = b.eval("document.querySelectorAll('.flex.justify-start').length")
    results["loop_click"] = b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(GOAL)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => (x.title||'').includes(
            'bounded Agent Loop'));
        if (!t) return 'NOT_FOUND';
        if (t.disabled) return 'DISABLED';
        t.click(); return 'CLICKED';
    }})()""")
    deadline = time.time() + 480
    text = ""
    while time.time() < deadline:
        text = b.eval("""(() => {
            const cards = [...document.querySelectorAll(
                '.flex.justify-start')];
            const last = cards[cards.length - 1];
            return last ? last.innerText : '';
        })()""")
        n_now = b.eval(
            "document.querySelectorAll('.flex.justify-start').length")
        if n_now > before and ("整体结果" in text or "部分回答" in text):
            break
        time.sleep(4)
    time.sleep(1.0)
    results["response"] = text[:2400]
    results["why_F3_newaddr"] = ("First Withdrawal to New Address" in text
                                 or "First withdrawal to new address" in text)
    results["why_F2_freq"] = ("High Withdrawal Frequency" in text
                              or "High withdrawal frequency" in text)
    results["dest_10"] = "10 个不同目的地" in text
    results["records_10"] = text.count("W0000") >= 10
    results["newaddr_statuses"] = "是新地址" in text
    results["outcome_line"] = [l for l in text.split("\n")
                               if "整体结果" in l][:1]
    b.screenshot(f"{SHOTS}/oblig-final-C-browser.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/oblig-final-FAIL.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass
core = ["why_F3_newaddr", "why_F2_freq", "dest_10", "records_10",
        "newaddr_statuses"]
results["acceptance"] = "PASS" if (not results.get("error") and all(
    results.get(k) for k in core)) else "FAIL"
out = ("/Users/vv/investigation-agent-client/docs/evaluations/"
       "oblig-final-browser-%s.json"
       % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:800], flush=True)
print("ACCEPTANCE:", results["acceptance"], out, flush=True)

"""B-chain L3 — real-browser acceptance journey for the U00005
reverse-trade-rate question (planner coverage guarantee).

Real user entry point on the canonical stack (:3001 -> :8001 -> :8000,
all processes restarted onto the current working tree):
  New Investigation -> intake ("Investigate U00005") -> type the EXACT
  historical question -> run the bounded Agent Loop -> wait for the
  composed per-output answer -> verify the three-deliverable structure,
  the own-account metric definition (never substituted by the cross-
  account zero), the honest partial outcome, and the explain step in the
  Agent Activity trace.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

GOAL = ("34.12%的反向交易率是账户间的对冲交易还是账户自己的对冲交易？"
        "为什么反向交易是风险项？")
results = {"goal": GOAL}
b = Browser()
try:
    b.start("http://localhost:3001/")
    # keep the page visible for the whole run (headless tab-throttle guard)
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
    before = b.eval(
        "document.querySelectorAll('.flex.justify-start').length")
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
    deadline = time.time() + 600
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
        if n_now > before and ("整体结果" in text):
            break
        time.sleep(4)
    time.sleep(1.0)
    results["response"] = text[:3000]

    # --- acceptance checks on the user-visible answer -------------------
    a = {}
    a["metric_own_definition"] = (
        "账户自身的反向交易比例" in text and "min(BUY, SELL)" in text)
    a["metric_not_cross_account_rate"] = "不是跨账户匹配率" in text
    a["hedging_zero_separate"] = (
        "未发现" in text and "对冲交易候选" in text)
    a["why_section_present"] = "被标记为风险" in text
    a["honest_partial_outcome"] = (
        "部分回答" in text or "部分项目未能完整回答" in text)
    a["never_false_complete"] = "本次调查目标已完整回答" not in text
    # the explain step must appear in the Agent Activity trace
    try:
        b.eval("""(() => { const tabs = [...document.querySelectorAll(
            'button, [role=tab]')];
            const t = tabs.find(x => x.textContent.trim() === 'Activity');
            t && t.click(); })()""")
        time.sleep(1.0)
        trace = b.eval("document.body.innerText")
        a["activity_shows_explain_step"] = "explain_signal" in trace or \
            "signal_explain" in trace
        a["activity_shows_analysis"] = "cross_account" in trace
    except Exception as e:
        a["activity_error"] = str(e)[:120]
    results["assertions"] = a
    results["PASS"] = all(v is True for v in a.values())
    b.screenshot(f"{SHOTS}/bchain-u00005-coverage-browser.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/bchain-u00005-coverage-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/bchain_u00005_browser.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "response"}, ensure_ascii=False, indent=2))
    print("--- RESPONSE (first 1500) ---")
    print(results.get("response", "")[:1500])

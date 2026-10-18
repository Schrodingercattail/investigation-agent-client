"""P0/A-fix — real-browser journey with first-call LLM fault injection
(LLM_FAULT_EMPTY_FIRST=1 on the backend): the semantic normalizer's first
call returns empty (injected), the flow recovers, and the user sees the
F4 shared-device explanation with the honest threshold boundary.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

Q = "共享设备为什么是风险项，是否有判定阈值"
results = {"question": Q}
b = Browser()
try:
    b.start("http://localhost:3001/")
    time.sleep(3)
    b.eval("""(() => { const t = Array.prototype.slice.call(
        document.querySelectorAll('button'));
    for (const x of t) {
        if (x.textContent.includes('New Investigation')) { x.click(); break; } } })()""")
    time.sleep(1.5)
    send_message(b, "Investigate U00047")
    wait_for(b, "document.body.innerText.includes('finding')", 180,
             desc="intake U00047")
    time.sleep(1)
    send_message(b, Q)
    dl = time.time() + 240
    while time.time() < dl:
        st = b.eval("""(() => { const t = Array.prototype.slice.call(
            document.querySelectorAll('button'));
        const x = t.find(x => (x.title||'').includes('bounded Agent Loop'));
        return x ? (x.disabled ? 'DISABLED' : 'ENABLED') : 'NOT_FOUND'; })()""")
        if st == "ENABLED":
            break
        time.sleep(5)
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(Q)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    b.eval("""(() => { const t = Array.prototype.slice.call(
        document.querySelectorAll('button'));
    for (const x of t) {
        if ((x.title||'').includes('bounded Agent Loop')) { x.click(); break; } } })()""")
    dl = time.time() + 600
    text = ""
    while time.time() < dl:
        text = b.eval("""(() => {
            const cards = Array.prototype.slice.call(
                document.querySelectorAll('.flex.justify-start'));
            const last = cards[cards.length - 1];
            return last ? last.innerText : ''; })()""")
        if "整体结果" in text:
            break
        time.sleep(4)
    time.sleep(1)
    results["answer"] = text
    checks = {
        "f4_explained": ("Shared Device Relationships" in text
                         or "共享设备" in text),
        "authority_or_threshold": ("Risk Platform" in text
                                   or "阈值" in text),
        "complete": "本次调查目标已完整回答" in text,
        "no_false_failure": "failed" not in text,
    }
    results["checks"] = checks
    results["PASS"] = all(checks.values())
    b.screenshot(f"{SHOTS}/p0a-browser-retry-recovery.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/p0a-retry-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/p0a_browser_retry.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))

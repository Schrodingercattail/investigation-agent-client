"""P2 — real-browser journey on the user's ORIGINAL acceptance
conversation (INV-23f98bf579e9): metric question → focused answer, no
roster re-delivery; then 再列一次所有关联账号 → full roster delivery.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS, send_message

Q1 = "opposite-trade ratio of 34.29%是关联账户的对冲交易比例吗，这是怎么计算的？"
Q2 = "再列一次所有关联账号"
results = {}
b = Browser()
try:
    b.start("http://localhost:3001/")
    time.sleep(3)
    b.eval("""(() => { const items = Array.prototype.slice.call(
        document.querySelectorAll('button, [role=button], .cursor-pointer'));
    for (const el of items) {
        if ((el.innerText || '').indexOf('INV-23f98bf579e9') >= 0) {
            el.click(); return 'OPENED'; } }
    return 'NOT_FOUND'; })()""")
    time.sleep(2.5)

    def loop_run(q):
        send_message(b, q)
        dl = time.time() + 300
        while time.time() < dl:
            st = b.eval("""(() => { const t = Array.prototype.slice.call(
                document.querySelectorAll('button'));
            const x = t.find(x => (x.title||'').includes(
                'bounded Agent Loop'));
            return x ? (x.disabled ? 'DISABLED' : 'ENABLED')
                      : 'NOT_FOUND'; })()""")
            if st == "ENABLED":
                break
            time.sleep(5)
        b.eval(f"""(() => {{
            const i = document.querySelector('input');
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(i, {json.dumps(q)});
            i.dispatchEvent(new Event('input', {{bubbles: true}}));
        }})()""")
        b.eval("""(() => { const t = Array.prototype.slice.call(
            document.querySelectorAll('button'));
        for (const x of t) {
            if ((x.title||'').includes('bounded Agent Loop')) {
                x.click(); break; } } })()""")
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
        return text

    text1 = loop_run(Q1)
    results["metric_answer"] = text1[:900]
    c1 = {
        "metric_answered": ("反向交易率指标指的是" in text1
                            and "min(BUY, SELL)" in text1),
        "source_stated": "feature_evidence" in text1,
        "no_roster": "关联账号共" not in text1,
        "no_candidate_rows": "- CATA-" not in text1,
        "complete": "本次调查目标已完整回答" in text1,
    }
    results["metric_checks"] = c1
    b.screenshot(f"{SHOTS}/p2-metric-focused.png")

    text2 = loop_run(Q2)
    results["relist_answer"] = text2[:600]
    c2 = {
        "roster_delivered": ("关联账号" in text2 and "U00" in text2),
        "shared_device_evidence": "DEV8Z1O4A35QU" in text2
            or "共享设备" in text2,
        "complete": "本次调查目标已完整回答" in text2,
    }
    results["relist_checks"] = c2
    b.screenshot(f"{SHOTS}/p2-relist.png")

    results["PASS"] = (all(c1.values()) and all(c2.values()))
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/p2-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/p2_browser.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))

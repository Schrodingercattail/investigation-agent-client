"""P1 — U00008 real-browser journey: full delivery of all 11 candidates.

New Investigation U00008 -> intake -> the combined related-accounts +
hedging request through the bounded Agent Loop -> verify the visible
answer claims 11 candidates AND renders 11 candidate lines with
direction/tx ids, dual matching criteria, floor-admission labels, and
the budget-bounded retrieval disclosure with retrieved counts.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

Q = ("把关联账户的明细发我，并且查询关联账户中是否有对冲交易，"
     "如有，交易明细也发我")
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
    send_message(b, "Investigate U00008")
    wait_for(b, "document.body.innerText.includes('finding')", 180,
             desc="intake U00008")
    time.sleep(1)
    send_message(b, Q)
    dl = time.time() + 300
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
    cand_lines = [l for l in text.split("\n")
                  if l.strip().startswith("- CATA-")]
    checks = {
        "claimed_11": "发现关联账户集合内部候选对冲交易关系 11 组" in text,
        "lines_rendered_11": len(cand_lines) == 11,
        "all_direction": all(("BUY" in l and "SELL" in l)
                             for l in cand_lines),
        "floor_labeled_9": text.count("按绝对下限 放行") == 9,
        "truncated_disclosure": "已检索" in text and "受预算限制" in text,
        "complete": "本次调查目标已完整回答" in text,
    }
    results["checks"] = checks
    results["PASS"] = all(checks.values())
    b.screenshot(f"{SHOTS}/p1-u00008-11candidates.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/p1-u00008-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/p1_browser.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items() if k != "answer"},
                     ensure_ascii=False, indent=2))

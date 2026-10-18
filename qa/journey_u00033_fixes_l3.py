"""U00033 fixes 2-4 — real-browser journey (three questions, one case).

New Investigation U00033 -> intake -> for EACH of the three acceptance
questions: submit through the real UI, run the bounded loop, verify the
user-visible answer.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

QUESTIONS = [
    ("具体解释一下ML Pattern Detection检测到了什么风险",
     ["机器学习", "LightGBM", "不是校准过的欺诈概率", "归因边界",
      "不能证明"], ["未能从中呈现具体的解释性事实"], "完整回答"),
    ("高频交易活动和高频提现为什么是风险项，9笔提现是提向同一新地址还是不同地址，把具体提现明细发我",
     ["High withdrawal frequency", "withdrawal_frequency_24h > 5",
      "9 个不同目的地", "W000"], ["couldn't safely determine"], "部分回答"),
    ("把关联账户的明细发我，并且查询关联账户中是否有对冲交易，如有，交易明细也发我",
     ["关联账户", "对冲交易关系", "U00310"], [], "完整回答"),
]

results = {"questions": [q for q, _, _, _ in QUESTIONS]}
b = Browser()
try:
    b.start("http://localhost:3001/")
    time.sleep(3)
    b.eval("""(() => { const t = Array.prototype.slice.call(
        document.querySelectorAll('button'));
    for (const x of t) {
        if (x.textContent.includes('New Investigation')) { x.click(); break; } } })()""")
    time.sleep(1.5)
    send_message(b, "Investigate U00033")
    wait_for(b, """(() => document.body.innerText.includes('finding'))()""",
             300, desc="intake U00033")
    time.sleep(1.0)

    outcomes = []
    for idx, (q, must, mustnot, outcome_word) in enumerate(QUESTIONS, 1):
        send_message(b, q)
        # refill (form submit clears) then run the loop
        deadline_btn = time.time() + 240
        state = "PENDING"
        while time.time() < deadline_btn:
            b.eval(f"""(() => {{
                const i = document.querySelector('input');
                if (!i) return;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(i, {json.dumps(q)});
                i.dispatchEvent(new Event('input', {{bubbles: true}}));
            }})()""")
            state = b.eval("""(() => {
                const t = Array.prototype.slice.call(
                    document.querySelectorAll('button'));
                const x = t.find(x => (x.title||'').includes(
                    'bounded Agent Loop'));
                return x ? (x.disabled ? 'DISABLED' : 'ENABLED')
                         : 'NOT_FOUND'; })()""")
            if state == "ENABLED":
                break
            time.sleep(4)
        b.eval("""(() => { const t = Array.prototype.slice.call(
            document.querySelectorAll('button'));
        for (const x of t) {
            if ((x.title||'').includes('bounded Agent Loop')) {
                x.click(); break; } } })()""")
        deadline = time.time() + 600
        text = ""
        while time.time() < deadline:
            text = b.eval("""(() => {
                const cards = Array.prototype.slice.call(
                    document.querySelectorAll('.flex.justify-start'));
                const last = cards[cards.length - 1];
                return last ? last.innerText : ''; })()""")
            if "整体结果" in text:
                break
            time.sleep(4)
        time.sleep(1.0)
        checks = {f"q{idx}_has_{m[:12]}": (m in text) for m in must}
        checks.update({f"q{idx}_absent_{m[:12]}": (m not in text)
                       for m in mustnot})
        checks[f"q{idx}_outcome_line"] = outcome_word in text
        checks[f"q{idx}_passed"] = all(v for v in checks.values())
        outcomes.append({"q": idx, "task_text_head": text[:180],
                         **checks})
        b.screenshot(f"{SHOTS}/u00033-fix-q{idx}.png")
    results["outcomes"] = outcomes
    results["PASS"] = all(o["passed"] if isinstance(o.get("passed"), bool)
                          else o[f"q{o['q']}_passed"] for o in outcomes)
    results["PASS"] = all(o[f"q{o['q']}_passed"] for o in outcomes)
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/u00033-fix-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/u00033_browser.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2)[:3000])

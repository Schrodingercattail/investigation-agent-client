"""B-chain rule-explanation L3 — real-browser acceptance journey.

Real user entry point on the canonical stack (all processes on the
current tree): New Investigation -> intake -> the ORIGINAL question ->
bounded Agent Loop -> verify the five-distinction answer -> open the
final Artifact and verify answer/artifact CONSISTENCY (signal
explanation present, rule-status statement verbatim, authoritative-zero
NO_MATCH wording, no contradicting "insufficient" claim).
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
    time.sleep(2.0)
    try:
        b.eval("""(() => { try {
            Object.defineProperty(document, 'hidden',
                {get: () => false, configurable: true});
            Object.defineProperty(document, 'visibilityState',
                {get: () => 'visible', configurable: true});
        } catch (e) {} })()""")
    except Exception:
        pass
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
    results["response"] = text[:3500]

    a = {}
    # five distinctions in the user-visible answer
    a["d1_metric_own_semantics"] = (
        "账户自身的反向交易比例" in text and "不是跨账户匹配率" in text)
    a["d2_hedging_zero"] = "未发现涉及 U00005 的对冲交易候选" in text
    a["d3_monitored_category"] = (
        "风险因子记录" in text and "权威说明" in text)
    a["d4_rule_status_verbatim"] = (
        "below the 40% threshold" in text
        and "this rule was not triggered" in text)
    a["d4_category_not_case"] = "观测记录的存在不等于规则已触发" in text
    a["d5_evidence_boundary"] = "不构成风险判定" in text
    a["no_hit_rule_claim"] = "命中规则" not in text
    a["complete_outcome"] = "本次调查目标已完整回答" in text

    # open the FINAL (goal-run) artifact tab and verify consistency.
    # NOTE: this panel's tabs ignore synthetic el.click() — use REAL CDP
    # mouse events (Input.dispatchMouseEvent) at element centers.
    def real_click(find_js):
        rect = b.eval(
            "(() => { const el = " + find_js + "; if (!el) return null;"
            "const r = el.getBoundingClientRect();"
            "return JSON.stringify({x: r.x + r.width/2,"
            "y: r.y + r.height/2}); })()")
        if not rect:
            return 'NO_EL'
        p = json.loads(rect)
        for type_ in ("mousePressed", "mouseReleased"):
            b._send("Input.dispatchMouseEvent",
                    {"type": type_, "x": p["x"], "y": p["y"],
                     "button": "left", "clickCount": 1})
        return 'CLICKED'

    try:
        real_click("""Array.prototype.slice.call(document.querySelectorAll(
            'button')).find(x =>
            x.textContent.trim().startsWith('Artifacts'))""")
        time.sleep(2)
        real_click("""Array.prototype.slice.call(document.querySelectorAll(
            'button')).find(x => (x.textContent || '').trim() === '#2')""")
        time.sleep(5)
        art = b.eval("document.body.innerText")
        a["artifact_is_goal_bundle"] = (
            "Cross-Account Trade Evidence" in art)
        a["artifact_signal_explanation"] = "Signal Explanation" in art
        a["artifact_feature_observation"] = (
            "opposite_trade_ratio=0.3412" in art)
        a["artifact_rule_status"] = (
            "below the 40% threshold" in art
            and "not triggered" in art)
        a["artifact_authoritative_zero"] = "authoritative zero" in art
        a["artifact_no_insufficient_contradiction"] = (
            "insufficient for a reliable conclusion" not in art)
    except Exception as e:
        a["artifact_check_error"] = str(e)[:150]

    results["assertions"] = a
    results["PASS"] = all(v is True for v in a.values())
    b.screenshot(f"{SHOTS}/rule-explain-u00005-browser.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/rule-explain-u00005-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/rule_explain_u00005_browser.json", "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "response"}, ensure_ascii=False, indent=2))

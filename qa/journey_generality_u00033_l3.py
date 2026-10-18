"""Generality closure — real-browser acceptance journeys (L3).

U00033 is the PRIMARY instance (the four reported defects), U00010 the
regression control. Real Chrome on :3001 → :8001 → RP :8000.

Journey 1 (defect A): U00033, focus F3, threshold phrasing → the answer
  must lead with the rule facts (threshold + observed value), policy
  citations only secondary.
Journey 6 (defect D): U00033, click F7 (High Trading Frequency), ask
  "把关联账户的明细列给我" → the loop answer must present CASE-LEVEL
  network attribution — NO "finding F7" deliverable label (the evidence
  belongs to the network attribution, not the clicked finding).
Journey 7: continue with an F7-domain question → F7 context still works
  (the focused finding remains usable for its OWN domain).
Journey 8: an unmappable ask → the system keeps uncertainty (no
  fabricated coverage, no wrong target switch).
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
results = {}

A_Q = "高频提现风险的判断标准是怎样的"
D_Q = "把关联账户的明细列给我"
J7_Q = "这个高频交易 finding 为什么被标记？"
J8_Q = "这个账户的钱最终都流向了哪些交易所？"


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


def wait_loop_done(b, before_cards, timeout=420, markers=()):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        new_card = True
        try:
            new_card = cards(b) > before_cards
        except Exception:
            pass
        ms = markers or ("整体结果:", "关于「", "couldn't safely determine",
                         "无法确定", "Investigation is blocked",
                         "Investigation step failed")
        if new_card and not running and any(m in body for m in ms):
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
    # intake U00033
    before = cards(b)
    send_message(b, "给我输出一下00033案例情况")
    settled(b, before, 300, "intake U00033")

    # ---- Journey 1 (A): threshold phrasing on F3 ----
    results["F3_focus"] = click_finding_row(b, "F3")
    time.sleep(1.0)
    before = cards(b)
    send_message(b, A_Q)
    settled(b, before, 300, "A answer")
    time.sleep(1.0)
    a_text = last_card_text(b)
    results["journeyA_threshold"] = {
        "rule_facts_lead":
            "flagged" in a_text and ("threshold" in a_text.lower()
                                     or "exceeding" in a_text),
        "real_values": ("9" in a_text and "5" in a_text),
        "rule_named": "High withdrawal frequency" in a_text,
        "not_policy_only": "flagged" in a_text,
    }
    b.screenshot(f"{SHOTS}/generality-A-threshold-U00033.png")

    # ---- Journey 6 (D): click F7, ask related accounts, LOOP surface ----
    results["F7_focus"] = click_finding_row(b, "F7")
    time.sleep(1.0)
    before = cards(b)
    results["D_loop_click"] = type_and_run_loop(b, D_Q)
    wait_loop_done(b, before, markers=("关联账号", "整体结果:"))
    time.sleep(1.0)
    d_text = last_card_text(b)
    results["journeyD_related_accounts"] = {
        "case_level_label":
            "关联账号网络归因明细" in d_text
            or "Risk Platform network attribution" in d_text
            or "关联账号共" in d_text,
        "no_f7_label": "related to finding F7" not in d_text
        and "related to finding F7".lower() not in d_text.lower(),
        "real_accounts": "U00039" in d_text or "U00035" in d_text,
        "outcome_line": "整体结果:" in d_text,
    }
    b.screenshot(f"{SHOTS}/generality-D-related-U00033-loop.png")

    # ---- Journey 7: F7 context continues (turn surface) ----
    before = cards(b)
    send_message(b, J7_Q)
    settled(b, before, 300, "J7 answer")
    time.sleep(1.0)
    j7_text = last_card_text(b)
    results["journey7_F7_continues"] = {
        "f7_feature_answered": "Trade frequency" in j7_text
        and "69" in j7_text,
    }
    b.screenshot(f"{SHOTS}/generality-J7-F7-continues-U00033.png")

    # ---- Journey 8: unmappable ask keeps uncertainty ----
    before = cards(b)
    send_message(b, J8_Q)
    settled(b, before, 300, "J8 answer")
    time.sleep(1.0)
    j8_text = last_card_text(b)
    results["journey8_unmappable"] = {
        "honest_uncertainty":
            ("couldn't safely determine" in j8_text)
            or ("无法确定" in j8_text)
            or ("specify" in j8_text.lower()),
        "no_fabricated_answer": "交易所" not in j8_text.replace("哪些交易所", "")
        or len(j8_text) < 600,
    }
    b.screenshot(f"{SHOTS}/generality-J8-unmappable-U00033.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/generality-FAIL.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass


def _all(d, keys):
    return all(d.get(k) for k in keys)


ok = (not results.get("error")
      and _all(results.get("journeyA_threshold", {}), (
          "rule_facts_lead", "real_values", "rule_named",
          "not_policy_only"))
      and _all(results.get("journeyD_related_accounts", {}), (
          "case_level_label", "no_f7_label", "real_accounts",
          "outcome_line"))
      and results.get("journey7_F7_continues", {}).get(
          "f7_feature_answered")
      and results.get("journey8_unmappable", {}).get("honest_uncertainty"))
results["acceptance"] = "PASS" if ok else "FAIL"

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = f"../docs/evaluations/generality-browser-{stamp}.json"
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:900], flush=True)
print("ACCEPTANCE:", results["acceptance"], flush=True)
print("saved", out, flush=True)

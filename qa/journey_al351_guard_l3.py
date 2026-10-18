"""AL-3.5.1 L3 — real browser journeys for the target-validity guard.

Canonical :3001 → :8001 (guard ON) → RP :8000. Two journeys:
  1. S11-style wrong topic → honest clarify/blocked card, ZERO
     misleading (wrong-target) answer content.
  2. Valid finding (S06) → existing investigation UX intact.

No UI changes — verifies existing clarification/error rendering.
"""
import json
import sys
import time

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

FRONTEND = "http://localhost:3001/"


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


def settled(b, before, timeout, desc):
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", timeout, desc=desc)


def type_and_run_loop(b, goal, timeout=60):
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(goal)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.4)
    deadline = time.time() + timeout
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
            return "CLICKED"
        time.sleep(2)
    return r


def wait_loop_done(b, timeout=420, before_cards=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        new_card = True
        if before_cards is not None:
            try:
                new_card = cards(b) > before_cards
            except Exception:
                pass
        if (new_card and not running and any(m in body for m in (
                "The investigation completed",
                "Investigation completed",
                "Investigation is blocked",
                "Investigation step failed",
                "specify which one you mean",
                "clarification"))):
            time.sleep(1.5)
            return
        time.sleep(3)


def establish_case(b):
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def main():
    results = {}
    # --- Journey 1: S11-style wrong topic (guard ON) -------------------
    b = Browser(port=9229)
    b.start(FRONTEND)
    try:
        b.eval("""(() => { const t = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('New Investigation'));
            t && t.click(); })()""")
        time.sleep(1.2)
        establish_case(b)
        before = cards(b)
        type_and_run_loop(b, "谈谈虚拟币洗钱这个风险项")
        wait_loop_done(b, before_cards=before)
        body = b.dom_text("body")
        notes = {
            "blocked_or_clarify": (
                "Investigation is blocked" in body
                or "couldn't safely determine" in body
                or "couldn't reliably determine" in body
                or "specify which one you mean" in body),
            # answer-specific phrases: only an executed F1 explain would
            # render these (the intake/sidebar listing is not an answer to
            # the wrong-topic question)
            "no_wrong_target_answer": not any(
                m in body for m in ("关于发现 F1",
                                    "ML Pattern Detection 的含义")),
            "no_wrong_tool_evidence": not any(
                m in body for m in ("W000087", "Trade frequency (24h)")),
        }
        ok = notes["blocked_or_clarify"] and notes["no_wrong_target_answer"]
        b.screenshot(f"{SHOTS}/al351-l3-S11-clarify.png")
        results["S11_wrong_topic"] = {"status": "PASS" if ok else "FAIL",
                                      "notes": notes}
    except Exception as e:
        b.screenshot(f"{SHOTS}/al351-l3-S11-FAIL.png")
        results["S11_wrong_topic"] = {"status": "ERROR", "error": str(e)[:200]}
    finally:
        b.close()

    # --- Journey 2: valid finding (existing UX intact) ------------------
    b = Browser(port=9229)
    b.start(FRONTEND)
    try:
        b.eval("""(() => { const t = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('New Investigation'));
            t && t.click(); })()""")
        time.sleep(1.2)
        establish_case(b)
        before = cards(b)
        type_and_run_loop(b,
            "为什么说高频交易是风险项，给我输出高频交易的具体交易明细")
        wait_loop_done(b, before_cards=before)
        body = b.dom_text("body")
        notes = {
            "completed_answer": "Trade frequency (24h) = 54" in body
                                and "T00" in body,
            "activity_present": any(m in body for m in (
                "explain_signal", "Activity", "Plan")),
            "no_clarify": "Investigation is blocked" not in body,
        }
        ok = (notes["completed_answer"] and notes["activity_present"]
              and notes["no_clarify"])
        b.screenshot(f"{SHOTS}/al351-l3-S06-valid.png")
        results["S06_valid_finding"] = {"status": "PASS" if ok else "FAIL",
                                        "notes": notes}
    except Exception as e:
        b.screenshot(f"{SHOTS}/al351-l3-S06-FAIL.png")
        results["S06_valid_finding"] = {"status": "ERROR", "error": str(e)[:200]}
    finally:
        b.close()

    print(json.dumps(results, ensure_ascii=False, indent=1))
    json.dump(results, open("/tmp/al351_l3_results.json", "w"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

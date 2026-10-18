"""WP-CG-3 L3 — real browser journeys for contract-less safety.

Canonical :3001 → :8001 (GSP flag ON + scripted-session seam providing
the goal-understanding template; the loop, termination, composer and
Activity projection all run for real) → RP :8000.

Journey 1 (AMBIGUOUS): a compound user request whose scripted goal
understanding is genuinely ambiguous → the page must show the bounded
clarification terminal — NOT a partial answer presented as completion.

Journey 2 (INVALID_PROPOSAL): a scripted proposal that deterministic
validation rejects → restricted execution completes, and the page must
show the coverage boundary — never a whole-request completion claim.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (SHOTS, click_finding_row,  # noqa: E402
                             send_message, wait_for)

FRONTEND = "http://localhost:3001/"
RESULTS = {}


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


def wait_loop_done(b, timeout=120, before_cards=None):
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
        if (new_card and not running
                and any(m in body for m in (
                    "The investigation completed",
                    "Investigation completed",
                    "Investigation is blocked",
                    "Investigation step failed",
                    "无法确定您要调查的目标",
                    "未能建立完整的目标分解"))):
            time.sleep(1.5)
            return
        time.sleep(2)


def new_case_session(b):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def run_journey(name, goal, must_contain, must_not_contain, shot,
                focus=None):
    b = Browser()
    rec = {"input": goal}
    try:
        b.start(FRONTEND)
        new_case_session(b)
        if focus:
            rec["focus_click"] = click_finding_row(b, focus)
            time.sleep(1.0)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        wait_loop_done(b, before_cards=before)
        time.sleep(1.0)
        body = b.dom_text("body")
        rec["notes"] = {
            "has_expected": all(m in body for m in must_contain),
            "no_forbidden": all(m not in body for m in must_not_contain),
        }
        b.screenshot(f"{SHOTS}/{shot}")
    except Exception as e:
        try:
            b.screenshot(f"{SHOTS}/{shot.replace('.png', '-FAIL.png')}")
        except Exception:
            pass
        rec["error"] = str(e)[:300]
    finally:
        try:
            b.close()
        except Exception:
            pass
    RESULTS[name] = rec
    print(f"[{name}]", json.dumps(rec, ensure_ascii=False)[:400], flush=True)


def main(only=None):
    # Journey 1 — genuine AMBIGUOUS → bounded clarification terminal
    if only in (None, "cg3_ambiguous_clarify"):
        run_journey(
            "cg3_ambiguous_clarify",
            "这个账号有没有对冲交易？另外把它的关联账号也列出来。",
            must_contain=["无法确定您要调查的目标",
                          "The investigation is blocked"],
            must_not_contain=["已完整回答", "本次调查目标已完整回答"],
            shot="cg3-J1-ambiguous-clarify.png")

    # Journey 2 — invalid proposal → restricted execution + coverage
    # boundary (never a whole-request completion claim)
    if only in (None, "cg3_invalid_guard"):
        run_journey(
            "cg3_invalid_guard",
            "帮我梳理一下这个账号目前的情况。",
            must_contain=["未能建立完整的目标分解", "可能未覆盖"],
            must_not_contain=["本次调查目标已完整回答",
                              "matches multiple possibilities"],
            shot="cg3-J2-invalid-guard.png", focus="F1")

    json.dump(RESULTS, open("/tmp/cg3_l3_results.json", "w"),
              ensure_ascii=False, indent=1)
    print("saved /tmp/cg3_l3_results.json", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

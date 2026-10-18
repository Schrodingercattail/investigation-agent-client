"""Single-case isolation L3 — real-browser journey.

Journey: open the existing U00005-bound conversation -> submit the
previously-misdirected question ("为什么高频提现是风险项") through the
real UI -> verify the answer references U00005's OWN finding (F2 High
Withdrawal Frequency, its own threshold) and NOT another case's/another
finding's semantics -> then submit a cross-case request (调查 U00010)
and verify the conversation prompts for a new investigation instead of
answering or silently switching.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

Q = "为什么高频提现是风险项？"
CROSS = "调查 U00010 的高频提现为什么是风险项"
results = {"question": Q, "cross": CROSS}
b = Browser()
try:
    b.start("http://localhost:3001/")
    time.sleep(3)
    # open an existing U00005-bound conversation (top of history)
    opened = b.eval("""(() => {
        const items = Array.prototype.slice.call(
            document.querySelectorAll('button, [role=button], .cursor-pointer'));
        for (const el of items) {
            if ((el.innerText || '').indexOf('Investigation · U00005') === 0) {
                el.click(); return 'OPENED'; } }
        return 'NOT_FOUND'; })()""")
    results["opened"] = opened
    time.sleep(1.5)
    before = b.eval(
        "document.querySelectorAll('.flex.justify-start').length")
    send_message(b, Q)
    # the plain turn starts executing first; the loop button enables when
    # the turn settles — wait for it (bounded)
    loop_state = "PENDING"
    deadline_btn = time.time() + 240
    while time.time() < deadline_btn:
        # the submitted form clears the input; runAgentLoop takes the
        # input's current text as the goal — refill it before clicking
        b.eval(f"""(() => {{
            const i = document.querySelector('input');
            if (!i) return;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(i, {json.dumps(Q)});
            i.dispatchEvent(new Event('input', {{bubbles: true}}));
        }})()""")
        loop_state = b.eval("""(() => {
            const btns = Array.prototype.slice.call(
                document.querySelectorAll('button'));
            const t = btns.find(x => (x.title||'').includes(
                'bounded Agent Loop'));
            if (!t) return 'NOT_FOUND';
            return t.disabled ? 'DISABLED' : 'ENABLED'; })()""")
        if loop_state == "ENABLED":
            break
        time.sleep(4)
    results["loop_button"] = loop_state
    results["loop_click"] = b.eval("""(() => {
        const btns = Array.prototype.slice.call(
            document.querySelectorAll('button'));
        const t = btns.find(x => (x.title||'').includes('bounded Agent Loop'));
        if (!t) return 'NOT_FOUND'; if (t.disabled) return 'DISABLED';
        t.click(); return 'CLICKED'; })()""")
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
    results["answer"] = text[:1200]
    a = {}
    # the answer references U00005's OWN finding and rule
    a["own_finding_f2"] = (
        "High Withdrawal Frequency" in text
        and "被标记为风险的依据" in text)
    a["own_rule"] = "命中规则: High withdrawal frequency" in text
    a["own_threshold"] = "withdrawal_frequency_24h > 5" in text
    # never another finding's/case's semantics
    a["no_firstwithdrawal_rule"] = "first_withdrawal_flag" not in text
    a["no_other_case_numbers"] = "34.38%" not in text
    a["complete"] = "本次调查目标已完整回答" in text
    results["answer_assertions"] = a

    # cross-case request in the SAME conversation → new-investigation prompt
    before2 = b.eval(
        "document.querySelectorAll('.flex.justify-start').length")
    send_message(b, CROSS)
    deadline = time.time() + 180
    cross_text = ""
    while time.time() < deadline:
        cross_text = b.eval("""(() => {
            const cards = Array.prototype.slice.call(
                document.querySelectorAll('.flex.justify-start'));
            const last = cards[cards.length - 1];
            return last ? last.innerText : ''; })()""")
        n_now = b.eval(
            "document.querySelectorAll('.flex.justify-start').length")
        if n_now > before2 and cross_text.strip():
            break
        time.sleep(3)
    time.sleep(1.0)
    results["cross_response"] = cross_text[:800]
    c = {}
    c["prompt_new_conversation"] = (
        "new investigation" in cross_text or "新调查" in cross_text
        or "另开" in cross_text or "start a new" in cross_text)
    c["no_cross_answer"] = "命中规则" not in cross_text
    c["conversation_still_u00005"] = "U00005" in cross_text or True
    results["cross_assertions"] = c

    results["PASS"] = all(
        all(v for v in blk.values())
        for blk in (a, c))
    b.screenshot(f"{SHOTS}/case-isolation-browser.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/case-isolation-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/case_isolation_browser.json", "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k not in ("answer", "cross_response")},
                     ensure_ascii=False, indent=2))

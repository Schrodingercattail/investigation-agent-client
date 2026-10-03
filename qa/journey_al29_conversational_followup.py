"""AL-2.9: conversational follow-up finding resolution — REAL browser.

Real headless Chrome → real frontend (:3011 QA vite) → real QA backend
(:8010, current code) → real RP (:8000) → real LLM. No mocks.

The boundary AL-2.8 never crossed: the SECOND turn of one conversation.
Turn 1 establishes case-level context through the real UI; turn 2 is a
bare finding follow-up that must resolve against that context.

    a   展开一下00010的具体情况 → 为什么说共享设备是风险项   → F5
    b   展开一下00010的具体情况 → 为什么说高频交易是风险项，给我输出
        高频交易的具体交易明细                               → F7 (+F3 guard)
    cmp 调查00010              → (same B follow-up)          → F7
    n   展开一下00010的具体情况 → 谈谈虚拟币洗钱这个风险项     → bounded

Run: cd qa && ../backend/.venv/bin/python journey_al29_conversational_followup.py a
"""
import json
import os
import sys
import time

from browser import Browser
from journeys_common import SHOTS, open_activity_tab, activity_state, \
    send_message, wait_for

# Canonical override: FRONTEND_BASE=http://localhost:3001/ runs
# the journey against the canonical stack (vite.config.ts).
FRONTEND_QA = os.environ.get("FRONTEND_BASE",
                            "http://localhost:3011/")
CASE = "U00010"
UNAVAILABLE_MARKER = "语义解析服务当前不可用"

TURN1_A = "展开一下00010的具体情况"
TURN1_CMP = "调查00010"
FOLLOWUP_A = "为什么说共享设备是风险项"
FOLLOWUP_B = ("为什么说高频交易是风险项，"
              "给我输出高频交易的具体交易明细")
FOLLOWUP_N = "谈谈虚拟币洗钱这个风险项"


def agent_card_count(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


def new_conversation(b):
    b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => x.textContent.includes('New Investigation'));
        if (!t) return 'NOT_FOUND';
        t.click(); return 'CLICKED';
    })()""")
    time.sleep(1.2)


def wait_settled(b, before, timeout, desc):
    """New agent card AND the pending skeleton gone (real-LLM turns are
    slow; the skeleton card is also an agent card)."""
    wait_for(b,
             """(() => {
                 const cards = document.querySelectorAll('.flex.justify-start');
                 const pending = document.body.innerText.includes(
                     'Resolving context, planning, and executing');
                 return cards.length > """ + str(before) + """ && !pending;
             })()""", timeout, desc=desc)


def send_and_settle(b, message, timeout, desc, retries=1):
    """Send; if the HONEST unavailable card comes back instead of an
    answer, honor its own guidance and re-send (harness retry of a user
    action — never a product fallback)."""
    for attempt in range(retries + 1):
        before = agent_card_count(b)
        send_message(b, message)
        try:
            wait_settled(b, before, timeout, desc)
        except AssertionError:
            raise
        time.sleep(1.5)
        body = b.dom_text("body")
        last = body[body.rfind(message):]
        if UNAVAILABLE_MARKER not in last:
            return body
        print(f"  honest unavailable card; retrying (attempt {attempt + 1})…")
    return b.dom_text("body")


def reload_and_reopen(b, marker, label):
    b.navigate(FRONTEND_QA, settle=3)
    clicked = b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => x.textContent.includes('Investigation ·'));
        if (!t) return 'NOT_FOUND';
        t.click(); return 'CLICKED';
    })()""")
    assert clicked == "CLICKED", f"{label}: reopen failed: {clicked}"
    time.sleep(2)
    wait_for(b,
             "document.body.innerText.includes(" + json.dumps(marker) + ")",
             60, desc=f"{label}: persisted after reload/reopen")
    print(f"  {label}: retained after reload/reopen")


def run_scenario(key):
    b = Browser(port=9229)
    b.start(FRONTEND_QA)
    try:
        new_conversation(b)
        turn1 = TURN1_CMP if key == "cmp" else TURN1_A
        body = send_and_settle(b, turn1, 180, "turn1 case-level reply")
        assert ("findings were identified" in body
                or "Investigation Evidence Bundle" in body), \
            f"{key}: turn1 did not produce the case-level result"
        print(f"  [{key}] turn1 case-level context established")

        followup = {"a": FOLLOWUP_A, "b": FOLLOWUP_B,
                    "cmp": FOLLOWUP_B, "n": FOLLOWUP_N}[key]
        before = agent_card_count(b)
        send_message(b, followup)
        wait_settled(b, before, 300, f"{key} follow-up reply")
        time.sleep(1.5)
        body = b.dom_text("body")
        # full-body assertions: the DOM repeats the request text inside
        # the task card AFTER the reply, so tail-slicing would exclude it
        if key == "a":
            assert "graph-based risk detection" in body, \
                "A: answer is not the F5 graph explanation"
            assert "Shared devices linking these accounts" in body
            b.screenshot(f"{SHOTS}/al29-01-a-followup.png")
            reload_and_reopen(b, "graph-based risk detection", "scenario-a")
        elif key in ("b", "cmp"):
            assert "Trade frequency (24h) = 54" in body, \
                f"{key}: answer does not carry the real F7 feature fact"
            assert "transaction record" in body and "T00" in body, \
                f"{key}: answer does not carry the real transaction records"
            # NEVER the withdrawal answer / finding
            assert "withdrawal record" not in body, \
                f"{key}: answered with the WITHDRAWAL evidence stream"
            assert "Withdrawal frequency (24h)" not in body, \
                f"{key}: answered with the withdrawal finding's facts"
            shot = {"b": "al29-02-b-followup",
                    "cmp": "al29-03-cmp-intake-then-followup"}[key]
            b.screenshot(f"{SHOTS}/{shot}.png")
            reload_and_reopen(b, "Trade frequency (24h) = 54",
                              f"scenario-{key}")
        else:   # n
            honest = ("clarification_needed" in body
                      or "unresolved_reference" in body
                      or "matches multiple possibilities" in body
                      or "safely determine" in body
                      or "couldn" in body or "无法" in body)
            assert honest, "N: no bounded honest terminal"
            assert "Trade frequency" not in body \
                and "graph-based risk detection" not in body, \
                "N: fabricated a finding-level answer"
            b.screenshot(f"{SHOTS}/al29-04-negative.png")
        open_activity_tab(b)
        time.sleep(1)
        print("  ACTIVITY:", (activity_state(b) or "null")[:200])
        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        assert api_calls and not b.network_failures
        print(f"SCENARIO {key}: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al29-99-FAIL-{key}.png")
        print(f"SCENARIO {key}: FAIL —", e)
        raise
    finally:
        b.close()


def main():
    key = (sys.argv[1] if len(sys.argv) > 1 else "a").lower()
    assert key in ("a", "b", "cmp", "n"), f"unknown scenario {key!r}"
    run_scenario(key)


if __name__ == "__main__":
    main()

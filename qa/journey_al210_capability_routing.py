"""AL-2.10: capability routing + answer fidelity — REAL browser journey.

Canonical runtime: FRONTEND_BASE=http://localhost:3001/ (vite.config.ts)
→ :8001 (current code) → RP :8000 → real LLM. One scenario per Chrome.

    r   展开一下00010 → 哪些账号是这个账号的关联账号  → related-account
        capability (RP network attribution)
    b   展开一下00010 → 是否有其他账号…对冲交易…证据   → cross-account
        TRADE capability (case-level; NEVER finding resolution)
    s   Investigate U00010 → 为什么说共享设备是风险项   → F5 graph answer
        (citation-relevance: no case-level policy dump)
    d   展开一下00010 → 高频交易+具体交易明细           → F7 + feature
        fact + transaction records (detail fidelity)

Run: FRONTEND_BASE=http://localhost:3001/ ../backend/.venv/bin/python \
        journey_al210_capability_routing.py r
"""
import json
import os
import sys
import time

from browser import Browser
from journeys_common import SHOTS, activity_state, open_activity_tab, \
    send_message, wait_for

FRONTEND_BASE = os.environ.get("FRONTEND_BASE", "http://localhost:3001/")
TURN1 = "展开一下00010的具体情况"

SCENARIOS = {
    "r": ("能告诉我哪些账号是这个账号的关联账号吗",
          ["Risk Platform network attribution", "U00"],
          "al210-01-related-accounts"),
    "b": ("是否有其他账号和这个账号之间存在对冲交易，如果有，输出相关证据给我",
          ["candidate cross-account opposite-trade relationships"],
          "al210-02-hedge-capability"),
    "s": ("为什么说共享设备是风险项",
          ["graph-based risk detection",
           "Shared devices linking these accounts"],
          "al210-03-shared-device-answer"),
    "d": ("为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
          ["Trade frequency (24h) = 54", "transaction record"],
          "al210-04-detail-fidelity"),
}


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
    wait_for(b,
             """(() => {
                 const cards = document.querySelectorAll('.flex.justify-start');
                 const pending = document.body.innerText.includes(
                     'Resolving context, planning, and executing');
                 return cards.length > """ + str(before) + """ && !pending;
             })()""", timeout, desc=desc)


def reload_and_reopen(b, marker, label):
    b.navigate(FRONTEND_BASE, settle=3)
    clicked = b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => x.textContent.includes('Investigation ·'));
        if (!t) return 'NOT_FOUND';
        t.click(); return 'CLICKED';
    })()""")
    assert clicked == "CLICKED", f"{label}: reopen failed"
    time.sleep(2)
    wait_for(b,
             "document.body.innerText.includes(" + json.dumps(marker) + ")",
             60, desc=f"{label}: persisted")
    print(f"  {label}: retained after reload/reopen")


def run_scenario(key):
    followup, markers, shot = SCENARIOS[key]
    b = Browser(port=9229)
    b.start(FRONTEND_BASE)
    try:
        new_conversation(b)
        if key == "s":
            send_message(b, "Investigate U00010")
            wait_for(b,
                     "document.body.innerText.includes("
                     "'Shared Device Relationships')",
                     120, desc="findings listed")
        else:
            before = agent_card_count(b)
            send_message(b, TURN1)
            wait_settled(b, before, 180, "turn1 case-level reply")
            body = b.dom_text("body")
            assert ("findings were identified" in body
                    or "Investigation Evidence Bundle" in body), \
                f"{key}: turn1 failed"
        print(f"  [{key}] conversation context established")

        before = agent_card_count(b)
        send_message(b, followup)
        wait_settled(b, before, 300, f"{key} reply")
        time.sleep(2)
        body = b.dom_text("body")
        for marker in markers:
            assert marker in body, f"{key}: missing {marker!r}"
        # answer-fidelity guard for the shared-device scenario: no
        # unexplained case-level policy padding after the direct answer
        if key == "s":
            assert "case-level policy references apply" not in body, \
                "s: case-level policy dump still present"
            assert "Risk Platform has attached no finding-level" in body \
                or "policy reference" in body
        b.screenshot(f"{SHOTS}/{shot}.png")
        print(f"  [{key}] answer rendered with expected content")
        if key in ("r", "b", "d"):
            reload_and_reopen(b, markers[0], f"scenario-{key}")
        open_activity_tab(b)
        time.sleep(1)
        print("  ACTIVITY:", (activity_state(b) or "null")[:180])
        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        assert api_calls and not b.network_failures
        print(f"SCENARIO {key}: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al210-99-FAIL-{key}.png")
        print(f"SCENARIO {key}: FAIL —", e)
        raise
    finally:
        b.close()


def main():
    key = (sys.argv[1] if len(sys.argv) > 1 else "r").lower()
    assert key in SCENARIOS, f"unknown scenario {key!r}"
    run_scenario(key)


if __name__ == "__main__":
    main()

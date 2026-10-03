"""AL-2.11: unified finding-semantic entry — REAL browser journey.

Canonical runtime: FRONTEND_BASE=http://localhost:3001/ → :8001 (current
code) → RP :8000 → real LLM. With the lexicon removed, EVERY finding
reference below must flow through SemanticNormalizer → M2 grounding.

    1  为什么共享设备是风险项            → F5 (M2 grounding)
    2  高频提现有哪些证据               → F3 (M2 grounding)
    3  为什么说高频交易是风险项，给我输出高频交易的具体交易明细 → F7 ≠ F3

Scenario 4 (LLM unavailable) runs separately on a temporary no-key stack.
"""
import json
import os
import sys
import time

from browser import Browser
from journeys_common import SHOTS, open_activity_tab, activity_state, \
    send_message, wait_for

FRONTEND_BASE = os.environ.get("FRONTEND_BASE", "http://localhost:3001/")
TURN1 = "展开一下00010的具体情况"

SCENARIOS = {
    "1": ("为什么共享设备是风险项",
          ["graph-based risk detection",
           "Shared devices linking these accounts"],
          "al211-01-shared-device"),
    "2": ("高频提现有哪些证据",
          ["withdrawal record", "W00"],
          "al211-02-withdrawal-evidence"),
    "3": ("为什么说高频交易是风险项，给我输出高频交易的具体交易明细",
          ["Trade frequency (24h) = 54", "transaction record"],
          "al211-03-trading-detail"),
}


def agent_card_count(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


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
        b.eval("""(() => { const t = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('New Investigation'));
            t && t.click(); })()""")
        time.sleep(1.2)
        before = agent_card_count(b)
        send_message(b, TURN1)
        wait_settled(b, before, 180, "turn1 case-level reply")
        print(f"  [{key}] conversation context established")

        before = agent_card_count(b)
        send_message(b, followup)
        wait_settled(b, before, 300, f"{key} reply")
        time.sleep(2)
        body = b.dom_text("body")
        for marker in markers:
            assert marker in body, f"{key}: missing {marker!r}"
        b.screenshot(f"{SHOTS}/{shot}.png")
        print(f"  [{key}] answer rendered via the unified M2 path")
        reload_and_reopen(b, markers[0], f"scenario-{key}")
        open_activity_tab(b)
        time.sleep(1)
        print("  ACTIVITY:", (activity_state(b) or "null")[:160])
        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        assert api_calls and not b.network_failures
        print(f"SCENARIO {key}: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al211-99-FAIL-{key}.png")
        print(f"SCENARIO {key}: FAIL —", e)
        raise
    finally:
        b.close()


def main():
    key = (sys.argv[1] if len(sys.argv) > 1 else "1").lower()
    assert key in SCENARIOS, f"unknown scenario {key!r}"
    run_scenario(key)


if __name__ == "__main__":
    main()

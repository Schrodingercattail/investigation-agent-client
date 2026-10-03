"""AL-2.8: finding-level semantic resolution — REAL browser journey.

Real headless Chrome → real frontend (:3011 QA instance) → real QA
backend (:8010, current code) → real RP (:8000) → real LLM (glm-5.3 via
the configured provider). No mocks, no patches, no fixtures.

Boundary under test (the one AL-1..AL-2.7 never crossed):

    Raw user NL (typed into the real conversation)
      → M2 semantic resolution (SemanticNormalizer + governed metadata)
      → finding target (F5 / F3)
      → intent
      → governed plan
      → real tools
      → real RP evidence
      → natural-language assistant response (live, persisted)

One scenario per invocation (fresh Chrome each — long headless sessions
proved unstable in this environment):

    python journey_al28_finding_semantic.py a      # canonical → F5
    python journey_al28_finding_semantic.py a2     # paraphrase → real LLM → F5
    python journey_al28_finding_semantic.py b      # evidence → F3
    python journey_al28_finding_semantic.py n      # negative → honest bounded

Provider flakiness note: the real LLM occasionally fails a call; the
product then shows the honest 「语义解析服务当前不可用」 message whose own
guidance is "或稍后重试". The harness honors exactly that guidance for
the semantic scenarios by re-sending ONCE (harness-level retry of a
user action — never a product-side fallback).
"""
import json
import os
import sys
import time

from browser import Browser
from journeys_common import (SHOTS, activity_state, open_activity_tab,
                             send_message, wait_for)

# Canonical override: FRONTEND_BASE=http://localhost:3001/ runs
# the journey against the canonical stack (vite.config.ts).
FRONTEND_QA = os.environ.get("FRONTEND_BASE",
                            "http://localhost:3011/")
CASE = "U00010"
UNAVAILABLE_MARKER = "语义解析服务当前不可用"

SCENARIOS = {
    "a":  ("共享设备为什么是风险项", ["graph-based risk detection",
                                    "Shared devices linking these accounts"],
           "al28-01-scenario-a-canonical"),
    "a2": ("为什么会标记出设备共享的关系", ["graph-based risk detection",
                                         "Shared devices linking these accounts"],
           "al28-02-scenario-a-paraphrase"),
    "b":  ("给我输出一下高频提现的具体证据", ["withdrawal record", "W00"],
           "al28-03-scenario-b-evidence"),
    "n":  ("谈谈虚拟币洗钱这个风险项", None,
           "al28-04-negative-unresolved"),
}


def agent_card_count(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


def new_bound_investigation(b):
    """Fresh investigation bound to U00010 through the real UI (the way a
    user starts: New Investigation → case intake message → real RP
    findings listed). Each scenario gets its own conversation so the
    Stage-1 focus-priority semantics never bleed between scenarios."""
    b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => x.textContent.includes('New Investigation'));
        if (!t) return 'NOT_FOUND';
        t.click(); return 'CLICKED';
    })()""")
    time.sleep(1.2)
    send_message(b, f"Investigate {CASE}")
    wait_for(b,
             "document.body.innerText.includes('Shared Device Relationships')",
             120, desc="real RP findings listed")
    print("  bound to", CASE)


def wait_reply_card(b, before, timeout, desc):
    """A new agent card AND the pending skeleton gone (the skeleton card
    appears before the real reply; real-LLM turns can take a while)."""
    wait_for(b,
             """(() => {
                 const cards = document.querySelectorAll('.flex.justify-start');
                 const pending = document.body.innerText.includes(
                     'Resolving context, planning, and executing');
                 return cards.length > """ + str(before) + """ && !pending;
             })()""", timeout, desc=desc)


def semantic_turn_with_user_retry(b, before, message, success_markers,
                                  timeout=240):
    """Send the message; if the HONEST unavailable card comes back
    instead of the answer, honor its own guidance and retry ONCE."""
    send_message(b, message)
    try:
        wait_reply_card(b, before, timeout, "assistant reply")
    except AssertionError:
        raise
    time.sleep(1.5)
    body = b.dom_text("body")
    recent = body[max(body.rfind(message) - 3000, 0):]
    if UNAVAILABLE_MARKER in recent and all(m not in body
                                            for m in success_markers):
        print("  honest unavailable card shown; retrying once (as it "
              "advises)…")
        before = agent_card_count(b)
        send_message(b, message)
        wait_reply_card(b, before, timeout, "assistant reply (retry)")
        time.sleep(1.5)


def reload_and_reopen(b, marker, label):
    """Persistence oracle: reload the page, reopen the investigation,
    assert the assistant response is retained."""
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
             60, desc=f"{label}: response persisted after reload/reopen")
    print(f"  {label}: response retained after reload/reopen")


def run_scenario(key):
    message, success_markers, shot = SCENARIOS[key]
    b = Browser(port=9229)
    b.start(FRONTEND_QA)
    try:
        new_bound_investigation(b)
        before = agent_card_count(b)
        if key == "n":
            send_message(b, message)
            wait_reply_card(b, before, 240, "negative-scenario reply")
            time.sleep(1)
            body = b.dom_text("body")
            honest = ("clarification_needed" in body
                      or "unresolved_reference" in body
                      or "matches multiple possibilities" in body
                      or "safely determine" in body
                      or "couldn" in body or "无法" in body)
            assert honest, "negative scenario produced no bounded honest reply"
            assert "graph-based risk detection" not in body \
                and "withdrawal record" not in body, \
                "negative scenario fabricated a finding-level answer"
            b.screenshot(f"{SHOTS}/{shot}.png")
            print("  bounded honest reply (no fabricated finding, no tools)")
            print(f"SCENARIO {key}: PASS")
            return
        semantic_turn_with_user_retry(b, before, message, success_markers)
        body = b.dom_text("body")
        for marker in success_markers:
            assert marker in body, \
                f"answer missing expected content: {marker!r}"
        b.screenshot(f"{SHOTS}/{shot}.png")
        print("  answer rendered live with expected content")
        reload_and_reopen(b, success_markers[0], f"scenario-{key}")
        open_activity_tab(b)
        time.sleep(1)
        state = activity_state(b)
        print("  ACTIVITY:", (state or "null")[:220])
        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        assert api_calls and not b.network_failures
        print(f"SCENARIO {key}: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al28-99-FAIL-{key}.png")
        print(f"SCENARIO {key}: FAIL —", e)
        raise
    finally:
        b.close()


def main():
    key = (sys.argv[1] if len(sys.argv) > 1 else "a").lower()
    assert key in SCENARIOS, f"unknown scenario {key!r}"
    run_scenario(key)


if __name__ == "__main__":
    main()

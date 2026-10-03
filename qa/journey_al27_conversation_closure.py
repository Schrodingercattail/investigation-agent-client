"""AL-2.7: case-level NL + conversational closure — REAL browser journey.

Real headless Chrome → real frontend (:3001) → real backend (:8001) →
real RP (:8000). No mocks, no patches, no fixtures.

Scenario A: in a U00010-bound conversation, the natural case-level
  request "展开一下00010案例情况" (Send → /turn) gets a real assistant
  reply — NOT unresolved_reference.
Scenario B: the raw cross-account goal (Run Loop → /goal) completes and
  an assistant conversation response appears LIVE (no reload), produced
  from the run's formal composition; reload + reopen retain it.

The four oracles are asserted separately: runtime completion (terminal
status), Activity projection (trace events), Conversation response (new
agent turn in the chat), Persistence (after reload/reopen).

Run: cd qa && ../backend/.venv/bin/python journey_al27_conversation_closure.py
"""
import json
import time
import urllib.request

from browser import Browser
from journeys_common import (FRONTEND, SHOTS, activity_state, new_browser,
                             new_investigation, open_activity_tab,
                             send_message, wait_for)

CASE = "U00010"
CASE_LEVEL_TEXT = "展开一下00010案例情况"
RAW_GOAL = ("查一下有没有不同账户之间的对冲交易情况，如果有，输出相关证据给我；"
            "如果没有，告诉我你都查了哪些范围内的情况从而判定没有关联交易的")


def rp_oracle_finding_title(case):
    req = urllib.request.Request(
        "http://localhost:8000/api/risk/explain",
        data=json.dumps({"user_id": case}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        explanation = json.loads(r.read())
    kf = (explanation.get("key_findings") or [])[0]
    body = kf.split(". ", 1)[1] if ". " in kf[:6] else kf
    return body.split("\n")[0].split(" — ")[0].strip()


def agent_card_count(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


def type_and_run_loop(b, goal):
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(goal)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.3)
    return b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const t = btns.find(x => (x.title||'').includes('bounded Agent Loop'));
        if (!t) return 'NOT_FOUND';
        if (t.disabled) return 'DISABLED';
        t.click(); return 'CLICKED';
    })()""")


def main():
    oracle_title = rp_oracle_finding_title(CASE)
    print("ORACLE (live RP finding title):", oracle_title)

    b = new_browser()
    try:
        # bind the conversation the way a real user would (working phrasing)
        new_investigation(b)
        send_message(b, f"Investigate {CASE}")
        wait_for(b,
                 f"document.body.innerText.includes({json.dumps(oracle_title)})",
                 120, desc=f"real RP finding '{oracle_title}' listed")
        print("BOUND: conversation bound to", CASE)

        # ---------- Scenario A: case-level NL via Send (/turn) ------------
        before_a = agent_card_count(b)
        send_message(b, CASE_LEVEL_TEXT)
        wait_for(b,
                 """(() => {
                     const cards = document.querySelectorAll('.flex.justify-start');
                     return cards.length > """ + str(before_a) + """;
                 })()""", 90, desc="assistant reply for case-level request")
        time.sleep(1)
        body = b.dom_text("body")
        assert "No specific target could be resolved" not in body, \
            "case-level request still unresolved"
        assert "unresolved_reference" not in body
        # the reply corresponds to the real case (bundle / findings text)
        assert ("Investigation Evidence Bundle" in body
                or "findings were identified" in body), \
            "assistant reply does not correspond to the case intake result"
        b.screenshot(f"{SHOTS}/al27-01-case-level-reply.png")
        print("SCENARIO-A: assistant reply rendered live (agent cards:",
              agent_card_count(b), "was", before_a, ")")

        # ---------- Scenario B: loop + LIVE conversation response ---------
        before_b = agent_card_count(b)
        clicked = type_and_run_loop(b, RAW_GOAL)
        print("RUN-LOOP:", clicked)
        assert clicked == "CLICKED", clicked
        wait_for(b,
                 "!!document.querySelector('[data-testid=agent-activity-panel]')",
                 30, desc="activity panel shown")
        wait_for(b,
                 """(() => { const el = document.querySelector('[data-testid=agent-status]');
                     return el && el.getAttribute('data-agent-status') !== 'running'; })()""",
                 240, poll=2.0, desc="loop terminal")
        time.sleep(0.5)
        final = json.loads(activity_state(b))
        print("ACTIVITY-FINAL:", json.dumps(
            final, ensure_ascii=False, indent=1)[:2200])
        b.screenshot(f"{SHOTS}/al27-02-loop-terminal.png")

        # Oracle 1+2: runtime completion + Activity projection
        assert final["status"] == "completed", final["status"]
        text = final.get("text") or ""
        assert "Investigation context resolved" in text
        assert "Plan created" in text
        assert "cross account trade analysis" in text.lower()
        assert text.lower().count("capability returned") >= 2
        assert "Result summary" in text
        # Oracle 3: Conversation response — a NEW agent turn appears LIVE
        # with the run's formal composition (never Activity text scraping
        # for the answer: the marker is the composer's canonical recap)
        wait_for(b,
                 """(() => {
                     const cards = document.querySelectorAll('.flex.justify-start');
                     const body = document.body.innerText;
                     return cards.length > """ + str(before_b) + """
                         && body.includes('Steps completed');
                 })()""", 60, desc="assistant loop response in conversation")
        b.screenshot(f"{SHOTS}/al27-03-loop-reply-live.png")
        print("SCENARIO-B: assistant loop response rendered LIVE (agent cards:",
              agent_card_count(b), "was", before_b, ")")

        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        print("API-CALLS-FROM-PAGE:", len(api_calls))
        assert api_calls and not b.network_failures

        # ---------- Persistence: reload + reopen ---------------------------
        b.navigate(FRONTEND, settle=3)
        reopen = b.eval("""(() => {
            const btns = [...document.querySelectorAll('button')];
            const t = btns.find(x => x.textContent.includes('Investigation ·'));
            if (!t) return 'NOT_FOUND';
            t.click(); return 'CLICKED';
        })()""")
        print("REOPEN:", reopen)
        time.sleep(2)
        wait_for(b,
                 "/Artifacts \\(\\d+\\)/.test(document.body.innerText)",
                 30, desc="artifacts count after reopen")
        wait_for(b,
                 "document.body.innerText.includes('Steps completed')",
                 60, desc="loop response persisted after reload")
        b.screenshot(f"{SHOTS}/al27-04-reload-retained.png")
        restored = None
        deadline = time.time() + 20
        while time.time() < deadline and not restored:
            open_activity_tab(b)
            time.sleep(1.0)
            raw = activity_state(b)
            if raw:
                restored = json.loads(raw)
        assert restored and restored["status"] == "completed", \
            f"restored activity: {restored}"
        print("PERSISTENCE: response + activity retained after reload/reopen")
        print("JOURNEY: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al27-99-FAIL.png")
        print("JOURNEY: FAIL —", e)
        raise
    finally:
        b.close()


if __name__ == "__main__":
    main()

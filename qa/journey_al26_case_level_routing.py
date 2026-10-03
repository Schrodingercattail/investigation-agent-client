"""AL-2.6: case-level cross-account routing — REAL browser journey.

The EXACT defect scenario from the manual browser test, replayed in a
real headless Chrome over the real stack (frontend :3001 → backend :8001
→ real RP :8000), starting from the RAW natural-language goal. No mocks,
no patches, no fixtures, no pre-resolved goal.

Journey:
  1. New Investigation for U00010 (real RP findings rendered)
  2. Raw Chinese goal → bounded Agent Loop button
  3. Activity reaches a terminal completed state; the persisted trace
     must show Context → Planning → Tool → Observation really happened
  4. Reload + reopen → artifact count and completed Activity restored

Run: cd qa && ../backend/.venv/bin/python journey_al26_case_level_routing.py
"""
import json
import time
import urllib.request

from browser import Browser
from journeys_common import (FRONTEND, SHOTS, activity_state, new_browser,
                             new_investigation, open_activity_tab,
                             send_message, wait_for)

CASE = "U00010"
RAW_GOAL = ("查一下有没有不同账户之间的对冲交易情况，如果有，输出相关证据给我；"
            "如果没有，告诉我你都查了哪些范围内的账户从而判定没有关联交易的")


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
        new_investigation(b)
        send_message(b, f"Investigate {CASE}")
        wait_for(b,
                 f"document.body.innerText.includes({json.dumps(oracle_title)})",
                 120, desc=f"real RP finding '{oracle_title}' listed")
        b.screenshot(f"{SHOTS}/al26-01-real-findings.png")
        print("FINDINGS-LISTED: real RP finding title rendered in UI")

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
            final, ensure_ascii=False, indent=1)[:3000])
        b.screenshot(f"{SHOTS}/al26-02-loop-terminal.png")

        # terminal state: the run CONVERGED (was: blocked / context unresolved)
        assert final["status"] == "completed", \
            f"loop terminal status: {final['status']}"
        assert (final["goal"] or "").startswith("查一下有没有不同账户之间"), \
            f"activity goal: {final['goal']}"
        text = final.get("text") or ""
        # the persisted trajectory must prove the chain REALLY happened:
        # Context resolved (NOT "unresolved"), Planning, Tool, Observation
        assert "Investigation context resolved" in text, \
            "context resolution step not visible"
        assert "unresolved" not in text.lower(), \
            "run still reports unresolved context"
        assert "Plan created" in text, "planning step not visible"
        assert "cross account trade analysis" in text.lower() or \
            "cross_account_trade_analysis" in text, \
            "the governed analysis tool is not in the visible trace"
        assert text.lower().count("capability returned") >= 2, \
            "tool results not visible"
        assert "Result summary" in text, "observations not visible"
        has_art = b.eval(
            "!!document.querySelector('[data-testid=agent-artifact-ref]')")
        print("ARTIFACT-REF-VISIBLE:", has_art)
        assert has_art, "artifact reference not visible in the trace"

        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        print("API-CALLS-FROM-PAGE:", len(api_calls))
        assert api_calls, "no API calls observed from the page"
        assert not b.network_failures, \
            f"network failures: {b.network_failures}"

        # persistence: reload + reopen restores the run's products
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
                 30, desc="artifacts count shown after reopen")
        arts_count = b.eval(
            "(document.body.innerText.match(/Artifacts \\((\\d+)\\)/)||[])[1]")
        print("ARTIFACTS-AFTER-REOPEN:", arts_count)
        assert int(arts_count) >= 1, "loop artifact not restored after reopen"
        restored = None
        deadline = time.time() + 20
        while time.time() < deadline and not restored:
            open_activity_tab(b)
            time.sleep(1.0)
            raw = activity_state(b)
            if raw:
                restored = json.loads(raw)
        b.screenshot(f"{SHOTS}/al26-03-reload-restored.png")
        assert restored, "no activity restored after reload"
        assert restored["status"] == "completed", \
            f"restored activity status: {restored['status']}"

        print("JOURNEY: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al26-99-FAIL.png")
        print("JOURNEY: FAIL —", e)
        raise
    finally:
        b.close()


if __name__ == "__main__":
    main()

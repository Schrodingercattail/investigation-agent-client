"""AL-2.5: real-RP browser journey — the Golden User Journey over the
REAL stack only.

Real headless Chrome (CDP) → real frontend (:3001) → real backend
(:8001, real Agent Loop runtime) → real Risk Platform (:8000). No mocks,
no patches, no repo fixtures anywhere in this journey.

Journey:
  1. New Investigation
  2. Turn "Investigate U00299"        → the UI renders the REAL RP
     findings; the asserted finding title is fetched from the RP oracle
     (live HTTP) by this script, never hardcoded.
  3. Goal-loop "Generate the investigation bundle for U00299"
     (bounded Agent Loop)             → Activity panel reaches a terminal
     status with real tool events and the artifact reference.
  4. Reload + reopen                  → the completed Activity history is
     restored read-only from persisted state.

Run: cd qa && python3 journey_al25_real_rp_loop.py
"""
import json
import time
import urllib.request

from browser import Browser
from journeys_common import (FRONTEND, SHOTS, activity_state, new_browser,
                             new_investigation, open_activity_tab,
                             send_message, wait_for)

CASE = "U00299"
BUNDLE_GOAL = f"Generate the investigation bundle for {CASE}"


def rp_oracle_finding_title(case):
    """Fetch one REAL finding title directly from the running RP (used
    ONLY as the assertion oracle for what the browser must show)."""
    req = urllib.request.Request(
        f"http://localhost:8000/api/risk/explain",
        data=json.dumps({"user_id": case}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        explanation = json.loads(r.read())
    kf = (explanation.get("key_findings") or [])[0]
    body = kf.split(". ", 1)[1] if ". " in kf[:6] else kf
    name = body.split("\n")[0].split(" — ")[0].strip()
    return name


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
        # --- 1) open the real client and start an investigation ----------
        new_investigation(b)
        send_message(b, f"Investigate {CASE}")

        # --- 2) the findings shown must be the REAL RP data --------------
        wait_for(b,
                 f"document.body.innerText.includes({json.dumps(oracle_title)})",
                 120, desc=f"real RP finding '{oracle_title}' listed")
        b.screenshot(f"{SHOTS}/al25-01-real-findings.png")
        print("FINDINGS-LISTED: real RP finding title rendered in UI")

        # --- 3) run the bounded Agent Loop on the real backend -----------
        clicked = type_and_run_loop(b, BUNDLE_GOAL)
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
            final, ensure_ascii=False, indent=1)[:2500])
        b.screenshot(f"{SHOTS}/al25-02-loop-terminal.png")

        assert final["status"] == "completed", \
            f"loop terminal status: {final['status']}"
        assert final["items"] >= 6, \
            f"activity items: {final['items']}"
        assert (final["goal"] or "").startswith("Generate the investigation bundle"), \
            f"activity goal: {final['goal']}"
        has_art = b.eval(
            "!!document.querySelector('[data-testid=agent-artifact-ref]')")
        print("ARTIFACT-REF-VISIBLE:", has_art)
        assert has_art, "artifact reference not visible in the trace"

        # authenticity: the page really crossed the network (no local
        # fixture surface), and every request succeeded
        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        print("API-CALLS-FROM-PAGE:", len(api_calls))
        assert api_calls, "no API calls observed from the page"
        assert not b.network_failures, \
            f"network failures: {b.network_failures}"

        # --- 4) persistence: reload restores the completed run -----------
        b.navigate(FRONTEND, settle=3)
        reopen = b.eval("""(() => {
            const btns = [...document.querySelectorAll('button')];
            const t = btns.find(x => x.textContent.includes('Investigation ·'));
            if (!t) return 'NOT_FOUND';
            t.click(); return 'CLICKED';
        })()""")
        print("REOPEN:", reopen)
        time.sleep(2)
        # the reopened investigation's Artifacts tab must already carry the
        # loop-produced bundle (restored from the persisted read model)
        wait_for(b,
                 "/Artifacts \\(\\d+\\)/.test(document.body.innerText)",
                 30, desc="artifacts count shown after reopen")
        arts_count = b.eval(
            "(document.body.innerText.match(/Artifacts \\((\\d+)\\)/)||[])[1]")
        print("ARTIFACTS-AFTER-REOPEN:", arts_count)
        assert int(arts_count) >= 1, "loop artifact not restored after reopen"

        # the Activity tab restores the completed loop run read-only
        # (retry the tab switch until the panel is hydrated)
        restored = None
        deadline = time.time() + 20
        while time.time() < deadline and not restored:
            open_activity_tab(b)
            time.sleep(1.0)
            raw = activity_state(b)
            if raw:
                restored = json.loads(raw)
        print("ACTIVITY-AFTER-RELOAD:", json.dumps(
            restored, ensure_ascii=False)[:400] if restored else "NONE")
        b.screenshot(f"{SHOTS}/al25-03-reload-restored.png")
        assert restored, "no activity restored after reload"
        assert restored["status"] == "completed", \
            f"restored activity status: {restored['status']}"
        assert (restored["goal"] or "").startswith(
            "Generate the investigation bundle"), \
            f"restored goal: {restored['goal']}"

        print("JOURNEY: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al25-99-FAIL.png")
        print("JOURNEY: FAIL —", e)
        raise
    finally:
        b.close()


if __name__ == "__main__":
    main()

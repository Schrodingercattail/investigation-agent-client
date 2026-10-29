"""Trailing Plan block dedup — real-browser journey (U00033).

Real stack :3001 -> :8001 (real LLM) -> RP :8000.

Pins the final-message composition for bounded Agent Loop turns after the
Activity-in-conversation redesign:

  A. intake loop run  → absorbed agent turn carries the Activity block
                         (plan lives INSIDE it) and NO trailing PlanCard
  B. preserved        → Task completed status, findings_summary artifact
                         row + Artifact ID, answer text
  C. Activity block   → expands and shows the Plan section (plan preserved
                         at the Activity start)
  D. right panel      → artifact content renders (open-artifact ability)
  E. reload + reopen  → restored loop turn: same composition (no trailing
                         PlanCard, block + answer intact)
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
results = {}


def agent_cards(b):
    return b.eval(
        "[...document.querySelectorAll('.flex.justify-start')]")


def last_card_state(b):
    """Composition of the LAST agent card: block / plan-card / status /
    artifacts / answer — evaluated against one card element."""
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        if (!last) return 'NO_CARD';
        return JSON.stringify({
            activityBlock: !!last.querySelector(
                '[data-testid="agent-activity-block"]'),
            trailingPlanCard: !!last.querySelector(
                '[data-testid="plan-card"]'),
            taskCompleted: /Task/.test(last.innerText)
              && /completed/.test(last.innerText),
            findingsSummary: last.innerText.includes('findings_summary'),
            artifactId: /ART-[0-9a-f]/i.test(last.innerText),
            hasAnswer: last.innerText.length > 120,
        });
    })()""")


b = Browser()
try:
    b.start(FRONTEND)
    wait_for(b, "!!document.querySelector('form input')", 120,
             desc="app mounted")
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)

    # ---- A+B: intake loop run → absorbed turn composition ----------------
    send_message(b, "Investigate U00033")
    wait_for(b, """(() => {
        const s = document.querySelector('[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", 600, desc="intake loop terminal")
    wait_for(b, """(() => {
        const s = document.querySelector('[data-testid="activity-block-status"]');
        if (!s || s.textContent.includes('starting')) return false;
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last && last.innerText.length > 120;
    })()""", 120, desc="intake answer absorbed")
    time.sleep(1.5)
    state = json.loads(last_card_state(b))
    results["live_turn"] = state
    results["live_ok"] = (
        state["activityBlock"] and not state["trailingPlanCard"]
        and state["taskCompleted"] and state["findingsSummary"]
        and state["artifactId"] and state["hasAnswer"])
    b.screenshot(f"{SHOTS}/tpd-1-live-turn.png")

    # ---- C: the Activity block carries the Plan section ------------------
    # NB: the trigger is the block's DIRECT button child; clicking the
    # block root (querySelector comma lists return the ancestor first)
    # toggles nothing — Radix keeps collapsed content unmounted.
    b.eval("""(() => {
        const blk = document.querySelector(
            '[data-testid="agent-activity-block"]');
        const btn = blk && blk.querySelector(':scope > button');
        btn && btn.click(); })()""")
    time.sleep(0.8)
    results["block_plan_section"] = b.eval("""(() => {
        const blk = document.querySelector(
            '[data-testid="agent-activity-block"]');
        return JSON.stringify({
            plan: !!blk && !!blk.querySelector('[data-testid="activity-block-plan"]'),
            planRows: blk
              ? blk.querySelectorAll('[data-testid="activity-block-plan"] li').length
              : 0,
        });
    })()""")
    d = json.loads(results["block_plan_section"])
    results["block_plan_ok"] = d["plan"] and d["planRows"] > 0
    b.screenshot(f"{SHOTS}/tpd-2-block-plan.png")

    # ---- D: artifact renders in the right panel (open ability) ----------
    results["artifact_panel"] = b.eval("""(() => {
        const asides = document.querySelectorAll('aside');
        const right = asides[asides.length - 1];
        const t = right ? right.innerText : '';
        return JSON.stringify({
            hasFindingsSummary: t.includes('findings_summary')
              || t.includes('Investigation Evidence Bundle'),
            hasContent: t.length > 400,
        });
    })()""")
    d = json.loads(results["artifact_panel"])
    results["artifact_ok"] = d["hasFindingsSummary"] and d["hasContent"]
    b.screenshot(f"{SHOTS}/tpd-3-artifact-panel.png")

    # ---- E: reload + reopen → restored composition ----------------------
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00033'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(12)
    state = json.loads(last_card_state(b))
    results["restored_turn"] = state
    results["restore_ok"] = (
        state["activityBlock"] and not state["trailingPlanCard"]
        and state["taskCompleted"] and state["findingsSummary"]
        and state["artifactId"] and state["hasAnswer"])
    b.screenshot(f"{SHOTS}/tpd-4-restored.png")

    results["PASS"] = (
        results["live_ok"] and results["block_plan_ok"]
        and results["artifact_ok"] and results["restore_ok"])
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/tpd-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

print(json.dumps(results, indent=2, ensure_ascii=False))
sys.exit(0 if results.get("PASS") else 1)

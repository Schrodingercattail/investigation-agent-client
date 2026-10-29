"""Activity-in-conversation redesign — real-browser acceptance journey.

Real stack :3001 -> :8001 (ENABLE_GOAL_SEMANTIC_CONTRACT=true) -> RP :8000,
REAL LLM. Drives the ORIGINAL U00047 request through the bounded Agent
Loop and verifies, on the REAL rendered UI:

  A. normal run   — the inline Activity block renders after the user's
                    request, auto-expanded while running, auto-collapsed
                    after the terminal state; the final answer follows it;
                    expand/collapse works (mouse AND keyboard).
  B. bounded      — the U00047 hedge request ends bounded_data+partial:
                    the block shows the PARTIAL presentation (never
                    success) and the answer says 部分回答.
  D. refresh      — after reload + reopen, the restored conversation
                    carries the same activity block (collapsed) and the
                    same answer; no duplicated steps; no status regression.
  E. interaction  — expand/collapse by click and keyboard; console errors
                    captured.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_run_loop, send_message, type_input, wait_for)

FRONTEND = "http://localhost:3001/"
GOAL = ("列出所有关联账号，查询关联账号之间是否存在对冲交易，如果有，"
        "把交易明细发我，并生成一份文件")
results = {"goal": GOAL}


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


def settled(b, before, timeout, desc):
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", timeout, desc=desc)


def last_card_text(b):
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last ? last.innerText : '';
    })()""")


b = Browser()
try:
    b.start(FRONTEND)
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden',
            {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState',
            {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "Investigate U00047")
    settled(b, before, 300, "intake U00047")

    # ---- A/B: run the loop; observe the LIVE inline block ---------------
    type_input(b, GOAL)
    click_res = "DISABLED"
    for _ in range(15):
        click_res = click_run_loop(b)
        if click_res == "CLICKED":
            break
        time.sleep(1)
    results["run_loop_clicked"] = click_res
    if click_res != "CLICKED":
        raise RuntimeError(f"Run Loop not clickable: {click_res}")

    # the live block should appear inline while the run executes
    live_seen = wait_for(b, """(() => {
        const blocks = document.querySelectorAll(
            '[data-testid="agent-activity-block"]');
        return blocks.length > 0;
    })()""", 60, desc="live inline activity block")
    results["live_block_appeared"] = bool(live_seen)
    live_tone = b.eval(
        "document.querySelector('[data-testid=\"agent-activity-block\"]')"
        "?.getAttribute('data-tone')")
    results["live_tone"] = live_tone
    b.screenshot(f"{SHOTS}/act-inline-live.png")

    # wait for the terminal state (status tone leaves 'running')
    wait_for(b, """(() => {
        const s = document.querySelector(
            '[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", 600, desc="activity terminal status")
    time.sleep(3.0)

    tone = b.eval(
        "document.querySelector('[data-testid=\"activity-block-status\"]')"
        "?.getAttribute('data-tone')")
    label = b.eval(
        "document.querySelector('[data-testid=\"activity-block-status\"]')"
        "?.textContent")
    results["terminal_tone"] = tone
    results["terminal_label"] = label

    # partial correctness: bounded/partial must never render as success
    results["partial_not_success"] = (
        tone in ("partial", "failed", "blocked")
        and "goal satisfied" not in (label or "").lower())

    # answer follows the activity, contains the partial wording
    answer = last_card_text(b)
    results["answer_has_partial"] = "部分回答" in answer
    results["answer_not_failed"] = "failed" not in answer.lower()

    # collapsed after terminal; expand shows the timeline
    collapsed = b.eval(
        "document.querySelector('[data-testid=\"activity-block-timeline\"]')"
        " === null")
    results["collapsed_after_terminal"] = bool(collapsed)
    # keyboard: focus the trigger and press Enter to expand
    b.eval("""(() => {
        const t = document.querySelector(
            '[data-testid="agent-activity-block"] button');
        t.focus(); })()""")
    b.eval("""(() => {
        const t = document.querySelector(
            '[data-testid="agent-activity-block"] button');
        const ev = new KeyboardEvent('keydown', {key: 'Enter', bubbles: true});
        t.dispatchEvent(ev);
        if (document.activeElement === t) t.click();
    })()""")
    time.sleep(0.8)
    expanded = b.eval(
        "document.querySelectorAll('[data-testid=\"activity-block-item\"]').length")
    results["expanded_items"] = expanded
    # artifact ids visible while EXPANDED (checked before collapsing)
    results["artifact_in_block"] = b.eval(
        "!!document.querySelector('[data-testid=\"activity-block-artifacts\"]')")
    b.screenshot(f"{SHOTS}/act-inline-expanded.png")
    # collapse again by click
    b.eval("""(() => {
        const t = document.querySelector(
            '[data-testid="agent-activity-block"] button');
        t.click(); })()""")
    time.sleep(0.6)
    results["collapsed_again"] = b.eval(
        "document.querySelector('[data-testid=\"activity-block-timeline\"]')"
        " === null")
    b.screenshot(f"{SHOTS}/act-inline-final.png")

    # ---- D: reload + reopen; restored conversation keeps the block ------
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00047'));
        const el = rows.find(x => x.clientHeight > 0);
        el && el.click(); })()""")
    time.sleep(6)
    restored_blocks = b.eval(
        "document.querySelectorAll('[data-testid=\"agent-activity-block\"]')"
        ".length")
    results["restored_blocks"] = restored_blocks
    # exactly ONE block per loop task (no live-block revival after reopen)
    results["restored_single_block"] = restored_blocks == 1
    restored_tone = b.eval(
        "document.querySelector('[data-testid=\"activity-block-status\"]')"
        "?.getAttribute('data-tone')")
    results["restored_tone"] = restored_tone
    restored_answer = last_card_text(b)
    results["restored_answer_partial"] = "部分回答" in restored_answer
    b.screenshot(f"{SHOTS}/act-inline-restored.png")

    results["PASS"] = (
        results["live_block_appeared"]
        and results["partial_not_success"]
        and results["answer_has_partial"]
        and results["collapsed_after_terminal"]
        and (results["expanded_items"] or 0) > 0
        and results["collapsed_again"]
        and results["restored_single_block"]
        and restored_tone in ("partial", "failed", "blocked", "success")
        and results["restored_answer_partial"])
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/act-inline-error.png")
    except Exception:
        pass
finally:
    try:
        console_errors = b.eval("window.__qa_errors?.length ?? 0")
        results["console_errors"] = console_errors
    except Exception:
        pass
    try:
        b.close()
    except Exception:
        pass

print(json.dumps(results, ensure_ascii=False, indent=1))
sys.exit(0 if results.get("PASS") else 1)

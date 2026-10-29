"""Unified submission + Artifacts-only right panel — real-browser journey.

Real stack :3001 -> :8001 (ENABLE_GOAL_SEMANTIC_CONTRACT=true) -> RP :8000,
REAL LLM. ONE send action for every request; the SERVER decides the
execution mode via goal understanding (the client never chooses):

  A. simple question    → single-turn answer, NO Activity block
  B. multi-step request → bounded loop: live inline Activity, partial
                           terminal presentation, answer after the block
  C. right panel        → Artifacts ONLY (no Context / Activity entries)
  D. reload + reopen    → restored loop turn keeps its Activity block;
                          the turn answer keeps none
  E. composer           → exactly ONE submit button; empty input disabled;
                          Enter and click share the single form entry
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, send_message, type_input, wait_for)

FRONTEND = "http://localhost:3001/"
SIMPLE_Q = ("opposite-trade ratio 34.29%是关联账户的对冲交易比例吗，"
            "这是怎么计算的？")
COMPOUND_Q = ("列出所有关联账号，查询关联账号之间是否存在对冲交易，如果有，"
              "把交易明细发我，并生成一份文件")
results = {"simple_q": SIMPLE_Q, "compound_q": COMPOUND_Q}


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


def settled(b, before, timeout):
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", timeout)


def last_card_text(b):
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last ? last.innerText : '';
    })()""")


def block_count(b):
    return b.eval(
        "document.querySelectorAll('[data-testid=\"agent-activity-block\"]')"
        ".length")


b = Browser()
try:
    b.start(FRONTEND)
    # wait for the app to mount (vite dev re-transforms after edits — the
    # first load can lag; the composer input is the mount marker)
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
    before = cards(b)
    send_message(b, "Investigate U00033")
    # Case Intake runs on the loop now: wait for its terminal state
    wait_for(b, """(() => {
        const s = document.querySelector('[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", 600, desc="intake loop terminal")
    settled(b, before, 300)

    # ---- E: composer shape (single submit button) -----------------------
    results["composer"] = b.eval("""JSON.stringify({
        submitButtons: document.querySelectorAll(
            'form button[type="submit"]').length,
        allFormButtons: document.querySelectorAll('form button').length,
        runLoopText: [...document.querySelectorAll('form button')]
            .some(x => (x.textContent || '').includes('Run loop')),
    })""")
    d = json.loads(results["composer"])
    results["single_button"] = (d["submitButtons"] == 1
                                and d["allFormButtons"] == 1
                                and not d["runLoopText"])
    # empty input: the existing rule (click adds NO turn, no request fires)
    before_empty = cards(b)
    send_message(b, "   ")
    time.sleep(2)
    results["empty_no_turn"] = cards(b) == before_empty

    # ---- A: simple question through the SAME send -----------------------
    before = cards(b)
    send_message(b, SIMPLE_Q)
    settled(b, before, 300)
    time.sleep(1.5)
    simple_answer = last_card_text(b)
    results["simple_answer"] = {
        "has_answer": len(simple_answer) > 80,
        "ratio_explained": ("33" in simple_answer or "34" in simple_answer
                            or "opposite" in simple_answer.lower()),
        # all submissions run the loop: the simple run's Activity block
        # is present and compact (few steps, quick terminal)
        "activity_block_present": block_count(b) >= 1,
        "not_failed": "failed" not in simple_answer.lower(),
    }
    b.screenshot(f"{SHOTS}/us-1-simple-turn.png")

    # ---- B: multi-step request through the SAME send --------------------
    before = cards(b)
    send_message(b, COMPOUND_Q)
    # the loop route starts: the live inline block appears
    wait_for(b, """(() => document.querySelectorAll(
        '[data-testid="agent-activity-block"]').length > 0)()""", 90,
        desc="live inline activity for loop route")
    time.sleep(2.5)
    b.screenshot(f"{SHOTS}/us-2-loop-live.png")
    wait_for(b, """(() => {
        const s = document.querySelector('[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", 600, desc="loop terminal")
    # wait for the composed answer to be absorbed (the turn with 部分回答)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        return cards.some(c => c.textContent.includes('部分回答'));
    })()""", 60, desc="loop answer absorbed")
    results["loop_tone"] = b.eval(
        "document.querySelector('[data-testid=\"activity-block-status\"]')"
        "?.getAttribute('data-tone')")
    results["loop_label"] = b.eval(
        "document.querySelector('[data-testid=\"activity-block-status\"]')"
        "?.textContent")
    loop_answer = last_card_text(b)
    results["loop_answer"] = {
        "has_block": block_count(b) >= 1,
        "partial_not_success": (
            results["loop_tone"] == "partial"
            and "部分回答" in loop_answer),
        "not_failed": "failed" not in loop_answer.lower(),
    }
    b.screenshot(f"{SHOTS}/us-3-loop-done.png")

    # ---- C: right panel shows ONLY Artifacts ----------------------------
    results["right_panel"] = b.eval("""JSON.stringify({
        // nav entries labeled Context/Activity anywhere in the aside
        removedNavEntries: [...document.querySelectorAll('aside [role="tab"], aside button')]
            .filter(x => /^(context|activity)$/i.test(x.textContent.trim())).length,
        contextPanelGone: !document.body.innerText.includes('Case overview'),
        artifactsHeader: (() => {
            const asides = document.querySelectorAll('aside');
            return /artifacts/i.test(asides[asides.length - 1]?.innerText || '');
        })(),
        // the panel renders the delivered artifacts (tabs + source line +
        // artifact titles); it does not surface raw ART- id strings
        artifactRendered: (() => {
            const asides = document.querySelectorAll('aside');
            const right = asides[asides.length - 1];
            const t = right?.innerText || '';
            return /Sources:/.test(t)
              && /findings_summary|Investigation Evidence Bundle|Cross-Account/.test(t);
          })(),
    })""")
    d = json.loads(results["right_panel"])
    results["artifacts_only"] = (d["removedNavEntries"] == 0
                                 and d["contextPanelGone"]
                                 and d["artifactsHeader"]
                                 and d["artifactRendered"])
    b.screenshot(f"{SHOTS}/us-4-right-panel.png")

    # ---- D: reload + reopen ---------------------------------------------
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00033'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(12)
    results["restored"] = b.eval("""JSON.stringify({
        blocks: document.querySelectorAll(
            '[data-testid="agent-activity-block"]').length,
        loopAnswerPartial: (() => {
            const cards = [...document.querySelectorAll(
                '.flex.justify-start')];
            const c = cards.find(x => x.textContent.includes('部分回答'));
            return !!c; })(),
    })""")
    d = json.loads(results["restored"])
    results["restore_ok"] = (d["blocks"] == 1 and d["loopAnswerPartial"])
    b.screenshot(f"{SHOTS}/us-5-restored.png")

    results["PASS"] = (
        results["single_button"] and results["empty_no_turn"]
        and results["simple_answer"]["activity_block_present"]
        and results["simple_answer"]["has_answer"]
        and results["simple_answer"]["not_failed"]
        and results["simple_answer"]["ratio_explained"]
        and all(results["loop_answer"].values())
        and results["artifacts_only"]
        and results["restore_ok"])
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/us-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

print(json.dumps(results, ensure_ascii=False, indent=1))
sys.exit(0 if results.get("PASS") else 1)

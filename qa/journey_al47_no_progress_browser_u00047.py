"""U00047 no-progress fix — real-browser acceptance journey (goal loop).

Real stack :3001 -> :8001 (ENABLE_GOAL_SEMANTIC_CONTRACT=true) -> RP :8000,
REAL LLM. Drives the ORIGINAL failing user request through the BOUNDED
AGENT LOOP (the fixed machinery) and asserts on the REAL rendered UI:

  - the loop reaches a terminal state well inside the iteration bound
    (no budget-exhaustion terminal)
  - the user sees the candidate details with the retrieval-budget
    boundary disclosed and the honest partial wording — never a generic
    "Investigation step failed"

Artifact CONTENT single-rendering is verified against the persisted
artifact in the storage read-back (the browser journey asserts the
delivery surface; storage is the content authority).
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_run_loop, send_message, type_input, wait_for)

FRONTEND = "http://localhost:3001/"
GOAL = ("列出所有关联账号，查询关联账号之间是否存在对冲交易，如存在，"
        "列出对冲交易明细给我，并生成一份交易明细文件")
results = {"question": GOAL, "path": "bounded agent loop"}


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
        const cards = [...document.querySelectorAll(
            '.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last ? last.innerText : '';
    })()""")


b = Browser()
try:
    b.start(FRONTEND)
    # headless tab visibility: the activity hook skips polling while
    # document.hidden — simulate a visible tab (real-user state)
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

    # the original failing request — through the bounded Agent Loop
    type_input(b, GOAL)
    click_res = click_run_loop(b)
    results["run_loop_clicked"] = click_res
    if click_res != "CLICKED":
        raise RuntimeError(f"Run Loop button not clickable: {click_res}")

    # wait for the loop to reach a terminal agent status (real polling)
    wait_for(b, """(() => {
        const el = document.querySelector(
            '[data-testid=agent-activity-panel]');
        if (!el) return false;
        const st = el.querySelector('[data-testid=agent-status]');
        if (!st) return false;
        const s = st.getAttribute('data-agent-status') || '';
        return ['completed','failed','blocked','stopped']
            .some(k => s.includes(k));
    })()""", 600, desc="loop terminal status")
    time.sleep(2.0)

    panel = b.eval("""(() => {
        const el = document.querySelector(
            '[data-testid=agent-activity-panel]');
        if (!el) return null;
        const st = el.querySelector('[data-testid=agent-status]');
        return JSON.stringify({
            status: st ? st.getAttribute('data-agent-status') : null,
            statusText: st ? st.textContent : null,
            iteration: el.querySelector(
                '[data-testid=agent-iteration]')?.textContent,
        });
    })()""")
    results["activity"] = json.loads(panel) if panel else None

    time.sleep(1.5)
    answer = last_card_text(b)
    results["answer_head"] = answer[:1000]
    results["answer"] = {
        "not_generic_failure": "failed" not in answer.lower(),
        "boundary_disclosed": ("受预算限制" in answer or "预算" in answer
                               or "bounded" in answer.lower()
                               or "500" in answer),
        "candidate_details": "CATA-" in answer or "对冲交易关系" in answer,
        "account_list": ("关联账号" in answer or "U00305" in answer),
        "honest_partial": "部分" in answer or "partial" in answer.lower(),
        "artifact_delivered": "ART-" in answer,
    }
    b.screenshot(f"{SHOTS}/al47-loop-answer.png")

    status = (results["activity"] or {}).get("status") or ""
    results["loop"] = {
        "terminal_completed": "completed" in status,
        "not_limit": "limit" not in status and "stopped" not in status,
    }
    results["PASS"] = (
        results["run_loop_clicked"] == "CLICKED"
        and results["loop"]["terminal_completed"]
        and results["loop"]["not_limit"]
        and results["answer"]["not_generic_failure"]
        and results["answer"]["boundary_disclosed"]
        and results["answer"]["candidate_details"]
        and results["answer"]["honest_partial"]
        and results["answer"]["artifact_delivered"])
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/al47-loop-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

print(json.dumps(results, ensure_ascii=False, indent=1))
sys.exit(0 if results.get("PASS") else 1)

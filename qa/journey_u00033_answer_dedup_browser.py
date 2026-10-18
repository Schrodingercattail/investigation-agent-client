"""U00033 — real-browser journey: multi-output answer dedup.

Real stack :3001 -> :8001 (ENABLE_GOAL_SEMANTIC_CONTRACT=true) -> RP :8000,
REAL LLM. Drives the withdrawal question and asserts on the REAL rendered
UI card that the answer is deduplicated:

  - rule + threshold explanation visible
  - destination comparison conclusion visible
  - the withdrawal detail records appear exactly ONCE (no facet-table
    repetition of the same records)
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_run_loop, send_message, type_input, wait_for)

FRONTEND = "http://localhost:3001/"
QUESTION = ("高频提现的触发规则是什么？这些提现是提向相同地址还是不同地址？"
            "把提现明细发我")
results = {"question": QUESTION}


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
    send_message(b, "Investigate U00033")
    settled(b, before, 300, "intake U00033")

    before = cards(b)
    # the multi-output composer is the GOAL LOOP path — drive it through
    # the bounded Agent Loop control, not the plain message turn
    type_input(b, QUESTION)
    click_res = click_run_loop(b)
    results["run_loop_clicked"] = click_res
    if click_res != "CLICKED":
        raise RuntimeError(f"Run Loop button not clickable: {click_res}")
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
    time.sleep(2.5)
    answer = last_card_text(b)
    results["answer_head"] = answer[:900]
    results["answer"] = {
        "rule_threshold": "withdrawal_frequency_24h" in answer,
        "comparison_conclusion": ("不同目的地" in answer
                                  or "同一目的地" in answer),
        "destination_groups": "dest-1" in answer,
        "detail_present": "W000261" in answer,
        # the full DETAIL line (timestamp + summary) renders exactly once;
        # the record ID additionally appears in the destination grouping
        # (the comparison mapping) — that is the dedup contract
        "detail_once": len([ln for ln in answer.splitlines()
                            if ln.strip().startswith("- W0002")
                            and "|" in ln]) == 9,
        "no_facet_table": "| 记录 ID" not in answer
        and "| --- " not in answer,
        "not_failed": "failed" not in answer.lower(),
    }
    b.screenshot(f"{SHOTS}/u33-withdrawal-dedup.png")
    results["PASS"] = all(results["answer"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/u33-dedup-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

print(json.dumps(results, ensure_ascii=False, indent=1))
sys.exit(0 if results.get("PASS") else 1)

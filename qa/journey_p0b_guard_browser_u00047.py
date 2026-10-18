"""P0/B-closure — real-browser journey: degraded-contract completion
protection.

Backend started with ENABLE_GOAL_SEMANTIC_CONTRACT=true and CONTROLLED
FAULT INJECTION (LLM_FAULT_SKIP_FIRST=1 LLM_FAULT_EMPTY_FIRST=2 — the
goal-understanding model's two bounded attempts are injected empty; the
A-fix retry exhausts; the deterministic candidate contract stands). This
is controlled fault injection, NOT a real outage.

The user asks the original U00047 compound request in a fresh session.
The user must see: the recognized related-account list delivered, the
honest partial outcome + disclosure (无法确认已覆盖您请求的全部内容),
and NEVER the complete claim (本次调查目标已完整回答) nor a fabricated
hedging conclusion.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_run_loop, send_message, type_input, wait_for)

FRONTEND = "http://localhost:3001/"
COMPOUND_Q = "列出所有关联账号，查询其中是否存在对冲交易，如果有，发交易明细给我"
results = {"question": COMPOUND_Q,
           "note": "backend fault-injected goal understanding (degraded)"}


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
    send_message(b, "Investigate U00047")     # intake: call #1, healthy (skip window)
    settled(b, before, 300, "intake U00047")

    before = cards(b)
    # run through the bounded Agent Loop (the GOAL path — the surface
    # with GoalOutcome); the chat turn path is request-driven and would
    # plan from vocabulary instead of the degraded contract
    type_input(b, COMPOUND_Q)
    click_run_loop(b)
    # the loop composer ends with the 整体结果 outcome line
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return cards.length > """ + str(before) + """ && last
            && last.innerText.includes('整体结果');
    })()""", 600, desc="compound goal loop (degraded)")
    time.sleep(1.0)
    ans = last_card_text(b)
    results["answer_head"] = ans[:1000]
    results["checks"] = {
        "list_delivered": ("U00043" in ans or "U00044" in ans
                           or "关联账号" in ans or "Related" in ans),
        "outcome_partial_language":
            ("部分回答" in ans or "部分" in ans
             or "partially" in ans.lower()),
        "disclosure_visible":
            "无法确认已覆盖您请求的全部内容" in ans
            or "确定性识别" in ans,
        "no_complete_claim":
            "本次调查目标已完整回答" not in ans
            and "fully answered" not in ans,
        "no_fabricated_hedge":
            ("CATA-" not in ans and "候选对冲" not in ans
             and "集合内部" not in ans),
        "not_failed": "failed" not in ans.lower(),
    }
    results["PASS"] = all(results["checks"].values())
    b.screenshot(f"{SHOTS}/p0b-guard-browser-U00047.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/p0b-guard-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass
    with open("/tmp/p0b_guard_browser.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "answer_head"},
                     ensure_ascii=False, indent=2))
    print("ANSWER_HEAD:", results.get("answer_head", "")[:600])

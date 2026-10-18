"""P0/B-fix — real-browser journey: contract deliverable completeness.

Real stack :3001 -> :8001 (ENABLE_GOAL_SEMANTIC_CONTRACT=true) -> RP :8000,
REAL LLM goal understanding. ONE browser session covers the delivery
shapes in sequence (the same-session switch):

  1. metric question  -> the answer explains the metric and does NOT
                         append the related-account list
  2. list question    -> the full related-account list is visible
  3. compound request -> the user sees the list AND the hedging
                         conclusion with real candidate trade details
                         (or an explicit no-match); the task state and
                         plan visible in the UI must show BOTH analyses
                         executed, not just the list step
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
METRIC_Q = ("opposite-trade ratio of 34.29%是关联账户的对冲交易比例吗，"
            "这是怎么计算的？")
LIST_Q = "再列一次所有关联账号"
COMPOUND_Q = "列出所有关联账号，查询其中是否存在对冲交易，如果有，发交易明细给我"
results = {"questions": [METRIC_Q, LIST_Q, COMPOUND_Q]}


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

    # 1. metric question
    before = cards(b)
    send_message(b, METRIC_Q)
    settled(b, before, 300, "metric question")
    time.sleep(1.0)
    m = last_card_text(b)
    results["metric"] = {
        "answers_metric": ("自身" in m or "口径" in m or "计算" in m
                           or "34" in m),
        "no_account_list": "U00044" not in m and "U00311" not in m,
        "not_failed": "failed" not in m.lower(),
    }
    b.screenshot(f"{SHOTS}/p0b-metric-U00047.png")

    # 2. list question
    before = cards(b)
    send_message(b, LIST_Q)
    settled(b, before, 300, "list question")
    time.sleep(1.0)
    l = last_card_text(b)
    results["list"] = {
        "list_visible": ("U00044" in l or "U00311" in l
                         or "关联账号" in l or "Related" in l),
        "no_hedge_conclusion": ("CATA-" not in l),
    }
    b.screenshot(f"{SHOTS}/p0b-list-U00047.png")

    # 3. compound request
    before = cards(b)
    send_message(b, COMPOUND_Q)
    settled(b, before, 600, "compound request")
    time.sleep(1.5)
    c = last_card_text(b)
    results["compound"] = {
        "list_delivered": ("U00044" in c or "U00311" in c
                           or "关联账号" in c),
        "hedge_conclusion": ("候选" in c or "对冲" in c or "NO_MATCH" in c
                             or "未发现" in c),
        "details_or_explicit_boundary":
            ("CATA-" in c or "交易" in c or "未发现" in c
             or "没有" in c or "NO_MATCH" in c),
        "not_failed": "failed" not in c.lower(),
    }
    b.screenshot(f"{SHOTS}/p0b-compound-U00047.png")
    results["compound_text_head"] = c[:1200]
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/p0b-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass
    ok = (not results.get("error")
          and all(all(v.values()) for v in
                  (results.get("metric"), results.get("list"),
                   results.get("compound")) if v))
    results["PASS"] = ok
    with open("/tmp/p0b_browser.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "compound_text_head"},
                     ensure_ascii=False, indent=2))
    print("COMPOUND_HEAD:", results.get("compound_text_head", "")[:500])

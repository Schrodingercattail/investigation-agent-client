"""Q2/R0+R1 — real-browser journey: the U00008 multi-part withdrawal
question under controlled malformed-JSON fault injection.

Backend: ENABLE_GOAL_SEMANTIC_CONTRACT=true, LLM_FAULT_SKIP_FIRST=1,
LLM_FAULT_MALFORMED_JSON_FIRST=99 — every structured model call is served
broken JSON (the live failure class), so goal understanding runs on the
deterministic candidate contract and every decision goes through the
deterministic fallback (R1 must keep the loop executing through all
planned steps).

Journey (real frontend :3001 -> backend :8001 -> real RP :8000, Run-loop
path): New Investigation -> Investigate U00008 -> the Q2 question via the
bounded Agent Loop. The final visible answer must deliver, from real RP
evidence:
  - F2 High Withdrawal Frequency's rule/threshold;
  - whether the withdrawals point to the same or different addresses;
  - per-record withdrawal details (with is_new_address status where the
    evidence supports it);
and must NOT fabricate fields, with an honest overall outcome.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_run_loop, send_message, type_input, wait_for)

FRONTEND = "http://localhost:3001/"
Q = ("高频提现判定阈值是怎样的？全部提现都提现到新地址是同一地址还是不同地址？"
     "把提现明细发我")
results = {"question": Q,
           "note": "backend MALFORMED_JSON=99 SKIP=1 (all structured calls faulted)"}


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


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
    send_message(b, "Investigate U00008")
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", 300, desc="intake U00008")

    before = cards(b)
    type_input(b, Q)
    click_run_loop(b)
    # the loop composer ends with the 整体结果 outcome line
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return cards.length > """ + str(before) + """ && last
            && last.innerText.includes('整体结果');
    })()""", 900, desc="Q2 goal loop (all-structured-calls faulted)")
    time.sleep(1.0)
    ans = last_card_text(b)
    results["answer_head"] = ans[:1600]
    results["checks"] = {
        "f2_threshold": ("withdrawal_frequency_24h" in ans
                         or "高频提现" in ans),
        "destination_answered": ("同一" in ans or "不同" in ans),
        "per_record_details": ("is_new_address" in ans
                               or "提现明细" in ans or "逐笔" in ans),
        "outcome_line_present": "整体结果" in ans,
        "no_fabricated_complete_with_gaps": not (
            "尚未产生可判定的结果" in ans
            and "已完整回答" in ans),
        "not_failed": "failed" not in ans.lower(),
    }
    results["PASS"] = all(results["checks"].values())
    b.screenshot(f"{SHOTS}/q2-r0r1-browser-U00008.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/q2-r0r1-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass
    with open("/tmp/q2_r0r1_browser.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items()
                      if k != "answer_head"},
                     ensure_ascii=False, indent=2))
    print("ANSWER_HEAD:", results.get("answer_head", "")[:700])

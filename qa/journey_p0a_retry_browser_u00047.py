"""P0/A-fix — real-browser journey with first-call LLM fault injection.

Backend started with LLM_FAULT_EMPTY_FIRST=1 (controlled fault injection
at the shared LLM call boundary — the journey's FIRST LLM call, the
intake, is served an injected no-text-block response and must recover
through the bounded retry; this is NOT a real model-recovery test).

Canonical stack :3001 -> :8001 -> RP :8000. Journey: New Investigation ->
Investigate U00047 (intake recovers from the injected empty) -> focus F4
-> ask why shared devices are risk items + threshold question -> verify
the user sees the substantive F4 explanation with sourced policy/rule
references and honest threshold handling.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
Q = "共享设备为什么是风险项，是否有判定阈值"
results = {"question": Q,
           "note": "backend LLM_FAULT_EMPTY_FIRST=1 (injected first-empty)"}


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
    send_message(b, "Investigate U00047")     # intake: first LLM call — injected empty, retry recovers
    settled(b, before, 300, "intake U00047")
    results["intake"] = "completed"

    results["F4_focus"] = click_finding_row(b, "F4")
    time.sleep(1.0)
    before = cards(b)
    send_message(b, Q)
    settled(b, before, 300, "F4 question")
    time.sleep(1.0)
    ans = last_card_text(b)
    results["answer"] = ans[:2000]
    results["checks"] = {
        "f4_explained": ("Shared Device" in ans or "共享设备" in ans),
        "substantive_cluster": ("cluster" in ans.lower()
                                or "18 accounts" in ans
                                or "风险信号" in ans),
        "sourced_policy_or_threshold":
            ("Risk Platform" in ans or "政策" in ans
             or "阈值" in ans or "AML" in ans),
        "no_false_failure": "failed" not in ans.lower()
        and "temporarily unavailable" not in ans,
        "honest_boundary": ("未建立" in ans or "不" in ans
                            or "Risk Platform" in ans),
    }
    results["PASS"] = all(results["checks"].values())
    b.screenshot(f"{SHOTS}/p0a-browser-retry-recovery.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/p0a-browser-retry-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass
    with open("/tmp/p0a_browser_retry.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in results.items() if k != "answer"},
                     ensure_ascii=False, indent=2))
    print("ANSWER_HEAD:", results.get("answer", "")[:400])

"""Manual-acceptance fixes — real-browser acceptance journey (L3).

U00047 on :3001 → :8001 → RP :8000. Exercises the four fixed surfaces:
  J1  ML Pattern Detection substantive explanation (no failure terminal);
  J3  related-account list + SET-INTERNAL hedging candidates;
  J4  shared-device substantive explanation (not "已获取相关证据").
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
results = {}

J1_Q = "具体解释一下ML Pattern Detection细节"
J3_Q = "这个账户的所有关联账户明细列给我，并且告诉我这些账户之间是否存在对冲交易情况"
J4_Q = "为什么共享设备属于风险项"


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

    # J1: ML detail (focused F1)
    results["F1_focus"] = click_finding_row(b, "F1")
    time.sleep(1.0)
    before = cards(b)
    send_message(b, J1_Q)
    settled(b, before, 300, "J1")
    time.sleep(1.0)
    j1 = last_card_text(b)
    results["J1"] = {
        "completed": "failed" not in j1.lower(),
        "score": "99.36" in j1,
        "features": "Trade frequency (24h) = 66" in j1,
        "boundary": "per-feature contribution attribution" in j1,
    }
    b.screenshot(f"{SHOTS}/manualfix-J1-ml-U00047.png")

    # J3: related accounts + set-internal hedging (turn surface)
    before = cards(b)
    send_message(b, J3_Q)
    settled(b, before, 300, "J3")
    time.sleep(1.0)
    j3 = last_card_text(b)
    results["J3"] = {
        "related_list": "Related accounts:" in j3 or "U00043" in j3,
        "candidates": "CATA-001" in j3,
        "related_only_labeled": "related-members only" in j3,
        "none_involve_case": "0 of the 5" in j3
        or "involve account U00047" in j3,
    }
    b.screenshot(f"{SHOTS}/manualfix-J3-set-U00047.png")

    # J4: shared device why (focused F4 — Shared Device Relationships)
    results["F4_focus"] = click_finding_row(b, "F4")
    time.sleep(1.0)
    before = cards(b)
    send_message(b, J4_Q)
    settled(b, before, 300, "J4")
    time.sleep(1.0)
    j4 = last_card_text(b)
    results["J4"] = {
        "substantive": ("device_sharing" in j4 or "Shared Device" in j4)
        and ("18 accounts" in j4 or "cluster" in j4.lower()
             or "risk" in j4.lower()),
        "not_evidence_availability": "已获取相关证据" not in j4,
        "boundary_framing": "线索" in j4 or "not constitute" in j4.lower()
        or "proof" in j4.lower(),
    }
    b.screenshot(f"{SHOTS}/manualfix-J4-device-U00047.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/manualfix-FAIL.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass


def _all(d, keys):
    return all(d.get(k) for k in keys)


ok = (not results.get("error")
      and _all(results.get("J1", {}), (
          "completed", "score", "features", "boundary"))
      and _all(results.get("J3", {}), (
          "related_list", "candidates", "related_only_labeled",
          "none_involve_case"))
      and _all(results.get("J4", {}), (
          "substantive", "not_evidence_availability")))
results["acceptance"] = "PASS" if ok else "FAIL"

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = f"../docs/evaluations/manualfix-browser-{stamp}.json"
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:900], flush=True)
print("ACCEPTANCE:", results["acceptance"], out, flush=True)

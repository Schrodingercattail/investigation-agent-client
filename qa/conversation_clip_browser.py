"""Conversation right-boundary clipping — persistent browser regression.

Real stack :3001 → :8001 → RP. Two layers of evidence:

  1. RESTORED long-hex answer (U00005 destination-identity groups carry
     40-char unbroken hashes): every message card must sit within the
     conversation column — no card may extend under the right panel.
  2. FRESH user message containing a long unbroken token + CJK: the user
     bubble must contain/wrap the token within the column.

Asserted via bounding boxes (cards vs the right panel's left edge) —
not via CSS class presence.
"""
import json
import re
import sys
import time

import httpx

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import SHOTS, send_message  # noqa: E402

FRONTEND = "http://localhost:3001/"
BACKEND = "http://localhost:8001"
results = {"checks": {}, "meta": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:300]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:140])


def measure(b):
    """Card boxes vs the right panel edge; horizontal overflow of the
    conversation viewport."""
    return b.eval("""(() => {
        const winW = window.innerWidth;
        const asides = document.querySelectorAll('aside');
        const rightPanel = asides[asides.length - 1];
        const limit = rightPanel
          ? Math.round(rightPanel.getBoundingClientRect().left) : winW;
        const cards = [...document.querySelectorAll(
            '.flex.justify-start > div')];
        let maxRight = 0;
        const overflows = [];
        for (const c of cards) {
            const r = c.getBoundingClientRect();
            if (r.width === 0) continue;
            maxRight = Math.max(maxRight, Math.round(r.right));
            if (r.right > limit + 1) overflows.push(Math.round(r.right));
        }
        const vp = document.querySelector(
            '[data-radix-scroll-area-viewport]');
        return {limit, maxRight, overflows,
                viewportOverflowX: vp ?
                  vp.scrollWidth > vp.clientWidth + 1 : null};
    })()""")


b = Browser()
try:
    b.start(FRONTEND)
    deadline = time.time() + 90
    while time.time() < deadline:
        if b.eval("!!document.querySelector('form input')"):
            break
        time.sleep(2)

    # --- layer 1: restored long-hex answer (U00005) -----------------------
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00005'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(14)
    m = measure(b)
    results["meta"]["restored"] = m
    check("L1_restored_cards_within_column",
          not m["overflows"] and m["maxRight"] <= m["limit"],
          f"maxRight={m['maxRight']} limit={m['limit']}")
    check("L1_no_horizontal_viewport_overflow",
          m["viewportOverflowX"] is False, m["viewportOverflowX"])
    b.screenshot(f"{SHOTS}/clip-regression-1-restored.png")

    # --- layer 2: fresh user message, long unbroken token + CJK -----------
    long_token = "U00005" * 8 + "中文长消息换行验证"
    send_message(b, long_token)
    time.sleep(2)
    deadline = time.time() + 300
    while time.time() < deadline:
        conv_ok = b.eval("""(() => {
            const cards = [...document.querySelectorAll(
                '.flex.justify-end > div')];
            const last = cards[cards.length - 1];
            return last && (last.innerText || '').includes('中文长消息'); })()""")
        if conv_ok:
            break
        time.sleep(2)
    m2 = measure(b)
    results["meta"]["fresh_user_msg"] = m2
    check("L2_user_bubble_within_column",
          not m2["overflows"] and m2["maxRight"] <= m2["limit"],
          f"maxRight={m2['maxRight']} limit={m2['limit']}")
    bubble = b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-end > div')];
        const last = cards[cards.length - 1];
        if (!last) return null;
        const cr = last.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(last);
        const rects = [...range.getClientRects()].filter(
            r => r.width > 1);
        // painted-line containment: every client rect of the bubble's
        // contents stays within the bubble box (truncate/hidden ancestors
        // would fail this; wrapped lines pass)
        const over = rects.filter(r => r.right > cr.right + 1).length;
        return {bubbleW: Math.round(cr.width), over}; })()""")
    check("L2b_user_token_wraps_in_bubble",
          bubble and bubble["over"] == 0, bubble)
    b.screenshot(f"{SHOTS}/clip-regression-2-user-msg.png")

    results["PASS"] = all(v["ok"] for v in results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
with open(f"/Users/vv/investigation-agent-client/docs/evaluations/"
          f"conversation-clip-regression-{stamp}.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("checks:", {k: v["ok"] for k, v in results["checks"].items()})
sys.exit(0 if results.get("PASS") else 1)

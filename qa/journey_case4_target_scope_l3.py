"""Case-4 correction — target-account scope browser journey (L3).

Real Chrome on :3001 → :8001 (flags ON, current working tree) → RP :8000.

Journey (the original failing scenario): case U00010, focus finding F7,
ask the original question about the 34.38% reverse-trade rate and
cross-account hedging. Both user surfaces are exercised:

  1. the conversation turn (send_message → POST /turn);
  2. the bounded Agent Loop (the historical failing surface of
     TASK-5e49e173e46c / ART-4eccea4943).

Acceptance (both surfaces):
  A. 34.38% is explained as the account's OWN buy/sell balance
     (opposite_trade_ratio), explicitly NOT a cross-account match rate;
  B. the cross-account result is target-scoped: no candidate involving
     U00010 exists in the real data, and the answer says exactly that —
     the unrelated U003xx global pairs of the pre-fix answer must NOT
     appear as hedging evidence;
  C. the actually-retrieved scope is stated (64 target transactions,
     counterparty window across 18 connected accounts).
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
QUESTION = ("关于反向交易，34.38%反向交易率指的是用户自身反向交易还是与其他账户的"
            "反向交易率？这个账户是否与其他账户存在对冲交易的情况，如果有，给我列出"
            "相关证据明细")
results = {}


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


def settled(b, before, timeout, desc):
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", timeout, desc=desc)


def type_and_run_loop(b, goal, timeout=90):
    b.eval(f"""(() => {{
        const i = document.querySelector('input');
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i, {json.dumps(goal)});
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")
    time.sleep(0.4)
    deadline = time.time() + timeout
    r = "NOT_FOUND"
    while time.time() < deadline:
        r = b.eval("""(() => {
            const btns = [...document.querySelectorAll('button')];
            const t = btns.find(x => (x.title||'').includes(
                'bounded Agent Loop'));
            if (!t) return 'NOT_FOUND';
            if (t.disabled) return 'DISABLED';
            t.click(); return 'CLICKED';
        })()""")
        if r == "CLICKED":
            return r
        time.sleep(2)
    return r


def wait_loop_done(b, before_cards, timeout=420):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        new_card = True
        try:
            new_card = cards(b) > before_cards
        except Exception:
            pass
        if (new_card and not running
                and any(m in body for m in (
                    "未发现涉及账户 U00010", "关于反向交易率",
                    "Investigation is blocked", "Investigation step failed"))):
            time.sleep(1.5)
            return
        time.sleep(3)


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
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    # --- intake: populate findings panel for U00010 ---
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")
    results["focus_click"] = click_finding_row(b, "F7")
    time.sleep(1.0)

    # --- surface 1: the conversation turn (real user entry point) ---
    before = cards(b)
    send_message(b, QUESTION)
    settled(b, before, 300, "turn answer")
    time.sleep(1.0)
    turn_response = last_card_text(b)
    results["turn"] = {
        "has_metric_meaning": "34.38%" in turn_response
        and "NOT a cross-account match rate" in turn_response,
        "has_own_balance_formula":
            "min(BUY count, SELL count)" in turn_response,
        "has_target_scoped_no_match":
            "No candidate involving account U00010" in turn_response,
        "refuses_global_substitution":
            "not reported as U00010's hedging evidence" in turn_response,
        "has_scope": "Analyzed scope:" in turn_response
        and "64 transactions retrieved" in turn_response,
        "no_global_candidates_as_answer":
            ("CATA-001" not in turn_response)
            and ("U00307" not in turn_response)
            and ("U00349" not in turn_response),
        "response_text": turn_response[:1200],
    }
    b.screenshot(f"{SHOTS}/case4-fix-turn.png")

    # --- surface 2: the bounded Agent Loop (historical failing surface) ---
    before = cards(b)
    results["loop_click"] = type_and_run_loop(b, QUESTION)
    wait_loop_done(b, before)
    time.sleep(1.0)
    loop_response = last_card_text(b)
    results["loop"] = {
        "has_metric_meaning": "关于反向交易率:34.38%" in loop_response
        and "不是跨账户匹配率" in loop_response,
        "has_target_scoped_no_match":
            "未发现涉及账户 U00010" in loop_response,
        "refuses_global_substitution":
            "不会作为 U00010 的对冲证据" in loop_response,
        "has_scope": "分析范围:" in loop_response
        and "64 笔" in loop_response,
        "no_global_candidates_as_answer":
            ("CATA-001" not in loop_response)
            and ("U00307" not in loop_response),
        "response_text": loop_response[:1200],
    }
    b.screenshot(f"{SHOTS}/case4-fix-loop.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/case4-fix-FAIL.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

ok = (not results.get("error")
      and all(results.get("turn", {}).get(k) for k in (
          "has_metric_meaning", "has_own_balance_formula",
          "has_target_scoped_no_match", "refuses_global_substitution",
          "has_scope", "no_global_candidates_as_answer"))
      and all(results.get("loop", {}).get(k) for k in (
          "has_metric_meaning", "has_target_scoped_no_match",
          "refuses_global_substitution", "has_scope",
          "no_global_candidates_as_answer")))
results["acceptance"] = "PASS" if ok else "FAIL"

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = f"../docs/evaluations/case4-fix-browser-results-{stamp}.json"
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:900], flush=True)
print("ACCEPTANCE:", results["acceptance"], flush=True)
print("saved", out, flush=True)

"""Three-constraints closure — real-browser acceptance journeys (L3).

Real Chrome on :3001 → :8001 (flags ON, current working tree) → RP :8000.

Journey A (multi-dimension compound request): U00010, focus F3, ask the
withdrawal two-dimension question. The answer must address the
destination-identity dimension and the per-record is_new_address
dimension SEPARATELY, with per-record traceability, over the correct
7-record population.

Journey B (case & evidence scope): U00010, focus F7, the Case-4
question. The 34.38% meaning and the target-anchored hedging result
must be consistent with scope and status; no U003xx substitution; and
the Agent Loop surface must NOT show the generic "未能建立完整的目标
分解" guard that contradicts the actually-answered request.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
JOURNEY_A_Q = ("7 条提现都提向同一个新地址，还是提向不同的地址？"
               "哪些笔提现属于新地址？")
CASE4_Q = ("关于反向交易，34.38%反向交易率指的是用户自身反向交易还是与其他账户的"
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
                    "整体结果:", "关于「",
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
    # --- intake U00010 ---
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")

    # ============ Journey A: F3 withdrawals, two dimensions ============
    results["A_focus"] = click_finding_row(b, "F3")
    time.sleep(1.0)
    before = cards(b)
    send_message(b, JOURNEY_A_Q)
    settled(b, before, 300, "journeyA answer")
    time.sleep(1.0)
    a_text = last_card_text(b)
    results["journeyA"] = {
        "dimension1_destination":
            ("7 DIFFERENT destinations" in a_text)
            or ("SAME destination" in a_text),
        "dimension1_not_merged": "Destination identity:" in a_text,
        "dimension2_new_address":
            "New-address status:" in a_text
            and ("ALL 7" in a_text or "marked as new addresses" in a_text),
        "population_correct": "7 withdrawal records" in a_text
        and "W000087" in a_text and "W000084" in a_text,
        "traceable_per_record": "dest-" in a_text or "W000" in a_text,
    }
    b.screenshot(f"{SHOTS}/three-constraints-journeyA-turn.png")

    # ============ Journey B: Case-4 question, turn surface ============
    results["B_focus"] = click_finding_row(b, "F7")
    time.sleep(1.0)
    before = cards(b)
    send_message(b, CASE4_Q)
    settled(b, before, 300, "journeyB answer")
    time.sleep(1.0)
    b_text = last_card_text(b)
    results["journeyB_turn"] = {
        "metric_meaning": "34.38%" in b_text
        and "NOT a cross-account match rate" in b_text,
        "target_scoped_no_match":
            "No candidate involving account U00010" in b_text,
        "no_u003xx_substitution":
            "U00307" not in b_text and "CATA-001" not in b_text,
        "scope_stated": "Analyzed scope:" in b_text,
    }
    b.screenshot(f"{SHOTS}/three-constraints-journeyB-turn.png")

    # ============ Journey B: loop surface — guard must not contradict ============
    before = cards(b)
    results["B_loop_click"] = type_and_run_loop(b, CASE4_Q)
    wait_loop_done(b, before)
    time.sleep(1.0)
    l_text = last_card_text(b)
    results["journeyB_loop"] = {
        "per_output_sections": "关于「" in l_text,
        "metric_section": "不是跨账户匹配率" in l_text,
        "hedging_section": "未发现涉及 U00010" in l_text,
        "outcome_line": "整体结果:" in l_text,
        # the closure must NOT contradict the answered request
        "generic_guard_absent": "未能建立完整的目标分解" not in l_text,
    }
    b.screenshot(f"{SHOTS}/three-constraints-journeyB-loop.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/three-constraints-FAIL.png")
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
      and _all(results.get("journeyA", {}), (
          "dimension1_destination", "dimension1_not_merged",
          "dimension2_new_address", "population_correct",
          "traceable_per_record"))
      and _all(results.get("journeyB_turn", {}), (
          "metric_meaning", "target_scoped_no_match",
          "no_u003xx_substitution", "scope_stated"))
      and _all(results.get("journeyB_loop", {}), (
          "per_output_sections", "metric_section", "hedging_section",
          "outcome_line", "generic_guard_absent")))
results["acceptance"] = "PASS" if ok else "FAIL"

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
out = f"../docs/evaluations/three-constraints-browser-{stamp}.json"
json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(results, ensure_ascii=False)[:800], flush=True)
print("ACCEPTANCE:", results["acceptance"], flush=True)
print("saved", out, flush=True)

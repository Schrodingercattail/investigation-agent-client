"""Cross-domain consistency — real-browser journeys (live stack).

Backend :8001 = the manual-acceptance runtime (flags ON, NO seam, real
model); RP :8000 live. Two journeys on fresh investigations, focused F3
(7 withdrawals / 7 distinct recorded destinations — the authoritative
population):

  K1 consistent premise  — "7笔提现…": conclusion over all 7, NO
     reconciliation note (population assertion matches the evidence);
  K2 population shortfall — "9笔提现…" over the 7-record case: the
     population-shortfall invariant BLOCKS the comparison (output
     unresolved, never satisfied by the subset); the response states
     「您的请求提及 9 笔提现…仅可取得 7 笔…无法对全部 9 笔完成比较」
     and shows the 7 records for verification — no completion claim.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
RESULTS = {}
WHOLE_CLAIM = "本次调查目标已完整回答"


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
                    "个不同目的地", "笔提现指向同一目的地", "已部分回答",
                    "尚未产生可判定的结果", "仅供核查", "无法对全部",
                    "Investigation is blocked",
                    "Investigation step failed"))):
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


def journey(name, goal, must, never, shot):
    b = Browser()
    rec = {"input": goal}
    try:
        b.start(FRONTEND)
        b.eval("""(() => { const t = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('New Investigation'));
            t && t.click(); })()""")
        time.sleep(1.2)
        before = cards(b)
        send_message(b, "给我输出一下00010案例情况")
        settled(b, before, 240, "intake")
        rec["focus_click"] = click_finding_row(b, "F3")
        time.sleep(1.0)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        wait_loop_done(b, before)
        time.sleep(1.0)
        response = last_card_text(b)
        rec["notes"] = {
            "has_expected": all(m in response for m in must),
            "no_forbidden": all(m not in response for m in never),
        }
        rec["response_text"] = response[:1200]
        b.screenshot(f"{SHOTS}/{shot}")
    except Exception as e:
        try:
            b.screenshot(f"{SHOTS}/{shot.replace('.png', '-FAIL.png')}")
        except Exception:
            pass
        rec["error"] = str(e)[:300]
    finally:
        try:
            b.close()
        except Exception:
            pass
    RESULTS[name] = rec
    print(f"[{name}]", json.dumps(
        {k: rec.get(k) for k in ("notes", "error")},
        ensure_ascii=False)[:300], flush=True)


def main():
    journey(
        "K1_consistent_premise",
        "7笔提现都提向同一个地址，还是每笔提向一个新地址？",
        must=["个不同目的地"],
        never=["您的请求提及"],
        shot="consistency-K1-consistent-premise.png")
    time.sleep(20)
    # K2 — the 9-ask over the 7-record case: the population-shortfall
    # invariant BLOCKS the comparison (never satisfied by the subset);
    # the response states the shortfall explicitly and shows the 7
    # records for verification — no normal completion message.
    journey(
        "K2_population_shortfall_blocked",
        "9笔提现都提向同一个地址，还是每笔提向一个新地址？",
        must=["您的请求提及 9 笔提现", "仅可取得 7 笔",
              "无法对全部 9 笔完成比较"],
        never=[WHOLE_CLAIM, "个不同目的地",
               "笔提现指向同一目的地"],
        shot="consistency-K2-population-shortfall.png")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = f"../docs/evaluations/consistency-browser-results-{stamp}.json"
    json.dump(RESULTS, open(out, "w"), ensure_ascii=False, indent=1)
    print("saved", out, flush=True)


if __name__ == "__main__":
    main()

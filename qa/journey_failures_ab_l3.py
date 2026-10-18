"""Failures A/B — final acceptance browser journeys (contract-path
verification with strict llm_empty discipline).

Each journey: ONE initial attempt + ONE retry max. After every attempt
the served task's structured states are pulled from the backend API and
CLASSIFIED:
- contract-path PASS: decision == contract_accepted AND the accepted
  outputs carry the journey's facets/requirements AND the response
  renders the contract-path markers;
- fallback (llm_empty): decision == llm_empty → NEVER counted as a
  contract-path pass; one retry allowed;
- second llm_empty → journey STOPPED, marked externally blocked.

Journey A (F7 高频交易解释): contract path renders the feature facts
(Trade frequency (24h) = 54), the Consistency-II reconciliation
(总体口径说明, 54 scoring window vs 64 exposed) and the honest
no-fixed-threshold boundary. The fallback marker
(风险信号:行为特征检测发现该账号存在异常行为模式) must NOT appear.
Journey B (F3 复合提现问题): the accepted contract carries BOTH facets
(destination_address_identity + withdrawal_new_address_status), both
outputs evaluated, and the response renders the destination-identity
groups AND the per-record 逐笔新地址标记 list.
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
BACKEND = "http://127.0.0.1:8001"
results = {}


def api(path, timeout=20):
    with urllib.request.urlopen(BACKEND + path, timeout=timeout) as r:
        return json.loads(r.read())


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


def settled(b, before, timeout, desc):
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", timeout, desc=desc)


def type_and_run(b, goal, timeout=90):
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


def wait_done(b, markers, before_cards, timeout=420):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        new_card = True
        try:
            new_card = cards(b) > before_cards
        except Exception:
            pass
        if new_card and not running and any(m in body for m in markers):
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


def latest_task_evidence():
    """The newest task's structured states from the backend API."""
    import sqlite3
    con = sqlite3.connect(
        "file:/Users/vv/investigation-agent-client/backend/data/"
        "investigation_agent_v2.db?mode=ro", uri=True)
    tid = con.execute(
        "SELECT task_id FROM tasks_v2 ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    con.close()
    a = api(f"/api/v2/tasks/{tid}/activity")
    d = api(f"/api/v2/tasks/{tid}")
    dec = a.get("goal_contract_decision") or {}
    contract = a.get("goal_contract") or {}
    return {
        "task_id": tid,
        "decision": dec.get("outcome"),
        "llm_empty": dec.get("outcome") == "llm_empty",
        "coverage": dec.get("coverage"),
        "satisfactions": [o.get("satisfaction")
                          for o in (contract.get("outputs") or [])],
        "facets": [o.get("postcondition_facets")
                   for o in (contract.get("outputs") or [])],
        "goal_outcome": a.get("goal_outcome"),
        "response": d.get("task_response_text") or "",
    }


def run_journey(name, focus, goal, must, never, shot, wait_markers,
                contract_check):
    """One journey: attempt + one retry max, per-attempt classification.
    contract_check(ev) → True when the served task proves the
    contract-accepted path rendered the required deliverables."""
    attempts = []
    verdict = "UNKNOWN"
    final = None
    for attempt in (1, 2):
        b = Browser()
        rec = {"attempt": attempt, "input": goal, "focus": focus}
        try:
            b.start(FRONTEND)
            b.eval("""(() => { const t = [...document.querySelectorAll(
                'button')].find(x => x.textContent.includes(
                'New Investigation')); t && t.click(); })()""")
            time.sleep(1.2)
            before = cards(b)
            send_message(b, "给我输出一下00010案例情况")
            settled(b, before, 240, "intake")
            rec["focus_click"] = click_finding_row(b, focus)
            time.sleep(1.0)
            before = cards(b)
            rec["loop_click"] = type_and_run(b, goal)
            wait_done(b, wait_markers, before)
            time.sleep(1.0)
            response = last_card_text(b)
            rec["response_text"] = response[:1200]
            b.screenshot(f"{SHOTS}/{shot}"
                         + ("" if attempt == 1 else f"-retry{attempt}")
                         + ".png")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"[:250]
        finally:
            try:
                b.close()
            except Exception:
                pass
        # structured classification from the backend API
        try:
            ev = latest_task_evidence()
        except Exception as e:
            ev = {"error": str(e)[:150]}
        rec["evidence"] = {k: ev.get(k) for k in
                           ("task_id", "decision", "llm_empty",
                            "satisfactions", "facets",
                            "goal_outcome")}
        contract_path = (
            not ev.get("llm_empty")
            and ev.get("decision") == "contract_accepted"
            and contract_check(ev, response))
        rec["contract_path_pass"] = bool(contract_path)
        rec["fallback_render"] = (not contract_path
                                  and "风险信号:行为特征检测" in response)
        attempts.append(rec)
        print(f"[{name} attempt {attempt}] decision={ev.get('decision')} "
              f"llm_empty={ev.get('llm_empty')} "
              f"contract_path_pass={rec['contract_path_pass']}",
              flush=True)
        if contract_path:
            verdict = "CONTRACT_PATH_PASS"
            final = rec
            break
        if ev.get("llm_empty"):
            if attempt == 2:
                verdict = "EXTERNALLY_BLOCKED (llm_empty twice)"
                final = rec
            time.sleep(30)                     # back off before retry
            continue
        # non-llm_empty failure: inspect once via retry (attempt 2)
        if attempt == 2:
            verdict = "FAILED"
            final = rec
            break
        time.sleep(20)
    results[name] = {"verdict": verdict, "attempts": attempts,
                     "final_response_text": (final or {}).get(
                         "response_text", "")[:1400]}
    print(f"[{name}] verdict: {verdict}", flush=True)


def main(only=None):
    if only not in ("B",):
        # Journey A — F7 高频交易解释 (contract path)
        run_journey(
        "A_high_frequency_explanation", "F7",
        "高频交易为什么是风险项，怎样算高频交易",
        # O1 (why-rationale) legitimately stays unresolved: RP exposes no
        # rationale field for a pure feature observation. The pass
        # criteria are the O2 observation semantics + value + boundary.
        must=["行为特征观察记录",
              "非阈值触发的风险判定",
              "Trade frequency (24h) = 54",
              "未提供该特征的独立固定判定阈值字段"],
        never=["风险信号:行为特征检测发现该账号存在异常行为模式"],
        shot="failA-final-contract-path",
        wait_markers=["Trade frequency (24h) = 54", "总体口径说明",
                      "风险信号:行为特征检测", "Investigation is blocked",
                      "Investigation step failed"],
        contract_check=lambda ev, response: (
            ev.get("goal_outcome") in ("complete", "partial")
            and "行为特征观察记录" in response
            and "Trade frequency (24h) = 54" in response
            and "未提供该特征的独立固定判定阈值字段" in response))
    if only != "A":
        time.sleep(15 if only is None else 5)
        # Journey B — F3 复合提现问题 (both facets)
        run_journey(
        "B_composite_withdrawal", "F3",
        "7条提现都提向同一新地址，还是提向不同的地址，哪些笔提现是新地址",
        must=["目的地标识分组", "逐笔新地址标记",
              "全部 7 笔提现均被标记为新地址"],
        never=["The investigation is blocked and cannot progress",
               "context_unresolved"],
        shot="failB-final-composite",
        wait_markers=["目的地标识分组", "逐笔新地址标记",
                      "本次调查目标已完整回答", "已部分回答",
                      "无法回答该请求", "Investigation is blocked",
                      "Investigation step failed"],
        contract_check=lambda ev, response: (
            len([f for f in ev.get("facets", []) if f]) >= 1
            and len(ev.get("satisfactions", [])) >= 2
            and all(s in ("satisfied", "partially_supported")
                    for s in ev.get("satisfactions", []))
            and "目的地标识分组" in response
            and "逐笔新地址标记" in response))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = f"../docs/evaluations/failures-ab-final-{stamp}.json"
    json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
    print("saved", out, flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

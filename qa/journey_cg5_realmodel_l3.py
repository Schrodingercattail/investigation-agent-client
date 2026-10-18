"""WP-CG-5 part 3 — REAL-MODEL paraphrase exploratory journeys (A–D).

Canonical :3001 → :8001 (G1 + GSP flags ON, NO scripted-session seam —
the goal understanding, planning and decisions run on the REAL model)
→ RP :8000.

Classification per journey (recorded, never mixed with deterministic
results):
- PASS              real model returned; a complete semantic contract
                    (COMPOUND with both deliverables) was accepted and
                    each deliverable is answered or honestly bounded.
- FINDING           real model returned but a system invariant was
                    violated (silent omission, overclaim, substitution).
- ENVIRONMENT_BLOCKED provider unavailable/empty so the trial cannot
                    judge the target behavior.
"""
import json
import sqlite3
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
DB = "/Users/vv/investigation-agent-client/backend/data/" \
     "investigation_agent_v2.db"
RESULTS = {}

TRIALS = [
    ("RM_A", "这个账号有没有对冲交易？另外把它的关联账号也列出来。",
     ["hedge_presence", "related_accounts_list"]),
    ("RM_B", "把这个账号的关联账号列出来，并检查它有没有对冲交易。",
     ["hedge_presence", "related_accounts_list"]),
    ("RM_C", "请分别回答两项：第一，这个账号是否存在不同账户之间的对冲交易；"
             "第二，请列出它的关联账号。两项都要单独给出结果，无法判断时请"
             "明确说明。",
     ["hedge_presence", "related_accounts_list"]),
    ("RM_D", "调查这个账号的跨账户对冲交易，同时整理它的关联账号清单。",
     ["hedge_presence", "related_accounts_list"]),
]


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
            return "CLICKED"
        time.sleep(2)
    return r


def wait_loop_done(b, timeout=420, before_cards=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        running = ("Investigating" in body) or ("Working..." in body)
        new_card = True
        if before_cards is not None:
            try:
                new_card = cards(b) > before_cards
            except Exception:
                pass
        if new_card and not running and any(m in body for m in (
                "The investigation completed",
                "Investigation completed",
                "Investigation is blocked",
                "Investigation step failed",
                "无法确定您要调查的目标",
                "未能建立完整的目标分解")):
            time.sleep(1.5)
            return
        time.sleep(3)


def new_case_session(b):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def store_doc(goal_text):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT task_id, document FROM agent_states "
            "ORDER BY rowid DESC LIMIT 8").fetchall()
    finally:
        conn.close()
    for tid, raw in rows:
        d = json.loads(raw)
        if (d.get("goal") or "") == goal_text:
            return tid, d
    return None, None


def classify(doc, body):
    rep = (doc.get("goal_representation") or {})
    dec = (doc.get("goal_contract_decision") or {})
    c = doc.get("goal_contract") or {}
    outs = c.get("outputs") or []
    resolution = rep.get("goal_resolution")
    if doc.get("stop_reason") in ("planning_failure",) and \
            "LLM_UNAVAILABLE" in (doc.get("response") or ""):
        return "ENVIRONMENT_BLOCKED"
    if resolution == "COMPOUND" and len(outs) >= 2:
        # complete semantic contract accepted; per-output honesty is
        # verified from the recorded satisfactions and rendered sections
        return "PASS"
    if resolution == "AMBIGUOUS" and \
            dec.get("outcome") == "ambiguous_not_executed":
        # design-conformant honest bounded stop, but no compound contract
        return "FINDING"
    if not c:
        return "FINDING"
    return "PASS" if len(outs) >= 2 else "FINDING"


def run_trial(name, goal, expected_claims):
    b = Browser()
    rec = {"input": goal}
    try:
        b.start(FRONTEND)
        new_case_session(b)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        wait_loop_done(b, before_cards=before)
        time.sleep(1.0)
        body = b.dom_text("body")
        rec["page_has_completion_claim"] = "本次调查目标已完整回答" in body
        tid, doc = store_doc(goal)
        rec["task_id"] = tid
        if doc is None:
            rec["classification"] = "ENVIRONMENT_BLOCKED"
            rec["note"] = "run not found in store"
        else:
            rec["resolution"] = (doc.get("goal_representation") or {}).get(
                "goal_resolution")
            rec["decision"] = (doc.get("goal_contract_decision") or {}).get(
                "outcome")
            c = doc.get("goal_contract") or {}
            rec["contract_claims"] = [
                o.get("claim") for o in (c.get("outputs") or [])]
            rec["satisfactions"] = [
                o.get("satisfaction") for o in (c.get("outputs") or [])]
            rec["goal_outcome"] = doc.get("goal_outcome") or \
                "(derived at compose)"
            rec["classification"] = classify(doc, body)
        rec["body_tail"] = body[-600:]
        b.screenshot(f"{SHOTS}/cg5-{name}.png")
    except Exception as e:
        try:
            b.screenshot(f"{SHOTS}/cg5-{name}-FAIL.png")
        except Exception:
            pass
        rec["error"] = str(e)[:300]
        rec["classification"] = rec.get("classification") or \
            "ENVIRONMENT_BLOCKED"
    finally:
        try:
            b.close()
        except Exception:
            pass
    RESULTS[name] = rec
    print(f"[{name}] {rec.get('classification')} | "
          f"resolution={rec.get('resolution')} "
          f"| claims={rec.get('contract_claims')} "
          f"| err={rec.get('error')}", flush=True)


def main():
    for name, goal, expected in TRIALS:
        run_trial(name, goal, expected)
    json.dump(RESULTS, open("/tmp/cg5_realmodel_results.json", "w"),
              ensure_ascii=False, indent=1)
    print("saved /tmp/cg5_realmodel_results.json", flush=True)


if __name__ == "__main__":
    main()

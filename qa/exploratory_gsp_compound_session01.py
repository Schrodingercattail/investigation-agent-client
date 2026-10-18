"""Exploratory Session 01 — compound intent vs GoalContract extraction.

Charter: when one request carries multiple goals, does the system extract
and preserve them all; and when a complete GoalContract cannot be built,
do missing goals vanish silently from the user-visible result?

Real browser (qa/browser.py), real model (no seam, no injection), fresh
investigation per trial, same case U00010, case-level focus throughout.
Per trial the FULL evidence set is captured: served goal representation,
GoalContract (from the canonical store document), planning coverage,
step_output_map, decisions, observations, terminal state, the composed
response (read model) and the full page text. Results are appended to
the output JSON after every trial (crash-safe).
"""
import json
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
BACKEND = "http://localhost:8001"
DB = "/Users/vv/investigation-agent-client/backend/data/" \
     "investigation_agent_v2.db"
OUT = "/tmp/gsp_exploratory_s01_results.json"

TRIALS = [
    ("A_original_compound",
     "这个账号有没有对冲交易？另外把它的关联账号也列出来。",
     "expl01-A"),
    ("B_swapped_order",
     "把这个账号的关联账号列出来，并检查它有没有对冲交易。",
     "expl01-B"),
    ("C_enumerated",
     "请分别回答两项：第一，这个账号是否存在不同账户之间的对冲交易；"
     "第二，请列出它的关联账号。两项都要单独给出结果，无法判断时请明确说明。",
     "expl01-C"),
    ("D_compound_verb",
     "调查这个账号的跨账户对冲交易，同时整理它的关联账号清单。",
     "expl01-D"),
    ("E1_control_hedge",
     "检查这个账号有没有不同账户之间的对冲交易。",
     "expl01-E1"),
    ("E2_control_related",
     "列出这个账号的关联账号。",
     "expl01-E2"),
]

RESULTS = {}


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
        if (new_card and not running and any(m in body for m in (
                "The investigation completed",
                "Investigation completed",
                "Investigation is blocked",
                "Investigation step failed",
                "specify which one you mean",
                "couldn't reliably determine",
                "couldn't safely determine",
                "clarification"))):
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


def api(path):
    with urllib.request.urlopen(BACKEND + path, timeout=15) as r:
        return json.loads(r.read())


def state_for(goal_text):
    """Newest agent_state whose goal matches the trial input (the store
    document is a JSON blob; there is no investigation_id column)."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT task_id, document FROM agent_states "
            "ORDER BY rowid DESC LIMIT 25").fetchall()
    finally:
        conn.close()
    key = goal_text[:18]
    for tid, doc in rows:
        d = json.loads(doc)
        if key in (d.get("goal") or ""):
            c = d.get("goal_contract") or {}
            return {
                "task_id": tid,
                "agent_status": d.get("agent_status"),
                "stop_reason": d.get("stop_reason"),
                "plan_revisions": d.get("plan_revisions"),
                "goal_outcome_field": d.get("goal_outcome"),
                "criteria_kind": d.get("criteria_kind"),
                "goal_representation": d.get("goal_representation"),
                "goal_contract": {
                    "contract_id": c.get("contract_id"),
                    "goal_type": c.get("goal_type"),
                    "subject_ref": c.get("subject_ref"),
                    "outputs": [
                        {"output_id": o.get("output_id"),
                         "claim": o.get("claim"),
                         "claim_kind": o.get("claim_kind"),
                         "subject_ref": o.get("subject_ref"),
                         "scope": o.get("scope"),
                         "evidence_requirements":
                             o.get("evidence_requirements"),
                         "satisfaction": o.get("satisfaction"),
                         "detail": o.get("detail")}
                        for o in (c.get("outputs") or [])],
                } if c else None,
                "planning_coverage": d.get("planning_coverage"),
                "step_output_map": d.get("step_output_map"),
                "plan_steps": [
                    {"type": s.get("type"), "status": s.get("status")}
                    for s in (d.get("plan_steps") or [])],
                "decisions": [
                    {"iter": x.get("iteration"),
                     "decision": x.get("decision"),
                     "reason": (x.get("reason") or "")[:160]}
                    for x in (d.get("decisions") or [])],
                "observations": [
                    {"step": o.get("step_type"), "outcome": o.get("outcome"),
                     "for_outputs": o.get("for_outputs")}
                    for o in (d.get("observations") or [])],
            }
    return {"error": "no matching agent_state"}


def run_trial(name, goal, shot):
    b = Browser()
    rec = {"trial": name, "input": goal}
    try:
        b.start(FRONTEND)
        new_case_session(b)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        wait_loop_done(b, before_cards=before)
        time.sleep(1.0)
        body = b.dom_text("body")
        rec["page_text"] = body[:12000]
        b.screenshot(f"{SHOTS}/{shot}.png")
        for r in reversed(b.requests):
            if "/goal" in r.get("url", "") and r.get("method") == "POST":
                import re
                m = re.search(r"/investigations/([^/]+)/goal", r["url"])
                rec["inv_id"] = m.group(1) if m else None
                break
        rec["state"] = state_for(goal)
        tid = (rec["state"] or {}).get("task_id")
        if tid:
            try:
                with urllib.request.urlopen(
                        f"{BACKEND}/api/v2/tasks/{tid}", timeout=15) as r:
                    rec["response_text"] = json.loads(
                        r.read()).get("task_response_text") or ""
            except Exception as e:
                rec["response_error"] = str(e)[:150]
    except Exception as e:
        try:
            b.screenshot(f"{SHOTS}/{shot}-FAIL.png")
        except Exception:
            pass
        rec["error"] = str(e)[:300]
    finally:
        try:
            b.close()
        except Exception:
            pass
    RESULTS[name] = rec
    s = rec.get("state") or {}
    print(f"[{name}] status={s.get('agent_status')} "
          f"contract={(s.get('goal_contract') or {}).get('contract_id')} "
          f"outputs={len((s.get('goal_contract') or {}).get('outputs') or [])} "
          f"rev={s.get('plan_revisions')} err={rec.get('error')}",
          flush=True)
    json.dump(RESULTS, open(OUT, "w"), ensure_ascii=False, indent=1)


def main(only=None):
    for name, goal, shot in TRIALS:
        if only and name not in only:
            continue
        run_trial(name, goal, shot)
    print("done", flush=True)


if __name__ == "__main__":
    main(set(sys.argv[1:]) or None)

"""WP-CG-5 — deterministic Browser E2E journeys (J1–J7).

Canonical :3001 → :8001 (G1 + GSP flags ON; scripted-session seam
provides the goal-understanding MODEL TEXT and planner/decider scripts —
the loop, contract acceptance, GSP-6 admission, execution, GSP-4/5,
composer and Activity projection all run for real) → RP :8000 (live
U00010 data).

Per journey: session swap → real browser (New Investigation → intake →
user-selected focus → bounded Agent Loop run) → user-visible text
assertions + structured backend evidence (store document: contract,
coverage, step_output_map, per-output satisfaction, decision record).

These are the DETERMINISTIC half of WP-CG-5; real-model paraphrase
exploration runs separately WITHOUT the seam (WP-CG-5 part 3).
"""
import json
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
SESSION = "/tmp/cg5_session.json"
DB = "/Users/vv/investigation-agent-client/backend/data/" \
     "investigation_agent_v2.db"
RESULTS = {}

JOURNEYS = [
    ("J1_single_hedge", "/tmp/cg5_j1.json",
     "检查这个账号有没有不同账户之间的对冲交易。",
     ["对冲"], ["未能建立完整的目标分解", "无法确定您要调查的目标"],
     "F1", "completed"),
    ("J2_single_related", "/tmp/cg5_j2.json",
     "列出这个账号的关联账号。",
     ["关联账号"], ["未能建立完整的目标分解"],
     "F1", "completed"),
    ("J3_compound_two_outputs", "/tmp/cg5_j3.json",
     "这个账号有没有对冲交易？另外把它的关联账号也列出来。",
     ["对冲", "关联账号"], ["未能建立完整的目标分解"],
     "F1", "completed"),
    ("J4_partial_honest_boundary", "/tmp/cg5_j4.json",
     "这个账号和它的关联账号之间有没有共享IP？另外把它的关联账号也列出来。",
     ["关联账号", "不具备"], ["本次调查目标已完整回答"],
     "F1", "completed"),
    ("J5_ambiguous_bounded_stop", "/tmp/cg5_j5.json",
     "这个账号有没有对冲交易？另外把它的关联账号也列出来。",
     ["无法确定您要调查的目标"], ["已完整回答"],
     None, "blocked"),
    ("J6a_invalid_proposal_guard", "/tmp/cg5_j6a.json",
     "帮我梳理一下这个账号目前的情况。",
     ["未能建立完整的目标分解"], ["本次调查目标已完整回答"],
     "F1", "completed"),
    ("J6b_llm_empty_guard", "/tmp/cg5_j6b.json",
     "帮我梳理一下这个账号目前的情况。",
     ["未能建立完整的目标分解"], ["本次调查目标已完整回答"],
     "F1", "completed"),
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


def wait_loop_done(b, timeout=180, before_cards=None):
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
                "Investigation step failed")):
            time.sleep(1.5)
            return
        time.sleep(2)


def new_case_session(b):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def store_doc(task_id):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT document FROM agent_states WHERE task_id = ?",
            (task_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def run_journey(name, session_src, goal, must, never, focus, expect_status):
    shutil.copy(session_src, SESSION)
    b = Browser()
    rec = {"input": goal}
    try:
        b.start(FRONTEND)
        new_case_session(b)
        if focus:
            rec["focus_click"] = click_finding_row(b, focus)
            time.sleep(1.0)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        wait_loop_done(b, before_cards=before)
        time.sleep(1.0)
        body = b.dom_text("body")
        rec["browser"] = {
            "has_expected": all(m in body for m in must),
            "no_forbidden": all(m not in body for m in never),
        }
        b.screenshot(f"{SHOTS}/cg5-{name}.png")
        # structured backend evidence for the newest matching run
        time.sleep(0.5)
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT task_id, document FROM agent_states "
                "ORDER BY rowid DESC LIMIT 6").fetchall()
        finally:
            conn.close()
        doc = None
        for tid, raw in rows:
            d = json.loads(raw)
            if (d.get("goal") or "") == goal:
                doc = d
                rec["task_id"] = tid
                break
        if doc:
            c = doc.get("goal_contract") or {}
            rec["state"] = {
                "status": doc.get("agent_status"),
                "stop": doc.get("stop_reason"),
                "decision": doc.get("goal_contract_decision"),
                "contract": {
                    "goal_type": c.get("goal_type"),
                    "outputs": [
                        {"id": o.get("output_id"), "claim": o.get("claim"),
                         "kind": o.get("claim_kind"),
                         "subject": o.get("subject_ref"),
                         "scope": o.get("scope"),
                         "reqs": o.get("evidence_requirements"),
                         "satisfaction": o.get("satisfaction")}
                        for o in (c.get("outputs") or [])],
                } if c else None,
                "planning_coverage": [
                    {"output_id": r.get("output_id"),
                     "status": r.get("coverage_status")}
                    for r in (doc.get("planning_coverage") or [])],
                "step_output_map": doc.get("step_output_map"),
                "plan_revisions": doc.get("plan_revisions"),
                "outcome": doc.get("goal_outcome"),
            }
    except Exception as e:
        try:
            b.screenshot(f"{SHOTS}/cg5-{name}-FAIL.png")
        except Exception:
            pass
        rec["error"] = str(e)[:300]
    finally:
        try:
            b.close()
        except Exception:
            pass
    RESULTS[name] = rec
    st = rec.get("state") or {}
    br = rec.get("browser") or {}
    print(f"[{name}] browser={br} | status={st.get('status')} "
          f"| outcome={st.get('outcome')} | err={rec.get('error')}",
          flush=True)


def main(only=None):
    for (name, session_src, goal, must, never, focus,
         expect_status) in JOURNEYS:
        if only and name != only:
            continue
        run_journey(name, session_src, goal, must, never, focus,
                    expect_status)
    json.dump(RESULTS, open("/tmp/cg5_l3_results.json", "w"),
              ensure_ascii=False, indent=1)
    print("saved /tmp/cg5_l3_results.json", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

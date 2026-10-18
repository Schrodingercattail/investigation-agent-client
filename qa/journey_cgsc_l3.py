"""WP-GS-C L3 — real-browser journey for the comparison-intent
coverage-incomplete disposition.

Canonical :3001 → :8001 (ENABLE_GOAL_SEMANTIC_CONTRACT=ON + scripted
session seam at /tmp/cgsc_session.json — ONLY the model boundary is
scripted: resolver stays real behind the explicit focus, planner
parsing/registry validation, executor, postconditions, coverage
detector, composer and Activity projection are the production path)
→ RP :8000.

J1 — the exact audited scenario: focused F3 withdrawal investigation,
the user asks the comparison question, the goal LLM proposes a valid
RETRIEVAL-ONLY contract (seam), the deterministic coverage detector
marks comparison_intent_uncovered, and the page must show the
basis-scoped outcome + uncovered-intent disclosure and must NOT claim
本次调查目标已完整回答.

J2 — false-positive control: the same seam contract, a NEUTRAL
(retrieval) request → the standard GSP-5 outcome line renders and no
coverage disclosure appears.
"""
import json
import shutil
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
SEAM = "/tmp/cgsc_session.json"
RESULTS = {}

DISCLOSURE = "未能将您的比较需求建立为结构化交付目标"
NOT_CONCLUSION = "不构成“同一/不同”的结论"
WHOLE_CLAIM = "本次调查目标已完整回答"

RETRIEVAL_ONLY_CONTRACT = json.dumps({
    "goal_resolution": "INFERRED", "goal_type": "policy_check",
    "ambiguous_goal_types": [], "reason": "r",
    "required_outputs": [
        {"claim": "case_risk_overview", "claim_kind": "retrieval",
         "subject_ref": "U00010", "scope": "case_level",
         "evidence_requirements": ["case_summary"]}]})
PLAN_FETCH = ('{"skill_id": "case_intake", "goal": "g", '
              '"steps": [{"type": "fetch_case", "reason": "r"}]}')

COMPARISON_GOAL = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"
NEUTRAL_GOAL = "帮我梳理一下这个账号目前的情况。"


def cards(b):
    return b.eval("document.querySelectorAll('.flex.justify-start').length")


def settled(b, before, timeout, desc):
    wait_for(b, """(() => {
        const c = document.querySelectorAll('.flex.justify-start').length;
        const p = document.body.innerText.includes(
            'Resolving context, planning, and executing');
        return c > """ + str(before) + """ && !p;
    })()""", timeout, desc=desc)


def type_and_run_loop(b, goal, timeout=60):
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
                    WHOLE_CLAIM, "已部分回答", DISCLOSURE,
                    "Investigation is blocked",
                    "Investigation step failed"))):
            time.sleep(1.5)
            return body
        time.sleep(3)
    return b.dom_text("body")


def new_case_session(b):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def run_journey(name, goal, must, never, shot, focus="F3"):
    b = Browser()
    rec = {"input": goal, "focus": focus}
    try:
        b.start(FRONTEND)
        new_case_session(b)
        if focus:
            rec["focus_click"] = click_finding_row(b, focus)
            time.sleep(1.0)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        body = wait_loop_done(b, before)
        time.sleep(1.0)
        body = b.dom_text("body")
        rec["notes"] = {
            "has_expected": all(m in body for m in must),
            "no_forbidden": all(m not in body for m in never),
        }
        rec["body_tail"] = body[-700:]
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
        {k: rec.get(k) for k in ("notes", "error", "focus_click",
                                 "loop_click")},
        ensure_ascii=False)[:400], flush=True)


def write_seam(name, payload, plans, decisions):
    json.dump({"plans": plans, "decisions": decisions,
               "goal_llm": [payload]},
              open(name, "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)


def main():
    # J1 — comparison intent uncovered (the audited scenario)
    write_seam(SEAM, RETRIEVAL_ONLY_CONTRACT, [PLAN_FETCH], ["STOP"])
    run_journey(
        "J1_comparison_intent_uncovered", COMPARISON_GOAL,
        must=[DISCLOSURE, NOT_CONCLUSION],
        never=[WHOLE_CLAIM],
        shot="cgsc-J1-comparison-uncovered.png", focus="F3")
    # J2 — control: neutral retrieval request, no coverage-incomplete
    # (focused, mirroring the GS-A J3 canonical shape — unfocused neutral
    # requests are legitimately blocked at the target-resolution
    # ambiguity boundary, which is not this WP's subject)
    write_seam(SEAM, RETRIEVAL_ONLY_CONTRACT, [PLAN_FETCH], ["STOP"])
    run_journey(
        "J2_neutral_no_false_coverage", NEUTRAL_GOAL,
        must=[WHOLE_CLAIM],
        never=[DISCLOSURE],
        shot="cgsc-J2-neutral-control.png", focus="F1")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = f"../docs/evaluations/wp-cgsc-browser-results-{stamp}.json"
    json.dump(RESULTS, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"saved {out}", flush=True)


if __name__ == "__main__":
    main()

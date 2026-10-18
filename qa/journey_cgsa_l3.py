"""WP-GS-A L3 — real-browser journey for the completion-claim guard.

Canonical :3001 → :8001 (flags OFF — the canonical user-facing runtime;
the universal guard INV-C is flag-independent) → RP :8000. Real model,
no seam.

Journey: contract-less request (neutral goal → heuristic single-goal
routing, no accepted contract) → the loop completes the retrieval →
the page must show the basis-scoped closure (「系统未建立目标语义契约，
不对"您的请求已得到完整回答"作出判定」) and must NOT present an
unqualified whole-request completion claim.
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
        if new_card and not running and any(m in body for m in (
                "The investigation completed",
                "Investigation completed",
                "Investigation is blocked",
                "Investigation step failed",
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


def run_journey(name, goal, must, never, shot, focus=None):
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
        {k: rec.get(k) for k in ("notes", "error")},
        ensure_ascii=False)[:400], flush=True)


def _write_seam_session(path, payload, plans, decisions):
    json.dump({"plans": plans, "decisions": decisions,
               "goal_llm": [payload]},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _seam_contract_journey(name, seam_path, goal, must, never, shot,
                           focus=None):
    shutil.copy(seam_path, "/tmp/cgsa_session.json")
    run_journey(name, goal, must, never, shot, focus=focus)


def main(only=None):
    import os
    only = sys.argv[1] if len(sys.argv) > 1 else None
    # J3 — valid contract, output satisfied: contract-relative
    # 已完整回答 renders; guard not applicable
    if only in (None, "J3_valid_contract_satisfied"):
        _write_seam_session(
            "/tmp/cgsa_seam_j3.json",
            json.dumps({
                "goal_resolution": "INFERRED",
                "goal_type": "policy_check",
                "ambiguous_goal_types": [], "reason": "r",
                "required_outputs": [
                    {"claim": "case_risk_overview",
                     "claim_kind": "retrieval", "subject_ref": "U00010",
                     "scope": "case_level",
                     "evidence_requirements": ["case_summary"]}]}),
            ['{"skill_id": "case_intake", "goal": "g", "steps": '
             '[{"type": "fetch_case", "reason": "r"}]}'],
            ["STOP"])
        _seam_contract_journey(
            "J3_valid_contract_satisfied", "/tmp/cgsa_seam_j3.json",
            "帮我梳理一下这个账号目前的情况。",
            must=["本次调查目标已完整回答"],
            never=["未能建立完整的目标分解",
                   "不对“您的请求已得到完整回答”作出判定"],
            shot="cgsa-J3-valid-contract-satisfied.png", focus="F1")
    # J4 — valid contract, PARTIAL outcome: honest per-output rendering
    if only in (None, "J4_valid_contract_partial"):
        _write_seam_session(
            "/tmp/cgsa_seam_j4.json",
            json.dumps({
                "goal_resolution": "INFERRED",
                "goal_type": "policy_check",
                "ambiguous_goal_types": [], "reason": "r",
                "required_outputs": [
                    {"claim": "case_risk_overview",
                     "claim_kind": "retrieval", "subject_ref": "U00010",
                     "scope": "case_level",
                     "evidence_requirements": ["case_summary"]},
                    {"claim": "network_relations_report",
                     "claim_kind": "retrieval", "subject_ref": "U00010",
                     "scope": "case_level",
                     "evidence_requirements": ["network_relations"]}]}),
            ['{"skill_id": "case_intake", "goal": "g", "steps": '
             '[{"type": "fetch_case", "reason": "r"}]}'],
            ["CONTINUE", "STOP"])
        _seam_contract_journey(
            "J4_valid_contract_partial", "/tmp/cgsa_seam_j4.json",
            "帮我梳理一下这个账号目前的情况。",
            must=["已部分回答"],
            never=["本次调查目标已完整回答"],
            shot="cgsa-J4-valid-contract-partial.png", focus="F1")

    json.dump(RESULTS, open("/tmp/cgsa_l3_j34_results.json", "w"),
              ensure_ascii=False, indent=1)
    print("saved /tmp/cgsa_l3_j34_results.json", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)


"""WP-GS-D L3 — real-browser journeys for the destination-identity
facet evaluation.

Canonical :3001 → :8001 (ENABLE_GOAL_SEMANTIC_CONTRACT=ON + scripted
session seam at /tmp/cgsd_session.json — ONLY the model boundary is
scripted; resolver behind the explicit focus, planner parsing,
ExecutorV2, finding_drilldown, the GS-B fact block, observation
projection, GSP-3 association, GSP-4 facet evaluation, GSP-5
derivation, composer and Activity projection are the production path)
→ RP :8000.

J1 — full comparison facts (live RP): focused F3, seven withdrawals
with seven distinct recorded destinations → the page must show the
comparison conclusion (7 笔提现共指向 7 个不同目的地), the per-group
provenance and the 目的地 evidence table; NO coverage-incomplete
disclosure (the facet covers the detected comparison intent) and no
"records retrieved"-style non-answer.

J2 — comparison facts unavailable (live RP): focused F1 (a
transaction-stream finding has no withdrawal records) → the tool
honestly reports no withdrawal stream, the facet output stays
PENDING/UNRESOLVED-shaped, and the page must NOT claim the request was
fully answered.

Partial-identity browser journey: NOT RUN — live RP holds no
withdrawal set with missing destination identifiers (F3/F4 are 7/7
complete); the partial shape is exercised at the real HTTP API layer
with a fixture-controlled adapter payload
(tests/test_cg_gs_d_api_e2e_v1.py) through the same production code
the browser exercises.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
SEAM = "/tmp/cgsd_session.json"
RESULTS = {}

WHOLE_CLAIM = "本次调查目标已完整回答"
COVERAGE_DISCLOSURE = "未能将您的比较需求建立为结构化交付目标"

PLAN_WITHDRAWALS = ('{"skill_id": "timeline_investigation", "goal": "g", '
                    '"steps": [{"type": "inspect_withdrawals", '
                    '"reason": "r"}]}')


def faceted_seam(subject):
    return json.dumps({
        "goal_resolution": "INFERRED", "goal_type": "policy_check",
        "ambiguous_goal_types": [], "reason": "r",
        "required_outputs": [
            {"claim": "withdrawal_destination_comparison",
             "claim_kind": "retrieval", "subject_ref": subject,
             "scope": "finding_level",
             "evidence_requirements": ["withdrawal_records"],
             "postcondition_facets": ["destination_address_identity"]}]})


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
                    WHOLE_CLAIM, "已部分回答", "尚未产生可判定的结果",
                    "个不同目的地", "笔提现指向同一目的地",
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


def last_card_text(b):
    """The newest conversation card's text — the must/never checks scope
    to the RESPONSE, never to the History sidebar (older task cards
    legitimately carry older wording)."""
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll(
            '.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last ? last.innerText : '';
    })()""")


def run_journey(name, goal, must, never, shot, focus, subject):
    json.dump({"plans": [PLAN_WITHDRAWALS] * 3,
               "decisions": ["CONTINUE"] * 2 + ["STOP"],
               "goal_llm": [faceted_seam(subject)]},
              open(SEAM, "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    b = Browser()
    rec = {"input": goal, "focus": focus}
    try:
        b.start(FRONTEND)
        new_case_session(b)
        rec["focus_click"] = click_finding_row(b, focus)
        time.sleep(1.0)
        before = cards(b)
        rec["loop_click"] = type_and_run_loop(b, goal)
        body = wait_loop_done(b, before)
        time.sleep(1.0)
        response = last_card_text(b)
        body = b.dom_text("body")
        rec["notes"] = {
            "has_expected": all(m in response for m in must),
            "no_forbidden": all(m not in response for m in never),
        }
        rec["response_text"] = response[:1600]
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


def main(only=None):
    goal = "9笔提现都提向同一个地址，还是每笔提向一个新地址？"
    if only in (None, "J1"):
        # J1 — full comparison facts (live RP, F3: 7 withdrawals / 7 distinct)
        # The comparison IS fully answered → the contract-relative whole-goal
        # completion claim is legitimate here (§7.4 case 1); what must NOT
        # appear is the coverage-incomplete disclosure or the contract-absent
        # guard wording.
        run_journey(
            "J1_full_facts_positive_conclusion", goal,
            must=["个不同目的地", "目的地标识分组", WHOLE_CLAIM],
            never=[COVERAGE_DISCLOSURE, "未能建立完整的目标分解"],
            shot="cgsd-J1-full-facts-conclusion.png", focus="F3",
            subject="F3")
        time.sleep(20)
    if only in (None, "J2"):
        # J2 — comparison facts unavailable (live RP, F1: transaction stream)
        run_journey(
            "J2_facts_unavailable_no_overclaim", goal,
            must=["尚未产生可判定的结果"],
            never=[WHOLE_CLAIM, "个不同目的地", "笔提现指向同一目的地"],
            shot="cgsd-J2-facts-unavailable.png", focus="F1",
            subject="F1")
        time.sleep(10)
    if only in (None, "J3"):
        # J3 — PARTIAL identity (acceptance supplement): the backend serves
        # the adapter-boundary fixture (9 withdrawals: 6 identified in two
        # groups + 3 blank — see qa/serve_cgsd_partial_l3.py, the marked
        # data-injection boundary; everything downstream is production
        # code). The page must show the comparable subset with its groups,
        # the explicit 3/9 missing count, the subset-only conclusion, the
        # whole-comparison-not-established statement, and the partial
        # outcome — never a full-answer claim.
        run_journey(
            "J3_partial_identity_partially_supported", goal,
            must=["3/9 笔记录缺少目的地标识", "整体的比较尚未建立",
                  "已部分回答", "目的地标识分组", "dest-1"],
            never=[WHOLE_CLAIM, "个不同目的地", "笔提现指向同一目的地",
                   "不存在可比较的目的地标识"],
            shot="cgsd-J3-partial-identity.png", focus="F3",
            subject="F3")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    suffix = f"-{only}" if only else ""
    out = (f"../docs/evaluations/wp-cgsd-browser-results-{stamp}"
           f"{suffix}.json")
    json.dump(RESULTS, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"saved {out}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

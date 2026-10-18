"""AL-3.3 baseline L3 — real canonical browser journeys for browser_required
scenarios (S01,S03,S04,S05,S06,S07,S08,S09,S12,S14).

Canonical runtime :3001 → :8001 → RP :8000, real LLM. One scenario per
Chrome. Evaluation harness only — no product code touched.

Usage: python3 journey_al33_baseline_l3.py S01 S03 ...
Results: /tmp/al33_evidence/l3_results.json + qa/screenshots/al33-l3-*.png
"""
import json
import os
import re
import sys
import time

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

FRONTEND = os.environ.get("FRONTEND_BASE", "http://localhost:3001/")
RESULTS = "/tmp/al33_evidence/l3_results.json"


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


def wait_loop_done(b, timeout=300, before_cards=None):
    """Terminal = closure marker present AND Activity panel not in a
    running state AND (when a baseline was captured) a new message card
    exists. Prevents matching stale closure text from prior turns."""
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
                "clarification"))):
            time.sleep(1.5)
            return
        time.sleep(3)


def new_inv(b):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)


def establish_case(b, intake="给我输出一下00010案例情况"):
    """Browser equivalent of the frozen scenarios' initial_context
    {case_id: U00010}: the UI binds a case via the intake conversation
    (no constructor injection exists on this surface). Observed journey
    metadata — mirrors qa new_case_conversation pattern."""
    send_turn(b, intake)


def send_turn(b, text):
    before = cards(b)
    send_message(b, text)
    settled(b, before, 240, "turn")


def reopen_first(b):
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('Investigation ·'));
        t && t.click(); })()""")
    wait_for(b, "document.body.innerText.includes('U00010')",
             60, desc="persistence restore")


def activity_present(b):
    """Light Activity-panel semantics check for loop scenarios: the panel
    renders plan/step/tool traces."""
    body = b.dom_text("body")
    markers = ("Activity", "explain_signal", "retrieve_policy",
               "inspect_", "analyze_", "fetch_case", "plan", "Plan")
    return sum(1 for m in markers if m in body)


def run_scenario(key):
    """Returns (status, notes). All visible-content checks are captured as
    observations; hard assertion failures mark FAIL."""
    b = Browser(port=9229)
    b.start(FRONTEND)
    notes = {}
    try:
        new_inv(b)
        if key == "S01":
            send_turn(b, "给我输出一下00010案例情况")
            body = b.dom_text("body")
            notes["has_8findings"] = "8 findings" in body
            notes["has_case"] = "U00010" in body
            notes["findings_listed"] = "ML Pattern Detection" in body
            ok = notes["has_8findings"] and notes["has_case"]
            b.screenshot(f"{SHOTS}/al33-l3-S01.png")

        elif key == "S03":
            establish_case(b)
            send_turn(b, "为什么共享设备是风险项")
            body = b.dom_text("body")
            # Two legitimate outcome classes (match API-run observations):
            # (a) clarification: "Please specify which one you mean."
            # (b) direct answer: focus panel F5 + graph facts rendered.
            notes["clarify"] = "specify which one you mean" in body
            notes["focus_f5"] = "Shared Device Relationships" in body
            notes["device"] = "DEV8Z1O4A35QU" in body
            notes["score80"] = "80.0" in body
            notes["direct_answer"] = (notes["focus_f5"] and notes["device"]
                                      and notes["score80"])
            ok = notes["clarify"] or notes["direct_answer"]
            b.screenshot(f"{SHOTS}/al33-l3-S03.png")

        elif key == "S04":
            establish_case(b)
            before = cards(b)
            type_and_run_loop(b, "ML Pattern Detection什么意思，为什么是风险")
            wait_loop_done(b, before_cards=before)
            time.sleep(1.5)
            body = b.dom_text("body")
            notes["score"] = "99.41" in body
            notes["lightgbm"] = "LightGBM" in body
            notes["boundary"] = "不能断言" in body or "attribution" in body
            notes["activity_markers"] = activity_present(b)
            ok = notes["score"] and notes["lightgbm"]
            b.screenshot(f"{SHOTS}/al33-l3-S04.png")

        elif key == "S05":
            establish_case(b)
            send_turn(b, "高频提现为什么是风险项，把所有高频提现证据发给我")
            body = b.dom_text("body")
            notes["w_records"] = "W000087" in body
            notes["seven"] = "7 withdrawal" in body or "7 条" in body
            notes["all_records"] = body.count("W0000") >= 7
            ok = notes["w_records"]
            b.screenshot(f"{SHOTS}/al33-l3-S05.png")

        elif key == "S06":
            establish_case(b)
            before = cards(b)
            type_and_run_loop(b,
                "为什么说高频交易是风险项，给我输出高频交易的具体交易明细")
            wait_loop_done(b, before_cards=before)
            time.sleep(1.5)
            body = b.dom_text("body")
            notes["freq"] = "Trade frequency (24h) = 54" in body
            notes["t_records"] = "T00" in body
            notes["count64"] = "64" in body
            notes["activity_markers"] = activity_present(b)
            ok = notes["freq"] and notes["t_records"]
            b.screenshot(f"{SHOTS}/al33-l3-S06.png")

        elif key in ("S07", "S08", "S09", "S14"):
            intake = {"S07": "给我输出一下00010案例情况",
                      "S08": "给我输出一下00010案例情况",
                      "S09": "Investigate U00010",
                      "S14": "给我输出一下00010案例情况"}[key]
            goal = {"S07": "为什么说共享设备是风险项",
                    "S08": "能告诉我哪些账号是这个账号的关联账号吗",
                    "S09": "这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我",
                    "S14": "高频提现为什么是风险项，把所有高频提现证据发给我"}[key]
            send_turn(b, intake)
            before = cards(b)
            type_and_run_loop(b, goal)
            wait_loop_done(b, before_cards=before)
            time.sleep(1.5)
            body = b.dom_text("body")
            notes["activity_markers"] = activity_present(b)
            if key == "S07":
                notes["cluster"] = "Cluster_device_sharing_1" in body
                notes["device"] = "DEV8Z1O4A35QU" in body
                ok = notes["cluster"] and notes["device"]
            elif key == "S08":
                notes["eighteen"] = "18" in body
                notes["ids"] = "U00004" in body and "U00337" in body
                notes["boundary"] = "不能证明" in body
                ok = notes["eighteen"] and notes["ids"]
            elif key == "S09":
                notes["cata"] = "CATA-001" in body
                notes["bounded"] = ("不构成对操纵或交易意图的结论" in body
                                    or "候选" in body)
                ok = notes["cata"] and notes["bounded"]
            else:  # S14
                notes["w87"] = "W000087" in body
                notes["count7"] = "7" in body
                ok = notes["w87"]
            b.screenshot(f"{SHOTS}/al33-l3-{key}.png")
            if key == "S14":
                reopen_first(b)
                body2 = b.dom_text("body")
                notes["reload_w87"] = "W000087" in body2
                notes["reload_conversation"] = "高频提现" in body2 or \
                    "withdrawal" in body2.lower()
                ok = ok and notes["reload_w87"]
                b.screenshot(f"{SHOTS}/al33-l3-S14-reload.png")

        elif key == "S12":
            establish_case(b)
            before = cards(b)
            type_and_run_loop(b, "关联账户关系为什么是风险项，这个账户有哪些关联账户")
            wait_loop_done(b, before_cards=before)
            time.sleep(1.5)
            body = b.dom_text("body")
            notes["eighteen"] = "18" in body
            notes["device"] = "DEV8Z1O4A35QU" in body
            notes["boundary"] = "不能证明" in body
            notes["activity_markers"] = activity_present(b)
            ok = notes["eighteen"] and notes["device"]
            b.screenshot(f"{SHOTS}/al33-l3-S12.png")
        else:
            return "SKIP", {"reason": f"{key} not a browser scenario"}

        return ("PASS" if ok else "FAIL"), notes
    except AssertionError as e:
        try:
            b.screenshot(f"{SHOTS}/al33-l3-FAIL-{key}.png")
        except Exception:
            pass
        return "FAIL", {"error": str(e)[:200]}
    except Exception as e:
        try:
            b.screenshot(f"{SHOTS}/al33-l3-ERROR-{key}.png")
        except Exception:
            pass
        return "ERROR", {"error": f"{type(e).__name__}: {e}"[:200]}
    finally:
        b.close()


if __name__ == "__main__":
    targets = sys.argv[1:] or ["S01", "S03", "S04", "S05", "S06",
                               "S07", "S08", "S09", "S12", "S14"]
    results = {}
    for k in targets:
        print(f"=== L3 {k} ===", flush=True)
        status, notes = run_scenario(k)
        results[k] = {"status": status, "notes": notes}
        print(f"{k}: {status} {json.dumps(notes, ensure_ascii=False)}",
              flush=True)
        json.dump(results, open(RESULTS, "w"), ensure_ascii=False, indent=1)
    print("\n=== L3 SUMMARY ===")
    for k, v in results.items():
        print(f"  {k}: {v['status']}")

"""U00005 compound criteria/withdrawals ask — REAL browser E2E.

Real stack :3001 → :8001 (real LLM) → RP :8000. Real input box submit;
assert the THREE deliverables individually against the rendered answer,
using API truth for run-completion waits (slow-LLM-proof) and DOM for
the user-visible content.
"""
import json
import re
import sys
import time

import httpx

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
BACKEND = "http://localhost:8001"
REQ = ("What is the criteria of High Withdrawal Frequency finding? "
       "Are the new withdrawal addresses all the same or different "
       "addresses? List all the withdrawal records to me.")
results = {"checks": {}, "meta": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:140])


def last_card(b):
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        return cards.length ? cards[cards.length - 1].innerText : ''; })()""")


def _reopen(b, inv_id):
    b.navigate(FRONTEND, settle=5)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes( %r ));
        rows.find(x => x.clientHeight > 0)?.click(); })()""" % (inv_id,))
    time.sleep(14)


b = Browser()
try:
    b.start(FRONTEND)
    wait_for(b, """(() => !!document.querySelector('input'))""",
             120, desc="any input rendered")
    check("M0_app_mounted", True, "input rendered")
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)

    # intake via the real input box (case reference resolves server-side)
    send_message(b, "Investigate U00005")
    time.sleep(2)
    inv_id = ""
    deadline = time.time() + 600
    response = ""
    while time.time() < deadline:
        rows = b.eval("""(() => {
            const rows = [...document.querySelectorAll(
                'button, [role=button]')]
                .map(x => x.innerText || '');
            const row = rows.find(t => t.includes('U00005') &&
                t.includes('INV-'));
            const m = row && row.match(/INV-[0-9a-f]+/);
            return m ? m[0] : ''; })()""")
        if rows and rows != inv_id:
            inv_id = rows
            tasks = httpx.get(
                f"{BACKEND}/api/v2/investigations/{inv_id}",
                timeout=30).json().get("tasks", [])
            if tasks:
                d = httpx.get(
                    f"{BACKEND}/api/v2/tasks/{tasks[0]['task_id']}",
                    timeout=30).json()
                response = d.get("task_response_text") or ""
                if response and d["task"]["status"] in (
                        "completed", "failed"):
                    break
        time.sleep(5)
    results["meta"]["investigation_id"] = inv_id
    check("A1_intake_completed", bool(response), response[:120])

    # the REAL request via the real input box
    send_message(b, REQ)
    time.sleep(2)
    deadline = time.time() + 1200
    response = ""
    while time.time() < deadline:
        tasks = httpx.get(
            f"{BACKEND}/api/v2/investigations/{inv_id}",
            timeout=30).json().get("tasks", [])
        hedge = next((t for t in tasks
                      if "criteria" in (t.get("user_request") or "")), None)
        if hedge:
            d = httpx.get(f"{BACKEND}/api/v2/tasks/{hedge['task_id']}",
                          timeout=30).json()
            response = d.get("task_response_text") or ""
            if response and hedge["status"] in ("completed", "failed"):
                results["meta"]["task_id"] = hedge["task_id"]
                break
        time.sleep(5)
    assert response, "run never persisted a response"
    results["meta"]["response_head"] = response[:400]

    # let the frontend render the absorbed answer, then read the DOM
    deadline2 = time.time() + 60
    while time.time() < deadline2:
        card = last_card(b)
        if "No determinable result" not in card and len(card) > 400:
            break
        if re.search(r"withdrawal_frequency_24h|different destinations",
                     card):
            break
        time.sleep(4)
    time.sleep(3)
    answer = last_card(b)
    results["meta"]["answer_head"] = answer[:400]

    # O1: criteria grounded (rule + threshold from finding metadata)
    o1 = bool(re.search(r"withdrawal_frequency_24h\s*>\s*\d+|"
                        r"[Ww]ithdrawal [Ff]requency.{0,120}"
                        r"(threshold|rule|24)", answer))
    check("O1_criteria_grounded_in_answer", o1, answer[:200])
    # O2: destination same/different verdict
    o2 = bool(re.search(r"(same destination|different destinations|"
                        r"不同的目的地|同一个目的地)", answer, re.IGNORECASE))
    check("O2_destination_verdict_in_answer", o2, answer[:200])
    # O3: withdrawal records listed (ids + timestamps)
    rec_ids = re.findall(r"W\d{4,6}", answer)
    check("O3_records_listed_in_answer", len(set(rec_ids)) >= 3,
          f"{len(set(rec_ids))} ids")
    stamps = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", answer)
    check("O3b_timestamps_in_answer", len(stamps) >= 3, str(stamps[:4]))
    # honesty: no all-three-failure masked by completed
    check("H_no_three_fold_failure",
          "No determinable result" not in answer, answer[-160:])
    b.screenshot(f"{SHOTS}/u00005-browser-1.png")

    # reload + reopen: the restored answer persists
    _reopen(b, inv_id)
    conv = b.eval("""(() => {
        const main = document.querySelector('main') || document.body;
        return main.innerText; })()""")
    check("R_restored_answer_persists",
          re.search(r"withdrawal_frequency_24h|\d{4}-\d{2}-\d{2}T", conv)
          is not None and bool(re.search(r"different destinations|"
                                         r"同一个目的地|不同的目的地", conv)),
          conv[:200])
    b.screenshot(f"{SHOTS}/u00005-browser-2-restored.png")

    results["PASS"] = all(v["ok"] for v in results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/u00005-browser-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
with open(f"/Users/vv/investigation-agent-client/docs/evaluations/"
          f"u00005-browser-{stamp}.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("checks:", {k: v["ok"] for k, v in results["checks"].items()})
sys.exit(0 if results.get("PASS") else 1)

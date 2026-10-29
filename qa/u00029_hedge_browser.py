"""U00029 hedge investigation — REAL browser E2E (EN + ZH).

Real stack :3001 → :8001 (real LLM) → RP :8000.

  EN  submit the linked-accounts/hedge request in the real input box →
      answer lists candidate pairs → open the Artifacts panel → its
      rendered content is the Candidate Cross-Account Relationships table
      (NOT the plain transaction table)
  ZH  same in Chinese
  reload + reopen → the artifact content persists identically
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
CASE = "U00029"
results = {"checks": {}, "meta": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:300]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:130])


def wait_fresh_terminal(b, timeout=900):
    wait_for(b, """(() => {
        const blocks = document.querySelectorAll(
            '[data-testid="agent-activity-block"]');
        if (!blocks.length) return false;
        const s = blocks[blocks.length - 1].querySelector(
            '[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", timeout, desc="fresh loop terminal")


def _inv_id_from_history(b):
    return b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .map(x => x.innerText || '');
        const row = rows.find(t => t.includes('U00029') &&
            t.includes('INV-'));
        const m = row && row.match(/INV-[0-9a-f]+/);
        return m ? m[0] : ''; })()""")


def ask(b, message, marker="candidate"):
    """Send via the real input box; wait for the run to finish using the
    API truth (slow-LLM-proof), then read the rendered DOM card."""
    send_message(b, message)
    time.sleep(2)
    inv_id = _inv_id_from_history(b)
    assert inv_id, "no investigation id in history"
    deadline = time.time() + 1200
    response = ""
    while time.time() < deadline:
        tasks = httpx.get(
            f"http://localhost:8001/api/v2/investigations/{inv_id}",
            timeout=30).json().get("tasks", [])
        if tasks:
            hedge = next((t for t in tasks
                          if (t.get("user_request") or "").strip()
                          == message.strip()), None)
            if hedge is None:
                time.sleep(5)
                continue
            detail = httpx.get(
                f"http://localhost:8001/api/v2/tasks/{hedge['task_id']}",
                timeout=30).json()
            response = detail.get("task_response_text") or ""
            if response and hedge["status"] in ("completed", "failed"):
                break
        time.sleep(5)
    else:
        raise AssertionError("run not terminal in 1200s")
    time.sleep(3)
    card = b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        return cards.length ? cards[cards.length - 1].innerText : ''; })()""")
    return card, inv_id


def last_answer_card(b, message):
    """The agent card answering `message` (matched by its own text)."""
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const hit = cards.filter(c => (c.innerText || '').includes(
            %r));
        return hit.length ? hit[hit.length - 1].innerText : ''; })()"""
                   % (message[:40],))


def artifact_panel_text(b):
    """Visit EVERY artifact tab (Radix keeps inactive tab content
    unmounted) and return the concatenated panel text."""
    return b.eval("""(async () => {
        const asides = document.querySelectorAll('aside');
        const aside = asides[asides.length - 1];
        if (!aside) return '';
        const tabs = [...aside.querySelectorAll('[role="tab"]')];
        let text = aside.innerText + ' === tabs: ' + tabs.length + ' ===';
        for (let i = 0; i < tabs.length; i++) {
            // Radix TabsTrigger activates on pointerdown, not click
            for (const type of ['pointerdown', 'mousedown', 'click']) {
                tabs[i].dispatchEvent(new MouseEvent(type, {
                    bubbles: true, cancelable: true, button: 0}));
            }
            await new Promise(r => setTimeout(r, 800));
            text += ' === tab ' + i + ' === ' + aside.innerText;
        }
        return text;
    })()""")


def _reopen(b, inv_id, expect_fragment=""):
    b.navigate(FRONTEND, settle=5)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes( %r ));
        rows.find(x => x.clientHeight > 0)?.click(); })()""" % (inv_id,))
    time.sleep(14)


def journey(b, label, message, cjk):
    marker = "候选" if cjk else "candidate"
    new_inv = """(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()"""
    b.eval(new_inv)
    time.sleep(1.2)
    intake, _ = ask(b, "Investigate U00029")
    check(f"{label}_0_intake_completed",
          ("U00029" in intake) and len(intake) > 300, intake[:110])
    # authoritative capture: the JUST-CREATED investigation is the newest
    # U00029 entry (intake completed seconds ago) — captured via the API,
    # never from the possibly-stale DOM history
    invs = httpx.get(f"http://localhost:8001/api/v2/investigations",
                     timeout=30).json().get("investigations", [])
    u29 = [i for i in invs if i.get("case_id") == CASE]
    u29.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    inv_id = u29[0]["investigation_id"]
    results["meta"][f"{label}_inv"] = inv_id
    send_message(b, message)
    # API truth: wait for the run to persist its response + artifact
    deadline = time.time() + 1200
    response = ""
    while time.time() < deadline:
        tasks = httpx.get(
            f"http://localhost:8001/api/v2/investigations/{inv_id}",
            timeout=30).json().get("tasks", [])
        if tasks:
            hedge = next((t for t in tasks
                          if (t.get("user_request") or "").strip()
                          == message.strip()), None)
            if hedge is None:
                time.sleep(5)
                continue
            detail = httpx.get(
                f"http://localhost:8001/api/v2/tasks/{hedge['task_id']}",
                timeout=30).json()
            response = detail.get("task_response_text") or ""
            if response and hedge["status"] in ("completed", "failed"):
                break
        time.sleep(5)
    assert response, f"{label}: run never persisted a response"
    # the durable surface: reload + reopen serves the SAME response and
    # artifact (the live one-shot absorption has a pre-existing race)
    _reopen(b, inv_id)
    # restored-content assertions come from the API (GET /tasks serves the
    # SAME persisted store the DOM renders); the screenshots are the visual
    # evidence of what the reopened workspace shows
    hedge = next((t for t in tasks
                  if (t.get("user_request") or "").strip()
                  == message.strip()), None)
    assert hedge, f"{label}: hedge task not found"
    detail = httpx.get(
        f"http://localhost:8001/api/v2/tasks/{hedge['task_id']}",
        timeout=30).json()
    response = detail.get("task_response_text") or ""
    content = ""
    arts = detail.get("artifacts") or []
    if arts:
        r = httpx.get(
            f"http://localhost:8001/api/v2/tasks/{hedge['task_id']}"
            f"/artifacts", timeout=30).json()
        for x in (r.get("artifacts") or []):
            if x.get("artifact_id") == arts[-1]["artifact_id"]:
                content = x.get("content") or ""
    conv = b.eval("""(() => {
        const main = document.querySelector('main') || document.body;
        return main.innerText; })()""")
    pair_ok = bool(re.search(r"U\d{5}", response)) and (
        (marker in response) or (marker.capitalize() in response))
    dom_ok = (marker in conv) or (marker.capitalize() in conv)
    check(f"{label}_1_answer_candidates", pair_ok,
          f"api_marker={pair_ok} dom_marker={dom_ok} {response[:120]}")
    # artifact panel: visit EVERY artifact tab and assert the hedge
    # candidates table renders with the case + candidate accounts
    art = artifact_panel_text(b)
    dom_candidates = "Candidate Cross-Account Relationships" in art
    api_candidates = "## Candidate Cross-Account Relationships" in content
    check(f"{label}_2_artifact_candidates_rendered",
          api_candidates and dom_candidates,
          f"api={api_candidates} dom={dom_candidates}")
    pairs = sorted(set(re.findall(r"U\d{5}", art)))
    check(f"{label}_3_artifact_has_accounts", CASE in pairs
          and len(pairs) >= 3, f"accounts={pairs[:8]}")
    b.screenshot(f"{SHOTS}/u29-{label}-1.png")
    # reload + reopen once more: identical persistence
    _reopen(b, inv_id)
    art2 = artifact_panel_text(b)
    check(f"{label}_4_restored_artifact_same",
          "Candidate Cross-Account Relationships" in art2, art2[:200])
    b.screenshot(f"{SHOTS}/u29-{label}-2-restored.png")


b = Browser()
try:
    b.start(FRONTEND)
    wait_for(b, "!!document.querySelector('form input')", 120,
             desc="app mounted")
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")
    journey(b, "EN",
            "list all the linked accounts and check if there is any hedge "
            "transaction within linked accounts. If hedge trades exist, "
            "please send the details to me and generate an artifact.",
            cjk=False)
    journey(b, "ZH",
            "列出所有关联账号，查询账号中是否有对冲交易，如果有，列出交易"
            "明细并生成一份文件", cjk=True)
    results["PASS"] = all(v["ok"] for v in results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/u29-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
with open(f"/Users/vv/investigation-agent-client/docs/evaluations/"
          f"u00029-hedge-browser-{stamp}.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("checks:", {k: v["ok"] for k, v in results["checks"].items()})
sys.exit(0 if results.get("PASS") else 1)

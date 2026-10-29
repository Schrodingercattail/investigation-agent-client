"""EN follow-up chip browser journey (closeout round).

Real stack :3001 → :8001 (real LLM) → RP :8000.

  1. real frontend, EN investigation "Investigate U00047"
  2. intake answer in English
  3. select F1 → EN selection ack + EN follow-up chips
  4. click the "Why is this finding flagged?" chip — a fetch hook captures
     the actual POST /submit body (follow_up_id + lang)
  5. the submitted intent resolves to the EN canonical intent; the loop
     answer arrives in English (no Chinese narrative)
  6. reload + reopen keeps the EN answer English
"""
import json
import re
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, click_finding_row, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
ZH = re.compile(r"[一-鿿]")
results = {"checks": {}, "meta": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:140])


def agent_cards(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


def last_card(b):
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        return cards.length ? cards[cards.length - 1].innerText : '';
    })()""")


def wait_fresh_terminal(b, timeout=600):
    wait_for(b, """(() => {
        const blocks = document.querySelectorAll(
            '[data-testid="agent-activity-block"]');
        if (!blocks.length) return false;
        const s = blocks[blocks.length - 1].querySelector(
            '[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", timeout, desc="fresh loop terminal")


def ask(b, message, timeout=600):
    before, before_last = agent_cards(b), last_card(b)
    send_message(b, message)
    wait_fresh_terminal(b, timeout)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        if (cards.length <= %d) return false;
        const last = cards[cards.length - 1];
        return last && last.innerText.length > 200
          && last.innerText !== %r;
    })()""" % (before, before_last), 300, desc="answer absorbed")
    time.sleep(1.5)
    return last_card(b)


b = Browser()
try:
    b.start(FRONTEND)
    wait_for(b, "!!document.querySelector('form input')", 120,
             desc="app mounted")
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    send_message(b, "Investigate U00047")
    wait_fresh_terminal(b)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last && last.innerText.length > 300
          && last.innerText.includes('Task');
    })()""", 300, desc="EN intake absorbed")
    time.sleep(1.5)
    intake = last_card(b)
    check("A1_en_intake_no_cjk", intake and not ZH.search(intake),
          intake[:110])

    # select F1 → EN ack + EN chips
    click_finding_row(b, "F1")
    time.sleep(3)
    ack = last_card(b)
    results["meta"]["ack_head"] = ack[:220]
    check("A2_en_ack_english",
          "You're now investigating" in ack and not ZH.search(ack),
          ack[:110])
    check("A3_en_chips_english",
          "Why is this finding flagged?" in ack
          and "Show related timeline" in ack,
          ack[:220])
    b.screenshot(f"{SHOTS}/chip-en-1-ack.png")

    # capture the actual /submit request body on chip click
    b.eval("""(() => {
        window.__lastSubmit = null;
        const orig = window.fetch;
        window.fetch = function(...args) {
            try {
                const url = typeof args[0] === 'string'
                  ? args[0] : (args[0] && args[0].url) || '';
                if (url.includes('/submit')) {
                    window.__lastSubmit = {
                        url: url,
                        body: (args[1] && args[1].body) || null };
                }
            } catch (e) {}
            return orig.apply(this, args);
        };
    })()""")
    clicked = b.eval("""(() => {
        const btns = [...document.querySelectorAll('button')];
        const chip = btns.find(x =>
            (x.textContent || '').trim().startsWith(
                'Why is this finding flagged?'));
        if (!chip) return 'NOT_FOUND';
        chip.click(); return 'CLICKED'; })()""")
    check("B1_chip_clicked", clicked == "CLICKED", clicked)
    time.sleep(2)
    submit_body = b.eval("window.__lastSubmit && window.__lastSubmit.body")
    results["meta"]["submit_body"] = submit_body
    body = json.loads(submit_body) if submit_body else {}
    check("B2_submit_carries_followup_id_and_en",
          body.get("follow_up_id") == "explain_finding"
          and body.get("lang") == "en",
          submit_body)

    # the loop answers the EN intent in English
    wait_fresh_terminal(b)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        return cards.some(c => (c.innerText || '').includes(
            'Why was this finding flagged?'));
    })()""", 300, desc="chip intent turn rendered")
    wait_fresh_terminal(b)
    time.sleep(2)
    answer = last_card(b)
    results["meta"]["chip_answer_head"] = answer[:300]
    check("B3_chip_answer_no_cjk", answer and not ZH.search(answer),
          answer[:160])
    b.screenshot(f"{SHOTS}/chip-en-2-answer.png")

    # reload + reopen: the EN answer stays English
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00047'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(14)
    restored = b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const c = cards.find(x => (x.innerText || '').includes(
            'Why was this finding flagged?'));
        return c ? c.innerText : ''; })()""")
    check("C_restored_en_kept",
          restored and not ZH.search(restored), restored[:160])
    b.screenshot(f"{SHOTS}/chip-en-3-restored.png")

    results["PASS"] = all(v["ok"] for v in results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/chip-en-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
with open(f"/Users/vv/investigation-agent-client/docs/evaluations/"
          f"chip-en-browser-{stamp}.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("checks:", {k: v["ok"] for k, v in results["checks"].items()})
sys.exit(0 if results.get("PASS") else 1)

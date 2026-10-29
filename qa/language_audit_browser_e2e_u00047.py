"""Language-audit real-browser E2E (Case A / Case B / ZH counterpart).

Real stack :3001 → :8001 (current tree, real LLM) → RP :8000.

  INV-1 (EN) "Investigate U00047" → EN intake;
             click F1 + "please explain what ML detected in details"
             → EN answer card (About header + body + boundary), facts kept
  INV-2 (EN) fresh investigation → Case B request (no finding focus)
             → EN About (canonical EN header), same/different verdict,
               withdrawal records with W-ids, no CJK in the card
  INV-3 (ZH) creation "查询 U00047 的整体情况" → ZH intake (M4);
             ZH Case B → ZH About + ZH body + records
  restore    reload + reopen each → restored answers keep their language

Also pins the selection-confirmation narrative face (M8): clicking a
finding while the conversation is zh renders 正在调查…; en renders
You're now investigating….
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
results = {"checks": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:300]}
    print(("PASS " if ok else "FAIL ") + name + " " + str(detail)[:120])


def agent_card_count(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


def last_agent_card(b):
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        return cards.length ? cards[cards.length - 1].innerText : '';
    })()""")


def wait_fresh_terminal(b, timeout=600):
    """Terminal state of the LATEST activity block (not the first)."""
    wait_for(b, """(() => {
        const blocks = document.querySelectorAll(
            '[data-testid="agent-activity-block"]');
        if (!blocks.length) return false;
        const s = blocks[blocks.length - 1].querySelector(
            '[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", timeout, desc="fresh loop terminal")


def ask_and_wait_answer(b, message, timeout=600):
    """Send one request and return the NEW answer card: waits for a fresh
    terminal block AND a new agent card beyond the pre-send count whose
    text differs from the pre-send last card (the ack/previous answer)."""
    before_count = agent_card_count(b)
    before_last = last_agent_card(b)
    send_message(b, message)
    wait_fresh_terminal(b, timeout)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        if (cards.length <= %d) return false;
        const last = cards[cards.length - 1];
        return last && last.innerText.length > 200
          && last.innerText !== %r;
    })()""" % (before_count, before_last), 180,
        desc="new answer card absorbed")
    time.sleep(1.5)
    return last_agent_card(b)


def new_inv(b, message):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    send_message(b, message)


b = Browser()
try:
    b.start(FRONTEND)
    wait_for(b, "!!document.querySelector('form input')", 120,
             desc="app mounted")
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")

    # ---------- INV-1: EN intake + Case A with F1 focused ----------------
    new_inv(b, "Investigate U00047")
    wait_fresh_terminal(b)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last && last.innerText.length > 300
          && last.innerText.includes('Task');
    })()""", 180, desc="intake answer absorbed")
    time.sleep(1.5)
    intake = last_agent_card(b)
    results["meta"] = {"en_intake_head": intake[:200]}
    check("A1_en_intake_no_cjk", intake and not ZH.search(intake),
          intake[:100])
    b.screenshot(f"{SHOTS}/lang-1-en-intake.png")

    # the M8 confirmation narrative face (en conversation)
    click_finding_row(b, "F1")
    time.sleep(1.2)
    ack = last_agent_card(b)
    check("A2_en_selection_ack_english",
          "You're now investigating" in ack and not ZH.search(ack), ack[:80])

    card = ask_and_wait_answer(
        b, "please explain what ML detected in details")
    results["meta"]["case_a_card"] = card[:400]
    check("B1_case_a_no_cjk", card and not ZH.search(card), card[:150])
    check("B2_case_a_facts", ("99." in card and "LightGBM" in card),
          card[:150])
    check("B3_case_a_task_completed", "completed" in card.lower())
    b.screenshot(f"{SHOTS}/lang-2-en-case-a.png")

    # ---------- INV-2: fresh EN investigation + Case B (no focus) --------
    new_inv(b, "Investigate U00047")
    wait_fresh_terminal(b)
    time.sleep(2)
    card = ask_and_wait_answer(
        b, "Are the withdrawal addresses all the same or "
           "different address? send all withdrawal records to me.")
    results["meta"]["case_b_card"] = card[:600]
    check("C1_case_b_no_cjk", card and not ZH.search(card), card[:150])
    about_line = next((ln for ln in card.split("\n")
                       if ln.startswith('About "')), "")
    check("C2_case_b_about_english",
          about_line.startswith('About "') and not ZH.search(about_line),
          about_line[:120])
    check("C3_case_b_verdict",
          ("different destination" in card.lower()
           or "same destination" in card.lower()
           or "different addresses" in card.lower()
           or "the same address" in card.lower()), card[:200])
    check("C4_case_b_records", "W00" in card, card[:200])
    b.screenshot(f"{SHOTS}/lang-3-en-case-b.png")

    # ---------- INV-3: ZH creation + ZH Case B ---------------------------
    new_inv(b, "查询 U00047 的整体情况")
    wait_fresh_terminal(b)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last && last.innerText.length > 300;
    })()""", 180, desc="zh intake answer absorbed")
    time.sleep(1.5)
    card = last_agent_card(b)
    results["meta"]["zh_intake_head"] = card[:200]
    check("D1_zh_intake_is_chinese", card and ZH.search(card), card[:100])
    # M8: the selection ack renders Chinese in a zh conversation
    click_finding_row(b, "F1")
    time.sleep(1.2)
    ack = last_agent_card(b)
    check("D2_zh_selection_ack_chinese",
          "正在调查" in ack, ack[:80])
    # drop the PENDING selection (reload loses pre-submit UI state) so the
    # Case-B ask stays case-level — a persisted F1 focus would bind the
    # withdrawal comparison to the ML finding and starve it of evidence
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00047'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(12)

    card = ask_and_wait_answer(
        b, "这些提现的目的地地址是同一个还是不同地址？"
           "把全部提现记录发我。")
    results["meta"]["zh_case_b_card"] = card[:500]
    check("E1_zh_case_b_is_chinese", card and ZH.search(card), card[:150])
    check("E2_zh_case_b_about_chinese",
          ZH.search(card.split("\n", 1)[0]), card[:120])
    check("E3_zh_case_b_records", "W00" in card, card[:150])
    b.screenshot(f"{SHOTS}/lang-4-zh-case-b.png")

    # ---------- restore: reload + reopen, language must survive ----------
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00047'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(14)
    restored_all = b.eval(
        "[...document.querySelectorAll('.flex.justify-start')]"
        ".map(c => c.innerText).join('\\n===\\n')")
    results["meta"]["restored_head"] = restored_all[:300]
    en_answer_card = b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const c = cards.find(x => (x.innerText || '')
            .includes('ML signal score'));
        return c ? c.innerText : ''; })()""")
    check("F1_restored_en_answer_no_cjk",
          en_answer_card and not ZH.search(en_answer_card),
          en_answer_card[:150])
    check("F2_restored_zh_present", ZH.search(restored_all),
          restored_all[:150])
    b.screenshot(f"{SHOTS}/lang-5-restored.png")

    results["PASS"] = all(v["ok"] for v in results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/lang-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
with open(f"/Users/vv/investigation-agent-client/docs/evaluations/"
          f"language-audit-browser-{stamp}.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(json.dumps({k: v for k, v in results.items()
                  if k != "checks"}, indent=2, ensure_ascii=False))
print("checks:", {k: v["ok"] for k, v in results["checks"].items()})
sys.exit(0 if results.get("PASS") else 1)

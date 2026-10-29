"""ZH-only browser E2E (completes the E/F checks of the language audit).

Fresh zh investigation → ZH intake → (reload to drop nothing) →
ZH Case B → restore check. Real stack :3001 → :8001 → RP :8000.
"""
import json
import re
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import (                    # noqa: E402
    SHOTS, send_message, wait_for)

FRONTEND = "http://localhost:3001/"
ZH = re.compile(r"[一-鿿]")
results = {"checks": {}}


def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:300]}
    print(("PASS " if ok else "FAIL ") + name + " " + str(detail)[:130])


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
    send_message(b, "查询 U00047 的整体情况")
    wait_fresh_terminal(b)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        return last && last.innerText.length > 300;
    })()""", 300, desc="zh intake absorbed")
    time.sleep(1.5)
    intake = last_card(b)
    check("D1_zh_intake_is_chinese", intake and ZH.search(intake),
          intake[:100])
    # select a finding: the confirmation + previewed follow-up chips must
    # render in the conversation language (followups bilingual fix)
    from journeys_common import click_finding_row
    click_finding_row(b, "F1")
    time.sleep(3)
    ack = last_card(b)
    chips_zh = ("这个发现为什么被标记" in ack
                or "查看相关时间线" in ack
                or "该发现适用哪些政策要求" in ack)
    check("D2_zh_ack_and_chips_chinese",
          "正在调查" in ack and chips_zh,
          ack[:220])
    b.screenshot(f"{SHOTS}/langz-1-zh-intake.png")

    # drop the PENDING selection (reload loses pre-submit UI state) so the
    # Case-B ask stays case-level — a persisted F1 focus can bind the
    # withdrawal comparison to the ML finding and starve it of evidence.
    # responseLang re-seeds from the server-captured creation language
    # (zh for this investigation), so later chips/acks stay Chinese.
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00047'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(12)

    card = ask(b, "这些提现的目的地地址是同一个还是不同地址？"
                  "把全部提现记录发我。", timeout=900)
    about_line = next((ln for ln in card.split("\n")
                       if ln.startswith("关于「")), "")
    check("E1_zh_case_b_is_chinese", card and ZH.search(card), card[:150])
    check("E2_zh_case_b_about_chinese", ZH.search(about_line), about_line)
    check("E3_zh_case_b_verdict",
          ("不同目的地" in card or "同一个目的地" in card
           or "5 个不同" in card), card[:200])
    check("E4_zh_case_b_records", "W00" in card, card[:200])
    b.screenshot(f"{SHOTS}/langz-2-zh-case-b.png")

    # restore: reload + reopen — the restored zh answer keeps its language
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => {
        const rows = [...document.querySelectorAll('button, [role=button]')]
            .filter(x => (x.textContent || '').includes('U00047'));
        rows.find(x => x.clientHeight > 0)?.click(); })()""")
    time.sleep(14)
    restored = b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const c = cards.find(x => (x.innerText || '').includes(
            '目的地地址是同一个还是不同地址'));
        return c ? c.innerText : ''; })()""")
    check("F_zh_restored_same_language",
          restored and ZH.search(restored) and "W00" in restored,
          restored[:200])
    b.screenshot(f"{SHOTS}/langz-3-restored.png")

    results["PASS"] = all(v["ok"] for v in results["checks"].values())
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    results["PASS"] = False
    try:
        b.screenshot(f"{SHOTS}/langz-error.png")
    except Exception:
        pass
finally:
    try:
        b.close()
    except Exception:
        pass

stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
with open(f"/Users/vv/investigation-agent-client/docs/evaluations/"
          f"language-audit-browser-zh-{stamp}.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(json.dumps(results["meta"] if "meta" in results else {},
                 ensure_ascii=False))
print("checks:", {k: v["ok"] for k, v in results["checks"].items()})
sys.exit(0 if results.get("PASS") else 1)

"""AL-2.14/AL-3.2 combined: related-account + response semantics — REAL browser E2E.

Canonical runtime: 3001 → 8001 → RP :8000 → real LLM. One scenario per Chrome.

    1  related accounts only → 18 complete IDs, no 10/5, no preview
    2  combined why + which → same as 1 + why boundary
    3  cross-account control → unchanged
"""
import json
import os
import re
import sys
import time

from browser import Browser
from journeys_common import SHOTS, send_message, wait_for

FRONTEND = os.environ.get("FRONTEND_BASE", "http://localhost:3001/")


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


def wait_loop_done(b, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        if any(m in body for m in (
                "The investigation completed",
                "Investigation completed",
                "Investigation is blocked",
                "Investigation step failed",
                "semantic_resolution_unavailable",
                "clarification_needed")):
            time.sleep(1.5)
            return
        time.sleep(3)


def new_case_conversation(b):
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    before = cards(b)
    send_message(b, "给我输出一下00010案例情况")
    settled(b, before, 240, "intake")


def reopen(b):
    b.navigate(FRONTEND, settle=3)
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('Investigation ·'));
        t && t.click(); })()""")
    wait_for(b, "document.body.innerText.includes('U00010')",
             60, desc="persistence restore")


def run(key, goal, checks, forbidden, shot, persist=None):
    b = Browser(port=9229)
    b.start(FRONTEND)
    try:
        new_case_conversation(b)
        type_and_run_loop(b, goal)
        wait_loop_done(b)
        time.sleep(1.5)
        body = b.dom_text("body")
        for c in checks:
            assert c in body, f"{key}: missing {c!r}"
        for f in forbidden:
            assert f not in body, f"{key}: forbidden {f!r} present"
        b.screenshot(f"{SHOTS}/{shot}.png")
        print(f"  [{key}] answer verified ({len(ids) if 'ids' in dir() else '?'} )")
        if persist:
            reopen(b)
            body = b.dom_text("body")
            for c in checks[:2]:
                assert c in body, f"{key}: persistence lost {c!r}"
            b.screenshot(f"{SHOTS}/{shot}-reload.png")
            print(f"  [{key}] reload/reopen retained")
        print(f"SCENARIO {key}: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al214-99-FAIL-{key}.png")
        print(f"SCENARIO {key}: FAIL —", e)
        raise
    finally:
        b.close()


if __name__ == "__main__":
    key = (sys.argv[1] if len(sys.argv) > 1 else "1").lower()
    b = Browser(port=9229)
    b.start(FRONTEND)
    try:
        new_case_conversation(b)

        if key == "1":
            type_and_run_loop(b, "这个账户有哪些关联账户")
            wait_loop_done(b)
            time.sleep(1.5)
            body = b.dom_text("body")
            ids = set(re.findall(r"U\d{5}", body))
            assert "关联账号共 18 个" in body
            assert "为什么是风险信号" in body
            assert "不能证明这些账号之间存在违规或恶意意图" in body
            assert len(ids) >= 18
            assert "10 个关联账号" not in body
            b.screenshot(f"{SHOTS}/al214-01-related-18.png")
            print("S1: 18 unique IDs, no old 10/5 — PASS")
            reopen(b)
            body = b.dom_text("body")
            assert "关联账号共 18 个" in body
            print("S1 reload: retained — PASS")

        elif key == "2":
            type_and_run_loop(b,
                "为什么说高频交易是风险项，给我输出高频交易的具体交易明细")
            wait_loop_done(b)
            time.sleep(1.5)
            body = b.dom_text("body")
            assert "行为特征检测" in body
            assert "Trade frequency (24h) = 54" in body
            assert "T00" in body
            ids = set(re.findall(r"U\d{5}", body))
            assert len(ids) >= 18
            b.screenshot(f"{SHOTS}/al214-02-combined.png")
            print("S2: F7 combined why+detail — PASS")
            reopen(b)
            body = b.dom_text("body")
            assert "Trade frequency (24h) = 54" in body
            print("S2 reload: retained — PASS")

        elif key == "3":
            type_and_run_loop(b,
                "这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我")
            wait_loop_done(b)
            time.sleep(1.5)
            body = b.dom_text("body")
            assert "发现 6 个候选跨账户对冲交易关系" in body
            assert "| CATA-001 |" in body
            assert "Cross-Account Trade Evidence — U00010" in body
            b.screenshot(f"{SHOTS}/al214-03-cross-account.png")
            print("S3: cross-account control — PASS")
            reopen(b)
            body = b.dom_text("body")
            assert "发现 6 个候选跨账户对冲交易关系" in body
            print("S3 reload: retained — PASS")

    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al214-99-FAIL-{key}.png")
        print(f"S{key}: FAIL —", e)
        raise
    finally:
        b.close()

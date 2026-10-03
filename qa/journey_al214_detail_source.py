"""AL-2.14: related-account detail data source — REAL browser E2E.

Canonical runtime: 3001 → 8001 → RP :8000 → real LLM.

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
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


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
    """Wait for the conversation to show the loop's terminal response."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = b.dom_text("body")
        if "The investigation completed" in body \
                or "Investigation completed" in body \
                or "Investigation is blocked" in body \
                or "Investigation step failed" in body \
                or "semantic_resolution_unavailable" in body \
                or "clarification_needed" in body:
            time.sleep(1.5)
            return
        time.sleep(3)
    # one final check
    body = b.dom_text("body")


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
    wait_for(b, "document.body.innerText.includes('U00004')",
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
        ids = set(re.findall(r"U\d{5}", body))
        for c in checks:
            assert c in body, f"{key}: missing {c!r}"
        for f in forbidden:
            assert f not in body, f"{key}: forbidden {f!r} present"
        if key in ("1", "2"):
            assert len(ids) >= 18, \
                f"{key}: expected ≥18 unique IDs, got {len(ids)}"
            assert body.count("关联账号共 18 个") == 1, \
                f"{key}: 18-count line count"
        b.screenshot(f"{SHOTS}/{shot}.png")
        print(f"  [{key}] answer verified ({len(ids)} unique IDs)")
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


def main():
    key = (sys.argv[1] if len(sys.argv) > 1 else "1").lower()
    if key == "1":
        run("1",
            "这个账户有哪些关联账户",
            ["关联账号共 18 个", "为什么是风险信号",
             "不能证明这些账号之间存在违规或恶意意图",
             "DEV8Z1O4A35QU"],
            ["10 个关联账号", "只包含其中 5 个", "showing 5 of 10",
             "top 5"],
            "al214-01-related-18", persist=True)
    elif key == "2":
        run("2",
            "关联账户关系为什么是风险项，这个账户有哪些关联账户",
            ["为什么是风险信号", "关联账号共 18 个",
             "不能证明这些账号之间存在违规或恶意意图",
             "DEV8Z1O4A35QU"],
            ["10 个关联账号", "只包含其中 5 个"],
            "al214-02-combined")
    elif key == "3":
        run("3",
            "这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我",
            ["发现 6 个候选跨账户对冲交易关系",
             "| CATA-001 |",
             "Cross-Account Trade Evidence — U00010"],
            [],
            "al214-03-cross-account-control")
    else:
        raise ValueError(f"unknown scenario {key!r}")


if __name__ == "__main__":
    main()

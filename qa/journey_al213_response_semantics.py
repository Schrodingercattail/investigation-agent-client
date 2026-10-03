"""AL-2.13: response-semantics corrective pass — REAL browser journey.

Canonical runtime: FRONTEND_BASE=http://localhost:3001/ → :8001 (current
code) → RP :8000 → real LLM. One scenario per Chrome.

    1  关联账户关系为什么是风险项，这个账户有哪些关联账户
       → ONE answer block: why (RP network facts + signal boundary) +
         10-total vs 5-returned disclosure + 5 IDs once + no churn
    2  ML Pattern Detection什么意思，为什么是风险
       → ML explanation (机器学习/LightGBM) + score semantics + feature
         context + attribution boundary + associated-only policy ref
    3  这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我
       → CONTROL: unchanged cross-account business answer
"""
import json
import os
import sys
import time

from browser import Browser
from journeys_common import SHOTS, open_activity_tab, activity_state, \
    send_message, wait_for

FRONTEND_BASE = os.environ.get("FRONTEND_BASE", "http://localhost:3001/")

SCENARIOS = {
    "1": ("关联账户关系为什么是风险项，这个账户有哪些关联账户",
          ["为什么是风险信号", "共有 10 个关联账号", "只包含其中 5 个",
           "U00337", "DEV8Z1O4A35QU",
           "不能证明这些账号之间存在违规或恶意意图"],
          ["Risk Platform 网络归属:10 个关联账号。"],
          "al213-01-related-accounts"),
    "2": ("ML Pattern Detection什么意思，为什么是风险",
          ["ML Pattern Detection 的含义", "机器学习", "LightGBM",
           "99.41/100", "不是校准过的欺诈概率",
           "不能准确断言某一个具体特征贡献了多少分",
           "ML Factors"],
          ["Investigation_and_Action_SOP"],
          "al213-02-ml-explanation"),
    "3": ("这个账户和其他账号是否存在对冲交易，如果有，输出相关证据给我",
          ["发现 6 个候选跨账户对冲交易关系",
           "匹配条件", "预算限制", "| CATA-001 |",
           "Cross-Account Trade Evidence — U00010"],
          [],
          "al213-03-cross-account-control"),
}


def agent_card_count(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


def wait_settled(b, before, timeout, desc):
    wait_for(b,
             """(() => {
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
            const t = btns.find(x => (x.title||'').includes('bounded Agent Loop'));
            if (!t) return 'NOT_FOUND'; if (t.disabled) return 'DISABLED'; t.click(); return 'CLICKED';
        })()""")
        if r == "CLICKED":
            return "CLICKED"
        time.sleep(2)
    return r


def run_scenario(key):
    goal, markers, forbidden, shot = SCENARIOS[key]
    b = Browser(port=9229)
    b.start(FRONTEND_BASE)
    try:
        b.eval("""(() => { const t = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('New Investigation'));
            t && t.click(); })()""")
        time.sleep(1.2)
        before = agent_card_count(b)
        send_message(b, "给我输出一下00010案例情况")
        wait_settled(b, before, 240, f"{key} intake reply")
        print(f"  [{key}] conversation established")

        clicked = type_and_run_loop(b, goal)
        assert clicked == "CLICKED", clicked
        wait_for(b, """(() => { const el =
            document.querySelector('[data-testid=agent-status]');
            return el && el.getAttribute('data-agent-status') !== 'running';
        })()""", 300, poll=2.0, desc=f"{key} loop terminal")
        time.sleep(1.5)
        body = b.dom_text("body")
        for marker in markers:
            assert marker in body, f"{key}: missing {marker!r}"
        for f in forbidden:
            assert f not in body, f"{key}: forbidden {f!r} present"
        if key == "1":
            assert body.count("为什么是风险信号") == 1, \
                "duplicated answer blocks"
            assert body.count("U00337") == 1
        b.screenshot(f"{SHOTS}/{shot}.png")
        print(f"  [{key}] answer verified in Conversation")

        # reload/reopen persistence — the reopen restore is asynchronous
        # (per-task getTask + getTaskResults); wait for the marker.
        b.navigate(FRONTEND_BASE, settle=3)
        clicked = b.eval("""(() => {
            const btns = [...document.querySelectorAll('button')];
            const t = btns.find(x => x.textContent.includes('Investigation ·'));
            if (!t) return 'NOT_FOUND'; t.click(); return 'CLICKED';
        })()""")
        assert clicked == "CLICKED", "reopen failed"
        wait_for(b,
                 "document.body.innerText.includes(" + json.dumps(markers[0]) + ")",
                 60, desc=f"{key}: persistence restore")
        body = b.dom_text("body")
        for marker in markers[:2]:
            assert marker in body, f"{key}: persistence lost {marker!r}"
        b.screenshot(f"{SHOTS}/{shot}-reload.png")
        print(f"  [{key}] reload/reopen retained")
        open_activity_tab(b)
        time.sleep(1)
        print("  ACTIVITY:", (activity_state(b) or "null")[:180])
        api_calls = [r for r in b.requests if "/api/" in r.get("url", "")]
        assert api_calls and not b.network_failures
        print(f"SCENARIO {key}: PASS")
    except AssertionError as e:
        b.screenshot(f"{SHOTS}/al213-99-FAIL-{key}.png")
        print(f"SCENARIO {key}: FAIL —", e)
        raise
    finally:
        b.close()


def main():
    key = (sys.argv[1] if len(sys.argv) > 1 else "1").lower()
    assert key in SCENARIOS, f"unknown scenario {key!r}"
    run_scenario(key)


if __name__ == "__main__":
    main()

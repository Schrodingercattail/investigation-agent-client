"""U00010 — same-session EN→ZH→EN per-turn response language acceptance.

ONE investigation; three consecutive free-text turns in the SAME browser
conversation: EN → ZH → EN. Per turn: task identified by goal filter
(excluding prior turns), task-terminal gate, task_response_text
persistence, and response-correlated DOM card matching.
"""
import json
import re
import sys
import time

import httpx

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import send_message         # noqa: E402

BACKEND = "http://localhost:8001"
FRONTEND = "http://localhost:3001/"
DB = "/Users/vv/investigation-agent-client/backend/data/investigation_agent_v2.db"
CASE = "U00010"

UNRESOLVED_MARKERS = (
    "尚未产生可判定的结果", "No determinable result",
    "当前证据不足以确定该项",
)

UNRESOLVED = ("尚未产生可判定的结果", "No determinable result",
              "当前证据不足以确定该项")


def _norm(t):
    return " ".join((t or "").split())


def body_language(text):
    t = re.sub(r"U\d{5,6}|\bF\d\b|\b[WT]\d{6}\b|CATA-\d+", " ", text or "")
    zh = sum(1 for ch in t if chr(0x4e00) <= ch <= chr(0x9fbf))
    latin = len(re.findall(r"\b[A-Za-z]{3,}\b", t))
    if zh >= 8 and zh > latin / 3:
        return "zh"
    if latin >= 8 and zh < latin / 3:
        return "en"
    return "mixed"


def composer_marker(text):
    if 'About "' in text:
        return "en"
    if "关于「" in text:
        return "zh"
    return None


def latest_goal_task(goal_substr, exclude=(), after=None):
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT task_id FROM tasks_v2 WHERE started_at > ? "
        "ORDER BY started_at DESC LIMIT 40", (after or "2000-01-01",)
    ).fetchall()
    for (tid,) in rows:
        if tid in exclude:
            continue
        doc = db.execute(
            "SELECT document FROM agent_states WHERE task_id=?",
            (tid,)).fetchone()
        if doc and goal_substr in (json.loads(doc[0]).get("goal") or ""):
            return tid
    return None


def persisted_response(task_id):
    r = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}", timeout=30).json()
    return r.get("task_response_text") or ""


def task_terminal(task_id):
    r = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}", timeout=30).json()
    return r.get("task", {}).get("status") in ("completed", "failed")


def find_card(b, prefix):
    cards = b.eval("""(() => JSON.stringify(
        [...document.querySelectorAll('.flex.justify-start')]
        .map(c => c.innerText)))()""")
    try:
        texts = json.loads(cards or "[]")
    except Exception:
        return ""
    nt = _norm
    for t in texts:
        if prefix and prefix in nt(t):
            return t
    return ""


def artifact_tab_count(b):
    n = b.eval("""(() => {
        const tabs = [...document.querySelectorAll('button')]
            .filter(x => /^#\\d+$/.test((x.textContent || '').trim()));
        if (tabs.length) return tabs.length;
        const panel = document.querySelector('[role="tabpanel"]')
            || [...document.querySelectorAll('.font-mono')]
            .find(x => (x.textContent || '').trim() === 'md');
        return panel ? 1 : 0;
    })()""")
    return int(n or 0)


def open_artifact_tab(b, n):
    return b.eval(f"""(() => {{
        const tabs = [...document.querySelectorAll('button')]
            .filter(x => /^#\\d+$/.test((x.textContent || '').trim()));
        const el = tabs[{n - 1}];
        if (!el) return 'NOT_FOUND';
        for (const t of ['pointerdown', 'mousedown', 'mouseup', 'click']) {{
            el.dispatchEvent(new MouseEvent(t, {{bubbles: true,
                cancelable: true, view: window}}));
        }}
        return 'CLICKED';
    }})()""")


def active_panel(b):
    return b.eval("""(() => {
        const panels = [...document.querySelectorAll('[role="tabpanel"]')];
        const vis = panels.find(p => p.clientHeight > 0)
            || panels[panels.length - 1];
        return vis ? vis.innerText : '';
    })()""")


def wait_task_terminal(task_id, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = task_status(task_id)
        if st in ("completed", "failed"):
            return st
        time.sleep(5)
    return None


def poll_new_task(after_iso, exclude=(), goal_substr=None, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        tid = latest_goal_task(goal_substr, exclude=exclude, after=after_iso)
        if tid:
            return tid
        time.sleep(3)
    return None


def run_turn(b, ask_text, goal_substr, exclude):
    send_message(b, ask_text)
    tid = poll_new_task("2000-01-01", exclude, goal_substr)
    if tid is None:
        return None, ""
    wait_task_terminal(tid, 900)
    resp = persisted_response(tid)
    lang = body_language(resp)
    marker = composer_marker(resp)
    return tid, resp, lang, marker


def main():
    checks = {}
    turns = {}

    def chk(name, ok, detail=""):
        entry = {"ok": bool(ok), "detail": str(detail)[:300]}
        checks[name] = entry
        print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:110])

    b = Browser()
    try:
        b.start(FRONTEND)
        wait_for(b, "!!document.querySelector('form input')", 60,
                 desc="app mounted")
        b.eval("""(() => { try {
            Object.defineProperty(document, 'hidden',
                {get: () => false, configurable: true});
        } catch (e) {} })()""")

        send_message(b, f"Investigate {CASE}")
        intake_tid = latest_goal_task("2000-01-01", [], "Investigate")
        chk("intake_task_created", intake_tid is not None,
            f"intake={intake_tid}")
        wait_task_terminal(intake_tid, 900)
        chk("intake_terminal", True, f"intake={intake_tid}")
        prior = [intake_tid]

        for name, expect, ask_text in [
                ("turn1", "en",
                 "Explain the evidence for F7 Trading Frequency (24h) in "
                 "case U00010. Distinguish the observed trading activity "
                 "from any inferred risk conclusion."),
                ("turn2", "zh",
                 "请用中文解释同一案例中 F7 交易频率的现有证据，并区分已经"
                 "观察到的交易活动与尚未被证实的风险结论。"),
                ("turn3", "en",
                 "Now summarize the remaining evidence gaps for F7 in "
                 "English, without treating missing evidence as a "
                 "confirmed fact.")]:
            send_message(b, ask_text)
            tid = latest_goal_task(ask_text[:20], exclude=prior)
            chk(f"{name}_task_created", tid is not None, f"tid={tid}")
            if tid is None:
                continue
            prior.append(tid)
            wait_task_terminal(tid, 900)
            resp = persisted_response(tid)
            chk(f"{name}_response_persisted", len(resp) >= 100,
                f"len={len(resp)}")
            lang = body_language(resp)
            marker = composer_marker(resp)
            chk(f"{name}_response_language", lang == expect,
                f"lang={lang} expect={expect}")
            chk(f"{name}_composer_marker", marker == expect,
                f"marker={marker} expect={expect}")
            chk(f"{name}_no_unresolved",
                not any(m in resp for m in UNRESOLVED_MARKERS),
                str([m for m in UNRESOLVED_MARKERS if m in resp]))
    except Exception as e:
        evidence["error"] = f"{type(e).__name__}: {e}"[:300]
        evidence["PASS"] = False
    finally:
        import datetime
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ")
        path = ("/Users/vv/investigation-agent-client/docs/evaluations/"
                f"u00010-sslang-{stamp}.json")
        with open(path, "w") as f:
            json.dump(evidence, f, indent=1, ensure_ascii=False)
        print("results →", path)
        print("PASS" if evidence.get("PASS") else "FAIL")
        return 0 if evidence.get("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())

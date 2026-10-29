"""U00010 Phase-2 — REAL BROWSER E2E acceptance (B1–B7).

Real stack: headless Chrome → :3001 (current vite tree) → :8001 (phase-1
backend) → RP :8000 (live) → real LLM (glm via z.ai).

Every journey performs the REAL user actions in the real frontend: type
into the composer, submit the form, click finding rows and Suggested
next-step chips (Radix-safe click), read the RENDERED answer and the
RENDERED artifact panel content, and cross-check persisted artifact
content via the backend API. No API-driven substitution of browser
evidence; no mocked providers.

Evidence: docs/evaluations/u00010-phase2-browser-<stamp>.json (+ per-
journey screenshots under qa/screenshots/u00010-p2-*). Each journey runs
EXACTLY ONE automated attempt; a failed journey is recorded FAIL.
"""
import json
import os
import re
import subprocess
import sys
import time

import httpx

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
import journeys_common                           # noqa: E402
from journeys_common import (                    # noqa: E402
    click_finding_row, send_message, wait_for)

SHOTS = ("/Users/vv/investigation-agent-client/qa/screenshots")
# journeys_common.wait_for writes its timeout screenshot via its OWN
# module-level SHOTS ("../qa/screenshots") — repoint it to the absolute
# path so timeouts produce evidence instead of a FileNotFoundError that
# masks the real failing check.
journeys_common.SHOTS = SHOTS

BACKEND = "http://localhost:8001"
FRONTEND = "http://localhost:3001/"
UNRESOLVED = ("尚未产生可判定的结果", "当前证据不足以确定该项",
              "No determinable result", "无法回答该请求",
              "当前系统能力与数据，无法回答")
results = {"journeys": {}, "meta": {}}
CUR = {"journey": "init"}


def check(name, ok, detail=""):
    j = results["journeys"][CUR["journey"]]
    j["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    print(("PASS " if ok else "FAIL ") + f"[{CUR['journey']}] " + name
          + "  " + str(detail)[:110])


def meta(**kw):
    results["journeys"][CUR["journey"]]["meta"].update(kw)


def has_unresolved(t):
    return any(m in t for m in UNRESOLVED)


def low(t):
    return (t or "").lower()


def body_language(text):
    """Dominant body language excluding ids/titles/case refs/tool names."""
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


# --- browser helpers ---------------------------------------------------------



def _norm(t):
    return " ".join((t or "").split())


def _find_card(b, prefix):
    cards = b.eval("""(() => JSON.stringify(
        [...document.querySelectorAll('.flex.justify-start')]
        .map(c => c.innerText)))()""")
    try:
        texts = json.loads(cards or "[]")
    except Exception:
        return ""
    for t in texts:
        if prefix and prefix in _norm(t):
            return t
    return ""


def prep_page(b):
    wait_for(b, "!!document.querySelector('form input')", 120,
             desc="app mounted")
    b.eval("""(() => { try {
        Object.defineProperty(document, 'hidden',
            {get: () => false, configurable: true});
        Object.defineProperty(document, 'visibilityState',
            {get: () => 'visible', configurable: true});
    } catch (e) {} })()""")


def agent_cards(b):
    return b.eval(
        "document.querySelectorAll('.flex.justify-start').length")


def last_card(b):
    return b.eval("""(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        return cards.length ? cards[cards.length - 1].innerText : '';
    })()""")


def wait_fresh_terminal(b, timeout=900):
    wait_for(b, """(() => {
        const blocks = document.querySelectorAll(
            '[data-testid="agent-activity-block"]');
        if (!blocks.length) return false;
        const s = blocks[blocks.length - 1].querySelector(
            '[data-testid="activity-block-status"]');
        return s && s.getAttribute('data-tone') !== 'running';
    })()""", timeout, desc="loop terminal")


def wait_new_answer(b, before, before_last, timeout=600):
    # `before` MUST be captured BEFORE the action that triggers the answer
    # (send / chip click) — the answer card renders LATE (minutes on a
    # slow LLM), so a short stability window silently returns the previous
    # card. Wait for cards.length to GROW, then for the new last card to
    # stabilize.
    # count may stay flat if the turn re-rendered in place: accept a
    # large CONTENT change of the last card instead (JS predicate —
    # wait_for evaluates strings via CDP)
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        if (cards.length > %d) return true;
        const last = cards[cards.length - 1];
        return !!(last && (last.innerText || '').length > %d + 100);
    })()""" % (before, len(before_last or "")), timeout,
        desc="answer card rendered")
    # the turn card mounts BEFORE the composed answer text fills in
    # (captured live: A9 B1_zh / F3 B5_zh assertions read a header-only
    # card). The composed answer always quotes its output claim — EN
    # 'About "..."' / zh 「关于「…」」 — so accept only a card carrying it.
    wait_for(b, """(() => {
        const cards = [...document.querySelectorAll('.flex.justify-start')];
        const last = cards[cards.length - 1];
        const t = (last && last.innerText) || '';
        return t.includes('About "') || t.includes('关于「');
    })()""", timeout, desc="answer composition present")
    prev = last_card(b)
    stable = 0
    for _ in range(36):          # up to ~3 min of render lag tolerance
        time.sleep(5)
        cur = last_card(b)
        if cur == prev and len(cur) > 150:
            stable += 1
            if stable >= 2:      # unchanged across 3 consecutive polls
                return cur
        else:
            stable = 0
        prev = cur
    return prev


def answer_card_for_task(b, tid, timeout=600):
    """The assistant answer card of THIS turn, located by the REAL task
    id the frontend renders on its activity block (AgentActivityBlock:
    data-task-id={turn.task.task_id}) — exact answer↔task correlation,
    never the intake card or an older answer. Waits for the block, then
    for its card to carry the composed answer and stabilize."""
    block_sel = f'[data-task-id="{tid}"]'
    wait_for(b, f"(() => !!document.querySelector('{block_sel}'))",
             timeout, desc="turn block by task id")

    def card_js():
        return ("(() => { const block = document.querySelector("
                + json.dumps(block_sel) + "); if (!block) return '';"
                " const card = block.closest('.flex.justify-start');"
                " return card ? card.innerText : ''; })()")

    prev = b.eval(card_js())
    stable = 0
    for _ in range(24):
        time.sleep(5)
        cur = b.eval(card_js())
        if cur == prev and len(cur) > 150:
            stable += 1
            if stable >= 2:
                return cur
        else:
            stable = 0
        prev = cur
    return prev


def ask(b, message, timeout=900):
    before, before_last = agent_cards(b), last_card(b)
    send_message(b, message)
    wait_fresh_terminal(b, timeout)
    return wait_new_answer(b, before, before_last)


def artifact_badge_count(b):
    # current panel renders the count as a BADGE ("Artifacts 2"), not
    # "(N)" — count the real #N tab buttons; a single artifact renders
    # with NO tabs (direct panel) so detect the panel itself
    n = b.eval("""(() => {
        const tabs = [...document.querySelectorAll('button')]
            .filter(x => /^#\d+$/.test((x.textContent || '').trim()));
        if (tabs.length) return tabs.length;
        const panel = document.querySelector('[role="tabpanel"]')
            || [...document.querySelectorAll('.font-mono')]
            .find(x => (x.textContent || '').trim() === 'md');
        return panel ? 1 : 0;
    })()""")
    return int(n or 0)


def open_artifact_tab(b, n):
    """Select artifact tab #n (1-based) with REAL Radix pointer events."""
    return b.eval(f"""(() => {{
        const tabs = [...document.querySelectorAll('button')]
            .filter(x => /^#\\d+$/.test((x.textContent || '').trim()));
        const el = tabs[{n - 1}];
        if (!el) return 'NOT_FOUND';
        for (const t of ['pointerdown','mousedown','mouseup','click']) {{
            el.dispatchEvent(new MouseEvent(t, {{bubbles: true,
                cancelable: true, view: window}}));
        }}
        return 'CLICKED';
    }})()""")


def active_panel_text(b):
    return b.eval("""(() => {
        const panels = [...document.querySelectorAll(
            '[role="tabpanel"]')];
        const visible = panels.find(p => p.clientHeight > 0)
            || panels[panels.length - 1];
        return visible ? visible.innerText : '';
    })()""")


def wait_until(fn, timeout=90, poll=5, desc="condition"):
    """Python-side bounded poll for eventually-consistent state (panel
    tab ingestion, artifact persistence). No silent budget expansion."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(poll)
    return last


RECORD_ROW_RE = re.compile(r"^(W00\d{3}|T00\d{3}) \|", re.M)
# innerText renders headers WITHOUT markdown hashes and pipe-table rows
# WITHOUT pipes — the stable record-row marker is the stream-prefixed id
# ("withdrawal:W000087" / "transaction:T000670"), which only exists in
# records rows, never in prose.
RECORDS_SECTION_RE = re.compile(
    r"Evidence for (F\d+) \(complete record set\)(.*?)(?=Evidence for |Source Tool Calls|\Z)",
    re.S)


def records_family_rows(content):
    """Records rows counted ONLY inside '### Evidence for F<n> (complete
    record set)' sections. A record row is the pipe-table line
    '| withdrawal:W000087 | …' / '| transaction:T000670 | …' (live
    format — the stream prefix sits between the pipe and the id).
    Signal Explanation / policy prose citing ids inline is never a
    record row. Returns {family_id: (w_rows, t_rows)} per section."""
    out = {}
    for m in RECORDS_SECTION_RE.finditer(content or ""):
        fid = m.group(1)
        section = m.group(2)
        out[fid] = (len(re.findall(r"withdrawal:W\d{4,}", section)),
                    len(re.findall(r"transaction:T\d{4,}", section)))
    return out


def file_stream_verdict(content):
    """(is_w_file, is_t_file) for ONE artifact: exactly one records
    family section with >=2 record rows and zero rows of the other
    family across ALL records sections."""
    fams = records_family_rows(content)
    w_total = sum(v[0] for v in fams.values())
    t_total = sum(v[1] for v in fams.values())
    return (w_total >= 2 and t_total == 0, t_total >= 2 and w_total == 0)


def wait_task_terminal(task_id, timeout=900):
    """PRIMARY completion gate: poll the REAL task API until a terminal
    status (completed/failed). Mid-flight (executing etc.) never closes
    the evidence window. Returns the final status string or None on
    timeout (caller records BLOCKED/NOT TESTED)."""
    terminal = {"completed", "failed"}
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            d = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}",
                          timeout=30).json()
            last = (d.get("task") or {}).get("status")
            if last in terminal:
                return last
        except Exception:
            pass
        time.sleep(5)
    return last


def persisted_artifacts(task_id):
    r = httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}/artifacts",
                  timeout=30).json()
    return [(a.get("artifact_id"), a.get("content") or "")
            for a in (r.get("artifacts") or [])]


def new_session(b, case, lang="en"):
    b.navigate(FRONTEND, settle=3)
    prep_page(b)
    b.eval("""(() => { const t = [...document.querySelectorAll('button')]
        .find(x => x.textContent.includes('New Investigation'));
        t && t.click(); })()""")
    time.sleep(1.2)
    # the UI conversation language (incl. follow-up chips) follows the
    # ENTRY message — zh journeys enter zh so the zh chips exist to click
    intake = f"调查{case}" if lang == "zh" else f"Investigate {case}"
    # snapshot BEFORE send: the answer card may render before the terminal
    # status lands, so a post-terminal snapshot would include the answer
    # and the render-wait could never pass
    before, before_last = agent_cards(b), last_card(b)
    send_message(b, intake)
    wait_fresh_terminal(b)
    wait_new_answer(b, before, before_last, 600)
    return last_card(b)


def click_chip(b, label, label_alt=None):
    """A finding-row click ack carries no message text, so the follow-up
    face() defaults to the EN label even in a zh session — match EITHER
    face; the semantic guarantee is the submitted follow_up_id."""
    clicked = b.eval(f"""(() => {{
        const want = [{json.dumps(label)}, {json.dumps(label_alt)}]
            .filter(Boolean);
        const btns = [...document.querySelectorAll('button')];
        const chip = btns.find(x =>
            want.includes((x.textContent || '').trim()));
        if (!chip) return 'NOT_FOUND';
        chip.click(); return 'CLICKED';
    }})()""")
    return clicked


def read_submit_body(b):
    for r in reversed(b.requests):
        if r.get("url", "").endswith("/submit") and r.get("body"):
            return r["body"]
    return None


def goal_substr_for(expect):
    """Language-distinguishing substring expected in the turn's persisted
    goal. zh turn: the request's own topic phrase; en turns: the finding
    reference (turn1/turn3 are separated by exclude_ids=prior)."""
    return "交易频率" if expect == "zh" else "F7"


def persisted_response(task_id):
    """Stored composed response for a task (session.turn_responses →
    GET /tasks/{id}.task_response_text); '' when absent."""
    try:
        return (httpx.get(f"{BACKEND}/api/v2/tasks/{task_id}",
                          timeout=30).json().get("task_response_text")
                or "")
    except Exception:
        return ""


def latest_task_since(iso_utc, goal_substr=None, exclude_ids=None):
    """Latest task started after iso_utc whose goal carries goal_substr.
    exclude_ids: task ids that must NEVER be returned (e.g. an earlier
    ask in the same journey — the second lookup must not re-match the
    first task when both requests share goal vocabulary)."""
    import sqlite3
    excluded = {str(x) for x in (exclude_ids or [])}
    db = sqlite3.connect(
        "/Users/vv/investigation-agent-client/backend/data/"
        "investigation_agent_v2.db")
    rows = db.execute(
        "SELECT task_id FROM tasks_v2 WHERE started_at > ? "
        "ORDER BY started_at DESC LIMIT 40", (iso_utc,)).fetchall()
    if not goal_substr:
        for (tid,) in rows:
            if tid not in excluded:
                return tid
        return None
    for (tid,) in rows:
        if tid in excluded:
            continue
        r = db.execute(
            "SELECT document FROM agent_states WHERE task_id=?",
            (tid,)).fetchone()
        if r and goal_substr in (json.loads(r[0]).get("goal") or ""):
            return tid
    return None


# --- journeys ----------------------------------------------------------------

def journey_b1(b, lang):
    _set(f"B1_{lang}")
    case = "U00010"
    new_session(b, case, lang)
    if lang == "zh":
        ask_text = ("高频提现和新账号高频活动的判定条件分别是什么？"
                    "给我生成提现和交易明细文件")
        w_rules = ["阈值", "命中规则", "超过", "频率", "24", "withdrawal"]
        a_rules = ["阈值", "命中规则", "活跃", "交易", "54",
                   "new account"]
    else:
        ask_text = ("What are the determination criteria for High "
                    "Withdrawal Frequency and for New Account with High "
                    "Activity? Generate the withdrawal detail file and "
                    "the transaction detail file.")
        w_rules = ["threshold", "rule", "exceed", "frequency", "24"]
        a_rules = ["threshold", "rule", "activ", "trade", "54"]
    # order: send → TASK-API terminal gate (primary) → task-linked
    # answer capture (data-task-id on the turn's activity block). The
    # DOM waits alone returned mid-flight on long multi-re-plan runs
    # (live: T112515Z) and the About-guard alone mistook the intake
    # card for the answer (live: T141055Z).
    send_message(b, ask_text)
    # goal-filtered task match (same rule as the persisted check): the
    # unfiltered lookup races the intake task / pre-mount rows
    goal_substr = "判定条件" if lang == "zh" else "criteria"
    # 180s: the goal-matching agent_state is persisted only AFTER the
    # goal-understanding LLM call (latency variance observed live)
    tid = wait_until(lambda: latest_task_since(CUR["start_iso"], goal_substr),
                     timeout=180, desc="task identified")
    meta(task=tid)
    if not tid:
        check("task_identified", False, "no task matched the ask")
        return
    status = wait_task_terminal(tid, timeout=900)
    meta(task_status=status)
    check("task_reached_terminal", status in ("completed", "failed"),
          f"status={status}")
    if status not in ("completed", "failed"):
        check("task_completed", False, f"final status={status}")
        return
    check("task_completed", status == "completed", f"status={status}")
    # B1-en-response-correlation: the composed answer is stored per task
    # (session.turn_responses → GET /tasks/{tid}.task_response_text) and
    # the frontend renders THAT text in the turn's card. Correlate the
    # DOM card by content: poll the API for the stored response, then
    # wait for a card whose normalized text contains its normalized
    # prefix — intake cards, older answers, and partial generations
    # cannot match (validated offline against real texts).
    api_text = wait_until(lambda: (lambda t: t if len(t or "") >= 100
                                   else None)(
        (httpx.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=30)
         .json().get("task_response_text") or "")),
        timeout=120, desc="composed response stored")
    if not api_text:
        check("answer_matches_task_response", False,
              "no composed response stored for this task")
        meta(answer_head="")
        return
    meta(api_response_len=len(api_text))

    def _norm(t):
        return " ".join((t or "").split())
    _prefix = _norm(api_text)[:100]

    def _match_card():
        cards = b.eval("""(() => JSON.stringify(
            [...document.querySelectorAll('.flex.justify-start')]
            .map(c => c.innerText)))()""")
        try:
            texts = json.loads(cards or "[]")
        except Exception:
            return None
        for t in texts:
            if _prefix in _norm(t) and len(_norm(t)) >= len(_prefix) + 50:
                return t
        return None
    answer = wait_until(_match_card, timeout=300,
                        desc="DOM card carrying this task's response")
    if not answer:
        check("answer_matches_task_response", False,
              "no rendered card carries this task's response")
        return
    check("answer_matches_task_response", True,
          f"card len={len(answer)}")
    # optional refinement: the task-id block may never mount live; the
    # response-correlated card above is already the accepted capture
    try:
        refined = answer_card_for_task(b, tid, timeout=120)
        if refined:
            answer = refined
    except Exception:
        pass
    meta(answer_head=answer[:400])
    check("criteria_withdrawal_content",
          any(k.lower() in low(answer) for k in w_rules), answer[:150])
    check("criteria_new_account_content",
          any(k.lower() in low(answer) for k in a_rules), answer[:150])
    check("criteria_no_unresolved", not has_unresolved(answer),
          str([m for m in UNRESOLVED if m in answer]))
    # the panel ingests task artifacts by polling — wait for BOTH files
    # (240s: the frontend artifact polling interval exceeded the previous
    # 90s window in the live B1_en run, docs/evaluations/…T123133Z.json)
    n_tabs = wait_until(lambda: (lambda n: n if n >= 2 else None)(
        artifact_badge_count(b)), timeout=240, desc="2 artifact tabs") \
        or artifact_badge_count(b)
    meta(artifact_count=n_tabs)
    # structural record-family judgment (NOT bare id regex — explain/
    # policy text can cite ids): a records FILE shows pipe-table rows
    # "| W000xxx …" / "| T000xxx …" (>=2) and is mutually exclusive with
    # the other family.
    w_rendered = t_rendered = False
    w_tab = t_tab = None
    seen = []
    for i in range(1, max(n_tabs, 1) + 1):
        if n_tabs > 1:
            open_artifact_tab(b, i)
            time.sleep(1.2)
        content = active_panel_text(b)
        seen.append(content[:60])
        w_sec, t_sec = file_stream_verdict(content)
        if w_sec:
            w_rendered, w_tab = True, i
        if t_sec:
            t_rendered, t_tab = True, i
    check("withdrawal_file_renders_withdrawal_records", w_rendered,
          f"tabs={n_tabs} w_tab={w_tab}")
    check("trade_file_renders_trade_records", t_rendered,
          f"tabs={n_tabs} t_tab={t_tab}")
    check("record_files_are_distinct_tabs",
          bool(w_tab and t_tab and w_tab != t_tab),
          f"w_tab={w_tab} t_tab={t_tab}")
    tid = latest_task_since(CUR["start_iso"],
                            "判定条件" if lang == "zh" else "criteria")
    meta(task=tid)
    if tid:
        # artifact persistence lands AFTER task completion (observed:
        # GET [] at check time, both files present seconds later) — poll
        def _scoped():
            arts = persisted_artifacts(tid)
            pw = any(file_stream_verdict(c)[0] for _, c in arts)
            pt = any(file_stream_verdict(c)[1] for _, c in arts)
            return (arts, pw, pt) if (pw and pt) else None
        got = wait_until(_scoped, timeout=90, desc="scoped persisted files")
        if got:
            arts, pw, pt = got
            check("persisted_files_stream_scoped", True,
                  str([(i, len(c)) for i, c in arts]))
        else:
            arts = persisted_artifacts(tid)
            check("persisted_files_stream_scoped", False,
                  str([(i, len(c)) for i, c in arts]))
    else:
        check("persisted_files_stream_scoped", False, "no task id")
    b.screenshot(f"{SHOTS}/u00010-p2-b1-{lang}-answer.png")


def journey_b2(b, lang):
    _set(f"B2_{lang}")
    case = "U00010"
    new_session(b, case, lang)
    if lang == "zh":
        ambiguous = "把与关联账号相关的引文原文发给我"
        explicit = "我要的是与关联账号相关的政策引文，把该条政策原文发我"
        policy1 = ("政策" in a_ if False else None)
    else:
        ambiguous = ("Send me the original text of the citations related "
                     "to the linked accounts.")
        explicit = ("I meant the policy citations related to the linked "
                    "accounts — send me that policy's original text.")
    # response-correlated capture (proven in journey_b1): the A9-era
    # B2_en failure captured the intake card while the REAL answer
    # (TASK-e6cc964918bd: AML 5.1 policy citations + excerpt) was
    # rendered — correlate the card to THIS ask's task via its stored
    # composed response.
    send_message(b, ambiguous)
    goal_substr = "引文" if lang == "zh" else "citations"
    tid1 = wait_until(lambda: latest_task_since(CUR["start_iso"], goal_substr),
                      timeout=180, desc="ambiguous-ask task identified")
    meta(task1=tid1)
    if not tid1:
        check("task_identified_ambiguous", False, "no task matched")
        return
    status1 = wait_task_terminal(tid1, timeout=900)
    check("ambiguous_task_terminal", status1 == "completed",
          f"status={status1}")
    api1 = wait_until(lambda: (lambda t: t if len(t or "") >= 100 else None)(
        (httpx.get(f"{BACKEND}/api/v2/tasks/{tid1}", timeout=30)
         .json().get("task_response_text") or "")),
        timeout=120, desc="composed response stored")

    _prefix1 = _norm(api1 or "")[:100]
    a1 = wait_until(lambda: _find_card(b, _prefix1) or None,
                    timeout=300, desc="ambiguous answer card")
    a1 = a1 or ""
    check("answer_matches_task_response_ambiguous",
          _prefix1 in _norm(a1), f"card len={len(a1)}")
    meta(ambiguous_head=a1[:400])
    if lang == "zh":
        check("ambiguous_not_network_substitution",
              "关联账号共" not in a1 and not has_unresolved(a1), a1[:160])
        pol1 = (("政策" in a1 or "引文" in a1) and
                ("AML" in a1.upper() or "原文" in a1 or "引用" in a1))
    else:
        check("ambiguous_not_network_substitution",
              "related-account network attribution details" not in a1
              and not has_unresolved(a1), a1[:160])
        pol1 = ("citation" in low(a1) or "policy" in low(a1))
    check("ambiguous_citation_or_clarification", pol1, a1[:160])
    send_message(b, explicit)
    # exclude tid1: both EN asks share the word "citations", so an
    # unfiltered lookup raced the goal persistence and re-matched the
    # FIRST task (live: T030209Z recorded tid2 == tid1)
    tid2 = wait_until(
        lambda: latest_task_since(CUR["start_iso"], goal_substr,
                                  exclude_ids=[tid1]),
        timeout=180, desc="explicit-ask task identified")
    meta(task2=tid2)
    status2 = wait_task_terminal(tid2, timeout=900)
    check("explicit_task_terminal", status2 == "completed",
          f"status={status2}")
    api2 = wait_until(lambda: (lambda t: t if len(t or "") >= 100 else None)(
        (httpx.get(f"{BACKEND}/api/v2/tasks/{tid2}", timeout=30)
         .json().get("task_response_text") or "")),
        timeout=120, desc="explicit composed response stored")
    _prefix2 = _norm(api2 or "")[:100]
    a2 = wait_until(lambda: _find_card(b, _prefix2) or None,
                    timeout=300, desc="explicit answer card")
    a2 = a2 or ""
    check("answer_matches_task_response_explicit",
          _prefix2 in _norm(a2), f"card len={len(a2)}")
    meta(explicit_head=a2[:400])
    doc_marker = ("AML" in a2.upper() or "政策" in a2
                  or "citation" in low(a2))
    provenance = ("[3]" in a2 or "原文摘录" in a2 or "章节" in a2
                  or "section" in low(a2) or "document" in low(a2)
                  or "S" in a2 and "5.1" in a2)
    check("explicit_policy_citation_rendered", doc_marker
          and not has_unresolved(a2), a2[:160])
    check("explicit_citation_provenance", provenance, a2[:160])
    b.screenshot(f"{SHOTS}/u00010-p2-b2-{lang}-citation.png")


def journey_b3(b, lang):
    _set(f"B3_{lang}")
    case = "U00029"
    new_session(b, case, lang)
    if lang == "zh":
        ask_text = ("查询关联账号中是否存在对冲交易，如果有，发交易明细给我，"
                    "并生成一份明细文件")
        goal_substr = "对冲交易"
    else:
        ask_text = ("Check whether the linked accounts contain hedge "
                    "trades. If they exist, send me the transaction "
                    "details and generate a detail file.")
        goal_substr = "hedge"
    # response-correlated capture (journey_b1/b2 pattern): the A8/A9-era
    # B3 failures were capture races (intake card), not product defects.
    before, before_last = agent_cards(b), last_card(b)
    send_message(b, ask_text)
    tid = wait_until(lambda: latest_task_since(CUR["start_iso"], goal_substr),
                     timeout=180, desc="task identified")
    meta(task=tid)
    if not tid:
        check("task_identified", False, "no task matched the ask")
        return
    status = wait_task_terminal(tid, timeout=900)
    meta(task_status=status)
    check("task_reached_terminal", status in ("completed", "failed"),
          f"status={status}")
    if status not in ("completed", "failed"):
        check("task_completed", False, f"final status={status}")
        return
    check("task_completed", status == "completed", f"status={status}")
    api_text = wait_until(lambda: (lambda t: t if len(t or "") >= 100
                                   else None)(
        (httpx.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=30)
         .json().get("task_response_text") or "")),
        timeout=120, desc="composed response stored")

    def _norm(t):
        return " ".join((t or "").split())
    _prefix = _norm(api_text or "")[:100]

    def _find_card(prefix):
        cards = b.eval("""(() => JSON.stringify(
            [...document.querySelectorAll('.flex.justify-start')]
            .map(c => c.innerText)))()""")
        try:
            texts = json.loads(cards or "[]")
        except Exception:
            return ""
        for t in texts:
            if prefix and prefix in _norm(t):
                return t
        return ""
    answer = wait_until(lambda: _find_card(_prefix) or None,
                        timeout=300, desc="answer card")
    answer = answer or ""
    check("answer_matches_task_response",
          _prefix in _norm(answer), f"card len={len(answer)}")
    meta(answer_head=answer[:400])
    check("candidates_in_answer",
          "候选" in answer or "candidate" in low(answer), answer[:150])
    check("coverage_limitation_surfaced",
          ("预算" in answer or "coverage" in low(answer)
           or "不能据此证明" in answer or "cannot prove" in low(answer)
           or "受预算限制" in answer or "未检索范围" in answer),
          answer[-240:])
    # the panel ingests task artifacts by polling — bounded 240s wait
    n_tabs = wait_until(lambda: (lambda n: n if n >= 2 else None)(
        artifact_badge_count(b)), timeout=240, desc="2 artifact tabs") \
        or artifact_badge_count(b)
    meta(artifact_count=n_tabs)
    rendered = False
    for i in range(1, max(n_tabs, 1) + 1):
        if n_tabs > 1:
            open_artifact_tab(b, i)
            time.sleep(1.2)
        content = active_panel_text(b)
        if ("CATA-" in content and "U00" in content
                and (re.search(r"T00\d{3}", content)
                     or "matching criteria" in low(content)
                     or "判据" in content)):
            rendered = True
    check("artifact_renders_candidate_table", rendered, f"tabs={n_tabs}")
    if tid:
        arts = persisted_artifacts(tid)
        ok = any("CATA-" in c and "U00" in c and re.search(r"T00\d{3}", c)
                 for _, c in arts)
        check("persisted_artifact_has_candidates", ok,
              str([(i, len(c), "CATA-" in c) for i, c in arts]))
    else:
        check("persisted_artifact_has_candidates", False, "no task id")
    b.screenshot(f"{SHOTS}/u00010-p2-b3-{lang}-artifact.png")


def _journey_chip(b, tag, case, finding, label, judge, fu_id=None,
                  label_alt=None):
    _set(tag)
    new_session(b, case, tag.split("_")[-1])
    clicked_row = click_finding_row(b, finding)
    time.sleep(3)
    ack = last_card(b)
    meta(ack_head=ack[:220], row_click=clicked_row)
    check("finding_selected", clicked_row == "CLICKED", ack[:120])
    check("ack_names_finding", finding in ack, ack[:140])
    clicked = click_chip(b, label, label_alt)
    check("chip_clicked", clicked == "CLICKED", clicked)
    if clicked != "CLICKED":
        return
    time.sleep(2)
    body = read_submit_body(b)
    meta(submit_body=body)
    if fu_id:
        check("chip_submitted_intent",
              bool(body) and fu_id in body, str(body)[:160])
    before, before_last = agent_cards(b), last_card(b)
    wait_fresh_terminal(b, 900)
    answer = wait_new_answer(b, before, before_last, 300)
    meta(answer_head=answer[:400])
    judge(answer)
    b.screenshot(f"{SHOTS}/u00010-p2-{tag}-answer.png")


def journey_b4(b, lang):
    tag = f"B4_{lang}"
    if lang == "zh":
        label = "该发现适用哪些政策要求？"
        alt = "Which policy requirements apply?"

        def judge(a):
            low_a = low(a)
            check("context_f6_kept",
                  "f6" in low_a or "linked account" in low_a
                  or "关联账号" in a, a[:150])
            pol = ("AML" in a.upper() or "政策" in a or "引文" in a)
            citation = ("[3]" in a or "原文摘录" in a or "章节" in a
                        or "政策依据" in a or "适用" in a)
            check("policy_retrieval_based", pol and citation, a[:160])
            check("no_case_summary_substitution",
                  "案例共识别" not in a and not has_unresolved(a), a[:150])
    else:
        label = "Which policy requirements apply?"
        alt = "该发现适用哪些政策要求？"

        def judge(a):
            low_a = low(a)
            check("context_f6_kept",
                  "f6" in low_a or "linked account" in low_a, a[:150])
            pol = ("AML" in a.upper() or "polic" in low(a)
                   or "citation" in low(a))
            citation = ("[3]" in a or "section" in low(a)
                        or "applies" in low(a) or "原文" in a)
            check("policy_retrieval_based", pol and citation, a[:160])
            check("no_case_summary_substitution",
                  not has_unresolved(a)
                  and "case summary" not in low(a), a[:150])
    _journey_chip(b, tag, "U00010", "F6", label, judge,
                  fu_id="check_policy", label_alt=alt)


def journey_b5(b, lang):
    tag = f"B5_{lang}"
    if lang == "zh":
        label = "这个发现为什么被标记？"
        alt = "Why is this finding flagged?"

        def judge(a):
            la = low(a)
            check("explains_selected_finding",
                  ("cluster" in la or "集群" in a or "设备" in a), a[:150])
            check("cluster_count_present",
                  "19" in a, a[:150])
            scope = ("样例" in a or "非完整" in a or "已展示" in a
                     or "5 / 18" in a or "5/18" in a or "18" in a)
            check("preview_vs_total_visible_in_dom", scope, a[:150])
            check("no_unresolved", not has_unresolved(a), a[:120])
    else:
        label = "Why is this finding flagged?"
        alt = "这个发现为什么被标记？"

        def judge(a):
            check("explains_selected_finding",
                  ("cluster" in low(a) or "device" in low(a)), a[:150])
            check("cluster_count_present", "19" in a, a[:150])
            scope = ("sample" in low(a) or "preview" in low(a)
                     or "not the full" in low(a) or "18" in a)
            check("preview_vs_total_visible_in_dom", scope, a[:150])
            check("no_unresolved", not has_unresolved(a), a[:120])
    _journey_chip(b, tag, "U00010", "F6", label, judge,
                  fu_id="explain_finding", label_alt=alt)


def journey_b6(b, lang):
    tag = f"B6_{lang}"
    if lang == "zh":
        label = "查看相关时间线"
        alt = "Show related timeline"

        def judge(a):
            la = low(a)
            check("finding_scoped_timeline",
                  ("发现时间线" in a or "f6-e" in la
                   or ("时间线" in a
                       and ("f6" in la or "该发现" in a or "发现" in a)),
                   a[:180]))
            check("no_case_wide_substitution",
                  "案例共识别" not in a and "案例的时间线" not in a, a[:150])
            check("no_unresolved", not has_unresolved(a), a[:120])
    else:
        label = "Show related timeline"
        alt = "查看相关时间线"

        def judge(a):
            check("finding_scoped_timeline",
                  ("timeline" in low(a) and ("F6" in a or "finding" in
                                             low(a))), a[:180])
            check("no_case_wide_substitution",
                  "case identified" not in low(a)
                  and "case-wide" not in low(a), a[:150])
            check("no_unresolved", not has_unresolved(a), a[:120])
    _journey_chip(b, tag, "U00010", "F6", label, judge,
                  fu_id="show_timeline", label_alt=alt)


def journey_b7(b, lang):
    """R7 authorization: investigation WITHOUT file intent must not create
    an analysis artifact; an explicit file request must. Per-task
    correlation: each ask's task is identified via the goal filter and
    its OWN persisted artifacts are the delivery evidence — page badge
    counts are observation only, never the verdict."""
    _set(f"B7_{lang}")
    case = "U00029"
    new_session(b, case, lang)
    before = artifact_badge_count(b)
    meta(baseline_artifacts=before)
    if lang == "zh":
        ask_text = "查询关联账号中是否存在对冲交易，如果有，发交易明细给我"
        file_text = "请把对冲交易候选整理成一份明细文件发给我"
        ask_goal, file_goal = "对冲交易", "明细文件"
    else:
        ask_text = ("Check whether the linked accounts contain hedge "
                    "trades. If they exist, send me the transaction "
                    "details.")
        file_text = ("Please organize the hedge candidates into a detail "
                     "file for me.")
        ask_goal, file_goal = "hedge trades", "detail file"

    def _ask_correlated(text, goal_substr, exclude=None):
        before_cards = agent_cards(b)
        send_message(b, text)
        tid = wait_until(
            lambda: latest_task_since(CUR["start_iso"], goal_substr,
                                      exclude_ids=exclude),
            timeout=180, desc="task identified")
        meta(task=tid)
        if not tid:
            check("task_identified", False,
                  f"no task matched {goal_substr}")
            return None, ""
        status = wait_task_terminal(tid, timeout=900)
        check("task_terminal_" + goal_substr[:8],
              status in ("completed", "failed"), f"status={status}")
        api_text = wait_until(lambda: (lambda t: t if len(t or "") >= 100
                                       else None)(
            (httpx.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=30)
             .json().get("task_response_text") or "")),
            timeout=120, desc="composed response stored")
        prefix = _norm(api_text or "")[:100]
        answer = wait_until(lambda: _find_card(b, prefix) or None,
                            timeout=300, desc="answer card") or ""
        check("answer_matches_task_response_" + goal_substr[:8],
              prefix in _norm(answer), f"card len={len(answer)}")
        return tid, answer

    # --- A. unauthorized path: no file intent in the ask ---
    tid1, a1 = _ask_correlated(ask_text, ask_goal)
    a1_ids = persisted_artifacts(tid1) if tid1 else []
    check("unauthorized_task_no_artifact",
          bool(tid1) and len(a1_ids) == 0,
          str([(i, len(c)) for i, c in a1_ids]))
    check("investigation_still_answered",
          ("候选" in a1 or "candidate" in low(a1)) and not has_unresolved(a1),
          a1[:150])
    meta(no_file_answer_head=a1[:300])

    # --- B. authorized path: explicit file request ---
    tid2, a2 = _ask_correlated(file_text, file_goal, exclude=tid1)
    meta(file_task=tid2)
    check("tasks_distinct", bool(tid1 and tid2 and tid2 != tid1),
          f"tid1={tid1} tid2={tid2}")
    n_tabs = wait_until(lambda: (lambda n: n if n >= 2 else None)(
        artifact_badge_count(b)), timeout=240, desc="2 artifact tabs")         or artifact_badge_count(b)
    meta(artifact_count=n_tabs)
    rendered = False
    for i in range(1, max(n_tabs, 1) + 1):
        if n_tabs > 1:
            open_artifact_tab(b, i)
            time.sleep(1.2)
        content = active_panel_text(b)
        if "CATA-" in content and "U00" in content:
            rendered = True
    check("authorized_file_renders_candidates", rendered, f"tabs={n_tabs}")
    if tid2:
        arts = persisted_artifacts(tid2)
        ok = any("CATA-" in c and "U00" in c for _, c in arts)
        check("persisted_authorized_file_has_candidates", ok,
              str([(i, len(c), "CATA-" in c) for i, c in arts]))
    else:
        check("persisted_authorized_file_has_candidates", False,
              "no task id")
    b.screenshot(f"{SHOTS}/u00010-p2-b7-{lang}-file.png")


# --- runner ------------------------------------------------------------------

def _set(tag):
    CUR["journey"] = tag
    CUR["start_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    results["journeys"].setdefault(tag, {"checks": {}, "meta": {}})


def run_journey(fn, *args):
    _set(f"{fn.__name__}_{args[-1]}")
    started = time.strftime("%H:%M:%S")
    try:
        fn(*args)
    except Exception as e:
        check("journey_error", False, f"{type(e).__name__}: {e}"[:280])
        try:
            b.screenshot(f"{SHOTS}/u00010-p2-error-{CUR['journey']}.png")
        except Exception:
            pass
    print(f"--- {CUR['journey']} done (started {started})", flush=True)


def journey_lang_switch(b, lang_seq):
    """U00010 — same-session per-turn response language switch.

    ONE investigation session; the turns in lang_seq run CONSECUTIVELY in
    the SAME browser conversation. Per turn: goal-filtered task id
    (excluding prior turns), task-terminal gate, task_response_text
    correlation with the DOM answer card, composer-language marker, and
    section-scoped body language."""
    _set("lang_switch_" + "_".join(x[0] for x in lang_seq))
    new_session(b, "U00010", lang_seq[0])
    prior: list = []
    for i, (name, expect, ask_text) in enumerate(lang_seq, 1):
        before = len(prior)
        send_message(b, ask_text)
        tid = wait_until(
            lambda: latest_task_since(CUR["start_iso"], goal_substr_for(expect),
                                      exclude_ids=list(prior)),
            timeout=180, desc=f"{name} task identified")
        meta(turn=name, task=tid, expect=expect)
        check(f"{name}_task_created", tid is not None, f"tid={tid}")
        if tid is None:
            continue
        status = wait_task_terminal(tid, timeout=900)
        check(f"{name}_task_terminal", status in ("completed", "failed"),
              f"status={status}")
        api_text = wait_until(lambda: (lambda t: t if len(t or "") >= 100
                                       else None)(persisted_response(tid)),
                              timeout=120,
                              desc="composed response stored") or ""
        check(f"{name}_response_persisted", len(api_text) >= 100,
              f"len={len(api_text)}")
        prefix = _norm(api_text)[:100]
        answer = wait_until(lambda: _find_card(b, prefix) or None,
                            timeout=300, desc="answer card") or ""
        check(f"{name}_answer_matches_task_response",
              prefix in _norm(answer), f"card len={len(answer)}")
        comp = composer_marker(answer)
        blang = body_language(answer)
        meta(composer=comp, body=blang, zh_chars=len(re.findall(
            r"[一-龿]", answer)))
        check(f"{name}_answer_language", blang == expect,
              f"body={blang} expect={expect}")
        check(f"{name}_composer_lang", comp == expect,
              f"composer={comp} expect={expect}")
    b.screenshot(f"{SHOTS}/u00010-sslang-final.png")


def _runtime_gate():
    """Runtime Verification Gate (CLAUDE.md, mandatory for acceptance
    runs): BOTH listeners must verify against the current tree and the
    declared target feature must be enabled BEFORE any browser or target
    request is issued. Non-zero → the acceptance is BLOCKED (record path
    printed); the browser is never started."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rec_dir = ("/Users/vv/investigation-agent-client/docs/evaluations")
    pf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "preflight_runtime.py")
    checks = [
        ("backend", ["--port", "8001", "--service", "backend",
                     "--watch", "backend/app", "--reload-model", "restart",
                     "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true",
                     "--target-feature", "goal_contract",
                     "--probe-url",
                     "http://localhost:8001/api/v2/runtime/identity",
                     "--probe-contains", "investigation"]),
        ("frontend", ["--port", "3001", "--service", "frontend",
                      "--watch", "frontend/src", "--reload-model", "hot",
                      "--probe-url",
                      "http://localhost:3001/src/api/investigation.ts",
                      "--probe-contains", "followUpId"]),
    ]
    for label, args in checks:
        record = os.path.join(rec_dir, f"gate-{stamp}-{label}.json")
        r = subprocess.run(
            [sys.executable, pf] + args + ["--record", record],
            cwd="/Users/vv/investigation-agent-client")
        if r.returncode != 0:
            print(f"GATE BLOCKED ({label}, exit {r.returncode}) — "
                  f"record: {record}")
            return None
    print(f"GATE PASS — records: {rec_dir}/gate-{stamp}-*.json")
    return stamp


def main():
    if _runtime_gate() is None:
        return 2                    # BLOCKED — no browser, no requests
    only = set(sys.argv[1:]) or None   # e.g. "B4 B5 B6" or "B4_zh"
    br = Browser()
    try:
        br.start(FRONTEND)
        results["meta"]["started"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        def wanted(tag):
            if not only:
                return True
            only_l = {o.lower() for o in only}
            short = tag.replace("journey_", "").lower()  # → b1_zh
            base = short.split("_")[0]                    # b1
            return short in only_l or base in only_l
        for lang in ("zh", "en"):
            for fn in (journey_b4, journey_b5, journey_b6):
                if wanted(f"{fn.__name__}_{lang}"):
                    run_journey(fn, br, lang)
        for lang in ("zh", "en"):
            for fn in (journey_b2, journey_b1, journey_b3, journey_b7):
                if wanted(f"{fn.__name__}_{lang}"):
                    run_journey(fn, br, lang)
        # same-session language switch: EN→ZH→EN in ONE conversation
        lang_switch_seq = [
            ("turn1", "en",
             "Explain the evidence for F7 Trading Frequency (24h) in case "
             "U00010. Distinguish the observed trading activity from any "
             "inferred risk conclusion."),
            ("turn2", "zh",
             "请用中文解释同一案例中 F7 交易频率的现有证据，并区分已经观察"
             "到的交易活动与尚未被证实的风险结论。"),
            ("turn3", "en",
             "Now summarize the remaining evidence gaps for F7 in English, "
             "without treating missing evidence as a confirmed fact."),
        ]
        journey_lang_switch(br, lang_switch_seq)
        results["PASS"] = all(
            c["ok"] for j in results["journeys"].values()
            for c in j["checks"].values())
    except Exception as e:
        results["error"] = f"{type(e).__name__}: {e}"[:300]
        results["PASS"] = False
        try:
            br.screenshot(f"{SHOTS}/u00010-p2-fatal.png")
        except Exception:
            pass
    finally:
        try:
            br.close()
        except Exception:
            pass
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
            f"u00010-phase2-browser-{stamp}.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("results →", path)
    print("PASS" if results.get("PASS") else "FAIL")
    return 0 if results.get("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())

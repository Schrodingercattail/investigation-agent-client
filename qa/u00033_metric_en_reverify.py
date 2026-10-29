"""U00033 real-browser re-verification: EN metric-facet answer.

Reproduces the live incident ask in a REAL browser session against the
REAL stack (:3001 → :8001 → RP :8000). After the fix, the en turn must
render the account-own opposite-trade-ratio explanation in ENGLISH in
the UI card of THIS turn's task — the zh verdict detail
(是账户自身的反向交易比例…) must not leak into the visible answer.

Evidence: docs/evaluations/u00033-metric-en-reverify-<stamp>.json
          qa/screenshots/u00033-metric-en-*.png
"""
import json
import re
import sys
import time

import httpx

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                      # noqa: E402
from journeys_common import send_message, wait_for  # noqa: E402

BACKEND = "http://localhost:8001"
FRONTEND = "http://localhost:3001/"
SHOTS = "/Users/vv/investigation-agent-client/qa/screenshots"
ZH_DETAIL = "是账户自身的反向交易比例"
EN_MARKER = "OWN opposite-trade ratio"
ASK = ("explain how to calculate the opposite-trade ratio of 33.33%. "
       "Is it an opposite-trade ratio within different accounts?")
GOAL_SUBSTR = "opposite-trade ratio"


def out(checks, **kw):
    return {"checks": checks, **kw}


def main():
    res = {"journey": "u00033-metric-en-reverify", "checks": {}, "meta": {}}
    ok_all = True

    def check(name, ok, detail=""):
        nonlocal ok_all
        res["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
        if not ok:
            ok_all = False
        print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:120])

    br = Browser()
    try:
        br.start(FRONTEND)
        res["meta"]["started"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime())
        # --- real intake (new investigation, EN entry) ---
        wait_for(br, "!!document.querySelector('form input')", 120,
                 desc="app mounted")
        br.eval("""(() => { const t = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('New Investigation'));
            t && t.click(); })()""")
        time.sleep(1.2)
        send_message(br, "Investigate U00033")
        wait_for(br, """(() => {
            const blocks = document.querySelectorAll(
                '[data-testid="agent-activity-block"]');
            if (!blocks.length) return false;
            const s = blocks[blocks.length - 1].querySelector(
                '[data-testid="activity-block-status"]');
            return s && s.getAttribute('data-tone') !== 'running';
        })()""", 600, desc="intake terminal")
        # --- the EXACT incident ask, same real conversation ---
        start_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        send_message(br, ASK)

        # task identity via persisted goal (intake goal cannot match)
        import sqlite3
        tid = None
        deadline = time.time() + 180
        db = sqlite3.connect(
            "/Users/vv/investigation-agent-client/backend/data/"
            "investigation_agent_v2.db")
        while time.time() < deadline and not tid:
            rows = db.execute(
                "SELECT task_id, investigation_id FROM tasks_v2 "
                "WHERE started_at > ? ORDER BY started_at DESC LIMIT 20",
                (start_iso,)).fetchall()
            for t, inv in rows:
                doc = db.execute("SELECT document FROM agent_states "
                                 "WHERE task_id=?", (t,)).fetchone()
                if doc and GOAL_SUBSTR in (json.loads(doc[0]).get("goal")
                                           or ""):
                    tid, inv_id = t, inv
                    break
            time.sleep(5)
        res["meta"]["task"] = tid
        res["meta"]["investigation"] = inv_id if tid else None
        check("task_identified", bool(tid), f"tid={tid} inv={inv_id if tid else '?'}")
        if not tid:
            raise RuntimeError("task not identified")
        check("goal_matches_incident_ask", True,
              json.loads(db.execute("SELECT document FROM agent_states "
                                    "WHERE task_id=?", (tid,)).fetchone()[0]
                         ).get("goal", "")[:120])

        # bounded API terminal gate (primary)
        status = None
        deadline = time.time() + 900
        while time.time() < deadline:
            d = httpx.get(f"{BACKEND}/api/v2/tasks/{tid}",
                          timeout=30).json()
            status = (d.get("task") or {}).get("status")
            if status in ("completed", "failed"):
                break
            time.sleep(5)
        res["meta"]["status"] = status
        check("task_terminal_completed", status == "completed",
              f"status={status}")

        # persisted composed response (the Agent's OWN answer body)
        api_text = ""
        deadline = time.time() + 120
        while time.time() < deadline:
            api_text = (httpx.get(f"{BACKEND}/api/v2/tasks/{tid}",
                                  timeout=30).json()
                        .get("task_response_text") or "")
            if len(api_text) >= 100:
                break
            time.sleep(5)
        res["meta"]["response_len"] = len(api_text)
        zh_hits = re.findall(r"[一-鿿]+", api_text)
        check("answer_body_is_english", len(api_text) >= 100 and not zh_hits,
              f"len={len(api_text)} zh_hits={zh_hits[:5]}")
        check("en_metric_explanation_present",
              EN_MARKER in api_text and "opposite_trade_ratio" in api_text,
              api_text[:120])
        check("semantic_own_account_not_cross_account",
              "NOT a cross-account match rate" in api_text
              and "min(BUY count, SELL count)" in api_text,
              [s for s in api_text.split(".") if "cross-account" in s][:1])
        check("provenance_source_present",
              "Source: Risk Platform feature_evidence." in api_text,
              api_text[-160:])
        ratio = re.search(r"(\d+\.\d{2})%", api_text)
        res["meta"]["stated_ratio"] = ratio.group(0) if ratio else None
        check("zh_detail_not_leaked", ZH_DETAIL not in api_text,
              f"zh_detail_hits={ZH_DETAIL in api_text}")

        # the UI card of THIS turn's task (data-task-id correlation)
        sel = f'[data-task-id="{tid}"]'
        found = None
        deadline = time.time() + 300
        prev = ""
        stable = 0
        while time.time() < deadline:
            cur = br.eval(
                "(() => { const b = document.querySelector("
                + json.dumps(sel) + "); if (!b) return '';"
                " const c = b.closest('.flex.justify-start');"
                " return c ? c.innerText : ''; })()")
            if cur and cur == prev and len(cur) > 150:
                stable += 1
                if stable >= 2:
                    found = cur
                    break
            else:
                stable = 0
            prev = cur
            time.sleep(4)
        res["meta"]["card_len"] = len(found or "")
        prefix = " ".join(api_text.split())[:80]
        norm = " ".join((found or "").split())
        check("dom_card_belongs_to_this_task", bool(found)
              and prefix in norm, f"prefix_found={prefix in norm}")
        check("dom_card_shows_english_answer",
              bool(found) and EN_MARKER in norm and ZH_DETAIL not in norm,
              f"en_in_card={EN_MARKER in norm} zh_in_card={ZH_DETAIL in norm}")
        br.screenshot(f"{SHOTS}/u00033-metric-en-answer.png")
        res["meta"]["answer_head"] = api_text[:300]
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"[:300]
        ok_all = False
        try:
            br.screenshot(f"{SHOTS}/u00033-metric-en-error.png")
        except Exception:
            pass
    finally:
        try:
            br.close()
        except Exception:
            pass
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = ("/Users/vv/investigation-agent-client/docs/evaluations/"
            f"u00033-metric-en-reverify-{stamp}.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print("results →", path)
    print("PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())

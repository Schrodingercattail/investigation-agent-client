"""U00010 same-session EN→ZH→EN — READ-ONLY turn-3 measurement completion.

Context: qa/u00010_phase2_browser.py lang_switch (run 2026-10-23T1528Z)
executed all THREE real sends in ONE session (INV-b2ee4c30ca95) but its
turn-3 gate raced (prior-exclusion harness defect) and exited before
correlating turn-3's rendered card. Turn-3's REAL task
TASK-be848aff48b4 completed in the SAME session after exit.

This probe does NOT send anything and does NOT rerun the journey. It
reopens the SAME persisted investigation in the real browser (the exact
reload view a user sees), correlates the turn blocks by data-task-id,
and captures the RENDERED card + body language for all three turns.
Evidence: docs/evaluations/u00010-sslang-turn3-capture-<stamp>.json
"""
import json
import re
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser                     # noqa: E402

FRONTEND = "http://localhost:3001/"
INV = "INV-b2ee4c30ca95"
TASKS = [
    ("turn1", "en", "TASK-47b1d01d2da5"),
    ("turn2", "zh", "TASK-4bf993b4db68"),
    ("turn3", "en", "TASK-be848aff48b4"),
]
SHOTS = "/Users/vv/investigation-agent-client/qa/screenshots"


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
    # zh composer echo observed live: 「关于发现 F7「…」」 — accept the
    # quote-intro family, not one exact surface form
    if 'About "' in text:
        return "en"
    if "关于「" in text or "关于发现" in text:
        return "zh"
    return None


def card_of(b, tid, timeout=90):
    """Rendered innerText of the answer card whose activity block carries
    data-task-id=tid; waits until it stabilizes across 2 polls."""
    sel = f'[data-task-id="{tid}"]'
    deadline = time.time() + timeout
    prev = ""
    stable = 0
    while time.time() < deadline:
        cur = b.eval(
            "(() => { const block = document.querySelector("
            + json.dumps(sel) + "); if (!block) return '';"
            " const card = block.closest('.flex.justify-start');"
            " return card ? card.innerText : ''; })()")
        if cur and cur == prev and len(cur) > 150:
            stable += 1
            if stable >= 2:
                return cur
        else:
            stable = 0
        prev = cur
        time.sleep(4)
    return prev


def main():
    out = {"investigation": INV, "turns": {}, "notes": []}
    br = Browser()
    code = 0
    try:
        br.start(FRONTEND)
        # pre-seed navigation state so the app restores THIS investigation
        br.eval(f'(() => {{ localStorage.setItem("investigation.currentId",'
                f' {json.dumps(INV)}); return "seeded"; }})()')
        br.navigate(FRONTEND, settle=3)
        seeded = json.loads(br.eval(
            '(() => JSON.stringify('
            'localStorage.getItem("investigation.currentId") || ""))()'))
        out["notes"].append(f"restored_id={seeded}")
        if seeded != INV:
            raise RuntimeError(f"session restore failed: {seeded!r}")
        deadline = time.time() + 120
        while time.time() < deadline:
            if b_cards(b=br):
                break
            time.sleep(4)
        out["notes"].append(f"cards={b_cards(b=br)}")
        for name, expect, tid in TASKS:
            card = card_of(br, tid)
            blang = body_language(card)
            comp = composer_marker(card)
            out["turns"][name] = {
                "task_id": tid, "expect": expect, "body_language": blang,
                "composer": comp, "card_len": len(card or ""),
                "card_head": (card or "")[:220],
                "ok": blang == expect and bool(card),
            }
            br.screenshot(f"{SHOTS}/u00010-sslang-{name}-{tid[-6:]}.png")
        out["PASS"] = all(t["ok"] for t in out["turns"].values())
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:300]
        out["PASS"] = False
    finally:
        try:
            br.close()
        except Exception:
            pass
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = (f"/Users/vv/investigation-agent-client/docs/evaluations/"
            f"u00010-sslang-turn3-capture-{stamp}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("results →", path)
    print("PASS" if out.get("PASS") else "FAIL")
    return 0 if out.get("PASS") else 1


def b_cards(b):
    return int(b.eval(
        "document.querySelectorAll('.flex.justify-start').length") or 0)


if __name__ == "__main__":
    sys.exit(main())

"""Artifact-consistency check as a fresh lightweight browser session.

Opens the most recent U00005 investigation's Artifacts panel, selects the
goal-run bundle (last #N tab), and asserts the SAME facts the answer
stated: Signal Explanation section, the platform's verbatim rule-status
statement, authoritative-zero NO_MATCH wording, no contradicting
"insufficient" claim.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/vv/investigation-agent-client/qa")

from browser import Browser
from journeys_common import SHOTS

results = {}
b = Browser()
try:
    b.start("http://localhost:3001/")
    time.sleep(3.0)
    # open the most recent investigation (top of History)
    b.eval("""(() => { const items = Array.prototype.slice.call(
        document.querySelectorAll('button, [role=button], .cursor-pointer'));
    for (const el of items) {
        if ((el.innerText || '').indexOf('Investigation · U00005') === 0) {
            el.click(); break; } } })()""")
    time.sleep(2.0)
    b.eval("""(() => { const t = Array.prototype.slice.call(
        document.querySelectorAll('button, [role=tab]'));
    for (const x of t) {
        if (/Artifacts/.test(x.textContent || '')) { x.click(); break; } } })()""")
    time.sleep(1.5)
    # select the LAST artifact tab (#N) — the goal run's bundle
    b.eval("""(() => {
        const tabs = Array.prototype.slice.call(
            document.querySelectorAll('button'));
        let last = null;
        for (const t of tabs) {
            if (/^#\\d+$/.test((t.textContent || '').trim())) last = t;
        }
        if (last) last.click();
    })()""")
    time.sleep(1.5)
    art = b.eval("document.body.innerText")
    results["artifact_is_goal_bundle"] = (
        "Cross-Account Trade Evidence" in art)
    a = {}
    a["artifact_signal_explanation"] = "Signal Explanation" in art
    a["artifact_feature_observation"] = (
        "opposite_trade_ratio=0.3412" in art)
    a["artifact_rule_status_verbatim"] = (
        "below the 40% threshold" in art and "not triggered" in art)
    a["artifact_authoritative_zero"] = (
        "authoritative zero" in art)
    a["artifact_no_insufficient_contradiction"] = (
        "insufficient for a reliable conclusion" not in art)
    results["assertions"] = a
    results["PASS"] = all(a.values())
    b.screenshot(f"{SHOTS}/rule-explain-u00005-artifact.png")
except Exception as e:
    results["error"] = f"{type(e).__name__}: {e}"[:300]
    try:
        b.screenshot(f"{SHOTS}/rule-explain-u00005-artifact-error.png")
    except Exception:
        pass
finally:
    with open("/tmp/rule_explain_artifact_check.json", "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))

"""Aggregate + render evaluation results. All percentages are computed
from actual scenario/checker results — nothing fabricated."""


def aggregate(results):
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    # task-status vs semantic-success mismatch analysis:
    #   task completed but semantic FAIL  -> "false success"
    #   task failed    but semantic PASS  -> "correct bounded failure"
    false_successes, bounded_failures = [], []
    for r in results:
        if r.capture is None:
            continue
        completed = all(t.task_status == "completed" for t in r.capture.turns)
        if completed and not r.passed:
            false_successes.append(r.scenario_id)
        if (not completed) and r.passed:
            bounded_failures.append(r.scenario_id)
    by_category = {}
    for r in results:
        c = by_category.setdefault(r.category, {"total": 0, "passed": 0})
        c["total"] += 1
        c["passed"] += 1 if r.passed else 0
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "semantic_success_rate":
            (passed / total) if total else 0.0,
        "task_completed_vs_semantic_mismatch": {
            "false_successes (task completed, semantic FAIL)": false_successes,
            "correct_bounded_failures (task failed, semantic PASS)":
                bounded_failures,
        },
        "by_category": by_category,
    }


def render(results, mode):
    agg = aggregate(results)
    lines = []
    lines.append(f"=== Week 1 Agent Evaluation — mode: {mode} ===")
    if mode == "live":
        lines.append("(LIVE: real configured Risk Platform + LLM. External "
                     "service/network failures are reported as "
                     "infrastructure errors, never as semantic success.)")
    lines.append(f"scenarios: {agg['total']}  passed: {agg['passed']}  "
                 f"failed: {agg['failed']}")
    rate = agg["semantic_success_rate"]
    lines.append(f"semantic success rate: {rate:.0%} "
                 f"({agg['passed']}/{agg['total']})")
    mm = agg["task_completed_vs_semantic_mismatch"]
    lines.append(f"task-status vs semantic-success mismatches: "
                 f"false successes={len(mm['false_successes (task completed, semantic FAIL)'])}, "
                 f"correct bounded failures={len(mm['correct_bounded_failures (task failed, semantic PASS)'])}")
    lines.append("")
    lines.append("by category:")
    for cat, c in sorted(agg["by_category"].items()):
        pct = (c["passed"] / c["total"]) if c["total"] else 0.0
        lines.append(f"  {cat:12} {c['passed']}/{c['total']} ({pct:.0%})")
    failures = [r for r in results if not r.passed]
    if failures:
        lines.append("")
        lines.append("failures:")
        for r in failures:
            if r.error:
                lines.append(f"  {r.scenario_id}: ERROR {r.error}")
                continue
            for c in r.check_results:
                if not c.passed:
                    lines.append(f"  {r.scenario_id} [{c.name}] {c.detail}")
    lines.append("")
    lines.append("scenario detail:")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        extra = f"  explanation_source={r.explanation_source}" \
            if r.explanation_source else ""
        lines.append(f"  [{status}] {r.scenario_id} ({r.category}, "
                     f"{r.mode}){extra}")
    return "\n".join(lines)

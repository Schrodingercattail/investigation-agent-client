"""AL-3 report generator — Markdown + JSON."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from eval.result_model import EvaluationRun


def generate_markdown(run: EvaluationRun) -> str:
    s = run.summary()
    lines = [
        f"# AL-3 Evaluation Report — {run.run_id}",
        f"**Timestamp**: {run.timestamp}",
        f"**Git commit**: {run.git_commit}",
        f"**Model**: {run.model_name}",
        f"**Scenario version**: {run.scenario_version}",
        "",
        "## Summary",
        f"- Total: {s['total_scenarios']}",
    ]
    for status, ids in s.get("scenario_ids_by_status", {}).items():
        lines.append(f"- {status}: {len(ids)} — {', '.join(sorted(ids))}")
    if s.get("failure_taxonomy"):
        lines.append("\n### Failure Taxonomy")
        for code, count in sorted(s["failure_taxonomy"].items(),
                                   key=lambda x: -x[1]):
            lines.append(f"- {code}: {count}")

    lines.append("\n## Per-Scenario Results\n")
    lines.append("| Scenario | Status | Gates | Judge Avg | Iter | Stop | Taxonomy |")
    lines.append("|---|---|---|---|---|---|---|")
    for sid in sorted(run.results):
        r = run.results[sid]
        gates_ok = all(c.passed for c in r.hard_gate_results)
        gate_str = "✓" if gates_ok else "✗"
        avg = r.avg_judge_score
        judge_str = f"{avg:.1f}" if avg is not None else "—"
        iter_str = str(r.trace.iteration_count)
        stop_str = (r.trace.stop_reason or "")[:20]
        tax = r.failure_taxonomy or ""
        lines.append(f"| {sid} | {r.status.value} | {gate_str} | "
                     f"{judge_str} | {iter_str} | {stop_str} | {tax} |")

    lines.append("\n## Failed Gate Details\n")
    for sid in sorted(run.results):
        r = run.results[sid]
        failures = [c for c in r.hard_gate_results if not c.passed]
        if failures:
            lines.append(f"### {sid}")
            for f in failures:
                lines.append(f"- **{f.check_id}**: expected={f.expected}, "
                             f"observed={f.observed}")

    if run.baseline_ref:
        lines.append(f"\n**Baseline**: {run.baseline_ref}")

    return "\n".join(lines)


def save_report(run: EvaluationRun, output_dir: str) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = run.timestamp[:19].replace(":", "").replace("-", "")

    json_path = out / f"al3-eval-{ts}.json"
    json_path.write_text(json.dumps(run.to_dict(), indent=2,
                                    ensure_ascii=False, default=str))
    md_path = out / f"al3-eval-{ts}.md"
    md_path.write_text(generate_markdown(run), encoding="utf-8")
    return json_path, md_path

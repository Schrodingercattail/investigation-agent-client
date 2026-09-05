"""Planner Evaluation report + CLI.

    python -m eval.run_planner_eval
    python -m eval.run_planner_eval --runs 1
    python -m eval.run_planner_eval --scenario P08

Invokes the REAL PlannerV2 with the REAL configured LLM. Reports
scenario-level results, run-level metrics, and failure categories —
never aggregating repeated runs as independent scenarios.
"""

import argparse
import sys

from eval.planner_runner import run_planner_evaluation


def render(summaries, results, runs_per_scenario):
    total_runs = len(results)
    passed_runs = sum(1 for r in results if r.passed)
    perfect = sum(1 for s in summaries if s["passed"])
    lines = []
    lines.append("Planner Evaluation")
    lines.append("=" * 19)
    lines.append(f"Runs per scenario: {runs_per_scenario}")
    lines.append(f"Scenarios: {len(summaries)}")
    lines.append(f"Total runs: {total_runs}")
    lines.append("")
    lines.append("Scenario Results")
    lines.append("----------------")
    for s in summaries:
        status = "PASS" if s["passed"] else "FAIL"
        lines.append(f"{s['scenario_id']} "
                     f"{s['expected_operation']:<28} "
                     f"{s['passed_runs']}/{s['runs']} {status}")
    lines.append("")
    lines.append("Run-level semantic pass rate:")
    rate = (passed_runs / total_runs * 100) if total_runs else 0.0
    lines.append(f"{passed_runs}/{total_runs} = {rate:.1f}%")
    lines.append("")
    stable = sum(1 for s in summaries if s["stability"] == 1.0)
    lines.append("Scenario-level perfect stability:")
    lines.append(f"{stable}/{len(summaries)} = "
                 f"{(stable / len(summaries) * 100) if summaries else 0:.0f}%")
    failures = [s for s in summaries if not s["passed"]]
    if failures:
        lines.append("")
        lines.append("Failures")
        lines.append("--------")
        for s in failures:
            lines.append(f"{s['scenario_id']} "
                         f"({s['expected_operation']}):")
            lines.append(f"  {s['passed_runs']}/{s['runs']} runs correct")
            for cat, n in sorted(s["categories"].items(),
                                 key=lambda kv: -kv[1]):
                if cat != "CORRECT":
                    lines.append(f"  {n}x {cat}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Week 1 Planner Evaluation (real LLM → plan selection)")
    parser.add_argument("--runs", type=int, default=3,
                        help="runs per scenario (default 3)")
    parser.add_argument("--scenario", action="append", default=None,
                        help="limit to scenario id(s), e.g. --scenario P08")
    args = parser.parse_args(argv)

    summaries, results = run_planner_evaluation(
        runs_per_scenario=args.runs, only_ids=args.scenario)
    print(render(summaries, results, args.runs))
    all_pass = all(s["passed"] for s in summaries)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())

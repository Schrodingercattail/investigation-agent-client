"""Week 1 Agent Evaluation runner.

    python -m eval.run_eval --mode scripted
    python -m eval.run_eval --mode live
    python -m eval.run_eval --mode scripted --category evidence

--mode scripted (default): deterministic — real resolver/executor/tools/
composer with a scripted planner LLM and stubbed RP.

--mode live: real configured Risk Platform + LLM. Live mode clearly
identifies itself as live; external service/network failures are reported
as infrastructure errors and never converted into semantic success.
"""

import argparse
import sys

from eval.report import render
from eval.scenarios import build_live_scenarios, build_scenarios


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Week 1 Investigation Agent semantic evaluation")
    parser.add_argument("--mode", choices=("scripted", "live", "all"),
                        default="scripted")
    parser.add_argument("--category", default=None,
                        help="filter by scenario category (e.g. evidence)")
    parser.add_argument("--scenario", default=None,
                        help="run a single scenario by id (e.g. E07)")
    args = parser.parse_args(argv)

    scenarios = []
    if args.mode in ("scripted", "all"):
        scenarios.extend(build_scenarios())
    if args.mode in ("live", "all"):
        scenarios.extend(build_live_scenarios())
    if args.category:
        scenarios = [s for s in scenarios if s.category == args.category]
    if args.scenario:
        scenarios = [s for s in scenarios
                     if s.scenario_id == args.scenario]
    if not scenarios:
        print("no scenarios matched", file=sys.stderr)
        return 2

    from eval.runner import run_all
    results = run_all(scenarios)
    mode_label = ("live" if args.mode == "live"
                  else "all (scripted + live)" if args.mode == "all"
                  else "scripted")
    print(render(results, mode_label))
    # scripted failures fail the process; live failures are reported but
    # do not gate (external services are outside this repository's control)
    scripted_failed = [r for r in results
                       if r.mode == "scripted" and not r.passed]
    return 1 if scripted_failed else 0


if __name__ == "__main__":
    sys.exit(main())

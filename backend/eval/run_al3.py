"""AL-3 entry point — run evaluation scenarios against canonical runtime."""
from __future__ import annotations
import argparse
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_git_commit() -> str:
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return "unknown"


def run_evaluation(
    backend_url: str = "http://localhost:8001",
    scenario_ids: list[str] | None = None,
    use_llm_judge: bool = True,
    output_dir: str = "../docs/evaluations",
    max_scenarios: int | None = None,
) -> dict:
    from eval.scenarios_al3 import get_scenarios
    from eval.runner_al3 import AL3Runner
    from eval.result_model import EvaluationRun
    from eval.report_al3 import save_report

    scenarios = get_scenarios()
    if scenario_ids:
        targets = [scenarios[sid] for sid in scenario_ids
                   if sid in scenarios]
    else:
        targets = list(scenarios.values())

    llm = None
    if use_llm_judge:
        try:
            from app.llm_provider import ClaudeProvider
            llm = ClaudeProvider()
        except Exception:
            logger.warning("LLM not available; judge disabled")

    runner = AL3Runner(backend_url=backend_url, llm=llm)

    run = EvaluationRun(
        run_id=f"al3-{uuid.uuid4().hex[:8]}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_commit=get_git_commit(),
        model_name=__import__("app.config", fromlist=["settings"]
                              ).settings.ANTHROPIC_MODEL,
        scenario_version="S01-S18.v1",
        rp_data_hash=hashlib.sha256(
            json.dumps({"case": "U00010"}).encode()).hexdigest()[:12],
        baseline_ref=None,
    )

    results = runner.run_all(targets, use_llm_judge=use_llm_judge,
                             max_scenarios=max_scenarios)
    run.results = results

    json_path, md_path = save_report(run, output_dir)
    logger.info("Report saved: %s + %s", json_path, md_path)
    s = run.summary()
    logger.info("Summary: %s", json.dumps(s["by_status"]))
    return run.to_dict()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AL-3 evaluation runner")
    parser.add_argument("--backend", default="http://localhost:8001")
    parser.add_argument("--scenarios", nargs="*", help="scenario IDs to run")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--output", default="../docs/evaluations")
    parser.add_argument("--max", type=int, default=None)
    args = parser.parse_args()
    result = run_evaluation(
        backend_url=args.backend,
        scenario_ids=args.scenarios,
        use_llm_judge=not args.no_judge,
        output_dir=args.output,
        max_scenarios=args.max,
    )
    print(json.dumps(result["summary"], indent=2))

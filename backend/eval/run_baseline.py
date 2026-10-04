"""AL-3.3 Full Baseline Evaluation Run."""
import json, hashlib, logging, os, time, uuid, re
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import httpx

from eval.scenarios_al3 import get_all
from eval.result_model import (
    EvaluationRun, ScenarioResult, ScenarioStatus,
    TraceSummary, CheckResult, JudgeScore,
)
from eval.evaluator import evaluate_hard_gates, classify_failure

BACKEND = "http://localhost:8001"
client = httpx.Client(timeout=120)


def _goal(iid, goal):
    r = client.post(f"{BACKEND}/api/v2/investigations/{iid}/goal",
                    json={"goal": goal})
    tid = r.json()["task_id"]
    for _ in range(120):
        a = client.get(f"{BACKEND}/api/v2/tasks/{tid}/activity",
                       timeout=15).json()
        if a.get("is_terminal"):
            break
        time.sleep(3)
    d = client.get(f"{BACKEND}/api/v2/tasks/{tid}", timeout=15).json()
    return a, d.get("task_response_text") or ""


def _turn(iid, msg):
    r = client.post(f"{BACKEND}/api/v2/investigations/{iid}/turn",
                    json={"message": msg})
    return r.json()


def main():
    run_id = f"al3-baseline-{uuid.uuid4().hex[:8]}"
    run_ts = datetime.now(timezone.utc).isoformat()
    import subprocess
    git_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    from app.config import settings
    model = settings.ANTHROPIC_MODEL

    results = {}
    scenarios = get_all()

    for sid in sorted(scenarios):
        s = scenarios[sid]
        gt = s.ground_truth
        mode = s.execution_mode
        logger.info("Running %s [%s] mode=%s", sid, s.category, mode)

        if mode not in ("turn", "loop", "no_key_loop", "deterministic"):
            results[sid] = ScenarioResult(
                scenario_id=sid, status=ScenarioStatus.ERROR,
                failure_detail=f"invalid execution_mode: {mode!r}")
            continue

        if mode == "deterministic":
            results[sid] = ScenarioResult(
                scenario_id=sid, status=ScenarioStatus.SKIPPED,
                failure_detail="Deterministic scenario: requires fixture harness")
            continue

        if mode == "no_key_loop":
            results[sid] = ScenarioResult(
                scenario_id=sid, status=ScenarioStatus.SKIPPED,
                failure_detail="No-key scenario: requires no-key backend")
            continue

        try:
            iid = client.post(
                f"{BACKEND}/api/v2/investigations",
                json={"case_id": "U00010"}
            ).json()["investigation"]["investigation_id"]

            prompts = (s.user_prompt
                       if isinstance(s.user_prompt, list)
                       else [s.user_prompt])
            response_text = ""
            focus = None
            agent_status = None
            stop_reason = None
            iteration = 0
            tool_calls = []
            plan_steps = []

            # AL-3.3 fix: respect execution_mode — turn scenarios use
            # /turn for ALL prompts; loop scenarios use /goal for the
            # last prompt only.
            for i, prompt in enumerate(prompts):
                is_last = (i == len(prompts) - 1)
                if mode == "turn" or not is_last:
                    r = _turn(iid, prompt)
                    if is_last:
                        response_text = r.get("response") or ""
                        focus = (r.get("context") or {}).get(
                            "focused_finding_id")
                        agent_status = r.get("task", {}).get("status")
                        iteration = 1
                        if r.get("plan"):
                            plan_steps = [
                                s["type"] for s in r["plan"].get("steps", [])]
                        if r.get("execution"):
                            for tc in (r["execution"].get("tool_calls") or []):
                                tool_calls.append({
                                    "name": tc.get("tool_name"),
                                    "outcome": tc.get("outcome")})
                else:
                    # loop mode: last prompt via /goal
                    a, resp = _goal(iid, prompt)
                    response_text = resp
                    agent_status = a.get("agent_status")
                    stop_reason = a.get("stop_reason")
                    iteration = a.get("iteration") or 0
                    # AL-3.3: focus source of truth for loop scenarios is
                    # AgentLoopState (Activity projection), NOT the
                    # InvestigationContext (which the loop does not write).
                    focus = a.get("focused_finding_id")
                    for p in (a.get("plan") or []):
                        if p.get("status") == "completed":
                            plan_steps.append(p.get("type", ""))
                    for it in (a.get("activity") or []):
                        if it.get("tool_name"):
                            tool_calls.append({
                                "name": it["tool_name"],
                                "outcome": "success"})

            try:
                inv = client.get(
                    f"{BACKEND}/api/v2/investigations/{iid}",
                    timeout=15).json()
                focus = (inv.get("context") or {}).get("focused_finding_id")
            except Exception:
                pass

            trace = TraceSummary(
                tool_calls=tool_calls, plan_steps=plan_steps,
                iteration_count=iteration, stop_reason=stop_reason,
                agent_status=agent_status, response_text=response_text,
                investigation_id=iid, focused_finding_id=focus,
            )

            gates = evaluate_hard_gates(gt, trace)
            taxonomy = (classify_failure(gates)
                        if not all(c.passed for c in gates) else None)

            judge_scores = []
            judge_available = False
            if gt.expected_key_facts:
                try:
                    from eval.llm_judge import judge_response
                    from app.llm_provider import ClaudeProvider
                    llm = ClaudeProvider()
                    gt_sum = {
                        "expected_key_facts": gt.expected_key_facts,
                        "prohibited_content": gt.prohibited_content,
                        "expected_answer_elements":
                            gt.expected_answer_elements,
                    }
                    q = prompts[-1] if isinstance(prompts, list) else prompts
                    raw, jAvail = judge_response(
                        llm, q, response_text[:3000], gt_sum)
                    judge_available = jAvail
                    judge_scores = [
                        JudgeScore(dimension=r["dimension"],
                                   score=r["score"],
                                   rationale=r.get("rationale", ""))
                        for r in raw]
                except Exception as e:
                    logger.warning("Judge error %s: %s", sid, e)

            all_gates = all(c.passed for c in gates)
            if not all_gates:
                status = ScenarioStatus.FAIL
            elif judge_available and judge_scores:
                avg = sum(j.score for j in judge_scores) / len(judge_scores)
                status = (ScenarioStatus.PASS if avg >= 1.5
                          else ScenarioStatus.PARTIAL)
            else:
                status = ScenarioStatus.PASS

            results[sid] = ScenarioResult(
                scenario_id=sid, status=status,
                hard_gate_results=gates,
                llm_judge_scores=judge_scores,
                judge_available=judge_available,
                trace=trace, failure_taxonomy=taxonomy,
            )
            gate_ok = "✓" if all_gates else "✗"
            javg = (f"{sum(j.score for j in judge_scores)/len(judge_scores):.1f}"
                    if judge_scores else "—")
            logger.info("  %s: %s | gates=%s | judge=%s | iter=%d | focus=%s",
                        sid, status.value, gate_ok, javg, iteration, focus)

        except Exception as e:
            logger.exception("Scenario %s error", sid)
            results[sid] = ScenarioResult(
                scenario_id=sid, status=ScenarioStatus.ERROR,
                failure_detail=str(e))

    run = EvaluationRun(
        run_id=run_id, timestamp=run_ts, git_commit=git_commit,
        model_name=model, scenario_version="S01-S18.v1",
        rp_data_hash=hashlib.sha256(b"U00010").hexdigest()[:12],
        baseline_ref=None, results=results,
    )

    from eval.report_al3 import save_report
    out_dir = os.path.join(os.path.dirname(__file__), "..",
                           "docs", "evaluations")
    json_path, md_path = save_report(run, out_dir)
    logger.info("Report: %s + %s", json_path, md_path)
    s = run.summary()
    logger.info("Summary: %s", json.dumps(s["by_status"]))

    print("\n" + "=" * 80)
    print("AL-3 BASELINE EVALUATION RESULTS")
    print("=" * 80)
    for sid in sorted(results):
        r = results[sid]
        gate_ok = "✓" if all(c.passed for c in r.hard_gate_results) else "✗"
        javg = (f"{r.avg_judge_score:.1f}"
                if r.avg_judge_score is not None else "—")
        focus = r.trace.focused_finding_id or "None"
        print(f"{sid:4s} | {r.status.value:7s} | gates={gate_ok} | "
              f"judge={javg:4s} | iter={r.trace.iteration_count} | "
              f"focus={focus:4s} | tax={r.failure_taxonomy or '—'}")

    s = run.summary()
    print(f"\nTotal: {len(results)} | " +
          " | ".join(f"{k}: {v}" for k, v in s["by_status"].items()))

    for sid in sorted(results):
        r = results[sid]
        failures = [c for c in r.hard_gate_results if not c.passed]
        if failures:
            print(f"\n{sid} FAILED GATES:")
            for f in failures:
                print(f"  {f.check_id}: expected={f.expected}, "
                      f"observed={f.observed}")

    print(f"\nRun ID: {run_id}")
    print(f"JSON: {json_path}")
    print(f"MD: {md_path}")


if __name__ == "__main__":
    main()

"""E3 Runtime Gate helpers for API E2E harnesses (P1-2).

Thin, fail-closed wrappers around qa/preflight_runtime.py so an API E2E
harness can enforce the B-lite two-stage feature-evidence contract:

  step 0    runtime gate (source/process/effective-flag dimensions)
  stage A   --require-feature-evidence <inv>  (declaration, pre-run)
  stage B   --verify-feature-evidence <stage-A record>  (report time)

Every function returns the REAL preflight exit code (0/2/3) — callers
must propagate it verbatim; nothing here masks, retries, or downgrades.
Deterministic control-flow tests stub subprocess.run in this module.
"""

import os
import subprocess
import sys

PF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "preflight_runtime.py")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD_DIR = os.path.join(REPO, "docs", "evaluations")


def gate(args, record_path):
    """Run one preflight invocation; return (exit_code, record_path)."""
    os.makedirs(RECORD_DIR, exist_ok=True)
    r = subprocess.run(
        [sys.executable, PF] + list(args) + ["--record", record_path],
        cwd=REPO)
    return r.returncode, record_path


def runtime_gate_backend(record_path):
    """Step 0: backend source/process/effective-flag dimensions."""
    return gate([
        "--port", "8001", "--service", "backend",
        "--watch", "backend/app", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true",
        "--target-feature", "goal_contract",
        "--probe-url", "http://localhost:8001/api/v2/runtime/identity",
        "--probe-contains", "investigation",
    ], record_path)


def require_feature_evidence(investigation_id, record_path):
    """Stage A: declare the evidence requirement for THIS acceptance's
    investigation, bound to the record's generated_at. Declaration only —
    never alters the preflight verdict dimensions."""
    return gate([
        "--port", "8001", "--service", "backend",
        "--watch", "backend/app", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true",
        "--target-feature", "goal_contract",
        "--require-feature-evidence", investigation_id,
        "--probe-url", "http://localhost:8001/api/v2/runtime/identity",
        "--probe-contains", "investigation",
    ], record_path)


def verify_feature_evidence(declaration_record, record_path):
    """Stage B (report time): verify against the DECLARATION record —
    task/investigation binding and strict time constraint come from the
    record, never from CLI arguments."""
    return gate([
        "--verify-feature-evidence", declaration_record,
    ], record_path)

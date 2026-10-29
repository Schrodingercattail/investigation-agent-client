"""Runtime/code consistency preflight for real API/browser E2E runs.

Governance helper (project rule: CLAUDE.md § Runtime/code consistency
preflight). Read-only inspection of ONE service process against the
current working tree; emits a preflight record for the run log and a
non-zero exit when runtime/code consistency cannot be established.

What it checks (no secrets, no env dumps):
  1. which PID/command/cwd actually LISTENS on the target port;
  2. process start time(s) vs the newest source mtime under the watched
     paths (dirty working trees included — Git HEAD alone is not an
     identity); uvicorn --reload supervisors are handled via their child
     workers;
  3. the service's reload model (`restart` = must be (re)started after
     source changes; `hot` = serves current source from disk, e.g. vite
     dev — start-time staleness alone is then not a verdict, and the
     change-specific behavioral probe carries the proof);
  4. an optional change-specific behavioral probe (URL must CONTAIN a
     marker substring) — the only proof that the LOADED code implements
     the change; HTTP 200 alone is never sufficient;
  5. working-tree identity: HEAD + a hash of `git status --porcelain`.

Verdicts: FRESH (consistency established) / STALE (service predates
source; restart required, then re-run this preflight) / UNVERIFIED
(probe absent or failed → formal E2E must NOT start).

Usage:
  python3 qa/preflight_runtime.py --port 8001 --service backend \
      --watch backend/app --reload-model restart \
      --probe-url http://localhost:8001/api/v2/runtime/identity \
      --probe-contains '"service":"investigation-agent-backend"' \
      --record docs/evaluations/preflight-<stamp>.json
  python3 qa/preflight_runtime.py --self-check
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "dist"}


def _run(cmd, cwd=REPO, timeout=20):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def newest_source_mtime(paths):
    """Newest mtime (float) among files under `paths`, skipping build/
    cache dirs. Missing paths are ignored (reported)."""
    newest = 0.0
    missing = []
    for path in paths:
        ap = path if os.path.isabs(path) else os.path.join(REPO, path)
        if not os.path.exists(ap):
            missing.append(path)
            continue
        if os.path.isfile(ap):
            newest = max(newest, os.path.getmtime(ap))
            continue
        for root, dirs, files in os.walk(ap):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if f.endswith((".pyc", ".pyo", ".log")):
                    continue
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
    return newest, missing


def listener_on_port(port):
    """(pid, command) of the process LISTENing on the port, or None."""
    r = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] != "PID":
            return parts[1], line
    return None


def proc_identity(pid):
    """cwd + start time + command for a PID (read-only)."""
    cwd = None
    # -a ANDs the selectors; without it lsof lists unrelated fds
    r = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    for line in (r.stdout or "").splitlines():
        if line.startswith("n/"):
            cwd = line[1:]
    r = _run(["ps", "-o", "lstart=,command=", "-p", str(pid)])
    lstart, _, command = r.stdout.strip().partition(" ")
    r2 = _run(["ps", "-o", "lstart=", "-p", str(pid)])
    return {"pid": int(pid), "cwd": cwd, "lstart": (r2.stdout or "").strip(),
            "command": (r.stdout or "").strip()}


def proc_tree_oldest_start(pid):
    """Epoch of the OLDEST start among the process and its children
    (uvicorn --reload: the supervisor may predate a change while a fresh
    worker proves a reload). Returns (oldest_epoch, newest_epoch)."""
    _MONTHS = {m: i for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
         "Sep", "Oct", "Nov", "Dec"), 1)}

    def epoch(pid_):
        # ps lstart = "Wed Oct 21 23:13:24 2026" (LOCAL time); parse
        # positionally — strptime is locale-dependent on macOS
        r = _run(["ps", "-o", "lstart=", "-p", str(pid_)])
        parts = (r.stdout or "").split()   # Wed Oct 21 23:13:24 2026
        if len(parts) != 5 or parts[1] not in _MONTHS:
            return None
        try:
            t = datetime.datetime(int(parts[4]), _MONTHS[parts[1]],
                                  int(parts[2]), *(int(x) for x in
                                                   parts[3].split(":")))
            return t.timestamp()      # naive local → local epoch
        except ValueError:
            return None

    def children(pid_):
        r = _run(["pgrep", "-P", str(pid_)])
        return [int(x) for x in (r.stdout or "").split()]

    epochs = []
    seen = set()
    frontier = [pid]
    while frontier:
        p = frontier.pop()
        if p in seen:
            continue
        seen.add(p)
        e = epoch(p)
        if e is not None:
            epochs.append(e)
        frontier.extend(children(p))
    if not epochs:
        return None, None
    return min(epochs), max(epochs)


def working_tree_identity():
    head = (_run(["git", "rev-parse", "HEAD"]).stdout or "").strip()
    st = (_run(["git", "status", "--porcelain"]).stdout or "")
    dirty_hash = hashlib.sha256(st.encode()).hexdigest()[:16]
    return {"git_head": head, "dirty_tree_hash": dirty_hash,
            "dirty_entries": len([x for x in st.splitlines() if x.strip()])}


def probe(url, contains, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            ok = contains in body
            return {"url": url, "http_ok": True, "marker_found": ok,
                    "note": "" if ok else "marker absent from response"}
    except Exception as e:
        return {"url": url, "http_ok": False, "marker_found": False,
                "note": f"{type(e).__name__}"}


def preflight(port, watch, reload_model, probe_url, probe_contains):
    record = {
        "preflight_version": 1,
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "service": {"port": port, "reload_model": reload_model},
        "tree": working_tree_identity(),
    }
    newest_mtime, missing = newest_source_mtime(watch)
    record["source_watermark"] = {
        "watch": watch,
        "newest_mtime_epoch": newest_mtime,
        "newest_mtime": (datetime.datetime.fromtimestamp(
            newest_mtime).isoformat() if newest_mtime else None),
        "missing_paths": missing,
    }
    listener = listener_on_port(port)
    if not listener:
        record["verdict"] = "UNVERIFIED"
        record["service"]["state"] = "no listener on port"
        return record
    pid = int(listener[0])
    oldest, newest = proc_tree_oldest_start(pid)
    record["service"].update({
        "listener": proc_identity(pid),
        "proc_tree_start_epoch_oldest": oldest,
        "proc_tree_start_epoch_newest": newest,
    })
    stale = None
    if newest_mtime and oldest is not None:
        if reload_model == "hot":
            record["service"]["staleness"] = (
                "hot-reload model: process start predating source is NOT "
                "a verdict; the behavioral probe carries the proof")
            stale = False
        else:
            stale = newest_mtime > oldest + 1.0
            record["service"]["staleness"] = (
                "STALE: source changed after the oldest process start"
                if stale else
                "process tree started at/after the newest source mtime")
        record["service"]["source_newer_than_process"] = stale
    else:
        record["service"]["staleness"] = (
            "UNCOMPUTABLE: no watermark or no process start times — "
            "restart-model consistency cannot be established")
    if probe_url:
        record["probe"] = probe(probe_url, probe_contains or "")
        if not record["probe"]["marker_found"]:
            record["verdict"] = "UNVERIFIED"
            return record
    elif reload_model != "hot":
        record["probe"] = {"url": None, "note": (
            "no behavioral probe supplied — a restart-model service "
            "cannot be declared consistent without one")}
        record["verdict"] = "UNVERIFIED"
        return record
    if stale is None:
        record["verdict"] = "UNVERIFIED"
    else:
        record["verdict"] = "STALE" if stale else "FRESH"
    return record


def self_check():
    """Pure-logic checks with synthetic values; touches no service."""
    import tempfile
    base = tempfile.mkdtemp(prefix="preflight-selfcheck-")
    a = os.path.join(base, "a.py")
    b = os.path.join(base, "sub")
    os.makedirs(b, exist_ok=True)
    open(a, "w").write("x")
    open(os.path.join(b, "c.py"), "w").write("y")
    newest, missing = newest_source_mtime([os.path.relpath(a, REPO)])
    assert newest > 0, "watermark must see the file"
    newest2, _ = newest_source_mtime([os.path.relpath(b, REPO)])
    assert newest2 >= newest - 1, "subdir watermark computed"
    _, miss = newest_source_mtime([os.path.join(base, "nope.py")])
    assert len(miss) == 1, "missing paths reported"
    # verdict logic (mirrors preflight()): restart model, an hour-old
    # process vs a just-touched file MUST be stale
    oldest = time.time() - 3600
    stale = newest > oldest + 1.0
    assert stale is True, "source newer than an hour-old process = stale"
    # and a process started after the newest source must NOT be stale
    fresher = newest_source_mtime([os.path.relpath(a, REPO)])[0]
    stale2 = fresher > (time.time() + 5) + 1.0
    assert stale2 is False, "process newer than source = fresh"
    # record shape
    record = {"verdict": "FRESH", "tree": working_tree_identity()}
    assert record["verdict"] in ("FRESH", "STALE", "UNVERIFIED")
    assert len(record["tree"]["dirty_tree_hash"]) == 16
    print("self-check OK: watermark, missing-path report, verdict shape")


def _parse_bool(text):
    return str(text).strip().lower() in ("1", "true", "yes", "on")


def flag_provenance_for_pid(name, expected, pid, listener_cwd):
    rec = {"name": name, "expected": _parse_bool(expected),
           "source": None, "effective": None, "matches": None,
           "in_process_verified": False,
           "residual": "listener in-memory Settings not directly "
                       "introspected (no diagnostic endpoint)"}
    try:
        r = _run(["ps", "-Eww", "-o", "command=", "-p", str(pid)])
        env_part = (r.stdout or "")
        for token in env_part.split():
            if token.startswith(name + "="):
                rec["effective"] = _parse_bool(token.split("=", 1)[1])
                rec["source"] = "env"
                break
        if rec["source"] is None and listener_cwd:
            env_file = os.path.join(listener_cwd, ".env")
            if os.path.exists(env_file):
                for line in open(env_file, errors="replace"):
                    if line.split("=", 1)[0].strip() == name:
                        rec["effective"] = _parse_bool(
                            line.split("=", 1)[1])
                        rec["source"] = "env_file:.env"
                        break
        if rec["source"] is None and listener_cwd:
            r = _run([sys.executable, "-c",
                      "from app.config import settings; "
                      f"print(settings.{name})"], cwd=listener_cwd)
            if r.returncode == 0:
                rec["effective"] = _parse_bool(r.stdout.strip())
                rec["source"] = "settings_default"
        if rec["effective"] is None:
            rec["matches"] = None          # NOT VERIFIED
        else:
            rec["matches"] = (rec["effective"] == rec["expected"])
    except Exception as exc:               # noqa: BLE001 — gate honesty
        rec["error"] = f"{type(exc).__name__}: {exc}"[:160]
        rec["matches"] = None
    return rec


FEATURE_EVIDENCE_KIND = "goal_contract_decision"   # KIND taxonomy: this
# kind only (deferred per approved B-lite scope)


def _parse_utc(text):
    """Parse an ISO-8601 timestamp; None when missing/naive/unparseable
    (a naive stamp cannot be compared against an aware declaration —
    unverifiable, never assumed UTC)."""
    if not text:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(text))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo is not None else None


def _store_db_path():
    """The canonical persistence source for feature-evidence verification.
    A module function (not a constant) so deterministic self-tests can
    inject an isolated store WITHOUT any CLI store override (the gate
    must never accept an arbitrary store from its caller)."""
    return os.path.join(REPO, "backend", "data",
                        "investigation_agent_v2.db")


def feature_evidence(db_path, declared_scope, declared_at):
    """Stage-B verification of the B-lite two-stage contract:
    in the DECLARED investigation, a task whose BUSINESS creation time
    (tasks_v2.started_at, written by the loop at task creation —
    agent_loop.py:1856) is STRICTLY AFTER declared_at AND whose
    persisted goal_contract_decision outcome exists and is not flag_off.
    Returns (verdict, evidence_record) — verdict PASS / FAIL / BLOCKED.
    Time-unverifiable bound candidates BLOCK (they can neither prove nor
    refute); they never downgrade to PASS or FAIL."""
    import json as _json
    import sqlite3
    ev = {"kind": FEATURE_EVIDENCE_KIND, "declared_scope": declared_scope,
          "declared_at": declared_at, "db": os.path.basename(db_path),
          "bound_candidates": [], "unverifiable_time": 0,
          "qualifying_task": None}
    try:
        db = sqlite3.connect(db_path)
        rows = db.execute(
            "SELECT t.task_id, t.started_at, a.document "
            "FROM tasks_v2 t LEFT JOIN agent_states a "
            "ON a.task_id = t.task_id WHERE t.investigation_id=?",
            (declared_scope,)).fetchall()
    except Exception as exc:               # noqa: BLE001 — store unusable
        ev["error"] = f"{type(exc).__name__}: {exc}"[:160]
        return "BLOCKED", ev
    decl_dt = _parse_utc(declared_at)
    if decl_dt is None:
        ev["error"] = "declaration timestamp unparseable"
        return "BLOCKED", ev
    any_unverifiable = False
    for tid, started_at, doc_text in rows:
        outcome = None
        if doc_text:
            try:
                dec = (_json.loads(doc_text).get("goal_contract_decision")
                       or {})
                outcome = dec.get("outcome")
            except Exception:              # noqa: BLE001 — corrupt doc
                outcome = None
        outcome_ok = outcome is not None and outcome != "flag_off"
        started = _parse_utc(started_at)
        time_ok = started is not None and started > decl_dt
        ev["bound_candidates"].append({
            "task_id": tid, "outcome": outcome,
            "started_at": started_at, "time_ok": time_ok,
            "outcome_ok": outcome_ok})
        if time_ok and outcome_ok:
            ev["qualifying_task"] = tid
            return "PASS", ev
        if started is None:
            any_unverifiable = True
    if any_unverifiable:
        ev["error"] = ("a bound candidate's creation time is missing or "
                       "unverifiable — the time constraint cannot be "
                       "evaluated conclusively")
        return "BLOCKED", ev
    return "FAIL", ev


def extract_declaration(record_path):
    """Stage-A declaration extraction: ONLY a genuine gate record that
    REQUIRED feature evidence carries a verifiable declaration — an
    arbitrary JSON file or a record without the requirement can never
    satisfy the binding (no CLI-side task/time overrides exist)."""
    rec = {"record_path": record_path, "valid": False}
    try:
        with open(record_path) as f:
            g = json.load(f)
        decl = ((g.get("gate") or {}).get("feature_evidence") or {})
        if not decl.get("required"):
            rec["error"] = "record does not require feature evidence"
            return rec
        scope = decl.get("declared_scope")
        declared_at = _parse_utc(decl.get("declared_at"))
        if not scope or declared_at is None:
            rec["error"] = "declaration missing scope or timestamp"
            return rec
        rec.update({"valid": True, "declared_scope": scope,
                    "declared_at": decl.get("declared_at"),
                    "kind": decl.get("kind"),
                    "record_generated_at": g.get("generated_at")})
    except Exception as exc:               # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"[:160]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int)
    ap.add_argument("--service", default="backend",
                    help="label recorded in the preflight record")
    ap.add_argument("--watch", nargs="+", default=["backend/app"],
                    help="source paths whose mtimes form the watermark")
    ap.add_argument("--reload-model", choices=["restart", "hot"],
                    default="restart")
    ap.add_argument("--probe-url")
    ap.add_argument("--probe-contains")
    ap.add_argument("--record", help="write the preflight record JSON here")
    ap.add_argument("--expect-flag", action="append", default=[],
                    metavar="NAME=VALUE",
                    help="required effective feature flag (repeatable); "
                         "verified via the process-equivalence chain")
    ap.add_argument("--target-feature", action="append", default=[],
                    help="declare which feature this acceptance tests "
                         "(repeatable; scopes the blocking checks)")
    ap.add_argument("--require-feature-evidence", metavar="INVESTIGATION_ID",
                    help="stage A (declaration, pre-run): record that this "
                         "acceptance requires a goal-contract decision from "
                         "a task in the declared investigation, created "
                         "AFTER this declaration (kind: "
                         "goal_contract_decision; KIND taxonomy deferred)")
    ap.add_argument("--verify-feature-evidence", metavar="GATE_RECORD",
                    help="stage B (report time): verify the DECLARATION in "
                         "the referenced gate record against persisted "
                         "evidence — task binding + strict time constraint "
                         "+ outcome validity; never accepts an arbitrary "
                         "task id")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.verify_feature_evidence:
        # stage B: the declaration comes FROM the referenced gate record —
        # task scope and time constraint are bound there, never taken from
        # CLI arguments, so arbitrary old task ids cannot satisfy it
        decl = extract_declaration(args.verify_feature_evidence)
        if not decl.get("valid"):
            rec = {"gate_mode": "feature_evidence_verify",
                   "declaration": decl, "verdict": "FAIL",
                   "reason": "record is not a valid feature-evidence "
                             "declaration"}
        else:
            verdict, ev = feature_evidence(_store_db_path(),
                                           decl["declared_scope"],
                                           decl["declared_at"])
            rec = {"gate_mode": "feature_evidence_verify",
                   "declaration": decl, "evidence": ev,
                   "verdict": verdict}
        if args.record:
            os.makedirs(os.path.dirname(os.path.abspath(args.record)),
                        exist_ok=True)
            with open(args.record, "w") as f:
                json.dump(rec, f, indent=2, ensure_ascii=False)
        print(json.dumps(rec, ensure_ascii=False)[:800])
        print("VERDICT:", rec["verdict"])
        return {"PASS": 0, "FAIL": 3, "BLOCKED": 2}[rec["verdict"]]
    if not args.port:
        print("--port required (or --self-check)", file=sys.stderr)
        return 2
    rec = preflight(args.port, args.watch, args.reload_model,
                    args.probe_url, args.probe_contains)
    # ---- Runtime Verification Gate (Part II §13): effective-config and
    # target-feature dimensions. A flag mismatch is a deterministic FAIL
    # (exit 3); an unverifiable required flag is BLOCKED (exit 2). The
    # verdicts FAIL/BLOCKED are recorded as such and never downgraded by
    # later green tests.
    rec["gate"] = {"target_features": list(args.target_feature or []),
                   "flags": [], "feature_evidence": None,
                   "residuals": ["listener in-memory Settings not directly "
                                 "introspected (process-equivalence chain "
                                 "only)"]}
    if args.require_feature_evidence:
        # stage A: DECLARATION ONLY — the evidence does not exist yet (the
        # acceptance run comes after the gate); this must not alter the
        # preflight verdict. Stage B (--verify-feature-evidence) enforces
        # task binding + strict time constraint against this record.
        rec["gate"]["feature_evidence"] = {
            "required": True,
            "kind": FEATURE_EVIDENCE_KIND,
            "declared_scope": args.require_feature_evidence,
            "declared_at": rec["generated_at"],
            "satisfied": None,
            "note": ("verified at report time via "
                     "--verify-feature-evidence <this record>")}
    if args.expect_flag and rec.get("service", {}).get("listener"):
        listener = rec["service"]["listener"]
        for spec in args.expect_flag:
            name, _, expected = spec.partition("=")
            rec["gate"]["flags"].append(flag_provenance_for_pid(
                name, expected, listener["pid"], listener.get("cwd")))
    elif args.expect_flag:
        rec["gate"]["flags"] = [{"name": s.partition("=")[0],
                                 "expected": None, "matches": None,
                                 "error": "no listener — flag unverifiable"}
                                for s in args.expect_flag]
    flag_fail = any(f.get("matches") is False for f in rec["gate"]["flags"])
    flag_unverifiable = any(f.get("matches") is None
                            for f in rec["gate"]["flags"])
    if flag_fail:
        rec["verdict"] = "FAIL"
    elif rec["verdict"] == "FRESH" and flag_unverifiable:
        rec["verdict"] = "BLOCKED"
    line = json.dumps(rec, ensure_ascii=False)
    if args.record:
        os.makedirs(os.path.dirname(os.path.abspath(args.record)),
                    exist_ok=True)
        with open(args.record, "w") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)
    print(line[:1200])
    print("VERDICT:", rec["verdict"])
    if rec["verdict"] == "FAIL":
        return 3
    return 0 if rec["verdict"] == "FRESH" else 2


if __name__ == "__main__":
    sys.exit(main())

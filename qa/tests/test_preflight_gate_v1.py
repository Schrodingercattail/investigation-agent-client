"""Runtime Verification Gate — deterministic self-tests (G1–G7).

The gate (qa/preflight_runtime.py) is tested against STUBBED process
tables (`_run`) and stubbed source watermarks — no real listener, no
real service, no flakiness. Proves the exit protocol:
0=PASS, 2=BLOCKED (incl. STALE/UNVERIFIED), 3=deterministic FAIL — and
that a preflight crash NEVER exits 0. G7 asserts pure unit-test runs
carry no gate coupling (no conftest hook exists).
"""

import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "preflight_runtime",
    os.path.join(_ROOT, "qa", "preflight_runtime.py"))
pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pf)


def _fresh_env(monkeypatch, mtime=1000.0, started=1500.0):
    """Stub EVERYTHING preflight() touches so the source/process
    dimensions are deterministically FRESH (process starts AFTER the
    newest source mtime)."""
    monkeypatch.setattr(pf, "working_tree_identity",
                        lambda: {"git_head": "stub", "dirty": "stub"})
    monkeypatch.setattr(pf, "newest_source_mtime",
                        lambda paths: (mtime, []))
    monkeypatch.setattr(pf, "listener_on_port",
                        lambda port: ("4242", "stub command"))
    monkeypatch.setattr(pf, "proc_tree_oldest_start",
                        lambda pid: (started, started))
    monkeypatch.setattr(pf, "proc_identity",
                        lambda pid: {"pid": pid, "cwd": "/stub/backend",
                                     "lstart": "stub", "command": "stub"})
    monkeypatch.setattr(pf, "probe",
                        lambda url, marker: {"url": url, "http_ok": True,
                                             "marker_found": True,
                                             "note": ""})


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["preflight_runtime.py"] + argv)
    return pf.main()


def _stub_ps(monkeypatch, env_token, settings_rc=1):
    """Stub _run for the flag provenance chain: `ps -Eww` returns the
    given env token inside the command line; the Settings subprocess
    fails with `settings_rc` (1 = mechanism cannot establish)."""
    def fake_run(cmd, cwd=None, timeout=20):
        class R:
            returncode = 0
            stdout = ""
        if cmd[:3] == ["ps", "-Eww", "-o"]:
            r = R()
            r.stdout = env_token
            return r
        if cmd[:1] == [sys.executable]:
            r = R()
            r.returncode = settings_rc
            r.stdout = ""
            return r
        if cmd[:2] == ["ps", "-o"] and "lstart" in cmd[2]:
            r = R()
            r.stdout = "stub"
            return r
        r = R()
        return r
    monkeypatch.setattr(pf, "_run", fake_run)


def test_g1_expected_flag_satisfied_pass(monkeypatch):
    _fresh_env(monkeypatch)
    _stub_ps(monkeypatch, "python -m uvicorn ENABLE_GOAL_SEMANTIC_CONTRACT"
                          "=true app.main:app")
    code = _run_main(monkeypatch, [
        "--port", "8001", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true",
        "--probe-url", "http://stub/"])
    assert code == 0


def test_g2_flag_contradiction_fail_exit3(monkeypatch):
    _fresh_env(monkeypatch)
    _stub_ps(monkeypatch, "python -m uvicorn ENABLE_GOAL_SEMANTIC_CONTRACT"
                          "=true app.main:app")
    code = _run_main(monkeypatch, [
        "--port", "8001", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=false"])
    assert code == 3


def test_g3_unverifiable_flag_blocked_exit2(monkeypatch, tmp_path):
    _fresh_env(monkeypatch)
    _stub_ps(monkeypatch, "python -m uvicorn app.main:app")  # no flag
    # no .env in the stubbed listener cwd; Settings subprocess fails
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(pf, "proc_identity",
                        lambda pid: {"pid": pid, "cwd": str(empty),
                                     "lstart": "stub", "command": "stub"})
    code = _run_main(monkeypatch, [
        "--port", "8001", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true"])
    assert code == 2


def test_g4_no_listener_blocked_exit2(monkeypatch):
    monkeypatch.setattr(pf, "working_tree_identity", lambda: {"stub": 1})
    monkeypatch.setattr(pf, "newest_source_mtime", lambda paths: (1.0, []))
    monkeypatch.setattr(pf, "listener_on_port", lambda port: None)
    code = _run_main(monkeypatch, [
        "--port", "8001", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true"])
    assert code == 2


def test_g5_stale_source_blocked_exit2(monkeypatch):
    # process started BEFORE the newest source mtime → STALE
    _fresh_env(monkeypatch, mtime=1000.0, started=500.0)
    _stub_ps(monkeypatch, "python -m uvicorn ENABLE_GOAL_SEMANTIC_CONTRACT"
                          "=true app.main:app")
    code = _run_main(monkeypatch, [
        "--port", "8001", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true"])
    assert code == 2


def test_g6_preflight_crash_never_exit0(monkeypatch):
    _fresh_env(monkeypatch)
    _stub_ps(monkeypatch, "python -m uvicorn ENABLE_GOAL_SEMANTIC_CONTRACT"
                          "=true app.main:app")
    monkeypatch.setattr(pf, "preflight",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            RuntimeError("gate exploded")))
    with pytest.raises(RuntimeError):
        _run_main(monkeypatch, [
            "--port", "8001", "--reload-model", "restart",
            "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true",
            "--record", "/tmp/never-written.json"])


def test_g7_unit_tests_have_no_gate_coupling():
    """Pure unit/component tests must not be forced through the gate:
    no conftest may import or invoke the preflight gate."""
    tests_dir = os.path.join(_ROOT, "backend", "tests")
    hits = []
    for name in os.listdir(tests_dir):
        if name == "conftest.py":
            text = open(os.path.join(tests_dir, name)).read()
            if "preflight_runtime" in text or "expect-flag" in text:
                hits.append(name)
    assert hits == [], f"gate coupling leaked into unit tests: {hits}"


# --- B-lite two-stage feature evidence (declaration + verify) ----------

import json as _json
import sqlite3

_DECLARED_AT = "2026-10-24T10:00:00+00:00"


def _tmp_db(tmp_path, rows):
    """rows: (task_id, investigation_id, started_at, outcome)."""
    db = tmp_path / "store.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks_v2 (task_id TEXT, "
                 "investigation_id TEXT, started_at TEXT)")
    conn.execute("CREATE TABLE agent_states (task_id TEXT, document TEXT)")
    for tid, inv, started, outcome in rows:
        conn.execute("INSERT INTO tasks_v2 VALUES (?,?,?)",
                     (tid, inv, started))
        dec = ({"goal_contract_decision": {"outcome": outcome}}
               if outcome is not None else {})
        conn.execute("INSERT INTO agent_states VALUES (?,?)",
                     (tid, _json.dumps(dec)))
    conn.commit()
    conn.close()
    return str(db)


def _declaration_record(tmp_path, scope="INV-test", declared_at=_DECLARED_AT):
    rec = {"generated_at": declared_at,
           "gate": {"feature_evidence": {
               "required": True, "kind": "goal_contract_decision",
               "declared_scope": scope, "declared_at": declared_at,
               "satisfied": None}}}
    path = tmp_path / "decl.json"
    path.write_text(_json.dumps(rec))
    return str(path)


def _verify(monkeypatch, record_path, db_path):
    # inject the isolated store via the module function — the CLI offers
    # NO store override (the gate never accepts an arbitrary store)
    monkeypatch.setattr(pf, "_store_db_path", lambda: db_path)
    return _run_main(monkeypatch, [
        "--verify-feature-evidence", record_path])


def test_stage_a_declaration_recorded(monkeypatch):
    """Stage A records the requirement bound to generated_at and does NOT
    alter the preflight verdict dimensions."""
    _fresh_env(monkeypatch)
    _stub_ps(monkeypatch, "python -m uvicorn ENABLE_GOAL_SEMANTIC_CONTRACT"
                          "=true app.main:app")
    code = _run_main(monkeypatch, [
        "--port", "8001", "--reload-model", "restart",
        "--expect-flag", "ENABLE_GOAL_SEMANTIC_CONTRACT=true",
        "--require-feature-evidence", "INV-test",
        "--probe-url", "http://stub/"])
    assert code == 0


def test_verify_pass_task_after_declaration(monkeypatch, tmp_path):
    db = _tmp_db(tmp_path, [("TASK-new", "INV-test",
                             "2026-10-24T11:00:00+00:00",
                             "contract_accepted")])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 0


def test_verify_old_evidence_cannot_pass(monkeypatch, tmp_path):
    """THE anti-bypass core: a valid accepted decision from BEFORE the
    declaration must never satisfy this acceptance."""
    db = _tmp_db(tmp_path, [("TASK-old", "INV-test",
                             "2026-10-24T09:00:00+00:00",
                             "contract_accepted")])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 3


def test_verify_equal_timestamp_fails(monkeypatch, tmp_path):
    db = _tmp_db(tmp_path, [("TASK-eq", "INV-test", _DECLARED_AT,
                             "contract_accepted")])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 3


def test_verify_other_investigation_fails(monkeypatch, tmp_path):
    db = _tmp_db(tmp_path, [("TASK-other", "INV-other",
                             "2026-10-24T11:00:00+00:00",
                             "contract_accepted")])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 3


def test_verify_missing_evidence_fails(monkeypatch, tmp_path):
    db = _tmp_db(tmp_path, [])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 3


def test_verify_flag_off_fails(monkeypatch, tmp_path):
    db = _tmp_db(tmp_path, [("TASK-fo", "INV-test",
                             "2026-10-24T11:00:00+00:00", "flag_off")])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 3


def test_verify_missing_outcome_fails(monkeypatch, tmp_path):
    db = _tmp_db(tmp_path, [("TASK-no", "INV-test",
                             "2026-10-24T11:00:00+00:00", None)])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 3


def test_verify_unverifiable_time_blocked(monkeypatch, tmp_path):
    db = _tmp_db(tmp_path, [("TASK-nt", "INV-test", None,
                             "contract_accepted")])
    assert _verify(monkeypatch, _declaration_record(tmp_path), db) == 2


def test_verify_store_unusable_blocked(monkeypatch, tmp_path):
    decl = _declaration_record(tmp_path)
    # an unwritable/unusable store path → BLOCKED, never FAIL-downgraded
    monkeypatch.setattr(pf, "_store_db_path",
                        lambda: str(tmp_path / "no_dir" / "x.db"))
    code = _run_main(monkeypatch, ["--verify-feature-evidence", decl])
    assert code == 2


def test_verify_non_declaration_record_rejected(monkeypatch, tmp_path):
    """An arbitrary JSON file (or a record that never required feature
    evidence) can NEVER satisfy the verification — no CLI override."""
    fake = tmp_path / "fake.json"
    fake.write_text(_json.dumps({"verdict": "PASS", "anything": True}))
    db = _tmp_db(tmp_path, [("TASK-new", "INV-test",
                             "2026-10-24T11:00:00+00:00",
                             "contract_accepted")])
    assert _verify(monkeypatch, str(fake), db) == 3


def test_unit_tests_have_no_gate_coupling():
    """Pure unit/component tests must not be forced through the gate:
    no conftest may import or invoke the preflight gate."""
    tests_dir = os.path.join(_ROOT, "backend", "tests")
    hits = []
    for name in os.listdir(tests_dir):
        if name == "conftest.py":
            text = open(os.path.join(tests_dir, name)).read()
            if "preflight_runtime" in text or "expect-flag" in text:
                hits.append(name)
    assert hits == [], f"gate coupling leaked into unit tests: {hits}"

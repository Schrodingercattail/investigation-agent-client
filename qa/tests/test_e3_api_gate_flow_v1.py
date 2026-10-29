"""E3 API-harness gate integration — deterministic control-flow tests.

Covers the P1-2 propagation contract WITHOUT the real service: the
harness-facing helpers (qa/e3_gate.py) must return the verifier's REAL
exit codes verbatim (0/2/3), never mask them, and must build the
two-stage CLI contract exactly (stage A declares the investigation;
stage B verifies against the declaration record). The real happy path
is exercised live by qa/journey_bchain_u00005_coverage_api.py.
"""

import importlib.util
import json as _json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "e3_gate", os.path.join(_ROOT, "qa", "e3_gate.py"))
e3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e3)


class _FakeCompleted:
    def __init__(self, code):
        self.returncode = code


def _stub_subprocess(monkeypatch, code, captured):
    def fake_run(argv, cwd=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return _FakeCompleted(code)
    monkeypatch.setattr(e3.subprocess, "run", fake_run)


def test_step0_exit_codes_propagate_verbatim(monkeypatch, tmp_path):
    for code in (0, 2, 3):
        captured = {}
        _stub_subprocess(monkeypatch, code, captured)
        got, rec = e3.runtime_gate_backend(str(tmp_path / "r.json"))
        assert got == code and rec.endswith("r.json")


def test_stage_a_declares_investigation(monkeypatch, tmp_path):
    captured = {}
    _stub_subprocess(monkeypatch, 0, captured)
    got, _ = e3.require_feature_evidence(
        "INV-real", str(tmp_path / "a.json"))
    assert got == 0
    argv = captured["argv"]
    assert "--require-feature-evidence" in argv
    assert argv[argv.index("--require-feature-evidence") + 1] == "INV-real"
    assert "--expect-flag" in argv
    assert "ENABLE_GOAL_SEMANTIC_CONTRACT=true" in argv


def test_stage_b_verifies_against_declaration_record(monkeypatch, tmp_path):
    captured = {}
    _stub_subprocess(monkeypatch, 3, captured)
    got, _ = e3.verify_feature_evidence(
        str(tmp_path / "stageA.json"), str(tmp_path / "b.json"))
    assert got == 3                      # real verifier exit, unmasked
    argv = captured["argv"]
    assert "--verify-feature-evidence" in argv
    assert argv[argv.index("--verify-feature-evidence") + 1].endswith(
        "stageA.json")
    # no CLI store/task/time override exists in the contract
    assert "--store" not in argv and "--task-id" not in argv
    assert "--after" not in argv


def test_final_exit_mapping_matrix():
    """P1-2 exit-code semantics fix: the final harness exit maps
    gate precedence verbatim and turns business-assertion failures
    non-zero. Exceptions keep the pre-existing exit 1 (out of scope for
    this pure function — the harness's except path is unchanged)."""
    f = e3.__dict__  # noqa: F841 — the harness module owns _final_exit
    import importlib.util as _ilu
    hspec = _ilu.spec_from_file_location(
        "bchain_harness",
        os.path.join(_ROOT, "qa", "journey_bchain_u00005_coverage_api.py"))
    # importing executes the module-level journey — load ONLY the function
    import ast, types
    hsrc = open(os.path.join(_ROOT, "qa",
                             "journey_bchain_u00005_coverage_api.py")).read()
    tree = ast.parse(hsrc)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_final_exit")
    mod = ast.Module(body=[fn], type_ignores=[])
    ns = {}
    exec(compile(ast.fix_missing_locations(mod), "<harness>", "exec"), ns)
    final_exit = ns["_final_exit"]
    # all business assertions pass + gate pass → 0
    assert final_exit(0, True) == 0
    # ONE business assertion failure + gate pass → 1 (never exit 0)
    assert final_exit(0, False) == 1
    # gate codes take precedence, business results never remap them
    assert final_exit(2, True) == 2
    assert final_exit(2, False) == 2
    assert final_exit(3, True) == 3
    assert final_exit(3, False) == 3


def test_final_exit_against_real_saved_run():
    """The mapping applied to the REAL saved run (5 business assertions
    failed, gate PASS) must yield a NON-ZERO exit — proving the gap
    (which exited 0) is closed, using actual run data without re-running
    the real-LLM journey."""
    import ast
    real = "/tmp/bchain_u00005_api_e2e.json"
    if not os.path.exists(real):
        import pytest
        pytest.skip("real run artifact not present on this machine")
    results = _json.load(open(real))
    hsrc = open(os.path.join(_ROOT, "qa",
                             "journey_bchain_u00005_coverage_api.py")).read()
    tree = ast.parse(hsrc)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_final_exit")
    ns = {}
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[])), "<harness>", "exec"), ns)
    gate_pass = results["e3_gate"]["verdict"] == "PASS"
    assert ns["_final_exit"](0 if gate_pass else 3,
                             results["PASS"]) != 0


def test_no_masking_layer_exists(monkeypatch, tmp_path):
    """A non-zero verifier exit is returned AS-IS — no retry, no
    downgrade, no conditional remapping inside the helper."""
    for code in (1, 2, 3, 4):
        captured = {}
        _stub_subprocess(monkeypatch, code, captured)
        got, _ = e3.verify_feature_evidence(
            str(tmp_path / "a.json"), str(tmp_path / "b.json"))
        assert got == code


def test_p0b_harness_wiring_shape():
    """Structural check of the designated harness's E3 integration:
    _run_goal must declare stage A before the /goal call and verify
    stage B before returning (the single chokepoint for all scenarios)."""
    hsrc = open(os.path.join(
        _ROOT, "qa", "journey_p0b_contract_api_e2e_u00047.py")).read()
    fn_start = hsrc.index("def _run_goal")
    fn_end = hsrc.index("def _record", fn_start)
    body = hsrc[fn_start:fn_end]
    i_decl = body.index("require_feature_evidence")
    i_goal = body.index('"/investigations/{inv_id}/goal"')
    i_ver = body.index("verify_feature_evidence")
    i_ret = body.index("return activity, detail, inv_id, task_id")
    assert i_decl < i_goal < i_ver < i_ret
    assert "sa_record" in body and "sb_record" in body
    # step 0 gate precedes the first investigation creation
    assert hsrc.index("runtime_gate_backend") < hsrc.index(
        '"/investigations"')


def test_u00033fx_harness_wiring_and_exit_semantics():
    """Structural check of journey_u00033_fixes_api.py (P1-2 final batch):
    step 0 gate precedes the try block; _run_goal declares stage A before
    the intake/goal calls and verifies stage B before returning; gate
    failures exit 2/3 fail-closed; the final exit is conditional on the
    business PASS (never a silent exit 0 on business failure)."""
    hsrc = open(os.path.join(
        _ROOT, "qa", "journey_u00033_fixes_api.py")).read()
    # step 0 precedes the scenario try block
    i_step0 = hsrc.index("runtime_gate_backend")
    i_try = hsrc.index("\ntry:")
    assert i_step0 < i_try
    fn_start = hsrc.index("def run_goal")
    body = hsrc[fn_start:hsrc.index("\n\ntry:", fn_start)]
    i_decl = body.index("require_feature_evidence")
    i_turn = body.index('"/investigations/{inv_id}/turn"')
    i_goal = body.index('"/investigations/{inv_id}/goal"')
    i_ver = body.index("verify_feature_evidence")
    i_ret = body.index("return {")
    assert i_decl < i_turn < i_goal < i_ver < i_ret
    # fail-closed propagation
    assert 'sys.exit(2)' in hsrc and 'sys.exit(3)' in hsrc or \
        '_gate_fail' in hsrc
    assert hsrc.index("def _gate_fail") > 0
    # business failure never exits 0: the success tail maps PASS → 0/1
    assert 'sys.exit(0 if results["PASS"] else 1)' in hsrc

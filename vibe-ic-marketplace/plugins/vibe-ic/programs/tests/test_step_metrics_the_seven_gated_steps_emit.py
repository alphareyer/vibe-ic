"""Steps 10, 21, 23, 24, 25, 37 and 37.3 EMIT through `step_metrics` (ruling:
adopt, never record).

`step_metrics_adoption_check` is blocking on the delta: a step that gains a
program and does not emit FAILS. #2261 (972c21233, 2026-09-15) gave steps 10,
21, 23, 24, 25 and 37 their first `programs:` and #2514 gave 37.3 its own; none
emitted, and the residual was never re-recorded, so the gate was red on main
from 2026-09-15. Recording them in `_step_metrics_adoption_residual.json` would
be a hand-written baseline, so each declared program now emits its OWN
outcome, attributed to the step whose flow clause ran it.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import step_metrics as SM                        # noqa: E402

_PLUGIN = _PROGRAMS.parent
_FLOW = _PLUGIN / "flow" / "phase1_phase2_phase3.yaml"
_STEPS = ("10", "21", "23", "24", "25", "37", "37.3")


def _clauses():
    """(step, program, argv) for each of the seven steps' gate clauses that
    run one of the step's declared programs -- read from the yaml, not typed."""
    doc = yaml.safe_load(_FLOW.read_text())
    out = []
    for s in doc["steps"]:
        sid = str(s["id"])
        if sid not in _STEPS:
            continue
        progs = set(s.get("programs") or [])
        for clause in SM._gate_strings(s.get("gate")):
            try:
                toks = shlex.split(clause)
            except ValueError:
                continue          # prose-shaped clause text, not a command
            if toks and Path(toks[0]).stem in progs:
                out.append((sid, Path(toks[0]).stem, toks[1:]))
    return out


def test_the_adoption_gate_passes_on_the_shipped_tree():
    r = subprocess.run([sys.executable,
                        str(_PROGRAMS / "step_metrics_adoption_check.py"),
                        str(_PLUGIN)], capture_output=True, text=True)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "declare programs and emit nothing" not in out, out


def test_every_one_of_the_seven_has_a_clause_this_test_can_read():
    assert {c[0] for c in _clauses()} == set(_STEPS), _clauses()


@pytest.mark.parametrize("step,program,argv", _clauses())
def test_each_clause_is_attributed_to_its_own_step(step, program, argv):
    got, why = SM.step_for_invocation(program, argv)
    assert got == step, (program, argv, why)


def test_a_program_two_steps_run_is_refused_without_its_output_path():
    got, why = SM.step_for_invocation("sta_report_check", ["."])
    assert got is None and "10" in why and "23" in why, why


def _sta_project(tmp: Path) -> Path:
    sta = tmp / "phase3" / "stage3" / "sta"
    sta.mkdir(parents=True)
    body = ("OpenSTA 2.4.0 report_checks\n"
            "Startpoint: reg_a (rising edge-triggered flip-flop clocked by clk)\n"
            "Endpoint: reg_b (rising edge-triggered flip-flop clocked by clk)\n"
            "Path Type: max\nWNS = 0.15 ns\nTNS = 0.0 ns\n"
            "slack (MET)\nsetup check: PASS\nhold check: PASS\n"
            "data arrival time: 2.34 ns\n" + "# " + ("=" * 78 + "\n") * 40)
    (sta / "post_route_timing.rpt").write_text(body)
    return tmp


def test_step23s_clause_writes_step23s_metrics_end_to_end(tmp_path):
    proj = _sta_project(tmp_path)
    argv = next(a for s, p, a in _clauses() if s == "23")
    r = subprocess.run([sys.executable, str(_PROGRAMS / "sta_report_check.py"),
                        *argv], cwd=proj, capture_output=True, text=True)
    m = proj / "reports" / "metrics" / "23.json"
    assert m.is_file(), r.stdout[-2000:] + r.stderr[-2000:]
    doc = json.loads(m.read_text())
    assert doc["23__gate__rc"] == r.returncode, doc
    assert doc["23__gate__passed"] is (r.returncode == 0), doc
    assert not (proj / "reports" / "metrics" / "10.json").exists()


def test_the_same_program_under_step10s_clause_writes_step10s(tmp_path):
    proj = _sta_project(tmp_path)
    argv = next(a for s, p, a in _clauses() if s == "10")
    r = subprocess.run([sys.executable, str(_PROGRAMS / "sta_report_check.py"),
                        *argv], cwd=proj, capture_output=True, text=True)
    doc = json.loads((proj / "reports/metrics/10.json").read_text())
    assert doc["10__gate__rc"] == r.returncode, doc
    assert not (proj / "reports" / "metrics" / "23.json").exists()


def test_the_emit_never_changes_the_gates_rc(tmp_path):
    """The same invocation with the metrics directory made unwritable exits
    with the same rc: the metric is bookkeeping, never a verdict."""
    proj = _sta_project(tmp_path)
    argv = next(a for s, p, a in _clauses() if s == "23")
    r1 = subprocess.run([sys.executable, str(_PROGRAMS / "sta_report_check.py"),
                         *argv], cwd=proj, capture_output=True, text=True)
    (proj / "reports" / "metrics").mkdir(parents=True, exist_ok=True)
    (proj / "reports" / "metrics" / "23.json").unlink(missing_ok=True)
    (proj / "reports" / "metrics" / "23.json").mkdir()   # blocks the write
    r2 = subprocess.run([sys.executable, str(_PROGRAMS / "sta_report_check.py"),
                         *argv], cwd=proj, capture_output=True, text=True)
    assert r1.returncode == r2.returncode
    assert "EMIT FAILED" in r2.stderr, r2.stderr[-1500:]


def test_gds_xor_check_emits_under_37_3_from_its_check_clause(tmp_path):
    argv = next(a for s, p, a in _clauses() if s == "37.3")
    r = subprocess.run([sys.executable, str(_PROGRAMS / "gds_xor_check.py"),
                        *argv], cwd=tmp_path, capture_output=True, text=True)
    doc = json.loads((tmp_path / "reports/metrics/37_3.json").read_text())
    assert doc["37_3__gate__rc"] == r.returncode, doc

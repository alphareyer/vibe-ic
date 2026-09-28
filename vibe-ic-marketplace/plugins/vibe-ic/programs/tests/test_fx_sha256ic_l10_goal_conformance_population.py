"""Step 4 gives L10 vectors and pass-rate goals separate authorities.

Measured on a SHA-256 IC front door: 9/9 case oracles and 4/4 scenario goals
passed, but l10_tb_conformance_check demanded a separate unit TB for each of
the four goal rows and blocked Step 4. This test drives its CLI over the real
execution-record contract, with only the simulator's file writes staged.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _l10_execution as X  # noqa: E402


VECTOR = {"name": "known_case", "kind": "functional_vector",
          "stimulus": "1", "expected": "1"}
GOAL = {"name": "boundary_pass_rate", "kind": "coverage_goal",
        "coverage_scope": "short and long transaction boundaries",
        "expected": "100% PASS", "covered_by": ["known_case"]}


def _project(tmp_path: Path, *, extra_vector: bool = False,
             literal_vector: bool = False) -> tuple[Path, Path, Path]:
    project = tmp_path / "ic"
    l10 = project / "phase1/generated_docs/L10_TEST_CASES.json"
    l10.parent.mkdir(parents=True)
    cases = [VECTOR, GOAL]
    if extra_vector:
        cases.append({"name": "unexecuted_vector", "kind": "functional_vector",
                      "stimulus": "0", "expected": "0"})
    if literal_vector:
        cases.append({"name": "literal_value", "kind": "functional_vector",
                      "stimulus": "2", "expected": "42"})
    l10.write_text(json.dumps({"schema_version": 2, "doc_class": "test_cases",
                               "test_cases": cases}) + "\n")
    tb_dir = project / "phase2/stage1/sim/tb"
    tb_dir.mkdir(parents=True)
    tb = tb_dir / "known_case.v"
    tb.write_text("""module tiny(input wire clk, output wire done);
assign done=clk;
endmodule
module known_case;
reg clk=0;
wire done;
tiny dut(.clk(clk),.done(done));
initial begin #1 clk=1; #1 if(done!==1'b1) $fatal(1); $finish; end
endmodule
""")
    X.write_record(project, l10,
                   [{"id": "known_case", "verdict": "PASS",
                     "sim_executed": True, "tb_file": str(tb),
                     "detail": "simulator checked the output"}],
                   producer="testbench_gen.run_unit_tbs", tb_dir=tb_dir)
    return project, l10, tb_dir


def _gate(project: Path, l10: Path, tb_dir: Path):
    out = project / "reports/phase2/gates/l10_tb_conformance.json"
    run = subprocess.run([sys.executable, str(PROG / "l10_tb_conformance_check.py"),
                          "--l10", str(l10), "--tb-dir", str(tb_dir),
                          "--project", str(project), "--out", str(out)],
                         text=True, capture_output=True, check=False)
    return run, json.loads(out.read_text())


def test_measured_coverage_goal_is_disclosed_but_not_demanded_as_a_vector(tmp_path):
    project, l10, tb_dir = _project(tmp_path)
    run, report = _gate(project, l10, tb_dir)
    assert run.returncode == 0, run.stderr
    assert report["coverage_goal_population"]["declared_count"] == 1
    assert report["coverage_goal_population"]["goals"] == ["boundary_pass_rate"]
    assert report["coverage_goal_population"]["measured_by"] == (
        "cpu_functional_oracle_waiver_check")
    assert [r["id"] for r in report["results"]] == ["known_case"]


def test_genuine_unexecuted_vector_still_blocks_with_a_goal_present(tmp_path):
    project, l10, tb_dir = _project(tmp_path, extra_vector=True)
    run, report = _gate(project, l10, tb_dir)
    assert run.returncode == 1
    assert any(r["id"] == "unexecuted_vector" and
               r["status"] == X.NOT_EXECUTED for r in report["results"])
    assert report["coverage_goal_population"]["declared_count"] == 1


def test_literal_expected_value_is_never_reclassified_as_a_goal(tmp_path):
    project, l10, tb_dir = _project(tmp_path, literal_vector=True)
    run, report = _gate(project, l10, tb_dir)
    assert run.returncode == 1
    assert any(r["id"] == "literal_value" and
               r["status"] == X.NOT_EXECUTED for r in report["results"])

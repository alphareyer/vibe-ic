"""Coverage instruments executed functional HDL, never a connectivity scaffold."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _l10_execution as l10  # noqa: E402
import design_one_shot_runner as runner  # noqa: E402
import testbench_gen  # noqa: E402
import verilator_coverage_measure as coverage  # noqa: E402


def _project(tmp_path, *, unit):
    project = tmp_path / "project"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "dut.v").write_text(
        "module dut(input clk, input rst, input [7:0] cmd); endmodule\n")
    full = project / "phase2/stage1/sim_full_stack"
    full.mkdir(parents=True)
    (full / "tb_dut_full.v").write_text(
        "// CONNECTIVITY-ONLY skeleton\n"
        "module tb_dut_full;\n"
        "reg clk = 0; reg rst = 0; reg [7:0] cmd = 0;\n"
        "dut u_dut(.clk(clk), .rst(rst), .cmd(cmd));\n"
        "initial begin rst = 1; #10; rst = 0; #10; $finish; end\n"
        "endmodule\n")
    if unit:
        unit_dir = project / "phase2/stage1/sim/tb"
        unit_dir.mkdir(parents=True)
        tb = unit_dir / "case.v"
        # The source audit cannot classify a `logic` declaration.  The
        # generator's provenance and hash-bound simulation record can.
        tb.write_text(
            "// CITATION : L10 case_a\n"
            "module case;\n"
            "logic clk, rst; logic [7:0] cmd;\n"
            "dut u_dut(.clk(clk), .rst(rst), .cmd(cmd));\n"
            "initial begin clk = 0; rst = 1; cmd = 8'h5a; #10; $finish; end\n"
            "endmodule\n")
        l10_path = project / "phase1/generated_docs/L10_TEST_CASES.json"
        l10_path.parent.mkdir(parents=True)
        l10_path.write_text(json.dumps({"cases": [{"id": "case_a"}]}))
        l10.write_record(project, l10_path, [{
            "id": "case_a", "verdict": "PASS", "sim_executed": True,
            "tb_file": str(tb),
        }], producer="testbench_gen.run_unit_tbs")
        provenance = testbench_gen.oracle_provenance(project)
        record = project / "reports/phase2/sim/l10_oracle_provenance.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(provenance))
    return project


def test_executed_unit_tb_is_selected_over_connectivity_skeleton(tmp_path):
    project = _project(tmp_path, unit=True)
    _, tbs = coverage.discover_measure_testbenches(project)
    assert [Path(p).name for p in tbs] == ["case.v"], tbs


def test_flow_build_instruments_the_executed_unit_tb(tmp_path, monkeypatch):
    project = _project(tmp_path, unit=True)
    built = []

    def fake_verilator(rtl, tb, build_dir, run_dir, *, exec_fn, build_jobs):
        built.append(Path(tb).name)
        dat = tmp_path / "coverage.dat"
        source = project / "phase2/stage1/rtl/dut.v"
        dat.write_text(
            f"C '\x01f\x02{source}\x01l\x021\x01page\x02v_line/dut' 1\n"
            f"C '\x01f\x02{source}\x01l\x022\x01page\x02v_toggle/dut' 1\n"
            f"C '\x01f\x02{source}\x01l\x023\x01page\x02v_branch/dut' 1\n")
        return str(dat)

    monkeypatch.setattr(coverage, "verilate_tb_and_run", fake_verilator)
    monkeypatch.setattr(runner, "_tool_in_container", lambda *_: True)
    monkeypatch.setattr(runner, "_verilator_stage_exec", lambda *_: None)
    monkeypatch.setattr(runner, "_eda_thread_count", lambda: 1)
    monkeypatch.setattr(runner, "_local_exec_mode", lambda: True)
    result = runner.step_verilator_coverage(project, "dut", container="test")
    assert result.status == "PASS", result.detail
    assert built == ["case.v"], built
    payload = json.loads((project / "reports/phase2/coverage"
                          / "coverage_verilator.json").read_text())
    assert [Path(p).name for p in payload["testbenches"]] == ["case.v"]


def test_skeleton_only_remains_unmeasured_and_names_handoff(tmp_path):
    project = _project(tmp_path, unit=False)
    assert coverage.discover_measure_testbenches(project)[1] == []
    stale = project / "reports/phase2/coverage/coverage_verilator.json"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"old": "measurement"}')
    result = runner.step_verilator_coverage(project, "dut")
    assert result.status == "NOT_MEASURED"
    assert "testbench-gen hand-off" in result.detail
    assert not stale.exists()

"""A testbench may import types declared by a staged RTL package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verilator_coverage_measure as coverage  # noqa: E402


def test_package_source_precedes_importing_testbench(tmp_path):
    pkg = tmp_path / "types_pkg.sv"
    pkg.write_text("package types_pkg; typedef logic [1:0] mode_e; endpackage\n")
    tb = tmp_path / "tb.v"
    tb.write_text("module tb; import types_pkg::*; mode_e mode; endmodule\n")
    build = tmp_path / "build"
    commands = []

    def execute(argv, cwd):
        commands.append(argv)
        if argv[0] == "verilator":
            assert argv.index(str(pkg)) < argv.index(str(tb))
            (build / "Vtb").write_text("")
        else:
            (build / "coverage.dat").write_text("C '\x01f\x02types_pkg.sv\x01l\x021' 1\n")
        return 0, "", ""

    dat = coverage.verilate_tb_and_run(
        [str(pkg)], str(tb), str(build), str(build), exec_fn=execute)
    assert Path(dat).is_file()
    assert len(commands) == 2

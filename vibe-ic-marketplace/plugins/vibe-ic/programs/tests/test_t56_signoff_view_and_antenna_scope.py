"""The repair and precheck must consume the same sign-off evidence as STA/antenna."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as runner  # noqa: E402
import general_precheck as precheck  # noqa: E402
from antenna_report_check import main as antenna_main, build_argv  # noqa: E402


def test_repair_reads_exact_io_liberty_in_each_signoff_scene(tmp_path):
    report = tmp_path / "reports/phase3/io_pad_chip_top.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"io_library_liberty": [
        "/io__ss.lib", "/io__tt.lib", "/io__ff.lib"]}))
    pdk = SimpleNamespace(macro_libs=[])
    corners = {"SS": "/core__ss.lib", "TT": "/core__tt.lib",
               "FF": "/core__ff.lib"}
    extras = {c: runner._sta_extra_liberties(tmp_path, pdk, lib)
              for c, lib in corners.items()}
    deck = runner._build_postroute_timing_repair_tcl(
        "chip", "/tech.lef", "/cell.lef", corners["TT"],
        "/pnr", "/repair", "M", corner_libs=corners,
        post_route_start=True,
        corner_spefs_c={"nom": "/nom.spef", "min": "/min.spef",
                        "max": "/max.spef"},
        corner_extra_libs_c=extras)
    assert "read_liberty -corner ss /io__ss.lib" in deck
    assert "read_liberty -corner ss_nom /io__ss.lib" in deck
    assert "read_liberty -corner tt /io__tt.lib" in deck
    assert "read_liberty -corner ff /io__ff.lib" in deck
    assert "read_liberty -corner ff_nom /io__ff.lib" in deck
    assert "read_liberty -corner ss /io__ff.lib" not in deck


def test_tapeout_antenna_delegate_reads_canonical_signed_report(tmp_path):
    reports = tmp_path / "reports/phase3"
    reports.mkdir(parents=True)
    (reports / "antenna.rpt").write_text(
        "# OpenROAD check_antennas\n"
        "antenna check: 2 net violations, 2 pin violations\n"
        "antenna clean: NO\n"
        "[INFO ANT-0002] Found 2 net violations.\n"
        "[INFO ANT-0001] Found 2 pin violations.\n"
        "# tail of the same tool log\n"
        "[INFO ANT-0002] Found 2 net violations.\n"
        "[INFO ANT-0001] Found 2 pin violations.\n"
        + "# tool output\n" * 20)
    (reports / "antenna_violations.rpt").write_text(
        "Net: signal\n  Pin: gate/I\n  Layer: Metal2\n" * 20)
    step = next(s for s in precheck.LADDER
                if s.step_id == "Checker.KLayoutAntenna")
    out = reports / "verdict.json"
    rc = antenna_main(build_argv([str(tmp_path), *step.delegate.argv_tail,
                                  "--json", str(out)]))
    verdict = json.loads(out.read_text())
    assert rc != 0
    assert verdict["summary"]["violations"] == 4
    assert all(f["rule"] != "ANTENNA_NO_TOOL_SIGNATURE"
               for f in verdict["findings"])
    assert verdict["summary"]["files_found"] == 1


def test_antenna_report_log_tail_does_not_double_count(tmp_path):
    reports = tmp_path / "reports/phase3"
    reports.mkdir(parents=True)
    (reports / "antenna.rpt").write_text(
        "# OpenROAD check_antennas\n"
        "antenna check: 1 net violations, 1 pin violations\n"
        "antenna clean: NO\n"
        "[INFO ANT-0002] Found 1 net violations.\n"
        "[INFO ANT-0001] Found 1 pin violations.\n"
        "# full log tail\n"
        "[INFO ANT-0002] Found 1 net violations.\n"
        "[INFO ANT-0001] Found 1 pin violations.\n"
        + "# tool output\n" * 20)
    out = reports / "verdict.json"
    assert antenna_main(build_argv([str(tmp_path), "--under",
                                    "reports/phase3/antenna.rpt",
                                    "--json", str(out)])) != 0
    assert json.loads(out.read_text())["summary"]["violations"] == 2

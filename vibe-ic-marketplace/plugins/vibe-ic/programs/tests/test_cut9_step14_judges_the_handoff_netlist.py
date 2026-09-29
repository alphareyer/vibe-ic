"""CUT_W4 item 1: step 14 judges the NETLIST step 9 hands to PnR, whichever
arm produced it, instead of the text of the direct recipe.

The four script-text programs (yosys_hilomap_required_check,
yosys_script_template_check, yosys_tiecell_recipe_order_check,
_yosys_inline_mode_detect) had nothing to read once step 9 became LibreLane
Yosys.Synthesis on the chip path: the tool writes no inline `yosys -p`. What
they protected is a property of the netlist, and each lesson is asserted here
on the netlist:

  * missing hilomap -> literal constants -> DRT-0305 `zero_` nets (v068);
  * setundef not before hilomap -> `1'hx` survives (HDLC, v0.1.98 RULE 1);
  * aggressive clean after hilomap deletes the tie cells -> constants return
    (v0.1.98 RULE 2);
  * an empty handoff netlist, and one older than the RTL it claims
    (audit_handoff_netlist, vibe-ic#1253).

The positive/negative netlists are the calibrated pair of
`synth_handoff_netlist_check::constant_connections` (real Yosys output).
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import synth_handoff_netlist_check as H  # noqa: E402

CAL = PROGRAMS / "calibration"
TIES = {"SYNTH_TIEHI_CELL": "gf180mcu_fd_sc_mcu7t5v0__tieh/Z",
        "SYNTH_TIELO_CELL": "gf180mcu_fd_sc_mcu7t5v0__tiel/ZN"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _project(tmp_path: Path, netlist_src: Path, *, tool: bool) -> Path:
    p = tmp_path / "proj"
    rtl = p / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "cal_const.v").write_text("module cal_const; endmodule\n")
    synth = p / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    netlist = synth / "cal_const_synth.v"
    netlist.write_bytes(netlist_src.read_bytes())
    (synth / "synth_inputs.json").write_text(json.dumps(
        {"netlist": netlist.name,
         "rtl_sha256": {"cal_const.v": _sha(rtl / "cal_const.v")}}))
    if tool:
        step = p / "phase3/librelane/02-yosys-synthesis"
        step.mkdir(parents=True)
        native = step / "cal_const.nl.v"
        native.write_bytes(netlist_src.read_bytes())
        (step / "state_out.json").write_text(json.dumps({"nl": str(native)}))
        (step / "config.json").write_text(json.dumps(TIES))
    return p


def _run(p: Path) -> int:
    return subprocess.run([sys.executable, str(PROGRAMS / "synth_handoff_netlist_check.py"),
                           str(p)], capture_output=True, text=True).returncode


def test_a_tool_netlist_is_judged_against_the_tool_config(tmp_path):
    good = H.check_project(_project(tmp_path / "g", CAL / "synth_const_tied_negative.v", tool=True))
    assert good["verdict"] == "PASS" and good["producer"] == "LibreLane Yosys.Synthesis"
    assert good["tie_cells"] == {"gf180mcu_fd_sc_mcu7t5v0__tieh": 1,
                                 "gf180mcu_fd_sc_mcu7t5v0__tiel": 1}


def test_a_direct_netlist_without_hilomap_fails_on_its_constants(tmp_path):
    """The lesson yosys_hilomap_required_check / _script_template_check held as
    recipe TEXT (no hilomap -> DRT-0305) is now read off the netlist, so the
    direct arm that non-chip designs keep is still judged."""
    p = _project(tmp_path, CAL / "synth_const_no_hilomap_positive.v", tool=False)
    report = H.check_project(p)
    assert report["producer"].startswith("direct recipe")
    assert report["verdict"] == "FAIL"
    assert any(f.startswith("CONSTANT_NOT_TIED") for f in report["findings"])
    assert _run(p) == 1


def test_an_undefined_constant_is_refused(tmp_path):
    """RULE 1 of the tie-cell recipe: an `x` that setundef never resolved."""
    src = tmp_path / "x.v"
    src.write_text((CAL / "synth_const_tied_negative.v").read_text()
                   .replace("endmodule", "  assign y = 1'hx;\nendmodule", 1))
    report = H.check_project(_project(tmp_path, src, tool=False))
    assert any(f.startswith("UNDEFINED_CONSTANT") for f in report["findings"])


def test_a_netlist_that_is_not_the_tools_bytes_is_not_credited_to_the_tool(tmp_path):
    p = _project(tmp_path, CAL / "synth_const_tied_negative.v", tool=True)
    (p / "phase2/stage2/synth/cal_const_synth.v").write_text(
        (CAL / "synth_const_no_hilomap_positive.v").read_text())
    report = H.check_project(p)
    assert report["producer"].startswith("direct recipe")
    assert report["verdict"] == "FAIL"


def test_an_empty_handoff_netlist_fails(tmp_path):
    src = tmp_path / "empty.v"
    src.write_text("// nothing\n\n")
    report = H.check_project(_project(tmp_path, src, tool=False))
    assert any(f.startswith("NETLIST_EMPTY") for f in report["findings"])


def test_a_netlist_older_than_its_rtl_fails(tmp_path):
    p = _project(tmp_path, CAL / "synth_const_tied_negative.v", tool=True)
    (p / "phase2/stage1/rtl/cal_const.v").write_text("module cal_const(input a); endmodule\n")
    report = H.check_project(p)
    assert any(f.startswith("HANDOFF_STALE") for f in report["findings"])
    assert _run(p) == 1


def test_no_step9_record_is_not_measured(tmp_path):
    p = tmp_path / "proj"
    (p / "phase2/stage2/synth").mkdir(parents=True)
    assert _run(p) == 2


def test_step14_gates_on_the_handoff_netlist_not_on_recipe_text():
    import yaml
    flow = yaml.safe_load((PROGRAMS.parent / "flow/phase1_phase2_phase3.yaml").read_text())
    step = next(s for s in flow["steps"] if str(s["id"]) == "14")
    text = json.dumps(step)
    assert "synth_handoff_netlist_check ." in text
    for gone in ("yosys_hilomap_required_check", "yosys_script_template_check",
                 "yosys_tiecell_recipe_order_check"):
        assert gone not in text


def test_a_hierarchical_netlist_is_refused_when_the_recipe_flattens(tmp_path):
    """yosys_script_template_check's `-flatten` token, read off the netlist:
    without flattening the ATPG flow breaks on hierarchical names."""
    src = tmp_path / "hier.v"
    src.write_text((CAL / "synth_const_tied_negative.v").read_text()
                   + "\nmodule leftover(input a, output y);\n  assign y = a;\nendmodule\n")
    report = H.check_project(_project(tmp_path, src, tool=True))
    assert any(f.startswith("HIERARCHY_NOT_FLAT") for f in report["findings"])

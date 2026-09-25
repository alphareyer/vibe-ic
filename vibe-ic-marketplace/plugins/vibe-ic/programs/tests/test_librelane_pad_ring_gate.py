"""The PadRing tool state must reach the existing geometry audit."""
import json
from pathlib import Path

import pytest

import pad_ring_check as gate


def _tool_output(tmp_path):
    project = tmp_path / "design"
    folder = project / "phase3/librelane/01-openroad-padring"
    folder.mkdir(parents=True)
    lef = project / "io.lef"
    lef.write_text("\n".join([
        "VERSION 5.8 ;", "MACRO io_pad", " CLASS PAD ;", " SIZE 10 BY 10 ;",
        "END io_pad", "END LIBRARY", "",
    ]))
    placed = folder / "chip.def"
    placed.write_text("\n".join([
        "VERSION 5.8 ;", "DESIGN chip ;", "UNITS DISTANCE MICRONS 1000 ;",
        "DIEAREA ( 0 0 ) ( 200000 200000 ) ;", "COMPONENTS 1 ;",
        "- u_io io_pad + PLACED ( 90000 0 ) N ;", "END COMPONENTS",
        "END DESIGN", "",
    ]))
    state = folder / "state_out.json"
    state.write_text(json.dumps({"def": str(placed)}))
    assignment = {"PAD_SOUTH": ["u_io"], "PAD_EAST": [],
                  "PAD_NORTH": [], "PAD_WEST": [], "SIGNAL_MAP": {"u_io": "data"}}
    return project, state, assignment, gate.PR.IoLibrary([lef])


def test_tool_state_yields_declared_pad_and_real_lef_footprint(tmp_path):
    project, state, assignment, lib = _tool_output(tmp_path)
    report = gate._librelane_report(project, state, assignment, lib)
    assert report["program"] == "OpenROAD.PadRing"
    assert report["pads"] == [{"instance": "u_io", "master": "io_pad",
                               "orient": "N", "width_dbu": 10000,
                               "height_dbu": 10000, "side": "S", "signal": "data"}]


def test_tool_state_refuses_missing_geometry(tmp_path):
    project, state, assignment, lib = _tool_output(tmp_path)
    state.write_text(json.dumps({"def": str(project / "absent.def")}))
    with pytest.raises(ValueError, match="PADRING_TOOL_DEF_MISSING"):
        gate._librelane_report(project, state, assignment, lib)


def test_tool_state_refuses_a_declared_pad_absent_from_def(tmp_path):
    project, state, assignment, lib = _tool_output(tmp_path)
    assignment["PAD_SOUTH"].append("u_missing")
    with pytest.raises(ValueError, match="PADRING_TOOL_PAD_MISSING: u_missing"):
        gate._librelane_report(project, state, assignment, lib)

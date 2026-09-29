"""CUT_W2 (1) — step 23 on OpenROAD.STAPostPNR by default, hold in EVERY scene.

R-0929-TOOL-DEFAULT: on the chip path (a die with its own pad ring) step 23's
production default is the tool. The direct 2-corner deck timed hold only at FF
(SETUP at SS/max-RC, HOLD at FF/min-RC), and `hold_corner_coverage_check` on
the tool arm asked only "was hold analysed at a FAST corner". So an SS-scene
hold residual (spm: max_ss -0.040 ns after F13; -0.370 ns in the padin 9-scene
census) passed the hold coverage gate.

Now: the class default for step 23 is `librelane`, and the hold coverage gate
on the tool arm requires a hold analysis in every declared scene and no
negative hold slack in any of them. The tool fixtures are the F15 ones.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import librelane_contract as LC  # noqa: E402
from test_f15_step23_gates_read_the_tool_arm import (  # noqa: E402
    gate_inputs, run_gate, tool_project, verdict)
from test_librelane_contract_production_defaults import _chip  # noqa: E402

_GATE = "hold_corner_coverage_check"


def test_the_chip_path_times_step_23_on_the_tool_by_default(tmp_path):
    project = _chip(tmp_path)
    assert LC.selected_mode(project, "23") == "librelane"
    # a project may still name it direct (the switch outranks the class)
    (project / "phase3").mkdir(parents=True, exist_ok=True)
    (project / "phase3/librelane_switch.json").write_text('{"steps": {"23": "direct"}}')
    assert LC.selected_mode(project, "23") == "direct"


def test_a_design_without_a_pad_ring_keeps_the_direct_deck(tmp_path):
    assert LC.selected_mode(tmp_path, "23") == "direct"


def test_an_ss_scene_hold_violation_fails_the_hold_gate(tmp_path):
    """The padin census shape: hold met at FF, -0.370 ns at a slow scene."""
    project = gate_inputs(tool_project(tmp_path, hold={"max_ss_125C_4v50": -0.370}))
    rc, doc = run_gate(_GATE, project, tmp_path)
    assert (rc, verdict(doc)) == (1, "FAIL"), doc
    assert doc["reason"] == "HOLD_VIOLATED"
    assert doc["hold_violations"] == [
        {"corner": "max_ss_125C_4v50", "hold_worst_slack_ns": -0.37}]


def test_an_ss_scene_with_no_hold_analysis_fails(tmp_path):
    project = gate_inputs(tool_project(tmp_path, hold_inf=("nom_ss_125C_4v50",)))
    rc, doc = run_gate(_GATE, project, tmp_path)
    assert (rc, verdict(doc), doc["reason"]) == (1, "FAIL", "NO_HOLD_ANALYSIS"), doc


def test_control_hold_met_in_every_scene_passes(tmp_path):
    project = gate_inputs(tool_project(tmp_path))
    rc, doc = run_gate(_GATE, project, tmp_path)
    assert (rc, verdict(doc), doc["reason"]) == (0, "PASS", "HOLD_EVERY_SCENE"), doc
    assert len(doc["judged_corners"]) == len(doc["tool_corners"]) == 9


def test_a_declared_pdk_root_still_binds_the_tool_corners_liberty(tmp_path):
    """Proof-run finding (spm run23 copy, 0.3.86): with the PDK root DECLARED
    (switch `pdk_root_host`) its provenance names the PDK at top level and no
    image, and every tool corner read 'Liberty bytes ... were not bound', so
    the completeness gate could not place any corner. The mapping now follows
    the declared root; the bytes are still checked against the run's sha256."""
    import json
    import sta_corner_record_completeness_check  # noqa: F401  (gate under test)
    project = gate_inputs(tool_project(tmp_path))
    prov = project / "phase3/librelane_pdk_root.provenance.json"
    doc = json.loads(prov.read_text())
    prov.write_text(json.dumps({"path": doc["path"], "source": "declared",
                                "pdk": doc["derivation"]["pdk"]}))
    rc, out = run_gate("sta_corner_record_completeness_check", project, tmp_path)
    assert (rc, verdict(out)) == (0, "PASS"), out
    # ...and a liberty whose bytes changed since the run still refuses
    lib = next(p for p in (tmp_path / "pdk_root").rglob("*.lib"))
    lib.write_text(lib.read_text() + "\n/* edited */\n")
    rc, out = run_gate("sta_corner_record_completeness_check", project, tmp_path)
    assert rc == 1, out

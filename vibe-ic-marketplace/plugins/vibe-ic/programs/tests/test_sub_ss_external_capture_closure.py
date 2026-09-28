"""The chip flow can recover a pad launched register to external capture path."""

import importlib
import os
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
PLUGIN = PROGRAMS.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))

closure = importlib.import_module("_ppa.closure")
prr = importlib.import_module("librelane_postroute_repair")


def test_the_cts_step_runs_a_measured_external_capture_retap():
    steps = PROGRAMS / "librelane_plugins/librelane_plugin_vibeic"
    driver = (steps / "clock_path_drive_sizing.tcl").read_text()
    helper = steps / "external_capture_launch_retap.tcl"
    assert helper.is_file(), "the CTS step has no external capture path repair"
    assert "source [file join [file dirname [info script]] external_capture_launch_retap.tcl]" in driver
    assert driver.index("external_capture_launch_retap.tcl") < driver.index("write_views")
    body = helper.read_text()
    assert "find_timing_paths" in body and "all_outputs" in body
    assert "_vic_prects_insts" in body and "is_buffer" in body
    assert "estimate_parasitics -placement" in body
    assert "disconnect" in body and "connect $oldnet" in body
    assert not any(name in body for name in ("subservient", "o_sram", "i_clk", "gf180"))


def test_size_only_postroute_candidate_is_bound_to_the_real_actuator():
    reg = closure.load_registry(PLUGIN / "config/ppa_actuator_registry.yaml")
    assert reg.verify_bindings() == []
    actuator = reg.actuators["timing.repair_setup"]
    bound = actuator.bind_params({"setup_sequence": "sizeup,swap"})
    assert bound["setup_sequence"] == "sizeup,swap"
    assert prr.PARAM_VARS["setup_sequence"] == "VIBEIC_PRR_SETUP_SEQUENCE"
    argv = actuator.build_argv(Path("/tmp/ssfixcx-actuator-test"), bound)
    assert "--setup-sequence" in argv and "sizeup,swap" in argv
    assert any("sizeup,swap" == rung.get("setup_sequence")
               for rung in reg.controllers["postroute.repair_setup"].plan)
    step = PROGRAMS / "librelane_plugins/librelane_plugin_vibeic/postroute_repair.tcl"
    body = step.read_text()
    assert 'if {$::env(VIBEIC_PRR_SETUP_SEQUENCE) eq "sizeup,swap"}' in body
    assert "lappend setup_args -sequence $::env(VIBEIC_PRR_SETUP_SEQUENCE)" in body


def test_setup_controller_reaches_size_only_after_two_tool_written_stagnations(
    tmp_path, monkeypatch
):
    from test_t102_librelane_postroute_repair import _cand, _controller, _scenario_impl

    project, arm, impl, shim = _scenario_impl(
        tmp_path, baseline=(-0.5, 0.2),
        candidates=[_cand(-0.6, 0.2), _cand(-0.6, 0.2), _cand(0.15, 0.2)]
    )
    run = _controller(impl, arm, shim, monkeypatch, tmp_path).run_controller(
        "postroute.repair_setup"
    )
    assert [it.decision for it in run.iterations] == [
        "ROLLED_BACK", "ROLLED_BACK", "PROMOTED"
    ]
    assert run.outcome is closure.Outcome.CONVERGED
    reg = closure.load_registry(PLUGIN / "config/ppa_actuator_registry.yaml")
    plan = reg.controllers["postroute.repair_setup"].plan
    # Preserve the landed low-cost rungs, then try the 0.2 ns tool-native
    # sizing move that recovered the extracted SS retap scene.
    assert plan[0]["setup_margin_ns"] == 0
    assert plan[1]["setup_margin_ns"] == 0.05
    assert plan[2]["setup_margin_ns"] == 0.2
    assert plan[2]["setup_sequence"] == "sizeup,swap"


def test_cts_considers_output_path_within_five_percent_of_clock_period():
    """A routed risk can be hidden by the CTS placement estimate."""
    helper = (PROGRAMS / "librelane_plugins/librelane_plugin_vibeic"
              / "external_capture_launch_retap.tcl")
    stub = r'''
set _vic_prects_insts [dict create]
proc all_clocks {} { return clk0 }
proc all_outputs {} { return out0 }
proc all_registers {} { return {} }
proc find_timing_paths {args} { return path0 }
proc get_cells {args} { return {} }
proc get_property {obj field} {
    if {$field eq "is_propagated"} { return 1 }
    if {$field eq "period"} { return 20.0 }
    if {$field eq "slack"} { return $::env(MOCK_SLACK) }
    if {$field eq "startpoint"} { return pin0 }
    error "unexpected property $obj/$field"
}
namespace eval ord { proc get_db_block {} { return block0 } }
source $::env(RETAP_HELPER)
'''
    for slack, should_consider in (("0.387953", True), ("2.0", False)):
        run = subprocess.run(
            ["tclsh"], input=stub, text=True, capture_output=True,
            env={**os.environ, "RETAP_HELPER": str(helper), "MOCK_SLACK": slack},
            check=False, timeout=10,
        )
        assert run.returncode == 0, run.stderr
        if should_consider:
            assert "VIC_RETAP margin=1.0 candidates=0" in run.stdout
        else:
            assert "VIC_RETAP none:" in run.stdout

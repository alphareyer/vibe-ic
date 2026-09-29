"""DRV sign-off standard section 1: the flow PRODUCES the stage receipts.

`drv_capture_plan._stages` reads `reports/phase3/drv_stages/<stage>.json`.
Before this, no stage wrote one, so every real run FAILed "required stage
absent".  These tests drive the producer the way the flow does:

* `librelane_contract.run_chain` with a stand-in `docker` that executes the
  shipped probe (`drv_stage_probe.tcl`) in a real Tcl interpreter against a
  stand-in OpenROAD / OpenSTA command set (only the tool's writes are fake);
* the plan reader over the receipts that produced, including the run-instance
  binding (a receipt from another run is refused);
* the SPECIALNETS zero-wiring geometry proof (R-0928-DRV-IC).
"""
from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import drv_capture_plan as plan  # noqa: E402
import drv_signoff_judge as drv  # noqa: E402
import drv_stage_receipts as receipts  # noqa: E402
import librelane_contract as ll  # noqa: E402

TCLSH = shutil.which("tclsh")

#: A stand-in `docker` for `python3 -m librelane.steps run`: it runs the
#: step's command sequence in tclsh after sourcing the init file the real tool
#: would source ($HOME/.openroad, or $HOME/.sta for the sign-off STA binary).
#: The stage commands are stand-ins that record their calls; the probe is the
#: shipped file, executed.
_FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
if "librelane.steps" not in argv:
    sys.exit(0)
out = argv[argv.index("-o") + 1]
os.makedirs(out, exist_ok=True)
step = argv[argv.index("--id") + 1]
home = next((a[5:] for a in argv if a.startswith("HOME=")), None)
cfg = json.load(open(argv[argv.index("-c") + 1]))
fanout = cfg.get("FAKE_SDC_FANOUT", 4)
sta = step == "OpenROAD.STAPostPNR"
tcl = []
if not sta:
    tcl.append("namespace eval ::ord {}")
tcl.append(f"set ::env(SAVE_SDC) {{{out}/top.sdc}}")
tcl.append(f"set ::fo {fanout}")
tcl.append(r"""
set ::redirect ""
proc write_sdc {args} {
    set f [open [lindex $args end] w]
    puts $f "create_clock -name clk -period 24 \[get_ports clk\]"
    puts $f "set_max_fanout $::fo \[current_design\]"
    puts $f "set_max_transition 3 \[current_design\]"
    puts $f "set_max_capacitance 0.2 \[current_design\]"
    close $f
}
proc emit {text} {
    if {$::redirect eq ""} { puts $text } else {
        set f [open $::redirect a]; puts $f $text; close $f }
}
proc report_check_types {args} {
    emit "max fanout\n\nPin u1/Z\nmax fanout $::fo\nfanout 5\n-----------\nSlack -1 (VIOLATED)"
}
namespace eval ::sta {}
proc ::sta::redirect_file_append_begin {path} { set ::redirect $path }
proc ::sta::redirect_file_end {} { set ::redirect "" }
proc ::sta::max_fanout_check_limit {} { return $::fo }
proc ::sta::max_slew_violation_count {} { return 0 }
proc ::sta::max_capacitance_violation_count {} { return 0 }
proc ::sta::max_fanout_violation_count {} { return 1 }
proc record {command args} {
    set probe [file join [file dirname $::env(SAVE_SDC)] vibeic_drv_stage]
    set f [open [file join [file dirname $::env(SAVE_SDC)] calls.log] a]
    puts $f [list $command $args snapshot_before=[llength [glob -nocomplain $probe/*.pre.sdc]]]
    close $f
}
proc repair_design {args} { record repair_design {*}$args; return repaired }
proc clock_tree_synthesis {args} { record clock_tree_synthesis {*}$args }
proc all_clocks {} { return clk }
proc get_property {obj prop} {
    if {$prop eq "is_propagated"} { return $::propagated }
    return $obj
}
set ::propagated 0
proc set_propagated_clock {clocks} { set ::propagated 1 }
proc unset_propagated_clock {clocks} { set ::propagated 0 }
proc estimate_parasitics {args} {}
if {[namespace exists ::ord]} { proc ::ord::get_db_block {} { return blk } }
proc blk {method} { return {net0} }
proc net0 {method} {
    switch $method {
        getSigType { return CLOCK }
        getName { return clknet_0 }
        getITerms { return {itd it1 it2} }
        getBTerms { return {} }
    }
}
proc iterm {self method} {
    switch $method {
        isOutputSignal { return [expr {$self eq "itd"}] }
        isInputSignal { return [expr {$self ne "itd"}] }
        getInst { return inst_$self }
        getMTerm { return mterm_$self }
    }
}
foreach it {itd it1 it2} {
    proc $it {method} "return \[iterm $it \$method\]"
    proc inst_$it {method} "return buf_$it"
    proc mterm_$it {method} "return Z"
}
""")
if home:
    tcl.append(f"source {{{os.path.join(home, '.sta' if sta else '.openroad')}}}")
if step in ("OpenROAD.RepairDesignPostGPL", "OpenROAD.RepairDesignPostGRT",
            "Vibeic.PostRouteRepair"):
    tcl.append("puts [repair_design -verbose -slew_margin 20]")
elif step == "OpenROAD.CTS":
    tcl.append("clock_tree_synthesis -root_buf clkbuf_16 -sink_clustering_size 4 "
               "-sink_clustering_enable")
    tcl.append("if {$::propagated} { error {probe left the clock propagated} }")
elif sta:
    tcl.append("report_check_types -max_slew -max_capacitance -max_fanout "
               "-violators -corner nom_tt")
tcl.append("write_sdc $::env(SAVE_SDC)")
script = os.path.join(out, "fake_tool.tcl")
open(script, "w").write("\n".join(tcl) + "\n")
run = subprocess.run(["tclsh", script], capture_output=True, text=True)
open(os.path.join(out, "fake_tool.log"), "w").write(run.stdout + run.stderr)
if run.returncode:
    sys.exit(run.returncode)
state = json.load(open(argv[argv.index("-i") + 1]))
state["metrics"] = {}
json.dump(state, open(os.path.join(out, "state_out.json"), "w"))
'''


def _fake_docker(tmp_path, monkeypatch):
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(_FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setitem(ll._CAPABILITY, ("img", str(docker)),
                        {"image": "img", "openroad_aliases": {}, "tcl_probe": "MEASURED"})
    return docker


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "phase3").mkdir(parents=True)
    view = project / "d.def"
    view.write_text("DESIGN x ;\n")
    state = project / "state_in.json"
    state.write_text(json.dumps({"odb": str(view), "def": str(view), "nl": str(view),
                                 "sdc": str(view), "metrics": {}}))
    return project, state


def _config(project, step, **extra):
    path = project / f"{step}.json"
    path.write_text(json.dumps({"meta": {"step": step}, **extra}))
    return path


def _run(project, docker, state, *steps, lane="l"):
    return ll.run_chain(project, "img",
                        [(step, _config(project, step, **extra), state)
                         for step, extra in steps],
                        docker=str(docker), lane=lane)


pytestmark = pytest.mark.skipif(TCLSH is None, reason="tclsh absent")


def test_a_stage_chain_installs_the_probe_for_openroad_and_sta(tmp_path, monkeypatch):
    docker = _fake_docker(tmp_path, monkeypatch)
    project, state = _project(tmp_path)
    receipts.claim(project)
    folder = _run(project, docker, state, ("OpenROAD.RepairDesignPostGPL", {}))[0]
    home = project / "phase3/librelane/l/.openroad_home"
    probe = receipts.PROBE_TCL.read_text()
    for init in (".openroad", ".sta"):
        assert probe.strip() in (home / init).read_text(), init
    fingerprint = json.loads((folder / "input_fingerprint.json").read_text())
    assert fingerprint["drv_stage_probe"] == receipts.probe_digest()
    # The snapshot is taken in the stage's process BEFORE the command runs.
    calls = (folder / "calls.log").read_text().splitlines()
    assert calls == ["repair_design {-verbose -slew_margin 20} snapshot_before=1"]
    # ...and the wrapped command still returns its own result.
    assert "repaired" in (folder / "fake_tool.log").read_text()


def test_a_chain_without_a_stage_step_gets_no_probe(tmp_path, monkeypatch):
    docker = _fake_docker(tmp_path, monkeypatch)
    project, state = _project(tmp_path)
    receipts.claim(project)
    folder = _run(project, docker, state, ("OpenROAD.CheckAntennas", {}))[0]
    assert "drv_stage_probe" not in json.loads((folder / "input_fingerprint.json").read_text())
    assert not (project / "phase3/librelane/l/.openroad_home").exists()


def test_run_chain_writes_receipts_the_plan_reads_from_tool_bytes(tmp_path, monkeypatch):
    docker = _fake_docker(tmp_path, monkeypatch)
    project, state = _project(tmp_path)
    run_id = receipts.claim(project)
    _run(project, docker, state, ("OpenROAD.RepairDesignPostGPL", {"FAKE_SDC_FANOUT": 10}),
         ("OpenROAD.CTS", {}), ("OpenROAD.STAPostPNR", {}))
    stored = {name: json.loads((project / receipts.RECEIPT_DIR / f"{name}.json").read_text())
              for name in ("placement_repair", "cts", "signoff_sta")}
    assert all(doc["run_id"] == run_id for doc in stored.values())
    for doc in stored.values():
        for key in ("behavior_report", "sdc_snapshot", "command_args"):
            assert drv._sha(Path(doc[key]["path"])) == doc[key]["sha256"], key
        assert "applied" not in doc  # a receipt carries references, never values
    rows, inventory = plan._stages(project, False)
    rows = {row["name"]: row for row in rows}
    assert inventory["synth"]["status"] == "absent"
    assert inventory["post_grt_repair"]["status"] == "absent"
    # The applied value is the tool's pre-command SDC, 10 here, not the 4
    # the design declares: the judge sees a stage broader than declared.
    assert rows["placement_repair"]["applied"] == {"fanout": 10.0, "slew_ns": 3.0,
                                                   "cap_pf": 0.2}
    assert rows["placement_repair"]["fanout_check_limit"] == 10.0
    assert rows["signoff_sta"]["applied"]["fanout"] == 4.0
    assert rows["cts"]["cts_parameters"] == {"root_buf": "clkbuf_16",
                                             "sink_clustering_size": "4",
                                             "sink_clustering_enable": True}
    assert rows["cts"]["clock_driver_fanout"] == [
        {"driver": "buf_itd/Z", "net": "clknet_0", "fanout": 2}]
    behavior = Path(rows["cts"]["behavior_report"]["path"]).read_text()
    assert drv._COMMAND in behavior and "max_fanout violators=1" in behavior


def test_a_receipt_from_another_run_is_refused(tmp_path, monkeypatch):
    docker = _fake_docker(tmp_path, monkeypatch)
    project, state = _project(tmp_path)
    receipts.claim(project)
    _run(project, docker, state, ("OpenROAD.CTS", {}))
    receipt = project / receipts.RECEIPT_DIR / "cts.json"
    stale = receipt.read_text()
    receipts.claim(project)          # a new run starts: its directory is empty
    assert not receipt.exists()
    receipt.write_text(stale)        # an old receipt copied back in
    rows, inventory = plan._stages(project, False)
    assert inventory["cts"]["status"] == "recorded by another run"
    assert "cts" not in {row["name"] for row in rows}


def test_no_claim_means_no_receipt(tmp_path, monkeypatch):
    docker = _fake_docker(tmp_path, monkeypatch)
    project, state = _project(tmp_path)
    _run(project, docker, state, ("OpenROAD.CTS", {}))
    assert not (project / receipts.RECEIPT_DIR / "cts.json").exists()
    assert plan._stages(project, False)[1]["cts"]["status"] == "absent"


def test_step32_rebinds_signoff_and_repair_to_the_final_state(tmp_path, monkeypatch):
    docker = _fake_docker(tmp_path, monkeypatch)
    project, state = _project(tmp_path)
    receipts.claim(project)
    base = _run(project, docker, state, ("Vibeic.PostRouteRepair", {"FAKE_SDC_FANOUT": 4}),
                ("OpenROAD.STAPostPNR", {"FAKE_SDC_FANOUT": 4}), lane="32-base")
    _run(project, docker, state, ("Vibeic.PostRouteRepair", {"FAKE_SDC_FANOUT": 6}),
         ("OpenROAD.STAPostPNR", {"FAKE_SDC_FANOUT": 6}), lane="32-cand01")
    # The last chain to run was the candidate; step 32 kept the base.
    report = {"adopted": "32-base", "adopted_state": str(base[0] / "state_out.json"),
              "final": {"sta_state": str(base[1] / "state_out.json")}}
    receipts.record_step32(project, report)
    rows = {row["name"]: row for row in plan._stages(project, True)[0]}
    assert rows["signoff_sta"]["applied"]["fanout"] == 4.0
    assert rows["postroute_repair"]["applied"]["fanout"] == 4.0
    doc = json.loads((project / receipts.RECEIPT_DIR / "signoff_sta.json").read_text())
    assert doc["tool_step"]["folder"] == str(base[1].resolve())


def test_a_gated_out_stage_is_recorded_as_not_run_and_fails(tmp_path):
    project = tmp_path / "proj"
    receipts.claim(project)
    receipts.record_not_run(project, "post_grt_repair", "gate false")
    rows = {row["name"]: row for row in plan._stages(project, False)[0]}
    assert rows["post_grt_repair"]["ran"] is False
    bundle = {"stages": list(rows.values())}
    fails: list = []
    # The judge's own stage rule: a stage that did not run is FAIL.
    result = drv.judge({"identity": {}, "frozen": {}, "current": {}, **bundle,
                        "pins": {}, "scenes": []})
    fails = result["failures"]
    assert "post_grt_repair: did not run or lacks DRV behavior report" in fails


# --- synthesis -----------------------------------------------------------------

def test_synth_keeps_the_script_abc_executed(tmp_path):
    project = tmp_path / "proj"
    receipts.claim(project)
    synth = project / "phase2/stage2/synth"
    kept = synth / "_tmp_yosys-abc-Qa3IyE"
    kept.mkdir(parents=True)
    (kept / "abc.script").write_text("echo + buffer -N 4;\nbuffer -N 4;\n")
    log = ("Running ABC script: <abc-temp-dir>/abc.script\n"
           "Running ABC script: _tmp_yosys-abc-Qa3IyE/abc.script\n")
    path = receipts.keep_abc_script(project, synth, log)
    assert path == receipts.abc_script_path(project)
    assert path.read_text() == "echo + buffer -N 4;\nbuffer -N 4;\n"
    assert not kept.exists(), "yosys's kept temp folder is removed"
    netlist = synth / "top_synth.v"
    netlist.write_text("module top; endmodule\n")
    sdc = project / "constraint.sdc"
    sdc.write_text("set_max_fanout 4 [current_design]\n")
    behavior = path.parent / "behavior.rpt"
    behavior.write_text("clocks 1\nclock clk is_propagated=0\n" + drv._COMMAND + "\n"
                        + "".join(f"{k} violators=0\n" for k in drv.KINDS))
    receipts.record_synth(project, abc_script=path, behavior=behavior,
                          netlist=netlist, sdc=sdc)
    row = plan._stages(project, False)[0][0]
    assert row["name"] == "synth" and row["ran"]
    assert row["applied"] == {"fanout": 4.0}
    assert row["synth_abc_buffering"] and row["ideal_clock_excluded"]


def test_synth_flags_come_from_evidence_not_the_receipt(tmp_path):
    project = tmp_path / "proj"
    run_id = receipts.claim(project)
    folder = project / "evidence"
    folder.mkdir(parents=True)
    script = folder / "abc.script"
    script.write_text("strash\n&nf\n&put\n")          # no buffering at all
    behavior = folder / "behavior.rpt"
    behavior.write_text("clocks 1\nclock clk is_propagated=1\n" + drv._COMMAND + "\n")
    (project / receipts.RECEIPT_DIR / "synth.json").write_text(json.dumps({
        "name": "synth", "run_id": run_id, "ran": True,
        "abc_script": {"path": str(script), "sha256": drv._sha(script)},
        "behavior_report": {"path": str(behavior), "sha256": drv._sha(behavior)},
        # prose claims the evidence contradicts
        "synth_abc_buffering": True, "ideal_clock_excluded": True,
        "applied": {"fanout": 4}}))
    row = plan._stages(project, False)[0][0]
    assert row["applied"] == {"fanout": None}
    assert row["synth_abc_buffering"] is False
    assert row["ideal_clock_excluded"] is False


def test_every_synth_abc_call_keeps_its_script():
    """All four yosys command sites (default read, slang, sv2v, retry) carry
    the flags that make yosys keep and name the script ABC ran."""
    source = (_PROGRAMS / "phase3_one_shot_runner.py").read_text()
    sites = source.count("abc -liberty {liberty_c}{_abc_timing}{_abc_fanout}")
    assert sites == 4
    assert source.count("abc -liberty {liberty_c}{_abc_timing}{_abc_fanout}{_abc_keep}; ") == 4
    assert '_abc_keep = " -showtmp -nocleanup"' in source


# --- SPECIALNETS zero-wiring proof (R-0928-DRV-IC) ------------------------------

def _pad_scene(tmp_path, def_body):
    from test_drv_signoff_judge import _port_pad_pins, _file
    folder = tmp_path / "scene"
    _file(folder, "annotation.rpt", "Found 1 unannotated drivers.\n p\n"
          "Found 0 partially unannotated drivers.\n")
    lef = _file(tmp_path, "pad.lef", "MACRO pad\n PIN PAD\n USE SIGNAL ;\n END PAD\nEND pad\n")
    lib = _file(tmp_path, "pad.lib", '''library (lib) {
 time_unit : "1ns"; capacitive_load_unit (1, pf);
 cell (pad) { pad_cell : true;
  pin (PAD) { direction : input; timing () { related_pin : "PAD"; } }
 }
}''')
    routed = _file(tmp_path, "route.def", def_body)
    spef = _file(tmp_path, "route.spef", "*D_NET other 0.1\n*END\n")
    return (folder, _port_pad_pins(), [lib], [lef], Path(routed["path"]),
            Path(spef["path"]))


_NETS = "NETS 1 ;\n - n1 ( u A ) ( v Z ) + ROUTED Metal1 ( 0 0 ) ( 1 0 ) ;\nEND NETS\n"


def _derive(tmp_path, special):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from drv_signoff_annotation import derive
    return derive(*_pad_scene(tmp_path, _NETS + "SPECIALNETS 1 ;\n" + special
                              + "END SPECIALNETS\n"))


def test_a_specialnets_port_pad_net_with_no_wiring_is_proven(tmp_path):
    result = _derive(tmp_path, " - p ( PIN p ) ( u PAD ) + USE SIGNAL ;\n")
    assert result["unresolved"] == []
    assert result["resolved"] == [{"pin": "p", "net": "p",
                                   "reason": "port_pad_zero_routed_segments",
                                   "def_sections": ["SPECIALNETS"],
                                   "def_endpoints": ["p", "u/PAD"]}]


@pytest.mark.parametrize("special", [
    " - p ( PIN p ) ( u PAD ) + ROUTED Metal2 10 ( 0 0 ) ( 10 0 ) + USE SIGNAL ;\n",
    " - p ( PIN p ) ( u PAD ) + RECT Metal2 ( 0 0 ) ( 10 10 ) + USE SIGNAL ;\n",
    " - p ( PIN p ) ( u PAD ) + POLYGON Metal2 ( 0 0 ) ( 1 0 ) ( 1 1 ) + USE SIGNAL ;\n",
    " - p ( PIN p ) ( u PAD ) + VIA via1 N ( 0 0 ) + USE SIGNAL ;\n",
    " - p ( PIN p ) ( u PAD ) + SHIELD VDD Metal2 10 ( 0 0 ) ( 10 0 ) ;\n",
    # the pins are not exactly its endpoints
    " - p ( PIN p ) + USE SIGNAL ;\n",
    " - p ( PIN p ) ( u PAD ) ( w A ) + USE SIGNAL ;\n",
    " - p ( PIN p ) ( * PAD ) + USE SIGNAL ;\n",
])
def test_specialnets_wiring_or_other_endpoints_is_not_a_proof(tmp_path, special):
    result = _derive(tmp_path, special)
    assert result["resolved"] == []
    assert [row["pin"] for row in result["unresolved"]] == ["p"]


def test_a_net_routed_in_nets_is_not_proven_by_its_empty_specialnet(tmp_path):
    from drv_signoff_annotation import derive
    body = ("NETS 1 ;\n - p ( PIN p ) ( u PAD ) + ROUTED Metal2 ( 0 0 ) ( 10 0 ) ;\n"
            "END NETS\nSPECIALNETS 1 ;\n - p ( PIN p ) ( u PAD ) + USE SIGNAL ;\n"
            "END SPECIALNETS\n")
    result = derive(*_pad_scene(tmp_path, body))
    assert [row["pin"] for row in result["unresolved"]] == ["p"]


def test_a_copied_run_rebases_report_evidence_with_its_reports_folder(tmp_path):
    """Receipt evidence lives under reports/phase3/; a path recorded on the
    producer host must keep that `reports` component when the copied project
    is evaluated elsewhere (the plan otherwise reads a phase3/ path that
    does not exist and the stage turns absent)."""
    copy = tmp_path / "copy"
    recorded = "/elsewhere/run/spm/reports/phase3/drv_stages/evidence/synth/abc.script"
    assert plan._run_path(copy, recorded) == copy / "reports/phase3/drv_stages/evidence/synth/abc.script"
    step = "/elsewhere/run/spm/phase3/librelane/32-base/04-openroad-stapostpnr/state_out.json"
    assert plan._run_path(copy, step) == copy / "phase3/librelane/32-base/04-openroad-stapostpnr/state_out.json"
    inside = copy / "reports/phase3/x.json"
    assert plan._run_path(copy, str(inside)) == inside


def test_synth_stage_runs_the_census_deck_and_binds_the_receipt(tmp_path):
    project = tmp_path / "proj"
    receipts.claim(project)
    synth = project / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    (synth / "_tmp_yosys-abc-A1/").mkdir()
    (synth / "_tmp_yosys-abc-A1/abc.script").write_text("buffer -N 4;\n")
    receipts.keep_abc_script(project, synth,
                             "Running ABC script: _tmp_yosys-abc-A1/abc.script\n")
    netlist = synth / "top_synth.v"
    netlist.write_text("module top; endmodule\n")
    sdc = project / "c.sdc"
    sdc.write_text("create_clock -period 24 [get_ports clk]\nset_max_fanout 4 [current_design]\n")
    decks = []

    def execute(cmd):              # stands in for OpenSTA: writes what it writes
        deck = Path(cmd.split()[-1])
        decks.append(deck.read_text())
        out = Path(deck.read_text().split("synth_census {")[1].split("}")[0])
        out.write_text("clocks 1\nclock clk is_propagated=0\n" + drv._COMMAND + "\n"
                       + "".join(f"{k} violators=0\n" for k in drv.KINDS))
        return 0, "", ""

    receipts.synth_stage(project, netlist=netlist, top="top", liberties=["/l.lib"],
                         sdc=sdc, to_container=str, execute=execute)
    assert "read_sdc {" + str(sdc) + "}" in decks[0] and "link_design {top}" in decks[0]
    row = plan._stages(project, False)[0][0]
    assert row["ran"] and row["applied"] == {"fanout": 4.0}
    assert row["ideal_clock_excluded"] is True
    # A census the tool did not finish is no behaviour evidence.
    receipts.synth_stage(project, netlist=netlist, top="top", liberties=["/l.lib"],
                         sdc=sdc, to_container=str, execute=lambda cmd: (1, "", "err"))
    assert "behavior_report" not in plan._stages(project, False)[0][0]

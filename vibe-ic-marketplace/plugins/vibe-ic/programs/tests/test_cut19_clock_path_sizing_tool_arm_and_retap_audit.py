"""CUT_W4 step 19 (R-0929-TOOL-DEFAULT): the tool sizes the clock path, and the
own retap sub-step is audited.

1. The clock-path sizing is OpenROAD's `repair_timing -phases GLOBAL_SIZING`
   with the clock network included (`Vibeic.ClockNetworkGlobalSizing`), the
   default arm; vibe-ic's #2160 swapMaster search stays selectable (harvest,
   then delete). MEASURED on spm x gf180mcuD (vibeic-eda 0.3.86, lane tailb):
   unrestricted, the tool re-mastered 76 CTS clkbuf_8 into DATA buffers, so the
   step holds the CTS-built instances dont_touch for the pass (vibeic/OpenROAD
   PR #38 makes the resizer keep a clock buffer a clock buffer).
2. `retap_audit_check` re-decides every retained retap from the numbers the
   step printed and matches it to the netlist change the step made. The logs
   below are the REAL step logs the calibration pair uses; the netlists are
   the step's view files, written the way OpenROAD `write_verilog` writes
   them (the only EDA output faked here).
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import librelane_cts_hold as cts  # noqa: E402
import librelane_contract as contract  # noqa: E402
try:        # absent before CUT_W4: every audit test then fails on its own line
    import retap_audit_check as RA  # noqa: E402
except ImportError:  # pragma: no cover - the RED arm
    RA = None

PLUGIN = PROGRAMS / "librelane_plugins/librelane_plugin_vibeic"
CAL = PROGRAMS / "calibration"
KEEP_LOG = (CAL / "retap_keep_positive.log").read_text()
REJECT_LOG = (CAL / "retap_reject_negative.log").read_text()

NL_IN = """module chip_top (clk, o);
 input clk;
 output o;
 gf_clkbuf_4 clkbuf_0_clk (.I(clk),
    .Z(clknet_0_i_clk__core));
 gf_clkbuf_4 clkbuf_leaf_17_clk (.I(clknet_0_i_clk__core),
    .Z(clknet_leaf_17_i_clk__core));
 gf_dffq_1 \\u_core/_1753_  (.CLK(clknet_leaf_17_i_clk__core),
    .D(n1),
    .Q(o));
 gf_dffq_1 \\u_core/_1683_  (.CLK(clknet_leaf_17_i_clk__core),
    .D(n2),
    .Q(n3));
 gf_filltie TAP_1 ();
endmodule
"""
NL_KEPT = NL_IN.replace(
    "\\u_core/_1753_  (.CLK(clknet_leaf_17_i_clk__core)",
    "\\u_core/_1753_  (.CLK(clknet_0_i_clk__core)")


def _project(tmp_path, log, nl_out, nl_in=NL_IN, corners=("nom",)):
    project = tmp_path / "proj"
    folder = project / "phase3/librelane/19-cts-hold/03-vibeic-externalcapturelaunchretap"
    inputs = project / "inputs"
    inputs.mkdir(parents=True)
    state = {}
    for key, contents in (("nl", nl_in), ("odb", "captured input ODB\n"),
                          ("def", "captured input DEF\n"),
                          ("sdc", "create_clock -period 20 [get_ports clk]\n")):
        path = inputs / ("input." + key)
        path.write_text(contents)
        state[key] = str(path)
    (inputs / "cells.lib").write_text('library(neutral) { time_unit : "1ns"; }\n')
    initial = inputs / "state.json"
    initial.write_text(json.dumps(state))
    config = inputs / "config.json"
    config.write_text(json.dumps({"meta": {"step": RA.STEP_ID},
                                 "PNR_SDC_FILE": state["sdc"], "PNR_CORNERS": list(corners),
                                 "CELL_LIBS": {c: [str(inputs / "cells.lib")] for c in corners}}))
    (project / "phase3").mkdir()
    (project / "phase3/librelane_switch.json").write_text(json.dumps(
        {"steps": {"19": "librelane", "20": "librelane"}}))

    def captured_native(cmd, **kwargs):
        # No native process: only the real producer's execution edge is replaced.
        assert cmd[cmd.index('--id') + 1] == RA.STEP_ID
        target = Path(cmd[cmd.index('-o') + 1])
        (target / "state_in.json").write_text(initial.read_text())
        (target / "config.json").write_text(config.read_text())
        outputs = {}
        for key, contents in (("nl", nl_out), ("odb", "captured output ODB\n"),
                              ("def", "captured output DEF\n")):
            path = target / ("chip_top.nl.v" if key == "nl" else "output." + key)
            path.write_text(contents)
            outputs[key] = str(path)
        outputs['sdc'] = state['sdc']
        (target / "state_out.json").write_text(json.dumps(outputs))
        (target / RA.LOG_NAME).write_text(log)
        return subprocess.CompletedProcess(cmd, 0, "captured native execution\n", "")

    # Captured preceding steps preserve the original proof's exact folder.
    # The actual run_chain retains fingerprints, native execution and view hashes.
    passthrough = inputs / "passthrough.json"
    passthrough.write_text(json.dumps({"meta": {"step": "OpenROAD.CTS"}}))
    sizing = inputs / "sizing.json"
    sizing.write_text(json.dumps({"meta": {"step": cts.CLOCK_PATH_SIZING_ARMS["tool"]}}))
    downstream = []
    for index, (step, scene_corners) in enumerate(
            [("OpenROAD.ResizerTimingPostCTS", list(corners))]
            + [("OpenROAD.STAMidPNR", [c]) for c in corners]):
        path = inputs / (step + ("@" + scene_corners[0] if index > 1 else "") + ".json")
        cfg = json.loads(config.read_text())
        cfg["meta"]["step"] = step
        cfg["PNR_CORNERS"] = scene_corners
        path.write_text(json.dumps(cfg))
        downstream.append((step, path, initial))
    def native(cmd, **kwargs):
        if cmd[cmd.index('--id') + 1] == RA.STEP_ID:
            return captured_native(cmd, **kwargs)
        target = Path(cmd[cmd.index('-o') + 1])
        current = Path(cmd[cmd.index('-i') + 1]).read_text()
        (target / "state_in.json").write_text(current)
        (target / "state_out.json").write_text(current)
        shutil.copyfile(Path(cmd[cmd.index('-c') + 1]), target / "config.json")
        return subprocess.CompletedProcess(cmd, 0, "captured preceding step\n", "")
    steps = [("OpenROAD.CTS", passthrough, initial),
             (cts.CLOCK_PATH_SIZING_ARMS["tool"], sizing, initial),
             (RA.STEP_ID, config, initial)] + downstream
    with patch.object(contract, "image_capability", return_value={}), \
            patch.object(contract, "run_container", side_effect=native):
        produced = contract.run_chain(project, "captured-image", steps,
                                      lane="19-cts-hold", pdk_root="/pdk")
    chain_folders, produced = produced, produced[:3]
    assert produced[-1] == folder
    # Preserve the original proof's input pathname; it aliases the actual input.
    (folder / "in.nl.v").symlink_to(inputs / "input.nl")
    outputs = json.loads((folder / "state_out.json").read_text())
    receipt = project / "reports/phase3/librelane_cts_hold_handoff.json"
    receipt.parent.mkdir(parents=True)
    views = {}
    for name, key in (("post_cts_def", "def"), ("post_hold_def", "def"),
                      ("post_hold_odb", "odb")):
        source = Path(outputs[key])
        dest = project / "adopted" / name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, dest)
        views[name] = {"source": str(source), "source_sha256": contract.digest(source),
                       "dest": str(dest.relative_to(project)), "dest_sha256": contract.digest(dest)}
    receipt.write_text(json.dumps({"chain": {step: str(path.relative_to(project))
                                           for (step, _, _), path in zip(steps, chain_folders)},
        "steps": [step for step, _, _ in steps], "corners": list(corners),
        "image": "captured-image", "modes": cts.modes(project),
        "selected": "librelane", "selection": {"selection": "librelane"},
        "clock_path_sizing": {"arm": "tool", "step": cts.CLOCK_PATH_SIZING_ARMS["tool"]},
        "measured_state": str((chain_folders[-1] / "state_out.json").relative_to(project)),
        "measured_state_sha256": contract.digest(chain_folders[-1] / "state_out.json"), "views": views}))
    return project


def _run(project):
    rc = RA.main([str(project), "--json", "reports/phase3/gates/retap_audit.json"])
    doc = json.loads((project / "reports/phase3/gates/retap_audit.json").read_text())
    return rc, doc


# ------------------------------------------------ 1. the tool sizing arm ---

def test_the_default_clock_path_sizing_is_the_tools_global_sizing(tmp_path):
    assert cts.clock_path_sizing_arm(tmp_path) == "tool"
    assert cts.CLOCK_PATH_SIZING_ARMS["tool"] == "Vibeic.ClockNetworkGlobalSizing"
    (tmp_path / "phase3").mkdir()
    (tmp_path / "phase3/librelane_switch.json").write_text(
        json.dumps({"arms": {"19.clock_path_sizing": "own"}}))
    assert cts.clock_path_sizing_arm(tmp_path) == "own"
    (tmp_path / "phase3/librelane_switch.json").write_text(
        json.dumps({"arms": {"19.clock_path_sizing": "guess"}}))
    with pytest.raises(contract.Refusal):
        cts.clock_path_sizing_arm(tmp_path)


def test_the_tool_step_runs_openroad_global_sizing_on_the_clock_network():
    init = (PLUGIN / "__init__.py").read_text()
    assert 'id = "Vibeic.ClockNetworkGlobalSizing"' in init
    assert "class ClockNetworkGlobalSizing(ResizerTimingPostCTS)" in init
    assert '"ClockNetworkGlobalSizing"' in init
    tcl = (PLUGIN / "clock_network_global_sizing.tcl").read_text()
    assert "set_global_sizing_config -include_clock_network true" in tcl
    assert "-phases GLOBAL_SIZING" in tcl
    # no own sizing algorithm in the tool arm
    assert "swapMaster" not in tcl and "VIBEIC_CLKPATH_SIZING_TCL" not in tcl
    # the measured defect's interim: CTS-built instances are held for the pass
    # and released before legalization; the audit fanout metric is kept
    hold = tcl.index("setDoNotTouch 1")
    assert hold < tcl.index("log_cmd repair_timing") < tcl.index("setDoNotTouch 0") \
        < tcl.index("common/dpl.tcl")
    assert 'utl::metric_integer "vibeic__cts__max_fanout"' in tcl
    assert "write_views" in tcl


# ------------------------------------------------ 2. the retap audit -------

def test_a_retained_retap_matched_to_its_netlist_change_passes(tmp_path):
    rc, doc = _run(_project(tmp_path, KEEP_LOG, NL_KEPT))
    assert rc == 0 and doc["verdict"] == "PASS", doc
    assert [k["inst"] for k in doc["keeps"]] == ["u_core/_1753_"]
    assert doc["netlist"]["moved_connections"] == 1
    assert doc["binding"]["retap_adopted"] is True


@pytest.mark.parametrize("changed", ["both-netlists", "input-state", "config", "scene",
                                     "liberty", "output-netlist", "step-log", "invocation-log",
                                     "adopted-def", "measured-hash", "mode", "sizing-arm"])
def test_current_retap_views_require_the_same_timing_run_and_adoption(tmp_path, changed):
    project = _project(tmp_path, KEEP_LOG, NL_KEPT)
    folder = project / "phase3/librelane/19-cts-hold/03-vibeic-externalcapturelaunchretap"
    receipt = json.loads((folder / "vibeic_receipt.json").read_text())
    if changed == "both-netlists":
        for path in (folder / "in.nl.v", folder / "chip_top.nl.v"):
            path.write_text(path.read_text().replace('.D(n1)', '.D(other_data)'))
        # The old keep still explains the sole current CLK delta. Only its
        # measured input ownership changed; pin-delta logic alone cannot see it.
        delta = RA.netlist_delta((folder / "in.nl.v").read_text(),
                                 (folder / "chip_top.nl.v").read_text())
        assert delta['moved'] == [('u_core/_1753_', 'CLK',
                                  'clknet_leaf_17_i_clk__core', 'clknet_0_i_clk__core')]
    elif changed == "input-state":
        path = folder / "state_in.json"
        path.write_text(path.read_text() + '\n')
    elif changed == "config":
        path = Path(receipt['execution']['config'])
        config = json.loads(path.read_text())
        config['PNR_CORNERS'] = ['other']
        path.write_text(json.dumps(config))
    elif changed in ("scene", "liberty"):
        path = project / "inputs" / ('input.sdc' if changed == 'scene' else 'cells.lib')
        path.write_text(path.read_text() + '\n// bytes changed after the native decision\n')
    elif changed in ("output-netlist", "step-log", "invocation-log"):
        name = {'output-netlist': 'chip_top.nl.v', 'step-log': RA.LOG_NAME,
                'invocation-log': 'invocation.log'}[changed]
        path = folder / name
        path.write_text(path.read_text() + '\n// output changed\n')
    elif changed == "adopted-def":
        (project / "adopted/post_cts_def").write_text('changed handoff DEF bytes\n')
    elif changed == "mode":
        (project / "phase3/librelane_switch.json").write_text(json.dumps(
            {'steps': {'19': 'direct', '20': 'direct'}}))
    elif changed == "sizing-arm":
        (project / "phase3/librelane_switch.json").write_text(json.dumps(
            {'steps': {'19': 'librelane', '20': 'librelane'},
             'arms': {'19.clock_path_sizing': 'own'}}))
    else:
        path = project / RA.HANDOFF
        handoff = json.loads(path.read_text())
        handoff['measured_state_sha256'] = '0' * 64
        path.write_text(json.dumps(handoff))
    rc, doc = _run(project)
    assert (rc, doc['verdict']) == (2, 'NOT_MEASURED'), doc
    assert 'RETAP_TIMING_EVIDENCE_UNBOUND' in doc['reason']
    if changed == 'both-netlists':
        assert 'LL_RETAP_INPUT_STALE' in doc['reason']


@pytest.mark.parametrize('missing', ['receipt', 'partial-input', 'execution', 'failed-run',
                                   'wrong-command', 'library', 'adopted-view', 'scene'])
def test_missing_partial_or_unresolved_timing_evidence_blocks_keep_consumption(tmp_path, missing):
    project = _project(tmp_path, KEEP_LOG, NL_KEPT)
    folder = project / 'phase3/librelane/19-cts-hold/03-vibeic-externalcapturelaunchretap'
    path = folder / 'vibeic_receipt.json'
    rec = json.loads(path.read_text())
    if missing == 'receipt':
        path.unlink()
    elif missing == 'library':
        (project / 'inputs/cells.lib').unlink()
    elif missing == 'scene':
        (project / 'inputs/input.sdc').unlink()
    elif missing == 'adopted-view':
        (project / 'adopted/post_hold_odb').unlink()
    else:
        if missing == 'partial-input':
            rec['input']['state_files'] = {}
        elif missing == 'execution':
            rec.pop('execution')
        elif missing == 'failed-run':
            rec['execution']['rc'] = 1
        else:
            rec['execution']['argv'][-1] = '/wrong-pdk-root'
        path.write_text(json.dumps(rec))
    rc, doc = _run(project)
    assert (rc, doc['verdict']) == (2, 'NOT_MEASURED'), doc
    assert 'RETAP_TIMING_EVIDENCE_UNBOUND' in doc['reason']


def test_fresh_rejected_trials_with_no_netlist_change_pass(tmp_path):
    rc, doc = _run(_project(tmp_path, REJECT_LOG, NL_IN))
    assert (rc, doc['verdict']) == (0, 'PASS'), doc
    assert doc['keeps'] == [] and doc['netlist']['moved_connections'] == 0


@pytest.mark.parametrize("decision", ["keep", "reject"])
@pytest.mark.parametrize("changed", ["scene", "subject", "hold-source", "sta-input", "sta-library"])
def test_current_adopted_chain_is_required_for_keeps_and_rejects(tmp_path, decision, changed):
    project = _project(tmp_path, KEEP_LOG if decision == "keep" else REJECT_LOG,
                       NL_KEPT if decision == "keep" else NL_IN)
    handoff_path = project / RA.HANDOFF
    handoff = json.loads(handoff_path.read_text())
    if changed == "scene":
        handoff["corners"] = ["other_scene"]
    elif changed == "subject":
        unrelated = project / "unadopted/state_out.json"
        unrelated.parent.mkdir()
        unrelated.write_text((project / handoff["measured_state"]).read_text())
        handoff["measured_state"] = str(unrelated.relative_to(project))
        handoff["measured_state_sha256"] = contract.digest(unrelated)
    elif changed == "hold-source":
        view = handoff["views"]["post_hold_def"]
        unrelated = project / "unadopted.def"
        unrelated.write_text("neutral unadopted DEF\n")
        shutil.copyfile(unrelated, project / view["dest"])
        view.update(source=str(unrelated), source_sha256=contract.digest(unrelated),
                    dest_sha256=contract.digest(project / view["dest"]))
    else:
        path = project / handoff["chain"]["OpenROAD.STAMidPNR"] / "vibeic_receipt.json"
        record = json.loads(path.read_text())
        if changed == "sta-input":
            record["execution"]["state"] = str(project / "inputs/state.json")
        else:
            record["input"]["liberty_files"] = {"/unrelated/cells.lib": "unrelated"}
            (path.parent / "input_fingerprint.json").write_text(json.dumps(record["input"]))
            record["sha256"]["input_fingerprint.json"] = contract.digest(path.parent / "input_fingerprint.json")
        path.write_text(json.dumps(record))
    handoff_path.write_text(json.dumps(handoff))
    verdict, doc = RA.audit(project)
    (tmp_path / "adoption_observed.json").write_text(json.dumps({
        "decision": decision, "changed": changed, "verdict": verdict, "doc": doc}))
    assert verdict == "NOT_MEASURED" and "RETAP_TIMING_EVIDENCE_UNBOUND" in doc["reason"], doc
    assert _run(project)[0] == 2


@pytest.mark.parametrize("decision", ["keep", "reject"])
def test_all_current_sta_corners_remain_bound_to_the_selected_chain(tmp_path, decision):
    project = _project(tmp_path, KEEP_LOG if decision == "keep" else REJECT_LOG,
                       NL_KEPT if decision == "keep" else NL_IN, corners=("scene_a", "scene_b"))
    rc, doc = _run(project)
    assert rc == 0 and doc["verdict"] == "PASS", doc
    assert doc["netlist"]["moved_connections"] == (1 if decision == "keep" else 0)


@pytest.mark.parametrize("decision", ["keep", "reject"])
def test_current_selected_direct_arm_remains_bound_without_claiming_retap_adoption(tmp_path, decision):
    project = _project(tmp_path, KEEP_LOG if decision == "keep" else REJECT_LOG,
                       NL_KEPT if decision == "keep" else NL_IN)
    arm = project / "phase3/tool_arms/19/openroad"
    bridge = arm / "bridge/state_in.json"
    bridge.parent.mkdir(parents=True)
    state = json.loads((project / "inputs/state.json").read_text())
    for key in ("def", "odb"):
        path = arm / ("post_hold." + key)
        path.write_text("neutral direct-arm " + key + "\n")
        state[key] = str(path)
    (arm / "post_cts.def").write_text("neutral direct-arm CTS DEF\n")
    bridge.write_text(json.dumps(state))
    def native(cmd, **kwargs):
        target = Path(cmd[cmd.index('-o') + 1])
        (target / "state_out.json").write_text(Path(cmd[cmd.index('-i') + 1]).read_text())
        shutil.copyfile(Path(cmd[cmd.index('-c') + 1]), target / "config.json")
        return subprocess.CompletedProcess(cmd, 0, "neutral direct STA edge; no EDA\n", "")
    with patch.object(contract, "image_capability", return_value={}), \
            patch.object(contract, "run_container", side_effect=native):
        produced = contract.run_chain(project, "captured-image", [
            ("OpenROAD.STAMidPNR", project / "inputs/OpenROAD.STAMidPNR.json", bridge)],
            lane="19-cts-hold-direct-arm", pdk_root="/pdk")
    (project / "phase3/librelane_switch.json").write_text(json.dumps(
        {"steps": {"19": "dual", "20": "dual"}}))
    selection = {"selection": "openroad", "mode": "dual"}
    (arm.parent / "selection.json").write_text(json.dumps(selection))
    handoff_path = project / RA.HANDOFF
    handoff = json.loads(handoff_path.read_text())
    measured = produced[-1] / "state_out.json"
    handoff.update(modes=cts.modes(project), selected="openroad", selection=selection,
                   measured_state=str(measured.relative_to(project)),
                   measured_state_sha256=contract.digest(measured))
    for name, filename in (("post_cts_def", "post_cts.def"), ("post_hold_def", "post_hold.def"),
                           ("post_hold_odb", "post_hold.odb")):
        source = arm / filename
        view = handoff["views"][name]
        shutil.copyfile(source, project / view["dest"])
        view.update(source=str(source), source_sha256=contract.digest(source),
                    dest_sha256=contract.digest(project / view["dest"]))
    handoff_path.write_text(json.dumps(handoff))
    rc, doc = _run(project)
    assert rc == 0 and doc["verdict"] == "PASS", doc
    assert doc["binding"]["selected"] == "openroad" and doc["binding"]["retap_adopted"] is False


def _repeat_chain(project, native):
    inputs = project / 'inputs'
    with patch.object(contract, 'image_capability', return_value={}), \
            patch.object(contract, 'run_container', side_effect=native):
        return contract.run_chain(project, 'captured-image', [
            ('OpenROAD.CTS', inputs / 'passthrough.json', inputs / 'state.json'),
            (cts.CLOCK_PATH_SIZING_ARMS['tool'], inputs / 'sizing.json', inputs / 'state.json'),
            (RA.STEP_ID, inputs / 'config.json', inputs / 'state.json'),
            ('OpenROAD.ResizerTimingPostCTS', inputs / 'OpenROAD.ResizerTimingPostCTS.json', inputs / 'state.json'),
            ('OpenROAD.STAMidPNR', inputs / 'OpenROAD.STAMidPNR.json', inputs / 'state.json')],
            lane='19-cts-hold', pdk_root='/pdk')


def test_unchanged_complete_native_binding_reuses_the_existing_run(tmp_path):
    project = _project(tmp_path, KEEP_LOG, NL_KEPT)
    def unexpected(*args, **kwargs):
        pytest.fail('a complete unchanged producer binding must be reusable')
    _repeat_chain(project, unexpected)
    assert _run(project)[0] == 0


@pytest.mark.parametrize('failure', ['native-failed', 'input-changed-during-run'])
def test_failed_or_concurrently_changed_producer_cannot_publish_an_admission(tmp_path, failure):
    project = _project(tmp_path, KEEP_LOG, NL_KEPT)
    folder = project / 'phase3/librelane/19-cts-hold/03-vibeic-externalcapturelaunchretap'
    (folder / RA.LOG_NAME).write_text('changed log forces a new retap run\n')
    calls = []
    def native(cmd, **kwargs):
        calls.append(cmd[cmd.index('--id') + 1])
        assert calls[-1] == RA.STEP_ID
        if failure == 'native-failed':
            return subprocess.CompletedProcess(cmd, 1, '', 'captured native failure\n')
        state = json.loads(Path(cmd[cmd.index('-i') + 1]).read_text())
        target = Path(cmd[cmd.index('-o') + 1])
        (target / 'state_out.json').write_text(json.dumps(state))
        path = project / 'inputs/input.nl'
        path.write_text(path.read_text().replace('.D(n1)', '.D(other_data)'))
        return subprocess.CompletedProcess(cmd, 0, 'captured edited input\n', '')
    code = 'LL_STEP_FAILED' if failure == 'native-failed' else 'LL_RETAP_INPUT_STALE'
    with pytest.raises(contract.Refusal, match=code):
        _repeat_chain(project, native)
    assert calls == [RA.STEP_ID]
    assert not (folder / 'vibeic_receipt.json').exists()
    assert _run(project)[0] == 2


def test_a_netlist_change_no_keep_row_explains_fails(tmp_path):
    # the reject-only log, but the netlist moved a clock pin anyway
    rc, doc = _run(_project(tmp_path, REJECT_LOG, NL_KEPT))
    assert rc == 1 and doc["verdict"] == "FAIL"
    assert [f["code"] for f in doc["findings"]] == ["RETAP_UNEXPLAINED_NETLIST_CHANGE"]


def test_a_keep_row_the_netlist_does_not_carry_fails(tmp_path):
    rc, doc = _run(_project(tmp_path, KEEP_LOG, NL_IN))
    assert rc == 1
    assert [f["code"] for f in doc["findings"]] == ["RETAP_KEEP_NOT_IN_NETLIST"]


def test_a_keep_its_own_numbers_do_not_support_fails(tmp_path):
    # the real keep row, with the hold it printed turned negative after the trial
    log = KEEP_LOG.replace("hold=0.3183198239541504->0.3183198239541504",
                           "hold=0.3183198239541504->-0.0100000000000000")
    assert log != KEEP_LOG
    rc, doc = _run(_project(tmp_path, log, NL_KEPT))
    assert rc == 1
    assert doc["findings"] == [{"code": "RETAP_KEEP_UNSUPPORTED",
                                "inst": "u_core/_1753_", "rule": "HOLD_REGRESSED"}]


def test_a_remastered_or_added_cell_is_not_a_retap(tmp_path):
    nl = NL_KEPT.replace("gf_dffq_1 \\u_core/_1683_", "gf_dffq_2 \\u_core/_1683_")
    rc, doc = _run(_project(tmp_path, KEEP_LOG, nl))
    assert rc == 1
    assert any("remastered u_core/_1683_" in str(f.get("change")) for f in doc["findings"])


def test_a_log_without_the_retap_grammar_is_not_measured(tmp_path):
    rc, doc = _run(_project(tmp_path, "Reading OpenROAD database...\n", NL_IN))
    assert rc == 2 and doc["verdict"] == "NOT_MEASURED"
    assert "VIC_RETAP" in doc["reason"]


def test_a_receipt_without_the_retap_step_is_not_measured(tmp_path):
    project = _project(tmp_path, KEEP_LOG, NL_KEPT)
    (project / "reports/phase3/librelane_cts_hold_handoff.json").write_text(
        json.dumps({"chain": {}}))
    rc, doc = _run(project)
    assert rc == 2 and doc["verdict"] == "NOT_MEASURED"


def test_the_redecision_mirrors_the_steps_own_policy():
    policy = (PLUGIN / "external_capture_retap_policy.tcl").read_text()
    for rule in ("LOCAL_SETUP_NOT_IMPROVED", "DESIGN_SETUP_REGRESSED", "HOLD_REGRESSED"):
        assert rule in policy
    base = {"local": (-1.0, 0.5), "setup": (-1.0, -1.0), "tns": (-5.0, -5.0),
            "hold": (0.2, 0.2)}
    assert RA.redecide(dict(base)) is None
    assert RA.redecide(dict(base, local=(-1.0, -1.0))) == "LOCAL_SETUP_NOT_IMPROVED"
    assert RA.redecide(dict(base, setup=(-1.0, -1.1))) == "DESIGN_SETUP_REGRESSED"
    assert RA.redecide(dict(base, hold=(0.2, -0.01))) == "HOLD_REGRESSED"
    # an already-negative hold may stay negative if it does not get worse
    assert RA.redecide(dict(base, hold=(-0.3, -0.3))) is None


def test_step_19_gates_on_the_retap_audit_when_the_chain_ran():
    flow = yaml.safe_load((PROGRAMS.parent / "flow/phase1_phase2_phase3.yaml").read_text())
    step = next(s for s in flow["steps"] if str(s["id"]) == "19")
    assert "retap_audit_check" in step["programs"]
    clauses = [c.get("optional_program_exit_zero") for c in step["gate"]["all_of"]]
    audit = [c for c in clauses if c and c["command"].startswith("retap_audit_check ")]
    assert audit and audit[0]["condition_files_exist"] == [
        "reports/phase3/librelane_cts_hold_handoff.json"]

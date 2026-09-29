"""CUT9 SEND_BACK: actual tool receipts and the harvested partial-x failure.

The historical failure input is read from the preserved producer regression,
not a golden netlist. The ABC fixture is the tool's actual buffer-only script.
"""
import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).parent))
import drv_capture_plan as plan
import drv_stage_receipts as receipts
import librelane_contract as ll
import synth_handoff_netlist_check as handoff
from test_cut9_step14_judges_the_handoff_netlist import _project


@pytest.mark.parametrize("literal", ["22'hxxxxxx", "22'h000000", "22'hffffff"])
def test_harvested_multibit_rhs_constants_fail_the_project_gate(tmp_path, literal):
    original = (PROGRAMS / "tests/test_setundef_zero_before_hilomap_producer.py").read_text()
    rhs = re.search(r"assign io_out = (\{.*?\});", original, re.S).group(1)
    src = tmp_path / "partial.v"
    src.write_text("module partial;\n  assign io_out = " +
                   rhs.replace("22'hxxxxxx", literal) + ";\nendmodule\n")
    result = handoff.check_project(_project(tmp_path, src, tool=False))
    assert result["verdict"] == "FAIL", result
    assert any(f.startswith("CONSTANT_NOT_TIED") for f in result["findings"])
    if "x" in literal:
        assert any(f.startswith("UNDEFINED_CONSTANT") for f in result["findings"])


@pytest.mark.parametrize("body", [
    "assign bus[7:0] = { sig, { 2'b10, sig } };",
    "vector inst (.DATA({ sig, 3'bx01 }));",
])
def test_nested_assign_and_vector_pin_literals_are_connections(body):
    constants = handoff.constant_connections("module neutral;\n" + body + "\nendmodule")
    assert len(constants) == 1, constants


def test_comment_attribute_and_signal_indices_are_not_connections():
    text = """// assign ignored = { sig, 1'bx };
/* vector ignored (.DATA({ sig, 1'b0 })); */
(* note = "assign ignored = 1'bx;" *)
module neutral(input [3:0] sig, output [1:0] bus);
  assign bus = {sig[1], sig[0]};
  vector inst (.DATA({sig[3:2], sig[1:0]}));
endmodule
"""
    assert handoff.constant_connections(text) == []


def test_native_escaped_identifier_partial_constant_is_refused(tmp_path):
    source = PROGRAMS / "calibration/synth_escaped_partial_constant.v"
    report = handoff.check_project(_project(tmp_path, source, tool=False))
    assert report["verdict"] == "FAIL", report
    assert any(f.startswith("CONSTANT_NOT_TIED") for f in report["findings"])
    assert any(f.startswith("UNDEFINED_CONSTANT") for f in report["findings"])
    assert report["constants"][0]["target"] == "\\a//b"


@pytest.mark.parametrize("body", [
    "assign \\a/*b = { sig, 1'bx };",
    "vector inst (.\\a//b ({ sig, 1'bx }));",
    "assign bus = { \\signal//x , 1'bx };",
])
def test_escaped_identifiers_do_not_hide_constant_connections(body):
    constants = handoff.constant_connections("module neutral; " + body + " endmodule")
    assert len(constants) == 1 and constants[0]["value"] == "1'bx", constants


def test_literals_in_escaped_signal_names_are_not_connections():
    text = "module neutral; assign bus = { \\signal//1'bx , sig }; endmodule"
    assert handoff.constant_connections(text) == []


def _synth_receipt(tmp_path, script):
    project = tmp_path / "project"
    receipts.claim(project)
    abc = receipts.abc_script_path(project)
    abc.parent.mkdir(parents=True)
    abc.write_text(script)
    behavior = abc.parent / "behavior.rpt"
    behavior.write_text("clocks 1\nclock clk is_propagated=0\nreport_check_types\n")
    nl, sdc = abc.parent / "netlist.v", abc.parent / "constraints.sdc"
    nl.write_text("module neutral; endmodule\n")
    sdc.write_text("set_max_fanout 4 [current_design]\n")
    receipts.record_synth(project, abc_script=abc, behavior=behavior, netlist=nl, sdc=sdc)
    return project, abc


@pytest.mark.parametrize("script", [
    "buffer -N 4 -S 3000\n", "buffer -S 3000 -N 4; stime -p\n",
    "strash; buffer -N 4 -S 3000; stime -p\n",
])
def test_real_buffer_command_option_order_and_separators(tmp_path, script):
    project, _ = _synth_receipt(tmp_path, script)
    row = plan._stages(project, False)[0][0]
    assert row["applied"]["fanout"] == 4.0, row
    assert row["synth_abc_buffering"]


def test_abc_echo_and_comment_do_not_prove_buffering(tmp_path):
    project, _ = _synth_receipt(tmp_path, "echo buffer -N 4 -S 3000;\n# buffer -N 4\n")
    row = plan._stages(project, False)[0][0]
    assert row["applied"]["fanout"] is None
    assert not row["synth_abc_buffering"]


def test_changed_script_and_previous_run_are_not_credited(tmp_path):
    project, abc = _synth_receipt(tmp_path, "buffer -N 4 -S 3000\n")
    abc.write_text("buffer -N 8 -S 3000\n")
    row = plan._stages(project, False)[0][0]
    assert not row["ran"]
    assert row["applied"]["fanout"] is None
    receipts.claim(project)
    assert plan._stages(project, False)[1]["synth"]["status"] == "absent"


def test_librelane_producer_keeps_only_the_script_abc_actually_sourced(tmp_path, monkeypatch):
    import phase3_one_shot_runner as runner
    project = tmp_path / "project"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "neutral.v").write_text("module neutral(input a, output y); assign y=a; endmodule\n")
    receipts.claim(project)
    folder = project / "phase3/librelane/02-yosys-synthesis"
    folder.mkdir(parents=True)
    script = folder / "AREA_0.abc"
    script.write_bytes((PROGRAMS / "calibration/librelane_buffer_only.abc").read_bytes())
    (folder / "UNUSED.abc").write_text("buffer -N 99\n")
    (folder / "yosys-synthesis.log").write_text(f"ABC: + source {script} \n")
    native = folder / "neutral.nl.v"
    native.write_text("module neutral; endmodule\n")
    (folder / "state_out.json").write_text(json.dumps({"nl": str(native), "metrics": {
        "design__instance_unmapped__count": 0, "synthesis__check_error__count": 0}}))
    (folder / "config.json").write_text(json.dumps({"meta": {"step": "Yosys.Synthesis"}}))
    (folder / "reports").mkdir()
    (folder / "reports/stat.json").write_text("{}")
    (folder / "fsm_encoding.enc").write_text("")
    liberty = tmp_path / "scl__tt.lib"
    liberty.write_text("library(scl) {}\n")
    monkeypatch.setattr(ll, "resolve_image", lambda *_: "stated-image")
    monkeypatch.setattr(ll, "pdk_root_resolution", lambda *_a, **_k: {"path": str(tmp_path)})
    def emit(_project, _pdk, output, *_a, **_k):
        output.with_suffix(".provenance.json").write_text("{}")
        return {"SYNTH_FSM_ENCFILE": True}
    monkeypatch.setattr(ll, "emit_synthesis_config", emit)
    monkeypatch.setattr(ll, "resolve_step_config", lambda _p, _i, src, *_a, **_k: src)
    monkeypatch.setattr(ll, "run_chain", lambda *_a, **_k: [folder, folder])
    import synth_area_stats_emit
    monkeypatch.setattr(synth_area_stats_emit, "emit_for_run", lambda *_a, **_k: str(folder / "stats.json"))
    monkeypatch.setattr(ll, "verify_synthesis_stat", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(runner, "_log_surviving_artefact", lambda *_a, **_k: None)
    pdk = SimpleNamespace(name="processA", liberty=str(liberty), macro_libs=[], macro_lefs=[], macro_v=[])
    try:
        result = runner._step_synth_librelane(project, "neutral", pdk, "unused")
    finally:
        runner.set_invocation_provenance_sink(None)
    assert result.status == "PASS", result
    kept = receipts.abc_script_path(project)
    assert kept.is_file(), "LibreLane synthesis must preserve its executed script"
    assert kept.read_bytes() == script.read_bytes()
    assert hashlib.sha256(kept.read_bytes()).hexdigest() == hashlib.sha256(script.read_bytes()).hexdigest()
    behavior = kept.parent / "behavior.rpt"
    behavior.write_text("clocks 1\nclock clk is_propagated=0\nreport_check_types\n")
    sdc = kept.parent / "constraints.sdc"
    sdc.write_text("set_max_fanout 4 [current_design]\n")
    receipts.record_synth(project, abc_script=kept, behavior=behavior,
                          netlist=project / "phase2/stage2/synth/neutral_synth.v", sdc=sdc)
    row = plan._stages(project, False)[0][0]
    assert row["ran"] and row["applied"]["fanout"] == 4.0
    assert row["tool_step"]["id"] == "Yosys.Synthesis"
    assert row["abc_source"]["sha256"] == row["abc_script"]["sha256"]
    # A script/config from another invocation must never get stage credit.
    (folder / "config.json").write_text("{}")
    row = plan._stages(project, False)[0][0]
    assert not row["ran"] and row["applied"]["fanout"] is None


def test_missing_merged_tribuf_feature_is_not_measured(tmp_path, monkeypatch):
    import phase3_one_shot_runner as runner
    from test_cut9_synthesis_is_the_tool_and_keeps_its_lessons import _chip_project
    project = _chip_project(tmp_path)
    liberty = tmp_path / "scl__tt.lib"
    liberty.write_text("library(scl) {}\n")
    monkeypatch.setattr(ll, "resolve_image", lambda *_: "stated-image")
    monkeypatch.setattr(ll, "pdk_root_resolution", lambda *_a, **_k: {"path": str(tmp_path)})
    def absent(_project, _image, source, *_a, **_k):
        config = json.loads(source.read_text())
        assert config["SYNTH_TRIBUF_LOGIC"] is True
        raise ll.Refusal("LL_TOOL_FEATURE_ABSENT", "SYNTH_TRIBUF_LOGIC")
    monkeypatch.setattr(ll, "resolve_step_config", absent)
    pdk = SimpleNamespace(name="processA", liberty=str(liberty), macro_libs=[], macro_lefs=[], macro_v=[])
    try:
        result = runner._step_synth_librelane(project, "block", pdk, "unused")
    finally:
        runner.set_invocation_provenance_sink(None)
    assert result.status == "NOT_MEASURED", result
    assert result.reason_class == "tool_absent"
    assert "SYNTH_TRIBUF_LOGIC" in result.detail


def test_resolver_refuses_instead_of_ignoring_an_absent_feature(tmp_path, monkeypatch):
    import subprocess
    source, output = tmp_path / "source.json", tmp_path / "resolved.json"
    source.write_text(json.dumps({"meta": {"step": "Yosys.Synthesis"},
                                  "SYNTH_TRIBUF_LOGIC": True}))
    monkeypatch.setattr(ll, "run_container", lambda *_a, **_k:
                        subprocess.CompletedProcess([], 1, "", "LL_TOOL_FEATURE_ABSENT: SYNTH_TRIBUF_LOGIC"))
    with pytest.raises(ll.Refusal, match="LL_TOOL_FEATURE_ABSENT"):
        ll.resolve_step_config(tmp_path, "stated-image", source, output)

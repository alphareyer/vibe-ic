"""A fresh front door whose SoC top is still owed must not crash phase 2.

MEASURED on subservient x gf180mcuD, IC path (DIE), main 90738d258, a fresh
input-only copy on 8HD-3 (2026-09-29): rtl_gen fetched the reused core and
handed the SoC glue to `catalog-glue-author`, so `rtl/` held the core's leaves
and no `subservient` module. The generic full-stack TB
(`tb_subservient_full.v`, `subservient u_dut (`) was compiled inside the EDA
container and iverilog answered

    /foss/designs/<proj>/phase2/stage1/sim_full_stack/tb_subservient_full.v:36:
    error: Unknown module type: subservient

Two defects, one crash:
  1. the step booked that as FAIL "real structural defect" -- a design not
     written yet is not a structural defect;
  2. the detail quoted the stderr in the CONTAINER's spelling of the project,
     `_refuse_relocated_copy_in_record` read `/foss/designs/<proj>/...` as a
     relocated copy, and phase 2 died with `_RecordNamesRelocatedCopy` and no
     report at all.

Now: the owed top is NOT_MEASURED (awaiting the named authoring skill, or
input_absent when none was handed the work), nothing is compiled, and tool
output from the container comes back in host spelling from the container's own
mount table. A real compile failure of an authored top still FAILs.
The simulator and the docker client are stand-ins; the sources are synthetic.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as runner  # noqa: E402

CONT_ROOT = "/foss/designs"
TB = """`timescale 1ns/1ps
module tb_soc_top_full;
  reg i_clk = 0;
  reg i_rst = 1;
  wire o_led;
  always #10 i_clk = ~i_clk;
  // the design top instantiated from L9.top_ports
  soc_top u_dut (
    .i_clk(i_clk),
    .i_rst(i_rst),
    .o_led(o_led)
  );
  initial begin #100 $display("FULL_STACK_TB_DONE"); $finish; end
endmodule
"""
LEAF = "module core_leaf(input i_clk, output o_q); assign o_q = i_clk; endmodule\n"
TOP = ("module soc_top(input i_clk, input i_rst, output o_led);\n"
       "  core_leaf u_leaf(.i_clk(i_clk), .o_q(o_led));\nendmodule\n")
SKILL = ("---\nname: catalog-glue-author\ndescription: author the SoC glue\n"
         "---\n")


def _project(tmp_path: Path, *, top_authored: bool, handed_off: bool) -> Path:
    proj = tmp_path / "designs" / "fresh_die"
    sfs = proj / "phase2" / "stage1" / "sim_full_stack"
    sfs.mkdir(parents=True)
    (sfs / "tb_soc_top_full.v").write_text(TB)
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "core_leaf.v").write_text(LEAF)
    if top_authored:
        (rtl / "soc_top.v").write_text(TOP)
    if handed_off:
        (proj / "phase2" / "stage1" / "fallback_skill.md").write_text(SKILL)
    return proj


@pytest.fixture
def container(monkeypatch, tmp_path):
    """The container view the measured run had: the designs root bind-mounted
    at /foss/designs. `_docker_exec` stands in for iverilog inside it and
    answers the way iverilog does, in the container's spelling."""
    host_root = str(tmp_path / "designs")
    monkeypatch.setattr(runner, "_container_mounts",
                        lambda c: [(host_root, CONT_ROOT)])
    monkeypatch.setattr(runner, "_iverilog_available", lambda c: True)
    monkeypatch.setattr(runner, "_iverilog_exec_container",
                        lambda *a, **k: True)
    monkeypatch.setattr(runner, "_record_sim_toolchain",
                        lambda *a, **k: None)
    calls = []

    def fake_exec(cont, cmd, timeout=600, **kw):
        calls.append(cmd)
        tb = [t for t in cmd.split() if t.endswith("_full.v")]
        rtl_sources = [t for t in cmd.split() if "/rtl/" in t]
        defined = any(t.endswith("/soc_top.v") for t in rtl_sources)
        if "iverilog" in cmd and not defined:
            return (2, "", f"{tb[0]}:8: error: Unknown module type: soc_top\n"
                           "2 error(s) during elaboration.\n")
        if "iverilog" in cmd:
            return (2, "", f"{tb[0]}:9: error: port ``o_led'' is not a port "
                           "of u_dut.\n")
        return (0, "FULL_STACK_TB_DONE\n", "")

    monkeypatch.setattr(runner, "_docker_exec", fake_exec)
    return calls


def _step(proj):
    return runner._reference_tb_generic_full_stack(
        proj, "soc_top", "generic_full_stack class", time.time(),
        container="stand_in", ic_class="processor_cpu")


def test_an_owed_top_is_not_measured_and_the_record_can_be_written(
        tmp_path, container):
    proj = _project(tmp_path, top_authored=False, handed_off=True)
    res = _step(proj)
    assert res.status == "NOT_MEASURED", res.detail
    assert res.reason_class == "awaiting_agent_pass"
    assert "soc_top" in res.detail and "catalog-glue-author" in res.detail
    assert "structural defect" not in res.detail
    assert container == [], "nothing is compiled for a top nobody wrote yet"
    # the phase-2 record carrying this row is writable (the measured crash)
    runner._refuse_relocated_copy_in_record(
        {"steps": [{"detail": res.detail, "extras": res.extras}]}, proj)


def test_an_owed_top_with_no_hand_off_is_input_absent(tmp_path, container):
    proj = _project(tmp_path, top_authored=False, handed_off=False)
    res = _step(proj)
    assert res.status == "NOT_MEASURED"
    assert res.reason_class == "input_absent"


def test_container_output_comes_back_in_host_spelling(tmp_path, container):
    proj = _project(tmp_path, top_authored=False, handed_off=True)
    run_dir = proj / "phase2" / "stage1" / "sim_full_stack" / "run"
    run_dir.mkdir()
    tb = proj / "phase2" / "stage1" / "sim_full_stack" / "tb_soc_top_full.v"
    rc, out, err = runner._run_iverilog_stage(
        ["iverilog", "-g2012", "-o", str(run_dir / "x.vvp"), str(tb)],
        run_dir, "stand_in")
    assert rc == 2
    assert CONT_ROOT not in err, err
    assert err.startswith(f"{tb}:8: error: Unknown module type: soc_top")
    runner._refuse_relocated_copy_in_record({"steps": [{"detail": err}]}, proj)


def test_a_real_compile_failure_of_an_authored_top_still_fails(
        tmp_path, container):
    proj = _project(tmp_path, top_authored=True, handed_off=True)
    res = _step(proj)
    assert res.status == "FAIL", res.detail
    assert "is not a port of u_dut" in res.detail
    assert CONT_ROOT not in res.detail, res.detail
    runner._refuse_relocated_copy_in_record(
        {"steps": [{"detail": res.detail}]}, proj)


def test_a_path_no_mount_covers_is_left_as_printed(monkeypatch):
    monkeypatch.setattr(runner, "_container_mounts",
                        lambda c: [("/data/x", CONT_ROOT),
                                   ("/same", "/same")])
    f = getattr(runner, "_container_text_to_host", None)
    assert f is not None
    text = ("/foss/designs/p/a.v:1: e\n/foss/designsX/p/b.v\n"
            "/foss/pdks/lib.v\n/same/q.v\n'/foss/designs'")
    assert f(text, "c") == ("/data/x/p/a.v:1: e\n/foss/designsX/p/b.v\n"
                            "/foss/pdks/lib.v\n/same/q.v\n'/data/x'")


def test_a_top_defined_only_inside_a_string_or_comment_is_still_owed(
        tmp_path, container):
    """The definition is read as Verilog, not as text: `module soc_top` in a
    comment or a `$display` string defines nothing."""
    proj = _project(tmp_path, top_authored=False, handed_off=True)
    (proj / "phase2" / "stage1" / "rtl" / "notes.v").write_text(
        "// module soc_top(input i_clk); -- to be authored\n"
        "/* module soc_top */\n"
        "module notes; initial $display(\"module soc_top pending\"); "
        "endmodule\n")
    res = _step(proj)
    assert res.status == "NOT_MEASURED", res.detail
    assert container == []

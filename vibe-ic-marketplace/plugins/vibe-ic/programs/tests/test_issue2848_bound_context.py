"""Neutral closure of the independent #2848 frontend-binding findings.

Fixtures are the retained independent review's exact source bytes. No oracle,
dataset or benchmark is used. Native measurements are explicitly unavailable
when a required tool is absent. Optional evidence goes outside the source tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diff_verify_harness as dvh

F = Path(__file__).parent / "fixtures" / "issue2848_binding"
REF = "def ref(seq):\n    return [0] + list(seq[:-1])\n"


@pytest.fixture
def scratch():
    with tempfile.TemporaryDirectory(prefix="dvhbind_") as root:
        yield Path(root)


@pytest.fixture
def native_tools():
    pytest.importorskip("pyslang", reason="NOT_VERIFIED: pyslang absent")
    missing = [name for name in ("iverilog", "vvp") if shutil.which(name) is None]
    if missing:
        pytest.skip(f"NOT_VERIFIED: missing {missing}")


def evidence(label, data):
    if target := os.environ.get("DVH_BINDING_EVIDENCE_DIR"):
        root = Path(target)
        root.mkdir(parents=True, exist_ok=True)
        (root / (label + ".json")).write_text(json.dumps(data, indent=2) + "\n")


def files(scratch, code):
    rtl, ref = scratch / "unit.sv", scratch / "reference.py"
    rtl.write_text(code)
    ref.write_text(REF)
    return rtl, ref


def check(scratch, code, top, label, cycles=16):
    rtl, ref = files(scratch, code)
    report = dvh.diff_verify(rtl, ref, top, "all", cycles, 0, True)
    evidence(label, report)
    return report


def native_probe(scratch, rtl, top, width, value, label):
    tb = scratch / "native_probe.sv"
    tb.write_text(f"""module native_probe; reg clk=0;
 reg [{width-1}:0] d={width}'d{value}; wire [{width-1}:0] q;
 {top} dut(.clk(clk),.d(d),.q(q));
 initial begin #1 clk=1; #1;
 $display("WIDTH_IN=%0d WIDTH_OUT=%0d Q=%0d",$bits(dut.d),$bits(dut.q),q);
 $finish; end endmodule
""")
    binary = scratch / "native.vvp"
    runs = []
    for argv in (["iverilog", "-g2012", "-s", "native_probe", "-o", str(binary), str(rtl), str(tb)],
                 ["vvp", str(binary)]):
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate(timeout=40)
        runs.append(dict(argv=argv, pid=p.pid, rc=p.returncode, stdout=out, stderr=err))
        assert p.returncode == 0, runs
    evidence(label, dict(driver_pid=os.getpid(), runs=runs))
    return runs[-1]["stdout"]


def test_icarus_predefined_macro_must_not_allow_false_agreement(native_tools, scratch):
    code = (F / "macro_pipe.sv").read_text()
    rtl, ref = files(scratch, code)
    assert "WIDTH_IN=8 WIDTH_OUT=8 Q=128" in native_probe(scratch, rtl, "macro_pipe", 8, 0, "macro_native")
    report = dvh.diff_verify(rtl, ref, "macro_pipe", "all", 16, 0, True)
    evidence("macro_defect", report)
    assert report["verdict"] == "MISMATCH", report
    assert report["driven_input"] == {"name": "d", "width": 8}
    assert report["sampled_output"] == {"name": "q", "width": 8}
    assert report["first_mismatch"] == {"cycle": 0, "signal": "q", "rtl": 128, "ref": 0, "sequence": 0}


def test_matching_pipeline_in_same_macro_context_agrees(native_tools, scratch):
    code = (F / "macro_pipe.sv").read_text().replace("d ^ 8'h80", "d")
    report = check(scratch, code, "macro_pipe", "macro_identity")
    assert (report["verdict"], report["n_sequences"]) == ("AGREE", 22), report
    assert report["driven_input"]["width"] == report["sampled_output"]["width"] == 8


def test_dut_log_text_cannot_impersonate_native_width_guard(native_tools, scratch):
    code = (F / "macro_pipe.sv").read_text().replace("d ^ 8'h80", "d")
    code = code.replace("endmodule", 'initial $display("DIFF_PORT_CONTEXT_MISMATCH: ordinary DUT log"); endmodule')
    report = check(scratch, code, "macro_pipe", "dut_log_collision")
    assert (report["verdict"], report["n_sequences"]) == ("AGREE", 22), report


def test_typed_integer_default_width_must_match_both_live_tools(native_tools, scratch):
    # Exact reviewed integer_defaults.sv: 32-bit declared integer overflow,
    # not an unbounded Python calculation, resolves TOTAL=1 and W=8.
    code = (F / "integer_defaults.sv").read_text()
    rtl, ref = files(scratch, code)
    assert "WIDTH_IN=8 WIDTH_OUT=8 Q=255" in native_probe(scratch, rtl, "integer_pipe", 8, 255, "integer_native")
    report = dvh.diff_verify(rtl, ref, "integer_pipe", "all", 16, 0, True)
    evidence("integer_defaults", report)
    assert report["verdict"] == "AGREE", report
    assert report["driven_input"]["width"] == report["sampled_output"]["width"] == 8


def test_narrowed_based_parameter_is_supported(native_tools, scratch):
    code = (F / "narrowed_based.sv").read_text()
    rtl, ref = files(scratch, code)
    assert "WIDTH_IN=8 WIDTH_OUT=8 Q=255" in native_probe(scratch, rtl, "narrowed_pipe", 8, 255, "narrowed_native")
    report = dvh.diff_verify(rtl, ref, "narrowed_pipe", "all", 16, 0, True)
    evidence("narrowed_based", report)
    assert report["verdict"] == "AGREE", report
    assert report["driven_input"]["width"] == report["sampled_output"]["width"] == 8


def test_primary_io_scope_discloses_undriven_and_unsampled_ports(native_tools, scratch):
    report = check(scratch, (F / "scope_pipe.sv").read_text(), "scope_pipe", "primary_scope")
    assert report["verdict"] == "AGREE", report
    assert report["driven_input"] == {"name": "z", "width": 8}
    assert report["sampled_output"] == {"name": "q", "width": 8}
    assert report["undriven_data_inputs"] == ["x", "auxiliary"]
    assert report["unsampled_outputs"] == ["status"]
    assert "not multi-input semantic verification" in report["primary_io_scope"]


@pytest.mark.parametrize("filename,verdict", [("packed_pipe.sv", "AGREE"), ("packed_mutant.sv", "MISMATCH")])
def test_signed_ascending_packed_order_and_mutant(native_tools, scratch, filename, verdict):
    code = (F / filename).read_text()
    report = check(scratch, code, "packed_pipe", filename.removesuffix(".sv"))
    assert report["verdict"] == verdict, report
    assert report["driven_input"] == {"name": "d", "width": 12}
    assert report["sampled_output"] == {"name": "q", "width": 12}
    assert report["clk"] == "clock" and report["resets_held_inactive"] == ["reset_n", "reset"]
    if verdict == "AGREE":
        rtl = scratch / "unit.sv"
        tb = scratch / "packed_probe.sv"
        tb.write_text("""module native_probe; reg clock=0,reset_n=1,reset=0;
 reg [11:0] d=12'h801; wire [11:0] q;
 packed_pipe dut(.clock(clock),.reset_n(reset_n),.reset(reset),.d(d),.q(q));
 initial begin #1 clock=1; #1;
 $display("WIDTH_IN=%0d WIDTH_OUT=%0d Q=%0d",$bits(dut.d),$bits(dut.q),q); $finish; end endmodule
""")
        binary = scratch / "packed.vvp"
        cp = subprocess.run(["iverilog", "-g2012", "-s", "native_probe", "-o", str(binary), str(rtl), str(tb)], capture_output=True, text=True)
        assert cp.returncode == 0, cp.stderr
        cp = subprocess.run(["vvp", str(binary)], capture_output=True, text=True)
        evidence("packed_native", dict(rc=cp.returncode, stdout=cp.stdout, stderr=cp.stderr))
        assert cp.returncode == 0 and "WIDTH_IN=12 WIDTH_OUT=12 Q=2049" in cp.stdout


def test_filename_line_includes_and_macro_selected_default_top(native_tools, scratch, monkeypatch):
    monkeypatch.chdir(scratch)
    header = scratch / "width.svh"
    header.write_text('`define WIDTH 8\n')
    code = """`include "width.svh"
`ifdef __ICARUS__
module first(input clk,input [`WIDTH-1:0] d,output reg [`WIDTH-1:0] q);
 localparam F=`__FILE__; localparam L=`__LINE__;
 always @(posedge clk) q<=d; endmodule
`else
module wrong(input clk,input [3:0] d,output reg [3:0] q);
 always @(posedge clk) q<=d^4'h8; endmodule
`endif
module second(input clk,input [1:0] d,output reg [1:0] q);
 always @(posedge clk) q<=d; endmodule
"""
    rtl, ref = files(scratch, code)
    report = dvh.diff_verify(rtl, ref, None, "all", 4, 0, True)
    evidence("include_default_top", report)
    assert report["verdict"] == "AGREE" and report["resolved_top"] == "first", report
    context = report["compilation_context"]
    assert context["ordered_sources"] == [str(rtl)]
    assert context["working_directory"] == str(scratch)
    assert [p["path"] for p in context["dependencies"]] == [str(rtl), str(header)]
    assert context["defines"] == context["include_dirs"] == context["parameter_overrides"] == []
    assert context["source_sha256"] == hashlib.sha256(rtl.read_bytes()).hexdigest()
    report = dvh.diff_verify(rtl, ref, "second", "all", 4, 0, True)
    evidence("include_requested_top", report)
    assert report["verdict"] == "AGREE" and report["driven_input"]["width"] == 2
    assert context["binding_sha256"] != report["compilation_context"]["binding_sha256"]


def test_source_changes_after_binding_do_not_change_executable_population(native_tools, scratch, monkeypatch):
    code = (F / "macro_pipe.sv").read_text().replace("d ^ 8'h80", "d")
    rtl, ref = files(scratch, code)
    load = dvh.load_reference
    def change_root(path):
        rtl.write_text((F / "macro_pipe.sv").read_text())
        return load(path)
    monkeypatch.setattr(dvh, "load_reference", change_root)
    report = dvh.diff_verify(rtl, ref, "macro_pipe", "all", 4, 0, True)
    evidence("frozen_population", report)
    assert report["verdict"] == "AGREE", report
    assert report["compilation_context"]["source_sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert report["compilation_context"]["source_sha256"] != hashlib.sha256(rtl.read_bytes()).hexdigest()


def test_native_width_disagreement_is_error_before_comparison(native_tools, scratch, monkeypatch):
    parse = dvh.parse_ports
    def incorrect_width(*args, **kwargs):
        name, ports, error = parse(*args, **kwargs)
        for p in ports:
            if p.name in ("d", "q"):
                p.width = 4
        return name, ports, error
    monkeypatch.setattr(dvh, "parse_ports", incorrect_width)
    report = check(scratch, (F / "macro_pipe.sv").read_text(), "macro_pipe", "native_width_refusal")
    assert report["verdict"] == "ERROR" and "DIFF_PORT_CONTEXT_MISMATCH" in report["reason"], report
    assert "first_mismatch" not in report


def test_unit_changes_during_elaboration_refuse_before_reference(native_tools, scratch, monkeypatch):
    parse, events = dvh.parse_ports, []
    def mutate(*args, **kwargs):
        result = parse(*args, **kwargs)
        kwargs["source_path"].write_text('module changed; endmodule')
        return result
    monkeypatch.setattr(dvh, "parse_ports", mutate)
    monkeypatch.setattr(dvh, "load_reference", lambda *a: events.append("reference"))
    report = check(scratch, (F / "narrowed_based.sv").read_text(), "narrowed_pipe", "changed_during_elaboration")
    assert report["verdict"] == "ERROR" and "DIFF_PORT_CONTEXT_CHANGED" in report["reason"], report
    assert events == [] and "driven_input" not in report


@pytest.mark.parametrize("tamper", ["unit", "top", "metadata", "removed", "malformed"],
                         ids=["unit-bytes", "selected-top", "context-metadata", "removed-unit", "malformed-context"])
def test_stale_or_mixed_binding_refuses_before_tb(native_tools, scratch, monkeypatch, tamper):
    bind, load, build = dvh._bind_rtl_unit, dvh.load_reference, dvh._build_tb
    captured, events = {}, []
    def capture(*args):
        result = bind(*args)
        captured["unit"], captured["binding"] = result[2], result[3]
        return result
    def modify(path):
        if tamper == "unit":
            captured["unit"].write_text('module changed; endmodule')
        elif tamper == "top":
            captured["binding"]["selected_top"] = "changed"
        elif tamper == "removed":
            captured["unit"].unlink()
        elif tamper == "malformed":
            captured["binding"]["ports"] = None
        else:
            captured["binding"]["parameter_overrides"] = ["W=4"]
        return load(path)
    def observe_tb(*args):
        events.append("TB")
        return build(*args)
    monkeypatch.setattr(dvh, "_bind_rtl_unit", capture)
    monkeypatch.setattr(dvh, "load_reference", modify)
    monkeypatch.setattr(dvh, "_build_tb", observe_tb)
    report = check(scratch, (F / "narrowed_based.sv").read_text(), "narrowed_pipe", "stale_" + tamper)
    assert report["verdict"] == "ERROR" and "DIFF_PORT_CONTEXT_CHANGED" in report["reason"], report
    assert events == []


@pytest.mark.parametrize("code", [
    'module bad(input clk,input real d,output [3:0] q); endmodule',
    'module bad(input clk,input [3:0] d [2][3],output [3:0] q); endmodule',
    'module bad(input clk,ref logic [3:0] d,output [3:0] q); endmodule',
    'module bad(input clk,input [UNBOUND:0] d,output [3:0] q); endmodule',
    '`include "missing_width.svh"\nmodule bad; endmodule',
    'module bad(clk,d,q); localparam W=8; input clk; input [W-1:0] d; endmodule',
], ids=["real", "nested-unpacked", "ref", "unbound", "missing-include", "incomplete-declaration"])
def test_context_or_port_refusal_precedes_reference_and_tb(native_tools, scratch, monkeypatch, code):
    rtl, ref = files(scratch, code)
    events = []
    monkeypatch.setattr(dvh, "load_reference", lambda *a: events.append("reference"))
    monkeypatch.setattr(dvh, "_build_tb", lambda *a: events.append("TB"))
    report = dvh.diff_verify(rtl, ref, "bad", "all", 4, 0, True)
    assert report["verdict"] == "ERROR" and "DIFF_PORT_" in report["reason"], report
    assert events == [] and "driven_input" not in report and "sampled_output" not in report


@pytest.mark.parametrize("require_tools,verdict", [(False, "SKIP"), (True, "ERROR")])
def test_missing_native_preprocessor_is_not_verified_before_reference(scratch, monkeypatch, require_tools, verdict):
    rtl, ref = files(scratch, (F / "integer_defaults.sv").read_text())
    events = []
    monkeypatch.setattr(dvh.shutil, "which", lambda name: None)
    monkeypatch.setattr(dvh, "load_reference", lambda *a: events.append("reference"))
    report = dvh.diff_verify(rtl, ref, "integer_pipe", "all", 4, 0, require_tools)
    assert report["verdict"] == verdict and report["tool_available"] is False, report
    assert "NOT_VERIFIED" in report["reason"] and events == []


def test_nonansi_localparam_inout_and_header_tie_order(native_tools, scratch):
    code = """module dut(clk,z,x,q,status,pad);
 localparam W=8; input clk; input [W-1:0] z,x;
 output reg [W-1:0] q; output status; inout [1:0] pad;
 always @(posedge clk) q<=z; assign status=0; endmodule
"""
    report = check(scratch, code, "dut", "nonansi_inout_scope")
    assert report["verdict"] == "AGREE", report
    assert report["driven_input"] == {"name": "z", "width": 8}
    assert report["undriven_data_inputs"] == ["x"] and report["unsampled_outputs"] == ["status"]
    assert report["unconnected_inout_ports"] == ["pad"]
    assert report["compilation_context"]["ports"][-1] == {"name": "pad", "direction": "inout", "width": 2}

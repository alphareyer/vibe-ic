"""Prompt-derived single-address byte SRAM observation; no reference CPU or FW.

The tiny DUTs below only exercise asynchronous reads and rising-edge writes.
They are not ISA implementations, benchmark inputs, oracle RTL or application
firmware. The actual ISA/reset producers emit the observers; Icarus executes
those emitted observers and the actual result readers judge their values.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


PROGRAMS = Path(os.environ.get("VIBEIC_SRAM_REVIEW_PROGRAMS",
                              str(Path(__file__).resolve().parent.parent))).resolve()
sys.path.insert(0, str(PROGRAMS))
import isa_suite_producer as ISA
import reset_invariant_oracle_tb_gen as RESET
import testbench_gen as TB


PROTOCOL = "single_port_8bit_async_read_posedge_write"
CASE = {"name": "reset_hold", "stimulus": "Reset asserted with a pending write",
        "expected": "SRAM bytes remain unchanged while reset is asserted"}
NATIVE = pytest.mark.skipif(not shutil.which("iverilog") or not shutil.which("vvp"),
                            reason="Icarus and vvp are required for actual emitted observation")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(root, *, kind="read_probe", wrong_address=False, bad_reset=False,
             mutation=None):
    project = root / "project"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    output = project / "plugin_output"
    output.mkdir()
    declaration = {
        "top_module": "toy_top", "clock_port_name": "clk", "reset_polarity": "active_high",
        "memsize_bytes": 16, "reset_pc": 0, "isa_units": ["I"], "rf_storage": "flops",
        "sram_interface_protocol": PROTOCOL,
        "sram_interface": {"address": "o_mem_addr", "write_data": "o_mem_data",
                           "read_data": "i_mem_data", "write_enable": "o_mem_we",
                           "read_latency_cycles": 0},
        # Explicit, prompt-derived verification data, never application FW.
        "verification_stimuli": {"reset_retention": {
            "memory_preload": [{"address": 3, "byte": 60}, {"address": 7, "byte": 167}]}}
    }
    header = """`timescale 1ns/1ps
module toy_top #(parameter MEM_BYTES=16)(
 input wire clk, input wire rst, input wire [7:0] i_mem_data,
 output wire [3:0] o_mem_addr, output wire [7:0] o_mem_data, output wire o_mem_we);
"""
    if kind == "read_probe":
        body = """
 reg [3:0] address=0;
 reg [7:0] data=0;
 reg enable=0;
 assign o_mem_addr=address;
 assign o_mem_data=data;
 assign o_mem_we=enable;
 initial begin
  wait(rst===1'b0);
  #1 address=3; #1;
  if(i_mem_data!==8'h3c) $fatal(1,"SPRAM_ASYNC_READ_FAIL first=%02x",i_mem_data);
  address=SECOND_ADDRESS; #1;
  if(i_mem_data!==8'ha7) $fatal(1,"SPRAM_ASYNC_READ_FAIL second=%02x",i_mem_data);
  $display("SPRAM_ASYNC_VALUES first=3c second=%02x",i_mem_data);
  @(negedge clk); address=8; data=1; enable=1;
  @(posedge clk); #1; enable=0;
 end
endmodule
""".replace("SECOND_ADDRESS", "6" if wrong_address else "7")
    else:
        assert kind == "reset_probe"
        body = """
 reg [2:0] state=0;
 always @(posedge clk) begin
  if(rst) state<=0;
  else if(state<3) state<=state+1;
 end
 assign o_mem_addr=(state==0) ? 3 : 8;
 assign o_mem_data=8'h7d;
 assign o_mem_we=(state==1) RESET_QUALIFIER;
endmodule
""".replace("RESET_QUALIFIER", "" if bad_reset else "&& !rst")
    source = header + body
    if mutation == "missing_role":
        declaration["sram_interface"].pop("write_enable")
    elif mutation == "read_latency":
        declaration["sram_interface"]["read_latency_cycles"] = 1
    elif mutation == "wrong_data_width":
        source = source.replace("input wire [7:0] i_mem_data", "input wire [6:0] i_mem_data")
    elif mutation == "unknown_protocol":
        declaration["sram_interface_protocol"] = "free_form_single_port_memory"
    elif mutation is not None:
        raise AssertionError(mutation)
    (output / "declaration.json").write_text(json.dumps(declaration, indent=2))
    (rtl / "toy_top.v").write_text(source)
    return project


def _bindings(project):
    facts, isa_reason = ISA.design_facts(project)
    top, ports, detail = TB.resolve_dut(project, "toy_top")
    assert top == "toy_top", detail
    inputs = [(name, width) for direction, width, name in ports if direction == "input"]
    outputs = [(name, width) for direction, width, name in ports if direction == "output"]
    memory, reset_reason = RESET.declared_retention_memory(project, top, inputs, outputs)
    return facts, memory, inputs, outputs, isa_reason, reset_reason


def _execute(project, work, emitted, top, plusargs=()):
    work.mkdir()
    observer = work / "observer.v"
    observer.write_text(emitted)
    dut = project / "phase2/stage1/rtl/toy_top.v"
    compile_argv = ["iverilog", "-g2012", "-s", top, "-o", "simulation", str(observer), str(dut)]
    compiled = subprocess.run(compile_argv, cwd=work, capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    run_argv = ["vvp", "simulation", *plusargs]
    run = subprocess.run(run_argv, cwd=work, capture_output=True, text=True, timeout=30)
    (work / "native.log").write_text(run.stdout + run.stderr)
    values = {"scope": "prompt-derived SRAM/tohost or reset observer component only; no ISA program or benchmark",
              "observer_sha256": _sha(observer), "dut_sha256": _sha(dut),
              "compile": {"argv": compile_argv, "rc": compiled.returncode,
                          "stdout": compiled.stdout, "stderr": compiled.stderr},
              "run": {"argv": run_argv, "rc": run.returncode,
                      "stdout": run.stdout, "stderr": run.stderr}}
    (work / "native-values.json").write_text(json.dumps(values, indent=2))
    return run, observer, values


def test_single_port_shared_binding_and_one_actual_connection(tmp_path):
    project = _project(tmp_path)
    facts, memory, _, _, isa_reason, reset_reason = _bindings(project)
    assert facts is not None and memory is not None, (isa_reason, reset_reason)
    assert facts["sram"]["kind"] == "sp"
    assert facts["sram"]["width"] == 8 and facts["sram"]["read_latency"] == 0
    assert memory["raddr"] == memory["waddr"] == "o_mem_addr"
    assert memory["depth"] == 16
    assert facts["sram_ports"] == {"raddr": memory["raddr"], "waddr": memory["waddr"],
                                   "wdata": memory["wdata"], "rdata": memory["rdata"],
                                   "wen": memory["we"]}
    emitted = ISA.gen_tb(facts)
    for port in ("o_mem_addr", "o_mem_data", "i_mem_data", "o_mem_we"):
        assert len(re.findall(r"\." + port + r"\s*\(", emitted)) == 1, emitted


@NATIVE
@pytest.mark.parametrize("wrong_address", [False, True], ids=["correct", "wrong-address"])
def test_emitted_isa_observer_reads_async_bytes_and_judges_native_tohost(tmp_path, wrong_address):
    project = _project(tmp_path, wrong_address=wrong_address)
    facts, reason = ISA.design_facts(project)
    assert facts is not None, reason
    work = tmp_path / "native-isa"
    image = tmp_path / "memory.hex"
    memory = bytearray(16)
    memory[3], memory[7] = 0x3c, 0xa7
    image.write_text("\n".join(f"{byte:02x}" for byte in memory) + "\n")
    run, _, values = _execute(project, work, ISA.gen_tb(facts), "isa_tb",
                              ["+hex=" + str(image), "+tohost=8"])
    parsed = ISA.parse_tb_transcript(run.stdout)
    verdict, why = ISA.judge_tohost(0, parsed)
    values["consumer"] = {"verdict": verdict, "reason": why,
                           "expected_tohost_source": "test-authored single byte write=1, not a reference ISA run"}
    (work / "native-values.json").write_text(json.dumps(values, indent=2))
    assert run.returncode == (1 if wrong_address else 0), values
    assert verdict == (ISA.FAIL if wrong_address else ISA.PASS), values
    assert ("SPRAM_ASYNC_READ_FAIL" if wrong_address else "SPRAM_ASYNC_VALUES") in run.stdout


@NATIVE
@pytest.mark.parametrize("bad_reset", [False, True], ids=["retained", "first-edge-corrupt"])
def test_emitted_reset_observer_measures_pending_first_edge_bytes(tmp_path, bad_reset):
    project = _project(tmp_path, kind="reset_probe", bad_reset=bad_reset)
    facts, memory, inputs, outputs, isa_reason, reset_reason = _bindings(project)
    assert facts is not None and memory is not None, (isa_reason, reset_reason)
    emitted = RESET.emit_case_oracle_from_ports(CASE, "toy_top", inputs, outputs, [], memory=memory)
    assert emitted is not None
    work = tmp_path / "native-reset"
    run, observer, values = _execute(project, work, emitted, CASE["name"])
    measured = RESET.retention_measurement(run.stdout, memory)
    sources = {str(project / "phase2/stage1/rtl/toy_top.v"):
               _sha(project / "phase2/stage1/rtl/toy_top.v")}
    evidence, why = RESET.capture_retention_execution(project, observer, work / "native.log", sources)
    values["consumer"] = {"measurement": measured, "captured": evidence, "reason": why}
    (work / "native-values.json").write_text(json.dumps(values, indent=2))
    assert evidence is not None and evidence["measurement"] == measured, values
    assert measured["complete"] is True and measured["changed"] is bad_reset, values
    assert measured["pending"] is not None
    assert run.returncode == (1 if bad_reset else 0), values
    if bad_reset:
        assert measured["samples"]["1"]["0"] != measured["baselines"]["1"], measured
    else:
        assert "PASS" in run.stdout


@pytest.mark.parametrize("mutation", ["missing_role", "read_latency", "wrong_data_width", "unknown_protocol"])
def test_single_port_consumers_keep_strict_contract_refusals(tmp_path, mutation):
    project = _project(tmp_path, mutation=mutation)
    facts, memory, _, _, isa_reason, reset_reason = _bindings(project)
    assert facts is None and isa_reason, (mutation, facts, isa_reason)
    assert memory is None and reset_reason, (mutation, memory, reset_reason)

"""Regression contracts for public issues #3237 and #3238.

These tests use only the public neutral issue inputs.  They exercise the real
``spec_coverage_check.py`` consumer and deliberately keep physical timing and
unrelated checklist gaps honest.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parent.parent
PROG = PROGRAMS / "spec_coverage_check.py"
sys.path.insert(0, str(PROGRAMS))

import spec_coverage_check as SC  # noqa: E402


CLOCK_PROMPT = """Current target clock period is 20 ns (50 MHz).
A prior implementation reached 250 MHz; this is historical information only.
Physical signoff requires post-route Fmax >= 47.6 MHz.
The input is clk and the output is result.
"""
CLOCK_RTL = (
    "module dut(input wire clk,output wire result); "
    "assign result=clk; endmodule\n"
)
CLOCK_TB = """`timescale 1ns/1ps
module tb; reg clk=0; wire result; time previous; dut u(clk,result); always #10 clk=~clk; initial begin @(posedge clk); previous=$time; @(posedge clk); if (($time-previous)!=20) $fatal(1,"period mismatch"); $display("CLOCK_PERIOD_PASS 20 ns"); $finish; end endmodule
"""

REG_PROMPT = """# Register map
Each of the16registers below must be written and read back.
| Address | Name | Access |
|---|---|---|
| 0x40 | R0 | R/W |
| 0x41 | R1 | R/W |
| 0x42 | R2 | R/W |
| 0x43 | R3 | R/W |
| 0x44 | R4 | R/W |
| 0x45 | R5 | R/W |
| 0x46 | R6 | R/W |
| 0x47 | R7 | R/W |
| 0x48 | R8 | R/W |
| 0x49 | R9 | R/W |
| 0x4a | R10 | R/W |
| 0x4b | R11 | R/W |
| 0x4c | R12 | R/W |
| 0x4d | R13 | R/W |
| 0x4e | R14 | R/W |
| 0x4f | R15 | R/W |
"""
REG_RTL = """module dut(input wire clk,input wire we,input wire [7:0] address,input wire [31:0] write_data,output wire [31:0] read_data); reg [31:0] mem[0:15]; always @(posedge clk) if(we && address>=8'h40 && address<8'h50) mem[address-8'h40]<=write_data; assign read_data=mem[address-8'h40]; endmodule
"""
REG_TB = """module tb; reg clk=0,we=0; reg[7:0] address=0; reg[31:0] write_data=0; wire[31:0]read_data; integer i; integer count=0; dut u(clk,we,address,write_data,read_data); always #1 clk=~clk; initial begin for(i=0;i<16;i=i+1)begin @(negedge clk); we=1;address=8'h40+i;write_data=i;@(posedge clk);#0.1;end @(negedge clk);we=0;for(i=0;i<16;i=i+1)begin address=8'h40+i;#1;if(read_data!==i)$fatal(1,"readback %0d",i);count=count+1;@(negedge clk);end if(count!=16)$fatal(1,"coverage_count %0d expected16",count);$display("LOOP_READBACK_PASS %0d addresses",count);$finish;end endmodule
"""


def _run(prompt: str, rtl: str, tb: str) -> dict:
    return SC.run({"user_prompt": prompt}, rtl, tb, None, True)


def _kind(report: dict, kind: str) -> list[dict]:
    return [item for item in report["items"] if item["kind"] == kind]


def _clock(report: dict, token: str) -> dict:
    return next(item for item in _kind(report, "clock_frequency")
                if token in item["requirement"])


def _registers(report: dict) -> dict[str, dict]:
    return {item["requirement"].split("offset ", 1)[1].split(",", 1)[0]: item
            for item in _kind(report, "register")}


def _reg_prompt(base: int, count: int) -> str:
    rows = "\n".join(
        f"| 0x{base + i:02x} | Q{i} | R/W |" for i in range(count)
    )
    return ("# Register map\n"
            f"Each of the {count} registers below must be written and read back.\n"
            "| Address | Name | Access |\n|---|---|---|\n" + rows + "\n")


def _loop_tb(base: int, start: int, bound_op: str, bound: str,
             step: str) -> str:
    return f"""module tb; reg clk=0,we=0; reg[7:0] address=0; reg[31:0] write_data=0; wire[31:0]read_data; integer i; integer limit=16; dut u(clk,we,address,write_data,read_data); always #1 clk=~clk; initial begin for(i={start};i{bound_op}{bound};i={step})begin we=1;address=8'h{base:02x}+i;write_data=i;@(posedge clk);end we=0;for(i={start};i{bound_op}{bound};i={step})begin address=8'h{base:02x}+i;#1;if(read_data!==i)$fatal(1,"readback");@(negedge clk);end $finish;end endmodule
"""


def test_issue3237_exact_cli_current_clock_is_proved_history_is_informational_and_physical_is_unmeasured(tmp_path: Path):
    prompt = tmp_path / "prompt.txt"
    rtl = tmp_path / "dut.v"
    tb = tmp_path / "tb.sv"
    report_path = tmp_path / "report.json"
    prompt.write_text(CLOCK_PROMPT)
    rtl.write_text(CLOCK_RTL)
    tb.write_text(CLOCK_TB)
    cp = subprocess.run(
        [sys.executable, "-B", str(PROG), "--prompt", str(prompt),
         "--rtl", str(rtl), "--tb", str(tb), "--strict", "--json",
         str(report_path)],
        text=True, capture_output=True, check=False,
    )
    report = json.loads(report_path.read_text())
    current = _clock(report, "50 MHz")
    history = _clock(report, "250 MHz")
    physical = _clock(report, "47.6 MHz")

    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert current["covered"] is True and current["block_eligible"] is True
    assert "measured 20 ns period" in current["coverage_note"]
    assert history["covered"] is False and history["block_eligible"] is False
    assert "historical" in history["coverage_note"]
    assert physical["covered"] is False and physical["block_eligible"] is False
    assert "NOT_MEASURED" in physical["coverage_note"]
    assert report["pass"] is False  # physical signoff is still open
    assert report["blocked"] is False  # but it is not a functional-TB emit veto


def test_issue3237_missing_period_proof_keeps_current_clock_blocking():
    tb = CLOCK_TB.replace(
        'previous=$time; @(posedge clk); if (($time-previous)!=20) $fatal(1,"period mismatch");',
        '@(posedge clk);',
    )
    report = _run(CLOCK_PROMPT, CLOCK_RTL, tb)
    assert _clock(report, "50 MHz")["covered"] is False
    assert report["blocked"] is True


def test_issue3237_wrong_measured_period_keeps_current_clock_blocking():
    tb = CLOCK_TB.replace("always #10", "always #5")
    report = _run(CLOCK_PROMPT, CLOCK_RTL, tb)
    assert _clock(report, "50 MHz")["covered"] is False
    assert report["blocked"] is True


def test_issue3237_unrelated_register_gap_is_not_dropped():
    prompt = CLOCK_PROMPT + """
| Address | Name | Access |
|---|---|---|
| 0x90 | EXTRA | R/W |
"""
    report = _run(prompt, CLOCK_RTL, CLOCK_TB)
    extra = _registers(report)["0x90"]
    assert extra["covered"] is False and extra["block_eligible"] is True
    assert report["blocked"] is True


def test_issue3238_exact_cli_finite_write_read_loops_cover_all_16(tmp_path: Path):
    prompt = tmp_path / "prompt.txt"
    rtl = tmp_path / "dut.v"
    tb = tmp_path / "tb.sv"
    report_path = tmp_path / "report.json"
    prompt.write_text(REG_PROMPT)
    rtl.write_text(REG_RTL)
    tb.write_text(REG_TB)
    cp = subprocess.run(
        [sys.executable, "-B", str(PROG), "--prompt", str(prompt),
         "--rtl", str(rtl), "--tb", str(tb), "--strict", "--json",
         str(report_path)],
        text=True, capture_output=True, check=False,
    )
    report = json.loads(report_path.read_text())
    registers = _registers(report)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert len(registers) == 16
    assert all(item["covered"] for item in registers.values())
    assert all("finite write/read loop" in item["coverage_note"]
               for item in registers.values())
    assert report["covered"] == report["checklist_items"] == 16


def test_issue3238_15_iterations_leave_only_0x4f_uncovered():
    report = _run(REG_PROMPT, REG_RTL, REG_TB.replace("i<16", "i<15"))
    registers = _registers(report)
    missing = {offset for offset, item in registers.items() if not item["covered"]}
    assert missing == {"0x4f"}
    assert report["blocking_gaps"] == 1 and report["blocked"] is True


def test_issue3238_reversed_bounds_cover_same_finite_set():
    tb = _loop_tb(0x40, 15, ">=", "0", "i-1")
    report = _run(REG_PROMPT, REG_RTL, tb)
    assert all(item["covered"] for item in _registers(report).values())


def test_issue3238_arbitrary_literal_base_is_not_hard_coded():
    prompt = _reg_prompt(0x80, 4)
    rtl = REG_RTL.replace("8'h40", "8'h80").replace("8'h50", "8'h84")
    tb = _loop_tb(0x80, 0, "<", "4", "i+1")
    report = _run(prompt, rtl, tb)
    assert set(_registers(report)) == {"0x80", "0x81", "0x82", "0x83"}
    assert all(item["covered"] for item in _registers(report).values())


def test_issue3238_decoy_address_loop_is_not_actual_bus_coverage():
    tb = REG_TB.replace("reg[7:0] address=0;", "reg[7:0] address=0,shadow_address=0;")
    tb = tb.replace("address=8'h40+i", "shadow_address=8'h40+i")
    report = _run(REG_PROMPT, REG_RTL, tb)
    assert not any(item["covered"] for item in _registers(report).values())
    assert report["blocked"] is True


def test_issue3238_unknown_dynamic_bound_is_refused():
    tb = _loop_tb(0x40, 0, "<", "limit", "i+1")
    report = _run(REG_PROMPT, REG_RTL, tb)
    assert not any(item["covered"] for item in _registers(report).values())
    assert report["blocked"] is True


def test_issue3238_write_only_loop_does_not_claim_readback_coverage():
    tb = REG_TB.split("we=0;for", 1)[0] + "we=0;$finish;end endmodule\n"
    report = _run(REG_PROMPT, REG_RTL, tb)
    assert not any(item["covered"] for item in _registers(report).values())


def test_issue3238_generic_loop_body_identifiers_do_not_claim_coverage():
    tb = """module tb; reg clk=0,we=0; reg[7:0] address=0,scratch=0; reg[31:0] write_data=0; wire[31:0]read_data; integer i; dut u(clk,we,address,write_data,read_data); always #1 clk=~clk; initial begin for(i=0;i<16;i=i+1)begin scratch=8'h40+i; write_data=i; if(scratch!==i) $display("write read register address"); end $finish; end endmodule
"""
    report = _run(REG_PROMPT, REG_RTL, tb)
    assert not any(item["covered"] for item in _registers(report).values())


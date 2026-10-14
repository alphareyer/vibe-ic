"""Tests for formal_complexity_classify.py.

Reproduces the worked k-induction-feasibility table from
skills/formal-verify/SKILL.md (timer_block / crc8_engine feasible;
aid_transceiver / cmd_processor / otp_controller BMC-needed) as real RTL,
plus the missing-data honesty paths.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "formal_complexity_classify.py"
assert SCRIPT.exists(), f"Script not found: {SCRIPT}"

sys.path.insert(0, str(SCRIPT.parent))
import formal_complexity_classify as fcc  # noqa: E402


# ---------------------------------------------------------------------------
# Real RTL fixtures matching the skill's worked table
# ---------------------------------------------------------------------------
# timer_block: 3 states, ~4 FFs, no mem, no deep counter -> prove feasible.
TIMER_BLOCK = """
module timer_block(input clk, input rst_n, input start, output reg done);
  localparam IDLE = 2'd0, RUN_STATE = 2'd1, DONE_STATE = 2'd2;
  reg [1:0] state;
  reg [1:0] tick;
  always @(posedge clk) begin
    if (!rst_n) begin
      state <= IDLE; tick <= 0; done <= 0;
    end else case (state)
      IDLE:       if (start) state <= RUN_STATE;
      RUN_STATE:  if (tick == 3) state <= DONE_STATE; else tick <= tick + 1;
      DONE_STATE: begin done <= 1; state <= IDLE; end
    endcase
  end
endmodule
"""

# crc8_engine: tiny, no counter, no memory -> prove feasible.
CRC8_ENGINE = """
module crc8_engine(input clk, input rst_n, input bit_in, output reg [7:0] crc);
  reg busy;
  always @(posedge clk) begin
    if (!rst_n) begin crc <= 8'h00; busy <= 1'b0; end
    else begin busy <= 1'b1; crc <= {crc[6:0], crc[7] ^ bit_in}; end
  end
endmodule
"""

# aid_transceiver: a 135-cycle timer -> BMC-needed, k must be >= 135.
AID_TRANSCEIVER = """
module aid_transceiver(input clk, input rst_n, input go, output reg pulse);
  reg [7:0] timer;
  always @(posedge clk) begin
    if (!rst_n) begin timer <= 0; pulse <= 0; end
    else if (go) begin
      if (timer >= 135) begin pulse <= 1; timer <= 0; end
      else timer <= timer + 1;
    end
  end
endmodule
"""

# otp_controller: 47-byte memory (376 bits) AND a 1650-cycle prog timer.
OTP_CONTROLLER = """
module otp_controller(input clk, input rst_n, input prog, output reg ready);
  reg [11:0] prog_timer;
  reg [7:0]  otp_mem [0:46];
  always @(posedge clk) begin
    if (!rst_n) begin prog_timer <= 0; ready <= 0; end
    else if (prog) begin
      if (prog_timer == 1650) begin ready <= 1; prog_timer <= 0; end
      else prog_timer <= prog_timer + 1;
    end
  end
endmodule
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# PASS path: a prove-feasible module
# ---------------------------------------------------------------------------
def test_timer_block_is_prove_feasible(tmp_path):
    _write(tmp_path, "timer_block.v", TIMER_BLOCK)
    rep = fcc.run([str(tmp_path)])
    assert rep.passed is True
    assert rep.summary["exit"] == 0
    m = next(m for m in rep.modules if m.module == "timer_block")
    assert m.recommended_mode == "prove"
    assert m.infeasible_reason is None
    assert m.min_k_bound == fcc.DEFAULT_K  # small counter (3) < DEFAULT_K
    assert m.ff_count < fcc.FF_ENVELOPE


def test_crc8_engine_is_prove_feasible(tmp_path):
    _write(tmp_path, "crc8.v", CRC8_ENGINE)
    rep = fcc.run([str(tmp_path)])
    m = next(m for m in rep.modules if m.module == "crc8_engine")
    assert m.recommended_mode == "prove"
    assert m.mem_bits == 0
    assert m.max_counter < fcc.DEEP_COUNTER


# ---------------------------------------------------------------------------
# Real FAIL path: deep-counter / big-memory modules need BMC
# ---------------------------------------------------------------------------
def test_aid_transceiver_needs_bmc_deep_counter(tmp_path):
    _write(tmp_path, "aid.v", AID_TRANSCEIVER)
    rep = fcc.run([str(tmp_path)])
    m = next(m for m in rep.modules if m.module == "aid_transceiver")
    assert m.recommended_mode == "bmc"
    assert m.max_counter == 135
    # k must be >= counter max value (the skill's stated rule)
    assert m.min_k_bound >= 135
    assert "counter" in m.infeasible_reason.lower()


def test_otp_controller_needs_bmc_memory_and_timer(tmp_path):
    _write(tmp_path, "otp.v", OTP_CONTROLLER)
    rep = fcc.run([str(tmp_path)])
    m = next(m for m in rep.modules if m.module == "otp_controller")
    assert m.recommended_mode == "bmc"
    assert m.mem_bits == 47 * 8  # 376 bits, matches the skill table
    assert m.max_counter == 1650
    assert m.min_k_bound >= 1650
    reason = m.infeasible_reason.lower()
    assert "memory" in reason
    assert "counter" in reason or "timer" in reason


def test_all_modules_bmc_makes_run_fail(tmp_path):
    # When NO module fits the prove envelope, the run FAILs (exit 1): the
    # caller cannot claim "proven for all reachable states".
    _write(tmp_path, "otp.v", OTP_CONTROLLER)
    _write(tmp_path, "aid.v", AID_TRANSCEIVER)
    rep = fcc.run([str(tmp_path)])
    assert rep.summary["prove_feasible"] == 0
    assert rep.passed is False
    assert rep.summary["exit"] == 1


def test_mixed_corpus_passes_when_one_provable(tmp_path):
    _write(tmp_path, "timer.v", TIMER_BLOCK)
    _write(tmp_path, "otp.v", OTP_CONTROLLER)
    rep = fcc.run([str(tmp_path)])
    assert rep.summary["prove_feasible"] >= 1
    assert rep.summary["bmc_needed"] >= 1
    assert rep.passed is True


# ---------------------------------------------------------------------------
# Honesty paths: never a vacuous PASS on missing/garbage input
# ---------------------------------------------------------------------------
def test_no_input_is_exit_2_not_pass():
    rep = fcc.run([])
    assert rep.passed is False
    assert rep.summary["exit"] == 2
    assert any(f.rule == "NO_INPUT" for f in rep.findings)


def test_empty_dir_is_exit_2(tmp_path):
    rep = fcc.run([str(tmp_path)])
    assert rep.passed is False
    assert rep.summary["exit"] == 2
    assert any(f.rule == "NO_RTL" for f in rep.findings)


def test_garbage_rtl_is_exit_2_no_vacuous_pass(tmp_path):
    # A file with no parseable module must NOT vacuously PASS.
    _write(tmp_path, "junk.v", "this is not verilog at all !!! { } ; ;")
    rep = fcc.run([str(tmp_path)])
    assert rep.passed is False
    assert rep.summary["exit"] == 2
    assert any(f.rule == "NO_MODULE" for f in rep.findings)


# ---------------------------------------------------------------------------
# CLI contract: exit codes + JSON shape
# ---------------------------------------------------------------------------
def test_cli_json_and_exit_codes(tmp_path):
    _write(tmp_path, "timer.v", TIMER_BLOCK)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "--json"],
        capture_output=True, text=True)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["program"] == "formal_complexity_classify"
    assert data["passed"] is True
    assert data["modules"][0]["recommended_mode"] == "prove"


def test_cli_exit_2_on_missing_dir(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "nope"), "--json"],
        capture_output=True, text=True)
    assert r.returncode == 2


def test_cli_exit_1_when_all_bmc(tmp_path):
    _write(tmp_path, "otp.v", OTP_CONTROLLER)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "--json"],
        capture_output=True, text=True)
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["summary"]["prove_feasible"] == 0


# ---------------------------------------------------------------------------
# Guard: do not false-fire on the real in-repo RTL corpus
# ---------------------------------------------------------------------------
def test_runs_clean_on_real_reference_tb():
    # The only .v in this repo is a port-less testbench
    # (`module aid_class_reference_tb;`), which the shared gate_utils.find_modules
    # parser deliberately skips (it requires an ANSI port list). The program must
    # therefore return an HONEST exit 2 (NO_MODULE) — never a vacuous PASS — and
    # must not crash on the real file's parameter/array/timer constructs.
    repo_rtl = (SCRIPT.parent.parent / "tools" / "protocol_tb"
                / "aid_class_reference_tb.v")
    if not repo_rtl.exists():
        pytest.skip("reference TB not present in this checkout")
    rep = fcc.run([str(repo_rtl)])
    assert rep.passed is False
    assert rep.summary["exit"] == 2  # honest: no parseable module, no PASS
    # Every classification that DID parse (none here) would still be well-formed.
    for m in rep.modules:
        assert m.recommended_mode in ("prove", "bmc")
        assert m.min_k_bound >= fcc.DEFAULT_K


def test_no_false_fire_on_realistic_param_timer_module(tmp_path):
    # Guard: a legitimate small module that uses the SAME constructs as the real
    # reference TB (named `parameter` timing constants, a small bit-cell counter,
    # a modest response buffer) must NOT be wrongly flagged BMC when its counter
    # terminal and memory stay within the prove envelope.
    rtl = """
    module bit_cell_fsm(input clk, input rst_n, input sample, output reg bit_out);
      parameter T_BIT_THRESHOLD = 8;        // small terminal, < DEEP_COUNTER
      parameter BUF_DEPTH       = 8;        // small buffer
      localparam IDLE = 2'd0, COUNT_STATE = 2'd1, EMIT_STATE = 2'd2;
      reg [1:0] state;
      reg [3:0] low_ticks;
      reg [7:0] sbuf [0:BUF_DEPTH-1];       // 8*8 = 64 bits, < MEM_BIG_BITS
      always @(posedge clk) begin
        if (!rst_n) begin state <= IDLE; low_ticks <= 0; bit_out <= 0; end
        else case (state)
          IDLE:        if (sample) state <= COUNT_STATE;
          COUNT_STATE: if (low_ticks >= T_BIT_THRESHOLD) state <= EMIT_STATE;
                       else low_ticks <= low_ticks + 1;
          EMIT_STATE:  begin bit_out <= 1; state <= IDLE; low_ticks <= 0; end
        endcase
      end
    endmodule
    """
    _write(tmp_path, "bit_cell_fsm.v", rtl)
    rep = fcc.run([str(tmp_path)])
    m = next(m for m in rep.modules if m.module == "bit_cell_fsm")
    # 64 mem bits < 256, counter 8 < 64, FFs well under 100 -> stays provable.
    assert m.recommended_mode == "prove", m.infeasible_reason
    assert m.mem_bits == 64
    assert m.max_counter == 8
    assert rep.passed is True


# Issue3144: public CLI counter-depth contracts and local state-relation controls.
_ISSUE3144_CASES = [{'id': 'addr_high', 'rtl': "module addr_high #(parameter [31:0] MATCH_ADDR=32'h90000000)(input wire [31:0] addr, output wire hit); assign hit=addr==MATCH_ADDR; endmodule", 'expected': [0, 'prove', 20, 0]}, {'id': 'addr_low', 'rtl': "module addr_low #(parameter [31:0] MATCH_ADDR=32'h00000010)(input wire [31:0] addr, output wire hit); assign hit=addr==MATCH_ADDR; endmodule", 'expected': [0, 'prove', 20, 0]}, {'id': 'addr_decimal', 'rtl': 'module addr_decimal(input wire [31:0] addr, output wire hit); assign hit=addr==2415919104; endmodule', 'expected': [0, 'prove', 20, 0]}, {'id': 'payload', 'rtl': "module payload #(parameter [31:0] MATCH_ADDR=32'h90000000)(input wire clk, input wire [31:0] data_in, output wire hit); reg [31:0] data_reg; always @(posedge clk) data_reg<=data_in; assign hit=data_reg==MATCH_ADDR; endmodule", 'expected': [0, 'prove', 20, 0]}, {'id': 'counter_literal', 'rtl': 'module counter_literal(input wire clk, input wire rst, input wire go, output reg pulse); reg [7:0] ticks; always @(posedge clk) begin if(rst) begin ticks<=0; pulse<=0; end else if(go) begin if(ticks>=135) begin ticks<=0; pulse<=1; end else ticks<=ticks+1; end end endmodule', 'expected': [135, 'bmc', 135, 1]}, {'id': 'counter_param', 'rtl': 'module counter_param(input wire clk, input wire rst, input wire go, output reg pulse); parameter LIMIT=135; reg [7:0] ticks; always @(posedge clk) begin if(rst) begin ticks<=0; pulse<=0; end else if(go) begin if(ticks>=LIMIT) begin ticks<=0; pulse<=1; end else ticks<=ticks+1; end end endmodule', 'expected': [135, 'bmc', 135, 1]}, {'id': 'counter_header', 'rtl': 'module counter_header #(parameter LIMIT=135)(input wire clk, input wire rst, input wire go, output reg pulse); reg [7:0] ticks; always @(posedge clk) begin if(rst) begin ticks<=0; pulse<=0; end else if(go) begin if(ticks>=LIMIT) begin ticks<=0; pulse<=1; end else ticks<=ticks+1; end end endmodule', 'expected': [135, 'bmc', 135, 1]}, {'id': 'decrement_reload', 'rtl': 'module downward(input clk, input load, output hit); reg [7:0] phase; always @(posedge clk) begin if(load) phase<=135; else if(phase>0) phase<=phase-1; end assign hit=phase==0; endmodule', 'expected': [135, 'bmc', 135, 1]}, {'id': 'combinational_register', 'rtl': 'module comb_state(input [7:0] a, output hit); reg [7:0] phase; always @* phase=phase+1; assign hit=phase>=135; endmodule', 'expected': [0, 'prove', 20, 0]}, {'id': 'constant_load_not_timer', 'rtl': 'module constant_data(input clk, output hit); reg [7:0] phase; always @(posedge clk) phase<=135; assign hit=phase==135; endmodule', 'expected': [0, 'prove', 20, 0]}, {'id': 'blocking_clocked_counter', 'rtl': 'module blocking_clocked_counter(input wire clk, input wire rst, input wire go, output reg pulse); reg [7:0] ticks; always @(posedge clk) begin if(rst) begin ticks=0; pulse=0; end else if(go) begin if(ticks>=135) begin ticks=0; pulse=1; end else ticks=ticks+1; end end endmodule', 'expected': [135, 'bmc', 135, 1]}, {'id': 'reverse_parameter_compare', 'rtl': 'module reverse_parameter_compare #(parameter LIMIT=135)(input wire clk, input wire rst, input wire go, output reg pulse); reg [7:0] ticks; always @(posedge clk) begin if(rst) begin ticks<=0; pulse<=0; end else if(go) begin if(LIMIT<=ticks) begin ticks<=0; pulse<=1; end else ticks<=ticks+1; end end endmodule', 'expected': [135, 'bmc', 135, 1]}, {'id': 'payload_beside_counter', 'rtl': 'module payload_beside_counter(input wire clk, input wire rst, input wire go, output reg pulse); reg [7:0] ticks; wire [31:0] payload; assign payload={32{go}}; wire address_hit=payload==2415919104; always @(posedge clk) begin if(rst) begin ticks<=0; pulse<=0; end else if(go) begin if(ticks>=135) begin ticks<=0; pulse<=1; end else ticks<=ticks+1; end end endmodule', 'expected': [135, 'bmc', 135, 1]}]

@pytest.mark.parametrize("case", _ISSUE3144_CASES, ids=lambda case: case["id"])
def test_issue3144_temporal_depth_uses_clocked_self_update(tmp_path, case):
    path = _write(tmp_path, case["id"] + ".v", case["rtl"] + "\n")
    argv = [sys.executable, "-B", str(SCRIPT), str(path), "--json"]
    process = subprocess.run(argv, capture_output=True, text=True)
    # The advisory CLI status is part of the contract; BMC rc1 is not a
    # failed source test when it faithfully classifies a genuine counter.
    (tmp_path / "cli-argv.json").write_text(json.dumps(argv, indent=2) + "\n")
    (tmp_path / "cli-stdout.json").write_text(process.stdout)
    (tmp_path / "cli-stderr.txt").write_text(process.stderr)
    (tmp_path / "cli-exit.json").write_text(json.dumps({"exit_code": process.returncode}) + "\n")
    report = json.loads(process.stdout)
    assert len(report["modules"]) == 1
    actual = report["modules"][0]
    observed = [actual["max_counter"], actual["recommended_mode"],
                actual["min_k_bound"], process.returncode]
    assert observed == case["expected"], (case["id"], observed, case["expected"])

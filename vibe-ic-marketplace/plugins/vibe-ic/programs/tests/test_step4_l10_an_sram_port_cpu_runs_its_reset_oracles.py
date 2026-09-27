#!/usr/bin/env python3
"""FX_STEP4_L10_CASES — which declared L10 cases a reused CPU core whose top is
an SRAM port can execute, asserted where Step 4's denominator is made.

MEASURED on subservient x gf180mcuD phase 2 (main 7fac744e1): 1 of the 9
declared functional cases executed its own oracle. The case table, per case,
is in the lane report. In short:

  class (i)   the flow can run it from the design input alone. There are three,
              all RESET-family, stated as observable pin behaviour by the
              verification plan:
                reset_n_cycle_instruction   first fetch within N cycles
                reset_assert_sram           no write while reset is asserted
                i_rst_glitch_...            no fetch race around a reset glitch
              On main only the second ran. The other two need a bus-activity
              output to observe, and the generators knew only handshake names
              (cyc/stb/req/valid). A core whose top is an SRAM port shows its
              fetch as a READ-ENABLE, so both fell to the substance floor.
  class (ii)  the input lacks what the case needs: blinky.hex / hello.hex (the
              plan names firmware the input does not carry), zifencei and the
              RV32I suite (no program or testbench, and the expected result is
              only "PASS"). The flow may not author those (§4.05).
  design N/A  M / Zicsr / C apply only if the design selects the option, and
              the design's declaration selects none.

This pins the class-(i) half at the producer: every reset-family case gets a
GENERATED oracle, and no other case gets one fabricated. It is RED on main
7fac744e1 for the two cases above, and GREEN once the generators take a
read-enable when the top exposes no handshake (lane rvp2's FX_P2, which this
branch stacks on). The fixture is shaped like the design: an SRAM-port top and
the verification plan's own case texts. No design name is used.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import testbench_gen as T  # noqa: E402

TOP = "core"

#: The verification plan's cases, as Phase 1 records them (L10 test_cases).
CASES = [
    {"name": "blinky_hex", "kind": "functional_vector",
     "stimulus": "blinky.hex", "expected": "GPIO 輸出規則性 toggle"},
    {"name": "hello_hex", "kind": "functional_vector",
     "stimulus": "hello.hex",
     "expected": "GPIO 輸出 \"Hello\" UART 字串(115200 baud rate by firmware "
                 "bit-banging)"},
    {"name": "rv32i_40", "kind": "functional_vector",
     "stimulus": "整套 RV32I 指令(40+ 條)單元測試",
     "expected": "100% PASS(可用 RISC-V Compliance suite 或 SERV 內附 testbench)"},
    {"name": "zifencei", "kind": "functional_vector",
     "stimulus": "Zifencei 指令", "expected": "PASS"},
    {"name": "plugin_m_mul_div", "kind": "functional_vector",
     "stimulus": "(若 Plugin 選 M) Mul/Div 指令", "expected": "PASS",
     "applies_when": {"option": "M", "stated": "(若 Plugin 選 M)"}},
    {"name": "plugin_zicsr_csr_access_timer_irq", "kind": "functional_vector",
     "stimulus": "(若 Plugin 選 Zicsr) CSR access + timer IRQ", "expected": "PASS",
     "applies_when": {"option": "Zicsr", "stated": "(若 Plugin 選 Zicsr)"}},
    {"name": "plugin_c_16_bit_compressed", "kind": "functional_vector",
     "stimulus": "(若 Plugin 選 C) 16-bit compressed 指令", "expected": "PASS",
     "applies_when": {"option": "C", "stated": "(若 Plugin 選 C)"}},
    {"name": "reset_n_cycle_instruction", "kind": "functional_vector",
     "stimulus": "Reset 解除後 N cycle 內取得第一條 instruction",
     "expected": "N ≤ SERV-MINI 策略決定的最大 boot latency(典型 < 10 cycle)"},
    {"name": "reset_assert_sram", "kind": "functional_vector",
     "stimulus": "Reset assert 中 SRAM 內容保留",
     "expected": "holds: Reset assert 中 SRAM 內容保留",
     "oracle_from_assertion_row": True, "assertion_affirmation": "✅"},
    {"name": "i_rst_glitch_instruction_fetch_race", "kind": "functional_vector",
     "stimulus": "i_rst glitch 不應導致 instruction fetch race",
     "expected": "holds: i_rst glitch 不應導致 instruction fetch race",
     "oracle_from_assertion_row": True,
     "assertion_affirmation": "✅(同步 reset 保證)"},
]

#: A bit-serial core's top: clock, reset, one SRAM port, a GPIO. No handshake.
RTL = """module core #(parameter memsize = 1024, parameter aw = $clog2(memsize))
  (input  wire          i_clk,
   input  wire          i_rst,
   output wire [aw-1:0] o_sram_waddr,
   output wire [7:0]    o_sram_wdata,
   output wire          o_sram_wen,
   output wire [aw-1:0] o_sram_raddr,
   input  wire [7:0]    i_sram_rdata,
   output wire          o_sram_ren,
   output wire          o_gpio);
  reg [aw-1:0] pc;
  always @(posedge i_clk) pc <= i_rst ? {aw{1'b0}} : pc + 1'b1;
  assign o_sram_raddr = pc;
  assign o_sram_ren   = !i_rst;
  assign o_sram_waddr = {aw{1'b0}};
  assign o_sram_wdata = i_sram_rdata;
  assign o_sram_wen   = 1'b0;
  assign o_gpio       = pc[0];
endmodule
"""

#: case -> (the emitter that owns it, the output it must observe)
CLASS_I = {
    "reset_n_cycle_instruction": ("_emit_case_boot_latency_oracle", "o_sram_ren"),
    "reset_assert_sram": ("_emit_case_reset_invariant_oracle", "o_sram_wen"),
    "i_rst_glitch_instruction_fetch_race": ("_emit_case_reset_invariant_oracle",
                                            "o_sram_ren"),
}


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    proj = tmp_path_factory.mktemp("sram_port_cpu")
    docs = proj / "phase1" / "generated_docs"
    docs.mkdir(parents=True)
    (docs / "L10_TEST_CASES.json").write_text(json.dumps(
        {"schema_version": 2, "doc_class": "test_cases", "test_cases": CASES},
        ensure_ascii=False))
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / f"{TOP}.v").write_text(RTL)
    n = T.emit_unit_tbs(proj, TOP, kind=T.DEFAULT_SCAFFOLD_KIND, report={})
    assert n == len(CASES), n
    tb = proj / "phase2" / "stage1" / "sim" / "tb"
    return {c["name"]: (tb / f"{c['name']}.v").read_text() for c in CASES}


def _oracle(text: str):
    for line in text.splitlines():
        if T.ORACLE_GENERATED_MARKER in line:
            return line.split(T.ORACLE_GENERATED_MARKER, 1)[1].strip()
    return None


@pytest.mark.parametrize("case", sorted(CLASS_I))
def test_every_reset_family_case_gets_its_own_generated_oracle(emitted, case):
    """RED on main 7fac744e1 for reset_n_cycle_instruction and
    i_rst_glitch_instruction_fetch_race: with no handshake output to observe,
    both fell to the substance floor although the design input states them
    completely."""
    emitter, observed = CLASS_I[case]
    text = emitted[case]
    assert _oracle(text) == emitter, text[:600]
    assert T.ORACLE_NONE_MARKER not in text
    assert f"'{observed}'" in text, text[:1200]


@pytest.mark.parametrize("case", sorted(c["name"] for c in CASES
                                        if c["name"] not in CLASS_I))
def test_no_oracle_is_fabricated_for_a_case_the_input_cannot_ground(emitted,
                                                                    case):
    """The paired half, green on both trees. Firmware the input does not
    carry, an instruction test with no program and only 'PASS' as its
    expectation, and an option the design does not select get the substance
    floor and no generated oracle. Step 4 books them with their reasons; an
    oracle here would be an invented one."""
    text = emitted[case]
    assert _oracle(text) is None, _oracle(text)
    assert T.ORACLE_NONE_MARKER in text

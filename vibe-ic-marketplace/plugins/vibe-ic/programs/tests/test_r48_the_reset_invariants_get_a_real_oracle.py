"""The two L10 cases that need nothing delivered get a real oracle.

MEASURED on subservient x gf180mcuD as a DIE, FRONT DOOR, run r48 (lane
icsub2, host 8HD-4, tree 41d3b39b8, image 0.3.67 sha256:4e9f54ef): 1 of 10
declared L10 cases executed its own oracle. `reset_assert_sram` and
`i_rst_glitch_instruction_fetch_race` need NO delivered program — they are
properties over the design's own declared observables — and fell to the
substance-floor scaffold only because the flow owned an emitter for one
property family (the "within N cycles of reset release" convention, which is
why `reset_n_cycle_instruction` was the single case that ran) and none for
the reset-ASSERT family. R-0915-102(2).

MEASURED end-to-end with iverilog in the pinned image, on the real declared
cases and the real DUT port surface (observed outputs picked structurally:
`o_sram_we` for the hold family, `o_sram_cyc` for the glitch family):

    arm                                   reset_assert_sram   glitch
    shipped RTL (registered boundary)     PASS                PASS
    mutant (reset branch drives it to 1)  FAIL (32 checks)    FAIL (1 check)
    pre-R-0915-99 combinational boundary  PASS                FAIL (1 check)

The mutant arm is what makes these oracles evidence rather than decoration.
The third arm is not a claim about the registered-boundary change — the hold
invariant holds in both revisions — but it does show the glitch oracle
catching a combinational control boundary on its own.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reset_invariant_oracle_tb_gen as R  # noqa: E402
import testbench_gen as T  # noqa: E402

_IN = [("i_clk", ""), ("i_rst", ""), ("i_sram_data", "[7:0]")]
_OUT = [("o_sram_addr", "[9:0]"), ("o_sram_data", "[7:0]"),
        ("o_sram_we", ""), ("o_sram_cyc", ""), ("o_gpio", "")]

_HOLD = {"name": "reset_assert_sram", "kind": "functional_vector",
         "stimulus": "Reset assert 中 SRAM 內容保留",
         "expected": "holds: Reset assert 中 SRAM 內容保留"}
_GLITCH = {"name": "i_rst_glitch_instruction_fetch_race",
           "kind": "functional_vector",
           "stimulus": "i_rst glitch 不應導致 instruction fetch race",
           "expected": "holds: i_rst glitch 不應導致 instruction fetch race"}


def test_each_case_is_claimed_by_exactly_its_own_family():
    assert R.case_family(_HOLD) == R.FAMILY_HOLD
    assert R.case_family(_GLITCH) == R.FAMILY_GLITCH


def test_the_release_side_family_is_not_claimed():
    """The boot-latency emitter owns "within N cycles of reset RELEASE"."""
    boot = {"name": "reset_n_cycle_instruction",
            "stimulus": "Reset 解除後 N cycle 內取得第一條指令",
            "expected": "第一個 bus access 在 10 cycle 內"}
    assert R.case_family(boot) is None


def test_an_unrelated_case_grounds_nothing():
    for c in ({"name": "blinky_hex", "stimulus": "blinky.hex",
               "expected": "GPIO 輸出規則性 toggle"},
              {"name": "rv32i_40", "stimulus": "整套 RV32I 指令",
               "expected": "100% PASS"}):
        assert R.case_family(c) is None
        assert R.emit_case_oracle_from_ports(c, "dut", _IN, _OUT, []) is None


def test_a_case_that_reads_as_both_is_refused():
    both = {"name": "x",
            "stimulus": "Reset assert 中 SRAM 內容保留; i_rst glitch",
            "expected": "不應導致 instruction fetch race"}
    assert R.case_family(both) is None


def test_the_hold_oracle_watches_the_write_output():
    tb = R.emit_case_oracle_from_ports(_HOLD, "subservient", _IN, _OUT, [])
    assert "o_sram_we" in tb
    assert "RESET_ASSERT_HOLD" in tb
    assert "$fatal(1);" in tb, "a check that cannot fail is not an oracle"
    assert "@(negedge i_clk);" in tb, "sample mid-cycle, never on the edge"


def test_the_glitch_oracle_watches_the_bus_activity_output():
    tb = R.emit_case_oracle_from_ports(_GLITCH, "subservient", _IN, _OUT, [])
    assert "o_sram_cyc" in tb
    assert "RESET_GLITCH_NO_RACE" in tb
    assert "$fatal(1);" in tb
    # the window closes when reset is released: ordinary resumption after a
    # glitch is not a race, and an earlier draft that stayed open past the
    # release FAILed the shipped design for restarting.
    i_check = tb.index("asserted inside the reset glitch")
    i_release = tb.index("i_rst = 1'b0;", i_check)
    assert i_release > i_check


def test_no_reset_port_grounds_nothing():
    ports = [("i_clk", "")]
    assert R.emit_case_oracle_from_ports(_HOLD, "dut", ports, _OUT, []) is None


def test_no_clock_grounds_nothing():
    assert R.emit_case_oracle_from_ports(
        _HOLD, "dut", [("i_rst", "")], _OUT, []) is None


def test_no_observable_output_grounds_nothing():
    """A DUT surface with nothing to watch defers to the scaffold."""
    outs = [("o_gpio", ""), ("o_led", "")]
    assert R.emit_case_oracle_from_ports(_HOLD, "dut", _IN, outs, []) is None
    assert R.emit_case_oracle_from_ports(_GLITCH, "dut", _IN, outs, []) is None


def test_an_active_low_reset_is_driven_the_right_way_round():
    ins = [("clk", ""), ("rst_n", ""), ("din", "[7:0]")]
    tb = R.emit_case_oracle_from_ports(_HOLD, "dut", ins, _OUT, [])
    assert "rst_n = 1'b0;" in tb, "asserted is LOW for an active-low reset"


def test_the_emitter_is_wired_into_the_producer_and_names_itself(tmp_path):
    out = tmp_path / "tb"
    out.mkdir()
    ports = [("input", w, n) for n, w in _IN] + \
            [("output", w, n) for n, w in _OUT]
    got = T._emit_case_reset_invariant_oracle(
        tmp_path, _HOLD, "subservient", ports, out, {})
    assert got == out / "reset_assert_sram.v"
    text = got.read_text()
    assert T.ORACLE_GENERATED_MARKER in text
    assert "_emit_case_reset_invariant_oracle" in text


def test_the_producer_defers_when_the_case_is_not_ours(tmp_path):
    out = tmp_path / "tb"
    out.mkdir()
    ports = [("input", w, n) for n, w in _IN] + \
            [("output", w, n) for n, w in _OUT]
    assert T._emit_case_reset_invariant_oracle(
        tmp_path, {"name": "blinky_hex", "stimulus": "blinky.hex",
                   "expected": "toggle"}, "subservient", ports, out, {}) is None
    assert list(out.iterdir()) == []


def test_work_already_authored_is_not_clobbered(tmp_path):
    out = tmp_path / "tb"
    out.mkdir()
    (out / "reset_assert_sram.v").write_text("module authored; endmodule\n")
    ports = [("input", w, n) for n, w in _IN] + \
            [("output", w, n) for n, w in _OUT]
    got = T._emit_case_reset_invariant_oracle(
        tmp_path, _HOLD, "subservient", ports, out, {})
    assert "authored" in got.read_text()

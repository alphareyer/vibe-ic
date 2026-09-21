"""A delivered testbench binds THIS implementation's ports, not the typical set.

L3:33 leaves the SRAM signal NAMES and L3:73 the bus PROTOCOL to the
implementation's own `declaration.json`; L3:37-41 then writes down a "typical"
set. A delivered testbench can only bind that typical set -- it is the only set
stated anywhere in the design input -- and the delivered subservient
testbenches say so in their own headers: "an implementation that declares a
different SRAM signal set or a different read latency will not bind to this
file". True of the file; this makes it untrue of the FLOW.

MEASURED, end to end, with iverilog in the pinned image (0.3.67
sha256:4e9f54ef), on the ACTUAL delivered testbench from benchmark-data PR #20
and the real `blinky.hex`:

  * a declaration naming addr/wdata/rdata/we/cyc as mem_a/mem_dout/mem_din/
    mem_wr/mem_en rebinds all five, and the rebound testbench COMPILES and
    PASSES against a DUT carrying those names:
        [TB blinky_hex] observed 6 transitions, mean half-period 31170.0 ns
        [TB blinky_hex] PASS -- o_gpio toggled 6 times with a regular period
  * subservient's own r48 declaration names NO SRAM ports (it declares
    `sram_interface_protocol` and `sram_interface_timing` only) and a read
    latency of 1, so nothing is rebound, nothing is retimed, and the installed
    bytes are identical to the delivered ones.

Both directions throughout: a silent declaration changes nothing, a role the
declaration omits keeps its typical name, the testbench's OWN nets are never
touched, and a declared read latency the testbench cannot express is REFUSED
BY NAME rather than installed to sample where the design does not.
"""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import testbench_gen as T  # noqa: E402

_TB = """// a delivered testbench
module blinky_hex;
  wire [9:0] o_sram_addr;
  wire [7:0] o_sram_data;
  reg  [7:0] i_sram_data;
  wire o_sram_we, o_sram_cyc, o_gpio;
  reg  [7:0] mem [0:1023];
  always @(posedge i_clk) if (o_sram_cyc) begin
    if (o_sram_we) mem[o_sram_addr] <= o_sram_data;
    else           i_sram_data      <= mem[o_sram_addr];
  end
  subservient dut (
      .i_clk       (i_clk),
      .i_rst       (i_rst),
      .o_sram_addr (o_sram_addr),
      .o_sram_data (o_sram_data),
      .i_sram_data (i_sram_data),
      .o_sram_we   (o_sram_we),
      .o_sram_cyc  (o_sram_cyc),
      .o_gpio      (o_gpio));
endmodule
"""
_DECL = {"addr": "mem_a", "wdata": "mem_dout", "rdata": "mem_din",
         "we": "mem_wr", "cyc": "mem_en"}


def _project(tmp_path, declaration):
    d = tmp_path / "plugin_output"
    d.mkdir(exist_ok=True)
    (d / "declaration.json").write_text(json.dumps(declaration))
    return tmp_path


# ── reading the declaration ───────────────────────────────────────────────
def test_a_declaration_that_names_its_sram_ports_is_read(tmp_path):
    p = _project(tmp_path, {"top_module": "subservient",
                            "sram_port_names": {"address": "mem_a",
                                                "write_data": "mem_dout",
                                                "read_data": "mem_din",
                                                "write_enable": "mem_wr",
                                                "cyc": "mem_en"}})
    assert T.declared_sram_ports(p) == _DECL


def test_a_declaration_that_names_none_decides_none(tmp_path):
    p = _project(tmp_path, {"top_module": "subservient",
                            "sram_interface_protocol": "sp8_a10_we_cyc"})
    assert T.declared_sram_ports(p) == {}


def test_an_absent_declaration_decides_none(tmp_path):
    assert T.declared_sram_ports(tmp_path) == {}
    assert T.declared_read_latency(tmp_path) is None


def test_an_illegal_port_name_is_not_bound(tmp_path):
    p = _project(tmp_path, {"sram_port_names": {"addr": "not a name!"}})
    assert T.declared_sram_ports(p) == {}


# ── rebinding ─────────────────────────────────────────────────────────────
def test_the_declared_names_replace_the_typical_ones():
    out, rebound = T.bind_delivered_tb(_TB, "subservient", _DECL)
    assert {r["now"] for r in rebound} == set(_DECL.values())
    assert ".mem_a(o_sram_addr)" in out
    assert ".o_sram_addr (o_sram_addr)" not in out


def test_the_testbenchs_own_nets_are_never_touched():
    out, _ = T.bind_delivered_tb(_TB, "subservient", _DECL)
    assert "wire [9:0] o_sram_addr;" in out
    assert "mem[o_sram_addr] <= o_sram_data;" in out, \
        "the SRAM model is the testbench's own and stays as delivered"


def test_a_silent_declaration_changes_nothing():
    out, rebound = T.bind_delivered_tb(_TB, "subservient", {})
    assert out == _TB and rebound == []


def test_a_role_the_declaration_omits_keeps_the_typical_name():
    out, rebound = T.bind_delivered_tb(_TB, "subservient", {"addr": "mem_a"})
    assert [r["role"] for r in rebound] == ["addr"]
    assert ".o_sram_we   (o_sram_we)" in out


def test_a_declaration_that_restates_the_typical_name_rebinds_nothing():
    out, rebound = T.bind_delivered_tb(
        _TB, "subservient", {"addr": "o_sram_addr"})
    assert out == _TB and rebound == []


def test_only_the_dut_instance_is_rewritten():
    """Another instance's identically-named port is not this design's port."""
    tb = _TB.replace("endmodule\n",
                     "  other_thing u2 (.o_sram_addr(o_sram_addr));\nendmodule\n")
    out, _ = T.bind_delivered_tb(tb, "subservient", _DECL)
    assert "other_thing u2 (.o_sram_addr(o_sram_addr));" in out


def test_an_unfindable_instance_rebinds_nothing():
    out, rebound = T.bind_delivered_tb(_TB, "some_other_top", _DECL)
    assert out == _TB and rebound == []


# ── read latency ──────────────────────────────────────────────────────────
def test_the_declared_latency_is_read(tmp_path):
    p = _project(tmp_path, {"sram_interface_timing": {
        "read_data_valid_after_request_cycles": 1}})
    assert T.declared_read_latency(p) == 1


def test_the_one_clock_the_input_describes_needs_no_retiming():
    out, refusal = T.retime_delivered_tb(_TB, 1)
    assert refusal is None and out == _TB


def test_an_undeclared_latency_needs_no_retiming():
    out, refusal = T.retime_delivered_tb(_TB, None)
    assert refusal is None and out == _TB


def test_a_latency_the_tb_cannot_express_is_refused_by_name():
    out, refusal = T.retime_delivered_tb(_TB, 2)
    assert out == _TB, "nothing is silently rewritten"
    assert refusal["reason"] == "tb_cannot_express_declared_read_latency"
    assert refusal["declared_read_latency_cycles"] == 2
    assert "sample at a latency the design does not have" in refusal["detail"]


def test_a_tb_that_declares_a_knob_is_retimed():
    tb = ("// VIBEIC_TB_READ_LATENCY_PARAM : READ_LAT\n"
          "localparam integer READ_LAT = 1;\n" + _TB)
    out, refusal = T.retime_delivered_tb(tb, 3)
    assert refusal is None
    assert "localparam integer READ_LAT = 3;" in out


def test_a_knob_that_is_named_but_absent_is_refused_by_name():
    tb = "// VIBEIC_TB_READ_LATENCY_PARAM : READ_LAT\n" + _TB
    _out, refusal = T.retime_delivered_tb(tb, 3)
    assert refusal["reason"] == "declared_latency_knob_not_found"


# ── the install path ──────────────────────────────────────────────────────
def _delivered(tmp_path):
    d = tmp_path / "input" / "sim" / "tb"
    d.mkdir(parents=True)
    (d / "blinky_hex.v").write_text(_TB)
    out = tmp_path / "tb"
    out.mkdir()
    return out


def test_the_installed_copy_is_bound_to_the_declaration(tmp_path):
    out = _delivered(tmp_path)
    _project(tmp_path, {"top_module": "subservient",
                        "sram_port_names": {"addr": "mem_a"}})
    rep: dict = {}
    got = T._emit_case_delivered_oracle(
        tmp_path, {"name": "blinky_hex", "stimulus": "blinky.hex"}, out, rep)
    assert ".mem_a(o_sram_addr)" in got.read_text()
    assert rep["delivered_oracles"][0]["bound_by"] == "declaration.json"
    assert rep["delivered_oracles"][0]["port_rebindings"][0]["was"] == \
        "o_sram_addr"


def test_a_silent_declaration_installs_the_delivered_bytes(tmp_path):
    out = _delivered(tmp_path)
    _project(tmp_path, {"top_module": "subservient",
                        "sram_interface_protocol": "sp8_a10_we_cyc"})
    rep: dict = {}
    got = T._emit_case_delivered_oracle(
        tmp_path, {"name": "blinky_hex", "stimulus": "blinky.hex"}, out, rep)
    body = got.read_text().split("\n", 1)[1]   # past the DELIVERED stamp
    assert body == _TB
    assert rep["delivered_oracles"][0]["bound_by"] == \
        "L3 typical set (declaration silent)"


def test_a_refused_latency_installs_nothing(tmp_path):
    out = _delivered(tmp_path)
    _project(tmp_path, {"top_module": "subservient",
                        "sram_interface_timing": {
                            "read_data_valid_after_request_cycles": 4}})
    rep: dict = {}
    got = T._emit_case_delivered_oracle(
        tmp_path, {"name": "blinky_hex", "stimulus": "blinky.hex"}, out, rep)
    assert got is None
    assert list(out.iterdir()) == [], "nothing installed"
    r = rep["delivered_oracle_refusals"][0]
    assert r["case"] == "blinky_hex" and r["declared_read_latency_cycles"] == 4

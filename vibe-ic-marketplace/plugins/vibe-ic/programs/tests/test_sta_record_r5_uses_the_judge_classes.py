"""sta_record R5 blocks on std-cell DRV only; bond-pad port rows are listed.

MEASURED on the routed spm DIE (lane spmdrv run2, tree main+padin+U1 fixes):
Step 32 closed with final_drv 0, and the pre-stream gate still FAILed on
`sta_record` R5 -- `sta_spef_multicorner.rpt` and `sta_mcorner_ocv.rpt` each
"max_capacitance x73": 70 bond-pad PORT rows (2 corners x 35 inputs, the pad's
own 2.9 pF PAD pin against the std-cell 0.2 pF margin) and 3 IO-cell pins
within their IO Liberty limit. R-0928-DRV-IC / root audit U1: those are not
std-cell DRV. R5 now counts the DRV standard's classes (the same classifier as
Step 32's census), lists the rest, and keeps every other row blocking.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import test_sta_corner_record_completeness as T  # noqa: E402

_IO_LIB = """library (io) {
  time_unit : "1ns";
  capacitive_load_unit (1, pf);
  default_max_capacitance : 999;
  default_max_fanout : 1;
  cell (PADIN) { pad_cell : true;
    pin (PAD) { max_transition : 1; direction : input; }
    pin (Y) { direction : output; } }
}
"""
_NETLIST = """module chip (a, n);
  input a;
  input n;
  PADIN u_pad_a (.PAD(a),
    .Y(a_core));
  BUF \\u_core/u1  (.A(a_core),
    .Z(z1));
  BUF u2 (.A(n),
    .Z(z2));
endmodule
"""


def _cap_table(rows):
    body = "".join(f"{pin:<40}{lim:>9} {val:>11} {float(lim) - float(val):>11.6f} (VIOLATED)\n\n"
                   for pin, lim, val in rows)
    return ("max capacitance\n\nPin" + " " * 40 + "Limit         Cap       Slack\n"
            + "-" * 72 + "\n" + body)


def _stage(tmp_path, rows, *, with_deck=True):
    run = T._run(tmp_path, "r5classes")
    T._declare(run)
    sta = run / "phase3/stage3/sta"
    host_pdk = tmp_path / "pdkcache" / "mypdk"
    T._write(host_pdk / "libs.ref/io/io.lib", _IO_LIB)
    T._write(run / "phase3/librelane_pdk_root.provenance.json", json.dumps(
        {"derivation": {"pdk": "mypdk", "host_path": str(host_pdk)}}))
    T._write(run / "phase3/stage3/pnr/chip_pnr.v", _NETLIST)
    if with_deck:
        T._write(sta / "sta_mcorner_ocv_setup.tcl",
                 "read_liberty /foss/pdks/vendor/v1/mypdk/libs.ref/io/io.lib\n"
                 f"read_verilog {run}/phase3/stage3/pnr/chip_pnr.v\n"
                 "link_design chip\n")
        # A sibling deck on another netlist (the real run's power deck reads the
        # synthesis netlist): only the deck on the report's own netlist counts.
        T._write(run / "phase2/stage2/synth/chip_synth.v", "module chip (a); endmodule\n")
        T._write(sta / "power_chip.tcl",
                 f"read_verilog {run}/phase2/stage2/synth/chip_synth.v\nlink_design chip\n")
    T._write(sta / "sta_spef_multicorner.rpt", T._multicorner(0.41, 0.08))
    T._write(sta / "sta_mcorner_ocv.rpt",
             "STA_BASIS_NETLIST: chip_pnr.v\n" + T._mcorner_ocv(2.68, 0.33)
             + _cap_table(rows))
    T._write(sta / "sta_spef_based.rpt", T._nominal(0.05))
    return run


_PORT = ("a", "0.200000", "2.989206")
_IO = ("u_pad_a/Y", "0.200000", "0.211182")
_CORE = ("u_core/u1/Z", "0.200000", "0.246000")
_PORT_INTO_CORE = ("n", "0.200000", "0.250000")


def test_port_to_pad_and_io_margin_rows_do_not_block(tmp_path):
    rc, res = T._judge(_stage(tmp_path, [_PORT, _IO]), tmp_path)
    assert "R5_DRV_VIOLATION" not in res["rules_violated"], res["reasons"]
    blob = " ".join(res["reasons"])
    assert "OFFCHIP_PORT_TO_PAD_NET max_capacitance x1" in blob, blob
    assert "IO_STD_CELL_MARGIN_DISCLOSURE max_capacitance x1" in blob, blob
    proc = next(a for a in res["axis_evidence"] if a["axis"] == "process")
    assert proc["drv"]["total"] == 2          # the tool's own count, unchanged


def test_a_core_row_beside_them_still_blocks(tmp_path):
    rc, res = T._judge(_stage(tmp_path, [_PORT, _IO, _CORE]), tmp_path)
    assert rc == 1 and "R5_DRV_VIOLATION" in res["rules_violated"]
    blob = " ".join(res["reasons"])
    assert "reports 1 DRV violation (of 3 tool rows" in blob, blob


def test_a_port_into_a_std_cell_still_blocks(tmp_path):
    rc, res = T._judge(_stage(tmp_path, [_PORT_INTO_CORE]), tmp_path)
    assert "R5_DRV_VIOLATION" in res["rules_violated"], res["reasons"]


def test_without_the_deck_every_row_counts_as_before(tmp_path):
    """Control: no resolvable netlist/IO Liberty -> classes UNAVAILABLE."""
    rc, res = T._judge(_stage(tmp_path, [_PORT], with_deck=False), tmp_path)
    assert rc == 1 and "R5_DRV_VIOLATION" in res["rules_violated"]

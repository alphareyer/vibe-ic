"""r41 (subservient x gf180mcuD as a DIE): the post-global-route repairs must
PROVE their legalization, like every other repair site does.

MEASURED: post_cts.def and post_hold.def check_placement CLEAN;
routed_preantenna.def carries 4 violations — two PAIRS of `clkbuf_16` clock
leaves at IDENTICAL coordinates (the master this flow's own cap had already
reported at the placeability bound: 50 sites against a 50-site free run). The
overlap is created by the `repair_design` / `repair_timing` pass that runs
after the global-route estimate, whose legalization was the bare
`catch {detailed_placement}` + NONFATAL shape — no `check_placement` proof, no
displacement escalation, no clock-buffer downsize rung. Nothing contradicted it
and the run shipped an illegal DEF to streamout, where PNR_PLACEMENT_ILLEGAL
refused the whole thing.

Both directions: the emitted deck carries the proving ladder at that site (and
the downsize rung when the PDK names a sink buffer); the bare shape is gone.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _deck(clk_buf: str = "BUF") -> str:
    return R._build_pnr_tcl_text(
        tech_lef_c="/x/tech.lef", cell_lef_c="/x/cell.lef",
        macro_lefs_tcl="", liberty_c="/x/c.lib", macro_libs_tcl="",
        netlist_c="/x/d.v", top="d", sdc_c="/x/d.sdc", dont_use_block="",
        metal_prefix="met", die_w=100, die_h=100, core_pad=10,
        core_w=90, core_h=90, site="unit", out_dir_c="/out",
        tapcell_block="", pdn_block="", util=0.3,
        spare_protection_tcl="", spare_postfix_tcl="",
        clk_buf=clk_buf, clk_buf_root=clk_buf, routing_constraint_tcl="",
        pg_cleanup_block="", spef_repair_block="",
        antenna_repair_block="", filler_block="")


def _gr_window(tcl: str) -> str:
    """The deck between the global-route repairs and detailed_route."""
    start = tcl.index("PNR_STAGE: global_route")
    end = tcl.index("PNR_STAGE: detailed_route")
    assert start < end
    return tcl[start:end]


def test_the_site_proves_its_placement_and_can_escalate():
    win = _gr_window(_deck())
    assert "GR_REPAIR_LEGALIZE_OK" in win
    # the proof, the escalation and the last-resort rung the other sites have
    assert "check_placement" in win
    assert "-max_displacement" in win
    assert "GR_REPAIR_CLKBUF_DOWNSIZE" in win


def test_the_bare_catch_shape_is_gone():
    win = _gr_window(_deck())
    assert "GR_REPAIR_LEGALIZE_NONFATAL: $_gr_dp_err | diamond:" not in win
    # a legalization that only ever says OK-or-nothing cannot refuse
    assert "GR_REPAIR_LEGALIZE_FAILED" in win


def test_without_a_sink_buffer_there_is_no_downsize_rung():
    win = _gr_window(_deck(clk_buf=""))
    assert "GR_REPAIR_LEGALIZE_OK" in win and "check_placement" in win
    assert "CLKBUF_DOWNSIZE" not in win


def test_every_repair_site_uses_the_same_builder():
    tcl = _deck()
    for marker in ("INITIAL_DPL", "POST_HOLD", "GR_REPAIR"):
        assert f"{marker}_LEGALIZE_OK" in tcl, marker

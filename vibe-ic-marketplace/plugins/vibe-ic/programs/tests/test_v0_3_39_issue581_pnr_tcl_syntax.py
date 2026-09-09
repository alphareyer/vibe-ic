"""ORGANIC #581 — the #557 post-route SPEF repair block emitted into
pnr.tcl was not valid Tcl: a plain-string fragment kept an
f-string-escaped `}}` (→ `]}}`) and a multi-line `catch {...}` was nested
inside the bracketed `if {[catch {` expression. OpenROAD died with
`missing close-bracket in expression "[catch { catch {def..."` AFTER
detailed route fully converged, so GDS / final DEF were never written —
on EVERY phase3 run (the block is emitted unconditionally).

Fixes:
(a) the SPEF repair block is restructured as strictly sequential
    one-line `if {[catch {...} e]} {...}` statements;
(b) the COMPLETE pnr.tcl template is extracted into the pure builder
    `_build_pnr_tcl_text` (v0.1.49 doctrine) and validated here by an
    ACTUAL tclsh parse/eval — string-content assertions alone proved
    insufficient (the broken block passed a content-only field audit).

The tclsh harness (`_pnr_tcl_stub.STUB`) makes every OpenROAD command a
no-op while the Tcl PARSER still sees the real structure. The four
primitives whose RETURN VALUE the deck reads -- `utl::redirectString*`,
`utl::report`, `report_dont_use` and `[ord::get_db] findMaster` -- are
modelled with real semantics, because a no-op is not a stand-in for a
command a block branches on: with `unknown` alone the v1.19.95 CTS
legality block's own "the capture did not work" guard fired and the deck
aborted 316 lines into 1,717, so 82% of it stopped being parsed at all.
"""
import shutil
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pnr_tcl_stub import STUB as _STUB  # noqa: E402
from _pnr_tcl_stub import (  # noqa: E402
    STUB_MASTERS_ABSENT as _STUB_MASTERS_ABSENT)


def _run_tclsh(script_path: Path):
    return _pr.run([tclsh, str(script_path)],
                          capture_output=True, text=True)


def _stage_captable(tmp_path: Path) -> str:
    """Create a fake PDK tree whose tech-LEF path makes the captable
    discovery glob succeed → the SPEF block takes the (formerly broken)
    captable path."""
    (tmp_path / "pdk" / "libs.ref" / "fix").mkdir(parents=True)
    (tmp_path / "pdk" / "libs.tech" / "openlane").mkdir(parents=True)
    (tmp_path / "pdk" / "libs.tech" / "openlane" /
     "rules.openrcx.fix.nom.magic").write_text("# captable fixture\n")
    return str(tmp_path / "pdk" / "libs.ref" / "fix" / "tech.lef")


def _pdk() -> "R.PdkConfig":
    return R.PdkConfig(
        name="fixture_pdk",
        liberty="/pdk/lib.lib", tech_lef="/pdk/tech.lef",
        cell_lef="/pdk/cells.lef", cell_gds=None,
        site="unithd", drc_deck=None, metal_prefix="met",
        tapcell_master="sky130_fd_sc_hd__tapvpwrvgnd_1",
        antenna_diode_cell="sky130_fd_sc_hd__diode_2",
        pnr_exclude_cell_file="/pdk/drc_exclude.cells",
    )


def _full_pnr_tcl(tmp_path: Path) -> str:
    """Compose the COMPLETE pnr.tcl exactly as step_pnr does — every
    sub-block from its REAL builder (a hand-written stand-in could be
    balanced while the real builder emits something unbalanced)."""
    pdk = _pdk()
    out_dir_c = str(tmp_path / "out")
    (tmp_path / "out").mkdir(exist_ok=True)
    tech_lef_c = _stage_captable(tmp_path)
    plan = R._build_spare_cells_plan(
        2000, 0.02, (10, 10, 290, 290),
        liberty_path="", container="")
    return R._build_pnr_tcl_text(
        tech_lef_c=tech_lef_c, cell_lef_c="/pdk/cells.lef",
        macro_lefs_tcl="", liberty_c="/pdk/lib.lib",
        macro_libs_tcl="", netlist_c="/work/netlist.v", top="chip_top",
        sdc_c="/work/chip_top.sdc",
        dont_use_block=R._dont_use_tcl(pdk),
        metal_prefix=pdk.metal_prefix, die_w=300, die_h=300,
        core_pad=10, core_w=280, core_h=280, site=pdk.site,
        out_dir_c=out_dir_c,
        tapcell_block=R._build_tapcell_tcl(pdk),
        pdn_block=R._build_pdn_tcl(pdk),
        util=0.45,
        spare_protection_tcl=R._build_spare_protection_tcl(plan, out_dir_c),
        spare_postfix_tcl=R._build_spare_postfix_tcl(
            plan, tie_lo_cell="sky130_fd_sc_hd__conb_1", tie_lo_pin="LO"),
        clk_buf="sky130_fd_sc_hd__clkbuf_4",
        clk_buf_root="sky130_fd_sc_hd__clkbuf_16",
        routing_constraint_tcl="",
        pg_cleanup_block=R._pg_net_cleanup_tcl(),
        spef_repair_block=R._post_route_spef_repair_tcl(
            out_dir_c, tech_lef_c),
        antenna_repair_block=R._antenna_repair_tcl(pdk),
        filler_block="",
    )


# ── the issue's exact 現象: full pnr.tcl must survive a real tclsh ──────────

@needs_tclsh
def test_full_pnr_tcl_parses_and_evaluates_in_tclsh(tmp_path):
    """The named defect end-state: OpenROAD (a Tcl interpreter) must reach
    the END of pnr.tcl. tclsh with all tool commands stubbed exercises the
    identical parser; the broken v0.3.38 block dies here with `missing
    close-bracket`."""
    script = tmp_path / "pnr.tcl"
    full = _full_pnr_tcl(tmp_path)
    # `exit` would mask later parse errors — replace with a sentinel puts.
    full = full.replace("\nexit\n", "\nputs PNR_TCL_END\n")
    script.write_text(_STUB + full)
    result = _run_tclsh(script)
    assert result.returncode == 0, result.stderr
    assert "missing close-bracket" not in result.stderr
    assert "PNR_TCL_END" in result.stdout


@needs_tclsh
def test_spef_repair_block_captable_path_reaches_complete(tmp_path):
    """The captable branch (the formerly broken one) must evaluate through
    to SPEF_MEASURE_COMPLETE (#581 r3: measure-only, no repair)."""
    tech_lef_c = _stage_captable(tmp_path)
    block = R._post_route_spef_repair_tcl(str(tmp_path / "out"), tech_lef_c)
    script = tmp_path / "block.tcl"
    script.write_text(_STUB + block)
    result = _run_tclsh(script)
    assert result.returncode == 0, result.stderr
    assert "SPEF_MEASURE_COMPLETE" in result.stdout


@needs_tclsh
def test_spef_repair_block_skip_path_still_works(tmp_path):
    block = R._post_route_spef_repair_tcl(
        str(tmp_path / "out"), "/nonexistent/libs.ref/x/tech.lef")
    script = tmp_path / "block.tcl"
    script.write_text(_STUB + block)
    result = _run_tclsh(script)
    assert result.returncode == 0, result.stderr
    assert "SPEF_REPAIR_SKIP" in result.stdout


@needs_tclsh
def test_harness_catches_the_v0_3_38_broken_shape(tmp_path):
    """NEGATIVE pin: the v0.3.38 broken emission (extra `}` after the
    write_spef bracket, verbatim from the field repro) MUST fail this
    harness — proving the tclsh eval actually catches the bug class that
    content-only assertions missed."""
    broken = (
        "set _prs_rules captable\n"
        "if {$_prs_rules ne \"\"} {\n"
        "  if {[catch {\n"
        "    catch {define_process_corner -ext_model_index 0 X}\n"
        "    extract_parasitics -ext_model_file $_prs_rules\n"
        "    if {[catch {write_spef /out/x.spef} _w]}} "
        "{ puts \"W: $_w\" }\n"
        "    puts \"SPEF_REPAIR_COMPLETE\"\n"
        "  } _e]} {\n"
        "    puts \"SPEF_REPAIR_NONFATAL: $_e\"\n"
        "  }\n"
        "} else {\n"
        "  puts \"SKIP\"\n"
        "}\n"
    )
    script = tmp_path / "broken.tcl"
    script.write_text(_STUB + broken)
    result = _run_tclsh(script)
    assert result.returncode != 0
    assert "close-bracket" in (result.stderr + result.stdout)


# ── ORGANIC #581 round-3 — measure-only: NO repair after detailed route ─────

def test_spef_repair_block_is_measure_only():
    """The round-3 reopen's 現象: `repair_timing -setup` (not just
    repair_design) ALSO segfaults (Signal 11) on a post-detailed-route
    SPEF-annotated design — the whole RSZ repair-move family crashes
    there. `catch` cannot contain a segfault (it kills the openroad
    process → GDS never written), so the post-route block must call NO
    repair and do NO reroute: extract_parasitics → write_spef only."""
    tcl = R._post_route_spef_repair_tcl("/out", "/p/libs.ref/x/tech.lef")
    # scan COMMAND lines only (the doctrine comment may name the banned
    # commands while explaining why they are gone).
    cmds = [ln for ln in tcl.splitlines() if not ln.lstrip().startswith("#")]
    cmd_txt = "\n".join(cmds)
    assert "repair_design" not in cmd_txt
    assert "repair_timing" not in cmd_txt
    assert "detailed_route" not in cmd_txt
    assert "global_route" not in cmd_txt
    # the sign-off SPEF extraction is still there (feeds #527 STA)
    assert "extract_parasitics" in cmd_txt
    assert "write_spef" in cmd_txt
    assert "SPEF_MEASURE_COMPLETE" in cmd_txt


def test_repair_builder_keeps_pre_route_repair():
    """The #561 repair builder runs from post_hold.def (a PRE-route state),
    so ITS repair_timing is safe and must be preserved — only the
    POST-route SPEF block drops repair (#581 r3)."""
    repair = R._build_postroute_timing_repair_tcl("top", "/t.lef", "/c.lef", "/l.lib",
                                  "/pnr", "/repair", "met")
    assert "post_hold.def" in repair           # pre-route start point
    assert "repair_timing" in repair           # safe pre-route repair kept
    assert repair.index("repair_design") < repair.index("pass 2")


def test_spef_repair_block_no_multiline_catch_in_bracket_expr():
    """Structural pin (tclsh-independent): the block must not contain the
    `if {[catch {\\n` multi-line-catch-inside-bracketed-expression shape —
    every catch BODY opened inside a bracketed `if {[catch {` expression
    must close before the `]` on the SAME line."""
    block = R._post_route_spef_repair_tcl("/out", "/p/libs.ref/x/tech.lef")
    assert "if {[catch {\n" not in block
    for line in block.splitlines():
        idx = line.find("if {[catch {")
        if idx < 0:
            continue
        bracket_close = line.find("]", idx)
        assert bracket_close > 0, f"catch bracket spans lines: {line!r}"
        # within `[catch { ... } var]` the braces must balance before `]`
        inner = line[idx + len("if {[") : bracket_close]
        assert inner.count("{") == inner.count("}"), line


# ── the harness is a STAND-IN, not a bypass: prove it both ways ────────────

@needs_tclsh
def test_the_deck_takes_the_real_cts_legality_path_under_the_stub(tmp_path):
    """A stub that merely stops the deck dying would be worthless: it has to
    carry the block down its REAL branch. Under `STUB` the policy read must
    succeed, every requested master must survive, and the block must announce
    the selection it hands `clock_tree_synthesis` -- not a fallback, not an
    abort.

    Without this, a future edit could quieten the block instead of modelling
    it and every deck test would stay green over a deck that never ran its
    own CTS legality filter.
    """
    script = tmp_path / "pnr.tcl"
    full = _full_pnr_tcl(tmp_path)
    full = full.replace("\nexit\n", "\nputs PNR_TCL_END\n")
    script.write_text(_STUB + full)
    result = _run_tclsh(script)
    assert result.returncode == 0, result.stderr
    assert "CTS_LEGAL_SELECTION: buffers=sky130_fd_sc_hd__clkbuf_4 " \
           "root=sky130_fd_sc_hd__clkbuf_16" in result.stdout, result.stdout
    # the degraded outcomes must NOT be what made the deck survive
    assert "CTS_CELL_EXCLUDED" not in result.stdout
    assert "CTS_ROOT_RESELECTED" not in result.stdout


@needs_tclsh
def test_the_stub_still_lets_the_block_refuse_an_empty_legal_pool(tmp_path):
    """NEGATIVE control on the harness itself. `STUB_MASTERS_ABSENT` is the
    same interpreter with `findMaster` answering NULL; the block's refusal
    (`CTS_LEGAL_BUFFER_POOL_EMPTY`, the contract
    `test_i2172_cts_buf_list_from_pdk_family` pins) must still reach the
    deck. A stand-in that could no longer express the refusal would be
    hiding the guard rather than representing it.
    """
    script = tmp_path / "pnr.tcl"
    full = _full_pnr_tcl(tmp_path)
    full = full.replace("\nexit\n", "\nputs PNR_TCL_END\n")
    script.write_text(_STUB_MASTERS_ABSENT + full)
    result = _run_tclsh(script)
    assert result.returncode != 0
    assert "CTS_LEGAL_BUFFER_POOL_EMPTY" in (result.stderr + result.stdout)
    assert "PNR_TCL_END" not in result.stdout

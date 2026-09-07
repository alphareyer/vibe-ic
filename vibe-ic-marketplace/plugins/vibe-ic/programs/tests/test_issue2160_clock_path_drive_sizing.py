#!/usr/bin/env python3
"""The clock path between the clock port and the CTS root belongs to nobody.

vibe-ic#2160, MEASURED on subservient x gf180mcuD (lane cz2160, 8HD-9, canonical
image sha256:1463dac58116..., plugin v1.19.14):

The DFT step inserts a test-clock multiplexer into the clock path as behavioural
Verilog (``assign __clk_source__ = test ? tck : i_clk``). Synthesis maps it to
the SMALLEST drive in its family -- nothing constrains a net that carries no data
path. Then no stage sizes it:

  * TritonCTS SKIPS the port net (``CTS-0041 Net "i_clk" has 1 sinks.
    Skipping...``) and starts its H-tree at the mux OUTPUT, so the mux is
    upstream of every buffer CTS builds.
  * The OpenROAD Resizer excludes clock-network cells from ``repair_design`` /
    ``repair_timing`` by construction. Proof from the same run's netlist:
    ``_0910_``, an ordinary DATA mux emitted by the same DFT step one line
    later, was sized to ``mux2_4`` by the resizer; ``_0909_``, the identical
    master on the CLOCK net, stayed at ``mux2_1``.

That one cell costs 2.00 ns of delay and a 2.44 ns output slew at the SS corner
-- 30 % of the design's whole 6.59 ns clock insertion delay -- and a clock
insertion delay is charged IN FULL against every register -> OUTPUT-PORT path,
where it does not cancel. All three of that run's SS setup violations are
register -> output-port paths, and the sign-off read
``setup -0.260 ns, TNS -1.14``.

Sizing that one cell up by ONE drive step took the SS-corner post-route setup
slack from -0.2155 ns to +0.2486 ns and the setup TNS from -0.3307 to 0.00, with
hold improving from +0.145 to +1.094 -- measured twice by independent methods (an
out-of-session netlist edit and an in-session ODB ``swapMaster``) agreeing to 16
significant figures.

WHAT THESE TESTS PIN, and why each one exists:

  * The pass selects by the TIMER's own clock network and by a pre-CTS instance
    SNAPSHOT -- never by a name pattern. A ``*clkbuf*`` match would be a guess
    about a PDK's naming AND would miss the cell that actually costs the time,
    which is not a buffer at all.
  * It keeps the sibling it MEASURED to be best, not the largest one. On the
    real design ``mux2_2`` beats ``mux2_4``; a "size to max" rule would have
    picked the worse of the two and still called it a fix.
  * When nothing improves it puts the ORIGINAL master back, so a design with no
    unowned clock-path cell -- or one where sizing does not help -- runs exactly
    as it did before.
  * A swap that turns a non-negative hold slack negative is refused.

BIDIRECTIONAL CONTROL: every behavioural test below drives the emitted TCL under
``tclsh``. Against the pre-fix tree the module has no
``_clock_path_drive_sizing_tcl`` at all, so collection fails outright -- these
tests cannot be green on the code they were written against. The template-order
test additionally fails if the block is emitted anywhere but between
``clock_tree_synthesis`` and the ``post_cts.def`` write.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess as _pr
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as P                            # noqa: E402

_TCLSH = shutil.which("tclsh")
_needs_tcl = pytest.mark.skipif(_TCLSH is None, reason="tclsh not available")


# --------------------------------------------------------------------------
# A stub ODB/OpenSTA world. Deliberately uses cell names from NO real PDK, so a
# fix that pattern-matched a vendor's naming would fail here.
# --------------------------------------------------------------------------
def _stub(*, driver_in_snapshot: bool = True,
          slack_max: str = "u_mux_1 -0.20 u_mux_2 0.25 u_mux_4 0.22",
          slack_min: str = "u_mux_1 0.10 u_mux_2 0.10 u_mux_4 0.10",
          liberty: str = "u_mux_1 u_mux_2 u_mux_4") -> str:
    """The world the emitted block runs in.

    ``slack_max`` / ``slack_min`` map the CURRENT master of the clock-path
    instance to the slack the timer reports, so a swap changes the measurement
    the way a real one does. ``liberty`` is the set of masters the timer has a
    model for -- a master present in the LEF but absent from the linked liberty
    must be skipped, not swapped to.
    """
    snap = ("dict set ::snapshot u_clkmux 1\n"
            if driver_in_snapshot else "# driver NOT in the pre-CTS snapshot\n")
    return f"""
namespace eval ord {{}}
namespace eval sta {{}}
set ::prop 0
# ---- masters (LEF) -------------------------------------------------------
set ::masters {{u_mux_1 u_mux_2 u_mux_4 u_and_1 u_clkbuf_1}}
set ::liberty {{{liberty}}}
array set ::smax {{{slack_max}}}
array set ::smin {{{slack_min}}}
set ::im(u_clkmux)  u_mux_1
set ::im(u_ctsbuf)  u_clkbuf_1
set ::swaps {{}}
set ::snapshot [dict create]
{snap}dict set ::snapshot u_someother 1
set ::_vic_prects_insts $::snapshot

proc ord::get_db {{}} {{ return DB }}
proc ord::get_db_block {{}} {{ return BLK }}
proc DB {{sub args}} {{
  if {{$sub eq "getLibs"}} {{ return LIB }}
  if {{$sub eq "findMaster"}} {{
    set n [lindex $args 0]
    if {{[lsearch -exact $::masters $n] >= 0}} {{ return $n }}
    return "NULL"
  }}
}}
proc LIB {{sub args}} {{ if {{$sub eq "getMasters"}} {{ return $::masters }} }}
foreach _m {{u_mux_1 u_mux_2 u_mux_4 u_and_1 u_clkbuf_1}} {{
  proc $_m {{sub args}} [format {{ if {{$sub eq "getName"}} {{ return %s }} }} $_m]
}}
proc BLK {{sub args}} {{
  if {{$sub eq "findInst"}} {{
    set n [lindex $args 0]
    if {{[info exists ::im($n)]}} {{ return $n }}
    return "NULL"
  }}
}}
foreach _i {{u_clkmux u_ctsbuf}} {{
  proc $_i {{sub args}} [format {{
    if {{$sub eq "getName"}}    {{ return %s }}
    if {{$sub eq "getMaster"}}  {{ return $::im(%s) }}
    if {{$sub eq "swapMaster"}} {{ set ::im(%s) [lindex $args 0]
                                  lappend ::swaps "%s->[lindex $args 0]" ; return }}
  }} $_i $_i $_i $_i]
}}
# the clock network, as the timer defines it: one net, driven by u_clkmux,
# plus a CTS-built net driven by u_ctsbuf.
proc all_clocks {{}} {{ return CLK }}
proc CLK {{sub args}} {{ return }}
proc sta::find_clk_nets {{clk}} {{ return {{NET_SRC NET_CTS}} }}
proc NET_SRC {{sub args}} {{ if {{$sub eq "getITerms"}} {{ return {{IT_DRV IT_SNK}} }} }}
proc NET_CTS {{sub args}} {{ if {{$sub eq "getITerms"}} {{ return {{IT_CTS}} }} }}
proc IT_DRV {{sub args}} {{
  if {{$sub eq "getMTerm"}} {{ return MT_OUT }}
  if {{$sub eq "getInst"}}  {{ return u_clkmux }} }}
proc IT_SNK {{sub args}} {{
  if {{$sub eq "getMTerm"}} {{ return MT_IN }}
  if {{$sub eq "getInst"}}  {{ return u_ctsbuf }} }}
proc IT_CTS {{sub args}} {{
  if {{$sub eq "getMTerm"}} {{ return MT_OUT }}
  if {{$sub eq "getInst"}}  {{ return u_ctsbuf }} }}
proc MT_OUT {{sub args}} {{ if {{$sub eq "getIoType"}} {{ return OUTPUT }} }}
proc MT_IN  {{sub args}} {{ if {{$sub eq "getIoType"}} {{ return INPUT }} }}

proc sta::worst_slack {{which}} {{
  if {{$which eq "-max"}} {{ return $::smax($::im(u_clkmux)) }}
  return $::smin($::im(u_clkmux))
}}
proc sta::total_negative_slack {{which}} {{
  set s $::smax($::im(u_clkmux))
  if {{$s < 0}} {{ return $s }}
  return 0.0
}}
proc set_propagated_clock {{args}} {{ set ::prop 1 }}
proc unset_propagated_clock {{args}} {{ set ::prop 0 }}
proc get_lib_cells {{n}} {{
  if {{[lsearch -exact $::liberty $n] < 0}} {{ error "no liberty cell $n" }}
  return $n
}}
proc detailed_placement {{args}} {{ return }}
proc check_placement {{}} {{ return }}
"""


def _run(tcl_body: str, tmp_path, **kw) -> str:
    f = tmp_path / "clkpath.tcl"
    f.write_text(_stub(**kw) + tcl_body
                 + '\nputs "SWAPS: $::swaps"\n'
                 + 'puts "FINAL_MASTER: $::im(u_clkmux)"\n'
                 + 'puts "PROP_LEFT_ON: $::prop"\n')
    r = _pr.run([_TCLSH, str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    return r.stdout


# --------------------------------------------------------------------------
# 1. The candidate rule
# --------------------------------------------------------------------------
@_needs_tcl
def test_the_unowned_clock_path_driver_is_the_candidate(tmp_path):
    """The driver present in the pre-CTS snapshot is a candidate; the buffer CTS
    created on the very same clock network is not."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path)
    assert "CLKPATH_SIZE_CANDIDATES: 1 (u_clkmux)" in out, out
    assert "u_ctsbuf" not in out.split("CLKPATH_SIZE_CANDIDATES")[1].split("\n")[0]


@_needs_tcl
def test_negative_control_every_clock_driver_is_cts_built(tmp_path):
    """NEGATIVE CONTROL. Same clock network, same masters, same slacks -- only
    the pre-CTS snapshot differs. With no unowned driver the pass finds nothing,
    swaps nothing and leaves the master alone. This is what makes the swap in
    the test above attributable to the snapshot rule rather than to the stub."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path,
               driver_in_snapshot=False)
    assert "CLKPATH_SIZE_CANDIDATES: 0 ()" in out, out
    assert "CLKPATH_SIZE_KEPT" not in out
    assert "SWAPS: \n" in out or "SWAPS: " == out.splitlines()[-3].rstrip()
    assert "FINAL_MASTER: u_mux_1" in out


@_needs_tcl
def test_selection_is_not_a_name_pattern():
    """The emitted TCL must not decide by cell name. A ``*clkbuf*``-style match
    would be a guess about a PDK's naming AND would miss the multiplexer that
    actually costs the time."""
    tcl = P._clock_path_drive_sizing_tcl()
    assert "clkbuf" not in tcl.lower()
    assert "string match" not in tcl
    assert "_vic_prects_insts" in tcl
    assert "sta::find_clk_nets" in tcl


def test_emitted_tcl_carries_no_design_pdk_or_vendor_literal():
    tcl = (P._clock_path_pre_cts_snapshot_tcl()
           + P._clock_path_drive_sizing_tcl()).lower()
    for tok in ("subservient", "gf180", "sky130", "ihp", "sg13", "nangate",
                "mux2", "clkbuf", "dffq"):
        assert tok not in tcl, tok


# --------------------------------------------------------------------------
# 2. The decision
# --------------------------------------------------------------------------
@_needs_tcl
def test_keeps_the_measured_best_sibling_not_the_largest(tmp_path):
    """THE discriminating case, and the real design's own numbers in miniature:
    the +1 drive step beats the +4 one. A "size to the strongest master" rule
    passes every other test here and fails this one."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path)
    assert "CLKPATH_SIZE_TRY u_clkmux u_mux_2 wns_max=0.25" in out, out
    assert "CLKPATH_SIZE_TRY u_clkmux u_mux_4 wns_max=0.22" in out, out
    assert "CLKPATH_SIZE_KEPT u_clkmux u_mux_1 -> u_mux_2" in out, out
    assert "FINAL_MASTER: u_mux_2" in out


@_needs_tcl
def test_restores_the_original_master_when_nothing_improves(tmp_path):
    """MUTATION DIRECTION. Every sibling is tried and none is better, so the
    instance must end on the master it started with -- a pass that left the last
    thing it tried in place would ship a design it measured to be WORSE."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path,
               slack_max="u_mux_1 0.50 u_mux_2 0.10 u_mux_4 -0.30")
    assert "CLKPATH_SIZE_UNCHANGED u_clkmux u_mux_1" in out, out
    assert "CLKPATH_SIZE_KEPT" not in out
    assert "FINAL_MASTER: u_mux_1" in out


@_needs_tcl
def test_refuses_a_swap_that_makes_a_met_hold_negative(tmp_path):
    """Setup is not bought with hold. ``u_mux_2`` has the best setup slack of the
    three and is refused because it drives hold negative; the pass falls to
    ``u_mux_4``, which improves setup and leaves hold met."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path,
               slack_max="u_mux_1 -0.20 u_mux_2 0.40 u_mux_4 0.22",
               slack_min="u_mux_1 0.10 u_mux_2 -0.05 u_mux_4 0.08")
    assert "CLKPATH_SIZE_REJECT_HOLD u_mux_2" in out, out
    assert "CLKPATH_SIZE_KEPT u_clkmux u_mux_1 -> u_mux_4" in out, out
    assert "FINAL_MASTER: u_mux_4" in out


@_needs_tcl
def test_a_master_absent_from_the_linked_liberty_is_never_swapped_to(tmp_path):
    """A master can be placeable (in the LEF) and untimable (absent from the
    liberty this run linked). Swapping to one would hand the timer a cell it has
    no model for -- so it is skipped by name, and the decision falls to the
    sibling that IS modelled."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path,
               liberty="u_mux_1 u_mux_4")
    assert "CLKPATH_SIZE_SKIP_SIBLING u_mux_2 not_in_linked_liberty" in out, out
    assert "CLKPATH_SIZE_KEPT u_clkmux u_mux_1 -> u_mux_4" in out, out


@_needs_tcl
def test_the_clock_is_left_exactly_as_the_pass_found_it(tmp_path):
    """The pass propagates the clock to MEASURE and unpropagates afterwards, so
    every later stage of the same OpenROAD session sees the clock state it saw
    before. Without this the change would silently alter what
    ``repair_timing -hold`` and the post-global-route repairs are optimising."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path)
    assert "CLKPATH_SIZE_PROPAGATED: 1" in out, out
    assert "PROP_LEFT_ON: 0" in out, out


@_needs_tcl
def test_a_master_with_no_drive_suffix_is_reported_not_guessed(tmp_path):
    """A master whose name carries no numeric drive suffix has no family this
    rule can read. That is a SKIP with the reason printed -- never a guess."""
    out = _run(P._clock_path_drive_sizing_tcl(), tmp_path)
    assert "no_drive_suffix" not in out          # the stub's master has one
    out2 = _run(P._clock_path_drive_sizing_tcl(), tmp_path,
                slack_max="u_mux_1 -0.20 u_mux_2 0.25 u_mux_4 0.22")
    assert "CLKPATH_SIZE_FAMILY u_clkmux u_mux_1 siblings=2" in out2, out2


# --------------------------------------------------------------------------
# 3. Where it is emitted
# --------------------------------------------------------------------------
_KW = dict(
    tech_lef_c="/t.lef", cell_lef_c="/c.lef", macro_lefs_tcl="",
    liberty_c="/l.lib", macro_libs_tcl="", netlist_c="/n.v", top="foo",
    sdc_c="/s.sdc", dont_use_block="", metal_prefix="met",
    die_w=100, die_h=100, core_pad=10, core_w=80, core_h=80, site="unit",
    out_dir_c="/out", tapcell_block="", pdn_block="", util=0.45,
    spare_protection_tcl="", spare_postfix_tcl="", clk_buf="", clk_buf_root="",
    routing_constraint_tcl="", pg_cleanup_block="", spef_repair_block="",
    antenna_repair_block="", filler_block="", spef_repair_estimate_block="",
)


def test_snapshot_precedes_cts_and_sizing_follows_it_before_post_cts_def():
    """Order is the whole property. The snapshot has to be taken BEFORE CTS or
    it cannot tell a CTS buffer from anything else; the sizing has to run AFTER
    CTS (the tree must exist to be measured) and BEFORE ``post_cts.def`` is
    written, or the DEF every later stage reads carries the unsized cell."""
    tcl = P._build_pnr_tcl_text(**_KW)
    i_snap = tcl.index("CLKPATH_SIZE_SNAPSHOT:")
    i_cts = tcl.index("clock_tree_synthesis -buf_list")
    i_size = tcl.index("CLKPATH_SIZE_CANDIDATES:")
    i_def = tcl.index("/out/post_cts.def")
    assert i_snap < i_cts < i_size < i_def, (i_snap, i_cts, i_size, i_def)


def test_the_pass_is_emitted_for_every_design_not_gated_on_a_pdk():
    """Program-first: this is a decision the RUNNER makes for every design of
    this class. It takes no design or PDK argument, so there is no configuration
    under which a design silently does not get it."""
    tcl = P._build_pnr_tcl_text(**_KW)
    assert "CLKPATH_SIZE_CANDIDATES:" in tcl
    assert P._clock_path_drive_sizing_tcl() in tcl
    assert P._clock_path_pre_cts_snapshot_tcl() in tcl

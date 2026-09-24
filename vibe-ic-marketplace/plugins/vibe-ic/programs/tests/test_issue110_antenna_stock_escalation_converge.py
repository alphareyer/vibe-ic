"""ORGANIC #110 — antenna-repair residual on the DEPLOYED STOCK OpenROAD.

Community issue #110: `check_antennas` on the committed sha256 route left a
real, reproducible residual (3 net / 4 pin, met2 side-area) on a 26k-instance
DRC-clean design. Root cause (proven live, 2026-07-11): the pre-escalation
antenna pass ran ONE `repair_antennas` + `detailed_route` turn, so the realizing
detailed route re-introduced a met2 side-area antenna the coarse global-route
repair did not foresee — a global-vs-detailed routing divergence the single pass
cannot chase.

The fix (`_antenna_repair_tcl`, v1.3.47, shipped/committed) is the INCREMENTAL
repair->reroute->recheck OUTER loop with an ESCALATING `-ratio_margin`. Its
PRIMARY path is the fork's `repair_antennas ... -reroute`; on the **deployed
stock binary** (`vibeic-eda:0.2.5`, which has NO `-reroute` — verified live:
`[ERROR STA-0562] repair_antennas -reroute is not a known keyword or flag`) the
`catch` DEGRADES to the external `repair_antennas -ratio_margin` +
`detailed_route` pass, once per outer turn, escalating the margin each turn.

Live proof on the DEPLOYED STOCK binary against the exact committed sha256
recipe (faithful re-route, base route == committed 0-DRC checkpoint):
    precheck  21 net / 22 pin
    iter 0 (margin 0)  -> +22 diodes -> 3 net / 4 pin   (== the #110 residual)
    iter 1 (margin 10) -> +7  diodes -> 0 net / 0 pin
    iter 2 (margin 20) -> check == 0 -> CONVERGED
Fresh-process re-read of the repaired DEF -> Found 0 net / 0 pin (geometric +
persistent, not an in-memory illusion); 29 diode_2 instances inserted
(base 0 -> 29); KLayout sign-off DRC on the repaired GDS == 0 items (identical
to the pre-antenna base GDS -> the diodes/reroute add ZERO DRC).

These tests pin the emitted-TCL control logic on SYNTHETIC before/after antenna
reports, driving the STOCK path (no `-reroute`) so a blind run auto-covers the
#110 convergence + the clean-design no-op. chip/PDK-AGNOSTIC.

R-0915-116(2)(iii), 2026-09-21 — THE STOCK PATH IS GONE, AND THIS FILE SAYS SO
RATHER THAN PRETENDING OTHERWISE. The degraded branch's whole-design
`detailed_route` is deleted from the antenna stage. It was measured destroying
finished work on two ICs: on spm x gf180mcuD (lane icspm5, run12 on v1.22.60)
the base route had CONVERGED and GRT-0012 read "Found 0 antenna violations"
when the fallback ran anyway and broke it (DRT-0206); on subservient x
gf180mcuD (int7) it hit DRT-1231 and cost the run its routed.def, DRC, LVS and
post-route STA. A NO-OP full `detailed_route` of int7's own pre-diode database
produced 57178 changed net lines and DRT-0206 with 1212 checkConnectivity
breaks, none on a supply net: this router cannot re-lay an already-routed
design at all, so the fallback was never a repair -- it was a coin flip that
risked the whole route.

WHAT THAT COSTS, NAMED: on a binary WITHOUT `-reroute` -- the deployed stock
0.2.5 this file was written against -- the native call now inserts nothing,
the pass is UNJUDGED, and the loop STOPS with the violation reported. #110's
21 -> 3 -> 0 trajectory is no longer reachable there. The shipped image is the
fork (0.3.67, pinned by digest) and DOES support `-reroute`, so the shipped
flow keeps its convergence through the native incremental path; only a stock
binary loses it, and it loses it to an honest FAIL rather than to a false 0
bought by unwiring the design. The convergence tests below are re-aimed at
that contract under names that say what they now pin.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402


def _invokes_cmd(text, cmd):
    """Is `cmd` INVOKED -- the first word of a statement -- in this deck?

    NOT a substring search. R-0915-122's capability probe passes
    `detailed_route` to `info body` to ask whether this build accepts
    `-nets`; it never calls it, and a substring scan reads that question as
    the answer. Same instrument, and the same reason, as `_invokes` in
    test_r0915_74_a_refused_antenna_repair_rolls_back.py, whose own docstring
    says "a bare `in` reads the deck's own citation of the defect as the
    defect".
    """
    for ch in "[]{}":
        text = text.replace(ch, " \n")
    for stmt in text.replace(";", "\n").splitlines():
        head = stmt.strip().split()
        if head and head[0] == cmd:
            return True
    return False


tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")


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


# A Tcl harness that EMULATES the deployed STOCK OpenROAD: `repair_antennas`
# with `-reroute` ERRORS (STA-0562), and each successful (no-`-reroute`) repair
# pass drops the check_antennas count along a caller-supplied sequence. This is
# the #110 environment (deployed vibeic-eda:0.2.5 has no `-reroute`).
_STOCK_HARNESS = """
set ::seq {%s}
set ::i 0
set ::diodes 0
proc check_antennas {args} {
  set v [lindex $::seq $::i]
  if {$::i < [expr {[llength $::seq]-1}]} { incr ::i }
  return $v
}
proc repair_antennas {args} {
  # stock: the `-reroute` flag is unknown -> hard error (STA-0562 analogue).
  if {[lsearch $args -reroute] >= 0} { error "STA-0562 unknown flag -reroute" }
  # a real (diode-inserting) repair pass; record that diodes were added.
  incr ::diodes
  return ""
}
proc detailed_route {args} { return "" }
"""


def _run(script_text: str):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script_text)
        return _pr.run([tclsh, str(p)],
                              capture_output=True, text=True)


@needs_tclsh
def test_stock_path_reports_the_110_residual_instead_of_routing_for_it():
    """WAS `test_stock_path_converges_the_110_residual`, which asserted that
    21 -> 3 -> 0 was reached ON STOCK via the external fallback.

    R-0915-116(2)(iii): that fallback is deleted. On stock the `-reroute` call
    errors, nothing is inserted, the pass is UNJUDGED and the loop stops. The
    #110 residual is then REPORTED by the authoritative post-loop check rather
    than repaired. This is a real loss on that binary and it is recorded as
    one -- see the module docstring. What must never happen is the loop
    claiming convergence it did not measure, or reaching for a whole-design
    route to get it."""
    harness = _STOCK_HARNESS % "21 3 0"
    res = _run(harness + R._antenna_repair_tcl(_pdk()))
    assert res.returncode == 0, res.stderr
    # the fork -reroute is unavailable on stock -> the raise is named, as before
    assert "ANTENNA_NATIVE_REROUTE_NONFATAL" in res.stdout
    # ...and judged, not papered over
    assert "ANTENNA_NATIVE_ERROR_UNJUDGED" in res.stdout
    assert "ANTENNA_LOOP_CONVERGED" not in res.stdout
    assert "ANTENNA_POSTROUTE_DONE" in res.stdout
    # the run still reaches its authoritative check and still ends
    assert "ANTENNA_LOOP_SEQUENCE" in res.stdout


@needs_tclsh
def test_the_escalating_diode_budget_survives_on_a_binary_that_has_reroute():
    """WAS `test_stock_path_escalates_diode_budget_until_clear`, driven on the
    stock harness whose `-reroute` errors. R-0915-116(2) removed the branch
    that turned that error back into a repair pass, so the escalation can only
    be observed where a repair actually happens: on a binary that HAS
    `-reroute` -- which is what the shipped image is.

    The ESCALATION ITSELF IS UNCHANGED and is what this test still pins: the
    margin grows 0 -> 10 -> ... across turns while the count is non-zero, so
    the diode budget is escalating rather than one-shot, and the loop still
    converges on 21 -> 3 -> 3 -> 0."""
    fork = _STOCK_HARNESS.replace(
        '  if {[lsearch $args -reroute] >= 0} { error "STA-0562 unknown flag -reroute" }\n',
        "")
    harness = fork % "21 3 3 0"
    res = _run(harness + R._antenna_repair_tcl(_pdk()))
    assert res.returncode == 0, res.stderr
    assert "ANTENNA_NATIVE_REROUTE_NONFATAL" not in res.stdout  # the fork path
    assert "margin=0" in res.stdout
    assert "margin=10" in res.stdout      # escalated after turn 0
    assert "ANTENNA_LOOP_CONVERGED" in res.stdout


@needs_tclsh
def test_clean_design_is_a_noop_no_repair_called():
    """ACCEPTANCE GATE (no-op on a clean design): when the precheck reports 0
    the loop is SKIPPED entirely — no `repair_antennas` pass runs, so no spurious
    diode is inserted (proven live on spm sky130: precheck 0/0 -> skip)."""
    harness = _STOCK_HARNESS % "0 0"
    res = _run(harness + R._antenna_repair_tcl(_pdk()))
    assert res.returncode == 0, res.stderr
    assert "ANTENNA_ALREADY_CLEAN" in res.stdout
    # the repair loop never ran -> zero diode-inserting repair passes
    assert "REPAIR_ANTENNA_DONE" not in res.stdout
    assert "ANTENNA_LOOP_CONVERGED" not in res.stdout
    # prove it geometrically: the diode counter emulated in the harness is 0
    probe = (harness + R._antenna_repair_tcl(_pdk())
             + '\nputs "DIODES_INSERTED=$::diodes"\n')
    res2 = _run(probe)
    assert "DIODES_INSERTED=0" in res2.stdout


@needs_tclsh
def test_stock_path_no_false_convergence_when_residual_persists():
    """HONESTY: if OSS OpenROAD genuinely cannot clear the residual, the loop
    must NOT emit ANTENNA_LOOP_CONVERGED — it runs the cap and leaves the
    authoritative in-session check to report the true residual (never masked)."""
    harness = _STOCK_HARNESS % "3 3 3 3 3 3 3 3"
    res = _run(harness + R._antenna_repair_tcl(_pdk()))
    assert res.returncode == 0, res.stderr
    assert "ANTENNA_LOOP_CONVERGED" not in res.stdout
    assert "ANTENNA_POSTROUTE_DONE" in res.stdout


def test_full_route_requires_the_parent_restored_retry():
    """WAS `test_block_degrades_without_reroute_and_never_full_global_routes`,
    which required BOTH the no-`-reroute` retry AND a whole-design
    `detailed_route` to be present in the emitted deck.

    R-0915-116(2)(iii) deleted the immediate in-session fallback. The scoped
    path and the ban on full `global_route` remain. A later measured DRT-0712
    permits one complete detailed route only after the parent restores an
    untouched ODB in a new session; failure still refuses that transaction."""
    cmds = "\n".join(ln for ln in R._antenna_repair_tcl(_pdk()).splitlines()
                     if not ln.lstrip().startswith("#"))
    assert not _invokes_cmd(cmds, "global_route")
    # The ordinary path keeps the native scoped reroute. A complete route is
    # allowed only under the parent's fresh-ODB retry flag after DRT-0712.
    assert _invokes_cmd(cmds, "detailed_route")
    assert cmds.index("if {$_ant_full_retry}") < cmds.index(
        "detailed_route {*}$_vic_drc_opt")
    assert "ANTENNA_FULL_ROUTE_REFUSED" in cmds
    # the native incremental path -- repair AND reroute in one call -- remains
    assert "-reroute" in cmds

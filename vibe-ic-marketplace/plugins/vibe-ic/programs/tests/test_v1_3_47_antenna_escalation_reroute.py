"""v1.3.47 — antenna-repair ESCALATION: tool-native `repair_antennas -reroute`
+ escalating `-ratio_margin`, pinned here (chip/PDK-AGNOSTIC).

Motivation (ORGANIC #110): on a large, dense routed design (sha256, 26268-cell,
DRC-clean) the pre-v1.3.47 antenna loop (`repair_antennas -iterations 1` ->
`detailed_route`, no margin) leaves a small residual (3 net / 4 pin, met1
side-area) — the diode/jumper fix is computed on the global-route estimate and
the realizing reroute re-introduces a met1 side-area antenna the repair did not
foresee. The fix drives each outer turn through the fork's tool-native
`repair_antennas -reroute` (ONE repair pass + an incremental detailed_route of
ONLY the diode-dirty nets, via hasInitialRouting — a single-pass repair WITHOUT a
reroute silently deletes the diode-dirty net's wire so check_antennas reads a
false 0) with an ESCALATING `-ratio_margin` (0->40) that over-fixes to give
head-room against the reroute re-introduction, until check_antennas == 0 or the
cap. On a build without `-reroute` the catch falls back to the external
repair -> detailed_route pass (byte-compatible with the pre-fix loop).

These tests pin the emitted-TCL control logic + verdict on SYNTHETIC before/after
antenna reports (FAIL->PASS), so a blind run auto-covers the escalation.

SUPERSEDED IN ONE PART BY R-0915-116(2) (2026-09-21), NOT IN THE REST. The
ESCALATION is untouched: native `-reroute` first, `-ratio_margin` 0->40, the
bounded outer loop, no full `global_route`. What is gone is the DEGRADED
BRANCH's whole-design `detailed_route`. It was measured destroying finished
work on two ICs: on spm x gf180mcuD (lane icspm5, run12, v1.22.60) the base
route had CONVERGED and GRT-0012 read "Found 0 antenna violations" when the
fallback ran anyway and broke it (DRT-0206); on subservient x gf180mcuD (int7)
it hit DRT-1231 and cost the run its routed.def, DRC, LVS and post-route STA.
A raise out of the native path is now JUDGED BY CONNECTIVITY of the nets that
pass touched. The tests below that pinned the fallback are re-aimed at the
judgement, under names that say what they now pin; each still fails if the
whole-design route comes back.
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


def _cmd_lines(block: str) -> str:
    return "\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def _run_tclsh(script_text: str):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ant.tcl"
        p.write_text(script_text)
        return _pr.run([tclsh, str(p)],
                              capture_output=True, text=True)


# ── emitted-shape assertions ─────────────────────────────────────────────────

def test_block_uses_tool_native_reroute():
    """Each repair turn must PREFER `repair_antennas ... -reroute` (the fork's
    proven repair+incremental-reroute-in-one-call path)."""
    cmds = _cmd_lines(R._antenna_repair_tcl(_pdk()))
    assert "-reroute" in cmds
    assert "repair_antennas sky130_fd_sc_hd__diode_2 -iterations 1" in cmds


def test_block_escalates_ratio_margin():
    """The margin must start at 0 and ESCALATE (over-fix head-room against the
    reroute re-introducing a residual)."""
    cmds = _cmd_lines(R._antenna_repair_tcl(_pdk()))
    assert "-ratio_margin" in cmds
    assert "_ant_margin" in cmds
    # escalation step present (grows the margin each turn)
    assert "$_ant_margin + 10" in cmds or "incr _ant_margin" in cmds


def test_block_keeps_incremental_outer_loop_and_no_global_route():
    """Still the bounded incremental OUTER loop; still NO full global_route
    (the ibex full-reroute timeout).

    R-0915-116(2)(iii): and now no full `detailed_route` either. This line used
    to read `assert "detailed_route -verbose 0" in cmds` -- the external
    fallback reroute. It is DELETED from this stage exactly as R-0915-114(a)
    deleted it from the PG-reconnect block, and for the same measured reason:
    a whole-design re-route of an already-routed design does not converge
    (a NO-OP full route of int7's pre-diode database produced 57178 changed
    net lines and DRT-0206 with 1212 checkConnectivity breaks, 0 of them on a
    supply net). The assertion is inverted, not dropped, so the fallback
    cannot come back unnoticed."""
    cmds = _cmd_lines(R._antenna_repair_tcl(_pdk()))
    assert "set _ant_cap" in cmds
    assert "for {set _i 0} {$_i < $_ant_cap} {incr _i}" in cmds
    assert "global_route" not in cmds
    assert "detailed_route" not in cmds               # R-0915-116(2)(iii)
    assert "-iterations 5" not in cmds


def test_block_judges_the_raise_instead_of_falling_back_to_a_route():
    """WAS `test_block_has_external_fallback_when_reroute_unsupported`, which
    pinned the degraded branch as `repair_antennas` (no -reroute) followed by a
    whole-design `detailed_route`. R-0915-116(2) replaces that branch: a raise
    out of the native path is still NON-FATAL and still named, but what follows
    is a CONNECTIVITY JUDGEMENT of the nets that pass touched -- spurious
    (everything still wired) lets the loop measure again, broken rolls this
    pass's diodes back by name and leaves the violation standing.

    CONSEQUENCE, STATED PLAINLY: on a binary that does not support `-reroute`
    at all (the deployed stock 0.2.5 of ORGANIC #110) the native call inserts
    nothing, the judgement reads UNJUDGED and the loop STOPS. That path no
    longer converges. The shipped image is the fork (0.3.67, pinned by digest)
    and does support `-reroute`, so the shipped flow is unaffected; a stock
    binary now reports its antenna violation instead of buying a false 0 with
    a route that unwires the design. See
    `test_issue110_antenna_stock_escalation_converge.py` for that half."""
    cmds = _cmd_lines(R._antenna_repair_tcl(_pdk()))
    assert "ANTENNA_NATIVE_REROUTE_NONFATAL" in cmds   # still non-fatal, named
    assert "REPAIR_ANTENNA_NONFATAL" not in cmds       # the no-reroute retry is gone
    assert "ANTENNA_NATIVE_ERROR_SPURIOUS" in cmds
    assert "ANTENNA_DIODE_ROLLED_BACK" in cmds
    assert "ANTENNA_NATIVE_ERROR_UNJUDGED" in cmds


def test_block_skips_when_pdk_has_no_diode():
    pdk = _pdk()
    pdk.antenna_diode_cell = None
    block = R._antenna_repair_tcl(pdk)
    assert "ANTENNA_REPAIR_SKIPPED" in block
    assert "repair_antennas" not in block


# ── control-logic proofs on SYNTHETIC before/after antenna reports ───────────

_SIM_HARNESS = """
set ::seq {%s}
set ::i 0
proc check_antennas {args} {
  set v [lindex $::seq $::i]
  if {$::i < [expr {[llength $::seq]-1}]} { incr ::i }
  return $v
}
proc repair_antennas {args} { %s }
proc detailed_route {args} { return "" }
"""


@needs_tclsh
def test_parse_eval_reaches_postroute_done():
    """Real Tcl parse/eval with every tool command stubbed reaches the terminal
    marker with returncode 0 (OpenROAD is a Tcl interpreter)."""
    script = 'proc unknown {args} { return "" }\n' + R._antenna_repair_tcl(_pdk())
    res = _run_tclsh(script)
    assert res.returncode == 0, res.stderr
    assert "missing close-bracket" not in res.stderr
    assert "ANTENNA_POSTROUTE_DONE" in res.stdout


@needs_tclsh
def test_native_reroute_path_converges_fail_to_pass():
    """FAIL->PASS via the tool-native `-reroute` path: check_antennas 21 -> 3
    -> 0 must emit ANTENNA_LOOP_CONVERGED (repair_antennas -reroute SUCCEEDS, so
    the external detailed_route is never needed)."""
    harness = _SIM_HARNESS % ("21 3 0", 'return ""')
    res = _run_tclsh(harness + R._antenna_repair_tcl(_pdk()))
    assert res.returncode == 0, res.stderr
    assert "ANTENNA_LOOP_CONVERGED" in res.stdout
    assert "ANTENNA_POSTROUTE_DONE" in res.stdout


@needs_tclsh
def test_no_reroute_support_stops_and_reports_instead_of_routing():
    """WAS `test_fallback_path_converges_when_reroute_unsupported`, which drove
    the deleted branch and asserted ANTENNA_LOOP_CONVERGED came out of it.

    R-0915-116(2): when `repair_antennas` errors on `-reroute` it has inserted
    nothing, so there is nothing whose connectivity can be judged -- the pass
    is UNJUDGED and the loop STOPS. It does not convert an unjudgeable pass
    into a clean one, and it does not run a whole-design route to try to
    realise a repair that never happened. The run still completes and still
    reaches its authoritative post-loop check, which reports the residual."""
    repair_body = ('if {[lsearch $args -reroute] >= 0} '
                   '{ error "unknown option -reroute" }\n  return ""')
    harness = _SIM_HARNESS % ("21 3 0", repair_body)
    res = _run_tclsh(harness + R._antenna_repair_tcl(_pdk()))
    assert res.returncode == 0, res.stderr
    assert "ANTENNA_NATIVE_REROUTE_NONFATAL" in res.stdout   # still named
    assert "ANTENNA_NATIVE_ERROR_UNJUDGED" in res.stdout     # and judged, not hidden
    assert "ANTENNA_LOOP_CONVERGED" not in res.stdout        # no false convergence
    assert "ANTENNA_POSTROUTE_DONE" in res.stdout            # the run still ends


@needs_tclsh
def test_no_false_convergence_when_residual_never_clears():
    """HONESTY: if the antenna count NEVER reaches 0, the loop must NOT emit
    ANTENNA_LOOP_CONVERGED — it runs the cap and leaves the authoritative check
    to report the residual (no masking)."""
    harness = _SIM_HARNESS % ("7 7 7 7 7 7 7 7", 'return ""')
    res = _run_tclsh(harness + R._antenna_repair_tcl(_pdk()))
    assert res.returncode == 0, res.stderr
    assert "ANTENNA_LOOP_CONVERGED" not in res.stdout
    assert "ANTENNA_POSTROUTE_DONE" in res.stdout


@needs_tclsh
def test_margin_escalates_across_turns():
    """The emitted REPAIR_ANTENNA_DONE trace must show the margin GROWING across
    turns (0,10,20,...) when the residual persists."""
    harness = _SIM_HARNESS % ("9 9 9 9 9 9 9 9", 'return ""')
    res = _run_tclsh(harness + R._antenna_repair_tcl(_pdk()))
    assert "margin=0" in res.stdout
    assert "margin=10" in res.stdout
    assert "margin=20" in res.stdout

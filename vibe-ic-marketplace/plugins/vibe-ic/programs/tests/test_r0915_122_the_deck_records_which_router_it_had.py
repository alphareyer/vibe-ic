"""The same deck is correct on a scoped router and an unscoped one.

icord1 put the missing capability into the fork rather than around it in Tcl,
and MEASURED it on run15's own `antenna_pass_pre.odb` with ONE patched binary
(26Q3-2625-g79f27347e2):

    detailed_route -nets {p__core}   LOST 0, SHRANK 0, 0 of 654 untouched nets
                                     changed a byte, antenna 0, rc 0, 26 s,
                                     DRC 621 byte-identical to baseline
    the SAME binary, no -nets        LOST 93, SHRANK 55, GREW 505, DRT-0206

The second line reproduces this lane's numbers exactly -- I measured 93/55/505
on the same DB through a `dont_touch`-scoped attempt, which is how we learned
that `dont_touch` does not restrain `detailed_route` and that 0.3.67 offers no
net subset at all (its own help lists none). On the patched build
`repair_antennas -reroute` routes scoped by itself: GRT-0313 antenna-clean
after one pass, only p__core's bytes changed.

So the deck keeps everything R-0915-121 landed -- the loop, the wire census,
the refusal, the rollback, the stop -- and adds only what tells the two
regimes apart:

  (a) the `-reroute` path is NOT stripped. On 0.3.70 it IS the scoped one.
  (b) a healthy pass censuses 0 lost / 0 shrunk and the loop CONTINUES; the
      refusal is driven by measurement, so it simply does not fire there.
  (c) the router's version is RECORDED, and the scoping capability is ASKED
      FOR rather than inferred from it -- a version string is a label,
      `-nets` in the command's own registered body is the fact. MEASURED on
      0.3.67 in this lane's container: `ord::openroad_version` =
      26Q3-2599-g697a63f1ad, and `info body detailed_route` contains
      `-verbose` and NOT `-nets`.

Both probes fail SAFE: an unreadable version records UNKNOWN and an unreadable
body records scoped=0, which keeps today's refuse-and-roll-back.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

SCOPED_VERSION = "26Q3-2625-g79f27347e2"      # icord1's patched fork
UNSCOPED_VERSION = "26Q3-2599-g697a63f1ad"    # 0.3.67, measured in-container


def _pdk():
    return R.PdkConfig(
        name="gf180mcuD", liberty="/l", tech_lef="/t", cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fx__filltie", antenna_diode_cell="fx__antenna")


def _tcl():
    return R._antenna_repair_tcl(_pdk())


# ── a fake router that reports a version AND a command body ─────────────────
#
# `info body detailed_route` is the capability probe, so the fake defines
# `detailed_route` as a real proc whose body does or does not mention -nets.
_ROUTER = r"""
set ::seq {%(seq)s}
set ::i 0
proc check_antennas {args} {
  set v [lindex $::seq $::i]
  if {$::i < [expr {[llength $::seq]-1}]} { incr ::i }
  return $v
}
proc write_db {args} { return "" }
namespace eval ord {
  proc openroad_version {} { return "%(version)s" }
  proc get_db_block {} { return ::BLK }
}
%(route_proc)s
set ::insts {i1}
set ::nets {nA}
set ::wire(nA) 1
set ::wlen(nA) 100
proc repair_antennas {args} { %(repair)s }
namespace eval odb { proc dbInst_destroy {i} { return "" } }
proc ::BLK {method args} {
  switch -- $method {
    getInsts { set r {} ; foreach n $::insts { lappend r ::INST_$n } ; return $r }
    getNets  { return {::NET_nA} }
    findInst { return NULL }
    findNet  { return ::NET_nA }
  }
  return NULL
}
proc ::INST_i1 {method args} {
  switch -- $method { getName { return i1 } getITerms { return {} } }
  return 0
}
proc ::INST_d1 {method args} {
  switch -- $method { getName { return d1 } getITerms { return {::IT_d1} } }
  return 0
}
proc ::IT_d1 {method args} { switch -- $method { getNet { return ::NET_nA } } ; return NULL }
proc ::NET_nA {method args} {
  switch -- $method {
    getName   { return nA }
    isSpecial { return 0 }
    getWire   { return [expr {$::wire(nA) ? "::WIRE_nA" : "NULL"}] }
  }
  return 0
}
proc ::WIRE_nA {method args} { switch -- $method { length { return $::wlen(nA) } } ; return 0 }
"""

# the two command bodies the probe distinguishes -- the ONLY difference is
# whether `-nets` appears, which is exactly what the deck asks about.
_SCOPED_PROC = ('proc detailed_route {args} '
                '{ # supports -nets <list>\n  return "" }')
_UNSCOPED_PROC = ('proc detailed_route {args} '
                  '{ # supports -verbose <level>\n  return "" }')

_HEALTHY = 'return ""'                                   # no raise at all
_DAMAGING = ('lappend ::insts d1 ; set ::wire(nA) 0 ; '
             'error "DRT-0206 checkConnectivity"')


def _drive(version, route_proc, repair, seq="5 5 0"):
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    script = (_ROUTER % {"seq": seq, "version": version,
                         "route_proc": route_proc, "repair": repair}) + _tcl()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.tcl"
        p.write_text(script)
        return subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                              cwd=td)


# ── (c) the record, on both images ─────────────────────────────────────────

@needs_tclsh
def test_a_scoped_router_is_recorded_as_scoped():
    r = _drive(SCOPED_VERSION, _SCOPED_PROC, _HEALTHY)
    assert f"ANTENNA_ROUTER: version={SCOPED_VERSION} scoped_reroute=1" \
        in r.stdout


@needs_tclsh
def test_an_unscoped_router_is_recorded_as_unscoped():
    r = _drive(UNSCOPED_VERSION, _UNSCOPED_PROC, _HEALTHY)
    assert f"ANTENNA_ROUTER: version={UNSCOPED_VERSION} scoped_reroute=0" \
        in r.stdout


@needs_tclsh
def test_the_capability_not_the_version_decides():
    """A version string is a label. If a build ever reports the scoped
    version WITHOUT the command, the deck must believe the command."""
    r = _drive(SCOPED_VERSION, _UNSCOPED_PROC, _HEALTHY)
    assert "scoped_reroute=0" in r.stdout
    r2 = _drive(UNSCOPED_VERSION, _SCOPED_PROC, _HEALTHY)
    assert "scoped_reroute=1" in r2.stdout


@needs_tclsh
def test_an_unreadable_version_records_unknown_and_stays_unscoped():
    """FAIL SAFE: unknown regime keeps today's refuse-and-roll-back."""
    script = (_ROUTER % {"seq": "5 5 0", "version": "x",
                         "route_proc": _UNSCOPED_PROC, "repair": _HEALTHY})
    script = script.replace('proc openroad_version {} { return "x" }',
                            'proc openroad_version {} { error "no version" }')
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.tcl"
        p.write_text(script + _tcl())
        r = subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                           cwd=td)
    assert "ANTENNA_ROUTER: version=UNKNOWN scoped_reroute=0" in r.stdout


# ── (a) and (b): the scoped path is kept, and a healthy pass proceeds ───────

@needs_tclsh
def test_the_native_reroute_path_is_not_stripped():
    """On 0.3.70 `-reroute` IS the scoped route. Removing it would remove the
    fix icord1 built."""
    assert "-reroute" in _tcl()


@needs_tclsh
def test_a_healthy_pass_on_a_scoped_router_converges_and_never_refuses():
    r = _drive(SCOPED_VERSION, _SCOPED_PROC, _HEALTHY, seq="5 5 0")
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_LOOP_CONVERGED" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" not in r.stdout
    assert "ANTENNA_REPAIR_REFUSED_STOP" not in r.stdout + r.stderr
    assert "ANTENNA_REPAIR_APPLIED" in r.stdout


@needs_tclsh
def test_the_backstop_still_fires_on_an_unscoped_router():
    """Everything R-0915-121 landed is kept, and this is why."""
    r = _drive(UNSCOPED_VERSION, _UNSCOPED_PROC, _DAMAGING, seq="5 5 5")
    assert "ANTENNA_NATIVE_DAMAGE_CENSUS" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST" in r.stdout
    assert "ANTENNA_REPAIR_REFUSED_STOP" in r.stdout + r.stderr
    assert r.returncode != 0


@needs_tclsh
def test_a_scoped_router_that_damages_anyway_is_named_a_regression():
    """THE BACKSTOP IS NOT CONDITIONAL ON THE REGIME -- it is driven by the
    census -- but the log says which regime it fired in, because on a scoped
    router damage is a fork regression and not an expected cost."""
    r = _drive(SCOPED_VERSION, _SCOPED_PROC, _DAMAGING, seq="5 5 5")
    assert "ANTENNA_SCOPED_ROUTER_STILL_DAMAGED" in r.stdout
    assert f"version={SCOPED_VERSION}" in r.stdout
    assert "that is a router regression" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert r.returncode != 0


@needs_tclsh
def test_an_unscoped_router_that_damages_is_not_called_a_regression():
    """OVER-BREADTH CONTROL: the expected cost must not be reported as a bug."""
    r = _drive(UNSCOPED_VERSION, _UNSCOPED_PROC, _DAMAGING, seq="5 5 5")
    assert "ANTENNA_SCOPED_ROUTER_STILL_DAMAGED" not in r.stdout


def test_everything_r0915_121_landed_is_still_here():
    """The census, the refusal, the rollback request, the stop, the
    no-sweep guard and the per-boundary probe are the backstop."""
    tcl = _tcl()
    for marker in ("ANTENNA_NATIVE_DAMAGE_CENSUS",
                   "ANTENNA_DIODE_ROLLED_BACK",
                   "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST",
                   "ANTENNA_REPAIR_REFUSED_STOP",
                   "_vic_antenna_refused",
                   "_ant_wire0", "_ant_shrunk", "_ant_damage"):
        assert marker in tcl, marker


def test_the_deck_still_runs_no_whole_design_route_of_its_own():
    cmds = "\n".join(l for l in _tcl().splitlines()
                     if not l.lstrip().startswith("#"))
    assert "detailed_route -verbose 0 {*}$_vic_drc_opt" not in cmds
    assert "global_route" not in cmds


def test_the_emitted_deck_is_balanced_tcl():
    cmds = "\n".join(l for l in _tcl().splitlines()
                     if not l.lstrip().startswith("#"))
    assert sum(l.count("{") - l.count("}") for l in cmds.splitlines()) == 0
    assert sum(l.count("[") - l.count("]") for l in cmds.splitlines()) == 0

"""The PG block's DRV check is a DELTA, and an uncounted counter is not a pass.

MEASURED on spm run16L (icsub2x's tip + icord1's fork binary). The check
raised at openroad.log:1775

    PG_DELTA_DRC: the router reports 599 DRV(s) after the PG re-connect

and the 599 was already there at line 1317, printed by the SCOPED ROUTE's own
post-route verification:

    DRT-0703 Post-route non-sufficient-metal repair: handed 7, widened 0,
             left 7 unresolved
    DRT-0706 NS-Metal repair outcome: handed 7, cleared 0, still present 7
    DRT-0701 Post-route verification found 599 violation(s) that the routing
             loop did not report (0 in-loop)
    DRT-0634 Scoped detailed routing touched 1 net(s) and left 654 net(s)
             byte-identical. Named: p__core.

-- some 460 lines before the PG block ran. The base route had verified clean
(DRT-0199 494 -> 201 -> 129 -> 2 -> 0, then DRT-0702 "Post-route verification:
0 violation(s)"), and the PG block lays no geometry at all: MEASURED 9498 PG
shapes before `global_connect` and 9498 after. It cannot have made them.

AND IT PASSED SILENTLY EVERYWHERE ELSE FOR THE WRONG REASON.
`detailed_route_num_drvs` is drt SESSION STATE, not a property of the
database. MEASURED on run16L's own checkpoints, read fresh with the FORK
binary: sdr_transaction/pre_repair.odb, sdr_transaction/candidate.odb,
sdr_transaction_reconverge/pre_repair.odb and .../candidate.odb all answer
DRT-0002. On 0.3.67 that state did not survive to this point, the `catch` left
-1, and `-1 > 0` is false -- so an UNMEASURED counter read as a pass on every
previous run. That is the same false-zero shape as `dbSBox.getBox` and the
`_ant_pre` array: a probe that cannot see reports "nothing there".

So the check now takes the count BEFORE the block and AFTER it, judges the
DIFFERENCE, discloses both numbers, and says UNKNOWN BY NAME when either end
is missing.

WHAT IS *NOT* CLAIMED HERE: whether those 599 are real DRC. Nobody can say
from run16L's artefacts, because nobody asked -- `-output_drc` was requested
for the BASE route (which wrote an empty report, consistent with its verified
0) and the scoped route inside `repair_antennas -reroute` was never given one.
That is a separate, named gap.
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


def _block():
    return R._build_pg_reconnect_tcl(reroute=True)


def _cmds():
    return "\n".join(l for l in _block().splitlines()
                     if not l.lstrip().startswith("#"))


# a session whose DRV counter returns a scripted sequence, so before/after can
# differ -- or raise DRT-0002, which is what a counter with no state does.
_H = r"""
set ::drv {%(seq)s}
set ::i 0
proc detailed_route_num_drvs {} {
  if {[lindex $::drv $::i] eq "ERR"} { error "DRT-0002" }
  set v [lindex $::drv $::i]
  if {$::i < [expr {[llength $::drv]-1}]} { incr ::i }
  return $v
}
proc global_connect {args} { return "" }
proc pin_access {args} { return "" }
namespace eval ord { proc get_db_block {} { return ::BLK } }
proc ::BLK {method args} {
  switch -- $method {
    getInsts   { return {} }
    getNets    { return {} }
    getDieArea { return ::DIE }
  }
  return NULL
}
proc ::DIE {method args} {
  switch -- $method { xMin {return 0} yMin {return 0} xMax {return 1000} yMax {return 1000} }
  return 0
}
"""


def _drive(seq):
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pg.tcl"
        p.write_text((_H % {"seq": seq}) + _block())
        return subprocess.run([tclsh, str(p)], capture_output=True, text=True,
                              cwd=td)


@needs_tclsh
def test_a_count_the_block_inherited_is_not_its_verdict():
    """run16L's case, reduced: 599 before, 599 after. The block added
    nothing, so it must not refuse."""
    r = _drive("599 599")
    assert "PG_DELTA_DRC_BEFORE: 599" in r.stdout
    assert "PG_DELTA_DRC_DELTA: 0 (before=599 after=599)" in r.stdout
    assert "PG_DELTA_DRC:" not in r.stdout + r.stderr
    assert r.returncode == 0, r.stderr


@needs_tclsh
def test_a_rise_across_the_block_is_still_refused():
    """THE TOOTH THE CHECK KEEPS. The block lays no geometry, so a rise is
    its verdict."""
    r = _drive("10 42")
    out = r.stdout + r.stderr
    assert "PG_DELTA_DRC_DELTA: 32 (before=10 after=42)" in r.stdout
    assert "the router's DRV count rose by 32 across the PG re-connect" in out
    assert r.returncode != 0


@needs_tclsh
def test_a_fall_is_not_a_refusal():
    """OVER-BREADTH CONTROL: only a RISE is this block's verdict."""
    r = _drive("42 10")
    assert "PG_DELTA_DRC_DELTA: -32" in r.stdout
    assert "PG_DELTA_DRC:" not in r.stdout + r.stderr
    assert r.returncode == 0, r.stderr


@needs_tclsh
def test_a_counter_with_no_state_is_unknown_by_name_not_a_pass():
    """THE DEFECT THAT HID THE OTHER ONE: on 0.3.67 the counter answered
    DRT-0002, the catch left -1, and `-1 > 0` was false. An unmeasured
    counter read as a pass on every run before run16L."""
    r = _drive("ERR ERR")
    assert "PG_DELTA_DRC_UNKNOWN" in r.stdout
    assert "NOT MEASURED" in r.stdout
    assert "this is not a pass" in r.stdout
    assert "DRT-0002" in r.stdout
    assert r.returncode == 0, r.stderr


@needs_tclsh
def test_one_missing_end_is_also_unknown():
    """A delta needs both ends. Having only the AFTER number is exactly the
    situation that produced the false verdict."""
    r = _drive("ERR 599")
    assert "PG_DELTA_DRC_UNKNOWN" in r.stdout
    assert "before=-1" in r.stdout
    assert "PG_DELTA_DRC:" not in r.stdout + r.stderr


def test_the_before_reading_is_taken_before_the_connect():
    """A 'before' taken after `global_connect` measures nothing."""
    c = _cmds()
    assert c.index("PG_DELTA_DRC_BEFORE") < c.index("global_connect")


def test_the_absolute_count_is_still_disclosed():
    """The delta is the verdict; both numbers stay in the log so a reader can
    attribute an inherited count to the stage that created it."""
    c = _cmds()
    for m in ("PG_DELTA_DRC_BEFORE", "PG_DELTA_DRC_COUNT",
              "PG_DELTA_DRC_DELTA"):
        assert m in c, m


def test_the_block_still_never_routes():
    c = _cmds()
    assert "detailed_route -verbose" not in c
    assert "global_route" not in c


def test_the_emitted_block_is_balanced_tcl():
    c = _cmds()
    assert sum(l.count("{") - l.count("}") for l in c.splitlines()) == 0
    assert sum(l.count("[") - l.count("]") for l in c.splitlines()) == 0

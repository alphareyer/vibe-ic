"""The CTS legality block refuses for TWO unrelated reasons; it must say which.

`_cts_legal_buffer_selection_tcl` reads the resizer's live exclusion set by
capturing `report_dont_use` through `utl::redirectString*`, between two
sentinels it writes itself. It refuses when the read did not work. But "did
not work" covers two conditions with nothing in common:

  A. `report_dont_use` RAISED. The cell policy genuinely is unavailable, and
     `catch` puts the tool's own words in the error variable.
  B. `report_dont_use` RETURNED rc=0 and the CAPTURE came back without the
     sentinels. Nothing at all is known about the cell policy -- not because
     the policy is missing, but because the channel that was to carry it did
     not round-trip. `catch` sets its variable to the command RESULT on
     success, so the error variable is EMPTY in this branch.

Before this change both raised `CTS_CELL_POLICY_UNAVAILABLE: $_cts_policy_err`,
so B emitted `CTS_CELL_POLICY_UNAVAILABLE: ` -- a claim about the POLICY, with
nothing after the colon, for a failure of the CHANNEL. That sentence is what
the six deck-evaluator files emitted 32 times at v1.19.95 (bisect: aa08f0556
clean -> 4b74ba713 17 red -> e435688b7 clean), and it sent every reader at the
PDK and the pinned image. Neither was the subject: the pinned image ships
`report_dont_use` and round-trips the capture (verified against
`sha256:89a8fd72…`, OpenROAD 26Q3-2075-g18e98f9e44, with and without
`-metrics`). The subject was the test stand-in, which `_pnr_tcl_stub` models.

These pin the PROPERTY, not the wording: drive both conditions through the
same emitted block and require each refusal to identify its own subject, and
require the two to be distinguishable from one another. The FIRING CONDITION
is not under test here -- `test_i2172_cts_buf_list_from_pdk_family` already
pins that the block refuses; what is pinned here is that the refusal is
readable enough to act on.
"""
import importlib
import shutil
import subprocess

import pytest

R = importlib.import_module("phase3_one_shot_runner")

_TCLSH = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(_TCLSH is None, reason="tclsh not installed")

#: A `utl` that captures exactly as OpenROAD's does -- measured in the pinned
#: image: `utl::report` under an active redirect accumulates, and
#: `redirectStringEnd` hands the accumulation back.
_UTL_FAITHFUL = r'''
namespace eval utl {
  variable cap ""
  variable on 0
  proc redirectStringBegin {} { variable cap ""; variable on 1 }
  proc report {args} {
    variable on
    variable cap
    if {$on} { append cap "[join $args { }]\n" } else { puts [join $args " "] }
  }
  proc redirectStringEnd {} {
    variable on
    variable cap
    set on 0
    set out $cap
    set cap ""
    return $out
  }
}
'''

#: CONDITION A -- the tool itself refuses. This is the real message the pinned
#: image emits when the block runs before a design is linked (measured:
#: `openroad -no_init`, `report_dont_use` -> rc=1, "no network has been
#: linked.").
_TOOL_RAISES = _UTL_FAITHFUL + '''
proc report_dont_use {} { error "no network has been linked." }
'''

#: CONDITION B -- every command answers, nothing is captured. This is not a
#: hypothetical: it is exactly what the one-line `proc unknown` stand-in did,
#: and it is what any future `utl` whose redirect stopped round-tripping would
#: do to a real run.
_CAPTURE_LOST = 'proc unknown {args} { return "" }\n'


def _refuse(prelude):
    """Run the emitted block under `prelude` and return its refusal text.

    `tclsh` reading a script from stdin exits 0 even after an uncaught error,
    so the body is wrapped and the refusal is re-raised explicitly.
    """
    block = R._cts_legal_buffer_selection_tcl(["fam_1", "fam_4"], "fam_4")
    body = (prelude + "if {[catch {\n" + block
            + '\n} err]} { puts stderr $err; exit 1 }\nexit 0\n')
    result = subprocess.run([_TCLSH], input=body, text=True, capture_output=True)
    assert result.returncode == 1, (
        "the block must still refuse under both conditions; "
        f"rc={result.returncode} stdout={result.stdout!r}")
    return result.stderr.strip()


@needs_tclsh
def test_a_tool_refusal_carries_the_tools_own_words():
    """Condition A must reach the reader with the tool's message intact."""
    refusal = _refuse(_TOOL_RAISES)
    assert "CTS_CELL_POLICY_UNAVAILABLE" in refusal, refusal
    assert "no network has been linked." in refusal, refusal
    assert "report_dont_use" in refusal, refusal


@needs_tclsh
def test_a_lost_capture_is_not_reported_as_an_unavailable_policy():
    """Condition B must name the CHANNEL, and must not be an empty claim.

    The pre-fix sentence was `CTS_CELL_POLICY_UNAVAILABLE: ` -- everything
    after the colon was whitespace, because `catch` had set the error variable
    to a successful command's empty RESULT. A refusal whose explanation is
    empty is the one that cost this cluster its diagnosis.
    """
    refusal = _refuse(_CAPTURE_LOST)
    assert "CTS_CELL_POLICY_UNAVAILABLE" in refusal, refusal
    tail = refusal.split("CTS_CELL_POLICY_UNAVAILABLE:", 1)[1]
    assert tail.strip(), (
        "the refusal explained nothing -- everything after the colon was "
        f"blank: {refusal!r}")
    # it must name the channel, and say the policy was never read rather than
    # that it is unavailable
    assert "capture" in tail, refusal
    assert "redirectString" in tail, refusal
    # and it must report what the channel actually gave back, so the reader
    # can tell "nothing" from "the wrong thing"
    assert "begin_sentinel=0" in tail and "end_sentinel=0" in tail, refusal


@needs_tclsh
def test_the_two_refusals_are_distinguishable():
    """The property: one sentence may not stand for two unrelated causes.

    Compare the two OBSERVED refusals against each other. If a future edit
    collapses them back into one message this fails, whatever the wording is.
    """
    tool = _refuse(_TOOL_RAISES)
    lost = _refuse(_CAPTURE_LOST)
    assert tool != lost, f"both conditions refuse identically: {tool!r}"
    # each names its own subject and not the other's
    assert "no network has been linked." in tool
    assert "no network has been linked." not in lost
    assert "redirectString" in lost
    assert "redirectString" not in tool


@needs_tclsh
def test_the_firing_condition_is_unchanged():
    """Guard the thing this change must NOT move.

    A faithful `utl` plus a `report_dont_use` that answers is the ONLY shape
    that may reach the selection. Both refusal conditions still refuse (above),
    and the good shape still gets through to the selection it announces.
    """
    prelude = _UTL_FAITHFUL + '''
proc report_dont_use {} { utl::report "Don't Use Cells:"; utl::report "  none" }
namespace eval ord { proc get_db {} { return DB } }
proc DB {op name} {
  if {$op ne "findMaster"} { error "unexpected DB operation $op" }
  interp alias {} $name {} MASTER $name
  return $name
}
proc MASTER {name op} {
  if {$op ne "getWidth"} { error "unexpected master operation $op" }
  return 10
}
'''
    block = R._cts_legal_buffer_selection_tcl(["fam_1", "fam_4"], "fam_4")
    body = (prelude + "if {[catch {\n" + block
            + '\n} err]} { puts stderr $err; exit 1 }\nexit 0\n')
    result = subprocess.run([_TCLSH], input=body, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "CTS_LEGAL_SELECTION: buffers=fam_1 fam_4 root=fam_4" in result.stdout, (
        result.stdout)
    assert "CTS_CELL_EXCLUDED" not in result.stdout, result.stdout

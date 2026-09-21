"""A tie the rung inserts creates two new gaps, and it must look at them.

MEASURED on spm run15, from the row DF.13_MV caught. Row y=1642.48 held 75
instances and exactly TWO well ties, at x=393.12 and x=2767.52 -- 2374.4 um
apart. The anchor set is {row start, row end, midpoint between each pair of
consecutive ties}, so two ties give THREE points and exactly ONE uncovered:
the midpoint at x=1580.88. One tie was placed there and the row was declared
done -- while the two fresh 1187 um half-gaps that very insertion created were
never examined, because `_wtpts` was built ONCE before the loop and only
`_wtc` was appended to inside it.

That is why run15 reported

    WELLTIE_COVERAGE_REPAIR: pitch=15.0um radius=15.0um anchor_rows=32
    uncovered_anchors=75 ties_added=75 unplaceable=0

-- every initially-computed point got a tie, so the rung declared success --
on rows still hundreds of microns bare. The sign-off DRC then found it:
DF.13_MV, one pactive at (528.26,1644.84)-(535.24,1646.06) whose nearest ntap
inside its own nwell is 17.91 um against a 15.0 um rule. The nwell island
holds 42 ntaps and its in-row gaps are 29.12 um everywhere EXCEPT one of
407.68 um -- the residue of the single non-iterated pass.

THE POINT SET IS A FUNCTION OF THE TIES, so it cannot be computed once and
then inserted into. The rung now repeats the whole computation until a pass
finds nothing uncovered, or finds nothing it can place, or trips a cap on
PASSES -- and then RE-DERIVES the point set one last time from the ties that
actually exist, so `ties_added` becomes a claim about the ROW rather than
about the loop. Whatever is still uncovered is named by row and span
(WELLTIE_ROW_STILL_UNCOVERED) and counted in rows_still_uncovered. Never
both silent.
"""
import re
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


def _pdk():
    return R.PdkConfig(
        name="gf180mcuD", liberty="/l", tech_lef="/t", cell_lef="/c",
        cell_gds=None, site="S", drc_deck=None, metal_prefix="M",
        tapcell_master="fx__filltie", antenna_diode_cell="fx__antenna")


def _tcl():
    return R._build_welltie_coverage_repair_tcl(_pdk(), 15.0)


def _cmds():
    return "\n".join(l for l in _tcl().splitlines()
                     if not l.lstrip().startswith("#"))


# ── the spec, in Python, which the emitted Tcl is written to ───────────────

def _iterate(row_x0, row_x1, ties, radius, width, site, blocked=()):
    """What the fixed rung must do: insert until nothing is uncovered."""
    centres = sorted(t + width // 2 for t in ties)
    added = []
    for _ in range(64):
        pts = sorted({row_x0, row_x1,
                      *[(a + b) // 2 for a, b in zip(centres, centres[1:])]})
        unc = [p for p in pts
               if not any(abs(c - p) <= radius for c in centres)]
        if not unc:
            return added, []
        progress = False
        for px in unc:
            placed = None
            k0 = (px - row_x0) // site
            for j in range(radius // site + 1):
                for sgn in (1, -1):
                    x = row_x0 + (k0 + sgn * j) * site
                    if x < row_x0 or x + width > row_x1:
                        continue
                    if abs(x + width // 2 - px) > radius:
                        continue
                    if any(x < b and a < x + width for a, b in blocked):
                        continue
                    placed = x
                    break
                if placed is not None:
                    break
            if placed is not None:
                centres = sorted(centres + [placed + width // 2])
                added.append(placed)
                progress = True
        if not progress:
            break
    pts = sorted({row_x0, row_x1,
                  *[(a + b) // 2 for a, b in zip(centres, centres[1:])]})
    return added, [p for p in pts
                   if not any(abs(c - p) <= radius for c in centres)]


def test_run15s_own_row_needs_more_than_one_tie():
    """THE DEFECT, as arithmetic. Two ties 2374.4 um apart, radius 15 um: one
    insertion leaves two 1187 um half-gaps, and the old rung stopped there."""
    U = 1000                       # DEF units per micron
    row_x0, row_x1 = 392 * U, 2770 * U
    ties = [393 * U + 120, 2767 * U + 520]
    added, left = _iterate(row_x0, row_x1, ties, 15 * U, 1120, 560)
    assert len(added) > 1, (
        "one insertion cannot cover a 2374 um gap at a 15 um radius; the "
        "old rung added exactly one and called the row done")
    assert left == [], "the spec must reach full coverage on a free row"
    assert len(added) >= 75, (
        f"a 2374 um gap needs ~79 ties at 30 um effective pitch, got "
        f"{len(added)}")


def test_the_spec_stops_and_reports_when_nothing_can_be_placed():
    """A row whose whole span is occupied cannot be covered, and that must be
    a NAMED residue rather than a silent success."""
    U = 1000
    row_x0, row_x1 = 0, 200 * U
    added, left = _iterate(row_x0, row_x1, [0], 15 * U, 1120, 560,
                           blocked=[(0, 200 * U)])
    assert added == []
    assert left, "an uncoverable row must report what is still uncovered"


# ── the emitted Tcl carries the same shape ─────────────────────────────────

def test_the_point_set_is_recomputed_inside_the_loop():
    c = _cmds()
    i_while = c.index("while {1} {")
    i_pts = c.index("set _wtpts {}")
    assert i_while < i_pts, (
        "the point set is still built before the insertion loop -- that is "
        "the defect run15 shipped")


def test_a_pass_that_places_nothing_stops():
    c = _cmds()
    assert "if {$_wtprog == 0} { break }" in c


def test_the_pass_cap_is_disclosed_not_silent():
    c = _cmds()
    assert "WELLTIE_COVERAGE_REPAIR_PASS_CAP" in c
    assert "not forgiven" in c


def test_the_final_state_is_re_derived_not_assumed():
    """`ties_added` must be a claim about the ROW, not about the loop."""
    c = _cmds()
    assert "set _wtvpts {}" in c
    i_loop_end = c.index("if {$_wtprog == 0} { break }")
    assert c.index("set _wtvpts {}") > i_loop_end


# ── the row logic, EXECUTED ────────────────────────────────────────────────
#
# A string-presence assertion cannot tell a live branch from a dead one: my
# first version of the test below passed while the refusal was mutated to
# `if {0}`, because the marker TEXT was still in the emitted deck. So the
# per-row section is sliced out of the real emission and driven through tclsh
# with the arrays it reads prepared by hand.

_ROW_BEGIN = "foreach _wty [lsort -integer [array names _wtanc]] {"
_ROW_END = "set ::_vibeic_welltie_uncovered $_wtfail"


def _row_section():
    t = _tcl()
    return t[t.index(_ROW_BEGIN):t.index(_ROW_END)]


_ROW_HARNESS = """
set _wtrad %(rad)d
set _wttw  %(tw)d
array set _wtanc {%(y)d 1}
array set _wtrx0 {%(y)d %(x0)d}
array set _wtrx1 {%(y)d %(x1)d}
array set _wtrsw {%(y)d %(site)d}
array set _wttie {%(y)d {%(ties)s}}
array set _wtocc {%(y)d {%(occ)s}}
array set _wtro  {%(y)d R0}
set _wtblk ::BLK
set _wtm   ::MASTER
set _wtadded 0; set _wtneed 0; set _wtfail 0; set _wtrows 0
set _wtrowsleft 0
set _wtfaildesc {}
set ::created {}
proc ::BLK {method args} {
  switch -- $method { getInsts { return {} } }
  return NULL
}
proc ::MASTER {args} { return "" }
namespace eval odb {
  proc dbInst_create {blk master name} { lappend ::created $name ; return ::NEWI }
}
proc ::NEWI {method args} { return "" }
"""


def _drive_row(x0, x1, ties, occ=(), rad=15000, tw=1120, site=560, y=1642480):
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    head = _ROW_HARNESS % {
        "rad": rad, "tw": tw, "y": y, "x0": x0, "x1": x1, "site": site,
        "ties": " ".join(str(t) for t in ties),
        "occ": " ".join(str(o) for o in occ),
    }
    script = (head + _row_section()
              + '\nputs "ADDED=[llength $::created]"\n'
              + 'puts "ROWSLEFT=$_wtrowsleft"\n')
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "row.tcl"
        f.write_text(script)
        return subprocess.run([tclsh, str(f)], capture_output=True, text=True,
                              cwd=td)


@needs_tclsh
def test_driven_run15s_row_gets_many_ties_not_one():
    """run15's actual row: two ties 2374.4 um apart. The old rung added ONE."""
    r = _drive_row(392000, 2770000, [393120, 2767520])
    assert r.returncode == 0, r.stderr
    n = int(re.search(r"ADDED=(\d+)", r.stdout).group(1))
    assert n > 1, f"only {n} tie(s) added -- that is the run15 defect"
    assert n >= 75, f"a 2374 um gap needs ~79 ties at this radius, got {n}"
    assert "ROWSLEFT=0" in r.stdout


@needs_tclsh
def test_driven_a_row_that_cannot_be_covered_is_named_and_counted():
    """THE TEST THAT CAUGHT MY OWN DEAD-BRANCH MUTATION. The whole span is
    occupied, so nothing can be placed and the row must SAY SO."""
    r = _drive_row(0, 200000, [0], occ=(0, 200000))
    assert r.returncode == 0, r.stderr
    assert "WELLTIE_ROW_STILL_UNCOVERED" in r.stdout
    assert "points_still_uncovered=" in r.stdout
    assert "ROWSLEFT=1" in r.stdout
    assert "ADDED=0" in r.stdout


@needs_tclsh
def test_driven_a_row_already_covered_adds_nothing_and_reports_nothing():
    """OVER-BREADTH CONTROL: iteration must not invent work on a good row."""
    ties = [x for x in range(0, 200001, 20000)]
    r = _drive_row(0, 200000, ties)
    assert r.returncode == 0, r.stderr
    assert "ADDED=0" in r.stdout
    assert "ROWSLEFT=0" in r.stdout
    assert "WELLTIE_ROW_STILL_UNCOVERED" not in r.stdout


def test_a_row_that_still_has_a_gap_is_named_with_its_span():
    c = _cmds()
    assert "WELLTIE_ROW_STILL_UNCOVERED" in c
    assert "span=$_wtrx0($_wty)..$_wtrx1($_wty)" in c
    assert "points_still_uncovered=$_wtleft" in c


def test_the_summary_reports_rows_that_are_still_uncovered():
    """run15's summary said `unplaceable=0` and meant `the loop finished`.
    A reader needs the number that means `rows that still have a hole`."""
    c = _cmds()
    assert "rows_still_uncovered=$_wtrowsleft" in c


def test_the_emitted_block_is_balanced_tcl():
    c = _cmds()
    assert sum(l.count("{") - l.count("}") for l in c.splitlines()) == 0
    assert sum(l.count("[") - l.count("]") for l in c.splitlines()) == 0

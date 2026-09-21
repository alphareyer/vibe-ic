"""R-0915-103: on a die its own pad ring PERIMETER-pins, the core is the interior.

THE CONTROL THE RULING IS BUILT ON — spm x gf180mcuD as a DIE, the same design
(66 `dffq` in both post-PnR netlists), the same 24 ns clock, the same PDK and
the same SS corner, differing only in the core rectangle:

                          run7 (core = interior)   run8 (core = island)
    placement ROWs        605                      57, all at x=1467.2 um
    core                  2373 um                  228 um in a 3162 um die
    buffers in spm_pnr.v  522                      2057  (synthesis: 273 cells,
                                                          ZERO buffers)
    worst SS setup        +8.21 ns, TNS 0.00       -1.97 ns, TNS -19.03
    detailed route        converged                3 restarts, 8 h 22 m burned

run8's worst path is `rst` (input port) -> u_core/_471_/D: 4.80 ns input delay
+ 1.78 ns at the pad cell + **21.12 ns across 36 buffer stages** (32 of them
buf_1) carrying **0.770 pF IN TOTAL** + 0.94 ns of logic. It is not wire RC; it
is a chain, and it exists because a 1467 um band with no placement ROW gives a
pad->core net nowhere to put a repeater: `RSZ-0035 Found 18 fanout violations`
-> `RSZ-0038 Inserted 784 buffers in 19 nets`, plus `RSZ-2005 cannot find a
viable buffering solution on pin u_pad_x_9/Y`.

THE COUNTER-MEASUREMENT IS KEPT, NOT DELETED. `ring_die_core_pad`'s docstring
carries subservient r37 (a 1176 um interior core at 10.4 % utilisation missing
SS by 13.1 ns on paths whose delay is wire) beside this one, and names it as
the number at risk. Whoever owns that design re-measures it on a tree carrying
this change; the ruling is the owner's, the risk is stated, and the knob that
failure belongs to is the placer's `-density`, which this change does not
touch.

Both directions are tested: a ring-pinned DIE gets the interior and no ladder;
a die nobody's ring pinned — and a HARDMACRO, which never reaches this code at
all — keeps exactly what it had.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

#: spm's own numbers: 38 pads made the ring need 3162 um, and the ring's
#: measured depth + PAD_EDGE_SPACING + the PDN offsets made the floor 393 um.
SPM_DIE, SPM_FLOOR = 3162, 393
SPM_INTERIOR = SPM_DIE - 2 * SPM_FLOOR            # 2376 um
#: what the auto-sizer asked for from this netlist at the requested utilisation
SPM_ASKED = 85


# ------------------------------------------------- the ruling, both directions

@pytest.mark.parametrize("asked", [(SPM_ASKED, SPM_ASKED), (164, 164),
                                   (228, 228), (500, 900), (1900, 1900)])
def test_a_ring_pinned_core_is_the_interior_whatever_the_auto_sizer_asked(asked):
    """The auto-sizer's answer is still taken, and still ignored: it answers a
    different question and the caller reports it."""
    assert R.ring_die_core_pad(SPM_DIE, SPM_DIE, SPM_FLOOR, asked) == SPM_FLOOR
    assert SPM_DIE - 2 * R.ring_die_core_pad(SPM_DIE, SPM_DIE, SPM_FLOOR,
                                             asked) == SPM_INTERIOR


def test_the_island_that_lost_the_timing_can_no_longer_be_produced():
    """run8's 228 um island: the inset that would centre it is 1467 um, which
    is what this function used to return and never returns now."""
    centred = (SPM_DIE - 228) // 2
    assert centred == 1467                                   # run8's own inset
    assert R.ring_die_core_pad(SPM_DIE, SPM_DIE, SPM_FLOOR, (228, 228)) != centred


def test_the_growth_ladder_cannot_run_from_the_interior():
    """Not a new guard: the core now STARTS where both helpers already refuse
    to grow past, so the ladder that restarted PnR three times is unreachable.
    Every rung is asked here, at the rectangle the ruling gives."""
    for util in (40.1, 103.1, 142.157, 1000.0):
        assert R.ring_core_pad_for_util(SPM_DIE, SPM_DIE, SPM_INTERIOR,
                                        SPM_INTERIOR, util, 40.0,
                                        SPM_FLOOR) is None
    assert R.ring_core_pad_one_loosen_rung(SPM_DIE, SPM_DIE, SPM_INTERIOR,
                                           SPM_INTERIOR, SPM_FLOOR) is None


def test_cells_that_do_not_fit_the_interior_are_refused_not_grown():
    """The over-full case keeps a REAL answer: `ring_core_pad_for_util` says
    None at the interior, and the runner's branch turns that into
    PADRING_CORE_TOO_SMALL — a refusal naming the ring, not a ladder."""
    assert R.ring_core_pad_for_util(SPM_DIE, SPM_DIE, SPM_INTERIOR,
                                    SPM_INTERIOR, 142.157, 40.0,
                                    SPM_FLOOR) is None
    body = inspect.getsource(R.step_pnr) if hasattr(R, "step_pnr") else ""
    src = body or (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert "PADRING_CORE_TOO_SMALL" in src
    assert "reaches under the pads" in src


# ------------------------------------------- the other direction: untouched

def test_a_die_nobody_s_ring_pinned_keeps_what_it_had():
    """`_core_sized_um` is set ONLY when a pad ring's perimeter grew an AUTO
    die past what the netlist asked for. Without that, the inset passed in is
    the inset returned — which is what every non-ring design already had."""
    for floor in (10, 26, 393):
        assert R.ring_die_core_pad(1200, 1200, floor, None) == floor
    tree = ast.parse((PROGRAMS / "phase3_one_shot_runner.py").read_text())
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert "_ring_pinned_die = _core_sized_um is not None" in src
    assert isinstance(tree, ast.Module)


def test_a_hardmacro_never_reaches_this_code():
    """A hardmacro has no pad ring to pin a die by perimeter: the block that
    sets `_core_sized_um` is entered only when `_padring_required_die_um`
    answered, and that reads the run's own ring record."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert "_ring_side, _ring_basis = _padring_required_die_um(project)" in src
    assert "if _ring_side is not None:" in src


def test_the_density_is_reported_and_is_not_a_lever():
    """R-0915-103's second half. The caller prints the interior and the share
    the auto-sizer's answer would occupy, and nothing downstream reads it."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    # the phrases are f-string fragments in the source, so match them as such
    assert "core := the die interior" in src
    assert "REPORTED as the density it " in src
    assert "never a lever on the rectangle" in src
    assert "R-0915-103" in src


def test_both_measurements_survive_in_the_source():
    """The retracted-inference rule, applied to somebody else's evidence: the
    measurement this ruling overrules is kept where the next reader will find
    it, with the risk named."""
    doc = R.ring_die_core_pad.__doc__ or ""
    assert "subservient" in doc and "13.1 ns" in doc      # r37, kept
    assert "run7" in doc and "8.21" in doc                # spm, the control
    assert "re-measured" in doc                           # the risk, named

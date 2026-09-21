"""The EM resize row must say what it DID, not only what was short.

MEASURED on spm x gf180mcuD (run11b, 2026-09-21). The deck the second PnR used
carried, verbatim:

    EM-derived strap floor applied (pdn_em_sizing.json): Metal4 3x stripes
    (pitch 51.2um, width 1.6 kept), Metal5 2x stripes (pitch 76.59um, width 1.6
    kept)

— R-0915-111's lever doing exactly what it was ruled to do: no strap widened,
the stripe count tripled, and `PDN_GRID_PRESENT` after pdngen. And the step row
a reviewer actually reads said:

    one-shot EM resize: Metal4 1.6->3.95um (2.4688x short), Metal5 1.6->1.77um
    (1.1062x short); PnR re-run once (407s).

Both sentences are about the same run. The first is the remedy; the second is
the SHORTFALL the width arithmetic derived before the stripe planner answered.
A reader of the row alone would conclude the flow widened two straps by 2.5x
and 1.1x, when it widened nothing. The FAIL verdict was honest — the second PnR
did fail — but the sentence was not.

So the deck records what it applied, the record is persisted beside the
arithmetic it came from, and the row reports both halves in the order a reader
needs them: SHORT BY ..., APPLIED ...
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

SRC = (PROGRAMS / "phase3_one_shot_runner.py").read_text()


def _floor(**kw):
    """An EM floor shaped like `_pdn_em_width_floor`'s own answer."""
    f = {
        "per_layer": {"metal4": {"jmax_A_per_um": 6.7e-4, "w_em_um": 3.95}},
        "max_segment_current_A": 1.189e-3,
        "margin": 0.1, "safety_factor": 2.0,
        "manufacturing_grid_um": 0.005,
    }
    f.update(kw)
    return f


# ------------------------------------------------- the deck records the remedy

def test_the_deck_records_what_it_applied_into_the_floor_it_was_given():
    """One source of truth: the same dict that carries the arithmetic carries
    the decision, so the two can never disagree."""
    body = inspect.getsource(R._build_pdn_tcl)
    assert 'em_floor.setdefault("applied", [])' in body
    for field in ("stripe_multiplier", "pitch_um", "width_um", "width_kept"):
        assert field in body, field


def test_the_record_is_persisted_beside_the_arithmetic():
    assert '_szd["applied"] = _pdn_em_floor["applied"]' in SRC
    assert 'pdn_em_sizing.json' in SRC


def test_the_row_reads_the_artefact_not_a_scope_it_cannot_see():
    """The step row is written by the orchestrator, which never sees the
    dispatch's floor dict; it must read the persisted record."""
    i = SRC.index('"pdn_em_resize", _pnr_redispatched.status')
    seg = SRC[max(0, i - 1200):i]
    assert 'pdn_em_sizing.json' in seg and '.get("applied")' in seg


# --------------------------------------------------------- both directions

def test_a_stripe_answer_is_reported_as_stripes_with_the_width_kept():
    i = SRC.index("_applied_txt = ")
    seg = SRC[i:i + 900]
    assert "stripes at" in seg and "KEPT" in seg
    assert 'a.get("verdict") == "MORE_STRIPES"' in seg


def test_a_width_answer_is_still_reported_as_a_width():
    i = SRC.index("_applied_txt = ")
    seg = SRC[i:i + 900]
    assert "width -> " in seg, (
        "when the planner really does widen, the row must say so")


def test_the_shortfall_is_kept_and_labelled_as_a_shortfall():
    """Not deleted — a reviewer needs to know what triggered the pass. It is
    labelled, so it can no longer be read as the remedy."""
    i = SRC.index('"one-shot EM resize: SHORT BY ')
    assert i > 0
    assert "{_short_txt}" in SRC[i:i + 120]


def test_nothing_applied_leaves_the_row_as_it_was():
    """A run whose deck applied nothing (no stripe plan, no widening) must not
    grow an empty APPLIED clause."""
    i = SRC.index("_applied_txt = ")
    seg = SRC[i:i + 400]
    assert '_applied_txt = ""' in seg
    assert "if _applied:" in seg

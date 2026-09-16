"""R-0915-71 — the DECODE an incremental converter needs travels ON THE DECK.

WHY. `analog_adc_enob_corner_check` already reads the tone, the sample clock
and the graded band off the corner deck, for a reason it states: "a spec
`fclk` the deck did not honour would put the band edge somewhere the spectrum
never was". The matched incremental decode needs four more numbers of exactly
that kind — the conversion window, the loop order, the per-stage coefficient
and the loop's feedback delay — and reading them off a document the deck did
not honour is the same error one metric further along.

So the producer that renders the deck (`analog_resolution_stimulus`, the one
place in the flow that holds both the spec and the topology IR at the moment a
deck exists) stamps what it built, and the gate reads it back. `stamp` and
`read_stamp` are inverses in one file so the two sides cannot drift.

BOTH DIRECTIONS. Every field is a REFUSAL BY NAME when the design does not
declare it — never a default, because a default would be a guess about the
circuit the emitter emitted. And the control: a converter whose spec does not
declare an incremental mode is NOT stamped and NOT refused; it keeps the
free-running instrument, which is the right one for it.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_incremental_decimator as D          # noqa: E402
import analog_resolution_stimulus as S            # noqa: E402


INCREMENTAL_SPEC = {"specs": [
    {"name": "enob", "value": 14.0},
    {"name": "osr", "value": 256},
    {"name": "converter_type", "value": "incremental delta-sigma"},
]}
FREE_RUNNING_SPEC = {"specs": [
    {"name": "enob", "value": 14.0},
    {"name": "osr", "value": 256},
    {"name": "converter_type", "value": "continuously sampling delta-sigma"},
]}
IR = {
    "ports": ["vin", "bit_out"],
    "constants": {"window_clocks": 256.0, "feedback_delay_clocks": 0.0},
    "stage_expansion": {"stages": 2, "count_from": "order",
                        "coefficients": [0.25, 0.25],
                        "chain": ["vin", "vo1", "vint"]},
}


# ── the stamp is a round trip ──────────────────────────────────────────────
def test_the_stamp_reads_back_every_field_it_wrote():
    line = D.stamp(D.MODE_INCREMENTAL, 256, 2, 0.2499, 1)
    got, detail = D.read_stamp("* a comment\n" + line + "\n.end\n")
    assert got == {"mode": D.MODE_INCREMENTAL, "window_clocks": 256,
                   "order": 2, "coeff": 0.2499,
                   "feedback_delay_clocks": 1}, detail


def test_the_stamp_is_a_spice_comment():
    """It must change nothing the simulator does."""
    assert D.stamp(D.MODE_INCREMENTAL, 256, 2, 0.25, 0).lstrip().startswith("*")


def test_a_deck_with_no_stamp_refuses_by_name():
    got, detail = D.read_stamp("v1 a 0 1.2\n.tran 1n 1u\n.end\n")
    assert got is None
    assert detail["reason"] == D.NO_STAMP


def test_an_incomplete_stamp_names_the_fields_it_is_missing():
    """A half-read stamp must never be completed from a default."""
    got, detail = D.read_stamp(
        f"* {D.PRODUCER}: mode=incremental order=2\n")
    assert got is None
    assert detail["reason"] == D.BAD_STAMP
    assert set(detail["missing_or_unreadable"]) == {
        "window_clocks", "coeff", "feedback_delay_clocks"}


# ── the declaration is DERIVED from the design, and refuses by name ────────
def test_an_incremental_block_declares_a_complete_decode():
    rec = S.incremental_decode(INCREMENTAL_SPEC, IR)
    assert rec["declared"] is True, rec
    assert rec["window_clocks"] == 256
    assert rec["order"] == 2
    assert rec["coeff"] == pytest.approx(0.25)
    assert rec["feedback_delay_clocks"] == 0
    # and what it declares is exactly what a reader will read back
    assert D.read_stamp(rec["stamp"])[0]["window_clocks"] == 256


def test_the_control_a_free_running_converter_is_not_stamped_and_not_refused():
    """THE CONTROL. The FFT instrument is RIGHT for a free-running modulator;
    a converter that does not declare the incremental mode must keep it."""
    rec = S.incremental_decode(FREE_RUNNING_SPEC, IR)
    assert rec["declared"] is False
    assert rec["mode"] == D.MODE_FREE_RUNNING
    assert rec["reason"] == "converter_is_not_declared_incremental"


@pytest.mark.parametrize("drop,expected", [
    ("window_clocks", S._INC_NO_WINDOW),
    ("feedback_delay_clocks", S._INC_NO_DELAY),
])
def test_a_missing_constant_is_refused_by_name(drop, expected):
    ir = copy.deepcopy(IR)
    ir["constants"].pop(drop)
    rec = S.incremental_decode(INCREMENTAL_SPEC, ir)
    assert rec["declared"] is False
    assert rec["reason"] == expected, rec


def test_an_order_the_cascade_does_not_declare_is_refused_by_name():
    ir = copy.deepcopy(IR)
    ir["stage_expansion"]["count_from"] = "osr"
    rec = S.incremental_decode(INCREMENTAL_SPEC, ir)
    assert rec["declared"] is False
    assert rec["reason"] == S._INC_NO_ORDER


def test_unequal_cascade_coefficients_are_refused_rather_than_averaged():
    """The flat DAC term carries `a2` where the triangular term carries
    `a1*a2`, so an unequal cascade needs them separately. Taking the first is
    the kind of silent approximation this file exists to stop."""
    ir = copy.deepcopy(IR)
    ir["stage_expansion"]["coefficients"] = [0.25, 0.5]
    rec = S.incremental_decode(INCREMENTAL_SPEC, ir)
    assert rec["declared"] is False
    assert rec["reason"] == S._INC_UNEQUAL_COEFF


def test_a_coefficient_count_that_does_not_match_the_order_is_refused():
    ir = copy.deepcopy(IR)
    ir["stage_expansion"]["coefficients"] = [0.25]
    rec = S.incremental_decode(INCREMENTAL_SPEC, ir)
    assert rec["declared"] is False
    assert rec["reason"] == S._INC_NO_COEFF

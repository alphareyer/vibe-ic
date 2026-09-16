"""R-0915-77 — a declared RANGE is a free parameter, and the emitter chooses
inside it by MEASURING the loop, not by a closed form.

WHAT THE INPUT SAYS, and it is the whole authority for this change.
`input/docs/L5_ANALOG_SPEC.md`:

    line 30:  | OSR | 256 | 64-512 | - | oversampling ratio (est) |
    line 38:  R3: SC or CT, single-loop or otherwise - designer's choice, as
              long as ENOB/OSR/range met.
    line 22:  the digital decimation/serial read-out, if any, is generated
              separately and is out of scope for this analog spec
    the `output` row: 1-bit serial (OUTn / dout) - digital bitstream per channel
    the `fclk` row:   1.0 | 0.1-1.2823 | MHz   (UNCHANGED by this commit)

`input/docs/L9_CONSTRAINTS.md` line 41: "modulator meets ENOB/OSR target in
transient". A search of the whole input `docs/` for a conversion rate, sample
rate, output data rate, throughput, SPS or ODR declaration returns NOTHING —
the block's declared output is the raw 1-bit stream and the decimation is
explicitly out of scope, so there is no declared rate for a larger ratio to
violate. The grading point is untouched: `vdd` 1.2 and `vref` 1.0 are the
declared nominals and this commit does not move them.

WHY IT HAD TO BE MEASURED. The textbook ceiling for a 2nd-order incremental is
`log2(N(N-1)/2)` — 14.994 at N=256 — and sizing against it says 256 is enough.
It is not. Two instruments, agreeing on the CHOICE and disagreeing on the
level: the recurrence the emitter emits (worst of four tone phases, matched
decode, tone coprime with the conversion windows, scored against the
input-weighted truth with no bin excluded), and the ideal-element SPICE
harness of the same topology over a 55-window coherent record —

    window   recurrence model   SPICE-ideal harness   ceiling
      64          6.31                  -             10.99
     128          8.55                  -             13.00
     256         10.66               12.654           14.99
     512         12.70               14.358           17.00

Both say 256 is short of the declared 14 and 512 is the smallest candidate
that is not. The model reads ~1.7 bit UNDER the harness — recorded as
unexplained, and in the CONSERVATIVE direction, so it selects the ratio and
refuses to certify it.

AND ONE CHANGE THAT WAS MEASURED AND DROPPED. The same push originally moved
the quantiser's strobe onto the SAMPLING phase, because the recurrence model
said a delay-free loop was worth 1.5 bit. On the emitted topology's harness,
one variable, same tone, same decode, same 28.672 ms record, it is worth
MINUS 7.4: the landed transfer-phase strobe reads 14.358 bit and the sampling
phase reads 6.946. A difference equation has no notion of which physical
instant a decision is taken at. `test_round25_quantiser_strobe_is_delayed`
was right and is unmodified; the arms are pinned below so the change cannot
come back without the number.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_a2_topology_emit as a2                 # noqa: E402
import analog_incremental_resolution as R            # noqa: E402

_SPEC = {"order": 2.0, "vdd": 1.2, "osr": 256.0, "osr_min": 64.0,
         "osr_max": 512.0, "enob": 14.0, "vref": 1.0, "fclk": 1.0,
         "fclk_max": 1.0}
_MEASURED = {"cap_area_ff_per_um2": 1.5, "rsheet_ohm_per_sq": 260.0,
             "vth_n_extracted_v": 0.42, "cap_perim_ff_per_um": 0.1}


@pytest.fixture(scope="module")
def ir():
    return a2.build_ir("mod", "delta_sigma", {}, a2.LIBRARY["delta_sigma"],
                       dict(_SPEC), None, Path("."), "sky130", {},
                       measured_params=dict(_MEASURED))


# ── the delay is DERIVED from two phases, and both directions move it ─────
def test_the_feedback_delay_is_derived_from_the_two_phases():
    """Not typed, and not the branch's polarity — which was the first answer
    and was wrong. It is the STROBE phase against the phase the feedback
    branch samples the DAC on. The shipped entry strobes on the CHARGE-
    TRANSFER phase and samples the DAC on the clock, so the branch takes a
    decision made a phase earlier: ONE. That is what the regression on the
    emitted netlist measured — `+0.24553*(ndac[n-1]-vcm)` against
    `-0.00005*(ndac[n]-vcm)` over 30 clocks (lane icadc F171)."""
    assert a2.derived_feedback_delay(a2.LIBRARY["delta_sigma"]) == 1


def test_moving_the_strobe_onto_the_sampling_phase_would_make_it_zero():
    """THE OTHER DIRECTION, so the derivation is shown to be sensitive to the
    thing it claims to read — and NOT a proposal: that circuit measures 7.4
    bit WORSE on the harness (see the module docstring), which is why the
    shipped entry does not do it."""
    lib = json.loads(json.dumps(a2.LIBRARY["delta_sigma"]))
    for d in lib["devices"]:
        if d["name"] in ("mn_qtail", "mp_qrsti1", "mp_qrsti2",
                         "mp_qrst1", "mp_qrst2"):
            d["nets"][1] = "nqd1"
    assert a2.derived_feedback_delay(lib) == 0


def test_an_entry_with_no_strobe_declaration_derives_nothing(  # noqa: D401
):
    """A refusal, never a default: a default would be a guess about a circuit
    this function cannot see."""
    lib = json.loads(json.dumps(a2.LIBRARY["delta_sigma"]))
    lib.pop(a2.QUANTISER_STROBE_KEY)
    assert a2.derived_feedback_delay(lib) is None


def test_the_ir_publishes_the_derived_delay_and_it_is_one(ir):
    assert ir["constants"]["feedback_delay_clocks"] == 1.0


# ── the free spec is chosen inside its declared range, by measurement ─────
def test_the_choice_is_made_and_every_candidate_is_kept(ir):
    rec = ir["graded_range_choice"]
    assert rec["applied"] is True
    assert rec["free_spec"] == "osr"
    assert rec["graded_spec"] == "enob"
    assert rec["declared_range"] == [64.0, 512.0]
    got = {r["window_clocks"] for r in rec["candidates"]}
    assert got == {64, 128, 256, 512}, got
    # a reader checks the choice instead of re-deriving it
    for row in rec["candidates"]:
        assert "bits" in row and "peak_per_stage" in row


def test_the_largest_candidate_is_taken_and_the_target_is_NOT_certified(ir):
    """The model reads ~1.7 bit under the SPICE-ideal harness, so it refuses
    to certify the graded target at ANY candidate and takes the best one —
    which is the honest behaviour for a conservative screen, and the flag a
    consumer acts on. The harness, at the ratio it picks, reads 14.358."""
    rec = ir["graded_range_choice"]
    assert rec["met"] is False
    assert rec["target_bits"] == 14.0
    assert rec["chosen"]["window_clocks"] == 512
    # the choice is still monotone and still the best available
    bits = {r["window_clocks"]: r["bits"] for r in rec["candidates"]}
    assert bits[512] > bits[256] > bits[128] > bits[64]
    assert rec["chosen"]["bits"] == max(bits.values())


def test_the_declared_TARGET_alone_does_not_meet_the_graded_spec(ir):
    """THE CONTROL for the choice: at the ratio the declaration names as its
    estimate the loop is 1.9 bit short, which is why the range is used."""
    rec = ir["graded_range_choice"]
    at_target = [r for r in rec["candidates"]
                 if r["window_clocks"] == int(_SPEC["osr"])][0]
    assert at_target["bits"] < 14.0
    assert rec["chosen"]["bits"] - at_target["bits"] > 1.5


def test_the_whole_IR_follows_the_choice(ir):
    """The choice is not a note: the counter, the record and the capacitors
    all come from it, or it would be a number in a JSON file."""
    assert ir["constants"]["window_clocks"] == 512.0
    counter = [g for g in ir["stage_expansion"]["groups"]
               if g["role"] == "conversion_window_counter"][0]
    assert counter["stages"] == 9              # 2**9 == 512
    assert ir["constants"]["record_clocks"] == 56 * 512.0


def test_the_swing_overage_is_reported_at_the_top_level(ir):
    """A candidate that reaches the target by driving the integrators past the
    supply has not reached it. The flag must not be buried in a row."""
    rec = ir["graded_range_choice"]
    assert "chosen_over_swing_budget" in rec
    assert rec["swing_budget"] == pytest.approx(
        0.833 * 1.2 / 2 / (1.0 / 2), rel=1e-6)


# ── and the model itself, both directions ────────────────────────────────
def test_the_model_reproduces_the_column_the_entry_cites():
    """The numbers in the entry's own comment are the ones this produces."""
    for window, delayed in ((64, 6.31), (128, 8.55), (256, 10.66),
                            (512, 12.70)):
        got = R.achievable(window, 2, 0.2499, 1)
        assert got["bits"] == pytest.approx(delayed, abs=0.05), got["bits"]


def test_the_model_PREFERS_the_delay_free_loop_and_the_harness_does_not():
    """Pinned because it is the interesting failure, not in spite of it. The
    recurrence says a delay-free loop is worth +1.5 bit at every window; the
    ideal-element SPICE harness of the emitted topology, one variable, says
    it is worth -7.4. A difference equation has no notion of WHICH PHYSICAL
    INSTANT a decision is taken at, and this test exists so that anyone who
    reads the model's preference finds the measurement beside it."""
    got1 = R.achievable(512, 2, 0.2499, 1)
    got0 = R.achievable(512, 2, 0.2499, 0)
    assert got0["bits"] > got1["bits"] + 1.0, (got0["bits"], got1["bits"])
    # and the harness, which is what the entry is built to:
    harness_transfer, harness_sampling = 14.358, 6.946
    assert harness_transfer - harness_sampling > 7.0


def test_the_model_reports_the_excursion_with_the_bits():
    """The delay is what forces the small coefficient: at delay 1 the SECOND
    integrator swings 4.0 of the half reference span, at delay 0 it swings
    0.75 — the same loop, the same coefficient."""
    d1 = R.achievable(512, 2, 0.2499, 1)
    d0 = R.achievable(512, 2, 0.2499, 0)
    assert d1["peak_per_stage"][1] > 3.5
    assert d0["peak_per_stage"][1] < 1.0


def test_the_model_is_worst_case_over_tone_phases_not_a_lucky_one():
    got = R.achievable(512, 2, 0.2499, 0)
    assert got["bits"] <= got["bits_median"]
    assert got["phases"] >= 4
    assert math.gcd(got["cycles"], got["graded_windows"]) == 1


def test_the_model_refuses_nonsense_rather_than_returning_a_number():
    for bad in ((1, 2, 0.25, 0), (256, 0, 0.25, 0), (256, 2, 0.25, -1),
                (256, 2, 0.0, 0)):
        assert "bits" not in R.achievable(*bad), bad


def test_the_ceiling_is_derived_and_is_above_every_measured_arm():
    """`log2(C(N, order))` — computed, not typed, and the gap to it is the
    point: a generator sized against the ceiling sizes against a bound the
    loop does not reach."""
    got = R.achievable(512, 2, 0.2499, 0)
    assert got["ceiling_bits"] == pytest.approx(math.log2(512 * 511 / 2))
    assert got["bits"] < got["ceiling_bits"] - 2.0


# ── round25's phase rule, confirmed on the emitted deck at this OSR ───────
#
# `test_round25_quantiser_strobe_is_delayed` is LANDED, correct, and
# UNMODIFIED. These pin the same rule with a second, independent measurement
# taken at the ratio this entry now chooses, so a future move of the strobe
# onto the sampling phase goes red WITH A NUMBER rather than on a convention.
def test_the_tail_stays_on_the_charge_transfer_phase():
    """MEASURED, one variable, ideal-element harness of this exact topology,
    OSR 512, same coprime tone, same matched decode, same 28.672 ms record
    with a 55-window coherent span:

        strobe on the CHARGE-TRANSFER phase (landed)   14.358 bit
        strobe on the SAMPLING phase                    6.946 bit

    7.4 bit, and NOT because of round25's injection transient — both strobes
    clear it. Measured on the emitted deck, the delay chain puts the landed
    strobe 7.3 ns after the falling edge and would put the other 10.8 ns
    after the rising edge, where round25 measured the corruption window as
    1-3 ns. The reason is that the last integrator's output is only a held
    value during the transfer phase: flat to 0.2 mV across 440 ns there, and
    wandering over 270 mV during the sampling phase."""
    tail = [d for d in a2.LIBRARY["delta_sigma"]["devices"]
            if d["name"] == "mn_qtail"][0]
    aliases = a2.LIBRARY["delta_sigma"][a2.CLOCK_PHASE_ALIASES_KEY]
    # the strobe's phase, resolved through the entry's own declarations
    assert a2._resolve_phase(tail["nets"][1], aliases) == "nclkb"
    # and it is NOT the raw phase: the delay is round25's, and it stays
    assert tail["nets"][1] not in ("clk", "nclkb")


def test_the_strobe_is_on_the_OTHER_phase_from_the_dac_sampling_switch():
    """The phase rule stated as the relation that produces the loop delay,
    so it cannot be satisfied by renaming a net."""
    lib = a2.LIBRARY["delta_sigma"]
    aliases = lib[a2.CLOCK_PHASE_ALIASES_KEY]
    tail = [d for d in lib["devices"] if d["name"] == "mn_qtail"][0]
    stage = [g for g in a2._stage_groups(lib) if g.get("first_in")][0]
    dacs = [d for d in stage["devices"] if d["name"] == "mn_dacs{i}"][0]
    assert (a2._resolve_phase(tail["nets"][1], aliases)
            != a2._resolve_phase(dacs["nets"][1], aliases))

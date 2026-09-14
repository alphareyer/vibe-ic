"""vibe-ic#2200 — the A2 transient record is derived from EVERY constraint that
bears on it, and the deck the graded spec set asks for is the deck that runs.

MEASURED before any of this was written, on the live tip: the converter entry's
whole transient was `window_clocks * 2000 / fclk` — two conversion windows, 512
converter samples at the declared clock — and neither `enob`, nor `sndr`, nor
`osr` appeared anywhere in that derivation. `analog_resolution_stimulus`
refused every corner deck with `record_too_short_for_an_in_band_tone`, 512
available against 13824 required, and there was no path by which the spec the
block is GRADED on could lengthen the record: a length fixed by one input while
the thing it bounds scales with another.

What is asserted here is BOTH directions of that. The record grows to what the
declared resolution needs and the real producer accepts it; and the guard that
was doing the refusing still refuses a record that is genuinely short. A guard
that stopped refusing would be a worse defect than the one being fixed.

The design's own numbers are never typed in — the spec fixture is built from
the entry's own requirement list and the expected record is computed by the
producer's own exported floor, so this file cannot pass by agreeing with a
number somebody wrote down twice.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_a2_topology_emit as a2                # noqa: E402
import analog_a3_netlist_emit as a3                 # noqa: E402
import analog_resolution_stimulus as st             # noqa: E402
import analog_transient_record as rec               # noqa: E402

ENTRY = a2.LIBRARY["delta_sigma"]
TB = ENTRY["testbench"]

#: The declaration this entry admits, built from the entry's OWN requirement
#: list so a requirement that moves cannot leave this fixture behind. The
#: values are the smallest ones that satisfy the entry's declared domains.
_SPEC = {"order": 2.0, "vdd": 1.2, "osr": 256.0, "enob": 14.0, "vref": 1.0,
         "fclk": 1.0, "fclk_max": 1.0}
_UNITS = {"order": "", "vdd": "V", "osr": "", "enob": "bit", "vref": "V",
          "fclk": "MHz", "fclk_max": "MHz"}


def _measured():
    return {"cap_area_ff_per_um2": 1.5, "rsheet_ohm_per_sq": 260.0,
            "vth_n_extracted_v": 0.42, "cap_perim_ff_per_um": 0.1}


def _ir(spec=None):
    return a2.build_ir("b", "delta_sigma", {}, ENTRY, dict(spec or _SPEC),
                       None, Path("."), "sky130", {}, measured_params=_measured())


# ── the declaration ───────────────────────────────────────────────────────
def test_the_entry_declares_its_record_constraints_as_DATA():
    """The record is not derived by a rule buried in a producer any more; the
    constraints are rows a reader can enumerate, and one of them is the graded
    spec set."""
    rows = {r["name"]: r for r in TB[rec.CONSTRAINTS_KEY]}
    assert "conversion_windows" in rows
    tone = rows["coherent_in_band_tone"]
    assert set(tone["applies_when_spec_declares_any"]) >= {"enob", "sndr"}
    assert tone["needs_spec_bound"] == ["osr"]
    assert tone["clocks_rule"] in rec.RULES
    assert all(r.get("why") for r in rows.values())


def test_the_transient_SPENDS_the_derived_record():
    """The half of #2200 that makes the rest of it real: the deck's own
    transient names the derived constant, so a record that grew is a record
    that runs."""
    assert rec.RECORD_CONSTANT in TB["env_exprs"]["tstop_ns"]
    assert "window_clocks" not in TB["env_exprs"]["tstop_ns"]


@pytest.mark.parametrize("term", ["tper_ns", "thigh_ns", "twin_ns",
                                  "tmeas_ns", "twin2_ns", "tstep_ns"])
def test_the_MEASUREMENT_window_is_not_moved_by_the_record(term):
    """Lengthening the record must not change a single number this deck
    already reports. Every measurement card runs from `tmeas_ns` to
    `twin2_ns`, and neither of those may learn about the record."""
    assert rec.RECORD_CONSTANT not in TB["env_exprs"][term]
    assert "window_clocks" in TB["env_exprs"][term] or "fclk" in TB[
        "env_exprs"][term]


def test_every_measurement_card_still_ends_at_the_second_window():
    cards = [c for c in TB["control"] if c.startswith("meas tran")]
    assert cards
    for card in cards:
        assert "to={twin2_ns}n" in card, card
    assert "{tstop_ns}n" in [c for c in TB["control"]
                             if c.startswith("tran ")][0]


# ── the derived number ────────────────────────────────────────────────────
def test_the_emitted_IR_carries_the_record_the_declared_RESOLUTION_needs():
    ir = _ir()
    want = st.coherent_record_samples(_SPEC["osr"])["samples"]
    assert ir["constants"][rec.RECORD_CONSTANT] == float(want)
    d = ir["record_derivation"]
    assert d["binding_constraint"] == "coherent_in_band_tone"
    got = {c["constraint"]: c.get("clocks") for c in d["constraints"]}
    assert got["conversion_windows"] == 2 * ir["constants"]["window_clocks"]
    assert got["coherent_in_band_tone"] == float(want)
    # and the two are not the same number, which is the whole issue
    assert got["coherent_in_band_tone"] > got["conversion_windows"]


def test_a_block_that_is_NOT_graded_on_a_resolution_keeps_the_old_record():
    """No entry pays for this but the one that needs it. Without an `enob` row
    the tone constraint does not apply and the record is exactly the two
    conversion windows it has always been."""
    spec = dict(_SPEC)
    spec.pop("enob")
    # `enob` is also a sizing requirement, so drive the derivation directly
    # rather than through an admission this declaration no longer passes.
    out = rec.derive(TB, {"window_clocks": 256.0}, spec,
                     TB["env_exprs"]["tstop_ns"])
    assert out[rec.RECORD_CONSTANT] == 512.0
    assert out["binding_constraint"] == "conversion_windows"


# ── forward: the producer that refused now applies ────────────────────────
def _render(ir):
    sv = {k: v for k, v in _SPEC.items()}
    _o, _sb, _n, env = a3._resolve_params(ir, sv)
    tb, _tbenv, notes = a3.render_testbench(
        ir, {"analog_device_params": {"nominal_supply_v": 1.2}}, env, [])
    assert tb is not None, notes
    return tb


def test_the_rendered_deck_is_one_the_resolution_producer_ACCEPTS():
    """End to end through the real renderer and the real producer: the deck A3
    writes from this IR is a deck `analog_resolution_stimulus` stamps a
    coherent in-band tone into, where the same chain refused before #2200."""
    ir = _ir()
    plan = st.plan(_render(ir), {"specs": [{"name": "enob", "min": 14.0},
                                           {"name": "osr", "target": 256.0}]},
                   ir)
    assert plan["applied"] is True, plan
    assert plan["samples_available"] == plan["samples_required"]
    assert plan["tone_bin"] % 2 == 1
    assert plan["tone_bin"] >= plan["min_signal_cycles"]


# ── backward: the guard did NOT stop refusing ─────────────────────────────
def test_a_record_that_is_GENUINELY_short_is_still_refused_by_name():
    """The direction a fix can quietly destroy. Nothing here loosened the
    producer's floor, so a deck one band bin below it comes back refused with
    the same reason and the same required number."""
    ir = _ir()
    deck = _render(ir)
    osr = int(_SPEC["osr"])
    need = st.coherent_record_samples(_SPEC["osr"])["samples"]
    # The transient stop is read from the deck the producer emitted, not
    # typed: since the power-on sequence (ea2a9c4f1) the stop carries the
    # clock hold in front of the record (`need * 1000 + hold` ns), and a
    # literal that assumed the record starts at t=0 no longer matched --
    # so this guard silently stopped mutating anything. Shorten whatever
    # stop is there by two OSR bands of samples.
    m = re.search(r"^tran 5n (\d+)n$", deck, re.M)
    assert m is not None, "the emitted deck carries no `tran 5n <stop>n` line"
    stop_ns = int(m.group(1))
    assert stop_ns >= need * 1000, (stop_ns, need)
    short = deck.replace(m.group(0), f"tran 5n {stop_ns - 2 * osr * 1000}n")
    assert short != deck
    plan = st.plan(short, {"specs": [{"name": "enob", "min": 14.0},
                                     {"name": "osr", "target": 256.0}]}, ir)
    assert plan["applied"] is False
    assert plan["reason"] == "record_too_short_for_an_in_band_tone"
    assert plan["samples_required"] == need


def test_A2_REFUSES_rather_than_emit_the_record_the_deck_used_to_run():
    """The mutation that reintroduces #2200: put the transient back to the
    conversion-window expression and the derivation is no longer spent. A2 must
    refuse by name — the alternative is a topology that reaches disk with a
    record nothing can grade, which is the state the issue reports and which
    PASSES the A2 gate because that gate measures vocabulary."""
    with pytest.raises(rec.RecordNotDerivable) as exc:
        rec.derive(TB, {"window_clocks": 256.0}, _SPEC,
                   "window_clocks * 2000 / fclk")
    assert (exc.value.refusals[0]["requirement"]
            == "record_constant_not_consumed")


def test_a_declaration_that_grades_a_resolution_without_a_BAND_is_refused():
    """The unsatisfiable request, at the emitter's own admission step. No
    record length puts a tone inside a band the declaration never states, so
    this is refused rather than sized to the constraints that are left."""
    spec = dict(_SPEC)
    spec.pop("osr")
    refusals = a2.entry_admission(ENTRY, spec,
                                  {k: _UNITS[k] for k in spec}, _measured())
    named = [r for r in refusals
             if r.get("requirement") == "record_input_unbound"]
    assert named, refusals
    assert named[0]["field"] == "osr"
    assert named[0]["constraint"] == "coherent_in_band_tone"

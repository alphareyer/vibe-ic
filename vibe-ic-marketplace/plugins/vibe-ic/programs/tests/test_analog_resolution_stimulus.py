"""test_analog_resolution_stimulus.py — the A4 corner deck's resolution
stimulus (vibe-ic#2188).

The defect this covers, MEASURED on the live tip against a real front-door run
of a second-order incremental modulator: nine corners really simulated and
`analog_adc_enob_corner_check` UNMEASURED on every one of them, because no
producer in the A-track had ever written a `wrdata` into a corner deck or
driven a converter input from anything but a DC level. The gate named both
missing properties correctly; the capability did not exist.

Both directions are proved here, and separately: a deck that gains the tone and
the dump, and every named refusal that must fire INSTEAD of a half-stamped
deck. The mutation arms are the ones that would let a wrong answer through
quietly — a tone that overdrives the converter, a probe on an internal node, a
cycle floor that has drifted away from the consumer's.

Block, port and net names are synthetic. No chip / SKU / foundry literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_adc_enob_corner_check as consumer   # noqa: E402
import analog_resolution_stimulus as ars          # noqa: E402


# ── fixtures ───────────────────────────────────────────────────────────────
def _deck(*, stop_ns=512000, clk_per_ns=1000, dc="0.9",
          meas=("meas tran vavg avg v(dout)",), extra_src=()):
    """A corner deck of the shape A4 writes: an inlined block netlist, the
    top-level sources that excite it, the DUT instance, and a `.control` with
    one transient and the measurements the design declared."""
    lines = [
        "* synthetic corner deck",
        ".subckt conv vdd vss ain vrefp vrefn clk dout",
        "r1 ain dout 1k",
        "c1 dout vss 1p",
        ".ends conv",
        "v_vdd vdd 0 1.8",
        "v_vrefp vrefp 0 1.4",
        "v_vrefn vrefn 0 0.4",
        f"v_clk clk 0 pulse(0 1.8 0n 1n 1n 499n {clk_per_ns}n)",
        f"v_ain ain 0 {dc}",
        *extra_src,
        "xdut vdd 0 ain vrefp vrefn clk dout conv",
        ".control",
        f"tran 5n {stop_ns}n",
        *meas,
        ".endc",
        ".end",
    ]
    return "\n".join(lines) + "\n"


def _spec(enob=10.0, osr=8.0):
    rows = [{"name": "enob", "min": enob}]
    if osr is not None:
        rows.append({"name": "osr", "target": osr})
    return {"block": "conv0", "specs": rows}


def _topo(chain=("ain", "n1"), ports=("vdd", "vss", "ain", "vrefp", "vrefn",
                                      "clk", "dout"), **kw):
    ir = {"ports": list(ports)}
    if chain is not None:
        ir["stage_expansion"] = {"chain": list(chain)}
    ir.update(kw)
    return ir


# ── the applied direction ──────────────────────────────────────────────────
def test_applied_deck_carries_the_tone_and_the_dump():
    """The deck the consumer would refuse becomes the deck it can grade — read
    through the CONSUMER's own grammar, so this cannot pass while the two
    disagree about what a tone or a dump looks like."""
    deck = _deck()
    assert consumer._SIN_RE.search(deck) is None
    assert consumer._WRDATA_RE.search(deck) is None

    out, rec = ars.apply(deck, _spec(), _topo(), "c.resolution.wrdata")
    assert rec["applied"] is True, rec
    sin = consumer._SIN_RE.search(out)
    wrd = consumer._WRDATA_RE.search(out)
    assert sin is not None, out
    assert wrd is not None, out
    assert wrd.group(1) == "c.resolution.wrdata"
    assert wrd.group(2) == "v(dout)"          # the node the deck itself grades


def test_the_tone_is_the_arithmetic_the_record_allows():
    """512 converter samples at OSR 8 -> a 32-bin band -> the highest ODD bin
    at or below a third of it, so the 2nd and 3rd harmonics stay inside the
    band the SNDR is taken over."""
    out, rec = ars.apply(_deck(), _spec(osr=8.0), _topo(), "c.wrdata")
    assert rec["samples_available"] == 512
    assert rec["band_bins"] == 32
    assert rec["tone_bin"] == 9 and rec["tone_bin"] % 2 == 1
    assert rec["tone_bin"] * ars.HARMONICS_IN_BAND <= rec["band_bins"]
    assert abs(rec["tone_hz"] - 9 * 1e6 / 512) < 1e-3
    assert f"{9 * 1e6 / 512:.10g}" in out


def test_the_dc_level_the_deck_already_measured_is_preserved():
    """The tone is CENTRED on the level the design held the input at, so every
    measurement the delivered deck already takes over that input keeps its
    expected value. Asserted on the emitted card, not on the record."""
    out, rec = ars.apply(_deck(dc="0.9"), _spec(), _topo(), "c.wrdata")
    sin = consumer._SIN_RE.search(out)
    assert float(sin.group(1)) == 0.9 == rec["dc_offset_v"]


def test_the_tone_never_reaches_the_nearest_other_declared_level():
    """MUTATION ARM. An amplitude that touched the reference would clip the
    converter and the resolution measured would be of this file's own
    distortion. The nearest other DC level the deck drives on the DUT is
    `vrefp` at 1.4 V; the peak must stay strictly under it."""
    _out, rec = ars.apply(_deck(dc="0.9"), _spec(), _topo(), "c.wrdata")
    peak = rec["dc_offset_v"] + rec["amplitude_v"]
    trough = rec["dc_offset_v"] - rec["amplitude_v"]
    assert peak < 1.4, rec
    assert trough > 0.4, rec
    assert abs(rec["amplitude_v"] - ars.AMPLITUDE_MARGIN * 0.5) < 1e-12


def test_the_output_node_is_the_one_the_deck_measures_not_a_probe():
    """MUTATION ARM. A real corner deck carries a hundred hierarchical rail
    probes beside its one declared output; exporting one of those would dump a
    node that is not the converter's answer, and nothing downstream could see
    it. Only a TOP-LEVEL measured node may be the output."""
    probes = ["meas tran vavg avg v(dout)",
              "meas tran railx_max_vint max v(xdut.vint)",
              "meas tran railx_min_ndac min v(xdut.ndac)"]
    _out, rec = ars.apply(_deck(meas=probes), _spec(), _topo(), "c.wrdata")
    assert rec["applied"] is True
    assert rec["output_node"] == "dout"


# ── the refusing direction ─────────────────────────────────────────────────
def _refuses(deck, spec, topo, reason):
    out, rec = ars.apply(deck, spec, topo, "c.wrdata")
    assert rec["applied"] is False, rec
    assert rec["reason"] == reason, rec
    # A refusal never half-edits a deck.
    assert out == deck
    return rec


def test_record_too_short_refuses_with_the_arithmetic():
    """The shape the real design is in: 512 converter samples against a
    declared OSR of 256. The refusal carries BOTH numbers, because "no
    transient dump" would send the reader to the wrong producer."""
    rec = _refuses(_deck(), _spec(osr=256.0), _topo(),
                   "record_too_short_for_an_in_band_tone")
    assert rec["samples_available"] == 512
    assert rec["samples_required"] == 13824
    assert rec["band_bins"] == 1
    assert "512" in rec["detail"] and "13824" in rec["detail"]


def test_the_published_record_length_is_the_ACTUAL_threshold():
    """MUTATION ARM, and it caught a real one.

    `samples_required` is a number a reader acts on: they lengthen the deck's
    conversion window to it. So it has to be the record that WORKS, not a
    convenient lower bound — the tone bin is always ODD, so 6 * OSR * 8 is
    short by one bin and a reader who lengthened to exactly it would get the
    same refusal back. Proved BOTH ways against the producer itself: at the
    published length the tone is emitted, one band-bin below it is not.
    """
    osr = 256.0
    rec = ars.plan(_deck(), _spec(osr=osr), _topo())
    need = rec["samples_required"]
    assert rec["applied"] is False
    assert need == 2 * osr * ars.HARMONICS_IN_BAND * rec["min_tone_bin"]
    assert rec["min_tone_bin"] % 2 == 1
    assert rec["min_tone_bin"] >= consumer._MIN_SIGNAL_CYCLES

    def at(samples):
        # 1 converter sample per clock period; the deck's clock is 1000 ns.
        return ars.plan(_deck(stop_ns=int(samples) * 1000),
                        _spec(osr=osr), _topo())

    assert at(need)["applied"] is True, at(need)
    one_bin_less = need - 2 * osr * ars.HARMONICS_IN_BAND
    assert at(one_bin_less)["applied"] is False, at(one_bin_less)


def test_block_that_declares_no_resolution_target_is_untouched():
    """Not every analog block is a converter. One that declares no ENOB/SNDR
    is not this transform's business, and its deck must come back byte-equal."""
    _refuses(_deck(), {"specs": [{"name": "vout", "target": 1.2}]}, _topo(),
             "block_declares_no_resolution_target")


def test_signal_input_port_not_declared_refuses():
    """Two references and a supply are DC-driven ports of a converter too.
    With nothing in the design saying which port carries the signal, a guess
    would drive a tone onto the reference — which would not fail, it would
    silently measure nonsense."""
    rec = _refuses(_deck(), _spec(), _topo(chain=None),
                   "signal_input_port_not_declared")
    assert "signal_input_port" in rec["detail"]


def test_signal_input_port_declared_outright_wins():
    """A converter whose input is not the head of a signal chain says so in the
    testbench IR, and that declaration binds first."""
    topo = _topo(chain=("vrefp",), testbench={"signal_input_port": "ain"})
    _out, rec = ars.apply(_deck(), _spec(), topo, "c.wrdata")
    assert rec["applied"] is True
    assert rec["signal_input_port"] == "ain"


def test_signal_chain_that_is_not_a_port_refuses():
    rec = _refuses(_deck(), _spec(), _topo(chain=("vint", "n1")),
                   "signal_input_port_not_declared")
    assert "vint" in rec["detail"]


def test_output_node_ambiguous_refuses():
    rec = _refuses(_deck(meas=("meas tran a avg v(dout)",
                               "meas tran b avg v(vrefp)")),
                   _spec(), _topo(), "output_node_ambiguous")
    assert "dout" in rec["detail"] and "vrefp" in rec["detail"]


def test_output_node_not_measured_refuses():
    _refuses(_deck(meas=("meas tran a max v(xdut.vint)",)), _spec(), _topo(),
             "output_node_not_measured")


def test_sample_clock_not_identifiable_refuses():
    """Two pulse sources on the DUT: which one the converter samples at is not
    this file's to decide, and the band edge depends on it."""
    extra = ["v_clk2 vrefn 0 pulse(0 1.8 0n 1n 1n 99n 200n)"]
    _refuses(_deck(extra_src=extra), _spec(), _topo(),
             "sample_clock_not_identifiable")


def test_no_transient_refuses():
    deck = _deck().replace("tran 5n 512000n", "op")
    _refuses(deck, _spec(), _topo(), "no_transient")


def test_signal_input_source_absent_refuses():
    """The declared port exists and nothing drives it: this step re-stamps the
    source the design put there and does not author one."""
    deck = _deck().replace("v_ain ain 0 0.9\n", "")
    _refuses(deck, _spec(), _topo(), "signal_input_source_absent")


def test_a_deck_that_already_dumps_is_not_stamped_twice():
    out, _rec = ars.apply(_deck(), _spec(), _topo(), "c.wrdata")
    _refuses(out, _spec(), _topo(), "deck_already_carries_a_transient_dump")


# ── the floor is the consumer's ────────────────────────────────────────────
def test_the_cycle_floor_is_the_consumer_s_own():
    """MUTATION ARM. The producer must never emit a tone the consumer will
    refuse as too few cycles. Restating the number here instead of importing it
    is exactly how the two would drift, so the test asserts they are the SAME
    object, not that they happen to be equal today."""
    assert ars._MIN_SIGNAL_CYCLES is consumer._MIN_SIGNAL_CYCLES
    _out, rec = ars.apply(_deck(), _spec(osr=8.0), _topo(), "c.wrdata")
    assert rec["tone_bin"] >= consumer._MIN_SIGNAL_CYCLES

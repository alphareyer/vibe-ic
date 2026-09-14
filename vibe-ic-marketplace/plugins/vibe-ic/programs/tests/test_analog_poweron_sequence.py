"""test_analog_poweron_sequence.py — bringing a CLOCKED analog block up
through a power-on sequence instead of demanding a DC operating point at full
rail (lane icadc, 2026-09-15).

THE DEFECT THIS COVERS, MEASURED ON REAL SILICON-LEVEL EVIDENCE.
`analog_a3_netlist_emit` rendered a clocked block's testbench with the supply
already at its final value at t=0 and the clock already toggling from t=0, so
ngspice had to SOLVE a DC operating point for a bias the clocked loop itself
establishes.  On the nine-corner set a converter's own input declares
(TT/SS/FF x -40/27/125 C), corner SS/-40 C, second-order incremental
modulator, inside the pinned image:

    CONTROL (deck as emitted)   all five ngspice homotopies failed —
        "Warning: Dynamic gmin stepping failed", true gmin, source stepping,
        transient op and gshunt stepping all failed;
        "doAnalyses: TRAN:  Timestep too small; initial timepoint: trouble
        with node \"xdut.nbias\"" ; "tran simulation(s) aborted" ; rc=1 in 20.7 s.
    FIXED (same deck, power-on sequence applied, nothing else changed)
        "Note: Dynamic gmin stepping completed" ; "Initial Transient
        Solution" ; the transient advances.

The two decks differ by FIVE cards: the supply source, the clock source, the
transient stop time and the three measurement windows.  Same netlist, same
corner section, same temperature, same `.options`, same models, same image.

BOTH DIRECTIONS ARE PROVED HERE, AND SEPARATELY.  The deck that must gain the
sequence gains it; every deck that must NOT be touched is returned BYTE FOR
BYTE with the refusal named.  The mutation arms are the ones that would let a
wrong answer through quietly: a supply that ends at a different value (every
`vavg / supply` in the deck would then read a different density), a window
that moves while the stop time does not (the same measurement would cover
different samples and still be called the same measurement), and a transform
that applies twice (the windows would walk).

Block, port and net names are synthetic.  No chip / SKU / foundry literal.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_poweron_sequence as po              # noqa: E402
import analog_a3_netlist_emit as a3               # noqa: E402

RAIL = "vdd"


# ── fixtures ───────────────────────────────────────────────────────────────
def _clocked_transient_deck(*, supply="1.2", per_ns=1000, stop_ns=13824000,
                            meas_from_ns=261120, meas_to_ns=512000):
    """A deck of exactly the shape A3 renders for a clocked block: the block
    inlined, the top-level sources that excite it, the DUT instance, and a
    `.control` with one transient and the measurements the design declared."""
    return "\n".join([
        "* tb_conv — stimulus for the A3 block netlist",
        "* condition: supply = %s V" % supply,
        ".include conv.sp",
        f"v_vdd {RAIL} 0 {supply}",
        "v_vrefp vrefp 0 1.1",
        "v_vrefn vrefn 0 0.1",
        f"v_clk clk 0 pulse(0 {supply} 0n 1n 1n {per_ns // 2 - 1}n {per_ns}n)",
        "v_in vin 0 0.7",
        f"xdut {RAIL} 0 vin vrefp vrefn clk bit_out conv",
        ".options trtol=1",
        ".control",
        f"tran 5n {stop_ns}n",
        f"meas tran vavg avg v(bit_out) from={meas_from_ns}n to={meas_to_ns}n",
        f"meas tran vmax max v(bit_out) from={meas_from_ns}n to={meas_to_ns}n",
        f"let dens = vavg / {supply}",
        ".endc",
        ".end",
        "",
    ])


def _op_only_deck():
    """A regulator-shaped deck: no transient at all."""
    return "\n".join([
        "* tb_reg — stimulus for the A3 block netlist",
        ".include reg.sp",
        f"v_vdd {RAIL} 0 1.8",
        "v_ref ref 0 0.6",
        f"xdut {RAIL} 0 ref vout reg",
        ".control",
        "op",
        "print v(vout)",
        ".endc",
        ".end",
        "",
    ])


def _unclocked_transient_deck():
    """A transient deck with no clock: nothing drives a clocked loop, so there
    is no bring-up to sequence and the stimulus must not be rewritten."""
    return "\n".join([
        "* tb_amp — stimulus for the A3 block netlist",
        ".include amp.sp",
        f"v_vdd {RAIL} 0 1.8",
        "v_in vin 0 sin(0.9 0.1 1k)",
        f"xdut {RAIL} 0 vin vout amp",
        ".control",
        "tran 1n 1000n",
        "meas tran vmax max v(vout) from=100n to=1000n",
        ".endc",
        ".end",
        "",
    ])


def _card(deck, head):
    for ln in deck.splitlines():
        if ln.split() and ln.split()[0] == head:
            return ln
    return ""


def _apply_ok(deck):
    out, rec = po.apply(deck, RAIL)
    assert rec["applied"], rec
    return out


def _windows_ns(deck):
    return [float(v) for v, _ in re.findall(r"(?i)\b(?:from|to)=([0-9.]+)(n)\b",
                                            deck)]


# ── the deck that MUST gain the sequence ───────────────────────────────────
def test_a_clocked_transient_deck_is_brought_up_through_a_power_on():
    """THE DEFECT ARM. Without the transform the rail is at full value at t=0
    and the clock is already toggling, which is the shape that exhausted every
    ngspice homotopy at the slow/cold corner."""
    before = _clocked_transient_deck()
    after, rec = po.apply(before, RAIL)
    assert rec["applied"] is True, rec
    assert _card(after, "v_vdd") == "v_vdd vdd 0 pwl(0n 0 100n 1.2 13825000n 1.2)"
    assert _card(after, "v_clk") == \
        "v_clk clk 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)"
    assert _card(after, "tran") == "tran 5n 13825000n"


def test_the_ramp_and_the_delay_are_derived_from_the_decks_own_clock():
    """NOT a wall-clock constant. A block clocked ten times faster ramps ten
    times faster and waits ten times less."""
    fast, rec = po.apply(_clocked_transient_deck(per_ns=100, stop_ns=1382400,
                                                 meas_from_ns=26112,
                                                 meas_to_ns=51200), RAIL)
    # `math.isclose`, not `==`: the period is read out of a `100n` token and
    # the products below are exact in decimal, not in binary.
    assert math.isclose(rec["ramp_s"], 1e-08, rel_tol=1e-12), rec
    assert math.isclose(rec["clock_delay_s"], 1e-07, rel_tol=1e-12), rec
    assert _card(fast, "v_vdd") == \
        "v_vdd vdd 0 pwl(0n 0 10n 1.2 1382500n 1.2)"
    assert _card(fast, "v_clk") == "v_clk clk 0 pulse(0 1.2 100n 1n 1n 49n 100n)"


def test_the_record_still_holds_the_same_number_of_converter_samples():
    """The stop time moves out by exactly the delay, so the record after the
    first clock edge is the record the deck had before. A transform that
    lengthened or shortened it would change the resolution the record can
    support without saying so."""
    _, rec = po.apply(_clocked_transient_deck(), RAIL)
    assert rec["samples_before"] == rec["samples_after"] == 13824, rec
    assert math.isclose(rec["tstop_after_s"] - rec["tstop_before_s"],
                        rec["clock_delay_s"], rel_tol=1e-9), rec
    assert math.isclose(rec["clock_delay_s"], rec["clock_period_s"],
                        rel_tol=1e-15), rec
    # And the card the deck actually carries is exact, because `_ns` rounds to
    # the whole nanosecond the deck is written in.
    assert _card(_apply_ok(_clocked_transient_deck()), "tran") == \
        "tran 5n 13825000n"


def test_every_measurement_window_moves_by_the_same_one_period():
    """THE MUTATION THAT WOULD PASS QUIETLY: moving the stop time and leaving
    the windows would make each `meas` cover a different part of the record
    and still report under the same name."""
    before = _clocked_transient_deck()
    after, rec = po.apply(before, RAIL)
    was, now = _windows_ns(before), _windows_ns(after)
    assert was == [261120.0, 512000.0, 261120.0, 512000.0], was
    assert now == [262120.0, 513000.0, 262120.0, 513000.0], now
    assert [n - w for n, w in zip(now, was)] == [1000.0] * 4
    assert rec["meas_window_endpoints_shifted"] == 4


def test_the_supply_ends_at_the_value_it_started_from():
    """THE MUTATION THAT WOULD PASS QUIETLY: a ramp that settles anywhere but
    the deck's own supply silently rescales every `vavg / supply` the deck
    computes — the density a converter is graded on."""
    after, _ = po.apply(_clocked_transient_deck(supply="0.9"), RAIL)
    pwl = re.search(r"pwl\(([^)]*)\)", _card(after, "v_vdd")).group(1).split()
    assert pwl[1] == "0", pwl
    assert pwl[3] == "0.9" and pwl[5] == "0.9", pwl
    assert "let dens = vavg / 0.9" in after


# ── the decks that MUST NOT be touched (the negative controls) ─────────────
def test_an_op_only_deck_is_returned_byte_for_byte_and_the_reason_is_named():
    before = _op_only_deck()
    after, rec = po.apply(before, RAIL)
    assert after == before
    assert (rec["applied"], rec["refused"]) == (False, "no_transient"), rec


def test_an_unclocked_transient_deck_is_returned_byte_for_byte():
    """A block with no clock has no clocked loop to bring up. Ramping its rail
    would change its stimulus for no convergence it needed."""
    before = _unclocked_transient_deck()
    after, rec = po.apply(before, RAIL)
    assert after == before
    assert (rec["applied"], rec["refused"]) == (False, "no_clock_source"), rec


def test_applying_twice_is_refused_so_the_windows_cannot_walk():
    """THE MUTATION THAT WOULD PASS QUIETLY: a second application would move
    every window out by another period and the record would still look
    well-formed."""
    once, _ = po.apply(_clocked_transient_deck(), RAIL)
    twice, rec = po.apply(once, RAIL)
    assert twice == once
    assert (rec["applied"], rec["refused"]) == (False,
                                                "supply_source_not_dc"), rec


def test_a_rail_nothing_drives_is_an_absence_not_a_sequenced_deck():
    """The two refusals are different answers and must not collapse: one says
    'find the source', the other says 'it is already sequenced'."""
    deck = _clocked_transient_deck().replace(f"v_vdd {RAIL} 0 1.2",
                                             "v_other other 0 1.2")
    after, rec = po.apply(deck, RAIL)
    assert after == deck
    assert (rec["applied"], rec["refused"]) == (False,
                                               "supply_source_absent"), rec


def test_two_sources_on_one_rail_are_refused_rather_than_one_picked():
    deck = _clocked_transient_deck().replace(
        f"v_vdd {RAIL} 0 1.2", f"v_vdd {RAIL} 0 1.2\nv_vdd2 {RAIL} 0 1.2")
    after, rec = po.apply(deck, RAIL)
    assert after == deck
    assert rec["refused"] == "supply_source_ambiguous", rec
    assert rec["supply_sources"] == ["v_vdd", "v_vdd2"], rec


# ── the deck the PRODUCER writes carries it (the wiring) ───────────────────
def _ir():
    """The smallest topology IR `render_testbench` accepts for a clocked
    transient block."""
    return {
        "block": "conv",
        "ports": ["vdd", "vss", "vin", "clk", "bit_out"],
        "rails": {"vdd": "vdd", "vss": "vss"},
        "internal_nets": [],
        "testbench": {
            "supply_exprs": ["vdd"],
            "env_exprs": {},
            "conditions": [],
            "stimulus": ["v_vdd vdd 0 {supply}",
                         "v_clk clk 0 pulse(0 {supply} 0n 1n 1n 499n 1000n)",
                         "v_in vin 0 0.7"],
            "cards": [],
            "control": ["tran 5n 13824000n",
                        "meas tran vavg avg v(bit_out) from=261120n "
                        "to=512000n"],
        },
    }


def test_the_producer_emits_the_power_on_sequence_end_to_end():
    """THE WIRING ARM. The transform existing is not the fix; the deck the
    producer WRITES carrying it is."""
    text, env, notes = a3.render_testbench(
        _ir(), {"analog_device_params": {"nominal_supply_v": 1.2}},
        {"vdd": 1.2}, [])
    assert text is not None, notes
    assert _card(text, "v_vdd") == \
        "v_vdd vdd 0 pwl(0n 0 100n 1.2 13825000n 1.2)"
    assert _card(text, "v_clk") == \
        "v_clk clk 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)"
    assert _card(text, "tran") == "tran 5n 13825000n"
    assert env["poweron_sequence"]["applied"] is True
    assert any("poweron_sequence=applied" in n for n in notes), notes


def test_the_deck_the_producer_writes_says_so_in_its_own_words():
    """A deck that differs from the one a reader expects and does not say why
    is read as a deck somebody edited by hand."""
    text, _, _ = a3.render_testbench(
        _ir(), {"analog_device_params": {"nominal_supply_v": 1.2}},
        {"vdd": 1.2}, [])
    said = [ln for ln in text.splitlines()
            if ln.startswith("* condition:") and "poweron_sequence" in ln]
    assert len(said) == 1, text
    assert "brought up through a physical power-on" in said[0]
    assert "instead of a DC operating point" in said[0]


def test_an_op_only_producer_deck_keeps_the_deck_it_had():
    """The producer's own negative control: the `op`-only block reaches the
    same wiring and is NOT rewritten, and the note says which refusal fired."""
    ir = _ir()
    ir["testbench"]["control"] = ["op", "print v(bit_out)"]
    text, env, notes = a3.render_testbench(
        ir, {"analog_device_params": {"nominal_supply_v": 1.2}},
        {"vdd": 1.2}, [])
    assert _card(text, "v_vdd") == "v_vdd vdd 0 1.2"
    assert env["poweron_sequence"]["refused"] == "no_transient"
    assert any("not applied: no_transient" in n for n in notes), notes


# ══ THE DECK'S ENGLISH IS NOT THE DECK ════════════════════════════════════
#
# vibe-ic#712's ratchet named `plan` and `apply` as prose extractors with no
# polarity vocabulary. Three of the module's four patterns are anchored `^...$`
# over one SPICE card and no sentence can satisfy them — but `_MEAS_WINDOW` is
# `\b(from|to)=(\S+?)(n?)\b`, unanchored, and it used to run over the WHOLE
# file. MEASURED on the deck below, `apply` rewrote a COMMENT that said the
# window "is NOT shifted" — a sentence DENYING the value, overwritten anyway,
# which is exactly the #706/#711 shape.
#
# `deck_code_only` is the repair, and these cases are what make the module's
# `_NOT_PROSE` entry a measurement rather than a claim.

_DENYING_DECK = (
    "* delta_sigma tb\n"
    "* note: the reference run measured from=1000n to=2000n and is NOT"
    " shifted\n"
    "v_vdd vdd 0 1.8\n"
    "v_clk clk 0 pulse(0 1.8 0n 1n 1n 500n 1000n)\n"
    "tran 1n 10000n\n"
    ".meas tran vavg AVG v(out) from=1000n to=2000n  $ window from=9n\n"
)


def test_a_comment_that_denies_the_shift_is_not_rewritten():
    out, rec = po.apply(_DENYING_DECK, "vdd")
    assert rec["applied"] is True, rec
    lines = out.splitlines()
    # PRECONDITION: the run really did shift the REAL window, so this is a
    # case about WHICH text moved and not about a run that did nothing.
    assert any(ln.startswith(".meas") and "from=2000n to=3000n" in ln
               for ln in lines), out
    # The two `*` comments come back byte-identical, denial and all.
    for original in _DENYING_DECK.splitlines():
        if original.startswith("*"):
            assert original in lines, (
                "a `*` comment was rewritten — a sentence was read as a "
                "declaration:\n" + out)
    # And an in-line `$` comment on a live card is a comment too.
    assert "$ window from=9n" in out, out


def test_a_comment_endpoint_is_not_counted_as_a_window_that_moved():
    """`meas_window_endpoints_shifted` is published in the provenance record,
    so counting a comment's `from=` reported a window that never moved."""
    rec = po.plan(_DENYING_DECK, "vdd")
    assert rec["meas_window_endpoints_shifted"] == 2, rec


def test_the_strip_is_load_bearing_and_not_decoration(monkeypatch):
    """THE NEGATIVE CONTROL. With `deck_code_only` replaced by the identity —
    the strip deleted — the SAME deck's denying comment moves again. So the
    zero above is a statement about the code, and a fixture that could not
    have moved would prove nothing."""
    monkeypatch.setattr(po, "deck_code_only", lambda text: text)
    out, rec = po.apply(_DENYING_DECK, "vdd")
    assert rec["applied"] is True, rec
    assert "measured from=2000n to=3000n and is NOT shifted" in out, (
        "the identity strip must reproduce the defect, or this control is "
        "not measuring the strip:\n" + out)


def test_the_filler_keeps_a_card_from_joining_the_line_above():
    """`\\s` matches NEWLINES. MEASURED with a space filler: `_DC_SOURCE`'s
    `^(\\s*)` swallowed the blanked comment lines, the match began at offset 0
    and the rebuild DELETED every comment above the supply card. The filler is
    `#` for this reason, and the property is asked of the output."""
    code = po.deck_code_only(_DENYING_DECK)
    assert len(code) == len(_DENYING_DECK), "offsets must be preserved"
    m = po._DC_SOURCE.search(code)
    assert m is not None
    assert code[m.start():m.end()].lstrip().startswith("v_vdd"), (
        "the supply card's match must not reach back over the comments: "
        + repr(code[m.start():m.end()]))

#!/usr/bin/env python3
"""A3's verify runs a BOUNDED copy of the deck, and the bound is derived.

WHY (R-0915-117(1)). A3's verification asks three questions and no others --
did it converge, does the operating point put a node outside the supply rails,
does the transient -- so it is an instrument check on the emitted netlist, not
the ENOB receipt. Its span is therefore the span over which that claim settles.

MEASURED (lane icadc2, 8hd-3, the emitted delta_sigma over four conversion
windows), cumulative worst excursion, which is what the `railx_*` cards report
because they carry no from/to:
    after 1 window : vo1 +0.1214/+1.0360   vint +0.1426/+1.0282
    after 2 windows: vo1 +0.1214/+1.0396   vint +0.1420/+1.0282
    after 3 windows: vo1 +0.1214/+1.0396   vint +0.1420/+1.0282
    after 4 windows: vo1 +0.1214/+1.0396   vint +0.1385/+1.0282
Final after two windows, because an incremental converter resets its
integrators every conversion window and an excursion cannot accumulate.
On the declaration in hand that is 28.673 ms -> 1.024 ms.
"""
import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
_spec = importlib.util.spec_from_file_location(
    "a3", os.path.join(_HERE, "analog_a3_netlist_emit.py"))
a3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a3)

CLK = "v_clk clk 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)\n"
STAMP = ("* analog_incremental_decimator: mode=incremental window_clocks=512 "
         "order=2 coeff=0.2499 feedback_delay_clocks=1\n")


def deck(tran="tran 5n 28673000n", clk=CLK, stamp="", extra=""):
    return ("* tb\n" + stamp + clk + extra +
            "v_in vin 0 0.7\n.control\n" + tran + "\n.endc\n.end\n")


def _stop(text):
    m = re.search(r"^tran\s+(\S+)\s+(\S+)", text, re.MULTILINE)
    return m.group(2)


# ── direction 1: it bounds, and the bound is DERIVED ─────────────────────

def test_the_span_is_two_conversion_windows():
    out, rec = a3.verify_window_bound(deck(stamp=STAMP))
    assert out is not None, rec
    assert rec["conversion_windows"] == 2
    assert rec["window_clocks"] == 512
    assert rec["verify_window_clocks"] == 1024
    assert abs(rec["verify_window_s"] - 1024 * 1e-6) < 1e-12
    assert _stop(out) == "1024000n"


def test_a_different_window_count_gives_a_different_span():
    """Derived, not typed: change the stamp and the span follows it."""
    s = STAMP.replace("window_clocks=512", "window_clocks=64")
    out, rec = a3.verify_window_bound(deck(stamp=s))
    assert rec["window_clocks"] == 64
    assert rec["verify_window_clocks"] == 128
    assert _stop(out) == "128000n"


def test_a_different_clock_period_gives_a_different_span():
    """The other of the two numbers. Same window count, half the period."""
    clk = "v_clk clk 0 pulse(0 1.2 500n 1n 1n 249n 500n)\n"
    out, rec = a3.verify_window_bound(deck(stamp=STAMP, clk=clk))
    assert abs(rec["sample_clock_hz"] - 2e6) < 1.0
    assert _stop(out) == "512000n"


def test_the_deck_is_otherwise_byte_identical():
    """Only the transient span moves. The step the producer chose, the cards,
    the sources and the stamp all survive."""
    d = deck(stamp=STAMP)
    out, _ = a3.verify_window_bound(d)
    a, b = d.splitlines(), out.splitlines()
    assert len(a) == len(b)
    differing = [i for i in range(len(a)) if a[i] != b[i]]
    assert len(differing) == 1, [a[i] for i in differing]
    assert a[differing[0]].startswith("tran ")
    assert b[differing[0]].startswith("tran 5n ")


def test_the_window_count_may_come_from_the_ir_when_the_deck_has_no_stamp():
    """At A3 time the stamp is not on the deck yet -- it is written later, by
    `analog_resolution_stimulus`, when the A4 corner is built. The IR constant
    the stamp is derived FROM is the same number one step earlier."""
    out, rec = a3.verify_window_bound(
        deck(), {"constants": {"window_clocks": 512.0}})
    assert out is not None, rec
    assert rec["window_clocks_source"] == "topology_ir_constant"
    assert _stop(out) == "1024000n"


def test_the_decks_own_stamp_outranks_the_ir():
    """A deck that states its own decode is believed over the IR that made it."""
    _out, rec = a3.verify_window_bound(
        deck(stamp=STAMP.replace("window_clocks=512", "window_clocks=256")),
        {"constants": {"window_clocks": 512.0}})
    assert rec["window_clocks_source"] == "deck_decode_stamp"
    assert rec["window_clocks"] == 256


# ── direction 2: it REFUSES BY NAME rather than defaulting ───────────────

def test_a_deck_with_no_window_declared_anywhere_refuses_by_name():
    out, rec = a3.verify_window_bound(deck())
    assert out is None
    assert rec["reason"].startswith("verify_window_not_derivable_no_decode_stamp")


def test_a_deck_with_no_clock_card_refuses_by_name():
    out, rec = a3.verify_window_bound(deck(stamp=STAMP, clk=""))
    assert out is None
    assert rec["reason"].startswith("verify_window_not_derivable_no_clock_card")


def test_two_pulse_sources_are_not_a_clock_card():
    """`sample_clock_card` names ONE top-level pulse source or none. Two is
    ambiguous and must not be resolved by picking the first."""
    out, rec = a3.verify_window_bound(
        deck(stamp=STAMP, extra="v_other x 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)\n"))
    assert out is None
    assert rec["reason"].startswith("verify_window_not_derivable_no_clock_card")


def test_a_deck_with_no_transient_refuses_by_name():
    out, rec = a3.verify_window_bound(
        deck(stamp=STAMP, tran="op"))
    assert out is None
    assert rec["reason"].startswith("verify_window_not_derivable_no_tran_card")


def test_a_record_already_shorter_than_the_window_is_left_alone():
    """Never LENGTHENS a deck. A producer that already asked for less keeps
    what it asked for; this is a bound, not a target."""
    out, rec = a3.verify_window_bound(deck(tran="tran 5n 1000n", stamp=STAMP))
    assert out is None
    assert rec["reason"] == "deck_record_already_within_the_verify_window"


# ── the claim A3 makes must not change ───────────────────────────────────

def test_the_rails_verdict_vocabulary_is_unchanged():
    """The bound changes the SPAN, never what A3 asserts over it."""
    src = open(os.path.join(_HERE, "analog_a3_netlist_emit.py"),
               encoding="utf-8").read()
    for verdict in ("CONVERGED", "DID_NOT_CONVERGE",
                    "DC_OP_NODE_ABOVE_RAIL", "TRAN_NODE_OUTSIDE_RAIL"):
        assert '"%s"' % verdict in src, verdict


def test_a3_still_does_not_decode_the_bitstream():
    """THE PRECONDITION OF THE WHOLE BOUND. A3 is bounded because it asks a
    rails question; a verify that DECODED would need the graded record and
    this bound would be a narrowing. If decoding ever appears here, this test
    is the one that should stop it."""
    src = open(os.path.join(_HERE, "analog_a3_netlist_emit.py"),
               encoding="utf-8").read()
    body = src[src.index("def verify_with_ngspice("):]
    body = body[:body.index("\ndef ")] if "\ndef " in body else body
    for forbidden in ("sndr", "enob", "decimate", "sample_decisions"):
        assert forbidden not in body.lower(), forbidden


def test_the_record_says_this_is_the_verify_bound_not_the_a4_window():
    _out, rec = a3.verify_window_bound(deck(stamp=STAMP))
    assert "A4" in rec["scope"]
    assert rec["declared_record_s"] == 0.028673

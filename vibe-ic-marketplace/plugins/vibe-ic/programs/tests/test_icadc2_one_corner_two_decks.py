#!/usr/bin/env python3
"""R-0915-117(2): a corner emits TWO decks from one netlist identity.

WHY (MEASURED, 8hd-3, image 0.3.67). Two decks identical but for the save list
and the rail cards, 21 us each -- the ratio is duration-independent:
    122 vectors, 240 railx cards : peak RSS 111596 kB, wall 249.3 s
      1 vector,    0 railx cards : peak RSS  39424 kB, wall 238.4 s
=> 38827 kB fixed, 28.4 kB per saved vector per us. At the declared 28.673 ms
record that is 94.79 GiB against 0.81 GiB (116x) while the wall moves 1.05x.
Cross-checked against a live corner's own RSS slope (96.9 GiB): agree to 2%.
120 of the 122 vectors existed only for the `railx_*` cards -- A3's rails
claim, which settles in two conversion windows (R-0915-117(1)) -- so they ride
on the graded record for nothing.
"""
import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
_spec = importlib.util.spec_from_file_location(
    "ars", os.path.join(_HERE, "analog_resolution_stimulus.py"))
ars = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ars)

STAMP = ("* analog_incremental_decimator: mode=incremental window_clocks=512 "
         "order=2 coeff=0.2499 feedback_delay_clocks=1\n")
CLK = "v_clk clk 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)\n"
RAILS = "".join("meas tran railx_max_n%d max v(xdut.n%d)\n" % (i, i)
                for i in range(3))
SAVE = ".save v(bit_out) v(xdut.n0) v(xdut.n1) v(xdut.n2)\n"


def deck(stamp=STAMP, clk=CLK, rails=RAILS, save=SAVE,
         wr="wrdata out.wrdata v(bit_out)\n", tran="tran 5n 28673000n"):
    return ("* tb\n" + stamp + clk + "v_in vin 0 sin(0.7 0.36 319.6)\n" +
            save + ".control\n" + tran + "\n" + rails + wr + ".endc\n.end\n")


def _vecs(text, line_re):
    m = re.search(line_re, text, re.IGNORECASE | re.MULTILINE)
    return re.findall(r"[vi]\([^)]*\)", m.group(0)) if m else []


# ── the RAILS deck ───────────────────────────────────────────────────────

def test_the_rails_deck_is_two_conversion_windows_and_keeps_its_instrumentation():
    out, rec = ars.rails_deck(deck())
    assert out is not None, rec
    assert rec["claim"] == "rails"
    assert rec["conversion_windows"] == 2 and rec["window_clocks"] == 512
    assert abs(rec["span_s"] - 1024e-6) < 1e-12
    assert re.search(r"(?im)^tran\s+5n\s+1024000n\s*$", out)
    assert len(re.findall(r"(?im)^meas tran railx_", out)) == 3
    assert len(_vecs(out, r"^\s*\.save\b.*$")) == 4


def test_the_rails_span_follows_the_declared_window_and_the_clock():
    _o, r1 = ars.rails_deck(deck(stamp=STAMP.replace("512", "64")))
    assert r1["window_clocks"] == 64 and abs(r1["span_s"] - 128e-6) < 1e-12
    half = "v_clk clk 0 pulse(0 1.2 500n 1n 1n 249n 500n)\n"
    _o, r2 = ars.rails_deck(deck(clk=half))
    assert abs(r2["span_s"] - 512e-6) < 1e-12


def test_the_rails_deck_refuses_by_name_without_a_window():
    out, rec = ars.rails_deck(deck(stamp=""))
    assert out is None and rec["reason"] == ars.SPLIT_NO_WINDOW


def test_the_rails_deck_refuses_by_name_without_one_clock_card():
    out, rec = ars.rails_deck(deck(clk=""))
    assert out is None and rec["reason"] == ars.SPLIT_NO_CLOCK
    two = CLK + "v_o x 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)\n"
    out, rec = ars.rails_deck(deck(clk=two))
    assert out is None and rec["reason"] == ars.SPLIT_NO_CLOCK


def test_a_window_may_be_supplied_when_the_deck_carries_no_stamp():
    out, rec = ars.rails_deck(deck(stamp=""), window_clocks=512)
    assert out is not None and rec["window_clocks"] == 512


# ── the GRADED deck ──────────────────────────────────────────────────────

def test_the_graded_deck_keeps_the_full_record_and_only_the_receipts_vectors():
    out, rec = ars.graded_deck(deck())
    assert out is not None, rec
    assert rec["claim"] == "graded"
    assert re.search(r"(?im)^tran\s+5n\s+28673000n\s*$", out), "record shortened"
    assert _vecs(out, r"^\s*\.save\b.*$") == ["v(bit_out)"]
    assert rec["retained_vectors"] == ["v(bit_out)"]
    assert rec["previously_retained"] == 4


def test_the_graded_deck_carries_no_rail_card():
    out, rec = ars.graded_deck(deck())
    assert re.findall(r"(?im)^meas tran railx_", out) == []
    assert rec["rail_cards_removed"] == 3


def test_the_retention_follows_the_wrdata_card_not_a_typed_list():
    """The receipt reads the DUMP; the dump is this deck's own wrdata card. Name
    a different vector there and the retention follows it."""
    out, rec = ars.graded_deck(deck(wr="wrdata out.wrdata v(xdut.n1)\n"))
    assert rec["retained_vectors"] == ["v(xdut.n1)"]
    assert _vecs(out, r"^\s*\.save\b.*$") == ["v(xdut.n1)"]


def test_a_deck_with_no_wrdata_refuses_by_name():
    out, rec = ars.graded_deck(deck(wr=""))
    assert out is None and rec["reason"] == ars.SPLIT_NO_WRDATA


def test_the_two_decks_come_from_the_same_source_text():
    """One corner, one netlist identity, two claims."""
    d = deck()
    r, _ = ars.rails_deck(d)
    g, _ = ars.graded_deck(d)
    for text in (r, g):
        assert ".include" not in d or ".include" in text
        assert "v_in vin 0 sin(0.7 0.36 319.6)" in text


# ── the derived admission estimate ───────────────────────────────────────

def test_the_rss_estimate_is_derived_from_vectors_times_record():
    d = deck()
    full = ars.estimated_peak_rss_bytes(d)
    g, _ = ars.graded_deck(d)
    r, _ = ars.rails_deck(d)
    assert full > ars.estimated_peak_rss_bytes(g) > 0
    assert ars.estimated_peak_rss_bytes(r) > 0
    # the graded deck keeps the record and drops 3 of 4 vectors
    assert ars.estimated_peak_rss_bytes(g) < full / 3


def test_the_estimate_scales_with_both_terms():
    a = ars.estimated_peak_rss_bytes(deck(tran="tran 5n 1000000n"))
    b = ars.estimated_peak_rss_bytes(deck(tran="tran 5n 2000000n"))
    fixed = ars.RSS_FIXED_BYTES
    assert abs((b - fixed) - 2 * (a - fixed)) < 1024
    c = ars.estimated_peak_rss_bytes(
        deck(tran="tran 5n 1000000n", save=".save v(bit_out) v(xdut.n0)\n"))
    assert abs((a - fixed) - 2 * (c - fixed)) < 1024


def test_the_estimate_refuses_rather_than_guessing():
    assert ars.estimated_peak_rss_bytes(deck(tran="op")) is None
    assert ars.estimated_peak_rss_bytes(deck(save="")) is None


# ── the admission plans against the deck, not against a declared constant ──

_aca_spec = importlib.util.spec_from_file_location(
    "aca", os.path.join(_HERE, "analog_corner_admission.py"))
aca = importlib.util.module_from_spec(_aca_spec)
_aca_spec.loader.exec_module(aca)


class _Ledger(aca.AdmissionLedger):
    """The ledger with its host facts pinned, so the arithmetic is the subject."""
    def __init__(self, ram, headroom):
        self.ram_bytes = ram
        self.headroom = headroom
    def _locked(self):
        import contextlib
        @contextlib.contextmanager
        def _cm():
            yield {}
        return _cm()
    def _accounted_bytes(self, data):
        return 0, 0
    def _receipt(self, *a, **k):
        return None


GiB = 1024 ** 3


def test_the_plan_uses_each_jobs_own_bytes_when_every_job_carries_them():
    led = _Ledger(125 * GiB, 16 * GiB)
    rec = led.plan([{"id": "a", "bytes": 3 * GiB},
                    {"id": "b", "bytes": 1 * GiB}], 24 * GiB)
    assert rec["reservation_source"] == "derived_from_each_deck"
    # planned against the LARGEST, because concurrency promises they run together
    assert rec["reservation_bytes"] == 3 * GiB
    assert rec["safe_concurrency"] == (125 - 16) // 3
    assert rec["per_job_bytes"] == {"a": 3 * GiB, "b": 1 * GiB}
    assert rec["declared_reservation_bytes"] == 24 * GiB


def test_a_declared_reservation_still_plans_exactly_as_before():
    led = _Ledger(125 * GiB, 16 * GiB)
    rec = led.plan([{"id": "a"}, {"id": "b"}], 24 * GiB)
    assert rec["reservation_source"] == "declared"
    assert rec["reservation_bytes"] == 24 * GiB
    assert rec["safe_concurrency"] == (125 - 16) // 24
    assert rec["per_job_bytes"] is None


def test_one_job_without_bytes_makes_the_whole_plan_fall_back():
    """Never half derived and half declared."""
    led = _Ledger(125 * GiB, 16 * GiB)
    rec = led.plan([{"id": "a", "bytes": 3 * GiB}, {"id": "b"}], 24 * GiB)
    assert rec["reservation_source"] == "declared"


def test_the_derived_plan_would_have_caught_the_real_case():
    """MEASURED: the graded delta_sigma deck holds 94.8 GiB, and 24 GiB was
    declared. On a 125 GiB host with 16 GiB headroom the declared number admits
    four corners; the deck's own number admits one."""
    led = _Ledger(125 * GiB, 16 * GiB)
    declared = led.plan([{"id": "c%d" % i} for i in range(4)], 24 * GiB)
    derived = led.plan([{"id": "c%d" % i, "bytes": int(94.8 * GiB)}
                        for i in range(4)], 24 * GiB)
    assert declared["safe_concurrency"] >= 4
    assert derived["safe_concurrency"] == 1

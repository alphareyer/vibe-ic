#!/usr/bin/env python3
"""Guards for the A4 ENOB producer.

THE GAP, pinned by `test_the_only_corner_results_writer_never_writes_enob`:
the ENOB gate reads `enob`/`sndr_db` out of `corner_results.json`, and the one
program that writes that file never puts either field in it. Without a
producer, the gate can only SKIP. That test holds on the PRE-FIX tree too — it
is a statement about `analog_real_corner_sweep.py`, which this change does not
touch — so it stays true as a description of why this file exists.

The fit itself is guarded BIDIRECTIONALLY: a clean modulator and the SAME
modulator with injected quantiser noise must both MEASURE and must DIFFER in
the right direction. A test that only asserts "a number came out" would pass
against a function that returns a constant.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

FIT = _PROGRAMS / "analog_adc_enob_fit.py"
GATE = _PROGRAMS / "analog_adc_enob_corner_check.py"

FS = 1e6
TONE = 651.0416667
OSR = 256
N = 13824


def _modulate(noise: float) -> list:
    """Second-order delta-sigma bitstream of a coherent tone.

    Deterministic: a fixed LCG, so the two arms differ only by `noise`.
    """
    seed = 12345
    i1 = i2 = 0.0
    prev = 0.0
    out = []
    for k in range(N):
        u = 0.6 * math.sin(2 * math.pi * TONE * k / FS)
        i1 += u - prev
        i2 += i1 - prev
        d = 0.0
        if noise:
            seed = (1103515245 * seed + 12345) % (1 << 31)
            d = (seed / (1 << 31) - 0.5) * noise
        prev = 1.0 if (i2 + d) > 0 else -1.0
        out.append(prev)
    return out


def _dump(tmp_path: Path, name: str, y: list, dt: float = 1 / FS) -> Path:
    p = tmp_path / name
    with p.open("w") as fh:
        for k, v in enumerate(y):
            fh.write(f"{k * dt:.12e}  {(v + 1) / 2 * 1.2:.12e}\n")
    return p


def _run(*args):
    return subprocess.run([sys.executable, str(FIT), *map(str, args)],
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
# 1. the fit MEASURES, and it DISCRIMINATES
# ---------------------------------------------------------------------------
def test_a_clean_modulator_measures_a_resolution(tmp_path):
    d = _dump(tmp_path, "clean.wrdata", _modulate(0.0))
    out = tmp_path / "clean.json"
    r = _run(d, "--corner", "tt_27c", "--fclk", FS, "--tone", TONE,
             "--osr", OSR, "--json", out)
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads(out.read_text())
    assert rec["status"] == "MEASURED"
    assert rec["enob"] is not None and rec["sndr_db"] is not None
    # the two are the SAME relation the gate uses; they cannot drift apart
    assert abs(rec["enob"] - (rec["sndr_db"] - 1.76) / 6.02) < 1e-2


def test_injected_noise_LOWERS_the_measured_resolution(tmp_path):
    """The discriminating control. Same modulator, same tone, same window —
    only the quantiser noise moves, and the answer must move with it.

    The level is MEASURED, not guessed: the sweep 0/6/20/60/200/600 gives
    12.930/12.798/11.980/10.310/8.648/7.027 bit on this tree, so 60 sits well
    clear of the 0.5-bit threshold instead of grazing it (6.0 moves only
    0.13 bit and would have made this control almost vacuous)."""
    clean = tmp_path / "c.json"
    noisy = tmp_path / "n.json"
    _run(_dump(tmp_path, "c.wrdata", _modulate(0.0)), "--corner", "c",
         "--fclk", FS, "--tone", TONE, "--osr", OSR, "--json", clean)
    _run(_dump(tmp_path, "n.wrdata", _modulate(60.0)), "--corner", "n",
         "--fclk", FS, "--tone", TONE, "--osr", OSR, "--json", noisy)
    a = json.loads(clean.read_text())
    b = json.loads(noisy.read_text())
    assert a["status"] == b["status"] == "MEASURED"
    assert b["enob"] < a["enob"] - 0.5, (a["enob"], b["enob"])


def test_the_measured_resolution_is_MONOTONE_in_the_injected_noise(tmp_path):
    """One pair can be luck; a monotone ladder cannot. A fit that returned a
    constant, or that keyed on anything but the spectrum, fails here."""
    got = []
    for nz in (0.0, 20.0, 60.0, 200.0):
        j = tmp_path / f"n{nz}.json"
        _run(_dump(tmp_path, f"n{nz}.wrdata", _modulate(nz)), "--corner",
             f"n{nz}", "--fclk", FS, "--tone", TONE, "--osr", OSR, "--json", j)
        rec = json.loads(j.read_text())
        assert rec["status"] == "MEASURED"
        got.append(rec["enob"])
    assert got == sorted(got, reverse=True), got
    assert got[0] - got[-1] > 2.0, got


# ---------------------------------------------------------------------------
# 2. it REFUSES rather than inventing a number
# ---------------------------------------------------------------------------
def test_a_record_too_short_for_the_band_is_UNMEASURED(tmp_path):
    """A 271-sample record at OSR 256 has ZERO in-band bins. This is the real
    shape of every 532 us startup control: no such record can ever carry an
    ENOB, and saying 0 bits would be a false FAIL."""
    d = _dump(tmp_path, "short.wrdata", _modulate(0.0)[:271])
    out = tmp_path / "s.json"
    r = _run(d, "--corner", "short", "--fclk", FS, "--tone", TONE,
             "--osr", OSR, "--json", out)
    assert r.returncode == 2
    rec = json.loads(out.read_text())
    assert rec["status"] == "UNMEASURED" and rec["enob"] is None
    assert "in-band bins" in rec["reason"]


def test_a_constant_bitstream_is_UNMEASURED_not_zero_bits(tmp_path):
    d = _dump(tmp_path, "flat.wrdata", [1.0] * N)
    out = tmp_path / "f.json"
    r = _run(d, "--corner", "flat", "--fclk", FS, "--tone", TONE,
             "--osr", OSR, "--json", out)
    assert r.returncode == 2
    rec = json.loads(out.read_text())
    assert rec["status"] == "UNMEASURED" and rec["enob"] is None


def test_a_record_ending_before_the_window_is_UNMEASURED(tmp_path):
    d = _dump(tmp_path, "early.wrdata", _modulate(0.0))
    out = tmp_path / "e.json"
    r = _run(d, "--corner", "early", "--fclk", FS, "--tone", TONE,
             "--osr", OSR, "--from-s", 1.0, "--json", out)
    assert r.returncode == 2
    assert json.loads(out.read_text())["status"] == "UNMEASURED"


def test_an_undeclared_stimulus_refuses_rather_than_assuming_one(tmp_path):
    """No fclk/tone/OSR in the design INPUT and none on the command line: the
    program must refuse. A fit against an assumed stimulus is not a
    measurement."""
    d = _dump(tmp_path, "x.wrdata", _modulate(0.0))
    r = _run(d, "--corner", "x")
    assert r.returncode == 2 and "does not declare" in r.stderr


# ---------------------------------------------------------------------------
# 3. the row it writes is the row the GATE reads — proven by driving the gate
# ---------------------------------------------------------------------------
def _block(tmp_path, target=14.0):
    b = tmp_path / "phase3" / "analog" / "adc"
    b.mkdir(parents=True)
    (b / "spec.json").write_text(json.dumps({
        "specs": [{"name": "enob", "min": target},
                  {"name": "fclk", "target": FS},
                  {"name": "tone", "target": TONE},
                  {"name": "osr", "target": OSR}]}))
    return b


def test_before_this_producer_the_gate_cannot_reach_a_verdict(tmp_path):
    """The pre-fix state, driven rather than asserted: with an ENOB target and
    no corner_results.json the gate reports NO MEASURED ENOB and does not pass.

    THE ASSERTION IS THE SUBSTANCE, NOT THE WORD. This test used to require
    `rc == 0` and the literal word SKIP, which described how live main behaved
    when the branch was written (2026-07-30). Main has since made that state
    honest on its own: the gate now exits 2 with

        [UNMEASURED] analog_adc_enob_corner_check: a block declares this axis
        and no data measures it — not a pass.

    which is a strictly better answer to the same question, and the old test
    failed on the improvement. What this branch is about is that no ENOB can be
    REACHED without a producer, so that is what is asserted; both SKIP and
    UNMEASURED satisfy it and a measured number does not."""
    b = _block(tmp_path)
    out = tmp_path / "g.json"
    r = subprocess.run([sys.executable, str(GATE), str(tmp_path),
                        "--json", str(out)], capture_output=True, text=True)
    blob = (out.read_text() if out.exists() else "") + r.stdout + r.stderr
    assert not (b / "corner_results.json").exists()
    # not a pass, by either spelling
    assert r.returncode != 0 or "SKIP" in blob.upper(), blob
    # and NO measured ENOB anywhere in what it reported
    verdict = json.loads(out.read_text()) if out.exists() else {}
    assert not _reported_enobs(verdict), (
        f"the gate reported an ENOB with no producer present: {verdict}")


def test_the_fitted_row_lets_the_gate_reach_a_verdict(tmp_path):
    """After the fit writes its row, the gate stops skipping and answers."""
    b = _block(tmp_path)
    _run(_dump(tmp_path, "w.wrdata", _modulate(0.0)), "--corner", "tt_27c",
         "--block-dir", b)
    doc = json.loads((b / "corner_results.json").read_text())
    assert doc["corners"][0]["name"] == "tt_27c"
    assert "enob" in doc["corners"][0]
    out = tmp_path / "g2.json"
    r = subprocess.run([sys.executable, str(GATE), str(tmp_path),
                        "--json", str(out)], capture_output=True, text=True)
    rep = json.loads(out.read_text())
    assert "SKIP" not in str(rep.get("verdict", "")).upper(), rep
    assert r.returncode in (0, 1)


def test_the_stimulus_is_taken_from_the_design_INPUT_spec(tmp_path):
    """With spec.json declaring the stimulus, no CLI values are needed."""
    b = _block(tmp_path)
    r = _run(_dump(tmp_path, "w.wrdata", _modulate(0.0)), "--corner", "c",
             "--block-dir", b)
    assert r.returncode == 0, r.stdout + r.stderr


def test_merging_a_second_corner_keeps_the_first(tmp_path):
    b = _block(tmp_path)
    for c in ("ff_125c", "ss_m40c"):
        _run(_dump(tmp_path, f"{c}.wrdata", _modulate(0.0)),
             "--corner", c, "--block-dir", b)
    names = [x["name"] for x in
             json.loads((b / "corner_results.json").read_text())["corners"]]
    assert names == ["ff_125c", "ss_m40c"]


# ---------------------------------------------------------------------------
# 4. the gap statement itself — holds on the pre-fix tree
# ---------------------------------------------------------------------------
def _reported_enobs(verdict):
    """Every MEASURED enob the gate put in its own report."""
    rows = []
    for key in ("blocks", "corners", "rows", "results"):
        v = verdict.get(key) if isinstance(verdict, dict) else None
        if isinstance(v, list):
            rows.extend(x for x in v if isinstance(x, dict))
    out = [r for r in rows
           if isinstance(r.get("enob"), (int, float))
           or isinstance(r.get("sndr_db"), (int, float))]
    if isinstance(verdict, dict) and isinstance(verdict.get("enob"), (int, float)):
        out.append(verdict)
    return out


def test_a_corner_sweep_row_alone_never_carries_a_measured_enob(tmp_path):
    """The gap statement, asserted BEHAVIOURALLY instead of by reading source.

    This was a substring scan: it required the literal `sndr` to be absent from
    `analog_real_corner_sweep.py`. That is not the property anybody wanted, and
    it broke on an unrelated landing — main added a SPEC-NAME NORMALISATION
    table so a converter's declared L5 target can be read at all::

        "sndr":    {"sndr", "sinad", "signaltonoiseanddistortion"},

    Reading a declared spec NAME is not emitting a measured value, so the scan
    fired on a change that has nothing to do with this branch. A test that
    watches a file's spelling watches the wrong thing.

    The invariant that matters: a corner row as the sweep produces it — no
    `enob`, no `sndr_db` — cannot make the gate report a measured ENOB. That is
    checked by RUNNING the gate on exactly such a row, so an unrelated edit to
    any producer's prose can never move it."""
    b = _block(tmp_path)
    (b / "corner_results.json").write_text(json.dumps({"corners": [
        {"name": "tt_27c", "status": "OK", "vout": 1.8},
        {"name": "ff_125c", "status": "OK", "vout": 1.81}]}), encoding="utf-8")
    out = tmp_path / "g.json"
    r = subprocess.run([sys.executable, str(GATE), str(tmp_path),
                        "--json", str(out)], capture_output=True, text=True)
    verdict = json.loads(out.read_text()) if out.exists() else {}
    assert not _reported_enobs(verdict), (
        f"a sweep row with no enob/sndr_db yielded a measured ENOB: {verdict}")
    assert r.returncode != 0 or "SKIP" in (r.stdout + r.stderr).upper(), (
        r.stdout + r.stderr)

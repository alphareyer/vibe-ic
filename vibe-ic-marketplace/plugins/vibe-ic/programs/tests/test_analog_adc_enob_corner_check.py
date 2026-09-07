"""test_analog_adc_enob_corner_check.py — R12 system-ENOB per-corner (v1.3.54).

Proves the gate (a) PASSes when every corner clears the ENOB target, (b) FAILs
when a NON-typ corner droops below it (the "measured at typ only" escape the
gate closes — ENOB computed per-corner, not just typ), (c) honest-SKIPs when a
converter has an ENOB target but no per-corner SNDR/ENOB was measured (the
real field-class corner file shape: OTA gains, no per-corner SNDR).

Block names are synthetic — no chip/SKU literal.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent / "analog_adc_enob_corner_check.py"


def _mk(project: Path, block: str, spec: dict, corners: dict | None):
    d = project / "phase3" / "analog" / block
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.json").write_text(json.dumps(spec))
    if corners is not None:
        (d / "corner_results.json").write_text(json.dumps(corners))


def _run(project: Path):
    r = subprocess.run(
        [sys.executable, str(PROG), str(project),
         "--json", str(project / "r.json")],
        capture_output=True, text=True)
    rpt = json.loads((project / "r.json").read_text())
    return r, rpt


def _adc_spec(enob=14):
    return {"block": "adc0", "type": "adc",
            "specs": [{"name": "enob", "target": enob, "min": 10,
                       "units": "bit"}]}


def test_pass_all_corners_meet_enob(tmp_path: Path):
    # SNDR 87.9 dB -> ENOB ~ 14.31 at every corner
    corners = {"corners": [
        {"name": "TT_27c", "sndr_db": 87.9},
        {"name": "SS_125c", "sndr_db": 87.5},
        {"name": "FF_-40c", "sndr_db": 88.4},
    ]}
    _mk(tmp_path, "adc0", _adc_spec(14), corners)
    r, rpt = _run(tmp_path)
    assert r.returncode == 0, r.stdout
    assert rpt["verdict"] == "PASS"


def test_fail_non_typ_corner_droops(tmp_path: Path):
    """Typ meets ENOB=14 but the hot corner droops to ~13.6 -> FAIL, and the
    failing corner (NOT typ) is named."""
    corners = {"corners": [
        {"name": "TT_27c", "sndr_db": 87.9},       # ENOB ~14.31 OK
        {"name": "SS_125c", "enob": 13.6},         # BELOW target
    ]}
    _mk(tmp_path, "adc0", _adc_spec(14), corners)
    r, rpt = _run(tmp_path)
    assert r.returncode == 1, r.stdout
    assert rpt["verdict"] == "FAIL"
    blk = next(b for b in rpt["blocks"] if b.get("status") == "FAIL")
    failing = {fc["corner"] for fc in blk["failing_corners"]}
    assert "SS_125c" in failing
    assert "TT_27c" not in failing


def test_skip_when_no_per_corner_sndr(tmp_path: Path):
    """field-class shape: ADC block with ENOB target but the corner file
    carries only OTA gains (no per-corner SNDR/ENOB) -> honest SKIP."""
    corners = {"corners": [
        {"name": "TT_27c", "ota_dc_gain_db": 55.1},
        {"name": "SS_125c", "ota_dc_gain_db": 51.5},
    ]}
    _mk(tmp_path, "adc0", _adc_spec(14), corners)
    r, rpt = _run(tmp_path)
    # RE-ANCHORED (#693 family). These four asserted `returncode == 0` and
    # `verdict == "SKIP"` for a block that DECLARES this axis and carries no
    # usable data — and this test's own name and docstring already call that
    # "not a silent pass" / "must be UNMEASURED". The assertion contradicted
    # the property the test is named for: at the exit-code level rc 0 IS a
    # pass, so a wired flow counted it among the gates that passed.
    #
    # A block with NO target at all is still SKIP / rc 0 — genuinely not
    # applicable, and that case is unchanged.
    assert r.returncode == 2
    assert rpt["verdict"] == "UNMEASURED"


def test_nan_enob_is_not_a_silent_pass(tmp_path: Path):
    """Step-2.7 finding — a NaN ENOB/SNDR corner (non-converged sim, bareword
    NaN from json allow_nan=True) must be UNMEASURED, never a clean pass. A
    lone NaN corner => SKIP, not [PASS]."""
    d = tmp_path / "phase3" / "analog" / "adc0"
    d.mkdir(parents=True)
    (d / "spec.json").write_text(json.dumps(_adc_spec(14)))
    (d / "corner_results.json").write_text(
        '{"corners": [{"name": "SS_125c", "enob": NaN}]}')
    r, rpt = _run(tmp_path)
    # RE-ANCHORED (#693 family). These four asserted `returncode == 0` and
    # `verdict == "SKIP"` for a block that DECLARES this axis and carries no
    # usable data — and this test's own name and docstring already call that
    # "not a silent pass" / "must be UNMEASURED". The assertion contradicted
    # the property the test is named for: at the exit-code level rc 0 IS a
    # pass, so a wired flow counted it among the gates that passed.
    #
    # A block with NO target at all is still SKIP / rc 0 — genuinely not
    # applicable, and that case is unchanged.
    assert r.returncode == 2
    assert rpt["verdict"] == "UNMEASURED", rpt


def test_nan_does_not_mask_real_fail(tmp_path: Path):
    d = tmp_path / "phase3" / "analog" / "adc0"
    d.mkdir(parents=True)
    (d / "spec.json").write_text(json.dumps(_adc_spec(14)))
    (d / "corner_results.json").write_text(
        '{"corners": [{"name": "TT_27c", "sndr_db": NaN},'
        ' {"name": "SS_125c", "enob": 13.6}]}')
    r, rpt = _run(tmp_path)
    assert r.returncode == 1
    assert rpt["verdict"] == "FAIL"


def test_skip_when_not_a_graded_converter(tmp_path: Path):
    """A block with no ENOB/SNDR target is not graded -> SKIP."""
    spec = {"block": "ldo0", "type": "ldo",
            "specs": [{"name": "Vout", "target": 1.2}]}
    corners = {"corners": [{"name": "TT_27c", "sndr_db": 60.0}]}
    _mk(tmp_path, "ldo0", spec, corners)
    r, rpt = _run(tmp_path)
    assert r.returncode == 0
    assert rpt["verdict"] == "SKIP"


def test_unmeasured_publishes_a_typed_reason_class(tmp_path: Path):
    """UNMEASURED must say WHY, in the taxonomy's own vocabulary.

    An UNMEASURED verdict exits 2. A consumer that reads only the exit code
    and the prose has no typed reason to read, and
    `_flow_reason_taxonomy.infer_nonverdict_reason` is deliberately
    fail-closed: an unclassified non-verdict is classified EXECUTION_ERROR,
    which tells a reader the gate CRASHED. This gate does not crash here — it
    runs, examines the block, and finds no corner carrying the field the
    declared axis is graded on. That is a zero measured denominator.

    Measured on a real run (u_hawaii_adc, v1.17.12): Step A4 carried
    `analog_adc_enob_corner_check rc=2 verdict=INCOMPLETE
    reason_class=EXECUTION_ERROR` for exactly this input.
    """
    spec = {"block": "adc0", "type": "adc",
            "specs": [{"name": "enob", "target": 14}]}
    # A corner that ran, and carries no sndr/enob field at all.
    corners = {"corners": [{"name": "TT_27c", "vout_v": 0.62}]}
    _mk(tmp_path, "adc0", spec, corners)
    r, rpt = _run(tmp_path)
    assert r.returncode == 2
    assert rpt["verdict"] == "UNMEASURED", rpt
    assert rpt.get("reason_class") == "ZERO_DENOMINATOR", rpt


def test_reason_class_is_not_skip_eligible(tmp_path: Path):
    """The published class must not be able to launder UNMEASURED into a skip.

    ZERO_DENOMINATOR is in `_flow_reason_taxonomy.INCOMPLETE`. If a later edit
    moved this gate to a SKIP_ELIGIBLE class, the step would silently leave the
    follow-up population, so the class is pinned against the taxonomy itself
    rather than against a literal.
    """
    import _flow_reason_taxonomy as tax
    spec = {"block": "adc0", "type": "adc",
            "specs": [{"name": "enob", "target": 14}]}
    corners = {"corners": [{"name": "TT_27c", "vout_v": 0.62}]}
    _mk(tmp_path, "adc0", spec, corners)
    _r, rpt = _run(tmp_path)
    cls = rpt.get("reason_class")
    assert cls not in tax.SKIP_ELIGIBLE, cls
    assert cls in tax.INCOMPLETE, cls


# ── vibe-ic#2188 — measuring a resolution instead of refusing to ───────────
#
# Everything above grades corners that ALREADY carry an `sndr_db`/`enob` field.
# What follows covers the path that had never had a producer: a corner graded
# from the A4 transient itself, the band that measurement has to be taken over,
# and the producer's own account of why a deck could not carry one.
import math                                                       # noqa: E402
import sys                                                        # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analog_adc_enob_corner_check as gate                       # noqa: E402


def _modulator_bitstream(n=16384, osr=64, fclk=1e6, amp=0.45):
    """A first-order 1-bit delta-sigma modulator, simulated numerically.

    Not a fixture of numbers: the loop below IS the modulator, so the spectrum
    it produces carries the noise shaping that makes the band matter. The tone
    sits at an odd bin a third of the way to the band edge, which is where this
    repo's producer puts it.
    """
    bins_in_band = n // (2 * osr)
    m = bins_in_band // 3
    if m % 2 == 0:
        m -= 1
    rows, integ, y = [], 0.0, 1.0
    for k in range(n):
        integ += amp * math.sin(2.0 * math.pi * m * k / n) - y
        y = 1.0 if integ >= 0 else -1.0
        rows.append(f"{k / fclk:.12e} {y:.12e}")
    deck = (f"v_ain ain 0 sin(0 {amp} {m * fclk / n:.10g})\n"
            f"v_clk clk 0 pulse(0 1.8 0n 1n 1n 499n {1e9 / fclk:.10g}n)\n"
            f"tran 5n {n / fclk * 1e9:.10g}n\n")
    return deck, "\n".join(rows)


def test_in_band_and_whole_span_disagree_on_a_modulator():
    """THE MUTATION ARM for the band, and the reason it exists.

    An oversampled modulator pushes its quantisation noise OUT of the band it
    is graded over — that is what oversampling is for. Integrate the whole
    Nyquist span instead and a converter that is working perfectly measures
    near 0 dB, so a gate that did it would FAIL every delta-sigma ever built.
    Same bitstream, same deck; only the declared OSR differs.
    """
    deck, dump = _modulator_bitstream(osr=64)
    in_band, meta_in = gate.sndr_db_from_transient(deck, dump, osr=64)
    whole, meta_all = gate.sndr_db_from_transient(deck, dump, osr=1.0)
    assert in_band is not None and whole is not None
    assert meta_in["method"] == "fft_signal_bin_vs_in_band_rest"
    assert meta_all["method"] == "fft_signal_bin_vs_rest"
    # The DIRECTION is the claim: the band is where the resolution is.
    assert in_band > whole + 30.0, (in_band, whole)
    assert (in_band - 1.76) / 6.02 > 5.0
    assert (whole - 1.76) / 6.02 < 2.0


def test_oversampled_without_a_clock_refuses_rather_than_grading():
    """No clock, no band. Grading the whole span here would be a FALSE FAIL,
    so the honest answer is that the band could not be established."""
    deck, dump = _modulator_bitstream(osr=64)
    deck = "\n".join(ln for ln in deck.splitlines()
                     if not ln.startswith("v_clk")) + "\n"
    value, meta = gate.sndr_db_from_transient(deck, dump, osr=64)
    assert value is None
    assert meta["reason"] == gate._UNMEASURABLE_NO_CLOCK
    # ... and with no OSR declared the same deck still measures.
    assert gate.sndr_db_from_transient(deck, dump, osr=1.0)[0] is not None


def test_tone_above_the_band_edge_refuses():
    """A tone the producer did not place — out of the band the converter is
    graded over. Nothing in that spectrum is this converter's resolution."""
    deck, dump = _modulator_bitstream(osr=64)
    value, meta = gate.sndr_db_from_transient(deck, dump, osr=4096)
    assert value is None
    assert meta["reason"] == gate._UNMEASURABLE_OUT_OF_BAND


def _tone_dump(cycles=32, n=4096, amp=0.45, offset=0.9, fclk=1e6, bits=16):
    """A clean quantised tone: what a working converter's output looks like."""
    step = 2.0 * amp / (2 ** bits)
    rows = []
    for k in range(n):
        v = offset + amp * math.sin(2.0 * math.pi * cycles * k / n)
        rows.append(f"{k / fclk:.12e} {round(v / step) * step:.12e}")
    return "\n".join(rows)


def test_a_corner_is_graded_from_the_a4_transient(tmp_path: Path):
    """The direction the capability exists for: a corner with NO `sndr_db`
    field at all, graded from the dump its own deck wrote."""
    block = tmp_path / "phase3" / "analog" / "conv0"
    (block / "sizing_loop").mkdir(parents=True)
    deck = ("v_ain ain 0 sin(0.9 0.45 7812.5)\n"
            "v_clk clk 0 pulse(0 1.8 0n 1n 1n 499n 1000n)\n"
            ".control\n"
            "tran 5n 4096000n\n"
            "wrdata c.resolution.wrdata v(dout)\n"
            ".endc\n")
    (block / "sizing_loop" / "c.sp").write_text(deck)
    (block / "sizing_loop" / "c.resolution.wrdata").write_text(_tone_dump())
    (block / "sizing_loop" / "c.ngspice.log").write_text("ok\n")
    (block / "spec.json").write_text(json.dumps(
        {"specs": [{"name": "enob", "min": 10.0},
                   {"name": "osr", "target": 8.0}]}))
    (block / "corner_results.json").write_text(json.dumps({"corners": [
        {"name": "tt_27c", "simulator_run": True,
         "ngspice_log": "phase3/analog/conv0/sizing_loop/c.ngspice.log"}]}))
    r, rpt = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert rpt["verdict"] == "PASS", json.dumps(rpt, indent=1)
    corner = rpt["blocks"][0]["measured_corners"][0]
    assert corner["source"] == "fft_of_a4_transient"
    assert corner["measurement"]["method"] == "fft_signal_bin_vs_in_band_rest"


def test_producer_refusal_outranks_the_deck_shape(tmp_path: Path):
    """`analog_resolution_stimulus` is the step that decides whether a corner
    deck can carry a tone at all. When it refused with the arithmetic, THAT is
    what the reader needs — "no wrdata" is true and sends them to the wrong
    producer."""
    _mk(tmp_path, "conv0",
        {"specs": [{"name": "enob", "min": 14.0},
                   {"name": "osr", "target": 256.0}]},
        {"resolution_stimulus": {
            "producer": "analog_resolution_stimulus", "applied": False,
            "reason": "record_too_short_for_an_in_band_tone",
            "detail": "the record holds 512 converter samples",
            "samples_available": 512, "samples_required": 12288,
            "band_bins": 1, "osr": 256.0},
         "corners": [{"name": "tt_27c", "simulator_run": True},
                     {"name": "ss_125c", "simulator_run": True}]})
    r, rpt = _run(tmp_path)
    assert r.returncode == 2, r.stdout
    assert rpt["verdict"] == "UNMEASURED"
    blk = rpt["blocks"][0]
    assert blk["reason"] == "record_too_short_for_an_in_band_tone"
    assert blk["osr"] == 256.0
    for corner in blk["unmeasurable_corners"]:
        assert corner["samples_available"] == 512
        assert corner["samples_required"] == 12288
    # NEVER a pass: an absence with a better name is still an absence.
    assert rpt["blocks_graded"] == 0
    assert rpt["reason_class"] == "ZERO_DENOMINATOR"


def test_a_measured_corner_still_outranks_a_producer_refusal(tmp_path: Path):
    """The refusal is a fallback for corners with no resolution, never a veto
    over one that has it: a corner carrying a real `sndr_db` is still graded."""
    _mk(tmp_path, "conv0",
        {"specs": [{"name": "enob", "min": 10.0}]},
        {"resolution_stimulus": {"applied": False, "reason": "no_transient"},
         "corners": [{"name": "tt_27c", "sndr_db": 87.9},
                     {"name": "ss_125c", "simulator_run": True}]})
    r, rpt = _run(tmp_path)
    assert r.returncode == 0, r.stdout
    assert rpt["verdict"] == "PASS"
    blk = rpt["blocks"][0]
    assert blk["corners_measured"] == 1
    assert blk["unmeasurable_corners"][0]["reason"] == "no_transient"

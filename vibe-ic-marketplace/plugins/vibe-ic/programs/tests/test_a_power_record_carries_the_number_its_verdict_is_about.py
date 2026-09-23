"""A power record may not state a verdict it has no power number for.

MEASURED on a signed-off run (lane icspm5, 2026-09-23). `phase3_one_shot_runner`
step 33 wrote a 2,616-byte OpenSTA `report_power` artefact whose Total row read

    Total   7.35e-03   2.19e-03   1.37e-06   9.54e-03  100.0%

and, beside it, a 152-byte `reports/phase3/power.json` reading in full

    {"tool": "opensta", "source": "reports/phase3/power.rpt",
     "analysis_mode": "vectorless_sdc", "verdict": "PASS",
     "evidence": "report_power output below"}

There is no output below; the file ends there. `_ic_release_artefacts.
_power_class` asked the one question worth asking of a power record and refused
the product documents:

    POWER_NO_TOTAL [power] reports/phase3/power.json: the power record carries
    no power number anywhere -- no total, no per-group figure. A Power section
    written over it prints a consumption nobody estimated.

It was right to. 9.54 mW had been measured, by that step, and no
machine-readable artefact of the run said so.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import _ic_release_artefacts as R  # noqa: E402
from _ppa import power as P  # noqa: E402

_REPORT = """\
# OpenSTA report_power
#   netlist: phase3/stage3/pnr/spm_pnr.v
#   liberty: gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib
OpenSTA 3.1.0 3a692c5766 Copyright (c) 2026, Parallax Software, Inc.
POWER_ANALYSIS_MODE: vectorless_sdc
Group                  Internal  Switching    Leakage      Total
                          Power      Power      Power      Power (Watts)
----------------------------------------------------------------
Sequential             1.41e-03   6.97e-05   2.62e-08   1.48e-03  15.6%
Combinational          4.03e-04   2.45e-04   6.74e-08   6.48e-04   6.8%
Clock                  1.85e-03   1.26e-03   1.12e-06   3.12e-03  32.7%
Macro                  0.00e+00   0.00e+00   0.00e+00   0.00e+00   0.0%
Pad                    3.68e-03   6.13e-04   1.64e-07   4.29e-03  45.0%
----------------------------------------------------------------
Total                  7.35e-03   2.19e-03   1.37e-06   9.54e-03 100.0%
"""

#: The same OpenSTA session, having judged nothing: a header, a banner and no
#: table. This is not a contrived file -- it is what the artefact looks like
#: when `report_power` runs before the design is linked.
_NO_TABLE = """\
# OpenSTA report_power
OpenSTA 3.1.0 3a692c5766 Copyright (c) 2026, Parallax Software, Inc.
POWER_ANALYSIS_MODE: vectorless_sdc
Warning: no design linked; nothing to report.
"""


def _record(tmp_path, text):
    rpt = tmp_path / "power.rpt"
    rpt.write_text(text)
    return P.signoff_record(P.read_power_report(rpt),
                            source="reports/phase3/power.rpt",
                            analysis_mode="vectorless_sdc")


# ------------------------------------------------------------------ POSITIVE

def test_the_total_the_report_states_is_the_total_the_record_carries(tmp_path):
    rec = _record(tmp_path, _REPORT)
    assert rec["power_measurement"] == "MEASURED", rec
    assert rec["total_power_w"] == 9.54e-03, rec
    assert rec["internal_power_w"] == 7.35e-03, rec
    assert rec["switching_power_w"] == 2.19e-03, rec
    assert rec["leakage_power_w"] == 1.37e-06, rec
    assert rec["verdict"] == "PASS"


def test_the_group_breakdown_survives_into_the_record(tmp_path):
    """A datasheet quotes a total; a designer reads the split. Publishing only
    the total would make the next question un-answerable from the artefact."""
    rec = _record(tmp_path, _REPORT)
    by = {g["group"]: g for g in rec["power_by_group"]}
    assert set(by) == {"Sequential", "Combinational", "Clock", "Macro", "Pad"}
    assert by["Clock"]["total_power_w"] == 3.12e-03, by["Clock"]
    assert by["Pad"]["leakage_power_w"] == 1.64e-07, by["Pad"]
    assert rec["power_groups"] == 5


def test_the_release_reader_now_finds_the_measured_number(tmp_path):
    """The consumer that refused the documents. This is the end the change
    exists for, and it must be satisfied by the measured total -- not by a
    count, an index or any other bookkeeping that sits under a matching key."""
    rec = _record(tmp_path, _REPORT)
    found = R._numbers_under_key(rec, ("power", "total", "watt"))
    assert 9.54e-03 in [v for _k, v in found], found
    totals = [v for k, v in found if "total" in k.lower()]
    assert max(totals) == 9.54e-03, totals


# ------------------------------------------------------------------ NEGATIVE

def test_no_table_means_no_verdict(tmp_path):
    """THE RULE. A verdict is a statement ABOUT a number; with no number there
    is nothing to state. The writer declines -- it does not downgrade to FAIL
    (a judgement it has no standing to make) and it does not invent a zero."""
    rec = _record(tmp_path, _NO_TABLE)
    assert rec["power_measurement"] == "NOT_MEASURED", rec
    assert rec["verdict"] == "NOT_MEASURED", rec
    assert "total_power_w" not in rec, rec
    assert "no OpenSTA report_power total row" in rec[
        "power_not_measured_reason"], rec


def test_an_unreadable_report_is_not_an_empty_one(tmp_path):
    """Different facts, kept different: `read_power_report` returns None only
    when the FILE could not be read, and the record says which happened."""
    rec = P.signoff_record(None, source="reports/phase3/power.rpt",
                           analysis_mode=None)
    assert rec["verdict"] == "NOT_MEASURED"
    assert "could not be read" in rec["power_not_measured_reason"], rec


def test_a_not_measured_record_is_still_refused_by_the_release_reader(
        tmp_path):
    """And that refusal must SURVIVE this change. The failure mode a richer
    record invites is a number that is bookkeeping rather than measurement --
    a group count, a row index -- sitting under a key containing "total" or
    "power" and satisfying the reader on a run that measured nothing. When
    nothing was measured, no number may appear under such a key at all."""
    rec = _record(tmp_path, _NO_TABLE)
    assert R._numbers_under_key(rec, ("power", "total", "watt")) == [], rec


def test_the_writer_cannot_emit_a_verdict_without_its_number():
    """The invariant as one function, which is what the runner asserts against
    before it writes. Proven here in both directions so that neither the check
    nor the thing it checks can be quietly disabled alone."""
    assert P.verdict_is_backed_by_a_number(
        {"verdict": "PASS", "total_power_w": 9.54e-03}) is True
    assert P.verdict_is_backed_by_a_number(
        {"verdict": "PASS"}) is False
    assert P.verdict_is_backed_by_a_number(
        {"verdict": "PASS", "total_power_w": None}) is False
    assert P.verdict_is_backed_by_a_number(
        {"verdict": "PASS", "total_power_w": True}) is False, (
            "a bool is not a watt")
    # A record that declines to judge owes no number.
    assert P.verdict_is_backed_by_a_number(
        {"verdict": "NOT_MEASURED"}) is True
    assert P.verdict_is_backed_by_a_number(None) is False


def test_the_shipped_record_shape_is_the_one_that_was_refused():
    """A regression pin on the exact 152-byte document this change replaces:
    if it is ever written again, it fails here rather than three phases later
    in a release that cannot be documented."""
    old = {"tool": "opensta", "source": "reports/phase3/power.rpt",
           "analysis_mode": "vectorless_sdc", "verdict": "PASS",
           "evidence": "report_power output below"}
    assert P.verdict_is_backed_by_a_number(old) is False
    assert R._numbers_under_key(old, ("power", "total", "watt")) == []


# ------------------------------------------------- the emitter the step calls

def _emit(tmp_path, text, mode="vectorless_sdc"):
    """Drive `phase3_one_shot_runner`'s own emitter, not just the helper."""
    import json
    import phase3_one_shot_runner as RUN
    rpt = tmp_path / "reports" / "phase3" / "power.rpt"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(text)
    out = rpt.parent / "power.json"
    notes = []
    rec = RUN._emit_power_signoff_json(tmp_path, rpt, out, mode, notes)
    return rec, json.loads(out.read_text()), notes


def test_the_step_writes_the_number_to_disk(tmp_path):
    rec, on_disk, notes = _emit(tmp_path, _REPORT)
    assert on_disk == rec
    assert on_disk["total_power_w"] == 9.54e-03, on_disk
    assert on_disk["source"] == "reports/phase3/power.rpt"
    assert on_disk["analysis_mode"] == "vectorless_sdc"
    assert notes == [], notes
    # The end this exists for, through the file the release reader opens.
    assert 9.54e-03 in [
        v for _k, v in R._numbers_under_key(on_disk,
                                            ("power", "total", "watt"))]


def test_the_step_discloses_a_run_that_measured_nothing(tmp_path):
    rec, on_disk, notes = _emit(tmp_path, _NO_TABLE)
    assert on_disk["verdict"] == "NOT_MEASURED", on_disk
    assert "total_power_w" not in on_disk
    assert notes and "NOT_MEASURED" in notes[0], notes
    # And the release reader still refuses, which is correct: nothing was
    # measured, so nothing may be written up.
    assert R._numbers_under_key(on_disk, ("power", "total", "watt")) == []


def test_the_step_refuses_rather_than_writes_an_unbacked_verdict(
        tmp_path, monkeypatch):
    """The assertion is load-bearing, so prove it fires. A `signoff_record`
    that ever returns a PASS with no total must stop the run at the write,
    not leave a durable file stating a judgement nobody measured."""
    import pytest
    import phase3_one_shot_runner as RUN
    monkeypatch.setattr(
        RUN._ppa_power, "signoff_record",
        lambda *a, **k: {"verdict": "PASS", "source": "reports/phase3/power.rpt"})
    rpt = tmp_path / "reports" / "phase3" / "power.rpt"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(_REPORT)
    out = rpt.parent / "power.json"
    with pytest.raises(AssertionError, match="no power number"):
        RUN._emit_power_signoff_json(tmp_path, rpt, out, "vectorless_sdc", [])
    assert not out.exists(), "a refused record must leave no file behind"

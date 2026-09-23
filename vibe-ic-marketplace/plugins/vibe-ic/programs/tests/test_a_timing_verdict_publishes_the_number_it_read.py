"""A timing audit that says it saw a slack must publish the slack it saw.

MEASURED on spm run22 (lane icspm5, 2026-09-23). The post-route STA audit
published

    reports/phase3/sta/post_route_summary.json
      summary: {"has_wns_tns": true, "has_setup_hold": true, ...}

over a transcript (`phase3/stage3/sta/post_route_timing.rpt`) reading

    worst slack max 14.31 / tns max 0.00
    worst slack min  0.51 / tns min  0.00

and the receipt carried NO slack number at all. `_ic_release_artefacts.
_sta_class` reads that receipt for any number under a slack/wns/tns key, found
none, and refused the release documents:

    STA_NO_SLACK [sta] reports/phase3/sta/post_route_summary.json: the
    post-route timing record carries no slack number anywhere — not a worst
    slack, not a total negative slack, not one per corner.

— while the same run's sign-off reported +0.940 ns. The detector had parsed
the numbers well enough to set a boolean and then dropped them.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import _ic_release_artefacts as R  # noqa: E402

AUDIT = PROGRAMS / "eda_report_audit.py"

_TRANSCRIPT = """\
Startpoint: u_core/_042_ (rising edge-triggered flip-flop)
          14.31   slack (MET)
worst slack max 14.31
tns max 0.00
           0.51   slack (MET)
worst slack min 0.51
tns min 0.00
"""


def _run(project, rel):
    out = project / "reports" / "phase3" / "sta" / "post_route_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, str(AUDIT), str(project), "--mode", "sta",
         "--under", rel, "--json", str(out)],
        capture_output=True, text=True)
    doc = json.loads(out.read_text()) if out.is_file() else {}
    return r.returncode, (doc.get("summary") or {})


def _stage(tmp_path, text):
    d = tmp_path / "phase3" / "stage3" / "sta"
    d.mkdir(parents=True, exist_ok=True)
    (d / "post_route_timing.rpt").write_text(text)
    return "phase3/stage3/sta/post_route_timing.rpt"


# ------------------------------------------------------------------ POSITIVE

def test_the_numbers_the_audit_read_are_published(tmp_path):
    rel = _stage(tmp_path, _TRANSCRIPT)
    rc, s = _run(tmp_path, rel)
    assert s.get("has_wns_tns") is True
    assert s.get("slack_measurement") == "MEASURED", s
    # AMENDED after the pre-landing review (2026-09-23), NOT weakened. The
    # first shape keyed every row `metric="wns", corner=<max|min>`. In OpenSTA
    # `max`/`min` are the SETUP and HOLD analyses, not process corners, and
    # `worst slack` is not WNS. The same four numbers are asserted here, each
    # now under the name of what it actually is.
    rows = s.get("slack_ns") or []
    assert rows, s
    assert rows[0]["setup_wns_ns"] == 14.31, rows
    assert rows[0]["hold_wns_ns"] == 0.51, rows
    assert rows[0]["tns_ns"] == 0.00, rows


def test_the_governing_number_of_each_analysis_is_published(tmp_path):
    """AMENDED after the pre-landing review, and this is the finding itself.

    This asserted `wns_ns == 0.51` -- the min over BOTH analyses -- and 0.51 is
    the HOLD worst slack. The setup worst slack was 14.31. "The worst of what
    was read" merged two different questions into one key, and a reader taking
    `wns_ns` as the setup margin would have been wrong by 13.8 ns on this very
    run. Each analysis now reports its own worst, and there is no key that
    means both."""
    rel = _stage(tmp_path, _TRANSCRIPT)
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") == 14.31, s
    assert s.get("hold_wns_ns") == 0.51, s
    assert s.get("tns_ns") == 0.00, s


def test_the_release_reader_finds_the_MEASURED_NUMBER_not_a_counter(tmp_path):
    """The consumer that refused the documents asks for any number under a
    slack/wns/tns key. This is the end the change exists for -- and it must be
    satisfied by a slack THIS RUN MEASURED, never by bookkeeping that happens
    to sit under a slack-named key."""
    rel = _stage(tmp_path, _TRANSCRIPT)
    _rc, s = _run(tmp_path, rel)
    found = R._numbers_under_key({"summary": s}, ("slack", "wns", "tns"))
    assert found, s
    assert 14.31 in [v for _k, v in found], found


# ------------------------------------------------------------------ NEGATIVE

def test_a_transcript_with_no_slack_says_not_measured_by_name(tmp_path):
    """Never a verdict without its number -- and never a number invented to
    stand in for one. A report carrying no slack says so, in words."""
    rel = _stage(tmp_path, "Startpoint: a\nEndpoint: b\nno timing here\n")
    rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert not (s.get("slack_ns") or [])
    assert (s.get("slack_scan") or {}).get("datapoints") == 0, s
    assert "no setup/hold worst-slack or TNS number" in str(
        s.get("slack_not_measured_reason") or ""), s


def test_absence_never_publishes_a_number_the_release_reader_would_accept(
        tmp_path):
    """THE FAIL-OPEN THIS CHANGE ALMOST SHIPPED, pinned.

    MEASURED by mutation (lane icspm5, 2026-09-23): a first draft published
    `"slack_datapoints": len(slack_rows)` beside the rows. Over a transcript
    with no timing in it that key read `0` -- a number, under a key containing
    "slack" -- and `_ic_release_artefacts._numbers_under_key` accepted it. The
    STA_NO_SLACK refusal that exists to stop precisely that run went silent,
    and the release documents would have been written over a run whose own
    audit says NOT_MEASURED.

    So: when nothing was measured, NO number may appear anywhere under a key
    naming slack, wns or tns -- and the consumer must still refuse."""
    rel = _stage(tmp_path, "Startpoint: a\nEndpoint: b\nno timing here\n")
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert "setup_wns_ns" not in s and "hold_wns_ns" not in s, s
    assert "tns_ns" not in s, s
    assert R._numbers_under_key(
        {"summary": s}, ("slack", "wns", "tns")) == [], s


def test_a_violated_slack_is_published_with_its_sign(tmp_path):
    """The change must not quietly publish only the happy numbers."""
    rel = _stage(tmp_path,
                 "worst slack max -1.25\ntns max -8.40\n")
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") == -1.25, s
    assert s.get("tns_ns") == -8.40, s


# ===========================================================================
# PRE-LANDING REVIEW, 2026-09-23 — the harvest must obey the SAME rules the
# audit's own verdict obeys. My first version wrote a SECOND parser beside
# `sta_corner_record_completeness_check.extract_slacks` and it disagreed with
# it in three ways, each of which the reviewer reproduced.
# ===========================================================================

#: The shape an unconstrained run writes. `_emit_spef_sta` runs report_tns,
#: report_wns and `report_worst_slack -max`, so a design whose clock port never
#: matched produces the non-negative ECHO of an empty path set beside the
#: authoritative INF sentinel. `extract_slacks` withholds both zeros; the
#: audit's own verdict is STA_VALUE_UNDETERMINED.
_NO_PATHS = """\
No paths found.
tns max 0.00
wns max 0.00
worst slack max INF
"""

#: The same statement with OpenSTA's numeric infinity instead of the word.
_NO_PATHS_1E30 = """\
No paths found.
tns max 0.00
wns max 0.00
worst slack max 1.0e+30
"""


def test_a_no_paths_report_publishes_no_slack_and_still_refuses(tmp_path):
    """THE FAIL-OPEN, BACK THROUGH THE 0.00 ECHO.

    A report that analysed nothing prints `tns max 0.00 / wns max 0.00` under
    a `worst slack max INF` sentinel. Publishing those zeros as MEASURED makes
    a run that timed NOTHING look like a run that met timing with zero
    margin — and silences the STA_NO_SLACK refusal that is the only thing
    standing between that run and a datasheet."""
    rel = _stage(tmp_path, _NO_PATHS)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert R._numbers_under_key({"summary": s}, ("slack", "wns", "tns")) == [], s


def test_numeric_infinity_is_the_same_sentinel_as_the_word(tmp_path):
    """`1.0e+30` is OpenSTA's INF. The first regex had no exponent in it, so
    it read the mantissa `1.0` and published a 1 ns worst slack."""
    rel = _stage(tmp_path, _NO_PATHS_1E30)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    for _k, v in R._numbers_under_key({"summary": s}, ("slack", "wns", "tns")):
        assert v != 1.0, ("the mantissa of 1.0e+30 was published as a slack: "
                          f"{s}")


def test_setup_and_hold_are_never_the_same_number(tmp_path):
    """In OpenSTA `max` is the SETUP analysis and `min` is HOLD. They are not
    process corners and the worse of the two is not "the WNS". The commit's
    own fixture published the HOLD worst slack 0.51 under `wns_ns` while the
    setup worst slack was 14.31."""
    rel = _stage(tmp_path, _TRANSCRIPT)
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") == 14.31, s
    assert s.get("hold_wns_ns") == 0.51, s
    assert "wns_ns" not in s, (
        "a single `wns_ns` cannot mean both analyses; name each one")


def test_a_hold_violation_is_not_published_as_a_setup_violation(tmp_path):
    """setup met, hold violated — the two must not be merged, in either
    direction."""
    rel = _stage(tmp_path, "worst slack max 3.20\nworst slack min -0.05\n")
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") == 3.20, s
    assert s.get("hold_wns_ns") == -0.05, s


def test_a_stamped_not_measured_report_adds_no_slack(tmp_path):
    """A producer stamp saying nothing was measured must not be overruled by
    the echo lines in the same file — that is the stamp filter the verdict
    path applies and the harvest did not."""
    rel = _stage(tmp_path, _NO_PATHS)
    (tmp_path / "phase3" / "stage3" / "sta"
     / "post_route_timing.rpt").write_text(
        "VIBEIC_MEASURED: false\nVIBEIC_REASON_CLASS: NOTHING_TO_MEASURE\n"
        + _NO_PATHS)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s


def test_a_stamp_can_never_subtract_a_violation_that_is_written_down(tmp_path):
    """The other direction, and the one the audit already protects: a stamp
    may decline to ADD a verdict; it can never delete a negative slack the
    producer's own output contains."""
    rel = _stage(tmp_path,
                 "VIBEIC_MEASURED: false\nVIBEIC_REASON_CLASS: NOTHING_TO_MEASURE\n"
                 "worst slack max -2.50\n")
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") == -2.50, s
    assert s.get("slack_measurement") == "MEASURED", s


def test_the_time_unit_is_read_or_named_as_unread(tmp_path):
    """OpenSTA reports in the FIRST LIBERTY's time unit and the runner
    supports ps liberties. Labelling every number `_ns` without reading a unit
    publishes a 1000x error as a margin."""
    rel = _stage(tmp_path, _TRANSCRIPT)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_time_unit_stated") is False, s
    assert "ns" in str(s.get("slack_time_unit_basis") or ""), s

    ps = _stage(tmp_path, "time 1ps\nworst slack max -35.20\n")
    _rc2, s2 = _run(tmp_path, ps)
    assert s2.get("slack_time_unit") == "ps", s2
    assert s2.get("slack_time_unit_stated") is True, s2
    assert s2.get("setup_wns_ns") == -0.0352, s2


def test_a_mirror_of_the_same_report_is_not_a_second_measurement(tmp_path):
    """MEASURED by an existing test (`test_sta_gate_step_scope::
    test_the_step_mirror_does_not_cost_the_gate_its_declared_report`), which
    went red on the branch tip: the row recorded the report's PATH, so
    publishing the same bytes at a mirrored step path moved the gate's
    summary. Same numbers, different string, different answer.

    Provenance is the bytes now, so two mirrors are one reading -- and the
    count does not double either."""
    rel = _stage(tmp_path, _TRANSCRIPT)
    mirror = tmp_path / "steps" / "10_pre_layout_sta" / "post_route_timing.rpt"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(_TRANSCRIPT)
    _rc, s = _run(tmp_path, rel)
    rows = s.get("slack_ns") or []
    assert len(rows) == 1, rows
    assert (s.get("slack_scan") or {}).get("datapoints") == 1, s
    assert "file" not in rows[0], rows[0]
    assert rows[0]["source_sha256"].startswith("sha256:"), rows[0]

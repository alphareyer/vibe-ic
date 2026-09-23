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
    rows = s.get("slack_ns") or []
    assert rows, s
    got = {(r["metric"], r["corner"]): r["value_ns"] for r in rows}
    assert got.get(("wns", "max")) == 14.31, got
    assert got.get(("wns", "min")) == 0.51, got
    assert got.get(("tns", "max")) == 0.00, got
    assert got.get(("tns", "min")) == 0.00, got


def test_the_governing_corner_is_the_worst_one_read(tmp_path):
    """A datasheet quotes the corner that governs. 0.51 is worse than 14.31."""
    rel = _stage(tmp_path, _TRANSCRIPT)
    _rc, s = _run(tmp_path, rel)
    assert s.get("wns_ns") == 0.51, s
    assert s.get("tns_ns") == 0.00, s


def test_the_release_reader_finds_the_MEASURED_NUMBER_not_a_counter(tmp_path):
    """The consumer that refused the documents asks for any number under a
    slack/wns/tns key. This is the end the change exists for -- and it must be
    satisfied by a slack THIS RUN MEASURED, never by bookkeeping that happens
    to sit under a slack-named key."""
    import _ic_release_artefacts as R
    rel = _stage(tmp_path, _TRANSCRIPT)
    _rc, s = _run(tmp_path, rel)
    found = R._numbers_under_key({"summary": s}, ("slack", "wns", "tns"))
    assert found, s
    assert 0.51 in [v for _k, v in found], found


# ------------------------------------------------------------------ NEGATIVE

def test_a_transcript_with_no_slack_says_not_measured_by_name(tmp_path):
    """Never a verdict without its number -- and never a number invented to
    stand in for one. A report carrying no slack says so, in words."""
    rel = _stage(tmp_path, "Startpoint: a\nEndpoint: b\nno timing here\n")
    rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert not (s.get("slack_ns") or [])
    assert (s.get("slack_scan") or {}).get("datapoints") == 0, s
    assert "no WNS/TNS/worst-slack number" in str(
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
    import _ic_release_artefacts as R
    rel = _stage(tmp_path, "Startpoint: a\nEndpoint: b\nno timing here\n")
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert "wns_ns" not in s and "tns_ns" not in s, s
    assert R._numbers_under_key(
        {"summary": s}, ("slack", "wns", "tns")) == [], s


def test_a_violated_slack_is_published_with_its_sign(tmp_path):
    """The change must not quietly publish only the happy numbers."""
    rel = _stage(tmp_path,
                 "worst slack max -1.25\ntns max -8.40\n")
    _rc, s = _run(tmp_path, rel)
    got = {(r["metric"], r["corner"]): r["value_ns"]
           for r in (s.get("slack_ns") or [])}
    assert got.get(("wns", "max")) == -1.25, got
    assert got.get(("tns", "max")) == -8.40, got

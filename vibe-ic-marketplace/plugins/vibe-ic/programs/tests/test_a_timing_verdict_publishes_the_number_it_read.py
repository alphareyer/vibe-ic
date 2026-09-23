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


def _stage(tmp_path, text, unit="ns"):
    """Stage a report the way the RUNNER writes one.

    ROUND-2: every canonical STA report this flow emits carries
    `STA_BASIS_LIBERTY: <path>`, and the liberty declares the time unit the
    numbers are in. The audit now takes the unit from there and REFUSES to
    publish a number when it cannot, so a fixture without that line is not a
    report this tree produces -- it is a report with no unit, and it is tested
    as such by `test_an_unresolvable_unit_refuses_the_number`.
    """
    d = tmp_path / "phase3" / "stage3" / "sta"
    d.mkdir(parents=True, exist_ok=True)
    head = ""
    if unit:
        lib = tmp_path / f"corner_{unit}.lib"
        lib.write_text("library (fixture) {\n"
                       f"  time_unit : 1{unit} ;\n"
                       "  voltage_unit : 1V ;\n}\n")
        head = f"STA_BASIS_LIBERTY: {lib}\n"
    (d / "post_route_timing.rpt").write_text(head + text)
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


def test_a_report_that_states_its_own_unit_is_believed(tmp_path):
    """OpenSTA reports in the FIRST LIBERTY's time unit and the runner
    supports ps liberties. Labelling every number `_ns` without reading a unit
    publishes a 1000x error as a margin."""
    # RETRACTED AND REVERSED by the round-2 review, 2026-09-23. This asserted
    # `slack_time_unit_stated is False` over a runner-shaped report -- i.e. it
    # pinned the DISCLOSURE as the correct outcome. The review's answer is
    # that a flag beside a wrong number is still a wrong number: no deck in
    # this tree prints a unit, so "not stated" was every real run, and the
    # numbers went out under `_ns` names regardless. The unit now comes from
    # the liberty the report names, and an unresolvable unit publishes NO
    # number (see `test_an_unresolvable_unit_refuses_the_number`).
    #
    # What survives unchanged is the half that was right: a report that DOES
    # state its own unit is believed, and the value is converted.
    ps = _stage(tmp_path, "time 1ps\nworst slack max -35.20\n", unit=None)
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


# ===========================================================================
# ROUND-2 REVIEW, 2026-09-23. Round 1's fix handled round 1's exact input and
# nothing more. These use the shapes THE RUNNER ITSELF WRITES, and the REAL
# measurement stamp prefix -- my round-1 stamp tests wrote `VIBEIC_MEASURED:
# false`, which is not a token this tree reads, so they never exercised the
# branch they were named for.
# ===========================================================================

import _mcp_measurement as _mcp  # noqa: E402

#: (a) step 23's aliased post_route_timing.rpt. The HOLD stanza runs
#: `report_worst_slack -min` and then an UNFLAGGED `report_tns`, which prints
#: `tns max 0.00` INSIDE the HOLD block. The max axis has no worst-slack line
#: at all, so no sentinel marks it -- and a run that analysed nothing on setup
#: published a met 0.00 TNS.
_HOLD_BLOCK_UNFLAGGED_TNS = """\
=== HOLD corner: process=FF ===
No paths found.
worst slack min INF
tns max 0.00
"""

#: (b) per_corner/sta_<c>.rpt and pre_pnr_timing.rpt: report_checks /
#: report_tns / report_wns with NO report_worst_slack, so there is no sentinel
#: anywhere and both zeros are published.
_NO_WORST_SLACK_AT_ALL = """\
=== SETUP corner: process=TT ===
No paths found.
tns max 0.00
wns max 0.00
"""


def test_a_hold_block_tns_echo_is_not_a_setup_measurement(tmp_path):
    """ROUND-2 HIGH (a). The runner writes this; `extract_slacks` decides
    vacuity per (block, axis) on the worst-slack sentinel and never reads
    `No paths found.`, so the unflagged `tns max 0.00` inside a HOLD block
    escaped and STA_NO_SLACK went silent on a run that timed nothing."""
    rel = _stage(tmp_path, _HOLD_BLOCK_UNFLAGGED_TNS)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert R._numbers_under_key({"summary": s}, ("slack", "wns", "tns")) == [], s


def test_a_report_with_no_worst_slack_line_publishes_nothing(tmp_path):
    """ROUND-2 HIGH (b). No `report_worst_slack` anywhere means no sentinel
    anywhere, so the round-1 rule had nothing to fire on."""
    rel = _stage(tmp_path, _NO_WORST_SLACK_AT_ALL)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert R._numbers_under_key({"summary": s}, ("slack", "wns", "tns")) == [], s


def test_no_paths_never_suppresses_a_violation(tmp_path):
    """The other direction, and the one that must not regress: a report that
    says it found no paths AND carries a negative slack still publishes it."""
    rel = _stage(tmp_path,
                 "=== SETUP corner: process=TT ===\n"
                 "No paths found.\n"
                 "tns max -8.40\nwns max -1.25\n")
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") == -1.25, s
    assert s.get("tns_ns") == -8.40, s


# ------------------------------------------------- the REAL stamp, this time

def _stamped(body, **fields):
    rec = {"schema": _mcp.SCHEMA_FAMILY + "1", "measured": False,
           "reason_class": "NOTHING_TO_MEASURE"}
    rec.update(fields)
    return _mcp.STAMP_PREFIX + _json_dumps(rec) + "\n" + body


def _json_dumps(o):
    import json as _j
    return _j.dumps(o, sort_keys=True)


def test_the_stamp_branch_uses_the_prefix_this_tree_actually_writes(tmp_path):
    """MY ROUND-1 TEST ERROR, fixed. It wrote `VIBEIC_MEASURED: false`, a
    token nothing in this tree reads, so the stamp branch was never entered
    and both 'stamp' tests were green for the wrong reason. The real prefix is
    `_mcp_measurement.STAMP_PREFIX` = '# MCP_MEASUREMENT: ' followed by the
    record as JSON."""
    rel = _stage(tmp_path, _stamped("worst slack max 3.20\ntns max 0.00\n"))
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert R._numbers_under_key({"summary": s}, ("slack", "wns", "tns")) == [], s


def test_a_real_stamp_still_cannot_subtract_a_violation(tmp_path):
    rel = _stage(tmp_path, _stamped("worst slack max -2.50\n"))
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") == -2.50, s
    assert s.get("slack_measurement") == "MEASURED", s


# ------------------------------------------------------------- the time unit

def test_the_unit_comes_from_the_liberty_the_run_loaded(tmp_path):
    """ROUND-2 HIGH/LOW. No deck in this tree prints `report_units` or
    `set_cmd_units` -- `git grep` finds only this audit's own comments -- so
    the round-1 reader could never find a unit and every run fell through to
    'assumed ns'. A flag beside a wrong number is still a wrong number.

    The report DOES name the liberty it timed against (`STA_BASIS_LIBERTY:`),
    and a liberty declares `time_unit`. That is the authority."""
    rel = _stage(tmp_path,
                 "=== SETUP corner: process=TT ===\n"
                 "worst slack max -35.20\n", unit="ps")
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_time_unit") == "ps", s
    assert s.get("slack_time_unit_stated") is True, s
    assert s.get("setup_wns_ns") == -0.0352, s


def test_an_unresolvable_unit_refuses_the_number(tmp_path):
    """And when neither the report nor a liberty settles the unit, the number
    is NOT published under an _ns name. Refuse, do not annotate."""
    # AMENDED at round 3. The fixture was `-35.20` -- a VIOLATION -- and the
    # round-2 rule refused it. That was the defect: a violation's sign does
    # not depend on its unit, and refusing it let a met number from another
    # report stand for a violating design. The refusal is about a MET number,
    # where the unit decides whether 35.20 is 35 ns of headroom or 0.035 ns
    # of it; the violation case is now
    # `test_a_stamped_report_keeps_the_negative_the_comment_promises` and
    # `test_a_violation_is_not_lost_when_no_report_settles_its_unit`.
    rel = _stage(tmp_path, "=== SETUP corner: process=TT ===\n"
                           "worst slack max 35.20\n", unit=None)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert R._numbers_under_key({"summary": s}, ("slack", "wns", "tns")) == [], s
    assert "time unit" in str(s.get("slack_not_measured_reason") or ""), s


# ===========================================================================
# ROUND-3 REVIEW, 2026-09-23 — MEDIUM. The round-2 unit refusal was right
# about units and wrong about VIOLATIONS. A violation's SIGN does not depend
# on the unit: -1.20 is negative in ps, ns and seconds alike. Dropping a row
# whose unit did not settle removed its negative from the headline, so the
# audit published a met worst slack, MEASURED, beside
# `real_violation_found: true`.
# ===========================================================================

def _stage_two(tmp_path, a_text, b_text, a_unit="ns", b_unit=None):
    """Two reports under one scope, as an unscoped audit discovers them."""
    d = tmp_path / "phase3" / "stage3" / "sta"
    d.mkdir(parents=True, exist_ok=True)
    head = ""
    if a_unit:
        lib = tmp_path / f"a_{a_unit}.lib"
        lib.write_text(f"library (a) {{\n  time_unit : 1{a_unit} ;\n}}\n")
        head = f"STA_BASIS_LIBERTY: {lib}\n"
    (d / "post_route_timing.rpt").write_text(head + a_text)
    pc = d / "per_corner"
    pc.mkdir(exist_ok=True)
    head_b = ""
    if b_unit:
        lib2 = tmp_path / f"b_{b_unit}.lib"
        lib2.write_text(f"library (b) {{\n  time_unit : 1{b_unit} ;\n}}\n")
        head_b = f"STA_BASIS_LIBERTY: {lib2}\n"
    (pc / "sta_SS.rpt").write_text(head_b + b_text)
    return "phase3/stage3/sta"


def test_a_violation_in_an_unresolved_unit_is_never_published_as_met(tmp_path):
    """ROUND-3 MEDIUM, the reviewer's first input. post_route_timing.rpt names
    an ns liberty and reports a met 0.50; per_corner/sta_SS.rpt names no
    liberty and reports wns -1.20 / tns -8.00. Dropping the second row left
    the headline reading setup_wns_ns 0.50, MEASURED -- a met margin over a
    design with a violation in it."""
    rel = _stage_two(tmp_path,
                     "=== SETUP corner: process=TT ===\nworst slack max 0.50\n",
                     "=== SETUP corner: process=SS ===\n"
                     "wns max -1.20\ntns max -8.00\n")
    _rc, s = _run(tmp_path, rel)
    assert s.get("real_violation_found") is True, s
    assert s.get("setup_wns_ns") != 0.50, (
        "a met margin was published over a report carrying a violation: %r" % s)
    # Never a better number than one you read: either the negative is
    # published with its unit named, or the headline is refused and says why.
    if s.get("slack_measurement") == "MEASURED":
        got = [v for _k, v in R._numbers_under_key({"summary": s},
                                                   ("slack", "wns", "tns"))]
        assert any(v < 0 for v in got), s
    else:
        assert "violation" in str(s.get("slack_not_measured_reason") or ""), s


def test_a_violation_is_not_lost_when_no_report_settles_its_unit(tmp_path):
    """The reviewer's second input: step-10 scope, where NO report names a
    liberty. Round 2 published NOT_MEASURED with a unit reason and said
    nothing about the violation sitting in the same scope."""
    rel = _stage_two(tmp_path,
                     "=== SETUP corner: process=TT ===\nwns max -1.20\n",
                     "=== SETUP corner: process=SS ===\ntns max -8.00\n",
                     a_unit=None, b_unit=None)
    _rc, s = _run(tmp_path, rel)
    assert s.get("real_violation_found") is True, s
    reason = str(s.get("slack_not_measured_reason") or "")
    assert s.get("slack_measurement") != "MEASURED" or any(
        v < 0 for _k, v in R._numbers_under_key({"summary": s},
                                                ("slack", "wns", "tns"))), s
    assert "violation" in reason or any(
        v < 0 for _k, v in R._numbers_under_key({"summary": s},
                                                ("slack", "wns", "tns"))), s


def test_a_stamped_report_keeps_the_negative_the_comment_promises(tmp_path):
    """The reviewer's third input, and it is the audit contradicting its own
    docstring: the stamp branch exists so that 'a stamp can decline to ADD a
    verdict; it can never subtract one that is written down'. With the unit
    unresolved, round 2 subtracted it."""
    rel = _stage(tmp_path, _stamped("worst slack max -2.50\n"), unit=None)
    _rc, s = _run(tmp_path, rel)
    got = [v for _k, v in R._numbers_under_key({"summary": s},
                                               ("slack", "wns", "tns"))]
    assert any(v == -2.50 for v in got), (
        "the stamp branch dropped the negative it promises to keep: %r" % s)


def test_a_met_report_with_no_unit_still_publishes_nothing(tmp_path):
    """And the round-2 rule survives where it was right: with no violation and
    no unit, no number goes out under an _ns name."""
    rel = _stage(tmp_path, "=== SETUP corner: process=TT ===\n"
                           "worst slack max 35.20\n", unit=None)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert R._numbers_under_key({"summary": s}, ("slack", "wns", "tns")) == [], s


def test_the_deck_states_its_own_unit_so_a_container_path_is_not_needed(tmp_path):
    """ROUND-3 NOTE, closed. `STA_BASIS_LIBERTY` is a CONTAINER path on
    image-PDK runs, so a host-side audit cannot open it and the unit could
    never settle -- STA_NO_SLACK again, for every run whose PDK is not staged
    under input/pdk. spm only worked because its liberty is identity-mounted.

    OpenSTA knows the unit inside the deck, after read_liberty. The runner now
    stamps `STA_TIME_UNIT:` from `sta::unit_scale_abbreviation time` +
    `sta::unit_suffix time` (measured in the image: `ns` for the shipped
    GF180 corner), and this reads it as the report stating its own unit --
    with no liberty file needed at all."""
    rel = _stage(tmp_path,
                 "STA_BASIS_LIBERTY: /foss/pdks/unreachable/from/the/host.lib\n"
                 "STA_TIME_UNIT: ps\n"
                 "=== SETUP corner: process=TT ===\n"
                 "worst slack max -35.20\n", unit=None)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_time_unit") == "ps", s
    assert s.get("setup_wns_ns") == -0.0352, s
    assert s.get("slack_measurement") == "MEASURED", s


# ===========================================================================
# ROUND-4 MEDIUM. The round-3 withdrawal keyed on a unit-less NEGATIVE ROW.
# A violation that never becomes a row -- a path table saying
# `slack (VIOLATED)`, or a measured:false stamp over a VIOLATED path -- left
# the met headline standing. And a unit-less MET reading that could be
# smaller than the published one was simply dropped.
# ===========================================================================

def test_a_violated_path_table_withdraws_the_met_headline(tmp_path):
    """`report_checks` path tables carry `slack (VIOLATED)` and no summary
    line, so they produce no slack ROW at all -- but they do set
    `real_violation_found`. The headline must not read met beside that."""
    rel = _stage_two(
        tmp_path,
        "=== SETUP corner: process=TT ===\nworst slack max 0.50\n",
        "Startpoint: u/_1_\n          -1.25   slack (VIOLATED)\n",
        a_unit="ns", b_unit="ns")
    _rc, s = _run(tmp_path, rel)
    assert s.get("real_violation_found") is True, s
    assert s.get("setup_wns_ns") != 0.50, (
        "a met headline stood beside a VIOLATED path table: %r" % s)


def test_a_stamped_violated_path_withdraws_the_met_headline(tmp_path):
    """The stamp branch contributes only negatives as rows; a VIOLATED path
    entry under a measured:false stamp is a violation with no number, and it
    too must withdraw the met reading."""
    rel = _stage_two(
        tmp_path,
        "=== SETUP corner: process=TT ===\nworst slack max 0.50\n",
        _stamped("Startpoint: u/_1_\n          -2.0   slack (VIOLATED)\n"),
        a_unit="ns", b_unit="ns")
    _rc, s = _run(tmp_path, rel)
    assert s.get("real_violation_found") is True, s
    assert s.get("setup_wns_ns") != 0.50, s


def test_a_unitless_met_reading_that_could_be_smaller_withdraws_it(tmp_path):
    """A unit-less met 0.30 beside an ns 5.00: 0.30 in ANY unit is smaller
    than 5.00 ns, so publishing 5.00 as the worst is a better number than one
    we read. The headline is withdrawn rather than guessed."""
    rel = _stage_two(
        tmp_path,
        "=== SETUP corner: process=TT ===\nworst slack max 5.00\n",
        "=== SETUP corner: process=SS ===\nworst slack max 0.30\n",
        a_unit="ns", b_unit=None)
    _rc, s = _run(tmp_path, rel)
    assert s.get("setup_wns_ns") != 5.00, (
        "published 5.00 ns as the worst while an unread 0.30 could be "
        "smaller: %r" % s)
    assert s.get("slack_measurement") == "NOT_MEASURED", s
    assert "unit" in str(s.get("slack_not_measured_reason") or ""), s


def test_a_unitless_met_reading_that_cannot_be_smaller_is_ignored(tmp_path):
    """The other direction, so the rule is not "any unit-less row withdraws".
    A unit-less 5000 cannot be smaller than an ns 0.50 in any supported unit
    (the smallest scaling is fs = 1e-6 ns, and 5000 fs = 0.005 ns)... it CAN.
    So the honest rule is the conservative one and this asserts it: the
    headline is withdrawn. Stated as its own test so the choice is visible
    rather than buried in the implementation."""
    rel = _stage_two(
        tmp_path,
        "=== SETUP corner: process=TT ===\nworst slack max 0.50\n",
        "=== SETUP corner: process=SS ===\nworst slack max 5000\n",
        a_unit="ns", b_unit=None)
    _rc, s = _run(tmp_path, rel)
    assert s.get("slack_measurement") == "NOT_MEASURED", s

"""icslot56 — step 31 read UNMEASURED over a DRC run that WAS measured.

THE INPUT, run21 (clean copy), step 31's own clause
`provenance_check . --output reports/phase3/drc_signoff.rpt --tool klayout,magic,svrfdrc
--require-measured`:

    UNMEASURED reports/phase3/drc_signoff.rpt
      the run bound to this artefact declares no measurement record, so whether
      klayout performed its work is not stated anywhere — unmeasured, which is
      neither measured nor failed

The run HAD measured it. `provenance.jsonl` carries two klayout rows for that
artefact, THREE SECONDS APART:

    line 58   09:54:21   klayout   reconstructed    measurement: YES
    line 61   09:54:24   klayout   rebound_from     measurement: no

`provenance_check._find_entry` binds an artefact to its MOST RECENT matching
entry, so it read the second one. And the artefact itself says plainly what
happened: `_runner_measurement.derive` over that same file returns
`measured: true`, "rule-deck run — deck <gf180mcu.drc> enumerated, 0 violation
item(s) recorded".

This is `_runner_measurement.attach`'s own docstring coming true a second time:
"a back-fill written seconds later SUPERSEDES the invocation record that carried
the reading, and the gate reports UNMEASURED over a run that was measured." It
was measured there at 15 seconds on `lvs.rpt`; here it is 3 seconds on
`drc_signoff.rpt`. Eight writers call `attach`; the rebound writer was the one
that did not.

WHAT THIS IS NOT. The rebound row already carries a flat `"measured": True`, set
beside `duration_ms`, which says the DURATION was measured. Reading THAT as the
third value is the fabrication `_runner_measurement` exists to refuse — it would
assert "klayout ran the deck" on the strength of "we timed the subprocess". The
record attached here is derived from the ARTEFACT ON DISK and labelled
`stated_by: runner-derived`, so an absent, empty or header-only report yields
`measured: false` with a hard class instead of a pass. Both directions are pinned
below.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import _runner_measurement as rmeas          # noqa: E402
import _mcp_measurement as mmeas             # noqa: E402

#: A KLayout report database with one enumerated deck and zero violation items —
#: the shape run21's own drc_signoff.rpt has.
_CLEAN_RDB = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <description/>
 <generator>drc: script='/pdk/tech/drc/openpdk.drc'</generator>
 <top-cell>chip_top</top-cell>
 <categories>
  <category><name>rule.1</name><description>min width</description></category>
  <category><name>rule.2</name><description>min space</description></category>
 </categories>
 <items>
 </items>
</report-database>
"""


def _project(tmp_path: Path, body: str = _CLEAN_RDB) -> Path:
    rpt = tmp_path / "reports/phase3/drc_signoff.rpt"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(body)
    return tmp_path


def _rebound_row(tmp_path: Path) -> dict:
    """The rebound writer's record, verbatim in the fields that matter."""
    return {
        "record": "invocation",
        "measured": True,                      # the DURATION flag, not the value
        "tool": "klayout",
        "exit_code": 0,
        "duration_ms": 1234,
        "outputs": {"reports/phase3/drc_signoff.rpt": "deadbeef"},
        "rebound_from": ["reports/phase3/signoff/drc.rpt"],
    }


# ── the fix ──────────────────────────────────────────────────────────────────

def test_the_rebound_row_carries_a_derived_measurement(tmp_path):
    """THE DEFECT. Without this the later row supersedes the measured one."""
    proj = _project(tmp_path)
    row = _rebound_row(proj)
    assert "measurement" not in row
    rmeas.attach(proj, row)
    assert "measurement" in row, "the rebound row still states no third value"
    m = row["measurement"]
    assert m["measured"] is True
    assert m["tool"] == "klayout"
    assert "violation item" in m["operation"]
    assert m["stated_by"] == "runner-derived"


def test_the_reader_accepts_what_the_writer_attached(tmp_path):
    """END TO END across the contract halves: the object the writer builds is
    the object `provenance_check` reads through `_mcp_measurement`."""
    proj = _project(tmp_path)
    row = _rebound_row(proj)
    rmeas.attach(proj, row)
    read = mmeas.from_provenance_entry(row)
    assert read.declared is True
    assert read.measured is True
    assert read.undeclared is False and read.hard_miss is False


def test_without_the_attach_the_reader_reports_undeclared(tmp_path):
    """The state step 31 was in: a row the reader cannot get a value from."""
    row = _rebound_row(_project(tmp_path))
    read = mmeas.from_provenance_entry(row)
    assert read.undeclared is True, (
        "a flat `measured: True` must NOT be readable as the third value")


# ── THE TEETH: derived from the artefact, never from the subprocess ──────────

def test_an_absent_report_states_nothing_rather_than_a_pass(tmp_path):
    """No artefact, no claim. `attach` must not invent a reading from the fact
    that a row exists."""
    row = _rebound_row(tmp_path)          # no report written
    rmeas.attach(tmp_path, row)
    assert "measurement" not in row


def test_an_empty_report_is_not_measured(tmp_path):
    proj = _project(tmp_path, body="")
    row = _rebound_row(proj)
    rmeas.attach(proj, row)
    assert "measurement" not in row, (
        "a zero-byte artefact must leave the UNDECLARED state untouched, not "
        "become a positive reading")


def test_a_header_only_report_does_not_read_as_a_clean_deck(tmp_path):
    """A KLayout RDB with no categories enumerated has not run a deck. If this
    ever reads `measured: true` the gate would pass a run that checked nothing."""
    proj = _project(tmp_path, body=(
        '<?xml version="1.0" encoding="utf-8"?>\n<report-database>\n'
        ' <description/>\n</report-database>\n'))
    row = _rebound_row(proj)
    rmeas.attach(proj, row)
    m = row.get("measurement")
    if m is not None:
        assert m["measured"] is False, (
            f"a header-only RDB read as measured: {m.get('operation')!r}")


def test_attach_never_overwrites_a_record_already_there(tmp_path):
    """A tool's OWN record outranks a derived one; `attach` is a back-fill."""
    proj = _project(tmp_path)
    row = _rebound_row(proj)
    row["measurement"] = {"schema": "mcp-eda/measurement/1", "measured": False,
                          "not_measured_class": "TOOL_SAID_SO",
                          "not_measured_reason": "the tool stated this itself"}
    rmeas.attach(proj, row)
    assert row["measurement"]["measured"] is False
    assert row["measurement"]["not_measured_class"] == "TOOL_SAID_SO"


def test_the_rebound_writer_calls_attach():
    """SOURCE-LEVEL: the one back-fill that did not state the value now does.
    Pinned by source because the writer runs inside a phase-3 run and its
    absence is invisible in any unit result -- which is how it survived while
    eight sibling writers called `attach`."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.index("def _rebind_measured_drc_invocation_to_canonical_path(")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    assert "_rmeas.attach(project, record)" in body, (
        "the rebound DRC row is written without a measurement, so it supersedes "
        "the measured invocation row and step 31 reads UNMEASURED")
    assert body.index("_rmeas.attach(project, record)") < body.index(
        'fh.write(json.dumps(record'), "attach must run BEFORE the row is written"

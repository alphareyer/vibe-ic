"""The synth-log marker is the stat PARSER's, not a second spelling of it (icspm2).

MEASURED FAILURE THIS PINS
==========================
A completed gf180mcuD run of `spm` (2026-09-15, lane icspm2) synthesised
perfectly — 449 cells, 65 `$_DFF_P_`, `Found and reported 0 problems`, two
complete `stat` tables in a 691-line `phase2/stage2/synth/yosys.log` — and

    reports/phase2/gates/synth_log_audit.json
        "expect_matched": [], "reject_matched": [], "pass": false
        ERROR EXPECTED_NOT_FOUND: None of the expected patterns were found
        details: Expected patterns: ['Number of cells', 'Number of wires']

    reports/phase2/gates/eda_log_check_receipt.json   verdict: FAIL

    step_internal_fail_bubble_up_check
        [STEP_FAIL_NOT_BUBBLED] reports/phase2/gates/eda_log_check_receipt.json
                                verdict=FAIL                            rc 1

`grep -n "Number of" <that log>` returns NOTHING. The shipped image's yosys
(0.68+) writes the BARE stat form:

    === spm ===
            +----------Local Count, excluding submodules.
          714 wires
          449 cells

`design_one_shot_runner._SYNTH_LOG_EXPECT` asserted the LABELLED form alone,
while `_yosys_stat` — the runner's own stat parser, called from the very step
that writes this log — has known all three forms since v1.7.36. Two gates went
red on a correct synthesis because one assertion was written from memory
instead of from the parser.

THE PROPERTY
============
If `_yosys_stat` can read a stat block out of a log, the synth-log audit passes
over that same log; if it cannot, the audit fails. Anything else is the drift
that caused the defect.
"""
import io
import json
import re
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import _yosys_stat as _ystat          # noqa: E402
import eda_log_check as _eda_log      # noqa: E402
import design_one_shot_runner as _dosr  # noqa: E402


HEAD = """\
-- Running command `read_verilog -sv /p/phase2/stage1/rtl/spm.v; synth -top spm; stat' --

1. Executing Verilog-2005 frontend: /p/phase2/stage1/rtl/spm.v
2. Executing SYNTH pass.
"""

# The shipped image's form, copied from the measured log.
BARE_STAT = HEAD + """\
8. Printing statistics.

=== spm ===

        +----------Local Count, excluding submodules.
        |
      714 wires
      869 wire bits
        5 ports
      449 cells
       65   $_DFF_P_
      221   $_NAND_

End of script. Logfile hash: fca6275efd
"""

LABELLED_STAT = HEAD + """\
8. Printing statistics.

=== spm ===

   Number of wires:                714
   Number of wire bits:            869
   Number of cells:                449
     $_DFF_P_                       65

End of script.
"""

LIBERTY_STAT = HEAD + """\
8. Printing statistics.

=== spm ===

      349 5.84E+03 cells
       64 3.14E+03   sg13g2_dfrbpq_1

   Chip area for module '\\spm': 5841.196200
"""

# A synthesis that died before `stat` — the gate MUST still bite.
NO_STAT = HEAD + """\
ERROR: Module `\\spm' referenced in module `\\top' in cell `\\u0' is not part of the design.
"""


def _expect_patterns():
    raw = _dosr._SYNTH_LOG_EXPECT
    return [p for p in raw.split("|") if p.strip()]


def _audit(tmp_path, text):
    log = tmp_path / "yosys.log"
    log.write_text(text)
    findings, info = _eda_log.audit_log(log, _expect_patterns(), [], None)
    return findings, info


# ---------------------------------------------------------------------------
# DIRECTION 1 — the measured false failure is gone
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,text", [("bare", BARE_STAT),
                                       ("labelled", LABELLED_STAT),
                                       ("liberty", LIBERTY_STAT)])
def test_every_stat_form_the_parser_reads_also_satisfies_the_log_audit(
        tmp_path, name, text):
    """The property, stated as one assertion over all three forms."""
    parsed = _ystat.parse_stat_block(text)
    assert parsed is not None, f"{name}: the parser itself cannot read this"
    findings, info = _audit(tmp_path, text)
    assert info["expect_matched"], (
        f"{name}: the parser reads a stat block the audit cannot see — "
        f"that is the drift this test exists to stop. patterns="
        f"{_expect_patterns()}")
    assert [f.category for f in findings] == []


def test_the_bare_form_is_the_one_the_shipped_image_writes(tmp_path):
    """The exact shape measured on the run, isolated."""
    assert "Number of" not in BARE_STAT
    _findings, info = _audit(tmp_path, BARE_STAT)
    assert info["expect_matched"]


# ---------------------------------------------------------------------------
# DIRECTION 2 — the gate still bites
# ---------------------------------------------------------------------------
def test_a_log_with_no_stat_table_still_fails(tmp_path):
    """The whole point of the row: a synthesis whose accounting never reached
    disk. `_yosys_stat` refuses it too, so the two agree in this direction."""
    assert _ystat.parse_stat_block(NO_STAT) is None
    findings, info = _audit(tmp_path, NO_STAT)
    assert info["expect_matched"] == []
    assert "EXPECTED_NOT_FOUND" in [f.category for f in findings]


def test_an_empty_log_still_fails(tmp_path):
    findings, _info = _audit(tmp_path, "")
    assert "EMPTY_LOG" in [f.category for f in findings]


def test_a_log_that_merely_says_the_word_cells_does_not_satisfy_it(tmp_path):
    """Prose is not accounting. The pattern is anchored to a COUNT LINE."""
    text = HEAD + "INFO: mapping cells to the target library\nDone.\n"
    _findings, info = _audit(tmp_path, text)
    assert info["expect_matched"] == [], info["expect_matched"]


# ---------------------------------------------------------------------------
# THE BINDING ITSELF — not a re-typed copy
# ---------------------------------------------------------------------------
def test_the_marker_is_built_from_the_parsers_published_patterns():
    for pat in _ystat.CELL_COUNT_LINE_PATTERNS:
        assert pat in _dosr._SYNTH_LOG_EXPECT, (
            "the runner re-typed the marker instead of taking it from "
            "_yosys_stat — that is exactly how the two drifted apart")


def test_no_published_pattern_carries_the_split_metacharacter():
    """`eda_log_check` SPLITS --expect-pattern on `|` before compiling, so a
    pattern containing one would be torn in half and silently never match."""
    for pat in _ystat.CELL_COUNT_LINE_PATTERNS:
        assert "|" not in pat, pat


def test_the_patterns_are_multiline_anchored_because_the_gate_is_not():
    """`eda_log_check.audit_log` compiles with re.IGNORECASE ALONE."""
    for pat in _expect_patterns():
        assert pat.startswith("(?m)"), pat
        re.compile(pat, re.IGNORECASE)   # must be a legal regex on its own


def test_the_published_patterns_are_the_ones_the_parser_compiles():
    """`_yosys_stat`'s own regexes are compiled FROM the published strings."""
    assert _ystat._LABELLED_CELLS_RE.pattern == _ystat.CELL_COUNT_LINE_PATTERNS[0]
    assert _ystat._BARE_CELLS_RE.pattern == _ystat.CELL_COUNT_LINE_PATTERNS[1]


# ---------------------------------------------------------------------------
# END TO END through the runner's own step
# ---------------------------------------------------------------------------
class _Synth:
    status = "PASS"


def test_step_synth_log_audit_passes_over_a_bare_form_log(tmp_path):
    proj = tmp_path / "proj"
    (proj / "phase2/stage2/synth").mkdir(parents=True)
    (proj / _dosr._SYNTH_LOG_REL).write_text(BARE_STAT)
    res = _dosr.step_synth_log_audit(proj, _Synth())
    row = res.extras["synth_log_audit"]
    assert row.get("rc") == 0, row
    assert row.get("verdict") not in ("FAIL",), row
    rep = json.loads((proj / "reports/phase2/gates/synth_log_audit.json").read_text())
    assert rep["summary"]["pass"] is True, rep["summary"]
    assert rep["summary"]["expect_matched"], rep["summary"]


def test_step_synth_log_audit_still_fails_over_a_log_with_no_stat(tmp_path):
    proj = tmp_path / "proj"
    (proj / "phase2/stage2/synth").mkdir(parents=True)
    (proj / _dosr._SYNTH_LOG_REL).write_text(NO_STAT)
    res = _dosr.step_synth_log_audit(proj, _Synth())
    row = res.extras["synth_log_audit"]
    assert row.get("rc") == 1, row

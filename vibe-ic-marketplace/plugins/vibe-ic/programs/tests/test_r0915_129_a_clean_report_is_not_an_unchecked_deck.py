'''R-0915-129: a clean DRC report carries no categories, which is not "unchecked".

`signoff_metrics_aggregate._density` decided whether density was checked by asking
whether a DRC report carried a `density` rule CATEGORY. A report with ZERO
violations carries no categories at all -- there is nothing to categorise -- so
that question cannot tell "the deck did not check density" from "the deck checked
density and found nothing", and it answered NOT_MEASURED for the second.

MEASURED on spm run21: `reports/phase3/drc_signoff.log` line 480 is
`Executing deck density from .../gf180mcuD/libs.tech/klayout/tech/drc/rule_decks/
density.rb`, one of 177 such lines naming 36 distinct rule decks, while
`drc_signoff.json` is `real_violation_total: 0` with `categories_found:
[spacing, width, antenna, via, enclosure]`. `tapeout_docs_gen` then refused to
write the release documents over `Density: NOT_MEASURED`, failing step 37.5ic --
over a number the run had produced.

The transcript is found by SHARED EVIDENCE, not by a filename guess: on run21
`drc_signoff.json` scopes only the report, and the sibling
`general_precheck/precheck_klayout_drc.json` scopes that SAME report AND the log.

Both directions are pinned. A log that shows the deck executed over a clean report
gives a MEASURED zero with the log line as its basis; a log that does NOT show it,
an absent log, a non-zero report, and a report whose category IS present all keep
their previous answers.
'''
from __future__ import annotations

import json
import pytest
import signoff_metrics_aggregate as A

DECK = ("/foss/pdks/ciel/gf180mcu/versions/b344/gf180mcuD/libs.tech/klayout/"
        "tech/drc/rule_decks")


def log_text(*decks):
    return "".join(
        f"2026-09-22 11:34:01 +0200: Memory Usage (6042916K) : "
        f"Executing deck {d.upper()}1.1 from {DECK}/{d}.rb\n" for d in decks)


def project(tmp_path, *, total=0, cats=("spacing", "width"), decks=("via", "density"),
            log_scoped_by_sibling=True, write_log=True):
    p = tmp_path / "run"
    (p / "reports/phase3/general_precheck").mkdir(parents=True)
    if write_log:
        (p / "reports/phase3/drc_signoff.log").write_text(log_text(*decks))
    (p / "reports/phase3/drc_signoff.json").write_text(json.dumps({
        "program": "eda_report_audit:drc", "passed": True,
        "summary": {"real_violation_total": total,
                    "categories_found": list(cats),
                    "scoped_under": ["reports/phase3/drc_signoff.rpt"]}}))
    if log_scoped_by_sibling:
        (p / "reports/phase3/general_precheck/precheck_klayout_drc.json").write_text(
            json.dumps({"program": "eda_report_audit:drc", "passed": True,
                        "summary": {"scoped_under": [
                            "reports/phase3/drc_signoff.rpt",
                            "reports/phase3/drc_signoff.log"]}}))
    return p


def test_a_clean_report_whose_log_shows_the_deck_is_a_measured_zero(tmp_path):
    cell = A._density(project(tmp_path))
    assert cell.value == 0 and cell.measured is True
    assert "MEASURED zero" in cell.basis
    assert "rule_decks/density.rb" in cell.basis      # the line that proves it


def test_a_log_without_the_density_deck_stays_unmeasured(tmp_path):
    '''THE NEGATIVE ARM the ruling requires. The transcript is there and it does
    NOT show the deck: that is a genuinely unchecked category and must stay so.'''
    cell = A._density(project(tmp_path, decks=("via", "metal")))
    assert cell.value == "NOT_MEASURED" and cell.measured is False
    assert "no density rule deck executed" in cell.reason or \
           "shows a density rule deck executed" in cell.reason


def test_no_transcript_at_all_stays_unmeasured(tmp_path):
    cell = A._density(project(tmp_path, write_log=False))
    assert cell.value == "NOT_MEASURED" and cell.measured is False


def test_a_log_nobody_scoped_is_not_consulted(tmp_path):
    '''The log is bound by shared evidence. A transcript no DRC audit in this run
    says it read is not this report's transcript, so it decides nothing.'''
    cell = A._density(project(tmp_path, log_scoped_by_sibling=False))
    assert cell.value == "NOT_MEASURED" and cell.measured is False


def test_a_non_zero_report_is_not_promoted_to_zero(tmp_path):
    '''THE LOAD-BEARING FALSIFIER. The clean-report route applies ONLY to a report
    with zero violations; a report carrying violations but no density category must
    never be read as a density zero.'''
    cell = A._density(project(tmp_path, total=7))
    assert cell.value == "NOT_MEASURED" and cell.measured is False


def test_a_report_that_DOES_carry_the_category_keeps_the_old_route(tmp_path):
    '''Pinned AS FOUND: the pre-existing path is untouched and still preferred, so
    this change is an addition and not a replacement.'''
    cell = A._density(project(tmp_path, total=3,
                             cats=("spacing", "density")))
    assert cell.value == 3 and cell.measured is True
    assert "deck carried a density category" in cell.basis

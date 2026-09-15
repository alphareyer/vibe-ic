"""R-0915-40 — the macro non-sequential-arc gate STATES its class.

The gate already wrote a TRUE reason -- "no design-staged macro Liberty
declares a non_seq_* timing arc (the PDK std-cell library is deliberately out
of scope)" -- and since #2282 prose is not a basis, so P0 booked it
EXECUTION_ERROR: "the program broke", when what happened is that the design has
no such thing. MEASURED on `subservient` x gf180mcuD (lane icsub2, r16): the
ledger row reads `macro_non_seq_arc_contract_check rc 2 EXECUTION_ERROR` while
the gate's own report says SKIP with that sentence.

So the gate now says it in TYPED fields: `reason_class: DESIGN_DECLARED_NA`
plus `applicability_evidence` of kind `design-declared-zero-population`, and it
LISTS the declaring population -- the design-staged macro Liberty files -- so a
reader can tell "no macro was staged" from "the gate examined nothing".

Three directions, because two would not be enough here:
  zero population                      -> N/A, and the population is named
  a staged lib DECLARING a non_seq arc -> the gate stays LIVE
  a staged lib present but UNREADABLE  -> INCOMPLETE, never N/A
"""
import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import macro_non_seq_arc_contract_check as G  # noqa: E402

_ARC_LIB = """library (my_macro_lib) {
  cell (my_sram_macro) {
    pin (Q) {
      timing () {
        related_pin : "D";
        timing_type : non_seq_setup_rising;
        rise_constraint (scalar) { values("0.5"); }
      }
    }
  }
}
"""


def _project(tmp_path, libs=None):
    p = tmp_path / "proj"
    (p / "input" / "macros").mkdir(parents=True)
    for name, body in (libs or {}).items():
        (p / "input" / "macros" / name).write_text(body)
    return p


# ------------------------------------------------------------ zero population

def test_no_staged_macro_is_a_design_declared_NA_not_an_execution_error(tmp_path):
    verdict, rc, report = G.evaluate(_project(tmp_path))
    assert verdict == "SKIP"
    assert report["reason_class"] == "DESIGN_DECLARED_NA"
    assert report["program"] == G.GATE_NAME
    ev = report["applicability_evidence"]
    assert ev["kind"] == "design-declared-zero-population"
    assert ev["declared_population"] == 0
    assert ev["population_paths"] == list(G._MACRO_LIB_GLOBS)


def test_the_population_is_LISTED_even_when_it_has_files_but_no_arcs(tmp_path):
    """"No macro was staged" and "macros were staged and none declares an arc"
    are different answers, and the old report could not tell them apart: it
    only ever listed the liberties that HAD an arc."""
    p = _project(tmp_path, {"plain.lib": "library (x) { cell (y) { } }\n"})
    verdict, rc, report = G.evaluate(p)
    assert verdict == "SKIP"
    assert report["reason_class"] == "DESIGN_DECLARED_NA"
    assert report["examined_macro_liberties"] == ["input/macros/plain.lib"]
    assert report["applicability_evidence"]["examined_files"] == [
        "input/macros/plain.lib"]
    assert report["applicability_evidence"]["declared_population"] == 0
    assert "1 staged macro Liberty file(s) examined" in report["reason"]


def test_the_audit_no_longer_books_it_incomplete(tmp_path):
    """The point of the change: the shipped taxonomy must classify the report
    as skip-eligible rather than as a broken program."""
    import _flow_reason_taxonomy as T
    _, _, report = G.evaluate(_project(tmp_path))
    cls = T.report_reason_class(report)
    assert cls == T.DESIGN_DECLARED_NA
    assert cls in T.SKIP_ELIGIBLE
    assert cls not in T.INCOMPLETE


# ------------------------------------------------------------ the gate stays live

def test_a_staged_lib_declaring_a_non_seq_arc_keeps_the_gate_live(tmp_path):
    """THE NEGATIVE CONTROL. A real macro with a real non_seq arc is a real
    question, and nothing here may answer it N/A."""
    p = _project(tmp_path, {"macro.lib": _ARC_LIB})
    verdict, rc, report = G.evaluate(p)
    assert verdict != "SKIP" or report.get("reason_class") != "DESIGN_DECLARED_NA"
    assert report.get("applicability_evidence") is None or \
        report["applicability_evidence"].get("declared_population") != 0
    assert report["macro_liberties"] == ["input/macros/macro.lib"]


# ------------------------------------------------------------ present but unreadable

def test_a_present_but_unreadable_lib_is_INCOMPLETE_never_NA(tmp_path):
    """Present-but-unparseable is not an absent population. The arc collector
    skips it silently -- correct for its own job, wrong as an applicability
    answer -- so the gate must say it does not KNOW."""
    p = _project(tmp_path, {"locked.lib": _ARC_LIB})
    (p / "input" / "macros" / "locked.lib").chmod(0o000)
    try:
        verdict, rc, report = G.evaluate(p)
    finally:
        (p / "input" / "macros" / "locked.lib").chmod(0o644)
    assert verdict == "INCOMPLETE", report
    assert report["reason_class"] == "EXECUTION_ERROR"
    assert "input/macros/locked.lib" in report["reason"]
    assert "UNKNOWN" in report["reason"]
    assert report.get("applicability_evidence") is None


def test_unreadable_is_never_folded_into_the_zero_population(tmp_path):
    p = _project(tmp_path, {"locked.lib": _ARC_LIB})
    (p / "input" / "macros" / "locked.lib").chmod(0o000)
    try:
        examined, unreadable = G.staged_macro_liberties(p)
    finally:
        (p / "input" / "macros" / "locked.lib").chmod(0o644)
    assert unreadable == ["input/macros/locked.lib"]
    assert examined == []

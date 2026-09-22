"""icslot57 — step 2's two PARTIALLY-VACUOUS clauses, and the one word that was wrong.

MEASURED on run21 (clean `cp -a` copy), step 2 reported
"PARTIALLY-VACUOUS (2 of 18 gate clause(s) examined nothing)" for:

  fresh_agent_rtl_bug_density_metric . --no-learning-log
      population: RTL-bug commits in the run tree's version-control history.
      what run21 gave it: NO git repository at all -- verified on the ORIGINAL
      run tree too, so this is not an artefact of copying. rc=2,
      "[skipped] version-control instrument unavailable — not inside a git
      repository (no commit history exists to count RTL-bug commits)", filed
      VACUOUS_PASS / CAPABILITY_ABSENT / DISCLOSED_SKIP.
      VERDICT: an instrument gap, already named honestly. NOT a reader defect
      (it reports the missing instrument), NOT design-structural (any design in
      a non-versioned run tree hits it). NOTHING TO FIX.

  internal_vs_external_timing_check … L8_TIMING_WAVEFORM.json …
      population: half-duplex protocol symbol timing in L8 -- timing_windows /
      timing_constants / waveforms, plus any group key carrying a directional
      or per-symbol token.
      what run21 gave it: all three empty of protocol content, and no such
      group key among the other 10. Zero.
      VERDICT: a STRUCTURAL N/A, and the answer was right -- spm is a signed
      serial-parallel multiplier, not a protocol IC; there are no two sides to
      split. Only the CLASS was wrong, which is what this change fixes.

THE MIS-NAMING, AND WHERE IT CAME FROM. This checker has TWO vacuity escapes
with byte-identical JSON blocks. The first fires when L2 EXPLICITLY declares
`protocol_overview.half_duplex=false` -- DESIGN_DECLARED_NA is exactly right
there. The second fires when L2 says NOTHING and the gate ENUMERATES the L8
document instead. It carried the same class, and the same justification comment
-- "the design's OWN L2 declaration rules the rule out" -- citing a declaration
that branch never reads.

R-0915-124/125: a structural N/A must be a NAMED `NOT_APPLICABLE_BY_STRUCTURE`
WITH its enumeration, never a bare vacuous pass. `_structural_absence.absence()`
refuses a claim that cannot say what it walked, and `is_valid` is the umbrella's
side of the same guard -- so the class is now one a reader can VALIDATE rather
than take on trust.

NOT step 2's FAIL, and worth stating because the premise pointed here: step 2
fails on `EVIDENCE_MISSING (#433)` -- `reports/phase2/lint/rtl_hygiene.json`
declares `verdict: PASS` with `rtl_files: []` and cites evidence
`reports/yosys_synth.log`, which does not exist. These two vacuous clauses are
disclosures sitting BESIDE that failure, not its cause.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
CHECK = PROGRAMS / "internal_vs_external_timing_check.py"
sys.path.insert(0, str(PROGRAMS))

import _structural_absence as sa            # noqa: E402

#: run21's own L8 shape: clock content only, no protocol symbols anywhere.
_NON_PROTOCOL_L8 = {
    "schema_version": 1,
    "doc_class": "L8_TIMING_WAVEFORM",
    "ic_name": "spm",
    "timing_windows": [],
    "timing_constants": [{"name": "fclk", "value": 100.0, "unit": "MHz"}],
    "waveforms": [],
    "clock_domains": [{"name": "clk", "period_ns": 10}],
    "clock_mhz": 100,
}

#: A genuine half-duplex L8: directional groups present, so the rule APPLIES.
_PROTOCOL_L8 = {
    "schema_version": 1,
    "doc_class": "L8_TIMING_WAVEFORM",
    "timing_windows": [],
    "timing_constants": [],
    "waveforms": [],
    "rx_counters": {"H1_low": [1, 9], "H0_low": [10, 30], "BR_low": [31, 65]},
    "tx_symbols": {"H1_low": [2, 8], "H0_low": [11, 29], "IBT": [70, 90]},
}


def _stage(tmp_path: Path, l8: dict, l2: dict | None = None) -> Path:
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L8_TIMING_WAVEFORM.json").write_text(json.dumps(l8, indent=1))
    (gd / "L8_RTL_CONSTANTS.json").write_text(json.dumps({"constants": []}))
    if l2 is not None:
        (gd / "L2_FRS.json").write_text(json.dumps(l2, indent=1))
    return gd


def _run(tmp_path: Path, gd: Path) -> tuple:
    out = tmp_path / "report.json"
    r = subprocess.run(
        [sys.executable, str(CHECK), str(gd / "L8_TIMING_WAVEFORM.json"),
         "--layer", str(gd / "L8_RTL_CONSTANTS.json"), "--json", str(out)],
        capture_output=True, text=True)
    doc = json.loads(out.read_text()) if out.is_file() else None
    return r.returncode, doc


# ── the inferred absence is named BY STRUCTURE, with its enumeration ─────────

def test_an_inferred_absence_is_named_not_applicable_by_structure(tmp_path):
    rc, doc = _run(tmp_path, _stage(tmp_path, _NON_PROTOCOL_L8))
    assert rc == 0
    assert doc["reason_class"] == sa.NOT_APPLICABLE_BY_STRUCTURE, doc
    assert doc["verdict"] == "VACUOUS_PASS"


def test_the_claim_carries_an_enumeration_the_guard_validates(tmp_path):
    """The whole point of R-0915-124/125: a class token with nothing behind it is
    not believed, whoever wrote it."""
    _rc, doc = _run(tmp_path, _stage(tmp_path, _NON_PROTOCOL_L8))
    ev = sa.evidence_of(doc)
    assert ev is not None and sa.is_valid(ev), doc
    assert ev["found"] == 0
    assert ev["scanned"] >= 3
    assert {"timing_windows", "timing_constants", "waveforms"} <= set(
        ev["scanned_names"]), "the three canonical containers must be named"


def test_the_rationale_states_an_answer_not_a_skip(tmp_path):
    _rc, doc = _run(tmp_path, _stage(tmp_path, _NON_PROTOCOL_L8))
    assert "ANSWERED, not" in doc["rationale"]
    assert "enumerated" in doc["rationale"]


# ── CONTROL: the DECLARED escape keeps its own, correct, class ───────────────

def test_an_explicit_l2_declaration_stays_design_declared_na(tmp_path):
    """The sibling escape reads a real declaration, so DESIGN_DECLARED_NA is
    right there and MUST NOT move. Two different facts, two different words."""
    gd = _stage(tmp_path, _PROTOCOL_L8,
                l2={"protocol_overview": {"half_duplex": False}})
    rc, doc = _run(tmp_path, gd)
    assert rc == 0
    assert doc["reason_class"] == "DESIGN_DECLARED_NA", doc
    assert "half_duplex=false" in doc["rationale"]


# ── THE TEETH: a real protocol design still gets the strict rule ────────────

def test_a_half_duplex_design_is_not_excused(tmp_path):
    """The escape must not swallow a design the rule DOES apply to: directional
    groups are present, so the split is checked and this is not an N/A."""
    rc, doc = _run(tmp_path, _stage(tmp_path, _PROTOCOL_L8))
    assert doc.get("reason_class") != sa.NOT_APPLICABLE_BY_STRUCTURE, (
        "an L8 carrying rx_/tx_ groups was excused as having no such subject")
    # And it did not merely avoid the escape: the strict rule RAN and judged.
    # On this input the split is genuinely wrong (rx BR=31..65 vs tx IBT=70..90),
    # so the gate FAILs with findings and files no class at all -- an N/A and a
    # verdict are different shapes, and only a verdict carries findings.
    assert rc == 1 and doc["verdict"] == "FAIL"
    assert doc["total_findings"] >= 1 and "reason_class" not in doc


# ── the guard itself refuses a claim that walked nothing ────────────────────

def test_absence_refuses_a_claim_with_nothing_behind_it():
    try:
        sa.absence(population="things", scanned=0, found=0)
    except ValueError as exc:
        assert "at least" in str(exc)
    else:                                    # pragma: no cover
        raise AssertionError("scanned=0 established an absence")


def test_absence_refuses_a_claim_whose_subject_is_present():
    try:
        sa.absence(population="things", scanned=5, found=2)
    except ValueError as exc:
        assert "the subject IS present" in str(exc)
    else:                                    # pragma: no cover
        raise AssertionError("found>0 established an absence")

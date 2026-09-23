"""R-0915-119's guard is only as good as the ENUMERATION it is handed — and
the enumeration is only as good as the scan that produced it.

MEASURED on spm run22 (lane icspm5, 2026-09-23). Step 2's
`internal_vs_external_timing_check` exits 0, prints nothing, and publishes
verdict VACUOUS_PASS / reason_class NOT_APPLICABLE_BY_STRUCTURE with
structural_absence {scanned 13, found 0}. The ledger row read verdict
INCOMPLETE / reason_class EXECUTION_ERROR and step 2 read NOT_MEASURED /
partial_population — "the gate reports its input was applicable and was NOT
examined" over a checker that had enumerated thirteen containers and said so.

The first cut of this change handed the guard the evidence at the rc-0 call
site and stopped there. THE PRE-LANDING REVIEW REFUSED IT, and it was right:
the fail-closed default was masking a defect in the GATE's own escape, so
removing the mask without fixing the escape would have turned a bad
enumeration into a PASS. Three things were wrong in the gate and one in the
ledger, and this file pins all four BY BEHAVIOUR.

  1. the escape never read L2. Its own comment says it fires "when L2 says
     NOTHING", but the condition did not look — so a design DECLARING
     protocol_overview.half_duplex=true, whose L8 emitted its canonical
     containers empty (a producible shape), was certified as a structural
     absence while the gate's own check() answered FAIL on the same input.
  2. `scanned` counted NAMES, not containers. It listed all three canonical
     containers whether or not the document had them. A container that is
     absent was never examined.
  3. the scan read top-level key names only, while check() prefers the nested
     `timing_groups` mapping — so a real RX/TX split, stored the canonical
     way, could be enumerated as if it were not there.
  4. `_record_gate_execution` re-runs the taxonomy and was not given the
     evidence either, so the published row carried verdict NOT_APPLICABLE
     beside reason_class EXECUTION_ERROR: one gate, two answers, one record.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import _flow_reason_taxonomy as _t  # noqa: E402
import _structural_absence as _sa  # noqa: E402

GATE = PROGRAMS / "internal_vs_external_timing_check.py"


def _project(tmp_path, l8, l2=None):
    docs = tmp_path / "phase1" / "generated_docs"
    docs.mkdir(parents=True)
    (docs / "L8_TIMING_WAVEFORM.json").write_text(json.dumps(l8))
    (docs / "L8_RTL_CONSTANTS.json").write_text("{}")
    if l2 is not None:
        (docs / "L2_FRS.json").write_text(json.dumps(l2))
    return docs


def _run(docs, out):
    r = subprocess.run(
        [sys.executable, str(GATE),
         str(docs / "L8_TIMING_WAVEFORM.json"),
         "--layer", str(docs / "L8_RTL_CONSTANTS.json"),
         "--json", str(out)],
        capture_output=True, text=True)
    doc = json.loads(out.read_text()) if out.is_file() else {}
    return r.returncode, doc


_EMPTY_L8 = {"timing_windows": [], "timing_constants": [], "waveforms": []}


# ------------------------------------------------- the GATE, by behaviour

def test_a_declared_half_duplex_design_is_never_certified_as_an_absence(tmp_path):
    """THE CASE THE REVIEW NAMED. The design's own word outranks the gate's
    inference: with half_duplex declared true and an empty L8, this must reach
    the gate's real answer, never a structural absence."""
    docs = _project(tmp_path, _EMPTY_L8,
                    l2={"protocol_overview": {"half_duplex": True}})
    rc, doc = _run(docs, tmp_path / "out.json")
    assert doc.get("reason_class") != _t.NOT_APPLICABLE_BY_STRUCTURE
    assert doc.get("verdict") == "FAIL"
    assert rc != 0
    assert len(doc.get("findings") or []) >= 1


def test_a_design_that_declares_nothing_still_reaches_the_escape(tmp_path):
    """The other direction: the escape is not removed, only bounded. With no
    L2 declaration and nothing in the document, the absence is still named."""
    docs = _project(tmp_path, _EMPTY_L8, l2=None)
    rc, doc = _run(docs, tmp_path / "out.json")
    assert rc == 0
    assert doc.get("reason_class") == _t.NOT_APPLICABLE_BY_STRUCTURE


def test_scanned_counts_containers_that_were_there_not_names(tmp_path):
    """An absent container was not examined. A document carrying NONE of the
    canonical containers may not report having scanned three of them."""
    docs = _project(tmp_path, {"doc_class": "L8"}, l2=None)
    rc, doc = _run(docs, tmp_path / "out.json")
    sa = doc.get("structural_absence") or {}
    if doc.get("reason_class") == _t.NOT_APPLICABLE_BY_STRUCTURE:
        names = sa.get("scanned_names") or []
        assert sa.get("scanned") == len(names), (sa.get("scanned"), names)
        for canonical in ("timing_windows", "timing_constants", "waveforms"):
            assert canonical not in names, (
                f"{canonical} is absent from the document and cannot have "
                f"been scanned: {names}")


def test_a_real_split_nested_under_timing_groups_is_not_an_absence(tmp_path):
    """check() prefers the nested `timing_groups` mapping; the escape read
    top-level names only, so a real RX/TX split stored the canonical way could
    be certified as absence."""
    docs = _project(tmp_path, {
        "timing_windows": [], "timing_constants": [], "waveforms": [],
        "timing_groups": {"rx_symbol_window": {"t": 1},
                          "tx_symbol_window": {"t": 2}},
    }, l2=None)
    rc, doc = _run(docs, tmp_path / "out.json")
    assert doc.get("reason_class") != _t.NOT_APPLICABLE_BY_STRUCTURE, (
        "a document that carries both directional groups has content to "
        "split; it is not an absence")


# ------------------------------------- the TAXONOMY call, by behaviour

def _report(scanned=13, found=0, reason_class="NOT_APPLICABLE_BY_STRUCTURE"):
    return {"verdict": "VACUOUS_PASS", "reason_class": reason_class,
            _sa.EVIDENCE_KEY: {"population": "containers", "scanned": scanned,
                               "found": found,
                               "scanned_names": [f"c{i}" for i in range(scanned)]}}


def test_the_enumerated_absence_is_classified_as_absence_not_as_a_crash():
    rep = _report()
    got = _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="",
        evidence={_sa.EVIDENCE_KEY: _sa.evidence_of(rep)},
        explicit=_t.report_reason_class(rep))
    assert got == _t.NOT_APPLICABLE_BY_STRUCTURE
    assert got in _t.SKIP_ELIGIBLE and got not in _t.INCOMPLETE


def test_a_bare_token_with_no_enumeration_is_still_refused():
    """The fail-closed default is NOT removed — it is given its evidence."""
    assert _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="", evidence=None,
        explicit="NOT_APPLICABLE_BY_STRUCTURE") == _t.EXECUTION_ERROR


def test_an_enumeration_that_found_its_subject_is_refused():
    rep = _report(found=4)
    assert _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="",
        evidence={_sa.EVIDENCE_KEY: _sa.evidence_of(rep)},
        explicit=_t.report_reason_class(rep)) == _t.EXECUTION_ERROR


def test_a_report_declaring_execution_error_stays_incomplete():
    rep = _report(reason_class="EXECUTION_ERROR")
    got = _t.infer_nonverdict_reason(
        verdict="VACUOUS_PASS", message="",
        evidence={_sa.EVIDENCE_KEY: _sa.evidence_of(rep)},
        explicit=_t.report_reason_class(rep))
    assert got == _t.EXECUTION_ERROR and got in _t.INCOMPLETE


# --------------------------------------------- the LEDGER ROW, by behaviour

def test_the_ledger_row_is_one_decision_not_two():
    """`_record_gate_execution` re-runs the taxonomy. Without the evidence it
    fail-closes there too, and the row published NOT_APPLICABLE beside
    EXECUTION_ERROR."""
    import flow_compliance_check as F
    rep = _report()
    row = F._record_gate_execution(
        "some_gate . --json r.json", 0, "NOT_APPLICABLE",
        _t.NOT_APPLICABLE_BY_STRUCTURE,
        evidence={_sa.EVIDENCE_KEY: _sa.evidence_of(rep)})
    assert row["verdict"] == "NOT_APPLICABLE"
    assert row["reason_class"] == _t.NOT_APPLICABLE_BY_STRUCTURE, row


def test_the_ledger_row_without_evidence_still_fails_closed():
    """The negative arm of the same call: no evidence, no decided state."""
    import flow_compliance_check as F
    row = F._record_gate_execution(
        "some_gate . --json r.json", 0, "NOT_APPLICABLE",
        _t.NOT_APPLICABLE_BY_STRUCTURE, evidence=None)
    assert row["reason_class"] == _t.EXECUTION_ERROR

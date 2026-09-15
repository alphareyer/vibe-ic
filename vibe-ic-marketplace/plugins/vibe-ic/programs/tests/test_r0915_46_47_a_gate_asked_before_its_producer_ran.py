"""R-0915-46 / R-0915-47: a gate asked before the thing it measures exists.

MEASURED 2026-09-16 (lane icspm3) on the SPM verdict candidate, run28 /
`spm23`. Two rows kept the run non-green, and neither was a fact about the
design:

  `gate_evidence_completeness_check` booked INCOMPLETE — "examined nothing
  (reason: no FINAL_REPORT.md or flow-compliance JSON in this run)". It books
  that for the absence of the completion audit's OWN record, which the audit
  it is a component of writes only after it passes. CIRCULAR: on a first run
  it can never be satisfied. MEASURED: `find_report(spm23)` returned None
  while `reports/audit/phase23_completion_audit.json` sat on disk with a
  246-entry `gate_execution_ledger`.

  `klayout_deck_mode_check` booked INCOMPLETE in flight — "[skipped] no
  KLayout DRC artefacts found" — and on the completed tree's re-audit the same
  gate is "[PASS] 14 DRC artefact(s); real rule deck attested by 3 of them".
  The P0 umbrella asks before phase-3 DRC has produced them.

Both are the R-0915-38 deadlock family: a gate demanding an artefact produced
after it.

TWO FIXES, AND THE SECOND ONE MATTERS AS MUCH AS THE FIRST. Teaching the
completeness gate to read the audit record turned its circular INCOMPLETE into
a FAIL naming 14 of 17 PASS gates as unevidenced — including
`yosys_hilomap_required_check`, while `reports/phase2/gates/yosys_hilomap.json`
sat on disk. The evidence locator globbed `reports/gates/*.json`, and THIS
FLOW WRITES `reports/phase2/gates/`. Shipping the lookup alone would have
replaced a circular INCOMPLETE with a FALSE FAIL, which is worse.

THE GRANT IS NARROW. `ASKED_BEFORE_PRODUCER` applies only to a registered
gate, and only while the tree shows the producer has NOT run. Once it has, an
absent subject keeps whatever non-green verdict the gate gave it — that is the
negative control, and it is what stops this becoming a blanket excuse.

chip-AGNOSTIC: synthetic projects in tmp_path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import _flow_reason_taxonomy as T  # noqa: E402
import flow_compliance_check as F  # noqa: E402
import gate_evidence_completeness_check as G  # noqa: E402

GATE = PROGRAMS / "gate_evidence_completeness_check.py"
REGISTERED = ("klayout_deck_mode_check", "gate_evidence_completeness_check")


def _audit(proj, ledger):
    """Write the completion audit's own record. Rows may carry `cmd`, which
    is how a gate DECLARES the artefact its PASS rests on."""
    p = proj / "reports" / "audit" / "phase23_completion_audit.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"gate_execution_ledger": ledger}))
    return p


def _gate_report(proj, rel, payload=None):
    p = proj / "reports" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload or {"verdict": "PASS"}))
    return p


def _run(proj):
    r = subprocess.run([sys.executable, str(GATE), str(proj)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── R-0915-46: the gate reads the population the audit is building ───────

def test_the_audit_record_is_what_the_gate_reads(tmp_path):
    _audit(tmp_path, [{"gate": "yosys_hilomap_required_check",
                       "verdict": "PASS"}])
    found = G.find_report(tmp_path)
    assert found is not None
    assert found.name == "phase23_completion_audit.json"
    assert G.extract_pass_gates_from_json(found) == [
        "yosys_hilomap_required_check"]


def test_a_pass_gate_whose_evidence_is_on_disk_is_backed(tmp_path):
    _audit(tmp_path, [{"gate": "yosys_hilomap_required_check",
                       "verdict": "PASS"}])
    # THE LOCATOR REGRESSION: this is where the flow actually writes.
    _gate_report(tmp_path, "phase2/gates/yosys_hilomap.json")
    rc, out = _run(tmp_path)
    assert rc == 0, out
    assert "0 without evidence" in out


def test_a_gate_that_DECLARED_an_artefact_and_did_not_produce_it_FAILS(
        tmp_path):
    """THE CONTROL R-0915-46 ASKS FOR, and the teeth of the whole check. A
    gate whose own command names `--json <path>` must have produced that
    file."""
    _audit(tmp_path, [
        {"gate": "yosys_hilomap_required_check", "verdict": "PASS",
         "cmd": "yosys_hilomap_required_check . "
                "--json reports/phase2/gates/yosys_hilomap.json"},
        {"gate": "a_gate_that_promised_a_file", "verdict": "PASS",
         "cmd": "a_gate_that_promised_a_file . "
                "--json reports/phase2/gates/never_written.json"}])
    _gate_report(tmp_path, "phase2/gates/yosys_hilomap.json")
    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert "a_gate_that_promised_a_file" in out
    assert "never_written.json" in out, "name the artefact it owed"


def test_a_gate_that_DECLARES_no_artefact_is_evidenced_by_its_ledger_row(
        tmp_path):
    """MEASURED on the SPM verdict candidate: **146 PASS gates, 16 without
    evidence** once the audit's full-scope record was read -- and a `find`
    over the whole run tree turns up NOTHING for any of the 16, because they
    are invoked WITHOUT `--json`. They print a verdict and the audit records
    it; they are working exactly as designed. Demanding a file would be a
    FALSE FAIL over 16 correct gates, which is worse than the circular
    INCOMPLETE this ruling set out to remove."""
    _audit(tmp_path, [{"gate": "constants_validation", "verdict": "PASS",
                       "cmd": "constants_validation ."}])
    rc, out = _run(tmp_path)
    assert rc == 0, out
    assert "declaring no artefact" in out


def test_a_run_with_no_gate_reports_at_all_still_refuses(tmp_path):
    """THE OTHER CONTROL. A claim with no ledger command and nothing on disk
    is not a pass."""
    _audit(tmp_path, [{"gate": "some_check", "verdict": "PASS"}])
    (tmp_path / "reports" / "audit"
     / "phase23_completion_audit.json").write_text(json.dumps(
         {"steps": [{"name": "some_check", "status": "PASS"}]}))
    rc, out = _run(tmp_path)
    assert rc == 1, out
    assert "some_check" in out


def test_a_gate_whose_product_is_a_transcript_is_evidenced_by_it(tmp_path):
    """`flow_compliance_check` writes no per-gate JSON because its record IS
    the audit; its transcript is its evidence. Without this the check reports
    the audit as the one unevidenced PASS in its own audit."""
    _audit(tmp_path, [{"gate": "flow_compliance_check", "verdict": "PASS"}])
    (tmp_path / "reports" / "audit" / "flow_compliance_check.log").write_text(
        "GATE_RAN ...\n")
    rc, out = _run(tmp_path)
    assert rc == 0, out


def test_the_legacy_steps_shape_still_works(tmp_path):
    p = tmp_path / "reports" / "flow_compliance.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"steps": [{"name": "old_check",
                                        "status": "PASS"}]}))
    assert G.extract_pass_gates_from_json(p) == ["old_check"]


def test_a_non_pass_ledger_row_is_not_a_claim(tmp_path):
    a = _audit(tmp_path, [{"gate": "skipped_check", "verdict": "SKIP"},
                          {"gate": "failed_check", "verdict": "FAIL"}])
    assert G.extract_pass_gates_from_json(a) == []


# ── R-0915-47: the grant, and the limit on it ────────────────────────────

@pytest.mark.parametrize("gate", REGISTERED)
def test_a_gate_asked_before_its_producer_is_not_an_execution_error(
        tmp_path, gate):
    (tmp_path / "reports").mkdir(parents=True)
    ev = F._asked_before_producer(gate, tmp_path)
    assert ev is not None, gate
    assert ev["kind"] == "asked-before-its-producer-ran"
    assert ev["producer_has_run"] is False
    assert ev["producer"], "the evidence must NAME what produces the subject"


@pytest.mark.parametrize("gate,producer_file", [
    ("klayout_deck_mode_check", "phase3/drc_signoff.json"),
    ("gate_evidence_completeness_check",
     "audit/phase23_completion_audit.json"),
])
def test_once_the_producer_HAS_run_there_is_no_grant(tmp_path, gate,
                                                     producer_file):
    """THE NEGATIVE CONTROL. A sub-gate whose subject exists by the time it is
    asked keeps its in-flight verdict; the grant is not a blanket excuse."""
    _gate_report(tmp_path, producer_file, {"passed": True})
    assert F._asked_before_producer(gate, tmp_path) is None, gate


def test_an_unregistered_gate_never_gets_the_grant(tmp_path):
    (tmp_path / "reports").mkdir(parents=True)
    assert F._asked_before_producer("some_other_check", tmp_path) is None


def test_the_class_is_skip_eligible_and_clears_P0():
    assert T.ASKED_BEFORE_PRODUCER in T.REASON_CLASS_SET
    assert T.ASKED_BEFORE_PRODUCER in T.SKIP_ELIGIBLE
    assert T.ASKED_BEFORE_PRODUCER not in T.INCOMPLETE
    assert T.record_verdict(T.ASKED_BEFORE_PRODUCER) == "SKIP"
    assert T.p0_tier_for_reason_classes({T.ASKED_BEFORE_PRODUCER}) == "PASS"
    assert T.p0_tier_for_reason_classes(
        {T.ASKED_BEFORE_PRODUCER, T.EXECUTION_ERROR}) == "INCOMPLETE"


def test_the_grant_actually_reaches_the_classifier():
    """The channel, end to end. An earlier attempt of mine put a class into
    the evidence dict that `normalise()` rejected because it was not in
    `REASON_CLASSES`, so the grant was inert while looking correct."""
    with_grant = T.infer_nonverdict_reason(
        verdict="SKIP", message="[skipped] no KLayout DRC artefacts found",
        evidence={"reason_class": T.ASKED_BEFORE_PRODUCER})
    without = T.infer_nonverdict_reason(
        verdict="SKIP", message="[skipped] no KLayout DRC artefacts found",
        evidence={})
    assert with_grant == T.ASKED_BEFORE_PRODUCER
    assert without == T.EXECUTION_ERROR
    assert with_grant in T.SKIP_ELIGIBLE and without not in T.SKIP_ELIGIBLE

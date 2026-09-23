"""An AUDITOR output is never a design input -- and a DIRECTORY cannot say which is which.

R-0915-151. `design_input_digest` subtracts the auditor's own writes before hashing the
design, so that "the tally moved" can be read as a statement about the DESIGN. The first
cut of that subtraction identified an auditor output by its LOCATION: anything under
`reports/audit/`. That is wrong here for a reason specific to this repo, and these arms are
the measurement of it.

`_path_layout.report_path` is an AUTO-ROUTER: a report name whose head it does not
recognise is routed to `reports/audit/` as the catch-all. So that directory holds
PRODUCER records and even the IC-expert's own answer file, side by side with the audit --
every fixture path below is produced by calling the real router, so these arms cannot
drift away from where the flow actually writes.

The two halves, and both must hold at once:
  * a producer record or a human/AI answer file that happens to sit there IS a design
    input: editing it must move the design hash, and a tally that moves with it is
    attributable to the design;
  * the auditor's own document is STILL not one, identified by what it IS -- a receipt
    stamping `program: flow_compliance_check`, a `*.superseded-<n>.json` minted only by the
    republish, and the completion audit named by its EXACT path, because MEASURED it
    carries no `program` key of its own (`flow_compliance_check.py` stamps the compliance
    RECEIPT, not the audit document).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import design_input_digest as D          # noqa: E402
import _path_layout as _pl               # noqa: E402

_AUDITOR = "flow_compliance_check"


def _sha(project: Path, footprint=()) -> str:
    """The design hash exactly as a pass publishes it."""
    scan = D.scan_inputs(project)
    block = D.build_digest(scan, list(footprint))
    assert block["unusable_reason"] is None, block
    assert isinstance(block["sha256"], str) and block["sha256"], block
    return block["sha256"]


def _kept(project: Path, footprint=()) -> set:
    scan = D.scan_inputs(project)
    return set(D.kept_inputs(scan, list(footprint)))


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a real design input, and the audit directory's real tenants."""
    p = tmp_path / "proj"
    (p / "input").mkdir(parents=True)
    (p / "input" / "spec.md").write_text("# a counter\n")
    # Every one of these is written by a PRODUCER (or, for the answer file, by the
    # IC expert); the router is what puts them under reports/audit/.
    for name, body in (
            ("expert_parse_track.json", {"program": "phase1_expert_parse_track",
                                         "layers": ["L1"]}),
            ("xor_allow_macros.json", {"allow": ["sram_16x8"]}),
            ("phase1_sufficiency.json", {"sufficient": True}),
            ("phase1/l21_rail_producers.json", {"rails": ["VDD"]}),
            ("phase2/gates/sdc_gen.json", {"program": "sdc_gen", "clocks": 1}),
            ("phase1/expert_parse_track_pack/l_doc_expectations.json",
             {"L1": {"expected": 4}}),
    ):
        f = _pl.report_path(p, name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(body))
    return p


#: The producer files the router puts in the auditor's directory, by the NAME the
#: producer passes to the router -- not by the routed path, so the arm follows the
#: router if the routing ever changes.
PRODUCER_RECORDS = [
    "expert_parse_track.json",
    "xor_allow_macros.json",
    "phase1_sufficiency.json",
    "phase1/l21_rail_producers.json",
    "phase2/gates/sdc_gen.json",
    "phase1/expert_parse_track_pack/l_doc_expectations.json",
]


@pytest.mark.parametrize("name", PRODUCER_RECORDS)
def test_a_producer_record_the_router_put_there_is_a_design_input(project, name):
    """Editing it must move the design hash. Under the prefix rule, none of these did."""
    routed = _pl.report_path(project, name)
    assert routed.is_file(), routed
    rel = str(routed.relative_to(project))
    assert rel.startswith("reports/audit/"), (
        f"this arm is only meaningful while the router still sends {name} there; got {rel}")

    assert rel in _kept(project), (
        f"{rel} is a produced record, not the auditor's output, so it must be part of "
        f"the design the digest hashes")

    before = _sha(project)
    doc = json.loads(routed.read_text())
    doc["edited_by_the_expert"] = True
    routed.write_text(json.dumps(doc))
    after = _sha(project)
    assert after != before, (
        f"the design hash did not move when {rel} was edited, so a tally that moves with "
        f"this edit is not attributable to the design")


def test_the_ai_answer_file_is_a_design_input_no_program_writes_it(project):
    """`l_doc_expectations.json` is the IC expert's ANSWER; no program emits it.

    It carries no `program` stamp because nothing stamps it, which is exactly why a
    content-identity rule must not fall back on its directory.
    """
    answer = _pl.report_path(
        project, "phase1/expert_parse_track_pack/l_doc_expectations.json")
    assert "program" not in json.loads(answer.read_text())
    assert D.is_auditor_output(project, answer) is False
    before = _sha(project)
    answer.write_text(json.dumps({"L1": {"expected": 9}}))
    assert _sha(project) != before


def test_the_completion_audit_is_not_a_design_input_and_carries_no_program_key(project):
    """The case the prefix got right, kept -- by exact path, since it cannot self-identify."""
    audit = _pl.report_path(project, "phase23_completion_audit.json")
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps({"schema_version": 2, "verdict": "PASS"}))
    # The measurement that forces the exact-path branch: the audit document does NOT
    # stamp its own emitter, so an identity-only rule would let the PREVIOUS pass's
    # verdict into THIS pass's design hash.
    assert "program" not in json.loads(audit.read_text())
    assert str(audit.relative_to(project)) in D.AUDITOR_OUTPUT_PATHS
    assert D.is_auditor_output(project, audit) is True

    before = _sha(project)
    audit.write_text(json.dumps({"schema_version": 2, "verdict": "FAIL",
                                 "failed_gate_count": 3}))
    assert _sha(project) == before, (
        "the auditor's own verdict changed the design hash, so the next pass would "
        "read its own previous answer as a design change")
    assert str(audit.relative_to(project)) not in _kept(project)


def test_a_receipt_that_stamps_the_auditor_is_not_a_design_input(project):
    """Identity, wherever the flow declares the receipt -- here OUTSIDE reports/audit/."""
    receipt = project / "reports" / "phase1" / "gates" / "stage_phase1_compliance.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"program": _AUDITOR, "overall": "PASS"}))
    assert D.is_auditor_output(project, receipt) is True
    before = _sha(project)
    receipt.write_text(json.dumps({"program": _AUDITOR, "overall": "FAIL"}))
    assert _sha(project) == before

    # ... and the same path with a PRODUCER's stamp is a design input again, which is
    # what makes the rule identity and not location.
    receipt.write_text(json.dumps({"program": "sdc_gen", "clocks": 2}))
    assert D.is_auditor_output(project, receipt) is False
    assert _sha(project) != before


def test_a_superseded_copy_is_not_a_design_input(project):
    """That name is only ever minted by the republish path."""
    sup = project / "reports" / "phase2" / "gates" / "stage1_compliance.superseded-2.json"
    sup.parent.mkdir(parents=True, exist_ok=True)
    sup.write_text(json.dumps({"overall": "FAIL"}))
    assert D.is_auditor_output(project, sup) is True
    before = _sha(project)
    sup.write_text(json.dumps({"overall": "FAIL", "published_by": "inv-2"}))
    assert _sha(project) == before


def test_an_edit_to_the_answer_file_is_classified_as_a_design_change(project):
    """End to end: the digest feeds `classify`, and this is the cost of the prefix.

    Pass 1 audits. The IC expert edits the answer file and nothing else. Pass 2's tally
    moves. With the design hash correctly moving, that movement is DESIGN_CHANGE; with
    the file subtracted as an "auditor output" the two hashes are identical and the same
    movement is published as unexplained -- the design's own progress credited to the
    ruler.
    """
    def audit(sha: str, failed: int) -> dict:
        return {
            "schema_version": 2,
            "verdict": "FAIL" if failed else "PASS",
            "step_counts": {"PASS": 70 - failed, "FAIL": failed},
            "passed_gate_count": 70 - failed,
            "failed_gate_count": failed,
            "run_at": "2026-09-23T00:00:00+00:00",
            "design_input_digest": {"sha256": sha},
            "measurement": D.build_measurement("1.19.90", None, {}),
        }

    answer = _pl.report_path(
        project, "phase1/expert_parse_track_pack/l_doc_expectations.json")
    sha1 = _sha(project)
    answer.write_text(json.dumps({"L1": {"expected": 4}, "L7": {"expected": 2}}))
    sha2 = _sha(project)

    verdict = D.classify(audit(sha1, 1), audit(sha2, 0))
    assert verdict["prior_eligibility"]["comparable"] is not False, verdict
    assert verdict["tally_moved"] is True, verdict
    assert verdict["design_moved"] is True, (
        "the one file the expert edited was subtracted from the design, so the digest "
        f"could not see the change: {verdict}")
    assert verdict["attributable_to_design"] is True, verdict
    assert verdict["classification"] == "DESIGN_CHANGE", verdict


def test_an_authorship_note_written_by_the_real_writer_is_not_a_design_input(project):
    """The notes stamp `written_by`, not `program` -- driven through the writer itself.

    Not a hand-built fixture: `_record_audit_created` is the only thing that mints these,
    and the note it produces here lands at the same digest-named path
    (`6be9e0442a07f03f3260.json`) as the real one read off run21x, so the arm and the
    fleet's artefacts are the same document.

    Each note records the noted file's `mtime_ns`, so its content moves every pass. Were
    it a design input, the design hash would move on every pass that wrote one -- which
    is why identity here has to cover BOTH stamp keys.
    """
    import flow_compliance_check as F

    noted_rel = "reports/audit/tapeout_checklist.json"
    noted = project / noted_rel
    noted.parent.mkdir(parents=True, exist_ok=True)
    noted.write_text(json.dumps({"checks": []}))

    before = _sha(project)
    F._record_audit_created(project, "36", [noted_rel])
    notes = sorted((project / "reports/audit/audit_created").glob("*.json"))
    assert len(notes) == 1, notes
    body = json.loads(notes[0].read_text())
    assert body["written_by"] == _AUDITOR and "program" not in body, body
    assert D.is_auditor_output(project, notes[0]) is True
    assert _sha(project) == before, (
        "the auditor's own authorship note moved the design hash; every pass that wrote "
        "one would then look like a design change")

    # Re-noting bumps `mtime_ns` inside the note. Still no design movement.
    noted.write_text(json.dumps({"checks": [], "again": True}))
    mid = _sha(project)
    assert mid != before, "the noted artefact itself is part of the tree that is hashed"
    F._record_audit_created(project, "36", [noted_rel])
    assert _sha(project) == mid

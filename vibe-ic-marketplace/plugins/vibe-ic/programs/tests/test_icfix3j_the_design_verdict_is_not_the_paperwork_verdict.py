"""R-0915-126 — the headline is the DESIGN's verdict; the completion audit
travels beside it and never overwrites it.

THE DEFECT. `_derive_headline_verdict` MERGED the two: when the completion
audit ranked worse than the run's own steps, the headline was REPLACED by the
audit's word. So a chip whose sign-off gates all passed published FAIL because
a paperwork gate — provenance receipts, docs_gen, foundry-handoff mode, waiver
staleness, fmeda applicability, formal-skill invocation, the expert handoff —
was unsatisfied. One word carried two different questions ("is this chip
right?" and "is this run's paperwork complete?") and the second silently won.

MEASURED on spm run15's published artefacts
(`reports/orchestrator/phase3_one_shot.json`): `verdict=FAIL`,
`steps_verdict=FAIL`, `completion_audit_verdict=FAIL`, with non-PASS steps
`drc=FAIL` and `tapeout_precheck=FAIL`. run15's design verdict is FAIL ON ITS
OWN STEPS, so the split does not flatter it — and that is the point: the split
changes what the word MEANS, not how generous it is.

NO GATE IS DELETED OR WEAKENED (R-0915-126(3)). The audit still runs, still
reaches its own verdict, and now publishes `audit_failed_gates` so "audit FAIL"
is never a word without a subject.

chip-AGNOSTIC: no design, PDK or vendor appears.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as P3  # noqa: E402

#: The eight audit-tier gates R-0915-126 names. None of them measures the chip.
_AUDIT_GATES = [
    "provenance_audit_chain", "docs_gen", "foundry_handoff_mode",
    "waiver_staleness", "fmeda_applicability", "formal_skill_invocation",
    "expert_handoff", "completion_receipts",
]

#: Repo-convention gates. R-0915-126(4) requires these be shown NOT to be on
#: the IC verdict path at all.
_REPO_CONVENTION_GATES = [
    "program_inventory_drift", "stated_counts", "flow_matrix_census",
]


def _project(tmp_path, audit_verdict, gates=None):
    a = tmp_path / "reports" / "audit"
    a.mkdir(parents=True, exist_ok=True)
    doc = {"verdict": audit_verdict}
    if gates is not None:
        doc["failed_gates"] = gates
    (a / "phase23_completion_audit.json").write_text(json.dumps(doc))
    return tmp_path


def test_a_clean_chip_is_not_failed_by_its_paperwork(tmp_path):
    """THE DEFECT, in one assertion: every step PASSES, one audit gate fails."""
    proj = _project(tmp_path, "FAIL", _AUDIT_GATES)
    verdict, audit_verdict, _ = P3._derive_headline_verdict(proj, "PASS")
    assert verdict == "PASS", (
        "the design verdict was overwritten by the completion audit: %r"
        % (verdict,))
    assert audit_verdict == "FAIL"


def test_the_audit_still_reports_and_names_its_gates(tmp_path):
    """R-0915-126(3). Not merging is not silencing: the audit keeps its own
    verdict AND now names every gate it failed, so nothing is laundered."""
    proj = _project(tmp_path, "FAIL", _AUDIT_GATES)
    _, audit_verdict, note = P3._derive_headline_verdict(proj, "PASS")
    assert audit_verdict == "FAIL"
    named = P3._audit_failed_gates(proj)
    for g in _AUDIT_GATES:
        assert g in named, (g, named)
    assert "audit_verdict" in note and "audit_failed_gates" in note, note


def test_a_failing_chip_is_still_failed(tmp_path):
    """THE OTHER DIRECTION, and the one that says this is not a weakening.
    run15's shape: the steps themselves FAIL (drc, tapeout_precheck)."""
    proj = _project(tmp_path, "FAIL", _AUDIT_GATES)
    verdict, audit_verdict, _ = P3._derive_headline_verdict(proj, "FAIL")
    assert verdict == "FAIL"
    assert audit_verdict == "FAIL"


def test_a_failing_chip_is_failed_even_when_the_paperwork_is_perfect(tmp_path):
    """The audit cannot RESCUE a bad chip either. The split cuts both ways, and
    a merge that only ever downgraded would still have been wrong here."""
    proj = _project(tmp_path, "PASS")
    verdict, audit_verdict, _ = P3._derive_headline_verdict(proj, "FAIL")
    assert verdict == "FAIL"
    assert audit_verdict == "PASS"


def test_no_audit_verdict_whatsoever_can_move_the_design_verdict(tmp_path):
    """R-0915-126(4), asserted as a PROPERTY rather than gate by gate: the
    design verdict is a function of the STEPS ALONE. Sweeping every word the
    audit can say — including the repo-convention gates, which measure this
    REPOSITORY and not the chip — the headline never moves. A gate that cannot
    reach the verdict path needs no separate proof that it is off it."""
    for steps in ("PASS", "PASS_WITH_WAIVERS", "FAIL"):
        for audit in list(P3._VERDICT_RANK) + [None, "", "NONSENSE"]:
            proj = _project(tmp_path, audit, _REPO_CONVENTION_GATES)
            verdict, _, _ = P3._derive_headline_verdict(proj, steps)
            assert verdict == steps, (steps, audit, verdict)


def test_an_absent_audit_leaves_the_design_verdict_standing(tmp_path):
    """An audit that could not be read is a 'could not look', and it must not
    become a verdict about the chip in either direction."""
    (tmp_path / "reports" / "audit").mkdir(parents=True, exist_ok=True)
    verdict, audit_verdict, note = P3._derive_headline_verdict(tmp_path, "PASS")
    assert verdict == "PASS"
    assert audit_verdict is None
    assert "absent" in note or "unreadable" in note, note


def test_the_failing_gate_names_survive_several_report_shapes(tmp_path):
    """The audit has rotated its key name across releases; a reader that knows
    one spelling would publish an empty list beside a FAIL, which is the same
    'word without a subject' this field exists to prevent."""
    for key in ("failed_gates", "failing_gates", "findings"):
        a = tmp_path / "reports" / "audit"
        a.mkdir(parents=True, exist_ok=True)
        (a / "phase23_completion_audit.json").write_text(
            json.dumps({"verdict": "FAIL", key: ["docs_gen"]}))
        assert "docs_gen" in P3._audit_failed_gates(tmp_path), key


def test_a_gate_row_that_passed_is_not_listed_as_failing(tmp_path):
    """The other direction for the enumerator: a structured gate list carries
    PASS rows too, and reporting them as failures would make the new field
    useless on its first real report."""
    a = tmp_path / "reports" / "audit"
    a.mkdir(parents=True, exist_ok=True)
    (a / "phase23_completion_audit.json").write_text(json.dumps({
        "verdict": "FAIL",
        "gates": [{"name": "docs_gen", "status": "FAIL"},
                  {"name": "provenance_audit_chain", "status": "PASS"}]}))
    named = P3._audit_failed_gates(tmp_path)
    assert "docs_gen" in named and "provenance_audit_chain" not in named, named

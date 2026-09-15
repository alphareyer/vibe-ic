"""The documents the flow declares a step's OWN producer writes must be written
by the RUN, not by the auditor evaluating that step's gate.

MEASURED 2026-09-15 (lane icsub2) on `subservient` x gf180mcuD — and the same
tier is what `spm` is held on::

    step 26  MISSING
      SELF-CERTIFIED EVIDENCE EXCLUDED (audit_created)
        ['reports/phase3/antenna_signoff.json']
        absent when this audit began []
        recorded by an earlier pass of this audit as its own write
        ['reports/phase3/antenna_signoff.json']
      ... PRODUCER GAP: no pre-audit producer supplied these paths; wire them
      into the owning runner before claiming this step complete.

The audit prints that remedy on ELEVEN steps, and it is right. For a
well-defined subset the flow has ALREADY NAMED the producer three times over:
the step lists the program under `programs:`, its own gate clause invokes that
same program with `--json <path>`, and `<path>` is one of the step's
`required_outputs`. `declared_producer_clauses` finds exactly that subset — 13
clauses over the shipped flow — and the RUNNER executes them once, after every
phase-3 step and before the completion audit.

THE TWO WAYS THIS COULD BE WRONG, and both are cases below:

  * OVERWRITING THE RUN'S OWN WORK. A document the run produced must be left
    byte-for-byte alone. Only an ABSENT target, or one the audit's own
    authorship note claims, is owed.

  * MANUFACTURING WORK FOR A STEP THE DELIVERY DOES NOT HAVE. MEASURED: the
    first version of this pass had no such guard and ran `pad_assignment_gen`
    for step 15.5ic on a HARDMACRO — a step whose own predicate
    (`_chip_path_requests_pad_ring`, vibe-ic#2246) says NO for exactly that
    delivery — plus `post_layout_sim_check` and `mixed_signal_merge_check` for
    steps this design never ran. Three MISSING steps became three FAILs and
    two new failed gates. A step the run DID perform has left at least one of
    its OTHER declared outputs on disk; a step it correctly skipped has left
    none, and its verdict document is not owed.

AND IT IS THE RUNNER'S, NEVER THE AUDITOR'S. A run executing the producers its
own flow declares is what a flow does; an auditor executing them and then
grading its own output is the self-certification the refused tier exists to
name. The last test asserts `flow_compliance_check` does not reference this
module at all, so the distinction cannot erode.

chip-AGNOSTIC: synthetic flow fixtures plus assertions over the shipped yaml.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402


# DEFERRED, and deliberately. `flow_declared_producer_run` does not exist on
# the pre-fix tree, and a module-level import of it would make this WHOLE FILE
# a collection error there — 0 cases run, 0 questions answered — so the two
# cases that need NO new symbol (the role boundary, and whether the runner
# invokes it) would never get to report what the pre-fix tree actually does.
def _P():
    import flow_declared_producer_run as P  # noqa: PLC0415
    return P


class _Lazy:
    def __getattr__(self, name):
        return getattr(_P(), name)


P = _Lazy()


FLOW = """\
steps:
  - id: "A1"
    name: a step whose producer writes a declared output
    programs: [fixture_producer]
    required_outputs:
      - "out/evidence.txt"
      - "reports/a1.json"
    gate:
      all_of:
        - program_exit_zero: "fixture_producer . --json reports/a1.json"
  - id: "A2"
    name: a step whose gate program is NOT one of its producers
    programs: [other_producer]
    required_outputs:
      - "out/other.txt"
      - "reports/a2.json"
    gate:
      all_of:
        - program_exit_zero: "some_gate . --json reports/a2.json"
  - id: "A3"
    name: a step whose gate writes somewhere it does not declare
    programs: [fixture_producer]
    required_outputs:
      - "out/third.txt"
    gate:
      all_of:
        - program_exit_zero: "fixture_producer . --json reports/a3.json"
"""


@pytest.fixture()
def flow_yaml(tmp_path):
    pytest.importorskip("yaml")
    p = tmp_path / "flow.yaml"
    p.write_text(FLOW)
    return p


# ── which clauses the flow declares ───────────────────────────────────────

def test_only_a_step_s_OWN_producer_of_its_OWN_declared_output_is_found(
        flow_yaml):
    got = P.declared_producer_clauses(flow_yaml)
    assert [c["step"] for c in got] == ["A1"], got
    assert got[0]["program"] == "fixture_producer"
    assert got[0]["target"] == "reports/a1.json"
    assert got[0]["siblings"] == ["out/evidence.txt"]


def test_the_shipped_flow_declares_the_measured_set():
    got = {(c["step"], c["program"]) for c in P.declared_producer_clauses()}
    for pair in (("26", "antenna_report_check"),
                 ("28", "perc_signoff_check"),
                 ("31", "erc_density_check"),
                 ("36", "tapeout_signoff_check")):
        assert pair in got, f"{pair} is no longer a declared producer clause"
    # WAS `("21", "drc_report_check")` and `("10", "sta_report_check")`, and
    # both were THE DEFECT rather than the rule. Steps 10 and 21 declare the
    # gate `--json` target as a `required_output` and named no producer, so
    # the audit refused its own document on both (lane icspm3, spm x
    # gf180mcuD, main b47917a47). Naming the producer is the fix; the two
    # pairs are asserted PRESENT by
    # `test_the_run_writes_the_13_spm_declared_outputs.py`.
    #
    # The rule this line was written for is kept, on a case that still shows
    # it: `rtl_bug_report_schema_check` IS listed under step 2's `programs:`
    # and its gate clause writes `--json reports/phase2/gates/
    # rtl_bug_schema.json`, which step 2 does NOT declare as a
    # `required_output` — so the document is not owed as run evidence and the
    # clause is not picked up. 64 clauses in the shipped flow have that shape.
    assert ("2", "rtl_bug_report_schema_check") not in got


# ── what is OWED, and what is left alone ──────────────────────────────────

def _clause(target="reports/a1.json", sibs=("out/evidence.txt",)):
    return [{"step": "A1", "program": "fixture_producer",
             "command": f"fixture_producer . --json {target}",
             "target": target, "siblings": list(sibs)}]


def test_an_absent_document_of_a_step_the_run_performed_is_owed(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/evidence.txt").write_text("the run did this step\n")
    to_run, skipped = P.owed(tmp_path, _clause())
    assert [r["step"] for r in to_run] == ["A1"] and not skipped


def test_a_document_the_run_produced_is_LEFT_ALONE(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/evidence.txt").write_text("x\n")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/a1.json").write_text('{"program": "fixture_producer"}')
    to_run, skipped = P.owed(tmp_path, _clause())
    assert not to_run
    assert skipped and "already produced" in skipped[0]["why"]


def test_a_step_the_run_never_performed_is_NOT_owed(tmp_path):
    """THE GUARD THE FIRST VERSION LACKED. No other declared output on disk
    means the run did not do this step, and its verdict document is not owed —
    otherwise the pass manufactures work for a delivery that has no such step
    (measured: a pad ring on a HARDMACRO)."""
    to_run, skipped = P.owed(tmp_path, _clause())
    assert not to_run
    assert skipped and "did not perform this step" in skipped[0]["why"]


def test_a_sibling_declared_as_a_glob_counts(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/thing.gds").write_bytes(b"\x00")
    to_run, _ = P.owed(tmp_path, _clause(sibs=("out/*.gds",)))
    assert [r["step"] for r in to_run] == ["A1"]


def test_a_sibling_declared_as_an_OR_counts(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/b.flag").write_text("")
    to_run, _ = P.owed(tmp_path, _clause(sibs=("out/a.log OR out/b.flag",)))
    assert [r["step"] for r in to_run] == ["A1"]


def test_a_clause_with_no_sibling_output_is_left_alone(tmp_path):
    """Undecidable, so nothing is done — the gap stays open and the audit
    keeps reporting it, which is the conservative direction."""
    to_run, skipped = P.owed(tmp_path, _clause(sibs=()))
    assert not to_run and skipped


def test_a_document_only_the_AUDITOR_has_written_is_reclaimed(tmp_path,
                                                              monkeypatch):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/evidence.txt").write_text("x\n")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/a1.json").write_text("{}")
    monkeypatch.setattr(_P(), "_audit_claims", lambda *a, **k: True)
    to_run, skipped = P.owed(tmp_path, _clause())
    assert [r["step"] for r in to_run] == ["A1"] and not skipped
    assert "the only writer so far is the auditor" in to_run[0]["why"]


# ── the role boundary ─────────────────────────────────────────────────────

def test_the_auditor_does_not_import_this():
    """A run executing its own declared producers is what a flow does; an
    AUDITOR executing them and then grading its own output is exactly the
    self-certification the refused tier names. The boundary is asserted, not
    assumed."""
    src = (PROGRAMS / "flow_compliance_check.py").read_text(errors="replace")
    assert "flow_declared_producer_run" not in src


def test_the_runner_invokes_it_before_the_completion_audit_refresh():
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text(errors="replace")
    assert "flow_declared_producer_run.py" in src
    assert src.index("flow_declared_producer_run.py") < src.index(
        'PROGRAMS_DIR / "flow_compliance_check.py"'), (
        "the producers must write before the audit reads")


def test_a_missing_program_is_NAMED_not_swallowed(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/evidence.txt").write_text("x\n")
    rec = P.run(tmp_path)
    # the shipped clauses are what `run` reads; assert the shape of the record
    assert rec["program"] == P.PROGRAM
    assert set(rec) >= {"declared_clauses", "owed", "executed",
                        "unexecutable", "verdict"}
    assert rec["verdict"] in ("PRODUCED", "INCOMPLETE")


def test_the_record_is_written_and_lists_every_decision(tmp_path):
    out = tmp_path / "rec.json"
    rc = P.main([str(tmp_path), "--json", str(out)])
    assert rc in (0, 1)
    rec = json.loads(out.read_text())
    assert rec["declared_clauses"] == len(P.declared_producer_clauses())
    assert len(rec["results"]) + len(rec["skipped"]) == rec["declared_clauses"]

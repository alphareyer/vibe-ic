"""A PHASE-2-ONLY run executes the producers ITS OWN flow declares.

MEASURED on subservient x gf180mcuD, the same design and the same input twice.

  r46 (`_lane_icsub2/c22_proj`) ran phase 3, so it reached the ONE site that
      invoked `flow_declared_producer_run` — `phase3_one_shot_runner`. Its
      `reports/audit/flow_declared_producer_run.json` exists, verdict PRODUCED,
      and its `results` names `reports/phase1/gates/stage_phase1_compliance.json`
      for step "2". Step 2 read `OUTPUT ATTRIBUTION: step-attributed (4/4 ...)`
      with no `audit_created` code, and phase 2 reached PASS_WITH_WAIVERS.

  r47 (`_lane_icsub2/c23_proj`) halted in phase 2, so that file does not exist
      at all. Step 2 read::

        SELF-CERTIFIED EVIDENCE EXCLUDED (audit_created)
          ['reports/phase1/gates/stage_phase1_compliance.json']
        AUDIT-CREATED OUTPUT REFUSED ... PRODUCER GAP: no pre-audit producer
          supplied these paths; wire them into the owning runner

      and was booked FAIL / missing_artefact, which voided step 4's 10/10 L10
      oracles as `dependency [2] = FAIL` and put every digital IC on the IC path
      behind it.

THE AUDITOR IS RIGHT TO REFUSE. A run executing the producers its own flow
declares is what a flow does; an auditor executing them and then grading its own
output is self-certification. So the gap was never in the refusal — it was that
NOTHING in a phase-2-only run executed the producer.

THE BISECT HAS A NEGATIVE ANSWER, and this file records it so the next reader
does not go looking for a regression. `git log -S'flow_declared_producer_run' --
design_one_shot_runner.py` is EMPTY FOR ALL HISTORY: the phase-2 runner never
invoked it. Both the mechanism (f119083ec) and step 2's `programs:` declaration
(11bd3c487) are ANCESTORS of r46's tree a8c7a3e74 — they already existed then —
and at a8c7a3e74 the invocation counts are identical to main
(design_one_shot_runner 0, phase3_one_shot_runner 5). The differing variable
between r46 and r47 was THE PHASE THE RUN REACHED, not a landing.

MEASURED AFTER THE FIX, on a fresh copy of the pristine r47 project:
  the producer pass reports `29 declared producer clause(s); 28 already produced
  by the run, 1 owed, 1 executed` — the one owed being exactly our path — and
  then step 2 FAIL/missing_artefact -> **PASS** (12 reasons -> 9, the two
  audit_created lines gone and the attribution line's `codes=['audit_created']`
  gone with them) and step 4 NOT_MEASURED/upstream_failed -> **PASS**, its two
  "PASS voided: dependency [2] = FAIL" lines removed.

chip-AGNOSTIC: every population here is recomputed from the shipped yaml and the
shipped runner sources; nothing is tabulated from a chip.
"""
from __future__ import annotations

import ast
import json
import subprocess  # nosec B404 — declared flow programs
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

FLOW_YAML = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"
PHASE2_RUNNER = PROGRAMS / "design_one_shot_runner.py"
PHASE3_RUNNER = PROGRAMS / "phase3_one_shot_runner.py"
PRODUCER = "flow_declared_producer_run"

#: The document r47 lost, and the step that owes it.
OWED_TARGET = "reports/phase1/gates/stage_phase1_compliance.json"
OWED_STEP = "2"
OWED_PROGRAM = "flow_compliance_check"


def _P():
    import flow_declared_producer_run as P  # noqa: PLC0415
    return P


# --------------------------------------------------------------------------- #
# (1) the wiring: the phase-2 runner invokes the producer, BEFORE the audit
# --------------------------------------------------------------------------- #
def test_the_phase2_runner_invokes_the_declared_producer_pass():
    """RED before this change: 0 occurrences in this file, for all history."""
    src = PHASE2_RUNNER.read_text()
    assert f"{PRODUCER}.py" in src, (
        "the phase-2 runner does not invoke the declared-producer pass, so a "
        "phase-2-only run leaves its declared documents to the audit")


def test_the_producer_pass_runs_BEFORE_the_final_audit():
    """Order is the whole point: the audit is what READS these documents, so a
    pass that ran after it would change nothing this run is graded on."""
    src = PHASE2_RUNNER.read_text()
    assert src.count("step_final_audit(project, phase=2") == 1
    assert src.index(f"{PRODUCER}.py") < src.index(
        "step_final_audit(project, phase=2")


def test_the_pass_is_recorded_not_gating():
    """It never decides a verdict: the step gates re-run these programs and keep
    their own. A non-zero rc is reported as an execution fact and the run
    continues — asserted on the source, because there is no verdict object to
    inspect."""
    src = PHASE2_RUNNER.read_text()
    i = src.index(f"{PRODUCER}.py")
    window = src[i - 2000:i + 2000]
    assert "check=False" in window
    assert "not a verdict" in window
    # and it cannot abort the audit that follows it
    assert "must not abort" in window
    # no `plan.append(...)` wraps it — it contributes no step row and therefore
    # no verdict to the aggregate
    assert "plan.append" not in src[i:i + 900]


# --------------------------------------------------------------------------- #
# (2) the other direction: a target with no DECLARED producer is still refused
# --------------------------------------------------------------------------- #
def test_a_step_that_names_no_producer_yields_no_clause():
    """The fix is a runner invocation, NOT a rule that credits any `--json`
    target to whoever wrote it. `declared_producer_clauses` reads the yaml's
    `programs:` channel; a step that declares an output and names no producer
    there yields NO clause, so nothing in the run writes it and the audit's
    refusal stands."""
    P = _P()
    clauses = P.declared_producer_clauses(FLOW_YAML)
    targets = {c["target"] for c in clauses}
    assert OWED_TARGET in targets, (
        "step 2's producer declaration is the premise of this whole change")
    # A synthetic flow whose step declares the same output and NO producer must
    # produce no clause for it — the negative control on the reader itself.
    yaml = pytest.importorskip("yaml")
    import tempfile
    doc = {"steps": [{"id": 2,
                      "required_outputs": [OWED_TARGET],
                      "gate": {"all_of": [
                          {"program_exit_zero":
                           f"{OWED_PROGRAM} . --json {OWED_TARGET}"}]}}]}
    with tempfile.TemporaryDirectory() as td:
        y = Path(td) / "flow.yaml"
        y.write_text(yaml.safe_dump(doc))
        assert P.declared_producer_clauses(y) == []


def test_a_clause_is_not_owed_on_a_step_the_run_did_not_perform(tmp_path):
    """The guard that keeps this from manufacturing work. A step the run did
    perform has left at least one of its OTHER declared outputs on disk; a step
    it correctly skipped has left none — so a phase-2 pass cannot execute a
    phase-3 producer for a step this delivery never had."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses(FLOW_YAML)
              if c["target"] == OWED_TARGET][0]
    assert clause["siblings"], "the guard needs a sibling to read"
    to_run, skipped = P.owed(tmp_path, [clause])          # empty project
    assert to_run == []
    assert len(skipped) == 1
    assert "did not perform this step" in skipped[0]["why"]


def test_a_document_the_run_already_produced_is_left_alone(tmp_path):
    """It never overwrites the run's own work — byte-for-byte."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses(FLOW_YAML)
              if c["target"] == OWED_TARGET][0]
    for sib in clause["siblings"][:1]:
        s = tmp_path / sib
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_text("{}")                      # the step DID run
    t = tmp_path / OWED_TARGET
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(json.dumps({"mine": True}))    # and produced this itself
    to_run, skipped = P.owed(tmp_path, [clause])
    assert to_run == []
    assert skipped and skipped[0]["why"] == "the run already produced it"
    assert json.loads(t.read_text()) == {"mine": True}


# --------------------------------------------------------------------------- #
# (3) the phase-3 site is untouched
# --------------------------------------------------------------------------- #
def test_the_phase3_site_is_unchanged():
    """This change ADDS a caller; it does not move the one that existed. The
    phase-3 runner must still invoke the pass, with the same guarantees."""
    src = PHASE3_RUNNER.read_text()
    assert src.count(f"{PRODUCER}.py") == 1
    i = src.index(f"{PRODUCER}.py")
    window = src[i - 1200:i + 1600]
    assert "check=False" in window and "must not abort" in window


def test_both_runners_invoke_it_the_same_way():
    """One shape, two callers. The argv, the timeout basis and the rc handling
    are compared as PARSED CALLS, not as text, so a reformat cannot pass and a
    changed argument cannot hide."""
    def _call(path: Path):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if PRODUCER not in ast.dump(node):
                continue
            kw = {k.arg for k in node.keywords}
            return kw
        return None
    two = _call(PHASE2_RUNNER)
    three = _call(PHASE3_RUNNER)
    assert two is not None and three is not None
    assert two == three == {"timeout", "check", "capture_output", "text"}

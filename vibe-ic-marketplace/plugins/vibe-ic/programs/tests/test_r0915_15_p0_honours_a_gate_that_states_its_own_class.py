"""P0 honours a gate that STATES its own reason class, instead of reading its
sentence.

MEASURED 2026-09-15 (lane icspm3, R-0915-15) on `spm` x gf180mcuD.
`_flow_reason_taxonomy.infer_nonverdict_reason` says branch-owned evidence
outranks prose, and `report_reason_class` reads `summary.reason_class` before
any recogniser runs. The P0 umbrella could honour neither, because it invoked
these gates with **no `--json` at all**: there was no report, so a gate that
KNEW its own class had nowhere to say so. Both gates below were changed to
state it, and the record still read::

    waiver_staleness_check — reason_class=EXECUTION_ERROR

off the prose, on an rc=2 the arm labels `skip_kind: input-missing` — an
ASSUMPTION about every rc=2, and simply wrong for a gate whose input was there
and whose subject was not.

BOTH DIRECTIONS: a declaring gate's class is used; a gate that declares
nothing, or writes an unreadable report, keeps exactly the reading it had.

chip-AGNOSTIC: paths derive from the project root and a scratch dir.
"""
from __future__ import annotations

import json
import subprocess  # nosec B404 — declared flow programs
import sys
import tempfile
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import flow_compliance_check as F  # noqa: E402
import _flow_reason_taxonomy as R  # noqa: E402

DECLARING = sorted(F._P0_GATE_DECLARES_REASON_CLASS)


def test_the_registry_is_not_empty_and_names_real_programs():
    assert DECLARING, "nothing declares a class; this file measures nothing"
    for gate in DECLARING:
        assert (PROGRAMS / f"{gate}.py").is_file(), gate


@pytest.mark.parametrize("gate", DECLARING)
def test_a_declaring_gate_is_given_somewhere_to_say_it(tmp_path, gate):
    argv = F._structural_gate_argv(gate, tmp_path, rtl_dir=tmp_path,
                                   scratch_dir=tmp_path / "scratch")
    assert "--json" in argv, argv
    target = Path(argv[argv.index("--json") + 1])
    assert target.name == f"{gate}.declared.json", target
    assert target.parent.is_dir(), "the scratch dir must exist before the run"


#: What each declaring gate says about THE FIXTURE BELOW — a project with a
#: reports/ dir, an ic_class declaring no analog, an auto-synthesized waiver,
#: and nothing else.
#:
#: R-0915-52 (lane icsub4): this used to be one blanket
#: `== DESIGN_DECLARED_NA`, which held only because both gates registered at
#: the time read that fixture as "the design declares nothing". It is not a
#: property of the registry — `l24_signoff_evidence_backed_check` reads the
#: same fixture as ASKED_BEFORE_PRODUCER, and correctly so: the fixture has no
#: `L24_SIGNOFF.json` because phase 1 never ran, which is a different fact
#: from a design that declares no sign-off. The expectation is therefore
#: stated per gate and the registry coverage is asserted, so a newly
#: registered gate must SAY what it declares here rather than inherit a class
#: it does not mean.
_CLASS_ON_THE_EMPTY_FIXTURE = {
    "waiver_staleness_check": R.DESIGN_DECLARED_NA,
    "analog_flow_compliance_check": R.DESIGN_DECLARED_NA,
    "l24_signoff_evidence_backed_check": R.ASKED_BEFORE_PRODUCER,
}


def test_every_declaring_gate_states_what_it_says_here():
    """No gate may join the registry without an expectation in this file."""
    assert set(_CLASS_ON_THE_EMPTY_FIXTURE) == set(DECLARING), (
        sorted(set(DECLARING) ^ set(_CLASS_ON_THE_EMPTY_FIXTURE)))
    for cls in _CLASS_ON_THE_EMPTY_FIXTURE.values():
        assert cls in R.REASON_CLASS_SET, cls


@pytest.mark.parametrize("gate", DECLARING)
def test_a_declaring_gate_actually_writes_a_class(tmp_path, gate):
    """Not asserted from the registry — RUN each one and read what it wrote."""
    proj = tmp_path / gate
    (proj / "reports").mkdir(parents=True)
    (proj / "reports" / "ic_class.json").write_text(
        json.dumps({"has_analog": False}))
    (proj / "waivers.json").write_text(json.dumps({"waived_steps": [
        {"id": 39, "reason": "ENV_UNAVAILABLE", "ticket": "T",
         "auto_synthesized": True}]}))
    scratch = proj / "scratch"
    argv = F._structural_gate_argv(gate, proj, rtl_dir=proj,
                                   scratch_dir=scratch)
    subprocess.run(argv, cwd=str(proj), capture_output=True,  # nosec B603
                   text=True, check=False)
    assert F._p0_declared_reason_class(gate, proj, scratch) == \
        _CLASS_ON_THE_EMPTY_FIXTURE[gate], gate


# ── the conservative direction ────────────────────────────────────────────

def test_a_gate_that_declares_nothing_gets_no_json_and_no_class(tmp_path):
    gate = "backlog_sanitize_check"
    assert gate not in F._P0_GATE_DECLARES_REASON_CLASS
    assert F._p0_declared_report_path(gate, tmp_path, tmp_path) is None
    assert F._p0_declared_reason_class(gate, tmp_path, tmp_path) is None


@pytest.mark.parametrize("body", ["", "{ not json", "[]", "{}",
                                  '{"summary": {}}'])
def test_an_absent_or_silent_report_changes_nothing(tmp_path, body):
    gate = DECLARING[0]
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    if body:
        (scratch / f"{gate}.declared.json").write_text(body)
    assert F._p0_declared_reason_class(gate, tmp_path, scratch) is None


def test_an_unregistered_class_word_is_refused(tmp_path):
    """`report_reason_class` normalises; a word outside the taxonomy is not a
    class and must not become one."""
    gate = DECLARING[0]
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / f"{gate}.declared.json").write_text(
        json.dumps({"summary": {"reason_class": "TOTALLY_FINE"}}))
    assert F._p0_declared_reason_class(gate, tmp_path, scratch) is None


def test_the_taxonomy_reads_branch_evidence_before_the_prose():
    """The contract this whole channel rests on, asserted directly."""
    assert R.infer_nonverdict_reason(
        verdict="SKIP",
        message="no RTL files found",
        evidence={"reason_class": R.DESIGN_DECLARED_NA,
                  "skip_kind": "declared-by-gate"}) == R.DESIGN_DECLARED_NA
    # and without the declaration the same sentence keeps its old reading
    assert R.infer_nonverdict_reason(
        verdict="SKIP", message="no RTL files found",
        evidence={"exit_code": 2, "skip_kind": "input-missing"}) \
        != R.DESIGN_DECLARED_NA

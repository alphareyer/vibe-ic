"""The re-emit back-fill re-declares only what THIS pass demonstrably produced.

MEASURED on main 240c0a353: a declared `reports/phase3/lvs.rpt`, rewritten with
no invocation, read PROVENANCE_HASH_MISMATCH under
`provenance_output_hash_completeness_check` -- and PASS after the runner's
`_record_reemitted_outputs` appended a `reconstructed` re-emit row declaring the
new bytes. The ledger laundered an unexplained rewrite; nobody reads
`reconstructed`.

The rule now: a drifted declared output is re-declared only when a step of this
pass wrote exactly those bytes (its StepResult lists the file, the file was
modified inside the step's run window, and the disk still holds the sha taken
when the step returned). The row names the producing step. Anything else is
written to `reports/phase3/provenance_unexplained_rewrites.json` and NOT
declared, so the hash check still reports it.

The real runner function and the real checker decide; nothing is stubbed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R                              # noqa: E402
import provenance_output_hash_completeness_check as C           # noqa: E402

REL = "reports/phase3/lvs.rpt"
ORIGINAL = b"LVS: circuits match uniquely\n"


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(R, "_PASS_PRODUCED", {}, raising=False)


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    f = proj / REL
    f.parent.mkdir(parents=True)
    f.write_bytes(ORIGINAL)
    (proj / "provenance.jsonl").write_text(json.dumps({
        "tool": "netgen", "command": "netgen -batch lvs", "exit_code": 0,
        "timestamp": "2026-09-24T01:00:00Z",
        "outputs": {REL: _sha(ORIGINAL)}}) + "\n")
    return proj


def _rows(proj: Path):
    return [json.loads(ln) for ln in
            (proj / "provenance.jsonl").read_text().splitlines() if ln.strip()]


def _rules(proj: Path):
    verdict, findings = C.audit(proj)
    return verdict, sorted({f.rule for f in findings})


# ── the laundering: RED on main ─────────────────────────────────────────────

def test_an_undeclared_rewrite_is_not_laundered_into_a_declaration(tmp_path):
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"LVS: circuits match uniquely (edited)\n")
    note = R._record_reemitted_outputs(proj)
    verdict, rules = _rules(proj)
    assert verdict == "FAIL" and "PROVENANCE_HASH_MISMATCH" in rules, rules
    assert len(_rows(proj)) == 1, _rows(proj)
    assert note and REL in note, note


def test_the_unexplained_rewrite_is_recorded_as_a_finding(tmp_path):
    proj = _project(tmp_path)
    edited = b"LVS: circuits match uniquely (edited)\n"
    (proj / REL).write_bytes(edited)
    R._record_reemitted_outputs(proj)
    rep = json.loads((proj / "reports/phase3/"
                      "provenance_unexplained_rewrites.json").read_text())
    assert rep["verdict"] == "FINDING"
    assert rep["rewrites"] == [{
        "path": REL, "declared_sha256": _sha(ORIGINAL),
        "disk_sha256": _sha(edited),
        "why": "no step of this pass wrote these bytes"}]


def test_a_change_after_the_producing_step_returned_is_unexplained(tmp_path):
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"LVS by the step\n")
    R.StepResult("lvs", "PASS", 1.0, "ran", [str(proj / REL)])
    (proj / REL).write_bytes(b"LVS changed afterwards\n")
    R._record_reemitted_outputs(proj)
    verdict, rules = _rules(proj)
    assert verdict == "FAIL" and "PROVENANCE_HASH_MISMATCH" in rules, rules


def test_a_file_a_step_lists_but_did_not_write_is_not_credited(tmp_path):
    """A step that REUSED a file (not modified in its window) did not produce
    its bytes, even though its row lists the path."""
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"edited before the step\n")
    old = time.time() - 3600
    os.utime(proj / REL, (old, old))
    R.StepResult("lvs", "PASS", 1.0, "reused", [str(proj / REL)])
    R._record_reemitted_outputs(proj)
    assert _rules(proj)[0] == "FAIL"


# ── the legitimate re-emit is kept ─────────────────────────────────────────

def test_a_rewrite_by_a_step_of_this_pass_is_re_declared_naming_it(tmp_path):
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"LVS by the step\n")
    R.StepResult("lvs", "PASS", 1.0, "ran", [str(proj / REL)])
    assert R._record_reemitted_outputs(proj) is None
    assert _rules(proj)[0] == "PASS"
    last = _rows(proj)[-1]
    assert last["command"] == "re-emit (phase3 iteration)"
    assert last["producing_step"] == {REL: "lvs"}
    assert last["reconstructed"] is True


def test_a_failed_step_still_names_what_it_wrote(tmp_path):
    """A FAIL row still ran and still wrote its report."""
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"LVS: mismatch\n")
    R.StepResult("lvs", "FAIL", 1.0, "mismatch", [str(proj / REL)])
    R._record_reemitted_outputs(proj)
    assert _rules(proj)[0] == "PASS"


def test_a_step_that_did_not_run_credits_nothing(tmp_path):
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"LVS by nobody\n")
    R.StepResult("lvs", "NOT_MEASURED", 1.0, "no netlist",
                 [str(proj / REL)], reason_class="missing_artefact")
    R._record_reemitted_outputs(proj)
    assert _rules(proj)[0] == "FAIL"

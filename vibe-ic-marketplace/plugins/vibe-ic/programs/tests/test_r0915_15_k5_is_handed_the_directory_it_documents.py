"""`phase1_k5_quality_check` is handed the directory its own usage documents.

MEASURED 2026-09-15 (lane icspm3, R-0915-15) on `spm` x gf180mcuD. The gate's
own usage line is::

    python3 phase1_k5_quality_check.py <generated_docs/>

and it takes that DIRECTORY as its positional. The umbrella handed it the
project root under the legacy project-positional convention, so it loaded no
document at all and answered::

    K5 census: docs loaded NONE; 0/13 checks examined anything;
    0 unit(s) examined in total.

which the umbrella booked ZERO_DENOMINATOR → P0 INCOMPLETE — on a run whose
Phase 1 PASSED and wrote 29 L-docs. This is the SAME defect the tree already
records one entry above, for `l9_response_delay_schema_check`: a gate with a
positional contract of its own, invoked as if it took the project root.

BOTH DIRECTIONS:
  * handed `phase1/generated_docs`, the gate READS the documents — K5-P stops
    saying "L1_DATASHEET.json absent" and starts reporting
    "L1.class_path is null/empty", which is a finding about the DESIGN;
  * handed a project with no Phase 1, the census still says so honestly, and
    ZERO_DENOMINATOR is then the right answer rather than an artefact of the
    argument.

chip-AGNOSTIC: the argv is built from the project path alone.
"""
from __future__ import annotations

import json
import subprocess  # nosec B404 — a declared flow program, argv from this file
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import flow_compliance_check as F  # noqa: E402

GATE = "phase1_k5_quality_check"


def test_the_gate_documents_a_directory_positional():
    """The claim this fix rests on, read from the program rather than assumed."""
    usage = (PROGRAMS / f"{GATE}.py").read_text(errors="replace")
    assert "phase1_k5_quality_check.py <generated_docs/>" in usage, (
        "the gate's documented invocation changed; re-derive the argv kind")


def test_the_registry_routes_it_to_that_directory():
    assert F._STRUCTURAL_GATE_INVOCATION_CONTRACTS.get(GATE) == \
        "generated-docs-positional", (
            F._STRUCTURAL_GATE_INVOCATION_CONTRACTS.get(GATE))


def test_the_argv_built_is_the_generated_docs_directory(tmp_path):
    argv = F._p0_contract_argv(GATE, tmp_path, tmp_path / "rtl",
                               tmp_path / "scratch")
    assert str(tmp_path / "phase1" / "generated_docs") in argv, argv
    assert argv[-1] != str(tmp_path), (
        "the gate is still being handed the project root", argv)


def _project(tmp_path, with_docs):
    gd = tmp_path / "phase1" / "generated_docs"
    if with_docs:
        gd.mkdir(parents=True)
        (gd / "L1_DATASHEET.json").write_text(json.dumps(
            {"doc_id": "L1", "applicability": "APPLICABLE",
             "fields": {"class_path": None}}))
    return tmp_path


def test_handed_the_directory_the_gate_reads_the_documents(tmp_path):
    proj = _project(tmp_path, with_docs=True)
    out = subprocess.run(  # nosec B603
        [sys.executable, str(PROGRAMS / f"{GATE}.py"),
         str(proj / "phase1" / "generated_docs")],
        capture_output=True, text=True, check=False).stdout
    assert "L1_DATASHEET.json absent" not in out, out[-600:]


def test_handed_a_project_with_no_phase1_the_census_still_says_so(tmp_path):
    """The conservative direction: the fix must not turn 'nothing to read' into
    a pass. A project that never ran Phase 1 still reports an empty census."""
    proj = _project(tmp_path, with_docs=False)
    out = subprocess.run(  # nosec B603
        [sys.executable, str(PROGRAMS / f"{GATE}.py"),
         str(proj / "phase1" / "generated_docs")],
        capture_output=True, text=True, check=False).stdout
    assert "docs loaded NONE" in out or "NOT CHECKED" in out, out[-600:]


def test_the_l9_precedent_is_still_wired(tmp_path):
    """The entry this one is modelled on must not be disturbed."""
    assert F._STRUCTURAL_GATE_INVOCATION_CONTRACTS.get(
        "l9_response_delay_schema_check") == "l9-positional"

"""W0 (`--librelane` v1): the import-manifest schema for external-flow runs.

Contracts:
  1. A row built from disk validates, and its provenance entry is accepted by
     the REAL `provenance_check` (allow-list on the underlying tool,
     --require-measured) — decision 4a: a witnessed run attributed to the flow.
  2. A symlinked canonical file, a digest that differs between the tool run
     and the canonical copy, a stale file, a stale source log, a missing flow
     step id, and a missing measurement are each refused with a reason.
  3. An invalid manifest writes nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _external_flow_manifest as M  # noqa: E402
import provenance_check  # noqa: E402

MEASURED = {"schema": "mcp-eda/measurement/1", "operation": "route",
            "measured": True, "not_measured_class": None,
            "not_measured_reason": None, "read": [], "wrote": [],
            "tool": "openroad"}
CANON = "phase3/stage3/pnr/routed.def"


@pytest.fixture
def world(tmp_path: Path):
    project = tmp_path / "proj"
    run = tmp_path / "ll_run" / "41-openroad-detailedrouting"
    run.mkdir(parents=True)
    tool_file = run / "design.def"
    tool_file.write_text("VERSION 5.8 ;\nDESIGN d ;\nEND DESIGN\n")
    log = run / "openroad-detailedrouting.log"
    log.write_text("[INFO DRT-0198] Complete detail routing.\n")
    (project / CANON).parent.mkdir(parents=True)
    shutil.copyfile(tool_file, project / CANON)
    return project, tool_file, log


def _row(project, tool_file, log, **over):
    kw = dict(step_id="21", canonical_path=CANON, tool_run_path=tool_file,
              flow="librelane", tool="openroad", source_log=log,
              timestamp="2026-09-28T01:00:00Z", exit_code=0,
              measurement=MEASURED, tool_step_id="OpenROAD.DetailedRouting",
              tool_version="26Q3")
    kw.update(over)
    return M.make_row(project, **kw)


def test_a_copied_row_validates_and_round_trips(world):
    project, tool_file, log = world
    row = _row(project, tool_file, log)
    assert M.validate_row(row, project) == []
    path = M.write_manifest(project, flow="librelane", run_dir=str(tool_file.parent),
                            rows=[row])
    assert path == project / M.MANIFEST_REL
    assert M.load_manifest(project)["rows"] == [row]


def test_the_provenance_entry_passes_the_real_provenance_check(world, capsys):
    project, tool_file, log = world
    entry = M.to_provenance_entry(_row(project, tool_file, log))
    assert entry["tool"] == "openroad" and entry["attributed_to"] == "librelane"
    assert entry["reconstructed"] is False
    assert entry["witness"] == {"kind": "tool_step_log",
                                "tool_step_id": "OpenROAD.DetailedRouting",
                                "log": str(log),
                                "log_sha256": M.sha256_file(log)}
    (project / "provenance.jsonl").write_text(json.dumps(entry) + "\n")
    rc = provenance_check.main([str(project), "--output", CANON,
                                "--tool", "openroad", "--require-measured"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "[PASS" in out and "INCOMPLETE:" not in out
    # The flow is not the tool: an allow-list naming only the flow refuses it.
    assert provenance_check.main([str(project), "--output", CANON,
                                  "--tool", "librelane"]) == 1


def test_a_failed_tool_step_does_not_bind_its_file(world, capsys):
    project, tool_file, log = world
    entry = M.to_provenance_entry(_row(project, tool_file, log, exit_code=1))
    (project / "provenance.jsonl").write_text(json.dumps(entry) + "\n")
    assert provenance_check.main([str(project), "--output", CANON,
                                  "--tool", "openroad"]) == 1


def test_a_symlinked_canonical_file_is_refused(world):
    project, tool_file, log = world
    (project / CANON).unlink()
    os.symlink(tool_file, project / CANON)
    problems = M.validate_row(_row(project, tool_file, log), project)
    assert any("symlink" in p for p in problems), problems


def test_a_symlinked_parent_directory_is_refused(world, tmp_path):
    project, tool_file, log = world
    shutil.rmtree(project / "phase3")
    elsewhere = tmp_path / "elsewhere" / "stage3" / "pnr"
    elsewhere.mkdir(parents=True)
    shutil.copyfile(tool_file, elsewhere / "routed.def")
    os.symlink(tmp_path / "elsewhere", project / "phase3")
    problems = M.validate_row(_row(project, tool_file, log), project)
    assert any("symlink" in p for p in problems), problems


def test_different_bytes_on_the_two_sides_are_refused(world):
    project, tool_file, log = world
    (project / CANON).write_text("VERSION 5.8 ;\nDESIGN other ;\nEND DESIGN\n")
    problems = M.validate_row(_row(project, tool_file, log), project)
    assert any("not the bytes the tool wrote" in p for p in problems), problems


@pytest.mark.parametrize("which", ["canonical", "tool", "log"])
def test_a_file_changed_after_the_row_was_written_is_refused(world, which):
    project, tool_file, log = world
    row = _row(project, tool_file, log)
    target = {"canonical": project / CANON, "tool": tool_file, "log": log}[which]
    target.write_text(target.read_text() + "# edited\n")
    problems = M.validate_row(row, project)
    assert any("no longer hashes" in p for p in problems), problems
    assert M.validate_row(row, project, verify_disk=False) == []


def test_each_flow_names_its_own_step_key(world):
    project, tool_file, log = world
    no_step = _row(project, tool_file, log, tool_step_id=None)
    assert any("tool_step_id" in p for p in M.validate_row(no_step, project))
    wrong = _row(project, tool_file, log, tool_step_id=None,
                 make_stage="5_2_route")
    assert M.validate_row(wrong, project)
    orfs = _row(project, tool_file, log, flow="orfs", tool_step_id=None,
                make_stage="5_2_route")
    assert M.validate_row(orfs, project) == []
    assert M.to_provenance_entry(orfs)["witness"]["make_stage"] == "5_2_route"


@pytest.mark.parametrize("measurement", [None, {}, {"measured": True},
                                         {**MEASURED, "measured": "yes"}])
def test_a_row_without_a_measurement_record_is_refused(world, measurement):
    project, tool_file, log = world
    row = _row(project, tool_file, log, measurement=measurement)
    assert any("measurement" in p for p in M.validate_row(row, project))


@pytest.mark.parametrize("rel", ["/abs/routed.def", "../outside.def"])
def test_a_canonical_path_outside_the_project_is_refused(world, rel):
    project, tool_file, log = world
    row = {**_row(project, tool_file, log), "canonical_path": rel}
    assert any("not inside the project" in p for p in M.validate_row(row, project))


def test_an_invalid_manifest_writes_nothing(world):
    project, tool_file, log = world
    good = _row(project, tool_file, log)
    with pytest.raises(M.ManifestError, match="already imported"):
        M.write_manifest(project, flow="librelane", run_dir="r", rows=[good, good])
    with pytest.raises(M.ManifestError, match="flow"):
        M.write_manifest(project, flow="orfs", run_dir="r", rows=[good])
    assert not (project / M.MANIFEST_REL).exists()


def test_the_manifest_sits_where_stage3_review_reads():
    assert M.MANIFEST_REL.startswith("reports/phase3/")

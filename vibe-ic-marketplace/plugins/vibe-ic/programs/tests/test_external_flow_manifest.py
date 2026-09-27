"""W0 (`--librelane` v1): the import-manifest schema for external-flow runs.

Contracts:
  1. A row built from disk validates, and its provenance entry is accepted by
     the REAL `provenance_check` (allow-list on the underlying tool,
     --require-measured) — decision 4a: a witnessed run attributed to the flow.
  2. The witness is a TOOL RUN: tool_run_path and source_log are stored
     relative to an absolute, existing run_dir, resolved against it whatever
     the cwd, and a row that witnesses itself (the canonical file as its own
     tool file or log, run_dir = the project) is refused.
  3. A symlinked canonical file, a digest that differs between the tool run
     and the canonical copy, a stale file, a stale source log, a missing flow
     step id, and a malformed or foreign measurement are each refused with a
     reason; `to_provenance_entry` refuses whatever `validate_row` refuses.
  4. `measurement: null` is the honest UNDECLARED state: the entry carries no
     record and provenance_check reports UNMEASURED (INCOMPLETE, rc 0).
  5. No tool version renders `version: None` with a NOT CAPTURED disclosure.
  6. An invalid manifest writes nothing.
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
STEP = "41-openroad-detailedrouting"


@pytest.fixture
def world(tmp_path: Path):
    project = tmp_path / "proj"
    run_dir = tmp_path / "ll_run"
    (run_dir / STEP).mkdir(parents=True)
    tool_file = run_dir / STEP / "design.def"
    tool_file.write_text("VERSION 5.8 ;\nDESIGN d ;\nEND DESIGN\n")
    log = run_dir / STEP / "openroad-detailedrouting.log"
    log.write_text("[INFO DRT-0198] Complete detail routing.\n")
    (project / CANON).parent.mkdir(parents=True)
    shutil.copyfile(tool_file, project / CANON)
    return project, run_dir, tool_file, log


def _row(project, run_dir, tool_file, log, **over):
    kw = dict(run_dir=run_dir, step_id="21", canonical_path=CANON,
              tool_run_path=tool_file, flow="librelane", tool="openroad",
              source_log=log, timestamp="2026-09-28T01:00:00Z", exit_code=0,
              measurement=MEASURED, tool_step_id="OpenROAD.DetailedRouting",
              tool_version="26Q3")
    kw.update(over)
    return M.make_row(project, **kw)


def test_a_copied_row_validates_and_round_trips(world):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log)
    assert row["tool_run_path"] == f"{STEP}/design.def"
    assert row["source_log"] == f"{STEP}/openroad-detailedrouting.log"
    assert M.validate_row(row, project, run_dir) == []
    path = M.write_manifest(project, flow="librelane", run_dir=str(run_dir),
                            rows=[row])
    assert path == project / M.MANIFEST_REL
    assert M.load_manifest(project)["rows"] == [row]


def test_paths_resolve_against_run_dir_not_the_cwd(world, tmp_path, monkeypatch):
    project, run_dir, tool_file, log = world
    # A same-named file in the cwd must never be the one hashed.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / STEP).mkdir(parents=True)
    (elsewhere / STEP / "design.def").write_text("OTHER BYTES\n")
    (elsewhere / STEP / "openroad-detailedrouting.log").write_text("OTHER\n")
    monkeypatch.chdir(elsewhere)
    row = _row(project, run_dir, Path(STEP) / "design.def",
               Path(STEP) / "openroad-detailedrouting.log")
    assert row["tool_run_sha256"] == M.sha256_file(tool_file)
    assert row["source_log_sha256"] == M.sha256_file(log)
    assert M.validate_row(row, project, run_dir) == []
    monkeypatch.chdir(run_dir)
    assert M.validate_row(row, project, run_dir) == []
    assert M.validate_row(row, project, str(run_dir)) == []


def test_the_provenance_entry_passes_the_real_provenance_check(world, capsys):
    project, run_dir, tool_file, log = world
    entry = M.to_provenance_entry(_row(project, run_dir, tool_file, log),
                                  project, run_dir)
    assert entry["tool"] == "openroad" and entry["attributed_to"] == "librelane"
    assert entry["reconstructed"] is False and entry["version"] == "26Q3"
    assert entry["witness"] == {"kind": "tool_step_log",
                                "tool_step_id": "OpenROAD.DetailedRouting",
                                "run_dir": str(run_dir), "log": str(log),
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


def test_an_undeclared_measurement_is_honestly_unmeasured(world, capsys):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log, measurement=None)
    assert M.validate_row(row, project, run_dir) == []
    entry = M.to_provenance_entry(row, project, run_dir)
    assert "measurement" not in entry
    (project / "provenance.jsonl").write_text(json.dumps(entry) + "\n")
    rc = provenance_check.main([str(project), "--output", CANON,
                                "--tool", "openroad", "--require-measured"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "UNMEASURED" in out and "INCOMPLETE:" in out


def test_no_tool_version_is_none_with_a_disclosure(world):
    project, run_dir, tool_file, log = world
    entry = M.to_provenance_entry(
        _row(project, run_dir, tool_file, log, tool_version=None),
        project, run_dir)
    assert entry["version"] is None
    assert entry["version_capture"].startswith("NOT CAPTURED:")


def test_a_failed_tool_step_does_not_bind_its_file(world, capsys):
    project, run_dir, tool_file, log = world
    entry = M.to_provenance_entry(_row(project, run_dir, tool_file, log,
                                       exit_code=1), project, run_dir)
    (project / "provenance.jsonl").write_text(json.dumps(entry) + "\n")
    assert provenance_check.main([str(project), "--output", CANON,
                                  "--tool", "openroad"]) == 1


def test_a_row_cannot_witness_itself(world):
    """The integrity review's forgery: no tool run, the published file named
    as its own tool file and log, run_dir the project."""
    project, run_dir, tool_file, log = world
    canon = project / CANON
    with pytest.raises(M.ManifestError):
        _row(project, run_dir, canon, canon)          # outside run_dir
    forged = _row(project, project, canon, canon)     # run_dir = project
    problems = M.validate_row(forged, project, project)
    assert any("the witness must be a tool run" in p for p in problems), problems
    assert any("cannot witness its own run" in p for p in problems), problems
    assert any("tool_run_path is the canonical file" in p for p in problems)
    with pytest.raises(M.ManifestError):
        M.to_provenance_entry(forged, project, project)
    with pytest.raises(M.ManifestError):
        M.write_manifest(project, flow="librelane", run_dir=str(project),
                         rows=[forged])


def test_a_run_dir_holding_the_canonical_file_is_refused(world, tmp_path):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log)
    problems = M.validate_row(row, project, project / "phase3")
    assert any("canonical file lies inside run_dir" in p for p in problems), problems


def test_witness_paths_outside_the_run_are_refused(world, tmp_path):
    project, run_dir, tool_file, log = world
    stray = tmp_path / "stray.def"
    shutil.copyfile(tool_file, stray)
    with pytest.raises(M.ManifestError, match="not inside run_dir"):
        _row(project, run_dir, stray, log)
    good = _row(project, run_dir, tool_file, log)
    for key, bad in (("tool_run_path", "../stray.def"),
                     ("tool_run_path", str(stray)),
                     ("source_log", "../stray.def")):
        problems = M.validate_row({**good, key: bad}, project, run_dir)
        assert any("not a relative path inside run_dir" in p
                   for p in problems), (key, bad, problems)
    # ...and through a symlink inside the run.
    os.symlink(stray, run_dir / STEP / "linked.def")
    problems = M.validate_row({**good, "tool_run_path": f"{STEP}/linked.def"},
                              project, run_dir)
    assert any("resolves outside run_dir" in p for p in problems), problems


@pytest.mark.parametrize("bad_run_dir", ["", "relative/run", None])
def test_run_dir_must_be_absolute(world, bad_run_dir):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log)
    assert any("is not an absolute path" in p
               for p in M.validate_row(row, project, bad_run_dir))
    with pytest.raises(M.ManifestError):
        M.make_row(project, **{**dict(
            run_dir=Path("relative/run"), step_id="21", canonical_path=CANON,
            tool_run_path=tool_file, flow="librelane", tool="openroad",
            source_log=log, timestamp="t", exit_code=0, measurement=None,
            tool_step_id="OpenROAD.DetailedRouting")})


def test_a_missing_run_dir_is_refused_on_disk(world):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log)
    shutil.rmtree(run_dir)
    problems = M.validate_row(row, project, run_dir)
    assert any("is not a directory" in p for p in problems), problems
    assert M.validate_row(row, project, run_dir, verify_disk=False) == []


def test_a_symlinked_canonical_file_is_refused_and_never_rendered(world):
    project, run_dir, tool_file, log = world
    (project / CANON).unlink()
    os.symlink(tool_file, project / CANON)
    row = _row(project, run_dir, tool_file, log)
    problems = M.validate_row(row, project, run_dir)
    assert any("symlink" in p for p in problems), problems
    with pytest.raises(M.ManifestError, match="symlink"):
        M.to_provenance_entry(row, project, run_dir)


def test_a_symlinked_parent_directory_is_refused(world, tmp_path):
    project, run_dir, tool_file, log = world
    shutil.rmtree(project / "phase3")
    elsewhere = tmp_path / "elsewhere" / "stage3" / "pnr"
    elsewhere.mkdir(parents=True)
    shutil.copyfile(tool_file, elsewhere / "routed.def")
    os.symlink(tmp_path / "elsewhere", project / "phase3")
    problems = M.validate_row(_row(project, run_dir, tool_file, log), project,
                              run_dir)
    assert any("symlink" in p for p in problems), problems


def test_different_bytes_are_refused_and_never_rendered(world):
    project, run_dir, tool_file, log = world
    (project / CANON).write_text("VERSION 5.8 ;\nDESIGN other ;\nEND DESIGN\n")
    row = _row(project, run_dir, tool_file, log)
    problems = M.validate_row(row, project, run_dir)
    assert any("not the bytes the tool wrote" in p for p in problems), problems
    with pytest.raises(M.ManifestError, match="not the bytes"):
        M.to_provenance_entry(row, project, run_dir)


@pytest.mark.parametrize("which", ["canonical", "tool", "log"])
def test_a_file_changed_after_the_row_was_written_is_refused(world, which):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log)
    target = {"canonical": project / CANON, "tool": tool_file, "log": log}[which]
    target.write_text(target.read_text() + "# edited\n")
    problems = M.validate_row(row, project, run_dir)
    assert any("no longer hashes" in p for p in problems), problems
    assert M.validate_row(row, project, run_dir, verify_disk=False) == []


def test_each_flow_names_its_own_step_key(world):
    project, run_dir, tool_file, log = world
    no_step = _row(project, run_dir, tool_file, log, tool_step_id=None)
    assert any("tool_step_id" in p
               for p in M.validate_row(no_step, project, run_dir))
    wrong = _row(project, run_dir, tool_file, log, tool_step_id=None,
                 make_stage="5_2_route")
    assert M.validate_row(wrong, project, run_dir)
    orfs = _row(project, run_dir, tool_file, log, flow="orfs",
                tool_step_id=None, make_stage="5_2_route")
    assert M.validate_row(orfs, project, run_dir) == []
    entry = M.to_provenance_entry(orfs, project, run_dir)
    assert entry["witness"]["make_stage"] == "5_2_route"


@pytest.mark.parametrize("measurement", [{}, {"measured": True},
                                         {**MEASURED, "measured": "yes"},
                                         "measured"])
def test_a_malformed_measurement_record_is_refused(world, measurement):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log, measurement=measurement)
    assert any("measurement" in p for p in M.validate_row(row, project, run_dir))


def test_another_tools_measurement_is_refused(world):
    project, run_dir, tool_file, log = world
    row = _row(project, run_dir, tool_file, log,
               measurement={**MEASURED, "tool": "klayout"})
    assert any("'klayout''s record" in p
               for p in M.validate_row(row, project, run_dir))


@pytest.mark.parametrize("rel", ["/abs/routed.def", "../outside.def"])
def test_a_canonical_path_outside_the_project_is_refused(world, rel):
    project, run_dir, tool_file, log = world
    row = {**_row(project, run_dir, tool_file, log), "canonical_path": rel}
    assert any("not a relative path inside the project" in p
               for p in M.validate_row(row, project, run_dir))


def test_an_invalid_manifest_writes_nothing(world):
    project, run_dir, tool_file, log = world
    good = _row(project, run_dir, tool_file, log)
    with pytest.raises(M.ManifestError, match="already imported"):
        M.write_manifest(project, flow="librelane", run_dir=str(run_dir),
                         rows=[good, good])
    with pytest.raises(M.ManifestError, match="flow"):
        M.write_manifest(project, flow="orfs", run_dir=str(run_dir), rows=[good])
    assert not (project / M.MANIFEST_REL).exists()


def test_the_manifest_sits_where_stage3_review_reads():
    assert M.MANIFEST_REL.startswith("reports/phase3/")

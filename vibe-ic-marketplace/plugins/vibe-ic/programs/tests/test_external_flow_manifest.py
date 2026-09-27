"""W0 (`--librelane` v1): the import manifest for external-flow runs (schema 2).

Contracts:
  1. ONE WITNESS SCHEMA (orchestrator ruling, llf_r3): a row's provenance
     entry is W19's `witnessed_row`, re-derived from the run itself; the real
     (witness-verifying) `provenance_check` accepts it, and it refuses what
     W19 refuses (a skipped or unfinished step, a transcript the flow's own
     log does not name).
  2. The witness is a TOOL RUN inside the project: run_dir is project-
     relative; tool_run_path, step_dir and source_logs are run-relative and
     resolved against it whatever the cwd; a row that witnesses itself
     (run_dir = the project, the canonical file as its own tool file or log)
     is refused, as are paths outside the run or outside step_dir.
  3. `source_logs` is a list: a step may cite several transcripts, or none
     (a row that will be a #365 back-fill).
  4. Symlinks, unequal digests, stale files, a missing step key, a tool that
     is not the step's tool, malformed or foreign measurements are refused.
  5. `measurement: null` is UNDECLARED: provenance_check reports UNMEASURED.
  6. No tool version renders `version: None` with a NOT CAPTURED disclosure.
  7. An invalid manifest writes nothing; `not_performed` is carried.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _external_flow_manifest as M  # noqa: E402
import _tool_log_provenance as T  # noqa: E402

CHECK = PROGRAMS / "provenance_check.py"
WHOLE = PROGRAMS / "calibration" / "librelane_flow_log_complete_negative.log"
#: A tool's self-report: the right shape, but nobody derived it.
SELF_REPORT = {"schema": "mcp-eda/measurement/1", "operation": "route",
               "measured": True, "not_measured_class": None,
               "not_measured_reason": None, "read": [], "wrote": [],
               "tool": "openroad"}
DEF_TEXT = ("VERSION 5.8 ;\nDESIGN top ;\nCOMPONENTS 3 ;\nEND COMPONENTS\n"
            "END DESIGN\n")
CANON = "phase3/stage3/pnr/routed.def"
RUN = "phase3/librelane/runs/seg2"
STEP = "44-openroad-detailedrouting"
STEP_ID = "OpenROAD.DetailedRouting"
LOG = f"{STEP}/openroad-detailedrouting.log"
SRC = f"{STEP}/top.def"


@pytest.fixture
def world(tmp_path: Path):
    project = tmp_path / "proj"
    run = project / RUN
    (run / STEP).mkdir(parents=True)
    shutil.copyfile(WHOLE, run / "flow.log")
    (run / LOG).write_text("[INFO DRT-0198] Complete detail routing.\n")
    (run / STEP / "state_out.json").write_text('{"def": "top.def"}\n')
    (run / SRC).write_text(DEF_TEXT)
    (project / CANON).parent.mkdir(parents=True)
    shutil.copyfile(run / SRC, project / CANON)
    return project, run


def _derived(project):
    return M.derived_measurement(project, CANON, "openroad")


def _row(project, run, **over):
    kw = dict(run_dir=RUN, step_id="21", canonical_path=CANON,
              tool_run_path=run / SRC, flow="librelane", tool="openroad",
              step_dir=STEP, source_logs=[run / LOG],
              timestamp="2026-09-28T01:00:00Z", exit_code=0,
              measurement=_derived(project), tool_step_id=STEP_ID,
              tool_version="26Q3")
    kw.update(over)
    return M.make_row(project, **kw)


def _check(project, *extra):
    return subprocess.run([sys.executable, str(CHECK), str(project),
                           "--output", CANON, "--tool", "openroad", *extra],
                          capture_output=True, text=True, timeout=120)


def test_the_field_names():
    assert M.SCHEMA == "vibe-ic/external-flow-import/2"
    assert M.ROW_KEYS == (
        "step_id", "canonical_path", "tool_run_path", "canonical_sha256",
        "tool_run_sha256", "flow", "tool", "step_dir", "source_logs",
        "timestamp", "exit_code", "measurement")
    assert M.MANIFEST_REL == "reports/phase3/impl/import_manifest.json"


def test_a_copied_row_validates_and_round_trips(world):
    project, run = world
    row = _row(project, run)
    assert row["tool_run_path"] == SRC and row["step_dir"] == STEP
    assert row["source_logs"] == [{"path": LOG,
                                   "sha256": M.sha256_file(run / LOG)}]
    assert M.validate_row(row, project, RUN) == []
    path = M.write_manifest(project, flow="librelane", run_dir=RUN,
                            rows=[row], not_performed=[{"step_id": "26.5ic"}])
    assert path == project / M.MANIFEST_REL
    loaded = M.load_manifest(project)
    assert loaded["rows"] == [row]
    assert loaded["not_performed"] == [{"step_id": "26.5ic"}]


def test_the_entry_is_w19s_witness_and_the_real_check_accepts_it(world):
    project, run = world
    entry = M.to_provenance_entry(_row(project, run), project, RUN)
    w19 = T.witnessed_row(project, flow="librelane", step_id=STEP_ID,
                          run_dir=run, step_dir=STEP,
                          outputs={CANON: run / SRC}, version="26Q3",
                          timestamp="2026-09-28T01:00:00Z")
    assert entry["witness"] == w19["witness"]
    assert {k: entry[k] for k in w19} == w19
    assert entry["measurement"] == _derived(project) and entry["step_id"] == "21"
    assert entry["measurement"]["stated_by"] == "runner-derived"
    assert entry["measurement"]["measured"] is True
    assert T.is_witnessed(entry, project)
    (project / "provenance.jsonl").write_text(json.dumps(entry) + "\n")
    r = _check(project, "--require-measured")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "INCOMPLETE:" not in r.stdout


def test_the_old_single_log_witness_is_refused_by_the_real_check(world):
    """What W0 used to emit (one log, no prefix, no completion, no sources)."""
    project, run = world
    old = {"timestamp": "t", "tool": "openroad", "attributed_to": "librelane",
           "outputs": {CANON: M.sha256_file(project / CANON)}, "exit_code": 0,
           "reconstructed": False,
           "witness": {"kind": "tool_step_log", "tool_step_id": STEP_ID,
                       "log": str(run / LOG),
                       "log_sha256": M.sha256_file(run / LOG)}}
    (project / "provenance.jsonl").write_text(json.dumps(old) + "\n")
    r = _check(project)
    assert r.returncode == 1 and "claims a witness that does not hold" in r.stdout


def test_an_undeclared_measurement_is_honestly_unmeasured(world):
    project, run = world
    row = _row(project, run, measurement=None)
    assert M.validate_row(row, project, RUN) == []
    entry = M.to_provenance_entry(row, project, RUN)
    assert "measurement" not in entry
    (project / "provenance.jsonl").write_text(json.dumps(entry) + "\n")
    r = _check(project, "--require-measured")
    assert r.returncode == 0, r.stdout
    assert "UNMEASURED" in r.stdout and "INCOMPLETE:" in r.stdout


def test_no_tool_version_is_none_with_a_disclosure(world):
    project, run = world
    entry = M.to_provenance_entry(_row(project, run, tool_version=None),
                                  project, RUN)
    assert entry["version"] is None
    assert entry["version_capture"].startswith("NOT CAPTURED:")
    assert T.is_witnessed(entry, project)


def test_what_w19_refuses_is_never_rendered(world):
    project, run = world
    row = _row(project, run)
    (run / STEP / "state_out.json").unlink()          # the step never finished
    with pytest.raises(M.ManifestError, match="did not finish"):
        M.to_provenance_entry(row, project, RUN)


def test_a_transcript_the_flow_log_does_not_name_is_refused(world):
    project, run = world
    (run / STEP / "notes.log").write_text("hand written\n")
    row = _row(project, run, source_logs=[run / LOG, run / STEP / "notes.log"])
    assert M.validate_row(row, project, RUN) == []   # a list is a valid shape
    with pytest.raises(M.ManifestError, match="not a transcript"):
        M.to_provenance_entry(row, project, RUN)


def test_an_exit_code_that_disagrees_with_the_witness_is_refused(world):
    project, run = world
    with pytest.raises(M.ManifestError, match="disagrees with the witness"):
        M.to_provenance_entry(_row(project, run, exit_code=1), project, RUN)


def test_source_logs_may_list_several_or_none(world):
    project, run = world
    (run / STEP / "second.log").write_text("x\n")
    two = _row(project, run, source_logs=[run / LOG, run / STEP / "second.log"])
    none = _row(project, run, source_logs=[])
    assert M.validate_row(two, project, RUN) == []
    assert M.validate_row(none, project, RUN) == []
    assert T.is_witnessed(M.to_provenance_entry(none, project, RUN), project)


def test_paths_resolve_against_run_dir_not_the_cwd(world, tmp_path, monkeypatch):
    project, run = world
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / STEP).mkdir(parents=True)
    (elsewhere / SRC).write_text("OTHER BYTES\n")
    (elsewhere / LOG).write_text("OTHER\n")
    monkeypatch.chdir(elsewhere)
    row = _row(project, run, tool_run_path=Path(SRC), source_logs=[Path(LOG)])
    assert row["tool_run_sha256"] == M.sha256_file(run / SRC)
    assert row["source_logs"][0]["sha256"] == M.sha256_file(run / LOG)
    assert M.validate_row(row, project, RUN) == []
    monkeypatch.chdir(run)
    assert M.validate_row(row, project, RUN) == []


def test_a_row_cannot_witness_itself(world):
    """The integrity review's forgery: the published file as its own tool file
    and log, run_dir the project."""
    project, run = world
    canon = project / CANON
    with pytest.raises(M.ManifestError):
        _row(project, run, tool_run_path=canon, source_logs=[canon])
    forged = _row(project, run, run_dir=".", tool_run_path=canon,
                  source_logs=[canon], step_dir="phase3")
    problems = M.validate_row(forged, project, ".")
    assert any("the witness must be a tool run" in p for p in problems), problems
    assert any("cannot witness its own run" in p for p in problems), problems
    assert any("tool_run_path is the canonical file" in p for p in problems)
    with pytest.raises(M.ManifestError):
        M.to_provenance_entry(forged, project, ".")
    with pytest.raises(M.ManifestError):
        M.write_manifest(project, flow="librelane", run_dir=".", rows=[forged])


def test_a_run_dir_holding_the_canonical_file_is_refused(world):
    project, run = world
    row = _row(project, run)
    problems = M.validate_row(row, project, "phase3")
    assert any("canonical file lies inside run_dir" in p for p in problems), problems


def test_witness_paths_outside_the_run_or_the_step_are_refused(world, tmp_path):
    project, run = world
    stray = tmp_path / "stray.def"
    shutil.copyfile(run / SRC, stray)
    with pytest.raises(M.ManifestError, match="not inside run_dir"):
        _row(project, run, tool_run_path=stray)
    good = _row(project, run)
    for key, bad in (("tool_run_path", "../stray.def"),
                     ("tool_run_path", str(stray)),
                     ("step_dir", "../x")):
        problems = M.validate_row({**good, key: bad}, project, RUN)
        assert any("not a relative path inside run_dir" in p
                   for p in problems), (key, bad, problems)
    bad_log = {**good, "source_logs": [{"path": "../../x.log",
                                        "sha256": good["source_logs"][0]["sha256"]}]}
    assert any("not a relative path inside run_dir" in p
               for p in M.validate_row(bad_log, project, RUN))
    os.symlink(stray, run / STEP / "linked.def")
    problems = M.validate_row({**good, "tool_run_path": f"{STEP}/linked.def"},
                              project, RUN)
    assert any("resolves outside run_dir" in p for p in problems), problems
    (run / "other").mkdir()
    shutil.copyfile(run / SRC, run / "other" / "top.def")
    problems = M.validate_row({**good, "tool_run_path": "other/top.def"},
                              project, RUN)
    assert any("not inside step_dir" in p for p in problems), problems


@pytest.mark.parametrize("bad_run_dir", ["", "/abs/run", "../run", None])
def test_run_dir_must_be_project_relative(world, bad_run_dir):
    project, run = world
    row = _row(project, run)
    assert any("is not a project-relative path" in p
               for p in M.validate_row(row, project, bad_run_dir))
    with pytest.raises(M.ManifestError):
        _row(project, run, run_dir=bad_run_dir or "")


def test_a_missing_run_dir_is_refused_on_disk(world):
    project, run = world
    row = _row(project, run)
    shutil.rmtree(run)
    problems = M.validate_row(row, project, RUN)
    assert any("is not a directory" in p for p in problems), problems
    assert M.validate_row(row, project, RUN, verify_disk=False) == []


def test_a_symlinked_canonical_file_is_refused_and_never_rendered(world):
    project, run = world
    (project / CANON).unlink()
    os.symlink(run / SRC, project / CANON)
    row = _row(project, run)
    assert any("symlink" in p for p in M.validate_row(row, project, RUN))
    with pytest.raises(M.ManifestError, match="symlink"):
        M.to_provenance_entry(row, project, RUN)


def test_different_bytes_are_refused_and_never_rendered(world):
    project, run = world
    (project / CANON).write_text(DEF_TEXT.replace("DESIGN top", "DESIGN other"))
    row = _row(project, run)
    assert any("not the bytes the tool wrote" in p
               for p in M.validate_row(row, project, RUN))
    with pytest.raises(M.ManifestError, match="not the bytes"):
        M.to_provenance_entry(row, project, RUN)


@pytest.mark.parametrize("which", ["canonical", "tool", "log"])
def test_a_file_changed_after_the_row_was_written_is_refused(world, which):
    project, run = world
    row = _row(project, run)
    target = {"canonical": project / CANON, "tool": run / SRC,
              "log": run / LOG}[which]
    target.write_text(target.read_text() + "# edited\n")
    assert any("no longer hashes" in p for p in M.validate_row(row, project, RUN))
    assert M.validate_row(row, project, RUN, verify_disk=False) == []


def test_each_flow_names_its_own_step_key_and_its_tool(world):
    project, run = world
    assert any("tool_step_id" in p for p in M.validate_row(
        _row(project, run, tool_step_id=None), project, RUN))
    assert M.validate_row(_row(project, run, make_stage="5_2_route"),
                          project, RUN)
    orfs = _row(project, run, flow="orfs", tool_step_id=None,
                make_stage="5_2_route")
    assert M.validate_row(orfs, project, RUN) == []
    with pytest.raises(M.ManifestError, match="no witness rule"):
        M.to_provenance_entry(orfs, project, RUN)
    wrong_tool = _row(project, run, tool="magic", measurement=None)
    assert any("is not the tool" in p
               for p in M.validate_row(wrong_tool, project, RUN))


@pytest.mark.parametrize("measurement", [{}, {"measured": True},
                                         {**SELF_REPORT, "measured": "yes"},
                                         "measured"])
def test_a_malformed_measurement_record_is_refused(world, measurement):
    project, run = world
    row = _row(project, run, measurement=measurement)
    assert any("measurement" in p for p in M.validate_row(row, project, RUN))
    # VALIDATE FIRST: witnessed_row would accept this row's witness; only the
    # validation in front of it keeps the claim out of a witnessed entry.
    with pytest.raises(M.ManifestError, match="measurement"):
        M.to_provenance_entry(row, project, RUN)


@pytest.mark.parametrize("tool", ["klayout", ""])
def test_another_tools_or_no_tools_measurement_is_refused(world, tool):
    project, run = world
    row = _row(project, run, measurement={**_derived(project), "tool": tool})
    assert any(f"{tool!r}'s record" in p
               for p in M.validate_row(row, project, RUN))
    with pytest.raises(M.ManifestError, match="measurement"):
        M.to_provenance_entry(row, project, RUN)


def test_a_self_reported_measurement_is_refused(world):
    """Right shape, right tool, measured:true -- but nobody derived it."""
    project, run = world
    row = _row(project, run, measurement=SELF_REPORT)
    assert any("not the record derived" in p
               for p in M.validate_row(row, project, RUN))
    with pytest.raises(M.ManifestError, match="not the record derived"):
        M.to_provenance_entry(row, project, RUN)
    assert M.validate_row(row, project, RUN, verify_disk=False) == []


def test_a_hand_flipped_measurement_is_refused_not_passed(world):
    """Integrity review MAJOR: a DEF with COMPONENTS 0 derives measured:false
    (TOOL_DID_NOT_RUN); editing the manifest to measured:true must never turn
    that FAIL into a PASS."""
    project, run = world
    empty = DEF_TEXT.replace("COMPONENTS 3", "COMPONENTS 0")
    (run / SRC).write_text(empty)
    (project / CANON).write_text(empty)
    row = _row(project, run)
    assert row["measurement"]["measured"] is False
    entry = M.to_provenance_entry(row, project, RUN)
    (project / "provenance.jsonl").write_text(json.dumps(entry) + "\n")
    assert _check(project, "--require-measured").returncode == 1
    M.write_manifest(project, flow="librelane", run_dir=RUN, rows=[row])
    mp = project / M.MANIFEST_REL
    doc = json.loads(mp.read_text())
    doc["rows"][0]["measurement"]["measured"] = True
    doc["rows"][0]["measurement"].pop("not_measured_class", None)
    doc["rows"][0]["measurement"].pop("not_measured_reason", None)
    mp.write_text(json.dumps(doc))
    with pytest.raises(M.ManifestError, match="not the record derived"):
        M.load_manifest(project)
    with pytest.raises(M.ManifestError, match="not the record derived"):
        M.to_provenance_entry(doc["rows"][0], project, RUN)


def test_a_record_where_nothing_can_be_derived_must_be_null(world):
    project, run = world
    lef = "phase3/stage4/hardmacro/top.lef"
    (run / STEP / "top.lef").write_text("MACRO top\nEND top\n")
    (project / lef).parent.mkdir(parents=True)
    shutil.copyfile(run / STEP / "top.lef", project / lef)
    assert M.derived_measurement(project, lef, "openroad") is None
    row = _row(project, run, canonical_path=lef, tool_run_path=run / STEP / "top.lef",
               measurement={**SELF_REPORT, "wrote": [lef]})
    assert any("must be null" in p for p in M.validate_row(row, project, RUN))


def test_a_symlinked_or_relative_project_path_still_witnesses(world, tmp_path,
                                                              monkeypatch):
    project, run = world
    row = _row(project, run)
    link = tmp_path / "linkdir"
    os.symlink(project.parent, link)
    via_link = link / project.name
    assert M.validate_row(row, via_link, RUN) == []
    assert T.is_witnessed(M.to_provenance_entry(row, via_link, RUN), project)
    monkeypatch.chdir(project.parent)
    rel = Path(project.name)
    assert T.is_witnessed(M.to_provenance_entry(row, rel, RUN), project)


@pytest.mark.parametrize("rel", ["/abs/routed.def", "../outside.def"])
def test_a_canonical_path_outside_the_project_is_refused(world, rel):
    project, run = world
    row = {**_row(project, run), "canonical_path": rel}
    assert any("not a relative path inside the project" in p
               for p in M.validate_row(row, project, RUN))


def test_an_invalid_manifest_writes_nothing(world):
    project, run = world
    good = _row(project, run)
    with pytest.raises(M.ManifestError, match="already imported"):
        M.write_manifest(project, flow="librelane", run_dir=RUN,
                         rows=[good, good])
    with pytest.raises(M.ManifestError, match="flow"):
        M.write_manifest(project, flow="orfs", run_dir=RUN, rows=[good])
    with pytest.raises(M.ManifestError, match="not_performed"):
        M.write_manifest(project, flow="librelane", run_dir=RUN, rows=[good],
                         not_performed="x")
    assert not (project / M.MANIFEST_REL).exists()

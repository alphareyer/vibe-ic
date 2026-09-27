"""llv1 W6: a LibreLane run is imported into the canonical tree, bound by sha.

The fixtures are the two CMP3 LibreLane arms (8HD-4 and 8HD-9), trimmed; see
`fixtures/librelane_import/PROVENANCE.md`. Each test copies one into a fresh
project the way the two-segment driver will leave it (the run INSIDE the
project) and imports it.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
_PLUGIN = PROGRAMS.parent
for _p in (str(PROGRAMS), str(_PLUGIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "librelane_import"
RUN_REL = "phase3/librelane/runs/cmp3"
CHECK = PROGRAMS / "provenance_check.py"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _project(tmp_path: Path, host: str = "8HD-4") -> Path:
    proj = tmp_path / "proj"
    run = proj / RUN_REL
    run.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES / host / "runs" / "cmp3", run)
    return proj


def _import(proj: Path):
    import librelane_import as LI
    return LI.import_run(proj, proj / RUN_REL)


def _rows(proj: Path):
    return [json.loads(l) for l in
            (proj / "provenance.jsonl").read_text().splitlines() if l.strip()]


def _canonical(proj: Path):
    """Every file the import could have written, outside the run itself."""
    return sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*")
                  if (p.is_file() or p.is_symlink())
                  and "phase3/librelane/runs" not in p.relative_to(proj).as_posix()
                  and "phase3/librelane/seg" not in p.relative_to(proj).as_posix())


def _nothing_written(proj: Path, before=()):
    assert _canonical(proj) == sorted(before), _canonical(proj)
    assert not list(proj.glob(".librelane_import.*"))


def _flow_lines(proj: Path, run_rel: str = RUN_REL):
    return (proj / run_rel / "flow.log").read_text().splitlines(keepends=True)


def _at(lines, needle: str) -> int:
    return next(i for i, l in enumerate(lines)
                if l.startswith("Running") and needle in l)


# ── the import itself ─────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["8HD-4", "8HD-9"])
def test_every_imported_file_is_a_copy_bound_on_both_sides(tmp_path, host):
    proj = _project(tmp_path, host)
    doc = _import(proj)
    assert doc["top"] == "spm" and len(doc["rows"]) > 100
    for row in doc["rows"]:
        # W0's meaning: tool-run paths are relative to the row's run_dir
        assert row["run_dir"] == RUN_REL, row
        base = proj / row["run_dir"]
        dest = proj / row["canonical_path"]
        src = base / row["tool_run_path"]
        assert dest.is_file() and not dest.is_symlink(), row
        assert src.is_file() and not src.is_symlink(), row
        assert row["tool_run_sha256"] == row["canonical_sha256"] \
            == "sha256:" + _sha(dest) == "sha256:" + _sha(src), row
        assert row["provenance"] == "witnessed", row
        assert row["flow"] == "librelane" and row["exit_code"] == 0, row
        if row["source_log"] is not None:          # the log that wrote it
            log = base / row["source_log"]
            assert row["source_log_sha256"] == "sha256:" + _sha(log)
            assert Path(row["source_log"]).parent in Path(
                row["tool_run_path"]).parents, row
    manifest = json.loads((proj / "phase3/librelane/import_manifest.json")
                          .read_text())
    assert manifest == doc


def test_the_canonical_views_land_where_the_gates_read_them(tmp_path):
    proj = _project(tmp_path)
    doc = _import(proj)
    by = {r["canonical_path"]: r for r in doc["rows"]}
    want = {
        "phase2/stage2/synth/netlist.v": ("9", "Yosys.Synthesis"),
        "phase3/stage3/pnr/floorplan.def": ("15", "OpenROAD.GeneratePDN"),
        "phase3/stage3/pnr/placed.def": ("17", "OpenROAD.DetailedPlacement"),
        "phase3/stage3/pnr/post_cts.def": ("19", "OpenROAD.CTS"),
        "phase3/stage3/cts/clock_tree.rpt": ("19", "OpenROAD.CTS"),
        "phase3/stage3/pnr/post_hold.def": ("20", "OpenROAD.ResizerTimingPostCTS"),
        "phase3/stage3/pnr/routed.def": ("21", "OpenROAD.DetailedRouting"),
        "phase3/stage3/pnr/routed_router.drc.rpt": ("21", "OpenROAD.DetailedRouting"),
        "phase3/stage3/extracted/spm.spef": ("22", "OpenROAD.RCX"),
        "phase3/stage3/extracted/spef_corners/spm.max.spef": ("22", "OpenROAD.RCX"),
        "phase3/stage3/pnr/filled.def": ("34", "OpenROAD.FillInsertion"),
        "phase3/stage4/gds/spm.gds": ("37", "Magic.StreamOut"),
        "phase3/stage4/hardmacro/spm.lef": ("37.5ip", "Magic.WriteLEF"),
    }
    for rel, (step, tool_step) in want.items():
        assert rel in by, rel
        assert (by[rel]["step_id"], by[rel]["tool_step_id"]) == (step, tool_step)
    # A corner folder holds two transcripts (sta.log, filter_unannotated.log):
    # no single source log is named rather than guessing one. The step's
    # provenance row still cites both.
    corner = next(r for r in doc["rows"] if r["step_id"] == "23"
                  and r["tool_run_path"].endswith("/nom_tt_025C_5v00/max.rpt"))
    assert corner["source_log"] is None and corner["source_log_sha256"] is None
    rcx = by["phase3/stage3/extracted/spef_corners/spm.nom.spef"]
    assert rcx["source_log"] == "54-openroad-rcx/nom/rcx.log"
    assert by["phase3/stage3/pnr/routed.def"]["source_log"] == \
        "44-openroad-detailedrouting/openroad-detailedrouting.log"
    # the nominal SPEF is the same tool file as the nom corner
    assert by["phase3/stage3/extracted/spm.spef"]["tool_run_sha256"] == \
        by["phase3/stage3/extracted/spef_corners/spm.nom.spef"]["tool_run_sha256"]
    # LibreLane's own sign-off reports never take a vibe-ic sign-off name
    for rel, row in by.items():
        if row["step_id"] in ("10", "23", "26", "31"):
            assert rel.startswith(f"reports/phase3/librelane/{row['step_id']}/"), rel
    assert not (proj / "reports/phase3/drc_signoff.rpt").exists()
    assert not (proj / "reports/phase3/lvs.rpt").exists()


def test_a_step_this_flow_did_not_run_is_listed_not_invented(tmp_path):
    proj = _project(tmp_path)
    doc = _import(proj)
    assert doc["not_performed"] == [{
        "flow_step": "15.5ic", "tool_step": "OpenROAD.PadRing",
        "reason": "the run's own flow.log never started OpenROAD.PadRing",
        "flow_complete": True}]
    assert doc["segments"][0]["flow_status"]["complete"] is True
    assert not (proj / "phase3/stage3/pnr/padring.def").exists()


def test_the_post_route_antenna_check_is_imported(tmp_path):
    """CheckAntennas runs top-level after global routing, nested inside
    RepairAntennas, and top-level again after the detailed route; rule 26
    takes the top-level run after DetailedRouting."""
    proj = _project(tmp_path)
    doc = _import(proj)
    ant = [r for r in doc["rows"] if r["step_id"] == "26"]
    assert ant and {r["tool_step_id"] for r in ant} == {"OpenROAD.CheckAntennas-1"}
    assert all(r["tool_run_path"].startswith("46-openroad-checkantennas-1/")
               for r in ant)


# ── provenance ────────────────────────────────────────────────────────────

def test_each_step_is_one_witnessed_row_the_flow_allow_lists_accept(tmp_path):
    import _tool_log_provenance as T
    proj = _project(tmp_path)
    _import(proj)
    rows = _rows(proj)
    steps = [r for r in rows if r.get("reconstructed") is False]
    assert len(steps) == 17
    assert all(T.is_witnessed(r, proj) for r in steps)
    assert all(r["attributed_to"] == "librelane" for r in steps)
    for out, tools in (("phase3/stage3/pnr/routed.def", "openroad"),
                       ("phase3/stage4/gds/spm.gds", "klayout,magic,openroad"),
                       ("phase3/stage3/extracted/spm.spef", "magic,openroad"),
                       ("phase2/stage2/synth/netlist.v", "yosys,yosys-abc")):
        r = subprocess.run([sys.executable, str(CHECK), str(proj), "--output",
                            out, "--tool", tools], capture_output=True,
                           text=True, timeout=120)
        assert r.returncode == 0, (out, r.stdout, r.stderr)


def test_a_log_edited_after_the_import_unbinds_its_outputs(tmp_path):
    """The CTS report: only step 19 wrote those bytes. (`provenance_check`
    also binds by digest, and in this fixture two DEF heads are identical and
    several reports are empty, so those would still bind to another row.)"""
    proj = _project(tmp_path)
    _import(proj)

    def check():
        return subprocess.run(
            [sys.executable, str(CHECK), str(proj), "--output",
             "phase3/stage3/cts/clock_tree.rpt", "--tool", "openroad"],
            capture_output=True, text=True, timeout=120)
    assert check().returncode == 0
    log = proj / RUN_REL / "35-openroad-cts/openroad-cts.log"
    log.write_text(log.read_text() + "edited\n")
    r = check()
    assert r.returncode == 1 and "does not hold" in r.stdout, r.stdout


def test_the_assembled_openroad_log_cites_every_section(tmp_path):
    proj = _project(tmp_path)
    doc = _import(proj)
    text = (proj / "phase3/stage3/pnr/openroad.log").read_text()
    stages = re.findall(r"^PNR_STAGE: (\S+)$", text, re.M)
    assert stages == ["floorplan", "placement", "cts", "hold_repair",
                      "global_route", "detailed_route"]
    sections = re.findall(
        r"^# >>> LIBRELANE (\S+) (\S+) sha256:([0-9a-f]{64})\n(.*?)"
        r"^# <<< LIBRELANE \1\n", text, re.M | re.S)
    assert len(sections) == len(doc["openroad_log"]["sources"]) > 10
    for step, rel, sha, body in sections:
        src = proj / rel
        assert _sha(src) == sha, rel
        assert body.rstrip("\n") == src.read_text(errors="replace").rstrip("\n")
        assert not step.split(".", 1)[1].startswith("STA"), step
    # the STA sessions inside the span did run, and wrote logs
    assert list((proj / RUN_REL / "43-openroad-stamidpnr-3").rglob("*.log"))
    backfill = [r for r in _rows(proj)
                if "phase3/stage3/pnr/openroad.log" in r["outputs"]]
    assert len(backfill) == 1 and backfill[0]["reconstructed"] is True


# ── keyed on the run's own records ────────────────────────────────────────

def test_the_import_keys_on_the_flow_log_not_the_folder_ordinal(tmp_path):
    proj = _project(tmp_path)
    run = proj / RUN_REL
    (run / "44-openroad-detailedrouting").rename(run / "99-renamed")
    flow = run / "flow.log"
    flow.write_text(flow.read_text().replace("44-openroad-detailedrouting",
                                             "99-renamed"))
    for st in run.rglob("state_out.json"):
        st.write_text(st.read_text().replace("44-openroad-detailedrouting",
                                             "99-renamed"))
    doc = _import(proj)
    routed = [r for r in doc["rows"]
              if r["canonical_path"] == "phase3/stage3/pnr/routed.def"]
    assert routed and routed[0]["tool_run_path"].startswith("99-renamed/")


def test_a_folder_whose_config_names_another_step_is_refused(tmp_path):
    import librelane_contract as C
    proj = _project(tmp_path)
    cfg = proj / RUN_REL / "44-openroad-detailedrouting/config.json"
    doc = json.loads(cfg.read_text())
    doc["meta"]["step"] = "OpenROAD.GlobalRouting"
    cfg.write_text(json.dumps(doc))
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_STEP_MISMATCH"


def test_a_view_another_step_wrote_is_not_attributed_to_this_one(tmp_path):
    import librelane_contract as C
    proj = _project(tmp_path)
    st = proj / RUN_REL / "44-openroad-detailedrouting/state_out.json"
    doc = json.loads(st.read_text())
    doc["def"] = doc["def"].replace("44-openroad-detailedrouting",
                                    "34-openroad-detailedplacement")
    st.write_text(json.dumps(doc))
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_VIEW_NOT_OWN"


def test_a_run_that_stopped_in_a_step_is_refused(tmp_path):
    """flow.log ends in DetailedRouting: LibreLane never finished the run."""
    import librelane_contract as C
    proj = _project(tmp_path)
    flow = proj / RUN_REL / "flow.log"
    lines = flow.read_text().splitlines(keepends=True)
    cut = next(i for i, l in enumerate(lines)
               if "'OpenROAD.DetailedRouting'" in l and l.startswith("Running"))
    flow.write_text("".join(lines[:cut + 1]))
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_FLOW_INCOMPLETE"
    assert "OpenROAD.DetailedRouting at 44-openroad-detailedrouting" in str(exc.value)
    _nothing_written(proj)


def test_a_chosen_step_without_its_state_out_is_refused(tmp_path):
    """The flow finished, yet the step folder the rule takes has no
    state_out.json: LibreLane writes it only after run() returns."""
    import librelane_contract as C
    proj = _project(tmp_path)
    (proj / RUN_REL / "35-openroad-cts/state_out.json").unlink()
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_STEP_NOT_COMPLETED"
    _nothing_written(proj)


def test_a_symlink_at_a_destination_is_replaced_not_written_through(tmp_path):
    proj = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere.def"
    elsewhere.write_text("not the routed design\n")
    dest = proj / "phase3/stage3/pnr/routed.def"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(elsewhere)
    rpt = proj / "phase3/stage3/cts/clock_tree.rpt"      # a copied file
    rpt.parent.mkdir(parents=True)
    rpt.symlink_to(elsewhere)
    _import(proj)
    for p in (dest, rpt):
        assert p.is_file() and not p.is_symlink(), p
    assert elsewhere.read_text() == "not the routed design\n"


def test_a_run_outside_the_project_is_refused(tmp_path):
    import librelane_contract as C
    import librelane_import as LI
    proj = _project(tmp_path)
    outside = tmp_path / "outside"
    shutil.copytree(proj / RUN_REL, outside / "cmp3")
    with pytest.raises(C.Refusal) as exc:
        LI.import_run(proj, outside / "cmp3")
    assert exc.value.code == "LL_IMPORT_RUN_OUTSIDE_PROJECT"


# ── a run LibreLane did not finish is never imported (review W6 MAJOR) ─────

@pytest.mark.parametrize("next_step,last", [
    # W19's aborted calibration shape: the flow stops in STAMidPNR-3
    ("'OpenROAD.DetailedRouting'",
     "OpenROAD.STAMidPNR-3 at 43-openroad-stamidpnr-3"),
    # the IR-drop report dies: before, the import returned normally with
    # 31, 37 and 37.5ip listed as steps the flow 'never started'
    ("'Magic.StreamOut'", "OpenROAD.IRDropReport at 56-openroad-irdropreport"),
])
def test_a_run_that_died_outside_every_rule_is_refused_not_partially_imported(
        tmp_path, next_step, last):
    """The review's scenario: the flow dies in a step no rule names."""
    import librelane_contract as C
    proj = _project(tmp_path)
    lines = _flow_lines(proj)
    cut = _at(lines, next_step)
    (proj / RUN_REL / "flow.log").write_text("".join(lines[:cut]))
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_FLOW_INCOMPLETE"
    msg = str(exc.value)
    assert last in msg, msg
    folder = last.split(" at ")[1]
    assert f"{folder}/" in msg.split("tool's own log:")[1], msg
    _nothing_written(proj)


@pytest.mark.parametrize("appended", ["a step", "nothing"])
def test_an_aborted_rerun_appended_to_a_finished_log_is_refused(tmp_path,
                                                                appended):
    """flow.log is appended per invocation: the LAST one decides, even when
    it died before starting any step."""
    import librelane_contract as C
    proj = _project(tmp_path)
    lines = _flow_lines(proj)
    i = _at(lines, "'OpenROAD.DetailedRouting'")
    (proj / RUN_REL / "flow.log").write_text(
        "".join(lines) + "Starting…\n" + (lines[i] if appended == "a step" else ""))
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_FLOW_INCOMPLETE"
    _nothing_written(proj)


def test_the_flow_status_reader_is_calibrated():
    import instrument_calibration as I
    import librelane_import as LI
    I.assert_calibrated("librelane_import::flow_status")
    cal = PROGRAMS / "calibration"
    failed = LI.flow_status((cal / "librelane_flow_log_failed_positive.log")
                            .read_text())
    assert failed["complete"] is False
    assert failed["last_started"][0] == "OpenROAD.STAMidPNR"
    assert LI.flow_status((cal / "librelane_flow_log_finished_negative.log")
                          .read_text())["complete"] is True


# ── segments (review W6: the two-segment plan is one call) ────────────────

SEG1, SEG2 = "phase3/librelane/seg1/runs/cmp3", "phase3/librelane/seg2/runs/cmp3"
SEG1_END = "Checker.NetlistAssignStatements"


def _segment_project(tmp_path: Path) -> Path:
    """Segment 1 stops --to NetlistAssignStatements; segment 2 runs from
    CheckSDCFiles. Each flow.log is the real one cut the way LibreLane
    writes a --to / --from run: the steps it ran, then its closing lines."""
    proj = tmp_path / "proj"
    for rel in (SEG1, SEG2):
        (proj / rel).parent.mkdir(parents=True)
        shutil.copytree(FIXTURES / "8HD-4" / "runs" / "cmp3", proj / rel)
    lines = _flow_lines(proj, SEG1)
    head = lines[:_at(lines, "'OpenROAD.CheckSDCFiles'")]
    end = [l for l in lines if l.startswith("Saving views") or
           l.startswith("Flow complete.")]
    assert len(end) == 2
    (proj / SEG1 / "flow.log").write_text("".join(head + end))
    # The trim kept state_out.json only for steps a rule imports; LibreLane
    # writes one for every step that returns, the declared end included.
    (proj / SEG1 / "09-checker-netlistassignstatements/state_out.json") \
        .write_text("{}\n")
    (proj / SEG2 / "flow.log").write_text(
        "Starting…\n" + "".join(lines[_at(lines, "'OpenROAD.CheckSDCFiles'"):]))
    return proj


def test_a_to_segment_is_complete_at_its_declared_end(tmp_path):
    import librelane_contract as C
    import librelane_import as LI
    proj = _segment_project(tmp_path)
    doc = LI.import_run(proj, proj / SEG1, SEG1_END)
    assert {r["canonical_path"] for r in doc["rows"]
            if not r["canonical_path"].startswith("reports/")} == \
        {"phase2/stage2/synth/netlist.v"}
    assert doc["segments"][0]["flow_status"]["to"] == SEG1_END
    assert all(n["flow_complete"] is True for n in doc["not_performed"])
    proj2 = _segment_project(tmp_path / "b")
    with pytest.raises(C.Refusal) as exc:
        LI.import_run(proj2, proj2 / SEG1, "OpenROAD.DetailedRouting")
    assert exc.value.code == "LL_IMPORT_SEGMENT_END_MISMATCH"
    _nothing_written(proj2)


def test_two_segments_import_as_one_tree(tmp_path):
    import librelane_import as LI
    proj = _segment_project(tmp_path)
    rc = LI.main([str(proj), f"{proj / SEG1}={SEG1_END}", str(proj / SEG2)])
    assert rc == 0
    doc = json.loads((proj / "phase3/librelane/import_manifest.json").read_text())
    assert [s["run_dir"] for s in doc["segments"]] == [SEG1, SEG2]
    by = {r["canonical_path"]: r for r in doc["rows"]}
    assert by["phase2/stage2/synth/netlist.v"]["run_dir"] == SEG1
    assert by["phase3/stage3/pnr/routed.def"]["run_dir"] == SEG2
    for r in doc["rows"]:
        assert (proj / r["run_dir"] / r["tool_run_path"]).is_file(), r
    # not_performed over the union: only the step neither segment ran
    assert [n["tool_step"] for n in doc["not_performed"]] == ["OpenROAD.PadRing"]


def test_a_step_two_segments_both_ran_is_refused(tmp_path):
    import librelane_contract as C
    import librelane_import as LI
    proj = _segment_project(tmp_path)
    shutil.copyfile(FIXTURES / "8HD-4/runs/cmp3/flow.log", proj / SEG2 / "flow.log")
    with pytest.raises(C.Refusal) as exc:
        LI.import_segments(proj, [(proj / SEG1, SEG1_END), (proj / SEG2, None)])
    assert exc.value.code == "LL_IMPORT_SEGMENT_OVERLAP"
    _nothing_written(proj)


# ── which run of a class (review W6: nested runs, top-level reruns) ───────

def _add_steps(proj: Path, before: str, steps):
    """Insert started steps into flow.log before the step ``before``; each is
    (instance, folder, class, files) with its own config and state."""
    run = proj / RUN_REL
    lines = _flow_lines(proj)
    i = _at(lines, before)
    new = []
    for instance, folder, cls, state in steps:
        d = run / folder
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({"meta": {"step": cls}}))
        new.append(f"Running '{instance}' at 'runs/cmp3/{folder}'…\n")
        if state is not None:
            for name, text in state.items():
                (d / name).write_text(text)
            rec = "/host/cmp3/librelane_8HD-4/design/runs/cmp3/" + folder
            (d / "state_out.json").write_text(json.dumps(
                {"def": f"{rec}/spm.def", "odb": f"{rec}/spm.odb"}))
    (run / "flow.log").write_text("".join(lines[:i] + new + lines[i:]))


def test_a_run_nested_in_a_later_composite_is_not_the_stage(tmp_path):
    """Odb.DiodesOnPorts re-runs DetailedPlacement after the route. The
    placement stage stays the top-level DetailedPlacement."""
    proj = _project(tmp_path)
    _add_steps(proj, "'OpenROAD.CheckAntennas-1'", [
        ("Odb.DiodesOnPorts", "47-odb-diodesonports", "Odb.DiodesOnPorts", None),
        ("OpenROAD.DetailedPlacement-1",
         "47-odb-diodesonports/2-openroad-detailedplacement",
         "OpenROAD.DetailedPlacement",
         {"spm.def": "VERSION 5.8 ;\nDESIGN nested ;\n", "spm.odb": "nested"})])
    _import(proj)
    top = proj / RUN_REL / "34-openroad-detailedplacement/spm.def"
    assert _sha(proj / "phase3/stage3/pnr/placed.def") == _sha(top)


def test_two_top_level_runs_of_a_stage_are_refused_not_picked(tmp_path):
    import librelane_contract as C
    proj = _project(tmp_path)
    _add_steps(proj, "'OpenROAD.CheckAntennas-1'", [
        ("OpenROAD.DetailedPlacement-1", "47-openroad-detailedplacement-1",
         "OpenROAD.DetailedPlacement",
         {"spm.def": "VERSION 5.8 ;\nDESIGN again ;\n", "spm.odb": "again"})])
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_AMBIGUOUS_STEP"
    assert "34-openroad-detailedplacement" in str(exc.value)
    _nothing_written(proj)


# ── all or nothing, and no stale file (review W6 MINORs) ──────────────────

def test_a_refusal_after_earlier_rules_writes_nothing(tmp_path):
    """Rule 21 refuses (its view is another step's); rules 9-20, which come
    first, used to have overwritten netlist.v and four DEFs already."""
    import librelane_contract as C
    proj = _project(tmp_path)
    st = proj / RUN_REL / "44-openroad-detailedrouting/state_out.json"
    doc = json.loads(st.read_text())
    doc["def"] = doc["def"].replace("44-openroad-detailedrouting",
                                    "34-openroad-detailedplacement")
    st.write_text(json.dumps(doc))
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_VIEW_NOT_OWN"
    _nothing_written(proj)


def test_a_failure_while_writing_restores_every_path(tmp_path, monkeypatch):
    import librelane_contract as C
    import librelane_import as LI
    proj = _project(tmp_path)
    placed = proj / "phase3/stage3/pnr/placed.def"
    placed.parent.mkdir(parents=True)
    placed.write_text("the previous placement\n")
    (proj / "provenance.jsonl").write_text('{"earlier": 1}\n')
    real = LI._copy

    def failing(src, dst):
        if dst.name == "clock_tree.rpt":
            raise C.Refusal("LL_IMPORT_COPY_MISMATCH", "disk full")
        return real(src, dst)
    monkeypatch.setattr(LI, "_copy", failing)
    with pytest.raises(C.Refusal):
        _import(proj)
    assert placed.read_text() == "the previous placement\n"
    assert (proj / "provenance.jsonl").read_text() == '{"earlier": 1}\n'
    _nothing_written(proj, ["phase3/stage3/pnr/placed.def", "provenance.jsonl"])


def test_an_import_over_another_runs_import_is_refused(tmp_path):
    import librelane_contract as C
    import librelane_import as LI
    proj = _project(tmp_path)
    _import(proj)
    before = {p: _sha(proj / p) for p in _canonical(proj)}
    other = proj / "phase3/librelane/runs_b/cmp3"
    shutil.copytree(FIXTURES / "8HD-9" / "runs" / "cmp3", other)
    with pytest.raises(C.Refusal) as exc:
        LI.import_run(proj, other)
    assert exc.value.code == "LL_IMPORT_OTHER_RUN_PRESENT"
    assert {p: _sha(proj / p) for p in _canonical(proj)} == before


def test_a_reimport_removes_what_the_same_runs_no_longer_perform(tmp_path):
    """A canonical file the earlier import of these runs wrote, for a rule
    this import does not perform, is removed and the removal recorded."""
    proj = _project(tmp_path)
    _import(proj)
    man = proj / "phase3/librelane/import_manifest.json"
    doc = json.loads(man.read_text())
    stale = proj / "phase3/stage3/pnr/padring.def"
    stale.write_text("an earlier import's pad ring\n")
    doc["rows"].append({"canonical_path": "phase3/stage3/pnr/padring.def"})
    man.write_text(json.dumps(doc))
    doc = _import(proj)
    assert not stale.exists()
    assert doc["removed"] == ["phase3/stage3/pnr/padring.def"]


def test_a_canonical_file_for_a_step_not_performed_is_refused(tmp_path):
    import librelane_contract as C
    proj = _project(tmp_path)
    stale = proj / "phase3/stage3/pnr/padring.def"
    stale.parent.mkdir(parents=True)
    stale.write_text("from somewhere else\n")
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_STALE_CANONICAL"
    _nothing_written(proj, ["phase3/stage3/pnr/padring.def"])


def test_two_files_for_one_destination_are_refused(tmp_path):
    """Files('*.drc') maps every match to routed_router.drc.rpt."""
    import librelane_contract as C
    proj = _project(tmp_path)
    (proj / RUN_REL / "44-openroad-detailedrouting/zz_extra.drc").write_text("x\n")
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_AMBIGUOUS_SOURCE"
    assert "routed_router.drc.rpt" in str(exc.value)
    _nothing_written(proj)


# ── the nominal corner comes from the run (review W6 MINOR) ───────────────

def _distinct_corners(proj: Path):
    """The trimmed SPEFs share their first 4096 bytes; mark each corner so a
    wrong choice shows."""
    rcx = proj / RUN_REL / "54-openroad-rcx"
    out = {}
    for key, value in json.loads((rcx / "state_out.json").read_text())["spef"].items():
        path = proj / RUN_REL / value.split("/runs/cmp3/", 1)[1]
        path.write_text(path.read_text() + f"* corner {key}\n")
        out[key] = _sha(path)
    assert len(set(out.values())) == len(out) == 3
    return out


@pytest.mark.parametrize("default,key", [("nom_tt_025C_5v00", "nom_*"),
                                         ("max_ff_n40C_5v50", "max_*"),
                                         ("min_ss_125C_4v50", "min_*")])
def test_the_nominal_spef_is_the_corner_the_rcx_step_calls_default(
        tmp_path, default, key):
    proj = _project(tmp_path)
    corners = _distinct_corners(proj)
    cfg = proj / RUN_REL / "54-openroad-rcx/config.json"
    doc = json.loads(cfg.read_text())
    doc["DEFAULT_CORNER"] = default
    cfg.write_text(json.dumps(doc))
    _import(proj)
    assert _sha(proj / "phase3/stage3/extracted/spm.spef") == corners[key]


def test_a_run_whose_rcx_names_no_default_corner_is_refused(tmp_path):
    import librelane_contract as C
    proj2 = _project(tmp_path)
    cfg = proj2 / RUN_REL / "54-openroad-rcx/config.json"
    doc = json.loads(cfg.read_text())
    del doc["DEFAULT_CORNER"]
    cfg.write_text(json.dumps(doc))
    with pytest.raises(C.Refusal) as exc:
        _import(proj2)
    assert exc.value.code == "LL_IMPORT_NO_NOMINAL_CORNER"
    _nothing_written(proj2)

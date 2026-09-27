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


# ── the import itself ─────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["8HD-4", "8HD-9"])
def test_every_imported_file_is_a_copy_bound_on_both_sides(tmp_path, host):
    proj = _project(tmp_path, host)
    doc = _import(proj)
    assert doc["top"] == "spm" and len(doc["rows"]) > 100
    for row in doc["rows"]:
        dest = proj / row["canonical_path"]
        src = proj / row["tool_run_path"]
        assert dest.is_file() and not dest.is_symlink(), row
        assert src.is_file() and not src.is_symlink(), row
        assert row["tool_run_sha256"] == row["canonical_sha256"] \
            == "sha256:" + _sha(dest) == "sha256:" + _sha(src), row
        assert row["provenance"] == "witnessed", row
        assert row["flow"] == "librelane" and row["exit_code"] == 0, row
        if row["source_log"] is not None:          # the log that wrote it
            log = proj / row["source_log"]
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
    assert rcx["source_log"].endswith("/54-openroad-rcx/nom/rcx.log")
    assert by["phase3/stage3/pnr/routed.def"]["source_log"].endswith(
        "/44-openroad-detailedrouting/openroad-detailedrouting.log")
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
        "reason": "the run's own flow.log never started OpenROAD.PadRing"}]
    assert not (proj / "phase3/stage3/pnr/padring.def").exists()


def test_the_last_completed_run_of_a_class_is_imported(tmp_path):
    """CheckAntennas runs nested inside RepairAntennas and again after the
    detailed route; the import takes the later one."""
    proj = _project(tmp_path)
    doc = _import(proj)
    ant = [r for r in doc["rows"] if r["step_id"] == "26"]
    assert ant and {r["tool_step_id"] for r in ant} == {"OpenROAD.CheckAntennas-1"}
    assert all("/46-openroad-checkantennas-1/" in r["tool_run_path"] for r in ant)


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
    assert routed and "/99-renamed/" in routed[0]["tool_run_path"]


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


def test_a_step_the_run_never_finished_is_refused(tmp_path):
    import librelane_contract as C
    proj = _project(tmp_path)
    flow = proj / RUN_REL / "flow.log"
    lines = flow.read_text().splitlines(keepends=True)
    cut = next(i for i, l in enumerate(lines)
               if "'OpenROAD.DetailedRouting'" in l and l.startswith("Running"))
    flow.write_text("".join(lines[:cut + 1]))
    with pytest.raises(C.Refusal) as exc:
        _import(proj)
    assert exc.value.code == "LL_IMPORT_STEP_NOT_COMPLETED"


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

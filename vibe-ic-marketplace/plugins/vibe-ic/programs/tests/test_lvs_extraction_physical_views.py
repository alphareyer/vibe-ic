"""Step 31 extracts the layout with the physical views place-and-route used.

MEASURED on a pad-ring die (gf180mcuD, IC path, 2026-09-29): a step-31 LVS in
a process that had not run place-and-route (a bounded `--entry-step 31`
window starts from `_detect_pdk`) read no IO LEF and the distribution tech LEF
instead of the via-legalized one.  Magic then loaded every pad master from the
PDK's `mag/` library as a full transistor-level layout, both power-aware
compares mismatched and the plain compare crashed netgen (rc 139).  The same
DEF matched uniquely inside the run that routed it.

Pinned here:
* the bounded window restores the tech LEF, the LEFs PnR read and the pad
  library GDS views from the run's own records before anything reads the PDK
  (the GDS admission basis hashes it; step 31 extracts with it);
* a record that does not verify (tech LEF sha) restores nothing;
* an extraction that read a placed master from a library path stops before
  netgen with NOT_MEASURED naming the master; a clean extraction reaches it.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

mod = importlib.import_module("phase3_one_shot_runner")

_MOVED = "/elsewhere/run/proj"          # where the records were written
_IO_LEF = "/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__fill1.lef"
_IO_GDS = "/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_io/gds/gf180mcu_fd_io.gds"


def _pdk():
    """A PDK object as `_detect_pdk` returns it: distribution views only."""
    return mod.PdkConfig(name="calpdk", liberty="/pdk/lib/cells.lib",
                         tech_lef="/pdk/techlef/cells__nom.tlef",
                         cell_lef="/pdk/lef/cells.lef", cell_gds=None,
                         site="core", drc_deck=None)


def _recorded_run(tmp_path: Path, *, sha_ok: bool = True):
    project = tmp_path / "proj"
    pnr = mod._pl.pnr_dir(project)
    pnr.mkdir(parents=True)
    staged = pnr / "active_via_legalized.tlef"
    staged.write_text("VERSION 5.8 ;\nEND LIBRARY\n")
    sha = hashlib.sha256(staged.read_bytes()).hexdigest()
    pdk = _pdk()
    reports = project / "reports"
    (reports / "phase3").mkdir(parents=True)
    moved_tlef = f"{_MOVED}/phase3/stage3/pnr/active_via_legalized.tlef"
    (reports / "pdk_via_patch_legalization.json").write_text(json.dumps({
        "status": "APPLIED", "source_tech_lef": pdk.tech_lef,
        "derived_tech_lef": moved_tlef,
        "derived_sha256": sha if sha_ok else "0" * 64}))
    (reports / "phase3/physical_view_inventory.json").write_text(json.dumps({
        "schema": "vibe-ic/physical-view-inventory/1",
        "read_lef_paths": [moved_tlef, pdk.cell_lef, _IO_LEF]}))
    (reports / "phase3/io_pad_chip_top.json").write_text(json.dumps({
        "verdict": "WROTE", "chip_top_verilog": "phase3/chip_top_io.v"}))
    return project, pdk


def _window(tmp_path, monkeypatch, *, sha_ok=True):
    """A real `--entry-step 31 --exit-step 31` window over the recorded run.
    Faked: the pad-library resolver's container query, the PDK hasher's
    container query and the site's EDA dispatch -- each captures the PDK
    object it is handed."""
    project, pdk = _recorded_run(tmp_path, sha_ok=sha_ok)
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "lvsfix31")
    monkeypatch.setattr(mod, "_discover_padring_io_views",
                        lambda pdk, container: ([_IO_LEF], [_IO_GDS]))
    seen = {}

    def snapshot(view):
        return {"tech_lef": view.tech_lef, "macro_lefs": list(view.macro_lefs),
                "macro_gds": list(view.macro_gds)}

    def basis(project_, top, view, container):
        seen.setdefault("basis", snapshot(view))
        return "", "stop at admission (captured)"

    def dispatch(project_, top, view, args, site, gate, run_id):
        seen[site] = snapshot(view)
        return mod.StepResult(site, "NOT_MEASURED", 0.0, "captured",
                              reason_class="not_executed")

    monkeypatch.setattr(mod, "_layout_basis", basis)
    monkeypatch.setattr(mod, "_direct_flow_window", dispatch)
    args = SimpleNamespace(entry_step="31", exit_step="31", container="c")
    mod._run_phase3_window(project, "spm", pdk, args,
                           mod._phase3_window_sites("31", "31"))
    seen["project"] = project
    return seen


def test_a_step31_window_reads_the_views_pnr_used(tmp_path, monkeypatch):
    seen = _window(tmp_path, monkeypatch)
    view = seen["basis"]              # the FIRST reader: the admission basis
    assert view["tech_lef"] == str(
        seen["project"] / "phase3/stage3/pnr/active_via_legalized.tlef")
    assert _IO_LEF in view["macro_lefs"]
    assert not any(p.endswith("active_via_legalized.tlef") for p in view["macro_lefs"])
    assert _IO_GDS in view["macro_gds"]


def test_a_tech_lef_record_that_does_not_verify_is_not_restored(tmp_path, monkeypatch):
    view = _window(tmp_path, monkeypatch, sha_ok=False)["basis"]
    assert not str(view["tech_lef"]).endswith("active_via_legalized.tlef")
    assert _IO_LEF in view["macro_lefs"]          # the LEF record stands on its own


def test_a_window_that_routes_again_does_not_restore(tmp_path, monkeypatch):
    """PnR re-derives the views itself; nothing is pre-loaded for it."""
    project, pdk = _recorded_run(tmp_path)
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", "lvsfix15")
    seen = {}

    def dispatch(project_, top, view, args, site, gate, run_id):
        seen[site] = list(view.macro_lefs)
        return mod.StepResult(site, "NOT_MEASURED", 0.0, "captured",
                              reason_class="not_executed")

    monkeypatch.setattr(mod, "_direct_flow_window", dispatch)
    args = SimpleNamespace(entry_step="15", exit_step="22", container="c")
    mod._run_phase3_window(project, "spm", pdk, args, ["pnr"])
    assert seen["pnr"] == []


# --- the extraction guard ----------------------------------------------------

_DEF = ("VERSION 5.8 ;\nDESIGN top ;\nCOMPONENTS 2 ;\n"
        "    - u_inv gf180mcu_fd_sc_mcu7t5v0__inv_1 + PLACED ( 0 0 ) N ;\n"
        "    - u_fill gf180mcu_fd_io__fill1 + FIXED ( 0 0 ) N ;\n"
        "END COMPONENTS\nPINS 1 ;\n- a + NET a ;\nEND PINS\n"
        "NETS 1 ;\n- a + ROUTED met1 ;\nEND NETS\nEND DESIGN\n")
#: Magic's own line, as the calibration log `cal_views_magic_positive.log`
#: records it for the same master.
_LIBRARY_READ = ("Cell gf180mcu_fd_io__fill1 read from path "
                 "/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_io/mag\n")


def _extract(tmp_path, monkeypatch, magic_says: str):
    project = tmp_path / "proj"
    pdk = _pdk()
    ext_dir = mod._pl.extracted_dir(project)
    ext_dir.mkdir(parents=True)
    spice_out = ext_dir / "top_extracted.sp"
    pnr = mod._pl.pnr_dir(project)
    pnr.mkdir(parents=True)
    def_file = pnr / "top.def"
    def_file.write_text(_DEF)
    netlist = pnr / "top_pnr.v"
    netlist.write_text("module top(input a); endmodule\n")
    calls = []

    def fake_docker_exec(container, cmd, timeout=None, **_):
        if "magic -dnull" in cmd:
            calls.append("magic")
            spice_out.write_text(".subckt top a\n.ends\n")
            (ext_dir / mod._mio.FEEDBACK_NAMES[0]).write_text("")
            return (0, magic_says + "0 errors\nMAGIC_EXT2SPICE_DONE", "")
        if "netgen -batch lvs" in cmd:
            calls.append("netgen")
            rpt = project / "reports/phase3/lvs.rpt"
            rpt.parent.mkdir(parents=True, exist_ok=True)
            rpt.write_text("Final result: Circuits match uniquely.\n")
            return (0, "", "")
        return (0, "", "")

    monkeypatch.setattr(mod, "_docker_exec", fake_docker_exec)
    res = mod._run_extraction_lvs(project, "top", pdk, "c", def_file, netlist,
                                  "/nonexistent.magicrc", "/setup.tcl", 0.0)
    return res, calls, project


def test_an_extraction_of_library_layouts_never_reaches_netgen(tmp_path, monkeypatch):
    res, calls, project = _extract(tmp_path, monkeypatch, _LIBRARY_READ)
    assert "netgen" not in calls
    assert res.status == "NOT_MEASURED", res.detail
    assert res.extras["finding"] == "LVS_EXTRACTION_VIEW_NOT_ABSTRACT"
    assert set(res.extras["library_layout_masters"]) == {"gf180mcu_fd_io__fill1"}
    verdict = json.loads((project / "reports/phase3/lvs_verdict.json").read_text())
    assert verdict["finding"] == "LVS_EXTRACTION_VIEW_NOT_ABSTRACT"
    assert verdict["status"] != "PASS"


def test_an_abstract_extraction_reaches_netgen(tmp_path, monkeypatch):
    _res, calls, _ = _extract(tmp_path, monkeypatch, "")
    assert calls[-1] == "netgen"

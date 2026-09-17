"""A pad ring in a FLAT sign-off GDS is proven by geometry, not assumed lost.

MEASURED on spm x gf180mcuD, DIE route (v1.22.3, image sha256:89a8fd72...):
`_klayout_merge_layers` (#601) flattens every KLayout stream-out before sign-off
DRC, so the shipped GDS references none of the final DEF's 46 masters and
`pad_ring_route_evidence` answered PADRING_GDS_HIERARCHY_ABSENT -> FAIL on every
KLayout-streamed die, while its own DEF chain matched 771/771 ring cells.

The merge now keeps the ring's as-streamed placement in a small side layout
before it flattens, and the gate checks, layer by layer, that every placed ring
instance is covered by the final GDS. Directions measured here:
  * merged flat GDS of an intact ring        -> PASS
  * one polygon of one pad cut away          -> FAIL, that instance named
  * a layer of the pad absent from the final -> FAIL
  * a pad moved in the final                 -> FAIL
  * reference absent / no container          -> finding stays (NOT PROVEN)
  * reference placing fewer than expected    -> FAIL without running klayout
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as p3  # noqa: E402

pya = pytest.importorskip("pya")

TOP = "chip_top"
PAD = "IO_PAD_A"
CORE = "STD_CELL_A"
L1 = pya.LayerInfo(34, 0)
L2 = pya.LayerInfo(46, 0)


def _hier_layout() -> "pya.Layout":
    """chip_top with two PAD instances (one rotated) abutting a core cell."""
    ly = pya.Layout()
    ly.dbu = 0.001
    l1, l2 = ly.layer(L1), ly.layer(L2)
    pad = ly.create_cell(PAD)
    pad.shapes(l1).insert(pya.Box(0, 0, 10000, 10000))
    pad.shapes(l1).insert(pya.Box(10000, 0, 12000, 3000))
    pad.shapes(l2).insert(pya.Box(1000, 1000, 9000, 9000))
    core = ly.create_cell(CORE)
    core.shapes(l1).insert(pya.Box(0, 0, 3000, 3000))
    top = ly.create_cell(TOP)
    top.insert(pya.CellInstArray(pad.cell_index(), pya.Trans(0, False, 0, 0)))
    top.insert(pya.CellInstArray(pad.cell_index(),
                                 pya.Trans(1, False, 50000, 0)))
    top.insert(pya.CellInstArray(core.cell_index(),
                                 pya.Trans(0, False, 12000, 0)))
    top.shapes(l1).insert(pya.Box(15000, 0, 40000, 1000))    # a route
    return ly


def _run_script(text: str, env: dict, tmp: Path) -> subprocess.CompletedProcess:
    script = tmp / f"s{len(list(tmp.glob('s*.py')))}.py"
    script.write_text(text)
    return subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, env={**os.environ, **env})


def _merge(tmp: Path, *, masters: str = PAD):
    src, out, ref = tmp / "in.gds", tmp / "merged.gds", tmp / "ref.gds"
    _hier_layout().write(str(src))
    env = {"GDS_IN": str(src), "GDS_OUT": str(out)}
    if masters:
        env.update({"RING_MASTERS": masters, "RING_REF_OUT": str(ref)})
    r = _run_script(p3._GDS_LAYER_MERGE_PY, env, tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return out, ref, r.stdout


def _proof(tmp: Path, ref: Path, final: Path) -> dict:
    out = tmp / "proof.json"
    r = _run_script(p3._PADRING_GEOMETRY_PROOF_PY,
                    {"REF_GDS": str(ref), "FINAL_GDS": str(final),
                     "TOP": TOP, "JSON_OUT": str(out)}, tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(out.read_text())


def _edit_final(final: Path, fn) -> Path:
    ly = pya.Layout()
    ly.read(str(final))
    fn(ly, ly.cell(TOP))
    edited = final.with_name("edited.gds")
    ly.write(str(edited))
    return edited


# -- the merge keeps the placement it is about to destroy -------------------

def test_merge_writes_the_ring_reference_before_flattening(tmp_path):
    merged, ref, stdout = _merge(tmp_path)
    assert "RING_REFERENCE_WRITTEN instances=2" in stdout
    final = pya.Layout()
    final.read(str(merged))
    assert final.cell(TOP).child_instances() == 0          # really flat
    assert p3._gds_reference_counts(ref, TOP) == {PAD: 2}  # core not copied


def test_merge_without_ring_masters_writes_no_reference(tmp_path):
    merged, ref, stdout = _merge(tmp_path, masters="")
    assert merged.is_file() and not ref.exists()
    assert "RING_REFERENCE" not in stdout


# -- the geometry proof, both directions ------------------------------------

def test_an_intact_ring_in_the_flat_gds_is_covered(tmp_path):
    merged, ref, _ = _merge(tmp_path)
    rep = _proof(tmp_path, ref, merged)
    assert rep["verdict"] == "PASS", rep
    assert rep["masters"] == {PAD: {"instances": 2, "covered": 2}}


def test_a_polygon_cut_from_one_pad_is_not_covered(tmp_path):
    merged, ref, _ = _merge(tmp_path)

    def cut(ly, top):
        li = ly.layer(L2)
        reg = pya.Region(top.shapes(li)) - pya.Region(
            pya.Box(42000, 2000, 43000, 3000))  # inside the R90 pad only
        top.shapes(li).clear()
        top.shapes(li).insert(reg)
    rep = _proof(tmp_path, ref, _edit_final(merged, cut))
    assert rep["verdict"] == "FAIL", rep
    assert rep["masters"][PAD] == {"instances": 2, "covered": 1}
    assert rep["uncovered"][0]["origin_dbu"] == [50000, 0]


def test_a_layer_missing_from_the_final_is_not_covered(tmp_path):
    merged, ref, _ = _merge(tmp_path)

    def drop(ly, top):
        top.shapes(ly.layer(L2)).clear()
    rep = _proof(tmp_path, ref, _edit_final(merged, drop))
    assert rep["verdict"] == "FAIL", rep
    assert rep["masters"][PAD]["covered"] == 0


def test_a_moved_pad_is_not_covered(tmp_path):
    merged, ref, _ = _merge(tmp_path)

    def move(ly, top):
        for li in ly.layer_indexes():
            reg = pya.Region(top.shapes(li)).moved(0, 20000)
            top.shapes(li).clear()
            top.shapes(li).insert(reg)
    rep = _proof(tmp_path, ref, _edit_final(merged, move))
    assert rep["verdict"] == "FAIL", rep


def test_a_final_without_the_top_is_not_measured(tmp_path):
    merged, ref, _ = _merge(tmp_path)

    def rename(ly, top):
        top.name = "other_top"
    rep = _proof(tmp_path, ref, _edit_final(merged, rename))
    assert rep["verdict"] == "NOT_MEASURED", rep


# -- the gate ---------------------------------------------------------------

def _project(tmp_path: Path, *, with_reference: bool = True,
             cut: bool = False) -> Path:
    project = tmp_path / "proj"
    pnr = p3._pl.pnr_dir(project)
    pnr.mkdir(parents=True)
    defs = ("VERSION 5.8 ;\nDESIGN chip_top ;\nUNITS DISTANCE MICRONS 1000 ;\n"
            "DIEAREA ( 0 0 ) ( 100000 100000 ) ;\nCOMPONENTS 3 ;\n"
            f"- u_pad0 {PAD} + FIXED ( 0 0 ) N ;\n"
            f"- u_pad1 {PAD} + FIXED ( 50000 0 ) E ;\n"
            f"- u_core {CORE} + PLACED ( 12000 0 ) N ;\n"
            "END COMPONENTS\nEND DESIGN\n")
    for name in ("padring.def", "routed.def", "spm.def"):
        (pnr / name).write_text(defs)
    (pnr / "pnr.tcl").write_text("puts {PADRING_ROUTING_CONSUMED: f}\n"
                                 "global_placement\ndetailed_route\n")
    (pnr / "openroad.log").write_text("PADRING_ROUTING_CONSUMED: f\n")
    reports = project / "reports" / "phase3"
    reports.mkdir(parents=True)
    (reports / "padring.json").write_text(json.dumps({"producer": {
        "pads": [{"instance": "u_pad0", "master": PAD},
                 {"instance": "u_pad1", "master": PAD}],
        "corners": [], "fillers": []}}))
    assert p3._padring_record_masters(project) == {PAD}
    work = tmp_path / "work"
    work.mkdir()
    merged, ref, _ = _merge(work)
    final = merged
    if cut:
        final = _edit_final(merged, lambda ly, top: top.shapes(
            ly.layer(L2)).clear())
    (pnr / "spm.gds").write_bytes(final.read_bytes())
    if with_reference:
        p3._padring_reference_path(pnr / "spm.gds").write_bytes(
            ref.read_bytes())
    return project


def _fake_docker(monkeypatch, tmp_path):
    """Run the container command's klayout script locally with the same env."""
    monkeypatch.setattr(p3, "_to_container_path", lambda p, c: p)

    def fake(container, cmd, timeout=1800, **kw):
        exports = cmd.split("&& klayout", 1)[0]
        env = dict(tok.split("=", 1) for part in exports.split("&&")
                   for tok in part.replace("export", " ").split() if "=" in tok)
        script = cmd.rsplit(" ", 1)[1]
        r = _run_script(Path(script).read_text(), env, tmp_path)
        return r.returncode, r.stdout, r.stderr
    monkeypatch.setattr(p3, "_docker_exec", fake)


def _gate(project: Path, container):
    gds = p3._pl.pnr_dir(project) / "spm.gds"
    res = p3.step_pad_ring_final_evidence(
        project, "spm", p3.StepResult("gds", "PASS", 0.0, "f", [str(gds)],
                                      extras={"streamout_engine": "klayout"}),
        container)
    doc = json.loads((project / "reports" / "phase3"
                      / "pad_ring_route_evidence.json").read_text())
    return res, doc


def test_the_gate_passes_a_flat_gds_whose_ring_is_covered(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, tmp_path)
    res, doc = _gate(_project(tmp_path), "c")
    assert res.status == "PASS", doc["findings"]
    assert doc["gds_hierarchy"] == "flat"
    assert doc["flat_ring_geometry_proof"]["verdict"] == "PASS"
    assert doc["flat_ring_geometry_proof"]["covered_by_master"] == {PAD: 2}


def test_the_gate_fails_a_flat_gds_whose_ring_lost_geometry(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, tmp_path)
    res, doc = _gate(_project(tmp_path, cut=True), "c")
    assert res.status == "FAIL"
    assert "PADRING_GDS_HIERARCHY_ABSENT" in " ".join(doc["findings"])
    assert doc["flat_ring_geometry_proof"]["verdict"] == "FAIL"


def test_no_reference_or_no_container_leaves_the_ring_not_proven(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, tmp_path)
    res, doc = _gate(_project(tmp_path, with_reference=False), "c")
    assert res.status == "FAIL"
    assert doc["flat_ring_geometry_proof"]["verdict"] == "NOT_MEASURED"
    res, doc = _gate(_project(tmp_path / "b"), None)
    assert res.status == "FAIL"
    assert doc["flat_ring_geometry_proof"]["verdict"] == "NOT_MEASURED"


def test_a_reference_short_of_the_expected_ring_fails_before_klayout(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("klayout must not run")
    monkeypatch.setattr(p3, "_docker_exec", boom)
    project = _project(tmp_path)
    doc = json.loads((project / "reports/phase3/padring.json").read_text())
    doc["producer"]["pads"].append({"instance": "u_pad2", "master": PAD})
    (project / "reports/phase3/padring.json").write_text(json.dumps(doc))
    for name in ("padring.def", "routed.def", "spm.def"):
        path = p3._pl.pnr_dir(project) / name
        path.write_text(path.read_text().replace(
            "END COMPONENTS", f"- u_pad2 {PAD} + FIXED ( 0 90000 ) N ;\nEND COMPONENTS"))
    res, doc = _gate(project, "c")
    assert res.status == "FAIL"
    proof = doc["flat_ring_geometry_proof"]
    assert proof["verdict"] == "FAIL" and "did not place the ring" in proof["reason"]


def test_merge_and_gate_agree_on_the_reference_path_whatever_the_top(tmp_path, monkeypatch):
    """The merge is handed the physical top (`chip_top`), the gate the file.
    MEASURED: `chip_top.ring_reference.gds` written, `spm.ring_reference.gds`
    sought, proof NOT_MEASURED. Both now spell it from the GDS file."""
    project = tmp_path / "proj"
    pnr = p3._pl.pnr_dir(project)
    pnr.mkdir(parents=True)
    reports = project / "reports" / "phase3"
    reports.mkdir(parents=True)
    (reports / "padring.json").write_text(json.dumps({"producer": {
        "pads": [{"instance": "u_pad0", "master": PAD}]}}))
    gds = pnr / "spm.gds"
    gds.write_bytes(b"x")
    seen = {}
    monkeypatch.setattr(p3, "_tool_in_path", lambda c, t: True)
    monkeypatch.setattr(p3, "_to_container_path", lambda p, c: p)

    def fake(container, cmd, timeout=1800, **kw):
        seen["cmd"] = cmd
        return 1, "", "not run"
    monkeypatch.setattr(p3, "_docker_exec", fake)
    p3._klayout_merge_layers(project, "chip_top", None, "c", gds)
    want = str(p3._padring_reference_path(gds))
    assert want.endswith("spm.ring_reference.gds")
    assert f"RING_REF_OUT={want} " in seen["cmd"]

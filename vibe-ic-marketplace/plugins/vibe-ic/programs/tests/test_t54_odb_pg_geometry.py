"""Via metal enclosing a PSM edge supplies its real EM cross section."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import em_current_density_check as E  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402
from test_phase3_signoff_chain_organic import _mk_project, _fake_pdk  # noqa: E402


def test_via_metal_geometry_changes_width_basis_but_keeps_true_failure(tmp_path):
    csv = tmp_path / "em_segments.csv"
    csv.write_text(
        "Node0 Layer,Node0 X location,Node0 Y location,Node1 Layer,"
        "Node1 X location,Node1 Y location,Current\n"
        "M3,10,5,M3,10,8,0.002589\n")
    jmax = tmp_path / "jmax.json"
    jmax.write_text(json.dumps({"layers": {"M3": {
        "kind": "routing", "width_um": 0.28,
        "jmax_mA_per_um": 0.67}}}))
    geometry = tmp_path / "em_pg_geometry.tsv"
    geometry.write_text(
        "net\tlayer\tx0_um\ty0_um\tx1_um\ty1_um\tsource\n"
        "SUP\tM3\t9.29\t4.5\t10.71\t8.5\tvia_metal\n")
    verdict, report = E.evaluate(
        csv, jmax, None, 0.1, 2.0, "SUP", 20,
        pg_geometry_path=geometry)
    assert verdict == "FAIL"
    assert report["summary"]["odb_pg_geometry_width_uses"] == 1
    assert report["offenders"][0]["width_source"] == "odb_pg_metal_geometry"
    assert report["offenders"][0]["width_um"] == pytest.approx(1.42)
    assert report["offenders"][0]["utilization"] > 1


def test_psm_producer_writes_odb_via_geometry_for_the_measured_def(
        tmp_path, monkeypatch):
    project = _mk_project(tmp_path)
    rpt = R._pl.reports_phase3_dir(project)
    rpt.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: p)

    def fake_eda(_container, _cmd, **_kwargs):
        (rpt / "em_pg_geometry.tsv").write_text(
            "net\tlayer\tx0_um\ty0_um\tx1_um\ty1_um\tsource\n"
            "SUP\tM3\t9\t4\t11\t9\tvia_metal\n")
        (rpt / "em_segments.csv").write_text(
            "Node0 Layer,Node0 X location,Node0 Y location,"
            "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
            "M3,10,5,M3,10,8,0.002\n")
        return 0, "Maximum current : 2.0e-03 A\n", ""

    monkeypatch.setattr(R, "_docker_exec", fake_eda)
    R._emit_ir_em_reports(project, "chip_top", _fake_pdk(), "image",
                          rpt / "ir_drop.rpt", rpt / "em.rpt", [])
    tcl = (rpt / "ir_em_chip_top.tcl").read_text()
    assert "getViaXY" in tcl and "getBlockVia" in tcl
    assert "getBoxes" in tcl and "via_metal" in tcl
    manifest = json.loads((rpt / "em_pg_geometry_subject.json").read_text())
    import hashlib
    measured_def = R._pl.pnr_dir(project) / "chip_top.def"
    assert manifest["def_sha256"] == hashlib.sha256(
        measured_def.read_bytes()).hexdigest()

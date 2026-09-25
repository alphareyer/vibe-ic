"""Via metal enclosing a PSM edge supplies its real EM cross section."""
import hashlib
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
        (rpt / "em_segments_VPWR.csv").write_text(
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


def test_psm_keeps_both_rails_and_audits_tool_density_on_the_same_def(
        tmp_path, monkeypatch):
    project = _mk_project(tmp_path)
    (project / "phase3" / "librelane_switch.json").write_text(
        json.dumps({"steps": {"25": "dual"}}))
    rpt = R._pl.reports_phase3_dir(project)
    rpt.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: p)
    monkeypatch.setattr(R, "_read_pdk_text", lambda *_: (
        "LAYER M3\n TYPE ROUTING ;\n WIDTH 0.4 ;\n"
        " THICKNESS 0.5 ;\n DCCURRENTDENSITY AVERAGE 1.0 ;\nEND M3\n"))

    def fake_eda(_container, _cmd, **_kwargs):
        tcl = (rpt / "ir_em_chip_top.tcl").read_text()
        assert "check_current_density -net VGND" in tcl
        assert "check_current_density -net VPWR" in tcl
        for net in ("VGND", "VPWR"):
            (rpt / f"em_segments_{net}.csv").write_text(
                "Node0 Layer,Node0 X location,Node0 Y location,"
                "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
                f"M3,0,0,M3,1,0,{1 if net == 'VPWR' else 2}e-6\n")
            (rpt / f"em_openroad_density_{net}.csv").write_text(
                "Layer,Ratio,Status\nM3,0.01,OK\n")
        return 0, ("Maximum current : 2e-6 A\n"
                   "Worstcase IR drop: 1e-4 V\nSupply voltage: 1 V\n"), ""

    monkeypatch.setattr(R, "_docker_exec", fake_eda)
    R._emit_ir_em_reports(project, "chip_top", _fake_pdk(), "image",
                          rpt / "ir_drop.rpt", rpt / "em.rpt", [])
    merged = (rpt / "em_segments.csv").read_text()
    assert "Net,Node0 Layer" in merged
    assert "VPWR,M3,0,0,M3,1,0,1e-6" in merged
    assert "VGND,M3,0,0,M3,1,0,2e-6" in merged
    tool = json.loads((rpt / "em_openroad_density.json").read_text())
    assert tool["verdict"] == "MEASURED"
    assert set(tool["nets"]) == {"VPWR", "VGND"}
    assert all(row["psm_segments"] == 1 for row in tool["nets"].values())
    assert tool["scope"] == "routing wires only"
    assert tool["via_cut_status"].startswith("NOT_MEASURED")
    assert tool["def_sha256"] == hashlib.sha256(
        (R._pl.pnr_dir(project) / "chip_top.def").read_bytes()).hexdigest()


def test_em_ab_refuses_empty_tool_pass_and_discloses_utilization_difference(tmp_path):
    rpt = R._pl.reports_phase3_dir(tmp_path)
    rpt.mkdir(parents=True)
    subject = {"subject_def_sha256": "abc"}
    gate = {"jmax_screen": {"verdict": "PASS", "offender_count": 0,
                            "summary": {"worst_utilization": 0.01}}}
    (rpt / "em.json").write_text(json.dumps(subject))
    (rpt / "em_current_authority.json").write_text(json.dumps(gate))
    tool = {"def_sha256": "abc", "verdict": "MEASURED",
            "nets": {"VPWR": {"checked": 0, "no_limit": 1,
                               "violated": 0, "worst_ratio": None}}}
    (rpt / "em_openroad_density.json").write_text(json.dumps(tool))
    E.emit_openroad_ab(tmp_path, [])
    assert json.loads((rpt / "em_openroad_ab.json").read_text())["verdict"] == "NOT_MEASURED"
    tool["nets"]["VPWR"] = {"checked": 1, "no_limit": 0,
                              "violated": 0, "worst_ratio": 0.13}
    (rpt / "em_openroad_density.json").write_text(json.dumps(tool))
    notes = []
    E.emit_openroad_ab(tmp_path, notes)
    ab = json.loads((rpt / "em_openroad_ab.json").read_text())
    assert ab["verdict"] == "MEASURED"
    assert ab["offender_count_agrees"] is True
    assert ab["utilization_agrees"] is False
    assert notes


def test_missing_ground_solution_cannot_be_em_measured(tmp_path, monkeypatch):
    project = _mk_project(tmp_path)
    rpt = R._pl.reports_phase3_dir(project)
    rpt.mkdir(parents=True)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: p)

    def fake_eda(_container, _cmd, **_kwargs):
        (rpt / "em_segments_VPWR.csv").write_text(
            "Node0 Layer,Node0 X location,Node0 Y location,"
            "Node1 Layer,Node1 X location,Node1 Y location,Current\n"
            "M3,0,0,M3,1,0,1e-6\n")
        return 0, "Maximum current : 1e-6 A\nWorstcase IR drop: 1e-4 V\n", ""

    monkeypatch.setattr(R, "_docker_exec", fake_eda)
    _, em_ok = R._emit_ir_em_reports(project, "chip_top", _fake_pdk(), "image",
                                     rpt / "ir_drop.rpt", rpt / "em.rpt", [])
    assert em_ok is False
    doc = json.loads((rpt / "em.json").read_text())
    assert doc["verdict"] == "NOT_MEASURED"
    assert "VGND" in doc["not_measured_reason"]

"""Step 27 reads the voltage and window evidence for the scene it measures."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import si_mcf_sta as emitter
import si_mcf_sta_check as checker
from _hostpaths import repo_path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    sta = project / "phase3/stage3/sta"
    sta.mkdir(parents=True)
    for scene, volts in (("setup", 4.5), ("hold", 5.5)):
        lib = project / f"{scene}.lib"
        lib.write_text(f"library (neutral) {{\n  nom_voltage : {volts} ;\n}}\n")
        (sta / f"sta_spef_{scene}.tcl").write_text(
            f"read_liberty {lib}\nread_verilog design.v\n")
    extracted = project / "phase3/stage3/extracted"
    extracted.mkdir(parents=True)
    shutil.copyfile(repo_path(
        "vibe-ic-marketplace/plugins/vibe-ic/programs/tests/fixtures/si_mcf_zero_coupling/coupled/design.spef"),
        extracted / "design.spef")
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "design_pnr.v").write_text("module design(); endmodule\n")
    (pnr / "constraint.sdc").write_text("create_clock -period 10 clk\n")
    return project


def test_scene_voltage_comes_from_each_active_liberty(tmp_path, monkeypatch):
    project = _project(tmp_path)
    def windows(*args):
        out_json = next(a for a in args if isinstance(a, Path) and
                        a.name.endswith(".json"))
        data = {"pins": {"ua:Z": {"arr_rise_min": 0, "arr_rise_max": 1},
                         "ub:Z": {"arr_rise_min": 0, "arr_rise_max": 1}}}
        out_json.write_text(json.dumps(data))
        return data, 0
    def slack(*_args, **_kwargs):
        return 1.0, 0.5, "worst slack max 1\nworst slack min 0.5\n", 0
    monkeypatch.setattr(emitter, "_run_windows", windows)
    monkeypatch.setattr(emitter, "_run_sta_slack", slack)
    report = emitter.run(project, container="not_used")
    assert report["corners"]["setup"]["vdd_v"] == 4.5
    assert report["corners"]["hold"]["vdd_v"] == 5.5
    assert report["voltage_source"] == "liberty.nom_voltage"


def test_unknown_liberty_voltage_stays_not_measured(tmp_path, monkeypatch):
    project = _project(tmp_path)
    (project / "setup.lib").write_text("library(neutral) { cell(x) {} }\n")
    monkeypatch.setattr(emitter, "_run_windows",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("EDA must not run with unknown voltage")))
    report = emitter.run(project, container="not_used")
    assert report["verdict"] == "NOT_MEASURED"
    assert report["vdd_v"] is None
    assert "setup" in report["error"]


def test_checker_refuses_embedded_path_outside_project(tmp_path):
    project = _project(tmp_path)
    outside = tmp_path / "outside.spef"
    outside.write_text((project / "phase3/stage3/extracted/design.spef").read_text())
    report_path = project / "reports/phase3/si_mcf_sta.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({"spef": str(outside),
                                      "windows_json": str(project / "windows.json"),
                                      "corners": {}}))
    findings, stats = checker.audit(project)
    report = checker.build_report(findings, stats, str(project))
    assert report["verdict"] == "NOT_MEASURED"
    assert report["summary"]["corners_checked"] == []
    assert any(f["category"] == "PATH_OUTSIDE_PROJECT" for f in report["findings"])


def test_checker_missing_windows_is_not_measured(tmp_path):
    project = _project(tmp_path)
    report_path = project / "reports/phase3/si_mcf_sta.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({
        "spef": str(project / "phase3/stage3/extracted/design.spef"),
        "windows_json": str(project / "missing.json"),
        "corners": {}}))
    findings, stats = checker.audit(project)
    report = checker.build_report(findings, stats, str(project))
    assert report["verdict"] == "NOT_MEASURED"
    assert report["summary"]["corners_checked"] == []
    assert any(f["category"] == "NO_WINDOWS" for f in report["findings"])

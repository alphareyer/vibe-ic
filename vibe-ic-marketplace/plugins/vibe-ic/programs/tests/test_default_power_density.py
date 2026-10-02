"""Default Step-24/25 behavioral controls, with an explicitly offline transport.

These neutral grammar fixtures test wiring and refusals. Native proof is a
separate committed-source run; no synthetic log is presented as native evidence.
"""
import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R
import em_current_density_check as E
import em_peak_current_authority_check as A
from _hostpaths import require_repo

TECH = """LAYER wireA
 TYPE ROUTING ;
 WIDTH 1 ;
 THICKNESS 1 ;
 DCCURRENTDENSITY AVERAGE 1 ;
END wireA
LAYER cutA
 TYPE CUT ;
END cutA
"""
HEADER = "Node0 Layer,Node0 X location,Node0 Y location,Node1 Layer,Node1 X location,Node1 Y location,Current\n"
DHEADER = "Layer,Node0 X,Node0 Y,Node1 X,Node1 Y,Current(A),Area(um^2),J(A/um^2),Jlimit(A/um^2),Ratio,Status,Cuts,Basis\n"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(tmp_path, monkeypatch, *, via=False, stress=False, direct=False):
    project = tmp_path / "run"
    pnr = R._pl.pnr_dir(project)
    rpt = R._pl.reports_phase3_dir(project)
    pnr.mkdir(parents=True); rpt.mkdir(parents=True)
    layout = "UNITS DISTANCE MICRONS 1000 ;\nSPECIALNETS 1 ;\n- PWR + USE POWER + ROUTED wireA 1000 ( 0 0 ) ( 2000 0 ) ;\nEND SPECIALNETS\n"
    for name in ("unit.def", "routed.def"):
        (pnr / name).write_text(layout)
    (pnr / "constraint.sdc").write_text("set_load 0.02 [get_ports output]\n")
    tech = tmp_path / "authority.tlef"; tech.write_text(TECH)
    cell = tmp_path / "cells.lef"; cell.write_text("VERSION 5.8 ;\n")
    lib = tmp_path / "cells.lib"; lib.write_text("library(unit) {}\n")
    pdk = SimpleNamespace(name="declared-open-kit", tech_lef=str(tech), cell_lef=str(cell),
                          liberty=str(lib), macro_lefs=[], macro_libs=[], metal_prefix="wire")
    calls = []
    if direct:
        (project / "phase3/librelane_switch.json").write_text(json.dumps({"steps": {"24": "direct"}}))
    def ir_refusal(project, top, pdk, mode, spef, written):
        calls.append("librelane_ir")
        (rpt / R._LL_IR_RECORD).write_text(json.dumps({
            "producer": "librelane:OpenROAD.IRDropReport", "mode": mode,
            "judgment": {"verdict": "NOT_MEASURED", "reasons": ["offline fixture has no native LibreLane state"]}}))
    monkeypatch.setattr(R, "_librelane_step24_record", ir_refusal)
    monkeypatch.setattr(R, "_to_container_path", lambda path, container: path)
    monkeypatch.setattr(R, "_sta_extra_liberties", lambda *args: [])
    monkeypatch.setattr(R._ppa_presweep, "finalize", lambda *args: None)
    current = 0.002 if stress else 0.000001
    def offline_transport(container, cmd, **kwargs):
        tcl = (rpt / "ir_em_unit.tcl").read_text()
        calls.append(tcl)
        (rpt / "em_segments_PWR.csv").write_text(HEADER + f"wireA,0,0,wireA,1,0,{current}\n" +
                                                  ("wireA,0,0,wireB,0,0,0.000001\n" if via else ""))
        (rpt / "em_pg_geometry.tsv").write_text("net\tlayer\tx0_um\ty0_um\tx1_um\ty1_um\tsource\nPWR\twireA\t0\t-0.5\t2\t0.5\tspecial_wire\n")
        native = "check_current_density -net PWR" in tcl
        if native:
            (rpt / "em_openroad_density_PWR.csv").write_text(DHEADER +
                f"wireA,0,0,1,0,{current},1,{current},0.0009,{current / 0.0009:.3f},{'VIOLATED' if stress else 'OK'},0,AREAL\n" +
                ("cutA,0,0,0,0,0.000001,1,0.000001,0,0,NO_LIMIT,1,NO_LIMIT\n" if via else ""))
        markers = []
        for stage, role, path in re.findall(r'EM_BOUND_(BEFORE|AFTER) (\w+) \[lindex \[exec sha256sum (.*?)\] 0\]', tcl):
            markers.append(f"EM_BOUND_{stage} {role} {digest(Path(path.strip('{}')))}")
        if native:
            markers += ["EM_DENSITY_DONE PWR", "EM_BOUND_OUTPUT em_openroad_density_PWR.csv " + digest(rpt / "em_openroad_density_PWR.csv")]
        log = ("\n".join(markers) + "\n=== PSM_NET PWR ===\n"
               "[INFO PSM-0040] All shapes on net PWR are connected.\n"
               "Net : PWR\nTotal power : 2 W\nSupply voltage : 5 V\n"
               f"Worstcase IR drop: {0.781 if stress else 0.00001} V\nMaximum current : {current} A\n"
               "=== EM_POWER_BASIS ===\nTotal 0 0 0 2 100%\n=== EM_POWER_BASIS_END ===\n"
               "PSM_SOURCE_MODEL: promoted_supply_pins_excluded=0 placed_pads=0\n")
        (rpt / "ir_em.log").write_text(log)
        return 0, log, ""
    monkeypatch.setattr(R, "_docker_exec", offline_transport)
    notes = []
    assert R._emit_ir_em_reports(project, "unit", pdk, "offline", rpt / "ir_drop.rpt", rpt / "em.rpt", notes) == (True, True)
    return project, pdk, calls


def authority(project, pdk):
    return A.evaluate(project, None, Path(pdk.tech_lef), 0.1)


def test_default_invokes_native_checker_and_existing_librelane_route(tmp_path, monkeypatch):
    project, pdk, calls = build(tmp_path, monkeypatch)
    commands = [value for value in calls if "check_current_density -net PWR" in value]
    assert len(commands) == 1, calls
    assert calls.count("librelane_ir") == 1, calls
    doc = json.loads((project / "reports/phase3/em_openroad_density.json").read_text())
    assert doc["mode"] == "direct"
    assert doc["nets"]["PWR"]["checked"] == 1
    assert authority(project, pdk)[0] == "INCOMPLETE"


@pytest.mark.parametrize("subject", ["DEF", "SDC", "PDK", "CSV", "REPORT", "EXECUTION", "CONSUMER", "AUTHORITY", "LIMIT", "VIA_SUBSTITUTION", "OUTPUT_BINDING", "BASIS"])
def test_current_gate_refuses_each_reverse(tmp_path, monkeypatch, subject):
    project, pdk, _ = build(tmp_path, monkeypatch, direct=True)
    rpt = project / "reports/phase3"
    doc_path = rpt / "em_openroad_density.json"
    doc = json.loads(doc_path.read_text())
    if subject in ("DEF", "SDC", "PDK", "CSV", "REPORT", "AUTHORITY"):
        target = {"DEF": R._pl.pnr_dir(project) / "unit.def", "SDC": R._pl.pnr_dir(project) / "constraint.sdc",
                  "PDK": Path(pdk.tech_lef), "CSV": rpt / "em_openroad_density_PWR.csv",
                  "REPORT": rpt / "em.rpt", "AUTHORITY": rpt / "em_native_authority.tlef"}[subject]
        # On base there is no staged native authority; change its selected
        # actual authority instead, so this remains a VALUE control.
        if subject == "AUTHORITY" and not target.exists():
            target = Path(pdk.tech_lef)
        target.write_text(target.read_text() + "\n# changed bytes\n")
    elif subject == "EXECUTION":
        doc_path.unlink()
    elif subject == "CONSUMER":
        doc["source_identity"] = {"em_peak_current_authority_check.py": "removed"}
    elif subject in ("LIMIT", "VIA_SUBSTITUTION"):
        limit = rpt / "em_openroad_limits.txt"
        limit.write_text("wireA 999\n" if subject == "LIMIT" else "wireA 0.0009\ncutA 0.0009\n")
        if "outputs" in doc:
            doc["outputs"][str(limit.relative_to(project))] = digest(limit)
    elif subject == "OUTPUT_BINDING":
        doc.get("outputs", {}).pop("reports/phase3/em.rpt", None)
    elif subject == "BASIS":
        doc["power_basis"] = {"id": "another-power-basis"}
    if subject not in ("EXECUTION", "DEF", "SDC", "PDK", "CSV", "REPORT", "AUTHORITY"):
        doc_path.write_text(json.dumps(doc))
    verdict, rep = authority(project, pdk)
    assert verdict == "INCOMPLETE", rep


def test_cli_rejects_fake_jmax_override(tmp_path, monkeypatch):
    project, _, _ = build(tmp_path, monkeypatch, direct=True)
    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps({"layers": {"wireA": {"kind": "routing", "thickness_um": 1, "jmax_mA_per_um": 999}}}))
    verdict, rep = A.evaluate(project, fake, None, 0.1)
    assert verdict == "INCOMPLETE", rep


def test_measured_stress_fail_precedes_unmeasured_vias_and_primary(tmp_path, monkeypatch):
    project, pdk, _ = build(tmp_path, monkeypatch, via=True, stress=True)
    verdict, rep = authority(project, pdk)
    assert verdict == "FAIL", rep
    assert rep["jmax_screen"]["summary"]["segments_unscreened"] == 1
    assert json.loads((project / "reports/phase3/ir_drop.json").read_text())["verdict"] == "FAIL"


def test_missing_via_limit_never_borrows_wire_authority(tmp_path, monkeypatch):
    project, pdk, _ = build(tmp_path, monkeypatch, via=True)
    verdict, rep = authority(project, pdk)
    assert verdict == "INCOMPLETE", rep
    assert rep["jmax_screen"]["summary"]["segments_screened"] == 1
    assert rep["jmax_screen"]["summary"]["segments_unscreened"] == 1


def test_real_canonical_row_runs_existing_consumer(tmp_path, monkeypatch):
    import flow_compliance_check as F
    import yaml
    project, _, _ = build(tmp_path, monkeypatch)
    row_path = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "flow", "phase1_phase2_phase3.yaml")
    row = next(row for row in yaml.safe_load(row_path.read_text())["steps"] if str(row["id"]) == "25")
    clause = next(clause for clause in row["gate"]["all_of"] if clause.get("program_exit_zero", "").startswith("em_peak_current_authority_check "))
    result = F.check_step(project, {"id": "25", "name": row["name"],
                                   "gate": {"all_of": [clause]}}, {})
    assert result.status != "PASS", result

"""Neutral native instruments; actual retained Default EM owner and consumers.

No EDA run or IC PASS is claimed. The IR checkpoint uses the real Step-24
record/publish/adopt API; only native tool acquisition is substituted.
"""
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import execution_backend_producers as B
import execution_policy as P
import phase3_one_shot_runner as R
import em_current_density_check as E
import em_peak_current_authority_check as A
import librelane_ir_antenna as L
import step_output_collector as V
from test_backend_input_contract_sdc import _project

_REAL_IR_RECORDER = R._librelane_step24_record
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


def _transport_fixture(tmp_path, monkeypatch, *, via=False, stress=False, direct=False):
    project = tmp_path / "run"
    pnr = R._pl.pnr_dir(project)
    rpt = R._pl.reports_phase3_dir(project)
    pnr.mkdir(parents=True); rpt.mkdir(parents=True)
    layout = "UNITS DISTANCE MICRONS 1000 ;\nSPECIALNETS 1 ;\n- PWR + USE POWER + ROUTED wireA 1000 ( 0 0 ) ( 2000 0 ) ;\nEND SPECIALNETS\n"
    for name in ("unit.def", "routed.def"):
        (pnr / name).write_text(layout)
    (pnr / "constraint.sdc").write_text("set_load 0.02 [get_ports output]\n")
    extracted = R._pl.extracted_dir(project)
    extracted.mkdir(parents=True)
    (extracted / "unit.spef").write_text('*SPEF "IEEE 1481-1998"\n*DESIGN "unit"\n')
    tech = tmp_path / "authority.tlef"; tech.write_text(TECH)
    stage = project / "phase3/pdk_stage"
    stage.mkdir(); (stage / "authority.tlef").write_text(TECH)
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
    return project, pdk, calls



def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _current_ir(tmp_path, monkeypatch, *, stress=False, ir_fail=False):
    project, pdk, calls = _transport_fixture(tmp_path, monkeypatch, stress=stress)
    seed = _project(tmp_path)
    import shutil
    shutil.copytree(seed, project, dirs_exist_ok=True)
    pnr, rpt = R._pl.pnr_dir(project), R._pl.reports_phase3_dir(project)
    # The current producer and its downstream consumer read these actual bytes.
    _write(project, "input/step_0_5ic_answers.json", "{}\n")
    _write(project, "input/submission_template/neutral.json", "{}\n")
    _write(project, "phase2/stage2/constraints/pvt_matrix.json", "{}\n")
    _write(project, "phase3/stage3/pnr/unit_pnr.v", "module unit; endmodule\n")
    _write(project, "phase3/stage3/pnr/pdn.done", "native PDN complete\n")
    assets = {}
    def native_ir(root, *_args, **kw):
        calls.append("native_ir_transport")
        folder = "phase3/librelane/24/01-openroad-irdropreport"
        drop = 0.6 if ir_fail else 0.01
        state = _write(root, folder + "/state_out.json", json.dumps({"metrics": {
            "design_powergrid__drop__worst__net:PWR": drop}}))
        odb = _write(root, folder + "/input.odb", "neutral native ODB\n")
        _write(root, folder + "/state_in.json", json.dumps({"odb": str(odb),
            "sdc": str(kw["sdc"]), "spef": {"unit": str(kw["spef"])}}))
        _write(root, "phase3/librelane/24-config/OpenROAD.IRDropReport.json", json.dumps({
            "DEFAULT_CORNER": "unit", "TECH_LEFS": {"unit": pdk.tech_lef}}))
        log = _write(root, folder + "/openroad-irdropreport.log",
            "Reading timing library for the 'unit' corner at '" + pdk.liberty + "'\n"
            "[INFO PSM-0040] All shapes on net PWR are connected.\n")
        _write(root, folder + "/irdrop.rpt", "neutral IR native report\n")
        assets.update(state=state, log=log, odb=odb)
        return {"tool_state": str(state), "tool_state_sha256": digest(state),
            "def_sha256": digest(kw["routed_def"]), "spef_sha256": digest(kw["spef"]),
            "vdd_nets": ["PWR"], "gnd_nets": [], "lib_voltage_v": 5.0,
            "budget_pct": 10.0, "budget_source": "neutral native budget",
            "rc_source": "native tech LEF", "source_model": "neutral native supply",
            "rc_model": {"verdict": "AGREE", "disagreeing": []},
            "cross_check": {"verdict": "MEASURED", "per_net_worst_drop_v": {"PWR": drop}},
            "transient": {"verdict": "MEASURED", "max_dynamic_drop_mv": 10.0}}
    monkeypatch.setattr(L, "run_ir", native_ir)
    monkeypatch.setattr(R, "_librelane_step24_record", _REAL_IR_RECORDER)
    monkeypatch.setattr(R, "_librelane_step_ctx", lambda *_: ("SOURCE_CONTROL_ONLY", tmp_path))
    monkeypatch.setattr(B, "_parameters", lambda *_: (R, None, pdk, "SOURCE_CONTROL_ONLY"))
    return project, pdk, calls, assets, native_ir


def _ready(tmp_path, monkeypatch, *, stress=False, ir_fail=False):
    project, pdk, calls, assets, _ = _current_ir(tmp_path, monkeypatch, stress=stress, ir_fail=ir_fail)
    params = {"step_id": "24", "top": "unit", "pdk_root": str(tmp_path),
              "image_id": "SOURCE_CONTROL_ONLY", "execution_mode": "default"}
    # Real current Step-24 producer, publication, adoption and gate. The
    # pre-existing adoption API supplies this package's required checkpoint;
    # the independent Step-24 caller fix owns its automatic activation.
    inputs = {str(R._pl.pnr_dir(project).joinpath("constraint.sdc").relative_to(project)):
              digest(R._pl.pnr_dir(project) / "constraint.sdc")}
    attempt = V.begin_step(project, "24", inputs)
    assert B.produce_signoff(project, params)["verdict"] == "PASS"
    basis = {"layout_sha256": digest(R._pl.pnr_dir(project) / "unit.def"),
             "spef_sha256": digest(R._pl.extracted_dir(project) / "unit.spef")}
    R._step24_adopt_primary(project, basis, [])
    # A static primary has no later EM session's power dictionary. Use the
    # real publisher for that independent scientific measurement.
    path = project / "reports/phase3" / R._LL_IR_RECORD
    doc = json.loads(path.read_text())
    doc.pop("power_basis", None)
    R._librelane_step24_publish(project, doc)
    doc["published"] = {name: digest(project / name) for name in doc["published"]}
    path.write_text(json.dumps(doc))
    import ir_drop_report_check as I
    assert I._run_and_emit([str(project), "--json", str(path.parent / "ir_drop_signoff.json")]) == (1 if ir_fail else 0)
    assert E.validate_primary_ir(project)["valid"] is True
    V.finish_step(project, attempt, verdict="FAIL" if ir_fail else "PASS", result={}, canonical_pass=not ir_fail)
    calls.clear()
    monkeypatch.setattr(R, "_backend_parameter_values", lambda *_: {
        "pdk_root": str(tmp_path), "image_id": "SOURCE_CONTROL_ONLY"})
    return project, pdk, calls, assets


@pytest.mark.parametrize("case", ["current", "report_changed", "state_changed", "sdc_changed",
                                  "spef_changed", "missing_primary", "ir_fail", "em_fail"])
def test_default_step25_keeps_current_ir_owned_by_step24(tmp_path, monkeypatch, case):
    project, pdk, calls, assets = _ready(tmp_path, monkeypatch,
        stress=case == "em_fail", ir_fail=case == "ir_fail")
    rpt = project / "reports/phase3"
    changed = {"report_changed": rpt / "ir_drop.json", "state_changed": assets["state"],
               "sdc_changed": R._pl.pnr_dir(project) / "constraint.sdc",
               "spef_changed": R._pl.extracted_dir(project) / "unit.spef"}.get(case)
    if changed:
        changed.write_text(changed.read_text() + "\nchanged current scientific bytes\n")
    if case == "missing_primary":
        (rpt / R._LL_IR_RECORD).unlink()
    protected = [rpt / name for name in ("ir_drop.rpt", "ir_drop.json", "ir_drop_signoff.json", "dynamic_ir.json")]
    protected += list((project / "phase3/librelane/24").rglob("*"))
    before = {str(path.relative_to(project)): digest(path) for path in protected if path.is_file()}
    row = R._default_pnr_rows(project, "unit", pdk, "SOURCE_CONTROL_ONLY", ["25"])
    after = {name: digest(project / name) for name in before}
    validation = E.validate_primary_ir(project)
    bound = V.latest_pass(project, "25")
    observation = {"case": case, "status": row.status, "detail": row.detail,
        "calls": calls, "before": before, "after": after, "primary": validation,
        "step25_latest": bound, "step24_latest": V.latest_pass(project, "24"),
        "native_density": E.validate_native_density(project),
        "scope": "neutral actual Default producer/consumer; native transports substituted"}
    _write(project, "API_OBSERVATION.json", json.dumps(observation, indent=2))
    assert before == after, "Step25 must not rewrite its current Step24 inputs"
    invalid = case in {"report_changed", "state_changed", "sdc_changed", "spef_changed", "missing_primary"}
    if invalid:
        assert row.status == "NOT_MEASURED", row.detail
        assert calls == [], "invalid IR proof must stop before EM native acquisition"
    else:
        assert "native_ir_transport" not in calls
        commands = [call for call in calls if "check_current_density -net PWR" in call]
        assert len(commands) == 1, calls
        assert validation["valid"] is True
        expected = "FAIL" if case in {"ir_fail", "em_fail"} else "PASS"
        assert row.status == expected, row.detail
        if case == "current":
            assert bound["current_inputs"] is True
            assert V.latest_pass(project, "24")["current_inputs"] is True
            assert E.validate_native_density(project)["valid"] is True
            assert json.loads((rpt / "ir_drop.json").read_text())["power_basis"] is None
            assert (rpt / "ir_drop_psm_cross_check.json").is_file()

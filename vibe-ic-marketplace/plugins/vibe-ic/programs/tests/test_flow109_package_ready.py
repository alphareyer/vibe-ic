"""D75 binds the existing bare-die package/test consumers without approving tapeout."""
from __future__ import annotations

import json
import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
PLUGIN = PROGRAMS.parent
sys.path.insert(0, str(PROGRAMS))

import _tapeout_declaration as TD  # noqa: E402
import flow109  # noqa: E402
import flow109_package_ready as D75  # noqa: E402


def test_d75_adapter_loads_by_path_with_existing_consumers():
    spec = importlib.util.spec_from_file_location(
        "flow109_package_ready_path_control", PROGRAMS / "flow109_package_ready.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.STEP == "D75"
    assert module.operation_sources()["programs/provider_bond_pad_edge.py"]


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value,
                    encoding="utf-8")


def _context(project: Path, **features):
    registry = flow109.load_registry()
    selected = dict(next(profile["features"] for profile in
        registry["reference_profiles"] if profile["name"] == "Small digital IC without DFT"))
    selected.update({"package_model_required": False, "requires_dft": False,
                     "requires_perc": True})
    selected.update(features)
    context = flow109.execution_context(flow109.resolve_plan(selected), "D75", "D75")
    _write(project / "reports/flow109/requests" /
           f"{context['context_sha256']}.json", context)
    return context


def _declaration():
    doc = TD.blank_declaration()
    doc["answers"]["deliverable"] = TD.DELIVERABLE_DIE
    doc[TD.PROVENANCE_KEY] = {"deliverable": {
        "answered_by": TD.ANSWERED_BY_OWNER_VALUE,
        "citation": "D75 neutral fixture authority: deliver a bare die.",
    }}
    return doc


def _gds(path: Path) -> None:
    import klayout.db as k
    layout = k.Layout()
    layout.dbu = 0.001
    pad = layout.create_cell("PADX")
    pad.shapes(layout.layer(167, 5)).insert(k.Box(7500, 290000, 67500, 350000))
    top = layout.create_cell("chip_top")
    top.insert(k.CellInstArray(pad.cell_index(), k.Trans(850000, 0)))
    path.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(path))


def _project(root: Path) -> Path:
    project = root / "project"
    decl = _declaration()
    _write(project / TD.DECLARATION_REL, decl)
    own = _declaration()
    own["operator_template"] = {
        "path": None, "slot": None,
        "absent_reason": "Owner selected independent bare-die tapeout.",
    }
    _write(project / "input/submission_template/operator_answers.json", own)
    _write(project / TD.SELF_TAPEOUT_REL, "owner-selected independent tapeout\n")
    _write(project / "input/docs/L3_interfaces.md", """\
## Physical Pad Placement

| side | signals |
|------|---------|
| East (E) | `clk` |
""")
    _write(project / "phase1/generated_docs/L20_DFT_SCAN_TOPOLOGY.json", {
        "layer": "L20", "applicability": "NOT_APPLICABLE", "fields": {},
    })
    _write(project / "phase1/generated_docs/L19_CONSTRAINTS_PDK.json", {
        "layer": "L19", "fields": {"die_area_budget_um": "1000x1000"},
    })
    _write(project / "reports/phase3/io_pad_chip_top.json", {
        "program": "io_pad_chip_top_gen", "verdict": "WROTE",
        "chip_top_module": "chip_top",
        "io_library_lefs": ["input/pdk/io.lef"],
    })
    _write(project / "input/pdk/tech.tlef", """\
UNITS
  DATABASE MICRONS 2000 ;
END UNITS
LAYER Metal5
  TYPE ROUTING ;
  WIDTH 0.44 ;
END Metal5
""")
    _write(project / "input/pdk/io.lef", """\
VERSION 5.8 ;
MACRO PADX
  CLASS PAD ;
  ORIGIN 0 0 ;
  SIZE 75 BY 350 ;
  PIN PAD
    DIRECTION INOUT ;
    USE SIGNAL ;
    PORT
      LAYER Metal5 ;
      RECT 7.5 290 67.5 350 ;
    END
  END PAD
END PADX
END LIBRARY
""")
    _write(project / "input/pdk/layers.lyp", """\
<layer-properties><properties><name>OPEN</name><source>OPEN 167/5@1</source></properties></layer-properties>
""")
    _write(project / "input/submission_template/provider_target.json", {
        "provider": "neutral",
        "technical_target": {"dimensions_um": [1000, 1000],
                             "origin_um": [0, 0], "packaging": "bare_die"},
        "bond_pad_edge_distance": {
            "opening_layer": {"name": "OPEN",
                              "layer_properties": "input/pdk/layers.lyp"},
            "io_lefs": ["input/pdk/io.lef"],
        },
    })
    _write(project / "phase3/stage3/pnr/padring.def", """\
VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN chip_top ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 2000000 2000000 ) ;
COMPONENTS 1 ;
- u_pad_clk PADX + PLACED ( 1700000 0 ) N ;
END COMPONENTS
PINS 1 ;
- clk + NET clk + DIRECTION INPUT + USE SIGNAL
  + PORT
    + LAYER Metal5 ( -60000 -60000 ) ( 60000 60000 )
    + PLACED ( 1820000 640000 ) N ;
END PINS
NETS 1 ;
- clk ( PIN clk ) ( u_pad_clk PAD ) ;
END NETS
END DESIGN
""")
    _gds(project / "phase3/stage4/gds/chip_top.gds")
    phase3 = project / "reports/phase3"
    _write(phase3 / "perc_equivalent.json", {
        "verdict": "PASS", "categories": [
            {"category": "ESD", "status": "AUTOMATED", "result": "PASS"}],
    })
    _write(phase3 / "perc_equivalent.rpt", "OVERALL VERDICT: PASS\nESD\n")
    _write(phase3 / "PERC_SIGNOFF_MEMO.md", "**Overall verdict:** `PASS`\nESD\n")
    return project


def _by_name(result, name):
    return next(check for check in result["checks"] if check["check"] == name)


def test_registry_routes_only_the_existing_default_d75_component():
    registry = json.loads((PLUGIN / "flow/flow109.json").read_text())
    row = next(row for row in registry["rows"] if row["id"] == "D75")
    dispatch = row["profiles"][0]["dispatch"]
    assert dispatch["adapter"] == "flow109_package_ready"
    assert dispatch["native_step_id"] == "flow109.D75"
    assert dispatch["admission_policy"] == "source_owned_existing_native"
    assert dispatch["acceptance_scope"] == "existing_native_component_only"
    assert dispatch["component_scope"] == "existing_Default_bare_die_interface_component_only"
    plan = flow109.resolve_plan(next(profile["features"] for profile in
        registry["reference_profiles"] if profile["name"] == "Small digital IC without DFT"))
    row = flow109.resolve_step(plan, "D75")
    assert flow109.source_owned_native_dispatch(row)["adapter"] == "flow109_package_ready"
    assert row["metric_acceptance_status"] == "UNIMPLEMENTED"


def test_bare_die_positive_runs_actual_consumers_but_not_whole_flow(tmp_path):
    project = _project(tmp_path)
    result = D75.execute(project, _context(project))
    assert result["component_status"] == "PASS", result
    for name in ("tapeout_declaration_check", "pad_side_constraint_check",
                 "pad_bterm_coincidence_check", "provider_bond_pad_edge",
                 "perc_signoff_check"):
        assert _by_name(result, name)["status"] == "PASS", result
    assert _by_name(result, "test_access_applicability")["status"] == "NOT_APPLICABLE"
    assert result["flow109_acceptance"] == "NOT_MEASURED"
    assert result["delivery_accepted"] is False
    assert result["inputs"] and all(item["sha256"] for item in result["inputs"])
    D75.validate(project, result)


def test_wrong_domain_pad_pin_geometry_fails(tmp_path):
    project = _project(tmp_path)
    path = project / D75.PAD_DEF
    path.write_text(path.read_text().replace(
        "+ PLACED ( 1820000 640000 ) N ;",
        "+ PLACED ( 1000000 640000 ) N ;"))
    result = D75.execute(project, _context(project))
    assert result["component_status"] == "FAIL", result
    assert _by_name(result, "pad_bterm_coincidence_check")["status"] == "FAIL"


def test_missing_geometry_and_unsupported_package_models_do_not_pass(tmp_path):
    project = _project(tmp_path)
    (project / "phase3/stage4/gds/chip_top.gds").unlink()
    missing = D75.execute(project, _context(project))
    assert missing["component_status"] == "NOT_MEASURED"
    assert missing["reason"] == "D75_FINAL_GEOMETRY_UNBOUND"

    project = _project(tmp_path / "packaged")
    packaged = D75.execute(project, _context(project, package_model_required=True))
    assert packaged["component_status"] == "NOT_MEASURED"
    assert packaged["reason"] == "D75_PACKAGE_MODEL_PATH_UNSUPPORTED"


def test_failing_and_missing_perc_evidence_remain_fail_or_unmeasured(tmp_path):
    project = _project(tmp_path)
    summary = project / "reports/phase3/perc_equivalent.json"
    doc = json.loads(summary.read_text())
    doc["verdict"] = "FAIL"
    doc["categories"][0]["result"] = "FAIL"
    summary.write_text(json.dumps(doc))
    for projection in (project / "reports/phase3/perc_equivalent.rpt",
                       project / "reports/phase3/PERC_SIGNOFF_MEMO.md"):
        projection.write_text(projection.read_text().replace("PASS", "FAIL"))
    failed = D75.execute(project, _context(project))
    assert failed["component_status"] == "FAIL"
    assert _by_name(failed, "perc_signoff_check")["status"] == "FAIL"

    project = _project(tmp_path / "missing")
    (project / "reports/phase3/perc_equivalent.json").unlink()
    missing = D75.execute(project, _context(project))
    assert missing["component_status"] == "NOT_MEASURED"
    assert _by_name(missing, "perc_signoff_check")["status"] == "NOT_MEASURED"


def test_current_receipt_revalidates_and_changed_source_is_stale(tmp_path):
    project = _project(tmp_path)
    result = D75.execute(project, _context(project))
    D75.validate(project, result)
    lef = project / "input/pdk/io.lef"
    lef.write_text(lef.read_text() + "\n# changed after receipt\n")
    with pytest.raises(D75.D75Refusal, match="D75_INPUT_STALE"):
        D75.validate(project, result)


def test_wrong_provider_and_consumer_load_identity_refuse_then_restore(tmp_path,
                                                                      monkeypatch):
    project = _project(tmp_path)
    result = D75.execute(project, _context(project))
    assert result["component_status"] == "PASS", result

    wrong_provider = copy.deepcopy(result)
    wrong_provider["provider_identity"]["provider"] = "different-provider"
    with pytest.raises(D75.D75Refusal, match="D75_PROVIDER_IDENTITY_MISMATCH"):
        D75.validate(project, wrong_provider)

    actual_sources = D75.operation_sources()
    monkeypatch.setattr(D75, "operation_sources",
                        lambda: {**actual_sources,
                                 "programs/provider_bond_pad_edge.py": "0" * 64})
    with pytest.raises(D75.D75Refusal, match="D75_LOAD_IDENTITY_MISMATCH"):
        D75.validate(project, result)
    monkeypatch.setattr(D75, "operation_sources", lambda: actual_sources)
    D75.validate(project, result)


def test_context_feature_source_is_bound_and_missing_stale_or_unknown_refuses(tmp_path):
    project = _project(tmp_path)
    context = _context(project)
    result = D75.execute(project, context)
    request = (project / "reports/flow109/requests" /
               f"{context['context_sha256']}.json")
    assert result["context_sha256"] == context["context_sha256"]
    assert any(item["path"] == str(request.relative_to(project))
               and item["sha256"] for item in result["inputs"])

    request.unlink()
    missing = D75.execute(project, context)
    assert missing["reason"] == "D75_CONTEXT_SOURCE_UNBOUND"

    _write(request, context)
    changed = json.loads(request.read_text())
    changed["features"]["requires_dft"] = True
    _write(request, changed)
    stale = D75.execute(project, context)
    assert stale["reason"] == "D75_CONTEXT_SOURCE_STALE"

    unknown = dict(context)
    unknown["features"] = dict(context["features"])
    unknown["features"].pop("requires_dft")
    invalid = D75.execute(project, unknown)
    assert invalid["reason"] == "D75_CONTEXT_INVALID"


def test_normal_flow109_default_entry_runs_d75_without_generic_qualification(
        tmp_path, monkeypatch):
    import _delivery_route
    import flow109_admission

    project = _project(tmp_path)
    assert _delivery_route.admit(project, "ic") is None
    registry = flow109.load_registry()
    features = dict(next(profile["features"] for profile in
        registry["reference_profiles"] if profile["name"] == "Small digital IC without DFT"))
    features["package_model_required"] = False
    manifest = project / "flow109-manifest.json"
    manifest.write_text(json.dumps({"features": features}))
    monkeypatch.delenv(flow109.CONTEXT_ENV, raising=False)
    monkeypatch.delenv(flow109.ATTEMPT_ENV, raising=False)
    generic_admission_calls = []
    monkeypatch.setattr(flow109_admission, "require_admission",
                        lambda *a, **k: generic_admission_calls.append("called") or
                        {"status": "PASS"})
    args = SimpleNamespace(flow="109", flow109_manifest=manifest,
                           flow109_plan_only=False, execution_mode="default",
                           entry_step="D75", exit_step="D75")
    rc = flow109.prepare_front_door(args, project)
    assert generic_admission_calls == [], generic_admission_calls
    assert rc == 0
    result = json.loads((project / "reports/flow109/components/D75.json").read_text())
    assert result["component_status"] == "PASS"
    assert result["flow109_acceptance"] == "NOT_MEASURED"
    admission = json.loads((project / "reports/flow109/admission.json").read_text())
    assert admission["qualification_status"] == "NOT_CLAIMED"
    assert admission["acceptance_scope"] == "existing_native_component_only"

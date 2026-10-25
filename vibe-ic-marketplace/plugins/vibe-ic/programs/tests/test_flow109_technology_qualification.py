"""D03 native config resolution and ciel pin controls (in the EDA image)."""
from __future__ import annotations

import json
from pathlib import Path

import ciel
import flow109
import flow109_technology_qualification as d03
from programs.tests._hostpaths import require_repo


def _case(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    config = project / "phase3/librelane/config.json"
    config.parent.mkdir(parents=True)
    rtl = project / "input.v"
    rtl.write_text("module neutral(input clk, input a, output y); assign y = a; endmodule\n")
    # Resolve the installed family/variant from ciel, never copy a design/PDK
    # fixture or infer a pin from a historic campaign record.
    candidates = [(f, v) for f in ciel.Family.by_name.values()
                  if (Path("/foss/pdks/ciel") / f.name / "versions").is_dir()
                  for v in ciel.Version.get_all_installed("/foss/pdks", f.name)
                  if (Path(v.get_dir("/foss/pdks")) / f.default_variant).is_dir()]
    assert candidates, "native controls require an installed ciel PDK"
    family, pin = candidates[0]
    config.write_text(json.dumps({"meta": {"version": 2, "flow": "Chip"},
        "DESIGN_NAME": "neutral", "PDK": family.default_variant,
        "VERILOG_FILES": [str(rtl)], "CLOCK_PORT": "clk", "CLOCK_PERIOD": 20}))
    monkeypatch.setenv("VIBEIC_EDA_IMAGE", "example.invalid/eda@sha256:" + "e" * 64)
    monkeypatch.setenv("VIBEIC_LIBRELANE_PDK_ROOT", pin.get_dir("/foss/pdks"))
    context = {"requested_step": "D03", "resolved_row": {"id": "D03"},
               "context_sha256": "a" * 64}
    return project, context, rtl


def test_checked_in_registry_routes_default_d03_to_adapter():
    registry = json.loads(require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "flow", "flow109.json").read_text())
    row = next(item for item in registry["rows"] if item["id"] == "D03")
    dispatch = row["profiles"][0]["dispatch"]
    assert dispatch["adapter"] == "flow109_technology_qualification"
    assert dispatch["acceptance_scope"] == "existing_native_component_only"
    assert row["metric_acceptance_status"] == "IMPLEMENTED"
    assert "librelane_contract" not in row["source_components"]


def test_current_native_config_and_installed_pin_pass(tmp_path, monkeypatch):
    project, context, _ = _case(tmp_path, monkeypatch)
    result = d03.execute(project, context)
    assert result["component_status"] == "PASS"
    assert result["native_started"] is True
    assert result["metric_acceptance_status"] == "IMPLEMENTED"
    assert result["delivery_accepted"] is False
    assert result["metrics"]["tool_deck_corner_binding"]["pdk"]["revision"]
    consumed = flow109._selected_step_result(project, context, result, d03)
    assert consumed["default_row_status"] == "PASS" and consumed["completed"]
    assert consumed["whole_flow_acceptance"] == "NOT_MEASURED"


def test_missing_selected_io_library_preserves_native_failure(tmp_path, monkeypatch):
    project, context, _ = _case(tmp_path, monkeypatch)
    path = project / "phase3/librelane/config.json"
    config = json.loads(path.read_text())
    config["PAD_LIBS"] = {"*": [str(project / "missing_io.lib")]}
    path.write_text(json.dumps(config))
    result = d03.execute(project, context)
    assert result["component_status"] == "FAIL"
    assert result["native_started"] is True
    assert "missing_io.lib" in result["checks"][0]["detail"]
    assert result["completed"] is False


def test_removed_input_cannot_reuse_previous_native_pass(tmp_path, monkeypatch):
    project, context, rtl = _case(tmp_path, monkeypatch)
    first = d03.execute(project, context)
    assert first["default_row_status"] == "PASS"
    rtl.unlink()
    result = d03.execute(project, context)
    assert result["component_status"] == "FAIL"
    assert result["native_started"] is True
    assert result["reason"] == "D03_NATIVE_RESOLUTION_FAILED"
    assert result["completed"] is False


def test_missing_installed_pin_authority_is_named(tmp_path, monkeypatch):
    project, context, _ = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(ciel.Version, "get_all_installed", lambda *args: [])
    result = d03.execute(project, context)
    assert result["component_status"] == "NOT_MEASURED"
    assert result["native_started"] is True
    assert result["reason"] == "D03_CIEL_PIN_UNRESOLVED"
    assert result["completed"] is False


def test_missing_runtime_image_refuses_before_native_resolution(tmp_path, monkeypatch):
    project, context, _ = _case(tmp_path, monkeypatch)
    monkeypatch.delenv("VIBEIC_EDA_IMAGE")
    result = d03.execute(project, context)
    assert result["component_status"] == "NOT_MEASURED"
    assert result["native_started"] is False
    assert result["reason"] == "D03_RUNTIME_IMAGE_UNRESOLVED"

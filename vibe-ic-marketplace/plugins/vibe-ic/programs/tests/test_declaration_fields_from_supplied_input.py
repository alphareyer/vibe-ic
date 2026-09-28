"""A reused-IP declaration uses its supplied design input and refuses conflicts."""

import json
import hashlib
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import spec_declaration_emit as emitter  # noqa: E402


def _project(tmp_path: Path, *, clock: str = "sys_clk", size: int = 64,
             doc_default: str = "selectable") -> Path:
    project = tmp_path / "project"
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L7.md").write_text(
        "The Plugin MUST declare `plugin_output/declaration.json` before authoring:\n\n"
        "| Field | Required | Example |\n|---|---|---|\n"
        "| `top_module` | yes | `tile_top` or `other_top` |\n"
        f"| `memsize_bytes` | yes | `{size}`(primary) or `128` |\n"
        "| `clock_port_name` | yes | `sys_clk` |\n"
        "| `reset_polarity` | yes | `active_high` |\n"
        "| `gpio_pin_count` | yes | integer |\n"
        "| `rf_storage` | yes | `shared_sram`(this design choice) |\n"
        "| `fabric_interface_protocol` | yes | custom |\n")
    (docs / "L3.md").write_text(
        f"CLOCK_PORT = {clock}\n\n"
        "| Port group | Width | Direction | Description |\n|---|---|---|---|\n"
        "| `sys_clk` | 1-bit | input | system clock |\n"
        "| `rst` | 1-bit | input | synchronous reset, active-high |\n"
        "| `gpio_bus` | 3-bit | output | GPIO pins |\n")
    with (docs / "L3.md").open("a") as out:
        out.write("\n| Parameter | Default | Unit |\n|---|---|---|\n"
                  f"| `memsize` | {doc_default} | bytes |\n")
    source = project / "input" / "vendor_rtl" / "tile.v"
    source.parent.mkdir(parents=True)
    source.write_text("module tile_top #(parameter memsize=64) "
                      "(input sys_clk, input rst, output [2:0] gpio_bus); endmodule\n")
    staged = project / "phase2" / "stage1" / "rtl"
    staged.mkdir(parents=True)
    (staged / source.name).write_bytes(source.read_bytes())
    (staged / "SOURCE_MANIFEST.json").write_text(json.dumps({
        "build_rtl_provided": True,
        "staged_from_input": ["input/vendor_rtl/tile.v"],
    }))
    return project


def _elaborated() -> dict:
    return {"modules": {"tile_top": {
        "attributes": {"top": "1", "src": "phase2/stage1/rtl/tile.v:1.1-1.80"},
        "parameter_default_values": {"memsize": format(64, "032b")},
        "ports": {"sys_clk": {"direction": "input", "bits": [1]},
                  "rst": {"direction": "input", "bits": [2]},
                  "gpio_bus": {"direction": "output", "bits": [3, 4, 5]}},
    }}}


def _run(project: Path, monkeypatch) -> int:
    # Fake only the Yosys file write; the real emitter, resolver and CLI run.
    helper = getattr(emitter, "_input", None)
    if helper is not None:
        monkeypatch.setattr(helper, "elaborate_supplied",
                            lambda _project, _top, _files: _elaborated())
    return emitter.main([str(project), "--supplied-top", "tile_top",
                         "--set", "fabric_interface_protocol=separate_rw"])


def test_required_fields_are_derived_with_provenance(tmp_path, monkeypatch):
    project = _project(tmp_path)
    assert _run(project, monkeypatch) == 0
    decl = json.loads((project / "plugin_output/declaration.json").read_text())
    assert {k: decl[k] for k in ("top_module", "memsize_bytes",
                                "clock_port_name", "reset_polarity",
                                "gpio_pin_count", "rf_storage")} == {
        "top_module": "tile_top", "memsize_bytes": 64,
        "clock_port_name": "sys_clk", "reset_polarity": "active_high",
        "gpio_pin_count": 3, "rf_storage": "shared_sram"}
    sidecar = json.loads((project / "plugin_output/declaration.provenance.json").read_text())
    assert sidecar["fields"]["clock_port_name"]["provenance"].startswith(
        "derived_from_supplied_rtl")
    assert sidecar["fields"]["rf_storage"]["provenance"].startswith(
        "derived_from_ldoc:input/docs/L7.md:")


def test_conflicting_doc_and_rtl_refuse_by_field_name(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path, clock="other_clk", size=128)
    assert _run(project, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "clock_port_name" in err
    assert "memsize_bytes" in err
    assert not (project / "plugin_output/declaration.json").exists()


def test_parameter_default_disagreement_also_refuses(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path, doc_default="128")
    assert _run(project, monkeypatch) == 1
    assert "memsize_bytes" in capsys.readouterr().err
    assert not (project / "plugin_output/declaration.json").exists()


def test_late_emission_consumes_the_same_d1_selection(tmp_path):
    import design_one_shot_runner as runner

    project = tmp_path / "later"
    docs = project / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L7.md").write_text(
        "The Plugin MUST declare `plugin_output/declaration.json` before authoring:\n\n"
        "| Field | Required | Example |\n|---|---|---|\n"
        "| `fabric_interface_protocol` | yes | `separate_rw` or `shared` |\n")
    pack = project / "reports/audit/phase1/expert_parse_track_pack"
    pack.mkdir(parents=True)
    answer = pack / "l_doc_expectations.json"
    answer.write_text(json.dumps({"declaration_selection": {
        "fabric_interface_protocol": "separate_rw"}}))
    report = pack.parent / "expert_parse_track.json"
    report.write_text(json.dumps({
        "execution": {"observed_ai_status": "CONSUMED"},
        "ai_subtrack": {"status": "CONSUMED",
                        "answer_sha256": hashlib.sha256(answer.read_bytes()).hexdigest()},
    }))
    row = runner.step_arith_declaration_emit(project)
    assert row.status == "PASS", row
    decl = json.loads((project / "plugin_output/declaration.json").read_text())
    assert decl["fabric_interface_protocol"] == "separate_rw"

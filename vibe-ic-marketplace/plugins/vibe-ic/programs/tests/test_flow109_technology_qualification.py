"""Focused D03 Default adapter controls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import flow109
import flow109_technology_qualification as d03
from programs.tests._hostpaths import require_repo


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(tmp_path: Path, monkeypatch, *, include_io=True):
    project = tmp_path / "project"
    folder = project / "phase3/native"
    core = _write(project / "input/pdk/core.lib", """\
library(x) {
  cell (core_inv) {}
}
""")
    io = _write(project / "input/pdk/io.lib", """\
library(y) {
  cell (io_pad) {}
}
""")
    tech = _write(project / "input/pdk/tech.lef",
                  "UNITS\n DATABASE MICRONS 1000 ;\nEND UNITS\n")
    netlist = _write(project / "phase3/netlist.v", """\
module neutral(input a, output y);
  core_inv u0 (.A(a), .Y(n));
  io_pad u1 (.A(n), .Y(y));
endmodule
""")
    config = {
        "PDK": "neutral_pdk", "DEFAULT_CORNER": "tt",
        "CELL_LIBS": {"tt": [str(core.relative_to(project))]},
        "PAD_LIBS": {"tt": [str(io.relative_to(project))]} if include_io else {"tt": [str(core.relative_to(project))]},
        "TECH_LEFS": {"tt": str(tech.relative_to(project))},
    }
    config_path = _write(folder / "config.json", config)
    state_path = _write(folder / "state_out.json", {"nl": str(netlist.relative_to(project))})
    record = _write(project / "reports/phase3/ir_drop.json",
                    {"tool_state": str(state_path.relative_to(project)),
                     "tool_state_sha256": _sha(state_path)})
    config_files = {str(core.relative_to(project)): _sha(core),
                    str(tech.relative_to(project)): _sha(tech)}
    if include_io:
        config_files[str(io.relative_to(project))] = _sha(io)
    fp = {"step": "Neutral.Native", "image": d03.PINNED_IMAGE,
          "config_files": config_files,
          "state_files": {str(netlist.relative_to(project)): _sha(netlist)}}
    monkeypatch.setattr(d03, "_current_receipt",
                        lambda _project: (record, {"tool_state": str(state_path.relative_to(project))},
                                          {"input": fp}))
    context = {"requested_step": "D03", "resolved_row": {"id": "D03"},
               "context_sha256": "a" * 64}
    return project, context, io


def test_checked_in_registry_routes_default_d03_to_adapter():
    registry = json.loads(require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "flow", "flow109.json").read_text())
    row = next(item for item in registry["rows"] if item["id"] == "D03")
    dispatch = row["profiles"][0]["dispatch"]
    assert dispatch["adapter"] == "flow109_technology_qualification"
    assert dispatch["acceptance_scope"] == "existing_native_component_only"
    assert row["metric_acceptance_status"] == "UNIMPLEMENTED"


def test_current_native_selected_views_and_cells_pass(tmp_path, monkeypatch):
    project, context, _ = _case(tmp_path, monkeypatch)
    result = d03.execute(project, context)
    assert result["component_status"] == "PASS"
    assert result["metric_acceptance_status"] == "UNIMPLEMENTED"
    assert result["delivery_accepted"] is False
    assert result["checks"][0]["total_cell_instances"] == 2


def test_removed_io_lib_preserves_real_cell_mismatch(tmp_path, monkeypatch):
    project, context, _ = _case(tmp_path, monkeypatch, include_io=False)
    result = d03.execute(project, context)
    assert result["component_status"] == "FAIL"
    assert {finding["cell_name"] for finding in result["checks"][0]["findings"]
            if finding["category"] == "CELL_NOT_IN_PDK"} == {"io_pad"}


def test_changed_selected_lib_is_stale_not_fail_or_pass(tmp_path, monkeypatch):
    project, context, io = _case(tmp_path, monkeypatch)
    io.write_text(io.read_text() + "/* changed */\n")
    result = d03.execute(project, context)
    assert result["component_status"] == "NOT_MEASURED"
    assert result["reason"] == "D03_INPUT_STALE"


def test_missing_current_native_authority_is_named(tmp_path, monkeypatch):
    project, context, _ = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(d03, "_current_receipt", lambda _project: (_ for _ in ()).throw(
        d03.D03Refusal("D03_CURRENT_NATIVE_AUTHORITY_UNRESOLVED", "none")))
    result = d03.execute(project, context)
    assert result["component_status"] == "NOT_MEASURED"
    assert result["reason"] == "D03_CURRENT_NATIVE_AUTHORITY_UNRESOLVED"

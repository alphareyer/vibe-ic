"""Regression coverage for vibe-ic#3478.

Step 15 keeps old program trees and timestamped evidence on purpose.  The OBS
parity consumer must read the current LibreLane producer contract rather than
turning those retained files into design inputs.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parents[1]
GATE = PROGRAMS / "macro_obs_load_parity_check.py"


def _gate():
    spec = importlib.util.spec_from_file_location("_issue3478_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tech(layer: str = "metalA") -> str:
    return ("VERSION 5.8 ;\n"
            f"LAYER {layer}\n  TYPE ROUTING ;\nEND {layer}\n"
            "END LIBRARY\n")


def _macro(layer: str = "metalA") -> str:
    return ("VERSION 5.8 ;\nMACRO neutral_block\n  CLASS BLOCK ;\n"
            "  OBS\n"
            f"    LAYER {layer} ;\n"
            "      RECT 0 0 1 1 ;\n"
            "  END\nEND neutral_block\nEND LIBRARY\n")


def _current_config(project: Path, paths: dict) -> Path:
    out = project / "phase3/librelane/15-floorplan/12-current/config.json"
    out.parent.mkdir(parents=True)
    out.write_text(json.dumps(paths))
    return out


def test_discovery_uses_current_config_and_ignores_retained_program_tree(tmp_path):
    gate = _gate()
    tech = tmp_path / "mounted/tech.tlef"
    macro = tmp_path / "mounted/cell.lef"
    tech.parent.mkdir()
    tech.write_text(_tech())
    macro.write_text(_macro())
    stale = tmp_path / ".vibeic/runtime-program-trees/old/calibration/stale.lef"
    stale.parent.mkdir(parents=True)
    stale.write_text(_macro("missing_layer"))
    _current_config(tmp_path, {
        "TECH_LEFS": {"nom_*": str(tech)},
        "CELL_LEFS": [str(macro)],
    })

    assert gate.discover_lefs(tmp_path) == [macro, tech]
    assert stale not in gate.discover_lefs(tmp_path)


def test_current_logs_exclude_history_and_old_runtime_logs(tmp_path):
    gate = _gate()
    current = tmp_path / "phase3/librelane/15-floorplan/02-current/tool.log"
    current.parent.mkdir(parents=True)
    current.write_text("current")
    historical = tmp_path / "steps/phase3/history/tool.log"
    historical.parent.mkdir(parents=True)
    historical.write_text("undefined layer (old) referenced")
    runtime = tmp_path / ".vibeic/runtime-program-trees/old/tool.log"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("undefined layer (old) referenced")

    assert gate.discover_logs(tmp_path) == [current]


def test_current_contract_passes_even_when_retained_fixture_would_fail(tmp_path):
    gate = _gate()
    tech = tmp_path / "mounted/tech.tlef"
    macro = tmp_path / "mounted/cell.lef"
    tech.parent.mkdir()
    tech.write_text(_tech())
    macro.write_text(_macro())
    stale = tmp_path / ".vibeic/runtime-program-trees/old/calibration/stale.lef"
    stale.parent.mkdir(parents=True)
    stale.write_text(_macro("not_declared"))
    _current_config(tmp_path, {
        "TECH_LEFS": {"nom_*": str(tech)},
        "CELL_LEFS": [str(macro)],
    })
    report = tmp_path / "report.json"

    assert gate.main([str(tmp_path), "--json", str(report)]) == 0
    data = json.loads(report.read_text())
    assert data["masters_with_obs"] == ["neutral_block"]
    assert data["findings"] == []


def test_current_contract_still_fails_on_real_undefined_layer(tmp_path):
    gate = _gate()
    tech = tmp_path / "mounted/tech.tlef"
    macro = tmp_path / "mounted/cell.lef"
    tech.parent.mkdir()
    tech.write_text(_tech())
    macro.write_text(_macro("not_declared"))
    _current_config(tmp_path, {
        "TECH_LEFS": {"nom_*": str(tech)},
        "CELL_LEFS": [str(macro)],
    })
    report = tmp_path / "report.json"

    assert gate.main([str(tmp_path), "--json", str(report)]) == 1
    data = json.loads(report.read_text())
    assert data["findings"][0]["unresolvable_layers"] == ["not_declared"]


def test_unreadable_current_config_lefs_remain_not_measured(tmp_path):
    gate = _gate()
    missing = tmp_path / "mounted/missing.tlef"
    config = _current_config(tmp_path, {
        "TECH_LEFS": {"nom_*": str(missing)},
    })
    report = tmp_path / "report.json"

    assert gate.main([str(tmp_path), "--json", str(report)]) == 2
    data = json.loads(report.read_text())
    assert data["verdict"] == "BLOCKED"
    assert data["reason_class"] == "BLOCKED_BY_UPSTREAM"
    assert str(config) in data["reason"]
    assert str(missing) in data["reason"]


def test_producer_mount_path_rebases_only_to_byte_identical_runtime_pdk(
        tmp_path, monkeypatch):
    gate = _gate()
    runtime_root = tmp_path / "runtime-pdks"
    runtime_lef = runtime_root / "neutral/libs/cell.lef"
    runtime_lef.parent.mkdir(parents=True)
    runtime_lef.write_text(_macro())
    producer_path = Path("/producer-pdk/neutral/libs/cell.lef")
    config = _current_config(tmp_path, {
        "PDK_ROOT": "/producer-pdk",
        "CELL_LEFS": [str(producer_path)],
    })
    digest = hashlib.sha256(runtime_lef.read_bytes()).hexdigest()
    config.with_name("input_fingerprint.json").write_text(json.dumps({
        "config_files": {str(producer_path): digest},
    }))
    monkeypatch.setenv("VIBEIC_RUNTIME_PDK_ROOT", str(runtime_root))

    assert gate.discover_lefs(tmp_path) == [runtime_lef]

    runtime_lef.write_text(_macro("changed"))
    assert gate.discover_lefs(tmp_path) == [producer_path]

"""The routing gate reads every recorded view where its run read the view."""
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _pdk_layer_authority as authority
import _eda_pin as pin
import macro_obs_geometry_intersect_check as gate


LEF = """MACRO cell
  SIZE 10 BY 10 ;
  OBS
    LAYER MET1 ;
      RECT 0 0 10 10 ;
  END
END cell
"""
DEF = """UNITS DISTANCE MICRONS 1000 ;
COMPONENTS 1 ;
- u cell + FIXED ( 20000 20000 ) N ;
END COMPONENTS
SPECIALNETS 1 ;
- VDD ( * VDD ) + USE POWER + ROUTED MET1 140 + SHAPE FOLLOWPIN
  ( 10000 25000 ) ( 40000 25000 ) ;
END SPECIALNETS
"""
IMAGE = "example.invalid/eda@sha256:" + "a" * 64
VIEW = "/container/views/cell.lef"


def _project(root, *, image=IMAGE):
    routed = root / "phase3/stage3/pnr/routed.def"
    routed.parent.mkdir(parents=True)
    routed.write_text(DEF)
    inventory = root / "reports/phase3/physical_view_inventory.json"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({
        "schema": "vibe-ic/physical-view-inventory/1", "verdict": "RECORDED",
        "scope": "pnr_read_lef_inputs", "read_lef_paths": [VIEW],
    }))
    (root / "reports/container_image.json").write_text(json.dumps({
        "container": "run-container", "image_match": True,
        "require_image": image,
    }))


@pytest.mark.parametrize("body,expected", [(LEF, 1), (None, 2)])
def test_run_container_view_is_examined_or_blocks(tmp_path, monkeypatch, body, expected):
    _project(tmp_path)
    monkeypatch.setenv("EDA_CONTAINER", "run-container")
    monkeypatch.setattr(pin, "container_image_digest",
                        lambda name: ("sha256:" + "a" * 64, ""))

    def docker(container, argv):
        assert container == "run-container"
        if argv == ["test", "-f", VIEW]:
            return (0, "") if body else (1, "")
        if argv == ["cat", VIEW]:
            return (0, body) if body else (1, "")
        raise AssertionError(argv)

    monkeypatch.setattr(authority.ContainerReader, "_docker", staticmethod(docker))
    out = tmp_path / "result.json"
    try:
        assert gate.main([str(tmp_path), "--json", str(out)]) == expected
        result = json.loads(out.read_text())
        if body:
            assert len(result["findings"]) == 1
        else:
            assert result["verdict"] == "BLOCKED"
            assert result["reason_class"] == "BLOCKED_BY_UPSTREAM"
    finally:
        gate._ACTIVE_PDK_READER = None


def test_wrong_recorded_container_stays_blocked(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.setenv("EDA_CONTAINER", "other-container")
    monkeypatch.setattr(pin, "container_image_digest",
                        lambda name: ("sha256:" + "a" * 64, ""))
    out = tmp_path / "result.json"
    try:
        assert gate.main([str(tmp_path), "--json", str(out)]) == 2
        assert json.loads(out.read_text())["verdict"] == "BLOCKED"
    finally:
        gate._ACTIVE_PDK_READER = None


def test_matching_name_with_wrong_image_stays_blocked(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.setenv("EDA_CONTAINER", "run-container")
    monkeypatch.setattr(pin, "container_image_digest",
                        lambda name: ("sha256:" + "b" * 64, ""))
    out = tmp_path / "result.json"
    try:
        assert gate.main([str(tmp_path), "--json", str(out)]) == 2
        rec = json.loads(out.read_text())
        assert rec["verdict"] == "BLOCKED"
        assert "image" in rec["reason"]
    finally:
        gate._ACTIVE_PDK_READER = None

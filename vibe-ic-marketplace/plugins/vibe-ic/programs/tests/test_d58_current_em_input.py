"""D58 consumes the already-validated current D57/STA physical subject."""
import hashlib
import json
from pathlib import Path

import pytest

import execution_backend_producers as B
import phase3_one_shot_runner as R
from test_default_step24_current_adoption import _external_current_primary
from test_default_step25_em_owner import _ready, _transport_fixture
from test_pad_connected_pdn_ring import _pdk


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_step25(project, tmp_path, monkeypatch, pdk, calls):
    def controlled_em(*_args, **kwargs):
        calls.append(kwargs.get("layout_basis"))
        return True, True

    monkeypatch.setattr(B, "_parameters",
                        lambda *_a, **_k: (R, None, pdk, "SOURCE_CONTROL_ONLY"))
    monkeypatch.setattr(R, "_emit_ir_em_reports", controlled_em)
    monkeypatch.setattr(R, "_emit_em_current_authority", lambda *_a, **_k: None)
    return B.produce_signoff(project, {
        "step_id": "25", "top": "neutral", "pdk_root": str(tmp_path),
        "image_id": "SOURCE_CONTROL_ONLY", "execution_mode": "default",
    })


def test_default_step25_hands_exact_current_owner_views_to_native_em(
        tmp_path, monkeypatch):
    project, _, selected, paths, _ = _external_current_primary(tmp_path, monkeypatch)
    legacy = project / "phase3/stage3/pnr/neutral.def"
    legacy.unlink()
    calls = []
    result = _run_step25(project, tmp_path, monkeypatch, _pdk(tmp_path, ring=None), calls)
    assert result["verdict"] == "PASS"
    assert len(calls) == 1
    basis = calls[0]
    observed_source = "current-owner" if basis is not None else "legacy-default"
    assert observed_source == "current-owner"
    observed_paths = {role: Path(basis[role]["path"]) for role in
                      ("odb", "def", "netlist", "sdc", "spef")}
    expected_paths = {
        "odb": paths["odb"], "def": paths["def"], "netlist": paths["nl"],
        "sdc": paths["sdc"], "spef": paths["spef"],
    }
    assert observed_paths == expected_paths
    assert all(basis[role]["sha256"] == selected["records"][role]["sha256"]
               for role in ("odb", "def", "sdc", "spef"))
    assert basis["netlist"]["sha256"] == selected["records"]["netlist"]["sha256"]
    assert not legacy.exists()


@pytest.mark.parametrize("damage", ["missing", "changed", "wrong_role", "symlink"])
def test_default_step25_refuses_damaged_current_owner_without_legacy_fallback(
        tmp_path, monkeypatch, damage):
    project, report, selected, paths, _ = _external_current_primary(tmp_path, monkeypatch)
    legacy = project / "phase3/stage3/pnr/neutral.def"
    if damage == "missing":
        paths["def"].unlink()
    elif damage == "changed":
        paths["def"].write_text(paths["def"].read_text() + "changed\n")
    elif damage == "wrong_role":
        doc = json.loads(report.read_text())
        doc["inputs"]["def"] = dict(selected["records"]["netlist"])
        report.write_text(json.dumps(doc))
    else:
        text = paths["def"].read_text()
        paths["def"].unlink()
        target = tmp_path / "foreign-final.def"
        target.write_text(text)
        paths["def"].symlink_to(target)
    calls = []
    result = _run_step25(project, tmp_path, monkeypatch, _pdk(tmp_path, ring=None), calls)
    assert result["verdict"] == "NOT_MEASURED"
    assert calls == []
    assert legacy.is_file(), "a present invalid owner must not select the legacy DEF"


def test_default_step25_preserves_primary_measured_fail_after_current_selection(
        tmp_path, monkeypatch):
    project, _, _, _, _ = _external_current_primary(tmp_path, monkeypatch, "native_fail")
    calls = []
    result = _run_step25(project, tmp_path, monkeypatch, _pdk(tmp_path, ring=None), calls)
    assert len(calls) == 1
    assert result["verdict"] == "FAIL"
    assert "current Step-24 IR measurement failed" in result["detail"]


def test_default_step25_without_current_owner_keeps_legacy_em_basis(
        tmp_path, monkeypatch):
    project, pdk, _, _ = _ready(tmp_path, monkeypatch)
    calls = []
    result = _run_step25(project, tmp_path, monkeypatch, pdk, calls)
    assert result["verdict"] == "PASS"
    assert calls == [None]


@pytest.mark.parametrize("damage", [
    "changed", "missing", "symlink", "changed_tech", "changed_model",
])
def test_native_em_current_basis_refuses_before_tool(tmp_path, monkeypatch, damage):
    project, pdk, calls = _transport_fixture(tmp_path, monkeypatch, direct=True)
    switch = project / "phase3/librelane_switch.json"
    switch.write_text(json.dumps({"steps": {"24": "direct", "25": "direct"}}))
    pnr = R._pl.pnr_dir(project)
    current = tmp_path / "current-owner"
    current.mkdir()
    source_def = pnr / "unit.def"
    paths = {
        "def": current / "final.def", "odb": current / "final.odb",
        "netlist": current / "final.v", "sdc": current / "final.sdc",
        "spef": current / "final.spef",
    }
    paths["def"].write_bytes(source_def.read_bytes())
    paths["odb"].write_text("current native ODB\n")
    paths["netlist"].write_text("module unit; endmodule\n")
    paths["sdc"].write_text("set_load 0.02 [get_ports output]\n")
    paths["spef"].write_text('*SPEF "IEEE 1481-1998"\n*DESIGN "unit"\n')
    source_def.unlink()
    basis = {role: {"path": str(path), "sha256": _sha(path)}
             for role, path in paths.items()}
    basis["tech_lef"] = {"path": str(pdk.tech_lef), "sha256": _sha(pdk.tech_lef)}
    basis["liberties"] = [{"path": str(pdk.liberty), "sha256": _sha(pdk.liberty)}]
    target = {"changed_tech": Path(pdk.tech_lef),
              "changed_model": Path(pdk.liberty)}.get(damage, paths["def"])
    if damage == "changed":
        target.write_text(target.read_text() + "changed\n")
    elif damage == "missing":
        target.unlink()
    else:
        text = target.read_text()
        target.unlink()
        foreign = tmp_path / "foreign.def"
        foreign.write_text(text)
        target.symlink_to(foreign)
    notes = []
    assert R._emit_ir_em_reports(
        project, "unit", pdk, "offline",
        project / "reports/phase3/ir_drop.rpt",
        project / "reports/phase3/em.rpt", notes,
        em_only=True, layout_basis=basis) == (False, False)
    assert calls == []
    assert any("basis changed" in note for note in notes)


def test_native_em_command_reads_exact_current_odb_without_legacy_def(
        tmp_path, monkeypatch):
    project, pdk, calls = _transport_fixture(tmp_path, monkeypatch, direct=True)
    switch = project / "phase3/librelane_switch.json"
    switch.write_text(json.dumps({"steps": {"24": "direct", "25": "direct"}}))
    pnr = R._pl.pnr_dir(project)
    current = tmp_path / "current-owner"
    current.mkdir()
    paths = {
        "def": current / "final.def", "odb": current / "final.odb",
        "netlist": current / "final.v", "sdc": current / "final.sdc",
        "spef": current / "final.spef",
    }
    paths["def"].write_bytes((pnr / "unit.def").read_bytes())
    paths["odb"].write_text("current native ODB\n")
    paths["netlist"].write_text("module unit; endmodule\n")
    paths["sdc"].write_text("set_load 0.02 [get_ports output]\n")
    paths["spef"].write_text('*SPEF "IEEE 1481-1998"\n*DESIGN "unit"\n')
    (pnr / "unit.def").unlink()
    basis = {role: {"path": str(path), "sha256": _sha(path)}
             for role, path in paths.items()}
    basis["tech_lef"] = {"path": str(pdk.tech_lef), "sha256": _sha(pdk.tech_lef)}
    basis["liberties"] = [{"path": str(pdk.liberty), "sha256": _sha(pdk.liberty)}]
    notes = []
    assert R._emit_ir_em_reports(
        project, "unit", pdk, "offline",
        project / "reports/phase3/ir_drop.rpt",
        project / "reports/phase3/em.rpt", notes,
        em_only=True, layout_basis=basis) == (True, True)
    command = next(call for call in calls if "check_current_density -net PWR" in call)
    assert f"read_db {paths['odb']}" in command
    assert "read_def" not in command
    density = json.loads((project / "reports/phase3/em_openroad_density.json").read_text())
    assert density["inputs"]["layout"]["sha256"] == _sha(paths["def"])
    assert density["inputs"]["librelane_odb"]["sha256"] == _sha(paths["odb"])
    assert density["inputs"]["netlist"]["sha256"] == _sha(paths["netlist"])
    assert not (pnr / "unit.def").exists()

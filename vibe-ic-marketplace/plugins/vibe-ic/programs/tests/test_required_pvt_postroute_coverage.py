"""Step 23 must account for every declared PVT corner after routing."""
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import sta_corner_record_completeness_check as gate  # noqa: E402


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


def _case(root, *, with_pvt_sweep=False):
    _write(root, "input/docs/L9_constraints_floorplan.md",
           "## Timing constraints (SDC)\n"
           "> Multi-corner sign-off: SS, TT, FF corners 均須 sign-off 通過。\n")
    _write(root, "phase2/stage2/constraints/pvt_matrix.json", json.dumps({
        "primary_corner": "TT", "corners": [
            {"name": "lib_SS", "label": "SS", "liberty": "/lib/SS.lib"},
            {"name": "lib_TT", "label": "TT", "liberty": "/lib/TT.lib"},
            {"name": "lib_FF", "label": "FF", "liberty": "/lib/FF.lib"}],
        "multi_corner": True,
    }))
    _write(root, "reports/phase3/mcorner_ocv_stance.json", json.dumps({
        "setup_process_corner": "SS", "hold_process_corner": "FF",
    }))
    _write(root, "phase3/stage3/sta/sta_mcorner_ocv.rpt",
           "=== SETUP corner: process=SS liberty=/lib/SS.lib, SPEF=x.max.spef ===\n"
           "STA_BASIS: POST_ROUTE_SPEF\nworst slack max 1.0\n"
           "=== HOLD corner: process=FF liberty=/lib/FF.lib, SPEF=x.min.spef ===\n"
           "STA_BASIS: POST_ROUTE_SPEF\nworst slack min 0.4\n"
           "SIGNOFF_CHECK_TYPES_REPORTED recovery removal max_slew "
           "min_pulse_width max_capacitance max_fanout\n")
    _write(root, "phase3/stage3/sta/per_corner/sta_TT.rpt",
           "STA_BASIS: PRE_LAYOUT_ESTIMATE\nworst slack max 3.0\n")
    if with_pvt_sweep:
        _write(root, "phase3/stage3/sta/sta_spef_based.rpt", "".join(
            f"=== {role} corner: process={corner} ===\n"
            "STA_BASIS: POST_ROUTE_SPEF\n"
            f"STA_BASIS_LIBERTY: /lib/{corner}.lib\n"
            "STA_BASIS_SPEF: x.nom.spef\n"
            f"worst slack {'max' if role == 'SETUP' else 'min'} 1.0\n"
            for corner in ("SS", "TT", "FF")
            for role in ("SETUP", "HOLD")))


def _judge(root):
    out = root / "verdict.json"
    rc = gate.main([str(root), "--json", str(out)])
    return rc, json.loads(out.read_text())


def test_missing_required_pvt_is_not_measured(tmp_path):
    _case(tmp_path)
    rc, data = _judge(tmp_path)
    assert rc == 1
    assert "R6_REQUIRED_PVT_NOT_MEASURED" in data["rules_violated"]
    assert data["verdict"] == "NOT_MEASURED"
    assert any("TT" in r for r in data["reasons"])


def test_postroute_pvt_sweep_is_indexed(tmp_path):
    _case(tmp_path, with_pvt_sweep=True)
    rc, data = _judge(tmp_path)
    assert rc == 0
    assert data["verdict"] == "PASS"
    rows = {r["corner"]: r for r in data["corners"] if r["axis"] == "process"}
    assert set(rows) == {"SS", "TT", "FF"}
    for row in rows.values():
        assert row["basis_used"]["setup_wns_ns"] == "SIGNOFF"
        assert row["basis_used"]["hold_wns_ns"] == "SIGNOFF"


def test_required_SS_corner_with_only_prelayout_data_is_not_measured(tmp_path):
    _case(tmp_path)
    _write(tmp_path, "phase3/stage3/sta/sta_mcorner_ocv.rpt",
           "=== HOLD corner: process=FF liberty=/lib/FF.lib, SPEF=x.min.spef ===\n"
           "STA_BASIS: POST_ROUTE_SPEF\nworst slack min 0.4\n"
           "SIGNOFF_CHECK_TYPES_REPORTED recovery removal max_slew "
           "min_pulse_width max_capacitance max_fanout\n")
    _write(tmp_path, "phase3/stage3/sta/per_corner/sta_SS.rpt",
           "STA_BASIS: PRE_LAYOUT_ESTIMATE\nworst slack max 4.0\n")
    rc, data = _judge(tmp_path)
    assert rc == 1
    assert data["verdict"] == "NOT_MEASURED"
    assert "R6_REQUIRED_PVT_NOT_MEASURED" in data["rules_violated"]
    SS = next(r for r in data["corners"] if r["corner"] == "SS")
    assert SS["basis_used"]["setup_wns_ns"] == "PRE_LAYOUT"


def test_sweep_with_prelayout_basis_does_not_close_pvt(tmp_path):
    _case(tmp_path, with_pvt_sweep=True)
    report = tmp_path / "phase3/stage3/sta/sta_spef_based.rpt"
    report.write_text(report.read_text().replace(
        "STA_BASIS: POST_ROUTE_SPEF", "STA_BASIS: PRE_LAYOUT_ESTIMATE"))
    rc, data = _judge(tmp_path)
    assert rc == 1
    assert data["verdict"] == "NOT_MEASURED"
    assert "R6_REQUIRED_PVT_NOT_MEASURED" in data["rules_violated"]


def test_explicit_all_corner_requirement_needs_a_pvt_matrix(tmp_path):
    _case(tmp_path)
    (tmp_path / "phase2/stage2/constraints/pvt_matrix.json").unlink()
    rc, data = _judge(tmp_path)
    assert rc == 1
    assert data["verdict"] == "NOT_MEASURED"
    assert "R6_REQUIRED_PVT_NOT_MEASURED" in data["rules_violated"]


def test_corner_label_with_wrong_liberty_is_not_measured(tmp_path):
    _case(tmp_path, with_pvt_sweep=True)
    report = tmp_path / "phase3/stage3/sta/sta_spef_based.rpt"
    report.write_text(report.read_text().replace(
        "STA_BASIS_LIBERTY: /lib/TT.lib",
        "STA_BASIS_LIBERTY: /lib/SS.lib"))
    rc, data = _judge(tmp_path)
    assert rc == 1
    assert data["verdict"] == "NOT_MEASURED"
    assert any("TT" in r for r in data["reasons"])

"""Default Step 7 consumes the existing tool's corner set in the fixed runner.

The prelayout tool-call seam is substituted. The production mode resolver,
runner, PVT adapter and independent PVT gate all execute unchanged.
The staged set deliberately differs from the tool's resolved set.
"""
import json
from types import SimpleNamespace

import pytest

import _plugin_tree  # noqa: F401
import librelane_contract as LC
import librelane_prelayout as LP
import phase3_one_shot_runner as R
import pvt_matrix_check as G
from test_librelane_contract_production_defaults import _chip, _switch


def _run(project, monkeypatch, *, corners=None, stop=None):
    libs = project / "input/pdk/liberty"
    libs.mkdir(parents=True)
    for name in ("staged__ss", "staged__tt"):
        (libs / f"{name}.lib").write_text('library(x) { time_unit : "1ns"; }')
    pdk = SimpleNamespace(name="testPDK", liberty=str(libs / "staged__tt.lib"),
                          macro_libs=[])
    calls = []

    def tool(proj, top, selected_pdk, sdc, design_staged, modes, notes):
        calls.append(dict(modes))
        assert proj == project and selected_pdk is pdk and sdc.is_file()
        assert modes["7"] != "dual"
        if stop:
            raise LC.Refusal(stop, "native tool did not complete")
        folder = project / "phase3/librelane/prelayout/tool-output"
        folder.mkdir(parents=True)
        return {"folder": folder,
                "resolved": {"STA_CORNERS": corners if corners is not None else
                              ["tool__ss_100C", "tool__tt_025C", "tool__ff_n40C"],
                             "DEFAULT_CORNER": "tool__tt_025C",
                             "LIB": {"tool__*": ["/pdk/tool.lib"]}},
                "sdc": {"verdict": "PASS", "findings": []},
                "slack": {"verdict": "PASS", "findings": []}}

    monkeypatch.setattr(R, "_prelayout_librelane", tool)
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {})
    monkeypatch.setattr(R, "_emit_multi_corner_sta", lambda *a, **k: False)
    monkeypatch.setattr(LP, "compose_corner_reports", lambda *a, **k: [])
    result = R.step_prelayout_signoff(project, "top", pdk, "unused-container")
    path = project / "phase2/stage2/constraints/pvt_matrix.json"
    return result, json.loads(path.read_text()) if path.is_file() else None, calls


def test_chip_default_binds_canonical_pvt_to_tool_output(tmp_path, monkeypatch):
    project = _chip(tmp_path)
    result, matrix, calls = _run(project, monkeypatch)
    assert result.status == "PASS", result.detail
    assert calls == [{"7": "librelane", "8": "dual", "10": "librelane"}]
    assert [c["name"] for c in matrix["corners"]] == [
        "tool__ss_100C", "tool__tt_025C", "tool__ff_n40C"]
    assert matrix["primary_corner"] == "tool__tt_025C"
    assert matrix["corner_source"].startswith("LibreLane resolved")
    assert G.audit(project)["rc"] == 0
    assert list((project / "phase2/stage2/constraints").glob("*.sdc"))
    assert not (project / "phase3/tool_arms/7").exists()


def test_explicit_direct_keeps_staged_population(tmp_path, monkeypatch):
    project = _chip(tmp_path)
    _switch(project, {"7": "direct"})
    result, matrix, calls = _run(project, monkeypatch)
    assert result.status == "PASS", result.detail
    assert calls[0]["7"] == "direct"
    assert [c["name"] for c in matrix["corners"]] == ["staged__ss", "staged__tt"]
    assert G.audit(project)["rc"] == 0


def test_empty_tool_corner_set_stays_rejected_by_existing_gate(tmp_path, monkeypatch):
    project = _chip(tmp_path)
    _, matrix, _ = _run(project, monkeypatch, corners=[])
    assert matrix["corners"] == []
    assert G.audit(project)["rc"] == 1


@pytest.mark.parametrize("stop,status,reason", [
    ("LL_TOOL_DEADLINE", "NOT_MEASURED", "budget_exhausted"),
    ("LL_TOOL_STALLED", "NOT_MEASURED", "stalled"),
    ("LL_STEP_FAILED", "FAIL", ""),
])
def test_tool_failure_does_not_fall_back_to_staged_pass(tmp_path, monkeypatch,
                                                       stop, status, reason):
    project = _chip(tmp_path)
    result, matrix, calls = _run(project, monkeypatch, stop=stop)
    assert calls and matrix is None
    assert result.status == status and result.reason_class == reason


def test_core_only_keeps_fixed_direct_producer(tmp_path, monkeypatch):
    result, matrix, calls = _run(tmp_path, monkeypatch)
    assert result.status == "PASS", result.detail
    assert calls == []
    assert [c["name"] for c in matrix["corners"]] == ["staged__ss", "staged__tt"]
    assert G.audit(tmp_path)["rc"] == 0

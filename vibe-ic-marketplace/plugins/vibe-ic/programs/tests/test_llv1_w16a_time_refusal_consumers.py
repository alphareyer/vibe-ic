"""Every W16a refusal consumer keeps timeouts environmental and other tool
failures red. The tests drive each consumer's real exception boundary."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as design  # noqa: E402
import librelane_contract as contract  # noqa: E402
import phase3_one_shot_runner as phase3  # noqa: E402

TIME_CODES = ("LL_TOOL_STALLED", "LL_TOOL_DEADLINE")


def _raise(code):
    def raised(*_args, **_kwargs):
        raise contract.Refusal(code, "forced consumer test refusal")
    return raised


def _assert_row(row, code, name):
    if code in TIME_CODES:
        assert row.name == name
        assert row.status == "NOT_MEASURED", row
        assert row.reason_class == contract.tool_stop_reason(code)
    else:
        assert row.status == "FAIL", row


@pytest.mark.parametrize("code", (*TIME_CODES, "LL_STEP_FAILED"))
def test_synth_consumer_books_time_stops_not_measured(tmp_path, monkeypatch, code):
    (tmp_path / "phase3").mkdir()
    (tmp_path / "phase3/librelane_switch.json").write_text("{}")
    rtl = tmp_path / "input/rtl/top.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top; endmodule\n")
    import _rtl_include_hub
    monkeypatch.setattr(_rtl_include_hub, "silicon_rtl_selection", lambda _p: [rtl])
    monkeypatch.setattr(contract, "resolve_image", _raise(code))
    pdk = SimpleNamespace(name="test", liberty=tmp_path / "lib.lib",
                          macro_libs=[], macro_lefs=[], macro_v=[])
    row = phase3._step_synth_librelane(tmp_path, "top", pdk, "container")
    _assert_row(row, code, "synth")


@pytest.mark.parametrize("code", (*TIME_CODES, "LL_STEP_FAILED"))
def test_step37_consumer_books_time_stops_not_measured(tmp_path, monkeypatch, code):
    pnr = tmp_path / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    for name in ("top.def", "routed.def"):
        (pnr / name).write_text("same def\n")
    (pnr / "top_pnr.v").write_text("module top; endmodule\n")
    (pnr / "constraint.sdc").write_text("create_clock -period 10 clk\n")
    monkeypatch.setattr(contract, "selected_mode", lambda *_: "librelane")
    monkeypatch.setattr(phase3, "_layout_basis", lambda *_: ("basis", None))
    monkeypatch.setattr(phase3._ga, "gate_passed", lambda *_: True)
    monkeypatch.setattr(phase3, "_vacuous_on_unrouted", lambda *_: None)
    monkeypatch.setattr(contract, "resolve_image", lambda *_: "img")
    monkeypatch.setattr(contract, "resolve_pdk_root", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(phase3, "_streamout_top", lambda *_: ("top", ""))
    monkeypatch.setattr(phase3, "publish_database_unit_declaration", lambda *_: None)
    monkeypatch.setattr(phase3, "publish_tapeout_declarations", lambda *_: None)
    import drc_feedback_repair
    monkeypatch.setattr(drc_feedback_repair, "has_reviewed_rule", lambda *_: False)
    import librelane_step37
    monkeypatch.setattr(librelane_step37, "run", _raise(code))
    pdk = SimpleNamespace(name="test", drc_deck=None)
    row = phase3.step_gds(tmp_path, "top", pdk, "container")
    _assert_row(row, code, "gds")


@pytest.mark.parametrize("code", (*TIME_CODES, "LL_STEP_FAILED"))
def test_step37_dual_consumer_books_time_stops_not_measured(tmp_path, monkeypatch, code):
    pnr = tmp_path / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    for name in ("top.def", "routed.def"):
        (pnr / name).write_text("same def\n")
    (pnr / "top_pnr.v").write_text("module top; endmodule\n")
    (pnr / "constraint.sdc").write_text("create_clock -period 10 clk\n")
    monkeypatch.setattr(contract, "selected_mode", lambda *_: "dual")
    monkeypatch.setattr(phase3, "_layout_basis", lambda *_: ("basis", None))
    monkeypatch.setattr(phase3._ga, "gate_passed", lambda *_: True)
    monkeypatch.setattr(phase3, "_vacuous_on_unrouted", lambda *_: None)
    monkeypatch.setattr(contract, "resolve_image", lambda *_: "img")
    monkeypatch.setattr(contract, "resolve_pdk_root", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(phase3, "_streamout_top", lambda *_: ("top", ""))
    monkeypatch.setattr(phase3, "publish_database_unit_declaration", lambda *_: None)
    monkeypatch.setattr(phase3, "publish_tapeout_declarations", lambda *_: None)
    import drc_feedback_repair
    monkeypatch.setattr(drc_feedback_repair, "has_reviewed_rule", lambda *_: False)
    import librelane_step37
    def emitted(*_args, **_kwargs):
        gds = pnr / "top.gds"
        gds.write_bytes(b"gds")
        return {"gds": gds, "engine": "fake", "promotion": "receipt", "state": {}}
    monkeypatch.setattr(librelane_step37, "run", emitted)
    monkeypatch.setattr(phase3, "_gds_substance_gate", lambda *_: None)
    def direct(*_args, **_kwargs):
        (pnr / "top.gds").write_bytes(b"direct")
        return phase3.StepResult("gds", "PASS", detail="direct")
    monkeypatch.setattr(phase3, "_step_gds_direct", direct)
    monkeypatch.setattr(contract, "resolve_step_configs", lambda *_a, **_k: [])
    import librelane_step37 as step37
    monkeypatch.setattr(step37, "_gds_state", lambda *_a, **_k: {})
    monkeypatch.setattr(step37, "_measured_drc", lambda *_a, **_k: {})
    monkeypatch.setattr(contract, "execute_dual", _raise(code))
    pdk = SimpleNamespace(name="test", drc_deck=None)
    row = phase3.step_gds(tmp_path, "top", pdk, "container")
    _assert_row(row, code, "gds")


@pytest.mark.parametrize("code", (*TIME_CODES, "LL_STEP_FAILED"))
def test_prelayout_consumer_books_time_stops_not_measured(tmp_path, monkeypatch, code):
    (tmp_path / "input/pdk/liberty").mkdir(parents=True)
    for name in ("tt.lib", "ss.lib"):
        (tmp_path / "input/pdk/liberty" / name).write_text("")
    pnr = tmp_path / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "constraint.sdc").write_text("design sdc\n")
    monkeypatch.setattr(phase3, "_resolve_staged_silicon_sdc", lambda *_: None)
    monkeypatch.setattr(contract, "selected_mode", lambda *_: "librelane")
    monkeypatch.setattr(phase3, "_prelayout_librelane", _raise(code))
    pdk = SimpleNamespace(name="test", liberty=tmp_path / "tt.lib")
    row = phase3.step_prelayout_signoff(tmp_path, "top", pdk, "container")
    _assert_row(row, code, "prelayout_signoff")


@pytest.mark.parametrize("code", (*TIME_CODES, "LL_STEP_FAILED"))
def test_design_lint_consumer_books_time_stops_not_measured(tmp_path, monkeypatch, code):
    switch = tmp_path / "phase3/librelane_switch.json"
    switch.parent.mkdir(parents=True)
    switch.write_text(json.dumps({"pdk": "test"}))
    monkeypatch.setattr(contract, "selected_mode", lambda *_: "librelane")
    monkeypatch.setattr(design, "_rcvar_l9_top_ports", lambda *_: None)
    import _rtl_include_hub
    monkeypatch.setattr(_rtl_include_hub, "silicon_rtl_selection", lambda *_: [])
    monkeypatch.setattr(contract, "resolve_image", lambda *_: "img")
    monkeypatch.setattr(contract, "emit_lint_config", _raise(code))
    row = design.step_rtl_lint_tool(tmp_path)
    _assert_row(row, code, "rtl_lint_tool")


@pytest.mark.parametrize("code", (*TIME_CODES, "LL_STEP_FAILED"))
def test_step22_rcx_consumer_separates_time_stop_from_real_failure(code):
    failures = []
    result = phase3._step22_rcx_failure(
        failures, contract.Refusal(code, "forced step-22 refusal"))
    if code in TIME_CODES:
        assert result == (contract.tool_stop_reason(code),
                          f"step 22 LibreLane RCX: {code}: forced step-22 refusal")
        assert failures == []
    else:
        assert result is None
        assert failures == [f"step 22 LibreLane RCX: {code}: forced step-22 refusal"]

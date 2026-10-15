"""The in-image model reader reaches local files without nested Docker."""
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sdf_gate_sim as sg


def _route(monkeypatch, local):
    monkeypatch.setattr(sg._ce, "no_container_route", lambda: local)
    monkeypatch.setattr(sg._instrument_calibration, "assert_calibrated", lambda *a: None)


def test_local_model_read_uses_actual_file_bytes(monkeypatch, tmp_path):
    _route(monkeypatch, True)
    model = tmp_path / "model's cells.v"
    model.write_text("module ordinary_cell(input A, output Y); assign Y=A; endmodule\n")
    def no_attach(*a, **k):
        raise AssertionError("local model read tried container supervision")
    monkeypatch.setattr(sg._ce, "run_in_container_supervised", no_attach)
    result = sg._docker("unused-container-label", "cat " + shlex.quote(str(model)))
    assert result.returncode == 0
    assert result.stdout == model.read_text()


def test_local_tool_failure_is_not_promoted_to_success(monkeypatch):
    _route(monkeypatch, True)
    def no_attach(*a, **k):
        raise AssertionError("local failure tried container supervision")
    monkeypatch.setattr(sg._ce, "run_in_container_supervised", no_attach)
    result = sg._docker("unused-container-label", "printf 'native error' >&2; exit 7", budget_s=17)
    assert result.returncode == 7
    assert result.stderr == "native error"


def test_container_route_retains_existing_guarded_supervision(monkeypatch):
    _route(monkeypatch, False)
    calls = []
    expected = subprocess.CompletedProcess([], 125, stdout="", stderr="refused image")
    def supervised(*a, **k):
        calls.append((a, k))
        return expected
    monkeypatch.setattr(sg._ce, "run_in_container_supervised", supervised)
    result = sg._docker("selected-container", "cat /pdk/cells.v", budget_s=71)
    assert result is expected
    assert calls == [(("selected-container", sg._TOOL_PATH + "cat /pdk/cells.v"), {"ceiling_s": 71.0})]

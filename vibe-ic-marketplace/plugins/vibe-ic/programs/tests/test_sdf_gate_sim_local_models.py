"""The in-image model reader reaches local files without nested Docker."""
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_local_shell_children_share_reapable_group(monkeypatch):
    _route(monkeypatch, True)
    # Keep the shell alive after the child, as the SDF run's trailing RC echo
    # does. Its group must be led by that shell so the supervisor reaps both.
    code = "import os; print(os.getpgrp() == os.getppid())"
    result = sg._docker("unused-container-label", "python3 -c " + shlex.quote(code) + "; :")
    assert result.returncode == 0
    assert result.stdout.strip() == "True"


@pytest.mark.skipif(not Path('/foss/pdks').is_dir(), reason='native PDK image required')
def test_actual_full_stack_model_resolver_in_image(tmp_path):
    import full_stack_functional_tb as fs
    # The public full-stack resolver used by generate(), with real pinned-image
    # PDK bytes and its default resolver; no injected callback or model stub.
    cell = 'gf180mcu_fd_sc_mcu7t5v0__tieh'
    files, why = fs.default_model_resolver(tmp_path, {cell}, 'outer-image-local')
    assert files, why
    assert any('module ' + cell in text for text in files.values())
    missing, _ = fs.default_model_resolver(tmp_path, {'unsupported_unknown_cell'}, 'outer-image-local')
    assert missing is None

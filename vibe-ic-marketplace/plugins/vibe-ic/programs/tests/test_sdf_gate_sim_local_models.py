"""The in-image model reader reaches local files without nested Docker."""
import shlex
import subprocess
import sys
import json
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
@pytest.mark.parametrize('container', [None, '', 'outer-image-local'], ids=['none', 'empty', 'label'])
def test_actual_full_stack_model_resolver_in_image(tmp_path, container):
    import full_stack_functional_tb as fs
    # The public full-stack resolver used by generate(), with real pinned-image
    # PDK bytes and its default resolver; no injected callback or model stub.
    cell = 'gf180mcu_fd_sc_mcu7t5v0__tieh'
    files, why = fs.default_model_resolver(tmp_path, {cell}, container)
    assert files, why
    assert any('module ' + cell in text for text in files.values())
    missing, _ = fs.default_model_resolver(tmp_path, {'unsupported_unknown_cell'}, container)
    assert missing is None
    staged, why = fs.stage_models(tmp_path, tmp_path / 'functional', {cell}, container)
    assert staged, why
    run = tmp_path / 'native-run'
    run.mkdir()
    tb = run / 'tb.v'
    tb.write_text('''module tb;
wire high;
gf180mcu_fd_sc_mcu7t5v0__tieh dut (.Z(high));
integer i, passed;
initial begin
  passed = 0;
  for (i = 0; i < 10000; i = i + 1) begin
    #1;
    if (high === 1'b1) passed = passed + 1;
    else $display("ORACLE_MISMATCH sample=%0d", i);
  end
  $display("ORACLE_TB_DONE pass=%0d/10000", passed);
  $finish;
end
endmodule
''')
    compile_argv = ['iverilog', '-g2012', '-s', 'tb', '-o', str(run / 'sim.vvp'),
                    *[str(tmp_path / row['path']) for row in staged], str(tb)]
    compile_rc, compile_log = fs.default_dispatch(compile_argv, run, container, 'iverilog', 60)
    (run / 'compile.log').write_text(compile_log)
    assert compile_rc == 0, compile_log
    run_rc, run_log = fs.default_dispatch(['vvp', str(run / 'sim.vvp')], run, container, 'vvp', 60)
    (run / 'run.log').write_text(run_log)
    score = fs.score_transcript('local_models', run_rc, run_log)
    (run / 'observation.json').write_text(json.dumps({'models': staged, 'compile_rc': compile_rc,
                                                    'run_rc': run_rc, 'score': score}, indent=2))
    assert score['state'] == fs.PASSED, score
    assert score['checks'] == {'passed': 10000, 'total': 10000}
    assert fs.population_shortfall(10000, score['checks']) is None
    assert fs.population_shortfall(10001, score['checks']) is not None
    incomplete, why = fs.stage_models(tmp_path, tmp_path / 'incomplete',
                                      {cell, 'unsupported_unknown_cell'}, container)
    assert incomplete is None
    assert 'unsupported_unknown_cell' in why


@pytest.mark.parametrize('container', [None, ''], ids=['none', 'empty'])
def test_remote_model_reader_still_requires_a_container_label(monkeypatch, tmp_path, container):
    import full_stack_functional_tb as fs
    import _eda_pin
    _route(monkeypatch, False)
    def forbidden(*args, **kwargs):
        raise AssertionError('missing remote label reached an attachment or model read')
    monkeypatch.setattr(_eda_pin, 'container_pin_state', forbidden)
    monkeypatch.setattr(sg, 'resolve_cell_models', forbidden)
    files, why = fs.default_model_resolver(tmp_path, {'ordinary_cell'}, container)
    assert files is None
    assert why == 'no EDA container to read the PDK models from'


def test_remote_model_reader_keeps_the_image_mismatch_refusal(monkeypatch, tmp_path):
    import full_stack_functional_tb as fs
    import _eda_pin
    _route(monkeypatch, False)
    monkeypatch.setattr(_eda_pin, 'container_pin_state', lambda label: ('MISMATCH', 'unit mismatched image'))
    def forbidden(*args, **kwargs):
        raise AssertionError('mismatched remote image reached the model reader')
    monkeypatch.setattr(sg, 'resolve_cell_models', forbidden)
    files, why = fs.default_model_resolver(tmp_path, {'ordinary_cell'}, 'selected-container')
    assert files is None
    assert why == 'the EDA container cannot be attached: unit mismatched image'

"""The deterministic serial template follows a declared reset style."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serial_parallel_mul_synth as synth


def _project(tmp_path, *, name, reset_name, reset_text):
    root = tmp_path / name
    docs = root / "phase1/generated_docs"
    docs.mkdir(parents=True)
    ports = [
        {"name": "clock", "direction": "input", "width": 1},
        {"name": reset_name, "direction": "input", "width": 1},
        {"name": "word", "direction": "input", "width": "WIDTH-1:0",
         "width_symbolic": "WIDTH-1:0", "msb": "WIDTH-1"},
        {"name": "serial_bit", "direction": "input", "width": 1},
        {"name": "result_bit", "direction": "output", "width": 1},
    ]
    (docs / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({
        "top_module": name, "top_ports": ports}))
    (docs / "L2_FRS.json").write_text(json.dumps({
        "description": "serial-parallel multiplier: result_bit = word * serial_bit mod 2^WIDTH. "
                       + reset_text}))
    return root


def test_async_low_and_sync_high_come_from_two_independent_inputs(tmp_path):
    async_project = _project(tmp_path, name="filter_unit", reset_name="reset_n",
                             reset_text="asynchronous active-low reset")
    sync_project = _project(tmp_path, name="vector_engine", reset_name="reset",
                            reset_text="synchronous active-high reset")
    for project, expected in ((async_project, "posedge clock or negedge reset_n"),
                              (sync_project, "posedge clock")):
        spec, why = synth.extract_serial_parallel_mul_spec(project, "digital_arithmetic_primitive")
        assert spec is not None, why
        rtl = synth.emit_rtl(spec)
        assert f"always @({expected})" in rtl
        assert synth.main([str(project), "--emit"]) == 0
        assert (project / "phase2/stage1/rtl" / (project.name + ".v")).is_file()


def test_silent_or_conflicting_reset_defers_without_outputs(tmp_path):
    for name, desc in (("quiet_unit", ""),
                       ("ambiguous_unit", "synchronous active-high reset")):
        project = _project(tmp_path, name=name, reset_name="reset",
                           reset_text=desc)
        if name == "ambiguous_unit":
            docs = project / "phase1/generated_docs"
            (docs / "L3_DETAIL_SPEC.json").write_text(json.dumps({
                "reset": {"mode": "asynchronous", "polarity": "active-high"}}))
        spec, why = synth.extract_serial_parallel_mul_spec(project, "digital_arithmetic_primitive")
        assert spec is None
        assert "reset" in why
        assert synth.main([str(project), "--emit"]) == 2
        assert not (project / "phase2/stage1/rtl").exists()
        assert not (project / "plugin_output/declaration.json").exists()

"""The serial framing offset has one edge and observation convention."""
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import arith_oracle_tb_gen as oracle
import design_one_shot_runner as runner


def _spec(top="vector_unit"):
    names = ("clock", "reset", "word", "bit_in", "bit_out")
    return {
        "top": top, "operator": "*", "width": 8, "signed": False,
        "parallel": names[2], "serial_in": names[3], "serial_out": names[4],
        "clk": names[0], "rst": names[1], "reset_active_low": False,
        "parallel_is_lhs": True,
        "ports": [{"name": n, "dir": "output" if i == 4 else "input",
                   "numeric_width": 8 if i == 2 else 1}
                  for i, n in enumerate(names)],
    }


def test_oracle_observes_after_the_sampling_edge_on_another_design():
    tb = oracle.emit_serial_oracle_module("tb_vector_unit", _spec(), [(3, 5)])
    assert "@(posedge clock);\n        #1;\n        _word[_k] = bit_out;" in tb


def test_calibrated_framing_carries_origin_and_refuses_bad_values(tmp_path):
    sim = tmp_path / "phase2/stage1/sim_full_stack"
    sim.mkdir(parents=True)
    path = sim / "arith_oracle_manifest.json"
    path.write_text("{}")
    runner._persist_oracle_calibrated_framing(
        tmp_path, "ORACLE_TB_FRAMING in_order=0 out_order=0 latency_cycles=1\n")
    measured = json.loads(path.read_text())
    assert measured["latency_origin"] == (
        "first serial input bit sampled at rising edge 0; matching output "
        "bit observed after rising edge L")
    from declaration_measurement_consistency_check import check
    decl = tmp_path / "plugin_output/declaration.json"
    decl.parent.mkdir()
    decl.write_text(json.dumps({"bit_order": "LSB_first", "latency_cycles": 1}))
    assert check(tmp_path)["verdict"] == "PASS"
    decl.write_text(json.dumps({"bit_order": "LSB_first", "latency_cycles": 2}))
    bad = check(tmp_path)
    assert bad["verdict"] == "FAIL"
    assert "latency_cycles" in bad["mismatches"]
    assert bad["latency_origin"] == measured["latency_origin"]
    step = runner.step_arith_declaration_emit(tmp_path)
    assert step.extras["declaration_measurement_consistency"]["verdict"] == "FAIL"
    assert "ADVISORY FAIL" in step.detail
    decl.write_text(json.dumps({"bit_order": "MSB_first", "latency_cycles": 1}))
    assert "bit_order" in check(tmp_path)["mismatches"]
    decl.write_text(json.dumps({"bit_order": "LSB_first", "latency_cycles": 1}))
    measured["calibrated_out_bit_order"] = "MSB_first"
    path.write_text(json.dumps(measured))
    assert "output_bit_order" in check(tmp_path)["mismatches"]
    path.write_text("{}")
    assert check(tmp_path)["verdict"] == "NOT_MEASURED"

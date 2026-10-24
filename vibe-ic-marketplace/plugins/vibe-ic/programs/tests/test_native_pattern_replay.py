"""Shared native test-pattern replay verdict controls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from librelane.state import DesignFormat
from librelane.steps.step import StepError
from librelane_plugin_vibeic import TestPatternReplay as PatternReplayStep


def _subject(tmp_path: Path, outcome="pass", stale=False, wrong_netlist=False):
    netlist = tmp_path / "core.v"
    expression = "~a" if wrong_netlist else "a"
    netlist.write_text(f"module core(input a, output y); assign y={expression}; endmodule\n")
    pattern = tmp_path / "case.vec"
    pattern.write_text("00\n11\n")
    marker = ("VIBEIC_PATTERN_REPLAY PASS case_1 2" if outcome == "pass"
              else "VIBEIC_PATTERN_REPLAY FAIL case_1 vector=1")
    tb = tmp_path / "tb.v"
    tb.write_text(f'''module pattern_tb;
reg a; wire y; reg [1:0] vectors [0:1]; integer i;
core dut(.a(a),.y(y));
initial begin
  $readmemb("{pattern}", vectors);
  for (i=0; i<2; i=i+1) begin
    a=vectors[i][1]; #1;
    if (y !== vectors[i][0]) begin
      $display("VIBEIC_PATTERN_REPLAY FAIL case_1 vector=%0d", i);
      $fatal(1, "pattern readback mismatch");
    end
  end
  $display("{marker}"); $finish;
end
endmodule
''')
    digest = hashlib.sha256(pattern.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"compile_flags": "-g2012", "models": [],
        "cases": [{"id": "case_1", "module": "pattern_tb",
                   "testbench": str(tb), "pattern": str(pattern),
                   "pattern_sha256": "0" * 64 if stale else digest,
                   "testbench_sha256": hashlib.sha256(tb.read_bytes()).hexdigest(),
                   "vector_count": 2}]}))
    step_dir = tmp_path / "step"
    instance = SimpleNamespace(id=PatternReplayStep.id,
        config={"VIBEIC_PATTERN_MANIFEST": str(manifest),
                "VIBEIC_PATTERN_TIMEOUT_S": 30},
        step_dir=str(step_dir), _run=PatternReplayStep._run)
    return instance, {DesignFormat.NETLIST: str(netlist)}, step_dir


def test_native_pattern_replay_passes_exact_population(tmp_path):
    instance, state, folder = _subject(tmp_path)
    _, metrics = PatternReplayStep.run(instance, state)
    assert metrics == {"vibeic__pattern_replay__case__count": 1,
                       "vibeic__pattern_replay__vector__count": 2,
                       "vibeic__pattern_replay__passed": 1}
    assert json.loads((folder / "pattern_replay_results.json").read_text())["verdict"] == "PASS"


@pytest.mark.parametrize("outcome,stale", [("fail", False), ("pass", True)])
def test_native_pattern_replay_blocks_bad_result_or_changed_pattern(
        tmp_path, outcome, stale):
    instance, state, folder = _subject(tmp_path, outcome, stale)
    with pytest.raises(StepError, match="BLOCKING pattern replay"):
        PatternReplayStep.run(instance, state)
    assert json.loads((folder / "pattern_replay_results.json").read_text())["verdict"] == "FAIL"


def test_native_pattern_replay_rejects_wrong_implementation(tmp_path):
    instance, state, folder = _subject(tmp_path, wrong_netlist=True)
    with pytest.raises(StepError, match="BLOCKING pattern replay"):
        PatternReplayStep.run(instance, state)
    result = json.loads((folder / "pattern_replay_results.json").read_text())
    assert result["verdict"] == "FAIL"
    assert result["runs"][0]["compile_rc"] == 0
    assert result["runs"][0]["sim_rc"] != 0
    assert result["runs"][0]["marker_count"] == 0

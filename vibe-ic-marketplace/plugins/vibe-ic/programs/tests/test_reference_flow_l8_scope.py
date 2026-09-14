"""A scoped reference-flow clock must not be promoted as an unscoped fact."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase1_doc_one_shot_runner as runner  # noqa: E402


def test_paired_scoped_tcl_emits_only_scoped_l8_record(tmp_path):
    docs = tmp_path / "phase1" / "generated_docs"
    docs.mkdir(parents=True)
    for name in ("L8_RTL_CONSTANTS", "L8_TIMING_WAVEFORM"):
        (docs / f"{name}.json").write_text(json.dumps({"clock_domains": []}))
    conf = tmp_path / "input" / "reference_flow" / "pre" / "synth.tcl"
    conf.parent.mkdir(parents=True)
    conf.write_text("# using the process_family_a library\nset flow_clk_input clk_a\nset flow_clk_period 8000.0\n")
    runner._post_emit_reference_clock_config(tmp_path)
    got = json.loads((docs / "L8_RTL_CONSTANTS.json").read_text())["clock_domains"]
    assert got == [{"name": "clk_a", "source_pin": "clk_a", "domain_kind": "primary", "role": "master", "period_ns": 8.0, "freq_mhz": 125.0, "pdk_scoped_target": "process_family_a", "evidence": {"path": "input/reference_flow/pre/synth.tcl", "kind": "reference_flow_clock"}}]


def test_unscoped_tcl_does_not_promote_clock(tmp_path):
    docs = tmp_path / "phase1" / "generated_docs"
    docs.mkdir(parents=True)
    (docs / "L8_RTL_CONSTANTS.json").write_text('{"clock_domains": []}')
    conf = tmp_path / "input" / "reference_flow" / "s.tcl"
    conf.parent.mkdir(parents=True)
    conf.write_text("set flow_clk_input clk_a\nset flow_clk_period 8000.0\n")
    runner._post_emit_reference_clock_config(tmp_path)
    assert json.loads((docs / "L8_RTL_CONSTANTS.json").read_text())["clock_domains"] == []

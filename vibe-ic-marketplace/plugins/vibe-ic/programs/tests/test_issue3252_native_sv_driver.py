"""Native SV frontend and procedural write attribution for #3252."""
import json
from pathlib import Path
import subprocess

import rtl_hygiene_lint as lint


COMPARISON = """module sample(input clk, input [3:0] d, output [3:0] y);
  reg [3:0] q;
  always @(posedge clk) q <= d;
  wire [3:0] phase;
  wire bounded=(phase<=4'd5);
  assign phase=q;
  assign y=phase & {4{bounded}};
endmodule
"""


def test_comparison_initializer_is_read_not_second_driver():
    findings = lint.rule_continuous_vs_procedural_driver(COMPARISON, 'sample.sv')
    assert not any(f.rule == 'multidriven-continuous-procedural' for f in findings), findings


def test_unparenthesized_comparison_initializer_is_read_not_second_driver():
    rtl = COMPARISON.replace("wire bounded=(phase<=4'd5);",
                             "wire bounded = phase <= 4'd5;")
    findings = lint.rule_continuous_vs_procedural_driver(rtl, 'sample.sv')
    assert not any(f.rule == 'multidriven-continuous-procedural' for f in findings), findings


def test_real_procedural_and_continuous_drivers_remain_error():
    rtl = COMPARISON.replace('q <= d;', 'q <= d; phase <= d;')
    findings = lint.rule_continuous_vs_procedural_driver(rtl, 'sample.sv')
    assert any(f.rule == 'multidriven-continuous-procedural' and f.symbol == 'phase'
               for f in findings), findings


def test_for_header_update_does_not_hide_real_procedural_driver():
    rtl = """module sample(input clk, input d, output y);
  logic phase;
  always @(posedge clk) for(integer i=0; i<1; i=i+1) phase <= d;
  assign phase=d;
  assign y=phase;
endmodule
"""
    findings = lint.rule_continuous_vs_procedural_driver(rtl, 'sample.sv')
    assert any(f.rule == 'multidriven-continuous-procedural' and f.symbol == 'phase'
               for f in findings), findings


def test_sv_native_driver_uses_selected_top_params_defines_and_no_implicit_synthesis(tmp_path, monkeypatch):
    sv = tmp_path / 'sample.sv'
    sv.write_text('module sample #(parameter W=4)(input [W-1:0] d, output [W-1:0] y); assign y=d; endmodule\n')
    scripts = []

    def run(script, work, include_dirs, context):
        scripts.append(script.read_text())
        (work / 'design.json').write_text(json.dumps({'modules': {'sample': {}}}))
        return subprocess.CompletedProcess(['yosys'], 0, '', '')

    monkeypatch.setattr(lint, '_native_driver_run', run)
    lint._native_driver_view([sv], 'sample', ['FEATURE'], ['W=12'], [tmp_path], tmp_path)
    assert len(scripts) == 1
    assert 'read_slang' in scripts[0]
    assert '--single-unit' in scripts[0]
    assert '--no-synthesis-define' in scripts[0]
    assert '--top sample' in scripts[0]
    assert '-G W=12' in scripts[0]
    assert '-D FEATURE' in scripts[0]
    assert str(sv) in scripts[0]


def test_verilog_native_driver_retains_read_verilog(tmp_path, monkeypatch):
    verilog = tmp_path / 'sample.v'
    verilog.write_text('module sample(input d, output y); assign y=d; endmodule\n')
    scripts = []

    def run(script, work, include_dirs, context):
        scripts.append(script.read_text())
        (work / 'design.json').write_text(json.dumps({'modules': {'sample': {}}}))
        return subprocess.CompletedProcess(['yosys'], 0, '', '')

    monkeypatch.setattr(lint, '_native_driver_run', run)
    lint._native_driver_view([verilog], 'sample', [], [], [tmp_path], tmp_path)
    assert 'read_verilog -sv -nosynthesis' in scripts[0]

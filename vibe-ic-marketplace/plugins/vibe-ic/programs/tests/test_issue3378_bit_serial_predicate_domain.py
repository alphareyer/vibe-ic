"""Issue #3378: predicate admission needs producer-owned word-domain proof."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


PROGRAM = Path(__file__).resolve().parents[1] / "interface_encoding_audit.py"


def _run(tmp_path: Path, source: str) -> dict:
    rtl = tmp_path / "rtl"
    out = tmp_path / "out"
    rtl.mkdir()
    (rtl / "neutral.v").write_text(source)
    result = subprocess.run(
        [sys.executable, str(PROGRAM), "--rtl-dir", str(rtl),
         "--top-module", "neutral", "-I", str(rtl),
         "--out-dir", str(out)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads((out / "encoding_audit_report.json").read_text())


def _edge(report: dict) -> dict:
    rows = [row for row in report["interfaces"]
            if row["wire_name"] == "link"
            and row["producer_module"] == "producer"
            and row["consumer_module"] == "consumer"]
    assert len(rows) == 1, report
    return rows[0]


def _serial_source(expression: str) -> str:
    return f"""
module producer(input [2:0] data, input [1:0] phase, output flag);
  wire [2:0] gray = data ^ (data >> 1);
  assign flag = {expression};
endmodule
module consumer(input clk, rst, flag, input [1:0] phase,
                output reg [2:0] value);
  always @(posedge clk) begin
    if (rst) value <= 3'b000;
    else if (flag) value[phase] <= 1'b1;
    else value[phase] <= 1'b0;
  end
endmodule
module neutral(input clk, rst, input [2:0] data, input [1:0] phase,
               output [2:0] value);
  wire link;
  producer source(.data(data), .phase(phase), .flag(link));
  consumer sink(.clk(clk), .rst(rst), .flag(link), .phase(phase), .value(value));
endmodule
"""


@pytest.mark.parametrize("expression", [
    "gray[phase]",
    "(gray >> phase) & 1'b1",
    "(phase == 0) ? gray[0] : (phase == 1) ? gray[1] : gray[2]",
    "(gray[phase] == 1'b1)",
])
def test_bit_serial_word_cannot_be_promoted_from_consumer_syntax(tmp_path, expression):
    row = _edge(_run(tmp_path, _serial_source(expression)))
    assert row["producer_encoding"] == "UNKNOWN", row
    assert row["consumer_encoding"] != "PREDICATE", row
    assert row["status"] == "UNKNOWN", row


def test_actual_one_bit_source_domain_keeps_predicate_proof(tmp_path):
    report = _run(tmp_path, """
module producer(input seed, output flag);
  assign flag = ~seed;
endmodule
module consumer(input flag, output reg seen);
  always @* if (flag) seen = 1'b1;
endmodule
module neutral(input seed, output seen);
  wire link;
  producer source(.seed(seed), .flag(link));
  consumer sink(.flag(link), .seen(seen));
endmodule
""")
    row = _edge(report)
    assert row["producer_encoding"] == "BIT_VALUE", row
    assert "one-bit source domain" in row["producer_evidence"], row
    assert row["consumer_encoding"] == "PREDICATE", row
    assert row["status"] == "MATCH", row


@pytest.mark.parametrize("producer_body", [
    "wire sample; assign sample = gray[phase]; assign flag = sample;",
    ("wire sample0, sample1; assign sample0 = gray[phase]; "
     "assign sample1 = sample0; assign flag = sample1;"),
    ("always @* begin if (gray[phase]) flag = 1'b1; "
     "else flag = 1'b0; end"),
])
def test_transitive_data_and_control_dependencies_keep_serial_words_unknown(
        tmp_path, producer_body):
    output_decl = "output reg flag" if producer_body.startswith("always") else "output flag"
    report = _run(tmp_path, f"""
module producer(input [2:0] data, input [1:0] phase, {output_decl});
  wire [2:0] gray = data ^ (data >> 1);
  {producer_body}
endmodule
module consumer(input clk, rst, flag, input [1:0] phase,
                output reg [2:0] value);
  always @(posedge clk) begin
    if (rst) value <= 3'b000;
    else if (flag) value[phase] <= 1'b1;
    else value[phase] <= 1'b0;
  end
endmodule
module neutral(input clk, rst, input [2:0] data, input [1:0] phase,
               output [2:0] value);
  wire link;
  producer source(.data(data), .phase(phase), .flag(link));
  consumer sink(.clk(clk), .rst(rst), .flag(link), .phase(phase), .value(value));
endmodule
""")
    assert len(report["interfaces"]) == 7, report
    row = _edge(report)
    assert row["producer_encoding"] == "UNKNOWN", row
    assert row["consumer_encoding"] != "PREDICATE", row
    assert row["status"] == "UNKNOWN", row


@pytest.mark.parametrize("producer_body, output_decl", [
    ("wire sample = seed; assign flag = sample;", "output flag"),
    ("always @* begin if (seed) flag = 1'b1; else flag = 1'b0; end",
     "output reg flag"),
])
def test_transitive_scalar_alias_and_guard_remain_proven(
        tmp_path, producer_body, output_decl):
    report = _run(tmp_path, f"""
module producer(input [2:0] unused_data, input [1:0] unused_phase,
                input seed, {output_decl});
  {producer_body}
endmodule
module consumer(input clk, rst, flag, input [1:0] phase,
                output reg [2:0] value);
  always @(posedge clk) begin
    if (rst) value <= 3'b000;
    else if (flag) value[phase] <= 1'b1;
    else value[phase] <= 1'b0;
  end
endmodule
module neutral(input clk, rst, seed, input [2:0] data, input [1:0] phase,
               output [2:0] value);
  wire link;
  producer source(.unused_data(data), .unused_phase(phase), .seed(seed), .flag(link));
  consumer sink(.clk(clk), .rst(rst), .flag(link), .phase(phase), .value(value));
endmodule
""")
    assert len(report["interfaces"]) == 8, report
    row = _edge(report)
    assert row["producer_encoding"] == "BIT_VALUE", row
    assert row["consumer_encoding"] == "PREDICATE", row
    assert row["status"] == "MATCH", row


def test_parent_binding_dependency_keeps_serial_word_unknown(tmp_path):
    report = _run(tmp_path, """
module producer(input seed, output flag); assign flag = seed; endmodule
module consumer(input clk, rst, flag, input [1:0] phase,
                output reg [2:0] value);
  always @(posedge clk) begin
    if (rst) value <= 3'b000;
    else if (flag) value[phase] <= 1'b1;
    else value[phase] <= 1'b0;
  end
endmodule
module neutral(input clk, rst, input [2:0] data, input [1:0] phase,
               output [2:0] value);
  wire link;
  wire [2:0] gray = data ^ (data >> 1);
  wire sample = gray[phase];
  producer source(.seed(sample), .flag(link));
  consumer sink(.clk(clk), .rst(rst), .flag(link), .phase(phase), .value(value));
endmodule
""")
    assert len(report["interfaces"]) == 6, report
    row = _edge(report)
    assert row["producer_encoding"] == "UNKNOWN", row
    assert row["consumer_encoding"] != "PREDICATE", row
    assert row["status"] == "UNKNOWN", row

"""PR3588 residual: source module identity from ``module named Name`` prose."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
CONFORMANCE = PROGRAMS / "spec_conformance_check.py"
sys.path.insert(0, str(PROGRAMS))

import _specrtl_common as subject  # noqa: E402


@pytest.mark.parametrize("text,expected", [
    ("Implement a module named probe_core.", "probe_core"),
    ("Implement a module named `probe_core`.", "probe_core"),
    ("Implement a module Named probe_core.", "probe_core"),
    ("module named(input a, output y); endmodule", "named"),
    ("module named #(parameter W=8)(input [W-1:0] a, output y); endmodule", "named"),
    ("module named; input a; output y; endmodule", "named"),
    ('module named(input a, output y); initial $display("module named setup_top"); endmodule',
     "named"),
    ("// Implement a module named comment_only.\n", None),
    ('"Implement a module named string_only."', None),
])
def test_reader_distinguishes_public_named_prose_from_hdl_and_noncode(text, expected):
    assert subject.extract_spec_contract(text, confirm=False).module == expected


def _write_consumer_case(tmp_path, *, target_present=True):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "00_helper.v").write_text(
        "module helper(input unrelated_i, output unrelated_o); "
        "assign unrelated_o = unrelated_i; endmodule\n")
    if target_present:
        (rtl / "10_probe_core.v").write_text(
            "module probe_core(input clk_i, input [7:0] data_i, "
            "output [7:0] data_o); assign data_o = data_i; endmodule\n")
    spec = tmp_path / "spec.md"
    spec.write_text(
        "Implement a module named probe_core with this exact interface.\n"
        "- input clk_i (1 bit)\n"
        "- input data_i (8 bits)\n"
        "- output data_o (8 bits)\n")
    report = tmp_path / "report.json"
    return rtl, spec, report


def _run_consumer(rtl, spec, report, *extra):
    run = subprocess.run(
        [sys.executable, str(CONFORMANCE), "--rtl-dir", str(rtl),
         "--spec", str(spec), "--json", str(report), *extra],
        capture_output=True, text=True)
    findings = json.loads(report.read_text()) if report.exists() else []
    return run, findings


def test_actual_conformance_consumer_selects_public_named_prose_target(tmp_path):
    """The ordinary gate must inspect probe_core, not alphabetic helper or `named`."""
    rtl, spec, report = _write_consumer_case(tmp_path)
    run, findings = _run_consumer(rtl, spec, report)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not [row for row in findings if row["severity"] == "ERROR"]


def test_actual_conformance_consumer_keeps_explicit_foreign_top_refusal(tmp_path):
    """The parser repair must not weaken the consumer's explicit top identity gate."""
    rtl, spec, report = _write_consumer_case(tmp_path)
    run, findings = _run_consumer(rtl, spec, report, "--top", "foreign_core")
    assert run.returncode == 2
    assert {row["rule"] for row in findings} == {"top-module-not-found"}


def test_actual_conformance_consumer_keeps_canonical_top_ambiguity_refusal(tmp_path):
    """Conflicting typed top declarations remain a refusal, never first-match policy."""
    rtl, _spec, report = _write_consumer_case(tmp_path)
    spec = tmp_path / "contract.json"
    spec.write_text(json.dumps({
        "module": "helper",
        "top_module": "probe_core",
        "ports": [{"name": "clk_i", "direction": "input", "width": 1}],
    }))
    run, findings = _run_consumer(rtl, spec, report)
    assert run.returncode == 2
    assert {row["rule"] for row in findings} == {"top-module-conflict"}


def test_topmodule_preference_and_parameterized_legal_hdl_are_unchanged():
    text = (
        "module helper(input a, output y); endmodule\n"
        "module TopModule #(parameter W=8)(input [W-1:0] data_i, output y); endmodule\n")
    contract = subject.extract_spec_contract(text, confirm=False)
    assert contract.module == "TopModule"
    _, ports = subject.parse_rtl_ports(text, "TopModule")
    assert {port.name: port.width for port in ports} == {"data_i": 8, "y": 1}

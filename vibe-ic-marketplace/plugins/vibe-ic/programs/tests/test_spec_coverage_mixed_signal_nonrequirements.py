"""Mixed-signal coverage must retain real gaps without inventing RTL work."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


PROGRAMS = Path(os.environ.get(
    "VIBEIC_SPEC_COVERAGE_SUBJECT",
    str(Path(__file__).resolve().parents[1]),
)).resolve()
SPEC = importlib.util.spec_from_file_location(
    "spec_coverage_subject", PROGRAMS / "spec_coverage_check.py")
assert SPEC is not None and SPEC.loader is not None
SUBJECT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUBJECT
SPEC.loader.exec_module(SUBJECT)


def _run(user_prompt="", l_docs=""):
    stations = {}
    if user_prompt:
        stations["user_prompt"] = user_prompt
    if l_docs:
        stations["l_docs"] = l_docs
    return SUBJECT.run(
        stations,
        "module d(input wire ck); always @(posedge ck) begin end endmodule",
        "module tb; endmodule",
        None,
        True,
    )


def test_explicit_absence_and_schema_instructions_are_not_requirements():
    report = _run(
        user_prompt="the die has NO reset pin",
        l_docs=(
            "Reset behavior verification.\n"
            "Spec does not specify DFT/scan topology; this is deferred to integration.\n"
            '"Look for scan / DFT / BIST / JTAG sections."\n'
            '"trim_loop": []\n'
            "floorplan\n"
        ),
    )
    kinds = {item["kind"] for item in report["items"]}
    assert kinds.intersection({
        "reset", "scan_chain", "jtag_tap", "bist",
        "calibration_field", "rounding_mode",
    }) == set()


def test_decimal_tail_does_not_mint_a_channel_count():
    report = _run(
        l_docs=(
            "incremental delta-sigma modulator core 1.2 V "
            "with 6 identical modulator channels"
        ),
    )
    requirements = {item["requirement"] for item in report["items"]}
    assert "ADC converter with 2 channel(s); the design must implement it and the TB / silicon must exercise the stated converter interface." not in requirements


def test_real_channel_count_is_retained():
    report = _run(
        l_docs="An array of 6 incremental delta-sigma modulator channels",
    )
    assert any(
        item["kind"] == "analog_converter" and "6 channel" in item["requirement"]
        for item in report["items"]
    )


def test_physical_corner_gap_is_advisory_to_a_digital_rtl_tb():
    report = _run(user_prompt="Supply rail is 1.8 V at -40/125 C.")
    assert report["blocked"] is False
    assert report["blocking_gaps"] == 0
    assert report["advisory_gaps"] >= 1


# Public #3088: normal CLI controls derived only from the stated source clauses.
import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import pytest  # noqa: E402

_NA_CALIBRATION = {
    "doc_id": "L6", "doc_name": "calibration", "status": "not-applicable",
    "description": "No calibration controller, OTP interface, OTP-based calibration or trim DAC is required.",
    "fields": {"datapath_description": "The unsigned product output is 8-bit."},
    "integration": {"input_filename": "L6_calibration"},
}
_SELECTED_CLOCK = "Selected target gf180mcuD: required clock frequency 41.7 MHz (24 ns).\n"
_UNSIGNED_RTL = "module neutral(input [7:0] a,b,output [7:0] product); assign product=a*b; endmodule\n"
_UNSIGNED_TB = """module tb;
reg unsigned [7:0] a,b; wire [7:0] product;
neutral dut(.a(a),.b(b),.product(product));
initial begin
  a=8'hff; b=8'h02; #1; if (product !== 8'hfe) $fatal(1);
  a=8'h80; b=8'h03; #1; if (product !== 8'h80) $fatal(1);
end
endmodule
"""
_PUBLIC_CONTEXT_CASES = {
    "na-calibration-and-otp": {"ldoc": _NA_CALIBRATION, "rc": 0,
        "absent": {"otp_field", "otp_lock", "calibration_field", "calibration_procedure"}},
    "filename-only": {"ldoc": {"doc_id": "L6", "doc_name": "calibration", "status": "extracted",
        "fields": {"datapath_description": "The product is the unsigned output."},
        "integration": {"input_filename": "L6_calibration"}}, "rc": 0,
        "absent": {"calibration_field", "calibration_procedure"}},
    "diagnostic-ceiling": {"ldoc": {"doc_id": "L7", "doc_name": "verification",
        "fields": {"diagnostic_notes": "Area/power diagnostic: ceiling is a technology-mismatched upper bound."}},
        "rc": 0, "absent": {"rounding_mode"}},
    "alternate-clocks": {"prompt": _SELECTED_CLOCK + "Alternate sky130A libraries have clock frequency 100 MHz and 125 MHz.\n",
        "rc": 1, "clock_rates": {"41.7"}},
    "estimated-reference-fmax": {"prompt": _SELECTED_CLOCK + "Estimated alternate-technology reference Fmax: 250 MHz.\n",
        "rc": 1, "clock_rates": {"41.7"}},
    "formula-example-frequency": {"prompt": _SELECTED_CLOCK + "The clock target example for 10 ns is 95 MHz; the formula depends on the selected target.\n",
        "rc": 1, "clock_rates": {"41.7"}},
    "alternate-clock-table": {"prompt": "Clock frequency targets:\n| Target | Value | Unit |\n|---|---|---|\n| alternate library | 100 | MHz |\n| selected target | 41.7 | MHz |\n",
        "rc": 1, "clock_rates": {"41.7"}},
    "declared-unsigned-modulo": {"prompt": "| Operand interpretation | Contract |\n|---|---|\n| Signed and unsigned | N-bit operands have identical modulo-2^N product bit patterns; the plugin may declare unsigned interpretation before RTL. |\nChosen interpretation: unsigned.\n",
        "tb": _UNSIGNED_TB, "rc": 0, "signed_token": "unsigned"},
    "required-otp": {"prompt": "An OTP map is required.\n| Field | Offset | Width |\n|---|---|---|\n| device_code | 0x10 | 8-bit |\n",
        "rc": 1, "required": {"otp_field"}},
    "required-calibration": {"prompt": "The calibration register trim_code at offset 0x10 is required.\n",
        "rc": 1, "required": {"calibration_field"}},
    "required-ceiling": {"prompt": "The arithmetic result must use rounding mode ceiling.\n",
        "rc": 1, "required": {"rounding_mode"}},
    "diagnostic-before-required-ceiling": {"prompt": "Area/power diagnostic: ceiling is an upper bound.\nThe arithmetic result must use rounding mode ceiling.\n",
        "rc": 1, "required": {"rounding_mode"}},
    "selected-clock": {"prompt": _SELECTED_CLOCK, "rc": 1, "clock_rates": {"41.7"}},
    "mandatory-alternate-clock": {"prompt": "The alternate library must support clock frequency 100 MHz.\n",
        "rc": 1, "clock_rates": {"100"}},
    "mandatory-example-clock": {"prompt": "Example verification must honor clock frequency 95 MHz.\n",
        "rc": 1, "clock_rates": {"95"}},
    "real-reference-clock": {"prompt": "The reference clock frequency is 100 MHz.\n",
        "rc": 1, "clock_rates": {"100"}},
    "diagnostic-real-arithmetic-ceiling": {"prompt": "Area/power diagnostic: the arithmetic result must use rounding mode ceiling.\n",
        "rc": 1, "required": {"rounding_mode"}},
    "modulo-choice-with-independent-signed-requirement": {"prompt": "Signed and unsigned N-bit operands have identical modulo-2^N product bit patterns.\nChosen interpretation: unsigned.\n| Other arithmetic | Requirement |\n|---|---|\n| Signed division | Additional signed division is required. |\n",
        "tb": _UNSIGNED_TB, "rc": 1, "required": {"signedness"}},
    "genuine-signed-arithmetic": {"prompt": "| Arithmetic | Required behavior |\n|---|---|\n| Signed multiplication | Inputs a and b are signed 8-bit two-complement values; the full 16-bit product must represent -2 for -1 times 2. |\n",
        "tb": _UNSIGNED_TB, "rc": 1, "required": {"signedness"}},
    "upstream-mandatory-na-layer": {"prompt": "The calibration register trim_code at offset 0x10 is required.\n",
        "ldoc": _NA_CALIBRATION, "rc": 1, "required": {"calibration_field"},
        "absent_token": "L6_calibration"},
    "typed-real-ceiling-next-to-diagnostic": {"ldoc": {"doc_id": "L3", "doc_name": "numeric",
        "fields": {"rounding_mode": "ceiling", "diagnostic_notes": "Area/power diagnostic: ceiling is an upper bound."}},
        "rc": 1, "required": {"rounding_mode"}},
    "plain-independent-signed-topic": {"prompt": "Signed and unsigned N-bit operands have identical modulo-2^N product bit patterns.\nChosen interpretation: unsigned.\nThe independent comparison path uses signed arithmetic.\n",
        "tb": _UNSIGNED_TB, "rc": 0, "uncovered_signed": False},
    "structural-independent-signed-path": {"prompt": "Signed and unsigned N-bit operands have identical modulo-2^N product bit patterns.\nChosen interpretation: unsigned.\n| Independent path | Behavior |\n|---|---|\n| comparison | The independent comparison path uses signed arithmetic. |\n",
        "tb": _UNSIGNED_TB, "rc": 1, "required": {"signedness"}, "uncovered_signed": True},
    "typed-selected-and-reference-clocks": {"ldoc": {"doc_id": "L7", "doc_name": "verification",
        "fields": {"selected_clock_requirement": "Selected target gf180mcuD: required clock frequency 41.7 MHz (24 ns).",
                   "alternative_library_reference": "Alternate sky130A libraries have clock frequency 100 MHz and 125 MHz.",
                   "reference_fmax": "Estimated alternate-technology reference Fmax: 250 MHz.",
                   "clock_example": "The clock target example for 10 ns is 95 MHz; the formula depends on the selected target."}},
        "rc": 1, "clock_rates": {"41.7"}},
}


@pytest.mark.parametrize("label", _PUBLIC_CONTEXT_CASES)
def test_public_3088_context_reaches_actual_cli(tmp_path, label):
    case = _PUBLIC_CONTEXT_CASES[label]
    rtl = tmp_path / "neutral.v"
    rtl.write_text(_UNSIGNED_RTL)
    report = tmp_path / "coverage.json"
    argv = [sys.executable, str(PROGRAMS / "spec_coverage_check.py"),
            "--rtl", str(rtl), "--strict", "--json", str(report)]
    if "prompt" in case:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(case["prompt"])
        argv += ["--prompt", str(prompt)]
    if "ldoc" in case:
        ldocs = tmp_path / "ldocs"
        ldocs.mkdir()
        (ldocs / "L6.json").write_text(json.dumps(case["ldoc"]) + "\n")
        argv += ["--ldocs", str(ldocs)]
    if "tb" in case:
        tb = tmp_path / "tb.sv"
        tb.write_text(case["tb"])
        argv += ["--tb", str(tb)]
    actual = subprocess.run(argv, capture_output=True, text=True)
    doc = json.loads(report.read_text())
    assert actual.returncode == case["rc"], (label, actual.stdout, actual.stderr, doc)
    kinds = {item["kind"] for item in doc["items"]}
    assert not kinds.intersection(case.get("absent", set())), (label, doc)
    for kind in case.get("required", set()):
        assert any(item["kind"] == kind and item["block_eligible"] and not item["covered"]
                   for item in doc["items"]), (label, doc)
    if "clock_rates" in case:
        clocks = [item for item in doc["items"] if item["kind"] == "clock_frequency"]
        assert {re.search(r"\b(\d+(?:\.\d+)?)\s+MHz\b", item["requirement"]).group(1)
                for item in clocks} == case["clock_rates"], (label, doc)
        assert all(item["block_eligible"] and not item["covered"] for item in clocks), (label, doc)
    if "signed_token" in case:
        signed = [item for item in doc["items"] if item["kind"] == "signedness"]
        assert len(signed) == 1 and signed[0]["coverage_tokens"] == [case["signed_token"]], (label, doc)
        assert signed[0]["covered"] is True, (label, doc)
    if "absent_token" in case:
        assert all(case["absent_token"] not in item["coverage_tokens"] for item in doc["items"]), (label, doc)
    if "uncovered_signed" in case:
        signed = [item for item in doc["items"] if item["kind"] == "signedness"]
        assert len(signed) == 1 and signed[0]["coverage_tokens"][0].lower() == "signed", (label, doc)
        assert signed[0]["covered"] is False, (label, doc)
        assert signed[0]["block_eligible"] is case["uncovered_signed"], (label, doc)


_NATIVE_SCHEMA_CASES = json.loads((Path(__file__).parent / "fixtures" /
    "issue3088_native_schema.json").read_text())["cases"]


@pytest.mark.parametrize("label", _NATIVE_SCHEMA_CASES)
def test_public_3088_native_schema_reaches_actual_cli(tmp_path, label):
    """Public native field shapes retain context before obligation attribution."""
    case = _NATIVE_SCHEMA_CASES[label]
    rtl = tmp_path / "neutral.v"
    rtl.write_text(_UNSIGNED_RTL)
    ldocs = tmp_path / "ldocs"
    ldocs.mkdir()
    (ldocs / "L7.json").write_text(json.dumps(case["ldoc"]) + "\n")
    report = tmp_path / "coverage.json"
    argv = [sys.executable, str(PROGRAMS / "spec_coverage_check.py"),
            "--ldocs", str(ldocs), "--rtl", str(rtl),
            "--strict", "--json", str(report)]
    if "prompt" in case:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(case["prompt"])
        argv += ["--prompt", str(prompt)]
    if case.get("tb"):
        tb = tmp_path / "tb.sv"
        tb.write_text(_UNSIGNED_TB)
        argv += ["--tb", str(tb)]
    actual = subprocess.run(argv, capture_output=True, text=True)
    (tmp_path / "CLI_RECEIPT.json").write_text(json.dumps({
        "argv": argv, "exit_code": actual.returncode,
        "stdout": actual.stdout, "stderr": actual.stderr,
    }, indent=2) + "\n")
    doc = json.loads(report.read_text())
    assert actual.returncode == case["rc"], (label, actual.stdout, actual.stderr, doc)
    kinds = {item["kind"] for item in doc["items"]}
    assert not kinds.intersection(case.get("absent", [])), (label, doc)
    for kind in case.get("required", []):
        assert any(item["kind"] == kind and item["block_eligible"] and not item["covered"]
                   for item in doc["items"]), (label, doc)
    if "clock_rates" in case:
        clocks = [item for item in doc["items"] if item["kind"] == "clock_frequency"]
        assert {re.search(r"\b(\d+(?:\.\d+)?)\s+MHz\b", item["requirement"]).group(1)
                for item in clocks} == set(case["clock_rates"]), (label, doc)
        assert all(item["block_eligible"] and not item["covered"] for item in clocks), (label, doc)
    if "signed_token" in case:
        signed = [item for item in doc["items"] if item["kind"] == "signedness"]
        assert len(signed) == 1 and signed[0]["coverage_tokens"] == [case["signed_token"]], (label, doc)
        assert signed[0]["covered"] is True, (label, doc)
    if "absent_token" in case:
        assert all(case["absent_token"] not in item["coverage_tokens"]
                   for item in doc["items"]), (label, doc)
    for token in case.get("required_tokens", []):
        assert any(token in item["coverage_tokens"] and item["block_eligible"]
                   and not item["covered"] for item in doc["items"]), (label, doc)

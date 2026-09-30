"""Real producers -> production RTL-review binding; no invented populations.

The fixtures are neutral RTL, not benchmark inputs. Negative arms deliberately
mutate genuine receipts, source bytes, census membership or the independent
consumer subject. The legacy consumer projection uses the same CLI/YAML API,
so its red arm is an observed NOT_MEASURED result, not a missing new symbol.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ._hostpaths import require_repo

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
PRODUCERS = Path(os.environ.get("RTL_AUDIT_PRODUCERS_UNDER_TEST", PROGRAMS))
CHECKER = Path(os.environ.get("RTL_AUDIT_CONSUMER_UNDER_TEST",
                              PLUGIN / "_shared" / "skill_compliance_check.py"))
SPEC = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic",
                    "skills", "rtl-review", "compliance.yaml")
NAMED = ("X_interface_encoding_audit", "X_crc_bitorder_check", "X_phy_counter_audit")
FILENAMES = ("encoding_audit_report.json", "crc_bitorder_report.json",
             "phy_counter_audit_report.json")
REVIEW = """# Synthetic RTL review
## Summary
Reviewed the neutral fixture, with no fabricated RTL post-check header.
## Findings
Interface encoding consistency was checked across module boundaries.
TX data content and byte layout were checked against the protocol spec.
The counter approach was checked: time-based, not bus-sampling.
## Next step
Next: run /vibe-ic-phase2
"""
SCALAR = """module storage(input wire clk, reset, enable, sample,
                      output reg held, output wire pulse);
always @(posedge clk or posedge reset) begin
    if (reset) held <= 1'b0;
    else if (enable) held <= sample;
end
assign pulse = sample ^ held;
endmodule
"""
WIDE = """module storage(input wire clk, input wire [7:0] sample,
                      output reg [7:0] held);
always @(posedge clk) held <= sample;
endmodule
"""


def _source(tmp_path, text=SCALAR):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    source = rtl / "source.sv"
    source.write_text(text)
    return rtl, source


def _produce(tmp_path, text=SCALAR, crc_signal=None):
    rtl, source = _source(tmp_path, text)
    evidence = tmp_path / "receipts"
    evidence.mkdir()
    commands = [
        ["interface_encoding_audit.py", "--rtl-dir", str(rtl), "--top-module", "storage"],
        ["crc_bitorder_check.py", "--rtl-files", str(source)],
        ["phy_counter_audit.py", "--rtl-files", str(source)],
    ]
    if crc_signal is not None:
        commands[1].extend(["--crc-signal", crc_signal])
    runs = []
    for command in commands:
        runs.append(subprocess.run(
            [sys.executable, str(PRODUCERS / command[0]), *command[1:],
             "--out-dir", str(evidence)], text=True, capture_output=True))
    return rtl, source, evidence, runs


def _consume(tmp_path, evidence, root, *, use_cli_root=False):
    report = tmp_path / "review.md"
    report.write_text(REVIEW)
    requirements = tmp_path / "requirements.yaml"
    text = SPEC.read_text()
    if root is not None and not use_cli_root:
        for auditor in ("interface_encoding_audit", "crc_bitorder_check", "phy_counter_audit"):
            text = text.replace(f"auditor: {auditor}\n",
                                f"auditor: {auditor}\n    rtl_source_root: {root}\n")
    requirements.write_text(text)
    output = tmp_path / "compliance.json"
    command = [sys.executable, str(CHECKER), "--requirements", str(requirements),
               "--evidence-dir", str(evidence), "--json", str(output), str(report)]
    if root is not None and use_cli_root:
        command.extend(["--rtl-source-root", str(root)])
    run = subprocess.run(command, text=True, capture_output=True)
    return run, json.loads(output.read_text())


def _states(data):
    return {f["id"]: (f["severity"], f["state"])
            for f in data["findings"] if f["id"] in NAMED}


def _mutate(path, callback):
    payload = json.loads(path.read_text())
    callback(payload)
    path.write_text(json.dumps(payload))


def test_closed_scalar_storage_is_not_applicable_not_three_passes(tmp_path):
    rtl, _, receipts, runs = _produce(tmp_path)
    assert [r.returncode for r in runs] == [0, 0, 0], [r.stdout + r.stderr for r in runs]
    run, data = _consume(tmp_path, receipts, rtl)
    assert run.returncode == 0
    assert _states(data) == {cid: ("INFO", "NOT_APPLICABLE") for cid in NAMED}
    assert data["not_measured"] == []
    assert data["verdict"] == "PASS"
    assert run.returncode == 0
    for filename in FILENAMES:
        receipt = json.loads((receipts / filename).read_text())
        assert receipt["applicability"]["structural_absence"]["scanned"] == 1
        assert receipt["applicability"]["source"]["files"][0]["size_bytes"] > 0


def test_wide_external_capture_remains_crc_unknown_not_absent(tmp_path):
    rtl, _, receipts, runs = _produce(tmp_path, WIDE)
    assert runs[1].returncode == 2
    run, data = _consume(tmp_path, receipts, rtl)
    assert _states(data) == {
        NAMED[0]: ("INFO", "NOT_APPLICABLE"),
        NAMED[1]: ("FAIL", "NOT_MEASURED"),
        NAMED[2]: ("INFO", "NOT_APPLICABLE"),
    }
    assert data["verdict"] == "FAIL" and run.returncode == 1


def test_consumer_must_independently_select_the_source(tmp_path):
    _, _, receipts, _ = _produce(tmp_path)
    run, data = _consume(tmp_path, receipts, None)
    assert _states(data) == {cid: ("FAIL", "NOT_MEASURED") for cid in NAMED}
    assert run.returncode == 1


@pytest.mark.parametrize("mutation", ["source_bytes", "added_source", "deleted_source", "wrong_root"])
def test_stale_or_incomplete_or_unrelated_subject_stays_blocking(tmp_path, mutation):
    rtl, source, receipts, _ = _produce(tmp_path)
    selected = rtl
    if mutation == "source_bytes":
        source.write_text(SCALAR.replace("sample ^ held", "sample & held"))
    elif mutation == "added_source":
        (rtl / "omitted.sv").write_text("module omitted(); endmodule\n")
    elif mutation == "deleted_source":
        source.unlink()
    else:
        selected = tmp_path / "different_source"
        selected.mkdir()
        (selected / "source.sv").write_text(SCALAR)
    run, data = _consume(tmp_path, receipts, selected)
    assert _states(data) == {cid: ("FAIL", "NOT_MEASURED") for cid in NAMED}
    assert run.returncode == 1


@pytest.mark.parametrize("filename", FILENAMES)
@pytest.mark.parametrize("mutation", ["forged_census", "malformed_claim", "wrong_auditor", "missing_receipt"])
def test_one_corrupt_or_missing_receipt_blocks_exactly_that_obligation(tmp_path, filename, mutation):
    rtl, _, receipts, _ = _produce(tmp_path)
    target = receipts / filename
    if mutation == "missing_receipt":
        target.unlink()
    elif mutation == "forged_census":
        _mutate(target, lambda d: d["applicability"]["structural_absence"].update(scanned=400))
    elif mutation == "wrong_auditor":
        _mutate(target, lambda d: d["applicability"].update(auditor="unrelated_auditor"))
    else:
        _mutate(target, lambda d: d["applicability"].update(source=None))
    run, data = _consume(tmp_path, receipts, rtl)
    bad = NAMED[FILENAMES.index(filename)]
    assert _states(data) == {cid: (("FAIL", "NOT_MEASURED") if cid == bad
                                  else ("INFO", "NOT_APPLICABLE")) for cid in NAMED}
    assert run.returncode == 1


@pytest.mark.parametrize("body", [
    "always @(posedge clk) value <= value + 1'b1;",
    "always @(posedge clk) case(value) 0: value <= 1; 1: value <= 2; default: value <= 0; endcase",
    "always @(posedge clk) if (value == 0) value <= 1; else value <= 0;",
])
def test_renamed_and_case_encoded_state_recurrence_is_not_counter_absence(tmp_path, body):
    text = "module storage(input clk, output reg [1:0] value);\n" + body + "\nendmodule\n"
    rtl, _, receipts, _ = _produce(tmp_path, text)
    run, data = _consume(tmp_path, receipts, rtl)
    assert _states(data)[NAMED[2]] == ("FAIL", "NOT_MEASURED")
    assert run.returncode == 1


@pytest.mark.parametrize("text", [
    "module storage(input clk, output value); missing child(.clk(clk), .value(value)); endmodule",
    "(* blackbox *) module storage(input clk, output value); endmodule",
    "module storage #(parameter WIDTH = 1)(input [WIDTH-1:0] sample, output [WIDTH-1:0] value); assign value = sample; endmodule",
    "`include \"missing.svh\"\nmodule storage(); endmodule",
    "module storage(input clk); /* unterminated",
    "module storage(input clk); wire [7:0] memory [0:3]; endmodule",
])
def test_unsupported_or_unresolved_source_never_becomes_not_applicable(tmp_path, text):
    rtl, _, receipts, _ = _produce(tmp_path, text)
    run, data = _consume(tmp_path, receipts, rtl)
    assert all(state != "NOT_APPLICABLE" for _, state in _states(data).values())
    assert run.returncode == 1


def test_found_crc_with_bad_order_is_still_an_applicable_failure(tmp_path):
    text = """module storage(input clk, input [7:0] checksum, output reg [7:0] word);
always @(posedge clk) word <= {checksum[3:0], checksum[7:4]};
endmodule
"""
    rtl, _, receipts, runs = _produce(tmp_path, text, crc_signal="checksum")
    assert runs[1].returncode == 1
    run, data = _consume(tmp_path, receipts, rtl)
    assert _states(data)[NAMED[1]] == ("FAIL", "FAIL")
    assert run.returncode == 1


def test_declared_but_unmeasured_crc_signal_is_not_structural_absence(tmp_path):
    rtl, _, receipts, _ = _produce(tmp_path, crc_signal="unknown_signal")
    run, data = _consume(tmp_path, receipts, rtl)
    assert _states(data)[NAMED[1]] == ("FAIL", "NOT_MEASURED")
    assert run.returncode == 1


def test_cli_selected_source_remeasures_instead_of_trusting_receipt(tmp_path):
    rtl, _, receipts, _ = _produce(tmp_path)
    run, data = _consume(tmp_path, receipts, rtl, use_cli_root=True)
    assert _states(data) == {cid: ("INFO", "NOT_APPLICABLE") for cid in NAMED}
    assert set(data["not_applicable"]) == set(NAMED)
    assert run.returncode == 0


@pytest.mark.parametrize("bad_auditor", [None, *NAMED])
def test_actual_applicable_good_and_bad_receipts_keep_their_measured_verdicts(tmp_path, bad_auditor):
    """Run all original producer contracts and the real compliance consumer.

    Every obligation has an actual population, so this is neither an absence
    test nor a fabricated JSON PASS/FAIL receipt. The same neutral vectors run
    against the fixed old producers/consumer via the environment path selectors.
    """
    comparison = "6'b11_0000" if bad_auditor == NAMED[0] else "6'd32"
    load = "{checksum[3:0], checksum[7:4]}" if bad_auditor == NAMED[1] else "checksum"
    condition = "enable && bus_in" if bad_auditor == NAMED[2] else "enable"
    text = f"""module source_unit(input clk, output reg [5:0] value);
always @(posedge clk) value <= value + 1'b1;
endmodule
module sink_unit(input clk, input [5:0] value, output reg indication);
always @(posedge clk) begin
  if (value == {comparison}) indication <= 1'b1;
  else indication <= 1'b0;
end
endmodule
module storage(input clk, enable, bus_in, input [7:0] checksum,
               output reg [7:0] word, output indication);
wire [5:0] transfer;
reg [7:0] low_cnt;
source_unit source(.clk(clk), .value(transfer));
sink_unit sink(.clk(clk), .value(transfer), .indication(indication));
always @(posedge clk) word <= {load};
always @(posedge clk) begin
  if ({condition}) low_cnt <= low_cnt + 1'b1;
end
endmodule
"""
    rtl, _, receipts, runs = _produce(tmp_path, text, crc_signal="checksum")
    run, data = _consume(tmp_path, receipts, rtl)
    assert _states(data) == {
        cid: (("FAIL", "FAIL") if cid == bad_auditor else ("INFO", "PASS"))
        for cid in NAMED
    }
    assert [r.returncode for r in runs] == [1 if cid == bad_auditor else 0 for cid in NAMED]
    assert run.returncode == (1 if bad_auditor else 0)
    assert data["verdict"] == ("FAIL" if bad_auditor else "PASS")


def test_source_change_during_actual_elaboration_cannot_bind_old_bytes(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROGRAMS))
    import _rtl_audit_applicability as applicability
    rtl, source = _source(tmp_path)
    original = applicability._elaborate
    def changing_elaborator(files, **kwargs):
        measured = original(files, **kwargs)
        source.write_text(SCALAR.replace("sample ^ held", "sample & held"))
        return measured
    monkeypatch.setattr(applicability, "_elaborate", changing_elaborator)
    result = applicability.assess("phy_counter_audit", rtl)
    assert result["state"] == "UNKNOWN"
    assert result["reason"] == "source_changed_during_elaboration"


@pytest.mark.parametrize("declaration", [
    "integer unobserved [0:3];",
    "typedef logic [7:0] element_t; element_t unobserved [0:3];",
])
def test_unoptimized_declaration_census_cannot_lose_unused_storage(tmp_path, declaration):
    text = "module storage(input clk, output held);\n" + declaration + "\nassign held=clk; endmodule\n"
    rtl, _, receipts, _ = _produce(tmp_path, text)
    run, data = _consume(tmp_path, receipts, rtl)
    assert _states(data) == {cid: ("FAIL", "NOT_MEASURED") for cid in NAMED}
    assert run.returncode == 1


def test_source_change_before_snapshot_is_unknown(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROGRAMS))
    import _rtl_audit_applicability as applicability
    rtl, source = _source(tmp_path)
    original = applicability._elaborate
    def changing_elaborator(files, **kwargs):
        source.write_text(SCALAR.replace('sample ^ held', 'sample & held'))
        return original(files, **kwargs)
    monkeypatch.setattr(applicability, '_elaborate', changing_elaborator)
    result = applicability.assess('phy_counter_audit', rtl)
    assert result['state'] == 'UNKNOWN'
    assert result['reason'] == 'source_changed_before_elaboration'

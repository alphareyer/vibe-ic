"""Native CLI controls distinguish active reset definedness from inactive undef.

These are prompt-derived generic circuits, not a benchmark implementation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROGRAMS = Path(__file__).resolve().parents[1]
ORIGINAL_SHA256 = "8fa104cb5f331bdb85bc3416a9b927f583b0a61bf670875cd2d0190d2bc0ca6d"


@pytest.mark.parametrize("declared,reset_value,data_value,expected,classification", [
    ("active_high", "1'b0", "1'bx", "PASS", "RESET_FORCE"),
    ("active_low", "1'b0", "1'bx", "REFUTED", "RESET_FORCE"),
    ("active_high", "1'bx", "1'b0", "NOT_DISCHARGED", "UNSUPPORTED"),
], ids=["defined-reset", "wrong-polarity", "undefined-reset"])
def test_normal_cli_keeps_reset_force_and_undef_in_their_actual_domains(
        tmp_path, declared, reset_value, data_value, expected, classification):
    assert shutil.which("yosys"), "Native Yosys is required for this regression."
    rtl = tmp_path / "generic.v"
    rtl.write_text("module m(input clk,rst,en,output reg q);\n"
                   f" always @(posedge clk) if(rst) q<={reset_value}; else if(en) q<={data_value};\n"
                   "endmodule\n")
    input_sha256 = hashlib.sha256(rtl.read_bytes()).hexdigest()
    if reset_value == "1'b0":
        assert rtl.stat().st_size == 112
        assert input_sha256 == ORIGINAL_SHA256
    output = tmp_path / "result.json"
    command = [sys.executable, "-B", str(PROGRAMS / "formal_structural_check.py"),
               "--rtl", str(rtl), "--top", "m", "--claim",
               "reset_polarity:rst:" + declared, "--work-dir", str(tmp_path / "work"),
               "--json", str(output)]
    process = subprocess.run(command, text=True, capture_output=True, check=False)
    (tmp_path / "cli.stdout").write_text(process.stdout)
    (tmp_path / "cli.stderr").write_text(process.stderr)
    record = json.loads(output.read_text())
    print("RAW_RESET_DOMAIN_OBSERVATION " + json.dumps({
        "command": command, "rc": process.returncode, "record": record}, sort_keys=True))
    assert (process.returncode, record["verdict"]) == (
        0 if expected == "PASS" else 1, expected), record
    claim = record["claims"][0]
    assert claim["verdict"] == expected, claim
    mapping = claim["reset_control_mapping"]
    assert mapping["source_binding"]["rtl_sha256"] == {str(rtl): input_sha256}, mapping
    assert len(mapping["raw_identities"]) == len(mapping["rows"]) == 1, mapping
    identity, row = mapping["raw_identities"][0], mapping["rows"][0]
    assert identity["literal_undef_in_transition"] is True, identity
    assert len(identity["current_q"]) == 1 and row["q"] == identity["current_q"][0], mapping
    assert (identity.get("reset_independence_certificate") or {}).get("status") != "CERTIFIED", identity
    assert (row.get("reset_independence_certificate") or {}).get("status") != "CERTIFIED", row
    assert row["classification"] == classification, row
    if expected == "NOT_DISCHARGED":
        assert mapping["unresolved"] and not mapping["complete"], mapping
    else:
        assert mapping["complete"] and not mapping["unresolved"], mapping
        assert row["polarity"] == "active_high", row
        assert mapping["state_model"] == "raw_pre_cleanup_current_source", mapping
        proof = row["raw_reset_domain_proof"]
        assert proof["status"] == "CERTIFIED", proof
        assert proof["proof_basis"] == "native_raw_paired_reset_domain", proof
        assert proof["scope"] == "one_step_shared_current_q", proof
        assert (proof["active_reset_value"], proof["inactive_reset_value"], proof["force_value"]) == (1, 0, 0), proof
        assert proof["active_definedness"] == "NO_UNDEF", proof
        assert proof["inactive_definedness"] == "HAS_UNDEF", proof
        assert proof["queries"] and all(value != "UNKNOWN" for value in proof["queries"].values()), proof

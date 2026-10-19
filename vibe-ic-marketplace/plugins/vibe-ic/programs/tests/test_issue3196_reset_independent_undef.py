"""Native CLI regression for a reset-independent literal-undef transition.

The public issue's generic RTL is used verbatim, including its final newline.
Graph certificates must retain identities and must not invent native SAT arms.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


PROGRAMS = Path(__file__).resolve().parents[1]
RTL = (
    "module m(input clk, rst, en, input [1:0] d, output reg [1:0] q, scratch);\n"
    "  always @(posedge clk) if (rst) q <= 0; else q <= d;\n"
    "  always @(posedge clk) if (en) scratch <= 2'bxx; else scratch <= d;\n"
    "endmodule\n"
)
RTL_SHA256 = "d282b8094f179c44157b74ce3445bea90b35e6c0b91013bc96e1c9789fe0c461"


def test_normal_cli_retains_and_certifies_independent_undef_identities(tmp_path):
    assert shutil.which("yosys"), "Native Yosys is required for this regression."
    rtl = tmp_path / "generic.v"
    rtl.write_bytes(RTL.encode("ascii"))
    assert rtl.stat().st_size == 207
    assert hashlib.sha256(rtl.read_bytes()).hexdigest() == RTL_SHA256
    output = tmp_path / "result.json"
    command = [sys.executable, "-B", str(PROGRAMS / "formal_structural_check.py"),
               "--rtl", str(rtl), "--top", "m", "--claim",
               "reset_polarity:rst:active_high", "--work-dir", str(tmp_path / "work"),
               "--json", str(output)]
    process = subprocess.run(command, text=True, capture_output=True, check=False)
    (tmp_path / "cli.stdout").write_text(process.stdout)
    (tmp_path / "cli.stderr").write_text(process.stderr)
    record = json.loads(output.read_text())
    print("UNDEF_INDEPENDENCE_OBSERVATION " + json.dumps({
        "command": command, "rc": process.returncode, "record": record}, sort_keys=True))
    # Keep the original failure a value failure on the real public CLI consumer.
    assert (process.returncode, record["verdict"]) == (0, "PASS"), record
    claim = record["claims"][0]
    assert claim["verdict"] == "PASS" and claim["signal"] == "rst", claim
    mapping = claim["reset_control_mapping"]
    assert mapping["source_binding"]["rtl_sha256"] == {str(rtl): RTL_SHA256}, mapping
    assert mapping["complete"] and not mapping["unresolved"], mapping
    assert mapping["shared_current_q_and_data"] is True, mapping
    assert mapping["paired_domain_reachable"] is True, mapping
    identities, rows = mapping["raw_identities"], mapping["rows"]
    assert len(identities) == len(rows) == 4, mapping
    assert len({(item["cell"], item["q_index"], item["raw_q"])
                for item in identities}) == 4, identities
    assert all(len(item["current_q"]) == 1 for item in identities), identities
    assert Counter(row["classification"] for row in rows) == {
        "RESET_FORCE": 2, "NO_DEPENDENCE": 2}, rows
    progress = mapping["query_progress"]
    assert progress["planned"] == progress["started"] == progress["completed"] == 59, progress
    assert not progress["unknown"] and not progress["not_started"], progress
    assert mapping["native_exit_code"] == 0, mapping
    assert mapping["native_run"]["termination"] == "COMPLETED", mapping
    undef = [item for item in identities if item["literal_undef_in_transition"]]
    assert len(undef) == 2, identities
    assert sum("reset_independence_certificate" in item for item in identities) == 2, identities
    raw = json.loads((tmp_path / "work" / "formal_structural_netlist_raw.json").read_text())
    raw_module = raw["modules"]["m"]
    raw_module_sha256 = hashlib.sha256(json.dumps(
        raw_module, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    raw_reset_bit = raw_module["ports"]["rst"]["bits"][0]
    certificates = []
    for identity in undef:
        row = next(row for row in rows if row["q"] == identity["current_q"][0])
        certificate = identity["reset_independence_certificate"]
        assert row["reset_independence_certificate"] == certificate, row
        assert row["proof_basis"] == "complete_raw_transition_fanin", row
        assert row["classification"] == "NO_DEPENDENCE", row
        assert row["unsupported"] == "literal-X/undef retained from raw next-state transition", row
        assert row["native_queries"] == {}, row
        assert certificate["status"] == "CERTIFIED", certificate
        assert certificate["scope"] == "one_step_shared_current_q", certificate
        assert certificate["signal"] == "rst", certificate
        assert certificate["raw_cell"] == identity["cell"], certificate
        assert certificate["raw_q"] == identity["raw_q"], certificate
        assert certificate["q_index"] == identity["q_index"], certificate
        assert certificate["current_q"] == identity["current_q"][0], certificate
        raw_cell = raw_module["cells"][identity["cell"]]
        roots = {pin: bits if pin != "D" else [bits[identity["q_index"]]]
                 for pin, bits in raw_cell["connections"].items() if pin != "Q"}
        assert certificate["roots"] == roots, certificate
        assert certificate["raw_reset_bit"] == raw_reset_bit, certificate
        assert certificate["literal_undef_bits"], certificate
        assert set(certificate["literal_undef_bits"]) <= {"x", "z"}, certificate
        assert certificate["visited_cells"], certificate
        assert set(certificate["visited_cells"]) <= set(raw_module["cells"]), certificate
        assert certificate["raw_reset_bit"] not in certificate["primary_input_bits"], certificate
        assert certificate["raw_reset_bit"] not in certificate["current_q_cut_bits"], certificate
        assert certificate["raw_module_sha256"] == raw_module_sha256, certificate
        certificates.append(certificate)
    assert len({item["raw_module_sha256"] for item in certificates}) == 1, certificates
    forced = [row for row in rows if row["classification"] == "RESET_FORCE"]
    assert all(row["native_queries"]["zero1"] == "PROVED" for row in forced), forced
    assert all("reset_independence_certificate" not in row for row in forced), forced

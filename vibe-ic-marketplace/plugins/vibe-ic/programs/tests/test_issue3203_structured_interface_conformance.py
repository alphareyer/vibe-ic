"""PR3203 generic controls on the published canonical typed-interface schema.

The contributor RTL-free document fixture, instance counts and physical-port
controls are retained. Producer assertions use the already-published schema.
"""
import json
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
from phase1_doc_one_shot_runner import gen_l1_datasheet, gen_l9_integration_spec
from _specrtl_common import extract_spec_contract

FIXTURE = (Path(__file__).parent / "fixtures/phase1_local/issue3200_comportable_typed/interfaces.md").read_text()


@pytest.mark.parametrize("instances", [1, 3])
def test_public_typed_bundle_refuses_unbound_physical_conformance(tmp_path, instances):
    docs = {"interfaces.md": FIXTURE.replace("| rsp | 1 |", f"| rsp | {instances} |")}
    l1 = gen_l1_datasheet(tmp_path, docs)
    pins = json.loads(Path(l1.path).read_text())["pin_table"]
    bus = next(pin for pin in pins if pin["name"] == "bus")
    assert bus.get("width") != instances, "STRUCT_BUNDLE_SCALARIZED"
    assert bus["mode"] == "interface" and bus["interface_type"] == "req_rsp"
    assert bus["package_struct"] == "transport_pkg::transport"
    assert bus["instance_count"] == instances
    assert bus["width_unresolved"] is True
    l9 = json.loads(Path(gen_l9_integration_spec(tmp_path, docs, {}).path).read_text())
    for alias in ("ports", "top_ports", "top_module_pins"):
        projected = next(pin for pin in l9[alias] if pin["name"] == "bus")
        assert projected.get("width") is None
        assert projected["package_struct"] == bus["package_struct"]
        assert projected["instance_count"] == bus["instance_count"]
        assert projected["interface_type"] == "req_rsp"
    (tmp_path / "CONFORMANCE_INPUT.json").write_text(json.dumps(l9, indent=2) + "\n")
    with pytest.raises(ValueError, match="UNRESOLVED_STRUCTURED_INTERFACE: bus"):
        extract_spec_contract(json.dumps(l9), is_json=True, confirm=False)


def test_explicit_scalar_and_physical_inout_keep_their_contract(tmp_path):
    docs = {"interfaces.md": """| Signal | Direction | Width | Description |
|:--|:--|--:|:--|
| ready_o | output | 1 | Scalar ready |
| data_io | inout | 8 | Physical bidirectional bus |
"""}
    pins = json.loads(Path(gen_l1_datasheet(tmp_path, docs).path).read_text())["pin_table"]
    ready = next(pin for pin in pins if pin["name"] == "ready_o")
    data = next(pin for pin in pins if pin["name"] == "data_io")
    assert (ready["mode"], ready["width"]) == ("output", 1)
    assert (data["mode"], data["width"]) == ("inout", 8)
    assert all("interface_contract" not in pin and "interface_type" not in pin
               for pin in [ready, data])
    l9 = json.loads(Path(gen_l9_integration_spec(tmp_path, docs, {}).path).read_text())
    contract = extract_spec_contract(json.dumps(l9), is_json=True, confirm=False)
    got = {port.name: (port.direction, port.width) for port in contract.ports}
    assert got["ready_o"] == ("output", 1)
    assert got["data_io"] == ("inout", 8)


def test_normal_conformance_entry_reports_unresolved_without_dut_failure(tmp_path):
    import subprocess

    docs = {"interfaces.md": FIXTURE}
    gen_l1_datasheet(tmp_path, docs)
    l9_path = Path(gen_l9_integration_spec(tmp_path, docs, {}).path)
    rtl = tmp_path / "packet_engine.sv"
    rtl.write_text("module packet_engine(input clk_i, output ready_o); assign ready_o = clk_i; endmodule\n")
    report = tmp_path / "conformance.json"
    result = subprocess.run(
        [sys.executable, str(PROGRAMS / "spec_conformance_check.py"),
         "--spec", str(l9_path), "--json", str(report), str(rtl)],
        text=True, capture_output=True)
    (tmp_path / "CLI_RESULT.json").write_text(json.dumps({
        "returncode": result.returncode, "stdout": result.stdout,
        "stderr": result.stderr}, indent=2) + "\n")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "NOT_MEASURED" in result.stderr
    assert "Traceback" not in result.stderr and "FAIL" not in result.stderr
    findings = json.loads(report.read_text())
    assert len(findings) == 1
    assert findings[0]["severity"] == "INFO"
    assert findings[0]["rule"] == "unresolved-structured-interface"
    assert findings[0]["message"].startswith("NOT_MEASURED: UNRESOLVED_STRUCTURED_INTERFACE: bus")

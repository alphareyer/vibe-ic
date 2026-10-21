"""Issue #3449: source-bound scan-shift timing actuator and consumer."""
from __future__ import annotations

import json
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import dft_signoff_check as SIGN  # noqa: E402
import fault_scan_chain_insert as SCAN  # noqa: E402
import scan_mode_constraint as MODE  # noqa: E402


def _project(tmp_path: Path, frequency=10.0, clock="clk_i") -> Path:
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    chain = {"name": "chain_0", "length": 2, "scan_in": "sin",
             "scan_out": "sout", "clock": clock}
    if frequency is not None:
        chain["frequency_mhz"] = frequency
    (docs / "L20_DFT_SCAN_TOPOLOGY.json").write_text(json.dumps({
        "doc_id": "L20", "applicability": "APPLICABLE",
        "extraction_status": "EXTRACTED",
        "fields": {"dft_present": True, "scan_chains": [chain]},
    }, indent=2) + "\n")
    return tmp_path


def _receipt(project: Path) -> dict:
    binding, problems = SCAN.load_l20_scan_contract(project, "clk_i", 2)
    assert problems == [] and binding is not None
    constraint, problems = MODE.emit(project, binding)
    assert problems == [] and constraint is not None
    report = {
        "clock": "clk_i", "input_flop_count": 2,
        "internal_chain_length": 2,
        "dft_ports": ["sin", "shift", "test", "tck", "sout"],
        "l20_contract": binding,
        "scan_mode_constraint": constraint,
        "published": True,
    }
    report["l20_reconciliation"] = SCAN.reconcile_l20_scan_contract(
        binding, report)
    path = project / "reports/phase2/dft/scan_chain.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def test_ten_mhz_emits_separate_current_100ns_scan_sdc(tmp_path):
    project = _project(tmp_path)
    functional = project / "phase2/stage2/constraints/design.sdc"
    functional.parent.mkdir(parents=True)
    functional.write_bytes(b"create_clock -period 8.0 [get_ports {clk_i}]\n")
    before = functional.read_bytes()
    report = _receipt(project)

    constraint = report["scan_mode_constraint"]
    assert constraint["frequency_mhz"] == "10.0"
    assert constraint["period_ns"] == "100.0"
    scan_sdc = (project / MODE.SCAN_MODE_SDC_REL).read_text()
    assert "-period 100.0 [get_ports {clk_i}]" in scan_sdc
    assert functional.read_bytes() == before
    assert SCAN.l20_binding_is_current(project, report)[0] is True
    assert SIGN.evaluate_scan_shift_timing(project)["status"] == "PASS"


def test_canonical_scan_producer_emits_and_binds_constraint_before_publish(
        tmp_path, monkeypatch):
    project = _project(tmp_path)
    netlist = project / "phase2/stage2/synth/mapped.v"
    netlist.parent.mkdir(parents=True)
    mapped = (
        "module top(input clk_i, input d, output q);\n"
        "sky130_fd_sc_hd__dfxtp_1 u0 (.CLK(clk_i), .D(d), .Q(q));\n"
        "sky130_fd_sc_hd__dfxtp_1 u1 (.CLK(clk_i), .D(d), .Q(q));\n"
        "endmodule\n")
    netlist.write_text(mapped)

    def native(project_path, cmd, **kwargs):
        meta = json.dumps({"sin": "sin", "sout": "sout", "shift": "shift",
                           "internalCount": 2, "boundaryCount": 0,
                           "order": []})
        produced = project_path / "phase2/stage2/dft/scan_chain_work/chained.v"
        produced.parent.mkdir(parents=True, exist_ok=True)
        produced.write_text(
            f"/* FAULT METADATA: '{meta}' END FAULT METADATA */\n" + mapped)
        return 0, (
            "Internal scan chain successfully constructed. Length: 2\n"
            "Boundary scan cells successfully chained. Length: 0\n"
            "Total scan-chain length: 2\n"), ""

    monkeypatch.setattr(SCAN._fatpg, "_run_docker", native)
    monkeypatch.setattr(SCAN._fatpg, "atpg_engine_identity",
                        lambda: {"exec_route": "test", "image": None})
    rc, report = SCAN.run_chain(
        project, "phase2/stage2/synth/mapped.v", "clk_i", "sky130",
        require_l20_contract=True)
    assert rc == 0, report
    assert report["published"] is True
    assert report["scan_mode_constraint"]["period_ns"] == "100.0"
    assert (project / MODE.SCAN_MODE_SDC_REL).is_file()
    assert SCAN.l20_binding_is_current(project, report)[0] is True


def test_changed_frequency_invalidates_saved_receipt_and_signoff(tmp_path):
    project = _project(tmp_path)
    report = _receipt(project)
    l20 = project / report["l20_contract"]["path"]
    data = json.loads(l20.read_text())
    data["fields"]["scan_chains"][0]["frequency_mhz"] = 20.0
    l20.write_text(json.dumps(data, indent=2) + "\n")
    assert SCAN.l20_binding_is_current(project, report)[0] is False
    assert SIGN.evaluate_scan_shift_timing(project)["status"] == "FAIL"


def test_missing_or_changed_scan_sdc_blocks_signoff(tmp_path):
    for mutation in ("missing", "changed"):
        project = _project(tmp_path / mutation)
        report = _receipt(project)
        sdc = project / MODE.SCAN_MODE_SDC_REL
        if mutation == "missing":
            sdc.unlink()
        else:
            sdc.write_text(sdc.read_text().replace("100.0", "50.0"))
        assert SCAN.l20_binding_is_current(project, report)[0] is False
        assert SIGN.evaluate_scan_shift_timing(project)["status"] == "FAIL"


def test_changed_measured_frequency_receipt_is_rejected(tmp_path):
    project = _project(tmp_path)
    report = _receipt(project)
    report["scan_mode_constraint"]["frequency_mhz"] = "5.0"
    measured = SCAN.reconcile_l20_scan_contract(
        report["l20_contract"], report)
    assert measured["ok"] is False


def test_no_declared_frequency_is_an_explicit_signoff_skip(tmp_path):
    project = _project(tmp_path, frequency=None)
    assert SIGN.evaluate_scan_shift_timing(project)["status"] == "SKIP"


def test_successful_no_frequency_publish_removes_stale_owned_sdc(
        tmp_path, monkeypatch):
    project = _project(tmp_path, frequency=None)
    netlist = project / "phase2/stage2/synth/mapped.v"
    netlist.parent.mkdir(parents=True)
    mapped = (
        "module top(input clk_i, input d, output q);\n"
        "sky130_fd_sc_hd__dfxtp_1 u0 (.CLK(clk_i), .D(d), .Q(q));\n"
        "sky130_fd_sc_hd__dfxtp_1 u1 (.CLK(clk_i), .D(d), .Q(q));\n"
        "endmodule\n")
    netlist.write_text(mapped)
    stale = project / MODE.SCAN_MODE_SDC_REL
    stale.parent.mkdir(parents=True)
    stale.write_text("stale preceding run\n")

    def native(project_path, cmd, **kwargs):
        meta = json.dumps({"sin": "sin", "sout": "sout", "shift": "shift",
                           "internalCount": 2, "boundaryCount": 0,
                           "order": []})
        produced = project_path / "phase2/stage2/dft/scan_chain_work/chained.v"
        produced.parent.mkdir(parents=True, exist_ok=True)
        produced.write_text(
            f"/* FAULT METADATA: '{meta}' END FAULT METADATA */\n" + mapped)
        return 0, (
            "Internal scan chain successfully constructed. Length: 2\n"
            "Boundary scan cells successfully chained. Length: 0\n"
            "Total scan-chain length: 2\n"), ""

    monkeypatch.setattr(SCAN._fatpg, "_run_docker", native)
    monkeypatch.setattr(SCAN._fatpg, "atpg_engine_identity",
                        lambda: {"exec_route": "test", "image": None})
    rc, report = SCAN.run_chain(
        project, "phase2/stage2/synth/mapped.v", "clk_i", "sky130",
        require_l20_contract=True)
    assert rc == 0 and report["published"] is True
    assert not stale.exists()


def test_failed_chain_does_not_replace_prior_canonical_scan_sdc(
        tmp_path, monkeypatch):
    project = _project(tmp_path)
    netlist = project / "phase2/stage2/synth/mapped.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text(
        "module top(input clk_i, input d, output q);\n"
        "sky130_fd_sc_hd__dfxtp_1 u0 (.CLK(clk_i), .D(d), .Q(q));\n"
        "sky130_fd_sc_hd__dfxtp_1 u1 (.CLK(clk_i), .D(d), .Q(q));\n"
        "endmodule\n")
    prior = project / MODE.SCAN_MODE_SDC_REL
    prior.parent.mkdir(parents=True)
    prior.write_bytes(b"prior successful canonical bytes\n")

    monkeypatch.setattr(SCAN._fatpg, "_run_docker",
                        lambda *args, **kwargs: (1, "", "native failed"))
    rc, report = SCAN.run_chain(
        project, "phase2/stage2/synth/mapped.v", "clk_i", "sky130",
        require_l20_contract=True)
    assert rc != 0
    assert report.get("published") is not True
    assert prior.read_bytes() == b"prior successful canonical bytes\n"


def test_nonpositive_or_nonfinite_frequency_is_refused(tmp_path):
    for value in (0, -1, "nan", "inf"):
        project = _project(tmp_path / str(value), frequency=value)
        binding, problems = SCAN.load_l20_scan_contract(project, "clk_i", 2)
        assert binding is None
        assert "finite and positive" in "; ".join(problems)

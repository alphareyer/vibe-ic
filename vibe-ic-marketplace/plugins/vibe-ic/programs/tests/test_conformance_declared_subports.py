"""An input-delegated port group uses its authored declaration's names."""

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
CHECKER = PROGRAMS / "spec_conformance_check.py"
DELEGATION = (
    "### SRAM port group sub-ports (typical)\n\n"
    "The concrete signal names for this port group are declared by the "
    "plugin in declaration.json.\n\n"
    "| Sub-port | Direction |\n|---|---|\n"
    "| `o_sram_addr` | output |\n"
    "| `o_sram_we` | output |\n"
)
L9_GROUP = [{"group": "sram", "authority": "plugin_output/declaration.json",
             "example_ports": ["o_sram_addr", "o_sram_we"],
             "source_document": "L3_external_interface.md"}]


def _project(tmp_path, *, delegation=True, extra="o_sram_waddr"):
    project = tmp_path / "design"
    rtl_dir = project / "phase2/stage1/rtl"
    rtl_dir.mkdir(parents=True)
    (project / "input/docs").mkdir(parents=True)
    (project / "input/docs/L3_external_interface.md").write_text(DELEGATION)
    (project / "plugin_output").mkdir()
    (project / "plugin_output/declaration.json").write_text(json.dumps({
        "sram_interface": {"read_address": "o_sram_addr",
                           "write_enable": "o_sram_we",
                           "write_address": "o_sram_waddr"}}))
    gd = project / "phase1/generated_docs"
    gd.mkdir(parents=True)
    l9_ports = [
        {"name": "o_sram_addr", "direction": "output", "width": 10},
        {"name": "o_sram_we", "direction": "output", "width": 1}]
    l9 = {"top_module": "dut", "ports": l9_ports, "top_ports": l9_ports,
        "plugin_declared_port_groups": L9_GROUP if delegation else []}
    (gd / "L9_INTEGRATION_SPEC.json").write_text(json.dumps(l9))
    ports = "output [9:0] o_sram_addr, output o_sram_we"
    if extra:
        ports += f", output [9:0] {extra}"
    (rtl_dir / "dut.v").write_text(f"module dut({ports}); endmodule\n")
    return project, gd / "L9_INTEGRATION_SPEC.json", rtl_dir


def _check(project, spec, rtl_dir):
    report = project / "findings.json"
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--spec", str(spec),
         "--rtl-dir", str(rtl_dir), "--json", str(report)],
        capture_output=True, text=True, check=False)
    assert report.is_file(), result.stderr
    return result, json.loads(report.read_text())


def _errors(findings):
    return {(row["rule"], row["symbol"]) for row in findings
            if row["severity"] == "ERROR"}


def test_declared_subport_is_accepted_only_with_structured_delegation(tmp_path):
    project, spec, rtl = _project(tmp_path)
    result, findings = _check(project, spec, rtl)
    assert ("port-extra", "o_sram_waddr") not in _errors(findings)
    assert result.returncode == 0, findings


def test_undeclared_extra_remains_port_extra(tmp_path):
    project, spec, rtl = _project(tmp_path, extra="o_foo")
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_foo") in _errors(findings)
    assert ("port-declared-missing", "o_sram_waddr") in _errors(findings)


def test_declaration_alone_does_not_authorize_extra(tmp_path):
    project, spec, rtl = _project(tmp_path, delegation=False)
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)


def test_declared_port_absent_from_rtl_is_reported(tmp_path):
    project, spec, rtl = _project(tmp_path, extra=None)
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-declared-missing", "o_sram_waddr") in _errors(findings)


def test_phase1_extracts_only_an_affirmative_delegation(tmp_path):
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups
    import phase1_doc_one_shot_runner as phase1

    extracted = {"L3_external_interface.md": DELEGATION}
    assert groups.extract_delegated_groups(extracted) == L9_GROUP
    (tmp_path / "input/docs").mkdir(parents=True)
    (tmp_path / "input/docs/L3_external_interface.md").write_text(DELEGATION)
    phase1.gen_l9_integration_spec(tmp_path, extracted, {})
    emitted = json.loads((tmp_path / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").read_text())
    assert emitted["plugin_declared_port_groups"] == L9_GROUP
    denied = DELEGATION.replace("are declared", "are not declared")
    assert groups.extract_delegated_groups(
        {"L3_external_interface.md": denied}) == []


def test_full_stack_tb_resolves_the_same_declared_interface(tmp_path):
    sys.path.insert(0, str(PROGRAMS))
    import design_one_shot_runner as runner

    project, _, _ = _project(tmp_path)
    gd = project / "phase1/generated_docs"
    (gd / "L3_CMD_PROTOCOL.json").write_text(json.dumps({
        "no_opcodes_in_input": True, "opcodes": []}))
    result = runner.step_full_stack_tb_gen(project, "dut")
    assert "L9.top_ports diverged" not in result.detail
    tb = (project / "phase2/stage1/sim_full_stack/tb_dut_full.v").read_text()
    assert ".o_sram_waddr(o_sram_waddr)" in tb


def test_full_stack_tb_names_a_missing_declared_port(tmp_path):
    sys.path.insert(0, str(PROGRAMS))
    import design_one_shot_runner as runner

    project, _, _ = _project(tmp_path, extra=None)
    result = runner.step_full_stack_tb_gen(project, "dut")
    assert result.status == "FAIL"
    assert "RTL omits plugin-declared port(s): o_sram_waddr" in result.detail

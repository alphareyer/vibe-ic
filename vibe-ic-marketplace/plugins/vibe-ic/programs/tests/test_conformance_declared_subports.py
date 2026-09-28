"""An input-delegated port group uses its authored declaration's names."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

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

NO_DELEGATION = (
    "The ports are fixed by this table; declaration.json records unrelated plugin metadata.",
    "The table defines the port names. declaration.json records plugin settings.",
    "declaration.json records plugin metadata about ports; this table fixes their names.",
    "The table, not declaration.json, determines the signal names for this port group.",
    "declaration.json does not define the signal names for this port group.",
    "The debug interface port names are defined by declaration.json.",
    "The debug interface port names are defined by declaration.json; "
    "SRAM names are fixed by the table below.",
    "The concrete signal names for this port group are declared by the plugin "
    "in declaration.json. SRAM names are fixed by the table below.",
)
AUTHORITY_SENTENCE = (
    "declaration.json defines the concrete signal names for this port group.",
    "The actual port names for this group come from declaration.json.",
    "此 port group 的訊號名稱由 Plugin 在 declaration.json 宣告。",
    "The SRAM interface port names are defined by declaration.json.",
    "In declaration.json live the port names for this interface.",
)

OTHER_GROUP_SENTENCE = (
    "The port names of the debug interface are defined by declaration.json.",
    "The port names for the debug interface come from declaration.json.",
    "In declaration.json live the port names for the debug interface.",
    "The debug interface has its port names in declaration.json.",
    "The debug interface port names and SRAM port names are in declaration.json.",
    "The port names for o_debug_req are in declaration.json.",
)

SIBLING_SECTION = (
    "\n### debug port group sub-ports (typical)\n\n"
    "| Sub-port | Direction |\n|---|---|\n"
    "| `o_debug_req` | output |\n| `o_debug_ack` | input |\n"
)


def _project(tmp_path, *, delegation=True, extra="o_sram_waddr", declaration=None):
    project = tmp_path / "design"
    rtl_dir = project / "phase2/stage1/rtl"
    rtl_dir.mkdir(parents=True)
    (project / "input/docs").mkdir(parents=True)
    (project / "input/docs/L3_external_interface.md").write_text(DELEGATION)
    (project / "plugin_output").mkdir()
    if declaration is None:
        declaration = {"sram_interface": {
            "read_address": "o_sram_addr", "write_enable": "o_sram_we",
            "write_address": "o_sram_waddr"}}
    (project / "plugin_output/declaration.json").write_text(json.dumps(declaration))
    gd = project / "phase1/generated_docs"
    gd.mkdir(parents=True)
    l9_ports = [
        {"name": "o_sram_addr", "direction": "output", "width": 10},
        {"name": "o_sram_we", "direction": "output", "width": 1}]
    l9 = {"top_module": "dut", "ports": l9_ports, "top_ports": l9_ports,
        "source_documents": ["input/docs/L3_external_interface.md"],
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


FOREIGN_DECLARATION = {
    "sram_interface": {"read_address": "o_sram_addr",
                       "write_enable": "o_sram_we"},
    "debug_interface": {"other": "o_sram_waddr"},
}


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
    # English polarity cannot decide ownership; the declaration entry does.
    assert groups.extract_delegated_groups(
        {"L3_external_interface.md": denied}) == L9_GROUP


@pytest.mark.parametrize("statement", NO_DELEGATION)
def test_phase1_non_authority_keeps_extra_port_blocked(tmp_path, statement):
    sys.path.insert(0, str(PROGRAMS))
    import phase1_doc_one_shot_runner as phase1

    project, spec, rtl = _project(
        tmp_path, delegation=False, declaration=FOREIGN_DECLARATION)
    document = DELEGATION.replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.", statement)
    (project / "input/docs/L3_external_interface.md").write_text(document)
    phase1.gen_l9_integration_spec(
        project, {"L3_external_interface.md": document}, {})
    emitted = json.loads(spec.read_text())
    # Keep the ordinary table's two required ports as the checker contract.
    emitted["top_module"] = "dut"
    emitted["ports"] = emitted["top_ports"] = [
        {"name": "o_sram_addr", "direction": "output", "width": 10},
        {"name": "o_sram_we", "direction": "output", "width": 1},
    ]
    spec.write_text(json.dumps(emitted))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)
    assert emitted["plugin_declared_port_groups"] == L9_GROUP


@pytest.mark.parametrize("statement", OTHER_GROUP_SENTENCE)
def test_phase1_sibling_reference_never_delegates_current_group(tmp_path, statement):
    sys.path.insert(0, str(PROGRAMS))
    import phase1_doc_one_shot_runner as phase1

    project, spec, rtl = _project(
        tmp_path, delegation=False, declaration=FOREIGN_DECLARATION)
    document = DELEGATION.replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.", statement) + SIBLING_SECTION
    (project / "input/docs/L3_external_interface.md").write_text(document)
    phase1.gen_l9_integration_spec(
        project, {"L3_external_interface.md": document}, {})
    emitted = json.loads(spec.read_text())
    emitted["top_module"] = "dut"
    emitted["ports"] = emitted["top_ports"] = [
        {"name": "o_sram_addr", "direction": "output", "width": 10},
        {"name": "o_sram_we", "direction": "output", "width": 1},
    ]
    spec.write_text(json.dumps(emitted))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)
    assert emitted["plugin_declared_port_groups"] == L9_GROUP


def test_sibling_declaration_map_cannot_authorize_group_port(tmp_path):
    project, spec, rtl = _project(tmp_path)
    (project / "plugin_output/declaration.json").write_text(
        json.dumps(FOREIGN_DECLARATION))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)


def test_sibling_sections_do_not_delegate_the_previous_heading():
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups

    document = DELEGATION.replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.", "The table lists the SRAM ports.")
    document += SIBLING_SECTION.replace(
        "| Sub-port | Direction |",
        "The debug port names are in declaration.json.\n\n| Sub-port | Direction |")
    assert not any(row["group"] == "sram" for row in
                   groups.extract_delegated_groups(
                       {"L3_external_interface.md": document}))


def test_generic_direction_prefix_of_sibling_does_not_veto_own_group():
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups

    sibling = SIBLING_SECTION.replace("o_debug_req", "o_addr").replace(
        "o_debug_ack", "o_we")
    rows = groups.extract_delegated_groups(
        {"L3_external_interface.md": DELEGATION + sibling})
    assert any(row["group"] == "sram" for row in rows)


@pytest.mark.parametrize("statement", AUTHORITY_SENTENCE)
def test_phase1_keeps_explicit_port_name_authority(statement):
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups

    document = DELEGATION.replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.", statement)
    assert groups.extract_delegated_groups(
        {"L3_external_interface.md": document}) == L9_GROUP


def test_real_input_top_choice_is_not_port_name_delegation():
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups
    from _hostpaths import require_repo

    source = require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
        "fixtures", "subservient_input_docs", "L8_submodule_integration.md")
    line = next(row for row in source.read_text().splitlines()
                if "declaration.json" in row and "Plugin" in row)
    document = DELEGATION.replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.", line)
    assert groups.extract_delegated_groups(
        {"L3_external_interface.md": document}) == L9_GROUP


def test_bare_section_mention_accepts_only_own_declaration_names(tmp_path):
    sys.path.insert(0, str(PROGRAMS))
    import phase1_doc_one_shot_runner as phase1

    project, spec, rtl = _project(tmp_path, delegation=False)
    document = DELEGATION.replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.",
        "declaration.json records the choices.")
    (project / "input/docs/L3_external_interface.md").write_text(document)
    phase1.gen_l9_integration_spec(
        project, {"L3_external_interface.md": document}, {})
    emitted = json.loads(spec.read_text())
    emitted["top_module"] = "dut"
    emitted["ports"] = emitted["top_ports"] = [
        {"name": "o_sram_addr", "direction": "output", "width": 10},
        {"name": "o_sram_we", "direction": "output", "width": 1},
    ]
    spec.write_text(json.dumps(emitted))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 0, findings
    assert ("port-extra", "o_sram_waddr") not in _errors(findings)
    assert emitted["plugin_declared_port_groups"] == L9_GROUP


def test_l9_row_without_own_section_mention_grants_nothing(tmp_path):
    project, spec, rtl = _project(tmp_path)
    (project / "input/docs/L3_external_interface.md").write_text(
        DELEGATION.replace("declaration.json", "a separate file"))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)


def test_l9_locator_cannot_read_a_generated_section(tmp_path):
    project, spec, rtl = _project(tmp_path)
    generated = project / "phase2" / "evidence.md"
    generated.write_text(DELEGATION)
    l9 = json.loads(spec.read_text())
    l9["plugin_declared_port_groups"][0]["source_document"] = (
        "__chip_root_docs__/phase2/evidence.md")
    spec.write_text(json.dumps(l9))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)


def test_forged_oracle_locator_is_never_opened(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups

    project, spec, _ = _project(tmp_path)
    (project / "input/docs/L3_external_interface.md").write_text(
        DELEGATION.replace("declaration.json", "a separate file"))
    oracle = project / "docs/oracle/README.md"
    oracle.parent.mkdir(parents=True)
    oracle.write_text(DELEGATION)
    l9 = json.loads(spec.read_text())
    l9["plugin_declared_port_groups"][0]["source_document"] = (
        "__chip_root_docs__/docs/oracle/README.md")
    l9["source_documents"].append("docs/oracle/README.md")

    original_read_text = Path.read_text

    def audited_read_text(path, *args, **kwargs):
        if path.resolve() == oracle.resolve():
            raise AssertionError("oracle source was opened")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", audited_read_text)
    assert groups.resolve_delegated_groups(project, l9) == []


def test_forged_oracle_locator_cannot_clear_port_extra(tmp_path):
    project, spec, rtl = _project(tmp_path)
    (project / "input/docs/L3_external_interface.md").write_text(
        DELEGATION.replace("declaration.json", "a separate file"))
    oracle = project / "docs/oracle/README.md"
    oracle.parent.mkdir(parents=True)
    oracle.write_text(DELEGATION)
    l9 = json.loads(spec.read_text())
    l9["plugin_declared_port_groups"][0]["source_document"] = (
        "__chip_root_docs__/docs/oracle/README.md")
    l9["source_documents"].append("docs/oracle/README.md")
    spec.write_text(json.dumps(l9))

    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)


def test_oracle_nested_under_input_docs_is_never_opened(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups

    project, spec, _ = _project(tmp_path)
    oracle = project / "input/docs/oracle/README.md"
    oracle.parent.mkdir(parents=True)
    oracle.write_text(DELEGATION)
    l9 = json.loads(spec.read_text())
    l9["plugin_declared_port_groups"][0]["source_document"] = (
        "oracle/README.md")
    l9["source_documents"].append("input/docs/oracle/README.md")

    original_read_text = Path.read_text

    def audited_read_text(path, *args, **kwargs):
        if path.resolve() == oracle.resolve():
            raise AssertionError("oracle source was opened")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", audited_read_text)
    assert groups.resolve_delegated_groups(project, l9) == []


def test_uninventoried_input_doc_cannot_grant_a_port(tmp_path):
    project, spec, rtl = _project(tmp_path)
    l9 = json.loads(spec.read_text())
    l9["source_documents"] = []
    spec.write_text(json.dumps(l9))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)


def test_inventoried_symlink_cannot_read_unlisted_source(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups

    project, spec, _ = _project(tmp_path)
    target = project / "input/docs/unlisted.md"
    target.write_text(DELEGATION)
    link = project / "input/docs/L3_external_interface.md"
    link.unlink()
    link.symlink_to(target)
    l9 = json.loads(spec.read_text())
    original_read_text = Path.read_text

    def audited_read_text(path, *args, **kwargs):
        if path.resolve() == target.resolve():
            raise AssertionError("unlisted source was opened")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", audited_read_text)
    assert groups.resolve_delegated_groups(project, l9) == []


def test_own_mention_and_entry_work_without_example_table(tmp_path):
    sys.path.insert(0, str(PROGRAMS))
    import phase1_doc_one_shot_runner as phase1

    project, spec, rtl = _project(tmp_path, delegation=False)
    document = "### SRAM port group\n\ndeclaration.json records this group.\n"
    (project / "input/docs/L3_external_interface.md").write_text(document)
    phase1.gen_l9_integration_spec(
        project, {"L3_external_interface.md": document}, {})
    emitted = json.loads(spec.read_text())
    emitted["top_module"] = "dut"
    emitted["ports"] = emitted["top_ports"] = [
        {"name": "o_sram_addr", "direction": "output", "width": 10},
        {"name": "o_sram_we", "direction": "output", "width": 1},
    ]
    spec.write_text(json.dumps(emitted))
    result, findings = _check(project, spec, rtl)
    assert result.returncode == 0, findings
    assert emitted["plugin_declared_port_groups"] == [
        {**L9_GROUP[0], "example_ports": []}]


def test_declaration_mention_in_group_heading_counts(tmp_path):
    sys.path.insert(0, str(PROGRAMS))
    import _delegated_port_groups as groups

    document = DELEGATION.replace(
        "### SRAM port group sub-ports (typical)",
        "### SRAM port group declaration.json").replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.", "The table is illustrative.")
    project, _, _ = _project(tmp_path)
    (project / "input/docs/L3_external_interface.md").write_text(document)
    rows = groups.extract_delegated_groups(
        {"L3_external_interface.md": document})
    assert rows == L9_GROUP
    assert groups.resolve_delegated_groups(
        project, {"source_documents": ["input/docs/L3_external_interface.md"],
                  "plugin_declared_port_groups": rows})[0][
            "declared_ports"] == {"o_sram_addr", "o_sram_we", "o_sram_waddr"}


@pytest.mark.parametrize("statement", (
    "The debug signal names are defined in declaration.json.",
    "The debug sub-port names are defined in declaration.json.",
    "The port names of the debug block live in declaration.json.",
))
def test_foreign_prose_and_foreign_entry_cannot_grant_sram_port(
        tmp_path, statement):
    sys.path.insert(0, str(PROGRAMS))
    import phase1_doc_one_shot_runner as phase1

    project, spec, rtl = _project(
        tmp_path, delegation=False, declaration=FOREIGN_DECLARATION)
    document = DELEGATION.replace(
        "The concrete signal names for this port group are declared by the "
        "plugin in declaration.json.", statement)
    (project / "input/docs/L3_external_interface.md").write_text(document)
    phase1.gen_l9_integration_spec(
        project, {"L3_external_interface.md": document}, {})
    emitted = json.loads(spec.read_text())
    emitted["top_module"] = "dut"
    emitted["ports"] = emitted["top_ports"] = [
        {"name": "o_sram_addr", "direction": "output", "width": 10},
        {"name": "o_sram_we", "direction": "output", "width": 1},
    ]
    spec.write_text(json.dumps(emitted))
    result, findings = _check(project, spec, rtl)
    assert emitted["plugin_declared_port_groups"] == L9_GROUP
    assert result.returncode == 1
    assert ("port-extra", "o_sram_waddr") in _errors(findings)


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

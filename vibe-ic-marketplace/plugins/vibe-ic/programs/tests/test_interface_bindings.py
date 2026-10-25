"""Native source -> L1/L9 -> unchanged strict consumer, with real tool arms.

Synthetic shared packages contain no DUT behavior. Tool tests require an
an immutable VIBEIC_EDA_IMAGE and the normal local/fresh-run transport. No author receipt,
mocked measurement, edited L-doc, policy downgrade or reused-IP fiction is used.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import interface_bindings as binding
import phase1_doc_one_shot_runner as docs

FIXTURE = Path(__file__).parent / "fixtures/interface_binding"
TOOL = pytest.mark.skipif(not os.environ.get("VIBEIC_EDA_IMAGE"),
                          reason="pinned EDA runtime image required for native package measurement")


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path):
    from programs.tests._hostpaths import require_repo
    fixture = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
                           "tests", "fixtures", "interface_binding")
    # The normal native transport owns and mounts its private package scratch.
    root = Path(os.environ.get("VIBEIC_INTERFACE_TEST_ROOT", tmp_path))
    root.mkdir(parents=True, exist_ok=True)
    import tempfile
    p = Path(tempfile.mkdtemp(prefix="interface-test-", dir=root))
    source = p / "input/docs/interfaces.md"
    source.parent.mkdir(parents=True)
    shutil.copyfile(fixture / "interfaces.md", source)
    context = p / binding.CONTEXT
    context.mkdir(parents=True)
    shutil.copyfile(fixture / "types.sv", context / "types.sv")
    (context / "provider.json").write_text('{"revision":"neutral-v1","parameters":"selected source bytes"}\n')
    extracted = {source.name: source.read_text()}
    docs.gen_l1_datasheet(p, extracted)
    result = docs.gen_l9_integration_spec(p, extracted, {})
    original = json.loads(result.path.read_text())
    contract = binding.input_contract(p, original)
    lines = source.read_text().splitlines()

    def basis(needle):
        i = next(i for i, line in enumerate(lines) if needle in line)
        return [{"file": "input/docs/interfaces.md", "line": i + 1, "quote": lines[i]}]

    contract["context"] = {
        "image": os.environ.get("VIBEIC_EDA_IMAGE", "test/image@sha256:" + "1" * 64),
        "revision": "neutral-v1",
        "files": {f"{binding.CONTEXT}/{x.name}": _hash(x) for x in sorted(context.iterdir())},
        "compile_order": [f"{binding.CONTEXT}/types.sv"], "include_dirs": [], "defines": [], "parameters": {}}
    contract["physical_ports"] = [
        {"name": name, "direction": direction, "type_ref": typ, "width": width, "basis": basis(needle)}
        for name, direction, typ, width, needle in (
            ("token_i", "input", "neutral_pkg::token_t", 5, "| token_i |"),
            ("code_i", "input", "neutral_pkg::code_t", 4, "| code_i |"),
            ("status_o", "output", "logic", 1, "| status_o |"),
            ("link_req_o", "output", "neutral_pkg::link_req_t", 9, "| link |"),
            ("link_rsp_i", "input", "neutral_pkg::link_rsp_t", 17, "| link |"),
            ("transport_req_i", "input", "neutral_pkg::transport_h2d_t", 11, "| transport |"),
            ("transport_rsp_o", "output", "neutral_pkg::transport_d2h_t", 13, "| transport |"))]
    contract["logical_bindings"] = [
        {"source_name": name, "kind": "req_rsp", "local_role": role, "instance_count": 1,
         "request_port": req, "response_port": rsp, "basis": basis(f"| {name} |"),
         "selected_payload_types": {"request": req_type, "response": rsp_type}}
        for name, role, req, rsp, req_type, rsp_type in (
            ("link", "req", "link_req_o", "link_rsp_i", "neutral_pkg::link_req_t", "neutral_pkg::link_rsp_t"),
            ("transport", "rsp", "transport_req_i", "transport_rsp_o", "neutral_pkg::transport_h2d_t", "neutral_pkg::transport_d2h_t"))]
    contract["decisions"] = [
        {"id": "token-spelling", "kind": "type_correction", "port": "token_i",
         "source_type_ref": "misspelled_pkg::token_t", "selected_type_ref": "neutral_pkg::token_t",
         "original_claim": "misspelled_pkg::token_t", "selected_claim": "neutral_pkg::token_t input token_i",
         "authority": "AGENT_ENGINEERING_CHOICE", "reason": "Selected shared-package spelling; original retained",
         "basis": basis("| token_i |")},
        {"id": "status-conflict", "kind": "source_conflict", "port": "status_o",
         "source_type_ref": "neutral_pkg::flag4", "selected_type_ref": "logic",
         "original_claim": "neutral_pkg::flag4", "selected_claim": "physical status_o remains scalar logic",
         "authority": "AGENT_ENGINEERING_CHOICE", "reason": "Physical scalar and internal encoding remain distinct",
         "basis": basis("| status |") + basis("| status_o |")}]
    author = p / binding.INPUT
    author.write_text(json.dumps(contract))
    return p, extracted, result.path, author, contract


def _gate(project):
    docs._emit_l14_to_l18_via_extractor(project, {
        "interfaces.md": (project / "input/docs/interfaces.md").read_text()})
    return subprocess.run([sys.executable, str(PROGRAMS / "l17_channel_catalog_consumer_contract_check.py"), str(project)],
                          capture_output=True, text=True, check=False)


@TOOL
def test_native_package_binding_actual_producer_and_strict_consumer(tmp_path):
    p, extracted, l9, author, contract = _project(tmp_path)
    l1 = l9.with_name("L1_DATASHEET.json")
    original_l1 = l1.read_bytes()
    before = _gate(p)
    assert before.returncode == 1
    assert "PORT_WIDTH_UNRESOLVED_BY_CONSUMER" in before.stdout
    from _specrtl_common import extract_spec_contract
    with pytest.raises(ValueError, match="UNRESOLVED_STRUCTURED_INTERFACE"):
        extract_spec_contract(l9.read_text(), is_json=True, project=p)
    docs.gen_l9_integration_spec(p, extracted, {})
    emitted = json.loads(l9.read_text())
    for key in binding.PORT_KEYS:
        ports = {r["name"]: r for r in emitted[key]}
        assert not {"link", "transport"} & ports.keys()
        for row in contract["physical_ports"]:
            assert {k: ports[row["name"]][k] for k in ("direction", "type_ref", "width")} == {
                k: row[k] for k in ("direction", "type_ref", "width")}
            assert "width_symbolic" not in ports[row["name"]]
            assert ports[row["name"]]["mode"] == ports[row["name"]]["direction"]
    ledger = emitted["interface_binding_authoring"]
    assert ledger["logical_bindings"] == contract["logical_bindings"]
    assert ledger["source_interface"] == contract["interface"]
    assert ledger["decisions"] == contract["decisions"]
    assert ledger["review"] == "PENDING_INDEPENDENT_D1"
    assert ledger["measurement"]["elaboration_rc"] == ledger["measurement"]["simulation_rc"] == 0
    assert l1.read_bytes() == original_l1
    after = _gate(p)
    assert after.returncode == 0, after.stdout + after.stderr
    consumed = extract_spec_contract(l9.read_text(), is_json=True, project=p)
    consumed_ports = {port.name: port for port in consumed.ports}
    for row in contract["physical_ports"]:
        assert consumed_ports[row["name"]].direction == row["direction"]
        assert consumed_ports[row["name"]].width == row["width"]
    # A second real native admission has a fresh execution receipt but the
    # semantic port contract/measurement remains identical.
    docs.gen_l9_integration_spec(p, extracted, {})
    again = json.loads(l9.read_text())
    assert again["interface_binding_authoring"] == ledger
    assert all(again[k] == emitted[k] for k in binding.PORT_KEYS)
    execution = json.loads((p / binding.REPORT / "execution.json").read_text())
    assert execution["semantic_proof"]["sha256"] == _hash(p / binding.REPORT / "proof.json")
    assert all(_hash(p / rel) == value for rel, value in execution["artifacts"].items())
    # Unsigned program success cannot author the independent D1 judgement.
    import ai_signed_judgement as judgement
    assert not judgement.check(p, "D1")[0]
    assert author in judgement.evidence(p, "D1")
    assert (p / binding.REPORT / "proof.json") in judgement.evidence(p, "D1")
    # Removing the author input and running the native writer restores the
    # honest unresolved source contract; a previously expanded L9 is not reused.
    author.unlink()
    docs.gen_l9_integration_spec(p, extracted, {})
    restored = json.loads(l9.read_text())
    assert "interface_binding_authoring" not in restored
    assert {"link", "transport"} <= {r["name"] for r in restored["ports"]}
    assert _gate(p).returncode == 1


@pytest.mark.parametrize("mutation,reason", [
    ("stale_source", "input_files"), ("stale_roster", "interface does not"),
    ("same_width_wrong_type", "without explicit correction"),
    ("wrong_role", "local_role"), ("wrong_direction", "local_role"),
    ("wrong_package", "package/family"), ("wrong_family", "package/family"),
    ("swapped_valid_pair", "package/family"), ("missing_pair", "complete unique"),
    ("missing_correction", "without explicit correction"), ("false_quote", "not on"),
    ("wrong_context_hash", "hash mismatch"), ("unlisted_context", "exact context subtree"),
    ("include_escape", "outside pinned context"), ("context_symlink", "symlink"),
    ("context_escape", "noncanonical path"), ("duplicate_json", "duplicate JSON key"),
    ("input_symlink", "symlink"), ("single_bundle_port", "single physical port"),
])
def test_native_input_refusals_preserve_prior_publication(tmp_path, monkeypatch, mutation, reason):
    p, extracted, l9, author, data = _project(tmp_path)
    before = l9.read_bytes()
    physical = {r["name"]: r for r in data["physical_ports"]}
    logical = data["logical_bindings"][0]
    if mutation == "stale_source": (p / "input/docs/interfaces.md").write_text("changed source\n")
    elif mutation == "stale_roster": data["interface"][0]["width"] = 99
    elif mutation == "same_width_wrong_type": physical["code_i"]["type_ref"] = "neutral_pkg::flag4_t"
    elif mutation == "wrong_role": logical["local_role"] = "rsp"
    elif mutation == "wrong_direction": physical["link_req_o"]["direction"] = "input"
    elif mutation in ("wrong_package", "wrong_family", "swapped_valid_pair"):
        req, rsp = ({"wrong_package": ("unrelated_pkg::link_req_t", "unrelated_pkg::link_rsp_t"),
                     "wrong_family": ("neutral_pkg::transport_h2d_t", "neutral_pkg::transport_d2h_t"),
                     "swapped_valid_pair": ("neutral_pkg::link_rsp_t", "neutral_pkg::link_req_t")}[mutation])
        physical["link_req_o"].update(type_ref=req, width=17 if mutation == "swapped_valid_pair" else 9)
        physical["link_rsp_i"].update(type_ref=rsp, width=9 if mutation == "swapped_valid_pair" else 17)
        logical["selected_payload_types"] = {"request": req, "response": rsp}
    elif mutation == "missing_pair": logical["response_port"] = "unbound_i"
    elif mutation == "missing_correction": data["decisions"].pop(0)
    elif mutation == "false_quote": physical["code_i"]["basis"][0]["quote"] = "a source quote which was never written"
    elif mutation == "wrong_context_hash": data["context"]["files"][f"{binding.CONTEXT}/types.sv"] = "0" * 64
    elif mutation == "unlisted_context": (p / binding.CONTEXT / "extra.json").write_text("{}")
    elif mutation == "include_escape":
        types = p / binding.CONTEXT / "types.sv"
        types.write_text('`include "../outside.svh"\n' + types.read_text())
        data["context"]["files"][f"{binding.CONTEXT}/types.sv"] = _hash(types)
    elif mutation == "context_symlink": (p / binding.CONTEXT / "link.sv").symlink_to(p / binding.CONTEXT / "types.sv")
    elif mutation == "context_escape": data["context"]["files"][binding.CONTEXT + "/../bad.sv"] = "0" * 64
    elif mutation == "single_bundle_port": physical["link_req_o"]["name"] = "link"
    if mutation == "input_symlink":
        target = author.with_name("choices.json")
        author.rename(target)
        author.symlink_to(target)
    else:
        raw = json.dumps(data)
        if mutation == "duplicate_json": raw = raw[:-1] + ',"schema":"duplicate"}'
        author.write_text(raw)
    # These are semantic/input refusals, so no tool or partial output should
    # be needed. The positive case above uses real measured values.
    monkeypatch.setattr(binding, "_probe", lambda *a: pytest.fail("unexpected tool execution"))
    with pytest.raises(ValueError, match=reason):
        docs.gen_l9_integration_spec(p, extracted, {})
    assert l9.read_bytes() == before


@TOOL
@pytest.mark.parametrize("mutation", ["wrong_width", "changed_package_with_updated_hash", "wrong_image"])
def test_fresh_tool_measurement_refuses_claimed_values(tmp_path, mutation):
    p, extracted, l9, author, data = _project(tmp_path)
    before = l9.read_bytes()
    if mutation == "wrong_width": data["physical_ports"][0]["width"] = 6
    elif mutation == "changed_package_with_updated_hash":
        types = p / binding.CONTEXT / "types.sv"
        types.write_text(types.read_text().replace("logic [4:0] token_t", "logic [6:0] token_t"))
        data["context"]["files"][f"{binding.CONTEXT}/types.sv"] = _hash(types)
    else: data["context"]["image"] = "test/wrong@sha256:" + "0" * 64
    author.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="fresh measured|runtime image"):
        docs.gen_l9_integration_spec(p, extracted, {})
    assert l9.read_bytes() == before


@TOOL
def test_uncovered_logical_binding_retains_strict_consumer_refusal(tmp_path):
    p, extracted, l9, author, data = _project(tmp_path)
    data["logical_bindings"] = data["logical_bindings"][:1]
    data["physical_ports"] = [r for r in data["physical_ports"] if not r["name"].startswith("transport_")]
    author.write_text(json.dumps(data))
    docs.gen_l9_integration_spec(p, extracted, {})
    emitted = json.loads(l9.read_text())
    ports = {r["name"]: r for r in emitted["ports"]}
    assert ports["code_i"]["width"] == 4
    assert ports["transport"]["package_struct"] == "neutral_pkg::transport"
    assert ports["transport"]["mode"] == "interface"
    assert ports["transport"]["width"] is None
    assert ports["transport"]["width_unresolved"] is True
    result = _gate(p)
    assert result.returncode == 1
    assert "PORT_WIDTH_UNRESOLVED_BY_CONSUMER" in result.stdout
    assert '"transport"' in result.stdout


def test_descriptor_is_read_only(tmp_path):
    p, _extracted, l9, author, data = _project(tmp_path)
    before = l9.read_bytes(), author.read_bytes()
    result = subprocess.run([sys.executable, str(PROGRAMS / "phase1_doc_one_shot_runner.py"), str(p),
                             "--describe-interface-binding-input"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    described = json.loads(result.stdout)
    assert described["interface"] == data["interface"]
    assert described["physical_ports"] == []
    assert before == (l9.read_bytes(), author.read_bytes())


@pytest.mark.parametrize("mutation", ["add_manifest", "change_manifest", "remove_manifest", "change_provider", "add_context"])
def test_authority_is_native_phase1_freshness_and_d1_subject(tmp_path, mutation):
    import _phase1_producer_identity as identity
    import ai_signed_judgement as judgement
    p, _extracted, _l9, author, data = _project(tmp_path)
    if mutation == "add_manifest": author.unlink()
    # Exercise the existing input-record/digest consumer, including absent
    # fixed paths; no new global gate or fabricated producer stamp is needed.
    context = p / binding.CONTEXT
    io = {"reads": {str(x) for x in context.iterdir()}, "listed": {str(context)}}
    record = identity.design_input_record(p, io, [])
    old = identity.inputs_digest(p, record)
    old_d1 = judgement.evidence_sha256(p, "D1")
    if mutation in ("add_manifest", "change_manifest"):
        data["decisions"][0]["reason"] += " new selected input"
        author.write_text(json.dumps(data))
    elif mutation == "remove_manifest": author.unlink()
    elif mutation == "change_provider": (context / "provider.json").write_text('{"revision":"changed"}')
    else: (context / "new_metadata.json").write_text("{}")
    assert identity.inputs_digest(p, record) != old
    assert judgement.evidence_sha256(p, "D1") != old_d1
    unrelated = p / "plugin_input/unrelated.json"
    unchanged = identity.inputs_digest(p, record)
    unchanged_d1 = judgement.evidence_sha256(p, "D1")
    unrelated.write_text("{}")
    assert identity.inputs_digest(p, record) == unchanged
    assert judgement.evidence_sha256(p, "D1") == unchanged_d1


def test_nested_context_changes_invalidate_even_pre_manifest_baseline(tmp_path):
    import _phase1_producer_identity as identity
    p, _extracted, _l9, author, _data = _project(tmp_path)
    author.unlink()
    nested = p / binding.CONTEXT / "provider/nested.json"
    nested.parent.mkdir()
    nested.write_text('{"provider":"first"}')
    record = identity.design_input_record(p, {"reads": set(), "listed": set()}, [])
    before = identity.inputs_digest(p, record)
    nested.write_text('{"provider":"changed"}')
    assert identity.inputs_digest(p, record) != before


def _primitive_project(tmp_path, *, range_only=False):
    """Neutral conflicting source claims; no authored implementation/probe."""
    p = tmp_path / "primitive"
    source = p / "input/docs/interface.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "Create the corrected interface described here.\n"
        "The result_o datapath must return thirteen bits, not its old scalar declaration.\n"
        "The six-bit slot_i vector uses the indices slot_i[10:5].\n"
        "module SelectedUnit (input [5:0] slot_i, output result_o);\n"
    )
    extracted = {source.name: source.read_text()}
    l1 = Path(docs.gen_l1_datasheet(p, extracted).path)
    l9 = Path(docs.gen_l9_integration_spec(p, extracted, {}).path)
    original = json.loads(l9.read_text())
    # The v1 descriptor still describes the raw source. V2 deliberately also
    # binds index endpoints, which a numeric width cannot express alone.
    legacy = binding.input_contract(p, original)
    interface = copy.deepcopy(legacy["interface"])
    by_name = {row["name"]: row for row in original["ports"]}
    for row in interface:
        row.update(msb=by_name[row["name"]].get("msb"), lsb=by_name[row["name"]].get("lsb"))
    refs = []
    for name, width, msb, lsb, line in [("slot_i", 6, 10, 5, 3), ("result_o", 13, 12, 0, 2)]:
        if range_only and name == "result_o":
            continue
        old = next(row for row in interface if row["name"] == name)
        refs.append({"port": name,
                     "before": {key: old.get(key) for key in ("direction", "width", "msb", "lsb")},
                     "after": {"width": width, "msb": msb, "lsb": lsb},
                     "authority": "AGENT_ENGINEERING_CHOICE",
                     "reason": "Select the desired interface claim while retaining the original declaration.",
                     "basis": [{"file": "input/docs/interface.md", "line": line,
                                "quote": source.read_text().splitlines()[line-1]},
                               {"file": "input/docs/interface.md", "line": 4,
                                "quote": source.read_text().splitlines()[3]}]})
    contract = {"schema": "vibeic.phase1-interface-bindings.v2",
                "input_files": legacy["input_files"], "top_module": original["top_module"],
                "interface": interface, "primitive_refinements": refs}
    author = p / binding.INPUT
    author.parent.mkdir(parents=True, exist_ok=True)
    author.write_text(json.dumps(contract))
    return p, source, extracted, l1, l9, author, contract


def test_native_primitive_refinement_actual_writer_and_current_consumers(tmp_path):
    import ai_signed_judgement as judgement
    import l1_pin_bus_width_actionable_check as widths
    from _specrtl_common import extract_spec_contract
    p, source, extracted, l1, l9, author, data = _primitive_project(tmp_path)
    original_l1 = l1.read_bytes()
    assert widths.evaluate(p)["rc"] == 1
    refused = None
    try:
        docs.gen_l9_integration_spec(p, extracted, {})
    except ValueError as exc:
        refused = str(exc)
    emitted = json.loads(l9.read_text())
    # Value-bearing pre-fix control: the actual native publication remains
    # scalar, rather than merely noticing a new API/marker is absent.
    assert next(row for row in emitted["ports"] if row["name"] == "result_o")["width"] == 13, refused
    assert refused is None
    for key in binding.PORT_KEYS:
        ports = {row["name"]: row for row in emitted[key]}
        assert (ports["slot_i"]["width"], ports["slot_i"]["msb"], ports["slot_i"]["lsb"]) == (6, 10, 5)
        assert (ports["result_o"]["width"], ports["result_o"]["msb"], ports["result_o"]["lsb"]) == (13, 12, 0)
    current = binding.current_published_ports(p, emitted)
    assert current["result_o"]["width"] == 13
    assert current["slot_i"]["lsb"] == 5
    assert widths.evaluate(p)["rc"] == 0
    consumed = extract_spec_contract(l9.read_text(), is_json=True, project=p)
    assert {row.name: row.width for row in consumed.ports} == {"slot_i": 6, "result_o": 13}
    ledger = emitted["interface_binding_authoring"]
    assert ledger["review"] == "PENDING_INDEPENDENT_D1"
    assert ledger["source_interface"] == data["interface"]
    assert "measurement" not in ledger
    assert not (p / binding.REPORT / "type_probe.sv").exists()
    assert l1.read_bytes() == original_l1
    assert author in judgement.evidence(p, "D1")
    assert not judgement.check(p, "D1")[0]
    before = {str(f.relative_to(p)): _hash(f) for f in p.rglob("*") if f.is_file()}
    binding.current_published_ports(p, emitted)
    widths.evaluate(p)
    assert before == {str(f.relative_to(p)): _hash(f) for f in p.rglob("*") if f.is_file()}


@pytest.mark.parametrize("mutation,reason", [
    ("stale_input", "input_files"), ("false_quote", "not on"),
    ("unnamed_quote", "name"), ("wrong_before", "before"),
    ("wrong_range", "range"), ("zero_width", "range"),
    ("unknown_port", "existing primitive"), ("direction", "before"),
    ("duplicate", "duplicate"), ("wrong_top", "top_module"),
    ("roster", "interface"), ("type", "interface"),
    ("authority", "authority"), ("rationale", "reason"),
])
def test_primitive_input_refusal_preserves_native_publication(tmp_path, mutation, reason):
    p, source, extracted, _l1, l9, author, data = _primitive_project(tmp_path)
    original = l9.read_bytes()
    row = data["primitive_refinements"][0]
    if mutation == "stale_input": source.write_text(source.read_text() + "changed\n")
    elif mutation == "false_quote": row["basis"][0]["quote"] = "not actually a source line"
    elif mutation == "unnamed_quote": row["basis"] = [{"file": "input/docs/interface.md", "line": 1, "quote": source.read_text().splitlines()[0]}]
    elif mutation == "wrong_before": row["before"]["width"] = 2
    elif mutation == "wrong_range": row["after"]["msb"] = 11
    elif mutation == "zero_width": row["after"]["width"] = 0
    elif mutation == "unknown_port": row["port"] = "absent_o"
    elif mutation == "direction": row["before"]["direction"] = "output"
    elif mutation == "duplicate": data["primitive_refinements"].append(copy.deepcopy(row))
    elif mutation == "wrong_top": data["top_module"] = "OtherUnit"
    elif mutation == "roster": data["interface"][0]["lsb"] = 2
    elif mutation == "type":
        # This is a metadata-forgery attempt, not a new source type.
        data["interface"][0]["type_ref"] = "external_pkg::payload_t"
    elif mutation == "authority": row["authority"] = "PROGRAM_PASS"
    elif mutation == "rationale": row["reason"] = ""
    author.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=reason):
        docs.gen_l9_integration_spec(p, extracted, {})
    assert l9.read_bytes() == original


@pytest.mark.parametrize("mutation", ["source", "manifest", "manifest_missing", "ledger", "alias", "direction_alias", "before", "raw_l1", "raw_l1_symlink"])
def test_current_primitive_publication_refuses_tampering(tmp_path, mutation):
    import l1_pin_bus_width_actionable_check as widths
    p, source, extracted, l1, l9, author, data = _primitive_project(tmp_path)
    docs.gen_l9_integration_spec(p, extracted, {})
    value = json.loads(l9.read_text())
    if mutation == "source": source.write_text(source.read_text() + "changed source\n")
    elif mutation == "manifest": author.write_text(author.read_text() + " ")
    elif mutation == "manifest_missing": author.unlink()
    elif mutation == "ledger": value["interface_binding_authoring"]["review"] = "PASS"
    elif mutation == "alias": value["ports"][0]["lsb"] = 0
    elif mutation == "direction_alias": value["ports"][0]["mode"] = "output"
    elif mutation == "before": value["interface_binding_authoring"]["source_interface"][0]["width"] = 100
    elif mutation == "raw_l1":
        raw = json.loads(l1.read_text()); raw["pin_table"][0]["width"] = 99; l1.write_text(json.dumps(raw))
    elif mutation == "raw_l1_symlink":
        retained = p / "retained_l1.json"
        l1.rename(retained)
        l1.symlink_to(retained)
    l9.write_text(json.dumps(value))
    assert widths.evaluate(p)["rc"] == 1


def test_primitive_range_does_not_excuse_uncovered_port(tmp_path):
    import l1_pin_bus_width_actionable_check as widths
    p, source, extracted, _l1, l9, author, data = _primitive_project(tmp_path, range_only=True)
    source.write_text(source.read_text() + "The current result_o signal also references result_o[12:0].\n")
    extracted = {source.name: source.read_text()}
    data["input_files"]["input/docs/interface.md"] = _hash(source)
    author.write_text(json.dumps(data))
    docs.gen_l9_integration_spec(p, extracted, {})
    result = widths.evaluate(p)
    assert result["rc"] == 1
    assert {row["pin"] for row in result["violations"]} == {"result_o"}


def test_primitive_branch_preserves_checked_in_package_type_boundary(tmp_path):
    p, _extracted, l9, author, _legacy = _project(tmp_path)
    original = l9.read_bytes()
    data = binding.input_contract(p, json.loads(original), schema=binding.REFINEMENT_SCHEMA)
    source = next(p for p in data["interface"] if p["name"] == "token_i")
    lines = (p / "input/docs/interfaces.md").read_text().splitlines()
    line = next(i for i, text in enumerate(lines, 1) if "| token_i |" in text)
    data["primitive_refinements"] = [{
        "port": "token_i", "before": {key: source.get(key) for key in ("direction", "width", "msb", "lsb")},
        "after": {"width": 5, "msb": 4, "lsb": 0},
        "authority": "AGENT_ENGINEERING_CHOICE", "reason": "A numeric interpretation cannot measure a package type.",
        "basis": [{"file": "input/docs/interfaces.md", "line": line, "quote": lines[line-1]}]}]
    author.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="existing primitive port"):
        binding.admit(p, json.loads(original))
    assert l9.read_bytes() == original

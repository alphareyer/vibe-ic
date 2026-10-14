"""Direct watchdog source-contract coverage; RTL execution stays in its consumer tests.

Use the existing neutral declaration fixture, then exercise the actual reader,
validator and emitter. These checks qualify source behavior, not silicon or a
complete design flow.
"""
import copy
import hashlib
import json

import pytest

import canonical_primitive_synth as consumer
import in_order_watchdog_synth as watchdog
from test_in_order_watchdog_consumer import _project


def _save(project, contract):
    path = project / watchdog.CONTRACT_REL
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


@pytest.mark.parametrize("actor,suffix", [("owner", ".md"), ("AI", ".TXT")])
def test_loader_returns_source_bound_candidate_without_writing_rtl(tmp_path, actor, suffix):
    contract = _project(tmp_path)
    original = tmp_path / "input/design.md"
    source = tmp_path / "input/docs" / ("declaration" + suffix)
    source.parent.mkdir()
    original.rename(source)
    contract["mapping"].update(actor=actor, source=source.relative_to(tmp_path).as_posix())
    declaration = _save(tmp_path, contract)
    original_bytes = {p: p.read_bytes() for p in (source, declaration)}

    candidate = watchdog.load_declared(tmp_path)

    assert candidate == {
        "shape": "in_order_watchdog", "module": "neutral_monitor",
        "rtl": watchdog.emit(contract),
        "contract_sha256": hashlib.sha256(original_bytes[declaration]).hexdigest(),
        "source_sha256": hashlib.sha256(original_bytes[source]).hexdigest(),
        "mapping_actor": actor,
    }
    assert consumer.declared_watchdog(tmp_path) == candidate
    assert {p: p.read_bytes() for p in (source, declaration)} == original_bytes
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == [
        source.relative_to(tmp_path).as_posix(), "input/in_order_watchdog.json"]
    assert not (tmp_path / "phase2").exists()


def test_missing_declaration_is_not_applicable_but_dangling_declaration_refuses(tmp_path):
    assert watchdog.load_declared(tmp_path) is None
    assert consumer.declared_watchdog(tmp_path) is None
    declaration = tmp_path / watchdog.CONTRACT_REL
    declaration.parent.mkdir()
    declaration.symlink_to(declaration.parent / "absent.json")
    with pytest.raises(ValueError, match="^WATCHDOG_CONTRACT_DANGLING_LINK$"):
        watchdog.load_declared(tmp_path)


@pytest.mark.parametrize("change,reason", [
    ("source_bytes", "WATCHDOG_MAPPING_SOURCE_CHANGED"),
    ("citation", "WATCHDOG_MAPPING_CITATION_ABSENT"),
    ("non_document", "WATCHDOG_MAPPING_SOURCE_NOT_PUBLIC_DOCUMENT"),
    ("self_mapping", "WATCHDOG_MAPPING_SOURCE_NOT_PUBLIC_DOCUMENT"),
])
def test_loader_refuses_stale_or_ungrounded_mapping_without_altering_inputs(tmp_path, change, reason):
    contract = _project(tmp_path)
    source = tmp_path / "input/design.md"
    if change == "source_bytes":
        # Even a trailing byte changes document identity; citations still exist.
        source.write_bytes(source.read_bytes() + b"\n")
    elif change == "citation":
        contract["mapping"]["citations"]["start"] = "This policy was never declared."
    elif change == "non_document":
        source = source.rename(tmp_path / "input/design.json")
        contract["mapping"]["source"] = "input/design.json"
    else:
        contract["mapping"]["source"] = watchdog.CONTRACT_REL.as_posix()
    declaration = _save(tmp_path, contract)
    held = {p: p.read_bytes() for p in (source, declaration)}

    with pytest.raises(ValueError, match="^" + reason + "$"):
        watchdog.load_declared(tmp_path)
    with pytest.raises(ValueError, match="^" + reason + "$"):
        consumer.declared_watchdog(tmp_path)

    assert {p: p.read_bytes() for p in (source, declaration)} == held
    assert not (tmp_path / "phase2").exists()


@pytest.mark.parametrize("boundary", ["contract", "source"])
def test_loader_refuses_inputs_resolved_outside_public_input_directory(tmp_path, boundary):
    project = tmp_path / "project"
    contract = _project(project)
    declaration = project / watchdog.CONTRACT_REL
    if boundary == "contract":
        declaration.rename(tmp_path / "outside.json")
        declaration.symlink_to(tmp_path / "outside.json")
    else:
        source = project / "input/design.md"
        source.rename(tmp_path / "outside.md")
        source.symlink_to(tmp_path / "outside.md")
    held = sorted((p.as_posix(), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())

    with pytest.raises(ValueError, match="not in the subpath"):
        watchdog.load_declared(project)

    assert sorted((p.as_posix(), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()) == held
    assert not (project / "phase2").exists()


@pytest.mark.parametrize("field,value,reason", [
    ("schema", "vibeic.in_order_watchdog.v0", "WATCHDOG_SCHEMA_UNSUPPORTED"),
    ("ports", {}, "WATCHDOG_PORT_ROLES_INCOMPLETE"),
    ("counter_bits", True, "WATCHDOG_COUNTER_BITS_UNSUPPORTED"),
    ("counter_bits", 0, "WATCHDOG_COUNTER_BITS_UNSUPPORTED"),
    ("counter_bits", 65, "WATCHDOG_COUNTER_BITS_UNSUPPORTED"),
    ("max_accepted", 1.0, "WATCHDOG_MAX_ACCEPTED_UNSUPPORTED"),
    ("max_accepted", 0, "WATCHDOG_MAX_ACCEPTED_UNSUPPORTED"),
    ("max_accepted", 1025, "WATCHDOG_MAX_ACCEPTED_UNSUPPORTED"),
    ("start", "first_response", "WATCHDOG_START_UNSUPPORTED"),
    ("response_order", "out_of_order", "WATCHDOG_RESPONSE_ORDER_UNSUPPORTED"),
    ("timeout_compare", "age_equal", "WATCHDOG_TIMEOUT_COMPARE_UNSUPPORTED"),
    ("initial_age", False, "WATCHDOG_INITIAL_AGE_UNSUPPORTED"),
    ("initial_age", 1, "WATCHDOG_INITIAL_AGE_UNSUPPORTED"),
    ("saturation", "wrap", "WATCHDOG_SATURATION_UNSUPPORTED"),
    ("reset", {"polarity": "high"}, "WATCHDOG_RESET_UNDECLARED_OR_UNSUPPORTED"),
    ("reset", {"polarity": "high", "synchrony": "sync", "guessed": True},
     "WATCHDOG_RESET_UNDECLARED_OR_UNSUPPORTED"),
    ("flag", "clear_on_done", "WATCHDOG_FLAG_UNSUPPORTED"),
    ("zero_latency", "enqueue_anyway", "WATCHDOG_ZERO_LATENCY_UNSUPPORTED"),
    ("violation_policy", "ignore", "WATCHDOG_VIOLATION_POLICY_UNSUPPORTED"),
    ("request_protocol", "cancel_on_idle", "WATCHDOG_REQUEST_PROTOCOL_UNSUPPORTED"),
])
def test_validator_and_emitter_refuse_unsupported_contracts(tmp_path, field, value, reason):
    contract = _project(tmp_path)
    contract[field] = value
    held = copy.deepcopy(contract)

    for operation in (watchdog.validate_contract, watchdog.emit):
        with pytest.raises(ValueError, match="^" + reason + "$"):
            operation(contract)

    assert contract == held


@pytest.mark.parametrize("change", ["missing_policy", "extra_policy", "non_object"])
def test_contract_field_set_must_be_explicit(tmp_path, change):
    contract = _project(tmp_path)
    if change == "missing_policy":
        del contract["timeout_compare"]
    elif change == "extra_policy":
        contract["guessed_timeout"] = 7
    else:
        contract = [contract]

    with pytest.raises(ValueError, match="^WATCHDOG_CONTRACT_FIELDS_UNDECLARED_OR_UNKNOWN$"):
        watchdog.validate_contract(contract)


@pytest.mark.parametrize("change", ["actor", "digest", "citation_role", "blank_quote", "extra_field"])
def test_mapping_requires_declared_actor_digest_and_each_policy_citation(tmp_path, change):
    contract = _project(tmp_path)
    mapping = contract["mapping"]
    if change == "actor":
        mapping["actor"] = "guessed"
    elif change == "digest":
        mapping["sha256"] = "f" * 63
    elif change == "citation_role":
        del mapping["citations"]["ports"]
    elif change == "blank_quote":
        mapping["citations"]["start"] = " \n"
    else:
        mapping["inferred"] = True

    with pytest.raises(ValueError, match="^WATCHDOG_POLICY_MAPPING_INCOMPLETE$"):
        watchdog.validate_contract(contract)


@pytest.mark.parametrize("bad_name", ["9watch", "with space", "tick;", "nexttime", "strong", ""])
def test_illegal_identifier_refuses_module_and_port_roles(tmp_path, bad_name):
    contract = _project(tmp_path)
    contract["module"] = bad_name
    with pytest.raises(ValueError, match="^WATCHDOG_IDENTIFIER_INVALID$"):
        watchdog.emit(contract)
    contract["module"] = "neutral_monitor"
    contract["ports"]["response_done"] = bad_name
    with pytest.raises(ValueError, match="^WATCHDOG_IDENTIFIER_INVALID$"):
        watchdog.emit(contract)


def test_duplicate_port_roles_refuse_instead_of_aliasing_independent_signals(tmp_path):
    contract = _project(tmp_path)
    contract["ports"]["response_done"] = contract["ports"]["request_valid"]
    with pytest.raises(ValueError, match="^WATCHDOG_PORT_ROLES_COLLIDE$"):
        watchdog.emit(contract)


@pytest.mark.parametrize("counter_bits,max_accepted,start", [
    (1, 1, "accepted"), (64, 1024, "first_valid")])
def test_supported_numeric_boundaries_emit_declared_dimensions_without_mutation(
        tmp_path, counter_bits, max_accepted, start):
    contract = _project(tmp_path, counter_bits=counter_bits, max_accepted=max_accepted, start=start)
    held = copy.deepcopy(contract)

    assert watchdog.validate_contract(contract) is contract
    rtl = watchdog.emit(contract)

    slots = max_accepted + int(start == "first_valid")
    assert f"input [{counter_bits - 1}:0] limit" in rtl
    assert f"reg [{counter_bits - 1}:0] wd_ages [0:{slots - 1}];" in rtl
    assert f"reg [{max(1, slots.bit_length()) - 1}:0] wd_count;" in rtl
    assert contract == held


def test_case_sensitive_ports_remain_public_and_repeated_internal_prefixes_do_not_shadow(tmp_path):
    contract = _project(tmp_path, module="Strong", ports={
        "clock": "wd_count", "reset": "wd__count", "request_valid": "wd___count",
        "request_ready": "take", "response_done": "finish", "threshold": "limit",
        "timed_out": "alarm", "violation": "fault"})

    rtl = watchdog.emit(contract)

    assert "module Strong (" in rtl
    assert "input wd_count, input wd__count" in rtl
    assert "input wd___count, input take" in rtl
    assert "reg [1:0] wd____count;" in rtl
    assert "always @(posedge wd_count)" in rtl
    assert "wire wd____start = wd___count && take;" in rtl

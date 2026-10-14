"""Behavioral coverage for source-bound requests and explicit factory routing."""
from argparse import Namespace
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_modes as em
import execution_step_protocol as protocol


def _request(tmp_path, **overrides):
    source = tmp_path / "provider.py"
    source.write_text("def prepare(request):\n    return request.step_id\n")
    values = dict(step_id="9", project=tmp_path, parameters={"top": "neutral"},
                  lease=tmp_path / "lease.json", record=tmp_path / "record.json",
                  source_sha="a" * 40, source_files={str(source): em.digest(source)})
    values.update(overrides)
    return protocol.StepRequest(**values)


def test_request_keeps_bound_inputs_and_detects_later_source_drift(tmp_path):
    parameters = {"top": "neutral"}
    request = _request(tmp_path, step_id=9, parameters=parameters)
    parameters["top"] = "caller_changed"
    assert request.step_id == "9"
    assert request.parameters == {"top": "neutral"}
    assert request.project == tmp_path
    assert request.lease == tmp_path / "lease.json"
    assert request.record == tmp_path / "record.json"
    request.check_source()
    Path(next(iter(request.source_files))).write_text("def prepare(request):\n    return None\n")
    with pytest.raises(em.Refusal) as caught:
        request.check_source()
    assert caught.value.code == "ADAPTER_SOURCE_MISMATCH"


@pytest.mark.parametrize("mutation,code", [
    ("relative_project", "STEP_REQUEST_PATH_UNBOUND"),
    ("symlink_project", "STEP_REQUEST_PATH_UNBOUND"),
    ("empty_sources", "INCOMPLETE_STEP_REQUEST"),
    ("short_sha", "INVALID_SOURCE_SHA"),
    ("uppercase_sha", "INVALID_SOURCE_SHA"),
    ("relative_source", "ADAPTER_SOURCE_MISMATCH"),
    ("symlink_source", "ADAPTER_SOURCE_MISMATCH"),
    ("missing_source", "ADAPTER_SOURCE_MISMATCH"),
    ("wrong_digest", "ADAPTER_SOURCE_MISMATCH"),
])
def test_request_refuses_unbound_or_changed_inputs(tmp_path, mutation, code):
    source = tmp_path / "bound.py"
    source.write_text("pass\n")
    changes = {}
    if mutation == "relative_project":
        changes["project"] = Path("relative")
    elif mutation == "symlink_project":
        link = tmp_path / "project_link"
        link.symlink_to(tmp_path, target_is_directory=True)
        changes["project"] = link
    elif mutation == "empty_sources":
        changes["source_files"] = {}
    elif mutation in ("short_sha", "uppercase_sha"):
        changes["source_sha"] = "a" * 39 if mutation == "short_sha" else "A" * 40
    elif mutation == "relative_source":
        changes["source_files"] = {"bound.py": em.digest(source)}
    elif mutation == "symlink_source":
        link = tmp_path / "source_link.py"
        link.symlink_to(source)
        changes["source_files"] = {str(link): em.digest(source)}
    elif mutation == "missing_source":
        changes["source_files"] = {str(tmp_path / "absent.py"): em.digest(source)}
    elif mutation == "wrong_digest":
        changes["source_files"] = {str(source): "0" * 64}
    with pytest.raises(em.Refusal) as caught:
        _request(tmp_path, **changes)
    assert caught.value.code == code


@dataclass
class _Options:
    root: Path
    enabled: bool


def test_parameters_round_trip_nested_paths_dataclasses_and_namespaces(tmp_path):
    parameters = {"options": _Options(tmp_path, True),
                  "namespace": Namespace(inputs=(tmp_path / "source.v", None)),
                  7: [1, 2.5, "text", False]}
    encoded = protocol.json_parameters(parameters)
    assert json.loads(json.dumps(encoded)) == {
        "options": {"root": str(tmp_path), "enabled": True},
        "namespace": {"inputs": [str(tmp_path / "source.v"), None]},
        "7": [1, 2.5, "text", False],
    }
    assert parameters["namespace"].inputs == (tmp_path / "source.v", None)


@pytest.mark.parametrize("unsupported", [object(), {1, 2}, _Options])
def test_parameters_refuse_objects_without_a_json_contract(unsupported):
    with pytest.raises(em.Refusal) as caught:
        protocol.json_parameters({"nested": [{"value": unsupported}]})
    assert caught.value.code == "PRODUCTION_PARAMETER_UNSERIALIZABLE"


def _prepare(request):
    return protocol.PreparedStep(request, "explicit-registry", consume=_consume,
                                 disposition="review", reason="source-only factory")


def _consume(value):
    return value


def _other_prepare(request):
    return None


def test_registered_factory_dispatches_request_and_preserves_prepared_contract(tmp_path):
    registry = protocol.FactoryRegistry()
    module = SimpleNamespace(STEP_IDS=("9", "10"), prepare=_prepare)
    registry.register(module)
    registry.register(module)
    request = _request(tmp_path)
    prepared = registry.prepare(request)
    assert prepared.context is request
    assert prepared.registry == "explicit-registry"
    assert prepared.consume("measured") == "measured"
    assert (prepared.disposition, prepared.reason) == ("review", "source-only factory")
    assert prepared.adoption_paths == ("project/phase2/stage2/synth", "producer.json",
                                       "native_commands.jsonl")
    assert registry.coverage() == {
        step: {"factory": "_prepare", "source": str(Path(__file__).resolve())}
        for step in ("9", "10")
    }


@pytest.mark.parametrize("module", [
    SimpleNamespace(STEP_IDS=(), prepare=_prepare),
    SimpleNamespace(STEP_IDS=["9"], prepare=_prepare),
    SimpleNamespace(STEP_IDS=("9",), prepare=None),
])
def test_incomplete_factory_cannot_be_registered(module):
    registry = protocol.FactoryRegistry()
    with pytest.raises(em.Refusal) as caught:
        registry.register(module)
    assert caught.value.code == "PRODUCTION_FACTORY_INCOMPLETE"
    assert registry.coverage() == {}


def test_duplicate_factory_refuses_and_leaves_original_route(tmp_path):
    registry = protocol.FactoryRegistry()
    registry.register(SimpleNamespace(STEP_IDS=("9",), prepare=_prepare))
    with pytest.raises(em.Refusal) as caught:
        registry.register(SimpleNamespace(STEP_IDS=("9",), prepare=_other_prepare))
    assert caught.value.code == "PRODUCTION_FACTORY_DUPLICATE"
    assert registry.prepare(_request(tmp_path)).registry == "explicit-registry"


def test_unregistered_route_cannot_guess_a_factory(tmp_path):
    registry = protocol.FactoryRegistry()
    registry.register(SimpleNamespace(STEP_IDS=("9",), prepare=_prepare))
    with pytest.raises(em.Refusal) as caught:
        registry.prepare(_request(tmp_path, step_id="unknown"))
    assert caught.value.code == "PRODUCTION_FACTORY_NOT_REGISTERED"


def test_imported_result_carries_consumer_values_without_execution_claim():
    assert protocol.imported_result(step_id="9", outputs=["netlist.v"]) == {
        "status": "IMPORTED", "step_id": "9", "outputs": ["netlist.v"]}

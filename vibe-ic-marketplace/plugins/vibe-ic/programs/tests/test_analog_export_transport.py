"""Full-population transport controls; fixture probes confer no native credit."""
from pathlib import Path
import copy
import json
import sys
import types

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_adapters_analog as analog
import execution_analog_installation as installation
import execution_modes as em
import execution_policy as policy
from programs.tests.test_execution_analog_export_integration import normal_export
from programs.tests.test_execution_analog_pdk_export import setup_export


def population(normal_export):
    project, args, _, _, _ = normal_export
    pdk = project.parent / 'container/foss/pdks/sky130A'
    # The existing fixture owns this declared literal population, not a hash
    # JSON prerequisite. The real helper copies and seals every referenced byte.
    models = pdk / 'libs.tech/ngspice/models'
    models.mkdir(parents=True, exist_ok=True)
    names = [f'current_declared_model_{index:03d}.lib' for index in range(365)]
    for name in names:
        (models / name).write_text('* supported correctness input only\n')
    (models / 'top.spice').write_text(''.join(f'.include "{name}"\n' for name in names))
    declaration = project / analog.DECLARATIONS[1]
    declaration.parent.mkdir(parents=True, exist_ok=True)
    declaration.write_text(json.dumps({'blocks': [{'name': 'unit'}]}))
    return project, installation.prepare_entry_request(project, args)


def test_canonical_binding_full_population_is_bounded_and_launches(normal_export):
    from programs.tests.test_analog_live_reachability_review import AnalogLiveReachabilityReview
    from programs.tests import test_execution_receipt_chain as live
    state = AnalogLiveReachabilityReview()
    state.setUp()
    try:
        project, request = population(normal_export)
        state.project = project
        issued = live.real_entry('IC', 'ultra', project)
        value = policy.configure(types.SimpleNamespace())
        registry = em.Registry()
        analog.register_adapters(registry, project=project, request=request)
        controller = em.Controller(registry, em.Budget(1, 512))
        runtime = dict(identity=(str(project), value['request_digest'], issued['route']['source_sha']),
            project=project, route=issued['route'], policy=value, registry=registry,
            controller=controller, parameters={}, contexts={}, bindings={}, runs={})
        policy._ordinary_runtime = runtime
        result = policy.dispatch_fixed_step(project, 'A7')
        binding = runtime['contexts']['A7'].binding()
        params = binding['objective']['parameters']
        assert json.loads(json.dumps(binding)) == binding
        assert len(json.dumps(binding).encode()) < 120 * 1024
        assert len(json.dumps(params).encode()) <= 8192
        assert 'pdk_export' not in params and 'model_hashes' not in params
        assert params['pdk_export_reference']['population_count'] >= 365
        assert set(request['model_hashes']).issubset(binding['inputs'])
        run = runtime['runs']['A7'][0]
        assert json.loads((run / 'issued-plan.json').read_text())['payload']['binding'] == binding
        receipt = json.loads((run / 'analog-a7/receipt.json').read_text())
        assert receipt['processes'][0].get('pid')
        print(json.dumps(dict(binding_bytes=len(json.dumps(binding).encode()),
            parameters_bytes=len(json.dumps(params).encode()),
            population_count=params['pdk_export_reference']['population_count'],
            worker_pid=receipt['processes'][0]['pid'], binding_roundtrip_equal=True,
            issued_plan_binding_equal=True, native_credit=False)))
        assert 'Argument list too long' not in receipt.get('detail', '')
        assert result['status'] in ('FAIL', 'NOT_MEASURED')
        assert not (run / 'selected').exists()
    finally:
        state.tearDown()


def test_staged_receipt_assets_and_reference_scope_refuse(normal_export):
    project, request = population(normal_export)
    params, _, _ = analog.installation_facts(project, 'A8',
        __import__('execution_synthesis_engines').source_sha(), analog.component_sources(), prepared=request)
    small = installation.transport_parameters(project, 'A8', params)
    reference = small['pdk_export_reference']
    inputs = {**request['model_hashes'],
        reference['output_root'] + '/_receipt.json': reference['receipt_sha256']}
    binding = dict(step_id='A8', objective=dict(original_project=str(project)), inputs=inputs)
    restored = installation.worker_parameters(project, binding, small)
    assert restored['model_hashes'] == request['model_hashes']
    assert restored['config_hashes'] == params['config_hashes']
    for path in (project / (reference['output_root'] + '/_receipt.json'),
                 project / next(iter(request['model_hashes']))):
        before = path.read_bytes()
        try:
            path.write_bytes(before + b'\n# edited current bytes\n')
            with pytest.raises(em.Refusal):
                installation.worker_parameters(project, binding, small)
        finally:
            path.write_bytes(before)
    for key, value in (('project', str(project.parent)), ('step_id', 'A6'),
                       ('image_id', 'sha256:' + 'c' * 64), ('receipt_digest', 'c' * 64),
                       ('population_digest', 'c' * 64), ('population_count', 1),
                       ('pdk_root_binding', {'canonical_root': '/outside'})):
        changed = copy.deepcopy(small)
        changed['pdk_export_reference'][key] = value
        with pytest.raises(em.Refusal):
            installation.worker_parameters(project, binding, changed)
    changed = copy.deepcopy(binding)
    changed['inputs'].pop(next(iter(request['model_hashes'])))
    with pytest.raises(em.Refusal, match='ANALOG_EXPORT_REFERENCE_INPUT_UNBOUND'):
        installation.worker_parameters(project, changed, small)

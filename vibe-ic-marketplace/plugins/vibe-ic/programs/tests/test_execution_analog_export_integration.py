"""Automatic callsite controls; local fixture processes confer no native credit."""
from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import types
from dataclasses import replace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_analog_installation as installation
import execution_adapters_analog as analog
import execution_analog_pdk_export as export
import analog_pdk_availability as resolver
import execution_modes as em
import _eda_pin as pin
from programs.tests.test_execution_analog_pdk_export import setup_export, IMAGE_REF, IMAGE_ID, MANIFEST


@pytest.fixture
def normal_export(setup_export, monkeypatch, tmp_path):
    project, pdk, _, calls = setup_export
    helper_transport = installation.lc.run_container
    binary = tmp_path / 'bin'
    binary.mkdir()
    for tool in installation.TOOL_COMMANDS:
        path = binary / tool
        path.write_text('#!' + sys.executable + '\nprint("correctness fixture; NOT_NATIVE_EDA")\n')
        path.chmod(0o755)
    image = dict(image_ref=IMAGE_REF, image_id=IMAGE_ID,
        image_manifest_digest=MANIFEST, image_repo_digests=[IMAGE_REF])
    monkeypatch.setattr(installation, 'inspect_image', lambda ref, **kw: image)
    monkeypatch.setattr(pin, 'pinned_image_present', lambda: (IMAGE_REF, ''))
    def no_named_container(*args, **kwargs):
        pytest.fail('normal entry used the named-container docker exec lister')
    monkeypatch.setattr(resolver, '_docker_lister', no_named_container)
    observed = []
    def supervised(argv, *, probe_deadline_s, env):
        assert 'run' in argv and 'exec' not in argv and IMAGE_REF in argv
        assert not any(k.startswith('VIBEIC_EXECUTION') for k in env)
        script = argv[argv.index('--skip') + 3]
        if script == export._CONTAINER_COPY:
            return helper_transport(argv, probe_deadline_s=probe_deadline_s, env=env)
        if script == installation.PROBE:
            command = [sys.executable, '-c', script, argv[-1]]
            env = {**env, 'PATH': str(binary)}
        else:
            local = pdk.parent / Path(argv[-1]).relative_to(resolver.DEFAULT_PDKS_ROOT)
            command = [sys.executable, '-c', script, str(local)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        stdout, stderr = process.communicate(timeout=20)
        observed.append(dict(pid=process.pid, rc=process.returncode, argv=argv))
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    monkeypatch.setattr(installation.lc, 'run_container', supervised)
    args = types.SimpleNamespace(pdk='sky130A', container='NO_SUCH_CONTAINER')
    return project, args, observed, binary, calls


def facts(project, request, step='A8'):
    from execution_synthesis_engines import source_sha
    return analog.installation_facts(project, step, source_sha(), analog.component_sources(), prepared=request)


def test_automatic_normal_entry_exports_and_qualifies_without_named_container(normal_export):
    project, args, observed, _, calls = normal_export
    original = (project / 'input/immutable.txt').read_bytes()
    request = installation.prepare_entry_request(project, args)
    assert request['_missing'] == []
    assert request['pdk_root'].startswith('reports/execution/analog-pdk-export/')
    assert not (project / 'input/analog_native.json').exists()
    assert (project / 'input/immutable.txt').read_bytes() == original
    assert observed and calls
    registry = em.Registry()
    analog.register_adapters(registry, project=project, request=request)
    for step in analog.STEP_IDS:
        assert 'input/analog_native.json' not in registry.adapters(step)[0].input_contract
    em.Controller(registry, em.Budget(1, 512))
    for step in ('A7', 'A8'):
        params, receipt, missing = facts(project, request, step)
        assert missing == [] and receipt['status'] == 'MEASURED'
        assert params['pdk_export']['image_id'] != params['pdk_export']['image_manifest_digest']
        assert params['pdk_export']['output_root'] + '/_receipt.json' in receipt['request']['input_hashes']
    assert facts(project, request, 'A6')[1] is None  # Missing decks stay A6-only NM.


def test_export_failure_stays_not_measured_and_unqualified(normal_export, monkeypatch):
    project, args, _, _, _ = normal_export
    monkeypatch.setattr(export, 'export_bounded_pdk', lambda *a, **kw:
        dict(status='NOT_MEASURED', not_measured=['PDK_FILE_MISSING']))
    request = installation.prepare_entry_request(project, args)
    assert request['_missing'] == ['PDK_FILE_MISSING']
    assert facts(project, request)[1] is None
    assert not (project / 'phase3').exists()


def test_edited_exported_bytes_refuse_current_installation(normal_export):
    project, args, _, _, _ = normal_export
    request = installation.prepare_entry_request(project, args)
    assert facts(project, request)[1]['status'] == 'MEASURED'
    copied = project / next(iter(request['model_hashes']))
    copied.write_bytes(copied.read_bytes() + b'\n# changed current copied model\n')
    _, receipt, missing = facts(project, request)
    assert receipt is None and 'PDK_EXPORT_UNAVAILABLE:ANALOG_PDK_EXPORT_OUTPUT_CHANGED' in missing
    assert not (project / 'phase3').exists()


def test_export_image_mismatch_and_arbitrary_report_root_refuse(normal_export):
    project, args, _, _, _ = normal_export
    request = installation.prepare_entry_request(project, args)
    changed = copy.deepcopy(request)
    changed['_step_exports']['A8']['image_id'] = 'sha256:' + 'c' * 64
    assert facts(project, changed)[1] is None
    arbitrary = copy.deepcopy(request)
    arbitrary.pop('_step_exports')
    arbitrary['pdk_root'] = 'reports/unissued-pdk'
    with pytest.raises(em.Refusal, match='ANALOG_DECLARED_MANIFEST_INVALID'):
        facts(project, arbitrary)


def test_missing_tool_stays_nm_and_measured_fail_dominates(normal_export):
    project, args, _, binary, _ = normal_export
    request = installation.prepare_entry_request(project, args)
    (binary / 'ngspice').unlink()
    assert facts(project, request)[1] is None
    (binary / 'magic').write_text('#!' + sys.executable + '\nprint("measured fixture failure")\nraise SystemExit(1)\n')
    params, receipt, missing = facts(project, request)
    assert receipt['status'] == 'FAIL' and params['installation_verdict'] == 'FAIL'
    assert missing == ['MEASURED_INSTALLATION_FAIL']
    assert not (project / 'phase3').exists()


def test_ordinary_controller_without_deployment_json_enters_existing_producer_and_keeps_fail(normal_export):
    """Supported supervised correctness process; deliberately no native credit."""
    import execution_policy as policy
    from programs.tests.test_analog_live_reachability_review import AnalogLiveReachabilityReview
    from programs.tests import test_execution_receipt_chain as live
    project, args, _, _, _ = normal_export
    state = AnalogLiveReachabilityReview()
    state.setUp()
    state.project = project
    try:
        issued = live.real_entry('IC', 'ultra', project)
        value = policy.configure(types.SimpleNamespace())
        block = project / 'phase3/analog/unit'
        block.mkdir(parents=True)
        (block.parent / 'analog_block_list.json').write_text(json.dumps({'blocks': [{'name': 'unit'}]}))
        (block / 'unit.gds').write_bytes(b'correctness fixture only; no GDS qualification')
        (block / 'unit.sp').write_text('.subckt unit a b\nR1 a b 1k\n.ends unit\n')
        (project / 'input/correctness.json').write_text(json.dumps(dict(widths=[0], minimum=1,
            expected_nodes=['a', 'b'], observed_nodes=['a', 'b'], missing_lvs=True)))
        request = installation.prepare_entry_request(project, args)
        registry = em.Registry()
        analog.register_adapters(registry, project=project, request=request)
        arm = registry.adapters('A6')[0]
        assert 'input/analog_native.json' not in arm.input_contract
        # The existing fixture owns its synthetic DRC/LVS process contract;
        # it does not qualify the image, decks or silicon.
        fixture = PROGRAMS / 'tests/fixtures/analog_correctness_process.py'
        sources = dict(arm.source_files, **{str(fixture): em.digest(fixture)})
        sources.update({str(p): em.digest(p) for p in em._source_closure(sources)})
        arm = replace(arm, available=True, qualified=False,
            qualification_evidence='SUPPORTED_CORRECTNESS_PROCESS_ONLY; NO_NATIVE_CREDIT',
            source_files=sources,
            objective=dict(arm.objective, source_manifest_sha256=em._hash(sources)),
            components=(em.Component('correctness-producer', (str(Path(sys.executable).resolve()),
                str(fixture), '--inputs', '{inputs}', '--outputs', '{outputs}'), 30),
                *analog.gate_components('A6')),
            input_contract=(*arm.input_contract, 'input/correctness.json'))
        installed = em.Registry()
        installed.register(arm)
        controller = em.Controller(installed, em.Budget(1, 512))
        runtime = dict(identity=(str(project), value['request_digest'], issued['route']['source_sha']),
            project=project, route=issued['route'], policy=value, registry=installed,
            controller=controller, parameters={}, contexts={}, bindings={}, runs={})
        policy._ordinary_runtime = runtime
        result = policy.dispatch_fixed_step(project, 'A6')
        assert result['status'] == 'FAIL'
        root = runtime['runs']['A6'][0]
        calls = json.loads((root / 'analog-a6/outputs/producer-calls/calls.json').read_text())
        assert calls[0]['entrypoint'] == 'analog_a6_native_pv.run_block_pv'
        assert calls[0]['result']['drc']['verdict'] == 'FAIL'
        assert 'input/analog_native.json' not in runtime['contexts']['A6'].binding()['inputs']
        assert runtime['contexts']['A6'].objective['parameters']['entry_request_sha256'] == em._hash(request)
        assert not (root / 'selected').exists()
        assert not (block / 'drc.report').exists()
    finally:
        state.tearDown()

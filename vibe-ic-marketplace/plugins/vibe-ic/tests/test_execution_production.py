"""Source protocol controls: real Python children, no Docker or EDA."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1] / 'programs'
sys.path.insert(0, str(PROGRAMS))


def test_frontdoor_child_receives_ultra_and_budget(tmp_path, monkeypatch):
    import vibe_ic_one_shot_runner as front
    monkeypatch.setenv('VIBEIC_EXECUTION_REQUEST', json.dumps(dict(
        mode='ultra', cpus=1, ram_mb=512, choice_wait_s=0)))
    output = tmp_path / 'argv.json'
    child = tmp_path / 'neutral.py'
    child.write_text('import sys,json,pathlib,argparse\n'
                     'p=argparse.ArgumentParser(); p.add_argument("--execution-mode",default="native-direct")\n'
                     'a,unknown=p.parse_known_args(sys.argv[2:])\n'
                     'pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(argv=sys.argv[2:],mode=a.execution_mode)))\n')
    rc = front._run_phase('PROTOCOL', child, [str(output)])
    actual = json.loads(output.read_text())
    print(json.dumps(dict(rc=rc, observed_argv=actual)))
    assert rc == 0
    assert actual['mode'] == 'ultra'
    assert actual['argv'] == ['--execution-mode', 'ultra', '--execution-cpus', '1',
                      '--execution-ram-mb', '512', '--execution-choice-wait-s', '0']


def test_default_request_and_conflicting_child(monkeypatch):
    import execution_policy as policy
    from execution_modes import Refusal
    monkeypatch.delenv(policy.ENV, raising=False)
    assert policy.request()['mode'] == 'default'
    with pytest.raises(Refusal, match='CHILD_EXECUTION_POLICY_CONFLICT'):
        policy.child_arguments(['--execution-mode', 'ultra'])


def test_actual_production_worker_native_input_failure_is_not_rc0_pass(tmp_path):
    import execution_modes as em
    import execution_production as production
    from execution_resource_lease import host_lease
    from _atomic_artefact import write_json
    inputs, outputs = tmp_path / 'inputs', tmp_path / 'outputs'
    inputs.mkdir(); outputs.mkdir()
    neutral = inputs / 'project/input/neutral.txt'
    neutral.parent.mkdir(parents=True)
    neutral.write_text('neutral transport input; no design or native-tool claim\n')
    source_sha, source_files = production.source_identity()
    binding = dict(source_sha=source_sha, required_gates=['synth_netlist_check'],
                   inputs={'project/input/neutral.txt': em.digest(neutral)})
    with host_lease(em.Budget(1, 512, workers=1)) as lease:
        spec = dict(source_sha=source_sha, source_files=source_files, top='neutral',
                    original_project=str(tmp_path / 'original'), original_pdk_root=str(tmp_path / 'pdk'),
                    pdk=dict(name='neutral', liberty='', tech_lef='', cell_lef='', cell_gds=None,
                             site='', drc_deck=None), image_id='NOT_MEASURED', lease=str(lease),
                    input_hashes={'project/input/neutral.txt': em.digest(neutral)})
        write_json(inputs / 'request.json', spec)
        env = {**os.environ, 'VIBEIC_EXECUTION_BINDING': json.dumps(binding)}
        command = [sys.executable, str(PROGRAMS / 'execution_native_worker.py'),
                   '--inputs', str(inputs), '--outputs', str(outputs)]
        cp = subprocess.run(command, env=env, capture_output=True, text=True, start_new_session=True)
        assert cp.returncode == 0, cp.stderr
        producer = json.loads((outputs / 'producer.json').read_text())
        evidence = production.validate_synthesis(outputs, binding)
        print(json.dumps(dict(argv=command, rc=cp.returncode, native_status=producer['status'],
                              adopted_gate_verdict=evidence.verdict, detail=producer['detail'])))
        assert producer['native_entrypoint'] == 'phase3_one_shot_runner._step_synth_librelane'
        assert producer['status'] == 'FAIL'
        assert evidence.verdict == 'FAIL'
        assert evidence.gates == {'synth_netlist_check': 'NOT_MEASURED'}


def test_real_primary_handoff_gate_consumes_changed_artifact(tmp_path):
    import execution_production as production
    from _atomic_artefact import write_json
    native = tmp_path / 'netlist.v'
    native.write_text('module neutral(input a, output b); wire n; endmodule\n')
    config = tmp_path / 'config.json'
    write_json(config, dict(SYNTH_TIEHI_CELL='TIEHI/Y', SYNTH_TIELO_CELL='TIELO/Y'))
    first = tmp_path / 'first'; second = tmp_path / 'second'
    first.mkdir(); second.mkdir()
    before = production._gate('synth_handoff_netlist_check',
        ['--netlist', str(native), '--resolved', str(config)], first)
    native.write_text("module neutral(output b);\nassign b = 1'b0;\nendmodule\n")
    after = production._gate('synth_handoff_netlist_check',
        ['--netlist', str(native), '--resolved', str(config)], second)
    # This is one real gate's verdict only, not a producer or EDA PASS.
    print(json.dumps(dict(before=before, after=after)))
    assert before == 'PASS'
    assert after == 'FAIL'


def test_native_boundary_has_actual_oci_budget_and_readonly_inputs(tmp_path):
    from execution_resource_lease import bounded_argv, start_ticks, LABEL
    from _atomic_artefact import write_json
    write_json(tmp_path / 'lease.json', dict(nonce='neutral', parent_pid=os.getpid(),
        parent_start_ticks=start_ticks(os.getpid()), cpus=1, ram_mb=512, container_ram_mb=256))
    write_json(tmp_path / 'worker.json', dict(readonly_mounts=[['/neutral/source', '/neutral/input']]))
    actual = bounded_argv(['docker', 'run', '--memory', '999m', '--memory-swap', '999m',
                           '--rm', '--entrypoint', 'python3', 'captured-native-image', '-m', 'librelane.steps'], tmp_path)
    print(json.dumps(dict(captured_native_argv=actual)))
    assert actual[actual.index('--memory') + 1] == '256m'
    assert actual[actual.index('--memory-swap') + 1] == '256m'
    assert actual[actual.index('--cpus') + 1] == '1'
    assert actual[actual.index('--label') + 1] == LABEL + '=neutral'
    assert '/neutral/source:/neutral/input:ro' in actual
    assert actual[-3:] == ['captured-native-image', '-m', 'librelane.steps']


def test_rc0_unmeasured_provenance_and_all70_catalog():
    import execution_production as production
    assert production.gate_verdict('provenance_check', 0,
        dict(ok=True, checks=[{}], unmeasured=['neutral.v'], uncalibrated=[])) == 'NOT_MEASURED'
    assert production.gate_verdict('provenance_check', 1,
        dict(ok=True, checks=[{}], unmeasured=[], uncalibrated=[])) == 'FAIL'
    rows = production.catalog()['steps']
    assert len(rows) == 70
    assert sum(row['status'] == 'IMPLEMENTED_UNQUALIFIED' for row in rows) == 1
    assert sum(row['status'] == 'NOT_IMPLEMENTED' for row in rows) == 69
    assert set(row['runtime'] for row in rows) == {'NOT_MEASURED'}


def test_step9_uses_dispatch_and_preserves_dual(tmp_path, monkeypatch):
    import execution_production as production
    import phase3_one_shot_runner as phase3
    import librelane_contract as lc
    calls = []
    # Capture the source dispatch boundary, never stand in a semantic PASS.
    def capture(*args):
        calls.append('production')
        return phase3.StepResult('synth', 'FAIL', detail='neutral captured boundary')
    monkeypatch.setattr(production, 'dispatch_synthesis', capture)
    monkeypatch.setattr(lc, 'selected_mode', lambda *args: 'direct')
    result = phase3.step_synth(tmp_path, 'neutral', None, 'unused')
    assert calls == ['production']
    assert result.status == 'FAIL'
    monkeypatch.setattr(lc, 'selected_mode', lambda *args: 'dual')
    result = phase3.step_synth(tmp_path, 'neutral', None, 'unused')
    assert calls == ['production']
    assert result.status == 'FAIL'
    assert 'LL_DUAL_POSTROUTE_NOT_READY' in result.detail


def test_native_run_boundary_uses_real_child_rc_with_captured_docker(tmp_path, monkeypatch):
    import librelane_contract as lc
    from execution_resource_lease import host_lease, native_boundary
    from execution_modes import Budget
    captured = tmp_path / 'native-argv.json'
    probe = tmp_path / 'neutral_native_capture.py'
    probe.write_text('import pathlib,json,sys\n'
        'pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n'
        'raise SystemExit(17)\n')
    actual_run = subprocess.run
    def native_capture(argv, **kwargs):
        # This replaces ONLY the external Docker boundary with a real neutral
        # executable, preserving the native source-built argv and actual wait rc.
        return actual_run([sys.executable, str(probe), str(captured), *argv], **kwargs)
    monkeypatch.setattr(lc.subprocess, 'run', native_capture)
    with host_lease(Budget(1, 512, workers=1)) as lease:
        with native_boundary(lease):
            observed = lc.run_container(['docker', 'run', '--rm', '--entrypoint', 'python3',
                'captured-native-image', '-m', 'librelane.steps', 'run', '--id', 'Yosys.Synthesis'],
                probe_deadline_s=2)
        argv = json.loads(captured.read_text())
        print(json.dumps(dict(native_argv=argv, actual_neutral_child_rc=observed.returncode)))
        assert observed.returncode == 17
        assert argv[argv.index('--cpus') + 1] == '1'
        assert argv[argv.index('--memory') + 1] == '256m'
        assert argv[-2:] == ['--id', 'Yosys.Synthesis']
        assert argv[argv.index('--cidfile') + 1].startswith(str(lease))
        assert '--name' in argv


def test_resource_recovery_reaps_exact_owned_child(tmp_path):
    from execution_resource_lease import recover, start_ticks, OWNER_ENV
    from _atomic_artefact import write_json
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True,
                             env={**os.environ, OWNER_ENV: 'neutral-recovery'})
    try:
        write_json(tmp_path / 'lease.json', dict(nonce='neutral-recovery'))
        write_json(tmp_path / 'worker.json', dict(pid=child.pid, start_ticks=start_ticks(child.pid)))
        recover(tmp_path)
        rc = child.wait(timeout=3)
        print(json.dumps(dict(owned_child_pid=child.pid, actual_recovery_rc=rc)))
        assert rc < 0
        assert json.loads((tmp_path / 'closed.json').read_text())['recovered'] is True
    finally:
        if child.poll() is None:
            child.kill(); child.wait()


def test_owned_cid_recovery_checks_label_before_removal(tmp_path, monkeypatch):
    import execution_resource_lease as lease
    from execution_modes import Refusal
    from _atomic_artefact import write_json
    cid = 'a' * 64
    write_json(tmp_path / 'lease.json', dict(nonce='owner'))
    (tmp_path / 'native.cid').write_text(cid)
    calls = []
    def captured(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({lease.LABEL: 'different-owner'}), '')
    monkeypatch.setattr(lease, '_command', captured)
    with pytest.raises(Refusal, match='RESOURCE_CONTAINER_OWNER_MISMATCH'):
        lease.recover(tmp_path)
    assert len(calls) == 1
    assert calls[0][1] == 'inspect'
    assert not (tmp_path / 'closed.json').exists()


def test_execution_request_changes_actual_span_identity(tmp_path, monkeypatch):
    import canonical_run_admission as admission
    import execution_policy as policy
    monkeypatch.setenv(policy.ENV, json.dumps(dict(mode='default', cpus=1, ram_mb=512)))
    first = admission.build_identity(tmp_path, 'phase3', container_image='captured-image',
                                     config={}, program_paths=[PROGRAMS / 'execution_policy.py'])
    monkeypatch.setenv(policy.ENV, json.dumps(dict(mode='ultra', cpus=1, ram_mb=512)))
    second = admission.build_identity(tmp_path, 'phase3', container_image='captured-image',
                                      config={}, program_paths=[PROGRAMS / 'execution_policy.py'])
    assert first['dispatch_config']['execution_request']['mode'] == 'default'
    assert second['dispatch_config']['execution_request']['mode'] == 'ultra'
    assert admission.identity_sha256(first) != admission.identity_sha256(second)


def test_host_admission_lock_is_shared_with_real_other_parent(tmp_path):
    from execution_resource_lease import host_lease
    from execution_modes import Budget
    command = 'from execution_resource_lease import host_lease; from execution_modes import Budget; '\
              'with_context=host_lease(Budget(1,512,workers=1)); with_context.__enter__()'
    with host_lease(Budget(1, 512, workers=1)):
        child = subprocess.run([sys.executable, '-c', command],
                               env={**os.environ, 'PYTHONPATH': str(PROGRAMS)}, capture_output=True, text=True)
    print(json.dumps(dict(actual_other_parent_rc=child.returncode, detail=(child.stderr.splitlines() or [''])[-1])))
    assert child.returncode == 1
    assert 'RESOURCE_HOST_BUSY' in child.stderr


def test_real_step9_factory_controller_worker_failure_blocks_import(tmp_path, monkeypatch):
    import phase3_one_shot_runner as phase3
    import librelane_contract as lc
    import librelane_image_facts as facts
    import execution_policy as policy
    from _atomic_artefact import write_json
    project = tmp_path / 'project'
    rtl = project / 'phase2/stage1/rtl/neutral.v'
    rtl.parent.mkdir(parents=True)
    rtl.write_text('module neutral(input a, output b); assign b=a; endmodule\n')
    pdk_root = tmp_path / 'pdk'
    pdk_root.mkdir()
    liberty = pdk_root / 'neutral.lib'
    liberty.write_text('library(neutral) {}\n')
    switch = project / 'phase3/librelane_switch.json'
    switch.parent.mkdir(parents=True)
    image = 'sha256:' + 'a' * 64
    write_json(switch, dict(image=image, pdk_root_host=str(pdk_root)))
    monkeypatch.setenv(policy.ENV, json.dumps(dict(mode='ultra', cpus=1, ram_mb=512, choice_wait_s=0)))
    # Capture the external image inventory/version boundary only. Actual source
    # factory, component argv, Popen/wait, native helper and validator all run.
    monkeypatch.setattr(facts, 'image_facts', lambda *args, **kwargs: dict(
        image_id=image, librelane_version='captured-external-boundary',
        flows={'Chip': ['Yosys.JsonHeader', 'Yosys.Synthesis', 'Checker.YosysUnmappedCells',
                        'Checker.YosysSynthChecks', 'Checker.NetlistAssignStatements']},
        test_scope='PROTOCOL_CAPTURE_ONLY_NOT_EDA'))
    monkeypatch.setattr(lc, 'run_container', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, 'captured native version boundary\n', ''))
    pdk = phase3.PdkConfig('neutral', str(liberty), '', '', None, '', None)
    canonical = project / 'phase2/stage2/synth/neutral_synth.v'
    canonical.parent.mkdir(parents=True)
    canonical.write_text('prior canonical bytes\n')
    result = phase3.step_synth(project, 'neutral', pdk, 'unused')
    receipts = list((project / 'phase3/execution_modes').rglob('receipt.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    print(json.dumps(dict(phase3_status=result.status, arm_status=receipt['status'],
        actual_pid=receipt['processes'][0]['pid'], actual_rc=receipt['processes'][0]['rc'],
        source_bound_native_detail=receipt['evidence']['detail'])))
    assert result.status == 'FAIL'
    assert receipt['status'] == 'FAIL'
    assert receipt['processes'][0]['rc'] == 0
    assert receipt['processes'][0]['pid'] > 0
    assert receipt['reason'] == 'GATE_FAIL'
    assert canonical.read_text() == 'prior canonical bytes\n'
    assert not list((project / 'phase3/execution_modes').rglob('import.json'))


def test_editable_pid_row_cannot_claim_an_unowned_real_child(tmp_path):
    from execution_resource_lease import recover, start_ticks, OWNER_ENV
    from execution_modes import Refusal
    from _atomic_artefact import write_json
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
        start_new_session=True, env={**os.environ, OWNER_ENV: 'actual-kernel-owner'})
    try:
        write_json(tmp_path / 'lease.json', dict(nonce='forged-json-owner'))
        write_json(tmp_path / 'worker.json', dict(pid=child.pid, start_ticks=start_ticks(child.pid)))
        observed = 'RECOVERED'
        try:
            recover(tmp_path)
        except Refusal as exc:
            observed = exc.code
        print(json.dumps(dict(ownership_observation=observed, child_rc=child.poll())))
        assert observed == 'RESOURCE_WORKER_OWNER_UNBOUND'
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.kill(); child.wait()


def test_unimplemented_actuator_returns_typed_not_measured(tmp_path, monkeypatch):
    import execution_policy as policy
    import execution_production as production
    from verdict import ReasonClass
    monkeypatch.setenv(policy.ENV, json.dumps(dict(mode='default', cpus=1, ram_mb=512)))
    result = production.dispatch_synthesis(tmp_path, 'neutral', None, 'unused', period_relax=2.0)
    assert result.status == 'NOT_MEASURED'
    assert result.reason_class == ReasonClass.NOT_EXECUTED
    assert 'PRODUCTION_PERIOD_RELAX_NOT_IMPLEMENTED' in result.detail

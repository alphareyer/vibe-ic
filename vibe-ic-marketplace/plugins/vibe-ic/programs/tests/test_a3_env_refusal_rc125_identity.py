"""A3 must identify returned image refusals at its supervised launch boundary."""
import json
import subprocess

import pytest
import _plugin_tree  # noqa: F401
import _container_exec as CE
import analog_a3_netlist_emit as A3


@pytest.mark.parametrize('stderr', ['', 'container command could not start'])
def test_an_unmarked_125_is_not_a_measured_image_mismatch(monkeypatch, stderr):
    got = _launch_answer(monkeypatch, stderr)
    assert got['simulation_status'] == 'SIMULATION_INVOCATION_FAILED', got
    assert not got.get('env_refused'), got
    assert got['simulation_status'] != A3.CONTAINER_IMAGE_MISMATCH_STATUS
    assert got['simulation_verified'] is False


def test_a_marked_125_still_carries_the_measured_refusal(monkeypatch):
    reason = 'CONTAINER_IMAGE_MISMATCH: required digest A, observed digest B'
    got = _launch_answer(monkeypatch, CE.IMAGE_REFUSAL_MARK + reason)
    assert got['env_refused'] is True, got
    assert got['simulation_status'] == A3.CONTAINER_IMAGE_MISMATCH_STATUS
    assert reason in got['detail']


def _launch_answer(monkeypatch, stderr, rc=125):
    monkeypatch.setattr(A3.shutil, 'which', lambda _: '/not-executed/docker')
    monkeypatch.setattr(A3, '_docker_ok', lambda _: True)
    monkeypatch.setattr(A3, 'container_image_refusal', lambda _: '')
    monkeypatch.setattr(CE, 'docker_exec_argv', lambda *a, **k: ['probe-supplied'])
    monkeypatch.setattr(A3._pr, 'run_best_effort',
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, 'yes', ''))
    monkeypatch.setattr(CE, 'run_in_container_supervised',
                        lambda *a, **kw: subprocess.CompletedProcess(['launch-supplied'], rc, '', stderr))
    return A3.verify_with_ngspice('own-control', 'block', '* block', '* testbench')


@pytest.mark.parametrize('mismatch,expected_rc', [(True, 69), (False, 0)])
def test_image_identity_changes_the_actual_producer_exit_value(tmp_path, mismatch, expected_rc):
    from test_a3_env_refusal_is_its_own_verdict import (
        LDO_SPEC, OTHER_DIGEST, _stub_docker, _run_a3)
    from _analog_producer_fixture import A1, A2, block, make_project, run_prog
    import _eda_pin as pin
    project = make_project(tmp_path/'project', [block('vreg_alpha', 'ldo', LDO_SPEC)])
    assert run_prog(A1, project).returncode == 0
    assert run_prog(A2, project).returncode == 0
    stub = _stub_docker(tmp_path, OTHER_DIGEST if mismatch else pin.IMAGE_DIGEST)
    cp = _run_a3(project, stub, '--verify-sim')
    assert cp.returncode == expected_rc, (cp.stdout, cp.stderr)


@pytest.mark.parametrize('marked,expected_rc', [(False, 0), (True, 69)])
def test_supervised_125_identity_reaches_the_actual_cli(tmp_path, marked, expected_rc):
    from test_a3_env_refusal_is_its_own_verdict import (
        LDO_SPEC, _stub_docker, _run_a3)
    from _analog_producer_fixture import A1, A2, block, make_project, run_prog, bdir
    import _eda_pin as pin
    project = make_project(tmp_path/'project', [block('vreg_alpha', 'ldo', LDO_SPEC)])
    assert run_prog(A1, project).returncode == 0
    assert run_prog(A2, project).returncode == 0
    stub = _stub_docker(tmp_path, pin.IMAGE_DIGEST)
    reason = 'CONTAINER_IMAGE_MISMATCH: required digest A, observed digest B'
    stderr = CE.IMAGE_REFUSAL_MARK + reason if marked else 'container command could not start'
    docker = stub/'docker'
    docker.write_text(docker.read_text().replace(
        "if a and a[0] == 'exec':\n",
        "if a and a[0] == 'exec':\n"
        "    if 'ngspice -b ' in ' '.join(a):\n"
        f"        print({stderr!r}, file=sys.stderr)\n"
        "        sys.exit(125)\n").replace("print('no')", "print('yes')"))
    report = tmp_path/'report.json'
    cp = _run_a3(project, stub, '--verify-sim', '--json', str(report))
    calls = (tmp_path/'docker_calls.log').read_text()
    assert 'ngspice -b ' in calls, calls
    assert cp.returncode == expected_rc, (cp.stdout, cp.stderr)
    rep = json.loads(report.read_text())
    if marked:
        assert rep['verdict'] == A3.CONTAINER_IMAGE_MISMATCH
        assert reason in rep['reason']
        assert not (bdir(project, 'vreg_alpha')/'vreg_alpha.sp').exists()
    else:
        assert rep['verdict'] == 'EMITTED'
        provenance = json.loads((bdir(project, 'vreg_alpha')/'netlist_provenance.json').read_text())
        sim = provenance['verification']['simulation']
        assert sim['simulation_status'] == 'SIMULATION_INVOCATION_FAILED'
        assert sim['simulation_verified'] is False
        assert 'ENV_REFUSED:' not in cp.stderr


@pytest.mark.parametrize('rc,status', [
    (124, 'SIMULATION_STOPPED_EXTERNALLY'), (127, 'SIMULATION_INVOCATION_FAILED'),
])
def test_other_supervised_exit_codes_keep_their_own_cause(monkeypatch, rc, status):
    got = _launch_answer(monkeypatch, 'shell diagnostic', rc)
    assert got['simulation_status'] == status, got
    assert got['ngspice_rc'] == rc
    assert not got.get('env_refused')
    if rc == 127:
        assert 'could not execute' in got['detail']
        assert 'timeout' not in got['detail']

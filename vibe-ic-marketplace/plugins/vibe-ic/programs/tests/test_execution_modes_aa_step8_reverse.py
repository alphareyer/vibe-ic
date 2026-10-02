"""Prompt-derived review controls. No production source is edited.

Run with VIBEIC_REVIEW_REPO set to the exact clean candidate or parent clone.
Assertions express required refusal behavior; a failing assertion is a finding.
"""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

REPO = Path(__file__).resolve().parents[5]
PROGRAMS = REPO / 'vibe-ic-marketplace/plugins/vibe-ic/programs'
sys.path[:0] = [str(PROGRAMS), str(PROGRAMS.parent), str(PROGRAMS / 'tests')]
import execution_modes as em
import execution_frontend_providers as providers
import execution_frontend_worker as worker
from programs.tests import test_execution_modes as H
from programs.tests import test_execution_modes_issued_adoption as I

EVIDENCE = Path('/tmp/aa-step8-r4-reverse-evidence')
EVIDENCE.mkdir(parents=True, exist_ok=True)


def record(name, **values):
    (EVIDENCE / (name + '.json')).write_text(json.dumps(values, indent=2, default=str) + '\n')


def caller_envelope(payload):
    # Caller computes this using only public bytes, without an issuer secret.
    content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
    return {'schema': 'execution-authority/v2', 'authority': 'controller',
            'payload': payload, 'payload_sha256': content_hash}


def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, sort_keys=True) + '\n')


def adopted(c, ctx, root, arm='frontend_8'):
    try:
        choice = {'arm_id': arm, 'binding': ctx.binding(),
                  'receipt_sha256': em.digest(root / arm / 'receipt.json'),
                  'reviewer': 'independent adversarial control',
                  'rationale': 'challenge caller-manufactured authority'}
        return c.adopt(ctx, root, choice)['status']
    except em.Refusal as exc:
        return 'REFUSED:' + exc.code


def inputs(tmp_path, *, bad=False, rtl=False, pvt=True):
    project = tmp_path / 'original'
    sdc = project / 'phase2/stage2/constraints/top.sdc'
    sdc.parent.mkdir(parents=True)
    sdc.write_text(('create_clock -period -10 [get_ports clk]\n' if bad else
                    'create_clock -period 10 [get_ports clk]\n') +
                   'set_input_delay 1 -clock clk [all_inputs]\n'
                   'set_output_delay 1 -clock clk [all_outputs]\n')
    l8 = project / 'phase1/generated_docs/L8_TIMING_WAVEFORM.json'
    write(l8, {'clocks': {'clk': {'period_ns': 10}}})
    files = {str(p.relative_to(project)): p for p in (sdc, l8)}
    if pvt:
        matrix = project / 'phase2/stage2/constraints/pvt_matrix.json'
        write(matrix, {'corners': []})
        files[str(matrix.relative_to(project))] = matrix
    if rtl:
        design = project / 'phase2/stage1/rtl/top.v'
        design.parent.mkdir(parents=True)
        design.write_text('module top(input clk, output reg div_clk);\n'
                          'always @(posedge clk) div_clk <= ~div_clk;\nendmodule\n')
        files[str(design.relative_to(project))] = design
    return project, files


def setup(tmp_path, **kw):
    project, files = inputs(tmp_path, **kw)
    registry = em.Registry()
    providers.register_factories(registry)
    portfolio = em.load_portfolio()
    row = next(s for s in portfolio['steps'] if s['id'] == '8')
    sha = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    ctx = em.Context('8', sha, files, {'metric': 'source_boundary'},
                     tuple(row['mandatory_gate_programs']))
    return project, ctx, registry.adapters('8')[0], em.Controller(registry, em.Budget(1, 512, 1), portfolio)


def legacy_manifest(project, files):
    write(project / 'input/issued_manifest.json',
          {'step_id': '8', 'parameters': {},
           'files': {name: em.digest(path) for name, path in files.items()}})


def test_caller_rehashed_completion_cannot_adopt(tmp_path):
    c, ctx, root = I.failed_measurement(tmp_path)
    path = root / 'a/issued-completion.json'
    envelope = json.loads(path.read_text())
    payload = envelope['payload']
    actual_rc = payload['processes'][-1]['rc']
    payload['processes'][-1]['rc'] = 0
    payload['actual_status'] = 'ELIGIBLE'
    payload['actual_reason'] = 'ADAPTER_EVIDENCE'
    write(path, caller_envelope(payload))
    status = adopted(c, ctx, root, 'a')
    record('rehashed-completion', measured_rc=actual_rc, claimed_rc=0, actual=status, run_root=root)
    assert actual_rc == 7
    assert status.startswith('REFUSED:'), status


def test_step8_caller_env_and_fabricated_transcript_cannot_adopt(tmp_path):
    project, ctx, arm, c = setup(tmp_path)
    root = tmp_path / 'fabricated-run'
    frozen, outputs = root / arm.arm_id / 'inputs', root / arm.arm_id / 'outputs'
    frozen.mkdir(parents=True)
    outputs.mkdir()
    plan = c.plan(ctx)  # Planning is read-only; Controller.run is never called.
    plan.update(run_id=uuid.uuid4().hex, budget=asdict(c.budget),
                public_portfolio=c.portfolio, public_portfolio_sha256=em._hash(c.portfolio),
                run_root=str(root), superiority=None)
    write(root / 'plan.json', plan)
    write(root / 'issued-plan.json', caller_envelope(plan))
    for name, source in ctx.inputs.items():
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o444)
    manifest = {'schema': 'execution-issued-manifest/v2', 'step_id': '8',
                'route': list(arm.route), 'parameters': dict(ctx.objective),
                'files': dict(ctx.binding()['inputs']), 'source_sha': arm.source_sha,
                'source_tree_sha': arm.source_tree_sha,
                'controller_sha256': ctx.binding()['controller_sha256'],
                'plan_sha256': em._hash(plan)}
    write(frozen / 'issued_manifest.json', manifest)
    env = {**os.environ, 'VIBEIC_MANIFEST_SHA256': em.digest(frozen / 'issued_manifest.json'),
           'VIBEIC_CANONICAL_ROUTE': json.dumps(list(arm.route)),
           'VIBEIC_SOURCE_SHA': arm.source_sha, 'VIBEIC_SOURCE_TREE_SHA': arm.source_tree_sha,
           'VIBEIC_EXECUTION_BINDING': json.dumps(ctx.binding()), 'PYTHONDONTWRITEBYTECODE': '1'}
    argv = [sys.executable, str(PROGRAMS / 'execution_frontend_worker.py'),
            '--step', '8', '--inputs', str(frozen), '--outputs', str(outputs)]
    cp = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    stdout, stderr = root / arm.arm_id / 'frontend_worker.stdout', root / arm.arm_id / 'frontend_worker.stderr'
    stdout.write_text(cp.stdout)
    stderr.write_text(cp.stderr)
    assert cp.returncode == 0, cp.stderr
    evidence = asdict(arm.validate(outputs, ctx.binding()))
    process = {'component': 'frontend_worker', 'argv': argv, 'rc': 0,
               'pid': os.getpid(), 'started_ns': time.monotonic_ns() - 1,
               'ended_ns': time.monotonic_ns(), 'stop_reason': None,
               'stdout': str(stdout), 'stderr': str(stderr),
               'stdout_sha256': em.digest(stdout), 'stderr_sha256': em.digest(stderr)}
    receipt = {'run_id': plan['run_id'], 'arm_id': arm.arm_id, 'binding': ctx.binding(),
               'adapter': arm.identity(), 'processes': [process], 'input_root': str(frozen),
               'output_root': str(outputs), 'manifest_sha256': em.digest(frozen / 'issued_manifest.json'),
               'manifest': manifest, 'status': 'ELIGIBLE', 'reason': 'ADAPTER_EVIDENCE',
               'evidence': evidence, 'ended_ns': time.monotonic_ns()}
    completion = {k: receipt[k] for k in ('run_id', 'arm_id', 'binding', 'adapter',
                                         'processes', 'input_root', 'output_root')}
    completion.update(actual_status='ELIGIBLE', actual_reason='ADAPTER_EVIDENCE',
                      ended_ns=receipt['ended_ns'], run_root=str(root))
    write(root / arm.arm_id / 'receipt.json', receipt)
    write(root / arm.arm_id / 'issued-completion.json', caller_envelope(completion))
    status = adopted(c, ctx, root)
    record('fabricated-step8', actual=status, controller_run_invocations=0,
           worker_rc=cp.returncode, evidence=evidence,
           canonical=json.loads((outputs / 'canonical.json').read_text()), run_root=root)
    assert status.startswith('REFUSED:'), status


def test_co_mutated_plan_catalog_portfolio_and_manifest_refused(tmp_path):
    project, ctx, arm, c = setup(tmp_path)
    root = tmp_path / 'run'
    c.run(ctx, root)
    receipt_path = root / arm.arm_id / 'receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'ELIGIBLE'
    plan = json.loads((root / 'plan.json').read_text())
    plan['portfolio'][0]['qualification_evidence'] = 'caller altered qualification after issue'
    plan['public_portfolio']['meta']['registered_production_executors'] = 999
    plan['public_portfolio_sha256'] = em._hash(plan['public_portfolio'])
    write(root / 'plan.json', plan)
    write(root / 'issued-plan.json', caller_envelope(plan))
    manifest_path = Path(receipt['input_root']) / 'issued_manifest.json'
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text())
    manifest['plan_sha256'] = em._hash(plan)
    write(manifest_path, manifest)
    receipt.update(manifest=manifest, manifest_sha256=em.digest(manifest_path))
    write(receipt_path, receipt)
    status = adopted(c, ctx, root)
    record('co-mutated-plan', actual=status, run_root=root,
           changed=['portfolio.qualification_evidence', 'public_portfolio',
                    'public_portfolio_sha256', 'issued-plan', 'manifest.plan_sha256', 'receipt'])
    assert status.startswith('REFUSED:'), status


def test_working_source_hash_checked_even_when_git_hides_mutation(tmp_path):
    repo = tmp_path / 'private-source'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    source = repo / 'helper.py'
    source.write_text('value = 1\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'helper.py'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Review Fixture',
                    '-c', 'user.email=review@example.invalid', 'commit', '-qm', 'neutral source'], check=True)
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    tree = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    arm = replace(H.adapter(), source_sha=sha, source_tree_sha=tree,
                  source_files={str(source): em.digest(source)})
    subprocess.run(['git', '-C', str(repo), 'update-index', '--assume-unchanged', 'helper.py'], check=True)
    source.write_text('value = 99\n')
    status = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True)
    try:
        em.Controller._source_current(arm)
        actual = 'ACCEPTED'
    except em.Refusal as exc:
        actual = 'REFUSED:' + exc.code
    record('hidden-source-mutation', actual=actual, git_status=status,
           expected_sha256=arm.source_files[str(source)], actual_sha256=em.digest(source))
    assert actual.startswith('REFUSED:'), actual


@pytest.mark.parametrize('bad,rtl', [(True, False), (False, True)])
def test_measured_producer_failure_retains_fail(tmp_path, bad, rtl):
    project, ctx, arm, c = setup(tmp_path, bad=bad, rtl=rtl)
    root = tmp_path / 'run'
    c.run(ctx, root)
    receipt = json.loads((root / arm.arm_id / 'receipt.json').read_text())
    reports = {str(p.relative_to(root)): json.loads(p.read_text())
               for p in (root / arm.arm_id / 'outputs/reports').rglob('*.json')}
    record('measured-failure-' + ('syntax' if bad else 'derived'), status=receipt['status'],
           reason=receipt['reason'], reports=reports, run_root=root)
    assert receipt['status'] == 'FAIL', receipt['status']


def test_step8_required_pvt_input_is_enforced(tmp_path):
    project, ctx, arm, c = setup(tmp_path, pvt=False)
    root = tmp_path / 'run'
    c.run(ctx, root)
    receipt = json.loads((root / arm.arm_id / 'receipt.json').read_text())
    record('missing-pvt', actual=receipt['status'], inputs=list(ctx.inputs), run_root=root)
    assert receipt['status'] != 'ELIGIBLE'


@pytest.mark.parametrize('case', ['missing', 'empty', 'stale'])
def test_worker_does_not_publish_missing_empty_stale_producer_outputs(tmp_path, monkeypatch, case):
    project, files = inputs(tmp_path)
    legacy_manifest(project, files)
    output = tmp_path / 'output'
    report = output / 'reports/phase2/sdc_check.json'
    if case == 'stale':
        worker.run_row('8', project, output)
        old = report.read_bytes()
    elif case == 'empty':
        report.parent.mkdir(parents=True)
        report.write_bytes(b'')
    calls = []
    def no_output(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(worker.subprocess, 'run', no_output)
    try:
        worker.run_row('8', project, output)
        actual = 'RETURNED_SUCCESS'
    except Exception as exc:
        actual = 'REFUSED:' + type(exc).__name__
    record('producer-output-' + case, actual=actual, report_exists=report.exists(),
           report_size=report.stat().st_size if report.exists() else None,
           stale_bytes_unchanged=case == 'stale' and report.read_bytes() == old,
           canonical_exists=(output / 'canonical.json').exists(), producer_calls=len(calls))
    assert actual.startswith('REFUSED:'), actual


def test_unissued_legacy_helper_does_not_execute_producers(tmp_path, monkeypatch):
    project, files = inputs(tmp_path)
    legacy_manifest(project, files)
    calls = []
    def spy(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(worker.subprocess, 'run', spy)
    try:
        worker.produce_8(project, tmp_path / 'out')
        actual = 'RETURNED_SUCCESS'
    except Exception as exc:
        actual = 'REFUSED:' + type(exc).__name__
    record('unissued-helper', actual=actual, producer_calls=len(calls))
    assert calls == [], calls


def test_default_normal_runner_has_a_step8_controller_hook():
    import ast
    hooks = {}
    for name in ('vibe_ic_one_shot_runner.py', 'design_one_shot_runner.py',
                 'phase23_one_shot_runner.py', 'phase3_one_shot_runner.py'):
        path = PROGRAMS / name
        nodes = ast.parse(path.read_text())
        hooks[name] = [ast.unparse(n) for n in ast.walk(nodes)
                       if isinstance(n, (ast.Import, ast.ImportFrom)) and
                       ('execution_modes' in ast.unparse(n) or
                        'execution_frontend_providers' in ast.unparse(n))]
    record('normal-runner-hooks', hooks=hooks,
           controller_docstring=em.__doc__, registry_docstring=em.Registry.__doc__)
    assert any(hooks.values()), hooks


def test_good_step8_default_selects_one_and_explicit_adoption_passes(tmp_path):
    project, ctx, arm, c = setup(tmp_path)
    root = tmp_path / 'run'
    plan = c.plan(ctx)
    assert plan['mode'] == 'default-mode' and plan['arms'] == ['frontend_8']
    result = c.run(ctx, root)
    receipt = json.loads((root / arm.arm_id / 'receipt.json').read_text())
    output = Path(receipt['output_root'])
    canonical = json.loads((output / 'canonical.json').read_text())
    assert receipt['status'] == 'ELIGIBLE'
    assert len(canonical['records']) == 3
    assert (output / 'reports/write_ledger.json').is_file()
    ledger = json.loads((output / 'reports/write_ledger.json').read_text())
    row = next(row for row in ledger['steps'] if str(row['id']) == '8')
    assert any(entry['rel'] == 'reports/phase2/sdc_check.json' for entry in row['produced'])
    status = adopted(c, ctx, root)
    record('good-step8', actual=status, default_plan=plan, run_result=result,
           ledger=ledger, run_root=root)
    assert status == 'ADOPTED'


@pytest.mark.parametrize('target', ['manifest', 'frozen-input', 'original-input', 'report'])
def test_single_mutation_is_refused(tmp_path, target):
    project, ctx, arm, c = setup(tmp_path)
    root = tmp_path / 'run'
    c.run(ctx, root)
    receipt = json.loads((root / arm.arm_id / 'receipt.json').read_text())
    assert receipt['status'] == 'ELIGIBLE'
    paths = {'manifest': Path(receipt['input_root']) / 'issued_manifest.json',
             'frozen-input': Path(receipt['input_root']) / 'phase2/stage2/constraints/top.sdc',
             'original-input': project / 'phase2/stage2/constraints/top.sdc',
             'report': Path(receipt['output_root']) / 'reports/phase2/sdc_check.json'}
    path = paths[target]
    path.chmod(0o644)
    if target == 'manifest':
        doc = json.loads(path.read_text())
        doc['parameters']['caller-added'] = True
        write(path, doc)
    elif target == 'report':
        doc = json.loads(path.read_text())
        doc['passed'] = 'true'
        write(path, doc)
    else:
        path.write_text(path.read_text() + '\n# current-input mutation\n')
    status = adopted(c, ctx, root)
    record('single-mutation-' + target, actual=status, run_root=root)
    assert status.startswith('REFUSED:'), status


def test_removed_step8_write_ledger_prevents_adoption(tmp_path):
    project, ctx, arm, c = setup(tmp_path)
    root = tmp_path / 'run'
    c.run(ctx, root)
    receipt = json.loads((root / arm.arm_id / 'receipt.json').read_text())
    assert receipt['status'] == 'ELIGIBLE'
    output = Path(receipt['output_root'])
    removed = []
    for p in [output / 'reports/write_ledger.json', *list((output / 'steps').rglob('written.json'))]:
        if p.exists():
            removed.append(str(p.relative_to(output)))
            p.unlink()
    status = adopted(c, ctx, root)
    record('removed-write-ledger', actual=status, removed=removed, run_root=root)
    assert status.startswith('REFUSED:'), status


def test_registration_rejects_wrong_worker_source_hash():
    registry = em.Registry()
    providers.register_factories(registry)
    arm = registry.adapters('8')[0]
    hashes = dict(arm.source_files)
    hashes[str(PROGRAMS / 'execution_frontend_worker.py')] = '0' * 64
    with pytest.raises(em.Refusal, match='ADAPTER_SOURCE_MISMATCH'):
        em.Registry().register(replace(arm, source_files=hashes))
    record('registration-hash-control', actual='REFUSED:ADAPTER_SOURCE_MISMATCH')

"""Source-bound controls for R4 admission, execution and CLI refusal contracts."""
from dataclasses import fields, replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import execution_modes as em
import execution_policy as policy
from programs.tests import test_execution_modes as H
from programs.tests.test_execution_receipt_chain import real_entry, isolated_transport


def issued_context(tmp_path, mode='ultra'):
    payload = real_entry('IC', mode, tmp_path)
    base = H.context(tmp_path)
    return em.Context('1', H.BASE, base.inputs, H.OBJECTIVE, ('transform',),
                      project_digest=payload['route']['project_digest'],
                      **policy.controller_fields(ic_ip_path='IC', route_receipt=payload['route']))


def test_INDEPENDENCE_ARGV_CLOSURE(tmp_path):
    ctx = issued_context(tmp_path)
    first = H.adapter('a')
    extra = H.VARIANTS / 'b.py'
    sources = dict(first.source_files, **{str(extra.resolve()): em.digest(extra)})
    first = replace(first, source_files=sources)
    second = replace(first, arm_id='b', tool_id='alias', engine_families=('alias',),
                     components=tuple(replace(c, argv=(*c.argv, str(extra.resolve()))) for c in first.components))
    controller = H.controller(first, second)
    result = controller.run(ctx, tmp_path / 'run')
    plan = json.loads((tmp_path / 'run/plan.json').read_text())
    assert plan['arms'] == ['a']
    assert plan['portfolio'][1]['admission'] == 'SAME_ENGINE_FAMILY'
    assert result['candidate_statuses'] == {'a': 'ELIGIBLE'}
    assert not (tmp_path / 'run/b').exists()
    assert plan['portfolio'][0]['invocation_sources'] != plan['portfolio'][1]['invocation_sources']


@pytest.mark.parametrize('identical', [True, False])
def test_ENTRY_SOURCE_BOUND(tmp_path, identical):
    ctx = issued_context(tmp_path, 'default')
    arm = H.adapter('a')
    private = tmp_path / 'private_entry.py'
    private.write_bytes(Path(arm.components[0].argv[1]).read_bytes() + (b'' if identical else b'\n# private\n'))
    revised = replace(arm, components=tuple(replace(c, argv=(c.argv[0], str(private), *c.argv[2:])) for c in arm.components))
    with pytest.raises(em.Refusal, match='ENTRY_SOURCE_UNBOUND'):
        H.controller(revised).run(ctx, tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


def test_CONTROLLER_ISSUANCE(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('VIBEIC_EXECUTION'):
            monkeypatch.delenv(key, raising=False)
    ctx = replace(H.context(tmp_path), intent_label='USER_EXPLICIT_ULTRA', request_digest='a' * 64)
    version_processes = []
    run = em.subprocess.run
    def observe(argv, *args, **kwargs):
        if '--version' in argv:
            version_processes.append(argv)
        return run(argv, *args, **kwargs)
    monkeypatch.setattr(em.subprocess, 'run', observe)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    result = controller.run(ctx, root, 'ultra-mode')
    assert result['status'] == 'REFUSED'
    assert result['reason'] == 'CONTROLLER_ISSUANCE_REQUIRED'
    assert controller.adopt(ctx, root, None)['status'] == 'REFUSED'
    assert not (root / 'a').exists()
    assert not (root / 'selected').exists()
    assert not (root / 'issued-plan.json').exists()
    assert version_processes == []


class CallerContext(em.Context):
    def binding(self):
        return H.NeutralContext.binding(self)


@pytest.mark.parametrize('live', [False, True])
def test_CONTROLLER_ISSUANCE_EXTERNAL_CONTEXT(tmp_path, live):
    base = issued_context(tmp_path) if live else H.context(tmp_path)
    values = {f.name: getattr(base, f.name) for f in fields(em.Context)}
    ctx = CallerContext(**values)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    result = controller.run(ctx, root, 'ultra-mode')
    assert result['status'] == 'REFUSED'
    assert result['reason'] == 'CONTEXT_IMPLEMENTATION_UNTRUSTED'
    assert controller.adopt(ctx, root, None)['status'] == 'REFUSED'
    assert not (root / 'a').exists()
    assert not (root / 'selected').exists()


def test_external_duck_context_cannot_call_binding(tmp_path):
    class Foreign:
        binding_called = False
        def binding(self):
            self.binding_called = True
            return H.context(tmp_path).binding()
    ctx = Foreign()
    result = H.controller(H.adapter('a')).run(ctx, tmp_path / 'run')
    assert result['reason'] == 'CONTEXT_IMPLEMENTATION_UNTRUSTED'
    assert not ctx.binding_called


def test_private_identical_executable_is_not_an_accepted_runtime(tmp_path):
    binary = tmp_path / 'private_python'
    binary.write_bytes(Path(sys.executable).resolve().read_bytes())
    binary.chmod(0o755)
    arm = H.adapter('a')
    sources = dict(arm.source_files, **{str(binary): em.digest(binary)})
    arm = replace(arm, source_files=sources,
                  components=tuple(replace(c, argv=(str(binary), *c.argv[1:])) for c in arm.components))
    with pytest.raises(em.Refusal, match='EXECUTABLE_SOURCE_UNBOUND'):
        H.controller(arm)


@pytest.mark.parametrize('mode', ['default', 'ultra'])
def test_live_issuance_binds_plan_argv_inputs_source_and_adoption(tmp_path, mode):
    ctx = issued_context(tmp_path, mode)
    controller = H.controller(H.adapter('a'), H.adapter('b'))
    root = tmp_path / 'run'
    result = controller.run(ctx, root)
    plan = json.loads((root / 'plan.json').read_text())
    authority = plan['execution_issuance']
    assert authority['kind'] == 'canonical-controller-issuance'
    assert authority['request_digest'] == ctx.request_digest
    assert authority['source_sha'] == H.BASE
    assert authority['inputs'] == ctx.binding()['inputs']
    assert authority['argv_digest'] == em._hash({r['arm_id']: r['components'] for r in plan['portfolio']})
    assert authority['implementation_digest'] == em._hash({r['arm_id']: r['invocation_sources'] for r in plan['portfolio']})
    if mode == 'ultra':
        assert plan['arms'] == ['a', 'b']
        result = controller.adopt(ctx, root, H.choice(ctx, root))
    assert result['status'] == 'ADOPTED'
    assert controller.verify_adoption(ctx, root)['status'] == 'ADOPTED'


def test_capability_removed_before_adoption_refuses_without_selected_bytes(tmp_path, monkeypatch):
    ctx = issued_context(tmp_path)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    assert controller.run(ctx, root)['candidate_statuses'] == {'a': 'ELIGIBLE'}
    choice = H.choice(ctx, root)
    monkeypatch.delenv(policy._CAPABILITY_FD_ENV)
    assert controller.adopt(ctx, root, choice)['reason'] == 'CONTROLLER_ISSUANCE_REQUIRED'
    assert not (root / 'selected').exists()


def test_cli_refusal_is_rc2_before_any_phase1_artifact(tmp_path):
    runner = Path(em.__file__).with_name('vibe_ic_one_shot_runner.py')
    environment = {k: v for k, v in os.environ.items() if not k.startswith('VIBEIC_EXECUTION')}
    result = subprocess.run([sys.executable, str(runner), str(tmp_path), '--no-dashboard'],
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert 'DELIVERY_ROUTE_UNDECLARED' in result.stderr
    assert not (tmp_path / 'phase1/generated_docs').exists()


def test_imported_main_catches_capability_refusal_and_preserves_route_input(tmp_path, monkeypatch):
    import vibe_ic_one_shot_runner as front
    path = tmp_path / 'input/step_0_5ic_answers.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'answers': {'other': 'keep'}}))
    monkeypatch.setattr(sys, 'argv', [str(Path(front.__file__)), str(tmp_path), '--route', 'ip'])
    monkeypatch.delenv(policy._CAPABILITY_FD_ENV, raising=False)
    assert front.main() == 2
    document = json.loads(path.read_text())
    assert document['answers'] == {'other': 'keep', 'deliverable': 'HARDMACRO'}
    assert document['answer_provenance']['deliverable']['answered_by'] == 'owner'
    assert not (tmp_path / 'phase1/generated_docs').exists()


def test_measured_fail_survives_unauthorized_adoption(tmp_path, monkeypatch):
    ctx = issued_context(tmp_path, 'default')
    controller = H.controller(H.adapter('a', fault='fail_gate'))
    root = tmp_path / 'run'
    assert controller.run(ctx, root)['status'] == 'FAIL'
    original_result = (root / 'result.json').read_bytes()
    original_comparison = (root / 'comparison.json').read_bytes()
    monkeypatch.delenv(policy._CAPABILITY_FD_ENV)
    assert controller.adopt(ctx, root, None)['status'] == 'REFUSED'
    assert json.loads((root / 'result.json').read_text())['status'] == 'FAIL'
    assert (root / 'result.json').read_bytes() == original_result
    assert (root / 'comparison.json').read_bytes() == original_comparison
    assert not (root / 'selected').exists()

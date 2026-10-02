"""Pure neutral controls for the existing execution_modes receipt chain."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_modes as em
import execution_policy as policy
from programs.tests import test_execution_modes as H


def issued_route(*, path, source_sha, project_digest, request_digest, route='macro'):
    receipt = dict(schema=1, kind='issued-route', ic_ip_path=path,
                   source_sha=source_sha, project_digest=project_digest,
                   request_digest=request_digest, route=route)
    receipt['route_digest'] = em._hash(receipt)
    return receipt


def test_default_omitted_is_one_existing_controller_plan(tmp_path):
    policy_value = policy.request()
    assert policy_value['intent_label'] == 'PROGRAM_DEFAULT'
    assert policy_value['mode_label'] == 'default-mode'
    assert policy_value['ultra_match'] is False
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'), H.adapter('b'))
    plan = controller.plan(ctx)
    assert plan['mode'] == 'default-mode'
    assert plan['arms'] == ['a']
    assert plan['mode_intent'] == 'default'


def test_frontdoor_default_omission_preserves_neutral_child_trace(tmp_path, monkeypatch):
    """Mode transport is observational: canonical child work sees the same trace."""
    import vibe_ic_one_shot_runner as front
    child = tmp_path / 'child.py'
    output = tmp_path / 'trace.json'
    child.write_text(
        'import json, os, pathlib, sys\n'
        'pathlib.Path(sys.argv[1]).write_text(json.dumps({"argv": sys.argv[1:],\n'
        '  "execution_env": os.environ.get("VIBEIC_EXECUTION_REQUEST"),\n'
        '  "cap_fd": os.environ.get("VIBEIC_EXECUTION_CAP_FD")}))\n')
    monkeypatch.delenv(policy.ENV, raising=False)
    assert front._run_phase('baseline', child, [str(output)]) == 0
    baseline = json.loads(output.read_text())
    assert front._run_phase('default-omitted', child, [str(output)]) == 0
    omitted = json.loads(output.read_text())
    assert omitted == baseline == {
        'argv': [str(output)], 'execution_env': None, 'cap_fd': None}

    args = type('Args', (), dict(execution_mode='default', execution_cpus=None,
                                 execution_ram_mb=None, execution_workers=None,
                                 execution_licenses=None, execution_choice=None,
                                 execution_choice_wait_s=None))()
    policy.configure(args)
    assert front._run_phase('default-explicit', child, [str(output)]) == 0
    explicit = json.loads(output.read_text())
    assert omitted == explicit == {
        'argv': [str(output)], 'execution_env': None, 'cap_fd': None}


def test_omitted_mode_keeps_each_canonical_phase_subprocess_boundary_exact(monkeypatch):
    import types
    import vibe_ic_one_shot_runner as front
    monkeypatch.delenv(policy.ENV, raising=False)
    monkeypatch.delenv(policy._CAPABILITY_FD_ENV, raising=False)
    baseline_env = dict(os.environ)
    observed = []

    def spy_run(command, *, env=None, pass_fds=(), **kwargs):
        observed.append((list(command), None if env is None else dict(env), tuple(pass_fds)))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(front.subprocess, 'run', spy_run)
    labels = ('PHASE 1', 'PHASE 2', 'PHASE 3', 'ANALOG')
    for label in labels:
        front._run_phase(label, Path('/tmp/runner.py'), ['--project', 'design'],
                         env=dict(baseline_env))
    assert len(observed) == len(labels)
    for command, env, pass_fds in observed:
        assert command == [sys.executable, '/tmp/runner.py', '--project', 'design']
        assert env == baseline_env
        assert policy.ENV not in env and policy._CAPABILITY_FD_ENV not in env
        assert pass_fds == ()


def test_postfix_chain_passes_and_records_all_links(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'), H.adapter('b'))
    root = tmp_path / 'run'
    controller.run(ctx, root, 'ultra-mode')
    assert (root / 'frozen-work.json').is_file()
    assert (root / 'issued-comparison.json').is_file()
    comparison = json.loads((root / 'comparison.json').read_text())
    assert [row['arm_id'] for row in comparison['arm_receipts']] == ['a', 'b']
    assert len(comparison['eligible_arms']) == 2
    adopted = controller.adopt(ctx, root, H.choice(ctx, root, 'b'))
    assert adopted['status'] == 'ADOPTED'
    assert adopted['frozen_work_digest'] == json.loads((root / 'plan.json').read_text())['frozen_work_digest']
    assert adopted['comparison_digest']
    assert adopted['acceptance_rerun']['status'] == 'PASS'
    assert controller.verify_adoption(ctx, root)['selected'] == 'b'


def test_reverse_mutation_of_frozen_work_is_refused(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    controller.run(ctx, root)
    frozen = json.loads((root / 'frozen-work.json').read_text())
    frozen['mode_intent'] = 'ultra'
    (root / 'frozen-work.json').write_text(json.dumps(frozen))
    with pytest.raises(em.Refusal, match='FROZEN_WORK_DIGEST_MISMATCH'):
        controller.adopt(ctx, root, H.choice(ctx, root))


def test_reverse_mutation_of_issued_arm_is_refused(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'), H.adapter('b'))
    root = tmp_path / 'run'
    controller.run(ctx, root, 'ultra-mode')
    receipt = json.loads((root / 'a/receipt.json').read_text())
    receipt['honest_verdict'] = 'PASS'
    (root / 'a/receipt.json').write_text(json.dumps(receipt))
    choice = H.choice(ctx, root, 'a')
    choice['receipt_sha256'] = em.digest(root / 'a/receipt.json')
    with pytest.raises(em.Refusal, match='ARM_RECEIPT_DIGEST_MISMATCH'):
        controller.adopt(ctx, root, choice)


def test_reverse_mutation_of_ai_comparison_is_refused(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    controller.run(ctx, root)
    comparison = json.loads((root / 'comparison.json').read_text())
    comparison['selection_criterion']['ordered_by'] = 'attacker'
    (root / 'comparison.json').write_text(json.dumps(comparison))
    with pytest.raises(em.Refusal, match='COMPARISON_AUTHORITY'):
        controller.adopt(ctx, root, H.choice(ctx, root))


def test_reverse_mutation_of_program_adoption_is_refused(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'))
    root = tmp_path / 'run'
    controller.run(ctx, root)
    controller.adopt(ctx, root, H.choice(ctx, root))
    adoption = json.loads((root / 'program_adoption.json').read_text())
    adoption['winner']['arm_id'] = 'wrong'
    (root / 'program_adoption.json').write_text(json.dumps(adoption))
    with pytest.raises(em.Refusal, match='PROGRAM_ADOPTION_CHANGED'):
        controller.verify_adoption(ctx, root)


def test_swapped_winner_artifact_and_changed_acceptance_refuse(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'), H.adapter('b'))
    root = tmp_path / 'run'
    controller.run(ctx, root, 'ultra-mode')
    choice = H.choice(ctx, root, 'b')
    choice['receipt_sha256'] = em.digest(root / 'a/receipt.json')
    with pytest.raises(em.Refusal, match='AI_RECEIPT_DIGEST_MISMATCH'):
        controller.adopt(ctx, root, choice)

    changed = replace(ctx, objective={'goal': 'different', 'metric': 'cost', 'direction': 'min'})
    with pytest.raises(em.Refusal, match='AI_CHOICE_UNBOUND'):
        controller.adopt(changed, root, H.choice(ctx, root, 'b'))


def test_incomplete_arm_set_and_failing_arm_cannot_be_adopted(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'), H.adapter('b'))
    root = tmp_path / 'run'
    controller.run(ctx, root, 'ultra-mode')
    (root / 'b/receipt.json').unlink()
    with pytest.raises(em.Refusal, match='INCOMPLETE_ARM_SET'):
        controller.adopt(ctx, root, H.choice(ctx, root, 'a'))

    failing = tmp_path / 'failing'
    failed_controller = H.controller(H.adapter('a', fault='fail_gate'))
    failed_controller.run(ctx, failing)
    assert json.loads((failing / 'a/receipt.json').read_text())['status'] == 'FAIL'
    with pytest.raises(em.Refusal, match='AI_CHOICE_INELIGIBLE'):
        failed_controller.adopt(ctx, failing, H.choice(ctx, failing))


def test_ultra_requires_explicit_user_evidence_and_children_preserve_it(monkeypatch):
    monkeypatch.setenv(policy.ENV, json.dumps({'mode': 'ultra', 'cpus': 1, 'ram_mb': 128}))
    with pytest.raises(em.Refusal, match='REQUEST_RECEIPT|ULTRA_INTENT'):
        policy.request()
    monkeypatch.delenv(policy.ENV)
    args = type('Args', (), dict(execution_mode='ultra', execution_cpus=1,
                                 execution_ram_mb=128, execution_workers=1,
                                 execution_licenses=None, execution_choice=None,
                                 execution_choice_wait_s=0))()
    import vibe_ic_one_shot_runner as front
    policy._register_frontdoor_issuer(front._EXECUTION_ISSUER)
    value = policy.configure(args, _issuer=front._EXECUTION_ISSUER)
    assert value['authority'] == 'USER_EXPLICIT_ULTRA'
    assert '--execution-mode' in policy.child_arguments(['project'])
    assert policy.require_explicit_ultra()['authority'] == 'USER_EXPLICIT_ULTRA'


@pytest.mark.parametrize('payload', [
    {'mode': 'ultra', 'authority': 'USER_EXPLICIT_ULTRA'},
    {'mode': 'ultra', 'category': 'ultra', 'intent_label': 'USER_EXPLICIT_ULTRA'},
])
def test_untrusted_environment_and_metadata_cannot_issue_ultra(monkeypatch, payload):
    monkeypatch.setenv(policy.ENV, json.dumps(payload))
    with pytest.raises(em.Refusal, match='REQUEST_RECEIPT|ULTRA_INTENT'):
        policy.request()


def test_recomputed_receipt_with_valid_looking_process_identity_is_not_authority(monkeypatch):
    monkeypatch.delenv(policy._CAPABILITY_FD_ENV, raising=False)
    forged = dict(schema=1, mode='ultra', mode_label='ultra-mode',
                  intent_label='USER_EXPLICIT_ULTRA', ultra_match=True,
                  issuer='live-frontdoor', issuer_pid=os.getppid(),
                  issuer_start_ticks='valid-looking-but-untrusted')
    forged['request_digest'] = policy._digest(forged)
    monkeypatch.setenv(policy.ENV, json.dumps({
        'mode': 'ultra', 'request_digest': forged['request_digest'],
        'request_receipt': forged}))
    with pytest.raises(em.Refusal, match='REQUEST_CAPABILITY_REQUIRED'):
        policy.request()


def test_child_cannot_upgrade_digest_bound_program_default(monkeypatch):
    monkeypatch.delenv(policy.ENV, raising=False)
    args = type('Args', (), dict(execution_mode='default', execution_cpus=1,
                                 execution_ram_mb=128, execution_workers=1,
                                 execution_licenses=None, execution_choice=None,
                                 execution_choice_wait_s=0))()
    policy.configure(args)
    ultra = type('Args', (), dict(execution_mode='ultra', execution_cpus=1,
                                  execution_ram_mb=128, execution_workers=1,
                                  execution_licenses=None, execution_choice=None,
                                  execution_choice_wait_s=0))()
    with pytest.raises(em.Refusal, match='ULTRA_ISSUER_NOT_ALLOWED|PARENT_REQUEST_CONFLICT'):
        policy.configure(ultra)


def test_child_parent_digest_and_receipt_must_travel_as_a_pair(monkeypatch):
    monkeypatch.delenv(policy.ENV, raising=False)
    args = type('Args', (), dict(execution_mode='ultra', execution_cpus=1,
                                 execution_ram_mb=128, execution_workers=1,
                                 execution_licenses=None, execution_choice=None,
                                 execution_choice_wait_s=0,
                                 execution_request_digest='a' * 64,
                                 execution_request_receipt=None))()
    with pytest.raises(em.Refusal, match='PARENT_REQUEST_MISSING'):
        policy.configure(args)


def test_child_preserves_only_an_issued_ultra_receipt_and_tamper_refuses(monkeypatch, tmp_path):
    monkeypatch.delenv(policy.ENV, raising=False)
    top = type('Args', (), dict(execution_mode='ultra', execution_cpus=1,
                                execution_ram_mb=128, execution_workers=1,
                                execution_licenses=None, execution_choice=None,
                                execution_choice_wait_s=0))()
    import vibe_ic_one_shot_runner as front
    policy._register_frontdoor_issuer(front._EXECUTION_ISSUER)
    issued = policy.configure(top, _issuer=front._EXECUTION_ISSUER)
    assert issued['request_digest']
    child = tmp_path / 'child.py'
    child.write_text('import execution_policy as p; print(p.request()["intent_label"])\n')
    env = dict(os.environ)
    env[policy.ENV] = json.dumps(issued, sort_keys=True)
    run = __import__('subprocess').run(
        [sys.executable, str(child)],
        env={**env, 'PYTHONPATH': str(Path(policy.__file__).parent)},
        pass_fds=policy.child_pass_fds(), capture_output=True, text=True)
    assert run.returncode == 0 and run.stdout.strip() == 'USER_EXPLICIT_ULTRA'
    receipt = Path(issued['request_receipt_path'])
    receipt.chmod(0o644)
    receipt.write_text('{}')
    with pytest.raises(em.Refusal, match='REQUEST_RECEIPT_INVALID'):
        policy.request()


def test_ip_context_requires_and_binds_distinct_route_receipt(tmp_path):
    source = tmp_path / 'input.txt'
    source.write_text('ip input\n')
    with pytest.raises(em.Refusal, match='ROUTE_RECEIPT_REQUIRED'):
        em.Context('ip', H.BASE, {'text.txt': source}, H.OBJECTIVE, ('transform',),
                   ic_ip_path='IP').binding()
    request_digest = 'd' * 64
    project_digest = 'e' * 64
    route = issued_route(path='IP', source_sha=H.BASE, project_digest=project_digest,
                         request_digest=request_digest)
    ctx = em.Context('ip', H.BASE, {'text.txt': source}, H.OBJECTIVE, ('transform',),
                     ic_ip_path='IP', route_receipt=route,
                     project_digest=project_digest, request_digest=request_digest)
    assert ctx.binding()['ic_ip_path'] == 'IP'
    with pytest.raises(em.Refusal, match='IC_IP_ROUTE_MISMATCH'):
        replace(ctx, route_receipt={**route, 'ic_ip_path': 'IC'}).binding()


def test_ic_context_also_requires_and_binds_an_issued_route_receipt(tmp_path):
    ctx = H.context(tmp_path)
    with pytest.raises(em.Refusal, match='ROUTE_RECEIPT_REQUIRED'):
        replace(ctx, route_receipt={}).binding()
    project_digest = 'f' * 64
    request_digest = '1' * 64
    route = issued_route(path='IC', source_sha=H.BASE, project_digest=project_digest,
                         request_digest=request_digest)
    production = replace(ctx, route_receipt=route, project_digest=project_digest,
                         request_digest=request_digest)
    assert production.binding()['ic_ip_path'] == 'IC'
    with pytest.raises(em.Refusal, match='ROUTE_RECEIPT_DIGEST_MISMATCH'):
        replace(production, route_receipt={**route, 'route': 'tampered'}).binding()
    wrong_source = {**route, 'source_sha': '0' * 40}
    wrong_source['route_digest'] = em._hash({k: v for k, v in wrong_source.items()
                                             if k != 'route_digest'})
    with pytest.raises(em.Refusal, match='ROUTE_SOURCE_MISMATCH'):
        replace(production, route_receipt=wrong_source).binding()
    wrong_request = {**route, 'request_digest': '4' * 64}
    wrong_request['route_digest'] = em._hash({k: v for k, v in wrong_request.items()
                                              if k != 'route_digest'})
    with pytest.raises(em.Refusal, match='ROUTE_REQUEST_MISMATCH'):
        replace(production, route_receipt=wrong_request).binding()


@pytest.mark.parametrize('frontdoor', [
    'phase1_one_shot_runner.py', 'design_one_shot_runner.py',
    'phase23_one_shot_runner.py', 'phase3_one_shot_runner.py',
])
def test_child_frontdoors_cannot_issue_ultra(frontdoor, monkeypatch):
    monkeypatch.delenv(policy.ENV, raising=False)
    args = type('Args', (), dict(execution_mode='ultra', execution_cpus=1,
                                 execution_ram_mb=128, execution_workers=1,
                                 execution_licenses=None, execution_choice=None,
                                 execution_choice_wait_s=0))()
    with pytest.raises(em.Refusal, match='ULTRA_ISSUER_NOT_ALLOWED'):
        policy.configure(args)
    source = (Path(policy.__file__).parent / frontdoor).read_text()
    assert '_execution.configure(args)' in source
    assert 'can_issue=' not in source


def test_controller_fields_requires_resolved_route_and_preserves_ip(monkeypatch):
    monkeypatch.delenv(policy.ENV, raising=False)
    with pytest.raises(em.Refusal, match='PRODUCTION_ROUTE_REQUIRED'):
        policy.controller_fields()
    fields_route = issued_route(
        path='IP', source_sha='a' * 40, project_digest='b' * 64,
        request_digest=policy.request()['request_digest'], route='macro-v1')
    fields = policy.controller_fields(ic_ip_path='IP', route_receipt=fields_route)
    assert fields['ic_ip_path'] == 'IP'
    assert fields['route_receipt']['ic_ip_path'] == 'IP'
    assert fields['intent_label'] == 'PROGRAM_DEFAULT'


def test_controller_fields_rejects_silent_ic_route_for_ip(monkeypatch):
    monkeypatch.delenv(policy.ENV, raising=False)
    fields_route = issued_route(
        path='IP', source_sha='a' * 40, project_digest='b' * 64,
        request_digest=policy.request()['request_digest'], route='macro-v1')
    with pytest.raises(em.Refusal, match='IC_IP_ROUTE_MISMATCH'):
        policy.controller_fields(
            ic_ip_path='IP', route_receipt={**fields_route, 'ic_ip_path': 'IC'})


def test_routed_production_context_cannot_bypass_explicit_ultra_intent(tmp_path):
    ctx = H.context(tmp_path)
    request_digest = '2' * 64
    project_digest = '3' * 64
    route = issued_route(path='IC', source_sha=H.BASE, project_digest=project_digest,
                         request_digest=request_digest, route='resolved')
    routed = replace(ctx, route_receipt=route, project_digest=project_digest,
                     request_digest=request_digest)
    with pytest.raises(em.Refusal, match='ULTRA_INTENT_MISSING'):
        H.controller(H.adapter('a'), H.adapter('b')).plan(routed, 'ultra-mode')


def test_default_run_auto_adopts_sole_eligible_without_choice_file(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'), H.adapter('b'))
    root = tmp_path / 'default'
    result = controller.run(ctx, root)
    assert result['status'] == 'ADOPTED'
    assert result['selected'] == 'a'
    assert not (root / 'choice.json').exists()
    assert json.loads((root / 'result.json').read_text())['status'] == 'ADOPTED'
    assert json.loads((root / 'adoption.json').read_text())['status'] == 'ADOPTED'
    assert [p.name for p in root.iterdir() if p.is_dir() and p.name in ('a', 'b')] == ['a']


@pytest.mark.parametrize('fault,expected', [
    ('fail_gate', 'FAIL'), ('unmeasured', 'NOT_MEASURED'),
])
def test_default_failure_or_unmeasured_result_is_not_auto_adopted(tmp_path, fault, expected):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a', fault=fault))
    root = tmp_path / fault
    result = controller.run(ctx, root)
    assert result['status'] == expected
    assert result['selected'] is None
    assert not (root / 'adoption.json').exists()


def test_ultra_waits_for_digest_bound_choice_and_rejects_ineligible_arm(tmp_path):
    ctx = H.context(tmp_path)
    controller = H.controller(H.adapter('a'), H.adapter('b', fault='fail_gate'))
    root = tmp_path / 'ultra'
    result = controller.run(ctx, root, 'ultra-mode')
    assert result['status'] == 'AWAITING_AI_SELECTION'
    with pytest.raises(em.Refusal, match='AI_CHOICE_INELIGIBLE'):
        controller.adopt(ctx, root, H.choice(ctx, root, 'b'))

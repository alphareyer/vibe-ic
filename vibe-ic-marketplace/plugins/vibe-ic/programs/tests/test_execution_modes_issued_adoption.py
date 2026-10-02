"""Append-only source controls for the four initial review blockers.

The original test module, finite tool and immutable reviewer proofs are kept
unchanged. These controls challenge issued process authority and committed
artifact generations using the same actual neutral subprocesses.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from programs.tests import test_execution_modes as H

M = H.em


def observed(controller, ctx, root, selected):
    try:
        return controller.adopt(ctx, root, selected)['status']
    except M.Refusal as exc:
        return 'REFUSED:' + exc.code


def failed_measurement(tmp_path):
    ctx = H.context(tmp_path)
    arm = H.adapter()
    measure = arm.components[1]
    wrapper = ('import runpy,sys;sys.argv=sys.argv[1:];'
               'runpy.run_path(sys.argv[0],run_name="__main__");sys.exit(7)')
    measure = replace(measure, argv=(measure.argv[0], '-c', wrapper, *measure.argv[1:]))
    arm = replace(arm, components=(arm.components[0], measure))
    c = H.controller(arm)
    root = tmp_path / 'run'
    c.run(ctx, root)
    receipt = H.read(root)
    assert receipt['processes'][-1]['rc'] == 7
    assert receipt['status'] == 'NOT_MEASURED'
    receipt['status'] = 'ELIGIBLE'
    receipt['evidence'] = H.validate_text(root / 'a/outputs', ctx.binding()).__dict__
    receipt['processes'][-1]['rc'] = 0
    (root / 'a/receipt.json').write_text(json.dumps(receipt))
    return c, ctx, root


def test_serialized_completion_cannot_replace_observed_process_authority(tmp_path):
    c, ctx, root = failed_measurement(tmp_path)
    path = root / 'a/issued-completion.json'
    certificate = json.loads(path.read_text())
    assert certificate['payload']['processes'][-1]['rc'] == 7
    certificate['payload']['processes'][-1]['rc'] = 0
    # A caller-controlled digest and serialized rc0 are not source issuance.
    certificate['signature'] = M._hash(certificate['payload'])
    path.write_text(json.dumps(certificate))
    assert observed(c, ctx, root, H.choice(ctx, root)) == 'REFUSED:ISSUED_AUTHORITY_INVALID'


def test_a_signature_alone_cannot_create_source_owned_completion(tmp_path):
    c, ctx, root = failed_measurement(tmp_path)
    path = root / 'a/issued-completion.json'
    payload = json.loads(path.read_text())['payload']
    payload['processes'][-1]['rc'] = 0
    # Even signature plumbing does not replace the immutable transcript
    # captured at Popen.wait. The source run, not the signer, issues authority.
    path.write_text(json.dumps(dict(payload=payload, signature=M._hash(payload))))
    assert observed(c, ctx, root, H.choice(ctx, root)) == 'REFUSED:ISSUED_AUTHORITY_INVALID'


@pytest.mark.parametrize('resource', ['cpu', 'ram'])
def test_current_resource_admission_is_rechecked(tmp_path, resource):
    ctx = H.context(tmp_path)
    arm = replace(H.adapter(), cpus=2 if resource == 'cpu' else 1,
                  ram_mb=256 if resource == 'ram' else 128)
    original = H.controller(arm)
    root = tmp_path / 'run'
    original.run(ctx, root)
    assert H.read(root)['status'] == 'ELIGIBLE'
    current = H.controller(arm, budget=M.Budget(1, 128))
    assert current.plan(ctx)['arms'] == []
    assert observed(current, ctx, root, H.choice(ctx, root)) == 'REFUSED:CURRENT_ADMISSION_REJECTED'


def test_current_default_policy_is_bound_to_the_original_run(tmp_path):
    ctx = H.context(tmp_path)
    arm = H.adapter('a')
    original = H.controller(arm)
    root = tmp_path / 'run'
    original.run(ctx, root)
    preferred = replace(H.adapter('0ll'), arm_id='0ll', tool_id='neutral_ll')
    current = H.controller(arm, preferred)
    assert current.plan(ctx)['arms'] == ['0ll']
    assert observed(current, ctx, root, H.choice(ctx, root)) == 'REFUSED:CURRENT_POLICY_REJECTED'


def test_adoption_commits_the_exact_validated_artifact_generation(tmp_path):
    ctx = H.context(tmp_path)
    c = H.controller(H.adapter())
    root = tmp_path / 'run'
    c.run(ctx, root)
    adopted = c.adopt(ctx, root, H.choice(ctx, root))
    assert adopted['status'] == 'ADOPTED'
    generation = adopted['selected_generation']
    directory = Path(generation['directory'])
    assert directory.parent == root / 'selected'
    assert M._issued(directory / 'manifest.json') == generation
    assert {n: M.digest(directory / n) for n in generation['outputs']} == adopted['evidence']['outputs']
    assert (directory / 'value.txt').read_text() == 'ONE INPUT\n'
    (root / 'a/outputs/value.txt').write_text('later mutable native candidate output')
    assert (directory / 'value.txt').read_text() == 'ONE INPUT\n'
    c._generation_current(generation)


def test_registered_source_manifest_is_immutable_against_caller_rehash(tmp_path):
    ctx = H.context(tmp_path)
    arm = H.adapter()
    registry = M.Registry()
    registry.register(arm)
    frozen = dict(registry.adapters('1')[0].source_files)
    # The old attack rehashed a caller-owned dict. Registration now owns its
    # own tracked snapshot, so changing this dict cannot replace any authority.
    tool = tmp_path / 'execution_modes_tool.py'
    tool.write_text('MUTATED_TRANSITIVE_SOURCE\n')
    arm.source_files.clear()
    arm.source_files[str(tool)] = M.digest(tool)
    assert dict(registry.adapters('1')[0].source_files) == frozen
    with pytest.raises(TypeError):
        registry.adapters('1')[0].source_files[str(tool)] = M.digest(tool)
    controller = M.Controller(registry, M.Budget(2, 512), H.controller().portfolio)
    result = controller.run(ctx, tmp_path / 'run')
    assert result['status'] == 'ADOPTED'
    assert (Path(result['selected_generation']['directory']) / 'value.txt').read_text() == 'ONE INPUT\n'


@pytest.mark.parametrize('arm_id', ['issued-plan.json', 'refusal.json', 'selected'])
def test_new_control_names_are_reserved(tmp_path, arm_id):
    try:
        H.controller(replace(H.adapter(), arm_id=arm_id))
        actual = 'ACCEPTED'
    except M.Refusal as exc:
        actual = 'REFUSED:' + exc.code
    assert actual == 'REFUSED:UNSAFE_ARM_ID'


def test_existing_run_directory_and_missing_adoption_plan_have_named_refusals(tmp_path):
    ctx = H.context(tmp_path)
    c = H.controller(H.adapter())
    root = tmp_path / 'existing'
    root.mkdir()
    try:
        c.run(ctx, root)
        actual = 'ACCEPTED'
    except M.Refusal as exc:
        actual = 'REFUSED:' + exc.code
    assert actual == 'REFUSED:RUN_OUTPUT_UNAVAILABLE'
    assert observed(c, ctx, root, None) == 'REFUSED:INVALID_ADOPTION_EVIDENCE'


def test_current_measured_default_override_remains_adoptable(tmp_path):
    ctx = H.context(tmp_path)
    c = H.controller(H.adapter('a', cost=9), H.adapter('b', cost=3))
    root = tmp_path / 'measurements'
    c.run(ctx, root, 'ultra-mode')
    superiority = M.Superiority(ctx.binding(), 'b', 'a', 'cost', 'min',
                               {i: root / i / 'receipt.json' for i in ('a', 'b')})
    selected_run = tmp_path / 'default-override'
    assert c.run(ctx, selected_run, superiority=superiority)['candidate_statuses'] == {'b': 'ELIGIBLE'}
    assert c.adopt(ctx, selected_run, H.choice(ctx, selected_run, 'b'))['selected'] == 'b'

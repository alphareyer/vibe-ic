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
    path.write_text(json.dumps(M._seal(payload)))
    assert observed(c, ctx, root, H.choice(ctx, root)) == 'REFUSED:ISSUED_AUTHORITY_CHANGED'


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
    preferred = replace(H.adapter('ll'), tool_id='librelane')
    current = H.controller(arm, preferred)
    assert current.plan(ctx)['arms'] == ['ll']
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


def test_direct_issue_call_cannot_manufacture_controller_observation(tmp_path):
    with pytest.raises(M.Refusal, match='ISSUED_AUTHORITY_INVALID'):
        M._issue_authority(tmp_path/'issued-plan.json', {'run_id':'caller'})
    assert not (tmp_path/'issued-plan.json').exists()


def test_caller_rehashed_source_cannot_replace_tracked_git_blob(tmp_path):
    import subprocess
    repo=tmp_path/'source'; repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    helper=repo/'helper.py'; helper.write_text('value = 1\n')
    subprocess.run(['git','-C',str(repo),'add','helper.py'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Neutral fixture',
                    '-c','user.email=fixture@example.invalid','commit','-qm','source'],check=True)
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    tree=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD^{tree}'],text=True).strip()
    subprocess.run(['git','-C',str(repo),'update-index','--assume-unchanged','helper.py'],check=True)
    helper.write_text('value = 99\n')
    arm=replace(H.adapter(),source_sha=sha,source_tree_sha=tree,
                source_files={str(helper):M.digest(helper)})
    assert subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True)==''
    with pytest.raises(M.Refusal,match='ADAPTER_SOURCE_MISMATCH'):
        M.Controller._source_current(arm)

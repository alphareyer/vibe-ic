"""R4-01 controls through real registered Step 8 run/adopt public callers."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

import execution_modes as em
from programs.tests import test_execution_modes_aa_step8_reverse as H
from programs.tests._hostpaths import require_repo


def setup(tmp_path, declared=()):
    # The registered worker, checkers and canonical portfolio are real repo
    # artifacts. Existing fixture inputs keep the design data neutral.
    require_repo('vibe-ic-marketplace/plugins/vibe-ic/programs/execution_frontend_worker.py')
    project, context, arm, controller = H.setup(tmp_path)
    files = dict(context.inputs)
    for index, name in enumerate(declared):
        original = project / ('extra-%d.json' % index)
        original.write_text('{"specification":"ordinary bound input"}\n')
        files[name] = original
    return replace(context, inputs=files), arm, controller, tmp_path / 'run'


def run(context, arm, controller, root):
    controller.run(context, root)
    return json.loads((root / arm.arm_id / 'receipt.json').read_text())


def adoption(context, arm, controller, root):
    choice = {'arm_id': arm.arm_id, 'binding': context.binding(),
              'receipt_sha256': em.digest(root / arm.arm_id / 'receipt.json'),
              'reviewer': 'R4-01 public caller control',
              'rationale': 'Reconsume the exact issued Step 8 evidence'}
    return controller.adopt(context, root, choice)


SAME_BASENAMES = ('metadata/issued_manifest.json',
                  'a/b/issued_manifest.json',
                  'Issued_Manifest.json',
                  'metadata/ISSUED_MANIFEST.JSON')


@pytest.mark.parametrize('name', SAME_BASENAMES)
def test_unlisted_same_basename_prevents_public_adoption(tmp_path, name):
    context, arm, controller, root = setup(tmp_path)
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    receipt_digest = em.digest(root / arm.arm_id / 'receipt.json')
    extra = frozen / name
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text('{"unlisted":"must enter census"}\n')
    assert em.digest(root / arm.arm_id / 'receipt.json') == receipt_digest
    with pytest.raises(em.Refusal, match='FROZEN_INPUT_CHANGED'):
        adoption(context, arm, controller, root)
    assert json.loads((root / 'adoption.json').read_text())['status'] == 'REFUSED'
    assert not (root / 'selected').exists()


@pytest.mark.parametrize('name', SAME_BASENAMES)
def test_declared_same_basename_is_bound_and_adoptable(tmp_path, name):
    context, arm, controller, root = setup(tmp_path, (name,))
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    metadata = frozen / 'issued_manifest.json'
    document = json.loads(metadata.read_text())
    assert document['schema'] == 'execution-issued-manifest/v2'
    assert document['files'] == context.binding()['inputs']
    assert document['files'][name] == em.digest(frozen / name)
    assert 'issued_manifest.json' not in document['files']
    assert adoption(context, arm, controller, root)['status'] == 'ADOPTED'


def test_input_subdirectory_manifest_is_ordinary_bound_input(tmp_path):
    """The normal registered worker must consume only Controller authority."""
    context, arm, controller, root = setup(tmp_path, ('input/issued_manifest.json',))
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    ordinary = frozen / 'input/issued_manifest.json'
    assert ordinary.is_file()
    assert receipt['manifest']['files']['input/issued_manifest.json'] == em.digest(ordinary)
    argv = receipt['processes'][0]['argv']
    assert argv[argv.index('--manifest') + 1] == str(frozen / 'issued_manifest.json')
    assert adoption(context, arm, controller, root)['status'] == 'ADOPTED'


@pytest.mark.parametrize('name,reason', [
    ('issued_manifest.json', 'RESERVED_INPUT_PATH'),
    ('./issued_manifest.json', 'RESERVED_INPUT_PATH'),
    ('issued_manifest.json/child.json', 'RESERVED_INPUT_PATH'),
    ('metadata/../issued_manifest.json', 'UNSAFE_RELATIVE_PATH'),
    ('../issued_manifest.json', 'UNSAFE_RELATIVE_PATH'),
    ('.', 'UNSAFE_RELATIVE_PATH'),
])
def test_metadata_location_is_reserved_before_input_copy(tmp_path, name, reason):
    context, arm, controller, root = setup(tmp_path, (name,))
    if reason == 'UNSAFE_RELATIVE_PATH':
        with pytest.raises(em.Refusal, match=reason):
            controller.run(context, root)
        assert json.loads((root / 'refusal.json').read_text())['reason'] == reason
        assert not (root / arm.arm_id).exists()
    else:
        receipt = run(context, arm, controller, root)
        assert receipt['status'] == 'NOT_MEASURED', receipt
        assert receipt['reason'] == reason
        assert receipt['processes'] == []
        assert list(Path(receipt['input_root']).iterdir()) == []


def test_duplicate_normalized_spelling_refuses_before_copy(tmp_path):
    context, arm, controller, root = setup(tmp_path,
        ('metadata/issued_manifest.json', 'metadata/./issued_manifest.json'))
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'NOT_MEASURED', receipt
    assert receipt['reason'] == 'DUPLICATE_INPUT_PATH'
    assert receipt['processes'] == []
    assert list(Path(receipt['input_root']).iterdir()) == []


def test_noncanonical_dot_spelling_cannot_bypass_issuance_census(tmp_path):
    context, arm, controller, root = setup(tmp_path, ('metadata/./issued_manifest.json',))
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'NOT_MEASURED', receipt
    assert receipt['reason'] == 'FROZEN_INPUT_CHANGED'
    assert receipt['processes'] == []


@pytest.mark.parametrize('spelling', ['metadata/./issued_manifest.json',
                                     'metadata/../other/issued_manifest.json'])
def test_normalized_unlisted_location_cannot_bypass_adoption(tmp_path, spelling):
    context, arm, controller, root = setup(tmp_path)
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    (frozen / 'metadata').mkdir()
    (frozen / 'other').mkdir()
    (frozen / spelling).write_text('{"unlisted":"normalized name"}\n')
    with pytest.raises(em.Refusal, match='FROZEN_INPUT_CHANGED'):
        adoption(context, arm, controller, root)


@pytest.mark.parametrize('location', ['metadata/issued_manifest.json',
                                     'issued_manifest.json', 'metadata'])
def test_frozen_symlink_cannot_impersonate_input_or_metadata(tmp_path, location):
    context, arm, controller, root = setup(tmp_path)
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    link = frozen / location
    if link.exists():
        preserved = tmp_path / 'preserved-metadata.json'
        preserved.write_bytes(link.read_bytes())
        link.unlink()
        target = preserved
    elif location == 'metadata':
        target = tmp_path / 'unlisted-directory'
        target.mkdir()
        (target / 'issued_manifest.json').write_text('{"unlisted":true}\n')
    else:
        link.parent.mkdir(parents=True)
        target = frozen / 'issued_manifest.json'
    link.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(em.Refusal, match='FROZEN_INPUT_CHANGED'):
        adoption(context, arm, controller, root)


@pytest.mark.parametrize('action', ['move', 'change'])
def test_controller_metadata_is_required_at_its_exact_issued_location(tmp_path, action):
    context, arm, controller, root = setup(tmp_path)
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    metadata = frozen / 'issued_manifest.json'
    if action == 'move':
        target = frozen / 'metadata/issued_manifest.json'
        target.parent.mkdir()
        metadata.rename(target)
    else:
        metadata.chmod(0o644)
        metadata.write_text('{"caller":"replaced metadata"}\n')
    with pytest.raises(em.Refusal, match='ISSUED_MANIFEST_CHANGED'):
        adoption(context, arm, controller, root)


def test_symlink_original_same_basename_is_not_a_regular_input(tmp_path):
    context, arm, controller, root = setup(tmp_path, ('metadata/issued_manifest.json',))
    source = context.inputs['metadata/issued_manifest.json']
    link = tmp_path / 'source-link.json'
    link.symlink_to(source)
    files = dict(context.inputs, **{'metadata/issued_manifest.json': link})
    with pytest.raises(em.Refusal, match='INPUT_NOT_REGULAR'):
        controller.run(replace(context, inputs=files), root)
    assert json.loads((root / 'refusal.json').read_text())['reason'] == 'INPUT_NOT_REGULAR'
    assert not (root / arm.arm_id).exists()

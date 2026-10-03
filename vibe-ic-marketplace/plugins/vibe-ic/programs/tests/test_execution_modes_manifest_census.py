"""R4-01 controls through real registered Step 8 run/adopt public callers."""
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

import execution_modes as em
from programs.tests import test_execution_receipt_chain as R
from execution_policy import controller_fields

isolated_transport = R.isolated_transport

@pytest.fixture(scope='module')
def registry():
    import execution_frontend_providers as providers
    result = em.Registry()
    providers.register_factories(result, step_ids=('8',))
    return result

_live_registry = None

@pytest.fixture(autouse=True)
def use_registry(registry):
    global _live_registry
    _live_registry = registry

def issued_step8(tmp_path):
    project = tmp_path / 'original'
    project.mkdir()
    payload = R.real_entry('IC', 'ultra', project)
    sdc = project / 'phase2/stage2/constraints/top.sdc'
    sdc.parent.mkdir(parents=True)
    sdc.write_text('create_clock -period 10 [get_ports clk]\n'
                   'set_input_delay 1 -clock clk [all_inputs]\n'
                   'set_output_delay 1 -clock clk [all_outputs]\n')
    l8 = project / 'phase1/generated_docs/L8_TIMING_WAVEFORM.json'
    l8.parent.mkdir(parents=True)
    l8.write_text(json.dumps({'clocks': {'clk': {'period_ns': 10}}})+'\n')
    matrix = sdc.with_name('pvt_matrix.json')
    matrix.write_text('{"corners":[]}\n')
    files = {str(path.relative_to(project)): path for path in (sdc, l8, matrix)}
    portfolio = em.load_portfolio()
    row = next(row for row in portfolio['steps'] if row['id']=='8')
    route = payload['route']
    context = em.Context('8', route['source_sha'], files, {'metric':'source_boundary'},
        tuple(row['mandatory_gate_programs']), project_digest=route['project_digest'],
        **controller_fields(ic_ip_path='IC', route_receipt=route))
    controller = em.Controller(_live_registry, em.Budget(1,512,1), portfolio)
    return project, context, _live_registry.adapters('8')[0], controller
from programs.tests._hostpaths import require_repo


def setup(tmp_path, declared=()):
    # The registered worker, checkers and canonical portfolio are real repo
    # artifacts. Existing fixture inputs keep the design data neutral.
    require_repo('vibe-ic-marketplace/plugins/vibe-ic/programs/execution_frontend_worker.py')
    project, context, arm, controller = issued_step8(tmp_path)
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
    assert document == receipt['manifest_payload']
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
    assert receipt['manifest_payload']['files']['input/issued_manifest.json'] == em.digest(ordinary)
    staged = Path(receipt['output_root']) / 'project/input/issued_manifest.json'
    assert staged.read_bytes() == ordinary.read_bytes()
    marker = json.loads((Path(receipt['output_root']) / 'canonical.json').read_text())
    assert [r['program'] for r in marker['records']] == [
        'sdc_syntax_check', 'sdc_validator_check', 'derived_clock_sdc_required_check']
    assert all(r['rc'] == 0 for r in marker['records'])
    argv = receipt['processes'][0]['argv']
    assert argv[argv.index('--manifest') + 1] == str(frozen / 'issued_manifest.json')
    assert adoption(context, arm, controller, root)['status'] == 'ADOPTED'


@pytest.mark.parametrize('relative,reason', [
    ('issued_manifest.json', 'FROZEN_INPUT_CHANGED'),
    ('phase2/stage2/constraints/top.sdc', 'FROZEN_INPUT_CHANGED'),
])
def test_external_hardlink_to_frozen_evidence_refuses(tmp_path, relative, reason):
    """A frozen file cannot be aliased outside the Controller input root."""
    context, arm, controller, root = setup(tmp_path)
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    subject = frozen / relative
    alias = tmp_path / 'outside-frozen-root-alias'
    os.link(subject, alias)
    with pytest.raises(em.Refusal, match=reason):
        adoption(context, arm, controller, root)
    result = json.loads((root / 'adoption.json').read_text())
    assert result['status'] == 'REFUSED', result
    assert result['reason'] == reason, result
    assert subject.stat().st_nlink == 2
    assert subject.stat().st_ino == alias.stat().st_ino
    assert not (root / 'selected').exists()


def test_hardlinked_caller_source_is_copied_to_exclusive_frozen_input(tmp_path):
    """Caller aliases are allowed; Controller-owned copies remain exclusive."""
    context, arm, controller, root = setup(tmp_path)
    original = context.inputs['phase2/stage2/constraints/top.sdc']
    alias = tmp_path / 'caller-hardlink-top.sdc'
    os.link(original, alias)
    context = replace(context, inputs={
        **context.inputs, 'phase2/stage2/constraints/top.sdc': alias})
    receipt = run(context, arm, controller, root)
    assert receipt['status'] == 'ELIGIBLE', receipt
    frozen = Path(receipt['input_root'])
    assert frozen.joinpath('phase2/stage2/constraints/top.sdc').stat().st_nlink == 1
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

import json
from pathlib import Path
import pytest
import execution_adapters_backend as backend

def _project(tmp_path):
    root=tmp_path/'project'; (root/'phase2/stage2/synth').mkdir(parents=True); (root/'phase2/stage2/constraints').mkdir(parents=True); (root/'phase3/librelane/prelayout/gates').mkdir(parents=True); (root/'phase3/stage3/pnr').mkdir(parents=True)
    (root/'phase2/stage2/synth/netlist.v').write_text('module x; endmodule\n')
    (root/'phase2/stage2/constraints/spm.asic.sdc').write_text('create_clock\n')
    (root/'phase2/stage2/constraints/asic_sdc.json').write_text(json.dumps({'path':'phase2/stage2/constraints/spm.asic.sdc'}))
    for p in ('provenance.jsonl','phase3/librelane/prelayout/gates/step8_sdc_opensta.json','phase3/stage3/pnr/constraint.sdc'):
        q=root/p; q.parent.mkdir(parents=True,exist_ok=True); q.write_text('bound\n')
    sta_items = ('phase3/stage3/sta/pre_pnr_timing.rpt','phase3/stage3/sta/prelayout_per_corner/sta_FF.rpt','phase3/stage3/sta/prelayout_per_corner/sta_SS.rpt','phase3/stage3/sta/prelayout_per_corner/sta_TT.rpt')
    for rel in ('reports/phase2/sdc_check.json', *sta_items):
        q=root/rel; q.parent.mkdir(parents=True,exist_ok=True); q.write_text('bound\n')
    summary = root/'reports/phase3/sta/pre_pnr_summary.json'; summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps({'subject': {'items': [{'path': rel, 'sha256': 'sha256:' + __import__('hashlib').sha256((root/rel).read_bytes()).hexdigest()} for rel in sta_items]}}))
    for rel in ('reports/lec.json', 'reports/lec.rpt', 'reports/analog/stage_analog_compliance.json'):
        q=root/rel; q.parent.mkdir(parents=True,exist_ok=True); q.write_text('bound\n')
    return root

def test_backend_contract_carries_canonical_pnr_sdc(tmp_path):
    root=_project(tmp_path); paths=backend.backend_project_input_contract(root,'15')
    assert 'phase3/stage3/pnr/constraint.sdc' in paths

def test_backend_contract_refuses_missing_pnr_sdc(tmp_path):
    root=_project(tmp_path); (root/'phase3/stage3/pnr/constraint.sdc').unlink()
    with pytest.raises(ValueError): backend.backend_project_input_contract(root,'15')

def test_step15_contract_materializes_canonical_sdc_bytes_in_private_project(tmp_path):
    from execution_backend_worker import _copy_input_tree
    root = _project(tmp_path)
    inputs = tmp_path / 'inputs'; (inputs / 'project').mkdir(parents=True)
    source = inputs / 'project'
    for rel in backend.backend_project_input_contract(root, '15'):
        src = root / rel
        if src.is_file():
            dst = source / rel; dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(src.read_bytes())
    private = tmp_path / 'private'
    _copy_input_tree(inputs, private)
    assert (private / 'phase3/stage3/pnr/constraint.sdc').read_bytes() == (root / 'phase3/stage3/pnr/constraint.sdc').read_bytes()


def test_step12_contract_keeps_previous_namespace_without_pnr_sdc(tmp_path):
    root = _project(tmp_path)
    (root / 'phase3/stage3/pnr/constraint.sdc').unlink()
    paths = backend.backend_project_input_contract(root, '12')
    assert 'phase3/stage3/pnr/constraint.sdc' not in paths
    assert 'provenance.jsonl' in paths
    assert 'phase3/librelane/prelayout/gates/step8_sdc_opensta.json' in paths

def test_step15_contract_carries_preexisting_prelayout_reports(tmp_path):
    root = _project(tmp_path)
    for rel in ('reports/phase2/sdc_check.json','phase3/stage3/sta/pre_pnr_timing.rpt','phase3/stage3/sta/prelayout_per_corner/sta_FF.rpt','phase3/stage3/sta/prelayout_per_corner/sta_SS.rpt','phase3/stage3/sta/prelayout_per_corner/sta_TT.rpt'):
        p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('issued\n')
    sta_items = ('phase3/stage3/sta/pre_pnr_timing.rpt','phase3/stage3/sta/prelayout_per_corner/sta_FF.rpt','phase3/stage3/sta/prelayout_per_corner/sta_SS.rpt','phase3/stage3/sta/prelayout_per_corner/sta_TT.rpt')
    summary = root/'reports/phase3/sta/pre_pnr_summary.json'; summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps({'subject': {'items': [{'path': rel, 'sha256': 'sha256:' + __import__('hashlib').sha256((root/rel).read_bytes()).hexdigest()} for rel in sta_items]}}))
    paths=backend.backend_project_input_contract(root,'15')
    assert all(rel in paths for rel in ('reports/phase2/sdc_check.json','reports/phase3/sta/pre_pnr_summary.json','phase3/stage3/sta/pre_pnr_timing.rpt'))

def test_step15_contract_derives_sta_subject_items_and_refuses_invalid_json(tmp_path):
    root = _project(tmp_path)
    summary = root / 'reports/phase3/sta/pre_pnr_summary.json'
    doc = json.loads(summary.read_text())
    assert set(item['path'] for item in doc['subject']['items']).issubset(
        set(backend.backend_project_input_contract(root, '15')))
    summary.write_text('bound\n')
    with pytest.raises(ValueError):
        backend.backend_project_input_contract(root, '15')

def test_step15_contract_refuses_changed_subject_digest(tmp_path):
    root = _project(tmp_path)
    summary = root / 'reports/phase3/sta/pre_pnr_summary.json'
    doc = json.loads(summary.read_text())
    doc['subject']['items'][0]['sha256'] = 'sha256:' + '0' * 64
    summary.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        backend.backend_project_input_contract(root, '15')


def test_step15_contract_carries_and_requires_stage_analog_producer_proof(tmp_path):
    root = _project(tmp_path)
    paths = backend.backend_project_input_contract(root, '15')
    assert 'reports/analog/stage_analog_compliance.json' in paths
    (root / 'reports/analog/stage_analog_compliance.json').unlink()
    with pytest.raises(ValueError):
        backend.backend_project_input_contract(root, '15')


def test_default15_cold_working_sdc_publication_keeps_issued_inputs_current(tmp_path):
    """The actual working-deck publisher cannot change Default15's inputs."""
    import hashlib
    from _ppa import timing
    root = _project(tmp_path)
    working = root / 'phase3/stage3/pnr/constraint.sdc'
    working.unlink()
    paths = backend.backend_project_input_contract(root, '15', execution_mode='default')
    assert 'phase3/stage3/pnr/constraint.sdc' not in paths
    assert 'phase2/stage2/constraints/spm.asic.sdc' in paths
    assert 'phase2/stage2/constraints/asic_sdc.json' in paths
    before = {p: hashlib.sha256((root / p).read_bytes()).hexdigest()
              for p in paths if (root / p).is_file()}
    record = {'path': 'phase2/stage2/constraints/spm.asic.sdc',
              'text': 'create_clock -period 10 [get_ports clk]\n',
              'step7_sha256': before['phase2/stage2/constraints/spm.asic.sdc'],
              'deck_sha256': 'source-control-deck', 'regenerated': None,
              'derivation': {'name': 'pad_ring_supply_port_drv_scope'}}
    assert timing.publish_pnr_sdc(record, working.parent) == working
    assert working.read_text() == record['text']
    assert backend.backend_project_input_contract(root, '15', execution_mode='default') == paths
    assert {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in before} == before


@pytest.mark.parametrize('source', ['phase2/stage2/constraints/spm.asic.sdc',
                                   'phase2/stage2/constraints/asic_sdc.json'])
@pytest.mark.parametrize('missing', [False, True])
def test_default15_still_requires_the_real_step7_input(tmp_path, source, missing):
    import hashlib
    from execution_backend_worker import resolve_input_contract
    root = _project(tmp_path)
    paths = backend.backend_project_input_contract(root, '15', execution_mode='default')
    manifest = {'declarations': list(paths), 'missing': [], 'files': {
        p: hashlib.sha256((root / p).read_bytes()).hexdigest()
        for p in paths if (root / p).is_file()}}
    assert resolve_input_contract(root, paths, manifest) == []
    if missing:
        (root / source).unlink()
    else:
        (root / source).write_text('changed after issuance\n')
    assert source in resolve_input_contract(root, paths, manifest)


def test_default15_accepts_the_step7_declared_design_staged_sdc(tmp_path):
    """A normal Step7 design-staged deck has the producer's .sdc name."""
    import hashlib
    from execution_backend_worker import resolve_input_contract

    root = _project(tmp_path)
    original = root / 'phase2/stage2/constraints/spm.asic.sdc'
    staged = root / 'phase2/stage2/constraints/spm.sdc'
    staged.write_bytes(original.read_bytes())
    original.unlink()
    record_path = root / 'phase2/stage2/constraints/asic_sdc.json'
    record = json.loads(record_path.read_text())
    record.update(path=str(staged.relative_to(root)), design_staged=True,
                  sha256=hashlib.sha256(staged.read_bytes()).hexdigest())
    record_path.write_text(json.dumps(record))
    paths = backend.backend_project_input_contract(root, '15', execution_mode='default')
    assert record['path'] in paths
    assert 'phase2/stage2/constraints/spm.asic.sdc' not in paths
    assert 'phase2/stage2/constraints/asic_sdc.json' in paths
    manifest = {'declarations': list(paths), 'missing': [], 'files': {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in paths if (root / rel).is_file()}}
    assert manifest['files'][record['path']] == record['sha256']
    assert resolve_input_contract(root, paths, manifest) == []

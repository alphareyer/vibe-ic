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
    for rel in ('reports/lec.json', 'reports/lec.rpt'):
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

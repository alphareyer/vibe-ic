"""Source admission of a finite local correction, through the ordinary consumer.

Neutral locally versioned IP exercises real independent official pulls. Fixture
challenge receipts are source-contract data, never credited as native proof.
"""
from pathlib import Path
import hashlib
import json
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_versioned_reused_ip_fetch import _upstream, _match, _project, _git
import design_one_shot_runner as runner
import ip_catalog_pull as pull
import ip_catalog_query as query


def _h(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def _fixture(tmp_path, monkeypatch):
    repo, erratum = _upstream(tmp_path)
    # A second official file must not escape the derivative's complete inventory.
    (repo / 'rtl/helper.v').write_text('module helper; endmodule\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'second official file')
    _git(repo, 'tag', '-f', '2.3.4')
    match = _match(repo, erratum)
    # This fixture's tag already contains the older corrected bytes; no claim
    # that a fix predates its declared parent is needed for this local contract.
    match.errata = []
    match.rtl_files.append('rtl/helper.v')
    monkeypatch.setattr(query, 'query_catalog', lambda *a, **kw: [match])
    monkeypatch.setattr(pull, 'CACHE_ROOT', tmp_path/'cache')
    project = _project(tmp_path, 'Reuse leaf from vendor:reusable:leaf:2.3.4.')
    result = runner.step_rtl_gen(project, 'processor_cpu')
    assert result.status == 'PASS_WITH_WAIVERS', result.detail
    rtl = project/'phase2/stage1/rtl'
    support = project/'local'
    parent = support/'parent'
    shutil.copytree(rtl, parent/'phase2/stage1/rtl')
    shutil.copy2(project/'provenance.jsonl', parent/'provenance.jsonl')
    old = (rtl/'leaf.v').read_text()
    new = old.replace("1'b1", "1'bx")
    before = {p.name: _h(p) for p in rtl.glob('*.v')}
    after = {**before, 'leaf.v': hashlib.sha256(new.encode()).hexdigest()}
    patch = {'format': 'exact_utf8_replacements.v1', 'files': [{
        'path': 'leaf.v', 'sha256_before': before['leaf.v'],
        'sha256_after': after['leaf.v'],
        'edits': [{'before': old, 'after': new}]}]}
    _json(support/'patch.json', patch)
    (support/'challenge.v').write_text('module challenge; endmodule\n')
    input_path = project/'input/docs/contract.md'
    input_path.parent.mkdir(parents=True)
    input_path.write_text('Reset is synchronous at the rising clock edge.\n')
    for name, code, inventory in [('red', 1, before), ('green', 0, after)]:
        _json(support/(name+'.json'), {
            'results': [{'name': 'compile', 'rc': 0}, {'name': 'run', 'rc': code}],
            'tb_sha256': _h(support/'challenge.v'), 'rtl': inventory})
    def ref(path):
        return {'path': path.relative_to(project).as_posix(), 'sha256': _h(path)}
    record = {
        'schema': 'vibeic.reused_ip_local_derivative.v1', 'kind': 'LOCAL_DERIVATIVE',
        'parent_project': 'local/parent',
        'parent_manifest': ref(parent/'phase2/stage1/rtl/SOURCE_MANIFEST.json'),
        'parent_provenance': ref(parent/'provenance.jsonl'),
        'patch': ref(support/'patch.json'), 'current_inventory': after,
        'local_provenance': {'actor': 'fixture author', 'reason': 'contract correction', 'owner_ruling': 'explicit fixture authorization'},
        'input_obligations': [{**ref(input_path), 'quote': 'Reset is synchronous at the rising clock edge.'}],
        'proof': {'challenge': ref(support/'challenge.v'), 'parent_red': ref(support/'red.json'), 'candidate_green': ref(support/'green.json')},
    }
    _json(support/'record.json', record)
    return project, match, ref(support/'record.json'), record


def _apply(project, match, ref):
    return pull.apply_local_derivative(project, [match], ref)


def test_normal_consumer_admits_complete_declared_local_derivative(tmp_path, monkeypatch):
    # Same supplied derivative bytes/receipts reach the old and new ordinary
    # runner. The pre-fix failure is its observed FAIL verdict, not a missing API.
    project, _, ref, record = _fixture(tmp_path, monkeypatch)
    patch = json.loads((project/'local/patch.json').read_text())
    (project/'phase2/stage1/rtl/leaf.v').write_text(patch['files'][0]['edits'][0]['after'])
    mf_path=project/'phase2/stage1/rtl/SOURCE_MANIFEST.json'
    mf=json.loads(mf_path.read_text());mf['local_derivative']=ref;_json(mf_path,mf)
    event={'event':'ip_catalog_local_derivative','kind':'LOCAL_DERIVATIVE',
           'record':ref,'current_inventory':record['current_inventory'],
           'exit_code':0,'outputs':{'phase2/stage1/rtl/leaf.v':'sha256:'+record['current_inventory']['leaf.v']},
           'official_unmodified_files':1,'locally_adapted_reused_files':1,
           'separately_authored_files':[]}
    with (project/'provenance.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
    result=runner.step_rtl_gen(project,'processor_cpu')
    assert result.status=='PASS_WITH_WAIVERS', result.detail
    assert result.extras['ip_fetch']['reuse_kind']=='LOCAL_DERIVATIVE'


def test_declared_derivative_runs_through_producer_and_normal_consumer(tmp_path, monkeypatch):
    project, match, ref, _ = _fixture(tmp_path, monkeypatch)
    outcome = _apply(project, match, ref)
    assert outcome['status'] == pull.PIN_VERIFIED
    assert outcome['official_unmodified_files'] == 1
    assert outcome['locally_adapted_reused_files'] == 1
    result = runner.step_rtl_gen(project, 'processor_cpu')
    assert result.status == 'PASS_WITH_WAIVERS'
    assert result.extras['ip_fetch']['reuse_kind'] == 'LOCAL_DERIVATIVE'
    assert 'Admitted LOCAL DERIVATIVE' in result.detail
    mf = json.loads((project/'phase2/stage1/rtl/SOURCE_MANIFEST.json').read_text())
    assert pull.verify_existing_official_pins_outcome(project, [match], mf)[0] == pull.PIN_MISMATCH
    assert _apply(project, match, ref)['status'] == pull.PIN_VERIFIED


@pytest.mark.parametrize('mutation', ['adapted_drift', 'extra_file', 'undeclared_change',
    'missing_parent', 'missing_patch', 'missing_evidence', 'missing_input',
    'producer_receipt', 'forged_parent', 'patch_context', 'symlink', 'record_drift'])
def test_normal_consumer_blocks_local_derivative_drift(tmp_path, monkeypatch, mutation):
    project, match, ref, record = _fixture(tmp_path, monkeypatch)
    assert _apply(project, match, ref)['status'] == pull.PIN_VERIFIED
    rtl = project/'phase2/stage1/rtl'
    if mutation == 'adapted_drift':
        (rtl/'leaf.v').write_text('module leaf; endmodule\n')
    elif mutation == 'extra_file':
        (rtl/'extra.vh').write_text('`define extra 1\n')
    elif mutation == 'undeclared_change':
        (rtl/'helper.v').write_text('module helper; wire x; endmodule\n')
    elif mutation == 'missing_parent':
        (project/'local/parent/phase2/stage1/rtl/helper.v').unlink()
    elif mutation == 'missing_patch':
        (project/'local/patch.json').unlink()
    elif mutation == 'missing_evidence':
        (project/'local/red.json').unlink()
    elif mutation == 'missing_input':
        (project/'input/docs/contract.md').unlink()
    elif mutation == 'producer_receipt':
        events = [json.loads(l) for l in (project/'provenance.jsonl').read_text().splitlines()]
        (project/'provenance.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events if e['event'] != 'ip_catalog_local_derivative'))
    elif mutation == 'forged_parent':
        # Rewrite ALL local hashes to agree with forged parent bytes. The fresh
        # independent official pull must still defeat this receipt laundering.
        p = project/'local/parent/phase2/stage1/rtl/helper.v'
        p.write_text('module helper; wire forged; endmodule\n')
        (rtl/'helper.v').write_bytes(p.read_bytes())
        digest = _h(p)
        for mf_path in [project/'local/parent/phase2/stage1/rtl/SOURCE_MANIFEST.json', rtl/'SOURCE_MANIFEST.json']:
            mf = json.loads(mf_path.read_text());mf['source_pins'][0]['files_sha256']['rtl/helper.v'] = digest;_json(mf_path,mf)
        for prov in [project/'local/parent/provenance.jsonl',project/'provenance.jsonl']:
            events = [json.loads(l) for l in prov.read_text().splitlines()]
            for e in events:
                if e['event']=='ip_catalog_pull':
                    e['outputs']['phase2/stage1/rtl/helper.v']='sha256:'+digest
                    e['outputs_sha256']=[e['outputs'][k][7:] for k in e['outputs']]
            prov.write_text(''.join(json.dumps(e)+'\n' for e in events))
        for key in ['parent_manifest','parent_provenance']:
            record[key]['sha256'] = _h(project/record[key]['path'])
        record['current_inventory']['helper.v'] = digest
        for key in ['parent_red','candidate_green']:
            p=project/record['proof'][key]['path'];receipt=json.loads(p.read_text());receipt['rtl']['helper.v']=digest;_json(p,receipt);record['proof'][key]['sha256']=_h(p)
        _json(project/'local/record.json',record)
        mf=json.loads((rtl/'SOURCE_MANIFEST.json').read_text());mf['local_derivative']['sha256']=_h(project/'local/record.json');_json(rtl/'SOURCE_MANIFEST.json',mf)
    elif mutation == 'patch_context':
        p=project/'local/patch.json';patch=json.loads(p.read_text());patch['files'][0]['edits'][0]['before']='absent exact context';_json(p,patch)
        record['patch']['sha256']=_h(p);_json(project/'local/record.json',record)
        mf=json.loads((rtl/'SOURCE_MANIFEST.json').read_text());mf['local_derivative']['sha256']=_h(project/'local/record.json');_json(rtl/'SOURCE_MANIFEST.json',mf)
    elif mutation == 'symlink':
        p=rtl/'helper.v';p.unlink();p.symlink_to(project/'local/parent/phase2/stage1/rtl/helper.v')
    elif mutation == 'record_drift':
        (project/'local/record.json').write_text('{}\n')
    result = runner.step_rtl_gen(project, 'processor_cpu')
    if mutation == 'symlink':
        # The unchanged earlier staged-closure guard stops the normal runner
        # before pin admission; source admission itself must still refuse it.
        assert result.status == 'NOT_MEASURED'
        mf = json.loads((rtl/'SOURCE_MANIFEST.json').read_text())
        assert pull.verify_existing_reused_pins_outcome(project, [match], mf)[0] == pull.PIN_MISMATCH
        return
    assert result.status == 'FAIL'
    assert 'LOCAL_DERIVATIVE_REFUSED' in result.detail or 'official parent:' in result.detail


def test_missing_local_inputs_refuse_before_producer_mutation(tmp_path, monkeypatch):
    project, match, ref, _ = _fixture(tmp_path, monkeypatch)
    path=project/'phase2/stage1/rtl/leaf.v';before=path.read_bytes()
    (project/'local/green.json').unlink()
    assert _apply(project, match, ref)['status']==pull.PIN_MISMATCH
    assert path.read_bytes()==before


def test_unavailable_parent_cannot_hide_proven_current_drift(tmp_path, monkeypatch):
    project, match, ref, _ = _fixture(tmp_path, monkeypatch)
    assert _apply(project, match, ref)['status'] == pull.PIN_VERIFIED
    (project/'phase2/stage1/rtl/helper.v').write_text('module helper; wire changed; endmodule\n')
    monkeypatch.setattr(pull, 'verify_existing_official_pins_outcome',
                        lambda *a, **kw: (pull.PIN_UNAVAILABLE, 'unreachable'))
    result = runner.step_rtl_gen(project, 'processor_cpu')
    assert result.status == 'FAIL'
    assert 'current full inventory drift' in result.detail

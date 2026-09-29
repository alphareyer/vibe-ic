"""Finite CUT20 source controls. Captured values, never native measurements.

Original 14/4 files are immutable. These controls reuse their exact numbers,
exercise actual adoption/Step20 consumers, and keep native insertion unmeasured.
"""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import pytest
import yaml

PROGRAMS = Path(os.environ.get('CUT20_PROGRAMS', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(PROGRAMS), str(PROGRAMS / 'tests')]
area = importlib.import_module('hold_area_budget_check')
flow = importlib.import_module('flow_compliance_check')
F = importlib.import_module('test_librelane_cts_hold')
cts, contract = F.cts, F.contract
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def put(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + '\n')
    return path


def capture(project, inserted=2, *, negative_hold=False, stale=False):
    """Original integrity values; only new producer binding schema is added."""
    corners = list(F.CORNERS)
    put(project / 'phase3/librelane_switch.json', {'steps': {'19': 'librelane', '20': 'librelane'}})
    base = project / 'phase3/librelane/19-cts-hold'
    before = put(base / '03-vibeic-externalcapturelaunchretap/state_out.json', {
        'metrics': {'design__instance__area__stdcell': 1000.0, 'design__instance__count': 400}})
    after = put(base / '04-openroad-resizertimingpostcts/state_out.json', {
        'metrics': {'design__instance__area__stdcell': 1000.0 + inserted * 5.0,
                    'design__instance__count': 400 + inserted,
                    'design__instance__count__hold_buffer': inserted,
                    'design__instance__count__setup_buffer': 0}})
    measured = put(base / '07-openroad-stamidpnr/state_out.json', {'metrics': {
        **json.loads(after.read_text())['metrics'],
        **{f'timing__hold__ws__corner:{c}': (-0.2 if negative_hold and i == 1 else 0.2)
           for i, c in enumerate(corners)}}})
    views = {}
    for name in ('post_cts', 'post_hold'):
        p = project / f'phase3/stage3/pnr/{name}.def'
        p.parent.mkdir(parents=True, exist_ok=True)
        n = 400 + inserted if name == 'post_hold' else 400
        p.write_text(f'VERSION 5.8 ;\nDESIGN chip_top ;\nUNITS DISTANCE MICRONS 1000 ;\n'
                     f'COMPONENTS {n} ;\n' + '\n'.join(
                         f'- n{i} neutral__buf_1 + PLACED ( {i} 0 ) N ;' for i in range(n)) +
                     '\nEND COMPONENTS\nEND DESIGN\n')
        views[name + '_def'] = {'dest': str(p.relative_to(project)), 'dest_sha256': sha(p)}
    source_paths = {k: str(p.relative_to(project)) for k, p in [('before', before), ('after', after)]}
    hashes = {'before': sha(before), 'after': sha(after)}
    put(project / 'reports/phase3/pnr/hold_area.json', {
        'schema': 'hold-area-state-pair-v1', 'program': 'librelane_cts_hold.execute',
        'before_total_area': 1000.0, 'after_total_area': 1000.0 + inserted * 5.0,
        'before_instance_count': 400, 'after_instance_count': 400 + inserted,
        'hold_buffer_count': inserted, 'setup_buffer_count': 0,
        'numerator_basis': 'setup-plus-hold delta upper bound',
        'sources': source_paths, 'sources_sha256': hashes})
    put(project / F.llev.RECEIPT_REL, {
        'selected': 'librelane', 'corners': corners, 'views': views,
        'measured_state': str(measured.relative_to(project)), 'measured_state_sha256': sha(measured),
        'hold_area_sources_sha256': hashes,
        'chain': {k: str(p.parent.relative_to(project)) for k, p in [
            ('Vibeic.ExternalCaptureLaunchRetap', before), ('OpenROAD.ResizerTimingPostCTS', after)]}})
    # This is the actual numeric classifier, not a canned FAIL report.
    area.main([str(project), '--json', str(project / 'reports/phase3/pnr/hold_area_budget.json')])
    if stale:
        with (project / views['post_hold_def']['dest']).open('a') as f:
            f.write('# changed after handoff\n')
    return before, after


def step20(project, *, bypass=False):
    definition = yaml.safe_load((PROGRAMS.parent / 'flow/phase1_phase2_phase3.yaml').read_text())
    step = copy.deepcopy(next(s for s in definition['steps'] if str(s['id']) == '20'))
    if bypass:
        step['gate']['all_of'] = [c for c in step['gate']['all_of']
                                 if 'hold_area_budget_check' not in c.get('program_exit_zero', '')]
    return flow.check_step(project, step, {})


def test_current_over_budget_blocks_step20_even_when_hold_clean(tmp_path):
    capture(tmp_path, 20)
    result = step20(tmp_path)
    assert result.status == 'FAIL', result
    doc = json.loads((tmp_path / 'reports/phase3/pnr/hold_area_budget.json').read_text())
    assert doc['reason'] == 'AREA_BUDGET_EXCEEDED'
    assert doc['overhead_pct'] == pytest.approx(9.090909090909092)
    assert any(r['program'] == 'hold_area_budget_check' and r['enforcement'] == 'GATE'
               and r['verdict'] == 'FAIL' for r in result.program_output_records)


@pytest.mark.parametrize('name,count,negative,stale,expected', [
    ('positive_original_2', 2, False, False, 'PASS'),
    ('negative_hold_original', 2, True, False, 'FAIL'),
    ('negative_stale_original', 2, False, True, 'FAIL')])
def test_original_integrity_siblings(tmp_path, name, count, negative, stale, expected):
    capture(tmp_path, count, negative_hold=negative, stale=stale)
    assert step20(tmp_path).status == expected


def test_reverse_adoption_bypass_recovers_original_false_pass(tmp_path):
    capture(tmp_path, 20)
    assert step20(tmp_path, bypass=True).status == 'PASS'
    assert json.loads((tmp_path / 'reports/phase3/pnr/hold_area_budget.json').read_text())['verdict'] == 'FAIL'


@pytest.mark.parametrize('missing', ['producer', 'source', 'receipt'])
def test_missing_current_area_is_unmeasured_with_clean_hold(tmp_path, missing):
    before, after = capture(tmp_path)
    if missing == 'producer': (tmp_path / 'reports/phase3/pnr/hold_area.json').unlink()
    elif missing == 'source': before.unlink()
    else:
        p = tmp_path / F.llev.RECEIPT_REL; d = json.loads(p.read_text());d.pop('hold_area_sources_sha256');put(p,d)
    assert step20(tmp_path).status == 'NOT_MEASURED'


def test_numeric_failure_wins_over_missing_other_binding(tmp_path):
    capture(tmp_path, 20)
    p = tmp_path / F.llev.RECEIPT_REL;d=json.loads(p.read_text());d.pop('hold_area_sources_sha256');put(p,d)
    verdict,rc,d=area.project_area_result(tmp_path,tmp_path/'reports/phase3/pnr/hold_area.json')
    assert verdict == 'FAIL' and rc == 1
    assert d['verdict'] == 'FAIL' and d['reason'] == 'AREA_BUDGET_EXCEEDED' and d['binding_problem']
    assert step20(tmp_path).status == 'FAIL'


@pytest.mark.parametrize('change', ['stale_state', 'area_value', 'count_value', 'foreign_pair', 'selected_arm'])
def test_copied_or_changed_area_cannot_pass(tmp_path, change):
    before, after = capture(tmp_path)
    path=tmp_path/'reports/phase3/pnr/hold_area.json';doc=json.loads(path.read_text())
    if change == 'stale_state':
        data=json.loads(after.read_text());data['metrics']['design__instance__area__stdcell']=1100;put(after,data)
    elif change == 'area_value': doc['after_total_area']=1011;put(path,doc)
    elif change == 'count_value': doc['hold_buffer_count']=True;put(path,doc)
    elif change == 'selected_arm':
        p=tmp_path/F.llev.RECEIPT_REL;d=json.loads(p.read_text());d['selected']='openroad';put(p,d)
    else:
        foreign = tmp_path/'foreign';capture(foreign)
        data=json.loads((foreign/'reports/phase3/pnr/hold_area.json').read_text())
        data['sources']={k:'foreign/'+v for k,v in data['sources'].items()};put(path,data)
    assert area.main([str(tmp_path)]) == 4
    assert step20(tmp_path).status != 'PASS'


@pytest.mark.parametrize('before,count,largest,setup,inserted,total,expected', [
    (1000,400,10,0,6,1060,'FAIL'),
    (100000,40000,10,0,500,105000,'PASS'),
    (1000000,40000,100,20000,650,1165000,'FAIL'),
    (1000000,40000,100,0,500,1050000,'PASS')])
def test_original_numeric_values_and_owning_adoption(tmp_path,before,count,largest,setup,inserted,total,expected):
    # These are immutable OLD captures, not insertions attributed to new ABI.
    a=put(tmp_path/'before/state_out.json',{'metrics':{'design__instance__area__stdcell':before,'design__instance__count':count}})
    b=put(tmp_path/'after/state_out.json',{'metrics':{'design__instance__area__stdcell':total,'design__instance__count':count+setup+inserted,
        'design__instance__count__hold_buffer':inserted,'design__instance__count__setup_buffer':setup}})
    hold_verdict,_,hold=area.evaluate(inserted*largest,total)
    assert hold_verdict == expected
    if expected == 'PASS': assert hold['overhead_pct'] == pytest.approx(4.761904761904762)
    doc,report,rc=area.check_state_pair(tmp_path,a,b)
    assert report['verdict']==expected
    assert (rc==0)==(expected=='PASS')
    assert doc['after_total_area']==total and doc['before_total_area']==before
    if setup: assert report['hold_buffer_area']==165000 # retained upper-bound meaning


def test_current_schema_refuses_before_resizer_and_adoption(tmp_path,monkeypatch):
    F.state_the_image(monkeypatch)
    run=F._run_split(tmp_path,monkeypatch)
    assert run.rc != 0 and 'LL_HOLD_ABSOLUTE_CAP_UNSUPPORTED' in run.out
    assert run.seen['steps'][-1]=='Vibeic.ExternalCaptureLaunchRetap'
    assert not (run.out_dir/'post_hold.def').exists()
    assert not any('pnr_cts_tail.tcl' in x for x in run.execs)


@pytest.mark.parametrize('inserted,reason',[(20,'AREA_BUDGET_EXCEEDED'),(2,'LL_HOLD_ABSOLUTE_CAP_UNSUPPORTED')])
def test_adoption_rejects_actual_area_fail_before_missing_native_attestation(tmp_path,monkeypatch,inserted,reason):
    # Deliberately expose proposed schema with OLD captured writes; no ABI PASS.
    real=F._fake_tool_run
    def factory(project,corners,**kwargs):
        chain,resolve,seen=real(project,corners,**kwargs)
        def resolver(*a,**kw):
            configs=resolve(*a,**kw);p=configs['OpenROAD.ResizerTimingPostCTS'];d=json.loads(p.read_text());d['PL_RESIZER_HOLD_MAX_BUFFER_COUNT']=None;put(p,d);return configs
        def captured(*a,**kw):
            folders=chain(*a,**kw)
            for folder in folders:
                if 'resizertimingpostcts' in folder.name:
                    p=folder/'state_out.json';d=json.loads(p.read_text());d['metrics'].update(
                        design__instance__count__hold_buffer=inserted,design__instance__area__stdcell=1000.0+inserted*5.0,
                        design__instance__count=400+inserted);put(p,d)
            return folders
        return captured,resolver,seen
    monkeypatch.setattr(F,'_fake_tool_run',factory);F.state_the_image(monkeypatch)
    run=F._run_split(tmp_path,monkeypatch)
    assert run.rc==1 and reason in run.out
    assert not (run.out_dir/'post_hold.def').exists()
    assert not any('pnr_cts_tail.tcl' in x for x in run.execs)


@pytest.mark.parametrize('metrics', [{},{'design__instance__count':400},
    {'design__instance__count':0,'design__instance__area__stdcell':1000},
    {'design__instance__count':400,'design__instance__area__stdcell':0},
    {'design__instance__count':'400','design__instance__area__stdcell':1000},
    {'design__instance__count':400,'design__instance__area__stdcell':'1000'},
    {'design__instance__count':float('inf'),'design__instance__area__stdcell':1000},
    {'design__instance__count':400,'design__instance__area__stdcell':True}])
def test_unmeasured_budget_basis_refuses(tmp_path,metrics):
    state,cfg,root=F._pct_fixture(tmp_path,metrics)
    with pytest.raises(contract.Refusal,match='LL_HOLD_BUDGET_UNMEASURED'):
        cts.hold_buffer_pct(F.runner,state,cfg,root,'pdkX')


@pytest.mark.parametrize('excluded,limit',[([],5),(['neutral__dly_*'],10)])
def test_largest_lef_and_inherited_exclusions_are_retained(tmp_path,excluded,limit):
    state,cfg,root=F._pct_fixture(tmp_path,{'design__instance__count':400,'design__instance__area__stdcell':1000},excluded)
    _,d=cts.hold_buffer_pct(F.runner,state,cfg,root,'pdkX')
    assert d['max_buffers']==limit and d['area_budget_pct']==5


def test_zero_budget_remains_zero_and_requires_native_support(tmp_path):
    state,cfg,root=F._pct_fixture(tmp_path,{'design__instance__count':400.0,'design__instance__area__stdcell':1.0})
    pct,d=cts.hold_buffer_pct(F.runner,state,cfg,root,'pdkX')
    assert pct==0 and d['max_buffers']==0


def test_candidate_percentage_keeps_absolute_area_ceiling(tmp_path,monkeypatch):
    real=F._fake_tool_run
    def factory(project,corners,**kwargs):
        chain,resolve,seen=real(project,corners,**kwargs)
        def resolver(*a,**kw):
            configs=resolve(*a,**kw);p=configs['OpenROAD.ResizerTimingPostCTS']
            d=json.loads(p.read_text());d['PL_RESIZER_HOLD_MAX_BUFFER_COUNT']=None;put(p,d)
            return configs
        return chain,resolver,seen
    monkeypatch.setattr(F,'_fake_tool_run',factory);F.state_the_image(monkeypatch)
    run=F._run_split(tmp_path,monkeypatch,overlay_extra={
        'PL_RESIZER_HOLD_MAX_BUFFER_PCT':(0.5,'bounded candidate')})
    assert run.rc==1 and 'LL_HOLD_ABSOLUTE_CAP_UNSUPPORTED' in run.out
    cfg=next(p for p in run.seen['configs'] if 'resizertimingpostcts' in p.name.lower())
    doc=json.loads(cfg.read_text())
    assert doc['PL_RESIZER_HOLD_MAX_BUFFER_COUNT']==5
    assert doc['PL_RESIZER_HOLD_MAX_BUFFER_PCT']==0.5
    assert not (run.out_dir/'post_hold.def').exists()

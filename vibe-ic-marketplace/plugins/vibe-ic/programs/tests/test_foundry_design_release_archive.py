"""Portable packaging tests; measurement-owner leaves are neutral substitutions.

No simulator, PV tool, IC, or real foundry acceptance is exercised here.
The actual generator, file copies, canonical YAML command and checker execute.
"""
import copy
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import foundry_handoff_pack_gen as gen
import foundry_handoff_package_check as check
from test_foundry_handoff_names_its_owner import _project,_precheck
import _step5_trace_fixture as step5

PROGRAMS=Path(__file__).resolve().parents[1]
ROLES=('final_gds','physical_reference_netlist','die_stack','stream_inputs',
       'layer_map','stream_log','tool_provenance','drc_report','lvs_report',
       'antenna_report','density_report','sta_report','sdc','spef')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def neutral(tmp_path,monkeypatch,damage=None):
    project=_project(tmp_path,with_chip_gds=True)
    _precheck(project,verdict='PASS');step5.plant(project)
    layout=project/'phase3/stage4/gds/alpha.gds'
    sources={str(layout.resolve()):{'sha256':digest(layout),'roles':['final_gds']}}
    data=project/'neutral_measurement_owner';data.mkdir()
    for role in ROLES[1:]:
        path=data/(role+'.txt');path.write_text('Neutral retained material: '+role+'\n')
        sources[str(path.resolve())]={'sha256':digest(path),'roles':[role]}
    validations={kind:{'verdict':'PASS','gds_sha256':digest(layout),
                         'rows':{'neutral_count':{'value':0}}}
                 for kind in ('drc','lvs','antenna','density')}
    validations.update({kind:{'verdict':'PASS'} for kind in ('die_stack','stream','sta')})
    if damage=='missing_lvs':
        validations['lvs']={'verdict':'NOT_MEASURED','reason':'current final-GDS LVS not produced'}
        sources={k:v for k,v in sources.items() if 'lvs_report' not in v['roles']}
    elif damage=='old_subject':validations['lvs']['gds_sha256']='0'*64
    elif damage=='measured_fail':validations['lvs']['verdict']='FAIL';validations['lvs']['rows']['neutral_count']['value']=2
    source={'files':sources,'validation':validations,'external_dependencies':{}}
    # Only measurement/readset discovery is substituted. Old code ignores this
    # neutral leaf, so baseline fails on missing copied values / wrong gate rc,
    # never because a newly added API or flag is unavailable.
    monkeypatch.setattr(check,'design_release_sources',lambda *a,**kw:copy.deepcopy(source),raising=False)
    return project,source


def canonical_gate(project):
    import _flow_yaml
    row=next(row for row in _flow_yaml.load()['steps'] if str(row['id'])=='38')
    command=row['gate']['all_of'][0]['program_exit_zero']
    argv=shlex.split(command);argv[0]=str(PROGRAMS/(argv[0]+'.py'))
    result=subprocess.run([sys.executable,*argv],cwd=project,text=True,capture_output=True)
    report=json.loads((project/'reports/phase3/foundry_handoff_check.json').read_text())
    return result,report,command


def test_normal_producer_copies_real_release_values_and_validates_portably(tmp_path,monkeypatch):
    project,source=neutral(tmp_path,monkeypatch)
    assert gen.main([str(project)])==0
    package=project/'phase3/stage4/foundry_handoff'
    path=package/'release_manifest.json'
    manifest=json.loads(path.read_text()) if path.is_file() else {}
    observed={role:sorted(e['sha256'] for e in manifest.get('files',[]) if role in e['roles']) for role in ROLES}
    expected={role:sorted(e['sha256'] for e in source['files'].values() if role in e['roles']) for role in ROLES}
    assert observed==expected,{'observed':observed,'expected':expected}
    for entry in manifest['files']:
        assert digest(package/entry['path'])==entry['sha256']
    assert {p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()} == {
        e['path'] for e in manifest['files']} | {'release_manifest.json','SHA256SUMS'}
    result,report,command=canonical_gate(project)
    assert result.returncode==0 and report['verdict']=='PASS',(result.stdout,result.stderr,report)
    assert report['scope']=='DESIGN_RELEASE' and report['technical_release_complete'] is True
    # Manufacturing-service disclosures remain honest and do not become seven
    # universal design-release requirements.
    assert report['pending_foundry_fields']
    assert manifest['provider_submission']['accepted'] is False
    archive=tmp_path/'release.tar'
    with tarfile.open(archive,'w') as tar:tar.add(package,arcname='release')
    held=tmp_path/'original-held';project.rename(held)
    moved=tmp_path/'relocated';moved.mkdir()
    with tarfile.open(archive) as tar:tar.extractall(moved)
    relocated=moved/'release'
    out=tmp_path/'portable-check.json'
    result=subprocess.run([sys.executable,str(PROGRAMS/'foundry_handoff_package_check.py'),str(relocated),
                           '--package-dir',str(relocated),'--json',str(out)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(out.read_text())['verdict']=='PASS'
    print('PORTABLE_VALUES',json.dumps({'roles':observed,'gate':command,'relocated_rc':result.returncode}))


@pytest.mark.parametrize('damage',['missing_lvs','old_subject','measured_fail'])
def test_normal_step38_gate_refuses_missing_stale_or_failed_final_gds_lvs(tmp_path,monkeypatch,damage):
    project,_source=neutral(tmp_path,monkeypatch,damage)
    assert gen.main([str(project)])==0  # Keep useful partial packaging work.
    result,report,command=canonical_gate(project)
    observed={'rc':result.returncode,'verdict':report['verdict']}
    assert observed=={'rc':1,'verdict':'FAIL'},observed
    assert any(f['rule']=='DESIGN_RELEASE_INCOMPLETE' for f in report['findings']),report
    print('NORMAL_RELEASE_REFUSAL',damage,json.dumps({'actual':observed,'command':command,'findings':report['findings']}))


def test_new_timestamp_destination_preserves_source_and_rejects_corrupt_members(tmp_path,monkeypatch):
    project,_source=neutral(tmp_path,monkeypatch)
    before={str(p.relative_to(project)):digest(p) for p in project.rglob('*') if p.is_file()}
    destination=tmp_path/'release-version-one'
    assert gen.main([str(project),'--out-dir',str(destination)])==0
    after={str(p.relative_to(project)):digest(p) for p in project.rglob('*') if p.is_file()}
    assert before==after
    assert not (project/'phase3/stage4/foundry_handoff').exists()
    doc,bad=check.check_design_release(destination);assert not bad,bad
    member=next(e for e in doc['files'] if 'lvs_report' in e['roles'])
    (destination/member['path']).write_text('corrupted current report\n')
    _doc,bad=check.check_design_release(destination)
    assert any('changed archive member' in problem for problem in bad),bad
    assert gen.main([str(project),'--out-dir',str(destination)])==1
    assert before=={str(p.relative_to(project)):digest(p) for p in project.rglob('*') if p.is_file()}


# Reproduce the two independent review findings with the real existing STA
# evaluator and real package entries. Native-output construction/admission and
# unrelated archive readsets remain neutral; no EDA tool is dispatched.
_REAL_RELEASE_SOURCES = check.design_release_sources


@pytest.mark.parametrize('scenario,expected',[
    ('clean','PASS'),('negative','FAIL'),('missing_metric','NOT_MEASURED'),
    ('missing_report','NOT_MEASURED')])
def test_archive_sta_uses_the_existing_signoff_consumer(tmp_path,monkeypatch,scenario,expected):
    from test_f15_step23_gates_read_the_tool_arm import tool_project,rebind,run_gate,STA,CORNERS
    setup={CORNERS[0]:-0.25} if scenario=='negative' else None
    timed=tool_project(tmp_path/'timed',setup=setup)
    if scenario=='missing_metric':
        # A current, bound native report that contains no measured slack.
        for corner in CORNERS:
            (timed/STA/corner/'vibeic_signoff.rpt').write_text(
                'OpenSTA report_checks\nSTA_BASIS: POST_ROUTE_SPEF\n'
                'Startpoint: launch\nEndpoint: capture\nPath Type: max\n'
                'Setup and hold values unavailable\n')
        rebind(timed)
    elif scenario=='missing_report':
        (timed/STA/CORNERS[0]/'vibeic_signoff.rpt').unlink()
    sta_rc,sta_doc=run_gate('sta_report_check',timed,tmp_path)
    assert sta_rc==(0 if scenario=='clean' else 1),sta_doc
    project,source=neutral(tmp_path/'pack',monkeypatch)
    measured=_REAL_RELEASE_SOURCES(timed,project/'phase3/stage4/gds/alpha.gds')
    source['validation']['sta']=measured['validation']['sta']
    for path,entry in measured['files'].items():
        if any(role in ('sta_report','sdc','spef','timed_reference_netlist','tool_provenance') for role in entry['roles']):
            source['files'][path]=entry
    monkeypatch.setattr(check,'design_release_sources',lambda *a,**kw:copy.deepcopy(source))
    assert gen.main([str(project)])==0
    package=project/'phase3/stage4/foundry_handoff'
    manifest=json.loads((package/'release_manifest.json').read_text())
    actual=manifest['validation']['sta']['verdict']
    observation={'scenario':scenario,'normal_sta_rc':sta_rc,'normal_sta':sta_doc,
                 'archive_sta':manifest['validation']['sta'],'expected':expected}
    (tmp_path/'STA_OBSERVATION.json').write_text(json.dumps(observation,indent=2)+'\n')
    assert actual==expected,{'observed':actual,'expected':expected,'normal_sta_rc':sta_rc}
    result,report,_command=canonical_gate(project)
    assert (result.returncode,report['verdict'])==((0,'PASS') if expected=='PASS' else (1,'FAIL'))
    _doc,bad=check.check_design_release(package)
    assert bool(bad)==(expected!='PASS'),bad
    print('STA_CONSUMER_VALUES',json.dumps(observation))


def _provider_package(tmp_path,monkeypatch,*,old='self',current='PASS',missing=False):
    project,source=neutral(tmp_path,monkeypatch)
    old_path=project/'reports/phase3/shuttle_precheck.json'
    if old=='self':old_path.write_text(json.dumps({'verdict':'NOT_APPLICABLE','verdict_is_the_operators':False}))
    elif old=='unreadable':old_path.write_text('{ original checkpoint unreadable')
    else:_precheck(project,verdict=old)
    layout=project/'phase3/stage4/gds/alpha.gds'
    folder=project/'reports/phase3/current-provider';folder.mkdir(parents=True)
    current_path=folder/'precheck.json'
    current_path.write_text(json.dumps({'provider':'current_mpw','status':current,'layout_sha256':digest(layout)}))
    selected={'provider':'current_mpw','slot':'engineering_full','cob':False,
              'official_revision':'neutral_current_revision','status':current,
              'layout_sha256':digest(layout),'source':{'path':str(current_path),'sha256':digest(current_path)},
              'options':{'target':'engineering only; no purchase'},'files':[]}
    record=project/'reports/phase3/provider_die_assembly.json';record.write_text('{}\n')
    assembly={'gds':layout,'sha256':digest(layout),'report':record,'source_project':project,
              'basis':{} if missing else {'provider_precheck':selected},'scope':'provider_die_assembly'}
    # Only the finishing producer's already validated admission is substituted.
    # The current provider files/hash are real neutral files, read by package
    # mode selection and the canonical checker after the leaf returns.
    monkeypatch.setattr(check,'admitted_provider_release',lambda *a,**kw:copy.deepcopy(assembly))
    source['files'][str(current_path)]={'sha256':digest(current_path),'roles':['provider_precheck']}
    source['provider_submission']={'accepted':False,'technical_precheck_status':current if not missing else 'NOT_MEASURED',
                                   'provider':'current_mpw','retained_report':selected['source']}
    source['validation']['provider_precheck']={'verdict':current if not missing else 'NOT_MEASURED','gds_sha256':digest(layout)}
    monkeypatch.setattr(check,'design_release_sources',lambda *a,**kw:copy.deepcopy(source))
    return project,source,current_path


def _in_process_canonical_gate(project,monkeypatch):
    import _flow_yaml
    row=next(row for row in _flow_yaml.load()['steps'] if str(row['id'])=='38')
    command=row['gate']['all_of'][0]['program_exit_zero']
    with monkeypatch.context() as m:
        m.chdir(project)
        rc=check.main(shlex.split(command)[1:])
    return rc,json.loads((project/'reports/phase3/foundry_handoff_check.json').read_text()),command


@pytest.mark.parametrize('old',['self','FAIL','unreadable'])
def test_selected_current_provider_owns_mode_and_normal_gate(tmp_path,monkeypatch,old):
    project,_source,current=_provider_package(tmp_path,monkeypatch,old=old)
    before=digest(project/'reports/phase3/shuttle_precheck.json')
    assert gen.main([str(project)])==0
    package=project/'phase3/stage4/foundry_handoff'
    mask=json.loads((package/'mask_spec.json').read_text())
    mode=mask['handoff_mode']
    observed=(mode['mode'],mode['operator'],mode['precheck_verdict'])
    expected=('shuttle','current_mpw','PASS')
    (tmp_path/'PROVIDER_OBSERVATION.json').write_text(json.dumps({'old':old,'observed':observed,'expected':expected,'mode':mode},indent=2)+'\n')
    assert observed==expected,{'observed':observed,'expected':expected}
    rc,report,command=_in_process_canonical_gate(project,monkeypatch)
    assert (rc,report['verdict'])==(0,'PASS'),report
    assert digest(project/'reports/phase3/shuttle_precheck.json')==before
    manifest=json.loads((package/'release_manifest.json').read_text())
    assert manifest['provider_submission']['accepted'] is False
    assert manifest['provider_submission']['retained_report']['sha256']==digest(current)
    print('CURRENT_PROVIDER_VALUES',json.dumps({'old':old,'mode':mode,'gate':command,'rc':rc,'verdict':report['verdict']}))


def test_current_provider_refusal_or_missing_evidence_never_uses_old_pass(tmp_path,monkeypatch):
    for state,missing in [('FAIL',False),('NOT_MEASURED',False),('PASS',True)]:
        with monkeypatch.context() as m:
            project,_source,_current=_provider_package(tmp_path/(state+str(missing)),m,old='PASS',current=state,missing=missing)
            assert gen.main([str(project)])==0
            rc,report,command=_in_process_canonical_gate(project,m)
            assert (rc,report['verdict'])==(1,'FAIL'),report
            print('CURRENT_PROVIDER_REFUSAL',json.dumps({'selected':state,'missing_basis':missing,'rc':rc,'verdict':report['verdict'],'gate':command}))

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

"""D22 actual Default owner controls; test references are not PDK qualification."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
import flow109
import flow109_frontend as frontend
import flow109_admission as admission


def put(project, name, value):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return {'path': str(path.relative_to(project)), 'sha256': frontend._sha(path)}


@pytest.fixture(autouse=True)
def actual_attested_image_route(monkeypatch):
    import librelane_contract
    # Tests execute inside the pinned, host-attested image. Do not attach
    # an unrelated ambient container merely because Docker is reachable.
    librelane_contract.local_image_attestation()
    monkeypatch.setattr(librelane_contract._ce, "no_container_route", lambda: True)


def case(project):
    import fnmatch
    from librelane.config import Config
    from librelane.steps.yosys import EQY
    cfg, _ = Config.load({'PDK':'gf180mcuD','STD_CELL_LIBRARY':'gf180mcu_fd_sc_mcu7t5v0',
        'DESIGN_NAME':'unit_alpha','VERILOG_FILES':[]}, EQY.get_all_config_variables(),
        design_dir=str(project),pdk_root='/foss/pdks')
    libs = [p for pattern,group in cfg['CELL_LIBS'].items()
            if fnmatch.fnmatch(cfg['DEFAULT_CORNER'],pattern) for p in group]
    assert len(libs)==1
    lib=Path(libs[0]);deck={'path':str(lib),'sha256':frontend._sha(lib)}
    put(project,'input/project.json',{'pdk':'gf180mcuD'})
    put(project,'input/submission_template/tapeout_declaration.json',
        {'answers':{'deliverable':'DIE','top_cell':'unit_alpha','fp_sizing':'absolute',
                    'die_area_um':[0,0,100,100],'core_area_um':[10,10,90,90]},
         'answer_provenance':{'deliverable':{'answered_by':'owner','citation':'Developer neutral IC control'}}})
    put(project,'phase1/generated_docs/L8_TIMING_WAVEFORM.json',{'clock_domains':[{'role':'primary','source_pin':'clk','period_ns':20}]})
    put(project,'phase1/generated_docs/L9_INTEGRATION_SPEC.json',{'top_module':'unit_alpha','synth_top':'unit_alpha'})
    put(project,'phase1/generated_docs/L19_CONSTRAINTS_PDK.json',{'pdk':'gf180mcuD','constraint_declarations':[],'fields':{'die_area_budget_um':10000}})
    put(project,'phase2/stage1/rtl/unit_alpha.v','module unit_alpha(input clk, input a,b, output reg y); always @(posedge clk) y <= a ^ b; endmodule\n')
    put(project,'phase3/librelane_switch.json',{'steps':{'9':'librelane'},'pdk_root':'/foss/pdks'})
    put(project,frontend.INPUT,{'schema':frontend.SCHEMA,'pdk':'gf180mcuD','top':'unit_alpha'})
    operation=put(project,'input/d22-operation.json',{'schema':frontend.SCHEMA,'operation':'Default.NativeMappedSynthesis','pdk':'gf180mcuD','top':'unit_alpha'})
    pdk=put(project,'input/d22-pdk.json',{'pdk':'gf180mcuD','root':'/foss/pdks','liberty':deck})
    reference=put(project,'input/control-reference.json',{'scope':'developer contract control only; not production qualification'})
    features=next(p['features'] for p in flow109.load_registry()['reference_profiles'] if p['name']=='Small digital IC without DFT')
    plan=flow109.resolve_plan(features);uses=[];records=[]
    sources=frontend.operation_sources()
    for use in flow109.resolve_step(plan,'D22')['tool_uses']:
        binary=Path(shutil.which(use['executable_name'])).resolve()
        executable={'path':str(binary),'sha256':frontend._sha(binary)}
        identity={'use_id':use['use_id'],'tool_id':use['tool_id'],'version':'developer control',
            'build_sha256':executable['sha256'],'executable':executable,'config':operation,'pdk':pdk,'deck':deck,
            'corner':cfg['DEFAULT_CORNER'],'semantics':{'purpose':use['purpose'],'native_steps':use['native_steps'],'operation_sources':sources}}
        record={'use_id':use['use_id'],'status':'QUALIFIED','identity':identity,'reference_evidence':[reference]}
        records.append(record);uses.append({**identity,'qualification_sha256':admission.canonical_sha(record)})
    put(project,'qualifications.json',{'schema':'vibeic.flow109.qualifications.v1','uses':records})
    context=flow109.execution_context(plan,'D22','D22',runtime_tool_uses=uses,qualification_registry='qualifications.json')
    put(project,'manifest.json',{'features':features,'runtime_tool_uses':uses,'qualification_registry':'qualifications.json'})
    request=project/'reports/flow109/requests/context.json';request.parent.mkdir(parents=True);request.write_text(json.dumps(context))
    return context,request


def test_actual_default_synthesis_uses_native_librelane_and_complete_existing_consumer(tmp_path,monkeypatch):
    context,path=case(tmp_path);monkeypatch.setenv('VIBEIC_FLOW109_CONTEXT',str(path))
    result=frontend.execute(tmp_path,context)
    print('D22_NATIVE_RESULT', json.dumps({k:result.get(k) for k in ('component_status','reason','producer','native_operations','native_receipt','detail')}, default=str))
    assert result['native_started'], result
    assert 'Yosys.Synthesis' in result['native_operations']
    receipt=tmp_path/result['native_receipt']['path']
    assert frontend._sha(receipt)==result['native_receipt']['sha256']
    assert result['flow109_acceptance']=='NOT_MEASURED'
    assert result['producer']['status']=='PASS',result
    assert result['canonical_current_pass'],result
    assert result['component_status']=='PASS',result


@pytest.mark.parametrize('damage',['unsupported','source','deck','declaration'])
def test_missing_or_changed_qualification_stops_before_existing_owner(tmp_path,monkeypatch,damage):
    context,path=case(tmp_path);monkeypatch.setenv('VIBEIC_FLOW109_CONTEXT',str(path))
    if damage=='declaration': (tmp_path/'input/submission_template/tapeout_declaration.json').unlink()
    elif damage=='source': monkeypatch.setattr(frontend,'operation_sources',lambda:{'changed':'source'})
    elif damage=='deck': context['runtime_tool_uses'][0]['deck']['sha256']='f'*64
    else:
        file=tmp_path/'qualifications.json';x=json.loads(file.read_text());x['uses'][0]['status']='UNSUPPORTED';file.write_text(json.dumps(x))
    calls=[]
    import step_preflight
    monkeypatch.setattr(step_preflight,'retained_call',lambda *a,**k:calls.append('owner'))
    result=frontend.execute(tmp_path,context)
    assert result['component_status']=='NOT_MEASURED' and not result['native_started'],result
    assert calls==[]


def test_native_contract_launch_rechecks_original_input_census(tmp_path,monkeypatch):
    context,path=case(tmp_path);monkeypatch.setenv('VIBEIC_FLOW109_CONTEXT',str(path))
    prepared=frontend.prepare(tmp_path,context)
    active={'project':tmp_path.resolve(),'context':context,'prepared':prepared,'operations':[]}
    token=frontend._ACTIVE.set(active)
    try:
        source=tmp_path/'phase2/stage1/rtl/unit_alpha.v';source.write_text(source.read_text()+'\n// changed\n')
        with pytest.raises(admission.AdmissionRefusal,match='D22_INPUT_CHANGED'):
            frontend.require_librelane_launch(tmp_path,'Yosys.Synthesis')
        assert active['operations']==[]
    finally:frontend._ACTIVE.reset(token)

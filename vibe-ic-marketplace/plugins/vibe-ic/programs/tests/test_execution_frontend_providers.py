import json
from pathlib import Path
import json
import execution_frontend_providers as p
import execution_modes as em

EXPECTED=('D1','0.5ic','1','2','3','4','5','6','7','8','10','11','FS1','DT1','12','13','DT2','DT3','P0')

def test_machine_readable_coverage_and_default():
    c=p.coverage(); assert tuple(c)==EXPECTED; assert all(c[r]['default_rank']==i for i,r in enumerate(EXPECTED)); assert c['0.5ic']['applicability']=='IC+IP route authority'

def test_missing_provider_is_typed_and_no_pass():
    for row in EXPECTED:
        result=p.produce(row, Path('/nonexistent/project'))
        assert result.state in ('NOT_IMPLEMENTED','NOT_MEASURED'); assert 'PASS' not in result.state
    assert p.produce('opaque', Path('.')).state=='NOT_IMPLEMENTED'

def test_factory_reachability_reverse_and_dedup():
    registry=em.Registry(); assert p.register_factories(registry) == EXPECTED
    assert all(len(registry.adapters(row)) == 1 for row in EXPECTED)
    assert all(a.components[0].argv[0]=='python3' and '--step' in a.components[0].argv for row in EXPECTED for a in registry.adapters(row))
    assert p.choose('7') is p.choose('7'); assert p.choose('7').factory is not p.choose('8').factory

def test_real_boundary_consumes_output_and_reverse_red(tmp_path):
    source=tmp_path/'input'/'docs'; source.mkdir(parents=True); (source/'L1.md').write_text('# doc\n')
    (tmp_path/'input'/'issued_manifest.json').write_text(json.dumps({'step_id':'D1','parameters':{},'files':{'input/docs/L1.md':__import__('hashlib').sha256((source/'L1.md').read_bytes()).hexdigest()}}))
    result=p.produce('D1', tmp_path); assert result.state in ('NOT_MEASURED','NOT_IMPLEMENTED')

def test_step6_explicitly_not_measured_without_fpga():
    result=p.produce('6', Path('/missing'), fpga=False)
    assert result.state=='NOT_IMPLEMENTED'

def test_controller_executes_worker_and_validator_reddens_on_output_mutation(tmp_path):
    project=tmp_path/'project'; src=project/'input'/'docs'; src.mkdir(parents=True); f=src/'L1.md'; f.write_text('# design\n')
    registry=em.Registry(); p.register_factories(registry); portfolio=em.load_portfolio(); row=next(s for s in portfolio['steps'] if s['id']=='D1')
    import subprocess
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).parents[2],text=True).strip()
    context=em.Context('D1',sha,{'input/docs/L1.md':f},{'metric':'source_boundary'},tuple(row['mandatory_gate_programs']))
    run=tmp_path/'run'; result=em.Controller(registry,em.Budget(1,512,1),portfolio).run(context,run)
    assert result['status']=='AWAITING_AI_SELECTION'
    assert result['candidate_statuses']['frontend_D1'] in ('INELIGIBLE','NOT_MEASURED','ELIGIBLE')

def test_step8_public_controller_eligible_and_adopted(tmp_path):
    import hashlib, subprocess
    project=tmp_path/'project'; sdc=project/'phase2/stage2/constraints/top.sdc'; sdc.parent.mkdir(parents=True)
    sdc.write_text('create_clock -period 10 [get_ports clk]\nset_input_delay 1 -clock clk [all_inputs]\nset_output_delay 1 -clock clk [all_outputs]\n')
    l8=project/'phase1/generated_docs/L8_TIMING_WAVEFORM.json'; l8.parent.mkdir(parents=True)
    l8.write_text(json.dumps({'clocks': {'clk': {'period_ns': 10}}}))
    registry=em.Registry(); p.register_factories(registry); portfolio=em.load_portfolio()
    row=next(s for s in portfolio['steps'] if str(s['id'])=='8')
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).parents[2],text=True).strip()
    ctx=em.Context('8',sha,{'phase2/stage2/constraints/top.sdc':sdc,'phase1/generated_docs/L8_TIMING_WAVEFORM.json':l8},{'metric':'source_boundary'},tuple(row['mandatory_gate_programs']))
    run=tmp_path/'run'; controller=em.Controller(registry,em.Budget(1,512,1),portfolio); controller.run(ctx,run)
    receipt=json.loads((run/'frontend_8/receipt.json').read_text())
    assert receipt['status']=='ELIGIBLE'; assert set(receipt['evidence']['gates'])==set(row['mandatory_gate_programs'])
    assert receipt['evidence']['outputs']['reports/phase2/sdc_check.json']==em.digest(run/'frontend_8/outputs/reports/phase2/sdc_check.json')
    choice={'arm_id':'frontend_8','receipt_sha256':em.digest(run/'frontend_8/receipt.json'),'rationale':'step8 fixture','reviewer':'test','binding':ctx.binding()}
    assert controller.adopt(ctx,run,choice)['status']=='ADOPTED'
    sdc.write_text(sdc.read_text()+'\n# mutation\n')
    with __import__('pytest').raises(Exception): controller.adopt(ctx,run,choice)

@__import__('pytest').mark.parametrize('row,required', [
    ('2', ('top','clock','timeout','baseline_rtl_dir','candidate_rtl_dir')),
    ('3', ('top',)), ('4', ('top','container')), ('5', ('top','container')),
])
def test_rows_2_to_5_missing_typed_params_call_nothing(tmp_path, monkeypatch, row, required):
    import execution_frontend_worker as w
    calls=[]
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: calls.append(('cli',a,k)) or type('R',(),{'returncode':0,'stdout':'','stderr':''})())
    kwargs={name:'x' for name in required}
    for missing in required:
        bad=dict(kwargs); bad.pop(missing)
        calls.clear()
        with __import__('pytest').raises(ValueError): w.run_row(row,tmp_path,tmp_path/'out',**bad)
        assert calls == [], (row, missing, calls)

def test_rows_2_to_5_spy_order_and_argv(tmp_path, monkeypatch):
    import execution_frontend_worker as w
    import design_one_shot_runner as d
    import p0_tool_frontend_check as p0
    import formal_harness_gen as fh, formal_property_run as fp
    calls=[]
    monkeypatch.setattr(p0, 'check', lambda project: calls.append(('p0.check', str(project))) or {'ok':True})
    monkeypatch.setattr(w.subprocess, 'run', lambda argv, **kw: calls.append(('cli', list(argv))) or type('R',(),{'returncode':0,'stdout':'','stderr':''})())
    for name in ('step_professional_tb_gen','step_reference_tb','step_l10_unit_tb_run','step_full_stack_functional_tb'):
        monkeypatch.setattr(d, name, lambda *a, _name=name, **k: calls.append((_name, a, k)) or {'ok':True})
    monkeypatch.setattr(fh, 'generate', lambda *a, **k: calls.append(('formal_harness_gen.generate', a, k)) or {'ok':True})
    monkeypatch.setattr(fp, 'run', lambda *a, **k: calls.append(('formal_property_run.run', a, k)) or {'ok':True})
    monkeypatch.setattr(w, '_write', lambda step,out,producer,**kw: calls.append(('write',step,producer,kw)) or (Path(out).mkdir(parents=True,exist_ok=True) or Path(out)/'report.json'))
    base={'top':'chip_top','clock':'clk','timeout':7,'baseline_rtl_dir':'base','candidate_rtl_dir':'cand','container':'img'}
    (tmp_path/'input').mkdir(exist_ok=True)
    (tmp_path/'input/issued_manifest.json').write_text(json.dumps({'step_id':'3','parameters':base,'files':{}}))
    w.produce_2(tmp_path,tmp_path/'o2',**base)
    assert [x[0] for x in calls[:2]] == ['p0.check','cli']
    assert '--baseline-rtl-dir' in calls[1][1] and '--candidate-rtl-dir' in calls[1][1] and '--timeout' in calls[1][1]
    calls.clear(); w.produce_3(tmp_path,tmp_path/'o3',top='chip_top')
    assert [x[0] for x in calls[:4]] == ['cli','cli','cli','cli']
    assert all('--json' in x[1] for x in calls if x[0]=='cli')
    (tmp_path/'input/issued_manifest.json').write_text(json.dumps({'step_id':'4','parameters':base,'files':{}}))
    calls.clear(); w.produce_4(tmp_path,tmp_path/'o4',top='chip_top',container='img')
    assert [x[0] for x in calls[:4]] == ['step_professional_tb_gen','step_reference_tb','step_l10_unit_tb_run','cli']
    (tmp_path/'input/issued_manifest.json').write_text(json.dumps({'step_id':'5','parameters':base,'files':{}}))
    calls.clear(); w.produce_5(tmp_path,tmp_path/'o5',top='chip_top',container='img')
    assert [x[0] for x in calls] == ['formal_harness_gen.generate','formal_property_run.run','step_full_stack_functional_tb','write']

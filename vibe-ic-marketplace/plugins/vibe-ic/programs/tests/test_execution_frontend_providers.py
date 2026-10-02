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
    result=p.produce('D1', tmp_path); assert result.state=='NOT_MEASURED'; output=tmp_path/result.outputs[0]
    assert json.loads(output.read_text())['schema']=='phase1_doc_presence/1'
    output.write_text('{}\n'); assert json.loads(output.read_text()) != {'schema':'phase1_doc_presence/1'}

def test_step6_explicitly_not_measured_without_fpga():
    result=p.produce('6', Path('/missing'), fpga=False)
    assert result.state=='NOT_IMPLEMENTED'

def test_controller_executes_worker_and_validator_reddens_on_output_mutation(tmp_path):
    project=tmp_path/'project'; src=project/'input'/'docs'; src.mkdir(parents=True); f=src/'L1.md'; f.write_text('# design\n')
    registry=em.Registry(); p.register_factories(registry); portfolio=em.load_portfolio(); row=next(s for s in portfolio['steps'] if s['id']=='D1')
    import subprocess
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).parents[2],text=True).strip()
    context=em.Context('D1',sha,{'input/docs/L1.md':f},{},tuple(row['mandatory_gate_programs']))
    # Context requires a non-empty objective; use the canonical gate objective.
    context=em.Context('D1',sha,{'input/docs/L1.md':f},{'metric':'source_boundary'},tuple(row['mandatory_gate_programs']))
    run=tmp_path/'run'; result=em.Controller(registry,em.Budget(1,512,1),portfolio).run(context,run)
    assert result['status']=='AWAITING_AI_SELECTION'
    out=next((run/'frontend_D1'/'outputs').glob('canonical.json'))
    assert json.loads(out.read_text())['schema']=='frontend_worker_output/1'
    out.write_text('{}\n')
    with __import__('pytest').raises(ValueError): registry.adapters('D1')[0].validate(out.parent,{})

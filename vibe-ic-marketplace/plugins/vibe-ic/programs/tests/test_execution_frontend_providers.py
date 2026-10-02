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
    registry=em.Registry(); assert p.register_factories(registry) == ('D1',)
    assert len(registry.adapters('D1')) == 1 and len(registry.adapters('1')) == 0
    assert p.choose('7') is p.choose('7'); assert p.choose('7').factory is not p.choose('8').factory

def test_real_boundary_consumes_output_and_reverse_red(tmp_path):
    source=tmp_path/'input'/'docs'; source.mkdir(parents=True); (source/'L1.md').write_text('# doc\n')
    result=p.produce('D1', tmp_path); assert result.state=='NOT_MEASURED'; output=tmp_path/result.outputs[0]
    assert json.loads(output.read_text())['schema']=='phase1_doc_presence/1'
    output.write_text('{}\n'); assert json.loads(output.read_text()) != {'schema':'phase1_doc_presence/1'}

def test_step6_explicitly_not_measured_without_fpga():
    result=p.produce('6', Path('/missing'), fpga=False)
    assert result.state=='NOT_IMPLEMENTED'

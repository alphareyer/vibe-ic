import json
from pathlib import Path
import execution_frontend_providers as p

EXPECTED=('D1','0.5ic','1','2','3','4','5','6','7','8','10','11','FS1','DT1','12','13','DT2','DT3','P0')

def test_machine_readable_coverage_and_default():
    c=p.coverage(); assert tuple(c)==EXPECTED; assert all(c[r]['default_rank']==i for i,r in enumerate(EXPECTED)); assert c['0.5ic']['applicability']=='IC+IP route authority'

def test_missing_provider_is_typed_and_no_pass():
    for row in EXPECTED:
        result=p.produce(row, Path('/nonexistent/project'))
        assert result.state in ('NOT_IMPLEMENTED','NOT_MEASURED'); assert 'PASS' not in result.state
    assert p.produce('opaque', Path('.')).state=='NOT_IMPLEMENTED'

def test_factory_reachability_reverse_and_dedup():
    assert p.register_factories(type('R',(),{'register':lambda self, x: None})()) == EXPECTED
    assert p.choose('7') is p.choose('7'); assert p.choose('7').factory is not p.choose('8').factory

def test_step6_explicitly_not_measured_without_fpga():
    result=p.produce('6', Path('/missing'), fpga=False)
    assert result.state=='NOT_IMPLEMENTED'

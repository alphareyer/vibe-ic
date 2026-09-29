"""Run the real producer on supplied INPUT; independent expected carriage."""
import json, sys, shutil, importlib.util
from pathlib import Path
sys.path.insert(0,'/source/vibe-ic-marketplace/plugins/vibe-ic/programs')
import l9_l19_contract_carrythrough as C

project=Path(sys.argv[1])
arm=sys.argv[2]
if len(sys.argv)>3:
    spec=importlib.util.spec_from_file_location('reverted_producer',sys.argv[3])
    C=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(C)
report=C.run(project)
doc=json.loads((project/'phase1/generated_docs/L9_INTEGRATION_SPEC.json').read_text())
contracts=doc.get('integration',{}).get('submodule_contracts',[])
observed=json.dumps(contracts,ensure_ascii=False)
print('PRODUCER_RESULT',json.dumps(report))
print('OBSERVED_SUBMODULE_CONTRACTS',observed)
assert 'instruction bus' in observed and 'data bus' in observed, 'INPUT module-bus contract omitted from L9.integration.submodule_contracts'
assert all(x['source']=='input/docs/L8_submodule_integration.md' for x in contracts)
again=C.run(project)
assert again['emitted_count']==0, 'producer must remain idempotent'
print('PRODUCER_INPUT_CARRIAGE PASS',arm)

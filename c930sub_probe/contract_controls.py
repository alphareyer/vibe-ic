"""Finite callable controls for the producer, without a suite or gate run."""
import contextlib, importlib.util, io, json, tempfile
from pathlib import Path
p=Path('/source/vibe-ic-marketplace/plugins/vibe-ic/programs/tests/test_l9_l19_contract_carrythrough.py')
spec=importlib.util.spec_from_file_location('contract_cases',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ids=[
 'test_submodule_contract_descendants_have_named_carriage',
 'test_denied_submodule_contract_heading_has_no_carriage',
 'test_positive_cross_layer_contracts_reach_their_consumers',
 'test_false_positive_denied_or_untyped_prose_emits_nothing',
 'test_not_applicable_normalization_requires_explicit_status',
 'test_idempotent_existing_consumer_fields_win',
]
for case in ids:
    with tempfile.TemporaryDirectory(prefix='c930sub-contract-') as d:
        getattr(m,case)(Path(d))
    print('FINITE_CONTROL PASS',case)
with tempfile.TemporaryDirectory(prefix='c930sub-degrade-') as d:
    captured=io.StringIO()
    with contextlib.redirect_stdout(captured): rc=m.R._post_emit_l9_l19_contract_carrythrough(Path(d))
    assert rc==0 and 'L9/L19 contract carry-through: SKIPPED' in captured.getvalue()
print('FINITE_CONTROL PASS test_runner_adapter_degrades_loudly_when_consumers_are_absent')
Path('/evidence/contract-controls.json').write_text(json.dumps({'passed':ids+['test_runner_adapter_degrades_loudly_when_consumers_are_absent'],'failures':[]},indent=2)+'\n')

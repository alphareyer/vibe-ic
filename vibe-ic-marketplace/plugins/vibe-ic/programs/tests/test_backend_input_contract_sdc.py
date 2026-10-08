import json
from pathlib import Path
import pytest
import execution_adapters_backend as backend

def _project(tmp_path):
    root=tmp_path/'project'; (root/'phase2/stage2/synth').mkdir(parents=True); (root/'phase2/stage2/constraints').mkdir(parents=True); (root/'phase3/librelane/prelayout/gates').mkdir(parents=True); (root/'phase3/stage3/pnr').mkdir(parents=True)
    (root/'phase2/stage2/synth/netlist.v').write_text('module x; endmodule\n')
    (root/'phase2/stage2/constraints/spm.asic.sdc').write_text('create_clock\n')
    (root/'phase2/stage2/constraints/asic_sdc.json').write_text(json.dumps({'path':'phase2/stage2/constraints/spm.asic.sdc'}))
    for p in ('provenance.jsonl','phase3/librelane/prelayout/gates/step8_sdc_opensta.json','phase3/stage3/pnr/constraint.sdc'):
        q=root/p; q.parent.mkdir(parents=True,exist_ok=True); q.write_text('bound\n')
    return root

def test_backend_contract_carries_canonical_pnr_sdc(tmp_path):
    root=_project(tmp_path); paths=backend.backend_project_input_contract(root,'15')
    assert 'phase3/stage3/pnr/constraint.sdc' in paths

def test_backend_contract_refuses_missing_or_changed_pnr_sdc(tmp_path):
    root=_project(tmp_path); (root/'phase3/stage3/pnr/constraint.sdc').unlink()
    with pytest.raises(ValueError): backend.backend_project_input_contract(root,'15')

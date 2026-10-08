from __future__ import annotations
import json, sys
from pathlib import Path
import pytest
PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path: sys.path.insert(0, str(PROGRAMS))
import execution_policy as policy
import librelane_contract as llc
import phase3_one_shot_runner as phase3

def _fixture(tmp_path):
    project=tmp_path/'project'; netlist=project/'phase2/stage2/synth/spm_synth.v'; declared=project/'phase2/stage2/constraints/spm.asic.sdc'; pnr=project/'phase3/stage3/pnr/constraint.sdc'; rec=project/'phase2/stage2/constraints/asic_sdc.json'
    for p in (netlist,declared,pnr,rec): p.parent.mkdir(parents=True,exist_ok=True)
    netlist.write_text('module spm; endmodule\n'); declared.write_text('create_clock -period 10 [get_ports clk]\n'); pnr.write_text('create_clock -period 9 [get_ports clk]\n'); rec.write_text(json.dumps({'path':str(declared.relative_to(project))}))
    return project,netlist,pnr

def _proof(project,netlist,pnr):
    folder=project/'phase3/librelane/prelayout/design_sdc/native'; folder.mkdir(parents=True,exist_ok=True)
    state=folder/'state_out.json'; fp=folder/'input_fingerprint.json'; pdk=folder/'pdk_root.json'; log=folder/'invocation.log'
    state.write_text(json.dumps({'nl':str(netlist)})); fp.write_text(json.dumps({'step':'OpenROAD.STAPrePNR'})); pdk.write_text('{}'); log.write_text('OpenROAD STAPrePNR\n')
    hashes={p.name:llc.digest(p) for p in (state,fp,pdk,log)}; (folder/'vibeic_receipt.json').write_text(json.dumps({'input':{'step':'OpenROAD.STAPrePNR'},'sha256':hashes}))
    gate=project/'phase3/librelane/prelayout/gates/step8_sdc_opensta.json'; gate.parent.mkdir(parents=True,exist_ok=True); gate.write_text(json.dumps({'verdict':'PASS','netlist_sha256':llc.digest(netlist),'sdc_sha256':llc.digest(pnr),'source':str(state),'state_out_sha256':llc.digest(state)})); return gate

def _runtime(project): return {'project':project,'policy':{'mode':'default'},'parameters':{'top':'spm','pdk':object(),'container':'vibeic-eda'}}

def test_default_step15_uses_real_producer_and_pnr_sdc(tmp_path,monkeypatch):
    project,netlist,pnr=_fixture(tmp_path); calls=[]
    def publish(project_,top,pdk,runner_sdc,staged,modes,notes):
        calls.append((runner_sdc,modes)); _proof(project_,netlist,runner_sdc); return {'sdc':{'verdict':'PASS'}}
    monkeypatch.setattr(phase3,'_prelayout_librelane',publish); policy._ensure_default_prelayout(_runtime(project),'15')
    assert calls==[(pnr,{'7':'librelane','8':'librelane','10':'librelane'})]

def test_default_step15_reuses_fresh_proof_without_producer(tmp_path,monkeypatch):
    project,netlist,pnr=_fixture(tmp_path); _proof(project,netlist,pnr)
    monkeypatch.setattr(phase3,'_prelayout_librelane',lambda *a: pytest.fail('stale producer invoked')); policy._ensure_default_prelayout(_runtime(project),'15')

def test_default_step15_refuses_malformed_proof(tmp_path,monkeypatch):
    project,netlist,pnr=_fixture(tmp_path); gate=_proof(project,netlist,pnr); gate.write_text('[]')
    def fail(*a): raise policy.Refusal('PRELAYOUT_PUBLICATION_FAILED','bad')
    monkeypatch.setattr(phase3,'_prelayout_librelane',fail)
    with pytest.raises(policy.Refusal,match='PRELAYOUT_PUBLICATION_FAILED'): policy._ensure_default_prelayout(_runtime(project),'15')

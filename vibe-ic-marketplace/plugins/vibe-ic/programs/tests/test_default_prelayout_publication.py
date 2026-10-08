from __future__ import annotations
import json, shutil, sys, types
from pathlib import Path
import pytest
PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path: sys.path.insert(0, str(PROGRAMS))
import execution_policy as policy
import design_one_shot_runner as design
import phase3_one_shot_runner as phase3
import test_step8_post_prelayout as postcheck_fixtures

NATIVE_PACKAGE = Path('/home/reyerchu/fleet-artefacts/20261008/spm-prelayout-current101008am/native-staprepnr')

def _fixture(tmp_path):
    project=tmp_path/'project'; netlist=project/'phase2/stage2/synth/spm_synth.v'; declared=project/'phase2/stage2/constraints/spm.asic.sdc'; pnr=project/'phase3/stage3/pnr/constraint.sdc'; rec=project/'phase2/stage2/constraints/asic_sdc.json'
    for p in (netlist,declared,pnr,rec): p.parent.mkdir(parents=True,exist_ok=True)
    netlist.write_text('module spm; endmodule\n'); declared.write_text('create_clock -period 10 [get_ports clk]\n'); pnr.write_text('create_clock -period 9 [get_ports clk]\n'); rec.write_text(json.dumps({'path':str(declared.relative_to(project))}))
    return project,netlist,pnr

def _proof(project,netlist,pnr):
    folder=project/'phase3/librelane/prelayout/design_sdc/native'; folder.mkdir(parents=True,exist_ok=True)
    state=folder/'state_out.json'; fp=folder/'input_fingerprint.json'; pdk=folder/'pdk_root.json'; log=folder/'invocation.log'
    state.write_text(json.dumps({'nl':str(netlist)})); fp.write_text(json.dumps({'step':'OpenROAD.STAPrePNR'})); pdk.write_text('{}'); log.write_text('OpenROAD STAPrePNR\n')
    import hashlib
    digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    hashes={p.name:digest(p) for p in (state,fp,pdk,log)}; (folder/'vibeic_receipt.json').write_text(json.dumps({'input':{'step':'OpenROAD.STAPrePNR'},'sha256':hashes}))
    gate=project/'phase3/librelane/prelayout/gates/step8_sdc_opensta.json'; gate.parent.mkdir(parents=True,exist_ok=True); gate.write_text(json.dumps({'verdict':'PASS','netlist_sha256':digest(netlist),'sdc_sha256':digest(pnr),'source':str(state),'state_out_sha256':digest(state)})); return gate

def _runtime(project): return {'project':project,'policy':{'mode':'default'},'parameters':{'top':'spm','pdk':object(),'container':'vibeic-eda'}}


def test_default_step15_uses_real_producer_and_pnr_sdc(tmp_path,monkeypatch):
    project,netlist,pnr=_fixture(tmp_path); calls=[]
    def publish(project_,top,pdk,runner_sdc,staged,modes,notes):
        calls.append((runner_sdc,modes)); _proof(project_,netlist,runner_sdc); return {'sdc':{'verdict':'PASS'}}
    monkeypatch.setattr(phase3,'_prelayout_librelane',publish); policy._ensure_default_prelayout(_runtime(project),'15')
    assert calls==[(pnr,{'7':'librelane','8':'librelane','10':'librelane'})]

def test_default_step15_calls_existing_producer_each_dispatch(tmp_path,monkeypatch):
    project,netlist,pnr=_fixture(tmp_path)
    calls=[]
    def publish(*args): calls.append(args); _proof(project,netlist,pnr); return {'sdc':{'verdict':'PASS'}}
    monkeypatch.setattr(phase3,'_prelayout_librelane',publish); policy._ensure_default_prelayout(_runtime(project),'15')
    assert len(calls)==1

def test_default_step15_refuses_failed_producer(tmp_path, monkeypatch):
    project, _, pnr = _fixture(tmp_path)
    gate = project / "phase3/librelane/prelayout/gates/step8_sdc_opensta.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps({"verdict": "PASS"}))
    def fail(*args): return {"sdc": {"verdict": "FAIL"}}
    monkeypatch.setattr(phase3, "_prelayout_librelane", fail)
    with pytest.raises(policy.Refusal, match="PRELAYOUT_GATE_IDENTITY_MISMATCH"):
        policy._ensure_default_prelayout(_runtime(project), "15")
    assert json.loads(gate.read_text())["verdict"] == "PASS"


def test_public_dispatch_calls_publication_before_input_census(tmp_path, monkeypatch):
    project, netlist, pnr = _fixture(tmp_path)
    order = []
    adapter = types.SimpleNamespace(applicability="applicable", tool_id="backend", objective={})
    registry = types.SimpleNamespace(adapters=lambda step: [adapter])
    controller = types.SimpleNamespace(portfolio={"steps":[{"id":"15","mandatory_gate_programs":[]}]})
    runtime = {"project": project, "policy":{"mode":"default","request_receipt":{"invocation_id":"x"}},
               "parameters":{"top":"spm","pdk":object(),"container":"vibeic-eda"}, "registry":registry, "controller":controller,
               "contexts":{}, "runs":{}, "route":{"source_sha":"s","project_digest":"p","ic_ip_path":"i"}}
    monkeypatch.setattr(policy, "_ordinary_runtime", runtime)
    def publish(project_, top, pdk, runner_sdc, staged, modes, notes):
        order.append("publish")
        assert runner_sdc == pnr
        (project_ / "phase3/librelane/prelayout/gates").mkdir(parents=True, exist_ok=True)
        (project_ / "phase3/librelane/prelayout/gates/step8_sdc_opensta.json").write_text(json.dumps({"verdict":"PASS"}))
        return {"sdc": {"verdict":"PASS"}}
    monkeypatch.setattr(phase3, "_prelayout_librelane", publish)
    def census(rt, step, **kwargs):
        order.append("census")
        assert (project / "phase3/librelane/prelayout/gates/step8_sdc_opensta.json").is_file()
        assert pnr.is_file()
        return {}
    monkeypatch.setattr(policy, "_fixed_inputs", census)
    result = policy.dispatch_fixed_step(project, "15")
    assert result["status"] == "NOT_MEASURED"
    assert order == ["publish", "census"]


def test_postcheck_accepts_current_native_proof_without_switch(tmp_path):
    gate, deck, netlist = postcheck_fixtures.project(tmp_path)
    folder = tmp_path / 'phase3/librelane/prelayout/design_sdc'
    for source in NATIVE_PACKAGE.iterdir():
        if source.name != 'state_out.json':
            shutil.copyfile(source, folder / source.name)
    state = json.loads((folder / 'state_out.json').read_text())
    state['nl'] = str(netlist)
    (folder / 'state_out.json').write_text(json.dumps(state))
    (folder / 'tt').rename(folder / 'nom_tt_025C_5v00')
    for corner in ('nom_ss_125C_4v50', 'nom_ff_n40C_5v50'):
        shutil.copytree(folder / 'nom_tt_025C_5v00', folder / corner)
    sta_log = Path(postcheck_fixtures.LP.__file__).parent / 'calibration' / 'sta_prepnr_linked_clean_negative.log'
    for corner in ('nom_ss_125C_4v50', 'nom_tt_025C_5v00', 'nom_ff_n40C_5v50'):
        for report in ('max.rpt', 'min.rpt'):
            shutil.copyfile(sta_log, folder / corner / report)
    postcheck_fixtures.LP.judge_sdc(
        folder, {'PNR_SDC_FILE': str(deck)}, deck, gate)
    (tmp_path / 'phase3/librelane_switch.json').unlink()

    row = design.step_sdc_validation_post_prelayout(
        tmp_path, 'top', native_result={'folder': folder})
    assert row.status == 'PASS', row.detail
    receipt = json.loads(Path(row.output_files[0]).read_text())
    assert receipt['binding']['native_proof'] is True
    assert (tmp_path / 'reports/phase2/sdc_check.json').is_file()
    published = phase3.publish_prelayout_reports(tmp_path, folder)
    assert Path(published['pre_pnr_timing']).is_file()
    assert Path(published['pre_pnr_summary']).is_file()


def test_postcheck_rejects_changed_native_proof_before_tools(tmp_path, monkeypatch):
    gate, deck, netlist = postcheck_fixtures.project(tmp_path)
    switch = tmp_path / 'phase3/librelane_switch.json'
    switch.unlink()
    outside = tmp_path.parent / 'changed-native-proof'
    outside.mkdir()
    monkeypatch.setattr(design.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('changed proof must refuse before tools'))
    row = design.step_sdc_validation_post_prelayout(
        tmp_path, 'top', native_result={'folder': outside})
    assert row.status == 'NOT_MEASURED'
    assert 'TOOL_MODE_REQUIRED' in row.detail

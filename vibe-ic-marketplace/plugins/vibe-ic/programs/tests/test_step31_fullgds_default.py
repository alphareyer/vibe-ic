"""Step31 uses current full-GDS LVS; DEF wiring is a disclosed triage arm."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import phase3_one_shot_runner as runner
import librelane_pv_signoff as pv
import librelane_signoff_evidence as evidence


@pytest.mark.parametrize('selection',[None,'direct','librelane','dual'])
def test_step31_canonical_lvs_uses_fullgds_producer(tmp_path,monkeypatch,selection):
    if selection:
        switch=tmp_path/'phase3/librelane_switch.json';switch.parent.mkdir()
        switch.write_text(json.dumps({'steps':{'31':selection}}))
    monkeypatch.setattr(runner,'_vacuous_on_unrouted',lambda *a:None)
    calls=[]
    def producer(p,t,k,half,publish):
        calls.append((half,publish))
        return runner.StepResult(half,'PASS',0,'native full GDS',output_files=['fullgds.rpt'])
    monkeypatch.setattr(runner,'_step31_librelane',producer)
    def triage():
        calls.append(('triage',False))
        return runner.StepResult('lvs','PASS',0,'gate-level wiring')
    result=runner._step31_dispatch(tmp_path,'unit',SimpleNamespace(), 'lvs',triage)
    assert ('lvs',True) in calls, (result.status,result.detail,calls)
    assert calls.count(('triage',False)) == (selection=='dual')
    assert result.output_files==['fullgds.rpt']
    assert result.status=='PASS'
    assert evidence.obligation(tmp_path,'lvs') is not None


def test_default_lvs_consumers_refuse_a_retained_report_without_current_chain(tmp_path):
    report=tmp_path/'reports/phase3/lvs.rpt'; report.parent.mkdir(parents=True)
    report.write_text('Final result: Circuits match uniquely.\n')
    record=evidence.obligation(tmp_path,'lvs')
    assert record is not None, 'default LVS consumer still routes to DEF authority'
    assert record['verdict']=='NOT_MEASURED'


@pytest.mark.parametrize('change',['none','layout','reference','report','measured_failure'])
def test_current_lvs_gate_reads_the_producer_and_rejects_replaced_inputs(
        tmp_path,monkeypatch,capsys,change):
    # Protocol test through the real run_chain/current reader. The external
    # process boundary writes maintenance metrics, not a native tool verdict.
    from _native_signoff_fixture import producer
    from _hostpaths import require_repo
    import lvs_report_check as gate
    info=producer(tmp_path,monkeypatch,half='lvs', counts=(
        {'design__lvs_error__count':1} if change=='measured_failure' else None))
    # Exercise the new DEFAULT selection, without changing DRC selection.
    switch=tmp_path/'phase3/librelane_switch.json'
    doc=json.loads(switch.read_text());doc['steps'].pop('31');switch.write_text(json.dumps(doc))
    report=tmp_path/'reports/phase3/lvs.rpt'
    report.parent.mkdir(parents=True,exist_ok=True)
    artefact=require_repo('vibe-ic-marketplace','plugins','vibe-ic','programs','tests',
                          'fixtures','netgen_json','netgen_lvs_match.rpt')
    native=info['folders'][-1]/'reports/lvs.netgen.rpt';native.parent.mkdir()
    native.write_bytes(artefact.read_bytes());report.write_bytes(native.read_bytes())
    # Parser boundary is separately tested. Here the actual current reader and
    # report binding are the subject; this does not certify the archived report.
    def audit(argv):
        print(json.dumps({'passed':True,'summary':{'files_found':1,'terminal_verdict':'MATCH'},'findings':[]}))
        return 0
    monkeypatch.setattr(gate,'_audit_main',audit)
    monkeypatch.setattr(evidence,'finished_layout',lambda *a,**kw:info['gds'])
    if change=='layout':info['gds'].write_bytes(b'changed actual layout input')
    elif change=='reference':
        state=json.loads(info['state'].read_text());Path(state['pnl']).write_text('changed reference')
    elif change=='report':report.write_text(report.read_text()+'\nreplaced\n')
    target=tmp_path/'audit.json'
    rc=gate.run([str(tmp_path),'--json',str(target)])
    capsys.readouterr()
    result=json.loads(target.read_text())
    if change=='none':
        assert rc==0,result
        assert result['passed'] is True
    else:
        assert rc==1,result
        assert result['passed'] is False
        assert result['verdict']==('FAIL' if change=='measured_failure' else 'NOT_MEASURED')


def test_previous_receipts_survive_unavailable_fullgds_producer(tmp_path,monkeypatch):
    reports=tmp_path/'reports/phase3';reports.mkdir(parents=True)
    previous={
        'librelane_pv_lvs.json':b'{"verdict":"PASS","previous_round":17}\n',
        'lvs_inputs.json':b'{"previous_reference":"round17.v"}\n',
        'lvs_verdict.json':b'{"status":"PASS","previous_round":17}\n',
    }
    for name,data in previous.items():(reports/name).write_bytes(data)
    monkeypatch.setattr(runner,'_vacuous_on_unrouted',lambda *a:None)
    calls=[]
    def unavailable(project,top,pdk,half,publish):
        calls.append((half,publish))
        # No canonical receipt from the previous round may stand for this run.
        assert all(not (reports/name).exists() for name in previous)
        return runner.StepResult('lvs','NOT_MEASURED',0,'fixture: producer unavailable',
                                 reason_class=runner._V.ReasonClass.TOOL_ABSENT)
    monkeypatch.setattr(runner,'_step31_librelane',unavailable)
    result=runner.step_lvs(tmp_path,'unit',SimpleNamespace(),'unused-fixture-container')
    retained={p.name:p.read_bytes() for p in (reports/'lvs_triage').glob('*/*')}
    assert retained==previous
    assert all(not (reports/name).exists() for name in previous)
    assert calls==[('lvs',True)]
    assert result.status=='NOT_MEASURED'
    assert evidence.obligation(tmp_path,'lvs')['verdict']=='NOT_MEASURED'

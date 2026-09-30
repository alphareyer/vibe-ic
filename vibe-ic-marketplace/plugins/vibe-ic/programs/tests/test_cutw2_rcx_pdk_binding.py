"""A native RCX cache must move with mounted PDK rule bytes."""
import json
import subprocess
from pathlib import Path
import pytest
import _plugin_tree  # noqa: F401
import librelane_contract as ll


def setup_case(tmp_path, monkeypatch, *, mutate_during_run=False, missing=False):
    project=tmp_path/'project';project.mkdir()
    pdks=tmp_path/'native-pdk';pdks.mkdir()
    rules=pdks/'rules.rcx';rules.write_text('4')
    if missing:rules.unlink()
    deff=project/'route.def';deff.write_text('VERSION 5.8 ;\nDESIGN top ;\nEND DESIGN\n')
    views={'def':str(deff),'metrics':{}}
    for key,suffix in [('odb','odb'),('nl','v'),('sdc','sdc')]:
        path=project/f'route.{suffix}';path.write_text(f'captured native {key} input')
        views[key]=str(path)
    state=project/'state.json';state.write_text(json.dumps(views))
    config=project/'rcx.json';config.write_text(json.dumps({'meta':{'step':'OpenROAD.RCX'},
       'RCX_RULESETS':{'nom_*':'/pdk/rules.rcx'}}))
    calls=[]
    monkeypatch.setattr(ll,'image_capability',lambda *a,**k:{})
    monkeypatch.setattr(ll,'openroad_home',lambda *a,**k:None)
    def native_writes(argv,**kwargs):
        folder=Path(argv[argv.index('-o')+1]);calls.append(folder)
        value=rules.read_text() if rules.exists() else 'unreadable'
        spef=folder/'route.spef';spef.write_text(f'*R_UNIT 1 OHM\n*RES\n1 victim:1 victim:2 {value}\n')
        (folder/'state_out.json').write_text(json.dumps({**views,
            'spef':{'nom_*':str(spef)},'metrics':{}}))
        if mutate_during_run:rules.write_text('9')
        return subprocess.CompletedProcess(argv,0,'native RCX completed','')
    monkeypatch.setattr(ll,'run_container',native_writes)
    def run():
        return ll.run_chain(project,'captured image',[('OpenROAD.RCX',config,state)],
            mounts=[(pdks,'/pdk')],lane='rcx',pdk_root='/pdk')
    return run,rules,calls


def test_rule_bytes_change_the_native_rcx_result_instead_of_reusing_four_ohms(tmp_path,monkeypatch):
    run,rules,calls=setup_case(tmp_path,monkeypatch)
    first=run()[0];assert 'victim:2 4' in (first/'route.spef').read_text()
    rules.write_text('9')
    second=run()[0]
    assert len(calls)==2
    assert 'victim:2 9' in (second/'route.spef').read_text()


def test_pdk_changed_inside_native_run_is_not_receipted(tmp_path,monkeypatch):
    run,rules,calls=setup_case(tmp_path,monkeypatch,mutate_during_run=True)
    with pytest.raises(ll.Refusal,match='LL_RCX_PDK_CHANGED_DURING_RUN'):run()
    assert len(calls)==1
    assert not (calls[0]/'vibeic_receipt.json').exists()


def test_unreadable_native_rule_bytes_are_refused_before_launch(tmp_path,monkeypatch):
    run,rules,calls=setup_case(tmp_path,monkeypatch,missing=True)
    with pytest.raises(ll.Refusal,match='LL_RCX_PDK_INPUT_UNREADABLE'):run()
    assert calls==[]

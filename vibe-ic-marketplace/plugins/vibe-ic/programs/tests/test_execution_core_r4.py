from pathlib import Path
from dataclasses import replace
import json,os
import pytest
from programs.tests import test_execution_modes as H
from programs.tests.test_execution_receipt_chain import real_entry, isolated_transport
import execution_modes as em
import execution_policy as policy
OUT=Path(os.environ.get('VIBEIC_TEST_EVIDENCE_DIR','/tmp'))
def record(ident,**facts):
 (OUT/(ident+'.json')).write_text(json.dumps(dict(invariant_id=ident,**facts),sort_keys=True,indent=2)+'\n')
def issued_context(tmp_path,mode):
 payload=real_entry('IC',mode,tmp_path)
 base=H.context(tmp_path)
 ctx=em.Context('1',H.BASE,base.inputs,H.OBJECTIVE,('transform',),project_digest=payload['route']['project_digest'],**policy.controller_fields(ic_ip_path='IC',route_receipt=payload['route']))
 return ctx
def public_controller(*arms):
 registry=em.Registry()
 for arm in arms: registry.register(arm)
 return em.Controller(registry,em.Budget(2,512),H.controller().portfolio)
@pytest.mark.parametrize('mode',['default','ultra'])
def test_ISSUED_VALID_CONTROL(tmp_path,mode):
 ctx=issued_context(tmp_path,mode)
 controller=public_controller(H.adapter('a'),H.adapter('b'))
 result=controller.run(ctx,tmp_path/'run')
 if mode=='ultra': result=controller.adopt(ctx,tmp_path/'run',H.choice(ctx,tmp_path/'run','a'))
 record('ISSUED_VALID_CONTROL_'+mode,status=result['status'],intent=ctx.intent_label,arms=json.loads((tmp_path/'run/plan.json').read_text())['arms'])
 assert result['status']=='ADOPTED'
def test_INDEPENDENCE_ARGV_CLOSURE(tmp_path):
 ctx=issued_context(tmp_path,'ultra')
 first=H.adapter('a')
 extra=H.VARIANTS/'b.py'
 sources=dict(first.source_files);sources[str(extra.resolve())]=em.digest(extra)
 first=replace(first,source_files=sources)
 second=replace(first,arm_id='b',tool_id='neutral_alias_b',engine_families=('neutral_alias_b',),components=tuple(replace(c,argv=(*c.argv,str(extra.resolve()))) for c in first.components))
 controller=public_controller(first,second)
 result=controller.run(ctx,tmp_path/'run')
 plan=json.loads((tmp_path/'run/plan.json').read_text())
 statuses=result['candidate_statuses']
 record('INDEPENDENCE_ARGV_CLOSURE',arms=plan['arms'],statuses=statuses,same_sources=dict(first.source_files)==dict(second.source_files),same_executables=[c.argv[0] for c in first.components]==[c.argv[0] for c in second.components])
 assert len(plan['arms'])==1,plan['arms']
def test_ENTRY_SOURCE_BOUND(tmp_path):
 ctx=issued_context(tmp_path,'default')
 first=H.adapter('a')
 selected=Path(first.components[0].argv[1])
 private=tmp_path/'unbound_entry.py';private.write_bytes(selected.read_bytes()+b'\n# private review entry\n')
 revised=replace(first,components=tuple(replace(c,argv=(c.argv[0],str(private),*c.argv[2:])) for c in first.components))
 try:
  controller=public_controller(revised)
  result=controller.run(ctx,tmp_path/'run')
  state=result['status']
 except em.Refusal as exc: state=exc.code
 record('ENTRY_SOURCE_BOUND',status=state,entry_in_declared_manifest=str(private) in revised.source_files,entry_in_source_tree=private.is_relative_to(em._REPO_ROOT))
 assert state!='ADOPTED',state
def test_CONTROLLER_ISSUANCE(tmp_path,monkeypatch):
 for key in list(os.environ):
  if key.startswith('VIBEIC_EXECUTION'):monkeypatch.delenv(key,raising=False)
 ctx=replace(H.context(tmp_path),intent_label='USER_EXPLICIT_ULTRA',request_digest='a'*64)
 controller=public_controller(H.adapter('a'))
 result=controller.run(ctx,tmp_path/'run','ultra-mode')
 result=controller.adopt(ctx,tmp_path/'run',H.choice(ctx,tmp_path/'run','a'))
 record('CONTROLLER_ISSUANCE',status=result['status'],live_capability_present=policy._CAPABILITY_FD_ENV in os.environ,intent=ctx.intent_label,route_kind=ctx.route_receipt['kind'])
 assert result['status']!='ADOPTED',result['status']

from pathlib import Path
from dataclasses import fields
import json,os
import pytest
from programs.tests import test_execution_modes as H
import execution_modes as em
import execution_policy as policy
class CallerContext(em.Context):
 def binding(self):
  return H.NeutralContext.binding(self)
def test_CONTEXT_BASE_REFUSAL(tmp_path,monkeypatch):
 for key in list(os.environ):
  if key.startswith('VIBEIC_EXECUTION'):monkeypatch.delenv(key,raising=False)
 base=H.context(tmp_path)
 ctx=em.Context(**{f.name:getattr(base,f.name) for f in fields(em.Context)})
 with pytest.raises(em.Refusal,match='ROUTE_RECEIPT_INVALID'):ctx.binding()
def test_CONTROLLER_ISSUANCE_EXTERNAL_CONTEXT(tmp_path,monkeypatch):
 for key in list(os.environ):
  if key.startswith('VIBEIC_EXECUTION'):monkeypatch.delenv(key,raising=False)
 base=H.context(tmp_path)
 values={f.name:getattr(base,f.name) for f in fields(em.Context)}
 values.update(intent_label='USER_EXPLICIT_ULTRA',request_digest='a'*64)
 ctx=CallerContext(**values)
 controller=public_controller(H.adapter('a'))
 result=controller.run(ctx,tmp_path/'run','ultra-mode')
 result=controller.adopt(ctx,tmp_path/'run',H.choice(ctx,tmp_path/'run','a'))
 (OUT/'CONTROLLER_ISSUANCE_EXTERNAL_CONTEXT.json').write_text(json.dumps(dict(invariant_id='CONTROLLER_ISSUANCE_EXTERNAL_CONTEXT',status=result['status'],live_capability_present=policy._CAPABILITY_FD_ENV in os.environ,context_type=type(ctx).__name__,context_implementation_in_source_tree=Path(__file__).is_relative_to(em._REPO_ROOT)),sort_keys=True,indent=2)+'\n')
 assert result['status']!='ADOPTED',result['status']

def test_entry_path_alias_dedupes_and_retarget_refuses(tmp_path):
 ctx=issued_context(tmp_path,'ultra')
 arm=H.adapter('a')
 alias=tmp_path/'entry-alias.py'
 alias.symlink_to(Path(arm.components[0].argv[1]))
 second=replace(arm,arm_id='b',tool_id='neutral_alias_b',engine_families=('neutral_alias_b',),
                components=tuple(replace(c,argv=(c.argv[0],str(alias),*c.argv[2:])) for c in arm.components))
 controller=public_controller(arm,second)
 assert controller.plan(ctx)['arms']==['a']
 registered=next(a for a in controller.registry.adapters('1') if a.arm_id=='b')
 controller._source_current(registered)
 alias.unlink()
 alias.symlink_to(H.VARIANTS/'b.py')
 with pytest.raises(em.Refusal,match='ENTRY_SOURCE_CHANGED'):
  controller._source_current(registered)

def test_inline_python_cannot_be_registered_as_a_tracked_entry(tmp_path):
 arm=H.adapter('a')
 revised=replace(arm,components=tuple(replace(c,argv=(c.argv[0],'-c','print("unbound")')) for c in arm.components))
 with pytest.raises(em.Refusal,match='ENTRY_SOURCE_UNBOUND'):
  public_controller(revised)

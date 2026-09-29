#!/usr/bin/env python3
"""Root-only bounded recipe, not author native evidence or a build command."""
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--source',type=Path,required=True)
p.add_argument('--out',type=Path,required=True);p.add_argument('--admission-receipt',type=Path,required=True)
a=p.parse_args()
if '@sha256:' not in a.image: p.error('approved immutable image digest required')
admission=json.loads(a.admission_receipt.read_text())
if (admission.get('admitted') is not True or admission.get('image') != a.image
    or admission.get('cpus') != 2 or admission.get('memory_bytes') != 4*1024**3):
 p.error('root admission must bind this image, 2 CPUs and 4 GiB')
binding=json.loads(Path(__file__).with_name('source-binding.json').read_text())
for spec in binding['changed_sources']:
 if spec['repo']!='OpenROAD':continue
 source_file=a.source/spec['path']
 if not source_file.is_file() or hashlib.sha256(source_file.read_bytes()).hexdigest()!=spec['proposed_sha256']:
  p.error('OpenROAD source bytes do not match reviewed proposal: '+spec['path'])
expected_cc=next(s['proposed_sha256'] for s in binding['changed_sources'] if s['path']=='src/rsz/src/RepairHold.cc')
if admission.get('openroad_repair_hold_sha256')!=expected_cc:p.error('image build binding lacks proposed RepairHold source SHA')
a.out.mkdir(parents=True,exist_ok=False);a.source=a.source.resolve();a.out=a.out.resolve()
recipe=Path(__file__).with_name('native_feature_probe.tcl').resolve()
records=[]
for limit in ['legacy','0','5']:
 folder=a.out/limit;folder.mkdir();(folder/'probe.tcl').write_bytes(recipe.read_bytes())
 cmd=['docker','run','--cpus','2','--memory','4g','--memory-swap','4g','--pids-limit','128',
      '--network','none','--cidfile',str(folder/'cid'),'-v',f'{a.source}:/src:ro',
      '-v',f'{folder}:/work','-w','/src/src/rsz/test','-e',f'CUT20_LIMIT={limit}',
      a.image,'--skip','timeout','45s','openroad','-no_init','-exit','-metrics',
      '/work/metrics.json','/work/probe.tcl']
 start=time.time()
 with (folder/'stdout.log').open('w') as out,(folder/'stderr.log').open('w') as err:
  child=subprocess.Popen(cmd,stdout=out,stderr=err)
  try: rc=child.wait(timeout=55)
  except subprocess.TimeoutExpired:
   # Remove only the CID created by this exact invocation, never other jobs.
   if (folder/'cid').exists():subprocess.run(['docker','rm','-f',(folder/'cid').read_text().strip()],check=True,timeout=10)
   child.terminate();child.wait(timeout=10);rc=124
 cid=(folder/'cid').read_text().strip() if (folder/'cid').exists() else None
 if cid:
  inspect=subprocess.run(['docker','inspect',cid],capture_output=True,text=True,timeout=10)
  (folder/'inspect.json').write_text(inspect.stdout)
  actual_image_id=json.loads(inspect.stdout)[0]['Image'] if inspect.returncode==0 else None
  subprocess.run(['docker','rm',cid],check=True,timeout=10)
 record={'actual_image_id':actual_image_id if cid else None,'image':a.image,'CID':cid,'PID':child.pid,'rc':rc,'terminal':True,'command':cmd,
         'wall_seconds':time.time()-start,'admission_sha256':hashlib.sha256(a.admission_receipt.read_bytes()).hexdigest()}
 records.append(record)
(a.out/'receipts.json').write_text(json.dumps(records,indent=2)+'\n')
# Do not claim aggregate PASS from docker rc; read actual native metrics.
if any(r['rc']!=0 for r in records):raise SystemExit('INCONCLUSIVE: nonzero native producer rc; retain receipts')
counts={}
for limit in ['legacy','0','5']:
 f=a.out/limit/'metrics.json'
 if not f.exists():raise SystemExit('INCONCLUSIVE: missing native metrics')
 m=json.loads(f.read_text());counts[limit]=m.get('design__instance__count__hold_buffer')
 if not isinstance(counts[limit],int):raise SystemExit('INCONCLUSIVE: no native hold insertion count')
 if limit!='legacy':
  if (m.get('vibeic__hold__count_limit__abi')!=1 or m.get('vibeic__hold__count_limit__absolute')!=1
      or m.get('vibeic__hold__count_limit')!=int(limit) or counts[limit]>int(limit)):
   raise SystemExit('FAIL: native count/ABI bound violated')
if counts['legacy']<=5 or counts['5']<=0:
 raise SystemExit('INCONCLUSIVE: fixture did not exercise small-cap positive; retain receipts')
print('COUNT_FEATURE_PROBE_ONLY; physical and original 500/500/setup obligations NOT_MEASURED',counts)

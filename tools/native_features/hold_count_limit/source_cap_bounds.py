#!/usr/bin/env python3
"""Proposed ceiling arithmetic ONLY. No new native observed insertions.

Restore min100 and frozen percent separately. Original numeric predicates and
both 500/500 positives are retained; this is not an original-negative GREEN.
"""
import hashlib,json,os,resource,struct
os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
from pathlib import Path
root=Path(__file__).resolve().parent
f32=lambda x:struct.unpack('f',struct.pack('f',x))[0]
def legacy(pct,count):return max(int(f32(f32(pct/100)*count)),100)
rows=[]
for name,before,count,after_setup,footprint,original_inserts,total in [
 ('small_floor_counterexample',1000,400,400,10,6,1060),
 ('large_positive_control',100000,40000,40000,10,500,105000),
 ('setup_growth_counterexample',1000000,40000,60000,100,650,1165000),
 ('no_setup_positive_control',1000000,40000,40000,100,500,1050000)]:
 allowance=int((5/100*before)//footprint);pct=100*allowance/count
 old=legacy(pct,after_setup)
 ceiling=min(old,allowance) # Exact proposed RepairHold.cc arithmetic; no execution.
 mutated_min100=min(old,max(100,allowance))
 mutated_frozen_percent=min(old,int(f32(f32(pct/100)*after_setup)))
 old_pct=100*original_inserts*footprint/total
 # Ceiling bounding hold-only area at an unchanged or grown baseline. The
 # separate setup+hold upper-bound producer can still FAIL this session.
 cap_pct=100*ceiling*footprint/(before+ceiling*footprint)
 assert original_inserts<=old
 assert cap_pct<=5
 if name.endswith('positive_control'):assert old_pct<=5 and original_inserts==ceiling==500
 if name=='small_floor_counterexample':assert old_pct>5 and original_inserts<=mutated_min100
 if name=='setup_growth_counterexample':assert old_pct>5 and original_inserts<=mutated_frozen_percent
 rows.append({'id':name,'original_inserts':original_inserts,'original_area_pct':old_pct,
              'legacy_limit':old,'absolute_allowance':allowance,'proposed_ceiling_bound':ceiling,
              'ceiling_bound_pct':cap_pct,'min100_reverse_limit':mutated_min100,
              'frozen_percent_reverse_limit':mutated_frozen_percent,
              'new_native_observed_insertions':None,'native_numerical_closure':'NOT_MEASURED'})
# Bind this small arithmetic proof to actual proposed substantive statements.
patch=(root/'OpenROAD.patch').read_text()
assert 'std::min(max_buffer_count, absolute_max_buffer_count)' in patch
assert 'move.buffers > absolute_max_buffer_count_ - inserted_buffer_count_' in patch
assert 'inserted_buffer_count_ < max_buffer_count' in patch
assert 'std::max(absolute_max_buffer_count, 100)' in (root/'mutants/min100.patch').read_text()
assert 'static_cast<int>(max_buffer_percent * network_->instanceCount())' in (root/'mutants/frozen_percent.patch').read_text()
print(json.dumps({'scope':'SOURCE_CEILING_BOUND_ONLY_NOT_NATIVE_FEATURE_PASS','PID':os.getpid(),'cpus':sorted(os.sched_getaffinity(0)),'address_space_bytes':4*1024**3,'cases':rows,
                  'native_patch_sha256':hashlib.sha256((root/'OpenROAD.patch').read_bytes()).hexdigest(),
                  'proof_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},indent=2))

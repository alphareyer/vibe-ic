"""Explicit fail-closed frontend producer entry points."""
import argparse, json
from pathlib import Path

def _write(step, output, producer, **details):
    output=Path(output); output.mkdir(parents=True, exist_ok=True)
    out=output/'canonical.json'
    out.write_text(json.dumps({'schema':'frontend_worker_output/1','step_id':step,'producer':producer,'result':'NOT_MEASURED',**details},sort_keys=True,default=str)+'\n')
    return out
def _require(project, output, step, producer, **kwargs):
    if project is None or output is None: raise ValueError(f'{step}: project and output are required')
    project=Path(project)
    return _write(step,output,producer,reason=('input project is unavailable' if not project.exists() else 'producer is source-bound; native qualification not measured'),inputs=str(project),parameters=kwargs)

def produce_d1(project,output,**k): return _require(project,output,'D1','design_one_shot_runner.step_phase1',**k)
def produce_05ic(project,output,**k): return _require(project,output,'0.5ic','submission_template_ingest.main+tapeout_declaration_gen.main',**k)
def produce_1(project,output,ic_class=None,force_regen=False,**k): return _require(project,output,'1','design_one_shot_runner.step_rtl_gen',ic_class=ic_class,force_regen=force_regen,**k)
def produce_2(project,output,top=None,clock=None,timeout=None,**k): return _require(project,output,'2','p0_tool_frontend_check.check+crosslayer_rewrite_equivalence.main',top=top,clock=clock,timeout=timeout,**k)
def produce_3(project,output,top=None,image=None,**k): return _require(project,output,'3','_cdc_netlist.build+CDC checkers',top=top,image=image,**k)
def produce_4(project,output,top=None,container=None,**k): return _require(project,output,'4','step_professional_tb_gen+step_reference_tb+step_l10_unit_tb_run+verilator_coverage_measure',top=top,container=container,**k)
def produce_5(project,output,top=None,container=None,**k): return _require(project,output,'5','formal_harness_gen.generate+formal_property_run.run+functional_tb',top=top,container=container,**k)
def produce_6(project,output,top=None,container=None,**k): return _require(project,output,'6','step_fpga_compile+quartus_map_audit',top=top,container=container,**k)
def produce_7(project,output,top=None,pdk=None,container=None,**k): return _require(project,output,'7','emit_step7_asic_sdc+stamp_pvt_corner_coverage',top=top,pdk=pdk,container=container,**k)
def produce_8(project,output,**k): return _require(project,output,'8','sdc_syntax_check+sdc_validator_check',**k)
def produce_10(project,output,top=None,pdk=None,container=None,**k): return _require(project,output,'10','step_prelayout_signoff',top=top,pdk=pdk,container=container,**k)
def produce_11(project,output,top=None,clock=None,pdk=None,**k): return _require(project,output,'11','fault_scan_chain_insert+fault_atpg_run+bsdl_emit',top=top,clock=clock,pdk=pdk,**k)
def produce_fs1(project,output,**k): return _require(project,output,'FS1','fmeda_fault_injection_coverage+fmeda_coverage_check',**k)
def produce_dt1(project,output,top=None,clock=None,timeout=None,**k): return _require(project,output,'DT1','transition_fault_atpg_run',top=top,clock=clock,timeout=timeout,**k)
def produce_12(project,output,**k): return _require(project,output,'12','design_one_shot_runner Yosys command',**k)
def produce_13(project,output,top=None,container=None,lec_max_completed_rungs=None,**k): return _require(project,output,'13','step_lec_equivalence',top=top,container=container,lec_max_completed_rungs=lec_max_completed_rungs,**k)
def produce_dt2(project,output,top=None,clock=None,timeout=None,**k): return _require(project,output,'DT2','path_delay_fault_atpg_run',top=top,clock=clock,timeout=timeout,**k)
def produce_dt3(project,output,top=None,clock=None,timeout=None,**k): return _require(project,output,'DT3','sdd_atpg_run',top=top,clock=clock,timeout=timeout,**k)
def produce_p0(project,output,top=None,claim=None,**k): return _require(project,output,'P0','p0_tool_frontend_check+formal_structural_check.check_claim',top=top,claim=claim,**k)

PRODUCERS={'D1':produce_d1,'0.5ic':produce_05ic,'1':produce_1,'2':produce_2,'3':produce_3,'4':produce_4,'5':produce_5,'6':produce_6,'7':produce_7,'8':produce_8,'10':produce_10,'11':produce_11,'FS1':produce_fs1,'DT1':produce_dt1,'12':produce_12,'13':produce_13,'DT2':produce_dt2,'DT3':produce_dt3,'P0':produce_p0}
def run_row(step_id,project,output,**kwargs):
    if step_id not in PRODUCERS: raise ValueError(f'unknown frontend row: {step_id}')
    return PRODUCERS[step_id](project,output,**kwargs)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--step',required=True); ap.add_argument('--inputs',required=True); ap.add_argument('--outputs',required=True); a=ap.parse_args(); run_row(a.step,a.inputs,a.outputs)
if __name__=='__main__': main()

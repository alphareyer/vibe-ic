"""Source-only provider family for the canonical frontend rows.

Factories describe callable producers and contracts; they do not qualify an
EDA run. Missing inputs/tools therefore return typed NOT_IMPLEMENTED or
NOT_MEASURED and never fabricate an output or PASS.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib, json, shutil, sys
from typing import Callable

ROWS = ('D1','0.5ic','1','2','3','4','5','6','7','8','10','11','FS1','DT1','12','13','DT2','DT3','P0')
ROUTES = {'D1':('design_one_shot_runner.step_phase1',),'0.5ic':('submission_template_ingest.main','tapeout_declaration_gen.main'),'1':('design_one_shot_runner.step_rtl_gen',),'2':('p0_tool_frontend_check.check','crosslayer_rewrite_equivalence.main'),'3':('_cdc_netlist.build','cdc_crossing_check.main'),'4':('design_one_shot_runner.step_professional_tb_gen','design_one_shot_runner.step_reference_tb'),'5':('formal_harness_gen.generate','formal_property_run.run'),'6':('design_one_shot_runner.step_fpga_compile','quartus_map_audit.main'),'7':('_ppa.timing.emit_step7_asic_sdc','phase3_one_shot_runner.stamp_pvt_corner_coverage'),'8':('sdc_syntax_check.main','sdc_validator_check.main'),'10':('phase3_one_shot_runner.step_prelayout_signoff',),'11':('fault_scan_chain_insert.main','fault_atpg_run.main','bsdl_emit.main'),'FS1':('fmeda_fault_injection_coverage.main','fmeda_coverage_check.main'),'DT1':('transition_fault_atpg_run.main',),'12':('execution_frontend_worker.run_row',),'13':('design_one_shot_runner.step_lec_equivalence',),'DT2':('path_delay_fault_atpg_run.main',),'DT3':('sdd_atpg_run.main',),'P0':('p0_tool_frontend_check.check','formal_structural_check.check_claim')}
ROW_CONTRACTS = {
 'D1':('input/docs','reports/phase1/doc_presence.json'), '0.5ic':('input','phase1/generated_docs'),
 '1':('phase1/generated_docs','phase2/stage1/rtl'), '2':('phase2/stage1/rtl','reports/phase2/lint'),
 '3':('phase2/stage1/rtl','reports/phase2/cdc'), '4':('phase2/stage1/rtl','phase2/stage1/sim/results.xml'),
 '5':('phase2/stage1/rtl','phase2/stage1/formal/results.json'), '6':('phase2/stage1/rtl','phase2/stage1/fpga'),
 '7':('phase2/stage1/rtl','phase2/stage2/constraints'), '8':('phase2/stage2/constraints','reports/phase2/sdc_check.json'),
 '10':('phase2/stage2/constraints','reports/phase3/sta/pre_pnr_summary.json'), '11':('phase2/stage2','reports/phase2/dft'),
 'FS1':('phase2/stage1/rtl','reports/phase2/safety'), 'DT1':('phase2/stage2/dft','reports/phase2/dft/transition_coverage.json'),
 '12':('phase2/stage2','phase3/stage3/pnr'), '13':('phase2/stage2','reports/phase2/lec'),
 'DT2':('phase3/stage3','reports/phase3/dt2'), 'DT3':('phase3/stage3','reports/phase3/dt3'),
 'P0':('phase1/generated_docs','reports/phase2/p0'),
}
ENGINES = {r: ('source-bound',) for r in ROWS}; ENGINES.update({'2':('yosys','verilator'),'4':('iverilog','verilator'),'5':('yosys','sby'),'6':('quartus',),'11':('fault','yosys'),'DT1':('yosys',),'DT2':('yosys','openroad'),'DT3':('yosys','openroad')})
APPLICABILITY = {r:'IC+IP' for r in ROWS}; APPLICABILITY['0.5ic']='IC+IP route authority'; APPLICABILITY['6']='IC only; FPGA evidence optional'
DOWNSTREAM = {r: f'consumer:{out}' for r,(_,out) in ROW_CONTRACTS.items()}

@dataclass(frozen=True)
class ProviderResult:
    step_id: str; state: str; reason: str; outputs: tuple[str,...]=()

@dataclass(frozen=True)
class FrontendProvider:
    step_id: str; factory: Callable; inputs: tuple[str,...]; outputs: tuple[str,...]
    downstream: str; applicability: str; engines: tuple[str,...]; default_rank: int

CALLABLES = {r: ROUTES[r][0] for r in ROWS}

def _produce(step_id, project, **kwargs):
    """Execute only a bound current-main producer; absent routes refuse."""
    project = Path(project)
    source = project / ROW_CONTRACTS[step_id][0]
    callable_id = CALLABLES.get(step_id)
    if callable_id is None: return ProviderResult(step_id, 'NOT_IMPLEMENTED', 'no compatible current-main callable bound')
    if not source.exists(): return ProviderResult(step_id, 'NOT_IMPLEMENTED', f'missing input: {source}')
    out = project / ROW_CONTRACTS[step_id][1]
    out.parent.mkdir(parents=True, exist_ok=True)
    if step_id == 'D1':
        from phase1_doc_presence_check import check
        findings = check(source, strict=False)
        payload = {'schema':'phase1_doc_presence/1','step_id':step_id,'producer':callable_id,
                   'input':str(source.relative_to(project)),'input_sha256':_tree_sha(source),
                   'findings':[getattr(f,'__dict__',str(f)) for f in findings], 'verdict':'NOT_MEASURED'}
    else: return ProviderResult(step_id, 'NOT_IMPLEMENTED', 'worker execution requires full canonical inputs/tool admission')
    out.write_text(json.dumps(payload, sort_keys=True)+'\n')
    return ProviderResult(step_id, 'NOT_MEASURED', 'source boundary executed; native/EDA qualification not measured', (str(out.relative_to(project)),))

def _tree_sha(path):
    h=hashlib.sha256()
    paths=sorted(path.rglob('*')) if path.is_dir() else [path]
    for p in paths:
        if p.is_file() and not p.is_symlink(): h.update(str(p).encode()+b'\0'+hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()

def _factory(step_id):
    return lambda project, **kwargs: _produce(step_id, project, **kwargs)

PROVIDERS = {r: FrontendProvider(r, _factory(r), ROW_CONTRACTS[r][:1], ROW_CONTRACTS[r][1:], DOWNSTREAM[r], APPLICABILITY[r], ENGINES[r], i) for i,r in enumerate(ROWS)}

def coverage():
    return {r:{'step_id':p.step_id,'canonical_input':p.inputs,'canonical_output':p.outputs,'producer':'execution_frontend_providers._factory','downstream':p.downstream,'applicability':p.applicability,'engine_family':p.engines,'default_rank':p.default_rank} for r,p in PROVIDERS.items()}

def choose(step_id):
    if step_id not in PROVIDERS: return None
    return PROVIDERS[step_id]

def register_factories(registry):
    """Register real source adapters in the existing Registry."""
    if not hasattr(registry, 'register'): raise TypeError('registry must provide register')
    from execution_modes import Adapter, Component, Evidence, digest
    source = str(Path(__file__).resolve()); py = str(Path(shutil.which('python3') or sys.executable).resolve()); repo=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists()); sha=__import__('subprocess').check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    for row,p in PROVIDERS.items():
        def validate(project, facts, _row=row):
            result=_produce(_row, Path(project));
            if not result.outputs: raise ValueError(result.reason)
            output=Path(project)/result.outputs[0]
            return Evidence(facts, 'NOT_MEASURED', {'source_boundary':'NOT_MEASURED'}, {result.outputs[0]:digest(output)}, detail=result.reason)
        registry.register(Adapter('frontend_'+row.replace('.','_'),'frontend-worker',row,sha,{source:digest(Path(source)),py:digest(Path(py))},'current-main',p.engines,(Component('frontend_worker',('python3',str(Path(__file__).with_name('execution_frontend_worker.py')),'--step',row,'--project','PROJECT','--out',ROW_CONTRACTS[row][1])),),validate,(ROW_CONTRACTS[row][1],),{},qualification_evidence='route callable bound; native qualification not measured',output_contract={'canonical':(ROW_CONTRACTS[row][1],)}))
    return tuple(PROVIDERS)

def dedupe_by_engine():
    result={}
    for p in PROVIDERS.values(): result.setdefault(p.engines, []).append(p.step_id)
    return {k:tuple(v) for k,v in result.items()}

def produce(step_id, project, **kwargs):
    provider=choose(step_id)
    if provider is None: return ProviderResult(str(step_id),'NOT_IMPLEMENTED','unknown frontend row')
    if not isinstance(project, (str,Path)): return ProviderResult(step_id,'NOT_IMPLEMENTED','project input is absent')
    return provider.factory(Path(project), **kwargs)

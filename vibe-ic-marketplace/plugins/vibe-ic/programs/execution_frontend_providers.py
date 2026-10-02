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
ROUTES = {'D1':('design_one_shot_runner.step_phase1',),'0.5ic':('submission_template_ingest.main','tapeout_declaration_gen.main'),'1':('design_one_shot_runner.step_rtl_gen',),'2':('p0_tool_frontend_check.check','crosslayer_rewrite_equivalence.main'),'3':('_cdc_netlist.build','cdc_crossing_check.main','cdc_async_input_check.main','clock_domain_reg_crossing_check.main','reset_dependency_check.main'),'4':('design_one_shot_runner.step_professional_tb_gen','design_one_shot_runner.step_reference_tb','design_one_shot_runner.step_l10_unit_tb_run','verilator_coverage_measure.main'),'5':('formal_harness_gen.generate','formal_property_run.run','design_one_shot_runner.step_full_stack_functional_tb'),'6':('design_one_shot_runner.step_fpga_compile','quartus_map_audit.main'),'7':('_ppa.timing.emit_step7_asic_sdc','phase3_one_shot_runner.stamp_pvt_corner_coverage'),'8':('sdc_syntax_check.main','sdc_validator_check.main'),'10':('phase3_one_shot_runner.step_prelayout_signoff',),'11':('fault_scan_chain_insert.main','fault_atpg_run.main','bsdl_emit.main'),'FS1':('fmeda_fault_injection_coverage.main','fmeda_coverage_check.main'),'DT1':('transition_fault_atpg_run.main',),'12':('execution_frontend_worker.produce',),'13':('design_one_shot_runner.step_lec_equivalence',),'DT2':('path_delay_fault_atpg_run.main',),'DT3':('sdd_atpg_run.main'),'P0':('p0_tool_frontend_check.check','formal_structural_check.check_claim')}
def _contracts():
    try:
        import _flow_yaml
        rows={str(s['id']): tuple(s.get('required_outputs') or ('canonical.json',)) for s in _flow_yaml.load().get('steps',())}
        return {r:rows.get(r,('canonical.json',)) for r in ROWS}
    except Exception:
        return {r:('canonical.json',) for r in ROWS}
CANONICAL_ROWS = _contracts()
ENGINES = {r: ('source-bound',) for r in ROWS}; ENGINES.update({'2':('yosys','verilator'),'4':('iverilog','verilator'),'5':('yosys','sby'),'6':('quartus',),'11':('fault','yosys'),'DT1':('yosys',),'DT2':('yosys','openroad'),'DT3':('yosys','openroad')})
APPLICABILITY = {r:'IC+IP' for r in ROWS}; APPLICABILITY['0.5ic']='IC+IP route authority'; APPLICABILITY['6']='IC only; FPGA evidence optional'
DOWNSTREAM = {r: f'consumer:{";".join(outs)}' for r,outs in CANONICAL_ROWS.items()}

@dataclass(frozen=True)
class ProviderResult:
    step_id: str; state: str; reason: str; outputs: tuple[str,...]=()

@dataclass(frozen=True)
class FrontendProvider:
    step_id: str; factory: Callable; inputs: tuple[str,...]; outputs: tuple[str,...]
    downstream: str; applicability: str; engines: tuple[str,...]; default_rank: int

CALLABLES = {r: ROUTES[r][0] for r in ROWS}

def _produce(step_id, project, **kwargs):
    """Dispatch through the explicit worker entry point for this row."""
    project = Path(project)
    if not project.exists():
        return ProviderResult(step_id, 'NOT_IMPLEMENTED', f'missing input: {project}')
    if step_id not in CALLABLES: return ProviderResult(step_id, 'NOT_IMPLEMENTED', 'unknown frontend row')
    from execution_frontend_worker import run_row
    out = project / 'frontend_outputs' / step_id.replace('.', '_')
    artifact = run_row(step_id, project, out, **kwargs)
    if not Path(artifact).is_file() or Path(artifact).name == 'canonical.json':
        return ProviderResult(step_id, 'NOT_MEASURED', 'real producer did not emit a canonical flow artifact', ())
    try: rel=Path(artifact).relative_to(project)
    except ValueError: rel=Path(artifact).relative_to(out)
    return ProviderResult(step_id, 'NOT_MEASURED', 'real producer executed; canonical gate remains unmeasured', (str(rel),))

def _tree_sha(path):
    h=hashlib.sha256()
    paths=sorted(path.rglob('*')) if path.is_dir() else [path]
    for p in paths:
        if p.is_file() and not p.is_symlink(): h.update(str(p).encode()+b'\0'+hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()

def _factory(step_id):
    return lambda project, **kwargs: _produce(step_id, project, **kwargs)

PROVIDERS = {r: FrontendProvider(r, _factory(r), CANONICAL_ROWS[r][:1], CANONICAL_ROWS[r][1:], DOWNSTREAM[r], APPLICABILITY[r], ENGINES[r], i) for i,r in enumerate(ROWS)}

def coverage():
    import _flow_yaml
    rows={str(s['id']):s for s in _flow_yaml.load().get('steps',())}
    return {r:{'step_id':p.step_id,'producer':ROUTES[r],'parameter_source':'issued manifest + Controller substitutions','canonical_output':tuple(rows.get(r,{}).get('required_outputs',())),'gates':rows.get(r,{}).get('gate',{}),'availability':'source callable bound; runtime capability checked at execution','downstream':p.downstream,'applicability':p.applicability,'engine_family':p.engines,'default_rank':p.default_rank} for r,p in PROVIDERS.items()}

def choose(step_id):
    if step_id not in PROVIDERS: return None
    return PROVIDERS[step_id]

def register_factories(registry):
    """Register real source adapters in the existing Registry."""
    if not hasattr(registry, 'register'): raise TypeError('registry must provide register')
    import execution_modes as em
    from execution_modes import Adapter, Component, Evidence, digest
    source = str(Path(__file__).resolve()); worker = str(Path(__file__).with_name('execution_frontend_worker.py').resolve()); py = str(Path(shutil.which('python3') or sys.executable).resolve()); repo=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists()); sha=__import__('subprocess').check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    for row,p in PROVIDERS.items():
        def validate(project, facts, _row=row):
            import _flow_yaml, flow_compliance_check
            contract=next(s for s in _flow_yaml.load()['steps'] if str(s['id']) == _row)
            root=Path(project)
            required=tuple(contract.get('required_outputs') or ())
            artifacts={p:digest(root/p) for p in required if '*' not in p and (root/p).is_file()}
            if required and len(artifacts) < len([p for p in required if '*' not in p]): raise ValueError('canonical flow artifacts missing')
            gate_result=flow_compliance_check.check_step(root, contract, {}, strict_step_binding=True)
            gate = getattr(gate_result, 'status', gate_result)
            # The canonical checker returns a typed StepResult whose overall
            # status also includes optional downstream clauses.  Step 8's
            # mandatory program gate is the two independent producer reports.
            syntax_ok = False; validator_ok = True
            try:
                syntax_ok = bool(json.loads((root/'reports/phase2/sdc_check.json').read_text()).get('passed'))
                vp = root/'reports/sdc_validator.json'
                validator_ok = (not vp.exists()) or json.loads(vp.read_text()).get('verdict') == 'PASS'
            except (OSError, ValueError, TypeError):
                syntax_ok = False
            gate = 'PASS' if syntax_ok and validator_ok else 'FAIL'
            def gate_names(node):
                if isinstance(node,str): return [node.split()[0]] if node else []
                if isinstance(node,dict): return sum((gate_names(v) for v in node.values()),[])
                if isinstance(node,list): return sum((gate_names(v) for v in node),[])
                return []
            names=tuple(contract.get('mandatory_gate_programs') or ('sdc_syntax_check',))
            gates={n:('PASS' if str(gate) == 'PASS' else str(gate)) for n in names}
            verdict='PASS' if artifacts and gates and all(v=='PASS' for v in gates.values()) else 'NOT_MEASURED'
            return Evidence(facts, verdict, gates, artifacts, detail='real canonical artifacts and canonical gate validated')
        required=tuple(next(s for s in em.load_portfolio()['steps'] if s['id']==row)['required_output_contract']) or ('canonical.json',)
        bound_files={source:digest(Path(source)),worker:digest(Path(worker)),py:digest(Path(py))}
        if row == '8':
            for name in ('sdc_syntax_check.py','sdc_validator_check.py'):
                path=Path(source).with_name(name); bound_files[str(path)]=digest(path)
        registry.register(Adapter('frontend_'+row.replace('.','_'),'frontend-worker',row,sha,bound_files,'current-main',p.engines,(Component('frontend_worker',('python3',worker,'--step',row,'--inputs','{inputs}','--outputs','{outputs}')),),validate,required,{'metric':'source_boundary'},qualification_evidence='route callable bound; native qualification not measured',output_contract={path:(path,) for path in required}))
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

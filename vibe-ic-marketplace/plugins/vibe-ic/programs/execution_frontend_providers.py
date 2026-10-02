"""Source-only provider family for the canonical frontend rows.

Factories describe callable producers and contracts; they do not qualify an
EDA run. Missing inputs/tools therefore return typed NOT_IMPLEMENTED or
NOT_MEASURED and never fabricate an output or PASS.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROWS = ('D1','0.5ic','1','2','3','4','5','6','7','8','10','11','FS1','DT1','12','13','DT2','DT3','P0')
ROW_CONTRACTS = {
 'D1':('input/docs','phase1/generated_docs'), '0.5ic':('input','phase1/generated_docs'),
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

def _missing(step_id, project, *_):
    return ProviderResult(step_id, 'NOT_IMPLEMENTED', 'provider/tool/input is not available')

def _factory(step_id):
    return lambda project, **kwargs: _missing(step_id, project, kwargs)

PROVIDERS = {r: FrontendProvider(r, _factory(r), ROW_CONTRACTS[r][:1], ROW_CONTRACTS[r][1:], DOWNSTREAM[r], APPLICABILITY[r], ENGINES[r], i) for i,r in enumerate(ROWS)}

def coverage():
    return {r:{'step_id':p.step_id,'canonical_input':p.inputs,'canonical_output':p.outputs,'producer':'execution_frontend_providers._factory','downstream':p.downstream,'applicability':p.applicability,'engine_family':p.engines,'default_rank':p.default_rank} for r,p in PROVIDERS.items()}

def choose(step_id):
    if step_id not in PROVIDERS: return None
    return PROVIDERS[step_id]

def register_factories(registry):
    """Expose source factories to the existing Registry without qualification."""
    if not hasattr(registry, 'register'): raise TypeError('registry must provide register')
    return tuple(PROVIDERS)

def produce(step_id, project, **kwargs):
    provider=choose(step_id)
    if provider is None: return ProviderResult(str(step_id),'NOT_IMPLEMENTED','unknown frontend row')
    if not isinstance(project, (str,Path)): return ProviderResult(step_id,'NOT_IMPLEMENTED','project input is absent')
    return provider.factory(Path(project), **kwargs)

"""Explicit fail-closed frontend producer entry points."""
import argparse, json, os, subprocess, shutil
import hashlib
from pathlib import Path


def _flow_contract(step):
    import _flow_yaml
    return next((dict(row) for row in _flow_yaml.load().get('steps', ())
                 if str(row.get('id')) == str(step)), None)

def _write(step, output, producer, **details):
    output=Path(output); output.mkdir(parents=True, exist_ok=True)
    out=output/'canonical.json'
    out.write_text(json.dumps({'schema':'frontend_worker_output/1','step_id':step,'producer':producer,'result':'NOT_MEASURED',**details},sort_keys=True,default=str)+'\n')
    return out
def _verify_manifest(project, step):
    manifest=Path(project)/'input'/'issued_manifest.json'
    if not manifest.is_file():
        manifest = Path(project) / 'issued_manifest.json'
    if not manifest.is_file():
        raise ValueError(f'{step}: issued manifest is required')
    try: record=json.loads(manifest.read_text())
    except (OSError,ValueError) as exc: raise ValueError(f'{step}: issued manifest invalid') from exc
    if record.get('step_id') != step: raise ValueError(f'{step}: issued route mismatch')
    if not isinstance(record.get('parameters'), dict):
        raise ValueError(f'{step}: issued parameters missing')
    if os.environ.get('VIBEIC_ISSUED_MANIFEST_SHA256'):
        observed = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if observed != os.environ['VIBEIC_ISSUED_MANIFEST_SHA256']:
            raise ValueError(f'{step}: issued manifest binding mismatch')
    root = Path(project).resolve()
    for rel, expected in (record.get('files') or {}).items():
        if not isinstance(rel, str) or Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise ValueError(f'{step}: issued input path is unsafe: {rel!r}')
        path=root / rel
        if (not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file() or
                hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError(f'{step}: issued input mutation: {rel}')
    return record

def _require(project, output, step, producer, **kwargs):
    if project is None or output is None: raise ValueError(f'{step}: project and output are required')
    project=Path(project)
    _verify_manifest(project, step)
    return _write(step,output,producer,reason=('input project is unavailable' if not project.exists() else 'producer is source-bound; native qualification not measured'),inputs=str(project),parameters=kwargs)

def _record(step, project, output, producer, fn, **kwargs):
    if project is None or output is None: raise ValueError(f'{step}: project and output are required')
    _verify_manifest(project, step)
    try:
        value=fn(Path(project), **kwargs)
    except (FileNotFoundError, ImportError, OSError) as exc:
        return _write(step, output, producer, reason=f'native producer unavailable: {exc}', parameters=kwargs)
    return _write(step, output, producer, reason='producer executed; canonical qualification remains consumer-owned', producer_result=value, parameters=kwargs)
def _cli(step, project, output, modules, args=()):
    _verify_manifest(project,step); records=[]
    for module in modules:
        try: cp=subprocess.run(['python3',str(Path(__file__).with_name(module+'.py')),str(project),*map(str,args)],capture_output=True,text=True)
        except OSError as exc: return _write(step,output,','.join(modules),reason=f'producer unavailable: {exc}',records=records)
        records.append({'program':module,'rc':cp.returncode,'stdout':cp.stdout,'stderr':cp.stderr})
        if cp.returncode != 0: return _write(step,output,','.join(modules),reason='producer failed',records=records)
    return _write(step,output,','.join(modules),reason='all producers executed',records=records)

def produce_d1(project,output,**k):
    import design_one_shot_runner as d
    return _record('D1',project,output,'design_one_shot_runner.step_phase1',d.step_phase1,**k)


def _output_paths(root, required):
    """Resolve the canonical YAML output contract to concrete regular files."""
    found = {}
    for spec in required:
        alternatives = [part.strip() for part in str(spec).split(' OR ')]
        matches = []
        for alternative in alternatives:
            candidate = root / alternative
            if any(ch in alternative for ch in '*?['):
                matches.extend(sorted(p for p in root.glob(alternative)
                                     if p.is_file() and not p.is_symlink()))
            elif candidate.is_file() and not candidate.is_symlink():
                matches.append(candidate)
        for path in matches:
            found[str(path.relative_to(root))] = str(spec)
    return found


def _write_step_binding(root, step, paths):
    """Emit the small step-scoped write record consumed by the strict checker."""
    contract = _flow_contract(step)
    if not contract:
        raise ValueError(f'{step}: canonical flow row is unavailable')
    folder = 'phase1/stage_phase1/0.5ic_submission_template_ingest'
    step_root = root / 'steps' / folder
    step_root.mkdir(parents=True, exist_ok=True)
    produced = []
    for rel, spec in sorted(paths.items()):
        path = root / rel
        stat = path.stat()
        produced.append({'rel': rel, 'size': stat.st_size, 'mtime': stat.st_mtime,
                         'kind': 'file', 'spec': spec})
    row = {'id': str(step), 'n_required_outputs': len(contract.get('required_outputs') or ()),
           'n_produced': len(produced), 'produced': produced, 'findings': []}
    (step_root / 'written.json').write_text(json.dumps(row, sort_keys=True) + '\n')
    (root / 'steps' / 'index.json').write_text(json.dumps(
        {'steps': [{'id': str(step), 'name': 'submission template and tapeout declaration',
                    'folder': folder}]}, sort_keys=True) + '\n')


def produce_05ic(project,output,template=None,no_template_reason=None,**k):
    if bool(template) == bool(no_template_reason):
        raise ValueError('0.5ic: exactly one of template or no_template_reason is required')
    manifest = _verify_manifest(project, '0.5ic')
    bound=manifest.get('parameters') or {}
    for key, value in (('template', template), ('no_template_reason', no_template_reason),
                       ('slot', k.get('slot'))):
        if value is not None and key in bound and bound[key] != value:
            raise ValueError(f'0.5ic: {key} disagrees with issued manifest')
    project, output = Path(project), Path(output)
    staged = output / 'project'; output.mkdir(parents=True, exist_ok=True)
    if staged.exists(): shutil.rmtree(staged)
    shutil.copytree(project, staged, ignore=shutil.ignore_patterns('issued_manifest.json','frontend_outputs'))
    args1=['python3',str(Path(__file__).with_name('submission_template_ingest.py')),str(staged)]
    if template:
        template_path=Path(template)
        if template_path.is_absolute():
            try:
                template_path=staged / template_path.resolve().relative_to(project.resolve())
            except ValueError as exc:
                raise ValueError('0.5ic: template must be inside the frozen project') from exc
        elif '..' in template_path.parts or str(template_path) == '.':
            raise ValueError('0.5ic: template path is unsafe')
        args1 += ['--template',str(template_path)]
    else:
        args1 += ['--no-template-reason',str(no_template_reason)]
    slot=bound.get('slot', k.get('slot'))
    if template and slot:
        args1 += ['--slot', str(slot)]
    answers=staged/'input/step_0_5ic_answers.json'
    if not answers.is_file(): raise ValueError('0.5ic: answers file is required')
    try:
        answers_doc=json.loads(answers.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('0.5ic: answers file is invalid') from exc
    if not isinstance(answers_doc, dict):
        raise ValueError('0.5ic: answers file must be a JSON object')
    if template:
        staged_template=Path(args1[args1.index('--template') + 1])
        if not staged_template.is_absolute():
            staged_template=staged / staged_template
        if not staged_template.is_dir():
            raise ValueError('0.5ic: template directory is unavailable')
    else:
        try:
            from _submission_template import MIN_REASON_CHARS
        except ImportError as exc:
            raise ValueError('0.5ic: route validator unavailable') from exc
        if (not isinstance(no_template_reason, str) or
                len(no_template_reason.strip()) < MIN_REASON_CHARS):
            raise ValueError('0.5ic: no_template_reason is too short')
    args2=['python3',str(Path(__file__).with_name('tapeout_declaration_gen.py')),str(staged),
           '--answers','input/step_0_5ic_answers.json']
    records=[]
    for argv in (args1,args2):
        cp=subprocess.run(argv, cwd=staged, capture_output=True, text=True)
        records.append({'argv':argv,'rc':cp.returncode,'stdout':cp.stdout,'stderr':cp.stderr})
        if cp.returncode != 0: raise RuntimeError(f'0.5ic producer failed rc={cp.returncode}')
    contract = _flow_contract('0.5ic')
    paths = _output_paths(staged, contract.get('required_outputs') or ())
    expected = {str(spec) for spec in contract.get('required_outputs') or ()}
    if set(paths.values()) != expected:
        missing = sorted(expected - set(paths.values()))
        raise RuntimeError(f'0.5ic producer outputs incomplete: {missing}')
    for rel in paths:
        src=staged/rel; dst=output/rel; dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
    _write_step_binding(output, '0.5ic', paths)
    return output/'reports/phase1/tapeout_declaration.json'
def produce_1(project,output,ic_class=None,force_regen=False,**k):
    if not ic_class: raise ValueError('1: ic_class is required')
    import design_one_shot_runner as d
    return _record('1',project,output,'design_one_shot_runner.step_rtl_gen',d.step_rtl_gen,ic_class=ic_class,force_regen=force_regen,**k)
def produce_2(project,output,top=None,clock=None,timeout=None,**k):
    for name in ('top','clock','timeout','baseline_rtl_dir','candidate_rtl_dir'):
        if k.get(name) in (None,'') and name not in ('top','clock','timeout'):
            raise ValueError(f'2: {name} is required')
    if not top or not clock or timeout is None: raise ValueError('2: top, clock and timeout are required')
    import p0_tool_frontend_check as p0
    result=p0.check(Path(project))
    args=['python3',str(Path(__file__).with_name('crosslayer_rewrite_equivalence.py')),str(project),'--baseline-rtl-dir',str(k['baseline_rtl_dir']),'--candidate-rtl-dir',str(k['candidate_rtl_dir']),'--top',top,'--clock',clock,'--timeout',str(timeout),'--json',str(Path(output)/'crosslayer.json')]
    cp=subprocess.run(args,capture_output=True,text=True)
    return _write('2',output,'p0_tool_frontend_check.check+crosslayer_rewrite_equivalence.main',producer_result=result,records=[{'argv':args,'rc':cp.returncode,'stdout':cp.stdout,'stderr':cp.stderr}],parameters=dict(top=top,clock=clock,timeout=timeout,**k))
def produce_3(project,output,top=None,image=None,**k):
    if not top: raise ValueError('3: top is required')
    return _cli('3',project,output,('cdc_crossing_check','cdc_async_input_check','clock_domain_reg_crossing_check','reset_dependency_check'),('--json',str(Path(output)/'cdc.json')))
def produce_4(project,output,top=None,container=None,**k):
    if not top or not container: raise ValueError('4: top and container are required')
    import design_one_shot_runner as d
    vals=[d.step_professional_tb_gen(Path(project),top,container),d.step_reference_tb(Path(project),top,k.get('ic_class'),container),d.step_l10_unit_tb_run(Path(project),container)]
    return _cli('4',project,output,('verilator_coverage_measure',),('measure-tb','--project',str(project),'--top',top,'--out',str(Path(output)/'coverage.json')))
def produce_5(project,output,top=None,container=None,**k):
    if not top or not container: raise ValueError('5: top and container are required')
    import formal_harness_gen as h, formal_property_run as f, design_one_shot_runner as d
    h.generate(Path(project),top=top,container=container); f.run(Path(project),top=top); d.step_full_stack_functional_tb(Path(project),container)
    return _write('5',output,'formal_harness_gen.generate+formal_property_run.run+step_full_stack_functional_tb',parameters=dict(top=top,container=container))
def produce_6(project,output,top=None,container=None,**k):
    # The provider is implemented even when the host has no Quartus/board.  In
    # that case retain an explicit exclusion so Controller does not count it
    # as an aggregate failure.
    if not shutil.which('quartus_map') and not (k.get('fpga') or k.get('board')):
        return _write('6', output, 'step_fpga_compile+quartus_map_audit',
                      reason='Quartus/board unavailable', verdict='NOT_MEASURED',
                      excluded_from_verdict=True)
    return _cli('6',project,output,('quartus_map_audit',),())
def produce_7(project,output,top=None,pdk=None,container=None,**k): return _require(project,output,'7','emit_step7_asic_sdc+stamp_pvt_corner_coverage',top=top,pdk=pdk,container=container,**k)
def produce_8(project,output,**k):
    """Run the complete Step-8 contract against the staged project."""
    _verify_manifest(project, '8')
    project, output = Path(project), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Stage only immutable design inputs; all reports are private outputs.
    staged = output / 'project'
    if staged.exists(): shutil.rmtree(staged)
    def _ignore(src, names):
        return {n for n in names if n in {'issued_manifest.json', 'out', 'frontend_outputs'}
                or (Path(src) / n).resolve() == output.resolve()}
    shutil.copytree(project, staged, ignore=_ignore)
    # Canonical consumer addresses the staged project root; expose immutable
    # staged inputs there while keeping the private reconstruction directory.
    for rel in ('phase1', 'phase2'):
        src = staged / rel; dst = output / rel
        if src.is_dir() and not dst.exists(): shutil.copytree(src, dst)
    report = output / 'reports/phase2/sdc_check.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    l8 = k.get('l8') or next(staged.rglob('L8_TIMING_WAVEFORM.json'), None)
    if l8 is not None:
        l8 = Path(l8)
        if not l8.is_absolute():
            l8 = staged / l8
        elif l8.exists() and str(l8).startswith(str(project)):
            l8 = staged / l8.relative_to(project)
    validator_report = output / 'reports/sdc_validator.json'
    commands = [
        ['python3', str(Path(__file__).with_name('sdc_syntax_check.py')), str(staged), '--json', str(report)],
        ['python3', str(Path(__file__).with_name('sdc_validator_check.py')), str(staged), '--l8', str(l8), '--json', str(validator_report)],
    ]
    records=[]
    for argv in commands:
        if l8 is None and '--l8' in argv:
            return _write('8', output, 'sdc_syntax_check+sdc_validator_check', reason='L8 fixture missing', records=records)
        cp=subprocess.run(argv, capture_output=True, text=True)
        records.append({'argv':argv, 'rc':cp.returncode, 'stdout':cp.stdout, 'stderr':cp.stderr})
        if cp.returncode != 0:
            raise RuntimeError(f'Step8 producer failed rc={cp.returncode}: {argv[0]}')
    return report
def produce_10(project,output,top=None,pdk=None,container=None,**k): return _require(project,output,'10','step_prelayout_signoff',top=top,pdk=pdk,container=container,**k)
def produce_11(project,output,top=None,clock=None,pdk=None,**k): return _cli('11',project,output,('fault_scan_chain_insert','fault_atpg_run','bsdl_emit'))
def produce_fs1(project,output,**k): return _cli('FS1',project,output,('fmeda_fault_injection_coverage','fmeda_coverage_check'))
def produce_dt1(project,output,top=None,clock=None,timeout=None,**k): return _cli('DT1',project,output,('transition_fault_atpg_run',))
def produce_12(project,output,**k):
    if project is None or output is None: raise ValueError('12: project and output are required')
    project=Path(project); output=Path(output); scan=project/'phase2/stage2/dft/scan_netlist.v'; target=output/'phase2/stage2/synth/post_dft_netlist.v'
    if not scan.is_file(): return _write('12',output,'design_one_shot_runner Yosys command',reason='scan netlist input unavailable',inputs=str(scan))
    target.parent.mkdir(parents=True,exist_ok=True)
    script=f'read_verilog "{scan}"; opt_clean -purge; write_verilog -noattr "{target}"'
    try: cp=subprocess.run(['yosys','-p',script],capture_output=True,text=True)
    except OSError as exc: return _write('12',output,'design_one_shot_runner Yosys command',reason=f'yosys unavailable: {exc}')
    if cp.returncode != 0 or not target.is_file(): return _write('12',output,'design_one_shot_runner Yosys command',reason='yosys producer failed',rc=cp.returncode,stderr=cp.stderr)
    return target
def produce_13(project,output,top=None,container=None,lec_max_completed_rungs=None,**k): return _cli('13',project,output,('design_one_shot_runner',))
def produce_dt2(project,output,top=None,clock=None,timeout=None,**k): return _cli('DT2',project,output,('path_delay_fault_atpg_run',))
def produce_dt3(project,output,top=None,clock=None,timeout=None,**k): return _cli('DT3',project,output,('sdd_atpg_run',))
def produce_p0(project,output,top=None,claim=None,**k):
    if not top or not claim: raise ValueError('P0: top and claim are required')
    import p0_tool_frontend_check as p0
    return _record('P0',project,output,'p0_tool_frontend_check.check+formal_structural_check.check_claim',p0.check,top=top,claim=claim,**k)

PRODUCERS={'D1':produce_d1,'0.5ic':produce_05ic,'1':produce_1,'2':produce_2,'3':produce_3,'4':produce_4,'5':produce_5,'6':produce_6,'7':produce_7,'8':produce_8,'10':produce_10,'11':produce_11,'FS1':produce_fs1,'DT1':produce_dt1,'12':produce_12,'13':produce_13,'DT2':produce_dt2,'DT3':produce_dt3,'P0':produce_p0}
REQUIRED_PARAMETERS={'D1':(), '0.5ic':(), '1':('ic_class',), '2':('top','clock','timeout','baseline_rtl_dir','candidate_rtl_dir'), '3':('top',), '4':('top','container'), '5':('top','container'), '6':('top','container'), '7':('top','pdk','container'), '8':(), '10':('top','pdk','container'), '11':('top','clock','pdk'), 'FS1':('asil',), 'DT1':('top','clock','timeout'), '12':(), '13':('top','container','lec_max_completed_rungs'), 'DT2':('top','clock','timeout'), 'DT3':('top','clock','timeout'), 'P0':('top','claim')}
def run_row(step_id,project,output,**kwargs):
    if step_id not in PRODUCERS: raise ValueError(f'unknown frontend row: {step_id}')
    manifest=Path(project)/'input'/'issued_manifest.json'
    if not manifest.is_file():
        manifest=Path(project)/'issued_manifest.json'
    if manifest.is_file():
        try:
            bound=json.loads(manifest.read_text()).get('parameters') or {}
        except (OSError,ValueError) as exc: raise ValueError(f'{step_id}: issued manifest invalid') from exc
        if any(key in kwargs and kwargs[key] != value for key, value in bound.items()
               if key in kwargs):
            raise ValueError(f'{step_id}: caller parameters disagree with issued manifest')
        merged=dict(bound); merged.update(kwargs); kwargs=merged
    missing=[key for key in REQUIRED_PARAMETERS[step_id] if kwargs.get(key) in (None,'')]
    if step_id == '0.5ic' and bool(kwargs.get('template')) == bool(kwargs.get('no_template_reason')):
        missing=['template_or_no_template_reason']
    if missing: raise ValueError(f'{step_id}: missing parameters: {", ".join(missing)}')
    return PRODUCERS[step_id](project,output,**kwargs)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--step',required=True); ap.add_argument('--inputs',required=True); ap.add_argument('--outputs',required=True); a=ap.parse_args(); run_row(a.step,a.inputs,a.outputs)
if __name__=='__main__': main()

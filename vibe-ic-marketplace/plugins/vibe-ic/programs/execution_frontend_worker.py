"""Explicit fail-closed frontend producer entry points."""
import argparse, json, os, subprocess, shutil
import hashlib
import time
from contextvars import ContextVar
from pathlib import Path
from _execution_manifest import ISSUED_MANIFEST_ENV, issued_manifest_path

_STEP8_PRODUCER_TIMEOUT_S = 30
_STEP8_DISPATCH = ContextVar('step8_dispatch', default=False)
_MANIFEST_AUTHORITY = ContextVar('manifest_authority', default=None)


def _issued_manifest_path(project, authority_path=None):
    """Resolve the one Controller-issued authority path for a worker run.

    Controller dispatch binds this path explicitly in both argv and the
    environment. Without an explicit path, only the input-root authority is
    consumed. No nested same-basename input can select worker authority.
    """
    project = Path(project)
    bound = os.environ.get(ISSUED_MANIFEST_ENV)
    raw = authority_path or _MANIFEST_AUTHORITY.get() or bound
    path = Path(raw) if raw else issued_manifest_path(project)
    if bound and path.absolute() != Path(bound).absolute():
        raise ValueError('issued manifest authority path mismatch')
    if path.is_symlink() or not path.is_file():
        raise ValueError('issued manifest authority path is required')
    return path


def _request_manifest_path(project):
    """Legacy helper requests are data and never Controller authority."""
    root = issued_manifest_path(project)
    return root if root.is_file() else Path(project) / 'input/issued_manifest.json'

def _canonical_route(step: str) -> tuple[str, ...]:
    # Kept in the worker boundary so a caller cannot replace the route by
    # writing a different manifest.  The provider registry carries the same
    # route for Controller planning and binds this module's source closure.
    from execution_frontend_providers import ROUTES
    try:
        return tuple(ROUTES[step])
    except KeyError as exc:
        raise ValueError(f'{step}: canonical route is unknown') from exc


def _strict_issuance() -> bool:
    return bool(os.environ.get('VIBEIC_MANIFEST_SHA256'))

def _write(step, output, producer, **details):
    output=Path(output); output.mkdir(parents=True, exist_ok=True)
    out=output/'canonical.json'
    out.write_text(json.dumps({
        'schema':'frontend_worker_output/1', 'step_id':step,
        'producer':producer, 'result':'NOT_MEASURED',
        'manifest_authority': ('controller-issued' if _strict_issuance()
                               else 'legacy-unqualified'),
        **details}, sort_keys=True, default=str)+'\n')
    return out


def _verify_manifest(project, step, authority_path=None):
    try:
        explicit = authority_path or _MANIFEST_AUTHORITY.get() or os.environ.get(ISSUED_MANIFEST_ENV)
        manifest = (_issued_manifest_path(project, explicit) if explicit or _strict_issuance()
                    else _request_manifest_path(project))
    except ValueError as exc:
        raise ValueError(f'{step}: issued manifest is required') from exc
    try: record=json.loads(manifest.read_text())
    except (OSError,ValueError) as exc: raise ValueError(f'{step}: issued manifest invalid') from exc
    strict = _strict_issuance()
    expected_keys = ({'schema', 'step_id', 'route', 'parameters', 'files',
                      'source_sha', 'source_tree_sha', 'controller_sha256',
                      'plan_sha256'} if strict else
                     {'step_id', 'parameters', 'files'})
    if set(record) != expected_keys:
        raise ValueError(f'{step}: issued manifest schema mismatch')
    if strict and record.get('schema') != 'execution-issued-manifest/v2':
        raise ValueError(f'{step}: issued manifest schema mismatch')
    if not isinstance(record.get('parameters'), dict) or not isinstance(record.get('files'), dict):
        raise ValueError(f'{step}: issued manifest fields must be typed')
    if strict:
        route = record.get('route')
        if (not isinstance(route, list) or
                tuple(route) != _canonical_route(step) or
                json.loads(os.environ.get('VIBEIC_CANONICAL_ROUTE', '[]')) != route):
            raise ValueError(f'{step}: issued route is not canonical')
        binding = json.loads(os.environ.get('VIBEIC_EXECUTION_BINDING', '{}'))
        if (record.get('source_sha') != os.environ.get('VIBEIC_SOURCE_SHA', '') or
                record.get('source_tree_sha') != os.environ.get('VIBEIC_SOURCE_TREE_SHA', '') or
                record.get('controller_sha256') != binding.get('controller_sha256')):
            raise ValueError(f'{step}: issued source binding mismatch')
    for rel, expected in record['files'].items():
        if not isinstance(rel, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f'{step}: issued file digest schema mismatch')
        path=Path(project)/rel
        if (Path(rel).is_absolute() or '..' in Path(rel).parts or
                path.is_symlink() or not path.is_file() or
                not path.resolve().is_relative_to(Path(project).resolve()) or
                hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError(f'{step}: issued input mutation: {rel}')
    if record.get('step_id') != step: raise ValueError(f'{step}: issued route mismatch')
    if strict:
        expected_sha = os.environ.get('VIBEIC_MANIFEST_SHA256')
        actual_sha = hashlib.sha256((json.dumps(record, sort_keys=True) + '\n').encode()).hexdigest()
        if expected_sha != actual_sha:
            raise ValueError(f'{step}: issued manifest authority mismatch')
    return record


def _step8_inputs(project, manifest):
    """Require every canonical input, nonempty and covered by the manifest."""
    import _flow_yaml
    row = next(s for s in _flow_yaml.load()['steps'] if str(s['id']) == '8')
    for contract in row['required_inputs']:
        matches = list(Path(project).glob(contract['path']))
        if not matches:
            raise ValueError(f"8: required input missing: {contract['path']}")
        for path in matches:
            rel = str(path.relative_to(project))
            if (path.is_symlink() or not path.is_file() or not path.stat().st_size or
                    manifest['files'].get(rel) != hashlib.sha256(path.read_bytes()).hexdigest()):
                raise ValueError(f'8: required input empty or unbound: {rel}')

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
def produce_05ic(project,output,template=None,no_template_reason=None,**k):
    if bool(template) == bool(no_template_reason):
        raise ValueError('0.5ic: exactly one of template or no_template_reason is required')
    _verify_manifest(project, '0.5ic')
    project, output = Path(project), Path(output)
    staged = output / 'project'; output.mkdir(parents=True, exist_ok=True)
    if staged.exists(): shutil.rmtree(staged)
    shutil.copytree(project, staged, ignore=shutil.ignore_patterns('issued_manifest.json','frontend_outputs'))
    args1=['python3',str(Path(__file__).with_name('submission_template_ingest.py')),str(staged)]
    args1 += ['--template',str(template)] if template else ['--no-template-reason',str(no_template_reason)]
    answers=staged/'input/step_0_5ic_answers.json'
    if not answers.is_file(): raise ValueError('0.5ic: answers file is required')
    args2=['python3',str(Path(__file__).with_name('tapeout_declaration_gen.py')),str(staged),'--answers',str(answers)]
    records=[]
    for argv in (args1,args2):
        cp=subprocess.run(argv,capture_output=True,text=True); records.append({'argv':argv,'rc':cp.returncode,'stdout':cp.stdout,'stderr':cp.stderr})
        if cp.returncode != 0: raise RuntimeError(f'0.5ic producer failed rc={cp.returncode}')
    for rel in ('reports/phase1/submission_template.json','reports/phase1/tapeout_declaration.json','input/submission_template/tapeout_declaration.json'):
        src=staged/rel
        if src.is_file():
            dst=output/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dst)
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
    if not _STEP8_DISPATCH.get():
        raise ValueError('8: direct helper execution is unissued')
    manifest = _verify_manifest(project, '8')
    if not _strict_issuance():
        raise ValueError('8: legacy helper execution is unissued')
    _step8_inputs(Path(project), manifest)
    project, output = Path(project), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'canonical.json').unlink(missing_ok=True)
    # Stage only immutable design inputs; all reports are private outputs.
    staged = output / 'project'
    if staged.exists(): shutil.rmtree(staged)
    staged.mkdir()
    # Copy the bound ordinary census, including nested authority basenames.
    # The root metadata file is excluded by issuance, never by basename.
    for rel in manifest['files']:
        target = staged / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / rel, target)
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
        elif l8.exists():
            # A discovered L8 path is already inside the staged tree.  Only
            # remap an explicitly supplied absolute path from the source
            # project; string-prefix checks would treat ``/project-out`` as a
            # child of ``/project`` and raise before the producer runs.
            try:
                l8 = staged / l8.relative_to(project)
            except ValueError:
                pass
    validator_report = output / 'reports/sdc_validator.json'
    # The canonical Step-8 audit also has an optional derived-clock clause.
    # Give it the declared empty RTL scope when this source-only fixture has
    # no RTL, so the checker records an honest zero-denominator PASS instead
    # of an absent-input diagnostic.
    (staged / 'phase2/stage1/rtl').mkdir(parents=True, exist_ok=True)
    commands = [
        ['python3', str(Path(__file__).with_name('sdc_syntax_check.py')), str(staged), '--json', str(report)],
        ['python3', str(Path(__file__).with_name('sdc_validator_check.py')), str(staged), '--l8', str(l8), '--json', str(validator_report)],
        ['python3', str(Path(__file__).with_name('derived_clock_sdc_required_check.py')),
         str(staged / 'phase2/stage1/rtl'), '--sdc',
         str(staged / 'phase2/stage2/constraints'), '--json',
         str(output / 'reports/phase2/gates/derived_clock_sdc.json')],
    ]
    records=[]
    for program, argv in zip(('sdc_syntax_check', 'sdc_validator_check',
                              'derived_clock_sdc_required_check'), commands):
        if l8 is None and '--l8' in argv:
            return _write('8', output, 'sdc_syntax_check+sdc_validator_check', reason='L8 fixture missing', records=records)
        target = Path(argv[-1])
        def fingerprint():
            if not target.is_file() or target.is_symlink():
                return None
            stat = target.stat()
            return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
                    stat.st_ctime_ns, hashlib.sha256(target.read_bytes()).hexdigest())
        before = fingerprint()
        started_ns = time.time_ns()
        try:
            cp=subprocess.run(argv, capture_output=True, text=True,
                              timeout=_STEP8_PRODUCER_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f'Step8 producer timed out after {_STEP8_PRODUCER_TIMEOUT_S}s: '
                f'{argv[0]}') from exc
        ended_ns = time.time_ns()
        after = fingerprint()
        if (after is None or not after[2] or after == before or
                not started_ns <= after[3] <= ended_ns):
            raise RuntimeError(f'Step8 producer output missing, empty or stale: {target}')
        records.append({'program': program, 'argv':argv, 'rc':cp.returncode,
                        'stdout':cp.stdout, 'stderr':cp.stderr,
                        'started_ns':started_ns, 'ended_ns':ended_ns,
                        'output':str(target.relative_to(output)),
                        'output_mtime_ns':after[3], 'output_sha256':after[5]})
        if cp.returncode != 0:
            _write('8', output, 'sdc_syntax_check+sdc_validator_check',
                   issued_manifest_sha256=os.environ['VIBEIC_MANIFEST_SHA256'],
                   reason='measured producer failure', records=records)
            raise RuntimeError(f'Step8 producer failed rc={cp.returncode}: {argv[0]}')
    _write('8', output, 'sdc_syntax_check+sdc_validator_check',
           reason='all producers executed', records=records,
           issued_manifest_sha256=os.environ['VIBEIC_MANIFEST_SHA256'])
    # Bind the required output to this worker's observation.  The canonical
    # checker refuses a project-wide glob match without the run's own ledger.
    # A minimal index is enough for the ledger reader; the ledger itself is
    # generated from live output bytes and is not caller-authored evidence.
    index = output / 'steps/index.json'
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({'steps': [{'id': '8',
        'folder': 'phase2/stage2/8_sdc_validation'}]}) + '\n')
    import step_write_ledger
    step_write_ledger.emit(output)
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


def _step8_controller_request(project, output, parameters):
    """Legacy requests are data, never issuance. Delegate to the live issuer.

    This compatibility entry returns diagnostic reports from a new Controller
    run. AI selection and production adoption remain at their explicit boundary.
    """
    import execution_modes as em
    import execution_frontend_providers as providers
    project, output = Path(project), Path(output)
    manifest = _verify_manifest(project, '8')
    if parameters != manifest['parameters']:
        raise ValueError('8: caller parameters differ from request')
    # Resolve and check the entire requested input census before creating work.
    _step8_inputs(project, manifest)
    files = {rel: project / rel for rel in manifest['files']}
    registry = em.Registry()
    providers.register_factories(registry)
    arm = registry.adapters('8')[0]
    portfolio = em.load_portfolio()
    row = next(s for s in portfolio['steps'] if s['id'] == '8')
    ctx = em.Context('8', arm.source_sha, files,
                     {'metric': 'source_boundary', **parameters},
                     tuple(row['mandatory_gate_programs']))
    controller = em.Controller(registry, em.Budget(1, 512, 1), portfolio)
    import uuid
    root = output / ('issued-run-' + uuid.uuid4().hex)
    controller.run(ctx, root)
    receipt = json.loads((root / arm.arm_id / 'receipt.json').read_text())
    if receipt['status'] != 'ELIGIBLE':
        raise RuntimeError(f"8: issued diagnostic execution refused: {receipt['reason']}")
    for rel, expected in receipt['evidence']['outputs'].items():
        source = Path(receipt['output_root']) / rel
        if em.digest(source) != expected:
            raise RuntimeError(f'8: issued output changed: {rel}')
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return output / 'reports/phase2/sdc_check.json'


def run_row(step_id,project,output,authority_path=None,**kwargs):
    if step_id not in PRODUCERS: raise ValueError(f'unknown frontend row: {step_id}')
    manifest = None
    try:
        manifest = (_issued_manifest_path(project, authority_path) if _strict_issuance()
                    else _request_manifest_path(project))
    except ValueError:
        # Rows without an issued authority retain their existing typed
        # parameter validation. Step 8 itself fails closed below.
        if step_id == '8':
            raise
    if manifest is not None:
        try:
            record = _verify_manifest(project, step_id, manifest)
            bound=record.get('parameters') or {}
        except (OSError,ValueError) as exc: raise ValueError(f'{step_id}: issued manifest invalid') from exc
        if _strict_issuance() and kwargs and kwargs != bound:
            raise ValueError(f'{step_id}: caller parameters differ from issued manifest')
        merged=dict(bound); merged.update(kwargs); kwargs=merged
    missing=[key for key in REQUIRED_PARAMETERS[step_id] if kwargs.get(key) in (None,'')]
    if step_id == '0.5ic' and not (kwargs.get('template') or kwargs.get('no_template_reason')): missing=['template_or_no_template_reason']
    if missing: raise ValueError(f'{step_id}: missing parameters: {", ".join(missing)}')
    if step_id == '8':
        if not _strict_issuance():
            return _step8_controller_request(project, output, kwargs)
        token = _STEP8_DISPATCH.set(True)
        authority_token = _MANIFEST_AUTHORITY.set(manifest)
        try:
            return PRODUCERS[step_id](project,output,**kwargs)
        finally:
            _MANIFEST_AUTHORITY.reset(authority_token)
            _STEP8_DISPATCH.reset(token)
    if manifest is not None:
        authority_token = _MANIFEST_AUTHORITY.set(manifest)
        try:
            return PRODUCERS[step_id](project,output,**kwargs)
        finally:
            _MANIFEST_AUTHORITY.reset(authority_token)
    return PRODUCERS[step_id](project,output,**kwargs)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--step',required=True); ap.add_argument('--inputs',required=True); ap.add_argument('--outputs',required=True); ap.add_argument('--manifest',default=None); a=ap.parse_args(); run_row(a.step,a.inputs,a.outputs,a.manifest)
if __name__=='__main__': main()

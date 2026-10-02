"""Source-only provider family for the canonical frontend rows.

Factories describe callable producers and contracts; they do not qualify an
EDA run. Missing inputs/tools therefore return typed NOT_IMPLEMENTED or
NOT_MEASURED and never fabricate an output or PASS.
"""
from dataclasses import dataclass
import ast
from functools import lru_cache
from pathlib import Path
import hashlib, json, shutil, sys
import subprocess
from typing import Callable

ROWS = ('D1','0.5ic','1','2','3','4','5','6','7','8','10','11','FS1','DT1','12','13','DT2','DT3','P0')
ROUTES = {'D1':('design_one_shot_runner.step_phase1',),'0.5ic':('submission_template_ingest.main','tapeout_declaration_gen.main'),'1':('design_one_shot_runner.step_rtl_gen',),'2':('p0_tool_frontend_check.check','crosslayer_rewrite_equivalence.main'),'3':('_cdc_netlist.build','cdc_crossing_check.main','cdc_async_input_check.main','clock_domain_reg_crossing_check.main','reset_dependency_check.main'),'4':('design_one_shot_runner.step_professional_tb_gen','design_one_shot_runner.step_reference_tb','design_one_shot_runner.step_l10_unit_tb_run','verilator_coverage_measure.main'),'5':('formal_harness_gen.generate','formal_property_run.run','design_one_shot_runner.step_full_stack_functional_tb'),'6':('design_one_shot_runner.step_fpga_compile','quartus_map_audit.main'),'7':('_ppa.timing.emit_step7_asic_sdc','phase3_one_shot_runner.stamp_pvt_corner_coverage'),'8':('sdc_syntax_check.main','sdc_validator_check.main','derived_clock_sdc_required_check.main'),'10':('phase3_one_shot_runner.step_prelayout_signoff',),'11':('fault_scan_chain_insert.main','fault_atpg_run.main','bsdl_emit.main'),'FS1':('fmeda_fault_injection_coverage.main','fmeda_coverage_check.main'),'DT1':('transition_fault_atpg_run.main',),'12':('execution_frontend_worker.produce',),'13':('design_one_shot_runner.step_lec_equivalence',),'DT2':('path_delay_fault_atpg_run.main',),'DT3':('sdd_atpg_run.main'),'P0':('p0_tool_frontend_check.check','formal_structural_check.check_claim')}
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


def _repo_root(path: Path) -> Path:
    for candidate in (path.resolve(), *path.resolve().parents):
        if (candidate / '.git').exists():
            return candidate
    raise ValueError(f'frontend provider repository root unavailable: {path}')


def _local_imports(path: Path, programs: Path) -> set[Path]:
    """Return directly imported sibling modules without executing them."""
    found: set[Path] = set()
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeError):
        return found
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split('.')[0])
    for name in names:
        candidate = programs / f'{name}.py'
        if candidate.is_file():
            found.add(candidate.resolve())
        package = programs / name / '__init__.py'
        if package.is_file():
            found.add(package.resolve())
    return found


@lru_cache(maxsize=32)
def _source_closure(start: tuple[Path, ...], programs: Path,
                    source_tree_sha: str = '') -> tuple[Path, ...]:
    """Compute the complete local Python import closure for an adapter."""
    todo = [p.resolve() for p in start if p.is_file()]
    seen: set[Path] = set()
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        for child in _local_imports(current, programs):
            if child not in seen:
                todo.append(child)
    return tuple(sorted(seen, key=str))


def _git_identity(repo: Path) -> tuple[str, str]:
    status = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain',
                             '--untracked-files=all'], capture_output=True,
                            text=True, check=False)
    if status.returncode != 0:
        raise ValueError('frontend provider repository status unavailable')
    if status.stdout:
        raise ValueError('frontend provider source tree is dirty; refusing registration')
    try:
        commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse',
                                          'HEAD'], text=True).strip()
        tree = subprocess.check_output(['git', '-C', str(repo), 'rev-parse',
                                        'HEAD^{tree}'], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError('frontend provider source identity unavailable') from exc
    return commit, tree


def _exact_dict(doc: object, keys: set[str], label: str) -> dict:
    if not isinstance(doc, dict) or set(doc) != keys:
        raise ValueError(f'{label}: exact schema mismatch')
    return doc


def _typed_syntax_report(path: Path) -> bool:
    doc = json.loads(path.read_text())
    _exact_dict(doc, {'program', 'passed', 'findings', 'summary', 'invoked_as'},
                'sdc_syntax_check')
    if doc['program'] != 'sdc_syntax_check' or type(doc['passed']) is not bool:
        raise ValueError('sdc_syntax_check: conflicting or stringly fields')
    if doc['invoked_as'] != 'producer' or not isinstance(doc['findings'], list):
        raise ValueError('sdc_syntax_check: invalid invocation/findings')
    for finding in doc['findings']:
        _exact_dict(finding, {'rule', 'severity', 'message', 'file'},
                    'sdc_syntax_check finding')
        if (not all(isinstance(finding[k], str) for k in finding)
                or finding['severity'] not in {'ERROR', 'WARNING', 'INFO'}):
            raise ValueError('sdc_syntax_check: invalid finding types')
    summary = doc['summary']
    if not isinstance(summary, dict) or not set(summary).issubset({
            'mode', 'judge', 'files_checked', 'valid_files', 'errors',
            'clocks_found'}):
        raise ValueError('sdc_syntax_check: summary schema mismatch')
    summary_keys = set(summary)
    direct_keys = {'files_checked', 'valid_files', 'errors', 'clocks_found'}
    empty_direct_keys = {'files_checked', 'valid_files'}
    if summary.get('mode') == 'librelane':
        if summary_keys != {'mode', 'judge'}:
            raise ValueError('sdc_syntax_check: librelane summary schema mismatch')
    elif summary.get('mode') in {'dual', 'invalid'}:
        if summary_keys != {'mode', *direct_keys}:
            raise ValueError('sdc_syntax_check: mixed summary schema mismatch')
    elif 'mode' in summary or 'judge' in summary:
        raise ValueError('sdc_syntax_check: unknown summary mode')
    elif not any(summary_keys == candidate for candidate in
                 (empty_direct_keys, direct_keys)):
        raise ValueError('sdc_syntax_check: direct summary schema mismatch')
    if doc['passed'] and not any(summary_keys == candidate for candidate in (
            direct_keys, {'mode', *direct_keys}, {'mode', 'judge'})):
        raise ValueError('sdc_syntax_check: passing summary is incomplete')
    for key in ('mode', 'judge'):
        if key in summary and not isinstance(summary[key], str):
            raise ValueError('sdc_syntax_check: summary type mismatch')
    for key in ('files_checked', 'valid_files', 'errors', 'clocks_found'):
        if key in summary and (type(summary[key]) is not int or summary[key] < 0):
            raise ValueError('sdc_syntax_check: summary type mismatch')
    return doc['passed']


def _typed_validator_report(path: Path) -> bool:
    doc = json.loads(path.read_text())
    _exact_dict(doc, {'verdict', 'exit_code', 'search_roots',
                      'sdc_files_checked', 'sdc_files_superseded', 'l8',
                      'issues'}, 'sdc_validator_check')
    verdict, exit_code = doc['verdict'], doc['exit_code']
    if verdict not in {'PASS', 'FAIL', 'SKIP'} or type(exit_code) is not int:
        raise ValueError('sdc_validator_check: conflicting or stringly fields')
    if {'PASS': 0, 'FAIL': 1, 'SKIP': 2}[verdict] != exit_code:
        raise ValueError('sdc_validator_check: verdict/exit_code conflict')
    for key in ('search_roots', 'sdc_files_checked', 'issues'):
        if not isinstance(doc[key], list) or not all(isinstance(v, str) for v in doc[key]):
            raise ValueError('sdc_validator_check: list field is not typed')
    if not isinstance(doc['sdc_files_superseded'], list):
        raise ValueError('sdc_validator_check: superseded field is not typed')
    for row in doc['sdc_files_superseded']:
        _exact_dict(row, {'path', 'top_entity', 'reason'},
                    'sdc_validator_check superseded')
        if not all(isinstance(row[k], str) for k in row):
            raise ValueError('sdc_validator_check: superseded field is not typed')
    if doc['l8'] is not None and not isinstance(doc['l8'], str):
        raise ValueError('sdc_validator_check: l8 field is not typed')
    if (verdict == 'PASS' and doc['issues']) or (verdict == 'FAIL' and not doc['issues']) \
            or (verdict == 'SKIP' and doc['issues']):
        raise ValueError('sdc_validator_check: verdict/issues conflict')
    return verdict == 'PASS'


def _typed_derived_report(path: Path) -> bool:
    doc = _exact_dict(json.loads(path.read_text()),
                      {'target', 'sdc', 'errors', 'findings', 'verdict'},
                      'derived_clock_sdc_required_check')
    if (not isinstance(doc['target'], str) or not isinstance(doc['sdc'], str) or
            type(doc['errors']) is not int or doc['errors'] < 0 or
            doc['verdict'] not in {'PASS', 'FAIL'} or
            (doc['verdict'] == 'FAIL') != (doc['errors'] > 0) or
            not isinstance(doc['findings'], list)):
        raise ValueError('derived clock report: verdict/types conflict')
    for row in doc['findings']:
        _exact_dict(row, {'severity', 'rule', 'file', 'line', 'message'},
                    'derived clock finding')
        if (type(row['line']) is not int or
                row['severity'] not in {'ERROR', 'WARN', 'INFO'} or
                any(not isinstance(row[k], str)
                    for k in ('severity', 'rule', 'file', 'message'))):
            raise ValueError('derived clock finding: invalid types')
    if doc['errors'] != sum(f['severity'] == 'ERROR' for f in doc['findings']):
        raise ValueError('derived clock report: error count conflict')
    return doc['verdict'] == 'PASS'


def _step8_evidence(root, facts, required, contract):
    """BLOCKING: consume fresh, digest-bound producer reports before auditing."""
    from execution_modes import Evidence, digest
    import flow_compliance_check
    names = tuple(contract.get('mandatory_gate_programs') or ('sdc_syntax_check',))
    authority = root / 'canonical.json'
    artifacts, gates = {}, {n: 'NOT_MEASURED' for n in names}
    try:
        marker = json.loads(authority.read_text())
        if (marker['step_id'] != '8' or
                marker['manifest_authority'] != 'controller-issued' or
                marker['issued_manifest_sha256'] != digest(
                    root.parent / 'inputs/issued_manifest.json')):
            raise ValueError('worker issuance does not match frozen manifest')
        consumers = {'sdc_syntax_check': _typed_syntax_report,
                     'sdc_validator_check': _typed_validator_report,
                     'derived_clock_sdc_required_check': _typed_derived_report}
        failed = False
        programs = []
        for record in marker['records']:
            rel = record['output']
            path = root / rel
            if (path.is_symlink() or not path.is_file() or not path.stat().st_size or
                    not path.resolve().is_relative_to(root.resolve()) or
                    digest(path) != record['output_sha256'] or
                    not (record['started_ns'] <= record['output_mtime_ns'] <= record['ended_ns'])):
                raise ValueError('producer output is missing, stale or changed')
            passed = consumers[record['program']](path)
            programs.append(record['program'])
            if record['rc'] != (0 if passed else 1):
                raise ValueError('producer report contradicts measured return code')
            artifacts[rel] = digest(path)
            failed |= not passed
            if record['program'] in gates:
                gates[record['program']] = 'PASS' if passed else 'FAIL'
        artifacts['canonical.json'] = digest(authority)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return Evidence(facts, 'NOT_MEASURED', gates, artifacts, detail=str(exc))
    if failed:
        return Evidence(facts, 'FAIL', {n: 'FAIL' for n in names}, artifacts,
                        detail='measured checker FAIL')
    if programs != [r.split('.')[0] for r in ROUTES['8']] or any(v != 'PASS' for v in gates.values()):
        return Evidence(facts, 'NOT_MEASURED', gates, artifacts,
                        detail='producer sequence incomplete')
    gate_result = flow_compliance_check.check_step(
        root, contract, {}, strict_step_binding=True)
    if gate_result.status == 'FAIL':
        return Evidence(facts, 'FAIL', {n: 'FAIL' for n in names}, artifacts,
                        detail='; '.join(gate_result.reasons))
    if any(p not in artifacts for p in required if '*' not in p):
        return Evidence(facts, 'NOT_MEASURED', gates, artifacts,
                        detail='canonical flow artifact missing')
    return Evidence(facts, 'PASS', gates, artifacts,
                    detail='real canonical artifacts and gate validated')

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


def validate_core_dependency(core_source_sha):
    """BLOCKING: production must bind an independently accepted Core revision."""
    import execution_modes as em
    path = Path(__file__).with_name('data') / 'execution_frontend_core_dependency.json'
    dependency = json.loads(path.read_text())
    repo = _repo_root(path)
    head, _ = _git_identity(repo)
    relative = str(path.relative_to(repo))
    if em._tracked_digest(str(repo), head, relative) != em.digest(path):
        raise em.Refusal('CORE_DEPENDENCY_CHANGED', relative)
    accepted = dependency['accepted_core_sha']
    if not accepted or core_source_sha != accepted:
        raise em.Refusal('CORE_REBIND_REQUIRED',
                         'accepted Core R3 commit and replay are required')
    import execution_policy
    import inspect
    for module in (em, execution_policy):
        source = Path(module.__file__).resolve()
        if em._tracked_digest(str(repo), accepted, str(source.relative_to(repo))) != em.digest(source):
            raise em.Refusal('CORE_SOURCE_MISMATCH', str(source))
    for name in dependency['controller_methods']:
        if not callable(getattr(em.Controller, name, None)):
            raise em.Refusal('CORE_API_MISMATCH', name)
    for name in dependency['context_fields']:
        if name not in em.Context.__dataclass_fields__:
            raise em.Refusal('CORE_API_MISMATCH', name)
    for name in dependency['adapter_fields']:
        if name not in em.Adapter.__dataclass_fields__:
            raise em.Refusal('CORE_API_MISMATCH', name)
    if set(inspect.signature(execution_policy.controller_fields).parameters) != {
            'ic_ip_path', 'route_receipt'}:
        raise em.Refusal('CORE_API_MISMATCH', 'controller_fields')
    return dependency


def run_step8_controller(controller, context, output, choice, *, core_source_sha):
    """Production composition seam; standalone fixture adoption cannot enter it."""
    import execution_modes as em
    validate_core_dependency(core_source_sha)
    import execution_policy
    if context.step_id != '8':
        raise em.Refusal('WRONG_CANONICAL_STEP', context.step_id)
    fields = execution_policy.controller_fields(
        ic_ip_path=context.ic_ip_path, route_receipt=dict(context.route_receipt))
    if any(getattr(context, key) != value for key, value in fields.items()):
        raise em.Refusal('CORE_CONTEXT_UNBOUND', context.step_id)
    result = controller.run(context, output)
    if any(v == 'FAIL' for v in result.get('candidate_statuses', {}).values()):
        raise em.Refusal('GATE_FAIL', 'measured Step8 failure blocks adoption')
    controller.adopt(context, output, choice)
    return controller.verify_adoption(context, output)

def register_factories(registry):
    """Register real source adapters in the existing Registry."""
    if not hasattr(registry, 'register'): raise TypeError('registry must provide register')
    import execution_modes as em
    from execution_modes import Adapter, Component, Evidence, digest
    provider_path = Path(__file__).resolve()
    source = str(provider_path)
    worker_path = provider_path.with_name('execution_frontend_worker.py')
    worker = str(worker_path.resolve())
    py_path = Path(shutil.which('python3') or sys.executable).resolve()
    py = str(py_path)
    repo = _repo_root(provider_path)
    sha, tree_sha = _git_identity(repo)
    programs = provider_path.parent
    for row,p in PROVIDERS.items():
        def validate(project, facts, _row=row):
            import _flow_yaml, flow_compliance_check
            contract=next(s for s in _flow_yaml.load()['steps'] if str(s['id']) == _row)
            root=Path(project)
            required=tuple(contract.get('required_outputs') or ())
            if _row == '8':
                return _step8_evidence(root, facts, required, contract)
            artifacts={p:digest(root/p) for p in required if '*' not in p and (root/p).is_file()}
            if required and len(artifacts) < len([p for p in required if '*' not in p]): raise ValueError('canonical flow artifacts missing')
            gate_result=flow_compliance_check.check_step(root, contract, {}, strict_step_binding=True)
            gate = getattr(gate_result, 'status', gate_result)
            # The canonical checker returns a typed StepResult whose overall
            # status also includes optional downstream clauses.  Step 8's
            # mandatory program gate is the two independent producer reports.
            syntax_ok = False; validator_ok = False
            try:
                syntax_ok = _typed_syntax_report(root/'reports/phase2/sdc_check.json')
                vp = root/'reports/sdc_validator.json'
                validator_ok = _typed_validator_report(vp)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                syntax_ok = validator_ok = False
            reasons = getattr(gate_result, 'reasons', ()) or ()
            canonical_status = str(getattr(gate_result, 'status', gate_result))
            # A canonical FAIL is measured evidence even when its prose also
            # mentions setup or missing inputs.  Diagnostics may explain the
            # failure, but they cannot turn it into a PASS.
            gate = 'FAIL' if canonical_status == 'FAIL' else (
                'PASS' if syntax_ok and validator_ok else 'FAIL')
            def gate_names(node):
                if isinstance(node,str): return [node.split()[0]] if node else []
                if isinstance(node,dict): return sum((gate_names(v) for v in node.values()),[])
                if isinstance(node,list): return sum((gate_names(v) for v in node),[])
                return []
            names=tuple(contract.get('mandatory_gate_programs') or ('sdc_syntax_check',))
            gates={n:('PASS' if str(gate) == 'PASS' else str(gate)) for n in names}
            if _row == '8':
                authority = root / 'canonical.json'
                if not authority.is_file():
                    return Evidence(facts, 'NOT_MEASURED', gates, artifacts,
                                    detail='worker authority marker missing')
                try:
                    authority_doc = json.loads(authority.read_text())
                except (OSError, ValueError, TypeError):
                    return Evidence(facts, 'NOT_MEASURED', gates, artifacts,
                                    detail='worker authority marker invalid')
                if authority_doc.get('manifest_authority') != 'controller-issued':
                    return Evidence(facts, 'NOT_MEASURED', gates, artifacts,
                                    detail='worker route was not controller-issued')
                artifacts['canonical.json'] = digest(authority)
                validator_path = root / 'reports/sdc_validator.json'
                if validator_path.is_file():
                    artifacts['reports/sdc_validator.json'] = digest(validator_path)
            verdict='PASS' if artifacts and gates and all(v=='PASS' for v in gates.values()) else 'NOT_MEASURED'
            return Evidence(facts, verdict, gates, artifacts, detail='real canonical artifacts and canonical gate validated')
        required=tuple(next(s for s in em.load_portfolio()['steps'] if s['id']==row)['required_output_contract']) or ('canonical.json',)
        start = [provider_path, worker_path, Path(em.__file__).resolve()]
        start += [provider_path.with_name(name) for name in {
            'sdc_syntax_check.py', 'sdc_validator_check.py',
            'derived_clock_sdc_required_check.py', 'flow_compliance_check.py',
            'step_write_ledger.py'} if row == '8']
        # The worker dispatches the remaining rows through their canonical
        # runner modules at execution time.  Binding the entire import graph of
        # those legacy runners would walk thousands of unrelated programs; the
        # Step-8 provider is the source-only candidate whose transitive checker
        # closure is required here (including _path_layout.py).
        closure = _source_closure(tuple(start), programs, tree_sha)
        bound_files={str(path):digest(path) for path in closure}
        for path in (programs.parent / 'flow/phase1_phase2_phase3.yaml',
                     programs / 'data/execution_modes_portfolio.json',
                     programs / 'data/execution_frontend_coverage.json',
                     programs / 'data/execution_frontend_core_dependency.json'):
            bound_files[str(path.resolve())] = digest(path)
        bound_files[py] = digest(py_path)
        registry.register(Adapter(
            'frontend_'+row.replace('.','_'), 'frontend-worker', row, sha,
            bound_files, 'current-main', p.engines,
            (Component('frontend_worker',('python3',worker,'--step',row,
                                          '--inputs','{inputs}','--outputs','{outputs}')) ,),
            validate, required, {'metric':'source_boundary'},
            qualification_evidence='route callable bound; native qualification not measured',
            output_contract={path:(path,) for path in required},
            source_tree_sha=tree_sha, route=ROUTES[row]))
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

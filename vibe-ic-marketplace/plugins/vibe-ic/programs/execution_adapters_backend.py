"""F3 production factory: 23 canonical backend producers and exact adoption.

F1 owns execution_step_protocol, native admission, and caller registration.
This module never invokes the outer Phase-3 runner or changes shared policy.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
import execution_backend_snapshot as snap

HERE = Path(__file__).resolve().parent
POLICY = HERE / 'data/execution_backend_policy.json'
ROWS = json.loads(POLICY.read_text())['rows']
STEP_IDS = tuple(ROWS)


def _capture(project, pdk, extra, project_input_roots=None):
    inputs = {}
    # F1 names the complete current consumed roots. Mutable outputs are never
    # an implicit whole-project root in a production request.
    rows = {}
    if project_input_roots is None:
        rows = snap.population(project, 'project', inputs)
    else:
        for name in project_input_roots:
            root = project / snap.relative(name)
            rows.update(snap.population(root, 'project/' + name, inputs, boundary=project))
    rows.update(snap.population(pdk, 'pdk', inputs))
    for name, path in extra.items():
        key = 'external/' + str(snap.relative(name))
        p = Path(path)
        if p.is_dir() and not p.is_symlink():
            rows.update(snap.population(p, key, inputs))
        else:
            rows[key] = snap.file_input(p, key, inputs)
    return inputs, rows


@dataclass(frozen=True)
class BackendContext(em.Context):
    project: Path | None = None
    pdk: Path | None = None
    extra: dict | None = None
    lexical: dict | None = None
    spec_path: Path | None = None
    source_files: dict = field(default_factory=dict)
    project_input_roots: tuple[str, ...] | None = None
    canonical_adoptions: tuple[str, ...] = ()

    def binding(self):
        _, current = _capture(self.project, self.pdk, self.extra, self.project_input_roots)
        if current != self.lexical:
            raise em.Refusal('BACKEND_INPUT_POPULATION_CHANGED', self.step_id)
        for name, expected in self.source_files.items():
            p = Path(name)
            if p.is_symlink() or not p.is_file() or snap.sha(p) != expected:
                raise em.Refusal('BACKEND_SOURCE_MISMATCH', name)
        return super().binding()


def required_source_files():
    """F1 source_identity additions; prepare never extends its request manifest."""
    # Producer helpers, gates, native scripts, and policies are runtime source.
    files = {}
    for p in HERE.rglob('*'):
        if p.is_file() and not p.is_symlink() and 'tests' not in p.parts and '__pycache__' not in p.parts:
            if p.suffix in ('.py', '.tcl', '.json', '.rb', '.drc', '.sh'):
                files[str(p.resolve())] = snap.sha(p)
    files[str(Path(sys.executable).resolve())] = snap.sha(Path(sys.executable).resolve())
    docker = shutil.which('docker')
    if docker:
        files[str(Path(docker).resolve())] = snap.sha(Path(docker).resolve())
    files[str(POLICY)] = snap.sha(POLICY)
    for p in (HERE.parent / 'flow').rglob('*'):
        if p.is_file() and not p.is_symlink():
            files[str(p.resolve())] = snap.sha(p)
    return files


def _source_files(request):
    files = dict(request.source_files)
    for name, expected in required_source_files().items():
        if files.get(name) != expected:
            raise em.Refusal('BACKEND_REQUIRED_SOURCE_UNBOUND', name)
    for name, expected in files.items():
        p = Path(name)
        if p.is_symlink() or not p.is_file() or snap.sha(p) != expected:
            raise em.Refusal('BACKEND_SOURCE_MISMATCH', name)
    return files


def _declared_na(step, project, params):
    if step not in ('15.5ic', '26.5ic'):
        return None
    supplied = params.get('declaration')
    if not supplied:
        return None
    if not isinstance(supplied, Path) or not supplied.is_absolute():
        raise em.Refusal('BACKEND_DECLARATION_NOT_CURRENT', 'declaration must remain a Path')
    declaration = supplied
    if declaration.is_symlink() or not declaration.is_file():
        raise em.Refusal('BACKEND_DECLARATION_NOT_CURRENT', str(declaration))
    expected = params.get('declaration_sha256')
    if expected is not None and expected != snap.sha(declaration):
        raise em.Refusal('BACKEND_DECLARATION_NOT_CURRENT', 'supplied declaration digest changed')
    if declaration.is_file():
        answers = json.loads(declaration.read_text()).get('answers', {})
        if answers.get('deliverable') == 'HARDMACRO':
            return {'declaration': str(declaration), 'declaration_sha256': snap.sha(declaration),
                    'facts': {'answers.deliverable': 'HARDMACRO'}}
    # An absent slot/declaration is unknown delivery, never factual N/A.
    return None


def adoption_paths(step, project=None):
    """Concrete canonical output destinations from this step's contracts."""
    row = ROWS[str(step)]['canonical_row']
    paths = set()
    from fnmatch import fnmatchcase
    for spec in row.get('required_outputs', []):
        for pattern in spec.split(' OR '):
            pattern = pattern.strip()
            if not any(ch in pattern for ch in '*?['):
                paths.add(pattern)
            elif project is not None:
                for candidate in Path(project).glob(pattern):
                    if candidate.is_file() and not candidate.is_symlink():
                        paths.add(str(candidate.relative_to(project)))
    # Step 20's independent area consumer emits this blocking evidence.
    if str(step) == '20':
        paths.add('reports/phase3/hold_area_budget.json')
    return tuple(sorted(paths))


def _project_roots(project, params, row):
    roots = params.get('input_roots')
    if not isinstance(roots, dict) or not roots or any(not isinstance(n, str) for n in roots):
        raise em.Refusal('BACKEND_PROJECT_INPUT_ROOTS_UNSTATED', str(row['id']))
    project_roots, external = [], {}
    for name, path in roots.items():
        if not isinstance(path, Path) or not path.is_absolute():
            raise em.Refusal('BACKEND_PROJECT_INPUT_ROOT_NOT_CURRENT', name)
        if name == 'declaration':
            if path.is_symlink() or not path.is_file():
                raise em.Refusal('BACKEND_PROJECT_INPUT_ROOT_NOT_CURRENT', name)
            # Preserve the actual supplied declaration under its original
            # logical label, whether it resides inside or outside the project.
            external['input_roots/declaration'] = path
        elif name == 'native_facts':
            facts_path = params.get('native_facts_file')
            if not isinstance(facts_path, Path) or path != facts_path:
                raise em.Refusal('BACKEND_PROJECT_INPUT_ROOT_NOT_CURRENT', name)
            external['native_facts_root'] = path
        else:
            try:
                rel = snap.relative(name)
                expected = project / rel
            except (em.Refusal, ValueError):
                expected = None
            if expected is not None and path == expected:
                project_roots.append(str(rel))
            elif not path.is_relative_to(project):
                # F1 labels real external current roots. Bind their full
                # lexical populations under stable aliases, retaining the
                # actual absolute path in each population entry.
                external['input_roots/' + name] = path
            else:
                raise em.Refusal('BACKEND_PROJECT_INPUT_ROOT_NOT_CURRENT', name)
    roots = tuple(sorted(set(project_roots)))
    output_patterns = [p.strip() for item in row.get('required_outputs', []) for p in item.split(' OR ')]
    from fnmatch import fnmatchcase
    for name in roots:
        p = project / name
        # Whole mutable directories would silently include this row's outputs.
        if (p.is_dir() and any(Path(pattern).is_relative_to(name) or Path(name).is_relative_to(Path(pattern).parent)
                               for pattern in output_patterns)) or any(fnmatchcase(name, pattern) for pattern in output_patterns):
            raise em.Refusal('BACKEND_MUTABLE_OUTPUT_IN_INPUT_ROOT', name)
    for required in row.get('required_inputs') or []:
        pattern = required.get('path')
        if not pattern:
            continue
        selected = [p for alternative in pattern.split(' OR ') for p in project.glob(alternative.strip()) if p.is_file()]
        if not selected or any(not any(str(p.relative_to(project)) == n or
                p.is_relative_to(project / n) for n in roots) for p in selected):
            raise em.Refusal('BACKEND_REQUIRED_INPUT_ROOT_UNBOUND', pattern)
    return roots, external


def _native_facts(params, extra):
    """F1 supplies the parsed document and its actual bound filesystem path."""
    if not params.get('native_facts_file') or not isinstance(params.get('native_facts'), dict):
        raise em.Refusal('BACKEND_NATIVE_FACTS_UNSTATED', 'native_facts_file Path + native_facts document')
    if not isinstance(params['native_facts_file'], Path):
        raise em.Refusal('BACKEND_NATIVE_FACTS_PATH_UNBOUND', 'native_facts_file must remain a Path')
    path = params['native_facts_file']
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise em.Refusal('BACKEND_NATIVE_FACTS_PATH_UNBOUND', str(path))
    facts = json.loads(path.read_text())
    if facts != params['native_facts']:
        raise em.Refusal('BACKEND_NATIVE_FACTS_DOCUMENT_CHANGED', str(path))
    expected = params.get('native_facts_sha256')
    if expected is not None and expected != snap.sha(path):
        raise em.Refusal('BACKEND_NATIVE_FACTS_DOCUMENT_CHANGED', str(path))
    extra['native_facts.json'] = path
    return facts


def prepare(request):
    from execution_step_protocol import PreparedStep
    sid = str(request.step_id)
    if sid not in ROWS:
        raise em.Refusal('BACKEND_UNKNOWN_STEP', sid)
    project = Path(request.project).absolute()
    params = dict(request.parameters)
    head = subprocess.check_output(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], text=True).strip()
    if request.source_sha != head:
        raise em.Refusal('BACKEND_SOURCE_COMMIT_CHANGED', request.source_sha)
    if subprocess.run(['git', '-C', str(HERE), 'diff', '--quiet', 'HEAD', '--', '.'], check=False).returncode:
        raise em.Refusal('BACKEND_SOURCE_DIRTY', sid)
    files = _source_files(request)
    na = _declared_na(sid, project, params)
    if na:
        # Factual HARDMACRO inapplicability does not require native admission.
        return PreparedStep(None, None, None, 'declared_inapplicable',
                            'Current owner declaration states HARDMACRO', na)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', str(params.get('top', ''))):
        raise em.Refusal('BACKEND_TOP_UNSTATED', sid)
    pdk = Path(params.get('pdk_root', '')).absolute()
    if not params.get('pdk_root') or not params.get('pdk_name'):
        raise em.Refusal('BACKEND_PDK_UNSTATED', sid)
    record = Path(request.record).absolute()
    if record.is_relative_to(project) or record.is_relative_to(pdk) or record.exists() or record.is_symlink():
        raise em.Refusal('BACKEND_REQUEST_NAMESPACE', str(record))
    extra = dict(params.get('extra_inputs') or {})
    # Native facts and lease are INPUT, not unbound trust in a parameter dict.
    fact = _native_facts(params, extra)
    lease = Path(request.lease).absolute()
    if lease.is_symlink() or not lease.is_dir():
        raise em.Refusal('BACKEND_LEASE_DIRECTORY_REQUIRED', str(lease))
    extra['lease.json'] = str(lease / 'lease.json')
    for path in extra.values():
        p = Path(path).absolute()
        if record == p or (p.is_dir() and record.is_relative_to(p)):
            raise em.Refusal('BACKEND_REQUEST_NAMESPACE', str(record))
    if record.is_relative_to(lease):
        raise em.Refusal('BACKEND_REQUEST_NAMESPACE', str(record))
    roots, named_roots = _project_roots(project, params, ROWS[sid]['canonical_row'])
    for name, path in named_roots.items():
        if name in extra and Path(extra[name]).absolute() != path.absolute():
            raise em.Refusal('BACKEND_INPUT_ROOT_ALIAS_COLLISION', name)
        extra[name] = path
    state = params.get('state_in')
    if state:
        selected = Path(state).absolute()
        if not selected.is_relative_to(project) and not selected.is_relative_to(pdk):
            # External predecessor states must be named complete INPUT.
            if not any(selected == Path(x).absolute() or
                       (Path(x).is_dir() and selected.is_relative_to(Path(x).absolute()))
                       for x in extra.values()):
                raise em.Refusal('BACKEND_STATE_INPUT_UNBOUND', str(selected))
    image = str(fact.get('image_id', ''))
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise em.Refusal('BACKEND_IMMUTABLE_IMAGE_REQUIRED', sid)
    # F1 alone issues/enforces real admission, parent PID/ticks and RAM in the
    # lease directory. The domain never invents admission fields in tool facts.
    inputs, lexical = _capture(project, pdk, extra, roots)
    cpus, ram = params.get('cpus'), params.get('ram_mb')
    timeout = params.get('timeout_s')
    if type(cpus) is not int or cpus < 1 or type(ram) is not int or ram < 1:
        raise em.Refusal('BACKEND_NATIVE_BUDGET_UNSTATED', sid)
    if type(timeout) not in (int, float) or not 0 < timeout <= 7200:
        raise em.Refusal('BACKEND_NATIVE_DEADLINE_UNSTATED', sid)
    from execution_step_protocol import json_parameters
    adoptions = adoption_paths(sid, project)
    spec = {'schema': 'execution_backend_request/1', 'step_id': sid,
            'parameters': json_parameters(params), 'project': str(project), 'pdk_root': str(pdk),
            'lexical': lexical, 'source_sha': request.source_sha,
            'project_input_roots': roots, 'adoption_paths': adoptions,
            'source_files': files, 'extra_inputs': json_parameters(extra), 'lease_directory': str(lease),
            'lease_sha256': snap.sha(lease / 'lease.json'),
            'native_facts_sha256': snap.sha(Path(params['native_facts_file'])),
            'policy_sha256': snap.sha(POLICY), 'image_id': image}
    snap.dump(record, spec)
    inputs['request.json'] = record
    required = tuple(dict.fromkeys(('backend_native_substance', 'backend_canonical') +
                     tuple(ROWS[sid]['portfolio_policy']['mandatory_gate_programs']) +
                     (('hold_area_budget_check',) if sid == '20' else ())))
    objective = params.get('objective')
    if not isinstance(objective, dict) or not objective:
        raise em.Refusal('BACKEND_OBJECTIVE_UNSTATED', sid)
    context = BackendContext(sid, request.source_sha, inputs, objective, required,
                             'librelane', project, pdk, extra, lexical, record, files, roots, adoptions)
    context.binding()
    registry = em.Registry()
    # The shared boundary is required even when source is staged without F1.
    available = (HERE / 'execution_resource_lease.py').is_file()
    contracts = {k: ('backend_result.json',) for k in
                 ROWS[sid]['portfolio_policy']['required_output_contract']}
    route_tool, route_families = _provider_route(sid, params)
    registry.register(em.Adapter(
        arm_id='backend_' + sid.replace('.', '_'), tool_id=route_tool,
        step_id=sid, source_sha=request.source_sha, source_files=files,
        tool_version=image, engine_families=route_families,
        components=(em.Component('backend_native', (str(Path(sys.executable).resolve()),
                    str(HERE / 'execution_backend_worker.py'), '--inputs', '{inputs}',
                    '--outputs', '{outputs}'), timeout),),
        validate=validate, required_outputs=('backend_result.json',),
        output_contract=contracts, objective=objective, qualified=True,
        qualification_evidence='source-bound backend producer; native qualification is per issued result',
        available=available,
        availability_reason='' if available else 'F1_NATIVE_BOUNDARY_UNAVAILABLE',
        cpus=cpus, ram_mb=ram,
        own_no_tool_reason=_complete_row_orchestration_reason(sid, params)))
    return PreparedStep(context, registry, consume, adoption_paths=adoptions)


def _provider_route(sid, params):
    """Attribute direct tools only where the exact row calls them as producers."""
    if sid == '29':
        return 'iverilog', ('iverilog',)
    if sid == '31':
        # One backend-owned row runs all complementary checker axes.
        return 'vibeic', ('klayout', 'magic', 'netgen')
    if sid == '33':
        return 'opensta', ('opensta',)
    if sid == '37.3':
        return 'klayout', ('klayout',)
    if sid == '30':
        supplied = _step30_simulators(params)
        if (not isinstance(supplied, (list, tuple)) or not supplied
                or any(x not in ('ngspice', 'xyce') for x in supplied)
                or len(set(supplied)) != len(supplied)):
            raise em.Refusal('BACKEND_SPICE_PROVIDER_UNSTATED', repr(supplied))
        engines = tuple(supplied)
        # OpenSTA supplies the common path extraction; requested simulators
        # are correlated instruments inside this single complete producer.
        return engines[0] if len(engines) == 1 else 'vibeic', ('opensta',) + engines
    return 'librelane', _engine_families(sid)


def _complete_row_orchestration_reason(sid, params):
    """Explain when this adapter orchestrates tools that are not whole-row alternatives."""
    if sid == '31':
        return ('The single complete Step31 producer runs DRC, LVS, ERC, and PERC as '
                'mandatory complementary checks; those native checkers are not interchangeable full-row producers.')
    if sid == '30' and len(_step30_simulators(params)) > 1:
        return ('The supplied Step30 simulators are correlated instruments in one complete '
                'producer; individual instruments do not replace the requested joint correlation result.')
    return None


def _step30_simulators(params):
    """Return the declared complementary instruments for the complete Step30 producer."""
    import path_spice_tool as spice
    return params.get('simulators', tuple(spice.SIMULATORS))


def _engine_families(sid):
    # Families here describe engines reached by this one producer, not arms.
    return {'31': ('klayout', 'magic', 'netgen'), '30': ('opensta', 'ngspice'),
            '29': ('iverilog',), '37': ('magic', 'klayout'),
            '37.3': ('klayout',), '26.5ic': ('klayout',),
            '34': ('openroad', 'klayout')}.get(sid, ('openroad',))


def validate(outputs, binding):
    outputs = Path(outputs)
    measured_fail = False
    try:
        result = json.loads((outputs / 'backend_result.json').read_text())
        if result['binding'] != binding or result['step_id'] != binding['step_id']:
            raise em.Refusal('BACKEND_RESULT_UNBOUND', binding['step_id'])
        measured_fail = result.get('producer_verdict') == 'FAIL' or 'FAIL' in result.get('gates', {}).values()
        files = result['outputs']
        if not files or not result['native_receipts']:
            raise em.Refusal('BACKEND_NATIVE_OUTPUT_UNMEASURED', binding['step_id'])
        for name, expected in files.items():
            path = outputs / snap.relative(name)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(outputs.resolve()):
                raise em.Refusal('BACKEND_OUTPUT_UNSAFE', name)
            if not path.stat().st_size or snap.sha(path) != expected:
                raise em.Refusal('BACKEND_OUTPUT_CHANGED', name)
        commands = result.get('native_commands') or []
        runs = [c for c in commands if c.get('submitted_argv', [None, None])[1:2] == ['run']]
        if not runs:
            raise em.Refusal('BACKEND_NATIVE_EXECUTION_UNMEASURED', binding['step_id'])
        for command in runs:
            argv = command['submitted_argv']
            if (command.get('rc') != 0 or not command.get('ended_ns') or
                    not command.get('worker_pid') or not re.fullmatch('[0-9a-f]{64}', command.get('cid', ''))):
                raise em.Refusal('BACKEND_NATIVE_TERMINAL_UNMEASURED', binding['step_id'])
            if '--entrypoint' in argv or '--network' not in argv or argv[argv.index('--network') + 1] != 'none':
                raise em.Refusal('BACKEND_NATIVE_ADMISSION_UNSAFE', str(argv[:2]))
            if not any(a in argv for a in ('--user', '-u')) or result['image_id'] not in argv:
                raise em.Refusal('BACKEND_NATIVE_IDENTITY_UNBOUND', binding['step_id'])
        hashes = dict(files, **{'backend_result.json': snap.sha(outputs / 'backend_result.json')})
        gates = result['gates']
        status = 'FAIL' if 'FAIL' in gates.values() else (
            'PASS' if result['producer_verdict'] == 'PASS' and all(
                gates.get(k) == 'PASS' for k in binding['required_gates']) else 'NOT_MEASURED')
        return em.Evidence(binding, status, gates, hashes, detail=result.get('detail', ''))
    except (OSError, ValueError, KeyError, TypeError, em.Refusal) as exc:
        return em.Evidence(binding, 'FAIL' if measured_fail else 'NOT_MEASURED', {}, {}, detail=str(exc))


def consume(project, context, controller, run, adopted):
    from execution_backend_consumer import import_selected
    return import_selected(Path(project), context, controller, Path(run), adopted)

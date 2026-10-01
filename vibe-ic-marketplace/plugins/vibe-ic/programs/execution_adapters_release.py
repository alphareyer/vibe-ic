"""F4 module-only release factories. F1 owns registration and runner hooks.

Document/package production and qualification never imply physical signoff.
Physical rows import current external receipts; no software physical producer
is registered. Complete lexical input populations are checked at each boundary.
"""
from __future__ import annotations
from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
from execution_step_protocol import PopulationContext, imported_result, input_population, json_parameters
from execution_release_rows import ROWS

STEP_IDS = tuple(ROWS)
PROGRAMS = Path(__file__).resolve().parent
EXTERNAL_IDS = ('39', '40', '41', '42', '43', '44')
WORKER = PROGRAMS / 'execution_release_worker.py'
DECLARATION = 'input/submission_template/tapeout_declaration.json'


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def alternatives(pattern):
    return pattern.split(' OR ')


def matches(name, pattern):
    return any(fnmatch.fnmatchcase(name, p) for p in alternatives(pattern))


def output_patterns(step):
    row = ROWS[step]['canonical']
    patterns = list(row.get('required_outputs', []))
    # Include producer and complementary gate reports. They are downstream
    # products of this invocation, not its inputs. All consumed reports outside
    # these exact destinations remain in the frozen population.
    patterns += gate_outputs(step)
    if step == '38':
        patterns += ['phase3/stage4/foundry_handoff/**', 'reports/phase3/foundry_handoff_audit.json']
    if step == '14':
        patterns.remove('phase2/stage2/synth/netlist.v')  # consumed upstream
    if step == '37.5ip':
        patterns += ['reports/phase3/ip_release_docs_gen.json', 'reports/phase3/ip_release_docs_context.json',
                     'reports/phase3/release_native_engines.json', 'reports/phase3/native-release-logs/**']
    return patterns


def gate_clauses(step):
    """Keep every canonical clause, including its advisory/condition meaning."""
    def visit(node):
        if not isinstance(node, dict):
            return
        for kind, value in node.items():
            if kind in ('all_of', 'any_of'):
                for child in value:
                    yield from visit(child)
            elif kind in ('program_exit_zero', 'advisory_program_exit_zero', 'optional_program_exit_zero'):
                record = {'command': value} if isinstance(value, str) else dict(value)
                record['classification'] = kind
                yield record
    return tuple(visit(ROWS[step]['canonical'].get('gate', {})))


def gate_outputs(step):
    import shlex
    paths = []
    for clause in gate_clauses(step):
        args = shlex.split(clause['command'])
        for flag in ('--json', '--out-dir'):
            if flag in args:
                dest = args[args.index(flag) + 1]
                paths.append(dest + '/**' if flag == '--out-dir' else dest)
    return paths


def population(project, excluded=(), selected_roots=None):
    """Lexical path + resolved identity + bytes, including empty directories.

    File aliases inside the project are supported and bound. Directory aliases,
    escaped targets, devices and broken links refuse before snapshot/import.
    """
    project = Path(project).resolve(strict=True)
    result = {}
    if selected_roots is not None:
        for root in selected_roots:
            if root != '.' and not (project / root).exists() and not (project / root).is_symlink():
                result[root] = {'kind': 'missing'}
    for directory, dirs, files in os.walk(project, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            rel = path.relative_to(project).as_posix()
            if selected_roots is not None and not any(root == '.' or rel == root or
                    rel.startswith(root + '/') or root.startswith(rel + '/') for root in selected_roots):
                if name in dirs:
                    dirs.remove(name)
                continue
            if any(matches(rel, pat) for pat in excluded):
                if name in dirs:
                    dirs.remove(name)
                continue
            try:
                target = path.resolve(strict=True)
            except OSError as exc:
                raise em.Refusal('RELEASE_INPUT_UNRESOLVED', rel) from exc
            if not target.is_relative_to(project):
                raise em.Refusal('RELEASE_INPUT_ESCAPE', rel)
            if path.is_symlink() and target.is_dir():
                raise em.Refusal('RELEASE_DIRECTORY_ALIAS', rel)
            if target.is_dir():
                # Output-only directory creation is not an upstream change.
                if not any(p.startswith(rel + '/') for pat in excluded for p in alternatives(pat)):
                    result[rel + '/'] = {'kind': 'directory'}
            elif target.is_file():
                result[rel] = {'kind': 'file', 'resolved': target.relative_to(project).as_posix(),
                               'sha256': em.digest(target), 'bytes': target.stat().st_size}
            else:
                raise em.Refusal('RELEASE_INPUT_NOT_REGULAR', rel)
    return result


def required_source_files(parameters=None):
    """F1 calls this before constructing the common immutable source manifest.

    This inventory is never silently unioned into a typed request. F1 owns
    the complete common manifest supplied unchanged to context and every arm.
    """
    # Bare sibling imports are pervasive. Bind the complete production-module
    # population rather than guessing a partial transitive dependency list.
    actual = {str(p.resolve()): em.digest(p) for p in PROGRAMS.glob('*.py')}
    actual[str(Path(sys.executable).resolve())] = em.digest(Path(sys.executable).resolve())
    git_binary = shutil.which('git')
    if git_binary:
        actual[str(Path(git_binary).resolve())] = em.digest(Path(git_binary).resolve())
    for relative in ('flow/phase1_phase2_phase3.yaml', 'benchmark/CAPTURE_ROUTING.json',
                     'programs/data/execution_modes_portfolio.json'):
        path = PROGRAMS.parent / relative
        actual[str(path.resolve(strict=True))] = em.digest(path)
    if parameters and parameters.get('native_admission'):
        admission = json.loads(Path(parameters['native_admission']).read_text())
        for name, digest in admission.get('tool_binaries', {}).items():
            path = Path(name)
            if not path.is_absolute() or path.is_symlink() or not path.is_file() or em.digest(path) != digest:
                raise em.Refusal('RELEASE_NATIVE_BINARY_CHANGED', str(name))
            actual[str(path)] = digest
    return actual


def source_binding(request):
    required = required_source_files(request.parameters)
    if not required.items() <= request.source_files.items():
        raise em.Refusal('RELEASE_SOURCE_MANIFEST_INCOMPLETE', json.dumps(sorted(set(required) - set(request.source_files))))
    for name, expected in request.source_files.items():
        p = Path(name)
        if not p.is_file() or p.is_symlink() or em.digest(p) != expected:
            raise em.Refusal('RELEASE_SOURCE_CHANGED', str(name))
    return dict(request.source_files)


class ReleaseContext(PopulationContext):
    def __init__(self, request):
        project = Path(request.project).resolve(strict=True)
        supplied_roots = request.parameters.get('input_roots')
        if not isinstance(supplied_roots, dict) or not supplied_roots:
            raise em.Refusal('RELEASE_STEP_INPUT_ROOTS_REQUIRED', request.step_id)
        selected_roots = []
        bound_roots = {}
        native_facts_file = request.parameters.get('native_facts_file')
        native_facts = request.parameters.get('native_facts')
        native_facts_sha256 = request.parameters.get('native_facts_sha256')
        has_native_facts = any(value is not None for value in
                                (native_facts_file, native_facts, native_facts_sha256))
        if has_native_facts:
            if (not isinstance(native_facts, dict) or not isinstance(native_facts_file, Path)
                    or not isinstance(native_facts_sha256, str)
                    or not re.fullmatch(r'[0-9a-f]{64}', native_facts_sha256)
                    or native_facts_file.is_symlink() or not native_facts_file.is_file()
                    or em.digest(native_facts_file) != native_facts_sha256):
                raise em.Refusal('RELEASE_NATIVE_FACTS_UNBOUND', str(native_facts_file))
            try:
                if json.loads(native_facts_file.read_text()) != native_facts:
                    raise ValueError('typed native facts differ from hashed file')
            except (OSError, ValueError, TypeError) as exc:
                raise em.Refusal('RELEASE_NATIVE_FACTS_UNBOUND', str(exc)) from exc
        for name, value in supplied_roots.items():
            if not isinstance(name, str) or not isinstance(value, (str, Path)):
                raise em.Refusal('RELEASE_STEP_INPUT_ROOT_INVALID', str(name))
            path = Path(value)
            if not path.is_absolute() or '..' in path.parts:
                raise em.Refusal('RELEASE_STEP_INPUT_ROOT_ESCAPE', str(path))
            if path.is_relative_to(project):
                try:
                    resolved = path.resolve(strict=False)
                except (OSError, RuntimeError) as exc:
                    raise em.Refusal('RELEASE_STEP_INPUT_ROOT_INVALID', str(path)) from exc
                if not resolved.is_relative_to(project):
                    raise em.Refusal('RELEASE_STEP_INPUT_ROOT_ESCAPE', str(path))
                if not path.exists():
                    ancestor = path
                    while not ancestor.exists() and not ancestor.is_symlink():
                        ancestor = ancestor.parent
                    try:
                        resolved_ancestor = ancestor.resolve(strict=True)
                    except (OSError, RuntimeError) as exc:
                        raise em.Refusal('RELEASE_STEP_INPUT_ROOT_INVALID', str(path)) from exc
                    if (not resolved_ancestor.is_relative_to(project) or not ancestor.is_dir()):
                        raise em.Refusal('RELEASE_STEP_INPUT_ROOT_INVALID', str(path))
            else:
                try:
                    path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise em.Refusal('RELEASE_STEP_INPUT_ROOT_INVALID', str(path)) from exc
                bound_roots['controls/' + name] = path
                continue
            selected_roots.append(path.relative_to(project).as_posix())
        excluded = [] if request.step_id in EXTERNAL_IDS else output_patterns(request.step_id)
        for control in (Path(request.lease), Path(request.record)):
            if control.resolve().is_relative_to(project):
                rel = control.resolve().relative_to(project).as_posix()
                if not rel.startswith('reports/execution/'):
                    raise em.Refusal('RELEASE_CONTROL_OVERLAPS_INPUT', rel)
                excluded.extend([rel, rel + '/**'])
        frozen = population(project, excluded, selected_roots)
        declaration_path = request.parameters.get('declaration')
        if not isinstance(declaration_path, Path) or not declaration_path.is_absolute():
            raise em.Refusal('RELEASE_DECLARATION_INPUT_UNBOUND', str(declaration_path))
        if declaration_path.is_relative_to(project):
            declaration_rel = declaration_path.relative_to(project).as_posix()
            if declaration_rel not in frozen or frozen[declaration_rel].get('kind') != 'file':
                raise em.Refusal('RELEASE_DECLARATION_INPUT_UNBOUND', declaration_rel)
        elif 'controls/declaration' not in bound_roots or bound_roots['controls/declaration'] != declaration_path:
            raise em.Refusal('RELEASE_DECLARATION_INPUT_UNBOUND', str(declaration_path))
        roots = {'project/' + name: project / name
                 for name, entry in frozen.items() if entry['kind'] == 'file'}
        roots.update(bound_roots)
        inputs, lexical_population = input_population(roots)
        lease_manifest = Path(request.lease) / 'lease.json'
        if not Path(request.lease).is_dir() or lease_manifest.is_symlink() or not lease_manifest.is_file():
            raise em.Refusal('RELEASE_LEASE_DIRECTORY_REQUIRED', str(request.lease))
        inputs['lease.json'] = lease_manifest
        if request.parameters.get('native_admission'):
            admission = Path(request.parameters['native_admission'])
            if not admission.is_file() or admission.is_symlink():
                raise em.Refusal('RELEASE_NATIVE_ADMISSION_MISSING', str(admission))
            inputs['native-admission.json'] = admission.resolve(strict=True)
        if not inputs:
            raise em.Refusal('RELEASE_INPUT_EMPTY', str(project))
        sources = source_binding(request)
        # Worker metadata is a real, source-bound frozen input, not mutable
        # output metadata. Allocate it outside the source/project population.
        metadata_root = project.parent / '.release-inputs'
        metadata_root.mkdir(parents=True, exist_ok=True)
        metadata = metadata_root / ('release-request-' + uuid.uuid4().hex + '.json')
        payload = {'step_id': request.step_id, 'source_sha': request.source_sha,
                   'parameters': json_parameters(request.parameters), 'population': frozen,
                   'project_name': project.name, 'sources': sources}
        write(metadata, payload)
        inputs['release-request.json'] = metadata
        required = tuple(dict.fromkeys(ROWS[request.step_id]['policy']['mandatory_gate_programs']))
        super().__init__(request.step_id, request.source_sha, inputs,
                         {'metric': 'release_artifact_integrity', 'direction': 'max',
                          'identities': json_parameters(request.parameters)},
                         required, 'direct', roots=roots, population=lexical_population, source_files=sources)
        object.__setattr__(self, 'project', project)
        object.__setattr__(self, 'excluded', tuple(excluded))
        object.__setattr__(self, 'selected_roots', tuple(selected_roots))
        object.__setattr__(self, 'population_at_prepare', frozen)
        object.__setattr__(self, 'sources_at_prepare', sources)
        object.__setattr__(self, 'metadata', metadata)
        object.__setattr__(self, 'control_inputs_at_prepare',
                           {name: em.digest(path) for name, path in inputs.items() if not name.startswith('project/')})
        object.__setattr__(self, 'parameters_at_prepare', json.dumps(json_parameters(request.parameters), sort_keys=True))

    def binding(self):
        for name, expected in self.control_inputs_at_prepare.items():
            path = Path(self.inputs[name])
            if path.is_symlink() or not path.is_file() or em.digest(path) != expected:
                raise em.Refusal('RELEASE_CONTROL_INPUT_CHANGED', name)
        now = population(self.project, self.excluded, self.selected_roots)
        if now != self.population_at_prepare:
            raise em.Refusal('RELEASE_INPUT_POPULATION_CHANGED', self.step_id)
        if {str(p.resolve()): em.digest(p) for p in PROGRAMS.glob('*.py')} != {
                p: d for p, d in self.sources_at_prepare.items() if Path(p).parent == PROGRAMS}:
            raise em.Refusal('RELEASE_SOURCE_POPULATION_CHANGED', self.step_id)
        for name, expected in self.sources_at_prepare.items():
            p = Path(name)
            if not p.is_file() or p.is_symlink() or em.digest(p) != expected:
                raise em.Refusal('RELEASE_SOURCE_CHANGED', name)
        result = super().binding()
        result['release_population'] = now
        # Controller passes this binding in a single environment value. Keep
        # the complete source map in the frozen request and Adapter identity,
        # while binding its canonical digest here (Linux caps one env string).
        result['release_sources_sha256'] = em._hash(self.sources_at_prepare)
        return result


def declared_identities(request):
    values = request.parameters
    for key in ('top', 'pdk', 'route', 'image'):
        if not isinstance(values.get(key), str) or not values[key].strip():
            raise em.Refusal('RELEASE_IDENTITY_MISSING', key)
    if values['route'] not in ('IC', 'IP'):
        raise em.Refusal('RELEASE_ROUTE_INVALID', str(values['route']))
    declaration = values.get('declaration')
    supplied = values.get('declaration')
    if (not isinstance(declaration, Path) or not declaration.is_absolute()
            or not declaration.is_file()):
        raise em.Refusal('RELEASE_DECLARATION_NOT_REQUESTED', str(declaration))
    try:
        declaration.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise em.Refusal('RELEASE_DECLARATION_NOT_REQUESTED', str(declaration)) from exc
    project = Path(request.project).resolve(strict=True)
    roots = values.get('input_roots')
    if not isinstance(roots, dict) or roots.get('declaration') != declaration:
        if not declaration.is_relative_to(project):
            raise em.Refusal('RELEASE_DECLARATION_INPUT_UNBOUND', str(declaration))
    try:
        doc = json.loads(declaration.read_text())
        answers = doc['answers']
        provenance = doc['answer_provenance']['deliverable']
        if provenance.get('answered_by') != 'owner' or not provenance.get('citation'):
            raise ValueError('owner citation missing')
        deliverable = answers['deliverable']
        if deliverable not in ('DIE', 'HARDMACRO') or (values['route'] == 'IC') != (deliverable == 'DIE'):
            raise ValueError('route contradicts declared deliverable')
        if answers.get('top_cell') != values['top']:
            raise ValueError('declared top differs')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise em.Refusal('RELEASE_DECLARATION_INVALID', str(exc)) from exc
    digest = em.digest(declaration)
    if values.get('declaration_sha256') not in (None, digest):
        raise em.Refusal('RELEASE_DECLARATION_DIGEST_MISMATCH', str(declaration))
    return {'declaration': str(declaration), 'declaration_sha256': digest,
            'facts': {'deliverable': deliverable, 'top_cell': answers['top_cell'],
                      'answered_by': provenance['answered_by'], 'citation': provenance['citation'],
                      'route': values['route']}}


def classify(request):
    """Classify release declarations before constructing ReleaseContext."""
    from execution_step_protocol import PreparedStep
    if request.step_id not in STEP_IDS:
        raise em.Refusal('RELEASE_UNKNOWN_STEP', request.step_id)
    request.check_source()
    declaration = declared_identities(request)
    raw_receipts = request.parameters.get('external_receipts', [])
    if not isinstance(raw_receipts, list):
        raise em.Refusal('RELEASE_EXTERNAL_POPULATION_INVALID', request.step_id)
    facts = dict(declaration['facts'], step_id=request.step_id,
                 source_sha=request.source_sha,
                 source_files_sha256=em._hash(dict(request.source_files)),
                 external_receipt_count=len(raw_receipts))
    handoff = {'declaration': declaration['declaration'],
               'declaration_sha256': declaration['declaration_sha256'],
               'facts': facts}
    if request.step_id == '37.5ic' and request.parameters['route'] == 'IP':
        return PreparedStep(None, None, None, 'declared_inapplicable',
                            'Owner-cited HARDMACRO declaration; IC-only precheck', handoff)
    if request.step_id in EXTERNAL_IDS:
        return PreparedStep(None, None, None, 'external_handoff',
                            'Owner-declared external handoff; live prepare validates receipts', handoff)
    return PreparedStep(None, None, None, 'execute',
                        'Source-bound release declaration preflight; live prepare is required',
                        {'facts': facts})


def semantic_consume(project, context, controller, run, adopted):
    """Transactionally import this live issuer's exact selected generation.

    Reconstruct an independent validation project from current upstream inputs
    plus selected bytes, ask the real existing consumers again, then publish.
    Roll back all canonical writes if any binding/gate/import boundary refuses.
    """
    from execution_release_worker import validate_products

    def refusal_for_consumer(result, stage):
        measured_fail = (result.get('design_verdict') == 'FAIL' or any(
            row.get('classification') != 'advisory_program_exit_zero'
            and row.get('verdict') == 'FAIL'
            for row in result.get('gate_records', []) if isinstance(row, dict)))
        code = 'GATE_FAIL' if measured_fail else 'RELEASE_' + stage + '_CONSUMER_REFUSED'
        raise em.Refusal(code, json.dumps(result, sort_keys=True))

    project = Path(project).resolve()
    run = Path(run).resolve()
    if project != context.project or adopted.get('status') != 'ADOPTED':
        raise em.Refusal('RELEASE_ADOPTION_INVALID', str(project))
    disk = json.loads((run / 'adoption.json').read_text())
    if disk != adopted:
        raise em.Refusal('RELEASE_WRONG_ADOPTION', str(run))
    generation = adopted['selected_generation']
    if generation.get('binding') != context.binding():
        raise em.Refusal('RELEASE_SELECTED_BINDING', context.step_id)
    # Prove live run/arm/issuer authority as well as generation authority.
    plan = json.loads((run / 'plan.json').read_text())
    receipt = json.loads((run / adopted['selected'] / 'receipt.json').read_text())
    arm = next(a for a in controller.registry.adapters(context.step_id) if a.arm_id == adopted['selected'])
    controller._execution_authority(run, plan, receipt, arm)
    controller._current_admission(context, plan, arm)
    controller._eligible(receipt, context, arm)
    selected_current(controller, generation)
    before = context.binding()
    directory = Path(generation['directory'])
    report = json.loads((directory / 'release-evidence.json').read_text())
    if report['source_sha'] != context.source_sha or report['step_id'] != context.step_id:
        raise em.Refusal('RELEASE_WRONG_PRODUCT', context.step_id)
    with tempfile.TemporaryDirectory(prefix='release-consumer-', dir=run.parent) as tmp:
        snapshot = Path(tmp) / project.name
        snapshot.mkdir()
        for name, path in context.inputs.items():
            if name.startswith('project/'):
                target = snapshot / name.removeprefix('project/')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        selected = {}
        for name, expected in report['products'].items():
            source = directory / 'artifacts' / name
            if em.digest(source) != expected or source.is_symlink():
                raise em.Refusal('RELEASE_SELECTED_PRODUCT_CHANGED', name)
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            selected[name] = source
        current = validate_products(context.step_id, snapshot, dict(json.loads(context.parameters_at_prepare), source_sha=context.source_sha))
        if current['qualification'] != 'PASS':
            refusal_for_consumer(current, 'SELECTED')
        if context.binding() != before:
            raise em.Refusal('RELEASE_INPUT_CHANGED_DURING_CONSUMER', context.step_id)
        selected_current(controller, generation)
        backups, created_dirs = {}, []
        try:
            for name, source in selected.items():
                target = project / name
                # Resolve BEFORE normalization and never overwrite an alias.
                if target.is_symlink() or not target.resolve().is_relative_to(project):
                    raise em.Refusal('RELEASE_DESTINATION_UNSAFE', name)
                if target.exists() and not target.is_file():
                    raise em.Refusal('RELEASE_DESTINATION_NOT_REGULAR', name)
                backups[target] = target.read_bytes() if target.exists() else None
                parent = target.parent
                while not parent.exists():
                    created_dirs.append(parent)
                    parent = parent.parent
                target.parent.mkdir(parents=True, exist_ok=True)
                from _atomic_artefact import write_bytes
                write_bytes(target, source.read_bytes())
            if context.binding() != before:
                raise em.Refusal('RELEASE_INPUT_CHANGED_DURING_IMPORT', context.step_id)
            selected_current(controller, generation)
            # Read the installed canonical bytes back into a fresh project at
            # the same canonical relative paths. Original CLI consumers may
            # write secondary reports even with --json redirected, so they
            # must never mutate upstream or escape the import journal.
            installed = Path(tmp) / 'installed' / project.name
            installed.mkdir(parents=True)
            for name, path in context.inputs.items():
                if name.startswith('project/'):
                    target = installed / name.removeprefix('project/')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
            for name, expected in report['products'].items():
                canonical = project / em._relative(name)
                if canonical.is_symlink() or em.digest(canonical) != expected:
                    raise em.Refusal('RELEASE_INSTALLED_BYTES_CHANGED', name)
                target = installed / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(canonical, target)
            final = validate_products(context.step_id, installed, dict(json.loads(context.parameters_at_prepare), source_sha=context.source_sha), write_reports=False)
            if final['qualification'] != 'PASS':
                refusal_for_consumer(final, 'INSTALLED')
            if context.binding() != before:
                raise em.Refusal('RELEASE_INPUT_CHANGED_AFTER_CONSUMER', context.step_id)
            selected_current(controller, generation)
            primary = {str(i) + ':' + r['program']: r['verdict']
                       for i, r in enumerate(final['gate_records'])
                       if r['classification'] != 'advisory_program_exit_zero'
                       and r['verdict'] != 'NOT_APPLICABLE'}
            return imported_result(step_id=context.step_id, source_sha=context.source_sha,
                                   selected_generation=generation, binding=before,
                                   copied=report['products'], primary_gates=primary,
                                   design_verdict=final['design_verdict'],
                                   consumer_detail={'status': 'CONSUMED', 'consumer': final,
                                                    'qualification': final['qualification']})
        except BaseException:
            from _atomic_artefact import write_bytes
            for target, content in backups.items():
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    write_bytes(target, content)
            for path in sorted(set(created_dirs), key=lambda p: len(p.parts), reverse=True):
                try:
                    path.rmdir()
                except OSError:
                    pass
            raise


def selected_current(controller, generation):
    controller._generation_current(generation)
    root = Path(generation['directory'])
    actual = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise em.Refusal('RELEASE_SELECTED_ALIAS', str(path))
        if path.is_file() and path != root / 'manifest.json':
            actual[path.relative_to(root).as_posix()] = em.digest(path)
    if actual != generation['outputs']:
        raise em.Refusal('RELEASE_SELECTED_POPULATION_CHANGED', str(root))


def prepare(request):
    from execution_step_protocol import PreparedStep
    if request.step_id not in STEP_IDS:
        raise em.Refusal('RELEASE_UNKNOWN_STEP', request.step_id)
    declaration = declared_identities(request)
    context = ReleaseContext(request)
    binding = context.binding()
    if request.step_id == '37.5ic' and request.parameters['route'] == 'IP':
        return PreparedStep(context, None, None, 'declared_inapplicable',
                            'Owner-cited HARDMACRO declaration; IC-only precheck', dict(declaration, binding=binding))
    if request.step_id in EXTERNAL_IDS:
        from execution_release_worker import external_handoff
        handoff = external_handoff(context)
        handoff.update(declaration)
        context.binding()
        return PreparedStep(context, None, None, 'external_handoff', handoff['reason'], handoff)
    if request.step_id == '37.5ip' and not request.parameters.get('native_admission'):
        raise em.Refusal('RELEASE_NATIVE_ADMISSION_REQUIRED', json.dumps(
            {'binding_sha256': em._hash(binding), 'required_outputs': ROWS[request.step_id]['canonical']['required_outputs'],
             'native_producer': 'execution_release_worker.native_hardmacro', 'design_verdict': 'NOT_MEASURED',
             'missing_obligations': ['actual native engine receipts', 'characterized Liberty', 'native admission'],
             'all_routes': True}, sort_keys=True))
    registry = em.Registry()
    required = ('release-evidence.json',)

    def validate(outputs, bound):
        from execution_release_worker import validate_products
        path = Path(outputs) / 'release-evidence.json'
        if not path.is_file() or path.is_symlink():
            return em.Evidence(bound, 'NOT_MEASURED', {}, {}, detail='Release worker did not produce substantive evidence')
        report = json.loads(path.read_text())
        hashes = {'release-evidence.json': em.digest(path)}
        for name, expected in report.get('products', {}).items():
            rel = 'artifacts/' + name
            file = Path(outputs) / rel
            if not file.resolve().is_relative_to(Path(outputs).resolve()) or file.is_symlink() or not file.is_file() or em.digest(file) != expected:
                return em.Evidence(bound, 'FAIL', {}, {}, detail='Release product identity changed: ' + name)
            hashes[rel] = expected
        actual_population = {p.relative_to(Path(outputs) / 'artifacts').as_posix(): em.digest(p)
                             for p in (Path(outputs) / 'artifacts').rglob('*') if p.is_file()}
        valid = report.get('qualification') == 'PASS' and bool(report.get('products')) and report.get('source_sha') == context.source_sha and report.get('step_id') == context.step_id and actual_population == report.get('products')
        semantic = None
        if valid:
            with tempfile.TemporaryDirectory(prefix='release-validation-', dir=Path(outputs).parent) as tmp:
                snapshot = Path(tmp) / context.project.name
                snapshot.mkdir()
                for name, path in context.inputs.items():
                    if name.startswith('project/'):
                        target = snapshot / name.removeprefix('project/')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, target)
                for name in report['products']:
                    target = snapshot / em._relative(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(Path(outputs) / 'artifacts' / name, target)
                semantic = validate_products(context.step_id, snapshot, dict(json.loads(context.parameters_at_prepare), source_sha=context.source_sha))
                valid = semantic['qualification'] == 'PASS'
        verdict = 'PASS' if valid else (semantic['qualification'] if semantic else
                  ('FAIL' if report.get('qualification') == 'PASS' else report.get('qualification', 'NOT_MEASURED')))
        return em.Evidence(bound, verdict,
                           semantic['gates'] if semantic else report.get('gates', {}), hashes,
                           detail=json.dumps({'qualification': semantic['qualification'] if semantic else 'NOT_MEASURED',
                                              'design_verdict': semantic['design_verdict'] if semantic else report.get('design_verdict'),
                                              'step': context.step_id}, sort_keys=True))

    contract = {pattern: required for pattern in ROWS[request.step_id]['canonical']['required_outputs']}
    registry.register(em.Adapter(
        arm_id='release_' + request.step_id.replace('.', '_'), tool_id='magic' if request.step_id == '37.5ip' else 'vibeic', step_id=request.step_id,
        source_sha=request.source_sha, source_files=context.sources_at_prepare,
        tool_version='source-owned release composition / Python ' + sys.version.split()[0],
        engine_families=('magic', 'opensta') if request.step_id == '37.5ip' else ('release_composition',),
        components=(em.Component('release-worker', (sys.executable, str(WORKER), '{inputs}', '{outputs}'), 120),),
        validate=validate, required_outputs=required, output_contract=contract, objective=context.objective,
        own_no_tool_reason='Source-owned composition of the canonical release contract, all original checker obligations and provenance. External engines remain complementary upstream measurements; no tool substitutes this cross-producer release task.',
        cpus=1, ram_mb=512, qualified=request.step_id != '37.5ip',
        qualification_evidence=('NOT_MEASURED: native engine qualification and separate P0 admission are open' if request.step_id == '37.5ip'
            else 'Source-owned substantive validators; design qualification remains separately recorded')))
    # Exact fixed destinations plus complete generated directories cover every
    # possible canonical write, including selected complementary reports.
    paths = set()
    for pattern in output_patterns(request.step_id):
        for alternate in alternatives(pattern):
            prefix = alternate.split('*', 1)[0]
            paths.add(prefix.rstrip('/') if prefix.endswith('/') else
                      (str(Path(prefix).parent) if '*' in alternate else alternate))
    if request.step_id == '14':
        paths.add('phase2/stage2/synth/netlist.v')
    adoption_paths = tuple(sorted(p for p in paths if p and p != '.'))
    return PreparedStep(context, registry, semantic_consume, adoption_paths=adoption_paths)

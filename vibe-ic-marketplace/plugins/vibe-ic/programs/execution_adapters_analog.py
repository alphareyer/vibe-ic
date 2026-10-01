"""Source-bound A1–A9/M1–M4 factories for the F1 production front door.

Native execution is delegated to F1/P0's admitted, ephemeral run boundary.
Software availability, protocol eligibility and silicon verdict are separate.
No portfolio/runner/shared-supervisor state is changed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Mapping

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import execution_modes as em
from execution_step_protocol import (
    PreparedStep, StepRequest, imported_result, input_population, json_parameters,
)

PROGRAMS = Path(__file__).resolve().parent
WORKER = PROGRAMS / 'execution_analog_worker.py'
CONTRACT_FILE = PROGRAMS / 'data/execution_analog_contracts.json'
CONTRACTS = {r['step_id']: r for r in json.loads(CONTRACT_FILE.read_text())['steps']}
STEP_IDS = tuple(CONTRACTS)
ANALOG_STEPS = dict(zip(STEP_IDS[:9], (
    'A1_spec_extract', 'A2_topology_select', 'A3_netlist_gen', 'A4_corner_sweep',
    'A5_layout', 'A6_block_pv', 'A7_post_layout_resim', 'A8_hardmacro_gen', 'A9_hw_verify')))
PRODUCERS = {
    'A1': ('analog_a1_spec_emit.py',), 'A2': ('analog_a2_topology_emit.py',),
    'A3': ('analog_a3_netlist_emit.py',), 'A4': ('analog_real_corner_sweep.py',),
    'A5': ('analog_a5_layout_emit.py',), 'A6': ('analog_a6_native_pv.py',),
    'A7': ('analog_a7_post_layout_emit.py',),
    'A8': ('analog_a8_hardmacro_emit.py', 'analog_hardmacro_gds_emit.py'),
    'A9': ('analog_a9_cosim_emit.py',), 'M1': ('mixed_signal_top_lvs_run.py',),
    'M2': ('execution_analog_mixed_worker.py',),
    'M3': ('analog_a9_cosim_emit.py', 'execution_analog_mixed_worker.py'),
    'M4': ('execution_analog_mixed_worker.py',),
}
ENGINE_FAMILIES = {
    'A1': ('structured-spec-binding',), 'A2': ('topology-sizing',),
    'A3': ('spice-netlist-generation', 'ngspice'), 'A4': ('ngspice',),
    'A5': ('magic',), 'A6': ('klayout', 'magic', 'netgen'),
    'A7': ('magic', 'ngspice'), 'A8': ('magic', 'ngspice'),
    'A9': ('ngspice', 'iverilog'), 'M1': ('klayout', 'magic', 'netgen'),
    'M2': ('openroad', 'yosys'), 'M3': ('ngspice', 'iverilog', 'opensta'),
    'M4': ('klayout', 'magic', 'netgen'),
}


def producer_work_contract(step: str) -> dict:
    """Existing complete providers; component tools are complementary work.

    There is no source contract for a second interchangeable analog simulator.
    Keep the real family identity so the F1 scheduler can reject duplicate
    wrappers. Neither an availability payload nor ultra creates a provider.
    """
    entrypoints = {
        'A3': ['analog_a3_netlist_emit.main'], 'A4': ['analog_real_corner_sweep.run_block'],
        'A5': ['analog_a5_layout_emit.main'], 'A6': ['analog_a6_native_pv.run_block_pv'],
        'A7': ['analog_a7_post_layout_emit.run'],
        'A8': ['analog_a8_hardmacro_emit.main', 'analog_hardmacro_gds_emit.main'],
        'A9': ['analog_a9_cosim_emit.run'], 'M1': ['mixed_signal_top_lvs_run.run'],
        'M2': ['execution_analog_mixed_worker.power_domains'],
        'M3': ['analog_a9_cosim_emit.run', 'execution_analog_mixed_worker.interface_si'],
        'M4': ['execution_analog_mixed_worker.signoff'],
    }
    return dict(step_id=step, producer_sources=list(PRODUCERS[step]),
        entrypoints=entrypoints.get(step, [Path(PRODUCERS[step][0]).stem + '.main']),
        optional_entrypoints=['execution_analog_mixed_worker.characterize'] if step == 'A8' else [],
        engine_families=list(ENGINE_FAMILIES[step]),
        source_owned=step in ('A1', 'A2'), complete_source_provider_count=1,
        interchangeable_external_provider_contracts=[],
        work_kind='structured_generation' if step in ('A1', 'A2') else
            'corner_simulation' if step == 'A4' else 'complete_complementary_chain',
        all_current_canonical_obligations_required=True,
        distinct_provider_claim=False)


def _relative(name: str) -> str:
    return str(em._relative(name))


def write_json(path: Path, value: object) -> None:
    from _atomic_artefact import write_json as atomic
    atomic(path, value)


def typed_snapshot(value):
    """Preserve this domain entrypoint while using F1's immutable encoding."""
    return json_parameters({'value': value})['value']


def journal_paths(patterns: tuple[str, ...]) -> tuple[str, ...]:
    paths = []
    for pattern in patterns:
        parts = Path(pattern).parts
        wildcard = next((i for i, part in enumerate(parts) if any(c in part for c in '*?[')), None)
        paths.append(str(Path(*parts[:wildcard])) if wildcard is not None else pattern)
    if any(p == '.' or not p for p in paths):
        raise em.Refusal('ANALOG_JOURNAL_NAMESPACE_UNSAFE', repr(paths))
    unique = tuple(dict.fromkeys(paths))
    return tuple(p for p in unique if not any(
        p != parent and p.startswith(parent.rstrip('/') + '/') for parent in unique))


def adoption_destinations(project: Path, inputs: Mapping[str, Path], patterns: tuple[str, ...],
                          supplied=None) -> tuple[str, ...]:
    """Concrete producer destinations cannot journal a consumed upstream."""
    if supplied is not None:
        if not isinstance(supplied, list) or not supplied or any(not isinstance(p, str) for p in supplied):
            raise em.Refusal('ANALOG_CONCRETE_ADOPTION_PATHS_REQUIRED', repr(supplied))
        paths = tuple(dict.fromkeys(_relative(p) for p in supplied))
        if any(any(c in p for c in '*?[') or not covered(p, patterns) for p in paths):
            raise em.Refusal('ANALOG_ADOPTION_NAMESPACE_UNDECLARED', repr(paths))
    else:
        paths = journal_paths(patterns)
    for rel in paths:
        destination = project / rel
        if any(destination == p or destination.is_relative_to(p) or p.is_relative_to(destination)
               for p in inputs.values()):
            raise em.Refusal('ANALOG_CONCRETE_ADOPTION_PATHS_REQUIRED',
                rel + ' overlaps consumed INPUT; supply exact producer files/directories without removing INPUT')
        if any(rel != other and rel.startswith(other.rstrip('/') + '/') for other in paths):
            raise em.Refusal('ANALOG_ADOPTION_PATH_OVERLAP', rel)
    return paths


def contract_outputs(step: str, blocks: tuple[str, ...]) -> tuple[str, ...]:
    """Every canonical import, including provenance and raw gate reports."""
    names = {
        'A1': ('spec.json', 'spec_gap.json'),
        'A2': ('topology.md', 'topology.json', 'topology_gap.json'),
        'A3': ('*.sp', 'netlist_provenance.json', 'netlist_gap.json'),
        'A4': ('corner_results.json', 'corner_*.json', 'sizing_loop', 'sizing_loop/*'),
        'A5': ('layout.mag', '*.gds', 'layout_provenance.json', 'layout_gap.json',
               'layout_*', 'gencells', 'gencells/*'),
        'A6': ('drc*', 'lvs*', '*.lyrdb', 'comp.json', '*pv*.json'),
        'A7': ('pre_vs_post.json', 'a7_post_layout.json', 'post_layout*', 'rcx*', 'extraction*'),
    }
    result = []
    if step in names:
        for block in blocks:
            for root in ('phase3/analog', 'phase2/analog', 'phase1/analog'):
                result.extend(f'{root}/{block}/{name}' for name in names[step])
        if step == 'A7':
            result.extend(f'phase3/librelane/analog/{b}/a7_resim' for b in blocks)
    elif step == 'A8':
        result += [f'phase3/analog/hardmacro/{block}' for block in blocks]
    elif step in ('A9', 'M3'):
        result += ['phase3/mixed_signal/cosim']
        if step == 'M3':
            result += ['reports/analog/mixed_signal/interface_si.json']
    elif step == 'M1':
        result += ['phase3/mixed_signal/top_merged.gds',
                   'reports/analog/mixed_signal/merge.json',
                   'reports/analog/mixed_signal/top_lvs.json',
                   'reports/analog/mixed_signal/top_lvs_run.json',
                   'phase3/mixed_signal/top_merged.spice']
    elif step == 'M2':
        result += ['reports/analog/mixed_signal/' + n + '.json'
                   for n in ('power_domain', 'level_shifter', 'isolation')]
    elif step == 'M4':
        result += ['reports/analog/mixed_signal/signoff.json',
                   'reports/analog/mixed_signal/top_pv']
    result += ['reports/execution/analog/' + step]
    # Existing canonical --json consumers keep their report destinations.
    for gate in gate_specs(step):
        words = gate['command'].split()
        if '--json' in words:
            result.append(words[words.index('--json') + 1])
    return tuple(dict.fromkeys(result))


def covered(name: str, paths: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p.rstrip('/') + '/')
               or fnmatch.fnmatchcase(name, p) for p in paths)


def gate_specs(step: str) -> tuple[dict, ...]:
    out = []
    def visit(node):
        if not isinstance(node, dict):
            return
        for kind in ('program_exit_zero', 'optional_program_exit_zero', 'advisory_program_exit_zero'):
            if kind in node:
                item = node[kind]
                row = {'command': item} if isinstance(item, str) else dict(item)
                row['kind'] = kind
                out.append(row)
        for key in ('all_of', 'any_of'):
            if isinstance(node.get(key), list):
                for child in node[key]:
                    visit(child)
    visit(CONTRACTS[step]['canonical_row']['gate'])
    return tuple(out)


def project_population(project: Path, excluded: tuple[str, ...], roots: tuple[str, ...]) -> tuple[dict, str]:
    """Freeze all consumed project files and lexical aliases, excluding outputs.

    Ordinary directories are containers rather than INPUT. Their aliases are
    identities even when empty. Thus creating an output's parent cannot mutate
    INPUT, while added/deleted/retargeted upstream files always do.
    """
    files, rows = {}, {}
    boundary = project.resolve(strict=True)
    def walk(path: Path, rel: str, ancestors: tuple[Path, ...]):
        if rel and covered(rel, excluded):
            return
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise em.Refusal('PRODUCTION_INPUT_LINK_UNRESOLVED', str(path)) from exc
        if not resolved.is_relative_to(boundary):
            raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path))
        if path.is_dir():
            if resolved in ancestors:
                raise em.Refusal('PRODUCTION_INPUT_LINK_CYCLE', str(path))
            rows[rel] = {'alias': os.readlink(path) if path.is_symlink() else None,
                         'resolved': str(resolved), 'kind': 'directory'}
            for child in sorted(path.iterdir()):
                walk(child, (rel + '/' if rel else '') + child.name, (*ancestors, resolved))
        elif path.is_file():
            key = 'project/' + rel
            rows[rel] = {'alias': os.readlink(path) if path.is_symlink() else None,
                         'resolved': str(resolved), 'sha256': em.digest(resolved)}
            files[key] = resolved
        else:
            raise em.Refusal('PRODUCTION_INPUT_NOT_REGULAR', str(path))
    for rel in roots:
        path = project / _relative(rel)
        if not path.exists() and not path.is_symlink():
            rows[rel] = {'kind': 'missing'}
        else:
            walk(path, rel, ())
    return files, json.dumps(rows, sort_keys=True)


@dataclass(frozen=True)
class AnalogContext(em.Context):
    project: Path = Path('/')
    excluded: tuple[str, ...] = ()
    project_roots: tuple[str, ...] = ()
    extra_roots: Mapping[str, Path] = field(default_factory=dict)
    population: str = ''
    extra_population: str = ''
    source_files: Mapping[str, str] = field(default_factory=dict)
    request_record: Path = Path('/')
    request_digest: str = ''
    canonical_destinations: tuple[str, ...] = ()

    def binding(self):
        _, population = project_population(self.project, self.excluded, self.project_roots)
        _, extra = input_population(self.extra_roots)
        if population != self.population or extra != self.extra_population:
            raise em.Refusal('PRODUCTION_INPUT_POPULATION_CHANGED', self.step_id)
        if any(str(path.resolve()) not in self.source_files for path in source_dependencies()):
            raise em.Refusal('ADAPTER_SOURCE_POPULATION_CHANGED', self.step_id)
        for name, expected in self.source_files.items():
            path = Path(name)
            if path.is_symlink() or not path.is_file() or em.digest(path) != expected:
                raise em.Refusal('ADAPTER_SOURCE_MISMATCH', name)
        if em.digest(self.request_record) != self.request_digest:
            raise em.Refusal('PRODUCTION_REQUEST_CHANGED', self.step_id)
        return super().binding()


DECLARATIONS = ('phase1/analog/analog_block_list.json',
                'phase3/analog/analog_block_list.json', 'analog/analog_block_list.json')


def declaration_path(project: Path) -> Path:
    """Name the existing declaration; never synthesize a Phase-1 copy."""
    return next((project / rel for rel in DECLARATIONS
                 if (project / rel).exists() or (project / rel).is_symlink()),
                project / DECLARATIONS[0])


def _blocks(project: Path, declaration: Path) -> tuple[str, ...]:
    if (declaration not in tuple(project / rel for rel in DECLARATIONS)
            or not declaration.is_file() or any(p.is_symlink() for p in
                (declaration, *declaration.parents))):
        raise em.Refusal('ANALOG_DECLARATION_REQUIRED', str(declaration_path(project)))
    try:
        data = json.loads(declaration.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise em.Refusal('ANALOG_DECLARATION_MALFORMED', str(declaration)) from exc
    rows = data.get('blocks') if isinstance(data, dict) else data
    if not isinstance(rows, list) or any(not isinstance(b, dict) for b in rows):
        raise em.Refusal('ANALOG_DECLARATION_MALFORMED', str(declaration))
    names = tuple(b.get('name') for b in rows)
    if any(not isinstance(n, str) or not n or '/' in n or n in ('.', '..') for n in names) or len(set(names)) != len(names):
        raise em.Refusal('ANALOG_BLOCK_IDENTITY_INVALID', repr(names))
    # Alternate current roots must name the same population. The selected
    # on-disk declaration itself remains a consumed, hash-bound INPUT.
    for rel in DECLARATIONS:
        candidate = project / rel
        if candidate == declaration:
            continue
        if candidate.is_symlink() or any(p.is_symlink() for p in candidate.parents):
            raise em.Refusal('ANALOG_DECLARATION_CONFLICT', rel)
        if candidate.is_file():
            try:
                other = json.loads(candidate.read_text())
            except (OSError, ValueError, TypeError) as exc:
                raise em.Refusal('ANALOG_DECLARATION_MALFORMED', str(candidate)) from exc
            other = other.get('blocks') if isinstance(other, dict) else other
            if other != rows:
                raise em.Refusal('ANALOG_DECLARATION_CONFLICT', rel)
    return names


def source_requirements(step: str) -> tuple[str, ...]:
    """F1 builds this common manifest before constructing StepRequest."""
    return tuple(str(p) for p in (Path(__file__).resolve(), WORKER,
        PROGRAMS / 'execution_analog_mixed_worker.py',
        PROGRAMS / 'execution_analog_local_transport.py', CONTRACT_FILE,
        PROGRAMS / 'execution_step_protocol.py', PROGRAMS / 'execution_modes.py',
        PROGRAMS / 'analog_one_shot_runner.py', Path(sys.executable).resolve(),
        *(PROGRAMS / name for name in PRODUCERS[step]),
        *(PROGRAMS / (g['command'].split()[0] + '.py') for g in gate_specs(step))))


def source_dependencies() -> tuple[Path, ...]:
    suffixes = {'.py', '.json', '.yaml', '.yml', '.tcl', '.rb', '.v', '.sp', '.j2', '.jinja2', '.txt', '.toml'}
    programs = tuple(p for p in PROGRAMS.rglob('*') if p.is_file()
                 and not {'tests', '__pycache__'}.intersection(p.relative_to(PROGRAMS).parts)
                 and (p.suffix in suffixes or p.is_relative_to(PROGRAMS / 'data')))
    # The canonical consumer reads this flow contract outside programs/.
    # Bind its current bytes before issuing the worker, just like gate source.
    return programs + (PROGRAMS.parent / 'flow/phase1_phase2_phase3.yaml',)


def classify(request: StepRequest) -> PreparedStep:
    """Classify the current analog declaration without creating a context."""
    step, project = request.step_id, request.project
    if step not in STEP_IDS:
        raise em.Refusal('ANALOG_STEP_UNKNOWN', step)
    request.check_source()
    params = json_parameters(request.parameters)
    if params.get('project', str(project)) != str(project):
        raise em.Refusal('ANALOG_FRONTDOOR_PROJECT_DISAGREES', str(params.get('project')))
    declaration = Path(str(params.get('declaration', '')))
    blocks = _blocks(project, declaration)
    requested_block = params.get('block')
    if requested_block is not None:
        if (step not in ANALOG_STEPS or not isinstance(requested_block, dict)
                or not isinstance(requested_block.get('name'), str)
                or requested_block['name'] not in blocks):
            raise em.Refusal('ANALOG_BLOCK_INPUT_UNBOUND', repr(requested_block))
        try:
            current = json.loads(declaration.read_text())
        except (OSError, ValueError, TypeError) as exc:
            raise em.Refusal('ANALOG_DECLARATION_MALFORMED', str(declaration)) from exc
        current = current.get('blocks') if isinstance(current, dict) else current
        row = next(b for b in current if b['name'] == requested_block['name'])
        if any(key not in row or row[key] != value for key, value in requested_block.items()):
            raise em.Refusal('ANALOG_BLOCK_INPUT_DISAGREES', requested_block['name'])
        blocks = (requested_block['name'],)
    facts = {'step_id': step, 'source_sha': request.source_sha,
             'source_files_sha256': em._hash(dict(request.source_files)),
             'declaration': str(declaration),
             'declaration_sha256': em.digest(declaration), 'blocks': list(blocks)}
    if not blocks:
        return PreparedStep(None, None, None, 'declared_inapplicable',
            'Current bound declaration contains zero analog blocks.',
            {'declaration': str(declaration), 'declaration_sha256': facts['declaration_sha256'],
             'facts': facts})
    return PreparedStep(None, None, None, 'execute',
        'Source-bound analog declaration preflight; live prepare is required',
        {'facts': facts})


def prepare(request: StepRequest) -> PreparedStep:
    step, project = request.step_id, request.project
    if step not in STEP_IDS:
        raise em.Refusal('ANALOG_STEP_UNKNOWN', step)
    request.check_source()
    params = json_parameters(request.parameters)
    if params.get('project', str(project)) != str(project):
        raise em.Refusal('ANALOG_FRONTDOOR_PROJECT_DISAGREES', str(params.get('project')))
    declaration = Path(str(params.get('declaration', '')))
    blocks = _blocks(project, declaration)
    requested_block = params.get('block')
    if requested_block is not None:
        if (step not in ANALOG_STEPS or not isinstance(requested_block, dict)
                or not isinstance(requested_block.get('name'), str)
                or requested_block['name'] not in blocks):
            raise em.Refusal('ANALOG_BLOCK_INPUT_UNBOUND', repr(requested_block))
        current = json.loads(declaration.read_text())
        current = current.get('blocks') if isinstance(current, dict) else current
        row = next(b for b in current if b['name'] == requested_block['name'])
        if any(key not in row or row[key] != value for key, value in requested_block.items()):
            raise em.Refusal('ANALOG_BLOCK_INPUT_DISAGREES', requested_block['name'])
        blocks = (requested_block['name'],)
    if not blocks:
        return PreparedStep(None, None, None, 'declared_inapplicable',
            'Current bound declaration contains zero analog blocks.',
            {'declaration': str(declaration), 'declaration_sha256': em.digest(declaration),
             'facts': {'blocks': [], 'canonical_condition': str(declaration.relative_to(project))}})
    for filename in source_requirements(step):
        if filename not in request.source_files:
            raise em.Refusal('ANALOG_SOURCE_DEPENDENCY_UNBOUND', filename)
    # Bind imported producer/helper source, not just the wrapper's own file.
    for path in source_dependencies():
        if str(path.resolve()) not in request.source_files:
            raise em.Refusal('ANALOG_SOURCE_DEPENDENCY_UNBOUND', str(path))
    if request.record.is_relative_to(project) or request.lease.is_relative_to(project):
        raise em.Refusal('ANALOG_CONTROL_NAMESPACE_OVERLAP', str(request.record))
    lease_file = request.lease / 'lease.json'
    if not lease_file.is_file() or lease_file.is_symlink():
        raise em.Refusal('ANALOG_LEASE_DIRECTORY_REQUIRED', str(request.lease))
    if not isinstance(params.get('objective'), dict) or not params['objective']:
        raise em.Refusal('ANALOG_OBJECTIVE_REQUIRED', step)
    for field_name in ('cpus', 'ram_mb', 'timeout_s'):
        if type(params.get(field_name)) is not int or params[field_name] <= 0:
            raise em.Refusal('ANALOG_RESOURCE_PARAMETER_REQUIRED', field_name)
    if step != 'A1' and not isinstance(params.get('pdk_name'), str):
        raise em.Refusal('ANALOG_PDK_IDENTITY_REQUIRED', step)
    if step in ('A2', 'A3') and not params.get('decision'):
        raise em.Refusal('ANALOG_AI_DECISION_REQUIRED', step)
    exclusions = contract_outputs(step, blocks)
    supplied_roots = params.get('input_roots')
    root_extras = {}
    if isinstance(supplied_roots, dict) and supplied_roots:
        # f199 frontdoor roots are lexical names -> absolute typed Paths.
        # Project roots retain canonical identity; external controls/PDK/AI
        # inputs retain the exact frontdoor key and complete population.
        project_roots = []
        for name, raw in supplied_roots.items():
            name = _relative(name)
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise em.Refusal('ANALOG_CONSUMED_INPUT_ROOT_UNBOUND', name)
            path = Path(raw)
            if path.is_relative_to(project):
                rel = _relative(str(path.relative_to(project)))
                if covered(rel, exclusions):
                    raise em.Refusal('ANALOG_INPUT_OUTPUT_OVERLAP', rel)
                project_roots.append(rel)
            else:
                if name in ('project', 'request.json') or name.startswith(('project/', 'controls/')):
                    raise em.Refusal('ANALOG_EXTRA_INPUT_RESERVED', name)
                root_extras[name] = path
        project_roots = tuple(dict.fromkeys(project_roots))
    elif isinstance(supplied_roots, list) and supplied_roots and all(isinstance(r, str) for r in supplied_roots):
        # Preserve the already-issued source-control route. The canonical
        # production frontdoor uses the mapping above.
        project_roots = tuple(_relative(r) for r in supplied_roots)
    else:
        raise em.Refusal('ANALOG_CONSUMED_INPUT_ROOTS_REQUIRED', step)
    inputs, population = project_population(project, exclusions, project_roots)
    declaration_key = 'project/' + str(declaration.relative_to(project))
    if declaration_key not in inputs or inputs[declaration_key] != declaration.resolve():
        raise em.Refusal('ANALOG_DECLARATION_INPUT_UNBOUND', str(declaration))
    roots = {'controls/lease.json': lease_file, **root_extras}
    for key, raw in (params.get('extra_inputs') or {}).items():
        key = _relative(key)
        if key in ('project', 'request.json') or key.startswith(('project/', 'controls/')):
            raise em.Refusal('ANALOG_EXTRA_INPUT_RESERVED', key)
        path = Path(raw)
        if not path.is_absolute() or path.is_relative_to(project):
            raise em.Refusal('ANALOG_EXTRA_INPUT_UNBOUND', str(path))
        roots[key] = path
    for key in ('pdk_root', 'decision', 'native_admission'):
        if params.get(key):
            roots[key] = Path(params[key])
    facts = params.get('native_facts') or {}
    facts_file = params.get('native_facts_file')
    if not isinstance(facts, dict):
        raise em.Refusal('ANALOG_NATIVE_FACTS_TYPE_INVALID', step)
    if facts_file:
        file_path = Path(facts_file)
        if not file_path.is_absolute() or file_path.is_symlink() or not file_path.is_file():
            raise em.Refusal('ANALOG_NATIVE_FACTS_FILE_UNBOUND', str(file_path))
        if json.loads(file_path.read_text()) != facts:
            raise em.Refusal('ANALOG_NATIVE_FACTS_DOCUMENT_MISMATCH', step)
        if params.get('native_facts_sha256', em.digest(file_path)) != em.digest(file_path):
            raise em.Refusal('ANALOG_NATIVE_FACTS_DIGEST_MISMATCH', step)
        roots['native_facts'] = file_path
    elif facts:
        raise em.Refusal('ANALOG_NATIVE_FACTS_FILE_REQUIRED', step)
    extra, extra_population = input_population(roots)
    inputs.update(extra)
    destinations = adoption_destinations(project, inputs, exclusions, params.get('adoption_paths'))
    if step not in ('A1', 'A2') and not params.get('pdk_root'):
        raise em.Refusal('ANALOG_PDK_INPUT_REQUIRED', step)
    # Resolve all canonical input alternatives; missing paths are explicit
    # upstream dependencies. A8's predecessors are consumed by its producer.
    for required in CONTRACTS[step]['canonical_row'].get('required_inputs', []):
        if required.get('outputs') == 'all':
            patterns = CONTRACTS[required['from']]['canonical_row']['required_outputs']
        else:
            patterns = [required['path']]
        for expression in patterns:
            if not any(any(p.is_file() for p in project.glob(alt.strip()))
                       for alt in expression.split(' OR ')):
                raise em.Refusal('ANALOG_UPSTREAM_INPUT_MISSING', expression)
            for alt in expression.split(' OR '):
                for path in project.glob(alt.strip()):
                    if path.is_file() and 'project/' + str(path.relative_to(project)) not in inputs:
                        raise em.Refusal('ANALOG_UPSTREAM_POPULATION_UNBOUND', str(path))
    native = step not in ('A1', 'A2')
    qualified = not native
    available = not native
    reason = ''
    if native:
        # No CLI/version probe and no physical declaration bypass for software.
        available = bool(facts_file and facts.get('available') is True and params.get('native_deadline_s'))
        qualified = facts.get('qualified') is True and bool(facts.get('measurement'))
        reason = 'Native INPUT/tool/image/peak admission and measured producer qualification required.'
        if tuple(facts.get('engine_families', ())) != ENGINE_FAMILIES[step]:
            available = False
            reason = 'No admitted complete provider for the source-owned complementary engine chain.'
        if available and not (PROGRAMS / 'execution_resource_lease.py').is_file():
            available = False
            reason = 'F1/P0 execution_resource_lease.native_boundary dependency not installed.'
    # F1 owns the full portfolio/flow population, including conditional and
    # advisory clauses. Coverage is declared before plan; verdicts stay measured.
    from execution_production import canonical_gate_population
    gates = canonical_gate_population(
        step, tuple(g['command'].split()[0] for g in gate_specs(step)))
    if not gates:
        raise em.Refusal('ANALOG_GATE_CONTRACT_EMPTY', step)
    objective = {'declared': params['objective'], 'blocks': list(blocks),
                 'parameters_sha256': em._hash(params), 'original_project': str(project),
                 'pdk_root': params.get('pdk_root'), 'contract_sha256': em.digest(CONTRACT_FILE),
                 'source_manifest_sha256': em._hash(dict(request.source_files))}
    spec = {'step_id': step, 'source_sha': request.source_sha,
            'source_files': dict(request.source_files), 'original_project': str(project),
            'parameters': params, 'blocks': list(blocks), 'adoption_paths': list(exclusions),
            'lease': str(request.lease), 'input_hashes': {k: em.digest(v) for k, v in inputs.items()}}
    if native:
        spec['producer_work_contract'] = producer_work_contract(step)
    if request.record.exists():
        raise em.Refusal('ANALOG_REQUEST_RECORD_REUSED', str(request.record))
    write_json(request.record, spec)
    inputs['request.json'] = request.record
    context = AnalogContext(step, request.source_sha, inputs, objective, gates,
        project=project, excluded=exclusions, extra_roots=roots, population=population,
        extra_population=extra_population, project_roots=project_roots, source_files=dict(request.source_files),
        request_record=request.record, request_digest=em.digest(request.record), canonical_destinations=destinations)
    registry = em.Registry()
    required = ('producer.json', 'products.json') + (
        ('native-launch.json', 'native-commands.jsonl', 'native-processes.jsonl',
         'producer-work.json', 'producer-calls/calls.json') if native else ())
    worker = (str(Path(sys.executable).resolve()), str(WORKER),
              '--inputs', '{inputs}', '--outputs', '{outputs}')
    registry.register(em.Adapter('analog-' + step.lower(), 'vibeic' if not native else 'analog-native',
        step, request.source_sha, dict(request.source_files),
        facts.get('image_id', 'source:' + request.source_sha), ENGINE_FAMILIES[step],
        (em.Component('produce-and-semantic-gates', worker, params['timeout_s']),),
        validate, required, objective, qualified=qualified, available=available,
        qualification_evidence=json.dumps(facts if native else {
            'kind': 'necessary-structured-generation', 'producer_sources': PRODUCERS[step],
            'source_manifest': dict(request.source_files), 'design_acceptance': False}, sort_keys=True),
        availability_reason=reason if not available else '',
        cpus=params['cpus'], ram_mb=params['ram_mb'],
        own_no_tool_reason=('Structured document attribution and declared topology sizing are '
                           'performed by the source-owned producer; no numerical targets are invented.') if not native else None,
        output_contract={key: required for key in CONTRACTS[step]['portfolio_policy']['required_output_contract']}))
    context.binding()
    return PreparedStep(context, registry, consume, adoption_paths=destinations)


def validate(outputs: Path, binding: dict) -> em.Evidence:
    from execution_analog_worker import validate_outputs
    return validate_outputs(outputs, binding)


def consume(project: Path, context: AnalogContext, controller: em.Controller,
            run: Path, adopted: dict) -> dict:
    """Import issued bytes, rerun real consumers, rollback on every refusal."""
    from execution_analog_worker import (semantic_gates, validate_products, canonical_report,
        read_canonical_report, report_obligations, _m1_observation, retain_producer_observation)
    if project != context.project or adopted.get('status') != 'ADOPTED':
        raise em.Refusal('ANALOG_ADOPTION_REQUIRED', context.step_id)
    binding = context.binding()
    choice = adopted.get('ai_choice')
    # Re-enter the live controller issuer. A caller's edited adoption.json is
    # insufficient; both original and newly issued generations must agree.
    original = adopted.get('selected_generation')
    if not isinstance(original, dict) or original.get('binding') != binding:
        raise em.Refusal('ANALOG_SELECTED_GENERATION_UNBOUND', context.step_id)
    controller._generation_current(original)
    current = controller.adopt(context, run, choice)
    if (current['selected'] != adopted.get('selected') or
            original.get('arm_id') != current['selected'] or original.get('run_id') != current['run_id'] or
            current['selected_generation']['outputs'] != original.get('outputs')):
        raise em.Refusal('ANALOG_ADOPTION_CHANGED', context.step_id)
    selected = Path(original['directory'])
    manifest = json.loads((selected / 'products.json').read_text())
    producer = json.loads((selected / 'producer.json').read_text())
    validate_products(selected, binding, producer, manifest)
    descriptor = json.loads(current['evidence']['detail'])
    relative = descriptor.get('canonical_gate_report')
    if not isinstance(relative, str):
        raise em.Refusal('ANALOG_CANONICAL_REPORT_UNBOUND', context.step_id)
    report_sha = original['outputs'].get(relative)
    if not report_sha or descriptor.get('sha256') != report_sha or current['evidence']['outputs'].get(relative) != report_sha:
        raise em.Refusal('ANALOG_CANONICAL_REPORT_CHANGED', relative)
    issued_report = read_canonical_report(selected, binding, relative, report_sha)
    if producer['design_verdict'] != 'PASS':
        raise em.Refusal('GATE_FAIL' if producer['design_verdict'] == 'FAIL'
                         else 'GATE_NOT_MEASURED', context.step_id)
    products = manifest['products']
    journal, created = {}, []
    try:
        for rel, expected in products.items():
            if (not covered(rel, context.excluded)
                    or not any(rel == p or rel.startswith(p.rstrip('/') + '/')
                               for p in context.canonical_destinations)):
                raise em.Refusal('ANALOG_PRODUCT_NAMESPACE_UNDECLARED', rel)
            target = project / _relative(rel)
            if (target.is_symlink() or (target.exists() and not target.is_file())
                    or not target.resolve().is_relative_to(project.resolve())):
                raise em.Refusal('ANALOG_ADOPTION_PATH_UNSAFE', rel)
            journal[rel] = target.read_bytes() if target.is_file() else None
            ancestor = target.parent
            while not ancestor.exists():
                created.append(ancestor)
                ancestor = ancestor.parent
            target.parent.mkdir(parents=True, exist_ok=True)
            data = (selected / 'project' / rel).read_bytes()
            if em.digest(selected / 'project' / rel) != expected:
                raise em.Refusal('ANALOG_SELECTED_OUTPUT_CHANGED', rel)
            from _atomic_artefact import write_bytes
            write_bytes(target, data)
        context.binding()
        controller._generation_current(original)
        # Gates execute in an isolated view of the now-imported canonical
        # bytes. Their write side effects are part of the declared transaction.
        import tempfile
        import uuid
        audit = run / ('analog-consumer-' + uuid.uuid4().hex)
        audit.mkdir()
        with tempfile.TemporaryDirectory(prefix='analog-consume-', dir=run) as temp:
            mirror = Path(temp) / 'project'
            mirror.mkdir()
            for name, source in context.inputs.items():
                if name.startswith('project/'):
                    target = mirror / name.removeprefix('project/')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            for rel in products:
                target = mirror / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(project / rel, target)
            from execution_analog_worker import _substance
            _substance(mirror, context.step_id, producer['blocks'])
            report_obligations(issued_report, binding, mirror)
            observation = retain_producer_observation(
                _m1_observation(selected, binding, producer['calls']), audit)
            results = semantic_gates(context.step_id, mirror, audit / 'gates',
                                     producer_observation=observation)
            report = canonical_report(results, binding, mirror, audit,
                                      dict(context.source_files), producer['design_verdict'],
                                      producer_work=issued_report.get('producer_work'))
            write_json(audit / 'canonical-gates.json', report)
            primary = tuple(report['blocking_gates'])
            if report['steps'][0]['status'] != 'PASS':
                measured_fail = producer['design_verdict'] == 'FAIL' or report['steps'][0]['status'] == 'FAIL'
                detail = {'step_id': context.step_id, 'source_sha': context.source_sha,
                          'binding': binding, 'selected_generation': original,
                          'primary_gates': report['gates'], 'consumer_detail': results,
                          'canonical_gate_report': report, 'issued_report_sha256': report_sha,
                          'design_verdict': 'FAIL' if measured_fail else 'NOT_MEASURED'}
                write_json(run / 'analog-consumer-refusal.json', detail)
                raise em.Refusal('GATE_FAIL' if measured_fail else 'ANALOG_DOWNSTREAM_GATE_REFUSED',
                                 json.dumps(report['gates']))
            if report_obligations(report, binding, mirror) != primary:
                raise em.Refusal('ANALOG_CANONICAL_OBLIGATIONS_CHANGED', context.step_id)
            for rel in products:
                if (mirror / rel).is_file() and em.digest(mirror / rel) != em.digest(project / rel):
                    raise em.Refusal('ANALOG_CONSUMER_REWROTE_SELECTED_PRODUCT', rel)
        context.binding()
        controller._generation_current(original)
        read_canonical_report(selected, binding, relative, report_sha)
        for rel, expected in products.items():
            if em.digest(project / rel) != expected:
                raise em.Refusal('ANALOG_IMPORTED_OUTPUT_CHANGED', rel)
        return imported_result(step_id=context.step_id, source_sha=context.source_sha,
                selected_generation=original, binding=binding, copied=products,
                primary_gates=report['gates'],
                design_verdict=producer['design_verdict'],
                consumer_detail={'status': 'CONSUMED', 'products': products, 'gates': results,
                    'blocking_gates': list(primary), 'canonical_gate_report': report,
                    'issued_report': {'relative_path': relative, 'sha256': report_sha},
                    'current_report': {'path': str(audit / 'canonical-gates.json'),
                                       'sha256': em.digest(audit / 'canonical-gates.json')},
                    'external': producer.get('external', [])})
    except Exception:
        from _atomic_artefact import write_bytes
        for rel, data in journal.items():
            target = project / rel
            if data is None:
                target.unlink(missing_ok=True)
            else:
                write_bytes(target, data)
        for directory in sorted(set(created), key=lambda p: len(p.parts), reverse=True):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise

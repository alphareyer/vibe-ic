"""Source-owned F2 producers, validators and canonical consumers.

ENFORCEMENT: blocking. Raw native FAIL, unknown proof and absent physical
handoffs remain distinct from adapter qualification. No parent flow main is
called. All engine work is inside one separately admitted skip-first container.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import fnmatch
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping

_PROGRAMS_DIR = str(Path(__file__).resolve().parent)
if _PROGRAMS_DIR not in sys.path:
    sys.path.insert(0, _PROGRAMS_DIR)
import execution_modes as em
import execution_step_protocol as protocol
from execution_adapters_frontend import (
    FrontendContext, PROGRAMS, STEP_IDS, lexical_tree, stable,
)

# Exact existing program routes. Gate ASTs and output alternatives are loaded
# from the existing canonical flow, rather than reimplementing its predicates.
ROUTES = {
    'D1': ('design_one_shot_runner.step_phase1',),
    '0.5ic': ('submission_template_ingest.main', 'tapeout_declaration_gen.main'),
    '1': ('design_one_shot_runner.step_rtl_gen',),
    '2': ('p0_tool_frontend_check.check', 'crosslayer_rewrite_equivalence.main'),
    '3': ('_cdc_netlist.build', 'cdc_crossing_check.main', 'cdc_async_input_check.main',
          'clock_domain_reg_crossing_check.main', 'reset_dependency_check.main'),
    '4': ('design_one_shot_runner.step_professional_tb_gen',
          'design_one_shot_runner.step_reference_tb',
          'design_one_shot_runner.step_l10_unit_tb_run', 'verilator_coverage_measure.main'),
    '5': ('formal_harness_gen.generate', 'formal_property_run.run',
          'design_one_shot_runner.step_full_stack_functional_tb'),
    '6': ('design_one_shot_runner.step_fpga_compile', 'quartus_map_audit.main'),
    '7': ('_ppa.timing.emit_step7_asic_sdc', 'phase3_one_shot_runner.stamp_pvt_corner_coverage'),
    '8': ('sdc_syntax_check.main', 'sdc_validator_check.main'),
    '10': ('phase3_one_shot_runner.step_prelayout_signoff',),
    '11': ('fault_scan_chain_insert.main', 'fault_atpg_run.main', 'bsdl_emit.main'),
    'FS1': ('fmeda_fault_injection_coverage.main', 'fmeda_coverage_check.main'),
    'DT1': ('transition_fault_atpg_run.main',),
    # Step12 was an inline native segment in the existing chain, not a
    # standalone runner callable. Its actual delegated callable is below.
    '12': ('execution_frontend_worker.produce',),
    '13': ('design_one_shot_runner.step_lec_equivalence',),
    'DT2': ('path_delay_fault_atpg_run.main',),
    'DT3': ('sdd_atpg_run.main',),
    'P0': ('p0_tool_frontend_check.check', 'formal_structural_check.check_claim'),
}
ENGINES = {
    'D1': ('vibeic-extraction',), '0.5ic': ('vibeic-declarations',),
    '1': ('vibeic-rtl-dispatch',), '2': ('yosys', 'verilator'),
    '3': ('yosys', 'vibeic-cdc'), '4': ('iverilog', 'verilator'),
    '5': ('yosys', 'abc', 'sby'), '6': ('quartus',),
    '7': ('vibeic-constraints',), '8': ('vibeic-sdc',), '10': ('openroad',),
    '11': ('fault', 'yosys', 'iverilog'), 'FS1': ('iverilog',),
    'DT1': ('yosys',), '12': ('yosys',), '13': ('yosys', 'eqy'),
    'DT2': ('yosys', 'openroad'), 'DT3': ('yosys', 'openroad'),
    'P0': ('yosys', 'verilator'),
}
# Outputs excluded from source inputs, not all reports or all downstream dirs.
# Shared gate paths are derived from this step's actual command AST below.
OUTPUT_DIRS = {
    'D1': ('phase1/generated_docs', 'phase1/extraction_patterns.json',
           'reports/phase1', 'reports/audit/phase1'),
    '0.5ic': ('input/submission_template', 'reports/phase1/submission_template.json',
              'reports/phase1/tapeout_declaration.json'),
    '1': ('phase2/stage1/rtl', 'reports/phase2/rtl_authoring_request.json'),
    '2': ('reports/phase2/lint', 'reports/crosslayer/rewrite_equivalence.json',
          'reports/crosslayer/rewrite_equivalence_check.json', 'reports/audit/phase2/rtl_elab.json'),
    '3': ('reports/phase2/cdc',),
    '4': ('phase2/stage1/sim/results.xml', 'phase2/stage1/sim/pass.flag',
          'phase2/stage1/sim/work', 'phase2/stage1/sim_professional',
          'reports/phase2/coverage'),
    '5': ('phase2/stage1/formal/results.json', 'phase2/stage1/formal/formal_not_run.json',
          'phase2/stage1/formal/work', 'phase2/stage1/sim_full_stack/results.json'),
    '6': ('phase2/stage1/fpga/output_files', 'phase2/stage1/fpga/compile.log',
          'reports/phase2/fpga'),
    '7': ('phase2/stage2/constraints',),
    '8': ('reports/phase2/sdc_check.json', 'reports/sdc_validator.json'),
    '10': ('phase3/stage3/sta/pre_pnr_timing.rpt', 'phase3/stage3/sta/prelayout_per_corner',
           'reports/phase3/sta/pre_pnr_summary.json'),
    '11': ('phase2/stage2/dft', 'reports/phase2/dft/coverage.json',
           'reports/phase2/dft/bsdl_plan.json', 'reports/phase2/dft/scan_chain.json'),
    'FS1': ('reports/phase2/safety',),
    'DT1': ('phase2/stage2/dft/tdf', 'reports/phase2/dft/transition_coverage.json'),
    '12': ('phase2/stage2/synth/post_dft_netlist.v',),
    '13': ('reports/lec.json', 'reports/lec.rpt', 'reports/lec_runs'),
    'DT2': ('reports/phase2/dft/path_delay_coverage.json', 'phase2/stage2/dft/pdf'),
    'DT3': ('reports/phase2/dft/sdd_coverage.json', 'phase2/stage2/dft/sdd'),
    'P0': ('reports/audit/phase2/rtl_elab.json',),
}


def write(path: Path, value: object):
    from _atomic_artefact import write_json
    write_json(path, value)


def contract(step_id: str) -> dict:
    import _flow_yaml
    return next(r for r in _flow_yaml.load()['steps'] if str(r['id']) == step_id)


def commands(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('program_exit_zero', 'optional_program_exit_zero', 'advisory_program_exit_zero'):
                yield item if isinstance(item, str) else item['command']
            elif isinstance(item, (dict, list)):
                yield from commands(item)
    elif isinstance(value, list):
        for item in value:
            yield from commands(item)


def exclusions(step_id: str) -> tuple[str, ...]:
    import shlex
    row = contract(step_id)
    result = list(OUTPUT_DIRS[step_id])
    for spec in row.get('required_outputs', []):
        result.extend(part.strip() for part in spec.split(' OR '))
    result.extend(item['path'] for item in row.get('program_outputs', []) if item.get('path'))
    for command in commands(row['gate']):
        parts = shlex.split(command)
        for flag in ('--json', '--out'):
            if flag in parts and parts.index(flag) + 1 < len(parts):
                result.append(parts[parts.index(flag) + 1])
    # Source-owned output receipts are not producer inputs.
    result += ['reports/execution_frontend', 'reports/step_metrics',
               'reports/flow_metrics', 'provenance.jsonl']
    return tuple(dict.fromkeys(result))


def adoption_paths(step_id: str) -> tuple[str, ...]:
    """Concrete canonical journals; output qualification may use patterns."""
    paths = []
    for pattern in exclusions(step_id):
        parts = Path(pattern).parts
        first_glob = next((i for i, part in enumerate(parts)
                           if any(c in part for c in '*?[')), len(parts))
        path = str(Path(*parts[:first_glob])) if first_glob else ''
        if not path or path == '.':
            raise em.Refusal('FRONTEND_ADOPTION_PATH_UNBOUNDED', pattern)
        paths.append(path)
    # F1 journals disjoint destinations. A directory already covers its
    # contained files, so nested entries must not form competing journals.
    return tuple(path for path in dict.fromkeys(paths)
                 if not any(path != other and path.startswith(other+'/') for other in paths))


def input_roots(project: Path, parameters: dict) -> tuple[str, ...]:
    """F1 names the step's consumed canonical roots, never a global default.

    A mapping uses F1's safe labels and exact absolute lexical Paths. Local
    paths are frozen under their actual canonical relative paths; external
    labels are bound separately. A relative sequence supports older callers.
    Missing named roots remain in the lexical population.
    """
    value = parameters.get('input_roots')
    if isinstance(value, Mapping) and value:
        names = []
        for name, path in value.items():
            if not isinstance(name, str) or not isinstance(path, (str, os.PathLike)):
                raise em.Refusal('FRONTEND_INPUT_ROOT_TYPE_INVALID', str(name))
            if not name or Path(name).is_absolute() or '..' in Path(name).parts:
                raise em.Refusal('FRONTEND_INPUT_ROOT_TYPE_INVALID', name)
            path = Path(path)
            if not path.is_absolute():
                raise em.Refusal('FRONTEND_INPUT_ROOT_IDENTITY_MISMATCH', name)
            if path.is_relative_to(project):
                names.append(str(path.relative_to(project)))
    elif isinstance(value, (tuple, list)) and value:
        names = list(value)
    else:
        raise em.Refusal('FRONTEND_STEP_INPUT_ROOTS_MISSING', '')
    for name in names:
        if (not isinstance(name, str) or not name or name == '.'
                or Path(name).is_absolute() or '..' in Path(name).parts
                or any(c in name for c in '*?[')):
            raise em.Refusal('FRONTEND_UNSAFE_INPUT_ROOT', str(name))
    if not names:
        raise em.Refusal('FRONTEND_STEP_INPUT_ROOTS_MISSING', 'no consumed project input')
    if parameters.get('step_id') == '4':
        # The existing differential caller reads this selector, including
        # its absence. Freeze it before the isolated producer is prepared.
        names.append('phase3/librelane_switch.json')
    return tuple(sorted(set(names)))


def required_input_specs(step_id: str) -> tuple[str, ...]:
    """Reuse the canonical expansion, including producer `outputs: all`."""
    import _flow_yaml
    import step_required_inputs_check as required
    by_id = {str(row['id']): row for row in _flow_yaml.load()['steps']}
    return tuple(spec for entry in by_id[step_id].get('required_inputs', [])
                 for _, spec in required.expand(entry, by_id))


def required_input_roots(step_id: str) -> tuple[str, ...]:
    result = []
    for spec in required_input_specs(step_id):
        for pattern in spec.split(' OR '):
            parts = Path(pattern.strip()).parts
            first = next((i for i, part in enumerate(parts) if any(c in part for c in '*?[')), len(parts))
            root = str(Path(*parts[:first])) if first else ''
            if not root or root == '.':
                raise em.Refusal('FRONTEND_CANONICAL_INPUT_UNBOUNDED', pattern)
            result.append(root)
    return tuple(sorted(set(result)))


def validate_step_inputs(project: Path, step_id: str, roots: tuple[str, ...], inputs: dict) -> dict:
    import _flow_yaml
    import step_required_inputs_check as required
    by_id = {str(row['id']): row for row in _flow_yaml.load()['steps']}
    # Missing alternatives are part of the population too. Binding one file
    # from a glob cannot freeze the producer's complete directory selection.
    for name in required_input_roots(step_id):
        if not any(name == root or name.startswith(root.rstrip('/')+'/') for root in roots):
            raise em.Refusal('FRONTEND_REQUIRED_INPUT_ROOT_NOT_BOUND', name)
    assessment = required.check_one(project, by_id[step_id], by_id, {}, required.load_ledger(project))
    if assessment['verdict'] != 'READY':
        raise em.Refusal('FRONTEND_REQUIRED_INPUT_MISSING', stable(assessment))
    for spec in required_input_specs(step_id):
        matches = [p for pattern in spec.split(' OR ') for p in project.glob(pattern.strip()) if p.is_file()]
        if any('project/'+str(p.relative_to(project)) not in inputs for p in matches):
            raise em.Refusal('FRONTEND_REQUIRED_INPUT_NOT_BOUND', spec)
    return assessment


def path_parameters(value):
    """Compatibility entry point; F1 owns typed snapshot serialization."""
    return protocol.json_parameters(value)


def nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise em.Refusal('FRONTEND_DECLARATION_MISSING', field)
    return value


def source_current(sha: str, files: dict):
    cp = subprocess.run(['git', '-C', str(PROGRAMS), 'rev-parse', 'HEAD'],
                        capture_output=True, text=True, timeout=10)
    if cp.returncode or cp.stdout.strip() != sha:
        raise em.Refusal('FRONTEND_WRONG_SOURCE', sha)
    for name, expected in files.items():
        p = Path(name)
        if p.is_symlink() or not p.is_file() or not expected or em.digest(p) != expected:
            raise em.Refusal('FRONTEND_SOURCE_MISMATCH', name)


def source_files() -> dict[str, str]:
    result = {}
    for root in (PROGRAMS, PROGRAMS.parent / 'flow', PROGRAMS.parent / 'agents'):
        for p in root.rglob('*'):
            if {'tests', 'benchmark', 'harness', '__pycache__'}.intersection(p.relative_to(root).parts):
                continue
            if p.is_file() and not p.is_symlink() and p.suffix in (
                    '.py', '.json', '.yaml', '.yml', '.tcl', '.sh', '.mjs', '.js', '.md'):
                result[str(p.resolve())] = em.digest(p)
    result[str(Path(sys.executable).resolve())] = em.digest(Path(sys.executable).resolve())
    docker = shutil.which('docker')
    if docker:
        result[str(Path(docker).resolve())] = em.digest(Path(docker).resolve())
    return result


def rtl_expert_sources(parameters: dict) -> dict[str, str]:
    """The existing RTL dispatcher can read these current skill documents."""
    import design_one_shot_runner as runner
    config = runner._lookup_class(nonempty(parameters.get('ic_class'), 'ic_class')) or {}
    names = {'spec-to-rtl', 'catalog-glue-author', config.get('fallback_skill')}
    result = {}
    for name in sorted(n for n in names if n):
        path = runner.SKILLS_DIR / name / 'SKILL.md'
        if path.is_file() and not path.is_symlink():
            result[str(path.resolve())] = em.digest(path)
    return result


def lease_current(path: Path, parameters: dict) -> dict:
    if not path.is_dir() or path.is_symlink():
        raise em.Refusal('FRONTEND_RESOURCE_LEASE_DIRECTORY_MISSING', str(path))
    lease_file = path / 'lease.json'
    if lease_file.is_symlink() or not lease_file.is_file():
        raise em.Refusal('FRONTEND_RESOURCE_LEASE_MISSING', str(path))
    data = json.loads(lease_file.read_text())
    for key in ('cpus', 'host_ram_mb', 'container_ram_mb'):
        if type(data.get(key)) not in (int, float) or data[key] <= 0 or not math.isfinite(data[key]):
            raise em.Refusal('FRONTEND_RESOURCE_LEASE_INVALID', key)
    if type(data.get('parent_pid')) is not int or data['parent_pid'] <= 0:
        raise em.Refusal('FRONTEND_RESOURCE_LEASE_INVALID', 'parent_pid')
    if (not isinstance(data.get('parent_start_ticks'), str)
            or not re.fullmatch('[1-9][0-9]*', data['parent_start_ticks'])):
        raise em.Refusal('FRONTEND_RESOURCE_LEASE_INVALID', 'parent_start_ticks')
    from execution_resource_lease import start_ticks
    ticks = start_ticks(data['parent_pid'])
    if ticks != data['parent_start_ticks']:
        raise em.Refusal('FRONTEND_RESOURCE_LEASE_NOT_LIVE', str(path))
    return data


def declaration_handoff(project: Path, parameters: dict, facts: dict) -> dict:
    path = canonical_path(parameters.get('declaration'), 'declaration')
    if not facts:
        raise em.Refusal('FRONTEND_DECLARATION_FACTS_EMPTY', str(path))
    return {'declaration': str(path), 'declaration_sha256': em.digest(path), 'facts': facts}


def canonical_path(value, field):
    if not isinstance(value, (str, os.PathLike)) or not os.fspath(value):
        raise em.Refusal('FRONTEND_PATH_TYPE_INVALID', field)
    path = Path(value)
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or path.resolve(strict=True) != path):
        raise em.Refusal('FRONTEND_CURRENT_DECLARATION_MISSING', field)
    return path


def native_facts(parameters: dict) -> tuple[Path, str]:
    """Consume issued facts; the F1 issuer owns their capacity provenance."""
    facts = parameters.get('native_facts')
    path = canonical_path(parameters.get('native_facts_file'), 'native_facts_file')
    expected = em.digest(path)
    if parameters.get('native_facts_sha256', expected) != expected:
        raise em.Refusal('FRONTEND_NATIVE_FACTS_CHANGED', str(path))
    if not isinstance(facts, dict) or not facts or json.loads(path.read_text()) != facts:
        raise em.Refusal('FRONTEND_NATIVE_FACTS_MISMATCH', str(path))
    if em.digest(path) != expected:
        raise em.Refusal('FRONTEND_NATIVE_FACTS_CHANGED', str(path))
    return path, expected


def native_deadline(parameters: dict) -> float:
    # This is the actual step timeout passed at the front door, not an
    # invented native-admission lease field. Capacity must admit this argv.
    seconds = parameters.get('timeout_s')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise em.Refusal('FRONTEND_NATIVE_DEADLINE_MISSING', 'timeout_s')
    return float(seconds)


def physical_declaration(project: Path, physical: dict) -> Path:
    if not isinstance(physical, dict) or physical.get('board_present') is not False:
        raise em.Refusal('FRONTEND_PHYSICAL_HANDOFF_INVALID', '6')
    declared = nonempty(physical.get('declaration_path'), 'physical_handoff.declaration_path')
    path = project/declared
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(project):
        raise em.Refusal('FRONTEND_PHYSICAL_DECLARATION_MISSING', declared)
    doc = json.loads(path.read_text())
    if doc.get('board_present') is not False or doc.get('answered_by') != 'owner':
        raise em.Refusal('FRONTEND_PHYSICAL_DECLARATION_INVALID', declared)
    return path


def image_current(image: str):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise em.Refusal('FRONTEND_MUTABLE_IMAGE', image)
    cp = subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'],
                        text=True, capture_output=True, timeout=10)
    if cp.returncode or cp.stdout.strip() != image:
        raise em.Refusal('FRONTEND_IMAGE_UNAVAILABLE', image)


def external_inputs(project: Path, parameters: dict):
    """Freeze the caller's external namespaces, including handoff controls."""
    roots = parameters.get('external_input_roots') or {}
    if not isinstance(roots, Mapping):
        raise em.Refusal('FRONTEND_EXTERNAL_ROOTS_INVALID', '')
    roots = dict(roots)
    if isinstance(parameters.get('input_roots'), Mapping):
        for name, root in parameters['input_roots'].items():
            if not Path(root).is_relative_to(project):
                if name in roots and Path(roots[name]) != Path(root):
                    raise em.Refusal('FRONTEND_EXTERNAL_ROOT_CONTRADICTION', name)
                roots[name] = root
    files, populations = {}, []
    for name, root in sorted(roots.items()):
        name = nonempty(name, 'external_input_roots key')
        if (not re.fullmatch('[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name)
                or any(part in ('.', '..') for part in Path(name).parts)):
            raise em.Refusal('FRONTEND_EXTERNAL_NAME_UNSAFE', name)
        if not isinstance(root, (str, os.PathLike)) or not Path(root).is_absolute():
            raise em.Refusal('FRONTEND_EXTERNAL_ROOT_UNBOUND', name)
        prefix = 'external/' + name
        if any(prefix.startswith(prior+'/') or prior.startswith(prefix+'/')
               for prior, _, _ in populations):
            raise em.Refusal('FRONTEND_EXTERNAL_NAMES_OVERLAP', name)
        extra, population = lexical_tree(Path(root), prefix)
        files.update(extra)
        populations.append((prefix, str(Path(root).absolute()), stable(population)))
    return files, tuple(populations)



def producer_parameters(project: Path, step_id: str, parameters: dict) -> dict:
    """Plumb the existing complete producers; never invent an alternative."""
    values = dict(parameters)
    if step_id == '4':
        import librelane_contract as librelane
        mode = librelane.selected_mode(project, '4')
        if mode not in ('direct', 'dual'):
            raise em.Refusal('FRONTEND_SIMULATION_PROVIDER_UNAVAILABLE', mode)
        if values.get('simulation_mode', mode) != mode:
            raise em.Refusal('FRONTEND_SIMULATION_MODE_CONTRADICTION', mode)
        # The professional TB caller already compares the same cocotb bundle
        # in dual mode. Reference/unit TB and coverage remain complementary.
        values['simulation_mode'] = mode
    for sid, key, supported, reason in (
            ('11', 'dft_engine', 'fault',
             'the current complete scan/ATPG/BSDL caller is Fault; partial scan is not full DFT'),
            ('12', 'post_dft_engine', 'yosys-opt-clean',
             'no current LibreLane Resynthesis caller maps this scan input to the post-DFT output and scan-survival consumer')):
        if step_id == sid:
            requested = values.get(key, supported)
            if requested != supported:
                raise em.Refusal('FRONTEND_COMPLETE_PROVIDER_UNAVAILABLE',
                                 stable({'step_id': sid, 'requested': requested, 'reason': reason}))
            values[key] = supported
    return values


def required_gates(step_id: str) -> tuple[str, ...]:
    # F1 owns the canonical clause population and enforcement. Include the
    # actual optional/advisory programs before the common plan is issued.
    from execution_production import canonical_gate_population
    return canonical_gate_population(step_id, ('canonical_step_consumer', 'native_producer'))


def prepare_frontend(request, PreparedStep, *, classification=False):
    sid = request.step_id
    project = Path(request.project).resolve(strict=True)
    p = dict(request.parameters)
    design_input = project/'phase1/input_doc/design.md'
    source_step1 = (sid == '1' and design_input.is_file() and not design_input.is_symlink()
                    and not isinstance(p.get('native'), dict))
    source_d1 = sid == 'D1'
    source_05ic = False
    owner_answers = None
    if sid == '0.5ic':
        answers_path = project/'input/step_0_5ic_answers.json'
        if answers_path.is_symlink() or not answers_path.is_file():
            raise em.Refusal('FRONTEND_OWNER_TAPEOUT_ANSWERS_MISSING', str(answers_path))
        try:
            owner_answers = json.loads(answers_path.read_text())
        except (OSError, ValueError) as exc:
            raise em.Refusal('FRONTEND_OWNER_TAPEOUT_ANSWERS_INVALID', str(exc)) from exc
        if (owner_answers.get('schema') != 'vibe-ic/step_0_5ic_answers/1'
                or not isinstance(owner_answers.get('answers'), dict)
                or not isinstance(owner_answers.get('operator_template'), dict)
                or owner_answers['answers'].get('deliverable') not in ('DIE', 'HARDMACRO')):
            raise em.Refusal('FRONTEND_OWNER_TAPEOUT_ANSWERS_INVALID', str(answers_path))
        owner_route = owner_answers['answers']['deliverable']
        supplied_route = p.get('route')
        if supplied_route is not None and supplied_route != owner_route:
            raise em.Refusal('FRONTEND_DELIVERY_ROUTE_CONTRADICTS_OWNER', sid)
        p['route'] = owner_route
        owner_template = owner_answers['operator_template']
        if owner_template.get('path') is None:
            absent_reason = nonempty(owner_template.get('absent_reason'),
                                     'owner_answers.operator_template.absent_reason')
            if p.get('no_template_reason') not in (None, absent_reason) or p.get('template') is not None:
                raise em.Refusal('FRONTEND_TEMPLATE_HANDOFF_CONTRADICTS_OWNER', sid)
            p['no_template_reason'] = absent_reason
        elif p.get('template') != owner_template.get('path'):
            raise em.Refusal('FRONTEND_TEMPLATE_HANDOFF_CONTRADICTS_OWNER', sid)
        source_05ic = True
    source_software = source_step1 or source_d1 or source_05ic
    route = ('SOURCE_AUTHORING' if source_step1 else
             'SOURCE_EXTRACTION' if source_d1 else
             p['route'] if source_05ic else nonempty(p.get('route'), 'route'))
    if not source_software and route not in ('DIE', 'HARDMACRO'):
        raise em.Refusal('FRONTEND_DELIVERY_ROUTE_INVALID', route)
    objective = (p.get('objective') if isinstance(p.get('objective'), dict) and p.get('objective') else
        {'operation': 'author_step1_rtl_from_consumed_design_input',
         'step_id': '1', 'direction': 'author'} if source_step1 else
        {'operation': 'extract_phase1_documents', 'step_id': 'D1', 'direction': 'extract'} if source_d1 else
        {'operation': 'ingest_owner_submission_template_and_declaration',
         'step_id': '0.5ic', 'direction': 'ingest'} if source_05ic else p.get('objective'))
    if not isinstance(objective, dict) or not objective:
        raise em.Refusal('FRONTEND_OBJECTIVE_MISSING', sid)
    row = contract(sid)
    # F1 prepares the complete common manifest. Every arm uses it unchanged;
    # the domain refuses missing dependencies instead of extending the map.
    own_sources = dict(request.source_files)
    required_sources = source_files()
    if sid == '1':
        required_sources.update(rtl_expert_sources(p))
    for name, expected in required_sources.items():
        if own_sources.get(name) != expected:
            raise em.Refusal('FRONTEND_REQUEST_DEPENDENCY_UNBOUND', name)
    source_current(request.source_sha, own_sources)
    roots = input_roots(project, dict(p, step_id=sid))
    inputs, pop = lexical_tree(project, roots=roots)
    if not inputs:
        if classification:
            return PreparedStep(None, None, None, disposition='current_input_missing',
                reason='current consumed input population is empty',
                handoff={'facts': {'step_id': sid, 'population': pop,
                                   'source_sha': request.source_sha,
                                   'source_files': own_sources,
                                   'design_verdict': 'NOT_MEASURED'}})
        raise em.Refusal('FRONTEND_INPUT_EMPTY', sid)
    if source_step1 and 'project/phase1/input_doc/design.md' not in inputs:
        raise em.Refusal('FRONTEND_STEP1_DESIGN_INPUT_NOT_CONSUMED', str(design_input))
    external_files, external = external_inputs(project, p)
    inputs.update(external_files)
    journal = project/'provenance.jsonl'
    journal_checkpoint = {'provenance.jsonl': 'ABSENT'}
    if journal.exists():
        if journal.is_symlink() or not journal.is_file():
            raise em.Refusal('FRONTEND_UPSTREAM_JOURNAL_UNSAFE', str(journal))
        journal_checkpoint['provenance.jsonl'] = em.digest(journal)
    if not inputs:
        if classification:
            return PreparedStep(None, None, None, disposition='current_input_missing',
                reason='current consumed input population is empty after exclusions',
                handoff={'facts': {'step_id': sid, 'population': pop,
                                   'source_sha': request.source_sha,
                                   'source_files': own_sources,
                                   'design_verdict': 'NOT_MEASURED'}})
        raise em.Refusal('FRONTEND_INPUT_EMPTY', sid)
    def current_handoff(facts):
        handoff = declaration_handoff(project, p, facts)
        declaration = Path(handoff['declaration'])
        if declaration not in inputs.values():
            raise em.Refusal('FRONTEND_DECLARATION_NOT_IN_STEP_INPUTS', str(declaration))
        _, current = lexical_tree(project, roots=roots)
        if stable(current) != stable(pop):
            raise em.Refusal('FRONTEND_INPUT_POPULATION_CHANGED', str(project))
        for prefix, root, expected in external:
            _, current_external = lexical_tree(Path(root), prefix)
            if stable(current_external) != expected:
                raise em.Refusal('FRONTEND_EXTERNAL_POPULATION_CHANGED', root)
        handoff['facts']['external_populations'] = external
        source_current(request.source_sha, own_sources)
        return handoff
    # Inapplicability comes from the existing canonical declaration evaluator,
    # including its nonempty design-input evidence. Missing L20 never means off.
    import flow_compliance_check as flow
    condition = row.get('condition') or {}
    if condition.get('l_doc_declares'):
        decl = flow._l_doc_declares_absence(project, condition['l_doc_declares'])
        if decl is not None and not flow._check_condition(project, condition):
            handoff = current_handoff({'route': route, 'step_id': sid,
                'absence_evidence': decl, 'population': pop, 'source_sha': request.source_sha,
                'source_files': own_sources, 'design_verdict': 'NOT_APPLICABLE'})
            if Path(handoff['declaration']).resolve() != (project/decl[0]).resolve():
                raise em.Refusal('FRONTEND_ABSENCE_DECLARATION_NOT_REQUESTED', str(decl[0]))
            return PreparedStep(None, None, None, disposition='declared_inapplicable',
                                reason=str(decl[1]), handoff=handoff)
    if sid == '0.5ic' and route == 'HARDMACRO':
        import tapeout_declaration_check
        result = tapeout_declaration_check.evaluate(project)
        if result.get('verdict') not in ('PASS', 'NOT_APPLICABLE'):
            raise em.Refusal('FRONTEND_ROUTE_DECLARATION_UNPROVEN', stable(result))
        handoff = current_handoff({'route': route, 'population': pop,
            'canonical_declaration_check': result, 'source_sha': request.source_sha,
            'design_verdict': 'NOT_APPLICABLE'})
        from _tapeout_declaration import DECLARATION_REL
        if Path(handoff['declaration']).resolve() != (project/DECLARATION_REL).resolve():
            raise em.Refusal('FRONTEND_ROUTE_DECLARATION_NOT_REQUESTED', handoff['declaration'])
        return PreparedStep(None, None, None, disposition='declared_inapplicable',
            reason='current owner-authored HARDMACRO delivery declaration', handoff=handoff)
    if sid == '6' and p.get('physical_handoff') is not None:
        path = physical_declaration(project, p['physical_handoff'])
        handoff = current_handoff({'route': route, 'population': pop,
            'board_present': False, 'answered_by': 'owner', 'source_sha': request.source_sha,
            'design_verdict': 'NOT_MEASURED', 'owed_outputs': row['required_outputs']})
        if Path(handoff['declaration']).resolve() != path.resolve():
            raise em.Refusal('FRONTEND_PHYSICAL_DECLARATION_NOT_REQUESTED', str(path))
        return PreparedStep(None, None, None, disposition='external_handoff',
            reason='owner declares board absent; Quartus/board evidence remains unmeasured',
            handoff=handoff)
    p = producer_parameters(project, sid, p)
    for name in roots:
        if name != 'provenance.jsonl' and any(
                name == x or name.startswith(x.rstrip('/')+'/') or fnmatch.fnmatchcase(name, x)
                for x in exclusions(sid)):
            raise em.Refusal('FRONTEND_CONSUMED_INPUT_IS_MUTABLE_OUTPUT', name)
    inputs, pop = lexical_tree(project, roots=roots, exclusions=exclusions(sid))
    if not inputs:
        raise em.Refusal('FRONTEND_INPUT_EMPTY', sid)
    if source_step1:
        input_assessment = {'verdict': 'NOT_MEASURED',
            'scope': 'Step1 program-first source authoring consumes only the bound design document',
            'consumed_input': 'phase1/input_doc/design.md',
            'sha256': em.digest(inputs['project/phase1/input_doc/design.md'])}
    else:
        input_assessment = validate_step_inputs(project, sid, roots, inputs)
    inputs.update(external_files)
    native = p.get('native')
    if not source_software:
        if not isinstance(native, dict):
            raise em.Refusal('FRONTEND_NATIVE_IDENTITY_MISSING', sid)
        image_current(nonempty(native.get('image_id'), 'native.image_id'))
        tools = native.get('tool_files')
        if not isinstance(tools, dict) or not tools or any(
                not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in tools.values()):
            raise em.Refusal('FRONTEND_NATIVE_TOOL_IDENTITIES_MISSING', sid)
    if source_software:
        facts_file = facts_sha = None
        deadline = 300
    else:
        facts_file, facts_sha = native_facts(p)
        deadline = native_deadline(p)
    if classification:
        return PreparedStep(None, None, None, disposition='execute',
            reason='source-owned classification selected execution',
            handoff={'facts': {'step_id': sid, 'route': route,
                               'source_software': source_software,
                               'source_sha': request.source_sha,
                               'source_files': own_sources,
                               'population': pop,
                               'design_verdict': 'NOT_MEASURED'}})
    quota = lease_current(Path(request.lease), p)
    if not source_step1 and sid not in ('D1', '0.5ic'):
        top = nonempty(p.get('top'), 'top')
        if not re.fullmatch('[A-Za-z_$][A-Za-z0-9_$]*', top):
            raise em.Refusal('FRONTEND_TOP_UNSAFE', top)
    record = Path(request.record).resolve()
    if record.is_relative_to(project):
        raise em.Refusal('FRONTEND_REQUEST_INSIDE_INPUT_PROJECT', str(record))
    if journal.exists():
        snapshot = record.with_name(record.name+'.upstream-journal')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with snapshot.open('xb') as f:
            f.write(journal.read_bytes())
        snapshot.chmod(0o444)
        if em.digest(snapshot) != journal_checkpoint['provenance.jsonl']:
            raise em.Refusal('FRONTEND_UPSTREAM_JOURNAL_CHANGED', str(journal))
        inputs['project/provenance.jsonl'] = snapshot
    lease_file = Path(request.lease) / 'lease.json'
    if facts_file is not None:
        inputs['native_facts.json'] = facts_file
    spec = {'schema': 1, 'step_id': sid, 'parameters': path_parameters(p), 'source_sha': request.source_sha,
            'source_files': own_sources, 'project': str(project), 'population': pop,
            'input_roots': roots, 'adoption_paths': adoption_paths(sid),
            'required_input_assessment': input_assessment,
            'external_populations': external, 'lease_path': str(lease_file),
            'lease_directory': str(request.lease), 'quota': quota,
            'input_hashes': {k: em.digest(v) for k, v in inputs.items()},
            'source_step1': source_step1, 'source_software': source_software,
            'canonical_row': row, 'route': route, 'callables': ROUTES[sid],
            'engine_families': ENGINES[sid]}
    write(record, spec)
    inputs['request.json'] = record
    inputs['lease.json'] = lease_file
    policy_row = next(r for r in em.load_portfolio()['steps'] if r['id'] == sid)
    gates = required_gates(sid)
    context = FrontendContext(sid, request.source_sha, inputs, objective, gates,
        native_mode='direct', project=project, roots=roots,
        exclusions=exclusions(sid), population=stable(pop),
        resources=((str(lease_file), em.digest(lease_file)), (str(record), em.digest(record))) +
                  (((str(facts_file), facts_sha),) if facts_file is not None else ()),
        sources=tuple(sorted(own_sources.items())), external_populations=tuple(external),
        journal_current=journal_checkpoint)
    native_family = next((x for x in ENGINES[sid] if not x.startswith('vibeic-')), None)
    adapter = em.Adapter('frontend-' + sid.replace('.', '-'), native_family or 'vibeic',
        sid, request.source_sha, own_sources,
        'software-step1-source-authoring' if source_step1 else
            'software-phase1-extraction' if source_d1 else
            'software-owner-declaration-ingest' if source_05ic else native['image_id'],
        ('vibeic-rtl-dispatch',) if source_step1 else
            ('vibeic-phase1-extraction',) if source_d1 else
            ('vibeic-owner-declaration-ingest',) if source_05ic else ENGINES[sid],
        (em.Component('frontend-producer', (str(Path(sys.executable).resolve()), str(Path(__file__).resolve()),
            '--phase', 'launch', '--inputs', '{inputs}', '--outputs', '{outputs}'), deadline + 30),),
        validate_frontend, ('producer.json', 'canonical-consumer.json') if source_software else
            ('producer.json', 'native.json', 'canonical-consumer.json'), objective,
        qualification_evidence=('issued host software callback; native evidence remains unmeasured' if source_step1 else
            'source-owned software producer; native evidence remains unmeasured' if source_software else
            'source-owned existing program routes; current engine/output/gates reconsumed; native design verdict is independent'),
        own_no_tool_reason=('Design-intent extraction/declarations/constraint generation are not an interchangeable external EDA producer'
                            if native_family is None else ''),
        cpus=int(quota['cpus']), ram_mb=int(quota['host_ram_mb']),
        output_contract={key: ('producer.json', 'canonical-consumer.json')
                         for key in policy_row['required_output_contract']})
    registry = em.Registry()
    registry.register(adapter)
    return PreparedStep(context, registry, consume_frontend, adoption_paths=adoption_paths(sid))


def cli(module: str, argv: list[str]) -> int:
    """Existing program CLI; no shell expansion, dynamic module or parent main."""
    cmd = [sys.executable, str(PROGRAMS / (module + '.py')), *argv]
    start = time.monotonic_ns()
    cp = subprocess.run(cmd, capture_output=True, text=True)
    log = {'program': module, 'argv': cmd, 'rc': cp.returncode,
           'started_ns': start, 'ended_ns': time.monotonic_ns(),
           'stdout': cp.stdout, 'stderr': cp.stderr,
           'program_sha256': em.digest(PROGRAMS / (module + '.py'))}
    with (Path(os.environ['F2_OUTPUT_ROOT']) / 'commands.jsonl').open('a') as f:
        f.write(stable(log) + '\n')
    return cp.returncode


def normalize(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [normalize(x) for x in value]
    return value


def status(value):
    if isinstance(value, list):
        states = [status(x) for x in value]
        return 'FAIL' if 'FAIL' in states else 'PASS' if states and all(x == 'PASS' for x in states) else 'NOT_MEASURED'
    if isinstance(value, dict):
        if value.get('passed') is False or value.get('status') == 'FAIL' or value.get('verdict') == 'FAIL':
            return 'FAIL'
        if value.get('not_measured'):
            return 'NOT_MEASURED'
        if value.get('passed') is True:
            return 'PASS'
        result = value.get('status', value.get('verdict'))
        return result if result in ('PASS', 'FAIL') else 'NOT_MEASURED'
    return 'PASS' if type(value) is int and value == 0 else 'FAIL' if type(value) is int and value == 1 else 'NOT_MEASURED'


def pdk_config(p):
    import phase3_one_shot_runner as runner
    obj = p.get('pdk')
    if isinstance(obj, runner.PdkConfig):
        if not obj.name or not obj.liberty:
            raise em.Refusal('FRONTEND_PDK_DECLARATION_MISSING', '')
        return obj
    if not isinstance(obj, Mapping) or not obj.get('name') or not obj.get('liberty'):
        raise em.Refusal('FRONTEND_PDK_DECLARATION_MISSING', '')
    try:
        return runner.PdkConfig(**obj)
    except TypeError as exc:
        raise em.Refusal('FRONTEND_PDK_DECLARATION_INVALID', str(exc)) from exc


def rtl_dispatch(project: Path, parameters: dict, *, issued_binding: dict | None = None):
    """Keep the existing program-first dispatch and its exact expert handoff."""
    import design_one_shot_runner as runner
    force = parameters.get('force_regen')
    if force is not None and type(force) is not bool:
        raise em.Refusal('FRONTEND_RTL_REGEN_INVALID', 'force_regen')
    # This producer owns an isolated project. The runner's staged transaction
    # may recopy unchanged authored files while adding its handoff documents.
    # Preserve those files' metadata without undoing any actual RTL change.
    rtl = project/'phase2/stage1/rtl'
    previous = {}
    if rtl.is_dir() and not rtl.is_symlink():
        for path in rtl.rglob('*'):
            if path.is_file() and not path.is_symlink() and path.resolve() == path:
                previous[path] = (em.digest(path), path.stat())
    if issued_binding is None:
        raw = runner.step_rtl_gen(project,
            nonempty(parameters.get('ic_class'), 'ic_class'), force_regen=force)
    else:
        current = json.loads(os.environ.get('VIBEIC_EXECUTION_BINDING', '{}'))
        if (issued_binding.get('step_id') != '1' or current != issued_binding):
            raise em.Refusal('FRONTEND_PRODUCER_REENTRY_UNBOUND', 'current Step1 binding')
        # The F1 issuer proves the worker PID, current plan/source and frozen
        # INPUT population over its live callback channel. Its scope restores
        # the authorized callable for this arm without native-marker reentry.
        callback = em.current_step1_callback()
        scope = (em.issued_step1_callback(project) if callback is None
                 else __import__('contextlib').nullcontext(callback))
        with scope:
            raw = runner.step_rtl_gen(project,
                nonempty(parameters.get('ic_class'), 'ic_class'), force_regen=force)
    raw = normalize(raw)
    # A body-only Step1 caller can reach the real production frontdoor without
    # an issued input manifest. Preserve that refusal as NM while handing the
    # exact shipped authoring skill back to the caller; this creates no design,
    # adoption, gate, or native credit and never substitutes for an issued run.
    preflight_detail = str(raw.get('detail', ''))
    if (raw.get('status') == 'NOT_MEASURED'
            and any(reason in preflight_detail for reason in
                    ('FRONTEND_STEP_INPUT_ROOTS_MISSING', 'RESOURCE_HOST_CAPACITY_UNAVAILABLE'))
            and not isinstance(raw.get('expert_handoff'), dict)):
        hint, staged = runner._stage_fallback_skill(project, 'spec-to-rtl')
        skill_source = canonical_path(staged.get('fallback_skill_source'), 'fallback_skill_source')
        if em.digest(skill_source) != staged.get('fallback_skill_sha256'):
            raise em.Refusal('FRONTEND_EXPERT_SKILL_CHANGED', str(skill_source))
        handoff = {'skill': 'spec-to-rtl', 'design_verdict': 'NOT_MEASURED',
                   'reason': preflight_detail, 'runner_detail': preflight_detail,
                   'runner_extras': staged}
        if staged.get('fallback_skill_staged') is True:
            staged_path = canonical_path(staged.get('fallback_skill_path'), 'fallback_skill_path')
            if (not staged_path.is_relative_to(project)
                    or em.digest(staged_path) != staged.get('fallback_skill_sha256')):
                raise em.Refusal('FRONTEND_EXPERT_SKILL_CHANGED', str(staged_path))
            handoff.update(staged_path=str(staged_path), staged_sha256=em.digest(staged_path),
                           source_path=str(skill_source), source_sha256=em.digest(skill_source))
        else:
            handoff['reason'] = staged.get('fallback_skill_unstaged_reason') or handoff['reason']
        raw['expert_handoff'] = handoff
        raw['detail'] = str(raw.get('detail', '')) + hint
    for path, (expected, metadata) in previous.items():
        if (path.is_file() and not path.is_symlink() and path.resolve() == path
                and em.digest(path) == expected):
            os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    extras = raw.get('extras') or {}
    # When this callback is reached from an issued Step-1 arm, the production
    # seam returns the validated producer payload as structured StepResult
    # extras. Preserve that actual payload before looking for the direct
    # program-first result fields below; never reconstruct it from a verdict.
    issued_raw = extras.get('raw_producer')
    if isinstance(issued_raw, dict):
        raw['raw_producer'] = issued_raw
        issued_handoff = extras.get('expert_handoff')
        if not isinstance(issued_handoff, dict):
            issued_handoff = issued_raw.get('expert_handoff')
        if isinstance(issued_handoff, dict):
            raw['expert_handoff'] = issued_handoff
    skill = extras.get('fallback_skill')
    if skill and raw.get('status') in ('PASS_WITH_WAIVERS', 'WAIVED', 'WAIVED-DEFERRED',
                                       'NOT_MEASURED'):
        handoff = {'skill': skill, 'design_verdict': 'NOT_MEASURED',
                   'reason': 'the current runner requests independent expert authoring',
                   'runner_detail': raw.get('detail'), 'runner_extras': extras}
        staged = extras.get('fallback_skill_path')
        if extras.get('fallback_skill_staged') is True:
            path = canonical_path(staged, 'fallback_skill_path')
            if not path.is_relative_to(project) or em.digest(path) != extras.get('fallback_skill_sha256'):
                raise em.Refusal('FRONTEND_EXPERT_SKILL_CHANGED', str(path))
            source = canonical_path(extras.get('fallback_skill_source'), 'fallback_skill_source')
            if em.digest(source) != em.digest(path):
                raise em.Refusal('FRONTEND_EXPERT_SKILL_CHANGED', str(source))
            handoff.update(staged_path=str(path), staged_sha256=em.digest(path),
                           source_path=str(source), source_sha256=em.digest(source))
        else:
            handoff['reason'] = extras.get('fallback_skill_unstaged_reason') or 'current skill bytes not staged'
        raw['expert_handoff'] = handoff
    return raw


def produce(project: Path, sid: str, p: dict, *, issued_binding: dict | None = None):
    """One frontend row only, on a source-owned isolated output project."""
    p = producer_parameters(project, sid, p)
    import design_one_shot_runner as d
    top = p.get('top')
    container = 'frontend-already-in-admitted-image'
    if sid == 'D1':
        return normalize(d.step_phase1(project))
    if sid == '0.5ic':
        # Owner answers are INPUT, never authored by this adapter. Fetching a
        # remote shuttle archive is an external staged-input handoff.
        answers = project / 'input/step_0_5ic_answers.json'
        if not answers.is_file():
            raise em.Refusal('FRONTEND_OWNER_TAPEOUT_ANSWERS_MISSING', str(answers))
        args = [str(project)]
        template = p.get('template')
        if template:
            args += ['--template', str(template)]
        elif p.get('no_template_reason'):
            args += ['--no-template-reason', p['no_template_reason']]
        else:
            raise em.Refusal('FRONTEND_TEMPLATE_HANDOFF_REQUIRED', str(project))
        rc = cli('submission_template_ingest', args)
        if rc != 0:
            return rc
        declaration_rc = cli('tapeout_declaration_gen', [str(project), '--answers', str(answers)])
        return {'status': status(declaration_rc), 'ingest_rc': rc, 'declaration_rc': declaration_rc}
    if sid == '1':
        return rtl_dispatch(project, p, issued_binding=issued_binding)
    if sid == '2':
        import p0_tool_frontend_check
        lint = p0_tool_frontend_check.check(project)
        # The rewrite baseline is a consumed upstream input, never a fresh
        # copy of the candidate (which would prove identity against itself).
        baseline = nonempty(p.get('baseline_rtl_dir'), 'baseline_rtl_dir')
        baseline_path = Path(baseline)
        baseline_path = baseline_path if baseline_path.is_absolute() else project/baseline_path
        if baseline_path.resolve() == (project/'phase2/stage1/rtl').resolve():
            raise em.Refusal('FRONTEND_REWRITE_BASELINE_IS_CANDIDATE', baseline)
        equivalence = cli('crosslayer_rewrite_equivalence', [str(project),
            '--baseline-rtl-dir', str(baseline_path), '--candidate-rtl-dir', str(project/'phase2/stage1/rtl'),
            '--top', top, '--clock', nonempty(p.get('clock'), 'clock'),
            '--timeout', str(p['timeout_s'])])
        return {'status': 'PASS' if lint.get('passed') is True and equivalence == 0 else 'FAIL',
                'lint': lint, 'rewrite_rc': equivalence}
    if sid == '3':
        import librelane_contract
        mode = librelane_contract.selected_mode(project, '3')
        if mode != 'direct':
            import _cdc_netlist
            import _specrtl_common as sources
            from _chip_synth_read import NON_SILICON_SUBSTRS
            rtl_files = [f for f in sources.rtl_source_files(project)
                         if not any(t in f.name.lower() for t in NON_SILICON_SUBSTRS)]
            native = p.get('native') if isinstance(p.get('native'), dict) else {}
            _cdc_netlist.build(project, rtl_files, top,
                               image=native.get('image_id'))
        reports = ('crossing', 'async_input', 'reg_crossing', 'reset_dep')
        return [cli(mod.split('.')[0], [str(project), '--json',
                str(project/'reports/phase2/cdc'/f'{report}.json')])
                for mod, report in zip(ROUTES[sid][-4:], reports)]
    if sid == '4':
        generated = normalize(d.step_professional_tb_gen(project, top, container))
        sim = normalize(d.step_reference_tb(project, top, p.get('ic_class'), container))
        units = normalize(d.step_l10_unit_tb_run(project, container))
        # Existing coverage engine consumes actual TBs, not JUnit counts.
        coverage = cli('verilator_coverage_measure', ['measure-tb', '--project', str(project),
            '--build-jobs', str(int(p['build_jobs'])), '--out',
            str(project/'reports/phase2/coverage/coverage_verilator.json')])
        return [generated, sim, units, coverage]
    if sid == '5':
        import formal_harness_gen as harness
        import formal_property_run as proof
        authored = harness.generate(project, top=top, container=container)
        formal = proof.run(project, top=top, container=container,
            timeout=int(p['timeout_s']), mem_limit_kb=int(p['mem_limit_kb']),
            prove_engines=nonempty(p.get('prove_engines'), 'prove_engines'))
        stack = normalize(d.step_full_stack_functional_tb(project, container))
        return {'status': status([normalize(formal), stack]),
                'authored_properties': normalize(authored), 'proof': normalize(formal), 'full_stack': stack}
    if sid == '6':
        compiled = normalize(d.step_fpga_compile(project, top, container))
        audit = cli('quartus_map_audit', ['--project', str(project), '--json',
                    str(project/'reports/phase2/fpga/quartus_map_audit.json')])
        return [compiled, audit]
    if sid == '7':
        import phase3_one_shot_runner as r
        from _ppa.timing import emit_step7_asic_sdc
        pdk = pdk_config(p)
        emitted = emit_step7_asic_sdc(r, project, top, pdk, container)
        libs = sorted((project/'input/pdk/liberty').glob('*.lib'))
        if len(libs) < 2:
            return {'status': 'NOT_MEASURED', 'why': 'fewer than two current staged Liberty corners', 'sdc': emitted}
        corners = [{'name': lib.stem, 'label': r._classify_corner_from_name(lib.name),
                    'liberty': str(lib.relative_to(project))} for lib in libs]
        matrix = dict(r._PVT_MATRIX_TEMPLATE, corners=corners, primary_corner='TT')
        r.stamp_pvt_corner_coverage(matrix, corners)
        write(project/'phase2/stage2/constraints/pvt_matrix.json', matrix)
        return {'status': 'PASS', 'sdc': emitted, 'matrix': matrix}
    if sid == '8':
        return [cli('sdc_syntax_check', [str(project), '--json', str(project/'reports/phase2/sdc_check.json')]),
                cli('sdc_validator_check', [str(project), '--l8',
                    str(project/'phase1/generated_docs/L8_TIMING_WAVEFORM.json'), '--json', str(project/'reports/sdc_validator.json')])]
    if sid == '10':
        import phase3_one_shot_runner as r
        return normalize(r.step_prelayout_signoff(project, top, pdk_config(p), container))
    if sid == '11':
        base = [str(project), '--netlist', nonempty(p.get('netlist'), 'netlist'),
                '--clock', nonempty(p.get('clock'), 'clock'), '--pdk', nonempty(p.get('pdk_name'), 'pdk_name')]
        if p.get('reset'):
            base += ['--reset', p['reset']]
        if p.get('reset_active_low') is True:
            base += ['--reset-active-low']
        chain = cli('fault_scan_chain_insert', base + ['--top-module', top,
            '--liberty', nonempty(p.get('liberty'), 'liberty'), '--timeout', str(p['timeout_s'])])
        if chain:
            return chain
        atpg = cli('fault_atpg_run', base + ['--cell-model-path', nonempty(p.get('cell_model'), 'cell_model'),
                    '--no-transition', '--json', str(project/'reports/phase2/dft/coverage.json')])
        return [chain, atpg, cli('bsdl_emit', [str(project), '--json', str(project/'reports/phase2/dft/bsdl_plan.json')])]
    if sid == 'FS1':
        return cli('fmeda_fault_injection_coverage', [str(project), '--rtl-dir',
                   str(project/'phase2/stage1/rtl'), '--asil', nonempty(p.get('asil'), 'asil'),
                   '--json', str(project/'reports/phase2/safety/fmeda_coverage.json')])
    if sid == 'DT1':
        return cli('transition_fault_atpg_run', [str(project), '--clock', nonempty(p.get('clock'), 'clock'),
            '--netlist', nonempty(p.get('netlist'), 'netlist'), '--liberty', nonempty(p.get('liberty'), 'liberty'),
            '--top', top, '--max-faults', str(p['max_faults']), '--sat-timeout', str(p['sat_timeout_s']),
            '--timeout', str(p['timeout_s']), '--json', str(project/'reports/phase2/dft/transition_coverage.json')])
    if sid == '12':
        scan = project/'phase2/stage2/dft/scan_netlist.v'
        out = project/'phase2/stage2/synth/post_dft_netlist.v'
        out.parent.mkdir(parents=True, exist_ok=True)
        # Exactly the existing Step-12 producer, separated from the full
        # DFT chain. Scan-survival is an additional mandatory real consumer.
        script = f'read_verilog "{scan}"; opt_clean -purge; write_verilog -noattr "{out}"'
        cp = subprocess.run(['yosys', '-p', script], capture_output=True, text=True)
        return {'status': status(cp.returncode), 'argv': ['yosys', '-p', script],
                'rc': cp.returncode, 'stdout': cp.stdout, 'stderr': cp.stderr}
    if sid == '13':
        return normalize(d.step_lec_equivalence(project, top, container,
                         lec_max_completed_rungs=int(p['lec_max_completed_rungs'])))
    if sid in ('DT2', 'DT3'):
        module = 'path_delay_fault_atpg_run' if sid == 'DT2' else 'sdd_atpg_run'
        args = [str(project), '--clock', nonempty(p.get('clock'), 'clock'),
                '--netlist', nonempty(p.get('netlist'), 'netlist'),
                '--sdc', nonempty(p.get('sdc'), 'sdc'), '--spef', nonempty(p.get('spef'), 'spef'),
                '--liberty', nonempty(p.get('liberty'), 'liberty'), '--top', top,
                '--timeout', str(p['timeout_s']), '--json', str(project/'reports/phase2/dft'/
                    ('path_delay_coverage.json' if sid == 'DT2' else 'sdd_coverage.json'))]
        return cli(module, args)
    if sid == 'P0':
        import p0_tool_frontend_check as frontend
        result = frontend.check(project)
        if result.get('passed') is True:
            import formal_structural_check as downstream
            path = project/frontend.RTL_ELAB_REL
            doc = json.loads(path.read_text())
            claims = p.get('structural_claims') or []
            if not claims:
                raise em.Refusal('FRONTEND_STRUCTURAL_CLAIMS_MISSING', sid)
            result['selected_elaboration_consumers'] = [downstream.check_claim(doc, top, c) for c in claims]
            if any(x.get('verdict') != 'PASS' for x in result['selected_elaboration_consumers']):
                result['passed'] = False
        return result
    raise em.Refusal('FRONTEND_STEP_NOT_OWNED', sid)


def semantic_consumer(project: Path, sid: str) -> dict:
    import flow_compliance_check as flow
    first = len(flow._GATE_LEDGER)
    result = flow.check_step(project, contract(sid), {}, strict_step_binding=True)
    detail = normalize(result)
    detail['program_execution_records'] = normalize(flow._GATE_LEDGER[first:])
    # Carry the real clause tree and canonical enforcement records to F1.
    # Conditional not-run and advisory failures remain disclosed as measured.
    detail['canonical_row'] = contract(sid)
    return detail


def record_step_outputs(project: Path, sid: str) -> dict:
    """Observe the isolated producer's writes with the existing ledger.

    The index routes the current step's record; it never certifies a verdict.
    These are private generation artifacts, not canonical journals to publish
    over another step's records. Consumers reuse them instead of inventing
    attribution for files merely discovered by a gate.
    """
    import step_write_ledger as ledger
    index = project/'steps/index.json'
    if not index.exists():
        folder = 'frontend/' + sid.replace('.', '-')
        (project/'steps'/folder).mkdir(parents=True, exist_ok=True)
        write(index, {'steps': [{'id': sid, 'folder': folder}]})
    result = ledger.emit(project)
    if not result.get('ok'):
        raise em.Refusal('FRONTEND_STEP_WRITE_RECORD_UNMEASURED', stable(result))
    observed = json.loads((project/'reports/write_ledger.json').read_text())
    row = next((r for r in observed['steps'] if str(r['id']) == sid), None)
    if row is None:
        raise em.Refusal('FRONTEND_STEP_WRITE_RECORD_MISSING', sid)
    return {'ledger_result': result, 'step_observation': row}


def design_verdict(producer: str, canonical: str) -> str:
    if 'FAIL' in (producer, canonical):
        return 'FAIL'
    return 'PASS' if producer == canonical == 'PASS' else 'NOT_MEASURED'


def measured_program_gates(consumer: dict, required: tuple[str, ...]) -> dict:
    """Report executed programs; an aggregate PASS cannot certify skipped gates."""
    import shlex
    measured = {name: 'NOT_MEASURED' for name in required}
    for name in required:
        rows = []
        for row in consumer.get('program_execution_records', []):
            parts = shlex.split(row.get('cmd', ''))
            if parts and Path(parts[0]).stem == name:
                rows.append(row)
        if rows and all(row.get('verdict') == 'PASS' and row.get('exit_code') == 0
                        for row in rows):
            measured[name] = 'PASS'
        # The canonical result owns advisory and conditional enforcement.
        # A nonblocking advisory FAIL cannot become a blocking design FAIL.
        elif consumer.get('status') == 'FAIL' and any(
                row.get('verdict') == 'FAIL' for row in rows):
            measured[name] = 'FAIL'
    return measured


def require_consumer_pass(semantic: dict, original_verdict: str) -> None:
    if semantic.get('status') != 'PASS':
        raise em.Refusal('GATE_FAIL' if semantic.get('status') == 'FAIL'
                         else 'GATE_NOT_MEASURED', stable(
            {'design_verdict': semantic.get('status'), 'consumer': semantic,
             'original_design_verdict': original_verdict}))


def output_manifest(root: Path) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise em.Refusal('FRONTEND_OUTPUT_ROOT_MISSING', str(root))
    result = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise em.Refusal('FRONTEND_OUTPUT_ALIAS', str(p))
        if p.is_file():
            result[str(p.relative_to(root))] = em.digest(p)
    return result


def validate_frontend(outputs: Path, binding: dict) -> em.Evidence:
    spec_path = outputs.parent/'inputs/request.json'
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, ValueError):
        spec = {}
    source_step1 = spec.get('source_step1') is True
    source_software = spec.get('source_software') is True
    gates = {k: 'NOT_MEASURED' for k in binding['required_gates']}
    required = ('producer.json', 'canonical-consumer.json') if source_step1 or source_software else (
        'producer.json', 'native.json', 'native-witness.json', 'canonical-consumer.json')
    if source_step1 or source_software:
        required += ('host-software.json',)
    if any(not (outputs/x).is_file() for x in required):
        return em.Evidence(binding, 'NOT_MEASURED', gates, {}, detail='substantive producer/consumer execution receipt missing')
    producer = json.loads((outputs/'producer.json').read_text())
    if producer.get('binding') is None or producer['binding'] != binding:
        raise em.Refusal('FRONTEND_PRODUCER_BINDING_MISMATCH', str(outputs))
    hashes = output_manifest(outputs)
    # The actual source-owned component prints its final complete manifest to
    # the stdout that Controller hashes and seals in issued-completion. Editing
    # output files and the editable receipt together cannot replace that log.
    log = outputs.parent/'frontend-producer.stdout'
    try:
        lines = [line[len('F2_OUTPUT_MANIFEST '):] for line in log.read_text().splitlines()
                 if line.startswith('F2_OUTPUT_MANIFEST ')]
        issued = json.loads(lines[-1]) if len(lines) == 1 else None
    except (OSError, ValueError):
        issued = None
    if not issued or issued.get('binding') != binding or issued.get('outputs') != hashes:
        raise em.Refusal('FRONTEND_OBSERVED_OUTPUT_MANIFEST_MISMATCH', str(outputs))
    actual = {k: v for k, v in hashes.items()
              if k != 'producer.json' and (source_step1 or source_software or k != 'native.json')}
    if not producer.get('outputs') or producer['outputs'] != actual:
        raise em.Refusal('FRONTEND_COMPLETE_OUTPUT_POPULATION_CHANGED', str(outputs))
    if source_step1:
        host = json.loads((outputs/'host-software.json').read_text())
        if (host.get('kind') != 'issued-host-software-step1' or host.get('binding') != binding
                or not isinstance(host.get('pid'), int) or host.get('pid') <= 0
                or host.get('rc') != 0 or host.get('native') != 'NOT_MEASURED'):
            raise em.Refusal('FRONTEND_HOST_SOFTWARE_RECEIPT_INVALID', str(outputs))
        raw = producer.get('raw_producer')
        if not isinstance(raw, dict):
            raise em.Refusal('FRONTEND_STEP1_PRODUCER_MISSING', str(outputs))
        semantic = json.loads((outputs/'canonical-consumer.json').read_text())
        handoff_pending = isinstance(raw.get('expert_handoff'), dict)
        if handoff_pending:
            producer_verdict = status(raw)
            measured = measured_program_gates(semantic, tuple(
                k for k in gates if k not in ('native_producer', 'canonical_step_consumer')))
            # A handoff does not erase an independently executed producer or
            # canonical-program failure. Missing candidate artifacts remain
            # NOT_MEASURED because they have no measured failing execution row.
            measured_failure = (producer_verdict == 'FAIL' or
                                any(value == 'FAIL' for value in measured.values()))
            candidate_root = outputs/'project/phase2/stage1/rtl'
            candidate_exists = (candidate_root.is_dir() and not candidate_root.is_symlink()
                and any(p.is_file() and not p.is_symlink() and p.suffix in ('.v', '.sv')
                        for p in candidate_root.iterdir()))
            if measured_failure:
                gates['native_producer'] = producer_verdict
                canonical = semantic.get('status')
                gates['canonical_step_consumer'] = (
                    canonical if canonical in ('PASS', 'FAIL') and
                    (canonical != 'FAIL' or any(v == 'FAIL' for v in measured.values()))
                    else 'NOT_MEASURED')
                gates.update(measured)
                candidate_verdict = design_verdict(
                    producer_verdict, gates['canonical_step_consumer'])
            else:
                candidate_verdict = 'NOT_MEASURED'
        else:
            producer_verdict = status(raw)
            canonical = semantic.get('status') if semantic.get('status') in ('PASS', 'FAIL') else 'NOT_MEASURED'
            gates['native_producer'] = producer_verdict
            gates['canonical_step_consumer'] = canonical
            gates.update(measured_program_gates(semantic, tuple(
                k for k in gates if k not in ('native_producer', 'canonical_step_consumer'))))
            candidate_verdict = design_verdict(producer_verdict, canonical)
        detail = {'scope': 'issued Step1 host software callback; host execution is distinct from native qualification',
            'host_software': host, 'raw_producer': raw,
            'expert_handoff': raw.get('expert_handoff'),
            'native_verdict': 'NOT_MEASURED'}
        if handoff_pending:
            detail['handoff_diagnostics'] = {
                'design_candidate_exists': candidate_exists,
                'observed_canonical_consumer': semantic,
                'canonical_gate_details_preserved': True}
            if measured_failure:
                detail['canonical_semantics'] = semantic
        else:
            detail['canonical_semantics'] = semantic
        return em.Evidence(binding, candidate_verdict, gates, hashes, detail=stable(detail))
    if source_software and spec.get('step_id') == '0.5ic':
        host = json.loads((outputs/'host-software.json').read_text())
        if (host.get('kind') != 'issued-host-software' or host.get('step_id') != '0.5ic'
                or host.get('binding') != binding or not isinstance(host.get('pid'), int)
                or host.get('pid') <= 0 or host.get('rc') != 0 or host.get('waited') is not True
                or host.get('native') != 'NOT_MEASURED' or not host.get('arm_id')):
            raise em.Refusal('FRONTEND_HOST_SOFTWARE_RECEIPT_INVALID', str(outputs))
        raw = producer.get('raw_producer')
        if (not isinstance(raw, dict) or raw.get('ingest_rc') != 0
                or not isinstance(raw.get('declaration_rc'), int)):
            raise em.Refusal('FRONTEND_05IC_PRODUCER_RECEIPT_INVALID', str(outputs))
        try:
            commands = [json.loads(line) for line in (outputs/'commands.jsonl').read_text().splitlines()]
        except (OSError, ValueError) as exc:
            raise em.Refusal('FRONTEND_05IC_COMMAND_LOG_INVALID', str(exc)) from exc
        expected_modules = ('submission_template_ingest', 'tapeout_declaration_gen')
        source_map = spec.get('source_files', {})
        if (len(commands) != len(expected_modules)
                or tuple(row.get('program') for row in commands) != expected_modules
                or tuple(row.get('rc') for row in commands) !=
                    (raw['ingest_rc'], raw['declaration_rc'])
                or any(row.get('argv', [None, None])[1] != str(PROGRAMS/(module+'.py'))
                       or source_map.get(str(PROGRAMS/(module+'.py'))) != em.digest(PROGRAMS/(module+'.py'))
                       or row.get('program_sha256') != em.digest(PROGRAMS/(module+'.py'))
                       or not isinstance(row.get('stdout'), str)
                       or not isinstance(row.get('stderr'), str)
                       for row, module in zip(commands, expected_modules))):
            raise em.Refusal('FRONTEND_05IC_COMMAND_LOG_INVALID', str(outputs))
        semantic, _ = reconsume(outputs/'project', binding, spec, outputs.parent)
        if output_manifest(outputs) != hashes:
            raise em.Refusal('FRONTEND_OUTPUT_CHANGED_DURING_CONSUMER', str(outputs))
        producer_verdict = status(raw)
        gates['native_producer'] = producer_verdict
        canonical = semantic.get('status')
        gates['canonical_step_consumer'] = canonical if canonical in ('PASS', 'FAIL') else 'NOT_MEASURED'
        gates.update(measured_program_gates(semantic, tuple(
            k for k in gates if k not in ('native_producer', 'canonical_step_consumer'))))
        verdict = design_verdict(producer_verdict, gates['canonical_step_consumer'])
        return em.Evidence(binding, verdict, gates, hashes, detail=stable({
            'scope': 'actual owner-answer-driven 0.5ic host software producer and canonical semantic consumer',
            'host_software': host, 'native_verdict': 'NOT_MEASURED',
            'raw_producer': raw, 'software_command_records': commands,
            'canonical_semantics': semantic,
            'candidate_gate_policy': 'canonical gates remain required; software execution is not native qualification'}))
    native = json.loads((outputs/'native.json').read_text())
    if (native.get('binding') != binding or not native.get('cid') or native.get('rc') != 0
            or native.get('cleanup_rc') != 0 or native.get('waited') is not True):
        raise em.Refusal('FRONTEND_NATIVE_IDENTITY_MISSING', str(outputs))
    try:
        witness = json.loads((outputs/'native-witness.json').read_text())
    except (OSError, ValueError) as exc:
        raise em.Refusal('FRONTEND_NATIVE_WITNESS_INVALID', str(outputs)) from exc
    native_binding = native.get('native_binding')
    native_client = native.get('native_client')
    if (not isinstance(witness, dict) or not isinstance(native_binding, dict)
            or not isinstance(native_client, dict)
            or witness != native.get('issuer_witness')
            or witness.get('binding') != binding
            or witness.get('inputs') != binding.get('inputs')
            or native_binding.get('step_id') != spec.get('step_id')
            or native_binding.get('source_sha256') != spec.get('source_sha')
            or native_binding.get('input_hashes') != binding.get('inputs')
            or native_binding.get('output_root') != str(outputs.resolve())
            or native_client.get('locator_env') != 'VIBEIC_NATIVE_CAPABILITY_SOCKET'
            or native_client.get('locator_value') != native_client.get('socket_container_path')
            or native_client.get('lease_sha256') != native.get('lease_sha256')
            or witness.get('lease_sha256') != native.get('lease_sha256')):
        raise em.Refusal('FRONTEND_NATIVE_WITNESS_BINDING_MISMATCH', str(outputs))
    raw = producer.get('raw_producer')
    native_verdict = status(raw)
    gates['native_producer'] = native_verdict
    spec = json.loads((outputs.parent/'inputs/request.json').read_text())
    consumer, _ = reconsume(outputs/'project', binding, spec, outputs.parent)
    if output_manifest(outputs) != hashes:
        raise em.Refusal('FRONTEND_OUTPUT_CHANGED_DURING_CONSUMER', str(outputs))
    canonical = consumer.get('status')
    gates['canonical_step_consumer'] = canonical if canonical in ('PASS', 'FAIL') else 'NOT_MEASURED'
    gates.update(measured_program_gates(consumer, tuple(
        k for k in gates if k not in ('native_producer', 'canonical_step_consumer'))))
    verdict = design_verdict(native_verdict, canonical)
    return em.Evidence(binding, verdict, gates, hashes, detail=stable({
        'scope': 'actual producer and canonical semantic consumer; IC signoff not claimed',
        'raw_producer': raw, 'canonical_semantics': consumer,
        'candidate_gate_policy': 'F1 owns canonical clause enforcement; unexecuted programs are not PASS'}))


def reconsume(project: Path, binding: dict, spec: dict, parent: Path):
    """Actual semantic gate consumer on isolated selected canonical bytes."""
    transaction = Path(tempfile.mkdtemp(prefix='frontend-consumer-', dir=parent))
    inputs, outputs = transaction/'inputs', transaction/'outputs'
    inputs.mkdir(); outputs.mkdir()
    # Consumer inputs include producer outputs and all upstream source, with
    # their own complete frozen manifest. The producer binding stays intact.
    for rel, expected in output_manifest(project).items():
        source = project/rel
        target = inputs/'project'/rel; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if em.digest(target) != expected:
            raise em.Refusal('FRONTEND_CONSUMER_INPUT_CHANGED', rel)
    producer_inputs = parent/'inputs'
    for prefix, root, population in spec.get('external_populations', []):
        src = producer_inputs/prefix
        if not src.is_dir() and not src.is_file():
            raise em.Refusal('FRONTEND_FROZEN_EXTERNAL_MISSING', prefix)
        if src.is_dir():
            shutil.copytree(src, inputs/prefix)
        else:
            (inputs/prefix).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, inputs/prefix)
    consumer_spec = dict(spec, input_hashes={str(p.relative_to(inputs)): em.digest(p)
                            for p in inputs.rglob('*') if p.is_file()})
    write(inputs/'request.json', consumer_spec)
    shutil.copyfile(spec['lease_path'], inputs/'lease.json')
    if spec.get('source_software') is not True:
        facts_file, _ = native_facts(spec['parameters'])
        shutil.copyfile(facts_file, inputs/'native_facts.json')
    consumer_binding = dict(binding, inputs={str(p.relative_to(inputs)): em.digest(p)
                                for p in inputs.rglob('*') if p.is_file()})
    previous = os.environ.get('VIBEIC_EXECUTION_BINDING')
    os.environ['VIBEIC_EXECUTION_BINDING'] = stable(consumer_binding)
    try:
        rc = launch(inputs, outputs, phase='consume')
    finally:
        if previous is None:
            os.environ.pop('VIBEIC_EXECUTION_BINDING', None)
        else:
            os.environ['VIBEIC_EXECUTION_BINDING'] = previous
    if rc != 0 or not (outputs/'canonical-consumer.json').is_file():
        raise em.Refusal('FRONTEND_CANONICAL_CONSUMER_UNMEASURED', str(outputs))
    return json.loads((outputs/'canonical-consumer.json').read_text()), transaction


def frontend_native_binding(inputs: Path, outputs: Path, spec: dict,
                            controller_binding: dict):
    """Build the shared typed descriptor from the live frontend snapshot.

    The controller binding is the only source of input hashes. The image and
    output root come from the current request and launch transaction; copied
    witness JSON never enters this descriptor.
    """
    from execution_resource_lease import NativeChildBinding
    native = spec.get('parameters', {}).get('native')
    if not isinstance(native, dict):
        raise em.Refusal('FRONTEND_NATIVE_IDENTITY_MISSING', spec.get('step_id', ''))
    input_hashes = controller_binding.get('inputs')
    if output_manifest(inputs) != input_hashes:
        raise em.Refusal('FRONTEND_NATIVE_INPUT_UNBOUND', str(inputs))
    return NativeChildBinding(
        step_id=spec['step_id'],
        source_sha256=spec['source_sha'],
        image_id=native.get('image_id'),
        input_hashes=input_hashes,
        output_root=str(outputs.resolve()),
    )


def frontend_native_contract(inputs: Path, outputs: Path, spec: dict,
                             controller_binding: dict):
    """Return the issuer-owned client descriptor and its lease digest."""
    from execution_resource_lease import native_client_contract
    directory = Path(spec['lease_directory']).resolve()
    lease_path = Path(spec['lease_path']).resolve()
    expected_lease_sha256 = em.digest(lease_path)
    binding = frontend_native_binding(inputs, outputs, spec, controller_binding)
    contract = native_client_contract(directory, binding=binding,
                                      expected_lease_sha256=expected_lease_sha256)
    return binding, contract, expected_lease_sha256


def native_docker_argv(inputs: Path, outputs: Path, spec: dict, *, phase='worker',
                       binding=None, expected_lease_sha256=None,
                       socket_path=None) -> list[str]:
    q = lease_current(Path(spec['lease_directory']), spec['parameters'])
    image = spec['parameters']['native']['image_id']
    image_current(image)
    cpuset = sorted(os.sched_getaffinity(0))[:int(q['cpus'])]
    name = 'vibeic_frontend_' + uuid.uuid4().hex
    import _docker_memory as _dmem
    container_memory = str(int(q['container_ram_mb']) * 1024 ** 2)
    args = ['docker', 'run', *_dmem.docker_memory_flags(
        {'VIBEIC_DOCKER_MEMORY': container_memory}),
        '--name', name, '--network', 'none',
        '--cpus', str(q['cpus']), '--cpuset-cpus', ','.join(map(str, cpuset)),
        '--pids-limit', '256', '--user', f'{os.getuid()}:{os.getgid()}',
        '-v', f'{PROGRAMS.parent}:{PROGRAMS.parent}:ro',
        '-v', f'{inputs}:{inputs}:ro', '-v', f'{outputs}:{outputs}:rw',
        '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'HOME=/tmp/frontend-home',
        '-e', 'TMPDIR=/tmp', '-e', 'VIBEIC_EXECUTION_BINDING='+os.environ['VIBEIC_EXECUTION_BINDING'],
        '-e', 'VIBEIC_EXECUTION_NATIVE_STEP='+spec['step_id']]
    for prefix, root, _ in spec.get('external_populations', []):
        frozen = inputs/prefix
        if not frozen.is_dir() and not frozen.is_file():
            raise em.Refusal('FRONTEND_FROZEN_EXTERNAL_MISSING', prefix)
        args += ['-v', f'{frozen}:{root}:ro']
    args += [image, '--skip', 'timeout', '-k', '5', str(native_deadline(spec['parameters'])),
             'python3', str(Path(__file__).resolve()), '--phase', phase,
             '--inputs', str(inputs), '--outputs', str(outputs),
             '--native-socket-path', socket_path]
    if (binding is None or not isinstance(expected_lease_sha256, str)
            or not isinstance(socket_path, str) or not socket_path):
        raise em.Refusal('FRONTEND_NATIVE_CLIENT_DESCRIPTOR_MISSING', spec['step_id'])
    from execution_resource_lease import native_argv as resource_native_argv
    return resource_native_argv(args, Path(spec['lease_directory']), binding=binding,
                                expected_lease_sha256=expected_lease_sha256)


def launch(inputs: Path, outputs: Path, *, phase='worker'):
    spec = json.loads((inputs/'request.json').read_text())
    if spec.get('source_step1') is True:
        if phase != 'worker' or spec.get('step_id') != '1':
            raise em.Refusal('FRONTEND_SOURCE_STEP1_PHASE_INVALID', phase)
        started = time.monotonic_ns()
        os.environ['VIBEIC_EXECUTION_NATIVE_STEP'] = '1'
        try:
            with em.issued_step1_callback(outputs/'project'):
                rc = worker(inputs, outputs)
        finally:
            os.environ.pop('VIBEIC_EXECUTION_NATIVE_STEP', None)
        receipt = {'kind': 'issued-host-software-step1',
            'binding': json.loads(os.environ['VIBEIC_EXECUTION_BINDING']),
            'pid': os.getpid(), 'rc': rc, 'waited': True,
            'started_ns': started, 'ended_ns': time.monotonic_ns(),
            'native': 'NOT_MEASURED'}
        write(outputs/'host-software.json', receipt)
        if (outputs/'producer.json').is_file():
            producer = json.loads((outputs/'producer.json').read_text())
            producer['outputs'] = {k: v for k, v in output_manifest(outputs).items()
                                   if k != 'producer.json'}
            write(outputs/'producer.json', producer)
        if phase == 'worker':
            print('F2_OUTPUT_MANIFEST ' + stable({'binding': receipt['binding'],
                                                  'outputs': output_manifest(outputs)}), flush=True)
        return rc
    if spec.get('source_software') is True and spec.get('step_id') == '0.5ic':
        if phase not in ('worker', 'consume'):
            raise em.Refusal('FRONTEND_SOURCE_SOFTWARE_PHASE_INVALID', phase)
        started = time.monotonic_ns()
        rc = worker(inputs, outputs, consuming=phase == 'consume')
        receipt = {'kind': 'issued-host-software', 'step_id': '0.5ic',
            'binding': json.loads(os.environ['VIBEIC_EXECUTION_BINDING']),
            'arm_id': os.environ.get('VIBEIC_ARM_ID'), 'pid': os.getpid(),
            'rc': rc, 'waited': True, 'started_ns': started,
            'ended_ns': time.monotonic_ns(), 'native': 'NOT_MEASURED'}
        write(outputs/'host-software.json', receipt)
        if (outputs/'producer.json').is_file():
            producer = json.loads((outputs/'producer.json').read_text())
            producer['outputs'] = {k: v for k, v in output_manifest(outputs).items()
                                   if k != 'producer.json'}
            write(outputs/'producer.json', producer)
        if phase == 'worker':
            print('F2_OUTPUT_MANIFEST ' + stable({'binding': receipt['binding'],
                                                  'outputs': output_manifest(outputs)}), flush=True)
        return rc
    if spec.get('source_software') is True and spec.get('step_id') == 'D1':
        # Keep the real phase1 runner/dispatch path. Its authenticated child
        # capability is a separate F1 API and is not emulated with ENV state.
        raise em.Refusal('FRONTEND_ISSUED_DESCENDANT_API_REQUIRED', 'D1')
    controller_binding = json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])
    native_binding, native_contract, lease_sha256 = frontend_native_contract(
        inputs, outputs, spec, controller_binding)
    argv = native_docker_argv(inputs, outputs, spec, phase=phase,
                              binding=native_binding,
                              expected_lease_sha256=lease_sha256,
                              socket_path=native_contract['socket_container_path'])
    name = argv[argv.index('--name') + 1]
    started = time.monotonic_ns()
    cid = None
    native_pid = None
    cgroup = None
    peak = None
    with (outputs/'docker.stdout').open('wb') as so, (outputs/'docker.stderr').open('wb') as se:
        process = subprocess.Popen(argv, stdout=so, stderr=se)
        deadline = time.monotonic() + native_deadline(spec['parameters']) + 15
        try:
            while process.poll() is None:
                state = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True, timeout=5)
                if state.returncode == 0:
                    data = json.loads(state.stdout)[0]
                    cid = data['Id']; native_pid = data['State']['Pid'] or native_pid
                    if native_pid and Path('/proc', str(native_pid), 'cgroup').is_file():
                        rel = Path('/proc', str(native_pid), 'cgroup').read_text().split('::')[1].strip().lstrip('/')
                        cgroup = Path('/sys/fs/cgroup')/rel
                        if (cgroup/'memory.peak').is_file():
                            peak = int((cgroup/'memory.peak').read_text())
                if time.monotonic() > deadline:
                    raise em.Refusal('FRONTEND_NATIVE_DEADLINE', name)
                time.sleep(.05)
            rc = process.wait()
            state = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True, timeout=5)
            data = json.loads(state.stdout)[0] if state.returncode == 0 else None
            if data:
                cid = data['Id']
                if data['State']['ExitCode'] != rc:
                    raise em.Refusal('FRONTEND_NATIVE_RC_MISMATCH', name)
        finally:
            cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=10)
            process.wait(timeout=10)
    witness_path = outputs/'native-witness.json'
    try:
        issuer_witness = json.loads(witness_path.read_text())
    except (OSError, ValueError) as exc:
        raise em.Refusal('FRONTEND_NATIVE_WITNESS_MISSING', str(witness_path)) from exc
    if not isinstance(issuer_witness, dict):
        raise em.Refusal('FRONTEND_NATIVE_WITNESS_INVALID', str(witness_path))
    receipt = {'binding': controller_binding,
        'native_binding': asdict(native_binding),
        'native_client': {key: native_contract[key] for key in (
            'socket_host_path', 'socket_container_path', 'locator_env',
            'locator_value', 'lease_sha256')},
        'issuer_witness': issuer_witness,
        'argv': argv, 'pid': process.pid, 'native_pid': native_pid, 'cid': cid,
        'rc': rc, 'waited': True, 'started_ns': started, 'ended_ns': time.monotonic_ns(),
        'lease_sha256': lease_sha256, 'memory_peak_bytes': peak,
        'cleanup_rc': cleanup.returncode, 'container_state': data}
    write(outputs/'native.json', receipt)
    if (outputs/'producer.json').is_file():
        producer = json.loads((outputs/'producer.json').read_text())
        producer['outputs'] = {k: v for k, v in output_manifest(outputs).items()
                               if k not in ('producer.json', 'native.json')}
        write(outputs/'producer.json', producer)
    if phase == 'worker':
        print('F2_OUTPUT_MANIFEST ' + stable({'binding': receipt['binding'],
                                             'outputs': output_manifest(outputs)}), flush=True)
    return rc


def worker(inputs: Path, outputs: Path, *, consuming=False, native_socket_path=None):
    binding = json.loads(os.environ['VIBEIC_EXECUTION_BINDING'])
    spec = json.loads((inputs/'request.json').read_text())
    if not spec.get('source_sha') or spec['source_sha'] != binding['source_sha']:
        raise em.Refusal('FRONTEND_WORKER_SOURCE_UNBOUND', '')
    source_step1 = spec.get('source_step1') is True
    source_software = spec.get('source_software') is True
    if (spec.get('step_id') not in STEP_IDS or binding.get('step_id') != spec['step_id']
            or (source_step1 and spec['step_id'] != '1')
            or (source_software and spec['step_id'] not in ('1', 'D1', '0.5ic'))
            or (not source_software and os.environ.get('VIBEIC_EXECUTION_NATIVE_STEP') != spec['step_id'])):
        raise em.Refusal('FRONTEND_NATIVE_REENTRY_UNBOUND', str(spec.get('step_id')))
    if source_step1 and (spec['step_id'] != '1' or
            em.current_step1_callback() is None):
        raise em.Refusal('STEP1_CALLBACK_UNBOUND', 'issued host software worker required')
    if source_software and json.loads(os.environ.get('VIBEIC_EXECUTION_BINDING', '{}')) != binding:
        raise em.Refusal('FRONTEND_SOFTWARE_BINDING_UNBOUND', spec['step_id'])
    def require_frozen():
        if not source_software:
            for name, expected in spec['parameters']['native']['tool_files'].items():
                p = Path(name)
                if not p.is_file() or p.is_symlink() or em.digest(p) != expected:
                    raise em.Refusal('FRONTEND_NATIVE_BINARY_CHANGED', name)
        for name, expected in spec['source_files'].items():
            # Host binaries are bound at the parent; mounted source and
            # native binaries are checked inside the admitted image.
            if Path(name).is_relative_to(PROGRAMS.parent) and em.digest(Path(name)) != expected:
                raise em.Refusal('FRONTEND_WORKER_SOURCE_CHANGED', name)
        if output_manifest(inputs) != binding['inputs']:
            raise em.Refusal('FRONTEND_FROZEN_INPUT_CHANGED', '')
        if not source_software and json.loads((inputs/'native_facts.json').read_text()) != spec['parameters']['native_facts']:
            raise em.Refusal('FRONTEND_NATIVE_FACTS_MISMATCH', '')
    require_frozen()
    project = outputs/'project'
    project.mkdir(exist_ok=False)
    for name in binding['inputs']:
        if name.startswith('project/'):
            dst = outputs/name; dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(inputs/name, dst)
    os.environ['F2_OUTPUT_ROOT'] = str(outputs)
    if not source_software and shutil.which('docker'):
        raise em.Refusal('FRONTEND_NESTED_CONTAINER_ROUTE_REFUSED', '')
    if not source_software:
        from execution_resource_lease import native_child_handshake
        native = spec['parameters'].get('native')
        outer_pid = os.environ.get('VIBEIC_NATIVE_OUTER_PID')
        if (not isinstance(native, dict) or not isinstance(native_socket_path, str)
                or not native_socket_path
                or not isinstance(outer_pid, str) or not outer_pid.isdigit()):
            raise em.Refusal('FRONTEND_NATIVE_CAPABILITY_UNAVAILABLE', spec['step_id'])
        # The issuer witness is evidence only. It is deliberately never passed
        # to execution_modes as a live child scope or callback grant.
        issuer_witness = native_child_handshake(
            step_id=spec['step_id'], source_sha256=spec['source_sha'],
            image_id=native['image_id'], binding=binding,
            inputs=binding['inputs'], outputs=output_manifest(project),
            socket_path=native_socket_path, outer_pid=int(outer_pid))
        if not isinstance(issuer_witness, dict):
            raise em.Refusal('FRONTEND_NATIVE_WITNESS_INVALID', spec['step_id'])
        write(outputs/'native-witness.json', issuer_witness)
    before = output_manifest(project)
    def remap(value):
        if isinstance(value, dict):
            return {k: (v if k == 'native_facts' else remap(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, str) and Path(value).is_absolute():
            p = Path(value)
            if p.is_relative_to(spec['project']):
                return str(project/p.relative_to(spec['project']))
        return value
    if not consuming:
        import step_write_ledger as ledger
        if not ledger.mark_run_start(project):
            raise em.Refusal('FRONTEND_STEP_WRITE_WINDOW_UNMEASURED', spec['step_id'])
        raw = produce(project, spec['step_id'], remap(spec['parameters']),
                      issued_binding=binding)
        write(outputs/'step-writes.json', record_step_outputs(project, spec['step_id']))
    else:
        raw = {'status': 'PASS'}
    semantic = semantic_consumer(project, spec['step_id'])
    write(outputs/'canonical-consumer.json', semantic)
    after = output_manifest(project)
    # The isolated worker may author outputs, never rewrite bound source.
    output_patterns = exclusions(spec['step_id'])
    def owed(rel):
        return any(rel == x or rel.startswith(x.rstrip('/')+'/') or fnmatch.fnmatchcase(rel, x)
                   for x in output_patterns)
    for rel, sha in before.items():
        if not owed(rel) and after.get(rel) != sha:
            raise em.Refusal('FRONTEND_PRODUCER_REWROTE_INPUT', rel)
    # Auxiliary worker artifacts stay in this private generation. Only the
    # declared canonical output population is eligible for publication.
    canonical_outputs = {k: v for k, v in after.items() if owed(k)}
    require_frozen()
    write(outputs/'producer.json', {'binding': binding, 'input_hashes': spec['input_hashes'],
          'raw_producer': raw, 'canonical_outputs': canonical_outputs,
          'outputs': {k: v for k, v in output_manifest(outputs).items() if k != 'producer.json'}})
    return 0


def publish_selected(project: Path, selected_project: Path, outputs: dict,
                     run: Path, context: FrontendContext, binding: dict,
                     before_publish, after_publish):
    """Held-directory CAS with rollback of only this publication's writes.

    Reuse the existing accepted source transaction. Upstream lexical aliases
    survive the snapshot, but no destination ancestor may be an alias. The
    child consumes the regular selected snapshot, never the parent's aliases.
    """
    import design_one_shot_runner as d
    from _atomic_artefact import write_bytes
    transaction_root = Path(tempfile.mkdtemp(prefix='frontend-import-', dir=run))
    stage = transaction_root/'project'
    held = d._Phase1ProjectBinding.open(project)
    staged = None
    published = None
    old_journal = dict(context.journal_current)
    try:
        # The shared snapshot copies bytes into a fresh tree. Keep metadata
        # for every unmodified old entry when a complete top-level directory
        # is swapped, and reject intervening metadata-only owner changes.
        old_metadata = {}
        for root, directories, files, directory_fd in os.fwalk(
                '.', dir_fd=held.project_fd, follow_symlinks=False):
            for name in (*directories, *files):
                relative = str(Path(root)/name)
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                old_metadata[relative] = info
        baseline = d._phase1_snapshot_to_stage(held, stage)
        allowed = adoption_paths(context.step_id)
        for name, expected in outputs.items():
            rel = Path(name)
            if (not isinstance(name, str) or rel.is_absolute() or '..' in rel.parts
                    or not name or not any(name == p or name.startswith(p+'/') for p in allowed)):
                raise em.Refusal('FRONTEND_IMPORT_PATH_UNSAFE', str(name))
            source = selected_project/rel
            if source.is_symlink() or not source.is_file() or em.digest(source) != expected:
                raise em.Refusal('FRONTEND_SELECTED_OUTPUT_CHANGED', name)
            destination = stage/rel
            for ancestor in (destination, *destination.parents):
                if ancestor == stage:
                    break
                if ancestor.is_symlink():
                    raise em.Refusal('FRONTEND_IMPORT_ALIAS', name)
            write_bytes(destination, source.read_bytes())
            if em.digest(destination) != expected:
                raise em.Refusal('FRONTEND_SELECTED_OUTPUT_CHANGED', name)
        for relative, info in old_metadata.items():
            if relative not in outputs:
                os.utime(stage/relative, ns=(info.st_atime_ns, info.st_mtime_ns),
                         follow_symlinks=False)
        staged = d._Phase1ProjectBinding.open(stage)
        final = d._phase1_tree_manifest_fd(staged.project_fd, project)
        before_publish()
        if context.binding() != binding:
            raise em.Refusal('FRONTEND_IMPORT_INPUT_CHANGED', str(project))
        for relative, info in old_metadata.items():
            if relative not in outputs:
                current = os.stat(relative, dir_fd=held.project_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino, current.st_mode, current.st_size,
                    current.st_mtime_ns, current.st_ctime_ns) != (
                    info.st_dev, info.st_ino, info.st_mode, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns):
                    raise em.Refusal('FRONTEND_IMPORT_OWNER_METADATA_CHANGED', relative)
        published = d._phase1_commit_staged_tree(held, staged, baseline, final)
        # The shared commit prepares another byte copy of each changed top.
        # Restore metadata on those newly published copies while our own
        # transaction still holds the original subtrees for rollback. Entries
        # in unchanged tops remain owned by their current writers and are
        # never touched here.
        changed_tops = {record['top'] for record in published.records
                        if record['new_published']}
        held.require_current()
        if d._phase1_tree_manifest_fd(held.project_fd, project,
                ignore_top={published.container_name}) != final:
            raise em.Refusal('FRONTEND_IMPORT_OUTPUT_CHANGED', str(project))
        for relative, info in old_metadata.items():
            if relative not in outputs and Path(relative).parts[0] in changed_tops:
                os.utime(relative, ns=(info.st_atime_ns, info.st_mtime_ns),
                         dir_fd=held.project_fd, follow_symlinks=False)
        if 'provenance.jsonl' in outputs:
            context.journal_current['provenance.jsonl'] = outputs['provenance.jsonl']
        if context.binding() != binding:
            raise em.Refusal('FRONTEND_IMPORT_INPUT_CHANGED', str(project))
        held.require_current()
        if d._phase1_tree_manifest_fd(held.project_fd, project,
                ignore_top={published.container_name}) != final:
            raise em.Refusal('FRONTEND_IMPORT_OUTPUT_CHANGED', str(project))
        after_publish()
        warning = d._phase1_finalize_accepted_transaction(published)
        return transaction_root, warning
    except BaseException as exc:
        errors = published.rollback() if published is not None else []
        context.journal_current.clear(); context.journal_current.update(old_journal)
        if errors:
            raise em.Refusal('GATE_FAIL' if getattr(exc, 'code', '') == 'GATE_FAIL'
                             else 'FRONTEND_IMPORT_ROLLBACK_INCOMPLETE', stable(
                {'original': str(exc), 'rollback_errors': errors, 'transaction': str(transaction_root)})) from exc
        if isinstance(exc, d._Phase1RtlOutputRefused):
            raise em.Refusal('FRONTEND_IMPORT_CAS_REFUSED', str(exc)) from exc
        raise
    finally:
        if staged is not None:
            staged.close()
        held.close()


def consume_frontend(project: Path, context: FrontendContext, controller: em.Controller,
                     run: Path, adopted: dict) -> dict:
    """Import the live parent's selected generation; rollback every change.

    The canonical consumer runs on an isolated transaction tree BEFORE CAS
    publication. Native calls in that consumer use the same current lease.
    The parent project is never handed to the child.
    """
    project = Path(project).resolve(strict=True)
    run = Path(run).resolve(strict=True)
    original_binding = context.binding()
    if project != context.project.resolve() or not adopted or adopted.get('status') != 'ADOPTED':
        raise em.Refusal('FRONTEND_ADOPTION_MISSING', str(project))
    selected = adopted.get('selected_generation')
    if not isinstance(selected, dict) or not selected.get('generation') or not selected.get('outputs'):
        raise em.Refusal('FRONTEND_SELECTED_GENERATION_MISSING', str(run))
    controller._generation_current(selected)
    if selected.get('binding') is None or selected['binding'] != original_binding:
        raise em.Refusal('FRONTEND_SELECTED_BINDING_MISMATCH', str(run))
    arm_id = adopted.get('selected')
    if not isinstance(arm_id, str) or selected.get('arm_id') != arm_id:
        raise em.Refusal('FRONTEND_SELECTED_ARM_MISMATCH', str(run))
    generation_root = Path(selected['directory']).resolve(strict=True)
    if not generation_root.is_relative_to(run/'selected'):
        raise em.Refusal('FRONTEND_SELECTED_ROOT_ESCAPE', str(generation_root))
    receipt_path = run/arm_id/'receipt.json'
    receipt = json.loads(receipt_path.read_text())
    plan = json.loads((run/'plan.json').read_text())
    arm = next((a for a in controller.registry.adapters(context.step_id) if a.arm_id == arm_id), None)
    if arm is None or selected.get('run_id') != plan.get('run_id') or not plan.get('run_id'):
        raise em.Refusal('FRONTEND_SELECTED_RUN_MISMATCH', str(run))
    controller._execution_authority(run, plan, receipt, arm)
    if context.step_id == '0.5ic':
        host_path = Path(receipt['output_root'])/'host-software.json'
        host = json.loads(host_path.read_text())
        workers = [process for process in receipt.get('processes', [])
                   if process.get('component') == 'frontend-producer']
        if (len(workers) != 1 or host.get('kind') != 'issued-host-software'
                or host.get('step_id') != '0.5ic' or host.get('binding') != original_binding
                or host.get('arm_id') != arm_id or host.get('pid') != workers[0].get('pid')
                or host.get('rc') != 0 or host.get('waited') is not True
                or host.get('native') != 'NOT_MEASURED'
                or not (workers[0].get('started_ns', 0) <= host.get('started_ns', -1)
                        <= host.get('ended_ns', 0) <= workers[0].get('ended_ns', -1))):
            raise em.Refusal('FRONTEND_HOST_ISSUER_RECEIPT_MISMATCH', arm_id)
    controller._current_admission(context, plan, arm)
    controller._eligible(receipt, context, arm)
    # manifest.json is controller-issued authority, not a selected output.
    actual = {k: v for k, v in output_manifest(generation_root).items() if k != 'manifest.json'}
    if actual != selected['outputs']:
        raise em.Refusal('FRONTEND_SELECTED_POPULATION_CHANGED', str(generation_root))
    producer = json.loads((generation_root/'producer.json').read_text())
    outputs = producer.get('canonical_outputs')
    if not isinstance(outputs, dict) or not outputs:
        raise em.Refusal('FRONTEND_CANONICAL_OUTPUT_MISSING', context.step_id)
    original_verdict = receipt.get('evidence', {}).get('verdict')
    primary_gates = receipt.get('evidence', {}).get('gates')
    # The live issuer above grades candidate eligibility. A flattened list
    # cannot independently reclassify its disclosed advisory/conditional rows.
    if (original_verdict != 'PASS' or not isinstance(primary_gates, dict)
            or set(primary_gates) != set(context.required_gates)
            or any(primary_gates.get(name) != 'PASS'
                   for name in ('native_producer', 'canonical_step_consumer'))):
        raise em.Refusal('GATE_FAIL' if original_verdict == 'FAIL' or
            isinstance(primary_gates, dict) and 'FAIL' in primary_gates.values()
            else 'FRONTEND_SELECTED_DESIGN_REFUSED', stable(
            {'design_verdict': original_verdict, 'primary_gates': primary_gates}))
    spec = json.loads(context.inputs['request.json'].read_text())
    consumer_detail = {}
    def generation_current():
        controller._generation_current(selected)
        actual = {k: v for k, v in output_manifest(generation_root).items() if k != 'manifest.json'}
        if actual != selected['outputs']:
            raise em.Refusal('FRONTEND_SELECTED_POPULATION_CHANGED', str(generation_root))
        controller._source_current(arm)
        controller._current_admission(context, plan, arm)
    def before_publish():
        semantic, consumer_transaction = reconsume(
            generation_root/'project', original_binding, spec, run/arm_id)
        consumer_detail.update(semantic=semantic, transaction=str(consumer_transaction))
        require_consumer_pass(semantic, original_verdict)
        generation_current()
    transaction, warning = publish_selected(project, generation_root/'project', outputs,
        run, context, original_binding, before_publish, generation_current)
    return protocol.imported_result(step_id=context.step_id,
        source_sha=context.source_sha, selected_generation=selected,
        binding=original_binding, copied=outputs,
        primary_gates=dict(primary_gates, canonical_step_consumer='PASS'),
        design_verdict=original_verdict, consumer_detail=dict(consumer_detail,
            status='CONSUMED', canonical_outputs=outputs,
            transaction=str(transaction), cleanup_warning=warning))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('launch', 'worker', 'consume'), required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--outputs', type=Path, required=True)
    parser.add_argument('--native-socket-path')
    args = parser.parse_args()
    if args.phase == 'launch':
        return launch(args.inputs, args.outputs)
    return worker(args.inputs, args.outputs, consuming=args.phase == 'consume',
                  native_socket_path=args.native_socket_path)


if __name__ == '__main__':
    raise SystemExit(main())

"""Import an issued backend generation, run actual canonical consumers, rollback."""
from __future__ import annotations
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
import execution_backend_snapshot as snap
from execution_backend_gates import evaluate, contracts, states


def _same(current, expected):
    if current is None or expected is None or current != expected:
        raise em.Refusal('BACKEND_CANONICAL_HASH_MISMATCH', 'missing is never equal to missing')


def import_selected(project, context, controller, run, adopted):
    from execution_adapters_backend import ROWS, validate
    from execution_step_protocol import imported_result
    project = Path(project).absolute()
    run = Path(run).absolute()
    if (run.is_relative_to(project) or run.is_relative_to(context.pdk) or any(
            run == Path(p).absolute() or (Path(p).is_dir() and run.is_relative_to(Path(p).absolute()))
            for p in context.extra.values())):
        raise em.Refusal('BACKEND_CONSUMER_NAMESPACE', str(run))
    if project != context.project or adopted.get('status') != 'ADOPTED':
        raise em.Refusal('BACKEND_ADOPTION_UNBOUND', str(project))
    generation = adopted['selected_generation']
    controller._generation_current(generation)
    plan = json.loads((run / 'plan.json').read_text())
    if (generation['run_id'] != plan['run_id'] or generation['arm_id'] != adopted['selected'] or
            adopted.get('run_id') != plan['run_id']):
        raise em.Refusal('BACKEND_WRONG_SELECTED_GENERATION', context.step_id)
    binding = context.binding()
    if generation['binding'] != binding or adopted['evidence']['binding'] != binding:
        raise em.Refusal('BACKEND_CONSUMER_INPUT_CHANGED', context.step_id)
    arm = next(a for a in controller.registry.adapters(context.step_id) if a.arm_id == adopted['selected'])
    controller._source_current(arm)
    receipt = json.loads((run / arm.arm_id / 'receipt.json').read_text())
    controller._execution_authority(run, plan, receipt, arm)
    controller._current_admission(context, plan, arm)
    controller._eligible(receipt, context, arm)
    if generation['outputs'] != receipt['evidence']['outputs']:
        raise em.Refusal('BACKEND_WRONG_SELECTED_OUTPUT', context.step_id)
    issued = Path(generation['directory'])
    if validate(issued, binding).verdict != 'PASS':
        raise em.Refusal('BACKEND_SELECTED_SUBSTANCE_REFUSED', context.step_id)
    result = json.loads((issued / 'backend_result.json').read_text())
    if not result['publication'] or not result['contracts']:
        raise em.Refusal('BACKEND_PUBLICATION_EMPTY', context.step_id)
    paths = context.canonical_adoptions
    for target in (*result['publication'], *result.get('removed', [])):
        snap.adoption_target(target, paths)
    journal = snap.Journal(project)
    before_inputs = {}
    before = snap.population(project, 'project', before_inputs)
    # Register every potential gate report before invoking the consumer. Roll
    # back all new/modified/deleted files if a gate or freshness check refuses.
    for key, row in before.items():
        if row.get('kind') == 'file' and row.get('alias') is None:
            journal.touch(key.removeprefix('project/'))
    gate_result = None
    primary = None
    try:
        for target, source in result['publication'].items():
            if target.startswith(('input/', 'docs/', 'phase1/')):
                raise em.Refusal('BACKEND_INPUT_WRITE_REFUSED', target)
            if source not in generation['outputs'] or not source.startswith('project/'):
                raise em.Refusal('BACKEND_UNISSUED_OUTPUT', source)
            data = (issued / snap.relative(source)).read_bytes()
            _same(snap.sha(issued / source), generation['outputs'][source])
            journal.write(target, data)
        for target in result.get('removed', []):
            if target.startswith(('input/', 'docs/', 'phase1/')):
                raise em.Refusal('BACKEND_INPUT_DELETE_REFUSED', target)
            journal.touch(target).unlink(missing_ok=True)
        for contract, files in result['contracts'].items():
            for name, expected in files.items():
                p = snap.safe_target(project, name)
                _same(snap.sha(p) if p.is_file() and p.stat().st_size else None, expected)
        controller._generation_current(generation)
        controller._source_current(arm)
        gate_result = evaluate(project, ROWS[context.step_id]['canonical_row'])
        if gate_result['status'] != 'PASS':
            code = 'GATE_FAIL' if gate_result['status'] == 'FAIL' else 'BACKEND_CANONICAL_CONSUMER_REFUSED'
            raise em.Refusal(code, str(gate_result))
        primary = states(binding, gate_result, result['producer_verdict'])
        if not primary or any(value != 'PASS' for value in primary.values()):
            code = 'GATE_FAIL' if 'FAIL' in (primary or {}).values() else 'BACKEND_CANONICAL_GATE_INCOMPLETE'
            raise em.Refusal(code, str(primary))
        resolved = contracts(project, ROWS[context.step_id]['canonical_row'])
        # Producer contract files remain the exact selected bytes. Gates may
        # refresh their own receipts, but may not rewrite native deliverables.
        for contract, files in result['contracts'].items():
            if not files or resolved.get(contract) != files:
                raise em.Refusal('BACKEND_CONSUMER_OUTPUT_CHANGED', contract)
        controller._generation_current(generation)
        controller._source_current(arm)
        after_inputs = {}
        after = snap.population(project, 'project', after_inputs)
        allowed = set(result['publication']) | set(result.get('removed', []))
        # Gate reports are covered by the same roots F1 journals. A gate may
        # not change a current upstream INPUT simply because it is a report.
        for key in set(before) | set(after):
            name = key.removeprefix('project/')
            if before.get(key) != after.get(key):
                snap.adoption_target(name, paths)
                if name not in allowed and key in context.lexical:
                    raise em.Refusal('BACKEND_UNEXPECTED_INPUT_MUTATION', name)
        # PDK/external INPUT population is independently fresh after publication.
        _, population = __import__('execution_adapters_backend')._capture(
            project, context.pdk, context.extra, context.project_input_roots)
        for key in set(context.lexical) | set(population):
            if context.lexical.get(key) != population.get(key):
                if not key.startswith('project/'):
                    raise em.Refusal('BACKEND_EXTERNAL_INPUT_CHANGED', key)
                if key.removeprefix('project/') not in allowed:
                    raise em.Refusal('BACKEND_UNEXPECTED_INPUT_MUTATION', key)
        copied = {name: digest for files in resolved.values() for name, digest in files.items()}
        for name, expected in copied.items():
            source = result['publication'].get(name)
            _same(generation['outputs'].get(source), expected)
        detail = {'status': 'CONSUMED', 'step_id': context.step_id, 'source_sha': context.source_sha,
                   'generation': generation, 'binding_before': binding, 'population_after': population,
                   'consumer': gate_result, 'contracts': resolved,
                   'canonical_outputs': copied,
                   'publication': result['publication'],
                   'blocking_consumer_verdict': gate_result['status']}
        receipt = imported_result(step_id=context.step_id, source_sha=context.source_sha,
                                  selected_generation=generation, binding=binding, copied=copied,
                                  primary_gates=primary, design_verdict=receipt['evidence']['verdict'],
                                  consumer_detail=detail)
        snap.dump(run / 'backend_consumption.json', receipt)
        return receipt
    except BaseException as exc:
        # Keep a measured consumer FAIL outside the rollback population so F1
        # can retain it when the canonical adoption is refused.
        fail = (result.get('producer_verdict') == 'FAIL' or
                'FAIL' in result.get('gates', {}).values() or
                (primary is not None and 'FAIL' in primary.values()) or
                (gate_result is not None and gate_result.get('status') == 'FAIL'))
        snap.dump(run / 'backend_consumer_refusal.json', {
            'status': 'REFUSED', 'step_id': context.step_id, 'source_sha': context.source_sha,
            'generation': generation, 'binding': binding,
            'design_verdict': 'FAIL' if fail else 'NOT_MEASURED',
            'consumer_detail': gate_result, 'reason': str(exc)})
        # Include files a failing semantic consumer created after journal setup.
        now_inputs = {}
        now = snap.population(project, 'project', now_inputs)
        for key, value in now.items():
            if value.get('kind') == 'file' and key not in before:
                name = key.removeprefix('project/')
                if name not in journal.before:
                    journal.before[name] = None
            elif value.get('kind') == 'directory' and key not in before:
                directory = project / key.removeprefix('project/')
                if not directory.is_symlink() and directory not in journal.directories:
                    journal.directories.append(directory)
        journal.directories.sort(key=lambda p: len(p.parts))
        journal.rollback()
        raise

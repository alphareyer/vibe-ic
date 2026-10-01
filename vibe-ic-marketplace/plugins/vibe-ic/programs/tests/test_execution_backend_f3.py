"""Finite F3 source controls. These tests do not qualify a native engine."""
from __future__ import annotations
import ast
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path

import pytest
import execution_modes as em
import execution_backend_snapshot as snap
import execution_adapters_backend as backend
from execution_backend_consumer import _same
from execution_backend_gates import states


def _context(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'selected.def').write_text('DESIGN finite; COMPONENTS 2; END COMPONENTS\n')
    (project / 'a.sdc').write_text('create_clock -period 10 a\n')
    (project / 'b.sdc').write_text('create_clock -period 20 a\n')
    (project / 'active.sdc').symlink_to('a.sdc')
    (project / 'empty').mkdir()
    pdk = tmp_path / 'pdk'
    pdk.mkdir()
    (pdk / 'model.lib').write_text('library(finite) { voltage: 1.8; }\n')
    inputs, population = backend._capture(project, pdk, {})
    if os.environ.get('BACKEND_WITNESS_BASE') == '1':
        # Parent's source-bound generic context has no caller-owned lexical
        # census. It continues to hash the previously resolved paths.
        context = em.Context('18', '8f8142248cc9912a096012cfee13003bfea879af',
                             inputs, {'spare_count': 'max'}, ('coverage',))
    else:
        context = backend.BackendContext('18', '8f8142248cc9912a096012cfee13003bfea879af',
                  inputs, {'spare_count': 'max'}, ('coverage',), 'librelane',
                  project, pdk, {}, population, None)
    return project, context


@pytest.mark.parametrize('mutation', ['add_file', 'delete_alias', 'retarget_alias', 'delete_directory'])
def test_lexical_change_cannot_reuse_selected_binding(tmp_path, mutation):
    project, context = _context(tmp_path)
    before = context.binding()
    old_alias = snap.sha(project / 'active.sdc')
    if mutation == 'add_file':
        (project / 'new.sdc').write_text('create_clock -period 5 a\n')
    elif mutation == 'delete_alias':
        (project / 'active.sdc').unlink()
    elif mutation == 'retarget_alias':
        (project / 'active.sdc').unlink()
        (project / 'active.sdc').symlink_to('b.sdc')
        assert snap.sha(project / 'active.sdc') != old_alias
    else:
        (project / 'empty').rmdir()
    observed = {'old_input_count': len(before['inputs']), 'mutation': mutation}
    try:
        current = context.binding()
        observed.update(status='ACCEPTED', binding_unchanged=current == before)
    except em.Refusal as exc:
        observed.update(status='REFUSED', code=exc.code)
    print('BACKEND_WITNESS ' + json.dumps(observed, sort_keys=True))
    assert observed['status'] == 'REFUSED', observed


def test_escape_refused_before_target_bytes_are_opened(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    external = tmp_path / 'forbidden.model'
    external.write_text('external bytes must not be read')
    (project / 'model').symlink_to(external)
    original = snap.sha
    reads = []
    def watched(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(snap, 'sha', watched)
    with pytest.raises(em.Refusal, match='BACKEND_INPUT_ESCAPE'):
        snap.population(project, 'project', {})
    assert external not in reads


def test_transaction_restores_original_bytes_modes_and_absence(tmp_path):
    p = tmp_path / 'existing.def'
    p.write_bytes(b'ORIGINAL PLACED INSTANCE u1')
    p.chmod(0o440)
    transaction = snap.Journal(tmp_path)
    transaction.write('existing.def', b'SELECTED ROUTED INSTANCE u1')
    transaction.write('new/tree/output.def', b'NEW INSTANCE u2')
    assert p.read_bytes() == b'SELECTED ROUTED INSTANCE u1'
    transaction.rollback()
    assert p.read_bytes() == b'ORIGINAL PLACED INSTANCE u1'
    assert p.stat().st_mode & 0o777 == 0o440
    assert not (tmp_path / 'new').exists()


def test_canonical_parent_alias_cannot_overwrite_external_file(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    target = tmp_path / 'outside'
    target.mkdir()
    original = target / 'route.def'
    original.write_bytes(b'PRESERVE')
    (project / 'pnr').symlink_to(target)
    with pytest.raises(em.Refusal, match='BACKEND_CANONICAL_ALIAS'):
        snap.Journal(project).write('pnr/route.def', b'REPLACE')
    assert original.read_bytes() == b'PRESERVE'


@pytest.mark.parametrize('left,right', [(None, None), (None, 'abc'), ('abc', None), ('abc', 'def')])
def test_missing_or_wrong_canonical_hash_refuses(left, right):
    with pytest.raises(em.Refusal, match='BACKEND_CANONICAL_HASH_MISMATCH'):
        _same(left, right)


def test_real_spare_consumer_measures_changed_tie_pin(tmp_path):
    import spare_cell_coverage_check as coverage
    pnr = tmp_path / 'phase3/stage3/pnr'
    pnr.mkdir(parents=True)
    plan = {'count': 2, 'placed_cells_est': 50, 'target_density': 0.02,
            'tied_off': True, 'instances': [{'name': 's1', 'llx': 1, 'lly': 1},
                                          {'name': 's2', 'llx': 10, 'lly': 10}],
            'tie_off': {'inputs': [{'inst': 's1', 'pin': 'A', 'net': 'tie1'},
                                  {'inst': 's2', 'pin': 'CLK', 'net': 'tie2'}]}}
    path = pnr / 'spare_cells.json'
    snap.dump(path, plan)
    green_rc = coverage.main([str(tmp_path)])
    green = json.loads((tmp_path / 'reports/spare_cell_coverage.json').read_text())
    assert green_rc == 0 and green['verdict'] == 'PASS', green
    plan['tie_off']['inputs'][1]['net'] = None
    snap.dump(path, plan)
    red_rc = coverage.main([str(tmp_path)])
    red = json.loads((tmp_path / 'reports/spare_cell_coverage.json').read_text())
    assert red_rc == 1 and red['verdict'] == 'FAIL'
    assert 's2/CLK' in json.dumps(red)


def test_measured_hold_and_area_failures_block_backend():
    import _librelane_cts_hold_evidence as hold
    import hold_area_budget_check as area
    reading = hold.hold_verdict({'corners': ['SS'],
                       'metrics': {'timing__hold__ws__corner:SS': -0.0397}})
    assert reading['clean'] is False and reading['worst'] == -0.0397
    verdict, rc, detail = area.evaluate(None, None, before_total_area=100, after_total_area=120)
    assert rc == 1 and verdict == 'FAIL'
    gates = states({'required_gates': ['backend_native_substance', 'backend_canonical', 'hold_closure_check']},
                   {'status': 'FAIL', 'ledger': [{'gate': 'hold_closure_check', 'rc': 1, 'verdict': 'FAIL'}]}, 'PASS')
    assert gates['backend_canonical'] == gates['hold_closure_check'] == 'FAIL'


def test_result_does_not_hide_measured_fail_when_output_missing(tmp_path):
    binding = {'step_id': '20', 'required_gates': ['hold_closure_check']}
    snap.dump(tmp_path / 'backend_result.json', {'binding': binding, 'step_id': '20',
               'producer_verdict': 'FAIL', 'gates': {'hold_closure_check': 'FAIL'},
               'outputs': {}, 'native_receipts': []})
    assert backend.validate(tmp_path, binding).verdict == 'FAIL'


def test_all_producer_calls_bind_to_actual_source_signatures():
    import phase3_one_shot_runner as runner
    path = Path(backend.__file__).with_name('execution_backend_producers.py')
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and
                isinstance(node.func.value, ast.Name) and node.func.value.id == 'R'):
            function = getattr(runner, node.func.attr)
            inspect.signature(function).bind(*[None for _ in node.args],
                                             **{kw.arg: None for kw in node.keywords})


def test_original_policy_and_harvest_bodies_are_retained():
    assert len(backend.STEP_IDS) == 23
    for sid, row in backend.ROWS.items():
        assert str(row['canonical_row']['id']) == sid
        assert row['original_harvest']['items']
    code = Path(backend.__file__).with_name('execution_backend_producers.py').read_text()
    tree = ast.parse(code)
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert 'main' in calls  # bounded declared producer programs only
    assert 'step_pnr' not in calls and 'step_canonicalize_artefacts' not in calls


def test_backend_provider_attribution_uses_current_complete_producer_routes():
    assert backend._provider_route('29', {}) == ('iverilog', ('iverilog',))
    assert backend._provider_route('33', {}) == ('opensta', ('opensta',))
    assert backend._provider_route('37.3', {}) == ('klayout', ('klayout',))
    assert backend._provider_route('30', {}) == ('ngspice', ('opensta', 'ngspice'))
    assert backend._provider_route('30', {'simulators': ['xyce']}) == (
        'xyce', ('opensta', 'xyce'))
    assert backend._provider_route('30', {'simulators': ['ngspice', 'xyce']}) == (
        'vibeic', ('opensta', 'ngspice', 'xyce'))
    # DRC/LVS/ERC/PERC remain one producer with all complementary families.
    assert backend._provider_route('31', {}) == (
        'vibeic', ('klayout', 'magic', 'netgen'))
    with pytest.raises(em.Refusal, match='BACKEND_SPICE_PROVIDER_UNSTATED'):
        backend._provider_route('30', {'simulators': ['xyce', 'xyce']})


def test_current_composite_provider_plan_keeps_complementary_tools_in_one_ready_row():
    """Exercise the live planner with a typed, source-bound F3 fixture; no worker runs."""
    import subprocess
    import sys
    sid = '31'
    source_head = subprocess.check_output(
        ['git', '-C', str(backend.HERE), 'rev-parse', 'HEAD'], text=True).strip()
    producer_source = Path(backend.__file__).with_name('execution_backend_producers.py')
    row = backend.ROWS[sid]
    required = tuple(dict.fromkeys(('backend_native_substance', 'backend_canonical') +
                                   tuple(row['portfolio_policy']['mandatory_gate_programs'])))
    objective = {'goal': 'complete canonical Step31 axes', 'metric': 'cost', 'direction': 'min'}
    context = em.Context(sid, source_head, {'source_fixture': producer_source}, objective,
                         required, 'librelane')
    tool_id, families = backend._provider_route(sid, {})
    contracts = {name: ('backend_result.json',)
                 for name in row['portfolio_policy']['required_output_contract']}
    adapter = em.Adapter(
        arm_id='backend_31', tool_id=tool_id, step_id=sid, source_sha=source_head,
        source_files=backend.required_source_files(), tool_version='source-only-plan-control',
        engine_families=families,
        components=(em.Component('backend_native',
            (str(Path(sys.executable).resolve()), str(backend.HERE / 'execution_backend_worker.py'),
             '--inputs', '{inputs}', '--outputs', '{outputs}'), 30),),
        validate=backend.validate, required_outputs=('backend_result.json',),
        output_contract=contracts, objective=objective,
        qualification_evidence='source-bound exact-row producer; this control performs no native run',
        cpus=2, ram_mb=2048,
        own_no_tool_reason=backend._complete_row_orchestration_reason(sid, {}))
    registry = em.Registry()
    registry.register(adapter)
    controller = em.Controller(registry, em.Budget(2, 2048))
    plan = controller.plan(context, 'ultra-mode')
    assert plan['status'] == 'PLANNED' and plan['arms'] == ['backend_31'], plan
    row_plan = plan['portfolio'][0]
    assert row_plan['admission'] == 'READY' and row_plan['tool_id'] == 'vibeic', row_plan
    assert 'DRC, LVS, ERC, and PERC' in row_plan['own_no_tool_reason']
    assert set(families) == {'klayout', 'magic', 'netgen'}
    # Reverse the exact repair: the live planner must reproduce the original
    # refusal, rather than silently relabeling the orchestra as an EDA vendor.
    unqualified = em.Registry()
    unqualified.register(replace(adapter, own_no_tool_reason=None))
    red = em.Controller(unqualified, em.Budget(2, 2048)).plan(context, 'ultra-mode')
    assert red['status'] == 'NOT_MEASURED' and red['arms'] == [], red
    assert red['portfolio'][0]['admission'] == 'OWN_TOOL_NOT_JUSTIFIED', red
    print('F3_PROVIDER_PLAN_CONTROL ' + json.dumps({
        'fixture': str(producer_source), 'source_head': source_head,
        'red': {'status': red['status'], 'admission': red['portfolio'][0]['admission']},
        'green': {'status': plan['status'], 'arms': plan['arms'],
                  'tool_id': row_plan['tool_id'], 'mandatory_axes': list(required)},
        'native_executed': False}, sort_keys=True))


def test_current_f1_typed_request_preserves_actual_bindings(tmp_path):
    """Affected API delta only; no native receipt or lease is manufactured."""
    import subprocess
    from execution_step_protocol import FactoryRegistry, StepRequest, imported_result, json_parameters
    project = tmp_path / 'project'
    pdk = tmp_path / 'pdk'
    project.mkdir(); pdk.mkdir()
    (pdk / 'model.lib').write_text('library(finite) {}')
    selected = project / 'phase3/stage3/pnr/placed.def'
    selected.parent.mkdir(parents=True)
    selected.write_text('DESIGN finite; COMPONENTS 2; END COMPONENTS\n')
    declaration = tmp_path / 'supplied-owner-declaration.json'
    snap.dump(declaration, {'answers': {'deliverable': 'HARDMACRO'}})
    (project / 'input').mkdir()
    row = backend.ROWS['18']['canonical_row']
    roots, external = backend._project_roots(project, {'input_roots': {
        'input': project / 'input', str(selected.relative_to(project)): selected}}, row)
    assert roots == ('input', 'phase3/stage3/pnr/placed.def') and external == {}
    inputs, population = backend._capture(project, pdk, {}, roots)
    source = backend.required_source_files()
    context = backend.BackendContext('18', '8f8142248cc9912a096012cfee13003bfea879af',
        inputs, {'spare_count': 'max'}, ('coverage',), 'librelane',
        project, pdk, {}, population, None, source, roots)
    before = context.binding()
    snap.dump(project / 'reports/phase3/producer.json', {'output': 'mutable'})
    assert context.binding() == before
    (project / 'input/new.sdc').write_text('create_clock -period 7 finite\n')
    with pytest.raises(em.Refusal, match='BACKEND_INPUT_POPULATION_CHANGED'):
        context.binding()
    with pytest.raises(em.Refusal, match='BACKEND_MUTABLE_OUTPUT_IN_INPUT_ROOT'):
        backend._project_roots(project, {'input_roots': {'phase3': project / 'phase3'}}, row)
    assert backend.adoption_paths('18') == (
        'phase3/stage3/pnr/spare_cells.json', 'reports/spare_cell_coverage.json')
    with pytest.raises(em.Refusal, match='BACKEND_UNDECLARED_ADOPTION_PATH'):
        snap.adoption_target('input/declaration.json', backend.adoption_paths('18'))
    facts_file = tmp_path / 'native-facts.json'
    facts = {'observed_feature': 'source-control-only'}
    snap.dump(facts_file, facts)
    extra = {}
    facts_path = Path(facts_file)
    assert backend._native_facts({'native_facts': facts, 'native_facts_file': facts_path}, extra) == facts
    assert json_parameters({'native_facts_file': facts_path}) == {'native_facts_file': str(facts_path)}
    assert json_parameters(extra) == {'native_facts.json': str(facts_path)}
    roots, external = backend._project_roots(project, {'input_roots': {
        'input': project / 'input', str(selected.relative_to(project)): selected,
        'declaration': declaration, 'native_facts': facts_path},
        'native_facts_file': facts_path}, row)
    assert 'input/submission_template/tapeout_declaration.json' not in roots
    assert external == {'input_roots/declaration': declaration, 'native_facts_root': facts_path}
    selected_generation = {'run_id': 'bounded', 'outputs': {'project/x': 'a' * 64}}
    canonical = imported_result(step_id='18', source_sha='8f8142248cc9912a096012cfee13003bfea879af',
        selected_generation=selected_generation, binding={'step_id': '18'},
        copied={'phase3/stage3/pnr/spare_cells.json': 'a' * 64},
        primary_gates={'backend_canonical': 'PASS'}, design_verdict='PASS',
        consumer_detail={'status': 'CONSUMED'})
    assert canonical['selected_generation'] is selected_generation and canonical['copied']
    with pytest.raises(em.Refusal, match='BACKEND_NATIVE_FACTS_DOCUMENT_CHANGED'):
        backend._native_facts({'native_facts': {'observed_feature': 'changed'}, 'native_facts_file': facts_path}, {})
    head = subprocess.check_output(['git', '-C', str(backend.HERE), 'rev-parse', 'HEAD'], text=True).strip()
    request = StepRequest('15.5ic', project, {'declaration': declaration},
                          tmp_path / 'unused-na-lease', tmp_path / 'unused-na-record', head, source)
    assert backend._source_files(request) == dict(request.source_files)
    registry = FactoryRegistry()
    registry.register(backend)
    prepared = registry.prepare(request)
    assert prepared.disposition == 'declared_inapplicable'
    assert prepared.handoff == {'declaration': str(declaration),
        'declaration_sha256': snap.sha(declaration), 'facts': {'answers.deliverable': 'HARDMACRO'}}
    print('F3_API_WITNESS ' + json.dumps({'disposition': prepared.disposition,
          'manifest_entries': len(source), 'canonical_roots': backend.adoption_paths('18'),
          'native_executed': False}, sort_keys=True))

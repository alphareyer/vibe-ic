"""Focused normal-dispatch controls for the external Ultra choice handoff."""
import json
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import execution_modes as em
import execution_policy as policy
from programs.tests import test_execution_modes as H


def _runtime(tmp_path, monkeypatch, *, arms=None, response=None, wait_s=2, mode='ultra'):
    project = (tmp_path / 'project').resolve()
    project.mkdir(parents=True)
    input_path = project / 'source.txt'
    input_path.write_text('one input\n')
    arms = arms or (H.adapter('a', cost=8), H.adapter('b', cost=3))
    controller = H.controller(*arms)
    neutral_route = {'kind': 'neutral-test', 'ic_ip_path': 'IC'}
    monkeypatch.setattr(em, 'Context', H.NeutralContext)
    monkeypatch.setattr(policy, '_fixed_inputs', lambda runtime, step: {'text.txt': input_path})
    monkeypatch.setattr(policy, 'controller_fields', lambda **kwargs: {
        'ic_ip_path': 'IC', 'route_receipt': neutral_route,
        'intent_label': 'PROGRAM_DEFAULT', 'request_digest': ''})
    monkeypatch.setattr(policy, '_ordinary_runtime', {
        'identity': ('neutral-test',), 'project': project,
        'policy': {'mode': mode, 'choice': str(response.resolve()) if response else None,
                   'choice_wait_s': wait_s,
                   'request_receipt': {'invocation_id': 'wait-test-invocation'}},
        'route': {'source_sha': H.BASE, 'project_digest': '', 'ic_ip_path': 'IC',
                  'route_receipt': neutral_route},
        'parameters': {}, 'registry': controller.registry, 'controller': controller,
        'contexts': {}, 'bindings': {}, 'runs': {},
    })
    return project, controller, input_path, policy._ordinary_runtime


def _dispatch_in_thread(project, response, outcome):
    try:
        outcome['result'] = policy.dispatch_fixed_step(project, '1')
    except BaseException as exc:  # test thread must return the exact Controller refusal
        outcome['error'] = exc


def _dispatch_with_choice_writer(project, response, runtime, monkeypatch, respond):
    """Observe the real request; do not time source validation with a test join."""
    request_ready = threading.Event()
    completed = threading.Event()
    outcome = {}
    original_request = policy._selection_request

    def published(*args, **kwargs):
        request = original_request(*args, **kwargs)
        outcome['request_path'] = Path(request['request_path'])
        request_ready.set()
        return request

    monkeypatch.setattr(policy, '_selection_request', published)

    def writer():
        # As in the existing staged-writer controls, terminal dispatch wakes
        # the observer even when a producer refuses before publishing.
        while not request_ready.is_set() and not completed.wait(.001):
            pass
        if not request_ready.is_set():
            outcome['writer_error'] = AssertionError(
                f'selection request was not published: {outcome!r}')
            return
        try:
            choice = respond(outcome['request_path'])
            pending = response.with_name(response.name + '.pending')
            pending.write_text(json.dumps(choice))
            pending.replace(response)
        except BaseException as exc:
            outcome['writer_error'] = exc

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        # The real Controller/worker/adoption chain completes synchronously.
        # The product's external-choice deadline and finite polls are intact.
        _dispatch_in_thread(project, response, outcome)
    finally:
        completed.set()
        thread.join()
    if 'writer_error' in outcome:
        raise AssertionError(f'choice writer failed: {outcome!r}') from outcome['writer_error']
    return outcome, thread


def test_delayed_core_choice_is_adopted_and_imported_in_same_dispatch(tmp_path, monkeypatch, capsys):
    response = tmp_path / 'choice.json'
    project, controller, _, runtime = _runtime(tmp_path, monkeypatch, response=response)
    def respond(request_path):
        ctx = runtime['contexts']['1']
        run = runtime['runs']['1'][0]
        return H.choice(ctx, run, 'b')

    outcome, thread = _dispatch_with_choice_writer(
        project, response, runtime, monkeypatch, respond)
    request_path = outcome['request_path']
    request = json.loads(request_path.read_text())
    assert request['step_id'] == '1'
    assert request['response_path'] == str(response.resolve())
    assert len(request['eligible_arms']) == 2
    assert all(Path(row['receipt_path']).is_file() and row['receipt_sha256']
               for row in request['eligible_arms'])
    ctx = runtime['contexts']['1']
    run = runtime['runs']['1'][0]
    assert not thread.is_alive()
    assert 'error' not in outcome
    assert outcome['result']['status'] == 'ADOPTED'
    assert outcome['result']['selected'] == 'b'
    assert (project / 'value.txt').read_text() == 'ONE INPUT\n'
    assert controller.verify_adoption(ctx, run)['selected'] == 'b'
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event['type'] == 'vibeic.ai_selection_request'
    assert event['binding'] == request['binding']


def test_step9_adoption_uses_product_importer_for_current_project(tmp_path, monkeypatch):
    project = (tmp_path / 'project').resolve()
    project.mkdir()
    input_path = project / 'source.txt'
    input_path.write_text('one input\n')
    arm = replace(H.adapter('a'), step_id='9')
    registry = em.Registry()
    registry.register(arm)
    controller = em.Controller(registry, em.Budget(2, 512), {
        'meta': {'test_only': True},
        'steps': [{'id': '9', 'mandatory_gate_programs': ['transform'],
                   'required_output_contract': ['value.txt', 'measurement.json']}],
    })
    neutral_route = {'kind': 'neutral-test', 'ic_ip_path': 'IC'}
    monkeypatch.setattr(em, 'Context', H.NeutralContext)
    monkeypatch.setattr(policy, '_fixed_inputs',
                        lambda runtime, step: {'text.txt': input_path})
    monkeypatch.setattr(policy, 'controller_fields', lambda **kwargs: {
        'ic_ip_path': 'IC', 'route_receipt': neutral_route,
        'intent_label': 'PROGRAM_DEFAULT', 'request_digest': ''})
    runtime = {
        'identity': ('neutral-step9',), 'project': project,
        'policy': {'mode': 'ultra', 'choice': None, 'choice_wait_s': 0,
                   'request_receipt': {'invocation_id': 'step9-import-test'}},
        'route': {'source_sha': H.BASE, 'project_digest': '', 'ic_ip_path': 'IC',
                  'route_receipt': neutral_route},
        'parameters': {}, 'registry': registry, 'controller': controller,
        'contexts': {}, 'bindings': {}, 'runs': {},
    }
    monkeypatch.setattr(policy, '_ordinary_runtime', runtime)
    # This neutral transform fixture measures importer transport. Native
    # installation and late input binding have their own lifecycle controls.
    preparations = []
    monkeypatch.setattr(policy, '_prepare_step9',
                        lambda current, parameters: preparations.append((current, parameters)))
    pending = policy.dispatch_fixed_step(project, '9')
    assert pending['status'] == 'AWAITING_AI_SELECTION'
    ctx = runtime['contexts']['9']
    run = runtime['runs']['9'][0]
    calls = []

    def import_selected(current_project, current_context, current_controller,
                        current_run, adoption):
        calls.append((current_project, current_context, current_controller,
                      current_run, adoption))
        (current_project / 'phase2/stage2/synth').mkdir(parents=True)
        (current_project / 'phase2/stage2/synth/netlist.v').write_text(
            'module top; endmodule\n')
        return {'status': 'IMPORTED', 'copied': {
            'phase2/stage2/synth/netlist.v': 'test-digest'}}

    import execution_production as production
    monkeypatch.setattr(production, 'import_selected', import_selected)
    adopted = policy.dispatch_fixed_step(project, '9',
                                         choice=H.choice(ctx, run, 'a'))
    assert adopted['status'] == 'ADOPTED'
    assert adopted['consumer']['status'] == 'IMPORTED'
    assert (project / 'phase2/stage2/synth/netlist.v').is_file()
    assert not (project / 'project').exists()
    assert len(calls) == 1
    assert calls[0][:4] == (project, ctx, controller, run)
    assert calls[0][4]['selected'] == 'a'
    assert preparations == [(runtime, {}), (runtime, {})]


def test_no_choice_times_out_without_adoption_or_import(tmp_path, monkeypatch):
    response = tmp_path / 'choice.json'
    project, _, _, runtime = _runtime(tmp_path, monkeypatch, response=response, wait_s=1)
    result = policy.dispatch_fixed_step(project, '1')
    run = runtime['runs']['1'][0]
    assert result['status'] == 'AWAITING_AI_SELECTION'
    assert (run / 'selection-request.json').is_file()
    assert not (run / 'adoption.json').exists()
    assert not (project / 'value.txt').exists()


def test_unchanged_previous_step_response_is_ignored(tmp_path, monkeypatch):
    old_root = tmp_path / 'old-run'
    old_project = tmp_path / 'old-project'
    old_project.mkdir()
    old_ctx = H.context(old_project)
    old_controller = H.controller(H.adapter('a', cost=8), H.adapter('b', cost=3))
    old_controller.run(old_ctx, old_root, 'ultra-mode')
    stale = H.choice(old_ctx, old_root, 'b')
    response = tmp_path / 'choice.json'
    response.write_text(json.dumps(stale))
    project, _, _, runtime = _runtime(tmp_path / 'current', monkeypatch,
                                      response=response, wait_s=1)
    result = policy.dispatch_fixed_step(project, '1')
    run = runtime['runs']['1'][0]
    assert result['status'] == 'AWAITING_AI_SELECTION'
    assert not (run / 'adoption.json').exists()
    assert not (project / 'value.txt').exists()


def test_new_wrong_receipt_digest_is_refused_by_core(tmp_path, monkeypatch):
    response = tmp_path / 'choice.json'
    project, _, _, runtime = _runtime(tmp_path, monkeypatch, response=response)
    def respond(request_path):
        ctx = runtime['contexts']['1']
        run = runtime['runs']['1'][0]
        bad = H.choice(ctx, run, 'b')
        bad['receipt_sha256'] = '0' * 64
        return bad

    outcome, thread = _dispatch_with_choice_writer(
        project, response, runtime, monkeypatch, respond)
    assert not thread.is_alive()
    assert isinstance(outcome.get('error'), em.Refusal)
    assert outcome['error'].code == 'AI_RECEIPT_DIGEST_MISMATCH'


@pytest.mark.parametrize('field', ['run_id', 'comparison_digest', 'frozen_work_digest'])
@pytest.mark.parametrize('damage', ['changed', 'missing'])
def test_external_choice_must_acknowledge_current_comparison(
        tmp_path, monkeypatch, field, damage):
    response = tmp_path / 'choice.json'
    project, _, _, runtime = _runtime(tmp_path, monkeypatch, response=response)
    def respond(request_path):
        ctx = runtime['contexts']['1']
        run = runtime['runs']['1'][0]
        choice = H.choice(ctx, run, 'b')
        if damage == 'missing':
            choice.pop(field)
        else:
            choice[field] = '0' * 64
        return choice

    outcome, thread = _dispatch_with_choice_writer(
        project, response, runtime, monkeypatch, respond)
    run = runtime['runs']['1'][0]
    assert not thread.is_alive()
    observed = (outcome['error'].code if isinstance(outcome.get('error'), em.Refusal)
                else outcome['result']['status'])
    assert observed == 'AI_COMPARISON_UNBOUND'
    assert not (run / 'selected').exists()
    assert not (project / 'value.txt').exists()


@pytest.mark.parametrize('fault,expected', [
    ('fail_gate', 'FAIL'), ('process_error', 'NOT_MEASURED'),
])
def test_terminal_fail_or_unmeasured_does_not_read_shared_response(
        tmp_path, monkeypatch, fault, expected):
    response = tmp_path / 'choice.json'
    response.write_text('not-json')
    project, _, _, runtime = _runtime(
        tmp_path, monkeypatch, arms=(H.adapter('a', fault=fault),), response=response)
    result = policy.dispatch_fixed_step(project, '1')
    run = runtime['runs']['1'][0]
    assert result['status'] == expected
    assert not (run / 'selection-request.json').exists()
    assert not (run / 'adoption.json').exists()
    assert not (project / 'value.txt').exists()


def test_default_without_live_runtime_does_not_wait(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(policy, '_ordinary_runtime', None)
    monkeypatch.setattr(policy, '_wait_for_choice', lambda *args: called.append(args))
    assert policy.dispatch_fixed_step(tmp_path, '1') is None
    assert called == []


def test_explicit_choice_still_reaches_core_for_terminal_result(tmp_path, monkeypatch):
    response = tmp_path / 'choice.json'
    response.write_text('not-json')
    project, _, _, runtime = _runtime(
        tmp_path, monkeypatch, arms=(H.adapter('a', fault='fail_gate'),),
        response=response)
    first = policy.dispatch_fixed_step(project, '1')
    ctx = runtime['contexts']['1']
    run = runtime['runs']['1'][0]
    assert first['status'] == 'FAIL'
    with pytest.raises(em.Refusal, match='AI_CHOICE_INELIGIBLE'):
        policy.dispatch_fixed_step(project, '1', choice=H.choice(ctx, run, 'a'))
    assert not (run / 'selection-request.json').exists()


@pytest.mark.parametrize('state',['absent','valid','wrong_comparison'])
def test_allocated_choice_poll_is_finite_and_bound(tmp_path,monkeypatch,state):
    from types import SimpleNamespace
    context=SimpleNamespace(step_id='neutral')
    request={'run_id':'run','comparison_digest':'comparison','frozen_work_digest':'work'}
    monkeypatch.setattr(policy,'_selection_request',lambda *a:request)
    attempts=[]
    def read(*a):
        attempts.append(1)
        assert len(attempts)<=6, 'choice wait exceeded the finite polling budget on a frozen clock'
        if state=='absent' or len(attempts)<2:return None
        return dict(request,comparison_digest='wrong' if state=='wrong_comparison' else 'comparison')
    monkeypatch.setattr(policy,'_read_new_choice',read)
    monkeypatch.setattr(policy,'time',SimpleNamespace(monotonic=lambda:1000.,sleep=lambda secs:None))
    if state=='wrong_comparison':
        with pytest.raises(policy.Refusal) as refused:
            policy._wait_for_choice(context,tmp_path,{},tmp_path/'choice.json',None,1)
        assert refused.value.code=='AI_COMPARISON_UNBOUND'
    else:
        got=policy._wait_for_choice(context,tmp_path,{},tmp_path/'choice.json',None,1)
        assert got==(None if state=='absent' else request)
    assert len(attempts)==(6 if state=='absent' else 2)


def _validate_frontend_boundary(outputs, binding):
    # A finite neutral transform's current successful validation proves the
    # binary source boundary. Its independent cost metric remains available.
    evidence = H.validate_text(outputs, binding)
    return replace(evidence, metrics=dict(evidence.metrics, source_boundary=1.0))


@pytest.fixture(scope='module')
def objective_frontend_arms():
    arms = []
    for name, cost in [('a', 8), ('b', 3)]:
        arm = H.adapter(name, cost=cost)
        source = dict(arm.source_files)
        here = Path(__file__).resolve()
        source[str(here)] = em.digest(here)
        source.update({str(p): em.digest(p) for p in em._source_closure(source)})
        # This is the frontend protocol over the existing neutral text tools,
        # not a simulator/tool qualification or native IC execution.
        arms.append(replace(arm, tool_id='frontend-worker', source_files=source,
                            objective={'parameters_from': 'issued_manifest'},
                            validate=_validate_frontend_boundary))
    return tuple(arms)


@pytest.mark.parametrize('parameters,selected,refusal', [
    pytest.param({}, 'a', None, id='default-boundary-max'),
    pytest.param({'metric': 'cost', 'direction': 'min'}, 'b', None,
                 id='custom-metric-min'),
    pytest.param({'metric': 'source_boundary', 'direction': 'min'}, 'a', None,
                 id='explicit-boundary-min'),
    pytest.param({'metric': 'source_boundary', 'direction': None}, 'a',
                 'OBJECTIVE_INVALID: None', id='explicit-none-refused'),
    pytest.param({'metric': 'source_boundary', 'direction': 'sideways'}, 'a',
                 'OBJECTIVE_INVALID: sideways', id='invalid-direction-refused'),
    pytest.param({'metric': 'cost'}, 'a', 'OBJECTIVE_INVALID: None',
                 id='custom-metric-missing-direction-refused'),
])
def test_frontend_objective_reaches_core_choice_consumer(
        tmp_path, monkeypatch, objective_frontend_arms, parameters, selected, refusal):
    project, controller, _, runtime = _runtime(
        tmp_path, monkeypatch, arms=objective_frontend_arms, wait_s=0)
    pending = policy.dispatch_fixed_step(project, '1', parameters=parameters)
    assert pending['status'] == 'AWAITING_AI_SELECTION'
    context = runtime['contexts']['1']
    run = runtime['runs']['1'][0]
    comparison = json.loads((run / 'comparison.json').read_text())
    assert len(comparison['eligible_arms']) == 2
    decision = H.choice(context, run, selected)
    observation = {'parameters': parameters, 'objective': dict(context.objective),
                   'eligible': comparison['eligible_arms'], 'selected': selected}
    (tmp_path / 'OBJECTIVE_OBSERVATION.json').write_text(json.dumps(observation, indent=2))
    if refusal:
        with pytest.raises(em.Refusal, match=refusal):
            policy.dispatch_fixed_step(project, '1', parameters=parameters, choice=decision)
        assert json.loads((run / 'adoption.json').read_text())['status'] == 'REFUSED'
        assert not (project / 'value.txt').exists()
    else:
        adopted = policy.dispatch_fixed_step(project, '1', parameters=parameters, choice=decision)
        assert adopted['status'] == 'ADOPTED'
        assert controller.verify_adoption(context, run)['selected'] == selected
        assert (project / 'value.txt').read_text() == 'ONE INPUT\n'
        assert context.objective['metric'] == parameters.get('metric', 'source_boundary')
        assert context.objective['direction'] == parameters.get('direction', 'max')


_RETAINED_STEP4_MEASUREMENTS = (
    'phase2/stage1/sim_professional/l10_unit_tb/results.xml',
    'phase2/stage1/sim_professional/neutral_logic/cocotb_run.log',
    'phase2/stage1/sim_professional/neutral_logic/results.xml',
)


def _retained_step4_project(root):
    root.mkdir(parents=True)
    sources = {
        'text.txt': 'one input\n',
        'phase2/stage1/rtl/neutral_logic.v': 'module neutral_logic; endmodule\n',
        'phase1/generated_docs/L10_TEST_CASES.json': '{}\n',
        'phase1/generated_docs/L12_BEHAVIORAL_SEQUENCES.json': '{}\n',
        'phase2/stage1/sim_professional/neutral_logic/Makefile': '# source configuration\n',
        'phase2/stage1/sim_professional/neutral_logic/tb_neutral.py': '# testbench source\n',
        'phase2/stage1/sim_professional/neutral_logic/helper.py': '# helper source\n',
        'phase2/stage1/sim_professional/neutral_logic/support.json': '{"operand": 2}\n',
        'phase2/stage1/sim/tb/scenario.v': 'module scenario; endmodule\n',
    }
    for rel, text in sources.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    plan = root / 'phase2/stage1/sim_professional/neutral_logic/verification_plan.json'
    plan.write_text(json.dumps({'random_goal_plan': {'binding': {
        rel: em.digest(root / rel) for rel in ('text.txt',
            'phase2/stage1/sim_professional/neutral_logic/support.json')}}}))
    for rel in _RETAINED_STEP4_MEASUREMENTS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('retained earlier measurement\n')
    return root


@pytest.fixture(scope='module')
def retained_step4_input_contract(tmp_path_factory):
    # Consume the actual source owner's registration, including its shared
    # professional-plan resolver. No native simulator component is executed.
    import execution_frontend_providers as frontend
    from execution_source_snapshot import SourceSnapshot, using
    project = _retained_step4_project(tmp_path_factory.mktemp('input-contract') / 'project')
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        registry = em.Registry(snapshot=snapshot)
        frontend.register_factories(registry, step_ids=('4',), project=project,
            parameters={'top': 'neutral_logic'}, execution_mode='ultra')
        registry.finalize()
    return tuple(registry.adapters('4')[0].input_contract)


def _validate_retained_step4_measurements(outputs, binding):
    evidence = H.validate_text(outputs, binding)
    expected = (outputs / 'value.txt').read_bytes()
    artifacts = dict(evidence.outputs)
    for rel in _RETAINED_STEP4_MEASUREMENTS:
        assert (outputs / rel).read_bytes() == expected
        artifacts[rel] = em.digest(outputs / rel)
    return replace(evidence, outputs=artifacts,
                   metrics=dict(evidence.metrics, source_boundary=1.0))


@pytest.mark.parametrize('change_source', [False, True], ids=['retained-output-import', 'source-drift-refused'])
def test_step4_retained_measurements_are_outputs_not_frozen_sources(
        tmp_path, monkeypatch, retained_step4_input_contract, change_source):
    """Real neutral subprocesses, dispatch/adopt/Journal/verify; no EDA or IC."""
    fixed_inputs = policy._fixed_inputs
    project, _, _, runtime = _runtime(tmp_path, monkeypatch, wait_s=0)
    # _runtime creates the neutral input. Populate this same canonical project.
    project.rename(tmp_path / 'empty-runtime-project')
    _retained_step4_project(project)
    monkeypatch.setattr(policy, '_fixed_inputs', fixed_inputs)
    arm = H.adapter('a')
    source = dict(arm.source_files)
    here = str(Path(__file__).resolve())
    source[here] = em.digest(Path(here))
    source.update({str(path): em.digest(path) for path in em._source_closure(source)})
    from execution_provider_catalog import implementation_closure
    execution_cwd = (project / 'reports/execution' /
        runtime['policy']['request_receipt']['invocation_id'] / '4' / arm.arm_id / 'outputs')
    source.update({str(path): em.digest(path)
                   for path in implementation_closure(Path(here), cwd=execution_cwd)})
    components = arm.components + (em.Component('measurement-artifacts',
        (str(Path(sys.executable).resolve()), here,
         '--neutral-retained-measurements', '{outputs}')),)
    required = ('value.txt', 'measurement.json', *_RETAINED_STEP4_MEASUREMENTS)
    arm = replace(arm, step_id='4', tool_id='frontend-worker', source_files=source,
        objective={'parameters_from': 'issued_manifest'},
        components=components, validate=_validate_retained_step4_measurements,
        input_contract=retained_step4_input_contract,
        required_outputs=required, output_contract={rel: (rel,) for rel in required})
    registry = em.Registry(); registry.register(arm)
    controller = em.Controller(registry, em.Budget(1, 512), {
        'meta': {'test_only': True}, 'steps': [{'id': '4',
        'mandatory_gate_programs': ['transform'], 'required_output_contract': list(required)}]})
    runtime.update(registry=registry, controller=controller)
    pending = policy.dispatch_fixed_step(project, '4', parameters={'top': 'neutral_logic'})
    assert pending['status'] == 'AWAITING_AI_SELECTION'
    context = runtime['contexts']['4']; run = runtime['runs']['4'][0]
    choice = H.choice(context, run, 'a')
    before = {rel: em.digest(project / rel) for rel in _RETAINED_STEP4_MEASUREMENTS}
    adopted = policy.dispatch_fixed_step(project, '4', parameters={'top': 'neutral_logic'}, choice=choice)
    assert adopted['status'] == 'ADOPTED'
    assert controller.verify_adoption(context, run)['selected'] == 'a'
    assert not set(_RETAINED_STEP4_MEASUREMENTS).intersection(context.inputs)
    sources = {
        'phase2/stage1/sim_professional/neutral_logic/Makefile',
        'phase2/stage1/sim_professional/neutral_logic/tb_neutral.py',
        'phase2/stage1/sim_professional/neutral_logic/helper.py',
        'phase2/stage1/sim_professional/neutral_logic/support.json',
        'phase2/stage1/sim/tb/scenario.v',
        'phase2/stage1/rtl/neutral_logic.v',
    }
    assert sources.issubset(context.inputs)
    for rel in _RETAINED_STEP4_MEASUREMENTS:
        assert (project / rel).read_text() == 'ONE INPUT\n'
        assert em.digest(project / rel) != before[rel]
    if change_source:
        (project / 'phase2/stage1/sim_professional/neutral_logic/helper.py').write_text('# changed source\n')
        with pytest.raises(em.Refusal, match='CURRENT_INPUT_CHANGED'):
            controller.verify_adoption(context, run)
    (tmp_path / 'STEP4_INPUT_OWNER_OBSERVATION.json').write_text(json.dumps({
        'issued_inputs': sorted(context.inputs), 'retained_before': before,
        'current_after': {rel: em.digest(project / rel) for rel in before},
        'adoption_status': adopted['status'], 'source_drift_refused': change_source,
        'scope': 'neutral process input/output ownership; no simulator qualification'}, indent=2))


if __name__ == '__main__':
    # Source-bound finite file producer for the ownership regression above.
    # No pytest, simulator or IC runner is invoked by this CLI.
    if len(sys.argv) != 3 or sys.argv[1] != '--neutral-retained-measurements':
        raise SystemExit(2)
    root = Path(sys.argv[2])
    content = (root / 'value.txt').read_bytes()
    for rel in _RETAINED_STEP4_MEASUREMENTS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

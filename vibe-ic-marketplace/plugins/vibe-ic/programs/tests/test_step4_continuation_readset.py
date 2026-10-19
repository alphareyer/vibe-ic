"""Fresh generated TB publication: real emitter/L12 reader/Core/Journal, no EDA."""
from dataclasses import replace
import inspect
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import execution_modes as em
import execution_policy as policy
from programs.tests import test_execution_modes as H
from programs.tests import test_execution_policy_choice_wait as W


def _emit_sources(outputs, inputs):
    import testbench_gen
    for rel in ('phase1/generated_docs/L10_TEST_CASES.json',
                'phase1/generated_docs/L12_BEHAVIORAL_SEQUENCES.json',
                'phase2/stage1/rtl/neutral_logic.v'):
        target = outputs / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inputs / rel, target)
    report = {}
    count = testbench_gen.emit_unit_tbs(outputs, 'neutral_logic', report=report)
    if count != 1:
        raise ValueError('real unit-TB emitter did not produce its one declared case: ' + str(report))


def _validate_sources(outputs, binding):
    import execution_step4 as step4
    import l12_tb_coverage_check as l12
    value = H.validate_text(outputs, binding)
    actual = l12.check(outputs,
        outputs / 'phase1/generated_docs/L12_BEHAVIORAL_SEQUENCES.json',
        outputs / 'phase2/stage1/sim/tb', outputs / 'l12-arm.json', True)
    # Both owner versions are exercised through their original API: the v1
    # helper lacked the input/output distinction argument. Its real missing
    # publication, rather than an unsupported argument, must cause the red.
    owned = (step4.continuation_artifacts(outputs, binding)
             if 'facts' in inspect.signature(step4.continuation_artifacts).parameters
             else step4.continuation_artifacts(outputs))
    return replace(value, verdict='PASS' if value.verdict == 'PASS' and actual == 0 else 'FAIL',
        gates={**value.gates, 'l12_tb_coverage': 'PASS' if actual == 0 else 'FAIL'},
        outputs={**value.outputs, **owned},
        metrics={'source_boundary': float(value.verdict == 'PASS' and actual == 0)})


def test_selected_fresh_unit_tb_reaches_same_l12_consumer(tmp_path, monkeypatch):
    from execution_provider_catalog import implementation_closure
    import l12_tb_coverage_check as l12
    project, _, _, runtime = W._runtime(tmp_path, monkeypatch)
    declarations = {
        'phase1/generated_docs/L10_TEST_CASES.json': {'test_cases': [{
            'name': 'tb_declared_sequence', 'kind': 'functional_vector',
            'description': 'Drive the declared combinational interface.'}]},
        'phase1/generated_docs/L12_BEHAVIORAL_SEQUENCES.json': {
            'sequences': [{'id': 'declared_sequence'}]},
    }
    for rel, data in declarations.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    rtl = project / 'phase2/stage1/rtl/neutral_logic.v'
    rtl.parent.mkdir(parents=True)
    rtl.write_text('module neutral_logic(input a, output y); assign y = a; endmodule\n')
    inputs = {'text.txt': project / 'source.txt', **{
        rel: project / rel for rel in (*declarations, str(rtl.relative_to(project)))}}
    arm = H.adapter('a')
    here = Path(__file__).resolve()
    source = {**arm.source_files, str(here): em.digest(here)}
    source.update({str(path): em.digest(path) for path in em._source_closure(source)})
    cwd = project / 'reports/execution/wait-test-invocation/4/a/outputs'
    gate_cli = here.parents[1] / 'l12_tb_coverage_check.py'
    for entry in (here, gate_cli):
        source.update({str(path): em.digest(path)
                       for path in implementation_closure(entry, cwd=cwd)})
    source.update({str(path): em.digest(path) for path in em._source_closure(source)})
    arm = replace(arm, step_id='4', tool_id='frontend-worker', source_files=source,
        components=arm.components + (em.Component('real-unit-tb-source-emitter',
            (str(Path(sys.executable).resolve()), str(here), '--emit-sources', '{outputs}', '{inputs}')),
            em.Component('l12_tb_coverage', (str(Path(sys.executable).resolve()),
                str(gate_cli), '{outputs}',
                '--json', '{outputs}/l12-component.json', '--strict'))),
        objective={'parameters_from': 'issued_manifest'}, validate=_validate_sources)
    registry = em.Registry(); registry.register(arm)
    controller = em.Controller(registry, em.Budget(1, 512), {
        'meta': {'test_only': True}, 'steps': [{'id': '4',
        'mandatory_gate_programs': ['transform', 'l12_tb_coverage'],
        'required_output_contract': ['value.txt', 'measurement.json']}]})
    runtime.update(registry=registry, controller=controller)
    monkeypatch.setattr(policy, '_fixed_inputs', lambda *a, **k: inputs)
    pending = policy.dispatch_fixed_step(project, '4')
    assert pending['status'] == 'AWAITING_AI_SELECTION', pending
    context = runtime['contexts']['4']; run = runtime['runs']['4'][0]
    response = tmp_path / 'choice.json'
    response.write_text(json.dumps(H.choice(context, run, 'a')))
    runtime['policy']['choice'] = str(response)
    adopted = policy.dispatch_fixed_step(project, '4')
    assert adopted['status'] == 'ADOPTED', adopted
    arm_root = run / 'a/outputs'
    arm_rc = l12.check(arm_root,
        arm_root / 'phase1/generated_docs/L12_BEHAVIORAL_SEQUENCES.json',
        arm_root / 'phase2/stage1/sim/tb', tmp_path / 'arm-reader.json', True)
    parent_rc = l12.check(project,
        project / 'phase1/generated_docs/L12_BEHAVIORAL_SEQUENCES.json',
        project / 'phase2/stage1/sim/tb', tmp_path / 'parent-reader.json', True)
    observed = [arm_rc, parent_rc]
    assert observed == [0, 0], ('same mandatory L12 reader before and after selected import', observed)
    assert controller.verify_adoption(context, run)['status'] == 'ADOPTED'
    for rel in inputs:
        assert em.digest(inputs[rel]) == context.binding()['inputs'][rel]
    (tmp_path / 'READSET_OBSERVATION.json').write_text(json.dumps({
        'arm_l12_rc': arm_rc, 'imported_parent_l12_rc': parent_rc,
        'selected_outputs': adopted['selected_generation']['outputs'],
        'scope': 'Real source emitter, L12 checker, neutral text Core/adopt/Journal; no simulator or mocked FCC leaf.'}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--emit-sources':
        raise SystemExit(2)
    _emit_sources(Path(sys.argv[2]), Path(sys.argv[3]))

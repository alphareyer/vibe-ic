"""Focused source-only controls for the ordinary A6/A7/A8 outer route.

Port the four archived review cases to 2c0's concrete Context/finalized Registry
and retain the inner native-marker refusal. No native tool or PDK is executed.
"""
from dataclasses import replace
from pathlib import Path
import inspect
import itertools
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS.parent))
import execution_modes as em
import execution_policy as policy
import execution_adapters_analog as analog
import execution_analog_worker as worker
import execution_analog_contract as contracts
import execution_production as production
import analog_one_shot_runner as runner
from programs.tests import test_execution_receipt_chain as live


class AnalogLiveReachabilityReview(unittest.TestCase):
    def setUp(self):
        self.saved_environment = dict(os.environ)
        for name in (policy.ENV, policy._CAPABILITY_FD_ENV, 'VIBEIC_EXECUTION_AUTH_SOCKET'):
            os.environ.pop(name, None)
        self.saved_runtime = policy._ordinary_runtime
        policy._ordinary_runtime = None
        self.temp = tempfile.TemporaryDirectory(prefix='analog-outer-control-')
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()

    def tearDown(self):
        self.close_issuer()
        policy._ordinary_runtime = self.saved_runtime
        os.environ.clear()
        os.environ.update(self.saved_environment)
        self.temp.cleanup()

    def close_issuer(self):
        for proc, fd, channel in live._launchers:
            channel.close()
            proc.wait(timeout=10)
            if proc.stderr:
                proc.stderr.close()
            try:
                os.close(fd)
            except OSError:
                pass
        live._launchers.clear()

    def ordinary(self):
        # Reuse the repository's real isolated canonical entry fixture. This
        # does not patch the issuer, Registry, Controller or source verifiers.
        issued = live.real_entry('IC', 'ultra', self.project)
        declaration = self.project / analog.DECLARATIONS[1]
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(json.dumps({'blocks': [{'name': 'unit', 'type': 'amplifier'}]}))
        upstream = self.project / 'phase3/analog/unit/layout.mag'
        upstream.parent.mkdir(parents=True)
        upstream.write_text('source-only upstream INPUT; not native layout evidence\n')
        runtime = policy.bootstrap(self.project)
        return issued, runtime

    def positive(self, step):
        issued, runtime = self.ordinary()
        controller = runtime['controller']
        registry = runtime['registry']
        prior = {row: tuple(registry.adapters(row)) for row in ('9', '8', '16', '30', 'M3', 'M4')}
        name = analog.ANALOG_STEPS[step]
        with patch.object(analog, 'prepare', wraps=analog.prepare) as prepared, \
                patch.object(analog, 'consume', wraps=analog.consume) as consumed:
            result = runner.step_for_block(self.project, {'name': 'unit'}, name)
        self.assertEqual(result.status, 'NOT_MEASURED')
        self.assertEqual(prepared.call_count, 1)
        self.assertEqual(consumed.call_count, 1)
        self.assertIs(prepared.call_args.kwargs['controller'], controller)
        self.assertIs(prepared.call_args.kwargs['registry'], registry)
        self.assertIs(consumed.call_args.args[2], controller)
        self.assertIs(type(prepared.call_args.args[0]), em.Context)
        ctx = runtime['contexts'][step]
        self.assertIs(ctx, prepared.call_args.args[0])
        root, original = runtime['runs'][step]
        plan = json.loads((root / 'plan.json').read_text())
        self.assertEqual(plan['arms'], [])
        self.assertEqual(plan['binding']['route_receipt'], issued['route'])
        self.assertEqual(plan['binding']['request_digest'], issued['request']['request_digest'])
        self.assertEqual(plan['binding'], result.extras['execution_result']['binding'])
        self.assertEqual(result.extras['execution_result']['consumer'], 'analog.consume')
        self.assertEqual(original['status'], 'NOT_MEASURED')
        self.assertIs(runtime['controller'], controller)
        self.assertIs(controller.registry, registry)
        self.assertTrue(registry._finalized)
        self.assertEqual(prior, {row: tuple(registry.adapters(row)) for row in prior})
        self.assertFalse((root / 'selected').exists())
        self.assertFalse((root / ('analog-' + step.lower())).exists())
        self.assertFalse((self.project / 'phase3/analog/hardmacro').exists())
        return runtime, root, ctx

    def test_ordinary_outer_a6_prepare_consume_returns_not_measured(self):
        self.positive('A6')

    def test_ordinary_outer_a7_prepare_consume_returns_not_measured(self):
        self.positive('A7')

    def test_ordinary_outer_a8_prepare_consume_returns_not_measured(self):
        self.positive('A8')

    def test_canonical_factories_register_only_existing_analog_family(self):
        # The authoritative focus narrows registration to A6/A7/A8 and keeps
        # the base's M3/M4 registrations. 2c0 uses its ordinary em.Registry.
        registry = em.Registry()
        analog.register_adapters(registry)
        self.assertEqual(tuple(analog.STEP_IDS), ('A6', 'A7', 'A8'))
        self.assertEqual(tuple(analog.ANALOG_STEPS), tuple(analog.ALL_STEP_IDS[:9]))
        for step in analog.STEP_IDS:
            adapters = registry.adapters(step)
            self.assertEqual(len(adapters), 1)
            arm = adapters[0]
            self.assertEqual(arm.arm_id, 'analog-' + step.lower())
            self.assertIn(str(analog.WORKER), arm.components[0].argv)
            self.assertIs(arm.validate, analog.validate)
            self.assertFalse(arm.available)
            self.assertFalse(arm.qualified)
            self.assertEqual(set(arm.output_contract),
                             set(analog.CONTRACTS[step]['portfolio_policy']['required_output_contract']))
        for step in ('A1', 'A2', 'A3', 'A4', 'A5', 'A9', 'M1', 'M2', 'M3', 'M4'):
            self.assertEqual(registry.adapters(step), [])

    def test_public_prepare_freezes_typed_worker_contract(self):
        # Port the donor-only record/lease assertions to the concrete current
        # issuer request and existing Controller budget; no new lease issuer.
        source = inspect.getsource(analog.prepare)
        self.assertIn('controller._context_binding(context)', source)
        self.assertIn('source_requirements', source)
        self.assertIn('prepared.binding()', source)
        worker_source = inspect.getsource(worker.execute)
        for member in ('input_hashes', 'source_files', 'producer_work_contract'):
            self.assertIn(member, worker_source)
        self.assertEqual(analog.ENGINE_FAMILIES['A4'], ('ngspice',))
        for step in analog.STEP_IDS:
            self.assertTrue(analog.producer_work_contract(step)['all_current_canonical_obligations_required'])
            # Actual checked-in flow/portfolio bytes, not a hand-authored gate list.
            self.assertTrue(contracts.canonical_gate_population(step))

    def test_native_marker_cannot_replace_live_controller_scope(self):
        binding = {'step_id': 'A4'}
        with patch.dict(os.environ, {'VIBEIC_EXECUTION_BINDING': json.dumps(binding),
                                    'VIBEIC_F5_NATIVE_LOCAL': '1'}, clear=True):
            with self.assertRaises(em.Refusal) as refusal:
                with worker._producer_reentry(Path('/tmp/isolated-analog'), binding):
                    self.fail('native marker granted controller authority')
            self.assertEqual(refusal.exception.code, 'PRODUCTION_CHILD_SCOPE_UNBOUND')

    def test_binding_and_fail_ratchets_cannot_be_demoted(self):
        binding = {'step_id': 'A4', 'objective': {'blocks': ['b']}}
        sources = {str(analog.PROGRAMS / 'analog_real_corner_sweep.py'): 'x'}
        calls = [{'entrypoint': 'analog_real_corner_sweep.run_block', 'pid': 7,
                  'block': 'b', 'source_sha256': 'x', 'result': {'verdict': 'FAIL'}}]
        with self.assertRaises(em.Refusal) as refusal:
            worker.producer_work(calls, binding, sources)
        self.assertEqual(refusal.exception.code, 'ANALOG_PRODUCER_BRANCH_UNBOUND')
        self.assertEqual(worker.producer_result_verdict([{'verdict': 'FAIL'}, {'verdict': 'PASS'}]), 'FAIL')
        self.assertEqual(worker.producer_result_verdict([{'verdict': 'PASS'}, {'executed': False}]), 'NOT_MEASURED')

    def test_missing_callback_and_serialized_facts_never_enter_inner_producer(self):
        for step in analog.STEP_IDS:
            for facts in ({}, {'VIBEIC_F5_NATIVE_LOCAL': '1'},
                          {'VIBEIC_EXECUTION_BINDING': json.dumps({'step_id': step})}):
                with self.subTest(step=step, facts=facts), patch.dict(os.environ, facts, clear=True):
                    with self.assertRaisesRegex(em.Refusal, 'PRODUCTION_CHILD_SCOPE_UNBOUND'):
                        with worker._producer_reentry(self.project, {'step_id': step}):
                            self.fail('inner producer entered')
        receipt = self.project / 'callback.json'
        receipt.write_text(json.dumps({'callback': 'serialized', 'token': 'x', 'digest': '0' * 64}))
        with patch.dict(os.environ, {'VIBEIC_CALLBACK_FILE': str(receipt),
                                    'VIBEIC_F5_NATIVE_LOCAL': '1'}, clear=True), \
                patch.object(worker, '_live_callback', lambda: True, create=True):
            with self.assertRaisesRegex(em.Refusal, 'PRODUCTION_CHILD_SCOPE_UNBOUND'):
                with worker._producer_reentry(self.project, {'step_id': 'A6', 'callback': object()}):
                    self.fail('durable facts granted authority')

    def test_wrong_project_step_and_current_request_are_refused(self):
        _, runtime = self.ordinary()
        policy.dispatch_fixed_step(self.project, 'A6')
        ctx = runtime['contexts']['A6']
        fields = dict(project=self.project, registry=runtime['registry'], controller=runtime['controller'])
        with self.assertRaisesRegex(em.Refusal, 'ANALOG_STEP_CONTEXT_UNBOUND'):
            analog.prepare(replace(ctx, step_id='A4'), **fields)
        with self.assertRaisesRegex(em.Refusal, 'ANALOG_FRONTDOOR_PROJECT_DISAGREES'):
            analog.prepare(ctx, **dict(fields, project=Path(self.temp.name)))
        with self.assertRaises(em.Refusal):
            analog.prepare(replace(ctx, request_digest='f' * 64), **fields)
        with self.assertRaisesRegex(em.Refusal, 'PRODUCTION_CHILD_SCOPE_UNBOUND'):
            analog.prepare(ctx, **dict(fields, controller=type('CallerController', (), {})()))

    def test_current_input_change_refuses_replay(self):
        _, runtime = self.ordinary()
        policy.dispatch_fixed_step(self.project, 'A6')
        (self.project / 'phase3/analog/unit/layout.mag').write_text('changed INPUT')
        with self.assertRaisesRegex(em.Refusal, 'FIXED_STEP_REENTRY_CHANGED'):
            policy.dispatch_fixed_step(self.project, 'A6')
        self.assertFalse((runtime['runs']['A6'][0] / 'selected').exists())

    def test_detached_live_controller_and_durable_receipt_refuse(self):
        _, runtime = self.ordinary()
        result = policy.dispatch_fixed_step(self.project, 'A6')
        root, original = runtime['runs']['A6']
        prepared = analog.prepare(runtime['contexts']['A6'], project=self.project,
                                  registry=runtime['registry'], controller=runtime['controller'])
        self.close_issuer()
        with self.assertRaises(em.Refusal):
            analog.consume(self.project, prepared, runtime['controller'], root, original)
        self.assertEqual(result['status'], 'NOT_MEASURED')
        self.assertFalse((root / 'selected').exists())

    def test_wrong_run_step_and_fabricated_adoption_refuse(self):
        _, runtime = self.ordinary()
        policy.dispatch_fixed_step(self.project, 'A6')
        policy.dispatch_fixed_step(self.project, 'A7')
        prepared = analog.prepare(runtime['contexts']['A6'], project=self.project,
                                  registry=runtime['registry'], controller=runtime['controller'])
        root6, original = runtime['runs']['A6']
        root7, _ = runtime['runs']['A7']
        with self.assertRaisesRegex(em.Refusal, 'ANALOG_CURRENT_INVOCATION_UNBOUND'):
            analog.consume(self.project, prepared, runtime['controller'], root7, original)
        with self.assertRaises(em.Refusal):
            analog.consume(self.project, prepared, runtime['controller'], root6, {'status': 'ADOPTED'})
        with self.assertRaisesRegex(em.Refusal, 'PRODUCTION_CHILD_SCOPE_UNBOUND'):
            analog.consume(self.project, object(), runtime['controller'], root6, original)

    def test_consumer_reopens_signed_comparison_and_rejects_edits(self):
        _, runtime = self.ordinary()
        policy.dispatch_fixed_step(self.project, 'A6')
        root, original = runtime['runs']['A6']
        prepared = analog.prepare(runtime['contexts']['A6'], project=self.project,
                                  registry=runtime['registry'], controller=runtime['controller'])
        comparison = json.loads((root / 'comparison.json').read_text())
        comparison['status'] = 'PASS'
        (root / 'comparison.json').write_text(json.dumps(comparison))
        with self.assertRaisesRegex(em.Refusal, 'COMPARISON_AUTHORITY_CHANGED'):
            analog.consume(self.project, prepared, runtime['controller'], root, original)
        self.assertFalse((root / 'selected').exists())

    def test_measured_fail_dominates_pass_and_missing_siblings(self):
        for rows in itertools.permutations([{'verdict': 'PASS'}, {'verdict': 'FAIL'}, {'verdict': 'NOT_MEASURED'}]):
            self.assertEqual(worker.producer_result_verdict(list(rows)), 'FAIL')
        self.assertEqual(worker.producer_result_verdict([{'rc': 0}]), 'NOT_MEASURED')
        self.assertEqual(worker.producer_result_verdict([]), 'NOT_MEASURED')
        self.assertEqual(worker._gate_status(0, {}), 'NOT_MEASURED')
        self.assertEqual(worker._gate_status(2, {'verdict': 'FAIL'}), 'FAIL')
        self.assertEqual(worker._gate_status(1, {'verdict': 'PASS'}), 'FAIL')

    def test_source_bound_a6_complementary_result_ratchet(self):
        path = PROGRAMS / 'analog_a6_native_pv.py'
        digest = em.digest(path)
        binding = {'step_id': 'A6', 'objective': {'blocks': ['unit']}}
        def measured(drc):
            return worker.producer_work([{'entrypoint': 'analog_a6_native_pv.run_block_pv',
                'pid': os.getpid(), 'block': 'unit', 'source_sha256': digest,
                'result': {'drc': drc, 'lvs': {'verdict': 'PASS', 'executed': True}}}],
                binding, {str(path): digest})['design_verdict']
        self.assertEqual(measured({'verdict': 'FAIL', 'executed': True}), 'FAIL')
        self.assertEqual(measured({'verdict': 'NOT_MEASURED', 'executed': False}), 'NOT_MEASURED')

    def correctness_runtime(self, *, failing=False):
        import types
        issued = live.real_entry('IC', 'ultra', self.project)
        value = policy.configure(types.SimpleNamespace())
        declaration = self.project / analog.DECLARATIONS[1]
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(json.dumps({'blocks': [{'name': 'unit', 'type': 'amplifier'}]}))
        block = self.project / 'phase3/analog/unit'
        block.mkdir(parents=True)
        (block / 'unit.gds').write_bytes(b'correctness fixture; no GDS/PDK credit')
        (block / 'unit.sp').write_text('.subckt unit a b\nR1 a b 1k\n.ends unit\n')
        data = self.project / 'input/correctness.json'
        data.write_text(json.dumps(dict(widths=[0 if failing else 2], minimum=1,
            expected_nodes=['a', 'b'], observed_nodes=['a', 'b'], missing_lvs=failing)))
        seed = em.Registry()
        analog.register_adapters(seed, project=self.project)
        arm = seed.adapters('A6')[0]
        fixture = PROGRAMS / 'tests/fixtures/analog_correctness_process.py'
        sources = dict(arm.source_files, **{str(fixture): em.digest(fixture)})
        sources.update({str(p): em.digest(p) for p in em._source_closure(sources)})
        objective = dict(arm.objective, source_manifest_sha256=em._hash(sources))
        arm = replace(arm, tool_id='analog-correctness-fixture', qualified=False, available=True,
            qualification_evidence='NO_NATIVE_PASS; supported finite correctness fixture',
            source_files=sources, objective=objective,
            components=(em.Component('correctness-producer', (str(Path(sys.executable).resolve()),
                str(fixture), '--inputs', '{inputs}', '--outputs', '{outputs}'), 30),
                         *analog.gate_components('A6')),
            input_contract=(*arm.input_contract, 'input/correctness.json'))
        registry = em.Registry()
        registry.register(arm)
        controller = em.Controller(registry, em.Budget(1, 512))
        runtime = dict(identity=(str(self.project), value['request_digest'], issued['route']['source_sha']),
            project=self.project, route=issued['route'], policy=value, registry=registry,
            controller=controller, parameters={}, contexts={}, bindings={}, runs={})
        policy._ordinary_runtime = runtime
        return runtime

    def test_supervised_correctness_process_parent_verify_import_and_reread(self):
        runtime = self.correctness_runtime()
        waiting = policy.dispatch_fixed_step(self.project, 'A6')
        root, original = runtime['runs']['A6']
        receipt_path = root / 'analog-a6/receipt.json'
        raw = json.loads(receipt_path.read_text())
        detail = raw.get('reason', '') + str(raw.get('processes', []))[:1000]
        self.assertEqual(original['status'], 'AWAITING_AI_SELECTION', detail)
        context = runtime['contexts']['A6']
        prepared = analog.prepare(context, project=self.project, registry=runtime['registry'],
                                  controller=runtime['controller'])
        for changed in (replace(context, step_id='A7'), replace(context, request_digest='f' * 64)):
            with self.assertRaises(em.Refusal):
                analog.prepare(changed, project=self.project, registry=runtime['registry'],
                               controller=runtime['controller'])
        with self.assertRaises(em.Refusal):
            analog.prepare(context, project=Path(self.temp.name), registry=runtime['registry'],
                           controller=runtime['controller'])
        choice = dict(arm_id='analog-a6', binding=context.binding(),
            receipt_sha256=em.digest(root / 'analog-a6/receipt.json'),
            reviewer='independent supported correctness fixture reviewer',
            rationale='Real bounded fixture process and actual A6 canonical gate; no native/PDK qualification.')
        result = policy.dispatch_fixed_step(self.project, 'A6', choice=choice)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['execution_result']['status'], 'ADOPTED')
        self.assertTrue((self.project / 'phase3/analog/unit/drc.report').is_file())
        self.assertTrue((root / 'canonical-consumer/analog_a6_block_pv_check.receipt.json').is_file())
        receipt = json.loads((root / 'analog-a6/receipt.json').read_text())
        self.assertTrue(receipt['processes'][0]['pid'] > 0)
        selected = Path(result['selected_generation']['directory'])
        observations = [json.loads(line) for line in (selected / 'native-processes.jsonl').read_text().splitlines()]
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(row['pid'] > 0 and row['rc'] == 0 for row in observations))
        self.assertFalse(runtime['registry'].adapters('A6')[0].qualified)
        with self.assertRaises(em.Refusal):
            analog.consume(self.project, prepared, runtime['controller'], root, {'status': 'ADOPTED'})
        original_input = (self.project / 'input/correctness.json').read_bytes()
        (self.project / 'input/correctness.json').write_text('changed current input')
        with self.assertRaises(em.Refusal):
            runtime['controller'].verify_adoption(context, root)
        (self.project / 'input/correctness.json').write_bytes(original_input)
        # Edited selected bytes refuse, even after a prior successful read.
        target = selected / 'project/phase3/analog/unit/drc.report'
        target.chmod(0o644); target.write_text('edited after adoption')
        with self.assertRaises(em.Refusal):
            runtime['controller'].verify_adoption(context, root)

    def test_supervised_correctness_fail_dominates_later_missing_lvs(self):
        runtime = self.correctness_runtime(failing=True)
        result = policy.dispatch_fixed_step(self.project, 'A6')
        self.assertEqual(result['status'], 'FAIL', str(result.get('reason')))
        self.assertFalse((self.project / 'phase3/analog/unit/drc.report').exists())
        self.assertFalse((runtime['runs']['A6'][0] / 'selected').exists())

    def test_installation_absence_tool_model_and_deck_remain_not_measured(self):
        source = 'a' * 40
        self.assertIn('ANALOG_INSTALLATION_REQUEST_ABSENT',
                      analog.installation_facts(self.project, 'A6', source, {})[2])
        config = self.project / 'input/analog_native.json'
        config.parent.mkdir()
        config.write_text(json.dumps(dict(image_id='sha256:' + 'a' * 64,
            pdk_name='synthetic', pdk_root='input/pdk', pv_resolution={})))
        params, receipt, missing = analog.installation_facts(self.project, 'A6', source, {})
        self.assertIsNone(receipt)
        self.assertIn('PDK_MODELS_UNAVAILABLE', missing)
        self.assertIn('DRC_DECK_UNAVAILABLE', missing)
        self.assertIn('LVS_DECK_UNAVAILABLE', missing)
        with patch('shutil.which', return_value=None):
            self.assertIn('DOCKER_UNAVAILABLE', analog.installation_facts(self.project, 'A6', source, {})[2])

    def test_normal_top_cli_bootstrap_live_fd_a6_a7_a8_not_measured(self):
        import types
        import subprocess
        import vibe_ic_one_shot_runner as top
        issued = live.real_entry('IC', 'ultra', self.project)
        policy.configure(types.SimpleNamespace())
        declaration = self.project / analog.DECLARATIONS[1]
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(json.dumps({'blocks': [{'name': 'unit', 'type': 'amplifier'}]}))
        layout = self.project / 'phase3/analog/unit/layout.mag'
        layout.parent.mkdir(parents=True)
        layout.write_text('correctness fixture input only; no PDK/layout qualification\n')
        # No subprocess spy: top launches the real analog parser/runner, which
        # independently consumes its inherited live FD and bootstraps policy.
        rc = top._run_phase('ANALOG', PROGRAMS / 'analog_one_shot_runner.py', [str(self.project)])
        self.assertNotEqual(rc, 0)  # Missing installation is never full-flow PASS.
        root = self.project / 'reports/execution' / issued['request']['invocation_id']
        for step in analog.STEP_IDS:
            plan = json.loads((root / step / 'plan.json').read_text())
            self.assertEqual(plan['binding']['step_id'], step)
            self.assertEqual(plan['binding']['request_digest'], issued['request']['request_digest'])
            self.assertEqual(plan['arms'], [])
            self.assertFalse((root / step / 'selected').exists())
        report = json.loads(runner._pl.report_path(self.project, 'analog_one_shot.json').read_text())
        rows = report.get('steps', report.get('plan', []))
        self.assertTrue(rows or report.get('blocks'))

    def test_validator_keeps_measured_fail_when_outputs_are_missing(self):
        outputs = Path(self.temp.name) / 'outputs'
        outputs.mkdir()
        binding = {'step_id': 'A6', 'source_sha': 'a' * 40, 'required_gates': ['analog_a6_block_pv_check']}
        for verdict in ('FAIL', 'PASS'):
            (outputs / 'producer.json').write_text(json.dumps({'binding': binding, 'step_id': 'A6',
                'source_sha': binding['source_sha'], 'design_verdict': verdict, 'native': False}))
            (outputs / 'products.json').write_text(json.dumps({'binding': binding, 'products': {}}))
            evidence = worker.validate_outputs(outputs, binding)
            self.assertEqual(evidence.verdict, 'FAIL' if verdict == 'FAIL' else 'NOT_MEASURED')
            self.assertEqual(evidence.gates, {'analog_a6_block_pv_check': 'NOT_MEASURED'})


if __name__ == '__main__':
    unittest.main(verbosity=2)

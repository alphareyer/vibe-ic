"""Current A7 product binding through the ordinary A8 caller; no native credit."""
from pathlib import Path
import json
import sys
import types
import unittest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS.parent))
import execution_modes as em
import execution_policy as policy
import execution_adapters_analog as analog
import analog_one_shot_runner as runner
from programs.tests.test_analog_live_reachability_review import AnalogLiveReachabilityReview
from programs.tests import test_execution_receipt_chain as live


class A8CurrentInputControl(unittest.TestCase):
    def test_existing_caller_binds_late_a7_product_and_refuses_changed_reuse(self):
        self.check_current_inputs()

    def test_existing_caller_binds_pg_snapshot_and_refuses_mutation_removal_or_provenance_edit(self):
        self.check_current_inputs(pg=True)

    def check_current_inputs(self, pg=False):
        state = AnalogLiveReachabilityReview()
        state.setUp()
        try:
            project = state.project
            issued = live.real_entry('IC', 'ultra', project)
            value = policy.configure(types.SimpleNamespace())
            declaration = project / analog.DECLARATIONS[1]
            declaration.parent.mkdir(parents=True)
            declaration.write_text(json.dumps({'blocks': [{'name': 'unit', 'type': 'amplifier'}]}))
            registry = em.Registry()
            analog.register_adapters(registry, project=project)
            controller = em.Controller(registry, em.Budget(1, 512))
            runtime = dict(identity=(str(project), value['request_digest'], issued['route']['source_sha']),
                project=project, route=issued['route'], policy=value, registry=registry,
                controller=controller, parameters={}, contexts={}, bindings={}, runs={})
            policy._ordinary_runtime = runtime
            # A7 writes this after registration. Presence is sufficient for
            # binding; no measured A7/full-IC PASS is introduced as a condition.
            name = 'phase3/analog/unit/pre_vs_post.json'
            product = project / name
            product.parent.mkdir(parents=True)
            product.write_text(json.dumps({'verdict': 'NOT_MEASURED', 'fixture': 'current A7 data'}))
            snapshot = product.parent / 'a5_stdcell_pg.lef'
            provenance = product.parent / 'layout_provenance.json'
            if pg:
                snapshot.write_text('MACRO current_pg\n PIN VPWR\n USE POWER ;\n END VPWR\nEND current_pg\n')
                provenance.write_text(json.dumps({'pins_basis': {'core_pg':
                    {'path': str(snapshot.relative_to(project)), 'sha256': em.digest(snapshot)}}}))
            result = runner.step_for_block(project, {'name': 'unit'}, 'A8_hardmacro_gen')
            self.assertEqual(result.status, 'NOT_MEASURED')
            context = runtime['contexts']['A8']
            self.assertIn(name, context.binding()['inputs'])
            self.assertEqual(context.binding()['inputs'][name], em.digest(product))
            if pg:
                self.assertEqual(context.binding()['inputs'][str(snapshot.relative_to(project))], em.digest(snapshot))
            root, original = runtime['runs']['A8']
            self.assertFalse((root / 'selected').exists())
            before = product.read_bytes()
            for status in ('FAIL', 'NOT_MEASURED'):
                product.write_text(json.dumps({'verdict': status, 'fixture': 'edited current A7 bytes'}))
                with self.assertRaisesRegex(em.Refusal, 'FIXED_STEP_REENTRY_CHANGED'):
                    runner.step_for_block(project, {'name': 'unit'}, 'A8_hardmacro_gen')
            product.write_bytes(before)
            self.assertEqual(runner.step_for_block(project, {'name': 'unit'}, 'A8_hardmacro_gen').status, 'NOT_MEASURED')
            self.assertIs(runtime['contexts']['A8'], context)
            self.assertIs(runtime['runs']['A8'][1], original)
            self.assertFalse((project / 'phase3/analog/hardmacro').exists())
            if pg:
                saved = snapshot.read_bytes()
                snapshot.write_bytes(saved + b'# edited current PG bytes\n')
                with self.assertRaisesRegex(em.Refusal, 'FIXED_STEP_REENTRY_CHANGED'):
                    runner.step_for_block(project, {'name': 'unit'}, 'A8_hardmacro_gen')
                snapshot.unlink()
                with self.assertRaisesRegex(em.Refusal, 'FIXED_STEP_REENTRY_CHANGED'):
                    runner.step_for_block(project, {'name': 'unit'}, 'A8_hardmacro_gen')
                snapshot.write_bytes(saved)
                provenance.write_text(json.dumps({'pins_basis': {'core_pg':
                    {'path': str(snapshot.relative_to(project)), 'sha256': '0' * 64}}}))
                with self.assertRaisesRegex(em.Refusal, 'FIXED_STEP_REENTRY_CHANGED'):
                    runner.step_for_block(project, {'name': 'unit'}, 'A8_hardmacro_gen')
        finally:
            state.tearDown()


if __name__ == '__main__': unittest.main(verbosity=2)

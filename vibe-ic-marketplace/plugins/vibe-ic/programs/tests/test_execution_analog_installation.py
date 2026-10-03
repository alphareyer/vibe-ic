"""Bounded source correctness controls; fixture executables are NOT native EDA."""
from dataclasses import replace
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_modes as em
import execution_policy as policy
import execution_adapters_analog as analog
import execution_analog_installation as installation
import execution_analog_worker as worker
import analog_one_shot_runner as runner
import analog_pdk_availability as resolver
import _eda_pin as pin


class AnalogInstallationControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='analog-installation-source-control-')
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.ref = 'correctness.invalid/fixture@sha256:' + 'a' * 64
        # Docker's Id deliberately differs from the manifest digest.
        self.image = dict(image_ref=self.ref, image_id='sha256:' + 'b' * 64,
            image_manifest_digest='sha256:' + 'a' * 64, image_repo_digests=[self.ref])
        self.args = types.SimpleNamespace(pdk='fixture', container='host')
        self.observed = []
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        for name in installation.TOOL_COMMANDS:
            executable = self.bin / name
            executable.write_text('#!' + sys.executable + '\nprint("supported correctness fixture; NOT_NATIVE_EDA")\n')
            executable.chmod(0o755)
        self.file('input/pdk/spice/unit.lib', '.model unit nmos level=1\n')
        self.file('input/pdk/klayout/unit.drc', '# self-contained source correctness deck\n')
        self.file('input/pdk/klayout/unit.lvs', '# self-contained source correctness deck\n')
        resolver._RESOLVE_CACHE.clear()

    def tearDown(self):
        resolver._RESOLVE_CACHE.clear()
        self.temp.cleanup()

    def file(self, name, value):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def request(self):
        with patch.object(pin, 'pinned_image_present', return_value=(self.ref, '')), \
             patch.object(installation, 'inspect_image', return_value=self.image):
            return installation.prepare_entry_request(self.project, self.args)

    def supervised_fixture(self, argv, **kwargs):
        # Execute the actual source-owned probe and tool processes. Only Docker
        # transport is substituted for this explicitly non-native fixture.
        command = [sys.executable, '-c', installation.PROBE, argv[-1]]
        env = {**kwargs['env'], 'PATH': str(self.bin)}
        self.assertFalse(any(k.startswith('VIBEIC_EXECUTION') for k in env))
        self.assertIn(self.ref, argv)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=env, start_new_session=True)
        try:
            out, err = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate(); raise
        self.observed.append(dict(pid=process.pid, rc=process.returncode, argv=command))
        return subprocess.CompletedProcess(command, process.returncode, out, err)

    def facts(self, step, request):
        from execution_synthesis_engines import source_sha
        with patch.object(installation, 'inspect_image', return_value=self.image), \
             patch.object(installation.lc, 'run_container', side_effect=self.supervised_fixture), \
             patch('shutil.which', return_value='/usr/bin/docker'):
            return analog.installation_facts(self.project, step, source_sha(), {}, prepared=request)

    def test_full_ref_manifest_and_different_local_id_are_bound(self):
        raw = self.image['image_id'] + '\t' + json.dumps([self.ref]) + '\n'
        with patch.object(installation.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, raw, '')) as run:
            actual = installation.inspect_image(self.ref)
        self.assertEqual(actual, self.image)
        self.assertNotEqual(actual['image_id'], actual['image_manifest_digest'])
        self.assertEqual(run.call_args.args[0][-1], self.ref)
        with patch.object(installation.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, self.image['image_id'] + '\t[]', '')):
            with self.assertRaises(em.Refusal): installation.inspect_image(self.ref)
        for invalid in ('sha256:' + 'a' * 64, 'fixture@sha256:bad'):
            with self.assertRaises(em.Refusal): installation.inspect_image(invalid)

    def test_program_owned_self_contained_request_measures_and_enters_all_producers(self):
        request = self.request()  # Actual project-staged production resolver.
        self.assertEqual(request['_missing'], [])
        self.assertEqual(request['_step_missing']['A6'], [])
        self.assertEqual(request['image_ref'], self.ref)
        self.assertEqual(request['image_id'], self.image['image_id'])
        self.assertFalse((self.project / 'input/analog_native.json').exists())
        for step in analog.STEP_IDS:
            params, receipt, missing = self.facts(step, request)
            self.assertEqual(missing, [], (step, missing))
            self.assertEqual(receipt['status'], 'MEASURED')
            self.assertEqual(params['entry_origin'], 'analog-normal-entry')
            # Actual existing producer entrypoints execute. Deliberately absent
            # design/layout inputs prevent any native or PDK design PASS.
            records = Path(self.temp.name) / step
            calls = worker.native_produce(self.project, self.project,
                dict(step_id=step, parameters=params, blocks=['unit']), records)
            self.assertTrue(calls, step)
            expected = {'A6': {'analog_a6_native_pv.run_block_pv'},
                'A7': {'analog_a7_post_layout_emit.run'},
                'A8': {'analog_a8_hardmacro_emit.main', 'analog_hardmacro_gds_emit.main'}}[step]
            self.assertEqual({row['entrypoint'] for row in calls}, expected)
            self.assertTrue(all(row['pid'] > 0 and row['source_sha256'] for row in calls))
            self.assertNotEqual(worker.producer_result_verdict(worker.producer_result_observations(calls)), 'PASS')
        self.assertEqual(len(self.observed), 6)  # measure + fresh verify, each row
        self.assertTrue(all(row['pid'] > 0 and row['rc'] == 0 for row in self.observed))

    def test_missing_model_include_is_not_measured_and_never_probes(self):
        self.file('input/pdk/spice/unit.lib', '.model unit nmos level=1\n.include "absent.mod"\n')
        request = self.request()
        self.assertIn('DECLARED_MODEL_OR_DECK_UNAVAILABLE:input/pdk/spice/absent.mod', request['_missing'])
        for step in analog.STEP_IDS:
            self.assertIsNone(self.facts(step, request)[1])
        self.assertEqual(self.observed, [])

    def test_a6_missing_deck_include_does_not_disable_a7_a8(self):
        self.file('input/pdk/klayout/unit.drc', 'require_relative "absent.rb"\n')
        request = self.request()
        self.assertEqual(request['_missing'], [])
        self.assertIn('DECLARED_MODEL_OR_DECK_UNAVAILABLE:input/pdk/klayout/absent.rb', request['_step_missing']['A6'])
        self.assertIsNone(self.facts('A6', request)[1])
        for step in ('A7', 'A8'):
            params, receipt, missing = self.facts(step, request)
            self.assertEqual(missing, [])
            self.assertEqual(receipt['status'], 'MEASURED')
            self.assertEqual(params['config_hashes'], {})
        self.assertEqual(len(self.observed), 4)

    def test_dynamic_deck_import_refuses_a6_only(self):
        self.file('input/pdk/klayout/unit.drc', 'load(File.join(root, "extra.rb"))\n')
        request = self.request()
        self.assertIn('DECLARED_IMPORT_UNRESOLVED:input/pdk/klayout/unit.drc', request['_step_missing']['A6'])
        self.assertIsNone(self.facts('A6', request)[1])
        for step in ('A7', 'A8'):
            self.assertEqual(self.facts(step, request)[1]['status'], 'MEASURED')

    def test_spice_source_nodes_are_not_import_commands(self):
        self.file('input/pdk/spice/unit.lib', '.subckt unit drain gate source bulk\n'
            'M1 drain gate source bulk unit_model\n.model unit_model nmos level=1\n.ends unit\n')
        request = self.request()
        self.assertEqual(request['_missing'], [])
        for step in analog.STEP_IDS:
            self.assertEqual(self.facts(step, request)[1]['status'], 'MEASURED')

    def test_ruby_inline_comment_and_quoted_hash_do_not_invent_imports(self):
        self.file('input/pdk/klayout/unit.drc', 'width = 1.0 # source layer\n'
            'label = "source load require"\nrequire_relative "extra#source.rb" # include note\n')
        child = self.file('input/pdk/klayout/extra#source.rb', '# regular declared config\n')
        request = self.request()
        self.assertEqual(request['_step_missing']['A6'], [])
        self.assertEqual(request['_input_hashes']['input/pdk/klayout/extra#source.rb'], em.digest(child))
        self.assertEqual(self.facts('A6', request)[1]['status'], 'MEASURED')
        child.unlink()
        missing = self.request()
        self.assertIn('DECLARED_MODEL_OR_DECK_UNAVAILABLE:input/pdk/klayout/extra#source.rb', missing['_step_missing']['A6'])

    def test_embedded_dynamic_import_and_escaping_include_still_refuse(self):
        for code in ('rules = load(File.join(root, "extra.rb"))',
                     'label = "#{require(dynamic_path)}"',
                     'require_relative "../../../../outside.rb"'):
            with self.subTest(code=code):
                self.file('input/pdk/klayout/unit.drc', code + '\n')
                request = self.request()
                self.assertTrue(request['_step_missing']['A6'])
                self.assertIsNone(self.facts('A6', request)[1])
                self.assertEqual(request['_step_missing']['A7'], [])
                self.assertEqual(request['_step_missing']['A8'], [])

    def test_resolved_include_edit_refuses_current_request_and_rebinds(self):
        self.file('input/pdk/klayout/unit.drc', 'require_relative "extra.rb"\n')
        dependency = self.file('input/pdk/klayout/extra.rb', '# bound source dependency\n')
        request = self.request()
        old = em.digest(dependency)
        self.assertEqual(request['_step_config_hashes']['A6']['input/pdk/klayout/extra.rb'], old)
        self.assertEqual(self.facts('A6', request)[1]['status'], 'MEASURED')
        dependency.write_text('# edited current source dependency\n')
        params, receipt, missing = self.facts('A6', request)
        self.assertIsNone(receipt)
        self.assertIn('CONFIG_UNAVAILABLE:input/pdk/klayout/extra.rb', missing)
        fresh = self.request()
        self.assertNotEqual(em._hash(fresh), em._hash(request))
        self.assertEqual(fresh['_input_hashes']['input/pdk/klayout/extra.rb'], em.digest(dependency))

    def test_safe_parent_include_binds_current_child_and_refuses_stale_bytes(self):
        self.file('input/pdk/klayout/unit.drc', 'require_relative "sub/../shared/rules.rb"\n')
        (self.project / 'input/pdk/klayout/sub').mkdir()
        name = 'input/pdk/klayout/shared/rules.rb'
        child = self.file(name, '# current bounded rules\n')
        request = self.request()
        self.assertEqual(request['_step_missing']['A6'], [])
        self.assertEqual(request['_step_config_hashes']['A6'][name], em.digest(child))
        self.assertEqual(self.facts('A6', request)[1]['status'], 'MEASURED')
        child.write_text('# changed bounded rules\n')
        self.assertIn('CONFIG_UNAVAILABLE:' + name, self.facts('A6', request)[2])
        self.assertEqual(self.request()['_step_config_hashes']['A6'][name], em.digest(child))
        child.unlink()
        self.assertIn('DECLARED_MODEL_OR_DECK_UNAVAILABLE:' + name,
                      self.request()['_step_missing']['A6'])

    def test_parent_include_escape_refuses_before_io_and_symlink_still_refuses(self):
        self.file('input/pdk/klayout/unit.drc', 'require_relative "../../../outside.rb"\n')
        outside = self.file('outside.rb', '# outside the one PDK root\n')
        original = Path.is_file
        def checked(path):
            self.assertNotEqual(path, outside, 'escaping include reached filesystem IO')
            return original(path)
        with patch.object(Path, 'is_file', checked):
            request = self.request()
        self.assertTrue(any(reason.startswith('DECLARED_INCLUDE_PATH_UNSAFE:')
                            for reason in request['_step_missing']['A6']))
        self.assertNotIn('outside.rb', request['_input_hashes'])
        self.file('input/pdk/klayout/unit.drc', 'require_relative "sub/../shared/rules.rb"\n')
        self.file('input/pdk/klayout/shared/rules.rb', '# bounded final path\n')
        (self.project / 'input/pdk/klayout/sub').symlink_to(self.project, target_is_directory=True)
        request = self.request()
        self.assertTrue(request['_step_missing']['A6'])
        self.assertIsNone(self.facts('A6', request)[1])

    def test_tcl_url_does_not_hide_later_dynamic_source(self):
        root = self.file('input/pdk/magic/unit.tcl',
                         'set root http://example.invalid; source $root/rules.tcl\n')
        facts = resolver.resolve_pdk('fixture', project=str(self.project), container='host')
        with patch.object(resolver, 'resolve_pdk', return_value={**facts, 'magicrc': str(root)}):
            request = self.request()
        name = 'input/pdk/magic/unit.tcl'
        self.assertEqual(request['config_hashes'][name], em.digest(root))
        self.assertIn('DECLARED_IMPORT_UNRESOLVED:' + name, request['_missing'])
        for step in analog.STEP_IDS:
            self.assertIsNone(self.facts(step, request)[1])

    def test_tool_failure_dominates_later_missing_tool_and_imports_nothing(self):
        (self.bin / 'magic').write_text('#!' + sys.executable + '\nprint("measured fixture failure")\nraise SystemExit(1)\n')
        (self.bin / 'ngspice').unlink()
        params, receipt, missing = self.facts('A7', self.request())
        self.assertEqual(receipt['status'], 'FAIL')
        self.assertEqual(params['installation_verdict'], 'FAIL')
        self.assertEqual(missing, ['MEASURED_INSTALLATION_FAIL'])
        self.assertFalse((self.project / 'phase3').exists())

    def test_missing_tool_is_not_measured_and_current_input_change_refuses(self):
        (self.bin / 'ngspice').unlink()
        params, receipt, missing = self.facts('A7', self.request())
        self.assertIsNone(receipt)
        self.assertTrue(any('TOOL_UNAVAILABLE:ngspice' in item for item in missing))
        request = self.request()
        self.file('input/pdk/spice/unit.lib', '.model changed nmos level=1\n')
        params, receipt, missing = self.facts('A7', request)
        self.assertIsNone(receipt)
        self.assertTrue(any('MODEL_UNAVAILABLE' in item for item in missing))
        self.assertFalse((self.project / 'phase3').exists())

    def test_contradiction_refuses_before_request_bootstrap_or_probe(self):
        self.file('phase1/generated_docs/L19_CONSTRAINTS_PDK.json', json.dumps({'fields': {'pdk_target': 'other'}}))
        with patch.object(sys, 'argv', ['analog_one_shot_runner.py', str(self.project), '--pdk', 'fixture']), \
             patch.object(installation, 'prepare_entry_request') as prepare, \
             patch.object(policy, 'bootstrap') as bootstrap:
            self.assertEqual(runner.main(), 2)
        prepare.assert_not_called(); bootstrap.assert_not_called()

    def test_l5_materializes_before_bootstrap_and_late_inputs_preserve_population(self):
        # No canonical block list exists initially: the ordinary L5 fallback
        # must write it before registration, then later inputs join the census.
        self.file('phase1/generated_docs/L5_ADI_SPEC.json', json.dumps({'analog_blocks': [{'name': 'unit', 'type': 'amplifier'}]}))
        class StopBeforeLoop(Exception): pass
        def bootstrap(project, *, parameters):
            declaration = analog.declaration_path(project)
            self.assertTrue(declaration.is_file())
            self.assertEqual(parameters['analog_request']['_origin'], 'analog-normal-entry')
            registry = em.Registry()
            analog.register_adapters(registry, project=project, request=parameters['analog_request'])
            objectives = {step: dict(registry.adapters(step)[0].objective) for step in analog.STEP_IDS}
            self.assertTrue(all(item['blocks'] == ['unit'] for item in objectives.values()))
            runtime = dict(project=project, registry=registry)
            before = policy._fixed_inputs(runtime, 'A6')
            for name in ('spec.json', 'topology.json', 'layout.mag', 'unit.sp', 'unit.gds', 'corner_results.json'):
                value = self.file('phase3/analog/unit/' + name, 'later A1-A5 source input\n')
                self.assertNotIn(str(value.relative_to(project)), before)
            for step in analog.STEP_IDS:
                after = policy._fixed_inputs(runtime, step)
                self.assertIn('phase3/analog/unit/layout.mag', after)
                self.assertEqual(registry.adapters(step)[0].objective, objectives[step])
                self.assertEqual(objectives[step]['declaration_sha256'], em.digest(declaration))
            raise StopBeforeLoop()
        with patch.object(sys, 'argv', ['analog_one_shot_runner.py', str(self.project)]), \
             patch.object(policy, 'request', return_value={'mode': 'ultra'}), \
             patch.object(pin, 'pinned_image_present', return_value=(None, 'correctness control: no image')), \
             patch.object(policy, 'bootstrap', side_effect=bootstrap):
            with self.assertRaises(StopBeforeLoop): runner.main()


if __name__ == '__main__': unittest.main(verbosity=2)

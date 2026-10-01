"""Finite F5 software controls. No EDA/native job or physical PASS is claimed."""
from __future__ import annotations

import json
import argparse
from dataclasses import replace
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_modes as em
import execution_adapters_analog as analog
from execution_step_protocol import FactoryRegistry, StepRequest
from execution_analog_mixed_worker import derive_power_reports, grade_si
from execution_analog_worker import semantic_gates, validate_outputs


def _current_source_sha():
    """Resolve typed INPUT against this regular producer/test checkout."""
    import re
    import subprocess
    test_source = Path(__file__).absolute()
    producer_source = Path(analog.__file__).absolute()
    if (not test_source.is_file() or not producer_source.is_file()
            or any(p.is_symlink() for source in (test_source, producer_source)
                   for p in (source, *source.parents))
            or producer_source.parent != test_source.parents[1]):
        raise RuntimeError('F5 source INPUT requires a regular common producer/test checkout')
    current = subprocess.run(
        ['git', '-C', str(producer_source.parent), 'rev-parse', '--verify', 'HEAD^{commit}'],
        capture_output=True, text=True, timeout=10)
    checkout_sha = current.stdout.strip()
    if current.returncode or not re.fullmatch(r'[0-9a-f]{40}', checkout_sha):
        raise RuntimeError('F5 source INPUT has no valid current checkout Git identity')
    supplied = os.environ.get('F5_SOURCE_SHA')
    if supplied is not None:
        if not re.fullmatch(r'[0-9a-f]{40}', supplied) or supplied != checkout_sha:
            raise RuntimeError('F5_SOURCE_SHA must be the nonempty current checkout Git identity')
        return supplied
    return checkout_sha


BASE = _current_source_sha()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + '\n')


def sources():
    paths = list(analog.source_dependencies())
    paths.append(Path(sys.executable).resolve())
    return {str(p.resolve()): em.digest(p.resolve()) for p in paths if p.is_file()}


class AnalogProductionControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='f5-controls-')
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.declaration = self.project / 'phase1/analog/analog_block_list.json'
        write(self.declaration, {'blocks': [{'name': 'ref', 'type': 'bandgap'}]})
        self.l5 = self.project / 'phase1/generated_docs/L5_ADI_SPEC.json'
        write(self.l5, {'analog_blocks': [{'name': 'ref', 'type': 'bandgap',
             'spec': {'specs': [{'name': 'Vref', 'target': 1.2, 'unit': 'V'}]}}]})
        write(self.project / 'phase1/generated_docs/L1_DATASHEET.json', {'electrical_specs': []})
        self.lease = self.root / 'lease'
        # Deliberately a SOURCE-FIXTURE control, never an F1 native admission.
        write(self.lease / 'lease.json', {'scope': 'source-fixture-only', 'cpus': 1, 'ram_mb': 256})

    def tearDown(self):
        self.temp.cleanup()

    def request(self, **updates):
        parameters = {'declaration': self.declaration, 'cpus': 1, 'ram_mb': 256,
            'timeout_s': 60, 'objective': {'task': 'extract block-attributed structured spec preserving V units'},
            'input_roots': ['phase1/generated_docs', 'phase1/analog/analog_block_list.json', 'input']}
        parameters.update(updates)
        return StepRequest('A1', self.project, parameters, self.lease,
            self.root / ('request-' + str(len(list(self.root.glob('request-*')))) + '.json'),
            BASE, sources())

    def prepared(self, **updates):
        registry = FactoryRegistry()
        registry.register(analog)
        return registry.prepare(self.request(**updates))

    def execute(self, prepared=None):
        if prepared is None:
            prepared = self.prepared()
        controller = em.Controller(prepared.registry, em.Budget(1, 256, workers=1))
        run = self.root / 'run'
        controller.run(prepared.context, run)
        receipt = run / 'analog-a1/receipt.json'
        data = json.loads(receipt.read_text())
        self.assertEqual(data['status'], 'ELIGIBLE', {k: data.get(k) for k in ('reason', 'detail', 'evidence')})
        choice = {'arm_id': 'analog-a1', 'receipt_sha256': em.digest(receipt),
            'binding': prepared.context.binding(), 'reviewer': 'finite-source-control',
            'rationale': 'Control selects the actual document-bound software artifact, not native acceptance.'}
        adopted = controller.adopt(prepared.context, run, choice)
        evidence = os.environ.get('F5_EVIDENCE_ROOT')
        if evidence:
            target = Path(evidence) / self._testMethodName
            shutil.copytree(self.project, target / 'original-input')
            shutil.copytree(run, target / 'controller-run')
            shutil.copyfile(prepared.context.request_record, target / 'request.json')
            write(target / 'proof-scope.json', {'source_sha': BASE, 'scope': 'SOURCE_FIXTURE',
                'actual_native_jobs': 0, 'independent_AI_integration_qualified': False,
                'selection': 'explicit control choice of actual eligible A1 software output'})
        return prepared, controller, run, adopted

    def private_worker_closure(self):
        """Copy the actual producer closure into a regular, current Git scratch."""
        import subprocess
        checkout = Path(subprocess.run(
            ['git', '-C', str(analog.PROGRAMS), 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip())
        private = self.root / 'producer-source'
        subprocess.run(['git', 'clone', '--shared', '--no-checkout', '--quiet',
                        str(checkout), str(private)],
                       capture_output=True, text=True, timeout=30, check=True)
        current = subprocess.run(['git', '-C', str(private), 'rev-parse',
                                  '--verify', 'HEAD^{commit}'],
                                 capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(current.stdout.strip(), BASE)
        subprocess.run(['git', '-C', str(private), 'checkout', '--detach',
                        '--quiet', current.stdout.strip()],
                       capture_output=True, text=True, timeout=30, check=True)
        copied = {}
        for source in analog.source_dependencies():
            if not source.is_file() or source.is_symlink():
                raise em.Refusal('ADAPTER_SOURCE_MISMATCH', str(source))
            target = private / source.relative_to(checkout)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            self.assertEqual(em.digest(target), em.digest(source))
            copied[str(target)] = em.digest(target)
        return private / analog.WORKER.relative_to(checkout), copied

    def test_real_a1_primary_reaches_existing_semantic_consumer(self):
        prepared, controller, run, adopted = self.execute()
        result = prepared.consume(self.project, prepared.context, controller, run, adopted)
        self.assertEqual(result['status'], 'IMPORTED')
        self.assertEqual(result['design_verdict'], 'PASS')
        self.assertEqual(result['primary_gates']['analog_a1_spec_extract_check'], 'PASS')
        path = self.project / 'phase3/analog/ref/spec.json'
        data = json.loads(path.read_text())
        self.assertEqual(data['_provenance']['fields_defaulted'], [])
        self.assertEqual(data['_provenance']['input']['sha256'], em.digest(self.l5))
        self.assertEqual(data['specs'][0]['unit'], 'V')
        self.assertIn(str(path.relative_to(self.project)), result['copied'])
        self.assertTrue(all('*' not in p for p in prepared.adoption_paths))

    def test_f199_frontdoor_issued_import(self):
        """Actual shared typed frontdoor and journal around the real A1 path."""
        import execution_production as production
        from execution_resource_lease import host_lease
        budget = em.Budget(1, 256, workers=1)
        evidence = os.environ.get('F5_EVIDENCE_ROOT')
        target = Path(evidence) / self._testMethodName if evidence else None
        if target:
            shutil.copytree(self.project, target / 'original-input')
        with host_lease(budget, fixture_root=self.root) as lease:
            parameters, extras = production.frontdoor_parameters(dict(
                step_id='A1', project=self.project, declaration=self.declaration,
                objective={'task': 'extract attributed L5 spec preserving V units'},
                timeout_s=60, route=argparse.Namespace(scope='SOURCE_FIXTURE'),
                input_roots={'generated_docs': self.project / 'phase1/generated_docs',
                             'block_list': self.declaration,
                             'optional_input': self.project / 'input'}),
                {'cpus': 1, 'ram_mb': 256})
            # F1 supports explicit source additions before the request. Bind
            # every actual domain dependency; never extend an arm afterwards.
            extras.update(sources())
            sha, common = production.source_identity(extras)
            self.assertEqual(sha, BASE)
            request = StepRequest('A1', self.project, parameters, lease,
                self.root / 'f199-request.json', sha, common)
            registry = FactoryRegistry(); registry.register(analog)
            prepared = registry.prepare(request)
            original = prepared.context
            context = production._SourceContext(original.step_id, original.source_sha,
                original.inputs, original.objective, original.required_gates, original.native_mode,
                original=original, sources=common, extra_sources=extras)
            prepared = replace(prepared, context=context)
            controller = em.Controller(prepared.registry, budget)
            run = self.root / 'f199-run'
            controller.run(context, run)
            receipt = run / 'analog-a1/receipt.json'
            self.assertEqual(json.loads(receipt.read_text())['status'], 'ELIGIBLE')
            choice = {'arm_id': 'analog-a1', 'receipt_sha256': em.digest(receipt),
                'binding': context.binding(), 'reviewer': 'finite-f199-source-control',
                'rationale': 'Explicit source fixture selection; independent AI integration remains pending.'}
            adopted = controller.adopt(context, run, choice)
            imported = production._consume(prepared, request, controller, run, adopted)
            self.assertEqual(imported['status'], 'IMPORTED')
            self.assertEqual(imported['selected_generation'], adopted['selected_generation'])
            self.assertEqual(imported['binding'], context.binding())
            self.assertEqual(imported['source_sha'], BASE)
            self.assertEqual(set(imported['primary_gates'].values()), {'PASS'})
            self.assertTrue(imported['copied'])
            for rel, digest in imported['copied'].items():
                self.assertEqual(em.digest(self.project / rel), digest)
            spec = json.loads((self.project / 'phase3/analog/ref/spec.json').read_text())
            self.assertEqual(spec['specs'][0]['unit'], 'V')
            self.assertEqual(spec['_provenance']['fields_defaulted'], [])
            self.assertEqual(spec['_provenance']['input']['sha256'], em.digest(self.l5))
            if target:
                shutil.copytree(run, target / 'controller-run')
                shutil.copytree(self.project, target / 'canonical-import')
                shutil.copyfile(request.record, target / 'request.json')
                shutil.copyfile(lease / 'lease.json', target / 'issued-source-lease.json')
                write(target / 'proof-scope.json', {'source_sha': BASE,
                    'shared_sha': 'f1997a38ee6922a301bb7e7d1160df23505c3cd3',
                    'scope': 'SOURCE_FIXTURE', 'native_jobs': 0,
                    'independent_AI_integration_qualified': False,
                    'consumer': 'execution_production._consume -> analog.consume -> real semantic_gates'})

    def test_stale_input_refuses_and_preserves_old_output(self):
        prepared, controller, run, adopted = self.execute()
        target = self.project / 'phase3/analog/ref/spec.json'
        target.parent.mkdir(parents=True)
        target.write_text('old preserved output')
        self.l5.write_text(self.l5.read_text().replace('1.2', '0.7'))
        with self.assertRaises(em.Refusal):
            prepared.consume(self.project, prepared.context, controller, run, adopted)
        self.assertEqual(target.read_text(), 'old preserved output')

    def test_added_deleted_and_alias_retarget_are_freshness_bound(self):
        extra = self.project / 'input/a.txt'
        extra.parent.mkdir(parents=True)
        extra.write_text('same')
        second = self.project / 'input/b.txt'
        second.write_text('same')
        alias = self.project / 'input/alias.txt'
        alias.symlink_to('a.txt')
        for mutate, restore in (
            (lambda: (self.project / 'input/new.txt').write_text('added'),
             lambda: (self.project / 'input/new.txt').unlink()),
            (lambda: extra.unlink(), lambda: extra.write_text('same')),
            (lambda: (alias.unlink(), alias.symlink_to('b.txt')),
             lambda: (alias.unlink(), alias.symlink_to('a.txt'))),
        ):
            prepared = self.prepared()
            prepared.context.binding()
            mutate()
            with self.assertRaises(em.Refusal):
                prepared.context.binding()
            restore()
            prepared.context.binding()

    def test_wrong_generation_and_changed_selected_output_refused(self):
        prepared, controller, run, adopted = self.execute()
        wrong = json.loads(json.dumps(adopted))
        wrong['selected_generation']['generation'] = 'wrong-generation'
        with self.assertRaises(em.Refusal):
            prepared.consume(self.project, prepared.context, controller, run, wrong)
        generation = Path(adopted['selected_generation']['directory'])
        path = generation / 'project/phase3/analog/ref/spec.json'
        path.chmod(0o644); path.write_text('{}')
        with self.assertRaises(em.Refusal):
            prepared.consume(self.project, prepared.context, controller, run, adopted)
        self.assertFalse((self.project / 'phase3/analog/ref/spec.json').exists())

    def test_consumer_gate_failure_rolls_back_every_import(self):
        prepared, controller, run, adopted = self.execute()
        target = self.project / 'phase3/analog/ref/spec.json'
        target.parent.mkdir(parents=True)
        target.write_text('old-output')
        # Fault-injection only at this owned consumer seam. The producer and
        # adoption were actual; a refused downstream gate must roll back.
        import execution_analog_worker as worker
        original = worker.semantic_gates
        def failed(*args, **kwargs):
            # Leave the actual producer/controller's fresh validation intact;
            # inject only after the consumer imported the selected bytes.
            directory = args[2]
            if directory.name == 'gates' and directory.parent.name.startswith('analog-consumer-'):
                # Corrupt only the consumer's private mirror. The real gate
                # emits its own FAIL/rc/raw/report and complete execution rows.
                spec = args[1] / 'phase3/analog/ref/spec.json'
                data = json.loads(spec.read_text())
                data['specs'] = []
                write(spec, data)
            return original(*args, **kwargs)
        worker.semantic_gates = failed
        try:
            import execution_production as production
            request = self.request()
            with self.assertRaises(em.Refusal) as refused:
                production._consume(prepared, request, controller, run, adopted)
            self.assertEqual(refused.exception.code, 'GATE_FAIL')
            failed_import = json.loads((run / 'import.json').read_text())
            self.assertEqual(failed_import['status'], 'ROLLED_BACK')
            self.assertEqual(failed_import['design_verdict'], 'FAIL')
            raw_failure = json.loads((run / 'analog-consumer-refusal.json').read_text())
            self.assertEqual(raw_failure['primary_gates']['analog_a1_spec_extract_check'], 'FAIL')
            self.assertEqual(raw_failure['selected_generation'], adopted['selected_generation'])
        finally:
            worker.semantic_gates = original
        self.assertEqual(target.read_text(), 'old-output')
        if os.environ.get('F5_EVIDENCE_ROOT'):
            evidence = Path(os.environ['F5_EVIDENCE_ROOT']) / self._testMethodName
            shutil.copytree(run, evidence / 'controller-run-after-refusal')
            write(evidence / 'canonical-after-refusal.json', {
                'preserved_old_output': target.read_text(), 'design_verdict': failed_import['design_verdict'],
                'failure_scope': 'owned consumer seam fault injection; no physical design failure inferred'})

    def test_missing_attributed_fields_creates_gap_and_no_eligible_pass(self):
        write(self.l5, {'analog_blocks': [{'name': 'ref', 'type': 'bandgap', 'spec': {'specs': []}}]})
        prepared = self.prepared()
        controller = em.Controller(prepared.registry, em.Budget(1, 256, workers=1))
        run = self.root / 'run'
        controller.run(prepared.context, run)
        receipt = json.loads((run / 'analog-a1/receipt.json').read_text())
        self.assertNotEqual(receipt['status'], 'ELIGIBLE')
        self.assertTrue((run / 'analog-a1/outputs/project/phase3/analog/ref/spec_gap.json').is_file())
        self.assertFalse((run / 'analog-a1/outputs/project/phase3/analog/ref/spec.json').exists())
        if os.environ.get('F5_EVIDENCE_ROOT'):
            target = Path(os.environ['F5_EVIDENCE_ROOT']) / self._testMethodName
            shutil.copytree(run, target / 'controller-run')
            shutil.copyfile(prepared.context.request_record, target / 'request.json')

    def test_typed_paths_and_facts_file_document_must_match(self):
        prepared = self.prepared()
        record = json.loads(prepared.context.request_record.read_text())
        self.assertEqual(record['parameters']['declaration'], str(self.declaration))
        facts = self.root / 'facts.json'
        write(facts, {'available': False})
        with self.assertRaises(em.Refusal):
            self.prepared(native_facts={'available': True}, native_facts_file=facts)
        with self.assertRaises(em.Refusal):
            self.prepared(unknown=object())

    def test_current_empty_declaration_is_bound_inapplicability(self):
        write(self.declaration, {'blocks': []})
        prepared = self.prepared()
        self.assertEqual(prepared.disposition, 'declared_inapplicable')
        self.assertEqual(prepared.handoff['declaration_sha256'], em.digest(self.declaration))
        self.assertEqual(prepared.handoff['facts']['blocks'], [])

    def test_source_change_and_wrong_producer_source_are_refused(self):
        worker, private_sources = self.private_worker_closure()
        original_worker = analog.WORKER
        try:
            # Existing owned factory seam selects this actual copied worker.
            analog.WORKER = worker
            registry = FactoryRegistry()
            registry.register(analog)
            request = replace(self.request(), source_files={**sources(), **private_sources})
            prepared, controller, run, adopted = self.execute(registry.prepare(request))
        finally:
            analog.WORKER = original_worker
        receipt = json.loads((run / 'analog-a1/receipt.json').read_text())
        self.assertEqual(Path(receipt['processes'][0]['argv'][1]), worker)
        self.assertIn(str(worker), prepared.context.source_files)
        output = run / 'analog-a1/outputs'
        producer = output / 'producer.json'
        data = json.loads(producer.read_text()); data['source_sha'] = '0' * 40
        producer.write_text(json.dumps(data))
        evidence = validate_outputs(output, prepared.context.binding())
        self.assertNotEqual(evidence.verdict, 'PASS')
        content = worker.read_bytes()
        try:
            worker.write_bytes(content + b'\n# finite source reverse control\n')
            with self.assertRaises(em.Refusal):
                prepared.context.binding()
        finally:
            worker.write_bytes(content)
        prepared.context.binding()


class MixedSubstanceControls(unittest.TestCase):
    def test_si_missing_limit_zero_population_and_unit_mismatch_refused(self):
        plan = {'interfaces': [{'name': 'ref', 'pin': 'u/A', 'metrics': {
            'slew_max_rise': {'unit': 's', 'limit': 2e-9, 'relation': 'max'}}}]}
        samples = {'ref:slew_max_rise': {'value': 1e-9, 'unit': 's'}}
        self.assertTrue(grade_si(plan, samples)['all_interfaces_clean'])
        samples['ref:slew_max_rise']['value'] = 3e-9
        self.assertFalse(grade_si(plan, samples)['all_interfaces_clean'])
        del plan['interfaces'][0]['metrics']['slew_max_rise']['limit']
        with self.assertRaises(em.Refusal):
            grade_si(plan, samples)
        with self.assertRaises(em.Refusal):
            grade_si({'interfaces': []}, {})

    def test_power_inserted_cell_path_and_physical_population_required(self):
        # Explicit synthetic post-native data for the new derivation engine;
        # this is not a measured OpenROAD/Yosys qualification.
        physical = {'top': 'top', 'domains': {
            'PD_A': {'voltage': 1.2, 'elements': ['src', 'ls'], 'power_switches': []},
            'PD_B': {'voltage': 1.8, 'elements': ['dst'], 'power_switches': []}},
            'cells': {'src': 'BUF', 'ls': 'LS', 'dst': 'BUF'}, 'nets': {
            'n1': {'signal_type': 'SIGNAL', 'terminals': [{'instance': 'src', 'pin': 'Y'}, {'instance': 'ls', 'pin': 'A'}]},
            'n2': {'signal_type': 'SIGNAL', 'terminals': [{'instance': 'ls', 'pin': 'Y'}, {'instance': 'dst', 'pin': 'A'}]}}}
        logical = {'modules': {'top': {'cells': {
            'src': {'type': 'BUF', 'port_directions': {'Y': 'output'}, 'connections': {'Y': [1]}},
            'ls': {'type': 'LS', 'port_directions': {'A': 'input', 'Y': 'output'}, 'connections': {'A': [1], 'Y': [2]}},
            'dst': {'type': 'BUF', 'port_directions': {'A': 'input'}, 'connections': {'A': [2]}}},
            'netnames': {'n1': {'bits': [1]}, 'n2': {'bits': [2]}}}}}
        states = {'domains': {'PD_A': {'can_power_down': False}, 'PD_B': {'can_power_down': False}}}
        roles = {'LS': {'level_shifter': True, 'isolation': False, 'liberty_sha256': 'a' * 64}}
        pd, ls, iso = derive_power_reports(physical, logical, states, roles)
        self.assertTrue(pd['all_crossings_protected'])
        self.assertEqual(ls['level_shifters'][0]['instance'], 'ls')
        # The same cell name, without the native Liberty protection role,
        # cannot satisfy the voltage crossing by naming convention.
        roles['LS']['level_shifter'] = False
        pd, _, _ = derive_power_reports(physical, logical, states, roles)
        self.assertFalse(pd['all_crossings_protected'])
        physical['cells']['dst'] = 'DIFFERENT'
        with self.assertRaises(em.Refusal):
            derive_power_reports(physical, logical, states, roles)


if __name__ == '__main__':
    unittest.main()

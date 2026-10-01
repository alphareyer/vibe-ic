"""Finite F4 source controls, not physical or independent review evidence."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_modes as em
import execution_adapters_release as release
from execution_step_protocol import StepRequest, FactoryRegistry

SOURCE = os.environ.get('F4_SOURCE_SHA', '16dfe2a2615fa86ed1791019ec485b22eeed67e8')

def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))

def request(tmp, step='37.4', route='IC'):
    project = tmp / 'neutral'
    project.mkdir()
    put(project / release.DECLARATION, {
        'answers': {'deliverable': 'DIE' if route == 'IC' else 'HARDMACRO', 'top_cell': 'neutral'},
        'answer_provenance': {'deliverable': {'answered_by': 'owner', 'citation': 'Original neutral source-control INPUT'}}})
    gds = project / 'phase3/stage4/gds/neutral.gds'
    gds.parent.mkdir(parents=True)
    gds.write_bytes(b'Neutral source-only opaque INPUT; no native/GDS measurement')
    put(project / 'reports/phase3/drc_signoff_magic.json', {'summary': {'real_violation_total': 3, 'producers': [{'producer': 'magic'}]}})
    lease = tmp / 'lease'
    put(lease / 'lease.json', {'scope': 'FINITE_SOURCE_CONTROL_ONLY', 'native_admission': False})
    return StepRequest(step, project, {'top': 'neutral', 'pdk': 'neutral_pdk', 'route': route,
                                     'image': 'NOT_USED_SOURCE_ONLY', 'declaration': project / release.DECLARATION,
                                     'input_roots': {'neutral_source_control': project}},
                       lease, tmp / 'run', SOURCE, release.required_source_files())

def prepare(req):
    factories = FactoryRegistry()
    factories.register(release)
    return factories.prepare(req)

def identity(req):
    return dict({k: req.parameters[k] for k in ('top', 'pdk', 'route', 'image')}, source_sha=SOURCE)

def execute(tmp, mode='default-mode'):
    req = request(tmp)
    prepared = prepare(req)
    controller = em.Controller(prepared.registry, em.Budget(2, 2048, workers=1))
    controller.run(prepared.context, req.record, mode)
    arm = prepared.registry.adapters(req.step_id)[0]
    receipt = req.record / arm.arm_id / 'receipt.json'
    measured = json.loads(receipt.read_text())
    assert measured['status'] == 'ELIGIBLE', measured
    # A disclosed source-control selection exercises adoption. Live independent
    # AI choice remains F1/root-owned and is not claimed by this fixture.
    choice = {'arm_id': arm.arm_id, 'receipt_sha256': em.digest(receipt),
              'rationale': 'Source-control choice of actual exact generated canonical metrics',
              'reviewer': 'FINITE_TEST_CONTROL_NOT_INDEPENDENT_REVIEW', 'binding': prepared.context.binding()}
    adopted = controller.adopt(prepared.context, req.record, choice)
    return req, prepared, controller, adopted

@pytest.mark.parametrize('mode', ['default-mode', 'ultra-mode'])
def test_actual_metrics_producer_adoption_document_consumer(tmp_path, mode):
    req, prepared, controller, adopted = execute(tmp_path, mode)
    consumed = prepared.consume(req.project, prepared.context, controller, req.record, adopted)
    assert consumed['status'] == 'IMPORTED'
    assert consumed['consumer_detail']['status'] == 'CONSUMED'
    assert consumed['binding'] == prepared.context.binding()
    assert consumed['selected_generation'] == adopted['selected_generation']
    assert consumed['copied']
    assert consumed['consumer_detail']['qualification'] == 'PASS'
    assert consumed['primary_gates'] and set(consumed['primary_gates'].values()) == {'PASS'}
    assert prepared.adoption_paths
    assert all(any(name == path or name.startswith(path + '/') for path in prepared.adoption_paths)
               for name in consumed['copied'])
    assert consumed['consumer_detail']['consumer']['substantive']['release_document_bytes_equal'] is True
    assert consumed['consumer_detail']['consumer']['substantive']['all_keys'] > 20
    assert consumed['design_verdict'] == 'FAIL'  # positive current Magic3 is preserved
    products = consumed['copied']
    for name, digest in products.items():
        assert em.digest(req.project / name) == digest
    metrics = json.loads((req.project / 'phase3/final/metrics.json').read_text())
    assert metrics['magic__drc_error__count'] == 3
    assert any(value == 'NOT_MEASURED' for value in metrics.values())


def test_f199_frontdoor_step16_clock_plan_producer_and_consumer(tmp_path):
    from execution_production import frontdoor_parameters
    req = request(tmp_path, '16')
    declaration = tmp_path / 'owner-controls' / 'signed-declaration.json'
    put(declaration, json.loads((req.project / release.DECLARATION).read_text()))
    supplemental_target = tmp_path / 'owner-controls' / 'release-scope-source.json'
    put(supplemental_target, {'control': 'actual external source control'})
    supplemental = tmp_path / 'owner-controls' / 'release-scope.json'
    supplemental.symlink_to(supplemental_target.name)
    supplied = dict(req.parameters, declaration=declaration,
                    input_roots={'neutral_source_control': req.project,
                                 'owner_release_scope': supplemental})
    floorplan = req.project / 'phase3/stage3/pnr/floorplan.def'
    floorplan.parent.mkdir(parents=True)
    floorplan.write_text('VERSION 5.8 ;\nDESIGN neutral ;\nEND DESIGN\n')
    sdc = req.project / 'phase2/stage2/constraints/clock.sdc'
    sdc.parent.mkdir(parents=True)
    sdc.write_text('create_clock -name core_clk -period 10 [get_ports clk]\n')
    parameters, extra_sources = frontdoor_parameters(
        dict(supplied, project=req.project, step_id='16'), {'cpus': 2, 'ram_mb': 2048})
    assert parameters['declaration'] == declaration
    normalized = type(req)(req.step_id, req.project, parameters, req.lease, req.record,
                           req.source_sha, req.source_files)
    prepared = prepare(normalized)
    controller = em.Controller(prepared.registry, em.Budget(2, 2048, workers=1))
    controller.run(prepared.context, normalized.record)
    arm = prepared.registry.adapters('16')[0]
    receipt = json.loads((normalized.record / arm.arm_id / 'receipt.json').read_text())
    assert receipt['status'] == 'ELIGIBLE', receipt
    evidence = json.loads((normalized.record / arm.arm_id / 'outputs/release-evidence.json').read_text())
    assert any(row['program'] == 'phase3_one_shot_runner.emit_clock_plan' and row['rc'] == 0
               for row in evidence['producer'])
    assert any(row['program'] == 'clock_plan_check' and row['verdict'] == 'PASS'
               for row in evidence['consumer']['gate_records'])
    choice = {'arm_id': arm.arm_id, 'receipt_sha256': em.digest(normalized.record / arm.arm_id / 'receipt.json'),
              'rationale': 'Finite F199 Step16 source control of the emitted clock plan',
              'reviewer': 'FINITE_TEST_CONTROL_NOT_INDEPENDENT_REVIEW',
              'binding': prepared.context.binding()}
    adopted = controller.adopt(prepared.context, normalized.record, choice)
    consumed = prepared.consume(normalized.project, prepared.context, controller, normalized.record, adopted)
    assert set(consumed) == {'status', 'step_id', 'source_sha', 'selected_generation', 'binding',
                             'copied', 'primary_gates', 'design_verdict', 'consumer_detail'}
    assert consumed['status'] == 'IMPORTED'
    assert consumed['selected_generation'] == adopted['selected_generation']
    assert consumed['binding'] == prepared.context.binding()
    assert consumed['source_sha'] == normalized.source_sha
    assert consumed['copied'] and consumed['primary_gates']
    assert set(consumed['primary_gates'].values()) == {'PASS'}
    assert consumed['consumer_detail']['qualification'] == 'PASS'
    plan = normalized.project / 'phase3/stage3/cts/clock_plan.json'
    assert em.digest(plan) == consumed['copied']['phase3/stage3/cts/clock_plan.json']
    assert extra_sources == {}
    assert prepared.context.inputs['controls/declaration'] == declaration
    assert prepared.context.inputs['controls/owner_release_scope'] == supplemental_target
    proof_path = os.environ.get('F4_F199_PROOF_OUTPUT')
    if proof_path:
        gates = evidence['consumer']['gate_records']
        proof = {'schema': 'vibeic.f4.f199.step16.source-control.v1',
                 'scope': 'finite software source control; no native engines or physical qualification',
                 'source_sha': normalized.source_sha,
                 'producer': evidence['producer'], 'consumer_gate_records': gates,
                 'input_population': json.loads(prepared.context.population),
                 'input_file_sha256': {name: em.digest(path)
                                       for name, path in prepared.context.inputs.items()},
                 'input_roots': {name: str(path) for name, path in parameters['input_roots'].items()},
                 'control_root_sha256': {'controls/declaration': em.digest(declaration),
                                         'controls/owner_release_scope': em.digest(supplemental)},
                 'adoption_paths': list(prepared.adoption_paths),
                 'parameter_snapshot': json.loads(prepared.context.metadata.read_text())['parameters'],
                 'issued_generation': adopted['selected_generation'],
                 'imported_result': consumed,
                 'canonical_clock_plan_sha256': em.digest(plan),
                 'source_api_sha256': {path.name: em.digest(path) for path in
                                       (PROGRAMS / 'execution_step_protocol.py',
                                        PROGRAMS / 'execution_production.py')}}
        target = Path(proof_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(proof, indent=2, sort_keys=True) + '\n')


def test_f199_native_facts_outside_project_are_typed_and_current(tmp_path):
    from execution_production import frontdoor_parameters
    req = request(tmp_path, '16')
    facts_file = tmp_path / 'capacity' / 'native-facts.json'
    facts = {'source_files': {str(release.WORKER.resolve()): em.digest(release.WORKER)}}
    put(facts_file, facts)
    parameters, extra_sources = frontdoor_parameters(
        dict(req.parameters, project=req.project, step_id='16'),
        {'cpus': 2, 'ram_mb': 2048, 'native_facts': facts_file})
    assert parameters['native_facts'] == facts
    assert parameters['native_facts_file'] == facts_file
    assert parameters['native_facts_sha256'] == em.digest(facts_file)
    assert parameters['input_roots']['native_facts'] == facts_file
    assert extra_sources[str(release.WORKER.resolve())] == em.digest(release.WORKER)
    normalized = type(req)(req.step_id, req.project, parameters, req.lease, req.record,
                           req.source_sha, req.source_files)
    prepared = prepare(normalized)
    assert prepared.context.inputs['controls/native_facts'] == facts_file
    prepared.context.binding()
    missing_root_parameters = dict(parameters, input_roots=dict(
        parameters['input_roots'], owner_missing_control=tmp_path / 'missing-owner-control.json'))
    missing_root_request = type(req)(req.step_id, req.project, missing_root_parameters,
                                     req.lease, req.record, req.source_sha, req.source_files)
    with pytest.raises(em.Refusal, match='RELEASE_STEP_INPUT_ROOT_INVALID'):
        prepare(missing_root_request)
    facts_file.write_text(json.dumps({'source_files': facts['source_files'], 'changed': True}))
    with pytest.raises(em.Refusal, match='RELEASE_CONTROL_INPUT_CHANGED'):
        prepared.context.binding()

@pytest.mark.parametrize('mutation', ['added', 'deleted', 'bytes', 'alias', 'alias_retarget'])
def test_complete_input_population_refuses_mutation(tmp_path, mutation):
    req = request(tmp_path)
    path = req.project / 'reports/phase3/consumed-upstream.json'
    put(path, {'counts': 4})
    twin = req.project / 'reports/phase3/other-upstream.json'
    twin.write_bytes(path.read_bytes())
    alias = req.project / 'reports/phase3/alias.json'
    if mutation == 'alias_retarget':
        alias.symlink_to(path.name)
    prepared = release.prepare(req)
    prepared.context.binding()
    if mutation == 'added':
        put(req.project / 'reports/phase3/new-upstream.json', {'counts': 4})
    elif mutation == 'deleted':
        path.unlink()
    elif mutation == 'bytes':
        put(path, {'counts': 0})
    elif mutation == 'alias':
        path.unlink(); path.symlink_to(twin.name)
    else:
        alias.unlink(); alias.symlink_to(twin.name)
    with pytest.raises(em.Refusal, match='RELEASE_INPUT_POPULATION_CHANGED'):
        prepared.context.binding()

@pytest.mark.parametrize('mutation', ['changed', 'deleted', 'added', 'alias'])
def test_selected_output_population_refused_before_import(tmp_path, mutation):
    req, prepared, controller, adopted = execute(tmp_path)
    generation = Path(adopted['selected_generation']['directory'])
    file = generation / 'artifacts/phase3/final/metrics.json'
    if mutation == 'changed':
        file.chmod(0o644); file.write_text('{}')
    elif mutation == 'deleted':
        file.unlink()
    elif mutation == 'added':
        (generation / 'unissued-extra-output').write_text('added')
    else:
        original = file.read_bytes(); file.unlink()
        sibling = generation / 'aliased-original'; sibling.write_bytes(original); file.symlink_to(sibling)
    before = release.population(req.project)
    with pytest.raises(em.Refusal):
        prepared.consume(req.project, prepared.context, controller, req.record, adopted)
    assert release.population(req.project) == before

def test_wrong_issued_generation_refuses(tmp_path):
    req, prepared, controller, adopted = execute(tmp_path)
    forged = dict(adopted)
    forged['selected_generation'] = dict(adopted['selected_generation'], generation='wrong')
    with pytest.raises(em.Refusal, match='RELEASE_WRONG_ADOPTION'):
        prepared.consume(req.project, prepared.context, controller, req.record, forged)
    assert not (req.project / 'phase3/final/metrics.json').exists()

def test_consumer_refusal_rolls_back_existing_and_new_outputs(tmp_path, monkeypatch):
    req, prepared, controller, adopted = execute(tmp_path)
    target = req.project / 'phase3/final/metrics.json'
    put(target, {'previous_release': 'byte-preserved'})
    before = release.population(req.project)
    import execution_release_worker as worker
    original = worker.validate_products
    def refuse_installed(step, project, parameters, write_reports=True):
        if not write_reports:
            return {'qualification': 'FAIL', 'design_verdict': 'FAIL'}
        return original(step, project, parameters, write_reports)
    monkeypatch.setattr(worker, 'validate_products', refuse_installed)
    from execution_production import _consume
    with pytest.raises(em.Refusal, match='GATE_FAIL'):
        _consume(prepared, req, controller, req.record, adopted)
    assert release.population(req.project) == before
    assert json.loads(target.read_text()) == {'previous_release': 'byte-preserved'}
    receipt = json.loads((req.record / 'import.json').read_text())
    assert receipt['status'] == 'ROLLED_BACK'
    assert receipt['reason'] == 'GATE_FAIL'
    assert receipt['design_verdict'] == 'FAIL'

def test_actual_gate_fail_is_ineligible(tmp_path):
    req = request(tmp_path, '16')
    floorplan = req.project / 'phase3/stage3/pnr/floorplan.def'
    floorplan.parent.mkdir(parents=True); floorplan.write_text('VERSION 5.8 ;\nDESIGN neutral ;\nEND DESIGN\n')
    sdc = req.project / 'phase2/stage2/constraints/empty.sdc'
    sdc.parent.mkdir(parents=True); sdc.write_text('# No clock declared; do not invent one\n')
    prepared = release.prepare(req)
    controller = em.Controller(prepared.registry, em.Budget(2, 2048, workers=1))
    controller.run(prepared.context, req.record)
    arm = prepared.registry.adapters(req.step_id)[0]
    receipt = json.loads((req.record / arm.arm_id / 'receipt.json').read_text())
    assert receipt['status'] != 'ELIGIBLE'
    assert receipt['evidence']['verdict'] in ('FAIL', 'NOT_MEASURED')
    assert not (req.project / 'phase3/stage3/cts/clock_plan.json').exists()

@pytest.mark.parametrize('step', release.EXTERNAL_IDS)
def test_missing_physical_population_is_external_never_pass(tmp_path, step):
    prepared = release.prepare(request(tmp_path, step))
    assert prepared.disposition == 'external_handoff'
    assert prepared.registry is None
    assert prepared.handoff['design_verdict'] == 'NOT_MEASURED'
    assert prepared.handoff['missing_obligations']
    assert prepared.handoff['software_physical_pass'] is False
    assert prepared.handoff['native_processes_launched'] == 0

def test_independent_bound_physical_fail_survives_missing_sibling(tmp_path):
    req = request(tmp_path, '44')
    raw = req.project / 'phase3/stage5_manufacturing/htol_results.json'
    put(raw, {'units_tested': 10, 'stress_hours': 1000, 'failures': 1, 'device_hours': 10000})
    document = {'schema': 'vibeic.release.external.v1', 'step_id': '44',
                'identities': identity(req),
                'measurement_id': 'NEUTRAL_SOURCE_FIXTURE_NOT_PHYSICAL', 'issuer': 'finite source control',
                'raw_files': {raw.relative_to(req.project).as_posix(): em.digest(raw)},
                'subject_inputs': {p.relative_to(req.project).as_posix(): em.digest(p)
                                   for p in req.project.rglob('*') if p.is_file() and p != raw},
                'verdict': 'FAIL'}
    path = req.project / 'external/htol-receipt.json'; put(path, document)
    malformed = req.project / 'external/malformed-receipt.json'; malformed.write_text('not json')
    prepared = release.prepare(StepRequest(req.step_id, req.project,
        dict(req.parameters, external_receipts=['external/htol-receipt.json', 'external/malformed-receipt.json']),
        req.lease, req.record, req.source_sha, req.source_files))
    assert prepared.handoff['design_verdict'] == 'FAIL'
    assert prepared.handoff['invalid_receipts_or_materials']
    assert prepared.handoff['software_physical_pass'] is False

def test_step38_actual_generator_packages_selected_layout_bytes(tmp_path):
    import struct
    req = request(tmp_path, '38')
    def record(kind, data_type, body=b''):
        if len(body) % 2: body += b'\0'
        return struct.pack('>HBB', len(body) + 4, kind, data_type) + body
    # One ordinary boundary in a readable source-control GDS stream. These are
    # neutral INPUT bytes, never a native design or signoff measurement.
    data = record(0, 2, struct.pack('>h', 600)) + record(1, 2, b'\0' * 24)
    data += record(2, 6, b'NEUTRAL') + record(3, 5, bytes.fromhex('3e4189374bc6a7f03944b82fa09b5a54'))
    data += record(5, 2, b'\0' * 24) + record(6, 6, b'neutral') + record(8, 0)
    data += record(13, 2, struct.pack('>h', 1)) + record(14, 2, struct.pack('>h', 0))
    data += record(16, 3, struct.pack('>10i', 0, 0, 10000, 0, 10000, 10000, 0, 10000, 0, 0))
    data += record(17, 0) + record(7, 0) + record(4, 0)
    (req.project / 'phase3/stage4/gds/neutral.gds').write_bytes(data)
    prepared = release.prepare(req)
    controller = em.Controller(prepared.registry, em.Budget(2, 2048, workers=1))
    controller.run(prepared.context, req.record)
    report_path = req.record / 'release_38/outputs/release-evidence.json'
    report = json.loads(report_path.read_text())
    assert report['producer'][0]['program'] == 'foundry_handoff_pack_gen'
    assert report['producer'][0]['rc'] == 0
    artifact = req.record / 'release_38/outputs/artifacts/phase3/stage4/foundry_handoff/neutral.gds'
    assert artifact.read_bytes() == data
    assert report['consumer']['substantive']['stale_layout_members'] == []
    assert report['physical_measurement'] == 'NOT_MEASURED'
    assert report['design_verdict'] != 'PASS'
    assert not (req.project / 'phase3/stage4/foundry_handoff/mask_spec.json').exists()

@pytest.mark.parametrize('mutation', ['source', 'raw', 'issuer', 'subject'])
def test_wrong_external_identity_refuses_credit(tmp_path, mutation):
    req = request(tmp_path, '44')
    raw = req.project / 'phase3/stage5_manufacturing/htol_results.json'
    put(raw, {'units_tested': 10, 'stress_hours': 1000, 'failures': 0, 'device_hours': 10000})
    doc = {'schema': 'vibeic.release.external.v1', 'step_id': '44',
           'identities': identity(req), 'measurement_id': 'NEUTRAL', 'issuer': 'source control',
           'raw_files': {raw.relative_to(req.project).as_posix(): em.digest(raw)},
           'subject_inputs': {release.DECLARATION: em.digest(req.project / release.DECLARATION)}, 'verdict': 'PASS'}
    if mutation == 'source': doc['identities']['source_sha'] = '0' * 40
    elif mutation == 'raw': doc['raw_files'][raw.relative_to(req.project).as_posix()] = '0' * 64
    elif mutation == 'issuer': doc['issuer'] = None
    else: doc['subject_inputs'][release.DECLARATION] = None
    put(req.project / 'external/receipt.json', doc)
    prepared = prepare(StepRequest(req.step_id, req.project, dict(req.parameters, external_receipts=['external/receipt.json']), req.lease, req.record, req.source_sha, req.source_files))
    assert prepared.handoff['design_verdict'] == 'NOT_MEASURED'
    assert prepared.handoff['invalid_receipts_or_materials']

@pytest.mark.parametrize('route', ['IC', 'IP'])
def test_hardmacro_requires_native_admission_on_every_route(tmp_path, route):
    with pytest.raises(em.Refusal, match='RELEASE_NATIVE_ADMISSION_REQUIRED') as refused:
        prepare(request(tmp_path, '37.5ip', route))
    assert json.loads(str(refused.value).split(': ', 1)[1])['all_routes'] is True

def test_ic_precheck_inapplicability_is_owner_bound(tmp_path):
    req = request(tmp_path, '37.5ic', 'IP')
    prepared = prepare(req)
    assert prepared.disposition == 'declared_inapplicable'
    assert prepared.handoff['binding']['inputs']['project/' + release.DECLARATION]
    assert prepared.handoff['declaration'] == str(req.parameters['declaration'])
    assert prepared.handoff['declaration_sha256'] == em.digest(req.project / release.DECLARATION)
    assert prepared.handoff['facts']['deliverable'] == 'HARDMACRO'
    put(req.project / release.DECLARATION, {'answers': {'deliverable': 'DIE', 'top_cell': 'neutral'}})
    with pytest.raises(em.Refusal, match='RELEASE_DECLARATION_INVALID'):
        release.prepare(req)

def test_current_content_findings_refuse_after_cli_checked_older_bytes(tmp_path, monkeypatch):
    """Actual producer/CLI/checker; one disclosed output-race injection.

    Restore the exact earlier refused DFM product after the real content CLI
    exits and before its current-byte semantic reread. No return code, finding,
    producer, checker or assertion is synthesized/replaced.
    """
    import execution_release_worker as worker
    req = request(tmp_path, '35')
    before = release.population(req.project)
    failed_producer = worker.produce('35', req.project, req.parameters)
    product = req.project / 'reports/phase3/dfm_screen.json'
    failed = product.read_bytes()
    import flow_step_output_content_check as content
    initial_findings = content.check(req.project, 'dfm')
    assert initial_findings
    failed_sha = em.digest(product)
    routed = req.project / 'phase3/stage3/pnr/routed.def'
    routed.parent.mkdir(parents=True)
    routed.write_text('VERSION 5.8 ;\nDESIGN neutral ;\nNETS 0 ;\nEND NETS\nEND DESIGN\n')
    current_producer = worker.produce('35', req.project, req.parameters)
    assert content.check(req.project, 'dfm') == []
    current_sha = em.digest(product)
    actual_call = worker.call
    def restore_actual_failed_product(program, arguments):
        result = actual_call(program, arguments)
        if program == 'flow_step_output_content_check' and 'dfm' in list(map(str, arguments)):
            assert result['rc'] == 0
            product.write_bytes(failed)
        return result
    monkeypatch.setattr(worker, 'call', restore_actual_failed_product)
    checked = worker.gates('35', req.project)
    row = next(r for r in checked if r['program'] == 'flow_step_output_content_check')
    receipt = {'case': 'F4_CURRENT_CONTENT_FINDINGS', 'expected': 'FAIL', 'actual': row['verdict'],
               'source_sha': SOURCE, 'worker_sha256': em.digest(Path(worker.__file__)),
               'consumer': {'path': str(Path(content.__file__).resolve()), 'sha256': em.digest(Path(content.__file__))},
               'input_population_before': before, 'input_population_current': release.population(req.project, release.output_patterns('35')),
               'canonical_product': 'reports/phase3/dfm_screen.json', 'failed_product_sha256': failed_sha,
               'earlier_checked_product_sha256': current_sha, 'failed_producer': failed_producer,
               'current_producer': current_producer, 'actual_gate': row,
               'injection': 'Restore byte-exact existing failed producer product after actual CLI rc0, before actual semantic reread',
               'native_runs': 0, 'pid': os.getpid(), 'cid': None}
    destination = os.environ.get('F4_CONTROL_RECEIPT_DIR')
    if destination:
        put(Path(destination) / 'F4_CURRENT_CONTENT_FINDINGS.json', receipt)
    assert row['rc'] == 0  # the earlier process result cannot override current findings
    assert row['content_check'] == initial_findings
    assert product.read_bytes() == failed
    assert row['verdict'] == 'FAIL', receipt

@pytest.mark.parametrize('mutation', ['declaration', 'source_manifest', 'lease_file'])
def test_f1_interface_refuses_missing_bound_facts(tmp_path, mutation):
    req = request(tmp_path)
    params, sources = dict(req.parameters), dict(req.source_files)
    lease = req.lease
    expected = 'RELEASE_'
    if mutation == 'declaration':
        params.pop('declaration')
    elif mutation == 'source_manifest':
        sources.pop(str(release.WORKER))
    else:
        lease = req.lease / 'lease.json'
    with pytest.raises(em.Refusal, match=expected):
        prepare(StepRequest(req.step_id, req.project, params, lease, req.record, req.source_sha, sources))

@pytest.mark.parametrize('step', ['14', '35', '36', '37.5ic'])
def test_remaining_actual_producer_gate_refusals_are_substantive(tmp_path, step):
    req = request(tmp_path, step)
    prepared = prepare(req)
    controller = em.Controller(prepared.registry, em.Budget(2, 2048, workers=1))
    controller.run(prepared.context, req.record)
    arm = prepared.registry.adapters(step)[0]
    receipt = json.loads((req.record / arm.arm_id / 'receipt.json').read_text())
    assert receipt['status'] != 'ELIGIBLE'
    report = json.loads((req.record / arm.arm_id / 'outputs/release-evidence.json').read_text())
    assert report['consumer']['qualification'] in ('FAIL', 'NOT_MEASURED')
    assert report['consumer']['missing'] or any(r['verdict'] != 'PASS' for r in report['consumer']['gate_records'])
    assert report['producer']
    if step == '14':
        assert report['producer'][1]['program'] == 'flow_compliance_check'
        assert (req.record / arm.arm_id / 'outputs/artifacts/reports/analog/stage_analog_compliance.json').is_file()
    assert report['physical_measurement'] == 'NOT_MEASURED'

@pytest.mark.parametrize('mutation', ['identity', 'expired', 'input'])
def test_native_admission_refuses_before_any_engine(tmp_path, mutation, monkeypatch):
    import execution_release_worker as worker
    import time
    req = request(tmp_path, '37.5ip')
    params = dict(req.parameters, source_sha=SOURCE, pdk_root='input/pdk', input_population_sha256='a' * 64)
    admission = {'identities': {k: params[k] for k in ('source_sha', 'top', 'pdk', 'image')},
                 'status': 'ADMITTED', 'deadline_epoch_s': time.time() + 60,
                 'exact_input_sha256': 'a' * 64, 'tool_binaries': {str(Path(sys.executable).resolve()): em.digest(Path(sys.executable).resolve())}}
    admission['identities']['step_id'] = '37.5ip'
    if mutation == 'identity': admission['identities']['source_sha'] = '0' * 40
    elif mutation == 'expired': admission['deadline_epoch_s'] = 1
    else: admission['exact_input_sha256'] = 'b' * 64
    path = tmp_path / 'INVALID_CONTROL_ONLY_NATIVE_ENVELOPE.json'
    put(path, admission)
    params['native_admission'] = str(path)
    def no_engine(*args, **kwargs):
        pytest.fail('Invalid native envelope reached an engine')
    monkeypatch.setattr(worker, 'call', no_engine)
    with pytest.raises(em.Refusal, match='RELEASE_NATIVE_'):
        worker.native_hardmacro(req.project, params)

def test_step_specific_roots_bind_missing_and_added_consumed_inputs(tmp_path):
    req = request(tmp_path)
    missing = req.project / 'consumed' / 'not-yet-staged.json'
    params = dict(req.parameters, input_roots={'declaration': req.project / release.DECLARATION,
                    'checker': req.project / 'reports/phase3/drc_signoff_magic.json',
                    'layout': req.project / 'phase3/stage4/gds', 'missing': missing})
    prepared = prepare(StepRequest(req.step_id, req.project, params, req.lease, req.record, req.source_sha, req.source_files))
    before = prepared.context.binding()
    put(req.project / 'unrelated/downstream/changed.json', {'mutable': True})
    assert prepared.context.binding() == before
    assert prepared.context.population_at_prepare['consumed/not-yet-staged.json']['kind'] == 'missing'
    put(missing, {'now': 'present'})
    with pytest.raises(em.Refusal, match='RELEASE_INPUT_POPULATION_CHANGED'):
        prepared.context.binding()

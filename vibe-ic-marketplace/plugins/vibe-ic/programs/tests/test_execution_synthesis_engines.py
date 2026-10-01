"""Bounded Step9 source/interface controls; no native engine qualification."""
from contextlib import nullcontext
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_modes as em
import execution_production as production
import execution_step_protocol as protocol
import execution_synthesis_engines as engines


@dataclass
class Pdk:
    name: str
    liberty: str


def request(tmp_path, monkeypatch, arm='librelane-mapped-synthesis'):
    project = tmp_path / 'project'
    rtl = project / 'phase2/stage1/rtl'
    rtl.mkdir(parents=True)
    (rtl / 'neutral.v').write_text('module neutral(input a, output y); assign y=a; endmodule\n')
    pdk = tmp_path / 'pdk'
    pdk.mkdir()
    liberty = pdk / 'neutral.lib'
    liberty.write_text('library(neutral) { cell(buf) { area: 1; } }\n')
    switch = project / 'phase3/librelane_switch.json'
    switch.parent.mkdir()
    switch.write_text(json.dumps({'pdk_root_host':str(pdk), 'steps':{'9':'librelane'}}))
    lease = tmp_path / 'lease'
    lease.mkdir()
    (lease / 'lease.json').write_text(json.dumps({'cpus':2, 'ram_mb':512,
        'host_ram_mb':256, 'container_ram_mb':256}))
    import librelane_contract as lc
    import librelane_image_facts as facts
    monkeypatch.setattr(production, 'native_boundary', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(lc, 'selected_mode', lambda *a: 'librelane')
    monkeypatch.setattr(lc, 'impl_step_modes', lambda *a: None)
    monkeypatch.setattr(lc, 'resolve_image', lambda *a: 'source-fixture-image')
    monkeypatch.setattr(lc, 'pdk_root_resolution', lambda *a, **kw: {'path':str(pdk)})
    monkeypatch.setattr(facts, 'image_facts', lambda *a: {'image_id':'sha256:'+'0'*64,
        'librelane_version':'source-fixture-NOT_QUALIFIED'})
    monkeypatch.setattr(facts, 'flow_steps', lambda *a: engines.LIBRELANE.native_steps)
    source = Path(production.__file__).resolve()
    files = [source, Path(sys.executable).resolve(), Path(engines.__file__).resolve(),
             Path(__file__).resolve().parents[1]/'execution_native_worker.py']
    return protocol.StepRequest('9', project, dict(top='neutral', pdk=Pdk('neutral',str(liberty)),
        container='source-fixture-no-native', synthesis_engine=arm), lease, tmp_path/'request.json',
        '624e18e13556064bb1fa575431e9f349bf2c5bc2', {str(path):em.digest(path) for path in files})


def test_default_provider_reaches_bound_native_component(tmp_path, monkeypatch):
    req = request(tmp_path, monkeypatch)
    prepared = production.prepare(req)
    spec = json.loads(req.record.read_text())
    arm, = prepared.registry.adapters('9')
    assert arm.arm_id == 'librelane-mapped-synthesis'
    assert arm.engine_families == ('yosys','abc')
    assert 'execution_native_worker.py' in arm.components[0].argv[1]
    assert spec['synthesis_engine'] == engines.LIBRELANE.contract()
    assert spec['engine_population']['source_provider_count'] == 1
    assert spec['input_hashes']['lease.json'] == em.digest(req.lease/'lease.json')
    assert spec['input_hashes']['project/phase2/stage1/rtl/neutral.v']
    assert prepared.consume is production.import_selected


@pytest.mark.parametrize('arm', ['independent-mapper', 'direct', 'slang'])
def test_unavailable_engine_is_refused_before_native_admission(tmp_path, monkeypatch, arm):
    req = request(tmp_path, monkeypatch, arm)
    entered = []
    monkeypatch.setattr(production, 'native_boundary', lambda *a, **kw: entered.append('entered') or nullcontext())
    observed = 'ADMITTED'
    try:
        production.prepare(req)
    except em.Refusal as exc:
        observed = exc.code
        assert 'SECOND_INDEPENDENT_ENGINE_UNAVAILABLE' in str(exc)
    assert observed == 'PRODUCTION_SYNTH_ENGINE_UNAVAILABLE'
    assert entered == []
    assert not req.record.exists()


def test_engine_population_keeps_wrappers_and_native_credit_separate():
    population = engines.population()
    assert population['independent_mapping_families'] == ['yosys+abc']
    assert population['source_provider_count'] == 1
    assert population['native_qualification'] == 'NOT_MEASURED'
    assert len(population['unavailable_independent_engine']['missing']) == 7


@pytest.mark.parametrize('field,value', [('native_entrypoint','another.mapper'),
    ('synthesis_engine', dict(engines.LIBRELANE.contract(), provenance_tool='another-tool'))])
def test_substituted_producer_does_not_reach_primary_consumers(tmp_path, monkeypatch, field, value):
    req = request(tmp_path, monkeypatch)
    production.prepare(req)
    inputs = tmp_path / 'run/inputs'
    outputs = tmp_path / 'run/outputs'
    inputs.mkdir(parents=True)
    outputs.mkdir()
    spec = json.loads(req.record.read_text())
    spec['input_hashes'] = {}
    (inputs/'request.json').write_text(json.dumps(spec))
    binding = {'required_gates':['synth_netlist_check'], 'inputs':{}, 'source_sha':req.source_sha}
    producer = dict(binding=binding, input_hashes={}, status='PASS',
        synthesis_engine=engines.LIBRELANE.contract(),
        native_entrypoint=engines.LIBRELANE.native_entrypoint)
    producer[field] = value
    (outputs/'producer.json').write_text(json.dumps(producer))
    observed = 'ACCEPTED'
    try:
        engines.consume_producer(spec, producer)
    except em.Refusal as exc:
        observed = exc.code
    assert observed == 'PRODUCTION_SYNTH_ENGINE_PRODUCER_CHANGED'
    consumed = []
    monkeypatch.setattr(production, '_gate', lambda *a: consumed.append(a) or 'PASS')
    with pytest.raises(em.Refusal) as error:
        production.validate_synthesis(outputs,binding)
    assert error.value.code == 'PRODUCTION_SYNTH_ENGINE_PRODUCER_CHANGED'
    assert consumed == []


@pytest.mark.parametrize('status', ['FAIL','NOT_MEASURED'])
def test_actual_nonpass_is_preserved_without_receipt_upgrade(tmp_path, status):
    outputs = tmp_path/'outputs'
    outputs.mkdir()
    binding = {'required_gates':['synth_netlist_check'], 'inputs':{}}
    (outputs/'producer.json').write_text(json.dumps(dict(binding=binding, input_hashes={},
        status=status, detail='original measured failure/nonmeasurement')))
    observed = production.validate_synthesis(outputs,binding)
    assert observed.verdict == status
    assert observed.gates == {'synth_netlist_check':'NOT_MEASURED'}
    assert observed.detail == 'original measured failure/nonmeasurement'


def test_selected_engine_substitution_stops_before_canonical_copy(tmp_path, monkeypatch):
    project = tmp_path/'canonical'
    project.mkdir()
    selected = tmp_path/'issued'
    selected.mkdir()
    run = tmp_path/'run'
    inputs = run/'librelane-mapped-synthesis/inputs'
    inputs.mkdir(parents=True)
    binding = {'source_sha':'624e18e13556064bb1fa575431e9f349bf2c5bc2'}
    class Context:
        def binding(self): return binding
    class Controller:
        def _generation_current(self, generation): pass
    producer = dict(synthesis_engine=engines.LIBRELANE.contract(), native_entrypoint='wrong.mapper')
    (selected/'producer.json').write_text(json.dumps(producer))
    (inputs/'request.json').write_text(json.dumps({'synthesis_engine':engines.LIBRELANE.contract()}))
    with pytest.raises(em.Refusal) as error:
        production.import_selected(project, Context(), Controller(), run,
            dict(selected='librelane-mapped-synthesis', selected_generation={'directory':str(selected)},
                 evidence={'binding':binding}))
    assert error.value.code == 'PRODUCTION_SYNTH_ENGINE_PRODUCER_CHANGED'
    assert list(project.iterdir()) == []


def aggregate_request(tmp_path, monkeypatch, quota=None):
    req = request(tmp_path, monkeypatch)
    quota = quota if quota is not None else dict(cpus=2, ram_mb=1536,
        host_ram_mb=512, container_ram_mb=1024)
    (req.lease/'lease.json').write_text(json.dumps(quota))
    return req


def test_aggregate_ram_reserves_real_host_plus_container(tmp_path, monkeypatch):
    req = aggregate_request(tmp_path, monkeypatch)
    prepared = production.prepare(req)
    arm, = prepared.registry.adapters('9')
    assert arm.ram_mb == 1536
    assert arm.cpus == 2
    spec = json.loads(req.record.read_text())
    assert spec['input_hashes']['lease.json'] == em.digest(req.lease/'lease.json')
    assert json.loads((req.lease/'lease.json').read_text()) == dict(cpus=2,ram_mb=1536,
        host_ram_mb=512,container_ram_mb=1024)


def test_aggregate_ram_drives_serial_scheduler(tmp_path, monkeypatch):
    """Capture only software scheduler calls; these are not real engine arms."""
    from dataclasses import replace
    import threading
    import time
    req = aggregate_request(tmp_path, monkeypatch)
    prepared = production.prepare(req)
    original, = prepared.registry.adapters('9')
    registry = em.Registry()
    # A one-CPU source fixture removes CPU serialization so RAM must bind.
    # No worker/component/native method is executed or tool qualified here.
    for name in ['source_fixture_a','source_fixture_b']:
        registry.register(replace(original,arm_id=name,cpus=1))
    controller = em.Controller(registry,em.Budget(cpus=2,ram_mb=1536,workers=2))
    # Queue two software jobs directly to test the unchanged RAM scheduler;
    # the real planner continues to deduplicate these same-engine wrappers.
    monkeypatch.setattr(controller,'plan',lambda context,*a: dict(
        arms=['source_fixture_a','source_fixture_b'],mode='ultra-mode',binding=context.binding()))
    lock = threading.Lock()
    both = threading.Event()
    active = 0
    peak = 0
    observed = []
    def software_call(arm,context,plan,output,cancel,cpuset=None):
        nonlocal active,peak
        with lock:
            active += 1
            peak = max(peak,active)
            if active == 2: both.set()
            observed.append(dict(arm=arm.arm_id,advertised_ram_mb=arm.ram_mb,
                host_plus_container_ram_mb=1536,cpuset=cpuset))
        both.wait(.15)
        with lock: active -= 1
        return dict(status='SOURCE_ONLY_NOT_NATIVE')
    monkeypatch.setattr(controller,'_run_arm',software_call)
    result = controller.run(prepared.context,tmp_path/'scheduler-run',execution_mode='ultra-mode')
    assert result['status'] == 'AWAITING_AI_SELECTION'
    assert len(observed) == 2
    assert peak * 1536 <= 1536
    assert peak == 1
    assert {row['advertised_ram_mb'] for row in observed} == {1536}


@pytest.mark.parametrize('change', [dict(ram_mb=1024),dict(container_ram_mb=None),
    dict(container_ram_mb=-1),dict(cpus=True),dict(host_ram_mb='512')])
def test_aggregate_ram_refuses_unbound_split(tmp_path, monkeypatch, change):
    quota = dict(cpus=2,ram_mb=1536,host_ram_mb=512,container_ram_mb=1024)
    quota.update(change)
    req = aggregate_request(tmp_path,monkeypatch,quota)
    observed = 'ADMITTED'
    try:
        production.prepare(req)
    except em.Refusal as exc:
        observed = exc.code
    assert observed == 'PRODUCTION_SYNTH_LEASE_BUDGET_UNBOUND'

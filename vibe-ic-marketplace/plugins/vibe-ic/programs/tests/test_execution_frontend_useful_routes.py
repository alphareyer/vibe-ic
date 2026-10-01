"""Only newly changed caller controls; no native tool or old proof execution."""
import json
from pathlib import Path
import shutil
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_adapters_frontend as frontend
import execution_frontend_worker as worker
import execution_modes as em


def test_existing_step4_selector_is_in_the_actual_input_binding(tmp_path):
    project = tmp_path/'project'
    rtl = project/'phase2/stage1/rtl'; rtl.mkdir(parents=True)
    (rtl/'fixture.v').write_text('module fixture(input clk, output q); assign q=clk; endmodule\n')
    parameters = {'step_id':'4', 'input_roots':{'rtl':rtl}}
    roots = worker.input_roots(project, parameters)
    assert 'phase3/librelane_switch.json' in roots
    files, population = frontend.lexical_tree(project, roots=roots)
    context = frontend.FrontendContext('4', 'a'*40, files,
        {'metric':'source_control', 'direction':'min'}, ('native_producer',),
        project=project, roots=roots, population=frontend.stable(population))
    before = context.binding()
    assert worker.producer_parameters(project, '4', {})['simulation_mode'] == 'direct'
    switch = project/'phase3/librelane_switch.json'; switch.parent.mkdir()
    switch.write_text('{"steps":{"4":"dual"}}\n')
    with pytest.raises(em.Refusal, match='FRONTEND_INPUT_POPULATION_CHANGED'):
        context.binding()
    assert worker.producer_parameters(project, '4', {'simulation_mode':'dual'})['simulation_mode'] == 'dual'
    with pytest.raises(em.Refusal, match='FRONTEND_SIMULATION_MODE_CONTRADICTION'):
        worker.producer_parameters(project, '4', {'simulation_mode':'direct'})
    assert json.loads(switch.read_text()) == {'steps':{'4':'dual'}}
    assert before['step_id'] == '4'


def test_unsupported_complete_provider_refuses_before_real_producer_boundary(tmp_path, monkeypatch):
    project = tmp_path/'project'; project.mkdir()
    def native_boundary(*args, **kwargs):
        pytest.fail('unsupported provider reached the actual native producer boundary')
    monkeypatch.setattr(worker, 'cli', native_boundary)
    monkeypatch.setattr(worker.subprocess, 'run', native_boundary)
    parameters = {'top':'fixture', 'netlist':'phase2/stage2/synth/netlist.v',
        'clock':'clk', 'pdk_name':'source_control', 'liberty':'neutral.lib',
        'cell_model':'neutral.v', 'timeout_s':1}
    for sid, key, unsupported in (('11','dft_engine','openroad-partial-scan'),
                                  ('12','post_dft_engine','librelane-resynthesis')):
        with pytest.raises(em.Refusal, match='FRONTEND_COMPLETE_PROVIDER_UNAVAILABLE') as caught:
            worker.produce(project, sid, dict(parameters, **{key:unsupported}))
        assert json.loads(str(caught.value).split(': ',1)[1])['requested'] == unsupported
    assert not (project/'phase2/stage2/synth/post_dft_netlist.v').exists()


def test_full_preplan_population_keeps_conditional_observations_unmeasured():
    required = worker.required_gates('8')
    assert set(required) == {'sdc_syntax_check', 'sdc_validator_check',
        'sdc_exception_correlation_check', 'derived_clock_sdc_required_check',
        'canonical_step_consumer', 'native_producer'}
    programs = tuple(name for name in required if name not in ('canonical_step_consumer','native_producer'))
    # An aggregate source record alone never measures the newly included
    # optional/advisory program population. Only this executed row is PASS.
    current = {'status':'PASS', 'program_execution_records':[
        {'gate':'sdc_syntax_check', 'cmd':'sdc_syntax_check .',
         'verdict':'PASS', 'exit_code':0}]}
    measured = worker.measured_program_gates(current, programs)
    assert measured['sdc_syntax_check'] == 'PASS'
    assert {name:measured[name] for name in programs if name != 'sdc_syntax_check'} == {
        'sdc_validator_check':'NOT_MEASURED', 'sdc_exception_correlation_check':'NOT_MEASURED',
        'derived_clock_sdc_required_check':'NOT_MEASURED'}


def test_step3_builds_current_yosys_input_before_all_four_consumers(tmp_path, monkeypatch):
    import _cdc_netlist as cdc
    fixture = PROGRAMS/'tests/fixtures/cdc_netlist'
    project = tmp_path/'project'
    rtl = project/'phase2/stage1/rtl'; rtl.mkdir(parents=True)
    shutil.copy(fixture/'multi_clock_sync.v', rtl/'multi_clock_sync.v')
    monkeypatch.setenv('F2_OUTPUT_ROOT', str(tmp_path/'outputs'))
    (tmp_path/'outputs').mkdir()

    # Keep the actual source enumeration, binding, build, and four CLIs. Only
    # the Yosys process boundary supplies the previously captured tool output.
    import subprocess
    actual_run = subprocess.run
    def captured_yosys(argv, **kwargs):
        if argv[0] != 'yosys':
            return actual_run(argv, **kwargs)
        target = Path(argv[-1].rsplit('json -o ', 1)[1])
        target.write_bytes((fixture/'multi_clock_sync.json').read_bytes())
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(cdc.shutil, 'which', lambda name: '/captured/yosys' if name == 'yosys' else None)
    monkeypatch.setattr(cdc.subprocess, 'run', captured_yosys)
    actual_cli = worker.cli
    checked_consumers = []

    def bound_cli(module, argv):
        netlist = project/cdc.NETLIST_REL
        parsed = cdc.load(netlist)
        cdc.require_binding(project, netlist, parsed)
        checked_consumers.append(module)
        return actual_cli(module, argv)

    monkeypatch.setattr(worker, 'cli', bound_cli)
    result = worker.produce(project, '3', {'top':'multi_clock_sync',
        'native':{'image_id':'sha256:'+'a'*64}})

    assert checked_consumers == ['cdc_crossing_check', 'cdc_async_input_check',
        'clock_domain_reg_crossing_check', 'reset_dependency_check']
    assert len(result) == 4
    assert (project/cdc.NETLIST_REL).is_file()
    assert worker.ENGINES['3'][0] == 'yosys'
    assert not any('NETLIST_MISSING' in p.read_text(errors='replace')
                   for p in (project/'reports/phase2/cdc').glob('*.json'))

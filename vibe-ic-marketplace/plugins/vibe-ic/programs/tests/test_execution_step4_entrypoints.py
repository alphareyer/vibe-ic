"""Fixed simulator entrypoints: real CLI refusals and neutral native output.

Native cases exercise only the component producer. The neutral bundle omits a
professional verification plan, so the canonical coverage consumer must refuse
qualification despite successful native execution and measured coverage bytes.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

import execution_step4_icarus
import execution_step4_verilator


ENTRYPOINTS = [
    pytest.param(execution_step4_icarus, "icarus", id="icarus"),
    pytest.param(execution_step4_verilator, "verilator", id="verilator"),
]


def _invoke(module, tmp_path, *args):
    cp = subprocess.run([sys.executable, module.__file__, *map(str, args)],
                        cwd=tmp_path, capture_output=True, text=True, timeout=180)
    (tmp_path / "entrypoint_stdout.log").write_text(cp.stdout)
    (tmp_path / "entrypoint_stderr.log").write_text(cp.stderr)
    return cp


@pytest.mark.parametrize("module,simulator", ENTRYPOINTS)
def test_entrypoint_requires_both_input_and_output_roots(tmp_path, module, simulator):
    cp = _invoke(module, tmp_path, "--inputs", tmp_path / "input")
    assert cp.returncode == 2
    assert "--inputs and --outputs are required" in cp.stderr
    assert not (tmp_path / "step4_execution.json").exists()


@pytest.mark.parametrize("module,simulator", ENTRYPOINTS)
def test_entrypoint_refuses_an_undeclared_gate(tmp_path, module, simulator):
    cp = _invoke(module, tmp_path, "--gate", "unregistered_gate")
    assert cp.returncode != 0
    assert "unsupported Step 4 gate bridge" in cp.stderr


@pytest.mark.parametrize("module,simulator", ENTRYPOINTS)
def test_entrypoint_preserves_typed_not_applicable_gate_report(tmp_path, module, simulator):
    report = tmp_path / "vacuous.json"
    cp = _invoke(module, tmp_path, "--gate", "vacuous_testbench_check", ".",
                 "--json", report)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert json.loads(report.read_text())["verdict"] == "NOT_APPLICABLE"


def _native_input(tmp_path):
    project = tmp_path / "input_project"
    rtl = project / "phase2/stage1/rtl/neutral_logic.v"
    bundle = project / "phase2/stage1/sim_professional/neutral_logic"
    rtl.parent.mkdir(parents=True)
    bundle.mkdir(parents=True)
    rtl.write_text("module neutral_logic(input [3:0] a, b, input select, output reg [3:0] q);\n"
                   "always @* begin\n"
                   "  if (select) q = a ^ b;\n"
                   "  else q = ~(a ^ b);\n"
                   "end\nendmodule\n")
    makefile = bundle / "Makefile"
    makefile.write_text("TOPLEVEL_LANG = verilog\n"
                        "COCOTB_TOPLEVEL = neutral_logic\n"
                        "COCOTB_TEST_MODULES = tb_neutral_logic\n"
                        "include $(shell cocotb-config --makefiles)/Makefile.sim\n")
    testbench = bundle / "tb_neutral_logic.py"
    testbench.write_text("import cocotb\nfrom cocotb.triggers import Timer\n\n"
                         "@cocotb.test()\nasync def checks_logic(dut):\n"
                         "    for select in (0, 1):\n"
                         "        for a in range(16):\n"
                         "            for b in range(16):\n"
                         "                dut.a.value = a\n"
                         "                dut.b.value = b\n"
                         "                dut.select.value = select\n"
                         "                await Timer(1, unit='ns')\n"
                         "                expected = (a ^ b) if select else (~(a ^ b) & 15)\n"
                         "                assert int(dut.q.value) == expected\n"
                         "    dut._log.info('PROFESSIONAL_TB PASS')\n")
    files = {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in (rtl, makefile, testbench)}
    manifest = project / "input/issued_manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"step_id": "4", "parameters": {"top": "neutral_logic"},
                                    "files": files}))
    return project, rtl


@pytest.mark.parametrize("module,simulator", ENTRYPOINTS)
def test_entrypoint_refuses_changed_source_before_native_execution(tmp_path, module, simulator):
    project, rtl = _native_input(tmp_path)
    rtl.write_text("module neutral_logic; endmodule\n")
    output = tmp_path / "output_project"
    cp = _invoke(module, tmp_path, "--inputs", project, "--outputs", output)
    assert cp.returncode != 0
    assert "issued input mutation: phase2/stage1/rtl/neutral_logic.v" in cp.stderr
    assert not output.exists()


@pytest.mark.skipif(any(shutil.which(tool) is None for tool in
                       ("iverilog", "vvp", "verilator", "make", "cocotb-config")),
                    reason="native simulator/cocotb toolchain is unavailable")
@pytest.mark.parametrize("module,simulator", ENTRYPOINTS)
def test_entrypoint_runs_its_fixed_simulator_on_bound_neutral_sources(tmp_path, module, simulator):
    project, original_rtl = _native_input(tmp_path)
    original_files = {str(path.relative_to(project)): path.read_bytes()
                      for path in project.rglob("*") if path.is_file()}
    output = tmp_path / "output_project"
    cp = _invoke(module, tmp_path, "--inputs", project, "--outputs", output)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    document = json.loads((output / "step4_execution.json").read_text())
    assert document["schema"] == "execution_step4/1"
    assert document["simulator"] == simulator
    functional = document["functional"]
    assert "SIM=" + simulator in functional["argv"]
    assert functional["rc"] == 0
    assert functional["verdict"] == "PASS"
    assert functional["summary"] == {"suite_names": ["tb_neutral_logic"],
                                      "tests": 1, "failures": 0, "errors": 0,
                                      "skipped": 0, "passed": 1}
    junit = Path(functional["junit"])
    assert ET.parse(junit).find(".//testcase").get("name") == "checks_logic"
    assert (output / "phase2/stage1/sim/results.xml").read_bytes() == junit.read_bytes()
    assert "PROFESSIONAL_TB PASS" in Path(functional["log"]).read_text()
    assert document["qualification"] == "CANONICAL_CONSUMER_REQUIRED"
    code_coverage = document["coverage"]
    assert "SIM=verilator" in code_coverage["run"]["argv"]
    assert code_coverage["run"]["verdict"] == "PASS"
    assert (code_coverage["verdict"], code_coverage["reason"]) == (
        "FAIL", "STEP4_CANONICAL_COVERAGE_CHECK")
    assert "PROFESSIONAL_COVERAGE_PLAN_INVALID" in cp.stdout
    measurement = json.loads((output / "reports/phase2/coverage/coverage_verilator.json").read_text())
    dat = Path(measurement["coverage_dat"])
    assert dat.stat().st_size > 0
    assert measurement["coverage_dat_sha256"] == hashlib.sha256(dat.read_bytes()).hexdigest()
    staged_rtl = str(output / "phase2/stage1/rtl/neutral_logic.v")
    assert measurement["rtl_sources"] == [staged_rtl]
    assert measurement["scope_files"]
    assert measurement["totals"]["line"]["covered"] > 0
    gate = json.loads((output / "reports/phase2/gates/professional_tb.json").read_text())
    assert (gate["status"], gate["simulator"], gate["functional_mismatch"]) == ("PASS", simulator, False)
    actual = json.loads((output / "reports/phase2/coverage/coverage_actual.json").read_text())
    assert actual["evidence_sha256"] == hashlib.sha256(junit.read_bytes()).hexdigest()
    assert (actual["verdict"], actual["tests"], actual["simulator"]) == ("PASS", 1, simulator)
    assert original_rtl.read_bytes() == original_files[str(original_rtl.relative_to(project))]
    assert {str(path.relative_to(project)): path.read_bytes()
            for path in project.rglob("*") if path.is_file()} == original_files


def _declared_goal_input(tmp_path):
    # The existing neutral serial arithmetic source/plan producer supplies the
    # same contracts as the normal caller, without borrowing any IC artefact.
    from test_professional_tb_gen import _random_goal_project
    import professional_tb_gen as professional
    project = tmp_path / 'declared_input'
    _random_goal_project(project, 8, '*', 4, True)
    declaration = project / 'plugin_output/declaration.json'
    choices = json.loads(declaration.read_text())
    choices.update(transaction_rearm='clocked_reset', rearm_reset_cycles=1,
                   parallel_hold='through_final_product_bit',
                   verification_stimuli={'arithmetic_scenarios': {
                       'case_3': {'transaction_count': 2}}})
    declaration.write_text(json.dumps(choices))
    l10 = project / 'phase1/generated_docs/L10_TEST_CASES.json'
    document = json.loads(l10.read_text())
    document['test_cases'] += [
        dict(name='corner_operand', kind='coverage_goal', expected='100% PASS',
             coverage_scope='Operand boundaries x=0, y=0, x=MAX, y=MAX, all matching the independent model'),
        dict(name='case_3', kind='coverage_goal', expected='100% PASS',
             coverage_scope='Consecutive transactions of multiplication with internal state reset between transactions'),
        dict(name='reset', kind='coverage_goal', expected='100% PASS',
             coverage_scope='Reset held, reset released, and reset asserted during computation'),
        dict(name='toggle_branch_coverage', kind='coverage_goal', expected='>= 95%',
             stimulus='same random run; not a sign-off gate')]
    l10.write_text(json.dumps(document))
    (project / 'phase1/generated_docs/L12_BEHAVIORAL_SEQUENCES.json').write_text(
        json.dumps({'behavioral_sequences': []}))
    generated = professional.generate(project)
    assert generated['status'] == 'PASS'
    bundle = Path(generated['out_dir'])
    # Freeze sources and plan, not any previous result/JUnit/measurement.
    files = {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in project.rglob('*') if p.is_file()}
    (project / 'input/issued_manifest.json').write_text(json.dumps({
        'step_id': '4', 'parameters': {'top': 'neutral_arithmetic'}, 'files': files}))
    return project, bundle


@pytest.mark.skipif(any(shutil.which(tool) is None for tool in
                       ('iverilog', 'vvp', 'verilator', 'make', 'cocotb-config')),
                    reason='native simulator/cocotb toolchain is unavailable')
@pytest.mark.parametrize('module,simulator', ENTRYPOINTS)
def test_current_arm_publishes_all_declared_goal_evidence(tmp_path, module, simulator):
    import cpu_functional_oracle_waiver_check as goal_gate
    import execution_frontend_providers as providers
    import execution_modes as modes
    import professional_tb_gen as professional
    import _l10_execution as execution
    project, bundle = _declared_goal_input(tmp_path)
    plan = json.loads((bundle / 'verification_plan.json').read_text())['random_goal_plan']
    registry = modes.Registry()
    providers.register_factories(registry, step_ids=('4',), project=project,
        parameters={'top': 'neutral_arithmetic'}, execution_mode='ultra')
    output = tmp_path / 'current_output'
    cp = _invoke(module, tmp_path, '--inputs', project, '--outputs', output)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    report = output / 'current_goal_gate.json'
    rc = goal_gate.main([str(output), '--json', str(report)])
    facts = json.loads(report.read_text())
    native = json.loads((output / 'step4_execution.json').read_text())
    observation = dict(simulator=simulator, actual_goal_rc=rc,
        coverage_goals=facts['coverage_goals'], native=native)
    (tmp_path / 'CURRENT_ARM_GOAL_OBSERVATION.json').write_text(
        json.dumps(observation, indent=2))
    assert rc == 0, facts['message']
    for adapter in registry.adapters('4'):
        assert all(rel in adapter.input_contract for rel in plan['binding'])
    assert facts['coverage_goals']['declared_count'] == 5
    assert facts['coverage_goals']['passed_count'] == 5
    assert native['functional']['verdict'] == 'PASS'
    assert 'SIM=' + simulator in native['functional']['argv']
    assert native['coverage']['verdict'] == 'PASS'
    assert native['complementary_engines'] == {
        'scenarios': 'verilator', 'coverage': 'verilator'}
    measured_plan = json.loads((output / 'phase2/stage1/sim_professional/neutral_arithmetic/verification_plan.json').read_text())['random_goal_plan']
    measurement_path = output / 'phase2/stage1/sim_professional/neutral_arithmetic' / measured_plan['measurement_file']
    measured = json.loads(measurement_path.read_text())
    assert measured['execution']['argv'] == native['functional']['argv']
    assert measured['execution']['returncode'] == 0
    assert measured['executed_random_pairs'] == measured['distinct_random_pairs'] == 8
    assert professional.random_pair_goal_measurement(output,
        json.loads((output / 'phase1/generated_docs/L10_TEST_CASES.json').read_text())['test_cases'][0])['verdict'] == 'PASS'
    per_case = execution.record_path(output)
    saved = per_case.read_bytes()
    per_case.unlink()
    assert goal_gate.main([str(output)]) != 0  # no scenario execution cannot borrow a smoke PASS
    per_case.write_bytes(saved)
    saved = measurement_path.read_bytes()
    measured.pop('execution')
    measurement_path.write_text(json.dumps(measured))
    assert goal_gate.main([str(output)]) != 0  # counts alone cannot borrow an old simulator run
    measurement_path.write_bytes(saved)
    assert goal_gate.main([str(output)]) == 0
    # Issuer and stager share one complete dependency list. Missing multiple
    # antecedents must be reported before a native subprocess can start.
    missing = ['plugin_output/declaration.json', 'phase1/generated_docs/L2_FRS.json']
    for rel in missing:
        (project / rel).unlink()
    from execution_step4 import professional_source_dependencies
    with pytest.raises(ValueError) as refused:
        professional_source_dependencies(project, 'neutral_arithmetic')
    assert all(rel in str(refused.value) for rel in missing)
    early = _invoke(module, tmp_path, '--inputs', project, '--outputs', tmp_path / 'refused_output')
    assert early.returncode != 0
    assert not (tmp_path / 'refused_output').exists()


@pytest.mark.parametrize('gate', [
    'functional_state_transition_coverage_check',
    'behavioral_evidence_per_spec_item_check',
])
def test_declared_step4_na_reaches_validation_without_becoming_pass(tmp_path, monkeypatch, gate):
    """Real gate CLI/proof/Step4 validation; unrelated row aggregation isolated.

    This does not run a simulator or qualify an IC. Core uses the same narrow
    required_gate_satisfied predicate at eligibility and final revalidation.
    """
    from types import SimpleNamespace
    import shlex
    import execution_step4 as step4
    import execution_modes as modes
    import flow_compliance_check as flow
    import _flow_yaml
    whole_flow = _flow_yaml.load()
    contract = next(row for row in whole_flow['steps'] if str(row['id']) == '4')
    command = step4.gate_commands(contract)[gate]
    argv = shlex.split(command)[1:]
    project = tmp_path / 'project'
    declarations = project / 'phase1/generated_docs'
    declarations.mkdir(parents=True)
    (project / 'phase2/stage1/sim/tb').mkdir(parents=True)
    l3 = declarations / 'L3_CMD_PROTOCOL.json'
    l9 = declarations / 'L9_INTEGRATION_SPEC.json'
    l3.write_text(json.dumps({'no_opcodes_in_input': True, 'opcodes': []}))
    l9.write_text(json.dumps({'top_module': 'neutral_logic', 'ports': []}))
    coverage = project / 'reports/phase2/coverage/coverage_actual.json'
    coverage.parent.mkdir(parents=True)
    coverage.write_text('{}')
    report = project / argv[argv.index('--json') + 1]
    monkeypatch.chdir(project)
    # The baseline executes the actual checker and returns its real rc2 here;
    # the failure is substantive, not an unsupported/new helper API.
    bridge_rc = step4.run_gate(gate, argv)
    declared = json.loads(report.read_text())
    (tmp_path / 'TYPED_NA_OBSERVATION.json').write_text(json.dumps({
        'gate': gate, 'bridge_rc': bridge_rc,
        'report': declared, 'command': command}, indent=2))
    assert bridge_rc == 0, 'executed design-declared N/A must cross the process seam'
    assert declared['verdict'] in ('VACUOUS_PASS', 'SKIPPED-CONDITION')
    assert flow._report_proves_executed_design_na(project, declared, command)
    cli = subprocess.run([sys.executable, step4.__file__, '--gate', gate, *argv],
                         cwd=project, capture_output=True, text=True)
    (tmp_path / 'bridge_stdout.log').write_text(cli.stdout)
    (tmp_path / 'bridge_stderr.log').write_text(cli.stderr)
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert 'actual_exit_code=2' in cli.stdout
    assert json.loads(report.read_text()) == declared
    # The native-independent aggregator is the only isolated boundary. Both
    # per-gate invocations and the Default declaration proof below are real.
    marker = project / 'marker.json'; marker.write_text('{}')
    selected = dict(contract, required_outputs=['marker.json'],
                    gate={'program_exit_zero': command})
    monkeypatch.setattr(_flow_yaml, 'load', lambda: dict(whole_flow, steps=[
        selected if str(row['id']) == '4' else row for row in whole_flow['steps']]))
    monkeypatch.setattr(flow, 'check_step', lambda *a, **k:
                        SimpleNamespace(status='PASS', reasons=[]))
    evidence = step4.validate(project, {'step_id': '4'}, (gate,))
    assert evidence.verdict == 'PASS'
    assert evidence.gates == {gate: 'NOT_APPLICABLE'}
    assert modes.required_gate_satisfied('4', gate, evidence.gates[gate])
    assert not modes.required_gate_satisfied('7', gate, 'NOT_APPLICABLE')
    assert not modes.required_gate_satisfied('4', 'coverage_closure', 'NOT_APPLICABLE')
    assert not modes.required_gate_satisfied('4', gate, 'NOT_MEASURED')
    # A real declared population with no execution is still a measured refusal.
    declaration = l3 if gate.startswith('functional_') else l9
    good = declaration.read_bytes()
    declaration.write_text(json.dumps(
        {'no_opcodes_in_input': False, 'opcodes': ['READ']} if gate.startswith('functional_')
        else {'behavioral_requirements': ['read_after_reset']}))
    assert step4.run_gate(gate, argv) == 1
    assert step4.validate(project, {'step_id': '4'}, (gate,)).verdict == 'FAIL'
    assert not flow._report_proves_executed_design_na(project, declared, command)
    declaration.unlink()
    assert step4.run_gate(gate, argv) != 0
    assert step4.validate(project, {'step_id': '4'}, (gate,)).verdict != 'PASS'
    declaration.write_bytes(good)
    assert step4.run_gate(gate, argv) == 0
    # An I/O rc2 cannot reuse the prior valid N/A JSON left at the same path.
    report.write_text(json.dumps(declared))
    invalid = coverage if gate.startswith('functional_') else declaration
    invalid.write_text('{not JSON')
    assert step4.run_gate(gate, argv) == 2
    assert not report.exists()
    report.write_text(json.dumps(declared))
    assert step4.validate(project, {'step_id': '4'}, (gate,)).verdict != 'PASS'

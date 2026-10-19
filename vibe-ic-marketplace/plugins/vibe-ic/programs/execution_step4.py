"""Step 4 simulator arms over one frozen professional cocotb bundle.

Functional simulation is arm-local. Verilator code coverage is a disclosed
complementary measurement, rerun inside each arm; it is never borrowed from
the other candidate. Canonical gates, rather than a simulator rc, qualify it.
"""
# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Restore the standalone-file import condition used by this tree.
import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import _atomic_artefact as atomic
import _sim_results_bridge as results
import verilator_coverage_measure as coverage


def _dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(data, indent=2, sort_keys=True) + '\n')


def professional_source_dependencies(project, top=None):
    """Resolve the retained professional plan's actual source bindings.

    Issuance and arm staging use this same list. A missing or changed
    antecedent cannot be repaired by borrowing an earlier measurement.
    """
    project = Path(project)
    root = project / 'phase2/stage1/sim_professional'
    plans = ([root / top / 'verification_plan.json'] if top else
             sorted(root.glob('*/verification_plan.json')))
    # This bundle is also a producer output directory.  Freeze its source
    # population, not retained JUnit/transcripts/builds that this arm replaces.
    # Non-code support files remain inputs when the plan explicitly binds them.
    dependencies = {
        str(path.relative_to(project)): path
        for path in root.rglob('*')
        if path.is_file() and not any(part.startswith('sim_build') for part in path.relative_to(root).parts)
        and (path.suffix in ('.py', '.sva', '.v', '.sv', '.vh', '.svh')
             or path.name in ('Makefile', 'verification_plan.json'))
    }
    for path in (project / 'phase2/stage1/sim/tb').glob('*'):
        if path.is_file() and path.suffix in ('.v', '.sv'):
            dependencies[str(path.relative_to(project))] = path
    missing, changed = [], []
    for path in plans:
        if not path.is_file():
            continue
        plan = json.loads(path.read_text())
        random_plan = plan.get('random_goal_plan')
        if random_plan is None:
            continue
        binding = random_plan.get('binding') if isinstance(random_plan, dict) else None
        if not isinstance(binding, dict) or not binding:
            raise ValueError('STEP4_PROFESSIONAL_BINDING_INVALID: ' + str(path))
        for rel, expected in binding.items():
            if (not isinstance(rel, str) or Path(rel).is_absolute()
                    or '..' in Path(rel).parts):
                raise ValueError('STEP4_PROFESSIONAL_INPUT_INVALID: ' + str(rel))
            source = project / rel
            dependencies[rel] = source
            if not source.is_file():
                missing.append(rel)
            elif coverage_hash(source) != expected:
                changed.append(rel)
    if missing or changed:
        raise ValueError('STEP4_PROFESSIONAL_INPUT_UNBOUND: ' +
                         json.dumps(dict(missing=sorted(set(missing)),
                                         changed=sorted(set(changed)))))
    return dependencies


def stage(project, output):
    """Copy issued source inputs, never prior simulator measurements/builds."""
    from execution_frontend_worker import _verify_manifest
    manifest = _verify_manifest(project, '4')
    dependencies = professional_source_dependencies(
        project, manifest['parameters'].get('top'))
    omitted = sorted(set(dependencies) - set(manifest['files']))
    if omitted:
        raise ValueError('STEP4_PROFESSIONAL_INPUT_NOT_ISSUED: ' + ', '.join(omitted))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for rel, expected in manifest['files'].items():
        if Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise ValueError('4: frozen source outside project')
        path = Path(project) / rel
        if path.is_symlink() or not path.is_file():
            raise ValueError('4: frozen source missing or nonregular: ' + rel)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('4: frozen source changed: ' + rel)
        # The manifest may include an existing bundle directory. Admit its
        # sources/configuration, but not a previous measurement or binary.
        if not (rel in dependencies or
                rel.startswith(('input/', 'phase1/', 'phase2/stage1/rtl/')) or
                (rel.startswith('phase2/stage1/sim/tb/') and path.suffix in ('.v', '.sv')) or
                ('/sim_professional/' in rel and
                 (path.suffix in ('.py', '.json', '.sva') or path.name == 'Makefile') and
                 not any(part.startswith('sim_build') for part in path.parts))):
            continue
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    return output, manifest['parameters']


def _simulate(bundle, simulator, *, instrument=False, jobs=4, rtl=None):
    if simulator not in ('icarus', 'verilator'):
        raise ValueError('4: unsupported simulator')
    if not 1 <= jobs <= 4:
        raise ValueError('4: build_jobs must be between 1 and 4')
    build = Path(tempfile.mkdtemp(prefix='sim_build_' + simulator + '_', dir=bundle))
    junit = bundle / ('coverage_results.xml' if instrument else 'results.xml')
    dat = bundle / 'coverage.dat'
    junit.unlink(missing_ok=True)
    if instrument:
        dat.unlink(missing_ok=True)
    argv = ['make', '-j' + str(jobs), 'SIM=' + simulator,
            'SIM_BUILD=' + str(build), 'COCOTB_RESULTS_FILE=' + str(junit)]
    if rtl:
        argv += ['VERILOG_SOURCES=' + ' '.join(str(path) for path in rtl)]
    environment = {**os.environ, 'RANDOM_SEED': '1'}
    if instrument:
        environment['COMPILE_ARGS'] = '--coverage --coverage-line --coverage-toggle'
        argv += ['SIM_ARGS=+verilator+coverage+file+' + str(dat)]
    cp = subprocess.run(argv, cwd=bundle, capture_output=True, text=True,
                        env=environment, timeout=1200)
    log = bundle / ('coverage_run.log' if instrument else 'cocotb_run.log')
    atomic.write_text(log, cp.stdout + '\n' + cp.stderr)
    summary = results.parse_junit(junit)
    if summary:
        # Empty JUnit outcome elements are still outcomes. Element truthiness
        # can otherwise let an all-skipped result look like a passing test.
        tree = ET.parse(junit)
        for key, tag in (('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')):
            summary[key] = max(summary[key], len(tree.findall('.//' + tag)))
    clean = bool(cp.returncode == 0 and summary and summary['tests'] > 0 and
                 summary['passed'] > 0 and summary['failures'] == 0 and
                 summary['errors'] == 0 and summary['skipped'] == 0 and
                 'PROFESSIONAL_TB PASS' in cp.stdout + cp.stderr)
    return dict(argv=argv, rc=cp.returncode, junit=str(junit), log=str(log),
                summary=summary, verdict='PASS' if clean else 'FAIL')


def measure_bundle(project, bundle, rtl, *, jobs=4):
    """Use the canonical parser and RTL scope on fresh native coverage bytes."""
    destination = project / 'reports/phase2' / coverage.COVERAGE_MEASUREMENT_REL
    destination.unlink(missing_ok=True)
    if (bundle / 'verification_plan.json').is_file():
        # Reuse the ordinary coverage producer, including its fresh native
        # JUnit/input-activity receipt. Raw coverage totals alone do not qualify.
        captured = {}
        def execute(argv, cwd):
            cp = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                timeout=1200)
            captured.update(argv=argv, rc=cp.returncode)
            return cp.returncode, cp.stdout, cp.stderr
        dat = Path(coverage.verilate_professional_tb_and_run(
            rtl, bundle / ('tb_' + bundle.name + '.py'),
            bundle / 'coverage_build', bundle / 'coverage_runs',
            exec_fn=execute, build_jobs=jobs))
        junit = dat.parent / 'coverage_results.xml'
        run = dict(captured, junit=str(junit),
                   log=str(dat.parent / 'coverage_cocotb.log'),
                   summary=results.parse_junit(junit), verdict='PASS')
    else:
        # Preserve the existing missing-plan component diagnostic. Its raw
        # measurement still fails the unchanged canonical coverage consumer.
        run = _simulate(bundle, 'verilator', instrument=True, jobs=jobs, rtl=rtl)
        dat = bundle / 'coverage.dat'
    if run['verdict'] != 'PASS' or not dat.is_file() or not dat.stat().st_size:
        return dict(verdict='FAIL', reason='STEP4_COVERAGE_RUN_FAILED', run=run)
    parsed = coverage.parse_coverage_dat(str(dat))
    scoped = coverage.scope_totals(parsed, [str(p) for p in rtl])
    if scoped is None:
        return dict(verdict='FAIL', reason='STEP4_COVERAGE_RTL_SCOPE_EMPTY', run=run)
    tb = bundle / ('tb_' + bundle.name + '.py')
    payload = dict(tool='verilator', measurement_mode='measure-cocotb',
                   coverage_dat=str(dat), coverage_dat_sha256=coverage_hash(dat),
                   testbench=str(tb), testbenches=[str(tb)],
                   rtl_sources=[str(p) for p in rtl], totals=scoped['totals'],
                   scope_files=scoped['scope_files'], per_file=parsed['per_file'],
                   format_detected=parsed['format_detected'],
                   path_namespace=parsed['path_namespace'],
                   simulator_run=run)
    _dump(destination, payload)
    # Apply the existing thresholds, with no arm-specific relaxation.
    check = coverage.cmd_check(coverage.build_parser().parse_args(
        ['check', '--coverage-json', str(destination)]))
    return dict(verdict='PASS' if check == 0 else 'FAIL',
                reason='STEP4_CANONICAL_COVERAGE_CHECK', run=run)


def coverage_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def staged_rtl(project):
    """Return the nonempty, regular RTL population in the staged arm."""
    root = Path(project).resolve()
    rtl = sorted(root.joinpath('phase2/stage1/rtl').rglob('*.v'))
    rtl += sorted(root.joinpath('phase2/stage1/rtl').rglob('*.sv'))
    if not rtl:
        raise ValueError('4: staged RTL population is empty')
    for path in rtl:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f'4: staged RTL file is unavailable: {path}') from exc
        if (path.is_symlink() or not path.is_file() or
                not resolved.is_relative_to(root)):
            raise ValueError(f'4: staged RTL file is outside the arm: {path}')
    return rtl


def produce(project, output, simulator, **parameters):
    project, bound = stage(Path(project), Path(output))
    top = bound.get('top') or parameters.get('top')
    if not top or Path(top).name != top:
        raise ValueError('4: a safe top is required')
    bundle = project / 'phase2/stage1/sim_professional' / top
    if not (bundle / 'Makefile').is_file():
        import professional_tb_gen
        generated = professional_tb_gen.generate(project)
        if generated.get('status') != 'PASS':
            raise ValueError('4: professional bundle unavailable')
    rtl = staged_rtl(project)
    import testbench_gen
    scenario_report = {}
    if (project / 'phase1/generated_docs/L10_TEST_CASES.json').is_file():
        testbench_gen.emit_unit_tbs(project, top,
            kind=testbench_gen.DEFAULT_SCAFFOLD_KIND, report=scenario_report)
        testbench_gen.run_unit_tbs(project, None, scenario_report)
    functional = _simulate(bundle, simulator, rtl=rtl)
    plan_path = bundle / 'verification_plan.json'
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text())
        if plan.get('random_goal_plan'):
            import professional_tb_gen
            import _eda_pin
            professional_tb_gen.finalize_random_pair_execution(
                bundle, plan['random_goal_plan'], argv=functional['argv'],
                returncode=functional['rc'], image=_eda_pin.configured_reference())
    # Publish the same arm's actual JUnit bytes at the normal consumer seam.
    # A view is not a second measurement; the source transcript remains bound.
    primary_junit = Path(functional['junit'])
    if primary_junit.is_file():
        sim = project / 'phase2/stage1/sim'
        sim.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(sim / 'results.xml', primary_junit.read_bytes())
    measured = measure_bundle(project, bundle, rtl)
    summary = functional['summary'] or {}
    _dump(project / 'reports/phase2/gates/professional_tb.json',
          dict(status=functional['verdict'], out_dir=str(bundle),
               ran_cocotb=True, cocotb_rc=functional['rc'],
               cocotb_pass_marker=functional['verdict'] == 'PASS',
               functional_mismatch=functional['verdict'] != 'PASS',
               cocotb_test_denominator=summary, simulator=simulator))
    evidence = str(Path(functional['junit']).relative_to(project))
    _dump(project / 'reports/phase2/coverage/coverage_actual.json',
          dict(verdict=functional['verdict'], verification_track='professional_tb',
               evidence=evidence, evidence_sha256=coverage_hash(Path(functional['junit']))
               if Path(functional['junit']).is_file() else None,
               simulator=simulator, tests=summary.get('tests', 0)))
    _dump(project / 'step4_execution.json', dict(schema='execution_step4/1',
          simulator=simulator, functional=functional, coverage=measured,
          scenario_execution=scenario_report,
          complementary_engines=dict(scenarios='verilator', coverage='verilator'),
          shared_checker='cocotb + verilator code coverage',
          qualification='CANONICAL_CONSUMER_REQUIRED'))
    from execution_frontend_providers import _concrete_outputs, CANONICAL_ROWS
    paths = _concrete_outputs(project, CANONICAL_ROWS['4'])
    folder = 'phase2/stage1/4_simulation'
    _dump(project / 'steps' / folder / 'written.json', dict(id='4',
          n_required_outputs=len(CANONICAL_ROWS['4']), n_produced=len(paths),
          produced=[dict(rel=rel, spec=spec, kind='file',
                         size=(project / rel).stat().st_size,
                         mtime=(project / rel).stat().st_mtime)
                    for rel, spec in sorted(paths.items())], findings=[]))
    _dump(project / 'steps/index.json', dict(steps=[dict(id='4',
          name='Simulation', folder=folder)]))
    return project / 'step4_execution.json'


DESIGN_NA_GATES = frozenset((
    'functional_state_transition_coverage_check',
    'behavioral_evidence_per_spec_item_check',
))
GATE_BRIDGES = DESIGN_NA_GATES | {'vacuous_testbench_check'}


def _clear_design_na_receipt(root, gate, argv):
    """These gate-owned receipts must be written by this invocation."""
    if gate not in DESIGN_NA_GATES:
        return None
    try:
        report = Path(argv[argv.index('--json') + 1])
    except (ValueError, IndexError, TypeError):
        return None
    if not report.is_absolute():
        report = Path(root) / report
    report.unlink(missing_ok=True)
    return report


def main(simulator):
    parser = argparse.ArgumentParser()
    parser.add_argument('--gate')
    parser.add_argument('--inputs')
    parser.add_argument('--outputs')
    parser.add_argument('gate_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.gate:
        if args.gate not in GATE_BRIDGES:
            raise SystemExit('unsupported Step 4 gate bridge')
        raise SystemExit(run_gate(args.gate, args.gate_args))
    if not args.inputs or not args.outputs:
        parser.error('--inputs and --outputs are required for a simulator arm')
    produce(args.inputs, args.outputs, simulator)


def run_gate(gate, argv):
    """Run a declared Step 4 gate, translating only typed N/A rc2.

    The Controller treats a component's nonzero rc as PROCESS_ERROR, while
    The existing vacuous bridge and the two design-declared checks use rc2
    for explicit, report-backed non-applicability. Keep that tier visible in the report and make the
    process seam agree with the canonical consumer. A crash/IO_ERROR or a
    measured FAIL keeps its original nonzero status.
    """
    fresh_report = _clear_design_na_receipt(Path.cwd(), gate, argv)
    script = Path(__file__).with_name(gate + '.py')
    cp = subprocess.run([sys.executable, str(script), *argv],
                        cwd=Path.cwd(), capture_output=True, text=True)
    if cp.stdout:
        print(cp.stdout, end='')
    if cp.stderr:
        print(cp.stderr, end='', file=sys.stderr)
    if gate in DESIGN_NA_GATES:
        print(f'STEP4_GATE_EXIT: program={gate} actual_exit_code={cp.returncode}')
        if cp.returncode != 2 or fresh_report is None:
            return cp.returncode
        try:
            document = json.loads(fresh_report.read_text())
        except (OSError, ValueError, TypeError):
            return cp.returncode
        import flow_compliance_check as flow
        command = shlex.join([gate, *argv])
        if (document.get('verdict') != 'FAIL' and
                flow._report_proves_executed_design_na(
                    Path.cwd(), document, command)):
            print(f'STEP4_GATE_APPLICABILITY: program={gate} '
                  'verdict=NOT_APPLICABLE reason_class=DESIGN_DECLARED_NA')
            return 0
        return cp.returncode
    if cp.returncode != 2 or gate != 'vacuous_testbench_check':
        return cp.returncode
    try:
        report_arg = argv.index('--json') + 1
        report = Path(argv[report_arg])
        if not report.is_absolute():
            report = Path.cwd() / report
        document = json.loads(report.read_text())
    except (ValueError, IndexError, OSError, TypeError, json.JSONDecodeError):
        return cp.returncode
    return 0 if document.get('verdict') == 'NOT_APPLICABLE' else cp.returncode


def gate_commands(contract):
    """Use the declared tool-arm consumer interface, including advisories."""
    commands = {}
    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ('program_exit_zero', 'optional_program_exit_zero', 'advisory_program_exit_zero'):
                    command = value if isinstance(value, str) else value['command']
                    commands[command.split()[0]] = command
                else:
                    collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)
    collect(contract.get('gate', {}))
    return commands


def continuation_paths(project):
    """The produced reports read after a selected Step4 arm is imported.

    Source plans/TBs stay inputs. These are the existing executor reports and
    the measurement named by the retained professional plan, not an arbitrary
    copy of its mixed source/output directory. Referenced native run paths in
    these reports still name the retained arm that actually executed them.
    """
    root = Path(project)
    paths = [root / 'reports/phase2/gates/professional_tb.json',
             root / 'reports/phase2/sim/l10_execution.json']
    record = root / 'step4_execution.json'
    executed = json.loads(record.read_text()) if record.is_file() else {}
    top = Path(executed.get('functional', {}).get('junit', '')).parent.name
    plan_path = root / 'phase2/stage1/sim_professional' / top / 'verification_plan.json'
    if top and plan_path.is_file():
        plan = json.loads(plan_path.read_text()).get('random_goal_plan')
        if isinstance(plan, dict) and plan.get('measurement_file'):
            measurement = plan_path.parent / plan['measurement_file']
            if not measurement.resolve().is_relative_to(plan_path.parent.resolve()):
                raise ValueError('4: random measurement outside its bundle')
            paths.append(measurement)
    return tuple(str(path.relative_to(root)) for path in paths)


def continuation_artifacts(project, facts):
    root = Path(project)
    paths = set(continuation_paths(root))
    # The L12 continuation reads generated unit-TB source at the ordinary
    # sim/tb seam. Publish only newly produced files, never replace an issued
    # source TB or the retained professional plan with an arm-local rewrite.
    for path in (root / 'phase2/stage1/sim/tb').glob('tb_*'):
        rel = str(path.relative_to(root))
        if path.suffix in ('.v', '.sv') and rel not in facts['inputs']:
            paths.add(rel)
    return {rel: coverage_hash(root / rel) for rel in sorted(paths)
            if (root / rel).is_file() and not (root / rel).is_symlink()}


def validate(project, facts, gate_names):
    """BLOCKING: canonical Step4 and every mandatory consumer must pass."""
    import _flow_yaml
    import flow_compliance_check as flow
    import execution_modes as em
    from execution_frontend_providers import _concrete_outputs
    root = Path(project)
    contract = next(row for row in _flow_yaml.load()['steps'] if str(row['id']) == '4')
    canonical = flow.check_step(root, contract, {}, strict_step_binding=True)
    commands = gate_commands(contract)
    gates = {}
    for name in gate_names:
        command = commands.get(name)
        if command is None:
            gates[name] = 'NOT_MEASURED'
            continue
        _clear_design_na_receipt(root, name, shlex.split(command)[1:])
        outcome = flow.__check_program_exit_zero(root, command)
        report = flow._command_json_report(root, command)
        structured = flow._report_verdict(report)
        if (name in DESIGN_NA_GATES and outcome.exit_code == 2 and
                structured != 'FAIL' and
                flow._report_proves_executed_design_na(root, report, command)):
            gates[name] = 'NOT_APPLICABLE'
        elif structured in ('NOT_APPLICABLE', 'NOT_MEASURED', 'SKIP', 'SKIPPED'):
            gates[name] = 'NOT_MEASURED'
        elif outcome.passed and outcome.exit_code == 0:
            gates[name] = 'PASS'
        elif outcome.exit_code == 1 or structured == 'FAIL':
            gates[name] = 'FAIL'
        else:
            gates[name] = 'NOT_MEASURED'
    required = tuple(contract.get('required_outputs') or ())
    concrete = _concrete_outputs(root, required)
    outputs = {rel: em.digest(root / rel) for rel in concrete}
    outputs.update(continuation_artifacts(root, facts))
    for path in root.glob('phase2/stage1/sim_professional/*/*'):
        if path.is_file() and not path.is_symlink() and path.suffix in ('.dat', '.log'):
            outputs[str(path.relative_to(root))] = em.digest(path)
    record = root / 'step4_execution.json'
    if record.is_file():
        outputs['step4_execution.json'] = em.digest(record)
    complete = all(any(fnmatch.fnmatch(rel, alt.strip()) for rel in concrete
                      for alt in str(spec).split(' OR ')) for spec in required)
    if canonical.status == 'FAIL' or 'FAIL' in gates.values():
        verdict = 'FAIL'
    elif canonical.status == 'PASS' and complete and gates and all(
            em.required_gate_satisfied('4', gate, value)
            for gate, value in gates.items()):
        verdict = 'PASS'
    else:
        verdict = 'NOT_MEASURED'
    return em.Evidence(facts, verdict, gates, outputs, metrics={'source_boundary': 1.0},
                       detail='Step4 canonical consumers: ' + '; '.join(canonical.reasons))


if __name__ == '__main__':
    # The normal producer entry points are the fixed Icarus/Verilator modules;
    # direct execution is reserved for the typed gate bridge above.
    main(None)

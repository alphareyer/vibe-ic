"""Source-owned ordered F4 production and substantive existing consumers.

Only this worker's isolated project is written by producers/checkers. Physical
steps import external populations and never register a software physical arm.
"""
from __future__ import annotations
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import sys
import tempfile
import time

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_modes as em
from execution_release_rows import ROWS
from execution_adapters_release import (PROGRAMS, EXTERNAL_IDS, alternatives,
                                       matches, write, population)


def call(program, arguments):
    """Execute actual shipped CLI code in process, retaining its raw result."""
    path = PROGRAMS / (program + '.py')
    if not path.is_file():
        raise em.Refusal('RELEASE_PROGRAM_ABSENT', program)
    before = list(sys.argv)
    output, error = io.StringIO(), io.StringIO()
    start = time.time_ns()
    try:
        sys.argv = [str(path), *map(str, arguments)]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            try:
                runpy.run_path(str(path), run_name='__main__')
                rc = 0
            except SystemExit as ended:
                rc = int(ended.code or 0)
    finally:
        sys.argv = before
    return {'program': program, 'argv': [sys.executable, str(path), *map(str, arguments)],
            'pid': os.getpid(), 'cid': None, 'source_sha256': em.digest(path),
            'started_ns': start, 'ended_ns': time.time_ns(), 'rc': rc,
            'stdout': output.getvalue(), 'stderr': error.getvalue()}


def command_args(clause, project):
    args = shlex.split(clause['command'])
    program, args = args[0], args[1:]
    args = [str(project) if value == '.' else value for value in args]
    for flag in ('--json', '--out-dir', '--compliance'):
        if flag in args:
            index = args.index(flag) + 1
            value = Path(args[index])
            if not value.is_absolute():
                args[index] = str(project / value)
    return program, args


def gates(step, project, write_reports=True):
    from execution_adapters_release import gate_clauses
    results = []
    # Gates must not populate producer outputs on the canonical project. Their
    # reports always go into private paths on installed-byte reconsumption.
    with tempfile.TemporaryDirectory(prefix='release-gates-') as tmp:
        for clause in gate_clauses(step):
            conditions = clause.get('condition_files_exist', [])
            if conditions and not all(list(project.glob(p)) for p in conditions):
                results.append({'program': shlex.split(clause['command'])[0],
                                'classification': clause['classification'], 'verdict': 'NOT_APPLICABLE',
                                'reason': clause.get('absent_condition_reason', 'Declared subject absent')})
                continue
            program, args = command_args(clause, project)
            if not write_reports:
                for flag in ('--json',):
                    if flag in args:
                        args[args.index(flag) + 1] = str(Path(tmp) / (str(len(results)) + '.json'))
                # The tapeout document command itself is a producer with a
                # release refusal, so evaluate on a private project elsewhere.
                if program == 'tapeout_docs_gen':
                    args[args.index('--out-dir') + 1] = str(Path(tmp) / 'docs')
            report_path = Path(args[args.index('--json') + 1]) if '--json' in args else None
            try:
                result = call(program, args)
                parsed = json.loads(report_path.read_text()) if report_path and report_path.is_file() else None
                if program == 'flow_step_output_content_check':
                    # This CLI deliberately accepts unknown modes; retain the
                    # actual substantive mode-specific findings separately.
                    import flow_step_output_content_check as content
                    result['content_check'] = content.check(project, args[args.index('--mode') + 1])
                value = parsed.get('verdict', parsed.get('overall')) if isinstance(parsed, dict) else None
                verdict = 'FAIL' if value == 'FAIL' or result['rc'] == 1 else (
                    'PASS' if result['rc'] == 0 and value not in ('WAIVED', 'SKIP', 'NOT_MEASURED', 'NOT_RUN', 'NOT_DETERMINED')
                    else 'NOT_MEASURED')
                if result.get('content_check'):
                    # Findings describe the current producer bytes. A prior
                    # CLI rc0 cannot erase a refusal from that semantic reread.
                    verdict = 'FAIL'
                result.update(verdict=verdict, report=parsed, classification=clause['classification'])
            except (Exception, SystemExit) as exc:
                result = {'program': program, 'argv': [sys.executable, str(PROGRAMS / (program + '.py')), *args],
                          'pid': os.getpid(), 'cid': None, 'rc': None, 'verdict': 'NOT_MEASURED',
                          'reason': str(exc), 'classification': clause['classification']}
            results.append(result)
    return results


def products_complete(step, project):
    missing = []
    products = {}
    for pattern in ROWS[step]['canonical'].get('required_outputs', []):
        paths = [p for alt in alternatives(pattern) for p in project.glob(alt) if p.is_file()]
        if not paths:
            missing.append(pattern)
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(project.resolve()) or not path.stat().st_size:
                missing.append(str(path.relative_to(project)))
            else:
                products[path.relative_to(project).as_posix()] = em.digest(path)
    return products, missing


def validate_products(step, project, parameters, write_reports=True):
    project = Path(project)
    products, missing = products_complete(step, project)
    records = gates(step, project, write_reports)
    grouping = {}
    blocking = []
    for record in records:
        name, verdict = record['program'], record['verdict']
        prior = grouping.get(name, 'PASS')
        grouping[name] = 'FAIL' if 'FAIL' in (prior, verdict) else (
            'NOT_MEASURED' if 'NOT_MEASURED' in (prior, verdict) else 'PASS')
        if record['classification'] != 'advisory_program_exit_zero' and verdict != 'NOT_APPLICABLE':
            blocking.append(verdict)
    substantive = {}
    if step == '37.4' and not missing:
        import signoff_metrics_aggregate as aggregator
        import tapeout_docs_gen as docs
        expected, aggregate_report = aggregator.aggregate(project)
        measured = json.loads((project / 'phase3/final/metrics.json').read_text())
        loaded = docs.load_metrics(project / 'phase3/final/metrics.json')
        if measured != expected or loaded != measured:
            blocking.append('FAIL')
        substantive = {'all_keys': len(aggregator.RULES), 'measured': aggregate_report['measured'],
                       'not_measured': aggregate_report['not_measured'],
                       'release_document_consumer': 'tapeout_docs_gen.load_metrics',
                       'release_document_bytes_equal': loaded == measured,
                       'metrics_sha256': em.digest(project / 'phase3/final/metrics.json')}
    if step == '38' and not missing:
        import foundry_handoff_package_check as handoff
        stale = handoff.stale_layout_members(project)
        if stale:
            blocking.append('FAIL')
        mask = json.loads((project / 'phase3/stage4/foundry_handoff/mask_spec.json').read_text())
        substantive = {'layout_members': mask.get('layout_members'), 'stale_layout_members': stale,
                       'physical_acceptance': 'NOT_MEASURED; package creation does not close foundry-owned items'}
    if step == '37.5ip' and not missing:
        native_record = project / 'reports/phase3/release_native_engines.json'
        try:
            record = json.loads(native_record.read_text())
            if not record['processes'] or any(type(p.get('pid')) is not int or p.get('rc') != 0 or not p.get('ended_ns') for p in record['processes']):
                raise ValueError('No completed actual native engine population')
            if record['source_sha'] != parameters['source_sha']:
                raise ValueError('Wrong native source')
            if not {'magic', 'sta'}.issubset({Path(p['binary']).name for p in record['processes']}):
                raise ValueError('Missing actual Magic/OpenSTA leaf process receipts')
            timing = json.loads((project / 'phase3/stage4/hardmacro/liberty_timing.json').read_text())
            if timing.get('characterised') is not True or not timing.get('arcs'):
                raise ValueError('Uncharacterized timing views do not qualify this native tier')
            for p in record['processes']:
                for channel in ('stdout', 'stderr'):
                    raw = project / em._relative(p[channel])
                    if raw.is_symlink() or em.digest(raw) != p[channel + '_sha256']:
                        raise ValueError('Native raw output identity changed')
            substantive = record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            blocking.append('NOT_MEASURED')
            substantive = {'native': 'NOT_MEASURED', 'reason': str(exc)}
    qualification = 'FAIL' if 'FAIL' in blocking else 'NOT_MEASURED' if missing or 'NOT_MEASURED' in blocking else 'PASS'
    # Mandatory advisory evidence remains advisory. The frozen controller's
    # mandatory list can conservatively make an arm ineligible; F1 must align
    # that list with canonical classifications, never relabel an advisory FAIL.
    for name in ROWS[step]['policy']['mandatory_gate_programs']:
        grouping.setdefault(name, 'NOT_MEASURED')
    design = 'NOT_MEASURED'
    if step in ('36', '37.5ic', '37.5ip') and qualification == 'FAIL':
        design = 'FAIL'
    if step == '37.4' and not missing:
        metrics = json.loads((project / 'phase3/final/metrics.json').read_text())
        counts = [metrics.get(k) for k in ('magic__drc_error__count', 'klayout__drc_error__count',
                                          'klayout__density_error__count', 'design__lvs_error__count')]
        if any(type(value) in (int, float) and value > 0 for value in counts):
            design = 'FAIL'
    return {'qualification': qualification, 'design_verdict': design, 'products': products,
            'missing': missing, 'gates': grouping, 'gate_records': records, 'substantive': substantive}


def produce(step, project, parameters):
    records = []
    if step == '38':
        records.append(call('foundry_handoff_pack_gen', [project, '--top', parameters['top']]))
    elif step == '37.4':
        records.append(call('signoff_metrics_aggregate', [project]))
    elif step == '36':
        records.append(call('tapeout_checklist_gen', [project]))
    elif step == '35':
        records.append(call('dfm_screen_check', [project, '--json', project / 'reports/phase3/dfm_screen.json']))
    elif step == '16':
        # Exact existing callable, no parent runner or full flow recursion.
        import phase3_one_shot_runner as runner
        target = project / 'phase3/stage3/cts/clock_plan.json'
        notes = []
        actual = runner.emit_clock_plan(project, target, project / 'phase3/stage3/pnr/floorplan.def',
                                        project / 'phase3/stage3/pnr', notes)
        records.append({'program': 'phase3_one_shot_runner.emit_clock_plan', 'pid': os.getpid(), 'cid': None,
                        'rc': 0 if actual else 2, 'outputs': [actual] if actual else [], 'notes': notes})
    elif step == '14':
        # An audit consumes the actual upstream engine handoff. It must not
        # synthesize a new candidate or replace the source's mapped netlist.
        records.append({'program': 'upstream synthesis handoff', 'pid': os.getpid(), 'cid': None,
                        'rc': None, 'netlist': 'phase2/stage2/synth/netlist.v'})
        # The existing front door pre-produces this scoped compliance record
        # before Step14 consumes it. Run that exact program/argument contract
        # here as producer work; a final audit's first write cannot stand in
        # for evidence produced by this invocation.
        records.append(call('flow_compliance_check', [project, '--stage-id', 'stage_analog', '--strict',
                       '--json', project / 'reports/analog/stage_analog_compliance.json']))
    elif step == '37.5ic':
        records.append(call('tapeout_precheck', [project, '--json', project / 'reports/phase3/tapeout_precheck.json']))
        # Do not force the documents past a measured precheck refusal.
        if records[-1]['rc'] == 0:
            records.append(call('tapeout_docs_gen', ['--project', project, '--out-dir', project / 'reports/phase3/docs']))
            records.append(call('ic_release_docs_gen', [project]))
    elif step == '37.5ip':
        records.extend(native_hardmacro(project, parameters))
        if records and all(r.get('rc') == 0 for r in records):
            # The source-owned context is required by the existing document
            # consumer and carries the original caller's identities.
            import phase3_one_shot_runner as runner
            context = runner._write_ip_release_docs_context(project, parameters['top'], parameters['pdk'],
                                                          parameters['source_sha'], parameters.get('module_role'))
            records.append(call('ip_release_docs_gen', [project, '--run-context', context.relative_to(project)]))
    return records


def native_hardmacro(project, parameters):
    """Actual full-view producer, never interface-only/timing-uncharacterised.

    This path cannot execute under source-author admission. F1/P0 supplies a
    measured, exact native lease and supported host engine identities. Container
    exec/entrypoint overrides are refused by the native-edge audit hook.
    """
    if not parameters.get('native_admission'):
        raise em.Refusal('RELEASE_NATIVE_NOT_ADMITTED', '37.5ip')
    admission = json.loads(Path(parameters['native_admission']).read_text())
    required = {'source_sha': parameters['source_sha'], 'top': parameters['top'], 'pdk': parameters['pdk'],
                'image': parameters['image'], 'step_id': '37.5ip'}
    if admission.get('identities') != required or admission.get('status') != 'ADMITTED':
        raise em.Refusal('RELEASE_NATIVE_ADMISSION_UNBOUND', '37.5ip')
    if type(admission.get('deadline_epoch_s')) not in (float, int) or time.time() >= admission['deadline_epoch_s']:
        raise em.Refusal('RELEASE_NATIVE_ADMISSION_EXPIRED', '37.5ip')
    if not parameters.get('pdk_root') or not admission.get('exact_input_sha256') or not admission.get('tool_binaries'):
        raise em.Refusal('RELEASE_NATIVE_ADMISSION_INCOMPLETE', '37.5ip')
    if admission['exact_input_sha256'] != parameters.get('input_population_sha256'):
        raise em.Refusal('RELEASE_NATIVE_INPUT_NOT_ADMITTED', '37.5ip')
    pdk_root = project / em._relative(parameters['pdk_root'])
    if not pdk_root.resolve().is_relative_to(project.resolve()) or not pdk_root.is_dir():
        raise em.Refusal('RELEASE_NATIVE_PDK_NOT_FROZEN', str(pdk_root))
    for name, digest in admission['tool_binaries'].items():
        binary = Path(name)
        if not binary.is_file() or binary.is_symlink() or em.digest(binary) != digest:
            raise em.Refusal('RELEASE_NATIVE_BINARY_CHANGED', name)
    import subprocess
    from unittest.mock import patch
    processes = []
    real_popen = subprocess.Popen
    class ObservedProcess(real_popen):
        """Observe the real native process without replacing its execution."""
        def __init__(self, argv, *args, **kwargs):
            self.release_observation = {}
            binary = Path(shutil.which(str(argv[0])) or str(argv[0])).resolve()
            if str(binary) not in admission['tool_binaries'] or em.digest(binary) != admission['tool_binaries'][str(binary)]:
                raise em.Refusal('RELEASE_NATIVE_BINARY_NOT_ADMITTED', str(binary))
            start = time.time_ns()
            super().__init__(argv, *args, **kwargs)
            self.release_observation.update({'argv': list(map(str, argv)), 'pid': self.pid, 'cid': None,
                'started_ns': start, 'rc': None, 'ended_ns': None, 'binary': str(binary),
                'affinity': sorted(os.sched_getaffinity(self.pid)),
                'binary_sha256': em.digest(binary)})
            processes.append(self.release_observation)
        def poll(self):
            rc = super().poll()
            if rc is not None:
                self.release_observation.update(rc=rc, ended_ns=time.time_ns())
            return rc
        def wait(self, timeout=None):
            remaining = max(.01, admission['deadline_epoch_s'] - time.time())
            rc = super().wait(timeout=min(timeout, remaining) if timeout is not None else remaining)
            self.release_observation.update(rc=rc, ended_ns=time.time_ns())
            return rc
        def communicate(self, input=None, timeout=None):
            remaining = max(.01, admission['deadline_epoch_s'] - time.time())
            out, err = super().communicate(input, min(timeout, remaining) if timeout is not None else remaining)
            self.release_observation.update(rc=self.returncode, ended_ns=time.time_ns())
            for channel, data in [('stdout', out), ('stderr', err)]:
                if data is not None:
                    raw = data.encode() if isinstance(data, str) else data
                    path = project / 'reports/phase3/native-release-logs' / (str(self.pid) + '.' + channel)
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
                    self.release_observation[channel] = str(path.relative_to(project))
                    self.release_observation[channel + '_sha256'] = em.digest(path)
            return out, err
    with patch.object(subprocess, 'Popen', ObservedProcess):
        record = call('digital_hardmacro_gen', [project, '--full-lef', '--container', '',
                                             '--pdk-root', pdk_root,
                                             '--json', project / 'reports/phase3/digital_hardmacro.json'])
    native = json.loads((project / 'reports/phase3/digital_hardmacro.json').read_text())
    engines = [p for p in processes if str(Path(shutil.which(p['argv'][0]) or p['argv'][0]).resolve()) in admission['tool_binaries']
               and not any(a in ('--version', '-version', '-v') for a in p['argv'][1:])]
    if not engines or any(p['rc'] != 0 or not p['ended_ns'] for p in engines):
        raise em.Refusal('RELEASE_NATIVE_PROCESS_RECEIPTS_MISSING', 'No complete admitted actual native engine population')
    timing = json.loads((project / 'phase3/stage4/hardmacro/liberty_timing.json').read_text())
    # The original generator/checker owns Liberty substance. Uncharacterised
    # interface-only outcomes can never satisfy the native full-view tier.
    if native.get('status') != 'PRODUCED' or timing.get('characterised') is not True or not timing.get('arcs'):
        raise em.Refusal('RELEASE_NATIVE_VIEWS_NOT_CHARACTERIZED', '37.5ip')
    if not {'magic', 'sta'}.issubset({Path(p['binary']).name for p in engines}):
        raise em.Refusal('RELEASE_NATIVE_LEAF_OBSERVATION_REQUIRED',
                         'Current producer shell-wraps OpenSTA; F1/P0 must supply actual leaf PID/raw rc supervision')
    for name, digest in admission['tool_binaries'].items():
        if em.digest(Path(name)) != digest:
            raise em.Refusal('RELEASE_NATIVE_BINARY_CHANGED', name)
    write(project / 'reports/phase3/release_native_engines.json',
          {'source_sha': parameters['source_sha'], 'processes': engines, 'producer': native,
           'admission_sha256': em.digest(Path(parameters['native_admission']))})
    return [record]


def external_handoff(context):
    """Current typed external populations plus actual original check semantics.

    A receipt names its immutable raw files and exact upstream subject. All
    those files, the envelope and their lexical aliases are context inputs.
    Missing evidence never becomes physical PASS or factual inapplicability.
    """
    step = context.step_id
    parameters = json.loads(context.parameters_at_prepare)
    bound = context.binding()
    outputs = ROWS[step]['canonical']['required_outputs']
    missing = []
    for pattern in outputs:
        if not any(list(context.project.glob(p)) for p in alternatives(pattern)):
            missing.append(pattern)
    receipts, diagnostics = [], []
    raw_rows = parameters.get('external_receipts', [])
    if not isinstance(raw_rows, list):
        raise em.Refusal('RELEASE_EXTERNAL_POPULATION_INVALID', step)
    for relative in raw_rows:
        try:
            path = context.project / em._relative(relative)
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(context.project):
                raise ValueError('receipt path unsafe')
            doc = json.loads(path.read_text())
            identity = {'source_sha': context.source_sha, 'top': parameters['top'],
                        'pdk': parameters['pdk'], 'route': parameters['route'], 'image': parameters['image']}
            if doc.get('schema') != 'vibeic.release.external.v1' or doc.get('step_id') != step or doc.get('identities') != identity:
                raise ValueError('receipt identity differs')
            if not isinstance(doc.get('measurement_id'), str) or not doc['measurement_id'].strip() or not doc.get('issuer'):
                raise ValueError('physical measurement identity/issuer absent')
            files = doc.get('raw_files')
            if not isinstance(files, dict) or not files:
                raise ValueError('no complete raw physical population')
            for name, digest in files.items():
                raw = context.project / em._relative(name)
                if not isinstance(digest, str) or not raw.is_file() or raw.is_symlink() or not raw.resolve().is_relative_to(context.project) or em.digest(raw) != digest:
                    raise ValueError('raw physical file changed: ' + name)
            subject = doc.get('subject_inputs')
            if not isinstance(subject, dict) or not subject:
                raise ValueError('no upstream subject identity')
            for name, digest in subject.items():
                value = bound['inputs'].get('project/' + name)
                if not isinstance(digest, str) or value is None or value != digest:
                    raise ValueError('upstream subject differs: ' + name)
            if doc.get('verdict') not in ('PASS', 'FAIL', 'NOT_MEASURED'):
                raise ValueError('unsupported receipt verdict')
            receipts.append({'path': relative, 'sha256': em.digest(path), 'document': doc})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            diagnostics.append({'path': relative, 'reason': str(exc)})
    # A current receipt population must bind every upstream consumed file,
    # not an arbitrary convenient subset. Raw physical output populations and
    # the envelopes themselves are independently hashed above, avoiding a
    # recursive self-hash. Independently valid measured FAIL stays visible even
    # if another receipt population is malformed or short.
    raw_names = {name for row in receipts for name in row['document']['raw_files']}
    from execution_adapters_release import gate_outputs
    upstream = {name.removeprefix('project/'): digest for name, digest in bound['inputs'].items()
                if name.startswith('project/') and name.removeprefix('project/') not in raw_names
                and name.removeprefix('project/') not in raw_rows
                and not any(matches(name.removeprefix('project/'), p) for p in gate_outputs(step))}
    for row in receipts:
        if row['document']['subject_inputs'] != upstream:
            diagnostics.append({'path': row['path'], 'reason': 'Complete upstream subject population differs'})
    # Record independently checked bound FAIL before population completeness:
    # it cannot become NOT_MEASURED because a separate member is missing.
    # Refuse the invalid population without discarding independently bound FAIL.
    measured_fail = any(r['document']['verdict'] == 'FAIL' and r['document']['subject_inputs'] == upstream for r in receipts)
    with tempfile.TemporaryDirectory(prefix='external-release-', dir=context.project.parent) as tmp:
        project = Path(tmp) / context.project.name
        project.mkdir()
        for name, path in context.inputs.items():
            if name.startswith('project/'):
                target = project / name.removeprefix('project/')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        checked = gates(step, project)
        # Independently reject malformed present raw output JSON even when a
        # sibling is missing and the legacy intake checker reports SKIP.
        for pattern in outputs:
            for alt in alternatives(pattern):
                for path in project.glob(alt):
                    if path.is_file() and path.suffix == '.json':
                        try:
                            doc = json.loads(path.read_text())
                            if not isinstance(doc, dict) or not doc:
                                raise ValueError('empty/nonobject physical data')
                        except (OSError, ValueError) as exc:
                            diagnostics.append({'path': path.relative_to(project).as_posix(), 'reason': str(exc)})
        gate_fail = any(r['verdict'] == 'FAIL' for r in checked)
    complete = not missing and not diagnostics and bool(receipts)
    actual_outputs = {p.relative_to(context.project).as_posix()
                      for pattern in outputs for alt in alternatives(pattern)
                      for p in context.project.glob(alt) if p.is_file()}
    complete = complete and actual_outputs.issubset(raw_names)
    current_verdict = 'FAIL' if measured_fail else 'NOT_MEASURED'
    if complete and all(r['document']['verdict'] == 'PASS' for r in receipts) and all(
            r['verdict'] == 'PASS' for r in checked if r['classification'] != 'advisory_program_exit_zero'):
        current_verdict = 'EXTERNAL_PASS_ATTESTED'
    elif complete and gate_fail:
        current_verdict = 'FAIL'
    context.binding()
    return {'mode': 'physical_external_handoff', 'binding': bound, 'step_id': step,
            'design_verdict': current_verdict, 'missing_obligations': missing + ([] if receipts else ['current typed physical receipts']),
            'receipts': receipts, 'invalid_receipts_or_materials': diagnostics, 'existing_checkers': checked,
            'condition': ROWS[step]['canonical'].get('condition'),
            'declaration': bound['inputs'].get('project/input/submission_template/tapeout_declaration.json'),
            'native_processes_launched': 0, 'software_physical_pass': False,
            'reason': 'External physical measurement/acceptance remains explicitly external; current receipts are validated without synthesizing manufacturing data'}


def main():
    inputs, outputs = map(Path, sys.argv[1:])
    request = json.loads((inputs / 'release-request.json').read_text())
    step = request['step_id']
    parameters = dict(request['parameters'], source_sha=request['source_sha'],
                      input_population_sha256=em._hash(request['population']))
    if parameters.get('native_admission'):
        parameters['native_admission'] = str(inputs / 'native-admission.json')
    for name, digest in request['sources'].items():
        if not Path(name).is_file() or em.digest(Path(name)) != digest:
            raise em.Refusal('RELEASE_WORKER_SOURCE_CHANGED', name)

    def native_boundary(event, args):
        if event in ('os.system', 'os.posix_spawn', 'os.posix_spawnp'):
            raise em.Refusal('RELEASE_UNSUPERVISED_NATIVE', event)
        if event == 'subprocess.Popen':
            argv = args[1]
            if not isinstance(argv, (list, tuple)) or not argv:
                raise em.Refusal('RELEASE_UNSUPERVISED_NATIVE', repr(argv))
            binary = Path(shutil.which(str(argv[0])) or str(argv[0])).resolve()
            # Existing source-owned Python secondary gates may run. Engine
            # calls require a separate exact measured native admission.
            if binary == Path(sys.executable).resolve() and len(argv) > 1 and Path(str(argv[1])).is_file() and Path(str(argv[1])).resolve().parent == PROGRAMS:
                return
            if Path(str(argv[0])).name == 'git' and len(argv) == 6 and list(argv[1:2]) == ['-C'] and list(argv[3:]) == ['ls-files', '-s', '-z']:
                if str(binary) in request['sources'] and em.digest(binary) == request['sources'][str(binary)]:
                    return
            if not parameters.get('native_admission'):
                raise em.Refusal('RELEASE_NATIVE_NOT_ADMITTED', repr(argv))
            if Path(str(argv[0])).name == 'docker' and ('exec' in argv or '--entrypoint' in argv):
                raise em.Refusal('RELEASE_NATIVE_UNSUPPORTED_TRANSPORT', repr(argv))
            admission = json.loads(Path(parameters['native_admission']).read_text())
            if time.time() >= admission.get('deadline_epoch_s', 0):
                raise em.Refusal('RELEASE_NATIVE_ADMISSION_EXPIRED', repr(argv))
            if str(binary) not in admission.get('tool_binaries', {}) or em.digest(binary) != admission['tool_binaries'][str(binary)]:
                raise em.Refusal('RELEASE_NATIVE_BINARY_NOT_ADMITTED', str(binary))
            if list(map(str, argv)) not in admission.get('allowed_argv', []):
                raise em.Refusal('RELEASE_NATIVE_ARGV_NOT_ADMITTED', repr(argv))
    sys.addaudithook(native_boundary)
    project = outputs / 'work' / request['project_name']
    shutil.copytree(inputs / 'project', project)
    producer = produce(step, project, parameters)
    consumer = validate_products(step, project, parameters)
    products = consumer['products']
    # Include all generated package members, manifests and checker reports so
    # adoption cannot select a partial or wrong output population.
    from execution_adapters_release import output_patterns
    for path in project.rglob('*'):
        if path.is_file() and any(matches(path.relative_to(project).as_posix(), p) for p in output_patterns(step)):
            products[path.relative_to(project).as_posix()] = em.digest(path)
    for name, expected in products.items():
        target = outputs / 'artifacts' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / name, target)
        if em.digest(target) != expected:
            raise em.Refusal('RELEASE_PRODUCT_COPY_CHANGED', name)
    report = {'step_id': step, 'source_sha': request['source_sha'], 'qualification': consumer['qualification'],
              'design_verdict': consumer['design_verdict'], 'gates': consumer['gates'], 'products': products,
              'producer': producer, 'consumer': consumer, 'pid': os.getpid(), 'cid': None,
              'input_population': request['population'], 'sources': request['sources'],
              'harvest': ROWS[step]['harvest'], 'physical_measurement': 'NOT_MEASURED'}
    write(outputs / 'release-evidence.json', report)
    # Protocol completion is separate from the substantive consumer verdict.
    # The controller always revalidates this report and refuses a gate FAIL/NM.
    return 0


if __name__ == '__main__':
    sys.exit(main())

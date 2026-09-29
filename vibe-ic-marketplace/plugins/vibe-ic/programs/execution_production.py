"""First production executor: existing LibreLane mapped synthesis (9), handoff (14).

The public 70-step portfolio is descriptive. Only step 9 has a producer here;
14's native synthesis checkers are consumed inside that producer. Other native
paths remain owned by their current runners, with NOT_IMPLEMENTED mode coverage.
No subprocess is allowed to issue adoption authority; run/choose/adopt/import
belong to the same live parent. Runtime qualification is measured on the host.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from _atomic_artefact import write_json
import execution_modes as em
import execution_policy as policy
from verdict import ReasonClass
from execution_resource_lease import host_lease, native_boundary

PROGRAMS = Path(__file__).resolve().parent
PROTECTED = {
    'W1A': '8ab5be061e27a6df3623e64301a7dfa3cfa4258c',
    'FRONT': '1c97185fdb4035817f4e63daa2ee5157de1b576b',
    'CUT7': '2c14403dcebe99230e012396a880701a758fb305',
    'CUT13': 'f3662b1f1797d7b699774feeffc0bf48306c8751',
    'CUT19': 'ddb64a49cc283aad9a8282a0a44d71addea65daf',
}
EXTRA_GATES = ('synth_handoff_netlist_check', 'native_synthesis_stat',
               'native_synthesis_checkers')


def catalog() -> dict:
    rows = []
    for row in em.load_portfolio()['steps']:
        executable = row['id'] == '9'
        rows.append(dict(id=row['id'], programs=row['current_default']['source']['programs'],
                         status='IMPLEMENTED_UNQUALIFIED' if executable else 'NOT_IMPLEMENTED',
                         executor='phase3_one_shot_runner._step_synth_librelane' if executable else None,
                         engine_families=['yosys', 'abc'] if executable else [],
                         runtime='NOT_MEASURED', native_producer_preserved=True,
                         scope=('mapped synthesis plus native handoff checkers; primary gates retained'
                                if executable else 'native handoff checker consumed by 9; full 14 policy pending'
                                if row['id'] == '14' else 'current native path; no execution-mode adapter'),
                         mandatory_gates=row['mandatory_gate_programs']))
    return dict(schema=1, steps=rows, count=len(rows), implemented_producers=1,
                runnable_measured=0, authority='one-live-parent-interpreter',
                resource_scope='exclusive cooperating host dispatch; owned Docker run only',
                license_executors='NOT_IMPLEMENTED', protected_prerequisites=PROTECTED,
                harvest='native dual obligations remain; no fork deletion authorized')


def source_identity() -> tuple[str, dict[str, str]]:
    cp = subprocess.run(['git', '-C', str(PROGRAMS), 'rev-parse', 'HEAD'],
                        capture_output=True, text=True, timeout=10)
    if cp.returncode:
        raise em.Refusal('PRODUCTION_SOURCE_NOT_VERSIONED', cp.stderr[-300:])
    files = {str(p.resolve()): em.digest(p) for p in PROGRAMS.rglob('*')
             if p.is_file() and not p.is_symlink() and
             not {'tests', 'benchmark', 'harness', '__pycache__'}.intersection(p.relative_to(PROGRAMS).parts) and
             p.suffix in ('.py', '.json', '.yaml', '.yml', '.tcl', '.sh', '.js', '.mjs', '.toml')}
    for p in (PROGRAMS / 'data/execution_modes_portfolio.json',
              PROGRAMS.parent / 'flow/phase1_phase2_phase3.yaml'):
        files[str(p.resolve())] = em.digest(p)
    binary = Path(sys.executable).resolve()
    files[str(binary)] = em.digest(binary)
    return cp.stdout.strip(), files


def _tree(inputs: dict[str, Path], prefix: str, root: Path) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob('*')):
        if path.is_file():
            # Resolve PDK links to regular immutable byte inputs. Preserve the
            # original alias name; an external escape is refused, not mounted.
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path))
            inputs[prefix + '/' + path.relative_to(root).as_posix()] = resolved


def prepare(project: Path, top: str, pdk, lease: Path, record: Path):
    import librelane_contract as lc
    import librelane_image_facts as facts
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', top):
        raise em.Refusal('PRODUCTION_TOP_PATH_UNSAFE', repr(top))
    switch = project / 'phase3/librelane_switch.json'
    switch_data = json.loads(switch.read_text()) if switch.is_file() else {}
    legacy_mode = lc.selected_mode(project, '9')
    layer = lc.impl_step_modes(project)
    if (switch_data.get('steps', {}).get('9') == 'direct' or (layer or {}).get('9') == 'direct'):
        raise em.Refusal('PRODUCTION_EXPLICIT_DIRECT_NOT_IMPLEMENTED', str(project))
    image = lc.resolve_image(project)
    measured = facts.image_facts(image)
    image_id = measured.get('image_id', '')
    if not image_id.startswith('sha256:') or not measured.get('librelane_version'):
        raise em.Refusal('PRODUCTION_IMAGE_UNQUALIFIED', image)
    native_steps = ('Yosys.JsonHeader', 'Yosys.Synthesis', 'Checker.YosysUnmappedCells',
                    'Checker.YosysSynthChecks', 'Checker.NetlistAssignStatements')
    available_steps = set(facts.flow_steps(measured, 'Chip'))
    if not set(native_steps).issubset(available_steps):
        raise em.Refusal('PRODUCTION_NATIVE_STEPS_UNAVAILABLE', image_id)
    # Existing PDK image materialization starts separate copy containers. It
    # has no production lease here, so require an already-declared host root.
    if not (switch_data.get('pdk_root_host') or os.environ.get('VIBEIC_LIBRELANE_PDK_ROOT')):
        raise em.Refusal('PRODUCTION_PDK_MATERIALIZATION_NOT_IMPLEMENTED', str(project))
    native_probe = ['docker', 'run', '--rm', '--entrypoint', 'yosys', image_id, '-V']
    checked = lc.run_container(native_probe, probe_deadline_s=60)
    if checked.returncode or not checked.stdout.strip():
        raise em.Refusal('PRODUCTION_YOSYS_UNAVAILABLE', f'rc={checked.returncode}')
    measured = {**measured, 'native_yosys': dict(argv=native_probe, rc=checked.returncode,
                    stdout=checked.stdout, stderr=checked.stderr)}
    root = Path(lc.pdk_root_resolution(project, pdk.name, image=image_id)['path']).resolve()
    if not root.is_dir() or not Path(pdk.liberty).is_file():
        raise em.Refusal('PRODUCTION_HOST_PDK_NOT_MEASURED', str(root))
    if switch.is_file() and json.loads(switch.read_text()).get('development_librelane_source'):
        raise em.Refusal('PRODUCTION_FORK_MOUNT_UNQUALIFIED', str(switch))
    sha, sources = source_identity()
    inputs: dict[str, Path] = {}
    inputs['lease.json'] = lease / 'lease.json'
    for rel in ('input', 'phase1/generated_docs', 'phase2/stage1/rtl',
                'phase2/stage2/constraints', 'pdk_local'):
        _tree(inputs, 'project/' + rel, project / rel)
    _tree(inputs, 'pdk', root)
    if not any(k.startswith('project/phase2/stage1/rtl/') for k in inputs):
        raise em.Refusal('PRODUCTION_RTL_INPUT_MISSING', str(project))
    for rel in ('waivers.json', 'SOURCE_MANIFEST.md', '.vibeic-state/impl-mode-v1.json',
                'phase3/librelane_switch.json'):
        path = project / rel
        if path.is_file():
            if path.is_symlink():
                raise em.Refusal('PRODUCTION_INPUT_LINK_ESCAPE', str(path))
            inputs['project/' + rel] = path
    objective = dict(metric='mapped_area_um2', direction='min', top=top)
    row = next(s for s in em.load_portfolio()['steps'] if s['id'] == '9')
    spec = dict(top=top, pdk=asdict(pdk), original_project=str(project),
                original_pdk_root=str(root), image_id=image_id, lease=str(lease),
                legacy_native_mode=legacy_mode, selected_native_mode='librelane',
                source_sha=sha, source_files=sources, qualification=measured,
                input_hashes={k: em.digest(v) for k, v in inputs.items()})
    write_json(record, spec)
    inputs['request.json'] = record
    context = em.Context('9', sha, inputs, objective,
                         tuple(dict.fromkeys(row['mandatory_gate_programs'] + list(EXTRA_GATES))),
                         native_mode='librelane')
    quota = json.loads((lease / 'lease.json').read_text())
    netlist = f'project/phase2/stage2/synth/{top}_synth.v'
    stats = 'project/phase2/stage2/synth/stats.json'
    adapter = em.Adapter(
        'librelane-mapped-synthesis', 'librelane', '9', sha, sources, image_id,
        ('yosys', 'abc'), (em.Component('native-mapped-synthesis',
        (str(Path(sys.executable).resolve()), str(PROGRAMS / 'execution_native_worker.py'),
         '--inputs', '{inputs}', '--outputs', '{outputs}'), timeout_s=7200),),
        validate_synthesis, (netlist, stats, 'producer.json', 'native_commands.jsonl'), objective,
        qualification_evidence=json.dumps(measured, sort_keys=True),
        cpus=quota['cpus'], ram_mb=quota['host_ram_mb'],
        output_contract={name: (netlist,) if 'netlist' in name else (stats,)
                         for name in row['required_output_contract']})
    registry = em.Registry()
    registry.register(adapter)
    return context, registry


def gate_verdict(name: str, rc: int, report: dict) -> str:
    """Consume native report semantics: rc0 alone cannot establish a gate."""
    if rc == 1:
        return 'FAIL'
    if rc != 0:
        return 'NOT_MEASURED'
    if name == 'provenance_check':
        if report.get('ok') is not True:
            return 'FAIL' if report.get('ok') is False else 'NOT_MEASURED'
        checks = report.get('checks') or []
        if any(check.get('status') == 'FAIL' for check in checks):
            return 'FAIL'
        return ('NOT_MEASURED' if report.get('unmeasured') or report.get('uncalibrated')
                or not checks or any(check.get('status') != 'PASS' for check in checks) else 'PASS')
    verdict = report.get('verdict')
    if verdict in ('PASS', 'FAIL'):
        return verdict
    if report.get('applicable') is False:
        return 'NOT_MEASURED'
    passed = report.get('summary', {}).get('pass')
    return 'PASS' if passed is True else 'FAIL' if passed is False else 'NOT_MEASURED'


def _gate(name: str, argv: list[str], directory: Path) -> str:
    report_path = directory / (name + '.json')
    command = [str(Path(sys.executable).resolve()), str(PROGRAMS / (name + '.py')),
               *argv, '--json', str(report_path)]
    try:
        cp = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        write_json(directory / (name + '.receipt.json'), dict(argv=command, rc=None,
            verdict='NOT_MEASURED', detail=str(exc), program_sha256=em.digest(PROGRAMS / (name + '.py'))))
        return 'NOT_MEASURED'
    try:
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    except ValueError:
        report = {}
    verdict = gate_verdict(name, cp.returncode, report)
    write_json(directory / (name + '.receipt.json'), dict(argv=command, rc=cp.returncode,
        verdict=verdict, program_sha256=em.digest(PROGRAMS / (name + '.py')),
        report_sha256=em.digest(report_path) if report_path.is_file() else None,
        stdout=cp.stdout, stderr=cp.stderr))
    return verdict


def validate_synthesis(outputs: Path, binding: dict) -> em.Evidence:
    """Fresh primary consumers on actual worker outputs; never a cached PASS."""
    outputs = Path(outputs)
    result_path = outputs / 'producer.json'
    gates = {name: 'NOT_MEASURED' for name in binding['required_gates']}
    if not result_path.is_file():
        return em.Evidence(binding, 'NOT_MEASURED', gates, {}, detail='producer receipt missing')
    producer = json.loads(result_path.read_text())
    if producer.get('binding') != binding:
        raise em.Refusal('PRODUCTION_WORKER_BINDING_CHANGED', str(result_path))
    if producer.get('input_hashes') != {k: v for k, v in binding['inputs'].items() if k != 'request.json'}:
        raise em.Refusal('PRODUCTION_WORKER_INPUT_MANIFEST_UNBOUND', str(result_path))
    hashes = {p.relative_to(outputs).as_posix(): em.digest(p) for p in outputs.rglob('*')
              if p.is_file() and (p == result_path or p == outputs / 'native_commands.jsonl' or
                  p.is_relative_to(outputs / 'project/phase3') or
                  p.is_relative_to(outputs / 'project/phase2/stage2/synth') or
                  p == outputs / 'project/provenance.jsonl')}
    for path in outputs.rglob('*'):
        if path.is_symlink():
            raise em.Refusal('PRODUCTION_OUTPUT_LINK', str(path))
    for name, expected in producer.get('input_hashes', {}).items():
        actual = outputs / name if name.startswith('project/') and name != 'project/phase3/librelane_switch.json' \
                 else outputs.parent / 'inputs' / name
        if not actual.is_file() or em.digest(actual) != expected:
            raise em.Refusal('PRODUCTION_WORKER_INPUT_CHANGED', name)
    status = producer.get('status')
    if status != 'PASS':
        # This is the actual existing native producer's failure/nonmeasurement.
        return em.Evidence(binding, 'FAIL' if status == 'FAIL' else 'NOT_MEASURED',
                           gates, hashes, detail=producer.get('detail', 'native producer not measured'))
    spec = json.loads((outputs.parent / 'inputs/request.json').read_text())
    project = outputs / 'project'
    original_switch = outputs.parent / 'inputs/project/phase3/librelane_switch.json'
    derived_switch = json.loads(original_switch.read_text()) if original_switch.is_file() else {}
    derived_switch.update(image=spec['image_id'], pdk_root_host=str(outputs.parent / 'inputs/pdk'))
    if json.loads((project / 'phase3/librelane_switch.json').read_text()) != derived_switch:
        raise em.Refusal('PRODUCTION_DERIVED_CONFIG_CHANGED', str(project))
    mapped = project / f"phase2/stage2/synth/{spec['top']}_synth.v"
    native_folder = Path(producer['native_folder'])
    if not native_folder.resolve().is_relative_to(project / 'phase3/librelane'):
        raise em.Refusal('PRODUCTION_NATIVE_FOLDER_UNBOUND', str(native_folder))
    state = json.loads((native_folder / 'state_out.json').read_text())
    native = Path(state['nl'])
    if not native.resolve().is_relative_to(native_folder) or not native.is_file():
        raise em.Refusal('PRODUCTION_NATIVE_NETLIST_UNBOUND', str(native))
    from _rtl_include_hub import silicon_rtl_selection
    rtl = silicon_rtl_selection(project / 'phase2/stage1/rtl')
    pdk = producer['pdk']
    directory = outputs.parent / 'validations' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    gates['synth_netlist_check'] = _gate('synth_netlist_check',
        ['--netlist', str(mapped), '--tool-netlist', str(native), '--rtl', *map(str, rtl)], directory)
    gates['area_total_vs_budget_check'] = _gate('area_total_vs_budget_check',
        [str(project), '--library', pdk['liberty']], directory)
    gates['pdk_consistency_check'] = _gate('pdk_consistency_check',
        ['--netlist', str(mapped), '--pdk-lib', pdk['liberty']], directory)
    gates['provenance_check'] = _gate('provenance_check',
        [str(project), '--output', str(mapped.relative_to(project)), '--tool', 'yosys', '--require-measured'], directory)
    gates['synth_handoff_netlist_check'] = _gate('synth_handoff_netlist_check',
        ['--netlist', str(native), '--resolved', str(native_folder / 'config.json')], directory)
    if 'FAIL' in gates.values():
        return em.Evidence(binding, 'FAIL', gates, hashes, detail='primary gate FAIL')
    import librelane_contract as lc
    try:
        lc.verify_synthesis_stat(native_folder / 'reports/stat.json', state,
                                 mapped.parent / 'stats.json', spec['top'], directory / 'stat.json')
        gates['native_synthesis_stat'] = 'PASS'  # only after the actual consumer returns
    except lc.Refusal as exc:
        gates['native_synthesis_stat'] = 'FAIL'
        write_json(directory / 'stat-refusal.json', dict(reason=exc.code, detail=str(exc)))
        return em.Evidence(binding, 'FAIL', gates, hashes, detail=str(exc))
    # All three existing source-owned checkers have actual rc/finished-state
    # receipts; state metrics remain required independently of their rc.
    checkers = producer.get('native_checkers', [])
    required = {'Checker.YosysUnmappedCells', 'Checker.YosysSynthChecks', 'Checker.NetlistAssignStatements'}
    seen = set()
    for folder_name in checkers:
        folder = Path(folder_name)
        if not folder.resolve().is_relative_to(project / 'phase3/librelane'):
            raise em.Refusal('PRODUCTION_CHECKER_FOLDER_UNBOUND', folder_name)
        receipt = json.loads((folder / 'vibeic_receipt.json').read_text())
        seen.add(receipt['input']['step'])
        for name, expected in receipt['sha256'].items():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise em.Refusal('PRODUCTION_CHECKER_PATH_UNSAFE', name)
            if em.digest(folder / name) != expected:
                raise em.Refusal('PRODUCTION_CHECKER_RECEIPT_CHANGED', str(folder))
    if seen == required and state.get('metrics', {}).get('design__instance_unmapped__count') == 0 and \
            state.get('metrics', {}).get('synthesis__check_error__count') == 0:
        gates['native_synthesis_checkers'] = 'PASS'
    verdict = 'FAIL' if 'FAIL' in gates.values() else 'PASS' if set(gates.values()) == {'PASS'} else 'NOT_MEASURED'
    stat = json.loads((mapped.parent / 'stats.json').read_text())
    area = stat.get('chip_area')
    metrics = {'mapped_area_um2': area} if type(area) in (int, float) else {}
    return em.Evidence(binding, verdict, gates, hashes, metrics)


def _choice(path: str | None, wait_s: int) -> dict | None:
    if not path:
        return None
    target = Path(path)
    deadline = time.monotonic() + wait_s
    while not target.is_file() and time.monotonic() < deadline:
        time.sleep(.1)
    if target.is_symlink():
        raise em.Refusal('PRODUCTION_CHOICE_LINK', str(target))
    return json.loads(target.read_text()) if target.is_file() else None


def import_selected(project: Path, context: em.Context, controller: em.Controller,
                    run: Path, adopted: dict) -> dict:
    """Copy the issued generation under a rollback journal, then primary gates."""
    import librelane_contract as lc
    import librelane_import as importer
    from phase3_one_shot_runner import _write_synth_inputs_sidecar
    generation = adopted['selected_generation']
    controller._generation_current(generation)
    selected = Path(generation['directory'])
    producer = json.loads((selected / 'producer.json').read_text())
    spec = json.loads((run / adopted['selected'] / 'inputs/request.json').read_text())
    original_output = run / adopted['selected'] / 'outputs'
    state_rel = Path(producer['native_folder']).relative_to(original_output) / 'state_out.json'
    netlist_rel = Path(f"phase2/stage2/synth/{spec['top']}_synth.v")
    copies = {netlist_rel: selected / 'project' / netlist_rel,
              Path('phase2/stage2/synth/stats.json'): selected / 'project/phase2/stage2/synth/stats.json'}
    enc = selected / 'project/phase2/stage2/synth/fsm_encoding.enc'
    if enc.is_file():
        copies[Path('phase2/stage2/synth/fsm_encoding.enc')] = enc
    elif (project / 'phase2/stage2/synth/fsm_encoding.enc').exists():
        raise em.Refusal('PRODUCTION_PRIOR_FSM_NOT_ADOPTED', str(project))
    journal = importer._Journal(project)
    try:
        for rel in copies:
            journal.touch(project / rel)
        journal.touch(project / 'phase2/stage2/synth/synth_inputs.json')
        journal.touch(project / 'provenance.jsonl')
        journal.touch(project / 'phase3/execution_modes_handoff.json')
        lc.handoff_to_direct(selected / state_rel, {'nl': project / netlist_rel},
            project / 'phase3/execution_modes_handoff.json',
            path_map={str(original_output): str(selected)})
        for rel, source in copies.items():
            if rel != netlist_rel:
                importer._copy(source, project / rel)
            if em.digest(project / rel) != em.digest(source):
                raise em.Refusal('PRODUCTION_SELECTED_IMPORT_CHANGED', str(rel))
        _write_synth_inputs_sidecar(project / netlist_rel, project / 'phase2/stage1/rtl')
        # Reuse the actual native producer row. Rebind only the canonical
        # output key; measurement and native logs remain unchanged and cited.
        rows = [json.loads(line) for line in (selected / 'project/provenance.jsonl').read_text().splitlines()]
        expected = 'sha256:' + em.digest(project / netlist_rel)
        matches = [row for row in rows if row.get('tool') == 'yosys' and
                   row.get('exit_code') == 0 and expected in row.get('outputs', {}).values()]
        if not matches:
            raise em.Refusal('PRODUCTION_SELECTED_PROVENANCE_MISSING', str(netlist_rel))
        row = dict(matches[-1], outputs={netlist_rel.as_posix(): expected},
                   execution_generation=str(selected), source_receipt=str(run / adopted['selected'] / 'receipt.json'))
        with (project / 'provenance.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        directory = run / 'import-gates'
        directory.mkdir()
        primary = {
            'synth_netlist_check': _gate('synth_netlist_check', ['--netlist', str(project / netlist_rel),
                '--tool-netlist', str(selected / 'project' / netlist_rel), '--rtl',
                *map(str, sorted((project / 'phase2/stage1/rtl').rglob('*.v'))),
                *map(str, sorted((project / 'phase2/stage1/rtl').rglob('*.sv')))], directory),
            'pdk_consistency_check': _gate('pdk_consistency_check', ['--netlist', str(project / netlist_rel),
                '--pdk-lib', spec['pdk']['liberty']], directory),
            'area_total_vs_budget_check': _gate('area_total_vs_budget_check', [str(project),
                '--library', spec['pdk']['liberty']], directory),
            'provenance_check': _gate('provenance_check', [str(project), '--output', netlist_rel.as_posix(),
                '--tool', 'yosys', '--require-measured'], directory)}
        if set(primary.values()) != {'PASS'}:
            raise em.Refusal('GATE_FAIL' if 'FAIL' in primary.values() else 'GATE_NOT_MEASURED', repr(primary))
        controller._generation_current(generation)
        if context.binding() != adopted['evidence']['binding']:
            raise em.Refusal('PRODUCTION_IMPORT_INPUT_CHANGED', str(project))
        controller._source_current(controller.registry.adapters('9')[0])
        for rel, source in copies.items():
            if em.digest(project / rel) != em.digest(source):
                raise em.Refusal('PRODUCTION_SELECTED_IMPORT_CHANGED', str(rel))
        document = dict(status='IMPORTED', selected_generation=generation, primary_gates=primary,
                        copied={str(k): em.digest(project / k) for k in copies})
        write_json(run / 'import.json', document)
        journal.close()
        return document
    except Exception:
        journal.rollback()
        raise


def dispatch_synthesis(project: Path, top: str, pdk, container: str, period_relax: float = 1.0):
    from phase3_one_shot_runner import StepResult
    started = time.time()
    request = policy.request()
    budget = em.Budget(request['cpus'], request['ram_mb'], workers=1)
    root = project / 'phase3/execution_modes' / uuid.uuid4().hex
    root.mkdir(parents=True)
    write_json(root / 'catalog.json', catalog())
    write_json(root / 'request.json', request)
    observed_native_failure = False
    try:
        if period_relax != 1.0:
            raise em.Refusal('PRODUCTION_PERIOD_RELAX_NOT_IMPLEMENTED', repr(period_relax))
        with host_lease(budget) as lease:
            with native_boundary(lease):
                context, registry = prepare(project, top, pdk, lease, root / 'native-request.json')
            controller = em.Controller(registry, budget)
            run = root / 'run'
            result = controller.run(context, run, request['mode'] + '-mode')
            statuses = result.get('candidate_statuses', {})
            if 'FAIL' in statuses.values():
                observed_native_failure = True
                return StepResult('synth', 'FAIL', time.time() - started,
                                  'execution adapter gate FAIL; ' + str(root), [str(root / 'run/result.json')])
            if 'ELIGIBLE' not in statuses.values():
                raise em.Refusal('PRODUCTION_NATIVE_NOT_MEASURED', json.dumps(result))
            adopted = controller.adopt(context, run, _choice(request['choice'], request['choice_wait_s']))
            imported = import_selected(project, context, controller, run, adopted)
            return StepResult('synth', 'PASS', time.time() - started,
                              'issued native generation imported; primary gates PASS; ' + str(run),
                              [str(project / rel) for rel in imported['copied']])
    except (em.Refusal, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        code = getattr(exc, 'code', 'PRODUCTION_PROTOCOL_UNMEASURED')
        write_json(root / 'refusal.json', dict(status='REFUSED', reason=code, detail=str(exc)))
        failed = observed_native_failure or code == 'GATE_FAIL'
        return StepResult('synth', 'FAIL' if failed else 'NOT_MEASURED',
                          time.time() - started, str(exc), [str(root / 'refusal.json')],
                          reason_class='' if failed else (
                              ReasonClass.NOT_EXECUTED if 'NOT_IMPLEMENTED' in code else ReasonClass.EXECUTION_ERROR))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(catalog(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

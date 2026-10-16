"""R6 Python execution closure controls; source/software evidence only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import execution_modes as em
from programs.tests._execution_source_fixture import register_source_fixture, issued_context
from execution_provider_catalog import implementation_closure, proven_dispatcher_closure
from programs.tests.test_execution_dispatcher_binding import arm, controller, issued_context16
from programs.tests import test_execution_backend_f3 as fixtures
from programs.tests import test_execution_modes as decisions
from programs.tests.test_execution_receipt_chain import isolated_transport


def package_arm(tmp_path, *, omit=False, dotted=False):
    original = arm()
    package = tmp_path / 'producer'; package.mkdir()
    init = package / '__init__.py'
    if dotted:
        init.write_text('"""Transparent package namespace."""\n')
        package = package / 'nested'; package.mkdir()
        nested_init = package / '__init__.py'
        nested_init.write_text('from .implementation import main\n')
        module = 'producer.nested'
    else:
        init.write_text('from producer.implementation import main\n')
        module = 'producer'
    helper = package / 'implementation.py'
    helper.write_text('import sys\nsys.path.insert(0, ' + repr(str(PROGRAMS)) + ')\n'
                      'from execution_release_worker import main\n')
    entry = tmp_path / 'entry.py'
    entry.write_text('import sys\nsys.path.insert(0, ' + repr(str(tmp_path)) + ')\n'
                     'from ' + module + ' import main\nraise SystemExit(main())\n')
    files = [entry, init, helper] + ([nested_init] if dotted else [])
    source = {**original.source_files, **{str(p): em.digest(p) for p in files if not (omit and p == helper)}}
    component = original.components[0]
    candidate = replace(original, arm_id='package_worker', engine_families=('package_label',),
                        source_files=source, components=(replace(component, argv=(component.argv[0], str(entry), *component.argv[2:])),))
    return candidate, helper, entry


@pytest.mark.parametrize('dotted', [False, True])
def test_complete_package_execution_and_drift_refusal(tmp_path, dotted):
    candidate, helper, entry = package_arm(tmp_path, dotted=dotted)
    proof = proven_dispatcher_closure(entry, Path(arm().components[0].argv[1]))
    assert helper in proof
    assert all(p in proof for p in tmp_path.rglob('__init__.py'))
    ctl = controller(candidate, fixture_root=tmp_path); ctx = fixtures.context16(tmp_path, native_clock=True); root = tmp_path / 'run'
    assert ctl.run(ctx, root, 'ultra-mode')['candidate_statuses'] == {candidate.arm_id: 'ELIGIBLE'}
    helper.write_text(helper.read_text() + '\n# changed after eligibility\n')
    with pytest.raises(em.Refusal, match='ADAPTER_SOURCE_MISMATCH'):
        ctl.adopt(ctx, root, decisions.choice(ctx, root, candidate.arm_id))
    adoption = json.loads((root / 'adoption.json').read_text())
    assert adoption['selected'] is None and adoption['status'] == 'REFUSED'
    assert not (root / 'selected').exists()


def test_unbound_package_helper_refuses_before_launch(tmp_path, monkeypatch):
    candidate, helper, entry = package_arm(tmp_path, omit=True)
    assert helper in proven_dispatcher_closure(entry, Path(arm().components[0].argv[1]))
    # A real omitted package helper is refused at enrollment once absolute
    # imports resolve correctly.  Runtime refusal remains covered by the
    # unsupported-interpreter controls below; no invalid enrollment is made.
    root = tmp_path / 'run'
    run_calls = []
    real_run = em.Controller.run
    def observe_run(self, *args, **kwargs):
        run_calls.append(True)
        return real_run(self, *args, **kwargs)
    monkeypatch.setattr(em.Controller, 'run', observe_run)
    with pytest.raises(em.Refusal, match='^ADAPTER_SOURCE_CLOSURE_INCOMPLETE:') as caught:
        controller(candidate, fixture_root=tmp_path)
    assert caught.value.code == 'ADAPTER_SOURCE_CLOSURE_INCOMPLETE'
    assert str(caught.value) == f'ADAPTER_SOURCE_CLOSURE_INCOMPLETE: {helper}'
    assert run_calls == []
    assert not root.exists()
    assert not (root / 'selected').exists()
    (tmp_path / 'package_enrollment_observed.json').write_text(json.dumps({
        'reason': caught.value.code, 'detail': str(caught.value),
        'omitted_helper': str(helper), 'controller_run_calls': len(run_calls),
        'run_root_exists': root.exists(), 'selected_exists': (root / 'selected').exists(),
        'scope': 'SOURCE_CONTROL enrollment refusal; no native worker launched',
    }, indent=2))


@pytest.mark.parametrize('option', ['-B', '-u', '-q', '-O', '-OO', '--'])
def test_interpreter_options_share_one_proven_worker(tmp_path, option):
    original = arm(); component = original.components[0]
    candidate = replace(original, arm_id='option_alias', engine_families=('option_label',),
                        components=(replace(component, argv=(component.argv[0], option, *component.argv[1:])),))
    ctl = controller(original, candidate, fixture_root=tmp_path); root = tmp_path / 'run'
    result = ctl.run(fixtures.context16(tmp_path, native_clock=True), root, 'ultra-mode')
    assert list(result['candidate_statuses'].values()) == ['ELIGIBLE']
    assert len(json.loads((root / 'plan.json').read_text())['arms']) == 1


@pytest.mark.parametrize('option', ['-c', '-m', '-I', '-E', '-P', '-S', '-W', '-X'])
def test_unsupported_invocation_is_scoped_refusal_before_launch(tmp_path, option):
    original = arm(); component = original.components[0]
    candidate = replace(original, arm_id='000_unsupported', engine_families=('unsupported',),
                        components=(replace(component, argv=(component.argv[0], option, *component.argv[1:])),))
    root = tmp_path / 'run'
    if option in ('-c', '-m'):
        # Unsupported entry forms are refused by registration before a plan exists.
        with pytest.raises(em.Refusal, match='ENTRY_SOURCE_UNBOUND'):
            controller(candidate, fixture_root=tmp_path)
        ctl = controller(original, fixture_root=tmp_path)
    else:
        ctl = controller(original, candidate, fixture_root=tmp_path)
    result = ctl.run(fixtures.context16(tmp_path, native_clock=True), root, 'ultra-mode')
    assert result['candidate_statuses'][original.arm_id] == 'ELIGIBLE'
    if option in ('-c', '-m'):
        assert not (root / candidate.arm_id).exists()
        return
    assert result['candidate_statuses'][candidate.arm_id] == 'NOT_MEASURED'
    receipt = json.loads((root / candidate.arm_id / 'receipt.json').read_text())
    assert receipt['reason'] == 'PROVIDER_DEPENDENCY_UNBOUND' and receipt['processes'] == []


def test_relative_entry_binds_actual_output_cwd(tmp_path):
    original = arm(); component = original.components[0]; root = tmp_path / 'run'
    name = 'relative_alias'
    relative = os.path.relpath(component.argv[1], root / name / 'outputs')
    candidate = replace(original, arm_id=name, engine_families=('relative_label',),
                        components=(replace(component, argv=(component.argv[0], '-B', relative, *component.argv[2:])),))
    assert em._provider_identity(candidate, cwd=root / name / 'outputs') == em._provider_identity(original)
    ctl = controller(original, candidate, fixture_root=tmp_path)
    result = ctl.run(issued_context16(tmp_path), root, 'ultra-mode')
    plan = json.loads((root / 'plan.json').read_text())
    receipt = json.loads((root / original.arm_id / 'receipt.json').read_text())
    assert plan['arms'] == [original.arm_id]
    assert next(row for row in plan['portfolio'] if row['arm_id'] == name)['admission'] == 'SAME_ENGINE_FAMILY'
    # The software Step-16 fixture has producer-ready inputs but deliberately
    # omits its SDC clock from CTS config. Preserve that real consumer FAIL
    # while proving the relative entry resolves to the
    # same current source closure and cannot become another provider.
    assert result['candidate_statuses'] == {original.arm_id: 'FAIL'}
    assert receipt['reason'] == 'GATE_FAIL'
    assert receipt['evidence']['gates']['clock_plan_check'] == 'FAIL'
    assert len(receipt['processes']) == 1
    assert receipt['processes'][0]['rc'] == 0
    alias_receipt = json.loads((root / name / 'receipt.json').read_text())
    assert alias_receipt['status'] == 'REFUSED'
    assert alias_receipt['reason'] == 'SAME_ENGINE_FAMILY'
    assert alias_receipt['processes'] == []
    evidence = json.loads((root / original.arm_id / 'outputs/release-evidence.json').read_text())
    assert evidence['missing_inputs'] == []
    assert evidence['producer_verdict'] == 'PASS'
    assert 'phase3/stage3/cts/clock_plan.json' in evidence['outputs']
    assert evidence['canonical_status'] == 'FAIL'
    assert any(row['gate'] == 'clock_plan_check' and row['rc'] == 1
               and row['verdict'] == 'FAIL' for row in evidence['canonical_gate_ledger'])
    gate = json.loads((root / original.arm_id / 'outputs/project/reports/phase2/gates/clock_plan.json').read_text())
    assert gate['verdict'] == 'FAIL'
    assert any(f['rule'] == 'LIBRELANE_CLOCK_MISSING' and f['severity'] == 'FAIL'
               for f in gate['findings'])


def test_literal_search_path_selects_actual_shadow_package(tmp_path):
    candidate, helper, entry = package_arm(tmp_path)
    module = tmp_path / 'producer.py'
    module.write_text('raise RuntimeError("Python must prefer the package")\n')
    # Bind the irrelevant shadow file without declaring it an executed dependency.
    candidate = replace(candidate, source_files={**candidate.source_files, str(module): em.digest(module)})
    closure = proven_dispatcher_closure(entry, Path(arm().components[0].argv[1]))
    assert helper in closure and module not in closure
    result = controller(candidate, fixture_root=tmp_path).run(fixtures.context16(tmp_path, native_clock=True), tmp_path / 'run', 'ultra-mode')
    assert result['candidate_statuses'][candidate.arm_id] == 'ELIGIBLE'


def test_active_pythonpath_omitted_helper_refuses(tmp_path, monkeypatch):
    original = arm(); component = original.components[0]
    directory = tmp_path / 'search'; directory.mkdir()
    helper = directory / 'environment_helper.py'
    helper.write_text('from execution_release_worker import main\n')
    entry = tmp_path / 'entry.py'
    entry.write_text('from environment_helper import main\nraise SystemExit(main())\n')
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(directory), str(PROGRAMS)]))
    candidate = replace(original, arm_id='environment_alias',
                        source_files={**original.source_files, str(entry): em.digest(entry)},
                        components=(replace(component, argv=(component.argv[0], str(entry), *component.argv[2:])),))
    assert helper in proven_dispatcher_closure(entry, Path(component.argv[1]))
    result = controller(candidate, fixture_root=tmp_path).run(fixtures.context16(tmp_path), tmp_path / 'run', 'ultra-mode')
    assert result['candidate_statuses'][candidate.arm_id] == 'NOT_MEASURED'
    assert json.loads((tmp_path / 'run' / candidate.arm_id / 'receipt.json').read_text())['processes'] == []


def test_opaque_earlier_wrapper_preserves_valid_representative(tmp_path):
    original = arm(); component = original.components[0]
    entry = tmp_path / 'opaque.py'
    entry.write_text('import sys\nsys.path.insert(0, ' + repr(str(PROGRAMS)) + ')\n'
                     'from execution_release_worker import main\n'
                     'open("side_effect", "w").write("opaque")\nraise SystemExit(main())\n')
    candidate = replace(original, arm_id='000_opaque', engine_families=('opaque_label',),
                        source_files={**original.source_files, str(entry): em.digest(entry)},
                        components=(replace(component, argv=(component.argv[0], str(entry), *component.argv[2:])),))
    ctl = controller(original, candidate, fixture_root=tmp_path); ctx = fixtures.context16(tmp_path, native_clock=True); root = tmp_path / 'run'
    plan = ctl.plan(ctx, 'ultra-mode')
    assert original.arm_id in plan['arms']
    assert plan['independence'][candidate.arm_id]['sha256'] is None
    result = ctl.run(ctx, root, 'ultra-mode')
    assert result['candidate_statuses'][original.arm_id] == 'ELIGIBLE'
    assert result['candidate_statuses'][candidate.arm_id] == 'NOT_MEASURED'
    assert json.loads((root / candidate.arm_id / 'receipt.json').read_text())['processes'] == []
    assert ctl.adopt(ctx, root, decisions.choice(ctx, root, original.arm_id))['status'] == 'ADOPTED'


def test_bound_search_path_change_cannot_reuse_execution_closure(tmp_path, monkeypatch):
    original = arm(); component = original.components[0]
    files = []
    directories = []
    for name in ['first', 'second']:
        directory = tmp_path / name; directory.mkdir(); directories.append(directory)
        helper = directory / 'environment_helper.py'
        helper.write_text('import sys\nsys.path.insert(0, ' + repr(str(PROGRAMS)) + ')\n'
                          'from execution_release_worker import main\n')
        files.append(helper)
    entry = tmp_path / 'entry.py'
    entry.write_text('from environment_helper import main\nraise SystemExit(main())\n')
    files.append(entry)
    candidate = replace(original, arm_id='environment_worker',
                        source_files={**original.source_files, **{str(p): em.digest(p) for p in files}},
                        components=(replace(component, argv=(component.argv[0], str(entry), *component.argv[2:])),))
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(directories[0]), str(PROGRAMS)]))
    ctl = controller(candidate, fixture_root=tmp_path); ctx = fixtures.context16(tmp_path, native_clock=True); root = tmp_path / 'run'
    result = ctl.run(ctx, root, 'ultra-mode')
    assert result['candidate_statuses'][candidate.arm_id] == 'ELIGIBLE'
    receipt = json.loads((root / candidate.arm_id / 'receipt.json').read_text())
    assert str(files[0]) in receipt['execution_closure']['files']
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(directories[1]), str(PROGRAMS)]))
    with pytest.raises(em.Refusal, match='PROVIDER_CLOSURE_CHANGED'):
        ctl.adopt(ctx, root, decisions.choice(ctx, root, candidate.arm_id))
    assert json.loads((root / 'adoption.json').read_text())['selected'] is None
    assert not (root / 'selected').exists()


def test_bound_shadow_dependency_cannot_claim_transparent_execution(tmp_path):
    original = arm(); component = original.components[0]
    shadow = tmp_path / '_atomic_artefact.py'
    shadow.write_text('SHADOW_DEPENDENCY = True\n')
    entry = tmp_path / 'entry.py'
    entry.write_text('import sys\nsys.path.insert(0, ' + repr(str(PROGRAMS)) + ')\n'
                     'sys.path.insert(0, ' + repr(str(tmp_path)) + ')\n'
                     'from execution_release_worker import main\nraise SystemExit(main())\n')
    candidate = replace(original, arm_id='shadow_worker',
                        source_files={**original.source_files, str(entry): em.digest(entry), str(shadow): em.digest(shadow)},
                        components=(replace(component, argv=(component.argv[0], str(entry), *component.argv[2:])),))
    ctl = controller(candidate, fixture_root=tmp_path); root = tmp_path / 'run'
    result = ctl.run(fixtures.context16(tmp_path), root, 'ultra-mode')
    assert result['candidate_statuses'][candidate.arm_id] == 'NOT_MEASURED'
    receipt = json.loads((root / candidate.arm_id / 'receipt.json').read_text())
    assert receipt['reason'] == 'PROVIDER_DEPENDENCY_UNBOUND'
    assert receipt['processes'] == []


@pytest.mark.parametrize('cached', [False, True])
def test_timestamp_valid_stale_package_cache_uses_current_source(tmp_path, cached):
    import py_compile

    candidate, helper, entry = package_arm(tmp_path)
    assert candidate.components[0].argv[1] == str(entry)
    nominal = helper.read_text()
    previous = nominal + '\nfrom pathlib import Path\nPath("previous-helper-executed.txt").write_text("stale")\n'
    current = nominal + '\n#' + ' ' * (len(previous.encode()) - len(nominal.encode()) - 2)
    helper.write_text(previous)
    cache = Path(py_compile.compile(str(helper), doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
    stamp = helper.stat()
    helper.write_text(current)
    os.utime(helper, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert cache.read_bytes()[4:8] == b'\0\0\0\0'
    assert helper.stat().st_size == len(previous.encode())
    stale_bytes = cache.read_bytes()
    if not cached:
        cache.unlink()
    candidate = replace(candidate, source_files={**candidate.source_files, str(helper): em.digest(helper)})
    ctl = controller(candidate, fixture_root=tmp_path); ctx = fixtures.context16(tmp_path, native_clock=True); root = tmp_path / 'run'
    assert ctl.run(ctx, root, 'ultra-mode')['candidate_statuses'] == {candidate.arm_id: 'ELIGIBLE'}
    assert not (root / candidate.arm_id / 'outputs/previous-helper-executed.txt').exists()
    accepted = ctl.adopt(ctx, root, decisions.choice(ctx, root, candidate.arm_id))
    assert accepted['status'] == 'ADOPTED'
    if cached:
        receipt = json.loads((root / candidate.arm_id / 'receipt.json').read_text())
        prefix = Path(receipt['processes'][0]['issued_environment']['PYTHONPYCACHEPREFIX'])
        assert prefix.is_dir() and not list(prefix.iterdir())
        # Put the applicable old helper cache into the issued private prefix
        # after eligibility. Adoption must revoke this execution proof while
        # preserving the earlier committed generation and canonical inputs.
        poison = prefix / str(helper.parent).lstrip('/') / cache.name
        poison.parent.mkdir(parents=True); poison.write_bytes(stale_bytes)
        prior = Path(accepted['selected_generation']['directory'])
        snapshot = {str(p.relative_to(prior)): (em.digest(p), p.stat().st_mode)
                    for p in prior.rglob('*') if p.is_file()}
        inputs = {name: em.digest(path) for name, path in ctx.inputs.items()}
        with pytest.raises(em.Refusal, match='PYTHON_BYTECODE_POLICY_CHANGED'):
            ctl.adopt(ctx, root, decisions.choice(ctx, root, candidate.arm_id))
        refused = json.loads((root / 'adoption.json').read_text())
        assert refused['status'] == 'REFUSED' and refused['selected'] is None
        assert list((root / 'selected').iterdir()) == [prior]
        assert snapshot == {str(p.relative_to(prior)): (em.digest(p), p.stat().st_mode)
                            for p in prior.rglob('*') if p.is_file()}
        assert inputs == {name: em.digest(path) for name, path in ctx.inputs.items()}
        ctl._generation_current(accepted['selected_generation'])


def test_image_editable_dependency_reaches_current_release_consumer(tmp_path):
    """Use the current worker and installed dependency, with no native PASS claim."""
    import importlib.util
    candidate = arm()
    native = Path(importlib.util.find_spec('cocotb.simulator').origin).resolve()
    assert str(native) in candidate.source_files
    assert candidate.source_files[str(native)] == em.digest(native)
    registry = em.Registry(); registry.register(candidate)
    assert em.Controller._source_current(registry.adapters('16')[0])['files'][str(native)] == em.digest(native)
    ctx = fixtures.context16(tmp_path, native_clock=True)
    root = tmp_path / 'real'
    ctl = em.Controller(registry, em.Budget(1, 4096, workers=1))
    assert ctl.run(ctx, root)['candidate_statuses'] == {candidate.arm_id: 'ELIGIBLE'}
    assert ctl.adopt(ctx, root, decisions.choice(ctx, root, candidate.arm_id))['status'] == 'ADOPTED'


@pytest.mark.parametrize('mutation', ['missing', 'changed', 'unbound'])
def test_current_image_dependency_refused_before_execution(tmp_path, monkeypatch, mutation):
    import importlib.util
    native = Path(importlib.util.find_spec('cocotb.simulator').origin).resolve()
    candidate = arm()
    assert str(native) in candidate.source_files
    registry = em.Registry(); registry.register(candidate)
    enrolled = registry.adapters('16')[0]
    if mutation == 'unbound':
        candidate = replace(enrolled, source_files={k: v for k, v in enrolled.source_files.items() if k != str(native)})
        with pytest.raises(em.Refusal, match='PROVIDER_DEPENDENCY_UNBOUND'):
            em.Controller._source_current(candidate)
        return
    original = native.read_bytes()
    assert em.digest(native) == enrolled.source_files[str(native)]
    control = tmp_path / native.name
    control.write_bytes(original)
    if mutation == 'missing':
        control.unlink()
    else:
        control.write_bytes(original + b'changed dependency control')

    # Installed image assets are root-owned. Supply this unit's controlled
    # filesystem observation at the exact bound dependency, while retaining
    # the real source checker and real digest of writable control bytes.
    observed = []
    real_is_file, real_digest = Path.is_file, em.digest
    with monkeypatch.context() as patch:
        if mutation == 'missing':
            def current_is_file(path):
                if path == native:
                    observed.append(str(path))
                    return real_is_file(control)
                return real_is_file(path)
            patch.setattr(Path, 'is_file', current_is_file)
        else:
            def current_digest(path):
                if Path(path) == native:
                    observed.append(str(path))
                    return real_digest(control)
                return real_digest(path)
            patch.setattr(em, 'digest', current_digest)
        with pytest.raises(em.Refusal, match='ADAPTER_SOURCE_MISMATCH') as caught:
            em.Controller._source_current(enrolled)
        assert caught.value.code == 'ADAPTER_SOURCE_MISMATCH'
        assert str(caught.value) == f'ADAPTER_SOURCE_MISMATCH: {native}'
        assert observed and set(observed) == {str(native)}
    assert native.read_bytes() == original


@pytest.mark.parametrize('mutation', ['valid', 'malformed', 'missing_metadata', 'bad_url',
                                      'not_editable', 'wrong_top', 'shadow', 'unbound_local'])
def test_editable_native_exact_package_ownership(tmp_path, monkeypatch, mutation):
    """Neutral metadata/source controls; no fixture receipt claims native success."""
    import importlib.metadata as metadata
    import importlib.util
    import execution_provider_catalog as catalog
    library = tmp_path / 'installed'; library.mkdir()
    project = tmp_path / 'dependency'; project.mkdir()
    package = project / 'src' / 'neutral_pkg'; package.mkdir(parents=True)
    init = package / '__init__.py'; init.write_text('from . import native\n')
    actual = Path(importlib.util.find_spec('cocotb.simulator').origin)
    suffix = actual.name.removeprefix('simulator')
    native = package / ('native' + suffix); native.write_bytes(actual.read_bytes())
    info = library / 'neutral_dist-1.0.dist-info'; info.mkdir()
    (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: neutral-dist\nVersion: 1.0\n')
    direct = info / 'direct_url.json'
    direct.write_text(json.dumps({'url': project.as_uri(), 'dir_info': {'editable': True}}))
    hook = library / '_editable_neutral.py'
    hook.write_text('def install(*args): pass\ninstall(' + repr({'neutral_pkg': str(init)}) + ', {}, {})\n')
    pth = library / '_editable_neutral.pth'; pth.write_text('import _editable_neutral\n' + str(package.parent) + '\n')
    (info / 'RECORD').write_text('\n'.join(p + ',,' for p in [
        '_editable_neutral.py', '_editable_neutral.pth', 'neutral_dist-1.0.dist-info/METADATA',
        'neutral_dist-1.0.dist-info/direct_url.json', 'neutral_dist-1.0.dist-info/RECORD']) + '\n')
    monkeypatch.setattr(catalog, '_python_trusted_roots', lambda: (library,))
    # Distribution discovery uses only the interpreter's roots, never local PYTHONPATH metadata.
    search = [package.parent]
    entry = tmp_path / 'entry.py'; entry.write_text('import neutral_pkg.native\n')
    if mutation == 'malformed': native.write_bytes(b'not a native extension')
    elif mutation == 'missing_metadata': direct.rename(info / 'direct_url.saved')
    elif mutation == 'bad_url': direct.write_text('{"url": "https://invalid.example/dependency", "dir_info": {"editable": true}}')
    elif mutation == 'not_editable': direct.write_text(json.dumps({'url': project.as_uri(), 'dir_info': {'editable': False}}))
    elif mutation == 'wrong_top': hook.write_text('install(' + repr({'other_pkg': str(init)}) + ', {}, {})\n')
    elif mutation in ('shadow', 'unbound_local'):
        shadow = tmp_path / 'shadow'; shadow.mkdir()
        local = shadow / 'neutral_pkg'; local.mkdir()
        (local / '__init__.py').write_text('from . import native\n')
        (local / native.name).write_bytes(native.read_bytes())
        search.insert(0, shadow)
        if mutation == 'unbound_local':
            # Local dist-info claiming an editable project cannot establish interpreter ownership.
            import shutil
            shutil.copytree(info, shadow / info.name)
            monkeypatch.setenv('PYTHONPATH', str(shadow))
    if mutation == 'valid':
        closure = implementation_closure(entry, search=search)
        assert {entry, init, native, direct, hook, pth, info / 'RECORD', info / 'METADATA'} <= closure
        assert em.digest(native) == em.digest(actual)
    else:
        with pytest.raises(ValueError, match='unsupported local Python module'):
            implementation_closure(entry, search=search)


def test_nonstandard_script_suffix_preserves_local_import_closure(tmp_path):
    entry = tmp_path / 'entry.script'
    helper = tmp_path / 'helper.py'
    entry.write_text('import helper\n')
    helper.write_text('VALUE = 1\n')
    assert implementation_closure(entry, search=[tmp_path]) == {entry, helper}


def test_installed_native_closure_reports_observed_binding(tmp_path):
    import importlib.util
    native = Path(importlib.util.find_spec('cocotb.simulator').origin).resolve()
    entry = tmp_path / 'entry.py'; entry.write_text('import cocotb.simulator\n')
    try:
        closure = implementation_closure(entry, search=[native.parent.parent])
        actual = 'BOUND' if native in closure else 'UNBOUND'
    except ValueError:
        actual = 'REFUSED'
    assert actual == 'BOUND'


@pytest.mark.parametrize('kind', ['bare', 'absolute', 'relative'])
def test_package_lookup_matches_actual_absolute_and_relative_imports(tmp_path, kind):
    import subprocess
    package = tmp_path / 'neutral_pkg'; package.mkdir()
    init = package / '__init__.py'; init.write_text('')
    sibling = package / 'logging.py'; sibling.write_text('VALUE = 1\n')
    member = package / 'member.py'
    imports = {'bare': 'import logging as selected',
               'absolute': 'import neutral_pkg.logging as selected',
               'relative': 'from . import logging as selected'}
    member.write_text(imports[kind] + '\nIMPORT_PATH = selected.__file__\n')
    actual = subprocess.run([sys.executable, '-B', '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); '
        'import neutral_pkg.member as selected; print(selected.IMPORT_PATH)', str(tmp_path)],
        capture_output=True, text=True, check=True)
    closure = em._source_closure([str(member)])
    observed = {'kind': kind, 'actual_import': actual.stdout.strip(),
                'closure': sorted(str(p.relative_to(tmp_path)) for p in closure),
                'scope': 'Real Python import and existing source closure on neutral files'}
    (tmp_path / 'package_lookup_observed.json').write_text(json.dumps(observed, indent=2) + '\n')
    if kind == 'bare':
        assert Path(actual.stdout.strip()).resolve() != sibling
        assert sibling not in closure, observed
    else:
        assert Path(actual.stdout.strip()).resolve() == sibling
        assert sibling in closure, observed
        if kind == 'absolute':
            assert init in closure, observed


def test_flat_script_import_remains_a_bound_local_dependency(tmp_path):
    import subprocess
    entry = tmp_path / 'entry.py'; helper = tmp_path / 'helper.py'
    entry.write_text('import helper\nprint(helper.VALUE)\n')
    helper.write_text('VALUE = 1\n')
    actual = subprocess.run([sys.executable, '-B', str(entry)],
                            capture_output=True, text=True, check=True)
    closure = em._source_closure([str(entry)])
    assert actual.stdout.strip() == '1'
    assert closure == {entry, helper}

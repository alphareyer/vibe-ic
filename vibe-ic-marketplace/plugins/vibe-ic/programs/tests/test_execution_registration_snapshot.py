"""Bounded source snapshot controls for one Ultra bootstrap."""
from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

import execution_modes as em
from execution_source_snapshot import SourceSnapshot, using
from execution_provider_catalog import _python_module
from programs.tests import test_execution_modes as H


def test_source_closure_accepts_mapping_tuple_and_set_path_contracts(tmp_path):
    entry = tmp_path / 'entry.py'
    helper = tmp_path / 'helper.py'
    entry.write_text('import helper\n')
    helper.write_text('value = 1\n')
    paths = (str(entry),)
    mapping = {str(entry): hashlib.sha256(entry.read_bytes()).hexdigest()}
    expected = {entry.resolve(), helper.resolve()}
    assert em._source_closure(paths) == expected
    assert em._source_closure(mapping) == expected
    assert em._source_closure({str(entry)}) == expected


def test_identical_registrations_share_one_snapshot_and_git_tree():
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        first = H.adapter('a')
        second = replace(first, arm_id='b')
        registry = em.Registry(snapshot=snapshot)
        registry.register(first)
        registry.register(second)
        registry.finalize()
    assert snapshot.stats['git_tree_reads'] == 1
    assert snapshot.stats['git_commands'] == 6
    assert snapshot.stats['closure_hits'] > 0
    assert snapshot.stats['final_reads'] == snapshot.stats['file_reads']


def test_finalization_rejects_a_file_changed_after_snapshot(tmp_path):
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    changed = tmp_path / 'worker.py'
    changed.write_text('value = 1\n')
    with using(snapshot):
        snapshot.read_bytes(changed)
        changed.write_text('value = 2\n')
        with pytest.raises(RuntimeError, match='SOURCE_AUTHORITY_DIRTY'):
            snapshot.finalize()


def test_snapshot_cannot_answer_for_a_different_source_commit():
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with pytest.raises(RuntimeError, match='SOURCE_AUTHORITY_STALE'):
        snapshot.git_blobs(f'{snapshot.commit}^', (em._canonical_flow_path(),))


def test_snapshot_preserves_canonical_portfolio_gate_and_default_outcome():
    portfolio = em.load_portfolio()
    expected = tuple((row['id'], tuple(row['mandatory_gate_programs']),
                      tuple(row['current_default']['source']['programs']))
                     for row in portfolio['steps'])
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        em._validate_portfolio(portfolio, snapshot=snapshot)
        actual = tuple((row['id'], tuple(row['mandatory_gate_programs']),
                        tuple(row['current_default']['source']['programs']))
                       for row in portfolio['steps'])
        snapshot.finalize()
    assert actual == expected


def test_snapshot_identity_cache_is_bounded_to_one_transaction(tmp_path):
    source = tmp_path / 'worker.py'
    source.write_bytes(b'value = 1\n')
    tracked = em._canonical_flow_path()
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    for _ in range(3):
        first = snapshot.digest(source)
        snapshot.git_blobs(snapshot.commit, (tracked,))
        snapshot.verify_tracked(tracked)
    assert snapshot.stats['path_resolutions'] == 2
    assert snapshot.stats['digest_computations'] == 1
    assert snapshot.stats['digest_cache_hits'] == 2
    assert snapshot.stats['blob_computations'] == 1
    assert snapshot.stats['blob_cache_hits'] == 2
    snapshot.finalize()
    source.write_bytes(b'value = 2\n')
    fresh = SourceSnapshot(em._REPO_ROOT, H.BASE)
    assert fresh.digest(source) != first
    fresh.finalize()


@pytest.mark.parametrize('mutation', ['parent_alias', 'leaf_alias', 'deleted'])
def test_snapshot_finalization_rechecks_original_path_target(tmp_path, mutation):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir(); second.mkdir()
    for directory in (first, second):
        (directory / 'worker.py').write_bytes(b'identical bytes\n')
    alias = tmp_path / 'alias'
    alias.symlink_to(first, target_is_directory=True)
    source = alias / 'worker.py'
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    original = snapshot.read_bytes(source)
    snapshot.digest(source)
    if mutation == 'parent_alias':
        alias.unlink(); alias.symlink_to(second, target_is_directory=True)
    else:
        (first / 'worker.py').unlink()
        if mutation == 'leaf_alias':
            (first / 'worker.py').symlink_to(second / 'worker.py')
    # Cached transaction bytes are provisional; unchanged contents must not
    # hide a changed path target or permit finalization after deletion.
    assert snapshot.read_bytes(source) == original
    with pytest.raises(RuntimeError, match='SOURCE_AUTHORITY_(DIRTY|UNAVAILABLE)'):
        snapshot.finalize()


@pytest.mark.parametrize('reader', ['modes', 'catalog'])
def test_snapshot_import_resolution_is_shared_across_seed_sets(tmp_path, reader):
    import execution_provider_catalog as catalog
    closure = em._source_closure if reader == 'modes' else catalog.source_closure
    counter = 'local_import_' if reader == 'modes' else 'provider_import_'
    entry = tmp_path / 'entry.py'
    helper = tmp_path / 'helper.py'
    entry.write_text('import helper\n')
    helper.write_text('value = 1\n')
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        expected = {entry, helper}
        assert closure((str(entry),)) == expected
        misses = snapshot.stats[counter + 'misses']
        assert closure((str(entry), str(helper))) == expected
        assert snapshot.stats[counter + 'misses'] == misses
        assert snapshot.stats[counter + 'hits'] >= 2
        snapshot.finalize()


@pytest.mark.parametrize('reader', ['modes', 'catalog'])
@pytest.mark.parametrize('mutation', ['new_local', 'leaf_alias', 'parent_alias'])
def test_snapshot_import_cache_rejects_changed_negative_resolution(tmp_path, mutation, reader):
    import execution_provider_catalog as catalog
    closure = em._source_closure if reader == 'modes' else catalog.source_closure
    entry = tmp_path / 'entry.py'
    if mutation == 'parent_alias':
        first = tmp_path / 'first'; first.mkdir()
        second = tmp_path / 'second'; second.mkdir()
        alias = tmp_path / 'late'; alias.symlink_to(first, target_is_directory=True)
        entry.write_text('import late.worker\n')
    else:
        entry.write_text('import snapshot_fresh_local_module\n')
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        assert closure((str(entry),)) == {entry}
        if mutation == 'new_local':
            (tmp_path / 'snapshot_fresh_local_module.py').write_text('value = 1\n')
        elif mutation == 'leaf_alias':
            (tmp_path / 'snapshot_fresh_local_module.py').symlink_to(tmp_path / 'missing.py')
        else:
            alias.unlink(); alias.symlink_to(second, target_is_directory=True)
        assert closure((str(entry),)) == {entry}
        with pytest.raises(RuntimeError, match='SOURCE_AUTHORITY_DIRTY'):
            snapshot.finalize()


def test_catalog_import_resolution_without_snapshot_remains_live(tmp_path):
    import execution_provider_catalog as catalog
    entry = tmp_path / 'entry.py'
    text = 'import snapshot_fresh_local_module\n'
    entry.write_text(text)
    assert catalog._local_imports(str(entry), text) == ()
    helper = tmp_path / 'snapshot_fresh_local_module.py'
    helper.write_text('value = 1\n')
    assert catalog._local_imports(str(entry), text) == (helper,)


def test_snapshot_module_lookup_caches_ordered_positive_and_negative_identity(tmp_path):
    first = tmp_path / 'first'; second = tmp_path / 'second'
    first.mkdir(); second.mkdir()
    (first / 'worker.py').write_text('value = "first"\n')
    (second / 'worker.py').write_text('value = "second"\n')
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        assert _python_module('worker', [first, second])[0][0] == (first / 'worker.py').resolve()
        # The same request is a cache hit, while changing ordered search roots
        # is a distinct lookup and preserves first-match semantics.
        assert _python_module('worker', [first, second])[0][0] == (first / 'worker.py').resolve()
        assert _python_module('worker', [second, first])[0][0] == (second / 'worker.py').resolve()
        # Relative package/level arguments remain part of the lookup identity.
        package = first / 'pkg'; package.mkdir()
        (package / '__init__.py').write_text('value = 1\n')
        (package / 'worker.py').write_text('value = 2\n')
        assert _python_module('worker', [first], package='pkg', level=1)[-1][0] == (package / 'worker.py').resolve()
        assert snapshot.stats['module_lookup_hits'] >= 1
        assert snapshot.stats['module_lookup_misses'] >= 3
        snapshot.finalize()


def test_snapshot_repeated_module_lookup_does_not_repeat_live_stat_probes(tmp_path, monkeypatch):
    module = tmp_path / 'worker.py'
    module.write_text('value = 1\n')
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    original_stat = Path.stat
    probes = []
    def counted_stat(path, *args, **kwargs):
        probes.append(path)
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', counted_stat)
    with using(snapshot):
        expected = [(module.resolve(), '')]
        assert _python_module('worker', [tmp_path]) == expected
        assert _python_module('missing_worker', [tmp_path]) == []
        first_count = len(probes)
        for _ in range(10):
            assert _python_module('worker', [tmp_path]) == expected
            assert _python_module('missing_worker', [tmp_path]) == []
        assert len(probes) == first_count
        snapshot.finalize()


def test_snapshot_module_lookup_rejects_new_preferred_package(tmp_path):
    module = tmp_path / 'worker.py'
    module.write_text('value = 1\n')
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        assert _python_module('worker', [tmp_path]) == [(module.resolve(), '')]
        package = tmp_path / 'worker'; package.mkdir()
        (package / '__init__.py').write_text('value = 2\n')
        assert _python_module('worker', [tmp_path]) == [(module.resolve(), '')]
        with pytest.raises(RuntimeError, match='SOURCE_AUTHORITY_DIRTY'):
            snapshot.finalize()
    assert _python_module('worker', [tmp_path]) == [((package / '__init__.py').resolve(), 'worker')]


@pytest.mark.parametrize('mutation', ['new_helper', 'leaf_retarget', 'parent_retarget', 'bytes'])
def test_snapshot_module_lookup_finalization_rejects_all_resolution_mutations(tmp_path, mutation):
    first = tmp_path / 'first'; second = tmp_path / 'second'
    first.mkdir(); second.mkdir()
    helper = first / 'helper.py'
    if mutation == 'new_helper':
        # The initial negative lookup must be retained and finalization must
        # reject the newly present module instead of silently accepting it.
        name = 'late_helper'
        target = first / 'late_helper.py'
        symlink = None
    elif mutation == 'leaf_retarget':
        helper.write_text('value = 1\n')
        target = helper
        symlink = first / 'helper_link.py'
        symlink.symlink_to(helper)
        name = 'helper_link'
    elif mutation == 'parent_retarget':
        helper.write_text('value = 1\n')
        (second / 'helper.py').write_text('value = 1\n')
        (first / '__init__.py').write_text('value = 1\n')
        (second / '__init__.py').write_text('value = 1\n')
        alias = tmp_path / 'alias'
        alias.symlink_to(first, target_is_directory=True)
        target = alias
        symlink = alias
        name = 'alias.helper'
    else:
        helper.write_text('value = 1\n')
        target = helper
        symlink = None
        name = 'helper'
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    search = [first]
    with using(snapshot):
        if mutation == 'parent_retarget':
            assert _python_module(name, [tmp_path])[-1][0] == helper.resolve()
        elif mutation == 'leaf_retarget':
            with pytest.raises(ValueError, match='unsupported local Python module'):
                _python_module(name, search)
        else:
            expected = [] if mutation == 'new_helper' else [(helper.resolve(), '')]
            assert _python_module(name, search) == expected
        if mutation == 'new_helper':
            target.write_text('value = 3\n')
        elif mutation == 'leaf_retarget':
            symlink.unlink(); symlink.symlink_to(second / 'helper.py')
        elif mutation == 'parent_retarget':
            symlink.unlink(); symlink.symlink_to(second, target_is_directory=True)
        else:
            target.write_text('value = 9\n')
        with pytest.raises(RuntimeError, match='SOURCE_AUTHORITY_(DIRTY|UNAVAILABLE)'):
            snapshot.finalize()


def test_finalized_snapshot_is_not_reused_for_later_module_authority(tmp_path):
    from execution_provider_catalog import implementation_closure
    entry = tmp_path / 'entry.py'
    entry.write_text('import worker\n')
    module = tmp_path / 'worker.py'
    module.write_text('value = 1\n')
    snapshot = SourceSnapshot(em._REPO_ROOT, H.BASE)
    with using(snapshot):
        assert _python_module('worker', [tmp_path])[0][0] == module.resolve()
        assert implementation_closure(entry) == {entry, module}
        snapshot.finalize()
        module.unlink()
        (tmp_path / 'worker').mkdir()
        (tmp_path / 'worker' / '__init__.py').write_text('value = 2\n')
        # active() deliberately stops exposing the finalized snapshot; the
        # live resolver now observes package precedence and the new target.
        assert _python_module('worker', [tmp_path])[0][0] == (tmp_path / 'worker' / '__init__.py').resolve()
        assert implementation_closure(entry) == {entry, tmp_path / 'worker' / '__init__.py'}


def test_source_current_uses_and_finalizes_a_fresh_snapshot_per_check(monkeypatch):
    registry = em.Registry()
    registry.register(H.adapter('source-current'))
    arm = registry.adapters('1')[0]
    snapshots = []
    original = em.SourceSnapshot

    class TrackingSnapshot(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            snapshots.append(self)

    monkeypatch.setattr(em, 'SourceSnapshot', TrackingSnapshot)
    first = em.Controller._source_current(arm)
    second = em.Controller._source_current(arm)
    assert first == second
    assert len(snapshots) == 2
    assert all(snapshot.finalized for snapshot in snapshots)

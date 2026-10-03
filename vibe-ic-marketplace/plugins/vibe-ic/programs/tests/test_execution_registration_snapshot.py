"""Bounded source snapshot controls for one Ultra bootstrap."""
from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

import execution_modes as em
from execution_source_snapshot import SourceSnapshot, using
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

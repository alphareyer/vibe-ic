"""The parent can request a sub-review without manufacturing hygiene admission."""
import json
import os
from pathlib import Path
import sys

import pytest

_root = os.environ.get('CXARCH_SUBJECT_ROOT')
sys.path.insert(0, str(Path(_root)/'vibe-ic-marketplace/plugins/vibe-ic/programs'
                       if _root else Path(__file__).resolve().parents[1]))
import gatekeeper_review as R


@pytest.fixture
def bounded(monkeypatch, tmp_path):
    for name in list(vars(R)):
        if name.endswith('_gate') and callable(getattr(R, name)):
            monkeypatch.setattr(R, name, lambda *a, _name=name, **k:
                                R.GateResult(_name, 0, 'measured stub'))
    monkeypatch.setattr(R, 'role_scope_gate', lambda *a:
                        (R.GateResult('role_scope', 0, 'measured stub'), False))
    return dict(base='base', head='HEAD', repo=tmp_path, plugin_root=tmp_path,
                override_files=[], override_cur='1.0.2', override_prev='1.0.1')


def test_scope_preserves_every_other_gate_and_never_authorizes_standalone(bounded):
    full = R.review(**bounded)
    scoped = R.review(**bounded, scope='structural')
    assert full.verdict == 'MERGE_OK'
    assert scoped.verdict == 'SCOPED_REVIEW_OK'
    assert [(g.name, g.rc) for g in scoped.gates] == [
        (g.name, g.rc) for g in full.gates
        if g.name not in {'repo_hygiene_gate', 'gate_red_since_gate'}]
    report = R._verdict_to_dict(scoped)
    assert report['standalone_admission'] is False
    assert report['required_parent_units'] == ['full:repo-hygiene']
    assert not any('hygiene' in g.name for g in scoped.gates)


def test_standalone_default_executes_hygiene_once(bounded, monkeypatch):
    runs = []
    monkeypatch.setattr(R, 'repo_hygiene_gate', lambda *a, **k:
                        runs.append('hygiene') or R.GateResult('repo_hygiene_gates', 0, 'ran'))
    result = R.review(**bounded)
    assert runs == ['hygiene']
    assert result.verdict == 'MERGE_OK'
    assert R._verdict_to_dict(result)['standalone_admission'] is True


@pytest.mark.parametrize('rc', [1, 2])
def test_real_red_and_incomplete_hygiene_remain_blocking(bounded, monkeypatch, rc):
    monkeypatch.setattr(R, 'repo_hygiene_gate', lambda *a, **k:
                        R.GateResult('repo_hygiene_gates', rc, 'new red or incomplete'))
    result = R.review(**bounded)
    assert result.verdict == 'REQUEST_CHANGES'
    assert result.blocking


def test_scoped_structural_red_still_requests_changes(bounded, monkeypatch):
    monkeypatch.setattr(R, 'one_commit_gate', lambda *a, **k:
                        R.GateResult('one_commit', 1, 'wrong landing shape'))
    result = R.review(**bounded, scope='structural')
    assert result.verdict == 'REQUEST_CHANGES'
    assert 'wrong landing shape' in result.blocking[0]


def test_scope_cannot_be_combined_with_substitute_evidence(bounded, tmp_path):
    with pytest.raises(RuntimeError, match='cannot accept'):
        R.review(**bounded, scope='structural', hygiene_record_in=tmp_path/'fake')


def test_expired_debt_is_visible_advisory_and_other_ledger_errors_block(tmp_path, monkeypatch):
    G = R._load_module('gate_red_since_check')
    monkeypatch.setattr(R, '_load_module', lambda name: G)
    rec = tmp_path/'hygiene.json'
    rec.write_text(json.dumps({'declared':1, 'gates':[{'label':'old', 'state':'FAIL'}]}))
    monkeypatch.setattr(G, 'load_ledger_from_ref', lambda *a: [{
        'gate':'old', 'since':'a'*40, 'since_date':'2026-01-01T00:00:00+00:00', 'max_days':2}])
    monkeypatch.setattr(G, 'git_age_days', lambda *a: lambda ref: 4)
    monkeypatch.setattr(G, 'git_commit_date', lambda *a: lambda ref:'2026-01-01T00:00:00+00:00')
    result = R.gate_red_since_gate(tmp_path, rec, base='base')
    assert result.green and result.rc == 1 and result.advisory
    assert 'expired' in result.summary and 'old' in result.summary
    monkeypatch.setattr(G, 'git_commit_date', lambda *a: lambda ref:'2026-01-02T00:00:00+00:00')
    result = R.gate_red_since_gate(tmp_path, rec, base='base')
    assert not result.green
    assert 'misdated' in result.summary


def test_missing_debt_evidence_is_incomplete_not_a_skip(tmp_path):
    result = R.gate_red_since_gate(tmp_path, tmp_path/'missing', base='base')
    assert result.rc == 2 and not result.green
    assert 'INCOMPLETE' in result.summary

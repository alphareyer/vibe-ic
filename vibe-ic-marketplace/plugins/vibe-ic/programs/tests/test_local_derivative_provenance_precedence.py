"""Local provenance corruption differs from a pure parent transport gap."""
from pathlib import Path

from test_reused_ip_local_derivative import _fixture, _apply
import ip_catalog_pull as pull
import design_one_shot_runner as runner


def test_complete_consumer_with_unavailable_parent_stays_not_measured(tmp_path, monkeypatch):
    project, match, ref, _ = _fixture(tmp_path, monkeypatch)
    assert _apply(project, match, ref)['status'] == pull.PIN_VERIFIED
    Path(match.canonical_url).rename(tmp_path / 'unreachable')
    result = runner.step_rtl_gen(project, 'processor_cpu')
    assert result.status == 'NOT_MEASURED', result.detail
    assert 'IP_REUSE_PIN_VERIFY_UNAVAILABLE' in result.detail


def test_new_producer_staging_does_not_owe_already_produced_event(tmp_path, monkeypatch):
    project, match, ref, _ = _fixture(tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in (project / 'phase2/stage1/rtl').iterdir()}
    assert 'ip_catalog_local_derivative' not in (project / 'provenance.jsonl').read_text()
    Path(match.canonical_url).rename(tmp_path / 'unreachable')
    result = _apply(project, match, ref)
    assert result['status'] == pull.PIN_UNAVAILABLE, result
    assert 'official parent:' in result['reason']
    assert {p.name: p.read_bytes() for p in (project / 'phase2/stage1/rtl').iterdir()} == before

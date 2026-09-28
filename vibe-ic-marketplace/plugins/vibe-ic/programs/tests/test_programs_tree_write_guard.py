"""The test write guard sees a transient write before it is removed."""

from __future__ import annotations

import _programs_tree_write_guard as guard


def test_a_transient_programs_write_is_seen_before_unlink(tmp_path, monkeypatch):
    # Give the audit hook a scratch stand-in for the repository's programs dir;
    # the real tree must never be written even by this guard's own test.
    programs = tmp_path / "programs"
    programs.mkdir()
    monkeypatch.setattr(guard, "_PROGRAMS", str(programs))
    assert guard._CURRENT is not None
    baseline = list(guard._CURRENT)
    victim = programs / "brand_new_gate.py"
    victim.write_text("print('temporary')\n")
    victim.unlink()
    try:
        assert any("brand_new_gate.py" in hit for hit in guard._CURRENT)
    finally:
        # The autouse fixture must judge only writes into the actual tree.
        guard._CURRENT[:] = baseline


def test_a_scratch_write_does_not_count_as_a_programs_write(tmp_path):
    assert guard._CURRENT is not None
    before = list(guard._CURRENT)
    (tmp_path / "scratch_gate.py").write_text("print('scratch')\n")
    assert guard._CURRENT == before


def test_mkdir_of_an_existing_fixture_does_not_report_a_write(
        tmp_path, monkeypatch):
    programs = tmp_path / "programs"
    programs.mkdir()
    monkeypatch.setattr(guard, "_PROGRAMS", str(programs))
    assert guard._CURRENT is not None
    before = list(guard._CURRENT)
    programs.mkdir(parents=True, exist_ok=True)
    assert guard._CURRENT == before

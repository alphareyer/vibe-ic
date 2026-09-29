"""The active liberty gives the early fanout resolver its library identity."""
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402


def _project(root, table, library, with_pnr=False):
    docs = root / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L9_constraints.md").write_text(table)
    liberty = root / "pdk" / "libs.ref" / library / "lib" / f"{library}__tt.lib"
    liberty.parent.mkdir(parents=True)
    liberty.write_text("library (fixture) {}\n")
    if with_pnr:
        pnr = root / "phase3" / "stage3" / "pnr" / "pnr.tcl"
        pnr.parent.mkdir(parents=True)
        pnr.write_text(f"set liberty {liberty}\n")
    return root, str(liberty)


@pytest.mark.parametrize("with_pnr", [False, True])
def test_early_fanout_uses_active_liberty_and_same_family_matcher(
        tmp_path, monkeypatch, with_pnr):
    table = ("| library | MAX_FANOUT_CONSTRAINT |\n|---|---|\n"
             "| famx_* | 3 |\n| alien_* | 2 |\n")
    project, liberty = _project(tmp_path, table, "famx_sc_a", with_pnr)
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (9, "fixture default", ""))
    cap, source, unread = R._synth_max_fanout(project, "famxD", liberty)
    assert (cap, unread) == (3, []), (cap, source, unread)
    assert "famx_*" in source


def test_second_design_own_family_matches_and_foreign_row_does_not(
        tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (9, "fixture default", ""))
    table = ("| library | MAX_FANOUT_CONSTRAINT |\n|---|---|\n"
             "| quartz_* | 5 |\n| alien_* | 2 |\n")
    own, own_lib = _project(tmp_path / "own", table, "quartz_sc_a")
    foreign, foreign_lib = _project(tmp_path / "foreign",
                                    table.replace("| quartz_* | 5 |\n", ""),
                                    "quartz_sc_a")
    assert R._synth_max_fanout(own, "quartzD", own_lib)[0] == 5
    assert R._synth_max_fanout(foreign, "quartzD", foreign_lib)[0] == 9


def test_pdk_family_match_uses_the_clock_resolver_rule():
    table = ("| library | MAX_FANOUT_CONSTRAINT |\n|---|---|\n"
             "| famx_* | 3 |\n")
    assert R._l9_library_scoped_fanout(table, "", "famxD") == (3, "famx_*")
    assert R._l9_library_scoped_fanout(table, "", "alienD") is None

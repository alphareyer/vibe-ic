"""`programs/reports/**` is a run's output directory, not part of the plugin.

MEASURED on `758ac4516`: TWO tracked files live there and NOTHING in the tree
reads either of them —

    programs/reports/gates/l10_tb_conformance.json   1772 B
    programs/reports/phase3/antenna.json              188 B

The first carries, in its body, a path from whoever last ran the suite:

    "/tmp/pytest-of-reyerchu/pytest-21619/test_noleak_a_mislabelled_digi0/p/
     phase2/stage1/sim/tb/tb_dummy.v"

so every lane that runs the tests rewrites it and ships the churn as a diff.

HOW THEY GOT THERE, and the two causes are different. `antenna.json` came from
a test handing a CWD-resolving audit a tree-relative `--json`, which
`test_a_test_may_not_write_the_tree_through_a_report_audit` already forbids and
holds at population 0 — the file simply outlived the gate that closed its
cause. `l10_tb_conformance.json` came from the CHECKER'S OWN DEFAULT:
`--out reports/gates/l10_tb_conformance.json`, resolved against the process CWD,
which in the suite is `programs/`. No test passed a bad path; the default was
one.

PRODUCTION WAS NEVER WRONG. The flow passes `--out` explicitly and launches
every gate with `cwd=project`. The fix anchors only the DEFAULT to the project
the `--tb-dir` names, and leaves an explicit `--out` CWD-relative, byte for
byte.

This file is the standing census, and it is about the TREE rather than about any
one writer: whatever route an artefact takes into `programs/reports/`, it has no
business being committed.
"""
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
REPO = PROGRAMS.parents[3]


def _tracked_under(rel: str):
    out = subprocess.run(["git", "ls-files", rel], cwd=REPO,
                         capture_output=True, text=True)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def test_the_plugin_tree_tracks_no_reports_directory():
    """RED on 758ac4516 with two files; green with none."""
    rel = "vibe-ic-marketplace/plugins/vibe-ic/programs/reports"
    tracked = _tracked_under(rel)
    assert tracked == [], (
        "a run's output directory is committed into the plugin: "
        + ", ".join(tracked))


def test_the_census_can_actually_see_a_tracked_file():
    """NON-VACUITY. `git ls-files` on a path that IS tracked must return it —
    otherwise the assertion above passes on a broken query forever."""
    seen = _tracked_under(
        "vibe-ic-marketplace/plugins/vibe-ic/programs/l10_tb_conformance_check.py")
    assert len(seen) == 1, seen


def test_the_checker_default_anchors_to_the_project_not_the_cwd():
    sys.path.insert(0, str(PROGRAMS))
    import l10_tb_conformance_check as C
    got = C.default_out_path("/tmp/x/p/phase2/stage1/sim/tb")
    assert got == Path("/tmp/x/p") / C.DEFAULT_OUT_REL, got


def test_a_tb_dir_that_names_no_project_keeps_the_old_meaning():
    """THE NEGATIVE CONTROL. Inventing a root would be worse than the behaviour
    it replaces, so a `--tb-dir` that is not a project's keeps the
    CWD-relative default."""
    sys.path.insert(0, str(PROGRAMS))
    import l10_tb_conformance_check as C
    for tb in ("phase2/stage1/sim/tb", "/tmp/somewhere/else", "tb"):
        assert C.default_out_path(tb) == Path(C.DEFAULT_OUT_REL), tb


def test_running_the_checker_without_out_writes_inside_the_project(tmp_path):
    """END TO END, and this is the behaviour the stray file proves was absent:
    invoked with no `--out` from a DIFFERENT cwd, the record lands in the
    project and NOT under the process's working directory."""
    proj = tmp_path / "p"
    (proj / "phase1/generated_docs").mkdir(parents=True)
    (proj / "phase2/stage1/sim/tb").mkdir(parents=True)
    (proj / "phase1/generated_docs/L10_TEST_CASES.json").write_text(
        '{"test_cases": []}')
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    subprocess.run(
        [sys.executable, str(PROGRAMS / "l10_tb_conformance_check.py"),
         "--l10", str(proj / "phase1/generated_docs/L10_TEST_CASES.json"),
         "--tb-dir", str(proj / "phase2/stage1/sim/tb")],
        cwd=cwd, capture_output=True, text=True, timeout=300)
    assert not (cwd / "reports").exists(), (
        "the checker wrote into the process CWD")
    assert (proj / "reports/gates/l10_tb_conformance.json").is_file()

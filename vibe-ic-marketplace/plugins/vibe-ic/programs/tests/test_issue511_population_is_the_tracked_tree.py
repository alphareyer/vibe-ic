#!/usr/bin/env python3
"""vibe-ic#511 follow-on — the gate population is WHAT SHIPS, not what is on disk.

MEASURED on a clean private clone of main 0b2d38210, the file alone in the
pinned image: 37 passed. Planting ONE untracked `zz_lane_scratch_check.py` (an
8-line `[PASS]`-printing stub) into `programs/` turned exactly two ids of
`test_issue511_empty_project_pass_disclosure.py` red —
`test_standing_check_drives_every_gate_and_passes` (its `silent_gates`
exact-set equality gained a member on neither dated exemption list) and
`test_standing_check_publishes_its_inventory_on_a_passing_run` (its
`returncode == 0`) — and took the check itself from rc 0 to rc 1. Removing the
file restored 37 passed. NOTHING in the committed tree changed in either
direction: the test file and all four programs it drives are byte-identical
between the sweep tree 9c653d47f and the tip 0b2d38210.

So the verdict was moving with the CHECKOUT, not the code. `project_population`
enumerated `programs_dir.glob(...)`, which on any working copy includes files
nobody is landing.

THIS NARROWS NOTHING THAT COULD EVER LAND. An untracked file cannot be in a
landing, so every gate that ships is still in the population and the exact-set
equality above it is untouched. On a clean tree the population is unchanged:
measured 717 programs before and after, `untracked_excluded: []`.

AND AN UNANSWERABLE GIT IS NOT AN EMPTY POPULATION — that would be the
zero-denominator false clean this module exists to refuse.
"""
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import gate_discloses_denominator_check as GD  # noqa: E402

STUB = ("#!/usr/bin/env python3\n"
        '"""A throwaway gate a sibling lane left in programs/."""\n'
        "import sys\n"
        "def main(argv=None):\n"
        "    print('[PASS]')\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n")


def _repo(tmp_path):
    """A real git work tree holding two suffix-named gates, one of them
    untracked. A real repo, because the whole point is what git answers."""
    d = tmp_path / "programs"
    d.mkdir()
    (d / "alpha_check.py").write_text(STUB)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "alpha_check.py"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "one tracked gate"], cwd=d, check=True)
    (d / "beta_check.py").write_text(STUB)          # untracked
    return d


def test_an_untracked_program_is_not_in_the_population(tmp_path):
    """The defect, reproduced at the population level."""
    d = _repo(tmp_path)
    names = {p.name for p in GD.project_population(d)[0]}
    assert "alpha_check.py" in names
    assert "beta_check.py" not in names, sorted(names)


def test_the_untracked_drop_is_published_not_folded_in(tmp_path):
    """A silent narrowing would be its own defect — the drop must be readable."""
    d = _repo(tmp_path)
    defn = GD.project_population(d)[1]
    assert defn["population_binding"] == "GIT_TRACKED"
    assert defn["untracked_excluded"] == ["beta_check.py"], defn


def test_a_tracked_program_is_still_counted(tmp_path):
    """The guard must not narrow anything that can land: commit the same file
    and it re-enters the population."""
    d = _repo(tmp_path)
    subprocess.run(["git", "add", "beta_check.py"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "now it ships"], cwd=d, check=True)
    names = {p.name for p in GD.project_population(d)[0]}
    assert {"alpha_check.py", "beta_check.py"} <= names, sorted(names)
    assert GD.project_population(d)[1]["untracked_excluded"] == []


def test_a_directory_git_cannot_answer_keeps_the_on_disk_population(tmp_path):
    """NOT a zero denominator. Outside a work tree the population is the disk
    one it has always been, and the degrade is disclosed rather than silent."""
    d = tmp_path / "loose"
    d.mkdir()
    (d / "alpha_check.py").write_text(STUB)
    (d / "beta_check.py").write_text(STUB)
    paths, defn = GD.project_population(d)
    assert defn["population_binding"] == "ON_DISK_UNVERIFIED"
    assert {p.name for p in paths} >= {"alpha_check.py", "beta_check.py"}
    assert defn["total"] >= 2, defn


def test_the_shipped_tree_population_is_unchanged_by_this_binding():
    """On the real programs/ dir — a clean checkout — nothing was narrowed."""
    paths, defn = GD.project_population(PROGRAMS)
    assert defn["population_binding"] in ("GIT_TRACKED", "ON_DISK_UNVERIFIED")
    if defn["population_binding"] == "GIT_TRACKED":
        assert defn["untracked_excluded"] == [], defn["untracked_excluded"]
    assert defn["total"] > 400, defn["total"]
